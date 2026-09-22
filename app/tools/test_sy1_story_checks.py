# -*- coding: utf-8 -*-
"""SY-1 스토리 패스 결정론 검사 회귀 (설계 §10 — 자가시험 레포 승격 1/3).

세션 스크래치의 자가시험(28종)을 인라인 픽스처로 재현한다 — 픽스처는 실측 붕괴
산출물(2026-08-14 시연 런)의 결함 형태를 최소 문자열로 옮긴 것이다. LLM 0.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:                                            # 배선 후 정본(엔진) 우선, 배선 전은 tools
    from novelcopilot.engine import story_pass_prompts as P   # type: ignore
except ImportError:
    sys.path.insert(0, os.path.dirname(__file__))
    import story_pass_prompts_v3 as P           # type: ignore

# 실측 붕괴 형태 픽스처: 병합 콜 계약 부재 → 무마커·과거형·대사 원문(따옴표) 산문화
COLLAPSED = (
    "주인이 포승줄로 구슬을 휘감아 궤짝 안에 처박자, 형상이 유리 안쪽으로 멀어졌다. "
    '주인은 거울까지 천으로 덮으며 말했다. "오늘은 됐어. 사흘 안에 가져와."\n\n'
    "다음 날, 그는 골목을 걸었다.")
# 규격 준수 픽스처(12줄·현재형·따옴표 0)
GOOD = "\n".join(f"- 사건 {i}이 벌어지고, 그가 다음 수를 고른다." for i in range(12))


def test_fmt_check_detects_collapse():
    errs = P.fmt_check(COLLAPSED)
    assert any("불릿" in e for e in errs)
    assert any("따옴표" in e for e in errs)
    assert any("과거형" in e for e in errs)


def test_fmt_check_passes_clean():
    assert P.fmt_check(GOOD) == []


def test_fmt_check_line_bounds_and_labels():
    assert any("줄 수" in e for e in P.fmt_check("- 간다.\n- 온다."))
    assert any("설계 용어" in e for e in P.fmt_check("- 변수: 그가 움직인다.\n" + GOOD))
    # 정상 부사 '훅'은 라벨꼴이 아니므로 무검출(위양성 방지 — 재감사 M7)
    eleven = "\n".join(f"- 사건 {i}이 벌어지고, 그가 다음 수를 고른다." for i in range(11))
    assert not any("설계 용어" in e for e in P.fmt_check("- 찬 기운이 훅 끼친다.\n" + eleven))


def test_past_ending_jamo():
    assert P.past_ending_count("- 그는 시장으로 갔다.") == 1      # 축약형(받침 ㅆ 자모 검출)
    assert P.past_ending_count("- 문을 열었다.") == 1
    assert P.past_ending_count("- 그는 시장으로 간다.") == 0
    assert P.past_ending_count("- 먹었다는 말을 전하며 웃는다.") == 0   # 문중 활용형
    assert P.past_ending_count("- 방에 그가 있다. 곧 먹겠다.") == 0    # 비과거 ㅆ받침 제외


def test_cite_ok_normalized_substring():
    src = GOOD
    assert P.cite_ok("사건 3이 벌어지고, 그가", src)
    assert not P.cite_ok("전혀 없는 문장이다 이것은", src)
    assert not P.cite_ok("사건 3", src)          # 8자 하한(우연 일치 차단)


def test_pos_and_merge_checks():
    assert P.pos_ok("기둥 줄 7 뒤", 16) and not P.pos_ok("기둥 줄 25 뒤", 16)
    assert not P.pos_ok("자연스러운 곳", 16)
    assert P.merge_ok("- a\n- b\n- c", 2, 1) and not P.merge_ok("- a\n- b", 2, 1)


def test_prose_ok_truncation():
    # 실측: 수리 콜이 대사 중간(-32%)에서 절단 — 자수 하한과 말미 종결이 함께 잡아야 한다
    cut = "그는 폰을 들었다.\n\n\"폰"
    errs = P.prose_ok(cut, lo=4000)
    assert any("자수" in e for e in errs) and any("말미" in e for e in errs)
    assert P.prose_ok("완결된 문장으로 끝난다." * 300, lo=1000) == []


def test_render_review_drops_hallucinated_quotes():
    rv = {"지적": [{"대상": "사건 3이 벌어지고, 그가 다음 수를", "방향": "비용을 키운다"},
                   {"대상": "전혀 없는 문장이다 이것은", "방향": "삭제한다"}]}
    text, dropped = P.render_review(rv, GOOD)
    assert dropped == 1 and "전혀 없는" not in text and "비용을 키운다" in text


def test_story_block_guards_empty():
    # OFF 경로 바이트 동일을 구조로 보증(재감사 2차 M6) — 빈 값이면 주입 0
    assert P.build_story_block_draft("") == ""
    assert P.build_story_block_draft("   ") == ""
    assert P.build_story_block_continue([]) == ""


def test_remaining_lines_candidate_pool():
    story = "- 그가 시장에서 구슬을 산다.\n- 그가 골목에서 남자를 만난다."
    # 커버리지 판정기는 주입식(엔진 자산 재사용) — 여기서는 스텁으로 계약만 검증
    remaining = P.remaining_story_lines(story, "본문", lambda needles, prose: True)
    assert remaining == P.story_lines(story)
    assert P.remaining_story_lines(story, "본문", lambda n, p: False) == []
