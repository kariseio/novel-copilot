# -*- coding: utf-8 -*-
"""DP-13 검증 — 계획층 이벤트 중복 lint (결정론·LLM 0콜).

DP-4b 5화 실측: 비트 key_events ①(계산·못박는다)과 ⑤(정리·확정한다)가 같은 자금 산수를
이중 발주 → 프로즈가 회차내 산수 2회 반복(retention 68 첫 하락·독자 실지적).
소스=계획층 이벤트 중복. 검사 축은 key_events '상호' 1축뿐(required/climax 축은 설계계약
역행이라 폐기 — 비트가 required/climax 를 실현하는 건 목표이지 중복이 아님). 이 테스트는(양방향):
  · 실측 중복(①↔⑤)이 잡히는가
  · 정당하게 '전진'하는 연속 사건은 오차단되지 않는가(보수적)
  · 짧은 태그·인물/장소 공유만으로는 안 걸리는가(오탐 소스 차단 — 공유 어간≥3 가드)
  · required/climax 를 '실현'하는 정상 비트는 오플래그되지 않는가(설계계약)
실행: PYTHONPATH=app PYTHONIOENCODING=utf-8 py -3.12 tools/test_dp13_event_dup.py
     (pytest 로도 강제 — 함수는 assert 로 경계를 고정한다)
"""
from __future__ import annotations
import sys

from novelcopilot.engine.plan_lint import (event_dup_pairs, lint_events,
                                           _stem_set, _containment,
                                           _DUP_THRESHOLD, _MIN_SHARED)


def _flagged(key_events, required=None) -> set:
    """중복으로 걸린 (a,b) 쌍의 원문 텍스트 집합(순서 무관 비교용)."""
    return {frozenset((a, b)) for a, b, _ in event_dup_pairs(key_events, required)}


# ---------- (1) 실측 5화 중복: key_events 상호 이중 발주 ----------
def test_real_ch5_duplicate_flagged() -> None:
    # DP-4b 5화 원문(dp4b_loop.md §6): ①=입실 직후 산수 / ⑤=말미 정리 — 같은 자금 계산의 재발주
    ev1 = "잔고 이만 팔천 원을 확인하고 벌금 오십만 원 미납과 월세 이십팔만 원을 합산해 구십삼만 원을 못 박는다"
    ev5 = "고시원 월세 이십팔만 원과 벌금 오십만 원을 정리해 최소 구십삼만 원 이상이 목표 자금임을 확정한다"
    other = "마정석 흡수를 중단하고 회로 자력 개통의 대가로 살갗이 화상을 입는다"   # 신규 재료(비중복)
    pairs = event_dup_pairs([ev1, other, ev5])
    flagged = _flagged([ev1, other, ev5])
    ok = (frozenset((ev1, ev5)) in flagged            # ①↔⑤ 중복 검출
          and frozenset((ev1, other)) not in flagged  # 신규 재료는 비중복
          and frozenset((ev5, other)) not in flagged)
    ratio = next((r for a, b, r in pairs if {a, b} == {ev1, ev5}), 0.0)
    print(f"[{'OK' if ok else 'FAIL'}] 5화 실측 이중발주 검출(①↔⑤ 겹침 {ratio:.2f}), 신규재료 통과")
    assert ok


def test_real_ch5_promoted_to_violation() -> None:
    ev1 = "잔고 이만 팔천 원을 확인하고 벌금 오십만 원 미납과 월세 이십팔만 원을 합산해 구십삼만 원을 못 박는다"
    ev5 = "고시원 월세 이십팔만 원과 벌금 오십만 원을 정리해 최소 구십삼만 원 이상이 목표 자금임을 확정한다"
    viols = lint_events([ev1, ev5])
    ok = (len(viols) == 1 and viols[0].kind == "plan_event_dup"
          and viols[0].grade.value == "deterministic" and viols[0].entity == "beat")
    print(f"[{'OK' if ok else 'FAIL'}] Violation 승격(plan_event_dup·deterministic): n={len(viols)}")
    assert ok


