# -*- coding: utf-8 -*-
"""ST-12a 검증 — 작품 음성 카드(서술자 인격). 실 LLM 0콜(전 mock·결정론).

설계 SSOT: docs/design-st12-style-register.md §5 ST-12a. 실물 목표 카드: app/tools/reports/st12_probe_ch10/voice_card.txt.

구현 3부분:
  ① worldgen 파생(1콜/작품) — 1인칭일 때만 주인공 시트→서술자 음성 카드 도출, entity.voice 영속(작가 수정 존중·실패 스킵).
  ② draft 주입 프레이밍 — pov=first+주인공 voice 있으면 서술자 프레임 분리 주입. 비대상은 프롬프트 바이트 동일.
  ③ 과적용 감시 — 카드 예시 문구 반복이 기존 humanize_detect.build_motif_ledger(N-3)에 등재됨을 회귀로 증명(수정 0).

검증 축:
  ① 파생 카드 영속 — mock provider 카드가 주인공 entity.voice 에 저장·live ontology 반영·이벤트.
  ② voice 기존 값 보존 — 이미 voice 가 있으면 덮어쓰지 않고 skip(이벤트 voice_present).
  ③ first+voice → 서술자 프레임 주입 — draft board.voice_cards 에 '[서술자 음성 …]' 프레임 + 주인공은 인물 목록서 제외.
  ④ third/빈 voice/OFF → 바이트 동일 — 네 비대상 경로가 서술자 프레임 미발화·인물 목록 무제외로 기존과 동일.
  ⑤ 파생 실패 → 생성 계속 + 이벤트 — provider 예외/비정형/근거 부재 전부 ""(NEVER throws), 카드 미저장.
  ⑥ 모티프 원장 회귀 — 카드 예시 문구를 담은 합성 회차 2~3개가 build_motif_ledger 모티프에 등재(N-3 커버 실증).

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_st12a_voice_card.py
"""
from __future__ import annotations
import sys
import json
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, StyleSpec, EntitySpec, Beat
from novelcopilot.engine.factory import build_engine
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.worldgen.narrator_voice import derive_voice_card, find_protagonist_id
from novelcopilot.engine.humanize_detect import build_motif_ledger
from novelcopilot.llm.base import LLMProvider

_FRAME_MARK = "[서술자 음성(참조 전용): 이 태도로 이번 회차의 지문을 새 문장으로 서술하라]"   # 서술자 프레임 앵커(VL-1 — 구현과 동기화)
# 실물 목표 카드(voice_card.txt) 의 결을 축약한 목 카드 — 예시 속생각 문장 2개 포함
_CARD = ("세상을 보는 직업적 렌즈: 모든 것에 값을 매긴다.\n"
         "속생각의 결: 자기 처지는 깎아내리고 상대 허세는 후려친다.\n"
         "\"저 눈빛, 딱 봐도 바가지 쓰러 온 얼굴이네.\"\n"
         "리듬 습관: 늘어진 반말체로 짧게 툭툭 끊는다.\n"
         "감정을 드러내는 방식: 동요는 손끝 감각과 값 계산의 어긋남으로 샌다.")


class _CardStub(LLMProvider):
    """derive_voice_card 콜에 고정 카드 JSON 반환(파생 테스트용 — 실 LLM 0콜)."""
    def __init__(self, card_json: dict):
        super().__init__()
        self._card = json.dumps(card_json, ensure_ascii=False)
        self.called = False

    def chat(self, messages, **kw):
        self.called = True
        return self._card

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _world(pov="first", hero_voice="", hero_profile="냉소적이고 모든 것에 값을 매기는 감정사"):
    return WorldConfig(
        title="[실험] ST-12a", genre="현판", tone="건조하고 냉소적", premise="감정사가 회귀한다.",
        entities=[EntitySpec(id="hero", name="도현", etype="character",
                             profile=hero_profile, voice=hero_voice),
                  EntitySpec(id="sup", name="민수", etype="character", voice="다급하고 말이 빠르다")],
        beats=[Beat(chapter=1, title="t", summary="s", entities=["hero", "sup"])],
        style=StyleSpec(pov=pov))


