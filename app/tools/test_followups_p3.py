# -*- coding: utf-8 -*-
"""후속 검증 — G1-P3 본문 약속 정산(지불+신규 약속 한 콜). LLM 0콜.
실행: PYTHONPATH=app python tools/test_followups_p3.py
"""
from __future__ import annotations
import sys

from novelcopilot.domain.ledger import PromiseLedger
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.engine.ledger_ops import (sync_ledger_from_spine, reconcile_ledger_from_prose,
                                            mark_paid, add_opened_promises, chapters_since_payoff, _key)
from novelcopilot.llm.base import LLMProvider


class ScriptFake(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = 0

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def _spine() -> NarrativeSpine:
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1,
                    plants=["회귀 전 결정적 실수"], payoffs=[])])])


def test_reconcile_paid_and_opened() -> bool:
    led = PromiseLedger()
    sync_ledger_from_spine(led, _spine(), 3)            # open 1: '회귀 전 결정적 실수'
    rid = _key("회귀 전 결정적 실수")
    text = "그는 회귀 전 결정적 실수의 전말을 밝혔다. 그리고 새로운 적, 그림자 단체의 존재가 드러났다."
    fake = ScriptFake([{
        "paid": [{"id": rid, "evidence": "회귀 전 결정적 실수의 전말을 밝혔다"},
                 {"id": _key("없는 약속"), "evidence": "본문에 없는 가짜"}],   # 환각 → 폐기
        "opened": [{"text": "그림자 단체의 정체는 무엇인가", "kind": "질문"},
                   {"text": "", "kind": "x"}]}])                              # 빈 text → 무시
    recon = reconcile_ledger_from_prose(fake, text, led.open_promises(), 6)
    ok = (recon["paid"] == [rid] and len(recon["opened"]) == 1
          and recon["opened"][0]["kind"] == "질문")
    n_paid = mark_paid(led, recon["paid"], 6)
    n_open = add_opened_promises(led, recon["opened"], 6)
    ok &= (n_paid == 1 and n_open == 1
           and led.by_id(rid).status == "paid"
           and chapters_since_payoff(led, 8) == 2                            # 지불 실데이터
           and any(p.text == "그림자 단체의 정체는 무엇인가" and p.status == "open" for p in led.promises))
    # 멱등: 같은 신규 약속 재등록 0
    n_again = add_opened_promises(led, recon["opened"], 7)
    ok &= (n_again == 0)
    # 빈 본문 → 콜 없이 빈 결과
    ok &= (reconcile_ledger_from_prose(ScriptFake([{}]), "", led.open_promises(), 7) == {"paid": [], "opened": []})
    print(f"[{'OK' if ok else 'FAIL'}] 약속 정산: 지불 환각폐기({recon['paid']})·신규 약속 등록(멱등)·since_payoff 실데이터(2)")
    return ok


if __name__ == "__main__":
    results = [test_reconcile_paid_and_opened()]
    print("\n후속(G1-P3) 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
