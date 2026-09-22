# -*- coding: utf-8 -*-
"""B-32 구조 사용 이력 결정론 주입 테스트 — 구조 재탕 소스 차단. LLM 0콜·신규 상태 0.

규명: beat_for_episode·generate_event_menu 가 `recent` 요약만 보고 자신이 과거에 고른 구조(훅/기능/
장소/key_events)를 못 봐 '구조 단위' 재탕이 반복. structure_history 는 영속된 회차 라벨에서 그 이력을
결정론 조립해 advisory 참고 블록으로 노출한다(판정기 아님). 미전달 → 프롬프트 바이트 동일(하위호환).

검증 축: ① structure_history 조립(결측 라벨·n 미만·n 상한·ESCALATED 제외·key_events 원문·집계)
        ② 블록 렌더 형식 잠금 ③ 두 설계 콜 주입 형식 + None 바이트 동일 ④ 부정명령→긍정 전환.
"""
import sys
import pathlib
import json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.domain.world import WorldConfig, Beat, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.engine.structure_history import structure_history, structure_history_block
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


def _rec(ch, hook="", func="", place=""):
    return ChapterRecord(chapter=ch, status=ChapterStatus.FINALIZED,
                         hook_type=hook, chapter_function=func, place=place)


# ---------------- ① structure_history 결정론 조립 ----------------

def test_empty_and_none_records():
    """레코드 없음/None → n=0·items 빈 리스트(집계 0). 콜드스타트 하위호환."""
    for recs in (None, [], [_rec(1)]):   # _rec(1)=라벨 전무(구 레코드) → skip
        h = structure_history(recs)
        assert h["n"] == 0 and h["items"] == []
        assert h["hook_monotony"] == 0.0 and h["hook_max_run"] == 0 and h["place_max_run"] == 0


def test_missing_labels_skipped():
    """구조 라벨(hook/func/place)·key_events 전무 회차는 items 에서 skip(구 레코드 하위호환)."""
    recs = [_rec(1, hook="reveal", place="길드"), _rec(2), _rec(3, func="payoff")]
    h = structure_history(recs)
    assert [it["chapter"] for it in h["items"]] == [1, 3]   # 2화(라벨 전무) 제외


def test_fewer_than_n_all_included():
    """포함 회차가 n 미만이면 전부 포함(정렬 유지)."""
    recs = [_rec(3, hook="action"), _rec(1, hook="reveal"), _rec(2, hook="reveal")]
    h = structure_history(recs, n=8)
    assert [it["chapter"] for it in h["items"]] == [1, 2, 3]   # chapter 오름차순


def test_n_cap_keeps_latest():
    """n 상한 초과 → 최근 n개(최대 chapter)만 유지."""
    recs = [_rec(i, hook="reveal") for i in range(1, 13)]   # 12화
    h = structure_history(recs, n=8)
    assert h["n"] == 8 and [it["chapter"] for it in h["items"]] == list(range(5, 13))


def test_escalated_excluded_finalized_and_beat_included():
    """ESCALATED 기록은 제외(전이·비영속). FINALIZED + status 없는 Beat 는 포함."""
    recs = [_rec(1, hook="reveal"),
            ChapterRecord(chapter=2, status=ChapterStatus.ESCALATED, hook_type="action", place="X"),
            Beat(chapter=3, hook_type="twist", place="탑")]   # Beat=status 속성 없음 → 포함
    h = structure_history(recs)
    assert [it["chapter"] for it in h["items"]] == [1, 3]     # ESCALATED 2화 제외


def test_key_events_verbatim_from_beat():
    """key_events 원문은 Beat 에서 그대로(원문 보존). ChapterRecord(key_events 필드 없음) → 빈 리스트로 격하."""
    h = structure_history([Beat(chapter=1, hook_type="reveal", key_events=["각성 접촉", "흡수", "무릎"])])
    assert h["items"][0]["key_events"] == ["각성 접촉", "흡수", "무릎"]
    h2 = structure_history([_rec(1, hook="reveal")])   # ChapterRecord → key_events 없음
    assert h2["items"][0]["key_events"] == []


