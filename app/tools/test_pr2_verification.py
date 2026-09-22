# -*- coding: utf-8 -*-
"""PR-2 검증 — 회차 통합 검증 리포트(SSOT) (LLM 0콜).

설계(design-pr1-pr2-product-gate.md §PR-2): FINALIZED/ESCALATED 확정 직후 결정론 집계가
ChapterRecord.verification(additive dict)을 만든다 — 캐넌·ai_tell 요약(정규식+Kiwi·인간 대역)·
재탕계수(엔진 이관)·분량·closing_device/hook_type 라벨·style_repairs·reader_feedback·
ending_contract_eval·claim_audit·gate(러너 실행 시만·자리). 축 결측 시 null 이 아니라 "미실행"
문자열 명시. ESCALATED 에도 advisory 축 대칭 계산(status 분기 밖). dead 함수
quality_gates.chapter_quality_report 는 이 집계로 흡수·삭제.

이 테스트:
  ⓐ 집계 완전성 — 전 축 키가 항상 존재(누락의 조용한 통과 차단)
  ⓑ 결측 정직 — 안 돈 축은 null 이 아니라 "미실행" 문자열(null 침묵 금지)
  ⓒ ESCALATED 대칭 — 검토 필요 회차도 verification 집계 + ai_tell 결정론 계산(경로 비대칭 수리)
  ⓓ 구 JSON 호환 — verification 결측 로드·additive(exclude_defaults 미출현·바이트 동일)
  ⓔ 재탕 엔진 이관 — engine.verification 재탕계수 == dp4_loop.retread_metrics(부품 승격·중복 소멸)
  ⓕ dead 흡수·삭제 — chapter_quality_report 는 quality_gates 에서 제거됨(살아남은 검출기는 유지)
  ⓖ 배선 통합 — copilot.generate_next_chapter FINALIZED/ESCALATED 둘 다 record.verification 채움
  ⓗ UI 렌더 — app.js verificationPanel 이 결측('미실행') 포함 데이터를 판정 라벨 없이 렌더할 가드 보유
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_pr2_verification.py
"""
from __future__ import annotations
import sys
import pathlib
import tempfile
from pathlib import Path

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from novelcopilot.engine.verification import build_verification, retread_coefficient, MISSING
from novelcopilot.domain.types import ChapterRecord, ChapterStatus, Violation, SignalGrade
from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.base import LLMProvider
import novelcopilot.worldgen.arc_planner as apmod


# 전 축 키(집계 완전성 계약) — 하나라도 빠지면 누락의 조용한 통과
# HM-1b(2026-07-13): humanize 축 추가 — Claude 윤문 before/after 계측(폐루프·설계 ⓓ). 개편 허용·집계 완전성 의도 보존.
# TM-1(2026-07-14): timing 축 추가 — 단계별 소요 시간 집계(usage_by_stage 시간판 대칭·미계측 시 MISSING).
# ST-14 FIX-5(2026-07-15): ending_runs 축 추가 — 무중단 동일 종결 키 run(형태 불문·대사 리셋·풍선 감시·advisory).
# SX-MIN(2026-07-15): world_reveal 축(SX-3 세계 노출 슬롯 존재 여부)·cold_read 축(SX-2 프로즈만 신규 독자 프로브) 추가.
# TL-1(2026-07-20): tell_lexicon 축 추가 — 따온 검증법(webnovel-writer 14범주 사전)의 한국어 이식,
#   기존 ai_tell 무사전 축과 병렬 A/B 대조(결정론·의존 0 → 결측 없음·advisory).
# TG-1(2026-08-07, VI-1): layout 축 추가 — 모바일 조판 계측(22자/줄·3줄 초과 문단·advisory).
# DG-1(2026-08-10): dialogue_ledger 축 추가 — 화자별 어체 분포(저장 원장 재집계·LLM 0·원장 없으면 MISSING).
# OV-5(2026-08-13): ontology 축 추가 — 캐논 갱신 건수+제안 스테이지 실패 표식(신작 2·3화 침묵 실패 갭 봉합).
_AXES = {"chapter", "status", "canon", "ai_tell", "retread", "length", "labels",
         "style_repairs", "ending_runs", "humanize", "reader_feedback", "ending_contract",
         "claim_audit", "timing", "gate", "world_reveal", "cold_read", "tell_lexicon", "layout",
         "dialogue_ledger", "ontology",
         "story_pass",   # SY-1 §5-bis ⓒ: 확정 스토리 커버리지 축(story 모드 외 회차='미적용' 문자열)
         "voice_leak",   # VL-1: 보이스·설정 언어 직역 누출 스윕(소스 미제공 시 MISSING)
         "ending_literal_leak"}  # XR-1/XR-13(2026-08-22~23): 결말 정본 문장의 '직접 복사' 대조 — 의역·효과 누출 불포함(관측 축·소스 미제공 시 MISSING)


