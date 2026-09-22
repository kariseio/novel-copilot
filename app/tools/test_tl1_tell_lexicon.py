# -*- coding: utf-8 -*-
"""TL-1 검증 — 사전식 AI티 검출축(따온 검증법·한국어 이식) (LLM 0콜).

이 테스트:
  ⓐ 사전 무결성 — 14범주·needle ≥2자·범주 간 중복 없음·버전 상수 존재
  ⓑ 계수 정확 — 심은 needle 이 정확히 계수(활용 변이 흡수 — 접두 매칭)
  ⓒ 겹침 마스킹 — "바로 그 순간"이 "그 순간"으로 이중 계수되지 않음(DE-2 형제 패턴)
  ⓓ 길이 불변 — 동일 본문 2배 반복 시 hits_per_1k 동일(밀도축 계약)
  ⓔ 무강제 계약 — 산출 dict 에 판정성 키(pass/fail/label/verdict) 부재
  ⓕ 빈 본문 정직 — 0 집계(예외·null 아님)
  ⓖ 검증 배선 — build_verification 에 tell_lexicon 축 존재 + 기존 축 키 전부 그대로(additive)
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_tl1_tell_lexicon.py
"""
from __future__ import annotations
import pathlib
import sys
from types import SimpleNamespace

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from novelcopilot.engine.tell_lexicon import (
    LEXICON, LEXICON_VERSION, tell_lexicon_profile, summarize_for_verification)
from novelcopilot.engine.verification import build_verification, MISSING


def t_lexicon_integrity():
    assert len(LEXICON) == 14
    seen = {}
    for cat, needles in LEXICON.items():
        assert needles, cat
        for n in needles:
            assert len(n) >= 2, f"어간≥2 위반: {cat}/{n}"
            assert n not in seen, f"범주 간 중복 needle: {n} ({seen.get(n)} vs {cat})"
            seen[n] = cat
    assert isinstance(LEXICON_VERSION, str) and LEXICON_VERSION


def t_count_with_conjugation():
    # 접두 needle("미간을 찌푸")이 활용 변이(찌푸렸다/찌푸리며)를 흡수하는지
    text = "그는 미간을 찌푸렸다. 그녀도 미간을 찌푸리며 물러섰다. 요컨대 둘 다 불쾌했다."
    p = tell_lexicon_profile(text)
    cats = p["by_category"]
    assert cats["동작상투"]["hits"] == 2, cats
    assert cats["총괄요약"]["hits"] == 1, cats
    assert p["total_hits"] == 3
    assert p["categories_hit"] == 2
    assert ["미간을 찌푸", 2] in p["top_hits"]


def t_overlap_masking():
    # "바로 그 순간"(긴 needle 우선 마스킹) 1회 — 내부의 "그 순간"이 이중 계수되면 2가 된다
    p = tell_lexicon_profile("바로 그 순간 문이 열렸다.")
    hits = p["by_category"]["전환템플릿"]
    assert hits["hits"] == 1, hits
    assert ["바로 그 순간", 1] in hits["top"]
    # 단독 "그 순간"은 정상 계수
    p2 = tell_lexicon_profile("그 순간 문이 열렸다.")
    assert p2["by_category"]["전환템플릿"]["hits"] == 1


def t_length_invariance():
    base = "그는 천천히 고개를 끄덕였다. 침묵이 흘렀다. 문득 알 수 없는 감정이 북받쳤다.\n"
    p1, p2 = tell_lexicon_profile(base), tell_lexicon_profile(base * 2)
    assert p1["hits_per_1k"] == p2["hits_per_1k"], (p1["hits_per_1k"], p2["hits_per_1k"])
    assert p2["total_hits"] == 2 * p1["total_hits"]


def t_no_verdict_keys():
    p = tell_lexicon_profile("천천히 걸었다.")
    banned = {"pass", "fail", "label", "verdict", "score", "grade", "ok"}
    assert not (set(map(str.lower, p.keys())) & banned), p.keys()
    s = summarize_for_verification("천천히 걸었다.")
    assert not (set(map(str.lower, s.keys())) & banned), s.keys()


def t_empty_text():
    p = tell_lexicon_profile("")
    assert p["total_hits"] == 0 and p["hits_per_1k"] == 0.0 and p["by_category"] == {}
    assert p["n_chars"] == 0 and p["lexicon_version"] == LEXICON_VERSION


def _fake_record(text: str):
    return SimpleNamespace(
        chapter=1, status="finalized", final_violations=[], ai_tell={}, text=text,
        closing_device="", hook_type="", chapter_function="", scene_form="",
        world_reveal=[], style_repairs=[], humanize=[], reader_feedback={},
        ending_contract_eval={}, claim_audit=[], time_by_stage={})


def t_verification_wiring():
    v = build_verification(_fake_record("그는 슬며시 웃었다. 바로 그때 정적이 흘렀다."))
    tl = v["tell_lexicon"]
    assert tl is not MISSING and tl["total_hits"] == 3, tl
    assert tl["lexicon_version"] == LEXICON_VERSION
    assert set(tl) == {"total_hits", "hits_per_1k", "categories_hit",
                       "by_category", "top_hits", "lexicon_version"}
    # additive 계약 — 기존 축 키 전부 그대로(PR-2 SSOT 무변경)
    for k in ("canon", "ai_tell", "retread", "length", "labels", "world_reveal",
              "style_repairs", "ending_runs", "humanize", "reader_feedback",
              "cold_read", "ending_contract", "claim_audit", "timing", "gate"):
        assert k in v, k
    # 빈 본문 — 축은 존재하고 0 집계(결측 아님·측정 정직)
    v0 = build_verification(_fake_record(""))
    assert v0["tell_lexicon"]["total_hits"] == 0


if __name__ == "__main__":
    tests = [t_lexicon_integrity, t_count_with_conjugation, t_overlap_masking,
             t_length_invariance, t_no_verdict_keys, t_empty_text, t_verification_wiring]
    results = []
    for t in tests:
        try:
            t(); results.append(True)
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            results.append(False)
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
