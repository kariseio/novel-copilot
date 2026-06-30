# -*- coding: utf-8 -*-
"""CN-1 스토리 시계(WorldClock) 테스트 — 결정론 누적·null degrade·플래너 라벨링·고신뢰 주입. LLM 0콜."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

from novelcopilot.domain.types import (TimeDelta, ChapterRecord, ChapterStatus,
                                        ContextBoard, OntologyFact)
from novelcopilot.domain.world import Beat, StyleSpec
from novelcopilot.engine.story_clock import (delta_minutes, elapsed_minutes,
                                             format_elapsed, story_time_for)
from novelcopilot.engine.prompts import PromptAssembler
from novelcopilot.domain.types import SceneSpec


def test_parse_robust():
    assert TimeDelta.parse({"amount": 3, "unit": "day", "mode": "advance"}).unit == "day"
    # LLM 오타('days')는 델타를 탈락시키지 않고 보관 — 의미 판정(미상)은 story_clock 가
    td = TimeDelta.parse({"amount": 2, "unit": "days"})
    assert td is not None and delta_minutes(td) is None        # 미상 단위 → 누적서 건너뜀
    assert TimeDelta.parse("nope") is None and TimeDelta.parse(None) is None


def test_delta_minutes_modes():
    assert delta_minutes(TimeDelta(amount=3, unit="day")) == 3 * 1440
    assert delta_minutes(TimeDelta(amount=0, unit="minute")) == 0.0          # 명시적 '없음' = 알려진 0
    assert delta_minutes(TimeDelta(amount=5, unit="day", mode="flashback")) == 0.0   # 회상=전진0
    assert delta_minutes(TimeDelta(amount=5, unit="day", mode="parallel")) == 0.0    # 동시=전진0
    assert delta_minutes(TimeDelta(amount=2, unit="century")) is None        # 미상 단위
    assert delta_minutes(None) is None


def test_accumulate_excludes_flashback():
    deltas = [TimeDelta(amount=0, unit="minute"), TimeDelta(amount=1, unit="day"),
              TimeDelta(amount=3, unit="day"), TimeDelta(amount=5, unit="day", mode="flashback"),
              TimeDelta(amount=2, unit="hour")]
    mins, unknown = elapsed_minutes(deltas)
    assert mins == 4 * 1440 + 2 * 60 and unknown is False     # 회상 5일 제외 → 4일 2시간
    assert "4.1일" in story_time_for(deltas)


def test_null_degrade():
    assert story_time_for([None, None]) == ""                 # 전부 미상 → 미주입(거짓 시작점 단정 안 함)
    # 일부 미상이면 '약' 헤지로 하향 정직
    s = story_time_for([TimeDelta(amount=1, unit="day"), TimeDelta.parse({"amount": 2, "unit": "fortnight"})])
    assert "약" in s and "1.0일" in s


def test_persist_roundtrip_and_old_record():
    beat = Beat(chapter=2, time_delta=TimeDelta(amount=3, unit="day"))
    rec = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, time_delta=beat.model_dump()["time_delta"])
    assert isinstance(rec.time_delta, TimeDelta) and rec.time_delta.amount == 3
    rec2 = ChapterRecord.model_validate_json(rec.model_dump_json())     # 디스크 영속 라운드트립
    assert rec2.time_delta.unit == "day"
    assert ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED).time_delta is None   # 구버전 하위호환


def test_high_trust_injection():
    board = ContextBoard(chapter=5, ground_truth=[OntologyFact(entity="레오", attr_label="등급", value="S")],
                         story_time="이야기 시작 후 4.1일 경과")
    out = PromptAssembler(StyleSpec(rules=["x"]), 4000).assemble(
        board, SceneSpec(index=0, goal="g", key_events=["e"]), "")
    high_trust = out.split("[작가 지시")[0]                  # 최상단 집필 기준 + 확정 설정 블록
    # CN-3: story_time 은 최상단 '집필 기준' 브리프에 통합됨(고살리언스 재표면화)
    assert "집필 기준" in high_trust and "4.1일" in high_trust
    assert "역행" in high_trust                              # 시간 역행 금지 지시
