# -*- coding: utf-8 -*-
"""CE-1 검증 — 비용 엔지니어링(ⓐ Anthropic 프롬프트 캐싱 + ⓑ 보조 스테이지 다운그레이드). 실 LLM 0콜(전 스텁).

설계 계약:
  ⓐ 프롬프트 캐싱(anthropic_provider.chat):
     ① 긴 system(≥2000자) → system 이 콘텐츠 블록 배열 + cache_control(ephemeral). 짧으면 평문 문자열(하위호환).
     ② SDK/모델이 cache_control 미지원 4xx → 1회 적응 후 평문 폴백 + 인스턴스 기억(이후 콜 헛 시도 0).
     ③ usage 캐시 카운터 additive: 응답의 cache_creation/read_input_tokens 를 별도 카운터로 누적.
        chat_tokens=input+output 산식 불변(이중 계상 0). as_dict 노출.
  ⓑ 보조 스테이지 라우팅(build_engine/EngineSession):
     ④ aux_model 설정 시 대상 4 스테이지(summarize·wiki·ontology_propose·ledger_reconcile)=aux provider,
        제외 스테이지(check 등)=기존 gen provider. 스텁 호출 로그로 판별.
     ⑤ aux_model="" 하위호환: 스왑 0 — aux provider is gen provider(동일 객체)·프롬프트/계측 바이트 동일.

실행: (app/ 에서) py -3.12 -X utf8 -m pytest tools/test_ce1_cost_engineering.py -q
"""
from __future__ import annotations
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/

from novelcopilot.llm.base import LLMProvider, Usage


# ─────────────────────────────────────────────────────────────────────────────
# 스텁: anthropic SDK 클라이언트(messages.create) — 실 네트워크 0. 요청 kwargs 를 로그로 남긴다.
# ─────────────────────────────────────────────────────────────────────────────
class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50,
                 cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text="본문", usage=None, stop_reason="end_turn"):
        self.content = [_FakeTextBlock(text)]
        self.usage = usage
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kw):
        self._owner.calls.append(kw)
        # cache_control 미지원 시뮬(플래그) — system 이 블록 배열(리스트)일 때만 4xx 흉내
        if self._owner.reject_cache and isinstance(kw.get("system"), list):
            raise Exception("messages.create: `cache_control` is not supported for this model (400)")
        return self._owner.next_resp


class _FakeClient:
    def __init__(self, owner):
        self.messages = _FakeMessages(owner)


def _make_provider(reject_cache=False, resp=None):
    """실 anthropic SDK 를 임포트하지 않고 AnthropicProvider 를 세운다(생성자 SDK import·client 생성을 우회)."""
    from novelcopilot.llm.anthropic_provider import AnthropicProvider

    class _EmbedStub(LLMProvider):
        def chat(self, *a, **k): return ""
        def embed(self, texts): return [[0.0] * 4 for _ in texts]

    prov = AnthropicProvider.__new__(AnthropicProvider)   # __init__(SDK import) 우회
    LLMProvider.__init__(prov)
    prov.gen_model = "claude-fable-5"
    prov._embed = _EmbedStub()
    prov._cap = 16000
    prov._use_temp = True
    prov._use_cache = True
    prov.calls = []
    prov.reject_cache = reject_cache
    prov.next_resp = resp or _FakeResp(usage=_FakeUsage())
    prov._client = _FakeClient(prov)
    return prov


# ─────────────────────────────────────────────────────────────────────────────
# ① 긴 system → 캐시 블록 변환 · 짧으면 문자열
# ─────────────────────────────────────────────────────────────────────────────
def test_a_long_system_becomes_cache_block():
    prov = _make_provider()
    long_sys = "가" * 2500   # ≥ _CACHE_SYS_MIN_CHARS(2000)
    prov.chat([{"role": "system", "content": long_sys},
               {"role": "user", "content": "질문"}])
    sysarg = prov.calls[-1]["system"]
    ok = isinstance(sysarg, list) and len(sysarg) == 1
    ok &= (sysarg[0]["type"] == "text")
    ok &= (sysarg[0]["text"] == long_sys)                      # 원문 무손실
    ok &= (sysarg[0].get("cache_control") == {"type": "ephemeral"})
    print(f"[{'OK' if ok else 'FAIL'}] ① 긴 system → cache_control(ephemeral) 콘텐츠 블록")
    assert ok


