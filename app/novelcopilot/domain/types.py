# -*- coding: utf-8 -*-
"""엔진 공유 타입 계약 (Pydantic v2). 비대칭 일관성 불변식을 '타입'으로 강제.

- ContextBoard 의 ground_truth / narrative / authority 는 서로 다른 타입 → 혼합 불가.
- 모든 Violation 은 signal_grade 를 갖는다 → det/quasi 만 binding, semantic 은 보고·escalation.
"""
from __future__ import annotations
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field


class SignalGrade(str, Enum):
    DETERMINISTIC = "deterministic"   # 입력에 LLM 산출물 0 (SSOT 내부·그래프·숫자/시점 비교)
    QUASI = "quasi-deterministic"     # LLM 추출 + 코드 비교 (통제어휘·시점상태·등급)
    SEMANTIC = "semantic"             # LLM 판단 — 게이트 비구속(보고/escalation)


class Violation(BaseModel):
    entity: str
    kind: str
    grade: SignalGrade
    canon: str = ""
    text: str = ""
    evidence: str = ""

    @property
    def is_hard(self) -> bool:
        return self.grade in (SignalGrade.DETERMINISTIC, SignalGrade.QUASI)


# ---- ContextBoard: 3개 슬롯을 '다른 타입'으로 분리 ----
class OntologyFact(BaseModel):
    """ground_truth — 결정론 lookup '박기'. 프롬프트 상단·누락0."""
    entity: str
    attr_label: str
    value: str


class RetrievedItem(BaseModel):
    """narrative — RAG/Wiki '찾기'. 하단·cap·trust_weight. ground_truth 승격 불가."""
    source: Literal["rag_chunk", "wiki_page", "arc_anchor", "bible", "cast_debut"]
    ref: str
    text: str
    trust_weight: float = 1.0


class AuthorDirective(BaseModel):
    """authority — 작가 지시. 상단 고정, 누적 전파."""
    directive_id: str
    text: str
    from_chapter: int


class ContextBoard(BaseModel):
    chapter: int
    ground_truth: list[OntologyFact] = Field(default_factory=list)
    narrative: list[RetrievedItem] = Field(default_factory=list)
    authority: list[AuthorDirective] = Field(default_factory=list)
    prev_chapter: str = ""            # 서사 흐름: 직전 회차 원문(연속성)
    story_so_far: str = ""            # 누적 줄거리 요약(narrative, '안 까먹기'의 요약 다리)
    voice_cards: str = ""             # 등장 인물별 말투 시그니처(보이스 분화 — 스타일 지침, 캐논 아님)


class SceneSpec(BaseModel):
    index: int
    goal: str
    key_events: list[str] = Field(default_factory=list)


class ChapterStatus(str, Enum):
    FINALIZED = "FINALIZED"
    ESCALATED = "ESCALATED"


class RoundTrace(BaseModel):
    round: int
    scene: Optional[int] = None
    n_violations: int
    n_hard: int
    kinds: list[str] = Field(default_factory=list)


class OntologyChange(BaseModel):
    """동적 온톨로지 업데이트 결과 1건(UI 표시·감사용)."""
    op: Literal["new_entity", "state_change", "contradiction", "relation"]
    entity: str
    detail: str
    applied: bool
    reason: str = ""


