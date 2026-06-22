# -*- coding: utf-8 -*-
"""A/B 다리 검증 — 집착/감각렌즈를 *회차 집필 프롬프트*에 직접 주입하면 프로즈가 풍부해지나(메타↔프로즈 다리).
같은(plain) 세계로 ch1 OFF(주입 없음) vs ON(generator.obsession_block 주입)만 변주 — 세계 고정·다리만 격리.
중립 루브릭 블라인드 심사. 실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_bridge_richness.py
"""
from __future__ import annotations
import sys, tempfile, random
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.worldgen.generator import WorldGenerator
from tools.ab_obsession_worldgen import SEED

OUT = r"C:\Users\owner\AppData\Local\Temp\sl_compare"


def main():
    prov = create_provider(get_settings())
    s = get_settings()
    s.world_obsession = False
    s.world_weird = False   # plain 세계(프로덕션 default) — 다리만 격리
    # 주입용 집착(시드에서 1회 추출)
    obs = WorldGenerator(prov).obsession(SEED)
    print("집착:", obs.get("obsession_vector", "(없음)"))
    block = ("\n\n[이 회차를 쓰는 렌즈 — 작품의 주제적 집착]\n"
             f"{obs.get('obsession_vector','')}\n"
             f"이 집착이 드러나는 구체물(가능하면 장면 속 사물·디테일로 한 번 이상 박아라): {obs.get('sensory_lens', [])}\n"
             "장면의 감정·갈등·배경을 이 집착의 그림자로 *구체화*하라 — 추상·일반론 대신 위 구체물과 감각으로.")

    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    print("plain 세계 + 프로젝트 생성...(느림)", flush=True)
    st, _ = svc.create_project(SEED.model_copy(deep=True))
    st2 = st.model_copy(deep=True); st2.id = st.id + "on"
    svc.repo.save(st2)   # 같은 세계 복제 → ON 아크에서 ch1 재생성

    print("[off] ch1 집필...", flush=True)
    off = (svc.generate_next_chapter(st.id).get("record").text or "")
    print(f"  off {len(off)}자", flush=True)
    sess_on, _ = svc.get_session(st2.id)
    sess_on.bundle.generator.obsession_block = block   # 다리 주입(ON)
    print("[on] ch1 집필(집착 주입)...", flush=True)
    on = (svc.generate_next_chapter(st2.id).get("record").text or "")
    print(f"  on {len(on)}자", flush=True)
    Path(OUT).mkdir(parents=True, exist_ok=True)
    Path(OUT, "ch1_bridge_off.txt").write_text(off, encoding="utf-8")
    Path(OUT, "ch1_bridge_on.txt").write_text(on, encoding="utf-8")
    if not off or not on:
        print("생성 실패"); return 1

    print("\n[블라인드 심사 6회 — 중립 루브릭]", flush=True)
    onw = offw = tie = 0
    for i in range(6):
        swap = random.random() < 0.5
        A, B = (on, off) if swap else (off, on)
        sysj = ("너는 웹소설 독자다. 두 1화 본문(A/B)을 읽고 *독자로서 '다음 화'를 결제하고 싶은 쪽*을 골라라. "
                "기준은 설정·명사의 양이 아니라 — 세계가 살아있게 읽히는가, 몰입되는가, 읽는 맛과 질감이 있는가, 진부하지 않은가. "
                '{"winner":"A|B|tie","reason":"한 줄"} JSON만.')
        try:
            d = prov.chat_json([{"role": "system", "content": sysj},
                                {"role": "user", "content": f"[1화 A]\n{A[:4500]}\n\n[1화 B]\n{B[:4500]}"}], temperature=0.3)
            w = d.get("winner", "tie"); on_is = "A" if swap else "B"
            if w == "tie": tie += 1; v = "무"
            elif w == on_is: onw += 1; v = "ON(주입) 승"
            else: offw += 1; v = "OFF 승"
            print(f"  #{i+1} {v} (ON={on_is}) — {str(d.get('reason',''))[:90]}", flush=True)
        except Exception as e:
            print(f"  #{i+1} 실패: {e}", flush=True)
    print(f"\n=== 다리: ON(주입) {onw}승 / OFF {offw}승 / 무 {tie} ===")
    print("(ON 우세 → 집착을 회차 프롬프트에 주입하면 프로즈에 도달=다리 성립. 무 → 또 다른 레버)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
