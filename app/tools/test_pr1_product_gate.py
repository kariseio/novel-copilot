# -*- coding: utf-8 -*-
"""PR-1 제품 경로 게이트 옵션 — 결정론 테스트(전부 모의·LLM 0콜).

설계(design-pr1-pr2-product-gate.md §PR-1):
  ① 게이트 코어를 engine.chapter_gate 로 승격 이동(tools 무의존) — gen_gated 는 그 부품을 import(이중 구현 소멸).
  ② generate_next_chapter FINALIZED 확정 직후 훅: config product_gate(기본 OFF — OFF 시 기존 경로 바이트 동일).
     ON이면 게이트 루프 실행, 판정·라운드를 verification.gate 로 영속, 작가 UI(app.js)에 게이트 결과·재시도 이력 노출.
  ③ 웹 regen(regenerate_last_chapter) fix directive 에 게이트 drop_trigger 인용·국소 앵커 우선(게이트 결과 있을 때만).

이 테스트:
  ⓐ 엔진 이관 — chapter_gate 가 tools 무의존이고 gen_gated 가 그 부품을 재노출(심볼 표면 불변)
  ⓑ 엔진 게이트 코어 == 러너(gen_gated 래퍼) 동치 — measure/rhythm 주입 계약(엔진 부품 기준 DP-21 산술)
  ⓒ OFF 바이트 동일 — product_gate=False 면 generate_next_chapter 반환·record.verification.gate=MISSING(기존 경로 불변)
  ⓓ ON 영속 — product_gate=True 면 run 후 record.verification.gate 채움(compact: verdict/재시도/라운드)
  ⓔ run_product_gate 국소 경로 — 모의 judge(FAIL→PASS)로 서비스 어댑터 revise/accept 위임·gate_rounds 산출(LLM 0콜)
  ⓕ 재진입 가드 — 게이트 안 regen 재진입 시 중첩 게이트 금지(_product_gate_active)
  ⓖ regen fix 증강 — verification.gate 있으면 drop_trigger 인용·앵커 우선 지시, 없으면 원본 바이트 동일
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 -m pytest tools/test_pr1_product_gate.py -q
"""
from __future__ import annotations
import sys
import pathlib
import tempfile
import threading
from pathlib import Path

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

import pytest

from novelcopilot.engine import chapter_gate as cg
from novelcopilot.engine import product_gate as pg
from novelcopilot.engine.verification import MISSING
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.base import LLMProvider
import novelcopilot.worldgen.arc_planner as apmod
import tools.gen_gated as g


# ─────────────────────────────────────────────────────────────────────────────
# ⓐ 엔진 이관 — tools 무의존 + 재노출 표면 불변
# ─────────────────────────────────────────────────────────────────────────────
def test_engine_chapter_gate_no_tools_import():
    import novelcopilot.engine.chapter_gate as m
    src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
    # 엔진 부품은 tools 를 import 하지 않는다(레이어 규율) — 'import tools'/'from tools' 부재
    assert "import tools" not in src
    assert "from tools" not in src
    # tools 바운드 계측/수리는 여기 없다(러너가 주입) — 정의 부재 확인
    assert "def measure_chapter" not in src and "def rhythm_spans" not in src


def test_gen_gated_reexports_engine_symbols():
    # gen_gated 가 엔진 부품을 재노출 → g.<name> 표면 불변(기존 테스트 계약 유지). 실제 engine 객체와 동일 식별.
    assert g.CallBudget is cg.CallBudget
    assert g.LLMBudgetExceeded is cg.LLMBudgetExceeded
    assert g.GATE_SYSTEM is cg.GATE_SYSTEM
    assert g.anchor_drop_trigger is cg.anchor_drop_trigger
    assert g.make_judge is cg.make_judge
    assert g._measure_digest is cg._measure_digest
    # tools 바운드는 gen_gated 에 남는다(엔진 아님)
    assert hasattr(g, "measure_chapter") and hasattr(g, "rhythm_spans")


