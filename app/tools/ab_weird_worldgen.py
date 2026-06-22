# -*- coding: utf-8 -*-
"""A/B 실측 — R-3 안티-클리셰 weirding 이 obsession 위에 *추가 가치*를 내는가.
baseline = obsession 세계, ON = obsession 세계 + weird(). 블라인드 심사 5회(위치 랜덤) + 프록시.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_weird_worldgen.py
"""
from __future__ import annotations
import sys, json, gzip, random

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.worldgen.generator import WorldGenerator
from tools.ab_obsession_worldgen import SEED, serialize, proxies


def main():
    prov = create_provider(get_settings())
    gen = WorldGenerator(prov)
    print("=== A/B: R-3 weirding (obsession baseline vs obsession+weird) ===")
    obs = gen.obsession(SEED)
    print("집착:", obs.get("obsession_vector", "(없음)"))
    base = gen.generate(SEED, obs=obs)
    weird = gen.weird(base.model_copy(deep=True), obs)
    s_base, s_weird = serialize(base), serialize(weird)
    open("/tmp/world_base.txt", "w", encoding="utf-8").write(s_base)
    open("/tmp/world_weird.txt", "w", encoding="utf-8").write(s_weird)
    print("\n[프록시] BASE:", proxies(base))
    print("[프록시] WEIRD:", proxies(weird))

    print("\n[블라인드 심사 5회]")
    on = off = tie = 0
    on_s, off_s = [], []
    for i in range(5):
        swap = random.random() < 0.5
        A, B = (s_weird, s_base) if swap else (s_base, s_weird)
        sysj = ("너는 웹소설 편집장이다. 두 세계관 설정(A/B)을 비교해 '어느 쪽이 더 풍부·구체적·덜 제너릭한가'를 판정하라. "
                "제너릭=간판 호명·뻔한 디폴트, 풍부=구체·감각·비자명·집착적 디테일. "
                '{"winner":"A|B|tie","A_score":0~10,"B_score":0~10,"reason":"한 줄"} JSON만.')
        try:
            d = prov.chat_json([{"role": "system", "content": sysj},
                                {"role": "user", "content": f"[세계 A]\n{A}\n\n[세계 B]\n{B}"}], temperature=0.3)
            w = d.get("winner", "tie"); on_is = "A" if swap else "B"
            sa, sb = float(d.get("A_score", 0)), float(d.get("B_score", 0))
            (on_s if swap else off_s).append(sa); (off_s if swap else on_s).append(sb)
            if w == "tie": tie += 1; v = "무"
            elif w == on_is: on += 1; v = "WEIRD 승"
            else: off += 1; v = "BASE 승"
            print(f"  #{i+1} {v} (WEIRD={on_is}) — {str(d.get('reason',''))[:75]}")
        except Exception as e:
            print(f"  #{i+1} 실패: {e}")
    print(f"\n=== WEIRD {on}승 / BASE {off}승 / 무 {tie} ===")
    if on_s and off_s:
        print(f"평균: WEIRD {sum(on_s)/len(on_s):.2f} vs BASE {sum(off_s)/len(off_s):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
