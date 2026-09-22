# -*- coding: utf-8 -*-
"""LN-1 분량 규범 적용 검증 — DEFAULT_STYLE_RULES 분량 문안·config 기본값·구 JSON 바이트 동일(LLM 0콜).

설계 design-st9-dp22-ln1.md §LN-1:
  ① DEFAULT_STYLE_RULES 분량 규칙을 상한 포함 긍정형으로: "한 회차는 공백 포함 4,500~5,500자를 목표로
     하라 — 밀도를 유지한 채 이 범위 안에서 끝나도록 장면을 배분하라". floor(끊긴 문장 완결)·시점/시제 절은 유지.
  ② config chapter_max_tokens 기본 9,000 → 6,500(보조 상한 — 하드캡 절단 방지).
  ③ 기존 작품 데이터 무수정(worldgen 스냅숏 rules 그대로) — 신규 작품·신규 생성분부터.
     B-02ⓐ 준수: 규범은 지시 문안, 검증 게이트 아님(길이 코드강제 0 — 이 파일도 게이트를 추가하지 않는다).
"""
import sys, pathlib, json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.world import DEFAULT_STYLE_RULES, StyleSpec
from novelcopilot.config import Settings


# 구 스냅숏이 style.rules 로 저장했던 분량 규칙(LN-1 이전 문안) — 하위호환 회귀 재료.
_OLD_LENGTH_RULE = (
    "이 장면을 약 1,800자 내외로, 문장을 자연스럽게 맺으며 완결하라 — 끊긴 문장으로 끝내기 금지"
    "(회차 총 5,000~5,500자). 서술 인칭과 시제는 작품 전체에서 하나로 일관되게 유지하라"
    "(지정이 없으면 3인칭·과거형을 기본으로, 액션 정점의 현재형만 양념)."
)


# ---------- ① 분량 규칙 문안(긍정형·상한 포함) ----------
def test_length_rule_positive_upper_bound():
    r8 = DEFAULT_STYLE_RULES[7]
    # 상한 포함 목표 범위(공백 포함) — 하한도 4,500 으로 승격
    assert "공백 포함 4,500~5,500자를 목표로 하라" in r8
    assert "이 범위 안에서 끝나도록 장면을 배분하라" in r8
    # LN-1 이전 문안(장면 1,800자·회차 총 5,000~5,500)은 사라졌다
    assert "1,800자 내외" not in r8
    assert "5,000~5,500" not in r8


def test_length_rule_keeps_floor_and_pov():
    # 하드 바닥(끊긴 문장 완결 계약)·시점/시제 일관 절은 유지(과잉 삭제 방지 — floor 보존)
    r8 = DEFAULT_STYLE_RULES[7]
    assert "끊긴 문장으로 끝내기 금지" in r8
    assert "서술 인칭과 시제는 작품 전체에서 하나로 일관되게 유지하라" in r8
    assert "3인칭·과거형을 기본으로" in r8


def test_rule_count_unchanged():
    # additive 아님·in-place 문안 교체 → 규칙 수 8 불변(C-5 헌법과 정합)
    assert len(DEFAULT_STYLE_RULES) == 8


# ---------- ② config chapter_max_tokens 기본값 ----------
def test_chapter_max_tokens_default(monkeypatch):
    # 환경변수 오염 차단 후 코드 기본값 확인(NOVEL_ 프리픽스).
    # fable-5 전환(2026-07-15)으로 6,500→12,000: fable 은 thinking 상시라 출력 예산에 추론 토큰이 포함돼
    # 6,500 이면 프로즈가 절단된다(MD-1 신작 7화가 12,000 으로 검증). 분량 제어는 코드 norm 이 담당·이 값은 하드캡.
    monkeypatch.delenv("NOVEL_CHAPTER_MAX_TOKENS", raising=False)
    s = Settings()
    assert s.chapter_max_tokens == 12000
    # 하드캡(max_output_cap)은 chapter 예산보다 커야 한다는 불변식 유지
    assert s.max_output_cap > s.chapter_max_tokens


# ---------- ③ 구 JSON 바이트 동일(하위호환 — 신규부터 적용) ----------
def test_old_json_rules_byte_identical():
    # 구 스냅숏은 style.rules 를 명시 저장 → default_factory 무시하고 그 값 그대로 로드(신규 문안 미유입).
    old_rules = list(DEFAULT_STYLE_RULES[:7]) + [_OLD_LENGTH_RULE]
    payload = {"rules": old_rules}
    spec = StyleSpec.model_validate(payload)
    assert spec.rules == old_rules                       # 데이터 무수정
    assert spec.rules[7] == _OLD_LENGTH_RULE
    assert "4,500~5,500" not in spec.rules[7]            # 신규 default 가 구 데이터를 덮지 않음
    # 직렬화 라운드트립 바이트 동일
    assert json.loads(spec.model_dump_json())["rules"] == old_rules


def test_new_spec_uses_new_default():
    # CX-7 신계약: 신규 StyleSpec 은 rules 빈 값 — 새 문안은 렌더 폴백(코드 상수)으로 실효
    from novelcopilot.engine.prompts import render_style
    assert StyleSpec().rules == []
    assert "공백 포함 4,500~5,500자를 목표로 하라" in render_style(StyleSpec())


if __name__ == "__main__":
    import types

    class _MP:  # monkeypatch 대체(직접 실행용)
        def delenv(self, k, raising=True):
            import os
            os.environ.pop(k, None)

    mp = _MP()
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        if "monkeypatch" in f.__code__.co_varnames:
            f(mp)
        else:
            f()
    print(f"LN-1 검증: ALL GREEN ({len(fns)} tests)")
