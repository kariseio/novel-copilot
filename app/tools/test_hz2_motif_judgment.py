# -*- coding: utf-8 -*-
"""HZ-2 지각 판정 모티프(N-3) 확장 검증 — 같은 판정 콜에서 모티프도 판정·인용 위치 게이트. LLM 0콜(스텁)·결정론.

설계(사용자 승인 2026-07-15 — 6화 성적표: 휴머나이즈가 N-3 모티프 6건을 매화 재작성 시도·대부분 over_change
폴백=헛수술). HZ-1 지각 판정을 확장해 *추가 콜 0*으로 회차 간 반복 모티프도 낭독 체감 판정한다:
  ① style_judge.judge_style 이 모티프 후보 목록을 [참고 자료]로 동봉 + {motif_spans:[{quote,why}]} 산출.
  ② apply_style_judgment(구명 apply_n4_judgment alias)가 N-3 findings 를 판정 motif_spans 인용 위치로 게이트
     (select_n4_findings 동형: 위치 매칭→모티프 스팬 정밀화→사실 밀집 금기→상한). 미확인 모티프=드롭+skip.
  ③ config humanize_motif_judgment(기본 True). OFF=구 동작(N-3 무조건 통과·바이트 동일).
  ④ trace(_trace_style_judgment)에 motif_spans 자동 포함(judgment 전문 저장).

검증 축(티켓 명세):
  1) judge_style 이 모티프 후보 [참고자료] 동봉 + motif_spans 산출(스텁).
  2) 모티프 인자 미전달 = 기존 동작(문말만·프롬프트 바이트 동일).
  3) N-3 findings 가 판정 motif_spans 인용 위치로 게이트(확인=수술 대상·미확인=드롭+skip 기록).
  4) 사실 밀집 모티프 금기(fallback=fact_dense).
  5) humanize_motif_judgment OFF = N-3 무조건 통과(구동작).
  6) 판정 실패(None) 시 N-3 보수 스킵(judge_missing).
  7) trace 에 motif_spans 포함(judgment 전문 저장).

실행: (app/ 에서) py -3.12 -X utf8 -m pytest tools/test_hz2_motif_judgment.py -q
"""
from __future__ import annotations
import pathlib
import sys
from types import SimpleNamespace

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from novelcopilot.config import Settings
from novelcopilot.engine import humanize_detect as hd
from novelcopilot.engine import style_judge as sj


# 회차 간 반복 모티프('빛줄기가 맥박쳤다')가 지문에 여러 번 나오는 두 화 — 원장/탐지 대상.
MOTIF_PHRASE = "빛줄기가 맥박쳤다"
CH1 = ("어둠 속에서 빛줄기가 맥박쳤다. 그는 멈춰 섰다.\n"
       "다시 빛줄기가 맥박쳤다. 심장이 뛰었다.")
CH2 = ("복도 끝에서 또 빛줄기가 맥박쳤다. 그녀는 돌아섰다.\n"
       "느리게 빛줄기가 맥박쳤다. 발이 굳었다.")


def _judge(needs=False, spans=None, motif_spans=None):
    """judge_style 산출 형식 스텁 — spans=[(q,w)…] N-4, motif_spans=[(q,w)…] N-3."""
    return {"needs_repair": needs,
            "spans": [{"quote": q, "why": w} for (q, w) in (spans or [])],
            "motif_spans": [{"quote": q, "why": w} for (q, w) in (motif_spans or [])],
            "reason": "stub", "reference": {"runs": [], "backend": "kiwi", "n_runs": 0}}


def _n3_findings(text):
    """text 에 대한 N-3 findings 를 실제 원장·탐지로 산출(결정론) — 게이트 입력."""
    ledger = hd.build_motif_ledger([{"chapter": 1, "text": CH1}, {"chapter": 2, "text": CH2}])
    return [f for f in hd.detect_chapter(text, ledger=ledger) if f.get("category") == "N-3"]


# ═════════════════ ① judge_style 모티프 후보 동봉 + motif_spans 산출 ═════════════════

