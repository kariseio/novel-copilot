# -*- coding: utf-8 -*-
"""DP-14 검증 — 1인칭(pov='first') agency 측정 축. LLM 0콜(전 결정론·측정 전용·판정기 0).

배경(dp4b_analyze): pov='first' 에서 needle(주인공 지칭형) 기반 능동 개시 휴리스틱이 전 회차 0.0 —
  구조적 실명(DP-4b). 원인은 프로즈가 아니라 측정: 1인칭 서술은 주인공을 주어 생략('나는/내가' 미표기)으로
  쓰므로 needle 매칭 문장이 회차당 0~2개로 붕괴해 init/react 표본이 사라진다(DP-4b 9화 실증).

수리: activity_metrics 에 pov 인자 추가 — pov='first' 일 때만 대상 문장 풀을 1인칭 명시주어·주어생략
  동작문단첫문장으로 확장(부정 재분류 포함). 기본(pov 미지정 또는 'third*')은 needle 축과 **바이트 동일**.

검증 축:
1) 3인칭 경로 바이트 불변 — pov 미지정/'third_limited' 이 needle 축과 동일(독립 카운트·부정 무재분류).
2) 1인칭 주어 생략 복구 — needle 부재 회차가 needle 축=0, 1인칭 축>0(주어생략 문단첫문장·명시 1인칭).
3) 부정 재분류 — 개시 어휘가 부정문('반격할 틈이 없었다')에 박히면 개시 아님(반응으로).
4) 제3자 명시 주어 배제 — '남자가 물러섰다' 는 주어생략 아님(주격표지)·명명 인물도 배제.
5) 부사구 파편 배제 — 종결어미 없는 파편('…향해.')은 주어생략 후보 아님.
6) 내면 결단 중립 유지 — 1인칭 축에서도 결심/계획은 개시로 안 셈(inner 분리·분모 제외).
7) DP-4b 9화 캘리브레이션 — 실 프로젝트 pov='first' 9화가 정독(강한 agentive)과 방향 일치(active·score↑),
   동시에 3인칭 DP-1 baseline 은 회귀 없음(라이브 데이터 존재 시에만 — 없으면 skip).

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_dp14_first_person_agency.py
"""
from __future__ import annotations
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

import dp1_dopamine_baseline as dp1

_LIVE_PID = "c6e39900882a"       # DP-4b '[실험 DP-4b] 1위의 재림' pov='first'
_DP1_PID = "81cbd54a8672"        # DP-1 시드A(3인칭) — 회귀 대조


# ---------- 1) 3인칭 경로 바이트 불변(독립 카운트·부정 무재분류) ----------
def test_third_person_path_byte_identical() -> bool:
    # DP-2 IN-27 회귀 시나리오(3인칭) — pov 미지정과 'third_limited' 가 동일해야 한다.
    acts = [{"chapter": 1, "text": "도현은 먼저 움직였다. 도현이 적을 향해 검을 뽑아 던졌다. 도현은 판을 장악했다."},
            {"chapter": 2, "text": "도현은 당황했다. 도현이 뒤로 밀렸다. 도현은 속수무책으로 끌려갔다."}]
    base = dp1.activity_metrics(acts, ["도현"])                       # pov 미지정
    third = dp1.activity_metrics(acts, ["도현"], pov="third_limited") # 명시 3인칭
    ok = (base["ratio"] == 0.5 and base["flags"] == [True, False])
    ok &= (base["pov_axis"] == "needle")
    ok &= (base["per_chapter"] == third["per_chapter"])               # 3인칭 경로 동일
    # 독립 카운트 보존: 한 문장에 개시+내면 어휘 공존 시 둘 다 셈(우선순위 단일배정 아님).
    dual = [{"chapter": 1, "text": "도현은 계획을 세우며 적을 향해 검을 뽑아 던졌다."}]
    m = dp1.activity_metrics(dual, ["도현"])                          # 3인칭: init·inner 둘 다
    r = m["per_chapter"][0]
    ok &= (r["init"] == 1 and r["inner"] == 1)                        # 독립 카운트(바이트 불변 근거)
    print(f"[{'OK' if ok else 'FAIL'}] 3인칭 경로 바이트 불변: ratio {base['ratio']}·독립카운트(init={r['init']}/inner={r['inner']})")
    return ok