# ─────────────────────────────────────────────────────────────────────────────
# ⓑ 엔진 게이트 코어 동치 — measure/rhythm 주입 계약(러너 래퍼와 동일 산술)
# ─────────────────────────────────────────────────────────────────────────────
def _judge_seq(verdicts, *, retentions=None, drop="합성 본문"):
    seq = list(verdicts); rets = list(retentions or []); st = {"i": 0}

    def judge(text, meas):
        i = st["i"]; v = seq[min(i, len(seq) - 1)]
        r = rets[min(i, len(rets) - 1)] if rets else 70
        st["i"] += 1
        return {"verdict": v, "drop_trigger": (drop if v == "FAIL" else "없음"),
                "fix_note": ("압축하라" if v == "FAIL" else ""), "retention_est": r, "hate_comment": ""}
    return judge


def _committed_harness():
    h = g.GatedDryRunHarness(g._tagged_seed(ProjectSeed(title="엔진게이트", genre="현판", target_chapters=12)))
    pid, _ = h.create(); h.step(pid)
    return h, pid


def test_engine_core_measure_rhythm_injection():
    # 엔진 gate_committed_chapter 를 직접(주입해) 호출 → 러너 래퍼와 동일 결과(초회 FAIL→국소 revise→PASS).
    h, pid = _committed_harness()
    calls = {"measure": 0, "rhythm": 0}

    def measure_fn(state, ch):
        calls["measure"] += 1
        return {}    # 계측 없음(digest '계측 없음') — 판정은 모의 judge

    def rhythm_fn(text):
        calls["rhythm"] += 1
        return []    # 리듬 스팬 없음

    rec = cg.gate_committed_chapter(
        harness=h, pid=pid, chapter=1, status="FINALIZED",
        judge_fn=_judge_seq(["FAIL", "PASS"], retentions=[50, 80]),
        fix_fn=None, budget=cg.CallBudget(100),
        measure_fn=measure_fn, rhythm_fn=rhythm_fn, max_retries=2)
    assert rec["verdict"] == "PASS" and rec["retries"] == 1
    assert rec["gate_rounds"][1]["repair_path"] == "revise"   # 앵커('합성 본문')됨 → 국소 경로
    assert calls["measure"] >= 2 and calls["rhythm"] >= 2      # 주입 함수가 실제로 호출됨(초회+재심사)


def test_engine_core_missing_injection_safe_degrade():
    # measure_fn/rhythm_fn 미주입이면 계측=빈dict·수리=스팬0으로 안전 강등(예외 없이 심사 진행).
    h, pid = _committed_harness()
    rec = cg.gate_committed_chapter(
        harness=h, pid=pid, chapter=1, status="FINALIZED",
        judge_fn=_judge_seq(["PASS"]), fix_fn=None, budget=cg.CallBudget(10))
    assert rec["verdict"] == "PASS" and rec["gate_rounds"][0]["round"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 서비스 통합 픽스처(PR-2 wiring 패턴 재사용) — LLM 0콜(Fake provider·stub generator/beat)
# ─────────────────────────────────────────────────────────────────────────────
class _Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _spine():
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1",
                    target_chapters=3, event_menu=["사건1"])])])


