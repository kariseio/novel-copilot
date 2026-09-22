# -*- coding: utf-8 -*-
"""엔진 세션 — 영속 상태(ProjectState) ↔ 살아있는 엔진(EngineBundle) 사이의 재수화/스냅샷.

세션은 프로세스 메모리에 캐시되고, 캐시 미스 시 Repository 에서 로드해 재구성한다.
writer_lock(단일 소유)으로 회차 생성을 직렬화(append-only 안전).
"""
from __future__ import annotations
import threading
from collections import OrderedDict

from ..config import Settings
from ..domain.project import ProjectState
from ..domain.world import WorldConfig
from ..llm.base import LLMProvider
from ..llm.factory import create_provider, create_role_provider
from ..engine.factory import build_engine, EngineBundle
from ..engine.ontology import Entity


class ProjectBusyError(RuntimeError):
    """XR-34(cross-review/025): 진행 중 writer 가 프로젝트 락을 쥐고 있어 삭제할 수 없음.

    제품 계약 = 즉시 거부(423) — 회차 생성이 분 단위로 락을 쥐므로 '대기 후 삭제'는 삭제 API 를
    매달리게 한다. 사용자에게 "작업 완료 후 다시 삭제"가 더 정직하고 단순하다(025 §4.1 양자 중 선택·근거)."""


class EngineSession:
    def __init__(self, project_id: str, world: WorldConfig, provider: LLMProvider,
                 settings: Settings):
        self.project_id = project_id
        self.world = world                 # state.world 와 동일 객체(비트 연장 반영)
        self.provider = provider
        # CE-1 ⓑ: 보조 스테이지용 aux provider — 세션 수명에서 '1회' 생성·재사용(콜마다 재생성 금지). aux_model 이
        #   빈값("")이면 create_role_provider 가 gen provider(=self.provider 와 동형)를 돌려주는데, 그때는 그 새 객체
        #   대신 self.provider 를 그대로 aux 로 넘겨 '동일 객체' 폴백을 보장한다 → build_engine/generator 에서
        #   aux is provider 판정이 성립해 스왑 0·기존 경로 바이트 동일(계측 이중계상 0). aux_model 이 설정되면 저가 provider.
        _aux_spec = (getattr(settings, "aux_model", "") or "").strip()
        aux = create_role_provider(settings, _aux_spec) if _aux_spec else provider
        self.aux_provider = aux
        # 캐논 추출 전용 provider — aux 와 동일 계약(세션 수명 1회 생성·빈값이면 self.provider 동일 객체 폴백 =
        #   스왑 0·기존 경로 바이트 동일). extract_model 이 aux_model 과 같은 스펙이어도 별도 인스턴스로 두어
        #   harness 의 스테이지 델타 합산(_mark/_track)이 이중계상 없이 성립한다.
        _ex_spec = (getattr(settings, "extract_model", "") or "").strip()
        ex = create_role_provider(settings, _ex_spec) if _ex_spec else provider
        self.extract_provider = ex
        self.bundle: EngineBundle = build_engine(world, provider, settings, aux_provider=aux,
                                                 extract_provider=ex)
        self.lock = threading.Lock()

    @property
    def bus(self):
        return self.bundle.event_bus

    def rehydrate(self, state: ProjectState) -> None:
        """영속 상태를 엔진에 복원(LLM 재계산 회피)."""
        ont = self.bundle.ontology
        for es in state.runtime_entities:
            if es.id not in ont.entities:
                ont.add(Entity(id=es.id, name=es.name, etype=es.etype, attrs=dict(es.attrs),
                               aliases=list(es.aliases), base_status=es.base_status,
                               voice=getattr(es, "voice", ""),
                               voice_stages=dict(getattr(es, "voice_stages", None) or {}),   # VB-1: 상태 연동 보이스 미러
                               provisional=es.provisional,
                               cardinality=dict(getattr(es, "cardinality", None) or {})))   # CN-4 상한 보존
        for t in state.runtime_timeline:
            ont.set_state(t.entity_id, t.attr, t.value, t.eff_from, reason=t.reason,
                          trust_tier=getattr(t, "trust_tier", "ground_truth"))   # tier 보존(구 데이터→gt 기본)
        for edge in state.runtime_edges:
            if not any(e.edge_id == edge.edge_id for e in ont.edges):
                ont.add_edge(edge)
        if state.rag_chunks:
            self.bundle.rag.import_chunks(state.rag_chunks)
        if state.wiki_pages:
            self.bundle.wiki.import_pages(state.wiki_pages, state.wiki_log)

    def snapshot_into(self, state: ProjectState) -> None:
        """엔진 메모리를 영속 상태로 직렬화."""
        state.rag_chunks = self.bundle.rag.export_chunks()
        state.wiki_pages = self.bundle.wiki.export_pages()
        state.wiki_log = list(self.bundle.wiki.log)


