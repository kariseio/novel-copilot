# -*- coding: utf-8 -*-
"""DP-2 검증 — 주인공 선수(先手) 계획 슬롯(protagonist_move). LLM 0콜.

배경(dp1_dopamine_baseline.md §5.4): A 5~6화 주인공 개시 붕괴는 프로즈가 아니라 '비트(적 데스크 신 배정)'에서
결정됨 → '이 회차에 주인공이 스스로 여는 수' 슬롯은 비트 레이어가 맞다(T2 교훈: 프로즈 강제 아님).

개입: beat_for_episode 의 자기 기술(G4 계보) 확장 — Beat.protagonist_move + 긍정형 단문 1개.
무강제(프로즈 게이트 0)·genre-blind(장르 분기 없음)·pink-elephant(피할 대상 비노출).

검증 축:
1) 스키마 하위호환 — Beat 기본값 "" · 필드 결측 dict 역직렬화(구 영속 세계) → "".
2) 긍정 지시 주입 — sys 에 '스스로 여는 수' 긍정 단문 + protagonist_move 자기기술 요청(부정명령 없음).
3) 산출·key_events 반영 — LLM 이 protagonist_move 를 내면 Beat 가 싣고, 그 수가 key_events 에 담김.
4) 미기술 하위호환 — LLM 이 필드 생략 → beat.protagonist_move == ""(강제 합성 없음).
5) IN-27① 사전/사후 측정 가능 — 능동 개시율 도구(activity_metrics)가 결정론으로 회차 프로즈를 측정.

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_dp2_protagonist_move.py
"""
from __future__ import annotations
import sys
import json
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.narrative import Arc, Episode
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


# 긍정 단문의 앵커 문구(sys 주입 검증용) — 프롬프트 문구와 동기화.
_POS_ANCHOR = "매 회차 주인공의 의지가 최소 한 번 사건을 연다"
_SELFDESC_ANCHOR = "protagonist_move(이 회차에서 주인공이 스스로 연 수"


class _Cap(LLMProvider):
    """sys/usr 캡처 + 지정 JSON 반환(폴백 아닌 실경로). 없으면 protagonist_move 포함 완본 반환."""
    def __init__(self, payload: dict | None = None):
        super().__init__()
        self.sys = ""
        self.usr = ""
        self._payload = payload if payload is not None else {
            "title": "선공", "summary": "도현이 먼저 판을 연다",
            "key_events": ["도현이 길드장을 먼저 도발해 결투를 건다", "각인을 숨긴 채 첫 수를 둔다"],
            "entities": ["hero"], "chapter_function": "escalation", "hook_type": "decision",
            "time_advance": "없음", "time_delta": {"amount": 0, "unit": "minute", "mode": "advance"},
            "place": "길드", "protagonist_move": "도현이 길드장을 먼저 도발해 결투를 건다(선수·도발)"}

    def chat(self, messages, *a, **k):
        self.sys = messages[0]["content"]
        self.usr = messages[-1]["content"]
        return json.dumps(self._payload, ensure_ascii=False)

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    return WorldConfig(title="t", genre="x",
                       entities=[EntitySpec(id="hero", name="도현",
                                            profile="복수를 원하는 회귀자. 욕망=판을 자기 손으로 뒤집는다")])


def _arc_ep():
    arc = Arc(arc_id="arc1", order=1, title="A1", goal="g")
    ep = Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", premise="도입",
                 climax="도현이 정체를 드러낸다", required_events=["첫 대치"],
                 required_cast=["hero"], target_chapters=3)
    return arc, ep


# ---------- 1) 스키마 하위호환 ----------
def test_schema_backward_compat() -> bool:
    b = Beat(chapter=1)
    ok = (b.protagonist_move == "")                          # 기본값
    # 구 영속 세계(필드 결측 dict) 역직렬화 — pydantic 하위호환
    old = {"chapter": 5, "title": "구", "key_events": ["e"], "chapter_function": "setup"}
    b2 = Beat(**old)
    ok &= (b2.protagonist_move == "" and b2.chapter == 5)
    # 직렬화 왕복(필드 추가 = 가산적, 기존 필드 불변)
    b3 = Beat(**b.model_dump())
    ok &= (b3.protagonist_move == "")
    print(f"[{'OK' if ok else 'FAIL'}] 스키마 하위호환: 기본값·결측 역직렬화·왕복")
    return ok


