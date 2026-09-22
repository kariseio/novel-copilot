# -*- coding: utf-8 -*-
"""HM-3 N-1/N-2 LLM 탐지 — 지문(서술)에서 자기해설 케이던스(N-1)·감정 명명(N-2) 스팬을 낭독 체감으로
찾아 findings(char 좌표)로 낸다. 결정론 탐지(humanize_detect, LLM 0)가 원리적으로 못 내는 '의미 판정'
축을 보강한다(분류학 SSOT §4 "결정론이 좌표를 못 내는 축 N-1·N-2 는 LLM 탐지가 보강한다·설계 ⓑ②").

원칙(style_judge.py 계보):
  · gen≠judge — 판정 provider 는 호출부가 style_judge_model(교차 벤더)로 라우팅해 주입한다(빈값이면 호출부가
    아예 스킵 — gen=judge 방지). 이 모듈은 provider 종류·라우팅을 모른다(레이어 규율).
  · measure-then-cite — 문제 구간은 '원문 정확 인용' 의무. 인용을 본문에서 결정론으로 재-앵커해 char 좌표를
    낸다(정확 일치 우선, 실패 시 공백 정규화 폴백 — humanize_detect._match_quote_span 재사용·이중 구현 금지).
  · 지문-only 결정론 백스톱 — 재앵커 후 스팬이 대사행(따옴표·대시 프리픽스) 안이면 드롭한다(대사 원형 불변·
    VL-1/VH-1: 대사=일상 입말·수술 금지). 지시가 아니라 좌표로 강제(결정론 형제와 대칭).
  · N-1 은 전수(catch-all)로 탐지한다 — 개별 자평·잠언·대조·값 자평을 빠짐없이 낸다(사용자 지시 2026-08-20
    '다 잡는 게 맞다'). 과탐-보이스 말살·반전 복선 파손 위험은 자동 재작성이 아니라 '가시화'(surface — 작가가
    스팬별 판단)로 관리한다. auto-rewrite(findings→humanize 병합)는 별도 opt-in(config humanize_llm_detect_rewrite).
  · 판정 실패(콜/파싱 불가) → [](비차단·보수·결측 정직). advisory·무강제 — 수술 후보 안내이지 게이트 아님.
  · few-shot 0·pink-elephant — 시그니처는 범주 서술만. 나쁜 예문을 나열하지 않는다.
"""
from __future__ import annotations
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)

# findings 심각도(분류학 SSOT: N-1=S1 자기해설·N-2=S2 감정 명명).
_SEVERITY = {"N-1": "S1", "N-2": "S2"}

# 탐지 판정 프롬프트(범주 서술·few-shot 0·지문-only·보수). 산출 = {"spans":[{category, quote, why}]}.
#   N-1 은 케이던스(닫힘의 연속) — 개별 자평 문장이 아니라 자평이 연달아 이어지는 구간만. 판정 기준은 낭독 체감.
_DETECT_SYSTEM = (
    "너는 한국어 웹소설 문장을 낭독 체감으로 읽는 진단가다. 아래 본문의 지문(서술 문장)에서 '쓴 티'가 나는 "
    "문장을 개별 문장 단위로 빠짐없이 찾아 원문 그대로 인용해 낸다. 이 검사는 전수 커버리지가 목적이다 "
    "(최종 판단은 작가 몫 — 여기서는 걸리는 것을 다 낸다).\n\n"
    "[N-1 자기해설] 서술자가 지문에서 요지를 말로 못박거나 자평·잠언으로 비트를 닫는 문장을 모두 낸다. "
    "다음 층위에 해당하면 잡아라: (a) 장면이 이미 행동·대사·묘사로 보여준 의미를 서술자가 한 번 더 말로 "
    "정리하는 문장, (b) 자기 수·처지·상황을 평하는 문장, (c) 일반 격언·잠언으로 비트를 닫는 문장, "
    "(d) 무엇이 아님을 앞세워 무엇임을 규정하는 대조 문장, (e) 값·시세·손익을 환산해 평하는 문장. "
    "연속이든 단발이든 위 층위에 해당하면 뚜렷한 화자 목소리라도 낸다.\n"
    "[N-2 감정 명명] 감정을 이름으로 선언하는 문장. 그 감정이 드러나는 몸짓·호흡·시선·목소리로 보여주지 "
    "않고 감정어로 명명하는 층위다.\n\n"
    "[대상 아님 — 넣지 마라]\n"
    "· 따옴표 안 대사(인물이 하는 말)는 저자 개입이 아니다. 지문만 본다.\n"
    "· 장면을 진행시키는 사건·행동·감각 서술은 티가 아니다. 다만 감각 문장이 단독으로 비트를 닫으며 "
    "의미를 실어 나르는 경우는 (a)/(c) 층위로 본다.\n\n"
    "[출력] JSON 객체 하나만 낸다: {\"spans\": [{\"category\": \"N-1\" 또는 \"N-2\", "
    "\"quote\": \"본문에 있는 그대로의 한 문장\", \"why\": \"어느 층위(a~e)인지 + 한 줄 사유\"}]}. "
    "quote 는 본문에서 글자 그대로 복사한다(요약·교정·재구성 금지 — 한 글자도 바꾸지 마라). "
    "걸리는 게 없으면 {\"spans\": []} 를 낸다.\n"
    "이건 진단(가시화)이지 PASS/FAIL 판정이 아니다."
)


