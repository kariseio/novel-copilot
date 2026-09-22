# -*- coding: utf-8 -*-
"""T6 회차 집필 진입 전 '준비도' advisory — 장르 중립 구조 신호(결정론·LLM 0콜·비차단).

'설정집 0개·6막 중 1막만 분해로 본문 강행'하던 무강제 구조를 *작가에게 가시화*만 한다(SL비교 '4회차=단일던전
패딩'의 소스). 강제·자동보강·하드게이트 없음 — 생성은 언제나 가능(작가 결정). 무강제 원칙 준수.

no-whack-a-mole·genre-blind 회피: 장르 특정 슬롯(마수분류·등급규칙·기관 등)을 하드코딩하지 않는다. 임계는
시스템이 *스스로 선언한 최소치*(worldgen 프롬프트: 추적 축 '2개 이상')와 순수 구조 카운트·계획 분해 비율만 쓴다.
"""
from __future__ import annotations

from .ontology_ops import machine_binding_report


def chapter_readiness(state) -> dict:
    """집필 진입 전 준비도 리포트(결정론·advisory). 반환 {level, signals, flags, tier_report}. flags=비차단 안내(강제 아님).

    level: 'ok'(경고 없음) | 'thin'(경고 1건+). signals: 순수 구조 카운트. flags: soft-threshold advisory.
    tier_report(XR-5): 속성별 자동 확정 티어 선언 현황·기계 binding 잔량(읽기 전용 참고 — level/flags 에
    영향 0. 소급 강등을 하지 않는다는 결정의 정직화 표면이지 게이트가 아니다)."""
    w = state.world
    bible_n = len([e for e in state.bible.entries if getattr(e, "status", "") != "deprecated"])
    attr_n = len(w.attributes)
    entity_n = len(w.entities) + len(getattr(state, "runtime_entities", []) or [])
    rule_n = len(w.world_rules)
    spine = getattr(w, "spine", None)
    arcs = list(spine.arcs) if spine else []
    arcs_n = len(arcs)
    decomposed_n = sum(1 for a in arcs if a.episodes)              # episodes 채워진 아크(=분해된 막)
    ending = getattr(spine, "ending", None) if spine else None
    ending_set = bool(ending and (getattr(ending, "ending", "") or getattr(ending, "central_question", "")))
    runway = sum(max(1, e.target_chapters) for a in arcs for e in a.episodes)   # 분해된 계획 커버 회차(에피는 최소 1화 소비=하한 클램프)
    cur = int(getattr(state, "current_chapter", 0) or 0)

    signals = {
        "bible": bible_n, "attributes": attr_n, "entities": entity_n, "world_rules": rule_n,
        "arcs": arcs_n, "arcs_decomposed": decomposed_n, "ending_set": ending_set,
        "plan_runway": runway, "current_chapter": cur,
    }

    flags: list[dict] = []

    def _warn(key, message, hint):
        flags.append({"key": key, "severity": "warn", "message": message, "hint": hint})

    # 설정집이 비었으면 엔진이 회차에 주입할 '세계 재료'가 없어 본문이 얕아지기 쉽다(가장 직접적인 패딩 소스).
    if bible_n == 0:
        _warn("bible_empty", "설정집이 비어 있습니다(0건).",
              "세계 재료가 얕으면 회차가 사건 없이 늘어지기 쉽습니다. 설정집에 핵심 설정 몇 개를 추가하면 밀도가 올라갑니다.")
    # 추적 축(attributes)은 worldgen 이 '2개 이상'을 자기 최소치로 선언 — 그 밑이면 연속성/변화 추적 재료가 부족.
    if attr_n < 2:
        _warn("attributes_thin", f"추적 속성 축이 {attr_n}개입니다(권장 2+).",
              "변화를 추적할 축(관계·신분·지식·자원·역량 등)이 적으면 상태 전개가 빈약해집니다.")
    # B-26: spine 부재/아크 0(생성 실패 빈 폴백=평면 모드) — 생성 오버레이의 일회성 경고(spine_skip)는 다음
    # 이벤트에 곧 덮여 사라짐 → 작업실 준비도 advisory 로 영속 가시화(무강제·비차단, 기존 T6 메커니즘 재사용).
    if arcs_n == 0:
        _warn("spine_missing", "이야기 구조(결말·단락 계획)가 없습니다.",
              "장기 계획 없이 매 회차를 즉흥으로 쓰게 됩니다(평면 모드). 작품 생성 중 구조 설계가 실패했을 수 있어요.")
    # 다막 spine 인데 1막만 분해됨 → 분해 안 된 막으로 본문이 넘어가면 계획 없는 패딩이 된다('4회차=단일던전'의 구조).
    if arcs_n > 1 and decomposed_n <= 1:
        _warn("arcs_undecomposed", f"{arcs_n}개 아크 중 {decomposed_n}개만 에피소드로 분해됐습니다.",
              "분해되지 않은 아크로 넘어가면 계획 없이 본문을 강행하게 됩니다. 다음 아크를 미리 분해하면 안정적입니다.")
    # 분해된 계획의 회차 런웨이를 이미 소진 → 지금 생성하려는 회차가 계획 밖.
    if runway and cur >= runway:
        _warn("runway_exhausted", f"분해된 계획이 {runway}화까지인데 현재 {cur}화까지 집필됐습니다.",
              "다음 아크/에피소드를 분해하지 않으면 계획 밖 회차를 즉흥으로 강행하게 됩니다.")

    # XR-5: 티어 선언 현황(additive·읽기 전용). level/flags 를 건드리지 않는다 — 경고를 하나 더 만드는 게
    #   아니라 '무엇이 기계 판단으로 [확정 설정]에 박혀 있는지'를 작가가 볼 수 있게 하는 창이다.
    return {"level": "thin" if flags else "ok", "signals": signals, "flags": flags,
            "tier_report": machine_binding_report(state)}


