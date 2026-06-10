# -*- coding: utf-8 -*-
"""A(줄거리 요약 레이어) + B(온톨로지 포괄화) 검증 — LLM 0콜(apply/요약빌더/프롬프트는 순수).
실행: PYTHONPATH=app python tools/test_summary_ontology.py
"""
from __future__ import annotations
import sys

from novelcopilot.config import get_settings
from novelcopilot.domain.world import (WorldConfig, AttributeSpec, EntitySpec, TimelineEntry, Beat, StyleSpec)
from novelcopilot.domain.types import RelationEdge, ContextBoard, SceneSpec, ChapterStatus
from novelcopilot.engine.factory import build_engine
from novelcopilot.engine.prompts import PromptAssembler
from novelcopilot.llm.base import LLMProvider
from novelcopilot.services.copilot import _build_story_so_far


class Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


class Ch:
    def __init__(self, ch, summ, text="", status=ChapterStatus.FINALIZED):
        self.chapter, self.summary, self.text, self.status = ch, summ, text, status


def test_story_so_far() -> bool:
    chs = [Ch(1, "가가 각성했다."), Ch(2, "강도현 등장."),
           Ch(3, "", text="본문3내용"),                                   # 요약실패 but FINALIZED→text fallback 포함
           Ch(4, "미회수.", text="x", status=ChapterStatus.ESCALATED)]    # ESCALATED→제외
    s, dropped = _build_story_so_far(chs, 6000)
    ok = ("1화:" in s and "2화:" in s and "3화: 본문3내용" in s and "4화" not in s and dropped == 0)
    # 예산 컷: 최신부터 채우고 시간순 제시 + dropped 카운트
    long = [Ch(i, "x" * 100) for i in range(1, 11)]
    s2, d2 = _build_story_so_far(long, 250)
    lines = s2.split("\n")
    ok &= 0 < len(lines) <= 3 and lines[-1].startswith("10화") and not s2.startswith("1화") and d2 == len(long) - len(lines)
    print(f"[{'OK' if ok else 'FAIL'}] story_so_far: ESCALATED제외·요약실패 text폴백 + 예산컷(최신·시간순)·dropped={d2}")
    return ok


def test_prompt_includes_sofar() -> bool:
    pa = PromptAssembler(StyleSpec(), 4000)
    board = ContextBoard(chapter=2, story_so_far="누적줄거리XYZ")
    prompt = pa.assemble(board, SceneSpec(index=0, goal="g", key_events=["e"]), "")
    ok = "지금까지 줄거리" in prompt and "누적줄거리XYZ" in prompt
    # 요약 없으면 블록도 없음
    p2 = pa.assemble(ContextBoard(chapter=1), SceneSpec(index=0, goal="g"), "")
    ok &= "지금까지 줄거리" not in p2
    print(f"[{'OK' if ok else 'FAIL'}] 프롬프트 story_so_far 블록 주입/생략")
    return ok


