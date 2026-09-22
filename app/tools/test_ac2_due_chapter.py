# -*- coding: utf-8 -*-
"""AC-2ⓐ 만기 세터 회귀 — Promise.due_chapter 는 존재·소비(정렬·telemetry)되는데 세터가
프로덕션 0건이던 구멍(라이브 원장 97건 전건 None 실측). 만기 = payoff 예정 에피소드의
예산 화 구간(payoff_at 위치 보정) 결정론 파생. LLM 0.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from novelcopilot.domain.ledger import PromiseLedger
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.engine.ledger_ops import (sync_ledger_from_spine, ledger_telemetry,
                                            _episode_windows, _due_in_window)


def _spine():
    # ep1(1~3화)·ep2(4~5화): ep1 이 심고 ep2 가 회수 예정(payoff_at=mid → 4화) + 회수 미예정 plant 1
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="a", order=1, episodes=[
            Episode(episode_id="a_ep1", arc_id="a", order=1, target_chapters=3,
                    plants=["방울의 정체", "회수 미예정 떡밥"], payoffs=[]),
            Episode(episode_id="a_ep2", arc_id="a", order=2, target_chapters=2,
                    plants=[], payoffs=["방울의 정체"], payoff_at="mid"),
        ])])


def test_episode_windows_arithmetic():
    wins = _episode_windows(_spine())
    assert wins == {"a_ep1": (1, 3), "a_ep2": (4, 5)}
    assert _due_in_window((4, 5), "early") == 4
    assert _due_in_window((4, 5), "mid") == 4          # (4+5)//2
    assert _due_in_window((4, 5), "climax") == 5
    assert _due_in_window((4, 5), "") == 5             # 미지정 = 말미(절정 관행)


def test_due_derived_only_for_scheduled_payoffs():
    led = PromiseLedger()
    sync_ledger_from_spine(led, _spine(), current_chapter=1)
    by_text = {p.text: p for p in led.promises}
    scheduled = by_text["방울의 정체"]
    assert scheduled.due_chapter == 4 and scheduled.due_source == "spine_arith"
    # 기존 P1 계약(불변): payoffs 라벨 일치 = 즉시 paid — 만기는 상태 무관 회계 이력으로 남는다.
    #   (설계 갭 PM 회부: open+due 조합은 P1 정리 전까지 sync 경로에서 성립 불가)
    assert scheduled.status == "paid"
    # 회수 미예정 약속은 만기 발명 금지 — None 유지
    free = by_text["회수 미예정 떡밥"]
    assert free.due_chapter is None and free.due_source == "" and free.status == "open"
    tele = ledger_telemetry(led, current_chapter=2)
    assert tele["due_known"] == 0 and tele["nearest_due"] is None   # open 중 due 보유 0(현행 정직)


def test_author_due_is_preserved_and_arith_updates_on_replan():
    led = PromiseLedger()
    sp = _spine()
    sync_ledger_from_spine(led, sp, current_chapter=1)
    pr = next(p for p in led.promises if p.text == "방울의 정체")
    # 작가 오버라이드 — 재동기화가 덮지 않는다(무강제)
    pr.due_chapter, pr.due_source = 9, "author"
    sync_ledger_from_spine(led, sp, current_chapter=2)
    assert pr.due_chapter == 9 and pr.due_source == "author"
    # 산술 출처는 재계획(예산 변경) 시 갱신된다(멱등 아님이 계약 — 최신 계획 추종)
    led2 = PromiseLedger()
    sync_ledger_from_spine(led2, sp, current_chapter=1)
    sp.arcs[0].episodes[0].target_chapters = 5       # ep1 이 5화로 늘면 ep2 구간은 6~7화
    sync_ledger_from_spine(led2, sp, current_chapter=2)
    pr2 = next(p for p in led2.promises if p.text == "방울의 정체")
    assert pr2.due_chapter == 6 and pr2.due_source == "spine_arith"   # (6+7)//2
