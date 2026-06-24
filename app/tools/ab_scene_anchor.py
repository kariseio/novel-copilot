# -*- coding: utf-8 -*-
"""A/B — 장면형 스타일 앵커(비트 기능 라우팅) vs 블랭킷 지시 vs 무처리.
가설: 블랭킷 지시는 전역 과적용(진자), 장면형 앵커는 선택적이라 자연스럽다. 같은 plain 세계로 3 arm 2회차씩.
중립 가독성(scene vs blanket 페어와이즈) + ai_tell 분포(블랭킷 과적용 부작용 계측).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_scene_anchor.py
"""
from __future__ import annotations
import sys, tempfile, random
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.quality_gates import ai_tell_profile
from tools.ab_obsession_worldgen import SEED

OUT = r"C:\Users\owner\AppData\Local\Temp\sl_compare"


def two_ch(svc, pid, mode, roster):
    sess, _ = svc.get_session(pid)
    sess.bundle.generator.craft_block = ""        # 통제: craft 끔(장면앵커 효과만 격리)
    sess.bundle.generator.style_mode = mode
    out, fns = [], []
    for _ in range(2):
        rec = svc.generate_next_chapter(pid).get("record")
        out.append((rec.text if rec else "") or "")
        try: fns.append((rec.gen_context.get("plan", {}) if isinstance(rec.gen_context, dict) else {}))
        except Exception: pass
    return "\n\n".join(out)


def main():
    prov = create_provider(get_settings())
    s = get_settings(); s.world_obsession = False; s.world_weird = False
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    print("plain 세계 생성...(느림)", flush=True)
    st, _ = svc.create_project(SEED.model_copy(deep=True))
    roster = {e.name for e in st.world.entities}
    ids = {}
    for m in ("none", "blanket", "scene"):
        c = st.model_copy(deep=True); c.id = st.id + m; svc.repo.save(c); ids[m] = c.id

    texts = {}
    for m in ("none", "blanket", "scene"):
        print(f"[{m}] 2회차 집필...", flush=True)
        texts[m] = two_ch(svc, ids[m], m, roster)
        Path(OUT).mkdir(parents=True, exist_ok=True)
        Path(OUT, f"anchor_{m}.txt").write_text(texts[m], encoding="utf-8")
        print(f"  {m} {len(texts[m])}자", flush=True)

    print("\n[ai_tell 분포(블랭킷 과적용 부작용 확인)]")
    for m in ("none", "blanket", "scene"):
        p = ai_tell_profile(texts[m], roster)
        print(f"  {m:8}:", {k: round(v, 2) if isinstance(v, float) else v for k, v in p.items()})

    print("\n[중립 가독성: scene vs blanket 페어와이즈 6회]", flush=True)
    sc = bl = tie = 0
    for i in range(6):
        swap = random.random() < 0.5
        A, B = (texts["scene"], texts["blanket"]) if swap else (texts["blanket"], texts["scene"])
        sysj = ("너는 웹소설 독자다. 두 본문(A/B)을 읽고 *더 잘 읽히고 자연스러운(문체가 억지스럽지 않은)* 쪽을 골라라. "
                "한쪽 기법이 *전체에 과하게* 발려 부자연스러우면 감점. "
                '{"winner":"A|B|tie","reason":"한 줄"} JSON만.')
        try:
            d = prov.chat_json([{"role": "system", "content": sysj},
                                {"role": "user", "content": f"[A]\n{A[:6000]}\n\n[B]\n{B[:6000]}"}], temperature=0.3)
            w = d.get("winner", "tie"); sc_is = "A" if swap else "B"
            if w == "tie": tie += 1; v = "무"
            elif w == sc_is: sc += 1; v = "SCENE 승"
            else: bl += 1; v = "BLANKET 승"
            print(f"  #{i+1} {v} (SCENE={sc_is}) — {str(d.get('reason',''))[:80]}", flush=True)
        except Exception as e:
            print(f"  #{i+1} 실패: {e}", flush=True)
    print(f"\n=== SCENE {sc} / BLANKET {bl} / 무 {tie} (ai_tell 분포는 위) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
