# -*- coding: utf-8 -*-
"""A/B — 비-프로즈 LLM 역할별 모델 판단. worldgen(세계관)·arc-plan(연재설계).
각 역할을 같은 입력으로 4모델 변주 → 객관 구조메트릭 + 블라인드 pairwise 3-family 판정단(양순서 일치만 승).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_roles.py
"""
from __future__ import annotations
import sys, itertools
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.openai_provider import OpenAIProvider
from novelcopilot.llm.anthropic_provider import AnthropicProvider
from novelcopilot.llm.gemini_provider import GeminiProvider
from novelcopilot.worldgen.generator import WorldGenerator
from novelcopilot.worldgen.arc_planner import ArcPlanner
from tools.ab_obsession_worldgen import SEED

OUT = Path(r"C:\Users\owner\AppData\Local\Temp\sl_compare")
_emb = OpenAIProvider("gpt-4.1", "text-embedding-3-small")


def prov(prov_name, model):
    if prov_name == "openai":
        return OpenAIProvider(model, "text-embedding-3-small")
    if prov_name == "anthropic":
        return AnthropicProvider(model, _emb)
    return GeminiProvider(model, _emb)


ARMS = {"gpt5.2-chat": ("openai", "gpt-5.2-chat-latest"),
        "gpt5.2": ("openai", "gpt-5.2"),
        "claude": ("anthropic", "claude-opus-4-8"),
        "gemini": ("gemini", "gemini-3.1-pro-preview")}

JUDGES_SPEC = [("J-gpt", "openai", "gpt-4.1"),
               ("J-claude", "anthropic", "claude-sonnet-4-6"),
               ("J-gemini", "gemini", "gemini-2.5-flash")]


def world_metrics(w):
    return dict(ent=len(w.entities), rel=len(w.relations), edges=len(w.seed_edges),
                rules=len(w.world_rules), attrs=len(w.attributes), beats=len(w.beats),
                wiki=len(w.wiki_seeds), synop=len(w.synopsis or ""),
                profc=sum(len(e.profile or "") for e in w.entities))


def world_brief(w):
    L = [f"제목:{w.title} / 장르:{w.genre} / 톤:{w.tone}", f"전제:{w.premise}",
         f"시놉시스:{(w.synopsis or '')[:600]}", f"집착벡터:{w.obsession_vector}", f"엔티티({len(w.entities)}):"]
    for e in w.entities[:12]:
        L.append(f"  - {e.name}({e.etype}): {(e.profile or '')[:130]}")
    L.append(f"세계규칙({len(w.world_rules)}):")
    for r in w.world_rules[:8]:
        L.append(f"  - {(r.text or '')[:130]}")
    L.append(f"[관계타입 {len(w.relations)} / seed엣지 {len(w.seed_edges)} / 속성 {len(w.attributes)} / 비트 {len(w.beats)} / 위키seed {len(w.wiki_seeds)}]")
    return "\n".join(L)


def spine_metrics(s):
    eps = [ep for a in s.arcs for ep in a.episodes]
    e = s.ending
    return dict(arcs=len(s.arcs), eps=len(eps),
                ending=bool(e and e.central_question and e.ending and e.thematic_payoff),
                plants=sum(len(ep.plants) for ep in eps), payoffs=sum(len(ep.payoffs) for ep in eps),
                reqev=sum(len(ep.required_events) for ep in eps))


def spine_brief(s):
    L = []
    if s.ending:
        L.append(f"중심질문:{s.ending.central_question}\n결말:{s.ending.ending}\n주제적페이오프:{s.ending.thematic_payoff}")
    L.append(f"아크({len(s.arcs)}):")
    for a in s.arcs:
        L.append(f"  [{a.order}] {a.title} — 목표:{(a.goal or '')[:80]} / 전환점:{(a.turning_point or '')[:80]} / 에피{len(a.episodes)}")
        for ep in a.episodes[:3]:
            L.append(f"      · {ep.title}: 절정={(ep.climax or '')[:60]} plants={len(ep.plants)} payoffs={len(ep.payoffs)}")
    return "\n".join(L)


