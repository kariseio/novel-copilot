# -*- coding: utf-8 -*-
"""FI-4 검증 — 전역 관측 대시보드 집계(읽기 전용·LLM 0콜·결정론).

설계 docs/design-fi4-observability-dashboard.md §5-4 사전 등록 잠금 6종:
  ⓐ 합성 데이터 카운트 정확성 — record/event 분리·actor 분리·직접 편집 스탬프·reverted.
  ⓑ 판정성 비율 필드 부재 계약 — **서버 파생 집계 구역(totals·counts·usage·rerender)만** 키 스캔(ratio/rate/
     percent류 0·tokens_per_chapter 화이트리스트). metrics_latest/metrics_series 는 저장 계측 인용 구역이라 스캔
     제외 — 대신 top_ratio 저장 키 원명 인용이 실재함을 잠근다(§5-4ⓑ 정정 2026-07-17: 금지 대상은 서버가 소표본
     카운트로 새로 계산한 판정성 비율이지 저장된 결정론 계측의 인용이 아님).
  ⓒ ts 혼재 정렬 + _ts_key 양본 일치(insights.ts_key ↔ tools.trace_view._ts_key).
  ⓓ 손상 trace → trace_errors 정직 카운트(작품 행 유지).
  ⓔ 회차 0 작품도 행 존재(작품 존재 정직).
  ⓕ 라우트 200·읽기 전용(저장물 바이트 불변).

실행: (app/ 에서) py -3.12 -X utf8 -m pytest tools/test_fi4_dashboard.py -q
"""
from __future__ import annotations
import re
import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot·tools

import pytest

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectSeed, ProjectState, RegenEvent
from novelcopilot.domain.world import WorldConfig
from novelcopilot.domain.types import ChapterRecord, ChapterRevision, ChapterStatus
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import insights


# ───────────────────────── 스캐폴딩(LLM 0콜 — 순수 저장물만) ─────────────────────────
def _repo_settings(tmp):
    repo = FilesystemProjectRepository(tmp)
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    return repo, settings


def _save(repo, state):
    repo.save(state)


def _emit(repo, pid, ch, surface, actor, ts):
    """author_intent 이벤트 1건을 트레이스 사이드카에 append(telemetry.emit_intent 봉투와 동형)."""
    run = {"kind": "author_intent", "v": 1, "ts": ts, "actor": actor, "surface": surface, "chapter": ch}
    repo.save_trace(pid, ch, run, kind="author_intent", max_runs=200)


def _emit_rr(repo, pid, ch, event, ts):
    """rerender_pipeline 이벤트 1건(RO-1 훅 라이프사이클)."""
    run = {"kind": "rerender_pipeline", "event": event, "ts": ts, "chapter": ch}
    repo.save_trace(pid, ch, run, max_runs=50)


def _state(pid, *, title="작품", chapters=None, regen_events=None, usage_total=None,
           created_at="2026-07-16T08:00:00"):
    return ProjectState(id=pid, seed=ProjectSeed(title="t"),
                        world=WorldConfig(title=title, synopsis="s"),
                        created_at=created_at, chapters=chapters or [],
                        regen_events=regen_events or [], usage_total=usage_total or {})


def _work(d, pid):
    return next(w for w in d["works"] if w["pid"] == pid)


