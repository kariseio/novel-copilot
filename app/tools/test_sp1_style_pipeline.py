# -*- coding: utf-8 -*-
"""SP-1 검증 — 생성 경로 내장 문체 파이프라인 3단(few-shot 분포 조향 + 국소 스팬 수리 + Kiwi 계측). LLM 0콜.

설계 docs/design-sp1-style-pipeline.md §1~§4. 검증 축(사전 등록):
  A. Stage A(few-shot) —
     1) style_fewshot OFF → _draft/_continue system 프롬프트가 도입 전과 **바이트 동일**(주입 0).
     2) 예시 주입 렌더 스냅샷 — ON 이면 문체 블록 뒤에 라벨 + EX1·EX2 원문 바이트 그대로.
     3) draft·continue·(발단 ch1) 공통 주입.
     4) builtin 스킬 슬롯 등록(작가 열람/교체) — examples == sp1_exemplars.json 원문(byte 동일 로드).
  B. Stage B(스팬 수리) —
     5) 상한 산술 — 긴 run 우선 선별(select_repair_spans), 상한 0 = 빈, None = 전부.
     6) 스팬별 격리 — 한 스팬 span_not_found → 그 스팬만 원문 유지(폴백 기록)·전진(나머지 수리).
     7) style_repairs 영속 — changed/before_len/after_len/coverage/fallback 기록(투명성).
     8) 실패 기록 — 무변경 폴백도 항목 남김(침묵 폴백 금지).
     9) 부품 부재/토글 상황 강등 — repair 예외 없이 (원문, []).
  C. Stage C(계측) —
    10) ai_tell 확장 — kiwi{ending_profile, da_streak} additive(기존 KatFishNet 축 보존).
    11) 부품 부재 시 확장 없음(기존 dict 만 — 결측 정직).
  D. 하위호환 —
    12) 구 JSON(style_repairs·ai_tell.kiwi 없는) 바이트 동일 로드(ChapterRecord additive default).

실행: (app/ 에서) py -3.12 -m pytest tools/test_sp1_style_pipeline.py -q
       또는     PYTHONIOENCODING=utf-8 py -3.12 tools/test_sp1_style_pipeline.py
"""
from __future__ import annotations
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot
sys.path.insert(0, str(_HERE))          # tools/

from types import SimpleNamespace

from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.types import ContextBoard, SceneSpec, ChapterRecord, ChapterStatus
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.engine.prompts import (SP1_EXEMPLARS, style_fewshot_block, _SP1_FEWSHOT_LABEL,
                                         _SP1_EXEMPLARS_PATH)
from novelcopilot.engine import style_pipeline as sp

_JSON_PATH = _HERE / "reports" / "sp1_exemplars.json"


class _Bus:
    def emit(self, *a, **k):
        pass


class _Cap:
    """프롬프트 캡처 provider — messages 기록 후 무해한 응답(LLM 0콜). usage 는 harness _track 용 stub."""
    def __init__(self):
        self.captured = []
        self.last_truncated = False
        self.usage = SimpleNamespace(chat_tokens=0, chat_calls=0)

    def chat(self, messages, *a, **k):
        self.captured.append(messages)
        return "본문."

    def chat_json(self, messages, *a, **k):
        self.captured.append(messages)
        return {}

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _gen(style_fewshot: bool):
    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=True, scene_style_anchor=False,
                               style_fewshot=style_fewshot, style_repair=True, style_repair_max_spans=6)
    prov = _Cap()
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    return g, prov


def _draft_sys(g, prov, chapter: int) -> str:
    board = ContextBoard(chapter=chapter)
    scene = SceneSpec(index=0, goal="주인공의 하루", key_events=["전환이 발발한다"])
    g._draft(board, scene, "", last=False, closing=False, chapter_mode=True)
    return prov.captured[-1][0]["content"]   # system


