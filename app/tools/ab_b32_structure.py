# -*- coding: utf-8 -*-
"""B-32e A/B — '구조 사용 이력' 주입(B-32, 4237f0e+e78ca0d)의 효과 검증(사전 등록: docs/design-b32-structure-history.md §5).

레버: beat_for_episode·generate_event_menu 는 이미 structure_history 파라미터를 받아 [최근 회차 구조 이력]
      advisory 블록을 렌더하지만(4237f0e), 프로덕션(copilot.py)은 그걸 *전달하지 않는다*(B-32e A/B 전용).
      ON arm = 실험 레벨에서 ArcPlanner 두 메서드를 감싸 직전 회차들의 structure_history(prior)를 주입.
      OFF arm = 현행 그대로(미전달 → 프롬프트 바이트 동일). 엔진 프로덕션 코드 무변경(C-2 전례: 실험 레벨 배선).

측정 4원칙:
  1) 결정론 재탕 지표(1차 목표) — hook/place 최대 run·최빈 비율, key_events 라벨 재출현률,
     인접 회차 구조 유사도(요약·key_events char-2gram echo, 훅/장소/기능 인접 일치). 판정기 아님(원시 숫자).
  2) 선호(2차) — 중립 루브릭 블라인드 쌍대 3판, 양순서 일치 승만, gen(anthropic)≠judge(openai).
  3) 최종 산출(프로즈) 측정 — 회차 본문 심사(설정 직렬화 심사 금지).
  4) 세계 공유(arm 간 동일 세계·spine deep-copy) — 주입 외 조건 동일.

사전 등록 kill(이원, docs/design §5):
  - 선호 무승부 이하 AND 재탕 지표 미개선  → 폐기(discard).
  - 선호 무승부여도 재탕 지표 뚜렷 개선     → '일관성 개선' 채택 권고(프로덕션 배선 1~3줄 후속).
  - 선호까지 ON 우세                         → 완승 채택.
  '재탕 지표 뚜렷 개선' 결정론 규칙(생성 전 고정): 헤드라인 3지표(hook_max_run·place_max_run·kev_reappear_rate)에서
   2시드 풀 기준 ON 개선(값 감소) 지표 수 ≥ 2 AND 헤드라인 악화(값 증가) 지표 수 = 0.

비용: 2시드 × {off,on} × 6회차 = 24회차 생성 + 심사 12콜(3판×2순서×2시드).

실행(app/ 에서, 단계 분리 — 장시간 생성·심사 분리, 킬 시 resume 재개):
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_b32_structure.py gen school
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_b32_structure.py gen modfan
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_b32_structure.py resume school <gen이 찍은 temp repo dir>
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_b32_structure.py judge

resume: gen 이 킬돼도 회차는 FINALIZED 단위로 temp repo 에 영속 — 같은 repo 지목 시 '리포트 JSON 없는 arm'만 이어감.
"""
from __future__ import annotations
import sys, json, gzip, tempfile
from collections import Counter
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.domain.types import ChapterStatus
from novelcopilot.engine.pacing import label_max_run, prose_rehash, event_echo, _norm_label
from novelcopilot.engine.structure_history import structure_history
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
import novelcopilot.worldgen.arc_planner as _ap

OUT = Path(__file__).resolve().parent / "reports"
N_CH = 6                                  # 재탕은 인접 회차 현상 — 6화가 3화보다 판정력(설계 §5)
JUDGE_PAIRS = 3
JUDGE_CAP = 26000                         # 6화 본문 상한(자) — 절단 여부 로그
JUDGE_MODEL = "gpt-5.2-chat-latest"       # 생성(anthropic)과 다른 벤더 — gen≠judge

