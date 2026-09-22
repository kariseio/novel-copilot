# -*- coding: utf-8 -*-
"""ST-1 웹소설 '가벼움' 결정론 문체 지표 + LR-1 3작 baseline 실측 (LLM 0콜·읽기 전용).

목적: "가볍게 · 설명/묘사 중심 금지 · 대사와 행동 중심 · 모바일 문단 리듬" 이라는 문체 목표를
      **측정 가능**하게 만든다. 이것은 ai_tell_profile 계보의 *측정 피처*다 — 판정기가 아니다.
      임계·라벨·자동 재작성 트리거 0 (프로젝트 G4 측정·가시화 원칙, no-whack-a-mole, ai_tell 선례와 정합).
      모든 값은 **작품 자기 코퍼스 대비 상대 추세**로만 해석한다 (절대 기준선 없음 — 아래 LIMITATIONS).

측정 축(회차별·작품별):
  1. 대사 비중      : 대사 문자비중 · 대사 포함 문단 비중 · 무대사 연속 문단 최대 run(묘사블록 프록시)
  2. 문단 리듬      : 문단당 문장수/자수 분포(mean/median/p90) · 3문장 초과 문단 비중 · 1문장 문단 비중
  3. 문장           : 평균 문장 길이 · sent_len_cv (ai_tell_profile 과 동일 추출 → 정합)
  4. LLM 틱(측정전용): ⓐ부정-교정 인접쌍 ⓑ판단유예 템플릿 ⓒ유사정밀 계측 ⓓ대사 직후 해설문 (회차당 건수)
  5. 화자 흐름 프록시: 무태그 연속 대사 라인 최대 run · 대사 라인의 발화자 표지(said-tag/행동비트) 동반율

교착어 substring 함정 회피: 틱 신호는 어간 ≥2음절의 종결형 endswith / 카운터 명사 경계 매칭만 사용
(단일 음절 needle 지양, 부분문자열 오탐 최소화 — MEMORY: korean-agglutination-substring-pitfall).

산출: tools/reports/st1_lightness_baseline.json / .md
사용: (app/ 에서) python tools/style_lightness_baseline.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

APP = pathlib.Path(__file__).resolve().parents[1]
REPORTS = APP / "tools" / "reports"

WORKS = [
    ("6cda5ee883e0", "봄이 오는 창가"),
    ("8f7e8cb966a2", "붕괴 각성"),
    ("7cba74b80209", "북부의 서리꽃"),
]

# ── 결정론 패턴 (닫힌 형태소/구두점 클래스 카운트 — 교정 규칙 아님, 측정 피처) ──────────────
_QUOTE = re.compile(r'["“](?P<c>[^"”\n]+)["”]')                 # 대사 스팬(직선·굽은 따옴표 모두)
_SENT_SPLIT = re.compile(r"\n+|(?<=[.!?…])\s+")                 # ai_tell_profile 과 동일한 문장 분리
_MARKUP_ONLY = re.compile(r"^[*\-–—_·•=~\s]+$")                 # 장면구분(***, ---) 등 마크업 전용 문단

# ⓑ 판단유예: "~라(고) 하기엔/부르기엔" 이중구문(하기엔/부르기엔 ≥3음절 앵커 — substring 안전)
_TIC_SUSPEND = re.compile(r"(?:라고|라)\s*(?:하기엔|하기에는|부르기엔|부르기에는)")
# ⓒ 유사정밀: 숫자(아라비아/토박이수) + 초/뼘/걸음/박자 카운터
_TIC_PSEUDO = re.compile(
    r"(?:\d+|[한두세석네넉]|다섯|여섯|일곱|여덟|아홉|열|스무|스물|반|몇)\s*(?:초|뼘|걸음|박자)")
# ⓓ 대사 직후 해설문: 발화를 해석·부연하는 종결 템플릿(≥3음절 종결형 — substring 안전)
_TIC_GLOSS = re.compile(
    r"(?:라는 뜻이었다|뜻이었다|의미였다|말이었다|소리였다|얘기였다|"
    r"질문이 아니었다|물음이 아니었다|대답이 아니었다|확인이었다|명령이었다|경고였다|다짐이었다)")


def _nws(s: str) -> int:
    """비공백 문자수(자수)."""
    return len(re.sub(r"\s", "", s))


def _pctl(xs: list[float], p: float) -> float:
    """선형보간 백분위수(numpy 기본과 동일) — 결정론."""
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _ai_tell_sents(body: str) -> list[str]:
    """ai_tell_profile 내부와 **동일한** 문장 추출 — sent_len_cv 정합 보장."""
    sents = [s.strip() for s in _SENT_SPLIT.split(body) if s and s.strip()]
    return [s for s in sents if len(s) >= 2]


def _para_sents(p: str) -> list[str]:
    """문단 1개의 문장 리스트(ai_tell 과 동일 분리기 — 최소길이 필터는 미적용: 짧은 1문장 문단 보존)."""
    parts = [s.strip() for s in _SENT_SPLIT.split(p) if s and s.strip()]
    return parts or ([p.strip()] if p.strip() else [])


def clean_paragraphs(text: str) -> list[str]:
    """본문 → 문단 리스트(제목 헤딩·&nbsp;·장면구분선·빈 문단 제거, 인라인 **/&nbsp; 정리)."""
    out: list[str] = []
    for raw in re.split(r"\n\s*\n", text or ""):
        p = raw.strip()
        if not p:
            continue
        if p.startswith("#"):                               # 마크다운 제목 헤딩
            continue
        if p.replace("&nbsp;", "").strip() == "":           # 빈 문단(&nbsp; only)
            continue
        if _MARKUP_ONLY.match(p):                            # 장면구분(***, ---) 등
            continue
        out.append(p.replace("&nbsp;", " ").replace("**", "").strip())
    return out


def lightness_metrics(text: str, roster: set[str] | None = None) -> dict:
    """회차 1개의 '가벼움' 문체 지표(결정론·LLM 0콜). 값은 상대추세용 — 절대판정 금지."""
    from novelcopilot.engine.quality_gates import ai_tell_profile   # 계보 정합(sent_len_cv 등)

    raw = text or ""
    paras = clean_paragraphs(raw)
    body_clean = "\n".join(paras)

    # ── 문단별 메타 ──
    meta = []                       # (is_dialogue, bare, nws, dlg_nws, n_sent)
    for p in paras:
        contents = [m.group("c") for m in _QUOTE.finditer(p)]
        is_dlg = bool(contents)
        dlg_nws = sum(_nws(c) for c in contents)
        residue = _QUOTE.sub(" ", p)                        # 대사 제거 후 잔여(지문/행동비트)
        bare = is_dlg and _nws(residue) < 3                 # 발화자 표지 없는 '맨' 대사 라인
        meta.append((is_dlg, bare, _nws(p), dlg_nws, len(_para_sents(p))))

    n_para = len(meta)
    n_dlg = sum(1 for m in meta if m[0])
    tot_nws = sum(m[2] for m in meta) or 0
    dlg_nws_tot = sum(m[3] for m in meta)

    # 1. 대사 비중
    dialogue_char_ratio = round(dlg_nws_tot / tot_nws, 3) if tot_nws else 0.0
    dialogue_para_ratio = round(n_dlg / n_para, 3) if n_para else 0.0
    max_narration_run = _max_run([not m[0] for m in meta])   # 무대사(지문) 연속 문단 최대 run

    # 2. 문단 리듬
    spp = [m[4] for m in meta]                               # 문단당 문장수
    cpp = [m[2] for m in meta]                               # 문단당 자수
    para_over3_ratio = round(sum(1 for s in spp if s > 3) / n_para, 3) if n_para else 0.0
    para_1sent_ratio = round(sum(1 for s in spp if s == 1) / n_para, 3) if n_para else 0.0

    # 3. 문장(ai_tell 정합)
    ats = _ai_tell_sents(raw)
    slen = [len(s) for s in ats]
    n_s = len(slen)
    s_mean = sum(slen) / n_s if n_s else 0.0
    s_sd = (sum((l - s_mean) ** 2 for l in slen) / n_s) ** 0.5 if n_s else 0.0
    sent_len_mean = round(s_mean, 1)
    sent_len_cv = round(s_sd / s_mean, 3) if s_mean else 0.0

    # 4. LLM 틱(측정 전용 — 회차당 건수)
    csents = _ai_tell_sents(body_clean)
    tic_neg = _neg_correction_pairs(csents)
    tic_suspend = len(_TIC_SUSPEND.findall(body_clean))
    tic_pseudo = len(_TIC_PSEUDO.findall(body_clean))
    tic_gloss = _dialogue_gloss(paras, meta)

    # 5. 화자 흐름 프록시
    max_bare_run = _max_run([m[1] for m in meta])           # 무태그 연속 대사 라인 최대 run
    attributed = sum(1 for m in meta if m[0] and not m[1])  # 발화자 표지 동반 대사 라인
    dialogue_attrib_ratio = round(attributed / n_dlg, 3) if n_dlg else 0.0

    return {
        "n_paragraph": n_para,
        "n_dialogue_paragraph": n_dlg,
        "n_sent": n_s,
        "n_char": tot_nws,                                   # 회차 자수(정제 본문 비공백 문자수)
        # 1
        "dialogue_char_ratio": dialogue_char_ratio,
        "dialogue_para_ratio": dialogue_para_ratio,
        "max_narration_run": max_narration_run,
        # 2
        "para_sent_mean": round(_avg(spp), 2),
        "para_sent_median": round(_pctl(spp, 50), 1),
        "para_sent_p90": round(_pctl(spp, 90), 1),
        "para_char_mean": round(_avg(cpp), 1),
        "para_char_median": round(_pctl(cpp, 50), 1),
        "para_char_p90": round(_pctl(cpp, 90), 1),
        "para_over3sent_ratio": para_over3_ratio,
        "para_1sent_ratio": para_1sent_ratio,
        # 3
        "sent_len_mean": sent_len_mean,
        "sent_len_cv": sent_len_cv,
        # 4
        "tic_neg_correction": tic_neg,
        "tic_judgment_suspend": tic_suspend,
        "tic_pseudo_precision": tic_pseudo,
        "tic_dialogue_gloss": tic_gloss,
        # 5
        "max_bare_dialogue_run": max_bare_run,
        "dialogue_attrib_ratio": dialogue_attrib_ratio,
        # 계보 정합(참조): ai_tell_profile 원본 — sent_len_cv 가 위와 동일함을 증거로 보관
        "ai_tell": ai_tell_profile(raw, roster),
    }


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _max_run(flags: list[bool]) -> int:
    """True 가 연속하는 최대 길이."""
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


def _strip_end(s: str) -> str:
    """말미 구두점·따옴표·공백 제거(종결형 endswith 판정용 — 경계 정규화)."""
    return re.sub(r'[\s.!?…"“”\'’—\-)\]]+$', "", s)


def _neg_correction_pairs(sents: list[str]) -> int:
    """ⓐ 부정-교정 인접쌍: '~아니었다.' 직후 '~였다/이었다.' (안티테제 틱).
    경계 가드: 말미 구두점 제거 후 종결형 endswith (부분문자열 오탐 회피)."""
    n = 0
    for a, b in zip(sents, sents[1:]):
        ea, eb = _strip_end(a), _strip_end(b)
        if ea.endswith("아니었다") and eb.endswith(("였다", "이었다")):
            n += 1
    return n


def _dialogue_gloss(paras: list[str], meta: list[tuple]) -> int:
    """ⓓ 대사 직후 해설문: 대사 문단 바로 뒤 지문 문단이 발화-해석 종결 템플릿과 일치."""
    n = 0
    for i in range(1, len(paras)):
        prev_is_dlg = meta[i - 1][0]
        cur_is_dlg = meta[i][0]
        if prev_is_dlg and not cur_is_dlg and _TIC_GLOSS.search(paras[i]):
            n += 1
    return n


# ── 집계 ──────────────────────────────────────────────────────────────────────
_SUMMARY_KEYS = [
    "n_paragraph", "n_dialogue_paragraph", "n_sent", "n_char",
    "dialogue_char_ratio", "dialogue_para_ratio", "max_narration_run",
    "para_sent_mean", "para_sent_median", "para_sent_p90",
    "para_char_mean", "para_char_median", "para_char_p90",
    "para_over3sent_ratio", "para_1sent_ratio",
    "sent_len_mean", "sent_len_cv",
    "tic_neg_correction", "tic_judgment_suspend", "tic_pseudo_precision", "tic_dialogue_gloss",
    "max_bare_dialogue_run", "dialogue_attrib_ratio",
]


def summarize(chapter_metrics: list[dict]) -> dict:
    """회차 지표들의 작품(또는 코퍼스) 평균 — 결측 없이 전 회차 mean."""
    out = {}
    for k in _SUMMARY_KEYS:
        vals = [m[k] for m in chapter_metrics if k in m]
        out[k] = round(_avg(vals), 3) if vals else 0.0
    return out


# ── 지표 정의(문서화) ──────────────────────────────────────────────────────────
DEFINITIONS = {
    "n_char": ("회차 자수(정제 본문의 비공백 문자수 — 제목·마크업·장면구분선 제외)", "가벼움 방향 없음(분량 참조)"),
    "dialogue_char_ratio": ("대사(따옴표 스팬) 비공백 문자 / 전체 비공백 문자", "높을수록 가벼움"),
    "dialogue_para_ratio": ("대사를 1개 이상 포함한 문단 비중", "높을수록 가벼움"),
    "max_narration_run": ("무대사(지문) 연속 문단 최대 run — 설명/묘사 블록 프록시", "낮을수록 가벼움"),
    "para_sent_mean": ("문단당 문장 수 평균", "낮을수록 가벼움(모바일 리듬)"),
    "para_sent_median": ("문단당 문장 수 중앙값", "낮을수록 가벼움"),
    "para_sent_p90": ("문단당 문장 수 p90(긴 문단 꼬리)", "낮을수록 가벼움"),
    "para_char_mean": ("문단당 자수 평균", "낮을수록 가벼움"),
    "para_char_median": ("문단당 자수 중앙값", "낮을수록 가벼움"),
    "para_char_p90": ("문단당 자수 p90", "낮을수록 가벼움"),
    "para_over3sent_ratio": ("3문장 초과 문단 비중(모바일 가독 기준)", "낮을수록 가벼움"),
    "para_1sent_ratio": ("1문장 문단 비중", "높을수록 가벼움(짧은 호흡)"),
    "sent_len_mean": ("평균 문장 길이(자) — ai_tell 문장추출 동일", "낮을수록 가벼움"),
    "sent_len_cv": ("문장 길이 변동계수 std/mean — ai_tell_profile 과 동일값(정합)", "가벼움 방향 없음(변주 지표)"),
    "tic_neg_correction": ("ⓐ '~아니었다.' 직후 '~였다/이었다.' 부정-교정 인접쌍(회차당 건수)", "낮을수록 좋음(측정전용)"),
    "tic_judgment_suspend": ("ⓑ '~라(고) 하기엔/부르기엔' 판단유예 이중구문(회차당 건수)", "낮을수록 좋음(측정전용)"),
    "tic_pseudo_precision": ("ⓒ 숫자+초/뼘/걸음/박자 유사정밀 계측(회차당 건수)", "낮을수록 좋음(측정전용)"),
    "tic_dialogue_gloss": ("ⓓ 대사 직후 해설문(뜻이었다/질문이 아니었다 류, 회차당 건수)", "낮을수록 좋음(측정전용)"),
    "max_bare_dialogue_run": ("무태그 연속 대사 라인 최대 run — 화자 혼동 위험 프록시", "낮을수록 안전"),
    "dialogue_attrib_ratio": ("대사 라인 중 발화자 표지(said-tag/행동비트) 동반 비율", "맥락 의존(추세로만)"),
}

LIMITATIONS = [
    "절대 기준선 없음 — 실제 웹소설 시장 코퍼스(상용 연재작)를 대조군으로 확보하지 못했다. "
    "모든 수치는 이 3작 사이/작품 자기 회차 간 **상대 비교용**이며, '가볍다/무겁다' 이진 판정에 쓰면 안 된다.",
    "판정기가 아니다 — 임계·라벨·자동 재작성 트리거 0 (ai_tell·G4·no-whack-a-mole 원칙). 작가가 추세를 보고 판단한다.",
    "대사 비중은 장르 교란이 크다 — 액션/전투 회차는 지문이 늘고, 대화 회차는 대사가 는다. 단일 회차 스파이크 금지.",
    "sent_len_cv 는 액션/대사 위주 회차에서 인간도 의도적으로 균일해진다(ai_tell 문서 경고) — 단독 판정 금물.",
    "틱 카운트는 무사전 근사다 — 형태소분석기 없이 종결형 endswith/카운터 경계로 잡아 오탐/미탐이 존재(예: 종속절 "
    "'아니었다는데'는 미포함). 추세 신호로만 읽고, 개별 문장은 원문 확인 필요.",
    "유사정밀(ⓒ)의 토박이수 needle('세','반' 등)은 카운터 명사 경계(초/뼘/걸음/박자)로 제한했으나 드물게 오탐 가능.",
    "문단 리듬은 저장 본문의 조판(빈 줄=문단 경계)에 의존한다 — 조판 규칙이 바뀌면 재보정 필요.",
]


def load_chapters(pid: str) -> tuple[dict, list[dict]]:
    """핫패스 JSON 직접 파싱(읽기 전용 — 저장 API 불사용, rag 사이드카 미로드)."""
    p = json.loads((APP / "data" / "projects" / f"{pid}.json").read_text(encoding="utf-8"))
    chs = sorted(p["chapters"], key=lambda c: c["chapter"])
    chs = [c for c in chs if c.get("status") == "FINALIZED"]
    return p, chs


def build_roster(state: dict) -> set[str]:
    """copilot._recompute_ai_tell 와 동일 구성(엔티티명 + bible 키워드) — ai_tell 정합용."""
    roster: set[str] = set()
    for e in state.get("runtime_entities") or []:
        nm = (e.get("name") or "").strip()
        if nm:
            roster.add(nm)
    bible = state.get("bible") or {}
    entries = bible.get("entries") if isinstance(bible, dict) else None
    for ent in entries or []:
        for k in (ent.get("keywords") or []):
            if k:
                roster.add(k)
    return roster


def build_report() -> dict:
    works = {}
    all_chapter_metrics: list[dict] = []
    for pid, title in WORKS:
        state, chs = load_chapters(pid)
        roster = build_roster(state)
        world = state.get("world") or {}
        rows = []
        for c in chs:
            m = lightness_metrics(c.get("text") or "", roster)
            m_out = {"chapter": c["chapter"], **{k: m[k] for k in _SUMMARY_KEYS}}
            rows.append(m_out)
            all_chapter_metrics.append(m)
        works[pid] = {
            "title": world.get("title") or title,
            "genre": world.get("genre") or "",
            "n_chapters": len(chs),
            "summary": summarize(all_chapter_metrics[-len(chs):]),
            "chapters": rows,
        }
    combined = {"n_chapters": len(all_chapter_metrics), "summary": summarize(all_chapter_metrics)}
    return {
        "report": "ST-1 웹소설 '가벼움' 결정론 문체 지표 baseline",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "결정론·LLM 0콜·읽기전용 — ai_tell_profile 계보의 측정 피처(판정기 아님)",
        "definitions": {k: {"desc": d, "lightness_direction": dr} for k, (d, dr) in DEFINITIONS.items()},
        "limitations": LIMITATIONS,
        "works": works,
        "combined": combined,
    }


# ── raw txt 입력 모드 (ST-1b: 파일/디렉토리 — 프로젝트 모드와 독립·무회귀) ──────────
def _ep_key(stem: str) -> int:
    """파일명(확장자 제외)에서 첫 정수 그룹을 회차 번호로 사용(EP.0/EP.1/1화… 자연 정렬)."""
    m = re.search(r"\d+", stem)
    return int(m.group()) if m else 0


def load_txt_file(path: pathlib.Path) -> tuple[str, str]:
    """raw txt 1개 → (제목, 본문). 첫 비어있지 않은 줄을 제목으로 분리(프로젝트 저장본이 제목을 본문에서
    떼는 것과 정합 — 제목이 1문장 문단으로 오염되지 않게). 개행은 \\n 로 정규화."""
    raw = pathlib.Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    title, start = "", 0
    for i, ln in enumerate(lines):
        if ln.strip():
            title, start = ln.strip(), i + 1
            break
    return title, "\n".join(lines[start:]).strip()


def load_txt_chapters(path) -> tuple[str, list[dict]]:
    """파일 또는 디렉토리 경로 → (작품 id, 회차 dict 리스트). 디렉토리면 *.txt 를 회차번호로 정렬."""
    p = pathlib.Path(path)
    if p.is_file():
        title, body = load_txt_file(p)
        return p.stem, [{"chapter": _ep_key(p.stem), "file": p.name, "title": title, "text": body}]
    files = sorted(p.glob("*.txt"), key=lambda f: _ep_key(f.stem))
    chapters = [
        {"chapter": _ep_key(f.stem), "file": f.name, **dict(zip(("title", "text"), load_txt_file(f)))}
        for f in files
    ]
    return p.name, chapters


def build_txt_report(paths) -> dict:
    """raw txt 파일/디렉토리 리스트 → 프로젝트 모드와 동일 스키마의 리포트(roster 없음 → ai_tell roster=None).
    작품 자기 코퍼스 대비 상대추세 원칙 동일. 판정기 아님."""
    works: dict = {}
    all_metrics: list[dict] = []
    for path in paths:
        wid, chapters = load_txt_chapters(path)
        rows, wmetrics = [], []
        for c in chapters:
            m = lightness_metrics(c.get("text") or "", None)
            rows.append({"chapter": c["chapter"], "file": c.get("file"), "title": c.get("title"),
                         **{k: m[k] for k in _SUMMARY_KEYS}})
            wmetrics.append(m)
            all_metrics.append(m)
        works[wid] = {
            "title": wid,
            "genre": "(raw txt)",
            "n_chapters": len(chapters),
            "summary": summarize(wmetrics),
            "chapters": rows,
        }
    combined = {"n_chapters": len(all_metrics), "summary": summarize(all_metrics)}
    return {
        "report": "ST-1b 레퍼런스 실작품 '가벼움' 문체 실측 (raw txt 입력)",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "결정론·LLM 0콜·읽기전용 — raw txt 파일/디렉토리 입력(roster 없음, 제목 줄 분리)",
        "definitions": {k: {"desc": d, "lightness_direction": dr} for k, (d, dr) in DEFINITIONS.items()},
        "limitations": LIMITATIONS,
        "works": works,
        "combined": combined,
    }


# ── 마크다운 렌더 ──────────────────────────────────────────────────────────────
_TABLE_KEYS = [
    "dialogue_char_ratio", "dialogue_para_ratio", "max_narration_run",
    "para_sent_mean", "para_sent_p90", "para_char_mean", "para_char_p90",
    "para_over3sent_ratio", "para_1sent_ratio", "sent_len_mean", "sent_len_cv",
    "tic_neg_correction", "tic_judgment_suspend", "tic_pseudo_precision", "tic_dialogue_gloss",
    "max_bare_dialogue_run", "dialogue_attrib_ratio",
]


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def render_md(rep: dict) -> str:
    L: list[str] = []
    L.append(f"# {rep['report']}")
    L.append("")
    L.append(f"> 생성: {rep['generated_at']} · {rep['method']}")
    L.append(">")
    L.append("> **판정기 아님.** 아래 수치는 전부 상대 추세용 advisory 다 — 절대 임계·이진 라벨·자동 교정 0. "
             "작가가 추세를 보고 판단한다(ai_tell·G4·no-whack-a-mole 원칙 정합).")
    L.append("")

    # 지표 정의
    L.append("## 지표 정의")
    L.append("")
    L.append("| 키 | 정의 | 가벼움 방향 |")
    L.append("|---|---|---|")
    for k in _TABLE_KEYS:
        d, dr = DEFINITIONS[k]
        L.append(f"| `{k}` | {d} | {dr} |")
    L.append("")

    # 작품별 종합표 + 3작 종합
    L.append("## 작품별 종합 (24화 평균) + 3작 종합")
    L.append("")
    header = ["지표"] + [rep["works"][pid]["title"] for pid, _ in WORKS] + ["3작 종합(72화)"]
    L.append("| " + " | ".join(header) + " |")
    L.append("|" + "---|" * len(header))
    for k in _TABLE_KEYS:
        cells = [f"`{k}`"]
        for pid, _ in WORKS:
            cells.append(_fmt(rep["works"][pid]["summary"][k]))
        cells.append(_fmt(rep["combined"]["summary"][k]))
        L.append("| " + " | ".join(cells) + " |")
    L.append("")
    L.append(f"작품 식별: " + " · ".join(
        f"{title}(`{pid}`, {rep['works'][pid]['genre']})" for pid, title in WORKS))
    L.append("")

    # 한계
    L.append("## 한계 (정직 기록)")
    L.append("")
    for lim in rep["limitations"]:
        L.append(f"- {lim}")
    L.append("")

    # 회차별 부록
    L.append("## 부록: 회차별 원시값")
    L.append("")
    for pid, title in WORKS:
        w = rep["works"][pid]
        L.append(f"### {title} (`{pid}`)")
        L.append("")
        cols = ["화", "dlg_char", "dlg_para", "narr_run", "p_sent_mean", "p_char_mean",
                ">3문장", "1문장", "sent_mean", "sent_cv",
                "틱ⓐ", "틱ⓑ", "틱ⓒ", "틱ⓓ", "bare_run", "attrib"]
        L.append("| " + " | ".join(cols) + " |")
        L.append("|" + "---|" * len(cols))
        for r in w["chapters"]:
            L.append("| " + " | ".join(_fmt(x) for x in [
                r["chapter"], r["dialogue_char_ratio"], r["dialogue_para_ratio"], r["max_narration_run"],
                r["para_sent_mean"], r["para_char_mean"], r["para_over3sent_ratio"], r["para_1sent_ratio"],
                r["sent_len_mean"], r["sent_len_cv"],
                r["tic_neg_correction"], r["tic_judgment_suspend"], r["tic_pseudo_precision"],
                r["tic_dialogue_gloss"], r["max_bare_dialogue_run"], r["dialogue_attrib_ratio"],
            ]) + " |")
        L.append("")
    return "\n".join(L)


def _run_project() -> int:
    """기존 프로젝트 3작 baseline 모드 (무회귀 — 인자 없이 호출 시 기본)."""
    rep = build_report()
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "st1_lightness_baseline.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORTS / "st1_lightness_baseline.md").write_text(render_md(rep), encoding="utf-8")
    for pid, title in WORKS:
        s = rep["works"][pid]["summary"]
        print(f"{title:8s} dlg_char={s['dialogue_char_ratio']} narr_run={s['max_narration_run']} "
              f"p_sent={s['para_sent_mean']} >3={s['para_over3sent_ratio']} "
              f"cv={s['sent_len_cv']} ticⓐ={s['tic_neg_correction']} ticⓓ={s['tic_dialogue_gloss']}")
    print("report:", REPORTS / "st1_lightness_baseline.json")
    print("report:", REPORTS / "st1_lightness_baseline.md")
    return 0


def _run_txt(paths: list[str], out: str | None) -> int:
    """raw txt 파일/디렉토리 모드 — JSON 덤프 + 콘솔 요약(회차당 자수 포함)."""
    rep = build_txt_report(paths)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out_path = pathlib.Path(out) if out else REPORTS / "st1b_reference_raw.json"
    out_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    for wid, w in rep["works"].items():
        s = w["summary"]
        print(f"{wid[:26]:26s} n_ch={w['n_chapters']} n_char={s['n_char']:.0f} "
              f"dlg_char={s['dialogue_char_ratio']} narr_run={s['max_narration_run']} "
              f"p_sent={s['para_sent_mean']} >3={s['para_over3sent_ratio']} 1s={s['para_1sent_ratio']} "
              f"cv={s['sent_len_cv']} ticⓐ={s['tic_neg_correction']} ticⓓ={s['tic_dialogue_gloss']}")
    print("report:", out_path)
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(
        description="ST-1 '가벼움' 문체 지표 — 인자 없으면 프로젝트 3작 baseline, "
                    "경로 주면 raw txt 파일/디렉토리 모드(ST-1b).")
    ap.add_argument("paths", nargs="*", help="측정할 txt 파일 또는 디렉토리(작품 폴더). 미지정 시 프로젝트 모드.")
    ap.add_argument("--out", default=None, help="txt 모드 JSON 출력 경로(기본 reports/st1b_reference_raw.json)")
    args = ap.parse_args(argv)
    return _run_txt(args.paths, args.out) if args.paths else _run_project()


if __name__ == "__main__":
    raise SystemExit(main())
