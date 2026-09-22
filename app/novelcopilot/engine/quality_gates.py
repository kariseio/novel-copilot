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
# DP-8: 1인칭 서술자 대명사(나는·내가·나를·나도)를 편입 — 3인칭 대명사(그녀·그는·그가)와 대칭.
#   1인칭 회차에서 서술 주어가 고빈도로 반복되는 것은 문체 틱이 아니라 시점의 문법적 필연이므로 빈도 검사 제외.
_STOP = {"그리고", "하지만", "그러나", "그런데", "있었다", "없었다", "것이다", "했다", "않았다",
         "그녀", "그는", "그가", "자신", "지금", "다시", "위해", "함께", "수многие", "있는", "없는",
         "했다가", "였다", "이었다", "한다", "된다", "대한", "통해", "처럼", "만큼", "아니라",
         "나는", "내가", "나를", "나도"}


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


# ─────────────────────────────────────────────────────────────────────────────
# DP-9: 결정론 카운터 2종(과거형 종결 run · 무동사 파편문 비율) — ai_tell 계보의 '측정 피처'.
#   판정기 아님(no-whack-a-mole): 임계·이진 판정·자동교정 트리거 없음. 작가/실험 게이트가 추세로만 해석.
#   DP-9 확정: 종결 run 은 house cadence(대조군)라 단독 변별력이 낮고, '파편문 비율'이 판별축
#   (DP-4 실측 16.7% vs 대조 5.1%). 자유 정독이 지각-하 틱을 건너뛰므로 수치가 정독의 앵커가 된다.
# ─────────────────────────────────────────────────────────────────────────────
_TRAIL = re.compile(r'[\s.!?…"“”\'’·—\-)\]]+$')       # 말미 구두점·따옴표 제거(종결 음절 노출)


def _has_ss_jong(ch: str) -> bool:
    """음절 종성이 ㅆ(쌍시옷)인가 — 과거 선어말어미 았/었/였/했 판별(종성 index 20)."""
    if not ch:
        return False
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3 and (o - 0xAC00) % 28 == 20


def _prose_sentences(text: str) -> list[str]:
    """지문(비대사) 문장 리스트 — tense_leak_ratio 와 동일 계보(대사행 제외).
    대사는 본디 파편·호격이 잦아 지문 문체 신호를 오염하므로 제외(두 카운터가 공유)."""
    prose_lines = [ln.strip() for ln in (text or "").splitlines()
                   if ln.strip() and not ln.strip().startswith(('"', '“', '”', '—', '-'))]
    sents = []
    for ln in prose_lines:
        for s in re.split(r"(?<=[.!?…])\s+", ln):
            s = s.strip()
            if len(s) >= 2:
                sents.append(s)
    return sents


def past_tense_run(text: str) -> dict:
    """과거형 종결(…ㅆ다) 문장의 최장 연속 run·비율 — 지문만, LLM 0콜.
    검출=종결 '다' 직전 음절의 종성이 ㅆ(았/었/였/했)일 때만(종성 ㅆ 가드) → 현재·형용사 '다'(간다/크다/이다)는 제외.
    측정 피처(advisory)일 뿐 판정기 아님(임계·자동교정 없음). DP-9 註: 종결 run 은 house cadence 라 단독
    변별력이 낮을 수 있어 파편문 비율과 함께 읽는다. 한계: 존재사 '있다'(종성 ㅆ·현재)는 과거로 근사 계수될
    수 있음(형태소분석기 없는 거친 근사) — 절대값 판정 금물."""
    sents = _prose_sentences(text)
    flags = [(len(c) >= 2 and c.endswith("다") and _has_ss_jong(c[-2]))
             for c in (_TRAIL.sub("", s) for s in sents)]
    n = len(flags)
    max_run = cur = best_start = run_start = 0
    for i, f in enumerate(flags):
        if f:
            run_start = run_start if cur else i
            cur += 1
            if cur > max_run:
                max_run, best_start = cur, run_start
        else:
            cur = 0
    examples = sents[best_start:best_start + max_run][:4] if max_run else []
    n_past = sum(flags)
    return {"max_run": max_run, "n_past": n_past, "n_sent": n,
            "ratio": round(n_past / n, 3) if n else 0.0, "examples": examples}


