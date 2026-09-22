# -*- coding: utf-8 -*-
"""HM-1b 검증 — Claude 윤문 패스 + 배선(설계 docs/design-hm1-humanize-pass.md §2ⓒⓓⓔ). LLM 0콜(모킹)·결정론.

검증 축(사전 등록):
  A. 변경률 가드(③) —
     1) 레벤슈타인/원문 산술 — 동일=0·치환 비율 정확·빈 문자열 대칭.
     2) 대역 분류 — <5% under·5~30% ok·>30% over(경계 포함).
  B. 카테고리 처방(ⓒ·few-shot 0·pink-elephant) —
     3) build_directive = 대원칙 6 + 카테고리 처방(N-1~N-6). 미등록 축=대원칙만. 예문 나열 0.
  C. 브릿지 — HM-1a finding(char 좌표) → 체시스 스팬(span_text·kind). 전범위/무효 좌표=None(스킵).
  D. 선별 — S1 우선→긴 스팬→좌표. 상한 0=빈·None=전부.
  E. 윤문 실행기(폴백·잔존 표기) —
     6) 실제 변경 수리 → text 갱신·changed=True·change_rate 기록.
     7) 과윤문(변경률 30%↑) → 그 스팬만 폴백(원문 유지)·author_review(S1).
     8) 무변경 폴백 → 원문 불변·항목 기록(침묵 폴백 금지)·S1 잔존 표기.
     9) 스팬 격리 — span_not_found → 그 스팬만 폴백·전진(회차 안 죽음).
     10) not_local(전범위) → 스킵·기록(S1이면 작가 확인).
     11) Claude provider 스코프 스왑 — 윤문 콜 동안만 generator.provider 교체·복원(예외 경로 포함).
  F. 배선(ⓓⓔ) —
     12) harness generate() 관통 — humanize ON 시 HM-1 호출·본문 반영·humanize 영속·usage 계상·service 관통.
     13) Stage B 흡수 — humanize ON 이면 style_pipeline.repair_spans 미호출(이중 패스 금지).
     14) humanize OFF → HM-1 미호출·Stage B 폴백(하위호환).
  G. verification 기록(ⓓ) — build_verification.humanize 축(before/after 계측·폐루프).
  H. 하위호환 — 구 JSON(humanize 없는) 로드·humanize default=[]·재직렬화 정합.

실행: (app/ 에서) py -3.12 -m pytest tools/test_hm1b_humanize_pass.py -q
       또는     PYTHONIOENCODING=utf-8 py -3.12 tools/test_hm1b_humanize_pass.py
"""
from __future__ import annotations
import json
import pathlib
import sys
from types import SimpleNamespace

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot
sys.path.insert(0, str(_HERE))          # tools/

from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.engine import humanize_pass as hp


class _Bus:
    def emit(self, *a, **k):
        pass


# 벽 텍스트('~다' 종결 run — HM-1a N-4 스팬 검출 대상, SP-1 테스트와 동형).
WALL = ('"준비됐어?"\n'
        "그는 고개를 끄덕였다. 문을 열었다. 계단을 내려갔다. 벽을 짚었다. "
        "손전등을 켰다. 어둠이 물러났다. 발을 디뎠다. 숨을 골랐다. 앞으로 나아갔다.\n"
        "계단은 끝없이 이어진다. 정말 끝이 있을까?")


# ═════════════════ A. 변경률 가드 ═════════════════

def test_change_rate_arithmetic() -> bool:
    """1) 레벤슈타인/원문 산술 — 동일=0·치환 비율·빈 문자열 대칭."""
    ok = (hp.change_rate("abcdefghij", "abcdefghij") == 0.0)   # 동일=0
    ok &= (hp.change_rate("abcdefghij", "abcXefghij") == round(1 / 10, 4))   # 1치환/10자
    ok &= (hp.change_rate("", "") == 0.0)
    ok &= (hp.change_rate("abc", "") == round(3 / max(1, 3), 4))   # 전삭제(원문 3, 분모 max(1,3)=3 → 1.0)
    ok &= (hp.change_rate("", "abc") == round(3 / max(1, 0), 4))   # 전삽입(원문 "" → 분모 max(1,0)=1 → 3.0)
    # 편집 거리 자체는 대칭(kitten↔sitting=3)
    ok &= (hp._levenshtein("kitten", "sitting") == 3)
    print(f"[{'OK' if ok else 'FAIL'}] A: 변경률 산술(동일 0·치환 비율·빈 문자열·편집거리)")
    return bool(ok)


def test_change_rate_band() -> bool:
    """2) 대역 분류 — under/ok/over 경계."""
    ok = (hp.classify_change_rate(0.0) == "under")
    ok &= (hp.classify_change_rate(0.049) == "under")
    ok &= (hp.classify_change_rate(0.05) == "ok")     # 하한 포함
    ok &= (hp.classify_change_rate(0.30) == "ok")     # 상한 포함
    ok &= (hp.classify_change_rate(0.3001) == "over")
    ok &= (hp.classify_change_rate(0.9) == "over")
    print(f"[{'OK' if ok else 'FAIL'}] A: 변경률 대역(under<5%·ok 5~30%·over>30%·경계)")
    return bool(ok)


# ═════════════════ B. 카테고리 처방 ═════════════════