def _mk_svc(pid: str, *, product_gate=False):
    tmp = tempfile.mkdtemp()
    s = get_settings().model_copy(update={"data_dir": tmp, "product_gate": product_gate})
    svc = CopilotService(s, FilesystemProjectRepository(Path(tmp)))
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = _spine()
    st = ProjectState(id=pid, seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    svc.repo.save(st)
    sess, _ = svc.get_session(pid)
    sess.provider = _Fake()
    return svc, sess


def _stub_beat():
    orig = apmod.ArcPlanner.beat_for_episode
    apmod.ArcPlanner.beat_for_episode = lambda self, world, arc, ep, ch, fin, rec, direc, plant_notes="", **kw: \
        Beat(chapter=ch, entities=["hero"], arc_id=ep.arc_id, episode_id=ep.episode_id,
             closing_device="dialogue", hook_type="question", chapter_function="setup")
    return orig


def _gen_one(svc, sess, pid, *, text="본문 문장. " * 80):
    sess.bundle.updater.propose = lambda *a, **k: {}
    sess.bundle.updater.apply = lambda *a, **k: ([], [], [], [])
    orig = _stub_beat()
    try:
        sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
            ChapterRecord(chapter=ch_no, status=ChapterStatus.FINALIZED, text=text,
                          summary="s", closing_device="dialogue", hook_type="question",
                          chapter_function="setup")
        return svc.generate_next_chapter(pid)
    finally:
        apmod.ArcPlanner.beat_for_episode = orig


# ─────────────────────────────────────────────────────────────────────────────
# ⓒ OFF 바이트 동일 — product_gate=False 면 gate 축이 MISSING(기존 경로 불변)
# ─────────────────────────────────────────────────────────────────────────────
def test_off_path_gate_missing_and_no_gate_call(monkeypatch):
    # OFF 면 run_product_gate 가 절대 호출되지 않고 verification.gate == '미실행'(MISSING).
    called = {"n": 0}
    monkeypatch.setattr(pg, "run_product_gate",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    svc, sess = _mk_svc("off1", product_gate=False)
    res = _gen_one(svc, sess, "off1")
    v = res["record"].verification
    assert v["gate"] == MISSING            # 게이트 미실행(결측 정직)
    assert called["n"] == 0                # 게이트 코어 미호출(OFF 경로 무접촉)
    # 디스크 영속도 MISSING
    st = svc.get_project("off1")
    assert st.chapters[-1].verification["gate"] == MISSING


# ─────────────────────────────────────────────────────────────────────────────
# ⓓ ON 영속 — product_gate=True 면 게이트 결과가 verification.gate 로 영속(모의 judge — LLM 0콜)
# ─────────────────────────────────────────────────────────────────────────────
def test_on_path_persists_gate(monkeypatch):
    # 실제 make_judge(cross-vendor provider 빌드)를 우회 — run_product_gate 에 모의 judge 를 주입해 LLM 0콜.
    _orig_run = pg.run_product_gate   # 원본 캡처(재귀 방지)

    def fake_run(svc, pid, chapter, *, settings, judge_fn=None, rhythm_fn=None):
        # 서비스 어댑터 경로를 실제로 태우되 judge 만 모의(PASS)로 — engine 실경로 검증.
        return _orig_run(svc, pid, chapter, settings=settings,
                         judge_fn=_judge_seq(["PASS"], retentions=[85]),
                         rhythm_fn=lambda t: [])
    monkeypatch.setattr(pg, "run_product_gate", fake_run)
    svc, sess = _mk_svc("on1", product_gate=True)
    res = _gen_one(svc, sess, "on1")
    gate = res["record"].verification["gate"]
    assert isinstance(gate, dict) and gate.get("source") == "product"
    assert gate["verdict"] == "PASS" and gate["retention_est"] == 85
    assert gate["fail_exhausted"] is False
    assert isinstance(gate["gate_rounds"], list) and gate["gate_rounds"][0]["round"] == 0
    # 디스크 영속 확인(SSOT)
    st = svc.get_project("on1")
    assert st.chapters[-1].verification["gate"]["verdict"] == "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# ⓔ run_product_gate 서비스 어댑터 — PASS 경로(재시도 0)로 어댑터 조립·compact 산출 검증(LLM 0콜).
#   (FAIL 시 국소 revise/regen 라우팅 산술은 engine 부품 기준 test_dp21_gate_refit 에서 이미 커버 — 여기선
#    서비스 어댑터 배선과 compact 형태만 확인. 실 서비스 revise 내부 재검증은 중복이라 여기서 다시 태우지 않는다.)
# ─────────────────────────────────────────────────────────────────────────────
def test_run_product_gate_service_adapter_pass():
    svc, sess = _mk_svc("loc1", product_gate=False)   # 초기 생성은 OFF(자동 게이트 실 judge 회피)
    _gen_one(svc, sess, "loc1", text="주인공이 문을 열었다. " * 40)   # 커밋된 회차(1)
    ch = svc.get_project("loc1").chapters[-1]
    gate = pg.run_product_gate(svc, "loc1", ch.chapter, settings=svc.settings,
                               judge_fn=_judge_seq(["PASS"], retentions=[88]),
                               rhythm_fn=lambda t: [])
    assert gate["source"] == "product" and gate["verdict"] == "PASS"
    assert gate["retention_est"] == 88 and gate["retries"] == 0
    assert gate["fail_exhausted"] is False
    assert gate["gate_rounds"] == [gate["gate_rounds"][0]] and gate["gate_rounds"][0]["round"] == 0
    assert "llm_calls" in gate


def test_service_gate_harness_delegates():
    # _ServiceGateHarness 가 load/revise/accept/undo 를 서비스 메서드로 위임하는지(스텁 svc — 인자 관통 확인).
    calls = []

    class _Svc:
        class repo:
            @staticmethod
            def get(pid): return {"pid": pid}
        def revise_chapter(self, pid, ch, directive, span_text=""):
            calls.append(("revise", pid, ch, directive, span_text)); return {"revision_id": "r1"}
        def accept_revision(self, pid, ch, rid, after_text_fb=None, span_text_fb=None, passes_fb=None):
            calls.append(("accept", pid, ch, rid, after_text_fb)); return {"accepted": True}
        def undo_revision(self, pid, ch):
            calls.append(("undo", pid, ch)); return {"reverted": True}
    h = pg._ServiceGateHarness(_Svc(), "p")
    assert h.load("p") == {"pid": "p"}
    assert h.revise("p", 2, "dir", "span")["revision_id"] == "r1"
    assert h.accept("p", 2, "r1", after_text_fb="a")["accepted"] is True
    assert h.undo("p", 2)["reverted"] is True
    assert [c[0] for c in calls] == ["revise", "accept", "undo"]
    assert calls[0] == ("revise", "p", 2, "dir", "span")


def test_measure_from_record_tools_free():
    # measure_fn 재구성이 tools 무의존(record.ai_tell + verification.retread)으로 digest 소비 가능 dict 를 낸다.
    rec = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="t",
                        ai_tell={"comma_per_100": 1.0, "kiwi": {"ending_profile": {"n_ending": 5}}},
                        verification={"retread": {"opening_coef": 0.4, "opening_jac": 0.2}})
    meas = pg._measure_from_record(rec)
    assert meas["kiwi"]["ending_profile"]["n_ending"] == 5
    assert meas["retread"]["opening_coef"] == 0.4
    # chapter_gate._measure_digest 가 예외 없이 소비(measure-then-cite 재료)
    dg = cg._measure_digest(meas)
    assert isinstance(dg, str) and "재탕계수" in dg


# ─────────────────────────────────────────────────────────────────────────────
# ⓕ 재진입 가드 — 게이트 안 regen 재진입 시 중첩 게이트 금지
# ─────────────────────────────────────────────────────────────────────────────
def test_reentrancy_guard_blocks_nested_gate():
    svc, sess = _mk_svc("re1", product_gate=True)
    # _product_gate_active 에 pid 가 있으면 generate_next_chapter 가 _run_gate 를 켜지 않는다.
    svc._product_gate_active.add("re1")
    called = {"n": 0}
    _orig = svc._apply_product_gate
    svc._apply_product_gate = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _orig(*a, **k)
    res = _gen_one(svc, sess, "re1")
    assert called["n"] == 0                 # 재진입 중 → 게이트 훅 미발화(중첩 방지)
    assert res["record"].verification["gate"] == MISSING


# ─────────────────────────────────────────────────────────────────────────────
# ⓖ regen fix 증강 — verification.gate 있으면 drop_trigger 인용·앵커 우선, 없으면 원본 불변
# ─────────────────────────────────────────────────────────────────────────────
def test_augment_fix_noop_without_gate():
    ch = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="t")   # verification 없음
    assert CopilotService._augment_fix_with_gate(ch, None) is None
    assert CopilotService._augment_fix_with_gate(ch, "작가 지시") == "작가 지시"


def test_augment_fix_noop_when_gate_pass_no_drop():
    ch = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="t",
                       verification={"gate": {"verdict": "PASS", "drop_trigger": "없음", "fix_note": ""}})
    assert CopilotService._augment_fix_with_gate(ch, "작가 지시") == "작가 지시"   # 지적 없음 → 무증강
    assert CopilotService._augment_fix_with_gate(ch, None) is None


def test_augment_fix_cites_drop_trigger():
    ch = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="t",
                       verification={"gate": {"verdict": "FAIL",
                                              "drop_trigger": "숨 막히는 파편문의 연속",
                                              "fix_note": "도입부 리듬을 풀어라"}})
    out = CopilotService._augment_fix_with_gate(ch, None)
    assert out and "숨 막히는 파편문의 연속" in out and "국소" in out
    assert "도입부 리듬을 풀어라" in out
    # 작가 지시가 있으면 그 뒤에 게이트 참고를 덧댐(작가 우선)
    out2 = CopilotService._augment_fix_with_gate(ch, "3인칭으로 바꿔라")
    assert out2.startswith("3인칭으로 바꿔라") and "[게이트 참고]" in out2
