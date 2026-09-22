# -*- coding: utf-8 -*-
"""설정집 생성 (R2) — 장르 템플릿 카테고리별로 산문 설정 항목을 LLM이 풍부하게 생성.

'AI는 사람과 다르지 않다 — 사람이 시간 때문에 못 하는 깊은 설정집을 즉시·일관되게'.
생성물은 provenance=ai_worldgen, promoted=False(작가가 캐논으로 박을지 선택). 구조적 사실은 온톨로지가,
여기서는 산문 디테일(마법체계·종족·지리·문화 등)을 담는다.
"""
from __future__ import annotations
import json
import re

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
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

    @promptlog.stage("worldgen:bible_category")
    def _gen_category(self, world: WorldConfig, cat: str, done_titles: list[str]) -> list[dict]:
        """카테고리 1개 심층 생성 — 개요 1 + 세부 4~6항목(항목당 5~8문장 + 주입 트리거 키워드).
        단일 콜 13카테고리 일괄(→12항목 빈약)의 깊이 한계를 분할로 해소: '책 한 권' 분량으로 가는 단위."""
        label = CATEGORY_LABEL.get(cat, cat)
        # VA-3(2026-08-15 감사): 어휘 계약 신설 — 설정집 프로즈는 매 장면 '구현하라' 명령이 붙는
        #   앵커라, 계약 없는 빈칸은 모델의 '고풍 백과' 프라이어가 채운다(낙관·잔지·조복 실측 계보).
        #   전부 긍정형·대시 0. prose 300자(다이제스트 200자 컷 경계 — 절단 파편 앵커 차단).
        sys = (f"너는 이 작품의 세계관 설정집을 쓴다. '{label}' 카테고리만 깊게 판다. "
               "1) overview: 이 카테고리의 전체 그림 하나(300자 이내). "
               "2) 세부 항목 4~6개: 각각 title, prose, keywords.\n"
               "prose 는 300자 이내로, 그것이 어떻게 작동하는지, 언제부터 그랬는지, 실제로 그 일이 "
               "벌어진 한 사례를 담아 쓴다. 첫 두 문장 안에 이 항목의 핵심이 다 들어가게 한다.\n"
               "[어휘] 항목은 이 이야기가 벌어지는 시대와 장소에서 사람들이 실제로 입에 올리는 말로 쓴다. "
               "낯선 현상은 그것이 하는 일과 사람들이 겪는 일로 적는다. 무엇이 언제 일어나고, 누가 무엇을 "
               "하고, 그래서 무엇이 남는지를 적는다. 손에 잡히는 사물, 눈으로 볼 수 있는 동작, 세어서 "
               "확인되는 것으로 문장을 세운다. 상태를 한 낱말로 이름 붙이는 일은 인물 시트와 온톨로지가 "
               "맡고, 여기에는 그 상태에서 무슨 일이 벌어지는지를 적는다.\n"
               "title 은 이 세계 사람들이 그것을 가리킬 때 실제로 쓰는 말로 적는다(현장에서 부르는 말, "
               "기관이 서류에 쓰는 말). 마땅한 이름이 없으면 그것이 하는 일을 그대로 제목으로 삼는다. "
               "keywords 는 본문에서 이 설정이 걸릴 때 등장할 단어 3~5개.\n"
               "각 항목은 이 세계에서 되풀이해 일어나는 일을 적는다. 특정 인물의 현재 수치와 등급은 "
               "인물 시트가 맡는다. [이미 있는 항목] 목록에 없는 것만 새로 쓴다. JSON만.")
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n"
               "[전제·시놉시스(참조 전용)] 여기서는 무슨 일이 일어나는지를 가져오고, 항목 문장은 위 어휘 원칙에 따라 새로 쓴다.\n"
               f"전제: {world.premise}\n시놉시스: {world.synopsis}\n"
               f"[이미 있는 항목]{done_titles[-30:]}\n\n"
               '{"entries":[{"title":"","prose":"","keywords":["",""]}]}')
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}],
                                          temperature=0.7)
            return raw.get("entries", []) or []
        except Exception:
            return []

    @promptlog.stage("worldgen:bible")
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
        # VA-3: 폴백도 같은 계약을 그대로 재명시(4조 — 축약 참조는 그 콜에서 계약 증발)
        sys = ("너는 이 작품의 세계관 설정집을 쓴다. 작품에 맞는 설정집 항목을 카테고리별로 1~2개씩 쓴다. "
               "각 항목은 title, prose(300자 이내), keywords(3개).\n"
               "[어휘] 항목은 이 이야기가 벌어지는 시대와 장소에서 사람들이 실제로 입에 올리는 말로 쓴다. "
               "낯선 현상은 그것이 하는 일과 사람들이 겪는 일로 적는다. 무엇이 언제 일어나고, 누가 무엇을 "
               "하고, 그래서 무엇이 남는지를 적는다. 손에 잡히는 사물, 눈으로 볼 수 있는 동작, 세어서 "
               "확인되는 것으로 문장을 세운다. 상태를 한 낱말로 이름 붙이는 일은 인물 시트와 온톨로지가 "
               "맡고, 여기에는 그 상태에서 무슨 일이 벌어지는지를 적는다.\n"
               "title 은 이 세계 사람들이 그것을 가리킬 때 실제로 쓰는 말로 적는다. 마땅한 이름이 없으면 "
               "그것이 하는 일을 그대로 제목으로 삼는다. 각 항목은 이 세계에서 되풀이해 일어나는 일을 "
               "적는다. 특정 인물의 현재 수치와 등급은 인물 시트가 맡는다. JSON만.")
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n전제: {world.premise}\n시놉시스: {world.synopsis}\n"
               f"[작성할 카테고리]\n{cat_desc}\n\n"
               '{"entries":[{"category":"<카테고리 키>","title":"","prose":"","keywords":[""]}]}')
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}], temperature=0.6)
            for e in raw.get("entries", []) or []:
                _add(e.get("category") or "glossary", e)
        except Exception:
            pass
        return out
