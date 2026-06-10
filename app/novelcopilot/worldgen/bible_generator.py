# -*- coding: utf-8 -*-
"""설정집 생성 (R2) — 장르 템플릿 카테고리별로 산문 설정 항목을 LLM이 풍부하게 생성.

'AI는 사람과 다르지 않다 — 사람이 시간 때문에 못 하는 깊은 설정집을 즉시·일관되게'.
생성물은 provenance=ai_worldgen, promoted=False(작가가 캐논으로 박을지 선택). 구조적 사실은 온톨로지가,
여기서는 산문 디테일(마법체계·종족·지리·문화 등)을 담는다.
"""
from __future__ import annotations
import json
import re

from ..domain.world import WorldConfig
from ..domain.project import ProjectSeed
from ..domain.bible import BibleEntry, template_for, CATEGORY_LABEL, normalize_category
from ..llm.base import LLMProvider


def _slug(s: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")   # 내부 entry id — ascii
    if not re.search(r"[a-z0-9]", base):
        base = "entry"
    sid, i = base, 2
    while sid in existing:
        sid, i = f"{base}_{i}", i + 1
    return sid


class BibleGenerator:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def generate(self, world: WorldConfig, seed: ProjectSeed) -> list[BibleEntry]:
        cats = template_for(world.genre or seed.genre)
        cat_desc = ", ".join(f"{c}({CATEGORY_LABEL.get(c, c)})" for c in cats)
        sys = ("너는 웹소설 세계관 설정집 작성자다. 작품에 맞는 설정집 항목을 카테고리별로 1~2개씩 작성하라. "
               "각 항목은 title(짧게)과 prose(3~6문장, 구체적 산문)로. 설정 수치(눈색·등급 등 추적값)는 적지 마라(그건 온톨로지 소유). "
               "세계의 작동 원리·분위기·디테일 위주. JSON만.")
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n전제: {world.premise}\n시놉시스: {world.synopsis}\n"
               f"[작성할 카테고리]\n{cat_desc}\n\n"
               '{"entries":[{"category":"<카테고리 키>","title":"","prose":""}]}')
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}],
                                          temperature=0.6, max_tokens=3000)
            items = raw.get("entries", []) or []
        except Exception:
            return []
        out: list[BibleEntry] = []
        ids: set[str] = set()
        for e in items:
            cat = normalize_category(e.get("category"))
            title = (e.get("title") or "").strip()
            if not title:
                continue
            eid = _slug(title, ids)
            ids.add(eid)
            out.append(BibleEntry(entry_id=eid, category=cat, title=title,
                                  prose=(e.get("prose") or "").strip(),
                                  provenance="ai_worldgen", status="ai_unreviewed", promoted=False))
        return out
