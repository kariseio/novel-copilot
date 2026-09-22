# -*- coding: utf-8 -*-
"""VP-1 — 조판 노브(paragraph_max_sents→narr_chunk) 테스트: 기본 2=종전 바이트 동일, 4=완화·멱등."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.engine.textfmt import reflow_paragraphs

P6 = "하나였다. 둘이었다. 셋이었다. 넷이었다. 다섯이었다. 여섯이었다."
P4 = "하나였다. 둘이었다. 셋이었다. 넷이었다."


def test_default_matches_legacy_two_stride():
    out = reflow_paragraphs(P6)
    assert out == "하나였다. 둘이었다.\n\n셋이었다. 넷이었다.\n\n다섯이었다. 여섯이었다."
    assert reflow_paragraphs(P6) == reflow_paragraphs(P6, narr_chunk=2)


def test_knob_four_relaxes_and_keeps_four_intact():
    assert reflow_paragraphs(P4, narr_chunk=4) == P4          # 상한 이내 순지문 → 무변경
    out = reflow_paragraphs(P6, narr_chunk=4)                  # 6문장 → ≤4문장 청크
    paras = out.split("\n\n")
    assert all(p.count(".") <= 4 for p in paras) and "".join(P6.split()) == "".join(out.split())
    assert reflow_paragraphs(out, narr_chunk=4) == out         # 멱등


def test_dialogue_handling_knob_independent():
    mixed = '그가 다가왔다. "멈춰." 나는 멈췄다.'
    # 대사 혼재 문단의 처리(분리 정책)는 노브와 무관하게 동일해야 한다 — 노브는 순지문 청크 폭만 바꾼다.
    assert reflow_paragraphs(mixed, narr_chunk=4) == reflow_paragraphs(mixed, narr_chunk=2)


if __name__ == "__main__":
    test_default_matches_legacy_two_stride()
    test_knob_four_relaxes_and_keeps_four_intact()
    test_dialogue_handling_knob_independent()
    print("VP-1 3/3 통과")
