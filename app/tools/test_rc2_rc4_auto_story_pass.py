# -*- coding: utf-8 -*-
"""RC-2 스토리패스 자동 실행 순서 수리 + RC-4 작가 판본 보호 회귀. LLM 0·결정론(run_funnel·provider 스텁).

RC-2:
  ⓐ 진단(직접): 에피소드 경계에서 '소진 커서'로 assemble → StoryPassNotReady, '전진 커서'로는 성공.
  ⓑ 배선(통합): auto 가 커서 전진(current_episode)·메뉴 생성 '뒤'에 돌아 새 에피소드 재료로 조립·확정.
  ⓒ 산출 독립 영속: 확정 뒤 후속 생성이 실패해도 깔때기 산출(레코드)이 디스크에 보존.
  ⓓ 실패 영속: 재료 부재/예외 시 StoryPassRecord(status='failed'·사유)로 영속(emit 휘발 금지).
RC-4:
  ⓔ 작가 수동 candidate 존재 시 깔때기 재실행 0 — variants 승자 선정만 재사용해 확정(작가 재료 보존).
  ⓕ auto 산 레코드·discarded 는 재사용 대상 아님(재생성 경로는 fresh funnel).
"""
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec, GenreContract
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus, StoryPassRecord
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.base import LLMProvider
from novelcopilot.engine import story_pass as SPmod
from novelcopilot.engine.story_pass import assemble_materials, StoryPassNotReady
import novelcopilot.services.copilot as C

_VOCAB = "생활어. 주인공의 도구 어휘(손거울·결박줄·망토·방울·구슬·봉인지)는 실물 여섯 점이다."


class Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def chat_json(self, *a, **k): return {}
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _boundary_world() -> WorldConfig:
    """arc1: ep1(target=2·done) → ep2(target=2). 경계 = ep1 finale 직후(커서 아직 ep1)."""
    w = WorldConfig(title="t", genre="x", synopsis="평범한 하루가 흔들린다.",
                    entities=[EntitySpec(id="hero", name="주인공")],
                    genre_contract=GenreContract(vocabulary_tone=_VOCAB))
    w.spine = NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1",
                    target_chapters=2, done=True, event_menu=["사건1"]),
            Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, climax="c2",
                    target_chapters=2, event_menu=["사건2"])])])
    return w


def _boundary_state(pid: str) -> ProjectState:
    st = ProjectState(id=pid, seed=ProjectSeed(target_chapters=6), world=_boundary_world(), created_at="t")
    st.chapters = [ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="1화", summary="s1",
                                 detail_synopsis="1화 요지", arc_id="arc1", episode_id="arc1_ep1"),
                   ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="2화", summary="s2",
                                 detail_synopsis="2화 요지", arc_id="arc1", episode_id="arc1_ep1")]
    st.current_chapter = 2
    st.narrative_progress.current_arc_id = "arc1"
    st.narrative_progress.current_episode_id = "arc1_ep1"
    st.narrative_progress.chapters_in_episode = 2
    return st


# ── RC-2 ⓐ 진단(직접 assemble — 순서 이동의 근거) ─────────────────────────────
def test_assemble_boundary_exhausted_raises_advanced_succeeds():
    st = _boundary_state("dx")
    # 소진 커서(ep1·cie=2, target=2) → slot=3 > target → StoryPassNotReady(순서 버그 재현)
    try:
        assemble_materials(st, chapter_no=3)
        assert False, "소진 커서에서 StoryPassNotReady 미발생(버그 재현 실패)"
    except StoryPassNotReady as e:
        assert "소진" in str(e)
    # 전진 커서(ep2·cie=0) → 성공(수리 후 auto 가 보는 상태)
    st.narrative_progress.current_episode_id = "arc1_ep2"
    st.narrative_progress.chapters_in_episode = 0
    mat = assemble_materials(st, chapter_no=3)
    assert mat["episode_id"] == "arc1_ep2" and mat["slot"] == 1 and mat["menu"] == ["사건2"]


# ── 통합 스텁 서비스 ──────────────────────────────────────────────────────────
def _mk_svc(pid: str):
    tmp = Path(tempfile.mkdtemp(prefix="rc2_"))
    s = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(s, FilesystemProjectRepository(tmp))
    svc._planning_provider = Fake()          # _beat_from_story_labels·generate_event_menu LLM 0
    st = _boundary_state(pid)
    svc.repo.save(st)
    sess, _ = svc.get_session(pid)
    sess.provider = Fake()
    sess.bundle.updater.propose = lambda *a, **k: {}
    sess.bundle.updater.apply = lambda *a, **k: ([], [], [], [])
    return svc, sess


