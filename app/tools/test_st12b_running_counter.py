# -*- coding: utf-8 -*-
"""ST-12b 검증 — draft 러닝 카운터 폐루프(CAPEL 패턴). 실 LLM 0콜(전 mock·결정론).

설계 SSOT: docs/design-st12-style-register.md §5 ST-12b.

구현: novelcopilot/engine/style_counter.py(결정론 산술) + harness.ChapterGenerator.generate() 이어쓰기 루프에
  청크별 계측→대역 밖이면 수치 상태블록을 _continue 프롬프트에 주입. config.style_running_counter(기본 OFF).

검증 축(설계 테스트 목록):
  ① 쿼터 산술 결정론(경계: top_n/t−n 음수→0, 상한/하한 clamp)
  ② in-band 무주입(대역 안 → block "")
  ③ 대역 밖 → _continue 프롬프트에 상태블록 실림(mock provider 캡처)
  ④ OFF 바이트 동일(generate() 전체 _continue 프롬프트 — 러닝 카운터 ON/OFF·기저 대조)
  ⑤ tools import 실패 강등(monkeypatch 로 kiwi_metrics import 차단 → 무주입·생성 계속)
  ⑥ 상태블록에 최빈 종결형 실물 미포함(pink-elephant lint) + 부정 예시 토큰 부재
  ⑦ draft_ctx.style_counter 기록(ON) / 키 부재(OFF)

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_st12b_running_counter.py
"""
from __future__ import annotations
import sys
import builtins
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/

from types import SimpleNamespace

from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.types import ContextBoard, SceneSpec, ChapterRecord
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.engine import style_counter as sc


# ── 상태블록 앵커(구현과 동기화) ──────────────────────────────────────────────
_BLOCK_MARK = "[서술 리듬 현황 — 코드 계측]"
_BLOCK_BAND = "사람 손글의 대역은 35~60%다"
_BLOCK_MENU = "리듬을 되돌려라"


class _Bus:
    def emit(self, *a, **k):
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# ① 쿼터 산술 결정론(경계)
# ═══════════════════════════════════════════════════════════════════════════════
def test_quota_arithmetic() -> bool:
    ok = True
    # raw = ceil(top_n/t − n). t=0.55 기본.
    # (a) 음수(이미 목표 이하) → raw=0 → 상태블록 낼 땐 하한 3
    ok &= (sc.quota_k(top_n=1, n_ending=100, remaining_sents=50) == 3)   # 1/.55−100 = −98.2 → 0 → 3
    # (b) 정상 + 넉넉한 remaining: ceil(80/.55 − 100)=ceil(45.45)=46, 상한 max(3,50)=50 → 46
    ok &= (sc.quota_k(top_n=80, n_ending=100, remaining_sents=50) == 46)
    # (c) 상한 clamp: 위 raw 46 이지만 remaining 5 → max(3,5)=5 로 절단
    ok &= (sc.quota_k(top_n=80, n_ending=100, remaining_sents=5) == 5)
    # (d) remaining None → 상한 가드 미적용(상한=3) → 3
    ok &= (sc.quota_k(top_n=80, n_ending=100, remaining_sents=None) == 3)
    # (e) top_n=0 또는 n=0 → 0(계측 근거 없음, 상태블록 안 나는 경로)
    ok &= (sc.quota_k(top_n=0, n_ending=100, remaining_sents=50) == 0)
    ok &= (sc.quota_k(top_n=5, n_ending=0, remaining_sents=50) == 0)
    # (f) 하한 우선: raw>0 이지만 작아 3 미만이면 3으로 승격. ceil(21/.55−30)=ceil(8.18)=9 → remaining 2 → max(3,2)=3
    ok &= (sc.quota_k(top_n=21, n_ending=30, remaining_sents=2) == 3)
    # (g) estimate_remaining_sents: 근거 부족 → None
    ok &= (sc.estimate_remaining_sents(text="x" * 5000, norm=4000, n_ending=20) is None)   # 이미 norm 초과
    ok &= (sc.estimate_remaining_sents(text="", norm=4000, n_ending=0) is None)            # n_ending 0
    est = sc.estimate_remaining_sents(text="가" * 1000, norm=3000, n_ending=20)            # (3000−1000)/(1000/20)=40
    ok &= (est == 40)
    print(f"[{'OK' if ok else 'FAIL'}] ① 쿼터 산술 결정론: 음수→0→하한3·상/하한 clamp·remaining 추정")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# ② in-band 무주입