def test_category_directives() -> bool:
    """3) build_directive = 대원칙 6 + 카테고리 처방. 미등록=대원칙만. few-shot 0(예문 나열 없음)."""
    ok = True
    for cat in ("N-1", "N-2", "N-3", "N-4", "N-5", "N-6"):
        d = hp.build_directive(cat)
        ok &= ("교정 작가" in d)                 # 대원칙 6 머리
        ok &= ("[이 구간의 처방]" in d)          # 카테고리 처방 붙음
        ok &= (hp.directive_for(cat) in d)       # 처방 지시문 포함
    # 미등록 축 = 대원칙만(처방 헤더 없음)
    unknown = hp.build_directive("ZZZ")
    ok &= ("교정 작가" in unknown and "[이 구간의 처방]" not in unknown)
    # few-shot 0: 처방에 나쁜 예문(따옴표로 감싼 티 예시)을 나열하지 않는다 — 규칙 지시문만.
    #   "빛줄기가 맥박쳤다" 같은 실측 예문이 처방 문안에 박혀 있지 않은가(pink-elephant 가드).
    for cat in _all_cats():
        ok &= ("빛줄기" not in hp.directive_for(cat) and "맥박" not in hp.directive_for(cat))
    print(f"[{'OK' if ok else 'FAIL'}] B: 처방=대원칙+카테고리·미등록=대원칙만·few-shot 0")
    return bool(ok)


def _all_cats():
    return list(hp._CATEGORY_DIRECTIVES.keys()) + list(hp._ESSAY_DIRECTIVES.keys())


# ═════════════════ C. 브릿지 ═════════════════

def test_chassis_span_bridge() -> bool:
    """4) HM-1a finding → 체시스 스팬. 유효 좌표=span_text 채움·전범위/무효=None(스킵)."""
    text = "가나다라마바사아자차"
    good = {"category": "N-4", "severity": "S2", "span": {"char_start": 2, "char_end": 6, "text": "다라마바"}}
    sp = hp._chassis_span_from_finding(text, good)
    ok = (sp is not None and sp["span_text"] == "다라마바" and sp["kind"] == "N-4")
    # 전범위 밀도 신호(N-5 layer_wall·N-6 simile — char 0~len·text="") = 국소 아님 → None
    full = {"category": "N-5", "severity": "S1", "span": {"char_start": 0, "char_end": len(text), "text": ""}}
    ok &= (hp._chassis_span_from_finding(text, full) is None)
    # 무효 좌표(역순·범위 밖·비정수) = None
    ok &= (hp._chassis_span_from_finding(text, {"span": {"char_start": 6, "char_end": 2}}) is None)
    ok &= (hp._chassis_span_from_finding(text, {"span": {"char_start": 0, "char_end": 999}}) is None)
    ok &= (hp._chassis_span_from_finding(text, {"span": {"char_start": None, "char_end": 5}}) is None)
    # 공백만 슬라이스 = None
    ok &= (hp._chassis_span_from_finding("가  나", {"span": {"char_start": 1, "char_end": 3}}) is None)
    print(f"[{'OK' if ok else 'FAIL'}] C: 브릿지 — 유효=span_text·전범위/무효=None(스킵)")
    return bool(ok)


# ═════════════════ D. 선별 ═════════════════

def test_select_priority() -> bool:
    """5) 선별 — S1 우선→긴 스팬→좌표. 상한 0=빈·None=전부."""
    f = [
        {"category": "N-4", "severity": "S2", "span": {"char_start": 0, "char_end": 10}},
        {"category": "N-5", "severity": "S1", "span": {"char_start": 20, "char_end": 25}},   # S1 우선
        {"category": "N-6", "severity": "S3", "span": {"char_start": 30, "char_end": 60}},   # 긴 스팬이나 S3
    ]
    picked = hp.select_humanize_spans(f, None)
    ok = (picked[0]["severity"] == "S1")                       # S1 최우선
    ok &= (len(picked) == 3)                                   # None=전부
    ok &= (hp.select_humanize_spans(f, 0) == [])              # 0=빈
    ok &= (len(hp.select_humanize_spans(f, 2)) == 2)          # 상한 절단
    # 동일 심각도면 긴 스팬 우선
    f2 = [{"category": "A", "severity": "S2", "span": {"char_start": 0, "char_end": 5}},
          {"category": "B", "severity": "S2", "span": {"char_start": 10, "char_end": 40}}]
    ok &= (hp.select_humanize_spans(f2, None)[0]["category"] == "B")   # 긴 스팬(30) 우선
    print(f"[{'OK' if ok else 'FAIL'}] D: 선별 — S1 우선·긴 스팬·상한 0=빈·None=전부")
    return bool(ok)


# ═════════════════ E. 윤문 실행기 ═════════════════

class _MockChassisGen:
    """revise_prose 호출을 관측하고 지정 방식으로 span 을 변형하는 스텁 generator(provider 스왑 관측용)."""
    def __init__(self, rewrite_fn):
        self._rewrite_fn = rewrite_fn
        self.provider = SimpleNamespace(name="gen-default")   # 스왑 관측용
        self.provider_seen = []                                # revise_prose 호출 시점의 provider 스냅샷

    def revise_prose(self, directive, before_text, span_text="", **kw):
        self.provider_seen.append(getattr(self.provider, "name", None))
        if not span_text or span_text not in before_text:
            return before_text
        return before_text.replace(span_text, self._rewrite_fn(span_text), 1)


def _finding(text, cat="N-4", sev="S2"):
    """text 전체를 한 스팬으로 잡는 N-4 finding(체시스 v2 는 문단 확장이라 실 대상은 문단)."""
    # 벽 문단(둘째 줄)만 잡도록 char 좌표 지정 — WALL 의 지문 벽 부분.
    cs = WALL.index("그는 고개를")
    ce = WALL.index("나아갔다.") + len("나아갔다.")
    return {"category": cat, "severity": sev,
            "span": {"char_start": cs, "char_end": ce, "text": WALL[cs:ce]}}


