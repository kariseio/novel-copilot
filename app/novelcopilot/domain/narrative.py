# -*- coding: utf-8 -*-
"""서사 구조 (R4) — 작품→아크→에피소드 계층. 엔딩을 먼저 고정하고 역순(backward)으로 설계.

원칙(이 대화에서 확정):
- 엔딩/아크/에피소드 목표는 '서사 의도'지 '결정론 사실'이 아니다 → narrative 슬롯으로 주입, ground_truth 아님.
- 플롯 단위는 1회차가 아니라 '에피소드(3~10화)'. 절정/뽕맛을 먼저 정하고 거기로 수렴.
- 복선(plants/payoffs)은 '추적'하되 '마감 강제' 안 함(슬로우번은 기법).
- spine=None 이면 기존 평면 beats 모드(하위호환).
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class EndingSpec(BaseModel):
    central_question: str = ""        # 작품이 던지는 단 하나의 질문
    ending: str = ""                  # 확정 엔딩(주인공 최종 상태)
    thematic_payoff: str = ""         # 주제적 보상


class Episode(BaseModel):
    episode_id: str
    arc_id: str
    order: int
    title: str = ""
    premise: str = ""                 # 에피소드 도입 상황
    climax: str = ""                  # 이 에피소드의 절정/뽕맛(backward 기준점)
    required_events: list[str] = Field(default_factory=list)   # 통제 태그(명시 set)
    required_cast: list[str] = Field(default_factory=list)     # 등장해야 할 인물 id
    plants: list[str] = Field(default_factory=list)            # 심는 복선(자유 라벨, 추적용)
    payoffs: list[str] = Field(default_factory=list)           # 회수하는 복선
    target_chapters: int = 4          # 이 에피소드에 배정할 회차 수(3~10)
    summary: str = ""                 # 완료 후 롤업 요약(계층 story_so_far 재료)
    done: bool = False


class Arc(BaseModel):
    arc_id: str
    order: int
    title: str = ""
    goal: str = ""                    # 아크 목표
    central_conflict: str = ""
    turning_point: str = ""
    episodes: list[Episode] = Field(default_factory=list)
    summary: str = ""                 # 완료 후 1줄 요약(상위 계층 압축)
    done: bool = False


class NarrativeSpine(BaseModel):
    ending: Optional[EndingSpec] = None
    arcs: list[Arc] = Field(default_factory=list)

    def arc(self, arc_id: str) -> Optional[Arc]:
        return next((a for a in self.arcs if a.arc_id == arc_id), None)

    def episode(self, arc_id: str, ep_id: str) -> Optional[Episode]:
        a = self.arc(arc_id)
        return next((e for e in a.episodes if e.episode_id == ep_id), None) if a else None


class NarrativeProgress(BaseModel):
    """현재 집필 커서. narrative_order(current_chapter) 위에 얹는 파생 뷰."""
    current_arc_id: Optional[str] = None
    current_episode_id: Optional[str] = None
    chapters_in_episode: int = 0
    completed: bool = False           # 모든 아크/에피소드 소진(엔딩 도달) → 완결. 무한 생성 종료 신호.
