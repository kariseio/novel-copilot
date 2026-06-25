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

    def _gen_category(self, world: WorldConfig, cat: str, done_titles: list[str]) -> list[dict]:
        """카테고리 1개 심층 생성 — 개요 1 + 세부 4~6항목(항목당 5~8문장 + 주입 트리거 키워드).
        단일 콜 13카테고리 일괄(→12항목 빈약)의 깊이 한계를 분할로 해소: '책 한 권' 분량으로 가는 단위."""
        label = CATEGORY_LABEL.get(cat, cat)
        sys = (f"너는 이 작품의 세계관 설정집을 쓴다. '{label}' 카테고리만 깊게 판다. "
               "1) overview: 이 카테고리의 전체 그림 1항목(6~10문장). "
               "2) 세부 항목 4~6개: 각각 title(고유명 위주) + prose(5~8문장 — 작동 원리·역사·예외·구체 사례, "
               "그리고 이 작품의 전제·톤이 요구하는 만큼의 긴장이나 변화의 여지) "
               "+ keywords(본문에 이 설정이 관련될 때 등장할 단어 3~5개). "
               "설정 수치(눈색·등급 등 추적값)는 쓰지 마라(온톨로지 소유). 이미 있는 항목과 중복 금지. JSON만.")
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n전제: {world.premise}\n시놉시스: {world.synopsis}\n"
               f"[이미 있는 항목]{done_titles[-30:]}\n\n"
               '{"entries":[{"title":"","prose":"","keywords":["",""]}]}')
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}],
                                          temperature=0.7, max_tokens=4500)
            return raw.get("entries", []) or []
        except Exception:
            return []

    def generate(self, world: WorldConfig, seed: ProjectSeed, deep: bool = True, bus=None) -> list[BibleEntry]:
        cats = template_for(world.genre or seed.genre)
        out: list[BibleEntry] = []
        ids: set[str] = set()
        titles: list[str] = []

        def _add(cat: str, e: dict):
            title = (e.get("title") or "").strip()
            if not title or title in titles:
                return
            eid = _slug(title, ids)
            ids.add(eid)
            titles.append(title)
            out.append(BibleEntry(entry_id=eid, category=normalize_category(cat), title=title,
                                  prose=(e.get("prose") or "").strip(),
                                  keywords=[k for k in (e.get("keywords") or []) if k][:5],
                                  provenance="ai_worldgen", status="ai_unreviewed", promoted=False))

        if deep:
            for i, cat in enumerate(cats):        # 카테고리별 분할 심층 생성(카테고리당 1콜) — 진행 실시간 방출
                if bus is not None:
                    bus.emit("worldgen", "bible", label=CATEGORY_LABEL.get(cat, cat),
                             idx=i + 1, total=len(cats))
                for e in self._gen_category(world, cat, titles):
                    _add(cat, e)
            if out:
                return out
        # 폴백/얕은 모드: 기존 단일 콜
        cat_desc = ", ".join(f"{c}({CATEGORY_LABEL.get(c, c)})" for c in cats)
        sys = ("너는 이 작품의 세계관 설정집을 쓴다. 작품에 맞는 설정집 항목을 카테고리별로 1~2개씩 작성하라. "
               "각 항목은 title 과 prose(3~6문장), keywords(3개). 설정 수치는 쓰지 마라. JSON만.")
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n전제: {world.premise}\n시놉시스: {world.synopsis}\n"
               f"[작성할 카테고리]\n{cat_desc}\n\n"
               '{"entries":[{"category":"<카테고리 키>","title":"","prose":"","keywords":[""]}]}')
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}], temperature=0.6, max_tokens=3000)
            for e in raw.get("entries", []) or []:
                _add(e.get("category") or "glossary", e)
        except Exception:
            pass
        return out
