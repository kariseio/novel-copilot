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


def ai_tell_profile(text: str, roster: set[str] | None = None) -> dict:
    """한국어 'AI 티'의 무사전·결정론 분포 신호(KatFishNet 자질 재구현 — LLM 0콜·사전 0·형태소분석기 의존 0).

    판정기가 아니라 '상대 추세 advisory' 다 — 절대 임계·이진 판정·자동 교정 트리거 금지(G4 측정·가시화 원칙,
    no-whack-a-mole: 검출 패턴을 '교정 규칙'이 아니라 '측정 피처'로만 격하). 작가가 추세를 보고 판단한다.
    근거: KatFishNet(ACL2025)·im-not-ai 가 독립 수렴한 한국어 AI티 핵심 축 — 쉼표 분포·문장길이 분산·어휘/종결 다양성.
      ① comma_*    : AI는 분절적 쉼표가 과다(영어 학습 흔적). 사람은 더 적고 불규칙.
      ② sent_len_cv: 문장 길이 변동계수(std/mean). 낮을수록 균일=AI 의심(사람은 장단 변주).
                     ※교란: 액션/대사 위주 회차는 인간이 의도적으로 균일화 → 단독 판정 금물, 추세로만.
      ③ lexical_mattr: 이동평균 TTR(고유어 제외, 길이-불변). 낮을수록 표현 반복=AI 의심.
      ④ ending_div : 종결형 다양성(말미 음절 근사 — 어간 혼입 줄이려 마지막 1음절만; 이동평균·길이-불변).
                     평서문 '~다' 단조 수렴을 포착. 형태소분석기 없는 거친 근사라 절대값 비교 금물.
      ⑤ simile_per_1k: 비교 형태소(처럼/마치/듯이/듯한) 밀도 — 닫힌 형태소 클래스의 count 신호(교정 아님).
    값은 절대 판정 금지 — 작품 코퍼스 분위수 대비 상대 추세로만 해석한다.
    """
    body = text or ""
    sents = [s.strip() for s in re.split(r"\n+|(?<=[.!?…])\s+", body) if s and s.strip()]
    sents = [s for s in sents if len(s) >= 2]
    n = len(sents)
    chars = len(re.findall(r"\S", body))
    if n == 0 or chars == 0:
        return {"comma_per_100": 0.0, "comma_per_sent": 0.0, "sent_len_cv": 0.0,
                "lexical_mattr": 0.0, "ending_diversity": 0.0, "simile_per_1k": 0.0, "n_sent": 0}
    commas = body.count(",") + body.count("，")
    lens = [len(s) for s in sents]
    mean = sum(lens) / n
    sd = (sum((l - mean) ** 2 for l in lens) / n) ** 0.5

    def _mawin(items: list, w: int) -> float:
        # 이동평균 고유율(MATTR류) — 길이 교란 제거. 원시 TTR/종결율은 텍스트가 길수록
        # 기계적으로 하락해 '회차 간 비교'를 왜곡(검증서 ch10·ch11 길이차로 확인). 창으로 길이-불변화.
        # items<=창: 표준 MATTR 한계정의=원시 TTR. 정식 회차(단어~700·문장~130)는 항상 창>충족 →
        # 이 fallback은 단편/미리보기 입력에만 닿고, 그 경우 값은 회차와 직접 비교하지 말 것.
        if len(items) <= w:
            return round(len(set(items)) / max(1, len(items)), 3)
        rs = [len(set(items[i:i + w])) / w for i in range(len(items) - w + 1)]
        return round(sum(rs) / len(rs), 3)

    roster = roster or set()
    words = [w for w in re.findall(r"[가-힣]{2,}", body) if not any(r in w for r in roster)]
    # 종결형 근사: 말미 구두점·따옴표 제거 후 마지막 음절만(어간 혼입 최소화 — 평서문은 '다'로 수렴해
    # 단조 AI 산문이 낮은 다양도로 정직하게 잡힌다). 완전한 어미 추출은 형태소분석기 필요 → 거친 근사.
    def _final_syll(s: str) -> str:
        t = re.sub(r'[\s.!?…"“”\'’—\-)\]]+$', "", s)
        return t[-1:] if t else ""
    endings = [e for e in (_final_syll(s) for s in sents) if e]
    simile = len(re.findall(r"처럼|마치|듯이|듯한", body))
    return {
        "comma_per_100": round(commas / chars * 100, 2),     # ① 밀도(길이-불변)
        "comma_per_sent": round(commas / n, 2),              # ① 문장당(길이-불변)
        "sent_len_cv": round(sd / mean, 3) if mean else 0.0,  # ② CV(척도-불변) 낮을수록 균일(AI)
        "lexical_mattr": _mawin(words, 60),                   # ③ 길이-불변 TTR, 낮을수록 반복(AI)
        "ending_diversity": _mawin(endings, 40),              # ④ 길이-불변, 낮을수록 종결 단조(AI)
        "simile_per_1k": round(simile / chars * 1000, 2),     # ⑤ 밀도, 높을수록 비유 강박(AI)
        "n_sent": n,
    }


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
        "ai_tell": ai_tell_profile(text, roster),   # 한국어 AI티 분포 신호(advisory 추세)
    }
