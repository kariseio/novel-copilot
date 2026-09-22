# -*- coding: utf-8 -*-
"""DP-4 매화 검증 루프 러너 — DP-6(소비 원장)·DP-2(선수 슬롯) 개입을 '한 화씩' 검증.

사용자 지시 방법론: 일괄 생성 금지. 회차 1개 생성 → 결정론 게이트 + (오케스트레이터의) 초비판 독자
리뷰 → 문제 시 fix 지시로 해당 화 재생성(최대 2회) → 통과 후 다음 화. 이 스크립트는 그 루프의
'기계 부분'(생성·재생성·결정론 게이트·비트 캡처·본문 덤프)만 담당한다. 독자 리뷰·진단·fix 문안은
호출자(에이전트)가 각 화 덤프를 읽고 판정한다.

명령:
  create                         : 시드A(DP-1 실패 케이스)로 target 24 신규 작품 생성('[실험 DP-4]' 태그) → pid 출력
  gen   <pid>                    : 다음 회차 생성 + 결정론 게이트 + 비트 캡처 + 본문 덤프
  regen <pid> "<fix instruction>": 마지막 회차를 fix 지시로 재생성(FE-2 경로) + 게이트 + 덤프
  gate  <pid> [N]                : (LLM 0콜) 회차 N(생략=마지막) 결정론 게이트만 재계산·출력
  analyze <pid> [dp1_pid]        : (LLM 0콜) DP-4 전 회차 결정론 종합 + DP-1 시드A 대비 재탕/능동성 대조

산출물(tools/reports/):
  dp4_ch<N>.txt      회차 본문 + 비트(protagonist_move/key_events/소비원장) + 게이트 결과(에이전트 정독용)
  dp4_beats.jsonl    회차별 비트 캡처(protagonist_move·required_override·climax_override) — 개입 관측
  dp4_gate.json      회차별 결정론 게이트 누적
  dp4_analyze.json   종합 대조(analyze)

실행(app/ 에서):
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/dp4_loop.py gen <pid>
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

APP = pathlib.Path(__file__).resolve().parents[1]
PROJ = APP / "data" / "projects"
REPORTS = APP / "tools" / "reports"
BEATS_LOG = REPORTS / "dp4_beats.jsonl"
GATE_LOG = REPORTS / "dp4_gate.json"

# ── DP-1 시드A(4.0점·잔존 27 실패 케이스) 동일 문안 — genre/tone/premise/protagonist 그대로, target 만 24 ──
SEED_A = {
    "title": "1위의 재림",
    "genre": "현대판타지(회귀·먼치킨)",
    "tone": "통쾌한 사이다, 시원한 전개, 주인공이 판을 주도",
    "premise": ("대륙 서열 1위 헌터가 최약체이던 20살로 회귀한다. 미래의 지식과 1위의 감각은 그대로 — "
                "이번 생은 당하기 전에 먼저 움직인다. 자신을 버렸던 길드, 아직 발견되지 않은 유물, "
                "곧 터질 게이트 사태의 순서를 전부 알고 있다."),
    "protagonist_hint": ("회귀한 전 서열 1위, 미래 지식 보유, 선수(先手)를 치는 성격 — 당하지 않고 먼저 판을 설계"),
    "target_chapters": 24,      # DP-1 의 target 8 교훈: 에피소드당 2화 고정 압축 교란을 제거(에피소드 산술 분리)
}
DP4_TAG = "[실험 DP-4]"

# 결정론 게이트 재사용 — DP-1 baseline 과 동일 기준(능동 개시·주인공 식별)
from tools.dp1_dopamine_baseline import (  # noqa: E402
    _needles, detect_protagonist, activity_metrics, _INITIATE, _REACT, _sents)
from novelcopilot.engine.drift import _stem, _event_keywords, uncovered  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 비트 캡처(monkeypatch) — DP-2 protagonist_move·DP-6 소비원장(required_override) 관측.
#   소스 무수정: 러너 프로세스에서만 ArcPlanner.beat_for_episode 를 래핑해 인자·산출을 기록한다.
# ─────────────────────────────────────────────────────────────────────────────
_CAPTURED: dict[int, dict] = {}


def _install_beat_capture():
    from novelcopilot.worldgen.arc_planner import ArcPlanner
    if getattr(ArcPlanner, "_dp4_wrapped", False):
        return
    orig = ArcPlanner.beat_for_episode

    def wrapped(self, world, arc, episode, chapter, is_finale, *a, **kw):
        beat = orig(self, world, arc, episode, chapter, is_finale, *a, **kw)
        try:
            _CAPTURED[int(chapter)] = {
                "chapter": int(chapter),
                "is_finale": bool(is_finale),
                "episode_id": getattr(episode, "episode_id", None),
                "episode_required": list(getattr(episode, "required_events", []) or []),
                "episode_climax": getattr(episode, "climax", "") or "",
                # DP-6 소비원장: copilot 이 이미 실현된 required/climax 를 차감해 넘긴 override(비-finale)
                "required_override": (list(kw.get("required_override"))
                                      if kw.get("required_override") is not None else None),
                "climax_override": kw.get("climax_override"),
                # DP-2 선수 슬롯 + 산출 비트
                "beat_protagonist_move": getattr(beat, "protagonist_move", "") or "",
                "beat_key_events": list(getattr(beat, "key_events", []) or []),
                "beat_place": getattr(beat, "place", "") or "",
                "beat_summary": getattr(beat, "summary", "") or "",
            }
        except Exception:
            pass
        return beat

    ArcPlanner.beat_for_episode = wrapped
    ArcPlanner._dp4_wrapped = True


def _persist_captured(chapter: int):
    rec = _CAPTURED.get(chapter)
    if not rec:
        return None
    REPORTS.mkdir(parents=True, exist_ok=True)
    # jsonl: 회차별 최종 캡처 1건(덮어쓰기 병합)
    rows = {}
    if BEATS_LOG.exists():
        for ln in BEATS_LOG.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    r = json.loads(ln)
                    rows[int(r["chapter"])] = r
                except Exception:
                    pass
    rows[chapter] = rec
    BEATS_LOG.write_text(
        "\n".join(json.dumps(rows[k], ensure_ascii=False) for k in sorted(rows)) + "\n",
        encoding="utf-8")
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# 결정론 게이트(LLM 0콜)
# ─────────────────────────────────────────────────────────────────────────────
_CW = re.compile(r"[가-힣]{2,}")

# PR-2: 재탕 근사 원자 부품(_content_stems·_overlap_coef·_jaccard)은 engine.verification 로 승격 이관됨
#   (엔진이 tools import 없이 회차 통합 검증에 재탕계수를 집계). dp4 러너는 그 부품을 재사용해
#   비트층 재탕·prev_pairs·근거 문장 등 러너 전용 진단만 아래에서 덧댄다(중복 정의 소멸).
from novelcopilot.engine.verification import _content_stems, _overlap_coef, _jaccard  # noqa: E402


def _stem_freq(text: str, head: int | None = None) -> dict[str, int]:
    """도입부(head자) 내용어 어간 빈도 — 겹친 상위 내용어 정렬용(어간 정규화 필수, 조사 변이 흡수)."""
    t = (text or "")[:head] if head else (text or "")
    freq: dict[str, int] = {}
    for w in _CW.findall(t):
        s = _stem(w)
        freq[s] = freq.get(s, 0) + 1
    return freq


def _first_sentence_with_stem(text: str, stem: str) -> str:
    """어간을 (어간정규화 매칭으로) 포함하는 첫 문장 원자료 — n-gram 아닌 '해당 어간 포함 문장' 통째로.
    본문의 조사형('광민이/광민은')도 어간('광민')으로 정규화해 매칭(교착어 substring 결함 회피)."""
    for s in _sents(text):
        for w in _CW.findall(s):
            if _stem(w) == stem:
                return s
    return ""


def _overlap_evidence(cur_text: str, prev_text: str, head: int, top: int = 6) -> dict:
    """직전화·현재화 도입부에서 겹친 상위 내용어 어간 + 그 어간 포함 문장(각 1개) 원자료 산출.
    임계 판정 없음 — 재탕 의심 대목을 정독에 그대로 넘기는 근거 문장만 뽑는다(_stem 어간 정규화 필수)."""
    fc = _stem_freq(cur_text, head)
    fp = _stem_freq(prev_text, head)
    shared = set(fc) & set(fp)
    if not shared:
        return {"stems": [], "stem": None, "prev_sentence": "", "cur_sentence": ""}
    # 겹친 어간을 (현재+직전 도입부 합산 빈도 내림차순, 동률은 어간 문자열)로 정렬 → 상위 내용어.
    ranked = sorted(shared, key=lambda s: (-(fc[s] + fp[s]), s))[:top]
    # 근거 어간: 상위 중 양쪽 도입부(전체 본문 아님)에서 실제 문장을 뽑을 수 있는 첫 어간.
    hp, hc = (prev_text or "")[:head], (cur_text or "")[:head]
    lead_stem, prev_sent, cur_sent = None, "", ""
    for s in ranked:
        ps = _first_sentence_with_stem(hp, s)
        cs = _first_sentence_with_stem(hc, s)
        if ps and cs:
            lead_stem, prev_sent, cur_sent = s, ps, cs
            break
    return {"stems": ranked, "stem": lead_stem,
            "prev_sentence": prev_sent, "cur_sentence": cur_sent}


def retread_metrics(chapters: list[dict], head: int = 600) -> list[dict]:
    """직전 화 재탕(도입 장면 재인스턴스) — DP-1 주범 지표(advisory — 최종 판정은 정독).
    각 회차 도입부(head자) 내용어 어간을 모든 선행 회차 도입부와 대조, 최대 유사 회차 지목.
      · opening_coef: overlap coefficient(부분 재인스턴스 민감) — 재탕 flag 기준.
      · opening_jac : Jaccard(참고).
      · keyevents_overlap_prev: 비트 key_events 어간 Jaccard(직전 회차) — DP-6 이 직접 겨눈 '비트층 재탕'
        (캡처 병합된 DP-4 에서만 채워짐; DP-1 은 비트 미영속이라 0).
      · prev_pairs (DP-19): 직전 2화 각각과 head 대조한 {chapter, coef, jac} — 정독이 '어느 직전화 재탕'인지 본다.
      · overlap_stems / evidence (DP-19): prev_pairs 중 최대 coef 직전화와 겹친 상위 내용어 어간 + 그 어간을
        포함한 직전화·현재화 원문 문장 각 1개(근거). 임계 판정 없음 — 정독에 넘길 재료일 뿐."""
    rows = []
    for i, c in enumerate(chapters):
        head_i = _content_stems(c.get("text"), head)
        best_c, best_j, best_k = 0.0, 0.0, None
        for j in range(i):
            head_j = _content_stems(chapters[j].get("text"), head)
            co = _overlap_coef(head_i, head_j)
            if co > best_c:
                best_c, best_j, best_k = co, _jaccard(head_i, head_j), chapters[j].get("chapter")
        # DP-19 ①: 직전 2화 각각과 head 대조(전체 선행 최댓값과 별개 — '바로 앞 흐름' 재탕을 분리 관측).
        prev_pairs = []
        for j in range(max(0, i - 2), i):
            head_j = _content_stems(chapters[j].get("text"), head)
            prev_pairs.append({"chapter": chapters[j].get("chapter"),
                               "coef": _overlap_coef(head_i, head_j),
                               "jac": _jaccard(head_i, head_j)})
        # DP-19 ②: 직전 2화 중 최대 coef 화와의 겹친 상위 어간 + 근거 문장(직전화·현재화 각 1개).
        overlap_stems, evidence = [], {"stem": None, "prev_sentence": "", "cur_sentence": ""}
        if prev_pairs:
            lead = max(prev_pairs, key=lambda p: p["coef"])
            lead_ch = lead["chapter"]
            prev_c = next((x for x in chapters[:i] if x.get("chapter") == lead_ch), None)
            if prev_c is not None:
                ev = _overlap_evidence(c.get("text"), prev_c.get("text"), head)
                overlap_stems = ev["stems"]
                evidence = {"prev_chapter": lead_ch, "prev_coef": lead["coef"], "stem": ev["stem"],
                            "prev_sentence": ev["prev_sentence"], "cur_sentence": ev["cur_sentence"]}
        ke_prev = 0.0
        ev_now = c.get("key_events") or []
        if i > 0 and ev_now and (chapters[i - 1].get("key_events")):
            s_now = {_stem(w) for e in ev_now for w in _CW.findall(e)}
            s_prev = {_stem(w) for e in chapters[i - 1]["key_events"] for w in _CW.findall(e)}
            ke_prev = _jaccard(s_now, s_prev)
        rows.append({"chapter": c.get("chapter"),
                     "opening_coef": best_c, "opening_jac": best_j, "opening_with": best_k,
                     "keyevents_overlap_prev": ke_prev,
                     "prev_pairs": prev_pairs, "overlap_stems": overlap_stems, "evidence": evidence,
                     # 캘리브레이션: DP-1 시드A 정상 인접 회차 coef≈0.10~0.13, 문서화된 재탕 회차(ch6→ch5 제자리걸음)=0.32.
                     #   0.30 은 배치 위치 참조(정독이 그 대목부터 본다)일 뿐 — 자동 PASS/FAIL 판정 아님.
                     "retread_flag": bool(best_c >= 0.30)})
    return rows


def _nws(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


def move_reflection(beat_rec: dict | None, chapter_text: str, needles: list[str]) -> dict:
    """DP-2 protagonist_move 가 (a) 비트에 떴는가 (b) key_events 에 반영됐는가 (c) 본문에 실현됐는가."""
    if not beat_rec:
        return {"present": None, "note": "비트 캡처 없음(재수화/구 회차)"}
    mv = (beat_rec.get("beat_protagonist_move") or "").strip()
    if not mv:
        return {"present": False, "in_key_events": False, "in_text": False}
    mv_stems = [s for s in ( _stem(w) for w in _CW.findall(mv)) if len(s) >= 2]
    ke_join = " ".join(beat_rec.get("beat_key_events") or [])
    in_ke = sum(1 for s in mv_stems if s in ke_join)
    in_tx = sum(1 for s in mv_stems if s in (chapter_text or ""))
    denom = max(1, len(mv_stems))
    return {"present": True, "move": mv,
            "in_key_events": round(in_ke / denom, 2), "in_text": round(in_tx / denom, 2)}


def consumption_view(beat_rec: dict | None) -> dict:
    """DP-6 소비 원장 관측 — required_override 가 episode_required 의 부분집합(=이미 실현분 차감)인가."""
    if not beat_rec:
        return {"active": None, "note": "비트 캡처 없음"}
    full = beat_rec.get("episode_required") or []
    ov = beat_rec.get("required_override")
    if beat_rec.get("is_finale"):
        return {"active": False, "reason": "finale=full(안전망 복원 — 차감 안 함)",
                "n_full": len(full)}
    if ov is None:
        return {"active": False, "reason": "override 미전달(첫 회차/refresh 아님)", "n_full": len(full)}
    consumed = [e for e in full if e not in ov]
    return {"active": True, "n_full": len(full), "n_override": len(ov),
            "n_consumed": len(consumed), "consumed": consumed[:6],
            "climax_override": beat_rec.get("climax_override")}


def gate_chapter(state, chapter: int) -> dict:
    """회차 1개의 결정론 게이트 — 능동 개시 / 재탕 / 자수 밴드 / protagonist_move 반영 / 소비원장."""
    chs = sorted([_c2d(c) for c in state.chapters if c.status.value == "FINALIZED"
                  if True], key=lambda c: c["chapter"])
    world = state.world.model_dump()
    _, needles = detect_protagonist(world, chs)
    # 비트 캡처를 chapters 에 병합(비트층 재탕 계산용)
    beat_rows = _load_beats()
    for c in chs:
        br = beat_rows.get(c["chapter"])
        if br:
            c["key_events"] = br.get("beat_key_events") or []
    idx = next((k for k, c in enumerate(chs) if c["chapter"] == chapter), None)
    if idx is None:
        return {"error": f"회차 {chapter} FINALIZED 아님/없음"}
    cur = chs[idx]
    # ① 능동 개시(이 회차)
    act = activity_metrics([cur], needles)["per_chapter"][0]
    # ② 재탕(선행 전부 대비)
    rt = retread_metrics(chs[: idx + 1])[idx]
    # ③ 자수
    nws = _nws(cur["text"])
    # ④/⑤ 비트 개입
    br = beat_rows.get(chapter)
    mv = move_reflection(br, cur["text"], needles)
    cons = consumption_view(br)
    return {"chapter": chapter, "nws": nws,
            "nws_band_ok": bool(3000 <= nws <= 6200),
            "active_open": {"init": act["init"], "react": act["react"],
                            "agency": act["agency_score"], "active": act["active_open"]},
            "retread": rt,
            "protagonist_move": mv, "consumption_ledger": cons,
            "drift_signals": cur.get("drift_signals") or []}


def _c2d(c) -> dict:
    """ChapterRecord → 게이트가 쓰는 얕은 dict."""
    return {"chapter": c.chapter, "text": c.text or "", "status": c.status.value,
            "hook_type": c.hook_type or "", "place": c.place or "",
            "drift_signals": list(c.drift_signals or [])}


def _load_beats() -> dict[int, dict]:
    rows = {}
    if BEATS_LOG.exists():
        for ln in BEATS_LOG.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    r = json.loads(ln)
                    rows[int(r["chapter"])] = r
                except Exception:
                    pass
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# DP-9: 틱 시트(수치+실례 프리펜드) + measure-then-cite 체크리스트 — 실험 게이트가 정독 앞에 붙인다.
#   자유 정독은 지각-하 틱(파편 리듬·과거형 단조·상투 종결)을 입증적으로 건너뛴다(DP-4 실증). 그래서 결정론
#   수치와 '상위 실례'를 먼저 보여주고, 지적은 반드시 숫자→본문 인용으로 확증하게(measure-then-cite) 강제한다.
#   엔진 판정기 아님 — 이 시트는 사람/에이전트 정독을 돕는 참고 계기(advisory)일 뿐.
# ─────────────────────────────────────────────────────────────────────────────
def _layer_raw(text: str) -> str:
    """ST-9 ⓒ: 종결(어미) 원자료 옆에 발화 층위 원자료를 병기하는 시트 한 줄.

    벽 판정 축은 어미 run 길이가 아니라 발화 층위 단일성(research-ending-monotony §④) — 종결 압축률/최장 run
    (어미 변주 축)과 대사 비중·무대사 지문 연속 run(층위 단일성 축)을 나란히 보여 정독이 두 축을 변별하게 한다.
    신규 검출기 0 — kiwi_metrics.ending_profile·style_lightness_baseline.lightness_metrics 재사용(원자료만 뽑음).
    advisory(임계·판정 아님). 로드 실패는 조용히 생략(시트 사망 금지)."""
    parts = []
    try:
        import tools.kiwi_metrics as km
        ep = km.ending_profile(text or "")
        if ep.get("n_ending"):
            parts.append(f"종결압축 {ep.get('compression')}(최장run {ep.get('max_run')}·최빈 {ep.get('top_ratio')})")
    except Exception:
        pass
    try:
        import tools.style_lightness_baseline as sl
        lm = sl.lightness_metrics(text or "")
        parts.append(f"대사문단비 {lm.get('dialogue_para_ratio')}·무대사지문연속 {lm.get('max_narration_run')}")
    except Exception:
        pass
    if not parts:
        return ""
    return ("· 층위 원자료(벽 축=층위 단일성, run 길이 아님): " + " / ".join(parts)
            + "   ← 대사·인용·입말 생각·의문이 지문 벽을 깨는지 정독으로 확증")


def _tick_sheet(text: str) -> str:
    from novelcopilot.engine.quality_gates import (
        past_tense_run, fragment_ratio, ai_tell_profile, word_tics)
    ptr = past_tense_run(text)
    fr = fragment_ratio(text)
    at = ai_tell_profile(text)
    tics = word_tics(text)
    fp = len(re.findall(r"(?:^|[^가-힣])(?:나는|내가|나를|나도|나의|내)(?![가-힣])", text or ""))
    first3 = [s.strip() for s in re.split(r"\n+|(?<=[.!?…])\s+", text or "") if s.strip()][:3]
    L = ["=" * 72,
         "## 틱 시트 — measure-then-cite (숫자를 먼저 재고, 지적은 반드시 본문 인용으로 확증하라)",
         f"· 무동사 파편문 비율: {fr['ratio']}  ({fr['n_fragment']}/{fr['n_sent']})   ← DP-9 판별축(대조 5.1%, DP-4 16.7%)"]
    if fr["examples"]:
        L.append("    상위 실례: " + " / ".join(f'“{e}”' for e in fr["examples"][:5]))
    L.append(f"· 과거형 종결 최장 run: {ptr['max_run']}   (과거형 비율 {ptr['ratio']}, {ptr['n_past']}/{ptr['n_sent']})")
    if ptr["examples"]:
        L.append("    run 실례: " + " → ".join(f'“{e}”' for e in ptr["examples"][:4]))
    L.append(f"· 종결 다양성 {at['ending_diversity']} · 문장길이CV {at['sent_len_cv']} · 쉼표/100자 {at['comma_per_100']} · 비유/1k {at['simile_per_1k']}")
    if tics:
        L.append("· 반복 편중(word_tics): " + ", ".join(f"{p}×{n}" for p, n in tics))
    L.append(f"· 1인칭 서술 마커(나는/내가/…): {fp}회   (많으면 1인칭 회차 — 종결·주어 반복은 시점의 문법적 필연이니 틱으로 오탐 말 것)")
    L.append("· 첫 3문장(도입 인상): " + " | ".join(first3))
    lyr = _layer_raw(text)   # ST-9 ⓒ: 종결(어미) 원자료 + 발화 층위 원자료 병기(벽 축은 run 길이가 아니라 층위 단일성)
    if lyr:
        L.append(lyr)
    L += ["",
          "### 정독 체크리스트 6항목 (각 항목: 숫자→본문 인용 강제 — 숫자만으로 지적 금지)",
          "1) 파편문 비율이 대조(5.1%)보다 높은가? 높다면 실제 파편문 3개를 본문에서 찾아 그대로 인용하라.",
          "2) 과거형 run 이 길게 이어지는가? 그렇다면 그 연속 구간을 본문에서 인용하고 단조로운지 판단하라.",
          "3) 종결 다양성이 낮은가? 낮다면 반복되는 종결이 쓰인 문장 2~3개를 인용해 확증하라.",
          "4) word_tics 에 어구가 떴는가? 떴다면 그 어구가 실제로 쓰인 문장을 인용하라(대사 양념/부사 편중 확인).",
          "5) 숫자가 모두 정상이어도 자유 정독으로 지각-하 틱(관용 비유·상투 종결·리듬 반복)을 직접 찾되 — 지적할 땐 반드시 본문 인용으로 뒷받침하라.",
          # DP-17→ST-9: 케이던스 정독 — 축을 'run 길이'에서 '발화 층위 단일성'으로 재정렬(research-ending-monotony §④).
          #   레퍼런스도 과거형 run 은 길다 — 벽을 깨는 건 층위 개입(대사·인용·입말 생각·의문·문서체). 같은 어미가 이어져도
          #   층위가 섞이면 벽이 아니다. 판정은 정독, 카운터는 원자료만(위 시트).
          "6) [케이던스 낭독 — 발화 층위 단일성] 본문에서 연속 10문장을 골라 소리 내어 읽어라. 볼 것은 어미 run 길이가 아니라 발화 층위가 하나뿐인가다 — 지문(사건 서술)만 계속 이어지고 대사·인용·화자의 입말 생각·짧은 의문·문서/화면/소리 인용이 그 사이에 끼어들지 않으면 층위가 단일한 벽 구간이다. 벽이면 하차 사유 FAIL 로 판정하고 그 구간 원문을 그대로 인용하라. 같은 어미가 이어져도 층위가 섞이면 벽이 아니다. 화자가 사건을 보고만 하는 카메라인지, 목소리로 겪는지 낭독으로 판별하라.",
          "=" * 72, ""]
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
# 덤프 / 게이트 로그
# ─────────────────────────────────────────────────────────────────────────────
def _dump_chapter(state, chapter: int, gate: dict):
    c = next((x for x in state.chapters if x.chapter == chapter), None)
    if c is None:
        return
    br = _load_beats().get(chapter)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = []
    out.append(_tick_sheet(c.text or ""))     # DP-9 틱 시트 프리펜드(정독 앞에 수치+실례+체크리스트)
    out.append(f"# DP-4 회차 {chapter} — {c.title}")
    out.append(f"status={c.status.value} nws={gate.get('nws')} hook={c.hook_type} place={c.place}")
    out.append("")
    out.append("## 비트(계획 레이어 — DP-2/DP-6 개입 관측)")
    if br:
        out.append(f"protagonist_move: {br.get('beat_protagonist_move')!r}")
        out.append(f"key_events: {json.dumps(br.get('beat_key_events'), ensure_ascii=False)}")
        out.append(f"episode_required(full {len(br.get('episode_required') or [])}): "
                   f"{json.dumps(br.get('episode_required'), ensure_ascii=False)}")
        out.append(f"required_override(소비원장 차감결과): "
                   f"{json.dumps(br.get('required_override'), ensure_ascii=False)}")
        out.append(f"climax_override: {br.get('climax_override')!r}  is_finale={br.get('is_finale')}")
    else:
        out.append("(비트 캡처 없음)")
    out.append("")
    out.append("## 결정론 게이트")
    out.append(json.dumps(gate, ensure_ascii=False, indent=2))
    out.append("")
    out.append("## 본문")
    out.append(c.text or "(빈 본문)")
    (REPORTS / f"dp4_ch{chapter}.txt").write_text("\n".join(out), encoding="utf-8")


def _log_gate(gate: dict):
    data = {}
    if GATE_LOG.exists():
        try:
            data = json.loads(GATE_LOG.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[str(gate.get("chapter"))] = gate
    GATE_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_gate(gate: dict):
    print(json.dumps(gate, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# 명령
# ─────────────────────────────────────────────────────────────────────────────
def _svc():
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    return CopilotService(s, repo), repo


def cmd_create() -> int:
    from novelcopilot.domain.project import ProjectSeed
    svc, repo = _svc()
    seed = ProjectSeed(**SEED_A)
    print(f"[create] 시드A target={seed.target_chapters} '{seed.title}' — worldgen 시작…", flush=True)
    t0 = time.monotonic()
    state, _ = svc.create_project(seed.model_copy(deep=True))
    # '[실험 DP-4]' 태그 — DP-1 과 동일 포맷으로 웹 가시화(제목 앞 태그)
    base = state.world.title
    if DP4_TAG not in base:
        state.world.title = f"{DP4_TAG} {SEED_A['title']}"
        repo.save(state)
    sp = state.world.spine
    n_arcs = len(sp.arcs) if sp else 0
    n_eps = sum(len(a.episodes) for a in sp.arcs) if sp else 0
    print(f"[create-done] pid={state.id} title='{state.world.title}' "
          f"arcs={n_arcs} episodes={n_eps} ({round(time.monotonic()-t0,1)}s)", flush=True)
    print(f"[usage] {json.dumps(state.usage_total)}", flush=True)
    return 0


def _run_gate_and_dump(repo, pid: str, chapter: int):
    state = repo.get(pid)
    _persist_captured(chapter)
    gate = gate_chapter(state, chapter)
    _log_gate(gate)
    _dump_chapter(state, chapter, gate)
    return state, gate


def cmd_gen(pid: str) -> int:
    _install_beat_capture()
    svc, repo = _svc()
    st = repo.get(pid)
    next_ch = st.current_chapter + 1
    print(f"[gen] {pid} 회차 {next_ch} 생성 시작…", flush=True)
    t0 = time.monotonic()
    res = svc.generate_next_chapter(pid)
    if res.get("completed"):
        print(f"[completed] {res.get('reason')} current={res.get('current_chapter')}", flush=True)
        return 0
    rec = res.get("record")
    status = getattr(getattr(rec, "status", ""), "value", "")
    st = repo.get(pid)
    if st.current_chapter != len(st.chapters):
        print(f"[ABORT] 커서 불일치 current={st.current_chapter} != len={len(st.chapters)}", flush=True)
        return 1
    state, gate = _run_gate_and_dump(repo, pid, st.current_chapter)
    print(f"[gen-done] 회차 {st.current_chapter} status={status} "
          f"nws={gate.get('nws')} ({round(time.monotonic()-t0,1)}s)", flush=True)
    _print_gate(gate)
    print(f"[usage_total] {json.dumps(state.usage_total)}", flush=True)
    print(f"[dump] {REPORTS / f'dp4_ch{st.current_chapter}.txt'}", flush=True)
    return 0


def cmd_regen(pid: str, fix: str) -> int:
    _install_beat_capture()
    svc, repo = _svc()
    st = repo.get(pid)
    tgt = max((c.chapter for c in st.chapters), default=0)
    print(f"[regen] {pid} 회차 {tgt} 재생성(fix 지시 {len(fix)}자)…", flush=True)
    t0 = time.monotonic()
    out = svc.regenerate_last_chapter(pid, fix_instruction=fix)
    if out is None:
        print("[regen] 거부(진행 중 잡/회차 없음)", flush=True)
        return 1
    job, _created = out
    while job.status == "running":
        time.sleep(2.0)
    if job.status == "failed":
        print(f"[regen] 실패: {job.error}", flush=True)
        return 1
    st = repo.get(pid)
    if st.current_chapter != len(st.chapters):
        print(f"[ABORT] 커서 불일치 current={st.current_chapter} != len={len(st.chapters)}", flush=True)
        return 1
    state, gate = _run_gate_and_dump(repo, pid, tgt)
    print(f"[regen-done] 회차 {tgt} nws={gate.get('nws')} ({round(time.monotonic()-t0,1)}s)", flush=True)
    _print_gate(gate)
    print(f"[usage_total] {json.dumps(state.usage_total)}", flush=True)
    print(f"[dump] {REPORTS / f'dp4_ch{tgt}.txt'}", flush=True)
    return 0


def cmd_gate(pid: str, chapter: int | None) -> int:
    _, repo = _svc()
    state = repo.get(pid)
    ch = chapter or max((c.chapter for c in state.chapters), default=0)
    gate = gate_chapter(state, ch)
    _print_gate(gate)
    return 0


def cmd_analyze(pid: str, dp1_pid: str | None) -> int:
    """DP-4 종합 결정론 + DP-1 시드A 대비 재탕/능동성 대조(LLM 0콜)."""
    from tools.dp1_dopamine_baseline import diagnose
    _, repo = _svc()
    dp4 = diagnose(pid)   # dp1 진단기 재사용(능동개시율·hook·정산·가벼움·자수 동일 기준)
    state = repo.get(pid)
    chs = sorted([_c2d(c) for c in state.chapters if c.status.value == "FINALIZED"],
                 key=lambda c: c["chapter"])
    beats = _load_beats()
    for c in chs:
        br = beats.get(c["chapter"])
        if br:
            c["key_events"] = br.get("beat_key_events") or []
    rt4 = retread_metrics(chs)
    # protagonist_move / 소비원장 요약
    mv_present = sum(1 for c in chs if (beats.get(c["chapter"]) or {}).get("beat_protagonist_move"))
    cons_active = sum(1 for c in chs
                      if consumption_view(beats.get(c["chapter"])).get("active"))
    cons_consumed = sum(consumption_view(beats.get(c["chapter"])).get("n_consumed", 0) or 0
                        for c in chs)
    out = {"dp4_pid": pid, "dp4_diagnosis": dp4,
           "dp4_retread": rt4,
           "dp4_intervention": {
               "chapters": len(chs),
               "protagonist_move_present": mv_present,
               "consumption_ledger_active_chapters": cons_active,
               "required_events_consumed_total": cons_consumed}}
    # DP-1 시드A 재탕(텍스트 도입 어간 — 동일 지표로 대조; DP-1 는 비트 key_events 미영속이라 텍스트만)
    if dp1_pid:
        try:
            d1 = json.loads((PROJ / f"{dp1_pid}.json").read_text(encoding="utf-8"))
            chs1 = sorted([{"chapter": c["chapter"], "text": c.get("text") or ""}
                           for c in d1["chapters"] if c.get("status") == "FINALIZED"],
                          key=lambda c: c["chapter"])
            out["dp1_pid"] = dp1_pid
            out["dp1_retread"] = retread_metrics(chs1)
            out["dp1_diagnosis"] = diagnose(dp1_pid)
        except Exception as e:
            out["dp1_error"] = str(e)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "dp4_analyze.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    # 콘솔 요약
    print(f"=== DP-4 {pid} '{dp4['title']}' 회차={dp4['n_finalized']} ===")
    a = dp4["activity"]
    print(f"① 능동개시율={a['ratio']} (agency_mean={a['agency_score_mean']}) 능동회차={a['active_open_chapters']}")
    print("  per-ch init/react: " + " ".join(f"{x['chapter']}:{x['init']}/{x['react']}"
                                              for x in a["per_chapter"]))
    print(f"② 재탕(coef~with): " + " ".join(f"{r['chapter']}:{r['opening_coef']}"
                                 f"(~{r['opening_with']}){'*' if r['retread_flag'] else ''}"
                                 for r in rt4))
    print(f"   비트층 keyev-overlap(직전): " + " ".join(f"{r['chapter']}:{r['keyevents_overlap_prev']}"
                                                       for r in rt4))
    h = dp4["hooks"]
    print(f"③ hook top={h['top_hook']}({h['top_ratio']}) distinct={h['distinct']} maxrun={h['max_same_run']} seq={h['sequence']}")
    lg = dp4["ledger"]
    print(f"④ 정산 paid_ch={lg['prose_paid_chapters']} intervals={lg['settlement_intervals']} "
          f"tail_gap={lg['tail_unsettled_gap']} open_end={lg['open_balance_end']}")
    print(f"⑤ 자수(nws) mean={dp4['length']['nws']['mean']} per={dp4['length']['nws_per_chapter']}")
    print(f"[개입] protagonist_move 뜬 회차={mv_present}/{len(chs)}  "
          f"소비원장 활성 회차={cons_active}  누적 차감 required={cons_consumed}")
    if dp1_pid and "dp1_retread" in out:
        print(f"--- DP-1 시드A({dp1_pid}) 재탕(coef~with): " +
              " ".join(f"{r['chapter']}:{r['opening_coef']}(~{r['opening_with']})"
                       f"{'*' if r['retread_flag'] else ''}" for r in out["dp1_retread"]))
        a1 = out["dp1_diagnosis"]["activity"]
        print(f"    DP-1 능동개시율={a1['ratio']} 능동회차={a1['active_open_chapters']}")
    print(f"[report] {REPORTS / 'dp4_analyze.json'}")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "create":
        return cmd_create()
    if cmd == "gen":
        return cmd_gen(argv[1])
    if cmd == "regen":
        return cmd_regen(argv[1], argv[2])
    if cmd == "gate":
        return cmd_gate(argv[1], int(argv[2]) if len(argv) > 2 else None)
    if cmd == "analyze":
        return cmd_analyze(argv[1], argv[2] if len(argv) > 2 else None)
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
