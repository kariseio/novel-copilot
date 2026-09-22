# -*- coding: utf-8 -*-
"""B-37 검증 — 재탕 후보 풀 결정론 제외(사건 메뉴 필터 + 훅 화이트리스트 순환) (LLM 0콜).

설계(design-b37-dp3.md §B-37): 재탕은 모델이 '이미 쓴 재료'를 후보로 다시 봐서다. B-32e 가 주입식을
반증했으므로 유일 정공 = 후보에서 코드로 빼서 안 보이게. 이 테스트는(양방향):
  ⓐ 사건 메뉴 필터:
     · 실측 재발주형(최근 실현 사건과 술어까지 겹침)이 후보에서 제거되는가
     · 보존 목록(미실현 required·climax·만기약속·payoffs)은 재탕처럼 보여도 **절대 제거되지 않는가**(불가침 — DP-13 HIGH)
     · 같은 장소·인물 재방문(명사만 공유)은 오제거되지 않는가(과차단 가드ⓒ — DP-13 min_shared)
     · 과차단(전부 제거)되면 원본 복원되는가(never-empty)
     · recent_key_events 미전달(None) → no-op(하위호환)
  ⓑ 훅 화이트리스트 순환:
     · 최근 사용 유형이 빠진 축소 리스트를 주는가(순환)
     · 전 유형 소진 시 전체 복원되는가(never-empty)
     · 미전달(None) → 전체(하위호환)
     · 대소문자·공백 정규화
  통합: generate_event_menu(재탕 후보 제거·보존·never-empty)·beat_for_episode(프롬프트 훅 목록 축소·바이트 동일)
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_b37_rehash_filter.py
"""
from __future__ import annotations
import sys
import json

from novelcopilot.engine.menu_filter import (event_menu_filter, hook_whitelist,
                                              HOOK_WHITELIST, _is_rehash)
from novelcopilot.engine.plan_lint import _stem_set, _DUP_THRESHOLD, _MIN_SHARED
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


# DP-4b 5화 실측 재발주(①계산 ↔ ⑤정리) — 최근 실현 사건과 후보가 술어까지 겹치는 재탕
_REAL = "잔고 이만 팔천 원을 확인하고 벌금 오십만 원 미납과 월세 이십팔만 원을 합산해 구십삼만 원을 못 박는다"
_REHASH = "고시원 월세 이십팔만 원과 벌금 오십만 원을 정리해 최소 구십삼만 원 이상이 목표 자금임을 확정한다"
_FRESH = "도현이 브로커 박씨를 접선해 마정석 조각을 흥정하고 시세를 파악한다"


# ---------- ⓐ-1 실측 재발주 후보 제거 ----------
def test_rehash_candidate_removed() -> None:
    out = event_menu_filter([_REHASH, _FRESH], [_REAL])
    ok = (out == [_FRESH])   # 재탕은 빠지고 신선 후보만 유지
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 재탕 후보 제거(신선 후보 유지): kept={len(out)}")
    assert ok


# ---------- ⓐ-2 보존 목록 불가침(DP-13 HIGH: 정상 실현 재료를 죽이지 마라) ----------
def test_preserve_list_inviolable() -> None:
    # 후보 자체가 최근 실현과 재탕이어도, 그것이 '미실현 required·climax·만기약속·payoffs'면 절대 제거 금지.
    #   여기서 _REHASH 를 보존 목록으로 넘기면(예: 아직 미실현 required) 재탕 매칭에도 살아남아야 한다.
    out = event_menu_filter([_REHASH, _FRESH], [_REAL], preserve=[_REHASH])
    ok = (_REHASH in out and _FRESH in out and len(out) == 2)
    # 어간 동일성 대조라 조사 변이도 흡수(원문 완전일치 의존 금지) — 보존 항목의 조사형이 달라도 지켜지는가
    preserve_josa = "고시원 월세를 이십팔만 원과 벌금이 오십만 원을 정리해 최소 구십삼만 원 이상이 목표 자금임이 확정된다"
    out2 = event_menu_filter([_REHASH], [_REAL], preserve=[preserve_josa])
    ok &= (_REHASH in out2)   # 어간 집합 동일 → 보존
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 보존 목록 불가침(재탕 매칭에도 미실현 required/climax/약속 생존·조사변이 흡수)")
    assert ok


# ---------- ⓐ-3 과차단 가드ⓒ: 같은 장소·인물 재방문은 오제거 금지 ----------
def test_place_revisit_not_removed() -> None:
    # 인물·장소 명사만 공유(동작은 별개) — DP-13 실측 공유 어간 ≤2 < _MIN_SHARED 라 재탕 아님(정당 재방문 보존)
    realized = ["주인공이 길드 접수처에서 등록 절차를 밟고 신분증을 발급받는다"]
    cand = ["주인공이 길드 훈련장에서 검술 스승과 대련하며 새 초식을 배운다"]
    out = event_menu_filter(cand, realized)
    ok = (out == cand)   # 오제거 0
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ 과차단 가드: 장소·인물 재방문(명사만 공유) 보존")
    assert ok


