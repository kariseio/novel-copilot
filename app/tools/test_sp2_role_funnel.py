# -*- coding: utf-8 -*-
"""SP-2 역할 깔때기 배선 회귀 — 감사 검사 제안 1·2·6 + 배정기 결정론. LLM 0.

검사 1: 같은 화의 role_line 이 설계·수정 콜에 동일 문자열로 실린다.
검사 2: 3안의 {event} 가 서로 다르고 각 후보의 사건이 메뉴에 실재한다.
검사 6: 역할별 5번째 심문 축이 audit_criteria 강등 루프와 pick_pillar 카운트 양쪽에 배선된다.
"""
import sys, os
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from novelcopilot.engine import story_pass as SP
from novelcopilot.engine import story_pass_prompts as P
from novelcopilot.engine.story_pass import StoryPassNotReady

MENU = ["낡은 방울이 계단참에서 다시 울린다", "관리국 서류가 이름 없이 반송된다",
        "옆집 문틈으로 젖은 발자국이 이어진다", "보류 잔금 봉투가 책상에 놓인다"]
MATS = {"digest": "abc123def456", "menu": list(MENU), "planned_event": ""}


def test_assign_events_distinct_from_menu_and_deterministic():
    ev = SP.assign_events(MATS)
    vals = list(ev.values())
    assert set(ev) == {"E1", "E2", "E3"}
    assert len(set(vals)) == 3 and all(v in MENU for v in vals)
    assert SP.assign_events(dict(MATS)) == ev                     # 같은 digest = 같은 배정
    ev2 = SP.assign_events({**MATS, "planned_event": "필수 사건이 계약대로 벌어진다"})
    assert ev2["E1"] == "필수 사건이 계약대로 벌어진다"           # 필수 사건은 후보 1의 중심(C2 단일 출처)


def test_role_line_same_bytes_in_design_and_revision_calls():
    role_line = P.ROLES["회수"].format(event=MENU[0])
    assert role_line in P.build_base_sys("", role_line)
    assert role_line in P.build_rev_sys(role_line)


def test_funnel_stages_fail_loud_without_role_lines():
    for fn in (lambda: SP.generate_candidates(None, MATS, None),
               lambda: SP.revise_independently(None, MATS, {}, {}, None)):
        try:
            fn()
            assert False, "StoryPassNotReady 미발생"
        except StoryPassNotReady:
            pass


def test_assign_role_override_rotation_and_recovery_pressure():
    state = SimpleNamespace(story_passes=[], chapters=[])
    assert SP.assign_role(state, MATS, "휴지") == "휴지"          # 작가 오버라이드 우선(무강제)
    try:
        SP.assign_role(state, MATS, "없는역할")
        assert False, "오타 역할이 조용히 무시됨"
    except StoryPassNotReady:
        pass
    r = SP.assign_role(state, MATS)
    assert r in P.ROLES and SP.assign_role(state, MATS) == r      # 결정론
    recent = [SimpleNamespace(chapter=8, checks={"role": r}),
              SimpleNamespace(chapter=9, checks={"role": r})]
    r2 = SP.assign_role(SimpleNamespace(story_passes=recent, chapters=[]), MATS)
    assert r2 != r                                                # 최근 2화 역할 제외 로테이션
    paid = [SimpleNamespace(chapter=i, chapter_function="payoff" if i % 2 else "escalation")
            for i in (5, 6, 7)]
    assert SP.assign_role(SimpleNamespace(story_passes=[], chapters=paid), MATS) == "회수"


class _FakeJudge:
    def __init__(self, resp):
        self.resp = resp

    def chat_json(self, msgs, temperature=0.0):
        return dict(self.resp)


def _deps(resp):
    return SimpleNamespace(judge=_FakeJudge(resp), emit=lambda *a, **k: None)


_STORY = ("- 낡은 방울이 손바닥으로 돌아오고, 그가 그것을 쥔다.\n"
          "- 그가 서류를 덮으며 다음 수를 고른다.")
_SYN_PREV = "지난 화에 그는 낡은 방울을 계단참 우편함에 맡겨 두었다."


def _audit_resp(source_quote):
    node = {"이행": True, "인용": "낡은 방울이 손바닥으로 돌아오고"}
    return {"중심사건": dict(node), "주인공선택": {"이행": True, "인용": "서류를 덮으며 다음 수를 고른다"},
            "아이러니": dict(node), "해소지연": dict(node),
            "회수": {"이행": True, "인용": "낡은 방울이 손바닥으로 돌아오고", "출처인용": source_quote}}


def test_audit_criteria_recovery_axis_cross_checks_syn_prev():
    finals = {"E1": {"story": _STORY, "fmt_errs": []}}
    ok = SP.audit_criteria(_deps(_audit_resp("낡은 방울을 계단참 우편함에 맡겨 두었다")),
                           finals, "1화 상세", role="회수", syn_prev=_SYN_PREV)
    assert ok["E1"]["회수"]["이행"] is True
    bad = SP.audit_criteria(_deps(_audit_resp("이 스토리에는 없는 출처 문장이다")),
                            finals, "1화 상세", role="회수", syn_prev=_SYN_PREV)
    assert bad["E1"]["회수"]["이행"] is False and "출처인용" in bad["E1"]["회수"]["강등"]


def test_pick_pillar_reads_role_axis_and_shuffles_ties():
    finals = {k: {"story": "", "fmt_errs": []} for k in ("E1", "E2", "E3")}
    four = {c: {"이행": True} for c in P.COMMON_AUDIT_AXES}
    audits = {"E1": dict(four), "E2": {**four, "회수": {"이행": True}}, "E3": dict(four)}
    assert SP.pick_pillar(finals, audits, role="회수", digest="d1") == "E2"   # 역할 축이 카운트에 산다
    tie = {k: dict(four) for k in finals}
    picks = {SP.pick_pillar(finals, tie, role="회수", digest=d) for d in ("d1", "d2", "d3", "d4", "d5")}
    assert len(picks) > 1                                          # 동률 타이브레이커가 사전순 고정이 아님
    assert SP.pick_pillar(finals, tie, role="회수", digest="d1") == SP.pick_pillar(
        finals, tie, role="회수", digest="d1")                     # 같은 digest = 같은 선정(결정론)
    assert SP.pick_pillar(finals, audits, pillar="E3", role="회수", digest="d1") == "E3"   # 작가 지정 우선
