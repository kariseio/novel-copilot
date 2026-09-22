# -*- coding: utf-8 -*-
"""DP-20 검증 — 정산 에스컬레이션·이자 트랙 (진행 소극성 소스 수리). LLM 0콜·결정론.

설계(docs/design-dp20-payoff-escalation.md §2):
  ⓐ 계약 주입 비대칭 해소 — _contract_block 을 _gen_episodes·generate_event_menu 프롬프트에 포함.
  ⓑ 이자 트랙 — build_spine/_gen_episodes payoff 지시 확장(긍정형): 에피소드 내 소정산 + 핵심 쾌감 첫 집행 아크 전반부.
  ⓒ required_events gloss 확장 — "필수 사건의 절반 이상은 주인공의 행동이 세계를 바꾸는 사건으로"(긍정형·트로프 호명 0).
  ⓓ 비트 레벨 이자 집행 — beat_for_episode 에 premise_asset 우위 하나를 이 회차 안에서 관측 가능한 결과로(원금 보존).

이 테스트는 4개 렌더 지점의 프롬프트 스냅샷(문안·계약 블록 포함)을 결정론으로 검증한다:
  · 계약 블록 4지점 주입(build_spine·_gen_episodes·generate_event_menu·beat_for_episode).
  · ⓑ 이자 트랙 긍정 문안(build_spine·_gen_episodes 대칭).
  · ⓒ gloss 확장 문안(build_spine·_gen_episodes 대칭).
  · ⓓ 비트 레벨 이자 집행 문안(beat_for_episode).
  · 원칙 가드: pink-elephant(부정명령·금지문 0)·genre-blind(장르 트로프 호명 0)·기존 payoff_at 스키마 보존.

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_dp20_payoff_escalation.py
"""
from __future__ import annotations
import sys
import json

from novelcopilot.domain.world import WorldConfig, EntitySpec, GenreContract
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.worldgen.arc_planner import _contract_block
from novelcopilot.llm.base import LLMProvider


