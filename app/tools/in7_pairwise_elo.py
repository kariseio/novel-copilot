# -*- coding: utf-8 -*-
"""IN-7 쌍대 Elo 독자 신호 검증 — 원본+재생성 변종 4 의 쌍대 비교가 회차 품질을 변별하는가.

배경: reader_desk(아첨 스칼라)·G-D 연독 시뮬(72/72 yes 포화)은 변별력 0 — 가설은 '쌍대 비교는 변별한다'.
대상: LR-1 arm1 "봄이 오는 창가"(6cda5ee883e0) 3화·12화 (선정 기준 사전 등록 — in7_pairwise_elo_2026-07.md §1.1).

단계(전부 resume 가능 — 콜 단위 즉시 영속, 재실행 시 스킵):
  verify  : LLM 0콜. 영속 gen_context 대비 재구성 충실도 검증(프리픽스/길이 대조) + 프롬프트 지문 출력.
  gen     : 회차당 변종 4 생성 — production 코드 경로(ChapterGenerator._draft/_continue) 재사용,
            anthropic claude-opus-4-6 · T0.85 · soft 훅 · craft ON. 라이브 프로젝트 불변(읽기 전용).
  elo     : 회차당 후보 5(원본+변종4) 10쌍 × 양순서 = 20게임, openai gpt-5.2-chat-latest · T=0.3
            (C-2 교훈: T=0 위치 편향 실측 → T=0.3 채택, 사전 등록). Elo(초기1000·K32·시드7 셔플) 집계.
  heldout : Elo 최상위 변종 vs 원본 — 별도 프레이밍(유료 연재 심사역)·별도 모델(gpt-5.5) 3판(양순서 일치 승만).
  report  : 워크스페이스 원자료 → 리포트 md §2~5 채움 + in7_raw 요약 json(로컬 보존).

사용: (app/ 에서) PYTHONIOENCODING=utf-8 PYTHONPATH=. python tools/in7_pairwise_elo.py <verify|gen|elo|heldout|report>
워크스페이스: %TEMP%/in7_pairwise (라이브 data/ 불가침)
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import pathlib
import random
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.config import get_settings                      # noqa: E402  (.env 로드 부작용)
from novelcopilot.domain.types import (ContextBoard, SceneSpec, RetrievedItem,   # noqa: E402
                                       OntologyFact, AuthorDirective)
from novelcopilot.domain.world import StyleSpec                   # noqa: E402
from novelcopilot.domain.bible import StoryBible                  # noqa: E402
from novelcopilot.engine.bible_compiler import bible_digest       # noqa: E402
from novelcopilot.engine.harness import ChapterGenerator, sanitize_meta   # noqa: E402
from novelcopilot.engine.correction import trim_dangling          # noqa: E402
from novelcopilot.llm.factory import create_provider              # noqa: E402
from novelcopilot.llm.openai_provider import OpenAIProvider       # noqa: E402

APP = pathlib.Path(__file__).resolve().parents[1]
REPORTS = APP / "tools" / "reports"
WORK = pathlib.Path(tempfile.gettempdir()) / "in7_pairwise"

PID = "6cda5ee883e0"          # 봄이 오는 창가 (LR-1 arm1)
CHAPTERS = [3, 12]            # 사전 등록 선정(리포트 §1.1)
N_VARIANTS = 4
TRUNC = 15000                 # 심사 입력 절단(G-D 동일)
ELO_JUDGE = "gpt-5.2-chat-latest"
HELDOUT_JUDGE = "gpt-5.5"
SEED = 7                      # Elo 게임 처리 순서 셔플(결정론 재현)


# ---------- 공용 ----------

def _load_project() -> dict:
    return json.loads((APP / "data" / "projects" / f"{PID}.json").read_text(encoding="utf-8"))


def _chapter(p: dict, n: int) -> dict:
    return next(c for c in p["chapters"] if c["chapter"] == n)


def _prior(p: dict, n: int) -> list[dict]:
    return sorted([c for c in p["chapters"] if c["chapter"] < n and c.get("status") == "FINALIZED"],
                  key=lambda c: c["chapter"])


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _append_jsonl(path: pathlib.Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class _Bus:                                   # ChapterGenerator 주입용 no-op(사용 안 됨 — _draft/_continue 는 emit 없음)
    def emit(self, *a, **k):
        pass


class _Recorder:
    """provider 프록시 — 마지막 chat 프롬프트를 기록(프롬프트 지문 감사용). last_truncated 는 inner 위임."""

    def __init__(self, inner):
        self._inner = inner
        self.last_messages = None
        self.last_kwargs = None

    def chat(self, messages, **kw):
        self.last_messages = messages
        self.last_kwargs = dict(kw)
        return self._inner.chat(messages, **kw)

    @property
    def last_truncated(self):
        return self._inner.last_truncated

    @last_truncated.setter
    def last_truncated(self, v):
        self._inner.last_truncated = v

    def __getattr__(self, k):                 # usage 등 나머지 전부 위임
        return getattr(self._inner, k)


# ---------- 컨텍스트 재구성(영속 gen_context + 프로젝트 원문, 읽기 전용) ----------

def _parse_fact(s: str) -> OntologyFact:
    """영속 'entity: attr=value' → OntologyFact. 첫 ': '/'=' 분할 — 재직렬화(f"- {e}: {a}={v}")가 원문과 동일함이 보장."""
    i = s.find(": ")
    ent, rest = s[:i], s[i + 2:]
    j = rest.find("=")
    return OntologyFact(entity=ent, attr_label=rest[:j], value=rest[j + 1:])


def _bible_hint(p: dict, rec: dict, beat: dict) -> str:
    ep_climax = ""
    for a in (p["world"].get("spine") or {}).get("arcs", []):
        for e in a.get("episodes", []):
            if e.get("episode_id") == rec.get("episode_id"):
                ep_climax = e.get("climax") or ""
    return f"{beat.get('title', '')} {beat.get('summary', '')} {' '.join(beat.get('key_events') or [])} {ep_climax}"


def _rehydrate_anchor(a: dict, p: dict, rec: dict, beat: dict, settings, notes: list[str]) -> str:
    """240자에서 절단 영속된 앵커를 결정론 재계산으로 복원 — 재계산본의 240 프리픽스가 영속값과 일치할 때만 채택."""
    text = a["text"]
    if len(text) < 240:                        # 절단 아님 — 영속값이 원문 그대로
        return text
    full = None
    if a["source"] == "bible" and a["ref"] == "digest":
        items, _ = bible_digest(StoryBible(**p["bible"]), settings.bible_digest_chars, _bible_hint(p, rec, beat))
        full = items[0].text if items else None
    elif a["source"] == "arc_anchor" and a["ref"] == "ending":
        end = (p["world"].get("spine") or {}).get("ending") or {}
        full = f"[작품 엔딩 방향] 질문: {end.get('central_question', '')} / 결말: {end.get('ending', '')}"
    elif a["source"] == "wiki_page":           # 역사적 본문 미영속(덮어쓰기 갱신) — 복원 불가, 프리픽스 사용(사전 공표 결손)
        notes.append(f"wiki_page:{a['ref']} 240자 프리픽스 사용(역사 본문 미영속)")
        return text
    if full and full[:240] == text:
        notes.append(f"{a['source']}:{a['ref']} 재계산 복원({len(full)}자, 프리픽스 일치)")
        return full
    notes.append(f"{a['source']}:{a['ref']} 재계산 불일치 → 240자 프리픽스 폴백")
    return text


def rebuild_context(p: dict, ch_no: int, settings) -> dict:
    """회차 ch_no 의 집필 입력 재구성 + 영속 gen_context 대비 충실도 체크. 반환: board/spec/tails/beat/checks/notes."""
    rec = _chapter(p, ch_no)
    d = rec["gen_context"]["draft"]
    prior = _prior(p, ch_no)
    checks, notes = [], []

    def ck(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 문체(StyleSpec — 프로젝트 영속값)
    style = StyleSpec(**p["world"]["style"])
    ck("persona", style.system_persona[:240] == d["persona"])
    ck("style_rules", list(style.rules) == list(d["style_rules"]))
    ck("author_style", (style.author_style or "")[:240] == d["author_style"])
    ck("ending_hook", style.ending_hook == d["ending_hook_mode"])

    # 확정 설정(파싱→재직렬화 왕복 동일성)
    facts = []
    for s in d["ground_truth"]:
        ok = (": " in s and "=" in s.split(": ", 1)[1])
        if not ok:
            ck("ground_truth_parse", False, s[:60])
            continue
        f = _parse_fact(s)
        facts.append(f)
        if f"{f.entity}: {f.attr_label}={f.value}" != s:
            ck("ground_truth_roundtrip", False, s[:60])
    ck("ground_truth_n", len(facts) == len(d["ground_truth"]), f"{len(facts)}/{len(d['ground_truth'])}")

    # 누적 줄거리 — detail_synopsis 재조립(계층 rollup 은 대상 회차에서 빈 것으로 프리픽스 검증)
    lines = [f"{c['chapter']}화: {c.get('detail_synopsis') or c.get('summary') or (c.get('text') or '')[:120]}"
             for c in prior]
    kept, used = [], 0
    for line in reversed(lines):
        if kept and used + len(line) + 1 > settings.story_so_far_chars:
            break
        kept.append(line)
        used += len(line) + 1
    story_so_far = "\n".join(reversed(kept))
    ck("story_so_far_prefix", story_so_far[:2500] == d["story_so_far"])
    ck("story_so_far_len", len(story_so_far) == d["story_so_far_chars"],
       f"{len(story_so_far)} vs {d['story_so_far_chars']}")

    # 직전 화 전문(프로젝트 원문) — 영속 발췌/길이 대조
    pv = _chapter(p, ch_no - 1)["text"] if ch_no > 1 else ""
    ck("prev_chars", len(pv) == d["prev_chapter_chars"], f"{len(pv)} vs {d['prev_chapter_chars']}")
    exc = pv[:200] + (" …(중략)… " + pv[-200:] if len(pv) > 420 else "")
    ck("prev_excerpt", exc == d["prev_chapter_excerpt"])

    # recent_tails(훅 로테이션 재료) — 영속 80자 꼬리와 대조
    tails = [c["text"][-160:] for c in prior[-3:] if c.get("text")]
    ck("recent_tails", [t[-80:] for t in tails] == d["recent_tails"])

    # 앵커(참조 맥락) — 절단 영속분 결정론 복원(프리픽스 일치 시만)
    beat = d["beat"]
    anchors = [RetrievedItem(source=a["source"], ref=a["ref"],
                             text=_rehydrate_anchor(a, p, rec, beat, settings, notes))
               for a in d["anchors"]]
    ck("anchors_n", len(anchors) == len(d["anchors"]))
    ck("anchors_prefix", all(x.text[:240] == a["text"] for x, a in zip(anchors, d["anchors"])))

    board = ContextBoard(
        chapter=ch_no, ground_truth=facts, world_rules=list(d["world_rules"])[:12],
        story_time=d["story_time"], narrative=anchors,
        authority=[AuthorDirective(directive_id=f"d{i+1}", text=t, from_chapter=ch_no)
                   for i, t in enumerate(d["directives"])],
        prev_chapter=pv, story_so_far=story_so_far, voice_cards=d["voice_roster"])
    spec = SceneSpec(index=0, goal=beat.get("summary", ""), key_events=beat.get("key_events") or [])
    return {"rec": rec, "d": d, "style": style, "board": board, "spec": spec,
            "tails": tails, "beat": beat, "checks": checks, "notes": notes}


def _make_generator(style: StyleSpec, settings, provider) -> ChapterGenerator:
    gen = ChapterGenerator(provider, checker=None, style=style, event_bus=_Bus(), settings=settings)
    gen._cur_scene_inject = ""    # 원 생성과 동일(scene_style_anchor=False → 주입 없음)
    gen._cur_skill_inject = ""    # 이 프로젝트 injected_skills=[] (gen_context 에 skills 키 부재 확인)
    return gen


# ---------- 단계: verify ----------

def cmd_verify() -> int:
    settings = get_settings()
    p = _load_project()
    WORK.mkdir(parents=True, exist_ok=True)
    out = {"pid": PID, "chapters": {}, "settings": {
        "gen": f"{settings.llm_provider}:{settings.gen_model}",
        "chapter_max_tokens": settings.chapter_max_tokens,
        "prev_chapter_context_chars": settings.prev_chapter_context_chars,
        "craft_progress": settings.craft_progress, "scene_style_anchor": settings.scene_style_anchor}}
    ok_all = True
    for ch in CHAPTERS:
        ctx = rebuild_context(p, ch, settings)
        gen = _make_generator(ctx["style"], settings, provider=None)   # 프롬프트 조립만(LLM 0콜)
        user_body = gen.assembler.assemble(ctx["board"], ctx["spec"], "")
        bad = [c for c in ctx["checks"] if not c["ok"]]
        ok_all = ok_all and not bad
        out["chapters"][str(ch)] = {
            "checks": ctx["checks"], "notes": ctx["notes"],
            "assemble_sha": _sha(user_body), "assemble_chars": len(user_body),
            "orig_chars": len(ctx["rec"]["text"])}
        print(f"ch{ch}: checks {sum(c['ok'] for c in ctx['checks'])}/{len(ctx['checks'])} ok"
              f" | assemble {len(user_body)}자 sha={_sha(user_body)}")
        for c in bad:
            print(f"  FAIL {c['check']} {c['detail']}")
        for n in ctx["notes"]:
            print(f"  note: {n}")
    (WORK / "verify.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("verify:", WORK / "verify.json", "| all_ok =", ok_all)
    return 0 if ok_all else 1


# ---------- 단계: gen (변종 생성) ----------

def cmd_gen() -> int:
    settings = get_settings()
    p = _load_project()
    WORK.mkdir(parents=True, exist_ok=True)
    provider = _Recorder(create_provider(settings))   # anthropic claude-opus-4-6 (config 기본 — LR-1 동일)
    norm_calls0 = provider.usage.chat_calls
    for ch in CHAPTERS:
        ctx = rebuild_context(p, ch, settings)
        if any(not c["ok"] for c in ctx["checks"]):
            print(f"ch{ch}: 재구성 체크 실패 — gen 중단(verify 먼저)")
            return 1
        gen = _make_generator(ctx["style"], settings, provider)
        norm = int(ctx["style"].target_chars_per_chapter * 0.85)
        for vi in range(1, N_VARIANTS + 1):
            vpath = WORK / f"ch{ch}_v{vi}.txt"
            mpath = WORK / f"ch{ch}_v{vi}.meta.json"
            if vpath.exists() and mpath.exists():                     # resume
                print(f"ch{ch} v{vi}: 존재 — 스킵")
                continue
            t0 = time.time()
            calls0 = provider.usage.chat_calls
            text, ext = "", 0
            for _regen in range(2):                                   # 빈 응답만 1회 재생성(파이프라인 동일)
                provider.last_truncated = False
                text = sanitize_meta(gen._draft(ctx["board"], ctx["spec"], "", last=True, closing=False,
                                                recent_tails=ctx["tails"], chapter_mode=True))
                if provider.last_truncated:
                    text = trim_dangling(text)
                if vi == 1 and _regen == 0 and provider.last_messages:   # 프롬프트 지문 감사(1회 저장)
                    (WORK / f"ch{ch}_prompt.json").write_text(json.dumps(
                        {"system_sha": _sha(provider.last_messages[0]["content"]),
                         "user_sha": _sha(provider.last_messages[1]["content"]),
                         "system": provider.last_messages[0]["content"],
                         "user": provider.last_messages[1]["content"],
                         "kwargs": provider.last_kwargs}, ensure_ascii=False, indent=1), encoding="utf-8")
                ext = 0
                while text.strip() and len(text) < norm and ext < 4:  # 출고 규범 미달 → 진행 이어쓰기(파이프라인 동일)
                    ext += 1
                    provider.last_truncated = False
                    more = sanitize_meta(gen._continue(ctx["board"], text, closing=False,
                                                       recent_tails=ctx["tails"],
                                                       key_events=ctx["beat"].get("key_events") or []))
                    if provider.last_truncated:
                        more = trim_dangling(more)
                    if len(more.strip()) < 200:
                        break
                    text = text.rstrip() + "\n\n" + more.strip()
                if text.strip():
                    break
            vpath.write_text(text, encoding="utf-8")
            mpath.write_text(json.dumps({
                "chapter": ch, "variant": vi, "chars": len(text), "extends": ext,
                "calls": provider.usage.chat_calls - calls0, "norm": norm,
                "model": f"{settings.llm_provider}:{settings.gen_model}",
                "elapsed_s": round(time.time() - t0, 1)}, ensure_ascii=False), encoding="utf-8")
            print(f"ch{ch} v{vi}: {len(text)}자 ext={ext} ({round(time.time() - t0, 1)}s)")
    print(f"gen 완료 — 총 chat 콜 {provider.usage.chat_calls - norm_calls0}")
    return 0


# ---------- 심사 공용 ----------

def _judge_user(p: dict, ch: int, text_a: str, text_b: str, question: str) -> str:
    chs = sorted([c for c in p["chapters"] if c.get("status") == "FINALIZED"], key=lambda c: c["chapter"])
    idx = next(i for i, c in enumerate(chs) if c["chapter"] == ch)
    w = p["world"]
    parts = [f"[작품] {w.get('title', '')} — {w.get('genre', '')}"]
    if idx > 0:
        parts.append("[지금까지 읽은 회차의 한 줄 요약]\n"
                     + "\n".join(f"{c['chapter']}화: {(c.get('summary') or '').strip()}" for c in chs[:idx]))
        prev = chs[idx - 1]
        parts.append(f"[직전 화({prev['chapter']}화) 전문]\n" + (prev.get("text") or "")[:TRUNC])
    parts.append(f"[원고 A — {ch}화 자리]\n" + text_a[:TRUNC])
    parts.append(f"[원고 B — {ch}화 자리]\n" + text_b[:TRUNC])
    parts.append(question)
    return "\n\n".join(parts)


_ELO_SYSTEM = (
    "너는 한국 웹소설 연재 플랫폼에서 이 작품을 1화부터 유료로 따라 읽어온 독자다. "
    "같은 작품의 같은 회차 자리에 들어갈 두 원고 A와 B를 읽는다. "
    "어느 원고가 더 몰입해서 읽히고, 다 읽었을 때 다음 화(100원)를 결제하고 싶게 만드는가? "
    "의무감으로 후하게 판단하지 말고 솔직한 독자 반응으로, 반드시 둘 중 하나만 골라라(무승부 없음).\n"
    '출력은 JSON 객체만: {"winner": "A" 또는 "B", "reason": "한 줄 이유"}')
_ELO_QUESTION = "[질문] A와 B 중 어느 원고가 더 몰입되고 다음 화를 결제하고 싶게 만드는가? winner 와 한 줄 이유."

_HELDOUT_SYSTEM = (
    "너는 웹소설 플랫폼의 유료 연재 심사역이다. 연독률(다음 화 결제 전환)이 낮은 원고를 걸러내는 것이 네 일이다. "
    "같은 작품의 같은 회차 자리에 후보 원고 A와 B가 올라왔고, 이 자리에는 하나만 실을 수 있다. "
    "독자가 이 회차를 읽고 다음 화를 결제하지 않고 떠날 위험이 더 낮은 쪽, 즉 연독을 더 강하게 끄는 쪽 하나만 승인하라. "
    "둘 다 아쉽더라도 반드시 하나는 골라야 한다.\n"
    '출력은 JSON 객체만: {"approve": "A" 또는 "B", "reason": "탈락 원고의 연독 위험 한 줄"}')
_HELDOUT_QUESTION = "[질문] A와 B 중 연독(다음 화 결제)을 더 강하게 끄는 원고 하나만 승인하라. approve 와 한 줄 이유."


def _load_candidates(p: dict, ch: int) -> dict[str, str]:
    cand = {"orig": _chapter(p, ch)["text"]}
    for vi in range(1, N_VARIANTS + 1):
        vp = WORK / f"ch{ch}_v{vi}.txt"
        if not vp.exists():
            raise SystemExit(f"변종 없음: {vp} — gen 먼저")
        cand[f"v{vi}"] = vp.read_text(encoding="utf-8")
    return cand


def _pick(d: dict, key: str) -> str:
    v = str(d.get(key) or "").strip().upper()
    if v in ("A", "B"):
        return v
    return "A" if "A" in v and "B" not in v else "B"


# ---------- 단계: elo ----------

def elo_table(games: list[dict], cands: list[str]) -> dict:
    """초기 1000 · K=32 · 고정 시드 셔플 순서(결정론). games: {a,b,winner_key}."""
    order = list(games)
    random.Random(SEED).shuffle(order)
    r = {c: 1000.0 for c in cands}
    for g in order:
        a, b, w = g["a"], g["b"], g["winner_key"]
        ea = 1.0 / (1.0 + 10 ** ((r[b] - r[a]) / 400.0))
        sa = 1.0 if w == a else 0.0
        r[a] += 32 * (sa - ea)
        r[b] += 32 * ((1 - sa) - (1 - ea))
    return {c: round(v, 1) for c, v in r.items()}


def cmd_elo() -> int:
    get_settings()
    p = _load_project()
    WORK.mkdir(parents=True, exist_ok=True)
    judge = OpenAIProvider(ELO_JUDGE, "text-embedding-3-small")
    log = WORK / "elo_games.jsonl"
    done = {g["game_id"]: g for g in _read_jsonl(log)}
    for ch in CHAPTERS:
        cand = _load_candidates(p, ch)
        keys = list(cand)                                             # orig, v1..v4
        for x, y in itertools.combinations(keys, 2):
            for first, second in ((x, y), (y, x)):                    # 양순서 = 독립 2게임
                gid = f"ch{ch}:{first}|{second}"
                if gid in done:
                    continue
                user = _judge_user(p, ch, cand[first], cand[second], _ELO_QUESTION)
                t0 = time.time()
                res = judge.chat_json([{"role": "system", "content": _ELO_SYSTEM},
                                       {"role": "user", "content": user}],
                                      temperature=0.3, max_tokens=400)
                w = _pick(res, "winner")
                row = {"game_id": gid, "chapter": ch, "a": first, "b": second,
                       "winner_pos": w, "winner_key": first if w == "A" else second,
                       "reason": str(res.get("reason") or "").strip(),
                       "judge": ELO_JUDGE, "temperature": 0.3,
                       "elapsed_s": round(time.time() - t0, 1)}
                _append_jsonl(log, row)
                done[gid] = row
                print(f"{gid} -> {row['winner_key']} ({row['elapsed_s']}s)")
    # 집계 출력
    for ch in CHAPTERS:
        games = [g for g in done.values() if g["chapter"] == ch]
        keys = ["orig"] + [f"v{i}" for i in range(1, N_VARIANTS + 1)]
        table = elo_table(games, keys)
        wins = {k: sum(1 for g in games if g["winner_key"] == k) for k in keys}
        print(f"ch{ch} Elo:", {k: table[k] for k in sorted(table, key=table.get, reverse=True)}, "| wins/8:", wins)
    return 0


# ---------- 단계: heldout ----------

def _elo_rank(ch: int) -> list[str]:
    games = [g for g in _read_jsonl(WORK / "elo_games.jsonl") if g["chapter"] == ch]
    if len(games) != 20:
        raise SystemExit(f"ch{ch} 게임 {len(games)}/20 — elo 먼저 완료")
    keys = ["orig"] + [f"v{i}" for i in range(1, N_VARIANTS + 1)]
    table = elo_table(games, keys)
    wins = {k: sum(1 for g in games if g["winner_key"] == k) for k in keys}
    return sorted(keys, key=lambda k: (table[k], wins[k], k != "orig"), reverse=True)


def cmd_heldout() -> int:
    get_settings()
    p = _load_project()
    judge = OpenAIProvider(HELDOUT_JUDGE, "text-embedding-3-small")
    log = WORK / "heldout.jsonl"
    done = {g["call_id"]: g for g in _read_jsonl(log)}
    for ch in CHAPTERS:
        rank = _elo_rank(ch)
        top_variant = next(k for k in rank if k != "orig")            # Elo 최상위 변종(사전 등록 §1.4)
        cand = _load_candidates(p, ch)
        print(f"ch{ch}: Elo 순위 {rank} → held-out {top_variant} vs orig")
        for rd in range(1, 4):                                        # 3판 × 양순서
            for first, second in ((top_variant, "orig"), ("orig", top_variant)):
                cid = f"ch{ch}:r{rd}:{first}|{second}"
                if cid in done:
                    continue
                user = _judge_user(p, ch, cand[first], cand[second], _HELDOUT_QUESTION)
                t0 = time.time()
                res = judge.chat_json([{"role": "system", "content": _HELDOUT_SYSTEM},
                                       {"role": "user", "content": user}], max_tokens=6000)
                w = _pick(res, "approve")
                row = {"call_id": cid, "chapter": ch, "round": rd, "a": first, "b": second,
                       "winner_key": first if w == "A" else second,
                       "reason": str(res.get("reason") or "").strip(), "judge": HELDOUT_JUDGE,
                       "elapsed_s": round(time.time() - t0, 1)}
                _append_jsonl(log, row)
                done[cid] = row
                print(f"{cid} -> {row['winner_key']} ({row['elapsed_s']}s)")
    return 0


# ---------- 단계: report ----------

def _heldout_verdict(ch: int, rows: list[dict], top_variant: str) -> dict:
    rounds = {}
    for rd in (1, 2, 3):
        pair = [r for r in rows if r["chapter"] == ch and r["round"] == rd]
        if len(pair) != 2:
            rounds[rd] = "미완"
            continue
        w = {pair[0]["winner_key"], pair[1]["winner_key"]}
        rounds[rd] = pair[0]["winner_key"] if len(w) == 1 else "불일치"
    cons = [v for v in rounds.values() if v in (top_variant, "orig")]
    return {"rounds": rounds,
            "variant_wins": sum(1 for v in cons if v == top_variant),
            "orig_wins": sum(1 for v in cons if v == "orig")}


def cmd_report() -> int:
    get_settings()
    p = _load_project()
    games = _read_jsonl(WORK / "elo_games.jsonl")
    hrows = _read_jsonl(WORK / "heldout.jsonl")
    keys = ["orig"] + [f"v{i}" for i in range(1, N_VARIANTS + 1)]
    out = {"chapters": {}}
    for ch in CHAPTERS:
        g = [x for x in games if x["chapter"] == ch]
        table = elo_table(g, keys)
        wins = {k: sum(1 for x in g if x["winner_key"] == k) for k in keys}
        # 양순서 일치(쌍 단위): 두 순서 게임의 승자가 같은 후보면 일치
        pairs = {}
        for x in g:
            pk = tuple(sorted((x["a"], x["b"])))
            pairs.setdefault(pk, []).append(x["winner_key"])
        consistent = {f"{a}|{b}": (w[0] if len(set(w)) == 1 else "불일치") for (a, b), w in pairs.items()}
        rank = _elo_rank(ch)
        top_variant = next(k for k in rank if k != "orig")
        meta = {}
        for vi in range(1, N_VARIANTS + 1):
            mp = WORK / f"ch{ch}_v{vi}.meta.json"
            if mp.exists():
                meta[f"v{vi}"] = json.loads(mp.read_text(encoding="utf-8"))
        out["chapters"][str(ch)] = {
            "elo": table, "wins_of_8": wins, "rank": rank, "pair_consistency": consistent,
            "top_variant": top_variant, "elo1_is_orig": rank[0] == "orig",
            "heldout": _heldout_verdict(ch, hrows, top_variant),
            "variant_meta": meta,
            "orig_chars": len(_chapter(p, ch)["text"]),
            "games": g, "heldout_rows": [r for r in hrows if r["chapter"] == ch]}
    raw = REPORTS / "in7_raw.json"
    raw.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("raw:", raw)
    for ch in CHAPTERS:
        r = out["chapters"][str(ch)]
        print(f"ch{ch}: rank={r['rank']} elo={r['elo']} heldout={r['heldout']}")
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="IN-7 쌍대 Elo 검증(라이브 data/ 읽기 전용)")
    ap.add_argument("stage", choices=["verify", "gen", "elo", "heldout", "report"])
    args = ap.parse_args(argv)
    return {"verify": cmd_verify, "gen": cmd_gen, "elo": cmd_elo,
            "heldout": cmd_heldout, "report": cmd_report}[args.stage]()


if __name__ == "__main__":
    raise SystemExit(main())
