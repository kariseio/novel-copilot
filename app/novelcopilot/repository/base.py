# -*- coding: utf-8 -*-
"""Repository 패턴 — 영속화 추상화. 서비스는 저장 매체를 모른다(메모리/파일/DB 교체 가능)."""
from __future__ import annotations
from abc import ABC, abstractmethod
from ..domain.project import ProjectState


class ProjectRepository(ABC):
    @abstractmethod
    def save(self, state: ProjectState) -> None: ...

    @abstractmethod
    def get(self, project_id: str) -> ProjectState | None: ...

    @abstractmethod
    def list_summaries(self) -> list[dict]: ...

    @abstractmethod
    def delete(self, project_id: str) -> bool: ...
