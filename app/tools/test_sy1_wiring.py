# -*- coding: utf-8 -*-
"""SY-1 배선 회귀 (설계 §10 — 자가시험 레포 승격 3/3). LLM 0·결정론.

ⓐ 미설정 시 assemble 바이트 계약 ⓑ story 모드 렌더 ⓒ 구 JSON 하위호환
ⓓ gen_context 격리 AST 스캔 ⓔ 잔여 줄 결정론 ⓕ 자동 승격 0 ⓖ repair 직접 콜 0."""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from novelcopilot.domain.types import ContextBoard, SceneSpec, StoryPassRecord
from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.engine.prompts import PromptAssembler
from novelcopilot.engine import story_pass_prompts as P
from novelcopilot.engine.drift import uncovered

_ENGINE = os.path.join(os.path.dirname(__file__), "..", "novelcopilot", "engine")

STORY = "\n".join(f"- 사건 {i}이 벌어지고, 그가 다음 수를 고른다." for i in range(12))


def _assemble(**board_kw):
    a = PromptAssembler(StyleSpec())
    b = ContextBoard(chapter=5, **board_kw)
    s = SceneSpec(index=0, goal="목표", key_events=["사건A", "사건B"])
    return a.assemble(b, s, "")


def test_legacy_byte_contract():
    # ⓐ 필드 미설정 = 명시적 "" — 스토리 표식 0·핵심사건 줄 유지(결정성 포함)
    legacy = _assemble()
    assert legacy == _assemble(confirmed_story="", story_remaining="")
    assert "핵심사건: 사건A" in legacy and "[이번 화 확정 스토리]" not in legacy


def test_story_mode_render():
    # ⓑ 치환 대상은 '핵심사건:' 한 줄뿐(목표 줄 보존 — F-B/M-D), 스토리 블록은 말미 배치
    out = _assemble(confirmed_story=STORY)
    assert "핵심사건" not in out and "[이번 장면 목표]" in out
    assert P.STORY_LAYER_LABEL in out and P.COVERAGE_REALIZE in out
    assert out.rstrip().endswith(STORY.splitlines()[-1])
    cont = _assemble(story_remaining=P.build_story_block_continue(["- 남은 줄이 이어진다."]))
    assert "이어 실현할 줄" in cont and P.COVERAGE_START not in cont   # 시작점 지정은 초안 전용(M-A)


def test_old_json_loads_without_story_passes():
    # ⓒ additive 하위호환 — story_passes 키 없는 구 JSON 무변경 로드
    from novelcopilot.domain.world import WorldConfig
    st = ProjectState(id="t", seed=ProjectSeed(), world=WorldConfig(title="t"))
    assert st.story_passes == []
    rec = StoryPassRecord(chapter=5)
    assert rec.status == "candidate" and rec.confirmed_story == ""


def test_gen_context_isolation_ast():
    # ⓓ §3: 스토리 패스 모듈은 gen_context(생성 산출)를 어떤 형태로도 읽지 않는다 —
    #   문자열 리터럴 0·속성 접근 0·ChapterRecord 타입 주석 0·산출층 필드 접근 0.
    banned_attrs = {"gen_context", "verification", "humanize", "style_repairs",
                    "reader_feedback", "dialogue_ledger", "usage_by_stage"}
    for fn in ("story_pass.py", "story_pass_prompts.py"):
        src = open(os.path.join(_ENGINE, fn), encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "gen_context" not in node.value, f"{fn}: gen_context 리터럴"
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned_attrs, f"{fn}: 산출층 필드 접근 {node.attr}"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                anns = [a.annotation for a in node.args.args if a.annotation is not None]
                for a in anns:
                    assert "ChapterRecord" not in ast.dump(a), f"{fn}: ChapterRecord 주석"


def test_brief_header_bytes_pinned():
    # 최종 감사 중대 3: 변형 헤더는 story 모드 전용 — 레거시(사건 0줄 이어쓰기 포함) 바이트 보존
    from novelcopilot.engine.fact_sheet import build_brief
    legacy_cont = build_brief("수요일 밤", [])                      # 레거시 이어쓰기(사건 0줄)
    assert "이 사건들을" in legacy_cont
    story_cont = build_brief("수요일 밤", [], story_mode=True)      # story 모드(사건 표면 봉쇄)
    assert "이 사건들을" not in story_cont and "확정 설정·세계 규칙" in story_cont
    assert build_brief("수요일 밤", ["A"], story_mode=True).count("이 사건들을") == 1  # 사건 있으면 기존 헤더


def test_remaining_lines_deterministic_with_live_uncovered():
    # ⓔ 엔진 커버리지 자산(어간 과반·보수적) 재사용 — 같은 입력 = 같은 출력
    prose = "사건 0이 벌어지고, 그가 다음 수를 고른다는 판단이었다."
    r1 = P.remaining_story_lines(STORY, prose, uncovered)
    r2 = P.remaining_story_lines(STORY, prose, uncovered)
    assert r1 == r2 and all(l.startswith("- ") for l in r1)


def test_no_auto_promotion():
    # ⓕ status != confirmed → 주입 ""(자동 승격 경로 부재)
    from novelcopilot.services.copilot import CopilotService
    from novelcopilot.domain.world import WorldConfig
    st = ProjectState(id="t", seed=ProjectSeed(), world=WorldConfig(title="t"))
    st.story_passes = [StoryPassRecord(chapter=5, status="candidate", confirmed_story="")]
    assert CopilotService._confirmed_story_for(None, st, 5) == ""
    st.story_passes = [StoryPassRecord(chapter=5, status="confirmed", confirmed_story=STORY)]
    assert CopilotService._confirmed_story_for(None, st, 5) == STORY
    st.story_passes[0].status = "discarded"
    assert CopilotService._confirmed_story_for(None, st, 5) == ""


def test_no_direct_repair_call_in_wiring():
    # ⓖ §7: 배선 코드에 build_repair_sys 직접 호출 0(가드 없는 재작성 경로 차단).
    #   시연 전용 심볼(build_sys_gen·build_user_gen·build_repair_sys)도 배선 미참조.
    demo_only = ("build_repair_sys", "build_sys_gen", "build_user_gen")
    for fn in ("story_pass.py", os.path.join("..", "services", "copilot.py"),
               "harness.py", "prompts.py"):
        src = open(os.path.join(_ENGINE, fn), encoding="utf-8").read()
        for sym in demo_only:
            assert f"{sym}(" not in src or fn == "story_pass_prompts.py", f"{fn}: 시연 전용 {sym} 호출"
