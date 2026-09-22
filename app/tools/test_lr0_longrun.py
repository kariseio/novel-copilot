# -*- coding: utf-8 -*-
"""LR-0 검증 — 롱런 러너 가드(①spine ②커서 정합 ③ESCALATED 연속)+--dry-run manifest (전부 LLM 0콜).

dry-run 은 CopilotService/provider 를 아예 생성하지 않는다(구조적 0콜) — 임시폴더 repo 에
실제 도메인 모델(ProjectState/NarrativeSpine/ChapterRecord)로 영속해 가드가 읽는 계약면을 실경로와 동일하게 유지.
실발사 게이트(--live)는 비TTY 차단·무플래그 차단을 검증한다(LiveHarness 미생성 확인 포함).
LR-1 마감 2건 추가 검증: run() confirm='FIRE' 2차 잠금(정확 일치·유사문자열 거부),
step 예외 1회 재시도(transient 흡수·재실패 arm 중단·무한 재시도 금지), CLI --fire/--tone 배선.
실행: cd app; python -m pytest tools/test_lr0_longrun.py -q  (스크립트: PYTHONPATH=. python tools/test_lr0_longrun.py)
"""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path

try:
    from tools import gen_longrun as lr
except ImportError:          # pytest 가 app/tools 를 sys.path prepend 한 경우
    import gen_longrun as lr

from novelcopilot.domain.project import ProjectSeed


def _seed(target: int = 8) -> ProjectSeed:
    return ProjectSeed(title="", genre="테스트 장르", target_chapters=target)


def test_dry_run_manifest_and_green() -> None:
    """④핵심: --dry-run 이 manifest JSON(작품id·화수·가드이벤트·경과·계측경로)을 기록하고 GREEN."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    m = lr.run(mode="dry-run", seed=_seed(), works=2, chapters=3, out_dir=tmp)
    p = Path(m["manifest_path"])
    disk = json.loads(p.read_text(encoding="utf-8"))
    ok = (p.exists() and p.parent == tmp
          and disk["ticket"] == "LR-0" and disk["mode"] == "dry-run"
          and disk["config"]["works"] == 2 and disk["config"]["chapters"] == 3
          and len(disk["arms"]) == 2
          and isinstance(disk["elapsed_sec"], (int, float))
          and disk["green"] is True)
    for a in disk["arms"]:
        lp = Path(a["log_path"])
        lines = ([json.loads(l) for l in lp.read_text(encoding="utf-8").splitlines() if l.strip()]
                 if lp.exists() else [])
        ok = (ok and a["project_id"] and not a["aborted"]
              and a["chapters_done"] == 3 and a["guard_events"] == []
              and lp.exists() and len(lines) == 3
              and all(l["status"] == "FINALIZED" and l["chars"] > 0 for l in lines))
    # dry-run 가드 셀프테스트(5건)가 manifest 에 영속되고 전건 pass
    st = disk["guard_selftest"]
    ok = ok and st is not None and st["pass"] is True and len(st["checks"]) == 5
    print(f"[{'OK' if ok else 'FAIL'}] dry-run manifest: 2 arms×3화 GREEN·계측로그·셀프테스트 pass")
    assert ok, "dry-run manifest 계약 위반(경로·arm 수·화수·가드이벤트·셀프테스트 중 하나)"


def test_guard1_spine_aborts_arm() -> None:
    """가드 ①: spine 위반(엔딩 공백 / 아크 0개) → arm 시작 즉시 중단+기록, step 0회."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    for fault in ({"no_ending": True}, {"no_arcs": True}):
        m = lr.run(mode="dry-run", seed=_seed(), works=1, chapters=4, out_dir=tmp,
                   harness_factory=lambda i, f=fault: lr.DryRunHarness(_seed(), faults=f),
                   selftest=False)
        a = m["arms"][0]
        ok = (a["aborted"] and a["abort_reason"].startswith("spine:")
              and a["attempts"] == 0 and a["chapters_done"] == 0
              and any(e["guard"] == "spine" and e["action"] == "abort" for e in a["guard_events"])
              and m["green"] is False)
        print(f"[{'OK' if ok else 'FAIL'}] 가드① spine {list(fault)[0]} → arm 중단(집필 0회)")
        assert ok, f"spine 가드 미발화 또는 중단 실패: {fault} → {a}"