# ═════════════════ ⓐ 카운트 정확성(record/event/actor/스탬프/reverted) ═════════════════
def _rich_project(repo):
    """퇴고(정상/직접편집 단일·다중/되돌림)+재생성+토큰+검증계측 + 이벤트(actor·surface 다양)·재실현 라이프사이클."""
    revs = [
        ChapterRevision(directive="문장 다듬어", reverted=False, created_at="2026-07-16T09:00:00"),        # revise_accepted
        ChapterRevision(directive="[직접 편집]", reverted=False, created_at="2026-07-16T09:01:00"),        # direct_edit(단일)
        ChapterRevision(directive="[직접 편집] 구간 2곳", reverted=False, created_at="2026-07-16T09:02:00"),  # direct_edit(다중 변형)
        ChapterRevision(directive="어조 바꿔", reverted=True, reverted_at="2026-07-16T09:05:00",
                        created_at="2026-07-16T09:03:00"),                                                # revise_accepted + undone
    ]
    ch1 = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="본문" * 20, revisions=revs,
                        verification={"ending_runs": {"max_run": 8, "threshold": 6, "count": 2},
                                      "length": {"chars": 500},
                                      "ai_tell": {"kiwi": {"top_ratio": 0.63, "da_ratio": 0.31}}})
    state = _state("pa", title="작품 가", chapters=[ch1],
                   regen_events=[RegenEvent(chapter=1, seq=1, at="2026-07-16T08:30:00"),
                                 RegenEvent(chapter=1, seq=2, at="2026-07-16T08:40:00")],
                   usage_total={"chat_tokens": 1000, "chat_calls": 5})
    _save(repo, state)
    today = time.strftime("%Y-%m-%dT12:00:00")
    past = "2020-01-01T00:00:00"
    _emit(repo, "pa", 1, "revise_propose", "author", today)   # event: author propose (오늘)
    _emit(repo, "pa", 1, "revise_propose", "tool", today)     # event: tool propose (오늘)
    _emit(repo, "pa", 1, "friction_locked", "author", past)   # event: author friction
    _emit(repo, "pa", 1, "revise_accept", "author", past)     # SSOT 레코드 존재 → 비계수
    _emit(repo, "pa", 1, "revise_undo", "author", past)       # SSOT 레코드 존재 → 비계수
    _emit(repo, "pa", 1, "direct_edit", "author", past)       # SSOT(직접편집) → 비계수
    _emit(repo, "pa", 0, "directive_add", "author", past)     # canon_edits(author)
    _emit(repo, "pa", 0, "bible_edit", "tool", past)          # canon_edits(tool)
    _emit_rr(repo, "pa", 1, "adopted", past)
    _emit_rr(repo, "pa", 1, "rejected", past)
    _emit_rr(repo, "pa", 1, "skip", past)                     # adopted/rejected 만 계수 → 비계수


def test_counts_record_event_actor_split(tmp_path):
    repo, settings = _repo_settings(tmp_path)
    _rich_project(repo)
    d = insights.build_dashboard(repo, settings)
    w = _work(d, "pa")
    # record_counts(전체 이력) — 직접 편집 스탬프 제외·reverted 별도
    assert w["record_counts"] == {"revise_accepted": 2, "direct_edits": 2, "undone": 1, "regens": 2}
    # event_counts(관측 이후·actor 분리) — accept/undo/direct_edit surface 는 SSOT 라 비계수
    ba = w["event_counts"]["by_actor"]
    assert ba["author"] == {"revise_propose": 1, "friction": 1, "canon_edits": 1}
    assert ba["tool"] == {"revise_propose": 1, "friction": 0, "canon_edits": 1}
    assert w["event_counts"]["observed_since"] == "2026-07-17"
    # 재실현 라이프사이클(adopted/rejected 만)
    assert w["rerender"] == {"adopted": 1, "rejected": 1}
    # 토큰·단위경제
    assert w["usage"]["tokens"] == 1000 and w["usage"]["tokens_per_chapter"] == 1000
    # 저장 계측 인용(재계산 아님) — top_ratio/da_ratio 는 저장 키 원명 그대로(§5-4ⓑ 정정)
    assert w["metrics_latest"] == {"chapter": 1, "top_ratio": 0.63, "da_ratio": 0.31,
                                   "ending_max_run": 8, "chars": 500, "ending_run_threshold": 6}
    # 오늘 이벤트(actor 병기) — TODAY 로 심은 2건만
    assert d["totals"]["events_today"] == {"author": 1, "tool": 1}
    # totals
    assert d["totals"]["projects"] == 1 and d["totals"]["chapters"] == 1 and d["totals"]["tokens"] == 1000


# ═════════════════ ⓑ 판정성 비율 필드 부재 계약(서버 파생 집계 구역 한정) ═════════════════
_FORBIDDEN = {"ratio", "rate", "rates", "percent", "percentage", "pct", "proportion", "proportions"}
_WHITELIST = {"tokens_per_chapter"}
# §5-4ⓑ 정정(2026-07-17): 스캔 제외 구역 = 회차에 저장된 계측값의 인용(top_ratio 등 저장 키 원명 그대로 노출).
#   금지 대상은 '서버가 소표본 카운트로 새로 계산한 판정성 비율'(채택률류) — 이 구역엔 인용만 허용(신규 나눗셈 금지,
#   그 불변은 insights._metric_point 주석·리뷰로 지킨다).
_CITATION_ZONES = {"metrics_latest", "metrics_series"}