# ---------- 2) 긍정 지시 주입(부정명령·앵커링 없음) ----------
def test_positive_directive_and_selfdesc_in_sys() -> bool:
    w = _world()
    arc, ep = _arc_ep()
    cap = _Cap()
    ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전 줄거리"], [])
    ok = (_POS_ANCHOR in cap.sys)                            # 긍정 단문 주입
    ok &= ("인물의 욕망에서 도출" in cap.sys)                 # 인물 설정(욕망)에서 도출
    ok &= (_SELFDESC_ANCHOR in cap.sys)                     # 자기기술 요청 항목
    # pink-elephant: DP-2 지시는 '피할 대상(반응·수습만 채우기)'을 프롬프트에 노출하지 않는다.
    ok &= ("반응·수습만" not in cap.sys and "반응만으로" not in cap.sys)
    # MED(적대검증): 갈등톤 예시(역습·판 설계)는 잔잔물 앵커링 위험 → sys 에서 배제(genre-blind 소스 차단).
    #   행위태 예시는 장르중립(결정·선택·먼저 나섬)만 노출 — 잔잔물이 '역습·판 설계'로 갈등을 발명하지 않게.
    ok &= ("역습" not in cap.sys and "판 설계" not in cap.sys)
    # JSON 스캐폴드에 필드 노출
    ok &= ('"protagonist_move":""' in cap.usr)
    print(f"[{'OK' if ok else 'FAIL'}] sys 긍정지시+자기기술 주입 · 부정명령 무 · usr 스캐폴드 노출")
    return ok


# ---------- 3) 산출 + key_events 반영 ----------
def test_move_produced_and_reflected_in_key_events() -> bool:
    w = _world()
    arc, ep = _arc_ep()
    cap = _Cap()
    beat = ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전"], [])
    ok = (beat.protagonist_move == "도현이 길드장을 먼저 도발해 결투를 건다(선수·도발)")   # 파싱 산출
    ok &= ("도현이 길드장을 먼저 도발해 결투를 건다" in beat.key_events)                  # key_events 반영
    ok &= (beat.chapter_function == "escalation" and beat.hook_type == "decision")       # 기존 라벨 회귀 없음
    print(f"[{'OK' if ok else 'FAIL'}] protagonist_move 산출='{beat.protagonist_move[:24]}…' · key_events 반영")
    return ok


# ---------- 4) 미기술 하위호환(강제 합성 없음) ----------
def test_omitted_move_defaults_empty() -> bool:
    w = _world()
    arc, ep = _arc_ep()
    # LLM 이 protagonist_move 를 생략 — 강제 채움 없이 "" 유지(무강제)
    payload = {"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"],
               "chapter_function": "relation", "hook_type": "emotion", "time_advance": "없음", "place": "p"}
    cap = _Cap(payload=payload)
    beat = ArcPlanner(cap).beat_for_episode(w, arc, ep, 4, False, ["직전"], [])
    ok = (beat.protagonist_move == "" and beat.chapter_function == "relation")
    # 폴백 경로(예외)도 기본값 "" (강제 합성 없음)
    class _Boom(LLMProvider):
        def chat(self, *a, **k): raise RuntimeError("boom")
        def embed(self, texts): return [[0.0] * 4 for _ in texts]
    fb = ArcPlanner(_Boom()).beat_for_episode(w, arc, ep, 4, False, ["직전"], [])
    ok &= (fb.protagonist_move == "")
    print(f"[{'OK' if ok else 'FAIL'}] 미기술·폴백 → protagonist_move='' (무강제)")
    return ok


# ---------- 5) IN-27① 사전/사후 측정 가능(결정론) ----------
def test_in27_active_initiation_measurable() -> bool:
    import dp1_dopamine_baseline as dp1
    world = {"entities": [{"id": "hero", "name": "도현"}]}
    chapters = [
        {"chapter": 1, "text": "도현은 먼저 움직였다. 도현이 적을 향해 검을 뽑아 던졌다. 도현은 판을 장악했다."},
        {"chapter": 2, "text": "도현은 당황했다. 도현이 뒤로 밀렸다. 도현은 속수무책으로 끌려갔다."},
    ]
    name, needles = dp1.detect_protagonist(world, chapters)
    m = dp1.activity_metrics(chapters, needles)
    # 회차1=능동(개시 우세), 회차2=반응 → ratio 0.5 (도구가 사전/사후 능동 개시율을 결정론 산출)
    ok = (name == "도현" and m["ratio"] == 0.5 and m["flags"] == [True, False])
    print(f"[{'OK' if ok else 'FAIL'}] IN-27① 측정 가능: 주인공={name} 능동개시율={m['ratio']} flags={m['flags']}")
    return ok


def main() -> int:
    results = [
        test_schema_backward_compat(),
        test_positive_directive_and_selfdesc_in_sys(),
        test_move_produced_and_reflected_in_key_events(),
        test_omitted_move_defaults_empty(),
        test_in27_active_initiation_measurable(),
    ]
    print("\nDP-2(주인공 선수 슬롯) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_dp2_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
