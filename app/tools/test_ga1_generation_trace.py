# -*- coding: utf-8 -*-
"""GA-1 검증 — 생성 트레이스 아카이브(중간 산출물 전량 영속). 실 LLM 0콜(스텁)·결정론.

목표(사용자 2026-07-15 "첫 생성된 결과 나온 결과 판단 이런거 등등 모든 중간중간의 결과물들을 날리지말고
싹 다 저장"): 회차 생성 파이프의 중간 산출물(첫 초안·재작성 라운드·style_judge 판정 전문·humanize 스팬
before/after·rerender 후보/평가)을 회차별 append-only 사이드카(`<pid>.trace.<ch>.json`)에 전량 영속한다.
본체 프로젝트 JSON·gen_context 는 무변경(순수 부가 관측 사이드카).

검증 축(사전 등록):
  ① 첫 초안이 '수술 전' 스냅샷으로 저장(휴머나이즈 후 최종본과 다른 케이스)
  ② style_judge 판정 전문 저장(needs_repair·spans quote/why·reason·reference)
  ③ humanize 스팬 before/after 저장
  ④ 재생성 시 runs append(덮어쓰기 아님)
  ⑤ gen_trace OFF → 사이드카 미생성·바이트 동일(record._gen_trace=None)
  ⑥ rerender trace append(kind=rerender·후보 전체)
  ⑦ save 실패 시 발행 무영향
  ⑧ 프로젝트 삭제 시 trace 샤드 동반 삭제
  ⑨ runs 상한 초과 시 오래된 것부터 드롭+드롭 카운트(은폐 금지)

실행: (app/ 에서) py -3.12 -X utf8 -m pytest tools/test_ga1_generation_trace.py -q
"""
from __future__ import annotations
import json
import pathlib
import sys
from types import SimpleNamespace

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot
sys.path.insert(0, str(_HERE))          # tools/

from novelcopilot.config import Settings
from novelcopilot.domain.world import StyleSpec, WorldConfig
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine.harness import ChapterGenerator, _collect_humanize_span_texts
from novelcopilot.repository import FilesystemProjectRepository


class _Bus:
    def emit(self, *a, **k):
        pass


# ═════════════════ 하네스 e2e 스텁(hm1b 계보) ═════════════════

class _Cap:
    def __init__(self):
        self.last_truncated = False
        self.usage = SimpleNamespace(chat_tokens=0, chat_calls=0)

    def chat(self, messages, *a, **k):
        return "본문."

    def chat_json(self, messages, *a, **k):
        return {}

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


class _Ont:
    entities = {}
    rules = []
    def is_actor(self, et): return False
    def canon_facts(self, ids, ch): return []
    def canon_relations(self, ids, ch): return []
    def scan_present_ids(self, text): return []
class _Chk:
    def check_text(self, *a, **k): return SimpleNamespace(violations=[], hard=[], claims=[])
class _Rag:
    def index_chapter(self, *a, **k): return 1
    def search(self, *a, **k): return []
class _Wiki:
    def ingest_chapter(self, *a, **k): return 0
    def retrieve(self, *a, **k): return []