def _no_null(obj) -> bool:
    """dict/list 안에 파이썬 None(=null)이 하나도 없는가 — 결측 정직(null 대신 '미실행') 불변식."""
    if obj is None:
        return False
    if isinstance(obj, dict):
        return all(_no_null(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_no_null(v) for v in obj)
    return True


# ---------- ⓐ 집계 완전성 ----------
def test_all_axes_present() -> None:
    r = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="본문 " * 200,
                      ai_tell={"comma_per_100": 1.0, "n_sent": 50}, closing_device="dialogue")
    v = build_verification(r, prev_texts=[], target_chars=5000)
    ok = (set(v.keys()) == _AXES)
    # 각 축이 값 dict 또는 MISSING 문자열(둘 다 '존재')
    ok &= all(v[k] is not None for k in _AXES)
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 집계 완전성: 전 축 키 존재(누락 조용한 통과 차단)")
    assert ok, f"축 키 불완전: {set(v.keys()) ^ _AXES}"


# ---------- ⓑ 결측 정직('미실행' 문자열, null 금지) ----------
def test_missing_axes_are_string_not_null() -> None:
    # ai_tell 빈·reader 빈·ec 빈 → 세 축 모두 MISSING 문자열, verification 어디에도 None 없음
    r = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="짧은 본문.")
    v = build_verification(r, prev_texts=[], target_chars=5000)
    ok = (v["ai_tell"] == MISSING and v["reader_feedback"] == MISSING
          and v["ending_contract"] == MISSING and v["gate"] == MISSING)
    ok &= (MISSING == "미실행")               # 결측 표식 문자열 계약
    ok &= _no_null(v)                          # verification 전체에 파이썬 None 부재(null 침묵 금지)
    # target 미상 → 분량 비교도 '미실행'(chars 는 실측)
    v2 = build_verification(r, prev_texts=[], target_chars=None)
    ok &= (v2["length"]["norm"] == MISSING and v2["length"]["ratio_to_norm"] == MISSING
           and v2["length"]["chars"] == len("짧은 본문."))
    ok &= _no_null(v2)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 결측 정직: 안 돈 축='미실행'(null 없음)·target 미상 비교 미실행")
    assert ok


# ---------- ⓑ-2 Kiwi 부품 부재 시 서브키만 미실행(종결/층위 결측 정직) ----------
def test_kiwi_missing_subkey_honest() -> None:
    r = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="본문.",
                      ai_tell={"comma_per_100": 1.0, "n_sent": 10})   # kiwi 서브키 없음
    v = build_verification(r, prev_texts=[], target_chars=5000)
    at = v["ai_tell"]
    ok = (at != MISSING and at["kiwi"] == MISSING)   # 정규식 축은 값·Kiwi 축만 미실행
    ok &= (at["comma_per_100"] == 1.0)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ-2 Kiwi 부품 부재: kiwi 서브키만 '미실행'(정규식 축은 값)")
    assert ok