# ═══════════════════════════════════════════════════════════════════════════════
def test_in_band_no_injection() -> bool:
    ok = True
    # in_band 판정 (top_ratio ≤ 0.60 and max_run ≤ 14)
    ok &= (sc.in_band(0.5, 10) is True)
    ok &= (sc.in_band(0.60, 14) is True)       # 경계 포함(≤)
    ok &= (sc.in_band(0.61, 10) is False)      # top 초과
    ok &= (sc.in_band(0.5, 15) is False)       # run 초과
    ok &= (sc.in_band(None, None) is True)     # 결측 → 무주입 간주
    # compute: 대역 안 텍스트(종결 다양) → block "" · injected False
    # regex backend 로도 종결 다양한 짧은 문장열은 top_ratio 낮음
    txt_diverse = "그가 웃는다. 나는 앉았다. 비가 온다. 문이 닫혔지. 바람이 분다. 그녀가 왔다."
    r = sc.compute(txt_diverse, norm=4000)
    ok &= (r["block"] == "")
    ok &= (r["debug"] is not None and r["debug"]["injected"] is False)
    print(f"[{'OK' if ok else 'FAIL'}] ② in-band 무주입: 경계·결측·대역 안 텍스트 block=''·injected=False")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# ③ 대역 밖 → 상태블록 생성(compute 직접) + _continue 프롬프트에 실림(mock)
# ═══════════════════════════════════════════════════════════════════════════════
def test_out_of_band_block_and_injection() -> bool:
    ok = True
    # 대역 밖 텍스트: 동일 종결('열었다' → 종결형 '었다')을 12문장 반복 → top_ratio=1.0(regex·kiwi 양 backend 모두 대역 밖).
    #   varied 종결은 regex backend(말미 2음절 근사)에서 다른 키로 흩어져 대역 안으로 보이므로(kiwi 라면 EF 템플릿 동일),
    #   backend 무관 결정론 검증을 위해 동일 종결 반복 픽스처를 쓴다(CI 는 kiwipiepy 미설치 → regex 경로).
    txt = "\n".join(["그는 문을 열었다."] * 12)
    r = sc.compute(txt, norm=8000)
    ok &= (r["block"] != "")                                # 대역 밖 → 상태블록 생성
    ok &= (_BLOCK_MARK in r["block"] and _BLOCK_BAND in r["block"])
    ok &= (r["debug"]["injected"] is True and r["debug"]["quota_k"] >= 3)

    # _continue 프롬프트에 실림(직접 호출 — style_state 인자 전달) — mock provider 캡처
    class _Cap:
        def __init__(self):
            self.captured = []
            self.last_truncated = False

        def chat(self, messages, *a, **k):
            self.captured.append(messages)
            return "이어지는 본문."

        def chat_json(self, messages, *a, **k):
            self.captured.append(messages)
            return {}

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=True, scene_style_anchor=False,
                               style_running_counter=True)
    prov = _Cap()
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    board = ContextBoard(chapter=3)
    g._continue(board, "지금까지 쓴 본문.", closing=False, recent_tails=None,
                key_events=["e"], style_state=r["block"])
    user = prov.captured[-1][1]["content"]
    ok &= (_BLOCK_MARK in user and _BLOCK_MENU in user)     # 상태블록이 user 메시지 말미에 실림
    print(f"[{'OK' if ok else 'FAIL'}] ③ 대역 밖 상태블록 생성 + _continue 프롬프트 주입(k={r['debug']['quota_k']})")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# ④ OFF 바이트 동일 — style_state="" 이면 _continue 프롬프트 바이트가 기저와 완전 동일
# ═══════════════════════════════════════════════════════════════════════════════
def _continue_user(style_running_counter: bool, style_state: str = "") -> str:
    class _Cap:
        def __init__(self):
            self.captured = []
            self.last_truncated = False

        def chat(self, messages, *a, **k):
            self.captured.append(messages)
            return "본문."

        def chat_json(self, messages, *a, **k):
            self.captured.append(messages)
            return {}

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=True, scene_style_anchor=False,
                               style_running_counter=style_running_counter)
    prov = _Cap()
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    board = ContextBoard(chapter=3)
    g._continue(board, "지금까지 쓴 본문의 마지막 문장.", closing=False, recent_tails=None,
                key_events=["e"], style_state=style_state)
    return prov.captured[-1][1]["content"]


