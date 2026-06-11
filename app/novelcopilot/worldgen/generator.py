# -*- coding: utf-8 -*-
"""협업형 worldgen — 시드(장르/톤/전제)로부터 WorldConfig 를 LLM이 생성(scenario.py 하드코딩 대체).

핵심: 일관성 엔진이 검증할 '추적 가능한 설정 축'을 포함시키되, **장르에 맞는 축**을 고르게 한다(레벨/사망 강제 금지).
- 추적 축 2+ (이 작품에 실제로 중요한 것만): categorical(눈색·소속·신분) / numeric(등급·내공 — 단조는 정말 단조일 때만) /
  state(생애주기: 사망·각성·정체발각·결혼 등; states+irreversible+terminal 데이터로). 종류는 자유.
- 관계(선택): 시작 관계(seed_edges)·작품별 관계 타입. rel_id 는 자유 라벨.
- 세계규칙/복선(선택). 회귀·부활·리젠·타임루프면 allow_state_reversal=true.
출력은 Pydantic 으로 검증(계약 위반 시 1회 교정 재시도). 인간 개입(검토·수정)은 상위 레이어에서.
"""
from __future__ import annotations
import json

from pydantic import ValidationError
from ..domain.world import WorldConfig
from ..domain.project import ProjectSeed
from ..llm.base import LLMProvider

_SCHEMA_HINT = """{
  "title": "작품 제목",
  "genre": "장르", "tone": "톤", "premise": "한 줄 전제", "synopsis": "3~5문장 시놉시스",
  "allow_state_reversal": false,        // 회귀/부활/리젠/타임루프면 true
  "attributes": [
    // 이 작품에 맞는 추적 축만(장르 불문). kind: categorical | numeric | state. 아래는 종류 예시일 뿐, 내용은 새로.
    {"key":"affiliation","label":"소속","kind":"categorical","vocab":["A","B","무소속"],"mutable":true},
    {"key":"status","label":"생사","kind":"state","states":["alive","dead"],"irreversible":["dead"],"terminal":["dead"],"mutable":true}
    // 로맨스 예) {"key":"secret","kind":"state","states":["숨김","발각"],"irreversible":["발각"]}
    // 무협 예)   {"key":"realm","label":"경지","kind":"numeric","mutable":true}   // 오르내리면 monotonic 생략
    // 게임/헌터 예) {"key":"rank","kind":"numeric","monotonic":"non_decreasing","mutable":true}
  ],
  "entities": [
    {"id":"hero","name":"주인공이름","etype":"character","aliases":["약칭"],
     "attrs":{"affiliation":"A"},"base_status":"alive",
     "profile":"인물 설계서: 배경·성격·욕망(원하는 것/두려운 것)·다른 핵심 인물과의 관계. 말투 지정 금지 — 말투는 이 설정에서 창발한다"}
  ],
  "relations": [],                      // 선택: 작품별 관계 타입(RelationSpec). rel_id 자유
  "seed_edges": [],                     // 선택: 시작 시점 관계 엣지
  "world_rules": [                      // 선택(비워도 됨). 넣을 땐 반드시 이 형태(flag 영문 필수):
    {"rule_id":"no_reawaken","text":"각성은 일생에 한 번뿐.","flag":"reawakening","keywords":["재각성"],"extract_hint":"두 번째 각성 사건"}
  ],
  "timeline": [],                       // 선택: 예정된 상태 전이(예: {"entity_id":"x","attr":"status","value":"dead","eff_from":4})
  "beats": [
    {"chapter":1,"title":"제목","summary":"요약","key_events":["사건1","사건2"],"entities":["hero"]}
  ],
  "wiki_seeds": [],                     // 선택: 회수할 복선 plot_thread(payoff_deadline)
  "style": {                            // 선택: 장르에 맞는 집필 스타일(미제공 시 웹소설 기본)
    "system_persona": "너는 인기 한국 <장르> 웹소설 작가다. ...(이 작품 장르의 정체성으로)",
    "ending_hook": "cliffhanger|soft|none — 잔잔한 장르는 soft 고려",
    "rules": ["이 장르 문체 규칙(예: 로맨스=감정·내면 충분히, 미스터리=단서는 공정하게)"]
  }
}"""


