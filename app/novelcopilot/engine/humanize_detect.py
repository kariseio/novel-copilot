# -*- coding: utf-8 -*-
"""HM-1a 결정론 티 탐지 + 모티프 원장 — 웹소설 휴머나이즈 패스의 탐지 1단(결정론·LLM 0콜).

설계: docs/design-hm1-humanize-pass.md §2ⓐ(분류학)·ⓑ①(결정론 탐지). SSOT: docs/webnovel-ai-tell-taxonomy.md.

이 모듈은 **신규 검출기를 만들지 않는다** — 검증된 기존 계측 부품(Kiwi 문말/층위·파편·retread 어간)을
재사용해 소설 고유 축(N-3 모티프·N-4 문말·N-5 층위·N-6 수식)의 *스팬 좌표·원자료*를 산출한다. 산출은
[{category, severity, span(char_start/end·원문), metric}] — 판정 라벨 없음(수술 입력 원자료·무강제).

**엔진 의존성0 불변(핵심)**: novelcopilot/(엔진)은 kiwipiepy·tools 에 *로드 타임* 의존이 없다. 이 파일의
  tools import 는 전부 함수 *안*에서 lazy·try/except 로 수행된다 — Kiwi/tools 부재 시 해당 축만 조용히
  강등(빈 리스트·결측 정직)하고 나머지 축은 계속 산출한다. 어간 정규화(_stem)는 이미 engine 소유(drift)라
  로드 타임에 import 해도 무방(tools 아님).

**모티프 원장(N-3 입력)**: 작품 단위 회차 간 반복 구절 상위 추출. retread 부품 계보(engine.verification._stem·
  _content_stems 가 쓰는 어간 정규화)를 재사용해 조사 변이를 흡수하고, 회차 문서빈도(DF — 그 구절이 몇
  회차에 나타나나)로 반복 상위를 뽑는다. LLM 0콜·결정론. 산출은 직렬화 가능한 additive 구조(작품별 영속
  대상 — 영속 배선은 후속 HM 티켓 ⓓ, 여기서는 순수 산출 함수만).

**무강제**: 어떤 축도 임계·판정·차단을 만들지 않는다 — severity(S1~S3)는 원전 계보의 밀도·수리 우선순위
  참조일 뿐 PASS/FAIL 아님. 결정론이 좌표를 못 내는 축(N-1 자기 해설·N-2 감정 명명 — 의미 판정 필요)은
  LLM 탐지(설계 ⓑ②)가 보강한다(이 모듈 범위 밖).
"""
from __future__ import annotations
import re

from .drift import _stem   # 어간 정규화(교착어 조사 흡수) — retread 부품이 의존하는 검증 자산(engine 소유·tools 아님)

# 내용어(2자+ 한글) — verification._CW 와 동형(모티프 원장 어간 추출 기저)
_CW = re.compile(r"[가-힣]{2,}")

# 지문/대사 경계 문장 분리(모티프 위치 근사용) — kiwi_metrics/st11 의 대사행 제외 규칙과 동일 기저.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n")


# ─────────────────────────────────────────────────────────────────────────────
# 모티프 원장 — 작품 단위 회차 간 반복 상위 구절(어간 정규화·retread 계보·LLM 0·결정론).
#
#   설계 §2ⓐ N-3: "작품 내 표현 반복('빛줄기가 맥박쳤다' ×n)". 탐지는 회차 간 반복 구절 목록을 공급한다.
#   방법(결정론): 각 회차 내용어 어간열의 n-gram(기본 2·3그램)을 만들고, **회차 문서빈도(DF)** — 그 어간
#   구절이 등장한 서로 다른 회차 수 — 를 센다. DF>=min_chapters 인 구절이 '회차를 넘는 반복'(cross-chapter
#   retread)이다. 어간 정규화로 조사 변이('빛줄기가'/'빛줄기의')를 흡수한다(retread _stem 계보). 한 회차
#   안에서만 여러 번 반복되는 구절(DF=1)은 회차 내 밀도이지 cross-chapter 모티프가 아니므로 제외한다.
#
#   surface_by_chapter: 어간 구절 키 → {회차: [원문 표층 구절 …]} (스팬 좌표 산출·pink-elephant 인용용).
#   원장은 직렬화 가능한 dict — 작품별 영속(additive) 대상. 영속 배선은 후속 HM 티켓(ⓓ)이 담당한다.
# ─────────────────────────────────────────────────────────────────────────────
_DLG_PREFIX = ('"', '“', '”', '—', '-')

# 닫힌 클래스 일반 서술 어간(대명사·존재/지각 만능 서술어·부사) — 장르·작품 어휘에 비의존인 함수어급 토큰.
#   이들'만'으로 이루어진 회차 간 반복 구절('나는 있었다'·'고개 돌렸다')은 소설이면 어디서나 재출현하는
#   문장 골격이지 N-3 크래프트 모티프(재사용된 이미지)가 아니다 → roster 필터와 함께 잡음 억제(선택 적용).
#   genre-blind 준수: 작품 고유 명사·이미지 어휘는 여기 없다(오직 한국어 서술 골격 함수어). 폐집합이라 확장 금지.
_GENERIC_STEMS = frozenset((
    "나는", "내가", "나를", "그것", "자신", "우리", "당신",           # 대명사·재귀
    "있었다", "있다", "없었다", "없다", "않았다", "않는다", "됐다",     # 존재·부정 만능 서술
    "봤다", "보았다", "본다", "말했다", "했다", "한다",               # 지각·발화·대동사
    "고개", "눈이", "손을", "입을",                                  # 신체 만능 목적어(제스처 골격)
    "다시", "먼저", "순간", "계속", "여전히", "잠시",                 # 서술 부사 골격
))


def _roster_name_stems(roster) -> frozenset:
    """roster 엔티티 이름의 내용어 어간 집합 — 인명·지명·아이템명 재출현(합법 서술)을 N-3 잡음에서 제외.

    roster 는 set[str](엔티티 이름) 또는 이름을 주는 임의 이터러블. genre-blind — 이름 목록은 작품 자신의
    온톨로지에서 오지 코드 하드코딩이 아니다. 각 이름을 내용어로 쪼개 어간 정규화(retread _stem 계보)."""
    stems: set[str] = set()
    for nm in (roster or ()):
        for w in _CW.findall(str(nm) or ""):
            stems.add(_stem(w))
    return frozenset(stems)


