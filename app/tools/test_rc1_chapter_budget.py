# -*- coding: utf-8 -*-
"""RC-1 회차 플롯 예산 분배 층 회귀 — 분배 파싱·슬롯 스코핑·역할 파생·줄 수 스케일·플래그 OFF 바이트 동일.
LLM 0(provider 스텁·결정론). 커버:
  ⓐ distribute_slots: LLM 산출 반영 + 절정 마지막 슬롯 수렴 + 결정론 폴백(never empty)·서사기능 태그.
  slot 소생: slot_central / planned_event_for 가 슬롯 분배를 우선(미분배 → [N화차] 접두 폴백 바이트 동일).
  ⓑ assemble_materials: [사건 재료]를 이 슬롯 몫 + 다음 슬롯 예고로 스코핑(미분배 → 통짜 메뉴 바이트 동일).
  ⓒ assign_role: 슬롯 서사기능 태그에서 역할 파생(작가 오버라이드 우선·미분배 → 로테이션 바이트 동일).
  ⓓ 줄 수 스케일: slot_line_budget 단조·4건=기본(12~16)·build_out_contract 스케일·build_base_sys OFF 바이트 동일.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from novelcopilot.domain.world import WorldConfig, EntitySpec, GenreContract
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec, SlotPlan
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.llm.base import LLMProvider
from novelcopilot.worldgen.arc_planner import ArcPlanner, SLOT_FUNCTIONS, _fallback_slot_function
from novelcopilot.engine.chapter_gate import slot_central, planned_event_for, slot_event
from novelcopilot.engine import story_pass as SP
from novelcopilot.engine import story_pass_prompts as P

_VOCAB = "생활어. 주인공의 도구 어휘(손거울·결박줄·망토·방울·구슬·봉인지)는 실물 여섯 점이다."


class _FailProvider(LLMProvider):
    def chat(self, *a, **k): return ""
    def chat_json(self, *a, **k): raise RuntimeError("LLM 실패(폴백 강제)")
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


class _RowsProvider(LLMProvider):
    """distribute_slots 산출을 고정 반환하는 스텁."""
    def __init__(self, rows):
        super().__init__()
        self.rows = rows
    def chat(self, *a, **k): return ""
    def chat_json(self, *a, **k): return {"slots": self.rows}
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _episode(target=3, menu=None, required=None, climax="절정 사건"):
    return Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="EP", premise="도입",
                   climax=climax, target_chapters=target,
                   required_events=list(required or []), event_menu=list(menu or []))


# ── ⓐ distribute_slots ────────────────────────────────────────────────────────
def test_distribute_slots_fallback_never_empty_and_climax_last():
    planner = ArcPlanner(_FailProvider())   # LLM 실패 → 결정론 폴백
    ep = _episode(target=3, menu=["m1", "m2", "m3"], required=["필수1", "필수2"], climax="절정")
    world = WorldConfig(title="t")
    slots = planner.distribute_slots(world, Arc(arc_id="arc1", order=1), ep, ep.event_menu)
    assert len(slots) == 3 and all(isinstance(s, SlotPlan) for s in slots)
    assert all((s.central or "").strip() for s in slots)          # never empty(중심 전부 채워짐)
    # 절정은 마지막 슬롯에 수렴(중심 또는 보조)
    last = slots[-1]
    assert "절정" in ([last.central] + last.support)
    # 서사기능 태그는 긍정 화이트리스트에서만
    assert all(s.function in SLOT_FUNCTIONS for s in slots)
    assert slots[0].function == "setup" and slots[-1].function == "payoff"   # 위치 기반 폴백
    # 필수 사건은 어느 슬롯엔가 실현(라운드로빈 채움)
    flat = [x for s in slots for x in ([s.central] + s.support)]
    assert "필수1" in flat and "필수2" in flat


def test_distribute_slots_honors_llm_rows_and_corrects_climax():
    rows = [{"slot": 1, "function": "setup", "central": "여는 사건", "support": ["보조a"]},
            {"slot": 2, "function": "escalation", "central": "조이는 사건", "support": []}]
    # LLM 이 절정을 어디에도 안 실었다 → 코드가 마지막 슬롯에 수렴시켜야 한다
    planner = ArcPlanner(_RowsProvider(rows))
    ep = _episode(target=2, menu=["m1"], required=[], climax="절정 폭발")
    slots = planner.distribute_slots(WorldConfig(title="t"), Arc(arc_id="arc1", order=1), ep, ep.event_menu)
    assert len(slots) == 2
    assert slots[0].central == "여는 사건" and slots[0].function == "setup"
    last = slots[-1]
    assert "절정 폭발" in ([last.central] + last.support)   # 코드 보정으로 마지막 슬롯 수렴


class _CaptureProvider(LLMProvider):
    """분배 콜 프롬프트를 포획(폴백 강제)."""
    def __init__(self):
        super().__init__()
        self.sys = None
        self.usr = None
    def chat(self, *a, **k): return ""
    def chat_json(self, messages, **k):
        self.sys, self.usr = messages[0]["content"], messages[1]["content"]
        return {}
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


# [치명 1a·2] climax pool 제외 + 원문 그대로 계약 ─────────────────────────────
def test_distribute_prompt_excludes_climax_from_pool_and_states_verbatim_contract():
    prov = _CaptureProvider()
    planner = ArcPlanner(prov)
    # generate_event_menu 는 seed 에 climax 를 붙여 pool 에 원문 상존시킨다 — 그 상황 재현.
    ep = _episode(target=3, menu=["m1", "절정사건", "m2"], required=["필수1"], climax="절정사건")
    planner.distribute_slots(WorldConfig(title="t"), Arc(arc_id="arc1", order=1), ep, ep.event_menu)
    assert "[에피소드 절정]절정사건" in prov.usr          # climax 는 배치 라벨에만
    pool_line = next(l for l in prov.usr.splitlines() if l.startswith("[사건 풀]"))
    assert "절정사건" not in pool_line                    # pool 렌더에서 정확 일치 제외
    assert "m1" in pool_line and "m2" in pool_line
    assert "원문 그대로 옮긴 것이다" in prov.sys           # 발명 차단 계약


# [치명 1b] 백스톱 위치 강제 — 비마지막 슬롯 climax → 마지막 이동 ───────────────
def test_backstop_moves_nonlast_climax_to_last_slot():
    rows = [{"slot": 1, "function": "escalation", "central": "절정폭발", "support": ["b1"]},
            {"slot": 2, "function": "setup", "central": "여는사건", "support": ["절정폭발"]},
            {"slot": 3, "function": "relation", "central": "중간사건", "support": []}]
    planner = ArcPlanner(_RowsProvider(rows))
    ep = _episode(target=3, menu=["m1", "m2", "m3"], required=[], climax="절정폭발")
    slots = planner.distribute_slots(WorldConfig(title="t"), Arc(arc_id="arc1", order=1), ep, ep.event_menu)
    for s in slots[:-1]:                                   # 비마지막 슬롯에 climax 잔존 0
        assert s.central != "절정폭발" and "절정폭발" not in s.support
    last = slots[-1]
    assert "절정폭발" in ([last.central] + last.support)   # 마지막 슬롯에만
    assert all((s.central or "").strip() for s in slots)  # 비운 central 은 fill 이 재채움(never empty)


# [경미 4] target≤1 콜 단락 — LLM 콜 없이 결정론 단일 슬롯 ─────────────────────
class _CountProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0
    def chat(self, *a, **k): return ""
    def chat_json(self, *a, **k):
        self.calls += 1
        return {}
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def test_distribute_target_one_skips_llm_single_slot():
    prov = _CountProvider()
    planner = ArcPlanner(prov)
    ep = _episode(target=1, menu=["m1", "m2"], required=["필수1"], climax="절정")
    slots = planner.distribute_slots(WorldConfig(title="t"), Arc(arc_id="arc1", order=1), ep, ep.event_menu)
    assert prov.calls == 0                                 # LLM 콜 0(단락)
    assert len(slots) == 1 and slots[0].slot == 1 and slots[0].function == "payoff"
    assert slots[0].central == "절정"                      # 단일 슬롯 central=climax
    flat = [slots[0].central] + slots[0].support
    assert "필수1" in flat and "m1" in flat and "m2" in flat   # pool 전량 배분
    assert "절정" not in slots[0].support                  # climax 중복 0
    # 대조: target>1 은 분배 콜 1회
    ep2 = _episode(target=3, menu=["m1", "m2"], required=["필수1"], climax="절정")
    ArcPlanner(prov).distribute_slots(WorldConfig(title="t"), Arc(arc_id="arc1", order=1), ep2, ep2.event_menu)
    assert prov.calls == 1


def test_fallback_slot_function_positional():
    assert _fallback_slot_function(0, 4) == "setup"
    assert _fallback_slot_function(3, 4) == "payoff"
    assert _fallback_slot_function(1, 4) == "escalation"
    assert _fallback_slot_function(0, 1) == "payoff"


# ── slot_central / planned_event_for 소생 ─────────────────────────────────────
def test_slot_central_and_planned_event_prefer_slots():
    ep = _episode(target=2, required=["[1화차] 접두사건", "[2화차] 접두둘째"])
    ep.slots = [SlotPlan(slot=1, function="setup", central="슬롯 중심1", support=["보조"]),
                SlotPlan(slot=2, function="payoff", central="슬롯 중심2")]
    assert slot_central(ep, 1) == "슬롯 중심1"
    assert slot_central(ep, 2) == "슬롯 중심2"
    assert slot_central(ep, 3) == ""

    class _NS:
        def __init__(self, **kw): self.__dict__.update(kw)
    world = _NS(spine=_NS(arcs=[_NS(episodes=[ep])]))
    chs = [_NS(chapter=10, episode_id="arc1_ep1"), _NS(chapter=11, episode_id="arc1_ep1")]
    st = _NS(world=world, chapters=chs)
    assert planned_event_for(st, 10) == "슬롯 중심1"    # 슬롯 분배 우선
    assert planned_event_for(st, 11) == "슬롯 중심2"


def test_planned_event_falls_back_to_prefix_when_no_slots():
    # 미분배(빈 slots)면 [N화차] 접두 폴백 — 바이트 동일(기존 EB-3 계약)
    ep = _episode(target=2, required=["[1화차] 접두사건", "[2화차] 접두둘째"])

    class _NS:
        def __init__(self, **kw): self.__dict__.update(kw)
    world = _NS(spine=_NS(arcs=[_NS(episodes=[ep])]))
    st = _NS(world=world, chapters=[_NS(chapter=10, episode_id="arc1_ep1")])
    assert slot_central(ep, 1) == ""                    # 슬롯 미분배
    assert planned_event_for(st, 10) == "접두사건"      # [1화차] 폴백


# ── assemble_materials (state 구성) ───────────────────────────────────────────
def _state_with_episode(ep, cie=0) -> ProjectState:
    w = WorldConfig(title="t", genre="x", synopsis="평범한 하루가 흔들린다.",
                    entities=[EntitySpec(id="hero", name="주인공")],
                    genre_contract=GenreContract(vocabulary_tone=_VOCAB))
    w.spine = NarrativeSpine(ending=EndingSpec(ending="E"),
                             arcs=[Arc(arc_id="arc1", order=1, title="A1", episodes=[ep])])
    st = ProjectState(id="rc1", seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    st.chapters = [ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="1화",
                                 summary="s1", detail_synopsis="1화 요지", arc_id="arc1",
                                 episode_id="arc1_ep1")]
    st.current_chapter = 1
    st.narrative_progress.current_arc_id = "arc1"
    st.narrative_progress.current_episode_id = "arc1_ep1"
    st.narrative_progress.chapters_in_episode = cie
    return st


def test_assemble_materials_scopes_to_slot_and_previews_next():
    ep = _episode(target=3, menu=["전체a", "전체b", "전체c", "전체d"])
    ep.slots = [SlotPlan(slot=1, function="setup", central="중심1", support=["보조1"]),
                SlotPlan(slot=2, function="escalation", central="중심2", support=["보조2"]),
                SlotPlan(slot=3, function="payoff", central="절정")]
    st = _state_with_episode(ep, cie=0)   # slot 1
    mat = SP.assemble_materials(st, chapter_no=2)
    assert mat["slot"] == 1
    assert mat["menu"] == ["중심1", "보조1"]              # 이 슬롯 몫만(통짜 아님)
    assert mat["planned_event"] == "중심1"               # 중심 사건
    assert mat["slot_function"] == "setup"
    assert mat["line_budget"] == SP.slot_line_budget(2)  # 사건 2건 스케일
    body = mat["mat"]
    assert "- 중심1" in body and "- 보조1" in body
    assert "전체c" not in body and "전체d" not in body   # 다른 슬롯 재료 미노출
    assert "[다음 화 예고" in body and "- 중심2" in body  # 다음 슬롯 예고(참고)


def test_assemble_materials_off_path_byte_identical():
    # 미분배(빈 slots) — 통짜 메뉴 경로. mat 은 스코핑 이전과 동일해야 한다.
    ep = _episode(target=3, menu=["전체a", "전체b"], required=["[1화차] 접두"])
    st = _state_with_episode(ep, cie=0)
    mat = SP.assemble_materials(st, chapter_no=2)
    assert mat["menu"] == ["전체a", "전체b"]              # 통짜 메뉴
    assert mat["slot_function"] == "" and mat["line_budget"] is None
    assert "[다음 화 예고" not in mat["mat"]
    assert mat["planned_event"] == "접두"                # [1화차] 폴백(슬롯 없음)
    # 종전 렌더와 바이트 동일 확인
    expect = ("[작품 전제]\n평범한 하루가 흔들린다.\n\n"
              "[지난 이야기(직전 2화 상세)]\n1화 요지\n\n"
              "[캐논 메모]\n주인공의 도구 여섯 점: 손거울·결박줄·망토·방울·구슬·봉인지.\n\n"
              "[사건 재료(취사선택)]\n- 전체a\n- 전체b")
    assert mat["mat"] == expect


# ── ⓒ assign_role 슬롯 파생 ───────────────────────────────────────────────────
def test_assign_role_derives_from_slot_function():
    base = {"digest": "abc123def456", "menu": ["m"], "planned_event": ""}
    for fn, expect in (("setup", "조사"), ("escalation", "위기"), ("payoff", "회수"),
                       ("relation", "관계"), ("respite", "휴지")):

        class _NS:
            def __init__(self, **kw): self.__dict__.update(kw)
        st = _NS(story_passes=[], chapters=[])
        assert SP.assign_role(st, {**base, "slot_function": fn}) == expect
    # 작가 오버라이드는 슬롯 파생보다 우선

    class _NS2:
        def __init__(self, **kw): self.__dict__.update(kw)
    st = _NS2(story_passes=[], chapters=[])
    assert SP.assign_role(st, {**base, "slot_function": "payoff"}, "휴지") == "휴지"


def test_assign_role_no_slot_function_uses_rotation():
    # 미분배(slot_function 부재) → 종전 로테이션 경로 바이트 동일(결정론)

    class _NS:
        def __init__(self, **kw): self.__dict__.update(kw)
    st = _NS(story_passes=[], chapters=[])
    mats = {"digest": "abc123def456", "menu": ["m"], "planned_event": ""}
    r = SP.assign_role(st, mats)
    assert r in P.ROLES and SP.assign_role(st, mats) == r


# ── ⓓ 줄 수 스케일 ────────────────────────────────────────────────────────────
def test_slot_line_budget_monotonic_and_default_at_four():
    assert SP.slot_line_budget(4) == {"lo": 12, "hi": 16}   # 4건=종전 기본(바이트 동일 앵커)
    b1, b2, b6 = SP.slot_line_budget(1), SP.slot_line_budget(2), SP.slot_line_budget(6)
    assert b1["lo"] < 12 and b1["hi"] < 16                  # 가벼운 슬롯 압축 해소
    assert b1["lo"] <= b2["lo"] <= 12 and b6["hi"] >= 16    # 단조·무거운 슬롯 상한 상승
    assert all(b["hi"] - b["lo"] >= 4 for b in (b1, b2, b6))


def test_out_contract_scales_and_default_byte_identical():
    assert P.OUT_CONTRACT == P.build_out_contract()                       # 기본 상수 바이트 동일
    assert P.build_out_contract() == "분량: 줄 12~16개.\n" + P.FORMAT
    scaled = P.build_out_contract(8, 12)
    assert "분량: 줄 8~12개." in scaled and P.FORMAT_CORE in scaled
    # build_base_sys/rev_sys 미전달 = 종전과 동일, out_contract 전달 시 스케일된 계약 실림
    assert P.build_base_sys() == P.build_base_sys("", "", out_contract=None)
    assert "줄 8~12개" in P.build_base_sys("", "역할선", out_contract=scaled)
    assert "줄 8~12개" in P.build_rev_sys("역할선", "", out_contract=scaled)
    assert "줄 12~16개" in P.build_base_sys()                             # 미전달=기본


# ── 통합: copilot 소비 지점 동작(구현됨의 기준=소비 지점 동작) ──────────────────
def test_copilot_wires_distribution_when_flag_on():
    """chapter_budget ON + 에피소드 활성 시 generate_next_chapter 가 distribute_slots 를 태워
    ep.slots 를 채우는지 — 배선이 실제 소비 지점에서 산다(정의만 아님)."""
    import tempfile
    from pathlib import Path
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    import novelcopilot.services.copilot as C

    class _Combo(LLMProvider):
        """generate_event_menu(event_menu) + distribute_slots(slots) 를 한 dict 로 만족."""
        def chat(self, *a, **k): return ""
        def chat_json(self, *a, **k):
            return {"event_menu": ["m1", "m2", "m3"],
                    "slots": [{"slot": 1, "function": "setup", "central": "중심1", "support": ["s1"]},
                              {"slot": 2, "function": "escalation", "central": "중심2", "support": []},
                              {"slot": 3, "function": "payoff", "central": "절정", "support": []}]}
        def embed(self, texts): return [[0.0] * 4 for _ in texts]

    tmp = Path(tempfile.mkdtemp(prefix="rc1int_"))
    s = get_settings().model_copy(update={"data_dir": str(tmp), "chapter_budget": True,
                                          "story_pass_auto": False})
    svc = CopilotService(s, FilesystemProjectRepository(tmp))
    svc._planning_provider = _Combo()
    w = WorldConfig(title="t", genre="x", synopsis="평범한 하루가 흔들린다.",
                    entities=[EntitySpec(id="hero", name="주인공")],
                    genre_contract=GenreContract(vocabulary_tone=_VOCAB))
    # ep1(target=2·done) → ep2(target=3·event_menu 빈=활성 트리거). 경계=ep1 finale 직후.
    w.spine = NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1",
                    target_chapters=2, done=True, event_menu=["사건1"]),
            Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, climax="절정",
                    target_chapters=3)])])   # event_menu 미설정 → 활성
    st = ProjectState(id="rc1int", seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    st.chapters = [ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="1화", summary="s1",
                                 detail_synopsis="1화 요지", arc_id="arc1", episode_id="arc1_ep1"),
                   ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="2화", summary="s2",
                                 detail_synopsis="2화 요지", arc_id="arc1", episode_id="arc1_ep1")]
    st.current_chapter = 2
    st.narrative_progress.current_arc_id = "arc1"
    st.narrative_progress.current_episode_id = "arc1_ep1"
    st.narrative_progress.chapters_in_episode = 2
    svc.repo.save(st)
    sess, _ = svc.get_session("rc1int")
    sess.provider = _Combo()
    sess.bundle.updater.propose = lambda *a, **k: {}
    sess.bundle.updater.apply = lambda *a, **k: ([], [], [], [])
    sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
        ChapterRecord(chapter=ch_no, status=ChapterStatus.FINALIZED, text="본문", summary="s")
    orig = C.create_role_provider
    C.create_role_provider = lambda cfg, m: sess.provider
    try:
        svc.generate_next_chapter("rc1int")
    finally:
        C.create_role_provider = orig
    st2 = svc.get_project("rc1int")
    ep2 = next(e for a in st2.world.spine.arcs for e in a.episodes if e.episode_id == "arc1_ep2")
    assert len(ep2.slots) == 3                       # 배선이 소비 지점에서 실행됨
    assert ep2.slots[0].central == "중심1" and ep2.slots[0].function == "setup"
    assert "절정" in ([ep2.slots[-1].central] + ep2.slots[-1].support)
    assert [s.slot for s in ep2.slots] == [1, 2, 3]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
