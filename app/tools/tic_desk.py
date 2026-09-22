# -*- coding: utf-8 -*-
"""틱 데스크 — 회차 간 반복 습관 판정(생성 후 advisory·본문 무접촉).

배경(사용자 착안 2026-07-27 "조사하라고 했을 때는 잡혔잖아 … 하나의 프로세스로 잡을 수도"):
문체 규칙으로 틱을 막으려던 시도가 실측에서 부작용을 냈다 — 규칙은 **형태로만** 판단해서
"좋은 논평"과 "굳은 습관"을 구별하지 못하고 같이 죽인다(부작용 A/B: 좋은 논평 생존 1~2/3).
반면 **생성 후 판정**은 문맥·빈도·기능을 함께 보므로 그 구별이 가능하고, 생성 프롬프트를
전혀 건드리지 않아 pink-elephant 부작용이 원천적으로 없다.

검증(블라인드 재현 테스트, 힌트 0): 반복 3/3 전부 최상위 틱을 적발. 결정론 빈도 계산기
(quality_gates.word_tics)가 구조적으로 못 잡는 것들을 잡았다 — '한 박자/반 박자' 계량 남발
15~18회, 도구 실험 장면의 붕어빵 구성 11회, '둘 중 하나' 이항 단정 격언조 10회 등.
(빈도 계산기는 2어절까지·"대사 양념 or 부사 편중" 게이트라 서술 습관을 못 본다.)

계약:
 · **advisory 전용** — 판정 라벨·임계·차단·자동수정 0. 본문 read-only. 처리 여부는 작가 결정.
 · **gen≠judge** — 심사는 교차 벤더 기본(settings.style_judge_model). 생성 모델은 자기 글을
   판정하면 유리해질 수 있어 기본에서 뺀다(--model 로 교차 검증은 가능).
 · **생성 컨텍스트 유입 금지** — 이 산출물을 회차 생성 프롬프트에 넣지 않는다(앵커링·
   pink-elephant 차단). 작가/PM 열람용. review_panel·cold_read 와 같은 층.
 · **결측 정직** — 심사 실패 시 0건으로 위장하지 않고 실패를 그대로 기록한다.

실행(app/ 에서):
  py -3.12 -X utf8 tools/tic_desk.py --pid <pid> [--chapter last] [--window 6]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.config import get_settings
from novelcopilot.repository.filesystem import FilesystemProjectRepository
from novelcopilot.llm.factory import create_role_provider

# 평문 우선(JSON 모드 금지) — 교차 벤더 추론 모델이 큰 입력 + json_object 조합에서 빈 응답을 내는
#   실측 때문(같은 예산·같은 입력에서 평문은 정상 산출). 파싱은 아래 _parse 가 관대하게 처리한다.
PROMPT = """아래는 한 연재 웹소설의 회차 전문이다.

이 원고에서 **작가의 습관으로 굳어 반복되는 것**을 찾아라. 회차를 넘나들며 같은 자리에 같은 방식으로 되풀이되는 구절·문형·서술 습관이 대상이다.
독자가 읽다가 "또 이거네"라고 느낄 만한 것만 골라라. 의도된 캐릭터 시그니처와 무의미한 습관을 구분하고, 습관 쪽만 보고하라.

심각한 순서로 최대 6개. 각 항목을 반드시 아래 형식 그대로, 다른 머리말 없이 써라.

[패턴] 반복되는 것이 무엇인지 한 줄
[횟수] 총 몇 회
[회차] 나온 화 번호들
[인용] 본문 원문 그대로
[인용] 본문 원문 그대로
[습관인이유] 왜 의도된 시그니처가 아니라 굳은 습관인지 한두 줄"""


def _parse(raw: str) -> list[dict]:
    """[태그] 형식 평문을 관대하게 항목화 — 굵게(**)·번호·머리말이 섞여도 태그만 보면 된다.
    한 항목도 못 뽑으면 빈 리스트(호출부가 raw 를 그대로 보존·표시 — 결측 위장 금지)."""
    if not raw:
        return []
    tag = re.compile(r"\[(패턴|횟수|회차|인용|습관인이유)\]\s*(.*)")
    items, cur = [], None
    for line in raw.splitlines():
        m = tag.search(line.replace("*", "").strip())
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip().strip("*").strip()
        if k == "패턴":
            if cur and cur.get("pattern"):
                items.append(cur)
            cur = {"pattern": v, "count": "", "chapters": "", "quotes": [], "why_tic": ""}
        elif cur is None:
            continue
        elif k == "횟수":
            cur["count"] = v
        elif k == "회차":
            cur["chapters"] = v
        elif k == "인용":
            cur["quotes"].append(v)
        else:
            cur["why_tic"] = v
    if cur and cur.get("pattern"):
        items.append(cur)
    return items


def run_desk(pid: str, chapter: str, window: int, model: str, out_dir: Path) -> dict:
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    state = repo.get(pid)
    if state is None:
        raise SystemExit(f"작품 없음: {pid}")
    chs = sorted([c for c in state.chapters if (c.text or "").strip()], key=lambda c: c.chapter)
    if not chs:
        raise SystemExit("본문 있는 회차가 없습니다")
    target = chs[-1].chapter if chapter == "last" else int(chapter)
    win = [c for c in chs if c.chapter <= target][-max(2, window):]
    if len(win) < 2:
        raise SystemExit(f"회차 간 반복 판정에는 2화 이상 필요(현재 {len(win)}화)")
    corpus = "\n\n".join(f"=== {c.chapter}화 「{c.title}」 ===\n{c.text}" for c in win)

    p = create_role_provider(s, model)
    raw, err = "", ""
    try:
        raw = (p.chat([{"role": "user", "content": PROMPT + "\n\n" + corpus}],
                      temperature=0.7) or "").strip()
        if p.last_truncated:               # 절단은 결측으로 위장하지 않는다
            err = "응답 절단(max_tokens) — 결과가 불완전할 수 있음"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:300]
    findings = _parse(raw)

    doc = {"pid": pid, "chapter": target, "window": [c.chapter for c in win], "model": model,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "mode": ("parsed" if findings else ("raw" if raw else "실패")),
           "findings": findings, "raw": raw, "error": err}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tic_desk_{pid}_ch{target}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"== 틱 데스크({model}) {pid} — 대상 {target}화 / 창 {doc['window']} / 모드 {doc['mode']}")
    if findings:
        for i, f in enumerate(findings, 1):
            print(f"  {i}. {(f.get('pattern') or '')[:70]}")
            print(f"     {f.get('count')} · {f.get('chapters')} | {(f.get('why_tic') or '')[:66]}")
            for q in (f.get("quotes") or [])[:2]:
                print(f'     "{str(q)[:64]}"')
    elif raw:
        print(raw[:1200])
    else:
        print(f"  판정 실패(결측 정직) — {err or '빈 응답'}")
    print("저장:", out)
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", required=True)
    ap.add_argument("--chapter", default="last")
    ap.add_argument("--window", type=int, default=6, help="판정에 넣을 최근 회차 수(회차 간 반복이 대상이라 2 이상)")
    ap.add_argument("--model", default="", help="빈값=settings.style_judge_model(교차 벤더 기본)")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "reports"))
    a = ap.parse_args()
    run_desk(a.pid, a.chapter, a.window, a.model or get_settings().style_judge_model,
             Path(a.out_dir))
