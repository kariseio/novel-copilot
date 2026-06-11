# -*- coding: utf-8 -*-
"""실사용자 페르소나 E2E 시뮬레이션 — 실제 서비스(HTTP API)를 작가처럼 사용해 20화 연재.

페르소나: 한도윤(29) — 밀리터리/아포칼립스 SF 덕후, 문피아 연재 지망생.
  세계관 설정 집착(설정집부터 다듬음), 건조한 하드SF 문체 선호, 연재 훅 중시, 복선은 슬로우번.
사용 패턴(실서비스 기능 그대로):
  ① 시드 한 줄로 작품 생성 → ② 월드빌딩 대화로 세계 심화 → ③ 설정집 점검·캐논 박기
  → ④ 문체/정책 조정 → ⑤ 회차 생성(SSE) ×20, 중간중간 빨간 펜 조향 → 로그 저장.
실행: PYTHONPATH=app python tools/sim_persona.py
"""
from __future__ import annotations
import json
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000/api"
LOG: dict = {"persona": "한도윤(29) 밀리터리/아포칼립스 SF, 문피아 지망", "actions": [], "chapters": []}


def act(kind, **kw):
    LOG["actions"].append({"t": time.strftime("%H:%M:%S"), "kind": kind, **kw})
    print(f"[{time.strftime('%H:%M:%S')}] {kind}: {json.dumps(kw, ensure_ascii=False)[:170]}")


def sse_generate(pid: str, directive: str = "") -> dict:
    """실서비스 SSE 회차 생성 — 이벤트 스트림 소비 후 complete/failed payload 반환."""
    params = {"directive": directive} if directive else {}
    out = {"events": 0}
    with requests.get(f"{BASE}/projects/{pid}/generate", params=params, stream=True, timeout=1800) as r:
        ev = None
        for raw in r.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw.startswith("event: "):
                ev = raw[7:].strip()
            elif raw.startswith("data: ") and ev:
                out["events"] += 1
                if ev in ("complete", "failed"):
                    out["final"] = ev
                    out["data"] = json.loads(raw[6:])
                    return out
    out["final"] = "stream_end"
    return out


def main():
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--pilot":   # fail-fast: N화만 생성+평가 후 종료(라운드당 비용 1/6)
        n = int(sys.argv[2])
        global PILOT_N
        PILOT_N = n
    if len(sys.argv) > 2 and sys.argv[1] == "--resume":   # 기존 작품 이어서 연재(중단 지점부터)
        pid = sys.argv[2]
        LOG["pid"] = pid
        cur = requests.get(f"{BASE}/projects/{pid}", timeout=120).json()["current_chapter"]
        act("resume", pid=pid, from_chapter=cur + 1)
        run_serial(pid, start_ch=cur, t0=time.time())
        return
    # ① 작품 생성 — 시드 한 줄(페르소나의 컨셉)
    seed = {
        "title": "",
        "genre": "SF 포스트아포칼립스",
        "tone": "",   # 비움 — 장르에 맞게 AI 가 정하는지 확인(페르소나는 귀찮아서 안 씀)
        "premise": ("초지능 AI 전쟁으로 문명이 멸망한 뒤, 연산자원(GPU 코어)이 화폐이자 무기가 된 세계. "
                    "재밍과 EMP 가 최강의 병기가 됐고, 멸망 전의 기술은 발굴되는 '고대 유물'이 되었다. "
                    "폐허를 뒤지는 스캐빈저가 아직 살아 있는 고대 AI 코어를 주우면서 이야기가 시작된다."),
        "protagonist_hint": "전직 통신병 출신 스캐빈저. 말수가 적고 계산적이지만 동료는 버리지 못한다.",
        "target_chapters": 20,
    }
    t0 = time.time()
    r = None
    for attempt in range(3):                      # 일시적 worldgen 출력 불량 흡수(실작가도 새로고침 누름)
        r = requests.post(f"{BASE}/projects", json=seed, timeout=600)
        if r.status_code == 200:
            break
        act("create_retry", attempt=attempt + 1, status=r.status_code)
        time.sleep(3)
    r.raise_for_status()
    pid = r.json()["id"]
    world = r.json()["world"]
    LOG["pid"] = pid
    act("create_project", pid=pid, title=world["title"], tone=world.get("tone", ""),
        attrs=[a["key"] for a in world.get("attributes", [])],
        entities=[e["name"] for e in world.get("entities", [])],
        arcs=len((world.get("spine") or {}).get("arcs", [])),
        persona=world.get("style", {}).get("system_persona", "")[:60],
        ending_hook=world.get("style", {}).get("ending_hook"))

    # ② 월드빌딩 대화 — 세계관 덕후의 심화 2턴
    for msg in [
        "재밍과 EMP가 왜 '최강의 무기'가 됐는지 구체화하자. 증강병·드론·자동포탑 전부 연산 의존이라 "
        "전자전 앞에 무력해지는 구조로. 그리고 GPU 코어를 독점하는 세력 하나(집정청)와 "
        "재머를 신봉하는 유목 약탈단(정전파)을 추가해줘.",
        "주인공과 같은 폐허를 도는 라이벌 스캐빈저를 한 명 추가하고, 주인공이 주운 고대 AI 코어에는 "
        "멸망 전쟁의 진실에 대한 기록이 잠겨 있다는 설정을 설정집에 넣자. 코어는 천천히 깨어나는 걸로.",
    ]:
        wr = requests.post(f"{BASE}/projects/{pid}/worldgen", json={"message": msg}, timeout=600).json()
        act("worldgen_turn", applied=len(wr.get("applied", [])), blocked=len(wr.get("blocked", [])),
            kinds=[a.get("kind") for a in wr.get("applied", [])])

    # ③ 설정집 점검 — 마음에 드는 세계규칙 하나를 캐논으로 박기
    bible = requests.get(f"{BASE}/projects/{pid}/bible", timeout=120).json()
    act("bible_review", entries=len(bible["entries"]))
    target = next((e for e in bible["entries"]
                   if e["category"] in ("taboo_worldrule", "tech_system", "power_system")), None)
    if target:
        pr = requests.post(f"{BASE}/projects/{pid}/bible/{target['entry_id']}/promote", timeout=120).json()
        act("promote_bible", title=target["title"], ok=pr.get("promoted"))

    # ④ 문체/정책 — 문피아 표준 분량 + 연재 훅 유지 + 복선 슬로우번(gentle 기본 확인)
    st = requests.put(f"{BASE}/projects/{pid}/style",
                      json={"target_chars_per_chapter": 5300, "ending_hook": "cliffhanger",
                            "plant_reminder": "gentle"}, timeout=120).json()
    act("style_policy", chars=5300, hook=st["style"]["ending_hook"], plant=st["plant_reminder"])

    # ⑤ 연재 20화 — 중간중간 빨간 펜(실작가 조향 패턴)
    run_serial(pid, start_ch=0, t0=t0, max_ch=globals().get('PILOT_N', 20))


