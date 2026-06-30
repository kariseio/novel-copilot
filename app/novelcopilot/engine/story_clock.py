# -*- coding: utf-8 -*-
"""스토리 시계(WorldClock, CN-1) — 결정론 시간 누적. LLM 0콜·산수 0.

원칙(온톨로지/생성아키텍처 분석 결론): LLM 은 *자기 회차의* 시간 점프를 구조화 델타로 '라벨링'만 하고
(chapter_function/hook_type 와 동일한 자기기술), 절대 시점 누적은 *코드*가 한다 — 모델이 산수를 하면
D-2 vs D-5 류 시간선 드리프트가 난다. 누적 결과를 ground_truth 로 주입(모델은 읽기만).

null degrade: 단위 미상·flashback/parallel·파싱 실패는 메인 시계를 전진시키지 않는다(시계가 회차 확정을 막지 않음).
pacing.py 의 '시간 라벨 키워드 분류 안 함' 원칙과 충돌하지 않는다 — 여기서 분류하지 않고, 플래너가 *직접 구조화*해 준
델타만 더한다(자유텍스트 time_advance 는 손대지 않는다).
"""
from __future__ import annotations

from ..domain.types import TimeDelta

# 단위→분 환산(결정론). month/year 는 역법 무관 서사 누적용 근사(30일/365일).
UNIT_MINUTES = {
    "minute": 1.0, "hour": 60.0, "day": 1440.0,
    "week": 10080.0, "month": 43200.0, "year": 525600.0,
}


def _coerce(d) -> TimeDelta | None:
    """TimeDelta | dict(beat.model_dump 산물) | None → TimeDelta | None(파싱 실패는 None)."""
    if d is None:
        return None
    if isinstance(d, TimeDelta):
        return d
    if isinstance(d, dict):
        try:
            return TimeDelta(**{k: d[k] for k in ("amount", "unit", "mode") if k in d})
        except Exception:
            return None
    return None


def delta_minutes(d) -> float | None:
    """이 델타가 메인 시계를 전진시키는 분(分).
    flashback/parallel → 0(전진 안 함). unit 빈값/미상·unknown·음수 amount → None(미상, 누적서 건너뜀)."""
    d = _coerce(d)
    if d is None:
        return None
    if d.mode in ("flashback", "parallel"):
        return 0.0
    if d.mode == "unknown":
        return None
    if d.amount == 0:
        return 0.0   # 명시적 '경과 없음' = 알려진 0(단위 무관, 미상 아님 — '약' 헤지 유발 안 함)
    m = UNIT_MINUTES.get(d.unit)
    if m is None or d.amount < 0:
        return None
    return d.amount * m


def elapsed_minutes(deltas) -> tuple[float, bool]:
    """델타 시퀀스(시작→현재 회차 순서) 누적 분 + '미상 구간 존재' 플래그.
    미상(None)은 0으로 건너뛰되 플래그=True(→ 표시에 '약'으로 하향 정직)."""
    total, had_unknown = 0.0, False
    for d in deltas:
        m = delta_minutes(d)
        if m is None:
            had_unknown = True
            continue
        total += m
    return total, had_unknown


def format_elapsed(minutes: float, had_unknown: bool = False) -> str:
    """누적 분 → 짧은 절대시점 문자열(작가·모델용). 역법 정밀이 아니라 '서사 경과'의 결정론 근사."""
    if minutes <= 0:
        return "이야기 시작 시점(아직 유의미한 시간 경과 없음)"
    if minutes < 60:
        body = f"{int(round(minutes))}분"
    elif minutes < 1440:
        body = f"{minutes / 60:.1f}시간"
    else:
        days = minutes / 1440.0
        if days < 14:
            body = f"{days:.1f}일"
        elif days < 60:
            body = f"{int(round(days / 7))}주"
        elif days < 730:
            body = f"{int(round(days / 30))}개월"
        else:
            body = f"{days / 365:.1f}년"
    hedge = "약 " if had_unknown else ""
    return f"이야기 시작 후 {hedge}{body} 경과"


def story_time_for(deltas) -> str:
    """델타 시퀀스(시작→현재) → ground_truth 주입용 절대시점 문자열. 빈/전부 미상이면 ''(주입 생략)."""
    minutes, had_unknown = elapsed_minutes(deltas)
    if minutes <= 0 and had_unknown:
        return ""   # 아는 게 하나도 없음 → 시계 미주입(null degrade, 거짓 '시작 시점' 단정 회피)
    return format_elapsed(minutes, had_unknown)