def test_off_byte_identical() -> bool:
    # 기저(러닝 카운터 없는 세계 — style_state 인자 자체를 default "" 로) vs style_state="" 명시
    baseline = _continue_user(style_running_counter=False)          # OFF · style_state 기본 ""
    explicit_empty = _continue_user(style_running_counter=True, style_state="")  # ON 이지만 빈 상태블록
    ok = (baseline == explicit_empty)                                # "" 이면 완전 바이트 동일
    # 상태블록이 붙으면(대역 밖) 프롬프트가 달라져야(주입이 실제로 바이트를 바꾼다 — 무주입 계약의 대우 증명)
    block = "\n\n[서술 리듬 현황 — 코드 계측]\n… 최소 5문장."
    with_block = _continue_user(style_running_counter=True, style_state=block)
    ok &= (with_block != baseline and with_block.endswith(block))
    ok &= (with_block[:len(baseline)] == baseline)                   # 상태블록은 기저 프롬프트 '말미'에 순수 append
    print(f"[{'OK' if ok else 'FAIL'}] ④ OFF 바이트 동일: style_state='' == 기저 · 상태블록은 말미 순수 append")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ tools import 실패 강등 — kiwi_metrics import 차단 → compute 무주입·NEVER throws
# ═══════════════════════════════════════════════════════════════════════════════
def test_tools_import_failure_degrades() -> bool:
    txt = "\n".join(["그는 문을 열었다."] * 12)   # 대역 밖이었을 텍스트
    _orig_import = builtins.__import__

    def _blocked_import(name, *a, **k):
        if name == "tools.kiwi_metrics" or name.startswith("tools.kiwi_metrics"):
            raise ImportError("blocked for test")
        return _orig_import(name, *a, **k)

    builtins.__import__ = _blocked_import
    try:
        r = sc.compute(txt, norm=8000)          # import 차단 → measure None → 무주입(강등)
    finally:
        builtins.__import__ = _orig_import
    ok = (r["block"] == "")                       # 조용한 강등(NEVER throws · block 무주입)
    ok &= (r["debug"] is not None and r["debug"]["injected"] is False and r["debug"]["backend"] is None)
    # 강등 후 정상 복귀(import 복원되면 다시 계측 — 상태 오염 없음)
    r2 = sc.compute(txt, norm=8000)
    ok &= (r2["block"] != "")                      # 복원되면 대역 밖 → 다시 주입
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ tools import 실패 강등: 무주입·backend None·NEVER throws·복귀 정상")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# ⑥ pink-elephant lint — 상태블록에 '측정된 최빈 종결형 실물' 미포함 + 부정 예시 토큰 부재
# ═══════════════════════════════════════════════════════════════════════════════
def test_pink_elephant_lint() -> bool:
    # 최빈 종결형이 '었다' 인 대역 밖 텍스트 → 상태블록엔 '었다'(측정된 회피 대상 실물)가 절대 없어야
    txt = "\n".join(["그는 문을 열었다."] * 12)
    m = sc.measure(txt)
    r = sc.compute(txt, norm=8000)
    ok = (r["block"] != "")
    # 측정된 최빈 종결형 실물 문자열(top_template)이 상태블록에 노출되지 않음(pink-elephant 핵심)
    top_tpl = m["top_template"] if m else None
    if top_tpl:
        ok &= (top_tpl not in r["block"])            # 회피 대상 실물 미노출
    ok &= ("었다" not in r["block"])                 # 대표 회피 어미 실물 미노출(regex backend '었다' 계보)
    # 부정 예시·회피 목록 토큰 부재(긍정 메뉴만)
    banned = ("하지 마", "쓰지 마", "말 것", "말라", "지 말고", "피하", "금지", "나쁜 예", "회피")
    for tok in banned:
        ok &= (tok not in r["block"])
    # 긍정 전환 메뉴는 존재해야(pink-elephant 는 '회피 대상 노출' 금지이지 긍정 메뉴 금지가 아님)
    ok &= (_BLOCK_MENU in r["block"] and "속생각" in r["block"])
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ pink-elephant lint: 측정 top_template('{top_tpl}') 실물 미노출·부정 토큰 0·긍정 메뉴 유지")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# ⑦ draft_ctx.style_counter 기록(ON) / 키 부재(OFF) — generate() 실경로
# ═══════════════════════════════════════════════════════════════════════════════
def _run_generate(style_running_counter: bool, draft_text: str, continue_text: str,
                  prev_text: str = "", capture: list | None = None) -> ChapterRecord:
    """generate() 실경로 — draft/continue mock 으로 이어쓰기 루프를 태워 draft_ctx 를 채운다(LLM 0콜).
    prev_text: generate(prev_chapter_text=…) 전달분(ST-12b-2 회차 간 카운터 검증용).
    capture: 리스트를 주면 mock provider 를 append(호출 프롬프트 검사용)."""
    from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
    from novelcopilot.engine.factory import build_engine
    from novelcopilot.engine.checker import CheckResult
    from novelcopilot.domain.types import SceneSpec as _SS
    from novelcopilot.llm.base import LLMProvider

    class _Cap(LLMProvider):
        def __init__(self):
            super().__init__()   # self.usage = Usage() (generate() 가 usage.chat_tokens 읽음)
            self.captured = []
            self.last_truncated = False

        def chat(self, messages, *a, **k):
            self.captured.append(messages)
            # draft(첫 콜)와 continue(이후)를 길이로 구분: 첫 콜=draft, 이후=continue
            return draft_text if len(self.captured) == 1 else continue_text

        def chat_json(self, messages, *a, **k):
            return {}

        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    world = WorldConfig(
        title="[실험] ST-12b", genre="현판", tone="건조", premise="테스트.",
        entities=[EntitySpec(id="hero", name="도현", etype="character", profile="주인공")],
        beats=[Beat(chapter=3, title="t", summary="s", entities=["hero"])],
        style=StyleSpec(pov="third_limited"))
    from novelcopilot.config import get_settings
    settings = get_settings().model_copy(update={"style_running_counter": style_running_counter,
                                                 "narrator_voice": False, "humanize": False,
                                                 "style_repair": False, "reader_desk": False,
                                                 "claim_audit": False,
                                                 "gen_tools": False})   # 운영 .env(NOVEL_GEN_TOOLS)가 새면 첫 콜=사서 턴이 돼 '첫 콜=초안' 가정 붕괴 — 테스트 격리 핀
    prov = _Cap()
    if capture is not None:
        capture.append(prov)
    b = build_engine(world, prov, settings)
    gen = b.generator
    gen.plan_scenes = lambda beat, directives: [_SS(index=0, goal="g", key_events=["미실현사건알파"])]
    gen._rewrite = lambda text, viols, board, **kw: text
    gen.checker.check_text = lambda *a, **k: CheckResult(violations=[], claims=[])
    # key_events 는 draft/continue mock 본문에 등장하지 않는 문구여야 DP-12 '비트 미소진' 트리거가 이어쓰기 루프를
    #   실제로 돌린다(단일 글자 'e' 는 uncovered 가 과대매칭해 '소진'으로 보고 루프 미진입 — 실측 교훈).
    rec = gen.generate(3, {"title": "t", "summary": "s", "entities": ["hero"],
                           "key_events": ["미실현사건알파"]},
                       b.ontology, b.rag, b.wiki, prev_chapter_text=prev_text)
    return rec