# HM-6 정밀 탐지(잔여 자동 수술 선별용 — 2026-08-21 사용자 결정 "정밀 전환"): '옮겨도 잃는 게 없는 잉여만'
#   엄격 선별. 근거 실측: 전량 스팬 변환 후에도 catch-all 카운트 불변(내용 구간은 기계로 못 뺌) · 정밀
#   프로브는 변환본에서 0. catch-all(_DETECT_SYSTEM)은 가시화 전수 목록으로 계속 쓴다(역할 분리).
_DETECT_PRECISE_SYSTEM = (
    "너는 한국어 웹소설 문장을 읽는 진단가다. 지문에서 '옮겨도 독자가 잃는 게 전혀 없는' 문장만 찾아 "
    "원문 그대로 낸다. 엄격하게, 확실한 것만.\n\n"
    "[N-1 잉여 자기해설] 바로 앞의 행동·대사·묘사가 이미 보여준 것을 서술자가 한 번 더 말로 정리·판정하거나, "
    "일반 격언으로 비트를 닫는 문장. 그 문장을 행동·감각·대사로 옮겨도 사건·정보·분위기가 하나도 안 "
    "줄어드는 것만.\n"
    "[N-2 감정 명명] 감정을 이름으로 선언하는 문장 중, 그 감정이 바로 앞의 행동·대사·표정에 이미 드러나 "
    "있어 이름을 몸으로 옮겨도 독자가 그 감정을 그대로 아는 것만.\n\n"
    "[절대 넣지 마라 — 대상 아님]\n"
    "· 장면이 아직 안 보여준 새 정보·플롯·인물 상태·복선을 더하는 서술.\n"
    "· 분위기·긴장·감각을 세우는 묘사.\n"
    "· 따옴표 안 대사.\n"
    "· 화자의 개성적 목소리(냉소·유머)가 새 정보나 웃음을 실어 나르는 문장.\n"
    "· 애매하면 빼라. 확실한 것만.\n\n"
    "[출력] JSON 객체 하나만 낸다: {\"spans\": [{\"category\": \"N-1\" 또는 \"N-2\", "
    "\"quote\": \"본문 그대로\", \"why\": \"그것을 이미 보여준 앞 문장을 본문에서 글자 그대로 인용\"}]}. "
    "quote 와 why 는 본문에서 글자 그대로 복사한다(요약·교정·재구성 금지). 없으면 {\"spans\": []} 를 낸다."
)