# ---------- ⓒ ESCALATED 대칭 ----------
def test_escalated_symmetric_aggregation() -> None:
    # ESCALATED(하드 위반 1)에도 verification 집계 + 결정론 축(캐넌·재탕·분량·라벨)은 값이 실림
    r = ChapterRecord(chapter=4, status=ChapterStatus.ESCALATED, text="주인공이 걸었다. " * 60,
                      closing_device="object", hook_type="reveal", chapter_function="setup",
                      final_violations=[Violation(entity="a", kind="death_conflict",
                                                  grade=SignalGrade.DETERMINISTIC)],
                      ai_tell={"comma_per_100": 0.8, "sent_len_cv": 0.5, "n_sent": 60})
    v = build_verification(r, prev_texts=["주인공이 걸었다. " * 60], target_chars=5000)
    ok = (set(v.keys()) == _AXES and v["status"] == "ESCALATED")
    ok &= (v["canon"]["hard"] == 1 and v["canon"]["hard_zero"] is False)   # 캐넌 대칭 계산
    ok &= (v["ai_tell"] != MISSING and v["ai_tell"]["comma_per_100"] == 0.8)  # advisory 문체 축 대칭
    ok &= (v["retread"]["opening_coef"] > 0)                               # 재탕 대칭(선행 동일)
    ok &= (v["labels"]["closing_device"] == "object")                     # craft 라벨 대칭
    # LLM 요하는 축(독자·엔딩계약)은 ESCALATED 경로 미실행 → 정직 미실행(값 위조 금지)
    ok &= (v["reader_feedback"] == MISSING and v["ending_contract"] == MISSING)
    ok &= _no_null(v)
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ ESCALATED 대칭: 결정론 축 값·LLM 축 정직 미실행·null 없음")
    assert ok


# ---------- ⓓ 구 JSON 호환(additive) ----------
def test_old_json_additive() -> None:
    old = {"chapter": 7, "status": "FINALIZED", "title": "7화", "hook_type": "question"}
    rec = ChapterRecord.model_validate(old)
    ok = (rec.verification == {})                                # 결측 → 기본 빈 dict
    d = rec.model_dump(exclude_defaults=True)
    ok &= ("verification" not in d)                              # 미설정 → 재직렬화 미출현(구 JSON 바이트 동일)
    # 값 세팅 왕복(additive 필드 정상 동작)
    rec.verification = {"canon": {"hard": 0}}
    ok &= (ChapterRecord.model_validate(rec.model_dump()).verification == {"canon": {"hard": 0}})
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ 구 JSON 호환: verification 결측=기본{{}}·additive·미출현(바이트 동일)")
    assert ok


# ---------- ⓔ 재탕 엔진 이관 parity ----------
def test_retread_engine_parity() -> None:
    from tools.dp4_loop import retread_metrics
    chs = [{"chapter": 1, "text": "게이트 던전 마수 공략 " * 30},
           {"chapter": 2, "text": "도시 동료 계약 만남 " * 30},
           {"chapter": 3, "text": "게이트 던전 마수 공략 " * 30}]   # ch3 재탕 ch1
    rm = retread_metrics(chs)
    ok = True
    for i, c in enumerate(chs):
        prev = [chs[j]["text"] for j in range(i)]
        rc = retread_coefficient(c["text"], prev)
        if i == 0:
            ok &= (rc == MISSING)                               # 1화=선행 없음=재탕 대조 미실행(결측 정직)
            continue
        ok &= (rc["opening_coef"] == rm[i]["opening_coef"]
               and rc["opening_jac"] == rm[i]["opening_jac"]
               and rc["retread_flag"] == rm[i]["retread_flag"])
        if rc["opening_with_index"] == MISSING:
            ok &= (rm[i]["opening_with"] is None)               # 무겹침: 엔진 '미실행' ↔ dp4 None(대응)
        else:
            ok &= (chs[rc["opening_with_index"]]["chapter"] == rm[i]["opening_with"])
    ok &= (rm[2]["retread_flag"] is True)                        # ch3 재탕 flag(캘리브레이션 유지)
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 재탕 엔진 이관: engine==dp4 parity(부품 승격·중복 소멸)")
    assert ok


# ---------- ⓕ dead 흡수·삭제 ----------
def test_dead_report_deleted() -> None:
    import novelcopilot.engine.quality_gates as qg
    ok = (not hasattr(qg, "chapter_quality_report"))            # dead 번들러 제거
    # 살아남은 검출기는 유지(실측 부품 — 삭제 금지)
    for fn in ("word_tics", "ai_tell_profile", "past_tense_run", "fragment_ratio", "tense_leak_ratio"):
        ok &= hasattr(qg, fn)
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ dead 흡수·삭제: chapter_quality_report 제거·검출기 유지")
    assert ok


