# -*- coding: utf-8 -*-
"""A/B — 모델 라우팅(공정판): 같은 세계·동일 프롬프트로 *본문 생성 모델만* 변주.
arm: gpt-5.2-chat-latest / gpt-5.2(추론) / claude-opus-4-8 / gemini-3.1-pro-preview.
1차 지표=객관(ai_tell + 번역투, '그녀' 제거). 2차=pairwise LLM 심사 3-family 판정단(양순서 일치만 인정→자기선호·위치편향 상쇄).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_model.py
"""
from __future__ import annotations
import sys, tempfile, random, re, itertools
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.openai_provider import OpenAIProvider
from novelcopilot.llm.anthropic_provider import AnthropicProvider
from novelcopilot.llm.gemini_provider import GeminiProvider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.quality_gates import ai_tell_profile
from tools.ab_obsession_worldgen import SEED

OUT = r"C:\Users\owner\AppData\Local\Temp\sl_compare"
ARMS = {"gpt5.2-chat": ("openai", "gpt-5.2-chat-latest"),
        "gpt5.2": ("openai", "gpt-5.2"),
        "claude": ("anthropic", "claude-opus-4-8"),
        "gemini": ("gemini", "gemini-3.1-pro-preview")}
# 번역투(영한 직역체) — '그녀'는 오염원(맥락상 정상 사용 많음)이라 제외.
_TRANS = [r"를?\s*통해", r"에\s*의해", r"되어지", r"지게\s*되", r"에\s*대(한|해)", r"에\s*있어", r"가지고\s*있", r"것으로\s*보였", r"에\s*다름\s*아니"]


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
    s0 = get_settings()
    _emb = OpenAIProvider("gpt-4.1", "text-embedding-3-small")  # 판정단 임베딩 위임용(미사용)
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
            print(f"  ERR {name}: {type(e).__name__} {str(e)[:200]}", flush=True); texts[name] = ""
        Path(OUT).mkdir(parents=True, exist_ok=True)
        Path(OUT, f"model_{name}.txt").write_text(texts[name], encoding="utf-8")
        print(f"  {name} {len(texts[name])}자", flush=True)

    print("\n[객관 지표 — 1차]")
    print(f"  {'arm':14}{'번역투/1k':>10}  comma/sentCV/어미다양/직유")
    for name in ARMS:
        t = texts[name]
        if not t:
            print(f"  {name:14}{'(실패)':>10}"); continue
        p = ai_tell_profile(t, roster)
        print(f"  {name:14}{trans_per_1k(t):>10}  {p['comma_per_100']:.2f} / {p['sent_len_cv']:.2f} / {p['ending_diversity']:.2f} / {p['simile_per_1k']:.2f}")

    avail = [n for n in ARMS if texts.get(n)]
    if len(avail) < 2:
        return 0

    # --- 2차: pairwise, 3-family 판정단, 양순서 일치만 승 인정 ---
    judges = []
    try:
        judges.append(("J-gpt", OpenAIProvider("gpt-4.1", "text-embedding-3-small")))
    except Exception as e:
        print("judge gpt skip", e)
    try:
        judges.append(("J-claude", AnthropicProvider("claude-sonnet-4-6", _emb)))
    except Exception as e:
        print("judge claude skip", e)
    try:
        judges.append(("J-gemini", GeminiProvider("gemini-2.5-flash", _emb)))
    except Exception as e:
        print("judge gemini skip", e)

    SYS = ('너는 까다로운 한국 웹소설 독자다. 두 본문 중 *덜 AI같고(번역투·기계적 반복·과설명 없이) 더 자연스럽고 잘 읽히는* 쪽을 고른다. '
           '{"winner":"A" 또는 "B","reason":"한 줄"} JSON만 출력.')
    wins = {n: 0 for n in avail}          # 양순서 일치 승만 합산(판정단 전체)
    per_judge = {jn: {n: 0 for n in avail} for jn, _ in judges}
    print("\n[pairwise — 양순서 일치 승만 인정(위치편향 상쇄), 판정단 3-family(자기선호 상쇄). 객관지표가 1차]", flush=True)
    for x, y in itertools.combinations(avail, 2):
        for jn, jp in judges:
            verdicts = []
            for a, b in [(x, y), (y, x)]:
                body = f"[본문 A]\n{texts[a][:4000]}\n\n[본문 B]\n{texts[b][:4000]}"
                try:
                    d = jp.chat_json([{"role": "system", "content": SYS}, {"role": "user", "content": body}], temperature=0.2)
                    w = d.get("winner")
                    verdicts.append(a if w == "A" else (b if w == "B" else None))
                except Exception as e:
                    verdicts.append(None); print(f"    {jn} {a}vs{b} 실패: {str(e)[:80]}", flush=True)
            if len(verdicts) == 2 and verdicts[0] and verdicts[0] == verdicts[1]:
                wins[verdicts[0]] += 1; per_judge[jn][verdicts[0]] += 1
                print(f"  {x} vs {y} | {jn}: {verdicts[0]} (양순서 일치)", flush=True)
            else:
                print(f"  {x} vs {y} | {jn}: 무승부/불일치 {verdicts}", flush=True)

    print(f"\n=== 양순서일치 승 합계(판정단 전체): {wins} ===")
    for jn in per_judge:
        print(f"    {jn}: {per_judge[jn]}")
    print("(1차=객관 번역투/comma/sentCV. 2차=판정단 — 같은-family 미세 자기선호 잔존 가능)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
