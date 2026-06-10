# -*- coding: utf-8 -*-
"""드리프트 신호 (R4) — 결정론 advisory(LLM 0콜). 의미 판정자는 두지 않음(비용·노이즈, 설계 cut).

에피소드 완료 시점에 '이 에피소드가 설계대로 굴러갔는가'를 결정론으로 점검:
- cast_missing: 필수 등장 인물이 에피소드 회차들에 한 번도 안 나옴(scan_present_ids=substring, 결정론).
- pacing_overrun: 배정 회차수(target_chapters) 초과.
신호는 '경고(advisory)'다 — 생성을 차단하지 않고 작가에게 surface. 재계획은 작가 선택(REGEN).
"""
from __future__ import annotations


def episode_drift_signals(episode, chapter_texts: list[str], ontology) -> list[str]:
    signals: list[str] = []
    appeared: set = set()
    for t in chapter_texts:
        appeared |= set(ontology.scan_present_ids(t))
    missing = [c for c in episode.required_cast if c not in appeared]
    if missing:
        names = [ontology.name(c) for c in missing]
        signals.append(f"cast_missing: 에피소드 필수 인물 미등장({', '.join(names)})")
    if len(chapter_texts) > episode.target_chapters:
        signals.append(f"pacing_overrun: {len(chapter_texts)}화 사용 > 목표 {episode.target_chapters}화")
    return signals
