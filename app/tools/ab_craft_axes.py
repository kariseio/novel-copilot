# -*- coding: utf-8 -*-
"""C-2 A/B — 구조 craft 축 탐색(순차·kill criteria 사전 등록. 이 파일 커밋=등록).

배경: craft/페이싱은 3장르 확인된 유일 프로즈 레버(D-28 4:2 → C-1 무협·로판 3:0/3:0).
문체축은 5연속 null 로 사전 제외 — **구조 craft 만** 순차 탐색한다.
IN-8 실측(2026-07-03): 끝긴장 갭률 50%(최대 긴장이 끝 분위 아님, 잔잔물 58.3%) — 축1(엔딩 훅)의 근거.

축 순서(사전 등록 — 순차 실행·조기 중단):
  1. ending  엔딩 훅   — 계획된 사건 안에서 장면 순서/절단점만 조정해 최대 긴장 지점을 회차 끝에 배치
  2. scenes  장면 구성 — 장소/시점이 다른 2~3개 장면, 장면 목적 완수 즉시 전환(긍정형)
  3. beats   감정 비트 — 사건→반응→결정 리듬(사건 직후 내면 반응 비트 명시 배치)
(대사 밀도 축은 후순위 — 3축 결과 후 별도 판단)

개입 방식: config 토글이 아니라 *실험 하네스에서 draft 단계 구조 지시 주입*(엔진 코드 무변경).
  양팔 모두 production 기본(craft_progress ON=_CRAFT_PROGRESS) 위에서, ON 팔만 축 지시를 덧붙인다
  → 측정 대상은 '축 지시의 증분 효과'. 이긴 축만 후속 티켓으로 엔진화.

kill criteria(사전 등록):
  - 축마다 시드 2종(현대판타지 modern·잔잔 학원물 school — IN-8 갭 최다 장르 포함) × {ON,OFF} × 3화.
  - cross-vendor 블라인드 쌍대 3판, 양순서 일치 승만 인정. 시드 판정: ON 일치승>OFF 일치승 → ON 우세.
  - 두 시드 모두 'ON 우세' 실패(무승부 이하) → 그 축 즉시 폐기(kill).
  - **2축 연속 폐기 → 탐색 전체 중단**(그 시점까지의 결과로 리포트). 축 생존 시 다음 축으로.
  - 보조 지표는 advisory — 판정을 뒤집지 않는다. 축1 한정: IN-8과 동일한 4분위 끝긴장 갭 ON/OFF 비교.

방법(측정 4원칙 — C-1 계승):
  1) 중립 루브릭 — 훅/긴장/craft 등 내부 메커니즘 단어를 심사문에 넣지 않는다(결과 차원만:
     전개 몰입·회차 마무리 인상·다음 화 욕구).
  2) gen ≠ judge — 생성 config 기본(anthropic), 심사 openai(gpt-5.2-chat-latest).
  3) 최종 산출(프로즈) 측정 — 회차 본문 자체를 심사.
  4) 블라인드·위치편향 상쇄 — 양순서 2콜 일치 승만 인정. temperature 0(사전 등록 — C-1의 0.3에서 변경).
  C-1 대비 명시 변경 2건(생성 전 등록): 심사 cap 20000→30000자(축1은 '회차 끝'이 관건 — 3화 말미 절단이
  측정 대상 자체를 지우는 것 방지), 심사 temperature 0.3→0(티켓 등록값).

비용 통제: 축당 12화(시드2×팔2×3화) + 심사 12콜(시드2×3판×양순서) ≤ 3축 36화.
reader_desk/claim_audit(본문 무영향 advisory 콜)는 양팔 동일 off. 세계는 시드당 1회 생성해
전 축이 공유(팔 상태는 세계 스냅샷 clone — 축·팔 간 독립, 세계 재료 통제 동일).

실행(app/ 에서 — 킬돼도 회차는 FINALIZED 단위 영속, 같은 명령 재실행=이어 생성):
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_craft_axes.py gen <ending|scenes|beats> <modern|school>
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_craft_axes.py judge <ending|scenes|beats>
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_craft_axes.py tension   # 축1 advisory(IN-8 재측정)
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_craft_axes.py status
"""
from __future__ import annotations
import sys, json, gzip, tempfile
from collections import Counter
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.domain.types import ChapterStatus
from novelcopilot.engine.harness import _CRAFT_PROGRESS
from novelcopilot.engine.pacing import label_max_run, prose_rehash, event_echo
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService

