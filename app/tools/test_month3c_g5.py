# -*- coding: utf-8 -*-
"""3개월차 잔여 검증 — G5 장르 계약(서술 컨텍스트, 강제 아님). LLM 0콜.
(G10=회차내 자기일관성은 _continuity_polish 프롬프트 확장이라 스모크가 경로 커버.)
실행: PYTHONPATH=app python tools/test_month3c_g5.py
"""
from __future__ import annotations
import sys

from novelcopilot.domain.world import WorldConfig, GenreContract
from novelcopilot.worldgen.arc_planner import _contract_block
from novelcopilot.engine.reader_desk import reader_prediction
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


def test_contract_block_and_roundtrip() -> bool:
    gc = GenreContract(pleasure_engine="정보우위로 미래를 바꾸는 통쾌함",
                       reader_expectations=["사이다", "성장", "역전"],
                       vocabulary_tone="현대 헌터물 어휘", premise_asset="회귀+미래지식은 장기 자산")
    w = WorldConfig(title="t", genre="헌터", genre_contract=gc)
    blk = _contract_block(w)
    ok = ("독자 쾌감" in blk and "정보우위" in blk and "독자 기대" in blk
          and "장기 자산" in blk and "참고" in blk
          and "반드시" not in blk and "해라" not in blk)            # 명령형 없음(서술 정보만)
    ok &= (_contract_block(WorldConfig(title="t", genre="x")) == "")  # 미생성→빈(하위호환)
    # round-trip(객체) + dict 검증(worldgen 산출 형태)
    w2 = WorldConfig.model_validate(w.model_dump())
    ok &= (w2.genre_contract.pleasure_engine == gc.pleasure_engine)
    w3 = WorldConfig.model_validate({"title": "t", "genre_contract":
                                     {"pleasure_engine": "x", "reader_expectations": ["a"]}})
    ok &= (w3.genre_contract.reader_expectations == ["a"])
    print(f"[{'OK' if ok else 'FAIL'}] 장르 계약: 서술 렌더(명령형 없음)·미생성 빈문자열·round-trip·dict 검증")
    return ok


def test_reader_desk_expectations() -> bool:
    fake = ScriptFake([{"got": "각성 등급 공개", "pay_next": True, "why": "궁금"}])
    p = reader_prediction(fake, "본문", "줄거리", "헌터", expectations=["사이다", "성장"])
    ok = (p and p["got"] == "각성 등급 공개" and p["pay_next"] is True)
    ok &= (reader_prediction(ScriptFake([{"got": "x", "why": "y"}]), "본문", "", "헌터") is not None)  # 기대 없어도 동작
    print(f"[{'OK' if ok else 'FAIL'}] 독자 데스크 장르 기대 주입(advisory)·하위호환")
    return ok


if __name__ == "__main__":
    results = [test_contract_block_and_roundtrip(), test_reader_desk_expectations()]
    print("\n3개월차 잔여(G5) 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
