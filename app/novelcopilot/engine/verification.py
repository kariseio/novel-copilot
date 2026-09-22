# -*- coding: utf-8 -*-
"""PR-2 회차 통합 검증 리포트 — 매화 전 축 상태의 단일 SSOT(결정론 집계·LLM 0콜).

설계(design-pr1-pr2-product-gate.md §PR-2): FINALIZED/ESCALATED 확정 직후 이 모듈이
ChapterRecord.verification(additive dict)을 생성한다. 축은 전부 *기존 계측의 집계*(신규
검출기 0) — 캐넌·ai_tell 요약·재탕계수·분량·closing_device/hook_type·style_repairs·
reader_feedback·ending_contract_eval·claim_audit·(러너 실행 시) gate.

무강제 불변식:
  · 판정 라벨·색상 경고·임계 0 — 원자료(값)와 인간 대역(참조선)만.
  · 결측 정직: 축이 안 돌았으면 None(null)이 아니라 "미실행" 문자열 명시(누락의 조용한
    통과 차단 — 이번 사고의 구조 수리). advisory 축은 status 분기 밖(FINALIZED·ESCALATED 대칭).
  · 엔진은 tools import 금지 — 재탕계수는 dp4_loop 부품을 여기(engine)로 승격 이관.
"""
from __future__ import annotations
import re

from .drift import _stem   # 어간 정규화(교착어 조사 흡수) — dp4_loop 재탕이 의존하던 부품(이미 engine 소유)

MISSING = "미실행"   # 결측 정직 표식(null 침묵 금지) — 전 축 공유 상수

_CW = re.compile(r"[가-힣]{2,}")


# ─────────────────────────────────────────────────────────────────────────────
# 재탕계수 — dp4_loop.retread_metrics 의 엔진 이관(부품 승격, tools import 금지).
#   도입부(head자) 내용어 어간 대조로 '직전 장면 재인스턴스'를 근사(advisory — 최종 판정은 정독).
#   값은 상대 추세·배치 참조일 뿐 자동 PASS/FAIL 판정 아님(retread_flag 는 정독 앵커 위치일 뿐).
# ─────────────────────────────────────────────────────────────────────────────
def _content_stems(text: str, head: int | None = None) -> set[str]:
    """내용어 어간 집합 — 재탕(장면 재인스턴스) 근사용. head 지정 시 앞부분만(도입 장면 대조)."""
    t = (text or "")[:head] if head else (text or "")
    return {_stem(w) for w in _CW.findall(t)}


def _overlap_coef(a: set, b: set) -> float:
    """overlap coefficient |A∩B|/min(|A|,|B|) — 한 장면이 선행 장면의 '부분 재인스턴스'일 때 Jaccard 보다 민감."""
    if not a or not b:
        return 0.0
    return round(len(a & b) / min(len(a), len(b)), 3)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def retread_coefficient(text: str, prev_texts: list[str], head: int = 600):
    """이번 회차 도입부(head자)를 모든 선행 회차 도입부와 대조 — 최대 재탕 유사도(직전 장면 재인스턴스).

    dp4_loop.retread_metrics 의 회차 1건 산출(opening_coef/opening_jac/opening_with)을 엔진에서 재현.
    선행 회차가 없으면(1화·prev_texts 빈) 대조 기저가 없어 축 자체가 "미실행"(결측 정직 — 0 위장 금지·
    null 침묵 금지). 값은 advisory(임계 없음·정독 앵커 위치 참조).
      · opening_coef: overlap coefficient(부분 재인스턴스 민감) — 최댓값 산출한 선행 회차와의 유사도.
      · opening_jac : 그 선행 회차와의 Jaccard(참고).
      · opening_with_index: 최대 유사 선행 회차의 리스트 인덱스(prev_texts 기준·표시용).
      · retread_flag: coef>=0.30(dp4 캘리브레이션 — 정독이 그 대목부터 본다·자동 판정 아님)."""
    if not prev_texts:
        return MISSING            # 선행 없음 = 재탕 대조 미실행(1화 등) — null/0 위장 금지
    head_i = _content_stems(text, head)
    best_c, best_j, best_k = 0.0, 0.0, None
    for j, pt in enumerate(prev_texts):
        head_j = _content_stems(pt, head)
        co = _overlap_coef(head_i, head_j)
        if co > best_c:
            best_c, best_j, best_k = co, _jaccard(head_i, head_j), j
    return {"opening_coef": best_c, "opening_jac": best_j,
            # 유의미한 겹침(coef>0)이 있을 때만 대상 선행 인덱스 — 무겹침이면 "미실행"(null 침묵 금지·dp4 의 None 대응)
            "opening_with_index": (best_k if best_k is not None else MISSING),
            "retread_flag": bool(best_c >= 0.30)}


