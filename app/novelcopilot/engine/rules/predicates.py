# -*- coding: utf-8 -*-
"""술어 평가기 — Strategy + Registry. 기존 rules.evaluate 의 if/elif 체인을 제거.

각 predicate_kind 는 독립 평가기 클래스. 새 술어 = 새 클래스 + register 한 줄.
입력 claim 은 LLM 추출 결과 → 여기서 나오는 위반은 quasi/semantic(순수 결정론은 ontology_internal_check).
판정 자체는 코드(LLM 미개입) = 비대칭의 준결정론 절반.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Callable

from ...domain.types import RuleSpec, Violation, SignalGrade
from ..vocabulary import Vocabulary, OTHER


def _norm(v):
    return None if v in (None, "null", "") else str(v).strip()


def _is_true(v) -> bool:
    """JSON bool 뿐 아니라 문자열화 bool('true'/'1'/'yes')도 참으로 — 추출기 표면변이에 강건(실위반 묵과 방지)."""
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "y"))


class PredicateEvaluator(ABC):
    kind: str = ""

    @abstractmethod
    def evaluate(self, rule: RuleSpec, claim: dict, ontology, chapter: int,
                 vocab: Vocabulary) -> list[Violation]:
        ...


class CategoricalEq(PredicateEvaluator):
    kind = "categorical_eq"

    def evaluate(self, rule, claim, ontology, chapter, vocab):
        eid, name = claim["__id"], claim["__name"]
        attr = rule.params["attr"]
        tv = _norm(claim.get(attr))
        if tv is None:
            return []
        cv = ontology.state_as_of(eid, attr, chapter)
        if cv is None:
            return []
        label = vocab.label(attr)
        if tv == OTHER:   # 통제어휘 밖 명시언급 → 침묵통과 금지(semantic uncertain)
            return [Violation(entity=name, kind=f"uncertain({attr})", grade=SignalGrade.SEMANTIC,
                              canon=f"{label}={cv}", text=f"={OTHER}(매핑불가)",
                              evidence="통제어휘 밖 명시 언급 — 사람 확인 필요")]
        if tv != str(cv).strip():
            return [Violation(entity=name, kind="field_value", grade=rule.grade,
                              canon=f"{label}={cv}", text=f"={tv}",
                              evidence=f"정규화값 '{tv}' ≠ 캐논 '{cv}'")]
        return []


class NumericMonotone(PredicateEvaluator):
    kind = "numeric_monotone"

    def evaluate(self, rule, claim, ontology, chapter, vocab):
        eid, name = claim["__id"], claim["__name"]
        attr = rule.params["attr"]
        direction = rule.params.get("direction", "non_decreasing")
        tv = claim.get(attr)
        if tv in (None, "null", "") or isinstance(tv, bool):   # bool 은 숫자 비교 대상 아님(int(True)=1 오비교 방지)
            return []
        cv = ontology.state_as_of(eid, attr, chapter)
        if cv is None:
            return []
        label = vocab.label(attr)
        try:
            ti, ci = int(tv), int(cv)
        except (ValueError, TypeError):
            return []
        bad = (direction == "non_decreasing" and ti < ci) or (direction == "non_increasing" and ti > ci)
        if bad:
            arrow = "↓" if direction == "non_decreasing" else "↑"
            return [Violation(entity=name, kind=f"field_value({attr}{arrow})", grade=rule.grade,
                              canon=f"{label}={cv}", text=f"={tv}",
                              evidence=f"단조({direction}) 위반 (숫자비교=결정론, 추출=LLM)")]
        return []


class TimelineState(PredicateEvaluator):
    kind = "timeline_state"

    def evaluate(self, rule, claim, ontology, chapter, vocab):
        eid, name = claim["__id"], claim["__name"]
        p = rule.params
        # binding(ground_truth) 상태만 하드게이트 — 기계추출 사망(narrative_inferred)으로 캐릭터를 영구 봉인하지 않음(비대칭)
        if (ontology.binding_state_as_of(eid, p["attr"], chapter) == p["forbidden_state"]
                and claim.get("appears_as") == p["forbidden_appearance"]):
            return [Violation(entity=name, kind="state_timeline", grade=rule.grade,
                              canon=f"{chapter}화 시점 {p['forbidden_state']}",
                              text="현재 시점 직접 행동/대사",
                              evidence="appears_as=present_acting (시점=결정론, 등장형태=LLM추출)")]
        return []


class WorldRuleFlag(PredicateEvaluator):
    kind = "worldrule_flag"

    def evaluate(self, rule, claim, ontology, chapter, vocab):
        # 등록된 세계규칙은 활성으로 본다. 위반 여부는 '추출기가 세운 flag'가 결정.
        # (구버전의 'keyword ∈ rule_text' 자기참조 게이트 제거 — promote 된 규칙이 제목≠본문이라 영구 비활성되던 버그 #1/#7)
        p = rule.params
        if _is_true(claim.get(p["flag"])):
            return [Violation(entity=claim["__name"], kind=f"worldrule({p['flag']})", grade=rule.grade,
                              canon="세계규칙 위반", text=f"{p['flag']} 묘사", evidence=f"{p['flag']}=true")]
        return []


# ---- 레지스트리: predicate_kind → 평가기 ----
_REGISTRY: dict[str, PredicateEvaluator] = {}


def register(evaluator: PredicateEvaluator) -> None:
    _REGISTRY[evaluator.kind] = evaluator


def get(kind: str) -> PredicateEvaluator | None:
    return _REGISTRY.get(kind)


for _e in (CategoricalEq(), NumericMonotone(), TimelineState(), WorldRuleFlag()):
    register(_e)
