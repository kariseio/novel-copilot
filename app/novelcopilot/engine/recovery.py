# -*- coding: utf-8 -*-
"""ESCALATED 회복 안내 — hard Violation 을 작가용 자연어 진단+회복 레버로 렌더(LLM 0콜, 결정론).

ESCALATED 는 회차 미생성(진행 차단)인데, 기존엔 kind 코드(edge_post_death 등)만 방출돼 작가가 무엇을·어떻게
고칠지 알 수 없었다. 필요한 정보는 이미 Violation(entity/canon/text/evidence)에 다 있으므로 '검출'이 아니라
'출력 레이어'만 보강한다(두더지잡기 금지 원칙 정합 — 검출기/사전 추가 아님). kind 는 유한 닫힌 집합이고,
미매핑도 항상 일반 안내를 반환해 빈손이 되지 않는다(조용한 정지 금지).
"""
from __future__ import annotations

from ..domain.types import Violation

# 회복 레버 토큰(프론트가 UI 동작에 매핑하는 API 계약 상수 — 코드 의존 아님)
LEVERS = {"set_entity_state", "end_relation", "add_relation", "add_entity", "allow_state_reversal", "rewrite"}


def recovery_hint(v: Violation) -> dict:
    """hard Violation 1건 → {진단, 회복 방법(조건부), 레버}. 단정 대신 '의도면 A / 오류면 B' 양 시나리오 제시."""
    k = v.kind
    if k in ("edge_post_death", "post_death_change", "state_timeline"):
        return {"kind": k, "entity": v.entity,
                "diagnosis": f"{v.entity}은(는) {v.canon} 상태인데, 본문에서 이후 행동·대사·관계가 나타났습니다.",
                "fix": [f"의도된 부활/생환이라면: ‘공식 설정’에서 {v.entity}의 상태를 되돌리세요(회귀물이면 세계 설정의 상태 되돌림 허용).",
                        "본문 오류라면: 그 장면을 회상·환영으로 바꾸도록 ‘작가 지시’에 적고 회차를 다시 생성하세요."],
                "levers": ["set_entity_state", "allow_state_reversal", "rewrite"]}
    if k == "edge_dangling":
        return {"kind": k, "entity": v.entity,
                "diagnosis": f"관계 '{v.canon}'가 명부에 없는 인물({v.entity})을 가리킵니다.",
                "fix": [f"{v.entity}을(를) 인물로 먼저 추가하거나, 잘못된 관계를 종료하세요."],
                "levers": ["add_entity", "end_relation"]}
    if k == "edge_self_loop":
        return {"kind": k, "entity": v.entity,
                "diagnosis": f"{v.entity}이(가) 자기 자신과 '{v.canon}' 관계로 설정됐습니다.",
                "fix": ["이 자기참조 관계를 관계도에서 종료하세요."], "levers": ["end_relation"]}
    if k == "relation_contradiction":
        return {"kind": k, "entity": v.entity,
                "diagnosis": f"{v.entity}: 공식 설정은 ‘{v.canon}’인데 본문이 ‘{v.text}’로 단정했습니다(상충).",
                "fix": ["관계가 실제로 바뀌었다면 기존 관계를 종료하고 이번 화부터 새 관계를 맺으세요.",
                        "본문 오류라면 ‘작가 지시’로 재생성을 요청하세요."],
                "levers": ["end_relation", "add_relation", "rewrite"]}
    if k == "ssot_ambiguous":
        return {"kind": k, "entity": v.entity,
                "diagnosis": f"{v.entity}의 ‘{v.canon}’ 값이 동시에 둘({v.text})로 설정돼 충돌합니다(설정 자기모순).",
                "fix": [f"‘공식 설정’에서 {v.entity}의 해당 속성을 하나의 값으로 확정하세요."],
                "levers": ["set_entity_state"]}
    if k.startswith("field_value"):
        return {"kind": k, "entity": v.entity,
                "diagnosis": f"{v.entity}: 공식 설정 ‘{v.canon}’과 본문 값 ‘{v.text}’이(가) 다릅니다.",
                "fix": ["설정이 바뀐 것이면 ‘공식 설정’에서 값을 갱신하세요.",
                        "본문 오류라면 ‘작가 지시’로 재생성을 요청하세요."],
                "levers": ["set_entity_state", "rewrite"]}
    # 미매핑(worldrule(...)·uncertain 등) — 항상 일반 안내 반환(빈손 금지)
    return {"kind": k, "entity": v.entity,
            "diagnosis": f"{v.entity}: 공식 설정 ‘{v.canon}’ 위반 — {v.text} ({v.evidence}).",
            "fix": ["설정집·관계도에서 해당 항목을 확인해 정정하거나, ‘작가 지시’로 재생성을 요청하세요."],
            "levers": ["rewrite"]}


def recovery_report(hard_violations) -> list[dict]:
    """hard 위반들 → 진단 카드 목록(중복 kind+entity 는 1개로)."""
    seen, out = set(), []
    for v in hard_violations:
        key = (v.kind, v.entity)
        if key in seen:
            continue
        seen.add(key)
        out.append(recovery_hint(v))
    return out
