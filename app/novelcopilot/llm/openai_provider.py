# -*- coding: utf-8 -*-
"""OpenAI 프로바이더 구현. retry + 인스턴스 usage 계측. 전역 client 금지(DI)."""
from __future__ import annotations
import time
from openai import OpenAI
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self, gen_model: str, embed_model: str, client: OpenAI | None = None,
                 timeout: float = 90.0):
        super().__init__()
        self._client = client or OpenAI(timeout=timeout)   # 기본 read=600s 대신 유한 타임아웃(워커 장시간 점유 방지)
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
                return r.choices[0].message.content or ""   # None(거부/빈응답) → "" (json.loads(None) TypeError 방지)
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    def embed(self, texts):
        if not texts:
            return []
        r = self._client.embeddings.create(model=self.embed_model, input=texts)
        self.usage.embed_calls += 1
        self.usage.embed_items += len(texts)
        return [d.embedding for d in r.data]