OUT = Path(__file__).resolve().parent / "reports"
WS = Path(tempfile.gettempdir()) / "c2_craft"   # 자체 임시 작업공간(app/data 라이브 불가침) — 고정 경로=resume 가능
N_CH = 3
JUDGE_PAIRS = 3
JUDGE_CAP = 30000        # C-1(20000)에서 상향 — 3화(~24k자) 무절단 목표(축1은 3화 말미가 측정 대상)
JUDGE_TEMP = 0.0         # 티켓 등록값(C-1은 0.3)
JUDGE_MODEL = "gpt-5.2-chat-latest"   # 생성(anthropic)과 다른 벤더 — gen≠judge

AXIS_ORDER = ["ending", "scenes", "beats"]
# 축 지시(draft/continue 프롬프트 말미 — production _CRAFT_PROGRESS 뒤에 덧붙음). 긍정 구조지시만(부정명령 0 —
# pink-elephant 소스차단), 새 사건 발명 없이 '배치/구성/리듬'만 지시(블랭킷 문체 지시 아님 — 회차 구조 수준 1건).
AXES = {
    "ending": ("\n\n[장면 배치] 이 회차에 계획된 사건의 범위 안에서 장면 순서와 끝 지점을 조정해, "
               "이 회차에서 가장 팽팽한 순간이 본문의 마지막 장면에 오게 하라. "
               "그 순간이 정점에 닿는 지점에서 회차를 끝내고, 사건의 뒷정리와 여운은 다음 화 첫 장면의 몫으로 남겨라."),
    "scenes": ("\n\n[장면 구성] 이 회차를 장소나 시점이 서로 다른 2~3개의 장면으로 구성하라. "
               "각 장면은 그 장면이 맡은 목적(드러낼 사실·벌어질 사건)을 완수하는 즉시 다음 장면으로 전환하고, "
               "전환한 곳에서는 새 장소·새 시점이 주는 재료로 이야기를 이어가라."),
    "beats": ("\n\n[감정 비트] 사건이 벌어질 때마다 그 직후에 인물이 겪는 내면 반응(감각·감정·판단)을 "
              "한 비트씩 배치하고, 그 반응이 인물의 다음 행동을 결정하게 하라 — "
              "사건→반응→결정의 리듬으로 회차를 전개하라."),
}

SEEDS = {
    # 현대판타지 — ab_obsession_worldgen.py 시드 재사용(도구 간 시드 정합)
    "modern": ProjectSeed(
        title="", genre="현대 판타지", tone="다크하고 비장하지만 통쾌한 성장물",
        premise=("게이트가 현실과 던전을 잇고 헌터가 몬스터를 사냥하는 현대. 헌터는 각성 시 등급(E~S)이 고정되어 더는 강해지지 않는다. "
                 "인류 최약체 E급 헌터가 병든 가족을 위해 저급 던전을 전전하다, 죽음의 이중 던전에서 그만이 보는 '시스템'을 각성해 "
                 "무한 성장하는 유일한 '플레이어'가 된다."),
        protagonist_hint="인류 최약체 E급 헌터, 병든 가족 부양, 죽음의 문턱에서 무한성장 시스템 각성", target_chapters=30),
    # 잔잔 학원물 — ab_genres.py 시드 재사용(IN-8 갭 최다 장르: 잔잔물 58.3%)
    "school": ProjectSeed(
        title="", genre="학원물(잔잔한 일상)", tone="잔잔하고 따뜻한 일상, 섬세한 감정선과 성장",
        premise=("평범한 고등학교의 2학년 교실. 봄에 전학 온 주인공이 낯선 학교와 반에 서서히 스며든다. "
                 "특별한 능력도 거대한 사건도 없이 동아리·점심시간·축제 준비·진로 고민·서툰 첫 우정과 짝사랑 같은 "
                 "일상의 결을 따라가며, 작은 오해와 화해 속에서 천천히 자란다."),
        protagonist_hint="봄 전학생, 능력·초자연 전혀 없는 평범한 고교생, 일상의 서툰 감정과 관계", target_chapters=30),
}


