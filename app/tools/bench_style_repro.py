# -*- coding: utf-8 -*-
"""SB-1 문체 재현 벤치 — 원작 발췌를 정답지로 프롬프트의 문체 재현력을 판정한다.

방법론(사용자 설계 2026-08-13): 발췌 → 사실 뼈대·대사 추출 → 현행 문체 프롬프트로 재생성 →
  ①내용 정합(대사 보존율·수치 보존) ②문체 계측 대조(참고 원자료) ③쌍대 블라인드 판별(1차 척도 —
  판정자가 원문을 못 가려낼수록 재현 성공). ST-12 쌍대 정독 판별율 방법론에 실제 원작 정답지를 결합.

원칙: 원작 텍스트는 로컬 파일로만 다루고 리포트에는 짧은 대조 인용만 남긴다(저작권 — 전문 전재 금지).
  판별 판정은 gen≠judge(style_judge_model 교차 벤더). 계측은 참고 원자료(판정 척도 아님 — 헌법 4-1).

용법:
  py -3.12 -X utf8 tools/bench_style_repro.py --source 발췌.txt [--rounds 4] [--self-test]
  --self-test: 활성작 1화 앞부분을 원작 대용으로 사용(하네스 기계 검증용 — 문체 판정 아님)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.config import get_settings
from novelcopilot.llm.factory import create_provider, create_role_provider
from novelcopilot.engine.rerender import (
    extract_dialogue_lines, extract_numeric_tokens, dialogue_preservation, missing_numerics)
from novelcopilot.engine.prompts import render_style
from novelcopilot.domain.world import StyleSpec
from novelcopilot.engine.quality_gates import fragment_ratio


def profile(text: str) -> dict:
    """문체 계측 프로파일(LLM 0 · 참고 원자료) — 문장 길이 분포·파편율·-다 비중·문단 짜임."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    narr = [s for s in sents if not s.startswith('"')]
    lens = [len(s) for s in narr] or [0]
    da = sum(1 for s in narr if re.search(r"다[.!?…]?$", s))
    paras = [p.strip() for p in text.split("\n\n") if p.strip() and not p.strip().startswith('"')]

    def _ns(p):
        return len([x for x in re.split(r"(?<=[.!?…])\s+", p) if x.strip()])

    dist = {}
    for p in paras:
        dist[min(_ns(p), 5)] = dist.get(min(_ns(p), 5), 0) + 1
    return {"n_sent": len(narr), "avg_len": round(sum(lens) / len(lens), 1),
            "frag": fragment_ratio(text)["ratio"],
            "da_share": round(da / max(len(narr), 1), 3),
            "para_dist": {str(k): v for k, v in sorted(dist.items())},
            "dlg_share": round(sum(1 for s in sents if s.startswith('"')) / max(len(sents), 1), 3)}


def extract_skeleton(provider, source: str) -> dict:
    """발췌 → 사실 뼈대(문체 표백된 사건·인과 목록). 대사·수치는 결정론 추출(rerender 부품 재사용)."""
    r = provider.chat_json(
        [{"role": "system", "content":
          "소설 발췌에서 '사실 뼈대'만 추출하라. 문체·표현은 버리고 정보만 남긴다.\n"
          'JSON: {"skeleton":"사건·인과·행동·소지품·장소를 시간 순서대로 건조한 개조식 목록(문체 없이). '
          '원문 문장을 베끼지 말고 정보로 환원하라","pov":"1인칭|3인칭"}'},
         {"role": "user", "content": source}],
        temperature=0.0)
    return {"skeleton": (r.get("skeleton") or "").strip(), "pov": (r.get("pov") or "").strip()}


def build_gen_prompt(skeleton: str, dialogue: list[str], numerics: list[str],
                     pov: str, n_chars: int) -> tuple[str, str]:
    """현행 상용 후보 프롬프트 = DEFAULT_STYLE_RULES(render_style) + 사실 계약. 검증 대상이 이 블록이다."""
    style = StyleSpec(pov="first" if "1" in pov else "third_limited")
    system = (
        "너는 한국 웹소설 프로 작가다. 아래 사실 뼈대와 대사 목록을 재료로 장면을 본문으로 써라.\n"
        "절대 계약: 1) 뼈대의 사건·순서·수치를 그대로 실현하고 없는 사건을 지어내지 마라. "
        "2) 대사 목록의 대사를 순서 그대로, 한 글자도 바꾸지 말고 전부 넣어라. "
        f"3) 분량은 {int(n_chars * 0.85)}~{int(n_chars * 1.15)}자.\n\n" + render_style(style)
        + "\n\n본문만 출력하라. 머리말·설명 금지.")
    user = "[사실 뼈대]\n" + skeleton
    if dialogue:
        user += "\n\n[대사 목록 — 순서 그대로 전부]\n" + "\n".join(dialogue)
    if numerics:
        user += "\n\n[수치 — 전부 그대로 등장]\n" + ", ".join(numerics)
    return system, user


