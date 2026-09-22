# -*- coding: utf-8 -*-
"""ST-12b — draft 러닝 카운터 폐루프(CAPEL 패턴의 문체 일반화).

설계 SSOT: docs/design-st12-style-register.md §5 ST-12b. 근거 §7(CAPEL — arXiv:2508.13805):
모델은 자기 출력의 통계를 못 센다(vibes 문체 지시 5-null 의 원인 — 자기 계수 불능). 코드가 대신
누적 종결 분포를 세서, 이어쓰기(_continue) 청크 직전에 **수치 상태블록**을 프롬프트에 주입하면 준수율이
뛴다(길이 제어 실증 30%→95% 를 문말 종결 분포로 일반화 — 문헌 공백이라 결과 자체가 신규 지식).

**엔진 의존성0 불변(style_pipeline.py 패턴 복제)**: novelcopilot/(엔진)은 kiwipiepy·tools 에 *로드 타임*
의존이 없다. 이 모듈의 tools import 는 전부 함수 *안*에서 lazy·try/except 로 수행된다 — 부품 부재/실패 시
상태블록을 무주입(조용한 강등, backend 를 디버그에 표기)하고 생성은 그대로 계속한다. kiwipiepy 미설치면
kiwi_metrics 내부가 정규식으로 자체 강등(backend='regex')하므로 값은 나오되 backend 를 정직 표기한다.

**무강제·pink-elephant(계약)**: 이 모듈은 재작성·차단·판정 라벨을 0으로 한다 — 이어쓰기 지시 강화(수치
현황 + 긍정 전환 메뉴)일 뿐이다. 상태블록엔 최빈 종결형 실물 문자열('었다' 등)·부정 예시·회피 목록을
절대 쓰지 않는다(피할 대상 노출 = pink-elephant). "최빈 종결형"이라고만 지칭하고 수치·긍정 메뉴만 담는다.

**상태 적응형(상수 블랭킷 아님)**: 누적 top_ratio·max_run 이 인간 대역 안이면 상태블록은 ""(개입 0).
대역 밖일 때만 잔여 쿼터를 산술로 계산해 주입한다. 초안(_draft) 콜·in-band 상황엔 어떤 문체 지시도 붙이지
않는다(적대 비평 §3-3: 상수 문체 계약 = 이미 실패한 블랭킷 레버의 긍정형 재판 — 철회하고 상태 적응형으로).
"""
from __future__ import annotations

import math

# ── 대역 상한(순수 숫자 상수 — 엔진은 숫자만) ──────────────────────────────────
#   설계 §5 ST-12b: in-band 무주입 = top_ratio ≤ 0.60 이고 max_run ≤ 14. 이 판정 상한은 순수 상수라
#   tools 없이도 동작한다(엔진 의존성0). tools.kiwi_human_band.HUMAN_BAND(수치만)를 lazy 로 읽어
#   실측 대역과 정합함을 advisory 로 확인할 수 있으나(아래 _band_ref), 판정 자체는 이 상수로 한다.
TOP_RATIO_BAND_MAX = 0.60      # 최빈 종결 지배율 개입 임계(인간 대역 상한 0.595 를 포함하는 여유 임계)
MAX_RUN_BAND_MAX = 14          # 동일 종결 최장 run 개입 임계(인간 대역 상한 14 = HUMAN_BAND.ending_profile.max_run.max)
TARGET_TOP_RATIO = 0.55        # 쿼터 산술의 목표 지배율 t(대역 중상단 — 되돌릴 목표점)


def _band_ref() -> dict | None:
    """tools.kiwi_human_band.HUMAN_BAND(동결 수치 상수) — advisory 참조용(판정 아님). style_pipeline._human_band 동형.

    순수 숫자 상수라 kiwipiepy 불필요 — import 실패 시 None(호출부가 참조 생략). 엔진 의존성0 불변(함수 안 import·try/except).
    이 값은 상태블록의 대역 문안('사람 손글의 대역은 35~60%')이 실측과 어긋나지 않는지 확인하는 데만 쓴다 —
    개입 임계(TOP_RATIO_BAND_MAX/MAX_RUN_BAND_MAX)는 위 순수 상수가 단일 출처다."""
    try:
        from tools.kiwi_human_band import HUMAN_BAND
        return HUMAN_BAND
    except Exception:
        return None


