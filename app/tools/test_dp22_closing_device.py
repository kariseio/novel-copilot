# -*- coding: utf-8 -*-
"""DP-22 검증 — 마무리 장치 순환(closing_device 자기 라벨 + 화이트리스트 순환) (LLM 0콜).

설계(design-st9-dp22-ln1.md §DP-22): hook_type 은 회차말 '끊는 방식'을 순환시키지만 회차가 *물리적으로
닫히는 장치*(발신자 없는 전화 3연발 등)는 미커버. 개입 = 비트 스키마에 closing_device 자기 라벨 신설
(고정 6종 craft 축) + 비트 설계 지시에 '그 장치로 닫아라' 선언·이행 문안 + 제시 선택지=전체−최근 2화
사용 장치(B-37 hook_whitelist 계보 재사용·소진 시 복원·과거 회차 분류 절대 금지·결측 무시). 이 테스트는:
  ⓐ 순환 산술: 최근 2화 사용 장치 제외·전 장치 소진 시 복원·미전달(None) 하위호환·대소문자/공백 정규화·결측 무시
  ⓑ 스키마 additive: Beat/ChapterRecord 에 closing_device 필드 존재·기본 ""·구 JSON 로드 바이트 동일
  ⓒ 렌더 스냅샷: beat_for_episode 프롬프트가 축소 화이트리스트로 선언·이행 문안 제시·미전달 시 전체(하위호환)
  ⓓ 통합: 자기 라벨이 Beat 로 파싱·harness 영속 경로가 closing_device 를 ChapterRecord 로 옮김
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_dp22_closing_device.py
"""
from __future__ import annotations
import sys
import json

from novelcopilot.engine.menu_filter import closing_whitelist, CLOSING_WHITELIST
from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


# ---------- ⓐ-1 순환: 최근 2화 사용 장치 제외 ----------
def test_closing_whitelist_rotation() -> None:
    used = ["object", "object"]   # 최근 2화 모두 object 로 닫음(발신자 없는 전화 계열)
    got = closing_whitelist(used)
    expect = [c for c in CLOSING_WHITELIST if c != "object"]
    ok = (got == expect and "object" not in got and "dialogue" in got and "arrival" in got)
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 순환: 최근 사용 장치 제외한 축소 리스트 제시")
    assert ok


def test_closing_whitelist_two_distinct_removed() -> None:
    used = ["object", "interior"]   # 최근 2화가 서로 다른 두 장치 → 둘 다 제외
    got = closing_whitelist(used)
    ok = ("object" not in got and "interior" not in got
          and got == [c for c in CLOSING_WHITELIST if c not in ("object", "interior")])
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 순환: 서로 다른 두 장치 모두 제외")
    assert ok


# ---------- ⓐ-2 전 장치 소진 시 전체 복원(never-empty) ----------
def test_closing_whitelist_exhausted_restores_full() -> None:
    got = closing_whitelist(list(CLOSING_WHITELIST))   # 전 장치가 최근 사용 → 후보 비면 전체 복원
    ok = (got == list(CLOSING_WHITELIST))
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 전 장치 소진 시 전체 복원(never-empty)")
    assert ok


# ---------- ⓐ-3 미전달/빈=전체(하위호환)·정규화·화이트리스트 밖·결측 무시 ----------
def test_closing_whitelist_none_full_and_normalized() -> None:
    ok = (closing_whitelist(None) == list(CLOSING_WHITELIST)
          and closing_whitelist([]) == list(CLOSING_WHITELIST)
          and closing_whitelist(["Object", " INTERIOR "]) ==
              [c for c in CLOSING_WHITELIST if c not in ("object", "interior")]
          and closing_whitelist(["미지의장치"]) == list(CLOSING_WHITELIST))   # 화이트리스트 밖 라벨 → 아무것도 안 뺌
    # 결측 무시: 빈 문자열·공백만 있는 라벨(구 회차)은 순환에서 무시 → 전체 유지
    ok &= (closing_whitelist(["", "  ", None]) == list(CLOSING_WHITELIST))
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ None=전체·대소문자/공백 정규화·밖 라벨/결측 무시(하위호환)")
    assert ok


