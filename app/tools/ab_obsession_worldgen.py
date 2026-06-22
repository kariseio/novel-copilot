# -*- coding: utf-8 -*-
"""A/B 실측 — Obsession-Vector-first 가 worldgen 을 더 풍부하게 만드는가.
같은 시드로 OFF(현행 단일콜) vs ON(집착 추출→편중 파생) 세계 생성 후, 블라인드 LLM 심사 N회(위치 랜덤) + 결정론 프록시.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_obsession_worldgen.py
"""
from __future__ import annotations
import sys, json, gzip, random

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.worldgen.generator import WorldGenerator

SEED = ProjectSeed(
    title="", genre="현대 판타지", tone="다크하고 비장하지만 통쾌한 성장물",
    premise=("게이트가 현실과 던전을 잇고 헌터가 몬스터를 사냥하는 현대. 헌터는 각성 시 등급(E~S)이 고정되어 더는 강해지지 않는다. "
             "인류 최약체 E급 헌터가 병든 가족을 위해 저급 던전을 전전하다, 죽음의 이중 던전에서 그만이 보는 '시스템'을 각성해 무한 성장하는 유일한 '플레이어'가 된다."),
    protagonist_hint="인류 최약체 E급 헌터, 병든 가족 부양, 죽음의 문턱에서 무한성장 시스템 각성",
    target_chapters=100)


def serialize(w) -> str:
    L = [f"제목: {w.title}", f"시놉시스: {w.synopsis}", "속성축:"]
    for a in w.attributes:
        L.append(f"  - {a.label}({a.kind}) vocab={a.vocab} states={a.states}")
    L.append("인물:")
    for e in w.entities:
        L.append(f"  - {e.name}: {e.profile}")
    L.append("세계규칙:")
    for r in w.world_rules:
        L.append(f"  - {r.text}")
    gc = w.genre_contract
    if gc:
        L.append(f"장르계약: 쾌감={gc.pleasure_engine} | 기대={gc.reader_expectations} | 어휘톤={gc.vocabulary_tone} | 전제자산={gc.premise_asset}")
    return "\n".join(L)


def proxies(w) -> dict:
    s = serialize(w)
    raw = s.encode("utf-8")
    return {"엔티티": len(w.entities), "속성축": len(w.attributes), "세계규칙": len(w.world_rules),
            "프로필총길이": sum(len(e.profile) for e in w.entities), "직렬길이": len(s),
            "gzip비율": round(len(gzip.compress(raw)) / max(1, len(raw)), 3)}   # 높을수록 덜 압축됨=덜 반복적


def main():
    prov = create_provider(get_settings())
    gen = WorldGenerator(prov)
    print("=== A/B: Obsession-Vector worldgen ===\nOFF(현행) 생성...")
    w_off = gen.generate(SEED)
    print("집착 추출 + ON 생성...")
    obs = gen.obsession(SEED)
    print("집착벡터:", obs.get("obsession_vector", "(없음)"))
    print("감각렌즈:", obs.get("sensory_lens", []))
    w_on = gen.generate(SEED, obs=obs)
    s_off, s_on = serialize(w_off), serialize(w_on)
    open("/tmp/world_off.txt", "w", encoding="utf-8").write(s_off)
    open("/tmp/world_on.txt", "w", encoding="utf-8").write(s_on)
    print("\n[결정론 프록시]")
    print("  OFF:", proxies(w_off))
    print("  ON :", proxies(w_on))

    # 블라인드 심사 N회(위치 랜덤 — 어느 쪽이 ON 인지 숨김)
    print("\n[블라인드 심사 5회]")
    on_wins = off_wins = tie = 0
    on_scores, off_scores = [], []
    for i in range(5):
        swap = random.random() < 0.5
        A, B = (s_on, s_off) if swap else (s_off, s_on)   # A 가 ON 이면 swap=True
        sysj = ("너는 웹소설 편집장이다. 두 세계관 설정(A/B)을 비교해 '어느 쪽이 더 풍부·구체적·덜 제너릭한가'를 판정하라. "
                "제너릭=간판 호명(게이트·E~S·시스템창 단어만), 풍부=구체적·감각적·비자명·집착적 디테일. "
                '{"winner":"A|B|tie","A_score":0~10,"B_score":0~10,"reason":"한 줄 근거"} JSON만.')
        try:
            d = prov.chat_json([{"role": "system", "content": sysj},
                                {"role": "user", "content": f"[세계 A]\n{A}\n\n[세계 B]\n{B}"}], temperature=0.3)
            win = d.get("winner", "tie")
            on_is = "A" if swap else "B"
            sa, sb = float(d.get("A_score", 0)), float(d.get("B_score", 0))
            (on_scores if swap else off_scores).append(sa)
            (off_scores if swap else on_scores).append(sb)
            if win == "tie":
                tie += 1; verdict = "무승부"
            elif win == on_is:
                on_wins += 1; verdict = "ON 승"
            else:
                off_wins += 1; verdict = "OFF 승"
            print(f"  #{i+1} {verdict} (ON={on_is}, A={sa} B={sb}) — {str(d.get('reason',''))[:80]}")
        except Exception as e:
            print(f"  #{i+1} 심사 실패: {e}")
    print(f"\n=== 결과: ON {on_wins}승 / OFF {off_wins}승 / 무 {tie} ===")
    if on_scores and off_scores:
        print(f"평균 점수: ON {sum(on_scores)/len(on_scores):.2f} vs OFF {sum(off_scores)/len(off_scores):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
