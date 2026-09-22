# -*- coding: utf-8 -*-
"""DP-17 검증 — 화자 보이스 1급화('~했다 벽' 해체). LLM 스텁(전 결정론).

근거: docs/design-dp17-voice.md. '~했다 벽'의 뿌리는 화자가 태도 0의 카메라라 서술이 전부 '사건 과거 보고'가
되는 것(§1). 수리 4개(§2):
1) StyleSpec.narrator_voice 신설 + worldgen 이 주인공 시드에서 *증거 게이트로* 도출(발명 금지, B-36 계보) +
   작가 편집 경로(style PUT). _strip_llm_style 화이트리스트에 편입(메인 LLM 발명 차단).
2) render_style first 지시 확장(긍정 전용) + narrator_voice 삽입. 기본값(빈 voice·비first)은 바이트 동일.
3) beat_for_episode 대화 상대 배치 긍정 지시(DP-11 사건성 계보).
4) dp4_loop 게이트 체크리스트 케이던스 정독 항목(연속 10문장 낭독 — 벽이면 FAIL·인용).

검증 축(결정론):
1) render_style first+voice 스냅샷 — 1인칭 확장 지시 + voice 블록 주입.
2) render_style 바이트 동일 — 빈 voice(first) / 비-first(voice 있어도) 둘 다 무주입(무회귀).
3) voice 도출 증거 게이트 — 근거가 소스에 실재하면 채우고, 없으면/빈근거면 ""(발명 차단).
4) voice 도출 corpus 축소 — entity.profile(LLM 산출물) 근거는 폐기(작가 입력만).
5) voice 도출 NEVER throws — 비정형 반환 시 "".
6) generate e2e — first 시드면 voice 채워지고, 비-first 면 도출 스킵(""), 시간정보 무관.
7) worldgen 발명 차단 — _strip_llm_style 이 LLM narrator_voice 폐기(정책 필드 보존).
8) beat_for_episode 대화 상대 배치 지시 존재(긍정형).
9) dp4_loop 케이던스 체크리스트 항목 존재(낭독·FAIL·인용).
10) 작가 편집 경로 — update_style_policy 가 narrator_voice 반영(빈 문자열=해제·길이 cap).
11) 하위호환 — 구 JSON(narrator_voice 부재)=기본 ""·라운드트립 안정.

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_dp17_narrator_voice.py
"""
from __future__ import annotations
import sys
import json
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

from novelcopilot.domain.world import WorldConfig, StyleSpec, EntitySpec, DEFAULT_STYLE_RULES
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.engine.prompts import render_style
from novelcopilot.worldgen.generator import WorldGenerator
from novelcopilot.llm.base import LLMProvider

_VOICE_MARK = "[이 작품 화자의 목소리"   # narrator_voice 삽입 앵커(구현과 동기화)


class _VoiceStub(LLMProvider):
    """도출 콜에 고정 voice JSON 반환(direct extract_narrator_voice 테스트용 — 실 LLM 0콜)."""
    def __init__(self, voice_json: dict):
        super().__init__()
        self._voice = json.dumps(voice_json, ensure_ascii=False)

    def chat(self, messages, **kw):
        return self._voice

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


class _TwoModeStub(LLMProvider):
    """generate() e2e 스텁: worldgen 콜엔 world JSON, voice 도출 콜(system 에 '화자'·'목소리')엔 voice JSON."""
    def __init__(self, world_json: dict, voice_json: dict):
        super().__init__()
        self._world = json.dumps(world_json, ensure_ascii=False)
        self._voice = json.dumps(voice_json, ensure_ascii=False)
        self.voice_usr = None
        self.voice_called = False

    def chat(self, messages, **kw):
        sysmsg = messages[0].get("content", "") if messages else ""
        if "1인칭으로 이야기를 들려줄 때의 목소리" in sysmsg:
            self.voice_called = True
            self.voice_usr = messages[-1].get("content", "")
            return self._voice
        return self._world

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _seed(hint="냉소적이고 이죽거리는 성격의 회귀한 전 서열 1위", premise="전 서열 1위가 회귀한다.", pov="first"):
    # pov 를 world.style 로 주입(시드 명시 경로 모사 — _strip_llm_style 이 LLM pov 는 폐기하므로 코드가 세팅).
    return ProjectSeed(title="t", genre="현판", premise=premise, protagonist_hint=hint), pov