class _Cap(LLMProvider):
    """chat 시스템/유저 프롬프트 캡처 + 지정 JSON 반환 Fake."""
    def __init__(self, payload: dict):
        super().__init__(); self.payload = payload; self.sys = ""; self.usr = ""
    def chat(self, msgs, **k):
        self.sys = msgs[0]["content"]; self.usr = msgs[-1]["content"]
        return json.dumps(self.payload, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


# 계약 원문 마커(장르 트로프 아님 — 작품 계약에서 옴). genre-blind 확인용: 코드가 심는 배치어에는 이 마커가 없다.
_GC = GenreContract(
    pleasure_engine="정보우위로 판을 뒤집는 통쾌함",
    reader_expectations=["사이다", "역전", "성장"],
    vocabulary_tone="현대 헌터물 어휘",
    premise_asset="회귀+미래지식은 소모되지 않는 장기 자산")


def _world_with_contract() -> WorldConfig:
    # 전제/시놉시스는 트로프 마커 없는 중립어로(genre-blind 검사가 '작가 데이터 에코'와 '코드 주입'을 혼동하지 않게 —
    # 계약 블록에는 premise_asset='회귀…'가 들어 있고 이는 작품이 스스로 도출한 계약 원문이라 정당하다).
    return WorldConfig(title="t", genre="헌터", tone="다크", premise="주인공의 복수극",
                       synopsis="적대 세력을 무너뜨리는 이야기", genre_contract=_GC,
                       entities=[EntitySpec(id="hero", name="주인공")])


def _spine_payload() -> dict:
    return {"ending": {"central_question": "Q", "ending": "E", "thematic_payoff": "T"},
            "arcs": [{"title": "A1", "goal": "g", "central_conflict": "c", "turning_point": "t",
                      "episodes": [{"title": "E1", "premise": "p", "climax": "cx",
                                    "required_events": ["일어나는 사건"], "required_cast": ["hero"],
                                    "plants": [], "payoffs": ["첫 승리를 거둔다"],
                                    "payoff_at": "early", "target_chapters": 4}],
                      "new_cast": []}]}


# 부정명령/금지문 프레이밍 마커(pink-elephant 위반 검출). '금지'는 코드 주석에만, 프롬프트 문안엔 없어야.
_NEG_MARKERS = ["소극", "지켜보는 사건만", "채우지 마라", "반응 일변도", "온레일"]
# 장르 트로프 호명 마커(genre-blind 위반 검출) — 코드가 심는 배치 문안에 특정 장르 장치 이름이 없어야.
_TROPE_MARKERS = ["회귀", "레벨업", "각성", "사이다", "먼치킨", "빙의", "환생"]
# DP-20 이 코드로 심는 배치·이자·능동 술어 문안 조각(작가 데이터·계약 원문과 분리) — 이 조각들에 트로프 호명이 0인지 검사.
_DP20_INJECTED = [
    "이 중 절반 이상은 주인공의 행동이 세계를 바꾸는 사건으로",
    "대정산(아크의 핵심 쾌감 집행)과 별개로",
    "그 에피소드 안에서 닫히는 소정산",
    "주인공의 우위가 관측 가능한 결과로 환금되는 사건",
    "핵심 쾌감의 첫 집행은 아크의 전반부 에피소드 안에 배치",
    "핵심 동력 전제(장기 자산)가 준 우위 하나를 이 회차 안에서 관측 가능한 결과로 닫히게",
    "자산 자체(원금)는 보존하고 이번 회차 몫의 이득만 환금",
]


def _no_trope_in_injected(text: str) -> bool:
    """DP-20 코드 주입 문안 조각(작가 데이터·계약 원문 밖)에 장르 트로프 호명이 0인가 — genre-blind 정밀 검사."""
    for frag in _DP20_INJECTED:
        if frag in text and any(m in frag for m in _TROPE_MARKERS):
            return False
    return True


# ============================================================
# ⓐ+ⓑ+ⓒ — build_spine 프롬프트 스냅샷
# ============================================================
def test_build_spine_snapshot() -> None:
    w = _world_with_contract()
    cap = _Cap(_spine_payload())
    spine = ArcPlanner(cap).build_spine(w, target_chapters=8)
    usr = cap.usr
    cblk = _contract_block(w)
    ok = True
    # ⓐ 계약 블록 주입(build_spine 은 기존부터 포함 — 회귀 가드)
    ok &= (cblk and cblk in usr and "정보우위로 판을 뒤집는 통쾌함" in usr)
    # ⓒ gloss 확장: '절반 이상 + 세계를 바꾸는 사건'(긍정형·능동 술어)
    ok &= ("절반 이상" in usr and "세계를 바꾸는 사건" in usr and "얻어내고" in usr)
    # ⓑ 이자 트랙: 소정산(에피소드 내 닫힘) + 핵심 쾌감 첫 집행 아크 전반부
    ok &= ("소정산" in usr and "관측 가능한 결과로 환금" in usr)
    ok &= ("첫 집행" in usr and "전반부" in usr)
    # 파싱 회귀: 기존 payoff_at 스키마 소비 유지(DP-3' 보존)
    ep = spine.arcs[0].episodes[0]
    ok &= (ep.payoff_at == "early" and ep.payoffs == ["첫 승리를 거둔다"])
    # pink-elephant: 부정/결핍 프레이밍 비노출
    ok &= all(m not in usr for m in _NEG_MARKERS)
    # genre-blind: DP-20 코드 주입 문안 조각에 장르 트로프 호명 0(작가 premise/계약 원문과 분리 검사)
    ok &= _no_trope_in_injected(usr)
    print(f"[{'OK' if ok else 'FAIL'}] build_spine: 계약 주입·ⓒgloss·ⓑ이자트랙·payoff_at 보존·pink-elephant·genre-blind")
    assert ok


# ============================================================
# ⓐ+ⓑ+ⓒ — _gen_episodes(lazy) 프롬프트 스냅샷 (대칭)
# ============================================================
def test_gen_episodes_snapshot() -> None:
    w = _world_with_contract()
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="arc2", order=2, title="A2", goal="g2")])
    payload = {"episodes": [{"title": "L1", "premise": "p", "climax": "cx",
                             "required_events": ["사건"], "required_cast": ["hero"],
                             "plants": [], "payoffs": ["보상 실현"], "payoff_at": "mid",
                             "target_chapters": 4}], "new_cast": []}
    cap = _Cap(payload)
    planner = ArcPlanner(cap)
    planner._gen_episodes(w, w.spine.arcs[0], ["직전 줄거리"], remaining=8)
    sys_p, usr = cap.sys, cap.usr
    cblk = _contract_block(w)
    ok = True
    # ⓐ 계약 블록 주입(신규 — 이전엔 lazy 경로 미주입, 비대칭 해소)
    ok &= (cblk and cblk in usr and "정보우위로 판을 뒤집는 통쾌함" in usr)
    # ⓒ gloss 확장(대칭·sys 프롬프트)
    ok &= ("절반 이상" in sys_p and "세계를 바꾸는 사건" in sys_p and "얻어내고" in sys_p)
    # ⓑ 이자 트랙(대칭·sys 프롬프트)
    ok &= ("소정산" in sys_p and "관측 가능한 결과로 환금" in sys_p)
    ok &= ("첫 집행" in sys_p and "전반부" in sys_p)
    # 파싱 회귀: payoff_at 소비 유지
    ep = w.spine.arcs[0].episodes[0]
    ok &= (ep.payoff_at == "mid" and ep.payoffs == ["보상 실현"])
    # pink-elephant + genre-blind(sys+usr 합산 검사)
    ok &= all(m not in (sys_p + usr) for m in _NEG_MARKERS)
    ok &= _no_trope_in_injected(sys_p + usr)
    print(f"[{'OK' if ok else 'FAIL'}] _gen_episodes(lazy): ⓐ계약주입(신규)·ⓒgloss·ⓑ이자트랙 대칭·payoff_at 보존")
    assert ok


