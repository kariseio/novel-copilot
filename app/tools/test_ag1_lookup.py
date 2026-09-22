# -*- coding: utf-8 -*-
"""AG-1 회귀 — T1 프로바이더 강등·T2 lookup 결정론. LLM 0."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.llm.base import LLMProvider
from novelcopilot.engine.lookup import CanonLookup, TOOL_SCHEMA


class _EchoProvider(LLMProvider):
    def chat(self, messages, *, temperature=0.7, max_tokens=2200, json_mode=False):
        return "단발응답"
    def embed(self, texts):
        return [[0.0] for _ in texts]


def test_chat_tools_base_degrades_to_chat():
    p = _EchoProvider()
    out = p.chat_tools([{"role": "user", "content": "x"}],
                       tools=TOOL_SCHEMA, tool_handler=lambda n, a: "결과")
    assert out == "단발응답"            # 미지원 프로바이더 = 안전 강등(기존 경로)
    assert p.usage.tool_calls == 0


class _FakeOnt:
    def __init__(self):
        self.entities = {"junho": NS(name="서준호", aliases=["준호"], etype="character",
                                     attrs={"money_won": None}, voice=""),
                         "const": NS(name="도시 물가", aliases=["라면 값"], etype="worldrule",
                                     attrs={"ramen_price_won": None}, voice="")}
        self.edges = [NS(src_id="junho", dst_id="const", rel_id="knows", eff_from=1)]
        self.vocab = NS(label=lambda k: {"money_won": "소지금(원)", "ramen_price_won": "라면 값(원)"}.get(k, k))
    def state_as_of(self, eid, a, ch):
        return {("junho", "money_won"): 12000, ("const", "ramen_price_won"): 3000}.get((eid, a))
    def binding_state_as_of(self, eid, a, ch):
        return self.state_as_of(eid, a, ch)
    def is_actor(self, etype):                                 # CX-2 계약 미러(실물 Ontology 와 동형)
        return etype == "character"
    def public_attrs(self, eid, ch):                           # CX-2/3: binding 우선 단일값 계약 미러
        e = self.entities.get(eid)
        out = []
        for a in (getattr(e, "attrs", None) or {}):
            b = self.binding_state_as_of(eid, a, ch)
            if b is not None:
                out.append((a, b, True))
        return out
    def rel_spec(self, rid):
        return NS(label="아는 사이")


def _lk():
    bible = [NS(title="라면 문화", keywords=["라면", "국물"], prose="이 도시의 라면은 싸고 진하다.",
                status="author_approved")]   # XR-8: 캐논 병행 계약은 승인 항목의 것 — 상태 명시
    return CanonLookup(_FakeOnt(), bible, 13)


def test_lookup_entity_exact_and_constants():
    lk = _lk()
    out = lk.handle("lookup_canon", {"query": "라면 값"})
    assert "라면 값(원)=3000" in out and "라면 문화" in out    # 상수 개체 + bible 병행
    out2 = lk.handle("lookup_canon", {"query": "서준호"})
    assert "소지금(원)=12000" in out2 and "아는 사이" in out2   # 상태 + 관계 1홉


def test_lookup_unregistered_honest():
    lk = _lk()
    out = lk.handle("lookup_canon", {"query": "존재안함XYZ"})
    assert "미등재" in out                                     # 발명 차단의 본체
    assert len(lk.log) == 1 and lk.log[0]["query"] == "존재안함XYZ"


def test_lookup_partial_returns_candidates_only():
    lk = _lk()
    out = lk.handle("lookup_canon", {"query": "준호"})          # '준호'는 별칭 정확 일치
    assert "소지금(원)=12000" in out
    out2 = lk.handle("lookup_canon", {"query": "서준"})         # 부분 일치 → 후보만
    assert "후보" in out2 and "소지금" not in out2


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("전체 통과")
