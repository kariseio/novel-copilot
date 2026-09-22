# -*- coding: utf-8 -*-
"""STV — 신문체 스택(ST-3 reflow + ST-2 규칙·persona) 검증 생성 + 본문 모델 재평가.

무엇을 검증하나:
  ① ST-2(DEFAULT_STYLE_RULES 재작성)·ST-3(발행 경계 문단 조판)이 실제로 신규 작품에 적용되는가
     (StyleSpec.rules 스냅샷 검사 — worldgen LLM 이 style.rules 를 자기값으로 덮어쓰면 ST-2 는 사장).
  ② 신스택 산출이 LR-1 baseline 대비 '가벼움' 밴드(design-style-lightness §4) 충족 개수를 명확히 개선하나.
  ③ 본문 모델 arm(claude-opus-4-6 vs openai gpt-5.5)의 밴드·틱 우열 → 본문 모델 유지/교체 권고.

통제(조건 동일):
  · 세계·bible·아크·비트·메뉴 = 전부 planning/worldgen 라우팅(anthropic:claude-opus-4-8) — **양 arm 공유**.
    두 arm 은 오직 '본문(프로즈)+연속성폴리시+요약'을 쓰는 세션 프로바이더(gen_model)만 다르다.
  · 세계 1회 생성 후 arm 별 deep-copy(주입 외 조건 동일 — b32e 전례).
  · 생성 시 paragraph_reflow=OFF 로 저장 → 저장본=pre-reflow. 측정 단계에서 reflow_paragraphs 를
    같은 파이프 위치에 순수 적용해 post-reflow 를 산출(조판 기여 vs 규칙 기여 분해). reflow 는 순수함수라
    저장 pre 에 재적용한 post 는 shipped(reflow ON) 저장본과 바이트 동일.

1차 판정(결정론·주판정): design-style-lightness §4 6밴드 충족 개수 vs LR-1(0/6).
2차(참고): arm 간 쌍대 — 생성 모델이 다르므로 제3계열(gemini) 심사, 참고 지표로만.

운영 관찰: gpt-5.5 는 udar/타임아웃 이력 — attempt 별 벽시계·예외(타임아웃/재시도)·escalated 기록.
  1차 모델이 반복 실패하면 gpt-5.2-chat-latest 로 폴백하고 fallback_at 로 명기.

영속·resume(b32e 교훈): 회차는 FINALIZED 단위로 temp repo 영속, arm JSON 존재 시 스킵.

실행(app/ 에서):
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py gen school
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py gen modfan
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py resume school <temp repo dir>
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py measure
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py judge

STV-2(ST-6 배선 후 최종 재검증 — 단일 arm c46=현행 gen_model, stv2_ 산출, stv_(dead) 는 비교 baseline 로 보존):
  · 생성 전 하드 게이트: 신규 작품 style.rules==DEFAULT_STYLE_RULES & persona==기본. 불일치=ST-6 미도달→집필 중단.
  · measure2: LR-1(0/6) vs STV c46(dead rules) vs STV-2 c46(live rules) 3원 밴드 비교 = ST-2 규칙 순수 델타(조판 동일).
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py gen2 school
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py gen2 modfan
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py resume2 school <temp repo dir>
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_style_stack.py measure2
"""
from __future__ import annotations
import sys, json, time, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # app/ → novelcopilot

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.domain.types import ChapterStatus
from novelcopilot.domain.world import DEFAULT_STYLE_RULES, StyleSpec
from novelcopilot.engine.textfmt import reflow_paragraphs
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService

from tools.style_lightness_baseline import lightness_metrics, summarize, _SUMMARY_KEYS

OUT = Path(__file__).resolve().parent / "reports"
N_CH = 4
JUDGE_MODEL = ("gemini", "gemini-2.5-flash")   # 제3계열(gen 은 claude/openai) — 참고 심사
JUDGE_PAIRS = 3
JUDGE_CAP = 20000

# 본문 모델 arm — 세계/설계는 공유(anthropic:claude-opus-4-8), 프로즈만 이 gen_model 로.
ARMS = {
    "c46": ("anthropic", "claude-opus-4-6"),    # arm1 = 현행 gen_model
    "g55": ("openai", "gpt-5.5"),               # arm2 = openai gpt-5.5(과거 본문 후보 — 타임아웃 이력)
}
G55_FALLBACK = ("openai", "gpt-5.2-chat-latest")   # g55 반복 실패 시 폴백(명기)
FALLBACK_TRIGGER = 3    # 한 arm 이 이 횟수만큼 '본문 생성 실패(예외/빈record)'면 폴백 스왑

