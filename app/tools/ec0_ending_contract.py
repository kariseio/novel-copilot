# -*- coding: utf-8 -*-
"""EC-0 엔딩 술어계약 소급 PoC — 신규 도구(엔진 코드 무변경 · 라이브 데이터 읽기 전용).

무엇: 각 작품의 EndingSpec 프로즈(central_question/ending/thematic_payoff)를 LLM 1콜로
'결정론 술어' 3~7개로 컴파일하고, 그 술어를 **기존 결정론 평가기**(LLM 0콜)로 평가해
"엔딩이 결정론 계약으로 얼마나 표현·평가 가능한가"(커버리지)를 측정한다. 순수 측정/advisory —
어떤 게이트·자동반응에도 연결되지 않는다(무강제).

허용 술어 타입(화이트리스트, 닫힌 집합 — 이외 전부 거부):
  attr_equals / attr_in     → Ontology.binding_state_as_of(gt만) · state_as_of(ni 포함)
  edge_active / edge_absent → Ontology.edges_as_of(tier 필터, pov=None 객관 엣지만)
  terminal / alive          → Ontology._in_terminal_state 의미론(tier 모드별 lookup 치환)
  promise_paid              → PromiseLedger(비구속 회계 — tier 무관, 두 모드 동일)
  clock_at_least            → story_clock.elapsed_minutes(tier 무관, 두 모드 동일)

검증: 미등록 eid/attr/rel_id/통제어휘 밖 값/미등록 promise_id 는 거부 카운트 → 교정 재호출 1회.
  값 어휘는 '선언 vocab/states ∪ SSOT 관측값'(라이브 데이터가 numeric/status 에 자유 텍스트를
  이미 보유 — 관측값은 SSOT 에 등록된 사실이므로 결정론 비교 가능. 리포트에 명시).

trust-tier 2모드: ground_truth_only(작가/시드 확정만) vs with_narrative_inferred(기계추출 포함).
판정 기준: 술어 커버리지 < 60% → '추출 보강 선행' 승격 권고를 리포트에 명시.
주의: 대상 작품이 전부 미완결 → satisfied 분포는 참고 지표(목적은 '표현 가능성' 측정).

사용: (app/ 에서)  python tools/ec0_ending_contract.py [--offline] [--project ID] [--out DIR]
리포트: app/tools/reports/ec0_report.json + ec0_report.md (gitignore 대상 산출물)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.project import ProjectState                      # noqa: E402
from novelcopilot.domain.types import ChapterStatus                       # noqa: E402
from novelcopilot.engine.factory import build_ontology                    # noqa: E402
from novelcopilot.engine.ontology import Ontology, Entity                 # noqa: E402
from novelcopilot.engine.vocabulary import Vocabulary                     # noqa: E402
# EC-1: 공유 로직은 engine/ending_contract 로 승격 — 이 도구는 소급 리포트 오케스트레이션만 유지(분기 금지).
#  주의: 승격판 평가기는 blocked 를 보수 규칙 3종(terminal 충돌·CN-4 상한0·약속 소멸)으로 확장했다(EC-1).
from novelcopilot.engine.ending_contract import (                          # noqa: E402
    PREDICATE_TYPES, TIER_GT, TIER_ALL,
    build_registry, validate_predicate, compile_predicates, evaluate_predicate,
)

COVERAGE_FLOOR = 0.60          # 미만 → '추출 보강 선행' 승격 권고(EC-0 리포트 전용 기준)


# ---------------------------------------------------------------- 로드(읽기 전용)
def load_states(projects_dir: pathlib.Path, only_id: str = "") -> list[ProjectState]:
    """핫패스 JSON 만 직접 파싱(rag 사이드카/임베딩 미로드 — 본 도구엔 불필요). 저장 API 를 아예 안 쓴다."""
    out: list[ProjectState] = []
    for p in sorted(projects_dir.glob("*.json")):
        if ".rag." in p.name:
            continue
        if only_id and not p.stem.startswith(only_id):
            continue
        try:
            out.append(ProjectState.model_validate_json(p.read_text(encoding="utf-8")))
        except Exception as e:                                  # 손상 파일은 건너뛰되 가시화
            print(f"[skip] {p.name}: {e}")
    return out


def build_project_ontology(state: ProjectState) -> Ontology:
    """factory.build_ontology + (factory 의 seed_edges 검증 적재) + (session.rehydrate 의 runtime 병합)
    을 재현한 읽기 전용 재수화 — 엔진 코드 무변경(provider 불필요 경로만 사용)."""
    vocab = Vocabulary.from_world(state.world)
    ont = build_ontology(state.world, vocab)
    # seed_edges: factory.build_engine 과 동치 검증(끝점 존재·self-loop·opt-in 타입 제약·대칭 정규화)
    for e in state.world.seed_edges:
        rspec = ont.rel_spec(e.rel_id)
        valid = (e.src_id in ont.entities and e.dst_id in ont.entities and e.src_id != e.dst_id)
        if valid and rspec.allowed_src_types and ont.entities[e.src_id].etype not in rspec.allowed_src_types:
            valid = False
        if valid and rspec.allowed_dst_types and ont.entities[e.dst_id].etype not in rspec.allowed_dst_types:
            valid = False
        if valid:
            s_id, d_id = ont.order_edge(e.rel_id, e.src_id, e.dst_id)
            if (s_id, d_id) != (e.src_id, e.dst_id) or not e.edge_id:
                e = e.model_copy(update={"src_id": s_id, "dst_id": d_id,
                                         "edge_id": e.edge_id or f"{e.rel_id}:{s_id}->{d_id}:{e.eff_from}"})
            ont.add_edge(e)
    # runtime 병합: session.rehydrate 와 동치(엔티티→timeline(tier 보존)→edges dedup)
    for es in state.runtime_entities:
        if es.id not in ont.entities:
            ont.add(Entity(id=es.id, name=es.name, etype=es.etype, attrs=dict(es.attrs),
                           aliases=list(es.aliases), base_status=es.base_status,
                           voice=getattr(es, "voice", ""), provisional=es.provisional,
                           cardinality=dict(getattr(es, "cardinality", None) or {})))
    for t in state.runtime_timeline:
        ont.set_state(t.entity_id, t.attr, t.value, t.eff_from, reason=t.reason,
                      trust_tier=getattr(t, "trust_tier", "ground_truth"))
    for edge in state.runtime_edges:
        if not any(x.edge_id == edge.edge_id for x in ont.edges):
            ont.add_edge(edge)
    return ont


# ---------------------------------------------------------------- 작품 단위 분석 + 리포트
def analyze_project(state: ProjectState, provider=None) -> dict:
    ont = build_project_ontology(state)
    registry = build_registry(state, ont)
    ending = (state.world.spine.ending.model_dump()
              if state.world.spine and state.world.spine.ending else {})
    eval_chapter = max(1, state.current_chapter)                  # 0화 작품은 시드 상태(1화 시점) 평가
    clock_deltas = [c.time_delta for c in sorted(state.chapters, key=lambda c: c.chapter)
                    if c.status == ChapterStatus.FINALIZED]
    allow_rev = bool(state.world.allow_state_reversal)

    has_ending = any((ending.get(k) or "").strip()
                     for k in ("central_question", "ending", "thematic_payoff"))
    if not has_ending:
        compiled = {"predicates": [], "passes": [], "adopted_pass": 0, "corrective_used": False,
                    "llm_calls": 0, "error": "no_ending_spec", "corrective_error": ""}
    elif provider is None:
        compiled = {"predicates": [], "passes": [], "adopted_pass": 0, "corrective_used": False,
                    "llm_calls": 0, "error": "llm_unavailable(offline)", "corrective_error": ""}
    else:
        compiled = compile_predicates(provider, ending, registry)

    preds_out = []
    dist = {"ground_truth_only": {"satisfied": 0, "open": 0, "blocked": 0},
            "with_narrative_inferred": {"satisfied": 0, "open": 0, "blocked": 0}}
    for pred in compiled["predicates"]:
        ev_gt = evaluate_predicate(pred, ont, state.promise_ledger, clock_deltas,
                                   eval_chapter, TIER_GT, allow_rev)
        ev_ni = evaluate_predicate(pred, ont, state.promise_ledger, clock_deltas,
                                   eval_chapter, TIER_ALL, allow_rev)
        dist["ground_truth_only"][ev_gt["result"]] += 1
        dist["with_narrative_inferred"][ev_ni["result"]] += 1
        preds_out.append({**pred, "eval": {"ground_truth_only": ev_gt,
                                           "with_narrative_inferred": ev_ni}})

    passes = compiled["passes"]
    adopted = (passes[compiled["adopted_pass"]] if passes
               else {"proposed": 0, "valid": 0, "rejected": []})   # 커버리지 분모 = 채택된 패스의 제안 수
    proposed_total = sum(p["proposed"] for p in passes)
    rejected_total = sum(len(p["rejected"]) for p in passes)
    coverage = (len(compiled["predicates"]) / adopted["proposed"]) if adopted["proposed"] else 0.0
    return {
        "id": state.id, "title": state.world.title, "genre": state.world.genre,
        "current_chapter": state.current_chapter, "eval_chapter": eval_chapter,
        "has_ending_spec": has_ending, "allow_state_reversal": allow_rev,
        "registry_size": {"entities": len(registry["entities"]), "attrs": len(registry["attrs"]),
                          "rel_ids": len(registry["rel_ids"]), "promises": len(registry["promises"])},
        "compile": {"llm_calls": compiled["llm_calls"], "corrective_used": compiled["corrective_used"],
                    "error": compiled["error"], "corrective_error": compiled["corrective_error"],
                    "passes": passes,
                    "adopted_pass": compiled["adopted_pass"], "adopted_proposed": adopted["proposed"],
                    "proposed_total": proposed_total, "rejected_total": rejected_total,
                    "error_rate": (rejected_total / proposed_total) if proposed_total else None},
        "coverage": round(coverage, 3),
        # 승격 권고는 '컴파일이 실제로 수행된' 작품만 — 오프라인/콜 실패의 coverage=0.0 은 무의미
        # (인프라 장애가 판정 출력을 오염시키지 않게 소스 차단).
        "escalate_extraction_first": bool(has_ending and not compiled["error"]
                                          and coverage < COVERAGE_FLOOR),
        "tier_distribution": dist,
        "predicates": preds_out,
    }


def run(states: list[ProjectState], provider=None) -> dict:
    """전 작품 분석 → 리포트 dict(순수 함수 — 파일/네트워크 부작용은 main/write_reports 만)."""
    projects = [analyze_project(s, provider) for s in states]
    n_adopted_proposed = sum(p["compile"]["adopted_proposed"] for p in projects)
    n_valid = sum(len(p["predicates"]) for p in projects)
    proposed_total = sum(p["compile"]["proposed_total"] for p in projects)
    rejected_total = sum(p["compile"]["rejected_total"] for p in projects)
    agg = {"ground_truth_only": {"satisfied": 0, "open": 0, "blocked": 0},
           "with_narrative_inferred": {"satisfied": 0, "open": 0, "blocked": 0}}
    for p in projects:
        for mode in agg:
            for k in agg[mode]:
                agg[mode][k] += p["tier_distribution"][mode][k]
    coverage_overall = (n_valid / n_adopted_proposed) if n_adopted_proposed else 0.0
    llm_executed = any(p["compile"]["llm_calls"] > 0 for p in projects)
    notes = [
        "본 리포트는 순수 측정/advisory — 어떤 게이트·자동반응에도 연결되지 않는다(무강제).",
        "대상 작품은 전부 미완결 → satisfied/open/blocked 분포는 참고 지표"
        "(목적은 엔딩의 '결정론 표현 가능성' 측정이지 이행 판정이 아님).",
        "값 어휘 검증은 '선언 vocab/states ∪ SSOT 관측값' — 라이브 데이터가 numeric/status 에 "
        "자유 텍스트 값을 이미 보유하므로 관측값도 등록된 사실로 인정(어휘 밖 신조어만 거부).",
        "promise_paid(원장)·clock_at_least(스토리시계)는 trust-tier 무관 — 두 모드 동일 값.",
        f"판정 기준: 커버리지 {COVERAGE_FLOOR:.0%} 미만 작품은 '추출 보강 선행' 승격 권고.",
    ]
    if not llm_executed:
        notes.insert(0, "LLM 컴파일 미실행(오프라인/키 부재/호출 실패) — 컴파일러·평가기 코드는 구현 완료, "
                        "커버리지 수치는 실행 후에만 유효.")
    recommendation = ""
    flagged = [p["title"] for p in projects if p["escalate_extraction_first"]]
    if llm_executed and (coverage_overall < COVERAGE_FLOOR or flagged):
        recommendation = ("커버리지 미달(<60%) — '추출 보강 선행' 승격 권고. 대상: "
                          + (", ".join(flagged) if flagged else "전체(집계 기준)"))
    elif llm_executed:
        recommendation = "커버리지 60% 이상 — 술어계약 본구현(EC-1) 진행 가능 신호(참고 지표)."
    return {
        "report": "EC-0 엔딩 술어계약 소급 PoC",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "llm_executed": llm_executed,
        "coverage_floor": COVERAGE_FLOOR,
        "notes": notes,
        "summary": {
            "projects": len(projects),
            "predicates_proposed_total": proposed_total,
            "predicates_valid": n_valid,
            "rejected_total": rejected_total,
            "compile_error_rate": round(rejected_total / proposed_total, 3) if proposed_total else None,
            "coverage_overall": round(coverage_overall, 3),
            "tier_distribution": agg,
            "recommendation": recommendation,
        },
        "projects": projects,
    }


def render_md(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# EC-0 엔딩 술어계약 소급 PoC 리포트",
        "",
        f"- 생성: {report['generated_at']} · LLM 실행: {'예' if report['llm_executed'] else '아니오'}",
        f"- 작품 {s['projects']}개 · 술어 제안 {s['predicates_proposed_total']} · 유효 {s['predicates_valid']}"
        f" · 거부 {s['rejected_total']} (컴파일 오류율 {s['compile_error_rate']})",
        f"- 전체 커버리지(결정론 평가 가능 비율): **{s['coverage_overall']:.1%}** (기준선 {report['coverage_floor']:.0%})",
        f"- 권고: {s['recommendation'] or '(해당 없음)'}",
        "",
        "## 주의",
    ]
    lines += [f"- {n}" for n in report["notes"]]
    lines += ["", "## 작품별", "",
              "| 작품 | 회차 | 제안(채택패스) | 유효 | 거부(누적) | 커버리지 | gt(만족/미결/봉쇄) | ni포함(만족/미결/봉쇄) | 권고 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for p in report["projects"]:
        g = p["tier_distribution"]["ground_truth_only"]
        n = p["tier_distribution"]["with_narrative_inferred"]
        rec = "추출 보강 선행" if p["escalate_extraction_first"] else "-"
        if p["compile"]["error"]:
            rec = f"컴파일 실패: {p['compile']['error'][:40]}"
        lines.append(f"| {p['title']} | {p['current_chapter']} | {p['compile']['adopted_proposed']} | {len(p['predicates'])} "
                     f"| {p['compile']['rejected_total']} | {p['coverage']:.0%} "
                     f"| {g['satisfied']}/{g['open']}/{g['blocked']} | {n['satisfied']}/{n['open']}/{n['blocked']} | {rec} |")
    lines.append("")
    for p in report["projects"]:
        lines += [f"### {p['title']} ({p['id']})", ""]
        if p["compile"]["error"]:
            lines += [f"- 컴파일: {p['compile']['error']}", ""]
        if p["compile"].get("corrective_error"):
            lines += [f"- 교정 콜 실패(1차 유효분 유지): {p['compile']['corrective_error']}", ""]
        for pr in p["predicates"]:
            gt = pr["eval"]["ground_truth_only"]
            ni = pr["eval"]["with_narrative_inferred"]
            params = {k: v for k, v in pr.items() if k not in ("type", "note", "eval")}
            lines.append(f"- `{pr['type']}` {json.dumps(params, ensure_ascii=False)}"
                         f" → gt:{gt['result']}({gt['observed']}) · ni:{ni['result']}({ni['observed']})"
                         + (f" — {pr['note']}" if pr.get("note") else ""))
        for ps in p["compile"]["passes"]:
            for r in ps["rejected"]:
                lines.append(f"- 거부: {json.dumps(r['raw'], ensure_ascii=False)[:120]} → {r['reason']}")
        lines.append("")
    return "\n".join(lines)


def write_reports(report: dict, out_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / "ec0_report.json"
    mp = out_dir / "ec0_report.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_md(report), encoding="utf-8")
    return jp, mp


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")                  # Windows cp949 콘솔 방어
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="EC-0 엔딩 술어계약 소급 PoC(읽기 전용)")
    ap.add_argument("--offline", action="store_true", help="LLM 호출 없이 실행(컴파일 생략을 리포트에 명시)")
    ap.add_argument("--project", default="", help="특정 프로젝트 id(접두) 만")
    ap.add_argument("--out", default="", help="리포트 출력 디렉토리(기본 tools/reports)")
    args = ap.parse_args(argv)

    app_dir = pathlib.Path(__file__).resolve().parents[1]
    states = load_states(app_dir / "data" / "projects", only_id=args.project)
    if not states:
        print("대상 프로젝트 없음")
        return 1

    provider = None
    if not args.offline:
        try:
            from novelcopilot.config import get_settings
            from novelcopilot.llm.factory import create_provider
            provider = create_provider(get_settings())            # 기존 provider 설정 재사용
        except Exception as e:
            print(f"[warn] provider 생성 실패 → 오프라인 모드로 전환: {e}")

    report = run(states, provider)
    out_dir = pathlib.Path(args.out) if args.out else (app_dir / "tools" / "reports")
    jp, mp = write_reports(report, out_dir)
    s = report["summary"]
    print(f"작품 {s['projects']} · 유효 술어 {s['predicates_valid']}/{s['predicates_proposed_total']}"
          f" · 커버리지 {s['coverage_overall']:.1%} · 거부 {s['rejected_total']}")
    print(f"권고: {s['recommendation'] or '(없음)'}")
    print(f"리포트: {jp}\n        {mp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
