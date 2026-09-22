# -*- coding: utf-8 -*-
"""B-33 시간 앵커(시계 파생 산술의 결정론 확장) 테스트 — 계약 만기 잔여·현재 나이. LLM 0콜.

규명(서리꽃 실데이터): CN-1 스토리 시계는 경과(≈6일)를 옳게 누적했으나, '삼 년 계약 만기'는
구조화된 시간량이 아니라 premise/에피소드/본문의 자유 서술로만 존재 → 파생 산술(만기 잔여·나이)을
계산하는 레이어가 0. 그래서 22화 본문이 6~7일차에 '삼 년 만기가 도래'를 발명해도 반증할 고신뢰 사실이 없었다.
차단: 세계가 시간 앵커를 선언하면 story_clock.anchor_facts 가 현재 클록 기준 잔여/나이를 결정론 계산해
[확정 설정] 고신뢰 팩트로 주입(모델은 읽기만). 미선언 세계 = 무주입(바이트 동일 하위호환)."""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

from novelcopilot.domain.types import (TimeDelta, ContextBoard, OntologyFact, SceneSpec)
from novelcopilot.domain.world import WorldConfig, TimeAnchor, StyleSpec, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.engine.story_clock import (anchor_facts, format_elapsed, _span_body,
                                             elapsed_minutes)
from novelcopilot.engine.prompts import PromptAssembler
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


# 서리꽃 24화 실측 델타 근사(엿새대에서 진행 — 실데이터: ch18 '성에 온 지 엿새' == 클록 6.1일)
_FROST_DELTAS = [
    TimeDelta(amount=1, unit="day"), TimeDelta(amount=0, unit="minute"),
    TimeDelta(amount=1, unit="day"), TimeDelta(amount=1, unit="day"),
    TimeDelta(amount=0, unit="minute"), TimeDelta(amount=14, unit="hour"),
    TimeDelta(amount=1, unit="day"), TimeDelta(amount=8, unit="hour"),
    TimeDelta(amount=12, unit="hour"),
]


def test_backward_compat_no_anchors():
    """앵커 미선언 → [](주입 생략). WorldConfig 기본값·구 JSON(time_anchors 없음) 로드 무변화."""
    assert anchor_facts(None, _FROST_DELTAS) == []
    assert anchor_facts([], _FROST_DELTAS) == []
    w = WorldConfig(title="t")                       # 기본값
    assert w.time_anchors == []
    # 구 JSON(time_anchors 키 부재) → 하위호환 로드
    w2 = WorldConfig.model_validate({"title": "old", "genre": "x"})
    assert w2.time_anchors == []


def test_deadline_residual_not_matured():
    """서리꽃 재현: 삼 년 계약, 엿새대 경과 → '약 3.0년 남음'(NOT '도래'). 6일차 '만기 도래' 발명의 결정론 반증."""
    anchors = [TimeAnchor(anchor_id="contract", label="계약 만기", kind="deadline", amount=3, unit="year")]
    facts = anchor_facts(anchors, _FROST_DELTAS)
    assert len(facts) == 1 and isinstance(facts[0], OntologyFact)
    v = facts[0].value
    assert "3.0년 남음" in v          # 잔여 ≈ 3년(6일밖에 안 지남)
    assert "도래" not in v            # 만기 도래 아님 — 본문의 '삼 년 만기 도래'를 반증
    # 경과가 함께 명시돼 모델이 현재 위치를 앎(엿새대)
    mins, _ = elapsed_minutes(_FROST_DELTAS)
    assert format_elapsed(mins) in v


def test_deadline_matured_when_elapsed_exceeds():
    """경과 ≥ 예정 → '기한 도래·경과'(참일 때만 도래 서술 허용)."""
    anchors = [TimeAnchor(label="짧은 기한", kind="deadline", amount=1, unit="day")]
    facts = anchor_facts(anchors, [TimeDelta(amount=3, unit="day")])   # 3일 경과 > 1일 기한
    assert "도래" in facts[0].value and "남음" not in facts[0].value


def test_age_frozen_within_year():
    """나이 15세, 엿새 경과 → '15세'·'나이 변동 없음'(15→16 드리프트 차단, 경과<1년=내림 0)."""
    anchors = [TimeAnchor(label="세라의 나이", kind="age", amount=15)]
    facts = anchor_facts(anchors, _FROST_DELTAS)
    assert "15세" in facts[0].value and "16세" not in facts[0].value
    assert "나이 변동 없음" in facts[0].value


def test_age_increments_after_full_year():
    """나이는 결정론 내림 산술: 2년 경과 → 15→17세(변동 없음 문구 제거)."""
    anchors = [TimeAnchor(label="세라의 나이", kind="age", amount=15)]
    facts = anchor_facts(anchors, [TimeDelta(amount=2, unit="year")])
    assert "17세" in facts[0].value and "나이 변동 없음" not in facts[0].value


def test_unknown_unit_null_degrade():
    """미상 단위 deadline 앵커 → 그 행만 건너뜀(거짓 잔여 단정 회피). 다른 앵커는 정상 산출."""
    anchors = [TimeAnchor(label="이상기한", kind="deadline", amount=3, unit="fortnight"),   # 미상 단위 → skip
               TimeAnchor(label="세라의 나이", kind="age", amount=15)]
    facts = anchor_facts(anchors, _FROST_DELTAS)
    assert len(facts) == 1 and facts[0].entity == "세라의 나이"