SEEDS = {
    # 학원물: ab_genres.py '학원물(잔잔한 일상)' 재사용(도구 간 시드 정합·재탕 최다 실측 장르).
    "school": ProjectSeed(
        title="", genre="학원물(잔잔한 일상)", tone="잔잔하고 따뜻한 일상, 섬세한 감정선과 성장",
        premise=("평범한 고등학교의 2학년 교실. 봄에 전학 온 주인공이 낯선 학교와 반에 서서히 스며든다. "
                 "특별한 능력도 거대한 사건도 없이 동아리·점심시간·축제 준비·진로 고민·서툰 첫 우정과 짝사랑 같은 "
                 "일상의 결을 따라가며, 작은 오해와 화해 속에서 천천히 자란다."),
        protagonist_hint="봄 전학생, 능력·초자연 전혀 없는 평범한 고교생, 일상의 서툰 감정과 관계", target_chapters=30),
    # 현대 판타지: 중립 프롬프트(트로프 호명·부정 최소). 재탕 실측된 '각성 원패턴' 계열 장르 대표.
    "modfan": ProjectSeed(
        title="", genre="현대 판타지", tone="현대 도시와 초자연이 겹친 긴장, 성장과 생존",
        premise=("어느 날부터 도시 곳곳에 이계로 통하는 균열이 열리고, 그 너머의 존재들이 넘어오기 시작한 현대. "
                 "균열에 노출된 일부 사람은 초상적 힘에 눈뜬다. 평범한 삶을 살던 주인공이 뜻하지 않게 그 힘에 각성하며, "
                 "균열을 둘러싼 세력과 그 근원의 비밀에 얽혀든다."),
        protagonist_hint="균열의 시대, 뒤늦게 각성한 평범한 청년, 세력 다툼과 균열의 근원", target_chapters=30),
}

# ---- ON arm 배선: ArcPlanner 두 메서드를 감싸 structure_history 를 주입(active 게이트로 OFF 는 바이트 동일) ----
_ORIG_MENU = _ap.ArcPlanner.generate_event_menu
_ORIG_BEAT = _ap.ArcPlanner.beat_for_episode
_HOLDER = {"active": False, "hist": None}


def _menu(self, *a, **k):
    if _HOLDER["active"] and "structure_history" not in k:
        k["structure_history"] = _HOLDER["hist"]
    return _ORIG_MENU(self, *a, **k)


def _beat(self, *a, **k):
    if _HOLDER["active"] and "structure_history" not in k:
        k["structure_history"] = _HOLDER["hist"]
    return _ORIG_BEAT(self, *a, **k)


def _install_injection() -> None:
    """멱등 설치 — 클래스 메서드 교체(게이트 off 로 시작하니 OFF·세계생성엔 무영향)."""
    if getattr(_ap.ArcPlanner.generate_event_menu, "_b32e", False):
        return
    _menu._b32e = _beat._b32e = True
    _ap.ArcPlanner.generate_event_menu = _menu
    _ap.ArcPlanner.beat_for_episode = _beat


def _kev_by_chapter(records) -> dict:
    """영속 회차 → {chapter: [key_events...]} (structure_history 파생 경로 = gen_context['draft']['beat'] 등)."""
    items = structure_history(records, n=999).get("items") or []
    return {it["chapter"]: list(it.get("key_events") or []) for it in items}


def _dump(seed_key: str, arm: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"b32e_{seed_key}_{arm}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / f"b32e_{seed_key}_{arm}.txt").write_text(
        "\n\n".join(c["text"] for c in data["chapters"]), encoding="utf-8")


def _base_settings():
    # advisory 전용 콜(본문 무영향)만 off — 양팔 동일. 프로즈 경로는 production 그대로.
    return get_settings().model_copy(update={"reader_desk": False, "claim_audit": False})


