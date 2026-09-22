# -*- coding: utf-8 -*-
"""ON-2 U2 — 확정 단계 상태 diff 승인 CLI. LLM 0.

설계(보드 ON-2): 승인 기구를 새로 만들지 않는다 — 커밋은 전부 기존 `set_entity_state`(가드·dedup·FI-1
내장, provenance=author)를 경유하고, 병합은 ontology_ops.merge_entity 를 쓴다. 이 CLI 는 **밥상과 실행**만.
자동 없음: --list 는 읽기 전용, 변경은 사람이 항목을 지정할 때만.

용법:
  py -3.12 -X utf8 tools/approve_state.py --pid P --chapter 12 --list
  py -3.12 -X utf8 tools/approve_state.py --pid P --approve junho:truth_awareness        # 기계값 그대로 작가 승인
  py -3.12 -X utf8 tools/approve_state.py --pid P --set "junho:money_won=12000@13"      # 작가 값 직접
  py -3.12 -X utf8 tools/approve_state.py --pid P --merge npc_13=jiyeon                 # 유령 병합
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.config import get_settings
from novelcopilot.repository.filesystem import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.ontology_ops import merge_entity, validate_timeline


def _similar_canon(state, name: str) -> list[str]:
    """신규 개체 제안 옆에 병기할 기존 정본 유사 후보 — 자동 병합 아님(교착어 함정), 사람 눈용 병기만."""
    out = []
    frag = (name or "").strip()
    for e in state.world.entities:
        pool = [e.name] + list(e.aliases or [])
        if any(f and (f in frag or frag in f) for f in pool if len(f) >= 2):
            out.append(f"{e.id}({e.name})")
    return out


def cmd_list(svc, st, chapter: int) -> None:
    print(f"# 상태 diff 승인 밥상 — {st.id} {chapter}화")
    tl = st.runtime_timeline or []
    print("\n## ⓐ 이 화 기계 캐논 커밋(승인 대상 — 그대로 두면 기계 캐논으로 유지)")
    rows = [ev for ev in tl if ev.eff_from == chapter + 1
            and "author" not in (getattr(ev, "provenance", None) or ["machine"])]
    for ev in rows:
        print(f"  - {ev.entity_id}:{ev.attr} = {ev.value} (tier {ev.trust_tier}) "
              f"→ 승인: --approve {ev.entity_id}:{ev.attr}")
    if not rows:
        print("  (없음)")
    ch = next((c for c in st.chapters if c.chapter == chapter), None)
    print("\n## ⓑ 미적용 제안(review/conflict — 판단 필요)")
    n = 0
    for o in (getattr(ch, "ontology_changes", None) or []):
        d = o if isinstance(o, dict) else o.__dict__
        if not d.get("applied"):
            n += 1
            print(f"  - [{d.get('severity', '-')}] {d.get('op')}: {d.get('entity')} — {str(d.get('detail'))[:70]}")
    if not n:
        print("  (없음)")
    print("\n## ⓒ 신규 개체(이 화 자동 커밋 — 유령 의심 시 --merge)")
    n = 0
    for o in (getattr(ch, "ontology_changes", None) or []):
        d = o if isinstance(o, dict) else o.__dict__
        if d.get("op") == "new_entity" and d.get("applied"):
            n += 1
            sim = _similar_canon(st, str(d.get("entity")))
            tail = f"  ← 유사 정본: {', '.join(sim)}" if sim else ""
            print(f"  - {d.get('entity')}{tail}")
    if not n:
        print("  (없음)")
    probs = validate_timeline(st)
    print(f"\n## 불변식 advisory: {len(probs)}건")
    for p in probs[:6]:
        print(f"  - [{p['kind']}] {p['entity']}.{p['attr']}: {p['detail']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="ON-2 상태 diff 승인")
    ap.add_argument("--pid", required=True)
    ap.add_argument("--chapter", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--approve", default="", help="entity:attr — 이 화 기계값을 작가 승인으로 재커밋")
    ap.add_argument("--set", dest="set_", default="", help="entity:attr=value@eff — 작가 값 직접")
    ap.add_argument("--merge", default="", help="dup_id=canon_id — 유령 개체 병합")
    args = ap.parse_args()

    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    svc = CopilotService(s, repo)
    st = repo.get(args.pid)
    if not st:
        raise SystemExit(f"작품 없음: {args.pid}")

    if args.merge:
        dup, _, canon = args.merge.partition("=")
        r = merge_entity(st, dup.strip(), canon.strip())
        repo.save(st)
        print(f"병합 완료: {r}")
        return
    if args.set_:
        spec, _, tail = args.set_.partition("=")
        eid, _, attr = spec.partition(":")
        value, _, eff = tail.partition("@")
        r = svc.set_entity_state(args.pid, eid.strip(), attr.strip(), value.strip(),
                                 eff_from=int(eff or 1), reason="작가 승인(approve_state)")
        print(f"작가 커밋: {r}")
        return
    if args.approve:
        eid, _, attr = args.approve.partition(":")
        eid, attr = eid.strip(), attr.strip()
        rows = [ev for ev in (st.runtime_timeline or [])
                if ev.entity_id == eid and ev.attr == attr
                and "author" not in (getattr(ev, "provenance", None) or ["machine"])]
        if not rows:
            raise SystemExit("승인 대상 기계 커밋 없음")
        ev = max(rows, key=lambda x: x.eff_from)   # 최신 기계값을 그대로 작가 승인
        r = svc.set_entity_state(args.pid, eid, attr, ev.value, eff_from=ev.eff_from,
                                 reason=f"작가 승인(기계 감지 유지·원 사유: {ev.reason[:30]})")
        print(f"승인 커밋: {r}")
        return
    if not args.chapter:
        raise SystemExit("--list 에는 --chapter 가 필요합니다")
    cmd_list(svc, st, args.chapter)


if __name__ == "__main__":
    main()