class WorldGenerator:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def _system(self, target_chapters: int) -> str:
        return (
            "너는 한국 웹소설 세계관 설계자다. 장르 불문, 주어진 시드로 '일관성 추적이 가능한' 설정집을 JSON으로 설계한다.\n"
            "이 작품에 '실제로 중요한' 축만 신선하게 설계하라 — 장르에 없는 축(레벨/사망 등)을 억지로 넣지 말 것:\n"
            "1) attributes: 변하면 안 되거나 변화를 추적해야 할 축 2개 이상. 종류 자유(key 는 영문 snake_case):\n"
            "   - categorical(통제어휘 vocab + mutable): 눈색·소속·진영·신분 등\n"
            "   - numeric(monotonic 은 '정말 단조일 때만'; 오르내리면 생략): 등급·내공·호감 등\n"
            "   - state(생애주기): 사망·각성·정체발각·결혼 등 상태 전이. states + irreversible(되돌릴 수 없는 값) "
            "+ terminal(등장불가='서사에서 제거됨' — 사망·소멸뿐. 'none' 같은 평범한 초기/기본값을 절대 넣지 마라) 지정.\n"
            "   ※ 장르에 맞춰: 로맨스=관계/비밀/정체, 무협=경지(비단조 가능)/진영, 미스터리=단서/지식, 현판=각성/등급 등.\n"
            "2) entities: '주인공과 핵심 관계 인물 2~3명만'(character). attrs 는 위 attribute key 로. id 는 영문. "
            "조연·적대·조력 캐스트는 여기서 만들지 마라 — 아크 설계 단계에서 그 시점의 이야기 상태로부터 태어난다. "
            "말투(voice) 지정 금지 — 말투는 인물 설정에서 창발한다. 대신 각 인물의 profile(배경·성격·욕망·관계)을 충실히.\n"
            "3) world_rules(선택): 이 세계 핵심 규칙(있으면 flag 영문, keywords 한국어).\n"
            "4) timeline(선택): 예정된 상태 전이가 있으면 eff_from 으로(없으면 빈 배열).\n"
            "5) relations/seed_edges(선택): 시작 관계·작품별 관계 타입(rel_id 자유 라벨).\n"
            f"6) beats: 정확히 {target_chapters}개(chapter 1..{target_chapters}). 기승전결, 각 비트는 entities 에 인물 id.\n"
            "7) wiki_seeds(선택): 회수할 복선 plot_thread.\n"
            "8) style(선택): 이 장르에 맞는 집필 persona·끝맺음 정책(ending_hook)·문체 규칙. "
            "잔잔한 장르(로맨스/문예)는 soft, 연재 긴장형은 cliffhanger.\n"
            "회귀·부활·리젠·타임루프 세계면 allow_state_reversal:true. 설정은 시드에 맞게 신선하게. JSON 객체만 출력."
        )

    def _user(self, seed: ProjectSeed) -> str:
        return (f"[시드]\n장르: {seed.genre}\n톤: {seed.tone or '(미지정 — 이 장르에 맞는 톤을 창작해 tone 으로 제시)'}\n전제: {seed.premise}\n"
                f"주인공 힌트: {seed.protagonist_hint or '(자유)'}\n목표 회차수: {seed.target_chapters}\n"
                f"제목 힌트: {seed.title or '(자유 창작)'}\n\n스키마 예시(형식만 참고, 내용은 새로):\n{_SCHEMA_HINT}")

    def generate(self, seed: ProjectSeed, _retry: bool = True) -> WorldConfig:
        msg = [{"role": "system", "content": self._system(seed.target_chapters)},
               {"role": "user", "content": self._user(seed)}]
        raw = self.provider.chat_json(msg, temperature=0.6, max_tokens=9000)
        try:
            world = WorldConfig.model_validate(raw)
        except ValidationError as e:
            try:
                fix = self.provider.chat_json(
                    [{"role": "system", "content": "다음 JSON을 스키마에 맞게 교정해 유효한 객체만 출력."},
                     {"role": "user", "content": f"[오류]\n{e}\n[원본]\n{json.dumps(raw, ensure_ascii=False)}\n"
                      f"[스키마]\n{_SCHEMA_HINT}"}],
                    temperature=0.0, max_tokens=9000)
                world = WorldConfig.model_validate(fix)
            except (ValidationError, ValueError):
                if _retry:                       # 교정 재시도도 실패 → 전체 1회 재생성(일시적 출력 불량 흡수)
                    return self.generate(seed, _retry=False)
                raise
        return self._normalize(world, seed)

    @staticmethod
    def _normalize(world: WorldConfig, seed: ProjectSeed) -> WorldConfig:
        if seed.title and not world.title:
            world.title = seed.title
        world.genre = world.genre or seed.genre
        world.tone = world.tone or seed.tone
        # persona 를 작품 장르로(현대 판타지 기본값 잔재 제거 — 로맨스 작품은 로맨스 작가 persona 로 집필)
        if world.genre and "한국 웹소설 작가" in world.style.system_persona:
            world.style.system_persona = world.style.system_persona.replace(
                "한국 웹소설 작가", f"한국 {world.genre} 웹소설 작가")
        # 생애주기 선언 새니타이즈: 출연진 과반이 '시작값'으로 갖는 상태가 terminal/irreversible 로 선언되면
        # 전원이 1화부터 '제거 상태' → 영구 에스컬레이션(파일럿 실측 결함). 의미 오해(none=평범한 초기값)를 결정론 제거.
        for a in world.attributes:
            if a.kind in ("state", "status") and (a.terminal or a.irreversible):
                holders = [e for e in world.entities if a.key in e.attrs]
                if len(holders) >= 2:
                    from collections import Counter
                    counts = Counter(str(e.attrs[a.key]) for e in holders)
                    for v, n in counts.items():
                        if n * 2 >= len(holders):   # 과반 시작값 → 제거/비가역일 수 없음
                            if v in a.terminal:
                                a.terminal = [t for t in a.terminal if t != v]
                            if v in a.irreversible:
                                a.irreversible = [t for t in a.irreversible if t != v]
        # 비트 번호 정렬·재부여
        world.beats.sort(key=lambda b: b.chapter)
        for i, b in enumerate(world.beats, start=1):
            b.chapter = i
        # 엔티티 attrs 에서 정의되지 않은 key 제거(검증 안전)
        keys = {a.key for a in world.attributes}
        for e in world.entities:
            e.attrs = {k: v for k, v in e.attrs.items() if k in keys}
        return world
