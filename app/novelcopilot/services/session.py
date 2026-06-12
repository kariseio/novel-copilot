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
from ..llm.factory import create_provider
from ..engine.factory import build_engine, EngineBundle
from ..engine.ontology import Entity


class EngineSession:
    def __init__(self, project_id: str, world: WorldConfig, provider: LLMProvider,
                 settings: Settings):
        self.project_id = project_id
        self.world = world                 # state.world 와 동일 객체(비트 연장 반영)
        self.provider = provider
        self.bundle: EngineBundle = build_engine(world, provider, settings)
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
                               provisional=es.provisional))   # 영속 플래그 보존(factory 와 동일 — 강제 True 금지)
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
    def __init__(self, settings: Settings):
        self.settings = settings
        self._sessions: "OrderedDict[str, EngineSession]" = OrderedDict()
        self._guard = threading.Lock()
        self._cap = max(1, getattr(settings, "max_live_sessions", 32))

    def get_or_create(self, state: ProjectState) -> EngineSession:
        with self._guard:
            sess = self._sessions.get(state.id)
            if sess is not None:
                sess.world = state.world      # 최신 world 참조 동기화
                self._sessions.move_to_end(state.id)   # LRU 갱신(최근 사용 = 뒤)
                return sess
            provider = create_provider(self.settings)
            sess = EngineSession(state.id, state.world, provider, self.settings)
            sess.rehydrate(state)
            self._sessions[state.id] = sess
            self._sessions.move_to_end(state.id)
            self._evict_over_cap_locked()
            return sess

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
            self._sessions.pop(project_id, None)
