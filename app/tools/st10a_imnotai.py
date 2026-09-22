# -*- coding: utf-8 -*-
"""ST-10a 선행 실측 — im-not-ai(epoko77-ai/im-not-ai, MIT) 어댑터.

외부 도구 `metrics_v2.py`(stdlib-only, LLM 0콜, 결정론)를 우리 산출물에 적용한다.
정량 지표(v1.6 8종 + v2.0 14종 + T1~T8 간섭지수)와 87패턴 중 결정론 매칭 가능한
서브셋을 우리 회차 vs 인간 대조군(레퍼런스 4화)에 돌려 표를 만든다.

**엔진 무접촉**: novelcopilot 코드 임포트 없음. app/data/·reference/ 읽기 전용.
외부 도구 원본은 스크래치에서 vendor 복사(커밋 비대상, gitignore 처리)한 뒤 import.

대상:
  ⓐ DP-4b '1위의 재림'(c6e39900882a) 9회차 — 특히 8화(벽 실증) vs 9화(voice 수리)
  ⓑ 레퍼런스 4화(TRPG EP.0~1 · 아카데미 EP.0~1) — 인간 대조군
  ⓒ (참고) DP-1 원본(81cbd54a8672) 1~2화

사용(app/ 에서):
  py -3.12 tools/st10a_imnotai.py           : 전체 실측 → JSON + 콘솔 요약
  py -3.12 tools/st10a_imnotai.py --json OUT : 결과 JSON 경로 지정

산출: 콘솔 표 + JSON(리포트 md 는 사람이 정독 해석으로 별도 작성).
도구 심각도(S1/S2/S3)·caveat(장르 가드·placeholder baseline)는 해석에서 존중.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# 경로 (엔진 무접촉 — novelcopilot import 금지)
# ---------------------------------------------------------------------------
APP = pathlib.Path(__file__).resolve().parents[1]          # app/
REPO = APP.parent                                          # ai-web-novel/
PROJ = APP / "data" / "projects"
REF = REPO / "reference"
REPORTS = APP / "tools" / "reports"

# im-not-ai vendored 위치: 환경변수 IMNOTAI_DIR 우선, 없으면 스크래치 기본 경로.
_DEFAULT_VENDOR = pathlib.Path(
    os.environ.get(
        "IMNOTAI_DIR",
        r"C:\Users\owner\AppData\Local\Temp\claude\D--study-ai-web-novel"
        r"\65022ca9-785b-447e-8a1b-26f537996f1f\scratchpad\im-not-ai"
        r"\.claude\skills\humanize-korean\references",
    )
)


def _load_metrics_module(vendor_dir: pathlib.Path):
    if not (vendor_dir / "metrics_v2.py").exists():
        raise SystemExit(
            f"[st10a] metrics_v2.py 없음: {vendor_dir}\n"
            "        IMNOTAI_DIR 환경변수로 vendored references 경로를 지정하세요."
        )
    sys.path.insert(0, str(vendor_dir))
    import metrics_v2 as m  # noqa: E402

    return m


# ---------------------------------------------------------------------------
# 대상 정의
# ---------------------------------------------------------------------------
DP4B_PID = "c6e39900882a"     # DP-4b '1위의 재림' 9회차 (1인칭·voice 수리 스택)
DP1_PID = "81cbd54a8672"      # DP-1 원본 시드A (4.0점·잔존 27 실패 케이스)

REF_FILES = [
    ("TRPG EP.0", REF / "이세계 TRPG 게임마스터" / "EP.0.txt"),
    ("TRPG EP.1", REF / "이세계 TRPG 게임마스터" / "EP.1.txt"),
    ("아카데미 EP.0", REF / "아카데미 훈수빌런이 되다" / "EP.0.txt"),
    ("아카데미 EP.1", REF / "아카데미 훈수빌런이 되다" / "EP.1.txt"),
]


def _project_chapters(pid: str) -> list[tuple[str, str]]:
    """(라벨, 본문) 리스트. 본문 = chapter['text']."""
    d = json.loads((PROJ / f"{pid}.json").read_text(encoding="utf-8"))
    out = []
    for c in d.get("chapters", []):
        n = c.get("chapter")
        text = c.get("text", "") or ""
        if text.strip():
            out.append((f"{pid[:6]} ch{n}", text))
    return out


# ---------------------------------------------------------------------------
# 결정론 87패턴 매칭 — 어간 경계 존중(교착어 substring 함정 회피)
# ---------------------------------------------------------------------------
# 각 패턴: (코드, 라벨, 심각도, 컴파일된 정규식). 도구 taxonomy 정의를 따르되,
# 우리 코퍼스 표층에 결정론으로 매칭 가능한 것만 선별. 조사 결합을 흡수하는
# 경계(공백·문장부호·EOS)를 명시해 '광민과의'≠'광민이' 류 오탐을 막는다.
#
# 도구 metrics_v2 가 이미 정량으로 세는 것(T1~T8·쉼표·명사화)은 그쪽 값을 쓰고,
# 여기서는 도구가 lexicon/regex 로 잡는 D/H/I/A 계열 상투구·번역투를 보강 카운트.
_PATTERNS: list[tuple[str, str, str, re.Pattern]] = [
    # A. 번역투
    ("A-1", "~에 대하여/대해서", "S1", re.compile(r"에\s*대(?:하여|해서|해)(?=[\s,\.!?…”’)\]]|$)")),
    ("A-2", "~를 통하여/통해", "S1", re.compile(r"(?:를|을)?\s*통(?:하여|해서|해)(?=[\s,\.!?…”’)\]]|$)")),
    ("A-3", "~에 있어(서)", "S1", re.compile(r"에\s*있어(?:서)?(?=[\s,\.!?…”’)\]]|$)")),
    ("A-6", "~에 기반하여/바탕으로", "S2", re.compile(r"(?:에\s*기반하여|을?\s*바탕으로)")),
    ("A-7", "가지고/갖고 있다", "S1", re.compile(r"(?:가지고|갖고)\s*있(?:다|는|었|으)")),
    ("A-8", "이중 피동(되어진/여진/혀진)", "S1",
     re.compile(r"(?:되어지|여지|혀지|려지|보여지|쓰여지|잊혀지|불려지|놓여지)(?:다|는|ㄴ다|는다|었|어)")),
    ("A-14", "문두 접속 '그리고'", "S2", re.compile(r"(?:^|[\.!?…”’]\s*|\n)\s*그리고(?=[\s,])")),
    # D. AI 특유 관용구 (lexicon)
    ("D-1", "결산 상투구(결론적으로·따라서·이를 통해·그러므로)", "S1",
     re.compile(r"(?:결론적으로|따라서|이를\s*통해|그러므로|요약하면|종합하면|정리하자면)")),
    ("D-2", "의의·중요성 과장(시사하는 바·주목할 만·간과할 수 없)", "S3",
     re.compile(r"(?:시사하는\s*바|주목할\s*만|간과할\s*수\s*없|무시할\s*수\s*없|의미가\s*적지\s*않)")),
    ("D-4", "hype 어휘(혁신적·획기적·전례 없는·압도적·폭발적)", "S3",
     re.compile(r"(?:혁신적|획기적|전례\s*없는|압도적|폭발적|파격적|대대적)")),
    ("D-6", "완결 공식형 결말(~할 때입니다/시점입니다)", "S2",
     re.compile(r"(?:할\s*때(?:이다|입니다|다)|시점(?:이다|입니다)|순간(?:이다|입니다))(?=[\s\.!?…”’]|$)")),
    # H. 접속사 남발 (문두)
    ("H-1", "문두 접속사(또한·따라서·즉·나아가·게다가·더욱이)", "S2",
     re.compile(r"(?:^|[\.!?…”’]\s*|\n)\s*(?:또한|따라서|즉|나아가|게다가|더욱이|아울러)(?=[\s,])")),
    ("H-3", "'이는 ~' 지시 반복", "S2", re.compile(r"(?:^|[\s\.!?…”’]\s*|\n)이는\s+\S+")),
    ("H-4", "재정의 '즉' 남발", "S2", re.compile(r"(?:^|[\s,\.!?…”’]\s*|\n)즉(?=[\s,])")),
    # I. 형식명사·의존명사
    ("I-1", "'것이다' 종결 남발", "S2", re.compile(r"(?:한|인|일|던|는)\s*것이다(?=[\s\.!?…”’]|$)")),
    ("I-3", "'~라는/다는 것이다·뜻이다' 결산", "S2",
     re.compile(r"(?:라는|다는)\s*(?:것이다|뜻이다|점이다)(?=[\s\.!?…”’]|$)")),
    ("I-4", "권고형 결말(~해야 한다/합니다)", "S2",
     re.compile(r"(?:해야|하여야)\s*(?:한다|합니다|했다)(?=[\s\.!?…”’]|$)")),
    # F. 과잉 수식
    ("F-1", "정도부사(매우·정말·대단히·극히)", "S3",
     re.compile(r"(?:^|[\s,\.!?…”’]\s*)(?:매우|대단히|극히)(?=\s)")),
    ("F-5", "'~적 N' 복합 추상어", "S2", re.compile(r"[가-힣]{2,}적\s+[가-힣]")),
    # G. hedging
    ("G-1", "추측·관측형 종결(~것으로 보인다/판단된다/여겨진다)", "S2",
     re.compile(r"(?:것으로\s*(?:보인다|판단된다)|(?:라고|로)\s*여겨진다|듯하다)(?=[\s\.!?…”’]|$)")),
    ("G-3", "안전 균형 lexicon(양쪽 모두·신중하게·균형)", "S2",
     re.compile(r"(?:양쪽\s*모두|두\s*가지\s*모두|장점도\s*있지만|신중하게|균형\s*잡)")),
]


def detect_patterns(text: str) -> dict[str, int]:
    """패턴 코드 -> 매칭 건수."""
    return {code: len(rx.findall(text)) for code, _label, _sev, rx in _PATTERNS}


def pattern_examples(text: str, code: str, k: int = 3) -> list[str]:
    """해당 패턴이 걸린 원문 인용(매칭 span 주변 문맥) 최대 k개."""
    rx = next((p[3] for p in _PATTERNS if p[0] == code), None)
    if rx is None:
        return []
    out = []
    for m in rx.finditer(text):
        s, e = m.start(), m.end()
        ctx = text[max(0, s - 25): min(len(text), e + 15)].replace("\n", " ").strip()
        out.append(ctx)
        if len(out) >= k:
            break
    return out


PATTERN_META = {p[0]: (p[1], p[2]) for p in _PATTERNS}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def run(vendor_dir: pathlib.Path, out_json: pathlib.Path) -> dict:
    m = _load_metrics_module(vendor_dir)

    targets: list[tuple[str, str, str]] = []  # (그룹, 라벨, 본문)
    for label, text in _project_chapters(DP4B_PID):
        targets.append(("DP-4b", label, text))
    for label, path in REF_FILES:
        targets.append(("REF(인간)", label, path.read_text(encoding="utf-8")))
    for label, text in _project_chapters(DP1_PID)[:2]:
        targets.append(("DP-1(참고)", label, text))

    rows = []
    for group, label, text in targets:
        # genre='essay' — 도구 v1.6 baseline z-score용(웹소설 baseline 부재 → 참고만).
        r = m.compute_all_v2(text, genre="essay")
        pats = detect_patterns(text)
        examples = {code: pattern_examples(text, code) for code, cnt in pats.items() if cnt > 0}
        rows.append({
            "group": group,
            "label": label,
            "char_count": r["char_count"],
            "metrics_v1": r["metrics"],
            "z_scores_v1": r["z_scores"],
            "risk_band": r["risk_band"],
            "risk_score": r["risk_score"],
            "metrics_v2": r["v2_metrics"],
            "interference": r["v2_interference_index"],
            "patterns": pats,
            "pattern_examples": examples,
        })

    result = {
        "tool": "im-not-ai (epoko77-ai/im-not-ai, MIT) metrics_v2.py",
        "note": (
            "z_scores_v1 는 KatFish 에세이/시/초록 baseline 기준 — 웹소설 baseline 아님(참고용). "
            "v2 baseline 은 전부 _placeholder(uncalibrated). 숫자는 보조, 최종 판정은 정독 해석."
        ),
        "targets": [{"group": g, "label": l} for g, l, _ in targets],
        "rows": rows,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _print_summary(result: dict) -> None:
    rows = result["rows"]
    # 정량 지표 표(핵심 지표)
    hdr = ["label", "chars", "ecr%", "cir%", "seg", "hanja%", "ttr",
           "endDiv", "norm", "daStrk", "T3pron", "relNest", "risk"]
    print("\n=== 정량 지표(im-not-ai) ===")
    print(" | ".join(hdr))
    for r in rows:
        mv1, mv2 = r["metrics_v1"], r["metrics_v2"]
        print(" | ".join(str(x) for x in [
            r["label"],
            r["char_count"],
            round(mv1["ending_comma_rate"] * 100, 1),
            round(mv1["comma_inclusion_rate"] * 100, 1),
            round(mv1["comma_segment_length"], 1),
            round(mv1["hanja_nominalizer_density"] * 100, 2),
            round(mv2["lexical_diversity_ttr"], 3),
            round(mv2["ending_diversity"], 2),
            round(mv2["normalisation_score"], 2),
            mv2["da_streak_rate"],
            round(mv2["pronoun_density"], 4),
            mv2["relative_clause_nesting"],
            r["risk_band"],
        ]))
    # 패턴 카운트 표
    codes = [p[0] for p in _PATTERNS]
    print("\n=== 결정론 패턴 카운트(87패턴 서브셋) ===")
    print("label | " + " ".join(codes))
    for r in rows:
        print(r["label"] + " | " + " ".join(str(r["patterns"].get(c, 0)) for c in codes))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ST-10a im-not-ai 어댑터")
    ap.add_argument("--vendor", default=str(_DEFAULT_VENDOR),
                    help="im-not-ai vendored references 디렉토리")
    ap.add_argument("--json", default=str(REPORTS / "st10a_imnotai.json"),
                    help="결과 JSON 출력 경로")
    args = ap.parse_args(argv)
    result = run(pathlib.Path(args.vendor), pathlib.Path(args.json))
    _print_summary(result)
    print(f"\n[st10a] JSON 저장: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
