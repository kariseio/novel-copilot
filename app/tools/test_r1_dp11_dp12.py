# -*- coding: utf-8 -*-
"""R1(DP-11+12) 검증 — 상태명사 유도 gloss 교체 · 이어쓰기 트리거 '비트 미소진'화. LLM 0콜.

근거: docs/design-dp-repair.md §DP-11·§DP-12.
- DP-11: required_events gloss '(통제 태그)' → 상태 명사(자각·각인)를 유도하므로 '구체적으로 일어나는
  사건 — 인물이 무엇을 하는가'로 긍정 재정의(build_spine·_gen_episodes 두 경로). protagonist_move gloss 를
  '지면에서 실행되는 수'로 강화. dp1 능동개시율의 내면 결단어(결정·결심·계획·설계)를 중립 버킷으로 분리.
- DP-12: harness 이어쓰기 트리거를 'norm 하한 강제'에서 '비트 미소진'(전달된 key_events 대비 미실현 재료가
  있을 때만 _continue)으로. 판정은 기존 커버리지 자산(drift.uncovered — 어간 과반·보수적) 재사용, 소진 시
  짧은 회차 수용 + under_norm 이벤트 유지. key_events 부재 시 보수적 하위호환(기존 norm 기준).

검증 축:
1) gloss 스냅샷(소스) — '(통제 태그)' 소거 · 긍정 재정의 2경로 · '지면에서 실행되는 수' 주입.
2) beat_for_episode 프롬프트(실경로) — 강화된 move gloss 가 sys 에 실제 주입 · 기존 자기기술 앵커 회귀 없음.
3) DP-12 트리거 분기 — 재료 남음→이어쓰기 계속 / 소진→중단(짧은 회차 수용) / key_events 부재→보수적 폴백.
4) DP-12 소스 와이어링 — harness while 조건이 uncovered 미소진 gate + not _beat_events 폴백을 쓴다.
5) DP-11 내면 결단어 중립화(하위호환) — 결심/계획만 있는 회차는 개시로 안 셈(inner 카운트) · 행위 회차 불변.

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_r1_dp11_dp12.py
"""
from __future__ import annotations
import sys
import json
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import Arc, Episode
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.engine.drift import uncovered
from novelcopilot.llm.base import LLMProvider

_ARC_PLANNER_SRC = (_HERE.parent / "novelcopilot" / "worldgen" / "arc_planner.py").read_text(encoding="utf-8")
_HARNESS_SRC = (_HERE.parent / "novelcopilot" / "engine" / "harness.py").read_text(encoding="utf-8")

# 긍정 재정의 gloss(프롬프트 문구와 동기화) — 상태 명사 차단.
_POS_EVENT_GLOSS = "구체적으로 일어나는 사건 — 인물이 무엇을 하는가"
_MOVE_STRENGTH = "지면에서 실제로 실행되는 수"
_SELFDESC_ANCHOR = "protagonist_move(이 회차에서 주인공이 스스로 연 수"


class _Cap(LLMProvider):
    """sys/usr 캡처 + protagonist_move 포함 완본 반환(실경로 — 폴백 아님)."""
    def __init__(self):
        super().__init__()
        self.sys = ""
        self.usr = ""

    def chat(self, messages, *a, **k):
        self.sys = messages[0]["content"]
        self.usr = messages[-1]["content"]
        return json.dumps({
            "title": "t", "summary": "s", "key_events": ["도현이 먼저 나섰다"],
            "entities": ["hero"], "chapter_function": "escalation", "hook_type": "decision",
            "time_advance": "없음", "place": "길드",
            "protagonist_move": "도현이 먼저 나섰다(선수)"}, ensure_ascii=False)

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    return WorldConfig(title="t", genre="x",
                       entities=[EntitySpec(id="hero", name="도현", profile="복수를 원하는 자. 욕망=판을 뒤집는다")])


def _arc_ep():
    arc = Arc(arc_id="arc1", order=1, title="A1", goal="g")
    ep = Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", premise="도입",
                 climax="도현이 정체를 드러낸다", required_events=["첫 대치"],
                 required_cast=["hero"], target_chapters=3)
    return arc, ep


# ---------- 1) gloss 스냅샷(소스): 통제 태그 소거 · 긍정 재정의 2경로 · move 강화 ----------
def test_gloss_snapshot() -> bool:
    ok = ("통제 태그" not in _ARC_PLANNER_SRC)                       # 상태명사 유도 gloss 소거
    ok &= (_ARC_PLANNER_SRC.count(_POS_EVENT_GLOSS) >= 2)            # build_spine + _gen_episodes 두 경로
    ok &= (_MOVE_STRENGTH in _ARC_PLANNER_SRC)                      # protagonist_move '지면에서 실행되는 수' 강화
    print(f"[{'OK' if ok else 'FAIL'}] gloss 스냅샷: 통제태그 소거 · 긍정 재정의 {_ARC_PLANNER_SRC.count(_POS_EVENT_GLOSS)}경로 · move 강화")
    return ok


