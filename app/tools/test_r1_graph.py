# -*- coding: utf-8 -*-
"""R1 검증 — 속성그래프(엣지) 엔진 + 작가 직접입력 서비스 + 그래프 payload (LLM 0콜).
실행: PYTHONPATH=app python tools/test_r1_graph.py
"""
from __future__ import annotations
import sys, tempfile, time
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.world import (WorldConfig, AttributeSpec, EntitySpec, EntityTypeSpec,
                                       TimelineEntry, Beat)
from novelcopilot.domain.relations import RelationSpec, REL_CATALOG
from novelcopilot.domain.types import RelationEdge
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.engine.factory import build_engine
from novelcopilot.llm.base import LLMProvider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService


class FakeProvider(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    return WorldConfig(
        title="그래프테스트", genre="현판",
        attributes=[AttributeSpec(key="eye_color", label="눈 색", kind="categorical",
                                  vocab=["붉은색", "금색"], mutable=False)],
        entities=[
            EntitySpec(id="hero", name="주인공", etype="character", attrs={"eye_color": "붉은색"}),
            EntitySpec(id="rival", name="라이벌", etype="character", attrs={"eye_color": "금색"}),
            EntitySpec(id="guild", name="길드", etype="faction"),
        ],
        relations=[],
        seed_edges=[RelationEdge(edge_id="member_of:hero->guild:1", rel_id="member_of",
                                 src_id="hero", dst_id="guild", eff_from=1, provenance=["seed"])],
        timeline=[TimelineEntry(entity_id="rival", attr="status", value="dead", eff_from=3)],
        beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])],
    )


def test_engine_edges() -> bool:
    s = get_settings()
    b = build_engine(_world(), FakeProvider(), s)
    o = b.ontology
    ok = True
    # 카탈로그/타입 시드
    ok &= "member_of" in o.rel_catalog and "character" in o.entity_types
    # 시드 엣지 활성
    ok &= o.edge_state_as_of("hero", "guild", "member_of", 1) is not None
    ok &= len(o.edges_as_of(5)) == 1
    ok &= len(o.neighbors("hero", 5, "out")) == 1 and len(o.neighbors("guild", 5, "in")) == 1
    # canon_relations → ground_truth OntologyFact
    cr = o.canon_relations(["hero"], 1)
    ok &= len(cr) == 1 and cr[0].entity == "주인공" and "소속" in cr[0].attr_label and cr[0].value == "길드"
    # 시드 엣지는 모순 없음
    base = [v for v in o.ontology_internal_check() if v.kind.startswith("edge_")]
    ok &= len(base) == 0
    # self-loop / dangling / post-death 검출(LLM0콜)
    o.add_edge(RelationEdge(rel_id="ally_of", src_id="hero", dst_id="hero", eff_from=1))      # self
    o.add_edge(RelationEdge(rel_id="ally_of", src_id="hero", dst_id="ghost", eff_from=1))     # dangling
    o.add_edge(RelationEdge(rel_id="ally_of", src_id="rival", dst_id="hero", eff_from=4))     # post-death(rival 3화 사망)
    kinds = {v.kind for v in o.ontology_internal_check() if v.kind.startswith("edge_")}
    ok &= {"edge_self_loop", "edge_dangling", "edge_post_death"} <= kinds
    print(f"[{'OK' if ok else 'FAIL'}] 엔진 엣지: catalog/seed/as_of/canon_relations/3검사 detect={sorted(kinds)}")
    return ok