def test_actual_humanize_updates_text() -> bool:
    """6) 실제 변경 수리 → text 갱신·changed=True·change_rate 기록·rate_band=ok."""
    def rw(span):   # 내용어 보존한 채 한 문장으로 흘림(커버리지 통과·변경률 중간)
        return ("그는 고개를 끄덕이며 문을 열고 계단을 내려가 벽을 짚었고, 손전등을 켜자 어둠이 물러났으며, "
                "발을 디뎌 숨을 고르고 앞으로 나아갔다.")
    gen = _MockChassisGen(rw)
    new_text, entries = hp.humanize_spans(gen, None, None, 5, WALL, [_finding(WALL)],
                                          max_spans=6, service=None, humanize_provider=None)
    ok = (new_text != WALL)
    ok &= (len(entries) == 1 and entries[0]["changed"] is True)
    ok &= (isinstance(entries[0]["change_rate"], float) and entries[0]["change_rate"] > 0)
    ok &= (entries[0]["rate_band"] in ("ok", "under"))
    ok &= (entries[0]["category"] == "N-4")
    print(f"[{'OK' if ok else 'FAIL'}] E6: 실제 윤문 — text 갱신·changed·change_rate·rate_band")
    return bool(ok)


# 부분집합 지문 문단 — 비과거 선행문 + 6문장 과거형 run + 현재형 후행문(한 빈줄 블록). 검출 run 은 문단의
#   *진부분집합*이다(WALL 은 run==문단이라 우연히 대칭 — 부분집합 케이스를 못 태운다). 실 회차의 일반형.
_SUBSET_PARA = (
    "이제 시작이다.\n"
    "그는 천천히 걸음을 옮겼다. 문 앞에서 잠시 멈췄다. 손잡이를 잡았다. "
    "문을 밀어 열었다. 안으로 들어섰다. 불을 켰다. 방을 둘러보았다.\n"
    "복도는 여전히 조용하다.\n"
)


class _FaithfulEditGen:
    """revise_prose 가 span(=체시스 확장 문단)의 종결 2개만 무손실 변주하는 스텁 — 실 체시스 관통용."""
    def __init__(self):
        self.provider = SimpleNamespace(name="gen-default")

    def revise_prose(self, directive, before_text, span_text="", **kw):
        if not span_text or span_text not in before_text:
            return before_text
        edited = span_text.replace("옮겼다", "옮겼고").replace("멈췄다", "멈췄으며")
        return before_text.replace(span_text, edited, 1)


def test_change_rate_baseline_uses_expanded_paragraph() -> bool:
    """6b) 변경률 기저 = 체시스가 반환하는 확장 문단(span_text_before) — 검출 run(진부분집합) 아님.

    적대검증 MED 재현·회귀: 실 체시스는 v2 에서 검출 run 을 문단 전체로 확장해 재작성하고 span_text_before(확장
    문단)·span_text_after 를 반환한다. before 를 확장 전 run 으로 잡으면 분모=run·분자=run↔확장문단이라 변경률이
    체계적 과대 → 정당 최소 수정을 over_change 로 오폴백. 여기서는 *실 체시스*(모킹 우회 아님·LLM 만 스텁)를
    관통해 종결 2개만 무손실 변주(실제 ~3%)한다. 기저 대칭(확장 문단)이면 under/ok 로 채택, 비대칭이면 over 로
    오폴백된다 — 이 테스트가 후자를 회귀로 막는다."""
    from novelcopilot.engine.humanize_detect import detect_ending_monotony
    findings = detect_ending_monotony(_SUBSET_PARA, run_threshold=6)
    # 전제: 검출 run 이 문단의 진부분집합(선행/후행 문장이 run 밖).
    f0 = findings[0]
    run_slice = _SUBSET_PARA[f0["span"]["char_start"]:f0["span"]["char_end"]]
    ok = (len(findings) == 1 and f0["category"] == "N-4")
    ok &= ("이제 시작이다" not in run_slice and "여전히 조용하다" not in run_slice)   # run 은 문단 부분집합

    gen = _FaithfulEditGen()
    new_text, entries = hp.humanize_spans(gen, None, None, 5, _SUBSET_PARA, findings,
                                          max_spans=6, service=None, humanize_provider=None)
    e = entries[0]
    ok &= (new_text != _SUBSET_PARA)                    # 무손실 최소 수정이 채택됨(폐기 아님)
    ok &= (e["changed"] is True)
    ok &= (e["fallback"] != "over_change")              # 과윤문 오폴백 아님(결함이면 여기서 걸림)
    ok &= (e["rate_band"] in ("under", "ok"))           # 확장 문단 기저 → 소변경(비대칭이면 over)
    ok &= (e["change_rate"] < hp.CHANGE_RATE_MAX)       # 30% 미만(비대칭이면 33%+ 로 over)
    # 직접 대칭 검증 — 실 체시스 반환의 before/after 로 계산한 변경률이 소값이어야.
    import tools.st11_span_rewrite as st11
    br = hp._chassis_span_from_finding(_SUBSET_PARA, f0)
    r = st11.rewrite_span_via_chassis(gen, None, None, 5, _SUBSET_PARA, br,
                                      directive=hp.build_directive("N-4"), service=None, mode="v2")
    symmetric = hp.change_rate(r["span_text_before"], r["span_text_after"])
    asymmetric = hp.change_rate(br["span_text"], r["span_text_after"])
    ok &= (symmetric < hp.CHANGE_RATE_MAX)              # 대칭 기저 = 소변경(채택)
    ok &= (asymmetric > symmetric)                      # 비대칭(결함) 기저는 과대 — 이 격차가 결함의 본질
    ok &= (abs(e["change_rate"] - symmetric) < 1e-9)    # 실행기가 대칭 기저를 쓴다(확장 문단)
    print(f"[{'OK' if ok else 'FAIL'}] E6b: 변경률 기저=확장 문단(부분집합 run 아님)·과윤문 오폴백 방지")
    return bool(ok)


