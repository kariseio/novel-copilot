# -*- coding: utf-8 -*-
"""XR-2 검증 — plant_reminder OFF 시 outstanding 의 이벤트 메뉴 유입 차단 (LLM 0콜).

계약(cross-review/005 §2.2 · BACKLOG XR-2):
- OFF 는 문자열(plant_notes)만이 아니라 데이터 인자(outstanding→메뉴 due·seed)도 끈다.
- ON(reminder/active) 경로는 무변경(outstanding 그대로).
- 골든 대조: OFF 경로 메뉴 프롬프트·seed 에 만기 약속 부재 / ON 경로 종전 동일.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr2_plant_off_menu_gate.py
"""
from __future__ import annotations
import sys
import json

from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.services.copilot import _plants_for_menu, _outstanding_plants
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


class CaptureMenu(LLMProvider):
    """메뉴 JSON 을 돌려주며 마지막 프롬프트를 캡처(골든 대조용)."""
    def __init__(self):
        super().__init__()
        self.last_messages = None

    def chat(self, messages, **k):
        self.last_messages = messages
        return json.dumps({"event_menu": ["신선사건1"]}, ensure_ascii=False)

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _spine_with_unpaid_plant() -> NarrativeSpine:
    done = Episode(episode_id="e0", arc_id="a1", order=1, title="E0", premise="도입", climax="절정0",
                   plants=["미회수 복선A"], done=True)
    cur = Episode(episode_id="e1", arc_id="a1", order=2, title="E1", premise="도입", climax="절정사건")
    return NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                          arcs=[Arc(arc_id="a1", order=1, title="A1", goal="g", episodes=[done, cur])])


def test_gate_off_blocks_data_on_keeps() -> bool:
    spine = _spine_with_unpaid_plant()
    ok = _outstanding_plants(spine) == ["미회수 복선A"]           # 산정 자체는 불변(가시화 경보 채널 재료)
    ok &= _plants_for_menu(spine, "off") == []                    # OFF=데이터 게이트(주석 계약 일치)
    ok &= _plants_for_menu(spine, "gentle") == ["미회수 복선A"]   # ON 무변경
    ok &= _plants_for_menu(spine, "active") == ["미회수 복선A"]
    print(f"[{'OK' if ok else 'FAIL'}] 게이트: off=[] · gentle/active=종전 동일 · 산정 함수 불변")
    assert ok
    return ok


def test_menu_prompt_and_seed_golden() -> bool:
    w = WorldConfig(title="t", genre="g", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = _spine_with_unpaid_plant()
    arc = w.spine.arcs[0]
    ep = arc.episodes[1]

    p_on = ArcPlanner(CaptureMenu())
    menu_on = p_on.generate_event_menu(w, arc, ep, ["직전 줄거리"], outstanding=["미회수 복선A"])
    usr_on = p_on.provider.last_messages[1]["content"]
    ok = "미회수 복선A" in usr_on and "미회수 복선A" in menu_on    # ON: due 가 프롬프트·seed 에 실림(종전 계약)

    p_off = ArcPlanner(CaptureMenu())
    menu_off = p_off.generate_event_menu(w, arc, ep, ["직전 줄거리"], outstanding=[])
    usr_off = p_off.provider.last_messages[1]["content"]
    ok &= "미회수 복선A" not in usr_off and "미회수 복선A" not in menu_off   # OFF: 만기 약속 부재(골든)
    ok &= "신선사건1" in menu_off and ep.climax in menu_off        # never-empty·여타 seed 재료 불변
    print(f"[{'OK' if ok else 'FAIL'}] 골든: OFF 프롬프트·seed 만기 부재 / ON 종전 동일 / never-empty 유지")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_gate_off_blocks_data_on_keeps(), test_menu_prompt_and_seed_golden()]
    print("\nXR-2 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
