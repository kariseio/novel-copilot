# -*- coding: utf-8 -*-
"""R3 라이브 — 새 프로젝트에 월드빌딩 대화 2턴. AI 응답·제안 적용·그래프 성장 확인.
실행: PYTHONPATH=app PYTHONIOENCODING=utf-8 python tools/live_r3.py
"""
from __future__ import annotations
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.domain.project import ProjectSeed

s = get_settings()
svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
print("[..] 프로젝트 생성")
state, _ = svc.create_project(ProjectSeed(genre="현대 판타지", tone="빠른 전개",
                                          premise="능력각성 시대, 빚을 진 청년이 각성한다", target_chapters=10))
pid = state.id
n0 = len([e for e in state.world.entities])
for msg in ["주인공에게 라이벌을 한 명 만들자. 같은 길드 출신인데 배신했어.",
            "그 라이벌이 속한 적대 조직과, 그 조직의 본거지 도시도 정하자."]:
    print(f"\n[작가] {msg}")
    r = svc.worldgen_turn(pid, msg)
    print(f"[AI] {r['reply'][:160]}")
    for a in r["applied"]:
        print("   ＋", a)
    for b in r["blocked"]:
        print("   ⛔", b.get("reason"))
    for q in r["questions"]:
        print("   ❓", q[:80])

g = svc.ontology_snapshot(pid)["graph"]
print(f"\n[graph] 노드 {len(g['nodes'])}(시작 {n0}) · 엣지 {len(g['edges'])}")
bib = svc.bible_snapshot(pid)
print(f"[bible] 항목 {len(bib['entries'])}")
st = svc.get_project(pid)
print(f"[chat] {len(st.worldgen_chat)}개 턴 기록")
print("\n라이브 R3:", "GREEN ✅" if len(g['nodes']) > n0 else "WARN(성장 없음)")
