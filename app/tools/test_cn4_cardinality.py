# -*- coding: utf-8 -*-
"""CN-4 혈연/배타 카디널리티 결정론 검출 — 어휘사전0·advisory·비차단. LLM 0콜."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.engine.ontology import Ontology, Entity
from novelcopilot.engine.ontology_updater import OntologyUpdater
from novelcopilot.domain.types import RelationEdge
from novelcopilot.domain.relations import merged_catalog

_cv = OntologyUpdater._cardinality_violation


class _V:  # 최소 vocab 스텁(카디널리티 검출은 vocab 미사용)
    pass


def _onto():
    o = Ontology(_V())
    o.rel_catalog = merged_catalog(None)          # REL_CATALOG(married_to=1:1, sibling_of 대칭 등)
    for i, n, card in [("hero", "영웅", {"sibling_of": 0}), ("x", "엑스", {}),
                       ("w1", "배우1", {}), ("w2", "배우2", {}), ("y", "와이", {})]:
        o.add(Entity(id=i, name=n, etype="character", attrs={}, cardinality=card))
    return o


def test_only_child_flags_first_sibling():
    o = _onto()
    v = _cv("hero", "x", "sibling_of", 3, o)
    assert v is not None and v.applied is False and "카디널리티" in v.detail   # 외동=상한0 → 첫 형제부터


def test_unconstrained_relation_ok():
    assert _cv("x", "y", "friend_of", 3, _onto()) is None                    # 상한 없음·N:N → None


def test_first_marriage_ok():
    assert _cv("x", "y", "married_to", 3, _onto()) is None                   # 신규 쌍 첫 결혼 → None


def test_second_spouse_flagged():
    o = _onto()
    o.add_edge(RelationEdge(edge_id="m1", rel_id="married_to", src_id="hero", dst_id="w1", eff_from=1))
    v = _cv("hero", "w2", "married_to", 3, o)
    assert v is not None and "1:1" in v.detail and v.applied is False        # 2번째 배우자(1:1 배타)


def test_symmetric_partner_already_bound():
    o = _onto()
    o.add_edge(RelationEdge(edge_id="m1", rel_id="married_to", src_id="hero", dst_id="w1", eff_from=1))
    assert _cv("x", "w1", "married_to", 3, o) is not None                    # w1 이 이미 결혼(대칭 셈)


def test_pov_edges_excluded():
    o = _onto()
    o.add_edge(RelationEdge(edge_id="p1", rel_id="sibling_of", src_id="hero", dst_id="y", eff_from=1, pov="hero"))
    # pov(주관) 엣지는 개수에서 제외 → 외동(0)은 여전히 첫 non-pov sibling 에서 위반
    assert _cv("hero", "x", "sibling_of", 3, o) is not None


def test_as_of_filter():
    o = _onto()
    o.add_edge(RelationEdge(edge_id="m1", rel_id="married_to", src_id="hero", dst_id="w1", eff_from=5))
    assert _cv("hero", "w2", "married_to", 3, o) is None                     # ch3 시점엔 eff_from=5 엣지 미활성