SEEDS = {
    # 잔잔 학원물 — LR-1 실측 '대사 최소' 장르(dialogue_char 0.052). 대사·행동 주도 스택엔 최난 케이스.
    "school": ProjectSeed(
        title="", genre="학원물(잔잔한 일상)", tone="잔잔하고 따뜻한 일상, 섬세한 감정선과 성장",
        premise=("평범한 고등학교의 2학년 교실. 봄에 전학 온 주인공이 낯선 학교와 반에 서서히 스며든다. "
                 "특별한 능력도 거대한 사건도 없이 동아리·점심시간·축제 준비·진로 고민·서툰 첫 우정과 짝사랑 같은 "
                 "일상의 결을 따라가며, 작은 오해와 화해 속에서 천천히 자란다."),
        protagonist_hint="봄 전학생, 능력·초자연 전혀 없는 평범한 고교생, 일상의 서툰 감정과 관계", target_chapters=30),
    # 현대판타지 액션 — 지문·전투가 늘어 무대사 run 이 길어지는 장르(스택 조판·대사 레버 반대 압력).
    "modfan": ProjectSeed(
        title="", genre="현대 판타지", tone="현대 도시와 초자연이 겹친 긴박한 액션, 속도감 있는 전투와 생존",
        premise=("어느 날부터 도시 곳곳에 이계로 통하는 균열이 열리고, 그 너머의 존재들이 넘어오기 시작한 현대. "
                 "균열에 노출된 일부 사람은 초상적 힘에 눈뜬다. 평범한 삶을 살던 주인공이 뜻하지 않게 그 힘에 각성하며, "
                 "쏟아지는 위협과 세력 다툼 한복판에서 싸우고 달아나며 균열의 근원에 얽혀든다."),
        protagonist_hint="균열의 시대, 뒤늦게 각성한 평범한 청년, 쫓기는 전투와 세력 다툼", target_chapters=30),
}


# ── 사전 등록 밴드(design-style-lightness §4) — 충족 개수가 1차 주판정 ──────────────
def band_eval(s: dict) -> dict:
    """summary(회차 평균) → 6밴드 충족 여부 + 충족 개수. 판정기 아님(사전 등록 임계 대조표)."""
    tics = {k: s[k] for k in ("tic_neg_correction", "tic_judgment_suspend",
                              "tic_pseudo_precision", "tic_dialogue_gloss")}
    bands = {
        "over3sent≤0.15": s["para_over3sent_ratio"] <= 0.15,
        "p_sent_mean∈[1.6,2.4]": 1.6 <= s["para_sent_mean"] <= 2.4,
        "p_1sent≥0.45": s["para_1sent_ratio"] >= 0.45,
        "narr_run≤20": s["max_narration_run"] <= 20,
        "tics_each≤1": all(v <= 1 for v in tics.values()),
        "attrib≥0.08": s["dialogue_attrib_ratio"] >= 0.08,
    }
    return {"bands": bands, "met": sum(bands.values()), "tics": tics}


def lr1_baseline_band() -> dict | None:
    """기존 st1 baseline JSON(72화 combined)에서 LR-1 밴드 충족 재계산(자기정합). 없으면 None."""
    p = OUT / "st1_lightness_baseline.json"
    if not p.exists():
        return None
    c = json.loads(p.read_text(encoding="utf-8"))["combined"]["summary"]
    return {"summary": c, **band_eval(c)}


# ── 생성 ────────────────────────────────────────────────────────────────────────
def _base_settings():
    # advisory 콜(본문 무영향) off — 양팔 동일. reflow OFF 로 저장 → pre-reflow 확보(측정서 post 재계산).
    return get_settings().model_copy(update={
        "reader_desk": False, "claim_audit": False, "paragraph_reflow": False})


def _arm_settings(base, provider: str, model: str):
    return base.model_copy(update={"llm_provider": provider, "gen_model": model})


def _dump(seed_key: str, arm: str, data: dict, prefix: str = "stv") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{prefix}_{seed_key}_{arm}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / f"{prefix}_{seed_key}_{arm}.txt").write_text(
        "\n\n\n".join(f"[ch{c['chapter']}] {c['title']}\n{c['text']}" for c in data["chapters"]),
        encoding="utf-8")


