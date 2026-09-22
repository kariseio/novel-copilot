# -*- coding: utf-8 -*-
"""VC-1 음성 카드 말버릇 문자열 금지 — 도출기 스냅샷 가드(LLM 0콜).

실측 결함(2026-07-17, 괴담작): 음성 카드 스키마의 구 ②'속생각 문장 2개를 직접 지어 넣어라'가 특정 문장
문자열(캐치프레이즈·예문)을 카드에 박았고, 그 문자열이 매 회차 카드+이전 회차 참조(RAG)로 이중 주입돼
과잉 앵커로 작동했다(예문 "응, 실력으로 해두자"·"이걸 사네? 와우"가 매 화 1~3회 기계 반복). 사용자 결정:
"카드는 버릇의 결·트리거·상황만 서술하고 특정 문장 문자열을 갖지 않는다 — 정착은 본문, 유지·전파는 RAG."

VS-1(형태 분리)과 동형 절단선: VS-1 은 문장 '형태'를, VC-1 은 문장 '문자열'을 카드에서 뺀다.

잠그는 계약:
 ① 카드 ②(말버릇/속생각 계열)가 '예문·특정 문장을 지어라'를 요구하지 않는다 — 결·트리거·기능만.
 ② 카드 스키마·시스템 프롬프트에 소관 경계 문장 존재("특정 대사/속생각 문장은 카드에 적지 않는다 —
    (말버릇) 문장의 정착은 본문 회차가 맡는다"류) — VS-1 형태 경계 문장과 같은 문법(긍정 서술·소관 선언).
 ③ 카드는 여전히 3항목(각도·속생각의 결·감정 표출) 구조 보존(내용 축 불변 — 과잉 절단 방지).
 ④ DP-17 나레이션 톤 도출도 태도·시선만 낳고 특정 문구 예문 생성을 지시하지 않음(누수 부재 확인).
상세: docs/issue-voice-style-separation-2026-07-16.md §1·§5
실행: PYTHONPATH=app py -3.12 -m pytest tools/test_vc1_voice_habit_no_verbatim.py -q
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.worldgen import narrator_voice as nv
from novelcopilot.worldgen.generator import WorldGenerator

# 카드가 '특정 문장을 지어라'고 요구하는 누수 토큰 — ②(말버릇/속생각) 항목에서 재유입 금지.
#   '예문'·'예시 문장'·'직접 지어'·'문장 2개'가 붙으면 특정 문자열이 카드에 박히고 매 화 이중 주입된다.
_VERBATIM_TOKENS = ("예문", "예시 문장", "직접 지어", "문장 2개", "문장 두 개", "캐치프레이즈")


def test_card_habit_item_requires_no_verbatim_examples():
    # ① 말버릇/속생각 계열(②)이 예문·특정 문장 생성을 요구하지 않는다 — 결·트리거만.
    #   경계 문장 안의 '특정 대사·속생각 문장은 짓지 않는다'는 금지 선언이라 토큰 스캔에서 제외한다.
    schema = nv._CARD_SCHEMA
    boundary_free = schema.replace("특정 대사·속생각 문장 예문은 짓지 않는다", "")
    for tok in _VERBATIM_TOKENS:
        assert tok not in boundary_free, f"카드 스키마 말버릇 항목에 예문/문자열 생성 지시 재유입: {tok!r}"
    # ② 항목이 결·트리거(언제 새어 나오는가)를 묻는지 — 문자열이 아니라 결·상황을 도출
    assert "② 속생각의 결" in schema
    assert "새어 나오는가" in schema           # 트리거(언제)를 묻는 긍정 서술


def test_card_has_verbatim_boundary_sentence():
    # ② 소관 경계 문장 존재 — VS-1 형태 경계와 같은 문법(정착은 본문 회차 소관 선언)
    #   스키마와 시스템 프롬프트 양쪽에 절단선이 박혀 있어야 도출 LLM 이 문자열을 짓지 않는다.
    assert "정착은 본문 회차가 맡는다" in nv._CARD_SCHEMA
    assert "특정 대사·속생각 문장은 카드에 적지 않는다" in nv._SYSTEM
    assert "말버릇 문장의 정착은 본문 회차가 맡는다" in nv._SYSTEM


def test_card_keeps_three_item_structure():
    # ③ 3항목 구조·내용 축 보존(과잉 절단 방지) — 각도·속생각의 결·감정 표출, ④ 없음
    s = nv._CARD_SCHEMA
    assert "① 세상을 보는 각도" in s
    assert "② 속생각의 결" in s
    assert "③ 감정을 드러내는 방식" in s
    assert "④" not in s


def test_dp17_voice_derivation_has_no_verbatim_leak():
    # ④ DP-17: 태도·시선만 도출·특정 문구 예문 생성 지시 부재(누수 부재 확인)
    schema = WorldGenerator._VOICE_SCHEMA
    assert "태도와 시선" in schema
    for tok in _VERBATIM_TOKENS:
        assert tok not in schema, f"DP-17 스키마에 예문/문자열 생성 지시 누수: {tok!r}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"VC-1 검증: ALL GREEN ({len(fns)} tests)")
