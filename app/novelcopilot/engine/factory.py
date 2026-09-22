# -*- coding: utf-8 -*-
"""엔진 팩토리 — WorldConfig(데이터) → 동작하는 엔진 일습(Factory 패턴).

룰 레지스트리·통제어휘·온톨로지·추출 스키마가 전부 world 에서 파생된다.
새 세계관 = 새 데이터. 코드 변경 0. (하드코딩 배제의 귀결점)
"""
from __future__ import annotations
from dataclasses import dataclass

from ..config import Settings
from ..domain.world import WorldConfig, BUILTIN_ENTITY_TYPES, DEFAULT_STATUS_ATTR
from ..domain.relations import merged_catalog
from ..domain.types import RuleSpec, SignalGrade, WikiPage
from ..llm.base import LLMProvider
from .vocabulary import Vocabulary
from .ontology import Ontology, Entity
from .rules import RuleEngine
from .extractor import ClaimExtractor
from .checker import Checker
from .rag import RAG
from .wiki import Wiki
from .harness import ChapterGenerator
from .ontology_updater import OntologyUpdater
from .observability import EventBus


def build_rules(world: WorldConfig) -> list[RuleSpec]:
    """AttributeSpec/WorldRuleSpec → RuleSpec row(코드 분기 없이 데이터로)."""
    rules: list[RuleSpec] = []
    # 생애주기 terminal 상태('제거'=등장 불가) → present_acting 금지 하드룰. 데이터주도 — death 는 기본 status 의 한 인스턴스
    # (장르 불문 'dead' 하드코딩 제거: 각성/소멸/폐인 등 작가가 선언한 terminal 도 동일 기계로).
    state_attrs = [a for a in world.attributes if a.kind in ("state", "status")]
    if not any(a.key == "status" for a in state_attrs):
        state_attrs = state_attrs + [DEFAULT_STATUS_ATTR]
    for sa in state_attrs:
        for term in (sa.terminal or (["dead"] if sa.key == "status" else [])):
            rules.append(RuleSpec(rule_id=f"{sa.key}_{term}_acting", layer="status",
                                  predicate_kind="timeline_state", grade=SignalGrade.QUASI,
                                  params={"attr": sa.key, "forbidden_state": term,
                                          "forbidden_appearance": "present_acting"}))
    for a in world.attributes:
        if a.kind == "categorical":
            # 가변(mutable) categorical = '진행형 상태'(관계단계·소속·적응 등): 값 변화는 *진전*이지 모순이 아니다.
            # OntologyUpdater 가 이를 timeline 진전으로 반영·가시화(state_change)한다 — 같은 변화를 체커가
            # 하드 동등성(categorical_eq QUASI)으로 또 잡으면 '자연스런 전개 = 매 회차 설정충돌 → 비수렴 → ESCALATED'.
            # 잔잔 로맨스/일상물(관계가 변하는 게 본질)에 치명. mutable=False(눈색 등 불변 사실)만 하드 캐논으로 강제.
            if a.mutable:
                continue
            rules.append(RuleSpec(rule_id=a.key, layer=a.key, predicate_kind="categorical_eq",
                                  grade=SignalGrade.QUASI, params={"attr": a.key}))
        elif a.kind == "numeric" and a.monotonic:
            rules.append(RuleSpec(rule_id=f"{a.key}_mono", layer=a.key, predicate_kind="numeric_monotone",
                                  grade=SignalGrade.QUASI, params={"attr": a.key, "direction": a.monotonic}))
    for wr in world.world_rules:
        rules.append(RuleSpec(rule_id=wr.rule_id, layer="worldrule", predicate_kind="worldrule_flag",
                              grade=SignalGrade.SEMANTIC,
                              params={"flag": wr.flag, "rule_keywords": wr.keywords}))
    return rules


def build_ontology(world: WorldConfig, vocab: Vocabulary) -> Ontology:
    o = Ontology(vocab)
    types = world.entity_types or BUILTIN_ENTITY_TYPES        # 빈→BUILTIN 시드(하위호환)
    o.entity_types = {t.key: t for t in types}
    o.rel_catalog = merged_catalog(world.relations)           # REL_CATALOG + 작품별
    for es in world.entities:
        o.add(Entity(id=es.id, name=es.name, etype=es.etype, attrs=dict(es.attrs),
                     aliases=list(es.aliases), base_status=es.base_status,
                     voice=getattr(es, "voice", ""), provisional=es.provisional,
                     voice_stages=dict(getattr(es, "voice_stages", None) or {}),   # VB-1: 상태 연동 보이스 미러(비면 기존 동작)
                     introduced=bool(getattr(es, "introduced", False)),        # RR-1: 데뷔 상태 미러(프로즈 명부 노출 차단용)
                     debut_episode=getattr(es, "debut_episode", "") or "",     # RR-1: 데뷔 계획 미러
                     cardinality=dict(getattr(es, "cardinality", None) or {})))   # CN-4 관계 상한 로드
    for wr in world.world_rules:
        # CX-2: internal 규칙(반전 서술 지침 등 스포 평문)은 생성 입력 채널(ontology.rules)에서 제외 —
        #   심사(build_rules)·아크 계획(world.world_rules 직접 참조)에는 그대로 남는다. 기본 public=바이트 동일.
        if getattr(wr, "exposure", "public") == "internal":
            continue
        # CN-5: 열거표가 있으면 고신뢰 주입 텍스트에 대응을 명시(집필이 정확한 키→값 보유=예방). WorldRuleSpec.text 는 원형 유지.
        tbl = getattr(wr, "table", None)
        suffix = (" [대응표: " + ", ".join(f"{k}={v}" for k, v in tbl.items()) + "]") if tbl else ""
        o.add_rule(wr.text + suffix)
    for t in world.timeline:
        o.set_state(t.entity_id, t.attr, t.value, t.eff_from, reason=t.reason)
    return o   # seed_edges 는 build_engine 에서 검증 후 적재(무검증 적재 시 영구 dangling 락 방지)


