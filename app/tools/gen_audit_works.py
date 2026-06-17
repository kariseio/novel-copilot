# -*- coding: utf-8 -*-
"""블라인드 감사용 신규 작품 3개 생성 — 인기 장르 3종, 각 5화 + 회차별 평가 기록.

조향(빨간 펜) 없이 시스템 원출력 그대로 받는다 — 감사 증거는 '시스템 혼자 어디까지 하는가'.
평가는 sim_persona.eval_chapter(편집자 미니 루브릭 + 결정론 상한) 재사용, test_history.jsonl 누적.
실행: PYTHONPATH=app python app/tools/gen_audit_works.py
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from sim_persona import sse_generate, eval_chapter, record, BASE  # noqa: E402

RUN = "audit-fresh-3works"
N_CH = 5

SEEDS = [
    {
        "title": "", "tone": "",
        "genre": "현대 판타지, 헌터, 회귀",
        "premise": ("게이트 사태 20년, 만년 F급으로 온갖 멸시를 받다 최후의 던전에서 죽은 헌터 서진우. "
                    "눈을 떠보니 각성 직전의 스무 살로 돌아왔다. 미래의 공략 지식과 아무도 모르는 "
                    "보상의 위치를 아는 채로, 이번엔 바닥부터 다시 오른다."),
        "protagonist_hint": "서진우, 28→20세 회귀. 냉정하지만 전생에서 자신을 도왔던 사람들에게는 약하다.",
        "target_chapters": 200,
    },
    {
        "title": "", "tone": "",
        "genre": "로맨스 판타지, 빙의, 악역",
        "premise": ("과로사한 웹소설 작가가 눈을 뜨니 자신이 연재하던 소설 속 처형 엔딩 확정 악역 공녀 "
                    "'이자벨라 디 모르간'이 되어 있었다. 원작 전개까지 남은 시간 3년. 처형 플래그를 "
                    "부수려 움직일수록 원작 주인공들의 운명이 뒤틀리기 시작한다."),
        "protagonist_hint": "이자벨라 — 원작 지식을 가진 전직 작가. 독설가지만 본질은 생존주의자.",
        "target_chapters": 200,
    },
    {
        "title": "", "tone": "",
        "genre": "무협, 복수",
        "premise": ("멸문당한 검가의 마지막 생존자 진서하. 원수의 문파에 신분을 숨기고 말단 제자로 들어가 "
                    "십 년, 가문을 멸한 진짜 배후가 문파 너머의 거대 세력임을 알게 된다. "
                    "검 하나로 강호의 판을 뒤집는 복수행."),
        "protagonist_hint": "진서하 — 복수를 위해 모든 것을 감추는 인내의 검수. 정이 드는 것을 두려워한다.",
        "target_chapters": 200,
    },
]


def make_work(seed: dict) -> dict:
    r = None
    for attempt in range(3):
        r = requests.post(f"{BASE}/projects", json=seed, timeout=900)
        if r.status_code == 200:
            break
        print(f"  create retry {attempt + 1}: {r.status_code}", flush=True)
        time.sleep(3)
    r.raise_for_status()
    pid = r.json()["id"]
    title = r.json()["world"]["title"]
    print(f"[{seed['genre']}] -> {pid} | {title}", flush=True)

    try:
        snap = requests.get(f"{BASE}/projects/{pid}/ontology", timeout=120).json()
        roster = {c["name"] for c in snap.get("characters", [])}
    except Exception:
        roster = set()

    evs, fails, ch = [], 0, 0
    while ch < N_CH and fails < 3:
        t1 = time.time()
        res = sse_generate(pid, "")
        data = res.get("data", {})
        if res.get("final") == "failed":
            fails += 1
            print(f"  ch{ch + 1} FAILED ({str(data)[:120]}) retry {fails}", flush=True)
            time.sleep(5)
            continue
        rec = data.get("record", {})
        ch = data.get("current_chapter", ch)
        status = rec.get("status")
        if status == "ESCALATED":
            res = sse_generate(pid, "직전 시도에서 설정 위반이 났다. 확정 설정(사망·속성·관계)을 어기지 말고 다시 써라.")
            data = res.get("data", {})
            rec = data.get("record", {})
            ch = data.get("current_chapter", ch)
            status = rec.get("status", "ESCALATED")
            if status != "FINALIZED":
                fails += 1
                continue
        ev = eval_chapter(rec.get("text", ""), rec.get("chapter", 0), roster)
        evs.append(ev.get("avg", -1))
        record({"type": "chapter_eval", "run": RUN, "pid": pid, "chapter": rec.get("chapter"),
                "status": status, "chars": len(rec.get("text", "")),
                "avg": ev.get("avg"), "scores": ev.get("scores"), "det": ev.get("det"),
                "defects": [(x or "")[:80] for x in (ev.get("defects") or [])[:3]]})
        print(f"  ch{rec.get('chapter')} {status} {len(rec.get('text', ''))}자 "
              f"avg={ev.get('avg')} ({round(time.time() - t1)}s)", flush=True)

    ok = [e for e in evs if e and e > 0]
    summary = {"pid": pid, "title": title, "genre": seed["genre"], "chapters": ch,
               "avg_eval": round(sum(ok) / len(ok), 2) if ok else None}
    record({"type": "run_summary", "run": RUN, **summary})
    return summary


def main():
    out = [make_work(s) for s in SEEDS]
    print("\n===== 3작품 생성 완료 =====")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
