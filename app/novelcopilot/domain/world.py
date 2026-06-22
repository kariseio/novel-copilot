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
    voice: str = ""                           # (레거시) 말투 지정 — 신규 작품은 비움: 말투는 설정+실측 대사 인용에서 창발
    profile: str = ""                         # 인물 설계서(배경·성격·욕망·관계 — 캐스트 플랜 레이어 산출물)
    debut_episode: str = ""                   # 등장 계획(에피소드 id — 아크 설계가 결정, 비트가 데뷔를 집행)
    introduced: bool = False                  # 본문 첫 등장 완료 여부(FINALIZED 시 코드가 마킹 — 데뷔 앵커 의무의 근거)
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
    # G4: 회차의 '기능' 차원(설계 단계에 명시 — 사건 요약만으로는 보상/페이싱이 안 보임). 자유 라벨, 강제 아님.
    chapter_function: str = ""                # payoff(지불)/setup(약속)/escalation(격상)/relation(관계)/respite(완급) …
    hook_type: str = ""                       # 회차말 훅 유형: question/action/reveal/emotion/new_threat/decision …
    time_advance: str = ""                    # 직전 화 대비 시간 경과(예: '몇 분'/'다음날'/'사흘 후'/'없음')
    place: str = ""                           # 주요 장소(장소 체류 단조 감지 재료)


class GenreContract(BaseModel):
    """장르 계약 (G5) — '이 장르/작품의 정체성'을 모든 레이어가 공유하는 서술 컨텍스트(강제 아님, 정보 제공).
    로판이 SF 용어로 표류하거나 무협이 '십 년 잠입' 전제를 1화에 태우던 결함의 소스 차단 —
    설계·집필·독자평가가 같은 '쾌감 엔진·전제 자산'을 보게 한다. narrative(서사 의도), 캐논 아님."""
    pleasure_engine: str = ""          # 이 장르 독자가 결제하는 핵심 쾌감(예: 회귀헌터=정보우위→공개 격상)
    reader_expectations: list[str] = Field(default_factory=list)   # 독자가 기대하는 것 톱 N
    vocabulary_tone: str = ""          # 장르 어휘·톤 가이드(로판≠SF 용어)
    premise_asset: str = ""            # 이 작품의 핵심 동력 전제(장기 자산 — 어떤 역할인지 서술)


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
    # 장르 중립 기본 persona — worldgen 이 장르를 끼워 넣는다(특정 장르 하드코딩 금지)
    system_persona: str = (
        "너는 카카오페이지·네이버시리즈·문피아 유료 연재와 실시간 랭킹을 찍어 본 한국 웹소설 프로 작가다. "
        "모바일 세로 화면을 엄지로 빠르게 넘기는 독자가 '다음 화'를 누르도록 빠르고 타격감 있는 사이다 문장을 쓴다. "
        "매끄럽게 정리된 '잘 쓴 글'이나 교과서적으로 안전한 전개가 아니라, "
        "거칠고 리듬이 살아 있으며 예상을 한 번씩 비트는, 'AI가 쓴 티' 안 나는 진짜 연재 본문을 쓴다.")
    ending_hook: Literal["cliffhanger", "soft", "none"] = "cliffhanger"   # 회차 끝맺음 정책(작가 제어, ③).
    # cliffhanger=절단신공(연재 웹소설 기본) / soft=과도한 위기 없이 자연스러운 여운 / none=지시 없음(자유)
    author_style: str = ""   # Layer 2 작가 문체 오버레이(빈 값=기본 8규칙만, 무회귀). 설정 시 기본 규칙의
    #   '미학 축'(문장 리듬·감정 처리·직유 밀도·서술 거리·어휘 격)을 작가 지정으로 덮어씀 — 단 하드 바닥
    #   (분량·모바일 가독·시점/시제 일관·번역투/이중피동/기계적 병렬 금지)은 유지. render_style 이 precedence 와 함께 주입.


