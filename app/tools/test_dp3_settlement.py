# -*- coding: utf-8 -*-
"""DP-3' 검증 — 정산 리듬 계획 산술(payoff 배치 + 무정산 연속 상한 K) (LLM 0콜·결정론).

설계(design-b37-dp3.md §DP-3'):
  ⓐ 에피소드 분해 지시에 payoff 배치(긍정형·기존 payoffs 필드 소비 강화·위치 포함).
  ⓑ 무정산 연속 상한 K=3 계획 산술(plan_lint 계보·DP-13 재계획 경로 재사용·chapter_function=payoff 라벨 추적).

이 테스트는:
  · settlement_plan 유닛: unsettled_run·settlement_gap 경계(K=3 도달·payoff 리셋·라벨 결측 카운트·비활성).
  · ⓐ 스키마: build_spine/_gen_episodes 산출 Episode 가 payoff_at 을 파싱하는가 + 프롬프트가 payoff 배치를 긍정형으로 담는가(genre-blind).
  · ⓑ 재계획 경로: 에피소드 내 K화 연속 무정산이면 DP-13 경로로 재계획 1회(긍정 배치 directive) 발화.
  · ⓑ 무오탐: 정상 정산 케이스(직전이 payoff·이번이 payoff·run<K)는 재계획을 트리거하지 않는다.
  · ⓑ 재료 보존: 재계획은 required/climax override 를 그대로 물려(어떤 재료도 죽이지 않음 — DP-13 HIGH).

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_dp3_settlement.py
"""
from __future__ import annotations
import sys
import json
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.narrative import (NarrativeSpine, Arc, Episode, EndingSpec, NarrativeProgress)
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.engine.settlement_plan import unsettled_run, settlement_gap, _is_payoff
from novelcopilot.llm.base import LLMProvider
import novelcopilot.worldgen.arc_planner as apmod


class Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