def test_ontology_comprehensive() -> bool:
    s = get_settings()
    w = WorldConfig(title="t", genre="x",
                    attributes=[AttributeSpec(key="affiliation", label="소속", kind="categorical",
                                              vocab=["A", "B"], mutable=True)],
                    entities=[EntitySpec(id="hero", name="주인공", attrs={"affiliation": "A"}),
                              EntitySpec(id="rival", name="라이벌")],
                    timeline=[TimelineEntry(entity_id="rival", attr="status", value="dead", eff_from=3)],
                    beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])])
    o = build_engine(w, Fake(), s).ontology
    updater = build_engine(w, Fake(), s).updater  # 동일 world, 별도 번들 — 대신 같은 ontology 써야: 재사용
    b = build_engine(w, Fake(), s)
    o, updater = b.ontology, b.updater
    proposal = {
        "new_entities": [{"name": "흑막길드", "etype": "faction", "role": "적"},
                         {"name": "고대유적", "etype": "place"}],
        "state_changes": [{"id": "hero", "attr": "affiliation", "value": "B"}],     # mutable→timeline
        "relations": [{"src": "hero", "dst": "흑막길드", "rel_id": "enemy_of"},       # valid(신규 dst)
                      {"src": "hero", "dst": "rival", "rel_id": "ally_of"},          # valid
                      {"src": "hero", "dst": "rival", "rel_id": "옛_동료", "state": "소원해짐"},  # 자유 타입+상태 valid
                      {"src": "hero", "dst": "hero", "rel_id": "ally_of"},           # self→skip(구조)
                      {"src": "hero", "dst": "없음", "rel_id": "ally_of"}],          # 미해결→skip(구조)
    }
    changes, new_specs, new_tl, new_edges = updater.apply(proposal, o, 5)
    ok = True
    ok &= any(sp.etype == "faction" and sp.name == "흑막길드" for sp in new_specs)
    ok &= any(sp.etype == "place" for sp in new_specs)                              # 신규: any etype
    ok &= any(t.attr == "affiliation" and t.value == "B" for t in new_tl)           # mutable 상태변화
    ok &= len(new_edges) == 3 and all(e.trust_tier == "narrative_inferred" and e.provenance == ["machine"]
                                      for e in new_edges)                            # 자유 타입 포함 유효 3건, 비binding
    ok &= any(e.rel_id == "옛_동료" and e.state == "소원해짐" for e in new_edges)     # 자유 타입+질적 상태 보존
    # 자동추출 관계는 비binding: 사망(rival@3) 후 추정관계여도 hard 위반 아님
    ok &= len([v for v in o.ontology_internal_check(5) if v.kind == "edge_post_death"]) == 0
    # 반면 ground_truth(작가) post-death 는 여전히 hard
    o.add_edge(RelationEdge(edge_id="gt1", rel_id="ally_of", src_id="rival", dst_id="hero",
                            eff_from=5, trust_tier="ground_truth"))
    ok &= any(v.kind == "edge_post_death" for v in o.ontology_internal_check(5))
    print(f"[{'OK' if ok else 'FAIL'}] 온톨로지 포괄화: 신규(any etype)/상태변화/관계(narrative_inferred 2건) "
          f"+ 자동관계 비binding·작가관계 binding")
    return ok


def test_machine_death_nonbinding() -> bool:
    """B1 — 기계추출 사망은 narrative_inferred(비구속): 추적은 하되 하드게이트/캐논주입 안 함.
    반면 시드/작가(ground_truth) 사망은 여전히 binding."""
    s = get_settings()
    w = WorldConfig(title="t", genre="x",
                    entities=[EntitySpec(id="hero", name="주인공"), EntitySpec(id="rival", name="라이벌")],
                    beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])])
    b = build_engine(w, Fake(), s)
    o, updater = b.ontology, b.updater
    # 기계추출: 라이벌 사망(5화)
    changes, _ns, new_tl, _ne = updater.apply({"state_changes": [{"id": "rival", "attr": "status", "value": "dead"}]}, o, 5)
    ok = True
    death_tl = [t for t in new_tl if t.attr == "status" and t.value == "dead"]
    ok &= len(death_tl) == 1 and death_tl[0].trust_tier == "narrative_inferred"      # 추적은 됨
    ok &= o.state_as_of("rival", "status", 6) == "dead"                              # 서사 인지엔 보임
    ok &= o.binding_state_as_of("rival", "status", 6) != "dead"                      # 캐논(binding)엔 비반영
    # canon_facts: 기계추정 사망은 캐논 비주입 + 살아있는 인물엔 '생존' 노이즈도 미주입(중대상태만 주입)
    facts = {(f.entity, f.attr_label): f.value for f in o.canon_facts(["rival"], 6)}
    ok &= ("라이벌", "생사") not in facts
    # 기계death 이후 작가 관계 엣지(ground_truth) → post_death 하드 위반 아님(기계death 비구속)
    o.add_edge(RelationEdge(edge_id="e_gt", rel_id="ally_of", src_id="hero", dst_id="rival",
                            eff_from=7, trust_tier="ground_truth"))
    ok &= not any(v.kind == "edge_post_death" for v in o.ontology_internal_check(7))
    # 반면 시드(ground_truth) 사망이면 동일 엣지가 binding 하드 위반
    w2 = WorldConfig(title="t2", genre="x",
                     entities=[EntitySpec(id="hero", name="주인공"), EntitySpec(id="rival", name="라이벌")],
                     timeline=[TimelineEntry(entity_id="rival", attr="status", value="dead", eff_from=5)],
                     beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])])
    o2 = build_engine(w2, Fake(), s).ontology
    o2.add_edge(RelationEdge(edge_id="e_gt2", rel_id="ally_of", src_id="hero", dst_id="rival",
                             eff_from=7, trust_tier="ground_truth"))
    ok &= any(v.kind == "edge_post_death" for v in o2.ontology_internal_check(7))
    ok &= o2.binding_state_as_of("rival", "status", 6) == "dead"                     # 시드death=binding
    print(f"[{'OK' if ok else 'FAIL'}] B1 사망 비대칭: 기계추출=narrative_inferred(추적O·게이트X·캐논X), 시드/작가=binding")
    return ok