def test_had_unknown_hedge():
    """일부 델타가 미상이면 '약' 헤지로 하향 정직(story_time 와 동일 철학)."""
    deltas = [TimeDelta(amount=1, unit="day"), TimeDelta.parse({"amount": 2, "unit": "fortnight"})]  # 두 번째=미상
    _, had_unknown = elapsed_minutes(deltas)
    assert had_unknown is True
    facts = anchor_facts([TimeAnchor(label="계약 만기", kind="deadline", amount=3, unit="year")], deltas)
    assert facts[0].value.startswith("약 ")


def test_span_body_regression():
    """_span_body 리팩터가 format_elapsed 경계값을 보존(단일 출처)."""
    assert _span_body(45) == "45분"
    assert _span_body(3 * 60) == "3.0시간"
    assert _span_body(6.1 * 1440) == "6.1일"
    assert _span_body(3 * 525600) == "3.0년"
    # format_elapsed 계약 불변(구 테스트와 동일)
    assert format_elapsed(0) == "이야기 시작 시점(아직 유의미한 시간 경과 없음)"
    assert "약 " in format_elapsed(1440, had_unknown=True)


def test_high_trust_injection_into_settled_block():
    """anchor_facts → ground_truth 로 [확정 설정] 최상단에 렌더(harness 가 prepend 하는 채널과 동일)."""
    facts = anchor_facts([TimeAnchor(label="계약 만기", kind="deadline", amount=3, unit="year")], _FROST_DELTAS)
    board = ContextBoard(chapter=22, ground_truth=facts, story_time="이야기 시작 후 6.1일 경과")
    out = PromptAssembler(StyleSpec(rules=["x"]), 4000).assemble(
        board, SceneSpec(index=0, goal="g", key_events=["e"]), "")
    settled = out.split("[작가 지시")[0]                 # 최상단 브리프 + 확정 설정 블록
    assert "계약 만기" in settled and "3.0년 남음" in settled
    assert "절대 위반 금지" in settled                    # 고신뢰 블록 헤더(위반 금지 바인딩)


class _CaptureProvider(LLMProvider):
    """usr 프롬프트를 캡처하고 고정 메뉴를 반환(메뉴 생성기 주입 검증용 — LLM 0콜)."""
    def __init__(self): super().__init__(); self.usr = ""
    def chat(self, messages, **kw):
        self.usr = messages[-1]["content"]
        return json.dumps({"event_menu": ["사건A", "사건B"]}, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _menu_world_arc_ep():
    w = WorldConfig(title="계약결혼", genre="로맨스", entities=[EntitySpec(id="riel", name="리엘")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="a1", order=1, title="A1", goal="계약을 지킨다")])
    ep = Episode(episode_id="e1", arc_id="a1", order=1, title="만기 직전",
                 premise="도입", climax="놓아줄 각오", required_events=["필수사건"])
    return w, w.spine.arcs[0], ep


def test_event_menu_receives_time_facts():
    """MED 소스차단: 메뉴 생성 콜(서리꽃서 '삼 년 기한=임박'을 *최초로* 발명한 지점)이 결정론 시간 사실을 usr 로 받는다."""
    w, arc, ep = _menu_world_arc_ep()
    facts = [f"{f.entity}: {f.value}"
             for f in anchor_facts([TimeAnchor(label="계약 만기", kind="deadline", amount=3, unit="year")],
                                   _FROST_DELTAS)]
    cap = _CaptureProvider()
    ArcPlanner(cap).generate_event_menu(w, arc, ep, ["직전 줄거리"], time_facts=facts)
    assert "[시간 기준(결정론" in cap.usr                       # 시간 블록이 메뉴 프롬프트에 주입됨
    assert "계약 만기" in cap.usr and "3.0년 남음" in cap.usr    # 결정론 잔여값이 메뉴 콜에 도달(6일차 '만기 도래' 반증)


def test_event_menu_backward_compat_no_time_facts():
    """앵커 미선언(time_facts 미전달/빈 리스트) → 시간 블록 미주입(무주입 세계 프롬프트 바이트 동일)."""
    w, arc, ep = _menu_world_arc_ep()
    cap = _CaptureProvider()
    ArcPlanner(cap).generate_event_menu(w, arc, ep, ["직전 줄거리"])            # 미전달
    base_usr = cap.usr
    assert "[시간 기준(결정론" not in base_usr
    cap2 = _CaptureProvider()
    ArcPlanner(cap2).generate_event_menu(w, arc, ep, ["직전 줄거리"], time_facts=[])   # 빈 리스트
    assert cap2.usr == base_usr                                   # 바이트 동일(빈 time_facts = 무주입)


def test_world_roundtrip_preserves_anchors():
    """time_anchors 디스크 영속 라운드트립(pydantic 하위호환)."""
    w = WorldConfig(title="t", time_anchors=[
        TimeAnchor(anchor_id="c1", label="계약 만기", kind="deadline", amount=3, unit="year", entity_id="riel"),
        TimeAnchor(label="세라의 나이", kind="age", amount=15, entity_id="sera")])
    w2 = WorldConfig.model_validate_json(w.model_dump_json())
    assert len(w2.time_anchors) == 2
    assert w2.time_anchors[0].kind == "deadline" and w2.time_anchors[0].amount == 3
    assert w2.time_anchors[1].kind == "age" and w2.time_anchors[1].label == "세라의 나이"