def test_service_author_input() -> bool:
    s = get_settings()
    tmp = Path(tempfile.mkdtemp())
    repo = FilesystemProjectRepository(tmp)
    svc = CopilotService(s, repo)
    state = ProjectState(id="t1", seed=ProjectSeed(premise="x"), world=_world(),
                         created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    repo.save(state)
    ok = True
    # 작가 노드 추가(미등록 타입도 허용 + fallback)
    r = svc.add_entity("t1", "흑막세력", etype="faction")
    ok &= r and r["created"]
    # 유효 관계
    r2 = svc.add_relation("t1", "rival", "guild", "member_of", eff_from=1)
    ok &= r2 and r2["created"]
    # 개방형: 미등록 자유 타입도 그대로 생성(카탈로그 FK 강제 폐기)
    rfree = svc.add_relation("t1", "hero", "guild", "사적_인연")
    ok &= bool(rfree and rfree["created"])
    # 사망 후 관계 → 결정론 게이트 reject (rival 3화 사망, eff_from=5)
    try:
        svc.add_relation("t1", "rival", "guild", "ally_of", eff_from=5); ok = False
    except ValueError: pass
    # opt-in 제약: 선언된 끝점 타입은 강제 — member_of 도착은 세력/조직만(인물 dst → reject)
    try:
        svc.add_relation("t1", "hero", "rival", "member_of"); ok = False
    except ValueError: pass
    # 영속·재수화 동등성: 재로드한 세션에 엣지 보존
    svc.sessions.evict("t1")
    snap = svc.ontology_snapshot("t1")
    g = snap["graph"]
    node_ids = {n["id"] for n in g["nodes"]}
    ok &= "흑막세력" not in node_ids and any(n["name"] == "흑막세력" for n in g["nodes"])  # name 보존, id는 슬러그
    ok &= len(g["edges"]) >= 2 and any(e["rel_id"] == "member_of" and e["src"] == "rival" for e in g["edges"])
    ok &= len(g["relations"]) == len(REL_CATALOG) and len(g["types"]) >= 5
    print(f"[{'OK' if ok else 'FAIL'}] 서비스 작가입력: add_entity/add_relation/타입검증/사망게이트/영속 "
          f"nodes={len(g['nodes'])} edges={len(g['edges'])}")
    return ok


def test_review_fixes() -> bool:
    s = get_settings()
    # #1 회차-국소 엣지검사 + #6(1) base_status dead 게이트
    w = WorldConfig(title="fix", genre="x",
                    entities=[EntitySpec(id="hero", name="H"), EntitySpec(id="rival", name="R"),
                              EntitySpec(id="ghost", name="G", base_status="dead")],
                    timeline=[TimelineEntry(entity_id="rival", attr="status", value="dead", eff_from=3)],
                    beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])])
    o = build_engine(w, FakeProvider(), s).ontology
    o.add_edge(RelationEdge(edge_id="ally_of:hero->rival:5", rel_id="ally_of",
                            src_id="hero", dst_id="rival", eff_from=5))   # 미래 엣지(rival 3화 사망)
    scope_ok = ("edge_post_death" not in {v.kind for v in o.ontology_internal_check(2)}      # 2화: 미활성→오염 X
                and "edge_post_death" in {v.kind for v in o.ontology_internal_check(5)}       # 5화: 활성→탐지
                and "edge_post_death" in {v.kind for v in o.ontology_internal_check()})       # 전역 감사: 탐지
    o.add_edge(RelationEdge(edge_id="ally_of:hero->ghost:2", rel_id="ally_of",
                            src_id="hero", dst_id="ghost", eff_from=2))   # base_status dead(=1) 이후
    base_ok = any(v.kind == "edge_post_death" and v.entity == "G" for v in o.ontology_internal_check(2))
    print(f"[{'OK' if scope_ok and base_ok else 'FAIL'}] #1 회차국소(2화 clean/5화 탐지) + #6 base_status dead")

    # #3/#5 canon_relations dedup (동일 src,dst,rel 의 eff_from 2개 → 사실 1건)
    o2 = build_engine(_world(), FakeProvider(), s).ontology
    o2.add_edge(RelationEdge(edge_id="ally_of:hero->rival:1", rel_id="ally_of", src_id="hero", dst_id="rival", eff_from=1))
    o2.add_edge(RelationEdge(edge_id="ally_of:hero->rival:2", rel_id="ally_of", src_id="hero", dst_id="rival", eff_from=2))
    dedup_ok = len([f for f in o2.canon_relations(["hero"], 5) if "동맹" in f.attr_label]) == 1
    print(f"[{'OK' if dedup_ok else 'FAIL'}] #3/#5 canon_relations dedup")

    # #8 seed edge 검증(무효 드롭+가시화)
    w3 = WorldConfig(title="seed", genre="x",
                     entities=[EntitySpec(id="a", name="A"), EntitySpec(id="b", name="B")],
                     seed_edges=[RelationEdge(rel_id="ally_of", src_id="a", dst_id="b", eff_from=1),
                                 RelationEdge(rel_id="bogus", src_id="a", dst_id="b", eff_from=1),
                                 RelationEdge(rel_id="ally_of", src_id="a", dst_id="ghost", eff_from=1)],
                     beats=[Beat(chapter=1, title="t", summary="s", entities=["a"])])
    b3 = build_engine(w3, FakeProvider(), s)
    # 개방형: ally_of(a→b)·자유타입 bogus(a→b) 둘 다 적재, a→ghost(끝점부재)만 드롭+경고
    seed_ok = len(b3.ontology.edges) == 2 and any(e["event"] == "invalid_seed_edge" for e in b3.event_bus.buffer)
    print(f"[{'OK' if seed_ok else 'FAIL'}] #8 seed 검증: 자유타입 포함 적재 {len(b3.ontology.edges)}(=2), 끝점부재만 무효경고")
    return scope_ok and base_ok and dedup_ok and seed_ok