# ── XR-7①: stale 파생물 사전 점검(결정론·LLM 0콜·비차단) ──────────────────────────────────────────
# 이 함수는 '퇴고 accept 로 본문이 바뀌었는데 옛 본문 기준으로 남아 있는 LLM 콜 파생물'(RV-2② 표식)을
# 걷어 작가에게 보여 주기만 한다. 생성을 막지 않는다(무강제 — 측정→가시화→작가).
#
# 파생물별 '다음 화 생성 입력' 유입 경로 정본 목록(2026-08-22 코드 실측 — 설계 docs/design-xr-batch.md §3.1
# 표를 여기에 박제한다. 새 소비 채널이 생기면 이 표를 먼저 갱신하는 것이 계약이다):
#   wiki            : harness `wiki.retrieve(beat.summary, ch-1)` → narrative 슬롯 → [참조 맥락] 블록
#                     → **자동·프롬프트 직접**
#   dialogue_ledger : copilot `_prev_ledgers` → generate(prev_ledgers=) → lookup.CanonLookup(ledgers=)
#                     `_recent_exchange` → 조회 응답 발췌(gen_tools ON 시) → **자동·프롬프트 직접(조건부)**
#                     ※ 이 채널만 XR-7② 결정론 유령-인용 필터가 소스 지점에서 방어한다.
#   promise_ledger  : reconcile 입력 open_promises() · pacing_window · telemetry · ending_contract
#                     → 자동·비프롬프트(회계 연쇄). 재정산은 후행 회차 지불 이력과 얽혀 별건(설계 §3.2).
#   claim_audit     : FE-2 '점검 반영 재생성' 모달 → 작가 선택 → fix_instruction 1회성 주입
#                     → 작가 발동·조건부 프롬프트
#   reader_feedback : reader_trend → 대시보드/UI 만. 생성 입력 유입 0(G2 조향 금지 계약)
#
# 정직 기록: 요약·detail_synopsis·RAG·ai_tell 은 accept 가 동형 갱신하므로 애초에 stale 대상이 아니다
# (RV-2② 계약) — 여기 목록에도 오르지 않는다.
def stale_derivatives(state) -> list[dict]:
    """확정 회차들의 '퇴고 반영 안 된 파생물' 목록 — [{chapter, names:[...]}](회차 오름차순).

    결정론·LLM 0콜·읽기 전용. 표식이 없는 회차는 목록에 담기지 않는다(빈 리스트=해소 상태).
    건수는 advisory 원자료다 — 품질 판정·보고 헤드라인으로 쓰지 않는다(K3)."""
    from ..domain.types import ChapterStatus
    out: list[dict] = []
    for c in sorted(getattr(state, "chapters", []) or [], key=lambda c: c.chapter):
        if getattr(c, "status", None) != ChapterStatus.FINALIZED:
            continue
        marks = getattr(c, "derivatives_revised_stale", None) or {}
        names = sorted(k for k, v in marks.items() if v)
        if names:
            out.append({"chapter": c.chapter, "names": names})
    return out


