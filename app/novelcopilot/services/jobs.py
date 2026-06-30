# -*- coding: utf-8 -*-
"""회차 생성 잡(Job) 레지스트리 — 요청 수명과 생성 수명을 분리한다.

회차 한 편 생성은 모델 전환·교정 루프 때문에 10분 넘게 걸릴 수 있다. 브라우저 연결(SSE)이
끊겨도(탭 닫힘·새로고침·네트워크 단절) 생성은 백그라운드 데몬 스레드에서 끝까지 진행·영속되고,
재접속하면 진행 중인 잡에 다시 붙어(이벤트 버퍼 리플레이) 같은 진행 상황을 이어 본다.

핵심 계약:
- **프로젝트당 동시 1개**(start_or_get 멱등) — 새로고침이 두 번째 생성을 트리거하지 않는다(중복 회차 방지).
- **이벤트 버퍼**(append-only) — 늦게 붙은 구독자도 처음부터 리플레이해 놓친 진행을 복원.
- **종료 후 보존**(retain_sec) — 연결이 끊긴 사이 끝난 잡의 결과를 재접속 시 보여줄 수 있게 잠시 유지.
세션 객체와 동일하게 프로세스 메모리에만 산다(단일 워커 가정 — 멀티워커는 외부 큐 필요, B-11 계열 한계).
"""
from __future__ import annotations
import threading
import time
from typing import Callable, Optional


class GenerationJob:
    """단일 회차 생성 1건의 진행 상태 + 이벤트 버퍼(스레드 안전)."""

    def __init__(self, project_id: str, chapter: int, directive: str = "") -> None:
        self.project_id = project_id
        self.chapter = chapter                 # 집필 중인 회차 번호(시작 시 확정 = current+1)
        self.directive = directive
        self.status = "running"                # running | done | failed
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.started_ts = time.time()
        self.finished_ts: Optional[float] = None
        self.events: list[dict] = []           # 하네스 진행 이벤트(리플레이용)
        self.result: Optional[dict] = None     # 성공 시 'complete' 페이로드
        self.error: Optional[dict] = None      # 실패 시 'failed' 페이로드
        self._lock = threading.Lock()

    # ---- 생산자(생성 스레드) ----
    def append_event(self, evt: dict) -> None:
        with self._lock:
            if self.status == "running":       # 종료 후 늦은 이벤트는 버린다(리플레이 일관성의 방어선)
                self.events.append(dict(evt))
            # 주의(불변식): _stream_job 은 'status!=running ⇒ events 가 최종'에 의존한다. 1차 보장은
            # runner 가 종료 설정 전에 sess.bus 구독을 해제하는 것(copilot.start_generation)이고,
            # 이 status 가드는 그 경계 창을 메우는 2차 방어다 — 둘 중 하나라도 지우면 torn replay 위험.

    def set_done(self, payload: dict) -> bool:
        with self._lock:
            if self.status != "running":
                return False                   # 최초 1회만(중복 종료 무시)
            self.result = payload
            self.status = "done"
            self.finished_ts = time.time()
            return True

    def set_failed(self, message: str) -> bool:
        with self._lock:
            if self.status != "running":
                return False
            self.error = {"message": message}
            self.status = "failed"
            self.finished_ts = time.time()
            return True

    # ---- 소비자(SSE 스트리머) ----
    def snapshot_from(self, cursor: int) -> tuple[list[dict], str, Optional[dict], Optional[dict]]:
        """cursor 이후 새 이벤트 + 현재 상태를 원자적으로 스냅샷.
        status 가 종료면 events 는 더 이상 늘지 않으므로(생산자는 unsub 후 종료 설정), 반환 슬라이스가 '잔여 전부'다."""
        with self._lock:
            return list(self.events[cursor:]), self.status, self.result, self.error

    def status_view(self) -> dict:
        with self._lock:
            return {"status": self.status, "chapter": self.chapter,
                    "started_at": self.started_at, "event_count": len(self.events),
                    "result": self.result if self.status == "done" else None,
                    "error": self.error if self.status == "failed" else None}


class GenerationJobManager:
    """프로젝트별 회차 생성 잡 레지스트리. 멱등 시작 + 게으른 GC."""

    def __init__(self, retain_sec: int = 1800) -> None:
        self._jobs: dict[str, GenerationJob] = {}
        self._guard = threading.Lock()
        self._retain = max(60, retain_sec)

    def start_or_get(self, project_id: str, chapter: int, directive: str,
                     runner: Callable[[GenerationJob], None]) -> tuple[GenerationJob, bool]:
        """진행 중 잡이 있으면 그것을 돌려준다(created=False — 중복 생성 금지). 없으면 새로 시작.
        runner(job) 는 새로 만든 경우에만 데몬 스레드에서 호출된다(생성 본체)."""
        with self._guard:
            self._gc_locked()
            existing = self._jobs.get(project_id)
            if existing is not None and existing.status == "running":
                return existing, False
            job = GenerationJob(project_id, chapter, directive)
            self._jobs[project_id] = job
            t = threading.Thread(target=self._thread_main, args=(job, runner),
                                 name=f"gen-{project_id}", daemon=True)
            t.start()
            return job, True

    def _thread_main(self, job: GenerationJob, runner: Callable[[GenerationJob], None]) -> None:
        try:
            runner(job)                         # 성공 시 runner 내부에서 job.set_done 호출
        except Exception as e:                  # noqa — 어떤 예외도 잡을 '실패'로 종결(좀비 running 방지)
            job.set_failed(f"{type(e).__name__}: {e}")
        finally:
            if job.status == "running":         # runner 가 종료 설정을 안 했으면(이른 return 등) 방어적 종결
                job.set_failed("생성이 비정상 종료되었습니다")

    def get(self, project_id: str) -> Optional[GenerationJob]:
        with self._guard:
            self._gc_locked()
            return self._jobs.get(project_id)

    def _gc_locked(self) -> None:
        """종료된 지 retain 초 지난 잡 회수. 진행 중(running)은 절대 회수 안 함."""
        now = time.time()
        for pid in list(self._jobs.keys()):
            j = self._jobs[pid]
            if j.status != "running" and j.finished_ts and (now - j.finished_ts) > self._retain:
                self._jobs.pop(pid, None)
