# -*- coding: utf-8 -*-
"""C-1 A/B — 페이싱 craft 지시(_CRAFT_PROGRESS)의 *일반화* 확인(신문구 기준).

배경: D-28(558037f)이 SL(잔잔 학원물 계열) 단일시드에서 craft ON 4:2 약확인.
C-3(6d2d99d)가 문구를 부정명령→긍정 구조지시로 교체 → 이 레버가 SL 특이적인지,
다른 장르(무협·로맨스판타지)로 일반화되는지 신문구로 재검증한다.

방법(측정 4원칙):
  1) 중립 루브릭 — craft/페이싱 메커니즘 단어를 심사문에 넣지 않는다(결과 차원만: 전개·다음 화 욕구).
  2) gen ≠ judge — 생성은 config 기본(anthropic), 심사는 다른 벤더(openai).
  3) 최종 산출(프로즈) 측정 — 회차 본문 자체를 심사(설정 직렬화 심사 금지).
  4) 블라인드·위치편향 상쇄 — A/B 라벨 무작위 아님·양순서 2판 일치 승만 인정(ab_model 관행).

사전 등록 판정(생성 전 고정 — 이 파일 커밋이 등록):
  - 시드별: 양순서-일치 쌍대 3판에서 ON 일치승 > OFF 일치승 → ON 우세 / 미만 → ON 열세 / 그 외 → 무승부.
  - 2시드 모두 무승부 이하 → "일반화 실패 — SL 특이적"(kill).
  - 1시드 이상 ON 우세 & ON 열세 0시드 → 일반화 지지(D-28 약확인 → 확인 승격 근거).
  - ON 우세·열세 혼재 → 장르 조건부(혼재)로 보고.
  - 결정론 지표(label_max_run·prose_echo·event_echo·16gram)는 advisory — 판정을 뒤집지 않는다.

비용 통제: 시드 2 × {ON,OFF} × 3회차 = 12회차 생성 + 심사 12콜.
reader_desk/claim_audit(본문 무영향 advisory 콜)는 양팔 동일하게 off — 예산 절약, 통제 유지.

실행(app/ 에서, 단계 분리 — 장시간 생성과 심사를 나눠 재개 가능):
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_pacing_general.py gen wuxia
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_pacing_general.py gen rofan
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_pacing_general.py resume wuxia <gen이 쓴 temp repo dir>
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_pacing_general.py judge

resume: gen 프로세스가 도중 킬돼도 회차는 FINALIZED 단위로 temp repo 에 영속된다 —
같은 repo 를 지목하면 '리포트 JSON 이 없는 arm'만 저장 지점부터 이어 생성(세계 공유 통제 보존).
"""
from __future__ import annotations
import sys, json, gzip, tempfile
from collections import Counter
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.domain.types import ChapterStatus
from novelcopilot.engine.pacing import label_max_run, prose_rehash, event_echo
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService

OUT = Path(__file__).resolve().parent / "reports"
N_CH = 3
JUDGE_PAIRS = 3          # 양순서-일치 쌍대 판 수(판당 2콜)
JUDGE_CAP = 20000        # 심사 한쪽 본문 상한(자) — 3화 대부분 무절단 통과 목표(절단 여부 로그)
JUDGE_MODEL = "gpt-5.2-chat-latest"   # 생성(anthropic)과 다른 벤더 — gen≠judge

SEEDS = {
    # ab_genres.py 의 '정통 무협' 시드 재사용(도구 간 시드 정합)
    "wuxia": ProjectSeed(
        title="", genre="정통 무협", tone="강호의 비장함과 쾌감, 고풍스러운 어휘",
        premise=("멸문한 명문 정파의 마지막 후예가 절벽에서 떨어져 사라진 마교 교주의 절세 내공심법을 얻는다. "
                 "신분을 감추고 강호에 나선 그는 가문을 멸한 흑막을 쫓으며 정사대전의 한가운데로 빨려든다."),
        protagonist_hint="멸문 정파 후예, 마교 절세심법 기연, 신분 은닉 복수", target_chapters=30),
    "rofan": ProjectSeed(
        title="", genre="로맨스 판타지", tone="우아한 궁정 분위기, 설렘과 긴장, 감정선 중심",
        premise=("읽던 소설 속 '처형당하는 악녀' 공작 영애의 몸에서 깨어난 주인공. 예정된 파멸을 피하려 "
                 "원작에서 자신을 단죄했던 냉혹한 북부 대공에게 먼저 계약 약혼을 제안한다. 황실의 견제와 "
                 "원작 여주인공의 등장 속에서, 서로를 이용하려던 계약은 점점 진심으로 물든다."),
        protagonist_hint="악녀 빙의, 처형 엔딩 회피, 북부 대공과의 계약 약혼", target_chapters=30),
}


def _dump(seed_key: str, arm: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"c1_{seed_key}_{arm}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / f"c1_{seed_key}_{arm}.txt").write_text(
        "\n\n".join(c["text"] for c in data["chapters"]), encoding="utf-8")


