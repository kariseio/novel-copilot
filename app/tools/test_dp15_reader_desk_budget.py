# -*- coding: utf-8 -*-
"""DP-15 검증 — reader_desk 컨텍스트 기아 수리(시뮬 독자 '지금까지 줄거리' 절단).

배경: reader_desk.py 가 story_so_far[:1500] 로 하드 절단했다. 호출자는 story_so_far_chars(12,000)
계층 요약을 넘기는데 87%가 잘렸고, 잘리는 쪽이 문자열 '앞'(오래된 롤업)이 아니라 '뒤'(최신 회차 상세)라
방금 벌어진 사건을 시뮬 독자가 못 봐 실재하지 않는 모순을 지적(DP-4b 7화 retention 오염).

수리 축(전부 assert — 반환 bool 아님, pytest 가 실제로 실패시키게):
1) 예산 확대: 기본 예산에서 12,000자 계층 요약이 절단 없이 전량 전달.
2) 절단 경계: 예산 == 길이면 무손실, 예산 < 길이면 정확히 예산 이하로 절단.
3) 최신 우선: 절단 시 '앞(오래된)'이 잘리고 '뒤(최신)'가 보존 + 생략 마커.
4) 예산 0/음수·마커보다 작은 예산 경계 방어.
5) 회귀: 짧은 요약·빈 요약·스키마/None 경계 무회귀(기존 reader_prediction 계약 유지).

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_dp15_reader_desk_budget.py
"""
from __future__ import annotations
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트

from novelcopilot.engine.reader_desk import reader_prediction, _tail_budget
from novelcopilot.config import get_settings
from novelcopilot.llm.base import LLMProvider


class CapturingFake(LLMProvider):
    """chat_json 에 넘어온 messages(=user content)를 캡처해 실제 주입된 줄거리 길이/내용을 검사."""
    def __init__(self, response=None):
        super().__init__()
        self.response = response or {"drop": True, "why": "y", "retention_est": 30}
        self.last_messages = None

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        self.last_messages = messages
        return self.response

    def _sofar_block(self) -> str:
        """user 메시지에서 [지금까지 줄거리]와 [이번 회차] 사이 블록 추출."""
        content = self.last_messages[-1]["content"]
        head = "[지금까지 줄거리]\n"
        i = content.index(head) + len(head)
        j = content.index("\n\n[이번 회차]", i)
        return content[i:j]


# ---------- _tail_budget 유닛(순수 함수 — 절단 방향·경계·마커) ----------
def test_tail_budget_unit():
    # 예산 >= 길이 → 무손실(원문 그대로, 마커 없음)
    s = "가나다라마"
    assert _tail_budget(s, 100) == s
    assert _tail_budget(s, len(s)) == s          # 정확 경계: ==길이면 무손실
    # 빈/None → 빈 문자열(방어)
    assert _tail_budget("", 100) == ""
    assert _tail_budget(None, 100) == ""
    # 음수 예산 → 원문 유지(무의미 예산에 데이터 파괴 안 함)
    assert _tail_budget(s, -1) == s
    # 예산 0 → 빈 문자열
    assert _tail_budget(s, 0) == ""

    # 절단 시: 오래된 앞쪽이 잘리고 최신 꼬리가 보존 + 생략 마커
    mark = "…(앞부분 줄거리 생략)…\n"
    long = "OLD_" + ("x" * 1000) + "_RECENT_TAIL"
    out = _tail_budget(long, 40)
    assert len(out) <= 40                                   # 예산 이하 보장
    assert out.startswith(mark)                             # 생략 마커로 시작
    assert out.endswith("_RECENT_TAIL")                    # 최신 꼬리 보존
    assert "OLD_" not in out                                # 오래된 앞쪽은 버려짐

    # 예산이 마커보다 작으면 마커 생략하고 순수 꼬리만(예산 정확 — 마커 때문에 초과 금지)
    tiny = _tail_budget(long, 5)
    assert len(tiny) == 5 and tiny == long[-5:]
    assert "…" not in tiny


