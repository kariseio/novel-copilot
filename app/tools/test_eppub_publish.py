# -*- coding: utf-8 -*-
"""EP-PUB '외부 플랫폼 수동 발행 원장' 잠금 — 발행 표시(무업로드·본문 불변)+수정 감지+재발행/해제 계약. LLM 0콜.

배경: 툴은 어디에도 자동 업로드하지 않는다. 작가가 문피아·카카오 등에 회차를 올린 뒤 '발행함으로 표시'를
누르면 그 순간의 본문 지문·시각만 기록한다. 이후 퇴고·편집·재실현·재생성으로 본문이 바뀌면 서버가
'발행 후 수정됨(재발행 필요)'으로 파생 판정한다(저장 안 함 — publish_state()). 발행 원장은 어떤 게이트에도
연결되지 않는다(무강제). RP-1 모의 세션 스캐폴딩을 복제(provider 불필요 — LLM 0).

잠그는 계약:
 ① text_fingerprint — 결정론·정확 일치(공백 한 칸 차이도 지문 변경)·빈 본문 안정.
 ② publish_state — 미발행/발행됨/발행 후 수정됨 3상태 전이.
 ③ mark_published 정상 — published_at·fingerprint 세팅·first_publish=True·본문 바이트 불변.
 ④ 발행 후 본문 변경 → chapter_view(모든 뷰)가 publish_state="modified" 를 노출(published_* 경량 필드 동승).
 ⑤ 재발행 — 지문·시각 갱신·first_publish=False·상태 다시 published.
 ⑥ note — 첫 발행 세팅 / 재발행 시 None=유지 / ""=지움 / 공백 strip.
 ⑦ unmark_published — 원장 3필드 초기화·상태 unpublished·본문 불변.
 ⑧ 빈 본문 발행 불가(ValueError) · 미존재 작품/회차 KeyError.
 ⑨ 423(락 경합 → None) — mark·unmark 둘 다.
 ⑩ 구 JSON(발행 필드 없음) 로드 → 기본값·미발행.
 ⑪ 스키마 파싱(PublishRequest).

실행: PYTHONPATH=app py -3 tools/test_eppub_publish.py  (또는 pytest)
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
from novelcopilot.domain.types import (ChapterRecord, ChapterStatus,
                                        text_fingerprint, publish_state)


# ───────────────────────── 모의 세션 스캐폴딩(LLM 0콜 — RP-1 픽스처 복제) ─────────────────────────
class _Gen:
    def _summarize(self, text, prior="", beat=None):
        return ("요약", "상세", None)


class _FakeBundle:
    def __init__(self, gen):
        self.generator = gen


class _FakeSession:
    def __init__(self, gen):
        self.lock = threading.Lock()
        self.bundle = _FakeBundle(gen)

    def snapshot_into(self, state):
        pass


class _FakeSessions:
    def __init__(self, gen):
        self._gen = gen
        self._s = {}

    def get_or_create(self, state):
        return self._s.setdefault(state.id, _FakeSession(self._gen))

    def evict(self, pid):
        self._s.pop(pid, None)


BODY = "진우는 검을 뽑았다. 어둠이 몰려왔다. 그는 뒤돌아 달렸다."


def _ch(text: str = BODY, **kw) -> ChapterRecord:
    base = dict(chapter=1, title="1화", status=ChapterStatus.FINALIZED, text=text)
    base.update(kw)
    return ChapterRecord(**base)


def _svc(chapter: ChapterRecord):
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    tmp = Path(tempfile.mkdtemp(prefix="eppub_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions(_Gen())
    state = ProjectState(id="p1", seed=ProjectSeed(title="t"),
                         world=WorldConfig(title="t", synopsis="s"),
                         current_chapter=1, chapters=[chapter])
    svc.repo.save(state)
    return svc


def _session(svc):
    return svc.sessions.get_or_create(svc.repo.get("p1"))


def _set_text(svc, text):   # 발행 후 본문 변경(퇴고·편집 등)을 흉내 — 저장까지
    state = svc.repo.get("p1")
    state.chapter(1).text = text
    svc.repo.save(state)


# ───────────────────────── ① text_fingerprint ─────────────────────────
def test_fingerprint_deterministic_and_exact():
    assert text_fingerprint("가나다") == text_fingerprint("가나다")     # 결정론
    assert text_fingerprint("가나다") != text_fingerprint("가나 다")    # 공백 한 칸도 변경
    assert text_fingerprint("") == text_fingerprint(None)               # 빈/None 안정(동일)
    assert len(text_fingerprint("x")) == 16                             # sha256 hex[:16]


# ───────────────────────── ② publish_state 3상태 ─────────────────────────
def test_publish_state_transitions():
    r = _ch()
    assert publish_state(r) == "unpublished"
    r.published_at = "2026-08-18T00:00:00"
    r.published_fingerprint = text_fingerprint(r.text)
    assert publish_state(r) == "published"
    r.text = r.text + " 덧붙임"
    assert publish_state(r) == "modified"


# ───────────────────────── ③ mark_published 정상 ─────────────────────────
def test_mark_published_ok_and_body_unchanged():
    svc = _svc(_ch())
    out = svc.mark_published("p1", 1)
    assert out["first_publish"] is True
    assert out["publish_state"] == "published"
    assert out["published_at"]                                   # 서버 time.strftime — 비어 있지 않음
    assert out["published_fingerprint"] == text_fingerprint(BODY)
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text == BODY                                       # 본문 바이트 불변(무업로드·무강제)
    assert ch.published_at == out["published_at"]
    assert publish_state(ch) == "published"


# ───────────────────────── ④ 발행 후 변경 → 모든 뷰가 modified 노출 ─────────────────────────
def test_modified_surfaces_in_all_views():
    svc = _svc(_ch())
    svc.mark_published("p1", 1)
    _set_text(svc, BODY + " 그리고 비가 내렸다.")               # 퇴고·편집 흉내
    ch = svc.repo.get("p1").chapter(1)
    for view in ("full", "summary", "text"):
        d = svc.chapter_view(ch, view)
        assert d["publish_state"] == "modified", view
        assert d["published_at"], view                          # 경량 필드라 summary 에도 동승
        assert d["published_fingerprint"] == text_fingerprint(BODY)   # 발행 당시 지문(현재 본문과 다름)


# ───────────────────────── ⑤ 재발행(지문·시각 갱신) ─────────────────────────
def test_republish_updates_fingerprint_and_clears_modified():
    svc = _svc(_ch())
    svc.mark_published("p1", 1)
    new = BODY + " 재발행 대상 수정본."
    _set_text(svc, new)
    assert publish_state(svc.repo.get("p1").chapter(1)) == "modified"
    out = svc.mark_published("p1", 1)                            # 수정본을 다시 올린 뒤 재발행
    assert out["first_publish"] is False
    assert out["publish_state"] == "published"
    assert out["published_fingerprint"] == text_fingerprint(new)
    assert publish_state(svc.repo.get("p1").chapter(1)) == "published"


# ───────────────────────── ⑥ note 처리 ─────────────────────────
def test_note_set_keep_clear_and_strip():
    svc = _svc(_ch())
    svc.mark_published("p1", 1, note="  문피아 123화  ")            # 공백 strip
    assert svc.repo.get("p1").chapter(1).published_note == "문피아 123화"
    _set_text(svc, BODY + " a")
    svc.mark_published("p1", 1, note=None)                         # None=기존 메모 유지(재발행 편의)
    assert svc.repo.get("p1").chapter(1).published_note == "문피아 123화"
    _set_text(svc, BODY + " b")
    svc.mark_published("p1", 1, note="")                           # ""=지움
    assert svc.repo.get("p1").chapter(1).published_note == ""


# ───────────────────────── ⑦ unmark_published ─────────────────────────
def test_unmark_resets_ledger_and_body_unchanged():
    svc = _svc(_ch())
    svc.mark_published("p1", 1, note="카카오")
    out = svc.unmark_published("p1", 1)
    assert out["publish_state"] == "unpublished"
    ch = svc.repo.get("p1").chapter(1)
    assert ch.published_at == "" and ch.published_fingerprint == "" and ch.published_note == ""
    assert ch.text == BODY                                        # 본문 불변


# ───────────────────────── ⑧ 발행 불가·미존재 ─────────────────────────
def test_empty_text_cannot_publish():
    svc = _svc(_ch(text="   "))
    with pytest.raises(ValueError) as ei:
        svc.mark_published("p1", 1)
    assert "본문이 없어" in str(ei.value)


def test_missing_project_or_chapter_keyerror():
    svc = _svc(_ch())
    for fn in (svc.mark_published, svc.unmark_published):
        with pytest.raises(KeyError):
            fn("nope", 1)
        with pytest.raises(KeyError):
            fn("p1", 99)


# ───────────────────────── ⑨ 423(락 경합) 전파 ─────────────────────────
def test_mark_unmark_propagate_423_on_lock():
    svc = _svc(_ch())
    svc.mark_published("p1", 1)
    sess = _session(svc)
    assert sess.lock.acquire(blocking=False)                      # 생성/편집 중 시뮬(락 보유)
    try:
        assert svc.mark_published("p1", 1) is None                # → 라우트 423
        assert svc.unmark_published("p1", 1) is None
    finally:
        sess.lock.release()


# ───────────────────────── ⑩ 구 JSON(발행 필드 없음) 로드 ─────────────────────────
def test_old_json_without_publish_fields_loads_unpublished():
    ch = ChapterRecord.model_validate({"chapter": 1, "status": "FINALIZED", "text": BODY})
    assert ch.published_at == "" and ch.published_fingerprint == "" and ch.published_note == ""
    assert publish_state(ch) == "unpublished"


# ───────────────────────── ⑪ 스키마 파싱 ─────────────────────────
def test_schema_parsing():
    from novelcopilot.api.schemas import PublishRequest
    assert PublishRequest().note is None                          # 미지정 → 기존 메모 유지 신호
    assert PublishRequest(note="문피아").note == "문피아"
    assert PublishRequest(note="").note == ""                     # 지움 신호


def main() -> int:
    return pytest.main([str(pathlib.Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    sys.exit(main())
