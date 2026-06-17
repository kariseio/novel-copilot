# -*- coding: utf-8 -*-
"""API DTO — 요청/응답 경계. 내부 도메인 타입과 분리."""
from __future__ import annotations
from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str = ""
    genre: str = "현대 판타지"
    tone: str = ""                          # 비우면 장르에 맞게 AI 가 설정
    premise: str = ""
    protagonist_hint: str = ""
    target_chapters: int = Field(default=12, ge=1, le=200)   # ≤0·과대 차단(spine/하드캡 수식 보호)


class DirectiveRequest(BaseModel):
    text: str


class EntityRequest(BaseModel):
    name: str
    etype: str = "character"
    aliases: list[str] = []


class RelationRequest(BaseModel):
    src_id: str
    dst_id: str
    rel_id: str                       # 자유 타입 라벨(카탈로그 FK 강제 없음)
    eff_from: int = 1
    reason: str = ""
    role: str = ""
    state: str = ""                   # 관계의 질적 현재 상태("잃어버림"/"어색"; 자유)
    pov: str | None = None            # 관점 주체 id(None=객관). 설정 시 그 주체의 인식/믿음


class EndRelationRequest(BaseModel):
    src_id: str
    dst_id: str
    rel_id: str
    eff_to: int


class BibleEntryRequest(BaseModel):
    category: str = "glossary"
    title: str
    prose: str = ""


class BibleUpdateRequest(BaseModel):
    title: str | None = None
    prose: str | None = None
    category: str | None = None


class WorldgenTurnRequest(BaseModel):
    message: str


class StylePolicyRequest(BaseModel):
    """문체/생성 정책 편집(③ 작가 입력 전용) — 시스템 스티어링을 작가가 제어하는 경로."""
    system_persona: str | None = None
    author_style: str | None = None           # Layer 2 작가 문체 오버레이(빈 문자열=해제)
    target_chars_per_chapter: int | None = None
    scenes_per_chapter: int | None = None
    rules: list[str] | None = None
    ending_hook: str | None = None            # cliffhanger | soft | none
    plant_reminder: str | None = None         # off | gentle | active
    allow_state_reversal: bool | None = None


# ---- 퇴고(회차 본문 사후 다듬기) ----
class ReviseRequest(BaseModel):
    """후보 생성 — 작가 자유 지시로 회차 산문을 다듬는다(저장 안 함)."""
    directive: str                            # 작가 자유 지시(필수)
    span_text: str = ""                       # 선택적 구간 원문 부분문자열(없으면 전체 다듬기)
    passes: list[str] = []                    # ['reformat', 'fix_tense'] 부분집합만 유효(D1)


class ReviseAcceptRequest(BaseModel):
    """후보 채택 — revision_id 로 캐시 후보를 저장. 멀티워커 캐시 미스 시 폴백 필드 사용."""
    revision_id: str                          # 후보 생성 시 발급된 ID
    after_text: str | None = None             # 멀티워커 캐시 미스 폴백용(선택)
    span_text: str | None = None              # 폴백 시 span 정보
    passes: list[str] | None = None           # 폴백 시 passes


class ReviseUndoRequest(BaseModel):
    """마지막 채택 되돌리기 — body 없음(빈 POST)."""
    pass


class RevisionSummary(BaseModel):
    """GET /revisions 응답 원소(이력 요약, 읽기 전용)."""
    revision_id: str
    directive: str
    created_at: str
    reverted: bool
    guardrail_passed: bool | None = None   # None=미검사/구형 레코드(라우트가 None→True 매핑) — bool 고정이 None 거부하던 결함 해소
    span_text: str = ""