def _all_keys(o):
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            out.append(k)
            if k in _CITATION_ZONES:
                continue          # 저장 계측 인용 구역 — 하위 키는 스캔 대상 아님(§5-4ⓑ)
            out += _all_keys(v)
    elif isinstance(o, list):
        for x in o:
            out += _all_keys(x)
    return out


def test_no_judgmental_ratio_fields_in_server_zones(tmp_path):
    repo, settings = _repo_settings(tmp_path)
    _rich_project(repo)   # 계측·이벤트·재실현·토큰까지 다 채운 응답을 스캔 대상으로
    d = insights.build_dashboard(repo, settings)
    keys = _all_keys(d)   # totals·record_counts·event_counts·rerender·usage 등 서버 파생 집계 구역 전체
    bad = []
    for k in keys:
        if k in _WHITELIST or k in _CITATION_ZONES:
            continue
        segs = re.split(r"[^a-z0-9]+", str(k).lower())   # '_' 등으로 분절 — 'generated_at'(rate 오탐) 방지
        if any(s in _FORBIDDEN for s in segs):
            bad.append(k)
    assert bad == [], f"서버 파생 집계 구역에 판정성 비율 필드 검출(설계 §0-2 위반): {sorted(set(bad))}"
    # 유일 허용 파생 수치(단위경제)는 실제로 존재해야 화이트리스트가 계약으로 유효
    assert "tokens_per_chapter" in keys
    # 저장 계측 인용은 오차단하지 않는다 — top_ratio 가 저장 키 원명 그대로 실재(§5-4ⓑ 정정의 존재 계약)
    w = _work(d, "pa")
    assert w["metrics_latest"]["top_ratio"] == 0.63
    assert any(p.get("top_ratio") == 0.63 for p in w["metrics_series"])


def test_metrics_cite_stored_values_with_fallback(tmp_path):
    """인용 소스 우선순위 잠금 — verification.ai_tell.kiwi(PR-2 SSOT) 우선, 부재 시 회차 ai_tell.kiwi 폴백.
    둘 다 부재·MISSING 문자열이면 None(0 위장 금지 — 재계산 안 함)."""
    repo, settings = _repo_settings(tmp_path)
    ch1 = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="a",
                        verification={"ai_tell": {"kiwi": {"top_ratio": 0.41, "da_ratio": 0.12}}},
                        ai_tell={"kiwi": {"ending_profile": {"top_ratio": 0.99},
                                          "da_streak": {"ratio": 0.98}}})     # verification 우선 → 0.41/0.12
    ch2 = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="b",
                        ai_tell={"kiwi": {"ending_profile": {"top_ratio": 0.77},
                                          "da_streak": {"ratio": 0.44}}})     # verification 부재 → 폴백 0.77/0.44
    ch3 = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="c",
                        verification={"ai_tell": "미실행"})                    # 결측(MISSING 문자열) → None(인용만·재계산 0)
    _save(repo, _state("pm", chapters=[ch1, ch2, ch3]))
    d = insights.build_dashboard(repo, settings)
    s = _work(d, "pm")["metrics_series"]
    assert (s[0]["top_ratio"], s[0]["da_ratio"]) == (0.41, 0.12)
    assert (s[1]["top_ratio"], s[1]["da_ratio"]) == (0.77, 0.44)
    assert (s[2]["top_ratio"], s[2]["da_ratio"]) == (None, None)


# ═════════════════ ⓒ ts 혼재 정렬 + _ts_key 양본 일치 ═════════════════
def test_ts_key_agrees_with_trace_view():
    import tools.trace_view as tv   # 뷰어(stdlib·novelcopilot 미임포트)의 자기 _ts_key 와 로직 일치 잠금
    samples = ["2026-07-16T10:00:00", "2026-07-16T10:00:00+0900", "2026-07-16T10:00:00+09:00",
               "2026-07-16T10:00:00-0500", "2026-07-16T10:00:00Z", "", "  2026-07-16T10:00:00Z  ", None]
    for s in samples:
        assert insights.ts_key(s) == tv._ts_key(s), f"ts_key 불일치: {s!r}"