def _continue_sys(g, prov, chapter: int) -> str:
    board = ContextBoard(chapter=chapter)
    g._continue(board, "지금까지 쓴 본문의 마지막 문장.", closing=False,
                recent_tails=None, key_events=["전환이 발발한다"])
    return prov.captured[-1][0]["content"]   # system


# ═════════════════ A. Stage A — few-shot ═════════════════

def test_stageA_off_byte_identical() -> bool:
    """1) style_fewshot OFF → system 프롬프트에 라벨·예시 부재. ON 대비 정확히 few-shot 블록만큼만 차이."""
    g_off, p_off = _gen(style_fewshot=False)
    g_on, p_on = _gen(style_fewshot=True)
    ok = True
    for ch in (1, 3):
        s_off = _draft_sys(g_off, p_off, ch)
        s_on = _draft_sys(g_on, p_on, ch)
        c_off = _continue_sys(g_off, p_off, ch)
        c_on = _continue_sys(g_on, p_on, ch)
        # OFF 엔 라벨·예시 전무
        ok &= (_SP1_FEWSHOT_LABEL not in s_off) and (SP1_EXEMPLARS[0] not in s_off)
        ok &= (_SP1_FEWSHOT_LABEL not in c_off) and (SP1_EXEMPLARS[0] not in c_off)
        # ON == OFF + few-shot 블록(정확히 append — 바이트 동일 복원)
        ok &= (s_on == s_off + g_on.style_fewshot_block)
        ok &= (c_on == c_off + g_on.style_fewshot_block)
    # OFF 인스턴스의 블록 자체가 빈 문자열
    ok &= (g_off.style_fewshot_block == "")
    print(f"[{'OK' if ok else 'FAIL'}] Stage A OFF: system 바이트 동일(라벨·예시 부재)·ON=OFF+block")
    return ok


def test_stageA_render_snapshot() -> bool:
    """2)+3) ON 이면 문체 블록 뒤 라벨 + EX1·EX2 원문 바이트 그대로 — draft·continue·ch1 공통."""
    g, prov = _gen(style_fewshot=True)
    ok = True
    for get in (lambda ch: _draft_sys(g, prov, ch), lambda ch: _continue_sys(g, prov, ch)):
        for ch in (1, 3):   # ch1 발단 변형에도 동일 주입
            s = get(ch)
            ok &= (_SP1_FEWSHOT_LABEL in s)
            ok &= (SP1_EXEMPLARS[0] in s and SP1_EXEMPLARS[1] in s)   # 원문 바이트 그대로
            # 라벨이 문체 블록(문체 규칙) '뒤'에 온다 — 규칙 헤더 위치보다 라벨 위치가 크다
            ok &= (s.index("[웹소설 문체 규칙") < s.index(_SP1_FEWSHOT_LABEL))
    print(f"[{'OK' if ok else 'FAIL'}] Stage A 렌더 스냅샷: 문체 블록 뒤 라벨+EX1/EX2 원문(draft·continue·ch1)")
    return ok


def test_stageA_builtin_skill_registered() -> bool:
    """4) builtin 스킬 슬롯 등록(작가 열람/교체) — examples == sp1_exemplars.json 원문·기본 OFF(어댑터 이중주입 없음)."""
    from novelcopilot.domain.skill import default_skills, _load_sp1_exemplars
    sk = next((s for s in default_skills() if s.id == "builtin_sp1_exemplars"), None)
    ok = sk is not None
    if sk is not None:
        ok &= (sk.builtin is True and sk.enabled is False and sk.point == "chapter")
        ok &= (sk.examples == _load_sp1_exemplars() == SP1_EXEMPLARS)   # 원문 바이트 그대로
    print(f"[{'OK' if ok else 'FAIL'}] Stage A builtin 스킬: 등록·examples 원문·기본 OFF")
    return bool(ok)