def _run_arm(s0, repo, seed_key: str, pid: str, arm: str, inject: bool) -> None:
    svc = CopilotService(s0, repo)
    svc.get_session(pid)
    st = svc.repo.get(pid)
    have = len([c for c in st.chapters if c.status == ChapterStatus.FINALIZED])
    print(f"[{seed_key}/{arm}] structure_history 주입={'ON' if inject else 'off'} — "
          f"{N_CH}회차 집필(기존 {have}화)", flush=True)
    esc = attempts = 0
    inj_log = []
    while attempts < (N_CH - have) + 3:      # ESCALATED(비영속) 재시도 여유 +3
        done = [c for c in svc.repo.get(pid).chapters if c.status == ChapterStatus.FINALIZED]
        if len(done) >= N_CH:
            break
        attempts += 1
        if inject:                           # 직전 회차들의 구조 이력을 이번 설계 콜에 주입
            records = list(svc.repo.get(pid).chapters)
            hist = structure_history(records)
            _HOLDER["hist"], _HOLDER["active"] = hist, True
            inj_log.append({"for_chapter": len(done) + 1, "n": hist["n"],
                            "kev_items": sum(1 for it in hist["items"] if it["key_events"]),
                            "hook_max_run": hist["hook_max_run"], "place_max_run": hist["place_max_run"]})
        else:
            _HOLDER["active"] = False
        try:
            r = svc.generate_next_chapter(pid)
        except Exception as e:
            print(f"  attempt#{attempts} ERR {type(e).__name__}: {str(e)[:160]}", flush=True)
            continue
        finally:
            _HOLDER["active"] = False        # 다음 루프까지 게이트 닫아 누수 방지
        rec = r.get("record")
        if rec is None:
            print(f"  attempt#{attempts} record 없음: {str(r)[:120]}", flush=True); continue
        if rec.status != ChapterStatus.FINALIZED:
            esc += 1
        print(f"  attempt#{attempts} ch{rec.chapter} "
              f"{rec.status.value if hasattr(rec.status,'value') else rec.status} "
              f"{len(rec.text)}자 hook={rec.hook_type!r} place={rec.place!r}", flush=True)
    st = svc.repo.get(pid)
    fin = sorted([c for c in st.chapters if c.status == ChapterStatus.FINALIZED], key=lambda c: c.chapter)[:N_CH]
    kev = _kev_by_chapter(fin)
    data = {"seed": seed_key, "genre": st.seed.genre, "arm": arm, "inject": inject,
            "world_title": st.world.title, "gen_model": s0.gen_model, "planning_model": s0.planning_model,
            "attempts": attempts, "escalated": esc, "injection_log": inj_log,
            "usage_total": getattr(st, "usage_total", {}),
            "chapters": [{"chapter": c.chapter, "title": c.title, "len": len(c.text),
                          "hook_type": c.hook_type, "chapter_function": c.chapter_function, "place": c.place,
                          "time_advance": c.time_advance, "summary": c.summary,
                          "key_events": kev.get(c.chapter, []), "text": c.text} for c in fin]}
    _dump(seed_key, arm, data)
    print(f"  [{seed_key}/{arm}] 완료 {len(fin)}화(escalated 재시도 {esc}) → b32e_{seed_key}_{arm}.json", flush=True)
    if len(fin) < N_CH:
        print(f"  !! [{seed_key}/{arm}] {N_CH}화 미달 — judge 에서 이 시드 판정 불가", flush=True)


def gen(seed_key: str) -> int:
    _install_injection()
    seed = SEEDS[seed_key]
    s0 = _base_settings()
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp(prefix=f"b32e_{seed_key}_")))
    svc0 = CopilotService(s0, repo)
    print(f"=== [{seed_key}] {seed.genre} — 세계 생성(공유 1회)...(느림)", flush=True)
    _HOLDER["active"] = False
    st, _ = svc0.create_project(seed.model_copy(deep=True))
    print(f"  세계='{st.world.title}' 엔티티 {len(st.world.entities)} | 본문모델={s0.gen_model} | repo={repo.dir.parent}", flush=True)
    for arm, inject in (("off", False), ("on", True)):
        cp = st.model_copy(deep=True); cp.id = st.id + arm
        svc0.repo.save(cp)
        _run_arm(s0, repo, seed_key, cp.id, arm, inject)
    return 0


