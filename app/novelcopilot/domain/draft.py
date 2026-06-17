# -*- coding: utf-8 -*-
"""세계관 컨셉 드래프트 — '생성 전' 대화로 누적되는 작품 설계서.

컨셉 전환: '한 줄 → 즉시 생성'이 아니라, 작가와 AI가 대화하며 ConceptBrief 를 점진적으로 채운다.
브리프가 충분히 무르익으면 '세계 생성'으로 기존 worldgen 파이프라인에 풍부한 시드로 투입(품질↑).
드래프트는 생성 전 단계라 휘발(in-memory) — finalize 시 ProjectState 로 승격되어 영속.
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class BriefCharacter(BaseModel):
    name: str = ""
    role: str = ""          # 주인공/라이벌/조력자/적대 등
    want: str = ""          # 욕망·동기·두려움


class ConceptBrief(BaseModel):
    """대화로 누적되는 작품 설계서 — 매 턴 LLM 이 갱신, 우측 패널에 실시간 표시."""
    title: str = ""
    genre: str = ""
    tone: str = ""
    logline: str = ""                                   # 한 문장 핵심(가장 중요)
    premise: str = ""                                   # 전제 2~4문장
    setting: str = ""                                   # 세계·배경
    characters: list[BriefCharacter] = Field(default_factory=list)
    world_rules: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    target_chapters: int = 200                          # 웹소설 기본=장편 연재(상한 아님, 작가가 조정)

    def completeness(self) -> int:
        """0~100 — 생성 준비도(채워진 축의 가중 합). 버튼 활성·진척 표시용."""
        score = 0
        score += 18 if self.logline else 0
        score += 14 if self.premise else 0
        score += 12 if self.setting else 0
        score += min(24, len(self.characters) * 12)     # 인물 2명이면 만점
        score += min(14, len(self.world_rules) * 7)
        score += min(12, len(self.conflicts) * 6)
        score += min(8, len(self.themes) * 4)           # 주제도 준비도 축(누락분 보강 — 디테일 추가가 미터에 반영되게)
        score += 6 if self.genre else 0
        return min(100, score)


class WorldDraft(BaseModel):
    id: str
    brief: ConceptBrief = Field(default_factory=ConceptBrief)
    chat: list[dict] = Field(default_factory=list)      # [{role:'author'|'ai', text:str}]
    open_questions: list[str] = Field(default_factory=list)
    locks: dict = Field(default_factory=dict)           # 작가가 컨트롤로 직접 정한 파라미터(장르·분위기·회차) — AI 갱신보다 우선
    created_at: str = ""
    last_touched: float = 0.0                           # epoch — TTL GC 기준(휘발)
