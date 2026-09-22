# -*- coding: utf-8 -*-
"""DP-3' ⓑ: 정산 리듬 계획 산술 — 무정산 연속 상한 K (LLM 0콜·신규 상태 0·결정론).

진단(design-b37-dp3.md §DP-3'): DP-1 4.0 의 치명(3화 내 사이다 0)·DP-4b 7~8화 무정산 —
에피소드 분해가 payoffs 를 형식 필드로만 갖고 회차 배분에 '정산 위치'를 지정하지 않았다.
런타임 신호 소비(SG-3)와 달리 여기는 **분해/비트 계획 시점의 산술**(중복 없음).

이 모듈은 계획 레이어 산술 하나를 한다:
  · settlement_gap: 최근 회차 + 방금 계획한 이번 회차의 chapter_function(G4 자기 라벨)에서
    '연속 무정산(payoff 아님) run'을 결정론으로 센다. run 이 K(기본 3) 이상이면 재계획 신호
    (DP-13 재계획 경로와 동일 Violation 입력으로 승격 — 새 경로·새 게이트 발명 없음).

원칙:
  · 계획 레이어 산술(무강제·무충돌 — 계획은 시스템 몫). 프로즈 게이트 0, "사이다를 넣어라" 강제 아님(배치 지시).
  · DP-6 소비 원장 계보: chapter_function=payoff 자기 라벨로 소진 추적(structure_history 가 회차별로 제공).
  · genre-blind — 무엇이 payoff 인지(강도·종류)는 작품 시드가 정하고, 여기선 '자기 라벨된 payoff'만 센다.
  · DP-13 HIGH 교훈 정합: 이 산술은 '재계획 1회 신호'일 뿐 어떤 재료(required·climax·만기약속·payoffs)도
    죽이지 않는다 — 재계획은 payoff 를 '보태라'는 긍정 신호이고, 실현은 여전히 LLM·후속 게이트 몫이다.
  · 하위호환: 라벨 결측 회차는 run 을 끊지 않되(구 레코드 = 무판정) 정산으로도 치지 않는다(보수적: 정산 근거 없음).
"""
from __future__ import annotations

# 정산으로 인정하는 chapter_function 자기 라벨(정규화 후 완전일치). beat_for_episode 프롬프트의
# chapter_function 선택지(payoff/setup/escalation/relation/respite)와 단일 출처 — payoff 만 정산.
_PAYOFF_LABEL = "payoff"


def _is_payoff(label) -> bool:
    """chapter_function 라벨이 '정산(payoff)'인가 — 대소문자·공백 정규화 후 완전일치(부분일치 금지: 오탐 차단)."""
    return isinstance(label, str) and label.strip().lower() == _PAYOFF_LABEL


def unsettled_run(prev_functions: list[str], this_function: str) -> int:
    """최근 회차 chapter_function 들(prev_functions, 시간 오름차순) 뒤에 이번 회차 라벨(this_function)을
    이어 붙였을 때, '끝에서부터 연속되는 무정산(payoff 아님) 회차 수'를 센다.

    · payoff 라벨을 만나면 run 이 거기서 끊긴다(정산이 리듬을 리셋).
    · 라벨 결측('' — 구 레코드/미기술)은 정산 근거가 없으므로 run 에 포함(무정산으로 셈, 보수적).
    · prev_functions 는 '같은 에피소드 범위'로 호출자가 좁혀 전달(에피소드 경계에서 리듬 리셋 — B-31/커서 정합).

    반환: 이번 회차까지의 연속 무정산 회차 수(이번 회차가 payoff 면 0).
    """
    if _is_payoff(this_function):
        return 0                                  # 이번 회차가 정산 → 연속 무정산 0(리듬 리셋)
    run = 1                                        # 이번 회차(무정산)부터 카운트
    for fn in reversed(prev_functions or []):
        if _is_payoff(fn):
            break                                 # 과거 정산 회차에서 run 종료
        run += 1
    return run


def settlement_gap(prev_functions: list[str], this_function: str, k: int = 3) -> int | None:
    """무정산 연속이 상한 K 에 '도달'했는지 판정한다(계획 산술 — DP-13 재계획 1회의 입력 근거).

    prev_functions: 이번 회차 직전까지의 최근 회차 chapter_function(같은 에피소드로 좁힘·시간 오름차순).
    this_function: 방금 계획한 이번 회차의 chapter_function 자기 라벨.
    k: 무정산 연속 상한(기본 3 — design). k<=1 은 사실상 상시 재계획이라 무의미 → None(no-op, 안전).

    반환: run>=K 이면 그 run 길이(재계획 트리거), 아니면 None(정상 리듬 — 무동작).
    이번 회차가 payoff 면 run=0 이라 항상 None(정산 회차는 트리거 안 함).
    """
    try:
        k = int(k)
    except (TypeError, ValueError):
        return None
    if k <= 1:
        return None                               # 상한 1 이하 = 매 무정산 회차 재계획 → 무강제 원칙 위반, 비활성
    run = unsettled_run(prev_functions, this_function)
    return run if run >= k else None
