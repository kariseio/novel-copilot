# -*- coding: utf-8 -*-
"""VB-1 검증 — 상태 연동 보이스(서술자 카드를 주인공 상태의 함수로). 실 LLM 0콜(전 mock·결정론).

배경(2026-07-28 실측): 서술자 보이스 카드가 작품에 딱 한 장 붙은 정적 문자열이라 30화 내내 같은
'반응 슬롯'(진지한 공기가 오면 농담으로 덮는다 / 결정적 대목엔 말수가 끊긴다)을 지정했고, 지정된 슬롯이
매번 같은 방식으로 채워지며 회차 간 틱이 됐다. 틱 데스크가 잡은 상위 항목이 카드 문구와 일대일로 붙는다:
  · "겁날수록 농담이 많아진다"        → 위기마다 자기비하 상술 농담(패널 4중 3 지목)
  · "불편한 진실을 딴 이유로 바꿔치기" → "A가 아니라 B" 자기정정 문형(양 라인 공통 1위)
  · "진지해지면 말수가 뚝 끊긴다"      → "나는 잠깐 말이 없었다"
카드 ②에는 이미 "이 버릇은 매번 다른 말, 다른 꼴로 나온다"가 적혀 있는데도 굳었다 — 지시로는 못 이긴다
(프로즈 앵커 지배항, ST-12). 남은 레버는 슬롯 자체를 갈아치우는 것.

설계: EntitySpec.voice_stages(상태값→카드) + StyleSpec.narrator_voice_stage_attr(어느 속성이 단계를 정하나).
      harness 가 회차 시점 Ontology.state_as_of 로 조회해 서술자 프레임에 얹는다. 이미 매 회차 갱신되는
      서사 상태(예 truth_awareness 외면→흠칫→의심→직시→인정)를 그대로 쓰므로 LLM 추가 콜 0.

잠그는 계약:
 ① 미설정 무회귀 — stage_attr="" 이면 조회 자체가 없고 voice_cards 바이트 동일(구작·기존 테스트 불변).
 ② 단계 전환 — 같은 작품이 회차에 따라 다른 카드를 받는다(state_as_of 의 eff_from 준수).
 ③ 폴백 — stage_attr 은 있는데 그 상태값의 카드가 없으면 기존 entity.voice 로(누락이 프레임 소실이 되지 않음).
 ④ 런타임 미러 — factory/세션 재수화 양 경로가 voice_stages 를 런타임 Entity 로 실어 나른다.
 ⑤ 하위호환 — 구 JSON(두 필드 부재)이 기본값으로 로드되고 라운드트립이 안정.

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_vb1_voice_stages.py
"""
from __future__ import annotations
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from novelcopilot.config import get_settings
from novelcopilot.domain.world import (WorldConfig, StyleSpec, EntitySpec, AttributeSpec,
                                       Beat, TimelineEntry)
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.engine.factory import build_engine
from novelcopilot.llm.base import LLMProvider

_FRAME_MARK = "[서술자 음성(참조 전용): 이 태도로 이번 회차의 지문을 새 문장으로 서술하라]"   # harness._narrator_frame 문안과 동기화(VL-1)
_BASE_VOICE = "① 정적 카드 — 단계 카드가 없을 때의 폴백."
_STAGES = {
    "외면": "① 값을 못 매기는 게 제일 싫다.",
    "흠칫": "① 값을 못 매기는 물건이 하나 생겼다. 그게 자기라는 걸 아직 장부에 올리지 않았다.",
}


class _Stub(LLMProvider):
    def chat(self, messages, **kw):
        return "x"

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _world(*, stage_attr: str, stages: dict, pov: str = "first") -> WorldConfig:
    return WorldConfig(
        title="t", genre="현대 판타지", premise="p",
        attributes=[AttributeSpec(key="truth_awareness", label="정체자각", kind="categorical",
                                  vocab=["외면", "흠칫", "의심"], mutable=True, ordered=True)],
        entities=[
            EntitySpec(id="hero", name="주인공", etype="character", profile="pf",
                       attrs={"truth_awareness": "외면"}, voice=_BASE_VOICE, voice_stages=dict(stages)),
            EntitySpec(id="sup", name="조연", etype="character", profile="pf2", voice="조연 말투"),
        ],
        # 8화부터 흠칫 — 회차별 전환 대상
        timeline=[TimelineEntry(entity_id="hero", attr="truth_awareness", value="흠칫", eff_from=8)],
        beats=[Beat(chapter=c, title="t", summary="s", entities=["hero", "sup"]) for c in (7, 8)],
        style=StyleSpec(pov=pov, narrator_voice_stage_attr=stage_attr),
    )


def _capture(world, chapter: int) -> str:
    """generate() 실경로에서 board.voice_cards 캡처(draft/rewrite/check LLM 우회)."""
    from novelcopilot.domain.types import SceneSpec
    from novelcopilot.engine.checker import CheckResult
    b = build_engine(world, _Stub(), get_settings())
    gen = b.generator
    gen.plan_scenes = lambda beat, directives: [SceneSpec(index=0, goal="g", key_events=["e"])]
    cap = {}
    gen._draft = lambda board, scene, prev, last=False, **kw: (cap.__setitem__("v", board.voice_cards), "지문.")[1]
    gen._rewrite = lambda text, viols, board, **kw: text
    gen.checker.check_text = lambda *a, **k: CheckResult(violations=[], claims=[])
    gen.generate(chapter, {"title": "t", "summary": "s", "entities": ["hero", "sup"]}, b.ontology, b.rag, b.wiki)
    return cap.get("v", "")


