# -*- coding: utf-8 -*-
"""XR-3 K3·K4 — 계보 역류 금지 + LLM 콜 증가 0(결정론·실 LLM 0콜).

K3(계보 역류 금지): 관측 산출물이 생성 프롬프트로 되돌아가면 자기 이력 앵커(B-32e 폐기 전례)가 되고,
관측이 대상을 바꾼다. lineage 는 **쓰기 전용**이다.
K4(LLM 콜 증가 0): 계보·태그는 전부 코드 층(contextvar·dict 기록)이다. 1콜이라도 늘면 설계 실패.

검사 6축:
  ⓐ 소비 코드 0 — novelcopilot 에서 'lineage' 를 언급하는 모듈은 기록자 3곳뿐(프롬프트 조립 모듈 0)
  ⓑ 조립 표면 부재 — ContextBoard·SceneSpec 에 lineage 필드가 없다(프롬프트 입력 스키마 불변)
  ⓒ 프롬프트 무노출 — fake 생성 1회의 전 콜 메시지에 'lineage'·gap 문구가 0건
  ⓓ 소비자 바이트 동일 — gen_context 를 읽는 기존 경로(structure_history·비트 복원·검증 축·
     결말 누출 소스)가 lineage 유무에 무관하게 같은 값을 낸다(구 레코드 vs 신 레코드)
  ⓔ 콜 수 동일(K4) — 계보 기록을 무력화한 생성과 정상 생성의 chat 콜 수가 같다
  ⓕ 태그 콜 0 — consumer 태그 자체가 어떤 콜도 만들지 않는다

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_xr3_no_backflow.py
"""
from __future__ import annotations
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from novelcopilot.domain.types import ContextBoard, SceneSpec, ChapterRecord, ChapterStatus
from novelcopilot.engine.harness import ChapterGenerator

from test_xr3_lineage import _Cap, _world, _settings, _generate   # 픽스처 재사용(이중 구현 금지)

_PKG = _HERE.parent / "novelcopilot"
# 기록자 — 여기 말고 다른 모듈이 lineage 를 알게 되면 역류 후보다.
_WRITERS = {
    "engine/harness.py",      # draft_ctx['lineage'] 생산
    "engine/ontology.py",     # with_lineage 부가 반환(주입값 불변)
    "services/copilot.py",    # 엔진 결측 블록 보강(_fill_draft_lineage)
}


# ── ⓐ 소비 코드 0 ──
def test_no_module_outside_writers_knows_lineage():
    hits = {p.relative_to(_PKG).as_posix() for p in _PKG.rglob("*.py")
            if "lineage" in p.read_text(encoding="utf-8")}
    assert hits == _WRITERS, f"lineage 를 아는 모듈이 늘었다(역류 후보): {sorted(hits - _WRITERS)}"
    # 프롬프트 조립 모듈은 특히 0이어야 한다(핑크 엘리펀트·자기 이력 앵커)
    for name in ("engine/prompts.py", "engine/story_pass_prompts.py", "worldgen/arc_planner.py"):
        assert "lineage" not in (_PKG / name).read_text(encoding="utf-8")
    print(f"[OK] ⓐ lineage 인지 모듈 = 기록자 {len(_WRITERS)}곳뿐(프롬프트 조립 모듈 0)")


# ── ⓑ 조립 표면 부재 ──
def test_prompt_input_schema_has_no_lineage():
    assert "lineage" not in ContextBoard.model_fields
    assert "lineage" not in SceneSpec.model_fields
    print("[OK] ⓑ ContextBoard·SceneSpec 에 lineage 필드 없음(프롬프트 입력 스키마 불변)")


# ── ⓒ 프롬프트 무노출 ──
def test_lineage_never_appears_in_any_prompt():
    rec, prov = _generate()
    assert prov.captured, "콜 미캡처"
    lin = rec.gen_context["draft"]["lineage"]
    needles = ["lineage", "계보", lin["scope"][:12],
               "rule_id 는 factory 직렬화에서 소실"]
    for msgs in prov.captured:
        blob = "\n".join(m.get("content", "") for m in msgs if isinstance(m.get("content"), str))
        for n in needles:
            assert n not in blob, f"계보 문자열이 프롬프트에 누출: {n!r}"
    print(f"[OK] ⓒ 캡처된 {len(prov.captured)}콜 전부에 계보 문자열 0건")


