# -*- coding: utf-8 -*-
"""HZ-1 ① 스타일 지각 판정 — 회차 프로즈를 '낭독 체감'으로 읽고 N-4(문말 run) 수리 필요 여부·인용을 낸다.

배경(사용자 결정 2026-07-15): 휴머나이즈 상시 발동은 화당 10~13만 토큰을 쓰고, 결정론 임계 발동은
  "두더지잡기"(임계 튜닝의 함정)라 폐기. 대신 매화 무조건(+1콜/화) 판정자가 회차 프로즈를 읽고 낭독
  체감으로 판정한다 — 사용자 노선 "무조건 검증". 결정론 계측(무중단 동일 어미 run)은 [참고 자료]로만
  주입한다(후보 위치 안내일 뿐, 판정 기준은 낭독 체감이지 임계가 아님을 프롬프트에 명시).

원칙(cold_read.py 계보):
  · gen≠judge — 판정 모델은 호출부가 config style_judge_model(교차 벤더 기본 openai:gpt-5.2-chat-latest)로
    라우팅해 provider 를 주입한다(이 모듈은 provider 종류·라우팅을 모른다 — 레이어 규율).
  · measure-then-cite — 문제 구간은 '원문 정확 인용' 의무(수술 대상 위치 매칭의 근거·게이트 관행).
  · 판정 실패(파싱 불가·콜 실패) → None(비차단·보수). 호출부가 수리 스킵 + 기록(결측 정직·은폐 금지).
  · advisory·무강제 — 회차 확정을 막지 않는다. 판정은 게이트가 아니라 편집 후보 안내다.

**엔진 의존성0 불변**: novelcopilot/(엔진)은 tools 에 *로드 타임* 의존이 없다. 이 모듈의 결정론 계측
  참고 블록(build_reference_block)은 tools.kiwi_metrics.uninterrupted_ending_runs 를 함수 *안*에서
  lazy·try/except 로 부른다 — 계측 불가 시 참고 블록만 비고(판정은 프로즈만으로 계속·결측 정직).
"""
from __future__ import annotations
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)

# 참고 자료 블록에 실을 무중단 run 목록 상한(비용/노이즈 가드 — 최악 run 우선 몇 개면 위치 안내로 충분).
_REF_MAX_RUNS = 8
# 참고 자료의 run 판정 임계(무중단 동일 어미 run 최소 문장 수) — kiwi_metrics.uninterrupted_ending_runs 기본과 동일.
_REF_RUN_THRESHOLD = 6


def build_reference_block(text: str, *, threshold: int = _REF_RUN_THRESHOLD,
                          max_runs: int = _REF_MAX_RUNS) -> dict:
    """결정론 계측 요약(무중단 동일 어미 run 목록) — 판정 프롬프트의 [참고 자료] 블록 원자료.

    반환: {"runs": [{"ending_key", "n_sent", "first_sentence"}…], "backend": "kiwi"|"unavailable", "n_runs"}.
    tools.kiwi_metrics 부재/실패 시 runs=[]·backend="unavailable"(판정은 프로즈만으로 계속 — 결측 정직).
    first_sentence = 그 run 첫 문장 앞부분(위치 안내용·정확 인용 아님 — 인용은 판정자가 프로즈에서 직접).
    무강제: 값(위치·길이)만 — 임계·판정·자동교정 0(이 목록은 '후보 위치 안내'이지 수리 근거가 아님)."""
    t = text or ""
    try:
        from tools.kiwi_metrics import uninterrupted_ending_runs
        raw = uninterrupted_ending_runs(t, threshold=threshold)
    except Exception:
        return {"runs": [], "backend": "unavailable", "n_runs": 0}
    # 최악(긴 run) 우선 상한 — n_sent 내림차순 → 위치(char_start) 안정 정렬.
    runs = sorted(raw or [], key=lambda r: (-int(r.get("n_sent") or 0), int(r.get("char_start") or 0)))
    out = []
    for r in runs[:max_runs]:
        cs = int(r.get("char_start") or 0)
        ce = int(r.get("char_end") or 0)
        # 첫 문장 앞부분(위치 안내) — 그 run 창의 시작 ~60자(줄바꿈 정규화). 정확 인용 의무는 판정자 몫.
        head = t[cs:ce][:60].replace("\n", " ").strip()
        out.append({"ending_key": r.get("key"), "n_sent": r.get("n_sent"), "first_sentence": head})
    return {"runs": out, "backend": "kiwi", "n_runs": len(raw or [])}