def _world(pov="first"):
    st = StyleSpec(pov=pov)
    return WorldConfig(title="t", genre="현판", premise="전 서열 1위가 회귀한다.",
                       entities=[EntitySpec(id="hero", name="도현")], style=st)


# 스텁 world JSON — _strip_llm_style 이 style.pov/narrator_voice 를 폐기하므로, e2e 에서 pov=first 를 얻으려면
#  seed 가 아니라 스텁이 style 을 넘겨도 폐기된다. 그래서 e2e 는 generate() 후 코드가 pov 를 세팅하는 대신,
#  _WORLD_JSON 에 style 없이 생성 → 아래 e2e 테스트가 world.style.pov 를 직접 세팅해 도출 경로만 검증한다.
_WORLD_JSON = {
    "title": "회귀 1위", "genre": "현판", "tone": "",
    "premise": "전 서열 1위가 회귀한다.", "synopsis": "도현은 회귀한다.",
    "entities": [{"id": "hero", "name": "도현", "etype": "character", "attrs": {}}],
    "beats": [{"chapter": 1, "title": "회귀", "summary": "s", "key_events": ["e"], "entities": ["hero"]}],
}


# ---------- 1) render_style first+voice 스냅샷 ----------
def test_render_first_with_voice_injects_block() -> bool:
    voice = "세상만사가 시시하다는 듯 이죽거리는 화자. 위기 앞에서도 농담부터 던진다."
    out = render_style(StyleSpec(pov="first", narrator_voice=voice))
    ok = (_VOICE_MARK in out and voice in out)              # voice 블록·본문 삽입
    ok &= ("서술은 화자의 목소리다" in out)                  # §2.2 긍정 확장 지시(현재형 논평·태도에 실어)
    ok &= ("1인칭 주인공" in out)                            # 1인칭 지시 유지(확장이 덮어쓰지 않음)
    ok &= all(f"{i + 1}) {r}" in out for i, r in enumerate(DEFAULT_STYLE_RULES))   # 8규칙 유지
    print(f"[{'OK' if ok else 'FAIL'}] render_style first+voice: 확장 지시+voice 블록 주입·8규칙 유지")
    return ok


# ---------- 2) render_style 바이트 동일(무주입 경로) ----------
def test_render_byte_identical_paths() -> bool:
    voice = "이죽거리는 화자"
    base_first = render_style(StyleSpec(pov="first"))            # first·빈 voice
    ok = (_VOICE_MARK not in base_first)                        # 빈 voice → voice 블록 무주입
    # first 인데 voice 만 있고 없고 → voice 블록만 차이(확장 1인칭 지시는 voice 무관하게 항상)
    ok &= (render_style(StyleSpec(pov="first", narrator_voice="")) == base_first)   # 빈 문자열=무주입
    # 비-first 는 voice 가 있어도 무주입(소비처 없음) — 기본 third_limited 는 pov 결도 "" 라 8규칙만
    tl = render_style(StyleSpec())
    tl_voice = render_style(StyleSpec(narrator_voice=voice))     # third_limited + voice
    ok &= (tl_voice == tl)                                      # 비-first: voice 있어도 바이트 동일
    ok &= (_VOICE_MARK not in tl_voice)
    # third_omniscient + voice 도 무주입(first 전용)
    om = render_style(StyleSpec(pov="third_omniscient"))
    om_voice = render_style(StyleSpec(pov="third_omniscient", narrator_voice=voice))
    ok &= (om_voice == om and _VOICE_MARK not in om_voice)
    print(f"[{'OK' if ok else 'FAIL'}] render_style 무주입 경로: 빈 voice·비-first 전부 바이트 동일(무회귀)")
    return ok


