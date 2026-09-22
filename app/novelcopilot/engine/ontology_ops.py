# -*- coding: utf-8 -*-
"""ON-2 U1 — 온톨로지 갱신 루프 무결성 연산(순수·결정론·LLM 0).

설계(보드 ON-2, 2026-08-07 PM 산출): 갱신 실패 모드 3종(유령 개체 재발·스테일 값 주입·구조 변경 후
잔재)의 차단 지점. 전부 **사람이 부르는 연산**이다 — 자동 병합·자동 수정 트리거는 만들지 않는다
(무강제·근접 문자열 자동 병합은 교착어 함정으로 기각).

- merge_entity: 유령 중복을 정본으로 병합(별칭 승계·타임라인 재지향·엣지 src/dst·edge_id 재작성).
- remap_chapters: 병합·재번호 시 eff_from 재매핑(수동 수리가 불변식을 깨온 실측의 도구화).
- validate_timeline: 불변식 점검 — advisory 목록만 반환(고아·eff 규약·동시점 중복). 수정 0.
- machine_binding_report: XR-5 티어 선언 현황 — 속성별 기계 binding 잔량·표본·선언 상태. 수정 0.
"""
from __future__ import annotations

import re

_REASON_CH = re.compile(r"(\d+)화 동적 감지")


def _all_entity_ids(state) -> set[str]:
    ids = {getattr(e, "id", None) for e in (getattr(state, "runtime_entities", None) or [])}
    ids |= {getattr(e, "id", None) for e in (getattr(getattr(state, "world", None), "entities", None) or [])}
    ids.discard(None)
    return ids


def _find_entity(state, eid: str):
    for e in (getattr(state, "world").entities or []):
        if e.id == eid:
            return e, "world"
    for e in (getattr(state, "runtime_entities", None) or []):
        if getattr(e, "id", None) == eid:
            return e, "runtime"
    return None, ""


def merge_entity(state, dup_id: str, canon_id: str) -> dict:
    """dup_id 개체를 canon_id 로 병합. 반환: 처리 요약(advisory). 병합 결정은 사람 몫 — 이 함수는 실행만."""
    if dup_id == canon_id:
        raise ValueError("dup 과 canon 이 같습니다")
    canon, _ = _find_entity(state, canon_id)
    dup, dup_store = _find_entity(state, dup_id)
    if canon is None:
        raise ValueError(f"정본 개체 없음: {canon_id}")
    if dup is None:
        raise ValueError(f"중복 개체 없음: {dup_id}")
    # ① 별칭 승계(이름 포함 — 향후 추출 결속)
    moved_aliases = []
    for a in [getattr(dup, "name", "")] + list(getattr(dup, "aliases", None) or []):
        a = (a or "").strip()
        if a and a != canon.name and a not in (canon.aliases or []):
            canon.aliases.append(a)
            moved_aliases.append(a)
    # ② 타임라인 재지향(+동일 키 중복 제거)
    seen, kept, repointed = set(), [], 0
    for ev in (getattr(state, "runtime_timeline", None) or []):
        if getattr(ev, "entity_id", None) == dup_id:
            ev.entity_id = canon_id
            repointed += 1
        key = (ev.entity_id, ev.attr, str(ev.value), ev.eff_from)
        if key in seen:
            continue
        seen.add(key)
        kept.append(ev)
    state.runtime_timeline = kept
    # ③ 엣지 재지향 + edge_id 문자열 잔재 재작성(병합 후 감사 혼동 소지 — ON-1 실측)
    edges_touched = 0
    for ed in (getattr(state, "runtime_edges", None) or []):
        touched = False
        for f in ("src", "dst", "src_id", "dst_id"):
            if getattr(ed, f, None) == dup_id:
                setattr(ed, f, canon_id)
                touched = True
        eid = getattr(ed, "edge_id", None)
        if isinstance(eid, str) and dup_id in eid:
            ed.edge_id = eid.replace(dup_id, canon_id)
            touched = True
        edges_touched += 1 if touched else 0
    # ④ 중복 개체 제거(runtime 만 — world 정본은 병합 대상이 아님)
    if dup_store == "runtime":
        state.runtime_entities = [e for e in state.runtime_entities if getattr(e, "id", None) != dup_id]
    else:
        raise ValueError("world 정본 개체는 병합 대상이 될 수 없습니다(정본↔정본 병합은 수동 결정)")
    return {"merged": dup_id, "into": canon_id, "aliases_moved": moved_aliases,
            "timeline_repointed": repointed, "edges_touched": edges_touched}


