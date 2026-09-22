# -*- coding: utf-8 -*-
"""HTTP 라우트 + SSE 실시간 하네스 스트리밍."""
from __future__ import annotations
import asyncio
import io
import json
import queue
import re
import zipfile

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, Response

from ..domain.project import ProjectSeed
from ..engine.textfmt import md_to_plain, collapse_dashes
from ..services.insights import build_dashboard
from .schemas import (CreateProjectRequest, DirectiveRequest, EntityRequest,
                      RelationRequest, EndRelationRequest, BibleEntryRequest, BibleUpdateRequest,
                      WorldgenTurnRequest, StylePolicyRequest, ProjectMetaRequest, EntityVoiceRequest,
                      ReviseRequest, ReviseAcceptRequest, RevisionSummary, EditRequest,
                      RedpenAddRequest, RedpenSuggestRequest, PublishRequest,
                      StoryPassRunRequest, StoryConfirmRequest, AttributeAutoCommitRequest, TierReviewRequest,
                      DerivativeRecomputeRequest)

router = APIRouter(prefix="/api")


def _svc(request: Request):
    return request.app.state.service


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


# ---- 문서 포털 서빙(SSOT — 복사하지 않고 레포 docs/ 를 그대로 읽어 반환) ----
# 경로는 상수 매핑으로 고정한다. 사용자 입력으로 경로를 조립하지 않으므로 path traversal(../ 등)
# 은 원천 차단된다(매핑에 없는 이름은 무조건 404). docs 루트는 결정론적으로 산출:
#   routes.py = app/novelcopilot/api/routes.py → parents[3] = 레포 루트 → /docs.
_DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"
# IA 계약(2026-07-14 개편 — 5섹션·17문서). 키·경로·순서는 사이드바(manual.js DOCS)와 1:1 계약.
#   [시작하기]  overview·getting-started·setup
#   [사용 가이드] create-work·write-chapters·read-verification·refine-chapters·
#                manage-assets·serial-operations·export-backup
#   [개념]      how-it-works
#   [레퍼런스]   settings·faq·troubleshooting·release-notes
#   [기술 문서]  pipeline·architecture
# 강등된 키: user-guide(가이드 8문서로 분리 소멸)·changelog(엔지니어링 로그 — 포털 밖. 파일은 존속하나 서빙 안 함).
# overview 경로는 SERVICE-OVERVIEW.md → guide/introduction.md 로 이관.
_DOCS_WHITELIST = {
    # 시작하기
    "overview": _DOCS_ROOT / "guide" / "introduction.md",
    "getting-started": _DOCS_ROOT / "guide" / "getting-started.md",
    "setup": _DOCS_ROOT / "guide" / "setup.md",
    # 사용 가이드
    "create-work": _DOCS_ROOT / "guide" / "create-work.md",
    "write-chapters": _DOCS_ROOT / "guide" / "write-chapters.md",
    "read-verification": _DOCS_ROOT / "guide" / "read-verification.md",
    "refine-chapters": _DOCS_ROOT / "guide" / "refine-chapters.md",
    "manage-assets": _DOCS_ROOT / "guide" / "manage-assets.md",
    "serial-operations": _DOCS_ROOT / "guide" / "serial-operations.md",
    "export-backup": _DOCS_ROOT / "guide" / "export-backup.md",
    # 개념
    "how-it-works": _DOCS_ROOT / "guide" / "how-it-works.md",
    # 레퍼런스
    "settings": _DOCS_ROOT / "guide" / "settings-reference.md",
    "faq": _DOCS_ROOT / "guide" / "faq.md",
    "troubleshooting": _DOCS_ROOT / "guide" / "troubleshooting.md",
    "release-notes": _DOCS_ROOT / "guide" / "release-notes.md",
    # 기술 문서(심화 — 내부 기술 명세)
    "pipeline": _DOCS_ROOT / "PIPELINE.md",
    "architecture": _DOCS_ROOT / "ARCHITECTURE.md",
}


@router.get("/docs/{name}")
def get_doc(name: str):
    """화이트리스트 고정 문서를 마크다운으로 반환. 매핑 밖 이름(../ 포함)은 404, 파일 부재도 404(정직).

    반환: {name, markdown}. 사용자 입력은 딕셔너리 키 조회에만 쓰이고 경로 조립에 절대 쓰이지 않는다."""
    path = _DOCS_WHITELIST.get(name)
    if path is None:
        raise HTTPException(404, "unknown document")
    if not path.is_file():
        raise HTTPException(404, "document file not found")
    return {"name": name, "markdown": path.read_text(encoding="utf-8")}


@router.post("/projects")
def create_project(req: CreateProjectRequest, request: Request):
    seed = ProjectSeed(**req.model_dump())
    state, usage = _svc(request).create_project(seed)
    return {"id": state.id, "world": state.world.model_dump(), "usage": usage,
            "created_at": state.created_at}


@router.post("/drafts")
def draft_turn(request: Request, body: dict):
    """컨셉 대화 한 턴 — 첫 메시지면 새 드래프트 시작. body: {draft_id?, message, params?}.
    params(작가가 컨트롤로 정한 genre/tone/target_chapters)는 AI 갱신보다 우선."""
    svc = _svc(request)
    did = body.get("draft_id") or svc.start_draft().id
    msg = (body.get("message") or "").strip()[:4000]   # 입력 상한(비용·남용 가드)
    return svc.draft_turn(did, msg, params=body.get("params"))


