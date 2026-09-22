# -*- coding: utf-8 -*-
"""RC-6 최종화층 강제 교정 검증 — 사용자 확정 '항상 결함' 2클래스만 강제 교정. LLM 0콜·결정론.

검증 축(사전 등록):
  A. 갭 인벤토리(ⓐ) —
     1) finale_gap_inventory 결정론 표 — 스키마·wiring 값 계보·null 0.
     2) RC-6 두 행(force:staccato·force:vocab)만 forced+always_defect+rc6=forced_new. advisory 축 다수 존재.
  B. ① 스타카토/용언 없는 조각 병합(결정론) —
     3) _merge_echo_span 코어 — 완결문 종결('.')→콤마 접합·조각 후행절화·용언 신설 0.
     4) 보수 규칙 — '!'·'?'·'…'·'..'·조각 미발견·스팬 맨 앞 = None(폴백=원문).
     5) force_staccato_merge(monkeypatch) — 검출기 finding → 병합 적용·entry 기록.
     6) force_staccato_merge(실 Kiwi) — 고립 조각 여운이 병합 후 소멸(잔존 보장 제거).
  C. ② 폐기 조어·금지어 정본 치환(결정론) —
     7) force_vocab_substitution — 폐기→정본 치환·조사 보존·건수 기록.
     8) 목록 비면 no-op(바이트 동일)·src==dst/결측 스킵.
  D. 오케스트레이터 + 사실 가드 —
     9) apply_forced_finale_fixes(service=None) — 결정론 채택(강등 경로).
     10) 사실 가드 불통과 → 전체 폴백(원문 유지)·증거 기록. 통과 → 채택.
  E. 하위호환 —
     11) 무변경 입력(조각 0·목록 비) → 원문 그대로·바이트 동일.

실행: (app/ 에서) py -3.12 -m pytest tools/test_rc6_finale_force.py -q
"""
from __future__ import annotations
import pathlib
import sys
from types import SimpleNamespace

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot
sys.path.insert(0, str(_HERE))          # tools/

from novelcopilot.engine import finale_force as ff
from novelcopilot.engine.finale_force import (
    finale_gap_inventory, force_staccato_merge, force_vocab_substitution,
    apply_forced_finale_fixes, _merge_echo_span,
)

_ALLOWED_WIRING = {"forced", "advisory", "gate", "none"}


# ── A. 갭 인벤토리 ──────────────────────────────────────────────────────────────
def test_gap_inventory_deterministic() -> None:
    inv = finale_gap_inventory()
    assert isinstance(inv, list) and len(inv) >= 12, "갭 인벤토리가 비었거나 축 누락"
    keys = {"axis", "detector", "corrector", "wiring", "always_defect", "rc6"}
    for row in inv:
        assert set(row.keys()) == keys, f"인벤토리 스키마 위배: {set(row.keys())}"
        assert row["wiring"] in _ALLOWED_WIRING, f"wiring 계보 위배: {row['wiring']}"
        for k, v in row.items():
            assert v is not None, f"인벤토리 null(결측 정직 위배): {k}"
    # 결정론 — 두 번 호출 동일
    assert finale_gap_inventory() == inv
    print("[OK] 갭 인벤토리 결정론·스키마·null 0")


def test_gap_inventory_rc6_rows() -> None:
    inv = finale_gap_inventory()
    rc6 = [r for r in inv if r["rc6"] == "forced_new"]
    assert len(rc6) == 2, f"RC-6 강제 승격 행은 정확히 2개여야 함(2클래스): {len(rc6)}"
    for r in rc6:
        assert r["wiring"] == "forced" and r["always_defect"] is True, "RC-6 행은 forced+always_defect"
    corr = {r["corrector"] for r in rc6}
    assert any("force_staccato_merge" in c for c in corr), "① 스타카토 병합 교정기 누락"
    assert any("force_vocab_substitution" in c for c in corr), "② 조어 치환 교정기 누락"
    # advisory 축(미감·스타일)이 다수 남아 있어야 함(게이트 증식 차단 — 두 클래스만 승격)
    advisory = [r for r in inv if r["wiring"] == "advisory"]
    assert len(advisory) >= 6, "advisory 축이 사라짐(과잉 강제화 의심)"
    print("[OK] RC-6 승격 정확히 2행·advisory 다수 존치")


