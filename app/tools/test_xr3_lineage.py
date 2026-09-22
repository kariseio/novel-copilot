# -*- coding: utf-8 -*-
"""XR-3 ⑵-b — 집필기 draft 블록 계보(관측 전용·결정론·LLM 0콜 추가).

왜: gen_context 는 '무엇이 들어갔나'의 내용 스냅샷이지 '어디서 왔나'가 아니다(005 §3 — 데이터의
전 소비 지점을 추적하지 않은 것이 4회 우회의 공통 소스). record.gen_context['draft']['lineage'] 에
블록별 ID·출처·선택 사유만 남긴다(전문 중복 금지·신규 저장 층 0).

검사 7축:
  ⓐ 스키마 — 9개 블록이 조립 순서대로 존재(brief~confirmed_story)
  ⓑ 캐논 계보 — ground_truth 항목에 eff_from·tier·출처 id 가 실린다(엣지는 provenance 까지)
  ⓒ K1 결측 정직 — 조립 경로를 바꿔야 얻는 항목(world_rules rule_id · 노드 provenance)은
     기록을 포기하고 gap 으로 명시한다(침묵 결측 금지)
  ⓓ 주입 바이트 불변 — canon_facts/canon_relations 의 with_lineage=True/False 산출 facts 가 동일
  ⓔ 조립 텍스트 불변 — _build_story_so_far_hier(with_contributors) 가 text·dropped 를 바꾸지 않는다
  ⓕ 서비스 보강 — _fill_draft_lineage 가 contributors·gen_no 를 채우고 그 gap 을 걷어낸다
  ⓖ 하위호환 — lineage 는 gen_context(자유형 dict) additive, 구 레코드 로드·덤프 무변경

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_xr3_lineage.py
"""
from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace as NS

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from novelcopilot.config import get_settings
from novelcopilot.domain.types import (AuthorDirective, RetrievedItem, RelationEdge,
                                       ChapterRecord, ChapterStatus)
from novelcopilot.domain.world import (WorldConfig, EntitySpec, AttributeSpec, WorldRuleSpec,
                                       TimelineEntry)
from novelcopilot.engine.factory import build_engine, build_ontology
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.llm.base import LLMProvider

_BLOCKS = ["brief", "ground_truth", "world_rules", "authority", "voice_cards",
           "story_so_far", "prev_chapter", "narrative", "confirmed_story"]


class _Cap(LLMProvider):
    """실 LLM 0 — 콜 수만 세고 고정 응답."""
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.captured: list = []

    def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
        self.calls += 1
        self.captured.append(messages)
        return "문장이 이어진다. " * 200

    def chat_json(self, messages, *, temperature=0.0, max_tokens=None):
        self.calls += 1
        self.captured.append(messages)
        return {}

    def chat_tools(self, messages, **kw):
        self.calls += 1
        return "완료"

    def embed(self, texts):
        return [[0.0] for _ in texts]


def _world() -> WorldConfig:
    return WorldConfig(
        title="[실험] XR-3 계보", genre="현판", tone="건조", premise="t.",
        attributes=[AttributeSpec(key="money_won", label="소지금(원)", kind="numeric")],
        entities=[
            EntitySpec(id="hero", name="주인공", etype="character",
                       voice="능청 요체로 말한다.", attrs={"money_won": None}),
            EntitySpec(id="const", name="도시 상수", etype="worldrule", attrs={"money_won": None}),
        ],
        world_rules=[WorldRuleSpec(rule_id="pub", text="공개 규칙이다.")],
        seed_edges=[RelationEdge(edge_id="e1", rel_id="ally_of", src_id="hero", dst_id="const",
                                 eff_from=1, trust_tier="ground_truth", provenance=["author"])],
        timeline=[TimelineEntry(entity_id="hero", attr="money_won", value=12000, eff_from=1),
                  TimelineEntry(entity_id="const", attr="money_won", value=3000, eff_from=1)])


def _settings():
    return get_settings().model_copy(update={
        "gen_tools": False, "narrator_voice": False, "humanize": False, "style_repair": False,
        "reader_desk": False, "claim_audit": False, "cold_read": False, "dialogue_ledger": False,
        "continuity_polish": False, "style_judge_model": "", "gen_trace": False})


