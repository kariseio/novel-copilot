# -*- coding: utf-8 -*-
"""EC-1 엔딩 술어계약 감시 레이어 테스트 — 실 LLM 0콜(FakeProvider) · 라이브 데이터 미접근(합성+tmp).

커버(티켓 계약):
  ① 컴파일 영속 하위호환: ending_contract/ending_contract_eval 없는 구 JSON 무변경 로드 + 라운드트립
  ② 컴파일: 술어 영속 · 거부개념='미표현 엔딩 요소' 영속(LR-1 arm3) · 지문 · 실패 격리(예외 0 — 차단 금지)
  ③ 평가 양 tier(LR-1 arm1): gt/ni 병렬 산출 · 단일 판정 없음 · ni충족&gt미결='승격 유도' 신호
  ④ blocked 보수성(kill criteria 사전 등록): R1 terminal(provisional 제외·reversal 제외·ni 비누수)
     R2 카디널리티(선언 상한 0만 — 가득참은 open, pov 비대상) R3 약속 소멸(open 약속은 blocked 아님)
  ⑤ 미정산 surface 경로: FINALIZED 평가 영속+이벤트 · 아크 소진 정산 스냅샷(lazy 미분해 아크=비정산 H1) ·
     완결 전이 멱등 ·
     revise_spine 엔딩 개정 재컴파일(실패 시 기존 계약 유지=stale 가시화) · GET 현황(stale/notes/미표현)
전부 advisory — 어떤 임계/라벨/자동반응도 검증하지 않는다(판정기 금지 준수)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.world import WorldConfig, AttributeSpec, EntitySpec, TimelineEntry
from novelcopilot.domain.narrative import (NarrativeSpine, EndingSpec, Arc, Episode,
                                           EndingContract, ContractPredicate, UnexpressedEnding)
from novelcopilot.domain.ledger import PromiseLedger, Promise
from novelcopilot.domain.types import RelationEdge, ChapterRecord, ChapterStatus, TimeDelta
from novelcopilot.engine.observability import EventBus
from novelcopilot.engine.ending_contract import (
    TIER_GT, TIER_ALL, compile_contract, evaluate_predicate, evaluate_contract,
    ending_fingerprint, settlement_snapshot, CONTRACT_NOTES,
)
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from tools.ec0_ending_contract import build_project_ontology


# ---------------------------------------------------------------- 합성 상태(라이브 데이터 미접근)
def _state(**over) -> ProjectState:
    world = WorldConfig(
        title="계약작", genre="테스트",
        attributes=[
            AttributeSpec(key="closeness", label="관계", kind="categorical",
                          vocab=["어색", "친구", "연인"], mutable=True),
            AttributeSpec(key="status", label="생사", kind="state",
                          states=["alive", "dead"], irreversible=["dead"], terminal=["dead"]),
        ],
        entities=[
            EntitySpec(id="hero", name="주인공", aliases=["히어로"]),
            EntitySpec(id="rival", name="라이벌"),
            EntitySpec(id="ghost", name="망자", base_status="dead"),
        ],
        spine=NarrativeSpine(
            ending=EndingSpec(central_question="둘은 연인이 되는가?",
                              ending="주인공과 라이벌은 연인이 된다.", thematic_payoff="관계의 회복"),
            arcs=[Arc(arc_id="a1", order=1, title="1막", episodes=[
                Episode(episode_id="e1", arc_id="a1", order=1, target_chapters=3)])]),
    )
    base = dict(
        id="ec1test", seed=ProjectSeed(title="계약작"), world=world, current_chapter=3,
        chapters=[
            ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED,
                          time_delta=TimeDelta(amount=1, unit="day", mode="advance")),
            ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED,
                          time_delta=TimeDelta(amount=2, unit="day", mode="advance")),
            ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, time_delta=None),
        ],
        runtime_timeline=[
            TimelineEntry(entity_id="hero", attr="closeness", value="친구", eff_from=2,
                          trust_tier="ground_truth"),
            TimelineEntry(entity_id="hero", attr="closeness", value="연인", eff_from=3,
                          trust_tier="narrative_inferred"),                     # tier 분기 재료
        ],
        promise_ledger=PromiseLedger(promises=[
            Promise(id="약속하나", text="약속 하나", status="paid", paid_chapter=2),
            Promise(id="약속둘", text="약속 둘", status="open"),
        ]),
    )
    base.update(over)
    return ProjectState(**base)


def _contract(*preds) -> EndingContract:
    return EndingContract(predicates=[ContractPredicate(**p) for p in preds],
                          ending_fingerprint="fp", compiled_at="t", source="worldgen")


def _eval1(st, pred, tiers, ch=3):
    ont = build_project_ontology(st)
    deltas = [c.time_delta for c in sorted(st.chapters, key=lambda c: c.chapter)
              if c.status == ChapterStatus.FINALIZED]
    return evaluate_predicate(pred, ont, st.promise_ledger, deltas, ch, tiers,
                              st.world.allow_state_reversal)


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_json(self, messages, *, temperature=0.0, max_tokens=2000):
        self.calls += 1
        return self.responses.pop(0)


class BoomProvider:
    def chat_json(self, *a, **k):
        raise RuntimeError("no api key")


# ---------------------------------------------------------------- ① 영속 하위호환
def test_old_json_without_contract_fields_loads_with_defaults():
    d = _state().model_dump()
    d.pop("ending_contract")                                     # 구 JSON: 필드 자체가 없음
    for c in d["chapters"]:
        c.pop("ending_contract_eval", None)
    st = ProjectState.model_validate(d)
    assert st.ending_contract.predicates == [] and st.ending_contract.unexpressed == []
    assert st.ending_contract.error == "" and st.ending_contract.settlement == {}
    assert st.chapters[0].ending_contract_eval == {}


def test_contract_roundtrip_persistence():
    st = _state()
    st.ending_contract = _contract({"type": "alive", "eid": "hero", "note": "생존 귀결"})
    st.ending_contract.unexpressed = [UnexpressedEnding(raw={"type": "alive", "eid": "환각"},
                                                        reason="unregistered_eid:환각")]
    st.ending_contract.settlement = {"trigger": "arcs_exhausted", "unsettled_gt": 1}
    st.chapters[2].ending_contract_eval = {"chapter": 3, "promotion_hints": 0}
    st2 = ProjectState.model_validate_json(st.model_dump_json())
    assert st2.ending_contract.predicates[0].eid == "hero"
    assert st2.ending_contract.unexpressed[0].reason.startswith("unregistered_eid")
    assert st2.ending_contract.settlement["trigger"] == "arcs_exhausted"
    assert st2.chapters[2].ending_contract_eval["chapter"] == 3


# ---------------------------------------------------------------- ② 컴파일(영속 모델 생성)
def test_compile_contract_predicates_and_unexpressed_persisted():
    st = _state()
    ont = build_project_ontology(st)
    bad = {"type": "attr_equals", "eid": "환각인물", "attr": "closeness", "value": "연인"}
    good = {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"}
    fp = FakeProvider([{"predicates": [bad, good]},
                       {"predicates": [good, bad]}])             # 교정 후에도 bad 잔존 → 미표현으로 영속
    c = compile_contract(fp, st, ont, source="worldgen")
    assert c.error == "" and c.source == "worldgen" and c.llm_calls == 2
    assert [p.type for p in c.predicates] == ["attr_equals"] and c.predicates[0].eid == "hero"
    assert len(c.unexpressed) == 1                               # LR-1 arm3: 거부개념 버리지 않음
    assert c.unexpressed[0].reason.startswith("unregistered_eid")
    assert c.unexpressed[0].raw.get("eid") == "환각인물"
    assert c.ending_fingerprint == ending_fingerprint(st.world.spine.ending.model_dump())
    assert c.compiled_at and c.compiled_chapter == 3


def test_compile_contract_failure_isolated_no_raise():
    st = _state()
    ont = build_project_ontology(st)
    c = compile_contract(BoomProvider(), st, ont, source="worldgen")   # 예외 → error 필드(차단 금지)
    assert c.error and c.predicates == []
    st.world.spine = None                                        # 엔딩 없음 → LLM 0콜 스킵
    fp = FakeProvider([])
    c2 = compile_contract(fp, st, build_project_ontology(st), source="worldgen")
    assert c2.error == "no_ending_spec" and fp.calls == 0


# ---------------------------------------------------------------- ③ 평가 양 tier(arm1 병렬)
def test_evaluate_contract_parallel_tiers_and_promotion_hint():
    st = _state()
    contract = _contract(
        {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"},  # gt open · ni satisfied
        {"type": "alive", "eid": "hero"},                                              # 양쪽 satisfied
        {"type": "promise_paid", "promise_id": "약속둘"},                               # 양쪽 open
    )
    ont = build_project_ontology(st)
    ev = evaluate_contract(contract, ont, st.promise_ledger, [], 3, False)
    g, n = ev["tiers"]["ground_truth_only"], ev["tiers"]["with_narrative_inferred"]
    assert g == {"satisfied": 1, "open": 2, "blocked": 0}
    assert n == {"satisfied": 2, "open": 1, "blocked": 0}
    assert ev["promotion_hints"] == 1                            # ni충족&gt미결 → 작가 승격 유도 신호
    assert ev["predicates"][0]["promotion_hint"] is True
    assert ev["settled"] == {"ground_truth_only": False, "with_narrative_inferred": False}
    assert "판정" not in str(ev.get("verdict", ""))              # 단일 판정 필드 없음
    assert "verdict" not in ev and "level" not in ev
    assert "지면" in ev["note"]                                  # arm2: 상태 기준 한계 명시
    assert "주인공" in ev["predicates"][0]["label"]              # 작가 언어 라벨


def test_evaluate_contract_settled_when_all_satisfied():
    st = _state()
    contract = _contract({"type": "alive", "eid": "hero"},
                         {"type": "promise_paid", "promise_id": "약속하나"})
    ev = evaluate_contract(contract, build_project_ontology(st), st.promise_ledger, [], 3, False)
    assert ev["settled"] == {"ground_truth_only": True, "with_narrative_inferred": True}
    assert ev["promotion_hints"] == 0


# ---------------------------------------------------------------- ④ blocked 보수성(kill criteria)
def test_r1_terminal_blocked_only_for_confirmed_irreversible():
    st = _state()
    # 확정(non-provisional) + 비가역 dead → blocked (규칙 태그 포함)
    r = _eval1(st, {"type": "alive", "eid": "ghost"}, TIER_GT)
    assert r["result"] == "blocked" and r["blocked_rule"] == "terminal_conflict"
    # kill: reversal 허용 세계 → open(부활 가능 — blocked 금지)
    st2 = _state()
    st2.world.allow_state_reversal = True
    assert _eval1(st2, {"type": "alive", "eid": "ghost"}, TIER_GT)["result"] == "open"


def test_r1_kill_provisional_entity_never_blocks():
    st = _state()
    st.world.entities = list(st.world.entities) + [
        EntitySpec(id="npc", name="떠돌이", base_status="dead", provisional=True)]   # AI 미확정
    r = _eval1(st, {"type": "alive", "eid": "npc"}, TIER_GT)
    assert r["result"] == "open" and "blocked_rule" not in r     # provisional 은 봉쇄 근거 금지
    r2 = _eval1(st, {"type": "edge_active", "src": "hero", "dst": "npc", "rel_id": "ally_of"}, TIER_GT)
    assert r2["result"] == "open"                                # 끝점 terminal 봉쇄도 provisional 제외


def test_r1_kill_ni_terminal_does_not_leak_into_gt():
    st = _state()
    st.runtime_timeline = list(st.runtime_timeline) + [
        TimelineEntry(entity_id="rival", attr="status", value="dead", eff_from=2,
                      trust_tier="narrative_inferred")]          # 기계추출 사망(미확정)
    assert _eval1(st, {"type": "alive", "eid": "rival"}, TIER_GT)["result"] == "satisfied"
    # ni 모드에선 병렬로 blocked 신호가 뜨지만(표시용) gt 캐논엔 비구속
    assert _eval1(st, {"type": "alive", "eid": "rival"}, TIER_ALL)["result"] == "blocked"


def test_r2_cardinality_zero_blocks_but_full_cap_stays_open():
    st = _state()
    st.world.entities = [
        EntitySpec(id="hero", name="주인공", cardinality={"sibling_of": 0, "ally_of": 1}),  # 외동 선언
        EntitySpec(id="rival", name="라이벌"), EntitySpec(id="third", name="제삼자"),
    ]
    # 선언 상한 0 → 구조적 불가 = blocked
    r = _eval1(st, {"type": "edge_active", "src": "hero", "dst": "rival", "rel_id": "sibling_of"}, TIER_GT)
    assert r["result"] == "blocked" and r["blocked_rule"] == "cardinality_zero"
    # kill: 상한 1 이 '가득참'(기존 엣지 종료로 해소 가능) → open — blocked 오탐 금지
    st.runtime_edges = [RelationEdge(edge_id="x", rel_id="ally_of", src_id="hero", dst_id="third",
                                     eff_from=1, trust_tier="ground_truth")]
    r2 = _eval1(st, {"type": "edge_active", "src": "hero", "dst": "rival", "rel_id": "ally_of"}, TIER_GT)
    assert r2["result"] == "open" and "blocked_rule" not in r2


def test_r2_kill_pov_edge_neither_satisfies_nor_blocks():
    st = _state()
    st.runtime_edges = [RelationEdge(edge_id="p", rel_id="loves", src_id="hero", dst_id="rival",
                                     eff_from=2, pov="hero", trust_tier="ground_truth")]  # 믿음(거짓 가능)
    r = _eval1(st, {"type": "edge_active", "src": "hero", "dst": "rival", "rel_id": "loves"}, TIER_ALL)
    assert r["result"] == "open"                                 # pov 는 성립 근거도 봉쇄 근거도 아님


def test_r3_promise_vanished_blocked_but_open_promise_stays_open():
    st = _state()
    r = _eval1(st, {"type": "promise_paid", "promise_id": "소멸한약속"}, TIER_GT)
    assert r["result"] == "blocked" and r["blocked_rule"] == "promise_vanished"
    # kill: open 약속은 '미결'이지 봉쇄가 아니다 · paid 는 충족
    assert _eval1(st, {"type": "promise_paid", "promise_id": "약속둘"}, TIER_GT)["result"] == "open"
    assert _eval1(st, {"type": "promise_paid", "promise_id": "약속하나"}, TIER_GT)["result"] == "satisfied"


# ---------------------------------------------------------------- ⑤ 서비스 경로(미정산 surface)
class _EcSession:
    def __init__(self, state):
        self.lock = threading.Lock()
        self.bus = EventBus()
        self.bundle = SimpleNamespace(ontology=build_project_ontology(state))


class _EcSessions:
    """EC-1 경로는 세션에서 lock·bus·bundle.ontology 만 쓴다(provider/엔진 빌드 0 — LLM 0콜)."""
    def __init__(self):
        self._s = {}

    def get_or_create(self, state):
        if state.id not in self._s:
            self._s[state.id] = _EcSession(state)
        return self._s[state.id]

    def evict(self, pid):
        self._s.pop(pid, None)


def _svc_with(state) -> CopilotService:
    tmp = Path(tempfile.mkdtemp())
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _EcSessions()
    svc.repo.save(state)
    return svc


def test_finalize_eval_persists_to_record_and_emits_event():
    st = _state()
    st.ending_contract = _contract({"type": "alive", "eid": "hero"},
                                   {"type": "promise_paid", "promise_id": "약속둘"})
    svc = _svc_with(st)
    sess = svc.sessions.get_or_create(st)
    record = st.chapters[-1]
    svc._ec_after_finalize(st, sess, record, 3)
    ev = record.ending_contract_eval
    assert ev["tiers"]["ground_truth_only"]["satisfied"] == 1    # gt/ni 병렬 스냅샷 영속
    assert ev["tiers"]["with_narrative_inferred"]["open"] == 1
    kinds = [(e["node"], e["event"]) for e in sess.bus.buffer]
    assert ("ending_contract", "contract_eval") in kinds
    assert ("ending_contract", "contract_unsettled") not in kinds  # 아크 미소진 → 정산 안 함
    assert st.ending_contract.settlement == {}


def test_arcs_exhausted_records_unsettled_settlement():
    st = _state()
    st.world.spine.arcs[0].episodes[0].done = True               # 아크 전량 소진
    st.ending_contract = _contract(
        {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"},  # gt open(ni 충족)
        {"type": "alive", "eid": "hero"})
    svc = _svc_with(st)
    sess = svc.sessions.get_or_create(st)
    svc._ec_after_finalize(st, sess, st.chapters[-1], 3)
    snap = st.ending_contract.settlement
    assert snap["trigger"] == "arcs_exhausted" and snap["unsettled_gt"] == 1
    assert snap["promotion_hints"] == 1 and snap["tiers"]["with_narrative_inferred"]["satisfied"] == 2
    unsettled = [e for e in sess.bus.buffer if e["event"] == "contract_unsettled"]
    assert len(unsettled) == 1 and unsettled[0]["unsettled_gt"] == 1


def test_lazy_undecomposed_arc_never_settles_mid_serial():
    """H1 회귀(적대검증 CONFIRMED): 제품 표준 형상 = 다아크 lazy spine(build_spine 이 첫 아크만 분해,
    n_arcs>=2, 후속 아크 episodes=[]). 1막 소진 시점(후속 아크 미분해)은 '연재 중'이다 —
    정산 스냅샷·contract_unsettled 가 발화하면 안 된다(아크 경계마다 '미정산 엔딩' 오탐 +
    가짜 스냅샷이 _ec_on_complete 의 진짜 정산을 선점 차단). 전 아크 실체화+소진 시에만 정산."""
    st = _state()
    st.world.spine.arcs[0].episodes[0].done = True               # 1막 전량 소진
    st.world.spine.arcs.append(Arc(arc_id="a2", order=2, title="2막", episodes=[]))  # lazy 미분해
    st.ending_contract = _contract({"type": "promise_paid", "promise_id": "약속둘"})  # gt open(미정산 재료)
    svc = _svc_with(st)
    sess = svc.sessions.get_or_create(st)
    svc._ec_after_finalize(st, sess, st.chapters[-1], 3)
    assert st.ending_contract.settlement == {}                   # 연재 중간 가짜 정산 0
    assert not any(e["event"] in ("contract_unsettled", "contract_settled") for e in sess.bus.buffer)
    assert st.chapters[-1].ending_contract_eval                  # 회차 평가 자체는 매 FINALIZED 영속(정산과 독립)
    svc._ec_on_complete(st, sess, trigger="hard_cap")            # 가짜 스냅샷이 없으니 진짜 완결 정산은 그대로 기록됨
    assert st.ending_contract.settlement["trigger"] == "hard_cap"
    # 후속 아크가 실체화되고 전부 소진되면 그때가 진짜 아크 소진 → finalize 경로 정산 발화
    st2 = _state()
    st2.world.spine.arcs[0].episodes[0].done = True
    st2.world.spine.arcs.append(Arc(arc_id="a2", order=2, title="2막", episodes=[
        Episode(episode_id="e2", arc_id="a2", order=1, target_chapters=3, done=True)]))
    st2.ending_contract = _contract({"type": "promise_paid", "promise_id": "약속둘"})
    svc2 = _svc_with(st2)
    svc2._ec_after_finalize(st2, svc2.sessions.get_or_create(st2), st2.chapters[-1], 3)
    assert st2.ending_contract.settlement["trigger"] == "arcs_exhausted"


def test_settlement_settled_event_when_all_satisfied_and_never_raises():
    st = _state()
    st.world.spine.arcs[0].episodes[0].done = True
    st.ending_contract = _contract({"type": "alive", "eid": "hero"})
    svc = _svc_with(st)
    sess = svc.sessions.get_or_create(st)
    svc._ec_after_finalize(st, sess, st.chapters[-1], 3)
    assert st.ending_contract.settlement["unsettled_gt"] == 0
    assert any(e["event"] == "contract_settled" for e in sess.bus.buffer)
    # 계약 없음/스파인 없음 — 조용히 no-op(예외 0: advisory 는 회차 확정을 절대 안 막음)
    st2 = _state()
    st2.world.spine = None
    svc2 = _svc_with(st2)
    svc2._ec_after_finalize(st2, svc2.sessions.get_or_create(st2), st2.chapters[-1], 3)
    assert st2.chapters[-1].ending_contract_eval == {}


def test_on_complete_records_once_idempotent():
    st = _state()
    st.ending_contract = _contract({"type": "promise_paid", "promise_id": "약속둘"})
    svc = _svc_with(st)
    sess = svc.sessions.get_or_create(st)
    svc._ec_on_complete(st, sess, trigger="ending_reached")
    assert st.ending_contract.settlement["trigger"] == "ending_reached"
    svc._ec_on_complete(st, sess, trigger="hard_cap")            # 이미 기록 → 첫 스냅샷 보존(멱등)
    assert st.ending_contract.settlement["trigger"] == "ending_reached"


def test_revise_spine_ending_recompiles_contract():
    st = _state()
    st.ending_contract = _contract({"type": "alive", "eid": "hero"})
    svc = _svc_with(st)
    good = {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"}
    svc._planning_provider = FakeProvider([{"predicates": [good]}])
    res = svc.revise_spine("ec1test", [{"target": "ending", "field": "ending",
                                        "new_value": "주인공은 홀로 떠난다."}])
    assert res["applied"] == [{"target": "ending", "field": "ending"}]
    saved = svc.repo.get("ec1test")
    assert saved.ending_contract.source == "revise_spine"
    assert saved.ending_contract.predicates[0].type == "attr_equals"
    # 재컴파일 지문 = 개정된 엔딩 지문(stale 아님)
    assert saved.ending_contract.ending_fingerprint == \
        ending_fingerprint(saved.world.spine.ending.model_dump())


def test_revise_spine_arc_only_does_not_recompile_and_failure_keeps_old_contract():
    st = _state()
    old = _contract({"type": "alive", "eid": "hero"})
    st.ending_contract = old
    svc = _svc_with(st)
    svc._planning_provider = BoomProvider()
    # ① 아크만 개정 → 컴파일 미발생(엔딩 계약 그대로)
    res = svc.revise_spine("ec1test", [{"target": "arc:a1", "field": "goal", "new_value": "새 목표"}])
    assert res["applied"] and svc.repo.get("ec1test").ending_contract.source == "worldgen"
    # ② 엔딩 개정 + 컴파일 실패 → 기존 계약 유지(개정 반영은 차단 안 함) + 이벤트
    res2 = svc.revise_spine("ec1test", [{"target": "ending", "field": "ending", "new_value": "다른 결말"}])
    assert res2["applied"]
    saved = svc.repo.get("ec1test")
    assert saved.world.spine.ending.ending == "다른 결말"        # 개정은 저장됨
    assert saved.ending_contract.predicates[0].type == "alive"   # 계약은 구판 유지
    sess = svc.sessions.get_or_create(saved)
    assert any(e["event"] == "compile_failed" for e in sess.bus.buffer)


def test_get_status_parallel_tiers_stale_and_unexpressed():
    st = _state()
    st.ending_contract = _contract(
        {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"})
    st.ending_contract.ending_fingerprint = ending_fingerprint(st.world.spine.ending.model_dump())
    st.ending_contract.unexpressed = [UnexpressedEnding(raw={"eid": "환각"}, reason="unregistered_eid:환각")]
    svc = _svc_with(st)
    out = svc.ending_contract_status("ec1test")
    assert out["has_contract"] is True and out["stale"] is False
    assert out["tiers"]["ground_truth_only"]["open"] == 1        # gt/ni 병렬(단일 판정 필드 없음)
    assert out["tiers"]["with_narrative_inferred"]["satisfied"] == 1
    assert out["promotion_hints"] == 1
    assert out["unexpressed"][0]["reason"].startswith("unregistered_eid")   # arm3 surface
    assert any("상태 기준" in n or "지면" in n for n in out["notes"])        # arm2 한계 고지
    assert "verdict" not in out and "level" not in out
    # 엔딩 개정 후 미갱신 → stale advisory
    saved = svc.repo.get("ec1test")
    saved.world.spine.ending.ending = "완전히 다른 결말"
    svc.repo.save(saved)
    assert svc.ending_contract_status("ec1test")["stale"] is True


def test_get_status_no_contract_and_missing_project():
    st = _state()                                                # 계약 미컴파일(구작품)
    svc = _svc_with(st)
    out = svc.ending_contract_status("ec1test")
    assert out["has_contract"] is False and out["predicates"] == []
    assert svc.ending_contract_status("없는작품") is None


def test_settlement_snapshot_shape():
    st = _state()
    contract = _contract({"type": "alive", "eid": "ghost"})      # gt blocked
    ev = evaluate_contract(contract, build_project_ontology(st), st.promise_ledger, [], 3, False)
    snap = settlement_snapshot(ev, "arcs_exhausted")
    assert snap["blocked_gt"] == 1 and snap["unsettled_gt"] == 1 and snap["satisfied_gt"] == 0
    assert snap["tiers"]["ground_truth_only"]["blocked"] == 1    # 양 tier 병렬 보존
    assert snap["at"] and snap["trigger"] == "arcs_exhausted"


def test_contract_notes_state_basis_documented():
    """LR-1 arm2: '상태 기준' 한계가 API 고지문에 명시(과잉 신뢰 방지) + 무강제 명시."""
    joined = " ".join(CONTRACT_NOTES)
    assert "지면" in joined and "advisory" in joined and "차단" in joined
