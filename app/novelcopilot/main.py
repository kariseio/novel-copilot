# -*- coding: utf-8 -*-
"""애플리케이션 조립(Composition Root) — 여기서만 구체 구현을 와이어링.

설정→Repository→CopilotService→FastAPI. 정적 프론트엔드(web/)를 루트에 마운트.
"""
from __future__ import annotations
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .repository import FilesystemProjectRepository
from .services import CopilotService
from .api.routes import router

_WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app() -> FastAPI:
    settings = get_settings()
    repo = FilesystemProjectRepository(settings.resolved_data_dir())
    service = CopilotService(settings, repo)

    app = FastAPI(title="AI 웹소설 코파일럿", version="1.0.0")
    app.state.settings = settings
    app.state.service = service

    @app.get("/api/health")
    def health():
        return {"ok": True, "provider": settings.llm_provider, "model": settings.gen_model}

    app.include_router(router)
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
    return app


app = create_app()