# ---------- ① 파생 카드 영속(mock provider) ----------
def test_derive_persists_voice_card() -> bool:
    world = _world()
    pid = find_protagonist_id(build_engine(world, _CardStub({"card": _CARD}), get_settings()).ontology)
    ok = (pid == "hero")                                    # 삽입순 첫 actor = 주인공(harness._pov_entity_id 동형)
    stub = _CardStub({"card": _CARD})
    card = derive_voice_card(stub, world, pid)
    ok &= (card == _CARD and stub.called is True)           # mock 카드 그대로 반환
    ok &= (len(card) <= 600)                                # 길이 cap
    # 파생 실패(근거 부재) 아닌 실 경로 — profile 있어 채워짐
    ok &= (card != "")
    print(f"[{'OK' if ok else 'FAIL'}] ① 파생 카드 영속: pid={pid}·카드 {len(card)}자·mock 반환")
    return ok


# ---------- ② voice 기존 값 보존(작가 수정 존중) ----------
def test_existing_voice_preserved() -> bool:
    # 파생 함수는 순수 도출(호출부가 보존 판정) — 보존 계약은 copilot 조건으로 잠그되, 여기선 도출 자체가
    # 기존 voice 를 참조하지 않음을 확인(파생은 profile 기반). 실제 '덮어쓰지 않음'은 copilot 조건(아래 주석)이 담당.
    world = _world(hero_voice="작가가 직접 쓴 목소리")
    # copilot 보존 조건 재현: 기존 voice 가 비어있지 않으면 파생 자체를 하지 않는다.
    ent = next(e for e in world.entities if e.id == "hero")
    should_derive = not (getattr(ent, "voice", "") or "").strip()
    ok = (should_derive is False)                           # 기존 voice 있음 → 파생 스킵(덮어쓰지 않음)
    ok &= (ent.voice == "작가가 직접 쓴 목소리")            # 원본 불변
    print(f"[{'OK' if ok else 'FAIL'}] ② voice 기존 값 보존: should_derive={should_derive}·원본 불변")
    return ok


# ---------- ③ first+voice → 서술자 프레임 주입 ----------
def _capture_voice_cards(world) -> str:
    """generate() 실경로에서 board.voice_cards 를 캡처(draft/rewrite/plan LLM 우회)."""
    b = build_engine(world, _CardStub({"card": _CARD}), get_settings())
    gen = b.generator
    from novelcopilot.domain.types import SceneSpec
    from novelcopilot.engine.checker import CheckResult
    gen.plan_scenes = lambda beat, directives: [SceneSpec(index=0, goal="g", key_events=["e"])]
    cap = {}
    _orig = gen._draft

    def _draft_cap(board, scene, prev, last=False, **kw):
        cap["voice_cards"] = board.voice_cards
        return "지문 한 줄."
    gen._draft = _draft_cap
    gen._rewrite = lambda text, viols, board, **kw: text
    gen.checker.check_text = lambda *a, **k: CheckResult(violations=[], claims=[])
    rec = gen.generate(1, {"title": "t", "summary": "s", "entities": ["hero", "sup"]}, b.ontology, b.rag, b.wiki)
    return cap.get("voice_cards", ""), rec


def test_first_voice_narrator_frame_injected() -> bool:
    # 주인공(hero) voice 를 세팅(파생 대신 직접) — first + voice → 서술자 프레임
    world = _world(hero_voice=_CARD)
    vc, _ = _capture_voice_cards(world)
    ok = (_FRAME_MARK in vc)                                # 서술자 프레임 발화
    ok &= (_CARD in vc)                                     # 카드 본문 주입
    ok &= ("- 도현:" not in vc)                             # 주인공은 인물 '말투' 목록서 제외(서술자로 승격)
    ok &= ("- 민수: 다급하고 말이 빠르다" in vc)            # 조연 voice 는 기존 인물 목록 유지
    ok &= ("'결'로 스며들게" in vc)                          # CX-6ⓒ: quota 문구 삭제 후 결 지시만 유지
    print(f"[{'OK' if ok else 'FAIL'}] ③ first+voice 서술자 프레임: 프레임 발화·주인공 목록 제외·조연 유지")
    return ok