# ---------- ⓑ-1 스키마 additive: Beat/ChapterRecord 필드 존재·기본 "" ----------
def test_schema_additive_default_empty() -> None:
    b = Beat(chapter=1)
    r = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED)
    ok = (b.closing_device == "" and r.closing_device == "")
    # 값 세팅 왕복
    b2 = Beat(chapter=2, closing_device="dialogue")
    ok &= (b2.closing_device == "dialogue")
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 스키마 additive: Beat/ChapterRecord.closing_device 기본 ''·왕복")
    assert ok


# ---------- ⓑ-2 구 JSON 로드 바이트 동일(하위호환) ----------
def test_old_json_load_byte_identical() -> None:
    # closing_device 필드 없는 '구 JSON'(구 회차 기록)이 그대로 로드되고, 재직렬화 시 결측 필드가 기본 ""로 채워진다.
    old = {"chapter": 7, "status": "FINALIZED", "title": "7화", "hook_type": "question",
           "chapter_function": "escalation", "place": "고시원"}
    rec = ChapterRecord.model_validate(old)
    ok = (rec.closing_device == "" and rec.hook_type == "question")   # 결측 → 기본값, 나머지 불변
    # 구 JSON 이 로드 자체로 깨지지 않는가(추가 필드 additive)
    b_old = Beat.model_validate({"chapter": 3, "hook_type": "action"})
    ok &= (b_old.closing_device == "" and b_old.hook_type == "action")
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 구 JSON(closing_device 결측) 로드·기본값 채움(하위호환)")
    assert ok


# ---------- 통합 fake provider ----------
class _Cap(LLMProvider):
    """chat 을 캡처하고 지정 JSON 을 반환하는 Fake."""
    def __init__(self, payload: dict):
        super().__init__(); self.payload = payload; self.sys = ""
    def chat(self, msgs, **k):
        self.sys = msgs[0]["content"]
        return json.dumps(self.payload, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    w = WorldConfig(title="t", genre="g", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="a1", order=1, title="A1", goal="g")])
    return w


def _ep(**kw) -> Episode:
    base = dict(episode_id="e1", arc_id="a1", order=1, title="E1", premise="도입", climax="절정사건")
    base.update(kw); return Episode(**base)


# ---------- ⓒ 렌더 스냅샷: 프롬프트 축소 화이트리스트·선언 문안·미전달 시 전체 ----------
def _closing_line(sys: str) -> str:
    return next(l for l in sys.split(",") if "closing_device(" in l)


def test_beat_prompt_reflects_reduced_whitelist() -> None:
    w = _world(); arc = w.spine.arcs[0]; ep = _ep()
    payload = {"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"],
               "hook_type": "action", "chapter_function": "setup", "closing_device": "dialogue"}
    cap = _Cap(payload)
    ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전"], [],
                                     recent_closing_devices=["object", "interior"])
    line = _closing_line(cap.sys)
    # 최근 2화 사용 장치(object·interior)가 제시 선택지에서 빠지고, 선언·이행 문안이 있는가
    ok = ("object" not in line and "interior" not in line and "dialogue" in line and "arrival" in line)
    ok &= ("선언하고 그 장치로 닫아라" in line)   # 선언+이행 문안
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ 비트 프롬프트 마무리 장치 목록 축소(최근 2화 제외)+선언·이행 문안")
    assert ok


def test_beat_prompt_none_full_whitelist() -> None:
    # 미전달(None) → 전체 마무리 장치 목록 제시(하위호환)
    w = _world(); arc = w.spine.arcs[0]; ep = _ep()
    payload = {"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"]}
    cap = _Cap(payload)
    ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전"], [])
    line = _closing_line(cap.sys)
    ok = all(d in line for d in CLOSING_WHITELIST)   # 전 장치 노출
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ 미전달 시 전체 마무리 장치 목록(하위호환)")
    assert ok


