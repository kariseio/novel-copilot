# -*- coding: utf-8 -*-
"""EC-1 엔딩 술어계약 감시 레이어 — EC-0(도구 PoC) 검증 로직의 엔진 승격.

무엇: 작품의 EndingSpec 프로즈(중심 질문/확정 엔딩/주제 보상)를 LLM 1콜(+교정 1콜)로 '결정론 술어'
계약으로 컴파일하고, 이후 매 FINALIZED 회차마다 **LLM 0콜**로 평가해 "엔딩이 상태로 얼마나 실현됐나"를
상시 감시한다. 완결(아크 소진) 시점에 미정산이면 advisory 로 표시만 한다(완결 차단·자동 에필로그 0).

무강제 원칙: 이 모듈의 어떤 출력도 게이트·자동 재작성·판정기에 연결되지 않는다 — advisory surface 만.
컴파일 실패는 조용히 스킵+이벤트(생성 차단 금지).

LR-1 실측 3건 반영(설계 입력):
  ① tier-출처 갭(arm1): 평가는 ground_truth(작가/시드 확정)·narrative_inferred(기계추출 포함) 양 tier 를
     *병렬 산출·병렬 표시*한다(단일 판정 금지). ni-satisfied & gt-open 항목은 '작가 승격 유도' 신호로 surface
     (자동 승격 없음 — 승격은 작가 확정 행위).
  ② 술어 satisfied ≠ 지면 실현(arm2): 이 감시는 SSOT '상태 기준'이다 — 술어가 satisfied 여도 그 귀결이
     프로즈(지면)에 극화됐다는 보장이 아니다(술어 감시의 본질 한계). UI/API 문구도 상태 기준임을 밝힌다
     (CONTRACT_NOTES — 과잉 신뢰 방지).
  ③ 등록부 표현력 갭(arm3): 컴파일에서 거부된 엔딩 개념(미등록 eid 등)은 버리지 않고 '미표현 엔딩 요소'
     (UnexpressedEnding)로 영속·surface — 등록부 승격(인물/속성/관계 등록) 유도. 발명 금지는 유지(자동 등록 0).

blocked(도달 불가) 판정은 보수 결정론 규칙 3종만 — kill criteria(오탐 방지 가드, 테스트로 사전 등록):
  R1 terminal 충돌: 대상 엔티티가 비가역 terminal 상태(사망 등)라 술어가 구조적으로 도달 불가.
     가드: allow_state_reversal 세계 제외 · provisional(AI 미확정) 엔티티 제외 · pov(믿음) 엣지 비대상.
  R2 CN-4 카디널리티 상한 충돌: edge_active 끝점의 '선언 상한 0'(외동={"sibling_of":0} 등)만 blocked.
     가드: 상한>0 '가득참'은 기존 엣지 종료(eff_to)로 해소 가능 → open(blocked 아님).
  R3 참조 약속 소멸: promise_paid 가 참조하는 약속이 원장에서 사라짐(컴파일 시 검증됐으므로 소멸=도달 불가).
     가드: status=open 인 약속은 open(미결)이지 blocked 가 아니다.
advisory 라 임계·라벨·자동반응 0(판정기 금지 준수) — blocked 도 '표시'일 뿐 아무것도 막지 않는다.

허용 술어 타입(화이트리스트, 닫힌 집합 — 이외 전부 거부):
  attr_equals / attr_in     → Ontology.binding_state_as_of(gt만) · state_as_of(ni 포함)
  edge_active / edge_absent → Ontology.edges_as_of(tier 필터, pov=None 객관 엣지만)
  terminal / alive          → Ontology._in_terminal_state 의미론(tier 모드별 lookup 치환)
  promise_paid              → PromiseLedger(비구속 회계 — tier 무관, 두 모드 동일)
  clock_at_least            → story_clock.elapsed_minutes(tier 무관, 두 모드 동일)
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..domain.narrative import EndingContract, ContractPredicate, UnexpressedEnding
from .story_clock import UNIT_MINUTES, elapsed_minutes, format_elapsed

PREDICATE_TYPES = ("attr_equals", "attr_in", "edge_active", "edge_absent",
                   "terminal", "alive", "promise_paid", "clock_at_least")
TIER_GT = ("ground_truth",)
TIER_ALL = ("ground_truth", "narrative_inferred")

# API/UI 로 함께 노출하는 계약 성격 고지(LR-1 arm1/arm2/arm3 — 과잉 신뢰 방지)
CONTRACT_NOTES = [
    "이 감시는 advisory 입니다 — 어떤 차단·자동반응에도 연결되지 않습니다(무강제).",
    "'충족'은 설정(SSOT) 상태 기준입니다 — 본문(지면)에 그 귀결이 극화됐다는 보장이 아닙니다.",
    "확정(작가/시드)과 추출포함(기계추출) 두 기준을 병렬로 보여줍니다 — 단일 판정을 내리지 않습니다.",
    "추출포함 충족·확정 미결 항목은 '작가 확정(승격)' 후보 신호입니다 — 자동 승격은 하지 않습니다.",
    "미표현 요소는 등록부(인물·속성·관계·약속)에 없어 감시할 수 없는 엔딩 개념입니다 — 등록하면 재컴파일 때 반영됩니다.",
]


# ---------------------------------------------------------------- 등록부(컴파일 검증의 결정론 기준)
def build_registry(state, ont) -> dict:
    """LLM 이 참조 가능한 '등록된 것'의 전량 — 검증은 이 등록부 멤버십만 본다(발명 차단).
    값 어휘 = '선언 vocab/states ∪ SSOT 관측값'(관측값은 SSOT 등록 사실이므로 결정론 비교 가능)."""
    attrs: dict[str, dict] = {}

    def _attr_slot(key: str) -> dict:
        if key not in attrs:
            spec = ont.vocab.attr(key)
            declared: list[str] = []
            kind = "free"
            if spec is not None:
                kind = spec.kind
                declared = list(spec.vocab) + list(spec.states)
            elif key == "status":
                kind, declared = "state", ["alive", "dead"]      # DEFAULT_STATUS_ATTR 하위호환
            attrs[key] = {"label": ont.vocab.label(key), "kind": kind,
                          "declared": declared, "observed": []}
        return attrs[key]

    for a in state.world.attributes:
        _attr_slot(a.key)
    _attr_slot("status")
    for e in ont.entities.values():
        for k, v in e.attrs.items():
            slot = _attr_slot(k)
            if str(v) not in slot["observed"]:
                slot["observed"].append(str(v))
        slot = _attr_slot("status")
        if e.base_status and str(e.base_status) not in slot["observed"]:
            slot["observed"].append(str(e.base_status))
    for (eid, attr, val, _f, _r, _t) in ont.timeline:            # 관측값 = SSOT 등록 사실
        slot = _attr_slot(attr)
        if str(val) not in slot["observed"]:
            slot["observed"].append(str(val))

    rel_ids = sorted(set(ont.rel_catalog.keys()) | {e.rel_id for e in ont.edges})
    promises = [{"id": p.id, "text": p.text, "status": p.status}
                for p in state.promise_ledger.promises]
    return {
        "entities": [{"id": e.id, "name": e.name, "aliases": list(e.aliases), "etype": e.etype}
                     for e in ont.entities.values()],
        "attrs": attrs,
        "rel_ids": rel_ids,
        "promises": promises,
        "clock_units": sorted(UNIT_MINUTES.keys()),
        "allow_state_reversal": bool(state.world.allow_state_reversal),
    }


def _resolve_eid(ref, registry: dict):
    """eid 해소 — id/이름/별칭 '정확 일치'만(한국어 조사 substring 함정 회피: 부분일치 금지)."""
    r = str(ref or "").strip()
    if not r:
        return None
    for e in registry["entities"]:
        if r == e["id"] or r == e["name"] or r in e["aliases"]:
            return e["id"]
    return None


def _allowed_values(attr_slot: dict) -> set[str]:
    return {str(v) for v in attr_slot["declared"]} | {str(v) for v in attr_slot["observed"]}


def _is_number(v) -> bool:
    try:
        float(str(v).strip())
        return True
    except (TypeError, ValueError):
        return False


def validate_predicate(raw: dict, registry: dict) -> tuple[dict | None, str | None]:
    """화이트리스트+등록부 멤버십 검증. 통과 → 정규화 술어(dict), 실패 → (None, 사유)."""
    if not isinstance(raw, dict):
        return None, "not_a_dict"
    ptype = str(raw.get("type") or "").strip()
    if ptype not in PREDICATE_TYPES:
        return None, f"unknown_type:{ptype}"
    note = str(raw.get("note") or "").strip()   # 절단 전면 제거(2026-08-21): 작가 가시화 기록 전문

    if ptype in ("attr_equals", "attr_in"):
        eid = _resolve_eid(raw.get("eid"), registry)
        if eid is None:
            return None, f"unregistered_eid:{raw.get('eid')}"
        attr = str(raw.get("attr") or "").strip()
        slot = registry["attrs"].get(attr)
        if slot is None:
            return None, f"unregistered_attr:{attr}"
        vals = [raw.get("value")] if ptype == "attr_equals" else list(raw.get("values") or [])
        vals = [str(v).strip() for v in vals if str(v or "").strip()]
        if not vals:
            return None, "empty_value"
        allowed = _allowed_values(slot)
        for v in vals:
            if slot["kind"] == "numeric" and _is_number(v):
                continue                                           # 숫자 속성의 숫자 값은 결정론 비교 가능
            if v not in allowed:                                   # 어휘 공집합이어도 발명 값 무사통과 금지
                return None, f"value_out_of_vocab:{attr}={v}"
        if ptype == "attr_equals":
            return {"type": ptype, "eid": eid, "attr": attr, "value": vals[0], "note": note}, None
        return {"type": ptype, "eid": eid, "attr": attr, "values": vals, "note": note}, None

    if ptype in ("edge_active", "edge_absent"):
        src = _resolve_eid(raw.get("src"), registry)
        dst = _resolve_eid(raw.get("dst"), registry)
        if src is None or dst is None:
            return None, f"unregistered_eid:{raw.get('src') if src is None else raw.get('dst')}"
        if src == dst:
            return None, "self_loop"
        rel = str(raw.get("rel_id") or "").strip()
        if rel not in registry["rel_ids"]:
            return None, f"unregistered_rel_id:{rel}"
        return {"type": ptype, "src": src, "dst": dst, "rel_id": rel, "note": note}, None

    if ptype in ("terminal", "alive"):
        eid = _resolve_eid(raw.get("eid"), registry)
        if eid is None:
            return None, f"unregistered_eid:{raw.get('eid')}"
        return {"type": ptype, "eid": eid, "note": note}, None

    if ptype == "promise_paid":
        pid = str(raw.get("promise_id") or "").strip()
        ids = {p["id"] for p in registry["promises"]}
        if pid not in ids:
            norm = re.sub(r"\s+", "", pid.lower())[:40]          # ledger_ops._key 와 동일 정규화(텍스트 echo 허용)
            if norm in ids:
                pid = norm
            else:
                return None, f"unregistered_promise:{pid[:40]}"
        return {"type": ptype, "promise_id": pid, "note": note}, None

    # clock_at_least
    unit = str(raw.get("unit") or "").strip().lower()
    if unit not in UNIT_MINUTES:
        return None, f"unknown_clock_unit:{unit}"
    try:
        amount = float(raw.get("amount"))
    except (TypeError, ValueError):
        return None, "bad_clock_amount"
    if amount <= 0:
        return None, "bad_clock_amount"
    return {"type": ptype, "amount": amount, "unit": unit, "note": note}, None


# ---------------------------------------------------------------- LLM 컴파일(작품당 1콜 + 교정 1콜)
_SYSTEM = (
    "너는 서사 시스템의 '엔딩 계약 컴파일러'다. 작품의 엔딩 서술(자연어)을 기계가 결정론적으로 "
    "평가할 수 있는 술어 3~7개로 변환하라.\n"
    "규칙:\n"
    "1) 반드시 [등록부]에 존재하는 entity id(또는 정확한 이름)/attr/값/rel_id/promise id 만 사용하라. "
    "등록부에 없는 개념은 술어로 만들지 말고 버려라(발명 금지).\n"
    "2) 허용 타입(닫힌 집합, 이외 금지)과 스키마:\n"
    '  {"type":"attr_equals","eid":"","attr":"","value":""}  — 엔딩 시점에 그 속성이 그 값\n'
    '  {"type":"attr_in","eid":"","attr":"","values":["",""]}  — 값이 목록 중 하나\n'
    '  {"type":"edge_active","src":"","dst":"","rel_id":""}  — 그 관계가 성립해 있음\n'
    '  {"type":"edge_absent","src":"","dst":"","rel_id":""}  — 그 관계가 없음(해소됨)\n'
    '  {"type":"terminal","eid":""}  — 그 존재가 제거 상태(사망 등)\n'
    '  {"type":"alive","eid":""}  — 제거 상태가 아님\n'
    '  {"type":"promise_paid","promise_id":""}  — 그 약속이 지불(회수)됨\n'
    '  {"type":"clock_at_least","amount":1,"unit":"day"}  — 서사 시간이 최소 그만큼 경과\n'
    "3) 각 술어에 note(근거가 된 엔딩 서술 구절 요약 한 줄)를 달아라.\n"
    "4) 엔딩 서술의 핵심(주인공 최종 상태·관계 귀결·핵심 약속 회수)을 우선하라. "
    "등록부로 표현 불가능한 주제적 내용은 건너뛴다.\n"
    '출력은 JSON 객체만: {"predicates":[...]}'
)


@promptlog.stage("ending_contract")
def compile_predicates(provider, ending: dict, registry: dict, max_predicates: int = 7) -> dict:
    """EndingSpec 프로즈 → 검증된 결정론 술어. LLM 1콜(+거부 발생 시 교정 재호출 1회).
    반환: {predicates, passes:[{proposed,valid,rejected:[{raw,reason}]}], adopted_pass, corrective_used,
          llm_calls, error, corrective_error}
    adopted_pass = 채택된 패스 인덱스(교정본이 빈손/실패면 1차 유효분 유지 → 0).
    교정 콜 예외는 격리(corrective_error 에만 기록) — 이미 확보한 1차 유효분을 파괴하지 않는다."""
    reg_prompt = {
        "entities": registry["entities"][:40],
        "attrs": {k: {"label": v["label"], "kind": v["kind"],
                      "values": (v["declared"] + [x for x in v["observed"] if x not in v["declared"]])[:20]}
                  for k, v in registry["attrs"].items()},
        "rel_ids": registry["rel_ids"],
        "promises": [{"id": p["id"], "text": p["text"], "status": p["status"]}   # 절단 전면 제거(2026-08-21): 약속 전문(건수는 리스트 캡)
                     for p in registry["promises"][:30]],
        "clock_units": registry["clock_units"],
    }
    user = ("[등록부]\n" + json.dumps(reg_prompt, ensure_ascii=False)
            + "\n\n[엔딩 서술]\n중심 질문: " + (ending.get("central_question") or "")
            + "\n확정 엔딩: " + (ending.get("ending") or "")
            + "\n주제적 보상: " + (ending.get("thematic_payoff") or ""))
    result = {"predicates": [], "passes": [], "adopted_pass": 0, "corrective_used": False,
              "llm_calls": 0, "error": "", "corrective_error": ""}
    messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]

    def _one_pass(msgs) -> tuple[list[dict], list[dict], str]:
        raw_list = (provider.chat_json(msgs, temperature=0.0).get("predicates") or [])
        raw_list = raw_list[:max_predicates] if isinstance(raw_list, list) else []
        valid, rejected = [], []
        for raw in raw_list:
            norm, reason = validate_predicate(raw, registry)
            if norm is not None:
                valid.append(norm)
            else:
                rejected.append({"raw": raw, "reason": reason})
        return valid, rejected, json.dumps(raw_list, ensure_ascii=False)

    try:
        valid, rejected, echo = _one_pass(messages)
        result["llm_calls"] += 1
        result["passes"].append({"proposed": len(valid) + len(rejected), "valid": len(valid),
                                 "rejected": rejected})
        if rejected:                                               # 교정 재호출 1회(그 이상 없음)
            result["corrective_used"] = True
            fix_msg = ("다음 술어가 검증에서 거부됐다(등록부 밖 참조):\n"
                       + "\n".join(f"- {json.dumps(r['raw'], ensure_ascii=False)} → {r['reason']}"
                                   for r in rejected)
                       + "\n등록부에 실제로 존재하는 id/attr/값만 사용해 전체 술어 목록(3~7개)을 다시 출력하라. "
                         "표현 불가능한 항목은 빼라.")
            messages2 = messages + [{"role": "assistant", "content": echo},
                                    {"role": "user", "content": fix_msg}]
            try:                                                   # 교정 콜만 격리 — 예외가 1차 유효분을 소실시키지 않게
                valid2, rejected2, _ = _one_pass(messages2)
            except Exception as e2:
                result["corrective_error"] = f"{type(e2).__name__}: {e2}"
            else:
                result["llm_calls"] += 1
                result["passes"].append({"proposed": len(valid2) + len(rejected2), "valid": len(valid2),
                                         "rejected": rejected2})
                if valid2:                                         # 교정본 채택(빈손이면 1차 유효분 유지)
                    valid = valid2
                    result["adopted_pass"] = 1
        result["predicates"] = valid
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def ending_fingerprint(ending: dict) -> str:
    """EndingSpec 프로즈의 결정론 지문 — 계약이 어떤 엔딩에서 컴파일됐는지 추적(불일치=stale advisory)."""
    key = json.dumps({k: (ending.get(k) or "").strip()
                      for k in ("central_question", "ending", "thematic_payoff")},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def compile_contract(provider, state, ont, source: str) -> EndingContract:
    """작품 엔딩 프로즈 → 영속 술어계약(LLM 1콜 + 교정 1콜). 예외를 던지지 않는다 — 실패는 error 필드
    (호출부는 이벤트만 emit — 생성/개정 차단 금지). 거부 개념은 '미표현 엔딩 요소'로 영속(LR-1 arm3)."""
    ending = (state.world.spine.ending.model_dump()
              if state.world.spine and state.world.spine.ending else {})
    meta = dict(ending_fingerprint=ending_fingerprint(ending),
                compiled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                compiled_chapter=int(state.current_chapter or 0), source=source)
    if not any((ending.get(k) or "").strip()
               for k in ("central_question", "ending", "thematic_payoff")):
        return EndingContract(error="no_ending_spec", **meta)
    try:
        registry = build_registry(state, ont)
        compiled = compile_predicates(provider, ending, registry)
    except Exception as e:                                        # compile_predicates 는 자체 격리 — 이중 방어
        return EndingContract(error=f"{type(e).__name__}: {e}", **meta)
    if compiled["error"]:
        return EndingContract(error=compiled["error"], llm_calls=compiled["llm_calls"], **meta)
    adopted = (compiled["passes"][compiled["adopted_pass"]] if compiled["passes"]
               else {"rejected": []})
    unexpressed = [UnexpressedEnding(raw=(r["raw"] if isinstance(r["raw"], dict)
                                          else {"value": str(r["raw"])}),
                                     reason=str(r["reason"] or ""))
                   for r in adopted["rejected"]]                  # 채택 패스의 최종 거부분 = 미표현 요소
    fields = set(ContractPredicate.model_fields)
    preds = [ContractPredicate(**{k: v for k, v in p.items() if k in fields})
             for p in compiled["predicates"]]
    return EndingContract(predicates=preds, unexpressed=unexpressed,
                          llm_calls=compiled["llm_calls"], **meta)


# ---------------------------------------------------------------- 결정론 평가(LLM 0콜)
def _lookup(ont, eid: str, attr: str, chapter: int, tiers: tuple):
    """tier 모드별 상태 조회 — 기존 평가기 위임(gt=binding_state_as_of, ni 포함=state_as_of)."""
    if tiers == TIER_GT:
        return ont.binding_state_as_of(eid, attr, chapter)
    return ont.state_as_of(eid, attr, chapter)


def _in_terminal(ont, eid: str, chapter: int, tiers: tuple) -> bool:
    """Ontology._in_terminal_state 와 동일 의미론 — lookup 만 tier 모드로 치환(엔진 무변경)."""
    if tiers == TIER_GT:
        return ont._in_terminal_state(eid, chapter)
    attrs = {"status"} | {t[1] for t in ont.timeline}
    for attr in attrs:
        term = ont.vocab.terminal_states(attr)
        if term and _lookup(ont, eid, attr, chapter, tiers) in term:
            return True
    return False


def _blockable(ont, eid: str) -> bool:
    """kill criteria 공통 가드: blocked 근거는 '확정(non-provisional) 엔티티'의 사실/선언만.
    provisional(AI 자동커밋 미확정) 엔티티의 terminal/선언은 봉쇄 근거로 쓰지 않는다(오탐 소스 차단)."""
    e = ont.entities.get(eid)
    return e is not None and not getattr(e, "provisional", False)


def _irreversibly_stuck(ont, eid: str, attr: str, chapter: int, tiers: tuple,
                        allow_reversal: bool) -> bool:
    """R1: 현재 값이 비가역 상태 → (reversal 비허용 세계에서) 다른 값으로 갈 수 없음. provisional 제외."""
    if allow_reversal or not _blockable(ont, eid):
        return False
    cur = _lookup(ont, eid, attr, chapter, tiers)
    return cur is not None and str(cur) in ont.vocab.irreversible_states(attr)


def _irreversibly_terminal(ont, eid: str, chapter: int, tiers: tuple,
                           allow_reversal: bool) -> bool:
    """R1: 비가역 terminal(사망 등) 상태 — reversal 비허용 세계에서만. provisional 제외."""
    if allow_reversal or not _blockable(ont, eid):
        return False
    attrs = {"status"} | {t[1] for t in ont.timeline}
    for attr in attrs:
        term = ont.vocab.terminal_states(attr)
        irr = ont.vocab.irreversible_states(attr)
        cur = _lookup(ont, eid, attr, chapter, tiers)
        if cur is not None and str(cur) in term and str(cur) in irr:
            return True
    return False


def _cardinality_zero(ont, eid: str, rel_id: str) -> bool:
    """R2: CN-4 선언 상한 0(구조적 불가)만 blocked 근거. 상한>0 '가득참'은 기존 엣지 종료(eff_to)로
    해소 가능하므로 open(보수 — 오탐 방지). provisional 엔티티의 선언은 근거로 쓰지 않는다."""
    if not _blockable(ont, eid):
        return False
    e = ont.entities.get(eid)
    return (getattr(e, "cardinality", None) or {}).get(rel_id) == 0


def _veq(a, b) -> bool:
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb:
        return True
    try:
        return float(sa) == float(sb)
    except (TypeError, ValueError):
        return False


def _edge_match(ont, src: str, dst: str, rel_id: str, chapter: int, tiers: tuple):
    """활성 엣지 존재 여부 — edges_as_of(기존 평가기) + tier 필터 + 객관(pov=None)만.
    대칭/무방향 관계는 양방향 일치 허용(order_edge 정규화와 정합). pov(믿음) 엣지는 어떤 판정 근거도 아님."""
    spec = ont.rel_spec(rel_id)
    both_ways = spec.symmetric or not spec.directed
    for e in ont.edges_as_of(chapter):
        if e.rel_id != rel_id or e.pov is not None or e.trust_tier not in tiers:
            continue
        if (e.src_id, e.dst_id) == (src, dst) or (both_ways and (e.src_id, e.dst_id) == (dst, src)):
            return e
    return None


def evaluate_predicate(pred: dict, ont, ledger, clock_deltas: list, chapter: int,
                       tiers: tuple, allow_reversal: bool) -> dict:
    """술어 1건을 chapter 시점 스냅샷으로 평가 → {result: satisfied|open|blocked, observed[, blocked_rule]}.
    open=아직 아님(도달 가능) / blocked=보수 결정론 규칙(R1~R3)상 도달 불가. LLM 0콜."""
    ptype = pred["type"]

    if ptype == "attr_equals":
        cur = _lookup(ont, pred["eid"], pred["attr"], chapter, tiers)
        if cur is not None and _veq(cur, pred["value"]):
            return {"result": "satisfied", "observed": str(cur)}
        if not _veq(cur, pred["value"]) and _irreversibly_stuck(ont, pred["eid"], pred["attr"],
                                                                chapter, tiers, allow_reversal):
            return {"result": "blocked", "observed": str(cur), "blocked_rule": "terminal_conflict"}
        return {"result": "open", "observed": str(cur)}

    if ptype == "attr_in":
        cur = _lookup(ont, pred["eid"], pred["attr"], chapter, tiers)
        if cur is not None and any(_veq(cur, v) for v in pred["values"]):
            return {"result": "satisfied", "observed": str(cur)}
        if _irreversibly_stuck(ont, pred["eid"], pred["attr"], chapter, tiers, allow_reversal):
            return {"result": "blocked", "observed": str(cur), "blocked_rule": "terminal_conflict"}
        return {"result": "open", "observed": str(cur)}

    if ptype in ("edge_active", "edge_absent"):
        e = _edge_match(ont, pred["src"], pred["dst"], pred["rel_id"], chapter, tiers)
        obs = (f"active(eff_from={e.eff_from},tier={e.trust_tier})" if e else "absent")
        if ptype == "edge_active":
            if e is not None:
                return {"result": "satisfied", "observed": obs}
            for endpoint in (pred["src"], pred["dst"]):
                # R1: 제거 상태 끝점 → 새 객관 관계 성립 불가(엔진 게이트와 동일 논리)
                if _irreversibly_terminal(ont, endpoint, chapter, tiers, allow_reversal):
                    return {"result": "blocked", "observed": obs + f"|terminal:{endpoint}",
                            "blocked_rule": "terminal_conflict"}
                # R2: CN-4 선언 상한 0 — 그 관계가 구조적으로 성립 불가(외동={"sibling_of":0} 등)
                if _cardinality_zero(ont, endpoint, pred["rel_id"]):
                    return {"result": "blocked", "observed": obs + f"|cardinality0:{endpoint}",
                            "blocked_rule": "cardinality_zero"}
            return {"result": "open", "observed": obs}
        return {"result": "satisfied" if e is None else "open", "observed": obs}

    if ptype == "terminal":
        hit = _in_terminal(ont, pred["eid"], chapter, tiers)
        return {"result": "satisfied" if hit else "open",
                "observed": "terminal" if hit else "non-terminal"}

    if ptype == "alive":
        if not _in_terminal(ont, pred["eid"], chapter, tiers):
            return {"result": "satisfied", "observed": "non-terminal"}
        if _irreversibly_terminal(ont, pred["eid"], chapter, tiers, allow_reversal):
            return {"result": "blocked", "observed": "terminal(irreversible)",
                    "blocked_rule": "terminal_conflict"}
        return {"result": "open", "observed": "terminal(reversible)"}

    if ptype == "promise_paid":                                   # 원장은 비구속 회계 — tier 무관(두 모드 동일)
        p = ledger.by_id(pred["promise_id"])
        if p is None:                                             # R3: 컴파일 시 존재가 검증된 약속의 소멸 = 도달 불가
            return {"result": "blocked", "observed": "missing", "blocked_rule": "promise_vanished"}
        return {"result": "satisfied" if p.status == "paid" else "open", "observed": p.status}

    # clock_at_least — story_clock 결정론 누적(tier 무관)
    minutes, had_unknown = elapsed_minutes(clock_deltas)
    need = pred["amount"] * UNIT_MINUTES[pred["unit"]]
    obs = format_elapsed(minutes, had_unknown)
    return {"result": "satisfied" if minutes >= need else "open", "observed": obs}


# ---------------------------------------------------------------- 계약 단위 평가·정산(LLM 0콜)
def _describe(pred: dict, ont, ledger) -> str:
    """술어 → 작가 언어 한 줄(표시용 — 판정 아님)."""
    t = pred["type"]
    nm = ont.name
    try:
        if t == "attr_equals":
            return f"{nm(pred['eid'])}의 {ont.vocab.label(pred['attr'])} = {pred['value']}"
        if t == "attr_in":
            return f"{nm(pred['eid'])}의 {ont.vocab.label(pred['attr'])} ∈ {', '.join(pred['values'])}"
        if t == "edge_active":
            return f"{nm(pred['src'])} ↔ {nm(pred['dst'])} '{ont.rel_spec(pred['rel_id']).label}' 관계 성립"
        if t == "edge_absent":
            return f"{nm(pred['src'])} ↔ {nm(pred['dst'])} '{ont.rel_spec(pred['rel_id']).label}' 관계 해소"
        if t == "terminal":
            return f"{nm(pred['eid'])} 퇴장(제거 상태)"
        if t == "alive":
            return f"{nm(pred['eid'])} 생존(제거 상태 아님)"
        if t == "promise_paid":
            p = ledger.by_id(pred["promise_id"])
            return f"약속 회수: {p.text if p else pred['promise_id']}"   # 절단 전면 제거(2026-08-21): 작가 표시용 전문
        if t == "clock_at_least":
            return f"서사 시간 {pred['amount']:g}{pred['unit']} 이상 경과"
    except Exception:
        pass
    return t


def evaluate_contract(contract: EndingContract, ont, ledger, clock_deltas: list,
                      chapter: int, allow_reversal: bool) -> dict:
    """계약 전체를 gt/ni 양 tier 로 병렬 평가(LR-1 arm1 — 단일 판정 없음). 순수 결정론·LLM 0콜.
    promotion_hint: ni-satisfied & gt-open 항목 수 — '작가 승격(확정)' 유도 신호(자동 승격 없음)."""
    tiers = {"ground_truth_only": {"satisfied": 0, "open": 0, "blocked": 0},
             "with_narrative_inferred": {"satisfied": 0, "open": 0, "blocked": 0}}
    preds_out, hints = [], 0
    for cp in contract.predicates:
        pred = cp.model_dump()
        ev_gt = evaluate_predicate(pred, ont, ledger, clock_deltas, chapter, TIER_GT, allow_reversal)
        ev_ni = evaluate_predicate(pred, ont, ledger, clock_deltas, chapter, TIER_ALL, allow_reversal)
        tiers["ground_truth_only"][ev_gt["result"]] += 1
        tiers["with_narrative_inferred"][ev_ni["result"]] += 1
        hint = bool(ev_ni["result"] == "satisfied" and ev_gt["result"] == "open")
        hints += 1 if hint else 0
        preds_out.append({**pred, "label": _describe(pred, ont, ledger),
                          "eval": {"ground_truth_only": ev_gt, "with_narrative_inferred": ev_ni},
                          "promotion_hint": hint})
    return {
        "chapter": chapter,
        "predicates": preds_out,
        "tiers": tiers,
        "promotion_hints": hints,                                 # arm1: 승격 유도 신호(표시만)
        "settled": {mode: bool(contract.predicates) and d["open"] == 0 and d["blocked"] == 0
                    for mode, d in tiers.items()},                # tier 별 병렬 — 단일 판정 없음
        "note": "상태 기준 평가 — 지면(프로즈) 실현 보장 아님(arm2)",
    }


def settlement_snapshot(ev: dict, trigger: str) -> dict:
    """완결(아크 소진 등) 시점 계약 정산 스냅샷(결정론·표시용 데이터 — 무강제).
    미정산 카운트는 gt(확정) 기준으로 세되 양 tier 를 병렬 보존한다(arm1)."""
    g = ev["tiers"]["ground_truth_only"]
    return {
        "chapter": ev["chapter"], "trigger": trigger,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "unsettled_gt": g["open"] + g["blocked"],
        "open_gt": g["open"], "blocked_gt": g["blocked"], "satisfied_gt": g["satisfied"],
        "promotion_hints": ev["promotion_hints"],
        "tiers": ev["tiers"],
    }