def _run_arm(base, repo, seed_key: str, pid: str, arm: str, provider: str, model: str,
             prefix: str = "stv") -> None:
    prov, mdl = provider, model
    svc = CopilotService(_arm_settings(base, prov, mdl), repo)
    svc.get_session(pid)
    have = len([c for c in svc.repo.get(pid).chapters if c.status == ChapterStatus.FINALIZED])
    print(f"[{seed_key}/{arm}] 본문모델={prov}:{mdl} — {N_CH}회차 집필(기존 {have}화)", flush=True)
    esc = attempts = fails = consecutive_fail = 0
    op_log, fallback_at = [], None
    while attempts < (N_CH - have) + 6:      # 실패 재시도 여유
        done = [c for c in svc.repo.get(pid).chapters if c.status == ChapterStatus.FINALIZED]
        if len(done) >= N_CH:
            break
        attempts += 1
        t0 = time.time()
        err = None
        try:
            r = svc.generate_next_chapter(pid)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:180]}"
            r = {}
        dt = round(time.time() - t0, 1)
        rec = r.get("record") if isinstance(r, dict) else None
        row = {"attempt": attempts, "for_chapter": len(done) + 1, "sec": dt,
               "model": f"{prov}:{mdl}", "error": err,
               "status": (rec.status.value if rec and hasattr(rec.status, "value") else
                          (str(rec.status) if rec else None)),
               "len": (len(rec.text) if rec else 0)}
        op_log.append(row)
        if err or rec is None:
            fails += 1; consecutive_fail += 1
            print(f"  #{attempts} for_ch{len(done)+1} {dt}s FAIL {err or 'record 없음'}", flush=True)
        else:
            consecutive_fail = 0
            if rec.status != ChapterStatus.FINALIZED:
                esc += 1
            print(f"  #{attempts} ch{rec.chapter} {row['status']} {row['len']}자 {dt}s "
                  f"hook={rec.hook_type!r} place={rec.place!r}", flush=True)
        # 폴백: g55 가 반복 실패하면 gpt-5.2-chat-latest 로 스왑(fresh svc → fresh provider), 명기.
        if (arm == "g55" and fallback_at is None and consecutive_fail >= FALLBACK_TRIGGER):
            prov, mdl = G55_FALLBACK
            fallback_at = len(done) + 1
            svc = CopilotService(_arm_settings(base, prov, mdl), repo)
            svc.get_session(pid)
            consecutive_fail = 0
            print(f"  !! [{seed_key}/{arm}] {FALLBACK_TRIGGER}연속 실패 → 폴백 {prov}:{mdl} "
                  f"(from ch{fallback_at})", flush=True)

    st = svc.repo.get(pid)
    fin = sorted([c for c in st.chapters if c.status == ChapterStatus.FINALIZED],
                 key=lambda c: c.chapter)[:N_CH]
    data = {"seed": seed_key, "genre": st.seed.genre, "arm": arm,
            "provider": provider, "model_primary": model,
            "model_final": f"{prov}:{mdl}", "fallback_at": fallback_at,
            "world_title": st.world.title, "planning_model": base.planning_model,
            "worldgen_model": base.worldgen_model, "paragraph_reflow_at_gen": False,
            "attempts": attempts, "escalated": esc, "fails": fails, "op_log": op_log,
            "usage_total": getattr(st, "usage_total", {}),
            "style_rules_snapshot": list(st.world.style.rules),
            "style_rules_is_default": list(st.world.style.rules) == list(DEFAULT_STYLE_RULES),
            "system_persona": st.world.style.system_persona,
            "chapters": [{"chapter": c.chapter, "title": c.title, "len": len(c.text),
                          "hook_type": c.hook_type, "chapter_function": c.chapter_function,
                          "place": c.place, "summary": c.summary, "text": c.text} for c in fin]}
    _dump(seed_key, arm, data, prefix)
    print(f"  [{seed_key}/{arm}] 완료 {len(fin)}화(escalated {esc}·fail {fails}"
          f"{'·폴백@'+str(fallback_at) if fallback_at else ''}) → {prefix}_{seed_key}_{arm}.json", flush=True)
    if len(fin) < N_CH:
        print(f"  !! [{seed_key}/{arm}] {N_CH}화 미달 — 이 arm 판정 주의(부분)", flush=True)


