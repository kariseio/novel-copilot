# -*- coding: utf-8 -*-
"""B-10 검증 — 교정 패스(_rewrite) floor-only 렌더: 미학 오버레이 미주입으로 재문체화 차단 (LLM 0콜).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_b10_floor_only.py
"""
from __future__ import annotations
import sys

from novelcopilot.engine.prompts import render_style, floor_only, FLOOR_CONSTRAINTS
from novelcopilot.domain.world import StyleSpec


def test_floor_excludes_aesthetic() -> bool:
    s = StyleSpec()
    s.author_style = "건조한 단문, 비유 최소, 하드보일드 어조"
    full = render_style(s)        # 메인 생성용(full 미학 + 작가 오버레이)
    floor = floor_only()          # 교정용(바닥 제약만)
    ok = (s.author_style in full)                              # 메인 생성엔 작가 오버레이 포함(무회귀)
    ok &= (s.author_style not in floor)                        # 교정 floor엔 오버레이 미포함(B-10 핵심)
    ok &= (FLOOR_CONSTRAINTS in floor) and ("재문체화" in floor)   # 바닥 제약 + 재문체화 금지 명시
    ok &= not any((r[:10] in floor) for r in s.rules if len(r) >= 10)   # 미학 기본규칙도 floor엔 미포함
    ok &= (len(floor) < len(full))
    print(f"[{'OK' if ok else 'FAIL'}] floor-only: 오버레이/미학규칙 미포함·바닥제약 포함·재문체화 금지(full {len(full)} vs floor {len(floor)})")
    return ok


def test_no_overlay_no_regression() -> bool:
    # 작가 오버레이 미설정이면 render_style 은 기존(기본 규칙)대로, floor 는 여전히 바닥만
    s = StyleSpec()
    full = render_style(s)
    ok = ("[웹소설 문체 규칙" in full) and ("작가 지정 문체" not in full)   # 오버레이 없으면 오버레이 블록 0
    ok &= (FLOOR_CONSTRAINTS in floor_only())
    print(f"[{'OK' if ok else 'FAIL'}] 무오버레이 무회귀: render_style 기본 규칙만·floor 정상")
    return ok


if __name__ == "__main__":
    results = [test_floor_excludes_aesthetic(), test_no_overlay_no_regression()]
    print("\nB-10 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
