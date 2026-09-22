# -*- coding: utf-8 -*-
"""XR-35 검증 — repository pid별 변이 선형화 (실 LLM 0콜).

계약(cross-review/029 §1.4·§3): 같은 pid 상태를 바꾸는 전 sink(save·delete·save_trace·
save_cover_bytes·delete_cover_file)가 하나의 선형화 경계를 공유한다. 허용 결과는 둘뿐 —
save 가 락을 먼저 얻으면 save 완료 뒤 delete 가 전량 제거, delete 가 먼저면 save 는
ProjectDeletedError 거부. **어느 경우에도 delete 성공 반환 뒤 이전 writer 가 파일을 만들 수 없다.**
장벽 기법: 029 재현과 동일(_atomic_write 등 락 안 지점에 결정적 정지) — 재현의 역방향 봉합 검사.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr35_repo_linearization.py
"""
from __future__ import annotations
import sys
import pathlib
import tempfile
import threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import novelcopilot.repository.filesystem as FS
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.repository.base import ProjectDeletedError
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.world import WorldConfig


def _repo():
    return FilesystemProjectRepository(tempfile.mkdtemp(prefix="xr35_"))


def _state(pid: str, syn: str = "s") -> ProjectState:
    return ProjectState(id=pid, seed=ProjectSeed(title="t"), world=WorldConfig(title="t", synopsis=syn))


def test_save_race_no_resurrection() -> bool:
    """029 §1.2 의 역: writer 가 tombstone 검사를 통과한 상태에서 delete 는 대기하고, 부활 0."""
    repo = _repo()
    repo.save(_state("race"))
    held = repo.get("race")
    held.world.synopsis = "writer-after"
    orig = FS._atomic_write
    gate, passed = threading.Event(), threading.Event()

    def barrier(path, text):
        if str(path).endswith("race.json") and ".trace." not in str(path):
            passed.set()
            gate.wait(timeout=10)
        orig(path, text)
    FS._atomic_write = barrier
    res: dict = {}
    try:
        tw = threading.Thread(target=lambda: res.update(save=_run(repo.save, held)))
        tw.start()
        assert passed.wait(timeout=10)
        td = threading.Thread(target=lambda: res.update(delete=_run(repo.delete, "race")))
        td.start()
        td.join(timeout=0.5)
        blocked = td.is_alive()                     # 삭제가 선형화 경계에서 대기 중이어야 한다
        gate.set()
        tw.join(timeout=10)
        td.join(timeout=10)
    finally:
        FS._atomic_write = orig
    ok = blocked and res.get("save") == "returned" and res.get("delete") is True
    ok &= repo.get("race") is None                  # save 선행 → delete 가 전량 제거(허용 결과 ①)
    ok &= not list(repo.dir.glob("race.*"))
    print(f"[{'OK' if ok else 'FAIL'}] save 경합: delete 대기({blocked})·save 후 전량 제거 — 부활 0")
    assert ok
    return ok


def test_delete_first_save_rejected() -> bool:
    """허용 결과 ②(순서 고정): delete 선행 → 이전 writer 의 save 는 거부·파일 생성 0."""
    repo = _repo()
    repo.save(_state("df"))
    held = repo.get("df")
    ok = repo.delete("df") is True
    try:
        repo.save(held)
        ok = False
    except ProjectDeletedError:
        pass
    ok &= repo.get("df") is None and not list(repo.dir.glob("df.*"))
    print(f"[{'OK' if ok else 'FAIL'}] delete 선행: save 거부·파일 생성 0")
    assert ok
    return ok


def test_trace_race_no_orphan() -> bool:
    """029 §1.3 의 역: trace writer 가 경계를 쥔 동안 delete 대기 → 고아 trace 0. delete 선행이면 skip."""
    repo = _repo()
    repo.save(_state("tr"))
    orig = FS._atomic_write
    gate, passed = threading.Event(), threading.Event()

    def barrier(path, text):
        if ".trace." in str(path):
            passed.set()
            gate.wait(timeout=10)
        orig(path, text)
    FS._atomic_write = barrier
    res: dict = {}
    try:
        tw = threading.Thread(target=lambda: res.update(tr=_run(repo.save_trace, "tr", 1, {"k": "v"})))
        tw.start()
        assert passed.wait(timeout=10)
        td = threading.Thread(target=lambda: res.update(de=_run(repo.delete, "tr")))
        td.start()
        td.join(timeout=0.5)
        blocked = td.is_alive()
        gate.set()
        tw.join(timeout=10)
        td.join(timeout=10)
    finally:
        FS._atomic_write = orig
    ok = blocked and not list(repo.dir.glob("tr.trace.*.json"))   # 고아 0(029 재현의 역)
    repo.save_trace("tr", 2, {"k": "v"})                          # delete 선행 → skip(파일 생성 0)
    ok &= not list(repo.dir.glob("tr.trace.*.json"))
    print(f"[{'OK' if ok else 'FAIL'}] trace 경합: delete 대기({blocked})·고아 trace 0·사후 trace skip")
    assert ok
    return ok


