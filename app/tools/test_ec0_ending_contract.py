# -*- coding: utf-8 -*-
"""EC-0 엔딩 술어계약 PoC 테스트 — 실 LLM 0콜(FakeProvider) · 라이브 데이터 미접근(합성 상태만).

커버: ①검증기(화이트리스트·미등록 거부·어휘 밖 값·빈 어휘 발명값 차단·eid 정확일치 해소)
②컴파일 교정 재호출 1회(예외 격리 — 1차 유효분 보존) ③결정론 평가기(tier 2모드 분기·
satisfied/open/blocked·엣지 대칭·원장·시계) ④커버리지/권고(승격 게이트=컴파일 성공 시만)
⑤오프라인 리포트."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.world import WorldConfig, AttributeSpec, EntitySpec, TimelineEntry
from novelcopilot.domain.narrative import NarrativeSpine, EndingSpec
from novelcopilot.domain.ledger import PromiseLedger, Promise
from novelcopilot.domain.types import RelationEdge, ChapterRecord, ChapterStatus, TimeDelta

from tools.ec0_ending_contract import (
    TIER_GT, TIER_ALL, COVERAGE_FLOOR,
    build_project_ontology, build_registry, validate_predicate,
    compile_predicates, evaluate_predicate, analyze_project, run,
)


# ---------------------------------------------------------------- 합성 상태(라이브 데이터 미접근)
def _state(**over) -> ProjectState:
    world = WorldConfig(
        title="테스트작", genre="테스트",
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
        seed_edges=[
            RelationEdge(rel_id="ally_of", src_id="rival", dst_id="hero", eff_from=1),   # 대칭 → 정규화
            RelationEdge(rel_id="owns", src_id="hero", dst_id="nobody", eff_from=1),     # dangling → 드롭
        ],
        spine=NarrativeSpine(ending=EndingSpec(
            central_question="둘은 연인이 되는가?", ending="주인공과 라이벌은 연인이 된다.",
            thematic_payoff="관계의 회복")),
    )
    base = dict(
        id="ec0test", seed=ProjectSeed(title="테스트작"), world=world, current_chapter=3,
        chapters=[
            ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED,
                          time_delta=TimeDelta(amount=1, unit="day", mode="advance")),
            ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED,
                          time_delta=TimeDelta(amount=2, unit="day", mode="advance")),
            ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, time_delta=None),   # 미상 → 시계 하향정직
        ],
        runtime_timeline=[
            TimelineEntry(entity_id="hero", attr="closeness", value="친구", eff_from=2,
                          trust_tier="ground_truth"),
            TimelineEntry(entity_id="hero", attr="closeness", value="연인", eff_from=3,
                          trust_tier="narrative_inferred"),                              # tier 분기 재료
        ],
        promise_ledger=PromiseLedger(promises=[
            Promise(id="약속하나", text="약속 하나", status="paid", paid_chapter=2),
            Promise(id="약속둘", text="약속 둘", status="open"),
        ]),
    )
    base.update(over)
    return ProjectState(**base)


class FakeProvider:
    """chat_json 만 구현(LLM 0콜) — 큐에 넣은 응답을 순서대로 반환."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_json(self, messages, *, temperature=0.0, max_tokens=2000):
        self.calls += 1
        return self.responses.pop(0)


# ---------------------------------------------------------------- ① 재수화(읽기 전용 온톨로지)
def test_build_ontology_seed_edge_validation_and_runtime_merge():
    st = _state()
    ont = build_project_ontology(st)
    # dangling seed edge 드롭 + 대칭(ally_of) 정규화 적재
    rels = [(e.rel_id, e.src_id, e.dst_id) for e in ont.edges]
    assert ("owns", "hero", "nobody") not in rels
    assert any(r == "ally_of" for r, _, _ in rels)
    # runtime timeline tier 보존
    assert ont.binding_state_as_of("hero", "closeness", 3) == "친구"      # gt 만
    assert ont.state_as_of("hero", "closeness", 3) == "연인"              # ni 포함