def _normalize_items(raw) -> list[dict]:
    """모델 산출 spans 정규화 — [{category, quote, why}] 문자열화·빈/무효 항목 제거(카테고리·인용 필수)."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if not isinstance(it, dict):
            continue
        cat = str(it.get("category") or "").strip().upper().replace("_", "-")
        quote = str(it.get("quote") or "").strip()
        if cat not in _SEVERITY or not quote:
            continue
        out.append({"category": cat, "quote": quote, "why": str(it.get("why") or "").strip()})   # 절단 전면 제거(2026-08-21): advisory 사유 전문
    return out


def _overlaps(span: tuple[int, int], used: list[tuple[int, int]]) -> bool:
    return any(not (span[1] <= u[0] or span[0] >= u[1]) for u in used)


def _anchor(text: str, quote: str, used: list[tuple[int, int]]) -> tuple[int, int] | None:
    """인용을 본문에서 결정론으로 재-앵커. 정확 일치(안 쓴 첫 매치) 우선, 실패 시 공백 정규화 폴백
    (humanize_detect._match_quote_span 재사용). 부재/기채택 겹침이면 None(오앵커보다 누락이 안전)."""
    start = 0
    while True:
        i = text.find(quote, start)
        if i < 0:
            break
        span = (i, i + len(quote))
        if not _overlaps(span, used):
            return span
        start = i + 1
    try:
        from .humanize_detect import _match_quote_span   # 공백 정규화 부분 매칭 폴백(이중 구현 금지)
        span = _match_quote_span(text, quote)
    except Exception:
        span = None
    if span and not _overlaps(span, used):
        return span
    return None


def _prose_ranges(text: str) -> list[tuple[int, int]]:
    """지문(대사행 제외) 줄의 char 범위 — humanize_detect._prose_line_ranges 재사용(부재 시 전범위 폴백)."""
    try:
        from .humanize_detect import _prose_line_ranges
        return _prose_line_ranges(text)
    except Exception:
        return [(0, len(text))]   # 헬퍼 부재 시 배제 스킵(결측 정직·기존 동작)


@promptlog.stage("narration_detect")
def detect_narration_tells(provider, text: str, *, max_spans: int = 8,
                           mode: str = "catchall") -> list[dict]:
    """지문에서 N-1(자기해설 케이던스)·N-2(감정 명명) 스팬을 LLM 낭독 판정으로 찾아 findings 로 낸다.

    provider: 판정 provider(호출부가 style_judge_model 로 라우팅 — gen≠judge·이 모듈은 종류를 모른다).
    반환: [{category, severity, span:{char_start,char_end,text}, metric:{source:'llm', why}}] — detect_chapter
      finding 스키마와 동형(humanize_spans 가 그대로 소비). 본문 전무·콜/파싱 실패·인용 앵커 실패·대사행 스팬 → 제외.
    max_spans: 회차당 상한(비용·노이즈 가드). S1(N-1) 우선은 humanize_pass.select_humanize_spans 가 담당.
    """
    prose = (text or "").strip()
    if not prose or provider is None:
        return []
    _sys = _DETECT_PRECISE_SYSTEM if mode == "precise" else _DETECT_SYSTEM   # HM-6: 수술 선별 vs 가시화 전수
    try:
        r = provider.chat_json(
            [{"role": "system", "content": _sys},
             {"role": "user", "content": f"[본문(이 회차 전체)]\n{text}"}],
            temperature=0.2)
    except Exception:
        return []
    if not isinstance(r, dict):
        return []
    items = _normalize_items(r.get("spans"))
    pranges = _prose_ranges(text)
    findings: list[dict] = []
    used: list[tuple[int, int]] = []
    for it in items:
        span = _anchor(text, it["quote"], used)
        if span is None:
            continue   # 인용이 본문과 정확/근사 일치 안 함 → 스킵(오앵커 방지·결측 정직)
        cs, ce = span
        # 지문-only 결정론 백스톱: 스팬이 어떤 지문 줄 범위 안에 온전히 들지 않으면(대사행 등) 드롭.
        if not any(ps <= cs and ce <= pe for ps, pe in pranges):
            continue
        # HM-6 정밀 모드(감사 중대 3): '잉여' 주장을 검증형으로 — why(이미 보여준 앞 문장 인용)가 본문에
        #   실재하고 대상 스팬보다 앞서는지 결정론 검사. 실패=근거 없는 단정 → 드롭(정밀도를 검사로 담보).
        if mode == "precise":
            _ev = it.get("why") or ""
            _epos = text.find(_ev) if _ev else -1
            if _epos < 0 and _ev:
                try:
                    from .humanize_detect import _match_quote_span
                    _esp = _match_quote_span(text, _ev)
                    _epos = _esp[0] if _esp else -1
                except Exception:
                    _epos = -1
            if _epos < 0 or _epos >= cs:
                continue
        used.append(span)
        cat = it["category"]
        findings.append({
            "category": cat, "severity": _SEVERITY[cat],
            "span": {"char_start": cs, "char_end": ce, "text": text[cs:ce]},
            "metric": {"source": "llm", "mode": mode, "why": it["why"]},
        })
        if len(findings) >= max_spans:
            break
    return findings
