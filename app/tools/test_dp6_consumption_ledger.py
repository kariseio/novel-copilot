# -*- coding: utf-8 -*-
"""DP-6 검증 — 에피소드 소비 원장(이미 지면에 실현된 required/climax 차감·재탕 소스차단). LLM 0콜.

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_dp6_consumption_ledger.py

검증 축:
1) 재탕 시나리오(결정론): setup 회차가 required/climax 키워드를 지면에 실현 → 다음 비트 입력에서
   그 required 는 차감되고(미실현만 전달), 비-finale climax 슬롯은 '잔여 미실현 요소'로 재조준된다.
2) 보수성: 애매하면(키워드 과반 미달) 유지, finale 은 required·climax 를 둘 다 차감/재조준하지 않음
   (마지막 지면 약속 — uncovered 오탐이 '마지막 기회'에서 미실현 필수사건을 죽이는 최악을 차단·M2 안전망 복원).
3) 하위호환: override 미전달(None) → 기존 동작(episode 값 그대로). 첫 회차(본문 없음)=차감 없음 no-op.
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
from novelcopilot.llm.base import LLMProvider
import novelcopilot.worldgen.arc_planner as apmod


class Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


class FakeCapture(LLMProvider):
    """beat_for_episode 의 usr 프롬프트를 캡처(정상 JSON 반환 → 폴백 아닌 실경로 렌더 검증)."""
    def __init__(self): super().__init__(); self.usr = ""
    def chat(self, messages, *a, **k):
        self.usr = messages[-1]["content"]
        return json.dumps({"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"],
                           "chapter_function": "setup", "hook_type": "question", "time_advance": "없음",
                           "time_delta": {"amount": 0, "unit": "minute", "mode": "advance"},
                           "place": "p"}, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    return WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="도현")])


def _ep(**kw) -> Episode:
    base = dict(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", premise="도입",
                climax="도현이 측정장에서 정체를 숨긴다",
                required_events=["측정 위장", "각성 은폐"], required_cast=["hero"], target_chapters=3)
    base.update(kw)
    return Episode(**base)


# ---------- 1) 유닛: beat_for_episode 가 override 로 슬롯을 렌더/차감하는가(실경로) ----------
def test_beat_override_rendering() -> None:
    w = _world()
    arc = Arc(arc_id="arc1", order=1, title="A1", goal="g")
    ep = _ep()
    fc = FakeCapture()
    ArcPlanner(fc).beat_for_episode(w, arc, ep, 2, False, ["직전"], [],
                                    required_override=["각성 은폐"], climax_override="각성 은폐")
    ok = ("[필수 사건]['각성 은폐']" in fc.usr)                 # override 로 미실현만 렌더
    ok &= ("측정 위장" not in fc.usr)                           # 소진분은 '보이지 않게' 사라짐(앵커링 없음)
    ok &= ("[에피소드 절정]각성 은폐" in fc.usr)                 # 절정 슬롯 재조준(중립 필러 아님)
    ok &= ("도현이 측정장에서 정체를 숨긴다" not in fc.usr)      # 원본 climax 노출 안 함
    # 하위호환: override 미전달 → episode 값 그대로
    fc2 = FakeCapture()
    ArcPlanner(fc2).beat_for_episode(w, arc, ep, 2, False, ["직전"], [])
    ok &= ("[필수 사건]['측정 위장', '각성 은폐']" in fc2.usr)
    ok &= ("[에피소드 절정]도현이 측정장에서 정체를 숨긴다" in fc2.usr)
    # 폴백 경로(FakeEmpty)도 eff_required/eff_climax 사용(소진 차감이 폴백에서 새지 않음)
    beat = ArcPlanner(Fake()).beat_for_episode(w, arc, ep, 2, False, ["직전"], [],
                                               required_override=["각성 은폐"], climax_override="각성 은폐",
                                               event_menu=["메뉴1"])
    ok &= (beat.key_events[0] == "각성 은폐") and ("측정 위장" not in beat.key_events)
    print(f"[{'OK' if ok else 'FAIL'}] 유닛: override 슬롯 렌더·소진 차감·재조준·하위호환·폴백")
    assert ok, "유닛: override 슬롯 렌더/소진 차감/재조준/하위호환/폴백 실패"


# ---------- 2) 통합: copilot 소비 원장이 올바른 override 를 계산·전달하는가 ----------
def _mk_state(prior_text: str | None, cie: int, cur_ch: int, ep_target: int = 3) -> ProjectState:
    w = _world()
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", goal="g", episodes=[_ep(target_chapters=ep_target)])])
    st = ProjectState(id="t", seed=ProjectSeed(premise="p", target_chapters=6), world=w, created_at="t")
    st.current_chapter = cur_ch
    st.narrative_progress = NarrativeProgress(current_arc_id="arc1", current_episode_id="arc1_ep1",
                                              chapters_in_episode=cie)
    if prior_text is not None:
        st.chapters = [ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, arc_id="arc1",
                                     episode_id="arc1_ep1", text=prior_text, summary="s1")]
    return st


def _capture_overrides(st: ProjectState) -> dict:
    """generate_next_chapter 를 태워 beat_for_episode 에 전달되는 override(소비원장 산출)를 캡처.
    beat_for_episode·generator.generate 스텁(LLM 0콜) — 생성은 ESCALATED 로 롤백되나 캡처는 그 전에 발생."""
    svc = CopilotService(get_settings(), FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    svc._planning_provider = Fake()        # 설계 콜(메뉴 등) 네트워크 차단
    svc.repo.save(st)
    sess, _ = svc.get_session("t")
    sess.provider = Fake()                 # beat_repeat_score embed 등 네트워크 차단
    cap: dict = {}
    _orig = apmod.ArcPlanner.beat_for_episode

    def _cap(self, world, arc, ep, ch, fin, rec, direc, plant_notes="", **kw):
        if not cap:                        # 최초 호출(초기 비트 설계)만 기록
            cap.update(required_override=kw.get("required_override"),
                       climax_override=kw.get("climax_override"), is_finale=fin, chapter=ch)
        return Beat(chapter=ch, title="t", summary="s", key_events=["e"], entities=["hero"],
                    arc_id=ep.arc_id, episode_id=ep.episode_id, is_episode_finale=fin)

    apmod.ArcPlanner.beat_for_episode = _cap
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
        ChapterRecord(chapter=ch_no, status=ChapterStatus.ESCALATED, text="x")
    try:
        svc.generate_next_chapter("t")
    finally:
        apmod.ArcPlanner.beat_for_episode = _orig
    return cap


def test_ledger_wiring() -> None:
    ok = True
    # (a) 재탕(비-finale): ch1(setup)이 '측정 위장'과 climax 키워드(도현/측정장/정체)를 실현 → ch2 비트 입력에서
    #     '측정 위장' 차감·미실현('각성 은폐')만, climax 슬롯은 잔여로 재조준.
    body = "도현이 측정장에서 측정 위장을 했다. 정체를 숨겼다."
    cap = _capture_overrides(_mk_state(body, cie=1, cur_ch=1, ep_target=3))
    ok &= (cap.get("chapter") == 2 and cap.get("is_finale") is False)
    ok &= (cap.get("required_override") == ["각성 은폐"])            # 소진 required 차감
    ok &= (cap.get("climax_override") == "각성 은폐")               # 비-finale climax 재조준(소진분 제외)
    print(f"   (a) 비-finale 재탕: required_override={cap.get('required_override')} climax_override={cap.get('climax_override')}")

    # (b) finale 은 required·climax 를 둘 다 차감/재조준하지 않는다 — 마지막 지면이라 약속이 여기서 터져야 하므로
    #     원본(full) 유지. uncovered 오탐이 '마지막 기회'에서 미실현 required 를 죽이는 최악을 원천 차단(M2 안전망 복원).
    cap_f = _capture_overrides(_mk_state(body, cie=1, cur_ch=1, ep_target=2))   # target 2 → ch2=finale
    ok &= (cap_f.get("is_finale") is True)
    ok &= (cap_f.get("required_override") is None)                   # finale=required 차감 안 함(full 유지·안전망 복원)
    ok &= (cap_f.get("climax_override") is None)                     # 원본 climax 유지(재조준 없음)
    print(f"   (b) finale: required_override={cap_f.get('required_override')} climax_override={cap_f.get('climax_override')}")

    # (c) 보수성: 지면이 아무것도 실현 안 함(키워드 과반 미달) → 차감 없음·재조준 없음.
    cap_c = _capture_overrides(_mk_state("도현은 조용히 앉아 있었다.", cie=1, cur_ch=1, ep_target=3))
    ok &= (cap_c.get("required_override") == ["측정 위장", "각성 은폐"])   # 전부 유지
    ok &= (cap_c.get("climax_override") is None)                          # 재조준 없음
    print(f"   (c) 보수(애매): required_override={cap_c.get('required_override')} climax_override={cap_c.get('climax_override')}")

    # (d) 하위호환/no-op: 첫 회차(에피소드 본문 없음) → 차감 없음(full required)·재조준 없음.
    cap_d = _capture_overrides(_mk_state(prior_text=None, cie=0, cur_ch=0, ep_target=3))
    ok &= (cap_d.get("required_override") == ["측정 위장", "각성 은폐"])
    ok &= (cap_d.get("climax_override") is None)
    print(f"   (d) 첫 회차 no-op: required_override={cap_d.get('required_override')} climax_override={cap_d.get('climax_override')}")

    print(f"[{'OK' if ok else 'FAIL'}] 통합: 소비원장 override 계산(재탕차감/비-finale재조준/finale보존/보수/no-op)")
    assert ok, "통합: 소비원장 override 계산(재탕차감/비-finale재조준/finale보존/보수/no-op) 실패"


if __name__ == "__main__":
    test_beat_override_rendering()
    test_ledger_wiring()   # assert 로 실패 시 즉시 AssertionError(비영점 종료) — pytest 와 동일 게이트
    print("\nDP-6 검증: ALL GREEN ✅")
    sys.exit(0)
