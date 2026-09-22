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
import logging

from pydantic import ValidationError
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..domain.world import WorldConfig, TimeAnchor
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
     "profile":"인물 설계서(공개·현재). 독자가 이 인물을 처음 만나 알 수 있는 것을 담는다: 배경·성격·지금 원하는 것·지금 겉으로 드러나 독자가 처음 보고 알아챌 수 있는 두려움·지금 이미 맺고 있는 관계. 말투 슬롯은 비워 둔다(말투는 이 설정에서 창발). 세계의 제도·조직 운영 방식은 world_rules 에 적는다",
     "arc_note":"(선택) 이 인물의 궤적. 설계 단계(아크·에피소드 계획)가 앞으로의 전개를 짤 때 참고한다: 앞으로 어떤 관계로 발전할 의도, 뒤에 가서야 드러날 면모, 결말로 가는 변화. 이 인물에게 실제로 그런 궤적이 있을 때만 채우고, 없으면 생략",
     "cardinality":{}}   // 선택: 이 인물의 관계 개수 상한(rel_id→최대수). 작품이 *명시*한 경우만 채운다 — 예 외동={"sibling_of":0}. 비면 {}(무제한)
  ],
  "relations": [],                      // 선택: 작품별 관계 타입(RelationSpec). rel_id 자유
  "seed_edges": [],                     // 선택: '시작 시점에 이미 사실인' 관계만(배경·기존 사이). eff_from=참이 된 회차. 형성될 관계 방향은 arc_note 로(설계가 참고, 본문이 성립시키면 엔진이 캐논 승격)
  "world_rules": [                      // 선택(비워도 됨·형태 예시일 뿐 내용은 이 작품에서 도출). 넣을 땐 이 형태(flag 영문 필수):
    {"rule_id":"night_market","text":"항구 시장은 해가 진 뒤에만 열린다.","flag":"day_market","keywords":["시장","항구"],"extract_hint":"낮에 시장이 서는 장면"}
    // 선택 필드 "table":{"키":"값",...} — 규칙이 '고정된 순서/등급 → 정해진 대상'의 열거 대응을 가질 때만(대응이 이야기 내내 불변인 경우). 일반 규칙은 생략
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
  "style": {                            // 선택: 회차 끝맺음 정책만(문체 규칙·집필 persona 는 코드 기본값 — 여기 넣지 않음)
    "ending_hook": "cliffhanger|soft|none — 잔잔한 장르는 soft 고려"
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
            "   - categorical(통제어휘 vocab): *변하지 않는 사실*(눈색·종족·성별 등)은 mutable 생략(기본 false=불변 캐논). "
            "*실제로 변할 수 있는 것*(소속·진영·신분 등)만 mutable:true. "
            "관계단계·호감처럼 *한 방향으로 깊어지는* 순서형 축은 vocab 을 진행 순서대로 나열하고 ordered:true 도 함께(회상·오추출이 캐논을 되감지 않게).\n"
            "   - numeric(monotonic 은 '정말 단조일 때만'; 오르내리면 생략): 등급·내공·호감 등\n"
            "   - state(생애주기): 사망·각성·정체발각·결혼 등 상태 전이. states + irreversible(되돌릴 수 없는 값) "
            "+ terminal(등장불가='서사에서 영구히 제거됨' — 인물이 다시 등장할 수 없게 되는 값만; 초기값·기본값·평범한 진행 상태는 terminal 이 아니다) 지정.\n"
            "   ※ 어떤 축이 중요한지는 시드의 전제·갈등·진전 방식에서 직접 도출하라. 이 작품의 긴장이 무엇을 두고 움직이는지 "
            "(관계·신분·지식·자원·내적 상태·역량 등 무엇이든)를 보고, 그 변화를 가장 잘 추적할 축을 골라라.\n"
            "2) entities: 작가가 인물 이름을 준 경우(아래 '[작가가 이미 지은 인물]')엔 그 인물 *전원*을 entities 로 등재하라 — "
            "수 제한 없음(작가 명명 인물이 몇 명이든 전부), 이름은 entities 의 name 에 *정확히 그대로*(발명·개명 금지), 명세의 역할·욕망은 profile 에 녹여라. "
            "시스템이 *새로 발명*하는 인물(character)만 '주인공과 핵심 관계 인물 2~3명' 범위로 절제하라. attrs 는 위 attribute key 로. id 는 영문. "
            "이름 없는 조연·적대·조력 캐스트는 여기서 만들지 마라 — 아크 설계 단계에서 그 시점의 이야기 상태로부터 태어난다. "
            "말투(voice) 슬롯은 비워 둔다(말투는 인물 설정에서 창발). 각 인물의 profile 에는 공개·현재 정체(배경·성격·지금 원하는 것·지금 드러나는 두려움·현재 관계)를 충실히 담고, 앞으로의 궤적·뒤에 드러날 면모는 arc_note 로 나눠 담아라.\n"
            "3) world_rules(선택): 이 세계 핵심 규칙(있으면 flag 영문, keywords 한국어). "
            "조직·제도·절차가 돌아가는 방식은 world_rules 규칙으로 적는다. "
            "규칙이 '고정된 순서/등급 → 정해진 대상'의 열거 대응을 가지면(대응이 이야기 내내 불변) table(키→값)로도 선언.\n"
            "4) timeline(선택): 예정된 상태 전이가 있으면 eff_from 으로(없으면 빈 배열).\n"
            "5) seed_edges(선택): **이야기 시작 시점에 이미 사실인 관계만**(전부터 알던 사이·가족·기존 구도 등 — 인물들이 이미 아는 관계). "
            "앞으로 *전개되며 형성될* 관계(만난 적 없는 둘이 나중에 동맹·연인·라이벌이 되는 등)는 seed_edges 에 넣지 마라 — "
            "그건 이야기로 펼쳐지고, 본문이 그 관계를 실제로 성립시키면 엔진이 그때 캐논으로 잡는다(미리 박지 않는다). "
            "그런 '의도된 관계 방향'은 해당 인물의 arc_note 에 서술하라(설계가 참고하는 궤적). seed_edge 의 eff_from 은 그 관계가 *참이 되는* 회차다(시작부터 참이면 1). "
            "relations 는 작품별 관계 타입(rel_id 자유 라벨).\n"
            "6) beats: 초반 전개의 핵심 비트 3~5개만(chapter 1부터 순서대로). 나머지 회차는 연재하며 자동 설계되니 "
            "여기서 전부 나열하지 마라. 각 비트는 entities 에 인물 id.\n"
            "7) wiki_seeds(선택): 회수할 복선 plot_thread.\n"
            "8) style(선택): 회차 끝맺음 정책(ending_hook)만. "
            "잔잔한 장르(로맨스/문예)는 soft, 연재 긴장형은 cliffhanger.\n"
            "9) genre_contract: 이 장르/작품의 '정체성'(쾌감 엔진·독자 기대·어휘 톤·핵심 동력 전제). "
            "설계·집필·독자평가가 같은 정체성을 공유하게 하는 서술 정보다(강제 규칙 아님).\n"
            "회귀·부활·리젠·타임루프 세계면 allow_state_reversal:true. 설정은 시드에 맞게 신선하게. JSON 객체만 출력."
        )

    @promptlog.stage("worldgen:obsession")
    def obsession(self, seed: ProjectSeed, skills_inject: str = "") -> dict:
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
                   f"주인공 힌트: {seed.protagonist_hint}\n") + (skills_inject or "")
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

    @promptlog.stage("worldgen:weird")
    def weird(self, world: WorldConfig, obs: dict | None = None, skills_inject: str = "") -> WorldConfig:
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
            usr = (f"[작품의 집착]{ob}\n[현재 세계 — 진부한 부분을 비틀 대상]\n"
                   f"{json.dumps(snap, ensure_ascii=False)}") + (skills_inject or "")
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

    def _user(self, seed: ProjectSeed, obs: dict | None = None, skills_inject: str = "", brief=None) -> str:
        cast = ""
        if brief is not None and getattr(brief, "characters", None):
            _ph = {"소년", "소녀", "그녀", "그", "주인공", "주연", "미정", "이름", "주인공이름"}
            # CB-1: 명명 인물 수 상한([:5]) 제거 — 작가가 지은 인물은 전원 등재한다(silent drop 금지).
            #   앙상블 작품(용병단·길드·팀물)은 명명 캐스트가 5명을 넘는 게 정상인데, 초과분이 경고도 기록도
            #   없이 잘려 세계관에서 증발하던 결함(2026-07-15 실측: 3인극 세계관). 과다 입력은 자르지 않고 드러낸다.
            named = [c for c in brief.characters
                     if (getattr(c, "name", "") or "").strip() and (c.name or "").strip() not in _ph]
            if len(named) > 10:
                logging.getLogger(__name__).warning(
                    "worldgen 명명 인물 %d명 — 상한 없이 전원 프롬프트 등재(CB-1)", len(named))
            if named:
                cast = ("\n[작가가 이미 지은 인물 — entities[].name 에 이 이름을 *정확히 그대로* 쓰고 발명·개명 금지]\n"
                        + "\n".join("- " + c.name
                                    + (f" ({c.role})" if (getattr(c, "role", "") or "").strip() else "")
                                    + (f": {c.want}" if (getattr(c, "want", "") or "").strip() else "")
                                    for c in named) + "\n")
        return (self._obsession_block(obs) + cast +
                f"[시드]\n장르: {seed.genre}\n톤: {seed.tone or '(미지정 — 이 장르에 맞는 톤을 창작해 tone 으로 제시)'}\n전제: {seed.premise}\n"
                f"주인공 힌트: {seed.protagonist_hint or '(자유)'}\n목표 회차수: {seed.target_chapters}\n"
                f"제목 힌트: {seed.title or '(자유 창작)'}\n\n스키마 예시(형식만 참고, 내용은 새로):\n{_SCHEMA_HINT}") + (skills_inject or "")

    @staticmethod
    def _strip_llm_style(raw) -> None:
        """ST-6 소스차단: worldgen LLM 이 style.rules·system_persona·author_style 를 장르 문안으로 통째
        대체하던 결함을 차단한다. 이 세 필드는 코드 SSOT(DEFAULT_STYLE_RULES·기본 persona)와 작가 오버레이
        슬롯이므로 LLM 산출을 파싱 전에 폐기 — 신규 작품이 코드 기본값을 그대로 상속한다(StyleSpec factory).
        스키마 힌트에서 이미 뺐지만 mode-collapse 로 모델이 여전히 채울 수 있어 파싱 지점에서도 무효화한다
        (스키마=1차·이 폐기=2차 소스차단). ending_hook·분량 등 정책 필드는 보존.

        DP-8: pov(서술 시점)도 폐기 대상에 편입 — 스키마 힌트에 없어도 mode-collapse 로 모델이 1인칭/전지를
        발명할 수 있다. 시점은 '시드 명시'로만 지정하는 작가 결정이지 worldgen 이 발명할 축이 아니므로(발명 금지),
        LLM 산출의 pov 는 폐기하고 코드 기본값(third_limited)을 상속시킨다.

        DP-17: narrator_voice(화자 보이스)도 폐기 대상 — 이 필드는 주인공 시드에서 *증거 게이트로 도출*하는
        전용 경로(extract_narrator_voice)로만 채운다(발명 금지). 메인 generate 콜의 LLM 산출 voice 는 근거 대조를
        거치지 않은 발명이므로 파싱 전 폐기하고, 증거 인용이 소스에 실재하는 도출분만 채워지게 한다(B-36 계보)."""
        if isinstance(raw, dict):
            st = raw.get("style")
            if isinstance(st, dict):
                for k in ("rules", "system_persona", "author_style", "pov", "narrator_voice"):
                    st.pop(k, None)

    @promptlog.stage("worldgen:world")
    def generate(self, seed: ProjectSeed, _retry: bool = True, obs: dict | None = None,
                 skills_inject: str = "", brief=None) -> WorldConfig:
        msg = [{"role": "system", "content": self._system(seed.target_chapters)},
               {"role": "user", "content": self._user(seed, obs, skills_inject, brief)}]
        raw = self.provider.chat_json(msg, temperature=0.6)
        self._strip_llm_style(raw)
        try:
            world = WorldConfig.model_validate(raw)
        except ValidationError as e:
            try:
                fix = self.provider.chat_json(
                    [{"role": "system", "content": "다음 JSON을 스키마에 맞게 교정해 유효한 객체만 출력."},
                     {"role": "user", "content": f"[오류]\n{e}\n[원본]\n{json.dumps(raw, ensure_ascii=False)}\n"
                      f"[스키마]\n{_SCHEMA_HINT}"}],
                    temperature=0.0)
                self._strip_llm_style(fix)
                world = WorldConfig.model_validate(fix)
            except (ValidationError, ValueError):
                if _retry:                       # 교정 재시도도 실패 → 전체 1회 재생성(일시적 출력 불량 흡수)
                    return self.generate(seed, _retry=False, obs=obs, skills_inject=skills_inject, brief=brief)
                raise
        world = self._normalize(world, seed)
        # DP-18: 시드가 명시한 서술 시점을 world.style.pov 로 배선한다(worldgen 발명분은 _strip_llm_style 이
        #  이미 폐기 → 여기 반영이 유일 SSOT). 작가 결정만 반영(발명 금지): seed.pov 가 빈 값이면 아무것도 하지
        #  않아 StyleSpec 기본값(third_limited)을 그대로 유지 = 구 시드/구 JSON 바이트 동일(하위호환). 이 배선이
        #  아래 first 분기(narrator_voice 도출)보다 *먼저* 실행돼야 도출이 정상 경로로 발화한다(사후 패치 스킵 해소).
        _seed_pov = (getattr(seed, "pov", "") or "").strip()
        if _seed_pov:
            world.style.pov = _seed_pov
        # B-36: 시간 앵커 배선 — 전제·브리프에 *명시된* 기간/기한/나이만 구조화(B-33 방어를 실효화).
        #  미확인이면 [] → B-33 무주입 하위호환(anchor_facts/extra_facts/time_facts 전부 빈 값, 프롬프트 바이트 동일).
        world.time_anchors = self.extract_time_anchors(seed, world, brief=brief, skills_inject=skills_inject)
        # DP-17: 화자 보이스 도출 — 주인공 시드에서 화자의 태도·말투를 *증거 게이트로* 도출(발명 금지, B-36 계보).
        #  first 시점(화자 정체성이 render_style 에 실제로 소비되는 경로)일 때만 도출한다 — 3인칭은 화자 보이스
        #  개념이 다르고 소비처가 없어 불필요한 LLM 콜을 아낀다. 근거 인용이 소스에 실재하지 않으면 "" → 무주입
        #  (프롬프트 바이트 동일 하위호환). 작가가 style PUT 으로 언제든 편집·설정 가능(도출은 초기값 제안).
        if getattr(world.style, "pov", "third_limited") == "first":
            world.style.narrator_voice = self.extract_narrator_voice(
                seed, world, brief=brief, skills_inject=skills_inject)
        return world

    # ---- B-36: 명시된 시간 기준점(기간/기한/나이) → TimeAnchor. B-33 소비경로(_plan_time_facts·anchor_facts) 실효화 ----
    _ANCHOR_SCHEMA = ('{"anchors":[{"label":"이 앵커의 짧은 이름","kind":"deadline|age",'
                      '"amount":숫자,"unit":"minute|hour|day|week|month|year","entity_id":"관련 인물 id(선택)",'
                      '"evidence":"이 앵커의 근거가 된 원문 구절을 그대로 인용"}]}')

    def _anchor_source(self, seed: ProjectSeed, world: WorldConfig, brief=None) -> str:
        """앵커 근거 대조에 쓰는 '작가가 실제로 진술한' 텍스트 코퍼스(evidence corpus).
        여기 문자 그대로 존재하지 않는 기간/기한/나이는 앵커로 만들지 않는다(발명 차단).
        B-36 corpus 축소: 코퍼스는 *작가 입력*(seed 전제·주인공 힌트·브리프)만 담는다 — world.premise/synopsis
        는 LLM 산출물이라 제외한다. LLM 이 시놉시스에 발명한 기한이 근거로 인정돼 앵커로 역류하는 경로를
        소스에서 차단(발명 금지·증거 강제 취지). world 인자는 호출부 호환 위해 유지하되 근거 대조엔 쓰지 않는다."""
        parts = [seed.premise or "", getattr(seed, "protagonist_hint", "") or ""]
        if brief is not None:
            for k in ("logline", "premise", "setting"):
                parts.append(getattr(brief, k, "") or "")
            for k in ("conflicts", "themes", "world_rules"):
                parts += [str(x) for x in (getattr(brief, k, None) or [])]
            for c in (getattr(brief, "characters", None) or []):
                parts += [getattr(c, "name", "") or "", getattr(c, "want", "") or ""]
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _norm(s: str) -> str:
        return "".join((s or "").split())   # 공백 제거 정규화(표기 토큰화 차 흡수 — '삼 년'≈'삼년')

    @promptlog.stage("worldgen:time_anchors")
    def extract_time_anchors(self, seed: ProjectSeed, world: WorldConfig, brief=None,
                             skills_inject: str = "") -> list[TimeAnchor]:
        """전제·브리프에 *문자 그대로 명시된* 시간 기준점만 TimeAnchor 로 구조화한다(B-36).

        B-33 은 시계 파생 산술(계약 만기 잔여·현재 나이)을 [확정 설정]·사건메뉴·비트 3레이어에 결정론 주입하도록
        배선했으나, time_anchors 를 채우는 코드가 0이라 방어가 비활성이었다 — 이 메서드가 그 소스를 잇는다.

        발명 금지(OV-3 증거 강제 재사용): 각 앵커는 근거(evidence=원문 인용) 필수. 근거가 소스 코퍼스에 문자 그대로
        존재하지 않으면 버린다 — 모델 자기보고가 아니라 *코드가 substring 대조*한다(거짓양성=발명은 차단, 거짓음성=누락은
        []=무주입으로 안전 하향). 명시 앵커 없으면 [](B-33 하위호환, 프롬프트 바이트 동일).

        genre-blind(WG-1): kind 는 결정론 산술 원시 2종(deadline/age)이지 장르 범주가 아니며(label 은 자유),
        예시는 중립·비능력 도메인(계약 기간·나이)만 든다. 무강제: 주입·판정은 B-33/판정기 0. NEVER throws(실패→[])."""
        src = self._anchor_source(seed, world, brief)
        if not src.strip():
            return []
        try:
            from ..engine.story_clock import UNIT_MINUTES
            sys = ("너는 주어진 작품 설정 텍스트에서 *문자 그대로 명시된* '시간 기준점'만 뽑아내는 추출기다. "
                   "추정·발명·계산 금지 — 텍스트에 실제로 적힌 기간/기한/나이만. 하나도 없으면 빈 배열. "
                   "각 항목엔 근거(evidence)로 원문에 나온 구절을 *그대로* 인용하라(숫자·표기 변형 금지). "
                   "근거를 댈 수 없으면 그 항목을 만들지 마라. 두 종류만: "
                   "deadline = 이야기 시작 시점부터 amount·unit 뒤에 도래하는 기한(예: 텍스트에 '3년 계약'이 있으면 amount=3, unit=year). "
                   "age = 어떤 인물의 시작 나이(예: 텍스트에 '열다섯 살'이 있으면 kind=age, amount=15). "
                   "unit 은 minute|hour|day|week|month|year 중 하나. JSON만.\n" + self._ANCHOR_SCHEMA)
            usr = f"[작품 설정 텍스트]\n{src}" + (skills_inject or "")   # 절단 전면 제거(2026-08-21): 소스 전문(2,000자 이후의 시간 기준점 미탐지 사각 소거)
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.0)
        except Exception:
            return []
        if not isinstance(d, dict):
            return []
        anchors = d.get("anchors")
        if not isinstance(anchors, list):   # 비정형 anchors(스칼라·bool·None) → [](NEVER throws 계약 준수 — for a in 1 폭발 차단)
            return []
        ids = {e.id for e in world.entities}
        src_norm = self._norm(src)
        out: list[TimeAnchor] = []
        for a in anchors:
            if not isinstance(a, dict):
                continue
            kind = (a.get("kind") or "").strip().lower()
            if kind not in ("deadline", "age"):   # 결정론 산술 원시만(자유 kind 금지 — 계산 불가값 폐기)
                continue
            ev = (a.get("evidence") or "").strip()
            if not ev or self._norm(ev) not in src_norm:   # OV-3: 근거가 소스에 실재해야(발명 차단, 코드 대조)
                continue
            try:
                amt = float(a.get("amount"))
            except (TypeError, ValueError):
                continue
            if amt <= 0:
                continue
            unit = (a.get("unit") or "year").strip().lower()
            if kind == "deadline" and unit not in UNIT_MINUTES:
                continue   # 미상 단위 deadline → 폐기(거짓 잔여 단정 회피 — B-33 null degrade 를 소스에서 선차단)
            eid = (a.get("entity_id") or "").strip()
            if eid and eid not in ids:
                eid = ""   # entity_id 는 감사·UI용(계산 불필요) — 실재 인물만
            label = (a.get("label") or "").strip() or ("기한" if kind == "deadline" else "나이")
            out.append(TimeAnchor(anchor_id=f"ta{len(out) + 1}", label=label, kind=kind,
                                  amount=amt, unit=("year" if kind == "age" else unit), entity_id=eid))
            if len(out) >= 6:   # 방어적 상한(설정 텍스트에서 6개 넘는 명시 앵커는 비정상)
                break
        return out

    # ---- DP-17: 화자 보이스 도출(주인공 시드 → narrator_voice). B-36 증거 게이트 패턴 재사용 ----
    # VS-1(2026-07-16): '말투'가 문장 형태(리듬·길이·종결) 지시로 번지지 않게 태도·시선으로 한정 —
    #   형태는 스타일 레이어 단독 소관(docs/issue-voice-style-separation-2026-07-16.md).
    _VOICE_SCHEMA = ('{"voice":"화자의 태도와 시선 — 무엇을 즐기고 무엇에 이죽거리는가 — 를 2~4문장으로. '
                     '주인공 성격 근거에서만 도출","evidence":"이 목소리의 근거가 된 주인공 묘사 구절을 원문 그대로 인용"}')

    @promptlog.stage("worldgen:narrator_voice")
    def extract_narrator_voice(self, seed: ProjectSeed, world: WorldConfig, brief=None,
                               skills_inject: str = "") -> str:
        """주인공 성격 시드에서 화자의 목소리(태도·시선·무엇을 즐기고 이죽거리는가)를 도출한다(DP-17).

        VC-1(2026-07-17) 점검: 이 도출은 스키마(_VOICE_SCHEMA voice 필드)·시스템 프롬프트 모두 '태도와 시선'만
        요구하고 특정 대사·캐치프레이즈 문자열 생성을 지시하지 않는다(narrator_voice.py 카드 ②의 옛 '예문 2개
        지어라'식 누수가 여기엔 없음). evidence 는 발명 차단용 근거 대조에만 쓰이고 도출값(voice)에 포함되지
        않는다 — 근거로 인용된 주인공 묘사 구절이 화자 컨텍스트로 새지 않는다. 즉 DP-17 은 이미 결·태도만 낳으며
        말버릇 문장의 정착·전파는 본문 회차 소관이다(카드와 동일한 절단선).

        '~했다 벽'의 뿌리는 화자가 태도 0의 카메라라는 것(design-dp17-voice.md §1). first 시점 서술이
        화자의 정체성으로 굴러가게, worldgen 이 주인공 시드에서 화자 보이스를 도출해 초기값으로 제안한다.

        발명 금지(B-36 계보·증거 강제): 도출된 voice 는 근거(evidence=주인공 묘사 원문 인용) 필수. 근거가
        소스 코퍼스(작가 입력 — 주인공 힌트·전제·브리프 인물)에 문자 그대로 존재하지 않으면 ""(무주입) —
        모델 자기보고가 아니라 *코드가 substring 대조*한다(거짓양성=발명 차단, 거짓음성=누락은 ""로 안전 하향,
        프롬프트 바이트 동일). 작가가 style PUT 으로 언제든 덮어쓴다(도출은 초기 제안일 뿐).

        genre-blind(voice 는 작품별 도출) · 긍정 전용(스키마·지시에 금지문·틱 이름 호명 0) · NEVER throws(실패→"")."""
        src = self._voice_source(seed, world, brief)
        if not src.strip():
            return ""
        try:
            sys = ("너는 주어진 주인공 묘사에서 이 인물이 '1인칭으로 이야기를 들려줄 때의 목소리'를 뽑아내는 도우미다. "
                   "그 인물의 성격·태도·처지에서 자연스럽게 나올 화자의 태도와 시선 — 무엇을 즐기고 무엇에 시큰둥하며 "
                   "어떤 결로 세상을 보는가 — 를 2~4문장으로 묘사하라. 화자가 무엇을 보고 어떻게 대하는가만 담아라 — "
                   "문장을 어떤 형태로 쓰는가는 작품의 문체 규칙이 따로 맡는다. 반드시 주어진 묘사에 실제로 적힌 성격에서만 도출하고, "
                   "없는 성격을 지어내지 마라. 근거(evidence)로 그 판단의 바탕이 된 묘사 구절을 원문에서 *그대로* 인용하라. "
                   "근거를 댈 수 없으면 voice 를 비워라. JSON만.\n" + self._VOICE_SCHEMA)
            usr = f"[주인공 묘사]\n{src}" + (skills_inject or "")   # 절단 전면 제거(2026-08-21): 소스 전문(증거 대조도 원래 전문 기준)
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.4)
        except Exception:
            return ""
        if not isinstance(d, dict):
            return ""
        v, e = d.get("voice"), d.get("evidence")
        voice = v.strip() if isinstance(v, str) else ""   # 비문자열(int/None/list) → ""(NEVER throws)
        ev = e.strip() if isinstance(e, str) else ""
        # 증거 게이트: 근거가 소스에 실재해야(발명 차단, 코드 대조 — B-36 OV-3 와 동일 원리)
        if not voice or not ev or self._norm(ev) not in self._norm(src):
            return ""
        return voice[:800]   # 매 draft 헤더 주입이라 방어적 길이 cap(author_style 2000·규칙 대비 짧게)

    def _voice_source(self, seed: ProjectSeed, world: WorldConfig, brief=None) -> str:
        """화자 보이스 근거 대조에 쓰는 '작가가 실제로 진술한' 주인공 묘사 코퍼스(evidence corpus).
        여기 문자 그대로 존재하지 않는 성격은 voice 로 만들지 않는다(발명 차단). B-36 _anchor_source 와 동형 —
        작가 입력(주인공 힌트·전제·브리프 인물 want)만 담고 world.synopsis/entity.profile(LLM 산출물)은 제외한다
        (LLM 이 프로필에 발명한 성격이 근거로 역류하는 경로를 소스에서 차단). 주인공 힌트를 앞세운다(voice 의 1차 근거)."""
        parts = [getattr(seed, "protagonist_hint", "") or "", seed.premise or ""]
        if brief is not None:
            parts.append(getattr(brief, "logline", "") or "")
            for c in (getattr(brief, "characters", None) or []):
                parts += [getattr(c, "name", "") or "", getattr(c, "want", "") or "",
                          getattr(c, "role", "") or ""]
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _normalize(world: WorldConfig, seed: ProjectSeed) -> WorldConfig:
        if seed.title and not world.title:
            world.title = seed.title
        world.genre = world.genre or seed.genre
        world.tone = world.tone or seed.tone
        # ST-6: persona 는 코드 기본값(장르 중립)을 그대로 유지 — 과거의 '장르 문안 하드 대체'(persona 를 작품
        #   장르로 치환)는 genre-blind 강화 방향에 따라 제거했다. 장르 문체 뉘앙스는 author_style(작가 오버레이) 몫.
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
        # XR-11(cross-review/007 §5): 모델이 제안한 추적 속성의 안전 기본값 — auto_commit 미지정이면
        #   non_binding 착지(동적 감지가 작가 확정 전에 binding 캐논으로 굳지 않게 — 안전이 opt-in 이던
        #   공백 봉합). 외적 관찰 축(소지·생사·소속 등)을 binding 으로 올리는 것은 작가 몫(PATCH·설정집
        #   토글·tier_report 검토 큐). 프롬프트로 분류를 시키지 않는다(감사 반려 — 코드 기본값+작가 선언).
        #   이 함수는 worldgen 산출에만 돌므로 구 JSON 로드는 무변경(미선언 ""=현행 유지·하위호환).
        for a in world.attributes:
            if not (getattr(a, "auto_commit", "") or ""):
                a.auto_commit = "non_binding"
        # 비트 번호 정렬·재부여
        world.beats.sort(key=lambda b: b.chapter)
        for i, b in enumerate(world.beats, start=1):
            b.chapter = i
        # 엔티티 attrs 에서 정의되지 않은 key 제거(검증 안전)
        keys = {a.key for a in world.attributes}
        for e in world.entities:
            e.attrs = {k: v for k, v in e.attrs.items() if k in keys}
        return world
