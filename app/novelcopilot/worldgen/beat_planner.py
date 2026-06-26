# -*- coding: utf-8 -*-
"""비트 플래너 — 골격 회차 공급 + 아웃라인 소진 시 자동 연장(끝없는 체험 가능).

골격에 해당 회차가 있으면 그대로, 없으면 '지금까지의 이야기'로부터 다음 비트를 생성한다.
이는 회차 단위 점진 생성 백본과 자연스럽게 맞물린다.
"""
from __future__ import annotations
import json

from ..domain.world import WorldConfig, Beat
from ..llm.base import LLMProvider


class BeatPlanner:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def beat_for(self, world: WorldConfig, chapter: int,
                 recent_summaries: list[str], directives: list[str]) -> Beat:
        existing = next((b for b in world.beats if b.chapter == chapter), None)
        if existing:
            return existing
        return self._extend(world, chapter, recent_summaries, directives)

    def _extend(self, world: WorldConfig, chapter: int,
                recent_summaries: list[str], directives: list[str]) -> Beat:
        char_ids = [e.id for e in world.entities if e.etype == "character"]
        _SCHEMA = ' 기존 설정과 모순 금지. JSON: {"title":"","summary":"","key_events":["",""],"entities":["인물 id"]}'
        # 도입부 1~3화 미니아크 페이싱(연구: 구체적 grounding 후 전환, 한 줄 라벨·즉시 액션 금물)
        if chapter == 1:
            sys = ("이번은 작품 '도입부 1/3화'다. 본격 전개로 직행 말고 — 주인공이 누구인지 한 줄 라벨 아닌 *구체적 장면*"
                   "(직업·하루의 결·처지·결핍·관계)으로 보여주고, 전제의 전환이 *시작되는 조짐*을 깔고 끝에 훅. 전환을 1화에서 다 끝내지 마라. "
                   "세계 규칙은 인포덤프 없이 행동·대사로." + _SCHEMA)
        elif chapter == 2:
            sys = ("이번은 '도입부 2/3화'다. 1화의 조짐이 현실이 되는 전환을 장면으로 극화하고, 규칙은 주인공 행동으로 흘리며 첫 대가가 생기게. "
                   "아직 본격 활약 전 — 적응 단계." + _SCHEMA)
        elif chapter == 3:
            sys = ("이번은 '도입부 3/3화 — 매듭'이다. 처음으로 작은 전환점/사이다를 주고 본격 엔진 진입 발판을 놓아라. "
                   "독자가 인물·변화·이유를 알게." + _SCHEMA)
        else:
            sys = ("웹소설 다음 회차 비트(beat) 1개를 설계. 직전 흐름을 이어 이야기를 의미 있게 전진시키되"
                   "(이 작품의 장르·톤·직전 상황이 요구하는 고유한 추진력을 그 맥락에서 도출해 적용)." + _SCHEMA)
        msg = [
            {"role": "system", "content": sys},
            {"role": "user", "content":
             f"[작품]{world.title} / {world.genre} / {world.tone}\n[인물 id]{char_ids}\n"
             f"[최근 회차 요약]\n" + "\n".join(recent_summaries[-4:]) +
             f"\n[작가 지시]{json.dumps(directives, ensure_ascii=False)}\n[다음 회차 번호]{chapter}"},
        ]
        try:
            d = self.provider.chat_json(msg, temperature=0.5)
            ents = [e for e in d.get("entities", []) if e in set(char_ids)] or char_ids[:2]
            beat = Beat(chapter=chapter, title=d.get("title", f"{chapter}화"),
                        summary=d.get("summary", ""), key_events=d.get("key_events", []), entities=ents)
        except Exception:
            beat = Beat(chapter=chapter, title=f"{chapter}화", summary="이야기를 이어간다.",
                        key_events=["전개"], entities=char_ids[:2])
        world.beats.append(beat)
        return beat
