# -*- coding: utf-8 -*-
"""파일시스템 Repository — 프로젝트당 핫패스 JSON 1개 + 임베딩 사이드카.

write-amplification 차단: rag_chunks(임베딩, 파일의 ~99%)를 사이드카(.rag.json)로 분리하고
지문(fingerprint)이 바뀐 저장에만 다시 쓴다 → 지시/설정집 편집 같은 소량 변이가 수십 MB를 재기록하지 않음.
구형(임베딩 인라인) JSON 도 로드 호환 — 다음 save 에서 자동 분리(무중단 마이그레이션).
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from ..domain.project import ProjectState, PersistedChunk
from .base import ProjectRepository


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())            # 디스크 반영 보장
    tmp.replace(path)                   # 원자적 교체


def _rag_fingerprint(chunks) -> str:
    """가벼운 변경 감지 — 청크수 + 회차합 + 텍스트 길이합(임베딩 재직렬화 없이)."""
    return f"{len(chunks)}:{sum(c.chapter for c in chunks)}:{sum(len(c.text) for c in chunks)}"


class FilesystemProjectRepository(ProjectRepository):
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "projects"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pid: str) -> Path:
        return self.dir / f"{pid}.json"

    def _rag_legacy(self, pid: str) -> Path:
        return self.dir / f"{pid}.rag.json"       # 구형 단일 사이드카(읽기 호환만)

    def _rag_shard(self, pid: str, chapter: int) -> Path:
        return self.dir / f"{pid}.rag.{chapter}.json"

    def save(self, state: ProjectState) -> None:
        chunks = state.rag_chunks
        try:
            by_ch: dict = {}
            for c in chunks:
                by_ch.setdefault(c.chapter, []).append(c)
            # 회차별 샤드 — 지문 바뀐 샤드만 재기록(O(변경 회차); 통짜 재기록 제거 → 수백 화 스케일)
            for ch, cs in by_ch.items():
                sp = self._rag_shard(state.id, ch)
                fp = _rag_fingerprint(cs)
                old_fp = None
                if sp.exists():
                    with open(sp, encoding="utf-8") as f:
                        old_fp = json.loads(f.readline() or "{}").get("fp")
                if fp != old_fp:
                    _atomic_write(sp, json.dumps({"fp": fp}, ensure_ascii=False) + "\n"
                                  + json.dumps([c.model_dump() for c in cs], ensure_ascii=False))
            for sp in self.dir.glob(f"{state.id}.rag.*.json"):    # 제거된 회차 샤드 정리
                try:
                    ch = int(sp.name.rsplit(".", 2)[-2])
                except ValueError:
                    continue
                if ch not in by_ch:
                    sp.unlink()
            legacy = self._rag_legacy(state.id)
            if legacy.exists():                    # 구형 단일 사이드카 → 샤드 이관 완료 후 제거
                legacy.unlink()
            state.rag_chunks = []                  # 핫패스 JSON 에는 미포함
            _atomic_write(self._path(state.id), state.model_dump_json(indent=2))
        finally:
            state.rag_chunks = chunks              # 메모리 객체 원복(호출자 불변)

    def get(self, project_id: str) -> ProjectState | None:
        p = self._path(project_id)
        if not p.exists():
            return None
        state = ProjectState.model_validate_json(p.read_text(encoding="utf-8"))
        if not state.rag_chunks:                   # 샤드(또는 구형 사이드카/인라인)에서 임베딩 복원
            chunks: list[PersistedChunk] = []
            shards = sorted(self.dir.glob(f"{project_id}.rag.*.json"),
                            key=lambda s: int(s.name.rsplit(".", 2)[-2])
                            if s.name.rsplit(".", 2)[-2].isdigit() else 0)
            for sp in shards:
                with open(sp, encoding="utf-8") as f:
                    f.readline()                   # 지문 헤더 skip
                    chunks += [PersistedChunk.model_validate(c) for c in json.loads(f.read() or "[]")]
            if not chunks and self._rag_legacy(project_id).exists():
                with open(self._rag_legacy(project_id), encoding="utf-8") as f:
                    f.readline()
                    chunks = [PersistedChunk.model_validate(c) for c in json.loads(f.read() or "[]")]
            state.rag_chunks = chunks
        return state

    def list_summaries(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                s = ProjectState.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({"id": s.id, "title": s.world.title, "genre": s.world.genre,
                        "created_at": s.created_at, "current_chapter": s.current_chapter,
                        "total_chapters": len(s.world.beats)})
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out

    def delete(self, project_id: str) -> bool:
        p = self._path(project_id)
        for sp in self.dir.glob(f"{project_id}.rag*.json"):   # 샤드 + 구형 사이드카 정리
            sp.unlink()
        if p.exists():
            p.unlink()
            return True
        return False
