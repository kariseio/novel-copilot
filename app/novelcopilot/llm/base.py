# -*- coding: utf-8 -*-
"""LLM 프로바이더 — Strategy 패턴. 벤더가 아니라 '계약'에 의존.

엔진의 어떤 모듈도 OpenAI 를 직접 import 하지 않는다(DI 로 이 인터페이스만 받음).
프로덕션에서 Claude/BGE-M3 프로바이더를 추가해도 엔진은 한 줄도 안 바뀐다.
사용량(usage)은 전역이 아니라 '인스턴스'에 누적 → 프로젝트별 비용 계측 가능.
"""
from __future__ import annotations
import json
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

    @abstractmethod
    def chat(self, messages: list[dict], *, temperature: float = 0.7,
             max_tokens: int = 2200, json_mode: bool = False) -> str:
        ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def chat_json(self, messages: list[dict], *, temperature: float = 0.0,
                  max_tokens: int = 2200) -> dict:
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