# ---------- (2) 정당한 연속 사건은 오차단 금지(보수적 — false-positive 방지) ----------
def test_legit_progression_not_flagged() -> None:
    # 같은 인물/장소를 공유하지만 '다른 행동'으로 전진하는 정당한 연속 — 오차단되면 재계획 낭비/사건 소멸
    evs = [
        "도현이 폐공장 균열 앞에서 그림자 늑대 세 마리를 처치하고 유물을 회수한다",
        "도현이 브로커 박씨를 접선해 마정석 조각을 흥정하고 시세를 파악한다",
        "도현이 백호의 미행을 감지하고 옥상으로 역진입해 감시 방향을 조작한다",
    ]
    flagged = _flagged(evs)
    ok = len(flagged) == 0
    print(f"[{'OK' if ok else 'FAIL'}] 정당 연속 사건 오차단 0(전진 사건 {len(evs)}건 무플래그)")
    assert ok


def test_shared_name_place_not_flagged() -> None:
    # 인물·장소 명사만 공유(내용 동작은 완전히 다름) — 부분 겹침으로 오탐 나면 안 됨
    evs = [
        "주인공이 길드 접수처에서 등록 절차를 밟고 신분증을 발급받는다",
        "주인공이 길드 훈련장에서 검술 스승과 대련하며 새 초식을 배운다",
    ]
    flagged = _flagged(evs)
    ok = len(flagged) == 0
    print(f"[{'OK' if ok else 'FAIL'}] 인물/장소 공유만으로는 무플래그(오탐 차단)")
    assert ok


def test_short_tags_excluded() -> None:
    # 내용어 어간 2개 미만 짧은 태그('전개'·'이동' 류)는 판정 근거 부족 → 검사 제외(오탐 소스)
    flagged = _flagged(["전개", "전개", "이동"])
    ok = len(flagged) == 0
    print(f"[{'OK' if ok else 'FAIL'}] 짧은 태그(<2 내용어) 검사 제외")
    assert ok


# ---------- (2b) MED 수정: 짧은 태그에서 엔티티(주인공명·상시 장소) 1~2개만 공유 → 오탐 금지 ----------
def test_short_tag_shared_name_not_flagged() -> None:
    # 실측(app/data 222 비트 스캔에서 유일 axis-1 flag 였던 오탐): '이하린'이라는 인물명만 공유하는 별개 비트
    evs = ["이하린과의 재회", "이하린의 의심과 첫 신뢰"]
    flagged = _flagged(evs)
    ok = len(flagged) == 0   # 공유 어간 1개(이하린) < _MIN_SHARED → 무플래그
    print(f"[{'OK' if ok else 'FAIL'}] 인물명 1개만 공유하는 짧은 별개 비트 무플래그(min_shared)")
    assert ok


def test_short_tag_repeated_name_not_flagged() -> None:
    # 같은 주인공명을 공유하는 3개의 서로 다른 짧은 비트 — 스퓨리어스 쌍 0
    evs = ["도현의 각성", "도현의 복수", "도현의 귀환"]
    flagged = _flagged(evs)
    ok = len(flagged) == 0
    print(f"[{'OK' if ok else 'FAIL'}] 주인공명 공유 3개 별개 비트 무플래그(스퓨리어스 쌍 0)")
    assert ok


def test_name_plus_place_two_shared_not_flagged() -> None:
    # 엔티티 2개(주인공명+상시 장소) 공유하지만 동작은 별개(처치 vs 함정 해제) — min_shared=3 이 차단
    evs = [
        "주인공이 보스방에서 오크 군주를 처치한다",
        "주인공이 보스방에서 함정을 해제한다",
    ]
    flagged = _flagged(evs)
    ok = len(flagged) == 0   # 공유 어간 2개(보스방·주인공) < _MIN_SHARED
    print(f"[{'OK' if ok else 'FAIL'}] 엔티티 2개(명+장소)만 공유하는 별개 동작 무플래그")
    assert ok


