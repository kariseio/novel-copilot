# -*- coding: utf-8 -*-
"""XR-19 검증 — 기계 binding 캐논 검토 도구 (LLM 0콜).

계약(cross-review/009 §9):
  · 리포트에 기계 binding '전량' 목록(출처 reason·증거 회차 포함 — 표본 상한과 별개).
  · 내면(non_binding 선언) 축 우선 표식(review_priority) + 미판정 잔량(totals).
  · 작가 판정 기록은 append-only·latest-wins 조인 — 값 자동 변경 0(무강제). 미존재 엔트리·잘못된 판정=400.
  · 구 JSON(tier_review 부재) 무변경 로드.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr19_tier_review.py
"""
from __future__ import annotations
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from novelcopilot.domain.project import ProjectSeed, ProjectState
from novelcopilot.domain.world import WorldConfig, AttributeSpec, TimelineEntry
from novelcopilot.engine.ontology_ops import machine_binding_report

from test_xr7_stale_derivatives import _ch, _svc


def _state_with_timeline() -> ProjectState:
    w = WorldConfig(title="t", genre="g",
                    attributes=[AttributeSpec(key="truth", label="자각", kind="categorical",
                                              vocab=["외면", "인정"], mutable=True, auto_commit="non_binding"),
                                AttributeSpec(key="money", label="소지금", kind="numeric", mutable=True)])
    st = ProjectState(id="p1", seed=ProjectSeed(title="t"), world=w)
    st.runtime_timeline = [
        TimelineEntry(entity_id="hero", attr="truth", value="흠칫", eff_from=2, reason="2화 동적 감지"),
        TimelineEntry(entity_id="hero", attr="truth", value="의심", eff_from=4, reason="4화 동적 감지"),
        TimelineEntry(entity_id="hero", attr="money", value=3000, eff_from=1, reason="1화 동적 감지"),
    ]
    return st


def test_report_full_entries_and_priority() -> bool:
    st = _state_with_timeline()
    rep = machine_binding_report(st)
    rows = {r["attr"]: r for r in rep["attributes"]}
    ok = len(rows["truth"]["entries"]) == 2 and len(rows["money"]["entries"]) == 1   # 전량(표본 상한 무관)
    e0 = rows["truth"]["entries"][0]
    ok &= e0["eff_from"] == 2 and "동적 감지" in e0["reason"] and e0["review"] == ""  # 출처·증거 회차·미판정
    ok &= rows["truth"]["review_priority"] is True and rows["money"]["review_priority"] is False   # 내면 우선
    ok &= rep["totals"]["review_priority_machine_binding"] == 2
    ok &= rep["totals"]["review_pending"] == 3
    print(f"[{'OK' if ok else 'FAIL'}] 리포트: 전량 목록·출처·내면 우선 표식·미판정 잔량")
    assert ok
    return ok


def test_review_join_latest_wins() -> bool:
    st = _state_with_timeline()
    st.tier_review = [{"entity_id": "hero", "attr": "truth", "eff_from": 2, "decision": "hold", "at": 5},
                      {"entity_id": "hero", "attr": "truth", "eff_from": 2, "decision": "approve", "at": 6}]
    rep = machine_binding_report(st)
    truth = next(r for r in rep["attributes"] if r["attr"] == "truth")
    ok = truth["entries"][0]["review"] == "approve"                   # append 원장·latest-wins 읽기
    ok &= truth["review_pending"] == 1 and rep["totals"]["review_pending"] == 2
    print(f"[{'OK' if ok else 'FAIL'}] 판정 조인: latest-wins·잔량 차감")
    assert ok
    return ok


def test_record_service_validation_and_no_mutation() -> bool:
    ch = _ch(chapter=1, text="본문.")
    svc, sess, _prov = _svc([ch])
    st = svc.repo.get("p1")
    st.runtime_timeline = [TimelineEntry(entity_id="hero", attr="truth", value="흠칫",
                                         eff_from=2, reason="2화 동적 감지")]
    svc.repo.save(st)
    res = svc.record_tier_review("p1", "hero", "truth", 2, "dismiss", note="능청 대사 오독")
    ok = res["recorded"] and res["decision"] == "dismiss"
    st2 = svc.repo.get("p1")
    ok &= len(st2.tier_review) == 1 and st2.tier_review[0]["note"] == "능청 대사 오독"
    ok &= st2.runtime_timeline[0].value == "흠칫"                      # 값 자동 변경 0(무강제 — 기록만)
    for bad in [("hero", "truth", 99, "dismiss"), ("hero", "없는속성", 2, "dismiss"),
                ("hero", "truth", 2, "이상한판정")]:
        try:
            svc.record_tier_review("p1", *bad)
            ok = False
        except ValueError:
            pass
    print(f"[{'OK' if ok else 'FAIL'}] 기록: 영속·값 불변·미존재/잘못된 판정 400")
    assert ok
    return ok


def test_target_identity_and_clear() -> bool:
    """XR-25(012 §7): 판정 대상은 '기계 binding' 뿐 — 작가 확정·비구속 엔트리는 400(고아 원장 차단).
    clear = 판정 취소(append·latest-wins 로 미판정 복귀)."""
    ch = _ch(chapter=1, text="본문.")
    svc, sess, _prov = _svc([ch])
    st = svc.repo.get("p1")
    st.runtime_timeline = [
        TimelineEntry(entity_id="hero", attr="truth", value="흠칫", eff_from=2, reason="2화 동적 감지"),
        TimelineEntry(entity_id="hero", attr="truth", value="의심", eff_from=3, reason="작가 확정",
                      provenance=["author"]),
        TimelineEntry(entity_id="hero", attr="mood", value="불안", eff_from=2, reason="2화 동적 감지",
                      trust_tier="narrative_inferred"),
    ]
    svc.repo.save(st)
    ok = svc.record_tier_review("p1", "hero", "truth", 2, "approve")["recorded"]   # 기계 gt=허용
    for bad in [("hero", "truth", 3, "approve"),    # 작가 확정 → 400
                ("hero", "mood", 2, "approve")]:    # 비구속 → 400
        try:
            svc.record_tier_review("p1", *bad)
            ok = False
        except ValueError as e:
            ok &= "검토 대상이 아닙니다" in str(e)
    res = svc.record_tier_review("p1", "hero", "truth", 2, "clear")               # 판정 취소
    ok &= res["decision"] == ""
    st2 = svc.repo.get("p1")
    from novelcopilot.engine.ontology_ops import machine_binding_report
    truth = next(r for r in machine_binding_report(st2)["attributes"] if r["attr"] == "truth")
    ok &= truth["entries"][0]["review"] == ""                                      # latest-wins → 미판정 복귀
    print(f"[{'OK' if ok else 'FAIL'}] XR-25: 기계 binding 만 허용·작가/비구속 400·clear 복귀")
    assert ok
    return ok


def test_old_json_backcompat() -> bool:
    st = ProjectState.model_validate({"id": "x", "seed": {"title": "t"}, "world": {"title": "t", "genre": "g"}})
    ok = st.tier_review == []                                          # 구 JSON 기본값 로드
    ok &= "tier_review" not in st.model_dump(exclude_defaults=True)    # 미사용 시 직렬화 미출현
    print(f"[{'OK' if ok else 'FAIL'}] 하위호환: tier_review 부재 로드·직렬화 미출현")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_report_full_entries_and_priority(), test_review_join_latest_wins(),
               test_record_service_validation_and_no_mutation(), test_target_identity_and_clear(),
               test_old_json_backcompat()]
    print("\nXR-19 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
