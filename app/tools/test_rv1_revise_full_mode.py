# -*- coding: utf-8 -*-
"""RV-1 퇴고 전체 모드 구조적 무변경 수리 검증(audit_e2e_2.md 이슈①·② 잠금). 실 LLM 0콜(fake provider).

이슈①(구조적 무변경 → 무상한 전환): 전체 모드 revise_prose 가 gen_max_tokens(3000) 고정캡으로 콜해
  5천자 회차를 절단하던 결함. 구 수리(본문 길이 기반 캡 산정)는 2026-08-07 "max token 전부 제거" 결정으로
  대체 — 이제 revise_prose 는 캡을 아예 지정하지 않는다(무상한·프로바이더 재량). 절단 원천 제거.
이슈②(무변경 원인 오귀속): 길이가드/살균공백/실제무변경을 구분하는 원인 코드를 사이드채널(_last_revise_cause)에
  실어 changed:false 응답이 정직 문안을 표시하게 한다. 반환 시그니처(str)는 불변(st11·mock 호환).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.domain.world import StyleSpec


class _Bus:
    def emit(self, *a, **k):
        pass


class MaxTokensProvider:
    """chat 의 max_tokens 를 기록하고, 미리 지정한 출력을 반환하는 fake provider(LLM 0콜)."""
    def __init__(self, out):
        self.out = out
        self.last_max_tokens = None
        self.calls = 0

    def chat(self, messages, temperature=0.0, max_tokens=None):
        self.calls += 1
        self.last_max_tokens = max_tokens
        return self.out

    def chat_json(self, messages, temperature=0.0, max_tokens=None):
        return {}


def _settings(gen_max_tokens=3000, chapter_max_tokens=6500, max_output_cap=16000):
    # 실 Settings 필드명을 모사(SimpleNamespace) — RV-1 캡 산정이 참조하는 3필드만 필수, 나머지는 __init__ 참조.
    return SimpleNamespace(
        gen_max_tokens=gen_max_tokens, chapter_max_tokens=chapter_max_tokens,
        max_output_cap=max_output_cap, prev_chapter_context_chars=4000,
        craft_progress=True, scene_style_anchor=False)


def _gen(provider, settings=None):
    settings = settings or _settings()
    return ChapterGenerator(provider, checker=None, style=StyleSpec(),
                            event_bus=_Bus(), settings=settings)


# ───────────────────────── 이슈① 무상한(캡 미지정) ─────────────────────────
def test_full_mode_passes_no_cap():
    """전체 퇴고: max_tokens 를 아예 넘기지 않는다(무상한 — provider 기본 None). 고정캡 절단 원천 제거."""
    before = "가" * 5000
    prov = MaxTokensProvider("나" * 3000)
    g = _gen(prov)
    out = g.revise_prose("전체를 간결하게 압축해 다듬어 줘", before)
    assert prov.last_max_tokens is None, f"캡이 여전히 전달됨(회귀): {prov.last_max_tokens}"
    assert out == "나" * 3000 and g._last_revise_cause == "ok"


def test_span_mode_passes_no_cap():
    """스팬 모드도 동일 — 캡 미지정(무상한)."""
    before = "앞부분. 개당 삼백만 원이나 하는 물건이었다. 뒷부분."
    span = "개당 삼백만 원이나 하는 물건이었다."
    prov = MaxTokensProvider("개당 삼백만 원짜리 물건이었다.")
    g = _gen(prov)
    out = g.revise_prose("간결하게", before, span_text=span)
    assert prov.last_max_tokens is None
    assert "개당 삼백만 원짜리 물건이었다." in out and g._last_revise_cause == "ok"


# ───────────────────────── 이슈② 원인 코드 3분기 ─────────────────────────
def test_cause_length_guard_on_truncated_output():
    """출력이 원문의 <50%(캡 절단 모사) → 길이가드 폴백, cause='length_guard', before 반환."""
    before = "가" * 5000
    prov = MaxTokensProvider("나" * 100)   # 2% — 0.5 하한 위반
    g = _gen(prov)
    out = g.revise_prose("압축", before)
    assert out == before and g._last_revise_cause == "length_guard"


def test_cause_length_guard_on_overexpanded_output():
    """출력이 원문의 >1.8배(과확장) → 동일 길이가드 폴백, cause='length_guard'."""
    before = "가" * 1000
    prov = MaxTokensProvider("나" * 3000)   # 3배 — 1.8 상한 위반
    g = _gen(prov)
    out = g.revise_prose("풀어 써", before)
    assert out == before and g._last_revise_cause == "length_guard"


def test_cause_empty_after_sanitize():
    """LLM 출력이 전부 메타 라인(살균 후 공백) → cause='empty_after_sanitize', before 반환.
    출력은 길이가드(0.5~1.8)는 통과하되 sanitize_meta 로 전부 제거되는 메타 라인만."""
    before = "가나다라마" * 200   # 1000자
    meta = "\n".join(["[END]"] * 200)   # ≈1200자(0.5~1.8 길이가드 통과 규모)의 메타 라인, 살균 후 ""
    prov = MaxTokensProvider(meta)
    g = _gen(prov)
    out = g.revise_prose("압축", before)
    assert out == before and g._last_revise_cause == "empty_after_sanitize"


def test_cause_unchanged_when_near_identical():
    """길이가드 통과했으나 모델이 원문 그대로 반환(near-identical) → cause='unchanged'(length_guard 와 구분)."""
    before = "그는 문 앞에 섰다. 어둠이 내려앉았다." * 30
    prov = MaxTokensProvider(before)   # 원문 그대로
    g = _gen(prov)
    out = g.revise_prose("압축", before)
    assert out == before and g._last_revise_cause == "unchanged"


def test_cause_llm_failure_on_provider_exception():
    """provider.chat 예외 → cause='llm_failure', before 반환."""
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("boom")
    before = "가" * 1000
    g = _gen(_Boom())
    out = g.revise_prose("압축", before)
    assert out == before and g._last_revise_cause == "llm_failure"


def test_cause_ok_on_real_change():
    """실질 변경 산출 → cause='ok'."""
    before = "그는 문 앞에 섰다. " * 50
    after = "그는 문 앞에 섰다. 어둠이 내려앉았다. " * 40   # 다른 텍스트, 0.5~1.8 범위
    prov = MaxTokensProvider(after)
    g = _gen(prov)
    out = g.revise_prose("다듬어", before)
    assert out != before and g._last_revise_cause == "ok"


def test_cause_reset_between_calls():
    """사이드채널이 콜마다 리셋되는지 — 실패 후 성공 콜이 stale 'length_guard'를 물려받지 않는다."""
    g = _gen(MaxTokensProvider("나" * 10))   # 첫 콜: 절단 실패
    g.revise_prose("압축", "가" * 5000)
    assert g._last_revise_cause == "length_guard"
    # 같은 generator 로 성공 콜(새 provider 응답)
    g.provider = MaxTokensProvider("나" * 3000)
    g.revise_prose("압축", "가" * 5000)
    assert g._last_revise_cause == "ok"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"RV-1 검증: ALL GREEN ({len(fns)} tests)")
