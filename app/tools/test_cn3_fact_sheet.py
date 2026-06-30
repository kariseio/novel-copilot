# -*- coding: utf-8 -*-
"""CN-3 회차 집필 브리프(fact_sheet) 테스트 — 초점화 조립·길이상한·빈입력·최상단 배치·캐논 비복제. LLM 0콜.

설계(적대검증 반영): 브리프는 캐논(등장 고정값)을 복제하지 않는다 — 시점 + 이번 사건만. 캐논은 인접한 [확정 설정]에.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.engine.fact_sheet import build_brief
from novelcopilot.domain.types import OntologyFact, ContextBoard, SceneSpec
from novelcopilot.domain.world import StyleSpec
from novelcopilot.engine.prompts import PromptAssembler


def test_brief_assembles_focused():
    b = build_brief("이야기 시작 후 4.1일 경과", ["균열 진입", "각성 발현"])
    assert "집필 기준" in b
    assert "4.1일" in b and "역행" in b                       # 시점 + 역행 금지
    assert "균열 진입 → 각성 발현" in b                       # 이번 사건(순서 힌트 ' → ')


def test_brief_empty_when_no_inputs():
    assert build_brief("", []) == ""                         # 전부 비면 미주입(브리프 생략)
    assert build_brief("", ["  ", ""]) == ""                  # 공백 사건만 → 미주입


def test_brief_length_cap():
    b = build_brief("시점X", ["사건 " * 30] * 40, cap=600)
    assert len(b) < 700 and b.endswith("…")                  # 본문 cap+말줄임(예산 경합 차단)


def test_brief_does_not_duplicate_canon():
    # 적대검증 핵심: 브리프는 등장 고정값(캐논)을 복제하지 않는다 — 시점·사건만
    b = build_brief("이야기 시작 후 2일 경과", ["사건A"])
    assert "등급=" not in b and "소속=" not in b and "고정값" not in b


def test_brief_at_top_and_no_time_dup():
    board = ContextBoard(chapter=3,
                         ground_truth=[OntologyFact(entity="강레오", attr_label="등급", value="S")],
                         story_time="이야기 시작 후 2.0일 경과")
    out = PromptAssembler(StyleSpec(rules=["x"]), 4000).assemble(
        board, SceneSpec(index=0, goal="목표", key_events=["사건A"]), "")
    # 브리프가 '확정 설정'보다 앞(최상단 재표면화)
    assert out.index("집필 기준") < out.index("[확정 설정") < out.index("[작가 지시")
    # 캐논은 확정 설정에만(브리프엔 없음), story_time 은 더 이상 별도 '현재 이야기 시점'으로 중복 안 됨
    assert "강레오: 등급=S" in out and "강레오(등급" not in out
    assert "현재 이야기 시점" not in out and "2.0일" in out