class ChapterRecord(BaseModel):
    chapter: int
    title: str = ""
    status: ChapterStatus
    text: str = ""
    summary: str = ""                 # 한 줄 요약(과거 회차의 압축 표현 — 누적 story_so_far 의 원거리 레이어)
    detail_synopsis: str = ""         # 상세 시놉시스(~1,500자: 사건 인과·감정 변화·물리 디테일·미결 — 근거리 레이어)
    scenes: int = 0
    n_retrieved: int = 0
    indexed_chunks: int = 0
    wiki_pages_touched: int = 0
    arc_id: Optional[str] = None      # spine 모드: 이 회차가 속한 아크/에피소드
    episode_id: Optional[str] = None
    # G4: 비트의 기능 차원을 회차 기록에 영속 — 다음 회차 설계가 '최근 훅 유형 이력'을 결정론 비교(반복 차단)
    chapter_function: str = ""
    hook_type: str = ""
    time_advance: str = ""
    place: str = ""
    drift_signals: list[str] = Field(default_factory=list)   # 결정론 드리프트 advisory
    reader_feedback: dict = Field(default_factory=dict)       # G2: 블라인드 독자 행동 예측(advisory — 작가 가시화, 비구속)
    gen_context: dict = Field(default_factory=dict)           # 디버그: 이 회차를 '어떤 정보로' 생성했는가(계획 비트 + 집필 입력 슬롯, 트림)
    recovery_hints: list[dict] = Field(default_factory=list)  # ESCALATED 시 작가용 자연어 진단+회복 레버(engine.recovery)
    initial_violations: list[Violation] = Field(default_factory=list)
    final_violations: list[Violation] = Field(default_factory=list)
    rounds: list[RoundTrace] = Field(default_factory=list)
    ontology_changes: list[OntologyChange] = Field(default_factory=list)
    usage_by_stage: dict = Field(default_factory=dict)   # 단계별 토큰(단위경제: 일관성 오버헤드율 계산 재료)

    @property
    def hard_remaining(self) -> list[Violation]:
        return [v for v in self.final_violations if v.is_hard]


# ---- RuleSpec: 룰을 '데이터'로(룰 추가 = row 추가). 닫힌 4종 술어 ----
PredicateKind = Literal["categorical_eq", "numeric_monotone", "timeline_state", "worldrule_flag"]


class RuleSpec(BaseModel):
    rule_id: str
    layer: str
    predicate_kind: PredicateKind
    grade: SignalGrade
    params: dict = Field(default_factory=dict)


# ---- 엔티티↔엔티티 자유 결합 엣지 (속성그래프) ----
# 설계 4축: (1) 자유 타입(rel_id 는 자유 라벨 — 카탈로그 FK 강제 폐기, 등록 안 된 타입도 동작),
#          (2) 시간(eff_from/eff_to 반열림 [eff_from, eff_to)),
#          (3) 관점(pov: None=객관/나레이터 참, entity=그 주체의 인식·믿음 — 거짓 가능. "잃어버림/안다/믿는다"가 엣지인 이유),
#          (4) 신뢰(trust_tier: narrative_inferred 자동승격 금지 — 노드 비대칭의 거울).
# state = 그 관계의 질적 현재 상태("잃어버림"/"어색"/"짝사랑"; 자유, rel-type 이 states 선언 시 게이팅). attrs = 자유 KV.
class RelationEdge(BaseModel):
    edge_id: str = ""                          # '{rel_id}:{src}->{dst}:{eff_from}' (서비스가 부여)
    rel_id: str                                # 자유 타입 라벨(카탈로그는 선택적 메타데이터)
    src_id: str
    dst_id: str
    role: str = ""                             # 'father'/'mother' 등 세분
    state: str = ""                            # 질적 현재 상태("잃어버림"/"어색") — 자유 or 선언 시 게이팅
    pov: Optional[str] = None                  # 관점 주체 id. None=객관(참). 설정 시 그 주체의 인식/믿음(거짓 가능)
    attrs: dict = Field(default_factory=dict)  # 자유 KV(부가 뉘앙스; 척도가 필요하면 여기 weight)
    eff_from: int = 1                           # narrative_order
    eff_to: Optional[int] = None                # None = 현재 유효
    reason: str = ""
    trust_tier: Literal["ground_truth", "narrative_inferred"] = "ground_truth"
    provenance: list[str] = Field(default_factory=list)   # relations.Provenance 값들


# ---- LLM Wiki ----
class TypedEdge(BaseModel):
    type: Literal["payoff_of", "contradicts", "supersedes", "extends"]
    target_page_id: str
    source_narrative_order: int
    source_span: str = ""


class WikiLifecycle(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    ARCHIVED = "archived"


class WikiPage(BaseModel):
    page_id: str
    page_type: Literal["character", "faction", "place", "plot_thread", "timeline"]
    body: str = ""
    typed_edges: list[TypedEdge] = Field(default_factory=list)
    lifecycle: WikiLifecycle = WikiLifecycle.DRAFT
    trust_tier: Literal["wiki_synthesized", "unreviewed_machine"] = "wiki_synthesized"
    as_of_narrative_order: int = 0
    provenance: list[str] = Field(default_factory=list)
    payoff_deadline: Optional[int] = None
