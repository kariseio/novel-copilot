# -*- coding: utf-8 -*-
"""엔진 공유 타입 계약 (Pydantic v2). 비대칭 일관성 불변식을 '타입'으로 강제.

- ContextBoard 의 ground_truth / narrative / authority 는 서로 다른 타입 → 혼합 불가.
- 모든 Violation 은 signal_grade 를 갖는다 → det/quasi 만 binding, semantic 은 보고·escalation.
"""
from __future__ import annotations
import hashlib
import uuid
from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field, PrivateAttr


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
    source: Literal["rag_chunk", "wiki_page", "arc_anchor", "bible", "cast_debut", "worldrule"]
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
    world_rules: list[str] = Field(default_factory=list)   # 세계 불변 규칙 — 고신뢰(확정 설정과 동급, 낮은 신뢰 narrative 아님)
    story_time: str = ""                                   # CN-1: 결정론 누적 절대시점(고신뢰 — 모델은 읽기만, 시간 역행/모순 금지)
    narrative: list[RetrievedItem] = Field(default_factory=list)
    authority: list[AuthorDirective] = Field(default_factory=list)
    prev_chapter: str = ""            # 서사 흐름: 직전 회차 원문(연속성)
    story_so_far: str = ""            # 누적 줄거리 요약(narrative, '안 까먹기'의 요약 다리)
    voice_cards: str = ""             # 등장 인물별 말투 시그니처(보이스 분화 — 스타일 지침, 캐논 아님)
    # SY-1 story 모드(설계 §5): 둘 다 기본 "" → assemble 산출 바이트 동일(하위호환 계약).
    confirmed_story: str = ""         # 초안 콜: 작가 게이트 통과 확정 스토리(원문 — 블록 렌더는 assemble)
    story_remaining: str = ""         # 이어쓰기 콜: generate()가 계산·렌더한 잔여 줄 블록(계산 지점 단일화)


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
    # OV-5: "stage_failure" = 제안 콜 자체가 실패한 회차의 정직 표식(빈 [] 와 구분 — 침묵 {} 강등 사고의 소스 차단).
    op: Literal["new_entity", "state_change", "contradiction", "relation", "stage_failure"]
    entity: str
    detail: str
    applied: bool
    reason: str = ""
    # OV-4: contradiction 재계층 — "review"=작가 결정 여지(통제어휘 밖·단조·미정의상태·카디널리티: 새 값/의도된 변화일 수 있음),
    #       "conflict"=논리적 불가(사망→생존·불변속성 변경·동시점 충돌). UI 가 필터 아닌 시각 계층으로 구분(실신호 묵음 회피).
    severity: Literal["conflict", "review"] = "conflict"


