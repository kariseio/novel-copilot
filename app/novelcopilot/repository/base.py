# -*- coding: utf-8 -*-
"""Repository 패턴 — 영속화 추상화. 서비스는 저장 매체를 모른다(메모리/파일/DB 교체 가능)."""
from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from ..domain.project import ProjectState


class ProjectDeletedError(RuntimeError):
    """XR-34(cross-review/025): 삭제된 프로젝트에 대한 저장 시도 — '변이 sink' 지점 거부.

    삭제 전에 발급된 ProjectState 스냅샷을 쥔 writer 가 삭제 성공 뒤 save 하면 프로젝트가 부활하던
    결함(025 재현)의 구조적 차단: 부활 방지는 호출자 열거(모집단 오류 — 025 §3)가 아니라 sink 에서 한다."""


class ProjectRepository(ABC):
    @abstractmethod
    def save(self, state: ProjectState) -> None: ...

    @abstractmethod
    def get(self, project_id: str) -> ProjectState | None: ...

    @abstractmethod
    def list_summaries(self) -> list[dict]: ...

    @abstractmethod
    def delete(self, project_id: str) -> bool: ...

    # CV-1: 표지 바이너리(메타는 ProjectState.cover 로 main JSON, 바이트는 매체별 사이드카) — rag 파일 분리 선례.
    #   구현체가 미지원이면 명시 에러(침묵 폴백 금지). Filesystem 구현이 오버라이드한다.
    def cover_path(self, pid: str) -> Path:
        raise NotImplementedError

    def save_cover_bytes(self, pid: str, png: bytes, filename: str | None = None) -> str:
        # CV-2: filename 지정 시 그 이름(보관함), 미지정 시 legacy {pid}.cover.png(하위호환).
        raise NotImplementedError

    def read_cover_bytes(self, pid: str) -> bytes | None:
        raise NotImplementedError

    # CV-2: 보관함 개별 파일 접근(파일명 검증 = 경로 탈출·타 프로젝트 차단은 구현체가 수행).
    def read_cover_file(self, pid: str, filename: str) -> bytes | None:
        raise NotImplementedError

    def delete_cover_file(self, pid: str, filename: str) -> bool:
        raise NotImplementedError

    # GA-1: 생성 트레이스 사이드카(중간 산출물 전량 영속 — 본체 JSON 무팽창). 미지원 구현은 no-op(관측·무강제:
    #   저장 실패·미지원이 발행을 막지 않는다). Filesystem 구현이 회차별 append-only 사이드카로 오버라이드한다.
    #   FI-1: kind kwarg(하위호환·기본 "") — Filesystem 구현이 kind 그룹별 상한(author_intent 별도)을 분기한다.
    def save_trace(self, pid: str, chapter: int, run: dict, *, max_runs: int = 50, kind: str = "") -> None:
        pass   # 미지원 매체(메모리 등)는 무동작 — 트레이스는 순수 부가(무강제·비차단)

    def load_trace(self, pid: str, chapter: int) -> dict | None:
        return None
