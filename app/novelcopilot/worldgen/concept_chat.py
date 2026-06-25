# -*- coding: utf-8 -*-
"""컨셉 대화 엔진 — 생성 '전' 대화로 ConceptBrief 를 점진 구축.

매 턴: 작가의 말을 브리프에 반영(누적·갱신) → 무엇이 바뀌었는지(changes) 보고 → 다음 질문 추천 →
빈 곳·모순 되묻기(gaps). LLM 은 '브리프 전체를 다시 써서' 반환(점진 병합은 코드가 신뢰) — 충돌 없는 단일 진실.
worldgen_chat(생성 후 그래프 반영)과 분리: 여기선 ProjectState 가 아직 없고 설계서만 빚는다.
"""
from __future__ import annotations
import json

from ..domain.draft import ConceptBrief
from ..llm.base import LLMProvider

_SYS = (
    "너는 작가와 함께 웹소설 세계관을 빚어가는 공동 창작자다. 친근하고 구체적으로, 한 번에 하나씩 깊게 판다.\n"
    "[하는 일] 작가의 새 메시지를 읽고 '작품 설계서(brief)'를 갱신한다. 작가가 던진 단서를 살리되, "
    "막연하면 한두 가지를 너의 제안으로 구체화해서 채운다(작가가 싫으면 바꾸면 됨). 매 턴 조금씩 발전시켜라.\n"
    "[반환 — JSON만]\n"
    "- reply: 작가에게 건네는 2~4문장. 공감 + '이번에 무엇을 어떻게 반영/제안했는지' + 자연스러운 다음 한 걸음.\n"
    "- brief: 설계서 '전체'를 갱신해 다시 써라(기존 내용 유지 + 이번 반영). 필드: "
    "title, genre, tone, logline(한 문장 핵심), premise(2~4문장), setting(세계·배경), "
    "characters[{name, role, want}], world_rules[문장], conflicts[문장], themes[단어], "
    "keywords[이 작품을 가리키는 검색·분류 태그 — 작가가 실제로 쓴 언어와 이 작품의 전제·소재·정서에서 도출해 채워라(미리 정한 장르 목록에 끼워맞추지 말 것)], target_chapters(정수).\n"
    "  · 장르·분위기(tone)·target_chapters 는 대화에서 드러나면 적극 제안해 채워라(작품의 호흡·연재 형태에 맞는 분량으로). "
    "단 [작가 확정]으로 표시된 값은 절대 바꾸지 말고 그대로 둬라.\n"
    "- changes: 이번 턴에 바뀐 것 짧은 항목들(예: '추가: 마법은 수명을 대가로 쓴다', '구체화: 주인공의 동기').\n"
    "- questions: 세계를 더 깊게 만들 추천 질문 2~3개(작가가 누르면 바로 답이 되는 형태).\n"
    "- gaps: 아직 비었거나 서로 어긋나는 점 0~2개(되물어 보완 유도). 없으면 빈 배열.\n"
    "- ready: 첫 회차를 쓰기 시작해도 좋을 만큼(로그라인+주인공+갈등+배경) 무르익었으면 true.\n"
    "기존 설정과 모순 금지. 일반적인 태그를 나열하기보다, 이 작품에서만 성립하는 구체를 적어라."
)

_SCHEMA = ('{"reply":"","brief":{"title":"","genre":"","tone":"","logline":"","premise":"","setting":"",'
           '"characters":[{"name":"","role":"","want":""}],"world_rules":[],"conflicts":[],"themes":[],"keywords":[],'
           '"target_chapters":200},"changes":[],"questions":[],"gaps":[],"ready":false}')


class ConceptChat:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def turn(self, brief: ConceptBrief, history: list[dict], message: str, locked: dict | None = None) -> dict:
        convo = "\n".join(f"{t.get('role')}: {t.get('text', '')}" for t in history[-10:])
        lock_block = (f"[작가 확정 — 절대 바꾸지 마라]\n{locked}\n\n" if locked else "")
        usr = (f"[현재 설계서]\n{brief.model_dump_json()}\n\n{lock_block}"
               f"[최근 대화]\n{convo or '(없음)'}\n\n[작가의 새 메시지]\n{message}\n\n{_SCHEMA}")
        try:
            r = self.provider.chat_json([{"role": "system", "content": _SYS},
                                         {"role": "user", "content": usr}],
                                        temperature=0.6, max_tokens=2600)
        except Exception:
            return {"reply": "(응답 생성에 실패했어요. 한 번만 다시 말씀해 주세요.)",
                    "brief": brief.model_dump(), "changes": [], "questions": [], "gaps": [], "ready": False}
        # 브리프 머지: LLM 이 전체를 다시 줬으면 검증 후 채택, 비면 기존 유지(데이터 손실 방지)
        merged = brief
        try:
            nb = ConceptBrief.model_validate(r.get("brief") or {})
            if nb.logline or nb.premise or nb.characters or nb.setting:   # 빈 응답이 기존을 밀어내지 않게
                merged = nb
        except Exception:
            pass
        return {
            "reply": (r.get("reply") or "").strip(),
            "brief": merged.model_dump(),
            "changes": [c for c in (r.get("changes") or []) if c][:6],
            "questions": [q for q in (r.get("questions") or []) if q][:3],
            "gaps": [g for g in (r.get("gaps") or []) if g][:2],
            "ready": bool(r.get("ready")) or merged.completeness() >= 70,
        }