# ---------- ⓖ 배선 통합(copilot generate → record.verification) ----------
class _Fake(LLMProvider):
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _spine() -> NarrativeSpine:
    return NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="c1",
                    target_chapters=3, event_menu=["사건1"])])])


def _mk_svc(pid: str, with_spine: bool = True):
    tmp = tempfile.mkdtemp()
    s = get_settings().model_copy(update={"data_dir": tmp})
    svc = CopilotService(s, FilesystemProjectRepository(Path(tmp)))
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    if with_spine:
        w.spine = _spine()
    st = ProjectState(id=pid, seed=ProjectSeed(target_chapters=6), world=w, created_at="t")
    svc.repo.save(st)
    sess, _ = svc.get_session(pid)
    sess.provider = _Fake()
    return svc, sess


def _stub_beat():
    orig = apmod.ArcPlanner.beat_for_episode
    apmod.ArcPlanner.beat_for_episode = lambda self, world, arc, ep, ch, fin, rec, direc, plant_notes="", **kw: \
        Beat(chapter=ch, entities=["hero"], arc_id=ep.arc_id, episode_id=ep.episode_id,
             closing_device="dialogue", hook_type="question", chapter_function="setup")
    return orig


def test_wiring_finalized_populates_verification() -> None:
    svc, sess = _mk_svc("pr2f")
    sess.bundle.updater.propose = lambda *a, **k: {}
    sess.bundle.updater.apply = lambda *a, **k: ([], [], [], [])
    orig = _stub_beat()
    try:
        sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
            ChapterRecord(chapter=ch_no, status=ChapterStatus.FINALIZED, text="본문 문장. " * 80,
                          summary="s", closing_device="dialogue", hook_type="question",
                          chapter_function="setup")
        res = svc.generate_next_chapter("pr2f")
    finally:
        apmod.ArcPlanner.beat_for_episode = orig
    v = res["record"].verification
    ok = (set(v.keys()) == _AXES and v["status"] == "FINALIZED"
          and v["labels"]["closing_device"] == "dialogue"
          and v["canon"]["hard_zero"] is True
          and v["ai_tell"] != MISSING              # FINALIZED 경로가 ai_tell 채움 → 요약도 값
          and _no_null(v))
    # 디스크 영속 확인(SSOT 저장)
    st2 = svc.get_project("pr2f")
    ok &= (st2.chapters and st2.chapters[-1].verification.get("status") == "FINALIZED")
    print(f"[{'OK' if ok else 'FAIL'}] ⓖ 배선: FINALIZED generate → record.verification 채움·영속")
    assert ok


def test_wiring_escalated_populates_verification() -> None:
    svc, sess = _mk_svc("pr2e")
    orig = _stub_beat()
    try:
        # 빈 본문 → harness 가 아니라 여기선 스텁이 ESCALATED 직접 반환(빈 본문·recovery)
        sess.bundle.generator.generate = lambda ch_no, beat, ont, rag, wiki, **kw: \
            ChapterRecord(chapter=ch_no, status=ChapterStatus.ESCALATED, text="검토 본문. " * 40,
                          closing_device="object",
                          final_violations=[Violation(entity="hero", kind="state_conflict",
                                                       grade=SignalGrade.DETERMINISTIC)])
        res = svc.generate_next_chapter("pr2e")
    finally:
        apmod.ArcPlanner.beat_for_episode = orig
    v = res["record"].verification
    # ESCALATED 는 디스크 미영속(B-25)이나 반환 페이로드 record 엔 verification 이 실려야(가시화 대칭)
    ok = (set(v.keys()) == _AXES and v["status"] == "ESCALATED"
          and v["canon"]["hard"] == 1
          and v["ai_tell"] != MISSING              # 대칭 계산(status 분기 밖)
          and v["reader_feedback"] == MISSING      # LLM 축은 정직 미실행
          and _no_null(v))
    print(f"[{'OK' if ok else 'FAIL'}] ⓖ 배선: ESCALATED generate → record.verification 대칭 채움")
    assert ok


