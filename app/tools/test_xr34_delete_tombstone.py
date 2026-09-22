# -*- coding: utf-8 -*-
"""XR-34 검증 — 프로젝트 삭제 tombstone·writer 직렬화 (실 LLM 0콜).

계약(cross-review/025 §4~5):
  · 삭제는 안정 프로젝트 락과 직렬화 — 진행 중 writer 면 ProjectBusyError(라우트 423 계약).
  · 삭제 성공 뒤 발급돼 있던 상태 스냅샷의 save 는 sink(repo)에서 거부 — 부활 불가.
  · 대기 후 loader 부재(=삭제)·tombstone pid 는 세션을 발급하지 않는다(유령 세션 차단).
  · 사이드카(rag·trace·cover) 동반 삭제 무회귀 + 삭제 후 trace 고아 생성 방지.
  · 검수 분모 교훈(§3): 가드는 호출자 열거가 아니라 변이 sink 자체에 있다.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr34_delete_tombstone.py
"""
from __future__ import annotations
import sys
import json
import pathlib
import tempfile
import threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from types import SimpleNamespace as NS
from pathlib import Path

from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.repository.base import ProjectDeletedError
from novelcopilot.services.session import ProjectBusyError
from test_xr7_stale_derivatives import _ch, _svc


def test_delete_vs_active_writer_no_resurrection() -> bool:
    """025 §1 의 역: writer 가 보유한 상태 스냅샷의 사후 save 가 sink 에서 거부 — 부활 불가."""
    svc, sess, _prov = _svc([_ch(chapter=1, text="본문.")])
    held = svc.repo.get("p1")                      # writer 가 이미 발급받은 상태 스냅샷
    ok = svc.delete_project("p1") is True
    ok &= svc.repo.get("p1") is None
    held.world.synopsis = "writer-after-delete"
    try:
        svc.repo.save(held)                        # 진행 중 writer 의 사후 저장 시도
        ok = False
    except ProjectDeletedError:
        pass
    ok &= svc.repo.get("p1") is None               # 부활 0(025 재현의 역)
    print(f"[{'OK' if ok else 'FAIL'}] 삭제 vs writer: 사후 save 는 sink 거부 — 부활 불가")
    assert ok
    return ok


def _real_manager(loader=None):
    import novelcopilot.services.session as S

    class _DummySession:
        def __init__(self, pid, world, provider, settings):
            self.project_id, self.world, self.lock = pid, world, threading.Lock()

        def rehydrate(self, state):
            pass
    orig = (S.EngineSession, S.create_provider)
    S.EngineSession, S.create_provider = _DummySession, lambda settings: NS()
    from novelcopilot.config import get_settings
    return S, orig, S.SessionManager(get_settings(), state_loader=loader)


def test_delete_vs_generation_423_contract() -> bool:
    """025 §4.1: 진행 중 writer(락 보유) → ProjectBusyError(423 계약). 종료 후 삭제 성공."""
    S, orig, mgr = _real_manager()
    try:
        st = NS(id="p1", world=NS())
        sess = mgr.get_or_create(st)
        sess.lock.acquire()                        # 생성 등 writer 진행 중 시뮬
        calls = {"n": 0}

        def _repo_delete():
            calls["n"] += 1
            return True
        try:
            mgr.delete_project("p1", _repo_delete)
            ok = False
        except ProjectBusyError:
            ok = calls["n"] == 0                   # 거부 시 레포 삭제 미실행(부분 삭제 0)
        sess.lock.release()
        ok &= mgr.delete_project("p1", _repo_delete) is True and calls["n"] == 1
        print(f"[{'OK' if ok else 'FAIL'}] 삭제 vs 생성: 락 보유 중 423 거부(부분 삭제 0)·종료 후 성공")
        assert ok
        return ok
    finally:
        S.EngineSession, S.create_provider = orig


