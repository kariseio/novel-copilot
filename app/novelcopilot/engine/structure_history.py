# -*- coding: utf-8 -*-
"""B-32: 구조 사용 이력 결정론 파생 — 계획 레이어의 '기억 상실' 소스 차단. LLM 0콜·신규 상태 0.

진단: beat_for_episode·generate_event_menu 는 `recent` **요약**만 보고, 자신이 과거 회차에서
고른 구조(훅 유형·회차 기능·장소·key_events 원문)를 못 봐서 '구조 단위' 재탕(같은 난입 장치·
같은 대치 구도·같은 각성 원패턴)이 반복된다. 기존 prose_rehash(16-gram)는 문장 복제만 잡고
구조 재탕엔 무감(no-whack-a-mole: 검출기 보강이 아니라 계획 레이어에 이력을 보여 소스 차단).

이 모듈은 영속된 회차 기록(ChapterRecord 의 G4 자기 라벨) 또는 Beat 에서 그 이력을 결정론으로
조립해 advisory 참고 블록으로 렌더한다. 판정기 아님(임계·라벨·자동재작성 0) — pacing 과 동일 철학의
'측정·가시화' 재료를, 이번엔 '작가'가 아니라 '다음 회차 설계 콜'에게 참고로 보인다(무강제).

원칙: pacing.label_max_run 재사용(교착어 substring 함정 없는 전체-라벨 동일성 run). 결측 라벨 회차는
skip(구 레코드 하위호환). 입력은 Beat(status 없음) 와 ChapterRecord(status=FINALIZED) 둘 다 수용 —
ESCALATED 기록만 제외(비영속·전이).
"""
from __future__ import annotations
from collections import Counter

from ..domain.types import ChapterStatus
from .pacing import label_max_run


def _s(v) -> str:
    return (v or "").strip() if isinstance(v, str) else (str(v).strip() if v is not None else "")


def _key_events(c) -> list[str]:
    """key_events 파생(B-32 미세수정): 영속 회차(ChapterRecord)는 *직접 필드가 아니라* 영속된 record.gen_context
    안의 계획 비트(gen_context['draft']['beat'] · 'plan' · 'beat')의 key_events 에서 파생한다.
    신규 상태 0 유지 — Beat 영속·ChapterRecord 필드 추가 없이 이미 영속된 디버그 컨텍스트만 읽는다.
    gen_context 부재/구 레코드/미주입이면 결측 → [](skip 하위호환). gen_context 없는 Beat 객체는
    자기 key_events 필드에서 파생(planner in-memory 경로 보존)."""
    gc = getattr(c, "gen_context", None)
    if isinstance(gc, dict):
        for path in (("draft", "beat"), ("plan",), ("beat",)):   # 영속된 계획 비트 후보 위치
            node = gc
            for k in path:
                node = node.get(k) if isinstance(node, dict) else None
            if isinstance(node, dict):
                ev = [_s(e) for e in (node.get("key_events") or []) if _s(e)]
                if ev:
                    return ev
    return [_s(e) for e in (getattr(c, "key_events", None) or []) if _s(e)]


