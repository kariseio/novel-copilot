# -*- coding: utf-8 -*-
"""신규 소설 생성 — '월급루팡으로 살고 싶었는데, 용사 파티급 프로젝트에 배정되었습니다' / 주인공 김성태.
실제 데이터 폴더에 생성(웹앱 노출). 실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/gen_salaryman.py
"""
from __future__ import annotations
import sys
from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.domain.project import ProjectSeed

N_CH = 5

SEED = ProjectSeed(
    title="월급루팡으로 살고 싶었는데, 용사 파티급 프로젝트에 배정되었습니다",
    genre="현대 판타지, 직장 코미디",
    tone="가볍고 유쾌한 직장 코미디 — 위트와 만담, 월급루팡의 능청과 통쾌한 사이다",
    premise=("야망 없이 정시 퇴근과 '월급루팡'(최소 노동으로 월급만 챙기기)을 인생 목표로 삼은 평범한 직장인 김성태. "
             "그가 다니는 회사는 사내 프로젝트를 RPG '파티'처럼 편성해 굴리는데, 난이도가 '용사 파티급'으로 분류된 "
             "사내 최고 난도 핵심 프로젝트(에이스만 차출되는 자리)에 김성태가 어쩌다 휘말려 배정된다. "
             "조용히 묻어가려던 그가 본의 아니게 이 괴물 같은 팀에서 살아남아야 하는 직장 코미디."),
    protagonist_hint=("김성태 — 야망 없이 월급루팡(최소 노동·칼퇴)으로 평온하게 살고 싶은 평범한 직장인. "
                      "얼떨결에 용사 파티급 프로젝트에 배정돼 휘말린다."),
    target_chapters=120)


def main():
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    svc = CopilotService(s, repo)
    print(f"라우팅: worldgen={s.worldgen_model} / 설계={s.planning_model} / 집필={s.gen_model}", flush=True)
    st, _ = svc.create_project(SEED.model_copy(deep=True))
    roster = [e.name for e in st.world.entities]
    print(f"세계 '{st.world.title}' 엔티티{len(st.world.entities)} 아크{len(st.world.spine.arcs) if st.world.spine else 0}", flush=True)
    print(f"  인물: {roster}", flush=True)
    print(f"  주인공 '김성태' 포함: {'김성태' in roster}", flush=True)
    for i in range(N_CH):
        try:
            rec = svc.generate_next_chapter(st.id).get("record")
            cur = repo.get(st.id)
            ch = cur.chapters[-1] if cur.chapters else None
            print(f"  ch{i+1} {len(rec.text) if rec else 0}자 [{ch.chapter_function if ch else '?'}] {ch.title if ch else ''}", flush=True)
        except Exception as e:
            print(f"  ch{i+1} ERR {type(e).__name__} {str(e)[:90]}", flush=True)
    print(f"\n완료: id={st.id} '{st.world.title}' — 웹앱 새로고침 시 확인", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
