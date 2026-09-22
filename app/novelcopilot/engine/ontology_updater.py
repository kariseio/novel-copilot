# -*- coding: utf-8 -*-
"""동적 온톨로지 업데이트 — 엔진 고도화 ①.

회차 finalize 후, 본문에서 '새 인물 / 상태·관계 변화'를 추출해 SSOT 를 점진 갱신한다.
정책(데이터 주도, AttributeSpec.mutable 기반):
- 신규 인물: 자동 커밋(provisional=True). 기존 인물 별칭과 겹치면 skip.
- mutable 속성 변화(예: 소속 변절, 등급 상승, 사망): timeline 에 eff_from=다음화로 progress 반영.
- immutable 속성 변화(예: 눈색) / 단조 위반(등급 하락) / 사망→생존: 모순 → 미적용 + escalation.
'덮어쓰기 없음, 추가/전진만, 모순은 사람에게'. LLM은 추출만, 정책 판정은 코드(비대칭 계승).

커밋 티어(XR-5 — 무엇이 [확정 설정]으로 박히는가): 두 분기 모두 기존 게이트(어휘·단조·동시점·비가역)를
통과한 뒤 **커밋 직전**에 AttributeSpec.auto_commit 선언을 적용한다. "non_binding" 축은 비구속
(narrative_inferred)으로만 착지하고 작가 승인(services set_entity_state)이 ground_truth 로 승격한다.
강등 단방향 — "binding" 선언은 어떤 기존 안전장치도 해제하지 못한다. 미선언("")=현행 동작 그대로.
"""
from __future__ import annotations
import json
import re

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..domain.types import OntologyChange, RelationEdge
from ..domain.world import TimelineEntry, EntitySpec
from ..llm.base import LLMProvider
from .ontology import Entity, Ontology
from .vocabulary import Vocabulary, OTHER
from .observability import EventBus
from .extractor import _quote_in_text   # OV-3: 증거 강제(추출 계약 통일 — 체커 ClaimExtractor 와 동일 함수)


# OV-5: 제안 응답 스키마(구조화 출력 강제) — 프롬프트의 JSON 예시와 1:1. 지원 프로바이더에서 유효 JSON 을
#   API 레벨에서 보장해, 파싱 실패→침묵 {} 강등(신작 2·3화 캐논 갱신 소실 실측)의 실패 양식을 소스 차단한다.
_S = {"type": "string"}
_SA = {"type": "array", "items": _S}
PROPOSAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "new_entities": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"name": _S, "etype": _S, "aliases": _SA, "role": _S},
            "required": ["name", "etype", "aliases", "role"]}},
        "state_changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": _S, "attr": _S, "value": _S, "evidence": _S, "note": _S},
            "required": ["id", "attr", "value", "evidence", "note"]}},
        "relations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"src": _S, "dst": _S, "rel_id": _S, "state": _S, "evidence": _S, "note": _S},
            "required": ["src", "dst", "rel_id", "state", "evidence", "note"]}},
        "new_settings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"category": _S, "title": _S, "prose": _S, "keywords": _SA, "evidence": _S},
            "required": ["category", "title", "prose", "keywords", "evidence"]}},   # VA-3: 발명 설정 차단(measure-then-cite)
    },
    "required": ["new_entities", "state_changes", "relations", "new_settings"],
}


