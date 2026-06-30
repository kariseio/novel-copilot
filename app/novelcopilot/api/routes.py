# -*- coding: utf-8 -*-
"""HTTP 라우트 + SSE 실시간 하네스 스트리밍."""
from __future__ import annotations
import asyncio
import json
import queue

from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, Response

from ..domain.project import ProjectSeed
from ..engine.textfmt import md_to_plain, collapse_dashes
from .schemas import (CreateProjectRequest, DirectiveRequest, EntityRequest,
                      RelationRequest, EndRelationRequest, BibleEntryRequest, BibleUpdateRequest,
                      WorldgenTurnRequest, StylePolicyRequest,
                      ReviseRequest, ReviseAcceptRequest, RevisionSummary)

router = APIRouter(prefix="/api")


def _svc(request: Request):
    return request.app.state.service


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


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


@router.get("/projects/{pid}")
def get_project(pid: str, request: Request):
    state = _svc(request).get_project(pid)
    if not state:
        raise HTTPException(404, "project not found")
    return {
        "id": state.id, "seed": state.seed.model_dump(), "world": state.world.model_dump(),
        "created_at": state.created_at, "current_chapter": state.current_chapter,
        "total_beats": state.seed.target_chapters or len(state.world.beats),
        "has_spine": state.world.spine is not None,
        "completed": state.narrative_progress.completed, "usage_total": state.usage_total,
        "directives": [d.model_dump() for d in state.directives],
        "chapters": [c.model_dump() for c in state.chapters],
    }


@router.delete("/projects/{pid}")
def delete_project(pid: str, request: Request):
    return {"deleted": _svc(request).delete_project(pid)}


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


@router.get("/projects/{pid}/ontology")
def get_ontology(pid: str, request: Request):
    snap = _svc(request).ontology_snapshot(pid)
    if snap is None:
        raise HTTPException(404, "project not found")
    return snap


@router.get("/projects/{pid}/bible")
def get_bible(pid: str, request: Request):
    snap = _svc(request).bible_snapshot(pid)
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
def get_wiki(pid: str, request: Request):
    snap = _svc(request).wiki_snapshot(pid)
    if snap is None:
        raise HTTPException(404, "project not found")
    return snap


@router.get("/projects/{pid}/chapters/{n}")
def get_chapter(pid: str, n: int, request: Request):
    state = _svc(request).get_project(pid)
    if not state:
        raise HTTPException(404, "project not found")
    ch = state.chapter(n)
    if not ch:
        raise HTTPException(404, "chapter not found")
    return ch.model_dump()


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


@router.get("/projects/{pid}/export")
def export_project(pid: str, request: Request, fmt: str = "txt"):
    """작품 전체를 다운로드 파일로 — fmt=txt|md. 본문 있는 회차를 순서대로 합쳐 원고로."""
    state = _svc(request).get_project(pid)
    if not state:
        raise HTTPException(404, "project not found")
    title = (state.world.title or "무제").strip()
    chs = sorted([c for c in state.chapters if (c.text or "").strip()], key=lambda c: c.chapter)
    if fmt == "md":
        parts = [f"# {title}", ""]
        if state.world.premise:
            parts += [f"> {state.world.premise}", ""]
        for c in chs:
            parts += [f"## {c.chapter}화 · {c.title or ''}".rstrip(" ·"), "", collapse_dashes(c.text).strip(), ""]   # .md 는 마크다운 유효 — 줄표 런만 정규화
        body, ext, mime = "\n".join(parts), "md", "text/markdown"
    else:   # txt
        parts = [title, "=" * max(4, len(title) * 2)]
        if state.world.premise:
            parts += ["", state.world.premise]
        for c in chs:
            head = f"{c.chapter}화 · {c.title or ''}".rstrip(" ·")
            parts += ["", "", head, "-" * 24, "", md_to_plain(c.text).strip()]   # .txt 경계: 마크다운→평문(&nbsp;·#·**·--- 누수 차단)
        body, ext, mime = "\n".join(parts), "txt", "text/plain"
    fname = quote(f"{title}.{ext}")
    return Response(content=body, media_type=f"{mime}; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=\"export.{ext}\"; filename*=UTF-8''{fname}"})


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
