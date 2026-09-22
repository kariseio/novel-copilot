# -*- coding: utf-8 -*-
"""FI-4 전역 관측 대시보드 집계 — 순수 읽기(락 불요·LLM 0콜·저장물 불변).

설계 docs/design-fi4-observability-dashboard.md. build_dashboard(repo, settings)가 전 작품 본체 JSON +
트레이스 사이드카를 결정론 집계해 GET /api/dashboard 응답 dict를 만든다.

원칙(설계 §0):
  · 수치 전 제공, 판정 형태 금지 — 서버는 판정성 비율을 나누지 않는다(record/event 카운트 분리·분수 표기는 UI).
    응답에 ratio/rate/percent류 파생 필드가 없다(계약 테스트로 잠금). 예외는 tokens_per_chapter(단위경제 관례)뿐.
  · 관측경계 정직 — event_counts 는 관측 이후(observed_since)·actor 분리, record_counts 는 전체 이력(분리 스키마).
  · RAG 샤드 미열람(§1) — 본체 JSON 만 직접 파싱한다(임베딩 사이드카 미접근 — repo.get 은 RAG 를 복원하므로 우회).
  · 재계산·재판정 안 함 — 회차 계측은 저장값(verification SSOT·부재 시 본문 길이 인용)만, 새 검출기 0.
  · 손상 트레이스는 은폐하지 않고 trace_errors 로 정직 카운트(결측과 구분 — 존재는 확인됐으나 못 읽음).

이 모듈은 저장물을 읽기만 한다(어떤 파일도 쓰지 않는다). stdlib 만 쓰므로 순환 임포트가 없다.
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path

# FI-1 작가 의도 이벤트 관측 개시일(그 이전 이력엔 이벤트가 없다 — 이벤트 카운트에 항상 병기, 전체 이력 record 와 분리).
OBSERVED_SINCE = "2026-07-17"

# copilot.edit_chapter 위임이 남기는 directive 스탬프. 단일("[직접 편집]")·다중("[직접 편집] 구간 N곳") 모두 이 접두.
_DIRECT_EDIT_STAMP = "[직접 편집]"

# 이벤트 카운트 카테고리(actor별). SSOT 레코드가 없는 신호만 — 채택/되돌림/직접편집은 record_counts(revisions) 소관이라
#   여기서 재계수하지 않는다(원장-뷰 분리: revise_accept/revise_undo/direct_edit surface 는 _event_category 에서 None).
_EVENT_CATS = ("revise_propose", "friction", "canon_edits")

# ts 정규화 — GA-2/FI-2 뷰어의 tz-벗김 정렬 키(tools/trace_view._ts_key)와 동일 로직(설계 §2·중복 구현 금지: 양쪽
#   1함수를 각자 두고 잠금 테스트로 일치를 계약화). tz 오프셋(±HHMM/±HH:MM/Z)만 벗겨 로컬 벽시계 문자열로 비교한다.
_TZ_SUFFIX = re.compile(r"[+-]\d{2}:?\d{2}$")


def ts_key(ts) -> str:
    """혼재 ts(naive vs %z)를 tz 오프셋만 벗긴 로컬 벽시계 문자열로 정규화(산술 변환 0·결정론). 빈 ts→""(맨 앞)."""
    if not ts:
        return ""
    s = str(ts).strip()
    if s.endswith("Z"):
        return s[:-1]
    m = _TZ_SUFFIX.search(s)
    return s[:m.start()] if m else s


def _num(x):
    """숫자(계측값)만 통과 — 그 외(None·문자열 'MISSING'·bool)는 None. 저장 계측 인용을 방어적으로 거른다."""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _dig(d, *keys):
    """중첩 dict 안전 탐색 — 경로 중 하나라도 dict 가 아니거나 없으면 None."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _event_category(surface, chapter):
    """author_intent surface → 이벤트 카운트 카테고리. SSOT 레코드가 있는 표면(accept/undo/direct_edit)은 None(비계수)."""
    if surface == "revise_propose":
        return "revise_propose"
    if isinstance(surface, str) and surface.startswith("friction"):
        return "friction"
    if chapter == 0:               # ch=0 = 캐논 정정(설정집·엔티티·관계·스파인·연재 지시) — 유일 사본이라 event 로만 존재
        return "canon_edits"
    return None


def _zero_cats() -> dict:
    return {c: 0 for c in _EVENT_CATS}


