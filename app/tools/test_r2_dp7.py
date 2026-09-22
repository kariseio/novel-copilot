# -*- coding: utf-8 -*-
"""R2(DP-7) 검증 — 발단(1화) 전용 rule⑤ 교체 + arc_planner 발단 hook 요지의 _draft 관통. LLM 0콜.

근거: docs/design-dp-repair.md §DP-7.
- 확정 원인: system 고권위 rule⑤(장면을 '사건의 한복판'에서 여는 in-medias-res 개시)가 매 회차 발단 hook
  (비트 레이어·저권위)을 눌러, 회귀물이 죽음/각성 한복판에서 개시(발단 grounding 소실)했음이 실데이터로 확정.
- 수리:
  ① chapter==1 에만 rule⑤를 '누구인지 구체 장면으로 세우고 곧 전환으로 굴려라'(grounding→전환) 결로 렌더
     시점 교체 주입(긍정 교체 — 금지 문구 없음). 저장된 style.rules 데이터 불변 · 비1화 프롬프트 바이트 동일.
  ② arc_planner 발단 hook(arc_planner.py:433~ / :429대) 요지를 _draft out_instr 에 chapter==1 조건부 결
     지시로 관통(짧게·긍정형·T2: 강제 아닌 결).

검증 축:
1) render_style 무회귀 — opening 기본(False)은 8규칙 그대로 직렬화(비1화 바이트 동일 근거).
2) render_style opening — rule⑤(in-medias-res)만 발단 변형으로 치환, 나머지 규칙·저장 데이터(DEFAULT) 불변.
3) 데이터 불변 — DEFAULT_STYLE_RULES[5] 원문 그대로(치환은 렌더 시점만, 소스 불변).
4) _draft 1화(실경로) — system 이 발단 변형 style_block 사용(in-medias-res 시그니처 소거) + out_instr 에
   발단 hook 요지(긍정) 관통.
5) _draft 비1화 바이트 동일 — system == 기본 render_style 래핑, out_instr 에 DP-7 발단 결 부재.
6) 기존 발단 분기 회귀 0 — arc_planner beat_for_episode 의 chapter==1 hook 원문 불변(내가 arc_planner 무수정).

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_r2_dp7.py
"""
from __future__ import annotations
import sys
import json
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

from types import SimpleNamespace

from novelcopilot.domain.world import DEFAULT_STYLE_RULES, StyleSpec, WorldConfig, EntitySpec
from novelcopilot.domain.narrative import Arc, Episode
from novelcopilot.domain.types import ContextBoard, SceneSpec
from novelcopilot.engine.prompts import render_style, _OPENING_RULE
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider

# 프롬프트 문구 앵커(구현과 동기화).
_INMEDIAS_SIG = "사건의 한복판에서 열고"     # rule⑤ in-medias-res 개시 시그니처
_OPENING_MARK = "작품의 첫머리(발단)"        # 발단 변형 rule⑤ 앵커
_DRAFT_HOOK_A = "이 회차는 작품의 첫머리다"   # _draft out_instr 발단 결(②) 앵커
_DRAFT_HOOK_B = "이어질 여지를 두라"          # _draft out_instr 발단 결(②) 앵커
_ARC_CH1_HOOK = "첫 회차(도입부 시작)"       # arc_planner 발단 hook(회귀 락)

# 발단 결(②)·변형 rule⑤(①)는 긍정 전용(pink-elephant) — 재유입 금지 부정명령 토큰.
_BANNED = ("하지 마", "쓰지 마", "말 것", "말라", "지 말고", "삼지 마", "반복", "되풀이", "재연")


class _Bus:
    def emit(self, *a, **k):
        pass


class _Cap(LLMProvider):
    """_draft 프롬프트 캡처 — messages 기록 후 무해한 응답."""
    def __init__(self):
        super().__init__()
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


def _gen():
    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=False, scene_style_anchor=False)
    prov = _Cap()
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    return g, prov


