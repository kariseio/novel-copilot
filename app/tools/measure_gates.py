# -*- coding: utf-8 -*-
"""게이트 유효성 측정 — 골든셋 대비 유형별 Recall/Precision/FPR + Wilson CI + 운영 판정.

이 제품의 가치명제("게이트가 위반을 잡는다")를 계산된 숫자로 바꾸는 도구.
실행:  PYTHONPATH=app python tools/measure_gates.py --n 5        (live LLM, 유형당 5문단 ≈ 37콜)
       PYTHONPATH=app python tools/measure_gates.py --n 50       (풀 측정)
판정 규칙: 유형별 Recall ≥ 0.8 AND 하드네거티브 FPR ≤ 0.05 → hard 게이트 유지, 미달 → advisory 강등 권고.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import time
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.engine.factory import build_engine
from novelcopilot.llm.factory import create_provider
from tools.golden_set import golden_world, build, TYPES


def wilson(p_hat: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    den = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / den
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / den
    return (max(0.0, center - margin), min(1.0, center + margin))


def run(n_per_type: int) -> dict:
    s = get_settings()
    provider = create_provider(s)
    b = build_engine(golden_world(), provider, s)
    ont, checker = b.ontology, b.checker
    involved = list(ont.entities.keys())
    samples = build(n_per_type)
    rows = []
    for i, sm in enumerate(samples):
        res = checker.check_text(sm["text"], ont, chapter=5, involved_ids=involved)
        fired = [(v.kind, v.entity, v.grade.value) for v in res.violations]
        hard = [(v.kind, v.entity) for v in res.violations if v.is_hard]
        rows.append({**sm, "fired": fired, "hard_fired": hard})
        print(f"  [{i+1}/{len(samples)}] {sm['label']:<22} hard={len(hard)} fired={[k for k,_,_ in fired]}")
    return analyze(rows)


def _matches(expect_kind, entity, fired_list) -> bool:
    return any(k.startswith(expect_kind) and (entity is None or entity in (e or ""))
               for k, e in fired_list)


def analyze(rows: list[dict]) -> dict:
    negatives = [r for r in rows if r["label"].startswith("negative:")]
    report = {"per_type": {}, "negatives": {}}
    for tname, spec in TYPES.items():
        pos = [r for r in rows if r["label"] == tname]
        if not pos:
            continue
        all_fired = "fired" if spec["grade"] == "semantic" else "hard_fired"
        def hit(r):
            fl = [(k, e) for k, e, *_ in r["fired"]] if all_fired == "fired" else r["hard_fired"]
            return _matches(spec["expect_kind"], spec["entity"], fl)
        tp = sum(1 for r in pos if hit(r))
        fn = len(pos) - tp
        fp_rows = [r for r in rows if r["label"] != tname and hit(r)]
        fp = len(fp_rows)
        recall = tp / max(1, tp + fn)
        precision = tp / max(1, tp + fp)
        rl, rh = wilson(recall, len(pos))
        verdict = ("hard 유지" if (recall >= 0.8 and spec["grade"] != "semantic") else
                   ("advisory(설계대로)" if spec["grade"] == "semantic" else "advisory 강등 권고"))
        report["per_type"][tname] = {
            "grade": spec["grade"], "n": len(pos), "TP": tp, "FN": fn, "FP": fp,
            "recall": round(recall, 3), "recall_ci95": [round(rl, 3), round(rh, 3)],
            "precision": round(precision, 3), "verdict": verdict,
            "fp_cases": [{"label": r["label"], "text": r["text"][:90]} for r in fp_rows[:5]],   # 오발화 원문(few-shot 보강 재료)
            "fn_cases": [{"text": r["text"][:90]} for r in pos if not hit(r)][:5]}
    # 하드네거티브 FPR: 어떤 hard 든 점등하면 위양성
    n_neg = len(negatives)
    fp_neg = sum(1 for r in negatives if r["hard_fired"])
    fpr = fp_neg / max(1, n_neg)
    fl, fh = wilson(fpr, n_neg)
    report["negatives"] = {"n": n_neg, "hard_FP": fp_neg, "FPR": round(fpr, 3),
                           "FPR_ci95": [round(fl, 3), round(fh, 3)],
                           "fp_cases": [{"label": r["label"], "hard": r["hard_fired"]}
                                        for r in negatives if r["hard_fired"]],
                           "verdict": "통과(≤0.05)" if fpr <= 0.05 else "위양성 초과 — 추출 few-shot/증거 강화 필요"}
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="유형당 위반 문단 수")
    args = ap.parse_args()
    t0 = time.time()
    print(f"게이트 유효성 측정 시작 (유형 5종 × {args.n} + 하드네거티브 12)")
    rep = run(args.n)
    out = Path(__file__).parent / "reports"
    out.mkdir(exist_ok=True)
    path = out / "gate_validity.json"
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 게이트 유효성 리포트 =====")
    for t, r in rep["per_type"].items():
        print(f"{t:<14} [{r['grade']:<8}] recall={r['recall']} CI{r['recall_ci95']} "
              f"precision={r['precision']} (TP {r['TP']}/FN {r['FN']}/FP {r['FP']}) → {r['verdict']}")
    ng = rep["negatives"]
    print(f"하드네거티브   FPR={ng['FPR']} CI{ng['FPR_ci95']} ({ng['hard_FP']}/{ng['n']}) → {ng['verdict']}")
    print(f"\n저장: {path}  ({time.time()-t0:.0f}s)")
    bad = [t for t, r in rep["per_type"].items() if "강등" in r["verdict"]]
    sys.exit(1 if (bad or ng["FPR"] > 0.05) else 0)