def measure(text: str) -> dict | None:
    """누적 본문의 종결 분포 계측 — km.ending_profile 재사용(지문 전용·대사 제외 내장). advisory·판정 0.

    반환(부품 가용 시): {top_ratio, max_run, n_ending, top_n, backend}. tools(kiwi_metrics) 부재/실패 시
    None(호출부가 상태블록 무주입 — 조용한 강등). top_ratio 가 None(종결 문장 0)이어도 None 반환(개입 근거 없음).
    lazy import·try/except 로 엔진 의존성0 불변(style_pipeline.kiwi_style_metrics 와 동일 계약)."""
    try:
        import tools.kiwi_metrics as km
    except Exception:
        return None
    try:
        ep = km.ending_profile(text or "")
    except Exception:
        return None
    tr = ep.get("top_ratio")
    n = ep.get("n_ending") or 0
    if tr is None or n <= 0:
        return None       # 종결 문장 없음 — 계측 근거 없음(개입 0, 결측 정직)
    return {
        "top_ratio": tr,
        "max_run": ep.get("max_run") or 0,
        "n_ending": n,
        "top_n": int(round(tr * n)),     # 최빈 종결형 문장 수(top_ratio*n 의 정수 환산 — 쿼터 산술 분자)
        "top_template": ep.get("top_template"),   # 측정된 최빈 종결형 실물(pink-elephant: 상태블록에 절대 미노출 — 계측 반환에만)
        "backend": ep.get("backend"),
    }


def in_band(top_ratio: float | None, max_run: int | None) -> bool:
    """인간 대역 안인가 — top_ratio ≤ 0.60 이고 max_run ≤ 14(설계 §5). 안이면 상태블록 무주입(개입 0).

    None(계측 결측)은 '대역 안으로 간주'(개입 근거 없음 → 무주입)."""
    if top_ratio is None:
        return True
    if top_ratio > TOP_RATIO_BAND_MAX:
        return False
    if max_run is not None and max_run > MAX_RUN_BAND_MAX:
        return False
    return True


def quota_k(top_n: int, n_ending: int, remaining_sents: int | None,
            target: float = TARGET_TOP_RATIO) -> int:
    """되돌릴 잔여 쿼터 k — 최빈 종결형을 목표 지배율 t 로 희석하는 데 필요한 '다른 종결' 최소 문장수(결정론 산술).

    산식(설계 §5): raw = ceil(top_n / t − n). 목표 t=0.55 는 '최빈 종결형 top_n 문장이 전체(현재 n + 추가 k)에서
    비율 t 이하가 되게' 만드는 추가 문장수의 하계다(top_n / (n + k) ≤ t ⇒ k ≥ top_n/t − n).
      · raw 음수(이미 목표 이하) → 0.
      · 상한 = max(3, 남은 분량 추정 문장수). 남은 문장수 = (norm − len(text)) / (지금까지 평균 문장 길이) — 호출부가
        산출해 remaining_sents 로 전달(None 이면 상한 가드 미적용, 하한만).
      · 하한 = 3(상태블록을 실제로 낼 때는 최소 3문장은 되돌리라 — 미세 쿼터로 개입이 무의미해지는 것 방지).
    최종 k = clamp(raw, 3, 상한). 상한이 하한(3)보다 작으면 상한=3 으로 승격(max(3, …) 가 이를 보장)."""
    if n_ending <= 0 or top_n <= 0:
        return 0
    raw = math.ceil(top_n / target - n_ending)
    if raw < 0:
        raw = 0
    upper = 3 if remaining_sents is None else max(3, int(remaining_sents))
    k = max(3, min(raw, upper)) if raw > 0 else max(3, min(3, upper))
    return k


def estimate_remaining_sents(text: str, norm: int, n_ending: int) -> int | None:
    """남은 분량 추정 문장수 = (norm − len(text)) / 지금까지 평균 문장 길이. 근거 부족 시 None(상한 가드 미적용).

    평균 문장 길이 = len(text)/n_ending(지문 종결 문장 기준 근사). norm 이하로 이미 찼거나 근거가 없으면 None."""
    if n_ending <= 0:
        return None
    remain_chars = norm - len(text or "")
    if remain_chars <= 0:
        return None
    avg_len = len(text or "") / n_ending
    if avg_len <= 0:
        return None
    est = int(remain_chars / avg_len)
    return est if est > 0 else None


def build_state_block(top_ratio: float, max_run: int, n_ending: int, k: int) -> str:
    """수치 상태블록(전부 결정론 산술 — 수치·긍정 전환 메뉴만). pink-elephant: 최빈 종결형 실물·부정 예시 0.

    형식(설계 §5 예시) — "최빈 종결형"이라고만 지칭(실물 '었다' 등 절대 미기입), 대역 수치·되돌릴 문장수·긍정 메뉴만."""
    pct = int(round(top_ratio * 100))
    return (
        "\n\n[서술 리듬 현황 — 코드 계측]\n"
        f"지금까지 지문 종결 {n_ending}문장 중 최빈 종결형이 {pct}%"
        f"({max_run}문장 연속 구간 존재). 사람 손글의 대역은 35~60%다.\n"
        "이어쓰는 분량에서는 다음 종결을 우선 사용해 리듬을 되돌려라: "
        "주인공 속생각 입말(-지/-려나/-거든/자문), 현재형 묘사(-ㄴ다/-는다). "
        f"최소 {k}문장."
    )