def test_over_change_fallback() -> bool:
    """7) 과윤문(변경률 30%↑) → 그 스팬만 폴백(원문 유지)·author_review(S1)·note.

    체시스 자신의 커버리지 가드는 *내용어 소실*을 잡지만 내용어를 보존한 과윤문(전면 재구성)은 통과시킬 수
    있다 — 그 경우 HM-1b 변경률 가드가 30% 상한을 잡는다. 체시스 스택은 SP-1이 검증하므로, 여기선 체시스가
    '내용 보존한 과윤문본을 changed=True 로 반환'하는 상황을 직접 모킹해 HM-1b 변경률 가드만 격리 검증한다."""
    import tools.st11_span_rewrite as st11
    orig = st11.rewrite_span_via_chassis
    f = _finding(WALL, sev="S1")
    before_span = WALL[f["span"]["char_start"]:f["span"]["char_end"]]
    heavy = before_span + " " + ("완전히 재구성된 긴 문장. " * 8)   # 원문 보존 + 대량 추가 → 변경률 폭주(>30%)

    def over_chassis(generator, ontology, checker, chapter_no, full_text, span, **kw):
        after = full_text.replace(span["span_text"], heavy, 1)
        return {"span_text_before": span["span_text"], "span_text_after": heavy,
                "full_after": after, "changed": True,
                "coverage_guard": {"passed": True}, "guardrail": None}

    st11.rewrite_span_via_chassis = over_chassis
    try:
        gen = _MockChassisGen(lambda s: s)
        new_text, entries = hp.humanize_spans(gen, None, None, 5, WALL, [f], max_spans=6)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok = (new_text == WALL)                              # 과윤문 폴백 → 본문 불변
    e = entries[0]
    ok &= (e["changed"] is False and e["fallback"] == "over_change")
    ok &= (e["author_review"] is True)                  # S1 잔존 정직
    ok &= (e["rate_band"] == "over")
    print(f"[{'OK' if ok else 'FAIL'}] E7: 과윤문 폴백 — 본문 불변·author_review(S1)·over")
    return bool(ok)


def test_unchanged_fallback_recorded() -> bool:
    """8) 무변경 폴백 → 원문 불변·항목 기록(침묵 폴백 금지)·S1 잔존 author_review."""
    gen = _MockChassisGen(lambda s: s)   # 무변경(체시스가 못 바꿈)
    new_text, entries = hp.humanize_spans(gen, None, None, 5, WALL, [_finding(WALL, sev="S1")], max_spans=6)
    ok = (new_text == WALL)
    ok &= (len(entries) == 1 and entries[0]["changed"] is False)
    ok &= (entries[0]["fallback"] is not None)          # 침묵 폴백 금지(항목 기록)
    ok &= (entries[0]["author_review"] is True)         # S1 잔존
    print(f"[{'OK' if ok else 'FAIL'}] E8: 무변경 폴백 — 원문 불변·항목 기록·S1 author_review")
    return bool(ok)


def test_span_isolation() -> bool:
    """9) 스팬 격리 — span_not_found → 그 스팬만 폴백·전진(회차 안 죽음)."""
    import tools.st11_span_rewrite as st11
    calls = {"n": 0}
    orig = st11.rewrite_span_via_chassis

    def boom(*a, **k):
        calls["n"] += 1
        raise ValueError("span_not_found")

    st11.rewrite_span_via_chassis = boom
    try:
        gen = _MockChassisGen(lambda s: s)
        new_text, entries = hp.humanize_spans(gen, None, None, 5, WALL, [_finding(WALL, sev="S1")], max_spans=6)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok = (new_text == WALL)
    ok &= (len(entries) == 1 and entries[0]["fallback"] == "span_not_found")
    ok &= (entries[0]["author_review"] is True)
    ok &= (calls["n"] == 1)
    print(f"[{'OK' if ok else 'FAIL'}] E9: 스팬 격리 — span_not_found 폴백·전진·S1 author_review")
    return bool(ok)


def test_not_local_skip() -> bool:
    """10) not_local(전범위 밀도 신호) → 스킵·기록·체시스 미호출. author_review 는 켜지 않는다(cry-wolf 방지).

    적대검증 MED 수리: not_local 은 국소 수술 *진입조차 안 한* 신호(계약 밖)다. author_review 는 "윤문
    시도→못 걷어낸 결정적 티 잔존"의 정직 표기이므로, HM-1a detect_layer_wall 이 N-5 S1 을 무임계·상시
    방출하는 것을 not_local 스킵에서 author_review=True 로 받으면 매 회차 cry-wolf 가 된다 → author_review=False."""
    import tools.st11_span_rewrite as st11
    calls = {"n": 0}
    orig = st11.rewrite_span_via_chassis

    def spy(*a, **k):
        calls["n"] += 1
        return {"changed": False}

    st11.rewrite_span_via_chassis = spy
    try:
        full = {"category": "N-5", "severity": "S1",
                "span": {"char_start": 0, "char_end": len(WALL), "text": ""}}
        gen = _MockChassisGen(lambda s: s)
        new_text, entries = hp.humanize_spans(gen, None, None, 5, WALL, [full], max_spans=6)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok = (new_text == WALL)
    ok &= (len(entries) == 1 and entries[0]["fallback"] == "not_local")
    ok &= (entries[0]["author_review"] is False)        # 시도 없는 스킵 → cry-wolf 금지(S1 이라도 안 켬)
    ok &= (calls["n"] == 0)                              # 국소 수술 불가 → 체시스 미호출
    print(f"[{'OK' if ok else 'FAIL'}] E10: not_local 스킵 — 체시스 미호출·기록·author_review 미발화(cry-wolf 방지)")
    return bool(ok)


