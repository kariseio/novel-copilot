# -*- coding: utf-8 -*-
"""DE-1 검증 — 작가 직접 편집(edit_chapter)이 폴백 채택 기계에 위임하는 정식 계약. 실 LLM 0콜(모의).

배경: 본문 수정 경로가 퇴고(AI revise)→채택→undo 뿐이라 작가가 문장을 직접 고치는 정식 기능이 없었다
(사용자 요구·승인). accept_revision 의 폴백 경로(after_text_fb)가 그 기계를 이미 갖고 있음이 라이브로
실증됐다 — 이를 edit_chapter 로 정식 승격하되 accept_revision 에 위임(중복 구현 금지)한다.

잠그는 계약:
 ① 구간 검증 — span_text 가 0회/2회+ 등장하면 ValueError(정직 사유), 무변경(after==before)도 ValueError.
 ② 배타 — new_text 와 span_text/replacement 동시 지정 ValueError.
 ③ 구간 1회 → accept_revision 위임(revision_id 접두 "edit-"·directive_fb="[직접 편집]"·after_text 정확).
 ④ 전체 교체(new_text) 모드도 위임(after_text=new_text·span 빈값).
 ⑤ accept_revision 의 directive_fb 기본값이 "(폴백)" 유지(하위호환) — 폴백 경로 record.directive 확인.
 ⑥ 423(None — 락 경합) 그대로 전파.

실행: PYTHONPATH=app py -3.12 tools/test_de1_direct_edit.py  (또는 pytest)
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
from novelcopilot.domain.types import ChapterRecord, ChapterStatus


# ───────────────────────── 모의 세션 스캐폴딩(LLM 0콜, RV-2 픽스처 관행 재사용) ─────────────────────────
class _FakeExtractorVocab:
    categorical_keys: list = []
    numeric_keys: list = []

    def state_specs(self):
        return []


class _FakeChecker:
    class extractor:
        vocab = _FakeExtractorVocab()

    def check_text(self, text, ont, chapter, ids):
        class _R:
            hard: list = []
            claims: list = []
        return _R()


class _FakeOnt:
    entities: dict = {}

    def scan_present_ids(self, text):
        return []

    def name(self, eid):
        return eid


class _Gen:
    def _summarize(self, text, prior="", beat=None):
        return ("요약", "상세", None)


class _FakeRag:
    def __init__(self):
        self.indexed = []

    def index_chapter(self, n, text):
        self.indexed.append((n, text))


class _FakeBundle:
    def __init__(self, gen):
        self.ontology = _FakeOnt()
        self.checker = _FakeChecker()
        self.generator = gen
        self.rag = _FakeRag()


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


def _svc(chapter: ChapterRecord):
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    tmp = Path(tempfile.mkdtemp(prefix="de1svc_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions(_Gen())
    state = ProjectState(id="p1", seed=ProjectSeed(title="t"),
                         world=WorldConfig(title="t", synopsis="s"),
                         current_chapter=1, chapters=[chapter])
    svc.repo.save(state)
    return svc


BODY = "진우는 검을 뽑았다. 어둠이 몰려왔다. 그는 뒤돌아 달렸다."


def _ch(text: str = BODY, **kw) -> ChapterRecord:
    base = dict(chapter=1, title="1화", status=ChapterStatus.FINALIZED, text=text)
    base.update(kw)
    return ChapterRecord(**base)


# ───────────────────────── ① 구간 검증(0회/2회/무변경) ─────────────────────────
def test_span_zero_occurrence_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, span_text="본문에없는구절", replacement="X")
    assert "본문에 없는 구간" in str(ei.value)


def test_span_multiple_occurrence_raises():
    # "그는 "이 두 번 등장하는 본문 → 정확히 1회 규칙 위반.
    svc = _svc(_ch(text="그는 갔다. 그는 왔다."))
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, span_text="그는 ", replacement="그가 ")
    assert "여러 곳" in str(ei.value)


def test_no_change_raises():
    # span==replacement → after==before → 무변경 거부.
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, span_text="어둠이 몰려왔다", replacement="어둠이 몰려왔다")
    assert "변경 없음" in str(ei.value)


def test_both_modes_raises():
    # ② 배타 — new_text 와 span/replacement 동시 지정.
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, new_text="새 본문", span_text="어둠", replacement="빛")
    assert "함께 지정할 수 없습니다" in str(ei.value)


def test_span_without_replacement_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, span_text="어둠이 몰려왔다")
    assert "교체문" in str(ei.value)


# ───────────────────────── ③ 구간 1회 → accept 위임(캡처) ─────────────────────────
def test_span_edit_delegates_to_accept(monkeypatch):
    svc = _svc(_ch())
    captured = {}

    def _fake_accept(pid, chapter_no, revision_id, after_text_fb=None,
                     span_text_fb=None, passes_fb=None, directive_fb="(폴백)"):
        captured.update(pid=pid, chapter_no=chapter_no, revision_id=revision_id,
                        after_text_fb=after_text_fb, span_text_fb=span_text_fb,
                        directive_fb=directive_fb)
        return {"accepted": True}

    monkeypatch.setattr(svc, "accept_revision", _fake_accept)
    out = svc.edit_chapter("p1", 1, span_text="어둠이 몰려왔다", replacement="빛이 번졌다")
    assert out == {"accepted": True}
    assert captured["pid"] == "p1" and captured["chapter_no"] == 1
    assert captured["revision_id"].startswith("edit-")          # edit- 접두
    assert captured["directive_fb"] == "[직접 편집]"             # 직접 편집 출처
    assert captured["span_text_fb"] == "어둠이 몰려왔다"
    # after_text 정확성 — 딱 그 구간만 교체(정확히 1회)
    assert captured["after_text_fb"] == "진우는 검을 뽑았다. 빛이 번졌다. 그는 뒤돌아 달렸다."


def test_full_replace_delegates_to_accept(monkeypatch):
    # ④ 전체 교체 모드 — after_text=new_text, span 빈값.
    svc = _svc(_ch())
    captured = {}

    def _fake_accept(pid, chapter_no, revision_id, after_text_fb=None,
                     span_text_fb=None, passes_fb=None, directive_fb="(폴백)"):
        captured.update(after_text_fb=after_text_fb, span_text_fb=span_text_fb,
                        revision_id=revision_id, directive_fb=directive_fb)
        return {"accepted": True}

    monkeypatch.setattr(svc, "accept_revision", _fake_accept)
    svc.edit_chapter("p1", 1, new_text="완전히 새로 쓴 본문입니다.")
    assert captured["after_text_fb"] == "완전히 새로 쓴 본문입니다."
    assert captured["span_text_fb"] == ""
    assert captured["revision_id"].startswith("edit-")
    assert captured["directive_fb"] == "[직접 편집]"


# ───────────────────────── ③b 위임 끝단까지(실 accept_revision) ─────────────────────────
def test_span_edit_end_to_end_records_directive():
    # monkeypatch 없이 실 accept_revision 관통 — 이력 record.directive 가 "[직접 편집]"·본문 교체 확인.
    svc = _svc(_ch())
    res = svc.edit_chapter("p1", 1, span_text="어둠이 몰려왔다", replacement="빛이 번졌다")
    assert res["accepted"] is True
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text == "진우는 검을 뽑았다. 빛이 번졌다. 그는 뒤돌아 달렸다."
    assert ch.revisions[-1].directive == "[직접 편집]"
    assert ch.revisions[-1].revision_id.startswith("edit-")
    # undo 가능(정식 이력) — 되돌리면 원문 복원.
    svc.undo_revision("p1", 1)
    assert svc.repo.get("p1").chapter(1).text == BODY


# ───────────────────────── ⑤ accept_revision directive_fb 하위호환 ─────────────────────────
# after_text 는 before(BODY)와 비슷한 길이여야 가드레일 길이 밴드(0.5~1.8)를 통과 → 실 accept 관통.
_AFTER_SIMILAR = "진우는 칼을 뽑았다. 어둠이 밀려왔다. 그는 몸을 돌려 달렸다."


def test_accept_default_directive_backward_compat():
    # 폴백 경로(draft 미스)에서 directive_fb 미지정 → record.directive == "(폴백)"(기존 동작 불변).
    svc = _svc(_ch())
    svc.accept_revision("p1", 1, "", after_text_fb=_AFTER_SIMILAR)
    ch = svc.repo.get("p1").chapter(1)
    assert ch.revisions[-1].directive == "(폴백)"


def test_accept_explicit_directive_fb_recorded():
    # directive_fb 명시 → 그 값이 record.directive 로 기록(edit_chapter 가 쓰는 경로).
    svc = _svc(_ch())
    svc.accept_revision("p1", 1, "edit-abc123", after_text_fb=_AFTER_SIMILAR,
                        directive_fb="[직접 편집]")
    ch = svc.repo.get("p1").chapter(1)
    assert ch.revisions[-1].directive == "[직접 편집]"


# ───────────────────────── ⑥ 423(락 경합) 전파 ─────────────────────────
def test_lock_contention_propagates_none(monkeypatch):
    svc = _svc(_ch())
    monkeypatch.setattr(svc, "accept_revision",
                        lambda *a, **k: None)   # 락 경합 = None
    out = svc.edit_chapter("p1", 1, span_text="어둠이 몰려왔다", replacement="빛이 번졌다")
    assert out is None


# ───────────────────────── 대상 부재 ─────────────────────────
def test_missing_project_raises_keyerror():
    svc = _svc(_ch())
    with pytest.raises(KeyError):
        svc.edit_chapter("nope", 1, span_text="어둠이 몰려왔다", replacement="빛")


def test_missing_chapter_raises_keyerror():
    svc = _svc(_ch())
    with pytest.raises(KeyError):
        svc.edit_chapter("p1", 99, span_text="어둠이 몰려왔다", replacement="빛")


def main() -> int:
    import traceback
    # pytest 픽스처(monkeypatch)를 쓰는 테스트가 있으므로 스크립트 모드에선 pytest 위임.
    return pytest.main([str(pathlib.Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    sys.exit(main())
