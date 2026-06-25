# -*- coding: utf-8 -*-
"""Anthropic(Claude) 프로바이더. chat=Claude, embed=OpenAI 위임(Anthropic 임베딩 API 없음).
모델 라우팅 A/B용 — 문장력·윤문·퇴고 1황 후보. gen_model 예: claude-sonnet-4-6 / claude-opus-4-8."""
from __future__ import annotations
import time
from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, gen_model: str, embed_provider: LLMProvider, api_key: str | None = None,
                 max_output_cap: int = 8192):
        super().__init__()
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()  # 없으면 ANTHROPIC_API_KEY env
        self.gen_model = gen_model
        self._embed = embed_provider
        self._cap = max_output_cap

    def chat(self, messages, *, temperature=0.7, max_tokens=2200, json_mode=False) -> str:
        sys = "\n".join(m["content"] for m in messages if m["role"] == "system")
        conv = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        if not conv:
            conv = [{"role": "user", "content": sys or "."}]; sys = ""
        if json_mode:   # Anthropic 네이티브 json 모드 없음 — 지시로(base.chat_json 가 파싱 실패 시 재시도)
            sys = (sys + "\n유효한 JSON 객체만 출력. 설명·코드펜스 금지.").strip()
        mt = min(int(max_tokens), self._cap)
        use_temp = True   # 신형 모델(opus-4-8 등)은 temperature deprecated → 거부 시 자동 제거 후 재시도
        last = None
        for attempt in range(4):
            try:
                kw = dict(model=self.gen_model, max_tokens=mt, messages=conv)
                if sys:
                    kw["system"] = sys
                if use_temp:
                    kw["temperature"] = temperature
                r = self._client.messages.create(**kw)
                self.usage.chat_calls += 1
                if getattr(r, "usage", None):
                    self.usage.chat_tokens += int(r.usage.input_tokens + r.usage.output_tokens)
                return "".join(b.text for b in r.content if getattr(b, "type", None) == "text") or ""
            except Exception as e:
                msg = str(e)
                if use_temp and "temperature" in msg and ("deprecated" in msg or "unsupported" in msg.lower()):
                    use_temp = False
                    continue   # 파라미터 적응 — 즉시 재시도(대기 없음)
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    def embed(self, texts):
        return self._embed.embed(texts)