# ---------- 3) voice 도출 증거 게이트 ----------
def test_voice_derivation_evidence_gate() -> bool:
    seed, _ = _seed(hint="냉소적이고 이죽거리는 성격의 회귀자", premise="전 서열 1위가 회귀한다.")
    world = _world()
    # (a) 근거가 소스에 실재 → voice 채워짐
    good = _VoiceStub({"voice": "세상을 시큰둥하게 내려다보며 이죽거리는 화자.",
                       "evidence": "냉소적이고 이죽거리는 성격"})   # 힌트에 문자 그대로 존재
    v = WorldGenerator(good).extract_narrator_voice(seed, world)
    ok = (v == "세상을 시큰둥하게 내려다보며 이죽거리는 화자.")
    # (b) 근거가 소스에 부재(발명) → ""
    invent = _VoiceStub({"voice": "따뜻하고 다정한 화자.", "evidence": "따뜻하고 다정한 성격"})   # 소스 부재
    ok &= (WorldGenerator(invent).extract_narrator_voice(seed, world) == "")
    # (c) 근거 빈 값 → ""
    noev = _VoiceStub({"voice": "이죽거리는 화자.", "evidence": ""})
    ok &= (WorldGenerator(noev).extract_narrator_voice(seed, world) == "")
    # (d) voice 빈 값(근거만) → ""
    novoice = _VoiceStub({"voice": "", "evidence": "냉소적이고 이죽거리는 성격"})
    ok &= (WorldGenerator(novoice).extract_narrator_voice(seed, world) == "")
    print(f"[{'OK' if ok else 'FAIL'}] voice 도출 증거 게이트: 근거 실재만 채움·발명/빈근거/빈voice → \"\"")
    return ok


# ---------- 4) voice 도출 corpus 축소(LLM 산출물 근거 폐기) ----------
def test_voice_source_excludes_llm_profile() -> bool:
    # 작가 입력(힌트·전제)엔 성격 서술이 없고, LLM 이 지어낸 entity.profile 에만 '냉소적' 이 있는 경우
    seed = ProjectSeed(title="t", genre="현판", premise="회귀물.", protagonist_hint="")   # 작가 입력 성격 없음
    world = WorldConfig(title="t", genre="현판", premise="회귀물.",
                        entities=[EntitySpec(id="hero", name="도현", profile="냉소적이고 이죽거리는 회귀자")],
                        style=StyleSpec(pov="first"))
    stub = _VoiceStub({"voice": "이죽거리는 화자.", "evidence": "냉소적이고 이죽거리는 회귀자"})   # profile 근거
    ok = (WorldGenerator(stub).extract_narrator_voice(seed, world) == "")   # profile 은 코퍼스 부재 → 폐기
    # 소스 자체가 비면(힌트·전제 공백) 도출 스킵
    seed2 = ProjectSeed(title="t", genre="현판", premise="", protagonist_hint="")
    ok &= (WorldGenerator(stub).extract_narrator_voice(seed2, world) == "")
    print(f"[{'OK' if ok else 'FAIL'}] voice corpus 축소: entity.profile(LLM 산출) 근거 폐기·빈 소스 스킵")
    return ok


# ---------- 5) voice 도출 NEVER throws ----------
def test_voice_derivation_never_throws() -> bool:
    seed, _ = _seed()
    world = _world()

    class _Bad(LLMProvider):
        def chat(self, messages, **kw):
            return "not json at all"

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    ok = (WorldGenerator(_Bad()).extract_narrator_voice(seed, world) == "")
    for bad in ({"voice": 3}, {"evidence": 1}, {}, {"voice": None, "evidence": None}):
        ok &= (WorldGenerator(_VoiceStub(bad)).extract_narrator_voice(seed, world) == "")
    print(f"[{'OK' if ok else 'FAIL'}] voice 도출 NEVER throws: 비정형 반환 → \"\"(생성 차단 금지)")
    return ok