def test_guard2_cursor_mismatch_aborts() -> None:
    """가드 ②: 회차 후 커서 불일치(current_chapter != len(chapters)) → arm 중단+플래그."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    m = lr.run(mode="dry-run", seed=_seed(), works=1, chapters=4, out_dir=tmp,
               harness_factory=lambda i: lr.DryRunHarness(_seed(), faults={"cursor_break_at": 2}),
               selftest=False)
    a = m["arms"][0]
    ev = [e for e in a["guard_events"] if e["guard"] == "cursor"]
    ok = (a["aborted"] and a["abort_reason"].startswith("cursor:")
          and a["attempts"] == 2                       # 2화에서 감지 → 3화 안 감(즉시 중단)
          and len(ev) == 1 and ev[0]["action"] == "abort"
          and m["green"] is False)
    print(f"[{'OK' if ok else 'FAIL'}] 가드② 커서 불일치 → 2화 직후 중단(이후 미집필)")
    assert ok, f"커서 가드 미발화 또는 중단 시점 오류: {a}"


def test_guard3_escalated_streak_flags_not_abort() -> None:
    """가드 ③: ESCALATED 연속 2회 → 플래그(중단 아님) — 루프 계속·GREEN 유지(advisory)."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    m = lr.run(mode="dry-run", seed=_seed(), works=1, chapters=5, out_dir=tmp,
               harness_factory=lambda i: lr.DryRunHarness(_seed(), faults={"escalate_at": [2, 3]}),
               selftest=False)
    a = m["arms"][0]
    flags = [e for e in a["guard_events"] if e["guard"] == "escalated_streak"]
    # 시도 5회 예산: 1=FIN(1화) 2=ESC 3=ESC(연속2→flag) 4=FIN(2화) 5=FIN(3화)
    ok = ((not a["aborted"]) and a["attempts"] == 5 and a["chapters_done"] == 3
          and len(flags) == 1 and flags[0]["action"] == "flag"
          and m["green"] is True)                      # 플래그는 advisory — GREEN 을 깨지 않음
    print(f"[{'OK' if ok else 'FAIL'}] 가드③ ESCALATED 연속2 → 플래그만(중단X·GREEN 유지)")
    assert ok, f"ESCALATED 스트릭 가드 오작동(중단했거나 플래그 누락): {a}"


def test_escalated_single_no_flag() -> None:
    """가드 ③ 경계: ESCALATED 1회(연속 아님)는 플래그 없음 — 스트릭이 FINALIZED 로 리셋."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    m = lr.run(mode="dry-run", seed=_seed(), works=1, chapters=4, out_dir=tmp,
               harness_factory=lambda i: lr.DryRunHarness(_seed(), faults={"escalate_at": [2]}),
               selftest=False)
    a = m["arms"][0]
    ok = ((not a["aborted"]) and a["guard_events"] == [] and a["chapters_done"] == 3
          and m["green"] is True)
    print(f"[{'OK' if ok else 'FAIL'}] 가드③ 경계: 단발 ESCALATED 무플래그(리셋 정상)")
    assert ok, f"단발 ESCALATED 에 과잉 플래그 또는 중단: {a}"


def test_launch_prevention_gates() -> None:
    """발사 방지: 무플래그=안내 종료(2)·양쪽 플래그=오류(2)·--live 비TTY=차단(3, LiveHarness 미생성)."""
    created = []
    orig_isatty, orig_live = lr._stdin_isatty, lr.LiveHarness

    class _Trap:                                   # 생성 자체가 실발사 준비 → 호출되면 실패
        def __init__(self, *a, **k):
            created.append(1)
    lr._stdin_isatty = lambda: False
    lr.LiveHarness = _Trap
    try:
        r_none = lr.main([])
        r_both = lr.main(["--dry-run", "--live"])
        r_live = lr.main(["--live", "--works", "1", "--chapters", "1"])
    finally:
        lr._stdin_isatty, lr.LiveHarness = orig_isatty, orig_live
    ok = (r_none == 2 and r_both == 2 and r_live == 3 and not created)
    print(f"[{'OK' if ok else 'FAIL'}] 발사 방지: 무플래그2/동시2/비TTY live 3·LiveHarness 미생성")
    assert ok, f"발사 게이트 위반 none={r_none} both={r_both} live={r_live} created={created}"


def test_main_dry_run_exit_green() -> None:
    """CLI 경로: main(--dry-run) 이 exit 0(GREEN) — out-dir 임시 재지정으로 reports 미오염."""
    tmp = tempfile.mkdtemp(prefix="lr0t_")
    rc = lr.main(["--dry-run", "--works", "1", "--chapters", "2", "--out-dir", tmp])
    manifests = list(Path(tmp).glob("longrun_*_manifest.json"))
    ok = rc == 0 and len(manifests) == 1
    print(f"[{'OK' if ok else 'FAIL'}] CLI dry-run: exit 0 + manifest 1건({tmp})")
    assert ok, f"main --dry-run 실패 rc={rc} manifests={len(manifests)}"


def test_run_live_requires_confirm_fire() -> None:
    """2차 잠금: run(mode='live') 는 confirm='FIRE' 정확 일치 없이는 PermissionError.
    유사 문자열('fire'/'LAUNCH')도 거부, LiveHarness 미생성(잠금이 하네스 생성보다 앞)."""
    created = []
    orig_live = lr.LiveHarness

    class _Trap:
        def __init__(self, *a, **k):
            created.append(1)
    lr.LiveHarness = _Trap
    rejected = []
    try:
        for bad in ("", "fire", "LAUNCH", "FIRE "):
            try:
                lr.run(mode="live", seed=_seed(), works=1, chapters=1)
                rejected.append(False)
            except PermissionError:
                rejected.append(True)
            if bad:                                # confirm 인자로도 재확인
                try:
                    lr.run(mode="live", seed=_seed(), works=1, chapters=1, confirm=bad)
                    rejected.append(False)
                except PermissionError:
                    rejected.append(True)
    finally:
        lr.LiveHarness = orig_live
    ok = all(rejected) and len(rejected) == 7 and not created
    print(f"[{'OK' if ok else 'FAIL'}] 2차 잠금: confirm 미지정/유사문자열 전건 거부·LiveHarness 미생성")
    assert ok, f"run() live 2차 잠금 누수 rejected={rejected} created={created}"


def test_run_live_confirm_fire_unlocks() -> None:
    """2차 잠금 해제면: confirm='FIRE' 정확 일치 시 live 모드 실행(주입 하네스로 LLM 0콜 검증).
    dry-run 은 confirm 무영향(기존 테스트들이 confirm 없이 통과하는 것으로 커버)."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    m = lr.run(mode="live", seed=_seed(), works=1, chapters=2, out_dir=tmp,
               harness_factory=lambda i: lr.DryRunHarness(_seed()), confirm="FIRE")
    a = m["arms"][0]
    ok = (m["mode"] == "live" and m["guard_selftest"] is None
          and (not a["aborted"]) and a["chapters_done"] == 2 and m["green"] is True)
    print(f"[{'OK' if ok else 'FAIL'}] 2차 잠금 해제: confirm='FIRE' → live 모드 arm 정상 수행")
    assert ok, f"confirm='FIRE' 해제 실패: {m['mode']} {a}"


