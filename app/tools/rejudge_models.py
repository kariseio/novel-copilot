# -*- coding: utf-8 -*-
"""모델 비교 재심판 — 이미 생성된 군별 회차를 찾아 심판만 재실행(재생성 비용 0).
gpt-5.2 심판의 reasoning 토큰 소모를 감안해 출력 예산 상향 + 결함 서술 최소화."""
from __future__ import annotations
import glob
import json
import os
import tempfile
from pathlib import Path

from novelcopilot.domain.project import ProjectState
from novelcopilot.llm.openai_provider import OpenAIProvider

JUDGE = OpenAIProvider("gpt-5.2", "text-embedding-3-small")


def judge(text, ch):
    r = JUDGE.chat_json(
        [{"role": "system", "content":
          "냉정한 웹소설 편집자. 5축(hook/style/world/consistency/progress) 각 1~10 채점. defects 는 최대 2개, 각 20자 이내. "
          'JSON만: {"scores":{"hook":0,"style":0,"world":0,"consistency":0,"progress":0},"defects":[""]}'},
         {"role": "user", "content": f"[{ch}화]\n{text[:8000]}"}],
        temperature=0.0, max_tokens=3000)
    sc = r.get("scores", {})
    return round(sum(sc.values()) / max(1, len(sc)), 2), r.get("defects", [])


rows = []
for d in glob.glob(os.path.join(tempfile.gettempdir(), "cmp_*")):
    model = Path(d).name.split("_", 1)[1].rsplit("_", 1)[0]
    for pj in glob.glob(os.path.join(d, "projects", "*.json")):
        if ".rag." in pj:
            continue
        st = ProjectState.model_validate_json(open(pj, encoding="utf-8").read())
        fin = [c for c in st.chapters if c.status.value == "FINALIZED"]
        if not fin:
            continue
        scores, defects = [], []
        for c in fin:
            try:
                a, df = judge(c.text, c.chapter)
                scores.append(a)
                defects += df
            except Exception as e:
                defects.append(f"judge err {str(e)[:40]}")
        rows.append({"model": model, "n": len(fin),
                     "judge_mean": round(sum(scores) / len(scores), 2) if scores else None,
                     "per_ch": scores, "avg_chars": sum(len(c.text) for c in fin) // len(fin),
                     "tokens": st.usage_total.get("chat_tokens"), "defects": defects[:4]})

print(json.dumps(rows, ensure_ascii=False, indent=1))
Path(__file__).parent.joinpath("reports", "model_comparison_rejudged.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