# ---------- 2) 1인칭 주어 생략 복구(needle 축 0 → 1인칭 축 >0) ----------
def test_first_person_subject_omission_recovered() -> bool:
    # 주인공 이름(도현) 지면 부재 + 주어 생략 동작 문단 — 3인칭 축은 0, 1인칭 축은 개시 포착.
    text = "손전등을 껐다.\n\n계단을 내려갔다.\n\n단검을 뽑아 던졌다.\n\n적을 향해 움직였다."
    chapters = [{"chapter": 1, "text": text}]
    needle = dp1.activity_metrics(chapters, ["도현"])                 # 3인칭(needle) — 표본 0
    first = dp1.activity_metrics(chapters, ["도현"], pov="first")     # 1인칭 — 주어생략 복구
    rn, rf = needle["per_chapter"][0], first["per_chapter"][0]
    ok = (rn["init"] == 0 and rn["prot_sents"] == 0)                  # needle 축 구조적 0
    ok &= (rf["init"] >= 2 and rf["active_open"] is True)             # 1인칭 축 개시 복구
    ok &= (first["pov_axis"] == "first")
    # 명시 1인칭 주어 문장도 포착(needle 부재라도)
    ex = [{"chapter": 1, "text": "나는 검을 뽑아 던졌다. 내가 먼저 움직였다."}]
    rf2 = dp1.activity_metrics(ex, ["도현"], pov="first")["per_chapter"][0]
    ok &= (rf2["init"] >= 2)
    print(f"[{'OK' if ok else 'FAIL'}] 1인칭 복구: needle init={rn['init']}(prot 0) → 1인칭 init={rf['init']} active={rf['active_open']}")
    return ok


# ---------- 3) 부정 재분류(개시 어휘가 부정문 → 반응) ----------
def test_negation_reclassifies_initiate_to_react() -> bool:
    # '반격'은 개시 어휘지만 '없었다'(부정)이면 지면 개시 아님 → 반응.
    neg = [{"chapter": 1, "text": "반격할 틈이 없었다."}]
    r = dp1.activity_metrics(neg, ["도현"], pov="first")["per_chapter"][0]
    ok = (r["init"] == 0 and r["react"] == 1)
    # 3인칭 축은 재분류 없음(바이트 불변) — needle 부재라 표본 0(부정 재분류 미적용 확인은 명시 주어로)
    neg3 = [{"chapter": 1, "text": "도현은 반격할 틈이 없었다."}]
    r3 = dp1.activity_metrics(neg3, ["도현"])["per_chapter"][0]
    ok &= (r3["init"] == 1)                                           # 3인칭: 부정 무시(독립 카운트, 개시로 셈)
    print(f"[{'OK' if ok else 'FAIL'}] 부정 재분류: 1인칭 init={r['init']}/react={r['react']} · 3인칭 개시 불변(init={r3['init']})")
    return ok


# ---------- 4) 제3자 명시 주어 배제(주격표지·명명 인물) ----------
def test_third_party_explicit_subject_excluded() -> bool:
    # 문단 첫 문장이 제3자 명시 주어('남자가 물러섰다')면 주인공 주어생략 아님 → 배제.
    t = "남자가 뒤로 물러섰다.\n\n남자가 손목을 움켜쥐며 밀렸다."
    r = dp1.activity_metrics([{"chapter": 1, "text": t}], ["도현"], pov="first")["per_chapter"][0]
    ok = (r["react"] == 0 and r["prot_sents"] == 0)                   # 제3자 반응 오집계 없음
    # 명명 인물(other_needles) 주어도 배제 — '민수가' 문단 첫 문장
    t2 = "민수가 검을 뽑아 던졌다."
    r2 = dp1.activity_metrics([{"chapter": 1, "text": t2}], ["도현"],
                              pov="first", other_needles=["민수"])["per_chapter"][0]
    ok &= (r2["init"] == 0)                                           # 조연 개시가 주인공으로 오집계 안 됨
    print(f"[{'OK' if ok else 'FAIL'}] 제3자 배제: 남자 주어 react={r['react']}(pool {r['prot_sents']})·조연 개시 init={r2['init']}")
    return ok