def _prose_lines(text: str) -> list[str]:
    """지문 줄만(대사행 제외) — 모티프는 지문 문체 신호(대사 원형·화자 목소리 불변, st11 지문 필터 동형)."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s and not s.startswith(_DLG_PREFIX):
            out.append(s)
    return out


def _stem_ngrams_with_surface(text: str, n: int) -> list[tuple[str, str]]:
    """지문에서 내용어 어간 n-gram → [(어간키, 원문 표층 구절)]. 어간 정규화로 조사 변이 흡수(retread 계보).

    표층 구절은 원문 단어들을 공백 1개로 이은 것(스팬 재탐색은 detect 단계가 어간 매칭으로 수행 — 여기선
    pink-elephant 인용·대표 표층 저장용). 지문 줄 경계를 넘지 않는다(줄 안에서만 n-gram — 문단 경계 잡음 방지)."""
    out: list[tuple[str, str]] = []
    for line in _prose_lines(text):
        words = _CW.findall(line)
        if len(words) < n:
            continue
        stems = [_stem(w) for w in words]
        for i in range(len(words) - n + 1):
            key = " ".join(stems[i:i + n])
            surface = " ".join(words[i:i + n])
            out.append((key, surface))
    return out


def build_motif_ledger(chapters: list[dict], *, ngram_sizes: tuple[int, ...] = (2, 3),
                       min_chapters: int = 2, top_k: int | None = None,
                       roster: set[str] | None = None) -> dict:
    """작품 단위 모티프 원장 — 회차 간 반복 상위 구절(어간 정규화·DF·결정론·LLM 0). N-3 탐지 입력.

    chapters: [{"chapter": int, "text": str}, …](영속 chapters 의 부분 dict 로 충분 — text 만 필요).
    ngram_sizes: 추출 n-gram 크기(기본 2·3그램 — 조사 변이 흡수 어간열).
    min_chapters: cross-chapter 판정 최소 등장 회차 수(기본 2 — 회차 1개만 등장=회차 내 밀도, 제외).
    top_k: 상위 절단(None=DF>=min 전체 — 완전 신호. 정수면 상위 top_k 개. 영속 비용 상한용).
    roster: 작품 온톨로지 엔티티 이름 집합(선택). 주어지면 인명·지명·아이템명 재출현(합법 서술)을 잡음
      억제 — 어간이 *전부* roster-name/일반서술(_GENERIC_STEMS)인 구절을 제외(genre-blind: 이름은 작품
      자신의 온톨로지 출처). 미제공이면 필터 없이 원시 반복 신호 전량(하위 안전·결측 정직).

    반환(직렬화 가능·additive 영속 대상):
      {
        "motifs": [{"key": 어간구절, "n": gram크기, "chapter_df": 등장회차수,
                    "total": 총출현수, "chapters": [회차…], "surface": 대표표층구절}, …],  # DF 내림차순
        "min_chapters": …, "ngram_sizes": […], "n_chapters": 회차수,
        "roster_filtered": bool, "backend": "stem-ngram-df"
      }
    회차 1개 이하(대조 기저 없음)면 motifs=[](결측 정직 — 재탕은 회차 간 대조가 전제)."""
    chs = [c for c in (chapters or []) if (c.get("text") or "").strip()]
    if len(chs) < 2:
        return {"motifs": [], "min_chapters": min_chapters, "ngram_sizes": list(ngram_sizes),
                "n_chapters": len(chs), "roster_filtered": bool(roster), "backend": "stem-ngram-df"}

    name_stems = _roster_name_stems(roster)
    drop = _GENERIC_STEMS | name_stems      # 이 집합만으로 이루어진 구절은 서술 골격/명명 재출현(제외)

    # 어간 구절 키 → {회차위치집합, 총출현, 대표표층(회차별 첫 표층)}. DF 는 회차 *위치 인덱스* 로 집계한다
    #   — chapter 라벨 값(중복·None 가능)으로 세면 라벨 충돌 두 회차가 하나로 붕괴돼 반복 모티프가 침묵 억제되므로
    #   enumerate 인덱스를 키로 쓴다(라벨은 emit 용으로 idx→label 매핑에 별도 보존). 라벨 유니크면 바이트 동일.
    idx_label = {i: c.get("chapter") for i, c in enumerate(chs)}
    df: dict[str, set] = {}
    total: dict[str, int] = {}
    ngram_of: dict[str, int] = {}
    surface: dict[str, str] = {}
    for i, c in enumerate(chs):
        for n in ngram_sizes:
            seen_this_ch: set[str] = set()
            for key, surf in _stem_ngrams_with_surface(c.get("text", ""), n):
                # roster/generic 필터: 구절 어간이 *전부* 서술 골격/명명이면 N-3 크래프트 모티프 아님(제외).
                #   하나라도 고유 이미지 어간(빛줄기·맥박 등)이 있으면 유지 — 크래프트 모티프 보존.
                if drop and all(s in drop for s in key.split(" ")):
                    continue
                total[key] = total.get(key, 0) + 1
                ngram_of[key] = n
                if key not in seen_this_ch:
                    df.setdefault(key, set()).add(i)
                    seen_this_ch.add(key)
                surface.setdefault(key, surf)   # 대표 표층 = 최초 등장 원문(인용용)

    motifs = []
    for key, idxset in df.items():
        if len(idxset) < min_chapters:
            continue                              # 회차 1개만 등장 = cross-chapter 아님(회차 내 밀도)
        labels = sorted((idx_label[i] for i in idxset), key=lambda v: (v is None, v))
        motifs.append({
            "key": key, "n": ngram_of[key],
            "chapter_df": len(idxset), "total": total.get(key, 0),
            "chapters": labels, "surface": surface.get(key, ""),
        })
    # 정렬 = 긴 구절(n) 우선 → DF(회차 반복) → 총출현 → 키(안정). 긴 어간 n-gram 이 회차를 넘어 반복되는 것은
    #   우연이 아니라 *의도적 재사용*(크래프트 모티프)의 강한 신호다 — 짧은 2-gram 은 서술/설정 골격이 우연히
    #   겹칠 여지가 크므로(제스처·상용 명사쌍), 길이를 1차 축으로 두어 '빛줄기가 맥박쳤다' 류 이미지 모티프를
    #   상위로 올린다(genre-blind — 작품 어휘 비의존, 오직 구절 길이·반복도). DF 는 2차 축(반복 강도).
    motifs.sort(key=lambda m: (-m["n"], -m["chapter_df"], -m["total"], m["key"]))
    if top_k is not None:
        motifs = motifs[:top_k]
    return {"motifs": motifs, "min_chapters": min_chapters,
            "ngram_sizes": list(ngram_sizes), "n_chapters": len(chs),
            "roster_filtered": bool(roster), "backend": "stem-ngram-df"}


# ─────────────────────────────────────────────────────────────────────────────
# N-3 스팬 탐지 — 원장의 반복 어간 구절을 현재 회차 본문에서 찾아 char 좌표를 낸다(어간 매칭).
#   surface(원문 조사형)는 회차마다 다르므로 정확 문자열 검색이 아니라 *어간 매칭 슬라이딩*으로 위치를
#   찾는다(교착어 substring 함정 회피 — 표층 검색은 '빛줄기가'≠'빛줄기의'에서 놓친다).
# ─────────────────────────────────────────────────────────────────────────────
def _prose_line_ranges(text: str) -> list[tuple[int, int]]:
    """지문 줄(대사행 제외)의 char (start,end) 범위 — 원문 오프셋 보존(splitlines 로 위치 손실 없이).

    _prose_lines 와 동일 판정(대사행 제외)이되 원장→탐지 대칭을 위해 *본문 char 좌표*를 낸다. n-gram 은
    줄 안에서만 만들어지므로(build 단계도 줄 경계 불교차) 탐지도 지문 줄 범위 안에서만 앵커를 잡는다."""
    ranges: list[tuple[int, int]] = []
    pos = 0
    for line in (text or "").splitlines(keepends=True):
        s = line.strip()
        if s and not s.startswith(_DLG_PREFIX):
            ranges.append((pos, pos + len(line)))
        pos += len(line)
    return ranges


def _word_spans(text: str, *, prose_only: bool = False) -> list[tuple[str, int, int]]:
    """본문 내용어(2자+ 한글)의 (원문단어, char_start, char_end) — 어간 슬라이딩 매칭의 앵커.

    prose_only=True 면 지문 줄(대사행 제외) 안의 단어만 — 원장이 지문-only 로 만들어지므로(대사 원형 불변·
    Do-NOT 따옴표 보존) N-3 탐지도 지문-only 로 대칭화해 대사 내부를 수술 후보로 표면화하지 않는다."""
    if not prose_only:
        return [(m.group(0), m.start(), m.end()) for m in _CW.finditer(text or "")]
    pranges = _prose_line_ranges(text)
    if not pranges:
        return []
    out: list[tuple[str, int, int]] = []
    ri = 0
    for m in _CW.finditer(text or ""):
        s, e = m.start(), m.end()
        while ri < len(pranges) and pranges[ri][1] <= s:
            ri += 1
        if ri < len(pranges) and pranges[ri][0] <= s and e <= pranges[ri][1]:
            out.append((m.group(0), s, e))
    return out


def _find_motif_spans(text: str, stem_key: str, gram_n: int) -> list[tuple[int, int]]:
    """본문 지문에서 어간구절(stem_key = 어간 gram_n개)이 나타나는 char (start,end) 범위 목록(어간 매칭).

    본문 지문 내용어를 어간으로 정규화한 뒤 연속 gram_n개가 stem_key 와 일치하는 창을 찾는다. 시작 단어의
    char_start ~ 끝 단어의 char_end 를 스팬으로. 조사형 변이('빛줄기가')도 어간('빛줄기')으로 매칭돼 잡힌다.
    스캔은 지문-only(_word_spans prose_only) — 원장(지문-only)과 대칭이라 대사 내부 반복은 잡지 않는다."""
    key_parts = stem_key.split(" ")
    if len(key_parts) != gram_n:
        return []
    words = _word_spans(text, prose_only=True)
    stems = [_stem(w) for w, _s, _e in words]
    out: list[tuple[int, int]] = []
    for i in range(len(words) - gram_n + 1):
        if stems[i:i + gram_n] == key_parts:
            out.append((words[i][1], words[i + gram_n - 1][2]))
    return out


def detect_motif_retread(text: str, ledger: dict, *, max_spans_per_motif: int = 3,
                         max_total: int | None = 24) -> list[dict]:
    """N-3 모티프 우려먹기 [S2] — 원장의 반복 구절이 이 회차 본문에 나타나는 스팬(어간 매칭·결정론).

    ledger = build_motif_ledger(...) 산출. 각 모티프별로 이 회차 내 출현 위치를 char 스팬으로 낸다
    (max_spans_per_motif 로 모티프당 상한). 회차 자기 자신도 원장 산출에 들어갔으므로(작품 전체 기준) 이
    회차에 없는 모티프는 스팬 0(무접촉). 판정 라벨 없음 — 좌표·원자료만.

    max_total: 회차당 N-3 스팬 총 상한(None=무제한). 원장은 긴 n-gram 우선 → DF(회차 반복) 순이므로(정렬은
      build_motif_ledger 참조) 상위(길고 반복 강한) 모티프 스팬을 우선 채운다 — 서술/설정 재출현으로 스팬이
      폭주하지 않게 하는 비용/가독 가드(무강제·advisory).
    겹침 제거: 긴 n-gram 모티프와 그 부분 2-gram이 같은 구간을 잡을 수 있다('머리카락 두께 빛줄기' ⊇
      '두께 빛줄기') — 원장 순서(길이·DF 우선)로 먼저 확정된 스팬과 char 범위가 겹치면 후순위 스팬은 폐기.
    상한(모티프당·회차 총)은 *겹침 제거 후* 적용한다 — 겹침으로 폐기될 앞 출현이 모티프당 예산을 먼저
      소진해 겹치지 않는 정당한 뒷 출현을 굶기지 않도록(cap-after-overlap)."""
    findings: list[dict] = []
    kept_ranges: list[tuple[int, int]] = []
    for m in (ledger.get("motifs") or []):
        key, n = m.get("key", ""), m.get("n", 0)
        if not key or n <= 0:
            continue
        kept_this_motif = 0
        for (cs, ce) in _find_motif_spans(text, key, n):
            if any(not (ce <= ks or cs >= ke) for (ks, ke) in kept_ranges):
                continue                          # 이미 확정 스팬과 겹침(부분 n-gram 중복) — 폐기
            findings.append({
                "category": "N-3", "severity": "S2",
                "span": {"char_start": cs, "char_end": ce, "text": text[cs:ce]},
                "metric": {"motif_key": key, "n": n,
                           "chapter_df": m.get("chapter_df"), "total": m.get("total"),
                           "chapters": m.get("chapters")},
            })
            kept_ranges.append((cs, ce))
            kept_this_motif += 1
            if max_total is not None and len(findings) >= max_total:
                return findings
            if kept_this_motif >= max_spans_per_motif:
                break                             # 모티프당 상한은 겹침 제거 후 산정(정당한 스팬 기아 방지)
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# N-4/N-5/N-6 — 기존 계측 재사용(Kiwi 문말·층위·파편·수식 밀도). 스팬 좌표는 st11 리듬 스팬 재사용.
#   tools/Kiwi 부재 시 해당 축만 강등(빈 결과)하고 나머지는 계속(결측 정직·엔진 의존성0).
# ─────────────────────────────────────────────────────────────────────────────
def _rhythm_spans(text: str, run_threshold: int = 6) -> list[dict]:
    """st11 리듬 스팬(과거형 종결 run·파편 클러스터)의 char 좌표 — Kiwi 분리기 주입(부재 시 정규식 강등).

    tools 부재/로드 실패 시 빈 리스트(N-4 스팬 강등 — 축 계측은 아래 kiwi_style_metrics 가 별도 산출)."""
    try:
        import tools.st11_span_rewrite as st11
    except Exception:
        return []
    try:
        import tools.kiwi_metrics as km
        splitter = km.split_sents
    except Exception:
        splitter = None
    try:
        return st11.extract_rhythm_spans(text or "", run_threshold=run_threshold,
                                         sent_splitter=splitter)
    except Exception:
        return []


def detect_ending_monotony(text: str, *, run_threshold: int = 6) -> list[dict]:
    """N-4 문말 템플릿 밀도 [S2] — 과거형 종결 run '벽' 스팬 + Kiwi 문말 계측 원자료(재사용).

    스팬: st11 리듬 스팬 중 무중단 동일 종결 키 run(ending_run — ST-14 FIX-1: 과거형 전용→형태 불문·대사
    리셋). 현재형 'ㄴ다' 벽도 잡는다. char 좌표. 각 스팬 metric 에 회차 문말 계측
    (ending_profile top_ratio/max_run·da_streak)을 병기(단독 목표 금지 원칙 — 층위·파편과 세트 해석 재료).
    Kiwi/tools 부재 시 스팬은 정규식 강등(빈 리스트 가능), 계측은 kiwi_style_metrics 가 backend='regex' 로 강등."""
    prof = {}
    try:
        from .style_pipeline import kiwi_style_metrics
        prof = kiwi_style_metrics(text or "")
    except Exception:
        prof = {}
    ep = (prof.get("ending_profile") or {}) if isinstance(prof, dict) else {}
    da = (prof.get("da_streak") or {}) if isinstance(prof, dict) else {}
    ending_metric = {
        "top_template": ep.get("top_template"), "top_ratio": ep.get("top_ratio"),
        "max_run": ep.get("max_run"), "da_max_run": da.get("max_run"),
        "backend": ep.get("backend") or da.get("backend"),
    }
    findings: list[dict] = []
    for sp in _rhythm_spans(text, run_threshold=run_threshold):
        if sp.get("kind") != "ending_run":   # ST-14 FIX-1: 구 'past_run' → 형태 불문 무중단 종결 키 run
            continue
        cs, ce = sp.get("char_start"), sp.get("char_end")
        spm = sp.get("metric") or {}
        findings.append({
            "category": "N-4", "severity": "S2",
            "span": {"char_start": cs, "char_end": ce, "text": (text or "")[cs:ce]},
            # span 자체의 종결 키(ending_key)도 병기 — 회차 최빈(top_template)과 별개인 '이 벽의 종결형'
            "metric": {"run_len": spm.get("run_len"), "ending_key": spm.get("ending_key"), **ending_metric},
        })
    return findings


def detect_fragment_cluster(text: str, *, run_threshold: int = 6) -> list[dict]:
    """N-4 짝(파편 클러스터) — 무동사 파편문 밀집 스팬(리듬 축). 문말 run 과 세트로 리듬 원자료 공급."""
    findings: list[dict] = []
    for sp in _rhythm_spans(text, run_threshold=run_threshold):
        if sp.get("kind") != "fragment_cluster":
            continue
        cs, ce = sp.get("char_start"), sp.get("char_end")
        findings.append({
            "category": "N-4", "severity": "S3",
            "span": {"char_start": cs, "char_end": ce, "text": (text or "")[cs:ce]},
            "metric": {"n_fragment": (sp.get("metric") or {}).get("n_fragment"), "kind": "fragment_cluster"},
        })
    return findings


def detect_fragment_echo(text: str, *, max_frag_len: int = 20, max_total: int = 6) -> list[dict]:
    """N-7 선언-조각 여운 [S3] — 완결문 직후, 용언 종결(EF) 없는 짧은 조각 문장이 단독 여운으로 서는 패턴.

    사용자 재발 실측(2026-08-11, 예: 완결 선언 뒤 '집 밖에.' 류): 기존 파편 축은 *밀집 클러스터*만 잡아
    고립 조각이 그물 아래로 지나갔다. 이 축은 고립 조각(앞 문장 완결 + 뒤 문장 비조각)만 잡는다 —
    나열형 조각 연속은 클러스터 축 소관(중복 수술 금지). 스팬은 앞 완결문 시작~조각 끝(윤문이 이어붙일
    재료를 함께 받도록). Kiwi 부재 시 빈 리스트(결측 정직·판정 아님·advisory)."""
    try:
        import tools.kiwi_metrics as km
        kw = km._get_kiwi()
    except Exception:
        return []
    if kw is None or not (text or "").strip():
        return []

    def _sents_in(seg: str, base: int) -> list[tuple[str, int, int]]:
        out = []
        try:
            for s in kw.split_into_sents(seg):
                t = (s.text or "").strip()
                if t:
                    out.append((t, base + s.start, base + s.end))
        except Exception:
            return []
        return out

    sents: list[tuple[str, int, int]] = []
    for ls, le in _prose_line_ranges(text):
        sents += _sents_in(text[ls:le], ls)

    def _is_fragment(s: str) -> bool:
        if len(s) > max_frag_len or len(s) < 5:   # 4자 이하(의성어 단발 '따랑—' 류)는 관용 펀치로 존중
            return False
        if not re.search(r"[가-힣]", s):    # 장면 구분자(***)·기호 행 제외
            return False
        try:
            toks = kw.analyze(s, top_n=1)[0][0]
        except Exception:
            return False
        return not any(t.tag.startswith("EF") for t in toks)

    flags = [_is_fragment(s) for s, _, _ in sents]
    findings: list[dict] = []
    for i, (s, cs, ce) in enumerate(sents):
        if not flags[i] or i == 0:
            continue
        prev_frag = flags[i - 1]
        next_frag = flags[i + 1] if i + 1 < len(flags) else False
        if prev_frag or next_frag:      # 조각 연속 = 나열/클러스터 소관
            continue
        p_s, p_cs, p_ce = sents[i - 1]
        if ce - p_cs > 220:             # 스팬 폭 가드(비정상 병합 방지)
            continue
        gap = (text or "")[p_ce:cs]
        if gap.strip():                 # 사이에 대사·구분자 등 제외 행이 끼면 인접 아님(병합 오탐 차단)
            continue
        findings.append({
            "category": "N-7", "severity": "S3",
            "span": {"char_start": p_cs, "char_end": ce, "text": (text or "")[p_cs:ce]},
            "metric": {"fragment": s, "frag_len": len(s), "kind": "fragment_echo"},
        })
        if len(findings) >= max_total:
            break
    return findings


def detect_layer_wall(text: str) -> list[dict]:
    """N-5 발화 층위 벽 [S1] — 지문 단일 층위 연속(max_narration_run) 원자료(ST-9 축·style_pipeline 재사용).

    결정론 스팬 좌표는 층위 원장 부품(style_lightness_baseline)이 run 위치를 직접 내지 않으므로 회차 단위
    원자료(층위 계측)를 낸다(span 은 전체 지문 범위 근사 — 좌표 정밀화는 LLM 탐지/후속). tools 부재 시 []."""
    layer = None
    try:
        from .style_pipeline import _layer_axes
        layer = _layer_axes(text or "")
    except Exception:
        layer = None
    if not layer:
        return []
    return [{
        "category": "N-5", "severity": "S1",
        # 회차 단위 신호 — 스팬은 지문 전체 범위(정밀 run 좌표는 후속). 좌표 없음 표식 대신 전범위.
        "span": {"char_start": 0, "char_end": len(text or ""), "text": ""},
        "metric": {"max_narration_run": layer.get("max_narration_run"),
                   "dialogue_para_ratio": layer.get("dialogue_para_ratio"),
                   "dialogue_char_ratio": layer.get("dialogue_char_ratio"),
                   "kind": "layer_wall"},
    }]


def detect_cliche_modifier(text: str, roster: set[str] | None = None) -> list[dict]:
    """N-6 클리셰 직유·수식 [S3] — 직유 밀도(simile_per_1k) 회차 단위 원자료(ai_tell_profile 재사용).

    상투성 판정은 LLM/정독 몫(결정론은 밀도만). 좌표는 회차 단위(밀도 신호). ai_tell_profile 부재/실패 시 []."""
    try:
        from .quality_gates import ai_tell_profile
        prof = ai_tell_profile(text or "", roster=roster)
    except Exception:
        return []
    simile = prof.get("simile_per_1k")
    if simile is None:
        return []
    return [{
        "category": "N-6", "severity": "S3",
        "span": {"char_start": 0, "char_end": len(text or ""), "text": ""},
        "metric": {"simile_per_1k": simile, "kind": "simile_density"},
    }]


# ─────────────────────────────────────────────────────────────────────────────
# 회차 통합 탐지 — 소설 결정론 축 전체를 한 회차에서 산출(수술 입력 원자료·판정 라벨 없음).
# ─────────────────────────────────────────────────────────────────────────────
def detect_chapter(text: str, *, ledger: dict | None = None, roster: set[str] | None = None,
                   run_threshold: int = 6) -> list[dict]:
    """한 회차 결정론 티 탐지 — [{category, severity, span(char_start/end·원문), metric}] (LLM 0콜).

    축: N-3(모티프 — ledger 제공 시)·N-4(문말 run + 파편 클러스터)·N-7(고립 조각 여운)·N-5(층위 벽)·N-6(직유 밀도).
    ledger 미제공(None)이면 N-3 생략(작품 원장이 있어야 cross-chapter 판별 — 결측 정직). 어떤 축도
    판정·차단을 만들지 않는다(무강제). 반환 순서: 스팬 좌표순 안정 정렬(char_start → category)."""
    findings: list[dict] = []
    if ledger:
        findings += detect_motif_retread(text, ledger)
    findings += detect_ending_monotony(text, run_threshold=run_threshold)
    findings += detect_fragment_cluster(text, run_threshold=run_threshold)
    findings += detect_fragment_echo(text)   # N-7: 고립 조각 여운(클러스터 축의 사각 — 재발 실측 2026-08-11)
    findings += detect_layer_wall(text)
    findings += detect_cliche_modifier(text, roster=roster)
    findings.sort(key=lambda f: (f["span"]["char_start"], f["category"]))
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# HZ-1 ①②④ 판정형 N-4 선별 — 스타일 지각 판정(style_judge)의 '문제 구간 인용'을 본문 위치에 매칭해
#   결정론 리듬 스팬으로 정밀 좌표를 잡고, 사실 밀집 스팬을 수술 금기로 사전 제외한다. N-5/N-6 은
#   불변(자체 검출 기반). HZ-2 부터 N-3 모티프도 판정 motif_spans 인용 위치로 게이트한다(select_n3_findings·
#   동형 로직). harness finalize 와 copilot._finalize_rerender_text 양쪽이 이 헬퍼를 재사용한다
#   (이중 구현 금지). LLM 0콜(판정 콜은 호출부가 이미 수행 — 여기는 결정론 매칭·금기·상한만).
# ─────────────────────────────────────────────────────────────────────────────


def motif_candidates_from_findings(findings: list[dict], *, max_candidates: int = 8) -> list[str]:
    """HZ-2: detect_chapter 산출 findings 에서 N-3 모티프 반복 구절(표층 문구)만 추린다 — judge_style 참고 자료용.

    style_judge.judge_style 의 motif_candidates 인자로 넘길 문자열 목록(회차 간 반복 구절의 본문 표층). 중복
    구절은 한 번만(순서 보존). max_candidates 상한(참고 자료 노이즈·비용 가드). N-3 findings 없으면 빈 목록
    (하위호환: judge_style 이 빈 목록이면 문말만 판정)."""
    out: list[str] = []
    for f in (findings or []):
        if f.get("category") != "N-3":
            continue
        q = ((f.get("span") or {}).get("text") or "").strip()
        if q and q not in out:
            out.append(q)
            if len(out) >= max_candidates:
                break
    return out

# HZ-1 ② 사실 밀집 수술 금기 임계(결정론 상수). 스팬 텍스트의 (아라비아 수치 토큰 수 + 온톨로지 엔티티 이름
#   등장 수) 합이 이 값 이상이면 그 스팬은 수술 시도 자체를 스킵한다. 근거(MD-1 실측): 사실 밀집 회차에서
#   G-B 사실 표면 가드가 윤문 대부분을 기각(3화 1/6 채택 — 12.9만 토큰이 153자 수리로 소진, 기각 사유
#   전부 "G-B 불통과"). 기각될 수술을 애초에 시도하지 않아 낭비를 소스 차단한다(no-whack-a-mole).
FACT_DENSE_TOKEN_MIN = 4


def _fact_token_count(span_text: str, roster: set[str] | None) -> tuple[int, int]:
    """스팬 텍스트의 (아라비아 수치 토큰 수, 온톨로지 엔티티 이름 등장 수). rerender.extract_numeric_tokens
    재사용(아라비아 한정 — 한글 수사 관용구는 재구성 여지, 하드 금기 대상 아님·재실현 계약 동형). 엔티티
    이름은 roster 의 각 이름이 스팬에 substring 으로 나타나면 카운트(이름은 사실 앵커 — 재구성 시 유실 위험)."""
    try:
        from .rerender import extract_numeric_tokens
        n_num = len(extract_numeric_tokens(span_text or "", arabic_only=True))
    except Exception:
        n_num = 0
    n_name = 0
    for nm in (roster or ()):
        nm = (str(nm) or "").strip()
        if nm and nm in (span_text or ""):
            n_name += 1
    return n_num, n_name


def _match_quote_span(text: str, quote: str) -> tuple[int, int] | None:
    """판정 인용(quote)을 본문에서 위치 매칭 — 정확 일치 우선, 실패 시 공백 정규화 부분 매칭. (start,end) 또는 None.

    measure-then-cite: 인용이 본문과 정확히 일치하면 그 창을 쓴다. LLM 이 공백/줄바꿈을 흘렸을 수 있으므로
    정확 실패 시 공백류를 단일화한 정규화 텍스트에서 재탐색하고, 정규화 오프셋을 원문 오프셋으로 역매핑한다.
    두 경로 다 실패(인용이 본문에 없음)면 None — 호출부가 스킵+기록(measure-then-cite 계약: 인용≠본문=무시)."""
    q = (quote or "").strip()
    if not q or not text:
        return None
    # 1) 정확 일치(첫 등장). 유일하지 않아도 첫 창을 쓴다(판정 인용은 순서대로 소비).
    i = text.find(q)
    if i >= 0:
        return (i, i + len(q))
    # 2) 공백 정규화 부분 매칭 — 원문 각 문자의 정규화 후 위치를 추적해 역매핑.
    import re as _re
    norm_chars: list[str] = []
    orig_pos: list[int] = []   # norm_chars[k] 가 온 원문 인덱스
    prev_ws = False
    for idx, ch in enumerate(text):
        if ch.isspace():
            if prev_ws:
                continue
            norm_chars.append(" ")
            orig_pos.append(idx)
            prev_ws = True
        else:
            norm_chars.append(ch)
            orig_pos.append(idx)
            prev_ws = False
    norm_text = "".join(norm_chars)
    norm_q = _re.sub(r"\s+", " ", q).strip()
    if not norm_q:
        return None
    j = norm_text.find(norm_q)
    if j < 0:
        return None
    start = orig_pos[j]
    end_norm = j + len(norm_q) - 1
    end = orig_pos[end_norm] + 1 if end_norm < len(orig_pos) else len(text)
    return (start, end)


def _rhythm_span_covering(rhythm_spans: list[dict], cs: int, ce: int) -> tuple[int, int] | None:
    """인용 창 (cs,ce)과 겹치는 결정론 리듬 스팬(ending_run)의 char 창 — 정밀 좌표. 겹침 없으면 None(인용 창 사용)."""
    for sp in (rhythm_spans or []):
        s, e = sp.get("char_start"), sp.get("char_end")
        if not isinstance(s, int) or not isinstance(e, int):
            continue
        if not (ce <= s or cs >= e):   # 겹침
            return (s, e)
    return None


def select_n4_findings(text: str, judgment: dict | None, settings, *,
                       roster: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """HZ-1 판정형 N-4 선별 — 판정 인용을 본문 위치에 매칭·결정론 리듬 스팬으로 정밀화·사실 밀집 금기 제외.

    text: 회차 프로즈(최종화 후·수리 전). judgment: style_judge.judge_style 산출(또는 None=판정 실패/스킵).
    settings: config(humanize_n4_max_spans 읽음). roster: 온톨로지 엔티티 이름 집합(사실 밀집 금기용).

    반환 (n4_findings, skip_records):
      · n4_findings — 윤문 대상 N-4 finding 리스트(humanize_pass.humanize_spans 가 소비할 형식:
        {category:'N-4', severity:'S2', span:{char_start,char_end,text}, metric:{...}}). 판정 인용 순서대로
        최대 humanize_n4_max_spans 개. 사실 밀집 스팬은 여기 없음(금기 제외).
      · skip_records — 투명성 기록(은폐 금지): 각 항목 {changed:False, fallback, note, quote?, why?}.
        fallback ∈ {'judge_missing','no_repair','quote_unmatched','fact_dense'}.

    판정 실패/스킵(judgment=None) 또는 needs_repair=false 면 n4_findings=[](수리 0) + 기록. 매화 무조건
    실행되는 판정 자체는 호출부가 수행하므로 이 헬퍼는 결정론(LLM 0콜)이다."""
    skips: list[dict] = []
    max_spans = int(getattr(settings, "humanize_n4_max_spans", 3))

    if judgment is None:
        skips.append({"category": "N-4", "changed": False, "fallback": "judge_missing",
                      "note": "스타일 지각 판정 실패/스킵(파싱·콜 실패) — N-4 수리 스킵(보수·결측 정직)"})
        return [], skips
    if not judgment.get("needs_repair"):
        skips.append({"category": "N-4", "changed": False, "fallback": "no_repair",
                      "note": "판정: 문말 단조 수리 불필요(needs_repair=false)",
                      "why": (judgment.get("reason") or "")})
        return [], skips

    # 결정론 리듬 스팬(ending_run) — 인용 창을 정밀 좌표로 스냅하는 데 쓴다(부재 시 인용 창 그대로).
    rhythm = _rhythm_spans(text, run_threshold=6)
    ending_runs = [sp for sp in rhythm if sp.get("kind") == "ending_run"]

    findings: list[dict] = []
    used_ranges: list[tuple[int, int]] = []
    for sp in (judgment.get("spans") or []):
        if len(findings) >= max_spans:
            break   # 상한 도달 — 나머지 인용은 시도하지 않음(최악 우선·판정 순서)
        quote = (sp.get("quote") or "").strip()
        why = (sp.get("why") or "").strip()
        if not quote:
            continue
        matched = _match_quote_span(text, quote)
        if matched is None:
            skips.append({"category": "N-4", "changed": False, "fallback": "quote_unmatched",
                          "note": "판정 인용이 본문과 매칭 실패(정확/공백정규화 둘 다) — 스킵(measure-then-cite)",
                          "quote": quote, "why": why})
            continue
        cs, ce = matched
        # 결정론 리듬 스팬과 겹치면 그 창을 정밀 좌표로(문장 경계 정렬). 없으면 인용 창 그대로.
        precise = _rhythm_span_covering(ending_runs, cs, ce)
        if precise is not None:
            cs, ce = precise
        # 중복 창 제거(같은 run 을 두 인용이 가리킬 수 있음).
        if any(not (ce <= ks or cs >= ke) for (ks, ke) in used_ranges):
            continue
        span_text = (text or "")[cs:ce]
        if not span_text.strip():
            continue
        # HZ-1 ② 사실 밀집 금기 — (수치 토큰 + 엔티티 이름) >= 임계면 수술 시도 자체 스킵(G-B 기각 낭비 소스 차단).
        n_num, n_name = _fact_token_count(span_text, roster)
        if (n_num + n_name) >= FACT_DENSE_TOKEN_MIN:
            skips.append({"category": "N-4", "changed": False, "fallback": "fact_dense",
                          "note": f"사실 밀집(수치 {n_num}+엔티티 {n_name} >= {FACT_DENSE_TOKEN_MIN}) — "
                                  "수술 금기(G-B 기각 낭비 사전 제외)",
                          "quote": quote, "why": why})
            continue
        used_ranges.append((cs, ce))
        findings.append({
            "category": "N-4", "severity": "S2",
            "span": {"char_start": cs, "char_end": ce, "text": span_text},
            "metric": {"source": "style_judge", "why": why,
                       "num_tokens": n_num, "entity_names": n_name},
        })
    return findings, skips


def _n3_span_covering(n3_findings: list[dict], cs: int, ce: int) -> tuple[int, int] | None:
    """인용 창 (cs,ce)과 겹치는 결정론 N-3 모티프 스팬의 char 창 — 정밀 좌표(select_n4 의 리듬 스팬 정밀화 동형).

    detect_motif_retread 가 낸 N-3 finding 스팬(어간 매칭으로 잡은 모티프 출현 창) 중 인용 창과 겹치는 첫 창을
    돌려준다 — 판정 인용을 결정론 모티프 스팬 경계로 스냅(수술 좌표 안정화). 겹침 없으면 None(인용 창 사용)."""
    for f in (n3_findings or []):
        sp = f.get("span") or {}
        s, e = sp.get("char_start"), sp.get("char_end")
        if not isinstance(s, int) or not isinstance(e, int):
            continue
        if not (ce <= s or cs >= e):   # 겹침
            return (s, e)
    return None


def select_n3_findings(text: str, n3_findings: list[dict], judgment: dict | None, settings, *,
                       roster: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """HZ-2 판정형 N-3 선별 — 판정 motif_spans 인용을 본문 위치에 매칭·결정론 모티프 스팬으로 정밀화·사실 밀집 금기.

    select_n4_findings 와 동형 로직(인용→위치 매칭(정확→공백정규화)→모티프 스팬 정밀화→사실 밀집 금기→상한).
    차이: 축이 N-4 리듬 스팬이 아니라 N-3 모티프 스팬(n3_findings = detect_chapter 의 N-3 findings). 판정
    motif_spans 로 확인된 모티프 구절만 수술 대상, 판정에 없는(미확인) N-3 findings 는 드롭 + skip 기록(은폐 금지).

    text: 회차 프로즈. n3_findings: detect_chapter 의 N-3 findings(자체 검출 모티프 스팬). judgment: judge_style
    산출(또는 None). settings: config(humanize_n4_max_spans 재사용 — N-3 상한). roster: 사실 밀집 금기용.

    반환 (n3_selected, skip_records):
      · n3_selected — 윤문 대상 N-3 finding 리스트({category:'N-3', severity:'S2', span, metric}). 판정 motif_spans
        인용 순서대로 최대 humanize_n4_max_spans 개. 사실 밀집 스팬은 여기 없음(금기 제외).
      · skip_records — 투명성 기록(은폐 금지): fallback ∈ {'judge_missing','no_motif','quote_unmatched','fact_dense',
        'motif_unconfirmed'}. 판정 실패/스킵=미확인 N-3 전부 드롭+기록. 판정 있음이면 확인된 것만 수술, 나머지 드롭."""
    skips: list[dict] = []
    n3_in = [f for f in (n3_findings or []) if f.get("category") == "N-3"]
    max_spans = int(getattr(settings, "humanize_n4_max_spans", 3))

    if judgment is None:
        if n3_in:
            skips.append({"category": "N-3", "changed": False, "fallback": "judge_missing",
                          "note": f"스타일 지각 판정 실패/스킵 — N-3 모티프 {len(n3_in)}건 수술 스킵(보수·결측 정직)"})
        return [], skips
    motif_spans = judgment.get("motif_spans") or []
    if not motif_spans:
        # 판정이 모티프를 하나도 확인하지 않음 → 검출된 N-3 전부 드롭(미확인·투명성 기록).
        if n3_in:
            skips.append({"category": "N-3", "changed": False, "fallback": "no_motif",
                          "note": f"판정: 기계 반복 모티프 없음(motif_spans 빈 배열) — N-3 {len(n3_in)}건 수술 스킵"})
        return [], skips

    findings: list[dict] = []
    used_ranges: list[tuple[int, int]] = []
    confirmed_ranges: list[tuple[int, int]] = []   # 판정 인용이 매칭된 창(미확인 N-3 집계용)
    for sp in motif_spans:
        if len(findings) >= max_spans:
            break   # 상한 도달 — 나머지 인용은 시도하지 않음(판정 순서·최악 우선)
        quote = (sp.get("quote") or "").strip()
        why = (sp.get("why") or "").strip()
        if not quote:
            continue
        matched = _match_quote_span(text, quote)
        if matched is None:
            skips.append({"category": "N-3", "changed": False, "fallback": "quote_unmatched",
                          "note": "판정 모티프 인용이 본문과 매칭 실패(정확/공백정규화 둘 다) — 스킵(measure-then-cite)",
                          "quote": quote, "why": why})
            continue
        cs, ce = matched
        # 결정론 N-3 모티프 스팬과 겹치면 그 창을 정밀 좌표로(어간 매칭 경계 정렬). 없으면 인용 창 그대로.
        precise = _n3_span_covering(n3_in, cs, ce)
        if precise is not None:
            cs, ce = precise
        confirmed_ranges.append((cs, ce))
        # 중복 창 제거(같은 모티프를 두 인용이 가리킬 수 있음).
        if any(not (ce <= ks or cs >= ke) for (ks, ke) in used_ranges):
            continue
        span_text = (text or "")[cs:ce]
        if not span_text.strip():
            continue
        # 사실 밀집 금기(N-4 와 동일 임계) — (수치 토큰 + 엔티티 이름) >= 임계면 수술 시도 스킵(G-B 기각 낭비 사전 제외).
        n_num, n_name = _fact_token_count(span_text, roster)
        if (n_num + n_name) >= FACT_DENSE_TOKEN_MIN:
            skips.append({"category": "N-3", "changed": False, "fallback": "fact_dense",
                          "note": f"사실 밀집(수치 {n_num}+엔티티 {n_name} >= {FACT_DENSE_TOKEN_MIN}) — "
                                  "수술 금기(G-B 기각 낭비 사전 제외)",
                          "quote": quote, "why": why})
            continue
        used_ranges.append((cs, ce))
        findings.append({
            "category": "N-3", "severity": "S2",
            "span": {"char_start": cs, "char_end": ce, "text": span_text},
            "metric": {"source": "style_judge", "why": why,
                       "num_tokens": n_num, "entity_names": n_name},
        })
    # 판정이 확인하지 않은(motif_spans 인용과 겹치지 않는) 검출 N-3 는 드롭 — 미확인 개수만 투명 기록(은폐 금지).
    unconfirmed = 0
    for f in n3_in:
        sp = f.get("span") or {}
        s, e = sp.get("char_start"), sp.get("char_end")
        if not isinstance(s, int) or not isinstance(e, int):
            unconfirmed += 1
            continue
        if not any(not (e <= ks or s >= ke) for (ks, ke) in confirmed_ranges):
            unconfirmed += 1
    if unconfirmed:
        skips.append({"category": "N-3", "changed": False, "fallback": "motif_unconfirmed",
                      "note": f"판정 미확인 모티프 {unconfirmed}건 드롭(판정 motif_spans 에 없음 — 자연스러운 반복)"})
    return findings, skips


def apply_style_judgment(text: str, findings: list[dict], judgment: dict | None, settings, *,
                         roster: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """detect_chapter 산출 findings 에 지각 판정형 선별을 적용 — 양 호출부(harness·copilot) 공용(이중 구현 금지).

    N-4 findings 는 결정론 검출을 *버리고* 판정 인용 기반 선별(select_n4_findings)로 대체한다(HZ-1 — 임계 발동
    폐기·매화 판정). HZ-2: N-3 모티프 findings 도 판정 motif_spans 인용 위치로 게이트한다(select_n3_findings) —
    config humanize_motif_judgment ON(기본)일 때. OFF 면 구 동작(N-3 무조건 통과·바이트 동일). N-5/N-6 은 어느
    쪽이든 자체 검출 기반 그대로 통과(저비용). 판정 콜은 호출부가 이미 수행(judgment 주입). LLM 0콜.

    반환 (recombined_findings, skip_records).
    recombined_findings 순서 = 판정 N-4(인용 순서) + 판정 N-3(인용 순서) + 나머지(char_start 정렬). skip_records
    는 N-4/N-3 스킵·금기·드롭 기록(투명성·은폐 금지) — 호출부가 humanize 내역 or 이벤트로 가시화한다."""
    motif_gate = bool(getattr(settings, "humanize_motif_judgment", True))
    n4, skips = select_n4_findings(text, judgment, settings, roster=roster)
    if motif_gate:
        # HZ-2: N-3 도 판정 게이트 — 검출 N-3 를 *버리고* 판정 확인 모티프만 선별(미확인=드롭+기록).
        n3_detected = [f for f in (findings or []) if f.get("category") == "N-3"]
        n3, n3_skips = select_n3_findings(text, n3_detected, judgment, settings, roster=roster)
        skips = list(skips) + list(n3_skips)
        others = [f for f in (findings or []) if f.get("category") not in ("N-4", "N-3")]
    else:
        # 구 동작(HZ-1): N-3 무조건 통과(자체 검출)·N-4 만 판정 대체 — humanize_motif_judgment OFF·바이트 동일.
        n3 = []
        others = [f for f in (findings or []) if f.get("category") != "N-4"]
    others.sort(key=lambda f: ((f.get("span") or {}).get("char_start", 0), f.get("category", "")))
    return n4 + n3 + others, skips


# 구명 alias 유지(HZ-2 개명 — 호출부/테스트 하위호환). apply_n4_judgment 는 apply_style_judgment 를 가리킨다.
apply_n4_judgment = apply_style_judgment
