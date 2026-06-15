# -*- coding: utf-8 -*-
"""2개월차 검증 (블라인드 감사 c-확정 처방, 무강제 적응) —
G6 설계콜 인물 컨텍스트 / G1-P2 본문 상환 검출 / G2 블라인드 독자 데스크. 모두 LLM 0콜(스텁).
실행: PYTHONPATH=app python tools/test_month2_g6_g1p2_g2.py
"""
from __future__ import annotations
import sys

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.ledger import PromiseLedger
from novelcopilot.engine.factory import build_engine
from novelcopilot.engine.ledger_ops import (sync_ledger_from_spine, detect_payoffs, mark_paid,
                                            chapters_since_payoff, _key)
from novelcopilot.engine.reader_desk import reader_prediction
from novelcopilot.services.copilot import _cast_context
from novelcopilot.llm.base import LLMProvider


class ScriptFake(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = []

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        self.calls.append(k)
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


# ---------- G6: 설계 콜 인물 컨텍스트 ----------
def test_cast_context() -> bool:
    s = get_settings()
    w = WorldConfig(title="t", genre="x", entities=[
        EntitySpec(id="hero", name="서진우", profile="만년 F급, 회귀 후 미래 지식 보유. 복수가 욕망."),
        EntitySpec(id="rival", name="강현식", profile="라이벌 헌터, 인정 욕구가 강함.", base_status="dead")])
    ont = build_engine(w, ScriptFake([{}]), s).ontology
    ctx = _cast_context(ont, w, ["hero", "rival", "ghost"], 5)   # ghost=무효 id → 무시
    ok = ("서진우" in ctx and "만년 F급" in ctx               # 이름+프로필 주입
          and "강현식" in ctx and "현재상태=dead" in ctx       # 현재 상태(사망) 결정론 조회
          and "ghost" not in ctx)
    # 빈 입력 → 빈 문자열(하위호환)
    ok &= (_cast_context(ont, w, [], 5) == "")
    print(f"[{'OK' if ok else 'FAIL'}] 인물 컨텍스트: 이름+프로필+현재상태(dead) 주입·무효id 무시·빈입력 안전")
    return ok


# ---------- G1-P2: 본문 상환 검출(측정) ----------
def _spine_with_plants() -> NarrativeSpine:
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1", target_chapters=2,
                    plants=["회귀 전 결정적 실수", "숨겨진 코어 기록"], payoffs=[])])])


def test_payoff_detect() -> bool:
    led = PromiseLedger()
    sync_ledger_from_spine(led, _spine_with_plants(), 3)         # open 2건
    text = "그는 마침내 회귀 전 결정적 실수의 전말을 모두에게 밝혔다. 길드가 술렁였다."
    rid = _key("회귀 전 결정적 실수")
    # 하나는 실재 증거(지불), 하나는 환각 증거(본문 미실재 → 폐기)
    fake = ScriptFake([{"paid": [{"id": rid, "evidence": "회귀 전 결정적 실수의 전말을 모두에게 밝혔다"},
                                 {"id": _key("숨겨진 코어 기록"), "evidence": "본문에 전혀 없는 가짜 증거 문장"}]}])
    paid = detect_payoffs(fake, text, led.open_promises(), 6)
    ok = (paid == [rid])                                        # 환각(증거 미실재) 폐기, 실재만
    n = mark_paid(led, paid, 6)
    ok &= (n == 1 and led.by_id(rid).status == "paid"
           and led.last_payoff_chapter == 6
           and chapters_since_payoff(led, 8) == 2)              # 카운터가 실데이터로(이전엔 None)
    # 빈 약속/빈 본문 → 콜 없이 빈 결과
    ok &= (detect_payoffs(ScriptFake([{}]), "", led.open_promises(), 7) == []
           and detect_payoffs(ScriptFake([{}]), "본문", [], 7) == [])
    print(f"[{'OK' if ok else 'FAIL'}] 상환 검출: 증거 실재만 지불({paid})·환각 폐기·since_payoff 실데이터화(None→2)")
    return ok


# ---------- G2: 블라인드 독자 데스크(advisory) ----------
def test_reader_desk() -> bool:
    fake = ScriptFake([{"got": "각성 등급 공개", "pay_next": True, "why": "다음 전투가 궁금해 결제"}])
    p = reader_prediction(fake, "이번 회차 본문...", "지금까지 줄거리", "헌터물")
    ok = (p and p["got"] == "각성 등급 공개" and p["pay_next"] is True and "결제" in p["why"])
    # 빈 응답 → None(비차단), 빈 본문 → None(콜 안 함)
    ok &= (reader_prediction(ScriptFake([{"got": "", "why": ""}]), "본문", "", "x") is None)
    ok &= (reader_prediction(ScriptFake([{}]), "", "", "x") is None)
    print(f"[{'OK' if ok else 'FAIL'}] 독자 데스크: got/pay_next/why 파싱·빈응답 None·빈본문 None(advisory·비차단)")
    return ok


if __name__ == "__main__":
    results = [test_cast_context(), test_payoff_detect(), test_reader_desk()]
    print("\n2개월차(G6/G1-P2/G2) 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
