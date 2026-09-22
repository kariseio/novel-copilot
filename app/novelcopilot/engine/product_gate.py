# -*- coding: utf-8 -*-
"""PR-1 제품 경로 정독 게이트 배선 — 웹 "다음 화 생성"에 러너의 게이트 루프를 옵션으로 얹는다.

설계(design-pr1-pr2-product-gate.md §PR-1): generate_next_chapter FINALIZED 확정 뒤(락 해제 후) config
product_gate 가 ON이면 engine.chapter_gate.gate_committed_chapter 동형 루프를 실행한다 — DP-21 국소 revise
라우팅·undo 최선 보존·R 상한·콜 예산 포함. 판정·라운드는 verification.gate 로 영속(러너 jsonl 무덤 해소).

이 모듈은 CopilotService 를 얇게 감싸 게이트 코어에 넘길 harness 어댑터·measure_fn·rhythm_fn·judge_fn 을
조립한다. 게이트 코어(chapter_gate)는 tools 무의존이고, 여기서 tools-독립 부품만 주입한다:
  · harness: load/revise/accept/undo/regen/llm_call_count 를 서비스 메서드로 위임(락 밖 실행 — 경쟁 없음).
  · measure_fn: 이미 계산된 record.ai_tell(정규식+Kiwi) + verification.retread 를 measure-then-cite digest 로
    재구성(신규 검출기 0·advisory·LLM 0콜). 판정은 정독이 한다(수치는 참고).
  · rhythm_fn: engine.style_pipeline.detect_rhythm_spans(tools 부재 시 [] — 엔진 의존성0 폴백).
  · judge_fn: chapter_gate.make_judge(prose 벤더와 다른 벤더 cross-vendor 심사).

무강제 불변식: OFF 기본(호출부 게이트) · 사실 불변 revise 우선 · R 상한 · 은폐 없는 기록(gate_rounds·
  fail_exhausted 표면화) · 자동 차단 0(FAIL 이어도 회차는 발행 상태 유지 — 게이트는 기록·재시도만).
"""
from __future__ import annotations

import time

from . import chapter_gate as cg


# ─────────────────────────────────────────────────────────────────────────────
# 서비스 어댑터 — 게이트 harness 계약(load/revise/accept/undo/regen/llm_call_count)을 CopilotService 로 위임.
#   전부 락 밖에서 호출되므로(generate_next_chapter 가 sess.lock 을 이미 해제) 서비스 메서드의 비차단 락
#   획득이 경쟁 없이 통과한다. revise/accept/undo 는 기존 작가 퇴고 경로(사실 불변 가드레일·이력 push)를 그대로 탄다.
# ─────────────────────────────────────────────────────────────────────────────
class _ServiceGateHarness:
    def __init__(self, svc, pid: str):
        self.svc = svc
        self.pid = pid

    def load(self, pid: str):
        return self.svc.repo.get(pid)

    # DP-21 국소 경로 — 서비스의 사실 불변 퇴고 3-콜 경로(revise_chapter → accept_revision)로 위임.
    def revise(self, pid: str, chapter: int, directive: str, span_text: str = ""):
        return self.svc.revise_chapter(pid, chapter, directive, span_text=(span_text or ""))

    def accept(self, pid: str, chapter: int, revision_id, after_text_fb=None,
               span_text_fb=None, passes_fb=None):
        return self.svc.accept_revision(pid, chapter, revision_id,
                                        after_text_fb=after_text_fb, span_text_fb=span_text_fb,
                                        passes_fb=passes_fb)

    def undo(self, pid: str, chapter: int):
        return self.svc.undo_revision(pid, chapter)

    def regen(self, pid: str, fix: str | None = None) -> dict:
        """전체 경로 폴백(앵커 실패·국소 무변경/가드레일 불통과) — 마지막 회차 동기 재생성(fix 지시 1회성 주입).
        regenerate_last_chapter 는 백업/스냅샷 복원 후 잡으로 재생성한다. 잡 완료까지 블로킹(러너 스레드 단일).
        회차 부풀림 없음(마지막 회차 in-place — 커서 불변). 잡 미기동(None)이면 무변경 반환(게이트가 소진 전진)."""
        out = self.svc.regenerate_last_chapter(pid, fix_instruction=(fix or ""))
        if out is None:
            st = self.load(pid)
            return {"completed": False, "record": None,
                    "current_chapter": (st.current_chapter if st else 0)}
        job, _created = out
        # 백그라운드 데몬 스레드가 generate_next_chapter 를 끝까지 수행 — 완료까지 대기(폴링).
        while getattr(job, "status", "") == "running":
            time.sleep(0.05)
        st = self.load(pid)
        rec = st.chapters[-1] if (st and st.chapters) else None
        return {"completed": False, "record": rec,
                "current_chapter": (st.current_chapter if st else 0)}

    def llm_call_count(self, pid: str) -> int | None:
        """P-8: 세션 provider 의 누적 chat_calls — 국소 revise/accept 실비용을 게이트 예산에 계상하기 위한 훅.
        세션/repo 부재·예외는 None(계상 0 방어 — 모의 경로 산술 불변)."""
        try:
            state = self.svc.repo.get(pid)
            if state is None:
                return None
            sess = self.svc.sessions.get_or_create(state)
            return int(sess.provider.usage.chat_calls)
        except Exception:
            return None