@router.get("/drafts/{did}")
def get_draft(did: str, request: Request):
    d = _svc(request).get_draft(did)
    if not d:
        raise HTTPException(404, "draft not found")
    return {"draft_id": d.id, "brief": d.brief.model_dump(), "chat": d.chat,
            "questions": d.open_questions, "completeness": d.brief.completeness()}


@router.get("/drafts/{did}/finalize")
async def finalize_draft(did: str, request: Request, target_chapters: int = 0, genre: str = "", tone: str = "", keywords: str = "", world_skills: str = ""):
    """SSE: 누적 브리프로 세계 생성(세계관→이야기 구조→설정집) 실시간 진행.
    작가가 컨트롤로 정한 파라미터(target_chapters/genre/tone/keywords)와 세계관 스킬(world_skills)을 최종 반영."""
    from ..engine.observability import EventBus
    svc = _svc(request)
    if not svc.get_draft(did):
        raise HTTPException(404, "draft not found")
    params = {k: v for k, v in {"target_chapters": target_chapters, "genre": genre, "tone": tone}.items() if v}
    params["keywords"] = [k.strip() for k in keywords.split("|") if k.strip()]   # 트로프 칩(B1: finalize 채널 — 빈 리스트면 _merge_locks 가 잠금 해제)
    params["world_skills"] = [s.strip() for s in world_skills.split("|") if s.strip()]   # 라이브러리에서 고른 세계관 스킬 id(잠금 아님)
    loop = asyncio.get_event_loop()
    q: "queue.Queue" = queue.Queue()
    bus = EventBus()
    unsub = bus.subscribe(lambda e: q.put(("event", e)))

    def work():
        try:
            state, usage = svc.finalize_draft(did, params=params, bus=bus)
            q.put(("complete", {"id": state.id, "world": state.world.model_dump(),
                                "usage": usage, "created_at": state.created_at}))
        except Exception as e:  # noqa
            q.put(("failed", {"message": str(e)}))

    async def stream():
        fut = loop.run_in_executor(None, work)
        try:
            yield _sse("start", {})
            while True:
                try:
                    kind, data = q.get_nowait()
                except queue.Empty:
                    if fut.done() and q.empty():
                        break
                    await asyncio.sleep(0.12)
                    continue
                yield _sse(kind, data)
                if kind in ("complete", "failed"):
                    break
        finally:
            unsub()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/projects")
def list_projects(request: Request):
    return _svc(request).list_projects()


# FI-4: 전역 관측 대시보드 집계(서재 레벨·읽기 전용·LLM 0콜). 200 고정 — 손상 트레이스 등 부분 실패는
#   응답의 trace_errors 로 정직 표면화(4xx/5xx 로 숨기지 않음). 응답에 판정성 비율 필드 없음(계약 테스트로 잠금)·
#   record_counts(전체 이력)와 event_counts(관측 이후·actor 분리) 분리. 어떤 저장물도 변형하지 않는다.
@router.get("/dashboard")
def get_dashboard(request: Request):
    svc = _svc(request)
    return build_dashboard(svc.repo, svc.settings)


@router.get("/projects/{pid}")
def get_project(pid: str, request: Request, chapters: str = "full"):
    """작품 상태. chapters= full(기본·기존 응답 그대로) | summary(본문·퇴고 이력 등 무거운 축 제외) | none.

    회차 레코드가 응답의 대부분(회차당 ~90KB)이라 목록만 그리는 화면은 summary 로 받고, 펼쳐 보는
    회차만 GET /chapters/{n} 으로 채운다. 기본값이 full 이라 구 클라이언트·도구는 무회귀."""
    svc = _svc(request)
    state = svc.get_project(pid)
    if not state:
        raise HTTPException(404, "project not found")
    if chapters not in ("full", "summary", "none"):
        raise HTTPException(400, "chapters 는 full|summary|none")
    return {
        "id": state.id, "seed": state.seed.model_dump(), "world": state.world.model_dump(),
        "created_at": state.created_at, "current_chapter": state.current_chapter,
        "total_beats": state.seed.target_chapters or len(state.world.beats),
        "has_spine": state.world.spine is not None,
        "completed": state.narrative_progress.completed, "usage_total": state.usage_total,
        "has_regen_backup": getattr(state, "has_regen_backup", False),   # 마지막 회차 재생성 '되돌리기' 가능 여부
        "cover": (state.cover.model_dump() if getattr(state, "cover", None) else None),   # CV-1: 표지 메타(없으면 None)
        "directives": [d.model_dump() for d in state.directives],
        "chapters": ([] if chapters == "none"
                     else [svc.chapter_view(c, chapters) for c in state.chapters]),
        "chapters_total": len(state.chapters),   # chapters 를 잘라 보내도 화면이 전체 개수를 알게(항상 동봉)
        # FI-2: 3원천 조인 타임라인(트레이스+퇴고+재생성)용 재생성 실행 로그(읽기 전용·append-only 계측).
        #   revisions 는 chapters[].revisions 로 이미 동승·trace 는 별도 라우트.
        "regen_events": [e.model_dump() for e in state.regen_events],
    }


@router.delete("/projects/{pid}")
def delete_project(pid: str, request: Request):
    """XR-34: 삭제 트랜잭션 — 진행 중 작업(회차 생성 등)이 프로젝트 락을 쥐고 있으면 423 즉시 거부
    (제품 계약: 대기 대신 정직한 거부 — 완료 후 다시 삭제). 성공 시 tombstone 으로 부활·유령 세션 차단."""
    from ..services.session import ProjectBusyError
    try:
        return {"deleted": _svc(request).delete_project(pid)}
    except ProjectBusyError as e:
        raise HTTPException(423, str(e))