class ChapterRevision(BaseModel):
    """퇴고(회차 본문 사후 다듬기) 1건의 이력 레코드.

    본문은 여전히 ChapterRecord.text 단 하나(최신본)이고 이 레코드는 가산(append)만 한다.
    undo 는 새 레코드를 만들지 않고 마지막 비-reverted 레코드를 reverted=True 마킹 +
    before_text/before_summary/before_detail_synopsis 스냅샷으로 결정론 복원한다.
    모든 필드가 default 보유 → 구 JSON 무중단 로드(revisions 없는 기존 회차는 [] 기본).
    """
    revision_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])  # 충돌 없는 고유 ID(accept/reject lookup 키)
    directive: str = ""                               # 작가 자유 지시
    span_text: str = ""                               # 선택적 구간 원문 부분문자열(없으면 전체 다듬기)
    before_text: str = ""                             # 퇴고 전 회차 본문(undo 복원용)
    after_text: str = ""                              # 퇴고 후 회차 본문
    before_summary: str = ""                          # undo 결정론 복원용 스냅샷
    before_detail_synopsis: str = ""                  # undo 결정론 복원용 스냅샷
    before_summary_degraded: bool = False             # P-2: undo 시 요약 신뢰강등 플래그도 복원(기본 False — 구 JSON 로드 정합)
    # RV-2②: 이 퇴고 채택 *직전*의 파생물 stale 스냅샷(undo 대칭 복원용). accept 는 LLM 콜 파생물(위키·약속원장·
    #   연속성점검·독자예측)을 자동 재콜하지 않고 stale 로 마킹만 하므로(비용·무강제), undo 는 새 상태를 지우고
    #   이 스냅샷으로 결정론 복원한다. 기본 {}=구 JSON 로드 바이트 동일(하위호환).
    before_derivatives_revised_stale: dict = Field(default_factory=dict)
    passes_used: list[Literal["reformat", "fix_tense"]] = Field(default_factory=list)  # 'reformat' | 'fix_tense' 만 허용(D1)
    violations_before: list[Violation] = Field(default_factory=list)   # 하드만
    violations_after: list[Violation] = Field(default_factory=list)    # 하드만
    claim_changes: list[dict] = Field(default_factory=list)    # G-B: {entity, key, before, after}
    claim_flaps: list[dict] = Field(default_factory=list)      # VP-3: 요동 강등분(차단 안 함·정직 기록). 기본 []=구 JSON 하위호환
    guardrail_passed: Optional[bool] = None           # None=미검사, False=실패, True=통과
    guardrail_reason: str = ""
    reverted: bool = False
    reverted_at: str = ""                             # FI-1 §4: undo 마킹 시각(서버 time.strftime). additive·기본 ""(구 JSON 바이트 동일 로드)
    created_at: str = ""                              # 서버 time.strftime — 클라이언트 생성 금지
    # ST-14 FIX-3: 재실현 채택 시 승자에 적용한 최종화 스택(reflow→휴머나이즈/style_repair) 내역(additive·투명성).
    #   {reflowed:bool, humanize:[…], style_repairs:[…]}. 작가 발동 퇴고(revise)엔 없음(기본 {} — 구 JSON 로드 정합).
    finalize_repairs: dict = Field(default_factory=dict)


