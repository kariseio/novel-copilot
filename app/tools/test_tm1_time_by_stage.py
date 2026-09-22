# -*- coding: utf-8 -*-
"""TM-1 검증 — 회차 생성 단계별 소요 시간 기록(time_by_stage). 실 LLM 0콜(전 mock·결정론).

설계: usage_by_stage 의 '시간판' 대칭 미러.
  · 도메인   : ChapterRecord.time_by_stage(additive dict — usage_by_stage 선례 그대로).
  · 하네스   : generate() 의 _track(stage, before_tokens, before_ts) 가 stage_usage 를 쌓는 모든 지점에서
               같은 stage 키로 경과(초·time.monotonic·소수1)도 쌓아 record.time_by_stage 로 병합.
               콜 없는 단계는 _track 미호출 → 애초에 안 들어감(0초 노이즈 금지).
  · 검증축   : engine.verification.build_verification 에 timing 축(total_sec + 상위 5개 단계). 결측 시 MISSING.

검증(설계 테스트 목록):
  ① 구 JSON(time_by_stage 부재) 로드 정상 + additive(exclude_defaults 미출현·바이트 동일)
  ② generate mock 경로에서 time_by_stage 에 단계 키 존재·값>0
  ③ usage_by_stage 와 키 대칭(usage 에 있는 단계는 time 에도 존재)
  ④ verification.timing 집계(total_sec·by_stage 상위5) + 결측 '미실행'

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_tm1_time_by_stage.py
        또는 py -3.12 -m pytest tools/test_tm1_time_by_stage.py -q
"""
from __future__ import annotations
import sys
import time
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/

from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine.verification import build_verification, MISSING


# ---------- ① 구 JSON 호환(additive) ----------
def test_old_json_load_additive() -> bool:
    """time_by_stage 부재 구 JSON 로드 정상 → 기본 {}·재직렬화 미출현(바이트 동일)·왕복 정상."""
    old = {"chapter": 5, "status": "FINALIZED", "title": "5화",
           "usage_by_stage": {"draft": 1200}}   # 구 레코드엔 usage 만·time 없음
    rec = ChapterRecord.model_validate(old)
    ok = (rec.time_by_stage == {})                       # 결측 → 기본 빈 dict(무중단 로드)
    d = rec.model_dump(exclude_defaults=True)
    ok &= ("time_by_stage" not in d)                     # 미설정 → 재직렬화 미출현(구 JSON 바이트 동일)
    ok &= ("usage_by_stage" in d)                        # 기존 필드는 그대로(대칭·무회귀)
    # 값 세팅 왕복(additive 필드 정상 동작 — usage 선례 동형)
    rec.time_by_stage = {"draft": 3.4, "check": 0.2}
    ok &= (ChapterRecord.model_validate(rec.model_dump()).time_by_stage == {"draft": 3.4, "check": 0.2})
    print(f"[{'OK' if ok else 'FAIL'}] ① 구 JSON 호환: time_by_stage 결측=기본{{}}·additive·미출현(바이트 동일)")
    assert ok
    return ok


# ---------- generate mock 경로(실 엔진·LLM 0콜, 미소 sleep 으로 경과 관측) ----------
def _run_generate():
    """generate() 실경로 — draft/continue/summarize mock(미소 sleep) 으로 단계 계측을 태운다(LLM 0콜).
    time.monotonic 델타가 소수1 반올림에서 0.0 으로 사라지지 않도록 각 콜에 짧은 sleep 을 넣는다
    (실 생성은 초 단위라 이 문제 없음 — mock 이 너무 빨라 생기는 계측 하한만 보정)."""
    from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat, StyleSpec
    from novelcopilot.engine.factory import build_engine
    from novelcopilot.engine.checker import CheckResult
    from novelcopilot.domain.types import SceneSpec as _SS
    from novelcopilot.llm.base import LLMProvider

    class _Cap(LLMProvider):
        def __init__(self):
            super().__init__()   # self.usage = Usage()
            self.n = 0
            self.last_truncated = False

        def chat(self, messages, *a, **k):
            self.n += 1
            time.sleep(0.12)     # 미소 지연 → 소수1 반올림에서 관측 가능(≥0.1)
            self.usage.chat_tokens += 50   # usage 델타도 발생(대칭 계측)
            # 충분히 긴 본문(norm 도달)으로 draft 채택 → 이어쓰기 최소화
            return "주인공이 문을 열고 걸어 나갔다. " * 40

        def chat_json(self, messages, *a, **k):
            time.sleep(0.12)
            self.usage.chat_tokens += 30
            return {"oneliner": "요약", "synopsis": "상세 시놉시스." * 30}

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    world = WorldConfig(
        title="[실험] TM-1", genre="현판", tone="건조", premise="테스트.",
        entities=[EntitySpec(id="hero", name="도현", etype="character", profile="주인공")],
        beats=[Beat(chapter=2, title="t", summary="s", entities=["hero"])],
        style=StyleSpec(pov="third_limited"))
    from novelcopilot.config import get_settings
    settings = get_settings().model_copy(update={"narrator_voice": False, "humanize": False,
                                                 "style_repair": False, "reader_desk": False,
                                                 "claim_audit": False, "continuity_polish": False})
    prov = _Cap()
    b = build_engine(world, prov, settings)
    gen = b.generator
    gen.plan_scenes = lambda beat, directives: [_SS(index=0, goal="g", key_events=[])]
    gen._rewrite = lambda text, viols, board, **kw: text
    gen.checker.check_text = lambda *a, **k: CheckResult(violations=[], claims=[])
    rec = gen.generate(2, {"title": "t", "summary": "s", "entities": ["hero"], "key_events": []},
                       b.ontology, b.rag, b.wiki, prev_chapter_text="", story_so_far="")
    return rec


