# -*- coding: utf-8 -*-
"""XR-36 검증 — 2축 StagePolicy(operation·scope)·AST census·의미 계약 (실 LLM 0콜).

계약(cross-review/029 §2): ⑴ 정책 결속 키는 단일 enum 이 아니라 (operation, scope) 2축 —
"결말을 읽는 검증기" 같은 조합이 표현된다 ⑵ census 는 따옴표 문법 무관(AST)·동적 라벨 0
⑶ 의미 검증은 완전성 검사와 분리 — 029 반례(ending_contract·style_rhythm)를 고정 fixture 로 잠그고,
결말 scope 는 프롬프트 조립 실측(허용 실증)으로 계약화한다.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr36_stage_policy.py
"""
from __future__ import annotations
import sys
import pathlib
import tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from novelcopilot.llm.promptlog import (OPERATIONS, SCOPES, STAGE_POLICIES, STAGE_CONSUMER_MAP,
                                        UNCLASSIFIED, StagePolicy, stage_policy, consumer)
from stage_census import declared_labels, dynamic_label_sites


def test_policy_structure_census() -> bool:
    """구조 검증(029 §2.3): 코드 스테이지 전수 == 정책 키 전수 · 값은 canonical 축 · 동적 라벨 0."""
    code = declared_labels()
    ok = code == set(STAGE_POLICIES)
    if not ok:
        print("  미등록:", sorted(code - set(STAGE_POLICIES)),
              "| 죽은 키:", sorted(set(STAGE_POLICIES) - code))
    ok &= all(p.operation in OPERATIONS and p.scope in SCOPES for p in STAGE_POLICIES.values())
    ok &= dynamic_label_sites() == []
    ok &= set(STAGE_POLICIES) == set(STAGE_CONSUMER_MAP)   # 두 층(정책·관측 그룹)은 같은 전수를 다룬다
    print(f"[{'OK' if ok else 'FAIL'}] 구조: 전수 {len(code)}종 == 정책 키 · 축 canonical · 동적 0")
    assert ok
    return ok


