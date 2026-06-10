# -*- coding: utf-8 -*-
"""검증 오케스트레이션 — 신뢰등급대로 합친다.
  (등급1 순수결정론) ontology.ontology_internal_check  ← LLM 0콜
  (준결정론/의미)    extractor.extract → RuleEngine.evaluate
결정론 코어는 위키/RAG 산출물을 입력으로 받지 않는다(비대칭 불변식).
"""
from __future__ import annotations
from dataclasses import dataclass, field

from ..domain.types import Violation, SignalGrade
from .extractor import ClaimExtractor
from .rules import RuleEngine


@dataclass
class CheckResult:
    violations: list[Violation] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)

    @property
    def hard(self):
        return [v for v in self.violations if v.is_hard]


class Checker:
    def __init__(self, extractor: ClaimExtractor, rule_engine: RuleEngine):
        self.extractor = extractor
        self.rule_engine = rule_engine

    def _relation_contradictions(self, rel_claims, ontology, chapter) -> list[Violation]:
        """본문이 단정한 관계 vs 캐논(ground_truth·객관) 활성 관계의 '선언된 상충'(conflicts_with) — 추출+코드비교(quasi).
        예: 캐논 '동맹'인 둘을 본문이 '원수'로 단정 → 위반. 상충 미선언 관계는 비대상(과잉게이트 금지)."""
        viols: list[Violation] = []
        canon = [e for e in ontology.edges_as_of(chapter)
                 if e.trust_tier == "ground_truth" and e.pov is None]
        label_to_key = {r.label: r.rel_id for r in ontology.rel_catalog.values()}   # 라벨 역해석("적대"→enemy_of)
        for rc in rel_claims:
            claimed = (rc.get("rel_id") or "").strip()
            claimed = label_to_key.get(claimed, claimed)
            spec = ontology.rel_spec(claimed)
            pair = {rc.get("src"), rc.get("dst")}
            for e in canon:
                if {e.src_id, e.dst_id} != pair:
                    continue
                espec = ontology.rel_spec(e.rel_id)
                if claimed in espec.conflicts_with or e.rel_id in spec.conflicts_with:
                    viols.append(Violation(
                        entity=f"{ontology.name(e.src_id)}↔{ontology.name(e.dst_id)}",
                        kind="relation_contradiction", grade=SignalGrade.QUASI,
                        canon=f"관계:{espec.label}", text=f"본문 단정:{spec.label}",
                        evidence=str(rc.get("evidence") or "")[:80]))
        return viols

    def check_text(self, text: str, ontology, chapter: int, involved_ids: list[str]) -> CheckResult:
        full = self.extractor.extract_full(text, ontology, involved_ids)
        claims = full["entities"]
        viols = self.rule_engine.evaluate(claims, ontology, chapter)   # quasi/semantic
        viols += self._relation_contradictions(full["relation_claims"], ontology, chapter)  # 관계 게이트(quasi)
        viols += ontology.ontology_internal_check(chapter)             # 등급1 (LLM 0콜, 엣지검사 회차-국소)
        return CheckResult(violations=viols, claims=claims)
