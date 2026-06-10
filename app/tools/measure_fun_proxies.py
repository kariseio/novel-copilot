# -*- coding: utf-8 -*-
"""재미 프록시 측정(결정론, LLM 0콜) — 인간 보정 전의 선행지표.

PoC 가 진단한 재미 실패축(페이싱·보이스·훅·복선회수)을 회차별 숫자로:
- dialogue_ratio: 대사 줄 비율(보이스/속도감 프록시)
- hook: 회차 말미 절단 훅 존재(미결 긴장 표지)
- event_density: 비트 핵심사건 수 / 1,000자(페이싱)
- payoff_recovery: spine 의 plant 대비 payoff 회수율
실행: PYTHONPATH=app python tools/measure_fun_proxies.py <project_id>
"""
from __future__ import annotations
import re
import sys

from novelcopilot.config import get_settings
from novelcopilot.repository import FilesystemProjectRepository

_HOOK_PAT = re.compile(r"(\?|…|—|그때|순간|마지막으로|것이다\.$|는데\.$|뿐이었다\.$)")


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(re.findall(r"[가-힣A-Za-z0-9]+", a)), set(re.findall(r"[가-힣A-Za-z0-9]+", b))
    return len(ta & tb) / max(1, len(ta | tb))


def measure(state) -> dict:
    rows = []
    prev_hooks: list[str] = []
    for c in state.chapters:
        lines = [ln for ln in c.text.splitlines() if ln.strip()]
        dlg = sum(1 for ln in lines if ln.lstrip().startswith(('"', '“', "—", "'")))
        tail = " ".join(lines[-3:]) if lines else ""
        hook = bool(_HOOK_PAT.search(tail))
        # 반복 훅 페널티: 직전 회차들의 훅과 토큰 유사도 높으면 '가짜/재탕 훅'(음의 신호) — 단순 표면 매칭 보정
        repeated = hook and any(_jaccard(tail, h) >= 0.6 for h in prev_hooks)
        if hook:
            prev_hooks.append(tail)
        beat = next((b for b in state.world.beats if b.chapter == c.chapter), None)
        n_events = len(beat.key_events) if beat else 0
        rows.append({"chapter": c.chapter, "chars": len(c.text),
                     "dialogue_ratio": round(dlg / max(1, len(lines)), 2),
                     "hook": hook and not repeated, "hook_repeated": repeated,
                     "event_density": round(n_events / max(1, len(c.text)) * 1000, 2)})
    plants = payoffs = 0
    if state.world.spine:
        for a in state.world.spine.arcs:
            for ep in a.episodes:
                plants += len(ep.plants)
                payoffs += len(ep.payoffs)
    return {"chapters": rows,
            "payoff_recovery": round(payoffs / max(1, plants), 2) if plants else None,
            "hook_rate": round(sum(1 for r in rows if r["hook"]) / max(1, len(rows)), 2),
            "avg_dialogue_ratio": round(sum(r["dialogue_ratio"] for r in rows) / max(1, len(rows)), 2)}


if __name__ == "__main__":
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    pid = sys.argv[1] if len(sys.argv) > 1 else None
    states = [repo.get(pid)] if pid else [repo.get(x["id"]) for x in repo.list_summaries()]
    for st in filter(None, states):
        rep = measure(st)
        print(f"\n[{st.world.title}] 회차 {len(rep['chapters'])} | 훅비율 {rep['hook_rate']} | "
              f"평균 대사비율 {rep['avg_dialogue_ratio']} | 복선회수율 {rep['payoff_recovery']}")
        for r in rep["chapters"]:
            print(f"  {r['chapter']}화: {r['chars']}자 대사 {r['dialogue_ratio']} 훅 {'O' if r['hook'] else 'X'} "
                  f"사건밀도 {r['event_density']}/천자")
