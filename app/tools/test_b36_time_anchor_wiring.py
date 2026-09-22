# -*- coding: utf-8 -*-
"""B-36 time_anchors worldgen 배선 — B-33 방어(시계 파생 산술 주입)의 실효화. LLM 스텁(결정론).

소스 진단(B-33 이월): B-33 은 story_clock.anchor_facts 로 계약 만기 잔여·현재 나이를 [확정 설정]·사건메뉴·
비트 3레이어에 결정론 주입하도록 배선했으나 time_anchors 를 채우는 코드가 0이라 방어가 비활성이었다.
이 티켓이 worldgen generate 경로에 '전제·브리프에 명시된' 기간/기한/나이만 구조화하는 추출을 잇는다.

핵심 계약:
- 발명 금지(OV-3 증거 강제): 각 앵커는 근거(원문 인용) 필수 — 코드가 소스 substring 대조(모델 자기보고 아님).
- 미확인이면 [] → B-33 무주입 하위호환(anchor_facts 빈 값, 프롬프트 바이트 동일).
- genre-blind: kind 는 결정론 산술 원시 2종(deadline/age), 예시는 중립·비능력 도메인(계약 기간·나이).
- B-33 이월노트 2건(①미래 age 이중표면 ②story_time 억제 비대칭) 동반 해소.
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

from novelcopilot.domain.project import ProjectSeed
from novelcopilot.domain.world import WorldConfig, TimeAnchor, EntitySpec, StyleSpec
from novelcopilot.domain.types import TimeDelta, ContextBoard, SceneSpec
from novelcopilot.engine.story_clock import anchor_facts, story_time_for
from novelcopilot.engine.prompts import PromptAssembler
from novelcopilot.worldgen import WorldGenerator
from novelcopilot.llm.base import LLMProvider


class _AnchorStub(LLMProvider):
    """추출 콜에 고정 앵커 JSON 을 반환(direct extract_time_anchors 테스트용 — LLM 0콜)."""
    def __init__(self, anchors_json: dict):
        super().__init__(); self._anchors = json.dumps(anchors_json, ensure_ascii=False)
    def chat(self, messages, **kw): return self._anchors
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


class _TwoModeStub(LLMProvider):
    """generate() e2e 스텁: worldgen 콜엔 world JSON, 추출 콜(system 에 '시간 기준점')엔 앵커 JSON."""
    def __init__(self, world_json: dict, anchors_json: dict):
        super().__init__()
        self._world = json.dumps(world_json, ensure_ascii=False)
        self._anchors = json.dumps(anchors_json, ensure_ascii=False)
        self.anchor_usr = ""
    def chat(self, messages, **kw):
        sysmsg = messages[0].get("content", "") if messages else ""
        if "시간 기준점" in sysmsg:
            self.anchor_usr = messages[-1].get("content", "")
            return self._anchors
        return self._world
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _seed_world(premise, entities=None):
    seed = ProjectSeed(title="t", genre="로맨스", premise=premise)
    world = WorldConfig(title="t", genre="로맨스", premise=premise,
                        entities=entities or [EntitySpec(id="heroine", name="세라")])
    return seed, world


_WORLD_JSON = {
    "title": "계약결혼", "genre": "로맨스", "tone": "",
    "premise": "여주는 3년 계약으로 정략결혼한다.", "synopsis": "세라는 계약으로 결혼한다.",
    "attributes": [{"key": "status", "label": "생사", "kind": "state",
                    "states": ["alive", "dead"], "irreversible": ["dead"], "terminal": ["dead"], "mutable": True}],
    "entities": [{"id": "sera", "name": "세라", "etype": "character", "attrs": {}}],
    "beats": [{"chapter": 1, "title": "만남", "summary": "s", "key_events": ["e"], "entities": ["sera"]}],
}


# ---------------------------------------------------------------- 배선(추출) ----

def test_extract_deadline_and_reaches_injection():
    """전제 '3년 계약' → deadline 앵커 채워짐 → B-33 소비경로로 '약 3.0년 남음'이 [확정 설정]에 도달."""
    seed, world = _seed_world("여주는 3년 계약으로 정략결혼한다.")
    stub = _AnchorStub({"anchors": [{"label": "계약 만기", "kind": "deadline",
                                     "amount": 3, "unit": "year", "evidence": "3년 계약"}]})
    anchors = WorldGenerator(stub).extract_time_anchors(seed, world)
    assert len(anchors) == 1 and anchors[0].kind == "deadline" and anchors[0].amount == 3
    facts = anchor_facts(anchors, [TimeDelta(amount=6, unit="day")])       # 엿새 경과
    assert "3.0년 남음" in facts[0].value and "도래" not in facts[0].value  # 6일차 '만기 도래' 발명 반증
    board = ContextBoard(chapter=5, ground_truth=facts)
    out = PromptAssembler(StyleSpec(rules=["x"]), 4000).assemble(
        board, SceneSpec(index=0, goal="g", key_events=["e"]), "")
    settled = out.split("[작가 지시")[0]
    assert "계약 만기" in settled and "3.0년 남음" in settled              # [확정 설정] 도달(end-to-end)


def test_discard_anchor_without_evidence():
    """근거 없는 앵커 폐기(발명 금지) — 소스에 없는 근거·빈 근거 전부 코드가 대조 후 버린다."""
    seed, world = _seed_world("잔잔한 학원 로맨스. 봄에 시작된다.")   # 명시 시간 기준점 없음
    stub = _AnchorStub({"anchors": [
        {"label": "세계 멸망", "kind": "deadline", "amount": 10, "unit": "year", "evidence": "10년 뒤 세계 멸망"},  # 소스 부재=발명
        {"label": "빈근거", "kind": "deadline", "amount": 5, "unit": "year", "evidence": ""},                      # 근거 빈 값
    ]})
    assert WorldGenerator(stub).extract_time_anchors(seed, world) == []


def test_synopsis_only_time_not_anchored():
    """B-36 corpus 축소: world.synopsis(LLM 산출물)에만 있는 시간 표현은 앵커가 되지 않는다.
    작가 입력(seed 전제·브리프)엔 없고 LLM 시놉시스가 지어낸 '3년 뒤'를 근거로 든 앵커는, 코퍼스에서
    synopsis 를 뺐으므로 substring 대조에서 탈락 → 발명 기한의 앵커 역류 차단(증거 강제)."""
    seed = ProjectSeed(title="t", genre="로맨스", premise="잔잔한 학원 로맨스.")    # 작가 입력엔 시간 기준점 없음
    world = WorldConfig(title="t", genre="로맨스", premise="잔잔한 학원 로맨스.",
                        synopsis="세라는 3년 뒤 졸업식에서 재회한다.",              # LLM 산출물에만 '3년 뒤'
                        entities=[EntitySpec(id="sera", name="세라")])
    stub = _AnchorStub({"anchors": [{"label": "졸업", "kind": "deadline",
                                     "amount": 3, "unit": "year", "evidence": "3년 뒤"}]})
    assert WorldGenerator(stub).extract_time_anchors(seed, world) == []   # synopsis 근거 → 코퍼스 부재 → 폐기


def test_empty_anchors_backward_compat():
    """추출기가 아무것도 못 찾음 → [] → anchor_facts 무주입(B-33 바이트 동일 경로)."""
    seed, world = _seed_world("평범한 전제.")
    anchors = WorldGenerator(_AnchorStub({"anchors": []})).extract_time_anchors(seed, world)
    assert anchors == []
    assert anchor_facts(anchors, [TimeDelta(amount=1, unit="day")]) == []


def test_unknown_unit_discarded_at_source():
    """근거는 유효해도 미상 단위 deadline 은 소스에서 선차단(거짓 잔여 단정 회피)."""
    seed, world = _seed_world("2 fortnight 안에 끝내야 하는 계약.")
    stub = _AnchorStub({"anchors": [{"label": "이상기한", "kind": "deadline",
                                     "amount": 2, "unit": "fortnight", "evidence": "2 fortnight"}]})
    assert WorldGenerator(stub).extract_time_anchors(seed, world) == []


def test_free_kind_discarded():
    """genre-blind: kind 는 결정론 산술 원시 2종만 — 계산 불가한 자유 kind 는 폐기(발명 산술 방지)."""
    seed, world = _seed_world("D-100 카운트다운이 걸린 세계. 3년 계약도 있다.")
    stub = _AnchorStub({"anchors": [
        {"label": "카운트다운", "kind": "countdown", "amount": 100, "unit": "day", "evidence": "D-100 카운트다운"},  # 미지원 kind
        {"label": "계약 만기", "kind": "deadline", "amount": 3, "unit": "year", "evidence": "3년 계약"},
    ]})
    anchors = WorldGenerator(stub).extract_time_anchors(seed, world)
    assert len(anchors) == 1 and anchors[0].kind == "deadline"   # countdown 폐기, deadline 만 생존


def test_extract_age_links_entity():
    """age 앵커 + 실재 entity_id 링크(감사·UI). amount=시작 나이."""
    seed, world = _seed_world("열다섯 살 소녀 세라의 이야기.", [EntitySpec(id="sera", name="세라")])
    stub = _AnchorStub({"anchors": [{"label": "세라의 나이", "kind": "age",
                                     "amount": 15, "entity_id": "sera", "evidence": "열다섯 살"}]})
    anchors = WorldGenerator(stub).extract_time_anchors(seed, world)
    assert len(anchors) == 1 and anchors[0].kind == "age" and anchors[0].amount == 15
    assert anchors[0].entity_id == "sera"


def test_bogus_entity_id_cleared_anchor_kept():
    """미존재 entity_id 는 클리어(계산 불필요 필드) — 근거 유효한 앵커 자체는 유지."""
    seed, world = _seed_world("열다섯 살 세라.", [EntitySpec(id="sera", name="세라")])
    stub = _AnchorStub({"anchors": [{"label": "나이", "kind": "age",
                                     "amount": 15, "entity_id": "nobody", "evidence": "열다섯 살"}]})
    anchors = WorldGenerator(stub).extract_time_anchors(seed, world)
    assert len(anchors) == 1 and anchors[0].entity_id == ""


def test_extract_never_throws_on_bad_json():
    """추출 실패(비정형 반환) → [](NEVER throws, 생성 차단 금지)."""
    class _Bad(LLMProvider):
        def chat(self, messages, **kw): return "not json at all"
        def embed(self, texts): return [[0.0] * 4 for _ in texts]
    seed, world = _seed_world("3년 계약.")
    assert WorldGenerator(_Bad()).extract_time_anchors(seed, world) == []


def test_extract_never_throws_on_scalar_anchors():
    """유효 JSON 이지만 anchors 가 스칼라/bool(비이터러블)이면 → [](for a in 1 폭발 차단, generate() 중단 방지).
    json_mode LLM 이 'count' 로 퇴화해 {"anchors": 3} 를 낼 때 세계생성 전체가 죽지 않아야 한다."""
    seed, world = _seed_world("3년 계약.")
    for bad in ({"anchors": 1}, {"anchors": 3.5}, {"anchors": True}, {"anchors": "3년"}, {"anchors": {"x": 1}}):
        assert WorldGenerator(_AnchorStub(bad)).extract_time_anchors(seed, world) == []


# ---------------------------------------------------------------- e2e generate() ----

def test_generate_populates_time_anchors_e2e():
    """스텁 LLM 통합: 전제 '3년 계약' 세계 생성 → world.time_anchors 채워짐(배선 소스→소비 연결)."""
    seed = ProjectSeed(title="계약결혼", genre="로맨스", premise="여주는 3년 계약으로 정략결혼한다.")
    anchors_json = {"anchors": [{"label": "계약 만기", "kind": "deadline",
                                 "amount": 3, "unit": "year", "evidence": "3년 계약"}]}
    stub = _TwoModeStub(_WORLD_JSON, anchors_json)
    world = WorldGenerator(stub).generate(seed)
    assert len(world.time_anchors) == 1 and world.time_anchors[0].kind == "deadline"
    assert "3년 계약" in stub.anchor_usr   # 추출 콜이 소스 텍스트(evidence corpus)를 받음
    facts = [f"{f.entity}: {f.value}" for f in anchor_facts(world.time_anchors, [TimeDelta(amount=6, unit="day")])]
    assert any("3.0년 남음" in t for t in facts)


def test_generate_no_time_info_empty_e2e():
    """시간 정보 없는 전제 → 추출기가 빈 배열 → world.time_anchors == [](무주입 하위호환)."""
    wj = dict(_WORLD_JSON, premise="평범한 학원 일상.", synopsis="세라의 학교 생활.")
    stub = _TwoModeStub(wj, {"anchors": []})
    world = WorldGenerator(stub).generate(ProjectSeed(title="학원물", genre="로맨스", premise="평범한 학원 일상."))
    assert world.time_anchors == []


# ---------------------------------------------------------------- B-33 이월노트 ----

def test_carried_note_age_advanced_references_origin():
    """이월노트①(미래 age 이중표면): 진행된 현재나이가 시작 나이를 명시 참조 → 정지 설정과 모순 아닌 '진행'."""
    facts = anchor_facts([TimeAnchor(label="세라", kind="age", amount=15)], [TimeDelta(amount=2, unit="year")])
    v = facts[0].value
    assert "17세" in v and "시작 시 15세" in v      # 15(정지 설정)→17(현재) 관계 명시
    assert "나이 변동 없음" not in v
    # 동결(경과<1년)은 여전히 '나이 변동 없음'(15→16 드리프트 차단 문구 유지)
    frozen = anchor_facts([TimeAnchor(label="세라", kind="age", amount=15)], [TimeDelta(amount=6, unit="day")])
    assert "15세" in frozen[0].value and "나이 변동 없음" in frozen[0].value and "16세" not in frozen[0].value


def test_carried_note_all_unknown_position_honest():
    """이월노트②(억제 비대칭): 전부 미상이면 anchor_facts 도 '시작 시점' 단정 대신 '경과 미상'(story_time 억제와 대칭)."""
    unk = [TimeDelta.parse({"amount": 2, "unit": "fortnight"})]   # 미상 단위 → 경과 0·had_unknown
    assert story_time_for(unk) == ""                              # story_time 은 억제(정직)
    v = anchor_facts([TimeAnchor(label="계약", kind="deadline", amount=3, unit="year")], unk)[0].value
    assert "경과 시간 미상" in v and "이야기 시작 시점" not in v   # 비대칭 해소(거짓 시작단정 회피)
    # known-zero(명시적 경과 없음)는 여전히 '시작 시점' 정당(억제 대상 아님)
    v0 = anchor_facts([TimeAnchor(label="계약", kind="deadline", amount=3, unit="year")],
                      [TimeDelta(amount=0, unit="minute")])[0].value
    assert "이야기 시작 시점" in v0
