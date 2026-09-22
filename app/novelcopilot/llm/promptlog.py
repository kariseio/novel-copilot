# -*- coding: utf-8 -*-
"""PL-1 — LLM 콜 전문 로깅 프록시(사용자 지시 2026-08-17 "전체 프롬프트 구성한 거는 로그에 왜 안 남겨?").

왜: 조립된 프롬프트 전문이 어디에도 영속되지 않아(gen_context 는 슬롯별 요약·절단 스냅샷),
"조립 결과가 말이 되는가"를 검사할 원자료가 없었다 — 9화 전문에서 2화용 지시 영속 주입·미등재
응답 노출·문서 중복이 사용자 눈으로 처음 발견된 실측이 근거다. 프로바이더 팩토리 단일 지점에서
모든 chat/chat_json/chat_tools 의 요청 전문(+응답)을 파일로 남긴다.

계약:
· 프롬프트 바이트 불변 — 관측만 하고 위임한다(콜 내용·순서·인자 무변경).
· NEVER throws — 로깅 실패는 콜을 막지 않는다(전량 흡수).
· settings.prompt_log=False 면 팩토리가 래핑 자체를 생략(프록시 미생성 — 종전과 객체 동일).
· 회전: 태그 폴더당 파일 상한 초과 시 오래된 것부터 삭제(무한 증식 방지).

XR-3(2026-08-22) consumer 태그 — additive 확장:
  로그 파일이 kind/model 만 갖고 있어 "이 콜이 무슨 소비자(스테이지)인가"를 사후에 알 수 없었다
  (005 §3 "데이터의 전 소비 지점을 추적하지 않았다"). contextvar 로 스테이지 라벨을 실어
  헤더 `consumer=<label>` + 파일명 접미로 남긴다. 미태그 콜은 `unclassified`(결측 정직 — 침묵 금지).
  라벨 어휘의 SSOT 는 `docs/llm-call-inventory.md`(문서-코드 계수는 test_xr3_inventory_sync 가 계약화).
  위임 코드는 한 줄도 바뀌지 않는다(프롬프트 바이트 불변·NEVER throws 유지·LLM 콜 증가 0).
"""
from __future__ import annotations

import collections as _collections
import contextvars
import datetime as _dt
import functools
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path

_LOG_ROOT = Path(__file__).resolve().parents[2] / "logs" / "prompts"
_MAX_FILES_PER_TAG = 600     # 초과 시 오래된 100개 삭제(대략 30~40화 분량 여유)
_TRIM_BATCH = 100

UNCLASSIFIED = "unclassified"
# 스레드별 컨텍스트 — 백그라운드 생성 스레드는 기본값 ""(=unclassified)로 시작한다(정직).
_CONSUMER: contextvars.ContextVar[str] = contextvars.ContextVar("prompt_consumer", default="")
_LABEL_SAFE = re.compile(r"[^0-9A-Za-z_.:-]+")


def _norm(label: str) -> str:
    """라벨 정규화 — 공백·경로문자 제거(로그 파일명 안전). 빈 값은 미태그로 취급."""
    return _LABEL_SAFE.sub("_", (label or "").strip())[:48]


def _fs_safe(label: str) -> str:
    """파일명용 — 콜론(story_pass:base)은 Windows 경로 금지문자라 하이픈으로 낮춘다(헤더는 원형 유지)."""
    return label.replace(":", "-")


@contextmanager
def consumer(label: str):
    """스테이지 경계 1랩(콜당 아님). 중첩되면 안쪽이 이긴다 — 세부 스테이지가 상위를 덮어쓴다."""
    tok = _CONSUMER.set(_norm(label))
    try:
        yield
    finally:
        _CONSUMER.reset(tok)


def stage(label: str):
    """함수 1개 = 스테이지 1개인 지점의 데코레이터 형태(호출부 본문 무변경 — 재들여쓰기 0)."""
    def _deco(fn):
        @functools.wraps(fn)
        def _wrapped(*a, **kw):
            with consumer(label):
                return fn(*a, **kw)
        return _wrapped
    return _deco


