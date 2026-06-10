# -*- coding: utf-8 -*-
"""런타임 설정 — 전부 환경변수 주입(API 키·모델·경로 하드코딩 금지)."""
from __future__ import annotations
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOVEL_", env_file=".env", extra="ignore")

    llm_provider: str = "openai"
    gen_model: str = "gpt-4.1"
    embed_model: str = "text-embedding-3-small"

    # 비용·지연 1급 제약(PRD §). 장면 분량·문맥 길이 튜닝 노브.
    gen_max_tokens: int = 3000
    max_rewrite_rounds: int = 3
    prev_chapter_context_chars: int = 4000   # 서사 흐름: 직전 회차 원문 주입 길이
    story_so_far_chars: int = 6000           # 누적 줄거리 요약 주입 길이(최신 우선 컷)
    bible_digest_chars: int = 1500           # 설정집 다이제스트 주입 길이
    plant_backlog_threshold: int = 3         # 미회수 복선 적체 경보 임계(advisory)
    plant_inject_cap: int = 5                # 비트 설계에 참고로 노출할 미회수 복선 최대 수

    data_dir: str = ""                       # 비우면 패키지 옆 data/

    def resolved_data_dir(self) -> Path:
        p = Path(self.data_dir) if self.data_dir else Path(__file__).resolve().parent.parent / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