class SessionManager:
    """프로젝트별 세션 캐시(LRU). 비대칭: 메모리 우선, 없으면 repo 에서 재수화 — 축출해도 정보 손실 0(디스크=권위).
    상한 초과 시 가장 오래 안 쓴 세션부터 축출하되, 생성 중(lock 보유)인 세션은 건너뛴다(lost update 방지)."""
    def __init__(self, settings: Settings, state_loader=None):
        self.settings = settings
        # pending eviction 대기 뒤에는 호출자가 건넨 state 자체가 이전 커밋 스냅샷일 수 있다. 매니저가
        # 디스크 권위를 다시 읽어 새 세대를 수화하도록 서비스 repository loader를 주입한다(테스트는 선택).
        self._state_loader = state_loader
        self._sessions: "OrderedDict[str, EngineSession]" = OrderedDict()
        self._guard = threading.Lock()
        self._cap = max(1, getattr(settings, "max_live_sessions", 32))
        # XR-30(cross-review/018 §3~6): 프로젝트 락은 **세션 수명보다 길다** — 세션 객체가 축출·재수화로
        #   교체돼도(무조건 evict 8개 호출부·LRU) 같은 pid 의 모든 세대가 이 안정 락 하나를 공유해,
        #   장시간 작업(위키 재구축)의 커밋과 새 세대의 변이가 항상 직렬화된다. 락이 세션 소유일 때는
        #   교체 순간 보호가 사라졌다(018 재현 — 옛 세대 커밋·현재 세대 옛 위키 잔존). 락은 축출하지 않는다.
        self._locks: dict[str, threading.Lock] = {}
        # XR-32(cross-review/021): 명시적 evict 가 프로젝트 락 보유 중 세션을 즉시 제거하면, 다른 요청이
        #   새 세대를 먼저 받아 같은 락에서 기다린 뒤 커밋 직후 옛 snapshot 을 덮어쓸 수 있다. 작업 중인
        #   세션은 캐시에서 즉시 제거하되 새 세대 생성은 보류하고, 다음 writer 가 resolve_locked 로 정본을 재해석한다.
        self._pending_evictions: set[str] = set()
        # XR-34(cross-review/025): 삭제 tombstone — 삭제된 pid 는 세션을 다시 발급하지 않는다(유령 세션
        #   차단). repo 층의 sink tombstone(save 거부)과 짝: 캐시 미스와 '삭제됨'은 다른 상태다.
        self._tombstones: set[str] = set()

    def _get_or_create_guarded(self, state: ProjectState) -> EngineSession:
        """_guard 보유 전용. pending 처리는 호출자가 결정한다."""
        sess = self._sessions.get(state.id)
        if sess is not None:
            sess.world = state.world      # 최신 world 참조 동기화
            self._sessions.move_to_end(state.id)
            return sess
        provider = create_provider(self.settings)
        if hasattr(provider, "set_log_tag"):
            provider.set_log_tag(state.id)
        sess = EngineSession(state.id, state.world, provider, self.settings)
        sess.rehydrate(state)
        sess.lock = self._locks.setdefault(state.id, sess.lock)
        self._sessions[state.id] = sess
        self._sessions.move_to_end(state.id)
        self._evict_over_cap_locked()
        return sess

    def get_or_create(self, state: ProjectState) -> EngineSession:
        """읽기/구독용 세션 조회.

        pending eviction 중 프로젝트 락이 잡혀 있으면 새 세대를 발급하지 않고 기존 writer 가 끝날 때까지
        기다린다. mutable 경로는 락 획득 뒤 반드시 resolve_locked 를 호출해 '세션 획득+락'을 완결한다.
        """
        refresh_after_wait = False
        while True:
            if refresh_after_wait and self._state_loader is not None:
                fresh = self._state_loader(state.id)
                if fresh is None:
                    # XR-34 §4.2: 대기 중 정본이 사라짐 = 삭제 — stale 인자 폴백은 유령 세션을 만든다(025 재현).
                    #   캐시 미스와 삭제를 같은 '상태 없음'으로 취급하지 않는다.
                    raise KeyError(f"프로젝트가 삭제되었습니다: {state.id}")
                state = fresh
                refresh_after_wait = False
            elif refresh_after_wait:
                refresh_after_wait = False
            wait_lock = None
            with self._guard:
                if state.id in self._tombstones:   # XR-34: 삭제된 pid 는 세션 발급 금지(유령 세션 차단)
                    raise KeyError(f"프로젝트가 삭제되었습니다: {state.id}")
                lock = self._locks.get(state.id)
                if state.id in self._pending_evictions:
                    if lock is not None and lock.locked():
                        wait_lock = lock
                    else:
                        self._pending_evictions.discard(state.id)
                        self._sessions.pop(state.id, None)
                if wait_lock is None:
                    return self._get_or_create_guarded(state)
            # _guard 밖에서만 대기한다(프로젝트 락→manager guard 순서와 역전 금지).
            wait_lock.acquire()
            wait_lock.release()
            refresh_after_wait = True

    def resolve_locked(self, state: ProjectState) -> EngineSession:
        """XR-32 writer lease의 세대 해석 단계. 호출자는 해당 pid 안정 락을 보유해야 한다.

        get_or_create 와 lock.acquire 사이에 세션이 축출된 경우에도 여기서 현재 세대를 다시 선택한다.
        락 보유 중 들어온 evict 는 pending 으로만 기록되므로 이 반환 뒤 세대가 다시 갈라지지 않는다.
        테스트용 단순 세션 매니저는 CopilotService 호환 어댑터가 기존 세션을 그대로 반환한다.
        """
        with self._guard:
            if state.id in self._tombstones:   # XR-34: 삭제된 pid — writer 진입 거부(정직 실패)
                raise KeyError(f"프로젝트가 삭제되었습니다: {state.id}")
            if state.id in self._pending_evictions:
                self._pending_evictions.discard(state.id)
                self._sessions.pop(state.id, None)
            return self._get_or_create_guarded(state)

    def current(self, project_id: str) -> "EngineSession | None":
        """XR-30: 매니저가 지금 보유한 세션(생성·재수화 없음 — 없으면 None). 장시간 작업의 커밋이
        '내가 커밋한 세대가 여전히 현재인가'를 검사하는 용도(불일치면 축출해 디스크 정본 재수화 유도)."""
        with self._guard:
            return self._sessions.get(project_id)

    def _evict_over_cap_locked(self) -> None:
        """상한 초과분 축출(오래된 순). 단 생성 중(lock 보유) 세션은 보존 — in-flight 객체 분기 방지."""
        for pid in list(self._sessions.keys()):       # OrderedDict: 앞=오래됨
            if len(self._sessions) <= self._cap:
                break
            s = self._sessions[pid]
            if not s.lock.locked():                    # 작업 중이 아닐 때만 축출(정보 손실 0)
                self._sessions.pop(pid, None)

    def evict(self, project_id: str) -> None:
        with self._guard:
            lock = self._locks.get(project_id)
            if lock is not None and lock.locked():
                self._pending_evictions.add(project_id)
                self._sessions.pop(project_id, None)   # 실패 롤백 계약: 캐시에서는 즉시 제거, 재수화만 락 종료까지 보류
            else:
                self._pending_evictions.discard(project_id)
                self._sessions.pop(project_id, None)

    def delete_project(self, project_id: str, repo_delete) -> bool:
        """XR-34(cross-review/025 §4.1·§4.3): 삭제 트랜잭션 primitive — 서비스가 evict+repo.delete 를
        따로 조합하지 않는다(그 조합이 삭제 성공 후 부활을 허용했다·025 재현).

        ⑴ 안정 프로젝트 락을 비블로킹 획득 — 진행 중 writer 가 있으면 ProjectBusyError(제품 계약: 423
        즉시 거부) ⑵ 획득 시 tombstone(이후 세션 발급 금지)·세션·pending 정리 ⑶ repo 삭제 콜백(레포 층
        sink tombstone 은 콜백 안에서 함께 기록됨 — 발급된 상태 스냅샷의 사후 save 차단). 삭제 성공 응답
        뒤 기존 writer 가 저장할 수 있는 경로는 없다(락 직렬화 + 양층 tombstone + writer 의 락 안 권위 재읽기)."""
        with self._guard:
            lock = self._locks.setdefault(project_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ProjectBusyError(f"진행 중인 작업이 있어 삭제할 수 없습니다(완료 후 다시 시도): {project_id}")
        try:
            with self._guard:
                self._tombstones.add(project_id)
                self._pending_evictions.discard(project_id)
                self._sessions.pop(project_id, None)
            return repo_delete()
        finally:
            lock.release()