def test_step_exception_retry_absorbs_transient() -> None:
    """가드 ⑤: step 예외 1회 → 재시도로 흡수(arm 계속·flag 기록·GREEN 유지)."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    h = lr.DryRunHarness(_seed(), faults={"raise_at": [2]})     # attempt2 만 transient 예외
    m = lr.run(mode="dry-run", seed=_seed(), works=1, chapters=3, out_dir=tmp,
               harness_factory=lambda i: h, selftest=False)
    a = m["arms"][0]
    flags = [e for e in a["guard_events"] if e["guard"] == "step_retry"]
    ok = ((not a["aborted"]) and a["chapters_done"] == 3
          and len(flags) == 1 and flags[0]["action"] == "flag"
          and h._attempt == 4                       # 3화 성공 + 실패 1콜 = 정확히 4콜(추가 재시도 없음)
          and m["green"] is True)
    print(f"[{'OK' if ok else 'FAIL'}] 가드⑤ transient 예외 → 1회 재시도 흡수(3화 완주·flag 1건)")
    assert ok, f"재시도 흡수 실패: {a} step콜={h._attempt}"


def test_step_exception_retry_fail_aborts_arm() -> None:
    """가드 ⑤: 재시도도 실패(연속 2회 예외) → arm 중단 + manifest 에 flag(재시도)+abort(기존 형식) 기록.
    step 콜 수로 무한 재시도 금지 증명(원시도 1 + 재시도 1 = 정확히 2콜 후 중단)."""
    tmp = Path(tempfile.mkdtemp(prefix="lr0t_"))
    h = lr.DryRunHarness(_seed(), faults={"raise_at": [2, 3]})  # attempt2·3 연속 예외
    m = lr.run(mode="dry-run", seed=_seed(), works=1, chapters=4, out_dir=tmp,
               harness_factory=lambda i: h, selftest=False)
    a = m["arms"][0]
    ok = (a["aborted"] and a["abort_reason"].startswith("exception:")
          and any(e["guard"] == "step_retry" and e["action"] == "flag" for e in a["guard_events"])
          and any(e["guard"] == "exception" and e["action"] == "abort" for e in a["guard_events"])
          and a["chapters_done"] == 1               # 1화 성공 후 2화 시도서 중단
          and h._attempt == 3                       # 1화 성공 + 원시도 + 재시도 = 정확히 3콜(무한 금지)
          and m["green"] is False)
    print(f"[{'OK' if ok else 'FAIL'}] 가드⑤ 재실패 → arm 중단+flag/abort 기록(step 정확 3콜)")
    assert ok, f"재실패 중단 계약 위반: {a} step콜={h._attempt}"


def test_cli_fire_gate_wiring() -> None:
    """CLI --fire 게이트: --fire 단독=오류(2)·--dry-run --fire=오류(2)·
    --live --fire=비TTY 라도 run(mode='live', confirm='FIRE') 위임(run 스텁으로 실발사 0)."""
    calls = []
    orig_run, orig_isatty, orig_live = lr.run, lr._stdin_isatty, lr.LiveHarness

    class _Trap:
        def __init__(self, *a, **k):
            raise AssertionError("stub run 경로에서 LiveHarness 생성 금지")

    def _stub_run(**kw):
        calls.append(kw)
        return {"mode": kw["mode"], "arms": [], "guard_selftest": None,
                "green": True, "elapsed_sec": 0.0, "manifest_path": "stub"}
    lr._stdin_isatty = lambda: False
    lr.LiveHarness = _Trap
    try:
        r_fire_only = lr.main(["--fire"])
        r_dry_fire = lr.main(["--dry-run", "--fire"])
        lr.run = _stub_run
        r_live_fire = lr.main(["--live", "--fire", "--works", "1", "--chapters", "1"])
    finally:
        lr.run, lr._stdin_isatty, lr.LiveHarness = orig_run, orig_isatty, orig_live
    ok = (r_fire_only == 2 and r_dry_fire == 2 and r_live_fire == 0
          and len(calls) == 1 and calls[0]["mode"] == "live" and calls[0]["confirm"] == "FIRE")
    print(f"[{'OK' if ok else 'FAIL'}] --fire 게이트: 단독/dry 조합 거부·--live --fire 는 confirm='FIRE' 위임")
    assert ok, f"--fire 배선 오류 rc=({r_fire_only},{r_dry_fire},{r_live_fire}) calls={calls}"


def test_cli_seed_fixing_tone_and_seed_idx() -> None:
    """시드 고정(LR-1 발사 커맨드용): --tone 신설 — 직접 시드 반영 + --seed-idx 시드에도 덮어쓰기,
    --target-chapters 오버라이드 유지."""
    import argparse
    ns = argparse.Namespace(seed_idx=None, title="", genre="로맨스 판타지", tone="설렘과 긴장",
                            premise="전제 고정", protagonist="영애", target_chapters=24, chapters=28)
    s1 = lr.build_seed(ns)
    ns2 = argparse.Namespace(seed_idx=1, title="", genre="", tone="아주 잔잔", premise="",
                             protagonist="", target_chapters=24, chapters=28)
    s2 = lr.build_seed(ns2)
    ok = (s1.genre == "로맨스 판타지" and s1.tone == "설렘과 긴장" and s1.premise == "전제 고정"
          and s1.target_chapters == 24
          and s2.genre == "학원물(잔잔한 일상)" and s2.tone == "아주 잔잔"
          and s2.target_chapters == 24 and s2.premise != "")
    print(f"[{'OK' if ok else 'FAIL'}] 시드 고정: --tone 직접/seed-idx 덮어쓰기·target 24 오버라이드")
    assert ok, f"build_seed 시드 고정 실패: s1={s1} s2={s2}"


def _run(fn) -> bool:
    """스크립트 모드 전용 — assert 실패를 잡아 전건 끝까지 보고(pytest 게이트는 assert strict)."""
    try:
        fn()
        return True
    except AssertionError:
        return False


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    results = [_run(t) for t in (test_dry_run_manifest_and_green,
                                 test_guard1_spine_aborts_arm,
                                 test_guard2_cursor_mismatch_aborts,
                                 test_guard3_escalated_streak_flags_not_abort,
                                 test_escalated_single_no_flag,
                                 test_launch_prevention_gates,
                                 test_main_dry_run_exit_green,
                                 test_run_live_requires_confirm_fire,
                                 test_run_live_confirm_fire_unlocks,
                                 test_step_exception_retry_absorbs_transient,
                                 test_step_exception_retry_fail_aborts_arm,
                                 test_cli_fire_gate_wiring,
                                 test_cli_seed_fixing_tone_and_seed_idx)]
    print("\nLR-0 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
