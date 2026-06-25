# -*- coding: utf-8 -*-
"""격리 검증 — 장르맹목이 프롬프트 탓인가 모델(gpt-5.5) 탓인가.
같은 학원물 세계로 [gpt-5.5 + 고친 프롬프트] vs [claude + 고친 프롬프트] spine 비교."""
from __future__ import annotations
import sys
from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_role_provider
from novelcopilot.llm.openai_provider import OpenAIProvider
from novelcopilot.worldgen.generator import WorldGenerator
from novelcopilot.worldgen.arc_planner import ArcPlanner
from tools.ab_genres import GENRES

BATTLE = ["회귀", "시스템", "각성", "절대권능", "최종결전", "결전", "적", "세력", "반격", "흑막", "몬스터", "던전", "세계정복", "마교", "내공"]


def scan(sp):
    txt = ((sp.ending.central_question if sp.ending else "") + (sp.ending.ending if sp.ending else "") +
           " ".join((a.title or "") + (a.goal or "") for a in sp.arcs))
    return [w for w in BATTLE if w in txt]


def main():
    s = get_settings()
    seed = GENRES[1]   # 학원물(잔잔한 일상)
    wg = create_role_provider(s, s.worldgen_model)   # claude worldgen
    print("학원물 세계 생성(claude)...", flush=True)
    world = WorldGenerator(wg).generate(seed.model_copy(deep=True))
    print(f"세계: {world.title} / {world.genre}\n", flush=True)

    for label, prov in [("gpt-5.5+고친프롬프트", OpenAIProvider("gpt-5.5", "text-embedding-3-small")),
                        ("claude+고친프롬프트", create_role_provider(s, "anthropic:claude-opus-4-8"))]:
        try:
            sp = ArcPlanner(prov).build_spine(world, 30)
            en = sp.ending
            hits = scan(sp)
            print(f"=== {label} ===", flush=True)
            print(" 중심질문:", (en.central_question or "")[:150] if en else "(없음)")
            for a in sp.arcs[:3]:
                print(f"  [{a.order}] {a.title} | {(a.goal or '')[:85]}")
            print(f" >>> 배틀/파워판타지 단어 출현: {hits or '없음 ✅'}\n", flush=True)
        except Exception as e:
            print(f"=== {label} 실패: {e}\n", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
