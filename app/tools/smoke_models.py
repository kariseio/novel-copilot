# -*- coding: utf-8 -*-
"""모델 스모크 — .env 키 로드 확인 + Anthropic/Gemini 모델 ID 확정 + 짧은 한국어 생성 1번씩.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/smoke_models.py
"""
from __future__ import annotations
import os
from novelcopilot.config import get_settings  # import 시 .env load_dotenv

get_settings()
print("[키 존재]", {k: bool(os.environ.get(k)) for k in
                  ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"]})

PROMPT = [{"role": "system", "content": "너는 한국 웹소설 작가다. 본문만 출력."},
          {"role": "user", "content": "최약체 헌터가 던전에서 시스템을 각성하는 순간을 3문장으로 써라."}]

# --- Anthropic ---
print("\n=== Anthropic ===")
try:
    import anthropic
    ac = anthropic.Anthropic()
    ids = [m.id for m in ac.models.list().data]
    print(" 모델:", ids[:10])
    pick = next((i for i in ["claude-opus-4-8", "claude-sonnet-4-6"] if i in ids), ids[0] if ids else None)
    if pick:
        from novelcopilot.llm.anthropic_provider import AnthropicProvider
        from novelcopilot.llm.openai_provider import OpenAIProvider
        p = AnthropicProvider(gen_model=pick, embed_provider=OpenAIProvider("gpt-4.1", "text-embedding-3-small"))
        print(f" [{pick}] 생성:\n  ", p.chat(PROMPT, temperature=0.85, max_tokens=2000).replace("\n", " ")[:300])
except Exception as e:
    print(" ERR:", type(e).__name__, str(e)[:200])

# --- Gemini ---
print("\n=== Gemini ===")
try:
    from google import genai
    gc = genai.Client()
    names = [m.name.split("/")[-1] for m in gc.models.list()]
    pros = [n for n in names if "gemini" in n and "pro" in n and "vision" not in n]
    print(" pro 모델:", pros[:10])
    pick = next((i for i in ["gemini-3.1-pro-preview", "gemini-2.5-pro", "gemini-pro-latest"] if i in names), pros[0] if pros else None)
    if pick:
        from novelcopilot.llm.gemini_provider import GeminiProvider
        from novelcopilot.llm.openai_provider import OpenAIProvider
        p = GeminiProvider(gen_model=pick, embed_provider=OpenAIProvider("gpt-4.1", "text-embedding-3-small"))
        print(f" [{pick}] 생성:\n  ", p.chat(PROMPT, temperature=0.85, max_tokens=2000).replace("\n", " ")[:300])
except Exception as e:
    print(" ERR:", type(e).__name__, str(e)[:200])
