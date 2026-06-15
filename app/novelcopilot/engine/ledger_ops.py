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


# ---------- P2: 본문 상환 검출(측정 — 생성 주입 아님) ----------
def detect_payoffs(provider, text: str, open_promises: list[Promise], chapter: int) -> list[str]:
    """G1-P2: 이번 회차 본문에서 '실제로 지불된' 약속을 검출(측정만). 반환=지불된 promise id 목록.

    증거 스팬 강제 — LLM 이 evidence(본문 인용)를 달고, 코드가 그 구절이 본문에 실재하는지 검증해 환각 폐기.
    설계 라벨 일치(P1)가 아니라 '본문이 실제로 지불했는가'를 보므로 since_payoff 카운터가 실데이터가 된다.
    narrative_inferred(기계추출) — 원장은 비구속 회계라 회차 확정/검증을 막지 않는다(비대칭 보존).
    """
    if not open_promises or not (text or "").strip():
        return []
    import json
    items = [{"id": p.id, "약속": p.text[:120]} for p in open_promises[:20]]
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              "웹소설 편집자. 아래 '독자에게 한 약속'들이 이번 회차 본문에서 '실제로 지불(회수·공개·달성·해소)됐는지' 판정하라. "
              "추측·예고는 제외 — 본문에서 명백히 일어난 것만. 각 지불 항목에 evidence(본문에서 그대로 복사한 구절)를 달아라. "
              '없으면 빈 배열. JSON: {"paid":[{"id":"","evidence":"본문 인용"}]}'},
             {"role": "user", "content": f"[약속]{json.dumps(items, ensure_ascii=False)}\n[이번 회차 본문]\n{text[:9000]}"}],
            temperature=0.0, max_tokens=1200)
        valid = {p.id for p in open_promises}
        out: list[str] = []
        for it in (r.get("paid") or []):
            pid, ev = it.get("id"), (it.get("evidence") or "").strip()
            if pid in valid and len(ev) >= 6 and ev[:50] in text and pid not in out:   # 증거 실재 검증(환각 폐기)
                out.append(pid)
        return out
    except Exception:
        return []


def mark_paid(ledger: PromiseLedger, paid_ids: list[str], chapter: int) -> int:
    """검출된 지불을 원장에 반영 — last_payoff_chapter 전진(결정론 카운터 실데이터화). 반환=실제 지불 처리 수."""
    n = 0
    for pid in paid_ids:
        p = ledger.by_id(pid)
        if p is not None and p.status == "open":
            p.status, p.paid_chapter = "paid", chapter
            n += 1
    if n:
        ledger.last_payoff_chapter = max(ledger.last_payoff_chapter, chapter)
    return n
