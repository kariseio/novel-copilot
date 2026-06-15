# -*- coding: utf-8 -*-
"""3개월차 1차분 검증 — G9 회차 종결 마커 정리 / G3 페이싱 텔레메트리(측정·가시화). LLM 0콜.
실행: PYTHONPATH=app python tools/test_month3a_g9_g3.py
"""
from __future__ import annotations
import inspect
import sys

from novelcopilot.engine.harness import sanitize_meta, ChapterGenerator
from novelcopilot.engine.pacing import pacing_window
from novelcopilot.domain.types import ChapterRecord, ChapterStatus, OntologyChange
from novelcopilot.domain.ledger import PromiseLedger


# ---------- G9: 회차 종결 마커 누출 정리(메타 sanitize 범주) ----------
def test_sanitize_closing() -> bool:
    txt = "\n".join([
        "그는 검을 들었다.",
        "(다음 회에서 계속)",                      # 괄호 단독행 — 제거
        "(다음 회, 새로운 적이 나타난다)",          # 괄호 단독행 — 제거
        "[다음 화] 예고편",                        # 줄머리 메타 — 제거
        "to be continued",                         # 영문 — 제거
        "다음 회의가 끝나고 그는 일어섰다.",         # prose — 유지(줄머리 '다음 회의', 괄호 아님)
        "그는 다음 화를 기약했다.",                 # prose — 유지(줄머리 '그는')
    ])
    out = sanitize_meta(txt)
    ok = ("그는 검을 들었다." in out
          and "다음 회에서 계속" not in out and "새로운 적이 나타난다" not in out
          and "예고편" not in out and "to be continued" not in out
          and "다음 회의가 끝나고 그는 일어섰다." in out          # 오탐 없음(회의)
          and "그는 다음 화를 기약했다." in out)                  # 오탐 없음(줄머리 그는)
    # _continue 가 key_events 컨텍스트를 받는다(G9 — 계획 사건 참고 주입)
    ok &= ("key_events" in inspect.signature(ChapterGenerator._continue).parameters)
    print(f"[{'OK' if ok else 'FAIL'}] 종결마커 정리: 메타 제거·prose 유지(오탐0)·_continue key_events 주입")
    return ok


# ---------- G3: 페이싱 텔레메트리(롤링 윈도, 측정만) ----------
def test_pacing_window() -> bool:
    chs = [
        ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, hook_type="action", place="길드",
                      time_advance="다음날",
                      ontology_changes=[OntologyChange(op="new_entity", entity="A", detail="", applied=True)]),
        ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, hook_type="action", place="길드",
                      time_advance="없음"),
        ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, hook_type="reveal", place="던전",
                      time_advance="사흘 후",
                      ontology_changes=[OntologyChange(op="new_entity", entity="B", detail="", applied=True),
                                        OntologyChange(op="state_change", entity="A", detail="", applied=True)]),
        ChapterRecord(chapter=4, status=ChapterStatus.ESCALATED, hook_type="x", place="y"),   # 비FINALIZED 제외
    ]
    led = PromiseLedger()
    pw = pacing_window(chs, led, 5, window=5)
    ok = (pw["window"] == 3                                  # FINALIZED만
          and pw["hooks"] == ["action", "action", "reveal"]  # 원시 라벨(분류 없음)
          and pw["hook_monotony"] == round(2 / 3, 2)         # 최빈 비율(집계만)
          and pw["places_distinct"] == 2
          and pw["times"] == ["다음날", "없음", "사흘 후"]    # 원시 시간 라벨(키워드 분류 안 함)
          and pw["new_names"] == 2                            # 신규 커밋 합(인플레)
          and pw["since_payoff"] is None)                     # 지불 이력 없음
    print(f"[{'OK' if ok else 'FAIL'}] 페이싱 윈도: FINALIZED만·원시 신호+집계(hook_monotony {pw['hook_monotony']}, new_names {pw['new_names']})·키워드분류 없음")
    return ok


if __name__ == "__main__":
    results = [test_sanitize_closing(), test_pacing_window()]
    print("\n3개월차 1차분(G9/G3-tel) 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