def resume(seed_key: str, repo_dir: str) -> int:
    _install_injection()
    s0 = _base_settings()
    repo = FilesystemProjectRepository(Path(repo_dir))
    ids = {p.stem for p in repo.dir.glob("*.json") if ".rag." not in p.name}
    if not ids:
        print(f"repo 에 프로젝트 없음: {repo.dir}"); return 1
    base = min(ids, key=len)
    print(f"=== [{seed_key}] resume — repo={repo.dir.parent} base={base}", flush=True)
    for arm, inject in (("off", False), ("on", True)):
        if (OUT / f"b32e_{seed_key}_{arm}.json").exists():
            print(f"[{seed_key}/{arm}] 리포트 JSON 존재 — 스킵", flush=True); continue
        pid = base + arm
        if pid not in ids:
            # gen 이 이 arm 의 복사 저장 '전에' 킬린 경우 — base(세계 생성 직후 원본·회차 0)에서
            # arm 복사를 재생성한다(gen 과 동일 절차 → 세계 공유 통제 보존).
            bst = repo.get(base)
            if bst is None or bst.chapters:
                print(f"[{seed_key}/{arm}] base 상태 이상(없음/회차 존재) — 스킵", flush=True); continue
            cp = bst.model_copy(deep=True); cp.id = pid
            repo.save(cp)
            print(f"[{seed_key}/{arm}] 저장 상태 없음 → base 에서 arm 복사 재생성({pid})", flush=True)
        _run_arm(s0, repo, seed_key, pid, arm, inject)
    return 0


# ---- 결정론 재탕 지표(원시 — 판정기 아님) ----
def _kev_norm(events: list[str]) -> list[str]:
    return [x for x in (_norm_label(e) for e in (events or [])) if x]


def rep16(text: str) -> dict:
    n = 16
    g = Counter(text[i:i + n] for i in range(max(0, len(text) - n)))
    rep = sum(1 for _, c in g.items() if c >= 2)
    raw = text.encode("utf-8")
    return {"rep16_per10k": round(rep / max(1, len(text)) * 10000, 2),
            "gzip": round(len(gzip.compress(raw)) / max(1, len(raw)), 3)}


def metrics(data: dict) -> dict:
    chs = sorted(data["chapters"], key=lambda c: c["chapter"])
    hooks = [c["hook_type"] for c in chs]
    places = [c["place"] for c in chs]
    funcs = [c["chapter_function"] for c in chs]
    texts = [c["text"] for c in chs]
    kev = [_kev_norm(c.get("key_events")) for c in chs]

    # key_events 라벨 재출현(전체·정규화 라벨 동일성) — '동일 사건 라벨 재출현'(설계 §5)
    label_chapters: dict[str, set] = {}
    for c, ks in zip(chs, kev):
        for k in set(ks):
            label_chapters.setdefault(k, set()).add(c["chapter"])
    distinct = len(label_chapters)
    reappeared = sum(1 for v in label_chapters.values() if len(v) >= 2)
    max_label_span = max((len(v) for v in label_chapters.values()), default=0)

    # 인접 회차 구조 유사도
    kev_echo = [round(event_echo(" ".join(kev[i + 1]), " ".join(kev[i])), 3) for i in range(len(kev) - 1)]
    sum_echo = [round(event_echo(chs[i + 1]["summary"], chs[i]["summary"]), 3) for i in range(len(chs) - 1)]
    prose_echo = [round(prose_rehash(texts[i + 1], texts[i]), 3) for i in range(len(texts) - 1)]
    # 인접 훅/장소/기능 동일 여부(구조 라벨 인접 재사용) — 0~1 평균
    def _adj_match(seq):
        n = sum(1 for i in range(len(seq) - 1) if _norm_label(seq[i]) and _norm_label(seq[i]) == _norm_label(seq[i + 1]))
        return round(n / max(1, len(seq) - 1), 3)

    hooks_nz = [h for h in hooks if _norm_label(h)]
    places_nz = [p for p in places if _norm_label(p)]
    return {
        # 헤드라인(재탕 1차)
        "hook_max_run": label_max_run(hooks),
        "place_max_run": label_max_run(places),
        "kev_reappear_rate": round(reappeared / max(1, distinct), 3),
        # 보조
        "hook_monotony": round(Counter(map(_norm_label, hooks_nz)).most_common(1)[0][1] / len(hooks_nz), 2) if hooks_nz else 0.0,
        "place_monotony": round(Counter(map(_norm_label, places_nz)).most_common(1)[0][1] / len(places_nz), 2) if places_nz else 0.0,
        "kev_max_label_span": max_label_span,
        "kev_distinct": distinct, "kev_total": sum(len(k) for k in kev),
        "kev_echo_adj_mean": round(sum(kev_echo) / len(kev_echo), 3) if kev_echo else 0.0,
        "sum_echo_adj_mean": round(sum(sum_echo) / len(sum_echo), 3) if sum_echo else 0.0,
        "hook_adj_match": _adj_match(hooks), "place_adj_match": _adj_match(places), "func_adj_match": _adj_match(funcs),
        # advisory(구조엔 무감 — B-32 진단)
        "prose_echo_adj_mean": round(sum(prose_echo) / len(prose_echo), 3) if prose_echo else 0.0,
        "hooks": hooks, "places": places, "funcs": funcs,
        "kev_echo": kev_echo, "sum_echo": sum_echo,
        **rep16("\n\n".join(texts)),
    }