# ─────────────────────────────────────────────────────────────────────────────
# 축별 요약(결측 정직) — 각 함수는 값 dict 또는 MISSING 문자열을 반환.
# ─────────────────────────────────────────────────────────────────────────────
def _summ_canon(record) -> dict:
    """캐넌 축 — 최종 위반 수·hard 수·hard 0 여부(결정론 체커 산출의 집계).
    final_violations 는 항상 존재(생성 경로가 채움)하므로 결측 없음 — 빈 리스트=위반 0."""
    fv = list(record.final_violations or [])
    hard = [v for v in fv if getattr(v, "is_hard", False)]
    kinds = sorted({v.kind for v in hard})
    return {"final_violations": len(fv), "hard": len(hard),
            "hard_zero": (len(hard) == 0), "hard_kinds": kinds}


def _nn(x):
    """None(=null) → MISSING 문자열(null 침묵 금지). 검출기 하위값이 '측정 없음'을 None 으로 주면
    정직한 '미실행'으로 표면화(0 위장·null 침묵 둘 다 금지 — 이번 사고의 구조 계약)."""
    return MISSING if x is None else x


def _summ_ai_tell(ai_tell: dict):
    """ai_tell 축 요약 — 정규식(KatFishNet) 핵심값 + Kiwi 종결/층위(있으면)·인간 대역 병기.
    ai_tell 미계산(빈 dict)이면 MISSING. Kiwi 부품 부재 시 kiwi 서브키만 MISSING(종결/층위/대역 결측 정직).
    개별 하위값이 None(측정 대상 없음 — 예: 종결 문장 0 → top_template)이면 _nn 으로 MISSING 표기(null 금지)."""
    if not ai_tell:
        return MISSING
    out = {
        # 정규식 축(항상 존재 — ai_tell_profile 결정론 산출)
        "comma_per_100": _nn(ai_tell.get("comma_per_100")),
        "sent_len_cv": _nn(ai_tell.get("sent_len_cv")),
        "lexical_mattr": _nn(ai_tell.get("lexical_mattr")),
        "ending_diversity": _nn(ai_tell.get("ending_diversity")),
        "simile_per_1k": _nn(ai_tell.get("simile_per_1k")),
        "past_run_max": _nn(ai_tell.get("past_run_max")),
        "frag_ratio": _nn(ai_tell.get("frag_ratio")),
        "n_sent": _nn(ai_tell.get("n_sent")),
    }
    kiwi = ai_tell.get("kiwi") or {}
    if not kiwi:
        out["kiwi"] = MISSING          # Kiwi 부품(kiwipiepy·tools) 부재 — 종결/층위 축 결측 정직
        return out
    ep = kiwi.get("ending_profile") or {}
    da = kiwi.get("da_streak") or {}
    out["kiwi"] = {
        # SP-1 Stage C 문말 종결 계측(정규식 근사 fallback 시 backend=regex 로 정직 표기)
        "top_template": _nn(ep.get("top_template")), "top_ratio": _nn(ep.get("top_ratio")),
        "max_run": _nn(ep.get("max_run")), "unique": _nn(ep.get("unique")),
        "n_ending": _nn(ep.get("n_ending")), "backend": _nn(ep.get("backend")),
        "da_max_run": _nn(da.get("max_run")), "da_ratio": _nn(da.get("ratio")),
        # SP-1b ② 발화 층위 원자료(ST-9 벽 판정축) — 부품 부재 시 layer 키 자체가 없음(결측 정직)
        "layer": (kiwi.get("layer") if kiwi.get("layer") is not None else MISSING),
    }
    # SP-1b ③ 인간 레퍼런스 대역(동결 수치 상수·advisory 병기) — 상수 부재 시 결측 정직
    out["human_band"] = (kiwi.get("human_band") if kiwi.get("human_band") is not None else MISSING)
    return out