# ── B. ① 스타카토 병합(결정론) ─────────────────────────────────────────────────
def test_merge_echo_span_core() -> None:
    # 전치 병합: '완결문P. 조각F.' → 'F, P' — 병합문이 P 의 용언으로 끝난다(용언 신설 0)
    assert _merge_echo_span("미련이 저기 있다. 집 밖에.", "집 밖에.") == "집 밖에, 미련이 저기 있다."
    assert _merge_echo_span("그는 사라졌다. 흔적도 없이.", "흔적도 없이.") == "흔적도 없이, 그는 사라졌다."
    # 말미 공백 보존
    assert _merge_echo_span("문이 열렸다. 천천히. ", "천천히.") == "천천히, 문이 열렸다. "
    print("[OK] _merge_echo_span 전치 병합·용언 종결 수렴·공백 보존")


def test_merge_echo_span_conservative() -> None:
    # 어조·여운 보존 = 병합 안 함(None) — 선행 완결문 P 의 종결 기준
    assert _merge_echo_span("정말이야! 집 밖에.", "집 밖에.") is None      # 감탄
    assert _merge_echo_span("어디에? 집 밖에.", "집 밖에.") is None        # 의문
    assert _merge_echo_span("그는 갔다… 집 밖에.", "집 밖에.") is None     # 말줄임(…)
    assert _merge_echo_span("그는 갔다.. 집 밖에.", "집 밖에.") is None     # 말줄임(..)
    assert _merge_echo_span("집 밖에.", "집 밖에.") is None                # 선행 완결문 없음
    assert _merge_echo_span("완결문이다. 딴것.", "없는조각.") is None       # 조각 미발견
    print("[OK] _merge_echo_span 보수 규칙(감탄·의문·말줄임·미발견·맨앞)")


def test_force_staccato_merge_monkeypatch(monkeypatch) -> None:
    text = "나는 문을 밀고 나갔다. 미련이 저기 있다. 집 밖에.\n\n다음 문장."
    span = "미련이 저기 있다. 집 밖에."
    cs = text.index(span)
    finding = {"category": "N-7", "severity": "S3",
               "span": {"char_start": cs, "char_end": cs + len(span), "text": span},
               "metric": {"fragment": "집 밖에."}}
    import novelcopilot.engine.humanize_detect as hd
    monkeypatch.setattr(hd, "detect_fragment_echo", lambda t: [finding])
    out, entries = force_staccato_merge(text)
    assert "집 밖에, 미련이 저기 있다." in out, "결정론 전치 병합 미적용"
    assert "미련이 저기 있다. 집 밖에." not in out, "원 조각 여운 잔존"
    assert len(entries) == 1 and entries[0]["changed"] is True
    assert entries[0]["category"] == "force:staccato"
    assert entries[0]["before"] == span and entries[0]["after"] == "집 밖에, 미련이 저기 있다."
    print("[OK] force_staccato_merge(monkeypatch) 전치 병합·entry 기록")


def test_force_staccato_merge_real_kiwi() -> None:
    import tools.kiwi_metrics as km
    if not km.kiwi_available():
        # Kiwi 부재 → 결측 정직(원문 그대로·빈 entries)
        t = "미련이 저기 있다. 집 밖에."
        assert force_staccato_merge(t) == (t, [])
        print("[OK] force_staccato_merge: Kiwi 부재 결측 정직")
        return
    from novelcopilot.engine.humanize_detect import detect_fragment_echo
    t = "나는 문을 밀고 마당으로 나갔다. 미련이 저기 있다. 집 밖에.\n\n다음 문장은 평범하게 이어졌다."
    assert detect_fragment_echo(t), "선행 조건: 원문에 N-7 고립 조각 여운 존재"
    out, entries = force_staccato_merge(t)
    assert out != t and any(e["changed"] for e in entries), "실 Kiwi 병합 미적용"
    # 잔존 보장 제거 — 전치로 병합문이 용언 종결이 되어 같은 고립 조각 여운이 소멸(검출기 수렴)
    assert not detect_fragment_echo(out), "병합 후에도 고립 조각 여운 잔존(검출기 미수렴)"
    assert "집 밖에, 미련이 저기 있다" in out
    print("[OK] force_staccato_merge(실 Kiwi) 전치 병합·잔존 소멸")


