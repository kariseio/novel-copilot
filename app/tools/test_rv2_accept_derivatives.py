# -*- coding: utf-8 -*-
"""RV-2 검증 — accept_revision 후 파생물 정합(감사 audit_e2e_2.md 이슈③). 실 LLM 0콜(모의).

잠그는 계약:
① accept 의 _summarize 호출이 이 회차 gen_context 의 beat 계획을 3번째 인자로 넘긴다(생성 경로 동형 —
   P-2 프로즈 슬라이스 폴백 재유입 차단). beat 소재=gen_context['draft']['beat'](또는 'plan'/'beat').
   - 인자 관통(capture) + 실 ChapterGenerator._summarize 를 태워 폴백값 발산(beat 합성 vs 프로즈 슬라이스) 실증.
② LLM 콜 파생물(wiki·promise_ledger·claim_audit·reader_feedback)은 accept 가 자동 재계산하지 않고
   derivatives_revised_stale 표식만 남긴다(비용·무강제). 존재하는 파생물만 표식(없는 건 미표식 — 오표식 금지).
③ undo 대칭: accept 전 stale 스냅샷을 ChapterRevision.before_derivatives_revised_stale 에 담고 undo 가 복원.
④ 구 JSON(신규 필드 없음) 로드가 하위호환(기본 {}·라운드트립 정합).

실행: PYTHONPATH=app py -3.12 tools/test_rv2_accept_derivatives.py  (또는 pytest)
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelcopilot.domain.project import ProjectSeed, ProjectState
from novelcopilot.domain.world import WorldConfig, StyleSpec
from novelcopilot.domain.types import ChapterRecord, ChapterStatus, ChapterRevision
from novelcopilot.domain.ledger import Promise, PromiseLedger
from novelcopilot.services.copilot import _persisted_beat


# ───────────────────────── 모의 세션 스캐폴딩(LLM 0콜) ─────────────────────────
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


class _CaptureGen:
    """_summarize 호출 인자(특히 beat)를 포착 — accept 가 beat 를 관통시키는지 검증."""
    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    def _summarize(self, text, prior="", beat=None):
        self.calls.append({"text": text, "prior": prior, "beat": beat})
        if self.boom:
            # 실패 폴백 흉내: beat 있으면 합성 요지, 없으면 프로즈 슬라이스(harness 계약과 동형 축약)
            if beat:
                syn = (beat.get("summary") or "") + " / 주요 사건: " + "; ".join(beat.get("key_events") or [])
                return (syn[:160], syn, {"degraded": True, "failure": "empty"})
            return (text[:200], text[:400], {"degraded": True, "failure": "empty"})
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


def _svc(chapter: ChapterRecord, gen=None, ledger: PromiseLedger | None = None):
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    gen = gen or _CaptureGen()
    tmp = Path(tempfile.mkdtemp(prefix="rv2svc_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions(gen)
    state = ProjectState(id="p1", seed=ProjectSeed(title="t"),
                         world=WorldConfig(title="t", synopsis="s"),
                         current_chapter=1, chapters=[chapter])
    if ledger is not None:
        state.promise_ledger = ledger
    svc.repo.save(state)
    return svc, gen


BEAT = {"title": "각성", "summary": "진우가 던전에서 각성한다",
        "key_events": ["던전 진입", "첫 각성", "위기 탈출"]}
PROSE = "진우는 검을 뽑아 어둠 속으로 달려들었다. 심장이 요동쳤다. " * 20


def _ch(**kw) -> ChapterRecord:
    base = dict(chapter=1, title="1화", status=ChapterStatus.FINALIZED, text="원본 회차 본문입니다.")
    base.update(kw)
    return ChapterRecord(**base)


# ───────────────────────── ① beat 관통(가장 핵심) ─────────────────────────
def test_persisted_beat_path_resolution():
    # gen_context['draft']['beat'] 우선, summary/key_events 담은 노드만 채택, plan(설계메타)·부재는 None.
    assert _persisted_beat(_ch(gen_context={"draft": {"beat": BEAT}})) == BEAT
    assert _persisted_beat(_ch(gen_context={"beat": BEAT})) == BEAT
    # plan 키에 summary/key_events 있으면 채택(구조상 드묾이나 계약상 후보)
    assert _persisted_beat(_ch(gen_context={"plan": BEAT})) == BEAT
    # copilot 이 실제로 넣는 plan(설계메타 — summary/key_events 없음)은 채택 안 함 → None
    assert _persisted_beat(_ch(gen_context={"plan": {"arc": "a", "episode": "e", "recent": []}})) is None
    # gen_context 부재/구 레코드 → None
    assert _persisted_beat(_ch()) is None
    assert _persisted_beat(_ch(gen_context={})) is None


def test_accept_passes_beat_to_summarize():
    # ①: accept 의 _summarize 호출이 gen_context 의 beat 를 3번째 인자로 넘긴다.
    gen = _CaptureGen()
    svc, gen = _svc(_ch(gen_context={"draft": {"beat": BEAT}}), gen=gen)
    res = svc.accept_revision("p1", 1, None, after_text_fb="퇴고된 본문입니다.")
    assert res["accepted"] is True
    assert len(gen.calls) == 1
    assert gen.calls[0]["beat"] == BEAT   # 프로즈 편집이라도 생성 시 beat 를 관통


def test_accept_no_beat_when_gencontext_absent():
    # 구 레코드(gen_context 없음) → beat=None 관통(종전 최후 프로즈 폴백 유지, 크래시 없음).
    gen = _CaptureGen()
    svc, gen = _svc(_ch(), gen=gen)
    svc.accept_revision("p1", 1, None, after_text_fb="새 본문입니다.")
    assert gen.calls[0]["beat"] is None


def test_accept_fallback_beat_synth_not_prose_slice():
    # ① 실증: 요약 LLM 실패 시 beat 있으면 합성 요지, 없으면 프로즈 슬라이스로 발산(P-2 재유입 차단).
    #   before/after 길이는 가드레일 밴드(0.5~1.8) 안이어야 하므로 before 도 긴 본문으로.
    before_long = PROSE
    after = "던전마커고유텍스트 " + PROSE   # 고유 마커가 detail 에 새면 프로즈 슬라이스 폴백(길이는 before 와 유사)
    svc_b, gen_b = _svc(_ch(text=before_long, gen_context={"draft": {"beat": BEAT}}), gen=_CaptureGen(boom=True))
    svc_b.accept_revision("p1", 1, None, after_text_fb=after)
    ch_b = svc_b.repo.get("p1").chapter(1)
    assert "던전마커고유텍스트" not in ch_b.detail_synopsis   # 프로즈 서두 재유입 없음
    assert "진우가 던전에서 각성한다" in ch_b.detail_synopsis  # beat 합성 요지
    assert ch_b.summary_degraded is True                     # degraded 플래그 영속

    svc_n, gen_n = _svc(_ch(text=before_long), gen=_CaptureGen(boom=True))   # beat 부재
    svc_n.accept_revision("p1", 1, None, after_text_fb=after)
    ch_n = svc_n.repo.get("p1").chapter(1)
    assert "던전마커고유텍스트" in ch_n.detail_synopsis        # beat 없으면 최후 프로즈 슬라이스(대조군)


def test_accept_fallback_beat_synth_with_real_summarizer():
    # ① 실 ChapterGenerator._summarize(boom provider) 를 accept 에 물려 폴백 발산 실증(모의 축약 아님).
    from novelcopilot.engine.harness import ChapterGenerator

    class _Bus:
        def emit(self, *a, **k):
            pass

    class _BoomProv:
        last_truncated = False

        def chat(self, *a, **k):
            return ""

        def chat_json(self, messages, temperature=0.0, max_tokens=0):
            return {"oneliner": "", "synopsis": ""}   # empty → 폴백 경로

    real_gen = ChapterGenerator(_BoomProv(), checker=None, style=StyleSpec(),
                                event_bus=_Bus(),
                                settings=SimpleNamespace(prev_chapter_context_chars=4000,
                                                         craft_progress=False, scene_style_anchor=False))
    after = "던전마커고유텍스트 " + PROSE
    svc, _ = _svc(_ch(text=PROSE, gen_context={"draft": {"beat": BEAT}}), gen=real_gen)
    svc.accept_revision("p1", 1, None, after_text_fb=after)
    ch = svc.repo.get("p1").chapter(1)
    assert "던전마커고유텍스트" not in ch.detail_synopsis
    assert "진우가 던전에서 각성한다" in ch.detail_synopsis and "던전 진입" in ch.detail_synopsis
    assert ch.summary_degraded is True


# ───────────────────────── ② 파생물 stale 표식(재계산 아님) ─────────────────────────
def test_accept_flags_present_llm_derivatives_stale():
    # ②: 이 회차에 실제 존재하는 LLM 콜 파생물만 stale 표식. wiki_pages_touched·claim_audit·reader_feedback·원장.
    led = PromiseLedger(promises=[
        Promise(id="pr1", text="복선 A", opened_chapter=1),          # 이 회차가 연 약속 → stale
        Promise(id="pr2", text="복선 B", opened_chapter=5, status="paid", paid_chapter=1)])  # 이 회차가 지불 → stale
    ch = _ch(wiki_pages_touched=2,
             claim_audit=[{"claim": "x", "canon": "y", "ref": 1}],
             reader_feedback={"why": "긴장", "retention_est": 0.6})
    svc, _ = _svc(ch, ledger=led)
    svc.accept_revision("p1", 1, None, after_text_fb="퇴고 후 본문입니다.")
    st = svc.repo.get("p1").chapter(1).derivatives_revised_stale
    assert st == {"wiki": True, "promise_ledger": True, "claim_audit": True, "reader_feedback": True}


def test_accept_does_not_flag_absent_derivatives():
    # ② 오표식 금지: 파생물이 이 회차에 없으면(설정 off·spine 없음·findings 0) stale 에 넣지 않는다.
    ch = _ch(wiki_pages_touched=0, claim_audit=[], reader_feedback={})
    svc, _ = _svc(ch)   # 원장 미주입(기본 빈 PromiseLedger)
    svc.accept_revision("p1", 1, None, after_text_fb="퇴고 후 본문입니다.")
    assert svc.repo.get("p1").chapter(1).derivatives_revised_stale == {}


def test_accept_flags_dialogue_ledger_stale():
    # ② DG-1: 원장 있는 회차의 accept → dialogue_ledger stale 표식(자동 재콜 0·원자료 불변).
    #    원장 없는 회차는 미표식(오표식 금지 — 기존 정직성 계약 동형).
    led_rows = [{"idx": 1, "speaker": "진우", "text": "간다."}]
    ch = _ch(dialogue_ledger=led_rows)
    svc, _ = _svc(ch)
    svc.accept_revision("p1", 1, None, after_text_fb="퇴고 후 본문입니다.")
    ch2 = svc.repo.get("p1").chapter(1)
    assert ch2.derivatives_revised_stale.get("dialogue_ledger") is True
    assert ch2.dialogue_ledger == led_rows          # 표식만 — 원장 자체는 재콜·수정 없음
    ch_no = _ch()                                    # 원장 없는 회차(구 데이터·OFF)
    svc2, _ = _svc(ch_no)
    svc2.accept_revision("p1", 1, None, after_text_fb="퇴고 후 본문입니다.")
    assert "dialogue_ledger" not in svc2.repo.get("p1").chapter(1).derivatives_revised_stale


def test_accept_no_auto_recall_of_derivatives():
    # ② 무강제: accept 는 wiki.ingest_chapter / reader_desk / claim_audit 를 재콜하지 않는다(값 불변 — 재계산 없음).
    ch = _ch(wiki_pages_touched=1,
             claim_audit=[{"claim": "old", "canon": "z", "ref": 1}],
             reader_feedback={"why": "old", "retention_est": 0.5})
    svc, _ = _svc(ch)
    svc.accept_revision("p1", 1, None, after_text_fb="퇴고 후 본문입니다.")
    ch2 = svc.repo.get("p1").chapter(1)
    # 파생물 원자료는 옛 본문 기준 그대로(자동 재콜 없음), stale 표식만 추가됨
    assert ch2.claim_audit == [{"claim": "old", "canon": "z", "ref": 1}]
    assert ch2.reader_feedback == {"why": "old", "retention_est": 0.5}
    assert ch2.derivatives_revised_stale.get("claim_audit") is True


def test_accept_merges_prior_stale():
    # ② 퇴고 반복: 직전 stale 표식과 합집합(미해소분 유지).
    ch = _ch(wiki_pages_touched=1, derivatives_revised_stale={"reader_feedback": True})
    svc, _ = _svc(ch)
    svc.accept_revision("p1", 1, None, after_text_fb="두 번째 퇴고 본문입니다.")
    st = svc.repo.get("p1").chapter(1).derivatives_revised_stale
    assert st.get("wiki") is True and st.get("reader_feedback") is True   # 신규 wiki + 직전 reader 유지


# ───────────────────────── ③ undo 대칭 복원 ─────────────────────────
def test_undo_restores_stale_snapshot():
    # ③: undo 는 stale 표식을 퇴고 *직전* 상태로 결정론 복원(퇴고가 부여한 표식 제거).
    ch = _ch(wiki_pages_touched=2, claim_audit=[{"claim": "x", "canon": "y", "ref": 1}])
    svc, _ = _svc(ch)
    svc.accept_revision("p1", 1, None, after_text_fb="퇴고 후 본문입니다.")
    ch_after = svc.repo.get("p1").chapter(1)
    assert ch_after.derivatives_revised_stale == {"wiki": True, "claim_audit": True}
    # 스냅샷이 revision 레코드에 담겼는지(직전=빈 dict)
    assert ch_after.revisions[-1].before_derivatives_revised_stale == {}
    svc.undo_revision("p1", 1)
    ch_undo = svc.repo.get("p1").chapter(1)
    assert ch_undo.derivatives_revised_stale == {}   # 퇴고 전(빈)으로 복원
    assert ch_undo.text == "원본 회차 본문입니다."


def test_undo_restores_prior_nonempty_stale():
    # ③: 직전에 이미 stale 이 있던(전 퇴고 미해소) 경우 undo 가 그 비어있지 않은 스냅샷으로 복원.
    ch = _ch(wiki_pages_touched=1, derivatives_revised_stale={"reader_feedback": True})
    svc, _ = _svc(ch)
    svc.accept_revision("p1", 1, None, after_text_fb="세 번째 퇴고 본문입니다.")
    assert svc.repo.get("p1").chapter(1).revisions[-1].before_derivatives_revised_stale == {"reader_feedback": True}
    svc.undo_revision("p1", 1)
    assert svc.repo.get("p1").chapter(1).derivatives_revised_stale == {"reader_feedback": True}


# ───────────────────────── ④ 구 JSON 하위호환 ─────────────────────────
def test_old_json_backward_compat_record():
    # ④ ChapterRecord: derivatives_revised_stale 없는 구 JSON → 기본 {}·라운드트립 정합.
    old = ('{"chapter": 3, "status": "FINALIZED", "text": "본문", '
           '"summary": "s", "detail_synopsis": "d"}')
    rec = ChapterRecord.model_validate_json(old)
    assert rec.derivatives_revised_stale == {}
    rec2 = ChapterRecord.model_validate_json(rec.model_dump_json())
    assert rec2.derivatives_revised_stale == {} and rec2.chapter == 3
    # 값 세팅 라운드트립
    rec3 = ChapterRecord(chapter=4, status=ChapterStatus.FINALIZED,
                         derivatives_revised_stale={"wiki": True})
    rec4 = ChapterRecord.model_validate_json(rec3.model_dump_json())
    assert rec4.derivatives_revised_stale == {"wiki": True}


def test_old_json_backward_compat_revision():
    # ④ ChapterRevision: before_derivatives_revised_stale 없는 구 JSON → 기본 {}·라운드트립.
    old = ChapterRevision.model_validate_json('{"revision_id": "abc"}')
    assert old.before_derivatives_revised_stale == {}
    rev = ChapterRevision(revision_id="x", before_derivatives_revised_stale={"claim_audit": True})
    rev2 = ChapterRevision.model_validate_json(rev.model_dump_json())
    assert rev2.before_derivatives_revised_stale == {"claim_audit": True}


def main() -> int:
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"[OK] {t.__name__}")
        except Exception:
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} RV-2 tests passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
