# -*- coding: utf-8 -*-
"""R3(DP-8) 검증 — 서술 시점(POV)을 데이터로 승격. LLM 0콜(전 결정론).

근거: docs/design-dp-repair.md §DP-8.
- 확정 원인: 3인칭은 하드코딩이 아니라 소프트 디폴트(규칙8 문장)+mode-collapse 로 굳었을 뿐이고,
  하류 파손은 '서술자 나→주인공 id 미결속→과소추출' 1건뿐이다.
- 수리: ①StyleSpec.pov 신설(third_limited 기본/third_omniscient/first) ②render_style 파생 지시
  (first=1인칭 주인공·'나는~했다'·무태그 대사 연쇄 / 기본값 경로는 바이트 동일) ③추출기 pov_entity_id
  결속(서술자 '나'→주인공 present 편입+roster 별칭 '나/내'+시스템 결속 지시) ④checker _STOP 대칭
  (나는·내가·나를·나도) ⑤_strip_llm_style 이 pov 폐기(worldgen 발명 금지) ⑥하위호환.

검증 축:
1) render_style 기본(third_limited) 바이트 동일 — 시점 결 무주입(무회귀).
2) render_style first — 1인칭 파생 지시 주입, 8규칙 유지, 기본과 다름.
3) render_style third_omniscient — 전지 파생 지시 주입.
4) 추출기 결속(1인칭) — 서술자 '나'→주인공 present 편입·roster 별칭 '나/내'·시스템 결속 지시,
   ontology 원본 aliases 불변.
5) 추출기 무결속(1인칭 표지 부재) — 결속 무동작(marker 게이트).
6) 추출기 3인칭 경로(pov="") — 기존과 동일(주인공 미결속).
7) checker 관통 — check_text 가 pov_entity_id 를 추출기까지 전달(기본값 "" 하위호환).
8) harness 주인공 도출 — first=선언순 첫 actor, 3인칭="".
9) quality_gates _STOP 대칭 — 1인칭 대명사 편입, word_tics 가 대사 반복도 틱 오탐 안 함.
10) worldgen 발명 차단 — _strip_llm_style 이 LLM 산출 pov 폐기(정책 필드는 보존).
11) 하위호환 — 구 JSON(pov 부재) 로드=기본값, WorldConfig 라운드트립 안정.

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_r3_dp8_pov.py
"""
from __future__ import annotations
import sys
import json
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

from types import SimpleNamespace

from novelcopilot.domain.world import WorldConfig, StyleSpec, EntitySpec, DEFAULT_STYLE_RULES
from novelcopilot.engine.prompts import render_style, _POV_DIRECTIVE
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.engine.factory import build_ontology
from novelcopilot.engine.extractor import ClaimExtractor
from novelcopilot.engine.checker import Checker
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.engine.quality_gates import word_tics, _STOP
from novelcopilot.worldgen.generator import WorldGenerator
from novelcopilot.llm.base import LLMProvider

_POV_MARK = "[서술 시점"   # first/third_omniscient 파생 지시 앵커(구현과 동기화)


class _Bus:
    def emit(self, *a, **k):
        pass


class _Cap(LLMProvider):
    """LLM 콜 캡처 — 마지막 messages 를 보관하고 무해한 응답을 돌린다(실 LLM 0콜)."""
    def __init__(self):
        super().__init__()
        self.msgs = None
        self.last_truncated = False

    def chat(self, messages, *a, **k):
        self.msgs = messages
        return "본문."

    def chat_json(self, messages, *a, **k):
        self.msgs = messages
        return {"entities": [], "relation_claims": [], "table_claims": []}

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _world():
    # worldgen 규약: entities 선언 첫 actor 가 주인공(스키마 예시 id 'hero'). 서포트는 지면 이름으로 등장.
    return WorldConfig(title="t", genre="x",
                       entities=[EntitySpec(id="hero", name="도현"),
                                 EntitySpec(id="sup", name="민수")])


def _ontology():
    w = _world()
    return build_ontology(w, Vocabulary.from_world(w))


def _extractor():
    w = _world()
    vocab = Vocabulary.from_world(w)
    ont = build_ontology(w, vocab)
    prov = _Cap()
    return ClaimExtractor(prov, vocab, []), ont, prov


def _gen(style: StyleSpec):
    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=False, scene_style_anchor=False)
    return ChapterGenerator(_Cap(), checker=None, style=style, event_bus=_Bus(), settings=settings)


# ---------- 1) render_style 기본(third_limited) 바이트 동일 ----------
def test_render_style_default_third_limited_byte_identical() -> bool:
    d = render_style(StyleSpec())
    ok = (StyleSpec().pov == "third_limited")                 # 기본 시점
    ok &= (_POV_DIRECTIVE.get("third_limited", "") == "")     # 기본은 덧붙임 0(바이트 동일 근거)
    ok &= (_POV_MARK not in d)                                # 시점 결 무주입
    ok &= (render_style(StyleSpec(pov="third_limited")) == d) # 명시=암묵 동일
    ok &= all(f"{i + 1}) {r}" in d for i, r in enumerate(DEFAULT_STYLE_RULES))   # 8규칙 그대로
    print(f"[{'OK' if ok else 'FAIL'}] render_style 기본(third_limited) 바이트 동일·시점 무주입")
    return ok