class _Cap(LLMProvider):
    """chat 시스템/유저 프롬프트 캡처 + 지정 JSON 반환 Fake."""
    def __init__(self, payload: dict):
        super().__init__(); self.payload = payload; self.sys = ""; self.usr = ""
    def chat(self, msgs, **k):
        self.sys = msgs[0]["content"]; self.usr = msgs[-1]["content"]
        return json.dumps(self.payload, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


# ============================================================
# ⓑ-유닛: unsettled_run / settlement_gap 경계
# ============================================================
def test_unsettled_run_counts_trailing_nonpayoff() -> None:
    # 끝에서부터 연속 무정산 회차 수(이번 회차 포함). payoff 를 만나면 끊긴다.
    ok = (unsettled_run(["setup", "escalation"], "escalation") == 3)     # 3개 연속 무정산(2 prev + 이번)
    ok &= (unsettled_run(["payoff", "setup"], "escalation") == 2)        # 앞 payoff 에서 끊김 → setup·이번 =2
    ok &= (unsettled_run(["setup", "payoff"], "escalation") == 1)        # 직전이 payoff → 이번만 =1(리듬 리셋)
    ok &= (unsettled_run([], "setup") == 1)                              # 첫 회차 무정산 =1
    ok &= (unsettled_run(["setup"], "payoff") == 0)                      # 이번이 정산 → 0(리듬 리셋)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ unsettled_run: 후행 연속 무정산·payoff 리셋·이번 payoff=0")
    assert ok


def test_missing_label_counted_as_unsettled() -> None:
    # 라벨 결측('')은 정산 근거 없음 → 무정산으로 셈(보수적, 정산으로 안 침).
    ok = (unsettled_run(["", "setup"], "") == 3)                          # 결측 2 + 이번 결측 =3
    ok &= (unsettled_run(["payoff", ""], "") == 2)                        # payoff 뒤 결측·이번 결측 =2
    ok &= (not _is_payoff("") and not _is_payoff(None) and not _is_payoff("setup"))
    ok &= (_is_payoff("payoff") and _is_payoff(" Payoff ") and _is_payoff("PAYOFF"))   # 정규화 완전일치
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 라벨 결측=무정산 카운트·payoff 정규화 완전일치")
    assert ok


def test_settlement_gap_boundary_k3() -> None:
    # K=3: run>=3 이면 그 run(트리거), run<3 이면 None(정상 리듬).
    ok = (settlement_gap(["setup"], "setup", 3) is None)                  # run=2 < 3 → 무트리거
    ok &= (settlement_gap(["setup", "escalation"], "setup", 3) == 3)      # run=3 → 트리거
    ok &= (settlement_gap(["setup", "escalation", "relation"], "setup", 3) == 4)   # run=4 → 트리거
    ok &= (settlement_gap(["setup", "escalation"], "payoff", 3) is None)  # 이번이 payoff → run 0 → None
    ok &= (settlement_gap(["payoff", "setup"], "escalation", 3) is None)  # 최근 payoff 로 run=2 → None
    # 비활성: k<=1 은 상시 재계획(무강제 위반) → None. 비정형 k → None.
    ok &= (settlement_gap(["setup", "escalation"], "setup", 1) is None
           and settlement_gap(["setup", "escalation"], "setup", 0) is None
           and settlement_gap(["setup", "escalation"], "setup", "x") is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ settlement_gap: K=3 경계·payoff 리셋·k<=1 비활성")
    assert ok


# ============================================================
# ⓐ-스키마: payoff_at 파싱 + 프롬프트 긍정 배치 지시(genre-blind)
# ============================================================
def _spine_payload(payoff_at: str = "mid") -> dict:
    return {"ending": {"central_question": "Q", "ending": "E", "thematic_payoff": "T"},
            "arcs": [{"title": "A1", "goal": "g", "central_conflict": "c", "turning_point": "t",
                      "episodes": [{"title": "E1", "premise": "p", "climax": "cx",
                                    "required_events": ["일어나는 사건"], "required_cast": ["hero"],
                                    "plants": [], "payoffs": ["첫 승리를 거둔다"],
                                    "payoff_at": payoff_at, "target_chapters": 4}],
                      "new_cast": []}]}


def test_build_spine_parses_payoff_at_and_prompt() -> None:
    w = WorldConfig(title="t", genre="g", entities=[EntitySpec(id="hero", name="주인공")])
    cap = _Cap(_spine_payload("mid"))
    spine = ArcPlanner(cap).build_spine(w, target_chapters=8)
    ep = spine.arcs[0].episodes[0]
    ok = (ep.payoff_at == "mid" and ep.payoffs == ["첫 승리를 거둔다"])       # payoff_at 파싱 + payoffs 소비
    # 프롬프트가 payoff 배치를 긍정형으로 담고 위치(early|mid|climax)를 요구하는가 — genre-blind(장르 트로프 호명 없음)
    ok &= ("payoff" in cap.usr and "payoff_at" in cap.usr and "early|mid|climax" in cap.usr)
    ok &= ("지불되는 사건" in cap.usr)                                        # 긍정형 '지불' 배치 지시
    ok &= ('"payoff_at"' in cap.usr)                                        # JSON 스키마에 필드 노출
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ build_spine: payoff_at 파싱·긍정 배치 지시·스키마 노출")
    assert ok


def test_gen_episodes_parses_payoff_at_and_prompt() -> None:
    # lazy 분해 경로도 대칭(payoff_at 파싱 + 긍정 배치 지시)
    w = WorldConfig(title="t", genre="g", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="arc2", order=2, title="A2", goal="g2")])
    payload = {"episodes": [{"title": "L1", "premise": "p", "climax": "cx",
                             "required_events": ["사건"], "required_cast": ["hero"],
                             "plants": [], "payoffs": ["보상 실현"], "payoff_at": "early",
                             "target_chapters": 4}], "new_cast": []}
    cap = _Cap(payload)
    planner = ArcPlanner(cap)
    arc = w.spine.arcs[0]
    planner._gen_episodes(w, arc, ["직전"], remaining=8)
    ep = arc.episodes[0]
    ok = (ep.payoff_at == "early" and ep.payoffs == ["보상 실현"])
    ok &= ("payoff_at" in cap.sys and "early|mid|climax" in cap.sys and "지불되는 사건" in cap.sys)
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ _gen_episodes(lazy): payoff_at 파싱·대칭 긍정 배치 지시")
    assert ok