def _create_world(base, repo, seed_key: str):
    """세계 1회 생성(arm 중립 — worldgen/planning=claude-4-8). style.rules 스냅샷 즉시 검사."""
    svc0 = CopilotService(base, repo)
    seed = SEEDS[seed_key]
    print(f"=== [{seed_key}] {seed.genre} — 세계 생성(공유 1회, worldgen={base.worldgen_model})...(느림)", flush=True)
    st, _ = svc0.create_project(seed.model_copy(deep=True))
    is_def = list(st.world.style.rules) == list(DEFAULT_STYLE_RULES)
    print(f"  세계='{st.world.title}' 엔티티 {len(st.world.entities)} | "
          f"style.rules == DEFAULT_STYLE_RULES(ST-2)? {is_def} | repo={repo.dir.parent}", flush=True)
    if not is_def:
        print(f"  !! [{seed_key}] worldgen LLM 이 style.rules 를 자기값으로 덮어씀 — ST-2 미적용(스냅샷 검사 적발)", flush=True)
    return svc0, st


def gen(seed_key: str, only_arm: str | None = None) -> int:
    base = _base_settings()
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp(prefix=f"stv_{seed_key}_")))
    _svc0, st = _create_world(base, repo, seed_key)
    for arm, (prov, mdl) in ARMS.items():
        if only_arm and arm != only_arm:
            continue
        if (OUT / f"stv_{seed_key}_{arm}.json").exists():
            print(f"[{seed_key}/{arm}] 리포트 JSON 존재 — 스킵", flush=True); continue
        cp = st.model_copy(deep=True); cp.id = st.id + arm
        repo.save(cp)
        _run_arm(base, repo, seed_key, cp.id, arm, prov, mdl)
    return 0


def resume(seed_key: str, repo_dir: str) -> int:
    base = _base_settings()
    repo = FilesystemProjectRepository(Path(repo_dir))
    ids = {p.stem for p in repo.dir.glob("*.json") if ".rag." not in p.name}
    if not ids:
        print(f"repo 에 프로젝트 없음: {repo.dir}"); return 1
    bstate = min(ids, key=len)
    print(f"=== [{seed_key}] resume — repo={repo.dir.parent} base={bstate}", flush=True)
    for arm, (prov, mdl) in ARMS.items():
        if (OUT / f"stv_{seed_key}_{arm}.json").exists():
            print(f"[{seed_key}/{arm}] 리포트 JSON 존재 — 스킵", flush=True); continue
        pid = bstate + arm
        if pid not in ids:
            b = repo.get(bstate)
            if b is None or b.chapters:
                print(f"[{seed_key}/{arm}] base 상태 이상 — 스킵", flush=True); continue
            cp = b.model_copy(deep=True); cp.id = pid; repo.save(cp)
            print(f"[{seed_key}/{arm}] arm 복사 재생성({pid})", flush=True)
        _run_arm(base, repo, seed_key, pid, arm, prov, mdl)
    return 0


# ── STV-2 : ST-6 배선 후 최종 재검증(단일 arm c46 = 현행 gen_model, 사전 게이트) ──────
#  STV(dead rules) 산출 stv_ 는 비교 baseline 으로 보존하고 재검증 산출은 stv2_ 로 분리 저장.
#  핵심 차이: 생성 전 하드 게이트 — style.rules != DEFAULT_STYLE_RULES(또는 persona 불일치)면
#  ST-6 이 신규 작품에 실도달하지 못한 것이므로 회차 집필 없이 즉시 중단(토큰 낭비·오측정 방지).
STV2_PREFIX = "stv2"
STV2_ARM = "c46"     # STV-2 = 현행 gen_model 단일 arm(모델 재평가는 STV 에서 완료 — 여기선 규칙 델타만)


def _stv2_gate(st) -> tuple[bool, dict]:
    """ST-6 실도달 증명 게이트 — 신규 작품의 style.rules·persona 가 코드 SSOT 와 일치하는가."""
    rules_default = list(st.world.style.rules) == list(DEFAULT_STYLE_RULES)
    persona_default = st.world.style.system_persona == StyleSpec().system_persona
    length_rule = any("4,500~5,500" in r for r in st.world.style.rules)   # 분량 규칙 복원 확인(LN-1)
    info = {"rules_default": rules_default, "persona_default": persona_default,
            "length_rule_present": length_rule,
            "n_rules": len(st.world.style.rules),
            "persona_head": st.world.style.system_persona[:40]}
    return (rules_default and persona_default), info


