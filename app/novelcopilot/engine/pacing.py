# -*- coding: utf-8 -*-
"""연재 페이싱 텔레메트리 (G3 측정부) — 롤링 윈도 결정론 지표. LLM 0콜.

원칙: 시스템은 '측정·가시화'만, '교정'은 작가가(빨간펜). 회차 생성에 주입·강제하지 않는다.
특정 단어 분류(예: 시간경과 '없음/즉시' 키워드 매칭)는 하지 않는다 — 원시 신호(훅/장소/시간 라벨)와
단순 집계(최빈 비율·distinct 수·합)만 산출해 작가가 추세를 '보게' 한다(판단은 사람).

지표 재료는 전부 ChapterRecord 의 기록 필드(G4: hook_type/time_advance/place, ontology_changes)와 원장(G1).
C-3 재탕 피처(훅/장소 연속 run·인접 회차 본문 n-gram 재탕률·요약 유사도)도 동일 원칙 — 원시 숫자만, 판정기 금지.
아크 경계 회고·아크 카드 개정(G3 거버넌스)은 후속 — 여기는 가시화까지만.
"""
from __future__ import annotations
import re
from collections import Counter

from ..domain.types import ChapterStatus
from .ledger_ops import chapters_since_payoff


# ---- C-3: 페이싱 재탕 결정론 피처(측정 전용 — 임계·라벨·자동교정 없음) ----
# 기존 회차-내 16자 n-gram '절대 카운트'(tools A/B 일회용, 길이 교란·회차간 재탕 무감지)를
# 회차-간 신호로 개선: 같은 훅/장소 '연속' run · 인접 회차 본문 n-gram 재탕률 · 인접 요약 유사도.

_WS = re.compile(r"\s+")


def _norm_label(s: str) -> str:
    return _WS.sub(" ", (s or "").strip())


def label_max_run(labels: list[str]) -> int:
    """최장 '연속 동일 라벨' run 길이 — '같은 장소 체류·같은 훅 연속' 원시 집계(의미 분류·판정 없음).
    비교는 정규화(공백 압축) 후 전체 라벨 동일성만 — 부분일치를 안 쓰므로 교착어 substring 함정
    ('길드'⊂'길드 지하' 오탐)이 원천적으로 없다. 빈 라벨(미기록)은 run 을 끊는다(결측≠동일)."""
    best = cur = 0
    prev: str | None = None
    for lb in (_norm_label(l) for l in labels):
        if not lb:
            prev, cur = None, 0
            continue
        cur = cur + 1 if lb == prev else 1
        prev = lb
        best = max(best, cur)
    return best


def _grams(text: str, n: int) -> set:
    t = _WS.sub(" ", (text or "").strip())
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def prose_rehash(cur: str, prev: str, n: int = 16) -> float:
    """직전 회차 본문 대비 n자-gram 재탕률(0~1) — cur 의 n-gram 중 prev 에도 있는 비율(길이-불변).
    16자 '연쇄' 일치만 세므로 조사·어미 변이 잡음(교착어 substring 함정)이 끼어들 자리가 없고,
    우연 일치 확률이 극소라 높은 값 = 문장 뭉치 수준의 그대로 재탕. 측정/advisory 전용."""
    g_cur = _grams(cur, n)
    g_prev = _grams(prev, n)
    if not g_cur or not g_prev:
        return 0.0
    return len(g_cur & g_prev) / len(g_cur)


def event_echo(cur_summary: str, prev_summary: str) -> float:
    """인접 회차 한줄요약의 문자 2-gram 자카드(0~1) — '유사 사건'(같은 사건 재서술) 근사. LLM 0콜.
    단어 완전일치 대신 문자 2-gram: 조사·어미가 붙어 표면형이 갈리는 교착어('광민과의'≠'광민이')에서도
    어간 연쇄('광민')가 겹침으로 잡히고, 1음절 우연 일치는 집계에 들어가지 않는다(어간≥2 가드와 동일 원리).
    거친 근사이므로 절대값 판정 금지 — 윈도 내 추세로만 본다(판정은 작가)."""
    a = _char2(cur_summary)
    b = _char2(prev_summary)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _char2(text: str) -> set:
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


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
    # C-3 재탕 피처: run/인접쌍은 'FINALIZED 윈도 시퀀스'(recent, 회차번호 정렬) 기준 — 위 hooks/places 는
    # 결측 제거라 인접성이 왜곡되므로 별도 시퀀스 사용. 회차번호 갭은 run/인접쌍을 끊지 않는다:
    # 현행 영속 규칙(ESCALATED 비영속·rewind 꼬리절단)상 FINALIZED 는 연속이라는 불변식에 의존(비연속 영속
    # 경로가 생기면 재검토). 인접쌍 수열은 오래된→최신 순(길이 window-1). 전부 원시 숫자 — 임계·판정·자동교정 없음.
    hook_seq = [getattr(c, "hook_type", "") or "" for c in recent]
    place_seq = [getattr(c, "place", "") or "" for c in recent]
    prose_echo = [round(prose_rehash(b.text, a.text), 3) for a, b in zip(recent, recent[1:])]
    event_sim = [round(event_echo(getattr(b, "summary", "") or "", getattr(a, "summary", "") or ""), 3)
                 for a, b in zip(recent, recent[1:])]
    return {"window": len(recent),
            "hooks": hooks,                        # 원시 훅 유형 라벨(작가가 단조 여부 판단)
            "hook_monotony": hook_monotony,        # 최빈 훅 비율(집계만)
            "hook_max_run": label_max_run(hook_seq),    # 같은 훅 '연속' 최대 길이(재탕 신호)
            "places": places,                      # 원시 장소 라벨(체류 여부 판단)
            "places_distinct": len(set(places)),
            "place_max_run": label_max_run(place_seq),  # 같은 장소 '연속' 체류 최대 길이
            "times": times,                        # 원시 시간경과 라벨(정체 여부 판단 — 코드 분류 안 함)
            "prose_echo": prose_echo,              # 인접 회차 본문 16자-gram 재탕률 수열(그대로 재탕)
            "event_echo": event_sim,               # 인접 회차 요약 유사도 수열(같은 사건 재서술 근사)
            "new_names": new_names,                # 윈도 내 신규 고유명사 커밋 수(인플레 추세)
            "since_payoff": chapters_since_payoff(ledger, current_chapter)}
