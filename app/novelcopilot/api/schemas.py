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
    target_chars_per_chapter: int | None = None
    scenes_per_chapter: int | None = None
    rules: list[str] | None = None
    ending_hook: str | None = None            # cliffhanger | soft | none
    plant_reminder: str | None = None         # off | gentle | active
    allow_state_reversal: bool | None = None
