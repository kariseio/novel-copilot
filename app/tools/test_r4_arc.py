# -*- coding: utf-8 -*-
"""R4 검증 — 엔딩-주도 아크/에피소드 커서·앵커·계층요약·드리프트 (LLM 0콜).
실행: PYTHONPATH=app python tools/test_r4_arc.py
"""
from __future__ import annotations
import sys

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec, NarrativeProgress
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine.factory import build_engine
from novelcopilot.engine.drift import episode_drift_signals
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.services.copilot import _arc_anchors, _build_story_so_far_hier
from novelcopilot.llm.base import LLMProvider


class Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _spine() -> NarrativeSpine:
    return NarrativeSpine(
        ending=EndingSpec(central_question="Q", ending="E", thematic_payoff="T"),
        arcs=[Arc(arc_id="arc1", order=1, title="A1", goal="g1", episodes=[
                  Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", climax="c1",
                          target_chapters=2, required_cast=["hero"]),
                  Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, title="E2", climax="c2",
                          target_chapters=2)]),
              Arc(arc_id="arc2", order=2, title="A2", goal="g2", episodes=[])])   # arc2: lazy


def test_cursor() -> bool:
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = _spine()
    p = NarrativeProgress()
    planner = ArcPlanner(Fake())
    ep1 = planner.current_episode(w, p, [])
    ok = ep1.episode_id == "arc1_ep1" and p.current_arc_id == "arc1"
    ep1.done = True
    ok &= planner.current_episode(w, p, []).episode_id == "arc1_ep2"   # 같은 아크 다음 에피소드
    w.spine.arc("arc1").episodes[1].done = True
    ep3 = planner.current_episode(w, p, [])                            # arc1 소진 → arc2(lazy gen)
    ok &= (p.current_arc_id == "arc2" and ep3 is not None and w.spine.arc("arc1").done
           and len(w.spine.arc("arc2").episodes) >= 1)                 # Fake 실패 → fallback 에피소드 보장
    print(f"[{'OK' if ok else 'FAIL'}] 커서: ep1→ep2→(arc1 done)→arc2 lazy 전진")
    return ok


def test_anchors_and_drift() -> bool:
    s = get_settings()
    sp = _spine()
    anchors = _arc_anchors(sp, sp.arcs[0], sp.arcs[0].episodes[0])
    ok = len(anchors) == 3 and all(a.source == "arc_anchor" for a in anchors)   # 엔딩+아크+에피소드, narrative
    w = WorldConfig(title="t", genre="x",
                    entities=[EntitySpec(id="hero", name="주인공"), EntitySpec(id="rival", name="라이벌")])
    o = build_engine(w, Fake(), s).ontology
    ep = Episode(episode_id="e", arc_id="a", order=1, target_chapters=2, required_cast=["hero"])
    s1 = episode_drift_signals(ep, ["라이벌이 등장했다."], o)                     # hero 미등장
    s2 = episode_drift_signals(ep, ["주인공 등장", "x", "y"], o)                   # hero 등장 + 3화>2 target
    ok &= any("cast_missing" in x for x in s1)
    ok &= (not any("cast_missing" in x for x in s2)) and any("pacing_overrun" in x for x in s2)
    print(f"[{'OK' if ok else 'FAIL'}] 앵커 3(narrative) + 드리프트(cast_missing/pacing_overrun)")
    return ok


def test_hier_summary() -> bool:
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = _spine()
    st = ProjectState(id="t", seed=ProjectSeed(premise="x"), world=w)
    st.narrative_progress.current_episode_id = "arc1_ep2"
    w.spine.arc("arc1").episodes[0].done = True
    w.spine.arc("arc1").episodes[0].summary = "EP1롤업"
    st.chapters = [ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, summary="s1", episode_id="arc1_ep1"),
                   ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, summary="s2", episode_id="arc1_ep1"),
                   ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, summary="s3", episode_id="arc1_ep2")]
    txt, dropped = _build_story_so_far_hier(st, 4, 6000)
    # 완료 에피소드(ep1)는 1줄 롤업으로 압축, 현재 에피소드(ep2) 회차는 상세
    ok = ("EP1롤업" in txt and "3화: s3" in txt and "1화: s1" not in txt and "2화: s2" not in txt)
    print(f"[{'OK' if ok else 'FAIL'}] 계층 요약: 완료EP 롤업 압축 + 현재EP 회차상세 (dropped={dropped})")
    return ok


def test_completion() -> bool:
    # 모든 에피소드 done → 무한 lazy-gen 금지, completed=True, None 반환
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, done=True),
            Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, done=True)])])
    p = NarrativeProgress(current_arc_id="arc1", current_episode_id="arc1_ep2")
    ep = ArcPlanner(Fake()).current_episode(w, p, [])
    ok = ep is None and p.completed is True
    print(f"[{'OK' if ok else 'FAIL'}] 완결 종료: 모든 에피소드 done → None + completed(무한생성 차단)")
    return ok


