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
  "genre_contract": {                   // 이 장르/작품의 '정체성'(설계·집필·독자평가가 공유할 서술 컨텍스트)
    "pleasure_engine": "이 작품의 전제·톤에서 도출한, 독자가 계속 읽게 만드는 핵심 쾌감(이 작품 고유의 동력을 한 문장으로)",
    "reader_expectations": ["독자가 기대하는 것 3~5개"],
    "vocabulary_tone": "이 장르다운 어휘·톤(로판이 SF 용어로 새지 않게)",
    "premise_asset": "이 작품의 핵심 동력 전제와 그 역할(예: '십 년 잠입'은 단번에 소모할 게 아니라 길게 가는 장기 자산)"
  },
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
            "이 작품의 전제·갈등·진전 방식에서 '실제로 변화를 추적할 가치가 있는' 축만 도출해 신선하게 설계하라:\n"
            "1) attributes: 변하면 안 되거나 변화를 추적해야 할 축 2개 이상. 종류 자유(key 는 영문 snake_case):\n"
            "   - categorical(통제어휘 vocab + mutable): 눈색·소속·진영·신분 등\n"
            "   - numeric(monotonic 은 '정말 단조일 때만'; 오르내리면 생략): 등급·내공·호감 등\n"
            "   - state(생애주기): 사망·각성·정체발각·결혼 등 상태 전이. states + irreversible(되돌릴 수 없는 값) "
            "+ terminal(등장불가='서사에서 영구히 제거됨' — 인물이 다시 등장할 수 없게 되는 값만; 초기값·기본값·평범한 진행 상태는 terminal 이 아니다) 지정.\n"
            "   ※ 어떤 축이 중요한지는 시드의 전제·갈등·진전 방식에서 직접 도출하라. 이 작품의 긴장이 무엇을 두고 움직이는지 "
            "(관계·신분·지식·자원·내적 상태·역량 등 무엇이든)를 보고, 그 변화를 가장 잘 추적할 축을 골라라.\n"
            "2) entities: '주인공과 핵심 관계 인물 2~3명만'(character). attrs 는 위 attribute key 로. id 는 영문. "
            "조연·적대·조력 캐스트는 여기서 만들지 마라 — 아크 설계 단계에서 그 시점의 이야기 상태로부터 태어난다. "
            "말투(voice) 지정 금지 — 말투는 인물 설정에서 창발한다. 대신 각 인물의 profile(배경·성격·욕망·관계)을 충실히.\n"
            "3) world_rules(선택): 이 세계 핵심 규칙(있으면 flag 영문, keywords 한국어).\n"
            "4) timeline(선택): 예정된 상태 전이가 있으면 eff_from 으로(없으면 빈 배열).\n"
            "5) relations/seed_edges(선택): 시작 관계·작품별 관계 타입(rel_id 자유 라벨).\n"
            "6) beats: 초반 전개의 핵심 비트 3~5개만(chapter 1부터 순서대로). 나머지 회차는 연재하며 자동 설계되니 "
            "여기서 전부 나열하지 마라. 각 비트는 entities 에 인물 id.\n"
            "7) wiki_seeds(선택): 회수할 복선 plot_thread.\n"
            "8) style(선택): 이 장르에 맞는 집필 persona·끝맺음 정책(ending_hook)·문체 규칙. "
            "잔잔한 장르(로맨스/문예)는 soft, 연재 긴장형은 cliffhanger.\n"
            "9) genre_contract: 이 장르/작품의 '정체성'(쾌감 엔진·독자 기대·어휘 톤·핵심 동력 전제). "
            "설계·집필·독자평가가 같은 정체성을 공유하게 하는 서술 정보다(강제 규칙 아님).\n"
            "회귀·부활·리젠·타임루프 세계면 allow_state_reversal:true. 설정은 시드에 맞게 신선하게. JSON 객체만 출력."
        )

    def obsession(self, seed: ProjectSeed) -> dict:
        """집착 벡터 추출(prewrite 풍부함 연구 — 최상단 '헌법'). 세계를 '균등 슬롯 채우기'가 아니라
        하나의 기이하고 불편한 주제적 집착에서 *편중되게* 파생시켜 평균회귀(mode collapse)를 깬다.
        Egri 전제([지배형질→원인→귀결])+McKee counter_idea+감각렌즈(구체물)로 외화. NEVER throws."""
        try:
            sys = ("너는 웹소설의 '주제적 집착'을 짚는 날카로운 평론가다. 주어진 시드에서 이 이야기가 *정말로·내장으로* "
                   "무엇에 대한 것인지 — 가장 선명하고 가장 작품다운 명제 하나를 뽑아라(이 작품의 톤이 어둡든 잔잔하든 따뜻하든 그 결을 따라가라). 무난한 요약 금지. "
                   "① obsession_vector: Egri 식 전제([지배 형질]→[원인]→[귀결])로 압축한 단 하나의 집착 명제. "
                   "② counter_idea: 그것을 뒤집는 반대 사상(주인공이 이 집착과 싸우는 축). "
                   "③ sensory_lens: 그 집착이 가장 날카롭게 드러나는 '감각 렌즈' 3~4개 — 추상 금지, 이 작품의 세계와 톤에 어울리는 손에 잡히는 구체물만. "
                   '{"obsession_vector":"...","counter_idea":"...","sensory_lens":["...","...","..."]} JSON만.')
            usr = (f"[시드]\n장르: {seed.genre}\n톤: {seed.tone}\n전제: {seed.premise}\n"
                   f"주인공 힌트: {seed.protagonist_hint}\n")
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.7)
            ov = (d.get("obsession_vector") or "").strip()
            if not ov:
                return {}
            return {"obsession_vector": ov, "counter_idea": (d.get("counter_idea") or "").strip(),
                    "sensory_lens": [s for s in (d.get("sensory_lens") or []) if (s or "").strip()][:4]}
        except Exception:
            return {}

    def _obsession_block(self, obs: dict | None) -> str:
        if not obs or not obs.get("obsession_vector"):
            return ""
        return (
            "[이 세계의 최상단 헌법 — 집착(여기서 모든 것을 편중되게 파생하라)]\n"
            f"집착: {obs['obsession_vector']}\n"
            f"반대 사상(주인공이 싸우는 축): {obs.get('counter_idea', '')}\n"
            f"이 집착이 드러나는 감각 렌즈(구체물): {obs.get('sensory_lens', [])}\n"
            "※ 균등하게 채우지 마라. 이 집착이 가장 날카롭게 드러나는 영역(감각 렌즈)을 비정상적으로 깊게 파고 나머지는 얇아도 된다. "
            "기성 장르의 간판어·관용 설정에 기대지 말고, 이 작품 집착의 고유한 어휘와 구체물로 모든 설정을 직접 빚어내라 — "
            "attributes·entities·world_rules·genre_contract 가 전부 이 한 집착에서 흘러나오게. 추상 대신 감각 렌즈의 구체물로 못박아라.\n\n")

    def weird(self, world: WorldConfig, obs: dict | None = None) -> WorldConfig:
        """R-3 안티-클리셰 적대 weirding(mode collapse 후처리 차단). 생성된 세계에서 '이 장르의 가장 전형적인
        디폴트'(간판어·뻔한 인물·예측 규칙)를 짚어 작품 집착에 맞게 *구체·감각·비자명*하게 비튼다. 구조(인물 id·속성축)는
        보존하고 프로즈 필드만 surgical override. NEVER throws(실패 시 원본 반환). 인물 *추가/삭제 안 함*(weird=재작성)."""
        try:
            ob = (obs or {}).get("obsession_vector") or world.obsession_vector
            snap = {"synopsis": world.synopsis,
                    "entities": [{"id": e.id, "name": e.name, "profile": e.profile} for e in world.entities],
                    "world_rules": [r.text for r in world.world_rules],
                    "genre_contract": (world.genre_contract.model_dump() if world.genre_contract else None)}
            sys = ("너는 클리셰 사냥꾼 편집자다. 아래 세계관에서 '이 장르의 가장 전형적인 디폴트'(간판어·뻔한 인물 설정·예측 가능한 "
                   "규칙·추상적 쾌감 서술)를 골라 작품의 집착에 맞게 *구체적·감각적·비자명*하게 다시 써라. 겉만 바꾸지 말고 "
                   "디폴트를 비틀되 인물 id·이름·속성 구조는 유지(인물 추가/삭제/개명 금지 — profile 내용만 비튼다). 수정한 필드만 같은 키로 반환 — "
                   'synopsis(문자열), entities([{id, profile}]), world_rules(문자열 배열 전체), genre_contract(객체). JSON만.')
            usr = f"[작품의 집착]{ob}\n[현재 세계 — 진부한 부분을 비틀 대상]\n{json.dumps(snap, ensure_ascii=False)}"
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.8)
            if (d.get("synopsis") or "").strip():
                world.synopsis = d["synopsis"].strip()
            patch = {x.get("id"): x for x in (d.get("entities") or []) if x.get("id")}
            for e in world.entities:
                p = patch.get(e.id)
                if p and (p.get("profile") or "").strip():
                    e.profile = p["profile"].strip()   # 이름은 보존(개명 시 aliases·타 인물 profile 잔존명·캐논 substring 탐지 누수 — critic major)
            nr = [s.strip() for s in (d.get("world_rules") or []) if (s or "").strip()]
            for i, r in enumerate(world.world_rules):
                if i < len(nr):
                    r.text = nr[i]
            gc = d.get("genre_contract")
            if gc and world.genre_contract:
                for k in ("pleasure_engine", "vocabulary_tone", "premise_asset"):
                    if (gc.get(k) or "").strip():
                        setattr(world.genre_contract, k, gc[k].strip())
                if gc.get("reader_expectations"):
                    world.genre_contract.reader_expectations = [x for x in gc["reader_expectations"] if (x or "").strip()][:6]
            return world
        except Exception:
            return world

    def _user(self, seed: ProjectSeed, obs: dict | None = None) -> str:
        return (self._obsession_block(obs) +
                f"[시드]\n장르: {seed.genre}\n톤: {seed.tone or '(미지정 — 이 장르에 맞는 톤을 창작해 tone 으로 제시)'}\n전제: {seed.premise}\n"
                f"주인공 힌트: {seed.protagonist_hint or '(자유)'}\n목표 회차수: {seed.target_chapters}\n"
                f"제목 힌트: {seed.title or '(자유 창작)'}\n\n스키마 예시(형식만 참고, 내용은 새로):\n{_SCHEMA_HINT}")

    def generate(self, seed: ProjectSeed, _retry: bool = True, obs: dict | None = None) -> WorldConfig:
        msg = [{"role": "system", "content": self._system(seed.target_chapters)},
               {"role": "user", "content": self._user(seed, obs)}]
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
                    return self.generate(seed, _retry=False, obs=obs)
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
