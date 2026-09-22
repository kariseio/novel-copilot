# -*- coding: utf-8 -*-
"""SP-1b ③(G3, 설계 §4) — Kiwi 문체 축의 인간 레퍼런스 대역 산출 + 동결 상수(원문 커밋 금지·수치만).

설계 docs/design-sp1-style-pipeline.md §4: "kiwi ending_profile·im-not-ai 축(daStreak·ending_diversity,
**인간 레퍼런스 대역 병기**)을 ChapterRecord.ai_tell 에 additive 확장". 감사 audit_e2e_0.md G-3·_1.md G-3 은
이 '인간 대역 대비 어디쯤인가' 대조축이 러너(st10a)에만 있고 루프(엔진 ai_tell)엔 없다고 지적했다.

**무엇을 하나**: reference/ 인간 웹소설 4화(TRPG EP.0~1·아카데미 EP.0~1 — st10a 인간 대조군과 동일)에
kiwi_metrics.ending_profile·da_streak_kiwi 와 style_lightness_baseline.lightness_metrics(층위 축)를 돌려
회차별 값을 뽑고, 축별 대역(min/max/mean/median)을 **수치 상수**로 산출한다.

**왜 동결 상수(HUMAN_BAND)인가**: 저작권·few-shot 금지 규칙(MEMORY)상 reference/ 원문은 커밋 대상이 아니다.
그래서 이 스크립트를 1회 돌려 나온 **수치만** 아래 HUMAN_BAND 로 동결(하드코딩)한다 — 엔진(style_pipeline)은
원문 없이 이 상수만 읽어 ai_tell.kiwi.human_band 로 advisory 병기한다. 원문 접근·kiwipiepy 는 이 도구에만.

**무강제(핵심)**: HUMAN_BAND 는 **advisory 대역**이다 — 임계·이진 판정·라벨·자동 재작성 트리거 0. 회차 값이
대역 밖이어도 아무 개입 없다(작가가 '사람 글 대역 어디쯤인가'를 눈으로 보는 참조선일 뿐). n(표본 문장수)이
작은 도입부(EP.0)는 top_ratio/compression 이 크게 튄다 — 대역은 넓고, 단일 회차 스파이크 판정 금지.

실행(app/ 에서):
  py -3.12 tools/kiwi_human_band.py            : reference/ 실측 → JSON(수치만) + 콘솔. 값이 아래
                                                 HUMAN_BAND 와 다르면(코퍼스·kiwi 버전 변화) 콘솔이 경고.
  py -3.12 tools/kiwi_human_band.py --emit     : 갱신된 HUMAN_BAND python 리터럴을 stdout 으로 출력(수기 이식용).
산출: tools/reports/kiwi_human_band.json (수치 상수 — 원문 0). 원문(reference/*.txt)은 산출물에 절대 미포함.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

APP = pathlib.Path(__file__).resolve().parents[1]     # app/
REPO = APP.parent                                     # ai-web-novel/
REF = REPO / "reference"
REPORTS = APP / "tools" / "reports"

sys.path.insert(0, str(APP))    # novelcopilot import(lightness_metrics 내부 quality_gates)

# ── 인간 대조군 4화(st10a 와 동일 — 인간 대역의 표본) ──────────────────────────
REF_FILES = [
    ("TRPG EP.0", REF / "이세계 TRPG 게임마스터" / "EP.0.txt"),
    ("TRPG EP.1", REF / "이세계 TRPG 게임마스터" / "EP.1.txt"),
    ("아카데미 EP.0", REF / "아카데미 훈수빌런이 되다" / "EP.0.txt"),
    ("아카데미 EP.1", REF / "아카데미 훈수빌런이 되다" / "EP.1.txt"),
]

# 대역을 산출·병기하는 축(engine ai_tell.kiwi 구조와 1:1 대응):
#   ending_profile.top_ratio  — 최빈 종결형 지배율(템플릿률). 높을수록 종결 단조.
#   ending_profile.max_run     — 동일 종결 최장 연속 run(종결 단일성 벽).
#   ending_profile.compression — unique/n_ending(낮을수록 종결이 소수로 압축).
#   da_streak.max_run          — 과거 '~다' 종결 최장 연속 run(어미 벽).
#   da_streak.ratio            — 과거 '~다' 종결 비율.
#   layer.max_narration_run    — 무대사 지문 연속 문단 최장 run(ST-9 '진짜 벽' 판정축).
#   layer.dialogue_para_ratio  — 대사 포함 문단 비중.
#   layer.dialogue_char_ratio  — 대사 문자 비중.
AXES = [
    ("ending_profile", "top_ratio"),
    ("ending_profile", "max_run"),
    ("ending_profile", "compression"),
    ("da_streak", "max_run"),
    ("da_streak", "ratio"),
    ("layer", "max_narration_run"),
    ("layer", "dialogue_para_ratio"),
    ("layer", "dialogue_char_ratio"),
]

# ── 동결 상수(HUMAN_BAND) — 이 스크립트 1회 실측값(수치만·원문 0). 엔진이 이것만 읽는다. ──
#   구조: {group: {metric: {min, max, mean, median, n_chapters}}}. n_chapters=대역 표본 회차 수(4).
#   *중요*: 대역은 advisory 참조선이다(임계·판정 0). 표본 4화·도입부 포함이라 넓다 — 아래 CAVEATS 참조.
HUMAN_BAND = {
    "ending_profile": {
        "top_ratio":   {"min": 0.347, "max": 0.595, "mean": 0.458, "median": 0.446},
        "max_run":     {"min": 1,     "max": 14,    "mean": 7.25,  "median": 7.0},
        "compression": {"min": 0.176, "max": 0.75,  "mean": 0.344, "median": 0.225},
    },
    "da_streak": {
        "max_run": {"min": 0,   "max": 14,    "mean": 5.25,  "median": 3.5},
        "ratio":   {"min": 0.0, "max": 0.389, "mean": 0.233, "median": 0.272},
    },
    "layer": {
        "max_narration_run":  {"min": 6,   "max": 19,    "mean": 12.75, "median": 13.0},
        "dialogue_para_ratio": {"min": 0.0, "max": 0.314, "mean": 0.171, "median": 0.185},
        "dialogue_char_ratio": {"min": 0.0, "max": 0.257, "mean": 0.115, "median": 0.1},
    },
    "meta": {
        "n_chapters": 4,
        "source": "reference/ 인간 웹소설 4화(TRPG EP.0~1·아카데미 EP.0~1) — 원문 미커밋·수치만",
        "backend": "kiwi",
        "advisory": True,   # 임계·판정 라벨 0 — 참조 대역일 뿐
    },
}

CAVEATS = [
    "표본 4화(도입부 EP.0 포함) — n(종결 문장수)이 작은 EP.0 는 top_ratio/compression 이 크게 튄다. 대역은 넓다.",
    "advisory 참조선일 뿐 — 임계·이진 판정·라벨·자동 재작성 0(무강제·no-whack-a-mole·ai_tell 선례 정합).",
    "장르 교란 큼(대사/액션 회차 편차) — 단일 회차가 대역 밖이라고 'AI티'로 판정 금지, 추세로만.",
    "kiwipiepy 미설치 환경에선 회차 값이 정규식 강등(backend='regex')이라 이 kiwi 대역과 직접 비교 부적절 —"
    " ai_tell.kiwi 각 축의 backend 표기를 함께 볼 것.",
]


def _load_body(path: pathlib.Path) -> str:
    """reference txt → 본문(제목 첫 줄 분리 — style_lightness_baseline.load_txt_file 동형). 원문은 리턴만·미저장."""
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip():
            start = i + 1
            break
    return "\n".join(lines[start:]).strip()


def _chapter_axes(body: str) -> dict:
    """한 회차의 축 값 dict {group: {metric: value}} — kiwi_metrics + lightness_metrics 재사용(신규 검출기 0)."""
    import tools.kiwi_metrics as km
    import tools.style_lightness_baseline as sl
    ep = km.ending_profile(body)
    da = km.da_streak_kiwi(body)
    lm = sl.lightness_metrics(body)
    return {
        "ending_profile": {"top_ratio": ep.get("top_ratio"), "max_run": ep.get("max_run"),
                           "compression": ep.get("compression")},
        "da_streak": {"max_run": da.get("max_run"), "ratio": da.get("ratio")},
        "layer": {"max_narration_run": lm.get("max_narration_run"),
                  "dialogue_para_ratio": lm.get("dialogue_para_ratio"),
                  "dialogue_char_ratio": lm.get("dialogue_char_ratio")},
    }


def _band(values: list) -> dict:
    """축 하나의 대역(min/max/mean/median) — None 제외. 표본 0이면 전부 None(결측 정직)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"min": None, "max": None, "mean": None, "median": None}
    return {"min": min(vals), "max": max(vals),
            "mean": round(statistics.mean(vals), 3), "median": round(statistics.median(vals), 3)}