def _base_settings():
    s = get_settings().model_copy(update={"reader_desk": False, "claim_audit": False})
    assert getattr(s, "craft_progress", True), "production 기본(craft ON)이 전제 — OFF 팔=production 그대로"
    return s


def _rep_path(axis: str, seed_key: str, arm: str) -> Path:
    return OUT / f"c2_{axis}_{seed_key}_{arm}.json"


def _dump(axis: str, seed_key: str, arm: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _rep_path(axis, seed_key, arm).write_text(
        json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / f"c2_{axis}_{seed_key}_{arm}.txt").write_text(
        "\n\n".join(c["text"] for c in data["chapters"]), encoding="utf-8")


def _seed_repo(seed_key: str):
    """시드 전용 워크스페이스 repo + 공유 세계(manifest) — 없으면 생성, 있으면 재사용(전 축 공유)."""
    ws = WS / seed_key
    ws.mkdir(parents=True, exist_ok=True)
    repo = FilesystemProjectRepository(ws)
    s0 = _base_settings()
    svc = CopilotService(s0, repo)
    mf = ws / "manifest.json"
    if mf.exists():
        base = json.loads(mf.read_text(encoding="utf-8"))["base_pid"]
        st = repo.get(base)
        assert st is not None, f"manifest 의 base 상태 없음: {base}"
        print(f"[{seed_key}] 공유 세계 재사용 — '{st.world.title}' (base={base})", flush=True)
        return s0, svc, st
    seed = SEEDS[seed_key]
    print(f"=== [{seed_key}] {seed.genre} — 공유 세계 생성(전 축 공유, 1회)...(느림)", flush=True)
    st, _ = svc.create_project(seed.model_copy(deep=True))
    mf.write_text(json.dumps({"base_pid": st.id, "genre": seed.genre}, ensure_ascii=False), encoding="utf-8")
    print(f"  세계='{st.world.title}' 엔티티 {len(st.world.entities)} | 본문모델={s0.gen_model} | ws={ws}", flush=True)
    return s0, svc, st


def _run_arm(svc, axis: str, seed_key: str, pid: str, arm: str) -> None:
    """한 arm 을 pid 저장 상태에서 N_CH FINALIZED 까지 이어 생성(멱등). 축 지시는 매 시도 전 재주입
    (실패 롤백이 세션을 evict→재수화하므로 — 킬/재개에도 조건 동일 보장)."""
    inject = AXES[axis] if arm == "on" else ""
    st = svc.repo.get(pid)
    have = len([c for c in st.chapters if c.status == ChapterStatus.FINALIZED])
    print(f"[{axis}/{seed_key}/{arm}] {N_CH}회차 집필(기존 {have}화, 축지시={'주입' if inject else '없음'})", flush=True)
    esc = attempts = 0
    while attempts < (N_CH - have) + 3:   # ESCALATED(비영속) 재시도 여유분 +3
        done = [c for c in svc.repo.get(pid).chapters if c.status == ChapterStatus.FINALIZED]
        if len(done) >= N_CH:
            break
        attempts += 1
        sess, _ = svc.get_session(pid)                     # 매 시도 재주입(evict 대비 멱등 — 명시 대입)
        sess.bundle.generator.craft_block = _CRAFT_PROGRESS + inject
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
    data = {"axis": axis, "seed": seed_key, "genre": st.seed.genre, "arm": arm,
            "axis_inject": inject, "world_title": st.world.title, "gen_model": _base_settings().gen_model,
            "attempts": attempts, "escalated": esc, "usage_total": getattr(st, "usage_total", {}),
            "chapters": [{"chapter": c.chapter, "title": c.title, "len": len(c.text),
                          "hook_type": c.hook_type, "place": c.place, "time_advance": c.time_advance,
                          "summary": c.summary, "text": c.text} for c in fin]}
    _dump(axis, seed_key, arm, data)
    print(f"  [{axis}/{seed_key}/{arm}] 완료 {len(fin)}화(escalated 재시도 {esc}) → {_rep_path(axis, seed_key, arm).name}", flush=True)
    if len(fin) < N_CH:
        print(f"  !! [{axis}/{seed_key}/{arm}] {N_CH}화 미달 — judge 에서 이 시드는 판정 불가 처리", flush=True)


def gen(axis: str, seed_key: str) -> int:
    s0, svc, base_st = _seed_repo(seed_key)
    for arm in ("off", "on"):
        if _rep_path(axis, seed_key, arm).exists():
            print(f"[{axis}/{seed_key}/{arm}] 리포트 JSON 존재 — 스킵", flush=True)
            continue
        pid = f"{base_st.id}-{axis}-{arm}"
        if svc.repo.get(pid) is None:                      # 팔 상태 없으면 세계 스냅샷 clone(있으면 이어 생성)
            cp = base_st.model_copy(deep=True); cp.id = pid
            svc.repo.save(cp)
        _run_arm(svc, axis, seed_key, pid, arm)
    return 0


# ---- 결정론 advisory(C-1 계승 — 판정 불변) ----
def rep16(text: str) -> dict:
    n = 16
    g = Counter(text[i:i + n] for i in range(max(0, len(text) - n)))
    rep = sum(1 for _, c in g.items() if c >= 2)
    raw = text.encode("utf-8")
    return {"rep16": rep, "rep16_per10k": round(rep / max(1, len(text)) * 10000, 2),
            "gzip": round(len(gzip.compress(raw)) / max(1, len(raw)), 3), "len": len(text)}


def metrics(data: dict) -> dict:
    chs = data["chapters"]
    texts = [c["text"] for c in chs]
    return {"hook_max_run": label_max_run([c["hook_type"] for c in chs]),
            "place_max_run": label_max_run([c["place"] for c in chs]),
            "prose_echo": [round(prose_rehash(b, a), 3) for a, b in zip(texts, texts[1:])],
            "event_echo": [round(event_echo(b["summary"], a["summary"]), 3) for a, b in zip(chs, chs[1:])],
            **rep16("\n\n".join(texts))}


def _load_axis(axis: str):
    """축의 4개 리포트 로드 — {seed: {arm: data}} (미달 시 해당 시드 None)."""
    out = {}
    for seed_key in SEEDS:
        arms = {}
        for arm in ("on", "off"):
            p = _rep_path(axis, seed_key, arm)
            if p.exists():
                arms[arm] = json.loads(p.read_text(encoding="utf-8"))
        if len(arms) == 2 and all(len(d["chapters"]) >= N_CH for d in arms.values()):
            out[seed_key] = arms
        else:
            out[seed_key] = None
    return out


def _state_load() -> dict:
    p = OUT / "c2_axes_state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"order": AXIS_ORDER, "verdicts": {}}


def _state_save(stt: dict) -> None:
    # 사전 등록 중단 규칙 재계산: 폐기(kill)가 실행 순서상 2축 연속이면 전체 중단
    kills = [stt["verdicts"].get(a, {}).get("killed") for a in AXIS_ORDER]
    stt["stop_all"] = any(kills[i] and kills[i + 1] for i in range(len(kills) - 1))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "c2_axes_state.json").write_text(json.dumps(stt, ensure_ascii=False, indent=1), encoding="utf-8")


