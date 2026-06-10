# -*- coding: utf-8 -*-
"""R2 라이브 — 새 프로젝트 생성 시 설정집 자동 생성 + promote→world_rule 확인.
실행: PYTHONPATH=app PYTHONIOENCODING=utf-8 python tools/live_r2.py
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
print("[..] worldgen + spine + bible 생성")
state, _ = svc.create_project(ProjectSeed(
    genre="정통 판타지", tone="장엄하고 비극적",
    premise="망각의 저주에 걸린 검사가 잊힌 신을 찾아 대륙을 횡단한다", target_chapters=12))
pid = state.id
snap = svc.bible_snapshot(pid)
print(f"[OK] 설정집 {len(snap['entries'])}개 (장르템플릿 {len(snap['template'])}섹션)")
for e in snap["entries"]:
    print(f"   [{e['category_label']}] {e['title']} — {e['prose'][:50]}…")

if snap["entries"]:
    n0 = len(state.world.world_rules)
    target = next((e for e in snap["entries"] if e["category"] == "taboo_worldrule"), snap["entries"][0])
    print(f"\n[..] '{target['title']}' 캐논으로 박기")
    r = svc.promote_bible_entry(pid, target["entry_id"])
    st2 = svc.get_project(pid)
    print(f"[OK] promote={r.get('promoted')} world_rules {n0}→{len(st2.world.world_rules)} "
          f"(승급 텍스트: {st2.world.world_rules[-1].text[:50]}…)")
    snap2 = svc.bible_snapshot(pid)
    promoted = [e for e in snap2["entries"] if e["promoted"]]
    print(f"[OK] 캐논 항목 {len(promoted)}개")

print("\n라이브 R2:", "GREEN ✅" if snap["entries"] else "WARN (설정집 생성 0 — LLM 응답 확인)")
