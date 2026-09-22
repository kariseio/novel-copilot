# -*- coding: utf-8 -*-
"""FI-1 검증 — 작가 의도 이벤트 영속(기각된 퇴고 제안·마찰·캐논 정정 원문 자산화). 실 LLM 0콜·결정론.

설계 docs/design-fi1-author-intent.md §9 사전 등록 잠금 테스트 9종(1:1 대응):
  ① 표면별 1액션=1이벤트(revise 제안→미채택 시 revise_propose 만·accept 이벤트 없음).
  ② OFF(author_intent_trace=False) → 신규 트레이스 파일 0(사이드카 미생성).
  ③ 구 JSON(gen_no·이벤트 없음) 로드 → 재저장 라운드트립 안정(additive 하위호환).
  ④ gen_no 각인: 생성(1)→재생성(2)→재생성(3) — regen_events 와 정합(§4 공식 잠금).
  ⑤ 동시 emit(스레드 N개) lost-update 0 — run 수 보존(§5 트레이스 쓰기 락).
  ⑥ kind 별 상한: 생성 run 드롭이 intent run 을 축출하지 않음(역방향 동일)·드롭 카운터 정확.
  ⑦ ch=0 착지·approx_chapter 스냅샷(회차 밖 캐논 정정 이벤트).
  ⑧ 변경 코어 diff 함수: 결정론·트림 표식·한글 경계 안전.
  ⑨ contextvar 스레드풀 전파 실측(sync def 라우트 → actor="author" 도달) + 헤더 X-NC-Actor: tool.

실행: (app/ 에서) py -3.12 -X utf8 -m pytest tools/test_fi1_author_intent.py -q
"""
from __future__ import annotations
import sys
import pathlib
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot

from types import SimpleNamespace

import pytest

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectSeed, ProjectState, RegenEvent
from novelcopilot.domain.world import WorldConfig
from novelcopilot.domain.types import ChapterRecord, ChapterRevision, ChapterStatus
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot import telemetry
from novelcopilot.telemetry import revise_core_diff


# ───────────────────────── 모의 세션 스캐폴딩(LLM 0콜 — DE-1/RV-2 관행 재사용) ─────────────────────────
class _Vocab:
    categorical_keys: list = []
    numeric_keys: list = []

    def state_specs(self):
        return []


class _Checker:
    class extractor:
        vocab = _Vocab()

    def check_text(self, text, ont, chapter, ids):
        return SimpleNamespace(hard=[], claims=[])


class _Ont:
    entities: dict = {}

    def scan_present_ids(self, text):
        return []

    def name(self, eid):
        return eid


class _Gen:
    _last_revise_cause = "ok"

    def revise_prose(self, directive, before_text, span_text="", passes=None, ids=None,
                     ont=None, chapter_no=0, skills_inject=""):
        if directive == "__boom__":
            raise RuntimeError("revise 실패 모사")   # friction_error 유도
        return before_text.replace("어둠", "빛")      # 길이 유사 실질 변경(changed=True·가드 통과)

    def _summarize(self, text, prior="", beat=None):
        return ("요약", "상세", None)


class _Rag:
    def index_chapter(self, n, text):
        return 1


class _Bus:
    def emit(self, *a, **k):
        pass


class _Bundle:
    def __init__(self):
        self.ontology = _Ont()
        self.checker = _Checker()
        self.generator = _Gen()
        self.rag = _Rag()


class _Sess:
    def __init__(self):
        self.lock = threading.Lock()
        self.bundle = _Bundle()
        self.bus = _Bus()

    def snapshot_into(self, state):
        pass


class _Sessions:
    def __init__(self):
        self._s = {}

    def get_or_create(self, state):
        return self._s.setdefault(state.id, _Sess())

    def evict(self, pid):
        self._s.pop(pid, None)


BODY = "진우는 어둠 속을 걸었다. 어스름이 짙게 깔렸고 바람이 불었다."