def test_judge_motif_reference_and_motif_spans() -> bool:
    """1) 모티프 후보 전달 시 [참고 자료]에 동봉('판정 기준은 낭독 체감')·motif_spans 산출·시스템에 모티프 지시."""
    captured = {"msgs": None}

    class Cap:
        usage = SimpleNamespace(chat_tokens=0)
        def chat_json(self, messages, *a, **k):
            captured["msgs"] = messages
            return {"needs_repair": True,
                    "spans": [],
                    "motif_spans": [{"quote": MOTIF_PHRASE, "why": "기계 반복"}],
                    "reason": "모티프 반복"}
    out = sj.judge_style(Cap(), CH1, motif_candidates=[MOTIF_PHRASE])
    joined = "\n".join(m["content"] for m in (captured["msgs"] or []))
    ok = ("참고 자료" in joined and "모티프" in joined)        # 모티프 후보 [참고 자료] 동봉
    ok &= (MOTIF_PHRASE in joined)                             # 후보 구절이 프롬프트에 실림
    ok &= ("낭독 체감" in joined)                              # 판정 기준=체감(임계 아님)
    ok &= ("motif_spans" in joined)                           # 시스템 프롬프트 모티프 판정 지시(JSON 필드 명세)
    ok &= (out is not None and out["motif_spans"] == [{"quote": MOTIF_PHRASE, "why": "기계 반복"}])
    print(f"[{'OK' if ok else 'FAIL'}] 1: judge_style 모티프 후보 동봉+motif_spans 산출")
    return bool(ok)


def test_judge_motif_spans_empty_when_natural() -> bool:
    """1보강) 모티프 후보가 있어도 판정이 자연스럽다고 하면 motif_spans 빈 배열(pink-elephant 최소·강제 아님)."""
    class Cap:
        usage = SimpleNamespace(chat_tokens=0)
        def chat_json(self, *a, **k):
            return {"needs_repair": False, "spans": [], "motif_spans": [], "reason": "자연스러움"}
    out = sj.judge_style(Cap(), CH1, motif_candidates=[MOTIF_PHRASE])
    ok = (out is not None and out["motif_spans"] == [])
    # motif_spans 필드 자체가 반환에 없어도 빈 배열로 취급(하위호환)
    class Cap2:
        usage = SimpleNamespace(chat_tokens=0)
        def chat_json(self, *a, **k):
            return {"needs_repair": False, "spans": [], "reason": "x"}   # motif_spans 필드 없음
    out2 = sj.judge_style(Cap2(), CH1, motif_candidates=[MOTIF_PHRASE])
    ok &= (out2 is not None and out2["motif_spans"] == [])
    print(f"[{'OK' if ok else 'FAIL'}] 1b: 자연스러우면 motif_spans 빈 배열·필드 없어도 빈 배열")
    return bool(ok)


# ═════════════════ ② 모티프 인자 미전달 = 기존 동작(문말만·바이트 동일) ═════════════════

def test_no_motif_candidates_byte_identical_prompt() -> bool:
    """2) 모티프 후보 미전달(None/빈) → 프롬프트가 HZ-1 문말 전용과 바이트 동일(하위호환)."""
    caps = []

    def _cap():
        class Cap:
            usage = SimpleNamespace(chat_tokens=0)
            def chat_json(self, messages, *a, **k):
                caps.append("\n".join(m["content"] for m in messages))
                return {"needs_repair": False, "spans": [], "reason": ""}
        return Cap()
    # (a) 미전달  (b) 명시 None  (c) 명시 빈 리스트 — 셋 다 문말 전용 프롬프트로 바이트 동일해야
    sj.judge_style(_cap(), CH1)
    sj.judge_style(_cap(), CH1, motif_candidates=None)
    sj.judge_style(_cap(), CH1, motif_candidates=[])
    ok = (caps[0] == caps[1] == caps[2])                       # 셋 다 바이트 동일
    ok &= ("motif_spans" not in caps[0])                       # 모티프 지시 미포함(문말 전용)
    ok &= ("모티프 후보" not in caps[0])
    # 반환은 motif_spans 필드를 항상 포함(빈 배열)해 호출부 하위호환(키 접근 안전)
    out = sj.judge_style(_cap(), CH1)
    ok &= (out is not None and out["motif_spans"] == [])
    print(f"[{'OK' if ok else 'FAIL'}] 2: 모티프 미전달=문말 전용 프롬프트 바이트 동일")
    return bool(ok)


# ═════════════════ ③ N-3 게이트(확인=수술·미확인=드롭+skip) ═════════════════