# ---------------------------------------------------------------- ② 검증기(등록부 멤버십)
def test_validate_rejects_unregistered_and_out_of_vocab():
    st = _state()
    reg = build_registry(st, build_project_ontology(st))
    ok, why = validate_predicate({"type": "attr_equals", "eid": "없는사람", "attr": "closeness",
                                  "value": "연인"}, reg)
    assert ok is None and why.startswith("unregistered_eid")
    ok, why = validate_predicate({"type": "attr_equals", "eid": "hero", "attr": "미등록속성",
                                  "value": "x"}, reg)
    assert ok is None and why.startswith("unregistered_attr")
    ok, why = validate_predicate({"type": "attr_equals", "eid": "hero", "attr": "closeness",
                                  "value": "어휘밖신조어"}, reg)
    assert ok is None and why.startswith("value_out_of_vocab")
    ok, why = validate_predicate({"type": "edge_active", "src": "hero", "dst": "rival",
                                  "rel_id": "미등록관계"}, reg)
    assert ok is None and why.startswith("unregistered_rel_id")
    ok, why = validate_predicate({"type": "promise_paid", "promise_id": "없는약속"}, reg)
    assert ok is None and why.startswith("unregistered_promise")
    ok, why = validate_predicate({"type": "완전히다른타입", "eid": "hero"}, reg)
    assert ok is None and why.startswith("unknown_type")
    ok, why = validate_predicate({"type": "clock_at_least", "amount": 3, "unit": "광년"}, reg)
    assert ok is None and why.startswith("unknown_clock_unit")


def test_validate_resolves_exact_name_and_alias_not_substring():
    st = _state()
    reg = build_registry(st, build_project_ontology(st))
    ok, _ = validate_predicate({"type": "alive", "eid": "주인공"}, reg)      # 정확 이름
    assert ok and ok["eid"] == "hero"
    ok, _ = validate_predicate({"type": "alive", "eid": "히어로"}, reg)      # 별칭
    assert ok and ok["eid"] == "hero"
    ok, why = validate_predicate({"type": "alive", "eid": "주인"}, reg)      # 부분 문자열 금지
    assert ok is None and why.startswith("unregistered_eid")


def test_validate_accepts_observed_ssot_value_and_promise_text_echo():
    st = _state()
    ont = build_project_ontology(st)
    ont.set_state("hero", "closeness", "선언밖관측값", 1)                     # SSOT 관측값
    reg = build_registry(st, ont)
    ok, _ = validate_predicate({"type": "attr_equals", "eid": "hero", "attr": "closeness",
                                "value": "선언밖관측값"}, reg)
    assert ok is not None
    ok, _ = validate_predicate({"type": "promise_paid", "promise_id": "약속 하나"}, reg)   # 텍스트 echo → 정규화 해소
    assert ok and ok["promise_id"] == "약속하나"


def test_validate_empty_vocab_attr_rejects_invented_value():
    """선언+관측 어휘가 모두 빈 속성 — 발명 값 무사통과 금지(numeric 은 숫자만 통과)."""
    st = _state()
    st.world.attributes = list(st.world.attributes) + [
        AttributeSpec(key="level", label="레벨", kind="numeric", mutable=True)]
    reg = build_registry(st, build_project_ontology(st))
    assert reg["attrs"]["level"]["declared"] == [] and reg["attrs"]["level"]["observed"] == []
    ok, why = validate_predicate({"type": "attr_equals", "eid": "hero", "attr": "level",
                                  "value": "천상천하유아독존"}, reg)
    assert ok is None and why.startswith("value_out_of_vocab")     # 자유 텍스트 발명 값 거부
    ok, _ = validate_predicate({"type": "attr_equals", "eid": "hero", "attr": "level",
                                "value": "50"}, reg)
    assert ok is not None                                          # 숫자 값은 결정론 비교 가능