def panel(briefs, what):
    """briefs: {arm:text}. pairwise, 3-family 판정단, 양순서 일치만 승."""
    judges = []
    for jn, pn, pm in JUDGES_SPEC:
        try:
            judges.append((jn, prov(pn, pm)))
        except Exception as e:
            print(f"  judge {jn} skip {e}")
    avail = [a for a in briefs if briefs[a]]
    wins = {a: 0 for a in avail}
    per = {jn: {a: 0 for a in avail} for jn, _ in judges}
    SYS = (f'너는 까다로운 한국 웹소설 편집자다. 두 {what} 중 어느 쪽이 더 우수한지 고른다. '
           '{"winner":"A" 또는 "B","reason":"한 줄"} JSON만 출력.')
    for x, y in itertools.combinations(avail, 2):
        for jn, jp in judges:
            vs = []
            for a, b in [(x, y), (y, x)]:
                body = f"[{what} A]\n{briefs[a][:4200]}\n\n[{what} B]\n{briefs[b][:4200]}"
                try:
                    d = jp.chat_json([{"role": "system", "content": SYS}, {"role": "user", "content": body}], temperature=0.2)
                    w = d.get("winner")
                    vs.append(a if w == "A" else (b if w == "B" else None))
                except Exception as e:
                    vs.append(None); print(f"    {jn} {a}v{b} 실패 {str(e)[:70]}", flush=True)
            if len(vs) == 2 and vs[0] and vs[0] == vs[1]:
                wins[vs[0]] += 1; per[jn][vs[0]] += 1
                print(f"  {x} vs {y} | {jn}: {vs[0]}", flush=True)
            else:
                print(f"  {x} vs {y} | {jn}: 무/불일치 {vs}", flush=True)
    print(f"  >>> 양순서일치 승: {wins}")
    for jn in per:
        print(f"      {jn}: {per[jn]}")
    return wins


def main():
    get_settings()
    OUT.mkdir(parents=True, exist_ok=True)

    # ===== 역할1: worldgen =====
    print("=== 역할1: worldgen (세계관 생성) ===", flush=True)
    worlds, wbriefs = {}, {}
    for name, (pn, pm) in ARMS.items():
        print(f"[{name}] 세계 생성...", flush=True)
        try:
            w = WorldGenerator(prov(pn, pm)).generate(SEED.model_copy(deep=True))
            worlds[name] = w; wbriefs[name] = world_brief(w)
            (OUT / f"world_{name}.json").write_text(w.model_dump_json(indent=1), encoding="utf-8")
            print("  ", {k: v for k, v in world_metrics(w).items()}, flush=True)
        except Exception as e:
            print(f"  ERR {name}: {type(e).__name__} {str(e)[:150]}", flush=True); wbriefs[name] = ""

    print("\n[worldgen 객관 구조메트릭]")
    print(f"  {'arm':12}{'ent':>4}{'rel':>4}{'edge':>5}{'rule':>5}{'attr':>5}{'beat':>5}{'wiki':>5}{'synop':>7}{'profc':>7}")
    for name in ARMS:
        w = worlds.get(name)
        if not w:
            print(f"  {name:12}(실패)"); continue
        m = world_metrics(w)
        print(f"  {name:12}{m['ent']:>4}{m['rel']:>4}{m['edges']:>5}{m['rules']:>5}{m['attrs']:>5}{m['beats']:>5}{m['wiki']:>5}{m['synop']:>7}{m['profc']:>7}")
    print("\n[worldgen 블라인드 판정단 — '더 구체적·집필재료 많고 일관된 세계관']", flush=True)
    panel(wbriefs, "웹소설 세계관 설정")

    # ===== 역할2: arc-plan (같은 세계 고정 → 모델만 변주) =====
    fixed = worlds.get("gpt5.2-chat") or next(iter(worlds.values()), None)
    if fixed:
        print(f"\n=== 역할2: arc-plan (고정세계={fixed.title}, 모델만 변주) ===", flush=True)
        sbriefs, smets = {}, {}
        for name, (pn, pm) in ARMS.items():
            print(f"[{name}] 스파인 설계...", flush=True)
            try:
                sp = ArcPlanner(prov(pn, pm)).build_spine(fixed, target_chapters=30)
                sbriefs[name] = spine_brief(sp); smets[name] = spine_metrics(sp)
                print("  ", smets[name], flush=True)
            except Exception as e:
                print(f"  ERR {name}: {type(e).__name__} {str(e)[:150]}", flush=True); sbriefs[name] = ""
        print("\n[arc-plan 객관 메트릭]")
        print(f"  {'arm':12}{'arcs':>5}{'eps':>5}{'ending':>7}{'plants':>7}{'payoffs':>8}{'reqev':>6}")
        for name in ARMS:
            if name not in smets:
                print(f"  {name:12}(실패)"); continue
            m = smets[name]
            print(f"  {name:12}{m['arcs']:>5}{m['eps']:>5}{str(m['ending']):>7}{m['plants']:>7}{m['payoffs']:>8}{m['reqev']:>6}")
        print("\n[arc-plan 블라인드 판정단 — '결말수렴 빌드업·페이오프 구조·연재동력']", flush=True)
        panel(sbriefs, "웹소설 연재 아크 설계")
    return 0


if __name__ == "__main__":
    sys.exit(main())
