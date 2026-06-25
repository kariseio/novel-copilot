# -*- coding: utf-8 -*-
"""협업형 월드젠 대화 (R3) — 작가와 AI가 대화하며 세계관을 점진 구축.

'기계가 쓰고 작가가 조향'의 월드빌딩판: 작가의 말에 맞춰 AI가 세계를 확장·심화하고(신규 엔티티/관계/설정집),
세계를 더 깊게 만들 '되묻는 질문'을 던진다. 제안은 코드가 결정론 게이트로 분류·커밋(LLM 자유도는 제안에만 격리).
비대칭 보존: AI 제안은 잠정으로 착지한다 — 엔티티 provisional=True, 관계 narrative_inferred, 설정집 ai_unreviewed.
작가가 그래프에서 직접 관계를 긋거나 설정집을 promote 해야 ground_truth 캐논으로 승격(서비스 worldgen_turn 이 강제).
"""
from __future__ import annotations
import json

from ..domain.world import WorldConfig
from ..llm.base import LLMProvider


class WorldgenChat:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def turn(self, world: WorldConfig, ontology, bible, history: list[dict], message: str) -> dict:
        roster = [{"id": e.id, "name": e.name, "etype": e.etype} for e in ontology.entities.values()]
        rel_keys = list(ontology.rel_catalog.keys())
        ent_types = list(ontology.entity_types.keys())
        bible_titles = [{"category": b.category, "title": b.title} for b in bible.entries]
        convo = "\n".join(f"{t.get('role')}: {t.get('text','')}" for t in history[-8:])
        sys = ("너는 작가와 함께 세계관을 만들어가는 공동 창작자다. 작가의 말에 맞춰 세계를 확장·심화하라.\n"
               "- reply: 작가에게 건네는 1~3문장 대화(공감+방향 제시).\n"
               "- new_entities: 대화에서 새로 등장/합의된 고유 엔티티(인물/세력/장소/아이템/사건). 기존 명부에 없는 것만.\n"
               "- new_relations: 엔티티 사이 관계. rel_id 는 제시된 관계키 우선, 없으면 간결한 자유 라벨(무엇이든). "
               "관계에 질적 상태가 있으면 state 에 이 작품의 톤·맥락에서 도출한 짧은 라벨을 적되, 특정 분위기를 미리 가정하지 말 것. src/dst 는 명부 id 또는 새 이름.\n"
               "- new_bible: 설정집 항목(카테고리+title+prose 3~5문장). 세계 작동원리·분위기·디테일.\n"
               "- questions: 세계를 더 깊게 만들 되묻는 질문 1~2개.\n"
               "기존 설정과 모순 금지. 한 턴에 너무 많이 쏟지 말고 대화 흐름에 맞게. JSON만.")
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n전제: {world.premise}\n"
               f"[기존 명부]{json.dumps(roster, ensure_ascii=False)}\n[엔티티타입]{ent_types}\n[관계키]{rel_keys}\n"
               f"[설정집 제목]{json.dumps(bible_titles, ensure_ascii=False)}\n[최근 대화]\n{convo}\n\n[작가]{message}\n\n"
               '{"reply":"","new_entities":[{"name":"","etype":"","role":""}],'
               '"new_relations":[{"src":"","dst":"","rel_id":"","state":""}],'
               '"new_bible":[{"category":"","title":"","prose":""}],"questions":[""]}')
        try:
            return self.provider.chat_json([{"role": "system", "content": sys},
                                            {"role": "user", "content": usr}],
                                           temperature=0.6, max_tokens=2500)
        except Exception:
            return {"reply": "(응답 생성 실패 — 다시 시도해 주세요)", "new_entities": [],
                    "new_relations": [], "new_bible": [], "questions": []}