# ---------- ② time_by_stage 단계 키 존재·값>0 ----------
def test_time_by_stage_populated() -> bool:
    rec = _run_generate()
    ts = rec.time_by_stage or {}
    ok = isinstance(ts, dict) and len(ts) >= 1
    # draft·check 는 어떤 회차든 반드시 도는 단계(콜 발생) → 키 존재
    ok &= ("draft" in ts and "check" in ts)
    # draft 는 실 콜(mock 미소 sleep)을 태운 단계 → 시간이 실제로 누적됨(값>0). 계측 자체가 작동함을 증명.
    ok &= (isinstance(ts["draft"], (int, float)) and ts["draft"] > 0)
    # 전 단계 값은 음수 아님(monotonic 델타 — 시스템 시간 역행 무관). 매우 빠른 콜은 소수1 반올림에서 0.0 가능(정상).
    ok &= all(isinstance(v, (int, float)) and v >= 0 for v in ts.values())
    # FINALIZED 경로 → wiki·summarize 도 계측(콜 발생 지점 기록)
    if rec.status == ChapterStatus.FINALIZED:
        ok &= ("summarize" in ts and "wiki" in ts)
    print(f"[{'OK' if ok else 'FAIL'}] ② generate: 단계 키 존재·draft 값>0(계측 작동)·음수 없음 — {sorted(ts)}")
    assert ok
    return ok


# ---------- ③ usage_by_stage 와 키 대칭 ----------
def test_key_symmetry_with_usage() -> bool:
    """usage 에 있는 단계(콜 발생)는 time 에도 있어야 — 같은 _track 지점에서 대칭 기록."""
    rec = _run_generate()
    us = set((rec.usage_by_stage or {}).keys())
    ts = set((rec.time_by_stage or {}).keys())
    ok = bool(us) and (us <= ts)          # usage 의 모든 단계 키가 time 에도 존재(대칭)
    # time 이 usage 초과 키를 갖지 않음(같은 지점만 기록 — 콜 없는 단계 0초 미기록도 대칭)
    ok &= (ts == us)
    print(f"[{'OK' if ok else 'FAIL'}] ③ 키 대칭: usage⊆time 이고 time==usage(같은 _track 지점) — usage={sorted(us)}")
    assert ok
    return ok


# ---------- ④ verification.timing 집계 + 결측 '미실행' ----------
def test_verification_timing_axis() -> bool:
    # 값 있는 경우 — total_sec·by_stage(상위5) 집계
    r = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="본문 " * 200,
                      time_by_stage={"draft": 12.3, "check": 0.4, "wiki": 2.1, "summarize": 3.0,
                                     "rewrite": 1.2, "polish": 0.5, "claim_audit": 0.9})
    v = build_verification(r, prev_texts=[], target_chars=5000)
    tm = v["timing"]
    ok = (tm != MISSING and isinstance(tm, dict))
    ok &= (tm["total_sec"] == round(12.3 + 0.4 + 2.1 + 3.0 + 1.2 + 0.5 + 0.9, 1))
    ok &= (len(tm["by_stage"]) == 5)                      # 상위 5개 단계만
    ok &= ("draft" in tm["by_stage"] and "check" not in tm["by_stage"])   # 최대 draft 포함·최소 check 탈락
    ok &= (list(tm["by_stage"].values()) == sorted(tm["by_stage"].values(), reverse=True))  # 내림차순

    # 결측(구 회차·미계측) — MISSING 문자열(null/0 위장 금지·usage 대칭)
    r2 = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="짧은 본문.")
    v2 = build_verification(r2, prev_texts=[], target_chars=5000)
    ok &= (v2["timing"] == MISSING and MISSING == "미실행")
    # timing 이 전 축 집계에 포함(키 존재 — 누락의 조용한 통과 차단)
    ok &= ("timing" in v and "timing" in v2)
    print(f"[{'OK' if ok else 'FAIL'}] ④ verification.timing: total_sec·상위5 집계·결측 '미실행'")
    assert ok
    return ok


_TESTS = [
    test_old_json_load_additive,
    test_time_by_stage_populated,
    test_key_symmetry_with_usage,
    test_verification_timing_axis,
]


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    results = []
    for t in _TESTS:
        try:
            results.append(bool(t()))
        except AssertionError:
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
