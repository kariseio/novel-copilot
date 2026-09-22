# -*- coding: utf-8 -*-
"""ST-12d 재실현 파이프라인 내장 — generate_next_chapter 발행 직후 자동 재실현 훅(전부 mock·실 LLM 0콜).

사용자 지시(2026-07-14): 문체 재실현을 opt-in API(POST …/rerender)로만 두지 말고 회차 생성 파이프라인에
상시 배선한다("문체 개선은 생성할 때 태워라"). no-harm 정독 게이트·사실 가드가 채택을 결정하므로 자동이어도
품질 역행이 구조적으로 차단된다.

잠그는 계약:
 ① ON + 대역 밖 → rerender_chapter 1회 호출(스텁 — 실 LLM 0콜).
 ② in-band(top_ratio ≤ 대역 상한·max_run ≤ 14) → rerender_chapter 호출 0 + skip(in_band) 이벤트.
 ③ OFF(rerender_in_pipeline=False) → rerender_chapter 호출 0(기존 경로 무접촉).
 ④ rerender_chapter 예외 → 회차 발행 상태 무영향(FINALIZED 유지) + failure 이벤트.
 ⑤ 순서 — 재실현 훅이 product_gate 훅보다 먼저(호출 로그로 검증).
 ⑥ 423(None 반환 — 락 경합) → skip(locked) 이벤트 기록.
 ⑦ RO-1(2026-07-15): 훅 라이프사이클(start/skip/failure/adopted/rejected)이 트레이스 사이드카에
    kind="rerender_pipeline" 로 영속 — sess.bus 는 인메모리 전용이고 gen-trace 이벤트 스냅샷은 훅 이전에
    저장되므로, 이 영속이 없으면 훅의 생사가 디스크에 안 남는다(라이브 침묵 실측의 교정). failure 는
    traceback 포함(rerender_chapter 가 자기 기록 전에 죽어도 사유가 남게).

계측(_kiwi_metrics_fn)은 per-test 스텁으로 대역 안/밖을 결정론 제어(Kiwi 설치 무관·실 계측 0).
실행: PYTHONPATH=app py -3.12 -m pytest tools/test_st12d_rerender_pipeline.py -q
"""
from __future__ import annotations
import sys
import pathlib
import tempfile
from pathlib import Path

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

import pytest

from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.base import LLMProvider
import novelcopilot.worldgen.arc_planner as apmod


# ─────────────────────────────────────────────────────────────────────────────
# 통합 픽스처(PR-1/PR-2 패턴 재사용) — LLM 0콜(Fake provider·stub generator/beat)
# ─────────────────────────────────────────────────────────────────────────────
class _Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _spine():
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1",
                    target_chapters=3, event_menu=["사건1"])])])