def test_stageA_block_empty_when_no_exemplars() -> bool:
    """빈 예시(부재 강등) → 블록 "" (OFF 와 동형·침묵 강등 관측 가능은 loaded 목록 길이로)."""
    ok = (style_fewshot_block([]) == "")
    ok &= (_SP1_FEWSHOT_LABEL in style_fewshot_block(["가짜 예시 A", "가짜 예시 B"]))
    print(f"[{'OK' if ok else 'FAIL'}] Stage A: 빈 예시→빈 블록·비어있지 않으면 라벨 포함")
    return ok


# ═════════════════ B. Stage B — 스팬 수리 ═════════════════

WALL = ('"준비됐어?"\n'
        "그는 고개를 끄덕였다. 문을 열었다. 계단을 내려갔다. 벽을 짚었다. "
        "손전등을 켰다. 어둠이 물러났다. 발을 디뎠다. 숨을 골랐다. 앞으로 나아갔다.\n"
        "계단은 끝없이 이어진다. 정말 끝이 있을까?")


def test_stageB_cap_arithmetic() -> bool:
    """5) 상한 산술 — 긴 run·파편 밀도 상위 우선 선별. 0=빈, None=전부, 여유 상한=원본."""
    fake = [{"kind": "ending_run", "metric": {"run_len": 9}, "sent_start": 0, "span_text": "a"},
            {"kind": "ending_run", "metric": {"run_len": 12}, "sent_start": 10, "span_text": "b"},
            {"kind": "fragment_cluster", "metric": {"n_fragment": 4}, "sent_start": 20, "span_text": "c"}]
    picked2 = sp.select_repair_spans(fake, 2)
    ok = (len(picked2) == 2)
    ok &= ({s["metric"].get("run_len", 0) + s["metric"].get("n_fragment", 0) for s in picked2} == {12, 9})
    # 원문 순서 복원(sent_start 오름차순) — run_len 12 가 sent_start 10 이라 9(sent0) 뒤에 온다
    ok &= [s["sent_start"] for s in picked2] == sorted(s["sent_start"] for s in picked2)
    ok &= (sp.select_repair_spans(fake, 0) == [])
    ok &= (len(sp.select_repair_spans(fake, None)) == 3)
    ok &= (len(sp.select_repair_spans(fake, 9)) == 3)   # 여유 상한 = 전부
    print(f"[{'OK' if ok else 'FAIL'}] Stage B 상한 산술: 긴 run 우선·0=빈·None/여유=전부·원문순 복원")
    return bool(ok)


def test_stageB_detect_and_repair_record() -> bool:
    """7)+8) 실제 수리(무변경 폴백) → text 불변 + style_repairs 항목 기록(침묵 폴백 금지)."""
    class MockGen:
        def revise_prose(self, directive, before_text, span_text="", **kw):
            return before_text   # 무변경 폴백

    new_text, repairs = sp.repair_spans(MockGen(), None, None, 5, WALL, max_spans=6, service=None)
    ok = (new_text == WALL)                    # 무변경 → 본문 불변
    ok &= (len(repairs) == 1)                  # 검출 스팬 1개 기록
    r = repairs[0]
    ok &= (r["kind"] == "ending_run" and r["changed"] is False)
    ok &= ("before_len" in r and "after_len" in r and r["before_len"] == r["after_len"])
    print(f"[{'OK' if ok else 'FAIL'}] Stage B 무변경 폴백: 본문 불변·항목 기록(침묵 금지)")
    return bool(ok)


def test_stageB_actual_repair_updates_text() -> bool:
    """7) 실제 변경 수리 → text 갱신 + changed=True·커버리지 통과 기록."""
    class MockGen:
        def revise_prose(self, directive, before_text, span_text="", **kw):
            if not span_text or span_text not in before_text:
                return before_text
            # 내용어 유지한 채 한 문장으로 흘림(커버리지 가드 통과 목적)
            rw = ("그는 고개를 끄덕이며 문을 열고 계단을 내려가 벽을 짚었고, 손전등을 켜자 어둠이 물러났고, "
                  "발을 디뎌 숨을 고르며 앞으로 나아갔다.")
            return before_text.replace(span_text, rw, 1)

    new_text, repairs = sp.repair_spans(MockGen(), None, None, 5, WALL, max_spans=6, service=None)
    ok = (new_text != WALL) and repairs[0]["changed"] is True
    ok &= (repairs[0]["coverage_passed"] is True)
    ok &= (repairs[0]["after_len"] < repairs[0]["before_len"])   # 벽이 흘려짐(짧아짐)
    print(f"[{'OK' if ok else 'FAIL'}] Stage B 실제 수리: text 갱신·changed=True·커버리지 통과")
    return bool(ok)


