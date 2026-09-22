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


class SlotPlan(BaseModel):
    """RC-1: 에피소드 사건 풀을 회차 슬롯(1..target_chapters) 몫으로 나눈 한 회차 배분(chapter_budget).

    긍정형 서사기능 태그(function — chapter_function 어휘 재사용 setup/escalation/payoff/relation/respite,
    신규 기피어 0·genre-blind) + 중심 사건 1(central) + 보조 사건 n(support). 절정은 마지막 슬롯에 수렴.
    전 필드 default → 구 JSON 무변경 로드(하위호환). 미분배(빈 slots)면 통짜 메뉴 경로 그대로."""
    slot: int = 0                     # 화차(1-based). 에피소드 내 순번
    function: str = ""                # 서사기능 태그(긍정형·genre-blind). 역할 파생·가시화 입력
    central: str = ""                 # 이 회차의 중심 사건 하나(slot_event/planned_event 소생 소스)
    support: list[str] = Field(default_factory=list)   # 보조 사건 n(스토리패스 재료 스코핑 소스)


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
    payoff_at: str = ""               # DP-3' ⓐ: 이 에피소드 payoff 를 터뜨릴 위치(early|mid|climax, 자유 라벨·advisory). 빈 값=미지정(구 레코드 하위호환)
    new_rule: str = ""                # SP-2 M9: 이 에피소드가 새로 세우는 규칙 한 줄(에피소드당 1 — 규칙 예산 원장·advisory). 빈 값=신규 규칙 없음(구 레코드 하위호환)
    target_chapters: int = 4          # 이 에피소드에 배정할 회차 수(3~10)
    summary: str = ""                 # 완료 후 롤업 요약(계층 story_so_far 재료)
    done: bool = False
    event_menu: list[str] = Field(default_factory=list)   # T3 적시 사건 메뉴: 활성 시 신선 생성한 후보 풀(advisory).
    #   required_events(구속 뼈대, T2 점검)와 별개 — 비트가 끌어쓰는 재료지 지시 아님. T2는 이 필드를 읽지 않는다.
    slots: list[SlotPlan] = Field(default_factory=list)   # RC-1: 회차 예산 분배(chapter_budget). 빈 리스트=미분배
    #   (구 JSON·플래그 OFF·분배 실패)=통짜 메뉴 경로 바이트 동일. slot_event/planned_event·재료 스코핑·역할·줄수의 소스.


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


# ---- EC-1: 엔딩 술어계약(감시 레이어 영속 모델) ----
# 전 필드 default → 구 JSON 무변경 로드(하위호환). 계약은 advisory 데이터일 뿐 어떤 게이트에도 연결되지 않는다.
class ContractPredicate(BaseModel):
    """컴파일된 엔딩 술어 1건 — 화이트리스트 타입(attr_equals/attr_in/edge_active/edge_absent/
    terminal/alive/promise_paid/clock_at_least)만 존재. 등록부 검증을 통과한 정규화 형태로 영속."""
    type: str = ""
    eid: str = ""                     # attr_*/terminal/alive 대상 엔티티 id(등록부 해소 완료)
    attr: str = ""
    value: str = ""                   # attr_equals
    values: list[str] = Field(default_factory=list)   # attr_in
    src: str = ""                     # edge_*
    dst: str = ""
    rel_id: str = ""
    promise_id: str = ""              # promise_paid
    amount: float = 0.0               # clock_at_least
    unit: str = ""
    note: str = ""                    # 근거가 된 엔딩 서술 구절 요약(작가 가시화)


class UnexpressedEnding(BaseModel):
    """LR-1 arm3: 컴파일에서 거부된 엔딩 개념(미등록 eid/attr/값 등) — 버리지 않고 '미표현 엔딩 요소'로
    영속·surface. 등록부 승격(인물/속성/관계 등록) 유도 신호일 뿐 발명 금지는 유지(자동 등록 없음)."""
    raw: dict = Field(default_factory=dict)   # LLM 제안 원문(등록부 밖 참조 포함 — 참고용)
    reason: str = ""                          # 거부 사유(unregistered_eid:… 등)


class EndingContract(BaseModel):
    """EC-1 엔딩 술어계약 — '엔딩이 지면에 도달했는지 시스템이 모른다'를 LLM 0콜 상시 감시로 소스차단.

    한계 명시(LR-1 arm2): 술어 satisfied 는 SSOT '상태 기준' 충족이지 지면(프로즈) 실현 보장이 아니다 —
    표시 문구도 상태 기준임을 밝힌다(과잉 신뢰 방지). 평가는 gt/ni 양 tier 병렬(arm1 — 단일 판정 금지).
    무강제: 컴파일 실패=조용히 스킵+이벤트, 미정산=advisory 표시만(완결 차단·자동 에필로그 0)."""
    predicates: list[ContractPredicate] = Field(default_factory=list)
    unexpressed: list[UnexpressedEnding] = Field(default_factory=list)
    ending_fingerprint: str = ""      # 컴파일 당시 EndingSpec 지문 — 현재 지문과 불일치=stale(개정 후 미갱신)
    compiled_at: str = ""             # 서버 time.strftime
    compiled_chapter: int = 0         # 컴파일 시점 커서(0=작품 생성 시)
    source: str = ""                  # "worldgen"(생성 완료 시) | "revise_spine"(엔딩 개정 시)
    llm_calls: int = 0
    error: str = ""                   # 컴파일 실패 사유(생성 차단 금지 — 이벤트+표시)
    settlement: dict = Field(default_factory=dict)   # 완결(아크 소진 등) 시점 정산 스냅샷(advisory — 표시용)
