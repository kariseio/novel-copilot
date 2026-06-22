# -*- coding: utf-8 -*-
"""A/B — '전진강제·반복금지' craft 지시가 페이싱/반복을 개선하나(프로즈 레버 = craft 가설).
같은 plain 세계로 2회차씩 OFF(지시X) vs ON(craft_block 주입). 반복은 *결정론 지표*(객관), 가독성은 중립 심사.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_pacing_richness.py
"""
from __future__ import annotations
import sys, tempfile, random, gzip
from collections import Counter
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from tools.ab_obsession_worldgen import SEED

OUT = r"C:\Users\owner\AppData\Local\Temp\sl_compare"
CRAFT = ("\n\n[전개·반복 금지 강제] 이 회차는 직전 장면·상황을 반복하거나 심화하지 말고 새 국면으로 전진시켜라 — "
         "장소·관계·정보·판세 중 최소 하나가 회차 안에서 실제로 바뀌어야 한다. 같은 위협·대치·감정·이미지를 두 번 그리지 말고, "
         "같은 모티프(같은 적·같은 묘사·같은 회상 대사)를 반복하지 마라. 한 장면을 길게 늘여 분량을 채우지 말고 사건이 전진해 자연히 길어지게 하라.")


def rep_metric(text: str) -> dict:
    raw = text.encode("utf-8")
    gz = round(len(gzip.compress(raw)) / max(1, len(raw)), 3)   # 낮을수록 반복적
    n = 16
    g = Counter(text[i:i + n] for i in range(max(0, len(text) - n)))
    rep = sum(1 for _, c in g.items() if c >= 2)                 # 16자 모티프 ≥2회 반복 수(낮을수록 좋음)
    return {"gzip": gz, "반복16gram": rep, "길이": len(text)}


def two_chapters(svc, pid, craft: str) -> str:
    sess, _ = svc.get_session(pid)
    sess.bundle.generator.craft_block = craft
    out = []
    for _ in range(2):
        rec = svc.generate_next_chapter(pid).get("record")
        out.append((rec.text if rec else "") or "")
    return "\n\n".join(out)


def main():
    prov = create_provider(get_settings())
    s = get_settings(); s.world_obsession = False; s.world_weird = False
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    print("plain 세계 + 프로젝트 생성...(느림)", flush=True)
    st, _ = svc.create_project(SEED.model_copy(deep=True))
    st2 = st.model_copy(deep=True); st2.id = st.id + "on"; svc.repo.save(st2)

    print("[off] 2회차 집필...", flush=True)
    off = two_chapters(svc, st.id, "")
    print(f"  off {len(off)}자", flush=True)
    print("[on] 2회차 집필(craft 주입)...", flush=True)
    on = two_chapters(svc, st2.id, CRAFT)
    print(f"  on {len(on)}자", flush=True)
    Path(OUT).mkdir(parents=True, exist_ok=True)
    Path(OUT, "pacing_off.txt").write_text(off, encoding="utf-8")
    Path(OUT, "pacing_on.txt").write_text(on, encoding="utf-8")
    if not off or not on:
        print("생성 실패"); return 1

    print("\n[결정론 반복 지표(낮을수록 덜 반복)]")
    print("  OFF:", rep_metric(off))
    print("  ON :", rep_metric(on))

    print("\n[중립 가독성 심사 6회]", flush=True)
    onw = offw = tie = 0
    for i in range(6):
        swap = random.random() < 0.5
        A, B = (on, off) if swap else (off, on)
        sysj = ("너는 웹소설 독자다. 두 연속 2화 본문(A/B)을 읽고 *독자로서 더 잘 읽히고 다음 화를 보고 싶은 쪽*을 골라라. "
                "기준: 이야기가 잘 굴러가는가, 지루하지 않은가, 읽는 맛. "
                '{"winner":"A|B|tie","reason":"한 줄"} JSON만.')
        try:
            d = prov.chat_json([{"role": "system", "content": sysj},
                                {"role": "user", "content": f"[A]\n{A[:6000]}\n\n[B]\n{B[:6000]}"}], temperature=0.3)
            w = d.get("winner", "tie"); on_is = "A" if swap else "B"
            if w == "tie": tie += 1; v = "무"
            elif w == on_is: onw += 1; v = "ON(craft) 승"
            else: offw += 1; v = "OFF 승"
            print(f"  #{i+1} {v} (ON={on_is}) — {str(d.get('reason',''))[:85]}", flush=True)
        except Exception as e:
            print(f"  #{i+1} 실패: {e}", flush=True)
    print(f"\n=== 가독성: ON {onw} / OFF {offw} / 무 {tie} | 반복지표는 위 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
