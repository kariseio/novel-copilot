# -*- coding: utf-8 -*-
"""스토리 바이블 (R2) — 작가의 '설정집'. 산문(prose) 자유서술 + '캐논으로 박기' 단일 게이트.

라우팅(이 프로젝트에서 확정):
- 구조적·검증가능 사실(관계·상태·속성) → 온톨로지(타입드 캐논). 1회성 소품·산문 디테일 → 여기(narrative).
- bible 항목은 기본 'narrative 참조'다. 작가가 promote 해야만(승인 게이트) world_rule 로 승격 → 비대칭 보존.
  단, 세계규칙 위반은 SEMANTIC(의미층) 신호 — 하드 게이트(자동 재작성/ESCALATED)가 아니라 추적·프롬프트 주입(advisory).
  위반 시 실제로 막는 하드 캐논은 관계 엣지·상태(det/quasi)뿐(types.py: det/quasi 만 binding).
- 카테고리/장르 템플릿은 '데이터'(하드코딩 분기 금지). 장르마다 어떤 섹션이 중요한지는 GENRE_TEMPLATES row.
MVP: promote_target 은 world_rule 만(가장 깔끔히 검증가능). 속성/타임라인/관계 승격은 후속.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

from .relations import Provenance

# 데이터 주도 카테고리(조사 기반). 새 카테고리 = 여기 추가(코드 분기 없음).
BibleCategory = Literal[
    "magic_system", "ability_system", "bestiary", "race", "geography",
    "faction_politics", "chronology", "artifact", "character",
    "culture_religion", "power_system", "taboo_worldrule", "glossary",
]

CATEGORY_LABEL: dict[str, str] = {
    "magic_system": "마법 체계", "ability_system": "능력 체계", "bestiary": "몬스터 도감",
    "race": "종족", "geography": "지리·세계", "faction_politics": "세력·정치",
    "chronology": "연표·역사", "artifact": "아이템·아티팩트", "character": "인물",
    "culture_religion": "문화·종교", "power_system": "권능·파워 시스템",
    "taboo_worldrule": "금기·세계규칙", "glossary": "용어집",
    # 장르 중립 확장(로맨스·미스터리·회빙환 등). 미등록 카테고리는 키를 그대로 라벨로(개방형).
    "relationship": "관계·인물관계", "secret_identity": "비밀·정체", "tech_system": "기술 체계",
    "clue_investigation": "단서·수사", "knowledge_state": "지식·정보", "emotion_arc": "감정선",
}

# 장르별 권장 섹션(worldgen 가이드 + UI 그룹핑). 데이터 — 미정의 장르는 DEFAULT. 카테고리는 자유(개방형).
GENRE_TEMPLATES: dict[str, list[str]] = {
    "로맨스 판타지": ["relationship", "secret_identity", "emotion_arc", "faction_politics", "race",
                 "magic_system", "geography", "character", "glossary"],
    "로판": ["relationship", "secret_identity", "emotion_arc", "faction_politics", "geography", "character", "glossary"],
    "로맨스": ["relationship", "emotion_arc", "secret_identity", "character", "geography", "glossary"],
    "현대 판타지": ["ability_system", "faction_politics", "geography", "taboo_worldrule",
                "power_system", "character", "glossary"],
    "정통 판타지": ["magic_system", "bestiary", "race", "geography", "faction_politics",
                "chronology", "artifact", "culture_religion", "taboo_worldrule", "glossary"],
    "무협": ["power_system", "faction_politics", "geography", "chronology", "artifact",
           "taboo_worldrule", "glossary", "character"],
    "미스터리": ["clue_investigation", "knowledge_state", "character", "chronology", "geography", "glossary"],
    "추리": ["clue_investigation", "knowledge_state", "character", "chronology", "geography", "glossary"],
    "스릴러": ["clue_investigation", "knowledge_state", "character", "faction_politics", "geography", "glossary"],
    "SF": ["tech_system", "faction_politics", "geography", "chronology", "race",
          "culture_religion", "taboo_worldrule", "glossary"],
    "회귀": ["chronology", "secret_identity", "knowledge_state", "faction_politics", "ability_system", "character", "glossary"],
    "빙의": ["secret_identity", "relationship", "faction_politics", "character", "geography", "glossary"],
    "환생": ["secret_identity", "race", "magic_system", "chronology", "character", "glossary"],
}
DEFAULT_TEMPLATE = ["character", "relationship", "faction_politics", "geography", "chronology", "glossary"]


def template_for(genre: str) -> list[str]:
    for key, cats in GENRE_TEMPLATES.items():
        if key in (genre or "") or (genre or "") in key:
            return cats
    return DEFAULT_TEMPLATE


def normalize_category(c: str | None) -> str:
    """자유 카테고리 허용(개방형) — 비면 glossary. 미등록 키는 그대로 보존(CATEGORY_LABEL.get 이 키를 라벨로 폴백).
    모든 장르/포맷의 임의 카테고리("관계","단서","연표" 등)를 강제로 glossary 로 뭉개지 않는다."""
    c = (c or "").strip()
    return c or "glossary"


class BibleEntry(BaseModel):
    entry_id: str
    category: str                              # BibleCategory(데이터주도라 str)
    title: str
    prose: str = ""                            # 자유 서술(narrative)
    keywords: list[str] = Field(default_factory=list)   # 로어북식 주입 트리거(비면 카테고리/제목 기반)
    promoted: bool = False                     # 단일 게이트: 캐논(world_rule)으로 박혔는가
    promote_target: Literal["none", "world_rule"] = "none"
    world_rule_id: str = ""                    # promote 시 생성된 world_rule id(삭제 시 역연산용)
    provenance: Provenance = "author"
    status: Literal["author_approved", "ai_unreviewed", "draft", "deprecated"] = "ai_unreviewed"


class StoryBible(BaseModel):
    entries: list[BibleEntry] = Field(default_factory=list)

    def get(self, entry_id: str) -> BibleEntry | None:
        return next((e for e in self.entries if e.entry_id == entry_id), None)
