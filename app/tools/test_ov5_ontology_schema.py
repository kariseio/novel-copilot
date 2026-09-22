# -*- coding: utf-8 -*-
"""OV-5 회귀 — 온톨로지 제안 스키마 강제·침묵 {} 강등 차단·검증 축 편입. LLM 0.

계약:
① propose 는 지원 프로바이더에 PROPOSAL_SCHEMA 를 넘긴다(미지원 프로바이더는 base 가 무시 — 하위호환).
② 제안 콜 실패는 {} 가 아니라 {"stage_failed": 예외명} 정직 표식으로 반환(침묵 강등 금지).
③ OntologyChange.op="stage_failure" 허용(레코드 영속 표식) + _summ_ontology 가 실패를 가시화.
④ 수치 문자열 value 는 int 로 복원(스키마 문자열 강제 ↔ 단조·비교 정책의 수치 판정 보존).
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.llm.base import LLMProvider
from novelcopilot.engine.ontology_updater import OntologyUpdater, PROPOSAL_SCHEMA
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.domain.types import OntologyChange
from novelcopilot.engine.verification import _summ_ontology, MISSING


class _Bus:
    def __init__(self):
        self.events = []
    def emit(self, *a, **k):
        self.events.append((a, k))


class _SchemaSpy(LLMProvider):
    """schema kwarg 수신 확인 + 정상 제안 반환."""
    def __init__(self, payload):
        super().__init__()
        self.got_schema = None
        self._payload = payload
    def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
        return "{}"
    def chat_json(self, messages, *, temperature=0.0, max_tokens=None, schema=None):
        self.got_schema = schema
        return dict(self._payload)
    def embed(self, texts):
        return [[0.0] for _ in texts]


class _Boom(LLMProvider):
    def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
        raise RuntimeError("boom")
    def embed(self, texts):
        return [[0.0] for _ in texts]


def _vocab():
    return Vocabulary(attributes=[])


def _ont():
    return NS(entities={}, rel_catalog={}, entity_types={}, alias_map=lambda: {})


def test_schema_wellformed_and_passed():
    """① 스키마 additionalProperties 전면 봉쇄 + propose 가 스키마를 프로바이더에 전달."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required") or []) == set((node.get("properties") or {}).keys())
            for v in node.values():
                walk(v)
    walk(PROPOSAL_SCHEMA)
    p = _SchemaSpy({"new_entities": [], "state_changes": [], "relations": [], "new_settings": []})
    upd = OntologyUpdater(p, _vocab(), _Bus())
    res = upd.propose("본문.", _ont(), 3)
    assert p.got_schema is PROPOSAL_SCHEMA
    assert res.get("state_changes") == []


def test_failure_returns_honest_marker():
    """② 실패 = stage_failed 표식(빈 {} 세탁 금지) + 관측 이벤트."""
    bus = _Bus()
    upd = OntologyUpdater(_Boom(), _vocab(), bus)
    res = upd.propose("본문.", _ont(), 2)
    assert res == {"stage_failed": "RuntimeError"}
    assert any(a[1] == "parse_failure" for a, k in bus.events)


def test_stage_failure_op_and_axis():
    """③ 레코드 표식 허용 + 검증 축 가시화(빈 기록은 MISSING)."""
    c = OntologyChange(op="stage_failure", entity="(온톨로지 제안)", detail="제안 콜 실패",
                       applied=False, reason="ValueError", severity="review")
    ax = _summ_ontology([c])
    assert ax == {"changes": 0, "applied": 0, "stage_failed": True}
    ok = OntologyChange(op="new_entity", entity="유채원", detail="", applied=True)
    ax2 = _summ_ontology([ok.model_dump()])
    assert ax2 == {"changes": 1, "applied": 1, "stage_failed": False}
    assert _summ_ontology([]) == MISSING and _summ_ontology(None) == MISSING


def test_numeric_string_value_restored():
    """④ '48500' → 48500 (단조·수치 비교 정책 보존)."""
    text = "지갑에 남은 돈은 사만 팔천오백 원이었다."
    p = _SchemaSpy({"new_entities": [], "relations": [], "new_settings": [],
                    "state_changes": [{"id": "j", "attr": "money_won", "value": "48500",
                                       "evidence": "남은 돈은 사만 팔천오백 원이었다", "note": ""}]})
    upd = OntologyUpdater(p, _vocab(), _Bus())
    res = upd.propose(text, _ont(), 3)
    assert res["state_changes"][0]["value"] == 48500


def test_base_chat_json_ignores_schema_kwarg():
    """① 하위호환 — base(비지원 프로바이더) 는 schema 를 무시하고 기존 경로."""
    class _Plain(LLMProvider):
        def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
            return '{"ok": 1}'
        def embed(self, texts):
            return [[0.0] for _ in texts]
    assert _Plain().chat_json([{"role": "user", "content": "x"}], schema={"type": "object"}) == {"ok": 1}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("전체 통과")
