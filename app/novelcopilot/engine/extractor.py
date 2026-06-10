# -*- coding: utf-8 -*-
"""LLM 구조화 추출 — 추출 스키마를 WorldConfig 어휘/룰에서 동적 생성(하드코딩 스키마 제거).

추출은 LLM이지만 판정은 코드(rules) — 비대칭의 준결정론 절반.
신뢰성 장치(quasi 게이트는 추출 정확도 위에 서므로):
- 증거 스팬 강제: 속성값·세계규칙 flag·관계 단정은 본문 인용(evidence)이 실재해야만 클레임 인정
  (환각 클레임의 결정론 차단 — 인용이 본문에 없으면 코드가 폐기).
- 하드네거티브 few-shot: 회상/환영/언급을 present_acting 으로 오인하지 않게 분류 예시 고정.
- '기타' 탈출구 보존(통제어휘 밖 명시언급이 침묵통과 않고 escalation).
"""
from __future__ import annotations
import json
import re

from ..llm.base import LLMProvider
from .vocabulary import Vocabulary


def _quote_in_text(quote: str, text: str) -> bool:
    """증거 인용이 본문에 실재하는지(공백 정규화 비교). 8자 이상 요구 — 한두 단어 우연일치 방지."""
    q = re.sub(r"\s+", "", quote or "")
    if len(q) < 8:
        return False
    return q in re.sub(r"\s+", "", text)


class ClaimExtractor:
    def __init__(self, provider: LLMProvider, vocab: Vocabulary, world_rules: list):
        self.provider = provider
        self.vocab = vocab
        self.world_rules = world_rules     # list[WorldRuleSpec]

    def remove_world_rule(self, rule_id: str) -> None:   # demote 역연산
        self.world_rules = [w for w in self.world_rules if w.rule_id != rule_id]

    def _schema(self) -> dict:
        ent: dict = {"id": "<roster id>"}
        for key in self.vocab.categorical_keys:
            ent[key] = f"{self.vocab.categorical(key)} 중 하나로 정규화 또는 null"
            ent[f"{key}_evidence"] = "그 값을 말한 본문 구절 그대로 인용(없으면 null)"
        for key in self.vocab.numeric_keys:
            ent[key] = f"명시된 {self.vocab.label(key)} int 또는 null"
            ent[f"{key}_evidence"] = "그 수치를 말한 본문 구절 인용(없으면 null)"
        for a in self.vocab.state_specs():
            if a.key != "status" and a.states:
                ent[a.key] = f"{a.states} 중 하나(본문이 분명히 말한 경우만) 또는 null"
                ent[f"{a.key}_evidence"] = "근거 본문 구절 인용(없으면 null)"
        ent["appears_as"] = "present_acting|flashback|mention|vision|absent"
        for wr in self.world_rules:
            hint = wr.extract_hint or wr.text
            ent[wr.flag] = f"{hint} 이면 true 아니면 false"
            ent[f"{wr.flag}_evidence"] = "true 라면 근거 본문 구절 인용"
        return {"entities": [ent],
                "relation_claims": [{"src": "<roster id>", "dst": "<roster id>",
                                     "rel_id": "본문이 단정한 관계(동맹/적대/연인 등 관계키 또는 자유 라벨)",
                                     "evidence": "그 관계를 단정한 본문 구절 인용"}]}

    def _rule_hints(self) -> str:
        lines = []
        for wr in self.world_rules:
            lines.append(f"- {wr.flag}: {wr.extract_hint or wr.text} (애매하면 false)")
        return "\n".join(lines)

    _FEWSHOT = (
        "appears_as 분류 기준(중요 — 오답이 흔한 사례):\n"
        '- "그는 죽은 스승을 회상했다 / ~가 떠올랐다 / 과거에 ~했었다" → flashback\n'
        '- "스승의 환영이 어른거렸다 / 꿈에 나타났다" → vision\n'
        '- "사람들이 스승 이야기를 했다 / ~라고 불리던 자" → mention\n'
        '- 현재 시점에서 직접 말하고 움직여야만 present_acting\n'
    )

    def extract_full(self, text: str, ontology, involved_ids: list[str]) -> dict:
        """엔티티 클레임 + 관계 단정 클레임(단일 콜). 증거 스팬 미실재 클레임은 코드가 폐기."""
        rel_dict = {r.rel_id: r.label for r in ontology.rel_catalog.values()}
        ids = list(dict.fromkeys(list(involved_ids) + ontology.scan_present_ids(text)))
        roster = [{"id": e.id, "name": e.name, "aliases": e.aliases}
                  for e in (ontology.entities[i] for i in ids if i in ontology.entities)
                  if ontology.is_actor(e.etype)]
        if not roster:
            return {"entities": [], "relation_claims": []}
        sys = ("본문이 '명시적으로' 말한 것만 추출·정규화. 추측 금지. 없으면 null. "
               "범주형은 제시된 통제어휘 토큰으로만(표면 변이는 대표 토큰으로; 매핑불가하면 '기타'). "
               "값을 보고할 때는 반드시 그 근거 본문 구절을 evidence 로 그대로 인용하라(인용 없으면 보고하지 마라).\n"
               + self._FEWSHOT + self._rule_hints() + "\nJSON만.")
        msg = [
            {"role": "system", "content": sys},
            {"role": "user", "content":
             f"[명부]\n{json.dumps(roster, ensure_ascii=False)}\n"
             f"[관계키 사전 — relation_claims.rel_id 는 가능한 한 이 키로 정규화(예: 원수/숙적→enemy_of), "
             f"매핑 불가만 자유 라벨]\n{json.dumps(rel_dict, ensure_ascii=False)}\n\n[본문]\n{text}\n\n"
             f"스키마:\n{json.dumps(self._schema(), ensure_ascii=False)}"},
        ]
        res = self.provider.chat_json(msg, temperature=0.0)
        ents = res.get("entities", []) or []
        evidence_keys = (set(self.vocab.categorical_keys) | set(self.vocab.numeric_keys)
                         | {a.key for a in self.vocab.state_specs() if a.key != "status"}
                         | {wr.flag for wr in self.world_rules})
        for c in ents:   # 증거 강제 — 값이 있는데 인용이 본문에 없으면 클레임 폐기(환각 차단, 결정론)
            for key in list(c.keys()):
                if key in evidence_keys and c.get(key) not in (None, "", "null", False):
                    if not _quote_in_text(str(c.get(f"{key}_evidence") or ""), text):
                        c[key] = None
        rels = []
        rids = {r["id"] for r in roster}
        for rc in res.get("relation_claims", []) or []:
            if (rc.get("src") in rids and rc.get("dst") in rids and rc.get("rel_id")
                    and _quote_in_text(str(rc.get("evidence") or ""), text)):
                rels.append(rc)
        return {"entities": ents, "relation_claims": rels}

    def extract(self, text: str, ontology, involved_ids: list[str]) -> list[dict]:
        return self.extract_full(text, ontology, involved_ids)["entities"]