def test_shared_name_only_not_removed() -> None:
    # 인물명 1개만 공유하는 별개 사건 — 우연 공유 오탐 차단(min_shared)
    realized = ["이하린이 옥상에서 주인공에게 정체를 밝힌다"]
    cand = ["이하린이 도서관에서 금서를 몰래 빌린다"]
    ok = (event_menu_filter(cand, realized) == cand)
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ 인물명 1개만 공유하는 별개 사건 보존(min_shared)")
    assert ok


# ---------- ⓐ-4 과차단 폴백(never-empty) ----------
def test_overblock_fallback_restores_original() -> None:
    # 모든 후보가 재탕이고 보존 목록에도 없으면 → 전부 제거 → 원본 복원(never-empty 불변식)
    out = event_menu_filter([_REHASH], [_REAL])   # 유일 후보가 재탕, preserve 없음
    ok = (out == [_REHASH])
    # 여러 후보 전부 재탕이어도 원본 그대로
    r2 = "잔고를 확인하고 벌금 미납과 월세를 합산해 목표 자금을 못 박는다"
    out2 = event_menu_filter([_REHASH, r2], [_REAL])
    ok &= (out2 == [_REHASH, r2])
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 과차단 폴백: 전부 재탕 → 원본 복원(never-empty)")
    assert ok


# ---------- ⓐ-5 하위호환 no-op ----------
def test_none_recent_is_noop() -> None:
    menu = [_REHASH, _FRESH]
    ok = (event_menu_filter(menu, None) == menu
          and event_menu_filter(menu, []) == menu
          and event_menu_filter([], [_REAL]) == []
          and event_menu_filter(None, [_REAL]) == [])
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ no-op: recent 미전달/빈 입력 안전(원본·하위호환)")
    assert ok


# ---------- ⓐ-6 캘리브레이션 경계(양방향 — DP-13 재사용 확증) ----------
def test_calibration_boundary_bidirectional() -> None:
    # _is_rehash 가 DP-13과 동일 임계(containment≥0.4 AND 공유≥3)를 쓰는지 어간 태그로 직접 고정.
    a = _stem_set("가나 다라 마바 사아 자차")      # 어간 5개
    b2 = _stem_set("가나 다라 하카 카타 파하")     # 겹침 2 → ratio 0.4, 공유 2 < min_shared → 재탕 아님
    b3 = _stem_set("가나 다라 마바 카타 파하")     # 겹침 3 → ratio 0.6, 공유 3 ≥ min_shared → 재탕
    two_not = (_is_rehash(a, [b2]) is False)    # 공유 2 → 통과(과차단 방지)
    three_yes = (_is_rehash(a, [b3]) is True)   # 공유 3 → 재탕
    ok = (two_not and three_yes and _DUP_THRESHOLD == 0.4 and _MIN_SHARED == 3)
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 경계: 공유2 통과·공유3 재탕 (threshold={_DUP_THRESHOLD}, min_shared={_MIN_SHARED})")
    assert ok


def test_short_tag_candidate_not_removed() -> None:
    # 내용어 어간 2개 미만 짧은 후보('전개' 류)는 판정 근거 부족 → 제거 안 함(오탐 소스 차단)
    ok = (event_menu_filter(["전개"], [_REAL]) == ["전개"]
          and event_menu_filter(["이동"], ["이동"]) == ["이동"])
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 짧은 태그 후보 보존(<2 내용어 검사 제외)")
    assert ok


# ---------- ⓑ-1 훅 화이트리스트 순환 ----------
def test_hook_whitelist_rotation() -> None:
    used = ["reveal", "reveal", "question"]
    got = hook_whitelist(used)
    expect = [h for h in HOOK_WHITELIST if h not in ("reveal", "question")]
    ok = (got == expect and "reveal" not in got and "question" not in got and "action" in got)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 순환: 최근 사용 유형 제외한 축소 리스트 제시")
    assert ok


def test_hook_whitelist_exhausted_restores_full() -> None:
    # 최근 N화가 전 유형을 다 써서 후보가 비면 → 전체 복원(never-empty)
    got = hook_whitelist(list(HOOK_WHITELIST))
    ok = (got == list(HOOK_WHITELIST))
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 전 유형 소진 시 전체 복원(never-empty)")
    assert ok


def test_hook_whitelist_none_full_and_normalized() -> None:
    ok = (hook_whitelist(None) == list(HOOK_WHITELIST)
          and hook_whitelist([]) == list(HOOK_WHITELIST)
          and hook_whitelist(["Reveal", " QUESTION "]) ==
              [h for h in HOOK_WHITELIST if h not in ("reveal", "question")]
          and hook_whitelist(["미지의훅"]) == list(HOOK_WHITELIST))   # 화이트리스트 밖 라벨 → 아무것도 안 뺌
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ None=전체·대소문자/공백 정규화·화이트리스트 밖 라벨 무시")
    assert ok