# ---------- 2) render_style first — 1인칭 파생 지시 ----------
def test_render_style_first_person_directive() -> bool:
    out = render_style(StyleSpec(pov="first"))
    ok = (_POV_MARK in out and "1인칭 주인공" in out)
    ok &= ("나는 ~했다" in out and "태그 없는 대사" in out)   # 내면을 서술에·무태그 대사 연쇄
    ok &= (out != render_style(StyleSpec()))                  # 기본과 다름
    ok &= all(f"{i + 1}) {r}" in out for i, r in enumerate(DEFAULT_STYLE_RULES))   # 8규칙은 유지(덧붙임)
    print(f"[{'OK' if ok else 'FAIL'}] render_style first: 1인칭 파생 지시 주입·8규칙 유지")
    return ok


# ---------- 3) render_style third_omniscient — 전지 파생 지시 ----------
def test_render_style_omniscient_directive() -> bool:
    out = render_style(StyleSpec(pov="third_omniscient"))
    ok = (_POV_MARK in out and "3인칭 전지" in out)
    ok &= (out != render_style(StyleSpec()))
    ok &= all(f"{i + 1}) {r}" in out for i, r in enumerate(DEFAULT_STYLE_RULES))
    print(f"[{'OK' if ok else 'FAIL'}] render_style third_omniscient: 전지 파생 지시 주입")
    return ok


# ---------- 4) 추출기 결속(1인칭) ----------
def test_extractor_first_person_binds_protagonist() -> bool:
    ex, ont, prov = _extractor()
    text = "나는 검을 뽑았다. 내가 민수를 노려봤다. 민수가 물러섰다."   # 주인공 이름(도현)은 지면 부재
    ex.extract_full(text, ont, [], pov_entity_id="hero")
    sysmsg, usermsg = prov.msgs[0]["content"], prov.msgs[1]["content"]
    ok = ("1인칭 서술" in sysmsg and "도현" in sysmsg and "id=hero" in sysmsg)   # 시스템 결속 지시
    ok &= ("도현" in usermsg)                       # 이름 지면 부재에도 roster 편입(과소추출 소스 차단)
    ok &= ('["나", "내"]' in usermsg)               # roster 별칭에 '나/내' 편입
    ok &= (ont.entities["hero"].aliases == [])      # ontology 원본 aliases 불변(로컬 복사만 증강)
    print(f"[{'OK' if ok else 'FAIL'}] 추출기 1인칭 결속: 서술자 나→주인공 present·별칭 '나/내'·원본 불변")
    return ok


# ---------- 5) 추출기 무결속(1인칭 표지 부재) ----------
def test_extractor_no_marker_no_binding() -> bool:
    ex, ont, prov = _extractor()
    text = "도현이 검을 뽑았다. 민수가 물러섰다."   # 1인칭 표지(나는/내가/나를/나도) 없음
    ex.extract_full(text, ont, [], pov_entity_id="hero")
    sysmsg, usermsg = prov.msgs[0]["content"], prov.msgs[1]["content"]
    ok = ("1인칭 서술" not in sysmsg)               # 표지 부재 → 결속 무동작(marker 게이트)
    ok &= ('["나", "내"]' not in usermsg)           # 별칭 증강 없음
    print(f"[{'OK' if ok else 'FAIL'}] 추출기 marker 게이트: 1인칭 표지 부재 시 결속 무동작")
    return ok


# ---------- 6) 추출기 3인칭 경로(pov="") — 기존과 동일 ----------
def test_extractor_third_person_pov_empty_unchanged() -> bool:
    ex, ont, prov = _extractor()
    text = "나는 검을 뽑았다. 민수가 물러섰다."   # 1인칭 표지 있으나 pov_entity_id 미지정(3인칭 경로)
    ex.extract_full(text, ont, [], pov_entity_id="")
    sysmsg, usermsg = prov.msgs[0]["content"], prov.msgs[1]["content"]
    ok = ("1인칭 서술" not in sysmsg)               # 3인칭 경로엔 결속 지시 없음
    ok &= ("도현" not in usermsg)                   # 주인공 이름 지면 부재 + pov 없음 → 미편입(기존 동작)
    print(f"[{'OK' if ok else 'FAIL'}] 추출기 3인칭 경로(pov=\"\"): 주인공 미결속(기존 동작 보존)")
    return ok


# ---------- 7) checker 관통 ----------
class _RecExtractor:
    world_rules: list = []

    def __init__(self):
        self.received = "SENTINEL"

    def extract_full(self, text, ontology, involved_ids, pov_entity_id=""):
        self.received = pov_entity_id
        return {"entities": [], "relation_claims": [], "table_claims": []}


