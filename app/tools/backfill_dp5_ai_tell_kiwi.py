# -*- coding: utf-8 -*-
"""SP-1b ④(감사 G5) — [실험 DP-5] 2작품 기존 회차 ai_tell 에 kiwi 축 결정론 백필(LLM 0콜·본문 불가침).

감사 audit_e2e_1.md G5: SP-1 이전 생성된 두 실험작(12772158ea8c·8aa688cf8881)은 ai_tell 에 .kiwi 키가
없어 웹 UI(app.js:331 kiwiEndingHtml)·SP-1b ②③(layer·human_band) 패널이 영구 공란이다. ai_tell 은
결정론 재계산 가능하므로(LLM 0콜) 기존 회차에 kiwi 축을 1회 백필한다.

**불가침 계약(사용자 승인 범위)**: 이 스크립트는 **오직 chapters[*].ai_tell 필드만** 갱신한다. 본문(text)·
요약(summary·detail_synopsis)·서사/구조 필드·world·bible 등 **그 밖 모든 바이트는 불변**임을 쓰기 전에
구조 diff 로 검증하고, ai_tell 이외가 하나라도 바뀌면 **중단(쓰기 취소)**한다. 대상도 [실험 DP-5] 2작 화이트
리스트로 고정 — 그 밖 작품은 접근조차 안 한다(원칙: 기존 작품 데이터 무수정).

**멱등(idempotent)**: ai_tell 재계산은 결정론(ai_tell_profile + kiwi_style_metrics, LLM 0콜)이고 직렬화는
json.dumps(indent=2, ensure_ascii=False)로 pydantic model_dump_json(indent=2)과 바이트 동형이다. 이미 백필된
파일에 재실행하면 산출 바이트가 동일해 no-op(변경 0). --check 는 쓰기 없이 예상 변경만 보고한다.

**base ai_tell 은 재계산하지 않고 보존한다(핵심 설계)**: 백필의 유일한 임무는 ai_tell 에 **.kiwi 하위 키를
additive 로 추가**하는 것이다. base 축(comma_*·sent_len_cv·lexical_mattr·ending_diversity·simile_per_1k·
past_run_max·frag_ratio·n_sent)은 **기존 저장값을 그대로 둔다** — 재계산하지 않는다.
  왜 재계산 금지: base 의 lexical_mattr 는 roster(고유어 제외 집합)에 의존하고, 그 roster 는 회차 생성 *당시*의
  ontology 상태(누적 성장 중)였다. 지금 재수화하면 roster 가 최종(가장 큰) 상태라 초반 회차 lexical_mattr 가
  ±0.001~0.002 미세 이동한다 — 이는 '구 JSON 바이트 동일·기존 계측 보존' 정신에 반한다. 그래서 base 는 불변,
  kiwi 만 순수 additive. (copilot._recompute_ai_tell 은 본문이 바뀌는 경로에서 전량 재계산이 맞지만, 백필은
  본문 불변이므로 base 도 불변이 정답이다.)
kiwi = kiwi_style_metrics(text)(LLM 0콜·결정론). kiwipiepy 미설치면 kiwi 축은 backend='regex' 강등판으로
채워진다(값은 정직 표기 — 사망 금지).

실행(app/ 에서):
  py -3.12 tools/backfill_dp5_ai_tell_kiwi.py --check   : 쓰기 없이 회차별 예상 변경/불변 보고(dry-run)
  py -3.12 tools/backfill_dp5_ai_tell_kiwi.py --apply   : 실제 백필(불가침 검증 통과 회차만·원자적 쓰기)
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import sys

APP = pathlib.Path(__file__).resolve().parents[1]     # app/
PROJ = APP / "data" / "projects"

sys.path.insert(0, str(APP))    # novelcopilot import
sys.path.insert(0, str(APP / "tools"))

# 대상 화이트리스트 — [실험 DP-5] 2작만(사용자 승인 범위). 그 밖 작품은 접근 금지.
TARGET_PIDS = ["12772158ea8c", "8aa688cf8881"]


def _add_kiwi(existing_ai_tell: dict, text: str) -> dict:
    """base ai_tell 보존 + kiwi 순수 additive(LLM 0콜·결정론). base 축은 재계산하지 않는다(본문 불변 → base 불변).

    existing 을 얕은 복사해 'kiwi' 키만 kiwi_style_metrics(text)로 채운다. kiwi 가 빈 dict(kiwi_metrics 부품
    부재)면 확장 없음(구 경로 동형·결측 정직) — 기존 dict 그대로 반환(멱등). base 키 순서/값 전부 보존."""
    from novelcopilot.engine.style_pipeline import kiwi_style_metrics
    kiwi = kiwi_style_metrics(text or "")
    if not kiwi:
        return dict(existing_ai_tell or {})   # 부품 부재 — base 그대로(확장 없음)
    return {**(existing_ai_tell or {}), "kiwi": kiwi}


def _structural_diff_paths(old, new, path: str = "") -> list[str]:
    """old→new 에서 값이 바뀐 경로(리프)를 전부 수집. 타입 불일치/키 추가·삭제도 경로로 기록.
    반환된 경로가 전부 'chapters[i].ai_tell...' 하위여야 불가침 계약 충족."""
    diffs: list[str] = []
    if type(old) is not type(new):
        return [path or "<root>"]
    if isinstance(old, dict):
        for k in set(old.keys()) | set(new.keys()):
            if k not in old or k not in new:
                diffs.append(f"{path}.{k}" if path else str(k))
            else:
                diffs += _structural_diff_paths(old[k], new[k], f"{path}.{k}" if path else str(k))
    elif isinstance(old, list):
        if len(old) != len(new):
            diffs.append(f"{path}[len {len(old)}->{len(new)}]")
        else:
            for i, (a, b) in enumerate(zip(old, new)):
                diffs += _structural_diff_paths(a, b, f"{path}[{i}]")
    else:
        if old != new:
            diffs.append(path or "<root>")
    return diffs


def _only_ai_tell(diff_paths: list[str]) -> bool:
    """모든 변경 경로가 chapters[i].ai_tell 하위인가(불가침 검증). 경로 형식: 'chapters[3].ai_tell...'."""
    import re
    pat = re.compile(r"^chapters\[\d+\]\.ai_tell(\.|\[|$)")
    return all(pat.match(p) for p in diff_paths)


def process(pid: str, apply: bool) -> dict:
    """1작 백필 — 원자적. 반환 요약(변경 회차수·불가침 검증·쓰기 여부)."""
    path = PROJ / f"{pid}.json"
    if not path.exists():
        raise SystemExit(f"[backfill] 대상 부재: {path}")
    raw = path.read_text(encoding="utf-8")
    old = json.loads(raw)
    # 원본 round-trip 이 바이트 동형인지 먼저 확인(직렬화 계약) — 아니면 그 밖 필드 churn 위험이라 중단.
    if json.dumps(old, ensure_ascii=False, indent=2) != raw:
        raise SystemExit(f"[backfill] {pid}: json.dumps round-trip 이 원본과 불일치 — 직렬화 계약 위반, 중단")

    new = copy.deepcopy(old)
    changed_chapters, unchanged_chapters, skipped_nonfinal = [], [], []
    for ch in new.get("chapters", []):
        if ch.get("status") != "FINALIZED":
            skipped_nonfinal.append(ch.get("chapter"))
            continue
        before = ch.get("ai_tell")
        after = _add_kiwi(before, ch.get("text") or "")   # base 보존·kiwi additive
        if before == after:
            unchanged_chapters.append(ch.get("chapter"))   # 이미 백필됨(멱등 no-op) 또는 부품 부재
        else:
            ch["ai_tell"] = after
            changed_chapters.append(ch.get("chapter"))

    # ── 불가침 계약 검증: old→new 변경 경로가 전부 chapters[i].ai_tell 하위인가 ──
    diff_paths = _structural_diff_paths(old, new)
    invariant_ok = _only_ai_tell(diff_paths)
    offending = [p for p in diff_paths if not __import__("re").match(r"^chapters\[\d+\]\.ai_tell(\.|\[|$)", p)]

    wrote = False
    serialized = json.dumps(new, ensure_ascii=False, indent=2)
    if apply:
        if not invariant_ok:
            raise SystemExit(f"[backfill] {pid}: 불가침 위반(ai_tell 이외 변경 {len(offending)}건) — 쓰기 취소\n"
                             f"          예: {offending[:5]}")
        if serialized != raw:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
            wrote = True
    return {
        "pid": pid,
        "changed_chapters": changed_chapters,
        "unchanged_chapters": unchanged_chapters,
        "skipped_nonfinal": skipped_nonfinal,
        "invariant_ai_tell_only": invariant_ok,
        "offending_paths": offending[:10],
        "would_write": (serialized != raw),
        "wrote": wrote,
        "kiwi_backend": _peek_backend(new),
    }


def _peek_backend(state: dict) -> str | None:
    for ch in state.get("chapters", []):
        k = (ch.get("ai_tell") or {}).get("kiwi") or {}
        ep = k.get("ending_profile") or {}
        if ep.get("backend"):
            return ep["backend"]
    return None


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="SP-1b ④ [실험 DP-5] ai_tell kiwi 백필(본문 불가침·멱등)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="쓰기 없이 예상 변경 보고(dry-run·기본)")
    g.add_argument("--apply", action="store_true", help="실제 백필(불가침 검증 통과 회차만·원자적)")
    args = ap.parse_args(argv)
    apply = bool(args.apply)
    mode = "APPLY" if apply else "CHECK(dry-run)"
    print(f"[backfill] mode={mode} targets={TARGET_PIDS}")
    all_ok = True
    for pid in TARGET_PIDS:
        r = process(pid, apply)
        all_ok &= r["invariant_ai_tell_only"]
        print(f"\n== {pid} ==")
        print(f"  changed   : {r['changed_chapters']}")
        print(f"  unchanged : {r['unchanged_chapters']}")
        print(f"  non-final : {r['skipped_nonfinal']}")
        print(f"  kiwi backend: {r['kiwi_backend']}")
        print(f"  invariant(ai_tell only): {r['invariant_ai_tell_only']}"
              + ("" if r["invariant_ai_tell_only"] else f"  OFFENDING={r['offending_paths']}"))
        print(f"  would_write={r['would_write']}  wrote={r['wrote']}")
    if not all_ok:
        print("\n[backfill] 불가침 위반 감지 — APPLY 금지. 위 OFFENDING 경로 확인 필요.", file=sys.stderr)
        return 1
    print(f"\n[backfill] {'완료(쓰기 반영)' if apply else 'dry-run 완료(쓰기 없음)'} · 불가침 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