def test_provider_scope_swap() -> bool:
    """11) Claude provider 스코프 스왑 — 윤문 콜(체시스 진입) 시점 generator.provider=Claude·종료 후 복원.

    체시스는 커버리지 재시도로 revise_prose 를 여러 번 부를 수 있으므로, 스왑 관측은 '체시스 진입 시점의
    generator.provider' 를 본다(콜 횟수 무관). 체시스 스택은 SP-1이 검증 — 여기선 스왑/복원만 격리한다."""
    import tools.st11_span_rewrite as st11
    orig = st11.rewrite_span_via_chassis
    claude = SimpleNamespace(name="claude-humanize")

    # 정상 경로: 체시스 진입 시 provider 는 Claude, 반환 후 복원.
    def obs_chassis(generator, ontology, checker, chapter_no, full_text, span, **kw):
        obs_chassis.seen = getattr(generator.provider, "name", None)
        return {"span_text_before": span["span_text"], "span_text_after": span["span_text"],
                "full_after": full_text, "changed": False,
                "coverage_guard": {"passed": True}, "guardrail": None}

    st11.rewrite_span_via_chassis = obs_chassis
    try:
        gen = _MockChassisGen(lambda s: s)
        hp.humanize_spans(gen, None, None, 5, WALL, [_finding(WALL)], max_spans=1, humanize_provider=claude)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok = (getattr(obs_chassis, "seen", None) == "claude-humanize")   # 콜 동안 스왑됨
    ok &= (gen.provider.name == "gen-default")                        # 복원됨

    # 예외 경로 복원 — span_not_found 폴백 후에도 provider 복원.
    def boom(generator, *a, **k):
        boom.seen = getattr(generator.provider, "name", None)
        raise ValueError("span_not_found")

    st11.rewrite_span_via_chassis = boom
    try:
        gen2 = _MockChassisGen(lambda s: s)
        hp.humanize_spans(gen2, None, None, 5, WALL, [_finding(WALL)], max_spans=1, humanize_provider=claude)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok &= (getattr(boom, "seen", None) == "claude-humanize")   # 예외 콜도 스왑됨
    ok &= (gen2.provider.name == "gen-default")                # 예외 후 복원됨

    # humanize_provider=None → 스왑 없음(기본 provider 유지·하위호환).
    def obs2(generator, ontology, checker, chapter_no, full_text, span, **kw):
        obs2.seen = getattr(generator.provider, "name", None)
        return {"span_text_before": span["span_text"], "span_text_after": span["span_text"],
                "full_after": full_text, "changed": False, "coverage_guard": {}, "guardrail": None}
    st11.rewrite_span_via_chassis = obs2
    try:
        gen3 = _MockChassisGen(lambda s: s)
        hp.humanize_spans(gen3, None, None, 5, WALL, [_finding(WALL)], max_spans=1, humanize_provider=None)
    finally:
        st11.rewrite_span_via_chassis = orig
    ok &= (getattr(obs2, "seen", None) == "gen-default")   # 스왑 없음(기본 provider)
    print(f"[{'OK' if ok else 'FAIL'}] E11: provider 스코프 스왑 — 콜 동안 Claude·복원(예외 포함)·None=미스왑")
    return bool(ok)


def test_off_no_spans() -> bool:
    """상한 0/빈 findings → (원문, []) 강등(윤문 호출 0)."""
    class NeverGen:
        provider = SimpleNamespace(name="x")
        def revise_prose(self, *a, **k):
            raise AssertionError("윤문 대상 없는데 호출됨")
    new_text, entries = hp.humanize_spans(NeverGen(), None, None, 5, WALL, [_finding(WALL)], max_spans=0)
    ok = (new_text == WALL and entries == [])
    new2, e2 = hp.humanize_spans(NeverGen(), None, None, 5, WALL, [], max_spans=6)
    ok &= (new2 == WALL and e2 == [])
    print(f"[{'OK' if ok else 'FAIL'}] E: 상한 0/빈 findings → (원문, [])·호출 0")
    return bool(ok)


# ═════════════════ F. 배선(harness e2e) ═════════════════

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