def test_n3_gated_by_confirmed_motif() -> bool:
    """3) N-3 findings 가 판정 motif_spans 인용 위치로 게이트 — 확인=수술 대상·미확인=드롭+skip 기록."""
    s = Settings()
    n3in = _n3_findings(CH1)
    ok = (len(n3in) >= 2)                                      # CH1 에 모티프 2회 출현(탐지 원자료)
    # 판정이 첫 출현만 확인 → 그 창만 수술 대상, 나머지 미확인=드롭+skip.
    q_confirm = MOTIF_PHRASE                                   # 첫 출현이 매칭됨(find 첫 창)
    n3, skips = hd.select_n3_findings(CH1, n3in, _judge(motif_spans=[(q_confirm, "기계 반복")]), s)
    ok &= (len(n3) == 1)                                       # 확인된 1건만 수술
    f = n3[0]
    cs, ce = f["span"]["char_start"], f["span"]["char_end"]
    ok &= (CH1[cs:ce] == f["span"]["text"])                    # 좌표↔텍스트 정합
    ok &= (f["category"] == "N-3" and f["metric"]["source"] == "style_judge")
    ok &= (MOTIF_PHRASE in f["span"]["text"])                  # 확인된 모티프 구절
    # 미확인 나머지 출현은 드롭 + motif_unconfirmed 기록(은폐 금지)
    ok &= (any(sk["fallback"] == "motif_unconfirmed" for sk in skips))
    print(f"[{'OK' if ok else 'FAIL'}] 3: N-3 판정 인용 게이트(확인=수술·미확인=드롭+skip)")
    return bool(ok)


def test_n3_no_motif_spans_drops_all() -> bool:
    """3보강) 판정이 모티프를 하나도 확인 안 함(motif_spans 빈) → 검출 N-3 전부 드롭(no_motif 기록)."""
    s = Settings()
    n3in = _n3_findings(CH1)
    n3, skips = hd.select_n3_findings(CH1, n3in, _judge(motif_spans=[]), s)
    ok = (n3 == [])                                            # 수술 0
    ok &= (any(sk["fallback"] == "no_motif" for sk in skips))  # 투명 기록
    # 인용이 본문과 매칭 실패 → quote_unmatched 기록
    n3b, skb = hd.select_n3_findings(CH1, n3in, _judge(motif_spans=[("본문에 없는 모티프", "x")]), s)
    ok &= (n3b == [] and any(sk["fallback"] == "quote_unmatched" for sk in skb))
    print(f"[{'OK' if ok else 'FAIL'}] 3b: motif_spans 빈=전부 드롭(no_motif)·매칭 실패=quote_unmatched")
    return bool(ok)


def test_apply_style_judgment_recombination() -> bool:
    """3c) apply_style_judgment(alias apply_n4_judgment) — N-3 도 판정 게이트, N-5/N-6 자체 검출 불변."""
    s = Settings()
    ledger = hd.build_motif_ledger([{"chapter": 1, "text": CH1}, {"chapter": 2, "text": CH2}])
    findings = hd.detect_chapter(CH1, ledger=ledger)
    cats_in = {f["category"] for f in findings}
    ok = ("N-3" in cats_in)                                    # N-3 검출됨
    # 판정이 모티프 확인 → N-3 수술 대상 복귀(판정 인용 기반)
    rec, skips = hd.apply_style_judgment(
        CH1, findings, _judge(motif_spans=[(MOTIF_PHRASE, "반복")]), s)
    cats_out = {f["category"] for f in rec}
    n3_selected = [f for f in rec if f["category"] == "N-3"]
    ok &= (all(f["metric"].get("source") == "style_judge" for f in n3_selected))   # 판정 기반 선별
    # 판정이 모티프 미확인(motif_spans 빈) → N-3 수술 대상 0(자체 검출 버림)
    rec0, _ = hd.apply_style_judgment(CH1, findings, _judge(motif_spans=[]), s)
    ok &= (not any(f["category"] == "N-3" for f in rec0))
    # alias 동일성
    ok &= (hd.apply_n4_judgment is hd.apply_style_judgment)
    print(f"[{'OK' if ok else 'FAIL'}] 3c: apply_style_judgment N-3 게이트·alias 유지")
    return bool(ok)


# ═════════════════ ④ 사실 밀집 모티프 금기 ═════════════════

def test_n3_fact_dense_taboo() -> bool:
    """4) 사실 밀집 모티프 스팬(수치+엔티티≥4) → 수술 금기·fallback=fact_dense(G-B 기각 낭비 사전 제외)."""
    s = Settings()
    # 모티프 구절이 사실 밀집 문장 안에 있음: 철수+영희(엔티티 2)+3+45(수치 2)=4 >= 임계
    text = "철수와 영희가 3개의 검을 45도로 든 순간 빛줄기가 맥박쳤다."
    n3in = [{"category": "N-3", "severity": "S2",
             "span": {"char_start": 0, "char_end": len(text), "text": text},
             "metric": {"motif_key": "x"}}]
    q = text                                                  # 판정이 전체 문장을 모티프로 인용
    n3, skips = hd.select_n3_findings(text, n3in, _judge(motif_spans=[(q, "x")]), s,
                                      roster={"철수", "영희"})
    ok = (n3 == [])                                           # 금기 → 수리 대상 아님
    fd = [sk for sk in skips if sk["fallback"] == "fact_dense"]
    ok &= (len(fd) == 1 and fd[0]["changed"] is False and "사실 밀집" in (fd[0]["note"] or ""))
    print(f"[{'OK' if ok else 'FAIL'}] 4: 사실 밀집 모티프 금기(fact_dense 기록)")
    return bool(ok)


