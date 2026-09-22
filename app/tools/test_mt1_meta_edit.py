# -*- coding: utf-8 -*-
"""작품 메타 편집 검증 — update_project_meta(제목·한 줄 소개·소개 작가 직접 수정). 실 LLM 0콜.

배경: 표시(작업실 헤더·라이브러리·뷰어)와 생성 컨텍스트가 읽는 SSOT 는 world(title/premise/synopsis)이고,
seed 는 최초 시드의 이력이라 불변으로 남긴다. 설정집/스타일 PUT 계보(작가 확정 수정·무강제·evict 재구성).

잠그는 계약:
 ① 수정 영속 — world.title/premise/synopsis 반영, seed 는 불변.
 ② 빈 제목(공백 포함) → ValueError. premise/synopsis 는 빈 값 허용(지우기).
 ③ 부분 수정 — None 필드는 무변경.
 ④ strip + 길이 cap(제목 200·한 줄 소개 500·소개 4000).
 ⑤ 미존재 pid → None(라우트 404 관행).
 ⑥ 저장 후 세션 evict(다음 요청이 새 메타로 엔진 재구성 — update_style_policy 관행).
 ⑦ ProjectMetaRequest 파싱 — 전 필드 선택(None 기본).

실행: cd app && PYTHONPATH=. py -3.12 -m pytest tools/test_meta_edit.py  (또는 스크립트 직접)
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import tempfile
import threading
from pathlib import Path

import pytest

from novelcopilot.domain.project import ProjectSeed, ProjectState
from novelcopilot.domain.world import WorldConfig


class _FakeSession:
    def __init__(self):
        self.lock = threading.Lock()

    def snapshot_into(self, state):
        pass


class _FakeSessions:
    def __init__(self):
        self._s = {}
        self.evicted = []

    def get_or_create(self, state):
        return self._s.setdefault(state.id, _FakeSession())

    def evict(self, pid):
        self.evicted.append(pid)
        self._s.pop(pid, None)


def _svc():
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    tmp = Path(tempfile.mkdtemp(prefix="metasvc_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions()
    state = ProjectState(id="p1", seed=ProjectSeed(title="시드 제목", premise="시드 전제"),
                         world=WorldConfig(title="원래 제목", premise="원래 한 줄", synopsis="원래 소개"))
    svc.repo.save(state)
    return svc


# ───────────────────────── ① 수정 영속(seed 불변) ─────────────────────────
def test_meta_update_persists_and_seed_immutable():
    svc = _svc()
    out = svc.update_project_meta("p1", title="새 제목", premise="새 한 줄", synopsis="새 소개")
    assert out == {"updated": True, "title": "새 제목", "premise": "새 한 줄", "synopsis": "새 소개"}
    st = svc.repo.get("p1")
    assert st.world.title == "새 제목" and st.world.premise == "새 한 줄" and st.world.synopsis == "새 소개"
    assert st.seed.title == "시드 제목" and st.seed.premise == "시드 전제"   # 이력 불변


# ───────────────────────── ② 빈 제목 거절·소개는 지우기 허용 ─────────────────────────
def test_empty_title_rejected():
    svc = _svc()
    with pytest.raises(ValueError) as ei:
        svc.update_project_meta("p1", title="   ")
    assert "제목" in str(ei.value)
    assert svc.repo.get("p1").world.title == "원래 제목"   # 불변


def test_empty_premise_synopsis_allowed():
    svc = _svc()
    svc.update_project_meta("p1", premise="", synopsis="")
    st = svc.repo.get("p1")
    assert st.world.premise == "" and st.world.synopsis == ""
    assert st.world.title == "원래 제목"   # 미지정 필드 무변경


# ───────────────────────── ③ 부분 수정(None 무변경) ─────────────────────────
def test_partial_update_title_only():
    svc = _svc()
    svc.update_project_meta("p1", title="제목만 변경")
    st = svc.repo.get("p1")
    assert st.world.title == "제목만 변경"
    assert st.world.premise == "원래 한 줄" and st.world.synopsis == "원래 소개"


# ───────────────────────── ④ strip + 무절단 전문 저장 ─────────────────────────
def test_strip_and_no_truncation():
    # 절단 전면 제거(2026-08-21): 구 200/500/4000 캡 소거 — 작가 입력은 strip 만 하고 전문 저장(조용한 유실 0)
    svc = _svc()
    svc.update_project_meta("p1", title="  공백 제목  ", premise="ㅍ" * 900, synopsis="ㅅ" * 5000)
    st = svc.repo.get("p1")
    assert st.world.title == "공백 제목"
    assert len(st.world.premise) == 900 and len(st.world.synopsis) == 5000


# ───────────────────────── ⑤ 미존재 pid ─────────────────────────
def test_unknown_pid_returns_none():
    svc = _svc()
    assert svc.update_project_meta("없는pid", title="x") is None


# ───────────────────────── ⑥ 세션 evict ─────────────────────────
def test_session_evicted_after_save():
    svc = _svc()
    svc.update_project_meta("p1", title="새 제목")
    assert svc.sessions.evicted == ["p1"]   # 다음 요청이 새 메타로 재구성


# ───────────────────────── ⑦ 스키마 파싱 ─────────────────────────
def test_meta_request_schema_all_optional():
    from novelcopilot.api.schemas import ProjectMetaRequest
    req = ProjectMetaRequest()
    assert req.title is None and req.premise is None and req.synopsis is None
    req2 = ProjectMetaRequest(title="t", synopsis="s")
    assert req2.title == "t" and req2.premise is None and req2.synopsis == "s"


def main() -> int:
    return pytest.main([str(pathlib.Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    sys.exit(main())
