# -*- coding: utf-8 -*-
"""B-25 검증 — 회차 생성 부분실패(본문 LLM 타임아웃 등) 시 커서↔회차 트랜잭션 정합 (LLM 0콜).

실측 재구성(무협: current_chapter=1, chapters=2):
  본문 LLM 타임아웃/장애가 ①하드예외로 오면 기존 예외 롤백이 디스크를 클린으로 지켰지만,
  ②'빈/부분 본문·하드위반 비수렴'으로 강등되면 harness 가 ESCALATED 레코드를 반환하고,
  구 코드는 이를 state.chapters 에 append+save 하면서 current_chapter 는 전진시키지 않았다
  → 디스크에 current(N)↔chapters(N+1) 반쪽 커밋이 영속(LR 러너가 측정하는 커서 불변식 붕괴).
수정 계약: 회차 append 는 커서 전진(FINALIZED)과 '한 단위'로만 커밋되고,
  비FINALIZED 는 spine/커서 롤백과 함께 chapters 에도 남지 않는다(함께 롤백 — 페이로드 가시화는 유지).
실행: PYTHONPATH=app python tools/test_b25_cursor_txn.py
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.base import LLMProvider
import novelcopilot.worldgen.arc_planner as apmod
from novelcopilot.worldgen import BeatPlanner


class Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _spine() -> NarrativeSpine:
    # event_menu 사전 적재 — T3 활성 콜(LLM) 자체를 우회해 테스트 LLM 0콜 보장
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1",
                    target_chapters=3, event_menu=["사건1"]),
            Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, climax="c2",
                    target_chapters=3, event_menu=["사건2"])])])


def _mk_svc(pid: str, with_spine: bool = True, chapters=None, current: int = 0,
            progress: dict | None = None):
    """임시 디스크 repo + 라이브 data_dir 미오염(settings.data_dir → tmp: pregen 스냅샷도 tmp 에)."""
    tmp = tempfile.mkdtemp()
    s = get_settings().model_copy(update={"data_dir": tmp})
    svc = CopilotService(s, FilesystemProjectRepository(Path(tmp)))
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    if with_spine:
        w.spine = _spine()
    st = ProjectState(id=pid, seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    st.chapters = list(chapters or [])
    st.current_chapter = current
    for k, v in (progress or {}).items():
        setattr(st.narrative_progress, k, v)
    svc.repo.save(st)
    sess, _ = svc.get_session(pid)
    sess.provider = Fake()          # copilot 이 직접 쓰는 provider(usage·repeat·정산·독자데스크) LLM 0콜화
    return svc, sess


def _stub_beat():
    """ArcPlanner.beat_for_episode 클래스 패치 — 반드시 finally 로 복원(스위트 오염 방지)."""
    orig = apmod.ArcPlanner.beat_for_episode
    apmod.ArcPlanner.beat_for_episode = lambda self, world, arc, ep, ch, fin, rec, direc, plant_notes="", **kw: \
        Beat(chapter=ch, entities=["hero"], arc_id=ep.arc_id, episode_id=ep.episode_id)
    return orig


def _esc_record(ch_no, **_):
    """본문 LLM 타임아웃 강등 재구성 — 재시도 소진 후 빈 본문 → harness 의 ESCALATED(empty_generation)."""
    return ChapterRecord(chapter=ch_no, status=ChapterStatus.ESCALATED, text="",
                         recovery_hints=[{"kind": "empty_generation",
                                          "diagnosis": "본문이 비어 있습니다(타임아웃/거부 추정)."}])


def _cursor_invariant(st) -> bool:
    """LR 러너가 측정하는 불변식: current == 회차 수 == 마지막 회차 번호, 전 회차 FINALIZED."""
    return (st.current_chapter == len(st.chapters)
            == max((c.chapter for c in st.chapters), default=0)
            and all(c.status == ChapterStatus.FINALIZED for c in st.chapters))


def test_escalated_rolls_back_append_with_cursor() -> None:
    """②핵심: ESCALATED(타임아웃 강등) → 회차 append 도 커서와 '함께' 롤백(반쪽 커밋 금지)."""
    svc, sess = _mk_svc("b25a")
    orig = _stub_beat()
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: _esc_record(ch_no)
    try:
        res = svc.generate_next_chapter("b25a")
    finally:
        apmod.ArcPlanner.beat_for_episode = orig
    st2 = svc.get_project("b25a")   # 디스크 권위 재읽기
    ok = (res["record"].status == ChapterStatus.ESCALATED          # 페이로드 가시화 유지(레코드+회복안내)
          and bool(res["record"].recovery_hints)
          and res["current_chapter"] == 0
          and st2.current_chapter == 0
          and len(st2.chapters) == 0                               # append 도 함께 롤백(디스크 미영속)
          and _cursor_invariant(st2))
    print(f"[{'OK' if ok else 'FAIL'}] ESCALATED 함께 롤백: 커서 0·chapters 0·페이로드 가시화 유지")
    assert ok, "ESCALATED 가 커서와 '함께' 롤백되지 않음(반쪽 커밋 잔존 또는 페이로드 가시화 상실)"


def test_finalized_commits_append_with_cursor() -> None:
    """②쌍대: ESCALATED 재시도 후 FINALIZED → append+커서 전진이 '함께' 커밋(불변식 성립)."""
    svc, sess = _mk_svc("b25b")
    sess.bundle.updater.propose = lambda *a, **k: {}
    sess.bundle.updater.apply = lambda *a, **k: ([], [], [], [])
    orig = _stub_beat()
    try:
        sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: _esc_record(ch_no)
        svc.generate_next_chapter("b25b")                          # 1차: 타임아웃 강등(ESCALATED)
        sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
            ChapterRecord(chapter=ch_no, status=ChapterStatus.FINALIZED, text="본문", summary="s")
        res = svc.generate_next_chapter("b25b")                    # 2차: 같은 next_ch 재시도 → 성공
    finally:
        apmod.ArcPlanner.beat_for_episode = orig
    st2 = svc.get_project("b25b")
    ok = (res["record"].status == ChapterStatus.FINALIZED
          and res["record"].chapter == 1                           # 재시도가 '같은 회차'를 다시 씀(구멍 없음)
          and st2.current_chapter == 1 and len(st2.chapters) == 1
          and st2.narrative_progress.chapters_in_episode == 1      # 에피소드 커서도 정상 전진
          and _cursor_invariant(st2))
    print(f"[{'OK' if ok else 'FAIL'}] FINALIZED 함께 커밋: 재시도→같은 회차 성공·불변식 성립")
    assert ok, "FINALIZED 재시도가 append+커서 전진을 '함께' 커밋하지 못함(구멍 또는 불변식 붕괴)"


def test_legacy_escalated_tail_selfheal() -> None:
    """자가치유: 이 수정 전에 영속된 무협 실측 형상(current=1, chapters=[1 FIN, 2 ESC])이
    다음 생성(또 ESCALATED)에서 정합으로 수렴 — FINALIZED 발행본은 불변."""
    legacy = [ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="1화 본문", summary="s1",
                            episode_id="arc1_ep1", arc_id="arc1"),
              ChapterRecord(chapter=2, status=ChapterStatus.ESCALATED, text="구 잔재")]
    svc, sess = _mk_svc("b25c", chapters=legacy, current=1,
                        progress={"current_arc_id": "arc1", "current_episode_id": "arc1_ep1",
                                  "chapters_in_episode": 1})
    orig = _stub_beat()
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: _esc_record(ch_no)
    try:
        res = svc.generate_next_chapter("b25c")
    finally:
        apmod.ArcPlanner.beat_for_episode = orig
    st2 = svc.get_project("b25c")
    ok = (res["record"].chapter == 2 and res["record"].status == ChapterStatus.ESCALATED
          and st2.current_chapter == 1
          and [c.chapter for c in st2.chapters] == [1]             # 구 ESCALATED 꼬리 제거·1화(발행본) 보존
          and st2.chapters[0].text == "1화 본문"
          and _cursor_invariant(st2))
    print(f"[{'OK' if ok else 'FAIL'}] 레거시 자가치유: 커서-불일치 꼬리 제거·발행본 불변·불변식 회복")
    assert ok, "레거시 ESCALATED 꼬리 자가치유 실패(꼬리 잔존·발행본 훼손·불변식 미회복 중 하나)"


def test_flat_mode_escalated_no_persist() -> None:
    """평면 모드(spine 없음 — 하위호환 경로)에서도 비FINALIZED 는 영속 안 됨(spine 분기 밖 공통 계약)."""
    svc, sess = _mk_svc("b25d", with_spine=False)
    orig = BeatPlanner.beat_for
    BeatPlanner.beat_for = lambda self, world, ch, summaries, directives: \
        Beat(chapter=ch, entities=["hero"], title="t", summary="s")
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: _esc_record(ch_no)
    try:
        res = svc.generate_next_chapter("b25d")
    finally:
        BeatPlanner.beat_for = orig
    st2 = svc.get_project("b25d")
    ok = (res["record"].status == ChapterStatus.ESCALATED
          and st2.current_chapter == 0 and len(st2.chapters) == 0
          and _cursor_invariant(st2))
    print(f"[{'OK' if ok else 'FAIL'}] 평면 모드 ESCALATED: chapters 미영속·커서 정합")
    assert ok, "평면 모드(spine 없음)에서 비FINALIZED 가 영속됨(공통 계약 위반)"


def test_post_advance_timeout_rolls_back_cursor() -> None:
    """①재구성(예외형): FINALIZED 블록 '중간'(커서 전진 후) 후속 단계 타임아웃 → 커서·회차 함께 롤백,
    디스크 클린(save 미도달)·세션 evict. reconcile 정산 콜 자리에 TimeoutError 주입."""
    import novelcopilot.engine.ledger_ops as lo
    svc, sess = _mk_svc("b25e")
    sess.bundle.updater.propose = lambda *a, **k: {}
    sess.bundle.updater.apply = lambda *a, **k: ([], [], [], [])
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
        ChapterRecord(chapter=ch_no, status=ChapterStatus.FINALIZED, text="본문", summary="s")
    orig_beat = _stub_beat()
    orig_rec = lo.reconcile_ledger_from_prose
    def _boom(*a, **k):
        raise TimeoutError("LLM timeout")
    lo.reconcile_ledger_from_prose = _boom
    raised = False
    try:
        try:
            svc.generate_next_chapter("b25e")
        except TimeoutError:
            raised = True
    finally:
        lo.reconcile_ledger_from_prose = orig_rec
        apmod.ArcPlanner.beat_for_episode = orig_beat
    st2 = svc.get_project("b25e")
    ok = (raised
          and "b25e" not in svc.sessions._sessions                 # 오염 커서 세션 폐기
          and st2.current_chapter == 0 and len(st2.chapters) == 0  # 디스크 클린(반쪽 전진 없음)
          and st2.narrative_progress.current_episode_id is None
          and _cursor_invariant(st2))
    print(f"[{'OK' if ok else 'FAIL'}] 커서 전진 후 타임아웃: 함께 롤백·디스크 클린·세션 evict(raised={raised})")
    assert ok, "커서 전진 후 후속 단계 타임아웃 시 디스크 클린·세션 evict 계약 위반"


def _run(fn) -> bool:
    """스크립트 모드 전용 — assert 실패를 잡아 5건 전부 끝까지 보고(pytest 게이트는 assert 로 strict 판정)."""
    try:
        fn()
        return True
    except AssertionError:
        return False


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows cp949 콘솔에서 ✅ 출력 보호(pytest 경로 무관)
    except Exception:
        pass
    results = [_run(t) for t in (test_escalated_rolls_back_append_with_cursor,
                                 test_finalized_commits_append_with_cursor,
                                 test_legacy_escalated_tail_selfheal,
                                 test_flat_mode_escalated_no_persist,
                                 test_post_advance_timeout_rolls_back_cursor)]
    print("\nB-25 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