# ---------- ⓗ UI 렌더 가드(app.js verificationPanel) ----------
# ---------- ⓘ ST-14 FIX-5 (무중단 종결 키 run 축 SSOT) ----------
def test_ending_runs_axis_present_and_detects_wall() -> None:
    """ending_runs 축이 무중단 동일 종결 키 run(형태 불문·대사 리셋)을 집계 — 현재형 'ㄴ다' 벽 검출·advisory."""
    # 현재형 'ㄴ다' 6연속 벽('ᆫ다' 단일 키) — 과거형 축이 못 보던 벽(FIX-1)이 SSOT 에 표면화
    wall = ("그는 문을 연다. 계단을 내려간다. 손전등을 켠다. 어둠이 물러난다. 발을 디딘다. 앞으로 나아간다.")
    r = ChapterRecord(chapter=5, status=ChapterStatus.FINALIZED, text=wall)
    v = build_verification(r, prev_texts=[], target_chars=5000)
    er = v["ending_runs"]
    ok = True
    if er == MISSING:
        # Kiwi/tools 부재 환경 — 결측 정직(MISSING 문자열, null 아님). 그 자체로 계약 통과.
        ok &= (er == "미실행")
    else:
        ok &= (er["max_run"] >= 6 and er["count"] >= 1 and er["threshold"] == 6)
        ok &= (er["runs"] and er["runs"][0]["n_sent"] >= 6 and bool(er["runs"][0]["key"]))
        ok &= _no_null(v)   # ending_runs 하위에 None 없음(결측 정직)
    # 벽 없는 짧은 본문 → max_run 0(측정했으나 벽 없음 — MISSING 과 구분) 또는 부품 부재 시 MISSING
    r2 = ChapterRecord(chapter=6, status=ChapterStatus.FINALIZED, text="짧은 한 문장이다.")
    er2 = build_verification(r2, prev_texts=[], target_chars=5000)["ending_runs"]
    ok &= (er2 == MISSING or (er2["max_run"] == 0 and er2["count"] == 0))
    print(f"[{'OK' if ok else 'FAIL'}] ⓘ ending_runs 축: 무중단 종결 키 run 집계(ㄴ다 벽 검출·결측 정직)")
    assert ok


def test_ui_panel_guards_present() -> None:
    js = (_HERE.parent / "novelcopilot" / "web" / "app.js").read_text(encoding="utf-8")
    ok = ("function verificationPanel(" in js)
    ok &= ("verificationPanel(c)" in js)              # 회차 상세 렌더에 배선됨
    ok &= ('const V_MISSING = "미실행"' in js)         # 결측 표식(엔진 MISSING 과 동일 문자열)
    ok &= ("v.ending_runs" in js)                     # FIX-5: 무중단 종결 run 축 패널 행 배선
    ok &= ("미실행" in js and "if(!v||!Object.keys(v).length) return" in js)  # 구 회차 가드
    # 판정 라벨·색상 경고 부재(무강제) — 검증 패널 함수 영역에 판정색 클래스(bad/warn/ok)·PASS/FAIL 미사용.
    #   (설명문의 '단일 판정 없음'·'코드 판정 아님' 같은 무판정 선언 문구는 허용 — 부정 서술이므로 별도 검사 안 함)
    start = js.index("function verificationPanel(")
    end = js.index("async function loadSpine(", start)
    panel = js[start:end]
    ok &= not any(tok in panel for tok in ('class="bad', 'class="warn', 'class="ok', "PASS", "FAIL"))
    print(f"[{'OK' if ok else 'FAIL'}] ⓗ UI 렌더: verificationPanel 존재·배선·미실행 가드·무판정색")
    assert ok


_TESTS = [
    test_all_axes_present, test_missing_axes_are_string_not_null, test_kiwi_missing_subkey_honest,
    test_escalated_symmetric_aggregation, test_old_json_additive, test_retread_engine_parity,
    test_dead_report_deleted, test_wiring_finalized_populates_verification,
    test_wiring_escalated_populates_verification, test_ending_runs_axis_present_and_detects_wall,
    test_ui_panel_guards_present,
]


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    results = []
    for t in _TESTS:
        try:
            t(); results.append(True)
        except AssertionError:
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