def test_last_activity_normalizes_mixed_tz(tmp_path):
    repo, settings = _repo_settings(tmp_path)
    ch = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="x",
                       revisions=[ChapterRevision(directive="d", created_at="2026-07-16T23:00:00")])   # naive 23:00
    state = _state("ps", chapters=[ch], created_at="2020-01-01T00:00:00",
                   regen_events=[RegenEvent(chapter=1, seq=1, at="2026-07-16T10:00:00+0900")])         # %z 10:00
    _save(repo, state)
    d = insights.build_dashboard(repo, settings)
    # tz 벗김 후 비교: 23:00(naive) > 10:00(+0900 벗김) — 산술 변환 없이 로컬 벽시계 문자열 max
    assert _work(d, "ps")["last_activity"] == "2026-07-16T23:00:00"


# ═════════════════ ⓓ 손상 trace → trace_errors ═════════════════
def test_corrupt_trace_counted_project_kept(tmp_path):
    repo, settings = _repo_settings(tmp_path)
    ch = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="x")
    _save(repo, _state("pd", chapters=[ch]))
    _emit(repo, "pd", 1, "revise_propose", "author", "2020-01-01T00:00:00")   # 유효 trace
    (repo.dir / "pd.trace.5.json").write_text("{ 손상 아닌 척 하는 깨진 JSON", encoding="utf-8")   # 손상 trace(존재하나 못 읽음)
    d = insights.build_dashboard(repo, settings)
    assert d["trace_errors"] == 1                                    # 은폐 금지 — 정직 카운트
    w = _work(d, "pd")                                               # 손상에도 작품 행 유지
    assert w["event_counts"]["by_actor"]["author"]["revise_propose"] == 1   # 유효 trace 는 정상 집계


# ═════════════════ ⓔ 회차 0 작품 행 존재 ═════════════════
def test_zero_chapter_project_has_row(tmp_path):
    repo, settings = _repo_settings(tmp_path)
    _save(repo, _state("pe", title="빈 작품", chapters=[]))
    d = insights.build_dashboard(repo, settings)
    w = _work(d, "pe")                                              # 0행 아니라 행 표시(작품 존재 정직)
    assert w["chapters"] == 0
    assert w["usage"]["tokens_per_chapter"] == 0                    # 분모 0 안전
    assert w["metrics_latest"] == {} and w["metrics_series"] == []


# ═════════════════ ⓕ 라우트 200 · 읽기 전용(저장물 바이트 불변) ═════════════════
def test_route_200_and_read_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import novelcopilot.config as cfg
    from novelcopilot.main import create_app
    monkeypatch.setenv("NOVEL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "_settings", None)   # 싱글톤 재생성(임시 data_dir 반영) — monkeypatch 종료 후 복원
    app = create_app()
    svc = app.state.service
    ch = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="본문",
                       revisions=[ChapterRevision(directive="[직접 편집]", created_at="2026-07-16T09:00:00")])
    svc.repo.save(_state("pf", chapters=[ch],
                         regen_events=[RegenEvent(chapter=1, seq=1, at="2026-07-16T08:30:00")],
                         usage_total={"chat_tokens": 500}))
    _emit(svc.repo, "pf", 1, "revise_propose", "author", "2020-01-01T00:00:00")
    _emit_rr(svc.repo, "pf", 1, "adopted", "2020-01-01T00:00:00")

    pdir = svc.repo.dir
    before = {p.name: p.read_bytes() for p in sorted(pdir.glob("*"))}
    r = TestClient(app).get("/api/dashboard")
    assert r.status_code == 200
    j = r.json()
    assert any(w["pid"] == "pf" for w in j["works"])
    after = {p.name: p.read_bytes() for p in sorted(pdir.glob("*"))}
    assert before == after, "GET /api/dashboard 가 저장물을 변형함(읽기 전용 위반)"


def main() -> int:
    return pytest.main([str(pathlib.Path(__file__).resolve()), "-q"])


if __name__ == "__main__":
    sys.exit(main())