def structure_history(records, n: int = 8) -> dict:
    """최근 n화의 구조 라벨 이력 + 집계(최빈 훅 비율·같은 훅/장소 연속 최대 run)를 결정론 조립한다.

    records: 영속된 ChapterRecord(권장) 또는 Beat 의 리스트(정렬·상태 무관하게 받아 내부에서 처리).
      - status 속성이 있고 FINALIZED 가 아니면 제외(ESCALATED 등 전이 기록 배제). status 없는 객체(Beat)는 포함.
      - chapter 오름차순 정렬 후 최근 n개만.
      - 구조 라벨(hook_type/chapter_function/place)·key_events 가 전부 비면 그 회차는 items 에서 skip(구 레코드).
    반환(전부 원시/집계 — 판정 없음):
      {"n": 포함 회차 수, "items": [{chapter,hook_type,chapter_function,place,scene_form,key_events}...],
       "hook_monotony": 최빈 훅 비율(0~1), "hook_max_run": 같은 훅 연속 최대, "place_max_run": 같은 장소 연속 최대,
       "scene_form_monotony": 최빈 안무 비율(0~1), "scene_form_max_run": 같은 안무 연속 최대}
    SX-1: scene_form(장면 안무)을 items·집계에 병기한다(advisory — 작가·검증 가시화). 단 이 값은 structure_history_block
      렌더에는 노출하지 않는다(순환 다양성은 whitelist-최근2 제외 경로가 담당 — 이력 텍스트를 설계 프롬프트에 보이면 앵커링, B-32e).
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 8
    if n <= 0:
        n = 8
    pool = []
    for c in (records or []):
        st = getattr(c, "status", None)
        if st is not None and st != ChapterStatus.FINALIZED:
            continue                                   # ESCALATED 등 전이 기록 제외(FINALIZED·Beat 만)
        pool.append(c)
    pool.sort(key=lambda c: getattr(c, "chapter", 0))
    recent = pool[-n:]
    items: list[dict] = []
    for c in recent:
        hook = _s(getattr(c, "hook_type", ""))
        func = _s(getattr(c, "chapter_function", ""))
        place = _s(getattr(c, "place", ""))
        sform = _s(getattr(c, "scene_form", ""))        # SX-1: 장면 안무 자기 라벨(영속 ChapterRecord 직접 필드)
        kev = _key_events(c)                           # B-32: 영속 레코드는 gen_context 계획 비트에서 파생(신규 상태 0)
        if not (hook or func or place or sform or kev):
            continue                                   # 구조 라벨 전무(구 레코드) → skip(하위호환)
        items.append({"chapter": getattr(c, "chapter", 0), "hook_type": hook,
                      "chapter_function": func, "place": place, "scene_form": sform, "key_events": kev})
    hooks = [it["hook_type"] for it in items if it["hook_type"]]
    hook_monotony = round(Counter(hooks).most_common(1)[0][1] / len(hooks), 2) if hooks else 0.0
    forms = [it["scene_form"] for it in items if it["scene_form"]]   # SX-1: 안무 최빈 비율(원자료·판정 없음)
    scene_form_monotony = round(Counter(forms).most_common(1)[0][1] / len(forms), 2) if forms else 0.0
    return {"n": len(items), "items": items, "hook_monotony": hook_monotony,
            "hook_max_run": label_max_run([it["hook_type"] for it in items]),
            "place_max_run": label_max_run([it["place"] for it in items]),
            "scene_form_monotony": scene_form_monotony,
            "scene_form_max_run": label_max_run([it["scene_form"] for it in items])}


def structure_history_block(hist: dict | None, key_events_chars: int = 120) -> str:
    """structure_history() 결과를 '[최근 회차 구조 이력]' advisory 참고 블록으로 렌더(정보 제공, 지시 아님).

    빈 이력(None·items 없음) → "" 반환(주입 생략·프롬프트 바이트 동일 하위호환). 각 회차 한 줄:
      '· 12화 [escalation/reveal@길드 지하] — 각성 접촉 / 흡수 / 무릎' (결측 라벨은 그 조각만 생략).
    말미 집계 한 줄로 최빈 훅 비율·같은 훅/장소 연속 최대 run 을 노출(재탕 신호 — 원시 숫자, 판정 없음)."""
    if not hist:
        return ""
    items = hist.get("items") or []
    if not items:
        return ""
    lines = ["[최근 회차 구조 이력 — 참고(원시 라벨, 지시 아님)]"]
    for it in items:
        tag = "/".join(p for p in (it.get("chapter_function", ""), it.get("hook_type", "")) if p)
        place = it.get("place", "")
        if tag and place:
            meta = f" [{tag}@{place}]"
        elif tag:
            meta = f" [{tag}]"
        elif place:
            meta = f" [@{place}]"
        else:
            meta = ""
        ke = " / ".join(e for e in (it.get("key_events") or []) if e)[:key_events_chars]
        lines.append(f"· {it.get('chapter')}화{meta}" + (f" — {ke}" if ke else ""))
    agg = [f"최빈 훅 비율 {hist.get('hook_monotony', 0.0)}"]
    if hist.get("hook_max_run", 0) > 1:
        agg.append(f"같은 훅 연속 최대 {hist['hook_max_run']}")
    if hist.get("place_max_run", 0) > 1:
        agg.append(f"같은 장소 연속 최대 {hist['place_max_run']}")
    lines.append("(집계) " + " · ".join(agg))
    return "\n".join(lines) + "\n"
