# -*- coding: utf-8 -*-
"""계획 하네스 — 비트(설계 산출물)의 결정론 lint (LLM 0콜, 임베딩 1콜 선택).

본문 검증만 있고 계획은 무검증 통과하던 갭을 닫는다: 계획 결함(죽은 인물 배정·무효 id silent drop·
비트 재탕)이 본문으로 흘러내려와 산문 게이트가 뒷수습하던 구조 — 계획 단계에서 잡으면 draft 콜이 통째로 절약된다.
검증은 본문과 같은 비대칭: 캐논(온톨로지) 대조는 binding, 재미·전개 품질은 비구속(여기서 다루지 않음).
"""
from __future__ import annotations

from ..domain.types import Violation, SignalGrade


def lint_beat(beat: dict, ontology, ch_no: int) -> list[Violation]:
    """비트 1개의 결정론 lint — 캐논 정합(엔티티 명부·생애주기)만. 위반은 재계획 1회의 입력이 된다(조용한 드롭 금지).

    재미·페이싱(훅 유형·시간 경과·장소)은 여기서 다루지 않는다 — 그건 '안 어긋남'(정합)이 아니라 '작법'이라
    시스템이 교정 지시로 강제하지 않고, 측정값을 작가에게 가시화만 한다(작가가 빨간펜으로 조향). 비대칭 보존.
    """
    viols: list[Violation] = []
    valid = set(ontology.entities.keys())
    for eid in beat.get("entities") or []:
        if eid not in valid:
            # 기존엔 무효 id 를 조용히 필터(silent drop) → 비트 의도가 소리 없이 증발(콜드 드롭의 숨은 원인)
            viols.append(Violation(entity=str(eid), kind="plan_unknown_entity",
                                   grade=SignalGrade.DETERMINISTIC, canon="엔티티 명부",
                                   text=f"비트가 명부에 없는 id '{eid}' 배정",
                                   evidence="계획 lint: id 정합"))
        elif ontology._in_terminal_state(eid, ch_no):
            # 제거 상태 인물 배정 — 본문 생성 '후'에야 잡히던 위반을 설계 단계로 전진(draft 콜 절약)
            viols.append(Violation(entity=ontology.name(eid), kind="plan_dead_cast",
                                   grade=SignalGrade.DETERMINISTIC, canon=f"{ch_no}화 시점 제거 상태",
                                   text=f"제거 상태 인물 '{ontology.name(eid)}'을 비트에 배정",
                                   evidence="계획 lint: 생애주기"))
    return viols


def beat_repeat_score(provider, summary: str, prev_summaries: list[str]) -> float:
    """비트 요약의 직전 비트들 대비 의미 유사도 최대값 — 재탕('같은 절벽 3번')을 설계 단계에서 차단.
    hook_repeat_semantic 과 동일 메커니즘(임베딩 코사인), 대상만 계획."""
    from .quality_gates import hook_repeat_semantic
    return hook_repeat_semantic(provider, summary, prev_summaries)
