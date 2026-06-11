# -*- coding: utf-8 -*-
"""설정집 컴파일 (R2) — promote(캐논으로 박기) + bible_digest(narrative 주입) + 기존 프로젝트 부트스트랩.

비대칭 보존: bible 항목은 기본 narrative. promote 해야만 world_rule 로 승격.
주의: 세계규칙은 SEMANTIC(의미층) — 추적·프롬프트 주입(advisory)이지 하드 게이트가 아님(하드 캐논은 det/quasi).
MVP promote_target = world_rule 만(가장 깔끔). 속성/타임라인/관계 승격은 후속.
"""
from __future__ import annotations
import re

from ..domain.world import WorldRuleSpec
from ..domain.bible import BibleEntry, StoryBible, CATEGORY_LABEL
from ..domain.types import RetrievedItem


def _slug(s: str, existing: set[str]) -> str:
    base = re.sub(r"[^가-힣a-z0-9]+", "_", (s or "").lower()).strip("_")   # 한글 허용(이전: 한글→'rule' 붕괴)
    if not base:
        base = "rule"
    sid, i = base, 2
    while sid in existing:
        sid, i = f"{base}_{i}", i + 1
    return sid


def entry_to_world_rule(entry: BibleEntry, existing_ids: set[str]) -> WorldRuleSpec:
    """promote 된 설정집 항목 → WorldRuleSpec(SEMANTIC 세계규칙 — 추적·주입 advisory). 'bible_' 접두로 build_rules 네임스페이스와 분리."""
    rid = "bible_" + _slug(entry.title or entry.entry_id, {e[6:] for e in existing_ids if e.startswith("bible_")})
    text = (entry.prose or entry.title).strip()
    kws = re.findall(r"[가-힣A-Za-z0-9]{2,}", (entry.title or "") + " " + (entry.prose or ""))[:6]  # 추출 가이드용
    return WorldRuleSpec(rule_id=rid, text=text, flag=rid, keywords=kws,
                         extract_hint=f"세계규칙 '{entry.title}' 위반")


def bible_digest(bible: StoryBible, budget: int = 1500,
                 context_hint: str = "") -> tuple[list[RetrievedItem], int]:
    """설정집 요약을 narrative 앵커로. promoted 상시 + 이번 화 맥락(키워드/제목 매칭) 관련 카드 우선
    — 로어북식 조건 주입(예산 컷 나열 → 관련도 선별). 반환=(items, dropped)."""
    live = [e for e in bible.entries if e.status != "deprecated"]

    def score(e):
        s = 100 if e.promoted else 0
        if context_hint:
            s += sum(3 for k in (e.keywords or []) if k and k in context_hint)
            if e.title and e.title in context_hint:
                s += 2
        return s

    ordered = sorted(live, key=score, reverse=True)
    lines, total = [], 0
    for e in ordered:
        cat = CATEGORY_LABEL.get(e.category, e.category)
        line = f"[{cat}] {e.title}: {e.prose[:200]}"
        if lines and total + len(line) > budget:
            break
        lines.append(line)
        total += len(line)
    dropped = len(ordered) - len(lines)
    if not lines:
        return [], dropped
    return [RetrievedItem(source="bible", ref="digest", text="[세계관 설정집]\n" + "\n".join(lines))], dropped


def migrate_world_to_bible(world) -> list[BibleEntry]:
    """기존 프로젝트(설정집 없음) 부트스트랩 — 이미 캐논인 world_rules 를 설정집에 표시(promoted)."""
    entries: list[BibleEntry] = []
    for i, wr in enumerate(world.world_rules, start=1):
        entries.append(BibleEntry(entry_id=f"mig_rule{i}", category="taboo_worldrule",
                                  title=(wr.text or "세계규칙")[:24], prose=wr.text,
                                  promoted=True, promote_target="world_rule", world_rule_id=wr.rule_id,
                                  provenance="seed", status="author_approved"))   # world_rule_id 부여 → 삭제 시 정상 demote
    return entries
