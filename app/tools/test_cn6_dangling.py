# -*- coding: utf-8 -*-
"""CN-6 bounded-dangling 테스트 — 결정론 트리거(이름 부재)·bounded·브랜드뉴 무잡음·as_of 자기제외. 실 LLM 0콜(fake rag).

핵심 안전성(no-whack-a-mole): 트리거는 사전/어휘가 아니라 '비트 지목 인물명이 현 맥락에 없다'는 구조 신호.
과거에 이름이 없는(≥2자) 신규 인물은 검색 결과에 이름이 안 잡혀 hits=∅ → 잡음 미유입. 1글자 이름은
흔한 형태소와 substring 충돌하므로 어간≥2 가드로 트리거서 제외(korean-agglutination-substring-pitfall)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.domain.types import RetrievedItem


class FakeRag:
    """query(=인물명) → 해당 이름을 포함/미포함한 과거 청크. as_of 인자 기록."""
    def __init__(self, by_query):
        self.by_query = by_query          # {name: [RetrievedItem, ...]}
        self.calls = []                    # (query, as_of, k)

    def search(self, q, as_of, k=4):
        self.calls.append((q, as_of, k))
        return list(self.by_query.get(q, []))[:k]


def _onto(entities: dict):
    """{id: name} → ontology-유사 객체(.entities[id].name)."""
    return SimpleNamespace(entities={i: SimpleNamespace(name=nm) for i, nm in entities.items()})


def _item(ch, text):
    return RetrievedItem(source="rag_chunk", ref=str(ch), text=text)


def test_no_search_when_names_present():
    # 지목 인물명이 모두 현 맥락에 있으면 dangling 없음 → 검색 0콜
    rag = FakeRag({"레오": [_item(2, "레오가 왔다")]})
    onto = _onto({"e_leo": "레오"})
    hits, resolved = ChapterGenerator._resolve_dangling(["e_leo"], onto, "이번 화에 레오가 등장한다", rag, 5)
    assert hits == [] and resolved == [] and rag.calls == []


def test_dangling_resolved_when_absent():
    # 레오가 비트에 지목됐지만 현 맥락 어디에도 이름이 없음 → 과거 검색으로 보강
    rag = FakeRag({"레오": [_item(2, "레오는 남부 출신 검객이었다")]})
    onto = _onto({"e_leo": "레오"})
    hits, resolved = ChapterGenerator._resolve_dangling(["e_leo"], onto, "낯선 방문객이 문을 두드렸다", rag, 5)
    assert resolved == ["레오"] and len(hits) == 1 and "레오" in hits[0].text
    assert rag.calls[0][1] == 4          # as_of = ch_no-1 (자기 회차 제외)


def test_brandnew_entity_no_noise():
    # 신규 인물: 과거에 이름이 없음 → 검색은 유사도 top-k 반환하나 '이름 미포함'이라 전량 폐기(잡음 0)
    rag = FakeRag({"신규조력자": [_item(2, "무관한 옛 장면"), _item(3, "또 다른 배경")]})
    onto = _onto({"e_new": "신규조력자"})
    hits, resolved = ChapterGenerator._resolve_dangling(["e_new"], onto, "새 인물이 등장", rag, 5)
    assert hits == [] and resolved == []      # 부작용 없음 — 핵심 안전성


def test_bounded_cap():
    # dangling 3인이 모두 과거 hit 가 있어도 cap=2 만 검색/보강
    rag = FakeRag({n: [_item(2, f"{n}의 과거 장면")] for n in ("가온", "나래", "다온")})
    onto = _onto({"a": "가온", "b": "나래", "c": "다온"})
    hits, resolved = ChapterGenerator._resolve_dangling(["a", "b", "c"], onto, "빈 맥락", rag, 5, cap=2)
    assert len(resolved) == 2 and len({q for q, *_ in rag.calls}) == 2


def test_per_k_cap():
    # 한 인물에 과거 청크가 많아도 인당 per_k 개로 제한
    rag = FakeRag({"레오": [_item(c, f"레오 {c}회차 장면") for c in range(2, 8)]})
    onto = _onto({"e_leo": "레오"})
    hits, _ = ChapterGenerator._resolve_dangling(["e_leo"], onto, "빈 맥락", rag, 9, per_k=2)
    assert len(hits) == 2


def test_dedup_same_name():
    # 같은 인물이 involved 에 중복돼도 1회만 검색
    rag = FakeRag({"레오": [_item(2, "레오 과거")]})
    onto = _onto({"e_leo": "레오", "e_leo2": "레오"})
    hits, resolved = ChapterGenerator._resolve_dangling(["e_leo", "e_leo2"], onto, "빈 맥락", rag, 5)
    assert resolved == ["레오"] and len([c for c in rag.calls if c[0] == "레오"]) == 1


def test_nameless_or_missing_entity_skipped():
    # 이름 없는/미등록 엔티티는 트리거 대상 아님(검색 0콜)
    rag = FakeRag({})
    onto = _onto({"e_blank": ""})
    hits, resolved = ChapterGenerator._resolve_dangling(["e_blank", "e_absent"], onto, "빈 맥락", rag, 5)
    assert hits == [] and resolved == [] and rag.calls == []


def test_short_name_guarded():
    # 어간≥2 가드(적대검증 MED): 1글자 이름 '강'은 흔한 형태소('강물'/'강하게')와 substring 충돌 → 트리거 제외(잡음 미유입).
    #  실제 인물명은 2자 이상. 이 가드가 브랜드-뉴 무잡음 보장을 '이름 ≥2자' 조건에서 참이 되게 한다.
    rag = FakeRag({"강": [_item(2, "강물이 크게 불어났다"), _item(3, "그는 강하게 반발했다")]})
    onto = _onto({"e_river": "강"})
    hits, resolved = ChapterGenerator._resolve_dangling(["e_river"], onto, "새 인물이 등장", rag, 5)
    assert hits == [] and resolved == [] and rag.calls == []