def eval_chapter(text: str, ch: int, roster: set | None = None) -> dict:
    """회차별 즉시 평가(편집자 미니 루브릭, 1콜) — 5축 1-10 + 결함 + 재생성 권고."""
    from novelcopilot.config import get_settings
    from novelcopilot.llm.factory import create_provider
    global _EVAL_PROVIDER
    if "_EVAL_PROVIDER" not in globals():
        _EVAL_PROVIDER = create_provider(get_settings())
    try:
        r = _EVAL_PROVIDER.chat_json(
            [{"role": "system", "content":
              "냉정한 웹소설 편집자. 이 회차를 5축(흡인력/문체조판/세계관구현/내적일관성/진행감) 각 1~10으로 채점. "
              "메타텍스트 누출·토막행갈이·수치모순·반복대사·제자리걸음이 보이면 defects 에 구체적으로. "
              'JSON: {"scores":{"hook":0,"style":0,"world":0,"consistency":0,"progress":0},'
              '"defects":["..."],"retry":false,"fix_directive":"재생성 시 지시 한 줄"}'},
             {"role": "user", "content": f"[{ch}화 본문]\n{text[:9000]}"}],
            temperature=0.0, max_tokens=2500)
        sc = r.get("scores", {})
        # 심판 정렬: LLM 심판의 맹점(자기 틱·조판·누출)은 결정론 지표가 점수 상한을 강제(Goodhart 차단)
        from novelcopilot.engine.quality_gates import chapter_quality_report
        det = chapter_quality_report(text, [], roster=roster)
        if det["tics"]:
            sc["style"] = min(sc.get("style", 10), 5)
            r.setdefault("defects", []).insert(0, f"[결정론] 틱 과용 {det['tics'][:3]}")
        if det["short_line_ratio"] > 0.15:
            sc["style"] = min(sc.get("style", 10), 4)
        if det["directive_leak"]:
            sc["consistency"] = min(sc.get("consistency", 10), 3)
        r["det"] = {k: det[k] for k in ("short_line_ratio", "tense_leak", "hook_sim")}
        r["avg"] = round(sum(sc.values()) / max(1, len(sc)), 1)
        return r
    except Exception as e:
        return {"avg": -1, "defects": [f"eval 실패 {e}"], "retry": False, "fix_directive": ""}


