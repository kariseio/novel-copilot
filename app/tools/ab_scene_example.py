# -*- coding: utf-8 -*-
"""A/B — 장면형 앵커 업그레이드: 짧은 *지시*(scene) vs few-shot *예시*(example) vs 무처리(none).
가설(연구 #1 레버): 예시는 분포를 모방시켜 지시의 잔여 과적용을 피한다. 단 내용복사(자기표절) 위험 → leak 체크.
같은 plain 세계 3arm 2회차씩. 3-way 랭킹(중립) + ai_tell + 예시 누출 검사.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_scene_example.py
"""
from __future__ import annotations
import sys, tempfile, random
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.quality_gates import ai_tell_profile
from novelcopilot.engine import harness as H
from tools.ab_obsession_worldgen import SEED

OUT = r"C:\Users\owner\AppData\Local\Temp\sl_compare"
ARMS = ["none", "scene", "example"]


def two_ch(svc, pid, mode):
    sess, _ = svc.get_session(pid)
    sess.bundle.generator.craft_block = ""
    sess.bundle.generator.style_mode = mode
    out = []
    for _ in range(2):
        rec = svc.generate_next_chapter(pid).get("record")
        out.append((rec.text if rec else "") or "")
    return "\n\n".join(out)


def leak(text: str) -> int:
    # 예시 문장 조각(8자+)이 본문에 그대로 들어갔나(자기표절)
    frags = []
    for ex in H._SCENE_EXAMPLES.values():
        for ln in ex.replace("“", "").replace("”", "").split("\n"):
            ln = ln.strip()
            if len(ln) >= 8:
                frags.append(ln)
    return sum(1 for f in frags if f in text)


def main():
    prov = create_provider(get_settings())
    s = get_settings(); s.world_obsession = False; s.world_weird = False
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    print("plain 세계 생성...(느림)", flush=True)
    st, _ = svc.create_project(SEED.model_copy(deep=True))
    roster = {e.name for e in st.world.entities}
    ids = {}
    for m in ARMS:
        c = st.model_copy(deep=True); c.id = st.id + m; svc.repo.save(c); ids[m] = c.id

    texts = {}
    for m in ARMS:
        print(f"[{m}] 2회차 집필...", flush=True)
        texts[m] = two_ch(svc, ids[m], m)
        Path(OUT).mkdir(parents=True, exist_ok=True)
        Path(OUT, f"ex_{m}.txt").write_text(texts[m], encoding="utf-8")
        print(f"  {m} {len(texts[m])}자 | 예시누출 {leak(texts[m])}건", flush=True)

    print("\n[ai_tell 분포]")
    for m in ARMS:
        p = ai_tell_profile(texts[m], roster)
        print(f"  {m:8}:", {k: round(v, 2) if isinstance(v, float) else v for k, v in p.items()})

    print("\n[3-way 중립 랭킹 6회]", flush=True)
    first = {m: 0 for m in ARMS}
    for i in range(6):
        order = ARMS[:]; random.shuffle(order)
        labels = ["A", "B", "C"]
        body = "\n\n".join(f"[본문 {labels[j]}]\n{texts[order[j]][:4200]}" for j in range(3))
        sysj = ("너는 웹소설 독자다. 세 본문(A/B/C)을 *더 잘 읽히고 문체가 자연스러운(억지로 힘주지 않은)* 순으로 순위를 매겨라. "
                '{"ranking":["A|B|C 1등","2등","3등"],"reason":"한 줄"} JSON만.')
        try:
            d = prov.chat_json([{"role": "system", "content": sysj},
                                {"role": "user", "content": body}], temperature=0.3)
            rk = d.get("ranking") or []
            if rk:
                winner_label = rk[0]
                idx = labels.index(winner_label) if winner_label in labels else 0
                first[order[idx]] += 1
                print(f"  #{i+1} 1등={order[idx]} (랭킹 {rk}) — {str(d.get('reason',''))[:70]}", flush=True)
        except Exception as e:
            print(f"  #{i+1} 실패: {e}", flush=True)
    print(f"\n=== 1등 횟수: {first} ===")
    print("(example > scene > none 이면 예시 업그레이드 검증 + 장면앵커>무처리 동시 확인)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