def _generate() -> tuple[ChapterRecord, _Cap]:
    w, prov = _world(), _Cap()
    b = build_engine(w, prov, _settings())
    beat = {"title": "t", "summary": "s", "key_events": ["사건 하나"], "entities": ["hero", "const"]}
    rec = b.generator.generate(
        2, beat, b.ontology, b.rag, b.wiki,
        directives=[AuthorDirective(directive_id="d1", text="지시 원문이다.", from_chapter=1)],
        prev_chapter_text="직전 회차 본문.", story_so_far="누적 줄거리.",
        anchors=[RetrievedItem(source="bible", ref="b1", text="설정 한 줄")])
    return rec, prov


def _lin(rec) -> dict:
    return rec.gen_context["draft"]["lineage"]


def _block(lin: dict, name: str) -> dict:
    return next(b for b in lin["blocks"] if b["block"] == name)


# ── ⓐ 스키마 ──
def test_lineage_blocks_present_in_assembly_order():
    rec, _ = _generate()
    lin = _lin(rec)
    assert "gap" not in lin, f"계보 기록 자체가 실패: {lin.get('gap')}"
    assert [b["block"] for b in lin["blocks"]] == _BLOCKS
    assert lin["wired"] == "draft"                   # 배선 범위 정직 표기(나머지 호출은 미배선)
    print(f"[OK] ⓐ 블록 {len(_BLOCKS)}종이 조립 순서대로 기록")


# ── ⓑ 캐논 계보 ──
def test_ground_truth_items_carry_tier_and_eff():
    rec, _ = _generate()
    items = _block(_lin(rec), "ground_truth")["items"]
    facts = [i for i in items if i["src"] == "canon_fact"]
    rels = [i for i in items if i["src"] == "canon_relation"]
    assert facts and all(i["tier"] == "ground_truth" and i["eff_from"] == 1 for i in facts)
    assert {i["id"] for i in facts} == {"hero.money_won", "const.money_won"}
    assert all(i["origin"] == "timeline" for i in facts)      # 시드 속성 vs 회차 커밋 구분
    assert rels and rels[0]["id"] == "e1" and rels[0]["provenance"] == ["author"]
    print(f"[OK] ⓑ 캐논 사실 {len(facts)}·관계 {len(rels)}건에 id·eff_from·tier(엣지는 provenance)")


# ── ⓒ K1 결측 정직 ──
def test_k1_gaps_are_declared_not_silent():
    rec, _ = _generate()
    lin = _lin(rec)
    wr = _block(lin, "world_rules")
    assert "rule_id" in wr["gap"] and "결측 정직" in wr["gap"]
    assert wr["items"] and "idx" in wr["items"][0]            # 결측이어도 위치·머리글자는 남긴다
    gt = _block(lin, "ground_truth")
    assert "provenance" in gt["gap"], gt["gap"]
    print("[OK] ⓒ K1 — 조립 경로를 바꿔야 얻는 항목은 gap 으로 명시(침묵 결측 0)")


# ── ⓓ 주입 바이트 불변(with_lineage 부작용 0) ──
def test_with_lineage_does_not_change_facts():
    w = _world()
    o = build_ontology(w, Vocabulary.from_world(w))
    o.edges.append(w.seed_edges[0])
    ids = ["hero", "const"]
    for kwargs in ({}, {"actors_status_only": True}):
        plain = o.canon_facts(ids, 5, **kwargs)
        paired, lin = o.canon_facts(ids, 5, with_lineage=True, **kwargs)
        assert [f.model_dump() for f in plain] == [f.model_dump() for f in paired]
        assert len(lin) == len(paired)                        # 1:1 순서 대응
    r_plain = o.canon_relations(ids, 5)
    r_pair, r_lin = o.canon_relations(ids, 5, with_lineage=True)
    assert [f.model_dump() for f in r_plain] == [f.model_dump() for f in r_pair]
    assert len(r_lin) == len(r_pair)
    # binding_state_as_of 의 값 선택 규칙이 계보 헬퍼와 갈라지지 않는다(계산 지점 단일화)
    assert o.binding_state_as_of("hero", "money_won", 5) == o.binding_entry_as_of("hero", "money_won", 5)[0]
    print("[OK] ⓓ with_lineage=True/False 의 facts 완전 동일(주입 바이트 불변)")


