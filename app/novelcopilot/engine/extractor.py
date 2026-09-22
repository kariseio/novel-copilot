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

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
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
            ent[key] = f"{self.vocab.categorical(key)} 중 하나로 정규화"
            ent[f"{key}_evidence"] = "그 값을 말한 본문 구절 그대로 인용"
        for key in self.vocab.numeric_keys:
            ent[key] = f"명시된 {self.vocab.label(key)} int"
            ent[f"{key}_evidence"] = "그 수치를 말한 본문 구절 인용"
        for a in self.vocab.state_specs():
            if a.key != "status" and a.states:
                ent[a.key] = f"{a.states} 중 하나(본문이 분명히 말한 경우만)"
                ent[f"{a.key}_evidence"] = "근거 본문 구절 인용"
        ent["appears_as"] = "[필수·모든 엔티티] present_acting|flashback|mention|vision|absent 중 하나 (본문 인용 불요 — 분류값)"
        for wr in self.world_rules:
            hint = wr.extract_hint or wr.text
            ent[wr.flag] = f"{hint} 이면 true 아니면 false"
            ent[f"{wr.flag}_evidence"] = "true 라면 근거 본문 구절 인용"
        schema = {"entities": [ent],
                  "relation_claims": [{"src": "<roster id>", "dst": "<roster id>",
                                       "rel_id": "본문이 단정한 관계(동맹/적대/연인 등 관계키 또는 자유 라벨)",
                                       "evidence": "그 관계를 단정한 본문 구절 인용"}]}
        if self._tabled():   # CN-5: 열거표가 선언된 세계에서만 접붙임(없으면 스키마·비용 무변경)
            schema["table_claims"] = [{
                "rule_id": "본문이 단정한 대응이 속한 표 규칙 id(아래 [열거표] 중 하나, 없으면 이 항목 생략)",
                "key": "본문이 지목한 표의 키(제시된 키 목록 중 하나로 정규화, 매핑 불가면 null)",
                # 값도 캐논 토큰으로 정규화(범주형 vocab 과 동일 규율) — 조사·어미 표면변이가 거짓 불일치 만들지 않게.
                "value": "본문이 그 키에 대응시킨 대상(제시된 값 목록 중 하나로 정규화; 조사·어미 떼고 표 값 토큰 그대로, 매핑 불가면 null)",
                "evidence": "그 키→값 대응을 단정한 본문 구절 그대로 인용",
            }]
        return schema

    def _tabled(self) -> list:
        return [wr for wr in self.world_rules if getattr(wr, "table", None)]

    def _rule_hints(self) -> str:
        lines = []
        for wr in self.world_rules:
            lines.append(f"- {wr.flag}: {wr.extract_hint or wr.text} (애매하면 false)")
        for wr in self._tabled():   # CN-5: 표 키·값 목록 제시 → LLM 이 본문 표현을 캐논 키·값으로 정규화(범주형 vocab 과 동일 규율)
            vals = list(dict.fromkeys(wr.table.values()))
            lines.append(f"- [열거표] {wr.rule_id} 키:{list(wr.table.keys())} 값:{vals} — "
                         f"본문이 한 키의 대응을 말하면 key/value 를 이 목록의 토큰으로 정규화해 table_claims 에 보고")
        return "\n".join(lines)

    _FEWSHOT = (
        "appears_as 분류 기준(중요 — 오답이 흔한 사례):\n"
        '- "그는 그 인물을 회상했다 / ~가 떠올랐다 / 과거에 ~했었다" → flashback\n'
        '- "그 인물의 환영이 어른거렸다 / 꿈에 나타났다 / 상상 속에 그려졌다" → vision\n'
        '- "사람들이 그 인물 이야기를 했다 / ~라고 불리던 자 / 소문으로만 언급" → mention\n'
        '- 현재 시점에서 직접 말하고 움직여야만 present_acting\n'
    )

    # DP-8: 1인칭 서술자 표지 — 이 중 하나라도 실재하면 그 본문은 1인칭으로 서술된다고 본다(주인공 결속 판정).
    #   교착어 substring 가드: 한글 경계(lookbehind/lookahead)로 '하나는'(하나+는)·'난로' 등 오탐 차단.
    #   checker 계열 _STOP 에 넣는 표지(나는·내가·나를·나도)와 대칭(design-dp-repair.md §DP-8).
    _FIRST_PERSON = re.compile(r"(?<![가-힣])(나는|내가|나를|나도)(?![가-힣])")

    @promptlog.stage("check_extract")
    def extract_full(self, text: str, ontology, involved_ids: list[str], pov_entity_id: str = "") -> dict:
        """엔티티 클레임 + 관계 단정 클레임(단일 콜). 증거 스팬 미실재 클레임은 코드가 폐기.

        DP-8(1인칭 결속): pov_entity_id(주인공)가 주어지고 본문이 1인칭이면(표지 실재), name-scan 이 놓치는
        서술자 '나'를 그 주인공 id 로 결속한다 — present 집합에 편입하고 roster 별칭에 '나/내'를 실어
        LLM 이 '나/내'의 행동·상태를 그 인물로 귀속하게 한다(하류 과소추출 1건 소스 차단). 3인칭이면 무동작(기존 경로)."""
        rel_dict = {r.rel_id: r.label for r in ontology.rel_catalog.values()}
        ids = list(dict.fromkeys(list(involved_ids) + ontology.scan_present_ids(text)))
        first_person = (bool(pov_entity_id) and pov_entity_id in ontology.entities
                        and ontology.is_actor(ontology.entities[pov_entity_id].etype)
                        and self._FIRST_PERSON.search(text) is not None)
        if first_person and pov_entity_id not in ids:   # 서술자 '나'는 지면에 이름이 없어도 주인공 — present 결속
            ids.append(pov_entity_id)
        roster = [{"id": e.id, "name": e.name, "aliases": list(e.aliases)}
                  for e in (ontology.entities[i] for i in ids if i in ontology.entities)
                  if ontology.is_actor(e.etype)]
        if not roster:
            return {"entities": [], "relation_claims": [], "table_claims": []}
        pov_bind = ""
        if first_person:
            for r in roster:
                if r["id"] == pov_entity_id:   # 주인공 roster 별칭에만 '나/내' 편입(ontology 원본 aliases 는 불변)
                    r["aliases"] = list(dict.fromkeys(r["aliases"] + ["나", "내"]))
                    pov_bind = (f"\n이 본문은 1인칭 서술이다 — 서술자 '나'(나는·내가·나를·나도·내)는 "
                                f"명부의 '{ontology.entities[pov_entity_id].name}'(id={pov_entity_id})다. "
                                "'나/내'로 지칭된 행동·상태·대사·관계는 그 인물의 것으로 귀속하라.")
                    break
        # CE-4(비용): 스키마 템플릿(_schema)과 이 지시에서 null 시연/나열을 제거 — 값이 명시된 필드만 키로 포함해
        #   출력 토큰을 줄인다(전 인물×전 필드 null 스캐폴딩이 출력의 큰 축이었다). 증거 인용 강제는 유지(환각 차단
        #   게이트). 생략==null 동치가 성립하는 필드(범주형·수치·상태·플래그)는 하류 rule predicate·증거 강제·
        #   G-B 비교·OV-2 브리지의 판정이 불변이다. **예외 = appears_as**: null 이 유효값인 적 없는 필수 분류값이고
        #   terminal 상태 하드룰(rules/predicates.py:107)의 유일 입력이라 생략 시 위반이 조용히 안 잡힌다 → 스키마
        #   앵커·지시로 상시 필수 고정 + 누락 엔티티를 advisory 로 가시화(자동 채움 금지 — 감사관 조건 CE-4 ⓐ).
        sys = ("본문이 '명시적으로' 말한 것만 추출·정규화. 추측 금지. "
               "값이 명시된 필드만 키로 포함하라(본문이 명시한 엔티티·범주만 보고). "
               "범주형은 제시된 통제어휘 토큰으로만(표면 변이는 대표 토큰으로; 매핑불가하면 '기타'). "
               "값을 보고할 때는 반드시 그 근거 본문 구절을 evidence 로 그대로 인용하라(인용 없으면 보고하지 마라). "
               "appears_as(등장 양태)는 본문 인용이 필요 없는 분류값이다 — 모든 엔티티마다 "
               "present_acting/flashback/mention/vision/absent 중 하나를 반드시 골라 넣어라"
               "(현재 시점에서 직접 말하고 움직이면 present_acting).\n"
               + self._FEWSHOT + self._rule_hints() + pov_bind + "\nJSON만.")
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
        # CN-5: 표 대응 클레임 — 증거 미실재는 폐기(환각 차단, 관계/속성과 동일 규율). 대조는 Checker(table_lookup 술어).
        tcs = []
        if self._tabled():
            valid_rids = {wr.rule_id for wr in self._tabled()}
            for tc in res.get("table_claims", []) or []:
                if (tc.get("rule_id") in valid_rids and tc.get("key") and tc.get("value")
                        and _quote_in_text(str(tc.get("evidence") or ""), text)):
                    tcs.append(tc)
        # CE-4 ⓐ(감사관): appears_as 누락 엔티티를 가시화만 한다(자동 기본값 주입 금지 — present_acting 은 하드룰
        #   오발, absent 는 침묵 유지·비대칭 독트린 위반). 누락 목록을 반환해 상위(checker→harness bus)가 표면화하면,
        #   terminal 상태 인물이 present 로 등장하는 화가 실제로 왔을 때 침묵 통과를 막는다(현 입력엔 미발현·엔딩 예고).
        missing_aa = [c.get("id") for c in ents if not c.get("appears_as")]
        return {"entities": ents, "relation_claims": rels, "table_claims": tcs,
                "missing_appears_as": missing_aa}

    def extract(self, text: str, ontology, involved_ids: list[str]) -> list[dict]:
        return self.extract_full(text, ontology, involved_ids)["entities"]