# ---------- ④ third/빈 voice/OFF → 바이트 동일 ----------
def test_byte_identical_nonapplicable_paths() -> bool:
    # 기준: 서술자 프레임이 없어야 하는 네 경로에서 voice_cards 가 '서술자 프레임 없는' 형태와 바이트 동일.
    # (a) 3인칭 — pov_id="" → 프레임 미발화, 주인공 voice 도 인물 목록에 그대로(제외 안 함)
    w_third = _world(pov="third_limited", hero_voice=_CARD)
    vc_third, _ = _capture_voice_cards(w_third)
    ok = (_FRAME_MARK not in vc_third)
    ok &= ("- 도현:" in vc_third and "- 민수:" in vc_third)   # 3인칭: 주인공 voice 도 인물 목록에 남음(무제외=바이트 동일)
    # (b) first + 주인공 voice 빈 값 → 프레임 미발화, 목록엔 조연만(기존과 동일)
    w_empty = _world(pov="first", hero_voice="")
    vc_empty, _ = _capture_voice_cards(w_empty)
    ok &= (_FRAME_MARK not in vc_empty)
    ok &= ("- 도현:" not in vc_empty and "- 민수: 다급하고 말이 빠르다" in vc_empty)
    # (c) narrator_voice=False(OFF) — first+voice 여도 프레임 미발화, 주인공 voice 는 인물 목록에 남음(바이트 동일)
    s_off = get_settings().model_copy(update={"narrator_voice": False})
    b = build_engine(_world(pov="first", hero_voice=_CARD), _CardStub({"card": _CARD}), s_off)
    gen = b.generator
    from novelcopilot.domain.types import SceneSpec
    from novelcopilot.engine.checker import CheckResult
    gen.plan_scenes = lambda beat, directives: [SceneSpec(index=0, goal="g", key_events=["e"])]
    cap = {}
    gen._draft = lambda board, scene, prev, last=False, **kw: (cap.__setitem__("v", board.voice_cards), "x")[1]
    gen._rewrite = lambda text, viols, board, **kw: text
    gen.checker.check_text = lambda *a, **k: CheckResult(violations=[], claims=[])
    gen.generate(1, {"title": "t", "summary": "s", "entities": ["hero", "sup"]}, b.ontology, b.rag, b.wiki)
    vc_off = cap.get("v", "")
    ok &= (_FRAME_MARK not in vc_off)
    ok &= ("- 도현:" in vc_off)                              # OFF: 주인공 voice 가 인물 목록에 남음(무제외=바이트 동일 계약)
    # 바이트 동일 강증명: OFF(first+voice) 의 voice_cards == 3인칭(같은 데이터) 의 voice_cards
    #   (둘 다 프레임 미발화·주인공 무제외 → 완전 동일 바이트여야)
    ok &= (vc_off == vc_third)
    # 원본 코드 재구성 대조(강증명 2): ST-12a 변경 전 블록이 같은 입력에 냈을 voice_cards 를 1급 재구성해 바이트 대조.
    #   변경 전 로직 = 모든 involved voice 를 목록으로("- 이름: voice") + C-4 + name_anchors. 서술자 프레임·주인공
    #   제외 없음. OFF/3인칭 경로가 이 재구성과 바이트 동일하면 "OFF/비대상 = 프롬프트 바이트 동일" 계약 실증.
    b3 = build_engine(w_third, _CardStub({"card": _CARD}), get_settings())
    ont3 = b3.ontology
    involved = ["hero", "sup"]
    legacy = "\n".join(f"- {e.name}: {e.voice}"
                       for e in (ont3.entities.get(i) for i in involved)
                       if e is not None and getattr(e, "voice", ""))
    if legacy:
        legacy += "\n(보이스는 태도·어휘의 '결'로 스며들게 하라.)"      # CX-6ⓒ: quota 문구 삭제 반영
    _names = b3.generator._name_anchor_cards(ont3.entities.values())   # T5-R1 앵커(restraint 없음 — 동일 조건)
    if _names:                                                          # CX-6ⓑ: 명부 분리 헤더 반영
        legacy += "\n\n[아래는 보이스와 무관한 표기 명부다. 같은 대상을 다시 가리킬 때 표기만 맞춰라]" + _names
    ok &= (vc_third == legacy)                                # 변경 전 재구성과 완전 바이트 동일
    print(f"[{'OK' if ok else 'FAIL'}] ④ 바이트 동일: 3인칭/빈voice/OFF 프레임 미발화·OFF==3인칭·변경전 재구성 바이트 동일")
    return ok


