# -*- coding: utf-8 -*-
"""동적 온톨로지 업데이트 — 엔진 고도화 ①.

회차 finalize 후, 본문에서 '새 인물 / 상태·관계 변화'를 추출해 SSOT 를 점진 갱신한다.
정책(데이터 주도, AttributeSpec.mutable 기반):
- 신규 인물: 자동 커밋(provisional=True). 기존 인물 별칭과 겹치면 skip.
- mutable 속성 변화(예: 소속 변절, 등급 상승, 사망): timeline 에 eff_from=다음화로 progress 반영.
- immutable 속성 변화(예: 눈색) / 단조 위반(등급 하락) / 사망→생존: 모순 → 미적용 + escalation.
'덮어쓰기 없음, 추가/전진만, 모순은 사람에게'. LLM은 추출만, 정책 판정은 코드(비대칭 계승).
"""
from __future__ import annotations
import json
import re

from ..domain.types import OntologyChange, RelationEdge
from ..domain.world import TimelineEntry, EntitySpec
from ..llm.base import LLMProvider
from .ontology import Entity, Ontology
from .vocabulary import Vocabulary
from .observability import EventBus


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

    def propose(self, text: str, ontology: Ontology, chapter: int,
                existing_setting_titles: list[str] | None = None) -> dict:
        roster = [{"id": e.id, "name": e.name, "etype": e.etype} for e in ontology.entities.values()]
        attr_keys = [a.key for a in self.vocab.values()]
        rel_keys = list(ontology.rel_catalog.keys())
        ent_types = list(ontology.entity_types.keys())
        # 생애주기 상태 어휘를 데이터에서 — 'status: alive|dead' 하드코딩 제거(장르별 선언 states 를 그대로 전달)
        state_hint = "; ".join(f"{a.key}:{a.states}" for a in self.vocab.state_specs() if a.states)
        # 통제어휘를 상류에서 고지(미고지 → 표면 변이 보고 → 하류 escalation 소음의 소스 차단). raw vocab 사용(게이트와 동일 기준)
        cat_hint = "; ".join(f"{a.key}:{a.vocab}" for a in self.vocab.values()
                             if a.kind == "categorical" and a.vocab)
        msg = [
            {"role": "system", "content":
             "너는 작품 설정 관리자다. 이번 회차 본문에서 '명시적으로' 드러난 구조적 사실만 보고하라. 추측 금지.\n"
             "1) 기존 명부에 없는 '새 엔티티'(고유명·역할 분명): 인물뿐 아니라 세력·장소·아이템·사건도. etype 지정.\n"
             "2) 기존 엔티티의 상태/소속/등급 변화(본문이 분명히 말한 것만).\n"
             "3) 엔티티 사이 관계(동맹/적대/사제/소속/소유/위치/친구/연모/앎 등). 제시된 관계키 우선, 없으면 간결한 자유 라벨. "
             "관계의 질적 현재 상태가 드러나면 state(예: '소원해짐','잃어버림','짝사랑').\n"
             "4) 이번 회차에서 새로 드러난 '세계 설정'(장소의 내력, 체계의 규칙/예외, 세력의 관습 등 — 인물 상태 말고 세계 지식). "
             "기존 설정집 제목과 중복 금지, 최대 2개.\n"
             "JSON: {\"new_entities\":[{\"name\":\"\",\"etype\":\"\",\"aliases\":[],\"role\":\"\"}],"
             "\"state_changes\":[{\"id\":\"명부 id\",\"attr\":\"속성키\",\"value\":\"새 값\",\"note\":\"\"}],"
             "\"relations\":[{\"src\":\"명부 id 또는 새 이름\",\"dst\":\"명부 id 또는 새 이름\",\"rel_id\":\"관계키 또는 자유 라벨\",\"state\":\"\",\"note\":\"\"}],"
             "\"new_settings\":[{\"category\":\"\",\"title\":\"\",\"prose\":\"3~5문장\",\"keywords\":[\"\"]}]}"},
            {"role": "user", "content":
             f"[기존 명부]\n{json.dumps(roster, ensure_ascii=False)}\n"
             f"[엔티티 타입]{ent_types}\n[추적 속성키]{attr_keys}\n[생애주기 상태값]{state_hint}\n[관계키(자유 라벨 가능)]{rel_keys}\n"
             f"[{chapter}화 본문]\n{text}"},
        ]
        try:
            return self.provider.chat_json(msg, temperature=0.0)
        except Exception:
            self.bus.emit("ontology_update", "parse_failure", chapter=chapter)
            return {}

    def apply(self, proposal: dict, ontology: Ontology, chapter: int
              ) -> tuple[list[OntologyChange], list[EntitySpec], list[TimelineEntry], list[RelationEdge]]:
        changes: list[OntologyChange] = []
        new_specs: list[EntitySpec] = []
        new_tl: list[TimelineEntry] = []
        new_edges: list[RelationEdge] = []
        amap = ontology.alias_map()

        # 관계에서 참조된 이름(구조적으로 연결됨 → 노드화 가치 있음)
        STRUCTURAL = {"character", "faction", "organization", "place", "location", "race"}
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
            if etype not in STRUCTURAL and name not in rel_names:
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
                                                  reason=f"선언된 states 밖 상태값 '{newv}' — {_act}"))
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
                binding_irrev = (newv in irr) or (newv in term)
                tier = "narrative_inferred" if binding_irrev else "ground_truth"
                tag = "(추정)" if binding_irrev else ""
                ontology.set_state(eid, attr, newv, eff, reason=f"{chapter}화 동적 감지{tag}", trust_tier=tier)
                new_tl.append(TimelineEntry(entity_id=eid, attr=attr, value=newv, eff_from=eff,
                                            reason=f"{chapter}화 동적 감지{tag}", trust_tier=tier))
                detail = f"{slabel}: {curv}→{newv}({eff}화부터)" + (" · 작가 확정 시 캐논" if binding_irrev else "")
                changes.append(OntologyChange(op="state_change", entity=ent.name, detail=detail, applied=True))
                continue

            if cur is not None and str(cur).strip() == str(val).strip():
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
                                                      applied=False, reason=f"단조 제약 위반 {cur}→{val} — {_act}"))
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
                                              applied=False, reason=f"통제어휘 밖 값 '{val}' — {_act}"))
                self.bus.emit("ontology_update", "escalation", chapter=chapter, entity=ent.name, attr=attr, action=_act)
                continue
            mutable = bool(spec and spec.mutable)
            if mutable:
                # M1: 캐논 주입집합 = 게이트집합. 신규 추적 속성 키를 엔티티에 등록해
                #     canon_facts(ground_truth 주입)가 이 속성을 빠뜨리지 않게(게이트만 걸고 미주입되는 비대칭 제거).
                ent.attrs.setdefault(attr, None)
                # 비-status 전진은 기존 '추가/전진만' 정책 유지(ground_truth). 단조/불변/사망복귀는 위에서 escalation.
                ontology.set_state(eid, attr, val, eff, reason=f"{chapter}화 동적 감지")
                new_tl.append(TimelineEntry(entity_id=eid, attr=attr, value=val, eff_from=eff,
                                            reason=f"{chapter}화 동적 감지"))
                changes.append(OntologyChange(op="state_change", entity=ent.name,
                                              detail=f"{self.vocab.label(attr)}: {cur}→{val}({eff}화부터)",
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
            if any(e.src_id == src and e.dst_id == dst and e.rel_id == rel_id and e.eff_to is None
                   for e in ontology.edges):                 # 이미 활성 동일 관계 → skip
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
