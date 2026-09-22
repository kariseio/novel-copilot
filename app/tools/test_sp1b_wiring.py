# -*- coding: utf-8 -*-
"""SP-1b 검증 — SP-1 잔여 배선 4건(감사 audit_e2e_1.md G1·G3·G5 + audit_e2e_0.md G-3). LLM 0콜·결정론.

검증 축(사전 등록):
  ① service 관통 실배선(G3/G-B) —
     1) repair_spans 가 service 를 rewrite_span_via_chassis 로 관통 전달한다(kwarg 포워딩).
     2) service(_guardrail 보유)+checker+ontology 가 있고 본문이 바뀌면 G-B 클레임 표면 비교가 **실제로 호출**되고
        그 판정이 style_repairs 항목 guardrail_ok 로 실린다(모의 _guardrail 호출 관측).
     3) service=None(러너·구 세션) 이면 G-B 미호출·guardrail_ok=None(기존 no-op 계약 불변).
     4) harness 배선 — ChapterGenerator.service default=None, generate() 가 self.service 를 repair_spans 로 관통.
  ② 층위 축 산출(G1) — kiwi_style_metrics.layer(dialogue_para_ratio·dialogue_char_ratio·max_narration_run)
     가 style_lightness_baseline.lightness_metrics 와 동일값으로 additive 병기(ST-9 '진짜 벽' 판정축).
  ③ 대역 병기 스키마(G3/§4) — kiwi_style_metrics.human_band 가 kiwi_human_band.HUMAN_BAND 상수와 동일 구조·
     동일값으로 병기(advisory·판정 라벨 0). 대역 산출 스크립트가 동결 상수와 무드리프트.
  ④ 백필 멱등성(G5) — [실험 DP-5] 2작 ai_tell kiwi 백필이 base 축 불변·kiwi additive·ai_tell 이외 불가침·
     재실행 no-op(멱등).

실행: (app/ 에서) py -3.12 -m pytest tools/test_sp1b_wiring.py -q
       또는     PYTHONIOENCODING=utf-8 py -3.12 tools/test_sp1b_wiring.py
"""
from __future__ import annotations
import importlib.util
import json
import pathlib
import sys
from types import SimpleNamespace

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot
sys.path.insert(0, str(_HERE))          # tools/

from novelcopilot.domain.world import StyleSpec
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.engine import style_pipeline as sp
import tools.st11_span_rewrite as st11


class _Bus:
    def emit(self, *a, **k):
        pass


# 벽 텍스트('~다' 종결 run — 스팬 검출됨). SP-1 테스트와 동형.
WALL = ('"준비됐어?"\n'
        "그는 고개를 끄덕였다. 문을 열었다. 계단을 내려갔다. 벽을 짚었다. "
        "손전등을 켰다. 어둠이 물러났다. 발을 디뎠다. 숨을 골랐다. 앞으로 나아갔다.\n"
        "계단은 끝없이 이어진다. 정말 끝이 있을까?")


class _ChangingGen:
    """revise_prose 가 실제로 벽을 한 문장으로 흘려 본문을 바꾼다(G-B 가드 도달 조건: full_after != full_text)."""
    def revise_prose(self, directive, before_text, span_text="", **kw):
        if not span_text or span_text not in before_text:
            return before_text
        rw = ("그는 고개를 끄덕이며 문을 열고 계단을 내려가 벽을 짚었고, 손전등을 켜자 어둠이 물러났고, "
              "발을 디뎌 숨을 고르며 앞으로 나아갔다.")
        return before_text.replace(span_text, rw, 1)


class _Ont:
    def scan_present_ids(self, text):
        return []


class _Chk:
    """check_text 는 claims 보유 결과를 낸다(_guardrail 이 before_res.hard/claims 를 참조)."""
    def check_text(self, text, ont, ch, ids, pov=""):
        return SimpleNamespace(hard=[], claims=[])


# ═════════════════ ① service 관통(G3/G-B) ═════════════════

def test_service_passthrough_forwarding() -> bool:
    """1) repair_spans → rewrite_span_via_chassis 로 service kwarg 관통(포워딩 관측)."""
    seen = {"service": "UNSET"}
    orig = st11.rewrite_span_via_chassis

    def spy(generator, ontology, checker, chapter_no, full_text, span, **kw):
        seen["service"] = kw.get("service", "MISSING")
        return {"changed": False, "full_after": full_text, "span_text_after": span.get("span_text", ""),
                "coverage_guard": {}, "guardrail": None, "retried": False}

    sentinel = object()
    st11.rewrite_span_via_chassis = spy
    try:
        sp.repair_spans(_ChangingGen(), _Ont(), _Chk(), 5, WALL, max_spans=6, service=sentinel)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok = (seen["service"] is sentinel)
    print(f"[{'OK' if ok else 'FAIL'}] ① service 포워딩: repair_spans→chassis service kwarg 관통")
    return ok