def run_serial(pid: str, start_ch: int, t0: float, max_ch: int = 20):
    directives = {
        5: "라이벌을 너무 일찍 퇴장시키지 마라. 적대와 협력 사이의 긴장을 유지할 것.",
        9: "전투 묘사 비중을 줄이고, 폐허에서의 생존 일상(물·배터리·식량 계산)의 디테일을 늘려라.",
        14: "고대 AI 코어가 잠금 해제되기 시작한다. 멸망 전쟁의 진실에 대한 복선을 회수하기 시작하라.",
    }
    ch = start_ch
    fails = 0
    run_serial.pending_fix = ""
    try:    # 인명 roster(틱 오탐 방지) — 온톨로지 스냅샷에서 1회
        snap = requests.get(f"{BASE}/projects/{pid}/ontology", timeout=120).json()
        roster = {c["name"] for c in snap.get("characters", [])}
    except Exception:
        roster = set()
    while ch < max_ch and fails < 3:
        nxt = ch + 1
        d = directives.get(nxt, "")
        if run_serial.pending_fix:   # 직전 화 평가의 교정 지시 전진 반영
            d = (d + " " if d else "") + f"[품질 교정] {run_serial.pending_fix}"
            run_serial.pending_fix = ""
        if d:
            act("red_pen", chapter=nxt, directive=d[:80])
        t1 = time.time()
        res = sse_generate(pid, d)
        data = res.get("data", {})
        if res.get("final") == "failed":
            fails += 1
            act("generate_failed", chapter=nxt, msg=str(data)[:140], retry=fails)
            time.sleep(5)
            continue
        if data.get("completed"):
            act("completed_early", at=ch)
            break
        rec = data.get("record", {})
        ch = data.get("current_chapter", ch)
        # 회차별 즉시 평가 → 미달(평균<6.5) 시 다음 화에 교정 지시 전진 반영(연재=append-only, 빨간 펜으로 조향)
        if rec.get("status") == "FINALIZED":
            ev = eval_chapter(rec.get("text", ""), rec.get("chapter", 0), roster)
            act("eval", chapter=rec.get("chapter"), avg=ev.get("avg"),
                defects=[d[:50] for d in ev.get("defects", [])][:3])
            if 0 < ev.get("avg", 10) < 6.5 and ev.get("fix_directive"):
                run_serial.pending_fix = ev["fix_directive"]
                act("eval_steer_next", after_chapter=rec.get("chapter"), fix=ev["fix_directive"][:80])
            LOG.setdefault("evals", []).append({"chapter": rec.get("chapter"), **{k: ev.get(k) for k in ("avg", "scores", "defects", "det")}})
        row = {"chapter": rec.get("chapter"), "status": rec.get("status"), "chars": len(rec.get("text", "")),
               "title": rec.get("title", ""), "secs": round(time.time() - t1),
               "init_viol": len(rec.get("initial_violations", [])),
               "final_viol": len(rec.get("final_violations", [])),
               "rounds": len(rec.get("rounds", [])), "drift": rec.get("drift_signals", []),
               "onto_changes": len(rec.get("ontology_changes", [])),
               "usage_by_stage": rec.get("usage_by_stage", {}),
               "usage_total": data.get("usage_total", {})}
        LOG["chapters"].append(row)
        act("chapter", **{k: row[k] for k in ("chapter", "status", "chars", "secs", "init_viol", "final_viol")})
        if rec.get("status") == "ESCALATED":
            # 페르소나 대응: 한 번은 지시로 풀어본다
            act("escalation_response", chapter=nxt)
            res2 = sse_generate(pid, "직전 시도에서 설정 위반이 났다. 확정 설정(사망·속성·관계)을 어기지 말고 다시 써라.")
            d2 = res2.get("data", {})
            rec2 = d2.get("record", {})
            if rec2.get("status") == "FINALIZED":
                ch = d2.get("current_chapter", ch)
                act("escalation_resolved", chapter=ch)
            else:
                fails += 1

    LOG["total_secs"] = round(time.time() - t0)
    out = Path(__file__).parent / "reports" / "sim_persona_log.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(LOG, ensure_ascii=False, indent=2), encoding="utf-8")
    fin = [c for c in LOG["chapters"] if c["status"] == "FINALIZED"]
    print(f"\n===== 시뮬레이션 종료: {ch}화 / {LOG['total_secs']}s =====")
    print(f"FINALIZED {len(fin)} / ESCALATED {len(LOG['chapters']) - len(fin)}")
    print(f"평균 분량 {sum(c['chars'] for c in fin) // max(1, len(fin))}자, 로그: {out}")
    print(f"PID: {pid}")


if __name__ == "__main__":
    main()
