# -*- coding: utf-8 -*-
"""P-2 검증 — 요약 LLM 실패 폴백 소스차단. 실 LLM 0콜(fake provider).

잠그는 계약:
① 실패 폴백값이 프로즈 서두(text[:200/400])가 아니라 beat 계획(summary+key_events) 합성 요지다
   → story_so_far 로 재유입돼도 '이미 쓴 문장'이 없어 재상연 불가.
② summary_degraded 플래그가 ChapterRecord 에 영속되고, story_so_far/최근요약 조립이 degraded 회차의
   detail 을 신뢰 강등(프로즈 슬라이스 폴백 금지 — 합성 요지만 재유입).
③ 실패 유형(truncated|empty|parse_failure)을 이벤트로 영속.
④ 구 JSON(summary_degraded 필드 없음) 로드가 바이트 동일(하위호환).

실행: PYTHONPATH=app py -3.12 tools/test_p2_summary_degraded.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.services.copilot import (_chapter_recap, _recent_summaries,
                                           _build_story_so_far)


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, node, event, **payload):
        self.events.append({"node": node, "event": event, **payload})


class _Prov:
    """chat_json 을 지정된 모드로 응답 — parse_failure(예외)/empty/truncated/ok."""
    def __init__(self, mode):
        self.mode = mode
        self.last_truncated = False

    def chat(self, *a, **k):
        return ""

    def chat_json(self, messages, temperature=0.0, max_tokens=0):
        if self.mode == "parse_failure":
            raise ValueError("chat_json: 재시도 후에도 JSON 파싱 실패")
        if self.mode == "empty":
            self.last_truncated = False
            return {"oneliner": "", "synopsis": ""}
        if self.mode == "truncated":
            self.last_truncated = True
            return {"oneliner": "", "synopsis": ""}
        # ok — RC-5 F3ⓐ: synopsis 하한(≥900) 이상이어야 degraded=None 성공 경로(짧은 산출은 underlength=degraded)
        return {"oneliner": "한줄요약", "synopsis": "상세한 사건 서술 " * 100}


def _gen(mode):
    settings = SimpleNamespace(prev_chapter_context_chars=4000, craft_progress=False,
                               scene_style_anchor=False)
    bus = _Bus()
    g = ChapterGenerator(_Prov(mode), checker=None, style=StyleSpec(),
                         event_bus=bus, settings=settings)
    return g, bus


# 이 회차 본문(프로즈 서두) — 폴백이 이걸 옮겨 적으면 재상연 소스가 된다
PROSE = "진우는 검을 뽑아 어둠 속으로 달려들었다. 심장이 요동쳤고, 그의 손은 떨리고 있었다. " * 20
BEAT = {"title": "각성", "summary": "진우가 던전에서 각성한다",
        "key_events": ["던전 진입", "첫 각성", "위기 탈출"]}


def test_fallback_is_beat_not_prose() -> bool:
    """① 실패 3종 모두 폴백값이 프로즈 서두가 아니라 beat 합성 요지."""
    ok = True
    for mode in ("parse_failure", "empty", "truncated"):
        g, bus = _gen(mode)
        one, syn, degraded = g._summarize(PROSE, "", BEAT)
        # 폴백이 프로즈 서두가 아님 — 본문 첫 40자가 요약에 통째로 실리지 않는다
        prose_head = PROSE[:40]
        cond_no_prose = (prose_head not in one) and (prose_head not in syn)
        # 대신 beat 계획 요소가 담긴다
        cond_beat = ("진우가 던전에서 각성한다" in syn) and ("던전 진입" in syn)
        cond_degraded = bool(degraded) and degraded.get("degraded") is True
        this = cond_no_prose and cond_beat and cond_degraded
        if not this:
            print(f"  [x] mode={mode} no_prose={cond_no_prose} beat={cond_beat} degraded={cond_degraded} syn={syn[:60]!r}")
        ok &= this
    print(f"[{'OK' if ok else 'FAIL'}] ① 폴백값=beat 합성 요지(프로즈 서두 아님)·degraded 반환")
    return ok


def test_failure_type_persisted() -> bool:
    """③ 실패 유형(truncated|empty|parse_failure)이 반환 degraded_info + 이벤트에 영속."""
    ok = True
    expect = {"parse_failure": "parse_failure", "empty": "empty", "truncated": "truncated"}
    for mode, want in expect.items():
        g, bus = _gen(mode)
        _, _, degraded = g._summarize(PROSE, "", BEAT)
        got = degraded.get("failure")
        # 이벤트에도 실패 유형이 실린다(empty_response 는 failure 페이로드, parse_failure 는 이벤트명)
        ev_ok = any(
            (e["event"] == "empty_response" and e.get("failure") == want) or
            (e["event"] == "parse_failure" and want == "parse_failure")
            for e in bus.events)
        this = (got == want) and ev_ok
        if not this:
            print(f"  [x] mode={mode} got={got!r} want={want!r} events={bus.events}")
        ok &= this
    print(f"[{'OK' if ok else 'FAIL'}] ③ 실패 유형(truncated|empty|parse_failure) 영속")
    return ok


def test_success_not_degraded() -> bool:
    """성공 경로는 degraded=None(플래그 미세팅)·프로즈 폴백 안 탐."""
    g, bus = _gen("ok")
    one, syn, degraded = g._summarize(PROSE, "", BEAT)
    ok = (degraded is None) and (one == "한줄요약") and ("상세" in syn)
    print(f"[{'OK' if ok else 'FAIL'}] 성공 경로 degraded=None(무회귀)")
    return ok


def test_no_beat_last_fallback_still_degraded() -> bool:
    """beat 부재(퇴고 재요약 등) → 최후 본문 슬라이스 폴백이되 degraded 플래그는 필수(②의 소비부가 강등)."""
    g, bus = _gen("empty")
    one, syn, degraded = g._summarize(PROSE, "", None)
    ok = (degraded is not None) and (degraded.get("degraded") is True)
    # beat 이 없으니 이 경로만 예외적으로 본문 슬라이스(요약 구멍 방지) — 그러나 degraded 로 소비부가 신뢰 강등
    ok &= (syn == PROSE[:400])
    print(f"[{'OK' if ok else 'FAIL'}] beat 부재 최후 폴백=본문 슬라이스+degraded 플래그")
    return ok


def test_consumer_trust_demotion() -> bool:
    """② degraded 회차의 recap 이 '그 회차 프로즈'로 폴백하지 않음(신뢰 강등)."""
    ok = True
    # degraded 회차의 고유 프로즈(다른 회차와 겹치지 않는 마커) — 이게 recap 에 새면 재상연 소스
    DEG_PROSE = "던전마커고유텍스트진우검투구먼지어둠발소리메아리철컹. " * 20
    # (a) degraded + detail 있음 → 합성 요지 사용(그 회차 프로즈 마커 부재)
    c_deg = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text=DEG_PROSE,
                          summary="합성요약", detail_synopsis="진우가 각성한다 / 주요 사건: 던전 진입",
                          summary_degraded=True)
    r = _chapter_recap(c_deg, near=True)
    ok &= ("던전마커고유텍스트" not in r) and ("진우가 각성한다" in r)
    # (b) degraded + detail/summary 모두 빈 경우 → 프로즈 슬라이스로 되돌아가지 않는다(빈 문자열)
    c_deg_empty = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text=DEG_PROSE,
                                summary="", detail_synopsis="", summary_degraded=True)
    r2 = _chapter_recap(c_deg_empty)
    ok &= (r2 == "")   # 프로즈 재유입 차단 — text[:120] 로 폴백하지 않음
    # (c) 정상 회차(비-degraded) + detail 빈 → 프로즈 슬라이스 폴백 유지(요약 구멍 방지, 기존 동작 무회귀)
    c_ok = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text=PROSE,
                         summary="", detail_synopsis="")
    r3 = _chapter_recap(c_ok)
    ok &= (r3 == PROSE[:120])
    # (d) story_so_far 조립: degraded 회차의 고유 프로즈 마커가 안 샌다(합성 요지만 실림)
    sofar, _ = _build_story_so_far([c_deg, c_ok], budget=10000)
    ok &= ("던전마커고유텍스트" not in sofar) and ("진우가 각성한다" in sofar)
    # (e) recent_summaries: degraded 직전 회차 상세가 프로즈로 안 샌다(단, 의도된 prev-tail verbatim 은 별개)
    rs = _recent_summaries([c_ok, c_deg])   # c_deg=직전
    detail_lines = "\n".join(x for x in rs if "상세:" in x)   # 상세 요약 라인만(말미 verbatim 제외)
    ok &= ("던전마커고유텍스트" not in detail_lines) and ("진우가 각성한다" in detail_lines)
    print(f"[{'OK' if ok else 'FAIL'}] ② 소비부 신뢰 강등(degraded 프로즈 슬라이스 재유입 차단)")
    return ok


def test_old_json_byte_identical() -> bool:
    """④ 구 JSON(summary_degraded 필드 없음) 로드 → 기본값 False·재직렬화 정합(하위호환)."""
    ok = True
    # 구 저장본에는 summary_degraded 키가 아예 없다
    old_json = ('{"chapter": 5, "status": "FINALIZED", "text": "본문", '
                '"summary": "요약", "detail_synopsis": "상세"}')
    rec = ChapterRecord.model_validate_json(old_json)
    ok &= (rec.summary_degraded is False)   # 기본값 False
    # 기존 필드 값 보존
    ok &= (rec.chapter == 5 and rec.summary == "요약" and rec.detail_synopsis == "상세")
    # 신규 저장본은 필드를 포함(영속) — 재로드 라운드트립 동일
    rec2 = ChapterRecord.model_validate_json(rec.model_dump_json())
    ok &= (rec2.summary_degraded is False and rec2.chapter == 5)
    # degraded=True 저장/로드 라운드트립
    rec3 = ChapterRecord(chapter=6, status=ChapterStatus.FINALIZED, summary_degraded=True)
    rec4 = ChapterRecord.model_validate_json(rec3.model_dump_json())
    ok &= (rec4.summary_degraded is True)
    print(f"[{'OK' if ok else 'FAIL'}] ④ 구 JSON 하위호환(기본값 False·라운드트립 정합)")
    return ok


def test_revision_snapshot_roundtrip() -> bool:
    """퇴고 undo 스냅샷 대칭: ChapterRevision.before_summary_degraded 가 구 JSON 무필드 로드 시 False,
    accept 스냅샷→undo 복원 라운드트립에서 값 보존(하위호환)."""
    from novelcopilot.domain.types import ChapterRevision
    ok = True
    # 구 JSON(필드 없음) → 기본 False
    old = ChapterRevision.model_validate_json('{"revision_id": "abc"}')
    ok &= (old.before_summary_degraded is False)
    # True 스냅샷 저장/로드 라운드트립
    rev = ChapterRevision(revision_id="x", before_summary_degraded=True)
    rev2 = ChapterRevision.model_validate_json(rev.model_dump_json())
    ok &= (rev2.before_summary_degraded is True)
    print(f"[{'OK' if ok else 'FAIL'}] 퇴고 undo 스냅샷 필드 하위호환·라운드트립")
    return ok


def main() -> int:
    tests = [test_fallback_is_beat_not_prose, test_failure_type_persisted,
             test_success_not_degraded, test_no_beat_last_fallback_still_degraded,
             test_consumer_trust_demotion, test_old_json_byte_identical,
             test_revision_snapshot_roundtrip]
    results = [t() for t in tests]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} P-2 tests passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
