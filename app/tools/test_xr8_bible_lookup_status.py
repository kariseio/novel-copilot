# -*- coding: utf-8 -*-
"""XR-8 검증 — 선조회 설정집 상태 필터 (LLM 0콜).

계약(cross-review/007 §6):
  · deprecated·draft 조회 결과 0건.
  · author_approved 만 캐논(=[확정 설정] 직렬화 대상).
  · ai_unreviewed 는 응답에 비구속 참고로 실리되 canon_facts_from_log 직렬화에서 제외.
  · 조회 로그에 entry id·status·tier 기록.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr8_bible_lookup_status.py
"""
from __future__ import annotations
import sys
from types import SimpleNamespace as NS

from novelcopilot.engine.lookup import CanonLookup


class _Ont:
    entities: dict = {}
    edges: list = []

    def is_actor(self, t):
        return False

    def public_attrs(self, eid, ch):
        return []


def _entry(eid, title, status, prose="설정 서술", keywords=None):
    return NS(entry_id=eid, title=title, status=status, prose=prose,
              keywords=keywords or [title])


def _lk(entries):
    return CanonLookup(_Ont(), entries, 9)


def test_status_channels() -> bool:
    lk = _lk([_entry("a1", "봉인 부적", "author_approved", prose="승인 프로즈"),
              _entry("u1", "괴담관리국", "ai_unreviewed", prose="미검수 프로즈", keywords=["괴담관리국", "부적"]),
              _entry("d1", "옛 부적 설정", "deprecated", keywords=["부적"]),
              _entry("r1", "초안 부적", "draft", keywords=["부적"])])
    out = lk.handle("lookup_canon", {"query": "부적"})
    ok = "봉인 부적" in out and "승인 프로즈" in out                       # 승인=캐논 응답(종전 표기)
    ok &= "(비구속 참고 — 작가 미확정)" in out and "미검수 프로즈" in out   # 미검수=비구속 참고 채널
    ok &= "옛 부적 설정" not in out and "초안 부적" not in out             # deprecated·draft 0건
    facts, misses, _ = CanonLookup.canon_facts_from_log(lk.log)
    joined = "\n".join(v for _, v in facts)
    ok &= "승인 프로즈" in joined and "미검수 프로즈" not in joined         # [확정 설정] 직렬화=승인만
    rec = lk.log[-1]
    ok &= {m["id"]: (m["status"], m["tier"]) for m in rec["bible"]} == \
        {"a1": ("author_approved", "binding"), "u1": ("ai_unreviewed", "narrative")}   # 감사 채널
    print(f"[{'OK' if ok else 'FAIL'}] 상태 채널: 승인=캐논·미검수=비구속 참고·폐기/초안 0건·직렬화 승인만·로그 기록")
    assert ok
    return ok


def test_unreviewed_only_is_not_missing() -> bool:
    lk = _lk([_entry("u1", "괴담관리국", "ai_unreviewed", prose="미검수 프로즈")])
    out = lk.handle("lookup_canon", {"query": "괴담관리국"})
    ok = "미등재" not in out and "(비구속 참고 — 작가 미확정)" in out   # 정보 실재 — 거짓 미등재 금지
    facts, _, _ = CanonLookup.canon_facts_from_log(lk.log)
    ok &= facts == []                                                   # 직렬화는 여전히 0
    lk2 = _lk([_entry("d1", "옛 설정", "deprecated")])
    out2 = lk2.handle("lookup_canon", {"query": "옛 설정"})
    ok &= "미등재" in out2                                              # 제외 항목뿐이면 미등재(정직)
    print(f"[{'OK' if ok else 'FAIL'}] 미검수 단독=비구속 응답(미등재 아님) · 폐기 단독=미등재")
    assert ok
    return ok


def test_missing_status_defaults_unreviewed_and_cap() -> bool:
    lk = _lk([NS(title="무상태 항목", keywords=["무상태"], prose="p")])   # status 결측 덕타이핑
    out = lk.handle("lookup_canon", {"query": "무상태"})
    ok = "(비구속 참고 — 작가 미확정)" in out                            # 도메인 기본값과 동일(정직 폴백)
    lk2 = _lk([_entry(f"a{i}", f"승인{i}", "author_approved", keywords=["공통"]) for i in range(3)]
              + [_entry("u9", "미검수9", "ai_unreviewed", keywords=["공통"])])
    out2 = lk2.handle("lookup_canon", {"query": "공통"})
    ok &= out2.count("〔") == 2 and "미검수9" not in out2                # 종전 총 2건 상한·승인 우선
    # 009 §3 경미: meta 는 실제 응답에 실린 항목만(응답=계보 정합 — 상한 초과 후보 미기록)
    ok &= [m["id"] for m in lk2.log[-1]["bible"]] == ["a0", "a1"]
    print(f"[{'OK' if ok else 'FAIL'}] 결측 status=미검수 폴백 · 총 2건 상한(승인 우선) · meta=응답 정합")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_status_channels(), test_unreviewed_only_is_not_missing(),
               test_missing_status_defaults_unreviewed_and_cap()]
    print("\nXR-8 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
