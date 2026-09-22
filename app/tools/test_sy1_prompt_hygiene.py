# -*- coding: utf-8 -*-
"""SY-1 스토리 패스 문안 위생 회귀 (설계 §10 — 자가시험 레포 승격 2/3).

감사 통과 상태를 고정한다: em dash 0(EM-1) · 금지어 노출 0(RB-6) · 형식 계약이
5개 변환 콜 전부에 동일 바이트 재명시 · 규칙 5 동일 바이트 재인용 · 계약 상수 SSOT.
SP-2(2026-08-18): 고정 각도 3종(ANGLES) 폐지 → 화 역할 6종(ROLES) — 스윕·계약 검사를
역할 체제로 개정. LLM 0 · 결정론.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from novelcopilot.engine import story_pass_prompts as P   # type: ignore
except ImportError:
    sys.path.insert(0, os.path.dirname(__file__))
    import story_pass_prompts_v3 as P           # type: ignore

_R5 = "묘사·관찰·정보 전달은 완결된 한 문장으로 이어 써라."
_RULES = ["r1", "r2", "r3", "r4", _R5]
_EVENT = "옥상 문이 안에서 잠긴다"
_ROLE_LINES = [tpl.format(event=_EVENT) for tpl in P.ROLES.values()]


def _all_prompts():
    return [
        P.build_base_sys(), P.build_base_sys("cliffhanger"), P.build_base_sys("soft"),
        P.REVIEW_SYS, P.build_rev_sys(_ROLE_LINES[0], "cliffhanger"), P.AUDIT_SYS,
        P.PAIR_SYS, P.SCAN_SYS, P.MERGE_SYS, P.CHECK_SYS, P.JOINT_SYS,
        P.build_enforce(_RULES), P.build_sys_gen("페르소나", "문체블록", _RULES),
        P.build_repair_sys(4000), P.FIX_FMT, P.REF_LABEL,
        P.build_story_block_draft("- 줄"), P.build_story_block_continue(["- 줄"]),
        P.build_label_sys(["confront", "chase"]), P.build_label_user("기준", "- 줄"),
    ] + [P.build_base_sys("", rl) for rl in _ROLE_LINES] \
      + [P.build_audit_sys(r) for r in P.ROLES] + list(P.ROLES.values())


def test_no_em_dash_in_any_prompt():
    assert all("—" not in x for x in _all_prompts())


def test_no_banned_terms_in_any_prompt():
    assert all(b not in x for x in _all_prompts() for b in P.BAN)


def test_format_contract_in_all_transform_calls():
    # 변환 5콜(설계·수정·병합·접합·재변환) 전부에 동일 바이트 재명시 — 사양 핵심 조항
    for call in (P.build_base_sys(), P.build_rev_sys(_ROLE_LINES[1]), P.MERGE_SYS, P.JOINT_SYS, P.FIX_FMT):
        assert P.FORMAT_CORE in call


def test_rule5_byte_identical_requote():
    assert _R5 in P.build_enforce(_RULES)


def test_ending_line_symmetric_across_calls():
    # 3차 감사 M2: 같은 결말 정책이면 설계·수정 콜에 같은 줄이 실린다(사슬 계약 증발 차단)
    for h in ("cliffhanger", "soft"):
        assert P._ENDING_LINES[h] in P.build_base_sys(h)
        assert P._ENDING_LINES[h] in P.build_rev_sys(_ROLE_LINES[0], h)
    assert P.build_base_sys() == P.build_base_sys("none")   # 미지정=무추가


def test_contract_constants_ssot():
    # 3차 감사 m-g: 동어반복 단언을 리터럴 스냅샷으로 — 초안 경로 계약 바이트를 실제로 잠근다
    assert P.COVERAGE_CONTRACT == ("확정 스토리의 각 줄은 본문에서 실제로 벌어지는 사건으로 실현하고, "
                                   "줄의 순서가 곧 사건의 순서다. 목록의 첫 줄부터 순서대로 실현하며 나아간다.")
    d = P.build_story_block_draft("- 줄")
    c = P.build_story_block_continue(["- 줄"])
    assert P.STORY_LAYER_LABEL in d and P.STORY_LAYER_LABEL in c
    assert P.COVERAGE_REALIZE in d and P.COVERAGE_REALIZE in c
    assert P.COVERAGE_START in d and P.COVERAGE_START not in c   # 시작점 지정은 초안 전용
    assert P.STORY_LAYER_LABEL in P.build_user_gen("a", "b", "c", "- s")


def test_label_call_describes_only():
    lbl = P.build_label_sys(["confront"])
    # 프로즈 속성 필드(끊는 방식·닫는 장치)는 스토리 시점 도출 불가라 미산출(재감사 2차 M-C)
    assert "hook_type" not in lbl and "closing_device" not in lbl
    for field in ("title", "summary", "chapter_function", "scene_form",
                  "time_advance", "time_delta", "time_source", "world_reveal", "place"):
        assert field in lbl


def test_roles_are_genre_neutral():
    # SP-2: 역할 문형은 genre-blind 중립 + 돈-가치 축 어휘 0(값·정산 모티프 배제 정책)
    joined = "".join(P.ROLES.values())
    for word in ("괴담", "준호", "유채원", "정산", "지불", "헐값"):
        assert word not in joined
    # 감사 검사 3: 작품 고유명 스윕을 전체 프롬프트로 확장(C1 '괴담' 잔존류의 결정론 재발 차단)
    for x in _all_prompts():
        for word in ("괴담", "준호", "유채원"):
            assert word not in x


def test_design_words_not_demonstrated_in_prompts():
    # 감사 검사 4(M3): 결정론 검출어(DESIGN_WORDS)가 어떤 프롬프트에도 문형째 시연되지 않는다 —
    #   검출어를 생성 문형으로 보여주면 산출 복제·재변환 콜 증가(fmt_check 와의 자기 충돌)
    for x in _all_prompts():
        for w in P.DESIGN_WORDS:
            assert w not in x


def test_roles_take_event_and_base_sys_mounts_role_block():
    # 3안 다양성 = 같은 역할 + 서로 다른 {event} — 전 역할이 event 슬롯을 가진다
    for tpl in P.ROLES.values():
        assert "{event}" in tpl and _EVENT in tpl.format(event=_EVENT)
    # role_line 이 실리면 설계·수정 콜 양쪽에 같은 라벨 블록(사슬 대칭), 없으면 무추가
    rl = _ROLE_LINES[2]
    assert "[이 화의 몫]" in P.build_base_sys("", rl) and rl in P.build_base_sys("", rl)
    assert "[이 화의 몫]" in P.build_rev_sys(rl) and rl in P.build_rev_sys(rl)
    assert "[이 화의 몫]" not in P.build_base_sys()


def test_audit_axes_role_conditional():
    # 심문표 = 공통 4축 + 역할 축 1 — 강등 루프·기둥 카운트가 이 상수를 읽는다(장식 계약 차단)
    for role in P.ROLES:
        axes = P.audit_axes(role)
        assert axes[:4] == P.COMMON_AUDIT_AXES and len(axes) == 5
        assert f'"{axes[4]}"' in P.build_audit_sys(role)
    assert P.AUDIT_SYS == P.build_audit_sys("")   # 구 호출 호환(기본 비용 축)
    # 회수 축은 이중 인용(스토리 + 지난 이야기 출처) — 대조 실패 시 강등의 근거 필드
    assert "출처인용" in P.build_audit_sys("회수")