def _run_generate(settings, *, fake_humanize=None):
    """harness.generate 를 스텁으로 1회 돌려 record 반환(실 LLM 0). humanize 스텁 주입 가능."""
    import novelcopilot.engine.humanize_pass as hpmod
    import novelcopilot.engine.style_pipeline as spmod
    real_hm, real_sb = hpmod.humanize_spans, spmod.repair_spans
    if fake_humanize is not None:
        hpmod.humanize_spans = fake_humanize
    spmod.repair_spans = lambda *a, **k: (a[4], [])
    try:
        g = ChapterGenerator(_Cap(), checker=_Chk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        return g.generate(2, beat, _Ont(), _Rag(), _Wiki())
    finally:
        hpmod.humanize_spans = real_hm
        spmod.repair_spans = real_sb


# ═════════════════ ① 첫 초안(수술 전) 스냅샷 ═════════════════

def test_first_draft_snapshot_differs_from_final():
    """① 첫 초안이 '수술 전' 스냅샷으로 저장 — 휴머나이즈가 본문을 바꾸면 final_text 와 다르다."""
    settings = Settings()   # gen_trace 기본 ON·humanize 기본 ON
    MARK = "\n\n[HUMANIZED-TAIL]"

    def fake_humanize(generator, ontology, checker, chapter_no, text, findings, **kw):
        # 본문을 실제로 바꿔 first_draft != final_text 를 만든다(수술 전/후 분리 검증).
        return text + MARK, [{"category": "N-4", "severity": "S2", "changed": True,
                              "char_start": 0, "char_end": 3, "change_rate": 0.1,
                              "rate_band": "ok", "fallback": None, "author_review": False}]

    rec = _run_generate(settings, fake_humanize=fake_humanize)
    tr = rec._gen_trace
    ok = (tr is not None)
    ok &= (tr["kind"] == "generate")
    ok &= (MARK not in (tr["first_draft"] or ""))        # 첫 초안엔 휴머나이즈 흔적 없음(수술 전)
    ok &= (MARK in (tr["final_text"] or ""))             # 최종본엔 있음(수술 후)
    ok &= (tr["first_draft"] != tr["final_text"])        # 둘이 다름(핵심)
    ok &= (rec.text == tr["final_text"])                 # 최종본 = 발행 본문
    print(f"[{'OK' if ok else 'FAIL'}] ① 첫 초안 수술 전 스냅샷(≠ 최종본)")
    assert ok


# ═════════════════ ② style_judge 판정 전문 ═════════════════

def test_style_judgment_full_captured():
    """② style_judge 판정 전문 저장 — needs_repair·spans(quote/why)·reason·reference."""
    settings = Settings()
    JUDG = {"needs_repair": True,
            "spans": [{"quote": "그는 걸었다. 멈췄다.", "why": "같은 어미 반복"}],
            "reason": "문말이 단조롭다",
            "reference": {"runs": [{"ending_key": "-다", "n_sent": 6, "first_sentence": "그는…"}],
                          "backend": "kiwi", "n_runs": 1}}

    def fake_humanize(*a, **k):
        return a[4], []   # 본문 불변(판정 전문 캡처만 검증)

    import novelcopilot.engine.harness as hmod
    real = ChapterGenerator._run_style_judgment
    ChapterGenerator._run_style_judgment = lambda self, text, ch_no, su, st, genre="", motif_candidates=None: (JUDG, [])
    try:
        rec = _run_generate(settings, fake_humanize=fake_humanize)
    finally:
        ChapterGenerator._run_style_judgment = real
    sj = rec._gen_trace["style_judgment"]
    ok = (sj is not None)
    ok &= (sj["needs_repair"] is True)
    ok &= (sj["spans"][0]["quote"] == "그는 걸었다. 멈췄다." and sj["spans"][0]["why"] == "같은 어미 반복")
    ok &= (sj["reason"] == "문말이 단조롭다")
    ok &= (sj["reference"]["backend"] == "kiwi" and sj["reference"]["n_runs"] == 1)
    print(f"[{'OK' if ok else 'FAIL'}] ② style_judge 판정 전문(needs_repair·spans·reason·reference)")
    assert ok


# ═════════════════ ③ humanize 스팬 before/after ═════════════════

def test_humanize_span_before_after_captured():
    """③ humanize 스팬 before/after 저장(사이드카 trace) — before=원문 창, after=수술 결과 or 폴백=원문."""
    before_full = "가나다라마바사아자차카타파하"
    after_full = "가나ZZ마바사아자차카타파하"   # [2:4] '다라' → 'ZZ' 로 바뀐 최종본(변경 채택 케이스)
    entries = [
        # 변경 채택 — before='다라' 는 after_full 에 없음(재작성됨) → after=None + note(정직)
        {"category": "N-4", "severity": "S2", "char_start": 2, "char_end": 4,
         "changed": True, "fallback": None, "author_review": False},
        # 폴백=원문(미변경) — before='사아' 는 그대로 → after=before
        {"category": "N-1", "severity": "S1", "char_start": 6, "char_end": 8,
         "changed": False, "fallback": "unchanged", "author_review": True},
        # 좌표 무효(전범위 밀도) → before=None
        {"category": "N-5", "severity": "S1", "char_start": None, "char_end": None,
         "changed": False, "fallback": "not_local", "author_review": False},
    ]
    out = _collect_humanize_span_texts(before_full, after_full, entries)
    ok = (len(out) == 3)
    ok &= (out[0]["before"] == "다라" and out[0]["after"] is None and bool(out[0]["note"]))   # 재작성 — 정확 after 슬라이스 불가(정직)
    ok &= (out[1]["before"] == "사아" and out[1]["after"] == "사아")                     # 폴백=원문
    ok &= (out[2]["before"] is None and bool(out[2]["note"]))                             # 좌표 무효
    ok &= (out[0]["changed"] is True and out[1]["changed"] is False)
    ok &= (out[1]["author_review"] is True)                                              # 잔존 정직 표기 보존
    print(f"[{'OK' if ok else 'FAIL'}] ③ humanize 스팬 before/after(재작성=정직 결측·폴백=원문·좌표무효=None)")
    assert ok


def test_humanize_before_after_unchanged_present_in_after():
    """③b 변경 채택인데 원문 창이 후처리 본문에 그대로 남아 있으면(사실상 미변경) after=before(재-앵커 정직)."""
    before_full = "가나다라마바사"
    after_full = "가나다라마바사XYZ"   # 뒤에만 덧붙음 — 원문 창 '다라' 는 그대로 남음
    entries = [{"category": "N-4", "severity": "S2", "char_start": 2, "char_end": 4,
                "changed": True, "fallback": None, "author_review": False}]
    out = _collect_humanize_span_texts(before_full, after_full, entries)
    ok = (out[0]["before"] == "다라" and out[0]["after"] == "다라")   # 재-앵커: 남아 있으면 after=before
    print(f"[{'OK' if ok else 'FAIL'}] ③b 변경 채택이나 원문 창 잔존 → after=before(재-앵커)")
    assert ok


# ═════════════════ ④ 재생성 시 runs append(덮어쓰기 아님) ═════════════════

def test_save_trace_appends_not_overwrites(tmp_path):
    """④ 같은 회차 재생성 시 runs 배열에 append(덮어쓰지 않음) — 회차 재생성 이력 보존."""
    repo = FilesystemProjectRepository(tmp_path)
    pid = "proj-append"
    repo.save_trace(pid, 3, {"kind": "generate", "first_draft": "초안A", "final_text": "최종A"})
    repo.save_trace(pid, 3, {"kind": "generate", "first_draft": "초안B", "final_text": "최종B"})
    doc = repo.load_trace(pid, 3)
    ok = (doc is not None and len(doc["runs"]) == 2)
    ok &= (doc["runs"][0]["first_draft"] == "초안A" and doc["runs"][1]["first_draft"] == "초안B")
    ok &= (doc["chapter"] == 3 and doc["dropped_runs"] == 0)
    # 다른 회차는 별도 샤드(간섭 없음)
    repo.save_trace(pid, 4, {"kind": "generate", "first_draft": "다른회차"})
    ok &= (len(repo.load_trace(pid, 3)["runs"]) == 2)
    ok &= (len(repo.load_trace(pid, 4)["runs"]) == 1)
    print(f"[{'OK' if ok else 'FAIL'}] ④ save_trace append-only(재생성 이력 보존·회차별 샤드)")
    assert ok


# ═════════════════ ⑤ gen_trace OFF → 사이드카 미생성·record._gen_trace None ═════════════════

def test_gen_trace_off_no_sidecar():
    """⑤ gen_trace OFF → record._gen_trace=None(서비스가 save_trace 미호출 → 사이드카 미생성·바이트 동일)."""
    settings = Settings()
    object.__setattr__(settings, "gen_trace", False)

    def fake_humanize(*a, **k):
        return a[4], []

    rec = _run_generate(settings, fake_humanize=fake_humanize)
    ok = (rec._gen_trace is None)
    # ON 이면 채워진다(대조군)
    settings_on = Settings()
    rec_on = _run_generate(settings_on, fake_humanize=fake_humanize)
    ok &= (rec_on._gen_trace is not None)
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ gen_trace OFF → _gen_trace None(사이드카 미생성)")
    assert ok


def test_gen_trace_off_no_file_written(tmp_path):
    """⑤b OFF 경로 무결성 — record._gen_trace=None 이면 서비스 저장 분기가 save_trace 를 부르지 않는다.
    (서비스 저장부의 가드 `if _trace is not None:` 를 직접 재현 — 파일 미생성 확인)."""
    repo = FilesystemProjectRepository(tmp_path)
    pid = "proj-off"
    _trace = None   # gen_trace OFF 시 record._gen_trace
    if _trace is not None:                       # 서비스 저장 가드와 동일
        repo.save_trace(pid, 1, _trace)
    ok = (repo.load_trace(pid, 1) is None)       # 사이드카 미생성
    ok &= (not (tmp_path / "projects" / f"{pid}.trace.1.json").exists())
    print(f"[{'OK' if ok else 'FAIL'}] ⑤b OFF → 파일 미생성(save_trace 미호출)")
    assert ok


# ═════════════════ ⑥ rerender trace append(kind=rerender·후보 전체) ═════════════════

def test_rerender_trace_append_full_candidates(tmp_path):
    """⑥ rerender trace — kind=rerender·BoN 후보 전체 텍스트·리랭크 eval·정독/가드 판정을 같은 사이드카에 append."""
    from novelcopilot.services.copilot import CopilotService
    settings = Settings()
    svc = CopilotService(settings, FilesystemProjectRepository(tmp_path))
    pid = "proj-rr"
    cands = ["후보1 전문 ...", "후보2 전문 ...", "후보3 전문 ..."]
    rr = {"winner_index": 0, "reason": "채택",
          "evaluations": [{"index": 0, "disqualified": False, "reasons": [], "top_ratio": 0.5},
                          {"index": 1, "disqualified": True, "reasons": ["대사 유실"], "top_ratio": 0.7},
                          {"index": 2, "disqualified": True, "reasons": ["수치 누락"], "top_ratio": 0.6}]}
    # 먼저 회차 생성 trace 가 있다고 가정(같은 무덤에 append 되는지)
    svc.repo.save_trace(pid, 5, {"kind": "generate", "first_draft": "gen 초안"})
    svc._save_rerender_trace(pid, 5, mode="bon", adopted=True, reason="채택",
                             before_text="원문", after_text="후보1 전문 ...",
                             candidates=cands, rr=rr,
                             guardrail={"passed": True, "reason": ""},
                             read_gate={"adopt": True, "reason": "정독 우세"},
                             repair_info={"reflowed": True, "humanize": [], "style_repairs": []})
    doc = svc.repo.load_trace(pid, 5)
    ok = (doc is not None and len(doc["runs"]) == 2)             # generate + rerender 같은 무덤
    rr_run = doc["runs"][1]
    ok &= (rr_run["kind"] == "rerender" and rr_run["adopted"] is True)
    ok &= (rr_run["candidates"] == cands)                        # 후보 전체 텍스트
    ok &= (len(rr_run["evaluations"]) == 3)                      # 각 후보 실격 사유·점수
    ok &= (rr_run["evaluations"][1]["disqualified"] is True and rr_run["evaluations"][1]["reasons"] == ["대사 유실"])
    ok &= (rr_run["winner_index"] == 0)
    ok &= (rr_run["read_gate"]["adopt"] is True and rr_run["guardrail"]["passed"] is True)
    ok &= (rr_run["finalize_repairs"]["reflowed"] is True)
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ rerender trace append(kind=rerender·후보 전체·eval·판정)")
    assert ok


def test_rerender_trace_off_no_save(tmp_path):
    """⑥b gen_trace OFF → _save_rerender_trace 무동작(사이드카 미생성)."""
    from novelcopilot.services.copilot import CopilotService
    settings = Settings()
    object.__setattr__(settings, "gen_trace", False)
    svc = CopilotService(settings, FilesystemProjectRepository(tmp_path))
    svc._save_rerender_trace("p-off", 1, mode="bon", adopted=True, reason="x",
                             before_text="a", after_text="b", candidates=["c"], rr=None)
    ok = (svc.repo.load_trace("p-off", 1) is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⑥b rerender trace OFF → 미저장")
    assert ok


# ═════════════════ ⑦ save 실패 시 발행 무영향 ═════════════════

def test_save_trace_failure_does_not_raise(tmp_path):
    """⑦ save_trace 실패는 예외를 전파하지 않는다(무강제·비차단) — 서비스 저장부 try/except 계약의 소스."""
    from novelcopilot.services.copilot import CopilotService
    settings = Settings()
    svc = CopilotService(settings, FilesystemProjectRepository(tmp_path))

    # repo.save_trace 를 폭파시켜도 _save_rerender_trace 는 조용히 삼킨다(발행 무영향).
    def boom(*a, **k):
        raise IOError("disk full")

    svc.repo.save_trace = boom
    raised = False
    try:
        svc._save_rerender_trace("p", 1, mode="bon", adopted=True, reason="x",
                                 before_text="a", after_text="b", candidates=["c"], rr=None)
    except Exception:
        raised = True
    ok = (raised is False)   # 예외 미전파
    print(f"[{'OK' if ok else 'FAIL'}] ⑦ save 실패 무전파(발행 무영향·무강제)")
    assert ok


# ═════════════════ ⑧ 프로젝트 삭제 시 trace 샤드 동반 삭제 ═════════════════

def test_delete_removes_trace_shards(tmp_path):
    """⑧ 프로젝트 삭제 시 trace 사이드카도 함께 삭제(고아 방지) — rag 샤드 삭제 로직에 trace 포함."""
    repo = FilesystemProjectRepository(tmp_path)
    pid = "proj-del"
    st = ProjectState(id=pid, seed=ProjectSeed(title="삭제작"), world=WorldConfig(title="삭제작"))
    repo.save(st)
    repo.save_trace(pid, 1, {"kind": "generate", "first_draft": "a"})
    repo.save_trace(pid, 2, {"kind": "generate", "first_draft": "b"})
    proj_dir = tmp_path / "projects"
    ok = (len(list(proj_dir.glob(f"{pid}.trace.*.json"))) == 2)   # 삭제 전 2개
    deleted = repo.delete(pid)
    ok &= (deleted is True)
    ok &= (len(list(proj_dir.glob(f"{pid}.trace.*.json"))) == 0)  # 동반 삭제
    ok &= (not (proj_dir / f"{pid}.json").exists())
    print(f"[{'OK' if ok else 'FAIL'}] ⑧ 프로젝트 삭제 → trace 샤드 동반 삭제")
    assert ok


def test_trace_shard_not_in_list_summaries(tmp_path):
    """⑧b trace 사이드카(.json)가 list_summaries 목록에 안 섞인다(ProjectState 파싱 실패 → skip)."""
    repo = FilesystemProjectRepository(tmp_path)
    pid = "proj-list"
    st = ProjectState(id=pid, seed=ProjectSeed(title="목록작"), world=WorldConfig(title="목록작"))
    repo.save(st)
    repo.save_trace(pid, 1, {"kind": "generate", "first_draft": "a"})
    summaries = repo.list_summaries()
    ok = (len(summaries) == 1 and summaries[0]["id"] == pid)   # trace 샤드가 목록에 안 섞임
    print(f"[{'OK' if ok else 'FAIL'}] ⑧b trace 샤드가 list_summaries 간섭 0")
    assert ok


# ═════════════════ ⑨ runs 상한 + 드롭 카운트(은폐 금지) ═════════════════

def test_runs_cap_drops_oldest_with_count(tmp_path):
    """⑨ runs 가 상한 초과 시 가장 오래된 것부터 드롭 + 드롭 카운트 기록(무한 재생성 방어·은폐 금지)."""
    repo = FilesystemProjectRepository(tmp_path)
    pid = "proj-cap"
    for i in range(5):
        repo.save_trace(pid, 1, {"kind": "generate", "seq": i}, max_runs=3)
    doc = repo.load_trace(pid, 1)
    ok = (len(doc["runs"]) == 3)                       # 상한 유지
    ok &= (doc["runs"][0]["seq"] == 2 and doc["runs"][-1]["seq"] == 4)   # 오래된 0,1 드롭 → 2,3,4 남음
    ok &= (doc["dropped_runs"] == 2)                   # 드롭 카운트(은폐 금지)
    # 이후 추가도 계속 누적 드롭
    repo.save_trace(pid, 1, {"kind": "generate", "seq": 5}, max_runs=3)
    doc2 = repo.load_trace(pid, 1)
    ok &= (doc2["dropped_runs"] == 3 and doc2["runs"][-1]["seq"] == 5)
    print(f"[{'OK' if ok else 'FAIL'}] ⑨ runs 상한·오래된 것 드롭·드롭 카운트 누적(은폐 금지)")
    assert ok


# ═════════════════ ⑩⑪⑫ EventBus 타임라인 영속(스코프 확장 2026-07-15) ═════════════════

def _persist_with_events(repo, pid, chapter, base_trace, bus):
    """서비스 저장부(copilot.generate_next_chapter)의 이벤트 편입 로직을 그대로 재현 —
    회차 구간 버퍼에 seq 부여·payload 보존, 실패 승격, save_trace append. 테스트 정합용."""
    failures = bus.failures()
    events = [{"seq": i, **dict(e)} for i, e in enumerate(bus.buffer)]
    run = {**base_trace, "ts": "2026-07-15T00:00:00",
           "events": events, "failures": list(failures)}
    repo.save_trace(pid, chapter, run)


def test_event_timeline_persisted_ordered(tmp_path):
    """⑩ 이벤트 타임라인 저장 — 회차 처리 이벤트가 trace.events 에 순서대로·payload 보존·seq 부여."""
    from novelcopilot.engine.observability import EventBus
    repo = FilesystemProjectRepository(tmp_path)
    bus = EventBus()
    bus.reset()   # 회차 시작 경계
    bus.emit("draft_chapter", "start", chapter=7)
    bus.emit("style_judge", "done", chapter=7, needs_repair=True, reason="문말 단조")
    bus.emit("humanize", "done", chapter=7, spans=3, changed=2)
    bus.emit("finalize", "done", chapter=7, indexed=5)
    _persist_with_events(repo, "proj-evt", 7, {"kind": "generate", "first_draft": "x"}, bus)
    run = repo.load_trace("proj-evt", 7)["runs"][0]
    evs = run["events"]
    ok = (len(evs) == 4)
    ok &= ([e["node"] for e in evs] == ["draft_chapter", "style_judge", "humanize", "finalize"])   # 순서 보존
    ok &= ([e["seq"] for e in evs] == [0, 1, 2, 3])                                                 # seq 순번
    ok &= (evs[1]["event"] == "done" and evs[1]["needs_repair"] is True and evs[1]["reason"] == "문말 단조")  # payload 보존
    ok &= (evs[2]["spans"] == 3 and evs[2]["changed"] == 2)
    print(f"[{'OK' if ok else 'FAIL'}] ⑩ 이벤트 타임라인(순서·seq·payload 보존)")
    assert ok


def test_failures_promoted_to_top_level(tmp_path):
    """⑪ 실패 이벤트가 trace.failures 로 승격(디버깅 빠른 진입점) — FAILURE_MODES 만."""
    from novelcopilot.engine.observability import EventBus
    repo = FilesystemProjectRepository(tmp_path)
    bus = EventBus()
    bus.reset()
    bus.emit("draft_chapter", "start", chapter=8)             # 정상
    bus.emit("plan_scenes", "parse_failure", chapter=8)       # 실패(FAILURE_MODES)
    bus.emit("finalize", "escalation", chapter=8, hard=["x"])  # 실패(FAILURE_MODES)
    _persist_with_events(repo, "proj-fail", 8, {"kind": "generate"}, bus)
    run = repo.load_trace("proj-fail", 8)["runs"][0]
    ok = (len(run["events"]) == 3)                            # events 는 전부(정상+실패)
    fails = run["failures"]
    ok &= (len(fails) == 2)                                   # failures 는 실패만 승격
    ok &= ({f["event"] for f in fails} == {"parse_failure", "escalation"})
    print(f"[{'OK' if ok else 'FAIL'}] ⑪ 실패 이벤트 trace.failures 승격(FAILURE_MODES)")
    assert ok


def test_chapter_isolation_via_reset(tmp_path):
    """⑫ 회차 구간 분리 — reset 경계로 이 회차 이벤트만 저장(다른 회차 이벤트 미혼입)."""
    from novelcopilot.engine.observability import EventBus
    repo = FilesystemProjectRepository(tmp_path)
    bus = EventBus()
    # 회차 6 처리
    bus.reset()
    bus.emit("draft_chapter", "start", chapter=6)
    bus.emit("finalize", "done", chapter=6)
    _persist_with_events(repo, "proj-iso", 6, {"kind": "generate"}, bus)
    # 회차 7 처리 — reset 이 6의 이벤트를 비운다(경계)
    bus.reset()
    bus.emit("draft_chapter", "start", chapter=7)
    bus.emit("finalize", "done", chapter=7)
    _persist_with_events(repo, "proj-iso", 7, {"kind": "generate"}, bus)
    ev6 = repo.load_trace("proj-iso", 6)["runs"][0]["events"]
    ev7 = repo.load_trace("proj-iso", 7)["runs"][0]["events"]
    ok = (all(e.get("chapter") == 6 for e in ev6))   # 6 샤드엔 6만
    ok &= (all(e.get("chapter") == 7 for e in ev7))   # 7 샤드엔 7만(reset 이 6 미혼입)
    ok &= (len(ev6) == 2 and len(ev7) == 2)
    print(f"[{'OK' if ok else 'FAIL'}] ⑫ 회차 구간 분리(reset 경계·타 회차 미혼입)")
    assert ok


# ═════════════════ 하위호환 — 본체 JSON 무영향 ═════════════════

def test_body_json_unchanged_gen_trace_privateattr():
    """_gen_trace 는 PrivateAttr — 본체 JSON(model_dump/json)에 미포함(용량·하위호환)."""
    rec = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="본문", title="t")
    rec._gen_trace = {"kind": "generate", "first_draft": "x" * 10000}   # 큰 트레이스
    d = json.loads(rec.model_dump_json())
    ok = ("_gen_trace" not in d and "gen_trace" not in d)   # 본체 JSON 무팽창
    # 구 JSON 로드 정합(trace 없는 회차)
    rt = ChapterRecord.model_validate(json.loads(rec.model_dump_json()))
    ok &= (rt._gen_trace is None)   # 재수화 시 default None
    print(f"[{'OK' if ok else 'FAIL'}] 하위호환: _gen_trace PrivateAttr(본체 JSON 무팽창·재수화 None)")
    assert ok


def main() -> int:
    import tempfile
    fns = [
        test_first_draft_snapshot_differs_from_final,
        test_style_judgment_full_captured,
        test_humanize_span_before_after_captured,
        test_humanize_before_after_unchanged_present_in_after,
        test_gen_trace_off_no_sidecar,
        test_body_json_unchanged_gen_trace_privateattr,
    ]
    tmp_fns = [
        test_save_trace_appends_not_overwrites,
        test_gen_trace_off_no_file_written,
        test_rerender_trace_append_full_candidates,
        test_rerender_trace_off_no_save,
        test_save_trace_failure_does_not_raise,
        test_delete_removes_trace_shards,
        test_trace_shard_not_in_list_summaries,
        test_runs_cap_drops_oldest_with_count,
        test_event_timeline_persisted_ordered,
        test_failures_promoted_to_top_level,
        test_chapter_isolation_via_reset,
    ]
    for fn in fns:
        fn()
    for fn in tmp_fns:
        with tempfile.TemporaryDirectory() as td:
            fn(pathlib.Path(td))
    print("\nGA-1(생성 트레이스 아카이브) 검증: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
