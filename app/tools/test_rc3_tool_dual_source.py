# -*- coding: utf-8 -*-
"""RC-3 도구 정본 이중 소스 회귀 — 엔티티 우선·정규식 폴백(단일 장애점 해소). LLM 0·결정론.

ⓐ object 카테고리 엔티티가 도구 정본 소스(entities 순서 보존)
ⓑ object 엔티티 부재 시 vocabulary_tone 정규식 폴백(m.group(1) 원문 = 바이트 동일)
ⓒ 둘 다 부재 시에만 시끄럽게 실패(StoryPassNotReady)
ⓓ assemble_materials·generate_event_menu 두 소비처가 같은 이중 소스를 탄다
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from novelcopilot.domain.world import (WorldConfig, GenreContract, EntitySpec, EntityTypeSpec,
                                       object_entity_names, canon_tool_vocab_match)
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine import story_pass as SP
from novelcopilot.engine.story_pass import StoryPassNotReady

_VOCAB = "현대 생활어. 주인공의 도구 어휘(손거울·결박줄·망토·방울·구슬·봉인지)는 생활 실물 여섯 점이다."
_TOOLS = ["손거울", "결박줄", "망토", "방울", "구슬", "봉인지"]


def _world(with_entities: bool, with_vocab: bool) -> WorldConfig:
    ents = [EntitySpec(id="hero", name="주인공", etype="character")]
    if with_entities:
        ents += [EntitySpec(id=f"t{i}", name=nm, etype="item") for i, nm in enumerate(_TOOLS)]
    gc = GenreContract(vocabulary_tone=_VOCAB) if with_vocab else None
    return WorldConfig(title="t", synopsis="평범한 하루가 흔들린다.", entities=ents, genre_contract=gc)


def test_object_entity_names_selects_object_category_in_order():
    w = _world(with_entities=True, with_vocab=False)
    assert object_entity_names(w) == _TOOLS               # item(=object) 만·entities 순서 보존
    # 작가 카탈로그가 item 을 재정의해도 category 로 해소(BUILTIN 폴백과 무관)
    w2 = _world(with_entities=True, with_vocab=False)
    w2.entity_types = [EntityTypeSpec(key="item", label="도구", category="object")]
    assert object_entity_names(w2) == _TOOLS
    # actor(character)·group 은 제외
    assert object_entity_names(_world(with_entities=False, with_vocab=True)) == []


def test_regex_fallback_bytes_identical_raw_group():
    w = _world(with_entities=False, with_vocab=True)
    m = canon_tool_vocab_match(w)
    assert m is not None and m.group(1) == "손거울·결박줄·망토·방울·구슬·봉인지"   # 원문 그대로(공백 보존)
    # genre_contract None 안전(폴백 소스 부재)
    assert canon_tool_vocab_match(_world(with_entities=False, with_vocab=False)) is None
    # 공백 포함 열거도 원문 보존(split-join 이 아닌 raw group)
    w.genre_contract = GenreContract(vocabulary_tone="도구 어휘(가 · 나)")
    assert canon_tool_vocab_match(w).group(1) == "가 · 나"


def _state(w: WorldConfig) -> ProjectState:
    ep = Episode(episode_id="ep1", arc_id="a1", order=1, climax="절정",
                 target_chapters=3, event_menu=["문이 다시 울린다"], required_events=[])
    w.spine = NarrativeSpine(ending=None, arcs=[Arc(arc_id="a1", order=1, title="A1", episodes=[ep])])
    st = ProjectState(id="p", seed=ProjectSeed(), world=w)
    st.narrative_progress.current_arc_id = "a1"
    st.narrative_progress.current_episode_id = "ep1"
    st.narrative_progress.chapters_in_episode = 0
    st.current_chapter = 0
    st.chapters = [ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED,
                                 text="지난 이야기", detail_synopsis="지난 화 요지")]
    return st


def test_assemble_materials_entity_first_then_regex_then_raises():
    # ⓐ 엔티티 우선: 엔티티 tools = "·".join(names)
    st = _state(_world(with_entities=True, with_vocab=False))
    mat = SP.assemble_materials(st, chapter_no=2)
    assert mat["tools"] == "·".join(_TOOLS) and "손거울·결박줄·망토·방울·구슬·봉인지" in mat["mat"]

    # ⓑ 엔티티 없고 vocab 있으면 정규식(원문 group). 라이브(엔티티=vocab 열거 동일)와 바이트 동일 확인
    st2 = _state(_world(with_entities=False, with_vocab=True))
    mat2 = SP.assemble_materials(st2, chapter_no=2)
    assert mat2["tools"] == "손거울·결박줄·망토·방울·구슬·봉인지"
    assert mat2["tools"] == mat["tools"]                  # 라이브 정합: 두 소스가 동일 도구 문자열

    # ⓒ 둘 다 부재 → 시끄럽게 실패(genre_contract None 안전 — AttributeError 아님)
    st3 = _state(_world(with_entities=False, with_vocab=False))
    try:
        SP.assemble_materials(st3, chapter_no=2)
        assert False, "StoryPassNotReady 미발생"
    except StoryPassNotReady as e:
        assert "도구 정본" in str(e)


def test_generate_event_menu_shares_dual_source():
    # ⓓ arc_planner 소비처도 같은 이중 소스: object 엔티티가 [이미 등장한 것]에 실린다(정규식 폴백과 동일 결과)
    import novelcopilot.worldgen.arc_planner as ap

    class _P:
        def chat_json(self, msgs, temperature=0.4):
            self.appeared = msgs[1]["content"]
            return {"event_menu": ["새 사건"]}

    def _run(w):
        p = _P()
        planner = ap.ArcPlanner(p)
        ep = w.spine.arcs[0].episodes[0]
        planner.generate_event_menu(w, w.spine.arcs[0], ep, recent=["최근"])
        return p.appeared

    we = _state(_world(with_entities=True, with_vocab=False)).world
    wv = _state(_world(with_entities=False, with_vocab=True)).world
    for tool in _TOOLS:
        assert tool in _run(we), f"엔티티 소스 누락: {tool}"
        assert tool in _run(wv), f"정규식 소스 누락: {tool}"
    # 엔티티/정규식 부재면 도구 블록 미추가(하위호환 — 도구 항목 0)
    wn = _state(_world(with_entities=False, with_vocab=False)).world
    appeared_none = _run(wn)
    assert not any(tool in appeared_none for tool in _TOOLS)
