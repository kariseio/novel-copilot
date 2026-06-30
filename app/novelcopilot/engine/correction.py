# -*- coding: utf-8 -*-
"""교정 안전 게이트(결정론·LLM 0콜) — '생성→검증→실패분류→라우팅'의 분류·게이트 축.

설계 골자(전체 아키텍처 차원, 두더지잡기 아님 — 토큰/사전 검출 0, 일반 구조 신호만):
- **절단(max_tokens)**: 좋은 본문 + '잘린 꼬리'다. 전량 폐기·재생성은 좋은 본문을 버리는 *본문파괴*(R2 위반)이자
  더 나쁜(빈/짧은) 재추첨으로 갈아치울 위험 → **꼬리만 trim**(마지막 종결 이후 미완성 조각 제거)해 본문을 보존한다.
- **빈 응답**: 보존할 게 없는 *유일한* 전역 파손 → **재생성**(harness, bounded). 재생성=원인을 소스에서 다시 만듦.
- **국소 위반**(설정 모순 등): 좋은 본문 보존. 단 전량 재작성 채널(_rewrite)은 좋은 본문까지 갈아엎을 수 있어
  **diff 한도 게이트**로 가둔다(R2 교훈을 코드 게이트로 — git diff 처럼 변경 비율 검사).
  (패치형 _continuity_polish/_fix_tense·계약검증형 _reformat 는 본문 보존이 이미 *구조적* — 게이트 불필요.)
"""
from __future__ import annotations
import difflib

# 문장 종결 신호 — 종결부호 + '닫는' 인용만. *여는* 따옴표(“)나 양용 직선(" ')을 넣으면, 대사 오프너
# 직후에서 절단됐을 때 trim 이 그 여는따옴표까지 보존해 '고아 여는따옴표'를 만든다(적대검증). 닫힘만 종결로 인정.
_SENT_END = (".", "!", "?", "…", "”", "’", "」", "』")


def trim_dangling(text: str) -> str:
    """절단된 본문의 '잘린 꼬리'(마지막 종결 이후 미완성 조각)만 제거 — 좋은 본문은 보존한다.
    이미 종결로 끝나면 그대로. 종결부호가 아예 없으면(통째 한 조각) 그대로 둔다(상위가 빈/짧음으로 판단)."""
    t = (text or "").rstrip()
    if not t or t.endswith(_SENT_END):
        return text
    idx = max((t.rfind(c) for c in _SENT_END), default=-1)   # 마지막 종결부호 위치
    return t[:idx + 1] if idx >= 0 else text


def drift_ratio(base: str, revised: str) -> float:
    """0~1 — base 대비 revised 가 얼마나 달라졌나(1=완전 다름). difflib(LCS 류, 결정론).
    autojunk=False: 긴 반복적 한국어 산문(공백·조사 빈출)에서 '인기 원소' 휴리스틱이 유사도를 왜곡해
    국소 교정을 '본문파괴'로 오판하던 결함 차단(적대검증 F7) — 내용 비례 단조 유사도 복원."""
    if not base:
        return 1.0 if (revised or "").strip() else 0.0
    return 1.0 - difflib.SequenceMatcher(None, base, revised, autojunk=False).ratio()


def within_correction_bounds(base: str, revised: str, *, max_drift: float,
                             min_len_ratio: float = 0.6) -> bool:
    """전량 재작성 교정(_rewrite)의 안전 한도 — 위반 시 호출부가 base 를 유지한다.
    ① 길이: revised 가 base 의 min_len_ratio 미만이면 절단/메타응답(기존 R2 길이 가드 계승).
    ② 드리프트: 지정 위반 범위를 넘어 좋은 본문까지 max_drift 초과로 재작성하면 '본문파괴'로 보고 거부.
       (거부 시 위반은 미해소로 남아 다음 라운드 재검→최종 ESCALATED 로 작가에게 노출 — silent 출고 아님.)"""
    rv = revised or ""
    if len(rv) < len(base) * min_len_ratio:
        return False
    if drift_ratio(base, rv) > max_drift:
        return False
    return True