# ============================================================
# ⓐ — generate_event_menu 프롬프트 스냅샷 (신규 계약 주입)
# ============================================================
def test_event_menu_contract_injection() -> None:
    w = _world_with_contract()
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="arc1", order=1, title="A1", goal="g1")])
    arc = w.spine.arcs[0]
    ep = Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", premise="도입",
                 climax="에피소드 절정", required_events=["필수 사건"], required_cast=["hero"],
                 payoffs=["보상 실현"], target_chapters=6)
    cap = _Cap({"event_menu": ["신선 사건1", "신선 사건2"]})
    menu = ArcPlanner(cap).generate_event_menu(w, arc, ep, ["직전 줄거리"])
    usr = cap.usr
    cblk = _contract_block(w)
    ok = True
    # CX-9 신계약: 장르 정체성 블록은 회차 인접 계층(적시 메뉴)에서 제거 — DP-20 ⓐ 대칭 주입을 대체
    ok &= (cblk not in usr and "정체성" not in usr)
    # never-empty·필수 우선 보존(기존 불변식 회귀)
    ok &= (menu and menu[0] == "필수 사건")
    # pink-elephant + genre-blind
    ok &= all(m not in usr for m in _NEG_MARKERS)
    ok &= _no_trope_in_injected(usr)
    print(f"[{'OK' if ok else 'FAIL'}] generate_event_menu: CX-9 계약 비주입·필수우선·never-empty·pink-elephant·genre-blind")
    assert ok


# ============================================================
# ⓓ — beat_for_episode 프롬프트 스냅샷 (비트 레벨 이자 집행)
# ============================================================
def test_beat_for_episode_interest_beat() -> None:
    w = _world_with_contract()
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="arc1", order=1, title="A1", goal="g1")])
    arc = w.spine.arcs[0]
    ep = Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", premise="도입",
                 climax="에피소드 절정", required_events=["필수 사건"], required_cast=["hero"],
                 target_chapters=6)
    payload = {"title": "회차", "summary": "s", "key_events": ["사건1", "사건2", "사건3"],
               "entities": ["hero"], "chapter_function": "escalation", "hook_type": "cliffhanger",
               "time_advance": "다음날", "place": "던전", "protagonist_move": "먼저 나선다"}
    cap = _Cap(payload)
    # chapter=5 (도입부 분기 회피 — ⓓ 문안은 도입/비도입 무관하게 sys 에 상시 포함)
    ArcPlanner(cap).beat_for_episode(w, arc, ep, chapter=5, is_finale=False,
                                     recent=["직전 줄거리"], directives=[])
    sys_p, usr = cap.sys, cap.usr
    cblk = _contract_block(w)
    ok = True
    # ⓓ 비트 레벨 이자 집행 문안(긍정형·원금 보존)
    ok &= ("핵심 동력 전제" in sys_p and "관측 가능한 결과로 닫히게" in sys_p)
    ok &= ("원금" in sys_p and "환금" in sys_p)   # 원금 보존 + 이번 회차 몫만 환금
    # CX-9 신계약: 회차 비트 콜에서 장르 정체성 블록 제거(생성 조향 → 심사 감시로 이관)
    ok &= (cblk not in usr)
    # pink-elephant + genre-blind(sys 문안)
    ok &= all(m not in sys_p for m in _NEG_MARKERS)
    ok &= _no_trope_in_injected(sys_p)
    print(f"[{'OK' if ok else 'FAIL'}] beat_for_episode: ⓓ이자집행(원금보존·긍정형)·CX-9 계약 비주입·pink-elephant·genre-blind")
    assert ok


# ============================================================
# 하위호환 — 계약 미생성 작품(구 데이터)에서 프롬프트 렌더 안전
# ============================================================
def test_no_contract_backward_compat() -> None:
    # genre_contract 미생성 → _contract_block=="" → 4지점 프롬프트가 빈 블록으로 안전 렌더(구 JSON 무영향).
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    ok = (_contract_block(w) == "")
    cap = _Cap(_spine_payload())
    ArcPlanner(cap).build_spine(w, target_chapters=8)
    # 계약 없어도 ⓑⓒ 배치 문안은 상시 렌더(계약 필드 소비와 독립 — 배치 규칙이라 genre-blind)
    ok &= ("소정산" in cap.usr and "절반 이상" in cap.usr)
    print(f"[{'OK' if ok else 'FAIL'}] 하위호환: 계약 미생성 → 빈 블록 안전 렌더·배치 문안은 상시(계약 독립)")
    assert ok


_TESTS = [
    test_build_spine_snapshot,
    test_gen_episodes_snapshot,
    test_event_menu_contract_injection,
    test_beat_for_episode_interest_beat,
    test_no_contract_backward_compat,
]

if __name__ == "__main__":
    results = []
    for t in _TESTS:
        try:
            t(); results.append(True)
        except AssertionError:
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