def eligible_wiki_source_chapters(state) -> dict:
    """XR-27(cross-review/015 §4): 위키 Projection 의 '정식 원천 자격' 회차 — **단일 정의**.

    재구축 입력(rebuild_wiki)과 지문 소비 검증(아래 mismatches)이 이 하나의 술어를 공유한다 — 두 곳이
    각자 정의를 들고 어긋나는 재발 클래스(입력은 FINALIZED 한정인데 검증은 전 회차 인정 — 015 실측)의
    소스 차단. 자격 = FINALIZED + 본문 실재(재구축이 실제로 replay 하는 집합과 문자 그대로 동일).
    반환 {str(회차): 본문 지문}."""
    from ..domain.types import ChapterStatus, text_fingerprint
    return {str(c.chapter): text_fingerprint(c.text)
            for c in (getattr(state, "chapters", []) or [])
            if getattr(c, "status", None) == ChapterStatus.FINALIZED and (c.text or "").strip()}


def wiki_fingerprint_mismatches(state, pages) -> list[dict]:
    """XR-18(cross-review/009 §8): 위키 페이지에 기록된 반영-회차 본문 지문과 **현재** 본문 지문의 결정론
    대조 — stale 표식을 우회한 본문 변경(표식 누락 신규 경로·외부/수동 수정)을 소비 지점에서 잡는 이중 방어.

    pages: WikiPage 목록(라이브 세션 위키 pages.values() 또는 영속 state.wiki_pages — 호출부 선택).
    반환 [{page_id, chapter, reason}] — reason 3단(전부 격리 사유):
      · source_missing        지문이 가리키는 회차가 현재 state 에 없음(고아 소스 — 012 §5)
      · source_not_finalized  회차는 있으나 정식 원천 자격(FINALIZED+본문)이 없음 — ESCALATED·자격 박탈
                              회차를 본문 일치만으로 정상 인정하던 창 봉합(XR-27·015 §4). 자격 술어는
                              재구축 입력과 동일(eligible_wiki_source_chapters — 단일 정의)
      · fingerprint_mismatch  자격 회차인데 본문이 기록 지문과 다름(표식 우회 변경)
    하나라도 있으면 소비자(생성 게이트)가 위키 전체를 보수적으로 격리한다(누적 합성물 부분 신뢰 불가 —
    009 판단). 하위호환은 **지문 자체가 빈 페이지**에만 적용(구 데이터·미재구축=빈 목록·바이트 동일) —
    지문이 명시된 뒤에는 원본 회차의 존재·자격·일치가 전부 불변식이다. LLM 0콜·읽기 전용."""
    eligible = eligible_wiki_source_chapters(state)
    present = {str(getattr(c, "chapter", "")) for c in (getattr(state, "chapters", []) or [])}
    out: list[dict] = []
    for p in (pages or []):
        for ch, fp in sorted((getattr(p, "source_fingerprints", None) or {}).items()):
            if ch not in present:
                reason = "source_missing"
            elif ch not in eligible:
                reason = "source_not_finalized"
            elif eligible[ch] != fp:
                reason = "fingerprint_mismatch"
            else:
                continue
            out.append({"page_id": p.page_id, "chapter": int(ch), "reason": reason})
    return sorted(out, key=lambda r: (r["chapter"], r["page_id"]))