def current() -> str:
    """현재 스테이지 라벨(미태그면 unclassified — 결측 정직)."""
    return _CONSUMER.get("") or UNCLASSIFIED


# ── XR-14(cross-review/023 §2·007 §8): 세부 스테이지(60종) → 상위 Consumer 분류 ─────────────────
# 세부 stage 는 위치 관측·비용 추적용으로 유지하고, 정보 노출 정책은 안정적인 상위 Consumer 에 결속한다
# (함수명이 바뀌어도 정책이 흔들리지 않게). 매핑의 SSOT 는 이 dict — 인벤토리 문서의 분류 표와
# 코드 스테이지 전수는 test_xr14 가 양방향 대조한다(새 스테이지가 미분류로 남으면 RED — 누락·오분류 검사).
# 분류 기준은 네임스페이스가 아니라 **의미**(무슨 목적의 소비자인가): 예) worldgen:spine/episodes 는
# worldgen 콜이지만 장기 계획이다. Consumer 별 허용·금지 정보 정책은 docs/consumer-policy.md(문서 결속·
# 집행은 기존 개별 불변식이 담당 — 새 자동 게이트 신설 없음·무강제).
CONSUMER_CLASSES = ("world_authoring", "long_range_planning", "chapter_planning",
                    "prose_generation", "revision", "extraction", "verification",
                    "evaluation", "summarization")
STAGE_CONSUMER_MAP: dict[str, str] = {
    # world_authoring — 작품 원천 저작(세계·인물·설정·표지)
    "worldgen:world": "world_authoring", "worldgen:weird": "world_authoring",
    "worldgen:obsession": "world_authoring", "worldgen:bible": "world_authoring",
    "worldgen:bible_category": "world_authoring", "worldgen:genre_contract": "world_authoring",
    "worldgen:narrator_voice": "world_authoring", "worldgen:voice_card": "world_authoring",
    "worldgen:time_anchors": "world_authoring", "worldgen:chat": "world_authoring",
    "worldgen:concept_chat": "world_authoring", "cover_prompt": "world_authoring",
    # long_range_planning — 작품 구조·아크 층(결말 열람이 허용되는 유일 계획 층 — 001 §6)
    "worldgen:spine": "long_range_planning", "worldgen:episodes": "long_range_planning",
    "retrospective": "long_range_planning",
    # chapter_planning — 회차 단위 설계(결말 원문 비노출 층)
    "beat_plan": "chapter_planning", "beat_plan_flat": "chapter_planning",
    "event_menu": "chapter_planning", "scene_plan": "chapter_planning",
    "slot_budget": "chapter_planning",   # RC-1: 에피소드 사건 풀 → 회차 슬롯 배분(결말 비노출 계획 층)
    "story_pass:base": "chapter_planning", "story_pass:review": "chapter_planning",
    "story_pass:pair": "chapter_planning", "story_pass:scan": "chapter_planning",
    "story_pass:merge": "chapter_planning", "story_pass:check": "chapter_planning",
    "story_pass:joint": "chapter_planning", "story_pass:revise": "chapter_planning",
    "story_pass:audit": "chapter_planning", "story_pass:fix_fmt": "chapter_planning",
    # prose_generation — 본문 산출과 그 최종화 기계 단계(집필기와 동일한 정보 제약)
    "draft": "prose_generation", "continue": "prose_generation",
    "lookup_prepass": "prose_generation", "regen_tail": "prose_generation",
    "continuity_polish": "prose_generation", "fix_tense": "prose_generation",
    "fix_tics": "prose_generation", "reformat": "prose_generation",
    # revision — 작가 발동 사후 편집 패스(사실 불변 계약)
    "revise": "revision", "redpen_suggest": "revision", "rewrite": "revision",
    "rerender": "revision", "rerender_gate": "revision",
    # extraction — 원고에서 구조화 데이터 추출(비구속 착지 원칙)
    "ontology_propose": "extraction", "dialogue_ledger": "extraction",
    "ledger_reconcile": "extraction", "ledger_payoff": "extraction",
    "story_labels": "extraction",
    # verification — 사실·정합 검증(생성 조향 금지)
    "check_extract": "verification", "claim_audit": "verification",
    "ending_contract": "verification", "narration_detect": "verification",
    # evaluation — 품질·독자 반응 평가(생성 입력 역류 금지)
    "reader_desk": "evaluation", "cold_read": "evaluation",
    "style_judge": "evaluation", "gate_judge": "evaluation",
    # XR-36(029 §2.1 재분류): judge provider·advisory 산출(needs_repair·인용) — 본문 변환 없음(실측).
    "style_rhythm": "evaluation",
    # summarization — 파생 요약·노트 합성(원천 변경 불가)
    "summarize": "summarization", "episode_rollup": "summarization",
    "wiki_ingest": "summarization",
}


