# -*- coding: utf-8 -*-
"""T6 준비도 advisory — 장르 중립 결정론 구조 신호·비차단. 임계는 시스템 자기선언 최소치(추적축2+)·구조 카운트만. LLM 0콜.

핵심: 게이트 아님(생성 차단 0)·장르 슬롯 하드코딩 0(genre-blind 회피)·자동보강 0. flags=작가 가시화 advisory."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.engine.readiness import chapter_readiness
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.world import WorldConfig, EntitySpec, AttributeSpec, WorldRuleSpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.bible import StoryBible, BibleEntry


def _state(world=None, bible_entries=None, current=0, runtime_entities=None):
    return ProjectState(
        id="p", seed=ProjectSeed(title="t"),
        world=world or WorldConfig(title="t", synopsis="s"),
        bible=StoryBible(entries=bible_entries or []),
        current_chapter=current,
        runtime_entities=runtime_entities or [])


def _rich_world():
    ep = Episode(episode_id="e1", arc_id="a1", order=1, target_chapters=5)
    ep2 = Episode(episode_id="e2", arc_id="a2", order=1, target_chapters=5)
    return WorldConfig(
        title="t", synopsis="s",
        attributes=[AttributeSpec(key="rank", label="등급", kind="numeric"),
                    AttributeSpec(key="affiliation", label="소속", kind="categorical", vocab=["A"])],
        entities=[EntitySpec(id="hero", name="레오"), EntitySpec(id="ally", name="미나")],
        world_rules=[WorldRuleSpec(rule_id="r1", text="규칙")],
        spine=NarrativeSpine(ending=EndingSpec(ending="주인공 승리"),
                             arcs=[Arc(arc_id="a1", order=1, episodes=[ep]),
                                   Arc(arc_id="a2", order=2, episodes=[ep2])]))


def test_thin_empty_state_flags():
    rep = chapter_readiness(_state())            # 기본 WorldConfig: bible0·attr0·spine없음
    keys = {f["key"] for f in rep["flags"]}
    assert rep["level"] == "thin"
    assert "bible_empty" in keys and "attributes_thin" in keys
    assert all(f["severity"] == "warn" for f in rep["flags"])   # 전부 advisory(차단 severity 없음)


def test_rich_state_ok_no_flags():
    rep = chapter_readiness(_state(world=_rich_world(),
                                   bible_entries=[BibleEntry(entry_id="b1", title="설정1", category="기타")],
                                   current=3))
    assert rep["level"] == "ok" and rep["flags"] == []
    s = rep["signals"]
    assert s["bible"] == 1 and s["attributes"] == 2 and s["entities"] == 2
    assert s["arcs"] == 2 and s["arcs_decomposed"] == 2 and s["ending_set"] is True
    assert s["plan_runway"] == 10                # 5+5


def test_multi_arc_only_one_decomposed():
    w = _rich_world()
    w.spine.arcs[1].episodes = []                # 2막 중 1막만 분해
    rep = chapter_readiness(_state(world=w,
                                   bible_entries=[BibleEntry(entry_id="b1", title="x", category="기타")]))
    keys = {f["key"] for f in rep["flags"]}
    assert "arcs_undecomposed" in keys and rep["signals"]["arcs_decomposed"] == 1


def test_runway_exhausted():
    # 분해된 계획 런웨이(10) 이상으로 커서 전진 → 계획 밖 회차 경고
    rep = chapter_readiness(_state(world=_rich_world(),
                                   bible_entries=[BibleEntry(entry_id="b1", title="x", category="기타")],
                                   current=10))
    keys = {f["key"] for f in rep["flags"]}
    assert "runway_exhausted" in keys


def test_bible_deprecated_excluded_and_runtime_entities_counted():
    rep = chapter_readiness(_state(
        world=_rich_world(),
        bible_entries=[BibleEntry(entry_id="b1", title="live", category="기타"),
                       BibleEntry(entry_id="b2", title="dead", category="기타", status="deprecated")],
        runtime_entities=[EntitySpec(id="npc", name="김동수")]))
    assert rep["signals"]["bible"] == 1                          # deprecated 제외
    assert rep["signals"]["entities"] == 3                        # 시드2 + 런타임1


def test_non_blocking_shape():
    # advisory 계약: 어떤 flag 도 생성을 막는 severity('block'/'error')를 내지 않는다
    rep = chapter_readiness(_state())
    assert all(f["severity"] == "warn" for f in rep["flags"])
    # 계약은 '게이트 판정 필드 부재'다. additive 참고 키(XR-5 tier_report 등)는 level/flags 를 건드리지
    #   않는 읽기 전용 창이라 허용 — 원래 집합 고정 assert 가 그 구분을 못 해 열어 둔다.
    assert {"level", "signals", "flags"} <= set(rep)
    assert not ({"blocked", "gate", "verdict", "allowed", "pass"} & set(rep))