def test_stageB_span_isolation() -> bool:
    """6) 스팬별 격리 — 한 스팬 span_not_found → 그 스팬만 폴백 기록·전진(예외로 회차 죽이지 않음)."""
    import tools.st11_span_rewrite as st11
    calls = {"n": 0}
    orig = st11.rewrite_span_via_chassis

    def boom(*a, **k):
        calls["n"] += 1
        raise ValueError("span_not_found")   # 선행 퇴고로 본문 변경 시뮬(격리 대상)

    st11.rewrite_span_via_chassis = boom
    try:
        new_text, repairs = sp.repair_spans(object(), None, None, 5, WALL, max_spans=6, service=None)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok = (new_text == WALL)                       # 실패 스팬 원문 유지(전진 — arm 안 죽음)
    ok &= (len(repairs) == 1 and repairs[0]["fallback"] == "span_not_found")
    ok &= (calls["n"] == 1)
    print(f"[{'OK' if ok else 'FAIL'}] Stage B 스팬 격리: span_not_found 폴백 기록·전진")
    return bool(ok)


def test_stageB_sequential_offset_refresh() -> bool:
    """6b) 순차 다중 스팬 — 앞 스팬 수리가 뒤 스팬 오프셋을 밀어도 재-앵커로 둘 다 수리·내용 보존(격리 정합).

    앞 벽 수리가 길이를 바꾸면 뒤 벽의 원 char_start 가 cur 에서 어긋난다. repair_spans 가 span_text 로
    cur 에서 재-앵커하지 않으면 chassis 내부 _extract_after_span 이 스테일해져 뒤 스팬이 헛폴백된다."""
    W1 = "그는 문을 열었다. 계단을 내려갔다. 벽을 짚었다. 손전등을 켰다. 어둠이 물러났다. 발을 디뎠다. 숨을 골랐다. 앞으로 나아갔다."
    W2 = "그녀는 뒤를 돌아봤다. 손을 뻗었다. 문고리를 잡았다. 힘을 주었다. 문이 열렸다. 빛이 샜다. 눈을 감았다. 다시 떴다."
    two = (W1 + "\n\n계단은 끝없이 이어진다. 정말 끝이 있을까?\n\n" + W2 + "\n\n복도는 조용하다. 아무도 없다.")

    class MockGen:
        def revise_prose(self, directive, before_text, span_text="", **kw):
            if not span_text or before_text.count(span_text) != 1:
                return before_text
            rw = span_text.replace(". ", "고, ").rstrip(".") + "다."   # 내용어 보존 흘림
            return before_text.replace(span_text, rw, 1)

    new_text, repairs = sp.repair_spans(MockGen(), None, None, 5, two, max_spans=6)
    ok = (len(repairs) == 2)
    ok &= all(r["changed"] for r in repairs)                        # 둘 다 수리
    ok &= not any(r.get("fallback") == "stale_offset" for r in repairs)   # 재-앵커로 헛폴백 없음
    ok &= ("문고리" in new_text and "손전등" in new_text)          # 두 벽 내용 모두 보존
    print(f"[{'OK' if ok else 'FAIL'}] Stage B 순차 오프셋: 앞 수리 후 뒤 스팬 재-앵커·둘 다 수리·내용 보존")
    return bool(ok)


