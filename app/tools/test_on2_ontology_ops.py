# -*- coding: utf-8 -*-
"""ON-2 회귀 — U1 갱신 루프 연산·U7 승인 표기. LLM 0.

계약: 병합은 별칭 승계+재지향+edge_id 재작성까지, 검증은 advisory만(수정 0),
TimelineEntry 기본 provenance=machine·작가 경로만 author, 구 JSON 하위호환(필드 부재 로드)."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.engine.ontology_ops import merge_entity, remap_chapters, validate_timeline
from novelcopilot.domain.world import TimelineEntry


def _state():
    canon = NS(id="jiyeon", name="하지연", aliases=["지연"])
    dup = NS(id="npc_x", name="하 요원", aliases=["요원님"])
    world = NS(entities=[canon])
    tl = [NS(entity_id="npc_x", attr="truth_awareness", value="직시", eff_from=13, reason="12화 동적 감지", trust_tier="ground_truth"),
          NS(entity_id="jiyeon", attr="truth_awareness", value="직시", eff_from=13, reason="12화 동적 감지", trust_tier="ground_truth"),
          NS(entity_id="ghost", attr="status", value="alive", eff_from=99, reason="", trust_tier="ground_truth")]
    edges = [NS(src="npc_x", dst="jiyeon", edge_id="npc_x->jiyeon", src_id=None, dst_id=None)]
    chapters = [NS(chapter=n) for n in range(1, 13)]
    return NS(world=world, runtime_entities=[dup], runtime_timeline=tl,
              runtime_edges=edges, chapters=chapters)


def test_merge_entity_full():
    st = _state()
    r = merge_entity(st, "npc_x", "jiyeon")
    assert "하 요원" in st.world.entities[0].aliases          # 별칭 승계(이름 포함)
    assert not st.runtime_entities                             # 중복 제거
    ids = {ev.entity_id for ev in st.runtime_timeline}
    assert "npc_x" not in ids                                  # 재지향
    assert len([e for e in st.runtime_timeline if e.attr == "truth_awareness"]) == 1   # 동일 키 dedup
    assert st.runtime_edges[0].src == "jiyeon" and "npc_x" not in st.runtime_edges[0].edge_id
    assert r["merged"] == "npc_x"


def test_validate_timeline_advisory_only():
    st = _state()
    before = [ev.eff_from for ev in st.runtime_timeline]
    probs = validate_timeline(st)
    kinds = {p["kind"] for p in probs}
    assert "고아_개체" in kinds                                # ghost
    assert "eff_범위" in kinds                                 # eff 99 > 최종화+1
    assert [ev.eff_from for ev in st.runtime_timeline] == before   # 수정 0(advisory)


def test_validate_reason_convention():
    st = _state()
    st.runtime_timeline.append(NS(entity_id="jiyeon", attr="scale_stage", value="도시",
                                  eff_from=9, reason="13화 동적 감지", trust_tier="ground_truth"))
    probs = validate_timeline(st)
    assert any(p["kind"] == "규약_불일치" for p in probs)      # 라벨 13화 vs eff 9


def test_remap_chapters():
    st = _state()
    r = remap_chapters(st, {13: 12})
    assert r["remapped"] == 2
    assert all(ev.eff_from != 13 for ev in st.runtime_timeline)


def test_timeline_provenance_default_and_compat():
    ev = TimelineEntry(entity_id="a", attr="k", value="v", eff_from=2)
    assert ev.provenance == ["machine"]                        # 기본 기계 표기
    old = TimelineEntry.model_validate({"entity_id": "a", "attr": "k", "value": "v", "eff_from": 2})
    assert old.provenance == ["machine"]                       # 구 JSON(필드 부재) 하위호환
    au = TimelineEntry(entity_id="a", attr="k", value="v", eff_from=2, provenance=["author"])
    assert "author" in au.provenance


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("전체 통과")