def consumer_class(stage_label: str | None = None) -> str:
    """스테이지의 상위 Consumer(미지정 시 현재 컨텍스트 라벨). 매핑 밖은 unclassified(결측 정직)."""
    label = stage_label if stage_label is not None else current()
    return STAGE_CONSUMER_MAP.get(label, UNCLASSIFIED)


# ── XR-36(cross-review/029 §2): 2축 StagePolicy — 단일 enum 이 "행위 목적"과 "열람 범위"를 섞어
# 표현 불가였던 조합(예: 결말을 읽는 검증기)을 분리한다. 정보 노출 **정책의 결속 키는 이 2축**이고,
# 위 STAGE_CONSUMER_MAP 은 관측 그룹(제품 파이프라인 소속 — 로그·비용 귀속)으로 유지한다.
# 두 축이 갈리는 실측 예: redpen_suggest/rerender_gate 는 퇴고 파이프라인 소속(그룹=revision)이지만
# 행위는 advisory 평가(operation=evaluation) — 한 라벨로는 이 둘을 동시에 말할 수 없다(029 §2.1).
# scope 는 "이 스테이지가 열람해도 되는 최대 민감 지평"(구현 실측 기준 — 열망 아님):
#   world_source=시드·작가 지시·확정 설정(원천 저작 재료) · ending=결말·중심질문·주제 원문 ·
#   story_plan=아크 목표·이벤트 메뉴·확정 스토리·요약(결말 원문 비노출) · current_prose=현재 회차
#   프로즈+계약·공개 캐논·최근 문맥 · past_prose=확정 회차 원고(추출·요약·검증 대상).
# scope 우선순위(XR-37 — 031 §2.3: 복수 클래스를 읽으면 최상위로 배정): ending > world_source >
#   story_plan > current_prose > past_prose. 비교 불가 조합이 실측되면 단일 값 대신 capability 집합으로
#   승격한다(현 60종은 전부 이 격자로 표현됨 — 의미 census 표 = 인벤토리 §9).
# operation 판정 기준(XR-37 — 파이프라인 소속이 아니라 **실제 산출 행위**·소속은 관측 그룹이 말한다):
#   새 산출물 창작=authoring(세계 원천)/planning(설계층)/generation(프로즈 텍스트층) · 기존 산출물의
#   목적 있는 개정=revision(층 불문 — 대상 층은 scope 가 말한다·사실 불변 계약) · 판단·조언=evaluation ·
#   기준 대조 판정=verification · 구조화 도출=extraction · 압축 파생=summarization.
# ending scope 는 프롬프트 조립 실측으로 배정했다(worldgen:spine/episodes=결말 생성·주입,
# retrospective=[현재 엔딩] 주입, ending_contract=결말 3필드 주입 — 029 §2.1 반례의 계약화).
OPERATIONS = ("authoring", "planning", "generation", "revision",
              "extraction", "verification", "evaluation", "summarization")
SCOPES = ("world_source", "ending", "story_plan", "current_prose", "past_prose")
StagePolicy = _collections.namedtuple("StagePolicy", ("operation", "scope"))
_POLICY_UNCLASSIFIED = StagePolicy(UNCLASSIFIED, UNCLASSIFIED)

