# -*- coding: utf-8 -*-
"""B-37: 재탕 후보 풀 결정론 제외 (LLM 0콜·신규 상태 0).

진단(design-b37-dp3.md §B-37): 재탕은 모델이 '이미 쓴 재료'를 후보로 다시 보기 때문이다.
B-32e 가 '이력을 보여주고 피하게'(주입식)를 반증했으므로 유일 정공은 **후보에서 코드로 빼서
아예 안 보이게** 하는 것이다(T4 '소진 required 제외'·DP-6 소비 원장 계보). 이 모듈은 두 가지를 한다:

  ⓐ event_menu_filter: 사건 메뉴 후보 중, 최근 N화에 이미 실현된 key_events 와 사건 술어까지
     겹치는(어간 containment≥0.4 그리고 공유 어간≥3 — DP-13 캘리브레이션 재사용) 후보를 제거한다.
     · 보존 목록(불가침): 미실현 required·climax·만기 약속·payoffs 시드는 어떤 매칭이 걸려도 제거하지
       않는다(DP-13 HIGH 교훈: 정상 실현 재료를 죽이지 마라 — never-empty 안전망의 씨앗).
     · 과차단 가드(ⓒ): 같은 장소·인물 재방문은 명사 어간만으론 공유 어간이 ≤2 라 임계 미달(DP-13
       실측: 인물+장소 공유=2)이라 통과한다. 사건 술어까지 ≥3 겹칠 때만 재탕으로 본다(min_shared 역할).
     · never-empty: 필터가 메뉴를 통째로 비우면(과차단) 필터 전 원본을 그대로 반환한다.

  ⓑ hook_whitelist: 훅 유형 화이트리스트에서 '최근 N화 사용 유형'을 뺀 축소 리스트를 준다(금지문
     아님 — 보이는 선택지 자체를 줄임, 앵커링 안전). 전 유형 소진 시 전체 복원(never-empty).

원칙: 무강제(후보 풀 축소일 뿐 지시 아님)·긍정 전용(피할 대상 비노출)·genre-blind·하위호환(입력 미전달 시 no-op).
"""
from __future__ import annotations

from .plan_lint import _stem_set, _containment, _DUP_THRESHOLD, _MIN_KEYWORDS, _MIN_SHARED

# 훅 유형 화이트리스트 — beat_for_episode 프롬프트가 제시하는 유형(자기 라벨용, 강제 아님)과 단일 출처.
# 순서·구성은 그 프롬프트의 것을 그대로 반영한다(누락/추가 시 양쪽을 함께 갱신).
HOOK_WHITELIST: tuple[str, ...] = (
    "action", "reveal", "emotion", "decision", "threat", "question", "twist", "cliffhanger",
)

# DP-22: 마무리 장치 화이트리스트 — 회차가 '물리적으로 닫히는' 장치(장르 무관 craft 축, 자기 라벨용·강제 아님).
#   hook_type(끊는 '방식')과 직교: 이건 마지막 순간을 무엇으로 닫느냐다. beat_for_episode 프롬프트의 closing_device
#   선택지와 단일 출처(누락/추가 시 양쪽 함께 갱신). 순서·구성은 그 프롬프트의 것을 그대로 반영한다.
CLOSING_WHITELIST: tuple[str, ...] = (
    "dialogue", "action", "sensory", "object", "arrival", "interior",
)

# SX-1: 장면 안무(scene_form) 화이트리스트 — 회차의 '중심 장면이 무엇을 하는가'(장르 무관 craft 축, 자기 라벨용·강제 아님).
#   closing_device(마지막 순간을 무엇으로 닫느냐)·hook_type(끊는 방식)과 직교: 이건 이 회차의 중심 장면 '안무'다
#   (접촉→복원→은폐 3연속·심부름 트릭 재사용 같은 안무 반복의 소스 차단 — 4화 정독 실측). beat_for_episode 프롬프트의
#   scene_form 선택지와 단일 출처(누락/추가 시 양쪽 함께 갱신). 순서·구성은 그 프롬프트의 것을 그대로 반영한다.
SCENE_FORM_WHITELIST: tuple[str, ...] = (
    "잠입", "대면", "시험", "추적", "의뢰", "제작", "이동", "사교", "협상",
    "발견", "은폐", "대결", "구출", "협력", "일상",
)


def _is_rehash(cand_stems: set[str], realized_stems: list[set[str]]) -> bool:
    """후보 1개(어간 집합)가 최근 실현 사건 어느 하나와 '재발주' 관계인가 — DP-13과 동일 판정.
    containment≥_DUP_THRESHOLD **그리고** 공유 어간≥_MIN_SHARED 둘 다일 때만 True(과차단 가드).
    후보 어간이 _MIN_KEYWORDS 미만이면(짧은 태그) 근거 부족 → 판정 안 함(False)."""
    if len(cand_stems) < _MIN_KEYWORDS:
        return False
    for rs in realized_stems:
        if len(rs) < _MIN_KEYWORDS:
            continue
        if len(cand_stems & rs) < _MIN_SHARED:
            continue                                  # 엔티티 1~2개 우연 공유(같은 장소·인물 재방문) → 재탕 아님
        if _containment(cand_stems, rs) >= _DUP_THRESHOLD:
            return True
    return False