def _base_settings():
    # advisory 전용 콜(본문 무영향)만 off — 양팔 동일 조건. 프로즈 경로(continuity_polish 등)는 production 그대로.
    return get_settings().model_copy(update={"reader_desk": False, "claim_audit": False})


def _run_arm(s0, repo, seed_key: str, pid: str, arm: str, flag: bool) -> None:
    """한 arm 을 pid 의 저장 상태에서 N_CH FINALIZED 까지 이어 생성(멱등 — 이미 있으면 남은 만큼만)."""
    s_arm = s0.model_copy(update={"craft_progress": flag})   # production 토글 그대로가 레버(주입 문구=C-3 신문구)
    svc = CopilotService(s_arm, repo)
    sess, _ = svc.get_session(pid)
    cb = sess.bundle.generator.craft_block
    assert bool(cb) == flag, f"craft_block 상태가 토글과 불일치: arm={arm} block={cb!r}"
    st = svc.repo.get(pid)
    have = len([c for c in st.chapters if c.status == ChapterStatus.FINALIZED])
    print(f"[{seed_key}/{arm}] craft_block={'주입(신문구)' if cb else '없음'} — "
          f"{N_CH}회차 집필(기존 {have}화)", flush=True)
    esc = attempts = 0
    while attempts < (N_CH - have) + 3:   # ESCALATED(비영속) 재시도 여유분 +3
        done = [c for c in svc.repo.get(pid).chapters if c.status == ChapterStatus.FINALIZED]
        if len(done) >= N_CH:
            break
        attempts += 1
        try:
            r = svc.generate_next_chapter(pid)
        except Exception as e:
            print(f"  attempt#{attempts} ERR {type(e).__name__}: {str(e)[:160]}", flush=True)
            continue
        rec = r.get("record")
        if rec is None:
            print(f"  attempt#{attempts} record 없음: {str(r)[:120]}", flush=True); continue
        if rec.status != ChapterStatus.FINALIZED:
            esc += 1
        print(f"  attempt#{attempts} ch{rec.chapter} {rec.status.value if hasattr(rec.status,'value') else rec.status} "
              f"{len(rec.text)}자 hook={rec.hook_type!r} place={rec.place!r}", flush=True)
    st = svc.repo.get(pid)
    fin = sorted([c for c in st.chapters if c.status == ChapterStatus.FINALIZED], key=lambda c: c.chapter)[:N_CH]
    data = {"seed": seed_key, "genre": st.seed.genre, "arm": arm, "craft_injected": bool(cb),
            "world_title": st.world.title, "gen_model": s0.gen_model,
            "attempts": attempts, "escalated": esc,
            "usage_total": getattr(st, "usage_total", {}),
            "chapters": [{"chapter": c.chapter, "title": c.title, "len": len(c.text),
                          "hook_type": c.hook_type, "place": c.place, "time_advance": c.time_advance,
                          "summary": c.summary, "text": c.text} for c in fin]}
    _dump(seed_key, arm, data)
    print(f"  [{seed_key}/{arm}] 완료 {len(fin)}화(escalated 재시도 {esc}) → c1_{seed_key}_{arm}.json", flush=True)
    if len(fin) < N_CH:
        print(f"  !! [{seed_key}/{arm}] {N_CH}화 미달 — judge 단계에서 이 시드는 판정 불가 처리", flush=True)


def gen(seed_key: str) -> int:
    seed = SEEDS[seed_key]
    s0 = _base_settings()
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp(prefix=f"c1_{seed_key}_")))
    svc0 = CopilotService(s0, repo)
    print(f"=== [{seed_key}] {seed.genre} — 세계 생성(공유 1회)...(느림)", flush=True)
    st, _ = svc0.create_project(seed.model_copy(deep=True))
    print(f"  세계='{st.world.title}' 엔티티 {len(st.world.entities)} | 본문모델={s0.gen_model} | repo={repo.dir.parent}", flush=True)

    for arm, flag in (("off", False), ("on", True)):
        cp = st.model_copy(deep=True); cp.id = st.id + arm
        svc0.repo.save(cp)
        _run_arm(s0, repo, seed_key, cp.id, arm, flag)
    return 0


def resume(seed_key: str, repo_dir: str) -> int:
    """킬/중단된 gen 재개 — 같은 temp repo 의 저장 상태에서 미완 arm 만 이어 생성(세계 공유 통제 보존)."""
    s0 = _base_settings()
    repo = FilesystemProjectRepository(Path(repo_dir))
    ids = {p.stem for p in repo.dir.glob("*.json") if ".rag." not in p.name}
    if not ids:
        print(f"repo 에 프로젝트 없음: {repo.dir}"); return 1
    base = min(ids, key=len)
    print(f"=== [{seed_key}] resume — repo={repo.dir.parent} base={base}", flush=True)
    for arm, flag in (("off", False), ("on", True)):
        if (OUT / f"c1_{seed_key}_{arm}.json").exists():
            print(f"[{seed_key}/{arm}] 리포트 JSON 존재 — 스킵", flush=True)
            continue
        pid = base + arm
        if pid not in ids:
            print(f"[{seed_key}/{arm}] 저장 상태 없음({pid}) — 스킵", flush=True)
            continue
        _run_arm(s0, repo, seed_key, pid, arm, flag)
    return 0


