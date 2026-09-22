# -*- coding: utf-8 -*-
"""SP-1 Stage B/C 브릿지 — 생성 경로 내장 문체 파이프라인(국소 스팬 수리 + Kiwi 계측 확장).

이 모듈은 실험 도구(tools/st11_span_rewrite·tools/kiwi_metrics)의 검증된 결정론 부품을 제품 생성
경로로 '이관'하는 단일 브릿지다. 설계 근거: docs/design-sp1-style-pipeline.md §3(Stage B)·§4(Stage C).

**엔진 의존성0 불변 유지(핵심)**: novelcopilot/(엔진)은 kiwipiepy·tools 모듈에 *로드 타임* 의존이 없다.
  이 파일의 tools import 는 전부 함수 *안*에서 lazy 로 수행되고 try/except 로 감싼다 — style_repair
  토글 OFF(또는 kiwipiepy·tools 부재)면 이 경로가 아예 호출되지 않거나 조용히 강등(no-op)되어
  본문 바이트가 불변이다. Kiwi 미설치 시 kiwi_metrics.split_sents 는 자체적으로 정규식 폴백으로 강등되므로
  스팬 경계·계측은 기존 정규식 축과 바이트 동일로 수렴한다(설계 §2 하위호환). 즉 '엔진이 tools 를 항상
  필요로 한다'가 아니라 '토글 ON + 부품 가용 시에만 태운다'.

**무강제 정합**: ①국소 스팬 수리(rewrite_span_via_chassis v2)는 사실 불변 가드레일·커버리지 가드·폴백=원문을
  그대로 태운다(판정→전체 재생성 없음). ②스팬별 격리(gen_gated fix_fn 계보 9108df9) — 한 스팬의 실패
  (span_not_found: 선행 수리가 본문을 바꿔 뒤 스팬 매칭 소실)는 그 스팬만 원문 유지하고 전진, 회차 전체를
  죽이지 않는다. ③실패/폴백은 원문 유지 + style_repairs 기록(침묵 폴백 금지). ④계측은 전부 advisory(임계·판정 0).
"""
from __future__ import annotations


# SP-1 Stage B 기본 검출 임계 — '~다' run≥6(ST-11/dp4b 기본과 동일). 파라미터 하드코딩 금지 원칙에 따라
#   호출부(harness)가 config 값을 넘길 수 있게 인자로 노출하되, 이관 부품의 계보 임계를 기본값으로 둔다.
RHYTHM_RUN_THRESHOLD = 6


def detect_rhythm_spans(text: str, run_threshold: int = RHYTHM_RUN_THRESHOLD) -> list[dict]:
    """ST-11 리듬 스팬 검출(결정론·LLM 0콜). Kiwi 인용 인식 분리기(부재 시 정규식 폴백)로 스팬 경계 산출.

    tools 부재/로드 실패 시 빈 리스트(강등 — 수리 없음). 엔진 의존성0 불변(import lazy·try/except)."""
    try:
        import tools.st11_span_rewrite as st11
        import tools.kiwi_metrics as km
    except Exception:
        return []
    try:
        return st11.extract_rhythm_spans(text or "", run_threshold=run_threshold,
                                         sent_splitter=km.split_sents)
    except Exception:
        return []


def select_repair_spans(spans: list[dict], max_spans: int | None) -> list[dict]:
    """비용 상한 선별 — 긴 run·파편 밀도 상위 max_spans 개(설계 §3: 실측 3~19건 분포에서 상위 우선).

    st11._select_spans(임팩트=run_len+n_fragment 내림차순 → 원문 순서 복원) 재사용. tools 부재 시
    앞에서 max_spans 개 절단(안전 폴백)."""
    if max_spans is None or len(spans) <= max_spans:
        return spans
    if max_spans <= 0:
        return []
    try:
        import tools.st11_span_rewrite as st11
        return st11._select_spans(spans, max_spans)
    except Exception:
        return spans[:max_spans]