def _metric_point(rec: dict) -> dict:
    """회차 1건의 저장 계측 인용(재계산·재판정 0). 값 부재는 None(0 위장 금지 — 스파크라인이 결측점을 건너뜀).

    불변(설계 §5-4ⓑ 정정 2026-07-17): 이 구역(metrics_latest/metrics_series)은 회차에 **저장된** 계측의 인용만 —
    서버가 새로 나눗셈하는 파생 필드 추가 금지. top_ratio/da_ratio 는 저장 키 원명 그대로의 인용이다(금지 대상은
    '서버가 소표본 카운트로 새로 계산한 판정성 비율'이지 기존 결정론 계측의 인용이 아님 — 계약 키 스캔도 이 구역 제외).

    top_ratio/da_ratio = 문말 종결 분포('-었다'류 최빈 비율·'-다' 연속 비율) — verification.ai_tell.kiwi(PR-2 SSOT)
    우선, 부재 시 회차 ai_tell.kiwi(ending_profile/da_streak) 폴백. ending_max_run = verification.ending_runs.max_run
    (무중단 동일 종결 어미 run 최대 — ST-14 벽 축). Kiwi 부품 계측이라 저장값이 없으면 재계산하지 않고 None.
    chars = verification.length.chars, 부재 시 본문 길이(판정 아닌 원자료)."""
    v = rec.get("verification") if isinstance(rec.get("verification"), dict) else {}
    at = rec.get("ai_tell") if isinstance(rec.get("ai_tell"), dict) else {}
    top_ratio = _num(_dig(v, "ai_tell", "kiwi", "top_ratio"))
    if top_ratio is None:
        top_ratio = _num(_dig(at, "kiwi", "ending_profile", "top_ratio"))
    da_ratio = _num(_dig(v, "ai_tell", "kiwi", "da_ratio"))
    if da_ratio is None:
        da_ratio = _num(_dig(at, "kiwi", "da_streak", "ratio"))
    chars = _num(_dig(v, "length", "chars"))
    if chars is None:
        chars = len(rec.get("text") or "")
    return {"ch": rec.get("chapter"),
            "top_ratio": top_ratio,
            "da_ratio": da_ratio,
            "ending_max_run": _num(_dig(v, "ending_runs", "max_run")),
            "chars": chars}


def _projects_dir(repo, settings) -> Path | None:
    """본체 JSON·트레이스 사이드카가 있는 projects 디렉터리. Filesystem 레포는 .dir 이 권위(실제 저장 위치)."""
    d = getattr(repo, "dir", None)
    if d is not None:
        return Path(d)
    try:
        return Path(settings.resolved_data_dir()) / "projects"
    except Exception:
        return None


def _iter_projects(projects_dir: Path):
    """본체 프로젝트 JSON 만 raw 로 읽어 (path, doc) 산출(§1 RAG 미열람 — .rag./.trace. 사이드카는 파일명으로 배제).

    list_summaries 관례처럼 손상·비프로젝트 JSON 은 건너뛴다(pydantic 미사용 — 스키마 드리프트로 작품이 조용히
    누락되지 않게 raw dict 로 관용 파싱). 프로젝트 서명 = world 보유(트레이스 doc 은 {chapter,runs} 라 world 없음)."""
    if not projects_dir or not projects_dir.exists():
        return
    for path in sorted(projects_dir.glob("*.json")):
        name = path.name
        if ".rag." in name or ".trace." in name:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(doc, dict) and doc.get("id") and isinstance(doc.get("world"), dict):
            yield path, doc


def _work_record_counts(chapters: list) -> dict:
    """전체 이력 카운트(revisions/regen 기반·actor 무관 — 구 레코드엔 actor 없음). 설계 §3 정의 그대로."""
    accepted = direct = undone = 0
    for c in chapters:
        for r in (c.get("revisions") or []):
            if str(r.get("directive") or "").startswith(_DIRECT_EDIT_STAMP):
                direct += 1
            else:
                accepted += 1                       # revise_accepted = len(revisions) − 직접 편집분
            if r.get("reverted") is True:
                undone += 1
    return {"revise_accepted": accepted, "direct_edits": direct, "undone": undone}


