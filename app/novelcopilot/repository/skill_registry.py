# -*- coding: utf-8 -*-
"""스킬 라이브러리 Repository — 앱-전역 단일 카탈로그(data/skills.json).

작품은 스킬 id 만 참조(ProjectState.injected_skills)하고, 본문 정의는 여기 한 곳에 산다(참조형 live SSOT).
→ 라이브러리에서 한 번 고치면 그 스킬을 주입한 모든 작품의 *다음* 회차부터 반영.
이미 생성된 회차는 ChapterRecord.gen_context['skills'] 에 적용 텍스트가 동결(스냅샷)되어 과거는 바뀌지 않음.
프로젝트 Repository 와 분리된 *자체 앱-레벨 락* — 라이브러리 편집은 특정 작품의 회차 생성 락(423)에 막히지 않는다.
"""
from __future__ import annotations
import threading
import time
import uuid
from pathlib import Path

from ..domain.skill import Skill, SkillLibrary, default_skills
from .filesystem import _atomic_write


def _clamp_skill_fields(data: dict, sk: Skill) -> None:
    """create/update 공용 — 입력을 안전 길이로 클램프해 sk 에 반영(있는 키만)."""
    if "name" in data:
        sk.name = (data.get("name") or sk.name or "새 스킬").strip()[:40]
    if data.get("point") in ("worldgen", "chapter", "revise"):
        sk.point = data["point"]
    if "instructions" in data:
        sk.instructions = (data.get("instructions") or "").strip()[:2000]
    if "examples" in data:
        sk.examples = [str(e).strip()[:1200] for e in (data.get("examples") or []) if str(e).strip()][:5]
    if "model" in data:
        sk.model = (data.get("model") or "").strip()[:80]
    if "description" in data:
        sk.description = (data.get("description") or "").strip()[:120]


class FilesystemSkillRegistry:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "skills.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._lock:
            self._seed_builtins_locked()

    # ---- 영속 ----
    def _load(self) -> SkillLibrary:
        if not self.path.exists():
            return SkillLibrary()                       # 파일 없음 = 정당한 빈 카탈로그(시드 대상)
        raw = self.path.read_text(encoding="utf-8")     # 진짜 OSError(파일 잠금 등)는 전파 — 빈값으로 위장해 mutator 가 덮어쓰지 않게(silent wipe 방지)
        try:
            return SkillLibrary.model_validate_json(raw)
        except Exception:
            # 파일은 있으나 파싱 불가(손상·외부 편집 중) → 원본을 옆으로 보존하고 빈값으로 강등.
            # 그래야 _seed_builtins/create/add_existing 의 후속 save 가 커스텀 스킬을 '복구 불가'로 지우지 않음(적대검증).
            try:
                self.path.replace(self.path.with_suffix(f".json.corrupt-{int(time.time())}"))
            except Exception:
                pass
            return SkillLibrary()

    def _save(self, lib: SkillLibrary) -> None:
        _atomic_write(self.path, lib.model_dump_json(indent=2))

    def _seed_builtins_locked(self) -> bool:
        """내장 프리셋을 id 기준 병합(없으면 추가). 멱등. 변경 여부 반환."""
        lib = self._load()
        have = {s.id for s in lib.skills}
        missing = [b for b in default_skills() if b.id not in have]
        if missing:
            lib.skills = lib.skills + missing
            self._save(lib)
        return bool(missing)

    # ---- 조회(락 불필요 — 읽기 일관성은 단일 파일 읽기로 충분) ----
    def list(self) -> list[Skill]:
        return self._load().skills

    def get(self, sid: str) -> Skill | None:
        return next((s for s in self.list() if s.id == sid), None)

    def resolve(self, ids) -> list[Skill]:
        """id 목록 → 스킬(라이브러리에 없는 끊긴 참조는 건너뜀). 입력 순서 보존."""
        by_id = {s.id: s for s in self.list()}
        return [by_id[i] for i in (ids or []) if i in by_id]

    # ---- 변경(앱-레벨 락 — read-modify-write 직렬화) ----
    def create(self, data: dict) -> Skill:
        with self._lock:
            lib = self._load()
            sk = Skill(id=f"custom_{uuid.uuid4().hex[:8]}", name="새 스킬",
                       point="chapter", enabled=False, builtin=False)
            _clamp_skill_fields(data, sk)
            lib.skills.append(sk)
            self._save(lib)
            return sk

    def add_existing(self, sk: Skill) -> Skill:
        """기존 Skill 객체를 라이브러리에 승격(인라인→전역 이관). id 충돌 시 기존 항목 유지."""
        with self._lock:
            lib = self._load()
            existing = next((s for s in lib.skills if s.id == sk.id), None)
            if existing is not None:
                return existing
            lib.skills.append(sk)
            self._save(lib)
            return sk

    def update(self, sid: str, data: dict) -> Skill:
        with self._lock:
            lib = self._load()
            sk = next((s for s in lib.skills if s.id == sid), None)
            if not sk:
                raise ValueError("존재하지 않는 스킬")
            if sk.builtin:
                raise ValueError("내장 스킬은 편집할 수 없습니다")
            _clamp_skill_fields(data, sk)
            self._save(lib)
            return sk

    def delete(self, sid: str) -> dict:
        with self._lock:
            lib = self._load()
            sk = next((s for s in lib.skills if s.id == sid), None)
            if not sk:
                raise ValueError("존재하지 않는 스킬")
            if sk.builtin:
                raise ValueError("내장 스킬은 삭제할 수 없습니다")
            lib.skills = [s for s in lib.skills if s.id != sid]
            self._save(lib)
            return {"deleted": sid}