def repair_spans(generator, ontology, checker, chapter_no: int, text: str,
                 run_threshold: int = RHYTHM_RUN_THRESHOLD, max_spans: int | None = None,
                 service=None) -> tuple[str, list[dict]]:
    """finalize 직전 국소 리듬 수리(Stage B) — 검출 스팬을 v2 문단 리팩토링으로 순차 수리(사실 불변·폴백 원문).

    반환 (new_text, repairs). repairs = 스팬별 수리 내역(style_repairs 영속용·투명성). 스팬 미검출/토글 상황은
    호출부(harness)가 style_repair·max_spans 로 게이트 — 여기 도달하면 실제 수리를 시도한다.

    스팬별 격리(gen_gated fix_fn 계보): 앞선 스팬 퇴고가 본문을 바꾸면 뒤 스팬의 원문 매칭(_find_span)이
    깨질 수 있다(실측 ch12: 13스팬 순차 중 span_not_found). 그 스팬만 원문 유지(폴백 기록)하고 전진 —
    회차 전체를 죽이지 않는다(무강제). 각 성공 수리는 다음 스팬 검출/replace 기준이 되도록 text 를 갱신한다.
    """
    try:
        import tools.st11_span_rewrite as st11
    except Exception:
        return text, []
    spans = detect_rhythm_spans(text, run_threshold=run_threshold)
    if not spans:
        return text, []
    spans = select_repair_spans(spans, max_spans)
    repairs: list[dict] = []
    cur = text
    for sp in spans:
        entry = {
            "kind": sp.get("kind"), "sent_start": sp.get("sent_start"), "sent_end": sp.get("sent_end"),
            "char_start": sp.get("char_start"), "char_end": sp.get("char_end"),
            "metric": sp.get("metric", {}),
            "before_len": len(sp.get("span_text", "") or ""),
            "changed": False, "retried": False, "fallback": None,
            "coverage_passed": None, "guardrail_ok": None,
        }
        # 오프셋 재-앵커(선행 스팬 수리로 cur 오프셋이 밀렸을 때): 스팬 원문(span_text)을 cur 에서 정확히 1회
        #   찾아 char_start/char_end 를 cur 기준으로 갱신한다. 안 하면 rewrite_span_via_chassis 내부의
        #   _extract_after_span(cur[:char_start]) 이 밀린 오프셋으로 어긋나 재검출·커버리지 계측이 스테일해진다
        #   (반환 본문 full_after 는 content 매칭이라 항상 정합 — 이 갱신은 advisory 계측 정확성 보정). 0회/2회+(선행
        #   수리가 이 스팬을 덮음)이면 stale_offset 폴백으로 그 스팬만 원문 유지·전진(스팬별 격리·데이터 무손실).
        sp_text = sp.get("span_text", "") or ""
        if cur is not text and sp_text:
            occ = cur.count(sp_text)
            if occ == 1:
                i = cur.index(sp_text)
                sp = {**sp, "char_start": i, "char_end": i + len(sp_text)}
            elif occ == 0:
                entry["fallback"] = "stale_offset"
                entry["after_len"] = entry["before_len"]
                repairs.append(entry)
                continue
            # occ>=2(모호) 는 원 오프셋 유지 — 아래 chassis 의 revise_prose _find_span 이 모호성을 자체 판정(안전)
        try:
            r = st11.rewrite_span_via_chassis(
                generator, ontology, checker, chapter_no, cur, sp,
                run_threshold=run_threshold, service=service, mode="v2")
        except ValueError as e:
            # 스팬 매칭 소실(선행 퇴고로 본문 변경) — 그 스팬만 원문 유지(폴백 기록·전진). 다른 ValueError 는 전파.
            if "span_not_found" not in str(e):
                raise
            entry["fallback"] = "span_not_found"
            entry["after_len"] = entry["before_len"]
            repairs.append(entry)
            continue
        entry["changed"] = bool(r.get("changed"))
        entry["retried"] = bool(r.get("retried"))
        entry["after_len"] = len(r.get("span_text_after", "") or "")
        cg = r.get("coverage_guard") or {}
        entry["coverage_passed"] = cg.get("passed")
        entry["fallback"] = cg.get("fallback")   # None | "v1" | "original"
        gr = r.get("guardrail")
        entry["guardrail_ok"] = (None if gr is None else (not gr.get("error") and bool(gr.get("passed", True))))
        if r.get("changed"):
            cur = r.get("full_after") or cur   # 수리 반영본을 다음 스팬 기준으로(스팬별 격리 — 순차 replace 정합)
        repairs.append(entry)
    return cur, repairs