def gen2(seed_key: str) -> int:
    """STV-2 생성 — 세계 1회 생성 → ST-6 하드 게이트 → 통과 시에만 c46 4화 집필(stv2_ 산출)."""
    base = _base_settings()
    if (OUT / f"{STV2_PREFIX}_{seed_key}_{STV2_ARM}.json").exists():
        print(f"[STV-2/{seed_key}] 산출 JSON 존재 — 스킵(재생성하려면 파일 삭제)", flush=True)
        return 0
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp(prefix=f"stv2_{seed_key}_")))
    svc0 = CopilotService(base, repo)
    seed = SEEDS[seed_key]
    print(f"=== [STV-2/{seed_key}] {seed.genre} — 세계 생성(worldgen={base.worldgen_model})...(느림)", flush=True)
    st, _ = svc0.create_project(seed.model_copy(deep=True))
    ok, info = _stv2_gate(st)
    print(f"  세계='{st.world.title}' 엔티티 {len(st.world.entities)} | repo={repo.dir.parent}", flush=True)
    print(f"  [게이트] rules==DEFAULT_STYLE_RULES(ST-6)? {info['rules_default']} "
          f"(n_rules={info['n_rules']}) | persona==기본? {info['persona_default']} "
          f"(head={info['persona_head']!r}) | 분량규칙(4,500~5,500) 복원? {info['length_rule_present']}", flush=True)
    if not ok:
        print(f"  !! [STV-2/{seed_key}] 게이트 실패 — ST-6 이 신규 작품에 미도달. 회차 집필 중단·보고.", flush=True)
        return 3
    print(f"  [게이트] 통과 — ST-6 실도달 증명. c46 {N_CH}화 집필 진행.", flush=True)
    cp = st.model_copy(deep=True); cp.id = st.id + STV2_ARM
    repo.save(cp)
    prov, mdl = ARMS[STV2_ARM]
    _run_arm(base, repo, seed_key, cp.id, STV2_ARM, prov, mdl, prefix=STV2_PREFIX)
    print(f"  [STV-2/{seed_key}] temp repo(resume 용): {repo.dir}", flush=True)
    return 0


def resume2(seed_key: str, repo_dir: str) -> int:
    """STV-2 resume — 프로세스 킬 후 이어쓰기(base 상태에서 c46 복사 재생성). 게이트는 base 로 재확인."""
    base = _base_settings()
    if (OUT / f"{STV2_PREFIX}_{seed_key}_{STV2_ARM}.json").exists():
        print(f"[STV-2/{seed_key}] 산출 JSON 존재 — 스킵", flush=True)
        return 0
    repo = FilesystemProjectRepository(Path(repo_dir))
    ids = {p.stem for p in repo.dir.glob("*.json") if ".rag." not in p.name}
    if not ids:
        print(f"repo 에 프로젝트 없음: {repo.dir}"); return 1
    bstate = min(ids, key=len)
    pid = bstate + STV2_ARM
    print(f"=== [STV-2/{seed_key}] resume — repo={repo.dir.parent} base={bstate}", flush=True)
    if pid not in ids:
        b = repo.get(bstate)
        if b is None:
            print(f"[STV-2/{seed_key}] base 상태 이상 — 스킵", flush=True); return 1
        ok, info = _stv2_gate(b)
        print(f"  [게이트/resume] rules_default={info['rules_default']} persona_default={info['persona_default']}", flush=True)
        if not ok:
            print(f"  !! [STV-2/{seed_key}] 게이트 실패 — 중단.", flush=True); return 3
        cp = b.model_copy(deep=True); cp.id = pid; repo.save(cp)
        print(f"[STV-2/{seed_key}] arm 복사 재생성({pid})", flush=True)
    prov, mdl = ARMS[STV2_ARM]
    _run_arm(base, repo, seed_key, pid, STV2_ARM, prov, mdl, prefix=STV2_PREFIX)
    return 0


