# -*- coding: utf-8 -*-
"""실사용자 가정 — 유명 장르별 실제 프로젝트를 *웹앱 데이터 폴더*(resolved_data_dir)에 생성하고 5화씩 집필.
임시폴더 아님 → 웹앱(localhost:8021) 프로젝트 목록에 그대로 나타나 읽고 이어쓸 수 있음.
현행 라우팅(worldgen=claude / 설계·본문=gpt-5.5) 사용. 실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/gen_genres.py
"""
from __future__ import annotations
import sys
from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from tools.ab_genres import GENRES   # 5장르 시드(정통판타지·잔잔학원물·현대로맨스·정통무협·회귀물)

N_CH = 5


def main():
    # 인자로 장르 인덱스 1개 지정 시 그것만(병렬 프로세스용). 없으면 전체(순차).
    sel = GENRES
    if len(sys.argv) > 1:
        i = int(sys.argv[1])
        sel = [GENRES[i]]
    s0 = get_settings()
    data_dir = s0.resolved_data_dir()
    repo = FilesystemProjectRepository(data_dir)
    svc = CopilotService(s0, repo)
    print(f"데이터 폴더(웹앱과 동일): {data_dir}", flush=True)
    print(f"라우팅: worldgen={s0.worldgen_model} / 설계={s0.planning_model} / 본문={s0.gen_model}\n", flush=True)

    made = []
    for seed in sel:
        g = seed.genre
        print(f"=== [{g}] 실제 프로젝트 생성 + {N_CH}화 ===", flush=True)
        try:
            st, _ = svc.create_project(seed.model_copy(deep=True))
            print(f"  생성됨 id={st.id} title='{st.world.title}' 엔티티{len(st.world.entities)} 아크{len(st.world.spine.arcs) if st.world.spine else 0}", flush=True)
            ok = 0
            for i in range(N_CH):
                try:
                    rec = svc.generate_next_chapter(st.id).get("record")
                    n = len(rec.text) if rec else 0
                    ok += 1 if n >= 800 else 0
                    print(f"   ch{i+1} {n}자", flush=True)
                except Exception as e:
                    print(f"   ch{i+1} ERR {type(e).__name__} {str(e)[:100]}", flush=True)
            made.append((g, st.id, st.world.title, ok))
        except Exception as e:
            print(f"  [{g}] 생성 실패: {type(e).__name__} {str(e)[:140]}", flush=True)
            made.append((g, "-", "(실패)", 0))

    print("\n========== 웹앱에서 확인 가능한 프로젝트 ==========")
    for g, pid, title, ok in made:
        print(f"  [{g}] {title}  (id={pid}, {ok}/{N_CH}화 정상)")
    print("\n→ 웹앱(localhost:8021) 새로고침 시 위 프로젝트가 목록에 나타납니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
