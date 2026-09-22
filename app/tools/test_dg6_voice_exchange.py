# -*- coding: utf-8 -*-
"""DG-6·CX-11 회귀 — 조회 보이스 전문 동봉(절단 수리) + 관계 어체 실물 앵커(원장 발췌). LLM 0.

계약:
· CX-11: 보이스는 전문 동봉(600자 위생 상한은 문장 경계) — 구 160자 중간 절단 금지.
· DG-6: 원장(ledgers) 제공 시, 인물 조회 응답에 그 인물이 낀 최근 확정 대화의 짧은 원문
  (canon 행·줄 60자·총 3줄·상대 발화 필수)이 동봉된다. 미제공이면 응답 바이트 동일(하위호환).
· DG-1 카브아웃 유지 조건(보드 SSOT): 화자 라벨 미동봉 — 원장 귀속은 선별 인덱스로만,
  고정 프레임을 뺀 주입 바이트 전량은 확정 본문의 부분 문자열(오귀속 승격 바이트 자체가 없음).
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.engine.lookup import CanonLookup, _voice_trim


_VOICE_4PARTS = ("① 나는 세상을 장난처럼 산다. 겁나는 것 앞일수록 일단 던져본다. "
                 "② 감탄은 아껴 쓸수록 세다. 빈정과 자기변호가 섞인 잔머리를 부린다. "
                 "③ 능청 떨 땐 짧게 툭툭 던지고, 진지해지는 순간엔 말수가 뚝 끊긴다. "
                 "④ 두려움을 두렵다고 하지 않는다. 화제를 딴 데로 튼다.")


class _Ont:
    def __init__(self):
        self.entities = {"junho": NS(name="서준호", aliases=["준호"], etype="character",
                                     attrs={}, voice=_VOICE_4PARTS),
                         "manmul": NS(name="만물", aliases=[], etype="character",
                                      attrs={}, voice="반말로 말한다. 장사꾼 말버릇이 짙다.")}
        self.edges = []
        self.vocab = NS(label=lambda k: k)
    def is_actor(self, etype):
        return etype == "character"
    def public_attrs(self, eid, ch):
        return []
    def rel_spec(self, rid):
        return NS(label="")


_ROWS_CH2 = [
    {"idx": 1, "speaker": "만물", "text": "누구야, 너.", "canon": True, "designation": "만물"},
    {"idx": 2, "speaker": "서준호", "text": "백탑, 진짜 몰라요?", "canon": True, "designation": "나"},
    {"idx": 3, "speaker": "만물", "text": "그런 이름은 이 바닥에 없어.", "canon": True, "designation": "만물"},
    {"idx": 4, "speaker": "미상", "text": "누구 목소리지.", "canon": False, "designation": "미상"},
]


def test_voice_full_no_midsentence_cut():
    lk = CanonLookup(_Ont(), [], 3)
    out = lk.handle("lookup_canon", {"query": "서준호"})
    assert "③" in out and "④" in out                      # 구 160자 절단이면 증발하던 항
    assert "말수가 뚝 끊긴다" in out


def test_voice_trim_sentence_boundary():
    long = "가나다라. " * 200                                 # 1,200자
    t = _voice_trim(long)
    assert len(t) <= 600 and t.endswith(".")               # 상한 이내 + 문장 경계


def test_exchange_attached_with_counterpart():
    import re
    lk = CanonLookup(_Ont(), [], 3, ledgers=[(2, _ROWS_CH2)])
    out = lk.handle("lookup_canon", {"query": "만물"})
    assert "최근 대화(2화)=" in out
    assert '"백탑, 진짜 몰라요?"' in out                    # 상대(관계 어체) 발화 동봉이 요체
    assert '"그런 이름은 이 바닥에 없어."' in out
    assert "누구 목소리지" not in out                       # canon=False 행 배제
    # 카브아웃 ⒜⒝: 라벨 미동봉 + 프레임 제외 전량이 원장 원문(=확정 본문 부분 문자열)
    body = out.split("최근 대화(2화)=", 1)[1].split(" · ", 1)[0]
    assert "서준호:" not in body and "만물:" not in body
    texts = {r["text"] for r in _ROWS_CH2}
    assert all(seg in texts for seg in re.findall(r'"([^"]+)"', body))


def test_exchange_requires_counterpart_and_backcompat():
    solo = [{"idx": 1, "speaker": "만물", "text": "혼잣말이다.", "canon": True, "designation": "만물"}]
    lk = CanonLookup(_Ont(), [], 3, ledgers=[(2, solo)])
    assert "최근 대화" not in lk.handle("lookup_canon", {"query": "만물"})   # 단독 발화=관계 앵커 아님
    # 하위호환: ledgers 미제공 응답 == 빈 원장 응답 (바이트 동일)
    a = CanonLookup(_Ont(), [], 3).handle("lookup_canon", {"query": "만물"})
    b = CanonLookup(_Ont(), [], 3, ledgers=[]).handle("lookup_canon", {"query": "만물"})
    assert a == b and "최근 대화" not in a


def test_exchange_skips_future_and_long_lines():
    long_rows = [{"idx": 1, "speaker": "만물", "text": "긴 대사 " * 20, "canon": True, "designation": "만물"},
                 {"idx": 2, "speaker": "서준호", "text": "네.", "canon": True, "designation": "나"}]
    # 현재 회차(3화) 이후 원장은 미래 누출 — 배제
    lk = CanonLookup(_Ont(), [], 3, ledgers=[(3, _ROWS_CH2), (5, _ROWS_CH2)])
    assert "최근 대화" not in lk.handle("lookup_canon", {"query": "만물"})
    # 60자 초과 행은 앵커 후보에서 빠진다(만물 발화가 전부 길면 관계쌍 불성립)
    lk2 = CanonLookup(_Ont(), [], 3, ledgers=[(2, long_rows)])
    assert "최근 대화" not in lk2.handle("lookup_canon", {"query": "만물"})


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("전체 통과")
