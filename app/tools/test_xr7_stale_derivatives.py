# -*- coding: utf-8 -*-
"""XR-7 검증 — stale 파생물 소비 차단(사전 점검 가시화 · 유령 인용 필터 · 작가 발동 재계산 · undo 대칭).

설계: docs/design-xr-batch.md §3. 실 LLM 0콜(모의 provider — conftest 잠금과 정합).

잠그는 계약(설계 §3.5 6건):
① `readiness.stale_derivatives(state)` — 표식 혼재 픽스처에서 목록 정확 · 생성 비차단(게이트 없음).
② 발췌 앵커 결정론 필터 — (a) 인용 전부 실재하면 재료·조회 응답 바이트 동일 (b) 1행 부재면 그 회차 통째
   제외 + emit payload 정확 (c) gen_tools OFF 면 원장 재료가 애초에 소비되지 않는다(무동작).
③ recompute — name 별 디스패치 · stale 키 제거 · derivatives_recomputed 각인 · promise_ledger/미지명/
   파생물 없는 회차 400(ValueError).
④ undo 대칭 — accept→recompute→undo 시 그 파생물 재-stale + 각인 제거. recompute 없는 undo 는 무변경.
⑤ 하위호환 — derivatives_recomputed 없는 구 JSON 라운드트립 무변경 · wiki ingest force 기본 False 경로 동일.
⑥ K2 — 사전 점검·필터 경로의 LLM 콜 0 · 자동 재계산 호출부 0(작가 발동 엔드포인트만).

실행: PYTHONPATH=. py -3.12 tools/test_xr7_stale_derivatives.py  (또는 pytest)
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ast
import tempfile
import threading
from pathlib import Path

from novelcopilot.domain.project import ProjectSeed, ProjectState
from novelcopilot.domain.world import WorldConfig
from novelcopilot.domain.types import ChapterRecord, ChapterStatus, ChapterRevision
from novelcopilot.engine.readiness import stale_derivatives
from novelcopilot.engine.lookup import CanonLookup
from novelcopilot.services.copilot import _verified_prev_ledgers


# ───────────────────────── 모의 스캐폴딩(LLM 0콜) ─────────────────────────
class _Usage:
    def __init__(self):
        self.chat_calls = 0
        self.chat_tokens = 0

    def as_dict(self):
        return {"chat_calls": self.chat_calls, "chat_tokens": self.chat_tokens}


class _FakeProvider:
    """system 프롬프트의 표식 문구로 응답을 라우팅하는 모의 provider(실 네트워크 0)."""
    ROUTES = [
        ("인물카드 관리자", {"pages": [{"id": "hero", "body": "상태: 재계산된 카드"}]}),
        ("연속성 감수자", {"contradictions": [{"claim": "새 진술", "canon": "옛 진술",
                                              "ref": "1", "why": "양립 불가"}]}),
        ("뒤로가기", {"drop": True, "kill_trigger": "여기서 멈칫", "hate_comment": "지루함",
                    "retention_est": 42, "why": "속도 저하"}),
        ("대사 화자 표기", {"speakers": {"1": "진우"}}),
    ]

    def __init__(self):
        self.usage = _Usage()
        self.seen: list[str] = []

    def chat_json(self, messages, temperature=0.0, max_tokens=0):
        self.usage.chat_calls += 1
        self.usage.chat_tokens += 100
        blob = "\n".join(str(m.get("content", "")) for m in messages)
        self.seen.append(blob)
        for needle, res in self.ROUTES:
            if needle in blob:
                return res
        return {}

    def chat(self, *a, **k):
        self.usage.chat_calls += 1
        return ""

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class _Ent:
    def __init__(self, eid, name, etype="character", aliases=None, voice=""):
        self.id = eid
        self.name = name
        self.etype = etype
        self.aliases = list(aliases or [])
        self.voice = voice


class _FakeOnt:
    def __init__(self, ents=()):
        self.entities = {e.id: e for e in ents}
        self.edges: list = []

    def scan_present_ids(self, text):
        return [e.id for e in self.entities.values() if e.name and e.name in (text or "")]

    def is_actor(self, etype):
        return etype == "character"

    def name(self, eid):
        e = self.entities.get(eid)
        return getattr(e, "name", eid)


class _FakeRag:
    def __init__(self, past=()):
        self.indexed = []
        self._past = list(past)

    def index_chapter(self, n, text):
        self.indexed.append((n, text))
        return 1

    def search(self, query, as_of, k=4):
        return list(self._past)


class _Past:
    def __init__(self, ref, text):
        self.ref = ref
        self.text = text


class _FakeBus:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, node, event, **payload):
        self.events.append({"node": node, "event": event, **payload})

    def reset(self):
        self.events.clear()


class _FakeGen:
    """accept 재요약 관통(RV-2 계약)과 XR-7 의 POV 도출만 흉내(LLM 0콜)."""
    def _summarize(self, text, prior="", beat=None):
        return ("요약", "상세", None)

    @staticmethod
    def _pov_entity_id(ontology):
        return ""


class _FakeChecker:
    class extractor:
        class vocab:
            categorical_keys: list = []
            numeric_keys: list = []

            @staticmethod
            def state_specs():
                return []

    def check_text(self, text, ont, chapter, ids):
        class _R:
            hard: list = []
            claims: list = []
        return _R()


class _FakeBundle:
    def __init__(self, provider, ont, rag):
        from novelcopilot.engine.wiki import Wiki
        self.ontology = ont
        self.checker = _FakeChecker()
        self.generator = _FakeGen()
        self.rag = rag
        self.wiki = Wiki(provider)
        self.event_bus = _FakeBus()


class _FakeSession:
    def __init__(self, provider, ont, rag):
        self.lock = threading.Lock()
        self.provider = provider
        self.aux_provider = provider
        self.bundle = _FakeBundle(provider, ont, rag)

    @property
    def bus(self):
        return self.bundle.event_bus

    def snapshot_into(self, state):
        state.wiki_pages = self.bundle.wiki.export_pages()
        state.wiki_log = list(self.bundle.wiki.log)


class _FakeSessions:
    def __init__(self, sess):
        self._sess = sess

    def get_or_create(self, state):
        return self._sess

    def current(self, pid):   # XR-30: 매니저 현재 세션 조회(고정 픽스처=단일 세대)
        return self._sess

    def evict(self, pid):
        pass

    def delete_project(self, pid, repo_delete):   # XR-34: 삭제 primitive(고정 픽스처=락 경합 없음)
        self._sess = None
        return repo_delete()


def _ch(**kw) -> ChapterRecord:
    base = dict(chapter=1, title="1화", status=ChapterStatus.FINALIZED, text="원본 회차 본문입니다.")
    base.update(kw)
    return ChapterRecord(**base)


def _state(chapters) -> ProjectState:
    return ProjectState(id="p1", seed=ProjectSeed(title="t"),
                        world=WorldConfig(title="t", synopsis="s"),
                        current_chapter=max((c.chapter for c in chapters), default=0),
                        chapters=list(chapters))


def _svc(chapters, provider=None, ont=None, rag=None):
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    provider = provider or _FakeProvider()
    ont = ont if ont is not None else _FakeOnt([_Ent("hero", "진우")])
    rag = rag if rag is not None else _FakeRag()
    tmp = Path(tempfile.mkdtemp(prefix="xr7svc_"))
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    sess = _FakeSession(provider, ont, rag)
    svc.sessions = _FakeSessions(sess)
    state = _state(chapters)
    svc.repo.save(state)
    return svc, sess, provider


# ───────────────────────── ① 사전 점검 목록(결정론·비차단) ─────────────────────────
def test_stale_derivatives_lists_marked_finalized_chapters():
    st = _state([
        _ch(chapter=1, derivatives_revised_stale={"wiki": True, "claim_audit": True}),
        _ch(chapter=2),                                                     # 표식 없음 → 제외
        _ch(chapter=3, derivatives_revised_stale={"dialogue_ledger": True}),
        _ch(chapter=4, status=ChapterStatus.ESCALATED,
            derivatives_revised_stale={"wiki": True}),                      # 미확정 → 제외
        _ch(chapter=5, derivatives_revised_stale={"wiki": False}),          # falsy 표식 → 제외
    ])
    assert stale_derivatives(st) == [{"chapter": 1, "names": ["claim_audit", "wiki"]},
                                     {"chapter": 3, "names": ["dialogue_ledger"]}]


def test_stale_derivatives_empty_when_clean():
    assert stale_derivatives(_state([_ch(chapter=1), _ch(chapter=2)])) == []
    assert stale_derivatives(_state([])) == []


def test_stale_derivatives_is_not_a_gate():
    """① 비차단: stale 이 있어도 준비도 level/flags 는 한 글자도 달라지지 않는다(게이트 아님)."""
    from novelcopilot.engine.readiness import chapter_readiness
    clean = _state([_ch(chapter=1)])
    dirty = _state([_ch(chapter=1, derivatives_revised_stale={"wiki": True})])
    a, b = chapter_readiness(clean), chapter_readiness(dirty)
    assert a["level"] == b["level"] and a["flags"] == b["flags"]


def test_readiness_surface_carries_additive_key():
    """① GET /readiness 표면: additive 키 `stale_derivatives`(XR-5 의 tier_report 와 분리)."""
    svc, _sess, _prov = _svc([_ch(chapter=1, derivatives_revised_stale={"wiki": True})])
    rep = svc.readiness("p1")
    assert rep["stale_derivatives"] == [{"chapter": 1, "names": ["wiki"]}]
    assert "level" in rep and "flags" in rep and "signals" in rep   # 기존 계약 무회귀


# ───────────────────────── ② 발췌 앵커 결정론 필터 ─────────────────────────
CH_TEXT = ('진우가 문을 열었다.\n"간다."\n지연이 뒤를 돌아봤다.\n"어디로?"\n'
           '진우는 대답하지 않았다.\n')
ROWS = [{"idx": 1, "speaker": "진우", "text": "간다.", "canon": True},
        {"idx": 2, "speaker": "지연", "text": "어디로?", "canon": True}]


def _legacy_prev_ledgers(prior):
    """XR-7 이전 조립식(바이트 대조 기준선)."""
    return [(c.chapter, c.dialogue_ledger) for c in sorted(prior, key=lambda c: c.chapter)
            if c.status == ChapterStatus.FINALIZED and c.dialogue_ledger]


def test_filter_byte_identical_when_quotes_all_present():
    """②(a) 인용이 전부 현재 본문에 실재 → 재료 목록도 조회 응답도 바이트 동일."""
    prior = [_ch(chapter=1, text=CH_TEXT, dialogue_ledger=ROWS)]
    bus = _FakeBus()
    got = _verified_prev_ledgers(prior, bus=bus, next_ch=2)
    assert got == _legacy_prev_ledgers(prior)
    assert bus.events == []                                   # 정합이면 emit 도 없음(노이즈 0)
    ont = _FakeOnt([_Ent("hero", "진우", voice="담백"), _Ent("j", "지연", voice="차분")])
    legacy = CanonLookup(ont, [], 2, ledgers=_legacy_prev_ledgers(prior))._recent_exchange("hero")
    filtered = CanonLookup(ont, [], 2, ledgers=got)._recent_exchange("hero")
    assert legacy == filtered and legacy != ""                # 앵커가 실제로 실리고, 바이트 동일


def test_filter_drops_whole_chapter_on_ghost_quote_with_emit():
    """②(b) 한 행이라도 현재 본문에 없으면 그 회차 원장 전체 제외 + emit payload 정확(침묵 공백 금지)."""
    revised = CH_TEXT.replace('"어디로?"', '"어디로 가는데?"')      # 퇴고로 2행 인용이 유령이 됨
    prior = [_ch(chapter=1, text=revised, dialogue_ledger=ROWS)]
    bus = _FakeBus()
    assert _verified_prev_ledgers(prior, bus=bus, next_ch=2) == []
    assert bus.events == [{"node": "assemble_memory", "event": "ledger_quote_stale",
                           "chapter": 2, "source_chapter": 1, "rows": 1, "total": 2}]


def test_filter_keeps_other_chapters():
    """②(b) 제외는 어긋난 회차에 한정 — 정합 회차는 그대로 남는다(과잉 기아 금지)."""
    bad = _ch(chapter=1, text=CH_TEXT.replace('"간다."', '"가자."'), dialogue_ledger=ROWS)
    good = _ch(chapter=2, text=CH_TEXT, dialogue_ledger=ROWS)
    bus = _FakeBus()
    got = _verified_prev_ledgers([bad, good], bus=bus, next_ch=3)
    assert got == [(2, ROWS)]
    assert [e["source_chapter"] for e in bus.events] == [1]


def test_filter_no_bus_is_safe():
    """② bus 미제공(러너·테스트 경로)에서도 필터는 돈다(가시화만 생략)."""
    prior = [_ch(chapter=1, text="빈 본문", dialogue_ledger=ROWS)]
    assert _verified_prev_ledgers(prior) == []


def test_ledger_material_unused_when_gen_tools_off():
    """②(c) gen_tools OFF: 원장 재료를 읽는 유일한 소비처(CanonLookup 생성)가 gen_tools 게이트 안에 있다
    — OFF 면 필터 결과가 프롬프트에 닿을 경로 자체가 없다(종전 무동작)."""
    src = pathlib.Path(__file__).resolve().parents[1] / "novelcopilot" / "engine" / "harness.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "CanonLookup"]
    assert calls, "CanonLookup 생성 지점을 찾지 못함(소스 계약 확인 필요)"
    for call in calls:
        gated, cur = False, parents.get(call)
        while cur is not None:
            if isinstance(cur, ast.If) and "gen_tools" in ast.dump(cur.test):
                gated = True
                break
            cur = parents.get(cur)
        assert gated, "CanonLookup 생성이 gen_tools 게이트 밖에 있다 — OFF 무동작 계약 깨짐"


# ───────────────────────── ③ 작가 발동 재계산 ─────────────────────────
def test_recompute_wiki_forces_reingest_and_clears_stale():
    ont = _FakeOnt([_Ent("hero", "진우")])
    ch = _ch(chapter=1, text="진우가 문을 열었다.", wiki_pages_touched=2,
             derivatives_revised_stale={"wiki": True, "claim_audit": True})
    svc, sess, prov = _svc([ch], ont=ont)
    sess.bundle.wiki.log.append("[1][hero][update]")          # 멱등 가드가 걸리는 상태(재적재 필요)
    res = svc.recompute_derivative("p1", 1, "wiki")
    ch2 = svc.repo.get("p1").chapter(1)
    assert res["recomputed"] == "wiki" and ch2.wiki_pages_touched == 1
    assert sess.bundle.wiki.pages["hero"].body == "상태: 재계산된 카드"
    assert ch2.derivatives_revised_stale == {"claim_audit": True}   # 그 키만 해제(나머지 유지)
    assert ch2.derivatives_recomputed == {"wiki": 0}                # 리비전 0개 상태에서 재계산
    assert prov.usage.chat_calls == 1                               # 회차당 0~1콜
    assert [e["event"] for e in sess.bus.events if e["node"] == "derivatives"] == ["recomputed"]


def test_recompute_claim_audit_uses_current_text():
    rag = _FakeRag([_Past(ref=1, text="옛 회차 발췌")])
    ch = _ch(chapter=2, text="퇴고된 2화 본문입니다.",
             claim_audit=[{"claim": "old", "canon": "z", "ref": 1}],
             derivatives_revised_stale={"claim_audit": True})
    svc, sess, prov = _svc([_ch(chapter=1), ch], rag=rag)
    svc.recompute_derivative("p1", 2, "claim_audit")
    ch2 = svc.repo.get("p1").chapter(2)
    assert ch2.claim_audit == [{"claim": "새 진술", "canon": "옛 진술", "ref": "1", "why": "양립 불가"}]
    assert ch2.derivatives_revised_stale == {} and ch2.derivatives_recomputed == {"claim_audit": 0}
    assert "퇴고된 2화 본문입니다." in prov.seen[-1]      # 현재 본문 기준으로 다시 돌았다


def test_recompute_reader_feedback():
    ch = _ch(chapter=1, text="본문입니다.", reader_feedback={"why": "old", "retention_est": 5},
             derivatives_revised_stale={"reader_feedback": True})
    svc, _sess, _prov = _svc([ch])
    svc.recompute_derivative("p1", 1, "reader_feedback")
    ch2 = svc.repo.get("p1").chapter(1)
    assert ch2.reader_feedback["retention_est"] == 42 and ch2.reader_feedback["why"] == "속도 저하"
    assert ch2.derivatives_revised_stale == {}


def test_recompute_dialogue_ledger():
    ont = _FakeOnt([_Ent("hero", "진우")])
    ch = _ch(chapter=1, text='진우가 말했다.\n"간다."\n', dialogue_ledger=[{"idx": 1, "speaker": "미상",
                                                                        "text": "옛 대사", "canon": False}],
             derivatives_revised_stale={"dialogue_ledger": True})
    svc, _sess, _prov = _svc([ch], ont=ont)
    svc.recompute_derivative("p1", 1, "dialogue_ledger")
    ch2 = svc.repo.get("p1").chapter(1)
    assert [r["text"] for r in ch2.dialogue_ledger] == ["간다."]
    assert ch2.dialogue_ledger[0]["speaker"] == "진우" and ch2.dialogue_ledger[0]["canon"] is True
    assert ch2.derivatives_revised_stale == {} and ch2.derivatives_recomputed == {"dialogue_ledger": 0}


def test_recompute_promise_ledger_rejected_with_reason():
    """③ 원장 재정산은 미지원 — 400 + 정직 사유(별건 설계 예약)."""
    from novelcopilot.domain.ledger import Promise, PromiseLedger
    ch = _ch(chapter=1, derivatives_revised_stale={"promise_ledger": True})
    svc, _sess, prov = _svc([ch])
    st = svc.repo.get("p1")
    st.promise_ledger = PromiseLedger(promises=[Promise(id="p", text="복선", opened_chapter=1)])
    svc.repo.save(st)
    try:
        svc.recompute_derivative("p1", 1, "promise_ledger")
        raise AssertionError("promise_ledger 는 400 이어야 한다")
    except ValueError as e:
        assert "약속 원장" in str(e) and "별도 설계" in str(e)
    assert prov.usage.chat_calls == 0                              # 반려 경로 LLM 0
    assert svc.repo.get("p1").chapter(1).derivatives_revised_stale == {"promise_ledger": True}


def test_recompute_unknown_name_and_absent_derivative_rejected():
    svc, _sess, prov = _svc([_ch(chapter=1, text="본문")])
    for bad in ("summary", "", "detail_synopsis"):
        try:
            svc.recompute_derivative("p1", 1, bad)
            raise AssertionError(f"미지명({bad!r})은 400 이어야 한다")
        except ValueError:
            pass
    try:                                       # 지원 name 이지만 이 회차엔 그 파생물이 없음
        svc.recompute_derivative("p1", 1, "claim_audit")
        raise AssertionError("파생물 없는 회차는 400 이어야 한다")
    except ValueError as e:
        assert "다시 계산할 대상이 아닙니다" in str(e)
    assert prov.usage.chat_calls == 0


def test_recompute_missing_project_or_chapter():
    svc, _sess, _prov = _svc([_ch(chapter=1)])
    for args in (("nope", 1, "wiki"), ("p1", 99, "wiki")):
        try:
            svc.recompute_derivative(*args)
            raise AssertionError("없는 작품·회차는 KeyError(404) 여야 한다")
        except KeyError:
            pass


# ───────────────────────── ④ undo 대칭 ─────────────────────────
def test_undo_remarks_stale_for_recomputed_derivative():
    """④ accept(표식) → recompute(해제·각인) → undo → 재표식 + 각인 제거."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    ch = _ch(chapter=1, text="원본 회차 본문입니다.", wiki_pages_touched=1)
    svc, sess, _prov = _svc([ch], ont=ont)
    svc.accept_revision("p1", 1, None, after_text_fb="퇴고된 진우 회차 본문입니다.")
    assert svc.repo.get("p1").chapter(1).derivatives_revised_stale == {"wiki": True}
    svc.recompute_derivative("p1", 1, "wiki")
    mid = svc.repo.get("p1").chapter(1)
    assert mid.derivatives_revised_stale == {} and mid.derivatives_recomputed == {"wiki": 1}
    svc.undo_revision("p1", 1)
    done = svc.repo.get("p1").chapter(1)
    assert done.text == "원본 회차 본문입니다."
    assert done.derivatives_revised_stale == {"wiki": True}   # 버려진 본문 기준 파생물 → 재표식
    assert done.derivatives_recomputed == {}                  # 각인 제거(정합 주장 철회)


