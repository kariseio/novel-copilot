# -*- coding: utf-8 -*-
"""블라인드 감사용 증거 추출 — 코드가 아닌 '결과물'만 모은다(1회용)."""
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\study\ai-web-novel\app")
OUT = ROOT / "tools" / "reports" / "blind_audit_evidence.md"

PIDS = sys.argv[1:] or ["5c1c7372cdbf", "031529cdd316", "f0b23c795876"]
MAX_CH = 5

parts = []
parts.append("# 블라인드 아키텍처 감사 — 증거 자료\n")
parts.append("이 파일은 '현재 시스템이 실제로 뽑아낸 결과물'과 그 평가 점수다. "
             "시스템 구현(코드)은 포함하지 않는다.\n")

for pid in PIDS:
    p = ROOT / "data" / "projects" / f"{pid}.json"
    if not p.exists():
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    w = d.get("world", {})
    chs = d.get("chapters", [])
    parts.append(f"\n---\n\n## 작품: {w.get('title')} (장르: {w.get('genre')}, 총 {len(chs)}화 생성됨)\n")
    parts.append(f"**전제(사용자 의도)**: {w.get('premise','')}\n")
    syn = w.get("synopsis") or ""
    if syn:
        parts.append(f"**시놉시스(시스템 생성)**: {syn}\n")
    spine = (w.get("spine") or {})
    if spine:
        end = (spine.get("ending") or {})
        parts.append(f"**엔딩 설계**: {end.get('one_line','')} / 중심질문: {end.get('central_question','')}\n")
    for c in chs[:MAX_CH]:
        n = c.get("chapter")
        txt = c.get("text", "")
        parts.append(f"\n### {n}화 (status={c.get('status')}, {len(txt)}자)\n")
        parts.append(txt)
        parts.append("\n")

# 평가 기록 — 위 작품들(PIDS)에 해당하는 회차별 편집자 평가만(무관 작품 기록은 혼동 방지 위해 제외)
f = ROOT / "tools" / "reports" / "test_history.jsonl"
if f.exists():
    lines = [line for line in f.read_text(encoding="utf-8").strip().splitlines()
             if any(pid in line for pid in PIDS)]
    parts.append(f"\n---\n\n## 회차별 편집자 평가 기록 (5축 1~10: hook/style/world/consistency/progress, {len(lines)}건)\n```\n")
    parts.extend(line + "\n" for line in lines[-80:])
    parts.append("```\n")

OUT.write_text("".join(parts), encoding="utf-8")
print("written:", OUT, OUT.stat().st_size, "bytes")