# 무동사 파편문 판별 — 종결 서술어(동사/형용사/존대/청유/명령/의문)나 연결어미로 끝나면 '동사 있는 문장'=파편 아님.
#   조사 §4 오탐 가드: 비종결 연결어미(-다면·-으면·-던가류)·의문·호격은 정상 문장이므로 반드시 제외.
#   반말 해체 종결(-어/-여/-해: 먹어·봤어·해)도 서술어가 있는 정상 문장 — 제외(DP-8 1인칭 도파민물의 idiom).
#     대가(문서화): 명사가 어/여/해로 끝나면(상어·오해) 과소계수 — 연결어미형(고/서/면/니) 명사와 같은 보수적
#     오탐 클래스로, 판별축을 '실제 서술어를 파편으로 오계수'(cry-wolf)하지 않는 방향으로 옮긴다.
_PREDICATE_TAIL = re.compile(
    r"("
    r"다|요|죠|"                                                     # 종결 서술(대다수 '다')·존대
    r"습니다|ㅂ니다|십니다|답니다|랍니다|"                            # 존대 종결
    r"군|군요|네|네요|구나|는구나|더라|더군|거든|걸|"                  # 반말·감탄 종결
    r"어|여|해|"                                                     # 반말 해체 종결(먹어·봤어·했어·해) — §4 확장 가드
    r"았어|었어|였어|"                                                # 과거 반말(어간 노출 — 어 subsume 하나 가독 유지)
    r"자|라|어라|아라|여라|렴|으렴|시오|십시오|ㅂ시다|읍시다|"          # 청유·명령
    r"까|까요|나|나요|니|냐|느냐|는가|은가|ㄴ가|을까|ㄹ까|던가|던지|는지|을지|ㄹ지|"  # 의문 종결(§4 가드)
    r"다면|라면|으면|면서|면|거나|든지|든가|지만|는데|은데|ㄴ데|"       # 비종결 연결어미(§4 가드)
    r"니까|어서|아서|고서|려고|려면|도록|어도|아도|고|서"              # 비종결 연결어미(계속)
    r")$")
_VOCATIVE_TAIL = re.compile(r"[가-힣]{1,6}[아야]$")                   # 호격(부름) — 이름/명사 + 아/야(§4 가드)


def fragment_ratio(text: str) -> dict:
    """무동사 파편문(서술어 없는 명사·부사 종결) 비율 — 지문만, LLM 0콜. DP-9 판별축(DP-4 16.7% vs 대조 5.1%).
    파편 후보=종결 서술어로 끝나지 않는 지문 문장. 조사 §4 오탐 가드로 '동사 있는 정상 문장'을 제외:
      · 비종결 연결어미(-다면·-으면·-던가류) · 의문(? 및 의문 종결) · 호격(이름+아/야)
      · 반말 해체 종결(-어/-여/-해: 먹어·봤어·해 — DP-8 1인칭 도파민물의 서술 idiom) → 전부 파편 아님.
    측정 피처(advisory) — 판정·임계·자동교정 없음. 형태소분석기 없는 거친 근사(명사가 우연히 연결어미·해체
    형태로 끝나면 — 고/서/면/니/어/여/해 — 과소계수) — 이 근사는 '실제 서술어를 파편으로 오계수(cry-wolf)'하지
    않는 보수적 방향이다. 절대값 판정 금물, 코퍼스 분위수 대비 추세로만."""
    sents = _prose_sentences(text)
    frags, n = [], 0
    for s in sents:
        if s.rstrip().endswith(("?", "？")):        # 의문 제외(§4)
            continue
        core = _TRAIL.sub("", s)
        if len(core) < 2:
            continue
        n += 1
        if _PREDICATE_TAIL.search(core):            # 서술어/연결어미 종결 → 파편 아님
            continue
        if _VOCATIVE_TAIL.search(core):             # 호격 제외(§4)
            continue
        frags.append(s)
    return {"n_fragment": len(frags), "n_sent": n,
            "ratio": round(len(frags) / n, 3) if n else 0.0, "examples": frags[:5]}


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
                "lexical_mattr": 0.0, "ending_diversity": 0.0, "simile_per_1k": 0.0,
                "past_run_max": 0, "frag_ratio": 0.0, "n_sent": 0}
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
        "past_run_max": past_tense_run(body)["max_run"],       # DP-9 과거형 종결 최장 run(지문·종성 ㅆ 가드)
        "frag_ratio": fragment_ratio(body)["ratio"],           # DP-9 무동사 파편문 비율(지문·§4 오탐 가드) — 판별축
        "n_sent": n,
    }

# NOTE(PR-2·감사 G4): dead였던 chapter_quality_report(text-level 번들러 — 제품 생성 경로에서 호출 0)는
#   회차 통합 검증 SSOT인 engine.verification.build_verification(ChapterRecord 결정론 집계)로 흡수·삭제됨.
#   개별 검출기(word_tics·short_line_ratio·fragmentation_score·tense_leak_ratio·hook_repeat·past_tense_run·
#   fragment_ratio·ai_tell_profile)는 그대로 유지 — 실측 부품이므로. 실험 러너는 필요한 검출기를 직접 호출.