# ═════════════════ ⑤ humanize_motif_judgment OFF = 구 동작(N-3 무조건 통과) ═════════════════

def test_motif_judgment_off_passes_n3_unconditionally() -> bool:
    """5) humanize_motif_judgment OFF → N-3 무조건 통과(자체 검출 그대로·구 HZ-1 동작·바이트 동일)."""
    s_on = Settings()                                         # 기본 True
    s_off = s_on.model_copy(update={"humanize_motif_judgment": False})
    ok = (s_on.humanize_motif_judgment is True)              # 기본 ON
    ledger = hd.build_motif_ledger([{"chapter": 1, "text": CH1}, {"chapter": 2, "text": CH2}])
    findings = hd.detect_chapter(CH1, ledger=ledger)
    n3_detected = [f for f in findings if f["category"] == "N-3"]
    ok &= (len(n3_detected) >= 1)
    # OFF: 판정이 모티프 미확인(motif_spans 빈)이어도 N-3 자체 검출 전부 통과(구 동작).
    rec_off, skips_off = hd.apply_style_judgment(CH1, findings, _judge(motif_spans=[]), s_off)
    n3_off = [f for f in rec_off if f["category"] == "N-3"]
    ok &= (len(n3_off) == len(n3_detected))                  # 무조건 통과(개수 보존)
    # OFF 바이트 동일: 구현상 OFF 경로 산출 == 판정 None 을 넣은 구 apply(N-3 그대로) 와 동형
    ok &= (all(f["metric"].get("source") != "style_judge" for f in n3_off))   # 판정 미개입(자체 검출 metric 유지)
    ok &= (not any(sk["category"] == "N-3" for sk in skips_off))   # N-3 skip 기록 없음(게이트 미작동)
    # ON: 같은 입력이면 N-3 드롭(대조 — 게이트 작동 확인)
    rec_on, _ = hd.apply_style_judgment(CH1, findings, _judge(motif_spans=[]), s_on)
    ok &= (not any(f["category"] == "N-3" for f in rec_on))
    print(f"[{'OK' if ok else 'FAIL'}] 5: humanize_motif_judgment OFF=N-3 무조건 통과(구동작)")
    return bool(ok)


def test_motif_judgment_off_byte_identical_to_legacy() -> bool:
    """5보강) OFF 경로 산출이 HZ-1 구 로직(others=[f≠N-4]+N-4판정)과 바이트 동일(하위호환 회귀 방지)."""
    s_off = Settings().model_copy(update={"humanize_motif_judgment": False})
    ledger = hd.build_motif_ledger([{"chapter": 1, "text": CH1}, {"chapter": 2, "text": CH2}])
    findings = hd.detect_chapter(CH1, ledger=ledger)
    j = _judge(needs=False, motif_spans=[(MOTIF_PHRASE, "x")])   # 모티프 확인해도 OFF 면 무시
    rec_off, _ = hd.apply_style_judgment(CH1, findings, j, s_off)
    # 구 로직 재현: N-4 판정 대체 + 나머지(N-3 포함) 그대로
    n4, _ = hd.select_n4_findings(CH1, j, s_off)
    others = [f for f in findings if f.get("category") != "N-4"]
    others.sort(key=lambda f: ((f.get("span") or {}).get("char_start", 0), f.get("category", "")))
    legacy = n4 + others
    ok = (rec_off == legacy)                                  # 바이트 동일
    print(f"[{'OK' if ok else 'FAIL'}] 5b: OFF=구 HZ-1 로직 바이트 동일")
    return bool(ok)


# ═════════════════ ⑥ 판정 실패(None) 시 N-3 보수 스킵 ═════════════════