def _summ_length(text: str, target_chars: int | None) -> dict:
    """분량 축 — 실제 글자수 vs 출고 규범(target_chars_per_chapter·norm=×0.85). 판정 아님(비율 원자료).
    target 미상(None/0)이면 chars 만 노출하고 norm 비교는 MISSING."""
    chars = len((text or ""))
    if not target_chars:
        return {"chars": chars, "target_chars": MISSING, "norm": MISSING, "ratio_to_norm": MISSING}
    norm = int(target_chars * 0.85)   # harness 출고 규범과 동일 산술(코드 판정 — 모델은 분량 모름)
    return {"chars": chars, "target_chars": int(target_chars), "norm": norm,
            "ratio_to_norm": (round(chars / norm, 3) if norm else MISSING)}


def _summ_style_repairs(style_repairs: list):
    """style_repairs 축 요약 — 수리 스팬 수·실제 변경 수·폴백 수(SP-1 Stage B 투명성 집계).
    빈 리스트=수리 없음(스팬 미검출·OFF·상한 0). style_repair OFF/부재로 애초에 안 돌았는지 여부는
    회차 기록만으로 구분 불가하므로, 빈 리스트는 '수리 0건'으로 집계(MISSING 아님 — 필드는 항상 존재)."""
    reps = list(style_repairs or [])
    changed = sum(1 for r in reps if r.get("changed"))
    fallback = sum(1 for r in reps if r.get("fallback"))
    return {"spans": len(reps), "changed": changed, "fallback": fallback}


def _summ_humanize(humanize: list):
    """humanize 축 요약(HM-1b) — 윤문 스팬 before/after 계측 집계(효과가 회차마다 관측되는 폐루프·설계 ⓓ).
    스팬 수·실제 변경 수·폴백 수·과윤문(변경률 30%↑) 폴백 수·평균 변경률(변경 스팬 한정)·'작가 확인 요망' 수·
    카테고리별 스팬 수. 빈 리스트=윤문 없음(탐지 0·humanize OFF·상한 0) → 0건 집계(필드는 항상 존재·MISSING 아님).
    무강제: 값만 — 판정 라벨·임계 0. author_review 는 잔존 정직 표기(은폐 금지)이지 FAIL 아님."""
    hs = list(humanize or [])
    changed = [h for h in hs if h.get("changed")]
    fallback = sum(1 for h in hs if h.get("fallback"))
    over = sum(1 for h in hs if h.get("fallback") == "over_change")
    review = sum(1 for h in hs if h.get("author_review"))
    rates = [h.get("change_rate") for h in changed if isinstance(h.get("change_rate"), (int, float))]
    avg_rate = round(sum(rates) / len(rates), 4) if rates else MISSING
    by_cat: dict = {}
    for h in hs:
        c = h.get("category") or "?"
        by_cat[c] = by_cat.get(c, 0) + 1
    return {"spans": len(hs), "changed": len(changed), "fallback": fallback,
            "over_change": over, "author_review": review,
            "avg_change_rate": avg_rate, "by_category": by_cat}


def _summ_reader(reader_feedback: dict):
    """reader_feedback 축 요약(DP-10 블라인드 독자 시뮬 — advisory). 미실행(빈 dict) 시 MISSING."""
    rf = reader_feedback or {}
    if not rf:
        return MISSING
    return {"drop": _nn(rf.get("drop")), "retention_est": _nn(rf.get("retention_est")),
            "kill_trigger": _nn(rf.get("kill_trigger")), "why": _nn(rf.get("why"))}


