# -*- coding: utf-8 -*-
"""연재 회고 (G3 거버넌스) — 텔레메트리+완결 아크로 진단하고 '남은 아크/엔딩' 개정을 제안한다.

핵심 원칙(무강제): 시스템은 측정·진단·'제안'만, 적용은 작가 승인 후(거버넌스 — bible promote·worldgen provisional 과 동형).
- 이미 집필된 아크/회차는 건드리지 않는다(미래 아크·엔딩만 — 과거는 append-only).
- 제안은 narrative(서사 의도)지 ground_truth 아님 → 적용해도 결정론 게이트/캐논 무접촉.
- 강제 자동 적용 없음 — 작가가 고른 revision 만 revise_spine 으로 반영.
"""
from __future__ import annotations
import json

# 미래 아크/엔딩에 한해 개정 가능한 필드(과거·집필분 보호)
ARC_FIELDS = {"goal", "central_conflict", "turning_point", "title"}
ENDING_FIELDS = {"ending", "central_question", "thematic_payoff"}


def generate_retrospective(provider, *, genre: str, ending: str, done_arcs: list, upcoming_arcs: list,
                           pacing: dict, ledger_open: list, reader_trend: list) -> dict:
    """회고 제안 생성(LLM 1콜). 반환 {diagnosis, revisions:[{target,field,new_value,reason}]}. 실패 시 빈 제안."""
    if not upcoming_arcs and not ending:
        return {"diagnosis": "", "revisions": []}
    sys = ("너는 웹소설 연재 PD다. 지금까지의 전개 지표와 완결된 아크를 보고 '페이싱·일관성·재미' 관점에서 진단하고, "
           "'아직 집필되지 않은 남은 아크의 목표/갈등'과 필요하면 '엔딩'에 대한 개정안을 제안하라. "
           "이미 쓰인 회차는 바꿀 수 없다 — 앞으로의 방향만. 이건 강제가 아니라 작가가 취사선택할 '제안'이다. "
           "지표가 양호하면 revisions 를 비워라(억지 변경 금지). JSON만.")
    usr = (f"[장르]{genre}\n[현재 엔딩]{ending}\n[완결 아크]{json.dumps(done_arcs, ensure_ascii=False)}\n"
           f"[남은 아크(미집필)]{json.dumps(upcoming_arcs, ensure_ascii=False)}\n"
           f"[페이싱 지표]{json.dumps(pacing, ensure_ascii=False)}\n"
           f"[미지불 약속]{json.dumps(ledger_open, ensure_ascii=False)}\n"
           f"[독자 반응 추세]{json.dumps(reader_trend, ensure_ascii=False)}\n"
           '{"diagnosis":"3~5문장 진단(무엇이 잘 가고 무엇이 위험한지)",'
           '"revisions":[{"target":"arc:arcN 또는 ending","field":"goal|central_conflict|turning_point|title|'
           'ending|central_question|thematic_payoff","new_value":"개정 내용","reason":"왜"}]}')
    try:
        r = provider.chat_json([{"role": "system", "content": sys},
                                {"role": "user", "content": usr}], temperature=0.4, max_tokens=2000)
    except Exception:
        return {"diagnosis": "", "revisions": []}
    valid_arc_ids = {a.get("arc_id") for a in upcoming_arcs}
    out = []
    for rv in (r.get("revisions") or [])[:8]:
        target = (rv.get("target") or "").strip()
        field = (rv.get("field") or "").strip()
        nv = (rv.get("new_value") or "").strip()
        if not nv:
            continue
        if target == "ending" and field in ENDING_FIELDS:
            out.append({"target": "ending", "field": field, "new_value": nv, "reason": rv.get("reason", "")})
        elif target.startswith("arc:") and field in ARC_FIELDS and target[4:] in valid_arc_ids:
            out.append({"target": target, "field": field, "new_value": nv, "reason": rv.get("reason", "")})
    return {"diagnosis": (r.get("diagnosis") or "").strip(), "revisions": out}
