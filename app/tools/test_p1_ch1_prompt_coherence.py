# -*- coding: utf-8 -*-
"""P-1 검증 — ch1 프롬프트 정합 2건(Fable 감사 발견). LLM 0콜(프롬프트 캡처).

수리(harness.py):
  ① ch1 발단 변형의 '이어쓰기 미적용' 해소 — _continue 가 무조건 self.style_block 을 쓰던 경로(구 :248)를
     ch==1 분기로 style_block_opening(발단 변형 rule⑤) 사용하게 하고, _continue 의 out_instr 에도 _draft 와
     동형·짧은 ch1 발단 관통(U14)을 추가. 비1화 경로는 바이트 동일.
  ② ch1 craft 긴장 완화 — _CRAFT_PROGRESS(U16)의 '직전 회차가 멈춘 지점을 출발선으로…전진'이 1화 발단
     grounding 과 정면 긴장. ch==1 일 때 craft_block 을 발단 호환 문안(_CRAFT_PROGRESS_OPENING: '인물과 일상을
     세우는 장면 자체가 전진이다 — 도입 안에서도 상황이 한 단계씩 움직이게')으로 교체. 비1화 바이트 동일.
     (두 안 중 '교체' 채택 — craft 의 directional '전진' 가치를 발단에서도 보존하는 편이 '생략'보다 결손이 적다.)

검증 축:
1) _draft ch1 — craft_block 이 발단 호환(_CRAFT_PROGRESS_OPENING)으로 교체(기본 _CRAFT_PROGRESS 프레이밍 부재).
2) _draft 비1화 — 기본 _CRAFT_PROGRESS 유지·발단 craft 부재(②의 비1화 불변).
3) _continue ch1 — system 이 발단 변형 style_block_opening 사용(in-medias-res 시그니처 소거) + out_instr 에
   발단 관통 관통 + craft 발단 호환 교체(①+②).
4) _continue 비1화(ch3) — system 바이트 동일(기본 render_style 래핑·in-medias-res 유지) + out_instr 발단 관통 부재
   + 기본 _CRAFT_PROGRESS 유지(①+② 비1화 바이트 동일 근거).
5) 긍정 전용(pink-elephant) — 새 발단 문안(_CRAFT_PROGRESS_OPENING·_continue ch1 hook)에 부정명령 토큰 부재.
6) craft_progress OFF — ch1/비ch1 모두 craft 블록 "" (opening 변형도 토글 종속·무동작).

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_p1_ch1_prompt_coherence.py
"""
from __future__ import annotations
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/

from types import SimpleNamespace

from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.types import ContextBoard, SceneSpec
from novelcopilot.engine.prompts import render_style
from novelcopilot.engine.harness import (ChapterGenerator, _CRAFT_PROGRESS,
                                          _CRAFT_PROGRESS_OPENING)

# 프롬프트 문구 앵커(구현과 동기화).
_INMEDIAS_SIG = "사건의 한복판에서 열고"       # rule⑤ in-medias-res 개시 시그니처(발단 변형 시 소거)
_OPENING_MARK = "작품의 첫머리(발단)"          # render_style opening 변형 rule⑤ 앵커
_CRAFT_BASE_SIG = "직전 회차가 멈춘 지점을 출발선"   # 기본 _CRAFT_PROGRESS 프레이밍(1화에 부적합)
_CRAFT_OPEN_SIG = "장면 자체가 전진이다"        # 발단 호환 craft 앵커
_CONT_HOOK_A = "이 회차는 작품의 첫머리다"      # _continue ch1 발단 관통 앵커(①)
_CONT_HOOK_B = "이어질 여지를 두라"             # _continue ch1 발단 관통 앵커(①)
_CONT_OUT_SIG = "마지막 문장 바로 다음"        # _continue 기본 out_instr(불변 계약) 앵커

