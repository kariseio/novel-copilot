# -*- coding: utf-8 -*-
"""3개월차 2차분 검증 — G3 거버넌스(연재 회고 제안 + 작가 승인 스파인 개정). LLM 0콜(스텁).
실행: PYTHONPATH=app python tools/test_month3b_g3gov.py
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.retrospective import generate_retrospective
from novelcopilot.llm.base import LLMProvider


class ScriptFake(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = 0

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


def test_retrospective_validation() -> bool:
    """제안 생성 — 미집필 아크/엔딩 외 대상·허용 외 필드·빈 값은 코드가 폐기(미래만 개정)."""
    fake = ScriptFake([{"diagnosis": "진단 문장", "revisions": [
        {"target": "ending", "field": "ending", "new_value": "새 엔딩", "reason": "r"},        # valid
        {"target": "arc:arc2", "field": "goal", "new_value": "새 목표", "reason": "r"},          # valid(미집필)
        {"target": "arc:arc1", "field": "goal", "new_value": "x"},                              # arc1=완결→폐기
        {"target": "arc:arc2", "field": "badfield", "new_value": "x"},                          # 허용외 필드→폐기
        {"target": "ending", "field": "central_question", "new_value": ""},                     # 빈값→폐기
    ]}])
    prop = generate_retrospective(fake, genre="x", ending="old", done_arcs=[{"arc_id": "arc1"}],
                                  upcoming_arcs=[{"arc_id": "arc2"}], pacing={}, ledger_open=[], reader_trend=[])
    kept = {(r["target"], r["field"]) for r in prop["revisions"]}
    ok = (prop["diagnosis"] == "진단 문장" and len(prop["revisions"]) == 2
          and ("ending", "ending") in kept and ("arc:arc2", "goal") in kept)
    print(f"[{'OK' if ok else 'FAIL'}] 회고 제안 검증: 유효 2건만 통과(완결아크·허용외필드·빈값 폐기) {kept}")
    return ok


def test_revise_spine_transaction() -> bool:
    """작가 승인 개정 — 미집필 아크/엔딩만 반영, 완결 아크·없는 아크·허용외 필드 거부. 영속 round-trip."""
    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(ending="옛 엔딩", central_question="Q"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", goal="옛 목표1", done=True,
            episodes=[Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, done=True)]),
        Arc(arc_id="arc2", order=2, title="A2", goal="옛 목표2", episodes=[])])   # 미집필
    st = ProjectState(id="t", seed=ProjectSeed(target_chapters=12), world=w, created_at="t")
    svc.repo.save(st)
    res = svc.revise_spine("t", [
        {"target": "arc:arc2", "field": "goal", "new_value": "새 목표2"},       # applied(미집필)
        {"target": "ending", "field": "ending", "new_value": "새 엔딩"},        # applied
        {"target": "arc:arc1", "field": "goal", "new_value": "침범"},           # rejected(완결)
        {"target": "arc:arc9", "field": "goal", "new_value": "x"},              # rejected(없음)
        {"target": "arc:arc2", "field": "summary", "new_value": "x"},           # rejected(허용외 필드)
    ])
    st2 = svc.get_project("t")
    ok = (len(res["applied"]) == 2 and len(res["rejected"]) == 3
          and st2.world.spine.arc("arc2").goal == "새 목표2"          # 미래 아크 개정 반영
          and st2.world.spine.ending.ending == "새 엔딩"             # 엔딩 개정 반영(영속)
          and st2.world.spine.arc("arc1").goal == "옛 목표1")         # 완결 아크 불변(과거 보호)
    print(f"[{'OK' if ok else 'FAIL'}] 스파인 개정: 미래아크·엔딩만 반영(applied {len(res['applied'])})·완결아크 보호·round-trip")
    return ok


if __name__ == "__main__":
    results = [test_retrospective_validation(), test_revise_spine_transaction()]
    print("\n3개월차 2차분(G3 거버넌스) 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
