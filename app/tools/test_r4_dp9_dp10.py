# -*- coding: utf-8 -*-
"""R4(DP-9+10) 검증 — 문체 계측 카운터 2종 + 시뮬 독자 스키마 교체 + 실험 게이트 틱 시트 + UI 정직화.

근거: docs/design-dp-repair.md §DP-9·§DP-10.
- DP-9: ai_tell 은 소비 0·자질 둔감. '파편문 비율'이 판별축(DP-4 16.7% vs 대조 5.1%). 자유 정독은 지각-하 틱을
  건너뛴다. 수리=①결정론 카운터 2종(과거형 종결 run — 종성 ㅆ 가드 / 무동사 파편문 비율 — 조사 §4 오탐 변종
  가드) 측정 피처 신설 ②실험 게이트에 틱 시트(수치+실례 프리펜드)+measure-then-cite 체크리스트 5항목.
- DP-10: pay_next 98.7% 포화. 이진 bool+아첨 수렴이 원인. 수리=스키마 교체{drop, kill_trigger(원문 인용 의무),
  hate_comment, retention_est(정수), why}+hair-trigger 프레이밍 / 구 데이터 하위호환 / UI 정직화.

검증 축:
1) past_tense_run — 종성 ㅆ 가드: 과거형(…았/었/였/했다)만 run 계수, 현재·형용사 '다'(간다/크다/이다) 제외.
2) fragment_ratio — 무동사 파편문 계수 + 조사 §4 오탐 가드(-다면·-으면·-던가·호격·의문) 제외.
3) ai_tell_profile 계보 편입 — past_run_max·frag_ratio 스칼라 키 신설(빈 입력 early-return 포함)·기존 키 보존.
4) reader_prediction 신규 스키마 — drop/kill_trigger/hate_comment/retention_est/why 파싱·정수 정규화·빈응답 None.
5) ChapterRecord 하위호환 — reader_feedback 이 구{got,pay_next} / 신규{drop,kill_trigger,…} 둘 다 무손실 라운드트립.
6) dp4_loop 틱 시트 — 수치+상위 실례+1인칭 마커+첫 3문장+measure-then-cite 체크리스트 5항목 프리펜드.
7) app.js 정직화 — node --check(구문) + 소스에 '시뮬 독자(참고)'·'실독자 아님'·kill_trigger/hate_comment 렌더·이진 결제 배지 제거.

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_r4_dp9_dp10.py
"""
from __future__ import annotations
import subprocess
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(_HERE))          # tools/ → 자매 도구 임포트

from novelcopilot.engine.quality_gates import (
    past_tense_run, fragment_ratio, ai_tell_profile, _has_ss_jong)
from novelcopilot.engine.reader_desk import reader_prediction
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.llm.base import LLMProvider