def _format_reference(ref: dict) -> str:
    """참고 자료 블록을 판정 프롬프트에 실을 텍스트로 — run 목록(어미·연속 문장 수·시작 위치). 결측이면 명시."""
    runs = (ref or {}).get("runs") or []
    if not runs:
        return "[참고 자료] 결정론 계측 없음(무중단 동일 어미 run 미검출 또는 계측 불가). 프로즈만으로 판정하라."
    lines = ["[참고 자료 — 후보 위치 안내일 뿐, 판정 기준은 낭독 체감이다]",
             "결정론 계측이 잡은 '무중단 동일 어미 run'(같은 종결형이 대사 없이 연달아 이어진 구간):"]
    for i, r in enumerate(runs, 1):
        key = r.get("ending_key") or "?"
        n = r.get("n_sent")
        head = r.get("first_sentence") or ""
        lines.append(f"  {i}) 어미 '{key}' × {n}문장 연속 — 시작: \"{head}…\"")
    lines.append("이 목록은 눈여겨볼 위치를 알려줄 뿐이다. 실제로 낭독 시 단조롭게 들리는지는 네가 판정한다 "
                 "— 목록에 없어도 걸리면 짚고, 목록에 있어도 자연스러우면 넘겨라.")
    return "\n".join(lines)


# HZ-2: 회차 간 반복 모티프 후보 목록 상한(참고 자료 노이즈·비용 가드 — 상위 몇 개면 위치 안내로 충분).
_REF_MAX_MOTIFS = 8


def _format_motif_reference(motif_candidates) -> str:
    """N-3 모티프 후보 구절 목록을 판정 프롬프트의 [참고 자료] 블록으로 — 회차 간 반복 구절(표층 문구).

    motif_candidates: [str …] 또는 [{"quote"/"surface"/"text"…}] — 호출부가 detect_chapter 의 N-3 findings 에서
      추린 반복 구절(표층 문구)만. N-4 run reference 와 동일 관례: '후보 위치 안내일 뿐, 판정 기준은 낭독
      체감'을 명시(임계·라벨 아님). 빈 목록/미전달이면 빈 문자열(프롬프트 바이트 최소 변화·하위호환)."""
    quotes: list[str] = []
    for m in (motif_candidates or []):
        if isinstance(m, str):
            q = m.strip()
        elif isinstance(m, dict):
            q = (m.get("quote") or m.get("surface") or m.get("text") or "").strip()
        else:
            q = ""
        if q and q not in quotes:
            quotes.append(q)
    if not quotes:
        return ""
    lines = ["[참고 자료 — 회차 간 반복 모티프 후보. 후보 위치 안내일 뿐, 판정 기준은 낭독 체감이다]",
             "결정론 계측이 잡은 '회차를 넘어 되풀이되는 구절'(같은 비유·표현이 여러 화에 걸쳐 재출현):"]
    for i, q in enumerate(quotes[:_REF_MAX_MOTIFS], 1):
        lines.append(f"  {i}) \"{q}\"")
    lines.append("이 목록은 눈여겨볼 구절을 알려줄 뿐이다. 실제로 낭독 시 같은 비유·구절이 기계처럼 되풀이돼 "
                 "틱으로 들리는지는 네가 판정한다 — 목록에 없어도 걸리면 짚고, 목록에 있어도 자연스러우면 넘겨라.")
    return "\n".join(lines)


# 판정 시스템 프롬프트(measure-then-cite·낭독 체감 기준·pink-elephant 최소화 — 나쁜 예문 나열 없음).
_JUDGE_SYSTEM = (
    "너는 웹소설 원고를 소리 내어 읽는 교정 편집자다. 이 회차 본문을 낭독한다고 상상하고, 문장 끝맺음(어미)이 "
    "같은 형태로 기계처럼 반복돼 리듬이 단조롭게 들리는 구간이 있는지 '체감'으로 판정하라. "
    "판정 기준은 오직 낭독 체감이다 — 아래 [참고 자료]의 계측 수치는 눈여겨볼 위치를 알려줄 뿐, 수치가 크다고 "
    "무조건 문제인 것도, 수치가 없다고 무조건 괜찮은 것도 아니다. 자연스럽게 읽히면 넘기고, 걸리면 짚어라.\n"
    "문제 구간은 반드시 본문을 '그대로 정확히 인용'하라(measure-then-cite — 인용이 본문과 한 글자라도 다르면 "
    "그 구간은 무시된다). 인용은 문제가 시작되는 한 문장~서너 문장으로 짧게 잡아라(구간 전체 복붙 금지).\n"
    "JSON 으로만 답하라: "
    '{"needs_repair":true,'
    '"spans":[{"quote":"문제가 되는 구간의 본문 정확 인용","why":"왜 단조롭게 들리는지 한 줄"}],'
    '"reason":"전체 판정 사유 한 줄"}. '
    "단조로운 구간이 없으면 needs_repair 는 false, spans 는 빈 배열로 하라."
)