def test_gb_guardrail_actually_runs() -> bool:
    """2) service(_guardrail)+checker+ontology + 본문 변경 → G-B 실제 호출·판정이 repairs.guardrail_ok 로 영속."""
    calls = {"n": 0, "args_ok": False}

    class _Service:
        def _guardrail(self, before_text, after_text, before_res, ids, ont, checker, chapter):
            calls["n"] += 1
            # st11 이 (full_text, full_after, before_res, ids, ontology, checker, chapter_no) 로 부른다
            calls["args_ok"] = (before_text == WALL and after_text != WALL and chapter == 5)
            return ({"passed": True, "g_a_passed": True, "g_b_passed": True, "reasons": []},
                    SimpleNamespace(hard=[], claims=[]))

    new_text, repairs = sp.repair_spans(_ChangingGen(), _Ont(), _Chk(), 5, WALL,
                                        max_spans=6, service=_Service())
    ok = (calls["n"] >= 1)                       # G-B 실제 호출(미배선이면 0)
    ok &= calls["args_ok"]                       # 올바른 인자(전체 대 전체·회차)
    ok &= (new_text != WALL)                     # 수리 반영(가드 도달 조건 충족)
    changed = [r for r in repairs if r.get("changed")]
    ok &= (len(changed) >= 1 and changed[0]["guardrail_ok"] is True)   # 판정이 항목에 실림
    print(f"[{'OK' if ok else 'FAIL'}] ① G-B 실배선: service._guardrail 실제 호출·판정 영속(guardrail_ok)")
    return bool(ok)


def test_gb_skipped_when_service_none() -> bool:
    """3) service=None(러너/구 세션) → G-B 미호출·guardrail_ok=None(기존 no-op 계약 불변)."""
    new_text, repairs = sp.repair_spans(_ChangingGen(), _Ont(), _Chk(), 5, WALL,
                                        max_spans=6, service=None)
    changed = [r for r in repairs if r.get("changed")]
    ok = (new_text != WALL and len(changed) >= 1)   # 수리 자체는 됨(가드만 생략)
    ok &= (changed[0]["guardrail_ok"] is None)      # G-B 미실행 → None(정직 결측)
    print(f"[{'OK' if ok else 'FAIL'}] ① service=None: G-B 미호출·guardrail_ok=None(no-op 계약)")
    return bool(ok)