def _layer_axes(text: str) -> dict | None:
    """SP-1b ②(G1): 발화 층위 원자료(ST-9 '벽' 판정축) — dp4b_loop._kiwi_axes 의 layer 와 동형.

    ST-9 가 정의한 *진짜* 벽 판정축은 어미 run(da_streak)이 아니라 **발화 층위 단일성**(지문만 40문장 연속
    되는 벽 = max_narration_run). dp4b 러너는 이 축을 산출해 심사 프롬프트에만 넣었고 엔진(ai_tell·style_repair)
    엔 없었다(감사 G1 비대칭 — 판별축 누락). 신규 검출기 0: 기존 style_lightness_baseline.lightness_metrics
    (대사 비중·무대사 지문 연속 run)를 재사용해 원자료만 뽑는다. 값은 advisory(임계·판정 0).
    tools 부재/실행 실패 시 None(호출부가 layer 를 생략 — 종결 축은 유지·결측 정직)."""
    try:
        import tools.style_lightness_baseline as sl
        lm = sl.lightness_metrics(text or "")
    except Exception:
        return None
    return {"dialogue_para_ratio": lm.get("dialogue_para_ratio"),
            "dialogue_char_ratio": lm.get("dialogue_char_ratio"),
            "max_narration_run": lm.get("max_narration_run")}


def _human_band() -> dict | None:
    """SP-1b ③(G3): Kiwi 축 인간 레퍼런스 대역(동결 수치 상수) — advisory 병기용(임계·판정 0).

    tools/kiwi_human_band.py 의 HUMAN_BAND(수치만 — reference/ 원문 미접촉·미커밋)를 lazy 로 읽는다.
    이 상수는 순수 숫자라 kiwipiepy 불필요 — import 실패 시 None(호출부가 human_band 생략·결측 정직).
    엔진 의존성0 불변: import 는 함수 안·try/except(로드 타임 tools 의존 없음, 종결/layer 축과 동일 계약)."""
    try:
        from tools.kiwi_human_band import HUMAN_BAND
        return HUMAN_BAND
    except Exception:
        return None


def kiwi_style_metrics(text: str) -> dict:
    """SP-1 Stage C — Kiwi 문말 계측 + 발화 층위 원자료 + 인간 대역을 ai_tell 에 additive 확장(advisory·판정 라벨 0).

    반환(부품 가용 시):
      {"ending_profile": {top_template, top_ratio, max_run, unique, compression, n_ending, backend},
       "da_streak": {max_run, ratio, n_past, n_sent, backend},
       "layer": {dialogue_para_ratio, dialogue_char_ratio, max_narration_run},   # SP-1b ②(G1) additive
       "human_band": {ending_profile{...}, da_streak{...}, layer{...}, meta{...}}}   # SP-1b ③(G3) advisory 대역
    tools(kiwi_metrics) 부재/로드 실패 시 빈 dict → 호출부가 ai_tell 을 확장하지 않음(구 경로 바이트 동일·결측 정직).
    Kiwi 미설치 시 종결 두 계측은 정규식 근사로 강등(backend='regex') — 값은 나오되 백엔드를 정직 표기.
    layer 축(dp4b _kiwi_axes 동형)은 style_lightness_baseline 재사용 — 그 부품만 실패하면 layer 키 생략(종결 축은 유지).
    human_band 는 인간 4화 실측 동결 대역(수치만) — 회차 값이 대역 밖이어도 개입 0(참조선·무강제). 상수 부재 시 생략."""
    try:
        import tools.kiwi_metrics as km
    except Exception:
        return {}
    try:
        ep = km.ending_profile(text or "")
        da = km.da_streak_kiwi(text or "")
    except Exception:
        return {}
    out = {
        "ending_profile": {
            "top_template": ep.get("top_template"), "top_ratio": ep.get("top_ratio"),
            "max_run": ep.get("max_run"), "unique": ep.get("unique"),
            "compression": ep.get("compression"), "n_ending": ep.get("n_ending"),
            "backend": ep.get("backend"),
        },
        "da_streak": {
            "max_run": da.get("max_run"), "ratio": da.get("ratio"),
            "n_past": da.get("n_past"), "n_sent": da.get("n_sent"),
            "backend": da.get("backend"),
        },
    }
    # SP-1b ②(G1): 발화 층위 축 additive — 종결 축 산출 성공 후에만 시도(부품 부재 시 layer 만 생략).
    layer = _layer_axes(text)
    if layer is not None:
        out["layer"] = layer
    # SP-1b ③(G3): 인간 레퍼런스 대역(동결 수치 상수) additive advisory — 상수 부재 시 생략(결측 정직).
    band = _human_band()
    if band is not None:
        out["human_band"] = band
    return out