def test_a_short_system_stays_string():
    prov = _make_provider()
    short_sys = "짧은 지시"   # < 2000자
    prov.chat([{"role": "system", "content": short_sys},
               {"role": "user", "content": "질문"}])
    sysarg = prov.calls[-1]["system"]
    ok = isinstance(sysarg, str) and sysarg == short_sys       # 평문 문자열(하위호환·바이트 동일)
    print(f"[{'OK' if ok else 'FAIL'}] ① 짧은 system → 평문 문자열(하위호환)")
    assert ok


# ─────────────────────────────────────────────────────────────────────────────
# ② cache_control 미지원 4xx → 인스턴스 기억 후 평문 폴백
# ─────────────────────────────────────────────────────────────────────────────
def test_a_unsupported_cache_falls_back_and_remembers():
    prov = _make_provider(reject_cache=True)
    long_sys = "나" * 2500
    out = prov.chat([{"role": "system", "content": long_sys},
                     {"role": "user", "content": "질문"}])
    ok = (out == "본문")                                        # 폴백 후 정상 응답
    ok &= (prov._use_cache is False)                           # 인스턴스 기억(이후 콜 헛 시도 0)
    # 첫 콜(캐시 블록 4xx) + 재시도(평문 성공) = 2회. 재시도 system 은 평문 문자열.
    ok &= (len(prov.calls) == 2)
    ok &= isinstance(prov.calls[0]["system"], list)           # 1차: 캐시 블록(거부됨)
    ok &= isinstance(prov.calls[1]["system"], str)            # 2차: 평문 폴백
    # 이후 콜은 캐시 시도조차 안 함(기억) — 긴 system 이어도 평문
    prov.calls.clear()
    prov.chat([{"role": "system", "content": long_sys}, {"role": "user", "content": "또"}])
    ok &= (len(prov.calls) == 1 and isinstance(prov.calls[0]["system"], str))
    print(f"[{'OK' if ok else 'FAIL'}] ② 캐시 미지원 4xx → 평문 폴백 + 인스턴스 기억(헛 시도 0)")
    assert ok


# ─────────────────────────────────────────────────────────────────────────────
# ③ usage 캐시 카운터 additive + chat_tokens 산식 불변(이중 계상 0)
# ─────────────────────────────────────────────────────────────────────────────
def test_a_usage_cache_counters_additive():
    # as_dict 에 신설 키 노출
    u = Usage()
    d = u.as_dict()
    ok = ("cache_creation_input_tokens" in d and "cache_read_input_tokens" in d)
    ok &= (d["cache_creation_input_tokens"] == 0 and d["cache_read_input_tokens"] == 0)
    # 기존 키 불변(대칭·무회귀)
    ok &= all(k in d for k in ("chat_calls", "chat_tokens", "embed_calls", "embed_items"))

    # provider chat: 응답 usage 에 캐시 토큰이 실리면 별도 카운터로 누적, chat_tokens=input+output 불변
    prov = _make_provider(resp=_FakeResp(usage=_FakeUsage(
        input_tokens=100, output_tokens=50,
        cache_creation_input_tokens=800, cache_read_input_tokens=1200)))
    prov.chat([{"role": "system", "content": "가" * 2500}, {"role": "user", "content": "q"}])
    us = prov.usage
    ok &= (us.chat_tokens == 150)                              # input(100)+output(50) — 캐시 토큰 미합산(이중 계상 0)
    ok &= (us.cache_creation_input_tokens == 800)
    ok &= (us.cache_read_input_tokens == 1200)
    # 2번째 콜 — additive 누적 확인
    prov.chat([{"role": "system", "content": "가" * 2500}, {"role": "user", "content": "q2"}])
    ok &= (us.chat_tokens == 300)
    ok &= (us.cache_creation_input_tokens == 1600 and us.cache_read_input_tokens == 2400)
    print(f"[{'OK' if ok else 'FAIL'}] ③ 캐시 카운터 additive·chat_tokens 산식 불변(이중 계상 0)")
    assert ok