# ── ⓔ 조립 텍스트 불변(story_so_far) ──
def test_contributors_flag_does_not_change_text():
    from novelcopilot.services.copilot import _build_story_so_far_hier
    chs = [NS(chapter=i, status=ChapterStatus.FINALIZED, episode_id="ep1",
              summary=f"{i}화 요약", detail_synopsis="", text="본문") for i in (1, 2, 3)]
    state = NS(world=NS(spine=NS(arcs=[])), narrative_progress=NS(current_episode_id="ep1"),
               chapters=chs)
    a = _build_story_so_far_hier(state, 4, 10_000)
    b = _build_story_so_far_hier(state, 4, 10_000, with_contributors=True)
    assert len(a) == 2 and len(b) == 3                        # 기본 반환형 무변경(구 호출부 안전)
    assert a == b[:2]                                         # 텍스트·dropped 바이트 동일
    assert b[2]["detail_chapters"] == [1, 2, 3]
    print("[OK] ⓔ with_contributors 는 조립 텍스트·dropped 를 바꾸지 않는다")


# ── ⓕ 서비스 보강 ──
def test_service_fills_engine_gaps():
    from novelcopilot.services import CopilotService
    rec, _ = _generate()
    prev = ChapterRecord(chapter=1, title="t", status=ChapterStatus.FINALIZED, text="x")
    prev.gen_no = 3
    CopilotService._fill_draft_lineage(
        rec, {"detail_chapters": [1], "rollup_episodes": [], "excluded_last_detail": True}, 2, prev)
    ssf, pc = _block(_lin(rec), "story_so_far"), _block(_lin(rec), "prev_chapter")
    assert ssf["contributors"]["detail_chapters"] == [1] and ssf["dropped"] == 2
    assert "gap" not in ssf                                   # 채워졌으면 결측 표기 제거
    assert pc["gen_no"] == 3 and pc["revisions"] == 0 and "gap" not in pc
    # 결측이 채워지지 않으면 gap 은 그대로 남는다(침묵 채움 금지)
    rec2, _ = _generate()
    CopilotService._fill_draft_lineage(rec2, None, 0, None)
    assert "gap" in _block(_lin(rec2), "story_so_far")
    print("[OK] ⓕ 서비스가 contributors·gen_no 를 채우고, 못 채우면 gap 유지")


# ── ⓖ 하위호환 ──
def test_backward_compatible_record_roundtrip():
    old = ChapterRecord(chapter=1, title="t", status=ChapterStatus.FINALIZED, text="x")
    assert old.gen_context == {}                              # lineage 없는 구 레코드 로드 정상
    dumped = old.model_dump(exclude_defaults=True)
    assert "gen_context" not in dumped                        # additive — 구 JSON 바이트 무팽창
    rec, _ = _generate()
    assert isinstance(rec.gen_context, dict) and "lineage" in rec.gen_context["draft"]
    assert ChapterRecord(**rec.model_dump()).gen_context["draft"]["lineage"]["wired"] == "draft"
    print("[OK] ⓖ gen_context additive — 구 레코드 무변경·라운드트립 보존")


if __name__ == "__main__":
    fails = 0
    for fn in (test_lineage_blocks_present_in_assembly_order, test_ground_truth_items_carry_tier_and_eff,
               test_k1_gaps_are_declared_not_silent, test_with_lineage_does_not_change_facts,
               test_contributors_flag_does_not_change_text, test_service_fills_engine_gaps,
               test_backward_compatible_record_roundtrip):
        try:
            fn()
        except AssertionError as e:
            fails += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print("=" * 60)
    print("[OK] XR-3 블록 계보 전 축 통과" if not fails else f"[FAIL] {fails}건")
    sys.exit(1 if fails else 0)