# 양성 원칙 헌법(항목 수 고정) — '두더지잡기' 회피 설계.
#  · 검출 가능한 축(틱·시제·조판 토막·훅 재탕)은 프롬프트가 아니라 quality_gates 결정론 백스톱이 담당하므로
#    여기서 빼고(harness 가 word_tics·tense_leak·fragmentation_score 로 자동 검출·국소 교정),
#    '게이트가 못 잡는 게슈탈트(보여주기·대사 결·어휘 질감·리듬·절단)'만 양성문으로 점화한다.
#  · 새 'AI 티'는 규칙을 +1 하지 말고(부정 명령은 패턴을 소환·증식), 기존 원칙 강화 또는 게이트 신호로 흡수.
#  · 고정 예문을 넣지 않는다(예문은 모델이 골격째 베끼는 자기표절의 진원 → few-shot 앵커는 별도 회전 슬롯에서만).
DEFAULT_STYLE_RULES = [
    "조판은 모바일 기준으로. 마침표·물음표·느낌표마다, 또는 행동·심리 한 덩어리마다 줄을 바꿔 1~3문장짜리 짧은 덩어리로 쪼개라. 대사는 한 줄에 하나. 임팩트·반전·한 방 대사 앞뒤는 빈 줄로 호흡을 끊어라. 2~4문장을 한 문단으로 묶는 종이책식 '벽'을 기본값으로 삼지 마라.",
    "감정·상태·판단을 이름표로 설명하지 말고 행동·생리 반응으로 보여줘라. '분노했다/두려웠다' 같은 직접 라벨도, '~을 깨달았다/느꼈다/직감했다'나 '거리 계산을 틀렸다' 같은 자각 요약도 쓰지 마라. 마른침, 굳는 손끝, 빗나간 손, 끊긴 말, 식은 시선으로 드러내고 해석은 독자에게 맡겨라.",
    "대사로 장면을 굴려라. 정보·갈등·긴장을 지문 설명이 아니라 인물이 주고받는 짧은 대사(티키타카)로 흘리고, 단독 액션 장면에도 짧은 대사·기합을 부딪쳐라. 화자는 '말했다' 태그가 아니라 말투와 직전 행동(비트)으로 드러내라. 인물마다 말투·1인칭·말버릇·계급감을 실제 대사로 분화하되, 서로 이미 아는 설정을 친절히 풀어 설명하는 정보 대사는 금지.",
    "어휘는 구어·직설·구체로. 장르 관용어를 적극 쓰고, 결정적 순간(각성·일격·반전·시스템 개입)일수록 '무언가/뭔가/모종의' 같은 막연한 공백어 대신 구체적인 기관·소리·색·온도로 못박아라. 추상적 은유나 번역투 대신 신체 반응으로. 의성어·의태어는 결정적 순간에만, 효과음을 먼저 던지고 짧게 해설하라.",
    "사이다·전투·각성은 '~인 듯했다/~처럼 보였다' 같은 헤지 없이 확정 서술로 타격감을 줘라. 매끄럽게 '잘 쓴 글'이 아니라 거칠고 리듬이 살아 있게 — 문장 길이를 의도적으로 들쭉날쭉 변주하되(단문을 잇다가 한 번 길게 풀고 다시 끊기), 변주 자체가 또 다른 기계적 패턴이 되지 않게 가끔 평범한 호흡도 섞어라. 'A가 아니라 B였다'식 부정-대조 강조나 삼단 병렬은 극히 드물게.",
    "회차·장면 끝을 교훈·요약('그렇게 ~끝났다', '한 뼘 성장했다')으로 매끄럽게 봉합하지 마라. 정점의 미해결 상태를 구체적 이미지 한 줄로 남겨 다음 화를 궁금하게 끊어라. 전개도 교과서 정석으로만 안전하게 풀지 말고, 한 장면에 최소 한 번은 예상을 비트는 변수(헛디딤·오판·대가·돌발)를 넣어라.",
    "호칭은 설정 명부 표기(괄호 라벨)를 그대로 노출하지 말고 자연스러운 작중 호칭으로. 회차를 자원·수치·설정 낭독으로 시작하지 말고 상태는 사건에 녹여라. 수치·시간·거리·연속성은 회차 안에서 정합되게.",
    "이 장면을 약 1,800자 내외로, 문장을 자연스럽게 맺으며 완결하라 — 끊긴 문장으로 끝내기 금지(회차 총 5,000~5,500자). 시점·시제는 작품 전체 동일(기본 3인칭·과거형, 액션 정점의 현재형만 양념).",
]


class WorldConfig(BaseModel):
    title: str
    genre: str = ""
    tone: str = ""
    premise: str = ""
    synopsis: str = ""
    obsession_vector: str = ""                 # 풍부함 헌법: worldgen 이 편중 파생한 '하나의 집착'(Egri 전제). 설계·집필 컨텍스트로 재사용
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
    genre_contract: Optional[GenreContract] = None   # G5: 장르 정체성 공유 컨텍스트(None=미생성, 하위호환)
    allow_state_reversal: bool = False        # True=회귀/부활/리젠 허용(비가역 상태 이탈을 모순으로 막지 않음)
    plant_reminder: Literal["off", "gentle", "active"] = "off"   # 미회수 복선 리마인더 강도(작가 제어, ③).
    # 기본 off=시스템이 떡밥을 생성에 주입 안 함(비강제 — 떡밥 가시화는 G1 원장 텔레메트리가 담당, 작가가 빨간펜으로 조향).
    # 작가가 명시 opt-in 시: gentle=참고 정보만(회수 비강제) / active=finale 에서 회수 독려
    style: StyleSpec = Field(default_factory=StyleSpec)
