# -*- coding: utf-8 -*-
"""G 검증 — ESCALATED 회복 안내(engine.recovery). hard Violation → 자연어 진단+레버. LLM 0콜."""
from __future__ import annotations
import sys
from novelcopilot.domain.types import Violation, SignalGrade
from novelcopilot.engine.recovery import recovery_hint, recovery_report, LEVERS

HARD_KINDS = ["edge_post_death", "post_death_change", "state_timeline", "edge_dangling",
              "edge_self_loop", "relation_contradiction", "ssot_ambiguous", "field_value",
              "worldrule(ai_control)", "uncertain(novel_thing)"]   # 마지막 둘=미매핑(폴백)


def _v(kind):
    return Violation(entity="레온", kind=kind, grade=SignalGrade.DETERMINISTIC,
                     canon="3화 시점 제거상태", text="현재 행동", evidence="제거 이후")


def test_all_kinds_render() -> bool:
    ok = True
    for k in HARD_KINDS:
        h = recovery_hint(_v(k))
        good = bool(h.get("diagnosis")) and bool(h.get("fix")) and "레온" in h["diagnosis"]
        lever_ok = set(h.get("levers", [])) <= LEVERS    # 레버는 알려진 집합만(오타·드리프트 가드)
        ok &= good and lever_ok
        if not (good and lever_ok):
            print(f"  [MISS] {k}: {h}")
    print(f"[{'OK' if ok else 'FAIL'}] 전 hard kind(미매핑 폴백 포함) 자연어 진단+레버 렌더")
    return ok


def test_dedup() -> bool:
    rep = recovery_report([_v("edge_post_death"), _v("edge_post_death"), _v("field_value")])
    ok = len(rep) == 2   # (kind,entity) 중복 제거
    print(f"[{'OK' if ok else 'FAIL'}] 중복(kind,entity) 1개로 — {len(rep)}건")
    return ok


if __name__ == "__main__":
    results = [test_all_kinds_render(), test_dedup()]
    print("\nG 회복 안내:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