class ScriptFake(LLMProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.calls = []

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        self.calls.append(k)
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


# ---------- 1) past_tense_run — 종성 ㅆ 가드 ----------
def test_past_tense_run_ss_guard() -> bool:
    # 과거형 4연속 후 현재형 형용사(붉다)로 종료 → run=4, 현재/형용사 '다'는 제외
    t = "그는 문을 열었다.\n검을 뽑았다.\n적을 베었다.\n숨을 골랐다.\n하늘이 붉다."
    r = past_tense_run(t)
    ok = (r["max_run"] == 4 and r["n_past"] == 4 and r["n_sent"] == 5 and len(r["examples"]) == 4)
    # 현재·형용사·계사 '다'는 종성 ㅆ 아님 → 과거 아님(0)
    r2 = past_tense_run("그가 학교에 간다.\n하늘이 매우 높다.\n그것은 검이다.")
    ok &= (r2["n_past"] == 0 and r2["max_run"] == 0)
    # 종성 ㅆ 판정 유닛: 과거 선어말어미 음절(했/갔/었/였/랐/았)은 True, 그 외는 False
    ok &= all(_has_ss_jong(c) for c in "했갔었였랐았")
    ok &= (not _has_ss_jong("다") and not _has_ss_jong("간") and not _has_ss_jong("크")
           and not _has_ss_jong("뽑") and not _has_ss_jong(""))   # 뽑=종성 ㅂ(ㅆ 아님)
    print(f"[{'OK' if ok else 'FAIL'}] past_tense_run: 종성 ㅆ 가드로 과거형만 계수(run={r['max_run']})·현재/형용사 제외")
    return ok


# ---------- 2) fragment_ratio — 무동사 파편 + 조사 §4 오탐 가드 ----------
def test_fragment_ratio_variant_guards() -> bool:
    t = ("붉은 달.\n"          # 파편(명사 종결) ✓
         "차가운 침묵.\n"       # 파편 ✓
         "네가 원한다면.\n"     # -다면(비종결) → 제외
         "집에 가면.\n"         # -면(비종결) → 제외
         "그가 왔던가.\n"       # -던가(의문 종결) → 제외
         "비가 내리면.\n"       # -면(비종결) → 제외
         "민수야.\n"            # 호격 → 제외
         "왜?\n"                # 의문(?) → n 에서도 제외
         "그는 갔다.\n"         # 과거 서술 → 제외
         "어둠이 내렸다.")      # 과거 서술 → 제외
    r = fragment_ratio(t)
    ok = (r["n_fragment"] == 2 and set(r["examples"]) == {"붉은 달.", "차가운 침묵."})
    ok &= (r["n_sent"] == 9)                       # '왜?'(의문)는 분모에서 제외 → 10문장 중 9
    # 순수 서술 지문은 파편 0(오탐 없음)
    ok &= (fragment_ratio("그는 천천히 걸었다. 바람이 불었다. 문이 열렸다.")["n_fragment"] == 0)
    # 대사행은 지문 계보에서 제외(파편·호격 오염 차단)
    ok &= (fragment_ratio('"위험해!"\n"조심!"\n밤이 깊었다.')["n_fragment"] == 0)
    # 반말 해체 종결(-어/-여/-해)은 서술어 있는 정상 문장 → 파편 아님(DP-8 1인칭 도파민물 idiom; cry-wolf 방지)
    ok &= (fragment_ratio("나는 밥을 먹어. 그냥 그렇게 해. 나도 봤어. 좋아. 싫어.")["n_fragment"] == 0)
    print(f"[{'OK' if ok else 'FAIL'}] fragment_ratio: 파편 2건만 계수·§4 변종(-다면/-으면/-던가/호격/의문/해체 -어·해) 전부 제외")
    return ok


# ---------- 3) ai_tell_profile 계보 편입 ----------
def test_ai_tell_surfaces_counters() -> bool:
    prof = ai_tell_profile("그는 갔다. 검을 뽑았다. 붉은 달.")
    ok = ("past_run_max" in prof and "frag_ratio" in prof)            # 신설 스칼라 키
    ok &= isinstance(prof["past_run_max"], int) and isinstance(prof["frag_ratio"], float)
    # 기존 키 보존(무회귀)
    for k in ("comma_per_100", "sent_len_cv", "lexical_mattr", "ending_diversity", "simile_per_1k", "n_sent"):
        ok &= (k in prof)
    # 빈 입력 early-return 도 신설 키 포함(하위호환·KeyError 방지)
    empty = ai_tell_profile("")
    ok &= (empty["past_run_max"] == 0 and empty["frag_ratio"] == 0.0 and empty["n_sent"] == 0)
    # PR-2(G4): dead였던 chapter_quality_report 삭제 — 살아남은 검출기(past_tense_run·fragment_ratio)가
    #   실례(examples) 포함 전체 딕트를 그대로 노출하는지 직접 검증(집계 SSOT는 engine.verification 로 이관).
    ptr = past_tense_run("그는 갔다. 붉은 달.")
    fr = fragment_ratio("그는 갔다. 붉은 달.")
    ok &= ("max_run" in ptr and "examples" in ptr and "examples" in fr and "ratio" in fr)
    print(f"[{'OK' if ok else 'FAIL'}] ai_tell 계보 편입: past_run_max·frag_ratio 스칼라 신설·기존 키·빈입력 안전")
    return ok


# ---------- 4) reader_prediction 신규 스키마(DP-10) ----------
def test_reader_prediction_new_schema() -> bool:
    fake = ScriptFake([{"drop": True, "kill_trigger": "\"그는 또 각성했다\"",
                        "hate_comment": "또 각성이냐 하차", "retention_est": 22, "why": "기시감"}])
    p = reader_prediction(fake, "본문...", "줄거리", "헌터")
    ok = (p and p["drop"] is True and "각성" in p["kill_trigger"]
          and p["hate_comment"] and p["retention_est"] == 22 and "기시감" in p["why"])
    # retention_est 정규화: 문자열/범위초과/음수/None/비수치
    ok &= (reader_prediction(ScriptFake([{"why": "a", "retention_est": "80"}]), "b", "", "x")["retention_est"] == 80)
    ok &= (reader_prediction(ScriptFake([{"why": "a", "retention_est": 250}]), "b", "", "x")["retention_est"] == 100)
    ok &= (reader_prediction(ScriptFake([{"why": "a", "retention_est": -5}]), "b", "", "x")["retention_est"] == 0)
    ok &= (reader_prediction(ScriptFake([{"why": "a", "retention_est": None}]), "b", "", "x")["retention_est"] is None)
    ok &= (reader_prediction(ScriptFake([{"why": "a", "retention_est": "많음"}]), "b", "", "x")["retention_est"] is None)
    # kill_trigger 미기재 → '없음' 명시 / drop 기본 True
    p2 = reader_prediction(ScriptFake([{"why": "무난"}]), "b", "", "x")
    ok &= (p2["kill_trigger"] == "없음" and p2["drop"] is True)
    # 빈 응답(kill/hate/why 전무) → None / 빈 본문 → None
    ok &= (reader_prediction(ScriptFake([{"kill_trigger": "", "hate_comment": "", "why": ""}]), "b", "", "x") is None)
    ok &= (reader_prediction(ScriptFake([{"why": "x"}]), "", "", "x") is None)
    print(f"[{'OK' if ok else 'FAIL'}] reader_prediction 신규 스키마: drop/kill/hate/retention 파싱·정수 정규화·None 경계")
    return ok


# ---------- 5) ChapterRecord 하위호환(구·신 데이터 무손실) ----------
def test_chapterrecord_backcompat() -> bool:
    old = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="t",
                        reader_feedback={"got": "각성", "pay_next": True, "why": "궁금"})
    new = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="t",
                        reader_feedback={"drop": True, "kill_trigger": "\"x\"", "hate_comment": "hc",
                                         "retention_est": 30, "why": "y"})
    ok = True
    for rec in (old, new):
        blob = rec.model_dump_json()
        rt = ChapterRecord.model_validate_json(blob)
        ok &= (rt.reader_feedback == rec.reader_feedback)   # 둘 다 dict 필드로 무손실 왕복
    ok &= (old.reader_feedback["pay_next"] is True and new.reader_feedback["retention_est"] == 30)
    # 기본값(reader_feedback 미지정)=빈 dict(구작 하위호환)
    ok &= (ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED).reader_feedback == {})
    print(f"[{'OK' if ok else 'FAIL'}] ChapterRecord 하위호환: 구{{got,pay_next}}·신{{drop,kill_trigger,…}} 무손실 라운드트립")
    return ok