# ---------- (3) 설계계약: required/climax 를 '실현'하는 정상 비트는 오플래그 금지 ----------
def test_required_realization_not_flagged() -> None:
    # 시스템은 비트가 required_events 를 이 지면에서 실현하도록 코드로 강제한다(arc_planner: 맨 앞 보존).
    #   그러므로 key_events 가 required 를 (심지어 verbatim) 실현하는 건 목표이지 중복이 아니다 — 오플래그되면
    #   재계획이 required 실현 자체를 결함으로 지시해 필수사건이 죽는다(설계계약 역행). required 축은 폐기됨.
    required = ["주인공이 각성 시험을 통과해 정식 헌터가 된다", "길드가 주인공을 스카우트한다"]
    kev = ["주인공이 각성 시험을 통과해 정식 헌터가 된다",   # required 를 verbatim 실현(정상)
           "주인공이 시험장에서 라이벌과 충돌한다"]           # + 신규 사건
    pairs = event_dup_pairs(kev, required)   # required 인자는 무시됨(하위호환)
    ok = len(pairs) == 0
    print(f"[{'OK' if ok else 'FAIL'}] required verbatim 실현 정상비트 무플래그(설계계약): pairs={len(pairs)}")
    assert ok


def test_finale_realizes_climax_not_flagged() -> None:
    # finale 회차는 설계상 climax 를 반드시 이 지면에서 터뜨린다(arc_planner: '아래 climax 를 터뜨려라').
    #   climax 를 실현하는 finale key_event 가 중복으로 잡히면 finale-delivery 계약이 역행한다.
    climax = "주인공이 마왕을 봉인해 대륙을 구한다"
    kev = ["주인공이 마왕을 봉인해 대륙을 구한다",   # climax 실현(finale 의 존재 이유)
           "동료들이 주인공의 귀환을 맞이한다"]
    pairs = event_dup_pairs(kev, [climax])
    ok = len(pairs) == 0
    print(f"[{'OK' if ok else 'FAIL'}] finale climax 실현 무플래그(finale-delivery 계약): pairs={len(pairs)}")
    assert ok


def test_required_arg_ignored() -> None:
    # required 인자를 넘겨도 결과가 안 넘길 때와 동일(축 폐기 — 하위호환 시그니처만 유지)
    kev = ["주인공이 협회 기록실에 잠입해 실종 헌터 명단을 빼낸다",
           "주인공이 첫 게이트에서 그림자 늑대를 급소 일격으로 처치한다"]
    required = ["주인공이 첫 게이트에서 그림자 늑대를 급소 일격으로 처치한다"]
    ok = event_dup_pairs(kev, required) == event_dup_pairs(kev, None) == event_dup_pairs(kev)
    print(f"[{'OK' if ok else 'FAIL'}] required 인자 무시(축 폐기·하위호환)")
    assert ok


# ---------- (4) 임계 경계·양방향 대칭·공유 어간 가드 ----------
def test_threshold_and_min_shared_boundary() -> None:
    # ratio≥0.4 '그리고' 공유 어간≥3 을 둘 다 요구. 어간 5개 태그 기준:
    #   b2: 겹침 2 → ratio 0.4 이지만 공유 2<3 → 통과(오탐 차단). b3: 겹침 3 → ratio 0.6·공유 3 → 위반.
    a = "가나 다라 마바 사아 자차"      # 어간 5개
    b2 = "가나 다라 하카 카타 파하"     # 겹침 2 → ratio 0.4, 공유 2 < _MIN_SHARED → 통과
    b3 = "가나 다라 마바 카타 파하"     # 겹침 3 → ratio 0.6, 공유 3 ≥ _MIN_SHARED → 위반
    r2 = _containment(_stem_set(a), _stem_set(b2))
    r3 = _containment(_stem_set(a), _stem_set(b3))
    two_shared_passes = len(event_dup_pairs([a, b2])) == 0    # 공유 2 → 오탐 차단
    three_shared_flags = len(event_dup_pairs([a, b3])) == 1   # 공유 3 → 위반
    ok = (abs(r2 - 0.4) < 1e-9 and abs(r3 - 0.6) < 1e-9
          and two_shared_passes and three_shared_flags
          and _DUP_THRESHOLD == 0.4 and _MIN_SHARED == 3)
    print(f"[{'OK' if ok else 'FAIL'}] 경계: 공유2(r={r2:.1f}) 통과·공유3(r={r3:.1f}) 위반 "
          f"(threshold={_DUP_THRESHOLD}, min_shared={_MIN_SHARED})")
    assert ok


