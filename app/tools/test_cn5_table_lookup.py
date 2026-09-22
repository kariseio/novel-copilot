# -*- coding: utf-8 -*-
"""CN-5 열거규칙 flat-lookup — 일반 table_lookup 술어(세계별 코드0)·구조화 선언·고신뢰 주입·증거강제·advisory. 실 LLM 0콜.

핵심: 판정=코드(선언된 표 대조), 키·값 추출=LLM. 미선언 키는 범위 밖(과잉게이트 금지). SEMANTIC=비차단 advisory."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.world import WorldConfig, WorldRuleSpec
from novelcopilot.domain.types import SignalGrade
from novelcopilot.engine.rules.predicates import table_lookup
from novelcopilot.engine.rules import RuleEngine
from novelcopilot.engine.extractor import ClaimExtractor
from novelcopilot.engine.checker import Checker
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.engine.factory import build_ontology


TABLE = {"첫째": "머리", "둘째": "가슴", "다섯째": "다리"}
RULE = WorldRuleSpec(rule_id="needles", text="다섯 침은 순서대로 정해진 부위를 노린다.", table=TABLE)


# ---- A. 순수 술어 table_lookup ----
def test_predicate_match_and_mismatch():
    assert table_lookup(TABLE, "첫째", "머리") is None          # 일치 → 위반 없음
    assert table_lookup(TABLE, "다섯째", "심장") == "다리"       # 불일치 → 캐논값 반환
    assert table_lookup(TABLE, "다섯째", " 다리 ") is None       # 공백 정규화 → 일치


def test_predicate_unknown_key_and_null():
    assert table_lookup(TABLE, "일곱째", "발") is None           # 미선언 키 → 범위 밖(침묵)
    assert table_lookup(TABLE, None, "머리") is None
    assert table_lookup(TABLE, "첫째", None) is None
    assert table_lookup(TABLE, "첫째", "") is None


# ---- B. Checker._table_contradictions (advisory·증거강제) ----
def _checker(rules):
    ext = ClaimExtractor(provider=None, vocab=None, world_rules=rules)
    return Checker(ext, RuleEngine([], None))


TEXT = "그는 다섯째 침을 뽑아 상대의 심장을 정확히 노렸다. 검은 그림자가 방을 가로질렀다."


def test_contradiction_flagged_semantic():
    ck = _checker([RULE])
    tc = [{"rule_id": "needles", "key": "다섯째", "value": "심장",
           "evidence": "다섯째 침을 뽑아 상대의 심장을 정확히 노렸다"}]
    viols = ck._table_contradictions(tc, TEXT, 5)
    assert len(viols) == 1 and viols[0].grade == SignalGrade.SEMANTIC
    assert not viols[0].is_hard and "다리" in viols[0].canon      # advisory·비차단, 캐논 노출


def test_agreement_no_flag():
    ck = _checker([RULE])
    tc = [{"rule_id": "needles", "key": "첫째", "value": "머리",
           "evidence": "첫째 침은 머리를 겨눈다고 스스로 되뇌었다 그러나"}]
    assert ck._table_contradictions(tc, "첫째 침은 머리를 겨눈다고 스스로 되뇌었다 그러나 손이 떨렸다", 5) == []


def test_unknown_rule_id_skipped():
    ck = _checker([RULE])
    tc = [{"rule_id": "없는규칙", "key": "다섯째", "value": "심장", "evidence": "다섯째 침을 뽑아 상대의 심장을"}]
    assert ck._table_contradictions(tc, TEXT, 5) == []


def test_unknown_key_skipped():
    ck = _checker([RULE])
    tc = [{"rule_id": "needles", "key": "여섯째", "value": "심장", "evidence": "다섯째 침을 뽑아 상대의 심장을 노렸다"}]
    assert ck._table_contradictions(tc, TEXT, 5) == []           # 미선언 키 → 범위 밖


def test_missing_evidence_skipped():
    ck = _checker([RULE])
    tc = [{"rule_id": "needles", "key": "다섯째", "value": "심장", "evidence": "짧음"}]   # 8자 미만·미실재
    assert ck._table_contradictions(tc, TEXT, 5) == []
    assert ck._table_contradictions([], TEXT, 5) == []           # 빈 입력 무해
    assert ck._table_contradictions(None, TEXT, 5) == []


# ---- C. 추출 스키마: 표 있을 때만 접붙임(없으면 비용·스키마 무변경) ----
class FakeVocab:
    categorical_keys: list = []
    numeric_keys: list = []
    def categorical(self, k): return []
    def label(self, k): return k
    def state_specs(self): return []


def test_schema_conditional_on_tables():
    with_tbl = ClaimExtractor(None, FakeVocab(), [RULE])
    assert "table_claims" in with_tbl._schema()
    hints = with_tbl._rule_hints()
    assert "[열거표]" in hints and "needles" in hints
    assert "머리" in hints and "다리" in hints                    # MED-2: 값 목록 제시(LLM 이 값도 캐논 토큰으로 정규화)

    no_tbl = ClaimExtractor(None, FakeVocab(), [WorldRuleSpec(rule_id="r", text="일반 규칙")])
    assert "table_claims" not in no_tbl._schema()                # 표 없으면 섹션 미접붙임
    assert no_tbl._tabled() == []


# ---- E. 고신뢰 주입: 표가 세계규칙 텍스트에 직렬화 ----
def test_injection_serializes_table():
    w = WorldConfig(title="t", synopsis="s", world_rules=[RULE])
    o = build_ontology(w, Vocabulary(w.attributes))
    joined = " ".join(o.rules)
    assert "다섯째=다리" in joined and "첫째=머리" in joined       # 집필이 정확한 대응 보유(예방)
    assert RULE.text in joined                                    # 원 규칙 텍스트도 유지


def test_demote_removes_suffixed_rule():
    # MED-1: demote 는 원형 텍스트로 remove_rule 호출 → 주입된 접미사 규칙이 exact-match 실패로 잔존하던 결함 차단
    w = WorldConfig(title="t", synopsis="s", world_rules=[RULE])
    o = build_ontology(w, Vocabulary(w.attributes))
    assert any("[대응표:" in r for r in o.rules)
    o.remove_rule(RULE.text)                                      # copilot._demote_rule 이 넘기는 원형 텍스트
    assert o.rules == []                                          # orphan 고신뢰 캐논 잔존 0

    # 일반(표 없는) 규칙도 정확히 제거(회귀 방지)
    o2 = build_ontology(WorldConfig(title="t", synopsis="s",
                                    world_rules=[WorldRuleSpec(rule_id="p", text="평범한 규칙")]),
                        Vocabulary([]))
    o2.remove_rule("평범한 규칙")
    assert o2.rules == []
