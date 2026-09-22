# -*- coding: utf-8 -*-
"""VP-3 사실 가드(G-B) 요동 해소 회귀 — 2026-08-18 실측(수술 배치 반려 6건·재실현 채택 2회 무산,
매회 다른 클레임 키 요동) 기반.

1단: 편집 변경 영역에 표기가 등장하지 않는 엔티티의 클레임 변화 = 추출 요동 강등(결정론·LLM 0).
2단: 남은 변화는 before/after 재추출에서 양쪽 다 재현될 때만 차단(재측정 다수결).
강등분은 claim_flaps 로 정직 표면화(은폐 금지). LLM 0 — 추출은 시퀀스 스텁.
"""
import sys, os
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from novelcopilot.services.copilot import CopilotService


class _Vocab:
    categorical_keys = ["affiliation"]
    numeric_keys: list = []
    def state_specs(self):
        return []


class _SeqChecker:
    """check_text 가 호출 순서대로 미리 정한 claims 를 반환 — 추출 요동 시뮬."""
    def __init__(self, claims_seq):
        self._seq = list(claims_seq)
        self._i = 0
        self.extractor = SimpleNamespace(vocab=_Vocab())

    def check_text(self, text, ont, chapter, ids):
        claims = self._seq[self._i] if self._i < len(self._seq) else self._seq[-1]
        self._i += 1
        return SimpleNamespace(hard=[], claims=claims)


class _Ont:
    def __init__(self, entities):
        self.entities = entities
    def name(self, eid):
        e = self.entities.get(eid)
        return e.name if e else eid


_ONT = _Ont({"junho": SimpleNamespace(name="서준호", aliases=["준호"]),
             "jiyeon": SimpleNamespace(name="하지연", aliases=["지연"])})

BEFORE = ("서준호가 로비에 섰다.\n\n하지연이 수첩을 폈다.\n\n"
          + "\n\n".join(f"본문 문단 {i}이다." for i in range(10)))
AFTER_LOCAL = BEFORE.replace("수첩을 폈다", "수첩을 펴 들었다")   # 하지연 문단만 편집(서준호 문단 비접촉)


def _svc():
    return CopilotService.__new__(CopilotService)   # _guardrail 은 self 상태 미사용(정적 부품만)


def _run(before, after, seq):
    svc = _svc()
    checker = _SeqChecker(seq)
    before_res = checker.check_text(before, _ONT, 1, [])
    return svc._guardrail(before, after, before_res, [], _ONT, checker, 1)[0]


def test_stage1_out_of_zone_flap_demoted():
    # 편집은 하지연 문장뿐인데 서준호 클레임이 요동 → 1단 결정론 강등(재측정 콜 없이 통과)
    b = [{"id": "junho", "affiliation": "관찰_대상"}]
    a = [{"id": "junho", "affiliation": "관리국_외주"}]
    g = _run(BEFORE, AFTER_LOCAL, [b, a])   # 재측정 시퀀스 미제공 = 2단 진입하면 마지막 요소 반복
    assert g["G_B_passed"] is True and g["claim_changes"] == []
    assert g["claim_flaps"] and g["claim_flaps"][0]["entity"] == "서준호"


def test_stage2_unreproduced_change_demoted():
    # 편집 영역 안 엔티티(하지연)의 변화지만 재측정에서 재현 안 됨 → 2단 강등
    b1 = [{"id": "jiyeon", "affiliation": "관리국"}]
    a1 = [{"id": "jiyeon", "affiliation": "미등록"}]
    b2 = [{"id": "jiyeon", "affiliation": "관리국"}]
    a2 = [{"id": "jiyeon", "affiliation": "관리국"}]   # 재측정 after = 변화 소멸(요동)
    g = _run(BEFORE, AFTER_LOCAL, [b1, a1, b2, a2])
    assert g["G_B_passed"] is True and g["claim_flaps"]


def test_stage2_reproduced_change_still_blocks():
    # 진짜 사실 변경(양쪽 재측정 재현) — 여전히 차단(가드 본연 계약 불변)
    b = [{"id": "jiyeon", "affiliation": "관리국"}]
    a = [{"id": "jiyeon", "affiliation": "미등록"}]
    g = _run(BEFORE, AFTER_LOCAL, [b, a, b, a])
    assert g["G_B_passed"] is False
    assert g["claim_changes"][0]["entity"] == "하지연" and "이름·수치" in g["reason"]


def test_full_rewrite_skips_stage1():
    # 전면 재작성(변경 영역=본문 대부분) — 1단 자연 통과, 판정은 2단 재측정이 가른다
    after_full = ("완전히 새로 쓴 본문이다.\n\n서준호가 움직였다.\n\n하지연이 물었다.\n\n"
                  + "\n\n".join(f"다른 문단 {i}이다." for i in range(10)))
    b = [{"id": "junho", "affiliation": "관찰_대상"}]
    a = [{"id": "junho", "affiliation": "관리국_외주"}]
    blocked = _run(BEFORE, after_full, [b, a, b, a])          # 재현 → 차단
    assert blocked["G_B_passed"] is False
    flappy = _run(BEFORE, after_full, [b, a, b, b])           # 비재현 → 강등
    assert flappy["G_B_passed"] is True and flappy["claim_flaps"]
