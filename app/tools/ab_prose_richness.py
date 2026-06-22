# -*- coding: utf-8 -*-
"""A/B 실측(적대리뷰 교정) — 풍부함 기제가 *메타데이터*가 아니라 *회차 본문(prose)*에 도달하는가.
같은 시드로 OFF(집착·weird off) vs ON 전체 프로젝트 생성→ch1 집필→**중립 루브릭**(생성 어휘 누설 0) 블라인드 심사.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_prose_richness.py
"""
from __future__ import annotations
import sys, tempfile, random
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from tools.ab_obsession_worldgen import SEED


OUT = r"C:\Users\owner\AppData\Local\Temp\sl_compare"


def build_ch1(obsession: bool, weird: bool, label: str) -> str:
    s = get_settings()
    s.world_obsession = obsession
    s.world_weird = weird
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    st, _ = svc.create_project(SEED.model_copy(deep=True))
    print(f"  [{label}] world done (entities {len(st.world.entities)}), 집필 ch1...", flush=True)
    rec = svc.generate_next_chapter(st.id).get("record")
    txt = (rec.text if rec else "") or ""
    Path(OUT).mkdir(parents=True, exist_ok=True)
    Path(OUT, f"ch1_{label}.txt").write_text(txt, encoding="utf-8")
    print(f"  [{label}] ch1 {len(txt)}자 저장", flush=True)
    return txt


def main():
    prov = create_provider(get_settings())
    print("=== 프로즈 A/B (OFF vs ON) — 전체 파이프라인 + ch1, 중립 심사 ===", flush=True)
    off = build_ch1(False, False, "off")
    on = build_ch1(True, True, "on")
    if not off or not on:
        print("생성 실패 — 중단"); return 1

    # 중립 루브릭: 생성 프롬프트 어휘('구체·감각·비자명') 누설 0. 독자 끌림으로 판단.
    print("\n[블라인드 심사 6회 — 중립 루브릭]")
    onw = offw = tie = 0
    for i in range(6):
        swap = random.random() < 0.5
        A, B = (on, off) if swap else (off, on)
        sysj = ("너는 웹소설 독자다. 두 1화 본문(A/B)을 읽고, *독자로서 '다음 화'를 결제하고 싶은 쪽*을 골라라. "
                "기준은 설정·명사의 양이 아니라 — 세계가 살아있게 읽히는가, 몰입되는가, 읽는 맛과 질감이 있는가, 진부하지 않은가. "
                '{"winner":"A|B|tie","reason":"한 줄(왜 그 쪽에 끌렸나)"} JSON만.')
        try:
            d = prov.chat_json([{"role": "system", "content": sysj},
                                {"role": "user", "content": f"[1화 A]\n{A[:4500]}\n\n[1화 B]\n{B[:4500]}"}], temperature=0.3)
            w = d.get("winner", "tie"); on_is = "A" if swap else "B"
            if w == "tie": tie += 1; v = "무"
            elif w == on_is: onw += 1; v = "ON 승"
            else: offw += 1; v = "OFF 승"
            print(f"  #{i+1} {v} (ON={on_is}) — {str(d.get('reason',''))[:90]}")
        except Exception as e:
            print(f"  #{i+1} 실패: {e}")
    print(f"\n=== 프로즈: ON {onw}승 / OFF {offw}승 / 무 {tie} ===")
    print("(ON 우세 → 풍부함이 본문에 도달. 무·OFF → 메타데이터만 풍부=디버그 장식, default 강등 검토)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
