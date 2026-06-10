# -*- coding: utf-8 -*-
"""R4 라이브 — 새 프로젝트(spine 생성) + 에피소드 경계까지 생성. 커서 전진·롤업·드리프트 확인.
실행: PYTHONPATH=app PYTHONIOENCODING=utf-8 python tools/live_r4.py
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
print("[..] worldgen + spine 설계")
state, _ = svc.create_project(ProjectSeed(
    genre="현대 판타지", tone="빠른 전개",
    premise="죽은 자의 기억을 읽는 형사가 자신의 죽음을 목격한다", target_chapters=12))
pid = state.id
sp = state.world.spine
if not sp:
    print("[FAIL] spine 미생성"); raise SystemExit(1)
print(f"[OK] 엔딩 고정: {sp.ending.ending[:80]}")
print(f"[OK] 아크 {len(sp.arcs)}개:")
for a in sp.arcs:
    print(f"   - {a.title} / 목표:{a.goal[:40]} / 에피소드 {len(a.episodes)}")
ep1 = sp.arcs[0].episodes[0]
n = min(ep1.target_chapters, 4)
print(f"\n[..] ep1 '{ep1.title}'(목표 {ep1.target_chapters}화) 경계까지 {n}회차 생성")
for i in range(1, n + 1):
    res = svc.generate_next_chapter(pid)
    r = res["record"]
    print(f"  ch{i} {r.status.value} ep={r.episode_id} finale={'Y' if r.episode_id and getattr(r,'drift_signals',None) is not None and r.episode_id else ''} "
          f"chars={len(r.text)} drift={r.drift_signals}")

snap = svc.spine_snapshot(pid)
done_eps = [e for a in snap["arcs"] for e in a["episodes"] if e["done"]]
print(f"\n[spine] 현재 에피소드={snap['current_episode_id']} chapters_in_episode={snap['chapters_in_episode']} 완료EP={len(done_eps)}")
for e in done_eps:
    print(f"   완료 '{e['title']}' 롤업요약: {(e['summary'] or '(없음)')[:120]}")
st = svc.get_project(pid)
from novelcopilot.services.copilot import _build_story_so_far_hier
txt, dropped = _build_story_so_far_hier(st, st.current_chapter + 1, s.story_so_far_chars)
print(f"\n[계층 story_so_far] len={len(txt)} dropped={dropped}\n{txt[:300]}")
print("\n라이브 R4:", "GREEN ✅" if (sp.arcs and st.current_chapter >= 1) else "WARN")