def measure2() -> int:
    """STV-2 3원 비교 측정 — LR-1(0/6) vs STV c46(dead rules) vs STV-2 c46(ST-6 live rules).
    조판(reflow)은 양 STV 세대 동일하므로 post-reflow 밴드·핵심갭의 세대 차 = ST-2 규칙의 순수 델타."""
    lr1 = lr1_baseline_band()
    rep = {"report": "STV-2 — ST-6 배선 후 문체 스택 최종 재검증(결정론 3원 비교)",
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "bands": list(band_eval(lr1["summary"])["bands"]) if lr1 else [],
           "lr1_baseline": lr1, "seeds": {}}
    key_metrics = ["dialogue_char_ratio", "dialogue_para_ratio", "max_narration_run",
                   "para_over3sent_ratio", "para_sent_mean", "para_1sent_ratio",
                   "dialogue_attrib_ratio", "sent_len_mean", "n_char"]
    for seed_key in SEEDS:
        seed_out = {}
        for gen_label, prefix in (("stv_dead", "stv"), ("stv2_live", STV2_PREFIX)):
            p = OUT / f"{prefix}_{seed_key}_{STV2_ARM}.json"
            if not p.exists():
                print(f"[{seed_key}/{gen_label}] 데이터 없음({p.name}) — 스킵"); continue
            d = json.loads(p.read_text(encoding="utf-8"))
            s = _arm_summaries(d)
            chapter_lens = [c["len"] for c in d["chapters"]]
            seed_out[gen_label] = {
                "prefix": prefix, "model_final": d.get("model_final"),
                "style_rules_is_default": d.get("style_rules_is_default"),
                "n_ch": s["n_ch"], "chapter_lens": chapter_lens,
                "len_min": min(chapter_lens), "len_max": max(chapter_lens),
                "pre": s["pre"], "post": s["post"],
                "pre_band": s["pre_band"], "post_band": s["post_band"],
                "key_post": {k: s["post"][k] for k in key_metrics},
                "key_pre": {k: s["pre"][k] for k in key_metrics}}
            print(f"[{seed_key}/{gen_label}] {d.get('model_final')} rules_default={d.get('style_rules_is_default')} "
                  f"밴드 pre={s['pre_band']['met']}/6 post={s['post_band']['met']}/6 "
                  f"lens={chapter_lens}")
            print(f"    post: over3={s['post']['para_over3sent_ratio']} psent={s['post']['para_sent_mean']} "
                  f"1s={s['post']['para_1sent_ratio']} narr={s['post']['max_narration_run']} "
                  f"attrib={s['post']['dialogue_attrib_ratio']} dlg_char={s['post']['dialogue_char_ratio']}")
            print(f"    tics post: {s['post_band']['tics']}")
        # 규칙 순수 델타(post 밴드 충족 개수·핵심 갭) — 조판 동일이므로 세대 차 = ST-2 기여
        if "stv_dead" in seed_out and "stv2_live" in seed_out:
            dead, live = seed_out["stv_dead"], seed_out["stv2_live"]
            seed_out["rule_delta"] = {
                "post_band_met": {"dead": dead["post_band"]["met"], "live": live["post_band"]["met"],
                                  "delta": live["post_band"]["met"] - dead["post_band"]["met"]},
                "dlg_char": {"dead": dead["key_post"]["dialogue_char_ratio"],
                             "live": live["key_post"]["dialogue_char_ratio"]},
                "dlg_attrib": {"dead": dead["key_post"]["dialogue_attrib_ratio"],
                               "live": live["key_post"]["dialogue_attrib_ratio"]},
                "para_1sent": {"dead": dead["key_post"]["para_1sent_ratio"],
                               "live": live["key_post"]["para_1sent_ratio"]},
                "tics_dead": dead["post_band"]["tics"], "tics_live": live["post_band"]["tics"],
                "len_dead": [dead["len_min"], dead["len_max"]],
                "len_live": [live["len_min"], live["len_max"]]}
            rd = seed_out["rule_delta"]
            print(f"  [{seed_key}] 규칙 순수 델타(post 밴드): dead {rd['post_band_met']['dead']}/6 → "
                  f"live {rd['post_band_met']['live']}/6 (Δ{rd['post_band_met']['delta']:+d}) | "
                  f"dlg_char {rd['dlg_char']['dead']}→{rd['dlg_char']['live']} | "
                  f"attrib {rd['dlg_attrib']['dead']}→{rd['dlg_attrib']['live']} | "
                  f"자수 {rd['len_dead']}→{rd['len_live']}")
        rep["seeds"][seed_key] = seed_out
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stv2_measure.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    if lr1:
        print(f"\nLR-1 baseline(72화): {lr1['met']}/6 밴드")
    print("measure2 →", OUT / "stv2_measure.json")
    return 0