def _mk_svc(pid: str, *, rerender_in_pipeline=True, product_gate=False):
    tmp = tempfile.mkdtemp()
    s = get_settings().model_copy(update={"data_dir": tmp,
                                          "rerender_in_pipeline": rerender_in_pipeline,
                                          "product_gate": product_gate})
    svc = CopilotService(s, FilesystemProjectRepository(Path(tmp)))
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = _spine()
    st = ProjectState(id=pid, seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    svc.repo.save(st)
    sess, _ = svc.get_session(pid)
    sess.provider = _Fake()
    return svc, sess


def _stub_beat():
    orig = apmod.ArcPlanner.beat_for_episode
    apmod.ArcPlanner.beat_for_episode = lambda self, world, arc, ep, ch, fin, rec, direc, plant_notes="", **kw: \
        Beat(chapter=ch, entities=["hero"], arc_id=ep.arc_id, episode_id=ep.episode_id,
             closing_device="dialogue", hook_type="question", chapter_function="setup")
    return orig


def _gen_one(svc, sess, pid, *, text="본문 문장. " * 80):
    sess.bundle.updater.propose = lambda *a, **k: {}
    sess.bundle.updater.apply = lambda *a, **k: ([], [], [], [])
    orig = _stub_beat()
    try:
        sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
            ChapterRecord(chapter=ch_no, status=ChapterStatus.FINALIZED, text=text,
                          summary="s", closing_device="dialogue", hook_type="question",
                          chapter_function="setup")
        return svc.generate_next_chapter(pid)
    finally:
        apmod.ArcPlanner.beat_for_episode = orig


def _stub_kiwi(svc, *, top_ratio, max_run):
    """_kiwi_metrics_fn 을 대역 제어 스텁으로 — 실 Kiwi 계측 없이 in-band 여부를 결정론 고정."""
    svc._kiwi_metrics_fn = lambda: (lambda text: {"ending_profile": {"top_ratio": top_ratio,
                                                                     "max_run": max_run}})


def _rerender_events(sess):
    return [e for e in sess.bus.buffer if e.get("node") == "rerender_pipeline"]


# ─────────────────────────────────────────────────────────────────────────────
# ① ON + 대역 밖 → rerender_chapter 1회 호출(스텁)
# ─────────────────────────────────────────────────────────────────────────────
def test_on_out_of_band_calls_rerender_once():
    svc, sess = _mk_svc("d1", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.82, max_run=30)   # 대역 밖(단조 종결) → 재실현 실행
    calls = []
    svc.rerender_chapter = lambda pid, ch: (calls.append((pid, ch))
                                            or {"adopted": True, "reason": "대역 최근접 리랭크",
                                                "chapter": ch, "before_kiwi": {"top_ratio": 0.82},
                                                "after_kiwi": {"top_ratio": 0.45}})
    _gen_one(svc, sess, "d1")
    assert calls == [("d1", 1)]                    # 정확히 1회, 발행된 회차 번호로
    evs = _rerender_events(sess)
    assert any(e["event"] == "start" for e in evs)
    assert any(e["event"] == "adopted" and e.get("adopted") is True for e in evs)
    # 발행 회차는 그대로 FINALIZED(훅은 발행을 바꾸지 않음)
    assert svc.get_project("d1").chapters[-1].status == ChapterStatus.FINALIZED


# ─────────────────────────────────────────────────────────────────────────────
# ② in-band → 호출 0 + skip(in_band) 이벤트
# ─────────────────────────────────────────────────────────────────────────────
def test_in_band_skips_no_call():
    svc, sess = _mk_svc("d2", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.42, max_run=8)     # 대역 안(사람 글 범위) → 스킵(무비용)
    calls = []
    svc.rerender_chapter = lambda pid, ch: calls.append((pid, ch)) or {"adopted": True}
    _gen_one(svc, sess, "d2")
    assert calls == []                             # 재실현 미실행(옮길 게 없음)
    evs = _rerender_events(sess)
    assert any(e["event"] == "skip" and e.get("reason") == "in_band" for e in evs)
    assert not any(e["event"] == "start" for e in evs)


def test_in_band_boundary_upper_edge_still_skips():
    # 경계값(대역 상한 정확히) 포함 스킵 — top_ratio == 상한, max_run == 14.
    from novelcopilot.engine import rerender as rr
    svc, sess = _mk_svc("d2b", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=rr._TOP_RATIO_BAND[1], max_run=rr._MAX_RUN_BAND_HI)
    calls = []
    svc.rerender_chapter = lambda pid, ch: calls.append((pid, ch)) or {"adopted": True}
    _gen_one(svc, sess, "d2b")
    assert calls == []
    assert any(e["event"] == "skip" and e.get("reason") == "in_band" for e in _rerender_events(sess))


def test_unmeasurable_runs_conservatively():
    # 계측 불가(kiwi 부재 → None)면 in_band=False → 실행 쪽으로(보수 — 스킵 아님).
    svc, sess = _mk_svc("d2c", rerender_in_pipeline=True)
    svc._kiwi_metrics_fn = lambda: (lambda text: None)   # 부품 부재 모사
    calls = []
    svc.rerender_chapter = lambda pid, ch: calls.append((pid, ch)) or {"adopted": False, "reason": "x", "chapter": ch}
    _gen_one(svc, sess, "d2c")
    assert calls == [("d2c", 1)]                   # 결측이 스킵 근거가 되지 않는다(실행)


# ─────────────────────────────────────────────────────────────────────────────
# ③ OFF → 호출 0(기존 경로 무접촉)
# ─────────────────────────────────────────────────────────────────────────────
def test_off_no_call_no_event():
    svc, sess = _mk_svc("d3", rerender_in_pipeline=False)
    _stub_kiwi(svc, top_ratio=0.90, max_run=40)    # 대역 밖이어도 OFF 면 무접촉
    calls = []
    svc.rerender_chapter = lambda pid, ch: calls.append((pid, ch)) or {"adopted": True}
    _gen_one(svc, sess, "d3")
    assert calls == []
    assert _rerender_events(sess) == []            # 이벤트도 0(경로 무접촉)


def test_rr1_pipeline_default_is_off():
    """RR-1(2026-07-16): 재실현 파이프라인 상시 배선의 제품 *기본값*은 OFF(수동 도구로 강등).

    conftest 가 테스트 프로세스 env(NOVEL_RERENDER_IN_PIPELINE=0)를 고정하므로 인스턴스 값이 아니라
    Settings 클래스 필드 기본값을 직접 잠근다(env 무관 — 제품 기본이 OFF 임을 보증)."""
    from novelcopilot.config import Settings
    assert Settings.model_fields["rerender_in_pipeline"].default is False


# ─────────────────────────────────────────────────────────────────────────────
# ④ rerender 예외 → 발행 상태 무영향 + failure 이벤트
# ─────────────────────────────────────────────────────────────────────────────
def test_rerender_exception_isolated_from_publish():
    svc, sess = _mk_svc("d4", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.85, max_run=25)
    def _boom(pid, ch):
        raise RuntimeError("재실현 내부 폭발")
    svc.rerender_chapter = _boom
    res = _gen_one(svc, sess, "d4")               # 예외가 전파되면 이 줄에서 실패했을 것
    # 발행은 완전히 무영향 — 회차는 FINALIZED, 반환 페이로드도 정상.
    assert res["record"].status == ChapterStatus.FINALIZED
    assert svc.get_project("d4").chapters[-1].status == ChapterStatus.FINALIZED
    evs = _rerender_events(sess)
    assert any(e["event"] == "failure" for e in evs)


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ 순서 — 재실현 훅이 product_gate 훅보다 먼저(게이트는 재실현 반영 최종본 심사)
# ─────────────────────────────────────────────────────────────────────────────
def test_order_rerender_before_product_gate():
    svc, sess = _mk_svc("d5", rerender_in_pipeline=True, product_gate=True)
    _stub_kiwi(svc, top_ratio=0.80, max_run=22)
    log = []
    svc.rerender_chapter = lambda pid, ch: log.append("rerender") or {"adopted": True, "reason": "r",
                                                                       "chapter": ch}
    # product_gate 훅을 호출 로그만 남기는 스텁으로(실 게이트 judge·LLM 0콜)
    svc._apply_product_gate = lambda pid, chapter, sess_, result: log.append("product_gate")
    _gen_one(svc, sess, "d5")
    assert log == ["rerender", "product_gate"]    # 재실현이 먼저, 그다음 발행 게이트


# ─────────────────────────────────────────────────────────────────────────────
# ⑥ 423(None 반환 — 락 경합) → skip(locked) 기록
# ─────────────────────────────────────────────────────────────────────────────
def test_lock_contention_none_records_skip():
    svc, sess = _mk_svc("d6", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.88, max_run=30)
    calls = []
    svc.rerender_chapter = lambda pid, ch: calls.append((pid, ch)) or None   # 423 = 락 경합
    _gen_one(svc, sess, "d6")
    assert calls == [("d6", 1)]                    # 실행은 시도됨(대역 밖)
    evs = _rerender_events(sess)
    assert any(e["event"] == "skip" and e.get("reason") == "locked" for e in evs)
    assert not any(e["event"] in ("adopted", "rejected") for e in evs)


# ─────────────────────────────────────────────────────────────────────────────
# 미채택(rejected) 이벤트 — 재실현 실행됐으나 no-harm 게이트/가드가 채택 거부(adopted=False)
# ─────────────────────────────────────────────────────────────────────────────
def test_rejected_event_when_not_adopted():
    svc, sess = _mk_svc("d7", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.83, max_run=28)
    svc.rerender_chapter = lambda pid, ch: {"adopted": False, "reason": "정독 게이트: 정독 열세(원문 양순서 완승)",
                                            "chapter": ch, "before_kiwi": None, "after_kiwi": None}
    _gen_one(svc, sess, "d7")
    evs = _rerender_events(sess)
    assert any(e["event"] == "rejected" and e.get("adopted") is False for e in evs)
    # 원문 보호 — 회차 발행 무영향
    assert svc.get_project("d7").chapters[-1].status == ChapterStatus.FINALIZED


# ─────────────────────────────────────────────────────────────────────────────
# ⑦ RO-1 — 훅 라이프사이클이 트레이스 사이드카에 영속(버스는 인메모리라 이것만이 디스크 흔적)
# ─────────────────────────────────────────────────────────────────────────────
def _pipeline_trace_runs(svc, pid, chapter=1):
    doc = svc.repo.load_trace(pid, chapter) or {}
    return [r for r in (doc.get("runs") or []) if r.get("kind") == "rerender_pipeline"]


def test_ro1_lifecycle_persisted_start_and_outcome():
    svc, sess = _mk_svc("d8", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.82, max_run=30)
    svc.rerender_chapter = lambda pid, ch: {"adopted": True, "reason": "대역 최근접 리랭크", "chapter": ch}
    _gen_one(svc, sess, "d8")
    events = [r.get("event") for r in _pipeline_trace_runs(svc, "d8")]
    assert "start" in events and "adopted" in events     # start→outcome 짝(생사 판별 가능)


def test_ro1_failure_persisted_with_traceback():
    # 급소: rerender_chapter 가 자기 기록(첫 _save_rerender_trace) 전에 죽어도 사유가 디스크에 남는다.
    svc, sess = _mk_svc("d9", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.85, max_run=25)
    def _boom(pid, ch):
        raise RuntimeError("재실현 내부 폭발")
    svc.rerender_chapter = _boom
    _gen_one(svc, sess, "d9")
    runs = _pipeline_trace_runs(svc, "d9")
    fails = [r for r in runs if r.get("event") == "failure"]
    assert fails, "failure 라이프사이클이 사이드카에 없음(조용한 침묵 재발)"
    assert "재실현 내부 폭발" in (fails[0].get("error") or "")
    assert "RuntimeError" in (fails[0].get("traceback") or "")   # 사유 전문(원인 직독 가능)
    assert any(r.get("event") == "start" for r in runs)          # start 는 남고 outcome 이 failure


def test_ro1_skip_in_band_persisted():
    svc, sess = _mk_svc("d10", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.42, max_run=8)
    svc.rerender_chapter = lambda pid, ch: {"adopted": True}
    _gen_one(svc, sess, "d10")
    runs = _pipeline_trace_runs(svc, "d10")
    assert any(r.get("event") == "skip" and r.get("reason") == "in_band" for r in runs)


def test_ro1_skip_locked_persisted():
    svc, sess = _mk_svc("d11", rerender_in_pipeline=True)
    _stub_kiwi(svc, top_ratio=0.88, max_run=30)
    svc.rerender_chapter = lambda pid, ch: None          # 423 = 락 경합
    _gen_one(svc, sess, "d11")
    runs = _pipeline_trace_runs(svc, "d11")
    assert any(r.get("event") == "skip" and r.get("reason") == "locked" for r in runs)


def test_ro1_off_leaves_no_trace_records():
    # OFF 무접촉 계약은 사이드카에도 동일 적용(경로 자체가 안 돈다).
    svc, sess = _mk_svc("d12", rerender_in_pipeline=False)
    _stub_kiwi(svc, top_ratio=0.90, max_run=40)
    svc.rerender_chapter = lambda pid, ch: {"adopted": True}
    _gen_one(svc, sess, "d12")
    assert _pipeline_trace_runs(svc, "d12") == []