def _summ_timing(time_by_stage: dict):
    """TM-1 timing 축 요약 — 단계별 소요 시간(usage_by_stage 시간판 대칭)의 총합·상위 5개 단계.
    빈 dict(구 회차·미계측)이면 MISSING(결측 정직 — null/0 위장 금지, usage 대칭 계약).
      · total_sec : 전 단계 합계(초·소수1).
      · by_stage  : 소요 시간 상위 5개 단계 {단계: 초}(내림차순 — 병목 가시화). 판정 아님(원자료)."""
    ts = time_by_stage or {}
    if not ts:
        return MISSING
    # 숫자 값만 집계(방어적 — 비정상 값 무시). 총합은 소수1, 상위 5개 단계는 값 내림차순.
    pairs = [(k, v) for k, v in ts.items() if isinstance(v, (int, float))]
    total = round(sum(v for _, v in pairs), 1)
    top5 = dict(sorted(pairs, key=lambda kv: kv[1], reverse=True)[:5])
    return {"total_sec": total, "by_stage": top5}


def _summ_ending_runs(text: str):
    """ST-14 FIX-5: 무중단 동일 종결 키 run 축(형태 불문·대사 리셋) — 지문 리듬 벽의 advisory 원자료.

    tools.kiwi_metrics.uninterrupted_ending_runs 재사용(FIX-1 공용 함수·중복 구현 금지). 엔진 로드타임 tools
    import 0 계약: import 는 함수 안·lazy·try/except. 계측 불가(부품 부재·실패) 시 MISSING(결측 정직 — 판정·임계
    0, null 침묵 금지). run 이 하나도 없으면 빈 목록이 아니라 max_run=0·runs=[] 로 채워 '측정했으나 벽 없음'을
    정직하게 표기(MISSING 은 '측정 자체 불가'만).

    반환(부품 가용): {max_run, count, runs:[{key, n_sent, char_start, char_end}](상위 5개·긴 순), threshold}.
    무강제: 어떤 값도 회차를 막거나 자동 수정하지 않는다 — 상위 목록 원자료·advisory."""
    try:
        import tools.kiwi_metrics as km
        runs = km.uninterrupted_ending_runs(text or "", threshold=6)
    except Exception:
        return MISSING   # 부품 부재/실패 — 측정 자체 불가(결측 정직)
    top = sorted(runs, key=lambda r: -r["n_sent"])[:5]
    return {"max_run": max((r["n_sent"] for r in runs), default=0),
            "count": len(runs), "threshold": 6,
            "runs": [{"key": r["key"], "n_sent": r["n_sent"],
                      "char_start": r["char_start"], "char_end": r["char_end"]} for r in top]}


def _summ_tell_lexicon(text: str):
    """TL-1 사전식 검출축 요약 — engine.tell_lexicon(따온 검증법·14범주 한국어 이식)의 압축판.
    기존 ai_tell 축은 그대로 두고 병렬 신규 축으로만 추가(A/B 대조 목적 — 러너 tools/ab_tell_lexicon.py).
    결정론·정규식·의존 0이라 MISSING 경로 없음(빈 본문=0 집계 — '측정했으나 적중 0' 정직 표기).
    무강제: 값(적중수·1k당 밀도·범주별 수·상위 needle)만 — 판정 라벨·임계 0. 사전은 계측 전용
    (핑크 엘리펀트 금지 — 생성·윤문 프롬프트에 needle 노출 금지, tell_lexicon 모듈 계약)."""
    try:
        from .tell_lexicon import summarize_for_verification
        return summarize_for_verification(text or "")
    except Exception:
        return MISSING   # 모듈 로드 실패(비정상) — 결측 정직(null 침묵 금지)


def _summ_ending_contract(ec: dict):
    """ending_contract_eval 축 요약(EC-1 gt/ni 병렬). 미실행(빈 dict·계약 없음) 시 MISSING."""
    if not ec:
        return MISSING
    tiers = ec.get("tiers") or {}
    g = tiers.get("ground_truth_only") or {}
    n = tiers.get("with_narrative_inferred") or {}
    return {"total": len(ec.get("predicates") or []),
            "gt": {"satisfied": _nn(g.get("satisfied")), "open": _nn(g.get("open")), "blocked": _nn(g.get("blocked"))},
            "ni": {"satisfied": _nn(n.get("satisfied")), "open": _nn(n.get("open")), "blocked": _nn(n.get("blocked"))},
            "promotion_hints": _nn(ec.get("promotion_hints"))}


