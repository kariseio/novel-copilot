# -*- coding: utf-8 -*-
"""PL-2 — 조회 직렬화 위생(감사 F1~F5) 결정론 검사.

계약: ① 프로토콜 줄(미등재·재조회 안내)은 사서 대화 반환에는 남고(발명 차단 계약 유지),
[확정 설정] 직렬화(canon_facts_from_log)에는 실리지 않는다 ② 실패 응답에 동반된 캐논 문서는
직렬화에서 살아남는다(결과 단위 제외가 버리던 것 — 9화 실측) ③ 같은 문서 줄은 전 로그에서 1회만.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.engine.lookup import CanonLookup


class _Ent:
    def __init__(self, id, name, etype="character", aliases=()):
        self.id, self.name, self.etype = id, name, etype
        self.aliases = list(aliases)
        self.voice = ""


class _Ont:
    def __init__(self):
        e = _Ent("junho", "서준호", aliases=["준호"])
        self.entities = {"junho": e}
        self.edges = []

    def is_actor(self, etype):
        return etype == "character"

    def public_attrs(self, eid, chapter):
        return []

    def state_as_of(self, eid, attr, ch):
        return None


class _Bible:
    def __init__(self):
        self.title = "포획 구슬: 용법·한계"
        self.prose = "약해진 것을 빨아들여 가둔다."
        self.keywords = ["구슬", "포획"]
        self.status = "author_approved"   # XR-8: 캐논 직렬화 계약은 승인 항목의 것 — 상태 명시


def _mk():
    return CanonLookup(_Ont(), [_Bible()], 9)


def test_protocol_lines_stay_in_dialog_but_not_in_serialization():
    lk = _mk()
    res = lk.handle("lookup_canon", {"query": "호명 권한"})
    assert "미등재" in res                          # 사서 대화 계약 유지(발명 차단)
    facts, misses, _ = CanonLookup.canon_facts_from_log(lk.log)
    joined = "\n".join(v for _q, v in facts)
    assert "미등재" not in joined and "재조회" not in joined and "정확 일치 없음" not in joined
    assert misses == 1                              # 미등재는 신호로 계수(조용히 삼키지 않음)


def test_candidate_miss_keeps_companion_canon_docs():
    lk = _mk()
    res = lk.handle("lookup_canon", {"query": "서준호의 구슬"})   # 부분 일치 → 후보 안내 + bible 문서 동반
    assert "정확 일치 없음" in res and "포획 구슬" in res
    facts, _m, _d = CanonLookup.canon_facts_from_log(lk.log)
    joined = "\n".join(v for _q, v in facts)
    assert "포획 구슬: 용법·한계" in joined          # 동반 문서 생존(F1)
    assert "정확 일치 없음" not in joined            # 프로토콜 줄만 제외


def test_same_doc_line_serialized_once_across_queries():
    lk = _mk()
    lk.handle("lookup_canon", {"query": "구슬"})
    lk.handle("lookup_canon", {"query": "포획"})     # 같은 bible 문서 재히트
    facts, _m, deduped = CanonLookup.canon_facts_from_log(lk.log)
    joined = "\n".join(v for _q, v in facts)
    assert joined.count("포획 구슬: 용법·한계") == 1  # 전 로그 줄 단위 dedup(F2)
    assert deduped >= 1


def test_legacy_log_without_canon_field_falls_back():
    facts, _m, _d = CanonLookup.canon_facts_from_log([{"query": "q", "result": "구 로그 전문"}])
    assert facts == [("q", "구 로그 전문")]           # 하위호환(구 데이터 종전 동작)


if __name__ == "__main__":
    for fn in [test_protocol_lines_stay_in_dialog_but_not_in_serialization,
               test_candidate_miss_keeps_companion_canon_docs,
               test_same_doc_line_serialized_once_across_queries,
               test_legacy_log_without_canon_field_falls_back]:
        fn()
        print("PASS", fn.__name__)
