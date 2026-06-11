# -*- coding: utf-8 -*-
"""HTTP 라우트 + SSE 실시간 하네스 스트리밍."""
from __future__ import annotations
import asyncio
import json
import queue

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from ..domain.project import ProjectSeed
from .schemas import (CreateProjectRequest, DirectiveRequest, EntityRequest,
                      RelationRequest, EndRelationRequest, BibleEntryRequest, BibleUpdateRequest,
                      WorldgenTurnRequest, StylePolicyRequest)

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
        "total_beats": len(state.world.beats) or state.seed.target_chapters,
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


@router.get("/projects/{pid}/spine")
def get_spine(pid: str, request: Request):
    snap = _svc(request).spine_snapshot(pid)
    if snap is None:
        raise HTTPException(404, "project not found")
    return snap


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


@router.get("/projects/{pid}/generate")
async def generate_chapter(pid: str, request: Request, directive: str = ""):
    """SSE: 하네스 루프 이벤트를 실시간 방출하고, 마지막에 회차 결과를 보낸다."""
    svc = _svc(request)
    sess, state = svc.get_session(pid)
    if not sess:
        raise HTTPException(404, "project not found")

    loop = asyncio.get_event_loop()
    q: "queue.Queue" = queue.Queue()
    unsub = sess.bus.subscribe(lambda e: q.put(("event", e)))

    def work():
        try:
            result = svc.generate_next_chapter(pid, directive or None)
            if result.get("completed"):          # R4: 엔딩 도달/하드캡 → 완결(회차 미생성)
                q.put(("complete", {"completed": True, "reason": result.get("reason"),
                                    "current_chapter": result.get("current_chapter"),
                                    "total_beats": result.get("total_beats")}))
            else:
                q.put(("complete", {"record": result["record"].model_dump(),
                                    "usage_delta": result["usage_delta"], "usage_total": result["usage_total"],
                                    "failures": result["failures"], "current_chapter": result["current_chapter"],
                                    "total_beats": result["total_beats"], "completed": False}))
        except Exception as e:  # noqa
            q.put(("failed", {"message": str(e)}))

    async def stream():
        fut = loop.run_in_executor(None, work)
        try:
            yield _sse("start", {"chapter": state.current_chapter + 1})
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