def test_escalated_rollback() -> bool:
    """ESCALATED 회차: 커서/spine 변이 롤백 + current_chapter 미전진(재시도 결정성). LLM 0콜(스텁)."""
    import tempfile
    from pathlib import Path
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    from novelcopilot.domain.world import Beat
    from novelcopilot.domain.types import ChapterRecord
    import novelcopilot.worldgen.arc_planner as apmod

    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1", target_chapters=3),
            Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, climax="c2", target_chapters=3)])])
    st = ProjectState(id="t", seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    svc.repo.save(st)
    sess, _ = svc.get_session("t")
    apmod.ArcPlanner.beat_for_episode = lambda self, world, arc, ep, ch, fin, rec, direc, plant_notes="": \
        Beat(chapter=ch, entities=["hero"], arc_id=ep.arc_id, episode_id=ep.episode_id)
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
        ChapterRecord(chapter=ch_no, status=ChapterStatus.ESCALATED, text="x")
    res = svc.generate_next_chapter("t")
    st2 = svc.get_project("t")
    ok = (res["record"].status == ChapterStatus.ESCALATED and st2.current_chapter == 0
          and st2.narrative_progress.current_episode_id is None        # 진입 시점(None)으로 롤백
          and st2.narrative_progress.completed is False
          and not any(e.done for a in st2.world.spine.arcs for e in a.episodes))  # 에피소드 done 영속 안 됨
    print(f"[{'OK' if ok else 'FAIL'}] ESCALATED 롤백: 커서/done 미영속·current_chapter 미전진")
    return ok


def test_generate_exception_rollback() -> bool:
    """generate 가 예외(LLM 타임아웃 등)를 던지면 커서 롤백 + 세션 evict → 오염 커서 재사용 차단. LLM 0콜."""
    import tempfile
    from pathlib import Path
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    from novelcopilot.domain.world import Beat
    import novelcopilot.worldgen.arc_planner as apmod

    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1", target_chapters=3)])])
    st = ProjectState(id="t", seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    svc.repo.save(st)
    sess, _ = svc.get_session("t")
    apmod.ArcPlanner.beat_for_episode = lambda self, world, arc, ep, ch, fin, rec, direc, plant_notes="": \
        Beat(chapter=ch, entities=["hero"], arc_id=ep.arc_id, episode_id=ep.episode_id)
    def _boom(*a, **k):
        raise RuntimeError("LLM timeout")
    sess.bundle.generator.generate = _boom
    raised = False
    try:
        svc.generate_next_chapter("t")
    except RuntimeError:
        raised = True
    st2 = svc.get_project("t")   # 디스크(미저장=클린) 재읽기
    ok = (raised
          and "t" not in svc.sessions._sessions               # 세션 evict 됨(오염 커서 폐기)
          and st2.current_chapter == 0                          # 미전진
          and st2.narrative_progress.current_episode_id is None
          and not any(e.done for a in st2.world.spine.arcs for e in a.episodes))
    print(f"[{'OK' if ok else 'FAIL'}] 예외 롤백: 커서 롤백·세션 evict·current_chapter 미전진(raised={raised})")
    return ok


def test_pacing_budget() -> bool:
    """페이싱 산술 정합 — n_arcs·_rebalance 가 목표 회차에 닿는가(조기완결/하드캡 회피). LLM 0콜.
    200화에서 share/arc 가 에피소드 천장(4×10)을 넘어 ~168화 조기완결되던 결함의 회귀 가드."""
    ok = True
    for target in (12, 50, 100, 150, 200, 300):
        n_arcs = max(2, min(8, round((target or 12) / 18)))      # build_spine 과 동일 공식
        sp = NarrativeSpine(arcs=[Arc(arc_id=f"arc{i+1}", order=i + 1, title="A") for i in range(n_arcs)])
        for ei in range(4):   # 첫 아크만 분해(build_spine 패턴)
            sp.arcs[0].episodes.append(Episode(episode_id=f"arc1_ep{ei+1}", arc_id="arc1",
                                               order=ei + 1, climax="c", target_chapters=5))
        ArcPlanner._rebalance(sp, target)
        share = max(2, round(target / n_arcs))
        first_sum = sum(e.target_chapters for e in sp.arcs[0].episodes)
        hit = abs(first_sum - share) <= max(2, share * 0.15)     # 천장 부족으로 인한 구조적 미달 없음
        ok &= hit
        print(f"   target={target:4} n_arcs={n_arcs} share={share:3} 첫아크합={first_sum:3} [{'OK' if hit else 'MISS'}]")
    print(f"[{'OK' if ok else 'FAIL'}] 페이싱 예산 정합(목표 회차 ≈ 아크 예산 합)")
    return ok


if __name__ == "__main__":
    results = [test_cursor(), test_anchors_and_drift(), test_hier_summary(),
               test_completion(), test_escalated_rollback(), test_generate_exception_rollback(),
               test_pacing_budget()]
    print("\nR4 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