class _NoRules:
    def evaluate(self, claims, ontology, chapter):
        return []


def test_checker_threads_pov_entity_id() -> bool:
    ont = _ontology()
    ex = _RecExtractor()
    ch = Checker(ex, _NoRules())
    ch.check_text("나는 갔다.", ont, 1, ["hero"], "hero")
    ok = (ex.received == "hero")                    # 명시 전달
    ch.check_text("도현이 갔다.", ont, 1, ["hero"])  # 기본값(pov_entity_id 생략)
    ok &= (ex.received == "")                        # 하위호환 기본 ""
    print(f"[{'OK' if ok else 'FAIL'}] checker.check_text 가 pov_entity_id 추출기까지 관통(기본 \"\")")
    return ok


# ---------- 8) harness 주인공 도출 ----------
def test_harness_pov_entity_id_derivation() -> bool:
    ont = _ontology()
    ok = (_gen(StyleSpec(pov="first"))._pov_entity_id(ont) == "hero")   # 선언순 첫 actor
    ok &= (_gen(StyleSpec())._pov_entity_id(ont) == "")                 # 3인칭 → 결속 무동작
    ok &= (_gen(StyleSpec(pov="third_omniscient"))._pov_entity_id(ont) == "")
    print(f"[{'OK' if ok else 'FAIL'}] harness 주인공 도출: first=첫 actor·3인칭=\"\"")
    return ok


# ---------- 9) quality_gates _STOP 대칭 ----------
def test_stop_first_person_symmetry() -> bool:
    ok = ({"나는", "내가", "나를", "나도"} <= _STOP)                    # 1인칭 대명사 편입
    ok &= ({"그녀", "그는", "그가"} <= _STOP)                          # 3인칭 대명사(기존)와 대칭 유지
    # 1인칭 서술자 반복(대사 안 ≥60%·cap 초과)도 틱으로 오탐하지 않음(시점의 문법적 필연)
    text = '"나는 간다."\n"나는 온다."\n"나는 안다."\n"나는 진다."\n"나는 이긴다."'
    tics = word_tics(text, set(), cap=4)
    ok &= all(p != "나는" for p, _ in tics)
    print(f"[{'OK' if ok else 'FAIL'}] _STOP 1인칭 대명사 대칭·word_tics 오탐 방지")
    return ok


# ---------- 10) worldgen 발명 차단 ----------
def test_worldgen_strips_llm_pov() -> bool:
    raw = {"style": {"pov": "first", "ending_hook": "soft", "rules": ["x"], "system_persona": "y"}}
    WorldGenerator._strip_llm_style(raw)
    ok = ("pov" not in raw["style"])                # LLM 발명 pov 폐기
    ok &= ("rules" not in raw["style"] and "system_persona" not in raw["style"])   # 기존 3필드도 폐기
    ok &= (raw["style"]["ending_hook"] == "soft")   # 정책 필드 보존
    print(f"[{'OK' if ok else 'FAIL'}] worldgen _strip_llm_style: LLM pov 폐기·정책 필드 보존")
    return ok


# ---------- 11) 하위호환 ----------
def test_backcompat_old_json_and_roundtrip() -> bool:
    old = {"target_chars_per_chapter": 5000, "ending_hook": "cliffhanger"}   # pov 필드 부재(구 JSON)
    s = StyleSpec.model_validate(old)
    ok = (s.pov == "third_limited")                 # 기본값으로 로드
    w = WorldConfig(title="t", genre="x")
    blob = w.model_dump_json()
    ok &= (WorldConfig.model_validate_json(blob).model_dump_json() == blob)   # 라운드트립 안정
    # 기존 커스텀 스타일(pov 없이 저장)도 로드·재저장 라운드트립 안정
    custom = StyleSpec(rules=["규칙 A"], system_persona="페르소나")
    w2 = WorldConfig(title="기존작", genre="x", style=custom)
    b2 = w2.model_dump_json()
    ok &= (WorldConfig.model_validate_json(b2).model_dump_json() == b2)
    print(f"[{'OK' if ok else 'FAIL'}] 하위호환: 구 JSON=기본 third_limited·라운드트립 안정")
    return ok


def main() -> int:
    results = [
        test_render_style_default_third_limited_byte_identical(),
        test_render_style_first_person_directive(),
        test_render_style_omniscient_directive(),
        test_extractor_first_person_binds_protagonist(),
        test_extractor_no_marker_no_binding(),
        test_extractor_third_person_pov_empty_unchanged(),
        test_checker_threads_pov_entity_id(),
        test_harness_pov_entity_id_derivation(),
        test_stop_first_person_symmetry(),
        test_worldgen_strips_llm_pov(),
        test_backcompat_old_json_and_roundtrip(),
    ]
    print("\nR3(DP-8 POV 데이터 승격) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_r3_dp8_pov_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
