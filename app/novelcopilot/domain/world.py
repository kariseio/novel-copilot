# -*- coding: utf-8 -*-
"""WorldConfig — 세계관을 '데이터'로 외부화(하드코딩 배제의 핵심).

기존 PoC 의 scenario.py(붉은 눈 세계 하드코딩) + rules.CATEGORICAL_VOCAB/ATTR_LABEL(하드코딩)을
전부 이 한 스키마로 대체한다. 룰 레지스트리/통제어휘/문체는 모두 여기서 파생된다(factory).
worldgen 이 시드로부터 이 객체를 생성한다.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

from .relations import RelationSpec
from .types import RelationEdge
from .narrative import NarrativeSpine


class EntityTypeSpec(BaseModel):
    """엔티티 타입 카탈로그(데이터주도). 시각화 색/모양을 데이터로 보유(하드코딩 금지).
    factory 가 vocabulary 처럼 이로부터 시각화 스타일을 파생한다. 빈 카탈로그면 BUILTIN 시드(하위호환)."""
    key: str
    label: str
    category: Literal["actor", "group", "place", "object", "abstract", "event"] = "actor"
    color: str = "#6aa9ff"
    shape: Literal["ellipse", "round-rectangle", "diamond", "hexagon", "triangle", "star"] = "ellipse"
    icon: str = ""
    is_builtin: bool = False


BUILTIN_ENTITY_TYPES: list[EntityTypeSpec] = [
    EntityTypeSpec(key="character", label="인물", category="actor", color="#5b8def", shape="ellipse", is_builtin=True),
    EntityTypeSpec(key="faction", label="세력", category="group", color="#b07cff", shape="round-rectangle", is_builtin=True),
    EntityTypeSpec(key="organization", label="조직", category="group", color="#b07cff", shape="round-rectangle", is_builtin=True),
    EntityTypeSpec(key="place", label="장소", category="place", color="#39d98a", shape="diamond", is_builtin=True),
    EntityTypeSpec(key="location", label="지역", category="place", color="#39d98a", shape="diamond", is_builtin=True),
    EntityTypeSpec(key="item", label="아이템", category="object", color="#ffb454", shape="triangle", is_builtin=True),
    EntityTypeSpec(key="artifact", label="아티팩트", category="object", color="#ff9f43", shape="triangle", is_builtin=True),
    EntityTypeSpec(key="event", label="사건", category="event", color="#ff6b6b", shape="hexagon", is_builtin=True),
    EntityTypeSpec(key="worldrule", label="세계규칙", category="abstract", color="#8b93a7", shape="star", is_builtin=True),
]


class AttributeSpec(BaseModel):
    """추적 속성 정의. 룰/통제어휘/추출 스키마가 전부 여기서 파생.
    kind="state"(생애주기) 로 사망/각성/정체발각/결혼 등 '상태 전이'를 데이터로 표현 — 사망은 그 한 인스턴스(하드코딩 제거)."""
    key: str                                  # 예: eye_color
    label: str                                # 예: 눈 색
    kind: Literal["categorical", "numeric", "status", "state"]   # "state"=생애주기; "status"는 별칭(하위호환)
    vocab: list[str] = Field(default_factory=list)        # categorical 통제어휘('기타'는 엔진이 추가)
    states: list[str] = Field(default_factory=list)       # state/status: 생애주기 값(예:[alive,dead],[미각성,각성],[비밀,발각])
    irreversible: list[str] = Field(default_factory=list) # 한번 들어가면 못 나오는 상태(이탈=모순; allow_state_reversal 세계 제외)
    terminal: list[str] = Field(default_factory=list)     # '제거' 상태(등장-불가 + 새 객관관계 차단; 예:[dead])
    monotonic: Optional[Literal["non_decreasing", "non_increasing"]] = None  # numeric
    mutable: bool = False                     # 동적 업데이트가 변경을 progress로 허용할지(False=모순→escalation)
    extract_hint: str = ""                    # 추출기 가이드(선택)


# 기본 생애주기 — 선언 없으면 이 status(생사)가 자동 적용(사망=비가역·terminal 의 기본 인스턴스, 하위호환).
DEFAULT_STATUS_ATTR = AttributeSpec(key="status", label="생사", kind="state",
                                    states=["alive", "dead"], irreversible=["dead"],
                                    terminal=["dead"], mutable=True)


class EntitySpec(BaseModel):
    id: str
    name: str
    etype: str = "character"                  # EntityTypeSpec.key 참조(닫힌 Literal→데이터주도 완화). factory 가 카탈로그 멤버십 검증.
    aliases: list[str] = Field(default_factory=list)
    attrs: dict[str, object] = Field(default_factory=dict)   # key→value (AttributeSpec.key)
    base_status: str = "alive"
    voice: str = ""                           # 말투 시그니처(어미·습관구·금지어 — 보이스 분화, draft 주입)
    provisional: bool = False                 # 동적 업데이트로 자동 커밋된 신규 인물 표식


class WorldRuleSpec(BaseModel):
    rule_id: str
    text: str                                 # 세계 규칙 산문(프롬프트 노출)
    flag: str = ""                            # 추출기 불리언 플래그명 — 누락 시 rule_id 로 자동 유도(LLM 산출 견고성)
    keywords: list[str] = Field(default_factory=list)   # 규칙 활성 판정 + 추출 가이드
    extract_hint: str = ""

    @model_validator(mode="after")
    def _default_flag(self):
        if not self.flag:
            self.flag = self.rule_id
        return self


class TimelineEntry(BaseModel):
    """시드 상태 변화(예: 특정 화부터 사망).
    trust_tier: 노드 상태에도 엣지(RelationEdge)와 동일한 비대칭을 적용 —
    ground_truth(시드/작가 확정)만 binding(canon_facts 주입·사망 하드게이트), narrative_inferred(기계추출)는 비구속·추적용."""
    entity_id: str
    attr: str
    value: object
    eff_from: int
    reason: str = ""
    trust_tier: Literal["ground_truth", "narrative_inferred"] = "ground_truth"


class Beat(BaseModel):
    chapter: int
    title: str = ""
    summary: str = ""
    key_events: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    arc_id: Optional[str] = None              # 이 회차가 속한 아크/에피소드(spine 모드)
    episode_id: Optional[str] = None
    is_episode_finale: bool = False           # 에피소드 절정/마무리 회차 표식


class WikiSeed(BaseModel):
    page_id: str
    page_type: Literal["character", "faction", "place", "plot_thread", "timeline"] = "plot_thread"
    body: str = ""
    payoff_deadline: Optional[int] = None
    as_of_narrative_order: int = 1


class StyleSpec(BaseModel):
    """문체 사양도 데이터(WEBNOVEL_STYLE 하드코딩 제거). 장르별 교체 가능."""
    target_chars_per_chapter: int = 5000
    scenes_per_chapter: int = 3
    rules: list[str] = Field(default_factory=lambda: list(DEFAULT_STYLE_RULES))
    # 장르 중립 기본 persona — worldgen 이 장르를 끼워 넣는다(현대 판타지 하드코딩 제거)
    system_persona: str = "너는 인기 한국 웹소설 작가다. 모바일 독자가 한 호흡에 몰입하는 회차를 쓴다."
    ending_hook: Literal["cliffhanger", "soft", "none"] = "cliffhanger"   # 회차 끝맺음 정책(작가 제어, ③).
    # cliffhanger=절단신공(연재 웹소설 기본) / soft=과도한 위기 없이 자연스러운 여운 / none=지시 없음(자유)


DEFAULT_STYLE_RULES = [
    "분량: 이 장면을 약 1,800자 내외로 쓰고 반드시 '문장을 자연스럽게 맺으며' 완결하라 — 끊긴 문장으로 끝내기 금지(회차 총 5,000~5,500자).",
    "조판: 대사는 한 줄에 하나. 지문은 2~4문장을 한 문단으로 묶는다. 문장을 한두 어절씩 토막내는 행갈이 금지(시처럼 끊지 마라).",
    "대사·지문 리듬: 대사를 충분히 활용해 장면에 속도를 주되, 비율을 기계적으로 맞추지 말고 장면 목적에 맞춘다.",
    "단문·속도감: 짧고 명료한 문장 위주(단문 중심, 가끔 중문). 만연체·장황한 시공간 묘사 금지.",
    "수치·연속성: 자원(배터리·산소·식량)·시간·거리 수치는 회차 안에서 정합되게 — 보급 없이 늘거나 사건 없이 급감 금지. 같은 정보를 되묻는 반복 대사 금지.",
    "금지: 접속사 남발, 불필요한 주어 반복, 번역체, 멋부린 어려운 단어, 같은 어미 말버릇의 과도한 반복.",
    "인물 속마음(내면)을 짧게 섞어 몰입감을 준다.",
    "시점·시제 일관: 작품 전체 동일 시점(기본 3인칭)·과거형 서술 고정 — 인칭 전환·현재형 누출 금지.",
    "말버릇 제한: 인물 시그니처(욕설·감탄사·어미 틱)는 회차당 3회 이내 — 도배는 캐릭터를 자기 패러디로 만든다.",
    "호칭: 설정 명부의 표기(괄호 라벨 포함)를 그대로 노출하지 말고 자연스러운 작중 호칭으로.",
    "도입 금지 패턴: 회차를 자원·수치 낭독(배터리 %·식량 개수 나열)으로 시작하지 마라 — 상태는 장면 사건에 녹여라.",
]


class WorldConfig(BaseModel):
    title: str
    genre: str = ""
    tone: str = ""
    premise: str = ""
    synopsis: str = ""
    attributes: list[AttributeSpec] = Field(default_factory=list)
    entity_types: list[EntityTypeSpec] = Field(default_factory=list)   # 빈→BUILTIN 시드(하위호환)
    entities: list[EntitySpec] = Field(default_factory=list)
    relations: list[RelationSpec] = Field(default_factory=list)        # 작품별 추가 관계(REL_CATALOG 위에 머지)
    seed_edges: list[RelationEdge] = Field(default_factory=list)       # 시드 관계 엣지
    world_rules: list[WorldRuleSpec] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    wiki_seeds: list[WikiSeed] = Field(default_factory=list)
    spine: Optional[NarrativeSpine] = None    # 엔딩-주도 아크/에피소드 구조(None=평면 beats 모드)
    allow_state_reversal: bool = False        # True=회귀/부활/리젠 허용(비가역 상태 이탈을 모순으로 막지 않음)
    plant_reminder: Literal["off", "gentle", "active"] = "gentle"   # 미회수 복선 리마인더 강도(작가 제어, ③).
    # off=주입 안 함 / gentle=참고 정보만(회수 비강제 — 슬로우번 존중, 기본) / active=finale 에서 회수 독려
    style: StyleSpec = Field(default_factory=StyleSpec)
