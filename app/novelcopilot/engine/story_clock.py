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

from ..domain.types import TimeDelta, OntologyFact

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


def _span_body(minutes: float) -> str:
    """양수 분(分) → 짧은 서사 근사 body('45분'/'6.1일'/'2주'/'3.0년'). 역법 정밀 아님(결정론 근사).
    format_elapsed(누적 경과)·anchor_facts(기한 잔여) 공용 — 단일 출처(경계값 일치 보장)."""
    if minutes < 60:
        return f"{int(round(minutes))}분"
    if minutes < 1440:
        return f"{minutes / 60:.1f}시간"
    days = minutes / 1440.0
    if days < 14:
        return f"{days:.1f}일"
    if days < 60:
        return f"{int(round(days / 7))}주"
    if days < 730:
        return f"{int(round(days / 30))}개월"
    return f"{days / 365:.1f}년"


def format_elapsed(minutes: float, had_unknown: bool = False) -> str:
    """누적 분 → 짧은 절대시점 문자열(작가·모델용). 역법 정밀이 아니라 '서사 경과'의 결정론 근사."""
    if minutes <= 0:
        return "이야기 시작 시점(아직 유의미한 시간 경과 없음)"
    hedge = "약 " if had_unknown else ""
    return f"이야기 시작 후 {hedge}{_span_body(minutes)} 경과"


def anchor_facts(anchors, deltas) -> list[OntologyFact]:
    """시간 앵커(계약 만기·인물 나이 류) → 현재 클록 위치 기준 결정론 파생값 고신뢰 팩트 행(B-33). LLM 산수 0.

    CN-1 story_time·CN-5 대응표와 같은 패턴: 세계가 '시간 기준점'을 선언하면 코드가 잔여/현재나이를
    계산해 [확정 설정] 블록(ground_truth)에 박는다 — 모델은 읽기만(파생 산수 발명 금지). 앵커 미선언 → []
    (주입 생략·바이트 동일 하위호환). 미상 단위/음수 앵커 → 그 행만 건너뜀(null degrade, story_time 와 동일 철학).
    counter류(목격·전학 횟수)는 결정론 추적 불가라 대상이 아니다(정직 범위는 T5 등록부가 담당).

    kind=deadline: amount·unit 만큼 '이야기 시작' 뒤 도래하는 기한(예 3년 계약) → 현재 잔여를 주입.
    kind=age: amount=이야기 시작 시점 나이(년) → 경과 연수(내림)를 더한 현재 나이를 주입."""
    anchors = list(anchors or [])
    if not anchors:
        return []
    minutes, had_unknown = elapsed_minutes(deltas)
    hedge = "약 " if had_unknown else ""
    # B-36(B-33 이월노트②·억제 비대칭 해소): story_time_for 는 '전부 미상(경과 0·미상 존재)'이면 억제(빈 문자열)해
    #   거짓 시작단정을 피하는데, anchor_facts 는 같은 상황에서 '이야기 시작 시점'을 단정해 비대칭이었다.
    #   전부 미상이면 위치를 '경과 시간 미상'으로 정직 하향(경과 0=known 일 때만 '시작 시점' 단정).
    if minutes > 0:
        now = format_elapsed(minutes, had_unknown)
    elif had_unknown:
        now = "경과 시간 미상"
    else:
        now = "이야기 시작 시점"
    facts: list[OntologyFact] = []
    for a in anchors:
        try:
            amt = float(getattr(a, "amount", 0) or 0)
        except (TypeError, ValueError):
            continue
        label = (getattr(a, "label", "") or "").strip() or (getattr(a, "anchor_id", "") or "시간 기준")
        kind = (getattr(a, "kind", "deadline") or "deadline")
        if kind == "age":
            if amt < 0:
                continue
            start_age = int(round(amt))
            years_passed = int(minutes // UNIT_MINUTES["year"])   # 결정론 내림: 경과<1년이면 0(나이 동결 — 15→16 드리프트 차단)
            age_now = start_age + years_passed
            # B-36(B-33 이월노트①·미래 age 이중표면 해소): 진행된 현재나이가 정지 설정(시작 나이 N세)과 병존하며
            #   '15세 vs 17세' 모순처럼 읽히던 위험 — 진행 시 시작 나이를 명시해 관계를 '진행'으로 못박는다(모순 아님).
            #   동결(경과<1년) 시엔 '나이 변동 없음'(15→16 드리프트 차단 문구 유지).
            detail = (f"시작 시 {start_age}세 → {now}" if years_passed else f"{now} — 나이 변동 없음")
            facts.append(OntologyFact(entity=label, attr_label="현재 나이",
                                      value=f"{hedge}{age_now}세({detail})"))
        else:  # deadline
            unit_m = UNIT_MINUTES.get((getattr(a, "unit", "year") or "year").lower())
            if unit_m is None or amt < 0:
                continue   # 미상 단위 앵커 → null degrade(거짓 잔여 단정 회피)
            residual = amt * unit_m - minutes
            if residual > 0:
                val = f"{hedge}{_span_body(residual)} 남음({now})"
            else:
                val = f"기한 도래·경과({now} ≥ 예정 {_span_body(amt * unit_m)})"
            facts.append(OntologyFact(entity=label, attr_label="기한까지", value=val))
    return facts


def story_time_for(deltas) -> str:
    """델타 시퀀스(시작→현재) → ground_truth 주입용 절대시점 문자열. 빈/전부 미상이면 ''(주입 생략)."""
    minutes, had_unknown = elapsed_minutes(deltas)
    if minutes <= 0 and had_unknown:
        return ""   # 아는 게 하나도 없음 → 시계 미주입(null degrade, 거짓 '시작 시점' 단정 회피)
    return format_elapsed(minutes, had_unknown)