# ---------- ① 미설정 무회귀 ----------
def test_unset_is_byte_identical() -> bool:
    off = _capture(_world(stage_attr="", stages=_STAGES), 8)      # 카드는 있어도 attr 미설정 → 조회 안 함
    bare = _capture(_world(stage_attr="", stages={}), 8)          # 카드 자체가 없는 기존 작품
    ok = (off == bare)                                            # 바이트 동일
    ok &= (_BASE_VOICE in off)                                    # 기존 voice 가 그대로 프레임에 실림
    ok &= all(s not in off for s in _STAGES.values())             # 단계 카드는 새어 나오지 않음
    print(f"[{'OK' if ok else 'FAIL'}] ① 미설정 무회귀: 바이트 동일·기존 voice 유지·단계 카드 미노출")
    return ok


# ---------- ② 회차에 따른 단계 전환 ----------
def test_stage_switches_by_chapter() -> bool:
    w = _world(stage_attr="truth_awareness", stages=_STAGES)
    v7, v8 = _capture(w, 7), _capture(w, 8)
    ok = (_STAGES["외면"] in v7 and _STAGES["흠칫"] not in v7)     # 7화 = 외면(eff_from=8 미도달)
    ok &= (_STAGES["흠칫"] in v8 and _STAGES["외면"] not in v8)    # 8화 = 흠칫(전환 발화)
    ok &= (v7 != v8)                                              # 같은 작품이 회차마다 다른 카드
    ok &= (_BASE_VOICE not in v8)                                 # 단계 카드가 정적 카드를 대체(중복 주입 없음)
    ok &= (_FRAME_MARK in v7 and _FRAME_MARK in v8)               # 프레임 구조 유지
    print(f"[{'OK' if ok else 'FAIL'}] ② 단계 전환: 7화=외면·8화=흠칫·정적 카드 대체")
    return ok


# ---------- ③ 단계 카드 누락 시 폴백 ----------
def test_missing_stage_falls_back() -> bool:
    w = _world(stage_attr="truth_awareness", stages={"외면": _STAGES["외면"]})   # 흠칫 카드 없음
    v8 = _capture(w, 8)
    ok = (_BASE_VOICE in v8)                                      # 프레임 소실이 아니라 기존 voice 로 폴백
    ok &= (_FRAME_MARK in v8)
    # 존재하지 않는 속성을 가리켜도 죽지 않는다(오타 내성)
    v_bad = _capture(_world(stage_attr="없는속성", stages=_STAGES), 8)
    ok &= (_BASE_VOICE in v_bad)
    print(f"[{'OK' if ok else 'FAIL'}] ③ 폴백: 단계 카드 누락·미지 속성 모두 기존 voice 로")
    return ok


# ---------- ④ 런타임 미러(factory / 세션 재수화) ----------
def test_runtime_mirror() -> bool:
    w = _world(stage_attr="truth_awareness", stages=_STAGES)
    ont = build_engine(w, _Stub(), get_settings()).ontology
    ok = (dict(getattr(ont.entities["hero"], "voice_stages", {})) == _STAGES)     # factory 경로
    # 세션 재수화 경로(runtime_entities → Entity)
    from novelcopilot.services.session import EngineSession
    st = ProjectState(id="vb1test", seed=ProjectSeed(title="t"), world=w)
    st.runtime_entities = [EntitySpec(id="dyn", name="동적", etype="character",
                                      voice="v", voice_stages={"외면": "동적 단계"})]
    sess = EngineSession("vb1test", w, _Stub(), get_settings())
    sess.rehydrate(st)
    ok &= (dict(sess.bundle.ontology.entities["dyn"].voice_stages) == {"외면": "동적 단계"})
    print(f"[{'OK' if ok else 'FAIL'}] ④ 런타임 미러: factory·세션 재수화 양 경로 보존")
    return ok


# ---------- ⑤ 하위호환(구 JSON) ----------
def test_backward_compat() -> bool:
    old_ent = EntitySpec.model_validate({"id": "x", "name": "n"})                 # 필드 부재
    old_style = StyleSpec.model_validate({})
    ok = (old_ent.voice_stages == {} and old_style.narrator_voice_stage_attr == "")
    # 라운드트립 안정
    rt = EntitySpec.model_validate(EntitySpec(id="y", name="m", voice_stages={"외면": "c"}).model_dump())
    ok &= (rt.voice_stages == {"외면": "c"})
    rts = StyleSpec.model_validate(StyleSpec(narrator_voice_stage_attr="a").model_dump())
    ok &= (rts.narrator_voice_stage_attr == "a")
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ 하위호환: 구 JSON 기본값·라운드트립 안정")
    return ok


if __name__ == "__main__":
    tests = [test_unset_is_byte_identical, test_stage_switches_by_chapter,
             test_missing_stage_falls_back, test_runtime_mirror, test_backward_compat]
    results = [t() for t in tests]
    n = sum(results)
    print(f"\nVB-1(상태 연동 보이스) 검증: {'ALL GREEN' if all(results) else 'FAIL'} ({n}/{len(results)} tests)")
    sys.exit(0 if all(results) else 1)
