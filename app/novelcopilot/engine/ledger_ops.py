# -*- coding: utf-8 -*-
"""약속 원장 결정론 연산 (G1-P1) — LLM 0콜.

설계 라벨(spine 의 plants/payoffs)을 원장으로 미러하고, '마지막 지불 후 경과 회차' 같은
결정론 카운터를 산출한다. 설계 의도를 1급 생애주기로 승격(plants 는 에피소드 스코프 자유 문자열이라
회차·아크를 횡단하는 추적이 불가능했음). 본문 지불 추출은 P2.
"""
from __future__ import annotations
import re

from ..domain.ledger import PromiseLedger, Promise


def _key(label: str) -> str:
    """라벨 정규화 키(공백·대소문자 무시, 앞 40자) — 중복 약속/지불 매칭의 결정론 기준."""
    return re.sub(r"\s+", "", (label or "").lower())[:40]


def sync_ledger_from_spine(ledger: PromiseLedger, spine, current_chapter: int) -> int:
    """spine 의 plants/payoffs(설계 라벨) → 원장 미러(가산적). 반환=새로 열린 약속 수.

    - 미등록 plant → open Promise(opened_chapter=현재) 추가(소급 정확도는 주장하지 않음 — 첫 관측 회차로 박음).
    - payoffs 라벨과 키 일치하는 open 약속 → paid 마킹(P1 한계: 설계 라벨 일치만, 본문 추출은 P2).
    """
    if spine is None or not getattr(spine, "arcs", None):
        return 0
    paid_keys = {_key(p) for a in spine.arcs for e in a.episodes for p in (e.payoffs or []) if p}
    opened = 0
    for a in sorted(spine.arcs, key=lambda x: x.order):
        for e in a.episodes:
            for label in (e.plants or []):
                pid = _key(label)
                if not pid:
                    continue
                pr = ledger.by_id(pid)
                if pr is None:
                    pr = Promise(id=pid, text=label[:200], opened_chapter=max(1, current_chapter))
                    ledger.promises.append(pr)
                    opened += 1
                if pr.status == "open" and pid in paid_keys:   # 설계 라벨 회수
                    pr.status = "paid"
                    pr.paid_chapter = current_chapter
                    ledger.last_payoff_chapter = max(ledger.last_payoff_chapter, current_chapter)
    return opened


def outstanding(ledger: PromiseLedger, current_chapter: int) -> list[Promise]:
    """미지불 약속 — 만기 임박/오래된 것 우선(비트 설계 컨텍스트 주입용)."""
    op = ledger.open_promises()
    return sorted(op, key=lambda p: ((p.due_chapter if p.due_chapter is not None else 10 ** 9),
                                     p.opened_chapter))


def chapters_since_payoff(ledger: PromiseLedger, current_chapter: int) -> int | None:
    """마지막 확정 지불 후 경과 회차(결정론 텔레메트리). 지불 이력 없으면 None(P1=설계 라벨 기준)."""
    if not any(p.status == "paid" for p in ledger.promises):
        return None
    return max(0, current_chapter - ledger.last_payoff_chapter)


def ledger_telemetry(ledger: PromiseLedger, current_chapter: int) -> dict:
    """작가 가시화용 요약 — 미지불 잔고/최고령 약속 나이/마지막 지불 후 경과."""
    op = ledger.open_promises()
    oldest_age = max((current_chapter - p.opened_chapter for p in op), default=0)
    return {"open": len(op), "paid": sum(1 for p in ledger.promises if p.status == "paid"),
            "oldest_open_age": oldest_age, "since_payoff": chapters_since_payoff(ledger, current_chapter)}
