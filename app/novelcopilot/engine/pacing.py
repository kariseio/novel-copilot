# -*- coding: utf-8 -*-
"""연재 페이싱 텔레메트리 (G3 측정부) — 롤링 윈도 결정론 지표. LLM 0콜.

원칙: 시스템은 '측정·가시화'만, '교정'은 작가가(빨간펜). 회차 생성에 주입·강제하지 않는다.
특정 단어 분류(예: 시간경과 '없음/즉시' 키워드 매칭)는 하지 않는다 — 원시 신호(훅/장소/시간 라벨)와
단순 집계(최빈 비율·distinct 수·합)만 산출해 작가가 추세를 '보게' 한다(판단은 사람).

지표 재료는 전부 ChapterRecord 의 기록 필드(G4: hook_type/time_advance/place, ontology_changes)와 원장(G1).
아크 경계 회고·아크 카드 개정(G3 거버넌스)은 후속 — 여기는 가시화까지만.
"""
from __future__ import annotations
from collections import Counter

from ..domain.types import ChapterStatus
from .ledger_ops import chapters_since_payoff


def pacing_window(chapters, ledger, current_chapter: int, window: int = 5) -> dict:
    """최근 window 회차의 페이싱 신호(측정). 분류·판정 없이 원시 신호+단순 집계만 반환(작가 가시화용)."""
    fin = [c for c in chapters if getattr(c, "status", None) == ChapterStatus.FINALIZED]
    recent = sorted(fin, key=lambda c: c.chapter)[-window:]
    hooks = [c.hook_type for c in recent if getattr(c, "hook_type", "")]
    places = [c.place for c in recent if getattr(c, "place", "")]
    times = [c.time_advance for c in recent if getattr(c, "time_advance", "")]
    # 훅 단조도 = 최빈 훅 유형의 비율(1.0=윈도 전부 동형). 키워드 매칭 아님 — 라벨 빈도만.
    hook_monotony = round(Counter(hooks).most_common(1)[0][1] / len(hooks), 2) if hooks else 0.0
    new_names = sum(len([ch for ch in (getattr(c, "ontology_changes", None) or [])
                         if getattr(ch, "op", "") == "new_entity"]) for c in recent)
    return {"window": len(recent),
            "hooks": hooks,                        # 원시 훅 유형 라벨(작가가 단조 여부 판단)
            "hook_monotony": hook_monotony,        # 최빈 훅 비율(집계만)
            "places": places,                      # 원시 장소 라벨(체류 여부 판단)
            "places_distinct": len(set(places)),
            "times": times,                        # 원시 시간경과 라벨(정체 여부 판단 — 코드 분류 안 함)
            "new_names": new_names,                # 윈도 내 신규 고유명사 커밋 수(인플레 추세)
            "since_payoff": chapters_since_payoff(ledger, current_chapter)}
