# -*- coding: utf-8 -*-
"""CN-2 claim-audit 테스트 — RAG-grounded·비차단·콜가드·편측폐기·cap. 실 LLM 0콜(fake provider)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.engine.claim_audit import audit_chapter
from novelcopilot.domain.types import RetrievedItem


class FakeRag:
    def __init__(self, items):
        self.items = items
        self.queries = []

    def search(self, q, as_of, k=4):
        self.queries.append((q, as_of))
        return self.items


class Prov:
    def __init__(self, ret):
        self.ret = ret
        self.calls = 0

    def chat_json(self, msg, temperature=0.0):
        self.calls += 1
        if isinstance(self.ret, Exception):
            raise self.ret
        return self.ret


PAST = [RetrievedItem(source="rag_chunk", ref="2", text="철수의 검은 푸른 빛이었다.")]


def test_no_call_on_chapter_one():
    p = Prov({"contradictions": []})
    assert audit_chapter(p, FakeRag(PAST), "본문", 1) == [] and p.calls == 0   # ch1=과거 없음 → 콜 0


def test_no_call_when_no_past():
    p = Prov({"contradictions": []})
    assert audit_chapter(p, FakeRag([]), "본문", 5) == [] and p.calls == 0      # 검색결과 0 → 콜 0


def test_excludes_self_via_as_of():
    rag = FakeRag(PAST)
    audit_chapter(Prov({"contradictions": []}), rag, "본문", 5)
    assert rag.queries and rag.queries[0][1] == 4                              # as_of=ch-1(=4): 새 회차 자신 제외


def test_finds_contradiction():
    p = Prov({"contradictions": [{"claim": "철수의 검은 붉은 빛", "canon": "철수의 검은 푸른 빛",
                                  "ref": "2", "why": "같은 검 색 충돌"}]})
    out = audit_chapter(p, FakeRag(PAST), "철수가 붉은 검을 들었다", 5)
    assert len(out) == 1 and out[0]["ref"] == "2" and "붉은" in out[0]["claim"]


def test_non_blocking_on_exception():
    assert audit_chapter(Prov(RuntimeError("boom")), FakeRag(PAST), "본문", 5) == []   # 비차단


def test_one_sided_dropped():
    p = Prov({"contradictions": [{"claim": "무언가", "canon": "", "ref": "2"},
                                 {"claim": "", "canon": "이전", "ref": "2"}]})
    assert audit_chapter(p, FakeRag(PAST), "본문", 5) == []                     # 양측 진술 없으면 폐기


def test_cap_limits_findings():
    many = {"contradictions": [{"claim": f"c{i}", "canon": f"k{i}", "ref": "2"} for i in range(20)]}
    out = audit_chapter(Prov(many), FakeRag(PAST), "본문", 5, cap=6)
    assert len(out) == 6                                                       # cap 적용
