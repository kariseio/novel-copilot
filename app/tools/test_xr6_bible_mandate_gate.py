# -*- coding: utf-8 -*-
"""XR-6⒜ 검증 — 세계 설정 구현 명령의 작가 확정(✓) 한정 (LLM 0콜).

계약(cross-review/005 §2.4 · prompt-auditor 조건부 통과 4조건):
  ⑴ 구현 명령은 ✓ 항목이 실주입된 화에만 · 장면 목표 조건 문형 · 명사 메뉴(경제·무기…) 삭제
  ⑵ digest 범례는 예산 컷 이후 실린 줄 기준(dangling legend 차단) · em dash 0 · 권위어 대신 기능어
  ⑶ 골든 프롬프트 2본(✓ 有/無) — ✓ 없는 경로엔 구현 문장 부재 + 대화 전진 문장 존재
  (⑷ lookup 상태 무필터는 후속 티켓 — 이 파일 범위 밖)
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr6_bible_mandate_gate.py
"""
from __future__ import annotations
import sys

from novelcopilot.domain.bible import StoryBible, BibleEntry
from novelcopilot.domain.types import ContextBoard, SceneSpec, RetrievedItem
from novelcopilot.domain.world import StyleSpec
from novelcopilot.engine.bible_compiler import bible_digest
from novelcopilot.engine.prompts import PromptAssembler


def _bible() -> StoryBible:
    return StoryBible(entries=[
        BibleEntry(entry_id="b1", category="setting", title="봉인 부적", prose="부적의 용법과 한계 서술",
                   provenance="author", status="author_approved"),
        BibleEntry(entry_id="b2", category="glossary", title="괴담관리국", prose="미검수 조직 설정 서술",
                   provenance="ai_worldgen", status="ai_unreviewed"),
    ])


def test_digest_marks_approved_only_and_legend() -> bool:
    items, dropped = bible_digest(_bible(), budget=1500)
    text = items[0].text
    ok = "[설정]✓ 봉인 부적:" in text                       # 승인 항목만 ✓ + 카테고리 한글 라벨(경미 7)
    ok &= "✓ 괴담관리국" not in text and "[용어집] 괴담관리국:" in text   # 미검수=무표시(부정 라벨 0)
    ok &= text.startswith("[세계관 설정집(✓=작가가 쓰기로 정한 항목)]")   # 범례(괄호형 — em dash 0)
    ok &= "—" not in text.split("\n")[0]                     # EM-1: 헤더에 대시 없음
    print(f"[{'OK' if ok else 'FAIL'}] digest: 승인만 ✓·미검수 무표시·범례 괄호형·대시 0")
    assert ok
    return ok


def test_digest_legend_follows_budget_cut() -> bool:
    """✓ 항목이 예산에서 잘리면 범례도 안 뜬다(dangling legend 차단 — 감사 조건 6)."""
    b = StoryBible(entries=[
        BibleEntry(entry_id="b2", category="glossary", title="긴미검수", prose="가" * 200,
                   provenance="ai_worldgen", status="ai_unreviewed"),
        BibleEntry(entry_id="b1", category="setting", title="승인항목", prose="나" * 200,
                   provenance="author", status="author_approved"),
    ])
    # context_hint 로 미검수 항목을 상위 정렬(제목 매칭 +2) → 예산 1줄이면 ✓ 항목이 잘린다
    items, dropped = bible_digest(b, budget=10, context_hint="긴미검수")
    text = items[0].text
    ok = dropped == 1 and "✓" not in text and text.startswith("[세계관 설정집]\n")
    print(f"[{'OK' if ok else 'FAIL'}] digest: ✓ 컷 화엔 범례 없음(실주입 기준 — dangling 차단)")
    assert ok
    return ok


def _assemble(narrative) -> tuple[str, str]:
    scene = SceneSpec(index=0, goal="장면 목표", key_events=["사건"])
    plain = PromptAssembler(StyleSpec(), 4000).assemble(
        ContextBoard(chapter=3, narrative=narrative), scene, "")
    xml = PromptAssembler(StyleSpec(structured_prompt=True), 4000).assemble(
        ContextBoard(chapter=3, narrative=narrative), scene, "")
    return plain, xml


def test_mandate_golden_with_and_without_mark() -> bool:
    marked = [RetrievedItem(source="bible", ref="digest",
                            text="[세계관 설정집(✓=작가가 쓰기로 정한 항목)]\n[설정]✓ 봉인 부적: 용법")]
    unmarked = [RetrievedItem(source="bible", ref="digest",
                              text="[세계관 설정집]\n[용어집] 괴담관리국: 미검수 서술")]
    p1, x1 = _assemble(marked)
    p0, x0 = _assemble(unmarked)
    ok = ("✓ 표시된 항목 중 이번 장면 목표에 걸리는 것이 있으면" in p1) and \
         ("✓ 표시된 항목 중 이번 장면 목표에 걸리는 것이 있으면" in x1)
    ok &= ("구현하라" not in p1) and ("최소 1회" not in p1)          # 무조건 쿼터·구 문형 소거
    ok &= ("경제·무기·기술" not in p1) and ("거래·전술·사물" not in p1)   # 명사 메뉴 삭제(헌법 1조)
    ok &= ("✓ 표시된" not in p0) and ("✓ 표시된" not in x0)          # ✓ 없는 화=구현 문장 부재(K4)
    ok &= ("대화는 매번 새 정보나 새 결정을 실어" in p0) and \
         ("대화는 매번 새 정보나 새 결정을 실어" in x0)               # 대화 전진 계약은 무조건 유지
    print(f"[{'OK' if ok else 'FAIL'}] 골든 2본: ✓有=조건 문형·메뉴 0 / ✓無=구현 문장 부재·대화 전진 유지")
    assert ok
    return ok


def test_mark_detection_is_marker_scoped() -> bool:
    """작가 프로즈 안의 ✓ 타이핑은 위양성 아님 — 검출은 "]✓ " 마커 한정(감사 경미 7)."""
    prose_check = [RetrievedItem(source="bible", ref="digest",
                                 text="[세계관 설정집]\n[용어집] 목록: 완료 표시는 ✓ 로 한다")]
    p, _ = _assemble(prose_check)
    other_src = [RetrievedItem(source="rag_chunk", ref="c1", text="[뭔가]✓ 제목: 본문")]
    p2, _ = _assemble(other_src)
    ok = ("✓ 표시된" not in p) and ("✓ 표시된" not in p2)   # bible 소스 + "]✓ " 마커일 때만 발화
    print(f"[{'OK' if ok else 'FAIL'}] 검출 정밀: 프로즈 내 ✓·비 bible 소스는 미발화")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_digest_marks_approved_only_and_legend(), test_digest_legend_follows_budget_cut(),
               test_mandate_golden_with_and_without_mark(), test_mark_detection_is_marker_scoped()]
    print("\nXR-6 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