def test_harness_service_default_and_passthrough() -> bool:
    """4) ChapterGenerator.service default=None + generate() 가 self.service 를 repair_spans 로 관통(스파이)."""
    from novelcopilot.config import Settings

    settings = Settings()   # style_repair=True
    object.__setattr__(settings, "humanize", False)   # HM-1b 흡수: humanize ON 이면 HM-1 경로가 Stage B 를 대신 태운다.
    #   이 테스트는 Stage B(style_pipeline.repair_spans) service 관통을 직접 검증하므로 humanize=OFF 로 폴백 경로를 태운다.
    #   HM-1 경로의 service 관통은 test_hm1b_humanize_pass 가 별도 검증(의도 보존·개편 허용).
    ok = True

    class _Cap:
        def __init__(self):
            self.last_truncated = False
            self.usage = SimpleNamespace(chat_tokens=0, chat_calls=0)
        def chat(self, messages, *a, **k):
            return "본문."
        def chat_json(self, messages, *a, **k):
            return {}
        def embed(self, texts):
            return [[0.0] * 4 for _ in texts]

    class _HOnt:
        entities = {}
        rules = []
        def is_actor(self, et): return False
        def canon_facts(self, ids, ch): return []
        def canon_relations(self, ids, ch): return []
        def scan_present_ids(self, text): return []
    class _HChk:
        def check_text(self, *a, **k): return SimpleNamespace(violations=[], hard=[], claims=[])
    class _HRag:
        def index_chapter(self, *a, **k): return 1
        def search(self, *a, **k): return []
    class _HWiki:
        def ingest_chapter(self, *a, **k): return 0
        def retrieve(self, *a, **k): return []

    g = ChapterGenerator(_Cap(), checker=_HChk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
    ok &= (g.service is None)   # default None(속성 존재·getattr 폴백 불필요)

    seen = {"service": "UNSET"}
    import novelcopilot.engine.style_pipeline as spmod
    real = spmod.repair_spans

    def spy(generator, ontology, checker, chapter_no, text, **kw):
        seen["service"] = kw.get("service", "MISSING")
        return text, []

    marker = object()
    g.service = marker          # 호출부(copilot) 주입 시뮬
    spmod.repair_spans = spy
    try:
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        g.generate(2, beat, _HOnt(), _HRag(), _HWiki(), prev_chapter_text="", story_so_far="")
    finally:
        spmod.repair_spans = real
    ok &= (seen["service"] is marker)   # generate() 가 self.service 를 관통
    print(f"[{'OK' if ok else 'FAIL'}] ① harness: service default=None·generate()→repair_spans 관통")
    return bool(ok)


# ═════════════════ ② 층위 축 산출(G1) ═════════════════

def test_layer_axis_emitted() -> bool:
    """②-a kiwi_style_metrics.layer 3필드 병기 — dp4b _kiwi_axes layer 와 동형(ST-9 '벽' 판정축)."""
    km = sp.kiwi_style_metrics(WALL)
    if not km:   # kiwi_metrics 부품 부재 강등 — layer 도 없음(11 로 커버)
        print("[OK] ② 부품 부재(빈 dict) — layer 확장 없음(정직 결측)")
        return True
    ok = ("layer" in km)
    layer = km.get("layer", {})
    ok &= all(k in layer for k in ("dialogue_para_ratio", "dialogue_char_ratio", "max_narration_run"))
    print(f"[{'OK' if ok else 'FAIL'}] ②-a layer 축: 3필드 병기(dp4b _kiwi_axes 동형)")
    return bool(ok)


def test_layer_axis_matches_lightness() -> bool:
    """②-b layer 값이 style_lightness_baseline.lightness_metrics 원값과 동일(신규 검출기 0·재사용 증명)."""
    km = sp.kiwi_style_metrics(WALL)
    if not km or "layer" not in km:
        print("[OK] ② 부품 부재/강등 — 값 대조 생략(정직 결측)")
        return True
    import tools.style_lightness_baseline as sl
    lm = sl.lightness_metrics(WALL)
    layer = km["layer"]
    ok = (layer["dialogue_para_ratio"] == lm["dialogue_para_ratio"])
    ok &= (layer["dialogue_char_ratio"] == lm["dialogue_char_ratio"])
    ok &= (layer["max_narration_run"] == lm["max_narration_run"])
    print(f"[{'OK' if ok else 'FAIL'}] ②-b layer 값 == lightness_metrics(재사용·신규 검출기 0)")
    return bool(ok)


# ═════════════════ ③ 대역 병기 스키마(G3/§4) ═════════════════

def test_human_band_schema() -> bool:
    """③-a kiwi_style_metrics.human_band 스키마 — HUMAN_BAND 상수와 동일 구조·값·advisory(판정 라벨 0)."""
    km = sp.kiwi_style_metrics(WALL)
    if not km:
        print("[OK] ③ 부품 부재 — human_band 병기 없음(정직 결측)")
        return True
    from tools.kiwi_human_band import HUMAN_BAND
    ok = ("human_band" in km)
    band = km.get("human_band", {})
    ok &= (band == HUMAN_BAND)                              # 상수 그대로 병기(수치만)
    ok &= (band.get("meta", {}).get("advisory") is True)   # advisory 표기(임계·판정 0)
    # 각 그룹에 대역 min/max/mean/median 구조
    for group in ("ending_profile", "da_streak", "layer"):
        for metric, b in band.get(group, {}).items():
            ok &= set(b.keys()) == {"min", "max", "mean", "median"}
    # 판정 라벨 부재(verdict/pass/label 키 없음)
    ok &= not any(k in json.dumps(band) for k in ("verdict", '"pass"', "label"))
    print(f"[{'OK' if ok else 'FAIL'}] ③-a human_band: HUMAN_BAND 동형·min/max/mean/median·advisory·판정 라벨 0")
    return bool(ok)


def test_human_band_no_drift() -> bool:
    """③-b 대역 산출 스크립트가 동결 HUMAN_BAND 와 무드리프트(reference/ 실측 == 상수). 원문 미커밋·수치만."""
    import tools.kiwi_human_band as khb
    if not all(p.exists() for _l, p in khb.REF_FILES):
        print("[OK] ③ reference/ 부재 — 실측 대조 생략(상수는 존재)")
        return True
    band, per_chapter = khb.compute_band()
    live, frozen = khb._flatten(band), khb._flatten(khb.HUMAN_BAND)
    ok = (live == frozen)                                  # 실측 == 동결 상수(무드리프트)
    # 산출물에 원문(장문 프로즈) 미포함 — per_chapter 는 수치 라벨만
    ok &= all(isinstance(c.get("label"), str) and len(c["label"]) < 40 for c in per_chapter)
    print(f"[{'OK' if ok else 'FAIL'}] ③-b 대역 무드리프트: reference 실측 == HUMAN_BAND·원문 미포함")
    return bool(ok)


# ═════════════════ ④ 백필 멱등성(G5) ═════════════════

def _load_backfill():
    spec = importlib.util.spec_from_file_location("bf_dp5", _HERE / "backfill_dp5_ai_tell_kiwi.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_backfill_add_kiwi_base_preserved() -> bool:
    """④-a _add_kiwi: base 축 전부 보존 + kiwi 순수 additive(재계산 아님·본문 불변 → base 불변)."""
    bf = _load_backfill()
    base = {"comma_per_100": 1.2, "sent_len_cv": 0.6, "lexical_mattr": 0.9, "n_sent": 30}
    after = bf._add_kiwi(base, WALL)
    ok = all(after.get(k) == base[k] for k in base)   # base 전부 보존
    if "kiwi" in after:                               # kiwi 가용 시 additive
        ok &= (set(after.keys()) == set(base.keys()) | {"kiwi"})
    else:                                             # 부품 부재 시 base 그대로(확장 없음)
        ok &= (after == base)
    print(f"[{'OK' if ok else 'FAIL'}] ④-a _add_kiwi: base 보존·kiwi additive(재계산 아님)")
    return bool(ok)


def test_backfill_idempotent_and_invariant() -> bool:
    """④-b 실데이터 멱등·불가침 — 현 디스크 상태에 --check(쓰기 없음)로 재실행 시 변경 0(이미 백필됨)이고
    ai_tell 이외 불변(invariant). 백필 미적용 환경(kiwi 부재)이면 변경이 있을 수 있으나 invariant 는 항상 True."""
    bf = _load_backfill()
    all_ok = True
    idempotent = True
    for pid in bf.TARGET_PIDS:
        if not (bf.PROJ / f"{pid}.json").exists():
            print(f"[SKIP] ④-b {pid} 부재 — 환경 데이터 없음")
            continue
        r = bf.process(pid, apply=False)          # dry-run(쓰기 없음)
        all_ok &= r["invariant_ai_tell_only"]     # ai_tell 이외 불변은 항상 성립해야 함
        idempotent &= (not r["would_write"])       # 이미 백필됨 → 추가 변경 0(멱등)
    ok = all_ok and idempotent
    print(f"[{'OK' if ok else 'FAIL'}] ④-b 백필 멱등·불가침: 재실행 변경 0·ai_tell 이외 불변"
          + ("" if idempotent else "  (미적용 환경 — 백필 필요)"))
    return bool(ok)


def test_backfill_structural_diff_guard() -> bool:
    """④-c 불가침 가드 로직 — ai_tell 밖 변경은 invariant False 로 잡아 APPLY 를 막는다(가드 자체 검증)."""
    bf = _load_backfill()
    old = {"chapters": [{"chapter": 1, "status": "FINALIZED", "text": "x",
                         "ai_tell": {"n_sent": 3}, "summary": "요약"}]}
    # (a) ai_tell 만 바뀐 경우 → invariant True
    new_ok = {"chapters": [{"chapter": 1, "status": "FINALIZED", "text": "x",
                            "ai_tell": {"n_sent": 3, "kiwi": {"x": 1}}, "summary": "요약"}]}
    d1 = bf._structural_diff_paths(old, new_ok)
    ok = bf._only_ai_tell(d1)
    # (b) summary(서사 필드) 가 바뀐 경우 → invariant False(APPLY 차단)
    new_bad = {"chapters": [{"chapter": 1, "status": "FINALIZED", "text": "x",
                             "ai_tell": {"n_sent": 3}, "summary": "변조됨"}]}
    d2 = bf._structural_diff_paths(old, new_bad)
    ok &= (not bf._only_ai_tell(d2))
    # (c) 본문(text) 변경도 차단
    new_text = {"chapters": [{"chapter": 1, "status": "FINALIZED", "text": "변조",
                              "ai_tell": {"n_sent": 3}, "summary": "요약"}]}
    ok &= (not bf._only_ai_tell(bf._structural_diff_paths(old, new_text)))
    print(f"[{'OK' if ok else 'FAIL'}] ④-c 불가침 가드: ai_tell만=True·summary/text 변경=False(APPLY 차단)")
    return bool(ok)


def main() -> int:
    results = [
        test_service_passthrough_forwarding(),
        test_gb_guardrail_actually_runs(),
        test_gb_skipped_when_service_none(),
        test_harness_service_default_and_passthrough(),
        test_layer_axis_emitted(),
        test_layer_axis_matches_lightness(),
        test_human_band_schema(),
        test_human_band_no_drift(),
        test_backfill_add_kiwi_base_preserved(),
        test_backfill_idempotent_and_invariant(),
        test_backfill_structural_diff_guard(),
    ]
    print("\nSP-1b(잔여 배선 4건) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


def test_sp1b_wiring_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
