# -*- coding: utf-8 -*-
"""HM-3 회귀 스위트 — narration_detect(N-1/N-2 LLM 탐지)의 결정론 계약 잠금(LLM 0·FakeProv).

잠그는 계약: ① 인용→char 재앵커(정확 일치 우선·공백 정규화 폴백) ② 지문-only 백스톱(대사행 배제)
③ 동일 인용 중복의 비겹침 앵커 ④ 실패/무효 폴백 [] ⑤ 정밀 모드의 잉여 근거 검증(원문 실재+선행)
⑥ HM-4 문단 그룹핑(같은 문단 병합·문서 순서·전범위 단독 유지). PM 발권 HM-9(스위트 잠금) 이행분.
실행: PYTHONUTF8=1 PYTHONPATH=. py -3.12 -m pytest tools/test_hm3_narration_detect.py -q
"""
from __future__ import annotations

from novelcopilot.engine import narration_detect as nd
from novelcopilot.engine.humanize_pass import group_findings_by_paragraph, build_group_directive


class _Usage:
    chat_tokens = 0


class FakeProv:
    """chat_json 페이로드를 그대로 돌려주는 판정 provider 대역(LLM 0)."""

    def __init__(self, payload):
        self.payload = payload
        self.usage = _Usage()

    def chat_json(self, messages, temperature=0.2):
        return self.payload


def test_catchall_anchor_dialogue_and_missing():
    text = ('그는 문을 열었다.\n'
            '그것은 그가 오래 기다린 순간이었다. 분노가 치밀었다.\n'
            '"가자, 이제."')
    prov = FakeProv({"spans": [
        {"category": "N-1", "quote": "그것은 그가 오래 기다린 순간이었다.", "why": "자평"},
        {"category": "N-2", "quote": "분노가 치밀었다.", "why": "감정 명명"},
        {"category": "N-1", "quote": "가자, 이제.", "why": "대사행 — 배제돼야"},
        {"category": "N-1", "quote": "본문에 없는 인용", "why": "앵커 실패해야"},
    ]})
    res = nd.detect_narration_tells(prov, text, max_spans=6)
    assert [f["category"] for f in res] == ["N-1", "N-2"]
    a = res[0]["span"]
    assert text[a["char_start"]:a["char_end"]] == "그것은 그가 오래 기다린 순간이었다."
    assert res[0]["severity"] == "S1" and res[1]["severity"] == "S2"
    assert all(f["metric"]["mode"] == "catchall" for f in res)


def test_whitespace_fallback_anchor():
    text = "그는 오래  기다린 사람이었다."   # 본문엔 공백 2개 — 모델이 1개로 흘린 인용도 근사 매칭
    prov = FakeProv({"spans": [{"category": "N-1", "quote": "그는 오래 기다린 사람이었다.", "why": "w"}]})
    assert len(nd.detect_narration_tells(prov, text)) == 1


def test_duplicate_quote_non_overlapping():
    text = "닫혔다. 그냥 닫혔다. 닫혔다."
    prov = FakeProv({"spans": [
        {"category": "N-1", "quote": "닫혔다.", "why": "1"},
        {"category": "N-1", "quote": "닫혔다.", "why": "2"},
    ]})
    res = nd.detect_narration_tells(prov, text)
    assert len(res) == 2
    assert res[0]["span"]["char_start"] != res[1]["span"]["char_start"]


def test_failure_paths_return_empty():
    text = "지문이다."
    assert nd.detect_narration_tells(None, text) == []
    assert nd.detect_narration_tells(FakeProv("not a dict"), text) == []
    assert nd.detect_narration_tells(FakeProv({"spans": [{"category": "N-9", "quote": "x"},
                                                         {"category": "N-1", "quote": ""}]}), text) == []
    assert nd.detect_narration_tells(FakeProv({"spans": []}), "") == []


def test_precise_evidence_must_exist_and_precede():
    text = '그가 웃었다. 입꼬리가 올라갔다.\n기뻤다.\n다음 문장이다.'
    ok = FakeProv({"spans": [{"category": "N-2", "quote": "기뻤다.", "why": "입꼬리가 올라갔다."}]})
    res = nd.detect_narration_tells(ok, text, mode="precise")
    assert len(res) == 1 and res[0]["metric"]["mode"] == "precise"
    # 근거가 대상 스팬 '뒤' 문장 → 드롭
    after_ev = FakeProv({"spans": [{"category": "N-2", "quote": "기뻤다.", "why": "다음 문장이다."}]})
    assert nd.detect_narration_tells(after_ev, text, mode="precise") == []
    # 근거가 본문에 없음 → 드롭
    no_ev = FakeProv({"spans": [{"category": "N-2", "quote": "기뻤다.", "why": "본문에 없는 근거"}]})
    assert nd.detect_narration_tells(no_ev, text, mode="precise") == []
    # catchall 은 근거 검증 없음(가시화 전수)
    assert len(nd.detect_narration_tells(no_ev, text, mode="catchall")) == 1


def test_paragraph_grouping_merge_order_and_passthrough():
    text = '문단 하나다. 자평이다.\n\n문단 둘이다. 또 자평이고 감정도 명명한다.\n\n셋째 문단.'
    fs = [
        {"category": "N-1", "severity": "S1", "span": {"char_start": 8, "char_end": 13, "text": "자평이다."}},
        {"category": "N-1", "severity": "S1", "span": {"char_start": 22, "char_end": 28, "text": "또 자평"}},
        {"category": "N-2", "severity": "S2", "span": {"char_start": 30, "char_end": 40, "text": "감정도 명명한다"}},
        {"category": "N-5", "severity": "S1", "span": {"char_start": 0, "char_end": len(text), "text": ""}},
    ]
    g = group_findings_by_paragraph(text, fs)
    assert len(g) == 3                       # 문단1 단독 + 문단2 병합 + 전범위 단독
    merged = [f for f in g if f.get("_group_cats")]
    assert len(merged) == 1 and merged[0]["_group_cats"] == ["N-1", "N-2"]
    d = build_group_directive(merged[0])
    assert "옮겨라" in d and "몸짓·호흡·시선·목소리" in d   # N-1+N-2 처방 합성
    starts = [(f.get("span") or {}).get("char_start") for f in g if (f.get("span") or {}).get("text")]
    assert starts == sorted(starts)          # 문서 순서(재현 가능)
