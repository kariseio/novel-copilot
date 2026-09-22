# -*- coding: utf-8 -*-
"""B-34 회귀 가드 — 회차번호 메타 누출 소스차단(컨텍스트 조립 형식 잠금, LLM 0콜).

실측(편집자 리뷰 2026-07-03): 서리꽃 7cba74b80209 22화 지문에 '14화 서재에서와 같은 문장이었다' 등
'14화'가 3회 노출 — 실제 14화에 없는 장면을 가리키는 날조 콜백 포함. 실데이터 규명 결과, 22화 gen_context 에서
숫자 회차 라벨(N화)이 나온 소스는 정확히 두 곳: draft.story_so_far(_build_story_so_far/_hier)·plan.recent(_recent_summaries).

소스차단(pink-elephant/두더지잡기 회피): '회차 번호 쓰지 마라' 부정명령이나 사후 스크러버가 아니라,
라벨 형식 자체를 서술자가 본문에 옮겨 적지 않는 out-of-band 메타 태그('[#N]')로 바꿔 소스에서 끊는다.
이 테스트는 컨텍스트 조립 산출물에 '숫자+화' 산문 라벨이 다시 새는 것을 막는 형식 잠금이다.

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_b34_chapter_meta.py
"""
from __future__ import annotations
import re
import sys
from types import SimpleNamespace as NS

from novelcopilot.domain.types import ChapterStatus
from novelcopilot.services.copilot import (
    _recap_tag, _recent_summaries, _build_story_so_far, _build_story_so_far_hier,
)

# 누출 벡터: 산문에 그대로 낄 수 있는 '숫자+화' 라벨(교착어 조사 흡수 대비 선택적 공백 허용)
NUM_CHAPTER = re.compile(r"\d+\s*화")


def _ch(n, syn, ep="ep1", status=ChapterStatus.FINALIZED):
    return NS(chapter=n, status=status, episode_id=ep, title=f"제목{n}",
              detail_synopsis=syn, summary=syn, text=syn)


def test_recap_tag_is_out_of_band_meta() -> None:
    tag = _recap_tag(14)
    assert tag == "[#14]", f"라벨 형식 잠금 실패: {tag!r}"
    assert not NUM_CHAPTER.search(tag), f"메타 태그에 산문형 'N화' 잔존: {tag!r}"
    assert "14" in tag, "회차 번호 대응(작가 가시화·순서) 보존 실패"
    print("[OK] _recap_tag: out-of-band 메타 태그 '[#14]' (산문 'N화' 토큰 제거, 번호 대응 보존)")


def test_story_so_far_no_numeric_chapter_label() -> None:
    chs = [_ch(13, "리엘이 봉인 문양을 발견한다"),
           _ch(14, "카일런의 등 상처가 드러난다"),
           _ch(15, "세라가 성에 도착한다")]
    out, dropped = _build_story_so_far(chs, budget=10000)
    hits = NUM_CHAPTER.findall(out)
    assert not hits, f"story_so_far 에 숫자 회차 라벨 누출: {hits}\n---\n{out}"
    assert "카일런의 등 상처" in out and "세라가 성에 도착" in out, "요약 내용 소실"
    assert out.index("리엘이 봉인") < out.index("세라가 성에"), "시간순 보존 실패"
    print(f"[OK] _build_story_so_far: 숫자 회차 라벨 0건(내용·순서 보존) — dropped={dropped}")


def test_hier_story_so_far_no_numeric_chapter_label() -> None:
    ep1 = NS(episode_id="ep1", title="에피1", done=True, summary="에피1 롤업 요약", order=1)
    ep2 = NS(episode_id="ep2", title="에피2", done=False, summary="", order=2)
    arc = NS(order=1, title="아크1", episodes=[ep1, ep2])
    spine = NS(arcs=[arc])
    chs = [_ch(1, "먼 과거 사건", ep="ep1"),
           _ch(2, "직전 완료 에피소드 사건", ep="ep1"),
           _ch(3, "현재 에피소드 진행", ep="ep2")]
    state = NS(world=NS(spine=spine),
               narrative_progress=NS(current_episode_id="ep2"),
               chapters=chs)
    out, dropped = _build_story_so_far_hier(state, next_ch=4, budget=10000)
    hits = NUM_CHAPTER.findall(out)
    assert not hits, f"계층 story_so_far 에 숫자 회차 라벨 누출: {hits}\n---\n{out}"
    assert "현재 에피소드 진행" in out, "상세 레이어 내용 소실"
    print(f"[OK] _build_story_so_far_hier: 숫자 회차 라벨 0건(상세·롤업 레이어) — dropped={dropped}")


def test_recent_summaries_no_numeric_chapter_label() -> None:
    prior = [_ch(18, "국경 예배당 납치"), _ch(19, "이름 대신 서리"),
             _ch(20, "봉인된 방 계단"), _ch(21, "양피지 발견")]
    out = _recent_summaries(prior)
    joined = "\n".join(out)
    hits = NUM_CHAPTER.findall(joined)
    assert not hits, f"plan.recent 에 숫자 회차 라벨 누출: {hits}\n---\n{joined}"
    assert "직전" in joined, "직전(상대 위치) 마커 소실 — 인계 신호 보존 실패"
    assert "양피지 발견" in joined, "직전 회차 상세 내용 소실"
    print("[OK] _recent_summaries: 숫자 회차 라벨 0건(직전 마커·내용 보존)")


def test_assembled_context_format_lock() -> None:
    # 조립 산출물(누적 줄거리 + 최근 요약)을 합쳐도 산문형 'N화' 라벨이 0건이어야 한다(형식 잠금)
    chs = [_ch(n, f"{n}번째 사건") for n in range(10, 23)]
    ssf, _ = _build_story_so_far(chs, budget=20000)
    recent = "\n".join(_recent_summaries(chs))
    blob = ssf + "\n" + recent
    hits = NUM_CHAPTER.findall(blob)
    assert not hits, f"조립 컨텍스트에 숫자 회차 라벨 누출: {sorted(set(hits))}"
    print("[OK] 조립 컨텍스트 형식 잠금: 산문형 'N화' 라벨 0건")


TESTS = [
    test_recap_tag_is_out_of_band_meta,
    test_story_so_far_no_numeric_chapter_label,
    test_hier_story_so_far_no_numeric_chapter_label,
    test_recent_summaries_no_numeric_chapter_label,
    test_assembled_context_format_lock,
]


if __name__ == "__main__":
    ok = True
    for t in TESTS:
        try:
            t()
        except AssertionError as e:
            ok = False
            print(f"[FAIL] {t.__name__}: {e}")
    print("\nB-34 검증:", "ALL GREEN ✅" if ok else "FAIL ❌")
    sys.exit(0 if ok else 1)
