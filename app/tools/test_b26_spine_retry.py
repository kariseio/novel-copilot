# -*- coding: utf-8 -*-
"""B-26 spine 빈-폴백 retry — transient 실패/빈 결과에 1회 재시도, 재실패 시 폴백 유지 + 가시 경고. LLM 0콜.

증상(실측): 작품 생성 시 spine LLM 콜 1회 transient 실패 → 빈 NarrativeSpine 폴백 → 아크 0개
flat 모드로 '조용히' 진행(5장르 동시생성서 1건). 수정 계약:
  ① 실패(예외)·빈 결과(아크 0)에 동일 콜 1회 retry — 무한 재시도 금지(정확히 +1콜)
  ② 재실패 시 폴백(빈 spine=평면 모드)은 '유지' — 기존 EventBus emit 관행으로 경고만 가시화
     (spine_retry / spine_gen_failed / copilot 의 spine_skip[reason]) — 무강제·advisory
  ③ create_project 가 아크 0 을 spine_done('완성 0개')으로 위장 보고하지 않음
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.engine.observability import EventBus
from novelcopilot.llm.base import LLMProvider
from novelcopilot.worldgen import ArcPlanner


# _spine_gaps 기준 완전한 설계(교정 콜 미발동): 엔딩·중심질문·아크 goal·첫 아크 에피소드 climax 모두 채움
GOOD = {
    "ending": {"central_question": "Q", "ending": "E", "thematic_payoff": "T"},
    "arcs": [
        {"title": "A1", "goal": "g1", "central_conflict": "c", "turning_point": "t",
         "episodes": [{"title": "E1", "premise": "p", "climax": "cl", "required_events": [],
                       "required_cast": [], "plants": [], "payoffs": [], "target_chapters": 4}],
         "new_cast": []},
        {"title": "A2", "goal": "g2", "central_conflict": "", "turning_point": "",
         "episodes": [], "new_cast": []},
    ],
}

# worldgen 용 최소 유효 world(WorldConfig.title 필수) — create_project e2e 를 LLM 0콜로 통과시키는 스텁
WORLD_RAW = {"title": "티", "genre": "판타지", "premise": "p",
             "entities": [{"id": "hero", "name": "주인공"}]}


class SeqProvider(LLMProvider):
    """chat_json 을 캔드 시퀀스로 대체 — 항목이 Exception 이면 raise, dict 면 deep copy 반환.
    시퀀스 소진 후엔 마지막 항목 반복(기존 ScriptFake 관행). LLM 0콜."""
    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.calls = 0

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, *a, **k):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return json.loads(json.dumps(item, ensure_ascii=False))   # 호출 간 변이 오염 방지


def _world():
    return WorldConfig(title="t", genre="x", premise="p",
                       entities=[EntitySpec(id="hero", name="주인공")])


def _events(bus):
    return [e["event"] for e in bus.buffer]


# ---------- ① build_spine: retry 성공 경로 ----------
def test_transient_failure_then_retry_success():
    # 1콜 transient 실패 → retry 성공: 빈 폴백이 아니라 정상 spine + spine_retry 가시화
    bus = EventBus()
    p = SeqProvider([RuntimeError("transient"), GOOD])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 2
    assert len(spine.arcs) == 2 and spine.ending and spine.ending.ending == "E"
    evs = _events(bus)
    assert evs.count("spine_retry") == 1
    assert "spine_gen_failed" not in evs and "spine_incomplete" not in evs


def test_empty_result_counts_as_failure_and_retries():
    # 예외가 아니라 '빈 결과(dict 이나 아크 0)'로 오는 transient 도 동일하게 retry
    bus = EventBus()
    p = SeqProvider([{}, GOOD])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 2 and len(spine.arcs) == 2
    assert "spine_retry" in _events(bus)


# ---------- ② build_spine: 재실패 → 폴백 유지 + 경고 ----------
def test_refail_keeps_empty_fallback_and_warns():
    bus = EventBus()
    p = SeqProvider([RuntimeError("boom1"), RuntimeError("boom2")])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 2                                            # 본 콜 + retry 1회뿐(무한 재시도 금지)
    assert isinstance(spine, NarrativeSpine) and spine.arcs == []  # 기존 빈 폴백 계약 불변(평면 모드)
    evs = _events(bus)
    assert evs.count("spine_retry") == 1 and "spine_gen_failed" in evs


def test_first_success_no_extra_call():
    # 정상 1콜 성공이면 retry 0(비용 가드) — 기존 경로 바이트 동일 동작
    bus = EventBus()
    p = SeqProvider([GOOD])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 1 and len(spine.arcs) == 2
    assert "spine_retry" not in _events(bus)


def test_partial_result_preserved_for_g8_fix():
    # 1콜=부분 결과(엔딩만·아크 0) → retry 예외 → 부분 결과를 버리지 않고 기존 G8 교정이 아크를 채움
    bus = EventBus()
    partial = {"ending": {"central_question": "Q", "ending": "E", "thematic_payoff": "T"}, "arcs": []}
    p = SeqProvider([partial, RuntimeError("boom"), GOOD])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 3                                            # 본 콜 + retry + G8 교정
    assert len(spine.arcs) == 2 and spine.ending.ending == "E"
    evs = _events(bus)
    assert "spine_retry" in evs and "spine_gen_failed" not in evs


def test_retry_junk_does_not_clobber_partial():
    # 적대검증 LOW 반영: attempt1=부분(엔딩 완비) → retry 가 '더 빈약한 무아크 잔해'를 반환해도
    # 덜 빈(gaps 적은) 쪽을 유지 — G8 재료(엔딩) 유실 금지. G8 교정까지 실패하는 최악 경로로 판별.
    bus = EventBus()
    partial = {"ending": {"central_question": "Q", "ending": "E", "thematic_payoff": "T"}, "arcs": []}
    junk = {"note": "잔해"}                                        # non-empty 지만 엔딩·질문·아크 전부 결손
    p = SeqProvider([partial, junk, RuntimeError("g8 down")])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 3                                            # 본 콜 + retry + G8 교정 시도
    assert spine.ending.ending == "E"                              # 잔해가 부분 결과를 덮었다면 "" 가 됐을 것
    assert spine.arcs == []                                        # 아크는 그대로 없음(빈 폴백 계약 불변)
    assert "spine_incomplete" in _events(bus)                      # 잔존 누락 가시화(기존 G8 경로)


def test_readiness_persistently_flags_empty_spine():
    # 적대검증 MED 반영: 생성 오버레이 경고(spine_skip)는 단일 라인이라 다음 이벤트에 곧 덮임 —
    # 작업실 T6 준비도 advisory 가 'spine 부재/아크 0(평면 모드)'을 영속 flag 로 가시화(무강제·비차단).
    from novelcopilot.domain.bible import StoryBible
    from novelcopilot.domain.narrative import Arc
    from novelcopilot.domain.project import ProjectState
    from novelcopilot.engine.readiness import chapter_readiness

    def _keys(spine):
        w = _world()
        w.spine = spine
        rep = chapter_readiness(ProjectState(id="p", seed=ProjectSeed(premise="p"), world=w,
                                             bible=StoryBible(), current_chapter=0))
        assert all(f["severity"] == "warn" for f in rep["flags"])  # advisory 계약(차단 severity 없음)
        return {f["key"] for f in rep["flags"]}

    assert "spine_missing" in _keys(NarrativeSpine())              # 재실패 빈 폴백(아크 0)
    assert "spine_missing" in _keys(None)                          # 예외 폴백(spine=None)
    assert "spine_missing" not in _keys(                           # 정상 spine 이면 미발화
        NarrativeSpine(arcs=[Arc(arc_id="a1", order=1)]))


def test_truncated_first_call_retries_uncapped():
    # 무상한 전환(2026-08-07 "max token 전부 제거"): spine 콜은 캡을 지정하지 않는다(절단은 프로바이더
    # 하드캡에서만 가능). 절단 관측 재시도의 '캡 상향' 경로는 제거 — retry 자체와 가시화(reason)만 남는다.
    bus = EventBus()

    class TruncProvider(SeqProvider):
        def __init__(self, script):
            super().__init__(script)
            self.max_tokens_seen = []

        def chat_json(self, *a, **k):
            self.max_tokens_seen.append(k.get("max_tokens"))
            item = self.script[min(self.calls, len(self.script) - 1)]
            self.calls += 1
            self.last_truncated = (self.calls == 1)   # 첫 콜만 절단 관측 모사
            if isinstance(item, Exception):
                raise item
            return json.loads(json.dumps(item, ensure_ascii=False))

    p = TruncProvider([{}, GOOD])                      # 첫 콜: 절단으로 파싱 잔해(빈 dict) 모사
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 2 and len(spine.arcs) == 2
    assert p.max_tokens_seen == [None, None]           # 캡 미지정(무상한) — 상향 경로 자체가 없음
    ev = next(e for e in bus.buffer if e["event"] == "spine_retry")
    assert ev.get("reason") == "truncated"


def test_non_truncated_retry_uncapped():
    # 절단이 아닌 실패(예외·빈 결과)의 재시도 — 무상한 유지·reason 은 transient 로 가시화
    bus = EventBus()

    class CapSpy(SeqProvider):
        def __init__(self, script):
            super().__init__(script)
            self.max_tokens_seen = []

        def chat_json(self, *a, **k):
            self.max_tokens_seen.append(k.get("max_tokens"))
            return super().chat_json(*a, **k)

    p = CapSpy([RuntimeError("transient"), GOOD])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    assert p.calls == 2 and len(spine.arcs) == 2
    assert p.max_tokens_seen == [None, None]           # 캡 미지정(무상한)
    ev = next(e for e in bus.buffer if e["event"] == "spine_retry")
    assert ev.get("reason") != "truncated"


def test_dict_payoffs_coerced_not_fatal():
    # ST-12a 검증 런 적발 2건째(2026-07-14): DP-3' 프롬프트(payoff_at 요구)에 모델이 payoffs 를
    # [{'what':…,'payoff_at':…}] 객체 배열로 반환 → pydantic string_type 예외 → spine 전체 유실.
    # 수리 계약: 신뢰 경계 관용 변환 — 텍스트만 회수(발명 0), 중첩 payoff_at 은 에피소드 라벨로 채택.
    bus = EventBus()
    good = json.loads(json.dumps(GOOD, ensure_ascii=False))
    good["arcs"][0]["episodes"][0]["payoffs"] = [
        {"what": "도현이 첫 응징을 완수한다", "payoff_at": "mid"},
        "명시 문자열 payoff",
        {"junk": 1},                                   # 텍스트 없는 잔해 — 버림(발명 0)
    ]
    good["arcs"][0]["episodes"][0]["plants"] = [{"text": "복선 A"}]
    good["arcs"][0]["episodes"][0].pop("payoff_at", None)   # 최상위 부재 → 중첩에서 채택
    p = SeqProvider([good])
    spine = ArcPlanner(p).build_spine(_world(), 12, bus=bus)
    ep = spine.arcs[0].episodes[0]
    assert ep.payoffs == ["도현이 첫 응징을 완수한다", "명시 문자열 payoff"]
    assert ep.plants == ["복선 A"]
    assert ep.payoff_at == "mid"                        # 중첩 payoff_at 채택
    assert "spine_gen_failed" not in _events(bus)


# ---------- ③ create_project: 경고 가시화(위장 보고 금지) ----------
def _svc(planning_script):
    from novelcopilot.services import CopilotService
    from novelcopilot.repository import FilesystemProjectRepository
    svc = CopilotService(get_settings(), FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    svc._wg_provider = SeqProvider([WORLD_RAW])          # worldgen·bible 스텁(마지막 항목 반복)
    svc._planning_provider = SeqProvider(planning_script)
    return svc


def test_create_project_warns_visibly_on_empty_spine():
    # 재실패 시 create_project 가 'spine_done 단락 0개'로 위장하지 않고 spine_skip(reason) 경고를 emit
    bus = EventBus()
    svc = _svc([RuntimeError("boom1"), RuntimeError("boom2")])
    state, _ = svc.create_project(ProjectSeed(premise="p", target_chapters=12), bus=bus)
    assert state.world.spine is not None and state.world.spine.arcs == []   # 폴백 유지(평면 모드 계약 불변)
    evs = _events(bus)
    assert "spine_retry" in evs and "spine_gen_failed" in evs
    assert "spine_done" not in evs                                          # 아크 0 을 '완성'으로 보고 금지
    skip = next(e for e in bus.buffer if e["event"] == "spine_skip")
    assert skip.get("reason") == "empty_after_retry"


def test_create_project_emits_done_when_spine_ok():
    # 정상 spine 이면 기존과 동일하게 spine_done(arcs·ending_ok) — 경고 이벤트 없음
    bus = EventBus()
    svc = _svc([GOOD])
    state, _ = svc.create_project(ProjectSeed(premise="p", target_chapters=12), bus=bus)
    assert state.world.spine and len(state.world.spine.arcs) == 2
    done = next(e for e in bus.buffer if e["event"] == "spine_done")
    assert done["arcs"] == 2 and done["ending_ok"] is True
    evs = _events(bus)
    assert "spine_skip" not in evs and "spine_gen_failed" not in evs