# ── ⓓ 소비자 바이트 동일 ──
def test_gen_context_consumers_ignore_lineage():
    from types import SimpleNamespace as NS
    from novelcopilot.engine.structure_history import structure_history
    from novelcopilot.engine.verification import _summ_story_pass
    from novelcopilot.services.copilot import _persisted_beat, CopilotService

    base_gc = {"draft": {"beat": {"title": "t", "summary": "s", "key_events": ["사건"],
                                  "entities": ["hero"]},
                         "story_so_far": "누적 줄거리.", "ground_truth": ["주인공: 소지금(원)=12000"]},
               "story_pass": {"status": "confirmed", "story": "- 한 줄", "lines": 1}}
    old = ChapterRecord(chapter=1, title="t", status=ChapterStatus.FINALIZED,
                        text="본문 한 줄", hook_type="cliff", gen_context=base_gc)
    # 신 레코드 = 같은 상태 + lineage(전 블록·gap 문구 포함 — 소비 경로가 이걸 보면 즉시 드러난다)
    new_gc = {**base_gc, "draft": {**base_gc["draft"],
                                   "lineage": _generate()[0].gen_context["draft"]["lineage"]}}
    new = ChapterRecord(chapter=1, title="t", status=ChapterStatus.FINALIZED,
                        text="본문 한 줄", hook_type="cliff", gen_context=new_gc)
    state = NS(world=NS(spine=NS(ending=NS(ending="결말 원문", thematic_payoff="", central_question="")),
                        synopsis="시놉시스 원문"), story_pass_records=[])

    assert structure_history([old]) == structure_history([new])
    assert _persisted_beat(old) == _persisted_beat(new)
    assert _summ_story_pass(old) == _summ_story_pass(new)
    assert CopilotService._ending_sources_for(state, old) == CopilotService._ending_sources_for(state, new)
    print("[OK] ⓓ gen_context 소비 경로 4종이 lineage 유무에 무관하게 동일 산출")


# ── ⓔ 콜 수 동일(K4) ──
def test_lineage_adds_zero_llm_calls():
    rec_a, prov_a = _generate()
    orig = ChapterGenerator._draft_lineage
    try:                                   # 계보 기록만 무력화하고 같은 생성 재실행
        ChapterGenerator._draft_lineage = lambda self, *a, **kw: {"blocks": []}
        rec_b, prov_b = _generate()
    finally:
        ChapterGenerator._draft_lineage = orig
    assert prov_a.calls == prov_b.calls, f"계보가 콜을 늘렸다: {prov_a.calls} vs {prov_b.calls}"
    assert rec_a.gen_context["draft"]["lineage"]["blocks"]      # 정상 경로는 실제로 기록했다
    assert not rec_b.gen_context["draft"]["lineage"]["blocks"]  # 대조군은 비었다(대조 유효)
    # 계보 on/off 두 경로의 조립 프롬프트가 바이트 동일(설계 §1.6-3 — 관측이 대상을 바꾸지 않는다)
    assert prov_a.captured == prov_b.captured, "계보 기록이 조립 프롬프트를 바꿨다(바이트 불변 위반)"
    print(f"[OK] ⓔ K4 — 계보 on/off 콜 수 동일({prov_a.calls}콜) · 조립 프롬프트 바이트 동일")


# ── ⓕ 태그 콜 0 ──
def test_consumer_tag_adds_zero_calls():
    from novelcopilot.llm import promptlog
    prov = _Cap()
    with promptlog.consumer("draft"):
        pass
    @promptlog.stage("draft")
    def _noop():
        return None
    _noop()
    assert prov.calls == 0
    print("[OK] ⓕ consumer 태그·데코레이터 자체의 LLM 콜 0")


if __name__ == "__main__":
    fails = 0
    for fn in (test_no_module_outside_writers_knows_lineage, test_prompt_input_schema_has_no_lineage,
               test_lineage_never_appears_in_any_prompt, test_gen_context_consumers_ignore_lineage,
               test_lineage_adds_zero_llm_calls, test_consumer_tag_adds_zero_calls):
        try:
            fn()
        except AssertionError as e:
            fails += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print("=" * 60)
    print("[OK] XR-3 K3(역류 금지)·K4(콜 증가 0) 통과" if not fails else f"[FAIL] {fails}건")
    sys.exit(1 if fails else 0)
