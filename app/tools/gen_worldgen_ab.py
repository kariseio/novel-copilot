# -*- coding: utf-8 -*-
"""worldgen 관계 수정 적대검증용 A/B 데이터 생성.
기존 저장 프로젝트(옛 프롬프트 산출)에서 (premise, old seed_edges, profiles)를 뽑고,
같은 premise 를 현재(새) 프롬프트로 재생성해 (new seed_edges, profiles)를 만든다.
결과를 tools/reports/wg_ab_data.json 에 덤프 → 적대 LLM-judge 워크플로가 읽는다.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/gen_worldgen_ab.py
"""
from __future__ import annotations
import json, glob, sys
from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_role_provider
from novelcopilot.worldgen.generator import WorldGenerator

# 미래/발전 관계가 옛 프롬프트에서 나왔던 작품들(복수·로맨스·판타지 파티 등)
TARGET_TITLES = ["리스타트", "봄의 결", "잿빛 검", "너의 옆자리"]
OUT = "tools/reports/wg_ab_data.json"


def edges_with_names(world):
    ents = {e.id: e.name for e in world.entities}
    return [{"rel_id": e.rel_id, "src": ents.get(e.src_id, e.src_id),
             "dst": ents.get(e.dst_id, e.dst_id), "eff_from": e.eff_from} for e in world.seed_edges]


def profiles(world):
    return [{"name": e.name, "profile": (e.profile or "")[:600]} for e in world.entities]


def main():
    s = get_settings()
    prov = create_role_provider(s, s.worldgen_model)
    wg = WorldGenerator(prov)
    items = []
    for f in glob.glob("data/projects/*.json"):
        if ".rag." in f:
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        title = d.get("world", {}).get("title", "")
        if not any(t in title for t in TARGET_TITLES):
            continue
        seed = d.get("seed", {})
        premise = seed.get("premise", "") or d.get("world", {}).get("premise", "")
        if not premise:
            continue
        print(f"[old] {title}: {len(d['world'].get('seed_edges', []))} seed_edges", flush=True)
        # old: 저장된 그대로
        ents = {e["id"]: e["name"] for e in d["world"].get("entities", [])}
        old_edges = [{"rel_id": e.get("rel_id"), "src": ents.get(e.get("src_id"), e.get("src_id")),
                      "dst": ents.get(e.get("dst_id"), e.get("dst_id")), "eff_from": e.get("eff_from")}
                     for e in d["world"].get("seed_edges", [])]
        old_profiles = [{"name": e.get("name"), "profile": (e.get("profile") or "")[:600]}
                        for e in d["world"].get("entities", [])]
        # new: 같은 premise 를 현재(새) 프롬프트로 재생성
        from novelcopilot.domain.project import ProjectSeed
        ns = ProjectSeed(title=title, genre=seed.get("genre", ""), tone=seed.get("tone", ""),
                         premise=premise, protagonist_hint=seed.get("protagonist_hint", ""),
                         target_chapters=seed.get("target_chapters", 100))
        try:
            nw = wg.generate(ns)
            new_edges = edges_with_names(nw)
            new_profiles = profiles(nw)
            print(f"[new] {title}: {len(new_edges)} seed_edges", flush=True)
        except Exception as e:
            print(f"[new] {title} ERR {e}", flush=True)
            new_edges, new_profiles = [], []
        items.append({"title": title, "premise": premise,
                      "old": {"seed_edges": old_edges, "profiles": old_profiles},
                      "new": {"seed_edges": new_edges, "profiles": new_profiles}})
    json.dump(items, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n덤프 완료: {OUT} ({len(items)} 작품)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
