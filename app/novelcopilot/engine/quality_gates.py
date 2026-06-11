# -*- coding: utf-8 -*-
"""품질 결정론 게이트 (LLM 0콜) — 편집자 감점 축의 코드 검출기.

7라운드 실측 교훈: 점수를 누적 견인한 축은 전부 '결정론 게이트가 있는 축'.
프롬프트 지시만 있는 축(틱·훅 재탕·조판·시제)은 런마다 주사위 → 여기서 결정론화한다.
검출은 코드, 교정은 국소(문제 지점만) — 전량 재생성 금지(R2 본문파괴 교훈).
"""
from __future__ import annotations
import re
from collections import Counter

# 서술 구조어(틱이 아님) — 빈도 검사 제외
_STOP = {"그리고", "하지만", "그러나", "그런데", "있었다", "없었다", "것이다", "했다", "않았다",
         "그녀", "그는", "그가", "자신", "지금", "다시", "위해", "함께", "수многие", "있는", "없는",
         "했다가", "였다", "이었다", "한다", "된다", "대한", "통해", "처럼", "만큼", "아니라"}


# 간투사/습관구 사전(역대 라운드 실측 누적) — 도메인 명사와 달리 '대사 양념'이라 다양화 대상
_FILLERS = {"즉", "설마", "짧게", "흥미롭군", "젠장", "그래", "잠깐", "어쨌든", "아무튼", "물론",
            "그 순간", "다음 순간", "순간", "분석 결과", "논리적으로", "통계적으로", "확률적으로"}


def word_tics(text: str, roster: set[str] | None = None, cap: int = 4) -> list[tuple[str, int]]:
    """틱(습관구) 검출 — 간투사/부사류가 cap 초과 반복. 도메인 명사(신호·코어 등)는 제외(세계관 어휘 희석 방지).
    틱 판정: 사전(_FILLERS) 일치 / 부사형 어미(게·으로·까) / 쉼표 동반율 ≥50%(대사 양념 패턴)."""
    roster = roster or set()
    words = re.findall(r"[가-힣]{1,6},?", text)
    uni = Counter(w.rstrip(",") for w in words if len(w.rstrip(",")) >= 1)
    big = Counter(f"{a.rstrip(',')} {b.rstrip(',')}" for a, b in zip(words, words[1:]))
    comma = Counter(w.rstrip(",") for w in words if w.endswith(","))
    out = []
    for phrase, n in (uni + big).most_common(60):
        if n <= cap or phrase in _STOP or any(r in phrase for r in roster):
            continue
        base = phrase.split()[-1]
        is_filler = (phrase in _FILLERS or base in _FILLERS
                     or base.endswith(("게", "으로", "까", "군요", "군"))
                     or (comma.get(phrase.split()[0], 0) / max(1, uni.get(phrase.split()[0], 1)) >= 0.5))
        if is_filler:
            out.append((phrase, n))
    dedup = []
    for p, n in sorted(out, key=lambda x: -len(x[0])):
        if not any(p in q for q, _ in dedup):
            dedup.append((p, n))
    return sorted(dedup, key=lambda x: -x[1])[:8]


def _tok(s: str) -> set:
    return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", s))


def hook_repeat(tail: str, prev_tails: list[str], thresh: float = 0.42) -> float:
    """말미 훅의 직전 회차들 대비 최대 자카드 유사도 — thresh 초과면 재탕."""
    t = _tok(tail)
    if not t:
        return 0.0
    best = 0.0
    for p in prev_tails:
        q = _tok(p)
        if q:
            best = max(best, len(t & q) / len(t | q))
    return best


def hook_repeat_semantic(provider, tail: str, prev_tails: list[str]) -> float:
    """임베딩 코사인 최대값 — 토큰이 달라도 같은 템플릿('누구의 X가 …할지—아무도 모른다')을 잡는다.
    (R7 소급검증: 자카드 0.17로 미탐했던 재탕을 의미 유사도로 포착. embed 1콜.)"""
    if not tail.strip() or not prev_tails:
        return 0.0
    try:
        import numpy as np
        vecs = provider.embed([tail] + prev_tails[-5:])
        t = np.array(vecs[0])
        t = t / (np.linalg.norm(t) + 1e-9)
        best = 0.0
        for v in vecs[1:]:
            v = np.array(v)
            best = max(best, float(t @ (v / (np.linalg.norm(v) + 1e-9))))
        return best
    except Exception:
        return 0.0


_BANNED_SOLO = re.compile(r"^\s*[\[(]?\s*(절단|훅|클리프행어|절단신공|cliffhanger|hook)\s*[.!\])]?\s*$",
                          re.IGNORECASE)


def strip_directive_leak(text: str) -> str:
    """하네스 지시어 어휘의 단독행 누출 제거(결정론) — R7 '절단.' 한 단어 누출 차단."""
    return "\n".join(ln for ln in text.splitlines() if not _BANNED_SOLO.match(ln.strip()))


def tense_leak_ratio(text: str) -> float:
    """과거형 기조 대비 현재형 종결('~ㄴ다.') 비율 — 지문(비대사)만."""
    prose = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(('"', '“', '—'))]
    sents = [s for ln in prose for s in re.split(r"(?<=다\.)\s+", ln) if s.strip().endswith("다.")]
    if len(sents) < 10:
        return 0.0
    present = sum(1 for s in sents if re.search(r"(?<![었았])[는한온간운인난된친낀]다\.$", s.strip()))
    return present / len(sents)


def chapter_quality_report(text: str, prev_tails: list[str], roster: set[str] | None = None) -> dict:
    """회차 1개의 결정론 품질 리포트 — 편집자 감점 축 전부 숫자로."""
    from .harness import fragmentation_score, short_line_ratio   # 기존 검출기 재사용
    tail = " ".join([ln for ln in text.splitlines() if ln.strip()][-3:])
    return {
        "tics": word_tics(text, roster),
        "hook_sim": round(hook_repeat(tail, prev_tails), 2),
        "short_line_ratio": round(short_line_ratio(text), 2),
        "frag_score": round(fragmentation_score(text), 1),
        "tense_leak": round(tense_leak_ratio(text), 3),
        "directive_leak": bool(re.search(r"^\s*절단", text, re.MULTILINE)),
    }