def test_harness_hm1_passthrough_e2e() -> bool:
    """12)+13) harness 관통 — humanize ON 시 HM-1(humanize_spans) 호출·본문 반영·humanize 영속·usage 계상·
    service 관통, 그리고 Stage B(repair_spans) 미호출(흡수·이중 패스 금지)."""
    from novelcopilot.config import Settings
    settings = Settings()   # humanize=True(기본)
    prov = _Cap()

    spy_hm = {"called": 0, "service": "UNSET", "in_text": None}
    spy_sb = {"called": 0}
    MARK = "\n\n[HM1-MARK]"

    def fake_humanize(generator, ontology, checker, chapter_no, text, findings, **kw):
        spy_hm["called"] += 1
        spy_hm["service"] = kw.get("service", "MISSING")
        spy_hm["in_text"] = text
        return text + MARK, [{"category": "N-4", "severity": "S2", "changed": True,
                              "change_rate": 0.12, "rate_band": "ok", "fallback": None,
                              "author_review": False}]

    def fake_repair(*a, **k):
        spy_sb["called"] += 1
        return a[4], []

    import novelcopilot.engine.humanize_pass as hpmod
    import novelcopilot.engine.style_pipeline as spmod
    real_hm, real_sb = hpmod.humanize_spans, spmod.repair_spans
    hpmod.humanize_spans = fake_humanize
    spmod.repair_spans = fake_repair
    try:
        g = ChapterGenerator(prov, checker=_Chk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
        marker = object()
        g.service = marker
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        rec = g.generate(2, beat, _Ont(), _Rag(), _Wiki())
    finally:
        hpmod.humanize_spans = real_hm
        spmod.repair_spans = real_sb

    ok = (spy_hm["called"] == 1)                          # HM-1 정확히 1회
    ok &= (spy_hm["service"] is marker)                  # service 관통(G-B 실배선)
    ok &= (MARK in (rec.text or ""))                     # 윤문 본문이 하류로 흐름
    # HZ-1: humanize 내역 = 실 윤문 엔트리(fake changed=True) + N-4 판정 스킵/금기 기록(은폐 금지·additive).
    #   fake provider(_Cap.chat_json={})는 needs_repair=False 를 내므로 N-4 스킵 1건이 additive 로 붙는다.
    _changed_entries = [e for e in rec.humanize if e.get("changed") is True]
    ok &= (len(_changed_entries) == 1)                   # 실 윤문 채택 엔트리 1건 영속
    ok &= ("humanize" in rec.usage_by_stage)             # usage 계상
    ok &= ("style_judge" in rec.usage_by_stage)          # HZ-1: 스타일 지각 판정 스테이지 usage 계상(매화 무조건)
    ok &= (spy_sb["called"] == 0)                        # Stage B 흡수(미호출·이중 패스 금지)
    ok &= (rec.style_repairs == [])                      # HM-1 ON 시 style_repairs 빈
    print(f"[{'OK' if ok else 'FAIL'}] F12/13: HM-1 관통·본문 반영·영속·service·Stage B 흡수·style_judge usage")
    return bool(ok)


def test_harness_off_falls_back_to_stageB() -> bool:
    """14) humanize OFF → HM-1 미호출·Stage B(repair_spans) 폴백(하위호환)."""
    from novelcopilot.config import Settings
    settings = Settings()
    object.__setattr__(settings, "humanize", False)   # OFF → Stage B 폴백
    prov = _Cap()

    spy_hm = {"called": 0}
    spy_sb = {"called": 0}

    def fake_humanize(*a, **k):
        spy_hm["called"] += 1
        return a[4], []

    def fake_repair(*a, **k):
        spy_sb["called"] += 1
        return a[4], []

    import novelcopilot.engine.humanize_pass as hpmod
    import novelcopilot.engine.style_pipeline as spmod
    real_hm, real_sb = hpmod.humanize_spans, spmod.repair_spans
    hpmod.humanize_spans = fake_humanize
    spmod.repair_spans = fake_repair
    try:
        g = ChapterGenerator(prov, checker=_Chk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        rec = g.generate(2, beat, _Ont(), _Rag(), _Wiki())
    finally:
        hpmod.humanize_spans = real_hm
        spmod.repair_spans = real_sb

    ok = (spy_hm["called"] == 0)                          # HM-1 미호출
    ok &= (spy_sb["called"] == 1)                        # Stage B 폴백 호출
    ok &= (rec.humanize == [])                           # HM-1 미실행 → humanize 빈
    print(f"[{'OK' if ok else 'FAIL'}] F14: humanize OFF → HM-1 미호출·Stage B 폴백(하위호환)")
    return bool(ok)


# ═════════════════ G. verification 기록 ═════════════════

def test_harness_humanize_on_zero_spans_no_stageB() -> bool:
    """흡수 불변식 — humanize ON + humanize_max_spans=0(탐지만) → HM-1 실 윤문 0 AND Stage B 미호출(폴백 새지 않음)."""
    from novelcopilot.config import Settings
    settings = Settings()
    object.__setattr__(settings, "humanize_max_spans", 0)   # 탐지만(윤문 0) — 그래도 Stage B 로 폴백 금지
    prov = _Cap()

    spy_hm = {"called": 0}
    spy_sb = {"called": 0}

    def fake_humanize(*a, **k):
        spy_hm["called"] += 1
        return a[4], []

    def fake_repair(*a, **k):
        spy_sb["called"] += 1
        return a[4], []

    import novelcopilot.engine.humanize_pass as hpmod
    import novelcopilot.engine.style_pipeline as spmod
    real_hm, real_sb = hpmod.humanize_spans, spmod.repair_spans
    hpmod.humanize_spans = fake_humanize
    spmod.repair_spans = fake_repair
    try:
        g = ChapterGenerator(prov, checker=_Chk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        rec = g.generate(2, beat, _Ont(), _Rag(), _Wiki())
    finally:
        hpmod.humanize_spans = real_hm
        spmod.repair_spans = real_sb

    ok = (spy_hm["called"] == 0)      # max_spans=0 → humanize_spans 진입 전 상한 가드로 미호출
    ok &= (spy_sb["called"] == 0)     # 흡수 불변: humanize ON 이면 Stage B 폴백 금지(상한 0이라도)
    ok &= (rec.humanize == [] and rec.style_repairs == [])
    print(f"[{'OK' if ok else 'FAIL'}] F: humanize ON + max_spans=0 → Stage B 미호출(흡수 불변)")
    return bool(ok)


def test_verification_humanize_axis() -> bool:
    """build_verification.humanize 축 — before/after 계측 집계(폐루프·설계 ⓓ)·무판정."""
    from novelcopilot.engine.verification import build_verification, _summ_humanize
    rec = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text=WALL, title="t")
    rec.humanize = [
        {"category": "N-4", "severity": "S2", "changed": True, "change_rate": 0.12,
         "rate_band": "ok", "fallback": None, "author_review": False},
        {"category": "N-1", "severity": "S1", "changed": False, "fallback": "over_change",
         "rate_band": "over", "author_review": True},
        {"category": "N-4", "severity": "S2", "changed": True, "change_rate": 0.20,
         "rate_band": "ok", "fallback": None, "author_review": False},
    ]
    s = _summ_humanize(rec.humanize)
    ok = (s["spans"] == 3 and s["changed"] == 2 and s["fallback"] == 1 and s["over_change"] == 1)
    ok &= (s["author_review"] == 1)
    ok &= (abs(s["avg_change_rate"] - round((0.12 + 0.20) / 2, 4)) < 1e-9)   # 변경 스팬 한정 평균
    ok &= (s["by_category"] == {"N-4": 2, "N-1": 1})
    # build_verification 에 humanize 축 실림
    v = build_verification(rec, prev_texts=[], target_chars=None)
    ok &= ("humanize" in v)
    ok &= (v["humanize"]["spans"] == 3 and v["humanize"]["changed"] == 2)
    # 빈 humanize → 0건 집계(MISSING 아님·필드 항상 존재)
    empty = _summ_humanize([])
    ok &= (empty["spans"] == 0 and empty["changed"] == 0 and empty["by_category"] == {})
    print(f"[{'OK' if ok else 'FAIL'}] G: verification.humanize 축(집계·평균변경률·카테고리별·빈=0건)")
    return bool(ok)


# ═════════════════ H. 하위호환 ═════════════════

def test_backcompat_old_json() -> bool:
    """구 JSON(humanize 없는) 로드 → humanize default=[]·재직렬화 정합(additive)."""
    old = {"chapter": 3, "status": "FINALIZED", "text": "본문", "style_repairs": []}
    rec = ChapterRecord.model_validate(old)
    ok = (rec.humanize == [])                            # additive default
    ok &= (rec.style_repairs == [])
    ok &= (rec.status == ChapterStatus.FINALIZED)
    rt = ChapterRecord.model_validate(json.loads(rec.model_dump_json()))
    ok &= (rt.humanize == [])                            # 재직렬화 정합
    print(f"[{'OK' if ok else 'FAIL'}] H: 구 JSON 로드·humanize default=[]·재직렬화 정합")
    return bool(ok)


def main() -> int:
    results = [
        test_change_rate_arithmetic(),
        test_change_rate_band(),
        test_category_directives(),
        test_chassis_span_bridge(),
        test_select_priority(),
        test_actual_humanize_updates_text(),
        test_change_rate_baseline_uses_expanded_paragraph(),
        test_over_change_fallback(),
        test_unchanged_fallback_recorded(),
        test_span_isolation(),
        test_not_local_skip(),
        test_provider_scope_swap(),
        test_off_no_spans(),
        test_harness_hm1_passthrough_e2e(),
        test_harness_off_falls_back_to_stageB(),
        test_harness_humanize_on_zero_spans_no_stageB(),
        test_verification_humanize_axis(),
        test_backcompat_old_json(),
    ]
    print("\nHM-1b(Claude 윤문 패스+배선) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


def test_hm1b_humanize_pass_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())


def test_guardrail_fail_blocks_acceptance(monkeypatch):
    """G-B 가드 불통과 수술은 채택 금지(원문 유지·fallback=guardrail) — ch10 실측 계약 위반의 회귀 방어."""
    import novelcopilot.engine.humanize_pass as hp

    def fake_chassis(gen, onto, chk, ch, text, sp, directive=None, service=None, mode="v2", **kw):
        return {"span_text_before": sp["span_text"], "span_text_after": sp["span_text"].replace("열었다", "연다"),
                "full_after": text.replace("열었다", "연다"),
                "guardrail": {"passed": False, "reason": "클레임 표면 변경"},
                "coverage_guard": {"passed": True}, "changed": True}

    import tools.st11_span_rewrite as st11
    monkeypatch.setattr(st11, "rewrite_span_via_chassis", fake_chassis)
    text = "그는 문을 열었다. 밖은 어두웠다. 바람이 불었다."
    spans = [{"category": "N-4", "severity": "S2", "metric": {},
              "span": {"char_start": 0, "char_end": len("그는 문을 열었다."), "text": "그는 문을 열었다."}}]
    new_text, entries = hp.humanize_spans(None, None, None, 1, text, spans, service=object())
    assert new_text == text                      # 원문 유지
    assert entries[0]["changed"] is False
    assert entries[0]["fallback"] == "guardrail"
    # CE-7: 반려 증거 영속 — 막힌 재작성문·가드 상세를 기록(소급 복원 불가하던 것을 남긴다).
    assert entries[0]["rejected_after_span"] == "그는 문을 연다."
    assert entries[0]["guardrail_detail"]["reason"] == "클레임 표면 변경"


def test_guardrail_none_degradation_accepts(monkeypatch):
    """가드 미실행(None — service 부재 강등)은 결측 정직 기록만, 채택은 유지."""
    import novelcopilot.engine.humanize_pass as hp

    def fake_chassis(gen, onto, chk, ch, text, sp, directive=None, service=None, mode="v2", **kw):
        return {"span_text_before": sp["span_text"], "span_text_after": sp["span_text"].replace("열었다", "연다"),
                "full_after": text.replace("열었다", "연다"),
                "guardrail": None, "coverage_guard": {"passed": True}, "changed": True}

    import tools.st11_span_rewrite as st11
    monkeypatch.setattr(st11, "rewrite_span_via_chassis", fake_chassis)
    text = "그는 문을 열었다. 밖은 어두웠다. 바람이 불었다."
    spans = [{"category": "N-4", "severity": "S2", "metric": {},
              "span": {"char_start": 0, "char_end": len("그는 문을 열었다."), "text": "그는 문을 열었다."}}]
    new_text, entries = hp.humanize_spans(None, None, None, 1, text, spans, service=None)
    assert entries[0]["changed"] is True
    assert entries[0]["guardrail_ok"] is None


def test_ce5_threading_reuses_before_res_when_ids_match():
    """CE-5: 직전 스팬 after_res 를 다음 스팬 before_res 로 재사용(ids 동일 시) → check_text 재계산 0.
    ids 불일치면 전문 재추출 폴백(결측 정직). 캐시가 아니라 오케스트레이션 층 결과 전달(VP-3 요동 판정 무침범)."""
    import tools.st11_span_rewrite as st11
    from novelcopilot.engine.humanize_detect import detect_ending_monotony

    calls = {"n": 0}

    class _R:                       # CheckResult 유사(최소 — 스레딩 경로는 값을 그대로 나른다)
        hard = []
        claims = []

    class _Ont:
        def scan_present_ids(self, t):
            return ["x"]

    class _Chk:
        def check_text(self, text, ont, ch, ids):
            calls["n"] += 1
            return _R()

    class _Svc:
        def _guardrail(self, bt, at, br, ids, ont, chk, ch):
            return {"passed": True}, _R()

    gen = _FaithfulEditGen()
    br = hp._chassis_span_from_finding(_SUBSET_PARA, detect_ending_monotony(_SUBSET_PARA, run_threshold=6)[0])
    ont, chk, svc = _Ont(), _Chk(), _Svc()
    d = hp.build_directive("N-4")

    # 1) 스레딩 없음 → before 추출 1회, after_res·ids 반환
    calls["n"] = 0
    r1 = st11.rewrite_span_via_chassis(gen, ont, chk, 5, _SUBSET_PARA, br, directive=d, service=svc, mode="v2")
    assert calls["n"] == 1
    assert r1["check_ids"] == ["x"]
    assert r1["after_res"] is not None            # 재작성 발생 → _guardrail 이 after 계산

    # 2) ids 동일 스레딩 → before 재사용(재계산 0)
    calls["n"] = 0
    st11.rewrite_span_via_chassis(gen, ont, chk, 5, _SUBSET_PARA, br, directive=d, service=svc, mode="v2",
                                  threaded_before_res=r1["after_res"], threaded_before_ids=["x"])
    assert calls["n"] == 0

    # 3) ids 불일치 → 전문 재추출 폴백(침묵 재활용 금지)
    calls["n"] = 0
    st11.rewrite_span_via_chassis(gen, ont, chk, 5, _SUBSET_PARA, br, directive=d, service=svc, mode="v2",
                                  threaded_before_res=r1["after_res"], threaded_before_ids=["different"])
    assert calls["n"] == 1


def test_ce5_loop_threads_after_res_to_next_span(monkeypatch):
    """CE-5 루프 배선(이 티켓의 유일 위험 축 = 스테일 before_res): humanize_spans 가 스팬 N 의 after_res 를
    스팬 N+1 의 threaded_before_res 로 실제 넘긴다. fake 가 받은 인자를 기록해 검증한다 — 기존 fake 는
    스레딩 키를 안 돌려줘 이 배선이 항상 OFF 인 채 통과했다(PM 발견)."""
    import tools.st11_span_rewrite as st11
    from novelcopilot.engine.humanize_detect import detect_ending_monotony

    seen = []   # 각 스팬 콜이 받은 threaded_before_res

    def rec_fake(gen, onto, chk, ch, text, sp, directive=None, service=None, mode="v2",
                 threaded_before_res=None, threaded_before_ids=None):
        seen.append(threaded_before_res)
        return {"span_text_before": sp["span_text"], "span_text_after": sp["span_text"] + "!",
                "full_after": text + "​",     # 실제 변경(cur 갱신) → 채택
                "changed": True, "guardrail": {"passed": True}, "coverage_guard": {"passed": True},
                "before_res": "BR", "after_res": "AR", "check_ids": ["x"]}

    class _Ont:
        def scan_present_ids(self, t):
            return ["x"]

    # HM-4는 같은 문단의 findings를 한 번의 수술로 그룹핑한다. CE-5 스레딩을 실제로
    # 태우려면 두 run이 서로 다른 문단에 있어야 한다(한 문단이면 호출 1회가 정상).
    two = ("그가 왔다. 밖이 어두웠다. 바람이 불었다. 문이 닫혔다. 무언가 이상하지.\n\n"
           "그녀가 섰다. 눈이 내렸다. 길이 얼었다. 손이 굳었다.")
    findings = detect_ending_monotony(two, run_threshold=4)
    assert len(findings) >= 2, "테스트 전제: 2 스팬 이상 검출(중간 종결 전환으로 run 분리)"

    monkeypatch.setattr(st11, "rewrite_span_via_chassis", rec_fake)
    hp.humanize_spans(object(), _Ont(), object(), 1, two, findings, max_spans=6, service=object())

    assert seen[0] is None                 # 첫 스팬 = 스레딩 없음(st11 내부 계산 경로)
    assert seen[1] == "AR"                 # 둘째 스팬 = 스팬0 채택본의 after_res 를 받음(before 아님·스테일 아님)
