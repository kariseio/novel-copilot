# -*- coding: utf-8 -*-
"""프로젝트 영속 상태 — Repository 가 직렬화/역직렬화하는 단일 집계 루트(aggregate)."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

from .world import WorldConfig, TimelineEntry, EntitySpec
from .types import AuthorDirective, WikiPage, ChapterRecord, RelationEdge, StoryPassRecord
from .narrative import NarrativeProgress, EndingContract
from .ledger import PromiseLedger
from .bible import StoryBible
from .skill import Skill


class ProjectSeed(BaseModel):
    """작가가 던지는 최소 시드(인간 개입 1차 지점)."""
    title: str = ""
    genre: str = "현대 판타지"
    tone: str = ""                          # 비우면 worldgen 이 장르에 맞는 톤을 정함(액션 톤 기본값 강제 제거)
    premise: str = ""                       # 한 줄 전제
    protagonist_hint: str = ""              # 주인공 힌트(선택)
    target_chapters: int = 12
    # DP-18: 서술 시점 명시(작가 결정 — worldgen 이 발명하지 못하게 시드에서만 지정). 기본 ""=무지정 →
    #   create_project 가 world.style.pov 에 아무것도 반영하지 않아 StyleSpec 기본값(third_limited)을 그대로
    #   상속, 프롬프트·구 JSON 바이트 동일(하위호환). 값을 명시하면 worldgen *전에* world.style.pov 로 배선되어
    #   first 일 때 generator 의 narrator_voice 도출(증거 게이트)이 정상 경로로 실행된다(사후 패치의 스킵 부작용
    #   소스 해소). ""=무지정 / third_limited=밀착 3인칭 / third_omniscient=3인칭 전지 / first=1인칭 주인공.
    pov: Literal["", "third_limited", "third_omniscient", "first"] = ""


class PersistedChunk(BaseModel):
    chapter: int
    version: int
    text: str
    emb: list[float] = Field(default_factory=list)


class RegenEvent(BaseModel):
    """IN-12: 재생성 '실행' 이벤트 1건 — 순수 계측(LLM0·결정론. 분석·판정·자동반응 없음).
    작가가 실제로 재생성을 실행한 회차의 원천 로그(유일한 비아첨 실행동 신호).
    append-only: pregen 스냅샷 복원·재생성 truncate·되돌리기(undo)에 휩쓸리지 않는다(계측은 서사 파생상태가 아님)."""
    chapter: int                     # 재생성 대상 회차 번호(FE-1/2 공통 = 마지막 회차)
    seq: int = 1                     # 같은 회차의 누적 재생성 횟수(이 이벤트 포함, 1-base — 코드 계산)
    fix_selected: bool = False       # FE-2 '점검 반영 재생성'(fix 그룹 선택) 여부. False=단순 재생성(FE-1)
    at: str = ""                     # 서버 타임스탬프(time.strftime — 클라이언트 생성 금지)


class CoverMeta(BaseModel):
    """CV-1/CV-2: 표지 이미지 메타(additive — 바이너리는 별도 파일 {pid}.cover.*.png, main JSON엔 메타만).
    CV-2: 재생성은 덮어쓰기 대신 보관함(ProjectState.covers)에 append — 표지 하나하나가 별도 파일·별도 메타.
    history 필드는 구 JSON(CV-1 재생성 이력) 로드 호환용으로만 유지 — 신규 생성은 더 이상 채우지 않는다(보관함이 이력)."""
    filename: str = ""                       # 바이너리 파일명(예: {pid}.cover.<ts>.png) — repository 가 경로 조립
    prompt: str = ""                          # 합성(또는 작가 오버라이드) 영문 이미지 프롬프트(작가 열람)
    model: str = ""                           # 사용 이미지 모델(계측·재현)
    size: str = ""                            # 생성 크기
    created_at: str = ""                      # 서버 타임스탬프
    history: list["CoverMeta"] = Field(default_factory=list)   # (구 CV-1 데이터) 재생성 이력 — 신규는 비움(보관함 대체)


class ProjectState(BaseModel):
    """프로젝트 전체 상태. 엔진 세션은 이로부터 재수화(rehydrate)된다."""
    id: str
    seed: ProjectSeed
    world: WorldConfig
    created_at: str = ""
    cover: Optional[CoverMeta] = None        # CV-1: '적용된' 표지 메타 SSOT(additive·기본 None — 구 JSON 바이트 동일 로드). 바이너리는 별도 파일
    covers: list[CoverMeta] = Field(default_factory=list)   # CV-2: 표지 보관함(갤러리) — 생성분 전량 append. 구 JSON 은 [] 기본(무변경 로드). state.cover 는 이 중 적용본

    # 진행 상태
    current_chapter: int = 0                 # 마지막으로 FINALIZED 된 회차(0=없음)
    chapters: list[ChapterRecord] = Field(default_factory=list)
    directives: list[AuthorDirective] = Field(default_factory=list)
    narrative_progress: NarrativeProgress = Field(default_factory=NarrativeProgress)   # spine 커서
    promise_ledger: PromiseLedger = Field(default_factory=PromiseLedger)   # G1: 재미 회계 장부(약속-지불 추적)
    ending_contract: EndingContract = Field(default_factory=EndingContract)   # EC-1: 엔딩 술어계약(advisory 감시 — 구 JSON 은 빈 계약으로 무변경 로드)
    bible: StoryBible = Field(default_factory=StoryBible)   # R2 설정집(편집 가능 descriptive layer)
    bible_migrated: bool = False                            # 기존 프로젝트 1회 부트스트랩 완료 표식
    worldgen_chat: list[dict] = Field(default_factory=list)  # R3 월드빌딩 대화 로그 [{role,text}]
    usage_total: dict = Field(default_factory=dict)   # 누적 LLM 사용량(비용 계측)
    skills: list[Skill] = Field(default_factory=list)   # (레거시) 인라인 스킬 — 전역 라이브러리로 1회 이관 후 비활성(skills_migrated)
    injected_skills: list[str] = Field(default_factory=list)   # 전역 라이브러리에서 이 작품에 *주입한* 스킬 id(순서=합성 우선순위; 참조형 live SSOT)
    skills_migrated: bool = False                              # 인라인 skills→전역 라이브러리 1회 이관 완료 표식

    # 동적으로 누적되는 SSOT 변경분(시드 world 와 분리 보관 → 출처 추적)
    runtime_entities: list[EntitySpec] = Field(default_factory=list)   # 신규 커밋 인물(동적/작가)
    runtime_timeline: list[TimelineEntry] = Field(default_factory=list)
    runtime_edges: list[RelationEdge] = Field(default_factory=list)    # 작가 직접 입력 관계 엣지(append-only)

    # 엔진 메모리 영속(재수화 시 LLM 재계산 회피)
    rag_chunks: list[PersistedChunk] = Field(default_factory=list)
    wiki_pages: list[WikiPage] = Field(default_factory=list)
    wiki_log: list[str] = Field(default_factory=list)

    story_passes: list[StoryPassRecord] = Field(default_factory=list)   # SY-1: 회차당 1레코드(additive — 구 JSON 무변경 로드). 승격은 작가 게이트뿐
    has_regen_backup: bool = False           # 마지막 회차 재생성 '되돌리기' 백업 존재 여부(blob 은 별도 파일 regen_backups/{id}.json)
    regen_events: list[RegenEvent] = Field(default_factory=list)   # IN-12: 재생성 실행 로그(append-only 순수 계측 — 구 JSON 은 [] 기본, 무변경 로드)
    # XR-19(cross-review/009 §9): 기계 binding 캐논의 작가 판정 원장(append-only·latest-wins 읽기 —
    #   {entity_id, attr, eff_from, decision(approve|dismiss|hold), note, at}). 판정은 기록일 뿐 값 자동 변경 0
    #   (대체는 기존 set_entity_state — supersedes 는 timeline append 이력이 담당). 구 JSON [] 기본 무변경 로드.
    tier_review: list[dict] = Field(default_factory=list)

    def chapter(self, n: int) -> Optional[ChapterRecord]:
        return next((c for c in self.chapters if c.chapter == n), None)
