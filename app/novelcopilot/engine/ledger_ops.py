# -*- coding: utf-8 -*-
"""약속 원장 결정론 연산 (G1-P1) — LLM 0콜.

설계 라벨(spine 의 plants/payoffs)을 원장으로 미러하고, '마지막 지불 후 경과 회차' 같은
결정론 카운터를 산출한다. 설계 의도를 1급 생애주기로 승격(plants 는 에피소드 스코프 자유 문자열이라
회차·아크를 횡단하는 추적이 불가능했음). 본문 지불 추출은 P2.
"""
from __future__ import annotations
import re

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..domain.ledger import PromiseLedger, Promise


def _key(label: str) -> str:
    """라벨 정규화 키(공백·대소문자 무시, 앞 40자) — 중복 약속/지불 매칭의 결정론 기준."""
    return re.sub(r"\s+", "", (label or "").lower())[:40]


def _episode_windows(spine) -> dict:
    """에피소드별 예상 화 구간 {episode_id: (start, end)} — 누적 target_chapters 산술(결정론·LLM 0).

    AC-2ⓐ: 만기 파생의 재료. 실제 배정과 어긋날 수 있는 '예산' 수치다 — 만기는 강제가 아니라
    가시화·정렬용이므로(Promise.due_chapter 계약) 예산 산술로 충분하다."""
    out: dict = {}
    ch = 1
    for a in sorted(getattr(spine, "arcs", None) or [], key=lambda x: x.order):
        for e in sorted(a.episodes, key=lambda x: x.order):
            n = max(1, int(getattr(e, "target_chapters", 1) or 1))
            out[e.episode_id] = (ch, ch + n - 1)
            ch += n
    return out


def _due_in_window(win: tuple, payoff_at: str) -> int:
    """payoff_at(early|mid|climax·자유 라벨) → 에피소드 화 구간 내 만기 위치. 미지정=말미(절정 관행)."""
    s, e = win
    pa = (payoff_at or "").strip().lower()
    if pa == "early":
        return s
    if pa == "mid":
        return (s + e) // 2
    return e


def sync_ledger_from_spine(ledger: PromiseLedger, spine, current_chapter: int) -> int:
    """spine 의 plants/payoffs(설계 라벨) → 원장 미러(가산적). 반환=새로 열린 약속 수.

    - 미등록 plant → open Promise(opened_chapter=현재) 추가(소급 정확도는 주장하지 않음 — 첫 관측 회차로 박음).
    - payoffs 라벨과 키 일치하는 open 약속 → paid 마킹(P1 한계: 설계 라벨 일치만, 본문 추출은 P2).
    - AC-2ⓐ(2026-08-18): 만기 파생 — 어떤 에피소드가 이 약속을 payoffs 로 예정하고 있으면, 그 에피소드의
      예산 화 구간(payoff_at 위치 보정)이 만기다. due_chapter 는 정렬·telemetry 가 이미 소비하는데 세터가
      프로덕션 0건이던 구멍(전 원장 97건 due=None 실측). 출처="spine_arith"는 재계획 시 갱신(멱등),
      "author"는 불변(작가 오버라이드 보존·무강제). 산출 없는 약속(본문 개설 등 payoffs 미예정)은
      None 유지 — 만기 발명 금지.
    """
    if spine is None or not getattr(spine, "arcs", None):
        return 0
    paid_keys = {_key(p) for a in spine.arcs for e in a.episodes for p in (e.payoffs or []) if p}
    # 만기 지도: payoff 키 → 그 payoff 를 예정한 에피소드의 예산 구간 내 위치(여러 에피소드면 가장 이른 만기)
    wins = _episode_windows(spine)
    due_map: dict = {}
    for a in spine.arcs:
        for e in a.episodes:
            w = wins.get(getattr(e, "episode_id", ""))
            if not w:
                continue
            for p in (e.payoffs or []):
                k = _key(p)
                if not k:
                    continue
                d = _due_in_window(w, getattr(e, "payoff_at", ""))
                due_map[k] = min(due_map[k], d) if k in due_map else d
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
                # 만기는 상태 무관 이력(paid 여도 '계획상 만기 X'는 사실) — 단 작가 지정은 불변.
                #   주의(실측 갭·PM 회부): 같은 신호(payoffs 라벨)를 아래 P1 이 '회수됨(즉시 paid)'으로
                #   읽어 open+due 조합이 sync 경로에서 구조적으로 성립하지 않는다. 계획=실행 혼동의
                #   P1 레거시로 보이나 기존 계약(test_ledger_sync 잠금)이라 여기서 바꾸지 않는다.
                if pid in due_map and pr.due_source != "author":
                    pr.due_chapter = due_map[pid]
                    pr.due_source = "spine_arith"
                if pr.status == "open" and pid in paid_keys:   # 설계 라벨 회수
                    pr.status = "paid"
                    pr.paid_chapter = current_chapter
                    ledger.last_payoff_chapter = max(ledger.last_payoff_chapter, current_chapter)
    return opened