def test_end_relation() -> bool:
    s = get_settings()
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp()))
    svc = CopilotService(s, repo)
    repo.save(ProjectState(id="t2", seed=ProjectSeed(premise="x"), world=_world(), created_at="t"))
    svc.add_relation("t2", "hero", "rival", "ally_of", eff_from=1)
    r = svc.end_relation("t2", "hero", "rival", "ally_of", eff_to=2)
    svc.sessions.evict("t2")                       # 영속 round-trip 후에도 종료 유지되는지
    o = svc.get_session("t2")[0].bundle.ontology
    ok = (r and r["ended"]
          and o.edge_state_as_of("hero", "rival", "ally_of", 1) is not None      # 1화 활성
          and o.edge_state_as_of("hero", "rival", "ally_of", 2) is None          # 2화부터 종료
          and all("동맹" not in f.attr_label for f in o.canon_relations(["hero"], 2)))
    print(f"[{'OK' if ok else 'FAIL'}] end_relation: eff_to 종료 + 영속 round-trip")
    return ok


def test_genre_generalization() -> bool:
    """장르 일반화 리뷰 수정 — actor 데이터주도(비인간 게이트)·reversal 엣지(부활 후 허용)·카디널리티 1:1(결혼 배타)."""
    s = get_settings()
    # 1) actor 일반화: 커스텀 비인간 actor 타입은 상태/등장 게이트 대상, object 는 아님(character 하드코딩 제거)
    w = WorldConfig(title="sf", genre="SF",
                    entity_types=[EntityTypeSpec(key="ai", label="AI", category="actor"),
                                  EntityTypeSpec(key="ship", label="함선", category="object")],
                    entities=[EntitySpec(id="core", name="코어", etype="ai"),
                              EntitySpec(id="vessel", name="배", etype="ship")],
                    beats=[Beat(chapter=1, title="t", summary="s", entities=["core"])])
    o = build_engine(w, FakeProvider(), s).ontology
    ok = o.is_actor("ai") and not o.is_actor("ship")
    ok &= "core" in o.scan_present_ids("코어가 작동했다") and "vessel" not in o.scan_present_ids("배가 있다")
    # 2) reversal 엣지: gt 사망@3 → gt 부활@5 → 4화 terminal, 6화 비-terminal → 부활 후 관계 허용(이탈 반영)
    w2 = WorldConfig(title="회귀", genre="x", allow_state_reversal=True,
                     entities=[EntitySpec(id="h", name="H"), EntitySpec(id="r", name="R")],
                     timeline=[TimelineEntry(entity_id="r", attr="status", value="dead", eff_from=3),
                               TimelineEntry(entity_id="r", attr="status", value="alive", eff_from=5)],
                     beats=[Beat(chapter=1, title="t", summary="s", entities=["h"])])
    o2 = build_engine(w2, FakeProvider(), s).ontology
    ok &= o2._in_terminal_state("r", 4) and not o2._in_terminal_state("r", 6)
    o2.add_edge(RelationEdge(edge_id="e6", rel_id="ally_of", src_id="h", dst_id="r", eff_from=6, trust_tier="ground_truth"))
    ok &= not any(v.kind == "edge_post_death" for v in o2.ontology_internal_check(6))
    # 3) 카디널리티 1:1: 결혼 배타성(opt-in) — A-B 결혼 후 A-C 결혼 거부
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp()))
    svc = CopilotService(s, repo)
    w3 = WorldConfig(title="로맨스", genre="로맨스",
                     entities=[EntitySpec(id="a", name="A"), EntitySpec(id="b", name="B"), EntitySpec(id="c", name="C")],
                     beats=[Beat(chapter=1, title="t", summary="s", entities=["a"])])
    repo.save(ProjectState(id="rom", seed=ProjectSeed(premise="x"), world=w3, created_at="t"))
    r1 = svc.add_relation("rom", "a", "b", "married_to")
    ok &= bool(r1 and r1["created"])
    try:
        svc.add_relation("rom", "a", "c", "married_to"); ok = False   # A 이미 배우자 있음 → 거부
    except ValueError:
        pass
    print(f"[{'OK' if ok else 'FAIL'}] 장르 일반화: actor 비인간 게이트·reversal 부활 후 관계 허용·카디널리티 1:1 결혼배타")
    return ok


if __name__ == "__main__":
    results = [test_engine_edges(), test_service_author_input(), test_review_fixes(), test_end_relation(),
               test_genre_generalization()]
    print("\nR1 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