def remap_chapters(state, mapping: dict[int, int]) -> dict:
    """병합·재번호 시 타임라인 eff_from 재매핑. mapping: {구 화번호: 새 화번호} — eff_from 은 화번호 좌표라
    구조 변경과 함께 움직여야 한다(커서 사고 eff_from=14, 병합 잔재 실측). time_delta 는 회차 레코드에
    붙어 함께 이동하므로 여기서 손대지 않는다 — 값 오류는 validate/사람 몫."""
    mapping = {int(k): int(v) for k, v in (mapping or {}).items()}
    changed = 0
    for ev in (getattr(state, "runtime_timeline", None) or []):
        if ev.eff_from in mapping:
            ev.eff_from = mapping[ev.eff_from]
            changed += 1
    return {"remapped": changed}


def validate_timeline(state) -> list[dict]:
    """불변식 점검 — advisory 목록(수정 0·판정 라벨 0). 항목: kind/entity/attr/detail."""
    out: list[dict] = []
    ids = _all_entity_ids(state)
    seen: dict[tuple, int] = {}
    max_ch = max((getattr(c, "chapter", 0) for c in (getattr(state, "chapters", None) or [])), default=0)
    for ev in (getattr(state, "runtime_timeline", None) or []):
        if ev.entity_id not in ids:
            out.append({"kind": "고아_개체", "entity": ev.entity_id, "attr": ev.attr,
                        "detail": f"타임라인이 존재하지 않는 개체를 가리킴(eff {ev.eff_from})"})
        if int(ev.eff_from or 0) <= 0:
            out.append({"kind": "eff_범위", "entity": ev.entity_id, "attr": ev.attr,
                        "detail": f"eff_from={ev.eff_from} (양수여야 함)"})
        if max_ch and int(ev.eff_from or 0) > max_ch + 1:
            out.append({"kind": "eff_범위", "entity": ev.entity_id, "attr": ev.attr,
                        "detail": f"eff_from={ev.eff_from} > 최종화+1({max_ch + 1}) — 재번호 잔재 의심"})
        m = _REASON_CH.search(getattr(ev, "reason", "") or "")
        if m and int(m.group(1)) + 1 != int(ev.eff_from or 0):
            out.append({"kind": "규약_불일치", "entity": ev.entity_id, "attr": ev.attr,
                        "detail": f"라벨 '{m.group(1)}화 감지' vs eff_from={ev.eff_from} (규약: 감지화+1)"})
        key = (ev.entity_id, ev.attr, ev.eff_from)
        if key in seen:
            out.append({"kind": "동시점_중복", "entity": ev.entity_id, "attr": ev.attr,
                        "detail": f"같은 (개체,속성,eff={ev.eff_from}) 항목 중복"})
        seen[key] = seen.get(key, 0) + 1
    return out


_SAMPLE_CAP = 3      # 속성당 표본 상한 — 리포트는 '무엇이 박혀 있나'를 보여주는 창이지 목록 덤프가 아니다


def _entity_names(state) -> dict:
    """id→표시 이름(없으면 id 그대로 — 결측 정직). world 정본 우선, 런타임이 덮지 않는다."""
    names: dict = {}
    for e in (getattr(state, "runtime_entities", None) or []):
        eid = getattr(e, "id", None)
        if eid:
            names[eid] = getattr(e, "name", None) or eid
    for e in (getattr(getattr(state, "world", None), "entities", None) or []):
        eid = getattr(e, "id", None)
        if eid:
            names[eid] = getattr(e, "name", None) or eid
    return names


