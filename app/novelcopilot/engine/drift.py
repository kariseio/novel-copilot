# -*- coding: utf-8 -*-
"""드리프트 신호 (R4) — 결정론 advisory(LLM 0콜). 의미 판정자는 두지 않음(비용·노이즈, 설계 cut).

에피소드 완료 시점에 '이 에피소드가 설계대로 굴러갔는가'를 결정론으로 점검:
- cast_missing: 필수 등장 인물이 에피소드 회차들에 한 번도 안 나옴(scan_present_ids=substring, 결정론).
- event_uncovered (T2): 필수 사건(required_events)의 키워드가 본문에 과반 미만 — 계획 사건 미실현 의심(cast_missing 과 대칭).
- pacing_overrun: 배정 회차수(target_chapters) 초과.
신호는 '경고(advisory)'다 — 생성을 차단하지 않고 작가에게 surface. 재계획은 작가 선택(REGEN).
"""
from __future__ import annotations
import re

# 교착어 조사 접미(긴 것 우선) — 키워드에 조사가 박혀 substring 매칭이 깨지는 한국어 결함 방지.
# 어간만 본문에 substring 매칭하면 본문의 다른 조사형('광민이'/'광민은')도 어간('광민')을 포함하므로 매칭됨.
_PARTICLES = sorted((
    "으로서", "으로써", "에게서", "이라고", "에서", "과의", "와의", "으로", "에게", "한테", "께서",
    "처럼", "보다", "마다", "까지", "부터", "조차", "마저", "라고", "이나",
    "은", "는", "이", "가", "을", "를", "와", "과", "의", "에", "도", "만", "로", "랑", "나",
), key=len, reverse=True)


def _stem(tok: str) -> str:
    """조사 접미 결정론 제거(어간 ≥2자 보호 — '회의'의 '의'처럼 과제거 방지)."""
    for p in _PARTICLES:
        if tok.endswith(p) and len(tok) - len(p) >= 2:
            return tok[:-len(p)]
    return tok


def _event_keywords(s: str) -> list[str]:
    """사건 태그의 의미 키워드(2자+ 한글/영숫자) — cast 의 인명 대신 사건의 '내용어'로 커버리지 근사."""
    return re.findall(r"[가-힣A-Za-z0-9]{2,}", s or "")


def uncovered(events: list[str], body: str) -> list[str]:
    """body 에 키워드 어간이 과반 미만으로 나타나는 사건만 반환(event_uncovered 와 동일 기준).
    T2(완료 점검)·T4(메뉴 refresh 시 실현된 required 소진 제외)가 공유 — 단일 기준 출처."""
    out: list[str] = []
    for ev in events:
        if not (ev or "").strip():
            continue
        kws = _event_keywords(ev)
        if not kws:
            continue
        if sum(1 for k in kws if _stem(k) in body) / len(kws) < 0.5:   # 어간 과반 미달 → 미실현
            out.append(ev)
    return out


def episode_drift_signals(episode, chapter_texts: list[str], ontology) -> list[str]:
    signals: list[str] = []
    appeared: set = set()
    for t in chapter_texts:
        appeared |= set(ontology.scan_present_ids(t))
    missing = [c for c in episode.required_cast if c not in appeared]
    if missing:
        names = [ontology.name(c) for c in missing]
        signals.append(f"cast_missing: 에피소드 필수 인물 미등장({', '.join(names)})")
    # T2: 필수 사건 커버리지 — cast_missing 과 대칭(결정론 advisory). 사건 태그 키워드의 '어간'이 에피소드 회차
    #     본문에 과반 미만 나타나면 '미실현 의심'. 어간 매칭(_stem)으로 조사 변이는 흡수, 동의어는 여전히 false 가능
    #     → 판정 아닌 신호, 차단 안 함. (적대리뷰 실측: 어간 미정규화 시 false positive 42%, 정규화로 회복)
    body = " ".join(chapter_texts)
    planned = [e for e in (episode.required_events or []) if (e or "").strip()]
    unc = uncovered(planned, body)
    if unc:
        signals.append(f"event_uncovered: 계획 필수 사건 미실현 의심 {len(unc)}/{len(planned)}건 — {', '.join(unc[:3])}")
    if len(chapter_texts) > episode.target_chapters:
        signals.append(f"pacing_overrun: {len(chapter_texts)}화 사용 > 목표 {episode.target_chapters}화")
    return signals