# ── 측정(1차 주판정 — 결정론) ────────────────────────────────────────────────────
def _arm_summaries(data: dict) -> dict:
    """arm 데이터 → pre-reflow(저장본)·post-reflow(reflow 적용) 회차평균 summary 2종 + 밴드."""
    pre_m, post_m = [], []
    for c in data["chapters"]:
        t = c["text"] or ""
        pre_m.append(lightness_metrics(t, None))
        post_m.append(lightness_metrics(reflow_paragraphs(t), None))
    pre, post = summarize(pre_m), summarize(post_m)
    return {"n_ch": len(pre_m), "pre": pre, "post": post,
            "pre_band": band_eval(pre), "post_band": band_eval(post)}


def measure() -> int:
    lr1 = lr1_baseline_band()
    rep = {"report": "STV 신문체 스택 검증 — 결정론 밴드 측정",
           "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "bands": list(band_eval(lr1["summary"])["bands"]) if lr1 else [],
           "lr1_baseline": lr1, "seeds": {}}
    for seed_key in SEEDS:
        seed_out = {}
        for arm in ARMS:
            p = OUT / f"stv_{seed_key}_{arm}.json"
            if not p.exists():
                print(f"[{seed_key}/{arm}] 데이터 없음 — 스킵"); continue
            d = json.loads(p.read_text(encoding="utf-8"))
            s = _arm_summaries(d)
            seed_out[arm] = {"model_final": d.get("model_final"), "fallback_at": d.get("fallback_at"),
                             "style_rules_is_default": d.get("style_rules_is_default"),
                             "escalated": d.get("escalated"), "fails": d.get("fails"),
                             "n_ch": s["n_ch"], "pre": s["pre"], "post": s["post"],
                             "pre_band": s["pre_band"], "post_band": s["post_band"]}
            pb, qb = s["pre_band"]["met"], s["post_band"]["met"]
            print(f"[{seed_key}/{arm}] {d.get('model_final')} rules_default={d.get('style_rules_is_default')} "
                  f"밴드 pre={pb}/6 post={qb}/6 | esc={d.get('escalated')} fail={d.get('fails')}")
            print(f"    post: over3={s['post']['para_over3sent_ratio']} psent={s['post']['para_sent_mean']} "
                  f"1s={s['post']['para_1sent_ratio']} narr={s['post']['max_narration_run']} "
                  f"attrib={s['post']['dialogue_attrib_ratio']} dlg_char={s['post']['dialogue_char_ratio']}")
            print(f"    tics post: {s['post_band']['tics']}")
        rep["seeds"][seed_key] = seed_out
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stv_measure.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    if lr1:
        print(f"\nLR-1 baseline(72화): {lr1['met']}/6 밴드 — {lr1['bands']}")
    print("measure →", OUT / "stv_measure.json")
    return 0