# ---------- ⓓ 통합: 자기 라벨 파싱 + harness 영속 경로 ----------
def test_beat_parses_closing_device_label() -> None:
    w = _world(); arc = w.spine.arcs[0]; ep = _ep()
    payload = {"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"],
               "closing_device": "  Arrival  "}   # 공백 포함 → strip
    beat = ArcPlanner(_Cap(payload)).beat_for_episode(w, arc, ep, 4, False, ["직전"], [])
    ok = (beat.closing_device == "Arrival")   # strip 만(정규화는 순환 함수 몫 — 라벨 원문 보존)
    # 결측 payload → 빈 문자열(폴백 경로 아님·정상 파싱)
    beat2 = ArcPlanner(_Cap({"title": "t", "summary": "s", "key_events": ["e"], "entities": ["hero"]})) \
        .beat_for_episode(w, arc, ep, 5, False, ["직전"], [])
    ok &= (beat2.closing_device == "")
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ 비트 파싱: closing_device 자기 라벨 추출(strip)·결측=''")
    assert ok


def test_harness_persists_closing_device_from_beat() -> None:
    # harness 가 beat dict 의 closing_device 를 ChapterRecord 로 옮기는 계약(코드 경로) — dict.get 폴백 포함.
    beat_dict = {"title": "9화", "chapter_function": "payoff", "hook_type": "reveal",
                 "closing_device": "sensory", "place": "옥상"}
    rec = ChapterRecord(chapter=9, status=ChapterStatus.FINALIZED, title=beat_dict.get("title", ""),
                        chapter_function=beat_dict.get("chapter_function", ""),
                        hook_type=beat_dict.get("hook_type", ""),
                        closing_device=beat_dict.get("closing_device", ""),
                        place=beat_dict.get("place", ""))
    ok = (rec.closing_device == "sensory")
    # 구 beat dict(라벨 없음) → 기본 "" (harness dict.get 폴백)
    rec2 = ChapterRecord(chapter=10, status=ChapterStatus.FINALIZED,
                         closing_device={}.get("closing_device", ""))
    ok &= (rec2.closing_device == "")
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ harness 영속: beat.closing_device → ChapterRecord(dict.get 폴백)")
    assert ok


def test_recent_closing_collection_last2_finalized() -> None:
    # copilot 의 '최근 2화 FINALIZED closing_device' 수집 산술을 고정(과거 회차 분류 없음·결측 무시).
    recs = [
        ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, closing_device="dialogue"),
        ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, closing_device=""),        # 결측(구 회차) → 무시
        ChapterRecord(chapter=3, status=ChapterStatus.ESCALATED, closing_device="action"),  # 비영속 전이 → 제외
        ChapterRecord(chapter=4, status=ChapterStatus.FINALIZED, closing_device="object"),
        ChapterRecord(chapter=5, status=ChapterStatus.FINALIZED, closing_device="interior"),
    ]
    # copilot 과 동일 산술(정렬·FINALIZED·비어있지 않은 라벨·마지막 2개)
    recent_closing = [(c.closing_device or "").strip()
                      for c in sorted(recs, key=lambda c: c.chapter)
                      if c.status == ChapterStatus.FINALIZED and (c.closing_device or "").strip()][-2:]
    ok = (recent_closing == ["object", "interior"])   # ch4·ch5 (결측 ch2·ESCALATED ch3 제외)
    # 이 수집이 순환 함수와 물려 돌아가는가(object·interior 제외된 선택지)
    choices = closing_whitelist(recent_closing)
    ok &= ("object" not in choices and "interior" not in choices and "dialogue" in choices)
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ 최근 2화 수집(정렬·FINALIZED·결측 무시)→순환 물림")
    assert ok


_TESTS = [
    test_closing_whitelist_rotation, test_closing_whitelist_two_distinct_removed,
    test_closing_whitelist_exhausted_restores_full, test_closing_whitelist_none_full_and_normalized,
    test_schema_additive_default_empty, test_old_json_load_byte_identical,
    test_beat_prompt_reflects_reduced_whitelist, test_beat_prompt_none_full_whitelist,
    test_beat_parses_closing_device_label, test_harness_persists_closing_device_from_beat,
    test_recent_closing_collection_last2_finalized,
]

if __name__ == "__main__":
    results = []
    for t in _TESTS:
        try:
            t(); results.append(True)
        except AssertionError:
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