def _due_key(p: Promise) -> tuple:
    """만기 임박 → 고령(개설 오래됨) → id 사전순 — 우선 세그먼트/정렬의 결정론 전순서."""
    return ((p.due_chapter if p.due_chapter is not None else 10 ** 9), p.opened_chapter, p.id)


def outstanding(ledger: PromiseLedger, current_chapter: int) -> list[Promise]:
    """미지불 약속 — 만기 임박/오래된 것 우선(비트 설계 컨텍스트 주입용)."""
    return sorted(ledger.open_promises(), key=_due_key)


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
            "oldest_open_age": oldest_age, "since_payoff": chapters_since_payoff(ledger, current_chapter),
            # AC-2ⓐ: 만기 커버리지 가시화 — due 를 아는 미지불 약속 수·최근접 만기(임계·강제 0)
            "due_known": sum(1 for p in op if p.due_chapter is not None),
            "nearest_due": min((p.due_chapter for p in op if p.due_chapter is not None), default=None)}


# ---------- B-30: 지불 검출창 선발 — 만기·고령 우선 + LRU 순환(구조적 기아 0) ----------
def select_reconcile_window(open_promises: list[Promise], window: int = 20,
                            priority_slots: int = 12) -> list[Promise]:
    """지불 검출창 선발 — LR-1 실측 결함 소스차단: 구 코드는 open 앞 20건만 LLM 에 전달해
    open>20 이면 그 뒤 약속이 '구조적으로 영원히' paid 판정 불가 → 잔고 55~62 인플레 아티팩트.

    구성(창 크기 불변 = 현행 토큰 비용 유지):
      · 우선 세그먼트(priority_slots): 만기 임박·고령 우선(_due_key) — 매회 검사.
      · 순환 세그먼트(window-priority, 최소 1 보장): '마지막 터치' LRU(최장 미검사 우선).
    기아 0 증명 스케치 — 순환 키 = 검사 시 2·last_checked+1, 미검사면 2·opened(개설도 터치).
    검사 키(2c+1)는 같은 회차 개설 키(2c)보다 항상 뒤 → 동회차 동률 id 재선발 고착 없음.
    키는 터치 시에만 현재 회차 기준으로 전진하므로 어떤 open 약속 P 의 앞줄(키 ≤ P)에는
    새 항목이 유한 건(당회 개설분)만 끼어들고, 매회 앞줄에서 rot 건 이상이 빠진다(검사=키 전진, 지불=이탈)
    → P 는 최대 ceil(앞줄 수/rot) ≤ ceil(open 잔고/rot) 회차 내 반드시 창 진입(결정론).
    """
    ops = list(open_promises or [])
    window = max(1, int(window))
    if len(ops) <= window:
        return sorted(ops, key=_due_key)                     # 전원 창 진입(순서만 결정론 안정화)
    rot = max(1, window - max(0, int(priority_slots)))       # 순환 슬롯 ≥1 — 우선슬롯 오설정에도 기아 0
    head = sorted(ops, key=_due_key)[: window - rot]
    head_ids = {p.id for p in head}
    tail = sorted((p for p in ops if p.id not in head_ids),
                  key=lambda p: ((2 * p.last_checked_chapter + 1) if p.last_checked_chapter
                                 else 2 * p.opened_chapter, p.opened_chapter, p.id))
    return head + tail[:rot]


def mark_window_checked(promises: list[Promise], chapter: int) -> None:
    """창에 들어가 '실제 검사된' 약속의 LRU 커서 전진 — LLM 콜 성공 시에만 호출(실패 회차=미검사 유지)."""
    for p in promises:
        p.last_checked_chapter = chapter


# ---------- P2: 본문 상환 검출(측정 — 생성 주입 아님) ----------
@promptlog.stage("ledger_payoff")
def detect_payoffs(provider, text: str, open_promises: list[Promise], chapter: int,
                   window: int = 20, priority_slots: int = 12) -> list[str]:
    """G1-P2: 이번 회차 본문에서 '실제로 지불된' 약속을 검출(측정만). 반환=지불된 promise id 목록.

    증거 스팬 강제 — LLM 이 evidence(본문 인용)를 달고, 코드가 그 구절이 본문에 실재하는지 검증해 환각 폐기.
    설계 라벨 일치(P1)가 아니라 '본문이 실제로 지불했는가'를 보므로 since_payoff 카운터가 실데이터가 된다.
    narrative_inferred(기계추출) — 원장은 비구속 회계라 회차 확정/검증을 막지 않는다(비대칭 보존).
    """
    if not open_promises or not (text or "").strip():
        return []
    import json
    win = select_reconcile_window(open_promises, window, priority_slots)   # B-30: 앞 20건 고정 → 순환창
    items = [{"id": p.id, "약속": p.text} for p in win]   # 절단 전면 제거(2026-08-21): 약속 전문(창 크기는 B-30 리스트 캡이 담당)
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              "웹소설 편집자. 아래 '독자에게 한 약속'들이 이번 회차 본문에서 '실제로 지불(회수·공개·달성·해소)됐는지' 판정하라. "
              "추측·예고는 제외 — 본문에서 명백히 일어난 것만. 각 지불 항목에 evidence(본문에서 그대로 복사한 구절)를 달아라. "
              '없으면 빈 배열. JSON: {"paid":[{"id":"","evidence":"본문 인용"}]}'},
             {"role": "user", "content": f"[약속]{json.dumps(items, ensure_ascii=False)}\n[이번 회차 본문]\n{text}"}],   # 절단 전면 제거(2026-08-21): 구 9,000자 머리 절단 = 회차말 지불 장면 사각
            temperature=0.0)
        mark_window_checked(win, chapter)                    # 콜 성공 → 이 창은 검사됨(LRU 커서 전진)
        valid = {p.id for p in win}                          # 창에 보여준 약속만 유효(창 밖 id=환각)
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