def test_stageB_degrade_no_spans() -> bool:
    """9) 스팬 미검출(평이한 지문) → (원문, []) 강등(수리 시도 0)."""
    plain = ("그는 창밖을 오래 바라보았다. 커피는 식어 있었고, 골목엔 아무도 없다.\n"
             '"오늘은 좀 다르네." 그가 중얼거렸다.\n'
             "무언가 달라질 것 같은 예감이 들었지만, 그게 무엇인지는 알 수 없었다.")

    class NeverCalled:
        def revise_prose(self, *a, **k):
            raise AssertionError("스팬 없는데 수리 호출됨")

    new_text, repairs = sp.repair_spans(NeverCalled(), None, None, 5, plain, max_spans=6)
    ok = (new_text == plain and repairs == [])
    print(f"[{'OK' if ok else 'FAIL'}] Stage B 강등: 스팬 미검출→(원문, [])·수리 호출 0")
    return ok


# ═════════════════ C. Stage C — 계측 ═════════════════

def test_stageC_kiwi_metrics_shape() -> bool:
    """10) kiwi_style_metrics 형태 — ending_profile·da_streak 두 핵심 축(advisory 원자료·판정 라벨 0).

    SP-1b 확장: layer(②·G1)·human_band(③·G3)가 additive 병기될 수 있다 — 핵심 두 축은 항상 present(subset 검사)."""
    km = sp.kiwi_style_metrics(WALL)
    if not km:   # 부품 부재 강등 — 빈 dict 면 확장 없음(11 로 커버). 여기선 형태만 검사.
        print("[OK] Stage C: 부품 부재(빈 dict) — 확장 없음(정직 결측)")
        return True
    ok = {"ending_profile", "da_streak"} <= set(km.keys())   # 핵심 두 축 present(SP-1b layer/human_band 는 additive)
    ok &= set(km.keys()) <= {"ending_profile", "da_streak", "layer", "human_band"}   # 알려진 축만(예상 밖 키 0)
    ep, da = km["ending_profile"], km["da_streak"]
    ok &= all(k in ep for k in ("top_template", "top_ratio", "max_run", "unique", "n_ending", "backend"))
    ok &= all(k in da for k in ("max_run", "ratio", "n_past", "n_sent", "backend"))
    ok &= (da["max_run"] >= 6)   # 벽 텍스트라 '~다' 연속 run 크다(원자료 정합)
    # 판정 라벨(verdict/pass/label) 부재 — advisory 원자료만(human_band.meta.advisory=True 는 대역 advisory 표기이지 판정 아님)
    ok &= not any("verdict" in str(v).lower() or "label" in str(k).lower()
                  for k in km for v in [km[k]])
    print(f"[{'OK' if ok else 'FAIL'}] Stage C: kiwi 두 축 present·additive 확장 허용·판정 라벨 0·벽 run 정합")
    return bool(ok)


def test_stageC_ai_tell_additive() -> bool:
    """10)+11) ai_tell 확장이 additive — 기존 KatFishNet 축 보존 + kiwi 확장(부품 가용 시)."""
    from novelcopilot.engine.quality_gates import ai_tell_profile
    base = ai_tell_profile(WALL, set())
    km = sp.kiwi_style_metrics(WALL)
    # copilot._recompute_ai_tell 확장 규칙과 동형: profile = {**base, "kiwi": km} (km 가용 시)
    merged = {**base, "kiwi": km} if km else dict(base)
    ok = all(merged.get(k) == base[k] for k in base)   # 기존 축 전부 보존(값 불변)
    if km:
        ok &= (merged["kiwi"] == km)
    else:
        ok &= ("kiwi" not in merged)   # 부품 부재 시 확장 없음(기존 dict 만)
    print(f"[{'OK' if ok else 'FAIL'}] Stage C ai_tell: additive(기존 축 보존·kiwi 확장/결측 정직)")
    return bool(ok)


# ═════════════════ D. 하위호환 ═════════════════

# ═════════════════ B(e2e). Stage B 하네스 삽입 — generate() 관통 ═════════════════