class TimeDelta(BaseModel):
    """구조화 시간 경과(CN-1) — 플래너가 *자기 회차의* 시간 점프를 라벨링한다(LLM 산수 0; 누적은 story_clock 코드).
    mode=advance 만 메인 시계를 전진시킨다(flashback/parallel 은 전진 안 함). unit 미상 → story_clock 이 null degrade.
    unit/mode 는 Literal 이 아니라 str — LLM 오타('days')가 델타 전체를 탈락(검증실패)시키지 않게, 미상은 누적서 건너뜀.
    허용값: unit ∈ {minute,hour,day,week,month,year}, mode ∈ {advance,flashback,parallel,unknown}."""
    amount: float = 0.0
    unit: str = ""
    mode: str = "advance"

    @classmethod
    def parse(cls, raw) -> "Optional[TimeDelta]":
        """LLM dict → TimeDelta(관대한 코어션). dict 아니면 None(null degrade). 의미 검증은 story_clock 가."""
        if not isinstance(raw, dict):
            return None
        try:
            amt = float(raw.get("amount", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        return cls(amount=amt,
                   unit=str(raw.get("unit") or "").strip().lower(),
                   mode=str(raw.get("mode") or "advance").strip().lower() or "advance")


class RedpenMark(BaseModel):
    """RP-1 '빨간펜' — 작가가 읽다가 마음에 안 드는 구간을 표시하고 이유 메모를 단 1건.

    설계 불변(3):
      ①무강제 — 마크도, 그에 붙은 방향 제안(suggestions)도 아무것도 자동 실행하지 않는다. 본문 바이트는
        절대 만지지 않는다(수집·표시 경로 LLM 0, 방향 제안만 작가 발동 1콜). 채택/편집은 별도 경로(edit/revise).
      ②stale 은 저장하지 않는 '파생 상태' — 현재 본문에서 앵커(anchor_text)를 겹침 포함 계수로 유일 탐색하는 데
        실패하면(0회 또는 2회+) 그 마크는 stale 이다. 본문 변경을 감시하는 훅은 0개이며, undo 로 본문이 원상 복구되면
        앵커가 다시 유일해져 마크가 자동 부활한다. 그래서 stale 플래그를 레코드에 굳히지 않는다(굳히면 부활이 깨진다).
      ③생성·퇴고·재실현 컨텍스트에 자동 유입 금지 — redpen 을 읽는 곳은 suggest_redpen_directions 와 직렬화뿐이다.
        '피할 대상을 컨텍스트에 노출'하는 자기 이력 앵커링 계보의 역설을 되풀이하지 않는다. 소비는 작가 발동 UI 프리필뿐.
    모든 필드가 default 보유 → 구 JSON 무중단 로드(redpen 없는 기존 회차는 [] 기본)."""
    mark_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    anchor_text: str = ""    # 본문 유일 앵커(표시 구간 포함) — 유일성은 겹침 포함 계수로 판정
    span_start: int = 0      # anchor_text 내 표시 구간 시작
    span_len: int = 0        # 표시 구간 길이(>0)
    note: str = ""           # 왜 마음에 안 드는지(선택)
    created_at: str = ""     # 서버 time.strftime — 클라이언트 생성 금지
    suggestions: list[str] = Field(default_factory=list)   # AI 방향 메뉴(advisory·다형 — 대체 문장 아님)
    suggested_at: str = ""


class ChapterRecord(BaseModel):
    chapter: int
    title: str = ""
    # FI-1 §4: 회차 세대 각인(결정론·LLM 0). 이 회차의 누적 재생성 횟수+1(최초 생성=1, 재생성마다 +1 — regen_events
    #   와 동급 식별자). additive·기본 1(구 JSON 바이트 동일 로드 — 구 회차의 1 은 '미상: 과거 재생성 이력 미반영'이며
    #   소급 추정하지 않는다). 작가 의도 이벤트가 emit 시점 회차 gen_no 를 스냅샷 → 폐기된 세대에 남긴 이벤트가
    #   새 세대 계측과 조인되는 유령 신호를 차단(§4).
    gen_no: int = 1
    status: ChapterStatus
    text: str = ""
    summary: str = ""                 # 한 줄 요약(과거 회차의 압축 표현 — 누적 story_so_far 의 원거리 레이어)
    detail_synopsis: str = ""         # 상세 시놉시스(~1,500자: 사건 인과·감정 변화·물리 디테일·미결 — 근거리 레이어)
    # P-2: 요약 LLM 실패(절단/빈응답/파싱)로 detail_synopsis 가 프로즈-유래가 아니라 '비트 계획 합성 요지'로 폴백됐음.
    #      기본 False(하위호환 — 구 JSON 로드 시 바이트 동일). True 면 story_so_far/최근요약 조립이 이 회차의
    #      근거리 detail 을 신뢰 강등(프로즈 슬라이스 폴백 금지 → 합성 요지만 재유입, 재상연 불가).
    summary_degraded: bool = False
    scenes: int = 0
    n_retrieved: int = 0
    indexed_chunks: int = 0
    wiki_pages_touched: int = 0
    # DG-1: 인물별 대사 원장 — [{idx, speaker, text}] (확정 후 관측 전용·생성 입력 주입 금지·기본 []=하위호환)
    dialogue_ledger: list[dict] = Field(default_factory=list)
    arc_id: Optional[str] = None      # spine 모드: 이 회차가 속한 아크/에피소드
    episode_id: Optional[str] = None
    # G4: 비트의 기능 차원을 회차 기록에 영속 — 다음 회차 설계가 '최근 훅 유형 이력'을 결정론 비교(반복 차단)
    chapter_function: str = ""
    hook_type: str = ""
    # DP-22: 회차가 물리적으로 닫히는 장치 자기 라벨(dialogue/action/sensory/object/arrival/interior). 다음 회차 설계가
    #   '최근 2화 사용 장치'를 결정론 비교해 순환 제시(B-37 계보). 빈 값=미기술(구 JSON 로드 바이트 동일 하위호환).
    closing_device: str = ""
    # SX-1: 회차 중심 장면 안무 자기 라벨(잠입/대면/시험/추적/…/일상). 다음 회차 설계가 '최근 2화 사용 형태'를 결정론 비교해
    #   순환 제시(closing_device 와 동형·B-37 계보). 빈 값=미기술(구 JSON 로드 바이트 동일 하위호환).
    scene_form: str = ""
    # SX-3: 이 회차에서 독자가 처음 알게 되는 세계 사실 0~2개(계획 재료·advisory). 검증 축이 존재 여부만 병기(판정 없음).
    #   빈 리스트=노출 없음(정직 — 발명 안 함). 구 JSON 로드 시 바이트 동일(default_factory).
    world_reveal: list[str] = Field(default_factory=list)
    time_advance: str = ""                                    # 자유텍스트 라벨(작가 가시화·pacing — 코드 분류 안 함)
    time_delta: Optional[TimeDelta] = None                    # CN-1: 구조화 델타(story_clock 결정론 누적용 영속). 없으면 null degrade
    place: str = ""
    drift_signals: list[str] = Field(default_factory=list)   # 결정론 드리프트 advisory
    claim_audit: list = Field(default_factory=list)          # CN-2: 자유형 사실 모순(과거 회차 대조) advisory — 비구속·작가 가시화
    reader_feedback: dict = Field(default_factory=dict)       # G2: 블라인드 독자 행동 예측(advisory — 작가 가시화, 비구속)
    # RV-2②: 퇴고 accept 후 '옛 본문 기준'으로 남은 LLM 콜 파생물의 stale 표식(additive·advisory·무강제).
    #   accept 는 본문·요약·RAG·ai_tell 처럼 LLM 0콜(또는 요약 1콜) 파생물만 동형 재계산하고, 위키 ingest·약속원장
    #   정산·연속성 점검(claim_audit)·독자 예측(reader_desk)처럼 회차당 추가 LLM 콜을 요하는 파생물은 자동 재콜하지
    #   않는다(비용·no-force). 대신 여기에 {"wiki":true,"promise_ledger":true,"claim_audit":true,"reader_feedback":true}
    #   식으로 '퇴고 후 stale' 을 표식만 하고 UI 배지로 가시화한다(작가가 필요 시 재생성/수동 검토). 생성 경로에서
    #   해당 파생물을 만들지 않는(설정 off·spine 없음) 항목은 애초에 stale 대상이 아니므로 넣지 않는다.
    #   빈 {}=stale 없음(퇴고 미채택·전부 재계산됨). 구 JSON 로드 시 바이트 동일(default_factory).
    derivatives_revised_stale: dict = Field(default_factory=dict)
    # XR-7④: 작가가 발동한 파생물 재계산의 '기준 리비전' 각인(additive·undo 대칭용). 값 = 재계산 시점의
    #   활성(비-reverted) 리비전 수 N. undo 로 활성 수가 N-1 이 되면 그 파생물은 *버려진 본문* 기준이므로
    #   derivatives_revised_stale 로 되돌려 표식한다(침묵 정합 오표 차단 — 복원 스냅샷이 '깨끗함'을 주장하는 창).
    #   빈 {}=재계산 이력 없음. 구 JSON 로드 시 바이트 동일(default_factory).
    derivatives_recomputed: dict = Field(default_factory=dict)
    ai_tell: dict = Field(default_factory=dict)             # 한국어 AI티 분포 신호(결정론·LLM 0콜·advisory 추세, KatFishNet 자질)
    # SP-1 Stage B: finalize 직전 국소 리듬 스팬 수리 내역(additive·투명성 영속 — 작가 열람). 각 항목:
    #   {span(kind/문장인덱스/문자범위), before/after 길이, changed, retried, fallback, guardrail_ok}.
    #   빈 리스트=수리 없음(스팬 미검출·style_repair OFF·상한 0). 구 JSON 로드 시 바이트 동일(default_factory). 무강제·비판정.
    style_repairs: list[dict] = Field(default_factory=list)
    # HM-1b: finalize 후 결정론 티 탐지→Claude 윤문 국소 수술 내역(additive·투명성 영속 — 작가 열람). 각 항목:
    #   {category(N-1~N-6), severity(S1~S3), char_start/end, before/after 길이, changed, change_rate, rate_band,
    #    fallback, coverage_passed, guardrail_ok, author_review("작가 확인 요망"), note}. 빈 리스트=윤문 없음
    #   (탐지 0·humanize OFF·상한 0). 구 JSON 로드 시 바이트 동일(default_factory). 무강제·비판정.
    humanize: list[dict] = Field(default_factory=list)
    ending_contract_eval: dict = Field(default_factory=dict)  # EC-1: 엔딩 술어계약 평가 스냅샷(결정론·LLM 0콜·gt/ni 병렬 — advisory, 비구속)
    # PR-2: 회차 통합 검증 리포트(SSOT) — FINALIZED/ESCALATED 확정 직후 전 축 결정론 집계(engine.verification.build_verification).
    #   축이 안 돈 경우 null 이 아니라 "미실행" 문자열 명시(결측 정직 — 누락의 조용한 통과 차단). 신규 검출기 0(기존 계측 집계).
    #   빈 {}=미집계(구 회차·구 JSON 로드 시 바이트 동일 하위호환). 무강제·비판정(값·인간 대역·미실행 표식만).
    verification: dict = Field(default_factory=dict)
    gen_context: dict = Field(default_factory=dict)           # 디버그: 이 회차를 '어떤 정보로' 생성했는가(계획 비트 + 집필 입력 슬롯, 트림)
    recovery_hints: list[dict] = Field(default_factory=list)  # ESCALATED 시 작가용 자연어 진단+회복 레버(engine.recovery)
    initial_violations: list[Violation] = Field(default_factory=list)
    final_violations: list[Violation] = Field(default_factory=list)
    rounds: list[RoundTrace] = Field(default_factory=list)
    ontology_changes: list[OntologyChange] = Field(default_factory=list)
    usage_by_stage: dict = Field(default_factory=dict)   # 단계별 토큰(단위경제: 일관성 오버헤드율 계산 재료)
    # TM-1: 단계별 소요 시간(초·time.monotonic 기반·소수 1자리) — usage_by_stage 의 '시간판' 대칭 미러.
    #   키는 usage_by_stage 와 동일(draft·check·rewrite·wiki·summarize·claim_audit·ontology_propose·
    #   ledger_reconcile·reader_desk·rerender 등). 콜 없는 단계는 넣지 않는다(0초 노이즈 금지). 빈 {}=미계측
    #   (구 회차·구 JSON 로드 시 바이트 동일 하위호환 — usage_by_stage 와 동급의 additive 필드). 무강제·advisory.
    time_by_stage: dict = Field(default_factory=dict)   # 단계별 소요 시간(초, 소수 1자리 — usage_by_stage 시간판 대칭)
    revisions: list[ChapterRevision] = Field(default_factory=list)   # 퇴고 이력(append-only; 본문은 text 1개 유지, undo 시 마지막 비-reverted 복원)
    # RP-1 '빨간펜' 마크(additive·구 JSON 무중단 로드). 작가가 표시한 '마음에 안 드는 구간'+이유 메모의 append-only 목록.
    #   본문 바이트 불변(무강제) — 마크는 본문을 만지지 않는다. stale 은 파생 상태라 저장하지 않고(RedpenMark docstring ②),
    #   생성/퇴고/재실현 컨텍스트 빌더로 유입시키지 않는다(앵커링 가드 ③). 소비는 suggest_redpen_directions·직렬화뿐.
    redpen: list["RedpenMark"] = Field(default_factory=list)
    # EP-PUB: 외부 플랫폼 수동 발행 원장(additive·구 JSON 무중단 로드·LLM 0·advisory·무강제).
    #   툴은 어디에도 자동 업로드하지 않는다 — 작가가 '발행함으로 표시'를 누른 사실과 그 시점 본문 지문만 기록한다.
    #   현재 본문 지문 ≠ published_fingerprint 이면 '발행 후 수정됨(재발행 필요)'으로 파생 판정한다(저장 안 함 —
    #   publish_state() 로 매 조회 시 계산). 어떤 게이트에도 연결되지 않는다(생성·퇴고·완결 차단 0).
    published_at: str = ""            # 마지막 발행 시각(서버 time.strftime; 빈 값=미발행)
    published_fingerprint: str = ""   # 발행 당시 본문 text 의 지문(text_fingerprint — 정확 일치 기준)
    published_note: str = ""          # 어디에 올렸는지 자유 메모(플랫폼명/URL — 선택)
    # OV-2: 최종 check_text 의 정규화+증거강제 엔티티 클레임(같은 회차 propose 가 재추출 없이 소비). PrivateAttr=휘발
    #  → 디스크 미영속(model_dump/json 제외)·FE 미노출·재수화 회차는 propose 안 타므로 무손실. 생성 후 harness 가 세팅.
    _final_claims: list = PrivateAttr(default_factory=list)
    # GA-1: 생성 트레이스(중간 산출물 전량) — 첫 초안·재작성 라운드·style_judge 판정 전문·humanize before/after·
    #   final. harness.generate 가 finalize 에서 채운다. PrivateAttr=본체 JSON 미영속(용량·하위호환) — 서비스가
    #   generate() 반환 직후 repo.save_trace 로 사이드카(`<pid>.trace.<ch>.json`)에 append-only 영속한다.
    #   None=gen_trace OFF 또는 미수집(트레이스 미생성) → 서비스가 save_trace 를 아예 호출하지 않는다(바이트 동일).
    _gen_trace: Optional[dict] = PrivateAttr(default=None)

    @property
    def hard_remaining(self) -> list[Violation]:
        return [v for v in self.final_violations if v.is_hard]


# ---- EP-PUB: 발행 원장 파생 계산(결정론·LLM 0·저장 안 함) ----
def text_fingerprint(text: str) -> str:
    """회차 본문 지문 — 발행 원장 대조용. sha256 hex 앞 16자. 정확 일치 기준이라 어떤 본문 변경도 지문을 바꾼다
    (퇴고·직접 편집·재실현·재생성 → 발행 지문과 어긋남 → '발행 후 수정됨'). 공백 정규화하지 않는다(작가가 발행한
    바로 그 판본과의 동일성만 본다)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def publish_state(ch: "ChapterRecord") -> str:
    """발행 파생 상태(영속 안 함) — 'unpublished' | 'published' | 'modified'.
    published_at 없으면 미발행. 발행 지문 = 현재 본문 지문이면 최신 발행본('published').
    다르면 발행 후 본문이 바뀐 것 → 재발행 필요('modified')."""
    if not (ch.published_at or "").strip():
        return "unpublished"
    return "published" if ch.published_fingerprint == text_fingerprint(ch.text) else "modified"


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
    # XR-10(cross-review/007 §3): 재구축 시 이 페이지에 반영된 회차별 본문 지문(str(회차)→fingerprint) —
    #   Projection-원고 연결(어떤 원고 판본을 근거로 합성됐는지). 구 JSON 은 빈 dict 로 무변경 로드(additive).
    source_fingerprints: dict = Field(default_factory=dict)


class StoryPassRecord(BaseModel):
    """SY-1 스토리 패스 회차 레코드(설계 docs/design-sy1-story-pass-wiring.md §2).

    경량 본체 — 깔때기 전문(후보 3·리뷰·심문·쌍대·스캔)은 repo.save_trace(kind="story_pass")
    사이드카에 영속하고 본체에는 확정 스토리·검사·포인터만 둔다. 승격 경로는 작가 게이트
    (confirm) 하나뿐이며 status != "confirmed" 이면 confirmed_story 는 항상 "" 다(무강제).
    """
    chapter: int
    # RC-2 ⓒ: "failed" 추가 — 깔때기 자동 실행 실패(재료 부재·예외)를 emit(휘발) 대신 영속하는 상태.
    #   구 JSON(이 값 부재)은 기본 "candidate" 로 로드(하위호환·additive).
    status: Literal["candidate", "confirmed", "discarded", "failed"] = "candidate"
    confirmed_story: str = ""          # 작가 게이트 통과분(SSOT)
    source: str = ""                   # "solo" | "merged" | "edited"(작가 편집분)
    variants: list[dict] = Field(default_factory=list)   # [{name, story, fmt_errs}] — 확정 사슬 통과분
    checks: dict = Field(default_factory=dict)           # 확정 시점 fmt_check·줄 수·훅 소화 인용
    materials_digest: str = ""         # 재료 sha256 앞 12(사본 저장 아닌 재조립 재현용)
    created_at: str = ""
    confirmed_at: str = ""
    trace_ref: str = ""                # 사이드카 참조(깔때기 전문)
