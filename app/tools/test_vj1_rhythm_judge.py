# -*- coding: utf-8 -*-
"""VJ-1 — 검증용 낭독 리듬 판정(judge_rhythm)·문단 짜임 참고 계측 테스트(LLM 0·가짜 provider)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.engine.style_judge import (
    build_paragraph_reference, judge_rhythm, _format_paragraph_reference)


class _FakeProvider:
    def __init__(self, ret=None, raise_=False):
        self.ret = ret
        self.raise_ = raise_
        self.calls = []

    def chat_json(self, messages, temperature=0.0, **kw):
        self.calls.append(messages)
        if self.raise_:
            raise RuntimeError("boom")
        return self.ret


def test_paragraph_reference_counts():
    # 서술 2문장 문단 ×3 연속 + 대사 문단(절단) + 1문장 문단
    text = ("첫 문장이다. 둘째 문장이다.\n\n셋째다. 넷째다.\n\n다섯째다. 여섯째다.\n\n"
            "\"대사 문단은 서술이 아니다.\"\n\n일곱째 혼자다.")
    ref = build_paragraph_reference(text)
    assert ref["n_narr_paras"] == 4
    assert ref["dist"]["2"] == 3 and ref["dist"]["1"] == 1
    assert ref["max_two_sent_streak"] == 3
    assert 0 < ref["two_sent_share"] < 1
    assert "2문장 문단 최장 연속 3" in _format_paragraph_reference(ref)


def test_judge_rhythm_parses_axis_and_reference():
    ret = {"needs_repair": True,
           "spans": [{"quote": "첫 문장이다. 둘째 문장이다.", "axis": "문단", "why": "같은 박자"},
                     {"quote": "셋째다.", "axis": "엉뚱축", "why": ""},
                     {"quote": "", "axis": "문말", "why": "빈 인용은 버려진다"}],
           "reason": "리듬 단조"}
    fp = _FakeProvider(ret=ret)
    j = judge_rhythm(fp, "첫 문장이다. 둘째 문장이다.\n\n셋째다. 넷째다.")
    assert j is not None and j["needs_repair"] is True
    assert [s["axis"] for s in j["spans"]] == ["문단", ""]   # 허용값 밖 축은 빈 문자열·빈 인용 제거
    assert j["reference"]["paragraphs"]["n_narr_paras"] == 2
    # 프롬프트에 참고 블록 두 종(어미 run·문단 분포) 동봉 — 판정 기준 아님 명시
    user = fp.calls[0][1]["content"]
    assert "[참고 자료" in user and "문단 짜임 분포" in user


def test_judge_rhythm_failure_paths():
    assert judge_rhythm(_FakeProvider(ret={}), "") is None            # 본문 전무
    assert judge_rhythm(_FakeProvider(raise_=True), "본문이다.") is None   # 콜 실패
    assert judge_rhythm(_FakeProvider(ret="문자열"), "본문이다.") is None   # 파싱 불가
    j = judge_rhythm(_FakeProvider(ret={"needs_repair": False, "spans": []}), "본문이다.")
    assert j is not None and j["needs_repair"] is False and j["spans"] == []


if __name__ == "__main__":
    test_paragraph_reference_counts()
    test_judge_rhythm_parses_axis_and_reference()
    test_judge_rhythm_failure_paths()
    print("VJ-1 3/3 통과")