def judge(axis: str) -> int:
    from novelcopilot.llm.openai_provider import OpenAIProvider
    jp = OpenAIProvider(JUDGE_MODEL, "text-embedding-3-small")
    # 중립 루브릭 — 내부 메커니즘 단어(훅/긴장/craft/장면/비트) 0. 결과 차원만.
    SYS = ("너는 한국 웹소설 독자다. 같은 작품 설정으로 쓰인 두 연재분(A/B, 각 3화)을 읽고, "
           "독자로서 더 계속 읽고 싶은 쪽을 하나만 골라라. 기준: 전개에 몰입되는가, "
           "각 회차의 마무리가 인상적인가, 다음 화를 읽고 싶어지는가. "
           '{"winner":"A" 또는 "B","reason":"한 줄"} JSON만 출력.')
    data = _load_axis(axis)
    log = {"axis": axis, "judge_model": JUDGE_MODEL, "pairs": JUDGE_PAIRS, "cap": JUDGE_CAP,
           "temperature": JUDGE_TEMP, "rubric": SYS, "seeds": {}}
    verdict_by_seed = {}
    for seed_key, arms in data.items():
        if arms is None:
            verdict_by_seed[seed_key] = "판정 불가(생성 미달)"
            continue
        print(f"\n=== [{axis}/{seed_key}] {arms['on']['genre']} — 결정론 지표(advisory) ===")
        det = {}
        for arm in ("off", "on"):
            det[arm] = metrics(arms[arm])
            print(f"  {arm.upper():3}: {det[arm]}")
        body = {arm: "\n\n".join(c["text"] for c in arms[arm]["chapters"][:N_CH]) for arm in arms}
        trunc = {arm: len(body[arm]) > JUDGE_CAP for arm in body}
        print(f"  본문: ON {len(body['on'])}자 / OFF {len(body['off'])}자 (cap {JUDGE_CAP}, 절단 {trunc})")

        print(f"[{axis}/{seed_key}] 블라인드 쌍대 {JUDGE_PAIRS}판(양순서 일치 승만, judge={JUDGE_MODEL}, T={JUDGE_TEMP})", flush=True)
        onw = offw = disc = 0
        jlog = []
        for p_i in range(JUDGE_PAIRS):
            verdicts, reasons = [], []
            for a_arm, b_arm in (("on", "off"), ("off", "on")):
                msg = f"[A]\n{body[a_arm][:JUDGE_CAP]}\n\n[B]\n{body[b_arm][:JUDGE_CAP]}"
                try:
                    d = jp.chat_json([{"role": "system", "content": SYS},
                                      {"role": "user", "content": msg}], temperature=JUDGE_TEMP, max_tokens=400)
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

    print(f"\n========== C-2 [{axis}] 사전 등록 판정 ==========")
    for k, v in verdict_by_seed.items():
        print(f"  {k}: {v}")
    ups = [k for k, v in verdict_by_seed.items() if v.startswith("ON 우세")]
    downs = [k for k, v in verdict_by_seed.items() if v.startswith("ON 열세")]
    killed = not ups                       # 두 시드 모두 우세 실패(무승부 이하) → 즉시 폐기
    if killed:
        overall = "폐기(kill) — 두 시드 모두 ON 우세 실패"
    elif downs:
        overall = "생존(혼재 — 장르 조건부: 우세·열세 병존)"
    elif len(ups) == len([k for k in verdict_by_seed]):
        overall = "생존(승 — 양시드 ON 우세)"
    else:
        overall = "생존(부분 우세 — 1시드 ON 우세·열세 0)"
    print(f"  종합: {overall}")
    log["overall"] = overall; log["killed"] = killed
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"c2_{axis}_judge_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    stt = _state_load()
    stt["verdicts"][axis] = {"killed": killed, "overall": overall, "by_seed": verdict_by_seed}
    _state_save(stt)
    if stt.get("stop_all"):
        print("  !! 2축 연속 폐기 — 사전 등록 규칙에 따라 탐색 전체 중단")
    print(f"  심사 로그 → c2_{axis}_judge_log.json | 상태 → c2_axes_state.json")
    return 0


