# -*- coding: utf-8 -*-
"""OV-3 propose 증거 강제 — state_change/relation 의 본문 인용(evidence) 실재만 유지(환각·유령 오결속 폐기). LLM 0콜."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.engine.ontology import Ontology, Entity
from novelcopilot.engine.ontology_updater import OntologyUpdater


class _A:
    def __init__(self, key, kind, vocab=None, states=None):
        self.key, self.kind, self.vocab, self.states = key, kind, vocab or [], states or []


class _Vocab:
    _items = [_A("affiliation", "categorical", vocab=["빛의탑"]), _A("rank", "numeric")]
    def values(self): return self._items
    def state_specs(self): return []


class _Prov:
    def __init__(self, ret): self.ret = ret
    def chat_json(self, m, temperature=0.0, schema=None): return self.ret   # OV-5: schema kwarg 수용(하위호환)


class _Bus:
    def __init__(self): self.events = []
    def emit(self, *a, **k): self.events.append((a, k))


TEXT = "강레오가 빛의탑에 정식으로 가입했다. 그는 김철수와 형제 사이임이 밝혀졌다."


def _onto():
    o = Ontology(_Vocab())
    o.add(Entity(id="leo", name="강레오", etype="character", attrs={}))
    o.add(Entity(id="cheol", name="김철수", etype="character", attrs={}))
    return o


def _propose(ret):
    bus = _Bus()
    out = OntologyUpdater(_Prov(ret), _Vocab(), bus).propose(TEXT, _onto(), 5)
    return out, bus


def test_keeps_evidenced_drops_hallucinated_state():
    out, bus = _propose({"state_changes": [
        {"id": "leo", "attr": "affiliation", "value": "빛의탑", "evidence": "강레오가 빛의탑에 정식으로 가입했다"},
        {"id": "leo", "attr": "rank", "value": "S", "evidence": "존재하지 않는 인용 문구입니다요"},
    ]})
    vals = [c.get("value") for c in out["state_changes"]]
    assert "빛의탑" in vals and "S" not in vals            # 실재 인용만 유지
    assert any(k.get("kind") == "state_change" for _, k in bus.events)   # 폐기 가시화(silent 아님)


def test_keeps_evidenced_drops_hallucinated_relation():
    out, _ = _propose({"relations": [
        {"src": "leo", "dst": "cheol", "rel_id": "sibling_of", "evidence": "그는 김철수와 형제 사이임이 밝혀졌다"},
        {"src": "leo", "dst": "x", "rel_id": "enemy_of", "evidence": "허구의 근거 문장이야"},
    ]})
    assert [r["rel_id"] for r in out["relations"]] == ["sibling_of"]


def test_valueless_state_bypasses_filter():
    # 값 없는 state_change 는 인용 요구 생략(어차피 apply 서 skip) → 필터가 떨구지 않음
    out, _ = _propose({"state_changes": [{"id": "leo", "attr": "rank", "value": None, "evidence": ""}]})
    assert len(out["state_changes"]) == 1


def test_short_quote_rejected():
    # 8자 미만 인용은 우연일치 방지로 미실재 취급 → 폐기
    out, _ = _propose({"relations": [{"src": "leo", "dst": "cheol", "rel_id": "ally_of", "evidence": "가입"}]})
    assert out["relations"] == []


def test_new_entities_not_evidence_filtered():
    # 신규 엔티티는 증거 필터 대상 아님(이름 자체가 본문 근거) — 그대로 통과
    out, _ = _propose({"new_entities": [{"name": "박도철", "etype": "character"}]})
    assert len(out.get("new_entities", [])) == 1