def build_prev_block(top_ratio: float, max_run: int, n_ending: int) -> str:
    """직전 회차 계측 기반 '초안 시점' 상태블록(ST-12b-2 — 회차 간 폐루프, Reflexion 패턴의 회차 간 버전).

    검증된 프로브 B형 비율 규칙(절반가량 전환·동일 종결 3연속 회피)을 상태 게이트 뒤에 둔다 — 직전 회차가
    대역 밖일 때만 발화하므로 상수 블랭킷이 아니다. 시제 기조 상수 지시(_draft out_instr '과거형 기조 일관')와의
    충돌은 마지막 문장이 해소한다. pink-elephant: 최빈 종결형 실물·부정 예시 0."""
    pct = int(round(top_ratio * 100))
    return (
        "\n\n[서술 리듬 계측 — 직전 회차(코드 계측)]\n"
        f"직전 회차는 지문 종결 {n_ending}문장 중 최빈 종결형이 {pct}%"
        f"({max_run}문장 연속 구간 존재)였다. 사람 손글의 대역은 35~60%다.\n"
        "이번 회차는 처음부터 리듬을 대역 안으로 되돌려 써라: 지문 서술 문장의 절반가량은 "
        "① 주인공 속생각 입말(-지/-려나/-거든/자문) ② 현재형 묘사(-ㄴ다/-는다)로 쓰고, "
        "같은 형태의 종결이 3문장 넘게 이어지기 전에 위 계열로 리듬을 바꿔라. "
        "(시제 기조는 과거 유지 — 속생각 입말·현재형 묘사 문장이 그 사이에 섞이는 것은 기조 위반이 아니라 "
        "사람 손글의 정상 리듬이다.)"
    )


def compute_prev(prev_text: str) -> dict:
    """직전 회차 본문 → 초안(_draft) 시점 상태블록 + 디버그(ST-12b-2). generate() 진입 시 1회 호출.

    12b 쌍 A/B 실측(2026-07-14, 야간 배송 ch2·ch3): 이어쓰기 주입은 개입 표면이 없다 — 이 엔진은 대부분
    초안 한 방에 norm 도달(DP-12 비트 소진 트리거)해 이어쓰기가 0~1회고, 회차 후반 도착이라 잔여 클램프에
    쿼터가 3문장으로 깎여 무효량(ch2: 측정 top 0.82 에 k=3, ch3: 발화 0회). 초안 주입은 매 회차 발화(표면
    100%)하고 회차 전체가 사정권이다. 직전 회차가 대역 밖일 때만(상태 적응형) — ch1(직전 없음)·in-band·
    계측 결측이면 block ""(무주입 — 프롬프트 바이트 동일)."""
    m = measure(prev_text or "")
    if m is None:
        return {"block": "", "debug": None}   # ch1/결측 — 무주입(디버그도 없음: OFF 와 구분 불요)
    base = {"src": "prev", "n_ending": m["n_ending"], "top_ratio": m["top_ratio"],
            "max_run": m["max_run"], "backend": m["backend"]}
    if in_band(m["top_ratio"], m["max_run"]):
        return {"block": "", "debug": {**base, "quota_k": 0, "injected": False}}
    block = build_prev_block(m["top_ratio"], m["max_run"], m["n_ending"])
    return {"block": block, "debug": {**base, "quota_k": None, "injected": True}}   # 비율 규칙이라 쿼터 산술 없음


def compute(text: str, norm: int) -> dict:
    """누적 본문 → 상태블록 + 디버그 레코드(단일 진입점). 이어쓰기 직전 호출.

    반환:
      {"block": str,           # 주입할 상태블록("" 이면 무주입 — 빈 문자열이면 프롬프트 바이트 완전 동일)
       "debug": {...} | None}  # draft_ctx.style_counter 에 append 할 레코드(A/B 포렌식). 계측 결측이면 None.
    조용한 강등(tools 부재/결측): block="" · debug{backend:None,injected:False}(호출부가 backend 표기).
    무주입 경로도 debug 를 남겨(injected:False) A/B 에서 '왜 무주입인지'(대역 안/결측)를 추적 가능하게 한다."""
    m = measure(text)
    if m is None:
        # 계측 결측(tools 부재·종결 0) — 무주입, backend 만 정직 표기(디버그 강등 사유)
        return {"block": "", "debug": {"n_ending": 0, "top_ratio": None, "max_run": None,
                                       "quota_k": 0, "injected": False, "backend": None}}
    top_ratio, max_run, n_ending, top_n, backend = (
        m["top_ratio"], m["max_run"], m["n_ending"], m["top_n"], m["backend"])
    if in_band(top_ratio, max_run):
        # 대역 안 — 개입 0(상수 블랭킷 금지 계약). 디버그엔 injected:False 로 남긴다.
        return {"block": "", "debug": {"n_ending": n_ending, "top_ratio": top_ratio, "max_run": max_run,
                                       "quota_k": 0, "injected": False, "backend": backend}}
    remaining = estimate_remaining_sents(text, norm, n_ending)
    k = quota_k(top_n, n_ending, remaining)
    block = build_state_block(top_ratio, max_run, n_ending, k)
    return {"block": block, "debug": {"n_ending": n_ending, "top_ratio": top_ratio, "max_run": max_run,
                                      "quota_k": k, "injected": True, "backend": backend}}
