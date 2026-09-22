# -*- coding: utf-8 -*-
"""CJ-1 — chat_json 결측 정직 가드 검증(실 LLM 0콜).

실사고(HZ-3, 2026-08-07): 판정 콜 출력 예산 소진 → 빈 1차 응답 → 교정 재시도가 빈 입력에 {} 반환 →
{} 가 유효 dict 라 통과 → needs_repair=False·reason="" 세탁(2~13화 판정 전멸). 가드 계약:
  ① 빈 1차 응답 → 즉시 ValueError(교정 재시도 콜 자체를 안 태움 — 세탁 경로+헛 비용 제거)
  ② 교정 재시도가 빈 {} → ValueError(원문에서 아무것도 복구 못한 결측)
  ③ 모델이 '직접' {} 를 1차 출력 → 통과(진짜 출력과 교정 세탁의 구분)
  ④ 정상 경로(1차 유효·교정 복구 성공)는 불변
예외는 기존 계약(재시도 실패 raise ValueError)과 동일 유형 — 호출부 계약 불변.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from novelcopilot.llm.base import LLMProvider


class _Seq(LLMProvider):
    """chat 이 script 를 순서대로 반환하는 fake — 콜 수 계측."""
    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.calls = 0

    def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
        out = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return out

    def embed(self, texts):
        return [[0.0] for _ in texts]


def test_empty_first_response_raises_without_retry():
    # ① 빈 1차 응답 → 즉시 결측 예외 + 교정 재시도 콜 0(헛 비용·세탁 경로 제거)
    p = _Seq(["", "{}"])
    with pytest.raises(ValueError, match="빈 응답"):
        p.chat_json([{"role": "user", "content": "x"}])
    assert p.calls == 1, "빈 응답에 교정 재시도를 태움(세탁 경로 잔존)"


def test_whitespace_first_response_raises():
    p = _Seq(["  \n\t ", "{}"])
    with pytest.raises(ValueError, match="빈 응답"):
        p.chat_json([{"role": "user", "content": "x"}])
    assert p.calls == 1


def test_retry_empty_object_raises():
    # ② 1차=비JSON 잔해 → 교정 재시도가 {} 반환 → 결측 예외(세탁 차단)
    p = _Seq(["죄송하지만 판단할 수 없습니다.", "{}"])
    with pytest.raises(ValueError, match="빈 객체"):
        p.chat_json([{"role": "user", "content": "x"}])
    assert p.calls == 2


def test_direct_empty_object_passes():
    # ③ 모델이 직접 {} 를 1차 출력 — 진짜 출력이므로 통과(호출부 스키마 처리 몫)
    p = _Seq(["{}"])
    assert p.chat_json([{"role": "user", "content": "x"}]) == {}
    assert p.calls == 1


def test_valid_first_response_passes():
    # ④ 정상 1차 응답 불변
    p = _Seq(['{"needs_repair": true, "spans": []}'])
    assert p.chat_json([{"role": "user", "content": "x"}])["needs_repair"] is True
    assert p.calls == 1


def test_fenced_valid_object_recovered_without_retry():
    # ④-a 코드펜스로 감싼 유효 객체는 파서가 펜스를 벗겨 즉시 복구 — 교정 콜 0(sonnet-5 실측 65/66 의 소스 차단).
    p = _Seq(["```json\n{\"a\": 1}\n```", '{"a": 1}'])
    assert p.chat_json([{"role": "user", "content": "x"}]) == {"a": 1}
    assert p.calls == 1


def test_retry_recovers_valid_object():
    # ④-b 진짜 깨진 1차 응답은 교정 재시도가 유효 객체를 복구하는 기존 경로 불변
    p = _Seq(["잔해 {\"a\": ", '{"a": 1}'])
    assert p.chat_json([{"role": "user", "content": "x"}]) == {"a": 1}
    assert p.calls == 2


def test_retry_parse_failure_still_raises():
    # 기존 계약 회귀 방지: 재시도도 파싱 실패 → ValueError
    p = _Seq(["잔해", "여전히 잔해"])
    with pytest.raises(ValueError, match="파싱 실패"):
        p.chat_json([{"role": "user", "content": "x"}])