def test_real_dup_above_legit_below() -> None:
    """양방향 분리 마진 확증: 실측 재발주(0.46·공유6) 는 FLAG, 인물+장소(0.25/0.5·공유≤2)·전진(0.11·공유1) 은 무플래그."""
    dup_a = "잔고 이만 팔천 원을 확인하고 벌금 오십만 원 미납과 월세 이십팔만 원을 합산해 구십삼만 원을 못 박는다"
    dup_b = "고시원 월세 이십팔만 원과 벌금 오십만 원을 정리해 최소 구십삼만 원 이상이 목표 자금임을 확정한다"
    r_dup = _containment(_stem_set(dup_a), _stem_set(dup_b))
    n_dup = len(_stem_set(dup_a) & _stem_set(dup_b))
    legit_a = "도현이 폐공장 균열 앞에서 그림자 늑대 세 마리를 처치하고 유물을 회수한다"
    legit_b = "도현이 브로커 박씨를 접선해 마정석 조각을 흥정하고 시세를 파악한다"
    dup_flagged = len(event_dup_pairs([dup_a, dup_b])) == 1
    legit_flagged = len(event_dup_pairs([legit_a, legit_b])) == 0
    ok = (r_dup >= _DUP_THRESHOLD and n_dup >= _MIN_SHARED and dup_flagged and legit_flagged)
    print(f"[{'OK' if ok else 'FAIL'}] 분리 마진: 재발주 r={r_dup:.2f}·공유{n_dup} FLAG > 전진 무플래그")
    assert ok


def test_containment_symmetric() -> None:
    # 방향 대칭(min 분모): 작은 쪽이 큰 쪽에 통째로 담기면 순서 무관 1.0. 공유 어간≥3 이라 min_shared 통과.
    small = "가나 다라 마바"
    big = "가나 다라 마바 사아 자차"
    r_ab = _containment(_stem_set(small), _stem_set(big))
    r_ba = _containment(_stem_set(big), _stem_set(small))
    # key_events 상호는 순서 무관 1회만 걸림
    flagged = event_dup_pairs([big, small])
    ok = abs(r_ab - 1.0) < 1e-9 and abs(r_ba - 1.0) < 1e-9 and len(flagged) == 1
    print(f"[{'OK' if ok else 'FAIL'}] containment 방향 대칭·중복 1회 검출")
    assert ok


def test_josa_variation_absorbed() -> None:
    # 어간(_stem) 매칭이 조사 변이를 흡수 — '벌금이'/'벌금을' 등 다른 조사형이라도 같은 사건으로 인식.
    #   공유 어간 5개(벌금·월세·잔고·목표·자금) ≥ _MIN_SHARED.
    ev1 = "벌금과 월세와 잔고를 합산해 목표 자금을 계산한다"
    ev2 = "벌금이 월세와 잔고에 더해져 목표 자금이 정리된다"
    ok = len(event_dup_pairs([ev1, ev2])) == 1
    print(f"[{'OK' if ok else 'FAIL'}] 조사 변이 흡수(어간 매칭으로 동일 사건 인식)")
    assert ok


def test_empty_and_none_safe() -> None:
    ok = (event_dup_pairs([]) == [] and event_dup_pairs(["전개"], None) == []
          and event_dup_pairs([""], [""]) == [] and lint_events([]) == [])
    print(f"[{'OK' if ok else 'FAIL'}] 빈/None 입력 안전(no-op)")
    assert ok


_TESTS = [
    test_real_ch5_duplicate_flagged, test_real_ch5_promoted_to_violation,
    test_legit_progression_not_flagged, test_shared_name_place_not_flagged,
    test_short_tags_excluded,
    test_short_tag_shared_name_not_flagged, test_short_tag_repeated_name_not_flagged,
    test_name_plus_place_two_shared_not_flagged,
    test_required_realization_not_flagged, test_finale_realizes_climax_not_flagged,
    test_required_arg_ignored,
    test_threshold_and_min_shared_boundary, test_real_dup_above_legit_below,
    test_containment_symmetric, test_josa_variation_absorbed, test_empty_and_none_safe,
]

if __name__ == "__main__":
    results = []
    for t in _TESTS:
        try:
            t()
            results.append(True)
        except AssertionError:
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
