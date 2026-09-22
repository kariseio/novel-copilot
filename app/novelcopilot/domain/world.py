# -*- coding: utf-8 -*-
"""WorldConfig — 세계관을 '데이터'로 외부화(하드코딩 배제의 핵심).

기존 PoC 의 scenario.py(붉은 눈 세계 하드코딩) + rules.CATEGORICAL_VOCAB/ATTR_LABEL(하드코딩)을
전부 이 한 스키마로 대체한다. 룰 레지스트리/통제어휘/문체는 모두 여기서 파생된다(factory).
worldgen 이 시드로부터 이 객체를 생성한다.
"""
from __future__ import annotations
import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

from .relations import RelationSpec
from .types import RelationEdge, TimeDelta
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
    ordered: bool = False                     # categorical 가변축이 vocab 순서대로 '한 방향 진행'(관계단계·호감 등). True면
    #   vocab 인덱스가 *낮아지는* 후진 전이(회상·오추출)는 binding 캐논으로 커밋 안 하고 비구속(narrative_inferred)으로 — 되감김 방지
    extract_hint: str = ""                    # 추출기 가이드(선택)
    # CX-2 노출 등급: "public"=생성 입력(확정 설정·조회·cast) 주입 가능 / "internal"=진행 추적 전용(검증·심사·UI·
    #   플래너만 — 집필 모델에게 절대 안 보인다). 반전 관리 지표(정체자각 등)·계측 축의 스포 유출 소스 차단.
    #   기본 public = 미설정 작품 프롬프트 바이트 동일(하위호환).
    exposure: Literal["public", "internal"] = "public"
    # XR-5 동적 감지 커밋 티어 선언(작품 도출 + 작가 오버라이드 — 속성명 사전·정규식·장르 분기 0).
    #   "binding"    = 현행(장면에서 외적으로 관찰 가능한 사실 — 소지·위치·생사·소속 류): 기존 티어 로직 그대로.
    #   "non_binding"= 동적 감지는 narrative_inferred 로만 착지(내면·인지·자각처럼 본문 서술만으로 사실 확정이
    #                  어려운 축) + 작가 승인(set_entity_state)이 ground_truth 로 승격.
    #   ""(기본)     = 미선언(구 JSON) — 현행 동작 유지(하위호환·프롬프트 바이트 동일). 소급 강등 없음: 미선언
    #                  잔량은 machine_binding_report(ontology_ops)로 가시화하고 작가가 속성별로 선언해 닫는다.
    #   강등 단방향 — "binding" 선언은 기존 안전장치(비가역·terminal 전이의 비구속 착지, 순서형 후진 강등,
    #   동시점 escalation)를 해제하지 못한다. 현행보다 구속이 세지는 경로는 없다.
    auto_commit: Literal["", "binding", "non_binding"] = ""


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
    # VB-1 상태 연동 보이스 — 정적 보이스 카드가 30화 내내 같은 '반응 슬롯'을 지정해 틱을 낳던 문제의 소스 차단.
    #   key=StyleSpec.narrator_voice_stage_attr 가 가리키는 속성의 값(예 truth_awareness: 외면/흠칫/…), value=그 단계의 카드.
    #   harness 가 회차별 state_as_of 로 조회해 서술자 프레임에 얹는다. 비면(기본) 조회 자체가 없어 voice 그대로 = 바이트 동일.
    #   카드는 '반응'(농담으로 덮어라)이 아니라 '이해관계'(무엇을 잃기 싫은가)로 적는다 — 반응 지정 = 슬롯 지정 = 틱.
    voice_stages: dict[str, str] = Field(default_factory=dict)
    profile: str = ""                         # 인물 설계서(공개·현재 정체: 배경·성격·현재 욕망·현재 관계 — 독자가 처음 만나 알 수 있는 것). 설계+데뷔에 full 주입.
    # PF-1: 아크 노트 — 앞으로의 관계 방향·궤적·반전 인접(내부 전용). 설계 계층(arc_planner)만 읽고 회차 생성엔 절대 미주입
    #   (FS-1·quiet_foreshadow 동형 — 생성기가 최종 반전을 보면 플랜에 없는 떡밥을 자유 발명하는 실측 차단). 소비 '계층'을 이름이
    #   지목해 오배선을 막는다(profile_internal='profile 의 나머지'로 읽혀 잡동사니 서랍 재발하던 결함 회피). 기본 ""=미설정 시
    #   주입 0(프롬프트 바이트 동일·하위호환 — 구 JSON 그대로 로드).
    arc_note: str = ""
    debut_episode: str = ""                   # 등장 계획(에피소드 id — 아크 설계가 결정, 비트가 데뷔를 집행)
    introduced: bool = False                  # 본문 첫 등장 완료 여부(FINALIZED 시 코드가 마킹 — 데뷔 앵커 의무의 근거)
    provisional: bool = False                 # 동적 업데이트로 자동 커밋된 신규 인물 표식
    cardinality: dict[str, int] = Field(default_factory=dict)   # CN-4: 이 인물의 관계 개수 상한(rel_id→max). 예 외동={"sibling_of":0}·유일생존자. 결정론 advisory(비면 무제한)