# ─────────────────────────────────────────────────────────────────────────────
# 스텁 provider(계측 로그) — 스테이지 라우팅 판별용
# ─────────────────────────────────────────────────────────────────────────────
class _LogProvider(LLMProvider):
    def __init__(self, tag):
        super().__init__()
        self.tag = tag
        self.last_truncated = False

    def chat(self, messages, *a, **k):
        self.usage.chat_calls += 1
        self.usage.chat_tokens += 10
        return "도현이 문을 열고 걸어 나갔다. " * 40   # 엔티티명 포함 → wiki.ingest 가 인물 present 판정→aux 콜

    def chat_json(self, messages, *a, **k):
        self.usage.chat_calls += 1
        self.usage.chat_tokens += 10
        # wiki/summarize/ontology_propose 응답 형태를 모두 만족하는 관대한 스텁
        return {"oneliner": "요약", "synopsis": "상세." * 30,
                "pages": [], "new_entities": [], "state_changes": [],
                "relations": [], "new_settings": []}

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _build_bundle(aux):
    """build_engine 을 aux_provider 주입 유무로 세운다. gen=별도 로그 provider."""
    from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat, StyleSpec
    from novelcopilot.engine.factory import build_engine
    from novelcopilot.config import get_settings
    world = WorldConfig(
        title="[실험] CE-1", genre="현판", tone="건조", premise="테스트.",
        entities=[EntitySpec(id="hero", name="도현", etype="character", profile="주인공")],
        beats=[Beat(chapter=2, title="t", summary="s", entities=["hero"])],
        style=StyleSpec(pov="third_limited"))
    settings = get_settings().model_copy(update={"narrator_voice": False, "humanize": False,
                                                 "style_repair": False, "reader_desk": False,
                                                 "claim_audit": False, "continuity_polish": False})
    gen = _LogProvider("gen")
    b = build_engine(world, gen, settings, aux_provider=aux)
    return b, gen, settings


# ─────────────────────────────────────────────────────────────────────────────
# ④ aux 라우팅 — 대상 4 스테이지=aux, 제외=gen
# ─────────────────────────────────────────────────────────────────────────────
def test_b_aux_routing_targets():
    aux = _LogProvider("aux")
    b, gen, settings = _build_bundle(aux)
    # wiki·ontology_updater 가 aux 로 구성됐는지(객체 동일성)
    ok = (b.wiki.provider is aux)                              # wiki=aux
    ok &= (b.updater.provider is aux)                          # ontology_propose=aux
    ok &= (b.generator.aux_provider is aux)                    # _summarize=aux
    ok &= (b.aux_provider is aux)                              # 번들 노출
    # 제외 스테이지: check(캐넌 가드)=extractor=gen provider(프로즈·check 는 aux 아님)
    ok &= (b.checker.extractor.provider is gen)                # check(G-A) 는 gen
    ok &= (b.generator.provider is gen)                        # draft/polish(프로즈)=gen
    ok &= (b.rag.provider is gen)                              # RAG 임베딩=gen(OpenAI 위임 경로)

    # 실경로: _summarize·wiki.ingest 는 aux 콜, check 는 gen 콜인지 콜 카운트로 판별
    aux0, gen0 = aux.usage.chat_calls, gen.usage.chat_calls
    b.generator._summarize("본문 " * 100, "", {"summary": "s", "key_events": []})
    ok &= (aux.usage.chat_calls == aux0 + 1 and gen.usage.chat_calls == gen0)   # summarize=aux only
    aux1, gen1 = aux.usage.chat_calls, gen.usage.chat_calls
    b.wiki.ingest_chapter(2, "도현이 문을 열었다. " * 30, b.ontology, reviewed=True)
    ok &= (aux.usage.chat_calls == aux1 + 1 and gen.usage.chat_calls == gen1)   # wiki=aux only
    print(f"[{'OK' if ok else 'FAIL'}] ④ aux 라우팅: 대상 4 스테이지=aux · 제외(check/프로즈/RAG)=gen")
    assert ok