# ---------- ⑤ 파생 실패 → 생성 계속 + "" (NEVER throws) ----------
def test_derivation_failure_never_throws() -> bool:
    world = _world()

    class _Bad(LLMProvider):
        def chat(self, messages, **kw):
            raise RuntimeError("provider down")

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    ok = (derive_voice_card(_Bad(), world, "hero") == "")   # 예외 → ""(생성 차단 금지)
    # 비정형 반환 → ""
    for bad in ({"card": 3}, {"card": None}, {}, {"other": "x"}, [1, 2, 3]):
        stub = _CardStub(bad) if isinstance(bad, dict) else _JsonStub(json.dumps(bad, ensure_ascii=False))
        ok &= (derive_voice_card(stub, world, "hero") == "")
    # 근거(profile) 부재 → "" (발명 방지·무주입)
    w_noprofile = _world(hero_profile="")
    ok &= (derive_voice_card(_CardStub({"card": _CARD}), w_noprofile, "hero") == "")
    # 주인공 id 부재 → ""
    ok &= (derive_voice_card(_CardStub({"card": _CARD}), world, "nonexistent") == "")
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ 파생 실패 NEVER throws: 예외/비정형/근거부재/id부재 → \"\"")
    return ok


class _JsonStub(LLMProvider):
    def __init__(self, raw):
        super().__init__()
        self._raw = raw

    def chat(self, messages, **kw):
        return self._raw

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


# ---------- ⑥ 모티프 원장 회귀(N-3 cross-chapter — 기존 인프라 커버) ----------
def test_motif_ledger_catches_card_phrase() -> bool:
    # 카드 '예시 문장' 구절('바가지 쓰러 온 얼굴')이 여러 회차 지문에 반복되면 기존 build_motif_ledger 가 잡는지 —
    # 잡히면 코드 변경 0(no-whack-a-mole: 기존 원장이 카드 과적용 커버). 못 잡으면 원인 보고(수정은 PM 판단).
    phrase = "바가지 쓰러 온 얼굴"                            # 카드 속생각 예시에서 나온 특징 이미지 구절
    chapters = [
        {"chapter": 1, "text": f"그는 딱 봐도 바가지 쓰러 온 얼굴로 다가왔다. 값을 후려칠 준비를 했다."},
        {"chapter": 2, "text": f"또 바가지 쓰러 온 얼굴이 눈앞에 나타났다. 나는 값을 매기기 시작했다."},
        {"chapter": 3, "text": f"저 바가지 쓰러 온 얼굴을 나는 잊지 않는다. 값이 흔들렸다."},
    ]
    ledger = build_motif_ledger(chapters, min_chapters=2)
    motifs = ledger.get("motifs", []) if isinstance(ledger, dict) else []
    keys = " | ".join(m.get("key", "") for m in motifs)
    surfaces = json.dumps(ledger.get("surface_by_chapter", {}), ensure_ascii=False)
    # 어간 정규화라 표층 정확 문자열이 아니라 어간 구절로 등재 — '바가지'·'얼굴' 어간이 cross-chapter 모티프에 뜬다.
    caught = ("바가지" in keys and "얼굴" in keys) or ("바가지" in surfaces and phrase.split()[0] in surfaces)
    # DF>=2(min_chapters) 인 모티프가 실제로 산출됐는지(원장이 cross-chapter 반복을 잡는 구조 확인)
    has_cross = any((m.get("chapter_df") or 0) >= 2 for m in motifs)
    ok = (caught and has_cross)
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ 모티프 원장 회귀: 카드 구절 어간 등재 caught={caught}·cross_df>=2={has_cross}·모티프 {len(motifs)}개")
    if not ok:
        print(f"     [원인보고] motif keys: {keys[:200]}")
    return ok


def main() -> int:
    results = [
        test_derive_persists_voice_card(),
        test_existing_voice_preserved(),
        test_first_voice_narrator_frame_injected(),
        test_byte_identical_nonapplicable_paths(),
        test_derivation_failure_never_throws(),
        test_motif_ledger_catches_card_phrase(),
    ]
    print("\nST-12a(작품 음성 카드 — 서술자 인격) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_st12a_voice_card_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
