# -*- coding: utf-8 -*-
"""관계·출처 단일 SSOT.

설계 원칙(개방형): 관계는 엔티티 사이의 '자유 결합'이다. rel_id 는 자유 타입 라벨 —
  **카탈로그 FK 강제 없음. 등록되지 않은 타입("친구","잃어버린_물건","전생_원수" 등)도 그대로 동작.**
- REL_CATALOG: '알려진' 관계 타입의 선택적 등록부 — 예쁜 라벨/시각화 스타일/선택적 제약(allowed_*types·states)용.
  여기 없는 타입은 default_spec(자유·방향·무제약)으로 자동 처리. 모든 장르/포맷을 데이터로 표현(하드코딩 배제).
- 제약(allowed_src/dst_types·cardinality·states)은 '작가가 선언할 때만' 게이팅(opt-in). 기본은 자유.
- Provenance: 모든 변경의 출처 enum 단일 정의. 직렬화 호환 보장.

이 파일은 LLM 을 호출하지 않으며 다른 도메인 모듈에 의존하지 않는다(순수 데이터 + types).
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

from .types import SignalGrade

# ---- 출처(provenance) 단일 enum ----
Provenance = Literal["seed", "author", "ai_worldgen", "ai_backfill", "machine", "human_edit"]
PROVENANCE_RANK: dict[str, int] = {   # state_as_of 동시점 충돌 시 권위 정렬(작가 > 기계)
    "human_edit": 5, "author": 4, "seed": 3, "ai_worldgen": 2, "ai_backfill": 1, "machine": 0,
}

RelationCategory = Literal[
    "kinship", "affiliation", "alliance", "hostility", "mentorship",
    "romance", "ownership", "usage", "location", "involvement", "custom",
]


class RelationSpec(BaseModel):
    """관계 타입의 '선택적' 메타데이터(데이터 주도). 시각화 스타일 + 작가가 원할 때만 거는 제약.
    모든 제약 필드는 비어 있으면 '무제한/자유'가 기본 — 강제는 작가의 선언(opt-in)."""
    rel_id: str                                    # 타입 키, 예: 'member_of'(자유 라벨도 가능)
    label: str                                     # '소속'
    category: str = "custom"                       # 자유 분류(데이터주도). RelationCategory 는 권장 어휘
    directed: bool = True
    symmetric: bool = False                        # 동맹/형제 등 무방향 대칭
    inverse_label: str = ""                        # 방향관계의 역라벨(예: 사제↔제자)
    allowed_src_types: list[str] = Field(default_factory=list)   # 비면 무제한(opt-in 제약)
    allowed_dst_types: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)   # 선언 시 RelationEdge.state 를 이 어휘로 게이팅(비면 자유)
    conflicts_with: list[str] = Field(default_factory=list)   # 본문이 이 관계를 단정할 때 캐논의 상충 관계(예: 동맹↔적대) → quasi 게이트
    mutable: bool = True                           # False=불변(혈연 등) → 변경 시 모순 escalation
    grade: SignalGrade = SignalGrade.QUASI
    cardinality: Literal["1:1", "1:N", "N:1", "N:N"] = "N:N"
    color: str = "#888888"
    line_style: Literal["solid", "dashed", "dotted"] = "solid"


def _r(**kw) -> RelationSpec:
    return RelationSpec(**kw)


def default_spec(rel_id: str, label: str = "") -> RelationSpec:
    """등록되지 않은 자유 관계 타입의 기본 스펙 — 방향·무제약·자유 상태. 라벨 없으면 rel_id 그대로."""
    return RelationSpec(rel_id=rel_id, label=label or rel_id, category="custom",
                        directed=True, symmetric=False, color="#9aa0aa", line_style="solid")


# ---- 기본 관계 카탈로그(장르 불문 공통). 작품별 추가는 WorldConfig.relations 로 ----
REL_CATALOG: dict[str, RelationSpec] = {
    s.rel_id: s for s in [
        _r(rel_id="member_of", label="소속", category="affiliation",
           directed=True, inverse_label="구성원", mutable=True,
           allowed_dst_types=["faction", "organization"], color="#5b8def", cardinality="N:1"),
        _r(rel_id="ally_of", label="동맹", category="alliance",
           directed=False, symmetric=True, mutable=True, color="#39d98a",
           conflicts_with=["enemy_of"]),
        _r(rel_id="enemy_of", label="적대", category="hostility",
           directed=False, symmetric=True, mutable=True, color="#ff6b6b",
           conflicts_with=["ally_of", "friend_of"]),
        _r(rel_id="mentor_of", label="사제", category="mentorship",
           directed=True, inverse_label="제자", mutable=True, color="#b07cff"),
        _r(rel_id="parent_of", label="부모", category="kinship",
           directed=True, inverse_label="자식", mutable=False, color="#ffb454", cardinality="1:N"),
        _r(rel_id="sibling_of", label="형제자매", category="kinship",
           directed=False, symmetric=True, mutable=False, color="#ffb454"),
        _r(rel_id="loves", label="연모", category="romance",
           directed=True, mutable=True, color="#ff8fce", line_style="dashed"),
        _r(rel_id="owns", label="소유", category="ownership",
           directed=True, inverse_label="소유됨", mutable=True,
           allowed_dst_types=["item", "artifact"], color="#c0c0c0"),
        _r(rel_id="wields", label="사용", category="usage",
           directed=True, mutable=True, color="#9bd1ff", line_style="dotted"),
        _r(rel_id="located_in", label="위치", category="location",
           directed=True, mutable=True, allowed_dst_types=["place", "location"],
           color="#8b93a7", line_style="dashed"),
        _r(rel_id="involved_in", label="관여", category="involvement",
           directed=True, mutable=True, allowed_dst_types=["event"], color="#a0a0a0"),
        # ---- 장르 중립 확장(로맨스·미스터리·사회물 등). 어디까지나 편의 — 미등록 타입도 자유 동작 ----
        _r(rel_id="friend_of", label="친구", category="alliance",
           directed=False, symmetric=True, color="#5fd0c0", conflicts_with=["enemy_of"]),
        _r(rel_id="rival_of", label="라이벌", category="hostility",
           directed=False, symmetric=True, color="#ff9f6b"),
        _r(rel_id="married_to", label="배우자", category="romance",
           directed=False, symmetric=True, cardinality="1:1", color="#ff6fa5"),     # 일부일처 배타성(opt-in 게이트)
        _r(rel_id="engaged_to", label="약혼", category="romance",
           directed=False, symmetric=True, cardinality="1:1", color="#ff8fce", line_style="dashed"),
        _r(rel_id="ex_of", label="과거연인", category="romance",
           directed=False, symmetric=True, color="#c98fb0", line_style="dashed"),
        _r(rel_id="knows", label="앎", category="custom",
           directed=True, color="#7fa8d0", line_style="dotted"),         # 관점(pov) 엣지에 흔히 사용
        _r(rel_id="believes", label="믿음", category="custom",
           directed=True, color="#9c8fd0", line_style="dotted"),         # pov=주체, 대상은 (틀릴 수 있는) 명제
        _r(rel_id="serves", label="섬김", category="affiliation",
           directed=True, inverse_label="주군", color="#8fb0d0"),
        _r(rel_id="part_of", label="일부", category="affiliation",
           directed=True, color="#9aa0aa"),                              # 조직/장소 계층 등 일반 포함
        _r(rel_id="related_to", label="관련", category="custom",
           directed=False, symmetric=True, color="#aab0ba"),             # 범용 결합(타입 미정 시)
    ]
}


def merged_catalog(extra: list[RelationSpec] | None = None) -> dict[str, RelationSpec]:
    """기본 카탈로그 + 작품별 관계(WorldConfig.relations). 동일 rel_id 는 작품 정의 우선."""
    cat = dict(REL_CATALOG)
    for r in (extra or []):
        cat[r.rel_id] = r
    return cat
