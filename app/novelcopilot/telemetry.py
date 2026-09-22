# -*- coding: utf-8 -*-
"""FI-1: 작가 의도 이벤트 관측 헬퍼(actor 관통 + emit 단일 지점 + 변경 코어 diff).

설계 docs/design-fi1-author-intent.md. 세 가지 관측 원칙만 담는다:
  · 관측 부가(무강제·비차단): 기록 실패가 작가 액션을 절대 막지 않는다(전부 try/except 무해).
  · raw 보존, 해석 0: 유형·분류·파생 필드 비저장 — 수집은 원문, 해석은 열람자(FI-2).
  · 생성 경로 미주입(read path 0): 이 헬퍼가 만든 이벤트를 어떤 프롬프트·후보 선택·게이트도 읽지 않는다.

이 모듈은 repository/settings 를 인자로만 받아(침습 0) 순환 임포트가 없다 — copilot 서비스가 이 모듈을
임포트하고, 이 모듈은 novelcopilot 내부를 임포트하지 않는다(stdlib 만).
"""
from __future__ import annotations
import time
from contextvars import ContextVar

# actor 관통(설계 §3): 웹 요청 스코프에서 미들웨어가 "author" 로 set, in-process 직접 호출(실험 tools 다수)은
#   기본값 "tool". 서비스 메서드 시그니처를 안 건드리는(침습 0) 관통 채널.
actor_var: ContextVar[str] = ContextVar("actor", default="tool")

# 트림 상한 — 사유·지시 원문은 원장을 스팸에서 지키려 절단(은폐 금지 표식은 각 호출부/코어에서). directive/reason 은
#   짧은 작가 지시라 2,000자면 충분(생성 fix_instruction 절단 관례와 동일 규모). 변경 코어는 §2.1 의 8KB(필드당).
_TXT_CAP = 2000
_CORE_CAP = 8192
_CTX = 200


def trim_text(s: str) -> str:
    """작가 지시·사유 문자열 트림(스팸 방어). 문자(코드포인트) 단위 — 한글 경계 안전(바이트 슬라이스 아님)."""
    s = s or ""
    return s if len(s) <= _TXT_CAP else s[:_TXT_CAP]


def revise_core_diff(before: str, after: str) -> dict:
    """revise_propose 산출 보존(§2.1) — 공통 접두/접미를 절단한 '변경 코어'만 남기는 결정론 순수 함수.

    기각 후보의 after 전문(회차 본문 전체, 한글 4~7천자=8~20KB)을 통저장하면 미변경 95%가 중복 팽창한다.
    대신 바뀐 대목(코어)과 그 앞뒤 맥락 200자만 남겨 '작가가 뭘 요구했고 기계가 뭘 내놨는데 왜 버려졌나'
    열람에 필요한 최소만 보존한다. 문자(코드포인트) 단위 연산이라 한글 경계 안전(바이트 슬라이스 아님).
    코어가 필드당 _CORE_CAP 초과 시 절단하고 trimmed 표식(은폐 금지)."""
    before, after = before or "", after or ""
    bl, al = len(before), len(after)
    m = min(bl, al)
    # 공통 접두 길이
    i = 0
    while i < m and before[i] == after[i]:
        i += 1
    # 공통 접미 길이(접두와 겹치지 않게 남은 길이 안에서만)
    j = 0
    while j < (m - i) and before[bl - 1 - j] == after[al - 1 - j]:
        j += 1
    core_before = before[i: bl - j]
    core_after = after[i: al - j]
    ctx_before = before[max(0, i - _CTX): i]                  # 코어 앞 맥락(before/after 공통 접두라 어느 쪽이든 동일)
    ctx_after = before[bl - j: min(bl, bl - j + _CTX)]        # 코어 뒤 맥락(공통 접미)
    trimmed = False
    if len(core_before) > _CORE_CAP:
        core_before, trimmed = core_before[:_CORE_CAP], True
    if len(core_after) > _CORE_CAP:
        core_after, trimmed = core_after[:_CORE_CAP], True
    out = {"before_len": bl, "after_len": al, "core_offset": i,
           "core_before": core_before, "core_after": core_after,
           "ctx_before": ctx_before, "ctx_after": ctx_after}
    if trimmed:
        out["trimmed"] = True
    return out


def emit_intent(repo, settings, pid: str, chapter: int, surface: str, *,
                payload: dict | None = None, ref: dict | None = None,
                gen_no: int | None = None) -> None:
    """작가 의도 이벤트 1건을 기존 트레이스 사이드카(kind='author_intent')에 append — 무강제·비차단(설계 §1·§2).

    봉투 조립 + 플래그 검사 + save_trace 위임(트레이스 쓰기 락은 save_trace 안·이 헬퍼는 sess.lock 절대 미획득).
    chapter=0 은 작품 스코프 샤드(설정집·엔티티·관계·연재 지시·스파인 — 회차 밖 이벤트). 회차 스코프 이벤트는
    chapter=회차번호·gen_no 스냅샷 동봉. approx_chapter 근사 조인 값은 호출부가 payload 에 넣는다(§1).

    OFF(author_intent_trace=False) 면 emit 0(신규 파일 0). 어떤 예외도 삼킨다 — 기록 실패가 작가 액션을
    막지 않는다(GA-1 계약·kill criteria ⓑ)."""
    try:
        if not getattr(settings, "author_intent_trace", True):
            return
        run = {
            "kind": "author_intent",
            "v": 1,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),   # TZ-aware(신규 이벤트만 위생 — 구 naive 포맷과 의도된 차이)
            "actor": actor_var.get(),
            "surface": surface,
            "chapter": chapter,
        }
        if gen_no is not None:
            run["gen_no"] = gen_no
        if payload is not None:
            run["payload"] = payload
        if ref is not None:
            run["ref"] = ref
        repo.save_trace(pid, chapter, run, kind="author_intent",
                        max_runs=int(getattr(settings, "intent_trace_max_runs", 200)))
    except Exception:
        pass   # 관측 부가 — 기록 실패·미지원 매체가 발행/작가 액션을 절대 막지 않는다(GA-1 base.py 계약 동형)
