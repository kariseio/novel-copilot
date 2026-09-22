# -*- coding: utf-8 -*-
"""VX-1 — XML 섹션 태그 + 대사 출력 태그 잠금(LLM 0·결정론).

감사관 반려(blocker 5) 반영분의 계약을 잠근다:
① 하위호환 — structured_prompt OFF 는 render_style·story 블록·assemble 바이트가 종전과 동일.
② ON 구조 — render_style 이 <문체규칙>·<대사표기>, assemble 이 <확정설정>/<인물보이스>/<확정스토리>/<참조맥락>.
③ 대사 라우팅(M3 긍정형) — 확정 스토리 라벨이 대사 어체를 <인물보이스>·<문체규칙> 앵커로 리다이렉트.
④ 디태거(C1) — <대사 화자="…"> → 정규 대사(따옴표) + (화자,대사) 쌍, 잔여 마커 0(리터럴 노출 차단).
⑤ 원장 우회 — tagged 쌍이 있으면 화자 귀속 LLM 콜을 건너뛴다(미상·오귀속 소멸). None 이면 종전 경로 폴백.

실행: PYTHONPATH=app py -3 -m pytest tools/test_vx1_dialogue_tags.py -q
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.domain.types import ContextBoard, SceneSpec
from novelcopilot.domain.world import StyleSpec
from novelcopilot.engine.prompts import PromptAssembler, render_style
from novelcopilot.engine import story_pass_prompts as P
from novelcopilot.engine.textfmt import lower_dialogue_tags, dialogue_tag_residue
from novelcopilot.engine.dialogue_ledger import build_ledger, UNKNOWN
from novelcopilot.llm.base import LLMProvider


def _assemble(style, **board_kw):
    a = PromptAssembler(style)
    b = ContextBoard(chapter=5, **board_kw)
    s = SceneSpec(index=0, goal="목표", key_events=["사건A", "사건B"])
    return a.assemble(b, s, "")


# ── ① 하위호환: OFF 바이트 동일 ──────────────────────────────────────────────
def test_off_byte_identity():
    assert render_style(StyleSpec(structured_prompt=False)) == render_style(StyleSpec())
    assert P.build_story_block_draft("- 줄", xml=False) == P.build_story_block_draft("- 줄")
    assert P.build_story_block_continue(["- 줄"], xml=False) == P.build_story_block_continue(["- 줄"])
    off = _assemble(StyleSpec(structured_prompt=False), voice_cards="하지연: 합쇼체", confirmed_story="- 사건")
    base = _assemble(StyleSpec(), voice_cards="하지연: 합쇼체", confirmed_story="- 사건")
    assert off == base                                   # assemble OFF = 종전 바이트 동일


# ── ② ON 구조: render_style 태그 ────────────────────────────────────────────
def test_on_render_style_tags():
    on = render_style(StyleSpec(structured_prompt=True))
    assert "<문체규칙>" in on and "</문체규칙>" in on     # M1: 라우팅이 참조하는 태그를 실재화
    assert "<대사표기>" in on and "</대사표기>" in on
    assert '<대사 화자="이름">' in on                     # 출력 표기 계약
    assert "[웹소설 문체 규칙" not in on                  # 대괄호 라벨 소멸
    # C3: 1인칭 시점에서 '태그 없는 대사'(창작 용어) ↔ XML '태그' 충돌 해소
    on_first = render_style(StyleSpec(structured_prompt=True, pov="first", narrator_voice="건조"))
    assert "태그 없는 대사" not in on_first


# ── ② ON 구조: assemble 태그 ────────────────────────────────────────────────
def test_on_assemble_tags():
    on = _assemble(StyleSpec(structured_prompt=True), voice_cards="하지연: 합쇼체",
                   world_rules=["규칙1"], confirmed_story="- 하지연이 통보한다")
    for tag in ("<확정설정", "</확정설정>", "<인물보이스", "</인물보이스>",
                "<확정스토리", "</확정스토리>", "<참조맥락", "<세계규칙>"):
        assert tag in on, tag
    for lbl in ("[확정 설정:", "[인물 보이스", "[이번 화 확정 스토리]", "[참조 맥락:"):
        assert lbl not in on, lbl
    # m2: 캐논 락 위반금지 열거는 속성으로 바이트 보존(예외 ⓐ)
    assert "위반금지=\"눈색·소속·생사·등급·관계·세계규칙\"" in on
    # PM 재감사: 인라인 구현 지시의 위치 참조는 '아래'(참조맥락은 이 지시 뒤에 온다) — '위' 오기 회귀 잠금.
    # XR-6⒜(2026-08-22): 구현 지시는 작가 확정(✓) bible 항목이 실주입된 화에만 발화하는 조건부 계약으로 개정
    #   — 이 픽스처(✓ 없음)에서는 부재가 정답이고, ✓ 픽스처에서 '아래' 잠금을 이어 검증한다.
    assert "(아래 <참조맥락>" not in on and "(위 <참조맥락>" not in on
    from novelcopilot.domain.types import RetrievedItem
    on_marked = _assemble(StyleSpec(structured_prompt=True), voice_cards="하지연: 합쇼체",
                          world_rules=["규칙1"], confirmed_story="- 하지연이 통보한다",
                          narrative=[RetrievedItem(source="bible", ref="digest",
                                                   text="[세계관 설정집(✓=작가가 쓰기로 정한 항목)]\n[설정]✓ 부적: 용법")])
    assert "아래 <참조맥락>에서 ✓ 표시된" in on_marked and "(위 <참조맥락>" not in on_marked


# ── ③ 대사 라우팅(M3 긍정형) ────────────────────────────────────────────────
def test_story_routing_positive():
    blk = P.build_story_block_draft("- 사건", xml=True)
    assert "<인물보이스>" in blk and "<문체규칙>" in blk    # 앵커 지정
    assert "에서 가져온다" in blk                          # 긍정 소스 지정형
    assert "아니라" not in blk                             # 부정형(핑크엘리펀트) 아님
    # M4: 이어쓰기 콜도 동일 라우팅
    cont = P.build_story_block_continue(["- 남은 줄"], xml=True)
    assert "<확정스토리" in cont and "<인물보이스>" in cont and "<직전장면들>" in cont


# ── ④ 디태거(C1) ────────────────────────────────────────────────────────────
def test_detagger_roundtrip():
    t = '방울 소리가 끊겼다.\n<대사 화자="하지연">규격 외 수거물로 올릴게요.</대사>\n준호는 침묵했다.'
    clean, pairs = lower_dialogue_tags(t)
    assert '"규격 외 수거물로 올릴게요."' in clean and "<대사" not in clean
    assert pairs == [("하지연", '"규격 외 수거물로 올릴게요."')]
    assert dialogue_tag_residue(clean) == 0


def test_detagger_malformed_noop_and_quotes():
    # 이미 따옴표 붙은 내부는 중복 방지 · 화자 속성 부재='' · 미종결 마커는 안전 제거(리터럴 0)
    t = '<대사 화자="준호">"밥 먹었냐."</대사>\n<대사>누구세요.</대사>\n<대사 화자="만물">어어, 그거.'
    clean, pairs = lower_dialogue_tags(t)
    assert '""' not in clean                              # 이중 따옴표 없음
    assert pairs == [("준호", '"밥 먹었냐."'), ("", '"누구세요."')]
    assert dialogue_tag_residue(clean) == 0               # 미종결 <대사 화자="만물"> 도 제거됨
    # OFF 작품(태그 없음) = no-op
    plain = '"안녕."\n그가 웃었다.'
    assert lower_dialogue_tags(plain) == (plain, [])


# ── ⑤ 원장 우회 ─────────────────────────────────────────────────────────────
class _Boom(LLMProvider):
    def chat(self, *a, **k):
        raise RuntimeError("LLM 이 불려선 안 된다")
    def embed(self, t):
        return [[0.0] for _ in t]


def _ont():
    ents = {"j": NS(name="서준호", etype="character", aliases=[]),
            "y": NS(name="하지연", etype="character", aliases=[])}
    return NS(entities=ents, is_actor=lambda t: t == "character")


def test_ledger_tagged_skips_llm():
    text = "하지연이 서류를 내려놓았다. 서준호는 받아 들었다."
    tagged = [("하지연", '"규격 외 수거물로 올릴게요."'), ("서준호", '"항목 있습니까."')]
    led = build_ledger(_Boom(), text, _ont(), tagged=tagged, pov_entity_id="j")   # LLM 안 불려도 성립
    assert [(r["speaker"], r["canon"]) for r in led] == [("하지연", True), ("서준호", True)]
    assert led[0]["text"] == "규격 외 수거물로 올릴게요."   # 따옴표 벗긴 내부 텍스트
    # 화자 속성 부재 → 미상
    led2 = build_ledger(_Boom(), text, _ont(), tagged=[("", '"음."')])
    assert led2[0]["speaker"] == UNKNOWN


def test_ledger_none_falls_back_to_llm():
    # tagged=None(태그 없는 작품·미준수) → 종전 경로. _Boom 은 chat 에서 예외 → 비차단 빈 원장.
    text = '나는 말했다.\n"항목 있습니까."\n그가 답했다.'
    assert build_ledger(_Boom(), text, _ont(), tagged=None) == []
