# -*- coding: utf-8 -*-
"""온톨로지 SSOT — 결정론 lookup '박기'의 원천 + 순수 결정론(등급1) 내부 검사.

vocab 라벨은 주입(하드코딩 ATTR_LABEL 제거). 동적 업데이트(신규 인물/상태 변화) 지원하되,
ontology_internal_check 는 LLM 0콜로 SSOT 자기모순만 검사(진짜 결정론).
"""
from __future__ import annotations
from dataclasses import dataclass, field

from ..domain.types import OntologyFact, Violation, SignalGrade, RelationEdge
from ..domain.relations import RelationSpec, default_spec
from ..domain.world import EntityTypeSpec
from .vocabulary import Vocabulary


@dataclass
class Entity:
    id: str
    name: str
    etype: str                       # character | item | faction | worldrule | place
    attrs: dict
    aliases: list = field(default_factory=list)
    base_status: str = "alive"
    voice: str = ""                  # 말투 시그니처(보이스 분화 — 스타일 지침)
    provisional: bool = False        # 동적 커밋된 신규 인물


class Ontology:
    def __init__(self, vocab: Vocabulary):
        self.vocab = vocab
        self.entities: dict[str, Entity] = {}
        self.timeline: list[tuple] = []   # (eid, attr, value, eff_from, reason, trust_tier)
        self.rules: list[str] = []
        # R1 속성그래프: 엔티티↔엔티티 1급 엣지 + 카탈로그(데이터주도)
        self.edges: list[RelationEdge] = []
        self.rel_catalog: dict[str, RelationSpec] = {}
        self.entity_types: dict[str, EntityTypeSpec] = {}

    # ---- 구성 ----
    def add(self, e: Entity) -> None:
        self.entities[e.id] = e

    def add_rule(self, text: str) -> None:
        self.rules.append(text)

    def remove_rule(self, text: str) -> None:   # demote 역연산
        try:
            self.rules.remove(text)
        except ValueError:
            pass

    def set_state(self, eid, attr, value, eff_from, reason="", trust_tier="ground_truth") -> None:
        self.timeline.append((eid, attr, value, eff_from, reason, trust_tier))

    # ---- 조회 ----
    def state_as_of(self, eid, attr, chapter):
        """서사 인지 상태값(모든 tier 포함) — 표시/추출 컨텍스트용. 게이트 캐논은 binding_state_as_of."""
        ent = self.entities.get(eid)
        if not ent:
            return None
        val = ent.base_status if attr == "status" else ent.attrs.get(attr)
        best = -1
        for (e, a, v, f, _r, _t) in self.timeline:
            if e == eid and a == attr and f <= chapter and f > best:
                val, best = v, f
        return val

    def binding_state_as_of(self, eid, attr, chapter):
        """ground_truth-tier 상태만 반영하는 결정론 캐논값('박기'). 기계추출(narrative_inferred) 상태는 비구속 → 제외.
        canon_facts 주입과 사망 하드게이트가 이걸 본다(비대칭: AI 추출 상태는 작가 확정 전 자동 binding 금지)."""
        ent = self.entities.get(eid)
        if not ent:
            return None
        val = ent.base_status if attr == "status" else ent.attrs.get(attr)
        best = -1
        for (e, a, v, f, _r, t) in self.timeline:
            if e == eid and a == attr and t == "ground_truth" and f <= chapter and f > best:
                val, best = v, f
        return val

    def alias_map(self) -> dict[str, str]:
        m = {}
        for e in self.entities.values():
            for nm in [e.name] + list(e.aliases):
                m[nm] = e.id
        return m

    def is_actor(self, etype: str) -> bool:
        """상태/등장 게이트 대상(행동 주체) — 데이터주도. EntityTypeSpec.category=='actor'(인물·AI·괴수·신령 등).
        미등록 etype 은 'character' 만 actor(하위호환). character 하드코딩을 카테고리로 일반화."""
        t = self.entity_types.get(etype)
        return (t.category == "actor") if t is not None else (etype == "character")

    def scan_present_ids(self, text: str) -> list[str]:
        """본문에 이름/별칭 등장하는 행동주체(actor) id. 인물뿐 아니라 선언된 비인간 주체(AI/괴수)도 게이트 대상."""
        return [e.id for e in self.entities.values()
                if self.is_actor(e.etype) and any(nm and nm in text for nm in [e.name] + list(e.aliases))]

    def canon_facts(self, eids, chapter) -> list[OntologyFact]:
        """ground_truth 슬롯용 결정론 사실. '박기'. 라벨은 vocab 에서."""
        facts: list[OntologyFact] = []
        for eid in eids:
            e = self.entities.get(eid)
            if not e:
                continue
            if self.is_actor(e.etype):
                # 생애주기 '중대 상태'(terminal/irreversible = 사망·각성·발각 등)만 캐논 주입. 데이터주도('dead' 리터럴 제거):
                # death 없는 장르에 '생존' 노이즈 강제 안 함 + custom 한글 states 의 거짓 '생존' 주입 방지.
                st = self.binding_state_as_of(eid, "status", chapter)   # ground_truth(작가·시드)만 — 기계추출 비주입
                crit = self.vocab.terminal_states("status") | self.vocab.irreversible_states("status")
                if st is not None and st in crit:
                    spec = self.vocab.attr("status")
                    facts.append(OntologyFact(entity=e.name, attr_label=(spec.label if spec else "생사"),
                                              value=("사망" if st == "dead" else str(st))))
            for a in e.attrs:
                v = self.binding_state_as_of(eid, a, chapter)
                if v is not None:
                    facts.append(OntologyFact(entity=e.name, attr_label=self.vocab.label(a), value=str(v)))
        return facts

    # ---- 관계 엣지(자유 속성그래프) ----
    def rel_spec(self, rel_id: str) -> RelationSpec:
        """등록된 스펙 또는 자유 타입의 기본 스펙(미등록도 동작 — 개방형)."""
        return self.rel_catalog.get(rel_id) or default_spec(rel_id)

    def add_edge(self, e: RelationEdge) -> None:
        self.edges.append(e)

    def edge_state_as_of(self, src_id, dst_id, rel_id, chapter):
        """(src,dst,rel) 의 chapter 시점 활성 엣지(최신 eff_from 승) 또는 None. 반열림 [eff_from,eff_to)."""
        best = None
        for e in self.edges:
            if (e.src_id == src_id and e.dst_id == dst_id and e.rel_id == rel_id
                    and e.eff_from <= chapter and (e.eff_to is None or chapter < e.eff_to)):
                if best is None or e.eff_from > best.eff_from:
                    best = e
        return best

    def edges_as_of(self, chapter: int) -> list[RelationEdge]:
        return [e for e in self.edges
                if e.eff_from <= chapter and (e.eff_to is None or chapter < e.eff_to)]

    def neighbors(self, eid, chapter, direction: str = "both") -> list[RelationEdge]:
        out = []
        for e in self.edges_as_of(chapter):
            if direction in ("out", "both") and e.src_id == eid:
                out.append(e)
            elif direction in ("in", "both") and e.dst_id == eid:
                out.append(e)
        return out

    def order_edge(self, rel_id: str, src_id: str, dst_id: str) -> tuple:
        """대칭(무방향) 관계는 끝점을 정렬해 A→B / B→A 가 같은 엣지로 접히게. 방향관계는 그대로."""
        spec = self.rel_catalog.get(rel_id)
        if spec is not None and (spec.symmetric or not spec.directed):
            return tuple(sorted([src_id, dst_id]))
        return (src_id, dst_id)

    def active_edges_deduped(self, chapter: int) -> list[RelationEdge]:
        """(src,dst,rel) 그룹별 대표 활성 엣지 1건만. ground_truth(작가 확정) 우선, 동tier면 최신 eff_from
        — 더 늦은 narrative_inferred(추정)가 작가 확정 엣지를 그래프/승격화면에서 가리지 않게(비대칭 일관)."""
        best: dict = {}
        for e in self.edges_as_of(chapter):
            k = (e.src_id, e.dst_id, e.rel_id)
            cur = best.get(k)
            if cur is None:
                best[k] = e
                continue
            e_gt, c_gt = (e.trust_tier == "ground_truth"), (cur.trust_tier == "ground_truth")
            if (e_gt and not c_gt) or (e_gt == c_gt and e.eff_from > cur.eff_from):
                best[k] = e
        return list(best.values())

    def canon_relations(self, eids, chapter) -> list[OntologyFact]:
        """ground_truth 슬롯용 결정론 관계 사실('박기'). 작가 확정(ground_truth) + 객관(pov=None) 엣지만 대상.
        - 추정(narrative_inferred) 엣지가 작가 확정 관계를 밀어내 누락시키지 않음.
        - 관점(pov) 엣지는 '그 주체의 인식/믿음'(거짓 가능)이라 객관 캐논에 주입하지 않음(비대칭·관점 분리)."""
        wanted = set(eids)
        best: dict = {}
        for e in self.edges_as_of(chapter):
            if e.trust_tier != "ground_truth" or e.pov is not None:
                continue
            k = (e.src_id, e.dst_id, e.rel_id)
            if k not in best or e.eff_from > best[k].eff_from:
                best[k] = e
        facts: list[OntologyFact] = []
        for e in best.values():
            if e.src_id not in wanted and e.dst_id not in wanted:
                continue
            src, dst = self.entities.get(e.src_id), self.entities.get(e.dst_id)
            if not src or not dst:
                continue
            label = self.rel_spec(e.rel_id).label
            value = f"{dst.name}({e.state})" if e.state else dst.name   # 질적 상태가 있으면 함께(예: 동맹(소원))
            facts.append(OntologyFact(entity=src.name, attr_label=f"관계:{label}", value=value))
        return facts

    def _name(self, eid: str) -> str:
        e = self.entities.get(eid)
        return e.name if e else eid

    def name(self, eid: str) -> str:   # 공개 별칭(외부 모듈용 — 캡슐화)
        return self._name(eid)

    def _terminal_map(self) -> dict:
        """엔티티별 최초 '제거(terminal)' 시점 — 사망은 그 한 인스턴스(데이터주도, 하드코딩 제거).
        base + ground_truth-tier timeline 의 terminal 상태(vocab.terminal_states)만. 기계추출(narrative_inferred)은 비구속 → 제외."""
        term: dict = {}
        status_term = self.vocab.terminal_states("status")
        for eid, e in self.entities.items():
            if e.base_status in status_term:
                term[eid] = 1
        for (eid, attr, val, eff, _r, t) in self.timeline:
            if t == "ground_truth" and str(val) in self.vocab.terminal_states(attr):
                term[eid] = min(term.get(eid, 1 << 30), eff)
        return term

    def _death_map(self) -> dict:   # 하위호환 별칭
        return self._terminal_map()

    def _in_terminal_state(self, eid, chapter) -> bool:
        """엔티티가 chapter 시점에 '제거(terminal)' 상태인가 — binding(ground_truth) 기준.
        부활/reversal(나중 ground_truth 비-terminal 상태)이면 binding 값이 갱신돼 자연히 False → 회귀/부활 후 관계 허용.
        '최초 사망 이후 전부 차단'(단조)이 아니라 '그 시점에 실제로 terminal 인가'로 판정(이탈 반영)."""
        attrs = {"status"} | {t[1] for t in self.timeline}
        for attr in attrs:
            term = self.vocab.terminal_states(attr)
            if term and self.binding_state_as_of(eid, attr, chapter) in term:
                return True
        return False

    def ontology_internal_check(self, chapter: int | None = None) -> list[Violation]:
        """순수 결정론(등급1): SSOT 내부 모순(LLM 0콜).
        chapter 를 주면 엣지 검사를 '그 시점 활성 엣지'로 한정 → 미래 엣지가 현재 회차 하드 게이트를 오염시키지 않음.
        chapter=None 은 전역 감사(모든 엣지). 노드(timeline) 검사는 chapter 무관(SSOT 자기일관성)."""
        viols: list[Violation] = []
        # SSOT 하드 자기일관성 검사는 ground_truth-tier 상태만 대상(기계추출 narrative_inferred 는 비구속).
        gt_timeline = [t for t in self.timeline if t[5] == "ground_truth"]
        by_key: dict = {}
        for (eid, attr, val, eff, _r, _t) in gt_timeline:
            by_key.setdefault((eid, attr, eff), set()).add(str(val))
        for (eid, attr, eff), vals in by_key.items():
            if len(vals) > 1:
                viols.append(Violation(entity=self._name(eid), kind="ssot_ambiguous",
                                       grade=SignalGrade.DETERMINISTIC, canon=f"{attr}@{eff}화",
                                       text=f"동시 값 {sorted(vals)}", evidence="(entity,attr,eff) 복수 값"))
        # 제거(terminal) 이후 속성 변경 — 데이터주도 + reversal 인지(binding-state). state-attr 자체 변경은 제외.
        for (eid, attr, val, eff, _r, _t) in gt_timeline:
            if not self.vocab.terminal_states(attr) and self._in_terminal_state(eid, eff):
                viols.append(Violation(entity=self._name(eid), kind="post_death_change",
                                       grade=SignalGrade.DETERMINISTIC, canon=f"{eff}화 시점 제거상태",
                                       text=f"{attr} 변경 {eff}화 예약", evidence="제거(사망 등) 이후 속성 변경"))
        # ---- 엣지 결정론 검사(LLM 0콜): self-loop / dangling / post-death. 중복 위반 dedup ----
        # 하드 게이트는 'ground_truth'(작가 확정) + 객관(pov=None) 엣지에만.
        # narrative_inferred(자동추출)·관점(pov, 믿음/인식)은 비binding(블로킹 금지).
        base = self.edges if chapter is None else self.edges_as_of(chapter)
        edge_set = [e for e in base if e.trust_tier == "ground_truth" and e.pov is None]
        seen: set = set()

        def _add(key, v):
            if key not in seen:
                seen.add(key)
                viols.append(v)

        for e in edge_set:
            if e.src_id == e.dst_id:
                _add(("self", e.rel_id, e.src_id, e.eff_from),
                     Violation(entity=self._name(e.src_id), kind="edge_self_loop",
                               grade=SignalGrade.DETERMINISTIC, canon=e.rel_id, text="src==dst",
                               evidence="자기참조 엣지 금지"))
            for endpoint in (e.src_id, e.dst_id):
                if endpoint not in self.entities:
                    _add(("dangling", e.rel_id, e.src_id, e.dst_id, endpoint),
                         Violation(entity=endpoint, kind="edge_dangling", grade=SignalGrade.DETERMINISTIC,
                                   canon=e.rel_id, text=f"{e.src_id}->{e.dst_id}",
                                   evidence="엣지 끝점 엔티티 부재"))
                elif self._in_terminal_state(endpoint, e.eff_from):   # reversal 인지: 부활 후 시점이면 False
                    _add(("postdeath", e.rel_id, e.src_id, e.dst_id, endpoint, e.eff_from),
                         Violation(entity=self._name(endpoint), kind="edge_post_death",
                                   grade=SignalGrade.DETERMINISTIC, canon=f"{e.eff_from}화 시점 제거상태",
                                   text=f"{e.rel_id} 엣지 {e.eff_from}화", evidence="제거(사망 등) 이후 새 관계 성립"))
        return viols