def test_stageB_harness_insertion_e2e() -> bool:
    """Stage B 삽입 관통 — generate() 가 reflow 후·최종 체크 전 repair_spans 를 호출하고, 그 반환 본문이
    최종 산출/색인의 입력이 되며, style_repairs·usage_by_stage['style_repair'] 가 영속된다(스파이·LLM 0콜).

    실 Settings(style_repair=True) 로 하네스를 태우되 style_pipeline.repair_spans 를 스파이로 대체 —
    수리 로직 자체는 위 유닛 테스트가 커버, 여기선 '하네스 배선(호출·본문 반영·영속·계상)'만 검증."""
    from novelcopilot.config import Settings

    settings = Settings()   # 실 설정: style_repair=True·style_repair_max_spans=6
    object.__setattr__(settings, "humanize", False)   # HM-1b 흡수: humanize ON 이면 HM-1 경로가 Stage B 를 대신 태우므로
    #   이 Stage B 배선 테스트는 humanize=OFF 로 Stage B 폴백 경로를 직접 검증(의도 보존·개편 허용). HM-1 e2e 는 별도 테스트.
    prov = _Cap()

    class _Ont:
        entities = {}
        rules = []
        def is_actor(self, et): return False
        def canon_facts(self, ids, ch): return []
        def canon_relations(self, ids, ch): return []
        def scan_present_ids(self, text): return []
    class _Chk:
        def check_text(self, text, ont, ch, ids, pov=""):
            return SimpleNamespace(violations=[], hard=[], claims=[])
    class _Rag:
        def index_chapter(self, ch, text): return 1
        def search(self, *a, **k): return []
    class _Wiki:
        def ingest_chapter(self, *a, **k): return 0
        def retrieve(self, *a, **k): return []

    spy = {"called": 0, "in_text": None}
    MARK = "\n\n[SP1-REPAIRED-MARK]"

    def fake_repair(generator, ontology, checker, chapter_no, text, **kw):
        spy["called"] += 1
        spy["in_text"] = text
        spy["max_spans"] = kw.get("max_spans")
        # 수리가 일어난 것으로 시뮬 — 본문에 마커를 붙여 '반환 본문이 하류로 흐르는지' 관측
        return text + MARK, [{"kind": "ending_run", "changed": True, "before_len": 10, "after_len": 8,
                              "coverage_passed": True, "fallback": None}]

    # 하네스는 함수 안에서 from .style_pipeline import repair_spans 하므로 모듈 심볼을 패치한다.
    import novelcopilot.engine.style_pipeline as spmod
    real = spmod.repair_spans
    spmod.repair_spans = fake_repair
    try:
        g = ChapterGenerator(prov, checker=_Chk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        rec = g.generate(2, beat, _Ont(), _Rag(), _Wiki(), prev_chapter_text="", story_so_far="")
    finally:
        spmod.repair_spans = real

    ok = (spy["called"] == 1)                                  # 정확히 1회 호출(회차당)
    ok &= (spy["max_spans"] == settings.style_repair_max_spans)  # config 상한 전달
    ok &= (MARK in (rec.text or ""))                           # 수리 반환 본문이 최종 본문에 반영(하류 정합)
    ok &= (len(rec.style_repairs) == 1 and rec.style_repairs[0]["changed"] is True)  # 영속
    ok &= ("style_repair" in rec.usage_by_stage)               # usage 계상(스테이지 키)
    print(f"[{'OK' if ok else 'FAIL'}] Stage B e2e: generate() 관통 호출·본문 반영·style_repairs·usage 계상")
    return bool(ok)


def test_stageB_harness_off_no_call() -> bool:
    """Stage B OFF(style_repair=False) → repair_spans 미호출·style_repairs 빈·본문 마커 부재(바이트 무영향)."""
    from novelcopilot.config import Settings

    settings = Settings()
    object.__setattr__(settings, "style_repair", False)   # OFF
    object.__setattr__(settings, "humanize", False)   # HM-1b 흡수: humanize 도 OFF 여야 두 문체 경로 모두 미호출(바이트 무영향 계약)
    prov = _Cap()

    class _Ont:
        entities = {}
        rules = []
        def is_actor(self, et): return False
        def canon_facts(self, ids, ch): return []
        def canon_relations(self, ids, ch): return []
        def scan_present_ids(self, text): return []
    class _Chk:
        def check_text(self, *a, **k): return SimpleNamespace(violations=[], hard=[], claims=[])
    class _Rag:
        def index_chapter(self, *a, **k): return 1
        def search(self, *a, **k): return []
    class _Wiki:
        def ingest_chapter(self, *a, **k): return 0
        def retrieve(self, *a, **k): return []

    import novelcopilot.engine.style_pipeline as spmod
    real = spmod.repair_spans
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return a[4], []

    spmod.repair_spans = spy
    try:
        g = ChapterGenerator(prov, checker=_Chk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        rec = g.generate(2, beat, _Ont(), _Rag(), _Wiki())
    finally:
        spmod.repair_spans = real

    ok = (called["n"] == 0 and rec.style_repairs == [] and "style_repair" not in rec.usage_by_stage)
    print(f"[{'OK' if ok else 'FAIL'}] Stage B OFF: repair_spans 미호출·style_repairs 빈·usage 무계상")
    return ok


def test_backcompat_old_json_load() -> bool:
    """12) 구 JSON(style_repairs·ai_tell.kiwi 없는) 로드 → default 채워짐·재직렬화 정합(additive)."""
    old = {"chapter": 3, "status": "FINALIZED", "text": "본문", "ai_tell": {"comma_per_100": 1.2}}
    rec = ChapterRecord.model_validate(old)
    ok = (rec.style_repairs == [])                 # additive default
    ok &= (rec.ai_tell == {"comma_per_100": 1.2})  # kiwi 없어도 그대로(확장은 재계산 경로에서만)
    ok &= (rec.status == ChapterStatus.FINALIZED)
    # 신 필드 포함 재직렬화 → 재로드 바이트 정합
    rt = ChapterRecord.model_validate(json.loads(rec.model_dump_json()))
    ok &= (rt.style_repairs == [] and rt.ai_tell == {"comma_per_100": 1.2})
    print(f"[{'OK' if ok else 'FAIL'}] 하위호환: 구 JSON 로드·style_repairs default·재직렬화 정합")
    return bool(ok)


def test_exemplars_json_byte_identity() -> bool:
    """예시 데이터 SSOT — prompts 로드 == reports/sp1_exemplars.json 원문 EX1/EX2(수정 금지)."""
    data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    ok = (SP1_EXEMPLARS == [data["EX1"], data["EX2"]])
    ok &= (_SP1_EXEMPLARS_PATH.resolve() == _JSON_PATH.resolve())   # 엔진이 이 파일을 읽는다
    print(f"[{'OK' if ok else 'FAIL'}] 예시 SSOT: prompts 로드 == JSON 원문(EX1/EX2 byte 동일)")
    return bool(ok)


def main() -> int:
    results = [
        test_stageA_off_byte_identical(),
        test_stageA_render_snapshot(),
        test_stageA_builtin_skill_registered(),
        test_stageA_block_empty_when_no_exemplars(),
        test_stageB_cap_arithmetic(),
        test_stageB_detect_and_repair_record(),
        test_stageB_actual_repair_updates_text(),
        test_stageB_span_isolation(),
        test_stageB_sequential_offset_refresh(),
        test_stageB_degrade_no_spans(),
        test_stageB_harness_insertion_e2e(),
        test_stageB_harness_off_no_call(),
        test_stageC_kiwi_metrics_shape(),
        test_stageC_ai_tell_additive(),
        test_backcompat_old_json_load(),
        test_exemplars_json_byte_identity(),
    ]
    print("\nSP-1(문체 파이프라인 3단) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_sp1_style_pipeline_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
