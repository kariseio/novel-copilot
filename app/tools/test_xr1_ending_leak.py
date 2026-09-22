# -*- coding: utf-8 -*-
"""XR-1 검증 — 결말 정본 필드 원문의 집필 체인 입력 누출 스윕 (LLM 0콜).

계약(cross-review/005 §2.1 · BACKLOG XR-1):
- needle 은 작품 데이터 정본 필드 파생만(사전·수기 목록 금지) · 문장 단위 정규화 ≥12자 대조.
- 관측 축(advisory) — 게이트 아님. 소스 결측(구 회차·결말 미설정)은 MISSING(결측 정직).
- 스토리 패스 재료 채널은 조립 시점 가드(assemble_materials hygiene['ending_literal_leak'])가 맡는다.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr1_ending_leak.py
"""
from __future__ import annotations
import sys
from types import SimpleNamespace

from novelcopilot.engine.verification import ending_literal_leak_sweep, _summ_ending_literal_leak, MISSING
from novelcopilot.services.copilot import CopilotService

TWIST = "그 본체는 다름 아닌 서준호 자신이다."
ENDING = "가장 강한 괴담을 추적한 끝에 준호는 그 정체의 진실 앞에 선다."


def test_sweep_detects_planted_sentence() -> bool:
    res = ending_literal_leak_sweep({"spine.ending.ending": ENDING + " 그는 답을 정한다."},
                            {"draft_inputs": f"장면 목표\n{ENDING}\n다음 사건"})
    ok = res["count"] == 1 and res["hits"][0]["field"] == "spine.ending.ending" \
        and res["hits"][0]["channel"] == "draft_inputs"
    # 공백·따옴표 변형에도 잡힌다(_norm 정규화 대조)
    res2 = ending_literal_leak_sweep({"f": ENDING}, {"c": ENDING.replace(" ", "")})
    ok &= res2["count"] == 1
    print(f"[{'OK' if ok else 'FAIL'}] 스윕: 심은 문장 적발(필드·채널 귀속)+정규화 대조")
    assert ok
    return ok


def test_sweep_ignores_short_and_clean() -> bool:
    res = ending_literal_leak_sweep({"f": "짧다."}, {"c": "짧다. 그리고 아무 관련 없는 긴 본문이 이어진다."})
    ok = res["count"] == 0 and res["sentences"] == 0          # 정규화 12자 미만 needle 제외
    res2 = ending_literal_leak_sweep({"f": TWIST}, {"c": "결말과 무관한 일상 사건이 길게 이어지는 조립물."})
    ok &= res2["count"] == 0 and res2["sentences"] == 1        # 무누출=0(거짓양성 없음)
    print(f"[{'OK' if ok else 'FAIL'}] 스윕: 단문 가드(≥12자)+무누출 0건")
    assert ok
    return ok


def test_summ_missing_honesty() -> bool:
    ok = _summ_ending_literal_leak(None) == MISSING                      # 미제공
    ok &= _summ_ending_literal_leak({}) == MISSING                       # 빈 dict
    ok &= _summ_ending_literal_leak({"needles": {}, "targets": {"c": "x"}}) == MISSING   # 결말 미설정
    ok &= _summ_ending_literal_leak({"needles": {"f": TWIST}, "targets": {}}) == MISSING  # 구 회차(gen_context 부재)
    print(f"[{'OK' if ok else 'FAIL'}] 축 요약: 소스 결측 전 조합 MISSING(결측 정직)")
    assert ok
    return ok


def _state(synopsis: str = "") -> SimpleNamespace:
    end = SimpleNamespace(ending=ENDING, thematic_payoff="", central_question="Q는 자신을 걸 수 있는가?")
    return SimpleNamespace(world=SimpleNamespace(spine=SimpleNamespace(ending=end), synopsis=synopsis))


def test_sources_builder_needles_and_targets() -> bool:
    rec = SimpleNamespace(gen_context={
        "draft": {"persona": "페르소나", "story_so_far": "지난 이야기 요약", "voice_roster": "",
                  "anchors": [{"source": "bible", "ref": "digest", "text": "[설정집] 카드"}],
                  "ground_truth": ["junho: 생사=alive"],
                  "beat": {"title": "비트", "summary": "요약", "key_events": ["사건1"]}},
        "plan": {"cast_context": "인물 컨텍스트", "plant_notes": "", "event_menu": ["후보1"], "recent": ["직전 줄거리"]},
        "story_pass": {"story": "- 확정 스토리 줄"}})
    src = CopilotService._ending_sources_for(_state(synopsis="시놉시스 전문. " + TWIST), rec)
    ok = set(src["needles"]) == {"spine.ending.ending", "spine.ending.central_question", "world.synopsis"}
    ok &= set(src["targets"]) == {"draft_inputs", "plan_inputs", "confirmed_story"}
    ok &= "빈 필드(thematic_payoff)는 needle 미포함" and "spine.ending.thematic_payoff" not in src["needles"]
    # 구 회차(gen_context 없음) → 빈 dict → 축 MISSING 경로
    ok &= CopilotService._ending_sources_for(_state(), SimpleNamespace(gen_context=None)) == {}
    print(f"[{'OK' if ok else 'FAIL'}] 재료 빌더: 정본 필드 needle·조립물 target·구 회차 결측")
    assert ok
    return ok


def test_end_to_end_current_violation_shape() -> bool:
    """005 실측 형상 재현 — 시놉시스의 반전 문장이 확정 스토리에 복사되면 적발, 안 되면 0(현행 실측=0)."""
    src_clean = {"needles": {"world.synopsis": TWIST}, "targets": {"confirmed_story": "- 준호가 사건을 접수한다"}}
    src_leak = {"needles": {"world.synopsis": TWIST}, "targets": {"confirmed_story": f"- {TWIST}"}}
    ok = _summ_ending_literal_leak(src_clean)["count"] == 0
    ok &= _summ_ending_literal_leak(src_leak)["count"] == 1
    print(f"[{'OK' if ok else 'FAIL'}] 종단 형상: 무누출 0(현행 실측)·복사 시 적발(회귀 가드)")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_sweep_detects_planted_sentence(), test_sweep_ignores_short_and_clean(),
               test_summ_missing_honesty(), test_sources_builder_needles_and_targets(),
               test_end_to_end_current_violation_shape()]
    print("\nXR-1 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
