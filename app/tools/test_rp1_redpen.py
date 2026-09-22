# -*- coding: utf-8 -*-
"""RP-1 '빨간펜' 잠금 — 표시(무강제·본문 바이트 불변)+옵트인 방향 제안 계약. 실 LLM 0콜(모의).

배경: 작가가 읽다가 '마음에 안 드는' 구간을 표시(빨간펜)하고 이유 메모를 달아 두면, 옵트인으로 AI 가 수정
'방향' 메뉴를 제안한다. 마크는 본문을 절대 만지지 않는다(무강제·본문 바이트 불변). 수집·표시 경로 LLM 0,
제안만 작가 발동 1콜. DE-2 모의 세션 스캐폴딩을 복제하고 chat_json 을 내는 fake provider 만 얹었다.

잠그는 계약:
 ① add 정상 — mark_id(12자)·created_at·count·본문 바이트 불변.
 ② add 검증 각 ValueError — 0회/2회+/자기겹침/빈 앵커/오프셋 범위 밖/span_len 0.
 ③ delete 정상·미존재 mark_id KeyError.
 ④ add·delete·suggest 의 423(락 경합 → None) 전파.
 ⑤ suggest — chat_json 모의로 suggestions·suggested_at 저장 + cross 반환 / 유효 마크 0 → ValueError(LLM 미콜) /
    형식 위반(미지 id·비문자열) → ValueError + suggestions 불변.
 ⑥ stale 파생 — add 후 edit_chapter 로 앵커 파괴 → suggest 대상 제외(유효 0), RedpenMark 에 stale 필드 부재(저장 금지).
 ⑦ 구 JSON(redpen 키 없음) 로드 → [].
 ⑧ 스키마 파싱(RedpenAddRequest·RedpenSuggestRequest).
 ⑨ _occ_capped 모듈 레벨 승격 잠금(직접 편집과 소스 단일화).

실행: PYTHONPATH=app py -3.12 tools/test_rp1_redpen.py  (또는 pytest)
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
from novelcopilot.domain.types import ChapterRecord, ChapterStatus, RedpenMark
from novelcopilot.llm.base import Usage


# ───────────────────────── 모의 세션 스캐폴딩(LLM 0콜, DE-2 픽스처 복제 + fake provider) ─────────────────────────
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


class _FakeProvider:
    """suggest 의 chat_json 을 흉내내는 provider — 응답/예외를 주입하고 usage(as_dict)·호출 로그를 노출."""
    def __init__(self, response=None, error=None):
        self.usage = Usage()
        self.response = response
        self.error = error
        self.calls = []

    def chat_json(self, messages, *, temperature=0.0, max_tokens=6000):
        self.calls.append(messages)
        self.usage.chat_calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class _FakeSession:
    def __init__(self, gen, provider=None):
        self.lock = threading.Lock()
        self.bundle = _FakeBundle(gen)
        self.provider = provider if provider is not None else _FakeProvider()

    def snapshot_into(self, state):
        pass


class _FakeSessions:
    def __init__(self, gen, provider=None):
        self._gen = gen
        self._provider = provider
        self._s = {}

    def get_or_create(self, state):
        return self._s.setdefault(state.id, _FakeSession(self._gen, self._provider))

    def evict(self, pid):
        self._s.pop(pid, None)


def _svc(chapter: ChapterRecord, provider=None):
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    tmp = Path(tempfile.mkdtemp(prefix="rp1svc_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions(_Gen(), provider=provider)
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


def _session(svc):
    """캐시된 fake 세션 획득(423 락 보유 테스트용)."""
    return svc.sessions.get_or_create(svc.repo.get("p1"))


# ───────────────────────── ① add 정상 ─────────────────────────
def test_add_mark_ok_and_body_unchanged():
    svc = _svc(_ch())
    out = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다",
                              span_start=0, span_len=2, note="밋밋함")
    assert out["count"] == 1
    mark = out["mark"]
    assert len(mark["mark_id"]) == 12          # uuid4().hex[:12]
    assert mark["created_at"]                  # 서버 time.strftime — 비어 있지 않음
    assert mark["note"] == "밋밋함"
    assert mark["anchor_text"] == "어둠이 몰려왔다" and mark["span_start"] == 0 and mark["span_len"] == 2
    assert mark["suggestions"] == [] and mark["suggested_at"] == ""
    # 본문 바이트 불변(무강제 — 마크는 본문을 만지지 않는다)
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text == BODY
    assert len(ch.redpen) == 1 and ch.redpen[0].mark_id == mark["mark_id"]


# ───────────────────────── ② add 검증(각 ValueError) ─────────────────────────
def test_add_empty_anchor_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.add_redpen_mark("p1", 1, anchor_text="", span_start=0, span_len=1)
    assert "앵커" in str(ei.value)


def test_add_span_len_zero_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=0)
    assert "길이" in str(ei.value)


def test_add_offset_out_of_range_raises():
    svc = _svc(_ch())
    # anchor len == 7; 5+5 == 10 > 7 → 범위 밖
    with pytest.raises(ValueError) as ei:
        svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=5, span_len=5)
    assert "범위" in str(ei.value)


def test_add_negative_span_start_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=-1, span_len=2)
    assert "범위" in str(ei.value)


def test_add_zero_occurrence_raises():
    svc = _svc(_ch())
    with pytest.raises(ValueError) as ei:
        svc.add_redpen_mark("p1", 1, anchor_text="본문에없는구절", span_start=0, span_len=2)
    assert "본문에 없는 구간" in str(ei.value)


def test_add_multiple_occurrence_raises():
    svc = _svc(_ch(text="가나다. 가나다."))
    with pytest.raises(ValueError) as ei:
        svc.add_redpen_mark("p1", 1, anchor_text="가나다", span_start=0, span_len=3)
    assert "여러 곳" in str(ei.value)


def test_add_self_overlapping_anchor_raises():
    # 겹침 포함 계수 — "X\nX\nX" 속 "X\nX" 는 str.count 로 1회지만 실제 위치는 2곳(0·중간).
    #   직접 편집(edit_chapter)과 같은 계수기(_occ_capped)를 공유해 '여러 곳'으로 정직 거절.
    svc = _svc(_ch(text="바람이 불었다.\n바람이 불었다.\n바람이 불었다."))
    with pytest.raises(ValueError) as ei:
        svc.add_redpen_mark("p1", 1, anchor_text="바람이 불었다.\n바람이 불었다.",
                            span_start=0, span_len=3)
    assert "여러 곳" in str(ei.value)


def test_add_missing_project_or_chapter_keyerror():
    svc = _svc(_ch())
    with pytest.raises(KeyError):
        svc.add_redpen_mark("nope", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    with pytest.raises(KeyError):
        svc.add_redpen_mark("p1", 99, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)


# ───────────────────────── ③ delete ─────────────────────────
def test_delete_ok_and_missing_keyerror():
    svc = _svc(_ch())
    add = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    mid = add["mark"]["mark_id"]
    out = svc.delete_redpen_mark("p1", 1, mid)
    assert out == {"deleted": True, "count": 0}
    assert svc.repo.get("p1").chapter(1).redpen == []
    # 본문 불변
    assert svc.repo.get("p1").chapter(1).text == BODY
    with pytest.raises(KeyError):
        svc.delete_redpen_mark("p1", 1, "존재하지않는id")


# ───────────────────────── ④ 423(락 경합) 전파 ─────────────────────────
def test_add_delete_suggest_propagate_423_on_lock():
    svc = _svc(_ch())
    add = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    mid = add["mark"]["mark_id"]
    sess = _session(svc)
    assert sess.lock.acquire(blocking=False)   # 생성 중 시뮬레이션(락 보유)
    try:
        assert svc.add_redpen_mark("p1", 1, anchor_text="검을 뽑았다", span_start=0, span_len=2) is None
        assert svc.delete_redpen_mark("p1", 1, mid) is None
        assert svc.suggest_redpen_directions("p1", 1) is None
    finally:
        sess.lock.release()


# ───────────────────────── ⑤ suggest ─────────────────────────
def test_suggest_saves_directions_and_returns_cross():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    a1 = svc.add_redpen_mark("p1", 1, anchor_text="검을 뽑았다", span_start=0, span_len=2, note="밋밋")
    a2 = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    id1, id2 = a1["mark"]["mark_id"], a2["mark"]["mark_id"]
    prov.response = {
        "marks": [
            {"mark_id": id1, "directions": ["감각 묘사를 늘려 긴장을 만든다", "행동 동사로 속도를 준다"]},
            {"mark_id": id2, "directions": ["시점 인물의 반응을 덧댄다"]},
        ],
        "cross": "두 구간 모두 반응 묘사가 얇다",
    }
    out = svc.suggest_redpen_directions("p1", 1)
    assert out["cross"] == "두 구간 모두 반응 묘사가 얇다"
    assert out["usage"]["chat_calls"] == 1      # 배치 1콜(편승 usage 계상)
    assert len(out["marks"]) == 2
    assert len(prov.calls) == 1                 # 정확히 1콜
    ch = svc.repo.get("p1").chapter(1)
    by = {m.mark_id: m for m in ch.redpen}
    assert by[id1].suggestions == ["감각 묘사를 늘려 긴장을 만든다", "행동 동사로 속도를 준다"]
    assert by[id1].suggested_at != ""
    assert by[id2].suggestions == ["시점 인물의 반응을 덧댄다"] and by[id2].suggested_at != ""
    # 본문 불변
    assert ch.text == BODY


def test_suggest_mark_ids_subset_targets_only_named():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    a1 = svc.add_redpen_mark("p1", 1, anchor_text="검을 뽑았다", span_start=0, span_len=2)
    a2 = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    id1, id2 = a1["mark"]["mark_id"], a2["mark"]["mark_id"]
    prov.response = {"marks": [{"mark_id": id1, "directions": ["더 짧게"]}], "cross": ""}
    out = svc.suggest_redpen_directions("p1", 1, mark_ids=[id1])
    assert len(out["marks"]) == 1 and out["marks"][0]["mark_id"] == id1
    # 지정 안 한 마크는 불변
    ch = svc.repo.get("p1").chapter(1)
    by = {m.mark_id: m for m in ch.redpen}
    assert by[id2].suggestions == [] and by[id2].suggested_at == ""


def test_suggest_unknown_target_mark_id_keyerror():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    with pytest.raises(KeyError):
        svc.suggest_redpen_directions("p1", 1, mark_ids=["없는id"])
    assert prov.calls == []


def test_suggest_no_valid_marks_raises():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    with pytest.raises(ValueError) as ei:
        svc.suggest_redpen_directions("p1", 1)
    assert "유효한 빨간펜이 없습니다" in str(ei.value)
    assert prov.calls == []      # 유효 0 → LLM 미콜


def test_suggest_unknown_response_mark_id_raises_and_unchanged():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    a = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    prov.response = {"marks": [{"mark_id": "정체불명", "directions": ["x"]}], "cross": ""}
    with pytest.raises(ValueError):
        svc.suggest_redpen_directions("p1", 1)
    # suggestions 불변(폴백 조작 0 — 검증 통과 전 저장 없음)
    m = svc.repo.get("p1").chapter(1).redpen[0]
    assert m.suggestions == [] and m.suggested_at == ""


def test_suggest_non_string_direction_raises_and_unchanged():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    a = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    id1 = a["mark"]["mark_id"]
    prov.response = {"marks": [{"mark_id": id1, "directions": [123]}], "cross": ""}
    with pytest.raises(ValueError):
        svc.suggest_redpen_directions("p1", 1)
    m = svc.repo.get("p1").chapter(1).redpen[0]
    assert m.suggestions == [] and m.suggested_at == ""


def test_suggest_empty_directions_raises_and_unchanged():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    a = svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    id1 = a["mark"]["mark_id"]
    prov.response = {"marks": [{"mark_id": id1, "directions": []}], "cross": ""}
    with pytest.raises(ValueError):
        svc.suggest_redpen_directions("p1", 1)
    m = svc.repo.get("p1").chapter(1).redpen[0]
    assert m.suggestions == [] and m.suggested_at == ""


# ───────────────────────── ⑥ stale 파생(앵커 파괴 → 대상 제외 + stale 필드 부재) ─────────────────────────
def test_stale_mark_excluded_and_no_stale_field():
    prov = _FakeProvider()
    svc = _svc(_ch(), provider=prov)
    svc.add_redpen_mark("p1", 1, anchor_text="어둠이 몰려왔다", span_start=0, span_len=2)
    # 직접 편집으로 앵커를 본문에서 제거 → 마크는 남지만 앵커 유일 탐색 실패(파생 stale)
    svc.edit_chapter("p1", 1, span_text="어둠이 몰려왔다", replacement="빛이 번졌다")
    with pytest.raises(ValueError) as ei:
        svc.suggest_redpen_directions("p1", 1)          # 유효 0(stale 제외)
    assert "유효한 빨간펜이 없습니다" in str(ei.value)
    assert prov.calls == []
    # stale 은 저장 상태가 아니라 파생 판정 — RedpenMark 에 stale 필드가 없어야(저장 금지 계약)
    assert "stale" not in RedpenMark.model_fields
    ch = svc.repo.get("p1").chapter(1)
    assert len(ch.redpen) == 1                          # 마크 자체는 살아있음(undo 시 부활 가능)
    assert "stale" not in ch.redpen[0].model_dump()


# ───────────────────────── ⑦ 구 JSON(redpen 없음) 로드 ─────────────────────────
def test_old_json_without_redpen_loads_empty():
    ch = ChapterRecord.model_validate({"chapter": 1, "status": "FINALIZED", "text": BODY})
    assert ch.redpen == []


# ───────────────────────── ⑧ 스키마 파싱 ─────────────────────────
def test_schema_parsing():
    from novelcopilot.api.schemas import RedpenAddRequest, RedpenSuggestRequest
    req = RedpenAddRequest(anchor_text="a", span_start=0, span_len=1)
    assert req.note == ""                       # 기본값
    req2 = RedpenAddRequest(anchor_text="a", span_start=2, span_len=3, note="이유")
    assert req2.span_start == 2 and req2.span_len == 3 and req2.note == "이유"
    s0 = RedpenSuggestRequest()
    assert s0.mark_ids is None                  # 미지정 → 전체 대상
    s1 = RedpenSuggestRequest(mark_ids=["x", "y"])
    assert s1.mark_ids == ["x", "y"]


def test_schema_missing_required_raises_validation():
    from pydantic import ValidationError
    from novelcopilot.api.schemas import RedpenAddRequest
    with pytest.raises(ValidationError):
        RedpenAddRequest(span_start=0, span_len=1)   # anchor_text 필수 누락


# ───────────────────────── ⑨ _occ_capped 모듈 레벨 승격 잠금 ─────────────────────────
def test_occ_capped_promoted_to_module_level():
    from novelcopilot.services.copilot import _occ_capped
    assert _occ_capped("가나다", "나") == 1
    assert _occ_capped("가나다", "라") == 0
    assert _occ_capped("가가가", "가가") == 2   # 겹침 포함(+1 전진) — str.count 였다면 1


def main() -> int:
    return pytest.main([str(pathlib.Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    sys.exit(main())
