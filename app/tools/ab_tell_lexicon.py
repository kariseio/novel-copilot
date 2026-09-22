# -*- coding: utf-8 -*-
"""TL-1 A/B 러너 — 기존 검증법(ai_tell 무사전 분포) vs 따온 검증법(tell_lexicon 14범주 사전) 병렬 계측.

목적: lingfengQAQ/webnovel-writer 에서 이식한 사전식 검출(engine.tell_lexicon)이 기존 축과
어떻게 다른 신호를 내는지 **동일 코퍼스에서 나란히** 계측한다. 두 그룹:
  · 인간 대조군: reference/ 4화(TRPG EP.0~1·아카데미 EP.0~1 — kiwi_human_band·st10a 와 동일 표본)
  · AI 코퍼스: app/data/projects/*.json 의 FINALIZED/ESCALATED 회차 전수(본문 비어있지 않은 것)

계약(무강제·측정 원칙):
  · LLM 0콜·결정론. 두 검증법 모두 **roster ∅·동일 조건 fresh 계산**(저장된 record.ai_tell 은
    roster 반영이라 lexical_mattr 이 미세 상이할 수 있음 — A/B 는 동일 조건 우선, 문서화).
  · 산출은 원자료(분수+n·대역 min/median/max)만 — 판정 라벨·임계·색상 0. 어느 검증법이 나은지의
    결론은 이 리포트가 내지 않는다(사용자/정독 판정).
  · 인간 원문 비커밋 계약(kiwi_human_band 동일): 리포트에는 **수치만** — 원문·인용 0.
    (top needle 표면형은 사전 항목이지 원문 인용이 아니므로 노출 가능.)

실행(app/ 에서): PYTHONIOENCODING=utf-8 py -3.12 tools/ab_tell_lexicon.py
산출: tools/reports/tl1_tell_lexicon_ab.md + .json
"""
from __future__ import annotations

import json
import pathlib
import statistics
import sys

APP = pathlib.Path(__file__).resolve().parents[1]     # app/
REPO = APP.parent                                     # ai-web-novel/
REF = REPO / "reference"
REPORTS = APP / "tools" / "reports"
sys.path.insert(0, str(APP))

from novelcopilot.engine.quality_gates import ai_tell_profile            # 기존 검증법(무사전 분포)
from novelcopilot.engine.tell_lexicon import (                            # 따온 검증법(14범주 사전)
    LEXICON, LEXICON_VERSION, tell_lexicon_profile)

# 인간 대조군 4화 — kiwi_human_band.REF_FILES 와 동일 표본(대조군 일관성)
REF_FILES = [
    ("TRPG EP.0", REF / "이세계 TRPG 게임마스터" / "EP.0.txt"),
    ("TRPG EP.1", REF / "이세계 TRPG 게임마스터" / "EP.1.txt"),
    ("아카데미 EP.0", REF / "아카데미 훈수빌런이 되다" / "EP.0.txt"),
    ("아카데미 EP.1", REF / "아카데미 훈수빌런이 되다" / "EP.1.txt"),
]

# 나란히 볼 기존 축 핵심값(verification._summ_ai_tell 정규식 코어와 동일 키)
OLD_KEYS = ["comma_per_100", "sent_len_cv", "lexical_mattr", "ending_diversity",
            "simile_per_1k", "past_run_max", "frag_ratio"]
NEW_KEYS = ["hits_per_1k", "total_hits", "categories_hit"]


def _measure(label: str, text: str) -> dict:
    """두 검증법 동일 조건 병렬 계측 — 1건."""
    old = ai_tell_profile(text)               # roster ∅(동일 조건 — 저장본과 미세 상이 가능·문서화)
    new = tell_lexicon_profile(text)
    return {"label": label, "n_chars": new["n_chars"],
            "old": {k: old.get(k) for k in OLD_KEYS},
            "new": {k: new.get(k) for k in NEW_KEYS},
            "by_category": {c: v["per_1k"] for c, v in new["by_category"].items()},
            "top_hits": new["top_hits"][:5]}


def _load_ai_corpus() -> list[tuple[str, str]]:
    """라이브 프로젝트 FINALIZED/ESCALATED 회차 전수 — (라벨, 본문)."""
    out = []
    for p in sorted((APP / "data" / "projects").glob("*.json")):
        if ".rag." in p.name or ".trace." in p.name:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        title = (d.get("title") or "").strip() or p.stem[:8]
        for ch in (d.get("chapters") or []):
            status = str(ch.get("status") or "").lower()
            text = ch.get("text") or ""
            if status in ("finalized", "escalated") and text.strip():
                out.append((f"{title} ch{ch.get('chapter')}", text))
    return out


def _band(vals: list) -> dict:
    """대역 원자료 — min/median/max/mean(n 병기). 수치 아닌 값 제외(방어적)."""
    xs = [v for v in vals if isinstance(v, (int, float))]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": round(min(xs), 3), "median": round(statistics.median(xs), 3),
            "max": round(max(xs), 3), "mean": round(statistics.mean(xs), 3)}