def compute_band() -> tuple[dict, list[dict]]:
    """reference/ 4화 실측 → (band, per_chapter). band 구조는 HUMAN_BAND 와 동형(meta 제외)."""
    per_chapter = []
    for label, path in REF_FILES:
        if not path.exists():
            raise SystemExit(f"[kiwi_human_band] 레퍼런스 부재: {path}")
        ax = _chapter_axes(_load_body(path))
        per_chapter.append({"label": label, **ax})   # 원문 미포함 — 축 값만
    band: dict = {}
    for group, metric in AXES:
        band.setdefault(group, {})[metric] = _band([c[group][metric] for c in per_chapter])
    return band, per_chapter


def _flatten(band: dict) -> dict:
    return {f"{g}.{m}": band.get(g, {}).get(m) for g, m in AXES}


def run(out_json: pathlib.Path) -> dict:
    band, per_chapter = compute_band()
    # per_chapter 는 축 값(수치)만 — 원문 없음. 대역 신뢰성 감사용으로 함께 저장(투명성).
    result = {
        "report": "SP-1b ③ Kiwi 인간 레퍼런스 대역(수치 상수 — 원문 0)",
        "method": "결정론·LLM 0콜 — kiwi_metrics + style_lightness_baseline 재사용(신규 검출기 0)",
        "n_chapters": len(per_chapter),
        "band": band,
        "per_chapter": per_chapter,
        "caveats": CAVEATS,
        "note": "reference/ 원문 미포함(저작권·few-shot 금지). 엔진은 kiwi_human_band.HUMAN_BAND 상수만 읽는다.",
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # 동결 상수와 실측이 어긋나면(코퍼스·kiwi 버전 변화) 경고 — 상수 재이식 신호(자동 갱신 안 함).
    live, frozen = _flatten(band), _flatten(HUMAN_BAND)
    drift = [k for k in live if live[k] != frozen.get(k)]
    if drift:
        print(f"[경고] 실측이 동결 HUMAN_BAND 와 다름({len(drift)}축): {drift}", file=sys.stderr)
        print("       --emit 로 갱신 리터럴을 뽑아 HUMAN_BAND 를 수기 이식하세요(자동 갱신 안 함).", file=sys.stderr)
    return result


def _emit_literal() -> str:
    """실측 band 를 HUMAN_BAND python 리터럴로 — 코퍼스/kiwi 변화 시 수기 이식용(원문 0)."""
    band, _ = compute_band()
    return json.dumps(band, ensure_ascii=False, indent=4)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="SP-1b ③ Kiwi 인간 레퍼런스 대역(수치 상수)")
    ap.add_argument("--out", default=str(REPORTS / "kiwi_human_band.json"), help="JSON 출력 경로(수치만)")
    ap.add_argument("--emit", action="store_true", help="갱신 band 리터럴만 stdout(수기 이식용)")
    args = ap.parse_args(argv)
    if args.emit:
        print(_emit_literal())
        return 0
    result = run(pathlib.Path(args.out))
    print("band:", json.dumps(_flatten(result["band"]), ensure_ascii=False))
    print("report:", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
