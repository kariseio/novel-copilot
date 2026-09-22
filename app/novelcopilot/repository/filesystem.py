# -*- coding: utf-8 -*-
"""파일시스템 Repository — 프로젝트당 핫패스 JSON 1개 + 임베딩 사이드카.

write-amplification 차단: rag_chunks(임베딩, 파일의 ~99%)를 사이드카(.rag.json)로 분리하고
지문(fingerprint)이 바뀐 저장에만 다시 쓴다 → 지시/설정집 편집 같은 소량 변이가 수십 MB를 재기록하지 않음.
구형(임베딩 인라인) JSON 도 로드 호환 — 다음 save 에서 자동 분리(무중단 마이그레이션).
"""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path

from ..domain.project import ProjectState, PersistedChunk
from .base import ProjectRepository, ProjectDeletedError

# FI-1 §5: 트레이스 쓰기 락(모듈 수준 1개). save_trace 는 read-modify-write + 원자 교체라 손상은 못 나지만
#   동시 쓰기의 lost-update(둘이 같은 구본을 읽고 나중 쓰기가 앞 run 을 지움)는 못 막는다. 단일 uvicorn 프로세스·
#   쓰기 소형·저빈도라 per-key 락 분할은 과잉 — 락 하나로 직렬화한다. 기존 GA-1 경로(rerender emit·회차 생성
#   trace)도 같은 락의 수혜 = 잠복 lost-update 의 소스 수리 겸. 락 순서: 서비스의 sess.lock → 이 락 단방향만
#   허용(이 락 안에서 sess.lock 획득 금지 — save_trace 는 sess.lock 을 절대 잡지 않으므로 데드락 불가).
_TRACE_WRITE_LOCK = threading.Lock()


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
        # XR-34(cross-review/025): 삭제 tombstone — 프로세스 내 집합(재시작은 진행 중 writer 도 함께
        #   죽이므로 영속 불필요). save() 가 이 집합을 검사해 삭제 뒤의 상태 부활을 sink 에서 거부한다.
        self._tombstones: set[str] = set()
        # XR-35(cross-review/029 §1): pid별 변이 선형화 락 — tombstone "검사"와 파일 "변이"가 한 경계에
        #   있지 않으면 검사 통과 후 삭제가 끼어들어 부활한다(TOCTOU — 029 결정적 재현). 같은 상태를 바꾸는
        #   전 sink(save·delete·save_trace·save_cover_bytes·delete_cover_file)가 이 락 하나를 공유한다
        #   (규율 9 정밀화: sink 를 세는 것으로 부족 — 전 sink 가 하나의 선형화 경계를 공유해야 한다).
        #   락 순서(단방향 고정): SessionManager 안정 프로젝트 락 → 이 pid 락 → _TRACE_WRITE_LOCK.
        #   레포는 세션 계층을 절대 호출하지 않으므로 역순 획득 경로가 구조적으로 없다.
        self._pid_locks: dict[str, threading.RLock] = {}
        self._pid_locks_guard = threading.Lock()

    def _mutation_lock(self, pid: str) -> threading.RLock:
        """pid 변이 락(생성 게이트만 별도 가드 — dict racy setdefault 방지). 읽기(get 등)는 비대상:
        읽기는 파일을 만들지 못하므로 부활 불가 — 삭제와 겹치면 부재(None)나 예외로 정직 표면화된다."""
        with self._pid_locks_guard:
            return self._pid_locks.setdefault(pid, threading.RLock())

    def _path(self, pid: str) -> Path:
        return self.dir / f"{pid}.json"

    def _rag_legacy(self, pid: str) -> Path:
        return self.dir / f"{pid}.rag.json"       # 구형 단일 사이드카(읽기 호환만)

    def _rag_shard(self, pid: str, chapter: int) -> Path:
        return self.dir / f"{pid}.rag.{chapter}.json"

    # GA-1: 생성 트레이스 사이드카(회차별·append-only) — RAG 샤드 관례 동형(`<pid>.trace.<ch>.json`).
    #   본체 프로젝트 JSON·gen_context 는 건드리지 않는다(용량·하위호환) — 순수 부가 관측 사이드카.
    #   list_summaries 는 '*.json' 만 스캔하지만 이 파일도 '.json' 이라 ProjectState 파싱 실패 → 조용히 skip
    #   (cover.png 와 달리 확장자 필터로는 못 거른다). model_validate_json 이 실패해 continue 되므로 목록 간섭 0.
    def _trace_shard(self, pid: str, chapter: int) -> Path:
        return self.dir / f"{pid}.trace.{chapter}.json"

    def save_trace(self, pid: str, chapter: int, run: dict, *, max_runs: int = 50, kind: str = "") -> None:
        """회차 트레이스 1건(run dict)을 사이드카에 **append-only** 병합한다(회차 재생성 이력·작가 의도 이벤트 보존).

        기존 파일이 있으면 runs 배열에 append(덮어쓰기 아님). FI-1 §1: **kind 그룹별 상한 분리** — 대형 생성 run 과
        소형 의도 이벤트가 한 상한을 공유하면 서로를 축출하므로, append 하는 run 의 그룹(author_intent vs 그 외)만
        max_runs 로 트림하고 그 그룹 전용 드롭 카운터(author_intent=dropped_intents / 그 외=dropped_runs)를 누적한다
        (은폐 금지 — '싹 다 저장' 정신 + 무한 재생성 방어). 그룹 판정은 kind kwarg(있으면 우선) 또는 run['kind'].
        같은 회차 샤드에 생성 trace 와 author_intent 이벤트가 섞여도(§1 회차 스코프 의도 이벤트) 각자 상한이 독립.
        원자적 쓰기(_atomic_write)를 **모듈 트레이스 락**(§5)으로 감싸 동시 read-modify-write lost-update 를 차단한다.
        시그니처 하위호환: 기존 호출부(max_runs 만 전달)는 kind="" → 그 외 그룹으로 흘러 종전 동작(전 runs=단일 그룹).
        스키마: {"chapter", "runs":[…], "dropped_runs":int, "dropped_intents":int}."""
        # XR-35: 검사·쓰기를 pid 변이 락 안으로 — 락 밖 검사는 통과 후 delete 가 끼어들어 고아 trace 를
        #   재생성한다(029 §1.3 재현). 락 순서: pid 락 → _TRACE_WRITE_LOCK(단방향 고정).
        with self._mutation_lock(pid):
            if pid in self._tombstones:   # XR-34: 삭제된 프로젝트의 고아 사이드카 생성 방지 — 부가 관측물이라 skip(비차단)
                return
            self._save_trace_inner(pid, chapter, run, max_runs=max_runs, kind=kind)

    def _save_trace_inner(self, pid: str, chapter: int, run: dict, *, max_runs: int, kind: str) -> None:
        p = self._trace_shard(pid, chapter)
        # 판정: 이 append 가 author_intent 그룹인가(kind kwarg 우선 — emit_intent 가 명시. 없으면 run 자체의 kind).
        is_intent = (kind == "author_intent") or (isinstance(run, dict) and run.get("kind") == "author_intent")
        with _TRACE_WRITE_LOCK:                        # §5: read-modify-write 를 직렬화(lost-update 소스 수리)
            doc = {"chapter": chapter, "runs": [], "dropped_runs": 0, "dropped_intents": 0}
            if p.exists():
                try:
                    loaded = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        doc["runs"] = list(loaded.get("runs") or [])
                        doc["dropped_runs"] = int(loaded.get("dropped_runs") or 0)
                        doc["dropped_intents"] = int(loaded.get("dropped_intents") or 0)   # 구 파일엔 없음 → 0(하위호환)
                except Exception:
                    pass   # 손상 파일은 새로 쓴다(관측 사이드카 — 발행·상태에 영향 0)
            doc["runs"].append(run)
            if max_runs > 0:
                # append 한 run 의 그룹만 트림 — 반대 그룹은 자기 append 시점에 이미 자기 상한으로 관리됨(상호 비축출).
                group_idx = [k for k, r in enumerate(doc["runs"])
                             if (isinstance(r, dict) and r.get("kind") == "author_intent") == is_intent]
                if len(group_idx) > max_runs:
                    drop_n = len(group_idx) - max_runs
                    drop_pos = set(group_idx[:drop_n])   # 그룹 내 가장 오래된 것부터(원 순서 = 오래된 순)
                    doc["runs"] = [r for k, r in enumerate(doc["runs"]) if k not in drop_pos]
                    doc["dropped_intents" if is_intent else "dropped_runs"] += drop_n   # 그룹 전용 카운터(은폐 금지)
            _atomic_write(p, json.dumps(doc, ensure_ascii=False))

    def load_trace(self, pid: str, chapter: int) -> dict | None:
        """회차 트레이스 사이드카를 읽는다(없거나 손상이면 None — 결측 정직)."""
        p = self._trace_shard(pid, chapter)
        if not p.exists():
            return None
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else None
        except Exception:
            return None

    def cover_path(self, pid: str) -> Path:
        """CV-1: legacy 단일 표지 바이너리 경로 {pid}.cover.png(main JSON 엔 메타만 — rag 파일 분리 선례).
        list_summaries 는 '*.json' 만 스캔하므로 .png 는 목록에 섞이지 않는다(간섭 없음)."""
        return self.dir / f"{pid}.cover.png"

    def _cover_file_path(self, pid: str, filename: str) -> Path | None:
        """CV-2: 표지 파일명 검증 후 절대경로 반환(부적합이면 None — 경로 탈출·타 프로젝트 접근 차단).
        규칙: 반드시 `{pid}.cover.` 접두 + `.png` 접미. 구분자('/','\\')·상위참조('..') 포함 시 거부."""
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            return None
        if not (filename.startswith(f"{pid}.cover.") and filename.endswith(".png")):
            return None
        return self.dir / filename

    def save_cover_bytes(self, pid: str, png: bytes, filename: str | None = None) -> str:
        """PNG 바이트 저장(원자적) → 파일명 반환.
        CV-2: filename 지정 시 그 이름으로(보관함 — 검증 통과 필수), 미지정 시 legacy {pid}.cover.png(하위호환·덮어쓰기)."""
        # XR-35: 표지 바이너리도 같은 선형화 경계 — 026 이 "잔여(고아 png 가능)"로 정직 기재했던 sink 를
        #   경계에 포함해 잔여 자체를 소거한다(재개봉 트리거 부기 불요). 삭제된 pid 는 거부(정직 실패 —
        #   호출 경로는 세션 락 안이고 직후 repo.save 가 같은 예외를 내므로 계약 동형).
        with self._mutation_lock(pid):
            if pid in self._tombstones:
                raise ProjectDeletedError(f"프로젝트가 삭제되어 표지를 저장할 수 없습니다: {pid}")
            if filename is None:
                p = self.cover_path(pid)                 # legacy 경로(하위호환)
            else:
                p = self._cover_file_path(pid, filename)
                if p is None:
                    raise ValueError(f"부적합 표지 파일명: {filename!r}")
            tmp = p.with_suffix(p.suffix + ".tmp")
            with open(tmp, "wb") as f:
                f.write(png)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(p)
            return p.name

    def read_cover_bytes(self, pid: str) -> bytes | None:
        """legacy 표지 PNG 바이트(없으면 None → 라우트 404)."""
        p = self.cover_path(pid)
        if not p.exists():
            return None
        return p.read_bytes()

    def read_cover_file(self, pid: str, filename: str) -> bytes | None:
        """CV-2: 보관함 개별 표지 PNG 바이트. 파일명 검증 실패(경로 탈출 등)·부재 시 None."""
        p = self._cover_file_path(pid, filename)
        if p is None or not p.exists():
            return None
        return p.read_bytes()

    def delete_cover_file(self, pid: str, filename: str) -> bool:
        """CV-2: 보관함 개별 표지 삭제. 검증 실패·부재 시 False.
        XR-35: 변이 sink — 같은 pid 선형화 경계(멱등 삭제라 tombstone 검사는 불요: 파일을 만들지 못한다)."""
        with self._mutation_lock(pid):
            p = self._cover_file_path(pid, filename)
            if p is None or not p.exists():
                return False
            p.unlink()
            return True

    def save(self, state: ProjectState) -> None:
        # XR-35: 검사와 변이를 한 선형화 경계에 — 락 안에서 tombstone 을 보고, 전 파일 쓰기를 락 안에서 끝낸다
        #   (락 밖 검사는 통과 후 삭제가 끼어드는 TOCTOU — 029 §1.2 부활 재현의 소스).
        with self._mutation_lock(state.id):
            # XR-34: 변이 sink 가드 — 삭제된 pid 의 저장은 부활이므로 거부(진행 중 writer 의 사후 save 차단).
            #   침묵 무시가 아니라 예외(정직 실패) — 호출자는 작업이 무효화됐음을 안다.
            if state.id in self._tombstones:
                raise ProjectDeletedError(f"프로젝트가 삭제되어 저장할 수 없습니다: {state.id}")
            self._save_inner(state)

    def _save_inner(self, state: ProjectState) -> None:
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
            # 사이드카(.rag.N/.trace.N)는 파일명으로 선차단 — 본체는 _path() 규약상 항상 "{pid}.json"(stem 에 점 없음).
            #   glob 이 같은 평면 디렉토리의 RAG 샤드까지 매칭해, 요약 8필드를 만들려고 수백 MB 를 읽고
            #   파싱 실패시켜 except 로 버리고 있었다(회차가 쌓일수록 악화 — 목록 로딩 3.4초의 85%).
            if "." in p.stem:
                continue
            try:
                s = ProjectState.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append({"id": s.id, "title": s.world.title, "genre": s.world.genre,
                        "created_at": s.created_at, "current_chapter": s.current_chapter,
                        "total_chapters": (s.seed.target_chapters or len(s.world.beats)),   # 비트 축소 후 목표 회차 기준(get_project 와 동일)
                        "cover": ({"filename": s.cover.filename, "created_at": s.cover.created_at}
                                  if s.cover else None)})   # CV-2: 목록 썸네일용 적용본 표지 요약(없으면 None)
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out

    def delete(self, project_id: str) -> bool:
        # XR-35: 같은 선형화 경계 — 진행 중 save/save_trace/표지 쓰기가 락을 쥔 동안 삭제는 대기하고,
        #   삭제가 반환된 뒤에는 어떤 이전 writer 도 파일을 새로 만들 수 없다(tombstone 검사가 같은 락 안).
        # 삭제 I/O 중간 실패 계약 = fail-closed: tombstone 은 유지된다(부활 방지 우선 — 예외는 전파,
        #   잔여 파일은 재삭제 호출로 정리 가능·본 메서드는 멱등).
        with self._mutation_lock(project_id):
            # XR-34: tombstone 선기록(파일 부재여도 — 동시 삭제 경쟁에서 진 쪽도 이후 save 가 막혀야 한다).
            #   생성 id 는 랜덤 hex 라 재사용 충돌 없음. 트레이스 사이드카는 save_trace 가 같은 집합을 보고
            #   skip(부가 관측물 — 고아 파일 생성 방지·비차단).
            # FI-3 반환 계약(033 §1): "본체가 있었는가"가 아니라 **삭제 계수 > 0** — 본체 없이 RAG/trace/
            #   표지만 남은 고아 정리도 True(구 계약은 정리하고도 False 를 반환하는 자기모순 — 033 재현).
            self._tombstones.add(project_id)
            removed = 0
            for sp in self.dir.glob(f"{project_id}.rag*.json"):   # 샤드 + 구형 사이드카 정리
                sp.unlink()
                removed += 1
            for tp in self.dir.glob(f"{project_id}.trace.*.json"):   # GA-1: 생성 트레이스 사이드카 동반 삭제(고아 방지)
                tp.unlink()
                removed += 1
            for cp in self.dir.glob(f"{project_id}.cover*.png"):   # CV-2: legacy + 보관함 표지 전량 정리(고아 방지)
                cp.unlink()
                removed += 1
            p = self._path(project_id)
            if p.exists():
                p.unlink()
                removed += 1
            return removed > 0
