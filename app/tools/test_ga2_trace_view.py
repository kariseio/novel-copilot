# -*- coding: utf-8 -*-
"""GA-2/FI-2 검증 — 생성 트레이스 디버그 뷰어(CLI + 웹 라우트). 실 LLM 0콜·결정론·읽기 전용.

GA-2: GA-1 사이드카(첫 초안·판정·수술·후보·이벤트·실패)를 사람이 읽는 시간순 타임라인으로 렌더.
      ① 웹 라우트 GET /projects/{pid}/chapters/{n}/trace — 200/404/ch=0(작품 스코프 샤드).
      ② CLI trace_view.py 렌더 스모크 — 합성 트레이스 dict→렌더 문자열에 실패 하이라이트·이벤트 포함.
FI-2: 위 위에 author_intent kind 렌더 + 3원천 조인(trace runs+revisions+regen_events) + 필터.
      ③ author_intent 인용이 렌더에 포함(measure-then-cite).
      ④ 조인 정렬 — 혼재 ts 포맷(naive vs %z)이 tz 벗김 정규화로 안정 정렬.
      ⑤ 필터(kind/actor/surface) 정확성.

읽기 전용 계약: 뷰어·라우트 어느 경로도 상태·트레이스를 변형하지 않는다(순수 조회).

실행: (app/ 에서) py -3.12 -X utf8 -m pytest tools/test_ga2_trace_view.py -q
"""
from __future__ import annotations
import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot
sys.path.insert(0, str(_HERE))          # tools/ → trace_view

from novelcopilot.domain.project import ProjectState, ProjectSeed, RegenEvent
from novelcopilot.domain.world import WorldConfig
from novelcopilot.domain.types import ChapterRecord, ChapterRevision, ChapterStatus
from novelcopilot.repository import FilesystemProjectRepository

import trace_view


# ═════════════════ 합성 트레이스 재료(결정론) ═════════════════

def _gen_run(ts="2026-07-15T10:00:00"):
    """이벤트 타임라인·실패 승격을 담은 generate run(GA-1 서비스 저장부 스키마)."""
    return {"kind": "generate", "chapter": 3, "ts": ts, "status": "FINALIZED",
            "first_draft": "가나다라마바사", "final_text": "가나ZZ마바사",   # 수술 diff(코어 '다라'→'ZZ')
            "rewrite_rounds": [{"round": 0, "fixing": ["worldrule(x)"], "text": "..."}],
            "style_judgment": {"needs_repair": True, "reason": "문말이 단조롭다",
                               "spans": [{"quote": "그는 걸었다. 멈췄다.", "why": "같은 어미 반복"}]},
            "humanize_spans": [{"category": "N-4", "severity": "S2", "changed": True,
                                "before": "옛문장", "after": "새문장", "author_review": True}],
            "events": [{"seq": 0, "node": "draft_chapter", "event": "start", "chapter": 3},
                       {"seq": 1, "node": "finalize", "event": "escalation", "chapter": 3, "hard": ["x"]}],
            "failures": [{"node": "finalize", "event": "escalation", "chapter": 3, "hard": ["x"]}]}


def _author_intent_run(surface="revise_propose", actor="author", ts="2026-07-15T10:00:05+0900",
                       directive="이 대목 문형을 바꿔라", core=True):
    p = {"directive": directive, "span_text": "응, 그걸로 해두자."}
    if core:
        p["core"] = {"before_len": 10, "after_len": 12, "core_offset": 3,
                     "core_before": "구본", "core_after": "신본"}
        p["guardrail_passed"] = True
    return {"kind": "author_intent", "v": 1, "ts": ts, "actor": actor,
            "surface": surface, "chapter": 3, "gen_no": 1, "payload": p,
            "ref": {"revision_id": "abc123"}}


def _seed_data(tmp: pathlib.Path, *, with_intent=True):
    """tmp/projects 에 트레이스 샤드 + 프로젝트 본체(revisions·regen)를 심는다. NOVEL_DATA_DIR=tmp 로 뷰어가 읽음."""
    repo = FilesystemProjectRepository(tmp)
    pid = "pv"
    repo.save_trace(pid, 3, _gen_run())
    if with_intent:
        repo.save_trace(pid, 3, _author_intent_run(surface="revise_propose", actor="author"))
        repo.save_trace(pid, 3, _author_intent_run(surface="bible_edit", actor="tool",
                                                    ts="2026-07-15T10:00:09+0900", directive="설정집 정정", core=False))
    rec = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="본문",
                        revisions=[ChapterRevision(revision_id="rev1", directive="문장 다듬기",
                                                   before_text="원문 앞부분 여기", after_text="다듬은 앞부분 여기",
                                                   created_at="2026-07-15T10:00:04", guardrail_passed=True)])
    st = ProjectState(id=pid, seed=ProjectSeed(title="뷰어작"), world=WorldConfig(title="뷰어작"),
                      chapters=[rec], current_chapter=3,
                      regen_events=[RegenEvent(chapter=3, seq=1, at="2026-07-15T10:00:02")])
    repo.save(st)
    return repo, pid