def test_undo_keeps_recompute_from_older_revision():
    """④ 더 앞선 본문 기준으로 재계산된 파생물은 undo 후에도 정합 — 재표식하지 않는다."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    svc, sess, _prov = _svc([_ch(chapter=1, text="원본 회차 본문입니다.", wiki_pages_touched=1)], ont=ont)
    svc.accept_revision("p1", 1, None, after_text_fb="첫 퇴고 진우 본문입니다.")
    svc.recompute_derivative("p1", 1, "wiki")                 # 리비전 1개 시점 기준(N=1)
    svc.accept_revision("p1", 1, None, after_text_fb="두 번째 퇴고 진우 본문입니다.")
    svc.undo_revision("p1", 1)                                # 활성 2 → 1(재계산 기준 본문으로 복귀)
    done = svc.repo.get("p1").chapter(1)
    assert done.text == "첫 퇴고 진우 본문입니다."
    assert done.derivatives_recomputed == {"wiki": 1}         # 각인 유지
    assert "wiki" not in done.derivatives_revised_stale       # 재표식 없음


def test_undo_without_recompute_unchanged():
    """④ 재계산 이력이 없으면 undo 는 종전 스냅샷 복원 그대로(기존 RV-2 경로 무회귀)."""
    svc, _sess, _prov = _svc([_ch(chapter=1, wiki_pages_touched=1,
                                  derivatives_revised_stale={"reader_feedback": True})])
    svc.accept_revision("p1", 1, None, after_text_fb="퇴고 후 본문입니다.")
    svc.undo_revision("p1", 1)
    done = svc.repo.get("p1").chapter(1)
    assert done.derivatives_revised_stale == {"reader_feedback": True}
    assert done.derivatives_recomputed == {}


# ───────────────────────── ⑤ 하위호환 ─────────────────────────
def test_old_json_roundtrip_unchanged():
    old = ('{"chapter": 3, "status": "FINALIZED", "text": "본문", "summary": "s", '
           '"detail_synopsis": "d", "derivatives_revised_stale": {"wiki": true}}')
    rec = ChapterRecord.model_validate_json(old)
    assert rec.derivatives_recomputed == {}
    rec2 = ChapterRecord.model_validate_json(rec.model_dump_json())
    assert rec2.derivatives_recomputed == {} and rec2.derivatives_revised_stale == {"wiki": True}
    rec3 = ChapterRecord(chapter=4, status=ChapterStatus.FINALIZED, derivatives_recomputed={"wiki": 2})
    assert ChapterRecord.model_validate_json(rec3.model_dump_json()).derivatives_recomputed == {"wiki": 2}
    assert ChapterRevision.model_validate_json('{"revision_id": "abc"}').before_derivatives_revised_stale == {}


def test_wiki_force_default_false_keeps_idempotent_guard():
    """⑤ wiki ingest force 기본 False = 기존 멱등 경로 바이트 동일(자동 경로 무변경)."""
    from novelcopilot.engine.wiki import Wiki
    prov = _FakeProvider()
    w = Wiki(prov)
    ont = _FakeOnt([_Ent("hero", "진우")])
    assert w.ingest_chapter(1, "진우가 걸었다.", ont) == 1 and prov.usage.chat_calls == 1
    assert w.ingest_chapter(1, "진우가 다시 걸었다.", ont) == 0   # 멱등 가드(기존 동작)
    assert prov.usage.chat_calls == 1                            # 콜 증가 0
    log_before = list(w.log)
    assert w.ingest_chapter(1, "진우가 또 걸었다.", ont, force=True) == 1
    assert prov.usage.chat_calls == 2
    assert w.log == [e for e in log_before if not e.startswith("[1]")] + ["[1][hero][update]"]


# ───────────────────────── ⑥ K2 — 자동 LLM 콜 0 ─────────────────────────
def test_preflight_and_filter_make_zero_llm_calls():
    """⑥ 사전 점검·발췌 필터는 전부 결정론 — fake provider 콜 0."""
    prov = _FakeProvider()
    ch = _ch(chapter=1, text=CH_TEXT.replace('"간다."', '"가자."'), dialogue_ledger=ROWS,
             derivatives_revised_stale={"wiki": True, "claim_audit": True})
    svc, _sess, prov2 = _svc([ch], provider=prov)
    stale_derivatives(svc.repo.get("p1"))
    svc.readiness("p1")
    _verified_prev_ledgers([ch], bus=_FakeBus(), next_ch=2)
    assert prov.usage.chat_calls == 0 and prov2.usage.chat_calls == 0


def test_no_automatic_recompute_call_sites():
    """⑥ 재계산은 작가 발동 엔드포인트만 — 엔진·서비스 어디에서도 자동 호출하지 않는다.
    (`recompute_derivative` 호출부는 라우트 1곳뿐 · wiki force=True 는 그 서비스 메서드 1곳뿐)"""
    root = pathlib.Path(__file__).resolve().parents[1] / "novelcopilot"
    callers, forcers = [], []
    for py in root.rglob("*.py"):
        body = py.read_text(encoding="utf-8")
        rel = py.relative_to(root).as_posix()
        if ".recompute_derivative(" in body:        # 메서드 호출부(정의 `def recompute_derivative` 는 제외)
            callers.append(rel)
        for node in ast.walk(ast.parse(body)):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "ingest_chapter"
                    and any(kw.arg == "force" for kw in node.keywords)):
                forcers.append(rel)
    assert callers == ["api/routes.py"], f"자동 재계산 호출부 발견: {callers}"
    assert forcers == ["services/copilot.py"], f"wiki 강제 재적재 지점 이상: {forcers}"


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
    print(f"\n{passed}/{len(tests)} XR-7 tests passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
