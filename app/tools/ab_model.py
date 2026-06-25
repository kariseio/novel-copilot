# -*- coding: utf-8 -*-
"""A/B — 모델 라우팅: 같은 세계·동일 프롬프트로 *본문 생성 모델만* 변주(gpt-4.1 vs Claude vs Gemini).
변수=모델 하나(프롬프트 교란 제거). 1차 지표=객관(ai_tell + 번역투 카운터), 2차=LLM 3-way 랭킹(자기선호 주의).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_model.py
"""
from __future__ import annotations
import sys, tempfile, random, re
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.quality_gates import ai_tell_profile
from tools.ab_obsession_worldgen import SEED

OUT = r"C:\Users\owner\AppData\Local\Temp\sl_compare"
ARMS = {"gpt-4.1": ("openai", "gpt-4.1"),
        "claude": ("anthropic", "claude-opus-4-8"),
        "gemini": ("gemini", "gemini-3.1-pro-preview")}
# 번역투(영한 직역체) — GPT의 핵심 AI티. 결정론 카운터.
_TRANS = [r"를?\s*통해", r"에\s*의해", r"되어지", r"지게\s*되", r"에\s*대(한|해)", r"에\s*있어", r"가지고\s*있", r"그녀"]


def trans_per_1k(text: str) -> float:
    n = sum(len(re.findall(p, text)) for p in _TRANS)
    return round(n / max(1, len(text)) * 1000, 2)


def two_ch(svc, pid):
    out = []
    for _ in range(2):
        rec = svc.generate_next_chapter(pid).get("record")
        out.append((rec.text if rec else "") or "")
    return "\n\n".join(out)


def main():
    judge = create_provider(get_settings())   # 심사(openai) — 자기선호 주의(객관지표가 1차)
    s0 = get_settings()
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp()))
    print("세계 생성(gpt-4.1)...(느림)", flush=True)
    svc0 = CopilotService(s0, repo)
    st, _ = svc0.create_project(SEED.model_copy(deep=True))
    roster = {e.name for e in st.world.entities}

    texts = {}
    for name, (prov, model) in ARMS.items():
        s_arm = s0.model_copy(update={"llm_provider": prov, "gen_model": model})
        svc = CopilotService(s_arm, repo)
        cp = st.model_copy(deep=True); cp.id = st.id + re.sub(r"[^a-z0-9]", "", name)
        svc.repo.save(cp)
        print(f"[{name}/{model}] 2회차 집필...", flush=True)
        try:
            texts[name] = two_ch(svc, cp.id)
        except Exception as e:
            print(f"  ERR {name}: {type(e).__name__} {str(e)[:160]}", flush=True); texts[name] = ""
        Path(OUT).mkdir(parents=True, exist_ok=True)
        Path(OUT, f"model_{name}.txt").write_text(texts[name], encoding="utf-8")
        print(f"  {name} {len(texts[name])}자", flush=True)

    print("\n[객관 지표 — 1차]")
    print(f"  {'arm':10}{'번역투/1k':>10}  ai_tell(comma/sentCV/어미다양/직유)")
    for name in ARMS:
        t = texts[name]
        if not t:
            print(f"  {name:10}{'(실패)':>10}"); continue
        p = ai_tell_profile(t, roster)
        print(f"  {name:10}{trans_per_1k(t):>10}  {p['comma_per_100']:.2f} / {p['sent_len_cv']:.2f} / {p['ending_diversity']:.2f} / {p['simile_per_1k']:.2f}")

    avail = [n for n in ARMS if texts.get(n)]
    if len(avail) >= 2:
        print("\n[LLM 3-way 중립 랭킹 6회 — 자기선호 주의, 객관지표가 1차]", flush=True)
        first = {n: 0 for n in avail}
        for i in range(6):
            order = avail[:]; random.shuffle(order)
            labels = ["A", "B", "C"][:len(order)]
            body = "\n\n".join(f"[본문 {labels[j]}]\n{texts[order[j]][:4200]}" for j in range(len(order)))
            sysj = ("너는 까다로운 한국 웹소설 독자다. 본문들을 *덜 AI같고(번역투·기계적 리듬 없이) 더 자연스럽고 잘 읽히는* 순으로 순위. "
                    '{"ranking":["가장 좋은 라벨",...],"reason":"한 줄"} JSON만.')
            try:
                d = judge.chat_json([{"role": "system", "content": sysj}, {"role": "user", "content": body}], temperature=0.3)
                rk = d.get("ranking") or []
                if rk and rk[0] in labels:
                    first[order[labels.index(rk[0])]] += 1
                    print(f"  #{i+1} 1등={order[labels.index(rk[0])]} ({rk}) — {str(d.get('reason',''))[:60]}", flush=True)
            except Exception as e:
                print(f"  #{i+1} 실패: {e}", flush=True)
        print(f"\n=== 1등 횟수: {first} (객관 번역투/ai_tell 우선) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