HEADLINE = ("hook_max_run", "place_max_run", "kev_reappear_rate")   # 낮을수록 재탕 적음(개선)
REUSE_LOWER_BETTER = HEADLINE + ("hook_monotony", "place_monotony", "kev_max_label_span",
                                 "kev_echo_adj_mean", "sum_echo_adj_mean",
                                 "hook_adj_match", "place_adj_match", "func_adj_match")


def judge() -> int:
    from novelcopilot.llm.openai_provider import OpenAIProvider
    jp = OpenAIProvider(JUDGE_MODEL, "text-embedding-3-small")
    SYS = ("너는 한국 웹소설 독자다. 같은 작품 설정으로 쓰인 두 연재분(A/B, 각 6화)을 읽고, 독자로서 "
           "더 계속 읽고 싶은 쪽을 하나만 골라라. 기준: 매 회차가 새롭게 전개되어 비슷한 장면·상황의 반복 없이 "
           "지루하지 않고, 다음 화가 궁금해지는가. "
           '{"winner":"A" 또는 "B","reason":"한 줄"} JSON만 출력.')
    log = {"judge_model": JUDGE_MODEL, "pairs": JUDGE_PAIRS, "cap": JUDGE_CAP, "rubric": SYS, "seeds": {}}
    verdict_by_seed, det_by_seed = {}, {}

    for seed_key in SEEDS:
        arms = {}
        for arm in ("on", "off"):
            p = OUT / f"b32e_{seed_key}_{arm}.json"
            if not p.exists():
                print(f"[{seed_key}] {arm} 데이터 없음({p.name}) — 시드 스킵"); break
            arms[arm] = json.loads(p.read_text(encoding="utf-8"))
        if len(arms) < 2 or any(len(d["chapters"]) < N_CH for d in arms.values()):
            verdict_by_seed[seed_key] = "판정 불가(생성 미달)"; continue

        print(f"\n=== [{seed_key}] {arms['on']['genre']} — 결정론 재탕 지표(원시) ===")
        det = {arm: metrics(arms[arm]) for arm in ("off", "on")}
        det_by_seed[seed_key] = det
        for m in REUSE_LOWER_BETTER:
            flag = " *헤드라인" if m in HEADLINE else ""
            print(f"  {m:20} OFF={det['off'][m]:<7} ON={det['on'][m]:<7} "
                  f"Δ={round(det['on'][m]-det['off'][m],3):<7}{flag}")

        body = {arm: "\n\n".join(c["text"] for c in arms[arm]["chapters"]) for arm in arms}
        trunc = {arm: len(body[arm]) > JUDGE_CAP for arm in body}
        print(f"  본문: ON {len(body['on'])}자 / OFF {len(body['off'])}자 (cap {JUDGE_CAP}, 절단 {trunc})")

        print(f"[{seed_key}] 블라인드 쌍대 {JUDGE_PAIRS}판(양순서 일치 승만, judge={JUDGE_MODEL})", flush=True)
        onw = offw = disc = 0
        jlog = []
        for p_i in range(JUDGE_PAIRS):
            verdicts, reasons = [], []
            for a_arm, b_arm in (("on", "off"), ("off", "on")):
                msg = f"[A]\n{body[a_arm][:JUDGE_CAP]}\n\n[B]\n{body[b_arm][:JUDGE_CAP]}"
                try:
                    d = jp.chat_json([{"role": "system", "content": SYS},
                                      {"role": "user", "content": msg}], temperature=0.3, max_tokens=400)
                    w = d.get("winner")
                    verdicts.append(a_arm if w == "A" else (b_arm if w == "B" else None))
                    reasons.append(str(d.get("reason", ""))[:160])
                except Exception as e:
                    verdicts.append(None); reasons.append(f"실패 {str(e)[:80]}")
            if len(verdicts) == 2 and verdicts[0] and verdicts[0] == verdicts[1]:
                res = verdicts[0]
                if res == "on": onw += 1
                else: offw += 1
                print(f"  판{p_i+1}: {res.upper()} 승(양순서 일치) — {reasons[0]} / {reasons[1]}", flush=True)
            else:
                disc += 1
                print(f"  판{p_i+1}: 불일치 {verdicts} — {reasons[0]} / {reasons[1]}", flush=True)
            jlog.append({"pair": p_i + 1, "verdicts": verdicts, "reasons": reasons})
        v = "ON 우세" if onw > offw else ("ON 열세" if offw > onw else "무승부")
        verdict_by_seed[seed_key] = f"{v} (ON {onw} / OFF {offw} / 불일치 {disc})"
        log["seeds"][seed_key] = {"deterministic": det, "judgments": jlog, "on_wins": onw,
                                  "off_wins": offw, "discord": disc, "verdict": v,
                                  "truncated": trunc, "len": {a: len(body[a]) for a in body}}

    # ---- 사전 등록 kill(이원) ----
    print("\n========== B-32e 사전 등록 판정 ==========")
    for k, v in verdict_by_seed.items():
        print(f"  선호[{k}]: {v}")
    pref_win = [k for k, v in verdict_by_seed.items() if v.startswith("ON 우세")]
    pref_lose = [k for k, v in verdict_by_seed.items() if v.startswith("ON 열세")]
    judged = [k for k in det_by_seed]

    # 헤드라인 재탕 지표: 2시드 풀 개선/악화 지표 수 집계(값 감소=개선)
    hl_improve = hl_regress = hl_tie = 0
    hl_detail = {}
    for m in HEADLINE:
        for sk in judged:
            d = round(det_by_seed[sk]["on"][m] - det_by_seed[sk]["off"][m], 3)
            hl_detail[f"{sk}.{m}"] = d
            if d < 0: hl_improve += 1
            elif d > 0: hl_regress += 1
            else: hl_tie += 1
    reuse_clear = (hl_improve >= 2 and hl_regress == 0)
    print(f"\n  헤드라인 재탕 Δ(ON-OFF, 음수=개선): {hl_detail}")
    print(f"  헤드라인 집계: 개선 {hl_improve} / 악화 {hl_regress} / 동률 {hl_tie} → 뚜렷 개선={reuse_clear}")

    pref_overall = "ON 우세" if (pref_win and not pref_lose) else ("ON 열세" if (pref_lose and not pref_win)
                    else ("혼재" if (pref_win and pref_lose) else "무승부"))
    if pref_win and not pref_lose:
        decision = "완승 채택 — 선호 ON 우세 + " + ("재탕 개선" if reuse_clear else "재탕 미개선(선호 근거)")
    elif reuse_clear:
        decision = "일관성 개선 채택 권고 — 선호 무승부 이하지만 재탕 지표 뚜렷 개선(프로덕션 배선 1~3줄 후속)"
    else:
        decision = "폐기(discard) — 선호 무승부 이하 AND 재탕 지표 미개선"
    print(f"\n  선호 종합: {pref_overall}")
    print(f"  KILL 판정: {decision}")

    log["headline_delta"] = hl_detail
    log["reuse_clear"] = reuse_clear
    log["pref_overall"] = pref_overall
    log["decision"] = decision
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "b32e_judge_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  심사 로그 → {OUT / 'b32e_judge_log.json'}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("gen", "resume", "judge"):
        print(__doc__); return 2
    if args[0] == "gen":
        if len(args) < 2 or args[1] not in SEEDS:
            print(f"gen <{'|'.join(SEEDS)}>"); return 2
        return gen(args[1])
    if args[0] == "resume":
        if len(args) < 3 or args[1] not in SEEDS:
            print(f"resume <{'|'.join(SEEDS)}> <repo_dir>"); return 2
        return resume(args[1], args[2])
    return judge()


if __name__ == "__main__":
    sys.exit(main())