# ---------------------------------------------------------------- ③ 컴파일(교정 재호출 1회)
def test_compile_corrective_recall_once():
    st = _state()
    reg = build_registry(st, build_project_ontology(st))
    ending = {"central_question": "q", "ending": "e", "thematic_payoff": "t"}
    bad = {"type": "attr_equals", "eid": "환각인물", "attr": "closeness", "value": "연인"}
    good = {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"}
    fp = FakeProvider([{"predicates": [bad, good]},
                       {"predicates": [good, {"type": "alive", "eid": "hero"}]}])
    r = compile_predicates(fp, ending, reg)
    assert fp.calls == 2 and r["corrective_used"] and r["llm_calls"] == 2
    assert len(r["predicates"]) == 2 and r["adopted_pass"] == 1
    assert len(r["passes"]) == 2 and len(r["passes"][0]["rejected"]) == 1


def test_compile_no_recall_when_all_valid_and_empty_recall_keeps_pass1():
    st = _state()
    reg = build_registry(st, build_project_ontology(st))
    ending = {"ending": "e"}
    good = {"type": "alive", "eid": "hero"}
    fp = FakeProvider([{"predicates": [good]}])
    r = compile_predicates(fp, ending, reg)
    assert fp.calls == 1 and not r["corrective_used"] and r["adopted_pass"] == 0
    # 교정본이 빈손이면 1차 유효분 유지(adopted_pass=0)
    bad = {"type": "alive", "eid": "환각"}
    fp2 = FakeProvider([{"predicates": [good, bad]}, {"predicates": [bad]}])
    r2 = compile_predicates(fp2, ending, reg)
    assert r2["adopted_pass"] == 0 and len(r2["predicates"]) == 1


def test_compile_llm_failure_recorded_not_raised():
    class Boom:
        def chat_json(self, *a, **k):
            raise RuntimeError("no api key")
    r = compile_predicates(Boom(), {"ending": "e"},
                           build_registry(_state(), build_project_ontology(_state())))
    assert r["error"] and r["predicates"] == [] and r["llm_calls"] == 0


class _FlakyCorrective:
    """1차 콜 성공(유효 2 + 거부 1) → 교정(2차) 콜만 예외."""
    def __init__(self):
        self.calls = 0

    def chat_json(self, messages, *, temperature=0.0, max_tokens=2000):
        self.calls += 1
        if self.calls == 1:
            return {"predicates": [
                {"type": "alive", "eid": "hero"},
                {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"},
                {"type": "alive", "eid": "환각"},
            ]}
        raise TimeoutError("transient outage")


def test_compile_corrective_call_exception_keeps_pass1():
    """교정 콜 예외는 격리 — 이미 확보한 1차 유효분을 소실시키지 않는다(빈손 폴백과 대칭)."""
    st = _state()
    reg = build_registry(st, build_project_ontology(st))
    r = compile_predicates(_FlakyCorrective(), {"ending": "e"}, reg)
    assert r["error"] == "" and r["corrective_error"]             # 본 에러 아님 · 교정 실패만 기록
    assert len(r["predicates"]) == 2 and r["adopted_pass"] == 0   # 1차 유효분 유지
    assert r["llm_calls"] == 1 and r["corrective_used"] is True


def test_corrective_failure_does_not_pollute_escalation():
    """인프라 장애(교정 콜 예외)가 coverage=0 → 승격 권고로 둔갑하지 않는다."""
    p = analyze_project(_state(), _FlakyCorrective())
    assert p["compile"]["corrective_error"] and not p["compile"]["error"]
    assert p["coverage"] == 0.667                                 # 2/3 — 1차 패스 기준 유효 커버리지
    assert p["escalate_extraction_first"] is False


def test_escalation_requires_successful_compile():
    """컴파일 미실행(오프라인)·1차 콜 실패 작품은 escalate 금지 — coverage=0.0 이 무의미하므로."""
    assert analyze_project(_state(), provider=None)["escalate_extraction_first"] is False

    class Boom:
        def chat_json(self, *a, **k):
            raise RuntimeError("no api key")
    p = analyze_project(_state(), Boom())
    assert p["compile"]["error"] and p["escalate_extraction_first"] is False

    class Mixed:                                                  # 작품1 성공 · 작품2 콜 실패
        def __init__(self):
            self.calls = 0

        def chat_json(self, *a, **k):
            self.calls += 1
            if self.calls == 1:
                return {"predicates": [{"type": "alive", "eid": "hero"}]}
            raise RuntimeError("outage")
    report = run([_state(), _state(id="ec0test2")], Mixed())
    assert report["llm_executed"] is True                         # 성공 작품 덕에 게이트 통과해도
    assert "추출 보강" not in report["summary"]["recommendation"]  # 실패 작품이 권고 대상에 오르지 않음
    assert report["projects"][1]["compile"]["error"]
    assert report["projects"][1]["escalate_extraction_first"] is False


# ---------------------------------------------------------------- ④ 결정론 평가(tier 2모드)
def _eval(st, pred, tiers, ch=3):
    ont = build_project_ontology(st)
    deltas = [c.time_delta for c in sorted(st.chapters, key=lambda c: c.chapter)
              if c.status == ChapterStatus.FINALIZED]
    return evaluate_predicate(pred, ont, st.promise_ledger, deltas, ch, tiers,
                              st.world.allow_state_reversal)


def test_attr_equals_tier_divergence():
    st = _state()
    pred = {"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"}
    assert _eval(st, pred, TIER_GT)["result"] == "open"          # gt 캐논은 아직 '친구'
    assert _eval(st, pred, TIER_ALL)["result"] == "satisfied"    # ni 포함이면 '연인'


def test_attr_in_and_numeric_coercion():
    st = _state()
    ok = {"type": "attr_in", "eid": "hero", "attr": "closeness", "values": ["친구", "연인"]}
    assert _eval(st, ok, TIER_GT)["result"] == "satisfied"
    ont = build_project_ontology(st)
    ont.set_state("hero", "level", "3", 1)
    r = evaluate_predicate({"type": "attr_equals", "eid": "hero", "attr": "level", "value": "3.0"},
                           ont, st.promise_ledger, [], 3, TIER_GT, False)
    assert r["result"] == "satisfied"                            # 숫자 코어션 동등


def test_alive_terminal_and_blocked():
    st = _state()
    assert _eval(st, {"type": "alive", "eid": "hero"}, TIER_GT)["result"] == "satisfied"
    assert _eval(st, {"type": "terminal", "eid": "ghost"}, TIER_GT)["result"] == "satisfied"
    # 비가역 terminal(dead) + reversal 비허용 → alive 는 blocked
    assert _eval(st, {"type": "alive", "eid": "ghost"}, TIER_GT)["result"] == "blocked"
    # reversal 허용 세계면 open(부활 가능)
    st2 = _state()
    st2.world.allow_state_reversal = True
    assert _eval(st2, {"type": "alive", "eid": "ghost"}, TIER_GT)["result"] == "open"


def test_attr_blocked_when_irreversibly_stuck():
    st = _state()
    pred = {"type": "attr_equals", "eid": "ghost", "attr": "status", "value": "alive"}
    assert _eval(st, pred, TIER_GT)["result"] == "blocked"       # dead 는 비가역


def test_edge_active_symmetric_and_absent_and_blocked():
    st = _state()
    # seed 는 rival→hero 로 넣었지만 ally_of 는 대칭 → 어느 방향으로 물어도 성립
    act = {"type": "edge_active", "src": "hero", "dst": "rival", "rel_id": "ally_of"}
    assert _eval(st, act, TIER_GT)["result"] == "satisfied"
    absent = {"type": "edge_absent", "src": "hero", "dst": "rival", "rel_id": "enemy_of"}
    assert _eval(st, absent, TIER_GT)["result"] == "satisfied"
    # 제거(사망) 끝점과의 새 관계 → blocked
    dead_edge = {"type": "edge_active", "src": "hero", "dst": "ghost", "rel_id": "ally_of"}
    assert _eval(st, dead_edge, TIER_GT)["result"] == "blocked"


def test_edge_tier_filter():
    st = _state()
    st.runtime_edges = [RelationEdge(edge_id="x", rel_id="loves", src_id="hero", dst_id="rival",
                                     eff_from=2, trust_tier="narrative_inferred")]
    pred = {"type": "edge_active", "src": "hero", "dst": "rival", "rel_id": "loves"}
    assert _eval(st, pred, TIER_GT)["result"] == "open"          # ni 엣지는 gt 모드 비가시
    assert _eval(st, pred, TIER_ALL)["result"] == "satisfied"


def test_promise_and_clock():
    st = _state()
    assert _eval(st, {"type": "promise_paid", "promise_id": "약속하나"}, TIER_GT)["result"] == "satisfied"
    assert _eval(st, {"type": "promise_paid", "promise_id": "약속둘"}, TIER_ALL)["result"] == "open"
    # 누적 3일(1+2, 3화는 미상 → 하향정직) — 2일 이상 satisfied, 10일 open
    assert _eval(st, {"type": "clock_at_least", "amount": 2, "unit": "day"}, TIER_GT)["result"] == "satisfied"
    r = _eval(st, {"type": "clock_at_least", "amount": 10, "unit": "day"}, TIER_ALL)
    assert r["result"] == "open" and "약" in r["observed"]        # 미상 구간 → '약' 헤지


# ---------------------------------------------------------------- ⑤ 커버리지·권고·리포트(오프라인)
def test_analyze_project_coverage_and_escalation_flag():
    st = _state()
    bad = {"type": "alive", "eid": "환각1"}
    preds = [bad, {"type": "alive", "eid": "환각2"}, {"type": "alive", "eid": "환각3"},
             {"type": "alive", "eid": "hero"}]
    fp = FakeProvider([{"predicates": preds}, {"predicates": preds}])   # 교정해도 1/4 유효
    p = analyze_project(st, fp)
    assert p["coverage"] == 0.25 < COVERAGE_FLOOR
    assert p["escalate_extraction_first"] is True
    assert p["compile"]["rejected_total"] == 6                   # 3 + 3(교정 패스)


def test_run_offline_notes_and_no_llm():
    report = run([_state()], provider=None)
    assert report["llm_executed"] is False
    assert any("LLM 컴파일 미실행" in n for n in report["notes"])
    assert any("참고 지표" in n for n in report["notes"])         # 미완결 → satisfied 분포는 참고
    p = report["projects"][0]
    assert p["compile"]["error"].startswith("llm_unavailable")
    assert p["predicates"] == [] and p["coverage"] == 0.0
    assert report["summary"]["recommendation"] == ""             # 미실행 수치로 승격 권고 안 함


def test_run_aggregates_tier_distribution_and_recommendation():
    st = _state()
    good = [{"type": "attr_equals", "eid": "hero", "attr": "closeness", "value": "연인"},
            {"type": "alive", "eid": "hero"}, {"type": "promise_paid", "promise_id": "약속하나"}]
    fp = FakeProvider([{"predicates": good}])
    report = run([st], provider=fp)
    assert report["llm_executed"] is True
    s = report["summary"]
    assert s["predicates_valid"] == 3 and s["coverage_overall"] == 1.0
    gt = s["tier_distribution"]["ground_truth_only"]
    ni = s["tier_distribution"]["with_narrative_inferred"]
    assert gt["satisfied"] == 2 and gt["open"] == 1              # closeness=연인 은 gt 에선 미결
    assert ni["satisfied"] == 3
    assert "EC-1" in s["recommendation"]                          # 60% 이상 → 본구현 신호


def test_no_ending_spec_skips_compile_without_llm():
    st = _state()
    st.world.spine = None
    fp = FakeProvider([])                                        # 호출되면 pop 에서 터짐
    p = analyze_project(st, fp)
    assert p["compile"]["error"] == "no_ending_spec" and fp.calls == 0


def test_render_and_write_reports(tmp_path):
    from tools.ec0_ending_contract import write_reports, render_md
    report = run([_state()], provider=None)
    jp, mp = write_reports(report, tmp_path)
    assert jp.exists() and mp.exists()
    md = render_md(report)
    assert "EC-0" in md and "커버리지" in md