def test_census_is_quote_agnostic() -> bool:
    """029 §2.2 사각지대의 역: single-quote·여러 줄·직접 호출 형태도 AST 가 수집한다."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="xr36q_"))
    (d / "probe.py").write_text(
        "import promptlog\n"
        "@promptlog.stage('single_quoted_stage')\n"
        "def f():\n    pass\n"
        "def g():\n"
        "    with promptlog.consumer(\n        'multi_line_stage'\n    ):\n        pass\n"
        "label = 'dyn'\n"
        "def h():\n"
        "    with promptlog.consumer(label):\n        pass\n",
        encoding="utf-8")
    found = declared_labels(d)
    dyn = dynamic_label_sites(d)
    ok = found == {"single_quoted_stage", "multi_line_stage"} and dyn == ["probe.py:12"]
    print(f"[{'OK' if ok else 'FAIL'}] 따옴표 무관 수집: {sorted(found)} · 동적 지점 검출 {dyn}")
    assert ok
    return ok


def test_two_axis_fixtures() -> bool:
    """의미 고정 fixture(029 §2.1 반례의 계약화) — 재분류는 이 테스트를 깨고 근거와 함께 갱신하라."""
    ok = stage_policy("ending_contract") == StagePolicy("verification", "ending")
    ok &= stage_policy("style_rhythm") == StagePolicy("evaluation", "current_prose")
    ok &= STAGE_CONSUMER_MAP["style_rhythm"] == "evaluation"          # 관측 그룹도 재분류(029)
    # 스토리 패스 혼합 실증 — 한 그룹(chapter_planning) 안에서 operation 이 갈린다
    ok &= stage_policy("story_pass:base").operation == "planning"
    ok &= stage_policy("story_pass:review").operation == "evaluation"
    ok &= stage_policy("story_pass:audit").operation == "verification"
    ok &= all(stage_policy(s).scope == "story_plan"
              for s in STAGE_POLICIES if s.startswith("story_pass:"))
    # 그룹≠operation 분리 실증 — 퇴고 파이프라인 소속의 advisory 평가기
    ok &= STAGE_CONSUMER_MAP["redpen_suggest"] == "revision"
    ok &= stage_policy("redpen_suggest").operation == "evaluation"
    ok &= stage_policy("없는_스테이지") == StagePolicy(UNCLASSIFIED, UNCLASSIFIED)
    print(f"[{'OK' if ok else 'FAIL'}] 2축 fixture: ending_contract·style_rhythm·스토리패스 혼합·그룹≠op")
    assert ok
    return ok


def test_ending_scope_enumeration_locked() -> bool:
    """결말 열람은 scope=ending 스테이지뿐 — 조립 실측으로 배정한 4종 열거 잠금(추가=RED·근거 요구)."""
    measured = {"worldgen:spine", "worldgen:episodes", "retrospective", "ending_contract"}
    got = {s for s, p in STAGE_POLICIES.items() if p.scope == "ending"}
    ok = got == measured
    print(f"[{'OK' if ok else 'FAIL'}] ending scope 열거 잠금: {sorted(got)}")
    assert ok
    return ok


def test_ending_contract_prompt_actually_reads_ending() -> bool:
    """의미 검증 — 허용 실증(029 §3-5: 문서 생성 테스트로 대체 금지): ending_contract 프롬프트에
    결말 3필드가 실제로 들어간다(scope=ending 이 구현과 일치함을 조립 실측으로 증명).
    부재 축(비 ending scope 경로의 결말 미주입)은 기존 조립 실측 스위트가 잠근다 —
    test_xr1_ending_leak(스윕+E2E)·story_pass hygiene ending_literal_leak·FS-1."""
    from novelcopilot.engine.ending_contract import compile_predicates
    captured: dict = {}

    class _Prov:
        gen_model = "fake"

        def chat_json(self, messages, **kw):
            captured["prompt"] = "\n".join(str(m.get("content", "")) for m in messages)
            return {"predicates": []}
    ending = {"central_question": "그 소리의 정체는 무엇인가",
              "ending": "정체가 밝혀지고 답을 마주한다", "thematic_payoff": "듣는 자의 책임"}
    registry = {"entities": [], "attrs": {}, "rel_ids": [], "promises": [], "clock_units": []}
    compile_predicates(_Prov(), ending, registry=registry)
    p = captured.get("prompt", "")
    ok = all(v in p for v in ending.values())
    print(f"[{'OK' if ok else 'FAIL'}] 의미(허용 실증): ending_contract 프롬프트에 결말 3필드 실재")
    assert ok
    return ok


def test_log_header_carries_axes() -> bool:
    """로그 헤더에 op·scope 병기(additive — 프롬프트 바이트 무관·xr14 헤더 테스트와 동형)."""
    import novelcopilot.llm.promptlog as PL

    class _Inner:
        gen_model = "m"

        def chat_json(self, messages, **k):
            return {}
    orig_root = PL._LOG_ROOT
    PL._LOG_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="xr36log_"))
    try:
        prov = PL.PromptLoggingProvider(_Inner(), tag="t")
        with consumer("ending_contract"):
            prov.chat_json([{"role": "user", "content": "x"}])
        head = next(PL._LOG_ROOT.rglob("*.txt")).read_text(encoding="utf-8").splitlines()[0]
        ok = "op=verification" in head and "scope=ending" in head
    finally:
        PL._LOG_ROOT = orig_root
    print(f"[{'OK' if ok else 'FAIL'}] 로그 헤더: op·scope 병기")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_policy_structure_census(), test_census_is_quote_agnostic(),
               test_two_axis_fixtures(), test_ending_scope_enumeration_locked(),
               test_ending_contract_prompt_actually_reads_ending(), test_log_header_carries_axes()]
    print("\nXR-36 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
