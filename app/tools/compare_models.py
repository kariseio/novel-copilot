# -*- coding: utf-8 -*-
"""모델 실측 비교 — 동일 시드, 모델별 풀파이프라인(월드젠+3화) 생성 후 고정 심판으로 채점.

스펙 비교가 아니라 '이 서비스의 실제 산출물' 기준: 품질(5축)·비용(토큰)·속도·에스컬레이션.
심판은 전 군 공통 고정(JUDGE) — 군별 채점 편향 제거(단 심판=후보 중 하나면 자기편향 가능성 명시).
실행: PYTHONPATH=app python tools/compare_models.py
"""
from __future__ import annotations
import json
import tempfile
import time
from pathlib import Path

from novelcopilot.config import Settings
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.openai_provider import OpenAIProvider

ARMS = ["gpt-4.1", "gpt-4.1-mini", "gpt-5.1-chat-latest", "gpt-5.2"]
JUDGE_MODEL = "gpt-5.2"
N_CH = 3
SEED = ProjectSeed(
    genre="SF 포스트아포칼립스", tone="",
    premise=("초지능 AI 전쟁으로 문명이 멸망한 뒤, 연산자원(GPU 코어)이 화폐이자 무기가 된 세계. "
             "재밍과 EMP 가 최강의 병기가 됐고, 멸망 전의 기술은 발굴되는 '고대 유물'이 되었다. "
             "폐허를 뒤지는 스캐빈저가 아직 살아 있는 고대 AI 코어를 주우면서 이야기가 시작된다."),
    protagonist_hint="전직 통신병 출신 스캐빈저. 말수가 적고 계산적이지만 동료는 버리지 못한다.",
    target_chapters=20)

_judge = OpenAIProvider(JUDGE_MODEL, "text-embedding-3-small")


def judge(text: str, ch: int) -> dict:
    try:
        r = _judge.chat_json(
            [{"role": "system", "content":
              "냉정한 웹소설 편집자. 이 회차를 5축(흡인력/문체조판/세계관구현/내적일관성/진행감) 각 1~10 채점. "
              '결함(메타텍스트 누출·토막행갈이·수치모순·반복)을 defects 에. JSON: '
              '{"scores":{"hook":0,"style":0,"world":0,"consistency":0,"progress":0},"defects":["..."]}'},
             {"role": "user", "content": f"[{ch}화]\n{text[:9000]}"}],
            temperature=0.0, max_tokens=500)
        sc = r.get("scores", {})
        r["avg"] = round(sum(sc.values()) / max(1, len(sc)), 2)
        return r
    except Exception as e:
        return {"avg": -1, "defects": [str(e)[:60]]}


def run_arm(model: str) -> dict:
    s = Settings(gen_model=model, data_dir=tempfile.mkdtemp(prefix=f"cmp_{model[:8]}_"))
    svc = CopilotService(s, FilesystemProjectRepository(s.resolved_data_dir()))
    t0 = time.time()
    out = {"model": model, "chapters": [], "world_ok": True}
    try:
        state, _ = svc.create_project(SEED)
    except Exception as e:
        return {"model": model, "world_ok": False, "error": str(e)[:160]}
    pid = state.id
    for i in range(N_CH):
        t1 = time.time()
        try:
            res = svc.generate_next_chapter(pid)
        except Exception as e:
            out["chapters"].append({"error": str(e)[:120]})
            break
        rec = res.get("record")
        if rec is None:
            break
        row = {"ch": rec.chapter, "status": rec.status.value, "chars": len(rec.text),
               "secs": round(time.time() - t1)}
        if rec.status.value == "FINALIZED":
            j = judge(rec.text, rec.chapter)
            row["judge_avg"] = j.get("avg")
            row["defects"] = [d[:60] for d in j.get("defects", [])][:3]
        out["chapters"].append(row)
    st = svc.get_project(pid)
    out["tokens"] = st.usage_total.get("chat_tokens", 0)
    out["total_secs"] = round(time.time() - t0)
    fin = [c for c in out["chapters"] if c.get("status") == "FINALIZED" and c.get("judge_avg", -1) > 0]
    out["judge_mean"] = round(sum(c["judge_avg"] for c in fin) / len(fin), 2) if fin else None
    out["avg_chars"] = sum(c.get("chars", 0) for c in fin) // max(1, len(fin))
    return out


if __name__ == "__main__":
    results = []
    for m in ARMS:
        print(f"\n===== ARM: {m} =====")
        r = run_arm(m)
        results.append(r)
        print(json.dumps({k: r.get(k) for k in ("model", "judge_mean", "tokens", "total_secs", "avg_chars", "error")},
                         ensure_ascii=False))
        for c in r.get("chapters", []):
            print("  ", json.dumps(c, ensure_ascii=False)[:170])
    Path(__file__).parent.joinpath("reports", "model_comparison.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 비교 요약 (judge=" + JUDGE_MODEL + ", 3화 풀파이프라인) =====")
    for r in results:
        print(f"{r['model']:<22} 품질 {r.get('judge_mean')} | 토큰 {r.get('tokens')} | "
              f"{r.get('total_secs')}s | 평균 {r.get('avg_chars')}자"
              + (f" | ERROR {r['error']}" if r.get("error") else ""))
