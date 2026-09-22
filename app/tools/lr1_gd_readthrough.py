# -*- coding: utf-8 -*-
"""LR-1 G-D 연독 시뮬 baseline — 유료 독자 회차별 결제 의사 곡선(읽기 전용 · openai 심사).

방법: 작품별 1~24화를 순서대로 읽는 독자 시뮬. 회차마다 openai 심사 1콜.
  입력 = [이전 화들의 누적 1줄 요약 목록(프로젝트 저장 summary 재사용) + 직전 화 전문(15k자 절단)
         + 현재 화 전문(15k자 절단)]
  질문 = "유료 연재 독자로서 다음 화(100원)를 결제하겠는가? yes/no + 한 줄 이유.
         지금까지 누적 피로/기대도 반영하라."
gen≠judge: 본문 생성은 anthropic 계열 → 심사는 openai 계열(gpt-5.2-chat-latest).
루브릭 중립: 내부 용어(원장/약속/craft 등) 없음 — 순수 독자 관점 질문만.
산출: 회차별 yes/no 수열 · 첫 no(손절점) · 12~20화 no 비율(중반 처짐)
     · no 2연속=이탈 생존 분석 · 대표 이유 인용.
사용: (app/ 에서) python tools/lr1_gd_readthrough.py --arm 1   (arm 1|2|3, 생략 시 전부 순차)
출력: tools/reports/lr1_gd_arm{N}.jsonl (회차별 append) + lr1_gd_readthrough.json/.md (집계)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.config import get_settings                      # noqa: E402  (.env 로드 부작용)
from novelcopilot.llm.openai_provider import OpenAIProvider       # noqa: E402

APP = pathlib.Path(__file__).resolve().parents[1]
REPORTS = APP / "tools" / "reports"
TRUNC = 15000
JUDGE_MODEL = "gpt-5.2-chat-latest"

ARMS = {
    "1": "6cda5ee883e0",   # 봄이 오는 창가 (학원물 잔잔한 일상)
    "2": "8f7e8cb966a2",   # 붕괴 각성 (현대판타지 액션)
    "3": "7cba74b80209",   # 북부의 서리꽃 (로맨스판타지)
}

_SYSTEM = (
    "너는 한국 웹소설 연재 플랫폼에서 이 작품을 1화부터 유료로 따라 읽고 있는 독자다. "
    "매 화를 다 읽을 때마다 다음 화(100원)를 결제할지 말지를 결정한다. "
    "지금까지 읽으며 쌓인 피로감과 기대감을 솔직하게 반영하라 — 의무감으로 후하게 주지 마라. "
    "돈이 아깝다고 느끼면 no 다.\n"
    '출력은 JSON 객체만: {"pay_next": "yes" 또는 "no", "reason": "한 줄 이유"}'
)


def load_chapters(pid: str) -> tuple[dict, list[dict]]:
    """핫패스 JSON 만 직접 파싱(읽기 전용 — 저장 API 불사용, rag 사이드카 미로드)."""
    p = json.loads((APP / "data" / "projects" / f"{pid}.json").read_text(encoding="utf-8"))
    chs = sorted(p["chapters"], key=lambda c: c["chapter"])
    chs = [c for c in chs if c.get("status") == "FINALIZED"]
    return p, chs


def build_user(world: dict, chs: list[dict], idx: int) -> str:
    """idx = 0-based 현재 화 인덱스. 누적 요약(1..idx-1화) + 직전 화 전문 + 현재 화 전문."""
    cur = chs[idx]
    parts = [f"[작품] {world.get('title','')} — {world.get('genre','')}"]
    if idx > 0:
        lines = [f"{c['chapter']}화: {(c.get('summary') or '').strip()}" for c in chs[:idx]]
        parts.append("[지금까지 읽은 회차의 한 줄 요약]\n" + "\n".join(lines))
        prev = chs[idx - 1]
        parts.append(f"[직전 화({prev['chapter']}화) 전문]\n" + (prev.get("text") or "")[:TRUNC])
    else:
        parts.append("[지금까지 읽은 회차] 없음 — 이번이 첫 화다.")
    parts.append(f"[방금 읽은 {cur['chapter']}화 전문]\n" + (cur.get("text") or "")[:TRUNC])
    parts.append("[질문] 유료 연재 독자로서 다음 화(100원)를 결제하겠는가? yes/no + 한 줄 이유. "
                 "지금까지 누적 피로/기대도 반영하라.")
    return "\n\n".join(parts)


def run_arm(arm: str, judge: OpenAIProvider) -> list[dict]:
    pid = ARMS[arm]
    world_state, chs = load_chapters(pid)
    world = world_state["world"]
    out_path = REPORTS / f"lr1_gd_arm{arm}.jsonl"

    done: dict[int, dict] = {}
    if out_path.exists():                                          # 중단 재개(이미 심사한 회차 스킵)
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done[row["chapter"]] = row

    rows: list[dict] = []
    for i, ch in enumerate(chs):
        n = ch["chapter"]
        if n in done:
            rows.append(done[n])
            continue
        user = build_user(world, chs, i)
        t0 = time.time()
        d = judge.chat_json([{"role": "system", "content": _SYSTEM},
                             {"role": "user", "content": user}],
                            temperature=0.0, max_tokens=500)
        pay = str(d.get("pay_next") or "").strip().lower()
        if pay not in ("yes", "no"):
            pay = "yes" if "yes" in pay else "no"
        row = {"arm": arm, "project_id": pid, "chapter": n,
               "pay_next": pay, "reason": str(d.get("reason") or "").strip(),
               "elapsed_s": round(time.time() - t0, 1), "judge": JUDGE_MODEL}
        rows.append(row)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"arm{arm} ch{n:02d} -> {pay} ({row['elapsed_s']}s)")
    return rows


def analyze(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda r: r["chapter"])
    seq = "".join("y" if r["pay_next"] == "yes" else "n" for r in rows)
    first_no = next((r["chapter"] for r in rows if r["pay_next"] == "no"), -1)
    mid = [r for r in rows if 12 <= r["chapter"] <= 20]
    mid_no = (sum(1 for r in mid if r["pay_next"] == "no") / len(mid)) if mid else 0.0
    churn_at = -1
    for a, b in zip(rows, rows[1:]):
        if a["pay_next"] == "no" and b["pay_next"] == "no":
            churn_at = b["chapter"]
            break
    return {
        "project_id": rows[0]["project_id"] if rows else "",
        "n_chapters": len(rows),
        "sequence": seq,
        "pay_next_ratio": round(seq.count("y") / len(seq), 3) if seq else 0.0,
        "first_no_chapter": first_no,
        "mid_sag_no_ratio": round(mid_no, 3),
        "churn_at_chapter": churn_at,                # no 2연속의 두 번째 no 회차(-1=이탈 없음)
        "survived_to_end": churn_at == -1,
        "reasons": [{"chapter": r["chapter"], "pay_next": r["pay_next"], "reason": r["reason"]}
                    for r in rows],
    }


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="LR-1 G-D 연독 시뮬(읽기 전용)")
    ap.add_argument("--arm", default="", help="1|2|3 (생략 시 전부 순차)")
    args = ap.parse_args(argv)
    get_settings()                                                 # .env → os.environ (OPENAI_API_KEY)
    judge = OpenAIProvider(JUDGE_MODEL, "text-embedding-3-small")

    arms = [args.arm] if args.arm else list(ARMS)
    results = {}
    for arm in arms:
        rows = run_arm(arm, judge)
        results[arm] = analyze(rows)
        r = results[arm]
        print(f"arm{arm} {r['project_id']}: seq={r['sequence']} pay={r['pay_next_ratio']}"
              f" first_no={r['first_no_chapter']} mid_no={r['mid_sag_no_ratio']}"
              f" churn={r['churn_at_chapter']}")

    # 집계 리포트(전 arm jsonl 이 있으면 병합)
    merged = {}
    for arm in ARMS:
        p = REPORTS / f"lr1_gd_arm{arm}.jsonl"
        if p.exists():
            rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
            if len(rows) == 24:
                merged[arm] = analyze(rows)
    if len(merged) == len(ARMS):
        report = {"report": "LR-1 G-D 연독 시뮬 baseline",
                  "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "judge_model": JUDGE_MODEL, "truncate_chars": TRUNC,
                  "question": "유료 연재 독자로서 다음 화(100원)를 결제하겠는가? yes/no + 한 줄 이유. "
                              "지금까지 누적 피로/기대도 반영하라.",
                  "arms": merged,
                  "usage": judge.usage.as_dict()}
        (REPORTS / "lr1_gd_readthrough.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("report:", REPORTS / "lr1_gd_readthrough.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
