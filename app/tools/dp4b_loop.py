# -*- coding: utf-8 -*-
"""DP-4b 매화 검증 루프 — DP-7~12 수리 스택(99e7e28·34b529d·5326bac·fef34f4) 검증.

DP-4(1화 중단·3인칭)와 동일 시드로 재개하되 **1인칭**(StyleSpec.pov='first')으로 재판정.
DP-4 도구(tools/dp4_loop.py)의 순수 지표 함수(재탕·능동개시·소비원장·틱 시트)를 그대로 재사용하고,
DP-4b 전용으로 (1) 생성 직후 pov='first' 패치 (2) 1화 프롬프트 gen_context 게이트(1인칭 지시·발단 변형
규칙 실주입 확인) (3) reader_feedback(DP-10 신 스키마) 덤프 를 추가한다. 자체 리포트 파일(dp4b_*)만 쓴다
(DP-4 원자료 무손상).

명령(app/ 에서):
  create                          : 시드A target 24 신규 작품 생성 → pov='first' 패치 → '[실험 DP-4b]' 태그 → pid
  gen   <pid>                     : 다음 회차 생성 + (1화면 genctx 캡처) + 결정론 게이트 + reader_feedback + 덤프
  regen <pid> "<fix instruction>" : 마지막 회차 fix 재생성 + 게이트 + 덤프
  gate  <pid> [N]                 : (LLM 0콜) 회차 N 결정론 게이트 재계산
  fix_spans <pid> <N> [runThr] [mode] : ST-11 스팬 국소 재작성(전체 regen 전 먼저·무강제, mode 기본 v2=문단 리팩토링) → st11_span_rewrite.md(원본 무수정)
  analyze <pid> [dp1_pid]         : (LLM 0콜) DP-4b 종합 + DP-1 시드A 대비 재탕/능동성 대조

산출(tools/reports/): dp4b_ch<N>.txt · dp4b_beats.jsonl · dp4b_gate.json · dp4b_genctx_ch1.txt · dp4b_analyze.json
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
BEATS_LOG = REPORTS / "dp4b_beats.jsonl"
GATE_LOG = REPORTS / "dp4b_gate.json"

# ── DP-4 와 동일 시드A(DP-1 4.0점·잔존 27 실패 케이스) — target 만 24 ──
SEED_A = {
    "title": "1위의 재림",
    "genre": "현대판타지(회귀·먼치킨)",
    "tone": "통쾌한 사이다, 시원한 전개, 주인공이 판을 주도",
    "premise": ("대륙 서열 1위 헌터가 최약체이던 20살로 회귀한다. 미래의 지식과 1위의 감각은 그대로 — "
                "이번 생은 당하기 전에 먼저 움직인다. 자신을 버렸던 길드, 아직 발견되지 않은 유물, "
                "곧 터질 게이트 사태의 순서를 전부 알고 있다."),
    "protagonist_hint": ("회귀한 전 서열 1위, 미래 지식 보유, 선수(先手)를 치는 성격 — 당하지 않고 먼저 판을 설계"),
    "target_chapters": 24,
}
DP4B_TAG = "[실험 DP-4b]"

# 순수 지표 함수 재사용(DP-4 도구·DP-1 baseline) — 측정축 동일(대조 유효성 보존)
from tools.dp1_dopamine_baseline import (  # noqa: E402
    detect_protagonist, activity_metrics, diagnose, other_needles)
from tools.dp4_loop import (  # noqa: E402
    retread_metrics, move_reflection, consumption_view, _tick_sheet, _nws, _c2d,
    _install_beat_capture)
import tools.dp4_loop as _dp4  # noqa: E402   (_CAPTURED 접근)


# ─────────────────────────────────────────────────────────────────────────────
# 비트 캡처 영속 — DP-4 의 _install_beat_capture 가 채운 _dp4._CAPTURED 를 dp4b 파일로 저장
# ─────────────────────────────────────────────────────────────────────────────
def _persist_captured(chapter: int):
    rec = _dp4._CAPTURED.get(chapter)
    if not rec:
        return None
    REPORTS.mkdir(parents=True, exist_ok=True)
    rows = _load_beats()
    rows[chapter] = rec
    BEATS_LOG.write_text(
        "\n".join(json.dumps(rows[k], ensure_ascii=False) for k in sorted(rows)) + "\n",
        encoding="utf-8")
    return rec


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
# 결정론 게이트(dp4_loop.gate_chapter 와 동형 — dp4b beats 를 읽도록 clone)
# ─────────────────────────────────────────────────────────────────────────────
def gate_chapter(state, chapter: int) -> dict:
    chs = sorted([_c2d(c) for c in state.chapters if c.status.value == "FINALIZED"],
                 key=lambda c: c["chapter"])
    world = state.world.model_dump()
    prot_name, needles = detect_protagonist(world, chs)
    # DP-14: pov='first' 이면 1인칭 agency 축(주어생략 복구) — needle 축의 구조적 0.0(DP-4b) 교정
    pov = ((world.get("style") or {}).get("pov") or "")
    others = other_needles(world, prot_name) if pov == "first" else []
    beat_rows = _load_beats()
    for c in chs:
        br = beat_rows.get(c["chapter"])
        if br:
            c["key_events"] = br.get("beat_key_events") or []
    idx = next((k for k, c in enumerate(chs) if c["chapter"] == chapter), None)
    if idx is None:
        return {"error": f"회차 {chapter} FINALIZED 아님/없음"}
    cur = chs[idx]
    act = activity_metrics([cur], needles, pov=pov, other_needles=others)["per_chapter"][0]
    rt = retread_metrics(chs[: idx + 1])[idx]
    nws = _nws(cur["text"])
    br = beat_rows.get(chapter)
    mv = move_reflection(br, cur["text"], needles)
    cons = consumption_view(br)
    return {"chapter": chapter, "nws": nws,
            "nws_band_ok": bool(3000 <= nws <= 6200),
            "active_open": {"init": act["init"], "react": act["react"], "inner": act["inner"],
                            "agency": act["agency_score"], "active": act["active_open"]},
            "retread": rt,
            "protagonist_move": mv, "consumption_ledger": cons,
            "drift_signals": cur.get("drift_signals") or [],
            # ST-10 §2ⓑ: kiwi 형태소 종결 축 additive 병기(기존 정규식 축 전부 보존 — 캘리브레이션 연속성).
            #   ending_profile=문말 EP+EF 열 압축률·run·템플릿률, da_streak=과거 '다' run 정밀판. 원자료·advisory.
            #   Kiwi 미설치 시 정규식 강등판(backend='regex')으로 자동 폴백(러너 사망 금지). 무강제(판정 아님).
            "kiwi": _kiwi_axes(cur["text"])}


def _kiwi_axes(text: str) -> dict:
    """ST-10 kiwi 종결 축(additive) — ending_profile + da_streak_kiwi. 로드 실패 시 오류 흡수(계측 사망 금지).

    ST-9 ⓒ: 종결(어미) 원자료 옆에 **발화 층위 원자료**를 병기한다 — 벽 판정 축은 run 길이가 아니라 층위 단일성이라
      digest 가 두 축을 나란히 보여줘야 정독이 올바른 축을 본다. layer 는 신규 검출기 0 — 기존
      style_lightness_baseline.lightness_metrics(대사 비중·무대사 지문 연속 run)를 재사용해 원자료만 뽑는다.
      값은 advisory(판정·임계 아님). 로드 실패 시 layer 만 생략(종결 축은 유지)."""
    try:
        import tools.kiwi_metrics as km
        out = {"ending_profile": km.ending_profile(text or ""),
               "da_streak": km.da_streak_kiwi(text or "")}
    except Exception as e:   # kiwi_metrics 자체 import/실행 실패도 계측을 죽이지 않는다(무강제·advisory)
        return {"error": f"{type(e).__name__}: {e}"}
    try:   # 발화 층위 원자료(기존 원자료 재사용 — 신규 검출기 0). 실패해도 종결 축은 죽이지 않는다.
        import tools.style_lightness_baseline as sl
        lm = sl.lightness_metrics(text or "")
        out["layer"] = {"dialogue_para_ratio": lm.get("dialogue_para_ratio"),
                        "dialogue_char_ratio": lm.get("dialogue_char_ratio"),
                        "max_narration_run": lm.get("max_narration_run")}
    except Exception as e:
        out["layer"] = {"error": f"{type(e).__name__}: {e}"}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# gen_context 캡처(1화) — AnthropicProvider.chat 클래스 래핑으로 실제 집필 콜의 system+user 포착.
#   1인칭 지시·발단 변형 규칙(rule⑤ 교체)·1화 grounding out_instr 가 실제 프롬프트에 들어갔는지 게이트.
# ─────────────────────────────────────────────────────────────────────────────
_PROMPTS: list[dict] = []
_STYLE_MARK = "[웹소설 문체 규칙"


def _install_prompt_capture():
    from novelcopilot.llm.anthropic_provider import AnthropicProvider
    if getattr(AnthropicProvider, "_dp4b_wrapped", False):
        return
    orig = AnthropicProvider.chat

    def wrapped(self, messages, *, temperature=0.7, max_tokens=2200, json_mode=False):
        try:
            sysm = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            usrm = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            if _STYLE_MARK in sysm:      # 집필(_draft) 콜만 — plan_scenes/추출 등 제외
                _PROMPTS.append({"system": sysm, "user": usrm})
        except Exception:
            pass
        return orig(self, messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode)

    AnthropicProvider.chat = wrapped
    AnthropicProvider._dp4b_wrapped = True


def _dump_genctx(chapter: int):
    """포착한 1화 집필 프롬프트로 gen_context 게이트 판정 — 마커 존재 체크 + 원문 저장."""
    if not _PROMPTS:
        return None
    p = _PROMPTS[0]                       # 이 회차 첫 집필 콜
    sysm, usrm = p["system"], p["user"]
    checks = {
        # 1인칭(DP-8): pov='first' 파생 지시가 system(style_block)에 실주입됐는가
        "pov_first_directive": "[서술 시점 — 1인칭 주인공]" in sysm,
        "pov_narration_marker": "'나는 ~했다'" in sysm,
        # 발단 변형(DP-7①): rule⑤ 가 grounding→전환 결로 교체됐는가(원 in-medias-res 시그니처 부재 + 발단문 존재)
        "opening_rule_swapped": ("이 회차는 작품의 첫머리(발단)다" in sysm),
        "in_medias_res_removed": ("사건의 한복판에서 열고" not in sysm),
        # 발단 out_instr 관통(DP-7②): 1화 grounding hook 이 user 집필 지시에 관통됐는가
        "ch1_grounding_outinstr": ("이 회차는 작품의 첫머리다" in usrm),
    }
    L = ["## DP-4b gen_context 게이트 — 1화 프롬프트 실주입 확인", ""]
    for k, v in checks.items():
        L.append(f"[{'PASS' if v else 'FAIL'}] {k}")
    L += ["", "=" * 72, "## 포착된 system(집필 문체 블록 — style_block_opening + pov)", sysm,
          "", "=" * 72, "## 포착된 user(assemble + hook + out_instr) 앞 4000자", usrm[:4000]]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"dp4b_genctx_ch{chapter}.txt").write_text("\n".join(L), encoding="utf-8")
    return checks


# ─────────────────────────────────────────────────────────────────────────────
# 덤프 / 로그
# ─────────────────────────────────────────────────────────────────────────────
def _reader_feedback(state, chapter: int) -> dict | None:
    c = next((x for x in state.chapters if x.chapter == chapter), None)
    return getattr(c, "reader_feedback", None) if c else None


def _dump_chapter(state, chapter: int, gate: dict, genctx: dict | None = None):
    c = next((x for x in state.chapters if x.chapter == chapter), None)
    if c is None:
        return
    br = _load_beats().get(chapter)
    rf = getattr(c, "reader_feedback", None)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = [_tick_sheet(c.text or "")]
    out.append(f"# DP-4b 회차 {chapter} — {c.title}")
    out.append(f"status={c.status.value} nws={gate.get('nws')} hook={c.hook_type} place={c.place} pov={state.world.style.pov}")
    if genctx is not None:
        out.append(f"gen_context(1화): {json.dumps(genctx, ensure_ascii=False)}")
    out.append("")
    out.append("## 비트(계획 레이어 — DP-2/DP-6/DP-11 개입 관측)")
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
    out.append("## reader_feedback (DP-10 신 스키마 — 시뮬 독자, 참고)")
    out.append(json.dumps(rf, ensure_ascii=False, indent=2) if rf else "(없음)")
    out.append("")
    out.append("## 결정론 게이트")
    out.append(json.dumps(gate, ensure_ascii=False, indent=2))
    out.append("")
    out.append("## 본문")
    out.append(c.text or "(빈 본문)")
    (REPORTS / f"dp4b_ch{chapter}.txt").write_text("\n".join(out), encoding="utf-8")


def _log_gate(gate: dict, genctx: dict | None = None):
    data = {}
    if GATE_LOG.exists():
        try:
            data = json.loads(GATE_LOG.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    row = dict(gate)
    if genctx is not None:
        row["gen_context"] = genctx
    data[str(gate.get("chapter"))] = row
    GATE_LOG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_gate(gate: dict):
    print(json.dumps(gate, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# 서비스
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
    # DP-4b: 생성 직후 1인칭 패치(seed→pov 자동 배선 미구현 — 신규 자기 작품이라 허용) + 태그
    state.world.style.pov = "first"
    base = state.world.title
    if DP4B_TAG not in base:
        state.world.title = f"{DP4B_TAG} {SEED_A['title']}"
    repo.save(state)
    # 검증 재로드
    st2 = repo.get(state.id)
    sp = st2.world.spine
    n_arcs = len(sp.arcs) if sp else 0
    n_eps = sum(len(a.episodes) for a in sp.arcs) if sp else 0
    print(f"[create-done] pid={st2.id} title='{st2.world.title}' pov={st2.world.style.pov} "
          f"arcs={n_arcs} episodes={n_eps} ({round(time.monotonic()-t0,1)}s)", flush=True)
    print(f"[usage] {json.dumps(st2.usage_total)}", flush=True)
    return 0


def _run_gate_and_dump(repo, pid: str, chapter: int, genctx: dict | None = None):
    state = repo.get(pid)
    _persist_captured(chapter)
    gate = gate_chapter(state, chapter)
    _log_gate(gate, genctx)
    _dump_chapter(state, chapter, gate, genctx)
    return state, gate


def cmd_gen(pid: str) -> int:
    _install_beat_capture()
    svc, repo = _svc()
    st = repo.get(pid)
    next_ch = st.current_chapter + 1
    if next_ch == 1:
        _PROMPTS.clear()
        _install_prompt_capture()
    print(f"[gen] {pid} 회차 {next_ch} 생성 시작…(pov={st.world.style.pov})", flush=True)
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
    genctx = _dump_genctx(st.current_chapter) if st.current_chapter == 1 else None
    state, gate = _run_gate_and_dump(repo, pid, st.current_chapter, genctx)
    print(f"[gen-done] 회차 {st.current_chapter} status={status} "
          f"nws={gate.get('nws')} ({round(time.monotonic()-t0,1)}s)", flush=True)
    if genctx is not None:
        print(f"[gen_context] {json.dumps(genctx, ensure_ascii=False)}", flush=True)
    _print_gate(gate)
    rf = _reader_feedback(state, st.current_chapter)
    print(f"[reader_feedback] {json.dumps(rf, ensure_ascii=False)}", flush=True)
    print(f"[usage_total] {json.dumps(state.usage_total)}", flush=True)
    print(f"[dump] {REPORTS / f'dp4b_ch{st.current_chapter}.txt'}", flush=True)
    return 0


def cmd_regen(pid: str, fix: str) -> int:
    _install_beat_capture()
    svc, repo = _svc()
    st = repo.get(pid)
    tgt = max((c.chapter for c in st.chapters), default=0)
    if tgt == 1:
        _PROMPTS.clear()
        _install_prompt_capture()
    print(f"[regen] {pid} 회차 {tgt} 재생성(fix {len(fix)}자)…", flush=True)
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
    genctx = _dump_genctx(tgt) if tgt == 1 else None
    state, gate = _run_gate_and_dump(repo, pid, tgt, genctx)
    print(f"[regen-done] 회차 {tgt} nws={gate.get('nws')} ({round(time.monotonic()-t0,1)}s)", flush=True)
    if genctx is not None:
        print(f"[gen_context] {json.dumps(genctx, ensure_ascii=False)}", flush=True)
    _print_gate(gate)
    rf = _reader_feedback(state, tgt)
    print(f"[reader_feedback] {json.dumps(rf, ensure_ascii=False)}", flush=True)
    print(f"[usage_total] {json.dumps(state.usage_total)}", flush=True)
    print(f"[dump] {REPORTS / f'dp4b_ch{tgt}.txt'}", flush=True)
    return 0


def cmd_gate(pid: str, chapter: int | None) -> int:
    _, repo = _svc()
    state = repo.get(pid)
    ch = chapter or max((c.chapter for c in state.chapters), default=0)
    gate = gate_chapter(state, ch)
    _print_gate(gate)
    return 0


def cmd_fix_spans(pid: str, chapter: int, run_threshold: int, mode: str = "v2") -> int:
    """ST-11 fix 단계 — 회차 전체 재생성 *전에* 스팬 수리 먼저 시도(무강제·실험 도구).

    검출된 '~다 벽'·파편 클러스터 스팬만 국소 재작성(사실 불변 revise 섀시). 회차 원본은 무수정 —
    전/후는 st11_span_rewrite.md 리포트로만 산출한다(라이브 반영은 사용자 판단). run 미해소면 st11 이
    스팬당 1회 재시도한다. 여기서 규모가 크면 회차 전체 regen 으로 폴백하는 건 사용자 결정 몫.
    mode='v2'(기본, ST-11b): 문단 리팩토링(문장 경계 재구성). mode='v1': 스팬 재작성(문장 경계 유지)."""
    import tools.st11_span_rewrite as st11
    svc, repo = _svc()
    state = repo.get(pid)
    ch = next((c for c in state.chapters if c.chapter == chapter), None)
    if ch is None or not (ch.text or "").strip():
        print(f"[fix-spans] 회차 {chapter} 없음/빈 본문", flush=True)
        return 1
    text = ch.text or ""
    sess = svc.sessions.get_or_create(state)
    spans = st11.extract_rhythm_spans(text, run_threshold=run_threshold)
    ptr = past_tense_run_gate(text)
    print(f"[fix-spans] {pid} ch{chapter} past_run_max={ptr['max_run']} 검출 스팬={len(spans)} "
          f"(run_threshold={run_threshold}, mode={mode}) — 국소 재작성 우선 시도", flush=True)
    results = []
    for k, sp in enumerate(spans):
        r = st11.rewrite_span_via_chassis(sess.bundle.generator, sess.bundle.ontology,
                                          sess.bundle.checker, chapter, text, sp,
                                          run_threshold=run_threshold, service=svc, mode=mode)
        results.append((sp, r))
        rb, ra = r["redetect_before"], r["redetect_after"]
        print(f"  [{k}] {sp['kind']} run {rb['ending_run_max']}→{ra['ending_run_max']} "
              f"σ {rb['len_stdev']}→{ra['len_stdev']} changed={r['changed']} retried={r['retried']}", flush=True)
    st11._write_report(pid, chapter, text, results, run_threshold, mode=mode)
    print(f"[fix-spans] 리포트: {REPORTS / 'st11_span_rewrite.md'} (회차 원본 무수정)", flush=True)
    return 0


def past_tense_run_gate(text: str) -> dict:
    from novelcopilot.engine.quality_gates import past_tense_run
    return past_tense_run(text)


def cmd_analyze(pid: str, dp1_pid: str | None) -> int:
    _, repo = _svc()
    dp4b = diagnose(pid)
    state = repo.get(pid)
    chs = sorted([_c2d(c) for c in state.chapters if c.status.value == "FINALIZED"],
                 key=lambda c: c["chapter"])
    beats = _load_beats()
    for c in chs:
        br = beats.get(c["chapter"])
        if br:
            c["key_events"] = br.get("beat_key_events") or []
    rt = retread_metrics(chs)
    mv_present = sum(1 for c in chs if (beats.get(c["chapter"]) or {}).get("beat_protagonist_move"))
    cons_active = sum(1 for c in chs if consumption_view(beats.get(c["chapter"])).get("active"))
    cons_consumed = sum(consumption_view(beats.get(c["chapter"])).get("n_consumed", 0) or 0 for c in chs)
    # reader_feedback 잔존 곡선(DP-10)
    ret_curve = []
    for c in state.chapters:
        rf = getattr(c, "reader_feedback", None) or {}
        ret_curve.append({"chapter": c.chapter, "retention_est": rf.get("retention_est"),
                          "drop": rf.get("drop"), "kill_trigger": (rf.get("kill_trigger") or "")[:80]})
    out = {"dp4b_pid": pid, "pov": state.world.style.pov, "dp4b_diagnosis": dp4b,
           "dp4b_retread": rt, "dp4b_reader_curve": ret_curve,
           "dp4b_intervention": {"chapters": len(chs), "protagonist_move_present": mv_present,
                                 "consumption_ledger_active_chapters": cons_active,
                                 "required_events_consumed_total": cons_consumed}}
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
    (REPORTS / "dp4b_analyze.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    print(f"=== DP-4b {pid} '{dp4b['title']}' pov={state.world.style.pov} 회차={dp4b['n_finalized']} ===")
    a = dp4b["activity"]
    print(f"① 능동개시율={a['ratio']} (agency_mean={a['agency_score_mean']}) 능동회차={a['active_open_chapters']}")
    print("  per-ch init/react/inner: " + " ".join(f"{x['chapter']}:{x['init']}/{x['react']}/{x.get('inner',0)}"
                                                    for x in a["per_chapter"]))
    print("② 재탕(coef~with): " + " ".join(f"{r['chapter']}:{r['opening_coef']}(~{r['opening_with']})"
                                          f"{'*' if r['retread_flag'] else ''}" for r in rt))
    print("   비트층 keyev-overlap(직전): " + " ".join(f"{r['chapter']}:{r['keyevents_overlap_prev']}" for r in rt))
    print("④ reader 잔존곡선: " + " ".join(f"{r['chapter']}:{r['retention_est']}" for r in ret_curve))
    print(f"[개입] protagonist_move 뜬 회차={mv_present}/{len(chs)}  소비원장 활성={cons_active}  누적차감={cons_consumed}")
    if dp1_pid and "dp1_retread" in out:
        print("--- DP-1 시드A 재탕: " + " ".join(f"{r['chapter']}:{r['opening_coef']}{'*' if r['retread_flag'] else ''}"
                                               for r in out["dp1_retread"]))
        a1 = out["dp1_diagnosis"]["activity"]
        print(f"    DP-1 능동개시율={a1['ratio']} 능동회차={a1['active_open_chapters']}")
    print(f"[report] {REPORTS / 'dp4b_analyze.json'}")
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
    if cmd == "fix_spans":   # ST-11: 스팬 국소 재작성 fix(회차 전체 regen 전에 먼저 — 무강제·리포트만)
        # argv: fix_spans <pid> <N> [runThr] [mode]  — mode 기본 v2(ST-11b 문단 리팩토링)
        rt = int(argv[3]) if len(argv) > 3 else 6
        md = argv[4] if len(argv) > 4 else "v2"
        return cmd_fix_spans(argv[1], int(argv[2]), rt, md)
    if cmd == "analyze":
        return cmd_analyze(argv[1], argv[2] if len(argv) > 2 else None)
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
