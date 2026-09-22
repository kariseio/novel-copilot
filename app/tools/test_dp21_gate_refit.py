# -*- coding: utf-8 -*-
"""DP-21 게이트 재시도 국소화·최선 판본 보존 — 결정론 테스트(전부 모의 LLM 0콜).

설계 docs/design-dp21-gate-refit.md §2~§3 계약을 겨눈다:
  ⓐ FAIL 재시도 2경로 — drop_trigger 앵커 성공 → revise(국소)→accept→재계측·재심사(기본),
     앵커 실패/무변경/가드레일 불통과 → regen 1회 폴백(이중 폴백 없음).
  ⓑ 국소 라운드 retention 악화 → undo 복원 + 재시도 중단·최선 판본 전진(regen 경로는 기록만).
  ⓒ GATE_SYSTEM 행동 판정 명세(포화 보정) — 프롬프트 문자열 계약(관대화 문구 부재).
  ⓓ accept_revision 폴백 revision_id=None 크래시 수리(엔진 S).
  §3 하네스 — GatedDryRunHarness revise/accept/undo 결함 주입, gate_rounds repair_path·reverted additive,
     resume 회귀(reverted 전진 인정), _LiveGatedBundle revise/undo 배선.

전부 GatedDryRunHarness(합성 도메인·실 revisions 이력) + 모의 judge(캔드 verdict·retention 시퀀스).
실 provider·LLM 콜 없음.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
import tempfile
import threading
from pathlib import Path

import pytest

import tools.gen_gated as g
from novelcopilot.domain.project import ProjectSeed, ProjectState
from novelcopilot.domain.world import WorldConfig
from novelcopilot.domain.types import ChapterRecord, ChapterStatus


SEED = ProjectSeed(title="게이트 재시도 테스트", genre="현대 판타지", target_chapters=12)


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="dp21_"))


def _harness(faults=None):
    return g.GatedDryRunHarness(g._tagged_seed(SEED), faults=faults or {})


# drop_trigger 가 dry-run 본문("[dry-run] 1화 합성 본문 — LLM 0콜.")에 실제 앵커되는 구절.
ANCHOR = "합성 본문"


def _judge_seq(verdicts, *, retentions=None, drop="합성 본문"):
    """캔드 verdict(+retention) 모의 judge(2-arg) — LLM 0콜.
    drop 은 본문에 앵커되는 구절(국소 경로 유도). retentions 로 라운드별 retention_est 지정."""
    seq = list(verdicts)
    rets = list(retentions or [])
    st = {"i": 0}

    def judge(text, meas):
        i = st["i"]
        v = seq[min(i, len(seq) - 1)]
        r = rets[min(i, len(rets) - 1)] if rets else 70
        st["i"] += 1
        return {"verdict": v,
                "drop_trigger": (drop if v == "FAIL" else "없음"),
                "fix_note": ("스팬을 압축하라" if v == "FAIL" else ""),
                "retention_est": r, "hate_comment": ""}
    return judge


def _gate(h, pid, chapter, judge, *, max_retries=2, budget=None, fix_fn=None):
    return g.gate_committed_chapter(harness=h, pid=pid, chapter=chapter, status="OK",
                                    judge_fn=judge, fix_fn=fix_fn,
                                    budget=budget or g.CallBudget(100), max_retries=max_retries)


def _committed(h):
    """1화 생성·커밋한 하네스+pid 반환."""
    pid, _ = h.create()
    h.step(pid)
    return pid


# ─────────────────────────────────────────────────────────────────────────────
# ⓐ 앵커 성공 → revise(국소) 경로
# ─────────────────────────────────────────────────────────────────────────────
def test_anchor_success_routes_to_revise_local():
    # FAIL(앵커됨)→revise→accept→재심사 PASS. repair_path='revise', regen 미사용, 본문 이력에 퇴고 1건.
    h = _harness()
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"], retentions=[50, 80]))
    assert rec["verdict"] == "PASS" and rec["retries"] == 1
    assert not rec["fail_exhausted"]
    gr = rec["gate_rounds"]
    assert [r["round"] for r in gr] == [0, 1]
    assert gr[1]["repair_path"] == "revise"          # 국소 경로로 진입
    assert gr[1]["reverted"] is False
    # 국소 경로는 revise/accept 로 본문 이력을 남긴다(regen 아님)
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert len(ch.revisions) == 1 and not ch.revisions[0].reverted
    assert g._has_event({"guard_events": rec["events"]}, "gate_revise", "flag")
    assert not any(e["guard"] == "gate_regen" for e in rec["events"])   # regen 폴백 안 씀


def test_anchor_helper_paragraph_window():
    # 앵커 헬퍼가 앵커 문단 ±1 창을 원문 그대로 슬라이스하는지(정확 substring 경로).
    text = "문단0 첫째.\n\n둘째 문단 여기 대상구절 있음.\n\n셋째 문단 끝."
    a = g.anchor_drop_trigger(text, "대상구절")
    assert a is not None and a["para_idx"] == 1
    # ±1 창 = 문단0~2 전체(원문 그대로 — revise span 검증 통과용)
    assert "문단0 첫째." in a["span_text"] and "셋째 문단 끝." in a["span_text"]
    assert a["anchor"] == "대상구절"


def test_anchor_whitespace_normalized_single_step():
    # 정확 substring 실패 시 공백 정규화 1단계로 개행만 다른 인용 흡수(교착어 fuzzy 아님).
    text = "앞 문장.\n\n대상\n구절이 개행으로 쪼개짐."
    a = g.anchor_drop_trigger(text, "대상 구절이")
    assert a is not None and "대상\n구절이" in a["span_text"]


def test_anchor_absent_returns_none():
    assert g.anchor_drop_trigger("본문에 없는 트리거 대조.", "전혀 다른 구절") is None
    assert g.anchor_drop_trigger("본문", "없음") is None        # 심사자 '없음' → 앵커 아님
    assert g.anchor_drop_trigger("", "무엇") is None


def test_anchor_ambiguous_multi_hit_returns_none():
    # 2회 이상(모호)이면 앵커 실패 → regen 폴백(정확 1회만 앵커).
    assert g.anchor_drop_trigger("반복 구절. 반복 구절.", "반복 구절") is None


# ─────────────────────────────────────────────────────────────────────────────
# ⓐ 앵커 실패 → regen 폴백
# ─────────────────────────────────────────────────────────────────────────────
def test_anchor_miss_routes_to_regen():
    # drop_trigger 가 본문에 없으면(구조적 지적) revise 안 쓰고 regen 폴백. 이력에 퇴고 0건.
    h = _harness()
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"], drop="본문에 없는 구조적 지적"))
    assert rec["verdict"] == "PASS" and rec["retries"] == 1
    assert rec["gate_rounds"][1]["repair_path"] == "regen"
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert len(ch.revisions) == 0                    # 국소 퇴고 없음
    assert any(e["guard"] == "gate_regen" for e in rec["events"])
    assert not any(e["guard"] == "gate_revise" for e in rec["events"])


# ─────────────────────────────────────────────────────────────────────────────
# ⓐ revise 무변경 → regen 폴백 (이중 폴백 없음)
# ─────────────────────────────────────────────────────────────────────────────
def test_revise_no_change_routes_to_regen():
    # 앵커는 되지만 revise 가 무변경(changed=False, 보수 편향) → accept 안 함 → regen 1회.
    h = _harness({"revise_changed": False})
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"]))
    assert rec["verdict"] == "PASS"
    assert rec["gate_rounds"][1]["repair_path"] == "regen"   # 국소 실패 → regen 폴백
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert len(ch.revisions) == 0
    ev = [e["guard"] for e in rec["events"]]
    assert "gate_regen" in ev and "gate_revise" not in ev


def test_revise_span_not_found_absorbed_to_regen():
    # 앵커는 됐으나 revise_chapter 가 span_not_found(ValueError)를 던져도 arm 중단 아니라 regen 폴백으로 흡수.
    class _H(g.GatedDryRunHarness):
        def revise(self, pid, chapter, directive, span_text=""):
            raise ValueError("span_not_found")
    h = _H(g._tagged_seed(SEED))
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"]))
    assert rec["verdict"] == "PASS"
    assert rec["gate_rounds"][1]["repair_path"] == "regen"   # 예외 흡수 → regen 폴백
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert len(ch.revisions) == 0


def test_revise_guardrail_fail_routes_to_regen():
    # 앵커·변경은 되지만 후보 가드레일 불통과 → accept 안 함 → regen 폴백.
    h = _harness({"revise_guardrail": False})
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"]))
    assert rec["gate_rounds"][1]["repair_path"] == "regen"
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert len(ch.revisions) == 0


def test_no_double_fallback_single_regen_per_round():
    # 국소 실패한 라운드는 regen 을 정확히 1회만(이중 폴백 금지). FAIL 2회 소진 → regen 2회(라운드당 1).
    h = _harness({"revise_changed": False})
    pid = _committed(h)
    b = g.CallBudget(100)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL"]), max_retries=2, budget=b)
    regen_events = [e for e in rec["events"] if e["guard"] == "gate_regen"]
    assert len(regen_events) == 2                    # 라운드1·2 각 1회(이중 아님)
    assert rec["fail_exhausted"] is True
    assert b.used == 3                               # 초1 + 재2 심사콜(regen 은 콜 계상 아님 — 심사만)


# ─────────────────────────────────────────────────────────────────────────────
# ⓑ 국소 라운드 retention 악화 → undo 복원 + 재시도 중단·최선 판본 전진
# ─────────────────────────────────────────────────────────────────────────────
def test_local_regression_undo_and_stop():
    # 초회 FAIL(ret 60) → 국소 revise → 재심사 FAIL 이고 ret 40(악화) → undo 복원·재시도 중단.
    # 최선 판본(직전, ret 60)으로 verdict 승격, fail_exhausted 아님(맹목 소진 아님), 이력 되감김.
    h = _harness()
    pid = _committed(h)
    b = g.CallBudget(100)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "FAIL"], retentions=[60, 40]),
                max_retries=2, budget=b)
    assert rec["retries"] == 1                        # 1라운드에서 중단(2라운드 안 감)
    assert rec["fail_exhausted"] is False             # ⓑ: 복원 중단은 소진 아님(kill 스트릭 제외)
    assert rec["retention"] == 60                     # 최선(직전) 판본 판정으로 승격
    gr = rec["gate_rounds"]
    assert gr[1]["repair_path"] == "revise" and gr[1]["reverted"] is True
    # 이력이 되감김(reverted=True) + 본문이 원복
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert len(ch.revisions) == 1 and ch.revisions[0].reverted is True
    assert "[dry-run revise]" not in ch.text          # 원문 복원
    assert g._has_event({"guard_events": rec["events"]}, "gate_revert", "flag")


def test_local_improvement_not_reverted():
    # 재심사 retention 이 개선(50→80)이면 undo 안 함·전진(악화만 되돌린다).
    h = _harness()
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"], retentions=[50, 80]))
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert ch.revisions[0].reverted is False          # 개선분 보존
    assert not g._has_event({"guard_events": rec["events"]}, "gate_revert", "flag")
    assert rec["gate_rounds"][1]["reverted"] is False


def test_regen_path_no_undo_only_records():
    # regen 경로(앵커 실패)는 복원 수단 없음 — 악화해도 undo 안 하고 기록만(맹목 전진 방지는 소진 스트릭 담당).
    h = _harness()
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "FAIL"], retentions=[60, 40],
                                      drop="본문에 없는 지적"), max_retries=2)
    # regen 경로라 undo 이벤트 없음(gate_revert 부재)
    assert not any(e["guard"] == "gate_revert" for e in rec["events"])
    assert all(r["repair_path"] in ("none", "regen") for r in rec["gate_rounds"])


# ─────────────────────────────────────────────────────────────────────────────
# gate_rounds 신필드(repair_path·reverted) additive
# ─────────────────────────────────────────────────────────────────────────────
_KEYS = {"round", "verdict", "drop_trigger", "fix_note", "retention", "hate_comment",
         "repair_path", "reverted"}


def test_gate_rounds_new_fields_present_and_additive():
    h = _harness()
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"], retentions=[50, 80]))
    for r in rec["gate_rounds"]:
        assert _KEYS.issubset(r.keys())
    # 초회(round=0)는 수리 진입 없음
    assert rec["gate_rounds"][0]["repair_path"] == "none"
    assert rec["gate_rounds"][0]["reverted"] is False


def test_gate_rounds_toplevel_keys_unchanged_dp21():
    # 톱레벨 키 셋 불변(resume 호환) — repair_path/reverted 는 gate_rounds 원소에만.
    h = _harness()
    pid = _committed(h)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"], retentions=[50, 80]))
    expected_top = {"completed", "status", "chapter", "verdict", "retries", "fail_exhausted",
                    "drop_trigger", "fix_note", "retention", "hate_comment", "events", "gate_rounds"}
    assert set(rec.keys()) == expected_top
    assert "repair_path" not in rec and "reverted" not in rec


# ─────────────────────────────────────────────────────────────────────────────
# resume 회귀 — ⓑ 복원 전진 회차도 완료로 인정(재심사 중복 방지)
# ─────────────────────────────────────────────────────────────────────────────
def test_resume_recognizes_reverted_advance():
    out = _tmp()
    log = out / "gate.jsonl"
    rows = [
        {"chapter": 1, "verdict": "PASS", "fail_exhausted": False, "gate_rounds": []},
        # ⓑ 복원 전진: verdict FAIL·소진 아님이지만 reverted=True 라운드로 확정 전진.
        {"chapter": 2, "verdict": "FAIL", "fail_exhausted": False,
         "gate_rounds": [{"round": 0, "reverted": False}, {"round": 1, "reverted": True}]},
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    assert g.resume_point(log) == 2                   # 복원 전진도 완료로 침(재심사 스킵)


def test_resume_unaffected_by_new_fields_normal():
    # 기존 resume 계약 불변 — 국소 경로로 PASS 전진한 회차도 종전과 동일 산출(PASS → 완료).
    h = _harness()
    out = _tmp()
    g.run_gated_arm(harness=h, arm_no=1, target_chapters=1,
                    judge_fn=_judge_seq(["FAIL", "PASS"], retentions=[50, 80]),
                    fix_fn=None, budget=g.CallBudget(100), log_path=out / "gate.jsonl",
                    max_retries=2, fail_abort_streak=3)
    assert g.resume_point(out / "gate.jsonl") == 1     # 신작 1화 PASS 전진


# ─────────────────────────────────────────────────────────────────────────────
# run_gated_arm 통합 — 국소 경로가 arm 루프에서 발화·kill 스트릭 정합
# ─────────────────────────────────────────────────────────────────────────────
def _regression_judge():
    """stateless 회차독립 judge — 국소 퇴고 마커([dry-run revise])가 본문에 있으면 악화(ret40) FAIL,
    없으면(초회 원문) ret60 FAIL. arm 루프에서 회차마다 '초회 FAIL→revise→악화→undo 중단'을 재현."""
    def judge(text, meas):
        revised = "[dry-run revise]" in (text or "")
        return {"verdict": "FAIL", "drop_trigger": ANCHOR,
                "fix_note": "스팬 압축", "retention_est": (40 if revised else 60),
                "hate_comment": ""}
    return judge


def test_arm_local_regression_does_not_count_as_fail_streak():
    # ⓑ 복원 중단(소진 아님)은 fail_streak 에 안 잡힌다 → 연속돼도 arm 중단 없음(최선 판본 전진).
    out = _tmp()
    h = _harness()
    b = g.CallBudget(100)
    arm = g.run_gated_arm(harness=h, arm_no=1, target_chapters=3,
                          judge_fn=_regression_judge(),
                          fix_fn=None, budget=b, log_path=out / "gate.jsonl",
                          max_retries=2, fail_abort_streak=3)
    assert not arm["aborted"]                          # 복원 전진 반복은 소진 스트릭 아님
    assert arm["chapters_done"] == 3
    assert arm["gate_fail_exhausted"] == 0             # 소진 0(전부 복원 전진)
    # 실제 undo 경로를 탔음(단순히 소진이 안 난 게 아님) — 각 회차마다 복원 이벤트
    revert_ev = [e for e in arm["guard_events"] if e["guard"] == "gate_revert"]
    assert len(revert_ev) == 3                          # 회차 3개 전부 복원 중단
    # 각 회차 본문은 최선 판본(원문)으로 복원 — 퇴고 마커 없음
    for ch in h.load(arm["project_id"]).chapters:
        assert "[dry-run revise]" not in ch.text


def test_arm_regen_fail_streak_still_aborts():
    # regen 경로 소진은 종전대로 kill 스트릭 발화(앵커 실패 → regen → 소진 3연속 → 중단).
    out = _tmp()
    h = _harness()
    b = g.CallBudget(100)
    arm = g.run_gated_arm(harness=h, arm_no=1, target_chapters=6,
                          judge_fn=_judge_seq(["FAIL"], drop="본문에 없는 지적"),
                          fix_fn=None, budget=b, log_path=out / "gate.jsonl",
                          max_retries=2, fail_abort_streak=3)
    assert arm["aborted"] and "gate_fail_streak" in arm["abort_reason"]
    assert arm["chapters_done"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# ⓓ accept_revision 폴백 revision_id=None 크래시 수리 (엔진 S)
# ─────────────────────────────────────────────────────────────────────────────
class _FakeExtractorVocab:
    categorical_keys: list = []
    numeric_keys: list = []

    def state_specs(self):
        return []


class _FakeChecker:
    class extractor:
        vocab = _FakeExtractorVocab()

    def check_text(self, text, ont, chapter, ids):
        class _R:
            hard: list = []
            claims: list = []
        return _R()


class _FakeOnt:
    def scan_present_ids(self, text):
        return []

    def name(self, eid):
        return eid


class _FakeGen:
    def _summarize(self, text, prior="", beat=None):   # RV-2①: accept 가 beat 를 3번째 인자로 전달(생성 경로 동형)
        return ("요약", "상세", None)


class _FakeRag:
    def index_chapter(self, n, text):
        pass


class _FakeBundle:
    ontology = _FakeOnt()
    checker = _FakeChecker()
    generator = _FakeGen()
    rag = _FakeRag()


class _FakeSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.bundle = _FakeBundle()

    def snapshot_into(self, state):
        pass


class _FakeSessions:
    def __init__(self):
        self._s = {}

    def get_or_create(self, state):
        return self._s.setdefault(state.id, _FakeSession())

    def evict(self, pid):
        self._s.pop(pid, None)


def _svc_with_chapter():
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    tmp = Path(tempfile.mkdtemp(prefix="dp21svc_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions()
    state = ProjectState(id="p1", seed=ProjectSeed(title="t"),
                         world=WorldConfig(title="t", synopsis="s"),
                         current_chapter=1,
                         chapters=[ChapterRecord(chapter=1, title="1화",
                                                 status=ChapterStatus.FINALIZED,
                                                 text="원본 회차 본문입니다.")])
    svc.repo.save(state)
    return svc


def test_accept_fallback_none_revision_id_no_crash():
    # ⓓ: 캐시 만료 폴백(draft 미스) + revision_id=None → 종전 ValidationError 크래시. 이제 서버가 id 부여.
    svc = _svc_with_chapter()
    res = svc.accept_revision("p1", 1, None, after_text_fb="퇴고된 회차 본문입니다.")
    assert res is not None and res["accepted"] is True
    ch = svc.repo.get("p1").chapter(1)
    assert len(ch.revisions) == 1
    assert ch.revisions[0].revision_id and isinstance(ch.revisions[0].revision_id, str)
    assert ch.text == "퇴고된 회차 본문입니다."


def test_accept_fallback_empty_revision_id_no_crash():
    # 빈 문자열도 동일 정합(falsy) — 서버가 고유 id 부여.
    svc = _svc_with_chapter()
    res = svc.accept_revision("p1", 1, "", after_text_fb="다른 퇴고 본문입니다.")
    assert res["accepted"] is True
    ch = svc.repo.get("p1").chapter(1)
    assert ch.revisions[0].revision_id


def test_chapter_revision_rejects_explicit_none():
    # 회귀 근거: revision_id=None 은 default_factory 를 우회해 pydantic 이 거절한다(=수리 필요성 증빙).
    from novelcopilot.domain.types import ChapterRevision
    with pytest.raises(Exception):
        ChapterRevision(revision_id=None)
    # 인자 미제공이면 default_factory 로 자동 부여(정상)
    assert ChapterRevision().revision_id


# ─────────────────────────────────────────────────────────────────────────────
# §3 하네스 결함 주입 — GatedDryRunHarness revise/accept/undo 계약(단위)
# ─────────────────────────────────────────────────────────────────────────────
def test_dry_harness_revise_accept_undo_roundtrip():
    h = _harness()
    pid = _committed(h)
    before = next(c for c in h.load(pid).chapters if c.chapter == 1).text
    rv = h.revise(pid, 1, "압축하라", before)
    assert rv["changed"] is True and rv["revision_id"] and rv["guardrail"]["passed"]
    ac = h.accept(pid, 1, rv["revision_id"], after_text_fb=rv["after_text"])
    assert ac["accepted"] is True
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert ch.text == rv["after_text"] and len(ch.revisions) == 1
    un = h.undo(pid, 1)
    assert un["reverted"] is True
    ch2 = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert ch2.text == before and ch2.revisions[0].reverted is True


def test_dry_harness_revise_changed_false_no_id():
    h = _harness({"revise_changed": False})
    pid = _committed(h)
    rv = h.revise(pid, 1, "d", "s")
    assert rv["changed"] is False and rv["revision_id"] is None


def test_dry_harness_accept_none_id_no_crash():
    # 하네스 accept 도 revision_id=None 을 서버 부여로 흡수(러너 _revise 가 무변경이면 accept 안 하지만 방어).
    h = _harness()
    pid = _committed(h)
    ac = h.accept(pid, 1, None, after_text_fb="새 본문")
    assert ac["accepted"] is True
    ch = next(c for c in h.load(pid).chapters if c.chapter == 1)
    assert ch.revisions[0].revision_id


# ─────────────────────────────────────────────────────────────────────────────
# _LiveGatedBundle revise/undo 배선(모의 svc 위임 — 실 LLM 0콜)
# ─────────────────────────────────────────────────────────────────────────────
def test_live_harness_revise_undo_delegate_to_svc():
    # GatedLiveHarness.revise/accept/undo 가 svc 의 실경로로 위임하는지(스텁 svc 로 인자 관통 확인).
    h = g.GatedLiveHarness.__new__(g.GatedLiveHarness)
    calls = []

    class _Svc:
        def revise_chapter(self, pid, ch, directive, span_text=""):
            calls.append(("revise", pid, ch, directive, span_text))
            return {"revision_id": "rid1", "changed": True, "after_text": "a",
                    "guardrail": {"passed": True}}

        def accept_revision(self, pid, ch, rid, after_text_fb=None, span_text_fb=None,
                            passes_fb=None):
            calls.append(("accept", pid, ch, rid, after_text_fb))
            return {"accepted": True}

        def undo_revision(self, pid, ch):
            calls.append(("undo", pid, ch))
            return {"reverted": True, "revision_id": "rid1"}

    h.svc = _Svc()
    assert h.revise("p", 3, "dir", "span")["revision_id"] == "rid1"
    assert h.accept("p", 3, "rid1", after_text_fb="a")["accepted"] is True
    assert h.undo("p", 3)["reverted"] is True
    assert [c[0] for c in calls] == ["revise", "accept", "undo"]
    assert calls[0] == ("revise", "p", 3, "dir", "span")


# ─────────────────────────────────────────────────────────────────────────────
# ⓒ GATE_SYSTEM 행동 판정 명세(포화 보정) — 프롬프트 계약(관대화 문구 부재)
# ─────────────────────────────────────────────────────────────────────────────
def test_gate_system_has_behavioral_verdict_spec():
    s = g.GATE_SYSTEM
    # 행동 문턱 명세: FAIL=하차 트리거 실재 + 인용 의무, 다듬기 지적=PASS+fix_note
    assert "하차 트리거" in s
    assert "PASS" in s and "fix_note" in s
    # hair-trigger 불변 — '닫을 이유 먼저'·인용 의무 유지
    assert "닫을 이유" in s
    assert "인용" in s
    # 관대화 문구 금지(프로토콜 위반 방지) — '관대'/'너그럽' 같은 완화 지시가 없어야
    assert "관대" not in s and "너그럽" not in s


# ─────────────────────────────────────────────────────────────────────────────
# P-8: 라이브 국소 경로(revise/accept)의 실제 LLM 콜을 CallBudget 에 계상 (비용 가드 정합)
# ─────────────────────────────────────────────────────────────────────────────
class _MeteredHarness(g.GatedDryRunHarness):
    """드라이런 도메인(실 revisions)에 라이브의 LLM 콜 계측 훅(llm_call_count)만 얹은 하네스.
    revise 는 +3콜, accept 는 +2콜을 누적 카운터에 더한다(라이브 revise_chapter/accept_revision 의
    실 콜 구조 근사) — GatedLiveHarness.llm_call_count(=세션 provider chat_calls 누적)와 동형 계약.
    실 provider·서비스는 만들지 않는다(LLM 0콜)."""

    def __init__(self, *a, revise_cost=3, accept_cost=2, **kw):
        super().__init__(*a, **kw)
        self._calls = 0
        self._revise_cost = revise_cost
        self._accept_cost = accept_cost

    def llm_call_count(self, pid: str):
        return self._calls

    def revise(self, pid, chapter, directive, span_text=""):
        self._calls += self._revise_cost
        return super().revise(pid, chapter, directive, span_text)

    def accept(self, pid, chapter, revision_id, after_text_fb=None, span_text_fb=None,
               passes_fb=None):
        self._calls += self._accept_cost
        return super().accept(pid, chapter, revision_id, after_text_fb=after_text_fb,
                              span_text_fb=span_text_fb, passes_fb=passes_fb)


def _committed_metered(h):
    pid, _ = h.create()
    h.step(pid)
    return pid


def test_p8_live_revise_accept_calls_charged_to_budget():
    # 앵커 성공 → revise(+3)→accept(+2) = 5 국소 콜 + 심사 3콜(초1·revise후1·pass는 없음… 실제: 초1+재심1=2) 계상.
    # 초회 FAIL(심사1) → revise+accept(국소5) → 재심사 PASS(심사1). 총 used = 1 + 5 + 1 = 7.
    h = _MeteredHarness(g._tagged_seed(SEED), revise_cost=3, accept_cost=2)
    pid = _committed_metered(h)
    b = g.CallBudget(100)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"], retentions=[50, 80]), budget=b)
    assert rec["verdict"] == "PASS" and rec["retries"] == 1
    assert b.used == 7                                 # 심사 2(초1+재1) + 국소 5(revise3+accept2)
    # 국소 경로로 실제 진입(regen 폴백 아님)
    assert rec["gate_rounds"][1]["repair_path"] == "revise"


def test_p8_dryrun_revise_not_charged_arithmetic_unchanged():
    # 훅 없는 드라이런은 revise/accept 콜 무계상 — 심사 콜만(초1+재1=2). 모의 경로 산술 불변 회귀.
    h = _harness()
    pid = _committed(h)
    b = g.CallBudget(100)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"], retentions=[50, 80]), budget=b)
    assert rec["gate_rounds"][1]["repair_path"] == "revise"
    assert b.used == 2                                 # 심사만(국소 콜 0 — 계측 불가)


def test_p8_revise_calls_charged_even_on_guardrail_fail():
    # 후보 가드레일 불통과(accept 안 함)여도 revise LLM 콜(+3)은 이미 소비됐으므로 계상.
    #   초회 FAIL(심사1) → revise(+3, 가드레일 불통과) → regen 폴백 → 재심사 PASS(심사1). used = 1+3+1 = 5.
    h = _MeteredHarness(g._tagged_seed(SEED), revise_cost=3, accept_cost=2,
                        faults={"revise_guardrail": False})
    pid = _committed_metered(h)
    b = g.CallBudget(100)
    rec = _gate(h, pid, 1, _judge_seq(["FAIL", "PASS"]), budget=b)
    assert rec["gate_rounds"][1]["repair_path"] == "regen"   # 국소 실패 → regen 폴백
    assert b.used == 5                                 # 심사 2 + revise 3(accept 0 — 채택 안 함)


def test_p8_local_calls_can_trip_budget_cap():
    # 국소 콜 계상이 상한을 넘기면 기존 LLMBudgetExceeded 흐름 그대로 → arm llm_cap abort.
    #   초회 심사1(used=1) → revise(+3)로 used=4 > cap=3 → LLMBudgetExceeded.
    out = _tmp()
    h = _MeteredHarness(g._tagged_seed(SEED), revise_cost=3, accept_cost=2)
    b = g.CallBudget(3)
    arm = g.run_gated_arm(harness=h, arm_no=1, target_chapters=4,
                          judge_fn=_judge_seq(["FAIL", "PASS"], retentions=[50, 80]),
                          fix_fn=None, budget=b, log_path=out / "gate.jsonl",
                          max_retries=2, fail_abort_streak=3)
    assert arm["aborted"] and g._has_event(arm, "llm_cap", "abort")


def test_p8_live_harness_llm_call_count_reads_session_usage():
    # GatedLiveHarness.llm_call_count 가 세션 provider 의 누적 chat_calls 를 그대로 노출하는지(스텁 세션).
    h = g.GatedLiveHarness.__new__(g.GatedLiveHarness)

    class _Usage:
        chat_calls = 17

    class _Provider:
        usage = _Usage()

    class _Sess:
        provider = _Provider()

    class _Sessions:
        def get_or_create(self, state):
            return _Sess()

    class _Repo:
        def get(self, pid):
            return object()          # 비어있지 않은 state 대역

    class _Svc:
        sessions = _Sessions()

    h.repo = _Repo()
    h.svc = _Svc()
    assert h.llm_call_count("p1") == 17
    # state None(작품 부재)·예외는 None 으로 흡수(계상 0 방어)
    h.repo = type("R", (), {"get": lambda self, pid: None})()
    assert h.llm_call_count("p1") is None