def test_draft_ctx_records() -> bool:
    # 대역 밖 초안(과거 종결 반복 · norm 미달로 이어쓰기 유발) → ON: style_counter 기록 / OFF: 키 부재
    draft = "\n".join(["그는 문을 열었다."] * 12)      # 짧고 대역 밖 → 이어쓰기 루프 진입
    cont = "\n".join(["다시 그는 걸었다."] * 12)        # 이어쓰기 세그먼트(>200자)
    rec_on = _run_generate(True, draft, cont)
    dctx_on = (rec_on.gen_context or {}).get("draft", {})   # draft_ctx 는 rec.gen_context["draft"] 에 영속
    ok = ("style_counter" in dctx_on)                  # ON → 키 존재
    entries = dctx_on.get("style_counter", [])
    ok &= (isinstance(entries, list) and len(entries) >= 1)   # 이어쓰기 1회+ → 레코드 1개+
    if entries:
        e0 = entries[0]
        ok &= all(kk in e0 for kk in ("ext", "n_ending", "top_ratio", "max_run", "quota_k", "injected", "backend"))
        ok &= (e0["injected"] is True)                 # 대역 밖 초안 → 첫 이어쓰기서 주입
    rec_off = _run_generate(False, draft, cont)
    dctx_off = (rec_off.gen_context or {}).get("draft", {})
    ok &= ("style_counter" not in dctx_off)            # OFF → 키 자체 부재(구 레코드 바이트 동일)
    print(f"[{'OK' if ok else 'FAIL'}] ⑦ draft_ctx.style_counter: ON 기록 {len(entries)}개(injected)·OFF 키 부재")
    return ok