# ---------- 5) 부사구 파편 배제(종결어미 없음) ----------
def test_adverbial_fragment_excluded() -> bool:
    # '왼팔을 향해.' 는 종결어미(finite) 없는 부사구 파편 → 주어생략 후보 아님(개시 오집계 차단).
    t = "왼팔을 향해.\n\n조각을 향해."
    r = dp1.activity_metrics([{"chapter": 1, "text": t}], ["도현"], pov="first")["per_chapter"][0]
    ok = (r["init"] == 0 and r["prot_sents"] == 0)
    # 대조: 종결어미 있는 동작문은 포착('향해 움직였다')
    t2 = "왼팔을 향해 움직였다."
    r2 = dp1.activity_metrics([{"chapter": 1, "text": t2}], ["도현"], pov="first")["per_chapter"][0]
    ok &= (r2["init"] == 1)
    print(f"[{'OK' if ok else 'FAIL'}] 파편 배제: 파편 init={r['init']}(pool {r['prot_sents']}) · 종결문 init={r2['init']}")
    return ok


# ---------- 6) 내면 결단 중립 유지(1인칭 축) ----------
def test_inner_decision_neutral_in_first_person() -> bool:
    # 1인칭 명시 주어라도 결심/계획은 개시 아님 → inner 로 분리(분모 제외).
    t = "나는 복수를 결심했다.\n\n계획을 세웠다."
    r = dp1.activity_metrics([{"chapter": 1, "text": t}], ["도현"], pov="first")["per_chapter"][0]
    ok = (r["init"] == 0 and r["inner"] >= 1 and r["react"] == 0)
    ok &= (r["agency_score"] == 0.0 and r["active_open"] is False)
    print(f"[{'OK' if ok else 'FAIL'}] 1인칭 내면 중립: init={r['init']}/inner={r['inner']} score={r['agency_score']}")
    return ok


# ---------- 7) DP-4b 9화 캘리브레이션 + 3인칭 회귀 대조(라이브 데이터 존재 시) ----------
def test_dp4b_ch9_calibration() -> bool:
    if not (dp1.PROJ / f"{_LIVE_PID}.json").exists():
        print("[SKIP] DP-4b 라이브 프로젝트 부재 — 캘리브레이션 생략(로직 테스트 1~6 로 커버)")
        return True
    d = dp1.diagnose(_LIVE_PID)
    a = d["activity"]
    ok = (d.get("pov") == "first" and a["pov_axis"] == "first")
    ok &= (a["ratio"] > 0.0)                                          # 구조적 0.0 탈출(핵심 수리)
    # 9화(정독=강한 agentive: 던전 잠입·늑대 3처치·함정 회피) 방향 일치 — 능동 회차로 뜬다.
    ch9 = next((r for r in a["per_chapter"] if r["chapter"] == 9), None)
    if ch9 is not None:                                              # 9화 존재 시(라이브 상태 의존)
        ok &= (ch9["active_open"] is True and ch9["agency_score"] >= 0.6)
    # 3인칭 DP-1 baseline 회귀 없음(존재 시) — needle 축 유지, 알려진 ratio 0.333.
    if (dp1.PROJ / f"{_DP1_PID}.json").exists():
        a1 = dp1.diagnose(_DP1_PID)["activity"]
        ok &= (a1["pov_axis"] == "needle" and a1["ratio"] == 0.333 and a1["active_open_chapters"] == [2, 4])
    ch9s = f"ch9 active={ch9['active_open']} score={ch9['agency_score']}" if ch9 else "ch9 부재"
    print(f"[{'OK' if ok else 'FAIL'}] DP-4b 캘리브레이션: pov=first ratio={a['ratio']} · {ch9s} · 3인칭 회귀 없음")
    return ok


def main() -> int:
    results = [
        test_third_person_path_byte_identical(),
        test_first_person_subject_omission_recovered(),
        test_negation_reclassifies_initiate_to_react(),
        test_third_party_explicit_subject_excluded(),
        test_adverbial_fragment_excluded(),
        test_inner_decision_neutral_in_first_person(),
        test_dp4b_ch9_calibration(),
    ]
    print("\nDP-14(1인칭 agency 축) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_dp14_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