def test_n3_conservative_on_judge_failure() -> bool:
    """6) 판정 실패/스킵(judgment=None) → N-3 수술 스킵(judge_missing·보수·결측 정직)."""
    s = Settings()
    n3in = _n3_findings(CH1)
    ok = (len(n3in) >= 1)
    n3, skips = hd.select_n3_findings(CH1, n3in, None, s)
    ok &= (n3 == [])                                          # 판정 없음 → 수술 0(보수)
    ok &= (any(sk["fallback"] == "judge_missing" for sk in skips))
    # apply 경로에서도 동일(판정 None → N-3·N-4 둘 다 스킵)
    ledger = hd.build_motif_ledger([{"chapter": 1, "text": CH1}, {"chapter": 2, "text": CH2}])
    findings = hd.detect_chapter(CH1, ledger=ledger)
    rec, sk2 = hd.apply_style_judgment(CH1, findings, None, s)
    ok &= (not any(f["category"] == "N-3" for f in rec))      # 판정 실패 → N-3 수술 대상 0
    ok &= (any(x["fallback"] == "judge_missing" and x["category"] == "N-3" for x in sk2))
    print(f"[{'OK' if ok else 'FAIL'}] 6: 판정 실패 → N-3 보수 스킵(judge_missing)")
    return bool(ok)


# ═════════════════ ⑦ trace 에 motif_spans 포함 ═════════════════

def test_trace_includes_motif_spans() -> bool:
    """7) style_judgment trace(judgment 전문 저장)에 motif_spans 포함 — 확장 필드 자동 포함(GA-1 정합)."""
    # judge_style 반환(=trace 에 저장되는 _trace_style_judgment)이 motif_spans 를 항상 담는지.
    class Cap:
        usage = SimpleNamespace(chat_tokens=0)
        def chat_json(self, *a, **k):
            return {"needs_repair": True, "spans": [],
                    "motif_spans": [{"quote": MOTIF_PHRASE, "why": "반복"}], "reason": "x"}
    judgment = sj.judge_style(Cap(), CH1, motif_candidates=[MOTIF_PHRASE])
    ok = ("motif_spans" in judgment)                          # trace 저장 dict 에 필드 존재
    ok &= (judgment["motif_spans"] == [{"quote": MOTIF_PHRASE, "why": "반복"}])
    # GA-1 사이드카가 판정 전문(judgment)을 그대로 style_judgment 로 저장 — 소스 grep-lock(회귀 방지)
    import inspect
    from novelcopilot.engine import harness as hn
    src = inspect.getsource(hn.ChapterGenerator.generate)
    ok &= ('"style_judgment": _trace_style_judgment' in src)  # 판정 전문 저장(확장 필드 자동 포함)
    print(f"[{'OK' if ok else 'FAIL'}] 7: trace(style_judgment)에 motif_spans 포함(전문 저장)")
    return bool(ok)


# ═════════════════ motif_candidates_from_findings 헬퍼 ═════════════════

def test_motif_candidates_from_findings() -> bool:
    """보조) motif_candidates_from_findings — N-3 findings 표층 추출(중복 제거·상한·비-N3 제외)."""
    findings = [
        {"category": "N-3", "span": {"text": MOTIF_PHRASE}},
        {"category": "N-3", "span": {"text": MOTIF_PHRASE}},   # 중복 → 1회만
        {"category": "N-4", "span": {"text": "그는 갔다."}},   # 비-N3 제외
        {"category": "N-3", "span": {"text": "다른 모티프"}},
    ]
    cands = hd.motif_candidates_from_findings(findings)
    ok = (cands == [MOTIF_PHRASE, "다른 모티프"])              # 중복 제거·순서 보존·N-4 제외
    # 빈/None 안전
    ok &= (hd.motif_candidates_from_findings([]) == [])
    ok &= (hd.motif_candidates_from_findings(None) == [])
    print(f"[{'OK' if ok else 'FAIL'}] +: motif_candidates_from_findings(중복제거·N-4 제외)")
    return bool(ok)


def main() -> int:
    results = [
        test_judge_motif_reference_and_motif_spans(),
        test_judge_motif_spans_empty_when_natural(),
        test_no_motif_candidates_byte_identical_prompt(),
        test_n3_gated_by_confirmed_motif(),
        test_n3_no_motif_spans_drops_all(),
        test_apply_style_judgment_recombination(),
        test_n3_fact_dense_taboo(),
        test_motif_judgment_off_passes_n3_unconditionally(),
        test_motif_judgment_off_byte_identical_to_legacy(),
        test_n3_conservative_on_judge_failure(),
        test_trace_includes_motif_spans(),
        test_motif_candidates_from_findings(),
    ]
    print("\nHZ-2(모티프 판정 확장) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


def test_hz2_motif_judgment_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