def _stub_funnel(seen: dict, story: str):
    def stub(deps, st, chapter=None, **kw):
        seen["ep"] = st.narrative_progress.current_episode_id
        seen["cie"] = st.narrative_progress.chapters_in_episode
        return {"materials": {"chapter_no": chapter, "digest": "d" * 12, "syn1": "", "syn_prev": "",
                              "hygiene": {}},
                "candidates": {}, "finals": {}, "reviews": {}, "audits": {}, "pairwise": {},
                "pillar": "E1", "grafts": [], "role": "조사", "events": {},
                "variants": [{"name": "solo", "story": story, "fmt_errs": [],
                              "line_budget": {"lo": 12, "hi": 20}}]}
    return stub


# ── RC-2 ⓑ 배선(통합) — auto 가 전진 커서·메뉴 뒤에서 확정 ────────────────────
def test_auto_runs_after_cursor_advance_and_confirms():
    svc, sess = _mk_svc("rc2b")
    seen = {}
    story = "\n".join(f"- 사건 {i}이 벌어지고 그가 다음 수를 고른다." for i in range(13))
    orig_rf, orig_crp = SPmod.run_funnel, C.create_role_provider
    SPmod.run_funnel = _stub_funnel(seen, story)
    C.create_role_provider = lambda s, m: sess.provider
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
        ChapterRecord(chapter=ch_no, status=ChapterStatus.FINALIZED, text="본문", summary="s")
    try:
        res = svc.generate_next_chapter("rc2b")
    finally:
        SPmod.run_funnel, C.create_role_provider = orig_rf, orig_crp
    st2 = svc.get_project("rc2b")
    rec = next((r for r in st2.story_passes if r.chapter == 3), None)
    assert seen.get("ep") == "arc1_ep2" and seen.get("cie") == 0   # 전진 커서(새 에피소드)로 조립
    assert res["record"].status == ChapterStatus.FINALIZED and st2.current_chapter == 3
    assert rec is not None and rec.status == "confirmed" and rec.source == "auto"
    assert rec.confirmed_story == story and rec.checks.get("auto_variant") == "solo"


# ── RC-2 ⓒ 산출 독립 영속 — 후속 생성 실패에도 레코드 보존 ────────────────────
def test_funnel_output_survives_generation_failure():
    svc, sess = _mk_svc("rc2c")
    seen = {}
    story = "\n".join(f"- 사건 {i}이 벌어진다." for i in range(13))
    orig_rf, orig_crp = SPmod.run_funnel, C.create_role_provider
    SPmod.run_funnel = _stub_funnel(seen, story)
    C.create_role_provider = lambda s, m: sess.provider

    def _boom(*a, **k):
        raise RuntimeError("본문 생성 실패(타임아웃 시뮬)")
    sess.bundle.generator.generate = _boom
    raised = False
    try:
        try:
            svc.generate_next_chapter("rc2c")
        except RuntimeError:
            raised = True
    finally:
        SPmod.run_funnel, C.create_role_provider = orig_rf, orig_crp
    st2 = svc.get_project("rc2c")   # 디스크 재읽기(_fail_rollback 후)
    rec = next((r for r in st2.story_passes if r.chapter == 3), None)
    assert raised
    # 깔때기 산출(확정 레코드)은 회차 커밋과 독립으로 디스크에 살아남는다(증발 방지)
    assert rec is not None and rec.status == "confirmed" and rec.confirmed_story == story
    # B-25: 회차·커서는 함께 롤백(반쪽 커밋 없음) — 전진 커서를 append 없이 남기지 않음
    assert st2.current_chapter == 2 and [c.chapter for c in st2.chapters] == [1, 2]
    assert st2.narrative_progress.current_episode_id == "arc1_ep1"


# ── RC-2 ⓓ 실패 영속 — 재료 부재 시 status='failed' 레코드 ────────────────────
def _mk_direct_svc(pid: str, story_passes=None):
    tmp = Path(tempfile.mkdtemp(prefix="rc4_"))
    s = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(s, FilesystemProjectRepository(tmp))
    st = ProjectState(id=pid, seed=ProjectSeed(), world=WorldConfig(title="t"))
    st.story_passes = list(story_passes or [])
    svc.repo.save(st)
    sess = SimpleNamespace(provider=Fake(), bus=SimpleNamespace(emit=lambda *a, **k: None))
    return svc, st, sess


