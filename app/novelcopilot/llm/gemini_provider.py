# -*- coding: utf-8 -*-
"""Gemini 프로바이더 (google.genai). chat=Gemini, embed=OpenAI 위임(임베딩은 RAG 일관 위해 단일 모델 유지).
모델 라우팅 A/B용 — 한국어 프로즈 자연스러움 후보(커뮤니티 '한글 최강'). gen_model 예: gemini-2.5-pro / gemini-3-pro."""
from __future__ import annotations
import time
from .base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, gen_model: str, embed_provider: LLMProvider, api_key: str | None = None):
        super().__init__()
        from google import genai
        self._genai = genai
        self._client = genai.Client(api_key=api_key) if api_key else genai.Client()  # 없으면 GOOGLE_API_KEY/GEMINI_API_KEY env
        self.gen_model = gen_model
        self._embed = embed_provider

    def chat(self, messages, *, temperature=0.7, max_tokens=2200, json_mode=False) -> str:
        from google.genai import types
        sys = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
        cfg = types.GenerateContentConfig(
            temperature=temperature, max_output_tokens=max_tokens,
            system_instruction=(sys or None),
            response_mime_type=("application/json" if json_mode else None))
        last = None
        for attempt in range(4):
            try:
                r = self._client.models.generate_content(model=self.gen_model, contents=contents, config=cfg)
                self.usage.chat_calls += 1
                um = getattr(r, "usage_metadata", None)
                if um:
                    self.usage.chat_tokens += int(getattr(um, "total_token_count", 0) or 0)
                _cands = getattr(r, "candidates", None) or []
                self.last_truncated = bool(_cands) and "MAX_TOKENS" in str(getattr(_cands[0], "finish_reason", "")).upper()
                try:
                    txt = r.text or ""
                except Exception:
                    txt = ""
                if not txt:   # thinking 모델이 text part 안 냈을 때 candidates 에서 직접 추출
                    for c in (getattr(r, "candidates", None) or []):
                        cont = getattr(c, "content", None)
                        for p in (getattr(cont, "parts", None) or []):
                            t = getattr(p, "text", None)
                            if t:
                                txt += t
                return txt
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    def embed(self, texts):
        return self._embed.embed(texts)