# ── C. ② 조어 치환(결정론) ─────────────────────────────────────────────────────
def test_force_vocab_substitution() -> None:
    text = "오랏줄을 꺼내 던졌다. 경면에 얼굴이 비쳤다. 오랏줄로 묶었다."
    subs = {"오랏줄": "결박줄", "경면": "손거울"}
    out, entries = force_vocab_substitution(text, subs)
    assert out == "결박줄을 꺼내 던졌다. 손거울에 얼굴이 비쳤다. 결박줄로 묶었다."   # 조사 보존
    m = {e["before"]: e for e in entries}
    assert m["오랏줄"]["count"] == 2 and m["오랏줄"]["after"] == "결박줄"
    assert m["경면"]["count"] == 1
    assert all(e["category"] == "force:vocab" and e["changed"] for e in entries)
    print("[OK] force_vocab_substitution 치환·조사 보존·건수")


def test_force_vocab_substitution_noop() -> None:
    text = "결박줄을 꺼냈다."
    assert force_vocab_substitution(text, {}) == (text, [])          # 목록 비면 no-op
    assert force_vocab_substitution(text, None) == (text, [])
    # src==dst·미등장·빈 값 스킵
    out, entries = force_vocab_substitution(text, {"결박줄": "결박줄", "오랏줄": "결박줄", "": "x"})
    assert out == text and entries == []                             # 실 변경 0
    print("[OK] force_vocab_substitution no-op·스킵 계약")


# ── D. 오케스트레이터 + 사실 가드 ──────────────────────────────────────────────
class _FakeOnt:
    def scan_present_ids(self, text):
        return []


class _FakeChecker:
    def check_text(self, text, ont, ch, ids, pov=""):
        return SimpleNamespace(hard=[], claims=[])


class _FakeService:
    def __init__(self, passed: bool):
        self._passed = passed
        self.calls = 0

    def _guardrail(self, before, after, before_res, ids, ont, checker, ch):
        self.calls += 1
        return ({"passed": self._passed, "G_A_passed": self._passed, "G_B_passed": self._passed,
                 "length_ok": True, "new_hard": [], "claim_changes": [], "claim_flaps": [],
                 "reason": "" if self._passed else "이름·수치가 바뀌었습니다"}, None)


def test_apply_no_service_adopts() -> None:
    text = "오랏줄을 꺼냈다."
    out, entries = apply_forced_finale_fixes(None, None, 3, text,
                                             subs_map={"오랏줄": "결박줄"}, service=None)
    assert out == "결박줄을 꺼냈다."                                  # service 미제공=가드 생략 채택(강등)
    assert any(e["category"] == "force:vocab" for e in entries)
    print("[OK] apply_forced_finale_fixes(service=None) 결정론 채택")


def test_apply_guard_revert() -> None:
    text = "오랏줄을 꺼냈다."
    # 가드 불통과 → 전체 폴백(원문 유지)
    svc_fail = _FakeService(passed=False)
    out, entries = apply_forced_finale_fixes(_FakeOnt(), _FakeChecker(), 3, text,
                                             subs_map={"오랏줄": "결박줄"}, service=svc_fail)
    assert out == text, "가드 불통과인데 교정 채택됨(사실 불변 위반)"
    guard_entry = [e for e in entries if e["category"] == "force:guard"]
    assert guard_entry and guard_entry[0].get("fallback") == "guard"
    assert svc_fail.calls == 1
    # 가드 통과 → 채택
    svc_ok = _FakeService(passed=True)
    out2, entries2 = apply_forced_finale_fixes(_FakeOnt(), _FakeChecker(), 3, text,
                                               subs_map={"오랏줄": "결박줄"}, service=svc_ok)
    assert out2 == "결박줄을 꺼냈다."
    assert any(e["category"] == "force:guard" and "guard" in e for e in entries2)
    print("[OK] apply_forced_finale_fixes 사실 가드 폴백·통과")


# ── E. 하위호환 ────────────────────────────────────────────────────────────────
def test_backward_compat_noop() -> None:
    # 조각 0·목록 비 → 원문 그대로(바이트 동일)
    text = "그는 문을 열고 천천히 걸어 들어갔다. 방은 조용했다."
    out, entries = apply_forced_finale_fixes(None, None, 1, text, subs_map={}, service=None)
    assert out == text, "무변경 입력인데 본문이 바뀜(바이트 동일 계약 위반)"
    print("[OK] 하위호환 — 무변경 입력 바이트 동일")


def test_default_settings_flag_off() -> None:
    from novelcopilot.config import Settings
    s = Settings()
    assert s.finale_force_fixes is False, "RC-6 기본 플래그는 OFF여야 함(바이트 동일)"
    from novelcopilot.domain.world import StyleSpec
    assert StyleSpec().deprecated_terms == {}, "deprecated_terms 기본은 빈 dict(하위호환)"
    print("[OK] 기본 설정 OFF·deprecated_terms 빈 dict")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