# ---------- P3: 본문이 '연' 약속 추출 + 지불 정산(한 콜) ----------
@promptlog.stage("ledger_reconcile")
def reconcile_ledger_from_prose(provider, text: str, open_promises: list[Promise], chapter: int,
                                window: int = 20, priority_slots: int = 12) -> dict:
    """G1-P3+P2: 본문을 한 번 읽고 (a) 기존 약속 중 지불된 것 (b) 이 회차가 '새로 연' 약속을 함께 추출.

    원장을 '설계 라벨(plant)'이 아니라 '본문이 실제로 독자에게 한 약속'으로 채운다 — 이 회차가 열어둔,
    독자가 다음을 궁금해하게 만든 미해결 기대. 증거 스팬 강제(지불은 본문 인용 검증→환각 폐기). 추가 LLM 콜 0(상환 검출과 같은 콜).
    검출창은 select_reconcile_window(B-30) — 만기·고령 우선 + LRU 순환으로 모든 open 이 유한 회차 내 검사됨.
    반환 {"paid": [id...], "opened": [{"text","kind"}...]}. narrative_inferred — 원장은 비구속 회계.
    """
    if not (text or "").strip():
        return {"paid": [], "opened": []}
    import json
    win = select_reconcile_window(open_promises or [], window, priority_slots)   # B-30: 앞 20건 고정 → 순환창
    items = [{"id": p.id, "약속": p.text} for p in win]   # 절단 전면 제거(2026-08-21): 약속 전문
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              "웹소설 편집자. 이 회차 본문을 읽고 두 가지를 뽑아라:\n"
              "① paid: 아래 '기존 약속' 중 이번 회차에서 '실제로 지불(회수·공개·달성·해소)된 것'. 각 항목에 "
              "evidence(본문에서 그대로 복사한 구절) 필수. 추측·예고는 제외.\n"
              "② opened: 이번 회차가 '새로 연 약속' 0~5개 — 이 회차가 열어두어 독자가 다음을 궁금해하게 만든 미해결 기대. "
              "text(한 줄)와 kind(이 작품의 톤·상황에 맞게, 그 약속이 어떤 종류의 기대인지 짧은 한 단어로 직접 명명)로. 이미 있던 약속의 재언급은 제외.\n"
              '없으면 빈 배열. JSON: {"paid":[{"id":"","evidence":""}],"opened":[{"text":"","kind":""}]}'},
             {"role": "user", "content": f"[기존 약속]{json.dumps(items, ensure_ascii=False)}\n[이번 회차 본문]\n{text}"}],   # 절단 전면 제거(2026-08-21): 회차말 지불/개설 사각 소거
            temperature=0.0)
        mark_window_checked(win, chapter)                    # 콜 성공 → 이 창은 검사됨(LRU 커서 전진)
        valid = {p.id for p in win}                          # 창에 보여준 약속만 유효(창 밖 id=환각)
        paid: list[str] = []
        for it in (r.get("paid") or []):
            pid, ev = it.get("id"), (it.get("evidence") or "").strip()
            if pid in valid and len(ev) >= 6 and ev[:50] in text and pid not in paid:   # 증거 실재 검증
                paid.append(pid)
        opened = []
        # 개설 캡(5건/회): LR-1 실측서 거의 매회 포화 — 검출창 기아(B-30 해소)와 별개로 잔고 인플레의
        # '다른 축'(유입 과다). B-30 범위 밖(기록만) — 캡 조정/개설 억제·중복 병합은 후속 티켓에서 다룬다.
        for it in (r.get("opened") or [])[:5]:
            t = (it.get("text") or "").strip()
            if t:
                opened.append({"text": t[:200], "kind": (it.get("kind") or "").strip()[:12]})
        return {"paid": paid, "opened": opened}
    except Exception:
        return {"paid": [], "opened": []}


def add_opened_promises(ledger: PromiseLedger, opened: list[dict], chapter: int) -> int:
    """본문이 연 약속을 원장에 등록(가산적·멱등). 반환=신규 등록 수."""
    n = 0
    for o in (opened or []):
        text = (o.get("text") or "").strip()
        pid = _key(text)
        if not pid or ledger.by_id(pid) is not None:
            continue
        ledger.promises.append(Promise(id=pid, text=text[:200], opened_chapter=max(1, chapter),
                                       kind=(o.get("kind") or "")))
        n += 1
    return n