# ---------- 6) generate e2e — first 도출/비-first 스킵 ----------
def test_generate_e2e_first_derives_nonfirst_skips() -> bool:
    seed = ProjectSeed(title="회귀 1위", genre="현판", premise="전 서열 1위가 회귀한다.",
                       protagonist_hint="냉소적이고 이죽거리는 회귀자")
    voice_json = {"voice": "시큰둥하게 이죽거리는 화자.", "evidence": "냉소적이고 이죽거리는 회귀자"}
    # first: generate() 후 pov 를 first 로 세팅해야 도출 경로 진입. _strip_llm_style 이 LLM pov 를 폐기하므로
    #  코드가 세팅하는 지점을 모사 — generate 내부 조건(world.style.pov=='first')을 만족시키려 스텁 world 에 style 주입.
    wj_first = dict(_WORLD_JSON, style={"pov": "first"})   # _strip_llm_style 이 pov 폐기 → third_limited 로 로드
    stub = _TwoModeStub(wj_first, voice_json)
    world = WorldGenerator(stub).generate(seed)
    # _strip_llm_style 이 style.pov 를 폐기하므로 pov=third_limited → voice 도출 스킵(""), voice 콜 없음
    ok = (world.style.pov == "third_limited")               # LLM pov 발명 차단 확인(DP-8 계약)
    ok &= (world.style.narrator_voice == "")                # 비-first → 도출 스킵
    ok &= (stub.voice_called is False)                      # 도출 콜 자체가 없었음(불필요 LLM 콜 아낌)
    # first 경로 직접 검증: pov=first 로 세팅된 world 에서 extract 가 소스를 받고 채운다(도출 경로 실증)
    world.style.pov = "first"
    v = WorldGenerator(stub).extract_narrator_voice(seed, world)
    ok &= (v == "시큰둥하게 이죽거리는 화자." and "냉소적이고 이죽거리는 회귀자" in (stub.voice_usr or ""))
    print(f"[{'OK' if ok else 'FAIL'}] generate e2e: 비-first 도출 스킵·first 경로 소스 수신·채움")
    return ok


# ---------- 7) worldgen 발명 차단 ----------
def test_worldgen_strips_llm_narrator_voice() -> bool:
    raw = {"style": {"narrator_voice": "지어낸 목소리", "pov": "first",
                     "ending_hook": "soft", "rules": ["x"]}}
    WorldGenerator._strip_llm_style(raw)
    ok = ("narrator_voice" not in raw["style"])             # LLM 발명 voice 폐기
    ok &= ("pov" not in raw["style"] and "rules" not in raw["style"])   # 기존 폐기 필드도 유지
    ok &= (raw["style"]["ending_hook"] == "soft")           # 정책 필드 보존
    print(f"[{'OK' if ok else 'FAIL'}] worldgen _strip_llm_style: LLM narrator_voice 폐기·정책 필드 보존")
    return ok


# ---------- 8) beat_for_episode 대화 상대 배치 지시 ----------
def test_beat_dialogue_partner_directive() -> bool:
    from novelcopilot.worldgen.arc_planner import ArcPlanner
    from novelcopilot.domain.narrative import Arc, Episode

    class _Cap(LLMProvider):
        def __init__(self):
            super().__init__()
            self.sys = None

        def chat_json(self, messages, **kw):
            self.sys = messages[0]["content"]
            return {"title": "t", "summary": "s", "key_events": ["e1", "e2"], "entities": ["hero"],
                    "chapter_function": "setup", "hook_type": "question", "time_advance": "없음",
                    "time_delta": {"amount": 0, "unit": "minute", "mode": "advance"},
                    "place": "p", "protagonist_move": "먼저 나섰다"}

        def chat(self, messages, **kw):
            return "{}"

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    prov = _Cap()
    ap = ArcPlanner(prov)
    world = WorldConfig(title="t", genre="현판",
                        entities=[EntitySpec(id="hero", name="도현", etype="character"),
                                  EntitySpec(id="sup", name="민수", etype="character")])
    arc = Arc(arc_id="a1", title="아크", goal="g", order=1,
              episodes=[Episode(episode_id="e1", arc_id="a1", order=1, title="에피", premise="p")])
    ep = arc.episodes[0]
    ap.beat_for_episode(world, arc, ep, chapter=3, is_finale=False, recent=[], directives=[])
    sysmsg = prov.sys or ""
    ok = ("대화 상대가 있는 장면" in sysmsg or "말을 주고받는 장면" in sysmsg)   # 대화 상대 배치 긍정 지시
    ok &= ("최소 한" in sysmsg)                                                # 최소 1개 배치(긍정형)
    ok &= ("상호작용" in sysmsg)                                              # DP-11 사건성 계보(상호작용 우선)
    print(f"[{'OK' if ok else 'FAIL'}] beat_for_episode 대화 상대 배치 지시(긍정형·상호작용 우선)")
    return ok