def test_cover_race_no_orphan() -> bool:
    """표지 sink 경계 포함(026 잔여 소거): 쓰기 중 delete 대기 → 고아 png 0. delete 선행이면 거부."""
    repo = _repo()
    repo.save(_state("cv"))
    orig_cp = repo.cover_path
    gate, passed = threading.Event(), threading.Event()

    def barrier_cp(pid):
        passed.set()
        gate.wait(timeout=10)
        return orig_cp(pid)
    repo.cover_path = barrier_cp                    # 락 안 첫 지점(경로 해석)에 결정적 정지
    res: dict = {}
    try:
        tw = threading.Thread(target=lambda: res.update(cv=_run(repo.save_cover_bytes, "cv", b"\x89PNG")))
        tw.start()
        assert passed.wait(timeout=10)
        td = threading.Thread(target=lambda: res.update(de=_run(repo.delete, "cv")))
        td.start()
        td.join(timeout=0.5)
        blocked = td.is_alive()
        gate.set()
        tw.join(timeout=10)
        td.join(timeout=10)
    finally:
        repo.cover_path = orig_cp
    ok = blocked and not list(repo.dir.glob("cv.cover*.png"))     # writer 선행 → delete 가 제거
    try:
        repo.save_cover_bytes("cv", b"\x89PNG")                   # delete 선행 → 거부(고아 png 0)
        ok = False
    except ProjectDeletedError:
        pass
    ok &= not list(repo.dir.glob("cv.cover*.png"))
    ok &= repo.delete_cover_file("cv", "cv.cover.x.png") is False  # 멱등 삭제 경로 무예외
    print(f"[{'OK' if ok else 'FAIL'}] 표지 경합: delete 대기({blocked})·고아 png 0·사후 쓰기 거부")
    assert ok
    return ok


def test_concurrent_delete_idempotent_and_pid_parallel() -> bool:
    """동시 delete 멱등·무예외 + 무관 pid 는 경계를 공유하지 않는다(병렬성)."""
    repo = _repo()
    repo.save(_state("dd"))
    outs: list = []
    ts = [threading.Thread(target=lambda: outs.append(_run(repo.delete, "dd"))) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(timeout=10) for t in ts]
    ok = sorted(outs, key=str) == [False, True]     # 한쪽만 본체 제거·예외 0
    # 무관 pid 병렬성: pid "pa" writer 가 경계를 쥔 동안 pid "pb" 저장이 완주한다
    repo.save(_state("pa"))
    orig = FS._atomic_write
    gate, passed = threading.Event(), threading.Event()

    def barrier(path, text):
        if str(path).endswith("pa.json"):
            passed.set()
            gate.wait(timeout=10)
        orig(path, text)
    FS._atomic_write = barrier
    try:
        ha = repo.get("pa")
        ta = threading.Thread(target=lambda: _run(repo.save, ha))
        ta.start()
        assert passed.wait(timeout=10)
        tb = threading.Thread(target=lambda: _run(repo.save, _state("pb")))
        tb.start()
        tb.join(timeout=5)
        parallel = not tb.is_alive() and repo.get("pb") is not None
        gate.set()
        ta.join(timeout=10)
    finally:
        FS._atomic_write = orig
    ok &= parallel
    print(f"[{'OK' if ok else 'FAIL'}] 동시 delete 멱등({sorted(outs, key=str)}) · 무관 pid 병렬({parallel})")
    assert ok
    return ok


def test_delete_io_failure_fail_closed() -> bool:
    """삭제 I/O 중간 실패 계약: tombstone 유지(fail-closed — 부활 방지 우선)·재삭제로 정리 가능."""
    repo = _repo()
    repo.save(_state("pf"))
    held = repo.get("pf")
    orig_unlink = pathlib.Path.unlink
    calls = {"n": 0}

    def failing(self, *a, **kw):
        if self.name == "pf.json" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("disk failure (simulated)")
        return orig_unlink(self, *a, **kw)
    pathlib.Path.unlink = failing
    try:
        try:
            repo.delete("pf")                       # 본체 unlink 에서 실패 → 예외 전파
            ok = False
        except OSError:
            ok = True
    finally:
        pathlib.Path.unlink = orig_unlink
    try:
        repo.save(held)                             # 실패했어도 tombstone 유지 — 부활 불가(fail-closed)
        ok = False
    except ProjectDeletedError:
        pass
    ok &= repo.delete("pf") is True                 # 재삭제로 잔여 정리(멱등 복구)
    ok &= repo.get("pf") is None and not list(repo.dir.glob("pf.*"))
    print(f"[{'OK' if ok else 'FAIL'}] 삭제 I/O 실패: fail-closed 유지·재삭제 복구")
    assert ok
    return ok


def _run(fn, *a, **kw):
    try:
        r = fn(*a, **kw)
        return "returned" if r is None else r
    except Exception as e:
        return type(e).__name__


if __name__ == "__main__":
    results = [test_save_race_no_resurrection(), test_delete_first_save_rejected(),
               test_trace_race_no_orphan(), test_cover_race_no_orphan(),
               test_concurrent_delete_idempotent_and_pid_parallel(),
               test_delete_io_failure_fail_closed()]
    print("\nXR-35 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
