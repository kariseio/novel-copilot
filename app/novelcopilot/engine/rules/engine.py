# -*- coding: utf-8 -*-
"""룰 엔진 — RuleSpec 레지스트리(데이터) × claims → Violation[]. 술어 평가는 Strategy 에 위임."""
from __future__ import annotations
from ...domain.types import RuleSpec, Violation
from ..vocabulary import Vocabulary
from . import predicates


class RuleEngine:
    def __init__(self, rules: list[RuleSpec], vocab: Vocabulary):
        self.rules = rules
        self.vocab = vocab

    def remove_rule(self, rule_id: str) -> None:   # demote 역연산
        self.rules = [r for r in self.rules if r.rule_id != rule_id]

    def evaluate(self, claims: list[dict], ontology, chapter: int) -> list[Violation]:
        out: list[Violation] = []
        for claim in claims:
            eid = claim.get("id")
            ent = ontology.entities.get(eid)
            if not ent:
                continue
            enriched = {**claim, "__id": eid, "__name": ent.name}
            for rule in self.rules:
                evaluator = predicates.get(rule.predicate_kind)
                if evaluator is None:
                    continue
                out.extend(evaluator.evaluate(rule, enriched, ontology, chapter, self.vocab))
        return out
