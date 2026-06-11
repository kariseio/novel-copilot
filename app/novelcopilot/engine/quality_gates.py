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


_QUOTE_SPAN = re.compile(r'["“]([^"”\n]{2,90})["”]')


def word_tics(text: str, roster: set[str] | None = None, cap: int = 4) -> list[tuple[str, int]]:
    """단일 일반 반복-편중 검출기(틱 사전 0개 — 유지보수 금지 원칙).
    역대 틱('즉','그래도','거든?','짧게')을 패턴 등록이 아니라 '분포'로 판정:
      ① 대사 양념 — 출현의 ≥60%가 인용 스팬 안 + cap 초과 반복(위치 무관: 첫어절·끝어절·중간 전부)
      ② 지문 부사 편중 — 쉼표 동반율 ≥50% 또는 부사형 어미(게·으로·군) + cap 초과
    제외는 데이터에서만: roster(인명·세계관 고유어 — 호출자가 전달), _STOP(닫힌 문법 클래스 — 사건 주도 추가 금지)."""
    roster = roster or set()
    words_all = re.findall(r"[가-힣]{2,6}", text)
    dlg = " ".join(_QUOTE_SPAN.findall(text))
    words_dlg = re.findall(r"[가-힣]{2,6}", dlg)
    tot, in_dlg = Counter(words_all), Counter(words_dlg)
    tot += Counter(f"{a} {b}" for a, b in zip(words_all, words_all[1:]))
    in_dlg += Counter(f"{a} {b}" for a, b in zip(words_dlg, words_dlg[1:]))
    comma = Counter(re.findall(r"([가-힣]{2,6}),", text))
    out = []
    for phrase, n in tot.most_common(80):
        if n <= cap or phrase in _STOP or phrase.split()[-1] in _STOP or any(r in phrase for r in roster):
            continue
        seasoning = in_dlg.get(phrase, 0) / n >= 0.6                       # ① 대사 양념(분포 판정)
        adverbial = (comma.get(phrase, 0) / n >= 0.5
                     or phrase.endswith(("게", "으로", "군요", "군")))      # ② 지문 부사 편중
        if seasoning or adverbial:
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