@dataclass
class EngineBundle:
    vocab: Vocabulary
    ontology: Ontology
    rag: RAG
    wiki: Wiki
    checker: Checker
    generator: ChapterGenerator
    updater: OntologyUpdater
    event_bus: EventBus
    aux_provider: LLMProvider | None = None   # CE-1 ⓑ: 보조 스테이지 provider(copilot 의 ledger/propose 델타 계측용). 폴백 시 gen provider


def build_engine(world: WorldConfig, provider: LLMProvider, settings: Settings,
                 event_bus: EventBus | None = None,
                 aux_provider: LLMProvider | None = None,
                 extract_provider: LLMProvider | None = None) -> EngineBundle:
    # CE-1 ⓑ: aux_provider = 보조(기계) 스테이지용 저가 provider. 미주입이면 gen provider 로 폴백 →
    #   Wiki/OntologyUpdater/generator._summarize 가 전부 동일 provider 로 돌아 기존 경로 바이트 동일(하위호환).
    #   세션이 aux_model 로부터 1회 생성해 주입한다(콜마다 재생성 금지). embed 는 여전히 OpenAI 위임(RAG 일관).
    # extract_provider = 캐논 추출(ClaimExtractor) 전용 — 미주입이면 gen provider 폴백(스왑 0·기존 경로 동일).
    aux = aux_provider or provider
    exp = extract_provider or provider
    bus = event_bus or EventBus()
    vocab = Vocabulary.from_world(world)
    ontology = build_ontology(world, vocab)

    # 엔티티 타입 카탈로그 멤버십 검증(데이터주도 완화의 안전장치 — 조용한 정지 금지)
    for es in world.entities:
        if es.etype not in ontology.entity_types:
            bus.emit("factory", "unknown_entity_type", entity=es.id, etype=es.etype)
    # timeline 참조 무결성(worldgen LLM 이 미존재 id 를 만들 수 있음 — KeyError 방지·가시화)
    for t in world.timeline:
        if t.entity_id not in ontology.entities:
            bus.emit("factory", "unknown_timeline_entity", entity=t.entity_id, attr=t.attr)
    # seed_edges 검증 적재(rel_id 멤버십·끝점 존재·self-loop) — 서비스 add_relation 과 동치. 무효는 드롭+가시화.
    for e in world.seed_edges:
        # 서비스 add_relation 과 '동치': 자유 타입 허용(카탈로그 FK 검사 폐기 — 미등록 rel_id 도 rel_spec 으로 적재).
        # 제약(allowed_src/dst_types)은 등록 spec 이 선언했을 때만 opt-in. 끝점 존재·self-loop·order_edge 정규화만 강제.
        rspec = ontology.rel_spec(e.rel_id)
        valid = (e.src_id in ontology.entities and e.dst_id in ontology.entities and e.src_id != e.dst_id)
        if valid and rspec.allowed_src_types and ontology.entities[e.src_id].etype not in rspec.allowed_src_types:
            valid = False
        if valid and rspec.allowed_dst_types and ontology.entities[e.dst_id].etype not in rspec.allowed_dst_types:
            valid = False
        if valid:
            s_id, d_id = ontology.order_edge(e.rel_id, e.src_id, e.dst_id)   # 대칭 정규화(중복 캐논 방지)
            if (s_id, d_id) != (e.src_id, e.dst_id) or not e.edge_id:
                e = e.model_copy(update={"src_id": s_id, "dst_id": d_id,
                                         "edge_id": e.edge_id or f"{e.rel_id}:{s_id}->{d_id}:{e.eff_from}"})
            ontology.add_edge(e)
        else:
            bus.emit("factory", "invalid_seed_edge",
                     edge_id=e.edge_id or f"{e.rel_id}:{e.src_id}->{e.dst_id}", rel_id=e.rel_id)

    extractor = ClaimExtractor(exp, vocab, list(world.world_rules))   # copy: world.world_rules 와 공유 금지(promote 시 이중 append 방지)
    rule_engine = RuleEngine(build_rules(world), vocab)
    checker = Checker(extractor, rule_engine)

    rag = RAG(provider)
    wiki = Wiki(aux)   # CE-1 ⓑ: wiki(인물카드 재생성)=aux. embed 는 aux 내부에서 OpenAI 위임(RAG 일관·비용 동일)
    for seed in world.wiki_seeds:
        wiki.seed_page(WikiPage(page_id=seed.page_id, page_type=seed.page_type, body=seed.body,
                                payoff_deadline=seed.payoff_deadline,
                                as_of_narrative_order=seed.as_of_narrative_order,
                                provenance=[f"{seed.as_of_narrative_order}화"]))

    generator = ChapterGenerator(provider, checker, world.style, bus, settings, aux_provider=aux)  # CE-1 ⓑ: _summarize=aux
    updater = OntologyUpdater(aux, vocab, bus, allow_reversal=world.allow_state_reversal)   # CE-1 ⓑ: ontology_propose=aux
    return EngineBundle(vocab=vocab, ontology=ontology, rag=rag, wiki=wiki, checker=checker,
                        generator=generator, updater=updater, event_bus=bus, aux_provider=aux)