# ---- 축1 advisory: IN-8 4분위 끝긴장 갭 재측정(ON/OFF) ----
def _quarters(text: str) -> list[str]:
    """IN-8 방법 재현 — 4등분 지점을 근처 줄바꿈에 스냅(문장 파손 방지), 없으면 정확 4등분."""
    n = len(text)
    cuts = []
    for q in (1, 2, 3):
        ideal = n * q // 4
        lo = max(0, ideal - 350)
        window = text[lo:min(n, ideal + 350)]
        # 창 안 줄바꿈 중 이상적 4등분점에 '가장 가까운' 것으로 스냅(마지막 줄바꿈 선택은 +350 편향)
        nls = [lo + i + 1 for i, ch in enumerate(window) if ch == "\n"]
        cuts.append(min(nls, key=lambda p: abs(p - ideal)) if nls else ideal)
    cuts = sorted(set(cuts))
    if len(cuts) != 3:
        cuts = [n // 4, n // 2, 3 * n // 4]
    a, b, c = cuts
    return [text[:a], text[a:b], text[b:c], text[c:]]


def tension() -> int:
    """축1(ending) 한정 advisory — 회차당 1콜, blind 4분위(최고 긴장 구간 1~4 + 끝 긴장 1~5). IN-8 동일."""
    from novelcopilot.llm.openai_provider import OpenAIProvider
    jp = OpenAIProvider(JUDGE_MODEL, "text-embedding-3-small")
    SYS = ("소설 한 회차를 순서대로 4개 구간([1]~[4])으로 나눠 제시한다. 전체를 읽고 JSON만 출력: "
           '{"max_tension_segment":1~4 정수(긴장이 가장 높은 구간 번호),'
           '"max_tension_event":"그 구간의 긴장이 가장 높은 사건 한 줄",'
           '"end_tension":1~5 정수(마지막 장면의 긴장도),"ending_event":"마지막 장면의 사건 한 줄"}')
    data = _load_axis("ending")
    rows, agg = [], {"on": [], "off": []}
    for seed_key, arms in data.items():
        if arms is None:
            print(f"[{seed_key}] 데이터 미달 — 스킵"); continue
        for arm in ("off", "on"):
            for c in arms[arm]["chapters"][:N_CH]:
                segs = _quarters(c["text"])
                msg = "\n\n".join(f"[{i+1}]\n{s}" for i, s in enumerate(segs))
                try:
                    d = jp.chat_json([{"role": "system", "content": SYS},
                                      {"role": "user", "content": msg}], temperature=JUDGE_TEMP, max_tokens=400)
                    seg = int(d.get("max_tension_segment", 0)); end = int(d.get("end_tension", 0))
                except Exception as e:
                    print(f"  [{seed_key}/{arm}] ch{c['chapter']} 실패: {str(e)[:100]}"); continue
                gap = seg != 4
                rows.append({"seed": seed_key, "arm": arm, "chapter": c["chapter"],
                             "max_tension_segment": seg, "end_tension": end, "gap": gap,
                             "max_tension_event": str(d.get("max_tension_event", ""))[:160],
                             "ending_event": str(d.get("ending_event", ""))[:160],
                             "segment_lengths": [len(s) for s in segs]})
                agg[arm].append((gap, end))
                print(f"  [{seed_key}/{arm}] ch{c['chapter']}: 최고긴장={seg}분위 끝긴장={end} gap={gap}", flush=True)
    summ = {}
    for arm in ("off", "on"):
        xs = agg[arm]
        summ[arm] = {"n": len(xs), "gap_rate": round(sum(1 for g, _ in xs if g) / max(1, len(xs)), 4),
                     "end_tension_mean": round(sum(e for _, e in xs) / max(1, len(xs)), 3)}
    rep = {"report": "C-2 축1(ending) advisory — IN-8 4분위 끝긴장 갭 ON/OFF", "judge_model": JUDGE_MODEL,
           "method": "IN-8 동일(blind 4분위·회차 1콜)", "summary": summ, "rows": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "c2_ending_tension.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[advisory] 갭률 OFF {summ['off']['gap_rate']} (n={summ['off']['n']}) → "
          f"ON {summ['on']['gap_rate']} (n={summ['on']['n']}) | "
          f"끝긴장평균 OFF {summ['off']['end_tension_mean']} → ON {summ['on']['end_tension_mean']}")
    print("  → c2_ending_tension.json (판정 불변 advisory)")
    return 0


def status() -> int:
    stt = _state_load()
    print(json.dumps(stt, ensure_ascii=False, indent=1))
    for axis in AXIS_ORDER:
        for seed_key in SEEDS:
            for arm in ("off", "on"):
                p = _rep_path(axis, seed_key, arm)
                if p.exists():
                    d = json.loads(p.read_text(encoding="utf-8"))
                    print(f"  {p.name}: {len(d['chapters'])}화")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("gen", "judge", "tension", "status"):
        print(__doc__); return 2
    if args[0] == "gen":
        if len(args) < 3 or args[1] not in AXES or args[2] not in SEEDS:
            print(f"gen <{'|'.join(AXES)}> <{'|'.join(SEEDS)}>"); return 2
        return gen(args[1], args[2])
    if args[0] == "judge":
        if len(args) < 2 or args[1] not in AXES:
            print(f"judge <{'|'.join(AXES)}>"); return 2
        return judge(args[1])
    if args[0] == "tension":
        return tension()
    return status()


if __name__ == "__main__":
    sys.exit(main())