# 발단 문안(①·②)은 긍정 전용(pink-elephant) — 재유입 금지 부정명령 토큰.
_BANNED = ("하지 마", "쓰지 마", "말 것", "말라", "지 말고", "삼지 마", "반복", "되풀이", "재연")


class _Bus:
    def emit(self, *a, **k):
        pass


class _Cap:
    """프롬프트 캡처 provider — messages 기록 후 무해한 응답(LLM 0콜)."""
    def __init__(self):
        self.captured = []
        self.last_truncated = False

    def chat(self, messages, *a, **k):
        self.captured.append(messages)
        return "본문."

    def chat_json(self, messages, *a, **k):
        self.captured.append(messages)
        return {}

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _gen(craft: bool = True):
    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=craft, scene_style_anchor=False)
    prov = _Cap()
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    return g, prov


def _draft_msgs(g, prov, chapter: int):
    board = ContextBoard(chapter=chapter)
    scene = SceneSpec(index=0, goal="주인공의 하루", key_events=["전환이 발발한다"])
    g._draft(board, scene, "", last=False, closing=False, chapter_mode=True)
    msgs = prov.captured[-1]
    return msgs[0]["content"], msgs[1]["content"]   # (system, user)


def _continue_msgs(g, prov, chapter: int):
    board = ContextBoard(chapter=chapter)
    g._continue(board, "지금까지 쓴 본문의 마지막 문장.", closing=False,
                recent_tails=None, key_events=["전환이 발발한다"])
    msgs = prov.captured[-1]
    return msgs[0]["content"], msgs[1]["content"]   # (system, user)


# ---------- 1) _draft ch1 — craft 발단 호환 교체(②) ----------
def test_draft_ch1_craft_opening() -> bool:
    g, prov = _gen()
    _sys, user = _draft_msgs(g, prov, chapter=1)
    ok = (_CRAFT_OPEN_SIG in user)               # 발단 호환 craft 주입
    ok &= (_CRAFT_BASE_SIG not in user)          # 기본 craft 프레이밍('직전 회차 출발선') 부재
    ok &= (_OPENING_MARK in _sys and _INMEDIAS_SIG not in _sys)   # DP-7① style 발단 변형(회귀 락)
    print(f"[{'OK' if ok else 'FAIL'}] _draft ch1: craft 발단 호환 교체(직전-출발선 프레이밍 소거)")
    return ok


# ---------- 2) _draft 비1화 — 기본 craft 유지 ----------
def test_draft_non_ch1_craft_base() -> bool:
    g, prov = _gen()
    _sys, user = _draft_msgs(g, prov, chapter=3)
    ok = (_CRAFT_BASE_SIG in user)               # 기본 _CRAFT_PROGRESS 유지
    ok &= (_CRAFT_OPEN_SIG not in user)          # 발단 craft 부재(②의 비1화 불변)
    ok &= (_INMEDIAS_SIG in _sys and _OPENING_MARK not in _sys)   # style 발단 변형 부재
    print(f"[{'OK' if ok else 'FAIL'}] _draft 비1화(ch3): 기본 craft 유지·발단 craft 부재")
    return ok


# ---------- 3) _continue ch1 — style_opening + out_instr 발단 관통 + craft 교체(①+②) ----------
def test_continue_ch1_wired() -> bool:
    g, prov = _gen()
    _sys, user = _continue_msgs(g, prov, chapter=1)
    # ① 발단 변형 style_block_opening 사용(무조건 self.style_block 쓰던 구 경로 수리)
    ok = (_OPENING_MARK in _sys and _INMEDIAS_SIG not in _sys)
    ok &= (g.style.system_persona in _sys and "확정 설정 절대 위반 금지" in _sys)   # 안전 계약 유지
    # ① out_instr 발단 관통(짧게·긍정) + 기존 이어쓰기 출력 계약 보존
    ok &= (_CONT_HOOK_A in user and _CONT_HOOK_B in user)
    ok &= (_CONT_OUT_SIG in user)                # '마지막 문장 바로 다음' 접합 계약 보존
    # ② craft 발단 호환 교체
    ok &= (_CRAFT_OPEN_SIG in user and _CRAFT_BASE_SIG not in user)
    for tok in _BANNED:
        ok &= (tok not in user)                  # 발단 결 긍정 전용
    print(f"[{'OK' if ok else 'FAIL'}] _continue ch1: style_opening+out_instr 관통+craft 교체·긍정 전용")
    return ok


