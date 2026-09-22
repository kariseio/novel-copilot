# -*- coding: utf-8 -*-
"""DP-5 게이트 러너 결정론 테스트 — 매화 게이트 루프의 계약을 전부 모의로 검증(LLM 0콜).

검증 6종(설계 §5):
  1. 재시도 상한 산술 — FAIL 시 fix_note 재생성이 정확히 R회(초1+재R), 콜/커서 산술 일치
  2. 연속 FAIL-소진 중단 — R 소진 FAIL 3화 연속 → arm 중단(kill criteria), 4화째 미생성
  3. FAIL 플래그 전진 — 단발 FAIL-소진은 중단 아님, fail_exhausted 플래그로 기록하고 전진(은폐 금지)
  4. resume — 게이트 로그에서 PASS/소진전진 최대 회차를 재개점으로 산출
  5. 이중 잠금 — run(mode='live') 는 confirm='FIRE' 없으면 PermissionError, CLI 비TTY --live 차단
  6. 콜 상한 — run 당 LLM 게이트 콜 상한 초과 시 즉시 중단+기록

전부 GatedDryRunHarness(합성 도메인·LLM 0콜) + 모의 judge(캔드 verdict 시퀀스). 실 provider·서비스 미생성.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
import tempfile
from pathlib import Path

import pytest

import tools.gen_gated as g
from novelcopilot.domain.project import ProjectSeed


SEED = ProjectSeed(title="게이트 테스트", genre="현대 판타지", target_chapters=12)


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="dp5test_"))


def _harness(faults=None):
    return g.GatedDryRunHarness(g._tagged_seed(SEED), faults=faults or {})


def _seq_judge(verdicts):
    """캔드 verdict(2-arg 모의 judge) — LLM 0콜. fix_note/drop_trigger 도 반환(재생성 흐름 재료)."""
    return g._seq_judge_factory(verdicts)()


def _arm(*, verdicts, chapters=6, llm_cap=100, max_retries=2, fail_streak=3, faults=None,
         fix_fn=None, out=None):
    out = out or _tmp()
    h = _harness(faults)
    b = g.CallBudget(llm_cap)
    return h, b, g.run_gated_arm(harness=h, arm_no=1, target_chapters=chapters,
                                 judge_fn=_seq_judge(verdicts), fix_fn=fix_fn, budget=b,
                                 log_path=out / "gate.jsonl", max_retries=max_retries,
                                 fail_abort_streak=fail_streak), out


def _log_rows(out: Path):
    p = out / "gate.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# 0. 게이트 verdict 의 verification.gate 영속(PR-2 SSOT — ST-12a 검증 런 적발 2026-07-14)
#    러너 경로가 jsonl 에만 기록하고 SSOT 는 '미실행'으로 남던 허위 결측의 회귀 차단.
# ─────────────────────────────────────────────────────────────────────────────
def test_persist_gate_verification_writes_ssot():
    from novelcopilot.domain.types import ChapterRecord, ChapterStatus
    from novelcopilot.domain.world import WorldConfig
    from novelcopilot.domain.project import ProjectState, ProjectSeed as PS

    rec = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="본문 문장. " * 80)
    state = ProjectState(id="p1", seed=PS(premise="p"), world=WorldConfig(title="t"),
                         current_chapter=1, chapters=[rec])
    saved = []

    class Repo:
        def save(self, st):
            saved.append(st)

    class H:
        repo = Repo()

        def load(self, pid):
            return state

    gate = {"chapter": 1, "verdict": "PASS", "retention": 82, "retries": 0,
            "fail_exhausted": False, "drop_trigger": "없음", "fix_note": "",
            "hate_comment": "", "gate_rounds": [{"round": 0, "verdict": "PASS"}]}
    assert g.persist_gate_verification(H(), "p1", 1, gate) is True
    assert saved, "repo.save 미호출 — 영속 안 됨"
    v = rec.verification
    assert isinstance(v, dict) and isinstance(v.get("gate"), dict)
    assert v["gate"]["verdict"] == "PASS" and v["gate"]["retention_est"] == 82
    assert v["gate"]["source"] == "runner"                       # 제품 경로(source='product')와 구분
    assert v["gate"]["gate_rounds"][0]["verdict"] == "PASS"


def test_persist_gate_verification_dry_run_noop():
    # 비영속 하네스(repo 없음 — dry-run) → True(실패 아님·플래그 미발화), 저장 시도 없음
    class H:
        repo = None

        def load(self, pid):
            raise AssertionError("비영속 하네스에서 load 호출되면 안 됨")

    assert g.persist_gate_verification(H(), "p1", 1, {"verdict": "PASS"}) is True


def test_persist_gate_verification_missing_chapter_false():
    # 대상 회차 부재 → False(호출부가 flag 가시화 — 은폐 금지)
    from novelcopilot.domain.world import WorldConfig
    from novelcopilot.domain.project import ProjectState, ProjectSeed as PS

    class Repo:
        def save(self, st):
            raise AssertionError("회차 부재인데 save 호출")

    class H:
        repo = Repo()

        def load(self, pid):
            return ProjectState(id="p1", seed=PS(premise="p"), world=WorldConfig(title="t"),
                                current_chapter=0, chapters=[])

    assert g.persist_gate_verification(H(), "p1", 1, {"verdict": "PASS"}) is False


# ─────────────────────────────────────────────────────────────────────────────
# 1. 재시도 상한 산술
# ─────────────────────────────────────────────────────────────────────────────
def test_retry_cap_exact_arithmetic():
    # 회차1: FAIL,FAIL,PASS → 초1+재2=3콜, retries=2(R=2 이내 회복). 나머지 PASS 1콜씩.
    h, b, arm, out = _arm(verdicts=["FAIL", "FAIL", "PASS"], chapters=1, max_retries=2)
    assert not arm["aborted"]
    assert arm["chapters_done"] == 1                 # 커서 정확히 1(regen 은 in-place — 회차 안 부풀림)
    assert b.used == 3                               # 초1 + 재2 = 3(정확)
    rows = _log_rows(out)
    assert len(rows) == 1 and rows[0]["verdict"] == "PASS" and rows[0]["retries"] == 2
    assert rows[0]["fail_exhausted"] is False


def test_retry_never_exceeds_R():
    # 항상 FAIL 이면 회차당 정확히 초1+재R 콜, retries==R, 회차 하나만 진행(소진)
    h, b, arm, out = _arm(verdicts=["FAIL"], chapters=1, max_retries=2)
    assert b.used == 3                               # 초1 + 재2 (R 초과 재시도 없음)
    rows = _log_rows(out)
    assert rows[0]["retries"] == 2 and rows[0]["fail_exhausted"] is True


def test_R_zero_no_retry():
    # R=0 이면 재생성 없이 1콜만 — FAIL 즉시 소진 전진(재시도 상한 0 계약)
    h, b, arm, out = _arm(verdicts=["FAIL"], chapters=1, max_retries=0)
    assert b.used == 1
    rows = _log_rows(out)
    assert rows[0]["retries"] == 0 and rows[0]["fail_exhausted"] is True


def test_regen_is_in_place_no_cursor_inflation():
    # 재생성이 새 회차를 만들지 않는지(커서 불변) 직접 확인 — DryRunHarness step 오사용 회귀 방어
    h, b, arm, out = _arm(verdicts=["FAIL", "PASS"], chapters=1, max_retries=2)
    st = h.load(arm["project_id"])
    assert st.current_chapter == 1 and len(st.chapters) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. 연속 FAIL-소진 중단(kill criteria)
# ─────────────────────────────────────────────────────────────────────────────
def test_consecutive_fail_exhausted_aborts_at_streak():
    h, b, arm, out = _arm(verdicts=["FAIL"], chapters=6, max_retries=2, fail_streak=3)
    assert arm["aborted"] and "gate_fail_streak" in arm["abort_reason"]
    assert arm["chapters_done"] == 3                 # 3화째에서 중단 — 4화 미생성(맹목 전진 금지)
    assert g._has_event(arm, "gate_fail_streak", "abort")
    assert b.used == 9                               # 3화 × (초1+재2) = 9


def test_fail_streak_threshold_respected():
    # fail_streak=2 로 낮추면 2화째 중단
    h, b, arm, out = _arm(verdicts=["FAIL"], chapters=6, max_retries=1, fail_streak=2)
    assert arm["aborted"] and arm["chapters_done"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 3. FAIL 플래그 전진(은폐 금지) — 단발 소진은 중단 아님
# ─────────────────────────────────────────────────────────────────────────────
def test_single_fail_exhausted_flags_and_advances():
    # 회차1 소진 FAIL, 회차2~4 PASS → 스트릭 리셋, 중단 없음, 소진 1건이 로그·집계에 그대로 남음
    h, b, arm, out = _arm(verdicts=["FAIL", "FAIL", "FAIL", "PASS"], chapters=4, max_retries=2,
                          fail_streak=3)
    # 시퀀스: ch1(FAIL,FAIL,FAIL=소진) → ch2(PASS) → ch3(PASS) → ch4(PASS)
    assert not arm["aborted"]
    assert arm["chapters_done"] == 4
    assert arm["gate_fail_exhausted"] == 1
    rows = _log_rows(out)
    ch1 = next(r for r in rows if r["chapter"] == 1)
    assert ch1["fail_exhausted"] is True and ch1["verdict"] == "FAIL"   # 은폐 없이 FAIL 표면화
    assert g._has_event(arm, "gate_fail_exhausted", "flag")


def test_non_consecutive_fails_do_not_abort():
    # FAIL-소진이 연속이 아니면(사이에 PASS) 스트릭이 리셋돼 중단되지 않음
    # ch1 소진, ch2 PASS, ch3 소진 — 연속 아님 → 중단 없음, 소진 2건 플래그 전진
    seq = ["FAIL", "FAIL", "FAIL",   # ch1 소진
           "PASS",                    # ch2
           "FAIL", "FAIL", "FAIL",   # ch3 소진
           "PASS", "PASS"]            # ch4, ch5
    h, b, arm, out = _arm(verdicts=seq, chapters=5, max_retries=2, fail_streak=3)
    assert not arm["aborted"] and arm["chapters_done"] == 5
    assert arm["gate_fail_exhausted"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. resume — 게이트 로그에서 재개점 산출
# ─────────────────────────────────────────────────────────────────────────────
def test_resume_point_from_gate_log():
    out = _tmp()
    log = out / "gate.jsonl"
    rows = [
        {"chapter": 1, "verdict": "PASS", "fail_exhausted": False},
        {"chapter": 2, "verdict": "PASS", "fail_exhausted": False},
        {"chapter": 3, "verdict": "FAIL", "fail_exhausted": True},   # 소진 전진도 '완료'로 침
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    assert g.resume_point(log) == 3


def test_resume_point_empty_and_missing():
    out = _tmp()
    assert g.resume_point(out / "nope.jsonl") == 0        # 로그 없음 → 0(처음부터)
    empty = out / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert g.resume_point(empty) == 0


def test_resume_ignores_non_advancing_lines():
    # LLM_CAP/미전진(ESCALATED·verdict None) 라인은 재개점으로 치지 않음(중복 콜/생성 방지)
    out = _tmp()
    log = out / "gate.jsonl"
    rows = [
        {"chapter": 1, "verdict": "PASS", "fail_exhausted": False},
        {"chapter": 2, "status": "ESCALATED", "verdict": None, "fail_exhausted": False},
        {"chapter": 0, "status": "LLM_CAP"},
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    assert g.resume_point(log) == 1                       # PASS 최대만(ch2 미전진·cap 무시)


# ─────────────────────────────────────────────────────────────────────────────
# 5. 이중 잠금(발사 방지) — run() confirm + CLI 비TTY
# ─────────────────────────────────────────────────────────────────────────────
def test_run_live_requires_confirm_fire():
    with pytest.raises(PermissionError):
        g.run(mode="live", seed=SEED, works=1, chapters=2)          # confirm 없음 → 잠금
    with pytest.raises(PermissionError):
        g.run(mode="live", seed=SEED, works=1, chapters=2, confirm="LAUNCH")  # 틀린 토큰


def test_run_dry_run_ignores_confirm():
    # dry-run 은 confirm 무영향 — LiveHarness/provider 미생성(구조적 0콜)
    m = g.run(mode="dry-run", seed=SEED, works=1, chapters=2, out_dir=_tmp())
    assert m["mode"] == "dry-run" and m["llm_calls_total"] >= 0


def test_cli_noninteractive_live_blocked(monkeypatch):
    # 비TTY(파이프/CI)에서 --live(--fire 없이)는 무조건 차단 → LiveHarness 미생성
    monkeypatch.setattr(g, "_stdin_isatty", lambda: False)
    rc = g.main(["--live", "--chapters", "2"])
    assert rc == 3                                                   # 실발사 취소


def test_cli_fire_without_live_rejected():
    assert g.main(["--fire", "--chapters", "2"]) == 2                # --fire 는 --live 필수


def test_cli_dry_and_live_mutually_exclusive():
    assert g.main(["--dry-run", "--live"]) == 2


def test_cli_no_mode_defaults_to_no_launch():
    assert g.main(["--chapters", "2"]) == 2                          # 모드 미지정 = 발사 없음


def test_cli_dry_run_green(tmp_path):
    rc = g.main(["--dry-run", "--chapters", "3", "--out-dir", str(tmp_path)])
    assert rc == 0                                                   # dry-run GREEN(가드+셀프테스트)


# ─────────────────────────────────────────────────────────────────────────────
# 6. LLM 콜 상한(비용 가드)
# ─────────────────────────────────────────────────────────────────────────────
def test_llm_cap_aborts_and_records():
    # cap=2 면 3콜째(2화 초안)에서 초과 → 즉시 중단+기록. cap 은 '게이트 콜'만 계상.
    h, b, arm, out = _arm(verdicts=["PASS"], chapters=6, llm_cap=2)
    assert arm["aborted"] and "llm_cap" in arm["abort_reason"]
    assert g._has_event(arm, "llm_cap", "abort")
    assert b.used == 3                                               # 상한(2) 초과 시점에 멈춤


def test_llm_cap_counts_retries():
    # 재시도 콜도 상한에 계상 — 회차1 FAIL 재시도 2콜로 cap=2 초과(cap은 심사 예산 전체)
    h, b, arm, out = _arm(verdicts=["FAIL"], chapters=6, llm_cap=2, max_retries=2)
    assert arm["aborted"] and g._has_event(arm, "llm_cap", "abort")


def test_call_budget_unit():
    b = g.CallBudget(2)
    b.charge(); b.charge()                                           # used=2 == cap → ok
    with pytest.raises(g.LLMBudgetExceeded):
        b.charge()                                                  # used=3 > cap → 예외
    b2 = g.CallBudget(-1)                                            # cap<0 = 무제한
    for _ in range(100):
        b2.charge()
    assert b2.used == 100


# ─────────────────────────────────────────────────────────────────────────────
# 추가: gen_longrun 이식 가드 불변(spine/cursor/예외 재시도)이 게이트 루프에서도 발화
# ─────────────────────────────────────────────────────────────────────────────
def test_ported_spine_guard_aborts():
    h, b, arm, out = _arm(verdicts=["PASS"], chapters=4, faults={"no_arcs": True})
    assert arm["aborted"] and g._has_event(arm, "spine", "abort")
    assert arm["attempts"] == 0 and b.used == 0                      # spine 위반은 생성/콜 0


def test_ported_cursor_guard_aborts():
    h, b, arm, out = _arm(verdicts=["PASS"], chapters=4, faults={"cursor_break_at": 2})
    assert arm["aborted"] and g._has_event(arm, "cursor", "abort")


def test_ported_step_exception_retried_once():
    # 게이트 회차 예외 1회 재시도(transient 흡수) — raise_at 는 step/regen attempt 마다 증가
    h, b, arm, out = _arm(verdicts=["PASS"], chapters=3, faults={"raise_at": [1]})
    assert g._has_event(arm, "step_retry", "flag")
    assert not arm["aborted"]                                        # 재시도로 흡수 → 정상 진행


def test_escalated_returns_without_gate_call():
    # ESCALATED 회차는 게이트 콜 없이 반환(생성 실패에 심사 콜 낭비 금지)
    h = _harness({"escalate_at": [1]})
    b = g.CallBudget(10)
    rec = g.gate_one_chapter(harness=h, pid=h.create()[0], judge_fn=_seq_judge(["PASS"]),
                             fix_fn=None, budget=b)
    assert rec["status"] == "ESCALATED" and b.used == 0


# ─────────────────────────────────────────────────────────────────────────────
# 추가: fix_spans 트리거(리듬 스팬 검출 시 v2 수리 시도) — 결정론·모의 fix_fn
# ─────────────────────────────────────────────────────────────────────────────
def test_fix_spans_triggers_on_rhythm_spans(monkeypatch):
    # 본문에 '~다' run 을 심어 rhythm_spans 가 스팬을 내면 fix_fn 이 호출되고 flag 가 남는지 검증(LLM 0콜)
    called = {"n": 0}

    def fake_fix(harness, pid, chapter, text, spans):
        called["n"] += 1
        return True                                                 # 수리 적용됨

    # DryRunHarness 합성 본문에 run 을 강제하기 위해 rhythm_spans 를 스텁(검출기 자체 검증은 st11 소관)
    monkeypatch.setattr(g, "rhythm_spans", lambda text, run_threshold=6: [{"kind": "ending_run"}])
    h, b, arm, out = _arm(verdicts=["PASS"], chapters=1, fix_fn=fake_fix)
    assert called["n"] >= 1
    assert g._has_event(arm, "fix_spans", "flag")


def test_no_fix_spans_when_none_detected(monkeypatch):
    called = {"n": 0}

    def fake_fix(harness, pid, chapter, text, spans):
        called["n"] += 1
        return True

    monkeypatch.setattr(g, "rhythm_spans", lambda text, run_threshold=6: [])
    h, b, arm, out = _arm(verdicts=["PASS"], chapters=1, fix_fn=fake_fix)
    assert called["n"] == 0                                          # 스팬 없으면 fix 미호출
    assert not g._has_event(arm, "fix_spans", "flag")


# ─────────────────────────────────────────────────────────────────────────────
# 추가: cross-vendor 심사 라우팅(gen≠judge) — 벤더 선택 규칙만 검증(콜 없음)
# ─────────────────────────────────────────────────────────────────────────────
def test_judge_is_cross_vendor():
    class _S:  # get_settings 대역 불필요 — make_judge 는 spec 문자열만 결정
        pass
    # anthropic 계열 prose → openai 심사 spec
    _, spec_a = g.make_judge(_settings_stub(), "anthropic")
    assert spec_a.startswith("openai:")
    _, spec_c = g.make_judge(_settings_stub(), "claude-opus-4-8")
    assert spec_c.startswith("openai:")
    # openai 계열 prose → anthropic 심사 spec
    _, spec_o = g.make_judge(_settings_stub(), "openai")
    assert spec_o.startswith("anthropic:")


def _settings_stub():
    # create_role_provider 는 키 부재 시 기본 provider 로 안전 폴백(빌드 실패 무해) — 실제 콜 없음
    from novelcopilot.config import get_settings
    return get_settings()


# ─────────────────────────────────────────────────────────────────────────────
# 추가: guard_selftest 전건 통과(dry-run GREEN 조건)
# ─────────────────────────────────────────────────────────────────────────────
def test_guard_selftest_all_pass():
    st = g.guard_selftest(_tmp(), "utrun")
    assert st["pass"] is True
    names = {c["name"]: c["pass"] for c in st["checks"]}
    assert all(names.values()), names


# ─────────────────────────────────────────────────────────────────────────────
# 회귀: 리뷰 확정 HIGH/MED 수리(전부 모의 — LLM 0콜)
# ─────────────────────────────────────────────────────────────────────────────
def _raise_once_then(judge_after):
    """첫 게이트 콜에서 1회 예외(post-step transient) 후 이어서 judge_after 시퀀스로 심사하는 모의 judge.
    2-arg 시그니처 유지(모의) — step 은 성공하고 게이트 단계에서만 터지는 경로를 만든다."""
    seq = list(judge_after)
    state = {"raised": False, "i": 0}

    def judge(text, meas):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("post-step transient (gate stage)")
        v = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        return {"verdict": v, "drop_trigger": "없음", "fix_note": "", "retention_est": 70,
                "hate_comment": ""}
    return judge


def test_post_step_exception_does_not_regenerate_ungated_chapter():
    # ★HIGH 회귀: step 성공 후(게이트 단계) 일시 예외 1회 → step 재호출 없이 게이트만 재시도.
    # 회차 중복 생성 금지: chapters==2 목표에 딱 2회차만, 커서==2, 각 회차 gate 로그 1줄(미심사 회차 없음).
    out = _tmp()
    h = _harness()
    b = g.CallBudget(100)
    arm = g.run_gated_arm(harness=h, arm_no=1, target_chapters=2,
                          judge_fn=_raise_once_then(["PASS", "PASS", "PASS"]),
                          fix_fn=None, budget=b, log_path=out / "gate.jsonl",
                          max_retries=2, fail_abort_streak=3)
    st = h.load(arm["project_id"])
    assert st.current_chapter == 2 and len(st.chapters) == 2       # 회차 부풀림 0(커서 멱등)
    assert arm["chapters_done"] == 2 and not arm["aborted"]
    assert g._has_event(arm, "gate_retry", "flag")                 # 게이트 단계 재시도(step_retry 아님)
    assert not g._has_event(arm, "step_retry", "flag")             # step 은 재호출되지 않음
    rows = _log_rows(out)
    assert len(rows) == 2                                          # 정확히 2회차 — 미심사 은폐 회차 없음
    assert all(r.get("verdict") == "PASS" for r in rows)          # 두 회차 모두 실제 심사됨


def test_retry_round_preserves_ctx_for_three_arg_judge():
    # ★MED 회귀: FAIL 재시도 라운드도 ctx(story_so_far·genre)를 관통해야 한다(_judge 경유).
    # 3-arg live-형 judge 로, 초회와 재시도 모든 라운드에서 ctx 가 실림을 확인.
    seen_ctx = []

    def judge3(text, meas, ctx=None):
        seen_ctx.append(ctx)
        # 1라운드 FAIL → 재시도 유도, 그다음 PASS
        v = "FAIL" if len(seen_ctx) == 1 else "PASS"
        return {"verdict": v, "drop_trigger": "없음", "fix_note": "고쳐라",
                "retention_est": 60, "hate_comment": ""}

    out = _tmp()
    h = _harness()
    b = g.CallBudget(100)
    g.run_gated_arm(harness=h, arm_no=1, target_chapters=1, judge_fn=judge3,
                    fix_fn=None, budget=b, log_path=out / "gate.jsonl",
                    max_retries=2, fail_abort_streak=3)
    assert len(seen_ctx) >= 2                                      # 초회 + 재시도 최소 1
    # 모든 라운드에서 ctx 가 None 이 아니고 chapter 키가 실려 있어야 함(재시도 ctx 상실 회귀 방어)
    assert all(c is not None for c in seen_ctx)
    assert all(isinstance(c, dict) and "chapter" in c for c in seen_ctx)


def test_measure_does_not_read_foreign_dp4b_beats(monkeypatch):
    # ★MED 회귀: measure_chapter 는 전역 dp4b_beats.jsonl(교차작품)을 읽지 않는다 —
    # _load_beats 가 보는 BEATS_LOG 가 gen_gated 전용 경로로 스코핑됐는지 확인.
    import tools.dp4b_loop as dp4b
    seen = {"path": None}
    orig = dp4b._load_beats

    def spy():
        seen["path"] = dp4b.BEATS_LOG
        return orig()
    monkeypatch.setattr(dp4b, "_load_beats", spy)

    h = _harness()
    pid, _ = h.create()
    h.step(pid)                                                    # 1화 커밋
    g.measure_chapter(h.load(pid), 1)
    assert seen["path"] is not None
    assert "dp4b_beats.jsonl" not in str(seen["path"])            # 전역 DP-4b beats 미접근
    assert dp4b.BEATS_LOG.name.endswith("dp4b_beats.jsonl")       # 원복(전역 오염 없음)


def test_all_escalated_arm_flags_and_run_not_green():
    # ★MED 회귀: 전건 ESCALATED arm → escalated_streak 플래그(가드 ③ 이식) + 회차 0 → run GREEN 아님.
    h = _harness({"escalate_at": [1, 2, 3, 4, 5, 6]})
    b = g.CallBudget(100)
    out = _tmp()
    arm = g.run_gated_arm(harness=h, arm_no=1, target_chapters=6, judge_fn=_seq_judge(["PASS"]),
                          fix_fn=None, budget=b, log_path=out / "gate.jsonl",
                          max_retries=2, fail_abort_streak=3)
    assert arm["chapters_done"] == 0 and b.used == 0              # 회차 0·게이트 콜 0
    assert g._has_event(arm, "escalated_streak", "flag")         # 가드 ③ 발화(gen_longrun 의미)
    assert not arm["aborted"]                                    # 플래그는 중단 아님(의미 불변)

    # run 레벨: 회차 0 arm 은 GREEN 아님(false-GREEN 차단)
    m = g.run(mode="dry-run", seed=SEED, works=1, chapters=6, out_dir=_tmp(),
              harness_factory=lambda i: _harness({"escalate_at": [1, 2, 3, 4, 5, 6]}),
              judge_factory=lambda: _seq_judge(["PASS"]), fix_factory=lambda: None)
    assert m["green"] is False                                    # 생성 0인데 GREEN 보고 금지


# ─────────────────────────────────────────────────────────────────────────────
# GatedLiveHarness — 태그는 worldgen 후 부착(작중 역류 차단)·--pov 는 seed.pov 로 전달(DP-18 정식 배선).
#   사후 pov 패치·voice 보정은 제거 — pov/voice 는 create_project(worldgen) 시점에 seed.pov 로 배선된다.
# ─────────────────────────────────────────────────────────────────────────────
class _PovFakeRepo:
    def __init__(self):
        self.saved = []
        self.state = None

    def save(self, st):
        self.saved.append(st)
        self.state = st

    def get(self, pid):
        return self.state


def _live_fake(title: str = "달빛 아래", pov: str = "third_limited", voice: str = ""):
    """GatedLiveHarness 를 실서비스 없이 구성(모의) — create() 의 태그 부착 경로만 검증.
    world.style.pov/narrator_voice 는 (실경로에선) create_project 가 seed.pov 로 이미 채운 상태를 모사한다."""
    h = g.GatedLiveHarness.__new__(g.GatedLiveHarness)
    h.repo = _PovFakeRepo()

    class _Style:
        pass

    _Style.pov = pov
    _Style.narrator_voice = voice

    class _World:
        pass

    _World.title = title
    _World.style = _Style()

    class _State:
        id = "pid-test"
        world = _World()

    st = _State()
    h.repo.state = st
    return h, st


def test_pov_folded_into_seed_not_patched_posthoc(monkeypatch):
    """DP-18: --pov 는 seed.pov 로 접힌다(사후 패치 제거) — LiveHarness.__init__ 에 seed.pov='first' 로 전달."""
    seen = {}
    monkeypatch.setattr(g.LiveHarness, "__init__",
                        lambda self, seed: seen.update(pov=seed.pov, obj=seed))
    h = g.GatedLiveHarness(SEED, pov="first")
    assert seen["pov"] == "first"                       # seed.pov 로 배선(worldgen 전 create_project 가 소비)
    assert SEED.pov == ""                               # 원본 시드 불변(model_copy 로 접음 — 오염 금지)


def test_pov_empty_leaves_seed_untouched(monkeypatch):
    """빈 --pov 는 seed 를 손대지 않는다(구 경로 바이트 동일 — pov 무지정=StyleSpec 기본 상속)."""
    seen = {}
    monkeypatch.setattr(g.LiveHarness, "__init__",
                        lambda self, seed: seen.update(pov=seed.pov, is_same=seed is SEED))
    g.GatedLiveHarness(SEED, pov="")
    assert seen["pov"] == ""                            # pov 무지정
    assert seen["is_same"] is True                      # 빈값이면 model_copy 조차 안 함(동일 객체 전달)


def test_create_tags_title_after_worldgen_no_pov_patch(monkeypatch):
    """create() 는 태그만 부착 — pov/voice 는 create_project 가 seed.pov 로 이미 채운 값을 그대로 둔다."""
    h, st = _live_fake(pov="first", voice="손익부터 따지는 냉소")
    monkeypatch.setattr(g.LiveHarness, "create", lambda self: ("pid-test", st))
    pid, out = h.create()
    assert out.world.title == f"{g.DP5_TAG} 달빛 아래"   # 태그는 생성 후 부착 — 시드/worldgen 무오염
    assert out.world.style.pov == "first"               # worldgen 이 채운 값 보존(사후 패치 없음)
    assert out.world.style.narrator_voice == "손익부터 따지는 냉소"   # voice 보정 제거 — worldgen 도출분 그대로
    assert len(h.repo.saved) == 1                       # 태그 변경만 영속


def test_create_tags_title_preserves_default_pov(monkeypatch):
    """pov 무지정 작품 — create() 는 태그만 부착, 기본 third_limited 를 손대지 않는다."""
    h, st = _live_fake(pov="third_limited")
    monkeypatch.setattr(g.LiveHarness, "create", lambda self: ("pid-test", st))
    pid, out = h.create()
    assert out.world.title.startswith(g.DP5_TAG)
    assert out.world.style.pov == "third_limited"       # 무변경
    assert len(h.repo.saved) == 1


def test_create_no_double_tag_and_no_save_when_clean(monkeypatch):
    h, st = _live_fake(title=f"{g.DP5_TAG} 이미 태그됨")
    monkeypatch.setattr(g.LiveHarness, "create", lambda self: ("pid-test", st))
    pid, out = h.create()
    assert out.world.title == f"{g.DP5_TAG} 이미 태그됨"  # 이중 태그 없음
    assert h.repo.saved == []  # 변경 없음 → save 미호출


# ─────────────────────────────────────────────────────────────────────────────
# --pid reattach(1화씩 분할 출고) — create 스킵·태그 가드·추가 회차 산술
# ─────────────────────────────────────────────────────────────────────────────
def _forbid_create(h):
    def boom():
        raise AssertionError("reattach 인데 create 가 호출됨")
    h.create = boom
    return h


def test_reattach_skips_create_and_advances_from_baseline():
    h = _harness()
    pid, st0 = h.create()          # 선행 run 의 신작 생성을 재현
    baseline = st0.current_chapter
    arm = g.run_gated_arm(harness=_forbid_create(h), arm_no=1, target_chapters=1,
                          judge_fn=_seq_judge(["PASS"]), fix_fn=None, budget=g.CallBudget(10),
                          log_path=_tmp() / "r.jsonl", reattach_pid=pid)
    assert not arm["aborted"]
    assert arm["project_id"] == pid
    assert arm["chapters_done"] == 1                        # 정확히 1화만 추가
    assert h.load(pid).current_chapter == baseline + 1      # 커서도 1 전진


def test_reattach_refuses_untagged_work():
    h = g.GatedDryRunHarness(SEED)     # 태그 없는 시드 = 기존 일반 작품 재현
    pid, _ = h.create()
    arm = g.run_gated_arm(harness=_forbid_create(h), arm_no=1, target_chapters=1,
                          judge_fn=_seq_judge(["PASS"]), fix_fn=None, budget=g.CallBudget(10),
                          log_path=_tmp() / "r.jsonl", reattach_pid=pid)
    assert arm["aborted"] and "reattach_tag" in arm["abort_reason"]
    assert arm["chapters_done"] == 0   # 기존 작품 무수정 — step 미호출


def test_run_pid_requires_live_single_arm():
    with pytest.raises(AssertionError):
        g.run(mode="dry-run", seed=SEED, works=1, chapters=1, pid="x", out_dir=_tmp())


def test_cli_pid_with_dry_run_rejected(capsys):
    rc = g.main(["--dry-run", "--pid", "abc"])
    assert rc == 2


# ─────────────────────────────────────────────────────────────────────────────
# DP-18 정식 경로 — seed.pov 가 worldgen(generator.generate) 시점에 world.style.pov 로 배선되고
#   first 면 narrator_voice 도출(증거 게이트)이 정상 경로로 발화한다. 사후 패치 대체(LLM 0콜 모의).
# ─────────────────────────────────────────────────────────────────────────────
def _stub_generate(gen_cls, monkeypatch, voice_out="손익부터 따지는 냉소적 입말"):
    """generate() 의 LLM 의존부(chat_json·재검증)를 우회하고 seed.pov 배선+voice 도출 분기만 실행시키는 모의.
    _normalize 이후 로직(pov 배선 → first 시 extract_narrator_voice)을 실제 소스로 태운다."""
    from novelcopilot.domain.world import WorldConfig
    voice_calls = {"n": 0}

    def fake_chat_json(self, msg, *a, **k):
        return {"title": "달빛 아래", "genre": "현대 판타지",
                "entities": [{"id": "hero", "name": "도현"}],
                "beats": [{"chapter": 1, "title": "b", "summary": "s", "key_events": ["e"],
                           "entities": ["hero"]}]}

    def fake_voice(self, seed, world, **kw):
        voice_calls["n"] += 1
        return voice_out
    monkeypatch.setattr(gen_cls, "extract_narrator_voice", fake_voice)
    monkeypatch.setattr(gen_cls, "extract_time_anchors", lambda self, *a, **k: [])
    return fake_chat_json, voice_calls


def test_seed_pov_first_wires_worldgen_and_derives_voice(monkeypatch):
    """seed.pov='first' → generate() 가 world.style.pov 배선 후 narrator_voice 도출(정상 경로 발화)."""
    from novelcopilot.worldgen.generator import WorldGenerator
    from novelcopilot.domain.project import ProjectSeed

    class _Prov:
        pass
    fake_chat_json, voice_calls = _stub_generate(WorldGenerator, monkeypatch)
    monkeypatch.setattr(_Prov, "chat_json", fake_chat_json, raising=False)
    wg = WorldGenerator(_Prov())
    seed = ProjectSeed(title="", genre="현대 판타지", pov="first", target_chapters=6)
    world = wg.generate(seed)
    assert world.style.pov == "first"                          # 시드 명시가 world.style 로 배선
    assert world.style.narrator_voice == "손익부터 따지는 냉소적 입말"  # first → 도출 정상 발화
    assert voice_calls["n"] == 1                               # 도출 경로가 실제로 1회 실행


def test_seed_pov_empty_leaves_default_and_skips_voice(monkeypatch):
    """seed.pov='' → world.style.pov 기본(third_limited) 유지·voice 도출 스킵(구 경로 바이트 동일)."""
    from novelcopilot.worldgen.generator import WorldGenerator
    from novelcopilot.domain.project import ProjectSeed

    class _Prov:
        pass
    fake_chat_json, voice_calls = _stub_generate(WorldGenerator, monkeypatch)
    monkeypatch.setattr(_Prov, "chat_json", fake_chat_json, raising=False)
    wg = WorldGenerator(_Prov())
    seed = ProjectSeed(title="", genre="현대 판타지", target_chapters=6)   # pov 무지정
    world = wg.generate(seed)
    assert world.style.pov == "third_limited"                  # 기본값 상속(무변경)
    assert world.style.narrator_voice == ""                    # 3인칭 → 도출 미발화
    assert voice_calls["n"] == 0                               # 불필요한 LLM 콜 없음


# ─────────────────────────────────────────────────────────────────────────────
# [P-3] 라운드별 심사문 영속 — gate_rounds:[{round,verdict,drop_trigger,fix_note,retention,hate_comment}]
#   · R라운드 배열 완전성(초회=0 + 재시도 1..R 전부)  · 톱레벨 키 불변(resume 호환)  · jsonl 영속  · fix_spans 중복 금지
# ─────────────────────────────────────────────────────────────────────────────
# DP-21 additive: repair_path("revise"|"regen"|"none")·reverted(bool) — 라운드 수리 경로·복원 여부 영속.
_ROUND_KEYS = {"round", "verdict", "drop_trigger", "fix_note", "retention", "hate_comment",
               "repair_path", "reverted"}


def _gate_committed(h, pid, chapter, judge, *, max_retries=2, budget=None, fix_fn=None):
    return g.gate_committed_chapter(harness=h, pid=pid, chapter=chapter, status="OK",
                                    judge_fn=judge, fix_fn=fix_fn,
                                    budget=budget or g.CallBudget(100), max_retries=max_retries)


def test_gate_rounds_records_initial_only_on_pass():
    # 초회 PASS → 라운드 1개(round=0)뿐, 재시도 없음
    h = _harness(); pid, _ = h.create(); h.step(pid)
    rec = _gate_committed(h, pid, 1, _seq_judge(["PASS"]))
    gr = rec["gate_rounds"]
    assert len(gr) == 1
    assert gr[0]["round"] == 0 and gr[0]["verdict"] == "PASS"
    assert _ROUND_KEYS.issubset(gr[0].keys())     # 6개 키 완전


def test_gate_rounds_complete_across_R_retries():
    # FAIL,FAIL,PASS(R=2) → 라운드 3개: round 0,1,2 순차, 마지막 PASS. 각 라운드에 판정 원문 실림.
    h = _harness(); pid, _ = h.create(); h.step(pid)
    rec = _gate_committed(h, pid, 1, _seq_judge(["FAIL", "FAIL", "PASS"]), max_retries=2)
    gr = rec["gate_rounds"]
    assert [r["round"] for r in gr] == [0, 1, 2]                 # 배열 완전성(초1+재R)
    assert [r["verdict"] for r in gr] == ["FAIL", "FAIL", "PASS"]
    # FAIL 라운드는 fix_note/drop_trigger 원문 보존, PASS 라운드는 빈 fix_note
    assert gr[0]["fix_note"] == "테스트 수정 지시" and gr[0]["drop_trigger"] == "테스트 하차 대목"
    assert gr[2]["fix_note"] == "" and gr[2]["drop_trigger"] == "없음"
    assert all(_ROUND_KEYS.issubset(r.keys()) for r in gr)


def test_gate_rounds_exhausted_has_R_plus_one_all_fail():
    # 항상 FAIL(R=2) → 소진: 라운드 정확히 3개(round 0,1,2) 전부 FAIL
    h = _harness(); pid, _ = h.create(); h.step(pid)
    rec = _gate_committed(h, pid, 1, _seq_judge(["FAIL"]), max_retries=2)
    gr = rec["gate_rounds"]
    assert [r["round"] for r in gr] == [0, 1, 2]
    assert all(r["verdict"] == "FAIL" for r in gr)
    assert rec["fail_exhausted"] is True and len(gr) == rec["retries"] + 1


def test_gate_rounds_R_zero_single_round():
    # R=0 이면 재시도 없음 → 라운드 1개(round=0)만
    h = _harness(); pid, _ = h.create(); h.step(pid)
    rec = _gate_committed(h, pid, 1, _seq_judge(["FAIL"]), max_retries=0)
    assert [r["round"] for r in rec["gate_rounds"]] == [0]


def test_gate_rounds_toplevel_keys_unchanged():
    # ★톱레벨 불변: gate_rounds 추가 후에도 기존 톱레벨 키 셋이 그대로여야 resume/집계 회귀 없음.
    h = _harness(); pid, _ = h.create(); h.step(pid)
    rec = _gate_committed(h, pid, 1, _seq_judge(["FAIL", "PASS"]), max_retries=2)
    expected_top = {"completed", "status", "chapter", "verdict", "retries", "fail_exhausted",
                    "drop_trigger", "fix_note", "retention", "hate_comment", "events", "gate_rounds"}
    assert set(rec.keys()) == expected_top          # gate_rounds 만 신규, 나머지 불변
    # 톱레벨 요약값은 '마지막' 라운드와 일치(최종 판정 승격 계약 불변)
    assert rec["verdict"] == rec["gate_rounds"][-1]["verdict"] == "PASS"
    assert rec["drop_trigger"] == rec["gate_rounds"][-1]["drop_trigger"]


def test_gate_rounds_persisted_in_jsonl():
    # jsonl 로그 라인에 gate_rounds 가 그대로 영속되는지(반환뿐 아니라 디스크에도)
    h, b, arm, out = _arm(verdicts=["FAIL", "FAIL", "PASS"], chapters=1, max_retries=2)
    rows = _log_rows(out)
    assert len(rows) == 1
    gr = rows[0]["gate_rounds"]
    assert [r["round"] for r in gr] == [0, 1, 2]
    assert [r["verdict"] for r in gr] == ["FAIL", "FAIL", "PASS"]
    assert all(_ROUND_KEYS.issubset(r.keys()) for r in gr)


def test_gate_rounds_accumulate_per_chapter_in_run():
    # 회차마다 독립적으로 gate_rounds 가 누적(회차 간 오염 없음) — 각 로그 라인이 자기 회차의 라운드만.
    seq = ["FAIL", "PASS",    # ch1: 2라운드
           "PASS"]            # ch2: 1라운드
    h, b, arm, out = _arm(verdicts=seq, chapters=2, max_retries=2)
    rows = sorted(_log_rows(out), key=lambda r: r["chapter"])
    assert [len(r["gate_rounds"]) for r in rows] == [2, 1]       # 회차별 라운드 수 정확
    assert [r["gate_rounds"][-1]["verdict"] for r in rows] == ["PASS", "PASS"]


def test_gate_rounds_do_not_duplicate_fix_spans_events(monkeypatch):
    # ★fix_spans 이벤트는 events 에만 — gate_rounds 에 스팬수리 이벤트가 새어들지 않음(중복 금지).
    monkeypatch.setattr(g, "rhythm_spans", lambda text, run_threshold=6: [{"kind": "ending_run"}])
    fixed = {"n": 0}

    def fix(harness, pid, chapter, text, spans):
        fixed["n"] += 1
        return True

    h = _harness(); pid, _ = h.create(); h.step(pid)
    rec = _gate_committed(h, pid, 1, _seq_judge(["PASS"]), fix_fn=fix)
    assert fixed["n"] >= 1
    # fix_spans 는 events 에 flag 로 남고
    assert any(e["guard"] == "fix_spans" for e in rec["events"])
    # gate_rounds 원소는 정확히 6개 심사 키만(이벤트/스팬 키 유입 없음)
    assert all(set(r.keys()) == _ROUND_KEYS for r in rec["gate_rounds"])


def test_gate_rounds_absent_for_escalated_chapter_line():
    # ESCALATED(게이트 콜 없음) 회차의 로그 라인은 gate_rounds=[](빈 배열) — 심사 없었음을 정직 표기.
    h = _harness({"escalate_at": [1]})
    b = g.CallBudget(100)
    out = _tmp()
    g.run_gated_arm(harness=h, arm_no=1, target_chapters=1, judge_fn=_seq_judge(["PASS"]),
                    fix_fn=None, budget=b, log_path=out / "gate.jsonl",
                    max_retries=2, fail_abort_streak=3)
    rows = _log_rows(out)
    # ESCALATED 라인이 있으면 gate_rounds 는 빈 배열(콜 0 → 라운드 0)
    for r in rows:
        if r.get("status") == "ESCALATED":
            assert r.get("gate_rounds") == []


def test_resume_point_unaffected_by_gate_rounds():
    # ★resume 회귀: gate_rounds 가 실려도 resume_point 는 톱레벨 verdict/fail_exhausted/chapter 만 본다.
    h, b, arm, out = _arm(verdicts=["FAIL", "FAIL", "PASS",   # ch1 회복 PASS
                                    "PASS",                    # ch2
                                    "FAIL"],                   # ch3 소진 전진
                          chapters=3, max_retries=2, fail_streak=99)
    log = out / "gate.jsonl"
    # 실제 실행 로그(gate_rounds 포함)에서 재개점이 정확히 3(소진 전진도 완료로 침)
    assert g.resume_point(log) == 3
    # 라인에 gate_rounds 가 실제로 있어도 resume 산출은 불변
    rows = _log_rows(out)
    assert all("gate_rounds" in r for r in rows)


def test_fix_fn_skips_stale_span_and_continues(monkeypatch):
    """스팬별 격리 — 순차 퇴고 중 span_not_found(선행 퇴고로 매칭 소실)는 그 스팬만 건너뛰고 전진."""
    calls = []

    def fake_rewrite(gen, onto, chk, ch, text, sp, run_threshold=6, service=None, mode="v2"):
        calls.append(sp["i"])
        if sp["i"] == 1:
            raise ValueError("span_not_found: 대상 구절이 본문에 없습니다")
        return {"changed": True}

    import tools.st11_span_rewrite as st11
    monkeypatch.setattr(st11, "rewrite_span_via_chassis", fake_rewrite)

    class _Sess:
        class bundle:
            generator = ontology = checker = None
    class _Svc:
        class sessions:
            @staticmethod
            def get_or_create(state): return _Sess()
    class _H:
        def load(self, pid): return object()

    b = g._LiveGatedBundle.__new__(g._LiveGatedBundle)
    class _BH:
        svc = _Svc()
    b.harness = _BH()
    fix = b.fix_fn()
    spans = [{"i": 0}, {"i": 1}, {"i": 2}]
    ok = fix(_H(), "pid", 12, "본문", spans)
    assert calls == [0, 1, 2]      # 1번 실패에도 2번까지 전진
    assert ok is True              # 성공분(0·2)의 changed 반영


def test_fix_fn_reraises_other_valueerror(monkeypatch):
    def fake_rewrite(*a, **k):
        raise ValueError("완전히 다른 오류")
    import tools.st11_span_rewrite as st11
    monkeypatch.setattr(st11, "rewrite_span_via_chassis", fake_rewrite)

    class _Sess:
        class bundle:
            generator = ontology = checker = None
    class _Svc:
        class sessions:
            @staticmethod
            def get_or_create(state): return _Sess()
    class _H:
        def load(self, pid): return object()
    b = g._LiveGatedBundle.__new__(g._LiveGatedBundle)
    class _BH:
        svc = _Svc()
    b.harness = _BH()
    fix = b.fix_fn()
    import pytest as _pt
    with _pt.raises(ValueError):
        fix(_H(), "pid", 12, "본문", [{"i": 0}])
