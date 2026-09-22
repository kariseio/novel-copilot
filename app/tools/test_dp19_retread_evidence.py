# -*- coding: utf-8 -*-
"""DP-19 재탕 근거 문장 추출 결정론 테스트 — retread_metrics 확장(직전 2화 대조·근거 문장·digest) 검증(LLM 0콜).

DP-19 재정의: 신규 계측 금지 — 기존 retread_metrics 확장만 검증한다.
  ① 직전 2화 각각과 head 대조(prev_pairs) — 조사 변이 흡수(어간 정규화 필수)
  ② 겹친 상위 내용어 어간 + 그 어간 포함 문장(직전화·현재화 각 1개) 원자료(evidence)
  ③ gen_gated._measure_digest 가 고계수(0.30 배치 위치 참조)면 상단에 '재탕 의심 대목: [문장]' 강조

임계 판정·자동 PASS/FAIL·원시 n-gram 없음 — 근거는 '어간 포함 문장' 통째로, 정독이 확증한다.
전부 순수 함수(합성 회차 dict)로 검증. 실 provider·서비스 미생성.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.dp4_loop import (
    retread_metrics, _overlap_evidence, _first_sentence_with_stem, _stem_freq)
from tools.gen_gated import _measure_digest


def _ch(n, text, key_events=None):
    return {"chapter": n, "text": text, "key_events": key_events or []}


# ─────────────────────────────────────────────────────────────────────────────
# ① 직전 2화 각각과 head 대조 — prev_pairs (조사 변이 흡수 = 어간 정규화 필수)
# ─────────────────────────────────────────────────────────────────────────────
def test_prev_pairs_two_chapters():
    chs = [_ch(1, "게이트가 열렸다. 헌터가 달려갔다."),
           _ch(2, "각성이 시작됐다. 서열이 흔들렸다."),
           _ch(3, "게이트가 다시 열렸다. 헌터가 또 달려갔다.")]
    rows = retread_metrics(chs, head=600)
    r3 = rows[2]
    # 직전 2화(ch1·ch2) 각각과 대조된 쌍이 정확히 2개.
    assert [p["chapter"] for p in r3["prev_pairs"]] == [1, 2]
    # ch3 은 ch1 재탕(게이트·헌터) → ch1 coef 가 ch2 보다 높다.
    c1 = next(p for p in r3["prev_pairs"] if p["chapter"] == 1)
    c2 = next(p for p in r3["prev_pairs"] if p["chapter"] == 2)
    assert c1["coef"] > c2["coef"]
    # 첫 회차는 선행 없음 → prev_pairs 빈 리스트.
    assert rows[0]["prev_pairs"] == []
    # 2화는 직전 1화만.
    assert [p["chapter"] for p in rows[1]["prev_pairs"]] == [1]
    print("[OK] prev_pairs: 직전 2화 각각 대조 + 재탕화 coef 우세 + 경계(첫화 빈·2화 1개)")
    return True


def test_particle_variation_absorbed():
    # 조사만 다른 동일 내용어('광민이'/'광민은'/'광민과')는 어간('광민')으로 흡수돼 재탕으로 잡혀야 한다.
    prev = "광민이 문을 열었다. 세라가 뒤따랐다."
    cur = "광민은 다시 문을 열었다. 세라가 또 뒤따랐다."
    ev = _overlap_evidence(cur, prev, head=600)
    assert "광민" in ev["stems"], ev["stems"]         # 조사 벗긴 어간이 겹침으로 집계
    assert "세라" in ev["stems"], ev["stems"]
    # 어간 빈도표도 조사 변이를 하나로 합산.
    fp = _stem_freq(prev)
    assert "광민" in fp and "광민이" not in fp
    # 원시 n-gram 아님: 근거는 문장 통째.
    assert ev["prev_sentence"] and ev["cur_sentence"]
    print("[OK] 조사 변이 흡수: '광민이'/'광민은' → 어간 '광민' 겹침 집계(교착어 substring 회피)")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ② 근거 문장 추출 — 직전화·현재화 각 1개, 해당 어간 포함(n-gram 아님)
# ─────────────────────────────────────────────────────────────────────────────
def test_evidence_sentence_extraction():
    chs = [_ch(1, "붉은 게이트가 도심 한복판에 열렸다. 사람들이 비명을 질렀다."),
           _ch(2, "붉은 게이트가 또 도심에 열렸다. 사람들이 다시 비명을 질렀다.")]
    rows = retread_metrics(chs, head=600)
    ev = rows[1]["evidence"]
    assert ev["prev_chapter"] == 1
    assert ev["stem"] is not None
    # 근거 문장은 원문 문장 그대로 — 해당 어간을 (어간정규화 매칭으로) 실제 포함.
    stem = ev["stem"]
    assert _first_sentence_with_stem(chs[0]["text"], stem) == ev["prev_sentence"]
    assert _first_sentence_with_stem(chs[1]["text"], stem) == ev["cur_sentence"]
    # 문장 통째(공백 포함) — 원시 n-gram 조각이 아니라 완결 문장.
    assert ev["prev_sentence"].endswith(("다.", "다")) or " " in ev["prev_sentence"]
    # overlap_stems 는 상위 어간 리스트(결정론 정렬).
    assert isinstance(rows[1]["overlap_stems"], list) and rows[1]["overlap_stems"]
    print(f"[OK] 근거 문장 추출: stem='{stem}' prev='{ev['prev_sentence'][:20]}…' cur='{ev['cur_sentence'][:20]}…'")
    return True


def test_no_overlap_empty_evidence():
    # 완전히 다른 도입부는 근거 어간 없음(빈 산출) — 없는 재탕을 발명하지 않음.
    chs = [_ch(1, "산속 오두막에 눈이 내렸다. 노인이 장작을 팼다."),
           _ch(2, "우주정거장 경보가 울렸다. 대원들이 헬멧을 썼다.")]
    rows = retread_metrics(chs, head=600)
    ev = rows[1]["evidence"]
    assert ev["stem"] is None
    assert ev["prev_sentence"] == "" and ev["cur_sentence"] == ""
    print("[OK] 무겹침: 근거 문장 없음(재탕 발명 안 함)")
    return True


def test_evidence_deterministic():
    # 동일 입력 → 동일 산출(정렬 결정론 — 상위 어간·근거 문장 재현).
    chs = [_ch(1, "붉은 게이트가 열렸다. 헌터가 달렸다."),
           _ch(2, "붉은 게이트가 또 열렸다. 헌터가 또 달렸다.")]
    a = retread_metrics(chs, head=600)[1]
    b = retread_metrics(chs, head=600)[1]
    assert a["overlap_stems"] == b["overlap_stems"]
    assert a["evidence"] == b["evidence"]
    print("[OK] 결정론: 반복 호출 시 상위 어간·근거 문장 동일")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ③ _measure_digest 스냅샷 — 고계수+근거 시 상단 '재탕 의심 대목' 강조
# ─────────────────────────────────────────────────────────────────────────────
def test_digest_high_coef_prepends_evidence():
    meas = {"nws": 4000, "nws_band_ok": True,
            "active_open": {"active": True, "agency": 0.7},
            "retread": {"opening_coef": 0.41, "keyevents_overlap_prev": 0.2,
                        "evidence": {"prev_chapter": 5, "stem": "게이트",
                                     "prev_sentence": "붉은 게이트가 열렸다.",
                                     "cur_sentence": "붉은 게이트가 또 열렸다."}},
            "drift_signals": []}
    d = _measure_digest(meas)
    lines = d.split("\n")
    # 상단(첫 줄)에 '재탕 의심 대목: [현재화 문장]' 강조 + 직전화 지목.
    assert lines[0].startswith("재탕 의심 대목:")
    assert "붉은 게이트가 또 열렸다." in lines[0]
    assert "5화" in lines[0]
    # 기존 원자료 요약(nws·재탕계수 등)은 그대로 뒤따른다(치환 아닌 프리펜드).
    assert "재탕계수=0.41" in d and "nws=4000" in d
    print("[OK] digest 스냅샷: 고계수+근거 → 상단 '재탕 의심 대목' 프리펜드")
    return True


def test_digest_low_coef_no_evidence_line():
    meas = {"nws": 4000, "nws_band_ok": True,
            "active_open": {"active": True, "agency": 0.7},
            "retread": {"opening_coef": 0.12, "keyevents_overlap_prev": 0.0,
                        "evidence": {"prev_chapter": 3, "stem": "문",
                                     "prev_sentence": "문이 열렸다.", "cur_sentence": "문이 닫혔다."}},
            "drift_signals": []}
    d = _measure_digest(meas)
    # 0.30 미만(배치 위치 참조)이면 강조 미부착 — 판정 아님, 단지 상단 배치 조건 미충족.
    assert not d.startswith("재탕 의심 대목:")
    assert "재탕계수=0.12" in d
    print("[OK] digest 스냅샷: 저계수 → 강조 미부착(원자료만)")
    return True


def test_digest_high_coef_but_no_evidence_sentence():
    # 고계수여도 근거 문장이 비면(도입부에서 문장 추출 실패) 강조 미부착 — 빈 인용 방지.
    meas = {"nws": 4000, "nws_band_ok": True,
            "active_open": {"active": True, "agency": 0.7},
            "retread": {"opening_coef": 0.5, "keyevents_overlap_prev": 0.0,
                        "evidence": {"prev_chapter": 2, "stem": None,
                                     "prev_sentence": "", "cur_sentence": ""}},
            "drift_signals": []}
    d = _measure_digest(meas)
    assert not d.startswith("재탕 의심 대목:")
    assert "재탕계수=0.5" in d
    print("[OK] digest 스냅샷: 고계수+근거 문장 없음 → 강조 미부착(빈 인용 방지)")
    return True


def test_digest_gate_uses_evidence_axis_not_global_max():
    # 축 일치(MED 수리): 전역 최댓값 opening_coef 가 높아도(먼 화 재탕) 인용문·직전화 지목을 낳은
    # evidence.prev_coef(직전 2화 축)가 저계수면 강조 미부착 — 오귀속 억제.
    #   시나리오: ch5가 ch1 오프닝 통째 재탕(opening_coef=1.0, opening_with=1)이나,
    #   직전 2화(ch3·ch4)는 모티프 단어만 우연 겹침(prev_coef=0.167) → 인용문은 ch3의 우연 겹침이므로 강조하면 오도.
    meas_low_prev = {"nws": 4000, "nws_band_ok": True,
                     "active_open": {"active": True, "agency": 0.7},
                     "retread": {"opening_coef": 1.0, "opening_with": 1,
                                 "keyevents_overlap_prev": 0.0,
                                 "evidence": {"prev_chapter": 3, "prev_coef": 0.167, "stem": "균열",
                                              "prev_sentence": "균열이 번졌다.", "cur_sentence": "균열이 번졌다."}},
                     "drift_signals": []}
    d = _measure_digest(meas_low_prev)
    assert not d.startswith("재탕 의심 대목:"), d   # 전역이 아니라 evidence 축으로 게이트 → 오도 강조 억제
    assert "재탕계수=1.0" in d                        # 원자료 요약은 전역 계수 그대로 보고(치환 아님)
    # 대조: 같은 전역 계수여도 직전 2화 축이 고계수(prev_coef>=0.30)면 정당하게 강조.
    meas_high_prev = {"nws": 4000, "nws_band_ok": True,
                      "active_open": {"active": True, "agency": 0.7},
                      "retread": {"opening_coef": 1.0, "opening_with": 1,
                                  "keyevents_overlap_prev": 0.0,
                                  "evidence": {"prev_chapter": 4, "prev_coef": 0.9, "stem": "게이트",
                                               "prev_sentence": "붉은 게이트가 열렸다.",
                                               "cur_sentence": "붉은 게이트가 또 열렸다."}},
                      "drift_signals": []}
    d2 = _measure_digest(meas_high_prev)
    assert d2.startswith("재탕 의심 대목:") and "4화" in d2.split("\n")[0]
    # 하위호환 폴백: evidence 에 prev_coef 부재(구 프로젝트 resume)면 opening_coef 로 게이트.
    meas_legacy = {"nws": 4000, "nws_band_ok": True,
                   "active_open": {"active": True, "agency": 0.7},
                   "retread": {"opening_coef": 0.41, "keyevents_overlap_prev": 0.0,
                               "evidence": {"prev_chapter": 5, "stem": "게이트",
                                            "prev_sentence": "붉은 게이트가 열렸다.",
                                            "cur_sentence": "붉은 게이트가 또 열렸다."}},
                   "drift_signals": []}
    d3 = _measure_digest(meas_legacy)
    assert d3.startswith("재탕 의심 대목:")           # prev_coef 없음 → opening_coef=0.41 폴백 발화
    print("[OK] digest 게이트 축 일치: 전역 최댓값 아닌 evidence.prev_coef 로 발화(오귀속 억제)+구프로젝트 폴백")
    return True


def test_backward_compat_keys_preserved():
    # 기존 소비처(dp4b·gen_gated·analyze)가 읽던 키 전부 보존(하위호환) + 신규 키 추가만.
    chs = [_ch(1, "게이트가 열렸다."), _ch(2, "게이트가 또 열렸다.")]
    row = retread_metrics(chs)[1]
    for k in ("chapter", "opening_coef", "opening_jac", "opening_with",
              "keyevents_overlap_prev", "retread_flag"):
        assert k in row, k
    for k in ("prev_pairs", "overlap_stems", "evidence"):
        assert k in row, k
    print("[OK] 하위호환: 기존 6키 보존 + 신규 3키(prev_pairs/overlap_stems/evidence) 추가")
    return True


def run_all() -> bool:
    results = [
        test_prev_pairs_two_chapters(),
        test_particle_variation_absorbed(),
        test_evidence_sentence_extraction(),
        test_no_overlap_empty_evidence(),
        test_evidence_deterministic(),
        test_digest_high_coef_prepends_evidence(),
        test_digest_low_coef_no_evidence_line(),
        test_digest_high_coef_but_no_evidence_sentence(),
        test_digest_gate_uses_evidence_axis_not_global_max(),
        test_backward_compat_keys_preserved(),
    ]
    ok = all(results)
    print(f"\n{'=' * 60}\nDP-19: {sum(results)}/{len(results)} PASS")
    return ok


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 run_all().
def test_dp19_all_green():
    assert run_all()


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
