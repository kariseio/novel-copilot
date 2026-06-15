# -*- coding: utf-8 -*-
"""1개월차 검증 (블라인드 감사 c-확정 처방) — G8 스파인 검증 / G1 약속 원장 / G4 회차 기능 차원.
모두 LLM 0콜(스텁 프로바이더·순수 함수). 실행: PYTHONPATH=app python tools/test_month1_g8_g1_g4.py
"""
from __future__ import annotations
import sys

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.ledger import PromiseLedger
from novelcopilot.engine.factory import build_engine
from novelcopilot.engine.plan_lint import lint_beat
from novelcopilot.engine.ledger_ops import (sync_ledger_from_spine, outstanding,
                                            chapters_since_payoff, ledger_telemetry)
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


class ScriptFake(LLMProvider):
    """chat_json 을 캔드 응답 시퀀스로 대체 + 호출 kwargs 기록(max_tokens 검증용). LLM 0콜."""
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = []

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        self.calls.append(k)
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


# ---------- G8: 스파인 검증 ----------
def test_spine_gaps_pure() -> bool:
    full = {"ending": {"ending": "E", "central_question": "Q"},
            "arcs": [{"goal": "g", "episodes": [{"climax": "c"}]}]}
    empty = {"ending": {"ending": "", "central_question": ""},
             "arcs": [{"goal": "", "episodes": [{"climax": ""}]}]}
    g_full = ArcPlanner._spine_gaps(full)
    g_empty = ArcPlanner._spine_gaps(empty)
    ok = (g_full == [] and "ending.ending(확정 결말)" in g_empty
          and "arc1.goal" in g_empty and "arc1.ep1.climax" in g_empty)
    print(f"[{'OK' if ok else 'FAIL'}] _spine_gaps: 완전={g_full} / 누락 감지 {len(g_empty)}건")
    return ok


def test_spine_validation_correction() -> bool:
    w = WorldConfig(title="t", genre="x", premise="p",
                    entities=[EntitySpec(id="hero", name="주인공")])
    incomplete = {"ending": {"central_question": "", "ending": "", "thematic_payoff": ""},
                  "arcs": [{"title": "A1", "goal": "", "central_conflict": "", "turning_point": "",
                            "episodes": [{"title": "E1", "premise": "", "climax": "",
                                          "required_events": [], "required_cast": [],
                                          "plants": [], "payoffs": [], "target_chapters": 3}],
                            "new_cast": []}]}
    corrected = {"ending": {"central_question": "Q채움", "ending": "결말채움", "thematic_payoff": "T"},
                 "arcs": [{"title": "A1", "goal": "목표채움", "central_conflict": "갈등", "turning_point": "전환",
                           "episodes": [{"title": "E1", "premise": "도입", "climax": "절정채움",
                                         "required_events": [], "required_cast": [],
                                         "plants": [], "payoffs": [], "target_chapters": 3}],
                           "new_cast": []}]}
    fake = ScriptFake([incomplete, corrected])
    spine = ArcPlanner(fake).build_spine(w, 12)
    n_arcs = max(2, min(8, round(12 / 18)))
    ok = (spine.ending.ending == "결말채움"                       # 교정본 채택
          and spine.arcs and spine.arcs[0].goal == "목표채움"
          and len(fake.calls) == 2                                # 빌드 + 교정 1회
          and fake.calls[0].get("max_tokens") == min(8000, 2200 + n_arcs * 500))  # 토큰 비례
    # 완전한 응답이면 교정 콜 없음
    fake2 = ScriptFake([corrected])
    ArcPlanner(fake2).build_spine(w, 12)
    ok &= len(fake2.calls) == 1
    print(f"[{'OK' if ok else 'FAIL'}] 스파인 검증: 빈 엔딩→교정 채택(콜 {len(fake.calls)}), 완전→교정 생략(콜 {len(fake2.calls)})")
    return ok


# ---------- G1: 약속 원장 ----------
def _spine_with_plants() -> NarrativeSpine:
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1", target_chapters=2,
                    plants=["회귀 전 결정적 실수", "조혜민의 정체"], payoffs=[]),
            Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, climax="c2", target_chapters=2,
                    plants=["숨겨진 코어 기록"], payoffs=["조혜민의 정체"])])])   # 한 약속은 회수됨