class WorldRuleSpec(BaseModel):
    rule_id: str
    text: str                                 # 세계 규칙 산문(프롬프트 노출)
    flag: str = ""                            # 추출기 불리언 플래그명 — 누락 시 rule_id 로 자동 유도(LLM 산출 견고성)
    keywords: list[str] = Field(default_factory=list)   # 규칙 활성 판정 + 추출 가이드
    extract_hint: str = ""
    # CN-5: 열거/순서 규칙의 '키→값' flat-lookup(예: 관문 순서 {"첫째":"머리","둘째":"가슴",...}). 있으면
    #  (1) 고신뢰 [확정 설정] 주입에 표를 명시(집필이 정확한 대응 보유=예방) (2) 일반 table_lookup 술어가 advisory 대조.
    #  세계별 코드 0 — 순수 데이터. 비면 {}(일반 세계규칙과 동일 동작).
    table: dict[str, str] = Field(default_factory=dict)
    # CX-2 노출 등급: internal 규칙은 생성 입력(ontology.rules → [확정 설정])에서 빠지고 심사·아크 계획에는 남는다
    #   — 반전 서술 규칙(스포 평문)의 집필 프롬프트 유출 차단. 기본 public(하위호환·바이트 동일).
    exposure: Literal["public", "internal"] = "public"

    @model_validator(mode="after")
    def _default_flag(self):
        if not self.flag:
            self.flag = self.rule_id
        return self


class TimelineEntry(BaseModel):
    """시드 상태 변화(예: 특정 화부터 사망).

    trust_tier: 노드 상태에도 엣지(RelationEdge)와 동일한 비대칭을 적용 — ground_truth 만 binding
    (canon_facts [확정 설정] 주입 · binding_state_as_of 사망 하드게이트·게이트 캐논), narrative_inferred 는
    비구속·추적용(state_as_of 는 전 tier 를 보므로 추출 컨텍스트·보이스 단계·UI 표시에는 그대로 남는다).

    ground_truth 로 착지하는 경로(XR-5 이후 현행 — 독스트링-코드 정합):
      · 시드/작가 확정(set_entity_state · provenance ["author"])
      · 회차 동적 감지 중 **가역 전이**(비가역·terminal 전이는 작가 확정 전 narrative_inferred)
    그 위에 속성 선언 티어(AttributeSpec.auto_commit)가 얹힌다 — 미선언·"binding" 은 위 규칙 그대로,
    "non_binding" 선언 축은 가역 전이도 narrative_inferred 로만 착지하고 작가 승인이 승격한다(강등 단방향)."""
    entity_id: str
    attr: str
    value: object
    eff_from: int
    reason: str = ""
    trust_tier: Literal["ground_truth", "narrative_inferred"] = "ground_truth"
    # ON-2 U7: 승인 표기 — RelationEdge.provenance 와 대칭(additive·구 JSON 로드 불변). 기계 커밋은
    #   ["machine"], set_entity_state(작가 승인 경유)만 ["author"]. 강등이 아니라 가시화 — 미승인 기계
    #   캐논 계수를 재검증(U6)이 정확히 세게 하는 마감.
    provenance: list[str] = Field(default_factory=lambda: ["machine"])