def _draft_msgs(g, prov, chapter: int):
    board = ContextBoard(chapter=chapter)
    scene = SceneSpec(index=0, goal="주인공의 하루", key_events=["전환이 발발한다"])
    g._draft(board, scene, "", last=False, closing=False, chapter_mode=True)
    msgs = prov.captured[-1]
    return msgs[0]["content"], msgs[1]["content"]   # (system, user)


# ---------- 1) render_style 무회귀(opening 기본=False) ----------
def test_render_style_default_unchanged() -> bool:
    out = render_style(StyleSpec())
    ok = (render_style(StyleSpec(), opening=False) == out)      # 기본값 명시=암묵 동일
    ok &= all(f"{i + 1}) {r}" in out for i, r in enumerate(DEFAULT_STYLE_RULES))   # 8규칙 그대로
    ok &= (_INMEDIAS_SIG in out and _OPENING_MARK not in out)   # 비발단: in-medias-res 유지·발단변형 무주입
    print(f"[{'OK' if ok else 'FAIL'}] render_style 기본 무회귀(8규칙 직렬화·발단변형 무주입)")
    return ok


# ---------- 2) render_style opening — rule⑤만 치환, 나머지 불변 ----------
def test_render_style_opening_swaps_only_rule5() -> bool:
    base = render_style(StyleSpec())
    op = render_style(StyleSpec(), opening=True)
    ok = (op != base)
    ok &= (_INMEDIAS_SIG not in op)          # in-medias-res 개시 지시 소거(발단 grounding 확보)
    ok &= (_OPENING_MARK in op and "누구" in op)   # 발단 변형 주입('누구인지 세우고 전환')
    # rule⑤ 외 규칙은 그대로(예: 규칙1 조판·규칙7 호칭·규칙8 완결 — 시그니처 미포함이라 불변)
    for keep in (DEFAULT_STYLE_RULES[0], DEFAULT_STYLE_RULES[6], DEFAULT_STYLE_RULES[7]):
        ok &= (keep in op)
    # 발단 변형 rule⑤ 텍스트 자체는 긍정 전용(pink-elephant) — 타 규칙의 floor 부정형(삼지 마·지 말고)은
    # 별개 항목이라 여기서 검사 대상 아님(C-5 가 floor 부정형을 별도 잠금).
    for tok in _BANNED:
        ok &= (tok not in _OPENING_RULE)
    print(f"[{'OK' if ok else 'FAIL'}] render_style opening: rule⑤만 발단 변형 치환·타규칙 불변·긍정 전용")
    return ok


# ---------- 3) 데이터 불변 — DEFAULT 소스 원문 그대로 ----------
def test_default_rules_data_immutable() -> bool:
    # 렌더 시점 치환일 뿐, 저장/소스 데이터(DEFAULT_STYLE_RULES[5])는 원문 그대로 — 기존 작품 rules 불변 근거.
    r5 = DEFAULT_STYLE_RULES[5]
    ok = ("한복판에서 열고" in r5 and "예상을 비트는" in r5 and "궁금해지게 끊어라" in r5)
    ok &= (len(DEFAULT_STYLE_RULES) == 8)
    print(f"[{'OK' if ok else 'FAIL'}] DEFAULT_STYLE_RULES 데이터 불변(치환은 렌더 시점만)")
    return ok


# ---------- 4) _draft 1화(실경로) — 발단 변형 style + hook 요지 관통 ----------
def test_draft_ch1_opening_wired() -> bool:
    g, prov = _gen()
    sys_msg, user = _draft_msgs(g, prov, chapter=1)
    ok = (_OPENING_MARK in sys_msg and _INMEDIAS_SIG not in sys_msg)   # ① 발단 변형 style_block 사용
    ok &= (g.style.system_persona in sys_msg and "확정 설정 절대 위반 금지" in sys_msg)   # 안전 계약 유지
    ok &= (_DRAFT_HOOK_A in user and _DRAFT_HOOK_B in user)            # ② 발단 hook 요지 out_instr 관통
    ok &= ("출력은 이번 회차의 소설 본문 그것 하나뿐이다" in user)      # 기존 출력 계약 보존
    for tok in _BANNED:
        ok &= (tok not in user)                                        # 발단 결 긍정 전용
    print(f"[{'OK' if ok else 'FAIL'}] _draft 1화: 발단 style(in-medias-res 소거)+hook 요지 관통·긍정 전용")
    return ok