def blind_pair_judge(judge, a: str, b: str) -> dict | None:
    """쌍대 블라인드 — 어느 쪽이 '사람이 쓴 연재 원문'인지 지목 + 근거. 순서 편향은 호출부가 스왑으로 상쇄."""
    try:
        r = judge.chat_json(
            [{"role": "system", "content":
              "두 지문 중 하나는 실제 연재 원문, 하나는 기계 재현이다. 낭독 체감으로 원문을 지목하라. "
              '판단이 안 서면 "모름"도 허용된다. JSON: {"original":"A|B|모름","why":"한 줄"}'},
             {"role": "user", "content": f"[A]\n{a}\n\n[B]\n{b}"}],
            temperature=0.0)
        return r if isinstance(r, dict) else None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="SB-1 문체 재현 벤치")
    ap.add_argument("--source", help="원작 발췌 텍스트 파일(UTF-8)")
    ap.add_argument("--rounds", type=int, default=4, help="쌍대 판별 라운드(짝수 권장 — 순서 스왑 상쇄)")
    ap.add_argument("--self-test", action="store_true", help="활성작 1화 앞부분으로 기계 검증")
    ap.add_argument("--label", default="", help="리포트 라벨(기준 작품 별칭 등 — 제목 전재 대신)")
    args = ap.parse_args()

    s = get_settings()
    if args.self_test:
        from novelcopilot.repository.filesystem import FilesystemProjectRepository
        repo = FilesystemProjectRepository(s.resolved_data_dir())
        st = repo.get("45295b91ffce")
        source = (st.chapter(1).text or "")[:2400]
        label = args.label or "self-test(활성작 1화)"
    else:
        if not args.source:
            raise SystemExit("--source 파일 또는 --self-test 필요")
        source = Path(args.source).read_text(encoding="utf-8").strip()
        label = args.label or Path(args.source).stem
    if len(source) < 600:
        raise SystemExit("발췌가 너무 짧다(600자 미만) — 문체 판별이 성립하지 않는다")

    gen = create_provider(s)
    judge_spec = (getattr(s, "style_judge_model", "") or "").strip()
    judge = create_role_provider(s, judge_spec) if judge_spec else gen

    dialogue = extract_dialogue_lines(source)
    numerics = extract_numeric_tokens(source)
    sk = extract_skeleton(gen, source)
    if not sk["skeleton"]:
        raise SystemExit("뼈대 추출 실패")
    system, user = build_gen_prompt(sk["skeleton"], dialogue, numerics, sk["pov"], len(source))
    cand = gen.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=0.7)
    cand = (cand or "").strip()
    if len(cand) < 400:
        raise SystemExit("재생성 실패(짧음)")

    # ① 내용 정합(결정론)
    dp = dialogue_preservation(cand, dialogue)
    mn = missing_numerics(cand, numerics)
    # ② 계측 대조(참고)
    p_src, p_cand = profile(source), profile(cand)
    # ③ 쌍대 블라인드 판별(1차 척도) — 라운드마다 A/B 스왑
    hits = miss = unsure = 0
    for i in range(args.rounds):
        swap = i % 2 == 1
        a, b = (cand, source) if swap else (source, cand)
        v = blind_pair_judge(judge, a, b)
        if not v:
            continue
        pick = (v.get("original") or "").strip()
        truth = "B" if swap else "A"
        if pick == "모름":
            unsure += 1
        elif pick == truth:
            hits += 1        # 판정자가 원문을 가려냄 = 재현 실패 신호
        else:
            miss += 1        # 못 가려냄(기계를 원문으로 오인) = 재현 성공 신호
    n_j = hits + miss + unsure

    lines = [f"# SB-1 문체 재현 벤치 — {label}",
             f"생성: {_dt.datetime.now().isoformat(timespec='seconds')} · gen={type(gen).__name__} · judge={judge_spec or 'gen 재사용'}",
             "",
             f"## 판별(1차 척도) — {args.rounds}라운드",
             f"- 원문 적중 {hits} · 오인(재현 성공) {miss} · 모름 {unsure} (유효 {n_j})",
             f"- 판별율 {hits}/{n_j} — 0.5 이하로 내려갈수록(찍기 수준) 프롬프트가 그 문체를 재현한 것",
             "",
             "## 내용 정합(결정론)",
             f"- 대사 보존율 {dp:.2f} ({len(dialogue)}줄) · 수치 누락 {len(mn)}건 {mn[:5]}",
             "",
             "## 계측 대조(참고 원자료 — 판정 척도 아님)",
             f"- 원문:   {json.dumps(p_src, ensure_ascii=False)}",
             f"- 재현문: {json.dumps(p_cand, ensure_ascii=False)}",
             "",
             "## 재현문 첫 400자(원문은 저작권상 미전재)",
             cand[:400]]
    out_dir = Path(__file__).resolve().parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"bench_style_{re.sub(r'[^0-9A-Za-z가-힣_-]', '', label)[:24]}_{_dt.datetime.now():%Y%m%d_%H%M%S}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:14]))
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
