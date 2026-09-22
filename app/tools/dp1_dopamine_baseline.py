# -*- coding: utf-8 -*-
"""DP-1 도파민 판타지 baseline — 결정론 진단(LLM 0콜·읽기 전용).

목적: "무겁지 않고 재밌고 도파민 터지는" 축의 현재 실력을 결정론 지표로 실측해
      DP-2(능동성 개입)·DP-3(페이오프 소비) 설계 입력을 만든다. 판정기 아님 — 측정·가시화.

측정 축(작품별·회차별):
  ① 능동 개시율(IN-27① 재구성) : 회차별 주인공이 '판을 여는가(agentive)' vs '끌려가는가(reactive)'
     - 원 스크래치 툴 소실(untracked) → 동일 기법으로 재구성:
       needle=주인공 지칭형(어간정규화, drift._stem/_PARTICLES 재사용, 어간≥2 가드),
       주인공 언급 문장에서 개시 어휘 vs 반응 어휘 순비교. advisory 프록시(정밀 아님) — 편집자 정독이 확증.
  ② hook_type 분포·최대 run   : ChapterRecord.hook_type 수열(동형 카덴차 = 훅 단조)
  ③ 약속원장 정산 간격         : promise_ledger 의 paid_chapter 로 회차별 since_payoff·정산 간격
  ④ ST-1 가벼움(신문체 유지)    : style_lightness_baseline.lightness_metrics 재사용(대사비중·문단리듬·틱·CV)
  ⑤ 회차 자수                  : 정제 본문 비공백 자수 + raw 자수

대상: 라이브 data/projects 에서 title 에 '[실험 DP-1]' 포함 작품 자동 발견(--pids 로 명시 가능).
산출: tools/reports/dp1_determinism.json  (편집자 정독+종합은 dp1_dopamine_baseline.md 에서 병합)
사용: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/dp1_dopamine_baseline.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

APP = pathlib.Path(__file__).resolve().parents[1]
PROJ = APP / "data" / "projects"
REPORTS = APP / "tools" / "reports"

# 어간정규화 — drift 의 레퍼런스 구현 재사용(needle 만 정규화, 어간≥2 가드)
from novelcopilot.engine.drift import _stem                      # noqa: E402
from tools.style_lightness_baseline import lightness_metrics     # noqa: E402  (ST-1 재사용)

# ── IN-27① 개시/반응 닫힌 어휘(어간 — substring, 교착어 안전: 모두 ≥2음절) ──────────────
# 주인공이 '판을 여는' 동작(선수·역습·주도) — 지면에서 실행되는 수. 반응 어휘보다 우세하면 그 회차 = 능동.
# DP-11: 내면 결단어(결정·결심·계획·설계)는 '지면에서 실행되는 수'가 아니라 내면의 결심/설계라
#        개시(agentive)로 오집계돼 능동 개시율을 부풀렸다 → 아래 _INNER_DECISION 중립 버킷으로 분리.
#        (arc_planner protagonist_move gloss 강화 '지면에서 실행되는 수'와 측정축이 대칭.)
_INITIATE = [
    "결의", "나섰", "나서", "먼저", "선택", "움직", "공격", "제안", "명령",
    "뽑아", "뽑았", "던지", "던졌", "베었", "베어", "잡아", "잡았", "향해", "앞서", "선수",
    "선공", "노렸", "노려", "노린", "준비", "파고", "밀어붙", "몰아", "나아가",
    "역습", "반격", "되갚", "받아쳤", "받아치", "쳐냈", "제압", "장악", "주도", "이끌",
    "처치", "해치웠", "쓸어", "발동", "시전", "겨눴", "겨누", "덤벼", "짓밟",
    "도발", "판을", "손을 뻗", "끊어", "휘둘", "찔렀", "찔러", "몰아붙", "먼저 움직",
]
# 내면 결단(중립 버킷) — 개시(_INITIATE)로도 반응(_REACT)으로도 세지 않는다. agency 분모에서 제외해
#   내면 결심을 지면 개시로 오집계하던 소스를 차단하고, 가시화용으로만 별도 카운트(측정 중립).
_INNER_DECISION = ["결정", "결심", "계획", "설계"]
_REACT = [
    "당했", "당하", "당한", "끌려", "휩쓸", "몰렸", "몰려", "쫓기", "쫓겨", "놀랐", "놀라",
    "당황", "어쩔 수 없", "피했", "피하", "막았", "막아", "물러", "밀렸", "밀려", "무너",
    "쓰러", "비명", "겁먹", "두려", "얼어붙", "굳었", "멈칫", "주저", "흔들렸", "당혹",
    "허둥", "눌렸", "짓눌", "휘말", "속수무책", "질질", "버둥", "허우적",
]

# ── DP-14: 1인칭(pov='first') agency 축 — 주어 생략 구조적 실명(DP-4b) 복구 ─────────────
# 실측(dp4b_analyze): pov='first' 에서 needle(주인공 지칭형) 기반 개시 휴리스틱이 전 회차 0.0.
#   원인은 프로즈가 아니라 측정 — 1인칭 서술은 주인공을 주어 생략('나는/내가' 미표기)으로 쓰므로
#   needle 매칭 문장(prot_sents)이 회차당 0~2개로 붕괴해 init/react 를 잴 표본이 사라진다(DP-4b 9화 실증).
# 복구(측정 전용·판정기 0): pov='first' 일 때만 대상 문장 풀을 두 근사로 확장(3인칭 경로 바이트 불변).
#   (1) 1인칭 명시 주어 문장 — '나는/내가/…'(어절형 표지) 보유 문장은 주인공 서술로 확정(파편 허용).
#   (2) 주어 생략 1인칭 문장 — 문단 첫 문장이 '평서 종결(~다 종결)'로 끝나고,
#       (a) 타 인물 명명 주어가 없고 (b) 명시 주격/화제 표지를 가진 비-1인칭 주어(제3자)도 없으면
#       서술자(=주인공)의 지면 서술로 근사. 부사구 파편('…향해')은 종결부호+종결어미 게이트로,
#       명시 제3자 주어('남자가 물러섰다')는 주격표지 게이트로 배제(구조 게이트 — 부정 블록리스트 아님, no-whack-a-mole).
#   ※ MED-1(적대검증 정정): _FP_FINITE 는 '동작 술어'만이 아니라 '~다'로 끝나는 모든 평서문(형용사·계사
#     '이다'·존재사 '있다'·명사구 '것이다')을 통과시킨다(마지막 대안 '다'가 포괄). 즉 게이트가 배제하는 건
#     '종결부호 없는 부사구 파편'이지 '비-동작 술어'가 아니다. 비-동작 평서문은 개시/반응 어휘가 없어
#     init/react 에 0/0 기여(score 중립) → 방향 무영향. 주어 있는 서술문은 (b) 주격표지 게이트가 별도 배제.
#   내면 결단(_INNER_DECISION)은 3인칭과 동일하게 중립 버킷 유지(개시 부풀림 차단).
# 캘리브레이션: DP-4b 9화 정독(폐던전 잠입·그림자 늑대 3처치·함정 회피 = 강한 agentive)과 방향 일치
#   (needle 축 0.0 → 1인칭 축 active=True score≈0.83). advisory 프록시(정밀 아님) — 편집자 정독이 확증.
#   ※ MED-2(적대검증 정정): 9화 init=5 는 유효 개시 2건('움직였다'·'발을 뗐다')에 substring 오탐 3건이
#     더해진 값이다 — '전생에서…잡아봤다'(과거 회상, 지면 동작 아님·'잡아'), '숨을 몰아쉬었다'(탈진 반응,
#     '몰아' 충돌), '내 발소리보다 먼저였다'(비교부사, '먼저' 충돌). 즉 방향(active=True)의 실근거는
#     유효 2건이고 score 0.83 은 오탐 3건이 밀어올린 상한이다. 임계·자동재작성 게이트가 없어 실해는 없으나
#     캘리브레이션 서사를 실제보다 강하게 읽지 말 것(닫힌 어휘 substring 축의 잔여 오탐 = 3인칭 축과 동급).
_FP_SUBJ = ("나는", "내가", "나를", "나도", "나에게", "나한테", "내게")   # 1인칭 명시 주어(어절형 — '내부' 등 과매칭 회피)
# 부정/무산 종결 — 개시 어휘가 부정문에 박히면 지면 개시가 아니다('반격할 틈이 없었다'=반응). 개시→반응 재분류.
_FP_NEG = ("없었다", "없다", "못했다", "못한다", "않았다", "않는다", "지 못", "수 없")
# 평서 종결('~다') — '다'로 끝나는 평서문만 주어 생략 후보(종결부호 없는 부사구 파편 배제).
#   NOTE(MED-1): 마지막 대안 '다'가 모든 '~다' 평서문(형용사·계사·존재사·명사구 포함)을 포괄하므로
#   앞의 어미 나열은 사실상 문서용이다. '동작 술어만' 게이트가 아니다 — 비-동작 평서문은 개시/반응
#   어휘가 없어 score 중립(0/0), 주어 있는 서술문은 _has_third_subject 가 별도 배제.
_FP_FINITE = re.compile(r"(었다|았다|였다|랐다|렸다|겼다|녔다|졌다|뎠다|텄다|뜬다|린다|한다|었다\.|다)[.!?…\"'”’)\s]*$")
# 명시 주격/화제 표지가 붙은 명사(주어) — 2자+ 한글 어절 + (이|가|은|는|께서). 주어 생략 경로에서 제3자 주어 배제.
_FP_SUBJ_MARK = re.compile(r"([가-힣]{2,})(?:이|가|은|는|께서)(?=\s|$|[,·—\"'”’)])")

_SENT_SPLIT = re.compile(r"\n+|(?<=[.!?…])\s+")


def _sents(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s and s.strip()]


def _para_first_sents(text: str) -> set[str]:
    """문단(빈 줄/줄바꿈 분리) 첫 문장 집합 — 주어 생략 1인칭 동작의 개시 위치 근사(DP-14)."""
    out: set[str] = set()
    for par in (text or "").split("\n"):
        par = par.strip()
        if not par:
            continue
        ss = _sents(par)
        if ss:
            out.add(ss[0])
    return out


def _needles(name: str) -> list[str]:
    """주인공 지칭형 needle 집합 — 성명 전체·어간·(3자 성명이면)이름 부분·이름 어간."""
    base = (name or "").strip()
    out: set[str] = set()
    if not base:
        return []
    out.add(base)
    st = _stem(base)
    if len(st) >= 2:
        out.add(st)
    if len(base) == 3:                       # 한국식 성(1)+이름(2) → 이름만 지칭도 흔함
        given = base[1:]
        out.add(given)
        gst = _stem(given)
        if len(gst) >= 2:
            out.add(gst)
    return sorted(out, key=len, reverse=True)


def detect_protagonist(world: dict, chapters: list[dict]) -> tuple[str, list[str]]:
    """주인공 결정론 식별 = 전 회차 최다 언급 엔티티(needle 어간 substring 합). 반환 (name, needles)."""
    ents = [e for e in (world.get("entities") or []) if (e.get("name") or "").strip()]
    corpus = "\n".join((c.get("text") or "") for c in chapters)
    best_name, best_needles, best_hits = "", [], -1
    for e in ents:
        nm = e["name"].strip()
        nds = _needles(nm)
        hits = sum(corpus.count(nd) for nd in nds)
        if hits > best_hits:
            best_name, best_needles, best_hits = nm, nds, hits
    return best_name, best_needles


def other_needles(world: dict, prot_name: str) -> list[str]:
    """DP-14: 주인공 외 명명 인물 needle 합집합 — 1인칭 축 주어생략 후보에서 '타 인물의 동작' 배제용."""
    out: set[str] = set()
    for e in (world.get("entities") or []):
        nm = (e.get("name") or "").strip()
        if nm and nm != prot_name:
            out.update(_needles(nm))
    return sorted(out, key=len, reverse=True)


def _has_third_subject(s: str, needles: list[str]) -> bool:
    """문장에 명시 주격/화제 표지를 가진 비-1인칭·비-주인공 명사(제3자 주어)가 있는가.
    있으면 그 문장은 '주어 생략'이 아니라 제3자 명시 주어 문장 → 주인공 주어생략 경로 배제."""
    for m in _FP_SUBJ_MARK.finditer(s):
        noun = m.group(1)
        if noun in ("나", "내") or any(nd in noun for nd in needles):
            continue                         # 1인칭/주인공 주어면 제3자 아님
        return True
    return False


def _first_person_pool(text: str, needles: list[str], other_needles: list[str]) -> list[str]:
    """DP-14 pov='first' 대상 문장 풀 — needle 문장 ∪ 1인칭 명시주어 ∪ 주어생략 동작문단첫문장.
    타 인물이 명명 주어인 문단 첫 문장은 제외(그 인물의 동작). 명시 1인칭/needle 은 파편도 허용."""
    para_first = _para_first_sents(text)
    pool: list[str] = []
    for s in _sents(text):
        has_nd = any(nd in s for nd in needles)
        has_1p = any(m in s for m in _FP_SUBJ)
        if has_nd or has_1p:
            pool.append(s)
            continue
        # 주어 생략 경로: 문단 첫 문장 + 타 인물 명명 주어 없음 + 동작 종결(finite) + 제3자 명시 주어 없음
        if (s in para_first and not any(o in s for o in other_needles)
                and _FP_FINITE.search(s) and not _has_third_subject(s, needles)):
            pool.append(s)
    return pool


def _fp_counts(prot: list[str]) -> tuple[int, int, int]:
    """DP-14 1인칭 축 버킷 — 어휘 우선순위(init→react→inner) 단일 배정 + 부정 재분류.
    3인칭 축(독립 카운트)과 별도 — 부정문 개시('반격할 틈이 없었다')를 반응으로 되돌린다."""
    init = react = inner = 0
    for s in prot:
        neg = any(g in s for g in _FP_NEG)
        hit_init = any(k in s for k in _INITIATE)
        if hit_init and not neg:
            init += 1
        elif any(k in s for k in _REACT) or (neg and hit_init):
            react += 1
        elif any(k in s for k in _INNER_DECISION):     # DP-11: 내면 결단(중립) — 분모 제외
            inner += 1
    return init, react, inner


def activity_metrics(chapters: list[dict], needles: list[str],
                     pov: str = "", other_needles: list[str] | None = None) -> dict:
    """IN-27① 재구성 — 회차별 주인공 개시(agentive) vs 반응(reactive) 순비교.

    pov='first'(DP-14): 주어 생략으로 needle 매칭이 붕괴하는 1인칭 서술을 위해 대상 문장 풀을
      1인칭 명시주어·주어생략 동작문단첫문장으로 확장 + 부정 재분류(측정 전용). 기본 경로(pov 미지정
      또는 'third*')는 기존 needle 축과 **바이트 동일**(하위호환·3인칭 회귀 없음)."""
    first = (pov == "first")
    others = other_needles or []
    flags, scores, rows = [], [], []
    for c in chapters:
        text = c.get("text") or ""
        if first:                                     # DP-14: 1인칭 축(확장 풀 + 우선순위 배정 + 부정 재분류)
            prot = _first_person_pool(text, needles, others)
            init, react, inner = _fp_counts(prot)
        else:                                         # 기존 needle 축(독립 카운트) — 바이트 불변
            prot = [s for s in _sents(text) if any(nd in s for nd in needles)]
            init = sum(1 for s in prot if any(k in s for k in _INITIATE))
            react = sum(1 for s in prot if any(k in s for k in _REACT))
            inner = sum(1 for s in prot if any(k in s for k in _INNER_DECISION))
        denom = init + react                          # 내면 결단어는 분모에서 빠짐(개시 오집계 차단)
        score = round(init / denom, 3) if denom else 0.0
        active = bool(init > react and init >= 2)     # 순-능동 회차(개시 우세 + 최소 2건 실체)
        flags.append(active)
        scores.append(score)
        rows.append({"chapter": c.get("chapter"), "prot_sents": len(prot),
                     "init": init, "react": react, "inner": inner,
                     "agency_score": score, "active_open": active})
    n = len(flags) or 1
    return {"needles": needles, "pov_axis": ("first" if first else "needle"),
            "active_open_chapters": [rows[i]["chapter"] for i, f in enumerate(flags) if f],
            "ratio": round(sum(flags) / n, 3),
            "agency_score_mean": round(sum(scores) / n, 3),
            "flags": flags, "per_chapter": rows}


def _max_run(seq: list[str]) -> int:
    best = run = 0
    prev = None
    for x in seq:
        run = run + 1 if x == prev else 1
        prev = x
        best = max(best, run)
    return best


def hook_metrics(chapters: list[dict]) -> dict:
    seq = [(c.get("hook_type") or "").strip() or "∅" for c in chapters]
    counts: dict[str, int] = {}
    for h in seq:
        counts[h] = counts.get(h, 0) + 1
    n = len(seq) or 1
    top = max(counts, key=counts.get) if counts else "∅"
    return {"sequence": seq, "counts": counts, "top_hook": top,
            "top_ratio": round(counts.get(top, 0) / n, 3),
            "max_same_run": _max_run(seq), "distinct": len(counts)}


def ledger_metrics(ledger: dict, n_ch: int) -> dict:
    """약속원장 정산 간격 — paid_chapter≥1(본문 추출 P3)만 실지불로. paid_chapter=0/sync 는 P1 설계라벨(별도 표기)."""
    promises = ledger.get("promises") or []
    opened_per = [0] * (n_ch + 1)
    prose_paid_ch: list[int] = []
    design_paid = 0
    for p in promises:
        oc = int(p.get("opened_chapter") or 0)
        if 1 <= oc <= n_ch:
            opened_per[oc] += 1
        if p.get("status") == "paid":
            pc = p.get("paid_chapter")
            if isinstance(pc, int) and pc >= 1:
                prose_paid_ch.append(pc)
            else:
                design_paid += 1
    paid_set = sorted(set(prose_paid_ch))
    # 회차별 since_payoff(본문 추출 기준) — 이전(포함) 지불 회차와의 거리. 지불 전엔 None
    since = []
    for n in range(1, n_ch + 1):
        prior = [pc for pc in paid_set if pc <= n]
        since.append((n - max(prior)) if prior else None)
    # 정산 간격(연속 지불 회차 사이 gap; 첫 지불은 1화 기준 gap)
    intervals = []
    prev = 0
    for pc in paid_set:
        intervals.append(pc - prev)
        prev = pc
    tail_gap = n_ch - prev            # 마지막 지불 이후 현재까지 미정산 구간
    open_end = sum(1 for p in promises if p.get("status") == "open")
    return {"n_promises": len(promises), "prose_paid_chapters": paid_set,
            "design_label_paid": design_paid, "opened_per_chapter": opened_per[1:],
            "since_payoff_series": since, "settlement_intervals": intervals,
            "tail_unsettled_gap": tail_gap,
            "max_gap": max(intervals + [tail_gap]) if (intervals or tail_gap) else 0,
            "mean_interval": round(sum(intervals) / len(intervals), 2) if intervals else None,
            "open_balance_end": open_end,
            "opened_total": sum(opened_per[1:]), "paid_total_prose": len(prose_paid_ch)}


def _series(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    n = len(vals)
    h1 = vals[: n // 2] or vals
    h2 = vals[n // 2:] or vals
    return {"mean": round(sum(vals) / n, 3), "min": round(min(vals), 3), "max": round(max(vals), 3),
            "h1_mean": round(sum(h1) / len(h1), 3), "h2_mean": round(sum(h2) / len(h2), 3),
            "series": [round(v, 3) for v in vals]}


def lightness_summary(chapters: list[dict], roster: set[str]) -> dict:
    """ST-1 가벼움 — 회차별 lightness_metrics 재사용 후 핵심 축만 추세 집계(신문체 유지 확인)."""
    per = [lightness_metrics(c.get("text") or "", roster) for c in chapters]
    pick = ["dialogue_char_ratio", "dialogue_para_ratio", "para_char_p90",
            "para_over3sent_ratio", "sent_len_cv", "max_narration_run"]
    out = {k: _series([m[k] for m in per]) for k in pick}
    tic_total = [m["tic_neg_correction"] + m["tic_judgment_suspend"]
                 + m["tic_pseudo_precision"] + m["tic_dialogue_gloss"] for m in per]
    out["tics_per_chapter"] = tic_total
    out["tics_total"] = sum(tic_total)
    return out


def length_metrics(chapters: list[dict]) -> dict:
    raw = [len(c.get("text") or "") for c in chapters]
    nws = [len(re.sub(r"\s", "", c.get("text") or "")) for c in chapters]
    def stat(xs):
        s = sorted(xs)
        n = len(s) or 1
        return {"n": len(xs), "mean": round(sum(xs) / n), "median": s[n // 2] if s else 0,
                "min": min(xs) if xs else 0, "max": max(xs) if xs else 0}
    return {"raw_per_chapter": raw, "nws_per_chapter": nws,
            "raw": stat(raw), "nws": stat(nws)}


def diagnose(pid: str) -> dict:
    p = json.loads((PROJ / f"{pid}.json").read_text(encoding="utf-8"))
    world = p["world"]
    chs = sorted([c for c in p["chapters"] if c.get("status") == "FINALIZED"],
                 key=lambda c: c["chapter"])
    roster = {(e.get("name") or "").strip() for e in (world.get("entities") or []) if e.get("name")}
    prot_name, needles = detect_protagonist(world, chs)
    pov = ((world.get("style") or {}).get("pov") or "")   # DP-14: 저장된 서술 시점(StyleSpec.pov) — 1인칭 축 스위치
    others = other_needles(world, prot_name) if pov == "first" else []
    return {
        "project_id": pid, "title": world.get("title", ""), "genre": world.get("genre", ""),
        "seed_tone": (p.get("seed") or {}).get("tone", ""), "pov": pov,
        "n_finalized": len(chs), "protagonist_detected": prot_name,
        "usage_total": p.get("usage_total", {}),
        "length": length_metrics(chs),
        "hooks": hook_metrics(chs),
        "activity": activity_metrics(chs, needles, pov=pov, other_needles=others),
        "ledger": ledger_metrics(p.get("promise_ledger") or {}, len(chs)),
        "lightness": lightness_summary(chs, roster),
    }


def discover_pids() -> list[str]:
    out = []
    for f in sorted(PROJ.glob("*.json")):
        if ".rag." in f.name:
            continue
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "[실험 DP-1]" in (p.get("world", {}).get("title") or ""):
            out.append(f.stem)
    return out


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="DP-1 도파민 baseline 결정론 진단(읽기 전용)")
    ap.add_argument("--pids", default="", help="쉼표구분 project id(생략 시 '[실험 DP-1]' 태그 자동발견)")
    args = ap.parse_args(argv)
    pids = [x.strip() for x in args.pids.split(",") if x.strip()] or discover_pids()
    if not pids:
        print("대상 없음 — '[실험 DP-1]' 태그 작품 미발견. --pids 로 지정하세요.")
        return 2
    results = {pid: diagnose(pid) for pid in pids}
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "dp1_determinism.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for pid, r in results.items():
        a, h, lg = r["activity"], r["hooks"], r["ledger"]
        print(f"\n=== {r['title']} ({pid}) — {r['genre']} ===")
        print(f"  회차={r['n_finalized']} 주인공={r['protagonist_detected']} needles={a['needles']}")
        print(f"  ① 능동개시율={a['ratio']} (agency_mean={a['agency_score_mean']}) 능동회차={a['active_open_chapters']}")
        print(f"     per-ch init/react/inner: " + " ".join(f"{x['chapter']}:{x['init']}/{x['react']}/{x.get('inner', 0)}" for x in a['per_chapter']))
        print(f"  ② hook top={h['top_hook']}({h['top_ratio']}) maxrun={h['max_same_run']} distinct={h['distinct']} seq={h['sequence']}")
        print(f"  ③ 정산: paid_ch={lg['prose_paid_chapters']} since={lg['since_payoff_series']} intervals={lg['settlement_intervals']} tail_gap={lg['tail_unsettled_gap']} open_end={lg['open_balance_end']} opened/ch={lg['opened_per_chapter']}")
        print(f"  ④ 가벼움: 대사자비={r['lightness']['dialogue_char_ratio']['mean']} 대사문단={r['lightness']['dialogue_para_ratio']['mean']} p90문단자수={r['lightness']['para_char_p90']['mean']} CV={r['lightness']['sent_len_cv']['mean']} 틱합={r['lightness']['tics_total']}")
        print(f"  ⑤ 자수(nws) mean={r['length']['nws']['mean']} min={r['length']['nws']['min']} max={r['length']['nws']['max']} per={r['length']['nws_per_chapter']}")
        u = r["usage_total"]
        print(f"  usage: chat_calls={u.get('chat_calls')} chat_tokens={u.get('chat_tokens')}")
    print("\nreport:", REPORTS / "dp1_determinism.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