# ═══════════════════════════════════════════════════════════════════════════════
# ⑧⑨ ST-12b-2 회차 간 카운터 — 12b 쌍 A/B 실측(개입 표면 부재: cont 0~1회·잔여 클램프 무효량)의 소스 수리.
#     직전 회차 계측이 대역 밖이면 '초안' 프롬프트에 상태블록(검증된 프로브 B형 비율 규칙) 주입 — 표면 100%.
# ═══════════════════════════════════════════════════════════════════════════════
def test_prev_counter_block() -> bool:
    # 대역 밖 직전 회차 → 초안 상태블록(비율 규칙+시제 충돌 해소), pink-elephant(실물 종결형 미노출)
    prev = "\n".join(["그는 문을 열었다."] * 12)
    r = sc.compute_prev(prev)
    ok = (r["block"] != "" and "직전 회차" in r["block"] and "절반가량" in r["block"])
    ok &= ("었다" not in r["block"])                          # 최빈 종결형 실물 미노출(pink-elephant)
    ok &= ("기조 위반이 아니라" in r["block"])                 # _draft out_instr '과거형 기조 일관'과의 충돌 해소 문구
    ok &= (r["debug"]["injected"] is True and r["debug"]["src"] == "prev")
    # in-band 직전 회차 → 무주입(상태 적응형 — 상수 블랭킷 아님)
    varied = ("문을 연다.\n정적.\n왜 하필 나였을까.\n그가 웃는다.\n상자 하나.\n"
              "손이 떨렸다.\n갈 수밖에 없지.\n다음은 내 차례다.\n") * 3
    r2 = sc.compute_prev(varied)
    ok &= (r2["block"] == "" and r2["debug"] is not None and r2["debug"]["injected"] is False)
    # ch1(직전 없음)·결측 → 무주입·디버그 없음(OFF 와 구분 불요)
    r3 = sc.compute_prev("")
    ok &= (r3["block"] == "" and r3["debug"] is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⑧ compute_prev: 대역 밖→비율 규칙 블록·in-band/ch1→무주입·실물 미노출")
    return ok


def test_prev_counter_generate_wiring() -> bool:
    # generate(prev_chapter_text=대역 밖) + ON → '초안 첫 콜' user 프롬프트에 직전 회차 블록 + draft_ctx ext:0 기록
    prev = "\n".join(["그는 문을 열었다."] * 12)
    draft = "괜찮은 본문 문장이 이어진다. " * 300
    cap: list = []
    rec = _run_generate(True, draft, "이어지는 본문이 계속 나아간다. " * 20, prev_text=prev, capture=cap)
    first_user = cap[0].captured[0][1]["content"]
    ok = ("[서술 리듬 계측 — 직전 회차" in first_user)         # 초안 콜에 실림(표면 100%)
    dctx = (rec.gen_context or {}).get("draft", {})
    entries = dctx.get("style_counter", [])
    ok &= any(e.get("src") == "prev" and e.get("ext") == 0 and e.get("injected") for e in entries)
    # OFF → 초안 프롬프트에 블록 없음(바이트 동일 계약은 ④가 증명 — 여기선 미주입만 확인)
    cap2: list = []
    _run_generate(False, draft, "이어지는 본문이 계속 나아간다. " * 20, prev_text=prev, capture=cap2)
    ok &= ("[서술 리듬 계측" not in cap2[0].captured[0][1]["content"])
    print(f"[{'OK' if ok else 'FAIL'}] ⑨ 회차 간 카운터 generate 배선: ON 초안 주입·ext0 기록·OFF 미주입")
    return ok


def main() -> int:
    results = [
        test_quota_arithmetic(),
        test_in_band_no_injection(),
        test_out_of_band_block_and_injection(),
        test_off_byte_identical(),
        test_tools_import_failure_degrades(),
        test_pink_elephant_lint(),
        test_draft_ctx_records(),
        test_prev_counter_block(),
        test_prev_counter_generate_wiring(),
    ]
    print("\nST-12b(draft 러닝 카운터 폐루프 — CAPEL) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_st12b_running_counter_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