def test_key_events_derived_from_gen_context():
    """B-32 미세수정: 영속 ChapterRecord 는 key_events 를 *직접 필드가 아니라* 영속된 gen_context 안의
    계획 비트에서 파생한다(신규 상태 0 — ChapterRecord 필드/Beat 영속 추가 없음).
    gen_context 있는 레코드 → 노출, 없는 레코드(미주입 {}) → [](결측 skip 하위호환)."""
    rec = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, hook_type="reveal",
                        gen_context={"draft": {"beat": {"key_events": ["각성 접촉", "흡수", "무릎"]}}})
    h = structure_history([rec])
    assert h["items"][0]["key_events"] == ["각성 접촉", "흡수", "무릎"]   # gen_context 계획 비트에서 파생

    bare = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, hook_type="reveal")   # gen_context 미주입({})
    assert structure_history([bare])["items"][0]["key_events"] == []      # 결측 → [](하위호환)

    # 대체 위치('plan' 최상위)도 파생 지원 — B-32e A/B 에서 계획 키가 어디로 실려도 결측되지 않게(방어적)
    alt = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, hook_type="reveal",
                        gen_context={"plan": {"key_events": ["난입", "대치"]}})
    assert structure_history([alt])["items"][0]["key_events"] == ["난입", "대치"]


def test_aggregates_monotony_and_runs():
    """집계: 최빈 훅 비율·같은 훅/장소 연속 최대 run(pacing.label_max_run 재사용)."""
    recs = [_rec(1, hook="reveal", place="길드"), _rec(2, hook="reveal", place="길드"),
            _rec(3, hook="reveal", place="탑"), _rec(4, hook="action", place="탑")]
    h = structure_history(recs)
    assert h["hook_monotony"] == 0.75           # reveal 3/4
    assert h["hook_max_run"] == 3               # reveal 3연속(1~3화)
    assert h["place_max_run"] == 2              # 길드 2연속(1~2화)


# ---------------- ② 블록 렌더 형식 잠금 ----------------

def test_block_empty_when_no_history():
    """None·빈 이력 → "" (주입 생략·프롬프트 바이트 동일)."""
    assert structure_history_block(None) == ""
    assert structure_history_block({"n": 0, "items": []}) == ""
    assert structure_history_block(structure_history([])) == ""


def test_block_format_lock():
    """렌더 형식: 헤더·회차별 라벨 라인([기능/훅@장소] — key_events)·집계 라인."""
    recs = [_rec(1, hook="reveal", func="escalation", place="길드"),
            _rec(2, hook="reveal", func="escalation", place="길드")]
    block = structure_history_block(structure_history(recs))
    assert block.startswith("[최근 회차 구조 이력")
    assert "· 1화 [escalation/reveal@길드]" in block
    assert "(집계)" in block and "최빈 훅 비율" in block and "같은 훅 연속 최대 2" in block
    assert block.endswith("\n")
    # key_events 원문 노출(Beat 경로)
    b2 = structure_history_block(structure_history([Beat(chapter=5, hook_type="twist", key_events=["난입", "대치"])]))
    assert "· 5화 [twist] — 난입 / 대치" in b2


# ---------------- ③ 두 설계 콜 주입 + None 바이트 동일 ----------------

class _CaptureProvider(LLMProvider):
    """usr 프롬프트를 캡처하고 benign JSON 반환(주입 검증용 — LLM 0콜, 예외/재시도 없음)."""
    def __init__(self):
        super().__init__()
        self.usr = ""

    def chat(self, messages, **kw):
        self.usr = messages[-1]["content"]
        return json.dumps({"title": "t", "key_events": ["e"], "event_menu": ["m"]}, ensure_ascii=False)

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _world_arc_ep():
    w = WorldConfig(title="t", genre="판타지", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="a1", order=1, title="A1", goal="g")])
    ep = Episode(episode_id="e1", arc_id="a1", order=1, title="E1", premise="도입", climax="절정")
    return w, w.spine.arcs[0], ep


def _hist():
    return structure_history([_rec(1, hook="reveal", func="escalation", place="길드"),
                              _rec(2, hook="reveal", func="escalation", place="길드")])


def test_beat_injection_and_backward_compat():
    """beat_for_episode: 이력 주입 시 블록 노출 / 미전달(None)·빈 이력 시 블록 없음·바이트 동일."""
    w, arc, ep = _world_arc_ep()
    cap = _CaptureProvider()
    ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전 줄거리"], [], structure_history=_hist())
    assert "[최근 회차 구조 이력" in cap.usr and "· 1화 [escalation/reveal@길드]" in cap.usr

    cap0 = _CaptureProvider()
    ArcPlanner(cap0).beat_for_episode(w, arc, ep, 3, False, ["직전 줄거리"], [])   # 미전달(None)
    base = cap0.usr
    assert "[최근 회차 구조 이력" not in base
    cap_empty = _CaptureProvider()
    ArcPlanner(cap_empty).beat_for_episode(w, arc, ep, 3, False, ["직전 줄거리"], [],
                                           structure_history=structure_history([]))   # 빈 이력
    assert cap_empty.usr == base                    # 빈 이력 = 무주입(바이트 동일)