def test_ledger_sync() -> bool:
    led = PromiseLedger()
    opened = sync_ledger_from_spine(led, _spine_with_plants(), current_chapter=3)
    ids = {p.id: p for p in led.promises}
    paid = [p for p in led.promises if p.status == "paid"]
    ok = (opened == 3 and len(led.promises) == 3                  # 3개 plant → 3 약속
          and len(paid) == 1 and paid[0].text == "조혜민의 정체"  # payoff 라벨 일치 → paid
          and led.last_payoff_chapter == 3)
    # 멱등: 재동기화는 새 약속 0(가산적, 중복 없음)
    again = sync_ledger_from_spine(led, _spine_with_plants(), current_chapter=4)
    ok &= (again == 0 and len(led.promises) == 3)
    print(f"[{'OK' if ok else 'FAIL'}] 원장 동기화: 신규 {opened}·회수 {len(paid)}·재동기화 신규 {again}(멱등)")
    return ok


def test_ledger_telemetry() -> bool:
    led = PromiseLedger()
    sync_ledger_from_spine(led, _spine_with_plants(), current_chapter=3)
    tele = ledger_telemetry(led, current_chapter=6)
    out = outstanding(led, 6)
    ok = (tele["open"] == 2 and tele["paid"] == 1
          and tele["since_payoff"] == 3                           # 6 - last_payoff(3)
          and len(out) == 2 and all(p.status == "open" for p in out))
    # 지불 이력 없으면 since_payoff=None
    empty = PromiseLedger()
    sync_ledger_from_spine(empty, NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="a", order=1, episodes=[
            Episode(episode_id="a_ep1", arc_id="a", order=1, plants=["미회수"], payoffs=[])])]), 2)
    ok &= chapters_since_payoff(empty, 5) is None
    print(f"[{'OK' if ok else 'FAIL'}] 텔레메트리: open={tele['open']} since_payoff={tele['since_payoff']} / 무지불=None")
    return ok


# ---------- G4: 회차 기능 차원(데이터 라벨 — 강제·주입 없음) ----------
def test_beat_dimensions_parsed() -> bool:
    w = WorldConfig(title="t", genre="x", premise="p",
                    entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"))
    arc = Arc(arc_id="arc1", order=1, goal="g")
    ep = Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c", target_chapters=3)
    resp = {"title": "1화", "summary": "요약", "key_events": ["e1"], "entities": ["hero"],
            "chapter_function": "payoff", "hook_type": "reveal", "time_advance": "사흘 후", "place": "길드"}
    beat = ArcPlanner(ScriptFake([resp])).beat_for_episode(w, arc, ep, 1, False, ["최근"], [])
    # 설계자가 자기 계획을 라벨링(서술 메타데이터) — 파싱만 확인. 명령형 주입(_reward_block)은 제거됨(구조적 보장)
    ok = (beat.chapter_function == "payoff" and beat.hook_type == "reveal"
          and beat.time_advance == "사흘 후" and beat.place == "길드"
          and not hasattr(ArcPlanner, "_reward_block"))   # 명령형 주입 헬퍼 제거 확인
    print(f"[{'OK' if ok else 'FAIL'}] 비트 기능 라벨 파싱({beat.chapter_function}/{beat.hook_type}/{beat.time_advance}) + _reward_block 제거")
    return ok


def test_lint_canon_only() -> bool:
    """lint_beat 는 캐논 정합(엔티티)만 — 훅/시간/장소 페이싱은 강제하지 않음(작가 가시화로 분리)."""
    import inspect
    s = get_settings()
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    ont = build_engine(w, ScriptFake([{}]), s).ontology
    # 무효 id 는 잡고, 유효 id 는 통과
    v_bad = lint_beat({"entities": ["ghost"]}, ont, 5)
    v_ok = lint_beat({"entities": ["hero"]}, ont, 5)
    # 훅/시간 라벨이 있어도 페이싱 위반은 생성하지 않음(강제 제거 확인)
    v_pace = lint_beat({"entities": ["hero"], "hook_type": "reveal", "time_advance": "없음",
                        "is_episode_finale": False}, ont, 5)
    sig = inspect.signature(lint_beat).parameters
    ok = ({v.kind for v in v_bad} == {"plan_unknown_entity"} and v_ok == [] and v_pace == []
          and "recent" not in sig)                       # recent 파라미터 제거 = 페이싱 강제 경로 소거
    print(f"[{'OK' if ok else 'FAIL'}] lint 캐논 전용: 무효 id 검출·유효 통과·페이싱 무위반·recent 파라미터 제거({list(sig)})")
    return ok


if __name__ == "__main__":
    results = [test_spine_gaps_pure(), test_spine_validation_correction(),
               test_ledger_sync(), test_ledger_telemetry(),
               test_beat_dimensions_parsed(), test_lint_canon_only()]
    print("\n1개월차(G8/G1/G4) 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