# ---------- 4) _continue 비1화(ch3) — system 바이트 동일 + 발단 결 부재 ----------
def test_continue_non_ch1_byte_identical() -> bool:
    g, prov = _gen()
    _sys, user = _continue_msgs(g, prov, chapter=3)
    expect_sys = f"{g.style.system_persona} 확정 설정 절대 위반 금지.\n{render_style(g.style)}"
    ok = (_sys == expect_sys)                     # 비1화 system == 기본 render_style 래핑(바이트 동일)
    ok &= (_INMEDIAS_SIG in _sys and _OPENING_MARK not in _sys)   # rule⑤ in-medias-res 유지
    ok &= (_CONT_HOOK_A not in user and _CONT_HOOK_B not in user)  # out_instr 발단 관통 부재
    ok &= (_CONT_OUT_SIG in user)                 # 기존 이어쓰기 계약 보존
    ok &= (_CRAFT_BASE_SIG in user and _CRAFT_OPEN_SIG not in user)   # 기본 craft 유지(② 비1화 불변)
    print(f"[{'OK' if ok else 'FAIL'}] _continue 비1화(ch3): system 바이트 동일·발단 결/craft 부재")
    return ok


# ---------- 5) 긍정 전용(pink-elephant) — 새 발단 문안 부정명령 토큰 부재 ----------
def test_opening_texts_positive_only() -> bool:
    ok = True
    for tok in _BANNED:
        ok &= (tok not in _CRAFT_PROGRESS_OPENING)
    # _continue ch1 hook 은 실경로 user(테스트 3)에서 이미 _BANNED 통과 검사됨 — 여기선 craft 상수만.
    ok &= (_CRAFT_OPEN_SIG in _CRAFT_PROGRESS_OPENING)   # 앵커 무결
    print(f"[{'OK' if ok else 'FAIL'}] 발단 craft 상수 긍정 전용(부정명령 토큰 부재)")
    return ok


# ---------- 6) craft_progress OFF — ch1/비ch1 모두 craft "" ----------
def test_craft_off_no_block_either_path() -> bool:
    g, prov = _gen(craft=False)
    ok = (g.craft_block == "" and g.craft_block_opening == "")   # 토글 OFF면 opening 변형도 무동작
    _sys1, user1 = _draft_msgs(g, prov, chapter=1)
    ok &= (_CRAFT_OPEN_SIG not in user1 and _CRAFT_BASE_SIG not in user1)   # ch1 draft craft 부재
    _sys3, user3 = _draft_msgs(g, prov, chapter=3)
    ok &= (_CRAFT_OPEN_SIG not in user3 and _CRAFT_BASE_SIG not in user3)   # 비1화 draft craft 부재
    _sysc, userc = _continue_msgs(g, prov, chapter=1)
    ok &= (_CRAFT_OPEN_SIG not in userc and _CRAFT_BASE_SIG not in userc)   # ch1 continue craft 부재
    print(f"[{'OK' if ok else 'FAIL'}] craft_progress OFF: ch1/비ch1 draft·continue 모두 craft 부재")
    return ok


def main() -> int:
    results = [
        test_draft_ch1_craft_opening(),
        test_draft_non_ch1_craft_base(),
        test_continue_ch1_wired(),
        test_continue_non_ch1_byte_identical(),
        test_opening_texts_positive_only(),
        test_craft_off_no_block_either_path(),
    ]
    print("\nP-1(ch1 프롬프트 정합 2건) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_p1_ch1_prompt_coherence_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
