# -*- coding: utf-8 -*-
"""SA-1 ⓑ-1 — 셈·재고 계측 축(paper_stock 계열)의 생성 노출 차단 잠금(LLM 0콜).

설계 SSOT docs/design-sa1-counting-anchor.md §2 ⓑ-1 · §5. 차터 근거 design-rb1-refounding.md §1
("소지금 등 돈·재고 계측 축은 온톨로지 internal — 생성 프롬프트에 상시 노출해 셈 서사 유도하는 소스 차단").

핵심 잠금(짐작 아닌 소비 지점 실증): exposure=internal 로 선언된 계측 축은
21화(재고 timeline eff_from 21)처럼 **binding(ground_truth) 값이 살아 있어도**
생성 입력 두 채널(push=canon_facts · pull=CanonLookup)에 실리지 않는다 —
`is_public_attr` 게이트가 `public_attrs` 루프 최상단에서 값 해소(binding_state_as_of)보다
먼저 걸러 '타임라인 부활 우회'가 없음을 잠근다. money_won 이 이미 internal 로 걸러지는 것과 대칭이며,
같은 시점 public 축은 그대로 실리는 대조로 '노출 등급이 하는 일'임을 못박는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.domain.world import WorldConfig, EntitySpec, AttributeSpec, TimelineEntry
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.engine.factory import build_ontology
from novelcopilot.engine.lookup import CanonLookup


def _world():
    """싸구려 퇴마사 계측 축을 최소 재현: 재고·경력 계열은 internal, 대조용 public 축 1종."""
    return WorldConfig(
        title="[실험] SA-1 재고 노출 차단", genre="현판", tone="건조", premise="t.",
        attributes=[
            # SA-1 표적 — 셈·재고 계측 축(internal)
            AttributeSpec(key="paper_stock", label="규격 외 지류 재고(장)", kind="numeric", exposure="internal"),
            AttributeSpec(key="marble_stains", label="구슬얼룩(개)", kind="numeric", exposure="internal"),
            AttributeSpec(key="career_exorcist_years", label="퇴마 경력(년)", kind="numeric", exposure="internal"),
            AttributeSpec(key="money_won", label="소지금(원)", kind="numeric", exposure="internal"),
            # 대조 — public 축(노출 등급이 하는 일임을 못박는 control)
            AttributeSpec(key="affiliation", label="소속", kind="categorical",
                          vocab=["원세계_프리랜서"], mutable=True),
        ],
        entities=[
            # 실데이터(45295b91ffce)와 동형: attrs 키는 있으나 정적 값은 null → 값은 timeline 이 공급
            EntitySpec(id="junho", name="서준호", etype="character",
                       attrs={"paper_stock": None, "marble_stains": None,
                              "career_exorcist_years": None, "money_won": None,
                              "affiliation": None}),
        ],
        timeline=[
            # SA-1 부활 우회 시나리오: 재고·계측이 21화 시점 ground_truth 로 살아 있다.
            TimelineEntry(entity_id="junho", attr="paper_stock", value=2, eff_from=21,
                          trust_tier="ground_truth"),
            TimelineEntry(entity_id="junho", attr="marble_stains", value=1, eff_from=16,
                          trust_tier="ground_truth"),
            TimelineEntry(entity_id="junho", attr="career_exorcist_years", value=7, eff_from=1,
                          trust_tier="ground_truth"),
            TimelineEntry(entity_id="junho", attr="money_won", value=88000, eff_from=1,
                          trust_tier="ground_truth"),
            # 대조 public 축도 같은 시점 binding 으로 살아 있다.
            TimelineEntry(entity_id="junho", attr="affiliation", value="원세계_프리랜서", eff_from=1,
                          trust_tier="ground_truth"),
        ])


def _ont():
    w = _world()
    return build_ontology(w, Vocabulary.from_world(w))


CH = 21
_HIDDEN_LABELS = ("규격 외 지류 재고", "구슬얼룩", "퇴마 경력", "소지금")


def test_binding_value_alive_but_internal():
    """전제 잠금: 재고 값은 21화 시점 binding 으로 실재(부활 경로 살아 있음) — 그런데도 노출은 막힌다."""
    o = _ont()
    # 값은 살아 있다(부활 경로가 실재함을 먼저 증명 — 우회가 '값 없음' 때문이 아님)
    assert o.binding_state_as_of("junho", "paper_stock", CH) == 2
    assert o.binding_state_as_of("junho", "marble_stains", CH) == 1
    # 그러나 노출 등급은 internal
    for k in ("paper_stock", "marble_stains", "career_exorcist_years", "money_won"):
        assert o.is_public_attr(k) is False
    assert o.is_public_attr("affiliation") is True


def test_push_channel_canon_facts_excludes_counting():
    """push(canon_facts) — 재고·계측 축 0건, public 대조축은 그대로."""
    o = _ont()
    facts = o.canon_facts(["junho"], CH)
    txt = " / ".join(f"{f.entity}:{f.attr_label}={f.value}" for f in facts)
    for lbl in _HIDDEN_LABELS:
        assert lbl not in txt, f"internal 계측 축 '{lbl}' 가 canon_facts 에 노출됨: {txt}"
    assert "소속=원세계_프리랜서" in txt          # public 축은 실린다(대조 — 값 해소 자체는 정상)


def test_pull_channel_lookup_excludes_counting():
    """pull(CanonLookup) — 조회 응답에도 재고·계측 축 0건, public 대조축은 그대로.
    실데이터 ch20 gen_context 는 이 채널로 '규격 외 지류 재고(장)=3' 을 실었다 → 차단 확인."""
    o = _ont()
    out = CanonLookup(o, [], CH).handle("lookup_canon", {"query": "서준호"})
    for lbl in _HIDDEN_LABELS:
        assert lbl not in out, f"internal 계측 축 '{lbl}' 가 조회 응답에 노출됨: {out}"
    assert "소속=원세계_프리랜서" in out


def test_public_attrs_only_public_survive():
    """소비 단일 질의점 public_attrs 는 public 축만 통과(internal 은 값이 있어도 제외)."""
    o = _ont()
    keys = {a for a, _v, _b in o.public_attrs("junho", CH)}
    assert keys == {"affiliation"}, f"internal 축 누출: {keys}"


def test_money_won_symmetry():
    """대칭성: money_won(기존 internal)과 신규 internal 3축이 동일하게 걸러진다 —
    노출 등급이 축별 하드코딩 없이 균일하게 작동함을 잠금."""
    o = _ont()
    txt = " / ".join(f"{f.attr_label}" for f in o.canon_facts(["junho"], CH))
    assert "소지금" not in txt              # 기존 internal
    assert "규격 외 지류 재고" not in txt   # 신규 internal — money_won 과 대칭
    assert "구슬얼룩" not in txt
    assert "퇴마 경력" not in txt