def test_b_aux_stage_usage_tracked_in_generate():
    """generate() 의 usage_by_stage['summarize']·['wiki'] 가 aux 델타를 잡는지(계측 연속성·언더카운트 차단)."""
    from novelcopilot.engine.checker import CheckResult
    from novelcopilot.domain.types import SceneSpec, ChapterStatus
    aux = _LogProvider("aux")
    b, gen, settings = _build_bundle(aux)
    g = b.generator
    g.plan_scenes = lambda beat, directives: [SceneSpec(index=0, goal="g", key_events=[])]
    g._rewrite = lambda text, viols, board, **kw: text
    g.checker.check_text = lambda *a, **k: CheckResult(violations=[], claims=[])
    rec = g.generate(2, {"title": "t", "summary": "s", "entities": ["hero"], "key_events": []},
                     b.ontology, b.rag, b.wiki, prev_chapter_text="", story_so_far="스토리")
    us = rec.usage_by_stage or {}
    ok = (rec.status == ChapterStatus.FINALIZED)
    # summarize·wiki 는 aux 콜(각 +10 토큰) → usage_by_stage 에 그 델타가 잡혀야(계측이 aux 를 봄)
    ok &= (us.get("summarize", 0) >= 10)                       # aux 델타 계상(0 아님)
    ok &= (us.get("wiki", 0) >= 10)
    # draft/check 는 gen 콜 → 여전히 계상(대칭)
    ok &= (us.get("draft", 0) >= 10)
    print(f"[{'OK' if ok else 'FAIL'}] ④ generate usage_by_stage 가 aux 델타 계상 — summarize={us.get('summarize')}·wiki={us.get('wiki')}")
    assert ok


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ aux_model="" 하위호환 — 스왑 0(aux is gen)
# ─────────────────────────────────────────────────────────────────────────────
def test_b_empty_aux_no_swap():
    # aux_provider 미주입(None) → build_engine 이 gen 으로 폴백(동일 객체) → 스왑 0
    b, gen, settings = _build_bundle(None)
    ok = (b.aux_provider is gen)
    ok &= (b.wiki.provider is gen and b.updater.provider is gen and b.generator.aux_provider is gen)
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ aux 미주입 → aux is gen(스왑 0·바이트 동일)")
    assert ok


def test_b_session_empty_aux_model_no_swap():
    """EngineSession: aux_model="" 이면 aux provider is gen provider(create_role_provider 호출조차 안 함)."""
    from novelcopilot.services.session import EngineSession
    from novelcopilot.domain.world import WorldConfig, EntitySpec, StyleSpec
    from novelcopilot.config import get_settings
    world = WorldConfig(title="t", synopsis="s",
                        entities=[EntitySpec(id="hero", name="레오")],
                        style=StyleSpec(pov="third_limited"))
    settings = get_settings().model_copy(update={"aux_model": ""})
    gen = _LogProvider("gen")
    sess = EngineSession("p1", world, gen, settings)
    ok = (sess.aux_provider is gen)                            # 빈값 → 동일 객체(스왑 0)
    ok &= (sess.bundle.aux_provider is gen)
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ EngineSession aux_model='' → aux is gen provider(스왑 0)")
    assert ok


def test_b_session_aux_model_set_routes(monkeypatch):
    """EngineSession: aux_model·extract_model 설정 시 create_role_provider 로 각 1회 생성·주입
    (세션 수명 1회 — 콜마다 재생성 금지. extract 라우팅은 2026-08-21 캐논 추출 sonnet 전환)."""
    from novelcopilot.services import session as _S
    from novelcopilot.domain.world import WorldConfig, EntitySpec, StyleSpec
    from novelcopilot.config import get_settings
    aux = _LogProvider("aux")
    calls = {"n": 0, "specs": []}

    def _fake_crp(settings, spec):
        calls["n"] += 1
        calls["specs"].append(spec)
        return aux
    monkeypatch.setattr(_S, "create_role_provider", _fake_crp)
    world = WorldConfig(title="t", synopsis="s",
                        entities=[EntitySpec(id="hero", name="레오")],
                        style=StyleSpec(pov="third_limited"))
    settings = get_settings().model_copy(update={"aux_model": "anthropic:claude-sonnet-5"})
    gen = _LogProvider("gen")
    sess = _S.EngineSession("p1", world, gen, settings)
    ok = (sess.aux_provider is aux)                            # 설정된 aux 로 스왑
    ok &= (sess.bundle.wiki.provider is aux)                  # 배선 확인
    ok &= (sess.extract_provider is aux)                       # 추출 provider 도 스펙 생성분(fake 동일 객체)
    ok &= (sess.bundle.checker.extractor.provider is aux)      # 추출 배선 확인(ClaimExtractor 주입)
    ok &= (calls["n"] == 2)                                    # aux+extract 각 1회(세션 수명 1회 — 콜마다 재생성 금지)
    ok &= (calls["specs"] == ["anthropic:claude-sonnet-5",
                              get_settings().extract_model])   # 생성 순서 = aux → extract
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ EngineSession aux/extract_model 설정 → create_role_provider 각 1회·스왑")
    assert ok


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                import inspect
                if "monkeypatch" in inspect.signature(fn).parameters:
                    continue   # pytest fixture 필요 — __main__ 에선 스킵
                fn()
            except AssertionError:
                print(f"  !! FAIL: {name}")
                raise
    print("CE-1 로컬 실행 완료")
