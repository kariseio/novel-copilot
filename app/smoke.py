# -*- coding: utf-8 -*-
"""스모크 테스트 — 임포트 무결성 + (옵션) 실제 1회차 생성.

  python smoke.py            # 임포트/와이어링만(LLM 호출 없음)
  python smoke.py --live     # 실제 worldgen + 1회차 생성(OPENAI_API_KEY 필요)
"""
import sys


def test_imports():
    from novelcopilot.main import create_app
    from novelcopilot.engine.factory import build_engine, build_rules
    from novelcopilot.config import get_settings
    app = create_app()
    assert app is not None
    print("[OK] 임포트/앱 조립 정상")


def test_engine_no_llm():
    """프로바이더를 가짜로 주입해 결정론 코어(LLM 0콜)만 검증."""
    from novelcopilot.domain.world import (WorldConfig, AttributeSpec, EntitySpec,
                                           WorldRuleSpec, TimelineEntry, Beat)
    from novelcopilot.config import get_settings
    from novelcopilot.engine.factory import build_engine
    from novelcopilot.llm.base import LLMProvider

    class FakeProvider(LLMProvider):
        def chat(self, *a, **k): return "{}"
        def embed(self, texts): return [[0.0] * 4 for _ in texts]

    world = WorldConfig(
        title="테스트", genre="현판",
        attributes=[AttributeSpec(key="eye_color", label="눈 색", kind="categorical",
                                  vocab=["붉은색", "금색"], mutable=False),
                    AttributeSpec(key="rank", label="등급", kind="numeric",
                                  monotonic="non_decreasing", mutable=True)],
        entities=[EntitySpec(id="a", name="가", attrs={"eye_color": "붉은색", "rank": 3}),
                  EntitySpec(id="b", name="나", attrs={"eye_color": "금색", "rank": 4})],
        world_rules=[WorldRuleSpec(rule_id="no_reawaken", text="재각성 불가", flag="reawakening",
                                   keywords=["재각성"])],
        timeline=[TimelineEntry(entity_id="b", attr="status", value="dead", eff_from=4)],
        beats=[Beat(chapter=1, title="시작", summary="가가 각성", entities=["a"])],
    )
    s = get_settings()
    bundle = build_engine(world, FakeProvider(), s)
    facts = bundle.ontology.canon_facts(["a", "b"], 5)
    assert any(f.value == "사망" for f in facts), "타임라인 사망 미반영"
    assert bundle.ontology.state_as_of("b", "status", 3) == "alive"
    assert bundle.ontology.state_as_of("b", "status", 4) == "dead"
    # 룰 생성 확인(status + 2 attr-derived + 1 worldrule)
    from novelcopilot.engine.factory import build_rules
    rules = build_rules(world)
    kinds = {r.predicate_kind for r in rules}
    assert {"timeline_state", "categorical_eq", "numeric_monotone", "worldrule_flag"} <= kinds, kinds
    print("[OK] 결정론 코어/룰 파생 정상 (LLM 0콜)")


def test_live():
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    from novelcopilot.domain.project import ProjectSeed

    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(s.resolved_data_dir()))
    seed = ProjectSeed(genre="현대 판타지", tone="빠른 전개",
                       premise="죽은 자의 기억을 읽는 형사가 자신의 죽음을 목격한다",
                       target_chapters=4)
    print("[..] worldgen 중…")
    state, usage = svc.create_project(seed)
    print(f"[OK] world '{state.world.title}' / 속성 {len(state.world.attributes)} / 인물 "
          f"{len(state.world.entities)} / 비트 {len(state.world.beats)} / worldgen {usage}")
    print("[..] 1회차 생성 중…")
    res = svc.generate_next_chapter(state.id)
    r = res["record"]
    print(f"[OK] {r.chapter}화 status={r.status.value} chars={len(r.text)} "
          f"hard={len(r.hard_remaining)} onto_changes={len(r.ontology_changes)} usage={res['usage_delta']}")
    print(f"     events={len(res['events'])} failures={len(res['failures'])}")
    print("---- 본문 미리보기 ----")
    print(r.text[:400])


if __name__ == "__main__":
    test_imports()
    test_engine_no_llm()
    if "--live" in sys.argv:
        test_live()
    print("\n스모크 완료.")
