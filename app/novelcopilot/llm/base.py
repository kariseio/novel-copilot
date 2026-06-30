# -*- coding: utf-8 -*-
"""LLM 프로바이더 — Strategy 패턴. 벤더가 아니라 '계약'에 의존.

엔진의 어떤 모듈도 OpenAI 를 직접 import 하지 않는다(DI 로 이 인터페이스만 받음).
프로덕션에서 Claude/BGE-M3 프로바이더를 추가해도 엔진은 한 줄도 안 바뀐다.
사용량(usage)은 전역이 아니라 '인스턴스'에 누적 → 프로젝트별 비용 계측 가능.
"""
from __future__ import annotations
import json
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Usage:
    chat_calls: int = 0
    chat_tokens: int = 0
    embed_calls: int = 0
    embed_items: int = 0

    def as_dict(self) -> dict:
        return {"chat_calls": self.chat_calls, "chat_tokens": self.chat_tokens,
                "embed_calls": self.embed_calls, "embed_items": self.embed_items}


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

    @abstractmethod
    def chat(self, messages: list[dict], *, temperature: float = 0.7,
             max_tokens: int = 2200, json_mode: bool = False) -> str:
        ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def chat_json(self, messages: list[dict], *, temperature: float = 0.0,
                  max_tokens: int = 6000) -> dict:
        """구조화 출력 — 실패 시 1회 교정 재시도(PRD §6.3 계승)."""
        txt = self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
        try:
            obj = json.loads(txt)
            if isinstance(obj, dict):     # 최상위가 객체(dict)인 경우만 정상 — 배열/문자열 등은 교정 재시도
                return obj
        except json.JSONDecodeError:
            pass
        fix = self.chat([{"role": "user",
                          "content": "다음을 유효한 JSON '객체'(dict)로만 다시 출력해. 설명 금지:\n" + txt}],
                        temperature=0.0, max_tokens=max_tokens, json_mode=True)
        try:
            obj = json.loads(fix)
        except json.JSONDecodeError as e:   # 재시도 후에도 실패 → ValueError 로 일관(JSONDecodeError 누수 방지)
            raise ValueError(f"chat_json: 재시도 후에도 JSON 파싱 실패: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError("chat_json: 최상위가 JSON 객체가 아님")
        return obj
