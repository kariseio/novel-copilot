# -*- coding: utf-8 -*-
"""GA-2/FI-2 — 생성 트레이스 디버그 뷰어(CLI). 읽기 전용·LLM 0·의존성 0(stdlib만).

GA-1 이 회차별 사이드카(`<pid>.trace.<ch>.json`)에 남긴 중간 산출물(첫 초안·재작성 라운드·style_judge
판정 전문·humanize before/after·rerender 후보/평가·이벤트 타임라인·실패 요약)과 FI-1 이 같은 사이드카에
append 하는 작가 의도 이벤트(kind='author_intent'), 그리고 ChapterRecord.revisions·ProjectState.regen_events
를 **사람이 읽는 시간순 타임라인**으로 렌더한다.

  · GA-2: 회차 트레이스 샤드의 generate/rerender/rerender_pipeline run 을 시간순 렌더(수술 diff 요약·판정 이유·
          이벤트 타임라인·실패 하이라이트).
  · FI-2: 위 위에 author_intent kind 렌더 + **3원천 조인**(trace runs + revisions + regen_events)을 ts 기준 병합
          + 필터(회차/actor/kind/surface). 원문 인용 나열(measure-then-cite) — 비율 지표·헤드라인 수치 0.

의존성 0: novelcopilot 패키지를 임포트하지 않는다(pydantic 등 유입 없음) — 트레이스·프로젝트 JSON 을 stdlib
json 으로 직접 읽고, 데이터 디렉터리는 config.resolved_data_dir 규칙을 그대로 미러(NOVEL_DATA_DIR 우선,
없으면 app/data). 어떤 경로도 상태·트레이스를 변형하지 않는다(순수 읽기).

사용:
  py -3.12 -X utf8 tools/trace_view.py <pid> <ch>                 # 기본 = 그 회차 전체 타임라인(3원천 조인)
  py -3.12 -X utf8 tools/trace_view.py <pid> <ch> --kind author_intent   # kind 필터
  py -3.12 -X utf8 tools/trace_view.py <pid> <ch> --actor author         # actor 필터
  py -3.12 -X utf8 tools/trace_view.py <pid> 0 --surface bible_edit      # ch=0 작품 스코프(설정집·엔티티·스파인 등)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path


# ══════════════════ 위치 확인 · 순수 읽기 로더(stdlib) ══════════════════

def _data_dir() -> Path:
    """config.Settings.resolved_data_dir 규칙 미러 — NOVEL_DATA_DIR 우선, 없으면 app/data.
    (tools/trace_view.py → parent.parent = app/ → app/data. config.py 는 novelcopilot/config.py 라 동일 app/data 를 가리킴)."""
    env = os.environ.get("NOVEL_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data"


def _projects_dir() -> Path:
    return _data_dir() / "projects"


def _load_trace(pid: str, ch: int) -> dict | None:
    """회차 트레이스 사이드카를 읽는다(없거나 손상이면 None — filesystem.load_trace 와 동일 계약·읽기 전용)."""
    p = _projects_dir() / f"{pid}.trace.{ch}.json"
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _load_project(pid: str) -> dict | None:
    """프로젝트 본체 JSON 을 stdlib json 으로 읽는다(revisions·regen_events 조인 재료). 읽기 전용·pydantic 미사용."""
    p = _projects_dir() / f"{pid}.json"
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


# ══════════════════ 공용 렌더 헬퍼 ══════════════════

def _excerpt(s, limit: int = 160) -> str:
    """긴 문자열을 발췌로(전문 덤프 아님 — 길이·발췌). 개행은 ⏎ 로 접어 한 줄 유지. 문자(코드포인트) 단위."""
    s = "" if s is None else str(s)
    s = s.replace("\r\n", "\n").replace("\n", " ⏎ ")
    return s if len(s) <= limit else s[:limit] + f" …(+{len(s) - limit}자)"


def _core_diff(before, after) -> dict:
    """공통 접두/접미를 벗긴 '변경 코어'만 남기는 결정론 순수 함수(telemetry.revise_core_diff 의 뷰어판 — 의존성 0
    이라 재구현). 전문 덤프 대신 바뀐 대목과 길이만 보여 준다. 문자 단위라 한글 경계 안전."""
    before, after = ("" if before is None else str(before)), ("" if after is None else str(after))
    bl, al = len(before), len(after)
    m = min(bl, al)
    i = 0
    while i < m and before[i] == after[i]:
        i += 1
    j = 0
    while j < (m - i) and before[bl - 1 - j] == after[al - 1 - j]:
        j += 1
    return {"before_len": bl, "after_len": al, "core_offset": i,
            "core_before": before[i: bl - j], "core_after": after[i: al - j]}


def _diff_line(before, after, prefix: str = "  ") -> list[str]:
    """first_draft→final 등 수술 diff 요약 렌더(길이 델타 + 변경 코어 발췌 — 전문 덤프 금지)."""
    d = _core_diff(before, after)
    if d["core_before"] == d["core_after"]:
        return [f"{prefix}변경 없음 (길이 {d['before_len']}자)"]
    out = [f"{prefix}수술: 길이 {d['before_len']}→{d['after_len']}자 · 변경 코어 @{d['core_offset']}"]
    out.append(f"{prefix}  - {_excerpt(d['core_before'])}")
    out.append(f"{prefix}  + {_excerpt(d['core_after'])}")
    return out


def _ts_key(ts) -> str:
    """조인 정렬 키 정규화(FI-2): ts 의 **tz 오프셋(±HHMM/±HH:MM/Z)만 벗겨** 로컬 문자열로 만든다.

    기존 레코드(naive "%Y-%m-%dT%H:%M:%S")와 FI-1 신규(TZ-aware "%z" 포함)가 혼재하므로, 산술 변환 없이
    문자열에서 tz 부분만 제거해 같은 축(로컬 벽시계 문자열)으로 비교한다(결정론·최소 변환). 빈 ts 는 ""→맨 앞.
    같은 키는 안정 정렬(파이썬 sorted 안정성)이 소스 삽입 순서를 보존한다."""
    if not ts:
        return ""
    s = str(ts).strip()
    if s.endswith("Z"):
        return s[:-1]
    m = re.search(r"[+-]\d{2}:?\d{2}$", s)
    return s[:m.start()] if m else s


# ══════════════════ GA-2: 트레이스 run 렌더(generate/rerender/rerender_pipeline) ══════════════════

def _render_generate(run: dict) -> list[str]:
    """생성 run — 스테이지별 판단/행위: 첫 초안→라운드→최종 수술 diff 요약, style_judge 판정 이유·인용,
    humanize before/after 발췌, 이벤트 타임라인, 실패 하이라이트."""
    out: list[str] = []
    st = run.get("status") or "?"
    gn = run.get("gen_no")
    head = f"■ 생성 (generate) — {st}"
    if gn is not None:
        head += f" · 세대 {gn}"
    out.append(head)
    # 첫 초안 → 최종본 수술 diff(전문 덤프 아님)
    fd, ft = run.get("first_draft"), run.get("final_text")
    if fd is not None or ft is not None:
        out += _diff_line(fd, ft, prefix="    ")
    # 재작성 라운드(카운트+사유 — RoundTrace 는 카운트만이라 trace 가 전문 보유)
    rounds = run.get("rewrite_rounds") or []
    if rounds:
        out.append(f"    재작성 라운드 {len(rounds)}회:")
        for r in rounds:
            fixing = ", ".join(str(x) for x in (r.get("fixing") or [])) or "—"
            out.append(f"      round{r.get('round')}: 교정 대상=[{fixing}] · 산출 {len(str(r.get('text') or ''))}자")
    # style_judge 판정 전문(이유·인용)
    sj = run.get("style_judgment")
    if isinstance(sj, dict):
        out.append(f"    style_judge 판정: needs_repair={sj.get('needs_repair')} · 이유: {_excerpt(sj.get('reason'), 120)}")
        for sp in (sj.get("spans") or []):
            out.append(f"      인용: “{_excerpt(sp.get('quote'), 80)}” — {_excerpt(sp.get('why'), 80)}")
        for sp in (sj.get("motif_spans") or []):
            out.append(f"      모티프: “{_excerpt(sp.get('quote'), 80)}” — {_excerpt(sp.get('why'), 80)}")
    # humanize 스팬 before/after 발췌
    hs = run.get("humanize_spans") or []
    if hs:
        out.append(f"    휴머나이즈 스팬 {len(hs)}건:")
        for e in hs:
            cat = f"{e.get('category') or '?'}/{e.get('severity') or '?'}"
            chg = "변경" if e.get("changed") else "유지"
            line = f"      [{cat}] {chg}"
            if e.get("author_review"):
                line += " · ⚑작가 확인 요망"
            out.append(line)
            b, a = e.get("before"), e.get("after")
            if b is not None:
                out.append(f"        - {_excerpt(b, 100)}")
            if a is not None and a != b:
                out.append(f"        + {_excerpt(a, 100)}")
            elif e.get("note"):
                out.append(f"        · {_excerpt(e.get('note'), 100)}")
    # 이벤트 타임라인 전량(순서·payload)
    evs = run.get("events") or []
    if evs:
        out.append(f"    이벤트 타임라인 {len(evs)}건:")
        for ev in evs:
            extra = {k: v for k, v in ev.items() if k not in ("seq", "node", "event", "chapter", "ts")}
            xs = (" · " + _excerpt(json.dumps(extra, ensure_ascii=False), 120)) if extra else ""
            out.append(f"      {ev.get('seq'):>3} {ev.get('node')}.{ev.get('event')}{xs}")
    # 실패 하이라이트(승격)
    fails = run.get("failures") or []
    if fails:
        out.append(f"    ⚠ 실패 {len(fails)}건:")
        for f in fails:
            xs = {k: v for k, v in f.items() if k not in ("node", "event", "chapter", "ts")}
            out.append(f"      ⚠ {f.get('node')}.{f.get('event')} · {_excerpt(json.dumps(xs, ensure_ascii=False), 140)}")
    return out


def _render_rerender(run: dict) -> list[str]:
    """재실현 run — 후보·리랭크 eval·가드·정독 요지."""
    out: list[str] = []
    adopted = run.get("adopted")
    out.append(f"■ 재실현 (rerender) — mode={run.get('mode')} · 채택={adopted} · 사유: {_excerpt(run.get('reason'), 120)}")
    cands = run.get("candidates") or []
    out.append(f"    후보 {len(cands)}개 · 승자 index={run.get('winner_index')}")
    for ev in (run.get("evaluations") or []):
        dq = "실격" if ev.get("disqualified") else "통과"
        reasons = ", ".join(str(x) for x in (ev.get("reasons") or [])) or "—"
        metrics = " · ".join(f"{k}={ev.get(k)}" for k in ("top_ratio", "max_run", "da_ratio", "uninterrupted_run_max")
                             if ev.get(k) is not None)
        out.append(f"      후보{ev.get('index')}: {dq} [{reasons}]" + (f" · {metrics}" if metrics else ""))
    g = run.get("guardrail")
    if isinstance(g, dict):
        out.append(f"    가드: passed={g.get('passed')} · {_excerpt(g.get('reason'), 100)}")
    rg = run.get("read_gate")
    if isinstance(rg, dict):
        out.append(f"    정독: adopt={rg.get('adopt')} · {_excerpt(rg.get('reason'), 100)}")
    fr = run.get("finalize_repairs")
    if isinstance(fr, dict) and fr:
        out.append(f"    최종화 수리: {_excerpt(json.dumps(fr, ensure_ascii=False), 120)}")
    return out


def _render_rerender_pipeline(run: dict) -> list[str]:
    """재실현 훅 라이프사이클 이벤트(start/skip/adopted/rejected/failure) — RO-1 관측 영속."""
    ev = run.get("event") or "?"
    fields = {k: v for k, v in run.items() if k not in ("kind", "event", "ts", "chapter", "traceback")}
    line = f"■ 재실현 훅 (rerender_pipeline) — {ev}"
    if fields:
        line += " · " + _excerpt(json.dumps(fields, ensure_ascii=False), 140)
    out = [("⚠ " + line) if ev == "failure" else line]
    tb = run.get("traceback")
    if tb:
        out.append(f"    traceback(tail): {_excerpt(tb, 200)}")
    return out


# ══════════════════ FI-2: author_intent · revision · regen 렌더 + 조인 ══════════════════

def _render_author_intent(run: dict) -> list[str]:
    """작가 의도 이벤트(FI-1) — surface 별 원문 인용 나열(measure-then-cite). 비율·판정 언어 0."""
    surface = run.get("surface") or "?"
    actor = run.get("actor") or "?"
    gn = run.get("gen_no")
    head = f"● 작가 의도 (author_intent) — {surface} · actor={actor}"
    if gn is not None:
        head += f" · 세대 {gn}"
    out = [head]
    p = run.get("payload") or {}
    # 지시·구간 원문 인용(있으면)
    if p.get("directive"):
        out.append(f"    지시: “{_excerpt(p.get('directive'), 200)}”")
    if p.get("span_text"):
        out.append(f"    구간: “{_excerpt(p.get('span_text'), 160)}”")
    if p.get("note"):
        out.append(f"    메모: “{_excerpt(p.get('note'), 200)}”")
    if p.get("reason"):
        out.append(f"    사유: {_excerpt(p.get('reason'), 160)}")
    # revise_propose 변경 코어(기각 후보의 산출 — 전문 아님)
    core = p.get("core")
    if isinstance(core, dict):
        gp = p.get("guardrail_passed")
        out.append(f"    가드 passed={gp} · 변경 코어 @{core.get('core_offset')} 길이 {core.get('before_len')}→{core.get('after_len')}자")
        if core.get("core_before") or core.get("core_after"):
            out.append(f"      - {_excerpt(core.get('core_before'), 120)}")
            out.append(f"      + {_excerpt(core.get('core_after'), 120)}")
    # 캐논 정정 표면(bible/entity/relation/spine 등) — 잔여 payload 키 원문 나열(발명 0)
    shown = {"directive", "span_text", "note", "reason", "core", "guardrail_passed", "passes", "latency_sec"}
    rest = {k: v for k, v in p.items() if k not in shown}
    if rest:
        out.append(f"    상세: {_excerpt(json.dumps(rest, ensure_ascii=False), 200)}")
    ref = run.get("ref") or {}
    if ref:
        out.append(f"    참조: {_excerpt(json.dumps(ref, ensure_ascii=False), 100)}")
    return out


def _render_revision(rev: dict, event: str) -> list[str]:
    """퇴고 이력(ChapterRevision) — 채택/되돌림. before/after 전문 대신 지시+변경 코어 발췌(measure-then-cite)."""
    rid = rev.get("revision_id")
    if event == "undo":
        return [f"◆ 퇴고 되돌림 (revision undo) — id={rid}"]
    gp = rev.get("guardrail_passed")
    out = [f"◆ 퇴고 채택 (revision) — id={rid} · 가드 passed={gp}"]
    if rev.get("directive"):
        out.append(f"    지시: “{_excerpt(rev.get('directive'), 200)}”")
    if rev.get("span_text"):
        out.append(f"    구간: “{_excerpt(rev.get('span_text'), 160)}”")
    out += _diff_line(rev.get("before_text"), rev.get("after_text"), prefix="    ")
    return out


def _render_regen(ev: dict) -> list[str]:
    """재생성 실행 로그(RegenEvent) — 순수 계측(판정 없음)."""
    fx = "점검 반영" if ev.get("fix_selected") else "단순 재생성"
    return [f"◇ 재생성 실행 (regen) — seq={ev.get('seq')} · {fx}"]


def _build_timeline(pid: str, ch: int) -> list[dict]:
    """3원천 조인(FI-2): trace runs(author_intent 포함) + ChapterRecord.revisions + ProjectState.regen_events.

    각 항목 = {sort_key, ts, kind, actor, surface, lines}. 정렬 키는 _ts_key(tz 벗김)로 정규화하고 안정 정렬한다.
    (GA-2 단독 렌더도 이 목록에서 trace kind 만 취하면 되지만, 조인은 FI-2 소관이라 이 함수가 세 원천을 모은다.)"""
    items: list[dict] = []
    # 원천 1: 트레이스 샤드 runs(generate/rerender/rerender_pipeline/author_intent)
    doc = _load_trace(pid, ch)
    for run in ((doc or {}).get("runs") or []):
        kind = run.get("kind") or "generate"   # 구 generate run 은 kind 없을 수 있음 → generate 로 간주(사이드카 관례)
        if kind == "generate":
            lines = _render_generate(run)
        elif kind == "rerender":
            lines = _render_rerender(run)
        elif kind == "rerender_pipeline":
            lines = _render_rerender_pipeline(run)
        elif kind == "author_intent":
            lines = _render_author_intent(run)
        else:
            lines = [f"■ (알 수 없는 kind={kind})"]
        items.append({"sort_key": _ts_key(run.get("ts")), "ts": run.get("ts") or "", "kind": kind,
                      "actor": run.get("actor"), "surface": run.get("surface"), "lines": lines})
    # 원천 2·3: 프로젝트 본체(revisions·regen_events)
    proj = _load_project(pid)
    if proj:
        rec = next((c for c in (proj.get("chapters") or []) if c.get("chapter") == ch), None)
        if rec:
            for rev in (rec.get("revisions") or []):
                items.append({"sort_key": _ts_key(rev.get("created_at")), "ts": rev.get("created_at") or "",
                              "kind": "revision", "actor": "author", "surface": None,
                              "lines": _render_revision(rev, "accept")})
                if rev.get("reverted_at"):
                    items.append({"sort_key": _ts_key(rev.get("reverted_at")), "ts": rev.get("reverted_at") or "",
                                  "kind": "revision", "actor": "author", "surface": None,
                                  "lines": _render_revision(rev, "undo")})
        for ev in (proj.get("regen_events") or []):
            if ev.get("chapter") == ch:
                items.append({"sort_key": _ts_key(ev.get("at")), "ts": ev.get("at") or "",
                              "kind": "regen", "actor": "author", "surface": None,
                              "lines": _render_regen(ev)})
    # 안정 정렬(같은 키=소스 삽입 순서 보존)
    items.sort(key=lambda it: it["sort_key"])
    return items


# ══════════════════ CLI ══════════════════

def _apply_filters(items: list[dict], *, kind: str | None, actor: str | None, surface: str | None) -> list[dict]:
    """필터: kind/actor/surface. 필터 지정 시 그 필드가 일치하는 항목만(actor/surface 미보유 항목은 해당 필터 지정 시 제외 — 정직)."""
    out = items
    if kind:
        out = [it for it in out if it["kind"] == kind]
    if actor:
        out = [it for it in out if it.get("actor") == actor]
    if surface:
        out = [it for it in out if it.get("surface") == surface]
    return out


def render(pid: str, ch: int, *, kind=None, actor=None, surface=None) -> str:
    """타임라인 전체를 문자열로 렌더(테스트 스모크용 공개 함수)."""
    items = _apply_filters(_build_timeline(pid, ch), kind=kind, actor=actor, surface=surface)
    lines: list[str] = []
    lines.append(f"═══ 트레이스 타임라인 — 작품 {pid} · {ch}화 ═══")
    filt = [f"{k}={v}" for k, v in (("kind", kind), ("actor", actor), ("surface", surface)) if v]
    lines.append(f"필터 결과 {len(items)}건" + (f" (필터: {', '.join(filt)})" if filt else ""))
    if _load_trace(pid, ch) is None and not any(it["kind"] in ("revision", "regen") for it in items):
        lines.append("(이 회차 트레이스 사이드카 없음 — GA-1 도입 전 생성이거나 기록 미수집)")
    lines.append("")
    for it in items:
        lines.append(f"[{it['ts'] or '시각미상'}]")
        lines += it["lines"]
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows 콘솔 UTF-8(‑X utf8 미지정 대비)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="생성 트레이스 디버그 뷰어(읽기 전용·LLM 0·의존성 0)")
    ap.add_argument("pid", help="프로젝트 id")
    ap.add_argument("ch", type=int, help="회차 번호(0=작품 스코프 샤드 — 설정집·엔티티·스파인 등 회차 밖 이벤트)")
    ap.add_argument("--kind", default=None,
                    help="kind 필터: generate|rerender|rerender_pipeline|author_intent|revision|regen")
    ap.add_argument("--actor", default=None, help="actor 필터: author|tool")
    ap.add_argument("--surface", default=None,
                    help="author_intent surface 필터: revise_propose|revise_accept|direct_edit|bible_edit|…")
    args = ap.parse_args(argv)
    print(render(args.pid, args.ch, kind=args.kind, actor=args.actor, surface=args.surface))
    return 0


if __name__ == "__main__":
    sys.exit(main())