def test_stale_waiter_and_phantom_current() -> bool:
    """025 §2 의 역: pending 대기 중 삭제(loader None) → 세션 미발급·유령 current 없음."""
    loaded = {"live": True}
    S, orig, mgr = _real_manager(loader=lambda pid: NS(id=pid, world=NS()) if loaded["live"] else None)
    try:
        stale = NS(id="p2", world=NS())
        s1 = mgr.get_or_create(stale)
        s1.lock.acquire()                          # writer 진행 중
        mgr.evict("p2")                            # pending 기록
        loaded["live"] = False                     # 대기 중 삭제됨(정본 소멸)
        result = {}

        def _waiter():
            try:
                result["sess"] = mgr.get_or_create(stale)
            except KeyError:
                result["err"] = True
        t = threading.Thread(target=_waiter)
        t.start()
        import time
        time.sleep(0.2)
        s1.lock.release()
        t.join(timeout=3)
        ok = result.get("err") is True and "sess" not in result   # stale 폴백 없이 정직 실패
        ok &= mgr.current("p2") is None                            # 유령 current 없음
        # tombstone 경로도 동일 계약: delete_project 후 재발급 금지
        mgr.delete_project("p2", lambda: True)
        try:
            mgr.get_or_create(stale)
            ok = False
        except KeyError:
            pass
        ok &= mgr.current("p2") is None
        print(f"[{'OK' if ok else 'FAIL'}] 대기 중 삭제: 세션 미발급(KeyError)·유령 current 0·tombstone 재발급 금지")
        assert ok
        return ok
    finally:
        S.EngineSession, S.create_provider = orig


def test_sidecar_deletion_and_no_orphan_trace() -> bool:
    """기존 사이드카 동반 삭제 계약 무회귀 + 삭제 후 trace 고아 생성 방지(비차단 skip)."""
    tmp = Path(tempfile.mkdtemp(prefix="xr34repo_"))
    repo = FilesystemProjectRepository(tmp)
    d = repo.dir
    (d / "px.json").write_text('{"id":"px"}', encoding="utf-8")
    (d / "px.rag.1.json").write_text("{}", encoding="utf-8")
    (d / "px.trace.1.json").write_text("{}", encoding="utf-8")
    (d / "px.cover.png").write_bytes(b"png")
    ok = repo.delete("px") is True
    ok &= not any(d.glob("px*"))                                   # 본체+rag+trace+cover 전량 삭제(무회귀)
    repo.save_trace("px", 2, {"kind": "gen"})                      # 삭제 후 trace 는 skip(고아 0·비차단)
    ok &= not (d / "px.trace.2.json").exists()
    ok &= repo.delete("px") is False                               # 재삭제=False(멱등·무예외)
    print(f"[{'OK' if ok else 'FAIL'}] 사이드카 전량 삭제 무회귀 · 삭제 후 trace 고아 0 · 재삭제 멱등")
    assert ok
    return ok


def test_sink_guard_is_at_repository_layer() -> bool:
    """§3 검수 분모 교훈: 가드는 호출자가 아니라 sink — repo 를 직접 쥔 어떤 코드가 save 해도 거부된다."""
    tmp = Path(tempfile.mkdtemp(prefix="xr34sink_"))
    repo = FilesystemProjectRepository(tmp)
    from novelcopilot.domain.project import ProjectSeed, ProjectState
    from novelcopilot.domain.world import WorldConfig
    st = ProjectState(id="py", seed=ProjectSeed(title="t"), world=WorldConfig(title="t", synopsis="s"))
    repo.save(st)
    ok = repo.get("py") is not None
    repo.delete("py")
    try:
        repo.save(st)                                              # 서비스 층 우회 저장 시도(임의 호출자)
        ok = False
    except ProjectDeletedError:
        pass
    ok &= repo.get("py") is None
    st2 = ProjectState(id="pz", seed=ProjectSeed(title="t"), world=WorldConfig(title="t", synopsis="s"))
    repo.save(st2)                                                 # 무관 pid 는 영향 0
    ok &= repo.get("pz") is not None
    print(f"[{'OK' if ok else 'FAIL'}] sink 가드: 호출자 무관 save 거부 · 무관 pid 무영향")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_delete_vs_active_writer_no_resurrection(),
               test_delete_vs_generation_423_contract(),
               test_stale_waiter_and_phantom_current(),
               test_sidecar_deletion_and_no_orphan_trace(),
               test_sink_guard_is_at_repository_layer()]
    print("\nXR-34 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
