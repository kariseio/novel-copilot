# -*- coding: utf-8 -*-
"""기존 좀비 프로젝트의 시드를 살려 발단 로직으로 재생성(웹앱 데이터 폴더). 실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/regen_zombie.py"""
from __future__ import annotations
import sys
from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService

N_CH = 5


def main():
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    svc = CopilotService(s, repo)
    z = [x for x in repo.list_summaries() if '좀비' in x['title']]
    if not z:
        print("좀비 프로젝트 없음"); return 1
    old = repo.get(z[0]['id'])
    seed = old.seed
    print(f"시드 살림: {seed.genre} / {seed.premise[:90]}", flush=True)
    repo.delete(z[0]['id'])
    print("기존 좀비 삭제 → 발단 로직으로 재생성", flush=True)
    print(f"라우팅: worldgen={s.worldgen_model} / 설계={s.planning_model} / 본문={s.gen_model}\n", flush=True)
    st, _ = svc.create_project(seed.model_copy(deep=True))
    print(f"새 세계: '{st.world.title}' 엔티티{len(st.world.entities)} 아크{len(st.world.spine.arcs) if st.world.spine else 0}", flush=True)
    for i in range(N_CH):
        try:
            rec = svc.generate_next_chapter(st.id).get('record')
            cur = repo.get(st.id)
            ch = cur.chapters[-1] if cur.chapters else None
            fn = ch.chapter_function if ch else '?'
            print(f"  ch{i+1} {len(rec.text) if rec else 0}자 [{fn}]", flush=True)
        except Exception as e:
            print(f"  ch{i+1} ERR {type(e).__name__} {str(e)[:80]}", flush=True)
    print(f"\n완료: id={st.id} '{st.world.title}' — 웹앱 새로고침 시 확인", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