def rep16(text: str) -> dict:
    """회차내 16자-gram 반복(D-28 rep_metric 계승) + 길이 정규화(절대 카운트의 길이 교란 보정)."""
    n = 16
    g = Counter(text[i:i + n] for i in range(max(0, len(text) - n)))
    rep = sum(1 for _, c in g.items() if c >= 2)
    raw = text.encode("utf-8")
    return {"rep16": rep, "rep16_per10k": round(rep / max(1, len(text)) * 10000, 2),
            "gzip": round(len(gzip.compress(raw)) / max(1, len(raw)), 3), "len": len(text)}


def metrics(data: dict) -> dict:
    chs = data["chapters"]
    texts = [c["text"] for c in chs]
    joined = "\n\n".join(texts)
    return {"hook_max_run": label_max_run([c["hook_type"] for c in chs]),
            "place_max_run": label_max_run([c["place"] for c in chs]),
            "prose_echo": [round(prose_rehash(b, a), 3) for a, b in zip(texts, texts[1:])],
            "event_echo": [round(event_echo(b["summary"], a["summary"]) , 3) for a, b in zip(chs, chs[1:])],
            **rep16(joined)}


def judge() -> int:
    from novelcopilot.llm.openai_provider import OpenAIProvider
    jp = OpenAIProvider(JUDGE_MODEL, "text-embedding-3-small")
    SYS = ("너는 한국 웹소설 독자다. 같은 작품 설정으로 쓰인 두 연재분(A/B, 각 3화)을 읽고, "
           "독자로서 더 계속 읽고 싶은 쪽을 하나만 골라라. 기준: 전개가 빠르고 늘어지지 않는가, "
           "다음 화를 읽고 싶어지는가. "
           '{"winner":"A" 또는 "B","reason":"한 줄"} JSON만 출력.')
    log = {"judge_model": JUDGE_MODEL, "pairs": JUDGE_PAIRS, "cap": JUDGE_CAP, "rubric": SYS, "seeds": {}}
    verdict_by_seed = {}
    for seed_key in SEEDS:
        arms = {}
        for arm in ("on", "off"):
            p = OUT / f"c1_{seed_key}_{arm}.json"
            if not p.exists():
                print(f"[{seed_key}] {arm} 데이터 없음({p.name}) — 시드 스킵"); break
            arms[arm] = json.loads(p.read_text(encoding="utf-8"))
        if len(arms) < 2 or any(len(d["chapters"]) < N_CH for d in arms.values()):
            verdict_by_seed[seed_key] = "판정 불가(생성 미달)"
            continue

        print(f"\n=== [{seed_key}] {arms['on']['genre']} — 결정론 지표(advisory) ===")
        det = {}
        for arm in ("off", "on"):
            det[arm] = metrics(arms[arm])
            print(f"  {arm.upper():3}: {det[arm]}")
        body = {arm: "\n\n".join(c["text"] for c in arms[arm]["chapters"]) for arm in arms}
        trunc = {arm: len(body[arm]) > JUDGE_CAP for arm in body}
        print(f"  본문: ON {len(body['on'])}자 / OFF {len(body['off'])}자 (cap {JUDGE_CAP}, 절단 {trunc})")

        print(f"[{seed_key}] 블라인드 쌍대 {JUDGE_PAIRS}판(양순서 일치 승만 인정, judge={JUDGE_MODEL})", flush=True)
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
                    reasons.append(str(d.get("reason", ""))[:120])
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
        log["seeds"][seed_key] = {"deterministic": det, "judgments": jlog,
                                  "on_wins": onw, "off_wins": offw, "discord": disc, "verdict": v,
                                  "truncated": trunc, "len": {a: len(body[a]) for a in body}}

    print("\n========== C-1 사전 등록 판정 ==========")
    for k, v in verdict_by_seed.items():
        print(f"  {k}: {v}")
    ups = [k for k, v in verdict_by_seed.items() if v.startswith("ON 우세")]
    downs = [k for k, v in verdict_by_seed.items() if v.startswith("ON 열세")]
    if ups and not downs:
        overall = "일반화 지지 — D-28 약확인 → 확인 승격 근거"
    elif not ups and not downs:
        overall = "일반화 실패 — SL 특이적(kill)"
    else:
        overall = "혼재 — 장르 조건부"
    print(f"  종합: {overall}")
    log["overall"] = overall
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "c1_judge_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  심사 로그 → {OUT / 'c1_judge_log.json'}")
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
