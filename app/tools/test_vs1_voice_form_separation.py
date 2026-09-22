# -*- coding: utf-8 -*-
"""VS-1 화자 목소리 vs 문장 형태 분리 — 도출기 스냅샷 가드(LLM 0콜).

실측 결함(2026-07-16, 퇴근작 vs 괴담작 포렌식): 음성 카드 스키마의 '③리듬 습관(문장 길이·호흡)' 축이
작품마다 문장 형태 지시를 카드에 박았고, 그 방향이 모델 기본 습성과 정렬되면 증폭기('-었다' 0.63→0.81),
역방향이면 억제기가 되는 복불복 소스였다. 사용자 결정: "생각은 캐릭터, 대사가 아니면 문장 형태는 분리."

잠그는 계약:
 ① 카드 도출 스키마·시스템 프롬프트에 문장 형태 축(리듬·문장 길이·호흡) 부재 — 형태는 스타일 레이어 단독 소관.
 ② 카드는 3항목(렌즈·속생각·감정 표출) — 내용 축은 보존(어휘의 결 포함).
 ③ DP-17 나레이션 톤 도출도 태도·시선 한정('말투' 형태 누수 봉합) + 소관 경계 문장.
 ④ 스타일 레이어의 정당한 형태 소관(DEFAULT_STYLE_RULES 등)은 이 분리의 대상이 아님(존재 확인).
상세: docs/issue-voice-style-separation-2026-07-16.md
실행: PYTHONPATH=app py -3.12 -m pytest tools/test_vs1_voice_form_separation.py -q
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.worldgen import narrator_voice as nv
from novelcopilot.worldgen.generator import WorldGenerator
from novelcopilot.domain.world import DEFAULT_STYLE_RULES

_FORM_TOKENS = ("리듬", "문장 길이", "호흡", "문장은 짧게", "단문")


def test_card_schema_has_no_form_axis():
    # ① 카드 스키마·시스템에서 형태 축 부재(경계 문장 "문장을 어떤 형태로 쓰는가는 …이 맡는다"는 소관 선언이라 허용)
    for tok in _FORM_TOKENS:
        assert tok not in nv._CARD_SCHEMA, f"카드 스키마에 형태 축 재유입: {tok!r}"
    body = nv._SYSTEM.replace("문장을 어떤 형태로 쓰는가는 작품의 문체 규칙이 따로 맡는다", "")
    for tok in _FORM_TOKENS:
        assert tok not in body, f"카드 시스템 프롬프트에 형태 지시 재유입: {tok!r}"


def test_card_schema_keeps_three_content_items():
    # ② 내용 3축 보존: 각도·속생각·감정 표출 — ④ 없음
    # MS-1(2026-07-16): ①을 '렌즈(모든 것을 해석)'→'각도(주의·태도)'로 재프레이밍 — 전칭 번역 사전이
    #   은유 포화(104회/화)의 소스였고 '어휘의 결 포함'은 어휘 덤프 유인이었다(실측). 절제는 카드가 내장.
    s = nv._CARD_SCHEMA
    assert "① 세상을 보는 각도" in s and "주의가 끌리고" in s
    assert "아껴 터뜨리는 시그니처" in s          # 밀도 절제 내장(포화 소스 차단)
    assert "모든 것을" not in s                   # 전칭 번역 지시 재유입 잠금
    assert "어휘의 결" not in s                   # 어휘 덤프 유인 재유입 잠금
    assert "② 속생각의 결" in s
    assert "③ 감정을 드러내는 방식" in s
    assert "④" not in s


def test_dp17_voice_scope_is_attitude_only():
    # ③ DP-17: '말투' 누수 봉합 + 태도·시선 한정 + 소관 경계 문장
    assert "말투" not in WorldGenerator._VOICE_SCHEMA
    assert "태도와 시선" in WorldGenerator._VOICE_SCHEMA


def test_style_layer_still_owns_form():
    # ④ 형태의 정당한 소유자(스타일 레이어)는 불변 — 과잉 절단 방지
    joined = " ".join(DEFAULT_STYLE_RULES)
    assert "1~4문장" in joined            # 규칙1 조판(형태 — 스타일 레이어 소관 유지, VP-1: 1~4문장 변주)
    # VP-1: 규칙5의 '들쭉날쭉' 리듬 공학 문안은 종결 다양화(3연속 전 순환)로 개정 — 형태 소유는 여전히 스타일 레이어.
    assert "세 문장 이어지기 전에" in joined


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"VS-1 검증: ALL GREEN ({len(fns)} tests)")