def _svc(tmp, *, chapters=None, current_chapter=1, **settings_over):
    settings = get_settings().model_copy(update={"data_dir": str(tmp), **settings_over})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _Sessions()
    if chapters is None:
        chapters = [ChapterRecord(chapter=1, title="1화", status=ChapterStatus.FINALIZED, text=BODY)]
    state = ProjectState(id="p1", seed=ProjectSeed(title="t"),
                         world=WorldConfig(title="t", synopsis="s"),
                         current_chapter=current_chapter, chapters=chapters)
    svc.repo.save(state)
    return svc


def _intent_runs(svc, chapter):
    doc = svc.repo.load_trace("p1", chapter)
    if not doc:
        return []
    return [r for r in doc["runs"] if isinstance(r, dict) and r.get("kind") == "author_intent"]


# ═════════════════ ① 표면별 1액션=1이벤트(제안→미채택) ═════════════════
def test_propose_only_leaves_propose_no_accept(tmp_path):
    svc = _svc(tmp_path)
    res = svc.revise_chapter("p1", 1, "어둠을 빛으로 바꿔줘")
    assert res["changed"] is True and res["revision_id"]
    runs = _intent_runs(svc, 1)
    surfaces = [r["surface"] for r in runs]
    assert surfaces == ["revise_propose"]                       # 정확히 1건·accept 없음
    ev = runs[0]
    assert ev["ref"]["revision_id"] == res["revision_id"]
    assert ev["gen_no"] == 1 and ev["chapter"] == 1
    assert ev["payload"]["core"]["core_before"] == "어둠" and ev["payload"]["core"]["core_after"] == "빛"
    assert "latency_sec" in ev["payload"] and ev["actor"] == "tool"   # in-process 직접 호출=tool(기본값)


def test_accept_adds_accept_event(tmp_path):
    svc = _svc(tmp_path)
    res = svc.revise_chapter("p1", 1, "어둠을 빛으로")
    out = svc.accept_revision("p1", 1, res["revision_id"])
    assert out["accepted"] is True
    surfaces = [r["surface"] for r in _intent_runs(svc, 1)]
    assert surfaces == ["revise_propose", "revise_accept"]      # 제안+채택 각 1건
    accept_ev = _intent_runs(svc, 1)[1]
    assert accept_ev["ref"]["revision_id"] == res["revision_id"] and "payload" not in accept_ev  # 전문 미저장(SSOT 참조)


def test_undo_emits_revise_undo(tmp_path):
    svc = _svc(tmp_path)
    res = svc.revise_chapter("p1", 1, "어둠을 빛으로")
    svc.accept_revision("p1", 1, res["revision_id"])
    svc.undo_revision("p1", 1)
    surfaces = [r["surface"] for r in _intent_runs(svc, 1)]
    assert surfaces == ["revise_propose", "revise_accept", "revise_undo"]
    # reverted_at 각인(§4)
    rev = svc.repo.get("p1").chapter(1).revisions[-1]
    assert rev.reverted is True and rev.reverted_at != ""


def test_direct_edit_emits_direct_edit_not_accept(tmp_path):
    # edit_chapter → accept_revision 위임: edit- 접두 → surface=direct_edit(1액션=1이벤트, revise_accept 아님).
    svc = _svc(tmp_path)
    svc.edit_chapter("p1", 1, span_text="어스름이 짙게 깔렸고", replacement="어스름이 옅게 걷혔고")
    surfaces = [r["surface"] for r in _intent_runs(svc, 1)]
    assert surfaces == ["direct_edit"]                          # 단 1건·direct_edit
    assert _intent_runs(svc, 1)[0]["ref"]["revision_id"].startswith("edit-")


