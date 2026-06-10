# -*- coding: utf-8 -*-
"""R3 검증 — 협업형 월드젠 대화 턴: 제안 적용(엔티티/관계/설정집)·결정론 게이트로 차단·영속 (LLM 0콜 스텁).
실행: PYTHONPATH=app python tools/test_r3_worldgen.py
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
import novelcopilot.services.copilot as cop

PROPOSAL = {
    "reply": "좋아요, 라이벌과 그 소속을 추가했어요.",
    "new_entities": [{"name": "라이벌", "etype": "character", "role": "적수"},
                     {"name": "흑야회", "etype": "faction"}],
    "new_relations": [{"src": "hero", "dst": "라이벌", "rel_id": "enemy_of"},   # valid
                      {"src": "라이벌", "dst": "흑야회", "rel_id": "member_of"},  # valid(신규 dst)
                      {"src": "hero", "dst": "라이벌", "rel_id": "운명의_적", "state": "숙적"},  # 자유 타입+상태 → valid(개방)
                      {"src": "hero", "dst": "없는이", "rel_id": "ally_of"},     # 미해결 → blocked(구조)
                      {"src": "hero", "dst": "hero", "rel_id": "ally_of"}],      # self → blocked(구조)
    "new_bible": [{"category": "faction_politics", "title": "흑야회 강령", "prose": "어둠 속에서 질서를 세운다."}],
    "questions": ["라이벌의 진짜 동기는 무엇인가요?"],
}


def test_worldgen_turn() -> bool:
    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    w = WorldConfig(title="t", genre="현대 판타지", entities=[EntitySpec(id="hero", name="주인공")])
    svc.repo.save(ProjectState(id="t", seed=ProjectSeed(), world=w, created_at="t"))
    cop.WorldgenChat.turn = lambda self, world, ont, bible, hist, msg: PROPOSAL    # LLM 0콜 스텁
    res = svc.worldgen_turn("t", "라이벌을 한 명 추가하자")
    ok = res is not None
    ents = [a for a in res["applied"] if a["kind"] == "entity"]
    rels = [a for a in res["applied"] if a["kind"] == "relation"]
    bibs = [a for a in res["applied"] if a["kind"] == "bible"]
    ok &= len(ents) == 2 and len(rels) == 3 and len(bibs) == 1     # 자유 타입 포함 유효분 적용(개방)
    ok &= len(res["blocked"]) == 2                                 # 구조적 차단만(미해결/self)
    ok &= any(a.get("state") == "숙적" for a in rels)                # 자유 타입+질적 상태 보존
    ok &= len(res["questions"]) == 1 and bool(res["reply"])
    # 영속·재로드 + 캐논(ground_truth) 반영
    svc.sessions.evict("t")
    st2 = svc.get_project("t")
    sess, _ = svc.get_session("t")
    names = {e.name for e in sess.bundle.ontology.entities.values()}
    ents = {e.name: e for e in sess.bundle.ontology.entities.values()}
    edges = sess.bundle.ontology.edges_as_of(1)
    ok &= "라이벌" in names and "흑야회" in names
    ok &= ents["라이벌"].provisional is True                                       # AI 제안 = 잠정(작가 promote 전)
    ok &= len(edges) == 3 and all(e.trust_tier == "narrative_inferred" for e in edges)  # AI 제안 = 비binding(비대칭 보존)
    ok &= any(b.title == "흑야회 강령" for b in st2.bible.entries)
    ok &= len(st2.worldgen_chat) == 2 and st2.worldgen_chat[0]["role"] == "author"
    print(f"[{'OK' if ok else 'FAIL'}] 월드젠 턴: 적용(엔티티2/관계3·자유타입+상태)·차단2(구조)·영속·provisional/narrative_inferred")
    return ok


if __name__ == "__main__":
    ok = test_worldgen_turn()
    print("\nR3 검증:", "ALL GREEN ✅" if ok else "FAIL ❌")
    sys.exit(0 if ok else 1)