def test_menu_injection_and_backward_compat():
    """generate_event_menu: 이력 주입 시 블록 노출 / None·빈 이력 시 블록 없음·바이트 동일."""
    w, arc, ep = _world_arc_ep()
    cap = _CaptureProvider()
    ArcPlanner(cap).generate_event_menu(w, arc, ep, ["직전 줄거리"], structure_history=_hist())
    assert "[최근 회차 구조 이력" in cap.usr and "같은 훅 연속 최대 2" in cap.usr

    cap0 = _CaptureProvider()
    ArcPlanner(cap0).generate_event_menu(w, arc, ep, ["직전 줄거리"])   # 미전달
    base = cap0.usr
    assert "[최근 회차 구조 이력" not in base
    cap_empty = _CaptureProvider()
    ArcPlanner(cap_empty).generate_event_menu(w, arc, ep, ["직전 줄거리"], structure_history=structure_history([]))
    assert cap_empty.usr == base                    # 빈 이력 = 무주입(바이트 동일)


def test_ch1_no_structure_block_and_no_cont():
    """1화(chapter==1)는 연속(cont) 문구 자체가 없음 — 이력 주입해도 블록만 붙고 cont 지시는 미부착."""
    w, arc, ep = _world_arc_ep()
    cap = _CaptureProvider()
    ArcPlanner(cap).beat_for_episode(w, arc, ep, 1, False, [], [], structure_history=_hist())
    # 1화 sys 에는 cont(이어받기/구별) 문구가 없다(도입부 분기)
    # (usr 에는 이력 블록이 붙을 수 있으나, chapter==1 은 recent 가 비어 자연스러움 — 블록은 truthy 이력이면 노출)
    assert "[최근 회차 구조 이력" in cap.usr


# ---------------- ④ 부정명령 → 긍정 전환(pink-elephant 소스 차단) ----------------

def test_reset_prohibition_replaced_with_positive():
    """beat_for_episode sys(cont)에서 '리셋·동일 장면 재연·비트 반복 금지' 부정명령 제거·긍정형 대체."""
    import novelcopilot.worldgen.arc_planner as apmod

    class _Cap(_CaptureProvider):
        def __init__(self):
            super().__init__()
            self.sys = ""

        def chat(self, messages, **kw):
            self.sys = messages[0]["content"]
            return super().chat(messages, **kw)

    w, arc, ep = _world_arc_ep()
    cap = _Cap()
    apmod.ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전"], [])   # chapter>1 → cont 부착
    assert "리셋·동일 장면 재연·비트 반복 금지" not in cap.sys           # 부정명령 제거
    assert "최근 회차들과 구별되는 새 구도·장치·전개 방식을 우선 선택" in cap.sys   # 긍정 대체
    assert "이전과 다른 결과가 나게 하라" in cap.sys


def test_settle_premise_positive():
    """B-31 LOW-3: _ensure_final_settlement 정산 에피소드 premise 부정명령→긍정('여운을 남긴다')."""
    spine = NarrativeSpine(
        ending=EndingSpec(central_question="Q", ending="E", thematic_payoff="주제 정산"),
        arcs=[Arc(arc_id="a1", order=1, title="최종", goal="g",
                  episodes=[Episode(episode_id="a1_ep1", arc_id="a1", order=1, title="절정",
                                    premise="p", climax="c", target_chapters=6)])])
    ArcPlanner._ensure_final_settlement(spine)
    settle = [e for e in spine.arcs[0].episodes if e.episode_id.endswith("_settle")]
    assert settle, "정산 에피소드가 부착되어야 함"
    prem = settle[0].premise
    assert "새 갈등·새 떡밥을 열지 말고" not in prem      # 부정명령 제거
    assert "이미 열린 것을 닫고 여운을 남긴다" in prem     # 긍정 대체


if __name__ == "__main__":
    import pytest as _pytest
    raise SystemExit(_pytest.main([__file__, "-q"]))