# ---------- 1+3) 기본 예산에서 12,000자 계층 요약 전량 전달 ----------
def test_full_hierarchical_summary_passes_at_default_budget():
    # 12,000자 계층 요약 모사: 오래된 롤업(앞) + 최신 회차 상세(뒤)
    old_head = "[아크1·에피1] 아주 오래된 롤업 요약."
    recent_tail = "가장 최근 회차 상세: 주인공이 각성해 보스를 처치했다."
    body = "중간회차상세 " * 900                              # 대략 12k자 채우기
    story = old_head + "\n" + body + "\n" + recent_tail
    story = story[:12000]                                    # config story_so_far_chars 수준
    # 꼬리가 잘리지 않게 최신 문구를 명시적으로 끝에 재부착
    story = story[:12000 - len(recent_tail) - 1] + "\n" + recent_tail
    assert len(story) <= 12000

    fake = CapturingFake()
    # 기본 예산(config.reader_desk_sofar_chars) 사용
    budget = get_settings().reader_desk_sofar_chars
    p = reader_prediction(fake, "이번 회차 본문 텍스트", story, "헌터물", sofar_budget=budget)
    assert p is not None
    injected = fake._sofar_block()
    # 기존 1,500 하드컷이었다면 최신 꼬리가 사라졌을 것 — 이제 전량 전달(마커 없음·최신 포함)
    assert "…(앞부분 줄거리 생략)…" not in injected           # 예산 내라 절단 안 함
    assert recent_tail in injected                            # 최신 사건이 시뮬 독자에게 도달
    assert injected == story                                  # 무손실 전량 전달


# ---------- 2+3) 절단 경계 + 최신 우선(회차생성 계약: story_so_far 는 시간순, 최신=꼬리) ----------
def test_truncation_boundary_and_recent_priority():
    old = "OLDEST_오래된_사건"
    recent = "NEWEST_방금_벌어진_사건"
    story = old + ("중간 " * 5000) + recent                   # 예산 초과
    fake = CapturingFake()

    # 예산을 story 길이보다 작게 → 절단 발생
    small = 200
    reader_prediction(fake, "본문", story, "헌터", sofar_budget=small)
    injected = fake._sofar_block()
    assert len(injected) <= small                             # 예산 경계 준수
    assert injected.endswith(recent)                          # 최신 꼬리 보존
    assert "OLDEST" not in injected                           # 오래된 앞쪽 버려짐
    assert injected.startswith("…(앞부분 줄거리 생략)…")        # 생략 표시

    # 예산 == 길이 경계 → 무손실 전량
    fake2 = CapturingFake()
    reader_prediction(fake2, "본문", story, "헌터", sofar_budget=len(story))
    assert fake2._sofar_block() == story


# ---------- 5) 회귀: 짧은/빈 요약·기존 스키마 계약 무회귀 ----------
def test_regression_no_schema_break():
    fake = CapturingFake()
    # 짧은 요약(예산 훨씬 이내) → 무손실, 마커 없음
    reader_prediction(fake, "본문", "짧은 줄거리", "헌터", sofar_budget=12000)
    assert fake._sofar_block() == "짧은 줄거리"

    # 빈 요약도 정상 동작(호출·스키마 유지)
    fake2 = CapturingFake({"drop": True, "why": "무난", "retention_est": 40})
    p = reader_prediction(fake2, "본문", "", "헌터", sofar_budget=12000)
    assert p is not None and p["drop"] is True and fake2._sofar_block() == ""

    # 기본 인자(sofar_budget 미지정) — 하위호환: 인자 없이 호출해도 동작(기본 12000)
    fake3 = CapturingFake()
    p3 = reader_prediction(fake3, "본문", "줄거리", "헌터")
    assert p3 is not None

    # 빈 본문 → None(기존 계약 유지)
    assert reader_prediction(CapturingFake(), "", "줄거리", "헌터") is None

    # retention_est 정규화 계약 무회귀(범위·비수치)
    assert reader_prediction(CapturingFake({"why": "a", "retention_est": 250}),
                             "b", "", "x")["retention_est"] == 100
    assert reader_prediction(CapturingFake({"kill_trigger": "", "hate_comment": "", "why": ""}),
                             "b", "", "x") is None


# ---------- 설정 배선: 기본값이 계층 요약 예산 이상 ----------
def test_config_budget_covers_hierarchical_summary():
    s = get_settings()
    # DP-15: reader_desk 예산 >= story_so_far detail 조립 예산. detail은 무손실 수용.
    # (주의: hier 조립기는 detail 예산 뒤에 rollups를 무검사로 앞에 덧붙이므로 장편에서
    #  story_so_far가 이 예산을 초과할 수 있음 — 그 경우에도 최신 detail 우선으로 우아하게 절단)
    assert s.reader_desk_sofar_chars >= s.story_so_far_chars
    # 기존 1,500 하드컷보다 대폭 확대(기아 해소)
    assert s.reader_desk_sofar_chars >= 12000


if __name__ == "__main__":
    tests = [test_tail_budget_unit,
             test_full_hierarchical_summary_passes_at_default_budget,
             test_truncation_boundary_and_recent_priority,
             test_regression_no_schema_break,
             test_config_budget_covers_hierarchical_summary]
    failed = []
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
        except AssertionError as e:
            failed.append(t.__name__)
            print(f"[FAIL] {t.__name__}: {e}")
    print("\nDP-15 reader_desk 예산 검증:", "ALL GREEN" if not failed else f"FAIL {failed}")
    sys.exit(0 if not failed else 1)