_P = StagePolicy
STAGE_POLICIES: dict[str, StagePolicy] = {
    # 세계 원천 저작(12)
    **{s: _P("authoring", "world_source") for s in (
        "worldgen:world", "worldgen:weird", "worldgen:obsession", "worldgen:bible",
        "worldgen:bible_category", "worldgen:genre_contract", "worldgen:narrator_voice",
        "worldgen:voice_card", "worldgen:time_anchors", "worldgen:chat",
        "worldgen:concept_chat", "cover_prompt")},
    # 장기 계획 — 결말 열람 층(3)
    "worldgen:spine": _P("planning", "ending"), "worldgen:episodes": _P("planning", "ending"),
    "retrospective": _P("planning", "ending"),
    # 회차 설계(결말 원문 비노출 지평) — 스토리 패스는 operation 이 갈린다(생성·개정·평가·심문 혼합 실측.
    # XR-37: 산출물이 계획이라는 사실은 scope 가 말하고, operation 은 행위로 판정 — 031 §2.2)
    **{s: _P("planning", "story_plan") for s in (
        "beat_plan", "beat_plan_flat", "event_menu", "scene_plan", "slot_budget",
        "story_pass:base", "story_pass:scan")},
    "story_pass:revise": _P("revision", "story_plan"),     # 리뷰 반영 후보 개정(XR-37 재판정)
    "story_pass:fix_fmt": _P("revision", "story_plan"),    # 형식 위반 후보 수리(XR-37 재판정)
    "story_pass:merge": _P("revision", "story_plan"),      # 기둥 스토리 병합·개정(XR-37 재판정)
    "story_pass:joint": _P("generation", "story_plan"),    # 접합 줄 신규 작성(함수 자체가 gen 콜 — XR-37)
    "story_pass:review": _P("evaluation", "story_plan"),   # 블라인드 교차 리뷰
    "story_pass:pair": _P("evaluation", "story_plan"),     # 쌍대 재미(참고 전용)
    "story_pass:audit": _P("verification", "story_plan"),  # 심문표(기준 대조)
    "story_pass:check": _P("verification", "story_plan"),  # 훅 소화 판정(judge — gen 과 콜 분리)
    # 집필 준비 조회 설계 — 회차 계획을 읽고 캐논 조회만(산문 산출 0 — XR-37 재판정)
    "lookup_prepass": _P("planning", "story_plan"),
    # 본문 산출(2)
    "draft": _P("generation", "current_prose"), "continue": _P("generation", "current_prose"),
    # 프로즈 개정 — 기계 최종화 5종(패치·국소 재작성 = 기존 드래프트 개정·사실 불변. 파이프라인 소속
    # prose_generation 은 관측 그룹이 유지 — 그룹≠operation 분리, XR-37 재판정) + 작가 발동 3종
    **{s: _P("revision", "current_prose") for s in (
        "regen_tail", "continuity_polish", "fix_tense", "fix_tics", "reformat",
        "revise", "rewrite", "rerender")},
    # advisory 평가(7) — redpen_suggest/rerender_gate 는 그룹=revision·행위=evaluation(2축 분리 실증)
    **{s: _P("evaluation", "current_prose") for s in (
        "style_rhythm", "style_judge", "reader_desk", "cold_read", "gate_judge",
        "redpen_suggest", "rerender_gate")},
    # 구조화 추출(5) — story_labels 는 입력이 원고가 아니라 **다음 화 작가 확정 스토리 계획**
    # (build_label_user 실측 — 031 §2.1 반례 교정: past_prose 는 미래 계획 금지 클래스)
    **{s: _P("extraction", "past_prose") for s in (
        "ontology_propose", "dialogue_ledger", "ledger_reconcile", "ledger_payoff")},
    "story_labels": _P("extraction", "story_plan"),
    # 검증 — scope 가 갈린다(현재 화 원고 / 결말). check_extract 는 주 호출이 생성 재작성 루프·확정 전
    # 최종 검사(harness — **현재 드래프트/후보** 입력)라 past_prose 오분류였다(033 §3 실측 교정).
    "narration_detect": _P("verification", "current_prose"),
    "claim_audit": _P("verification", "current_prose"),
    "check_extract": _P("verification", "current_prose"),
    "ending_contract": _P("verification", "ending"),       # 결말 검증기 — 결말 열람은 정당(029 §2.1)
    # 파생 요약(3)
    "summarize": _P("summarization", "past_prose"), "episode_rollup": _P("summarization", "past_prose"),
    "wiki_ingest": _P("summarization", "past_prose"),
}
del _P


