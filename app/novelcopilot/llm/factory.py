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
