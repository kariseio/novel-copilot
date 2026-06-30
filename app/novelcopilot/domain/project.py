# -*- coding: utf-8 -*-
"""프로젝트 영속 상태 — Repository 가 직렬화/역직렬화하는 단일 집계 루트(aggregate)."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field

from .world import WorldConfig, TimelineEntry, EntitySpec
from .types import AuthorDirective, WikiPage, ChapterRecord, RelationEdge
from .narrative import NarrativeProgress
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


class PersistedChunk(BaseModel):
    chapter: int
    version: int
    text: str
    emb: list[float] = Field(default_factory=list)


class ProjectState(BaseModel):
    """프로젝트 전체 상태. 엔진 세션은 이로부터 재수화(rehydrate)된다."""
    id: str
    seed: ProjectSeed
    world: WorldConfig
    created_at: str = ""

    # 진행 상태
    current_chapter: int = 0                 # 마지막으로 FINALIZED 된 회차(0=없음)
    chapters: list[ChapterRecord] = Field(default_factory=list)
    directives: list[AuthorDirective] = Field(default_factory=list)
    narrative_progress: NarrativeProgress = Field(default_factory=NarrativeProgress)   # spine 커서
    promise_ledger: PromiseLedger = Field(default_factory=PromiseLedger)   # G1: 재미 회계 장부(약속-지불 추적)
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

    def chapter(self, n: int) -> Optional[ChapterRecord]:
        return next((c for c in self.chapters if c.chapter == n), None)