def build_dashboard(repo, settings) -> dict:
    """전 작품 관측 집계(읽기 전용·LLM 0콜). 부분 실패(손상 트레이스)는 trace_errors 로 정직 표면화(200 고정 라우트).

    응답에 판정성 비율 필드 없음(§0-2 — 나눗셈은 서버가 하지 않는다). record_counts(전체 이력)와 event_counts
    (관측 이후·actor 분리)는 별도 키. tokens_per_chapter 만 단위경제 예외."""
    projects_dir = _projects_dir(repo, settings)
    today = time.strftime("%Y-%m-%d")
    works: list = []
    total_projects = total_chapters = total_tokens = 0
    events_today = {"author": 0, "tool": 0}
    trace_errors = 0

    for _path, doc in _iter_projects(projects_dir):
        pid = doc.get("id")
        chapters = doc.get("chapters") or []
        n_chapters = len(chapters)
        tokens = int(_num(_dig(doc, "usage_total", "chat_tokens")) or 0)

        record_counts = _work_record_counts(chapters)
        record_counts["regens"] = len(doc.get("regen_events") or [])

        # last_activity 후보(§3 — 전 원천 ts 의 max, 정규화 키 기준). 작품 생성 시각을 하한으로 포함(활동 없음=생성만).
        activity = [ts_key(doc.get("created_at"))]
        for c in chapters:
            for r in (c.get("revisions") or []):
                activity.append(ts_key(r.get("created_at")))
                activity.append(ts_key(r.get("reverted_at")))
        for e in (doc.get("regen_events") or []):
            activity.append(ts_key(e.get("at")))

        # 트레이스 사이드카: author_intent 이벤트 카운트(actor별) + rerender_pipeline 채택/기각 + 활동 ts.
        by_actor = {"author": _zero_cats(), "tool": _zero_cats()}
        rr_adopted = rr_rejected = 0
        for tp in sorted(projects_dir.glob(f"{pid}.trace.*.json")):
            try:
                ch = int(tp.name.rsplit(".", 2)[-2])
            except (ValueError, IndexError):
                continue
            tdoc = repo.load_trace(pid, ch)
            if tdoc is None:                    # glob 로 존재 확인됨 → None = 손상(결측 아님) → 은폐 금지·정직 카운트
                trace_errors += 1
                continue
            for run in (tdoc.get("runs") or []):
                if not isinstance(run, dict):
                    continue
                kind = run.get("kind")
                ts = run.get("ts")
                if ts:
                    activity.append(ts_key(ts))
                if kind == "author_intent":
                    actor = run.get("actor") or "tool"
                    cat = _event_category(run.get("surface"), run.get("chapter"))
                    if cat:
                        bucket = by_actor.get(actor)
                        if bucket is None:
                            bucket = by_actor[actor] = _zero_cats()   # 예기치 못한 actor 값도 정직 노출(드롭 금지)
                        bucket[cat] += 1
                    if ts and ts_key(ts)[:10] == today:
                        events_today[actor] = events_today.get(actor, 0) + 1
                elif kind == "rerender_pipeline":
                    ev = run.get("event")
                    if ev == "adopted":
                        rr_adopted += 1
                    elif ev == "rejected":
                        rr_rejected += 1

        activity = [a for a in activity if a]
        last_activity = max(activity) if activity else ""

        # 회차 저장 계측 인용(스파크라인 시리즈 + 최신 계측). 회차 번호 순 정렬.
        chs_sorted = sorted(chapters, key=lambda c: c.get("chapter") or 0)
        series = [_metric_point(c) for c in chs_sorted]
        metrics_latest: dict = {}
        if chs_sorted:
            last_c = chs_sorted[-1]
            latest = dict(series[-1])
            latest["chapter"] = latest.pop("ch")
            thr = _num(_dig(last_c.get("verification") if isinstance(last_c.get("verification"), dict) else {},
                            "ending_runs", "threshold"))
            if thr is not None:
                latest["ending_run_threshold"] = thr        # advisory 참조선(대역 인용 — 판정 아님)
            metrics_latest = latest

        works.append({
            "pid": pid,
            "title": _dig(doc, "world", "title") or "",
            "chapters": n_chapters,
            "last_activity": last_activity,
            "record_counts": record_counts,
            "event_counts": {"observed_since": OBSERVED_SINCE, "by_actor": by_actor},
            "rerender": {"adopted": rr_adopted, "rejected": rr_rejected},
            "usage": {"tokens": tokens,
                      "tokens_per_chapter": (tokens // n_chapters if n_chapters else 0)},
            "metrics_latest": metrics_latest,
            "metrics_series": series,
        })
        total_projects += 1
        total_chapters += n_chapters
        total_tokens += tokens

    # 기본 정렬 = 최근 활동순(활동 사실·판정 아님 — 설계 §4 허용 기본값). 클라이언트가 컬럼 클릭으로 재정렬한다.
    works.sort(key=lambda w: w["last_activity"], reverse=True)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "observed_since": OBSERVED_SINCE,
        "totals": {"projects": total_projects, "chapters": total_chapters, "tokens": total_tokens,
                   "events_today": {"author": events_today.get("author", 0),
                                    "tool": events_today.get("tool", 0)}},
        "trace_errors": trace_errors,
        "works": works,
    }