# ═════════════════ 마찰(friction) 표면 ═════════════════
def test_friction_empty_directive(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.revise_chapter("p1", 1, "   ")
    assert [r["surface"] for r in _intent_runs(svc, 1)] == ["friction_empty_directive"]


def test_friction_span_not_found(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.revise_chapter("p1", 1, "다듬어", span_text="본문에절대없는구간XYZ")
    runs = _intent_runs(svc, 1)
    assert [r["surface"] for r in runs] == ["friction_span_not_found"]
    assert runs[0]["payload"]["reason"] == "span_not_found" and runs[0]["gen_no"] == 1


def test_friction_locked(tmp_path):
    svc = _svc(tmp_path)
    sess = svc.sessions.get_or_create(svc.repo.get("p1"))
    sess.lock.acquire()   # 회차 생성 중 모사 → revise_chapter 의 non-blocking acquire 실패
    try:
        out = svc.revise_chapter("p1", 1, "어둠을 빛으로")
        assert out is None                                      # 423 계약
    finally:
        sess.lock.release()
    assert [r["surface"] for r in _intent_runs(svc, 1)] == ["friction_locked"]


def test_friction_guardrail(tmp_path):
    # accept 서버 가드레일 재검증 실패(분량 밴드 이탈) → friction_guardrail.
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.accept_revision("p1", 1, "", after_text_fb="x")   # ratio≈0.02 < 0.5 → length_ok False
    assert [r["surface"] for r in _intent_runs(svc, 1)] == ["friction_guardrail"]


def test_friction_error(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(RuntimeError):
        svc.revise_chapter("p1", 1, "__boom__")               # revise_prose 예외
    runs = _intent_runs(svc, 1)
    assert [r["surface"] for r in runs] == ["friction_error"]
    assert "RuntimeError" in runs[0]["payload"]["reason"]


# ═════════════════ ② OFF → 사이드카 미생성 ═════════════════
def test_off_no_sidecar(tmp_path):
    svc = _svc(tmp_path, author_intent_trace=False)
    res = svc.revise_chapter("p1", 1, "어둠을 빛으로")
    assert res["changed"] is True                              # 퇴고는 정상 동작(무강제 — emit 만 꺼짐)
    assert svc.repo.load_trace("p1", 1) is None                # 사이드카 미생성(신규 파일 0)
    # 캐논 정정도 OFF 면 무기록
    svc.add_directive("p1", "복선 회수")
    assert svc.repo.load_trace("p1", 0) is None


# ═════════════════ ③ 구 JSON 로드 additive 하위호환 ═════════════════
def test_old_json_additive_compat(tmp_path):
    # gen_no·reverted_at·author_intent 이벤트가 전혀 없던 구 회차/퇴고 JSON 을 모사 → 로드 시 default·라운드트립 안정.
    old_rev = {"revision_id": "r1", "directive": "구", "before_text": "a", "after_text": "b",
               "reverted": True}                              # reverted_at 없음(구 레코드)
    old_ch = {"chapter": 1, "title": "1화", "status": "FINALIZED", "text": "본문",
              "revisions": [old_rev]}                          # gen_no 없음(구 회차)
    rec = ChapterRecord.model_validate(old_ch)
    assert rec.gen_no == 1                                     # additive 기본 1(미상·소급 추정 안 함)
    assert rec.revisions[0].reverted_at == ""                 # additive 기본 ""
    # 라운드트립 안정(dump→load→dump 멱등 — additive 필드가 자기일관)
    import json as _j
    d1 = rec.model_dump_json()
    d2 = ChapterRecord.model_validate_json(d1).model_dump_json()
    assert d1 == d2


# ═════════════════ ④ gen_no 각인 공식(§4) ═════════════════
def test_gen_no_formula_matches_regen_events():
    """생성(1)→재생성(2)→재생성(3): regenerate_last_chapter 가 '생성 전' append 하는 RegenEvent.seq 규칙과,
    generate 의 각인 공식 record.gen_no = 1 + max(seq for e.chapter==N) 이 정확히 정합함을 잠근다(§4)."""
    N = 5
    events: list = []

    def imprint():   # copilot.generate_next_chapter 의 각인 라인과 동일 표현
        return 1 + max((e.seq for e in events if e.chapter == N), default=0)

    def regen():     # regenerate_last_chapter 의 append 라인과 동일 표현
        events.append(RegenEvent(chapter=N, seq=1 + sum(1 for e in events if e.chapter == N)))

    assert imprint() == 1          # 최초 생성
    regen(); assert imprint() == 2  # 1차 재생성
    regen(); assert imprint() == 3  # 2차 재생성
    # 다른 회차 이벤트는 간섭 없음
    events.append(RegenEvent(chapter=N + 1, seq=1))
    assert imprint() == 3


# ═════════════════ ⑤ 동시 emit lost-update 0(트레이스 쓰기 락) ═════════════════
def test_concurrent_save_trace_no_lost_update(tmp_path):
    repo = FilesystemProjectRepository(tmp_path)
    pid = "cc"
    N = 40
    errs = []

    def worker(i):
        try:
            repo.save_trace(pid, 1, {"kind": "author_intent", "seq": i}, kind="author_intent", max_runs=1000)
        except Exception as e:   # pragma: no cover
            errs.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    doc = repo.load_trace(pid, 1)
    assert not errs
    assert len(doc["runs"]) == N                               # 전건 보존(lost-update 0)
    assert sorted(r["seq"] for r in doc["runs"]) == list(range(N))


# ═════════════════ ⑥ kind 별 상한 분리 ═════════════════
def test_kind_group_caps_independent(tmp_path):
    repo = FilesystemProjectRepository(tmp_path)
    pid = "cap"
    # 같은 회차 샤드에 생성 run(상한 3) + 의도 이벤트(상한 200) 혼재.
    for i in range(3):
        repo.save_trace(pid, 1, {"kind": "author_intent", "seq": i}, kind="author_intent", max_runs=200)
    for i in range(5):
        repo.save_trace(pid, 1, {"kind": "generate", "seq": i}, max_runs=3)   # 생성 상한 3
    doc = repo.load_trace(pid, 1)
    gen = [r for r in doc["runs"] if r["kind"] == "generate"]
    intent = [r for r in doc["runs"] if r["kind"] == "author_intent"]
    assert len(gen) == 3 and doc["dropped_runs"] == 2          # 생성만 드롭
    assert len(intent) == 3 and doc["dropped_intents"] == 0    # 의도 이벤트 축출 안 됨(역방향)
    assert [r["seq"] for r in gen] == [2, 3, 4]                # 오래된 0,1 드롭
    # 역방향: 의도 이벤트 상한 초과가 생성 run 을 축출하지 않음
    for i in range(3, 6):
        repo.save_trace(pid, 1, {"kind": "author_intent", "seq": i}, kind="author_intent", max_runs=4)
    doc2 = repo.load_trace(pid, 1)
    gen2 = [r for r in doc2["runs"] if r["kind"] == "generate"]
    intent2 = [r for r in doc2["runs"] if r["kind"] == "author_intent"]
    assert len(gen2) == 3 and doc2["dropped_runs"] == 2        # 생성 불변
    assert len(intent2) == 4 and doc2["dropped_intents"] == 2  # 의도 이벤트 상한 4로 트림(6개 중 2 드롭)


# ═════════════════ ⑦ ch=0 착지 + approx_chapter 스냅샷 ═════════════════
def test_canon_events_land_ch0_with_approx(tmp_path):
    svc = _svc(tmp_path, current_chapter=7)
    svc.add_directive("p1", "떡밥을 8화에 회수")
    doc0 = svc.repo.load_trace("p1", 0)                        # 작품 스코프 샤드
    assert doc0 is not None
    ev = [r for r in doc0["runs"] if r.get("kind") == "author_intent"][0]
    assert ev["surface"] == "directive_add" and ev["chapter"] == 0
    assert ev["payload"]["approx_chapter"] == 7                # emit 시점 current_chapter 스냅샷
    assert "gen_no" not in ev                                   # 회차 무관 이벤트는 gen_no 생략
    # 회차 스코프 샤드(trace.1)엔 안 섞임
    assert svc.repo.load_trace("p1", 1) is None


def test_entity_state_before_after(tmp_path):
    # set_entity_state 의 before→after 요약 — 엔티티가 필요하므로 ont 에 하나 심는다.
    svc = _svc(tmp_path, current_chapter=3)
    sess = svc.sessions.get_or_create(svc.repo.get("p1"))
    sess.bundle.ontology.entities = {"jinwoo": SimpleNamespace(attrs={})}
    sess.bundle.vocab = SimpleNamespace(attr=lambda a: None, irreversible_states=lambda a: set())
    # ont.binding_state_as_of / set_state 최소 스텁
    sess.bundle.ontology.binding_state_as_of = lambda eid, attr, eff: "alive"
    sess.bundle.ontology.set_state = lambda *a, **k: None
    svc.set_entity_state("p1", "jinwoo", "status", "dead", eff_from=3)
    ev = [r for r in svc.repo.load_trace("p1", 0)["runs"] if r.get("kind") == "author_intent"][0]
    assert ev["surface"] == "entity_state"
    assert ev["payload"]["before"] == "alive" and ev["payload"]["after"] == "dead"
    assert ev["payload"]["approx_chapter"] == 3


# ═════════════════ ⑧ 변경 코어 diff 함수(결정론·트림·한글 경계) ═════════════════
def test_core_diff_deterministic_and_korean_safe():
    d = revise_core_diff("가나다라마바사", "가나ZZ바사")
    assert d["core_before"] == "다라마" and d["core_after"] == "ZZ"   # 공통 접두 '가나'·접미 '바사' 절단
    assert d["core_offset"] == 2 and d["before_len"] == 7 and d["after_len"] == 6
    assert d["ctx_before"] == "가나" and d["ctx_after"] == "바사"
    assert revise_core_diff("같다", "같다") == revise_core_diff("같다", "같다")   # 결정론(동일 입력=동일 출력)
    # 동일 텍스트 → 코어 빈 문자열(no_change 안전)
    same = revise_core_diff("동일", "동일")
    assert same["core_before"] == "" and same["core_after"] == ""


def test_core_diff_trim_flag():
    big_before = "가" * 9000       # 접두/접미 공통 없음 → 코어 전체(9000 > 8192 cap)
    big_after = "나" * 9000
    d = revise_core_diff(big_before, big_after)
    assert d.get("trimmed") is True
    assert len(d["core_before"]) == 8192 and len(d["core_after"]) == 8192   # 필드당 cap 절단
    # 트림 없으면 표식 없음(은폐 금지 — 있을 때만)
    assert "trimmed" not in revise_core_diff("짧다", "길다")


# ═════════════════ ⑨ contextvar 스레드풀 전파 실측(sync def 라우트 → actor) ═════════════════
def _build_app(tmp_path, monkeypatch):
    import novelcopilot.config as cfg
    from novelcopilot.main import create_app
    monkeypatch.setenv("NOVEL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "_settings", None)   # 싱글톤 재생성 강제(임시 data_dir 반영) — monkeypatch 가 종료 후 복원
    app = create_app()
    svc = app.state.service
    svc.sessions = _Sessions()                    # add_directive 는 sess.lock 만 씀 — provider 키 의존 제거
    state = ProjectState(id="pv", seed=ProjectSeed(title="t"),
                         world=WorldConfig(title="t", synopsis="s"), current_chapter=2)
    svc.repo.save(state)
    return app, svc


def test_actor_contextvar_propagates_author(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app, svc = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.post("/api/projects/pv/directives", json={"text": "복선 회수"})
    assert r.status_code == 200
    ev = [x for x in svc.repo.load_trace("pv", 0)["runs"] if x.get("kind") == "author_intent"][-1]
    assert ev["surface"] == "directive_add"
    assert ev["actor"] == "author", "미들웨어 actor='author' 가 sync 라우트 스레드풀로 전파 실패(예비안 §3 필요)"


def test_actor_header_tool_override(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app, svc = _build_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.post("/api/projects/pv/directives", json={"text": "실험 도구 지시"},
                    headers={"X-NC-Actor": "tool"})
    assert r.status_code == 200
    ev = [x for x in svc.repo.load_trace("pv", 0)["runs"] if x.get("kind") == "author_intent"][-1]
    assert ev["actor"] == "tool", "X-NC-Actor: tool 헤더가 author 로 오분류됨"


def test_inprocess_direct_call_is_tool(tmp_path):
    # 미들웨어를 안 타는 in-process 직접 호출은 기본값 tool(실험 스크립트 경로).
    svc = _svc(tmp_path, current_chapter=1)
    svc.add_directive("p1", "직접 호출")
    ev = [x for x in svc.repo.load_trace("p1", 0)["runs"] if x.get("kind") == "author_intent"][-1]
    assert ev["actor"] == "tool"


def main() -> int:
    return pytest.main([str(pathlib.Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    sys.exit(main())
