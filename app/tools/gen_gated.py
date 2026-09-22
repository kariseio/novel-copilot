# -*- coding: utf-8 -*-
"""DP-5 매화 게이트 출고 러너 — 생성→계측→(스팬수리)→LLM 정독 게이트→FAIL시 fix_note 재생성 루프의 내장화.

배경(docs/design-dp5-gated-runner.md): DP-4b 가 4.0→7.1 을 냈으나 그 순환(생성→결정론 계측→정독 판정→
fix_note 재생성)은 **세션의 에이전트가 손으로 돌려야만** 재현됐다(일회 실적). DP-5 는 그 루프를 러너로
굳혀 재현 가능하게 만든다 — 엔진 파일 0 변경, dp4b_loop·gen_longrun 의 검증된 부품 재사용.

회차당 루프(§2 의사코드 그대로):
    draft   = gen(ch)                         # gen_longrun 하네스 step(=dp4b cmd_gen 계보)
    meas    = measure(state, ch)              # 결정론 계측(dp4b gate_chapter — 원자료, 판정 아님)
    if 리듬 스팬 있음: fix_spans(v2)            # ST-11c 문단 리팩토링(무강제·폴백=원문)
    verdict = llm_gate(draft, meas)           # ★신설: cross-vendor LLM 정독(hair-trigger + dp4b 체크리스트)
    while verdict.FAIL and retries < R(=2):
        draft = regen(ch, fix=verdict.fix_note)   # dp4b cmd_regen 계보
        재계측·재수리·재심사
    기록(견본·게이트 로그·정독 사유 원문) 후 다음 화

중단/전진(kill criteria 내장 — 맹목 전진 금지):
 · 연속 FAIL-소진(R 다 쓰고도 FAIL) 3화 → **arm 중단**.
 · R 소진 단화(3연속 미달)는 FAIL 플래그로 기록하고 전진(은폐 금지 — manifest 에 그대로).
 · run 당 LLM 콜 상한(config) 초과 → 즉시 중단+기록(비용 가드).

gen_longrun 이식(불변): spine/cursor 가드 · --live --fire 이중 잠금 · run() confirm='FIRE' 2차 잠금 ·
 resume(게이트 로그 기준) · [실험] 태그 · 기존 작품 무수정 · manifest.

LLM 정독 게이트(신설 유일 부품):
 · 심사 모델 = prose 모델과 **다른 벤더**(gen≠judge — create_role_provider 라우팅 재사용).
 · 프롬프트 = dp4b 게이트 체크리스트(measure-then-cite) + hair-trigger 독자 프로토콜(기본값=하차·닫을
   이유 먼저·원문 인용 의무). 산출 {verdict, drop_trigger(원문 인용), fix_note(스팬 지목), retention_est}.

검증(전부 모의 — LLM 0콜): 재시도 상한 산술 · 연속 FAIL 중단 · FAIL 플래그 전진 · resume 재개점 ·
 이중 잠금 · 콜 상한. → tools/test_dp5_gated_runner.py

실행(app/ 에서):
  PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/gen_gated.py --dry-run --chapters 4
  PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/gen_gated.py --live --fire --chapters 6   # LR-1 결재 후
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # app/ → novelcopilot·tools 임포트

from novelcopilot.domain.project import ProjectSeed

# gen_longrun 부품 재사용(러너 골격·가드·이중 잠금·하네스) — CLI 동작·가드 의미 불변으로 import
import tools.gen_longrun as lr
from tools.gen_longrun import (
    check_spine, check_cursor, guard_event, _rec_status, _rec_field,
    DryRunHarness, LiveHarness, _stdin_isatty,
)

# PR-1: 게이트의 tools-독립 코어는 engine 으로 승격 이동(이중 구현 소멸 — 러너·제품 경로 공용 SSOT).
#   여기서 그 엔진 부품을 import 해 러너로 쓰고, tools 바운드 계측/수리(measure_chapter·rhythm_spans)만
#   아래 얇은 래퍼로 주입한다. 재노출(re-export)로 g.<name> 심볼 표면은 불변(기존 테스트 GREEN 유지).
from novelcopilot.engine import chapter_gate as _cg
from novelcopilot.engine.chapter_gate import (
    GATE_MAX_RETRIES, FAIL_ABORT_STREAK, DEFAULT_LLM_CALL_CAP, RHYTHM_RUN_THRESHOLD,
    CallBudget, LLMBudgetExceeded, GATE_SYSTEM, _layer_digest, _measure_digest, llm_gate,
    make_judge, anchor_drop_trigger, _split_paragraphs, _norm_ws, _positional_arity,
    _chapter_text, _story_so_far, _regen, _revise, _undo, _llm_call_count,
)

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# ── 게이트 파라미터(설계 §2·§3 등록값) — 엔진에서 승격 import(상단). 러너 전용 상수만 여기 유지. ──
DP5_TAG = "[실험 DP-5]"       # experiments-visible-on-web 규칙 — 라이브 작품 제목 태그
# 정식 승격 작품 — 사용자 지시로 제목의 [실험] 태그를 뗀 연재작. reattach 무수정 가드의 명시 예외
#   (태그 부재≠기존 작품 오발사 — 이 목록은 '이 러너로 계속 출고하는 승격작'의 결정론 허용목록).
#   2026-07-20 괴담작 「싸구려 퇴마사가 괴담을 너무 잘 잡음」 승격(사용자 "제목에서 실험 빼줘").
DP5_PROMOTED_PIDS = {"08bb0875ee75", "o5rewriteall"}   # o5rewriteall: opus5 재집필선 정식 승격(사용자 지시 2026-07-31)
LIVE_CONFIRM = lr.LIVE_CONFIRM  # 'FIRE' — gen_longrun 과 동일 2차 잠금 토큰(재정의 아님)
ESCALATED_STREAK_FLAG = lr.ESCALATED_STREAK_FLAG  # 2 — gen_longrun 가드 ③ 임계(연속 ESCALATED 플래그, 의미 불변)


# ─────────────────────────────────────────────────────────────────────────────
# 게이트 하네스 — gen_longrun 의 step(=다음 회차 생성) 계약에 dp4b 의 regen(=마지막 회차 in-place
# 재생성, 커서 불변)을 더한다. gen_longrun.DryRunHarness/LiveHarness 는 손대지 않는다(그 step 의미·
# 가드 셀프테스트 불변) — 여기서 서브클래싱으로 regen 계약만 추가.
# ─────────────────────────────────────────────────────────────────────────────
class GatedDryRunHarness(DryRunHarness):
    """DryRunHarness + regen(pid, fix): 마지막 회차 본문을 fix 반영본으로 '제자리 교체'(커서 전진 없음).
    dp4b cmd_regen 계보 — regen 은 새 회차를 만들지 않는다(FAIL→fix_note 재생성이 회차 수를 부풀리면
    타깃 예산·커서가 오염되므로). regen 도 raise_at 결함 주입 계약(attempt 증가)을 공유한다."""

    def regen(self, pid: str, fix: str | None = None) -> dict:
        self._attempt += 1
        if self._attempt in set(self.faults.get("raise_at") or ()):
            raise RuntimeError(f"dry-run 주입 regen 예외(attempt {self._attempt})")
        st = self.repo.get(pid)
        if not st.chapters:
            return {"completed": False, "record": None, "current_chapter": st.current_chapter}
        rec = st.chapters[-1]
        n = rec.chapter
        rec.text = f"[dry-run regen] {n}화 재생성 본문(fix={(fix or '')[:40]}) — LLM 0콜."
        self.repo.save(st)   # 커서(current_chapter) 불변 — 마지막 회차만 교체
        return {"completed": False, "record": rec, "current_chapter": st.current_chapter}

    # ── DP-21 §3: revise/accept/undo 결함 주입 계약(국소 경로 라우팅·최선 판본 보존 검증용, LLM 0콜) ──
    #   faults 로 조작:
    #     revise_changed: bool  → revise 무변경 폴백(False) vs 변경됨(True, 기본). regen 폴백 라우팅 검증.
    #     revise_guardrail: bool → 후보 가드레일 통과(True, 기본) vs 불통과(False). accept 안 함 → regen 폴백.
    #     revise_raise: bool    → revise 호출 시 예외(게이트 예외 재시도 경로 재료).
    #   실제 도메인 모델(ChapterRecord.revisions)에 append/복원 → undo 가 실제 이력을 되감는다(라이브와 동형).
    def revise(self, pid: str, chapter: int, directive: str, span_text: str = "") -> dict | None:
        if self.faults.get("revise_raise"):
            raise RuntimeError("dry-run 주입 revise 예외")
        st = self.repo.get(pid)
        rec = next((c for c in st.chapters if c.chapter == chapter), None)
        if rec is None:
            return None
        before = rec.text or ""
        changed = self.faults.get("revise_changed", True)
        gpass = self.faults.get("revise_guardrail", True)
        after = (before + f"\n[dry-run revise] 스팬 국소 퇴고(dir={(directive or '')[:24]}) — LLM 0콜.") \
            if changed else before
        return {"revision_id": (uuid.uuid4().hex[:12] if changed else None),
                "before_text": before, "after_text": after, "span_text": span_text,
                "changed": bool(changed),
                "guardrail": {"passed": bool(gpass), "reason": "" if gpass else "dry-run 주입 가드레일 불통과"}}

    def accept(self, pid: str, chapter: int, revision_id, after_text_fb=None,
               span_text_fb=None, passes_fb=None) -> dict | None:
        from novelcopilot.domain.types import ChapterRevision
        st = self.repo.get(pid)
        rec = next((c for c in st.chapters if c.chapter == chapter), None)
        if rec is None:
            return None
        before = rec.text or ""
        after = after_text_fb if after_text_fb is not None else before
        # ⓓ 회귀 방어(dry-run 동형): revision_id 가 None/빈값이면 서버가 부여(default_factory 우회 크래시 차단).
        rid = revision_id or uuid.uuid4().hex[:12]
        rec.revisions.append(ChapterRevision(
            revision_id=rid, directive="(dry-run)", span_text=(span_text_fb or ""),
            before_text=before, after_text=after,
            before_summary=rec.summary, before_detail_synopsis=rec.detail_synopsis,
            guardrail_passed=True, created_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
        rec.text = after
        self.repo.save(st)   # 커서 불변 — 본문 in-place 교체(라이브 accept 와 동형)
        return {"accepted": True, "revision_count": len(rec.revisions)}

    def undo(self, pid: str, chapter: int) -> dict:
        st = self.repo.get(pid)
        rec = next((c for c in st.chapters if c.chapter == chapter), None)
        if rec is None:
            raise ValueError("대상 회차 없음")
        last = next((r for r in reversed(rec.revisions) if not r.reverted), None)
        if last is None:
            raise ValueError("되돌릴 퇴고 이력이 없습니다")
        rec.text = last.before_text        # 결정론 복원(text·summary 스냅샷)
        rec.summary = last.before_summary
        last.reverted = True
        self.repo.save(st)
        return {"reverted": True, "revision_id": last.revision_id}


class GatedLiveHarness(LiveHarness):
    """LiveHarness + regen: svc.regenerate_last_chapter(dp4b cmd_regen 실경로) 로 마지막 회차 제자리 재생성.
    LR-1 결재 전 사용 금지(main() --live 게이트 + run() confirm='FIRE' 통과 후에만 생성)."""

    def __init__(self, seed: ProjectSeed, pov: str = ""):
        # DP-18: --pov 는 seed.pov 로 전달(사후 패치 폐기 — 정식 배선). create_project 가 worldgen 전에
        #  world.style.pov 에 반영 → first 면 generator 의 narrator_voice 도출(증거 게이트)이 정상 경로로
        #  실행된다(사후 패치의 first 전용 voice 도출 스킵 부작용 소스 해소). 빈값=무지정(기존 경로 바이트 동일).
        if pov:
            seed = seed.model_copy(update={"pov": pov})
        super().__init__(seed)

    def create(self) -> tuple[str, "ProjectState"]:
        pid, st = super().create()
        # 태그는 worldgen '후' 부착(dp4b cmd_create 선례) — 시드 제목에 미리 넣으면 worldgen 입력이
        # 오염되어 태그가 작중 설정으로 역류한다(실측 2026-07-09: "'관측자 특성 말소' 실험(DP-5)" 플롯 발명 —
        # B-34/B-35 프롬프트 잔재 노출 계보). experiments-visible-on-web 태그 의무는 여기서 이행.
        # pov/voice 는 여기서 손대지 않는다 — seed.pov 로 create_project(worldgen) 시점에 이미 정식 배선됨(DP-18).
        if DP5_TAG not in (st.world.title or ""):
            st.world.title = f"{DP5_TAG} {(st.world.title or '').strip()}".strip()
            self.repo.save(st)
            st = self.repo.get(pid)
        return pid, st

    def regen(self, pid: str, fix: str | None = None) -> dict:
        out = self.svc.regenerate_last_chapter(pid, fix_instruction=(fix or ""))
        if out is None:
            return {"completed": False, "record": None,
                    "current_chapter": self.load(pid).current_chapter}
        job, _created = out
        while getattr(job, "status", "") == "running":
            time.sleep(1.0)
        st = self.load(pid)
        rec = st.chapters[-1] if st.chapters else None
        return {"completed": False, "record": rec, "current_chapter": st.current_chapter}

    # ── DP-21 §3: 국소 경로(revise/accept/undo) 실경로 배선 — 기존 작가 기능(copilot.py)의 기계화.
    #   revise_chapter 는 후보만 생성(저장 안 함), accept_revision 이 서버 가드레일 재검증 후 본문 교체·이력 push,
    #   undo_revision 이 마지막 채택을 결정론 복원(text·summary·RAG 정합). 러너의 _revise/_undo 어댑터가 소비.
    def revise(self, pid: str, chapter: int, directive: str, span_text: str = "") -> dict | None:
        return self.svc.revise_chapter(pid, chapter, directive, span_text=(span_text or ""))

    def accept(self, pid: str, chapter: int, revision_id, after_text_fb=None,
               span_text_fb=None, passes_fb=None) -> dict | None:
        return self.svc.accept_revision(pid, chapter, revision_id,
                                        after_text_fb=after_text_fb, span_text_fb=span_text_fb,
                                        passes_fb=passes_fb)

    def undo(self, pid: str, chapter: int) -> dict:
        return self.svc.undo_revision(pid, chapter)

    # P-8: 라이브 국소 경로(revise/accept)가 실제로 소비한 LLM 콜 수를 CallBudget 에 계상하기 위한 계측 훅.
    #   세션 provider 의 누적 chat_calls(인스턴스별 usage — base.Usage)를 그대로 노출한다. _revise 가 revise+accept
    #   전후로 이 값을 읽어 델타만큼 charge → DP-21 국소 경로의 실비용이 심사 예산 가드에 반영된다(비용 가드 정합).
    #   드라이런 하네스는 이 훅이 없어 델타=0(모의 경로 산술 불변). 세션 미생성 등 예외 시 None → 계상 0(방어).
    def llm_call_count(self, pid: str) -> int | None:
        try:
            state = self.repo.get(pid)
            if state is None:
                return None
            sess = self.svc.sessions.get_or_create(state)
            return int(sess.provider.usage.chat_calls)
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# CallBudget/LLMBudgetExceeded 는 engine.chapter_gate 로 승격 이동(상단 import 재노출).
# 아래 두 부품(measure_chapter·rhythm_spans)은 tools 바운드(dp4b/st11)라 러너에 남고,
# 게이트 코어(engine)에는 아래 얇은 래퍼가 measure_fn/rhythm_fn 으로 주입한다(엔진 tools 무의존).
# ─────────────────────────────────────────────────────────────────────────────
# 결정론 계측(dp4b gate_chapter 재사용) + 리듬 스팬 검출(ST-11)
def measure_chapter(state, chapter: int) -> dict:
    """dp4b_loop.gate_chapter 로 결정론 계측(원자료·판정 아님). LLM 0콜.

    ★MED 수리(외부 beats 누수 차단): gate_chapter 는 모듈 전역 dp4b_loop.BEATS_LOG(=reports/dp4b_beats.jsonl)
    를 회차번호 키로 읽는다. gen_gated 는 그 파일을 쓰지 않으므로, 라이브에서 과거 DP-4b 런의 동번호
    beats 가 이 작품의 계측(retread.keyevents_overlap_prev·protagonist_move·consumption_ledger)에
    잘못 접붙는 교차작품 오염이 발생한다. gen_gated 전용 beats 경로(없으면 빈 dict)로 격리해 읽게 한다 —
    gate_chapter 로직은 불변(그대로), beats 소스만 스코핑."""
    import tools.dp4b_loop as dp4b
    gated_beats = REPORTS_DIR / "dp5_gated_beats.jsonl"   # gen_gated 전용(현재 미기록 → 빈 beats = 오염 0)
    saved = dp4b.BEATS_LOG
    dp4b.BEATS_LOG = gated_beats
    try:
        return dp4b.gate_chapter(state, chapter)
    finally:
        dp4b.BEATS_LOG = saved


def rhythm_spans(text: str, run_threshold: int = RHYTHM_RUN_THRESHOLD) -> list[dict]:
    """ST-11 리듬 스팬 검출(결정론) — '~다 run'·파편 클러스터 스팬. 있으면 fix_spans v2 트리거.

    ST-10 §2ⓐ: 문장 분리를 kiwi_metrics.split_sents 로(인용문 인식 — ch8 절단·ch12 스팬 어긋남 소스 수리).
    Kiwi 미설치/로드 실패 시 split_sents 는 기존 정규식으로 강등되므로 스팬 경계가 종전과 바이트 동일로 수렴한다."""
    import tools.st11_span_rewrite as st11
    import tools.kiwi_metrics as km
    return st11.extract_rhythm_spans(text or "", run_threshold=run_threshold,
                                     sent_splitter=km.split_sents)


# ─────────────────────────────────────────────────────────────────────────────
# 게이트 코어 래퍼 — engine.chapter_gate 의 tools-독립 루프에 tools 바운드 계측/수리를 주입한다.
#   measure_chapter·rhythm_spans 는 '모듈 전역에서 호출 시점에' 해석되므로 기존 테스트의
#   monkeypatch(g.rhythm_spans / g.measure_chapter) 가 그대로 발화한다(재노출 표면·계약 불변).
#   guard_event/_rec_status 는 gen_longrun 이식본을 주입(러너 이벤트 형식·상태 판별 불변).
# ─────────────────────────────────────────────────────────────────────────────
def gen_step(*, harness, pid: str) -> dict:
    """engine.gen_step 래퍼 — _rec_status(gen_longrun) 주입. (계약·반환 불변)"""
    return _cg.gen_step(harness=harness, pid=pid, rec_status_fn=_rec_status, event_fn=guard_event)


def gate_committed_chapter(*, harness, pid: str, chapter: int, status: str,
                           judge_fn, fix_fn, budget, max_retries: int = GATE_MAX_RETRIES) -> dict:
    """engine.gate_committed_chapter 래퍼 — tools 바운드 measure_chapter·rhythm_spans 를 주입한다.
    두 함수는 이 모듈 전역에서 매 호출 해석되므로 monkeypatch(g.rhythm_spans 등)가 반영된다.
    시그니처·반환은 종전과 동일(테스트가 부르는 키워드 그대로)."""
    return _cg.gate_committed_chapter(
        harness=harness, pid=pid, chapter=chapter, status=status,
        judge_fn=judge_fn, fix_fn=fix_fn, budget=budget,
        measure_fn=measure_chapter, rhythm_fn=rhythm_spans, event_fn=guard_event,
        max_retries=max_retries)


def persist_gate_verification(harness, pid: str, chapter: int, gate: dict) -> bool:
    """러너 게이트 verdict 를 ChapterRecord.verification.gate 로 영속(PR-2 SSOT — ST-12a 검증 런 적발 2026-07-14).

    PR-1 계약("러너 jsonl 무덤 해소")이 제품 경로에만 이행돼, 러너 경로는 게이트가 실제로 돌았는데도
    verification.gate 가 '미실행'으로 남는 **허위 결측**이 있었다. 제품 경로(copilot 2015행)와 동일하게
    build_verification(gate=compact) 재집계 후 저장. source='runner' 표식. 실패는 False 반환(호출부가
    flag 이벤트로 가시화 — 은폐 금지, 게이트 결과 자체는 jsonl 에 이미 있음)."""
    if getattr(harness, "repo", None) is None:
        return True   # 비영속 하네스(dry-run 등) — 영속 대상 자체가 없음(실패 아님·플래그 미발화)
    try:
        from novelcopilot.engine.product_gate import _compact_gate
        from novelcopilot.engine.verification import build_verification
        st = harness.load(pid)
        rec = next((c for c in (st.chapters if st else []) if c.chapter == chapter), None)
        if rec is None:
            return False
        compact = _compact_gate(gate)
        compact["source"] = "runner"
        prev = [c.text for c in sorted(st.chapters, key=lambda c: c.chapter)
                if str(getattr(c, "status", "")).endswith("FINALIZED") and c.chapter < rec.chapter and c.text]
        target = getattr(getattr(st.world, "style", None), "target_chars_per_chapter", None)
        # HZ-1 ⑤(실측 버그 수리): cold_read 는 게이트가 새로 돌리지 않는 축이다. gate 이월과 동형으로, build_verification
        #   재집계 시 기존 verification.cold_read(dict)를 그대로 이월한다 — 미이월 시 MISSING 으로 덮여 MD-1 4개 화의
        #   콜드리드 실측이 '미실행'으로 소실됐다(러너 경로만 누락). 기존이 실판정(dict)일 때만 이월("미실행" 문자열은 무시).
        _prior_cr = (rec.verification or {}).get("cold_read")
        _prior_cr = _prior_cr if isinstance(_prior_cr, dict) else None
        rec.verification = build_verification(rec, prev_texts=prev, target_chars=target,
                                              gate=compact, cold_read=_prior_cr)
        harness.repo.save(st)
        return True
    except Exception:
        return False


def gate_one_chapter(*, harness, pid: str, judge_fn, fix_fn, budget,
                     max_retries: int = GATE_MAX_RETRIES) -> dict:
    """engine.gate_one_chapter 래퍼 — measure/rhythm/이벤트/상태 판별 주입(단발 편의용, 계약 불변)."""
    return _cg.gate_one_chapter(
        harness=harness, pid=pid, judge_fn=judge_fn, fix_fn=fix_fn, budget=budget,
        measure_fn=measure_chapter, rhythm_fn=rhythm_spans, event_fn=guard_event,
        rec_status_fn=_rec_status, max_retries=max_retries)


# ─────────────────────────────────────────────────────────────────────────────
# arm 러너 — gen_longrun.run_arm 의 가드(spine/cursor/예외 재시도)를 게이트 루프로 감싼다.
# ─────────────────────────────────────────────────────────────────────────────
def run_gated_arm(*, harness, arm_no: int, target_chapters: int, judge_fn, fix_fn,
                  budget: CallBudget, log_path: Path,
                  max_retries: int = GATE_MAX_RETRIES,
                  fail_abort_streak: int = FAIL_ABORT_STREAK,
                  reattach_pid: str = "") -> dict:
    """arm 1개 = 작품 생성 + 최대 target_chapters 회 게이트 회차. gen_longrun 가드 + DP-5 게이트 kill.

    kill criteria: 연속 FAIL-소진 fail_abort_streak(=3)화 → arm 중단. R 소진 단화는 플래그 전진.
    LLM 콜 상한(budget) 초과 → LLMBudgetExceeded 로 즉시 중단+기록. spine/cursor/예외는 gen_longrun 계약 그대로.

    reattach_pid: 기존 작품에 이어붙기(1화씩 분할 출고용). create 를 건너뛰고 load — 단
    [실험 DP-5] 태그 작품만 허용(기존 작품 무수정 가드, spine 가드와 동형의 abort).
    baseline=현재 커서이므로 target_chapters 는 '이번 run 에서 추가 출고할 회차 수'가 된다.
    """
    t0 = time.monotonic()
    events: list[dict] = []
    log_lines: list[dict] = []
    pid, title = "", ""
    baseline = chapters_done = attempts = 0
    aborted, abort_reason, completed_reason = False, "", ""
    fail_streak = 0
    esc_streak = 0
    try:
        if reattach_pid:
            pid = reattach_pid
            st0 = harness.load(pid)
        else:
            pid, st0 = harness.create()
        title = st0.world.title
        baseline = st0.current_chapter
        # 가드 ⓪ — reattach 대상 태그 검증(기존 작품 무수정). 승격작(DP5_PROMOTED_PIDS)은 명시 예외.
        if reattach_pid and DP5_TAG not in (title or "") and reattach_pid not in DP5_PROMOTED_PIDS:
            detail = f"reattach 대상이 {DP5_TAG} 작품이 아님: '{title}'"
            events.append(guard_event("reattach_tag", baseline, "abort", detail))
            aborted, abort_reason = True, f"reattach_tag: {detail}"
        # 가드 ① — arm 시작 spine 검증(gen_longrun 계약 그대로)
        detail = None if aborted else check_spine(st0)
        if detail:
            events.append(guard_event("spine", st0.current_chapter, "abort", detail))
            aborted, abort_reason = True, f"spine: {detail}"
        while not aborted and attempts < target_chapters and chapters_done < target_chapters:
            attempts += 1
            ts = time.monotonic()
            step_events: list[dict] = []

            # ── 단계 1: 생성(step). 예외 = 회차 미커밋 → 전체 1회 재시도 안전(가드 ⑤). ──
            # ★HIGH 수리: step 재시도는 여기(커밋 이전)에만 한정. step 성공 후의 계측/수리/심사
            # 예외는 아래 게이트 단계에서만 재시도되어 step 을 다시 부르지 않는다 → 미심사 회차 중복 생성 차단.
            try:
                step = gen_step(harness=harness, pid=pid)
            except LLMBudgetExceeded:
                raise
            except Exception as e:
                ev = guard_event("step_retry", baseline + chapters_done + 1, "flag",
                                 f"생성(step) 예외 1회 재시도: {type(e).__name__}: {str(e)[:200]}")
                events.append(ev); step_events.append(ev)
                step = gen_step(harness=harness, pid=pid)   # 재실패 → 바깥 except 가 arm 중단 수렴
            events.extend(step.get("events") or [])
            step_events.extend(step.get("events") or [])

            if step.get("completed"):
                completed_reason = str(step.get("reason") or "completed")
                log_lines.append({"arm": arm_no, "attempt": attempts, "chapter": chapters_done,
                                  "status": "COMPLETED", "guard_events": step_events,
                                  "elapsed_sec": round(time.monotonic() - ts, 3)})
                break

            # ── 가드 ③(gen_longrun 이식) — ESCALATED 연속 스트릭(플래그만, 중단 아님). ──
            # 게이트 콜 없이 회차 미전진 → 심사 스트릭(fail_streak)과 독립. 전건 ESCALATED 무진행 은폐 방지.
            if step.get("status") == "ESCALATED":
                rec = step
            else:
                # ── 단계 2: 게이트(계측→수리→심사→regen). step 을 재호출하지 않음(커서 멱등). ──
                try:
                    gate = gate_committed_chapter(
                        harness=harness, pid=pid, chapter=step["chapter"], status=step["status"],
                        judge_fn=judge_fn, fix_fn=fix_fn, budget=budget, max_retries=max_retries)
                except LLMBudgetExceeded:
                    raise
                except Exception as e:
                    # ★HIGH 수리: 커밋된 회차의 게이트 예외 1회 재시도 — step 재호출 없이 계측/심사만 재시도.
                    ev = guard_event("gate_retry", step["chapter"], "flag",
                                     f"게이트(계측/심사) 예외 1회 재시도: {type(e).__name__}: {str(e)[:200]}")
                    events.append(ev); step_events.append(ev)
                    gate = gate_committed_chapter(
                        harness=harness, pid=pid, chapter=step["chapter"], status=step["status"],
                        judge_fn=judge_fn, fix_fn=fix_fn, budget=budget, max_retries=max_retries)
                events.extend(gate.get("events") or [])
                step_events.extend(gate.get("events") or [])
                # PR-2 SSOT: 게이트 verdict 를 verification.gate 로 영속(제품 경로 대칭 — jsonl 무덤·허위 결측 금지)
                _gch = int(gate.get("chapter") or step.get("chapter") or 0)
                if _gch and not persist_gate_verification(harness, pid, _gch, gate):
                    ev = guard_event("gate_persist", _gch, "flag",
                                     "게이트 verdict 의 verification.gate 영속 실패 — jsonl 에만 기록됨")
                    events.append(ev); step_events.append(ev)
                if gate.get("completed"):
                    completed_reason = str(gate.get("reason") or "completed")
                    log_lines.append({"arm": arm_no, "attempt": attempts, "chapter": chapters_done,
                                      "status": "COMPLETED", "guard_events": step_events,
                                      "elapsed_sec": round(time.monotonic() - ts, 3)})
                    break
                rec = gate

            status = rec.get("status") or ""
            rec_ch = int(rec.get("chapter") or (baseline + chapters_done + 1))
            # 가드 ② — 매 회차 후 디스크 권위 재읽기로 커서 정합(gen_longrun 계약)
            st = harness.load(pid)
            chapters_done = st.current_chapter - baseline
            detail = check_cursor(st)
            if detail:
                ev = guard_event("cursor", st.current_chapter, "abort", detail)
                events.append(ev); step_events.append(ev)
                aborted, abort_reason = True, f"cursor: {detail}"
            # ── 가드 ③ — ESCALATED 연속 2회 플래그(중단 아님, gen_longrun 의미 불변) ──
            if status == "ESCALATED":
                esc_streak += 1
                if esc_streak >= ESCALATED_STREAK_FLAG:
                    ev = guard_event("escalated_streak", rec_ch, "flag",
                                     f"ESCALATED 연속 {esc_streak}회(회차 {rec_ch} 미전진)")
                    events.append(ev); step_events.append(ev)
            else:
                esc_streak = 0
            # ── DP-5 kill: 연속 FAIL-소진 스트릭 ──
            if rec.get("fail_exhausted"):
                fail_streak += 1
                if fail_streak >= fail_abort_streak:
                    ev = guard_event("gate_fail_streak", rec_ch, "abort",
                                     f"연속 FAIL-소진 {fail_streak}화 — arm 중단(맹목 전진 금지)")
                    events.append(ev); step_events.append(ev)
                    aborted, abort_reason = True, f"gate_fail_streak: {fail_streak}화 연속 소진"
            elif status != "ESCALATED":
                fail_streak = 0   # ESCALATED 는 심사 스트릭에 무영향(게이트 콜 없음) — 리셋도 안 함
            log_lines.append({"arm": arm_no, "attempt": attempts, "chapter": rec_ch,
                              "status": status, "verdict": rec.get("verdict"),
                              "retries": rec.get("retries"), "fail_exhausted": rec.get("fail_exhausted"),
                              "retention": rec.get("retention"),
                              "drop_trigger": (rec.get("drop_trigger") or "")[:200],
                              "gate_rounds": rec.get("gate_rounds") or [],   # P-3: 라운드별 심사문 jsonl 영속
                              "guard_events": step_events,
                              "elapsed_sec": round(time.monotonic() - ts, 3)})
    except LLMBudgetExceeded as e:
        # 콜 상한 초과 — 전용 llm_cap abort(+로그). step/gate 어느 단계에서 터져도 동일 형식으로 수렴.
        ev = guard_event("llm_cap", baseline + chapters_done + 1, "abort", str(e))
        events.append(ev)
        aborted, abort_reason = True, f"llm_cap: {e}"
        log_lines.append({"arm": arm_no, "attempt": attempts, "chapter": chapters_done,
                          "status": "LLM_CAP", "guard_events": [ev],
                          "elapsed_sec": 0.0})
    except Exception as e:
        aborted = True
        abort_reason = f"exception: {type(e).__name__}: {str(e)[:200]}"
        events.append(guard_event("exception", baseline + chapters_done + 1, "abort", abort_reason))
    finally:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as f:
                for line in log_lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass
    fail_exhausted_total = sum(1 for ln in log_lines if ln.get("fail_exhausted"))
    pass_total = sum(1 for ln in log_lines if ln.get("verdict") == "PASS")
    return {"arm": arm_no, "project_id": pid, "title": title,
            "target_chapters": target_chapters, "chapters_done": chapters_done,
            "attempts": attempts, "aborted": aborted, "abort_reason": abort_reason,
            "completed_early": completed_reason,
            "gate_pass": pass_total, "gate_fail_exhausted": fail_exhausted_total,
            "guard_events": events, "log_path": str(log_path),
            "llm_calls": budget.used,
            "elapsed_sec": round(time.monotonic() - t0, 3)}


# ─────────────────────────────────────────────────────────────────────────────
# resume — 게이트 로그(jsonl)에서 이미 통과(PASS)한 최대 회차를 읽어 재개점 산출.
# ─────────────────────────────────────────────────────────────────────────────
def resume_point(log_path: Path) -> int:
    """게이트 로그에서 '이미 완료(PASS·소진 전진·ⓑ 복원 전진)한' 최대 회차 반환(없으면 0).
    dp4b/lr1 resume 패턴(이미 심사한 회차 스킵)과 동형 — 콜/생성 중복 방지."""
    if not log_path.exists():
        return 0
    done = 0
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            # 회차가 확정 전진한 라인만. LLM_CAP/COMPLETED/미전진은 재개점 아님.
            #  · PASS 또는 fail_exhausted(소진 전진).
            #  · DP-21 ⓑ: 국소 악화 복원으로 재시도를 끊고 '최선 판본으로 전진'한 회차 — verdict 는 FAIL 이고
            #    fail_exhausted 도 아니지만(kill 스트릭 제외) 회차는 확정 커밋됐다. gate_rounds 의 reverted=True 로 식별.
            advanced = (row.get("verdict") in ("PASS",) or row.get("fail_exhausted")
                        or any(r.get("reverted") for r in (row.get("gate_rounds") or [])))
            if advanced:
                done = max(done, int(row.get("chapter") or 0))
    except Exception:
        return done
    return done


# ─────────────────────────────────────────────────────────────────────────────
# run — works×arm 반복, manifest 기록. 이중 잠금(gen_longrun 계승 — live 는 confirm='FIRE' 필수).
# ─────────────────────────────────────────────────────────────────────────────
def run(*, mode: str, seed: ProjectSeed, works: int, chapters: int,
        out_dir: Path | None = None, harness_factory=None, judge_factory=None,
        fix_factory=None, llm_cap: int = DEFAULT_LLM_CALL_CAP,
        max_retries: int = GATE_MAX_RETRIES, fail_abort_streak: int = FAIL_ABORT_STREAK,
        confirm: str = "", pov: str = "", pid: str = "") -> dict:
    """DP-5 게이트 런. 2차 잠금: mode='live' 는 confirm='FIRE' 정확 일치 필수(gen_longrun 과 동일 잠금 —
    CLI 게이트를 우회하는 프로그래매틱 import 경로도 명시 승인 없이는 발사 불가). dry-run 은 confirm 무영향.

    harness_factory(arm_no)->harness / judge_factory()->judge_fn / fix_factory()->fix_fn 주입 가능(테스트용).
    기본: dry-run=DryRunHarness + 모의 PASS judge(LLM 0콜) / live=LiveHarness + cross-vendor LLM judge + fix_spans.
    """
    assert mode in ("dry-run", "live"), f"unknown mode: {mode}"
    if pid:
        # reattach(1화씩 분할 출고)는 라이브 단일 arm 전용 — dry-run 합성 하네스에는 기존 pid 가 없다.
        assert mode == "live" and works == 1, "pid reattach 는 mode='live'·works=1 전용"
    if mode == "live" and confirm != LIVE_CONFIRM:
        raise PermissionError(
            "run(mode='live') 는 confirm='FIRE' 명시 인자가 필요합니다"
            "(실발사 2차 잠금 — CLI --live 게이트와 독립). 검증은 mode='dry-run'.")
    out = Path(out_dir) if out_dir else REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    t0 = time.monotonic()
    budget = CallBudget(llm_cap)

    # ── 하네스/심사/수리 팩토리 기본값 배선 ──
    live_bundle = None
    if harness_factory is None:
        if mode == "dry-run":
            harness_factory = lambda i: GatedDryRunHarness(_tagged_seed(seed))
        else:
            live_bundle = _LiveGatedBundle(seed, pov=pov)
            harness_factory = lambda i: live_bundle.harness
    if judge_factory is None:
        judge_factory = (_pass_judge_factory if mode == "dry-run"
                         else (live_bundle.judge_fn if live_bundle else _pass_judge_factory))
    if fix_factory is None:
        fix_factory = ((lambda: None) if mode == "dry-run"
                       else (live_bundle.fix_fn if live_bundle else (lambda: None)))

    arms = []
    for i in range(1, max(1, works) + 1):
        log_path = out / f"gated_{run_id}_arm{i}.jsonl"
        arm = run_gated_arm(harness=harness_factory(i), arm_no=i, target_chapters=chapters,
                            judge_fn=judge_factory(), fix_fn=fix_factory(), budget=budget,
                            log_path=log_path, max_retries=max_retries, reattach_pid=pid,
                            fail_abort_streak=fail_abort_streak)
        arms.append(arm)
        flag_n = sum(1 for e in arm["guard_events"] if e["action"] == "flag")
        print(f"  arm{i} [{arm['title']}] {arm['chapters_done']}/{chapters}화 "
              f"PASS{arm['gate_pass']} FAIL소진{arm['gate_fail_exhausted']} "
              f"{'중단(' + arm['abort_reason'][:80] + ')' if arm['aborted'] else '정상'} "
              f"플래그{flag_n} llm콜{arm['llm_calls']} {arm['elapsed_sec']}s", flush=True)

    st_result = guard_selftest(out, run_id) if mode == "dry-run" else None
    manifest_path = out / f"gated_{run_id}_manifest.json"
    manifest = {
        "ticket": "DP-5", "run_id": run_id, "mode": mode,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {"seed_title": seed.title, "genre": seed.genre,
                   "target_chapters": seed.target_chapters, "works": works, "chapters": chapters,
                   "max_retries": max_retries, "fail_abort_streak": fail_abort_streak,
                   "llm_cap": llm_cap},
        "arms": arms,
        "llm_calls_total": budget.used,
        "gate_selftest": st_result,
        # GREEN = 무중단 AND 셀프테스트 통과 AND '모든 arm 이 실제로 회차를 하나 이상 전진'.
        # 전건 ESCALATED(chapters_done==0)로 무진행한 arm 은 GREEN 아님(생성 0인데 GREEN 보고 방지 —
        # 리뷰 지적 false-GREEN 차단. 가드 ③ 의 flag-only 의미는 불변, run 레벨 판정만 엄격화).
        "green": (all(not a["aborted"] for a in arms)
                  and all(a["chapters_done"] > 0 for a in arms)
                  and (st_result is None or bool(st_result["pass"]))),
        "elapsed_sec": round(time.monotonic() - t0, 3),
        "manifest_path": str(manifest_path),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def _tagged_seed(seed: ProjectSeed) -> ProjectSeed:
    """라이브/합성 작품 제목에 [실험 DP-5] 태그(experiments-visible-on-web 규칙). 기존 작품 무수정 — 신작만."""
    s = seed.model_copy(deep=True)
    title = (s.title or s.genre or "무제").strip()
    if DP5_TAG not in title:
        s.title = f"{DP5_TAG} {title}"
    return s


# ─────────────────────────────────────────────────────────────────────────────
# 기본 심사/하네스 팩토리 — dry-run 은 LLM 0콜 모의 PASS, live 는 cross-vendor 실심사.
# ─────────────────────────────────────────────────────────────────────────────
def _pass_judge_factory():
    """dry-run 기본 심사 — 항상 PASS(LLM 0콜). budget.charge 는 러너가 회차마다 1회 계상(상한 산술 검증용)."""
    def judge(text: str, meas: dict) -> dict:
        return {"verdict": "PASS", "drop_trigger": "없음", "fix_note": "",
                "retention_est": 80, "hate_comment": ""}
    return judge


class _LiveGatedBundle:
    """live 실발사 배선 — LiveHarness(gen) + cross-vendor 심사 provider + fix_spans(v2) 수리.
    LR-1 결재 전 사용 금지: main() 의 --live 게이트 + run() confirm='FIRE' 2차 잠금을 통과해야만 생성/콜."""

    def __init__(self, seed: ProjectSeed, pov: str = ""):
        from novelcopilot.config import get_settings
        self.settings = get_settings()
        # 시드는 무태그로 worldgen에 — 태그는 GatedLiveHarness.create() 가 생성 후 부착(작중 역류 차단).
        self.harness = GatedLiveHarness(seed, pov=pov)
        self.judge_provider, self.judge_spec = make_judge(self.settings, self.settings.llm_provider)

    def judge_fn(self):
        prov = self.judge_provider

        def judge(text: str, meas: dict, ctx: dict | None = None) -> dict:
            ctx = ctx or {}
            return llm_gate(prov, text, meas,
                            story_so_far=ctx.get("story_so_far", ""),
                            genre=ctx.get("genre", ""),
                            planned_event=ctx.get("planned_event", ""))   # EB-3: 설계 사건 대조(advisory)
        return judge

    def fix_fn(self):
        svc = self.harness.svc

        def fix(harness, pid, chapter, text, spans) -> bool:
            """ST-11 v2 국소 수리 — dp4b cmd_fix_spans 와 동형(무강제·폴백=원문). 라이브 본문에 반영.
            스팬별 격리: 앞선 스팬 퇴고가 본문을 바꾸면 뒤 스팬의 원문 매칭이 깨질 수 있다
            (실측 2026-07-10 ch12: 13스팬 순차 중 span_not_found → arm 중단). 스팬 하나의 실패는
            그 스팬만 원문 유지(폴백)하고 전진 — 회차 게이트 전체를 죽이지 않는다(무강제 정합)."""
            import tools.st11_span_rewrite as st11
            state = harness.load(pid)
            sess = svc.sessions.get_or_create(state)
            changed_any = False
            for sp in spans:
                try:
                    r = st11.rewrite_span_via_chassis(
                        sess.bundle.generator, sess.bundle.ontology, sess.bundle.checker,
                        chapter, text, sp, run_threshold=RHYTHM_RUN_THRESHOLD, service=svc, mode="v2")
                    changed_any = changed_any or bool(r.get("changed"))
                except ValueError as e:
                    if "span_not_found" not in str(e):
                        raise
                    print(f"[fix-span-skip] ch{chapter} 스팬 매칭 소실(선행 퇴고로 본문 변경) — 원문 유지", flush=True)
            return changed_any
        return fix


# ─────────────────────────────────────────────────────────────────────────────
# 게이트 셀프테스트(--dry-run 전용) — 게이트 kill/전진/상한이 실제로 발화하는지 결함 주입(LLM 0콜).
# ─────────────────────────────────────────────────────────────────────────────
def _seq_judge_factory(verdicts):
    """캔드 verdict 시퀀스 심사(모의) — 소진 후 마지막 반복. dp4b fix_note 재생성 흐름 검증용."""
    seq = list(verdicts)

    def make():
        state = {"i": 0}

        def judge(text: str, meas: dict) -> dict:
            v = seq[min(state["i"], len(seq) - 1)]
            state["i"] += 1
            return {"verdict": v, "drop_trigger": "없음" if v == "PASS" else "테스트 하차 대목",
                    "fix_note": "" if v == "PASS" else "테스트 수정 지시", "retention_est": 70,
                    "hate_comment": ""}
        return judge
    return make


def guard_selftest(out_dir: Path, run_id: str) -> dict:
    """--dry-run 전용: 게이트 kill/전진/상한이 발화하는지 결함 주입 검증(전부 LLM 0콜·모의 judge)."""
    seed = ProjectSeed(title="셀프테스트", genre="셀프테스트", target_chapters=12)
    checks = []

    def _arm(name, *, verdicts, chapters=6, llm_cap=60, max_retries=2, fail_streak=3, faults=None):
        h = GatedDryRunHarness(_tagged_seed(seed), faults=faults or {})
        b = CallBudget(llm_cap)
        return run_gated_arm(harness=h, arm_no=0, target_chapters=chapters,
                             judge_fn=_seq_judge_factory(verdicts)(),
                             fix_fn=None, budget=b,
                             log_path=out_dir / f"gated_{run_id}_selftest_{name}.jsonl",
                             max_retries=max_retries, fail_abort_streak=fail_streak)

    a = _arm("all_pass", verdicts=["PASS"])
    checks.append({"name": "all_pass_no_abort",
                   "pass": bool((not a["aborted"]) and a["chapters_done"] == 6
                                and a["gate_fail_exhausted"] == 0 and a["llm_calls"] == 6),
                   "arm": a})
    b = _arm("fail_streak_abort", verdicts=["FAIL"])
    checks.append({"name": "consecutive_fail_exhausted_aborts_at_3",
                   "pass": bool(b["aborted"] and b["chapters_done"] == 3
                                and _has_event(b, "gate_fail_streak", "abort")),
                   "arm": b})
    # FAIL→(retry)→PASS 회복: 소진 아님, 스트릭 리셋(전진), 콜=회차1은 3콜(초1+재2), 이후 PASS 1콜
    c = _arm("fail_recover_pass", verdicts=["FAIL", "PASS"], chapters=2)
    checks.append({"name": "fail_then_recover_advances_no_exhaust",
                   "pass": bool((not c["aborted"]) and c["chapters_done"] == 2
                                and c["gate_fail_exhausted"] == 0),
                   "arm": c})
    d = _arm("cap_abort", verdicts=["PASS"], llm_cap=2)
    checks.append({"name": "llm_cap_aborts",
                   "pass": bool(d["aborted"] and _has_event(d, "llm_cap", "abort")),
                   "arm": d})
    return {"pass": all(ch["pass"] for ch in checks), "checks": checks}


def _has_event(arm: dict, guard: str, action: str) -> bool:
    return any(e["guard"] == guard and e["action"] == action for e in arm["guard_events"])


# ─────────────────────────────────────────────────────────────────────────────
# CLI — gen_longrun 발사 방지 게이트 이식(--dry-run / --live --fire 이중 잠금)
# ─────────────────────────────────────────────────────────────────────────────
def _confirm_launch(works: int, chapters: int, seed: ProjectSeed) -> bool:
    """실발사 2중 게이트 ⓑ: TTY 에서 'LAUNCH' 정확 입력. 비TTY(스크립트/CI/파이프)는 무조건 거부."""
    if not _stdin_isatty():
        print("[차단] --live 는 TTY 확인 프롬프트가 필요합니다(비대화형 실발사 금지 — LR-1 결재 필요).")
        return False
    print("=" * 60)
    print("경고: 실발사(LLM 실비용 발생 + 웹앱 라이브 데이터 폴더에 작품 기록)")
    print(f"  작품수={works} × 목표회차={chapters} / 장르='{seed.genre}'")
    print("  DP-5 범위는 dry-run 검증까지 — 실발사는 별도 결재 사항입니다.")
    print("=" * 60)
    try:
        ans = input("실발사를 승인하려면 LAUNCH 를 정확히 입력(그 외 취소): ")
    except (EOFError, KeyboardInterrupt):
        return False
    return ans.strip() == "LAUNCH"


def build_seed(args) -> ProjectSeed:
    """시드 조립 — --seed-idx 는 ab_genres.GENRES 재사용(gen_longrun.build_seed 계보)."""
    if args.seed_idx is not None:
        try:
            from tools.ab_genres import GENRES
        except ImportError:
            from ab_genres import GENRES
        seed = GENRES[args.seed_idx].model_copy(deep=True)
    else:
        seed = ProjectSeed(title=args.title, genre=args.genre, tone=args.tone,
                           premise=args.premise, protagonist_hint=args.protagonist,
                           target_chapters=args.target_chapters or max(12, args.chapters))
    if args.target_chapters:
        seed.target_chapters = args.target_chapters
    if args.tone:
        seed.tone = args.tone
    return seed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="DP-5 게이트 출고 러너 — 매화 gen→계측→정독 게이트→FAIL시 재생성. "
                    "기본값 발사 없음(--dry-run 또는 --live 명시).")
    p.add_argument("--dry-run", action="store_true", help="LLM 0콜 — 게이트 kill/전진/상한 검증(임시폴더·모의 judge)")
    p.add_argument("--live", action="store_true", help="실발사(별도 결재 필요 — TTY 에서 LAUNCH 확인 입력)")
    p.add_argument("--fire", action="store_true",
                   help="--live 와 함께만 유효: TTY 프롬프트 생략 발사(승인된 스크립트/백그라운드 발사용 2중 명시 플래그)")
    p.add_argument("--works", type=int, default=1, help="작품수(arm 수)")
    p.add_argument("--chapters", type=int, default=6, help="arm 당 목표 회차(step 예산)")
    p.add_argument("--llm-cap", type=int, default=DEFAULT_LLM_CALL_CAP, help="run 당 LLM 게이트 콜 상한(비용 가드)")
    p.add_argument("--max-retries", type=int, default=GATE_MAX_RETRIES, help="FAIL 시 fix_note 재생성 상한 R")
    p.add_argument("--fail-abort-streak", type=int, default=FAIL_ABORT_STREAK,
                   help="연속 FAIL-소진 몇 화면 arm 중단(kill criteria)")
    p.add_argument("--genre", default="현대 판타지", help="장르(시드)")
    p.add_argument("--tone", default="", help="톤(시드)")
    p.add_argument("--title", default="", help="제목(비우면 worldgen 이 정함 — [실험 DP-5] 자동 태그)")
    p.add_argument("--premise", default="", help="한 줄 전제")
    p.add_argument("--protagonist", default="", help="주인공 힌트")
    p.add_argument("--target-chapters", type=int, default=None, help="작품 목표 총 회차(seed.target_chapters)")
    p.add_argument("--seed-idx", type=int, default=None, help="tools/ab_genres.GENRES 인덱스로 시드 선택")
    p.add_argument("--pov", default="", choices=["", "first", "third_limited"],
                   help="시드 서술 시점(seed.pov 로 전달 — DP-18 정식 배선). first 면 worldgen 중 화자 보이스 "
                        "도출이 정상 경로로 실행. 빈값=무지정(무변경)")
    p.add_argument("--pid", default="",
                   help=f"기존 {DP5_TAG} 작품에 이어붙기(1화씩 분할 출고) — --live·works=1 전용. 빈값=신작")
    p.add_argument("--out-dir", default=None, help="manifest/로그 출력 폴더(기본 tools/reports)")
    args = p.parse_args(argv)

    if args.dry_run and args.live:
        print("[오류] --dry-run 과 --live 는 동시 지정 불가.")
        return 2
    if args.fire and not args.live:
        print("[오류] --fire 는 --live 와 함께만 사용(실발사 스크립트 게이트 — 단독/dry-run 조합 무효).")
        return 2
    if not args.dry_run and not args.live:
        p.print_usage()
        print("모드 미지정 — 발사 방지 기본값. 검증은 --dry-run, 실발사(결재 후)는 --live.")
        return 2
    if args.pid and (not args.live or args.works != 1):
        print("[오류] --pid reattach 는 --live·--works 1 전용(dry-run 합성 하네스엔 기존 pid 없음).")
        return 2

    seed = build_seed(args)
    out_dir = Path(args.out_dir) if args.out_dir else None

    if args.live:
        # 발사 방지 2중 게이트: 명시 플래그(--live) + (TTY 'LAUNCH' 확인 또는 --fire 명시 플래그).
        # 어느 쪽이든 통과 전 LiveHarness/judge 미생성. run() 자체도 confirm='FIRE' 2차 잠금.
        if args.fire:
            print("=" * 60)
            print("경고: 실발사(--live --fire 2중 명시 플래그 — TTY 프롬프트 생략)")
            print(f"  작품수={args.works} × 목표회차={args.chapters} / 장르='{seed.genre}'")
            print("=" * 60, flush=True)
        elif not _confirm_launch(args.works, args.chapters, seed):
            print("실발사 취소됨(게이트 검증은 --dry-run 사용).")
            return 3
        mode = "live"
    else:
        mode = "dry-run"

    print(f"DP-5 게이트 런 시작 mode={mode} works={args.works} chapters={args.chapters} "
          f"genre='{seed.genre}' llm_cap={args.llm_cap} R={args.max_retries}", flush=True)
    m = run(mode=mode, seed=seed, works=args.works, chapters=args.chapters, out_dir=out_dir,
            llm_cap=args.llm_cap, max_retries=args.max_retries,
            fail_abort_streak=args.fail_abort_streak,
            confirm=(LIVE_CONFIRM if mode == "live" else ""), pov=args.pov, pid=args.pid)
    st = m.get("gate_selftest")
    if st is not None:
        for ch in st["checks"]:
            print(f"  selftest {ch['name']}: {'OK' if ch['pass'] else 'FAIL'}", flush=True)
    print(f"manifest: {m['manifest_path']}")
    print(f"LLM 콜 합계: {m['llm_calls_total']}")
    print(f"결과: {'GREEN' if m['green'] else 'NOT GREEN'} (arms={len(m['arms'])}, {m['elapsed_sec']}s)")
    return 0 if m["green"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows cp949 콘솔 보호
    except Exception:
        pass
    sys.exit(main())
