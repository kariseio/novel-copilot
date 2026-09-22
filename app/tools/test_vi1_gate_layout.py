# -*- coding: utf-8 -*-
"""VI-1 회귀 — EB-3(설계 사건 대조 문항)·TG-1(조판 축). LLM 0콜.

계약: ① planned_event 비면 게이트 사용자 프롬프트 바이트 동일(하위호환) ② 있으면 대조 문항이
사용자 메시지에만 붙고 시스템 프롬프트 불변 ③ [N화차] 분해 없는 스파인이면 주입 0 ④ layout 축은
고정 규격(문단 3줄)·advisory 값만."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.engine.chapter_gate import slot_event, planned_event_for, llm_gate, GATE_SYSTEM
from novelcopilot.engine.verification import _summ_layout


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeJudge:
    def __init__(self):
        self.messages = None

    def chat_json(self, messages, **kw):
        self.messages = messages
        return {"verdict": "PASS", "drop_trigger": "없음", "fix_note": "", "retention_est": 70}


def test_slot_event_parses_and_strips():
    evs = ["[1화차] 접수와 규칙의 꼬리", "[2화차] 매뉴얼 미성립", "분해 안 된 사건"]
    assert slot_event(evs, 1) == "접수와 규칙의 꼬리"
    assert slot_event(evs, 2) == "매뉴얼 미성립"
    assert slot_event(evs, 3) == ""          # 몫 없음
    assert slot_event(["옛 형식 사건"], 1) == ""   # 미분해 스파인 → 주입 0
    assert slot_event(None, 1) == ""


def test_planned_event_for_slot_counting():
    ep = _NS(episode_id="ep_a", required_events=["[1화차] 첫 사건", "[2화차] 둘째 사건"])
    world = _NS(spine=_NS(arcs=[_NS(episodes=[ep])]))
    chs = [_NS(chapter=10, episode_id="ep_a"), _NS(chapter=11, episode_id="ep_a")]
    st = _NS(world=world, chapters=chs)
    assert planned_event_for(st, 10) == "첫 사건"     # 에피 1화차
    assert planned_event_for(st, 11) == "둘째 사건"   # 에피 2화차
    assert planned_event_for(st, 99) == ""            # 미배정 회차
    st2 = _NS(world=world, chapters=[_NS(chapter=5, episode_id="")])
    assert planned_event_for(st2, 5) == ""            # episode_id 없음 → 주입 0


def test_llm_gate_byte_identical_when_no_planned_event():
    j1, j2 = _FakeJudge(), _FakeJudge()
    llm_gate(j1, "본문.", {}, story_so_far="줄거리", genre="현판")
    llm_gate(j2, "본문.", {}, story_so_far="줄거리", genre="현판", planned_event="")
    assert j1.messages[1]["content"] == j2.messages[1]["content"]   # 바이트 동일
    assert j1.messages[0]["content"] == GATE_SYSTEM


def test_llm_gate_appends_question_user_only():
    j = _FakeJudge()
    llm_gate(j, "본문.", {}, planned_event="계단 규칙의 꼬리를 잡는다")
    user = j.messages[1]["content"]
    assert "설계 사건 대조" in user and "계단 규칙의 꼬리를 잡는다" in user
    assert "설계 사건 미소진" in user                     # fix_note 접두 규약 안내
    assert j.messages[0]["content"] == GATE_SYSTEM        # 시스템 프롬프트 불변


def test_summ_layout_counts_over_paragraphs():
    long_para = "가" * 90                                  # 22자/줄 → 5줄
    text = "짧은 문단.\n\n" + long_para + "\n\n또 짧은 문단."
    ax = _summ_layout(text)
    assert ax["n_paras"] == 3 and ax["over"] == 1
    assert ax["worst"][0]["lines"] == 5 and ax["worst"][0]["head"].startswith("가")
    empty = _summ_layout("")
    assert empty["n_paras"] == 0 and empty["over"] == 0 and empty["worst"] == []



def test_opening_move_line_rotation():
    from novelcopilot.engine.harness import opening_move_line
    moves = ["대사|대사 한 줄로 연다.", "소리|소리 하나로 연다.", "사물|눈앞의 사물 하나로 연다."]
    assert opening_move_line([], "아무 본문", 5) == ""            # 풀 비면 바이트 동일
    assert opening_move_line(moves, "", 1) == ""                  # ch1 발단 결 유지
    out = opening_move_line(moves, '"대사로 시작하는 직전 화"', 5)
    assert "[오프닝]" in out and "대사 한 줄로" not in out         # 직전=대사 → 대사 태그 제외
    out2 = opening_move_line(moves, "지문으로 시작하는 직전 화였다.", 5)
    assert "눈앞의 사물" not in out2                               # 직전=비대사 → 사물 태그 제외
    a = opening_move_line(moves, "지문.", 5)
    assert a == opening_move_line(moves, "지문.", 5)               # 결정론(같은 입력 같은 낙점)
if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("전체 통과")