class _EnvData:
    """NOVEL_DATA_DIR 를 tmp 로 세팅했다가 복원(뷰어 CLI 가 그 경로에서 읽게) — pytest·main 공용."""
    def __init__(self, tmp): self.tmp = str(tmp); self._old = None
    def __enter__(self):
        self._old = os.environ.get("NOVEL_DATA_DIR")
        os.environ["NOVEL_DATA_DIR"] = self.tmp
        return self
    def __exit__(self, *a):
        if self._old is None:
            os.environ.pop("NOVEL_DATA_DIR", None)
        else:
            os.environ["NOVEL_DATA_DIR"] = self._old


# ═════════════════ ① 웹 라우트 200/404/ch=0 ═════════════════

def _build_app(tmp):
    import novelcopilot.config as cfg
    from novelcopilot.main import create_app
    os.environ["NOVEL_DATA_DIR"] = str(tmp)
    cfg._settings = None   # 싱글톤 재생성(임시 data_dir 반영)
    app = create_app()
    return app, app.state.service


def test_route_200_and_404_and_ch0(tmp_path):
    """① 라우트 — 사이드카 있으면 200(load_trace 그대로)·없으면 404 정직·ch=0 작품 스코프 허용."""
    from fastapi.testclient import TestClient
    old = os.environ.get("NOVEL_DATA_DIR")
    try:
        app, svc = _build_app(tmp_path)
        client = TestClient(app)
        svc.repo.save_trace("pv", 3, _gen_run())
        svc.repo.save_trace("pv", 0, _author_intent_run(surface="bible_edit", actor="author"))   # 작품 스코프 샤드
        r200 = client.get("/api/projects/pv/chapters/3/trace")
        r404 = client.get("/api/projects/pv/chapters/9/trace")     # 사이드카 없음
        r0 = client.get("/api/projects/pv/chapters/0/trace")       # ch=0 허용
        ok = (r200.status_code == 200 and r200.json()["runs"][0]["kind"] == "generate")
        ok &= (r404.status_code == 404)
        ok &= (r0.status_code == 200 and r0.json()["runs"][0]["surface"] == "bible_edit")
        print(f"[{'OK' if ok else 'FAIL'}] ① 라우트 200/404/ch=0")
        assert ok
    finally:
        import novelcopilot.config as cfg
        cfg._settings = None
        if old is None:
            os.environ.pop("NOVEL_DATA_DIR", None)
        else:
            os.environ["NOVEL_DATA_DIR"] = old


def test_route_readonly_no_mutation(tmp_path):
    """①b 읽기 전용 — GET 트레이스가 사이드카 바이트를 바꾸지 않는다."""
    from fastapi.testclient import TestClient
    old = os.environ.get("NOVEL_DATA_DIR")
    try:
        app, svc = _build_app(tmp_path)
        client = TestClient(app)
        svc.repo.save_trace("pv", 3, _gen_run())
        p = tmp_path / "projects" / "pv.trace.3.json"
        before = p.read_bytes()
        client.get("/api/projects/pv/chapters/3/trace")
        ok = (p.read_bytes() == before)   # 바이트 불변(순수 조회)
        print(f"[{'OK' if ok else 'FAIL'}] ①b 라우트 읽기 전용(바이트 불변)")
        assert ok
    finally:
        import novelcopilot.config as cfg
        cfg._settings = None
        if old is None:
            os.environ.pop("NOVEL_DATA_DIR", None)
        else:
            os.environ["NOVEL_DATA_DIR"] = old


# ═════════════════ ② CLI 렌더 스모크 — 실패 하이라이트·이벤트 ═════════════════

def test_cli_render_generate_failure_and_events(tmp_path):
    """② generate run 렌더에 실패 하이라이트(⚠)·이벤트 타임라인·수술 diff 가 나온다(전문 덤프 아님)."""
    with _EnvData(tmp_path):
        _seed_data(tmp_path, with_intent=False)
        out = trace_view.render("pv", 3)
    ok = ("생성 (generate)" in out)
    ok &= ("⚠ 실패 1건" in out)                       # 실패 승격 하이라이트
    ok &= ("finalize.escalation" in out)              # 실패 이벤트 노드
    ok &= ("이벤트 타임라인 2건" in out)              # 이벤트 전량
    ok &= ("변경 코어" in out)                         # 수술 diff 요약(길이·발췌)
    ok &= ("같은 어미 반복" in out)                    # style_judge 인용
    print(f"[{'OK' if ok else 'FAIL'}] ② CLI generate 렌더(실패 하이라이트·이벤트·수술 diff·판정 인용)")
    assert ok


# ═════════════════ ③ author_intent 인용 포함(FI-2) ═════════════════