def stage_policy(stage_label: str | None = None) -> StagePolicy:
    """스테이지의 2축 정책(미지정 시 현재 컨텍스트 라벨). 매핑 밖은 (unclassified, unclassified)."""
    label = stage_label if stage_label is not None else current()
    return STAGE_POLICIES.get(label, _POLICY_UNCLASSIFIED)


class PromptLoggingProvider:
    """LLMProvider 프록시 — chat 계열 요청·응답 전문을 파일로 영속하고 그대로 위임한다."""

    def __init__(self, inner, tag: str = "misc"):
        self._inner = inner
        self._tag = tag
        self._seq = 0
        self._seq_lock = threading.Lock()

    # 세션이 작품 id 로 태그를 지정한다(EngineSession 생성 지점 1줄).
    def set_log_tag(self, tag: str) -> None:
        self._tag = (tag or "misc").strip() or "misc"

    # ---- 관측 대상 3종 ----
    def chat(self, messages, **kw):
        out = self._inner.chat(messages, **kw)
        self._write("chat", messages, out, kw)
        return out

    def chat_json(self, messages, **kw):
        out = self._inner.chat_json(messages, **kw)
        self._write("chat_json", messages, out, kw)
        return out

    def chat_tools(self, messages, **kw):
        out = self._inner.chat_tools(messages, **kw)
        self._write("chat_tools", messages, out, kw)
        return out

    # 그 외(embed 등)는 전부 원본 위임 — 관측 축 아님.
    def __getattr__(self, name):
        return getattr(self._inner, name)

    # ---- 기록 ----
    def _write(self, kind: str, messages, response, kw) -> None:
        try:
            with self._seq_lock:
                self._seq += 1
                seq = self._seq
            d = _LOG_ROOT / self._tag
            d.mkdir(parents=True, exist_ok=True)
            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")   # 마이크로초 — 다중 인스턴스 동초 파일명 충돌 방지(PM PL-3)
            model = getattr(self._inner, "gen_model", "") or type(self._inner).__name__
            label = current()   # XR-3: 스테이지 태그(미태그=unclassified — 침묵 대신 결측 정직)
            # XR-14: 상위 Consumer(관측 그룹) + XR-36: 2축 정책(op·scope) 병기 — additive 헤더.
            pol = stage_policy(label)
            lines = [f"# kind={kind} · model={model} · tag={self._tag} · consumer={label} "
                     f"· consumer_class={consumer_class(label)} · op={pol.operation} "
                     f"· scope={pol.scope} · time={ts} · seq={seq}"]
            for m in (messages or []):
                role = m.get("role", "?") if isinstance(m, dict) else "?"
                content = m.get("content", "") if isinstance(m, dict) else str(m)
                lines.append(f"\n===== {role} =====\n{content}")
            if isinstance(response, (dict, list)):
                resp = json.dumps(response, ensure_ascii=False)
            else:
                resp = str(response)
            lines.append(f"\n===== response ({len(resp)}자) =====\n{resp}")
            (d / f"{ts}_{seq:04d}_{kind}_{_fs_safe(label)}.txt").write_text("\n".join(lines), encoding="utf-8")
            self._rotate(d)
        except Exception:
            pass   # 로깅 실패는 생성을 막지 않는다

    def _rotate(self, d: Path) -> None:
        try:
            files = sorted(d.glob("*.txt"))
            if len(files) > _MAX_FILES_PER_TAG:
                for f in files[:_TRIM_BATCH]:
                    f.unlink(missing_ok=True)
        except Exception:
            pass