def _slug(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "npc"
    if not re.search(r"[a-z0-9]", base):   # 한글 이름 → 인덱스 슬러그
        base = "npc"
    sid, i = base, 2
    while sid in existing:
        sid, i = f"{base}_{i}", i + 1
    return sid


class OntologyUpdater:
    def __init__(self, provider: LLMProvider, vocab: Vocabulary, event_bus: EventBus,
                 allow_reversal: bool = False):
        self.provider = provider
        self.vocab = vocab
        self.bus = event_bus
        self.allow_reversal = allow_reversal   # 회귀/부활/리젠 세계: 비가역 상태 이탈 모순 해제

    @staticmethod
    def _canon_state(attr, v):
        """생애주기 상태값 정규화. status 는 사망/dead·생존/alive 동의어 흡수, 그 외 state-attr 는 라벨 그대로."""
        if v is None:
            return None
        s = str(v).strip()
        if not s or s == "null":
            return None
        if attr == "status":
            low = s.lower()
            if low in ("dead", "사망"):
                return "dead"
            if low in ("alive", "생존"):
                return "alive"
        return s

    @staticmethod
    def _cardinality_violation(src, dst, rel_id, chapter, ontology):
        """혈연/배타 카디널리티 결정론 검출(CN-4·advisory·비차단). 위반=OntologyChange, 아니면 None.
        ①엔티티별 선언 상한(EntitySpec.cardinality, 예 외동={"sibling_of":0}·유일생존자) ②관계 1:1(배우자/약혼 등 대칭 배타).
        어휘사전 0 — 활성 엣지 개수 vs 선언값만 비교(두더지금지). pov(주관 앎/믿음) 엣지는 셈에서 제외."""
        def _cnt(eid):
            return sum(1 for e in ontology.edges_as_of(chapter)
                       if e.rel_id == rel_id and getattr(e, "pov", None) is None
                       and (e.src_id == eid or e.dst_id == eid))
        # ① 엔티티별 선언 상한(외동=0 이면 첫 엣지부터 위반)
        for eid in (src, dst):
            ent = ontology.entities.get(eid)
            lim = (getattr(ent, "cardinality", None) or {}).get(rel_id) if ent else None
            if lim is not None and _cnt(eid) >= lim:
                lab = ontology.rel_spec(rel_id).label
                other = ontology.name(dst if eid == src else src)
                _act = (f"이 관계가 맞으면 설정에서 {ontology.name(eid)}의 '{lab}' 상한({lim})을 늘리세요. "
                        "본문 오류(예: 외동인데 형제 등장)면 그 부분을 교정해 재생성하세요.")
                return OntologyChange(op="contradiction", entity=ontology.name(eid),
                    detail=f"{ontology.name(eid)} '{lab}' 카디널리티 위반(상한 {lim}·기존 {_cnt(eid)}) ↔ {other}",
                    applied=False, severity="review", reason=f"{ontology.name(eid)}은(는) '{lab}' 관계 최대 {lim}개로 선언됨 — {_act}")
        # ② 관계 스펙 1:1(대칭 배타 — 배우자/약혼)
        spec = ontology.rel_spec(rel_id)
        if getattr(spec, "cardinality", "N:N") == "1:1":
            for eid in (src, dst):
                if _cnt(eid) >= 1:
                    other = ontology.name(dst if eid == src else src)
                    _act = (f"본문 오류면 교정해 재생성하고, 실제 새 관계면 설정에서 {ontology.name(eid)}의 이전 "
                            f"'{spec.label}'을(를) 종료(eff_to)하세요.")
                    return OntologyChange(op="contradiction", entity=ontology.name(eid),
                        detail=f"{ontology.name(eid)} '{spec.label}' 1:1 위반(기존 {_cnt(eid)}) ↔ {other}",
                        applied=False, severity="review", reason=f"'{spec.label}'은(는) 배타(1:1) 관계 — {ontology.name(eid)}에 이미 관계 존재. {_act}")
        return None

    def _claims_to_state_changes(self, claims, ontology):
        """OV-2: 체커의 정규화+증거강제 actor 클레임(flat dict) → state_change(코드 파생·LLM 0). 정규화·증거·roster 계약 단일화.
        attr ∈ categorical∪numeric∪(state≠status). 값 '기타'(OTHER)는 state_change 로 넘기지 않고 evidence 실린 advisory 로
        분기 → apply 의 categorical '통제어휘 밖 값' contradiction 격상이 구조적으로 불가(티켓: 기타=사람확인 advisory·격상금지).
        반환 (code_scs, covered=(id,attr) 집합, advisories). status·비-actor·미커버 actor 는 LLM 백스톱이 담당(호출부 병합)."""
        attr_keys = (set(self.vocab.categorical_keys) | set(self.vocab.numeric_keys)
                     | {a.key for a in self.vocab.state_specs() if a.key != "status"})
        code_scs, covered, advisories = [], set(), []
        for c in claims or []:
            eid = c.get("id")
            if not eid or eid not in ontology.entities:   # 미해소·환각 id 스킵(advisory 소음 방지)
                continue
            for k in attr_keys:
                v = c.get(k)
                # apply 계약과 동일한 공허성(None/''/'null' 만; False/0 은 값) — 수치 0 전이 보존
                if v is None or (isinstance(v, str) and v.strip() in ("", "null")):
                    continue
                covered.add((eid, k))   # code 가 처리한 (id,attr) → 호출부가 LLM 중복을 드롭(단일 계약·OTHER 무격상)
                ev = str(c.get(f"{k}_evidence") or "")
                if isinstance(v, str) and v.strip() == OTHER:   # 통제어휘 밖 명시언급 → advisory(격상 아님)
                    advisories.append({"id": eid, "attr": k, "evidence": ev})
                    continue
                code_scs.append({"id": eid, "attr": k, "value": v, "evidence": ev, "note": "checker_claim"})
        return code_scs, covered, advisories

    @promptlog.stage("ontology_propose")
    def propose(self, text: str, ontology: Ontology, chapter: int,
                existing_setting_titles: list[str] | None = None, claims: list[dict] | None = None) -> dict:
        roster = [{"id": e.id, "name": e.name, "etype": e.etype} for e in ontology.entities.values()]
        attr_keys = [a.key for a in self.vocab.values()]
        rel_keys = list(ontology.rel_catalog.keys())
        ent_types = list(ontology.entity_types.keys())
        # 생애주기 상태 어휘를 데이터에서 — 'status: alive|dead' 하드코딩 제거(장르별 선언 states 를 그대로 전달)
        state_hint = "; ".join(f"{a.key}:{a.states}" for a in self.vocab.state_specs() if a.states)
        # 통제어휘를 상류에서 고지(미고지 → 표면 변이 보고 → 하류 escalation 소음의 소스 차단). raw vocab 사용(게이트와 동일 기준)
        cat_hint = "; ".join(f"{a.key}:{a.vocab}" for a in self.vocab.values()
                             if a.kind == "categorical" and a.vocab)
        # OV-1: 통제어휘를 모델에 고지 → 같은 값의 '표기 변형'을 선언값으로 정규화하게 해 허위 '통제어휘 밖 값'
        #       escalation 소음 차단. 단 게이트(apply, 'val not in vocab')는 단방향이라 force-fit(의미적 새 값을
        #       기존 vocab 로 억지 매핑)을 못 잡으므로, 가이드를 '표기 변형만 정규화, 의미적 새 값은 그대로 보고(escalate)'로
        #       기울여 escalation recall 을 모델 판단에 의존시키지 않는다(검출기 추가 아님 — 기존 어휘 상류 고지).
        cat_guidance = ("\n※ categorical 속성(등급·소속 등 아래 [통제어휘]에 허용값이 선언된 속성)을 보고할 때: 본문 표현이 "
                        "통제어휘의 한 값과 '같은 대상'을 가리키는 표기 변형(띄어쓰기·접미사·약칭. 예를 들어 'A급'·'에이급'→통제어휘 'A')이면 "
                        "그 통제어휘 값으로 표기를 맞춰 보고하라. 그러나 통제어휘 어디에도 없는 '다른' 값을 본문이 분명히 확정하면, "
                        "기존 값과 비슷해 보여도 본문 값 그대로 보고하라(작가 검수 대상).") if cat_hint else ""
        msg = [
            {"role": "system", "content":
             "너는 작품 설정 관리자다. 이번 회차 본문이 문장으로 말한 것만 보고한다.\n"
             "1) 기존 명부에 없는 '새 엔티티'(고유명·역할 분명): 인물뿐 아니라 세력·장소·아이템·사건도. etype 지정.\n"
             "2) 기존 엔티티의 상태/소속/등급 변화(본문이 분명히 말한 것만).\n"
             "3) 엔티티 사이 관계(본문이 보여주는 연결의 성격을 그대로). 제시된 관계키 우선, 없으면 본문에서 도출한 간결한 자유 라벨. "
             "관계의 질적 현재 상태가 본문에 드러나면 그 변화를 짧게 state 로.\n"
             # VA-3: '내력'(사극어 프라이밍 실측)·부정 지시 제거, 어휘·자수 계약, evidence 계약을 4)에도 확장
             "4) 이번 회차 본문이 새로 알려 준 세계 지식(그 장소에서 전에 무슨 일이 있었는지, 어떤 규칙이 통하고 "
             "어떤 경우에 통하지 않는지, 사람들이 늘 그렇게 하는 방식). 인물 개인의 상태 변화는 2)가 맡는다. "
             "기존 설정집 제목에 없는 것만, 최대 2개. prose 는 200자 이내로, 본문이 실제로 쓴 말을 그대로 살려 적는다.\n"
             "state_changes, relations, new_settings 는 그 근거가 된 본문 구절을 evidence 에 그대로(8자 이상) 인용한다. "
             "인용을 대지 못한 항목은 미이행으로 둔다.\n"
             "JSON: {\"new_entities\":[{\"name\":\"\",\"etype\":\"\",\"aliases\":[],\"role\":\"\"}],"
             "\"state_changes\":[{\"id\":\"명부 id\",\"attr\":\"속성키\",\"value\":\"새 값\",\"evidence\":\"그 변화를 말한 본문 구절 인용\",\"note\":\"\"}],"
             "\"relations\":[{\"src\":\"명부 id 또는 새 이름\",\"dst\":\"명부 id 또는 새 이름\",\"rel_id\":\"관계키 또는 자유 라벨\",\"state\":\"\",\"evidence\":\"그 관계를 단정한 본문 구절 인용\",\"note\":\"\"}],"
             "\"new_settings\":[{\"category\":\"\",\"title\":\"\",\"prose\":\"200자 이내\",\"keywords\":[\"\"],\"evidence\":\"그 설정을 알려 준 본문 구절 인용\"}]}"
             + cat_guidance},
            {"role": "user", "content":
             f"[기존 명부]\n{json.dumps(roster, ensure_ascii=False)}\n"
             f"[엔티티 타입]{ent_types}\n[추적 속성키]{attr_keys}\n[생애주기 상태값]{state_hint}\n"
             + (f"[통제어휘(categorical 허용값 — 같은 대상의 표기 변형만 이 값으로 맞춤)]{cat_hint}\n" if cat_hint else "")
             + f"[관계키(자유 라벨 가능)]{rel_keys}\n"
             f"[{chapter}화 본문]\n{text}"},
        ]
        try:
            # OV-5: 지원 프로바이더는 스키마 강제(유효 JSON 보장), 미지원은 기존 지시-기반 경로(schema 무시).
            res = self.provider.chat_json(msg, temperature=0.0, schema=PROPOSAL_SCHEMA)
        except Exception as e:
            # OV-5: 침묵 {} 강등 금지 — 실패를 호출부가 레코드에 영속할 수 있게 정직 표식으로 반환.
            self.bus.emit("ontology_update", "parse_failure", chapter=chapter, error=type(e).__name__)
            return {"stage_failed": type(e).__name__}
        # 스키마가 value 를 문자열로 강제하므로 수치 문자열은 종전 동작(모델이 수치를 낼 수 있던)과 동형 복원
        #   — 단조·비교 정책이 수치로 판정하던 경로 보존.
        for sc in res.get("state_changes") or []:
            v = sc.get("value")
            if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
                sc["value"] = int(v.strip())
        # OV-2: 체커 클레임 브리지 — 최종 check_text 의 정규화+증거강제 클레임을 code state_change 로 파생해 병합.
        #  겹침((id,attr) covered)은 code 우선(=체커 정규화 단일계약), 그 LLM 중복은 드롭. LLM 은 status·비-actor·미커버
        #  actor·관계·신규엔티티/설정의 백스톱으로 넓게 유지(커버리지 갭0). '기타'는 code 가 advisory 로 빼 apply 격상 원천차단.
        if claims is not None:
            code_scs, covered, advisories = self._claims_to_state_changes(claims, ontology)
            llm_scs = [sc for sc in (res.get("state_changes") or [])
                       if (sc.get("id"), sc.get("attr")) not in covered]
            res["state_changes"] = code_scs + llm_scs
            for adv in advisories:   # OTHER = SEMANTIC 사람확인 advisory(evidence 실어 무엇이 언급됐는지 보존)
                self.bus.emit("ontology_update", "uncertain", chapter=chapter,
                              entity=adv["id"], attr=adv["attr"], evidence=adv["evidence"][:80])
        # OV-3: 증거 강제 — state_change/relation 의 근거 인용(evidence)이 본문에 실재하지 않으면 폐기(환각·유령 오결속
        #       결정론 차단, 체커 ClaimExtractor 와 동형). 값 없는 항목은 어차피 apply 서 skip → 인용 요구 생략.
        #       병합된 code_scs 도 이 단일 관문을 통과(멱등 — 이미 강제됨·미증거 수치0 은 계약대로 드롭).
        kept_sc = []
        for c in (res.get("state_changes") or []):
            if c.get("value") in (None, "", "null") or _quote_in_text(str(c.get("evidence") or ""), text):
                kept_sc.append(c)
            else:
                self.bus.emit("ontology_update", "evidence_dropped", chapter=chapter,
                              kind="state_change", entity=c.get("id"), attr=c.get("attr"))
        res["state_changes"] = kept_sc
        kept_rel = []
        for rc in (res.get("relations") or []):
            if _quote_in_text(str(rc.get("evidence") or ""), text):
                kept_rel.append(rc)
            else:
                self.bus.emit("ontology_update", "evidence_dropped", chapter=chapter,
                              kind="relation", src=rc.get("src"), dst=rc.get("dst"))
        res["relations"] = kept_rel
        return res

    def apply(self, proposal: dict, ontology: Ontology, chapter: int
              ) -> tuple[list[OntologyChange], list[EntitySpec], list[TimelineEntry], list[RelationEdge]]:
        changes: list[OntologyChange] = []
        new_specs: list[EntitySpec] = []
        new_tl: list[TimelineEntry] = []
        new_edges: list[RelationEdge] = []
        amap = ontology.alias_map()

        # T5-R2: 노드화 가치 있는 타입 = 데이터주도 EntityTypeSpec.category 분류(actor/group/place)와 정합(is_actor 는
        #   actor 만이라 더 좁음 — 여기선 그래프 노드가 돼야 할 group/place 도 포함, 구 STRUCTURAL 과 동일 취지). 하드코딩 리터럴
        #   ∪ 카탈로그 category — 리터럴로 하위호환('race' 등 미카탈로그 키 유지), category 로 장르 선언 커스텀 actor(괴수·AI·신령)까지
        #   admit(전엔 STRUCTURAL 영어 리터럴에 없어 무관계 시 prop_skip 으로 조용히 드롭됐음). object·abstract·event(소품·설정·사건)는 제외.
        STRUCTURAL = {"character", "faction", "organization", "place", "location", "race"}
        _node_cats = {"actor", "group", "place"}
        node_types = STRUCTURAL | {k for k, t in (ontology.entity_types or {}).items()
                                   if getattr(t, "category", "") in _node_cats}
        # 관계에서 참조된 이름(구조적으로 연결됨 → 노드화 가치 있음)
        rel_names: set = set()
        for rc in proposal.get("relations", []) or []:
            for key in ("src", "dst"):
                v = (rc.get(key) or "").strip()
                if v:
                    rel_names.add(v)

        # 1) 신규 엔티티 자동 커밋. 구조적 타입(인물/세력/장소…) 또는 관계에 연결된 것만 노드화.
        #    1회성 소품(머그잔·칼 등)은 노드로 박지 않는다 — 요약/Wiki(narrative)가 보존(합의된 라우팅: 구조적 사실만 캐논).
        for nc in proposal.get("new_entities", []) or proposal.get("new_characters", []) or []:
            name = (nc.get("name") or "").strip()
            if not name:
                continue
            if name in amap:        # 정확/별칭 일치(amap=이름+별칭) → 중복. 관측(조용한 정지 금지).
                self.bus.emit("ontology_update", "dup_skip", chapter=chapter, entity=name, matched=amap[name])
                continue
            etype = (nc.get("etype") or "character").strip() or "character"
            if etype not in node_types and name not in rel_names:
                self.bus.emit("ontology_update", "prop_skip", chapter=chapter, entity=name, etype=etype)
                continue          # 소품 → 요약이 보존(노드 미생성)
            sid = _slug(name, set(ontology.entities))
            aliases = [a for a in nc.get("aliases", []) if a]
            ontology.add(Entity(id=sid, name=name, etype=etype, attrs={},
                                aliases=aliases, provisional=True))
            amap[name] = sid
            new_specs.append(EntitySpec(id=sid, name=name, etype=etype,
                                        aliases=aliases, attrs={}, provisional=True))
            changes.append(OntologyChange(op="new_entity", entity=name,
                                          detail=f"신규 {etype} 자동 커밋({nc.get('role', '')})", applied=True))
            self.bus.emit("ontology_update", "new_entity", chapter=chapter, entity=name, etype=etype)
            if etype == "character":
                # 캐스트 플랜 레이어 위반: 이름 있는 인물이 '설계 없이' 본문에서 발명됨(콜드 드롭) —
                # 등록(잠정)은 안전망으로 유지하되, 사후 수확이 아니라 공정 위반으로 가시화(다음 아크 설계 입력)
                self.bus.emit("cast_plan", "uncast_character", chapter=chapter, entity=name)

        # 2) 상태 변화 — mutable 정책
        eff = chapter + 1
        for sc in proposal.get("state_changes", []) or []:
            eid = sc.get("id")
            ent = ontology.entities.get(eid)
            if not ent:
                continue
            attr, val = sc.get("attr"), sc.get("value")
            if attr is None or val in (None, "", "null"):
                continue
            cur = ontology.state_as_of(eid, attr, chapter)
            spec = self.vocab.attr(attr)

            # 생애주기(state/status) 전이 — 데이터주도(death=한 인스턴스, 하드코딩 제거).
            if attr == "status" or (spec and spec.kind in ("state", "status")):
                irr = self.vocab.irreversible_states(attr)     # 비가역 상태(이탈=모순)
                term = self.vocab.terminal_states(attr)         # '제거' 상태(등장/관계 차단)
                slabel = "생사" if attr == "status" else self.vocab.label(attr)
                newv = self._canon_state(attr, val)
                curv = self._canon_state(attr, cur)
                if newv is None or newv == curv:
                    continue
                if spec and spec.states and newv not in spec.states:
                    # 선언 어휘 밖 상태값 → 침묵 통과/자동커밋 금지(비가역 전이가 표면형 불일치로 ground_truth 박히는 누수 방지)
                    _act = f"이 상태가 맞으면 설정집에서 {slabel} 속성의 states 에 '{newv}'를 추가(승인)하세요. 오타·환각이면 본문을 교정해 재생성하세요."
                    changes.append(OntologyChange(op="contradiction", entity=ent.name,
                                                  detail=f"{slabel} 미정의 상태값 '{newv}'(선언 어휘 밖)", applied=False,
                                                  severity="review", reason=f"선언된 states 밖 상태값 '{newv}' — {_act}"))
                    self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)
                    continue
                if curv is not None and curv in irr and not self.allow_reversal:
                    # 비가역 상태 이탈(예: 사망→생존, 각성→미각성) → 모순. 회귀/부활 세계(allow_reversal)는 허용.
                    _act = f"회귀·부활 세계라면 작품 설정에서 '상태 되돌림 허용'을 켜세요. 본문 오류라면 그 장면을 회상·환영으로 바꿔(작가 지시) 재생성하세요."
                    changes.append(OntologyChange(op="contradiction", entity=ent.name,
                                                  detail=f"{slabel} 비가역 상태 '{curv}' 이탈 시도→'{newv}'",
                                                  applied=False, reason=f"비가역 상태 '{curv}'→'{newv}' 이탈 — {_act}"))
                    self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)
                    continue
                # 동일 시점(eid,attr,eff)에 이미 다른 ground_truth 값 존재 → 커밋하면 ssot_ambiguous 영구 점등.
                # (시드 예약 vs 자동추출 충돌 — 시뮬 실측) 커밋 대신 escalation 으로 작가에게.
                if any(t2[0] == eid and t2[1] == attr and t2[3] == eff and t2[5] == "ground_truth"
                       and str(t2[2]) != newv for t2 in ontology.timeline):
                    _act = f"공식 설정에서 {slabel}의 {eff}화 시점 값을 하나로 확정하세요(시드 예약과 자동 감지가 충돌)."
                    changes.append(OntologyChange(op="contradiction", entity=ent.name,
                                                  detail=f"{slabel} {eff}화 시점에 상충 예약 존재({newv} vs 기존)",
                                                  applied=False, reason=f"동시점 충돌 — {_act}"))
                    self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)
                    continue
                # 비가역/제거 전이 = 작가 확정 전 비구속(narrative_inferred). 가역 전이 = '전진만' 자동커밋(ground_truth).
                # XR-5: 그 위에 속성 선언 티어를 얹는다 — auto_commit="non_binding" 축(내면·인지·자각처럼 본문
                #   서술만으로 사실 확정이 어려운 축)은 가역 전이도 비구속으로만 착지하고 작가 승인(set_entity_state)이
                #   승격한다. **강등 단방향** — "binding" 선언은 위 비가역/terminal 강등을 해제하지 못한다(미선언 축은
                #   현행 그대로 = 프롬프트 바이트 동일). 판별은 선언 데이터만 — 속성명 사전·정규식·장르 라벨 0.
                binding_irrev = (newv in irr) or (newv in term)
                non_binding_attr = (spec is not None and getattr(spec, "auto_commit", "") == "non_binding")
                tier = "narrative_inferred" if (binding_irrev or non_binding_attr) else "ground_truth"
                tag = "(추정)" if tier == "narrative_inferred" else ""
                ontology.set_state(eid, attr, newv, eff, reason=f"{chapter}화 동적 감지{tag}", trust_tier=tier)
                new_tl.append(TimelineEntry(entity_id=eid, attr=attr, value=newv, eff_from=eff,
                                            reason=f"{chapter}화 동적 감지{tag}", trust_tier=tier))
                detail = f"{slabel}: {curv}→{newv}({eff}화부터)" + (" · 작가 확정 시 캐논" if tier == "narrative_inferred" else "")
                changes.append(OntologyChange(op="state_change", entity=ent.name, detail=detail, applied=True))
                continue

            if cur is not None and str(cur).strip() == str(val).strip():
                continue

            # ON-2 U4: numeric 계약 집행 — 비정수 값은 커밋하지 않는다(산문 값이 numeric 축을 오염시킨
            #   실측: anomaly_insight="원인 모를 직관적 감별 능력…". 유입구는 아래 단조 검사의 int() 실패가
            #   조용히 통과되던 것). 커밋 대신 review advisory 로 남긴다 — 자동 교정 0(무강제).
            if spec and spec.kind == "numeric":
                try:
                    int(str(val).strip())
                except (ValueError, TypeError):
                    changes.append(OntologyChange(
                        op="state_change", entity=ent.name, applied=False, severity="review",
                        detail=f"{self.vocab.label(attr)} numeric 계약 위반 값 '{str(val)[:40]}' — 미커밋",
                        reason="numeric 축에는 정수만 커밋(산문 값은 작가 확인 대상)"))
                    continue
            # 단조 위반 검사
            if spec and spec.kind == "numeric" and spec.monotonic and cur is not None:
                try:
                    ti, ci = int(val), int(cur)
                    bad = (spec.monotonic == "non_decreasing" and ti < ci) or \
                          (spec.monotonic == "non_increasing" and ti > ci)
                    if bad:
                        _act = f"값이 실제로 그 방향으로 변했다면 설정집에서 {self.vocab.label(attr)}의 단조 제약을 완화하세요. 본문 오류라면 교정해 재생성하세요."
                        changes.append(OntologyChange(op="contradiction", entity=ent.name,
                                                      detail=f"{self.vocab.label(attr)} 단조 위반 {cur}→{val}",
                                                      applied=False, severity="review", reason=f"단조 제약 위반 {cur}→{val} — {_act}"))
                        self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)
                        continue
                except (ValueError, TypeError):
                    pass

            # categorical 통제어휘 검증 — 어휘 밖 자유값을 ground_truth 로 자동커밋하면
            # 이후 모든 회차가 '쓰레기 캐논 vs 어휘값' 영구 불일치로 ESCALATED 에 갇힌다(시뮬 실측 결함).
            if spec and spec.kind == "categorical" and spec.vocab and str(val).strip() not in spec.vocab:
                _act = f"이 값이 맞으면 설정집에서 {self.vocab.label(attr)}의 통제어휘에 '{val}'을 추가하세요. 오타·환각이면 본문을 교정해 재생성하세요."
                changes.append(OntologyChange(op="contradiction", entity=ent.name,
                                              detail=f"{self.vocab.label(attr)} 통제어휘 밖 값 '{val}'",
                                              applied=False, severity="review", reason=f"통제어휘 밖 값 '{val}' — {_act}"))
                self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)
                continue
            mutable = bool(spec and spec.mutable)
            if mutable:
                # M1: 캐논 주입집합 = 게이트집합. 신규 추적 속성 키를 엔티티에 등록해
                #     canon_facts(ground_truth 주입)가 이 속성을 빠뜨리지 않게(게이트만 걸고 미주입되는 비대칭 제거).
                ent.attrs.setdefault(attr, None)
                # 순서형(ordered) 가변축의 '후진' 전이(vocab 인덱스 감소 — 회상·오추출)는 binding 캐논을 *되감지* 않게
                #   비구속(narrative_inferred)으로만 기록. 비교 기준은 보호 대상인 *binding 캐논*(ground_truth)이다
                #   — all-tier cur 가 아니라 binding_state_as_of(적대검증: cur 베이스라인은 비구속값을 끼워 오판).
                # XR-5: 기본 티어(ground_truth)는 '관찰 가능한 외적 사실' 가정 위에 서 있다 — 그 가정이 안 서는 축
                #   (auto_commit="non_binding")은 아래에서 비구속으로 강등하고 작가 승인이 승격한다. 후진 강등과
                #   합류(둘 중 하나라도 걸리면 비구속)하며 **강등 단방향**이라 "binding" 선언이 후진 강등을 못 푼다.
                #   미선언 축은 현행 그대로 = 프롬프트 바이트 동일. 판별은 선언 데이터만(속성명 사전·정규식 0).
                tier, back = "ground_truth", False
                bcur = ontology.binding_state_as_of(eid, attr, chapter)
                if getattr(spec, "ordered", False) and spec.vocab and bcur is not None:
                    try:
                        if spec.vocab.index(str(val).strip()) < spec.vocab.index(str(bcur).strip()):
                            tier, back = "narrative_inferred", True
                    except ValueError:
                        pass
                if spec is not None and getattr(spec, "auto_commit", "") == "non_binding":
                    tier = "narrative_inferred"
                # ground_truth 커밋이 같은 시점 *다른* ground_truth(작가/시드 예약·직전 재생성)와 충돌하면 침묵
                #   덮어쓰기 금지 — status 분기(위)와 대칭으로 escalation 노출. set_state upsert(FIX1)가 작가 확정
                #   캐논을 신호 없이 갈아엎던 결함 차단(적대검증). 커밋 안 함 → 중복 0 → ssot_ambiguous 재점등 없음.
                if tier == "ground_truth" and any(
                        t2[0] == eid and t2[1] == attr and t2[3] == eff and t2[5] == "ground_truth"
                        and str(t2[2]).strip() != str(val).strip() for t2 in ontology.timeline):
                    _act = f"공식 설정에서 {self.vocab.label(attr)}의 {eff}화 시점 값을 하나로 확정하세요(예약과 자동 감지 충돌)."
                    changes.append(OntologyChange(op="contradiction", entity=ent.name,
                                                  detail=f"{self.vocab.label(attr)} {eff}화 시점 상충(예약 vs 감지 '{val}')",
                                                  applied=False, reason=f"동시점 충돌 — {_act}"))
                    self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)
                    continue
                ontology.set_state(eid, attr, val, eff, reason=f"{chapter}화 동적 감지", trust_tier=tier)
                new_tl.append(TimelineEntry(entity_id=eid, attr=attr, value=val, eff_from=eff,
                                            reason=f"{chapter}화 동적 감지", trust_tier=tier))
                changes.append(OntologyChange(op="state_change", entity=ent.name,
                                              detail=f"{self.vocab.label(attr)}: {cur}→{val}({eff}화부터)"
                                                     + (" · 후진(비구속)" if back else "")
                                                     + (" · 작가 확정 시 캐논"
                                                        if (tier == "narrative_inferred" and not back) else ""),
                                              applied=True))
            else:
                _act = f"{self.vocab.label(attr)}이(가) 실제로 변할 수 있는 속성이면 설정집에서 가변으로 바꾸세요. 본문 오류라면 교정해 재생성하세요."
                changes.append(OntologyChange(op="contradiction", entity=ent.name,
                                              detail=f"{self.vocab.label(attr)} 불변속성 변경 {cur}→{val}",
                                              applied=False, reason=f"불변 속성 변경 {cur}→{val} — {_act}"))
                self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)

        # 3) 관계 — 자동추출은 narrative_inferred(비binding, 비대칭 보존: ground_truth 자동승격 금지)
        def _resolve(x):
            x = (x or "").strip()
            return x if x in ontology.entities else amap.get(x)

        for rc in proposal.get("relations", []) or []:
            rel_id = (rc.get("rel_id") or "").strip()
            src, dst = _resolve(rc.get("src")), _resolve(rc.get("dst"))
            if not rel_id or not src or not dst or src == dst:   # 자유 타입 허용 — 카탈로그 FK 검사 폐기
                continue
            src, dst = ontology.order_edge(rel_id, src, dst)   # 대칭 관계 정렬 → A↔B 중복 방지
            rstate = (rc.get("state") or "").strip()
            if any(e.src_id == src and e.dst_id == dst and e.rel_id == rel_id
                   and e.eff_from <= chapter and (e.eff_to is None or chapter < e.eff_to)
                   for e in ontology.edges):                 # 이미 '활성(as-of)' 동일 관계 → skip(카디널리티 셈과 정의 일치)
                continue
            cviol = self._cardinality_violation(src, dst, rel_id, chapter, ontology)   # CN-4: 혈연/배타 카디널리티
            if cviol is not None:                            # 위반 → 커밋 안 함 + advisory(비차단)
                changes.append(cviol)
                self.bus.emit("ontology_update", "cardinality", chapter=chapter, entity=cviol.entity, rel=rel_id)
                continue
            edge = RelationEdge(edge_id=f"{rel_id}:{src}->{dst}:{chapter}", rel_id=rel_id,
                                src_id=src, dst_id=dst, state=rstate, eff_from=chapter, reason=rc.get("note", ""),
                                trust_tier="narrative_inferred", provenance=["machine"])
            ontology.add_edge(edge)
            new_edges.append(edge)
            label = ontology.rel_spec(rel_id).label
            detail = f"관계 추정: {label}" + (f" · {rstate}" if rstate else "") + "(추정, 작가 확정 시 캐논 승격)"
            changes.append(OntologyChange(op="relation",
                                          entity=f"{ontology.name(src)}→{ontology.name(dst)}",
                                          detail=detail, applied=True))
            self.bus.emit("ontology_update", "relation", chapter=chapter, rel=rel_id, src=src, dst=dst)

        return changes, new_specs, new_tl, new_edges
