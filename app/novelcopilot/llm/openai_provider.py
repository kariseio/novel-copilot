# -*- coding: utf-8 -*-
"""OpenAI 프로바이더 구현. retry + 인스턴스 usage 계측. 전역 client 금지(DI)."""
from __future__ import annotations
import time
from openai import OpenAI
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self, gen_model: str, embed_model: str, client: OpenAI | None = None,
                 timeout: float = 300.0):
        super().__init__()
        self._client = client or OpenAI(timeout=timeout)   # 추론 모델(gpt-5.5 등)은 1콜 >90s — 300s 로(워커 점유 vs 회차 절단 트레이드오프)
        self.gen_model = gen_model
        self.embed_model = embed_model

    def chat(self, messages, *, temperature=0.7, max_tokens=2200, json_mode=False) -> str:
        kwargs = dict(model=self.gen_model, messages=messages,
                      temperature=temperature, max_tokens=max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        last = None
        for attempt in range(4):
            try:
                try:
                    r = self._client.chat.completions.create(**kwargs)
                except Exception as e:   # 신형(gpt-5/o계열) 파라미터 차이 자동 적응 — 모델 교체 시 코드 무변경
                    msg = str(e)
                    adapted = False
                    if "max_tokens" in msg and "max_completion_tokens" in msg:
                        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens", max_tokens)
                        adapted = True
                    if "temperature" in msg and ("unsupported" in msg.lower() or "does not support" in msg.lower()):
                        kwargs.pop("temperature", None)
                        adapted = True
                    if not adapted:
                        raise
                    r = self._client.chat.completions.create(**kwargs)
                self.usage.chat_calls += 1
                if r.usage:
                    self.usage.chat_tokens += r.usage.total_tokens
                self.last_truncated = (getattr(r.choices[0], "finish_reason", None) == "length")   # 절단 신호(harness 재생성용)
                return r.choices[0].message.content or ""   # None(거부/빈응답) → "" (json.loads(None) TypeError 방지)
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    def embed(self, texts):
        if not texts:
            return []
        # 빈/공백 문자열은 임베딩 API 가 400 거부('input cannot be an empty string') — 호출부 길이/정렬
        # (rag.py zip(paras, vecs))을 깨지 않도록 공백 1칸으로 치환(드롭 금지). 결정론·순서 보존.
        safe = [t if (t and t.strip()) else " " for t in texts]
        r = self._client.embeddings.create(model=self.embed_model, input=safe)
        self.usage.embed_calls += 1
        self.usage.embed_items += len(safe)
        return [d.embedding for d in r.data]
