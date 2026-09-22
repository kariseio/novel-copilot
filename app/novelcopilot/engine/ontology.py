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
    voice_stages: dict = field(default_factory=dict)   # VB-1: 상태 연동 보이스(EntitySpec.voice_stages 미러). key=상태값, value=그 단계 카드. 비면 voice 사용 = 기존 동작
    provisional: bool = False        # 동적 커밋된 신규 인물
    introduced: bool = False         # RR-1: 본문 첫 등장 완료(EntitySpec.introduced 미러 — 프로즈 생성 시 데뷔 전 인물 명부 노출 차단용). SSOT 는 state.world.entities
    debut_episode: str = ""          # RR-1: 데뷔 계획 에피소드(EntitySpec.debut_episode 미러). 미등장·미캐스트 판정 보조
    cardinality: dict = field(default_factory=dict)   # CN-4: 관계 개수 상한(rel_id→max). 예 외동={"sibling_of":0}. 비면 무제한


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
        # CN-5: 주입 시 열거표 접미사(' [대응표: ...]')가 붙을 수 있음 → 원형 텍스트 또는 그 접두 매칭까지 제거
        #  (exact-match 실패로 orphan 고신뢰 캐논이 잔존하던 결함 차단, 적대검증 MED-1). 결정론(고정 마커).
        self.rules = [r for r in self.rules if r != text and not r.startswith(text + " [대응표:")]

    def set_state(self, eid, attr, value, eff_from, reason="", trust_tier="ground_truth") -> None:
        # upsert(last-writer-wins) by (eid,attr,eff,tier): 같은 시점에 *다른* ground_truth 값이 쌓이면
        # ontology_internal_check 가 ssot_ambiguous(DETERMINISTIC)를 영구 점등해 이후 모든 회차가 ESCALATED 로
        # 봉인되고 복구도 불가했다(재생성·작가오버라이드 — 적대검증). 한 시점 한 값으로 강제: 중복 제거 후 추가.
        # 재수화 시에도 동일 dedup 이 적용돼 *이미 봉인된 작품이 다음 로드에 자동 치유*된다.
        self.timeline = [t for t in self.timeline
                         if not (t[0] == eid and t[1] == attr and t[3] == eff_from and t[5] == trust_tier)]
        self.timeline.append((eid, attr, value, eff_from, reason, trust_tier))

    # ---- 조회 ----
    def state_as_of(self, eid, attr, chapter):
        """서사 인지 상태값(모든 tier 포함) — 표시/추출 컨텍스트용. 게이트 캐논은 binding_state_as_of."""
        ent = self.entities.get(eid)
        if not ent:
            return None
        val = ent.base_status if attr == "status" else ent.attrs.get(attr)
        best, best_gt = -1, False
        for (e, a, v, f, _r, t) in self.timeline:
            if e == eid and a == attr and f <= chapter:
                gt = (t == "ground_truth")
                # 더 늦은 eff 우선 — 동률이면 ground_truth 우선(삽입순서 의존 제거: 같은 시점 gt/ni 공존 시 binding 값 채택)
                if f > best or (f == best and gt and not best_gt):
                    val, best, best_gt = v, f, gt
        return val

    def binding_entry_as_of(self, eid, attr, chapter) -> tuple:
        """XR-3 계보용 — binding 값과 '그 값이 어디서 왔는가'를 함께 준다: (값, eff_from|None, 출처).
        출처는 'timeline'(회차 커밋) 또는 'seed_attr'(EntitySpec 초기값). 값 선택 규칙은
        binding_state_as_of 와 동일한 루프다(계산 지점 단일화 — 계보와 주입값이 갈라지지 않게).

        결측 정직(K1): 런타임 timeline 튜플은 (eid, attr, value, eff_from, reason, trust_tier) 6원소로
        TimelineEntry.provenance(machine|author)를 싣지 않는다(factory:84·session:59 직렬화에서 소실).
        provenance 를 계보에 담으려면 튜플 형태를 바꿔야 하고 그건 조립 경로 변경이라 기록을 포기한다 —
        world_rules 의 rule_id 소실과 같은 처리(호출부가 gap 으로 표기)."""
        ent = self.entities.get(eid)
        if not ent:
            return (None, None, "")
        val = ent.base_status if attr == "status" else ent.attrs.get(attr)
        best, src = -1, "seed_attr"
        for (e, a, v, f, _r, t) in self.timeline:
            if e == eid and a == attr and t == "ground_truth" and f <= chapter and f > best:
                val, best, src = v, f, "timeline"
        return (val, (best if best >= 0 else None), src)

    def binding_state_as_of(self, eid, attr, chapter):
        """ground_truth-tier 상태만 반영하는 결정론 캐논값('박기'). 기계추출(narrative_inferred) 상태는 비구속 → 제외.
        canon_facts 주입과 사망 하드게이트가 이걸 본다(비대칭: AI 추출 상태는 작가 확정 전 자동 binding 금지)."""
        return self.binding_entry_as_of(eid, attr, chapter)[0]

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

    # ── CX-2 노출 등급 — 생성 입력용 단일 질의점 ──
    def is_public_attr(self, attr: str) -> bool:
        """생성 입력(확정 설정·조회·cast)에 보여도 되는 속성인가. 스펙 미등록 속성은 public(하위호환)."""
        spec = self.vocab.attr(attr)
        return getattr(spec, "exposure", "public") == "public" if spec is not None else True

    def public_attrs(self, eid, chapter) -> list[tuple[str, object, bool]]:
        """개체의 public 속성 (attr, 값, is_binding) 목록 — 생성 입력 소비처(canon_facts·lookup·cast) 공용.

        값 규약(CX-3 채널 일관): 같은 속성에 binding(ground_truth) 값이 있으면 그것만, 없을 때만
        비구속(state_as_of) 값을 is_binding=False 로 — push/lookup 이 서로 다른 값을 싣는 모순의 소스 차단.
        internal 속성은 여기서 걸러진다(검증·심사·UI 는 무필터 state_as_of 경로를 그대로 쓴다)."""
        e = self.entities.get(eid)
        if not e:
            return []
        out: list[tuple[str, object, bool]] = []
        for a in e.attrs:
            if not self.is_public_attr(a):
                continue
            b = self.binding_state_as_of(eid, a, chapter)
            if b is not None:
                out.append((a, b, True))
                continue
            v = self.state_as_of(eid, a, chapter)
            if v is not None:
                out.append((a, v, False))
        return out

    def canon_facts(self, eids, chapter, *, actors_status_only: bool = False,
                    with_lineage: bool = False):
        """ground_truth 슬롯용 결정론 사실. '박기'. 라벨은 vocab 에서. CX-2: public 속성만(내부 계측 축 비주입).

        actors_status_only(CX-3): True 면 행동주체(인물)의 속성 push 를 생략하고 생사 중대 상태만 남긴다 —
        인물 사실은 조회(lookup) 단일 경로가 전담(3중 주입·채널 모순 해소). 세계 상수 등 비행동주체는 계속 push.
        기본 False = 기존 동작 그대로(gen_tools OFF 하위호환).

        with_lineage(XR-3): True 면 (facts, lineage_items) 튜플 — facts 는 False 일 때와 완전 동일하고
        (주입 바이트 불변 계약·테스트 고정) lineage_items 는 facts 와 1:1 순서 대응하는 출처 기록이다
        (ID·eff_from·tier·채택 사유). 기본 False = 기존 시그니처·반환형 그대로."""
        facts: list[OntologyFact] = []
        lin: list[dict] = []
        for eid in eids:
            e = self.entities.get(eid)
            if not e:
                continue
            if self.is_actor(e.etype):
                # 생애주기 '중대 상태'(terminal/irreversible = 사망·각성·발각 등)만 캐논 주입. 데이터주도('dead' 리터럴 제거):
                # death 없는 장르에 '생존' 노이즈 강제 안 함 + custom 한글 states 의 거짓 '생존' 주입 방지.
                st, _eff, _org = self.binding_entry_as_of(eid, "status", chapter)   # ground_truth(작가·시드)만 — 기계추출 비주입
                crit = self.vocab.terminal_states("status") | self.vocab.irreversible_states("status")
                if st is not None and st in crit:
                    spec = self.vocab.attr("status")
                    facts.append(OntologyFact(entity=e.name, attr_label=(spec.label if spec else "생사"),
                                              value=("사망" if st == "dead" else str(st))))
                    lin.append({"src": "canon_fact", "id": f"{eid}.status", "eff_from": _eff,
                                "tier": "ground_truth", "origin": _org, "reason": "critical_status"})
            if actors_status_only and self.is_actor(e.etype):
                continue   # CX-3: 인물 속성은 조회 단일 경로 — 위 생사 중대 상태만 push
            for a, v, is_binding in self.public_attrs(eid, chapter):
                if a == "status":
                    continue   # 생애주기는 위 중대 상태 분기가 전담(이중 주입 방지 — 기존 동작 유지)
                if is_binding:   # push 는 종전대로 binding 만(비구속 값은 조회 채널 몫)
                    facts.append(OntologyFact(entity=e.name, attr_label=self.vocab.label(a), value=str(v)))
                    _, _eff2, _org2 = self.binding_entry_as_of(eid, a, chapter)
                    lin.append({"src": "canon_fact", "id": f"{eid}.{a}", "eff_from": _eff2,
                                "tier": "ground_truth", "origin": _org2, "reason": "binding_state_as_of"})
        return (facts, lin) if with_lineage else facts

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

    def canon_relations(self, eids, chapter, *, with_lineage: bool = False):
        """ground_truth 슬롯용 결정론 관계 사실('박기'). 작가 확정(ground_truth) + 객관(pov=None) 엣지만 대상.
        - 추정(narrative_inferred) 엣지가 작가 확정 관계를 밀어내 누락시키지 않음.
        - 관점(pov) 엣지는 '그 주체의 인식/믿음'(거짓 가능)이라 객관 캐논에 주입하지 않음(비대칭·관점 분리).

        with_lineage(XR-3): True 면 (facts, lineage_items) — facts 는 False 일 때와 동일. 엣지는 노드
        timeline 과 달리 provenance 를 런타임까지 보존하므로(RelationEdge.provenance) 계보에 실린다."""
        wanted = set(eids)
        best: dict = {}
        for e in self.edges_as_of(chapter):
            if e.trust_tier != "ground_truth" or e.pov is not None:
                continue
            k = (e.src_id, e.dst_id, e.rel_id)
            if k not in best or e.eff_from > best[k].eff_from:
                best[k] = e
        facts: list[OntologyFact] = []
        lin: list[dict] = []
        for e in best.values():
            if e.src_id not in wanted and e.dst_id not in wanted:
                continue
            src, dst = self.entities.get(e.src_id), self.entities.get(e.dst_id)
            if not src or not dst:
                continue
            label = self.rel_spec(e.rel_id).label
            value = f"{dst.name}({e.state})" if e.state else dst.name   # 질적 상태가 있으면 함께(예: 동맹(소원))
            facts.append(OntologyFact(entity=src.name, attr_label=f"관계:{label}", value=value))
            lin.append({"src": "canon_relation",
                        "id": (e.edge_id or f"{e.rel_id}:{e.src_id}->{e.dst_id}:{e.eff_from}"),
                        "eff_from": e.eff_from, "tier": e.trust_tier,
                        "provenance": list(e.provenance or []), "reason": "edges_as_of·objective"})
        return (facts, lin) if with_lineage else facts

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
