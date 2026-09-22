# -*- coding: utf-8 -*-
"""애플리케이션 조립(Composition Root) — 여기서만 구체 구현을 와이어링.

설정→Repository→CopilotService→FastAPI. 정적 프론트엔드(web/)를 루트에 마운트.
"""
from __future__ import annotations
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .repository import FilesystemProjectRepository, FilesystemSkillRegistry
from .services import CopilotService
from .api.routes import router
from .telemetry import actor_var

_WEB_DIR = Path(__file__).resolve().parent / "web"


def create_app() -> FastAPI:
    settings = get_settings()
    repo = FilesystemProjectRepository(settings.resolved_data_dir())
    registry = FilesystemSkillRegistry(settings.resolved_data_dir())   # 앱-전역 스킬 라이브러리(작품 간 공유)
    service = CopilotService(settings, repo, registry)

    app = FastAPI(title="AI 웹소설 코파일럿", version="1.0.0")
    app.state.settings = settings
    app.state.service = service

    @app.get("/api/health")
    def health():
        return {"ok": True, "provider": settings.llm_provider, "model": settings.gen_model}

    # FI-1: actor 관통(설계 §3) — 웹 요청 스코프에서 actor="author"(작가 UI 경유). 서비스 메서드 시그니처를
    #   안 건드리고(침습 0) in-process 직접 호출(실험 tools 다수)은 미들웨어를 안 타므로 기본값 "tool" 유지.
    #   HTTP 실험 도구는 헤더 X-NC-Actor: tool 로 "tool" 자기표기(그 경우만 tool 유지). set 은 요청 컨텍스트에서
    #   1회 — sync def 라우트는 anyio 스레드풀에서 실행되고 그 컨텍스트 복사가 이 값을 전파한다(§9 실측 잠금 테스트).
    @app.middleware("http")
    async def _actor_scope(request, call_next):
        actor_var.set("tool" if request.headers.get("x-nc-actor", "").strip().lower() == "tool"
                      else "author")
        return await call_next(request)

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