class TimeAnchor(BaseModel):
    """시계 파생 산술의 결정론 앵커(B-33). 세계가 '시간 기준점'(계약 만기·인물 나이 류)을 선언하면
    story_clock 이 현재 클록 위치 기준 잔여/현재나이를 결정론 계산해 [확정 설정] 고신뢰 블록에 주입한다
    (LLM 산수 0 — CN-1 story_time·CN-5 대응표와 같은 패턴). 미선언 세계 = 무주입(바이트 동일 하위호환).
    counter류(목격 횟수·전학 횟수)는 결정론 추적 불가라 대상이 아니다(정직 범위는 T5 등록부가 담당).

    kind=deadline: 이야기 시작 기준 amount·unit 뒤에 도래하는 기한(예 '삼 년 계약 만기'→amount=3,unit=year).
    kind=age: 이야기 시작 시점 인물의 나이(amount=시작 나이 '년', unit 무시). 경과 연수(내림)를 더한 현재 나이를 주입."""
    anchor_id: str = ""
    label: str = ""                            # 작가·모델 노출 라벨(예 '계약 만기', '세라의 나이')
    kind: Literal["deadline", "age"] = "deadline"
    amount: float = 0.0                        # deadline: 시작 기준 경과량 / age: 시작 나이(년)
    unit: str = "year"                         # deadline 전용(story_clock.UNIT_MINUTES 키); age 는 무시
    entity_id: str = ""                        # 선택 — 어느 인물의 앵커인지(감사·UI용, 계산엔 불필요)


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
    time_advance: str = ""                    # 직전 화 대비 시간 경과 라벨(자유텍스트: '몇 분'/'다음날'/'사흘 후'/'없음')
    time_delta: Optional[TimeDelta] = None    # CN-1: 같은 경과를 구조화(amount,unit,mode) — story_clock 결정론 누적용(없으면 null degrade)
    place: str = ""                           # 주요 장소(장소 체류 단조 감지 재료)
    # DP-2: 이 회차에서 주인공이 '스스로 여는 수' 한 줄(결정·선수·역습·판 설계) — 인물의 욕망·성격(cast_context)에서 도출.
    # G4 계보 자기 기술 필드(강제 아님·프로즈 게이트 없음). 빈 값=미기술(콜드스타트·구 레코드 하위호환).
    protagonist_move: str = ""
    # DP-22: 이 회차의 '마지막 순간'이 어느 장치로 물리적으로 닫히는지 자기 라벨(G4 계보 — 강제 아님·프로즈 게이트 없음).
    #   고정 분류(장르 무관 craft 축): dialogue(대사)/action(행동)/sensory(감각·이미지)/object(사물·화면·문서)/
    #   arrival(인물 등장·신호 도착)/interior(화자 내면 한 줄). hook_type(끊는 '방식')과 직교 — 이건 닫는 '장치'다.
    #   비트 설계가 최근 2화 사용 장치를 뺀 축소 선택지에서 골라 순환(B-37 화이트리스트 계보·앵커링 0). 결측=미기술(구 레코드 하위호환).
    closing_device: str = ""
    # SX-1: 이 회차의 '중심 장면 안무'가 무엇인지 자기 라벨(G4/DP-22 계보 — 강제 아님·프로즈 게이트 없음). 분류형(자유 텍스트 아님):
    #   잠입/대면/시험/추적/의뢰/제작/이동/사교/협상/발견/은폐/대결/구출/협력/일상. closing_device(닫는 장치)·hook_type(끊는
    #   방식)과 직교 — 이건 회차 중심 장면이 '무엇을 하는가'다(접촉→복원→은폐 3연속·심부름 트릭 재사용 같은 안무 반복 소스 차단).
    #   비트 설계가 최근 2화 사용 형태를 뺀 축소 선택지에서 골라 순환(B-37 화이트리스트 계보·앵커링 0). 결측=미기술(구 레코드 하위호환).
    scene_form: str = ""
    # SX-3: 이 회차에서 독자가 *처음* 알게 되는 세계 사실 0~2개(계획 재료 — 강제 아님·발명 강제 없음). 각 항목은 설명 문단이
    #   아니라 '장면 사건으로 드러나는'(인물이 부딪히거나 목격하는) 세계 사실이어야 한다(설명 덤프 차단 — protagonist_move·DP-2
    #   중립화 교훈과 동급 신중). 밝힐 사실이 없으면 빈 배열(정직 — 발명 금지). 결측=[](구 레코드 하위호환).
    world_reveal: list[str] = Field(default_factory=list)


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
    # CX-7 SSOT 단일화: 기본 빈 리스트 — 렌더(prompts.render_style)가 빈 값이면 코드 상수(DEFAULT_STYLE_RULES)로
    #   폴백한다. 종전 default_factory=DEFAULT_STYLE_RULES 는 생성 시점 스냅샷이 작품에 굳어 코드 개정이 기존
    #   작품에 안 닿는 저장 이중화(PM 실측: 작품 1,597자=코드와 바이트 동일 복사본). 작품에 값이 있으면 그 값
    #   우선(기존 작품 무회귀) · 신규 작품은 빈 값 저장 → 코드 개정 즉시 반영.
    rules: list[str] = Field(default_factory=list)
    # 장르 중립 기본 persona — worldgen 이 장르를 끼워 넣는다(특정 장르 하드코딩 금지).
    # ST-2: 대사·행동 주도 + 모바일 문단 리듬 표적으로 재작성(긍정 전용 — 틱 이름 호명·부정-대조 제거).
    # 캐논 계약 문구('확정 설정 절대 위반 금지.')는 harness 가 이 persona 뒤에 붙이며 이 필드 밖 — 불변.
    # N-1(2026-08-20): 말미 '예상을 비트는·손맛'(문장 반전·펀치라인 반사 = 자기해설 소스)을 '장면 자기추동'
    #   긍정 대체 + 앞부분을 저장본 개선(타격감·짧게 끊어 제거)에 동기화. 1화 정독 N-1 진단·프롬프트 감사 통과.
    system_persona: str = (
        "너는 카카오페이지·네이버시리즈·문피아 유료 연재로 써 온 한국 웹소설 프로 작가다. "
        "모바일 세로 화면으로 읽는 독자를 위해, 인물의 대사와 행동으로 장면을 끌고 가는 "
        "본문을 쓴다. 문장은 자연스러운 현대 한국어 산문으로 완결해 잇고, 문단은 장면의 호흡에 맞춰 변주한다. "
        "정보와 감정은 인물이 하는 말·몸짓·선택으로 드러낸다. "
        "장면이 제 힘으로 다음으로 굴러가는 진짜 연재 본문을 쓴다.")
    # VP-1: 발행 경계 조판(reflow)의 지문 문단 최대 문장 수. 기본 2 = 종전 강제 분할과 바이트 동일(하위호환 —
    #   구 JSON 은 필드 부재로 이 기본값 로드). 고정 2-stride 가 '2문장 문단 벽'의 결정론 소스였음이 실측돼
    #   (적대 리뷰 2026-08-13) 작품 데이터 노브로 승격 — 값을 올리면 강제가 줄어드는 완화 방향(무강제 정합).
    paragraph_max_sents: int = 2
    # VX-1(2026-08-17): 생성 프롬프트를 Claude XML 태그 구조로 렌더하고, 대사를 <대사 화자="…"> 로 감싸
    #   출력하게 하는 스위치. 기본 False = 프롬프트 바이트·출력 형식 전부 종전과 동일(하위호환 — 구 JSON 은
    #   필드 부재로 False 로드). True 인 작품만 XML 섹션 태그 + 대사 태그 경로를 탄다(생성 직후 결정론 디태거가
    #   태그를 정규 대사로 낮춰 저장 본문엔 태그가 남지 않는다 — 하류 처리·리더 무접촉).
    structured_prompt: bool = False
    ending_hook: Literal["cliffhanger", "soft", "none"] = "cliffhanger"   # 회차 끝맺음 정책(작가 제어, ③).
    # cliffhanger=절단신공(연재 웹소설 기본) / soft=과도한 위기 없이 자연스러운 여운 / none=지시 없음(자유)
    author_style: str = ""   # Layer 2 작가 문체 오버레이(빈 값=기본 8규칙만, 무회귀). 설정 시 기본 규칙의
    #   '미학 축'(문장 리듬·감정 처리·직유 밀도·서술 거리·어휘 격)을 작가 지정으로 덮어씀 — 단 하드 바닥
    #   (분량·모바일 가독·시점/시제 일관·번역투/이중피동/기계적 병렬 금지)은 유지. render_style 이 precedence 와 함께 주입.
    # DP-8: 서술 시점(작가 제어). 3인칭은 하드코딩이 아니라 소프트 디폴트였고(규칙8 문장)+mode-collapse 로 굳었을 뿐 —
    #   시점을 데이터로 승격한다. third_limited(기본)=밀착 3인칭(기본 8규칙이 이미 담는 결) / third_omniscient=3인칭 전지 /
    #   first=1인칭 주인공. 기본값 third_limited 는 render_style 이 아무것도 덧붙이지 않아 기존 프롬프트와 바이트 동일(무회귀);
    #   구 JSON(필드 부재)은 이 기본값으로 로드된다(하위호환). worldgen 이 발명하지 못하게 _strip_llm_style 이 폐기 —
    #   1인칭은 '시드 명시'로만 지정(발명 금지). 도파민물 재개 시 시드에 first 권고(design-dp-repair.md §DP-8).
    pov: Literal["third_limited", "third_omniscient", "first"] = "third_limited"
    # DP-17: 화자 보이스 1급화(화자 정체성 — 누가 말하는가). '~했다 벽'의 뿌리는 화자가 태도 0의 카메라라
    #   서술이 전부 '사건 과거 보고'가 되는 것(design-dp17-voice.md §1). narrator_voice 는 화자의 태도·입버릇·
    #   무엇을 즐기고 무엇에 이죽거리는가를 담는 자유 텍스트. worldgen 이 주인공 성격 시드에서 *도출*하되(발명 금지 —
    #   protagonist 문안 근거·증거 인용 게이트, B-36 time_anchors 패턴 재사용) 작가가 style PUT 으로 편집한다.
    #   문체 '품질' 지시는 null(D-26~30)이나 이건 품질이 아니라 화자 정체성(author_style/persona 계열=조향 실측 유효).
    #   기본 빈 값이면 render_style 이 아무것도 덧붙이지 않아 기존 프롬프트와 바이트 동일(무회귀); 구 JSON(필드 부재)은
    #   이 기본값으로 로드된다(하위호환). worldgen LLM 발명 차단: _strip_llm_style 이 폐기(pov·rules 와 동일 취급) —
    #   narrator_voice 는 증거 게이트 통과분만 도출 경로로 채워진다(발명 금지). first 시점에서만 render_style 이 삽입한다.
    narrator_voice: str = ""
    # VB-1: 서술자 보이스를 '정적 문자열'에서 '주인공 상태의 함수'로. 이 값이 가리키는 AttributeSpec.key 를
    #   회차 시점으로 조회(Ontology.state_as_of)해 EntitySpec.voice_stages 에서 해당 단계 카드를 고른다.
    #   빈 값(기본)이면 조회 자체를 하지 않아 기존 경로와 바이트 동일(무회귀·구 JSON 하위호환).
    #   근거: 정적 카드는 30화 내내 같은 반응 슬롯을 지정 → 같은 슬롯이 같은 방식으로 채워짐 = 틱. 카드가
    #   단계마다 갈리면 이전 단계의 틱 실현이 '지금 목소리에 안 맞는 것'이 되어 억제 없이 소스에서 끊긴다.
    #   ※ 이 갱신 축에는 사실 상태만 태운다(소속·자각도). 문체 관찰('농담을 자주 한다')을 태우면 모델이 쓴 것을
    #     카드가 추인하는 자기강화 루프가 되어 틱이 증폭된다(자기 이력 앵커링 역설과 동형).
    narrator_voice_stage_attr: str = ""
    # OP-1(VI-1): 오프닝 수법 풀 — "태그|긍정 지시문" 형식(예: "대사|대사 한 줄로 연다.").
    #   비면(기본) 주입 0 = 프롬프트 바이트 동일. 채우면 harness 가 회차마다 직전 화 오프닝 유형을
    #   결정론 제외하고 하나를 긍정형으로 낙점한다(핑크 엘리펀트 금지 — 피할 유형 호명 없음, 차단은
    #   후보 풀 제외로만). 근거: 오프닝 동형 9/11 실측 + 독자 증언("시작이 틀로 찍은 듯 비슷").
    opening_moves: list[str] = Field(default_factory=list)
    # RC-6 ②: 폐기 조어·금지어 → 정본 결정론 치환 목록(키=폐기 표기·값=정본 표기, 예: {"오랏줄":"결박줄","경면":"손거울"}).
    #   최종화 강제 교정(config finale_force_fixes)이 소비 — 명사 어간 치환이라 뒤따르는 조사는 보존된다('오랏줄을'→'결박줄을').
    #   목록은 **사용자 확정분만**(RX-3 계보·v4 정본 — memory `tool-noun-modernization`). 엔진에 작품 어휘 하드코딩 0(genre-blind).
    #   비면(기본) 치환 없음 = 바이트 동일(무회귀·구 JSON 하위호환). 사실 가드 G-A/G-B 통과분만 실제 채택된다.
    deprecated_terms: dict[str, str] = Field(default_factory=dict)