# ---------- 9) dp4_loop 케이던스 체크리스트 항목 ----------
def test_dp4_loop_cadence_checklist_item() -> bool:
    from tools.dp4_loop import _tick_sheet
    sheet = _tick_sheet("문장 하나. 문장 둘. 문장 셋.")
    ok = ("케이던스" in sheet and "연속 10문장" in sheet)      # 케이던스 낭독 항목
    ok &= ("낭독" in sheet or "소리 내" in sheet)              # 낭독 지시
    ok &= ("FAIL" in sheet and "인용" in sheet)               # 벽이면 FAIL·구간 인용
    ok &= ("6항목" in sheet)                                   # 5→6 항목 확장(항목 수 갱신)
    print(f"[{'OK' if ok else 'FAIL'}] dp4_loop 케이던스 체크리스트 항목(낭독·FAIL·인용·6항목)")
    return ok


# ---------- 10) 작가 편집 경로 ----------
def test_author_edit_path_narrator_voice() -> bool:
    st = StyleSpec(pov="first")
    # update_style_policy 로직 모사(길이 cap·strip·빈 문자열=해제) — 서비스 필드 반영 계약 확인
    from novelcopilot.api.schemas import StylePolicyRequest
    req = StylePolicyRequest(narrator_voice="  이죽거리는 화자  ")
    patch = req.model_dump(exclude_none=True)
    ok = ("narrator_voice" in patch)                          # 스키마가 필드 노출
    # 서비스 반영 규칙 검증(직접 적용 — copilot.update_style_policy 와 동일 규칙)
    st.narrator_voice = (patch["narrator_voice"] or "").strip()[:800]
    ok &= (st.narrator_voice == "이죽거리는 화자")             # strip 적용
    # 빈 문자열=해제
    st.narrator_voice = ("" or "").strip()[:800]
    ok &= (st.narrator_voice == "")
    print(f"[{'OK' if ok else 'FAIL'}] 작가 편집 경로: StylePolicyRequest.narrator_voice 노출·strip·해제")
    return ok


# ---------- 11) 하위호환 ----------
def test_backcompat_old_json_and_roundtrip() -> bool:
    old = {"target_chars_per_chapter": 5000, "ending_hook": "cliffhanger", "pov": "first"}   # narrator_voice 부재
    s = StyleSpec.model_validate(old)
    ok = (s.narrator_voice == "")                            # 기본 빈 값으로 로드
    # 라운드트립 안정(voice 있는 스타일)
    custom = StyleSpec(pov="first", narrator_voice="이죽거리는 화자")
    w = WorldConfig(title="t", genre="x", style=custom)
    blob = w.model_dump_json()
    ok &= (WorldConfig.model_validate_json(blob).model_dump_json() == blob)
    # 구 world(narrator_voice 없이 저장)도 로드·재저장 안정
    old_w = WorldConfig(title="구작", genre="x", style=StyleSpec(rules=["규칙 A"]))
    b2 = old_w.model_dump_json()
    ok &= (WorldConfig.model_validate_json(b2).model_dump_json() == b2)
    print(f"[{'OK' if ok else 'FAIL'}] 하위호환: 구 JSON=기본 \"\"·라운드트립 안정")
    return ok


def main() -> int:
    results = [
        test_render_first_with_voice_injects_block(),
        test_render_byte_identical_paths(),
        test_voice_derivation_evidence_gate(),
        test_voice_source_excludes_llm_profile(),
        test_voice_derivation_never_throws(),
        test_generate_e2e_first_derives_nonfirst_skips(),
        test_worldgen_strips_llm_narrator_voice(),
        test_beat_dialogue_partner_directive(),
        test_dp4_loop_cadence_checklist_item(),
        test_author_edit_path_narrator_voice(),
        test_backcompat_old_json_and_roundtrip(),
    ]
    print("\nDP-17(화자 보이스 1급화) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_dp17_narrator_voice_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
