# -*- coding: utf-8 -*-
"""타임아웃 등으로 5화 미달인 장르 프로젝트를 5화까지 보충(새 300초 타임아웃 적용).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/topup_genres.py
"""
from __future__ import annotations
import sys
from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from tools.ab_genres import GENRES

TARGET = 5


def main():
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    svc = CopilotService(s, repo)
    mine = {g.genre for g in GENRES}
    todo = [x for x in repo.list_summaries() if x['genre'] in mine and x['current_chapter'] < TARGET]
    print(f"보충 대상 {len(todo)}개 / 본문={s.gen_model}\n", flush=True)
    for x in todo:
        pid = x['id']
        cur = x['current_chapter']
        print(f"[{x['genre']}] {x['title']} {cur}/{TARGET}", flush=True)
        while cur < TARGET:
            try:
                rec = svc.generate_next_chapter(pid).get('record')
                cur = repo.get(pid).current_chapter
                print(f"   → {cur}화 {len(rec.text) if rec else 0}자", flush=True)
            except Exception as e:
                print(f"   ERR {type(e).__name__} {str(e)[:80]}", flush=True)
                break
    print("\n보충 완료:", [(x['genre'], repo.get(x['id']).current_chapter) for x in todo])
    return 0


if __name__ == "__main__":
    sys.exit(main())