# ---------- 2) beat_for_episode 프롬프트(실경로): move 강화 주입 · 자기기술 앵커 회귀 없음 ----------
def test_move_gloss_injected() -> bool:
    w = _world()
    arc, ep = _arc_ep()
    cap = _Cap()
    beat = ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전 줄거리"], [])
    ok = (_MOVE_STRENGTH in cap.sys)                                # 강화 gloss 실제 주입
    ok &= (_SELFDESC_ANCHOR in cap.sys)                            # 기존 자기기술 항목 보존(회귀 없음)
    ok &= ("결정·선택·먼저 나섬" in cap.sys)                        # DP-2 행위태 lens 보존
    ok &= (beat.protagonist_move == "도현이 먼저 나섰다(선수)")     # 산출 파싱 회귀 없음
    print(f"[{'OK' if ok else 'FAIL'}] move 강화 gloss 주입 · 자기기술/lens 보존 · 산출 파싱")
    return ok


# ---------- 3) DP-12 트리거 분기: 재료 남음/소진/부재(보수적 폴백) ----------
def _exhaust_gate(beat_events, text) -> bool:
    """harness while 조건의 '비트 미소진' gate 를 그대로 재현(판정 자산 = drift.uncovered)."""
    ev = [e for e in (beat_events or []) if (e or "").strip()]
    return bool(not ev or uncovered(ev, text))   # True=아직 미소진(이어쓰기 계속), False=소진(중단)


def test_trigger_branching() -> bool:
    events = ["도현이 길드장을 도발한다", "각인을 숨긴 채 첫 수를 둔다"]
    body_partial = "도현이 길드장을 도발하며 판을 열었다."             # 둘째 사건 미실현
    body_full = "도현이 길드장을 도발했다. 그리고 각인을 숨긴 채 첫 수를 두었다."  # 둘 다 실현
    ok = (bool(uncovered(events, body_partial)))                    # 미실현 남음
    ok &= (not uncovered(events, body_full))                        # 전부 실현(소진)
    # gate: 재료 남음 → 계속 / 소진 → 중단 / 부재 → 보수적 폴백(계속=기존 norm 기준 유지)
    ok &= (_exhaust_gate(events, body_partial) is True)
    ok &= (_exhaust_gate(events, body_full) is False)
    ok &= (_exhaust_gate([], "짧은 본문") is True)                   # key_events 부재 → 보수적 폴백
    ok &= (_exhaust_gate(["  "], "짧은 본문") is True)               # 공백만 → 부재 취급
    print(f"[{'OK' if ok else 'FAIL'}] DP-12 트리거: 재료남음=계속 · 소진=중단 · 부재=보수적폴백")
    return ok


# ---------- 4) DP-12 소스 와이어링: harness 트리거가 미소진 gate + 폴백을 쓴다 ----------
def test_harness_wiring() -> bool:
    ok = ("from .drift import uncovered as _uncovered" in _HARNESS_SRC)   # 판정 자산 재사용 임포트
    ok &= ("_beat_events = [e for e in (beat.get(\"key_events\")" in _HARNESS_SRC)
    ok &= ("_uncovered(_beat_events, text)" in _HARNESS_SRC)             # 미소진 gate
    ok &= ("not _beat_events or _uncovered(_beat_events, text)" in _HARNESS_SRC)  # 보수적 폴백 포함
    ok &= ("under_norm" in _HARNESS_SRC)                                # norm 미달 가시화 유지
    print(f"[{'OK' if ok else 'FAIL'}] harness 와이어링: uncovered 임포트 · 미소진 gate · 폴백 · under_norm 유지")
    return ok


# ---------- 5) DP-11 내면 결단어 중립화(하위호환) ----------
def test_inner_decision_neutralized() -> bool:
    import dp1_dopamine_baseline as dp1
    ok = (set(dp1._INNER_DECISION) == {"결정", "결심", "계획", "설계"})
    ok &= all(w not in dp1._INITIATE for w in dp1._INNER_DECISION)   # _INITIATE 에서 제거됨
    # 내면 결심/계획만 있는 회차: 개시로 안 셈(inner 로 분리), 순-능동 아님
    chapters = [{"chapter": 1, "text": "도현은 복수를 결심했다. 도현은 계획을 세웠다."}]
    m = dp1.activity_metrics(chapters, ["도현"])
    row = m["per_chapter"][0]
    ok &= (row["init"] == 0 and row["inner"] == 2 and row["react"] == 0)
    ok &= (row["active_open"] is False and row["agency_score"] == 0.0)
    # 행위(지면 실행) 회차는 불변 — DP-2 IN-27 시나리오 회귀 가드(ratio 0.5, inner 0)
    acts = [{"chapter": 1, "text": "도현은 먼저 움직였다. 도현이 적을 향해 검을 뽑아 던졌다. 도현은 판을 장악했다."},
            {"chapter": 2, "text": "도현은 당황했다. 도현이 뒤로 밀렸다. 도현은 속수무책으로 끌려갔다."}]
    m2 = dp1.activity_metrics(acts, ["도현"])
    ok &= (m2["ratio"] == 0.5 and m2["flags"] == [True, False])
    ok &= (all(r["inner"] == 0 for r in m2["per_chapter"]))
    print(f"[{'OK' if ok else 'FAIL'}] 내면결단 중립화: 결심/계획 회차 init={row['init']}/inner={row['inner']} · 행위 회차 불변(ratio {m2['ratio']})")
    return ok


def main() -> int:
    results = [
        test_gloss_snapshot(),
        test_move_gloss_injected(),
        test_trigger_branching(),
        test_harness_wiring(),
        test_inner_decision_neutralized(),
    ]
    print("\nR1(DP-11+12) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_r1_dp11_dp12_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