def test_cli_render_author_intent_quote(tmp_path):
    """③ author_intent 렌더에 지시 원문 인용이 그대로 나온다(measure-then-cite)."""
    with _EnvData(tmp_path):
        _seed_data(tmp_path, with_intent=True)
        out = trace_view.render("pv", 3)
    ok = ("작가 의도 (author_intent)" in out)
    ok &= ("revise_propose" in out and "actor=author" in out)
    ok &= ("이 대목 문형을 바꿔라" in out)             # 지시 원문 인용
    ok &= ("설정집 정정" in out)                        # 캐논 정정 표면 지시 원문
    print(f"[{'OK' if ok else 'FAIL'}] ③ author_intent 원문 인용 렌더")
    assert ok


# ═════════════════ ④ 3원천 조인 + 혼재 ts 안정 정렬(FI-2) ═════════════════

def test_join_and_mixed_ts_stable_sort(tmp_path):
    """④ trace runs(author_intent 포함)+revisions+regen_events 조인 + 혼재 ts(naive vs %z) 안정 정렬.

    정규화 키 순서: generate 10:00:00 < regen 10:00:02 < revision 10:00:04 < ai(revise) 10:00:05+0900 < ai(bible) 10:00:09+0900.
    tz 벗김이 안 되면 '+0900' 문자가 붙어 정렬이 흐트러진다 → 벗김 정규화가 혼재 포맷을 한 축으로 세우는지 검증."""
    with _EnvData(tmp_path):
        _seed_data(tmp_path, with_intent=True)
        out = trace_view.render("pv", 3)
    # 각 항목 헤더의 등장 위치로 순서 확인
    pos = {name: out.find(name) for name in
           ("재생성 실행 (regen)", "퇴고 채택 (revision)", "생성 (generate)",
            "revise_propose", "bible_edit")}
    ok = all(v >= 0 for v in pos.values())
    ok &= (pos["생성 (generate)"] < pos["재생성 실행 (regen)"] < pos["퇴고 채택 (revision)"]
           < pos["revise_propose"] < pos["bible_edit"])
    # 조인 3원천 전부 등장 + 건수 요약(비율 아님): gen1 + ai(revise) + ai(bible) + revision1 + regen1 = 5
    ok &= ("필터 결과 5건" in out)
    print(f"[{'OK' if ok else 'FAIL'}] ④ 3원천 조인·혼재 ts 안정 정렬")
    assert ok


# ═════════════════ ⑤ 필터 정확성(FI-2) ═════════════════

def test_filters_kind_actor_surface(tmp_path):
    """⑤ 필터 — kind/actor/surface 각각 정확히 좁힌다."""
    with _EnvData(tmp_path):
        _seed_data(tmp_path, with_intent=True)
        base = trace_view._build_timeline("pv", 3)
        f_kind = trace_view._apply_filters(base, kind="author_intent", actor=None, surface=None)
        f_actor = trace_view._apply_filters(base, kind=None, actor="author", surface=None)
        f_surf = trace_view._apply_filters(base, kind=None, actor=None, surface="bible_edit")
    # kind=author_intent → 2건(revise_propose + bible_edit)
    ok = (len(f_kind) == 2 and all(it["kind"] == "author_intent" for it in f_kind))
    # actor=author → author_intent(revise, author) + revision + regen = 3(bible_edit 는 actor=tool 제외)
    ok &= (len(f_actor) == 3 and all(it["actor"] == "author" for it in f_actor))
    # surface=bible_edit → 1건
    ok &= (len(f_surf) == 1 and f_surf[0]["surface"] == "bible_edit")
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ 필터 kind={len(f_kind)}·actor={len(f_actor)}·surface={len(f_surf)}")
    assert ok


# ═════════════════ ⑥ ts 정규화 순수 함수(혼재 포맷) ═════════════════

def test_ts_key_normalization(tmp_path=None):
    """⑥ _ts_key — tz 오프셋(±HHMM/±HH:MM/Z)만 벗기고 나머지는 그대로(산술 변환 0)."""
    k = trace_view._ts_key
    ok = (k("2026-07-17T14:03:22+0900") == "2026-07-17T14:03:22")
    ok &= (k("2026-07-17T14:03:22-05:00") == "2026-07-17T14:03:22")
    ok &= (k("2026-07-17T14:03:22Z") == "2026-07-17T14:03:22")
    ok &= (k("2026-07-17T14:03:22") == "2026-07-17T14:03:22")   # naive 불변
    ok &= (k("") == "" and k(None) == "")                       # 빈 값→맨 앞
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ _ts_key tz 벗김 정규화(혼재 포맷)")
    assert ok


def main() -> int:
    import tempfile
    tmp_fns = [
        test_route_200_and_404_and_ch0,
        test_route_readonly_no_mutation,
        test_cli_render_generate_failure_and_events,
        test_cli_render_author_intent_quote,
        test_join_and_mixed_ts_stable_sort,
        test_filters_kind_actor_surface,
    ]
    for fn in tmp_fns:
        with tempfile.TemporaryDirectory() as td:
            fn(pathlib.Path(td))
    test_ts_key_normalization()
    print("\nGA-2/FI-2(트레이스 디버그 뷰어) 검증: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