# HZ-2 모티프 판정 지시(시스템 프롬프트 additive — 모티프 후보가 있을 때만 이어붙인다·긍정형·pink-elephant 최소).
#   문말(N-4)과 별개 축: 같은 비유·구절이 여러 화에 걸쳐 기계처럼 되풀이돼 '틱'으로 들리는지를 함께 판정한다.
#   motif_spans 는 그런 구절의 원문 정확 인용(measure-then-cite) — 자연스러우면 빈 배열. 후보는 안내일 뿐 기준은 체감.
_JUDGE_MOTIF_CLAUSE = (
    "\n또한 이 회차를 낭독할 때, 같은 비유·표현·구절이 (특히 앞선 회차들에서 이미 여러 번 나온 것이) 기계처럼 "
    "되풀이돼 '틱'으로 들리는 구간이 있으면 그것도 짚어라(문장 끝맺음과는 별개 축이다). 아래 [참고 자료]의 "
    "모티프 후보는 눈여겨볼 구절을 알려줄 뿐, 자연스럽게 스며 읽히면 넘기고 기계 반복으로 걸리면 짚어라.\n"
    "고칠 가치가 있는 반복 구절은 위 규칙과 동일하게 본문을 '그대로 정확히 인용'하라. JSON 에 "
    '"motif_spans":[{"quote":"기계 반복으로 들리는 모티프 구절의 본문 정확 인용","why":"왜 틱으로 들리는지 한 줄"}] '
    "필드를 함께 담아라 — 되풀이가 자연스러우면 motif_spans 는 빈 배열로 하라."
)


def _normalize_spans(raw) -> list[dict]:
    """판정 spans/motif_spans 정규화 — [{quote, why}] (quote 필수·빈 항목 제거). 문자열 항목도 허용(why="")."""
    out: list[dict] = []
    for sp in (raw or []):
        if isinstance(sp, dict):
            q = (sp.get("quote") or "").strip()
            w = (sp.get("why") or "").strip()
            if q:
                out.append({"quote": q, "why": w})
        elif isinstance(sp, str) and sp.strip():
            out.append({"quote": sp.strip(), "why": ""})
    return out