def _summ_world_reveal(world_reveal: list) -> dict:
    """SX-3 world_reveal 축 요약 — 이 회차 세계 노출 슬롯의 '존재 여부'(advisory·판정 없음).
    항목 수·항목 텍스트(원자료)만 병기한다. 빈 리스트=노출 없음(정직 — 발명 안 함·MISSING 아님, 필드는 항상 존재).
    무강제: 슬롯이 정독에서 설명 덤프를 유발하는지는 사람 판정(kill 기준)이지 이 축이 막지 않는다."""
    wr = [str(x).strip() for x in (world_reveal or []) if str(x).strip()]
    return {"count": len(wr), "items": wr}


def _summ_layout(text: str, *, cpl: int = 22, limit_lines: int = 3) -> dict:
    """TG-1 — 문단별 모바일 줄수 분포(웹소설 조판 관행: 한 문단 3줄 이내, 한 줄 cpl자 어림).
    고정 형식 규격 1개(사전 증식 없음). 판정·색상·임계 없음(advisory) — 값·위치만. 초과 상위 3만 머리 인용."""
    paras = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not paras:
        return {"cpl": cpl, "limit_lines": limit_lines, "n_paras": 0, "over": 0, "worst": []}
    over = sorted(((len(p) // cpl + 1, p[:30]) for p in paras if len(p) > cpl * limit_lines),
                  reverse=True)
    return {"cpl": cpl, "limit_lines": limit_lines, "n_paras": len(paras), "over": len(over),
            "worst": [{"lines": ln, "head": hd} for ln, hd in over[:3]]}


def _summ_dialogue_ledger(ledger) -> dict | str:
    """DG-1 화자별 어체 분포 — 저장된 원장 재집계(LLM 0). 원장 없으면 MISSING(결측 정직).

    speakers 값은 {화자: {어체: 건수}}. ending_class 는 휴리스틱('미소'·'아니오' 류 하오체 오탐 가능)이라
    오탐 가능 원자료다 — 판정 라벨·임계를 만들지 않는다(무강제·4-1)."""
    if not ledger:
        return MISSING
    from .dialogue_ledger import speaker_profile, UNKNOWN
    return {"quotes": len(ledger),
            "unknown": sum(1 for r in ledger if (r.get("speaker") or UNKNOWN) == UNKNOWN),
            # DG-4: 캐논 승격 행 수(지칭 행 = quotes - canon - unknown 근사) — 귀속 감사 원자료.
            #   canon 무표기(구 원장 행)는 비캐논 취급(PM 보정 ③) — 캐논·지칭은 자동 합산하지 않는다(⑦).
            "canon": sum(1 for r in ledger if r.get("canon")),
            "canon_speakers": sorted({(r.get("speaker") or "") for r in ledger if r.get("canon")}),
            "speakers": speaker_profile(ledger)}


def _summ_ontology(changes) -> dict | str:
    """OV-5 온톨로지 갱신 축 — 커밋/전체 건수 + 제안 스테이지 실패 가시화(침묵 사망 차단·매화 전축 검증 편입).
    기록 없음(빈 [])은 MISSING — '변경 0 제안'과 '구 회차·미실행'을 구분하지 않는 결측 정직(표식이 실패를 구분)."""
    if not changes:
        return MISSING
    rows = [c if isinstance(c, dict) else c.model_dump() for c in changes]
    return {"changes": sum(1 for r in rows if r.get("op") != "stage_failure"),
            "applied": sum(1 for r in rows if r.get("applied")),
            "stage_failed": any(r.get("op") == "stage_failure" for r in rows)}


_QUOTE_SPAN = re.compile(r'"([^"\n]{4,})"')


def _summ_voice_leak(record, leak_sources) -> dict | str:
    """VL-1(2026-08-15 감사): 보이스 카드·서술자 음성·설정집·확정 스토리의 문구가 본문에
    직역 노출됐는지 결정론 스윕(정규화 후 최장 공통 부분열 ≥8자 — 사후 감사 전용·차단 0).
    지문 누출(voice/narrator)은 본문 전체, 설정 언어의 대사 직역(bible/story)은 따옴표
    스팬만 대조한다. 소스 미제공(None)이면 MISSING(결측 정직)."""
    if not leak_sources:
        return MISSING
    try:
        from difflib import SequenceMatcher
        from .story_pass_prompts import _norm
        body = record.text or ""
        nbody = _norm(body)
        nquotes = _norm(" ".join(m.group(1) for m in _QUOTE_SPAN.finditer(body)))
        hits = []
        for name, (src, scope) in leak_sources.items():
            nsrc = _norm(src or "")
            target = nbody if scope == "body" else nquotes
            if len(nsrc) < 8 or not target:
                continue
            # VH-2(2026-08-17 실측): 소스당 최장 1건 보고는 차순위 직역을 가린다 — 7화 "매뉴얼에없는처리"(8자)
            #   불릿 직역이 12자 히트에 가려 미보고. 찾은 스팬을 소스에서 제거하고 재탐색(소스당 최대 4건).
            for _ in range(4):
                m = SequenceMatcher(None, nsrc, target, autojunk=False).find_longest_match(
                    0, len(nsrc), 0, len(target))
                if m.size < 8:
                    break
                hits.append({"source": name, "scope": scope,
                             "match": nsrc[m.a:m.a + m.size][:60], "len": m.size})
                nsrc = nsrc[:m.a] + nsrc[m.a + m.size:]
                if len(nsrc) < 8:
                    break
        return {"count": len(hits), "hits": hits}
    except Exception as e:
        return f"계산 실패: {str(e)[:80]}"


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def ending_literal_leak_sweep(needles: dict, targets: dict, min_len: int = 12) -> dict:
    """XR-1/XR-13(cross-review/005 §2.1·007 §7): 결말 정본 문장의 **직접 복사(문자 그대로)** 누출 스윕
    (결정론·LLM 0·advisory). **의역·사건 구조 변환·복선 밀도 같은 '효과 누출'은 이 축의 범위 밖이다** —
    count=0 을 '결말 영향 없음'으로 해석하지 않는다(효과 누출은 콜드리드·블라인드 정독 지표 소관).

    needles={필드명: 원문} — 작품 데이터 필드 파생만(어휘 사전·수기 목록·정규식 needle 로 변질되는 순간 폐기 계약).
    targets={채널명: 입력 조립물 텍스트}. 문장 단위로 쪼개 정규화(≥min_len 자) 후 부분 문자열 대조.
    게이트 아님 — 관측 축(2단 배선 ⓐ). 게이트 승격은 시놉 이중화 결정(XR-4 후속) 이후에만.
    needle 은 검사 코드 내부 대조 전용으로 어떤 프롬프트에도 노출되지 않는다(핑크 엘리펀트 무저촉)."""
    from .story_pass_prompts import _norm
    hits: list[dict] = []
    n_sent = 0
    for fname, raw in (needles or {}).items():
        for sent in _SENT_SPLIT.split((raw or "").strip()):
            sent = sent.strip()
            ns = _norm(sent)
            if len(ns) < min_len:
                continue
            n_sent += 1
            for cname, tgt in (targets or {}).items():
                if ns in _norm(tgt or ""):
                    hits.append({"field": fname, "channel": cname, "sentence": sent[:60]})
    return {"fields": sorted((needles or {}).keys()), "channels": sorted((targets or {}).keys()),
            "sentences": n_sent, "count": len(hits), "hits": hits}


def _summ_ending_literal_leak(sources) -> dict | str:
    """XR-1 축 요약 — 소스 미제공(None/빈 needles/빈 targets: 구 회차·gen_context 부재·결말 미설정)은 MISSING(결측 정직)."""
    if not sources or not sources.get("needles") or not sources.get("targets"):
        return MISSING
    try:
        return ending_literal_leak_sweep(sources["needles"], sources["targets"])
    except Exception as e:
        return f"계산 실패: {str(e)[:80]}"


def _summ_story_pass(record) -> dict | str:
    """SY-1: 확정 스토리 최종본 커버리지 — record.gen_context['story_pass'] 스냅샷 기준(결정론·LLM 0).
    잔여 판정은 어간 과반(보수적)이라 시제 층위를 못 가로질러 과소 소거가 상례 — 라벨은 '미실현 의심'."""
    gc = getattr(record, "gen_context", None)
    sp = gc.get("story_pass") if isinstance(gc, dict) else None
    if not isinstance(sp, dict) or not (sp.get("story") or "").strip():
        return "미적용(레거시 경로)"
    try:
        from . import story_pass_prompts as _spp
        from .drift import uncovered as _unc
        lines = [l[2:] if l.startswith("- ") else l for l in _spp.story_lines(sp.get("story", ""))]
        rem = [l for l in lines if _unc([l], record.text or "")]
        return {"lines": len(lines), "uncovered_suspect": len(rem),
                "suspect_ratio": round(len(rem) / max(1, len(lines)), 2),
                "suspect_lines": rem[:8],
                "time_source_cited": sp.get("time_source_cited"),
                "inject_fmt_errs": sp.get("fmt_errs") or [], "inject_bans": sp.get("bans") or [],
                "digest": sp.get("digest", "")}
    except Exception as e:
        return f"계산 실패: {str(e)[:80]}"


def build_verification(record, *, prev_texts: list[str] | None = None,
                       target_chars: int | None = None, gate: dict | None = None,
                       cold_read: dict | None = None,
                       leak_sources: dict | None = None,
                       ending_sources: dict | None = None) -> dict:
    """회차 통합 검증 리포트 — 전 축을 ChapterRecord 단일 dict에 결정론 집계(SSOT). LLM 0콜.

    설계 §PR-2: FINALIZED/ESCALATED 확정 직후 호출(status 분기 밖 — advisory 축 대칭). 전 축 키는
    항상 존재하고(집계 완전성), 안 돈 축은 MISSING 문자열로 명시한다(null 침묵 금지·누락 조용한 통과 차단).

    axes:
      canon           : 결정론 체커 최종 위반 집계(항상 존재)
      ai_tell         : KatFishNet 정규식 + Kiwi 종결/층위 + 인간 대역(미계산 시 MISSING·Kiwi 부재 시 kiwi=MISSING)
      tell_lexicon    : TL-1 사전식 검출(따온 검증법·14범주 한국어 이식) — 기존 ai_tell 무사전 축과
                        병렬 A/B 대조축(결정론·의존 0 → 결측 없음, 빈 본문=0 집계·advisory)
      retread         : 도입부 재탕계수(dp4 부품 엔진 이관 — 선행 회차 없으면 대조 기저 없어 MISSING)
      length          : 글자수 vs 출고 규범(target 미상 시 비교 MISSING)
      labels          : closing_device·hook_type·chapter_function·scene_form 자기 라벨(craft 순환 원자료)
      world_reveal    : SX-3 세계 노출 슬롯 존재 여부(항목 수·텍스트 — 빈 리스트=노출 없음, 정직)
      cold_read       : SX-2 프로즈만 읽는 신규 독자 프로브(이해도·혼란·궁금·하차 — 미실행 시 MISSING)
      style_repairs   : SP-1 Stage B 국소 수리 집계(스팬·변경·폴백 수)
      ending_runs     : ST-14 무중단 동일 종결 키 run(형태 불문·대사 리셋 — 상위 목록·부품 부재 시 MISSING)
      humanize        : HM-1b Claude 윤문 before/after 계측(스팬·변경·과윤문·작가확인·평균변경률·카테고리별)
      reader_feedback : 블라인드 독자 시뮬(미실행 시 MISSING)
      ending_contract : EC-1 gt/ni 병렬(미실행 시 MISSING)
      claim_audit     : 과거 회차 사실 모순 수(항상 존재 — 빈 리스트=0건)
      dialogue_ledger : DG-1 화자별 어체 분포(저장 원장 재집계 — 원장 없으면 MISSING: 구 회차·OFF·귀속 실패·
                        대사 0 을 구분하지 않는 정직 결측. 어체 분류는 휴리스틱 원자료 — 판정 라벨·임계 0)
      ending_literal_leak : XR-1/XR-13 결말 정본 문장의 '직접 복사' 대조(의역·효과 누출 불포함 — count=0 ≠ 결말
                        영향 없음. 소스 미제공 시 MISSING — 관측 축·게이트 0)
      timing          : TM-1 단계별 소요 시간 집계(total_sec + 상위 5개 단계 — usage 시간판, 미계측 시 MISSING)
      gate            : 러너/제품 정독 게이트 판정(PR-1이 채움 — 여기선 자리만, 미실행 시 MISSING)

    무강제: 어떤 축도 판정 라벨·색상·임계를 만들지 않는다 — 값·인간 대역·결측 표식만."""
    return {
        "chapter": record.chapter,
        "status": (record.status.value if hasattr(record.status, "value") else str(record.status)),
        "canon": _summ_canon(record),
        "ai_tell": _summ_ai_tell(record.ai_tell),
        # TL-1: 사전식 검출축(webnovel-writer 14범주 사전의 한국어 이식) — 기존 ai_tell(무사전 분포)과
        #   병렬 계측해 A/B 대조(러너 tools/ab_tell_lexicon.py). additive·advisory — 기존 축 무변경.
        "tell_lexicon": _summ_tell_lexicon(record.text),
        "retread": retread_coefficient(record.text, prev_texts or []),
        "length": _summ_length(record.text, target_chars),
        # TG-1: 조판 축 — 모바일 문단 줄수(고정 형식 규격·advisory). VI-1 우산.
        "layout": _summ_layout(record.text),
        "labels": {"closing_device": (record.closing_device or ""),
                   "hook_type": (record.hook_type or ""),
                   "chapter_function": (record.chapter_function or ""),
                   "scene_form": (getattr(record, "scene_form", "") or "")},   # SX-1: 장면 안무 자기 라벨(craft 순환 원자료)
        "world_reveal": _summ_world_reveal(getattr(record, "world_reveal", None)),   # SX-3: 세계 노출 슬롯 존재 여부(advisory)
        "style_repairs": _summ_style_repairs(record.style_repairs),
        "ending_runs": _summ_ending_runs(record.text),   # ST-14 FIX-5: 무중단 동일 종결 키 run 축(형태 불문·풍선 감시·advisory)
        "humanize": _summ_humanize(getattr(record, "humanize", None)),   # HM-1b: 윤문 before/after 계측(폐루프·설계 ⓓ)
        "reader_feedback": _summ_reader(record.reader_feedback),
        # SX-2: 콜드리드 독자 축(프로즈만 읽는 신규 독자 프로브) — 호출부가 실행 시 dict 로 주입, 미실행 시 MISSING(결측 정직).
        "cold_read": (cold_read if cold_read else MISSING),
        # SY-1 §5-bis ⓒ: 스토리 패스 축 — 최종본 커버리지('미실현 의심' 라벨·사실 주장 아님)+주입 검사.
        #   story 모드가 아닌 회차는 '미적용'(결측 정직 — 축 배선 전 story 생성 런 금지 계약의 검증 지점).
        "story_pass": _summ_story_pass(record),
        # VL-1: 보이스·설정 언어 직역 누출 스윕(소스 미제공 시 MISSING — 결측 정직)
        "voice_leak": _summ_voice_leak(record, leak_sources),
        # XR-1/XR-13: 결말 정본 문장의 '직접 복사' 누출 스윕(문자 한정 — 의역·효과 누출 불포함. 게이트 0)
        "ending_literal_leak": _summ_ending_literal_leak(ending_sources),
        "ending_contract": _summ_ending_contract(record.ending_contract_eval),
        "claim_audit": len(record.claim_audit or []),
        # DG-1: 화자별 어체 분포 축 — 저장된 대사 원장 재집계(LLM 0·advisory). 생성 입력 주입 금지 계약의
        #   소비처(검증 밥상) — 원장이 없으면 MISSING(빈 원장은 미실행/대사0 구분 불가 → 결측 정직).
        "dialogue_ledger": _summ_dialogue_ledger(getattr(record, "dialogue_ledger", None)),
        # OV-5: 온톨로지 갱신 축 — 캐논 갱신이 매화 검증에 편입(2·3화 침묵 실패가 리포트에 안 잡히던 갭 봉합)
        "ontology": _summ_ontology(getattr(record, "ontology_changes", None)),
        # TM-1: 단계별 소요 시간 집계(usage_by_stage 시간판 대칭) — 미계측(빈 dict) 시 MISSING(결측 정직·usage 대칭)
        "timing": _summ_timing(getattr(record, "time_by_stage", None)),
        # PR-1 제품 게이트 실행 시만 채움(러너 jsonl 무덤 해소) — 미실행 시 MISSING(결측 정직·필드 자리 유지)
        "gate": (gate if gate else MISSING),
    }
