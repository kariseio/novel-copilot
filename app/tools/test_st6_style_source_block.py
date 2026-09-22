# -*- coding: utf-8 -*-
"""ST-6 스타일 규칙 배선 소스차단 검증 — worldgen LLM 이 style.rules·system_persona 를 장르 문안으로
통째 대체해 코드 SSOT(DEFAULT_STYLE_RULES·기본 persona)가 신규 작품에 한 번도 도달하지 못하던(dead code)
결함의 회귀 가드(LLM 0콜 스텁).

소스 진단(STV 2026-07-07): 스키마 힌트가 선택 필드 style.rules·system_persona 를 LLM 산출 대상으로
노출 → LLM 이 매번 채움(관측 5/5) → 코드 기본값 영구 silenced. 병합 지점 없음(render_style 은 저장 rules
만 렌더). 개입: 스키마 힌트에서 두 필드 제거 + 파싱 지점(_strip_llm_style)에서 폐기(2차 소스차단).

계약(CX-7 갱신):
- 신규 생성 시 StyleSpec.rules == [](렌더 폴백=코드 상수), system_persona == 기본 persona(장르 문안 아님).
- LLM 이 style.rules·system_persona·author_style 를 반환해도 전부 무시.
- 기존 작품(저장된 커스텀 rules/persona)은 로드 바이트 동일(소스차단은 신규 generate 경로에만).
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

from novelcopilot.domain.project import ProjectSeed
from novelcopilot.domain.world import WorldConfig, StyleSpec, DEFAULT_STYLE_RULES
from novelcopilot.worldgen import WorldGenerator
from novelcopilot.worldgen.generator import _SCHEMA_HINT
from novelcopilot.llm.base import LLMProvider


class _StyleStub(LLMProvider):
    """generate() 스텁: worldgen 콜엔 world JSON, 추출 콜(system 에 '시간 기준점')엔 빈 앵커. LLM 0콜."""
    def __init__(self, world_json: dict):
        super().__init__()
        self._world = json.dumps(world_json, ensure_ascii=False)
    def chat(self, messages, **kw):
        sysmsg = messages[0].get("content", "") if messages else ""
        if "시간 기준점" in sysmsg:
            return json.dumps({"anchors": []}, ensure_ascii=False)
        return self._world
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


# LLM 이 style.rules·system_persona·author_style 를 장르 문안으로 통째 대체한 산출을 모사(관측 결함 재현)
_LLM_GENRE_STYLE = {
    "system_persona": "너는 인기 한국 학원 로맨스 웹소설 작가다. 사건보다 감정의 결에 집중한다.",
    "ending_hook": "soft",
    "rules": ["감정과 내면을 충분히 그려라.", "사건보다 관계의 온도에 집중하라."],
    "author_style": "worldgen 이 침범한 작가 슬롯",
}
_WORLD_JSON = {
    "title": "학원물", "genre": "로맨스", "tone": "잔잔",
    "premise": "평범한 학원 일상.", "synopsis": "전학생이 온다.",
    "attributes": [{"key": "status", "label": "생사", "kind": "state",
                    "states": ["alive", "dead"], "irreversible": ["dead"],
                    "terminal": ["dead"], "mutable": True}],
    "entities": [{"id": "hero", "name": "지훈", "etype": "character",
                  "base_status": "alive"}],
    "beats": [{"chapter": 1, "title": "전학", "summary": "전학생 등장", "entities": ["hero"]}],
    "style": _LLM_GENRE_STYLE,
}


def _gen(world_json: dict) -> WorldConfig:
    seed = ProjectSeed(title="학원물", genre="로맨스", premise="평범한 학원 일상.")
    return WorldGenerator(_StyleStub(world_json)).generate(seed)


def test_schema_hint_no_rules_or_persona():
    # 1차 소스차단: 스키마 힌트가 더 이상 rules·system_persona 를 LLM 산출 대상으로 노출하지 않음
    assert '"rules"' not in _SCHEMA_HINT
    assert "system_persona" not in _SCHEMA_HINT
    assert '"ending_hook"' in _SCHEMA_HINT   # 정책 필드는 보존


def test_new_work_rules_are_code_default_snapshot():
    # CX-7 신계약: 신규 작품 rules 는 빈 값(스냅샷 폐기 — 저장 이중화 해소), 렌더가 코드 상수로 폴백
    from novelcopilot.engine.prompts import render_style
    world = _gen(_WORLD_JSON)
    assert list(world.style.rules) == []
    assert DEFAULT_STYLE_RULES[0][:20] in render_style(world.style)


def test_new_work_persona_is_code_default():
    # 기본 persona(장르 중립) 그대로 — LLM 의 '학원 로맨스 작가'·역방향 규칙("감정의 결") 이 아님
    world = _gen(_WORLD_JSON)
    assert world.style.system_persona == StyleSpec().system_persona
    assert "감정의 결" not in world.style.system_persona
    assert "학원 로맨스 작가" not in world.style.system_persona


def test_llm_returned_style_rules_ignored():
    # LLM 이 rules 를 반환해도 무시 — 역방향 장르 규칙이 스며들지 않음
    from novelcopilot.engine.prompts import render_style
    world = _gen(_WORLD_JSON)
    rendered = render_style(world.style)
    assert "관계의 온도에 집중" not in rendered           # LLM 역방향 규칙 미유입(CX-7: 저장이 아니라 렌더 기준)
    # 분량 규칙(공백 포함 4,500~5,500자 목표)은 코드 상수 폴백으로 실효(LN-1 유지)
    assert "4,500~5,500" in rendered


def test_worldgen_cannot_pollute_author_style():
    # worldgen 이 author_style(작가 오버레이 슬롯)을 채우지 못함 — 기본 빈값 유지
    world = _gen(_WORLD_JSON)
    assert world.style.author_style == ""


def test_ending_hook_policy_field_still_flows():
    # 정책 필드(ending_hook)는 보존 — 잔잔한 장르 soft 지정이 그대로 반영
    world = _gen(_WORLD_JSON)
    assert world.style.ending_hook == "soft"


def test_new_work_with_no_style_block_uses_defaults():
    # style 키 자체가 없는 산출도 코드 기본값 상속(하위호환)
    wj = {k: v for k, v in _WORLD_JSON.items() if k != "style"}
    world = _gen(wj)
    assert list(world.style.rules) == []                   # CX-7: 빈 값 저장(렌더 폴백이 코드 기본 전담)
    assert world.style.system_persona == StyleSpec().system_persona
    assert world.style.ending_hook == "cliffhanger"   # StyleSpec 기본


def test_existing_work_style_load_byte_identical():
    # 하위호환: 기존 작품의 저장된 커스텀 rules/persona 는 로드 시 무변경(소스차단은 신규 generate 만)
    custom = StyleSpec(rules=["기존 커스텀 규칙 A", "기존 커스텀 규칙 B"],
                       system_persona="기존 저장된 장르 페르소나",
                       author_style="기존 작가 오버레이")
    w = WorldConfig(title="기존작", genre="로맨스", style=custom)
    blob = w.model_dump_json()
    w2 = WorldConfig.model_validate_json(blob)
    assert list(w2.style.rules) == ["기존 커스텀 규칙 A", "기존 커스텀 규칙 B"]
    assert w2.style.system_persona == "기존 저장된 장르 페르소나"
    assert w2.style.author_style == "기존 작가 오버레이"
    assert w2.model_dump_json() == blob   # 바이트 동일


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"ST-6 검증: ALL GREEN ({len(fns)} tests)")
