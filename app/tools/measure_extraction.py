# -*- coding: utf-8 -*-
"""Stage 0 측정(§3.3) — ontology_updater.propose 를 기존 실제 프로젝트 회차에 돌려 추출 품질 관찰.

라벨 없는 정성 측정(작가 교정 부담 추정용). 신규인물 오탐/state_change 오추출을 눈으로 검증.
실행: PYTHONPATH=app python tools/measure_extraction.py [project_id]
"""
from __future__ import annotations
import sys

from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService


def main(pid: str | None):
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    svc = CopilotService(s, repo)
    if not pid:
        lst = svc.list_projects()
        if not lst:
            print("프로젝트 없음"); return
        pid = lst[0]["id"]
    print(f"[project] {pid}")
    sess, state = svc.get_session(pid)
    if not sess:
        print("로드 실패"); return
    roster = {e.id: e.name for e in sess.bundle.ontology.entities.values() if e.etype == "character"}
    print(f"[roster] 기존 인물 {len(roster)}: {list(roster.values())}")
    total_new, total_chg = 0, 0
    for ch in state.chapters:
        prop = sess.bundle.updater.propose(ch.text, sess.bundle.ontology, ch.chapter)
        nc = prop.get("new_characters", []) or []
        sc = prop.get("state_changes", []) or []
        total_new += len(nc); total_chg += len(sc)
        print(f"\n--- {ch.chapter}화 '{ch.title}' (chars={len(ch.text)}) ---")
        print(f"  new_characters({len(nc)}): " +
              ", ".join(f"{c.get('name')}[{c.get('role','')}]" for c in nc) or "  (없음)")
        for c in sc:
            cur = sess.bundle.ontology.state_as_of(c.get("id"), c.get("attr"), ch.chapter)
            print(f"  state_change: {roster.get(c.get('id'), c.get('id'))}.{c.get('attr')} "
                  f"{cur} -> {c.get('value')}  근거='{c.get('note','')}'")
    print(f"\n[요약] {len(state.chapters)}개 회차 | 제안 신규인물 {total_new}, 상태변화 {total_chg} "
          f"(회차당 신규 {total_new/max(1,len(state.chapters)):.1f}, 변화 {total_chg/max(1,len(state.chapters)):.1f})")
    print("→ 작가가 이 제안들을 보고 '오탐'을 세면 회차당 교정 건수가 나온다(임계 >3 → 자동추출 출시 제외).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
