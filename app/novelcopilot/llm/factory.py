# -*- coding: utf-8 -*-
"""프로바이더 팩토리 + 레지스트리. 이름→생성자 매핑(새 벤더는 register 한 줄)."""
from __future__ import annotations
from typing import Callable
from ..config import Settings
from .base import LLMProvider

_REGISTRY: dict[str, Callable[[Settings], LLMProvider]] = {}


def register_provider(name: str, builder: Callable[[Settings], LLMProvider]) -> None:
    _REGISTRY[name] = builder


def create_provider(settings: Settings) -> LLMProvider:
    name = settings.llm_provider
    if name not in _REGISTRY:
        raise ValueError(f"알 수 없는 LLM 프로바이더: {name!r} (등록됨: {list(_REGISTRY)})")
    return _REGISTRY[name](settings)


def create_role_provider(settings: Settings, model_spec: str) -> LLMProvider:
    """역할별 provider(B-22b 라우팅). model_spec='gpt-5.2'(settings.llm_provider 사용) 또는
    'anthropic:claude-opus-4-8'(provider 명시). 빈값이면 기본 provider. 빌드 실패(키 없음 등)면
    기본 provider 로 안전 폴백 — 라우팅이 하드브레이크를 내지 않게(다른 env 호환)."""
    spec = (model_spec or "").strip()
    if not spec:
        return create_provider(settings)
    prov, model = spec.split(":", 1) if ":" in spec else (settings.llm_provider, spec)
    try:
        return create_provider(settings.model_copy(update={"llm_provider": prov, "gen_model": model}))
    except Exception:
        return create_provider(settings)   # 키 부재/미등록 → 기본으로 폴백


def _build_openai(settings: Settings) -> LLMProvider:
    from .openai_provider import OpenAIProvider
    return OpenAIProvider(gen_model=settings.gen_model, embed_model=settings.embed_model)


def _openai_embed(settings: Settings) -> LLMProvider:
    from .openai_provider import OpenAIProvider     # embed 위임용(Anthropic/Gemini 임베딩 API 없음·RAG 일관)
    return OpenAIProvider(gen_model="gpt-4.1", embed_model=settings.embed_model)


def _build_anthropic(settings: Settings) -> LLMProvider:
    from .anthropic_provider import AnthropicProvider
    return AnthropicProvider(gen_model=settings.gen_model, embed_provider=_openai_embed(settings))


def _build_gemini(settings: Settings) -> LLMProvider:
    from .gemini_provider import GeminiProvider
    return GeminiProvider(gen_model=settings.gen_model, embed_provider=_openai_embed(settings))


register_provider("openai", _build_openai)
register_provider("anthropic", _build_anthropic)
register_provider("gemini", _build_gemini)
