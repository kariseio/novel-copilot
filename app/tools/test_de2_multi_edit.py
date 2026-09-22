# -*- coding: utf-8 -*-
"""DE-2 검증 — 작가 직접 편집의 다중 구간 교체(edit_chapter edits=[...]) 계약. 실 LLM 0콜(모의).

배경: 직접 편집(DE-1)의 웹 UI가 '좌 원본/우 편집 전문 에디터 + hunk diff 검토'로 재설계되며, 클라이언트가
바뀐 대목(hunk)들을 구간 교체 목록으로 모아 한 요청으로 보낸다. 서버는 전부 검증 후 원자 적용(전부 아니면
전무)해야 한다 — 전체 본문 덮어쓰기(new_text)가 아니라 구간 목록 계약(사용자 명시 요구). 기계는 DE-1과
동일하게 accept_revision 폴백 경로에 위임(중복 구현 금지)한다.

잠그는 계약:
 ① 다중 위임 — 한 문장 안 인접 2구간 교체로 after_text 정확(오른쪽부터 적용 증명), revision_id "edit-"
    접두, directive "[직접 편집] 구간 2곳", span_text_fb == "".
 ② 단일 원소 리스트 — span_text_fb == 그 span, directive "[직접 편집]".
 ③ 원소별 검증 — 0회/2회+ 각각 ValueError + 사유에 1-기반 구간 번호. 등장 계수는 겹침 포함(+1 전진) —
    "X\\nX\\nX" 속 "X\\nX"(자기겹침 2위치)를 1회로 오인하지 않는다((b) 단일 구간 모드도 동일 계수기).
 ④ 겹침 — 겹치는 구간이 있으면 ValueError(어느 구간끼리인지 번호).
 ⑤ 빈 리스트 — ValueError.
 ⑥ 배타 — edits + new_text, edits + span_text/replacement 각각 ValueError.
 ⑦ 원소 무변경 — span==replacement 인 원소가 있으면 ValueError.
 ⑧ 423 — accept_revision 이 None(락 경합) 반환 시 그대로 None 전파.
 ⑨ EditRequest 파싱 — edits 원소 pydantic 검증(필수 키 누락 시 ValidationError).

실행: PYTHONPATH=app py -3.12 tools/test_de2_multi_edit.py  (또는 pytest)
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


# ───────────────────────── 모의 세션 스캐폴딩(LLM 0콜, DE-1/RV-2 픽스처 관행 재사용) ─────────────────────────
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
    tmp = Path(tempfile.mkdtemp(prefix="de2svc_"))
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


def _capture_accept(captured: dict):
    """accept_revision monkeypatch — 위임 인자를 캡처하고 성공 dict 반환."""
    def _fake_accept(pid, chapter_no, revision_id, after_text_fb=None,
                     span_text_fb=None, passes_fb=None, directive_fb="(폴백)"):
        captured.update(pid=pid, chapter_no=chapter_no, revision_id=revision_id,
                        after_text_fb=after_text_fb, span_text_fb=span_text_fb,
                        directive_fb=directive_fb)
        return {"accepted": True}
    return _fake_accept


# ───────────────────────── ① 다중 위임(오른쪽부터 적용 증명) ─────────────────────────
def test_multi_edit_delegates_right_to_left(monkeypatch):
    svc = _svc(_ch())
    captured = {}
    monkeypatch.setattr(svc, "accept_revision", _capture_accept(captured))
    # 왼쪽 구간은 길이가 늘어난다("검을 뽑았다"5자→"칼을 뽑아 들었다"8자). 만약 왼쪽부터(오름차순)
    # 미리 구한 인덱스로 적용하면 이 길이 변화가 오른쪽 구간 인덱스를 밀어 결과가 깨진다 —
    # 아래 정확한 after 는 오른쪽부터(내림차순) 적용해야만 성립한다. 입력 순서는 [왼, 오]이지만
    # 서비스가 내부에서 시작 오프셋으로 정렬하므로 순서와 무관하다.
    out = svc.edit_chapter("p1", 1, edits=[
        {"span_text": "검을 뽑았다", "replacement": "칼을 뽑아 들었다"},
        {"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"},
    ])
    assert out == {"accepted": True}
    assert captured["pid"] == "p1" and captured["chapter_no"] == 1
    assert captured["after_text_fb"] == "진우는 칼을 뽑아 들었다. 빛이 번졌다. 그는 뒤돌아 달렸다."
    assert captured["revision_id"].startswith("edit-")
    assert captured["directive_fb"] == "[직접 편집] 구간 2곳"
    assert captured["span_text_fb"] == ""   # 다중 → span 은 비운다


def test_multi_edit_input_order_independent(monkeypatch):
    # 입력을 [오른쪽, 왼쪽]으로 뒤집어도 서비스가 오프셋 정렬 → 동일 after.
    svc = _svc(_ch())
    captured = {}
    monkeypatch.setattr(svc, "accept_revision", _capture_accept(captured))
    svc.edit_chapter("p1", 1, edits=[
        {"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"},
        {"span_text": "검을 뽑았다", "replacement": "칼을 뽑아 들었다"},
    ])
    assert captured["after_text_fb"] == "진우는 칼을 뽑아 들었다. 빛이 번졌다. 그는 뒤돌아 달렸다."


# ───────────────────────── ② 단일 원소 리스트 ─────────────────────────
def test_single_item_list_carries_span_and_directive(monkeypatch):
    svc = _svc(_ch())
    captured = {}
    monkeypatch.setattr(svc, "accept_revision", _capture_accept(captured))
    svc.edit_chapter("p1", 1, edits=[{"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"}])
    assert captured["after_text_fb"] == "진우는 검을 뽑았다. 빛이 번졌다. 그는 뒤돌아 달렸다."
    assert captured["span_text_fb"] == "어둠이 몰려왔다"     # 단일 → 그 span
    assert captured["directive_fb"] == "[직접 편집]"          # 단일 → 곳 수 없이
    assert captured["revision_id"].startswith("edit-")


# ───────────────────────── ③ 원소별 검증(0회/2회+ + 구간 번호) ─────────────────────────
def test_element_zero_occurrence_raises_with_number():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, edits=[
            {"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"},
            {"span_text": "본문에없는구절", "replacement": "X"},
        ])
    msg = str(ei.value)
    assert "구간 2" in msg and "본문에 없는 구간" in msg


def test_element_multiple_occurrence_raises_with_number():
    svc = _svc(_ch(text="그는 갔다. 그는 왔다. 끝."))
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, edits=[
            {"span_text": "끝", "replacement": "종료"},
            {"span_text": "그는 ", "replacement": "그가 "},   # 2회 등장
        ])
    msg = str(ei.value)
    assert "구간 2" in msg and "여러 곳" in msg


def test_self_overlapping_occurrences_rejected_multi():
    # ③c 겹침 포함 등장 계수 잠금 — "X\nX\nX" 속 "X\nX" 는 str.count(겹침 배제)로는 1회지만 실제
    #   위치는 2곳(1~2번째·2~3번째 문단). 첫 위치 적용이 작가 의도(둘째 위치)와 어긋날 수 있으므로
    #   '여러 곳과 일치'로 정직 거절해야 한다(DE-2 클라 퍼즈 실측 — 겹침 포함 +1 전진 계수).
    svc = _svc(_ch(text="바람이 불었다.\n바람이 불었다.\n바람이 불었다."))
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, edits=[
            {"span_text": "바람이 불었다.\n바람이 불었다.", "replacement": "바람이 불었다.\n비가 내렸다."},
        ])
    msg = str(ei.value)
    assert "구간 1" in msg and "여러 곳" in msg


def test_self_overlapping_occurrences_rejected_single_mode():
    # ③c 동일 규칙이 기존 (b) 단일 구간 모드에도 적용 — DE-1 잠복 결함의 소스 수리(같은 계수기 공유).
    svc = _svc(_ch(text="바람이 불었다.\n바람이 불었다.\n바람이 불었다."))
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, span_text="바람이 불었다.\n바람이 불었다.",
                         replacement="바람이 불었다.\n비가 내렸다.")
    assert "여러 곳" in str(ei.value)


# ───────────────────────── ④ 겹침 ─────────────────────────
def test_overlapping_spans_raise():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, edits=[
            {"span_text": "검을 뽑았다", "replacement": "칼을 들었다"},
            {"span_text": "뽑았다. 어둠", "replacement": "휘둘렀다. 빛"},   # 앞 구간과 겹침
        ])
    msg = str(ei.value)
    assert "겹칩" in msg and "구간 1" in msg and "구간 2" in msg


def test_duplicate_span_element_caught_as_overlap():
    # 동일 span 중복 원소 — 각자 1회 등장은 통과하나 같은 시작 오프셋으로 겹침에 걸림.
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, edits=[
            {"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"},
            {"span_text": "어둠이 몰려왔다", "replacement": "어둠이 걷혔다"},
        ])
    assert "겹칩" in str(ei.value)


# ───────────────────────── ⑤ 빈 리스트 ─────────────────────────
def test_empty_edits_list_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, edits=[])
    assert "비어" in str(ei.value)


# ───────────────────────── ⑥ 배타 ─────────────────────────
def test_edits_with_new_text_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, new_text="새 본문",
                         edits=[{"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"}])
    assert "함께 지정할 수 없습니다" in str(ei.value)


def test_edits_with_span_replacement_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, span_text="어둠", replacement="빛",
                         edits=[{"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"}])
    assert "함께 지정할 수 없습니다" in str(ei.value)


# ───────────────────────── ⑦ 원소 무변경 ─────────────────────────
def test_element_no_change_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.edit_chapter("p1", 1, edits=[
            {"span_text": "진우는 검을 뽑았다", "replacement": "진우는 검을 뽑았다"},   # span==replacement
        ])
    msg = str(ei.value)
    assert "변경 없음" in msg and "구간 1" in msg


# ───────────────────────── ⑧ 423(락 경합) 전파 ─────────────────────────
def test_lock_contention_propagates_none(monkeypatch):
    svc = _svc(_ch())
    monkeypatch.setattr(svc, "accept_revision", lambda *a, **k: None)   # 락 경합 = None
    out = svc.edit_chapter("p1", 1, edits=[
        {"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"},
    ])
    assert out is None


# ───────────────────────── ⑨ EditRequest 파싱(pydantic) ─────────────────────────
def test_edit_request_parses_edits():
    from novelcopilot.api.schemas import EditRequest
    req = EditRequest(edits=[{"span_text": "a", "replacement": "b"},
                             {"span_text": "c", "replacement": "d"}])
    assert req.edits is not None and len(req.edits) == 2
    assert req.edits[0].span_text == "a" and req.edits[1].replacement == "d"
    assert req.new_text is None and req.span_text == ""   # 기존 필드 기본값 불변


def test_edit_request_missing_key_raises_validation():
    from pydantic import ValidationError
    from novelcopilot.api.schemas import EditRequest
    with pytest.raises(ValidationError):
        EditRequest(edits=[{"span_text": "a"}])   # replacement 필수 키 누락


# ───────────────────────── ③b 위임 끝단까지(실 accept_revision) ─────────────────────────
def test_multi_edit_end_to_end_records_directive():
    # monkeypatch 없이 실 accept_revision 관통 — 본문이 원자 교체되고 이력 directive 가 "구간 2곳".
    svc = _svc(_ch())
    res = svc.edit_chapter("p1", 1, edits=[
        {"span_text": "검을 뽑았다", "replacement": "칼을 뽑아 들었다"},
        {"span_text": "어둠이 몰려왔다", "replacement": "빛이 번졌다"},
    ])
    assert res["accepted"] is True
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text == "진우는 칼을 뽑아 들었다. 빛이 번졌다. 그는 뒤돌아 달렸다."
    assert ch.revisions[-1].directive == "[직접 편집] 구간 2곳"
    assert ch.revisions[-1].revision_id.startswith("edit-")
    # undo 가능(정식 이력) — 되돌리면 원문 복원(원자 편집 전체가 한 리비전).
    svc.undo_revision("p1", 1)
    assert svc.repo.get("p1").chapter(1).text == BODY


def main() -> int:
    # pytest 픽스처(monkeypatch)를 쓰는 테스트가 있으므로 스크립트 모드에선 pytest 위임.
    return pytest.main([str(pathlib.Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    sys.exit(main())