def test_payoff_at_backward_compat_missing() -> None:
    # payoff_at 미제공(구 payload) → 빈 문자열(하위호환·구 레코드 로드 안전)
    payload = _spine_payload("mid")
    del payload["arcs"][0]["episodes"][0]["payoff_at"]
    w = WorldConfig(title="t", genre="g", entities=[EntitySpec(id="hero", name="주인공")])
    spine = ArcPlanner(_Cap(payload)).build_spine(w, target_chapters=8)
    ok = (spine.arcs[0].episodes[0].payoff_at == "")
    # 구 JSON 라운드트립: payoff_at 없는 dict 로 Episode 생성 안전
    ok &= (Episode(episode_id="e", arc_id="a", order=1).payoff_at == "")
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ payoff_at 미제공 하위호환(빈 문자열·구 레코드 안전)")
    assert ok


# ============================================================
# ⓑ-통합: 재계획 경로(DP-13 재사용) 발화/무오탐
# ============================================================
def _ep(**kw) -> Episode:
    base = dict(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", premise="도입",
                climax="에피소드 절정 사건", required_events=["필수 사건A", "필수 사건B"],
                required_cast=["hero"], target_chapters=6)
    base.update(kw)
    return Episode(**base)


def _mk_state(prev_funcs: list[str], this_func: str) -> ProjectState:
    """직전 회차들(prev_funcs, 같은 에피소드 FINALIZED)의 chapter_function 을 심고,
    이번 비트가 this_func 로 계획되게 세팅한 상태."""
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="도현")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", goal="g", episodes=[_ep()])])
    st = ProjectState(id="t", seed=ProjectSeed(premise="p", target_chapters=12), world=w, created_at="t")
    n = len(prev_funcs)
    st.current_chapter = n
    st.narrative_progress = NarrativeProgress(current_arc_id="arc1", current_episode_id="arc1_ep1",
                                              chapters_in_episode=n)
    st.chapters = [ChapterRecord(chapter=i + 1, status=ChapterStatus.FINALIZED, arc_id="arc1",
                                 episode_id="arc1_ep1", text=f"본문{i+1}", summary=f"s{i+1}",
                                 chapter_function=fn)
                   for i, fn in enumerate(prev_funcs)]
    return st, this_func


