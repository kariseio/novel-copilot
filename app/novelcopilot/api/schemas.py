# -*- coding: utf-8 -*-
"""API DTO — 요청/응답 경계. 내부 도메인 타입과 분리."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str = ""
    genre: str = "현대 판타지"
    tone: str = ""                          # 비우면 장르에 맞게 AI 가 설정
    premise: str = ""
    protagonist_hint: str = ""
    target_chapters: int = Field(default=12, ge=1, le=200)   # ≤0·과대 차단(spine/하드캡 수식 보호)
    # DP-18: 서술 시점 명시(작가 결정 — 발명 금지, 시드에서만 지정). 기본 ""=무지정(구 요청 바이트 동일) →
    #   worldgen 이 StyleSpec 기본값(third_limited)을 상속. first 지정 시 worldgen 중 화자 보이스 도출이 발화.
    pov: Literal["", "third_limited", "third_omniscient", "first"] = ""


class DirectiveRequest(BaseModel):
    text: str


class EntityRequest(BaseModel):
    name: str
    etype: str = "character"
    aliases: list[str] = []


class EntityVoiceRequest(BaseModel):
    """인물 보이스 카드 편집(③ 작가 입력) — None=무변경(부분 수정), 빈 문자열=해제.

    voice=음성 카드(ST-12a·harness 서술자 프레임/인물 말투), voice_stages=상태 단계별 카드(VB-1 —
    key 는 style.narrator_voice_stage_attr 가 가리키는 속성의 값)."""
    voice: str | None = None
    voice_stages: dict[str, str] | None = None


class TierReviewRequest(BaseModel):
    """XR-19: 기계 binding 캐논 1건의 작가 판정 기록(approve/dismiss/hold — 값 자동 변경 0)."""
    entity_id: str
    attr: str
    eff_from: int
    decision: str
    note: str = ""


class AttributeAutoCommitRequest(BaseModel):
    """XR-5 추적 속성의 '자동 확정 기준' 작가 선언(③ 작가 입력).

    ""=미선언(현행 동작) / "binding"=장면에서 외적으로 관찰 가능한 사실 / "non_binding"=본문 서술만으로
    사실 확정이 어려운 축(작가 확정 전 비구속). 이후 커밋부터 적용 — 이미 박힌 값은 소급 변경되지 않는다."""
    auto_commit: str


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


class ProjectMetaRequest(BaseModel):
    """작품 메타 편집(작가 직접 수정) — 제목·한 줄 소개(premise)·소개(synopsis). None=무변경(부분 수정)."""
    title: str | None = None
    premise: str | None = None
    synopsis: str | None = None


class StylePolicyRequest(BaseModel):
    """문체/생성 정책 편집(③ 작가 입력 전용) — 시스템 스티어링을 작가가 제어하는 경로."""
    system_persona: str | None = None
    author_style: str | None = None           # Layer 2 작가 문체 오버레이(빈 문자열=해제)
    narrator_voice: str | None = None          # DP-17 화자 보이스(빈 문자열=해제) — first 시점에서 소비
    narrator_voice_stage_attr: str | None = None   # VB-1 상태 연동 보이스가 따라갈 추적 속성 key(빈 문자열=해제)
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


class DerivativeRecomputeRequest(BaseModel):
    """XR-7③: 퇴고로 옛 본문 기준이 된 파생물 하나를 현재 본문으로 다시 계산(작가 발동 — 자동 호출 없음).
    name: 'wiki' | 'claim_audit' | 'reader_feedback' | 'dialogue_ledger'. 'promise_ledger' 는 미지원(400+사유)."""
    name: str


# ---- 직접 편집(작가가 문장을 직접 고쳐 저장 — DE-1/DE-2) ----
class EditSpanItem(BaseModel):
    """DE-2: 다중 구간 교체의 원소 — 바뀐 대목(hunk) 하나. span_text 를 replacement 로 교체."""
    span_text: str
    replacement: str


class EditRequest(BaseModel):
    """DE-1/DE-2: 작가 직접 편집. 전체 교체(new_text)·구간 교체(span_text+replacement)·
    다중 구간 교체(edits) — 셋 중 하나만(서로 배타)."""
    new_text: str | None = None               # 전체 교체 모드(구간 필드와 배타)
    span_text: str = ""                        # 구간 교체 — 현재 본문에 정확히 1회 등장하는 원문 구간
    replacement: str | None = None             # 구간 교체문(span_text 를 이 문장으로 교체)
    edits: list[EditSpanItem] | None = None   # DE-2: 다중 구간 교체 — 다른 모드와 배타


# ---- 빨간펜(작가가 마음에 안 드는 구간 표시 + 옵트인 방향 제안 — RP-1) ----
class RedpenAddRequest(BaseModel):
    """빨간펜 표시 1건 추가 — 본문 불변(무강제). anchor_text 는 본문에 유일해야 하고, 표시 구간은 그 안의 부분범위."""
    anchor_text: str                          # 본문 유일 앵커(표시 구간 포함)
    span_start: int                            # anchor_text 내 표시 구간 시작
    span_len: int                              # 표시 구간 길이(>0)
    note: str = ""                             # 왜 마음에 안 드는지(선택)


class RedpenSuggestRequest(BaseModel):
    """옵트인 방향 제안 — mark_ids 지정 시 그 마크들만, None 이면 전체(유효 마크만) 대상."""
    mark_ids: list[str] | None = None


# ---- EP-PUB: 외부 플랫폼 수동 발행 표시(툴은 업로드하지 않음 — 발행 사실+지문만 기록) ----
class PublishRequest(BaseModel):
    """발행/재발행 표시 — body 없어도 됨(빈 POST). note=어디에 올렸는지 자유 메모(선택):
    None=기존 메모 유지(재발행 편의), 문자열이면 덮어씀(""=지움)."""
    note: str | None = None


class RevisionSummary(BaseModel):
    """GET /revisions 응답 원소(이력 요약, 읽기 전용)."""
    revision_id: str
    directive: str
    created_at: str
    reverted: bool
    guardrail_passed: bool | None = None   # None=미검사/구형 레코드(라우트가 None→True 매핑) — bool 고정이 None 거부하던 결함 해소
    span_text: str = ""


class StoryPassRunRequest(BaseModel):
    """SY-1: 깔때기 실행 요청 — chapter 미지정이면 다음 화, pillar 미지정이면 결정론 선정.
    SP-2: role 은 작가 오버라이드(위기/꼼수/회수/조사/관계/휴지) — 미지정이면 배정기가 정한다."""
    chapter: int | None = None
    pillar: str | None = None
    role: str | None = None


class StoryConfirmRequest(BaseModel):
    """SY-1 작가 게이트 — 작가가 보낸 텍스트를 확정(후보 그대로도, 편집분도 가능)."""
    story: str
    source: str = "solo"          # "solo" | "merged" | "edited"