# ---------- 통합: ArcPlanner 경로 ----------
class _Cap(LLMProvider):
    """chat 을 캡처하고 지정 JSON 을 반환하는 Fake."""
    def __init__(self, payload: dict):
        super().__init__(); self.payload = payload; self.sys = ""
    def chat(self, msgs, **k):
        self.sys = msgs[0]["content"]
        return json.dumps(self.payload, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    w = WorldConfig(title="t", genre="g", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="a1", order=1, title="A1", goal="g")])
    return w


def _ep(**kw) -> Episode:
    base = dict(episode_id="e1", arc_id="a1", order=1, title="E1", premise="도입", climax="절정사건")
    base.update(kw); return Episode(**base)


def test_generate_event_menu_removes_rehash_keeps_required() -> None:
    # LLM 이 메뉴에 재탕 후보를 넣어도 코드가 제거. 단 required(맨앞 보존)는 재탕처럼 보여도 절대 안 빠진다.
    w = _world(); arc = w.spine.arcs[0]
    cap = _Cap({"event_menu": [_REHASH, _FRESH]})
    ep = _ep(required_events=[_REAL])   # required = 최근 실현과 유사하지만 required 는 보존 목록(불가침)
    menu = ArcPlanner(cap).generate_event_menu(w, arc, ep, ["직전"], recent_key_events=[_REAL])
    ok = (_REAL in menu and menu[0] == _REAL)   # required 맨앞 보존(never-empty 씨앗)
    ok &= (_REHASH not in menu)                 # LLM 메뉴의 재탕 후보는 제거
    ok &= (_FRESH in menu)                       # 신선 후보 유지
    # 하위호환: recent 미전달이면 재탕 후보도 그대로(no-op)
    cap2 = _Cap({"event_menu": [_REHASH, _FRESH]})
    menu2 = ArcPlanner(cap2).generate_event_menu(w, arc, _ep(required_events=[_REAL]), ["직전"])
    ok &= (_REHASH in menu2)
    print(f"[{'OK' if ok else 'FAIL'}] 통합: 메뉴 재탕 제거 + required 보존 + 미전달 no-op")
    assert ok


def test_generate_event_menu_never_empty_on_overblock() -> None:
    # LLM 메뉴가 전부 재탕이고 required/climax 도 전부 재탕형이면? seed(req+climax+...)로 never-empty 보장.
    w = _world(); arc = w.spine.arcs[0]
    cap = _Cap({"event_menu": [_REHASH]})       # 유일 메뉴 후보가 재탕
    ep = _ep(required_events=[], climax=_REAL)   # climax = 보존 목록(불가침)
    menu = ArcPlanner(cap).generate_event_menu(w, arc, ep, [], recent_key_events=[_REAL])
    ok = (len(menu) >= 1 and _REAL in menu)      # climax(보존) 살아남아 never-empty
    print(f"[{'OK' if ok else 'FAIL'}] 통합: 과차단 상황에서도 never-empty(보존 seed 생존)")
    assert ok


def test_beat_prompt_reflects_reduced_whitelist() -> None:
    w = _world(); arc = w.spine.arcs[0]; ep = _ep()
    payload = {"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"],
               "hook_type": "action", "chapter_function": "setup"}
    cap = _Cap(payload)
    ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전"], [],
                                     recent_hook_types=["reveal", "question", "reveal"])
    line = next(l for l in cap.sys.split(",") if "hook_type(회차말" in l)
    ok = ("reveal" not in line and "question" not in line and "action" in line)
    # 미전달 → 전체 목록(바이트 동일 하위호환)
    cap2 = _Cap(payload)
    ArcPlanner(cap2).beat_for_episode(w, arc, ep, 3, False, ["직전"], [])
    line2 = next(l for l in cap2.sys.split(",") if "hook_type(회차말" in l)
    ok &= all(t in line2 for t in HOOK_WHITELIST)
    print(f"[{'OK' if ok else 'FAIL'}] 통합: 비트 프롬프트 훅 목록 축소(최근 제외)·미전달 시 전체(하위호환)")
    assert ok


_TESTS = [
    test_rehash_candidate_removed, test_preserve_list_inviolable,
    test_place_revisit_not_removed, test_shared_name_only_not_removed,
    test_overblock_fallback_restores_original, test_none_recent_is_noop,
    test_calibration_boundary_bidirectional, test_short_tag_candidate_not_removed,
    test_hook_whitelist_rotation, test_hook_whitelist_exhausted_restores_full,
    test_hook_whitelist_none_full_and_normalized,
    test_generate_event_menu_removes_rehash_keeps_required,
    test_generate_event_menu_never_empty_on_overblock,
    test_beat_prompt_reflects_reduced_whitelist,
]

if __name__ == "__main__":
    results = []
    for t in _TESTS:
        try:
            t(); results.append(True)
        except AssertionError:
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