# ---------- 5) _draft 비1화 바이트 동일 ----------
def test_draft_non_ch1_byte_identical() -> bool:
    g, prov = _gen()
    sys_msg, user = _draft_msgs(g, prov, chapter=3)
    expect_sys = f"{g.style.system_persona} 확정 설정 절대 위반 금지.\n{render_style(g.style)}"
    ok = (sys_msg == expect_sys)                     # 비1화 system == 기본 render_style 래핑(바이트 동일)
    ok &= (_INMEDIAS_SIG in sys_msg)                 # rule⑤ in-medias-res 유지(발단 변형 미적용)
    ok &= (_OPENING_MARK not in sys_msg)             # 발단 변형 부재
    ok &= (_DRAFT_HOOK_A not in user and _DRAFT_HOOK_B not in user)   # out_instr 발단 결 부재
    print(f"[{'OK' if ok else 'FAIL'}] _draft 비1화(ch3): system 바이트 동일·발단 결 부재")
    return ok


# ---------- 6) 기존 발단 분기 회귀 0 — arc_planner ch1 hook 불변 ----------
def _world():
    return WorldConfig(title="t", genre="x",
                       entities=[EntitySpec(id="hero", name="도현", profile="회귀자")])


def _arc_ep():
    arc = Arc(arc_id="arc1", order=1, title="A1", goal="g")
    ep = Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", premise="도입",
                 climax="정체를 드러낸다", required_events=["첫 대치"], required_cast=["hero"],
                 target_chapters=3)
    return arc, ep


class _CapArc(LLMProvider):
    def __init__(self):
        super().__init__()
        self.sys = ""

    def chat(self, messages, *a, **k):
        self.sys = messages[0]["content"]
        return json.dumps({"title": "t", "summary": "s", "key_events": ["e"], "entities": ["hero"],
                           "chapter_function": "setup", "hook_type": "question", "time_advance": "없음",
                           "place": "p"}, ensure_ascii=False)

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def test_arc_planner_ch1_hook_regression_zero() -> bool:
    w, (arc, ep) = _world(), _arc_ep()
    cap1 = _CapArc()
    ArcPlanner(cap1).beat_for_episode(w, arc, ep, 1, False, ["직전"], [])
    ok = (_ARC_CH1_HOOK in cap1.sys)              # arc_planner ch1 발단 hook 원문 유지(내가 arc_planner 무수정)
    ok &= ("구체적인 장면" in cap1.sys)            # 발단 grounding 지시 유지
    # 비1화(ch3, 같은 arc/ep 초반)에는 ch1 hook 미주입(arc_planner 자체 분기 회귀 락)
    cap3 = _CapArc()
    ArcPlanner(cap3).beat_for_episode(w, arc, ep, 3, False, ["직전"], [])
    ok &= (_ARC_CH1_HOOK not in cap3.sys)
    print(f"[{'OK' if ok else 'FAIL'}] arc_planner 발단 분기 회귀 0(ch1 hook 유지·비1화 미주입)")
    return ok


def main() -> int:
    results = [
        test_render_style_default_unchanged(),
        test_render_style_opening_swaps_only_rule5(),
        test_default_rules_data_immutable(),
        test_draft_ch1_opening_wired(),
        test_draft_non_ch1_byte_identical(),
        test_arc_planner_ch1_hook_regression_zero(),
    ]
    print("\nR2(DP-7 발단 rule⑤ 교체·hook 관통) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_r2_dp7_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