def test_generic_lifecycle() -> bool:
    """L — 생애주기 데이터주도(death 하드코딩 제거): 임의 장르의 비가역/제거 상태가 동일 기계로.
    각성(비가역)·비가역 이탈 모순·기본 status terminal 룰·회귀(reversal) 부활."""
    from novelcopilot.domain.world import AttributeSpec
    s = get_settings()
    w = WorldConfig(title="현판", genre="현대 판타지",
                    attributes=[AttributeSpec(key="각성", label="각성", kind="state",
                                              states=["미각성", "각성"], irreversible=["각성"], mutable=True)],
                    entities=[EntitySpec(id="hero", name="주인공", attrs={"각성": "미각성"})],
                    beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])])
    b = build_engine(w, Fake(), s)
    o, updater = b.ontology, b.updater
    ok = True
    # 각성(비가역) 기계추출 → narrative_inferred(작가 확정 전 비구속) — death 와 동일 비대칭, 장르 불문
    _c, _n, tl, _e = updater.apply({"state_changes": [{"id": "hero", "attr": "각성", "value": "각성"}]}, o, 3)
    aw = [t for t in tl if t.attr == "각성"]
    ok &= len(aw) == 1 and aw[0].value == "각성" and aw[0].trust_tier == "narrative_inferred"
    # 각성→미각성(비가역 이탈) → 모순(미적용)
    _c2, _n2, tl2, _e2 = updater.apply({"state_changes": [{"id": "hero", "attr": "각성", "value": "미각성"}]}, o, 5)
    ok &= any(c.op == "contradiction" for c in _c2) and len(tl2) == 0
    # death 는 하드코딩이 아니라 기본 status 의 한 인스턴스 — status 미선언인데도 terminal 룰 생성
    ok &= "status_dead_acting" in {r.rule_id for r in b.checker.rule_engine.rules}
    # 회귀(reversal) 세계: 사망자 부활이 모순이 아님(비가역 이탈 허용)
    w2 = WorldConfig(title="회귀", genre="현판", allow_state_reversal=True,
                     entities=[EntitySpec(id="hero", name="주인공", base_status="dead")],
                     beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])])
    b2 = build_engine(w2, Fake(), s)
    cR, _n3, _t3, _e3 = b2.updater.apply({"state_changes": [{"id": "hero", "attr": "status", "value": "alive"}]},
                                         b2.ontology, 1)
    ok &= not any(c.op == "contradiction" for c in cR)
    print(f"[{'OK' if ok else 'FAIL'}] L 일반 생애주기: 각성(비가역)=narrative_inferred·이탈=모순·death=데이터(status_dead_acting)·회귀부활허용")
    return ok


if __name__ == "__main__":
    results = [test_story_so_far(), test_prompt_includes_sofar(), test_ontology_comprehensive(),
               test_machine_death_nonbinding(), test_generic_lifecycle()]
    print("\nA+B 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