# ── 참고 심사(2차 — 제3계열 gemini 쌍대) ───────────────────────────────────────────
def judge() -> int:
    from novelcopilot.llm.factory import create_role_provider
    jp = create_role_provider(get_settings(), f"{JUDGE_MODEL[0]}:{JUDGE_MODEL[1]}")
    SYS = ("너는 한국 웹소설 독자다. 같은 작품 설정으로 쓰인 두 연재분(A/B, 각 4화)을 읽고, 모바일로 "
           "더 술술 읽히고 다음 화가 궁금해 계속 넘기고 싶은 쪽을 하나만 골라라. 기준: 문단이 짧아 세로로 "
           "시원하게 읽히고, 대사·행동이 장면을 끌고 가며, 설명·묘사 벽이 적고, 문체가 기계적이지 않은가. "
           '{"winner":"A" 또는 "B","reason":"한 줄"} JSON만 출력.')
    log = {"judge_model": f"{JUDGE_MODEL[0]}:{JUDGE_MODEL[1]}", "note":
           "생성 모델이 서로 다른 arm(claude vs openai) 심사라 모델 선호가 교란 — 참고 지표. 주판정은 결정론 밴드(measure).",
           "pairs": JUDGE_PAIRS, "cap": JUDGE_CAP, "seeds": {}}
    for seed_key in SEEDS:
        arms = {}
        for arm in ARMS:
            p = OUT / f"stv_{seed_key}_{arm}.json"
            if not p.exists():
                break
            d = json.loads(p.read_text(encoding="utf-8"))
            # shipped stack = reflow ON → post-reflow 텍스트로 심사(읽는 느낌은 조판 포함).
            arms[arm] = "\n\n".join(reflow_paragraphs(c["text"]) for c in d["chapters"])
        if len(arms) < 2:
            print(f"[{seed_key}] arm 부족 — 스킵"); continue
        a_arm, b_arm = "c46", "g55"
        body = {a_arm: arms[a_arm], b_arm: arms[b_arm]}
        trunc = {k: len(v) > JUDGE_CAP for k, v in body.items()}
        print(f"\n[{seed_key}] 쌍대 {JUDGE_PAIRS}판(양순서 일치 승만, judge={JUDGE_MODEL[1]}) "
              f"c46 {len(body['c46'])}자 / g55 {len(body['g55'])}자 절단{trunc}", flush=True)
        c46w = g55w = disc = 0
        jlog = []
        for i in range(JUDGE_PAIRS):
            verdicts, reasons = [], []
            for x, y in ((a_arm, b_arm), (b_arm, a_arm)):
                msg = f"[A]\n{body[x][:JUDGE_CAP]}\n\n[B]\n{body[y][:JUDGE_CAP]}"
                try:
                    # gemini-2.5-flash 는 thinking 모델 — max_tokens=400 이면 사고분이 출력 예산을 삼켜
                    # JSON 이 중간 절단됨(1차 심사 전판 무효 실측). 2000 으로 상향.
                    r = jp.chat_json([{"role": "system", "content": SYS},
                                      {"role": "user", "content": msg}], temperature=0.3, max_tokens=2000)
                    w = r.get("winner")
                    verdicts.append(x if w == "A" else (y if w == "B" else None))
                    reasons.append(str(r.get("reason", ""))[:160])
                except Exception as e:
                    verdicts.append(None); reasons.append(f"실패 {str(e)[:90]}")
            if len(verdicts) == 2 and verdicts[0] and verdicts[0] == verdicts[1]:
                res = verdicts[0]
                if res == "c46": c46w += 1
                else: g55w += 1
                print(f"  판{i+1}: {res} 승 — {reasons[0]} / {reasons[1]}", flush=True)
            else:
                disc += 1
                print(f"  판{i+1}: 불일치 {verdicts} — {reasons[0]} / {reasons[1]}", flush=True)
            jlog.append({"pair": i + 1, "verdicts": verdicts, "reasons": reasons})
        v = "c46 우세" if c46w > g55w else ("g55 우세" if g55w > c46w else "무승부")
        print(f"  → {v} (c46 {c46w} / g55 {g55w} / 불일치 {disc})", flush=True)
        log["seeds"][seed_key] = {"c46_wins": c46w, "g55_wins": g55w, "discord": disc,
                                  "verdict": v, "truncated": trunc,
                                  "len": {k: len(v) for k, v in body.items()}, "judgments": jlog}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stv_judge_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print("judge →", OUT / "stv_judge_log.json")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = sys.argv[1:]
    if not a or a[0] not in ("gen", "resume", "measure", "judge", "gen2", "resume2", "measure2"):
        print(__doc__); return 2
    if a[0] == "gen":
        if len(a) < 2 or a[1] not in SEEDS:
            print(f"gen <{'|'.join(SEEDS)}> [arm]"); return 2
        return gen(a[1], a[2] if len(a) > 2 else None)
    if a[0] == "resume":
        if len(a) < 3 or a[1] not in SEEDS:
            print(f"resume <{'|'.join(SEEDS)}> <repo_dir>"); return 2
        return resume(a[1], a[2])
    if a[0] == "measure":
        return measure()
    if a[0] == "gen2":
        if len(a) < 2 or a[1] not in SEEDS:
            print(f"gen2 <{'|'.join(SEEDS)}>"); return 2
        return gen2(a[1])
    if a[0] == "resume2":
        if len(a) < 3 or a[1] not in SEEDS:
            print(f"resume2 <{'|'.join(SEEDS)}> <repo_dir>"); return 2
        return resume2(a[1], a[2])
    if a[0] == "measure2":
        return measure2()
    return judge()


if __name__ == "__main__":
    sys.exit(main())