@promptlog.stage("style_judge")
def judge_style(provider, text: str, *, genre: str = "",
                threshold: int = _REF_RUN_THRESHOLD, max_runs: int = _REF_MAX_RUNS,
                motif_candidates=None) -> dict | None:
    """회차 프로즈를 낭독 체감으로 읽고 N-4 문말 run 수리 필요 여부·인용을 낸다(매화 무조건·+1콜). 실패 시 None.

    provider: 판정 provider(호출부가 style_judge_model 로 라우팅 — gen≠judge). 이 모듈은 종류를 모른다.
    text: 회차 프로즈(최종화 후·수리 전). genre: 장르 라벨(프레이밍·advisory).
    motif_candidates(HZ-2·선택): 회차 간 반복 모티프 후보 구절 목록([str] 또는 [{"quote"/"surface"…}]). 호출부가
      detect_chapter 의 N-3 findings 에서 반복 구절(표층 문구)만 추려 넘긴다. 같은 판정 콜에서 모티프도 판정
      (추가 콜 0). N-4 run reference 와 동일 관례로 [참고 자료]에 동봉('후보 위치 안내일 뿐, 판정 기준은 체감').
      미전달(None/빈)이면 기존 동작(문말만)·프롬프트 바이트 최소 변화(하위호환).

    산출 JSON:
      · needs_repair : bool — 이 회차에 문말 단조 수리가 필요한가(낭독 체감)
      · spans        : [{quote, why}] — 문제 구간 원문 정확 인용(measure-then-cite) + 사유 한 줄
      · motif_spans  : [{quote, why}] — 기계 반복으로 읽혀 고칠 가치가 있는 모티프 구절의 원문 정확 인용(HZ-2).
                       모티프 후보 미전달이거나 반환에 필드 없으면 빈 배열(하위호환·자연스러우면 빈 배열).
      · reason       : str — 전체 판정 사유 한 줄
    반환: 위 필드 + reference(주입한 계측 참고 블록·정직 기록). 본문 전무·파싱 실패·콜 실패 시 None(보수).
    """
    prose = (text or "").strip()
    if not prose:
        return None
    ref = build_reference_block(text, threshold=threshold, max_runs=max_runs)
    ref_text = _format_reference(ref)
    # HZ-2: 모티프 후보가 있으면 시스템 프롬프트에 모티프 판정 지시를 additive 로 이어붙이고 [참고 자료]에 동봉.
    #   미전달(빈)이면 system=_JUDGE_SYSTEM·user 도 문말 참고 자료만 → 프롬프트 바이트 동일(하위호환).
    motif_ref_text = _format_motif_reference(motif_candidates)
    system = _JUDGE_SYSTEM + (_JUDGE_MOTIF_CLAUSE if motif_ref_text else "")
    user = f"[장르] {genre or '웹소설'}\n\n{ref_text}\n\n"
    if motif_ref_text:
        user += motif_ref_text + "\n\n"
    user += f"[본문(이 회차 전체)]\n{prose}"
    try:
        r = provider.chat_json(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            temperature=0.2)
    except Exception:
        return None
    if not isinstance(r, dict):
        return None
    # spans/motif_spans 정규화(quote/why 문자열·빈 항목 제거 — 정확 인용 의무는 위치 매칭 단계가 검증).
    spans = _normalize_spans(r.get("spans"))
    motif_spans = _normalize_spans(r.get("motif_spans"))
    needs = bool(r.get("needs_repair", bool(spans)))
    return {"needs_repair": needs, "spans": spans, "motif_spans": motif_spans,
            "reason": (r.get("reason") or "").strip(),
            "reference": ref}


# ── VJ-1: 검증용 낭독 리듬 판정(문말·문단 호흡·문장 길이 — HZ-1 계보 확장) ──────────────
# 배경(사용자 지시 2026-08-13): 판정은 무조건 LLM 정독으로, 수치는 참고 원자료로만. 생성 경로의
#   judge_style(N-4 문말 한정·수리 집도용)은 그대로 두고, 검증 리포트가 부르는 낭독 판정은 리듬
#   전반을 한 콜로 듣는다. 판정기는 생산자가 아니므로 단일 최종화 관문(ST-14)과 무관하다
#   (집도는 여전히 revise/humanize 단일 경로).

def build_paragraph_reference(text: str) -> dict:
    """문단 짜임 결정론 계측(LLM 0·의존성 0) — 참고 원자료: 서술 문단 문장수 분포·2문장 문단 최장 연속.

    무강제: 값만 — 임계·판정·자동교정 0. 낭독 판정 프롬프트의 [참고 자료] 블록 원자료다."""
    import re as _re
    paras = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    narr = [p for p in paras if not p.startswith(('"', '“'))]

    def _n_sent(p: str) -> int:
        return len([x for x in _re.split(r"(?<=[.!?…])\s+", p) if x.strip()])

    dist: dict[int, int] = {}
    for p in narr:
        k = min(_n_sent(p), 4)
        dist[k] = dist.get(k, 0) + 1
    best = cur = 0
    for p in paras:
        if p.startswith(('"', '“')):
            cur = 0
            continue
        if _n_sent(p) == 2:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    n = len(narr)
    return {"n_narr_paras": n, "dist": {str(k): v for k, v in sorted(dist.items())},
            "two_sent_share": round(dist.get(2, 0) / n, 3) if n else 0.0,
            "max_two_sent_streak": best}


def _format_paragraph_reference(pref: dict) -> str:
    """문단 짜임 분포를 판정 프롬프트의 [참고 자료] 한 줄로 — 분포 요약일 뿐 판정 기준 아님을 명시."""
    d = (pref or {}).get("dist") or {}
    n = (pref or {}).get("n_narr_paras") or 0
    if not n:
        return ""
    body = " · ".join(f"{k}문장 {v}개" for k, v in d.items())
    return ("[참고 자료 — 문단 짜임 분포 요약. 판정 기준은 낭독 체감이다]\n"
            f"서술 문단 {n}개: {body} (2문장 문단 비중 {int(round((pref.get('two_sent_share') or 0) * 100))}%"
            f" · 2문장 문단 최장 연속 {pref.get('max_two_sent_streak')})")


