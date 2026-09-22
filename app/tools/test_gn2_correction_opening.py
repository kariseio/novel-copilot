# -*- coding: utf-8 -*-
"""GN-2 검증 — 발단(ch1) 결이 교정 패스(_rewrite·_regen_tail·_continuity_polish·_fix_tics)에 관통되는지. LLM 0콜(프롬프트 캡처).

근거(audit_e2e_0.md A-1): 발단 분기는 _draft(DP-7)·_continue(P-1)에는 ch==1 로 관통하나, 캐논 교정 _rewrite·
  말미 재작성 _regen_tail·출고 검수 _continuity_polish·틱 교정 _fix_tics 는 ch 번호 자체를 안 받아 발단 결이
  없다. 1화가 이 패스들을 타면 in-medias-res 억제(rule⑤ 치환)·발단 grounding 이 그 패스에서 소실될 수 있다.

수리(harness.py):
  · _rewrite: 이미 board.chapter 를 가지므로 board.chapter==1 이면 발단 grounding 보존 한 줄(_OPENING_CORR_HINT)을
    system(floor_block 뒤)에 얹는다. 비ch1 이면 "" → 프롬프트 바이트 동일.
  · _continuity_polish·_fix_tics·_regen_tail: ch 파라미터(기본 0)를 추가하고 generate()가 ch_no 를 넘긴다.
    ch==1 이면 발단 보존 한 줄을 각 system 지시에 얹고, 비ch1(기본 0)이면 "" → 프롬프트 바이트 동일.

검증 축:
1) _rewrite ch1 — system 에 발단 보존 한 줄(_OPENING_CORR_HINT) 관통 + floor_block(바닥 제약) 보존.
2) _rewrite 비1화(ch3) — system 바이트 동일(발단 한 줄 부재) + floor_block 보존.
3) _continuity_polish ch1/비ch1 — ch1 은 발단 한 줄 관통, ch3(및 ch 미지정 기본 0)은 바이트 동일.
4) _fix_tics ch1/비ch1 — 동형.
5) _regen_tail ch1/비ch1 — 동형.
6) 발단 보존 문안 긍정 전용(pink-elephant) — _OPENING_CORR_HINT 에 부정명령 토큰 부재.
7) 기본값 무관통(하위호환) — ch 미지정(기본 0) 호출은 비1화 경로와 완전 동일(바이트 동일 회귀 락).

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_gn2_correction_opening.py
"""
from __future__ import annotations
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/

from types import SimpleNamespace

from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.types import ContextBoard
from novelcopilot.engine.harness import ChapterGenerator, _OPENING_CORR_HINT

# 프롬프트 문구 앵커(구현과 동기화).
_OPEN_SIG = "작품의 첫머리(발단)"        # 발단 보존 한 줄(_OPENING_CORR_HINT) 앵커
_OPEN_KEEP = "그대로 유지한 채 교정"      # 발단 grounding 보존(사건 한복판 개시로 바꾸지 말라는 결) 앵커

# 발단 보존 문안은 긍정 전용(pink-elephant) — 재유입 금지 부정명령 토큰.
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
        return "정정된 본문 산문."

    def chat_json(self, messages, *a, **k):
        self.captured.append(messages)
        return {"fixes": []}

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _gen():
    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=True, scene_style_anchor=False,
                               correction_max_drift=0.6)
    prov = _Cap()
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    return g, prov


def _sys_of_last(prov) -> str:
    return prov.captured[-1][0]["content"]


# ---------- 1)·2) _rewrite ch1 관통 / 비1화 바이트 동일 ----------
def test_rewrite_ch1_vs_non_ch1() -> bool:
    g, prov = _gen()
    # ch1
    g._rewrite("원본 본문 산문입니다.", [], ContextBoard(chapter=1))
    sys1 = _sys_of_last(prov)
    ok = (_OPEN_SIG in sys1 and _OPEN_KEEP in sys1)          # 발단 보존 한 줄 관통
    ok &= (g.floor_block in sys1)                            # floor-only 계약(B-10) 보존
    ok &= (sys1.endswith(_OPENING_CORR_HINT))               # 한 줄이 floor_block 뒤에 정확히 붙음
    # 비1화(ch3)
    g._rewrite("원본 본문 산문입니다.", [], ContextBoard(chapter=3))
    sys3 = _sys_of_last(prov)
    expect3 = sys1[:-len(_OPENING_CORR_HINT)]               # ch1 system 에서 발단 한 줄만 제거 = 비1화 system
    ok &= (sys3 == expect3)                                 # 비1화 바이트 동일(발단 한 줄만 차이)
    ok &= (_OPEN_SIG not in sys3)                           # 발단 한 줄 부재
    ok &= (g.floor_block in sys3)                           # floor_block 보존
    print(f"[{'OK' if ok else 'FAIL'}] _rewrite: ch1 발단 보존 관통·floor 보존 / ch3 바이트 동일")
    return ok


