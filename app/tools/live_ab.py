# -*- coding: utf-8 -*-
"""A+B 라이브 검증 — worldgen + 2회차 실제 생성. 요약 생성·주입·관계/신규엔티티 추출 확인.
실행: PYTHONPATH=app PYTHONIOENCODING=utf-8 python tools/live_ab.py
"""
from __future__ import annotations
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.services.copilot import _build_story_so_far
from novelcopilot.domain.project import ProjectSeed

s = get_settings()
svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
print("[..] worldgen")
state, _ = svc.create_project(ProjectSeed(
    genre="현대 판타지", tone="빠른 전개",
    premise="죽은 자의 기억을 읽는 형사가 자신의 죽음을 목격한다", target_chapters=4))
pid = state.id
print(f"[OK] world '{state.world.title}' / 인물 {len([e for e in state.world.entities])}")

for n in (1, 2):
    res = svc.generate_next_chapter(pid)
    r = res["record"]
    print(f"\n[ch{n}] {r.status.value} chars={len(r.text)} summary_len={len(r.summary)} "
          f"onto_changes={len(r.ontology_changes)}")
    print("   summary:", (r.summary or "(없음)")[:180])
    for c in r.ontology_changes[:6]:
        print(f"    - {c.op} | {c.entity} | {c.detail[:50]} | applied={c.applied}")

st = svc.get_project(pid)
sof = _build_story_so_far([c for c in st.chapters if c.chapter < 2], s.story_so_far_chars)
print(f"\n[story_so_far→ch2] len={len(sof)} :: {sof[:160]}")
g = svc.ontology_snapshot(pid)["graph"]
inferred = [e for e in g["edges"] if e["trust_tier"] == "narrative_inferred"]
new_nodes = [n for n in g["nodes"] if n["provisional"]]
print(f"[graph] nodes={len(g['nodes'])}(신규 {len(new_nodes)}) edges={len(g['edges'])}(추정 {len(inferred)})")

c1, c2 = st.chapter(1), st.chapter(2)
verdict = bool(c1 and c1.summary and c2 and c2.summary and sof)
print("\n라이브 A+B:", "GREEN ✅ (요약 생성·누적 주입 동작)" if verdict else "WARN ❌ (요약/주입 확인 실패)")