def machine_binding_report(state) -> dict:
    """XR-5 티어 선언 현황 리포트 — advisory·결정론·LLM 0·수정 0(validate_timeline 과 같은 패턴).

    소급 강등을 하지 않는다는 결정(설계 §2.4)의 정직화 표면이다: **미선언 축 + 기계 binding 잔량**이 곧
    '신정책 이전에 박힌 캐논이 그대로 남아 있다'는 뜻이고, 작가는 그걸 보고 속성별로 티어를 선언하거나
    (이후 커밋부터 적용) 개별 값을 set_entity_state 로 확정한다. 자동 처리 0(무강제 — 측정→가시화→작가).

    반환 {attributes: [...], totals: {...}}. attributes 원소:
      attr/label/kind/declared(추적 속성 목록에 선언된 축인가)/auto_commit(""|binding|non_binding)
      machine_binding(기계 커밋 ground_truth 수)/author_binding/non_binding(비구속 수)/samples

    셈의 기준은 timeline 엔트리의 provenance(ON-2 U7)다 — 구 데이터(필드 부재)는 기계로 본다(기본값 계약).
    수치는 원자료다(K3): 품질 헤드라인으로 쓰지 않는다."""
    names = _entity_names(state)
    specs = {getattr(a, "key", None): a for a in (getattr(getattr(state, "world", None), "attributes", None) or [])}
    rows: dict = {}

    def _row(attr: str) -> dict:
        r = rows.get(attr)
        if r is None:
            spec = specs.get(attr)
            r = {"attr": attr,
                 "label": (getattr(spec, "label", "") or attr) if spec is not None else attr,
                 "kind": getattr(spec, "kind", "") if spec is not None else "",
                 "declared": spec is not None,
                 "auto_commit": getattr(spec, "auto_commit", "") if spec is not None else "",
                 # XR-11(007 §5): 티어와 노출은 독립 축(auto_commit=캐논 승격 여부 / exposure=어느 소비자가
                 #   보는가) — 한 행에서 함께 검토되도록 리포트에 동봉(additive·읽기 전용).
                 "exposure": getattr(spec, "exposure", "public") if spec is not None else "",
                 "machine_binding": 0, "author_binding": 0, "non_binding": 0, "samples": [],
                 # XR-19(009 §9): 기계 binding '전량' 목록(표본 상한과 별개 — 검토 큐 원자료) + 작가 판정 조인.
                 "entries": []}
            rows[attr] = r
        return r

    for attr in specs:                     # 선언된 축은 잔량 0 이어도 목록에 남는다(작가가 여기서 선언한다)
        if attr:
            _row(attr)
    for ev in (getattr(state, "runtime_timeline", None) or []):
        attr = getattr(ev, "attr", None)
        if not attr:
            continue
        r = _row(attr)
        tier = getattr(ev, "trust_tier", "ground_truth") or "ground_truth"
        prov = list(getattr(ev, "provenance", None) or ["machine"])
        if tier != "ground_truth":
            r["non_binding"] += 1
            continue
        if "author" in prov:
            r["author_binding"] += 1
            continue
        r["machine_binding"] += 1
        eid = getattr(ev, "entity_id", "")
        if len(r["samples"]) < _SAMPLE_CAP:
            r["samples"].append({"entity_id": eid, "entity": names.get(eid, eid),
                                 "value": str(getattr(ev, "value", "")),
                                 "eff_from": getattr(ev, "eff_from", 0)})
        # XR-19: 전량 목록(출처=reason·증거 회차=eff_from) + 작가 판정 조인(아래 review 루프)
        r["entries"].append({"entity_id": eid, "entity": names.get(eid, eid),
                             "value": str(getattr(ev, "value", "")),
                             "eff_from": getattr(ev, "eff_from", 0),
                             "reason": getattr(ev, "reason", "") or "", "review": ""})
    # XR-19: 작가 판정 기록 조인 — 키 (entity_id|attr|eff_from), 나중 기록이 이긴다(append 원장·latest-wins 읽기)
    review = {}
    for rec in (getattr(state, "tier_review", None) or []):
        review[(rec.get("entity_id"), rec.get("attr"), rec.get("eff_from"))] = rec.get("decision", "")
    for r in rows.values():
        for e in r["entries"]:
            e["review"] = review.get((e["entity_id"], r["attr"], e["eff_from"]), "")
        # 내면 우선 표식(009 — non_binding 선언 축의 기계 binding 잔량이 1차 검토 대상)
        r["review_priority"] = bool(r["auto_commit"] == "non_binding" and r["machine_binding"])
        r["review_pending"] = sum(1 for e in r["entries"] if not e["review"])
    # 결정론 정렬: 기계 binding 많은 축 먼저(검토 큐), 동수는 속성키 사전순
    out = sorted(rows.values(), key=lambda r: (-r["machine_binding"], r["attr"]))
    return {"attributes": out,
            "totals": {"attributes": len(out),
                       "machine_binding": sum(r["machine_binding"] for r in out),
                       "author_binding": sum(r["author_binding"] for r in out),
                       "non_binding": sum(r["non_binding"] for r in out),
                       "undeclared_tier": sum(1 for r in out if not r["auto_commit"]),
                       "undeclared_tier_with_machine_binding":
                           sum(1 for r in out if not r["auto_commit"] and r["machine_binding"]),
                       # XR-19: 내면(비구속 선언) 축의 기계 binding 잔량·미판정 수(검토 큐 진행도 원자료 — K3: 헤드라인 금지)
                       "review_priority_machine_binding":
                           sum(r["machine_binding"] for r in out if r["review_priority"]),
                       "review_pending": sum(r["review_pending"] for r in out)}}