def _group_bands(rows: list[dict]) -> dict:
    """그룹(인간/AI)별 축 대역 — 두 검증법 + 범주별 per_1k(미적중 회차는 0으로 계수: 밀도 0 정직)."""
    bands = {"old": {k: _band([r["old"][k] for r in rows]) for k in OLD_KEYS},
             "new": {k: _band([r["new"][k] for r in rows]) for k in NEW_KEYS},
             "by_category": {c: _band([r["by_category"].get(c, 0.0) for r in rows])
                             for c in LEXICON}}
    return bands


def _fmt_band(b: dict) -> str:
    if not b.get("n"):
        return "—(n=0)"
    return f"{b['min']}~{b['max']} (med {b['median']}, n={b['n']})"


def main() -> int:
    human_rows, missing_ref = [], []
    for label, path in REF_FILES:
        if path.exists():
            human_rows.append(_measure(label, path.read_text(encoding="utf-8")))
        else:
            missing_ref.append(label)
    ai_rows = [_measure(lb, tx) for lb, tx in _load_ai_corpus()]

    result = {
        "ticket": "TL-1", "lexicon_version": LEXICON_VERSION,
        "human": {"rows": human_rows, "bands": _group_bands(human_rows) if human_rows else {},
                  "missing": missing_ref},
        "ai": {"rows": ai_rows, "bands": _group_bands(ai_rows) if ai_rows else {}},
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "tl1_tell_lexicon_ab.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 마크다운 리포트(수치 전 제공·판정 형태 금지) ─────────────────────────
    L = []
    L.append(f"# TL-1 — 기존(무사전 분포) vs 따온(14범주 사전) 검증법 A/B 계측\n")
    L.append(f"- 사전 버전: `{LEXICON_VERSION}` · 두 검증법 모두 roster ∅ 동일 조건 fresh 계산(LLM 0콜)")
    L.append(f"- 표본: 인간 대조군 {len(human_rows)}화"
             + (f"(누락: {', '.join(missing_ref)})" if missing_ref else "")
             + f" · AI 확정 회차 {len(ai_rows)}건")
    L.append("- 본 리포트는 원자료만 제공한다 — 어느 검증법이 나은지의 판정은 하지 않는다(무강제).\n")

    L.append("## 1. 그룹 대역 — 따온 검증법(사전 적중)\n")
    L.append("| 축 | 인간 대역 | AI 대역 |")
    L.append("|---|---|---|")
    for k in NEW_KEYS:
        hb = result["human"]["bands"].get("new", {}).get(k, {})
        ab = result["ai"]["bands"].get("new", {}).get(k, {})
        L.append(f"| {k} | {_fmt_band(hb)} | {_fmt_band(ab)} |")

    L.append("\n## 2. 그룹 대역 — 기존 검증법(참조 병기)\n")
    L.append("| 축 | 인간 대역 | AI 대역 |")
    L.append("|---|---|---|")
    for k in OLD_KEYS:
        hb = result["human"]["bands"].get("old", {}).get(k, {})
        ab = result["ai"]["bands"].get("old", {}).get(k, {})
        L.append(f"| {k} | {_fmt_band(hb)} | {_fmt_band(ab)} |")

    L.append("\n## 3. 범주별 밀도(per 1k자) — 어느 범주가 갈라놓는가(원자료)\n")
    L.append("| 범주 | 인간 대역 | AI 대역 |")
    L.append("|---|---|---|")
    for c in LEXICON:
        hb = result["human"]["bands"].get("by_category", {}).get(c, {})
        ab = result["ai"]["bands"].get("by_category", {}).get(c, {})
        L.append(f"| {c} | {_fmt_band(hb)} | {_fmt_band(ab)} |")

    L.append("\n## 4. 회차별 원자료(사전 축 + 기존 핵심 2축)\n")
    L.append("| 회차 | 자수 | hits/1k | 적중범주 | top needle | frag_ratio | ending_div |")
    L.append("|---|---|---|---|---|---|---|")
    for r in human_rows + ai_rows:
        top = ", ".join(f"{n}×{c}" for n, c in r["top_hits"][:3]) or "—"
        L.append(f"| {r['label']} | {r['n_chars']} | {r['new']['hits_per_1k']} "
                 f"| {r['new']['categories_hit']}/14 | {top} "
                 f"| {r['old']['frag_ratio']} | {r['old']['ending_diversity']} |")
    L.append("")
    (REPORTS / "tl1_tell_lexicon_ab.md").write_text("\n".join(L), encoding="utf-8")

    print(f"[TL-1] 인간 {len(human_rows)}화 · AI {len(ai_rows)}건 계측 완료")
    print(f"  -> {REPORTS / 'tl1_tell_lexicon_ab.md'}")
    print(f"  -> {REPORTS / 'tl1_tell_lexicon_ab.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