def _measure_from_record(record) -> dict:
    """measure-then-cite digest 재구성(신규 검출기 0·LLM 0콜·advisory) — 이미 계산된 회차 신호를 게이트 심사문에
    참고 수치로 넘긴다. 판정은 정독이 한다(chapter_gate._measure_digest 가 이 dict 를 요약). 결측은 조용히 생략.

    소스: record.ai_tell(정규식 KatFishNet + Kiwi 종결/층위) + record.verification.retread(도입부 재탕계수).
    러너의 measure_chapter(dp4b) 와 키 형태(kiwi.ending_profile·layer·retread.opening_coef)를 맞춰
    _measure_digest/_layer_digest 가 동일하게 소비하게 한다(tools 무의존)."""
    ai = getattr(record, "ai_tell", None) or {}
    meas: dict = {}
    kiwi = ai.get("kiwi") if isinstance(ai, dict) else None
    if kiwi:
        meas["kiwi"] = kiwi
    # verification.retread 는 build_verification 이 채운 도입부 재탕계수(엔진 이관 부품). MISSING 문자열/부재면 생략.
    ver = getattr(record, "verification", None) or {}
    rt = ver.get("retread") if isinstance(ver, dict) else None
    if isinstance(rt, dict):
        meas["retread"] = {"opening_coef": rt.get("opening_coef"),
                           "opening_jac": rt.get("opening_jac")}
    return meas


def _rhythm_spans(text: str) -> list:
    """리듬 스팬 검출(engine.style_pipeline — tools 부재 시 []). 게이트 fix_fn 이 없으면 검출만 하고 수리는 스킵."""
    try:
        from .style_pipeline import detect_rhythm_spans
        return detect_rhythm_spans(text or "", run_threshold=cg.RHYTHM_RUN_THRESHOLD)
    except Exception:
        return []


def _compact_gate(gate: dict) -> dict:
    """gate_committed_chapter 반환을 verification.gate 로 영속할 compact dict 로 정리(events 제외 — 라운드 요약만).
    무강제: 판정 라벨·색상 없음 — verdict/retention/재시도 이력(gate_rounds)·drop_trigger/fix_note 원자료만.
    제품 경로 표식(source='product')과 상한/재시도 산출을 함께 실어 작가 UI 가 재시도 이력을 그대로 전재한다."""
    rounds = []
    for r in (gate.get("gate_rounds") or []):
        rounds.append({"round": r.get("round"), "verdict": r.get("verdict"),
                       "drop_trigger": r.get("drop_trigger"), "fix_note": r.get("fix_note"),
                       "retention": r.get("retention"), "repair_path": r.get("repair_path"),
                       "reverted": r.get("reverted")})
    return {
        "source": "product",
        "verdict": gate.get("verdict"),
        "retention_est": gate.get("retention"),
        "retries": gate.get("retries"),
        "fail_exhausted": bool(gate.get("fail_exhausted")),
        "drop_trigger": gate.get("drop_trigger"),
        "fix_note": gate.get("fix_note"),
        "hate_comment": gate.get("hate_comment"),
        "gate_rounds": rounds,
    }


def run_product_gate(svc, pid: str, chapter: int, *, settings, judge_fn=None,
                     rhythm_fn=None) -> dict | None:
    """제품 경로 게이트 1회차 — 커밋된 FINALIZED 회차를 정독 게이트(심사→국소 revise/regen→최선 보존)에 태우고,
    compact gate 결과를 반환한다(호출부가 verification.gate 로 영속). 전부 락 밖에서 호출(경쟁 없음).

    judge_fn/rhythm_fn 미주입 시: judge=make_judge(cross-vendor), rhythm=engine.style_pipeline.detect_rhythm_spans.
    fix_fn=None(리듬 스팬은 검출·플래그만 — 국소 스팬 수리는 러너의 st11 v2 소관·제품 경로는 정독 게이트 국소 revise 로 수렴).
    LLM 콜 상한(product_gate_llm_cap) 초과 시 그 회차 게이트는 예외로 중단하고 부분 결과를 정직 표기(은폐 금지).
    실패/예외는 호출부가 흡수(게이트가 회차 발행을 막지 않음 — 무강제)."""
    harness = _ServiceGateHarness(svc, pid)

    def measure_fn(state, ch):
        st = harness.load(pid)
        rec = next((c for c in (st.chapters if st else []) if c.chapter == ch), None)
        return _measure_from_record(rec) if rec is not None else {}

    if judge_fn is None:
        # gen≠judge: prose 벤더(gen_model 계열)와 다른 벤더로 심사(create_role_provider 재사용).
        provider, _spec = cg.make_judge(settings, getattr(settings, "llm_provider", ""))

        def judge_fn(text, meas, ctx=None):   # noqa: F811 (지역 재바인딩 — 미주입 시에만)
            ctx = ctx or {}
            return cg.llm_gate(provider, text, meas,
                               story_so_far=ctx.get("story_so_far", ""),
                               genre=ctx.get("genre", ""))
    if rhythm_fn is None:
        rhythm_fn = _rhythm_spans

    budget = cg.CallBudget(int(getattr(settings, "product_gate_llm_cap", 12)))
    max_retries = int(getattr(settings, "product_gate_max_retries", cg.GATE_MAX_RETRIES))
    partial = None
    try:
        gate = cg.gate_committed_chapter(
            harness=harness, pid=pid, chapter=chapter, status="FINALIZED",
            judge_fn=judge_fn, fix_fn=None, budget=budget,
            measure_fn=measure_fn, rhythm_fn=rhythm_fn, max_retries=max_retries)
    except cg.LLMBudgetExceeded as e:
        # 콜 상한 초과 — 부분 결과를 정직 표기(은폐 금지). 회차는 그대로 발행 상태 유지(무강제).
        partial = {"source": "product", "verdict": "미완(콜 상한 초과)",
                   "budget_exceeded": str(e), "llm_calls": budget.used}
        return partial
    out = _compact_gate(gate)
    out["llm_calls"] = budget.used
    return out
