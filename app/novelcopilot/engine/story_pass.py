# -*- coding: utf-8 -*-
"""SY-1 스토리 패스 — 깔때기 스테이지 오케스트레이션 (설계 docs/design-sy1-story-pass-wiring.md §1).

원칙(설계 계약):
- 각 스테이지는 순수 함수(state 읽기 외 부수효과 0·repo 접근 0). 영속·게이트는 서비스 몫.
- run_funnel 은 작가 게이트 직전까지만 — 후보를 반환할 뿐 승격하지 않는다(무강제).
- 재료는 라이브 캐논에서 생성 전 해석(§1-A): 에피소드는 narrative_progress 커서로 조회.
  ChapterRecord 에서 읽는 필드는 detail_synopsis·text·episode_id 3개뿐(§3 화이트리스트).
- 문안·검사는 story_pass_prompts 단일 출처(감사 3회전 통과분 — 문자 하나도 안 바꾼다).
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from . import story_pass_prompts as P
from .chapter_gate import slot_event


# RC-1 ⓒ: 슬롯 서사기능 태그(chapter_function 어휘) → 화 역할 파생(genre-blind 구조 매핑). 미매핑/미분배 → 로테이션 폴백.
_FUNCTION_ROLE = {"setup": "조사", "escalation": "위기", "payoff": "회수",
                  "relation": "관계", "respite": "휴지", "reveal": "조사"}


def slot_line_budget(n_events: int) -> dict:
    """RC-1 ⓓ: 슬롯에 배분된 사건 수 → 확정 스토리 줄 수 예산. 4건 슬롯이 종전 기본(12~16)과 정확히 일치하도록
    맞춘 단조 함수 — 가벼운 슬롯은 바닥을 낮춰 압축 강제를 풀고, 무거운 슬롯은 상한을 올린다(짧은 슬롯 압축 해소)."""
    n = max(1, int(n_events))
    lo = max(6, min(12, 4 + 2 * n))
    hi = max(lo + 4, min(20, 8 + 2 * n))
    return {"lo": lo, "hi": hi}


class StoryPassNotReady(RuntimeError):
    """커서 부재·에피소드 소진 등 재료를 조립할 수 없는 상태 — 시끄럽게 실패(폴백 금지)."""


@dataclass
class StoryPassDeps:
    gen: object                 # 본문 생성 프로바이더(chat)
    judge: object               # cross-vendor 심사(chat_json) — style_judge_model 재사용
    bus: object = None          # EventBus(선택 — 없으면 무음)

    def emit(self, stage: str, event: str, **kw):
        if self.bus is not None:
            try:
                self.bus.emit(stage, event, **kw)
            except Exception:
                pass


# ── §1-A 재료 조립(생성 전 해석 — PM 실측 수리: ChapterRecord 전제 제거) ──────────

def _episode_by_id(state, ep_id: str):
    for arc in state.world.spine.arcs:
        for ep in arc.episodes:
            if getattr(ep, "episode_id", "") == ep_id:
                return ep
    return None


def assemble_materials(state, chapter_no: int | None = None) -> dict:
    """반환: {mat, syn_prev, tools, episode_id, slot, planned_event, chapter_no, digest}.

    에피소드 = narrative_progress.current_episode_id(생성 전 커서), slot = chapters_in_episode+1.
    커서가 비었거나 에피소드 소진이면 StoryPassNotReady(하드코딩 폴백 금지 — 구판 유출 채널)."""
    np_ = getattr(state, "narrative_progress", None)
    ep_id = getattr(np_, "current_episode_id", "") or ""
    if not ep_id:
        raise StoryPassNotReady("narrative_progress 커서가 비어 있음 — 에피소드 배정 필요")
    ep = _episode_by_id(state, ep_id)
    if ep is None:
        raise StoryPassNotReady(f"커서 에피소드({ep_id!r})를 spine 에서 못 찾음")
    slot = int(getattr(np_, "chapters_in_episode", 0) or 0) + 1
    target = int(getattr(ep, "target_chapters", 0) or 0)
    if target and slot > target:
        raise StoryPassNotReady(f"에피소드 {ep_id} 소진(slot {slot} > target {target}) — 커서 전진 필요")
    if chapter_no is None:
        chapter_no = int(getattr(state, "current_chapter", 0) or 0) + 1

    # RC-3: 도구 정본 이중 소스 — object 엔티티 우선, vocabulary_tone 정규식 폴백(둘 다 부재 시에만 실패).
    #   엔티티 없는 작품 = 정규식 경로 바이트 동일(m.group(1) 원문 그대로 — 하위호환).
    from ..domain.world import object_entity_names, canon_tool_vocab_match
    _tool_names = object_entity_names(state.world)
    if _tool_names:
        tools = "·".join(_tool_names)
    else:
        m = canon_tool_vocab_match(state.world)
        if not m:
            raise StoryPassNotReady("도구 정본을 찾지 못함(object 엔티티·장르 계약 도구 어휘 모두 부재) — 캐논 확인 필요")
        tools = m.group(1)

    # 지난 이야기: 이번 화보다 앞선 회차 중 최근 확정 2화의 상세(최종 감사 중대 4: 회차 상한 —
    #   임의 chapter 지정 시 자기·미래 회차 상세가 재료로 새는 채널 차단). 화이트리스트 필드만.
    prev = sorted([c for c in state.chapters
                   if c.chapter < chapter_no and (c.detail_synopsis or "").strip()],
                  key=lambda c: c.chapter)[-2:]
    syn_prev = "\n\n".join((c.detail_synopsis or "").strip() for c in prev)
    # 최종 감사 치명 1: 심문 콜의 '[1화 상세]' 라벨 계약 — 재탕근접 대조 대상은 1화 해법(라벨=값)
    ch1 = next((c for c in state.chapters if c.chapter == 1), None)
    syn1 = (getattr(ch1, "detail_synopsis", "") or "").strip()

    full_menu = list(getattr(ep, "event_menu", None) or [])
    if not full_menu:
        raise StoryPassNotReady(f"에피소드 {ep_id} 의 사건 메뉴가 비어 있음 — 메뉴 생성 후 재시도(재료 기아·빈 라벨 방지)")
    # RC-1 ⓑ: 회차 예산 분배(episode.slots)가 있으면 [사건 재료]를 '이 슬롯 몫 + 다음 슬롯 예고(참고)'로 좁힌다 —
    #   후보 풀 결정론 조정(T4 패턴·본문 게이트 0·무강제). 미분배(빈 slots·플래그 OFF·구 JSON) → 아래 slot_plan=None
    #   분기가 종전 코드와 '바이트 동일'한 mat/menu/planned 를 만든다(통짜 메뉴 경로 불변).
    slots = list(getattr(ep, "slots", None) or [])
    slot_plan = next((s for s in slots if int(getattr(s, "slot", 0) or 0) == slot), None) if slots else None
    slot_function = (getattr(slot_plan, "function", "") or "").strip() if slot_plan else ""
    this_events: list[str] = []
    if slot_plan:
        this_events = [e for e in ([(slot_plan.central or "").strip()]
                                   + [s.strip() for s in (slot_plan.support or [])]) if e]
    scoped = bool(this_events)
    menu = this_events if scoped else full_menu
    nxt = (next((s for s in slots if int(getattr(s, "slot", 0) or 0) == slot + 1), None) if slots else None)
    preview = [(nxt.central or "").strip()] if (nxt and (nxt.central or "").strip()) else []
    preview_block = ("\n\n[다음 화 예고(참고 — 이번 화 재료 아님)]\n"
                     + "\n".join(f"- {x}" for x in preview)) if (scoped and preview) else ""
    # 줄 수 예산(RC-1 ⓓ): 이 슬롯 사건 수로 스케일. 미분배 → None(호출부가 종전 12~16 유지).
    line_budget = slot_line_budget(len(menu)) if scoped else None
    # SP-2(감사 C2): 중심 사건 지정의 단일 출처는 role_line({event}) — 구 [이번 화 설계 사건] 블록은
    #   system(role_line)과 user(재료)가 서로 다른 사건을 중심으로 지목하는 이중 출처였다. planned 는
    #   역할·이벤트 배정기의 입력으로만 쓴다(후보 1의 중심 사건 우선권).
    #   RC-1: 슬롯 분배가 있으면 중심 사건(central) 우선, 없으면 [N화차] 접두 폴백(미분배 시 바이트 동일).
    planned = ((getattr(slot_plan, "central", "") or "").strip() if slot_plan else "") \
        or slot_event(getattr(ep, "required_events", None), slot) or ""
    mat = (f"[작품 전제]\n{state.world.synopsis}\n\n"
           f"[지난 이야기(직전 2화 상세)]\n{syn_prev}\n\n"
           f"[캐논 메모]\n주인공의 도구 여섯 점: {tools}.\n\n"
           f"[사건 재료(취사선택)]\n" + "\n".join(f"- {x}" for x in menu) + preview_block)
    bad = [b for b in P.BAN if b in mat]
    if bad:
        raise StoryPassNotReady(f"조립 재료에 금지어 잔존 {bad} — 프로젝트 데이터 수리 필요(프롬프트 덧대기 금지)")
    # 최종 감사 치명 2(가시화 축): 재료에 실린 대시·기획서 말투 계수 — 소스는 구판 영속 요약이므로
    #   차단이 아니라 emit 원자료(재요약이 소스 차단, 프롬프트 덧대기 금지).
    hygiene = {"dash": mat.count("—"),
               "design": sorted(set(P.DESIGN_LABEL_RE.findall(mat))),
               "briefish": mat.count("복선") + mat.count("미결 긴장")}
    # XR-1 ⓑ(조립 시점 회귀 가드): 결말 '정본 필드' 문장이 재료에 실렸는지 결정론 검사(관측 — 차단 0).
    #   synopsis 는 이 재료의 구성 요소 자체라 needle 에서 제외(그 채널의 정책은 XR-4 콜드리드 후속 결정) —
    #   여기 needle 은 spine.ending 필드만. FS-1 이전식 결말 앵커 재도입을 조립 지점에서 즉시 가시화한다.
    end = getattr(getattr(state.world, "spine", None), "ending", None)
    needles = {f"spine.ending.{f}": (getattr(end, f, "") or "")
               for f in ("ending", "thematic_payoff", "central_question")
               if end is not None and (getattr(end, f, "") or "").strip()}
    if needles:
        from .verification import ending_literal_leak_sweep
        hygiene["ending_literal_leak"] = ending_literal_leak_sweep(needles, {"materials": mat})["count"]
    return {"mat": mat, "syn_prev": syn_prev, "syn1": syn1, "tools": tools, "episode_id": ep_id,
            "slot": slot, "planned_event": planned, "chapter_no": chapter_no,
            "menu": menu, "hygiene": hygiene,
            "slot_function": slot_function,   # RC-1 ⓒ: 역할 파생 입력(미분배 → "" = 로테이션 폴백)
            "line_budget": line_budget,       # RC-1 ⓓ: 슬롯 예산 줄 수(미분배 → None = 종전 12~16)
            "digest": hashlib.sha256(mat.encode("utf-8")).hexdigest()[:12]}


# ── SP-2 §1-A2 역할·이벤트 배정(결정론 — LLM 0콜) ──────────────────────────────

def _digest_int(digest: str, salt: str = "") -> int:
    return int(hashlib.sha256((digest + salt).encode("utf-8")).hexdigest()[:8], 16)


def assign_role(state, materials, role: str | None = None) -> str:
    """화 역할 결정 — 작가 지정 > 회수 압력 > 최근 사용 역할 제외 로테이션(digest 결정론).

    회수 압력: 최근 3화 라벨(chapter_function)이 전부 payoff/escalation 이면 '회수'를 후보 상단에
    (수지 원장 취지 — 지불 연속 뒤에는 돌려받는 화가 서는 자리). 시스템 단독 결정이 아니라
    작가 오버라이드(role 인자)가 항상 우선한다(무강제)."""
    if role:
        if role not in P.ROLES:
            raise StoryPassNotReady(f"알 수 없는 화 역할 {role!r} — 사용 가능: {', '.join(P.ROLES)}")
        return role
    # RC-1 ⓒ: 슬롯 서사기능 태그가 있으면 역할을 그 태그에서 파생(로테이션 대체). 회수압력은 무조건 분기가 아니라
    #   태그 부재 시의 입력 신호로 강등한다. 미분배(빈 slot_function·플래그 OFF·구 JSON) → 아래 종전 경로 바이트 동일.
    _fn = (materials.get("slot_function") or "").strip()
    _derived = _FUNCTION_ROLE.get(_fn)
    if _derived and _derived in P.ROLES:
        return _derived
    recent_roles = []
    for rec in sorted(getattr(state, "story_passes", None) or [],
                      key=lambda r: getattr(r, "chapter", 0))[-2:]:
        rr = (getattr(rec, "checks", None) or {}).get("role")
        if rr:
            recent_roles.append(rr)
    pool = [r for r in P.ROLES if r not in recent_roles] or list(P.ROLES)
    recent_fn = [(getattr(c, "chapter_function", "") or "")
                 for c in sorted(state.chapters, key=lambda c: c.chapter)[-3:]]
    if recent_fn and all(f in ("payoff", "escalation") for f in recent_fn) and "회수" in pool:
        return "회수"
    return pool[_digest_int(materials["digest"], "role") % len(pool)]


def assign_events(materials) -> dict:
    """3안의 중심 사건 배정 — 필수 사건(planned)이 있으면 후보 1의 중심, 나머지는 메뉴에서
    digest 결정론으로 서로 다른 사건을 뽑는다(다양성은 렌즈가 아니라 재료 분배 — SP-2 골격)."""
    menu = list(materials.get("menu") or [])
    picks: list[str] = []
    if materials.get("planned_event"):
        picks.append(materials["planned_event"])
    start = _digest_int(materials["digest"], "event") % max(1, len(menu))
    i = 0
    while len(picks) < 3 and i < len(menu) * 2:
        cand = menu[(start + i) % len(menu)]
        i += 1
        if cand not in picks:
            picks.append(cand)
    while len(picks) < 3:   # 메뉴가 3개 미만인 극단 폴백 — 중복 허용(시끄럽게 죽지 않기)
        picks.append(picks[-1] if picks else "")
    return {"E1": picks[0], "E2": picks[1], "E3": picks[2]}


# ── §1-B 깔때기 스테이지(확정 문안 그대로 — 검사는 결정론) ─────────────────────

@promptlog.stage("story_pass:fix_fmt")
def _ensure_fmt(deps: StoryPassDeps, text: str, label: str, line_budget: dict | None = None) -> tuple[str, list[str]]:
    """형식 검사 → 불합격 시 재변환 1회 → 재검사. 잔존 위반은 반환(작가 플래그 — 차단 없음).
    RC-1 ⓓ: line_budget(슬롯 예산)이 있으면 줄 수 범위를 그 예산으로 검사. 미전달(None)=종전 12~16(바이트 동일)."""
    lo = int(line_budget["lo"]) if line_budget else 12
    hi = int(line_budget["hi"]) if line_budget else 16
    errs = P.fmt_check(text, lo=lo, hi=hi)
    if not errs:
        return text, []
    deps.emit("story_pass", "fmt_retry", label=label, errs=errs[:4])
    fixed = (deps.gen.chat([{"role": "system", "content": P.FIX_FMT},
                            {"role": "user", "content": text}], temperature=0.2) or "").strip()
    errs2 = P.fmt_check(fixed, lo=lo, hi=hi)
    if errs2:
        deps.emit("story_pass", "fmt_unresolved", label=label, errs=errs2[:4])
        return text, errs
    return fixed, []


@promptlog.stage("story_pass:base")
def generate_candidates(deps, materials, role_lines: dict | None = None, ending_hook="",
                        line_budget: dict | None = None) -> dict:
    """SP-2: 3안 = 같은 역할, 서로 다른 중심 사건. role_lines={key: 채워진 role_line}.
    미전달이면 시끄럽게 실패한다(조용한 구판 각도 폴백 금지 — 감사 지정).
    RC-1 ⓓ: line_budget 이 있으면 줄 수 계약을 슬롯 예산으로 스케일. 미전달=종전 12~16(바이트 동일)."""
    if not role_lines:
        raise StoryPassNotReady("role_lines 미전달 — 역할 배정기(assign_role/assign_events)를 먼저 태워라")
    oc = P.build_out_contract(int(line_budget["lo"]), int(line_budget["hi"])) if line_budget else None
    out = {}
    for key, role_line in role_lines.items():
        t = (deps.gen.chat([{"role": "system", "content": P.build_base_sys(ending_hook, role_line, out_contract=oc)},
                            {"role": "user", "content": materials["mat"]}], temperature=0.6) or "").strip()
        t, errs = _ensure_fmt(deps, t, f"draft:{key}", line_budget=line_budget)
        out[key] = {"story": t, "fmt_errs": errs}
    return out


@promptlog.stage("story_pass:review")
def cross_review(deps, cands: dict) -> dict:
    keys = list(cands)
    reviews = {}
    for i, key in enumerate(keys):
        target = keys[(i + 1) % len(keys)]
        try:
            r = deps.judge.chat_json([{"role": "system", "content": P.REVIEW_SYS},
                                      {"role": "user", "content": cands[target]["story"]}], temperature=0.3)
            if not isinstance(r, dict):
                r = {"지적": []}
        except Exception:
            r = {"지적": []}
        text, dropped = P.render_review(r, cands[target]["story"])
        reviews[target] = text
        deps.emit("story_pass", "review", target=target, dropped=dropped)
    return reviews


@promptlog.stage("story_pass:revise")
def revise_independently(deps, materials, cands, reviews, role_lines: dict | None = None, ending_hook="",
                         line_budget: dict | None = None) -> dict:
    if not role_lines:
        raise StoryPassNotReady("role_lines 미전달 — 설계 콜과 동일 바이트 계약(감사 검사 1)")
    oc = P.build_out_contract(int(line_budget["lo"]), int(line_budget["hi"])) if line_budget else None
    out = {}
    for key in cands:
        t = (deps.gen.chat(
            [{"role": "system", "content": P.build_rev_sys(role_lines[key], ending_hook, out_contract=oc)},
             {"role": "user", "content": f"{materials['mat']}\n\n[내 초안]\n{cands[key]['story']}\n\n[수정 지시]\n{reviews.get(key, '')}"}],
            temperature=0.5) or "").strip()
        t, errs = _ensure_fmt(deps, t, f"rev:{key}", line_budget=line_budget)
        out[key] = {"story": t, "fmt_errs": errs}
    return out


@promptlog.stage("story_pass:audit")
def audit_criteria(deps, finals: dict, syn1: str, role: str = "", syn_prev: str = "") -> dict:
    """SP-2: 심문표 = 공통 4축 + 역할 축. syn1은 재탕근접 판정 자료, syn_prev는 회수 축
    '출처인용'(돌려받는 것이 앞 화에서 마련된 근거) 대조 자료 — 스토리 단일 소스 대조는
    구조적으로 실패한다(감사 C4)."""
    axes = P.audit_axes(role)
    sys_prompt = P.build_audit_sys(role)
    audits = {}
    for key, v in finals.items():
        t = v["story"]
        try:
            a = deps.judge.chat_json([{"role": "system", "content": sys_prompt},
                                      {"role": "user", "content": P.build_audit_user(t, syn1, syn_prev)}],
                                     temperature=0.0)
            if not isinstance(a, dict):
                a = {"error": "non-dict"}
        except Exception as e:
            a = {"error": str(e)[:100]}
        for crit in axes:
            node = a.get(crit) or {}
            if node.get("이행") and not P.cite_ok(node.get("인용", ""), t):
                node["이행"] = False
                node["강등"] = "인용 원문 불일치"
            if crit == "회수" and node.get("이행") and not P.cite_ok(node.get("출처인용", ""), syn_prev):
                node["이행"] = False
                node["강등"] = "출처인용 지난 이야기 불일치"
        for crit in ("가장약한이음", "재탕근접"):
            node = a.get(crit) or {}
            if isinstance(node, dict) and node.get("인용"):
                node["원문대조"] = P.cite_ok(node.get("인용", ""), t)
        audits[key] = a
    return audits


@promptlog.stage("story_pass:pair")
def pairwise_reference(deps, finals: dict) -> dict:
    """쌍대 재미 — 참고 전용(자동 결정에 쓰지 않는다 — 호출부 계약)."""
    import itertools
    score = {k: 0 for k in finals}
    pairs = []
    for x, y in itertools.combinations(list(finals), 2):
        try:
            r = deps.judge.chat_json([{"role": "system", "content": P.PAIR_SYS},
                                      {"role": "user", "content": f"[A]\n{finals[x]['story']}\n\n[B]\n{finals[y]['story']}"}],
                                     temperature=0.0)
            if not isinstance(r, dict):
                continue
            r["A_인용_대조"] = P.cite_ok(r.get("A_인용", ""), finals[x]["story"])
            r["B_인용_대조"] = P.cite_ok(r.get("B_인용", ""), finals[y]["story"])
            p = (r.get("pick") or "").strip()
            if p == "A":
                score[x] += 1
            elif p == "B":
                score[y] += 1
            pairs.append({"pair": f"{x}v{y}", **r})
        except Exception:
            pass
    return {"score": score, "pairs": pairs}


def pick_pillar(finals: dict, audits: dict, pillar: str | None = None,
                role: str = "", digest: str = "") -> str:
    """기둥 선정 — 기계 재미 픽 금지(§1-B): ⓐ심문 5기준(공통 4+역할 축) 이행 수 최다
    → ⓑ형식 검사 무결 → ⓒdigest 해시 셔플(사전순 타이브레이커가 K1 7/9 편중의 소스였다 —
    감사 필수 지정). 작가 지정(pillar)이 있으면 그것이 우선."""
    if pillar and pillar in finals:
        return pillar
    axes = P.audit_axes(role)
    def rank(k):
        a = audits.get(k) or {}
        ok = sum(1 for c in axes if (a.get(c) or {}).get("이행"))
        return (-ok, len(finals[k]["fmt_errs"]), _digest_int(digest, k))
    return sorted(finals, key=rank)[0]


@promptlog.stage("story_pass:scan")
def scan_grafts(deps, pillar_story: str, donors: dict) -> list[dict]:
    n_pillar = len(P.story_lines(pillar_story))
    grafts = []
    for d, v in donors.items():
        try:
            r = deps.judge.chat_json([{"role": "system", "content": P.SCAN_SYS},
                                      {"role": "user", "content": f"[기둥 스토리]\n{pillar_story}\n\n[후보 스토리]\n{v['story']}"}],
                                     temperature=0.0)
            if not isinstance(r, dict):
                continue
        except Exception:
            continue
        for a in (r.get("assets") or []):
            if not isinstance(a, dict):
                continue
            if (bool(a.get("관문a")) and bool(a.get("관문b"))
                    and P.cite_ok(a.get("컷", ""), v["story"]) and P.pos_ok(a.get("자리", ""), n_pillar)):
                grafts.append({"from": d, "컷": a["컷"], "자리": a["자리"], "근거": a.get("근거", "")})
    return grafts[:2]


@promptlog.stage("story_pass:merge")
def merge_grafts(deps, pillar_story: str, grafts: list[dict]) -> str | None:
    """병합 시공 — 형식·줄 수 등식 불합격이면 None(단독 유지). 채택은 작가 게이트."""
    if not grafts:
        return None
    import json as _json
    n_pillar = len(P.story_lines(pillar_story))
    merged = (deps.gen.chat([{"role": "system", "content": P.MERGE_SYS},
                             {"role": "user", "content": f"[기둥]\n{pillar_story}\n\n[이식 명세]\n{_json.dumps(grafts, ensure_ascii=False)}"}],
                            temperature=0.3) or "").strip()
    n_exp = n_pillar + len(grafts)
    if P.fmt_check(merged, lo=n_exp, hi=n_exp) or not P.merge_ok(merged, n_pillar, len(grafts)):
        deps.emit("story_pass", "merge_rejected", expected=n_exp, got=len(P.story_lines(merged)))
        return None
    return merged


@promptlog.stage("story_pass:check")
def check_and_joint(deps, story: str, prev_tail: str) -> tuple[str, dict]:
    """훅 소화 판정(judge)과 접합 줄 작성(gen)의 콜 분리(감사 M6). 접합 줄은 형식 3종 검사."""
    try:
        c = deps.judge.chat_json([{"role": "system", "content": P.CHECK_SYS},
                                  {"role": "user", "content": f"[직전 화 말미]\n{P.REF_LABEL}\n{prev_tail}\n\n[확정 스토리]\n{story}"}],
                                 temperature=0.0)
        if not isinstance(c, dict):
            c = {}
    except Exception:
        c = {}
    digested = bool(c.get("소화됨")) and P.cite_ok(c.get("인용", ""), story)
    info = {"소화됨": digested, "인용": c.get("인용", "")}
    if not digested:
        with promptlog.consumer("story_pass:joint"):   # XR-3: 같은 함수 안의 두 번째 스테이지(판정→접합)
            joint = (deps.gen.chat([{"role": "system", "content": P.JOINT_SYS},
                                    {"role": "user", "content": f"[직전 화 말미]\n{P.REF_LABEL}\n{prev_tail}\n\n[확정 스토리(맨 앞에 붙을 자리)]\n{story}"}],
                                   temperature=0.3) or "").strip()
        jlines = P.story_lines(joint)
        ok = (1 <= len(jlines) <= 2 and all(l.startswith("- ") for l in jlines)
              and not sum(joint.count(q) for q in P.QUOTE_CHARS) and not P.past_ending_count(joint))
        if ok:
            story = "\n".join(jlines) + "\n" + story
            info["접합"] = len(jlines)
        else:
            deps.emit("story_pass", "joint_rejected", lines=len(jlines))
            info["접합"] = 0
    return story, info


def run_funnel(deps: StoryPassDeps, state, chapter_no: int | None = None, *,
               pillar: str | None = None, ending_hook: str = "", role: str | None = None) -> dict:
    """깔때기 전체 — 작가 게이트 직전까지. 반환에 승격 없음(candidates 만).
    SP-2: 화 역할(role)이 3안을 이끈다 — 같은 역할, 서로 다른 중심 사건."""
    mats = assemble_materials(state, chapter_no)
    hg = mats.get("hygiene") or {}
    if hg.get("dash") or hg.get("design") or hg.get("briefish"):
        deps.emit("story_pass", "materials_hygiene", chapter=mats["chapter_no"], **hg)
    role = assign_role(state, mats, role)
    events = assign_events(mats)
    role_lines = {k: P.ROLES[role].format(event=ev) for k, ev in events.items()}
    deps.emit("story_pass", "role_assigned", chapter=mats["chapter_no"], role=role, events=events)
    _lb = mats.get("line_budget")   # RC-1 ⓓ: 슬롯 예산 줄 수(미분배 → None = 종전 12~16 바이트 동일)
    cands = generate_candidates(deps, mats, role_lines, ending_hook=ending_hook, line_budget=_lb)
    reviews = cross_review(deps, cands)
    finals = revise_independently(deps, mats, cands, reviews, role_lines, ending_hook=ending_hook, line_budget=_lb)
    audits = audit_criteria(deps, finals, mats["syn1"], role=role, syn_prev=mats["syn_prev"])
    pair = pairwise_reference(deps, finals)
    pk = pick_pillar(finals, audits, pillar, role=role, digest=mats["digest"])
    donors = {k: v for k, v in finals.items() if k != pk}
    grafts = scan_grafts(deps, finals[pk]["story"], donors)
    merged = merge_grafts(deps, finals[pk]["story"], grafts)

    prev = sorted([c for c in state.chapters if (c.text or "").strip()], key=lambda c: c.chapter)
    prev_tail = (prev[-1].text or "")[-700:] if prev else ""
    n_pillar = len(P.story_lines(finals[pk]["story"]))
    variants = []
    for name, s in (("solo", finals[pk]["story"]),) + ((("merged", merged),) if merged else ()):
        s2, hook_info = check_and_joint(deps, s, prev_tail)
        # M-F: line_budget 은 깔때기 시점에 계산·영속(확정 시점엔 기둥 정보가 없다)
        # RC-1 ⓓ: 슬롯 예산이 있으면 바닥·상한을 그 예산으로(그래도 병합 이식 줄 수는 상한에 반영). 미분배 → 종전 12~16.
        if _lb:
            budget = {"lo": int(_lb["lo"]), "hi": max(int(_lb["hi"]), n_pillar + len(grafts) + 2)}
        else:
            budget = {"lo": 12, "hi": max(16, n_pillar + len(grafts) + 2)}
        variants.append({"name": name, "story": s2,
                         "fmt_errs": P.fmt_check(s2, lo=budget["lo"], hi=budget["hi"]),
                         "hook": hook_info, "line_budget": budget})
    return {"materials": mats, "candidates": {k: v["story"] for k, v in cands.items()},
            "finals": {k: v["story"] for k, v in finals.items()}, "reviews": reviews,
            "audits": audits, "pairwise": pair, "pillar": pk, "grafts": grafts,
            "variants": variants, "role": role, "events": events}