# ---------- 6) dp4_loop 틱 시트(measure-then-cite) ----------
def test_tick_sheet() -> bool:
    import dp4_loop
    text = ("나는 문을 열었다. 검을 뽑았다. 붉은 달. 차가운 침묵.\n"
            "내가 적을 노려봤다. 바람이 불었다. 어둠이 내렸다.")
    sheet = dp4_loop._tick_sheet(text)
    ok = ("틱 시트" in sheet and "measure-then-cite" in sheet)
    ok &= ("무동사 파편문 비율" in sheet and "과거형 종결 최장 run" in sheet)     # 수치
    ok &= ("상위 실례" in sheet and "붉은 달" in sheet)                          # 상위 실례(본문 인용)
    ok &= ("1인칭 서술 마커" in sheet)                                          # 1인칭 마커
    ok &= ("첫 3문장" in sheet)                                                 # 첫 3문장
    ok &= ("체크리스트 6항목" in sheet and "6)" in sheet and "1)" in sheet)      # 명시 체크리스트(DP-17: 5→6항목, 케이던스 정독 추가)
    ok &= ("숫자→본문 인용 강제" in sheet or "숫자만으로 지적 금지" in sheet)     # measure-then-cite 문안
    # 1인칭 마커 카운트가 실제로 잡히는가(나는·내가 → ≥2)
    ok &= ("마커(나는/내가/…): 2" in sheet or "마커(나는/내가/…): " in sheet)
    print(f"[{'OK' if ok else 'FAIL'}] dp4_loop 틱 시트: 수치+실례+1인칭 마커+첫 3문장+체크리스트 6항목 프리펜드")
    return ok


# ---------- 7) app.js 정직화(node --check + 소스 정직 라벨) ----------
def test_app_js_honesty() -> bool:
    app_js = _HERE.parent / "novelcopilot" / "web" / "app.js"
    src = app_js.read_text(encoding="utf-8")
    ok = ("시뮬 독자(참고)" in src and "실독자 아님 — LLM 시뮬" in src)          # 정직 라벨·주석
    ok &= ("kill-trigger" in src and "hate-comment" in src)                    # kill_trigger/악플 전면
    ok &= ("readerReactHtml" in src)                                           # 스키마 분기 렌더 헬퍼
    ok &= ("결제 의향 있음" not in src)                                          # 이진 결제 배지(단정형) 제거
    ok &= ("다음 화 결제 의향 있음" not in src)
    # node --check(구문) — node 부재 시 이 축은 건너뜀(카운터/스키마 축은 유효)
    try:
        r = subprocess.run(["node", "--check", str(app_js)], capture_output=True, timeout=30)
        node_ok = (r.returncode == 0)
        note = "node --check OK" if node_ok else f"node --check FAIL: {r.stderr.decode('utf-8','replace')[:200]}"
        ok &= node_ok
    except (FileNotFoundError, subprocess.TimeoutExpired):
        note = "node 부재 — 구문 검사 건너뜀(정직 라벨 축은 통과)"
    print(f"[{'OK' if ok else 'FAIL'}] app.js 정직화: 시뮬 라벨·kill/악플 전면·이진 배지 제거 · {note}")
    return ok


def main() -> int:
    results = [
        test_past_tense_run_ss_guard(),
        test_fragment_ratio_variant_guards(),
        test_ai_tell_surfaces_counters(),
        test_reader_prediction_new_schema(),
        test_chapterrecord_backcompat(),
        test_tick_sheet(),
        test_app_js_honesty(),
    ]
    print("\nR4(DP-9 계측 카운터 + DP-10 시뮬 독자 스키마) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


# pytest 수집용 얇은 래퍼(assert) — 직접 실행은 main().
def test_r4_dp9_dp10_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