def test_failed_persists_record_not_just_emit():
    svc, st, sess = _mk_direct_svc("rc2d")
    orig_rf, orig_crp = SPmod.run_funnel, C.create_role_provider
    def _raise(deps, s, chapter=None, **kw):
        raise StoryPassNotReady("도구 정본을 찾지 못함 — 캐논 확인 필요")
    SPmod.run_funnel = _raise
    C.create_role_provider = lambda s, m: sess.provider
    try:
        out = svc._auto_story_pass_locked("rc2d", st, sess, 5)
    finally:
        SPmod.run_funnel, C.create_role_provider = orig_rf, orig_crp
    rec = next((r for r in st.story_passes if r.chapter == 5), None)
    assert out is None
    assert rec is not None and rec.status == "failed"
    assert rec.checks["auto_failure"]["event"] == "auto_not_ready"
    assert "도구 정본" in rec.checks["auto_failure"]["reason"]
    # 실패 레코드도 디스크 영속(emit 휘발 금지)
    rec2 = next((r for r in svc.repo.get("rc2d").story_passes if r.chapter == 5), None)
    assert rec2 is not None and rec2.status == "failed"


# ── RC-4 ⓔ 작가 candidate 재사용 — 깔때기 0콜·재료 보존 ───────────────────────
def test_candidate_reuse_zero_funnel_calls():
    author_variants = [{"name": "solo", "story": "작가 초안 스토리", "fmt_errs": [],
                        "line_budget": {"lo": 12, "hi": 20}},
                       {"name": "merged", "story": "작가 병합안", "fmt_errs": []}]
    cand = StoryPassRecord(chapter=7, status="candidate", source="",
                           variants=author_variants, checks={"role": "회수"})
    svc, st, sess = _mk_direct_svc("rc4a", story_passes=[cand])
    orig_rf, orig_crp = SPmod.run_funnel, C.create_role_provider
    def _must_not_run(*a, **k):
        raise AssertionError("RC-4 위반: candidate 존재 시 깔때기가 재실행됨")
    SPmod.run_funnel = _must_not_run
    C.create_role_provider = lambda s, m: sess.provider
    try:
        out = svc._auto_story_pass_locked("rc4a", st, sess, 7)
    finally:
        SPmod.run_funnel, C.create_role_provider = orig_rf, orig_crp
    rec = next(r for r in st.story_passes if r.chapter == 7)
    assert out == "작가 병합안"                              # merged(fmt 통과) 우선 선정
    assert rec.status == "confirmed" and rec.source == "auto"
    assert rec.checks.get("auto_reused_candidate") is True
    assert len(rec.variants) == 2                            # 작가 재료(variants) 보존
    assert next(v for v in rec.variants if v["name"] == "solo")["story"] == "작가 초안 스토리"


# ── RC-4 ⓕ auto 산 레코드·discarded 는 재사용 대상 아님(fresh funnel) ─────────
def test_auto_and_discarded_records_run_fresh_funnel():
    story = "\n".join(f"- 사건 {i}." for i in range(13))
    for src, status in (("auto", "candidate"), ("", "discarded")):
        rec0 = StoryPassRecord(chapter=9, status=status, source=src,
                               variants=[{"name": "solo", "story": "구 판본", "fmt_errs": []}])
        svc, st, sess = _mk_direct_svc(f"rc4b_{status}", story_passes=[rec0])
        ran = {"funnel": False}
        def _stub(deps, s, chapter=None, **kw):
            ran["funnel"] = True
            return {"materials": {"chapter_no": chapter, "digest": "d" * 12, "syn1": "", "syn_prev": "",
                                  "hygiene": {}},
                    "candidates": {}, "finals": {}, "reviews": {}, "audits": {}, "pairwise": {},
                    "pillar": "E1", "grafts": [], "role": "조사", "events": {},
                    "variants": [{"name": "solo", "story": story, "fmt_errs": [],
                                  "line_budget": {"lo": 12, "hi": 20}}]}
        orig_rf, orig_crp = SPmod.run_funnel, C.create_role_provider
        SPmod.run_funnel = _stub
        C.create_role_provider = lambda s, m: sess.provider
        try:
            out = svc._auto_story_pass_locked(f"rc4b_{status}", st, sess, 9)
        finally:
            SPmod.run_funnel, C.create_role_provider = orig_rf, orig_crp
        rec = next(r for r in st.story_passes if r.chapter == 9)
        assert ran["funnel"] is True, f"{src}/{status}: 재사용돼선 안 되는데 깔때기 미실행"
        assert out == story and rec.status == "confirmed" and rec.confirmed_story == story