# ---- CV-1: 표지 이미지(작가 발동형) ----
@router.post("/projects/{pid}/cover")
def generate_cover(pid: str, request: Request, body: dict | None = None):
    """설정·주인공 기반 표지 생성(+저장) → CoverMeta. body 선택 {prompt: 합성 대신 쓸 오버라이드,
    include_title: 한글 제목 타이포 포함 여부(기본 true — CV-4)}.
    중복 생성·회차 생성 중=423, 없는 작품=404, 이미지/합성 실패=500(무변경·명시 에러)."""
    override = ((body or {}).get("prompt") or "").strip() or None
    include_title = bool((body or {}).get("include_title", True))
    try:
        res = _svc(request).generate_cover(pid, prompt_override=override, include_title=include_title)
    except Exception as e:   # 합성·이미지 콜 실패 → 저장·메타 무변경 + 명시 에러(침묵 폴백 금지 — P-2)
        raise HTTPException(500, f"표지 생성 실패: {e}")
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "표지 생성 중이거나 회차 생성 중입니다 — 잠시 후 다시 시도하세요")
    return res


@router.get("/projects/{pid}/cover")
def get_cover(pid: str, request: Request):
    """표지 PNG 바이트(없으면 404)."""
    png = _svc(request).get_cover_bytes(pid)
    if png is None:
        raise HTTPException(404, "cover not found")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-cache"})   # 재생성 즉시 반영(디스크 캐시 정적 캐싱 회피)


# ---- CV-2: 표지 보관함(갤러리) ----
@router.get("/projects/{pid}/covers")
def list_covers(pid: str, request: Request):
    """표지 보관함 목록 + 적용본 파일명 {covers:[meta...], applied: filename|None}. 없는 작품=404."""
    res = _svc(request).list_covers(pid)
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.post("/projects/{pid}/covers/{filename}/apply")
def apply_cover(pid: str, filename: str, request: Request):
    """보관함 표지 하나를 적용본으로 전환. 없는 작품·미존재 파일명=404, 생성 중=423."""
    try:
        res = _svc(request).apply_cover(pid, filename)
    except ValueError:
        raise HTTPException(404, "cover not found")
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "생성 중입니다 — 잠시 후 다시 시도하세요")
    return res


@router.delete("/projects/{pid}/covers/{filename}")
def delete_cover(pid: str, filename: str, request: Request):
    """보관함 표지 하나 삭제(메타+바이너리). 없는 작품·미존재 파일명=404, 생성 중=423.
    적용 중이던 표지를 지우면 최신본으로 적용 이전(없으면 해제)."""
    try:
        res = _svc(request).delete_cover(pid, filename)
    except ValueError:
        raise HTTPException(404, "cover not found")
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "생성 중입니다 — 잠시 후 다시 시도하세요")
    return res


@router.get("/projects/{pid}/covers/{filename}/file")
def get_cover_file(pid: str, filename: str, request: Request):
    """보관함 개별 표지 PNG. 파일명 검증(경로 탈출 차단)은 repo 층. 없으면 404.
    타임스탬프 파일은 불변 → 캐시 허용(max-age). 단 legacy {pid}.cover.png 는 덮어쓰기 이력 → no-cache."""
    png = _svc(request).get_cover_file_bytes(pid, filename)
    if png is None:
        raise HTTPException(404, "cover not found")
    cache = "no-cache" if filename == f"{pid}.cover.png" else "max-age=86400"
    return Response(content=png, media_type="image/png", headers={"Cache-Control": cache})


@router.post("/projects/{pid}/directives")
def add_directive(pid: str, req: DirectiveRequest, request: Request):
    d = _svc(request).add_directive(pid, req.text)
    if not d:
        raise HTTPException(404, "project not found")
    return d.model_dump()


@router.post("/projects/{pid}/entities")
def add_entity(pid: str, req: EntityRequest, request: Request):
    try:
        res = _svc(request).add_entity(pid, req.name, req.etype, req.aliases)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.post("/projects/{pid}/relations")
def add_relation(pid: str, req: RelationRequest, request: Request):
    try:
        res = _svc(request).add_relation(pid, req.src_id, req.dst_id, req.rel_id,
                                         req.eff_from, req.reason, req.role, req.state, req.pov)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.post("/projects/{pid}/relations/end")
def end_relation(pid: str, req: EndRelationRequest, request: Request):
    try:
        res = _svc(request).end_relation(pid, req.src_id, req.dst_id, req.rel_id, req.eff_to)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


# ---- 보이스(목소리) — 설정집 노출 + 작가 편집 ----
@router.get("/projects/{pid}/voices")
def get_voices(pid: str, request: Request):
    """화자 보이스·서술자 음성 카드·상태 단계 카드·인물 보이스 통합 조회(읽기 전용·LLM 0콜)."""
    res = _svc(request).voice_snapshot(pid)
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.patch("/projects/{pid}/entities/{eid}/voice")
def update_entity_voice(pid: str, eid: str, req: EntityVoiceRequest, request: Request):
    """인물 보이스 카드·단계 카드 작가 편집 — style PUT 관행(ValueError→400·None→404). 부분 수정."""
    try:
        res = _svc(request).update_entity_voice(pid, eid, req.voice, req.voice_stages)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


