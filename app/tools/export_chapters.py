# -*- coding: utf-8 -*-
"""EPT-2 연재 업로드용 회차 추출 — 회차별 txt 파일로 내보내기.

파일명 = "NN_제목.txt" (제목은 EPT-1 공개 형식 '에피소드명 (n)'), 내용 = 본문만(제목 미포함 —
플랫폼 등록 폼은 제목 칸이 따로 있다). FINALIZED 만 기본 대상(--all 로 전체).

사용: (app/ 에서) python tools/export_chapters.py --pid <pid> [--out <dir>] [--from N] [--to N] [--all]
"""
from __future__ import annotations
import argparse
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from novelcopilot.config import get_settings
from novelcopilot.domain.types import ChapterStatus
from novelcopilot.repository.filesystem import FilesystemProjectRepository


def _safe_name(s: str) -> str:
    """윈도우 금지 문자만 치환(한글·괄호·공백 유지 — 업로드 대조가 쉬워야 한다)."""
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip() or "무제"


def main() -> int:
    ap = argparse.ArgumentParser(description="회차별 txt 추출(연재 업로드용)")
    ap.add_argument("--pid", required=True)
    ap.add_argument("--out", default="", help="출력 폴더(기본: tools/reports/export_<pid>)")
    ap.add_argument("--from", dest="from_ch", type=int, default=1)
    ap.add_argument("--to", dest="to_ch", type=int, default=0, help="0=끝까지")
    ap.add_argument("--all", action="store_true", help="ESCALATED 포함 전체 상태 내보내기")
    args = ap.parse_args()

    repo = FilesystemProjectRepository(get_settings().resolved_data_dir())
    st = repo.get(args.pid)
    if st is None:
        print(f"작품 없음: {args.pid}")
        return 1

    out = pathlib.Path(args.out) if args.out else (_HERE / "reports" / f"export_{args.pid}")
    out.mkdir(parents=True, exist_ok=True)

    n_ok = n_skip = 0
    for c in sorted(st.chapters, key=lambda x: x.chapter):
        if c.chapter < args.from_ch or (args.to_ch and c.chapter > args.to_ch):
            continue
        if not args.all and c.status != ChapterStatus.FINALIZED:
            print(f"  skip {c.chapter}화 ({c.status}) — --all 로 포함 가능")
            n_skip += 1
            continue
        fname = f"{c.chapter:02d}_{_safe_name(c.title or f'{c.chapter}화')}.txt"
        (out / fname).write_text((c.text or "").strip() + "\n", encoding="utf-8-sig")   # BOM: 메모장 호환
        print(f"  {fname}  ({len(c.text or ''):,}자)")
        n_ok += 1
    print(f"\n{n_ok}개 추출 (건너뜀 {n_skip}) → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