# 낭독 리듬 판정 시스템 프롬프트 — 긍정형·체감 기준·measure-then-cite(_JUDGE_SYSTEM 관례 동형).
_RHYTHM_SYSTEM = (
    "너는 웹소설 원고를 소리 내어 읽는 교정 편집자다. 이 회차 본문을 낭독한다고 상상하고, 리듬이 "
    "단조롭게 들리는 구간이 있는지 '체감'으로 판정하라. 세 갈래를 함께 듣는다.\n"
    "① 문장 끝맺음 — 같은 꼴의 종결이 기계처럼 이어져 박자가 하나로 고이는가.\n"
    "② 문단 호흡 — 비슷한 길이·짜임의 문단이 장면 성격과 무관하게 계속 되풀이되는가. 장면이 요구하는 "
    "호흡(눌러 쓰는 긴 문단, 끊어 치는 짧은 문단)이 살아 있으면 자연스러운 것이다.\n"
    "③ 문장 길이 — 비슷한 길이의 문장이 같은 박자로 이어지는가.\n"
    "판정 기준은 오직 낭독 체감이다 — 아래 [참고 자료]의 수치는 눈여겨볼 위치·분포를 알려줄 뿐, 수치가 "
    "크다고 무조건 문제인 것도, 없다고 무조건 괜찮은 것도 아니다. 자연스럽게 읽히면 넘기고, 걸리면 짚어라.\n"
    "문제 구간은 반드시 본문을 '그대로 정확히 인용'하라(인용이 본문과 한 글자라도 다르면 그 구간은 "
    "무시된다). 인용은 문제가 시작되는 한 문장~서너 문장으로 짧게 잡아라(구간 전체 복붙 금지).\n"
    "JSON 으로만 답하라: "
    '{"needs_repair":true,'
    '"spans":[{"quote":"문제가 되는 구간의 본문 정확 인용","axis":"문말|문단|문장길이",'
    '"why":"왜 단조롭게 들리는지 한 줄"}],'
    '"reason":"전체 판정 사유 한 줄"}. '
    "단조로운 구간이 없으면 needs_repair 는 false, spans 는 빈 배열로 하라."
)

_RHYTHM_AXES = ("문말", "문단", "문장길이")


def _normalize_rhythm_spans(raw) -> list[dict]:
    """리듬 판정 spans 정규화 — [{quote, axis, why}] (quote 필수·axis 는 허용값 밖이면 빈 문자열)."""
    out: list[dict] = []
    for sp in (raw or []):
        if isinstance(sp, dict):
            q = (sp.get("quote") or "").strip()
            if not q:
                continue
            ax = (sp.get("axis") or "").strip()
            out.append({"quote": q, "axis": ax if ax in _RHYTHM_AXES else "",
                        "why": (sp.get("why") or "").strip()})
        elif isinstance(sp, str) and sp.strip():
            out.append({"quote": sp.strip(), "axis": "", "why": ""})
    return out


@promptlog.stage("style_rhythm")
def judge_rhythm(provider, text: str, *, genre: str = "",
                 threshold: int = _REF_RUN_THRESHOLD, max_runs: int = _REF_MAX_RUNS) -> dict | None:
    """회차 프로즈를 낭독 체감으로 읽고 리듬(문말·문단 호흡·문장 길이) 수리 필요 여부·인용을 낸다.

    provider: 판정 provider(호출부가 style_judge_model 로 라우팅 — gen≠judge). 실패(본문 전무·콜·파싱)=None(보수).
    산출: {needs_repair, spans[{quote, axis, why}], reason, reference{runs…, paragraphs…}} — advisory·무강제."""
    prose = (text or "").strip()
    if not prose:
        return None
    ref = build_reference_block(text, threshold=threshold, max_runs=max_runs)
    pref = build_paragraph_reference(text)
    user = f"[장르] {genre or '웹소설'}\n\n{_format_reference(ref)}\n\n"
    p_txt = _format_paragraph_reference(pref)
    if p_txt:
        user += p_txt + "\n\n"
    user += f"[본문(이 회차 전체)]\n{prose}"
    try:
        r = provider.chat_json(
            [{"role": "system", "content": _RHYTHM_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.2)
    except Exception:
        return None
    if not isinstance(r, dict):
        return None
    spans = _normalize_rhythm_spans(r.get("spans"))
    return {"needs_repair": bool(r.get("needs_repair", bool(spans))), "spans": spans,
            "reason": (r.get("reason") or "").strip(),
            "reference": {"runs": ref, "paragraphs": pref}}