# 양성 원칙 헌법(항목 수 고정) — '두더지잡기' 회피 설계.
#  · 검출 가능한 축(틱·시제·조판 토막·훅 재탕)은 프롬프트가 아니라 quality_gates 결정론 백스톱이 담당하므로
#    여기서 빼고(harness 가 word_tics·tense_leak·fragmentation_score 로 자동 검출·국소 교정),
#    '게이트가 못 잡는 게슈탈트(보여주기·대사 결·어휘 질감·리듬·절단)'만 양성문으로 점화한다.
#  · 새 'AI 티'는 규칙을 +1 하지 말고(부정 명령은 패턴을 소환·증식), 기존 원칙 강화 또는 게이트 신호로 흡수.
#  · 고정 예문을 넣지 않는다(예문은 모델이 골격째 베끼는 자기표절의 진원 → few-shot 앵커는 별도 회전 슬롯에서만).
#  · C-5: 소프트 디폴트 성격(미학 — 보여주기·대사주도·어휘 구체화·확정서술·절단·오프닝 연출)의 부정명령과
#    기피 예문 호명은 긍정 지시로 전환(pink-elephant 소스차단 — B-23·C-3·C-4 계보).
#    하드 바닥(안전 계약)만 부정형 유지: 조판 '벽'(규칙1)·호칭 라벨 노출(규칙7)·끊긴 문장(규칙8)·
#    번역투 대체(규칙4, E-02 실측 입증) — docs/style-layering.md Layer 0(FLOOR_CONSTRAINTS·floor_only 관행).
#  · SR-1(2026-07-15): 규칙5에 quota 형으로 남아 있던 금지 예문 호명("'A가 아니라 B였다'식 … 아껴 써라")이
#    pink-elephant 로 그 문형을 오히려 유발(라이브 1화 실측: 부정-대조 7건/5.2천자·검출기는 전무) →
#    예문·범주 호명을 삭제하고 '직진 단언 + 동작 진행으로 긴 문장' 긍정 지향으로 대체(소스차단 — 검출기 신설 없음).
#  · ST-2: 표적을 '대사·행동 주도 + 모바일 문단 리듬 + 화자 표지'로 재작성(st1b_reference_style.md 실측 근거 —
#    가벼움은 짧은 문장이 아니라 짧은 문단·틱 0에서 온다). 항목 수(8)·floor 위치(0/3/6/7) 불변.
#  · EM-1(2026-07-20): 규칙 문안 자체의 삽입 대시(— 부연 —) 시연 제거(괄호·문장 분리로 재작성) + 규칙5에
#    '부연·정정·주석은 제 문장으로' 긍정 지침 증보. 지시문의 구두법이 곧 few-shot 앵커 — 본문 대시가 회차
#    누적 증가(괴담작 1→3→4→7건/화 실측)한 소스는 규칙·예시(sp1 EX1)가 그 구두법을 시연한 것(pink-elephant
#    거울상·FS-1 계보). 단일 대체형 지목 없음(ST-14 — 괄호/분리/증보 다형), 의성어·대사 끊김 대시는 비표적.
#    화자 표지(규칙3)는 인칭 분기를 중립 문안으로 담는다(3인칭=행동 비트 동반 / 1인칭=무태그 연쇄 허용) —
#    world/style 에 서술 인칭 필드가 없어 규칙8이 '지정 없으면 3인칭·과거형' 기본만 명시(인칭 필드 신설은 범위 밖).
#  · NM-1(2026-07-29): 수사 표기를 규칙1(모바일 조판)에 흡수 — 항목 수(8) 불변. 근거는 가독이지 정확성이 아니다
#    (모바일 세로에서 두 자리 이상 한글 수사는 파싱이 한 박자 늦다). 표기 축을 '값이 정보인 수 / 리듬으로 읽히는 수'
#    라는 **기능**으로 가르고 예시 토큰은 넣지 않는다 — 헌법의 고정 예문 금지 조항 그대로다. 이번 세션에 예문형
#    지시가 그대로 본문에 실려 나온 실측이 둘(EM-1 대시 시연·보이스 단계 카드 직역) 있어 같은 소스를 반복하지 않는다.
#    '한 대목 안 표기 정합' 문장을 함께 넣은 이유: 실제 전환 작업에서 판정 모델이 같은 대화 안의 금액을 88만/팔십팔만
#    으로 갈라놓은 실측(o5rewriteall 9화)이 있었고, 혼용은 어느 한쪽 표기보다 나쁘다. 한글 수사가 레지스터인 장르
#    (무협·사극 등)는 작가 문체 오버레이가 이 미학 축을 덮는다 — genre-blind 유지(장르 분기 코드 0).
DEFAULT_STYLE_RULES = [
    # VP-1(2026-08-13): "대체로 1~2문장" 처방이 2문장 문단 벽의 소스로 실측(작가 정독 + 페르소나 패널 4인)돼
    #   1~4문장 변주로 개정. 상한 4는 패널 합치점(5문장은 모바일 이탈점). 종이책 '벽' 부정 지시도 제거(pink-elephant).
    "조판은 모바일 세로 화면 기준으로. 문단은 장면의 호흡에 맞춰 1~4문장으로 변주하라. 관찰·설명·정리는 서너 문장을 한 문단으로 이어 쓰고, 행동·심리·대사가 바뀔 때 줄을 바꿔라. 대사는 한 줄에 하나, 단독 문단으로 세워라. 수량·금액·시간처럼 값 자체가 정보인 수는 아라비아 숫자로 적어 한눈에 들어오게 하고, 관용 표현에 붙어 리듬으로 읽히는 수는 한글로 적어라. 한 대목 안에서는 같은 계열의 수를 같은 표기로 맞춰라.",
    # VP-1: 화자 명제 규칙 추가 — 개성은 지각·해석 회로의 출력(조사 2026-08-13). 근거 없는 격언은 호명하지 않고
    #   긍정형('출력으로만 세워라')으로만 조인다.
    "정보와 감정은 인물의 행동·대사·선택으로 드러내라. 마음 상태는 몸이 먼저 말하게 하고(마른침, 굳는 손끝, 빗나간 손, 끊긴 말), 거기에 이름을 붙여 해석하는 몫은 독자에게 맡겨라. 세계관·설정도 화자의 설명에 기대기보다 인물이 하는 말과 행동 속에 실어 흘려라. 화자의 결론 명제는 바로 앞 장면에서 보고 세고 겪은 것의 출력으로만 세워라.",
    "대사가 장면을 끌고 가게 하라. 정보·갈등·긴장은 인물이 주고받는 짧은 대사(티키타카)로 흘리고, 단독 액션 장면에도 짧은 대사가 오가게 하라. 누가 말하는지는 대사 앞뒤에 붙는 짧은 행동 비트(손짓·시선·자세)로 보여라. 외부(3인칭) 시점일수록 비트를 성실히 붙여 화자를 몸짓으로 못박고, 화자의 목소리가 장면을 이끄는 1인칭이라면 태그 없는 대사를 몇 마디 이어 붙여 리듬을 살려도 좋다. 인물마다 말투·말버릇·계급감을 실제 대사로 분화하고, 서로 아는 설정은 전제로 깐 채 대사에는 상대가 모르는 것과 지금 결정할 것만 실어라. 대사는 대부분 특별할 것 없는 생활의 말로 흘러가게 하라. 정보를 건네고, 되묻고, 머뭇거리고, 딴청을 부리는 보통 말. 대사 문장은 다음 행동으로 넘어가는 디딤돌로 쓰고, 울림 있는 단정 한 문장은 회차의 결정적 순간 한두 번이면 족하다. 인물은 처음 겪는 일도 제가 아는 쉬운 말로 옮겨 말한다.",
    "어휘는 구어·직설·구체로. 구체적인 명사와 동사로 문장을 세우고, 수식어·비유는 아껴 결정적 순간에만 한 번씩 살려라. 추상적 은유나 번역투 대신 몸이 느끼는 감각(소리·색·온도·무게)으로 못박아라. 의성어·의태어는 꼭 필요한 결정적 순간에만 아껴 써라.",
    # VP-1: '타격감·들쭉날쭉' 리듬 공학 문안을 정보-완결문 원칙 + 종결 다양화로 교체. 종결 다양화는 파편 축을
    #   조일 때 '-다 벽' 재폭주를 막는 필수 대체 수단(적대 리뷰 치명 1 — 파편↔'-다' r=-0.95 실측, 같은 커밋 조건).
    "묘사·관찰·정보 전달은 완결된 한 문장으로 이어 써라. 시간·수량·정도·결과는 그 문장 안에 안겨 넣어라. 같은 종결이 세 문장 이어지기 전에 다른 종결(연결형으로 잇기·현재형 판단·입말 생각)로 옮겨 가며 순환하라. 긴 문장은 하나의 동작이 다음 동작으로 이어지는 진행으로 늘리고, 결정적 대목은 단정하는 확정 서술로 못박아라. 덧붙일 부연·정정은 제 문장으로 세워라.",
    "장면은 지금 벌어지는 사건의 한복판에서 열고 곧장 움직여라. 자원·수치·설정·상태는 그 사건 속 행동과 대사로 드러내고, 한 장면에 최소 한 번은 예상을 비트는 변수(헛디딤·오판·대가·돌발)를 넣어 이야기를 굴려라. 장면과 회차의 끝은 정점의 미해결 상태를 구체적 이미지 한 줄로 남겨, 마지막 줄까지 장면 '안'의 사건·대사·이미지로만 말하며 다음이 궁금해지게 끊어라.",
    "호칭은 설정 명부의 괄호 라벨을 그대로 노출하지 말고, 장면에 맞는 자연스러운 작중 호칭으로 불러라. 수치·시간·거리·인원·연속성은 회차 안에서 서로 어긋나지 않게 맞춰라.",
    "한 회차는 공백 포함 4,500~5,500자를 목표로 하라. 밀도를 유지한 채 이 범위 안에서 끝나도록 장면을 배분하라. 문장을 자연스럽게 맺으며 완결하고, 끊긴 문장으로 끝내기 금지. 서술 인칭과 시제는 작품 전체에서 하나로 일관되게 유지하라(지정이 없으면 3인칭·과거형을 기본으로, 액션 정점의 현재형만 양념).",
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
    time_anchors: list[TimeAnchor] = Field(default_factory=list)   # B-33: 시계 파생 산술 앵커(계약 만기·나이). 비면 무주입(하위호환)
    beats: list[Beat] = Field(default_factory=list)
    wiki_seeds: list[WikiSeed] = Field(default_factory=list)
    spine: Optional[NarrativeSpine] = None    # 엔딩-주도 아크/에피소드 구조(None=평면 beats 모드)
    genre_contract: Optional[GenreContract] = None   # G5: 장르 정체성 공유 컨텍스트(None=미생성, 하위호환)
    allow_state_reversal: bool = False        # True=회귀/부활/리젠 허용(비가역 상태 이탈을 모순으로 막지 않음)
    plant_reminder: Literal["off", "gentle", "active"] = "off"   # 미회수 복선 리마인더 강도(작가 제어, ③).
    # 기본 off=시스템이 떡밥을 생성에 주입 안 함(비강제 — 떡밥 가시화는 G1 원장 텔레메트리가 담당, 작가가 빨간펜으로 조향).
    # 작가가 명시 opt-in 시: gentle=참고 정보만(회수 비강제) / active=finale 에서 회수 독려
    style: StyleSpec = Field(default_factory=StyleSpec)


# ── RC-3 도구 정본 이중 소스 — 엔티티 우선·정규식 폴백(단일 장애점 해소) ────────────
_TOOL_VOCAB_RE = re.compile(r"도구 어휘\(([^)]+)\)")


def _etype_category(world, etype: str) -> str | None:
    """etype → 카테고리 해소(작품 카탈로그 우선·BUILTIN 폴백). 미등록 etype 은 character 만 actor
    (ontology.is_actor 폴백과 동일 계약). genre-blind — 장르 라벨 분기 0."""
    for t in (getattr(world, "entity_types", None) or []):
        if getattr(t, "key", None) == etype:
            return getattr(t, "category", None)
    for t in BUILTIN_ENTITY_TYPES:
        if t.key == etype:
            return t.category
    return "actor" if etype == "character" else None


def object_entity_names(world) -> list[str]:
    """RC-3 ⓐ: object 카테고리(도구·아이템·아티팩트) 엔티티의 이름 목록(entities 순서 보존).
    도구 정본의 '엔티티 우선' 소스 — 없으면 [](호출부가 정규식 폴백). genre-blind."""
    out: list[str] = []
    for e in (getattr(world, "entities", None) or []):
        nm = (getattr(e, "name", "") or "").strip()
        if nm and _etype_category(world, getattr(e, "etype", "")) == "object":
            out.append(nm)
    return out


def canon_tool_vocab_match(world) -> "re.Match | None":
    """RC-3 ⓑ 폴백: 장르 계약 vocabulary_tone 의 '도구 어휘(...)' 정규식 매치(genre_contract None 안전).
    m.group(1) = 도구 열거 원문(엔티티 부재 시 바이트 동일 소스 — 하위호환)."""
    gc = getattr(world, "genre_contract", None)
    return _TOOL_VOCAB_RE.search(getattr(gc, "vocabulary_tone", "") or "") if gc is not None else None
