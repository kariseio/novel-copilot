# -*- coding: utf-8 -*-
"""LLM 프로바이더 — Strategy 패턴. 벤더가 아니라 '계약'에 의존.

엔진의 어떤 모듈도 OpenAI 를 직접 import 하지 않는다(DI 로 이 인터페이스만 받음).
프로덕션에서 Claude/BGE-M3 프로바이더를 추가해도 엔진은 한 줄도 안 바뀐다.
사용량(usage)은 전역이 아니라 '인스턴스'에 누적 → 프로젝트별 비용 계측 가능.
"""
from __future__ import annotations
import json
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


def _strip_code_fence(s: str) -> str:
    """마크다운 코드펜스(```json … ```)만 벗긴다 — 응답 파싱 보조(요청 바이트 불변·펜스 없으면 원문 그대로)."""
    t = (s or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z0-9_-]*[ \t]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t


@dataclass
class Usage:
    chat_calls: int = 0
    chat_tokens: int = 0
    embed_calls: int = 0
    embed_items: int = 0
    # CE-1 ⓐ: Anthropic 프롬프트 캐싱 계측(additive·별도 카운터). 응답 usage 의 cache_creation/read_input_tokens 를
    #   여기 누적한다. Anthropic 응답에서 input_tokens 는 '캐시 밖(비캐시) 잔여'만 세므로(캐시 생성/읽기 토큰은
    #   input_tokens 에 미포함 — 세 값이 상호배타), chat_tokens=input+output 산식은 불변이고 이중 계상이 없다.
    #   이 두 카운터는 순수 관측용(비용 산정 재료)이며 chat_tokens 에는 합산하지 않는다.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # AG-1 T1: 생성 중 툴 조회 횟수(additive·관측용) — 에이전틱 pull 의 비용·행태 계측 원자료.
    tool_calls: int = 0

    def as_dict(self) -> dict:
        return {"chat_calls": self.chat_calls, "chat_tokens": self.chat_tokens,
                "embed_calls": self.embed_calls, "embed_items": self.embed_items,
                "cache_creation_input_tokens": self.cache_creation_input_tokens,
                "cache_read_input_tokens": self.cache_read_input_tokens}


class LLMProvider(ABC):
    def __init__(self) -> None:
        self.usage = Usage()
        self._tls = threading.local()   # 절단 플래그는 스레드별(공유 인스턴스를 동시 생성/퇴고가 쓰므로)

    # 직전 chat() 이 max_tokens 로 절단됐나(절단 trim 라우팅 신호). 공유 provider 인스턴스를 백그라운드 생성잡과
    # 퇴고 스레드가 동시에 쓰는 구조라(F8) 단일 슬롯이면 크로스스레드 덮어쓰기 발생 → thread-local 로 호출자 스레드 격리.
    @property
    def last_truncated(self) -> bool:
        return getattr(getattr(self, "_tls", None), "v", False)

    @last_truncated.setter
    def last_truncated(self, val: bool) -> None:
        if getattr(self, "_tls", None) is None:
            self._tls = threading.local()
        self._tls.v = bool(val)

    # max_tokens 계약(2026-08-07 사용자 결정 "모든 곳에서 다 빼자"): 기본 None=무상한.
    #   출력 상한을 작게 잡은 판정 콜이 추론 모델(gpt-5.6)에서 추론 토큰에 예산을 다 뺏겨 빈 출력→{} 세탁
    #   (HZ-3, 2~13화 판정 전멸 실측)된 소스 차단 — 호출부는 상한을 지정하지 않는다. None 처리는 프로바이더
    #   재량(OpenAI/Gemini=파라미터 생략, Anthropic=API 필수라 max_output_cap 대입). 명시값은 계속 존중(동결
    #   실험 스크립트 재현성).
    @abstractmethod
    def chat(self, messages: list[dict], *, temperature: float = 0.7,
             max_tokens: int | None = None, json_mode: bool = False) -> str:
        ...

    def chat_tools(self, messages: list[dict], *, tools: list | None = None, tool_handler=None,
                   temperature: float = 0.7, max_tokens: int | None = None, max_rounds: int = 6) -> str:
        """AG-1 T1 — 툴 인터리브 생성. **기본 구현은 tools 를 무시하고 chat 위임**(안전 강등):
        미지원 프로바이더(openai/gemini)가 자동으로 기존 단발 경로를 탄다. Anthropic 이 오버라이드.
        tool_handler(name, input_dict) -> str. 미설정(tools 없음) 호출은 chat 과 동작·바이트 동일."""
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def chat_json(self, messages: list[dict], *, temperature: float = 0.0,
                  max_tokens: int | None = None, schema: dict | None = None) -> dict:
        """구조화 출력 — 실패 시 1회 교정 재시도(PRD §6.3 계승).

        OV-5: schema(JSON Schema)는 지원 프로바이더(Anthropic 구조화 출력)만 소비한다 — 기본 구현은
        무시(지시-기반 경로 그대로·바이트 동일 하위호환). 호출부는 스키마를 넘겨두면 지원 경로에서
        파싱 실패 양식 자체가 사라진다.

        CJ-1 결측 정직 가드(2026-08-09): 빈 1차 응답·교정 재시도의 빈 {} 를 성공으로 돌려주지 않는다.
        실사고(HZ-3): 출력 예산 소진 → 빈 응답 → 교정 재시도가 빈 입력에 {} 반환 → needs_repair=False 로
        세탁돼 판정 축이 2~13화 침묵. 예외(ValueError)는 기존 계약(재시도 실패 시 raise)과 동일 유형이라
        호출부 계약 불변 — 결측이 '자신만만한 빈 판정'이 아니라 실패로 정직하게 떨어진다.
        단 모델이 '직접' {} 를 출력한 1차 응답은 통과(진짜 출력과 교정 세탁을 구분)."""
        txt = self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
        if not (txt or "").strip():   # 빈 1차 응답 — 교정할 내용 자체가 없다(재시도는 {} 세탁 경로일 뿐) → 즉시 결측
            raise ValueError("chat_json: 빈 응답(출력 0자) — 결측")
        for cand in (txt, _strip_code_fence(txt)):   # 펜스 구조: 직파싱 실패 시 펜스 제거 후 1회 더(LLM 콜 0 —
            try:                                     #   sonnet-5 가 '코드펜스 금지' 지시에도 ```json 으로 감싸는 실측 65/66)
                obj = json.loads(cand)
                if isinstance(obj, dict):     # 최상위가 객체(dict)인 경우만 정상 — 배열/문자열 등은 교정 재시도
                    return obj
                break
            except json.JSONDecodeError:
                continue
        fix = self.chat([{"role": "user",
                          "content": "다음을 유효한 JSON '객체'(dict)로만 다시 출력해. 설명 금지:\n" + txt}],
                        temperature=0.0, max_tokens=max_tokens, json_mode=True)
        try:
            obj = json.loads(_strip_code_fence(fix))
        except json.JSONDecodeError as e:   # 재시도 후에도 실패 → ValueError 로 일관(JSONDecodeError 누수 방지)
            raise ValueError(f"chat_json: 재시도 후에도 JSON 파싱 실패: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("chat_json: 최상위가 JSON 객체가 아님")
        if not obj:   # 교정 재시도의 빈 {} — 원문에서 아무것도 복구 못한 결측의 세탁 → 정직 실패
            raise ValueError("chat_json: 교정 재시도가 빈 객체({}) — 결측")
        return obj
