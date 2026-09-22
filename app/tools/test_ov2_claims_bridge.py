# -*- coding: utf-8 -*-
"""OV-2 추출 단일화 — 체커 정규화 클레임을 propose 가 소비(재추출 제거). 겹침 code 우선·OTHER 무격상·numeric0 보존·백스톱. 실 LLM 0콜.

핵심 계약: 판정=코드, 정규화·증거강제·roster 계약 단일화. LLM 은 status·비-actor·미커버 actor·관계·신규의 백스톱으로 넓게 유지."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.world import AttributeSpec
from novelcopilot.engine.vocabulary import Vocabulary, OTHER
from novelcopilot.engine.ontology import Ontology, Entity
from novelcopilot.engine.ontology_updater import OntologyUpdater


ATTRS = [
    AttributeSpec(key="affiliation", label="소속", kind="categorical", vocab=["빛의탑", "그림자단"], mutable=True),
    AttributeSpec(key="rank", label="등급", kind="numeric", mutable=True),
    AttributeSpec(key="awakening", label="각성", kind="state", states=["미각성", "각성"], mutable=True),
]


def _vocab():
    return Vocabulary(ATTRS)


def _onto():
    o = Ontology(_vocab())
    o.add(Entity(id="leo", name="레오", etype="character", attrs={}))
    return o


class FakeBus:
    def __init__(self): self.events = []
    def emit(self, *a, **k): self.events.append((a, k))


class FakeProv:
    def __init__(self, ret): self.ret = ret
    def chat_json(self, m, temperature=0.0, schema=None): return self.ret   # OV-5: schema kwarg 수용(하위호환)


def _updater(ret=None):
    return OntologyUpdater(FakeProv(ret or {}), _vocab(), FakeBus())


# 본문 — 클레임/LLM 증거 인용이 실재해야 kept_sc 통과(≥8자)
TEXT = ("레오가 빛의탑에 정식으로 합류했다고 밝혔다. 그는 마침내 각성의 문턱을 넘어섰다. "
        "오래된 동료가 조용히 숨을 거두었다. 누군가 그림자단을 언급하기도 했다.")


# ---- A. 브리지 순수 파생 ----
def test_bridge_derives_actor_state_changes():
    claims = [{"id": "leo",
               "affiliation": "빛의탑", "affiliation_evidence": "레오가 빛의탑에 정식으로 합류했다고 밝혔다",
               "rank": 3, "rank_evidence": "등급이 3으로 올랐다는 기록",
               "awakening": "각성", "awakening_evidence": "그는 마침내 각성의 문턱을 넘어섰다"}]
    code, covered, adv = _updater()._claims_to_state_changes(claims, _onto())
    by = {c["attr"]: c for c in code}
    assert set(by) == {"affiliation", "rank", "awakening"} and adv == []
    assert by["affiliation"]["value"] == "빛의탑" and by["affiliation"]["evidence"]
    assert covered == {("leo", "affiliation"), ("leo", "rank"), ("leo", "awakening")}


def test_bridge_numeric_zero_preserved():
    # apply 계약(None/''/'null' 만 공허) — 수치 0 은 값이므로 보존(_norm_claim_map 의 0 드롭과 다름=회귀 차단)
    claims = [{"id": "leo", "rank": 0, "rank_evidence": "남은 목숨이 0이 되었다는 서술"}]
    code, covered, _ = _updater()._claims_to_state_changes(claims, _onto())
    assert len(code) == 1 and code[0]["value"] == 0 and ("leo", "rank") in covered


def test_bridge_other_routes_to_advisory_not_state_change():
    # '기타'(OTHER) → state_change 아님 + covered 에는 포함(LLM 중복 드롭용) — apply 격상 원천차단
    claims = [{"id": "leo", "affiliation": OTHER, "affiliation_evidence": "정체불명 세력에 속했다는 소문"}]
    code, covered, adv = _updater()._claims_to_state_changes(claims, _onto())
    assert code == [] and ("leo", "affiliation") in covered
    assert len(adv) == 1 and adv[0]["attr"] == "affiliation" and adv[0]["evidence"]


def test_bridge_skips_bogus_id():
    claims = [{"id": "ghost", "affiliation": "빛의탑", "affiliation_evidence": "유령이 빛의탑에 합류"}]
    code, covered, adv = _updater()._claims_to_state_changes(claims, _onto())
    assert code == [] and covered == set() and adv == []      # 미해소 id → advisory 소음 0


# ---- B. propose 병합(code 우선·백스톱·OTHER 무격상) ----
def test_propose_code_wins_llm_dup_dropped():
    # LLM 이 같은 (leo,affiliation)을 '그림자단'으로 보고(증거 유효)해도 code 의 '빛의탑'이 이김(단일 정규화계약)
    llm = {"state_changes": [
        {"id": "leo", "attr": "affiliation", "value": "그림자단", "evidence": "누군가 그림자단을 언급하기도 했다"},
        {"id": "leo", "attr": "status", "value": "dead", "evidence": "오래된 동료가 조용히 숨을 거두었다"}]}
    claims = [{"id": "leo", "affiliation": "빛의탑", "affiliation_evidence": "레오가 빛의탑에 정식으로 합류했다고 밝혔다"}]
    res = _updater(llm).propose(TEXT, _onto(), 5, claims=claims)
    scs = {(c["id"], c["attr"]): c["value"] for c in res["state_changes"]}
    assert scs[("leo", "affiliation")] == "빛의탑"              # code 승(LLM '그림자단' 드롭)
    assert scs[("leo", "status")] == "dead"                    # status=claims 미커버 → LLM 백스톱 생존


def test_propose_other_blocks_llm_raw_value_escalation():
    # claims 가 affiliation='기타'로 커버 → LLM 이 통제어휘 밖 raw '그림자길드'를 보고해도 병합서 드롭(apply 격상 불가)
    llm = {"state_changes": [
        {"id": "leo", "attr": "affiliation", "value": "그림자길드", "evidence": "누군가 그림자단을 언급하기도 했다"}]}
    claims = [{"id": "leo", "affiliation": OTHER, "affiliation_evidence": "정체불명 세력에 속했다는 소문이 돌았다"}]
    upd = _updater(llm)
    res = upd.propose(TEXT, _onto(), 5, claims=claims)
    assert all(c["attr"] != "affiliation" for c in res["state_changes"])   # raw OTHER 값 소멸(격상 원천차단)
    assert any(k[1] == "uncertain" for k, _ in upd.bus.events)             # 대신 advisory 발화


def test_propose_backstop_uncovered_actor_survives():
    # claims 가 비어(actor 미커버) → LLM state_change 가 백스톱으로 그대로 생존(커버리지 갭0)
    llm = {"state_changes": [
        {"id": "leo", "attr": "rank", "value": 5, "evidence": "레오의 등급이 5로 크게 뛰었다는 기록이 남았다"}]}
    res = _updater(llm).propose("레오의 등급이 5로 크게 뛰었다는 기록이 남았다.", _onto(), 5, claims=[])
    assert any(c["attr"] == "rank" and c["value"] == 5 for c in res["state_changes"])


def test_propose_backward_compat_none_claims():
    # claims 미전달 → 기존 풀 LLM 동작(브리지 미개입)
    llm = {"state_changes": [
        {"id": "leo", "attr": "affiliation", "value": "빛의탑", "evidence": "레오가 빛의탑에 정식으로 합류했다고 밝혔다"}]}
    res = _updater(llm).propose(TEXT, _onto(), 5)                # claims=None
    assert any(c["attr"] == "affiliation" for c in res["state_changes"])