# ---- XR-5: 추적 속성의 '자동 확정 기준' 작가 오버라이드(강등 단방향·이후 커밋부터) ----
@router.patch("/projects/{pid}/attributes/{key}")
def update_attribute_auto_commit(pid: str, key: str, req: AttributeAutoCommitRequest, request: Request):
    """속성별 auto_commit 선언(""|binding|non_binding). 3값 밖·미등록 키는 400(조용한 무동작 방지).
    소급 강등 없음 — 이미 박힌 타임라인 값은 그대로고, 다음 회차 커밋부터 새 선언이 적용된다."""
    try:
        res = _svc(request).set_attribute_auto_commit(pid, key, req.auto_commit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


# ---- XR-19: 기계 binding 캐논의 작가 판정 기록(append-only — 값 자동 변경 0·무강제) ----
@router.post("/projects/{pid}/tier-review")
def record_tier_review(pid: str, req: TierReviewRequest, request: Request):
    """기계 감지로 확정된 값 1건에 대한 작가 판정(approve/dismiss/hold) 기록.
    기록일 뿐 값을 바꾸지 않는다 — 실제 정정은 공식 설정의 상태 확정(set_entity_state)으로."""
    try:
        res = _svc(request).record_tier_review(pid, req.entity_id, req.attr, req.eff_from,
                                               req.decision, req.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.get("/projects/{pid}/ontology")
def get_ontology(pid: str, request: Request):
    snap = _svc(request).ontology_snapshot(pid)
    if snap is None:
        raise HTTPException(404, "project not found")
    return snap


@router.get("/projects/{pid}/readiness")
def get_readiness(pid: str, request: Request):
    """T6: 회차 집필 진입 전 준비도 advisory(결정론·비차단). 생성 게이트 아님 — 작가 가시화용."""
    rep = _svc(request).readiness(pid)
    if rep is None:
        raise HTTPException(404, "project not found")
    return rep


@router.get("/projects/{pid}/bible")
def get_bible(pid: str, request: Request, offset: int = 0, limit: int | None = None):
    """설정집. limit 미지정=전 항목(기존 응답), 지정 시 offset 부터 limit 개(+total·has_more)."""
    snap = _svc(request).bible_snapshot(pid, offset=offset, limit=limit)
    if snap is None:
        raise HTTPException(404, "project not found")
    return snap


@router.post("/projects/{pid}/bible")
def add_bible(pid: str, req: BibleEntryRequest, request: Request):
    try:
        res = _svc(request).add_bible_entry(pid, req.category, req.title, req.prose)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.put("/projects/{pid}/bible/{entry_id}")
def update_bible(pid: str, entry_id: str, req: BibleUpdateRequest, request: Request):
    try:
        res = _svc(request).update_bible_entry(pid, entry_id, req.title, req.prose, req.category)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.delete("/projects/{pid}/bible/{entry_id}")
def delete_bible(pid: str, entry_id: str, request: Request):
    return _svc(request).delete_bible_entry(pid, entry_id)


@router.post("/projects/{pid}/bible/{entry_id}/promote")
def promote_bible(pid: str, entry_id: str, request: Request):
    try:
        res = _svc(request).promote_bible_entry(pid, entry_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.get("/projects/{pid}/worldgen")
def get_worldgen(pid: str, request: Request):
    res = _svc(request).worldgen_chat_log(pid)
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.post("/projects/{pid}/worldgen")
def worldgen_turn(pid: str, req: WorldgenTurnRequest, request: Request):
    try:
        res = _svc(request).worldgen_turn(pid, req.message)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.post("/projects/{pid}/state")
def set_state(pid: str, body: dict, request: Request):
    """작가 상태 정정(③) — 낡은/틀린 캐논 속성을 직접 박는다. {entity_id, attr, value, eff_from}"""
    try:
        res = _svc(request).set_entity_state(pid, body.get("entity_id", ""), body.get("attr", ""),
                                             body.get("value"), int(body.get("eff_from", 1)))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.put("/projects/{pid}/meta")
def update_meta(pid: str, req: ProjectMetaRequest, request: Request):
    """작품 메타(제목·한 줄 소개·소개) 작가 직접 수정 — bible/style PUT 관행(ValueError→400·None→404)."""
    try:
        res = _svc(request).update_project_meta(pid, req.title, req.premise, req.synopsis)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.put("/projects/{pid}/style")
def update_style(pid: str, req: StylePolicyRequest, request: Request):
    """문체/생성 정책(절단 훅·복선 리마인더·persona·분량 등) 작가 제어 — ③ 입력 전용."""
    try:
        res = _svc(request).update_style_policy(pid, req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


# ---- 스킬(파이프라인 지점에 꽂는 토글 증강) ----
@router.get("/projects/{pid}/skills")
def list_skills(pid: str, request: Request):
    res = _svc(request).list_skills(pid)
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.put("/projects/{pid}/skills/{sid}")
def toggle_skill(pid: str, sid: str, body: dict, request: Request):
    try:
        res = _svc(request).set_skill_enabled(pid, sid, bool(body.get("enabled")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "회차 생성 중입니다 — 잠시 후 다시 시도하세요")
    return res


@router.post("/projects/{pid}/skills")
def add_skill(pid: str, body: dict, request: Request):
    try:
        res = _svc(request).create_skill(pid, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "회차 생성 중입니다")
    return res


@router.delete("/projects/{pid}/skills/{sid}")
def remove_skill(pid: str, sid: str, request: Request):
    try:
        res = _svc(request).delete_skill(pid, sid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "회차 생성 중입니다")
    return res


# ---- 작품에 주입/해제(체크박스 대신 '주입' — injected_skills membership) ----
@router.post("/projects/{pid}/skills/{sid}/inject")
def inject_skill(pid: str, sid: str, request: Request):
    try:
        res = _svc(request).inject_skill(pid, sid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "회차 생성 중입니다 — 잠시 후 다시 시도하세요")
    return res


@router.delete("/projects/{pid}/skills/{sid}/inject")
def eject_skill(pid: str, sid: str, request: Request):
    res = _svc(request).eject_skill(pid, sid)
    if res is None:
        raise HTTPException(404, "project not found")
    if res is False:
        raise HTTPException(423, "회차 생성 중입니다 — 잠시 후 다시 시도하세요")
    return res


# ---- 전역 스킬 라이브러리(메인 화면 — 작품 간 공유 카탈로그) ----
@router.get("/skills")
def library_skills(request: Request):
    return _svc(request).library_list()


@router.post("/skills")
def library_add(body: dict, request: Request):
    try:
        return _svc(request).library_create(body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/skills/{sid}")
def library_edit(sid: str, body: dict, request: Request):
    try:
        return _svc(request).library_update(sid, body)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/skills/{sid}")
def library_remove(sid: str, request: Request):
    try:
        return _svc(request).library_delete(sid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{pid}/spine")
def get_spine(pid: str, request: Request):
    snap = _svc(request).spine_snapshot(pid)
    if snap is None:
        raise HTTPException(404, "project not found")
    return snap


@router.get("/projects/{pid}/ending-contract")
def get_ending_contract(pid: str, request: Request):
    """EC-1: 엔딩 술어계약 현황(advisory — 비차단·LLM 0콜). gt/ni 병렬 표시(단일 판정 없음),
    미표현 엔딩 요소·완결 정산 스냅샷·stale 여부 포함. '상태 기준' 감시임을 notes 로 함께 노출."""
    res = _svc(request).ending_contract_status(pid)
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.get("/projects/{pid}/retrospective")
def get_retrospective(pid: str, request: Request):
    """G3: 연재 회고 제안(읽기 전용) — 페이싱 진단 + 남은 아크/엔딩 개정안. 적용은 작가 승인(아래 POST)."""
    res = _svc(request).arc_retrospective(pid)
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.post("/projects/{pid}/genre-contract/backfill")
def backfill_genre_contract(pid: str, request: Request):
    """M-2: G5 이전 작품의 장르 계약을 추론해 채운다(작가 요청). narrative 컨텍스트 — 캐논 아님."""
    try:
        res = _svc(request).backfill_genre_contract(pid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.post("/projects/{pid}/spine/revise")
def revise_spine(pid: str, body: dict, request: Request):
    """G3: 작가 승인한 개정만 반영 — 미집필 아크 카드/엔딩만. {revisions:[{target,field,new_value}]}"""
    try:
        res = _svc(request).revise_spine(pid, body.get("revisions") or [])
    except ValueError as e:
        raise HTTPException(400, str(e))
    if res is None:
        raise HTTPException(404, "project not found")
    return res


@router.get("/projects/{pid}/wiki")
def get_wiki(pid: str, request: Request, offset: int = 0, limit: int | None = None):
    """작품 노트. limit 미지정=전 페이지(기존 응답), 지정 시 offset 부터 limit 개(+total·has_more)."""
    snap = _svc(request).wiki_snapshot(pid, offset=offset, limit=limit)
    if snap is None:
        raise HTTPException(404, "project not found")
    return snap


@router.get("/projects/{pid}/chapters")
def list_chapters(pid: str, request: Request, offset: int = 0, limit: int = 20,
                  view: str = "summary"):
    """회차 목록 페이지 — {items, total, offset, limit, has_more}.

    view= summary(본문 없는 메타·기본) | text(요약+본문 — 뷰어·내보내기) | full(전체 레코드).
    한 번에 전 회차를 싣던 프로젝트 GET 의 부담을 이 창으로 나눈다."""
    if view not in ("summary", "text", "full"):
        raise HTTPException(400, "view 는 summary|text|full")
    res = _svc(request).chapters_page(pid, offset=offset, limit=limit, view=view)
    if res is None:
        raise HTTPException(404, "project not found")
    return res


# 주의: '/chapters/regenerate-last' 같은 리터럴 경로는 '/chapters/{n}'(n:int)보다 *먼저* 등록해야
# FastAPI 가 "regenerate-last" 를 {n} 으로 매칭(→422)하지 않는다(라우트 순서=구체적 우선).
@router.get("/projects/{pid}/chapters/regenerate-last")
async def regenerate_last_chapter(pid: str, request: Request, fix: str = ""):
    """SSE: 마지막 회차만 재생성. 반영 전 전체 상태를 백업(되돌리기 가능)하고 그 회차를 다시 뽑아 진행을 스트리밍.
    fix: '점검 반영 재생성'의 1회성 교정 지시(선택, EventSource=GET 이라 쿼리). 마지막 회차 한정 — 고아 없음."""
    res = _svc(request).regenerate_last_chapter(pid, fix_instruction=(fix or None))
    if res is None:
        raise HTTPException(404, "project or last chapter not found")
    job, _created = res
    return _stream_job(job)


@router.post("/projects/{pid}/chapters/restore-last-regen")
def restore_last_regen(pid: str, request: Request):
    """마지막 재생성 '되돌리기' — 반영 전 백업으로 복원."""
    res = _svc(request).restore_last_regen(pid)
    if res is None:
        raise HTTPException(404, "no regeneration backup to restore")
    return res


@router.get("/projects/{pid}/chapters/{n}")
def get_chapter(pid: str, n: int, request: Request, view: str = "full"):
    """회차 단건. view= full(기본·전체 레코드) | text(요약+본문 — 읽기 화면) | summary(메타만).
    읽기만 하는 화면이 퇴고 이력·생성 컨텍스트까지 받지 않게 자른다."""
    if view not in ("full", "text", "summary"):
        raise HTTPException(400, "view 는 full|text|summary")
    svc = _svc(request)
    state = svc.get_project(pid)
    if not state:
        raise HTTPException(404, "project not found")
    ch = state.chapter(n)
    if not ch:
        raise HTTPException(404, "chapter not found")
    return svc.chapter_view(ch, view)


# GA-2: 회차 생성 트레이스 사이드카 열람(읽기 전용) — repo.load_trace 그대로 반환.
#   결측(사이드카 없음·GA-1 도입 전 회차·기록 미수집)=404 정직. n=0 허용 = 작품 스코프 샤드
#   (설정집·엔티티·관계·스파인 등 회차 밖 author_intent 이벤트). 어떤 상태·트레이스도 변형하지 않는다.
@router.get("/projects/{pid}/chapters/{n}/trace")
def get_chapter_trace(pid: str, n: int, request: Request):
    doc = _svc(request).repo.load_trace(pid, n)
    if doc is None:
        raise HTTPException(404, "trace not found")
    return doc


# ---- 퇴고(회차 본문 사후 다듬기 — 사실 불변) ----
@router.post("/projects/{pid}/chapters/{n}/revise")
def revise_chapter(pid: str, n: int, req: ReviseRequest, request: Request):
    """후보 생성 — 작가 지시로 회차 산문을 다듬고 before/after·가드레일 반환(저장 안 함)."""
    passes = [p for p in (req.passes or []) if p in ("reformat", "fix_tense")]   # D1: 허용 pass 만(우회 차단)
    try:
        result = _svc(request).revise_chapter(pid, n, req.directive, req.span_text, passes)
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        if "span_not_found" in str(e):
            raise HTTPException(400, "구간을 원문에서 찾을 수 없습니다")
        raise HTTPException(400, str(e))
    if result is None:   # 423 Locked: 회차 생성 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


@router.post("/projects/{pid}/chapters/{n}/revise/accept")
def accept_revision(pid: str, n: int, req: ReviseAcceptRequest, request: Request):
    """후보 채택 → 새 버전 저장. 서버 가드레일 재검증 실패 시 409."""
    try:
        result = _svc(request).accept_revision(pid, n, req.revision_id, req.after_text,
                                               req.span_text, req.passes)
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    if result is None:   # 423 Locked: 회차 생성 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


@router.post("/projects/{pid}/chapters/{n}/edit")
def edit_chapter(pid: str, n: int, req: EditRequest, request: Request):
    """DE-1: 작가 직접 편집 — 본문을 작가가 직접 고쳐 저장(가드 재검증·요약 갱신 자동, 되돌리기 가능).

    revise/accept 라우트 관행을 따른다: ValueError→400(사유), None→423(회차 생성 중), KeyError→404."""
    try:
        result = _svc(request).edit_chapter(
            pid, n, req.new_text, req.span_text, req.replacement,
            edits=([{"span_text": e.span_text, "replacement": e.replacement} for e in req.edits]
                   if req.edits is not None else None))
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:   # 423 Locked: 회차 생성 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


@router.post("/projects/{pid}/chapters/{n}/revise/undo")
def undo_revision(pid: str, n: int, request: Request):
    """마지막 채택 되돌리기 — text/summary/detail_synopsis 복원."""
    try:
        result = _svc(request).undo_revision(pid, n)
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/projects/{pid}/chapters/{n}/derivatives/recompute")
def recompute_derivative(pid: str, n: int, req: DerivativeRecomputeRequest, request: Request):
    """XR-7③: 퇴고로 '옛 본문 기준'이 된 파생물 하나를 현재 본문으로 다시 계산(작가 발동 — 자동 호출 0).

    지원: wiki · claim_audit · reader_feedback · dialogue_ledger(회차당 0~1콜, 전부 기존 기계 재사용).
    promise_ledger 는 후행 회차 지불 이력과 얽혀 별도 설계 대상이라 400+사유로 반려한다(정직 미지원)."""
    try:
        result = _svc(request).recompute_derivative(pid, n, (req.name or "").strip())
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/projects/{pid}/wiki/rebuild")
def rebuild_wiki(pid: str, request: Request):
    """XR-10 정식(cross-review/007 §3): Wiki Projection 전체 재구축(작가 발동 — 자동 호출 0).

    확정 회차를 시간순 replay 해 인물카드를 새로 합성한다(회차당 1콜). 회차별 재적재(force)와 달리 옛
    누적 본문을 입력으로 쓰지 않아, 퇴고 전 원고에서 들어간 서술이 남지 않는다. 성공 시에만 전 회차의
    위키 stale 표식이 걷혀 생성 제외가 해제된다."""
    try:
        result = _svc(request).rebuild_wiki(pid)
    except KeyError:
        raise HTTPException(404, "project not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/projects/{pid}/chapters/{n}/rerender")
def rerender_chapter(pid: str, n: int, request: Request):
    """ST-12c: 비앵커 재실현 패스(작가 opt-in) — 사실 뼈대에서 웹소설 레지스터로 회차를 다시 실현.

    BoN-3 후보를 Kiwi 대역 최근접으로 리랭크 → 가드 스택 통과 시 채택(기존 revision 기록·undo 가능).
    원문은 항상 보존 — 전 후보 실격·가드 불통과 = 원문 유지 + 정직 사유({adopted:false, reason}). 423=회차 생성 중."""
    try:
        result = _svc(request).rerender_chapter(pid, n)
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        # 락 밖 LLM 콜 사이 동시 생성으로 본문이 교체된 재시도성 충돌은 409(accept 라우트와 동형) — 400 오귀속 방지.
        if "본문이 생성 사이 변경됨" in str(e):
            raise HTTPException(409, str(e))
        raise HTTPException(400, str(e))
    if result is None:   # 423 Locked: 회차 생성 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


# ---- 빨간펜(마음에 안 드는 구간 표시 + 옵트인 방향 제안 — RP-1) ----
@router.post("/projects/{pid}/chapters/{n}/redpen")
def add_redpen_mark(pid: str, n: int, req: RedpenAddRequest, request: Request):
    """작가가 표시한 '마음에 안 드는' 구간+이유 메모를 추가(본문 불변).

    edit/revise 라우트 관행: ValueError→400(사유), None→423(회차 생성 중), KeyError→404."""
    try:
        result = _svc(request).add_redpen_mark(pid, n, req.anchor_text, req.span_start,
                                               req.span_len, req.note)
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:   # 423 Locked: 회차 생성 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


@router.delete("/projects/{pid}/chapters/{n}/redpen/{mark_id}")
def delete_redpen_mark(pid: str, n: int, mark_id: str, request: Request):
    """빨간펜 마크 1건 삭제(본문 불변). 미존재 마크·작품·회차=404, 생성 중=423."""
    try:
        result = _svc(request).delete_redpen_mark(pid, n, mark_id)
    except KeyError:
        raise HTTPException(404, "project, chapter, or mark not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:   # 423 Locked: 회차 생성 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


@router.post("/projects/{pid}/chapters/{n}/redpen/suggest")
def suggest_redpen_directions(pid: str, n: int, req: RedpenSuggestRequest, request: Request):
    """옵트인 방향 제안(작가 발동 1콜) — 유효 마크마다 수정 '방향' 2~3개. 본문 불변·advisory."""
    try:
        result = _svc(request).suggest_redpen_directions(pid, n, req.mark_ids)
    except KeyError:
        raise HTTPException(404, "project, chapter, or mark not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:   # 423 Locked: 회차 생성 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


@router.get("/projects/{pid}/chapters/{n}/revisions")
def get_revisions(pid: str, n: int, request: Request):
    """퇴고 이력 조회(읽기 전용)."""
    state = _svc(request).get_project(pid)
    if not state:
        raise HTTPException(404, "project not found")
    ch = state.chapter(n)
    if not ch:
        raise HTTPException(404, "chapter not found")
    return {"revisions": [RevisionSummary(
        revision_id=r.revision_id, directive=r.directive, created_at=r.created_at,
        reverted=r.reverted,
        # guardrail_passed 는 Optional[bool] — None(미검사/구형 레코드)을 bool(None)=False 로
        # '실패' 오보하던 버그 수정. accept 는 guardrail 통과 후에만 기록(=True)하므로 None 은 통과로 매핑.
        guardrail_passed=(r.guardrail_passed if r.guardrail_passed is not None else True),
        span_text=r.span_text).model_dump() for r in ch.revisions]}


# ---- EP-PUB: 외부 플랫폼 수동 발행 원장(툴은 업로드하지 않음 — 발행 사실+지문만 기록) ----
@router.post("/projects/{pid}/chapters/{n}/publish")
def mark_chapter_published(pid: str, n: int, request: Request, req: PublishRequest | None = None):
    """이 회차를 '발행함'으로 표시(이미 발행됐으면 재발행 — 지문·시각을 현재 본문으로 갱신). 본문 불변·LLM 0.
    redpen 라우트 관행: ValueError→400(빈 본문 등), None→423(생성/편집 중), KeyError→404."""
    try:
        result = _svc(request).mark_published(pid, n, (req.note if req else None))
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:   # 423 Locked: 회차 생성/편집 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


@router.delete("/projects/{pid}/chapters/{n}/publish")
def unmark_chapter_published(pid: str, n: int, request: Request):
    """발행 표시 해제(미발행으로 되돌림). 본문 불변·LLM 0. None→423, KeyError→404."""
    try:
        result = _svc(request).unmark_published(pid, n)
    except KeyError:
        raise HTTPException(404, "project or chapter not found")
    if result is None:   # 423 Locked: 회차 생성/편집 중(lost-update 방지)
        raise HTTPException(423, "회차 생성 중입니다")
    return result


def _zip_entry_name(s: str) -> str:
    """zip 엔트리 파일명 안전화 — OS 금지 문자(\\/:*?"<>|)·제어문자를 공백으로."""
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", s)).strip()


@router.get("/projects/{pid}/export")
def export_project(pid: str, request: Request, fmt: str = "txt"):
    """작품을 zip 으로 다운로드 — 회차당 문서 1개(fmt=txt|md). 본문 있는 회차만 순서대로."""
    state = _svc(request).get_project(pid)
    if not state:
        raise HTTPException(404, "project not found")
    title = (state.world.title or "무제").strip()
    chs = sorted([c for c in state.chapters if (c.text or "").strip()], key=lambda c: c.chapter)
    ext = "md" if fmt == "md" else "txt"
    pad = max(3, len(str(chs[-1].chapter))) if chs else 3   # 번호 0패딩 → 압축 해제 후 이름순 = 연재순
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if state.world.premise:   # 작품 소개는 별도 문서(회차 문서는 본문만) — 이름순으로 맨 앞
            intro = (f"# {title}\n\n> {state.world.premise}\n" if ext == "md"
                     else f"{title}\n{'=' * max(4, len(title) * 2)}\n\n{state.world.premise}\n")
            zf.writestr(f"{'0' * pad} 작품 소개.{ext}", intro)
        for c in chs:
            head = f"{c.chapter}화 · {c.title or ''}".rstrip(" ·")
            body = (f"# {head}\n\n{collapse_dashes(c.text).strip()}\n" if ext == "md"   # .md 는 마크다운 유효 — 줄표 런만 정규화
                    else f"{head}\n{'-' * 24}\n\n{md_to_plain(c.text).strip()}\n")      # .txt 경계: 마크다운→평문(&nbsp;·#·**·--- 누수 차단)
            entry = _zip_entry_name(f"{c.chapter:0{pad}d}화 · {c.title or ''}".rstrip(" ·"))
            zf.writestr(f"{entry}.{ext}", body)
    fname = quote(f"{title}.zip")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=\"export.zip\"; filename*=UTF-8''{fname}"})


def _stream_job(job, note: str | None = None) -> StreamingResponse:
    """진행 중(또는 갓 끝난) 생성 잡을 SSE 로 — 버퍼를 처음부터 리플레이(재접속 복원)한 뒤
    라이브 테일하고, 종료 시 complete/failed 를 보낸다. 클라이언트 연결이 끊겨도(아래 stream 취소)
    생성 스레드는 잡에서 계속 돈다 — 다음 접속이 같은 잡에 다시 붙는다.
    note: 기존 진행 잡에 '합류'한 경우(이번 directive 미반영) 작가에게 알릴 한 줄."""
    async def gen():
        yield _sse("start", {"chapter": job.chapter, **({"note": note} if note else {})})
        cursor = 0
        while True:
            new, status, result, error = job.snapshot_from(cursor)
            cursor += len(new)
            for e in new:
                yield _sse("event", e)
            if status == "running":
                await asyncio.sleep(0.12)
                continue
            # 종료: snapshot 에서 status 가 종료면 잔여 이벤트는 위에서 모두 흘렸다(생산자는 unsub 후 종료 설정).
            if status == "done":
                yield _sse("complete", result or {})
            else:
                yield _sse("failed", error or {"message": "생성에 실패했습니다"})
            break
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/projects/{pid}/generate")
async def generate_chapter(pid: str, request: Request, directive: str = ""):
    """SSE: 회차 생성을 백그라운드 잡으로 '시작 또는 재접속'(멱등)하고 진행을 스트리밍.
    이미 진행 중이면 새로 만들지 않고 그 잡에 붙는다(중복 회차 방지). 연결이 끊겨도 생성은 계속된다."""
    res = _svc(request).start_generation(pid, directive or None)
    if res is None:
        raise HTTPException(404, "project not found")
    job, created = res
    note = None
    req_dir = (directive or "").strip()
    if not created and req_dir and req_dir != job.directive:   # 다른 탭이 이미 진행 중 — 이번 지시는 묻힌다(정직하게 통지)
        note = "이미 진행 중인 생성에 합류했어요 — 입력하신 지시는 이번 회차엔 반영되지 않아요(다음 회차에 다시 적어 주세요)."
    return _stream_job(job, note)


@router.get("/projects/{pid}/generation")
def generation_status(pid: str, request: Request):
    """페이지 로드/폴링 — 진행 중 생성 유무(+끝났으면 결과). status: idle|running|done|failed."""
    return _svc(request).generation_status(pid)


@router.get("/projects/{pid}/generation/stream")
async def generation_stream(pid: str, request: Request):
    """진행 중 잡에만 '재접속'(시작 안 함) — 새로고침 복원 전용. 잡이 없으면 즉시 종료 이벤트로 닫는다."""
    job = _svc(request).get_generation_job(pid)
    if job is not None and job.status == "running":
        return _stream_job(job)

    async def once():
        if job is not None and job.status == "done":
            yield _sse("complete", job.result or {})
        elif job is not None and job.status == "failed":
            yield _sse("failed", job.error or {"message": "생성에 실패했습니다"})
        else:
            yield _sse("idle", {})
    return StreamingResponse(once(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- SY-1 스토리 패스(설계 §6) — 승격 경로는 confirm 하나뿐(자동 승격 0) ----

@router.post("/projects/{pid}/story-pass")
def run_story_pass(pid: str, req: StoryPassRunRequest, request: Request):
    try:
        return _svc(request).run_story_pass(pid, chapter=req.chapter, pillar=req.pillar, role=req.role)
    except KeyError:
        raise HTTPException(404, "project not found")
    except RuntimeError as e:          # StoryPassNotReady 포함 — 시끄러운 실패를 그대로 전달
        raise HTTPException(409, str(e))


@router.get("/projects/{pid}/story-pass/{n}")
def get_story_pass(pid: str, n: int, request: Request):
    try:
        return _svc(request).get_story_pass(pid, n)
    except KeyError:
        raise HTTPException(404, "project not found")


@router.post("/projects/{pid}/story-pass/{n}/confirm")
def confirm_story(pid: str, n: int, req: StoryConfirmRequest, request: Request):
    try:
        res = _svc(request).confirm_story(pid, n, req.story, source=req.source)
    except KeyError:
        raise HTTPException(404, "project not found")
    if not res.get("ok"):
        raise HTTPException(400, detail=str(res))   # 형식 불합격 — 위반 목록 반환(내용 판정 0)
    return res


@router.post("/projects/{pid}/story-pass/{n}/discard")
def discard_story_pass(pid: str, n: int, request: Request):
    try:
        return _svc(request).discard_story_pass(pid, n)
    except KeyError:
        raise HTTPException(404, "project not found")
