# -*- coding: utf-8 -*-
"""Stage 0 실증(§3.4) — ESCALATED/contradiction 분기가 '실제로 점등'하는지 오염주입으로 증명.

비평2 Blocker: escalation 영속 큐를 짓기 전에, 그 emit 지점이 정상 경로에서 실발생하는지 먼저 green.
LLM 0콜(FakeProvider + 결정론 경로만). 실행: PYTHONPATH=app python tools/prove_escalation.py
"""
from __future__ import annotations
import sys

from novelcopilot.config import get_settings
from novelcopilot.domain.world import (WorldConfig, AttributeSpec, EntitySpec, TimelineEntry, Beat)
from novelcopilot.domain.types import Violation, SignalGrade, SceneSpec
from novelcopilot.engine.factory import build_engine
from novelcopilot.engine.checker import CheckResult
from novelcopilot.llm.base import LLMProvider


class FakeProvider(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    return WorldConfig(
        title="실증", genre="현판",
        attributes=[
            AttributeSpec(key="eye_color", label="눈 색", kind="categorical",
                          vocab=["붉은색", "금색"], mutable=False),          # 불변
            AttributeSpec(key="rank", label="등급", kind="numeric",
                          monotonic="non_decreasing", mutable=True),          # 단조
        ],
        entities=[
            EntitySpec(id="a", name="가", attrs={"eye_color": "붉은색", "rank": 5}),
            EntitySpec(id="b", name="나", attrs={"eye_color": "금색", "rank": 4}),
        ],
        timeline=[TimelineEntry(entity_id="b", attr="status", value="dead", eff_from=2,
                                reason="2화 사망")],
        beats=[Beat(chapter=1, title="t", summary="s", entities=["a"])],
    )


def test_escalated_path() -> bool:
    """비수렴 → ChapterStatus.ESCALATED 실발생 + 미색인 증명(harness 실경로)."""
    s = get_settings()
    b = build_engine(_world(), FakeProvider(), s)
    gen = b.generator
    # 오염주입: 항상 hard 위반을 뱉는 checker + 항등 _rewrite + 고정 draft/plan(LLM 우회)
    gen.plan_scenes = lambda beat, directives: [SceneSpec(index=0, goal="g", key_events=["e"])]
    gen._draft = lambda board, scene, prev, last=False, **kw: "사망자가 칼을 휘둘렀다."   # **kw: 단일패스 재설계 후 closing/recent_tails/chapter_mode 흡수
    gen._rewrite = lambda text, viols, board, **kw: text     # 항등 → 비수렴 강제(max_tokens 등 흡수)
    gen.checker.check_text = lambda text, ont, ch, inv: CheckResult(
        violations=[Violation(entity="나", kind="state_timeline", grade=SignalGrade.QUASI,
                              canon="2화 사망", text="현재 행동", evidence="주입")], claims=[])

    rec = gen.generate(1, {"title": "t", "summary": "s", "entities": ["a"]},
                       b.ontology, b.rag, b.wiki)
    events = {(e["node"], e["event"]) for e in b.event_bus.buffer}
    ok = (rec.status.value == "ESCALATED" and len(rec.hard_remaining) > 0
          and rec.indexed_chunks == 0 and rec.wiki_pages_touched == 0
          and ("finalize", "escalation") in events
          and ("scene_loop", "non_convergence") in events)
    print(f"[{'OK' if ok else 'FAIL'}] ESCALATED 실경로: status={rec.status.value} "
          f"hard={len(rec.hard_remaining)} indexed={rec.indexed_chunks} "
          f"events={'finalize/escalation' if ('finalize','escalation') in events else 'MISSING'},"
          f"{'non_convergence' if ('scene_loop','non_convergence') in events else 'MISSING'}")
    return ok


def test_contradiction_branches() -> bool:
    """ontology_updater.apply 의 3개 contradiction 분기 각각 점등(미적용 + escalation emit)."""
    s = get_settings()
    b = build_engine(_world(), FakeProvider(), s)
    ch = 5
    proposal = {"new_characters": [], "state_changes": [
        {"id": "b", "attr": "status", "value": "alive", "note": "사망→생존"},     # death_revive
        {"id": "a", "attr": "rank", "value": "3", "note": "등급 하락"},           # monotonic
        {"id": "a", "attr": "eye_color", "value": "금색", "note": "눈색 변경"},     # immutable
    ]}
    changes, new_specs, new_tl, new_edges = b.updater.apply(proposal, b.ontology, ch)
    contras = [c for c in changes if c.op == "contradiction"]
    escalations = [e for e in b.event_bus.buffer
                   if e["node"] == "ontology_update" and e["event"] == "escalation"]
    none_applied = all(not c.applied for c in contras)
    no_timeline = len(new_tl) == 0   # 모순은 timeline 에 박히지 않아야
    ok = len(contras) == 3 and none_applied and len(escalations) == 3 and no_timeline
    print(f"[{'OK' if ok else 'FAIL'}] contradiction 3분기: contradictions={len(contras)}/3 "
          f"applied=0?{none_applied} escalation_emits={len(escalations)}/3 timeline_writes={len(new_tl)}(=0)")
    for c in contras:
        print(f"     - {c.entity}: {c.detail} | {c.reason}")
    return ok


if __name__ == "__main__":
    a = test_escalated_path()
    b_ = test_contradiction_branches()
    print("\n실증 결과:", "ALL GREEN ✅" if (a and b_) else "FAIL ❌")
    sys.exit(0 if (a and b_) else 1)
