# -*- coding: utf-8 -*-
"""IN-12 재생성 이벤트 영속 로깅 — FE-1(단순 재생성)·FE-2(점검 반영 재생성) 실행 시
회차 번호·누적 횟수·fix 그룹 선택 여부·타임스탬프를 ProjectState.regen_events 에 append-only 영속.
순수 계측(LLM 0콜·결정론) — 분석·판정·자동반응 없음(유일한 비아첨 실행동 신호의 '원천'만 확보).

계약:
  ① FE-1: fix_selected=False / FE-2: fix_selected=True(공백 fix 는 FE-1 취급 — generate 의 strip 게이트와 동일 기준)
  ② seq = 같은 회차의 누적 재생성 횟수(1-base, 코드 계산)
  ③ append-only 생존: 되돌리기(undo)·pregen 스냅샷 복원이 로그를 되감지 않는다
  ④ 하위호환: regen_events 없는 구 JSON 무변경 로드([] 기본)
  ⑤ 재생성 미실행(대상 없음·거부) 시 이벤트 0 — '실행동'만 기록
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
import tempfile
import threading
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectState, ProjectSeed, RegenEvent
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.domain.world import WorldConfig
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService


class _FakeSession:
    def __init__(self):
        self.lock = threading.Lock()


class _FakeSessions:
    """regenerate/restore 경로는 세션에서 lock·evict 만 쓴다 — provider/엔진 빌드 0(API 키 불요·LLM 0콜)."""
    def __init__(self):
        self._s = {}

    def get_or_create(self, state):
        return self._s.setdefault(state.id, _FakeSession())

    def evict(self, pid):
        self._s.pop(pid, None)


def _ch(n, text="본문"):
    return ChapterRecord(chapter=n, title=f"{n}화", status=ChapterStatus.FINALIZED, text=text)


def _svc_state(n_chapters=2):
    """임시 data_dir 서비스 + n_chapters 회차 상태(라이브 app/data 미접촉). start_generation 은 스텁 —
    IN-12 는 '실행 기록'까지가 계약이고 실제 생성은 기존 경로(별도 테스트) 소관."""
    tmp = Path(tempfile.mkdtemp())
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions()
    calls = []
    svc.start_generation = lambda pid, directive_text=None, fix_instruction=None: (
        calls.append({"pid": pid, "fix": fix_instruction}) or ("job", True))
    state = ProjectState(id="p1", seed=ProjectSeed(title="t"),
                         world=WorldConfig(title="t", synopsis="s"),
                         current_chapter=n_chapters,
                         chapters=[_ch(i + 1) for i in range(n_chapters)])
    svc.repo.save(state)
    return svc, calls


# ---------- ① FE-1: 단순 재생성 이벤트 ----------
def test_fe1_simple_regen_logs_event():
    svc, calls = _svc_state()
    res = svc.regenerate_last_chapter("p1")
    assert res == ("job", True) and calls == [{"pid": "p1", "fix": None}]
    st = svc.repo.get("p1")
    assert len(st.regen_events) == 1
    ev = st.regen_events[0]
    assert ev.chapter == 2 and ev.seq == 1 and ev.fix_selected is False
    assert ev.at                                              # 서버 타임스탬프 기록
    assert st.current_chapter == 1 and len(st.chapters) == 1  # 기존 truncate 계약 불변(계측이 동작을 안 바꿈)


# ---------- ① FE-2: 점검 반영 재생성 + ② 누적 횟수 ----------
def test_fe2_fix_regen_flag_and_cumulative_count():
    svc, calls = _svc_state()
    svc.regenerate_last_chapter("p1")                          # 1번째(FE-1)
    st = svc.repo.get("p1")
    st.chapters.append(_ch(2, "재생성 본문"))                   # 생성 완료 시뮬레이션(스텁이라 수동 복원)
    st.current_chapter = 2
    svc.repo.save(st)
    svc.regenerate_last_chapter("p1", fix_instruction="[점검 반영] 1) 확정 설정을 지켜라")   # 2번째(FE-2)
    evs = svc.repo.get("p1").regen_events
    assert [(e.chapter, e.seq, e.fix_selected) for e in evs] == [(2, 1, False), (2, 2, True)]
    assert calls[-1]["fix"] == "[점검 반영] 1) 확정 설정을 지켜라"   # 기존 fix 전달 계약 불변


def test_blank_fix_counts_as_simple_regen():
    # 공백 fix 는 generate_next_chapter 의 strip 게이트처럼 '반영 안 됨' — fix_selected=False 로 정직 기록
    svc, _ = _svc_state()
    svc.regenerate_last_chapter("p1", fix_instruction="   ")
    ev = svc.repo.get("p1").regen_events[0]
    assert ev.fix_selected is False


# ---------- ③ append-only 생존: undo·스냅샷 복원이 로그를 못 되감음 ----------
def test_undo_restore_keeps_event_log():
    svc, _ = _svc_state()
    svc.regenerate_last_chapter("p1", fix_instruction="fix")
    out = svc.restore_last_regen("p1")
    assert out and out["current_chapter"] == 2                 # 본문·상태는 원복
    st = svc.repo.get("p1")
    assert len(st.chapters) == 2 and st.has_regen_backup is False
    assert [(e.chapter, e.seq, e.fix_selected) for e in st.regen_events] == [(2, 1, True)]   # '실행했다' 사실은 유지


def test_pregen_snapshot_excludes_and_restore_does_not_rewind_log():
    svc, _ = _svc_state()
    st = svc.repo.get("p1")
    st.regen_events.append(RegenEvent(chapter=2, seq=1, fix_selected=False, at="T0"))
    svc.repo.save(st)
    svc._save_pregen_snapshot(st)                              # 다음 회차 '직전' 스냅샷(regen_events 미포함이어야)
    snap = json.loads(svc._regen_snapshot_path("p1").read_text(encoding="utf-8"))
    assert "regen_events" not in snap                          # 계측 로그는 파생 서사상태 아님 — 스냅샷 제외
    svc.regenerate_last_chapter("p1")                          # 스냅샷 복원 경로 경유 — 로그가 T0 시점으로 안 되감김
    evs = svc.repo.get("p1").regen_events
    assert [(e.chapter, e.seq) for e in evs] == [(2, 1), (2, 2)]


# ---------- ④ 하위호환: 구 JSON 무변경 로드 ----------
def test_old_json_without_field_loads_with_default():
    svc, _ = _svc_state()
    p = svc.repo._path("p1")
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("regen_events", None)                                # 구 JSON 시뮬레이션(필드 자체가 없음)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    st = svc.repo.get("p1")
    assert st.regen_events == []                               # pydantic 기본값으로 무변경 로드
    svc.regenerate_last_chapter("p1")                          # 구 상태에서도 첫 이벤트 seq=1
    assert [(e.chapter, e.seq) for e in svc.repo.get("p1").regen_events] == [(2, 1)]


# ---------- ⑤ 실행 안 된 재생성은 기록 0(순수 실행동 신호) ----------
def test_no_event_when_nothing_to_regen():
    svc, calls = _svc_state(n_chapters=0)
    assert svc.regenerate_last_chapter("p1") is None           # 회차 없음 → 거부(기존 계약)
    assert svc.repo.get("p1").regen_events == [] and calls == []
