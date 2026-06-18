# -*- coding: utf-8 -*-
"""T3 검증 — 적시 사건 메뉴(event_menu) 생성·소비·폴백·코드강제·영속 (LLM 0콜).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_t3_event_menu.py
"""
from __future__ import annotations
import sys
import json

from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


class FakeEmpty(LLMProvider):       # chat='' → chat_json ValueError (LLM 실패 경로 — 활성 가드가 타는 그 경로)
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


class FakeMenu(LLMProvider):        # 메뉴 반환하되 '필수 사건'은 일부러 누락(코드 prepend 강제 검증)
    def __init__(self, menu): super().__init__(); self._menu = menu
    def chat(self, *a, **k): return json.dumps({"event_menu": self._menu}, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    w = WorldConfig(title="t", genre="아카데미", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="a1", order=1, title="A1", goal="g")])
    return w


def _ep(**kw) -> Episode:
    base = dict(episode_id="e1", arc_id="a1", order=1, title="E1", premise="도입", climax="절정사건")
    base.update(kw)
    return Episode(**base)


def test_fallback_never_throws_never_empty() -> bool:
    w = _world(); arc = w.spine.arcs[0]
    p = ArcPlanner(FakeEmpty())
    ep = _ep(required_events=["입학 시험", "결투 신청"], payoffs=["떡밥 회수"])
    menu = p.generate_event_menu(w, arc, ep, ["직전 줄거리"], outstanding=["미회수 약속X"])
    ok = bool(menu) and menu[0] == "입학 시험" and menu[1] == "결투 신청"     # 필수가 맨 앞(코드 강제)
    ok &= ("절정사건" in menu) and ("미회수 약속X" in menu)                  # 폴백 재료(climax·outstanding)
    ep2 = _ep(required_events=[], climax="", premise="유일한 도입")          # 전부 빈 → premise 로라도 never empty
    ok &= (p.generate_event_menu(w, arc, ep2, []) == ["유일한 도입"])
    print(f"[{'OK' if ok else 'FAIL'}] 폴백: never throws·never empty·required 맨앞·climax/약속 포함")
    return ok


def test_required_prepended_even_when_llm_omits() -> bool:
    # GAP-4(T2 역설): LLM 이 메뉴에서 required 를 빠뜨려도 코드가 무조건 앞에 보존
    w = _world(); arc = w.spine.arcs[0]
    p = ArcPlanner(FakeMenu(["신선사건1", "신선사건2", "신선사건3"]))        # required 없음
    menu = p.generate_event_menu(w, arc, _ep(required_events=["필수A", "필수B"]), [])
    ok = (menu[:2] == ["필수A", "필수B"]) and ("신선사건1" in menu)         # 코드 prepend + LLM 보강
    print(f"[{'OK' if ok else 'FAIL'}] 코드 강제: LLM 이 required 누락해도 맨앞 보존(T2 역설 차단)")
    return ok


def test_beat_fallback_merges_menu() -> bool:
    # beat_for_episode 가 LLM 실패(Fake='') 시 required+menu 병합(빈약 회차 방지)·dedup·required 우선
    w = _world(); arc = w.spine.arcs[0]
    beat = ArcPlanner(FakeEmpty()).beat_for_episode(
        w, arc, _ep(required_events=["필수A"]), 1, False, ["직전"], [], event_menu=["메뉴1", "메뉴2", "필수A"])
    ok = (beat.key_events[0] == "필수A") and ("메뉴1" in beat.key_events) and (beat.key_events.count("필수A") == 1)
    print(f"[{'OK' if ok else 'FAIL'}] 비트 폴백: required+메뉴 병합·dedup·required 우선")
    return ok


def test_persistence_roundtrip() -> bool:
    w = _world()
    w.spine.arcs[0].episodes.append(_ep(event_menu=["사건1", "사건2"]))
    sp2 = NarrativeSpine.model_validate_json(w.spine.model_dump_json())
    ok = sp2.arcs[0].episodes[0].event_menu == ["사건1", "사건2"]
    old = json.loads(w.spine.model_dump_json())            # 구 JSON(event_menu 키 없음) 무마이그레이션
    del old["arcs"][0]["episodes"][0]["event_menu"]
    sp3 = NarrativeSpine.model_validate_json(json.dumps(old, ensure_ascii=False))
    ok &= (sp3.arcs[0].episodes[0].event_menu == [])
    print(f"[{'OK' if ok else 'FAIL'}] 영속: event_menu round-trip + 구 JSON 무마이그레이션")
    return ok


if __name__ == "__main__":
    results = [test_fallback_never_throws_never_empty(), test_required_prepended_even_when_llm_omits(),
               test_beat_fallback_merges_menu(), test_persistence_roundtrip()]
    print("\nT3 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
