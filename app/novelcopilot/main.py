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

    # 정적 프론트엔드(html/js/css)는 항상 재검증 — 코드 갱신이 브라우저 디스크 캐시에 묻혀
    # '옛 화면이 계속 보이는' 문제 차단(no-cache = ETag 재검증 후 변경 시 즉시 반영).
    @app.middleware("http")
    async def _no_cache_static(request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") and (path == "/" or path.endswith((".html", ".js", ".css"))):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    app.include_router(router)
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
    return app


app = create_app()