def event_menu_filter(menu: list[str], recent_key_events: list[str] | None,
                      preserve: list[str] | None = None) -> list[str]:
    """사건 메뉴에서 '최근 실현 사건 재탕' 후보를 결정론 제거한다(순서 보존·dedup 없음 — 호출부 dedup 前 단계).

    menu: generate_event_menu 산출(코드 강제 prepend·dedup·cap 이전의 원본 후보 순서 그대로 받아도 안전).
    recent_key_events: 최근 N화에 실제로 실현된 key_events 원문(호출자가 결정론으로 수집·전달). None/빈 → no-op.
    preserve: 보존 목록(불가침) — 이 텍스트들은 재탕 매칭이 걸려도 절대 제거하지 않는다(미실현 required·climax·
        만기 약속·payoffs). 어간 집합 동일성으로 대조해 조사 변이도 흡수(원문 문자열 완전일치에 의존하지 않음).

    반환: 재탕 후보만 뺀 메뉴. **과차단(전부 제거)되면 원본 menu 를 그대로 반환**(never-empty 불변식).
    """
    src = [e for e in (menu or []) if (e or "").strip()]
    realized = [e for e in (recent_key_events or []) if (e or "").strip()]
    if not src or not realized:
        return list(src)                              # 실현 이력 없음 → 원본(하위호환 no-op)
    realized_stems = [_stem_set(e) for e in realized]
    # 보존 어간 집합(불가침) — 후보의 어간 집합이 보존 항목과 동일하면 재탕이어도 남긴다.
    preserve_stems = [_stem_set(p) for p in (preserve or []) if (p or "").strip()]

    def _protected(cs: set[str]) -> bool:
        if not cs:
            return True                               # 어간 없는 후보(짧은 태그)는 판정 근거 부족 → 보존(안전측)
        return any(cs == ps for ps in preserve_stems if ps)

    kept: list[str] = []
    for e in src:
        cs = _stem_set(e)
        if _protected(cs) or not _is_rehash(cs, realized_stems):
            kept.append(e)
    return kept or list(src)                           # 과차단(전부 제거) → 원본 복원(never-empty)


def hook_whitelist(recent_hook_types: list[str] | None,
                   whitelist: tuple[str, ...] = HOOK_WHITELIST) -> list[str]:
    """훅 유형 화이트리스트에서 '최근 사용 유형'을 뺀 축소 리스트를 준다(순환 제시 — 보이는 선택지 축소).

    recent_hook_types: 최근 N화의 hook_type 원문(대소문자·공백 정규화). None/빈 → 전체 화이트리스트(하위호환).
    반환: whitelist 순서 보존, 최근 사용 유형 제외. **전 유형 소진(전부 최근 사용)되면 전체 복원**(never-empty).
    """
    base = [h for h in whitelist if h]
    used = {(_h or "").strip().lower() for _h in (recent_hook_types or []) if (_h or "").strip()}
    if not used:
        return list(base)                             # 최근 이력 없음 → 전체(하위호환)
    reduced = [h for h in base if h.lower() not in used]
    return reduced or list(base)                      # 전 유형 소진 → 전체 복원(never-empty)


def closing_whitelist(recent_closing_devices: list[str] | None,
                      whitelist: tuple[str, ...] = CLOSING_WHITELIST) -> list[str]:
    """DP-22: 마무리 장치 화이트리스트에서 '최근 사용 장치'를 뺀 축소 리스트를 준다(순환 제시 — 보이는 선택지 축소).

    hook_whitelist 와 동일 계보(B-37 화이트리스트 패턴 확장): 금지문 없이 보이는 선택지 자체를 줄여
    다음 회차 설계가 다른 장치로 닫도록 순환시킨다(앵커링·pink-elephant 안전 — 피할 대상 비노출).
    호출부(copilot)가 '최근 2화'의 자기 라벨 closing_device 를 결정론 수집해 넘긴다(과거 회차 분류 없음).

    recent_closing_devices: 최근 2화의 closing_device 원문(대소문자·공백 정규화). None/빈 → 전체(하위호환).
    반환: whitelist 순서 보존, 최근 사용 장치 제외. **전 장치 소진(전부 최근 사용)되면 전체 복원**(never-empty).
    """
    base = [h for h in whitelist if h]
    used = {(_d or "").strip().lower() for _d in (recent_closing_devices or []) if (_d or "").strip()}
    if not used:
        return list(base)                             # 최근 이력 없음 → 전체(하위호환)
    reduced = [h for h in base if h.lower() not in used]
    return reduced or list(base)                      # 전 장치 소진 → 전체 복원(never-empty)


def scene_form_whitelist(recent_forms: list[str] | None,
                         whitelist: tuple[str, ...] = SCENE_FORM_WHITELIST) -> list[str]:
    """SX-1: 장면 안무 화이트리스트에서 '최근 2화 사용 형태'를 뺀 축소 리스트를 준다(순환 제시 — 보이는 선택지 축소).

    closing_whitelist(DP-22)·hook_whitelist(B-37)와 동일 계보: 금지문 없이 보이는 선택지 자체를 줄여
    다음 회차 설계가 다른 안무로 전개하도록 순환시킨다(앵커링·pink-elephant 안전 — 피할 대상 비노출·B-32e 준수).
    호출부(copilot)가 '최근 2화'의 자기 라벨 scene_form 을 결정론 수집해 넘긴다(과거 회차 분류 없음 — 검출기 0).

    recent_forms: 최근 2화의 scene_form 원문(대소문자·공백 정규화). None/빈 → 전체(하위호환).
    반환: whitelist 순서 보존, 최근 사용 형태 제외. **전 형태 소진(전부 최근 사용)되면 전체 복원**(never-empty).
    """
    base = [h for h in whitelist if h]
    used = {(_f or "").strip().lower() for _f in (recent_forms or []) if (_f or "").strip()}
    if not used:
        return list(base)                             # 최근 이력 없음 → 전체(하위호환)
    reduced = [h for h in base if h.lower() not in used]
    return reduced or list(base)                      # 전 형태 소진 → 전체 복원(never-empty)