def _capture_replan(st: ProjectState, this_func: str) -> dict:
    """generate_next_chapter 를 태워 beat_for_episode 호출을 모두 캡처.
    첫 호출은 this_func 로 라벨된 비트를 반환(초기 계획), 재계획(2번째 호출)이 있으면 그 directive 를 잡는다."""
    svc = CopilotService(get_settings(), FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    svc._planning_provider = Fake()
    svc.repo.save(st)
    sess, _ = svc.get_session("t")
    sess.provider = Fake()
    calls: list[dict] = []
    _orig = apmod.ArcPlanner.beat_for_episode

    def _cap(self, world, arc, ep, ch, fin, rec, direc, plant_notes="", **kw):
        calls.append(dict(direc=list(direc), required_override=kw.get("required_override"),
                          climax_override=kw.get("climax_override"), chapter=ch, is_finale=fin))
        # 첫 호출: this_func 라벨(정산 판정 대상). 재계획 호출도 같은 라벨 유지(수렴 여부는 테스트 관심 아님).
        return Beat(chapter=ch, title="t", summary="s", key_events=["e1", "e2"], entities=["hero"],
                    arc_id=ep.arc_id, episode_id=ep.episode_id, is_episode_finale=fin,
                    chapter_function=this_func)

    apmod.ArcPlanner.beat_for_episode = _cap
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
        ChapterRecord(chapter=ch_no, status=ChapterStatus.ESCALATED, text="x")
    try:
        svc.generate_next_chapter("t")
    finally:
        apmod.ArcPlanner.beat_for_episode = _orig
    return {"calls": calls}


def _replan_directive(calls: list[dict]) -> str | None:
    """재계획 호출(2번째)의 '(계획 결함 교정 필수)' directive 추출(없으면 None)."""
    if len(calls) < 2:
        return None
    for d in calls[1]["direc"]:
        if "계획 결함 교정 필수" in d:
            return d
    return None


def test_replan_fires_on_k_run() -> None:
    # 직전 2화(setup·escalation) + 이번(escalation) = 무정산 3연속 = K=3 도달 → 재계획 1회(긍정 배치 directive).
    st, tf = _mk_state(["setup", "escalation"], "escalation")
    out = _capture_replan(st, tf)
    calls = out["calls"]
    ok = (len(calls) == 2)                                              # 초기 + 재계획 1회(무한 루프 아님)
    directive = _replan_directive(calls)
    ok &= (directive is not None and "payoff" in directive)            # 긍정 배치 지시
    ok &= ("지불되는 사건" in directive)                                # 긍정형(결핍 진술 아님)
    ok &= ("무정산" not in directive)                                   # 부정/결핍 프레이밍 비노출(앵커 안전)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ K=3 도달 → 재계획 1회·긍정 배치 directive: calls={len(calls)}")
    assert ok


def test_no_replan_when_this_chapter_is_payoff() -> None:
    # 직전 2화 무정산이어도 이번 회차가 payoff 면 run=0 → 무트리거(정상 정산 리듬, 무오탐).
    st, tf = _mk_state(["setup", "escalation"], "payoff")
    out = _capture_replan(st, tf)
    # 이번이 payoff → settlement 무트리거. (다른 lint 위반이 없으면 재계획 0회.)
    directive = _replan_directive(out["calls"])
    ok = (directive is None or "payoff" not in directive) and len(out["calls"]) == 1
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 무오탐: 이번 회차 payoff → 정산 재계획 없음(calls={len(out['calls'])})")
    assert ok


def test_no_replan_when_recent_payoff_resets() -> None:
    # 직전에 payoff 가 있어 run<K → 무트리거(정상 리듬). [payoff, setup] + 이번(setup) → run=2 < 3.
    st, tf = _mk_state(["payoff", "setup"], "setup")
    out = _capture_replan(st, tf)
    ok = (len(out["calls"]) == 1)                                       # 재계획 없음
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 무오탐: 최근 payoff 로 run<K → 무트리거(calls={len(out['calls'])})")
    assert ok


def test_replan_preserves_material_overrides() -> None:
    # DP-13 HIGH: 재계획은 어떤 재료도 죽이지 않는다 — required/climax override 를 초기 호출과 동일하게 물려준다.
    st, tf = _mk_state(["setup", "escalation"], "escalation")
    out = _capture_replan(st, tf)
    calls = out["calls"]
    ok = (len(calls) == 2)
    # 재계획 호출도 초기와 같은 override 전달(비대칭 방지 — 소진원장·안전망 그대로)
    ok &= (calls[1]["required_override"] == calls[0]["required_override"])
    ok &= (calls[1]["climax_override"] == calls[0]["climax_override"])
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 재계획 재료 보존: required/climax override 동일 물림(DP-13 HIGH)")
    assert ok


def test_settlement_disabled_no_replan() -> None:
    # settlement_gap_k=1(비활성)이면 K-run 이어도 정산 재계획 없음(바이트 동일 하위호환).
    st, tf = _mk_state(["setup", "escalation"], "escalation")
    svc = CopilotService(get_settings(), FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    svc.settings = svc.settings.model_copy(update={"settlement_gap_k": 1})
    svc._planning_provider = Fake()
    svc.repo.save(st)
    sess, _ = svc.get_session("t")
    sess.provider = Fake()
    calls: list[dict] = []
    _orig = apmod.ArcPlanner.beat_for_episode

    def _cap(self, world, arc, ep, ch, fin, rec, direc, plant_notes="", **kw):
        calls.append(dict(direc=list(direc)))
        return Beat(chapter=ch, title="t", summary="s", key_events=["e1", "e2"], entities=["hero"],
                    arc_id=ep.arc_id, episode_id=ep.episode_id, is_episode_finale=fin,
                    chapter_function=tf)

    apmod.ArcPlanner.beat_for_episode = _cap
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
        ChapterRecord(chapter=ch_no, status=ChapterStatus.ESCALATED, text="x")
    try:
        svc.generate_next_chapter("t")
    finally:
        apmod.ArcPlanner.beat_for_episode = _orig
    ok = (len(calls) == 1)   # 재계획 없음(정산 게이트 비활성)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ settlement_gap_k<=1 비활성 → 재계획 없음(하위호환)")
    assert ok


_TESTS = [
    test_unsettled_run_counts_trailing_nonpayoff, test_missing_label_counted_as_unsettled,
    test_settlement_gap_boundary_k3,
    test_build_spine_parses_payoff_at_and_prompt, test_gen_episodes_parses_payoff_at_and_prompt,
    test_payoff_at_backward_compat_missing,
    test_replan_fires_on_k_run, test_no_replan_when_this_chapter_is_payoff,
    test_no_replan_when_recent_payoff_resets, test_replan_preserves_material_overrides,
    test_settlement_disabled_no_replan,
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
