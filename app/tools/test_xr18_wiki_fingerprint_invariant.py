# -*- coding: utf-8 -*-
"""XR-18 검증 — 위키 반영-지문 소비 불변식 (LLM 0콜).

계약(cross-review/009 §8):
  · ingest 가 반영 회차의 본문 지문을 페이지에 기록(전 경로 단일 지점).
  · 생성 게이트: stale 표식이 없어도 지문 불일치만으로 위키 전체 격리(보수적 — 누적 합성물 부분 신뢰 불가).
  · mismatch 원본 회차·page_id 관측(emit·readiness 노출). 기록 없는 구 데이터=빈 목록(바이트 동일 하위호환).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr18_wiki_fingerprint_invariant.py
"""
from __future__ import annotations
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from types import SimpleNamespace as NS

from novelcopilot.domain.types import WikiPage, text_fingerprint, ChapterStatus
from novelcopilot.engine.readiness import wiki_fingerprint_mismatches
from novelcopilot.engine.wiki import Wiki
from novelcopilot.services.copilot import _WikiRetrievalDisabled, _wiki_for_generation

from test_xr7_stale_derivatives import _FakeProvider, _FakeOnt, _Ent


def _state(chapters):
    # XR-27: 자격 술어(FINALIZED+본문)가 소비 검증에 편입 — 픽스처도 정식 원천 자격으로 구성
    return NS(chapters=[NS(chapter=c, text=t, status=ChapterStatus.FINALIZED) for c, t in chapters])


def test_ingest_records_fingerprint() -> bool:
    w = Wiki(_FakeProvider())
    ont = _FakeOnt([_Ent("hero", "진우")])
    w.ingest_chapter(3, "진우가 문을 열었다.", ont, reviewed=True)
    ok = w.pages["hero"].source_fingerprints == {"3": text_fingerprint("진우가 문을 열었다.")}
    print(f"[{'OK' if ok else 'FAIL'}] ingest 지문 기록: 반영 회차→본문 지문(단일 지점)")
    assert ok
    return ok


def test_mismatch_detection_and_backcompat() -> bool:
    text1 = "진우가 문을 열었다."
    page = WikiPage(page_id="hero", page_type="character", body="카드",
                    source_fingerprints={"1": text_fingerprint(text1)})
    legacy = WikiPage(page_id="old", page_type="character", body="구 카드")   # 기록 없음
    ok = wiki_fingerprint_mismatches(_state([(1, text1)]), [page, legacy]) == []      # 일치+기록 없음=빈 목록
    mm = wiki_fingerprint_mismatches(_state([(1, "표식 없이 바뀐 본문.")]), [page, legacy])
    ok &= mm == [{"page_id": "hero", "chapter": 1, "reason": "fingerprint_mismatch"}]  # 본문 변경=적발+사유
    # XR-23(012 §5): 지문이 가리키는 회차 부재 = 검사 불가가 아니라 '고아 소스' — 격리 사유다.
    #   하위호환은 지문 자체가 빈 페이지(legacy)에만 적용.
    ok &= wiki_fingerprint_mismatches(_state([]), [page]) == \
        [{"page_id": "hero", "chapter": 1, "reason": "source_missing"}]
    ok &= wiki_fingerprint_mismatches(_state([]), [legacy]) == []
    # XR-27(015 §4): 회차는 있으나 정식 원천 자격(FINALIZED+본문)이 없으면 본문이 일치해도 격리 —
    #   자격 술어는 재구축 입력과 동일(단일 정의).
    esc = NS(chapter=1, text=text1, status=ChapterStatus.ESCALATED)
    ok &= wiki_fingerprint_mismatches(NS(chapters=[esc]), [page]) == \
        [{"page_id": "hero", "chapter": 1, "reason": "source_not_finalized"}]
    print(f"[{'OK' if ok else 'FAIL'}] 대조: 일치·기록없음=0 / 변경=적발 / 부재=source_missing / 비자격=source_not_finalized")
    assert ok
    return ok


def test_gate_isolates_on_mismatch_without_stale_mark() -> bool:
    text1 = "진우가 문을 열었다."
    w = Wiki(_FakeProvider())
    w.pages["hero"] = WikiPage(page_id="hero", page_type="character", body="카드",
                               source_fingerprints={"1": text_fingerprint(text1)})
    clean = _state([(1, text1)])
    tampered = _state([(1, "표식 없이 바뀐 본문.")])
    sel, chs, mm = _wiki_for_generation(w, [], state=clean)
    ok = sel is w and chs == [] and mm == []                          # 정합=원본(바이트 동일)
    sel2, chs2, mm2 = _wiki_for_generation(w, [], state=tampered)
    ok &= isinstance(sel2, _WikiRetrievalDisabled) and chs2 == []     # stale 표식 0 인데도 격리
    ok &= mm2 == [{"page_id": "hero", "chapter": 1, "reason": "fingerprint_mismatch"}]
    sel3, _c3, mm3 = _wiki_for_generation(w, [])                      # state 미제공(구 호출부)=검사 생략
    ok &= sel3 is w and mm3 == []
    print(f"[{'OK' if ok else 'FAIL'}] 게이트: 지문 불일치만으로 격리 · 정합/미제공=원본")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_ingest_records_fingerprint(), test_mismatch_detection_and_backcompat(),
               test_gate_isolates_on_mismatch_without_stale_mark()]
    print("\nXR-18 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
