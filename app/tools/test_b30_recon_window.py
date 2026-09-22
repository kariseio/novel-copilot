# -*- coding: utf-8 -*-
"""B-30 검증 — 원장 지불 검출창 정직화(만기·고령 우선 + LRU 순환). LLM 0콜.

실측 결함(LR-1): reconcile 가 open 앞 20건만 LLM 에 전달 → open>20 이면 뒤 약속은 구조적으로
paid 판정 불가(잔고 55~62 인플레). 수정 계약: '영원히 검사 안 되는 open 약속' = 0 —
모든 open 이 최대 ceil(잔고/순환슬롯) 회차 내 반드시 검출창에 들어온다(결정론).
실행: PYTHONPATH=app python tools/test_b30_recon_window.py
"""
from __future__ import annotations
import math
import sys

from novelcopilot.domain.ledger import PromiseLedger, Promise
from novelcopilot.engine.ledger_ops import (select_reconcile_window, mark_window_checked,
                                            reconcile_ledger_from_prose)
from novelcopilot.llm.base import LLMProvider

WINDOW, PRI = 20, 12                       # 기본값(config ledger_recon_window/priority 와 동일)
ROT = WINDOW - PRI                         # 순환 슬롯 8


class ScriptFake(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses, self.calls = responses, 0

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class BoomFake(ScriptFake):
    def chat_json(self, messages, **k):
        raise TimeoutError("LLM timeout")


def _mk(n: int, opened: int = 1, prefix: str = "p") -> list[Promise]:
    return [Promise(id=f"{prefix}{i:03d}", text=f"약속 {prefix}{i:03d}", opened_chapter=opened)
            for i in range(n)]


def test_full_coverage_within_bound() -> None:
    """핵심 계약: open 60(>창 20) — 모든 약속이 ceil(순환풀 48/8)=6 회차 내 반드시 창 진입."""
    ops = _mk(60)
    seen: set[str] = set()
    for ch in range(1, 7):
        win = select_reconcile_window(ops, WINDOW, PRI)
        assert len(win) == WINDOW, f"창 크기 {len(win)} != {WINDOW}(토큰 비용 계약 위반)"
        mark_window_checked(win, ch)
        seen |= {p.id for p in win}
    missing = {p.id for p in ops} - seen
    print(f"[{'OK' if not missing else 'FAIL'}] 전수 커버리지: open 60 → 6회차 내 미검사 {len(missing)}건")
    assert not missing, f"6회차 내 미검사 잔존(기아): {sorted(missing)[:5]}"


def test_no_starvation_under_arrival_saturation() -> None:
    """개설 캡 포화(매회 +5) 폭주에서도 기아 0 — 신규는 LRU 뒤로만 들어오므로
    어떤 약속의 대기도 ceil(터치 시점 잔고/rot) 회차를 넘지 않는다(터치=개설 또는 직전 검사)."""
    ops = _mk(40)
    touch = {p.id: (0, len(ops)) for p in ops}              # pid -> (터치 회차, 터치 시점 잔고)
    worst = 0
    for ch in range(1, 31):
        win = select_reconcile_window(ops, WINDOW, PRI)
        for p in win:
            t, pool = touch[p.id]
            gap, bound = ch - t, math.ceil(pool / ROT)
            worst = max(worst, gap)
            assert gap <= bound, f"{p.id}: 대기 {gap} > 보장 {bound}(터치 ch{t}, 잔고 {pool})"
            touch[p.id] = (ch, len(ops))
        mark_window_checked(win, ch)
        arrivals = _mk(5, opened=ch, prefix=f"n{ch:02d}_")   # 개설 캡 5건/회 포화 재현
        ops += arrivals
        touch.update({p.id: (ch, len(ops)) for p in arrivals})
    # 시뮬 종료 시점에도 보장 초과 대기 중인 약속 0
    over = [pid for pid, (t, pool) in touch.items() if 30 - t > math.ceil(pool / ROT)]
    print(f"[{'OK' if not over else 'FAIL'}] 포화 무기아: 30회차·잔고 190, 최장 대기 {worst}, 보장 초과 {len(over)}건")
    assert not over, f"보장 초과 대기: {over[:5]}"


def test_due_imminent_always_in_window() -> None:
    """우선 세그먼트: 만기 임박 약속은 검사된 뒤에도 매회 창에 들어온다(LRU 로 밀려나지 않음)."""
    ops = _mk(60)
    ops[45].due_chapter = 5                                  # 유일한 만기 보유 → _due_key 최상위
    ok = True
    for ch in range(1, 9):
        win = select_reconcile_window(ops, WINDOW, PRI)
        ok &= any(p.id == ops[45].id for p in win)
        mark_window_checked(win, ch)
    print(f"[{'OK' if ok else 'FAIL'}] 만기 임박 상시 창 진입(8회차 연속)")
    assert ok, "만기 임박 약속이 창에서 탈락"


def test_determinism_and_purity() -> None:
    """결정론: 동일 상태 → 동일 선발 시퀀스. 순수성: select 는 상태 비변경(mark 만 커서 전진)."""
    a, b = _mk(35), _mk(35)
    seq_a, seq_b = [], []
    for ch in range(1, 6):
        wa, wb = select_reconcile_window(a, WINDOW, PRI), select_reconcile_window(b, WINDOW, PRI)
        seq_a.append([p.id for p in wa]); seq_b.append([p.id for p in wb])
        mark_window_checked(wa, ch); mark_window_checked(wb, ch)
    pure = _mk(30)
    select_reconcile_window(pure, WINDOW, PRI)
    ok = (seq_a == seq_b and all(p.last_checked_chapter == 0 for p in pure))
    print(f"[{'OK' if ok else 'FAIL'}] 결정론(시퀀스 일치)·순수성(select 무부작용)")
    assert ok, "선발 비결정 또는 select 가 상태를 변경"


def test_small_pool_and_misconfig_guard() -> None:
    """잔고 ≤ 창이면 전원 진입. 우선슬롯 오설정(pri>window)에도 순환 슬롯 ≥1 → 기아 0."""
    small = _mk(7)
    ok = {p.id for p in select_reconcile_window(small, WINDOW, PRI)} == {p.id for p in small}
    ops = _mk(12)
    seen: set[str] = set()
    for ch in range(1, 4):                                   # window=10, pri=15 → head 9 + 순환 1
        win = select_reconcile_window(ops, window=10, priority_slots=15)
        ok &= (len(win) == 10)
        mark_window_checked(win, ch)
        seen |= {p.id for p in win}
    ok &= (seen == {p.id for p in ops})                      # 비우선 3건이 3회차 내 순환 완주
    print(f"[{'OK' if ok else 'FAIL'}] 소규모 전원 진입·오설정 가드(rot>=1, 3회차 전수)")
    assert ok, "소규모 전원 진입 또는 오설정 가드 실패"


def test_reconcile_marks_checked_success_only() -> None:
    """배선: reconcile 성공 → 창 약속만 커서 전진 + 창 밖 id 는 증거가 실재해도 폐기(환각 규약).
    LLM 실패 회차는 미검사 유지(커서 비전진 — 실패가 커버리지로 집계되면 보장이 거짓말이 됨)."""
    led = PromiseLedger(promises=_mk(25))
    win_ids = {p.id for p in select_reconcile_window(led.open_promises(), WINDOW, PRI)}
    outside = sorted({p.id for p in led.promises} - win_ids)[0]
    inside = sorted(win_ids)[0]
    text = f"그는 약속 {inside}을 지켰다. 그리고 약속 {outside}도 지켰다."
    fake = ScriptFake([{"paid": [{"id": inside, "evidence": f"약속 {inside}을 지켰다"},
                                 {"id": outside, "evidence": f"약속 {outside}도 지켰다"}],
                        "opened": []}])
    recon = reconcile_ledger_from_prose(fake, text, led.open_promises(), 6)
    checked = {p.id for p in led.promises if p.last_checked_chapter == 6}
    ok = (recon["paid"] == [inside]                          # 창 밖 id = 폐기(보여준 약속만 유효)
          and checked == win_ids
          and all(p.last_checked_chapter == 0 for p in led.promises if p.id not in win_ids))
    recon2 = reconcile_ledger_from_prose(BoomFake([]), text, led.open_promises(), 7)
    ok &= (recon2 == {"paid": [], "opened": []}
           and not any(p.last_checked_chapter == 7 for p in led.promises))   # 실패 회차 = 커서 비전진
    print(f"[{'OK' if ok else 'FAIL'}] reconcile 배선: 성공만 커서 전진·창 밖 id 폐기·실패 미집계")
    assert ok, "reconcile 커서 전진/창 밖 폐기/실패 미집계 계약 위반"


def test_backcompat_old_json() -> None:
    """하위호환: last_checked_chapter 없는 구 JSON 이 기본 0(미검사)으로 로드 — 가산적 필드."""
    led = PromiseLedger.model_validate(
        {"promises": [{"id": "a", "text": "t", "opened_chapter": 2}], "last_payoff_chapter": 0})
    p = led.by_id("a")
    ok = (p is not None and p.last_checked_chapter == 0
          and [q.id for q in select_reconcile_window(led.open_promises(), WINDOW, PRI)] == ["a"])
    print(f"[{'OK' if ok else 'FAIL'}] 구 JSON 무변경 로드(last_checked=0)·선발 정상")
    assert ok, "구 JSON 하위호환 로드 실패"


def _run(fn) -> bool:
    try:
        fn()
        return True
    except AssertionError as e:
        print(f"[FAIL] {fn.__name__}: {e}")
        return False


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    results = [_run(t) for t in (test_full_coverage_within_bound,
                                 test_no_starvation_under_arrival_saturation,
                                 test_due_imminent_always_in_window,
                                 test_determinism_and_purity,
                                 test_small_pool_and_misconfig_guard,
                                 test_reconcile_marks_checked_success_only,
                                 test_backcompat_old_json)]
    print("\nB-30 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