# ---------- 3) _continuity_polish ch1 / 비1화 / 기본 0 ----------
def test_continuity_polish_ch1_vs_non_ch1() -> bool:
    g, prov = _gen()
    g._continuity_polish("본문 텍스트.", names=["도현"], ch=1)
    sys1 = _sys_of_last(prov)
    g._continuity_polish("본문 텍스트.", names=["도현"], ch=3)
    sys3 = _sys_of_last(prov)
    g._continuity_polish("본문 텍스트.", names=["도현"])   # ch 미지정(기본 0) — 하위호환 경로
    sys_default = _sys_of_last(prov)
    ok = (_OPEN_SIG in sys1 and _OPEN_KEEP in sys1)         # ch1 발단 보존 관통
    ok &= (sys1 == sys3 + _OPENING_CORR_HINT)              # ch1 == ch3 + 발단 한 줄(정확 부착)
    ok &= (_OPEN_SIG not in sys3)                           # ch3 발단 한 줄 부재
    ok &= (sys_default == sys3)                             # 기본 0 == 비1화 바이트 동일(하위호환 회귀 락)
    print(f"[{'OK' if ok else 'FAIL'}] _continuity_polish: ch1 관통 / ch3·기본0 바이트 동일")
    return ok


# ---------- 4) _fix_tics ch1 / 비1화 / 기본 0 ----------
def test_fix_tics_ch1_vs_non_ch1() -> bool:
    g, prov = _gen()
    offenders = [("짧게 말했다", 5), ("그 순간", 4)]
    g._fix_tics("본문 텍스트 짧게 말했다 그 순간.", offenders, ch=1)
    sys1 = _sys_of_last(prov)
    g._fix_tics("본문 텍스트 짧게 말했다 그 순간.", offenders, ch=3)
    sys3 = _sys_of_last(prov)
    g._fix_tics("본문 텍스트 짧게 말했다 그 순간.", offenders)   # 기본 0
    sys_default = _sys_of_last(prov)
    ok = (_OPEN_SIG in sys1 and _OPEN_KEEP in sys1)
    ok &= (sys1 == sys3 + _OPENING_CORR_HINT)
    ok &= (_OPEN_SIG not in sys3)
    ok &= (sys_default == sys3)
    print(f"[{'OK' if ok else 'FAIL'}] _fix_tics: ch1 관통 / ch3·기본0 바이트 동일")
    return ok


# ---------- 5) _regen_tail ch1 / 비1화 / 기본 0 ----------
def test_regen_tail_ch1_vs_non_ch1() -> bool:
    g, prov = _gen()
    body = "\n".join(f"문단 {i} 본문 산문입니다." for i in range(20))
    tails = ["직전 회차 끝 A", "직전 회차 끝 B"]
    g._regen_tail(body, tails, ch=1)
    sys1 = _sys_of_last(prov)
    g._regen_tail(body, tails, ch=3)
    sys3 = _sys_of_last(prov)
    g._regen_tail(body, tails)   # 기본 0
    sys_default = _sys_of_last(prov)
    ok = (_OPEN_SIG in sys1 and _OPEN_KEEP in sys1)
    ok &= (sys1 == sys3 + _OPENING_CORR_HINT)
    ok &= (_OPEN_SIG not in sys3)
    ok &= (sys_default == sys3)
    print(f"[{'OK' if ok else 'FAIL'}] _regen_tail: ch1 관통 / ch3·기본0 바이트 동일")
    return ok


# ---------- 6) 발단 보존 문안 긍정 전용(pink-elephant) ----------
def test_opening_corr_hint_positive_only() -> bool:
    ok = True
    for tok in _BANNED:
        ok &= (tok not in _OPENING_CORR_HINT)
    ok &= (_OPEN_SIG in _OPENING_CORR_HINT and _OPEN_KEEP in _OPENING_CORR_HINT)   # 앵커 무결
    print(f"[{'OK' if ok else 'FAIL'}] _OPENING_CORR_HINT 긍정 전용(부정명령 토큰 부재)")
    return ok


def main() -> int:
    results = [
        test_rewrite_ch1_vs_non_ch1(),
        test_continuity_polish_ch1_vs_non_ch1(),
        test_fix_tics_ch1_vs_non_ch1(),
        test_regen_tail_ch1_vs_non_ch1(),
        test_opening_corr_hint_positive_only(),
    ]
    print("\nGN-2(발단 결 교정 패스 관통) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_gn2_correction_opening_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
