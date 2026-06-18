# -*- coding: utf-8 -*-
"""CopilotService — 유스케이스 오케스트레이션(Facade).

create_project(worldgen) → generate_next_chapter(하네스 + 동적 온톨로지) → directive 주입.
영속화/세션 재수화/비용 계측을 한데 묶되, 엔진 내부는 모른다(레이어 분리).
"""
from __future__ import annotations
import time
import uuid
import threading

from ..config import Settings
import re

from ..domain.project import ProjectSeed, ProjectState
from ..domain.draft import WorldDraft, ConceptBrief
from ..domain.world import EntitySpec, WorldRuleSpec
from ..domain.types import (AuthorDirective, ChapterStatus, WikiPage, RelationEdge, RetrievedItem,
                            RuleSpec, SignalGrade, ChapterRevision, Violation)
from ..domain.bible import StoryBible, BibleEntry, CATEGORY_LABEL, template_for, normalize_category
from ..llm.base import LLMProvider
from ..llm.factory import create_provider
from ..repository.base import ProjectRepository
from ..worldgen import WorldGenerator, BeatPlanner, ArcPlanner, BibleGenerator, WorldgenChat, ConceptChat
from ..engine.drift import episode_drift_signals
from ..engine.bible_compiler import bible_digest, entry_to_world_rule, migrate_world_to_bible
from .session import SessionManager


def _usage_delta(before: dict, after: dict) -> dict:
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}


def _accumulate(total: dict, delta: dict) -> dict:
    out = dict(total)
    for k, v in delta.items():
        out[k] = out.get(k, 0) + v
    return out


def _build_story_so_far(chapters, budget: int) -> tuple[str, int]:
    """누적 줄거리 요약 — FINALIZED 회차만, 최신부터 예산 내로 채운 뒤 시간순 제시. 반환=(text, 드롭된 회차수).
    요약이 비어도(요약 실패) 본문 앞부분 fallback → FINALIZED 회차가 줄거리에서 통째 누락(영구망각)되지 않게."""
    lines = [f"{c.chapter}화: {getattr(c, 'detail_synopsis', '') or c.summary or c.text[:120]}"
             for c in chapters if c.status == ChapterStatus.FINALIZED]
    if not lines:
        return "", 0
    out, total = [], 0
    for line in reversed(lines):
        if out and total + len(line) + 1 > budget:   # +1 = "\n" 구분자(예산 정확)
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(reversed(out)), len(lines) - len(out)


def _outstanding_plants(spine) -> list[str]:
    """완료 에피소드가 심었는데 어디서도 회수(payoff)되지 않은 복선 — 측정→생성 되먹임 고리의 재료.
    회수 여부는 결정론(라벨 일치). 마감 '강제'가 아니라 비트 설계에 '회수 우선 고려'로 주입(슬로우번은 작가 지시로 우회 가능)."""
    paid = {p for a in spine.arcs for e in a.episodes for p in e.payoffs}
    out: list[str] = []
    for a in sorted(spine.arcs, key=lambda x: x.order):
        for e in a.episodes:
            if e.done:
                out += [p for p in e.plants if p and p not in paid]
    return out


def _arc_anchors(spine, arc, ep) -> list[RetrievedItem]:
    """엔딩/아크/에피소드 방향을 narrative 앵커로(서사 의도 — ground_truth 아님)."""
    items: list[RetrievedItem] = []
    if spine and spine.ending and spine.ending.ending:
        items.append(RetrievedItem(source="arc_anchor", ref="ending",
                                   text=f"[작품 엔딩 방향] 질문: {spine.ending.central_question} / 결말: {spine.ending.ending}"))
    if arc:
        items.append(RetrievedItem(source="arc_anchor", ref=arc.arc_id,
                                   text=f"[현재 아크] {arc.title}: 목표 {arc.goal}"))
    if ep:
        items.append(RetrievedItem(source="arc_anchor", ref=ep.episode_id,
                                   text=f"[현재 에피소드] {ep.title}: 절정 '{ep.climax}'로 수렴"))
    return items


def _cast_context(ontology, world, entity_ids, chapter: int) -> str:
    """G6: 설계 콜에 인물 '스토리 컨텍스트' 주입 — 이름+프로필(배경·성격·욕망·관계)+현재 상태/속성+관계(결정론 조회).
    설계자가 인물을 'id 문자열'로만 받아 '욕망 없는 반사판'으로 퇴화하던 컨텍스트 기아를 해소(정보 제공, 강제 아님).
    이름·생사·소속은 온톨로지에서 시점 조회 → 죽은 인물·바뀐 소속을 모른 채 설계하던 결함 차단."""
    espec = {e.id: e for e in world.entities}
    ids = list(dict.fromkeys([i for i in entity_ids if i]))
    rel_by: dict[str, list[str]] = {}
    try:
        for f in ontology.canon_relations(ids, chapter):
            rel_by.setdefault(f.entity, []).append(f"{f.attr_label}={f.value}")
    except Exception:
        pass
    lines = []
    for eid in ids:
        ent = ontology.entities.get(eid)
        spec = espec.get(eid)
        name = ontology.name(eid) if ent else (spec.name if spec else None)
        if not name:
            continue
        seg = f"- {name}"
        prof = ((spec.profile if spec else "") or "").strip()
        if prof:
            seg += f": {prof[:220]}"
        meta = []
        if ent is not None:
            st = ontology.state_as_of(eid, "status", chapter)
            if st and st != "alive":
                meta.append(f"현재상태={st}")
            for a in list(ent.attrs)[:8]:   # I-4: [:4]→[:8] 핵심 추적축(rank 등) 비결정적 누락 방지
                if a == "status":
                    continue
                v = ontology.state_as_of(eid, a, chapter)
                if v is not None:           # I-4: 'if v:' 는 캐논값 0(F급=0 등)을 묵음 탈락 → is not None(ground_truth str(v) 정책 일치)
                    meta.append(f"{a}={v}")
        rels = rel_by.get(name) or []
        if rels:
            meta.append(", ".join(rels[:4]))   # rel 라벨이 이미 '관계:..' 자기기술 형태 → 접두어 생략
        if meta:
            seg += " [" + " / ".join(meta) + "]"
        lines.append(seg)
    return "\n".join(lines)


def _rollup_episode(provider, episode, chapters) -> str:
    """완료 에피소드의 회차 요약들을 1~2문장 에피소드 요약으로 압축(계층 story_so_far 재료)."""
    parts = [f"{c.chapter}화: {c.summary or c.text[:150]}" for c in chapters]
    try:
        out = provider.chat(
            [{"role": "system", "content": "여러 회차를 1~2문장 에피소드 요약으로 압축. 핵심 사건·결과·미결만. 군더더기 금지."},
             {"role": "user", "content": f"[에피소드]{episode.title}\n" + "\n".join(parts) + "\n에피소드 요약:"}],
            temperature=0.2, max_tokens=250).strip()
        return out or (episode.climax or episode.title)
    except Exception:
        return episode.climax or episode.title


def _build_story_so_far_hier(state, next_ch: int, budget: int) -> tuple[str, int]:
    """계층 누적 줄거리(spine 모드) — 예산을 '최근 회차 상세'로 채우고(최신 우선), 예산 밖 먼 에피소드만 1줄 롤업으로 압축.
    (I-1 교정) 예전 버전은 현재 에피소드 회차에만 상세를 썼다 → 에피소드 경계 직후엔 현재 회차가 0이라 롤업 1줄로 붕괴(예산 12k인데 1문단).
    이제 경계 무관 직전 완료 에피소드 상세까지 끌어와 예산을 채운다 → '경계 기아' 해소. 먼 과거는 롤업으로 압축(토큰 선형)."""
    spine = state.world.spine
    cur_ep = state.narrative_progress.current_episode_id
    prior = sorted([c for c in state.chapters if c.chapter < next_ch and c.status == ChapterStatus.FINALIZED],
                   key=lambda c: c.chapter)
    # 1) 최근 회차 상세로 예산 채움(최신 우선) — 직전 완료 에피소드까지 끌어와 경계 직후에도 예산 활용
    kept, covered, used = [], set(), 0
    for c in reversed(prior):
        line = f"{c.chapter}화: {getattr(c, 'detail_synopsis', '') or c.summary or c.text[:120]}"
        if kept and used + len(line) + 1 > budget:
            break
        kept.append(c); covered.add(c.episode_id); used += len(line) + 1
    detail = [f"{c.chapter}화: {getattr(c, 'detail_synopsis', '') or c.summary or c.text[:120]}"
              for c in sorted(kept, key=lambda c: c.chapter)]
    # 2) 상세에 안 든(예산 밖) 완료 에피소드는 1줄 롤업으로 — 엔딩 backward 인과의 먼 시작점 보존(부분 포함 에피소드는 롤업 생략=중복 방지)
    rollups = [f"[{arc.title}·{ep.title}] {ep.summary}"
               for arc in sorted(spine.arcs, key=lambda a: a.order)
               for ep in arc.episodes
               if ep.done and ep.summary and ep.episode_id != cur_ep and ep.episode_id not in covered]
    if not rollups and not detail:
        return "", 0
    return "\n".join(rollups + detail), len(prior) - len(kept)   # dropped = 상세에서 빠져 롤업/생략된 회차 수


def _slug(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")   # 내부 id(엔티티/설정항목) — ascii
    if not re.search(r"[a-z0-9]", base):
        base = "node"
    sid, i = base, 2
    while sid in existing:
        sid, i = f"{base}_{i}", i + 1
    return sid


class CopilotService:
    def __init__(self, settings: Settings, repo: ProjectRepository):
        self.settings = settings
        self.repo = repo
        self.sessions = SessionManager(settings)
        self._wg_provider: LLMProvider | None = None
        self._esc: dict = {}          # (pid,chapter)→연속 ESCALATED 횟수(무한 갇힘 경보용, 휘발)
        self._drafts: dict = {}       # 생성 전 컨셉 드래프트(휘발 — finalize 시 ProjectState 로 승격)
        self._finalizing: set = set() # finalize 진행 중인 draft id(EventSource 재연결 중복 생성 차단)
        self._draft_lock = threading.Lock()
        self._revise_drafts: dict = {}     # 퇴고 후보 캐시: revision_id → draft dict(휘발, TTL — accept 시 소비)
        self._revise_lock = threading.Lock()
        self._revise_ttl: float = 1800     # 30분(만료 후보 정리)

    @property
    def wg_provider(self) -> LLMProvider:
        if self._wg_provider is None:
            self._wg_provider = create_provider(self.settings)
        return self._wg_provider

    # ---- 컨셉 드래프트(대화로 빚는 세계관) ----
    def _new_draft_locked(self) -> WorldDraft:
        """_draft_lock 보유 상태에서 드래프트 생성(start_draft 재호출 시 재진입 데드락 회피)."""
        d = WorldDraft(id=uuid.uuid4().hex[:12], created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                       last_touched=time.time())
        self._drafts[d.id] = d
        return d

    def _sweep_drafts_locked(self) -> None:
        """만료(TTL 초과) 드래프트 폐기 + 개수 하드캡. finalize 중인 것은 보존. _draft_lock 보유 상태에서."""
        now = time.time()
        ttl = self.settings.draft_ttl_sec
        for k in [k for k, d in self._drafts.items()
                  if k not in self._finalizing and now - (d.last_touched or 0) > ttl]:
            self._drafts.pop(k, None)
        if len(self._drafts) > self.settings.max_drafts:   # TTL 내 폭주 방어(오래된 순 폐기)
            alive = sorted((d for d in self._drafts.values() if d.id not in self._finalizing),
                           key=lambda d: d.last_touched or 0)
            for d in alive[: len(self._drafts) - self.settings.max_drafts]:
                self._drafts.pop(d.id, None)

    def start_draft(self) -> WorldDraft:
        with self._draft_lock:
            self._sweep_drafts_locked()
            return self._new_draft_locked()

    def get_draft(self, did: str) -> "WorldDraft | None":
        return self._drafts.get(did)

    @staticmethod
    def _merge_locks(locks: dict, params: dict | None) -> dict:
        """작가가 컨트롤로 정한 파라미터(장르·분위기·회차)를 잠금에 병합. 빈 값은 무시(자동=AI 제안 유지)."""
        if not params:
            return locks
        for k in ("genre", "tone", "target_chapters"):
            v = params.get(k)
            if v in (None, ""):
                continue
            if k == "target_chapters":
                try:
                    v = max(1, min(1000, int(v)))
                except (ValueError, TypeError):
                    continue
            locks[k] = v
        kw = params.get("keywords")     # 트로프 키워드(작가 칩 선택) — 리스트. _apply_locks 가 brief.keywords 에 setattr.
        if isinstance(kw, list):
            cleaned = [str(x).strip() for x in kw if str(x).strip()][:6]   # 소프트 캡 6(태그 남발 방지)
            if cleaned:
                locks["keywords"] = cleaned
            else:
                locks.pop("keywords", None)
        return locks

    @staticmethod
    def _apply_locks(brief: ConceptBrief, locks: dict) -> ConceptBrief:
        for k, v in (locks or {}).items():
            if k == "keywords" and isinstance(v, list):   # S1: AI 추론 키워드 + 작가 칩 = 합집합(치환 아님 — 칩이 AI 추론분을 지우지 않게)
                brief.keywords = list(dict.fromkeys(list(brief.keywords or []) + v))[:8]
            elif hasattr(brief, k):
                setattr(brief, k, v)
        return brief

    def draft_turn(self, did: str, message: str, params: dict | None = None) -> dict:
        """대화 한 턴 — 브리프 갱신 + 변경점·추천 질문·되묻기 반환. (드래프트 없으면 새로 시작)
        params(작가가 컨트롤로 정한 장르·분위기·회차)는 AI 갱신보다 우선 — 12화로 되돌아가는 일 방지."""
        with self._draft_lock:               # 부기(생성·정리·터치)만 락 안에서 — LLM 콜은 락 밖(드래프트 간 직렬화 방지)
            self._sweep_drafts_locked()
            d = self._drafts.get(did) or self._new_draft_locked()
            d.locks = self._merge_locks(d.locks, params)
            d.last_touched = time.time()
            d.chat.append({"role": "author", "text": message})
            d.chat = d.chat[-60:]                               # 무한 누적·토큰 폭주 방지
            did, brief_in, chat_in, locks_in = d.id, d.brief, list(d.chat), dict(d.locks)
        r = ConceptChat(self.wg_provider).turn(brief_in, chat_in, message, locked=locks_in or None)
        with self._draft_lock:
            d = self._drafts.get(did)
            if d is None:                     # 턴 사이 만료/폐기된 극단 — 새로 만들어 결과 보존(영구망각 방지)
                d = self._new_draft_locked(); did = d.id
            d.brief = self._apply_locks(ConceptBrief.model_validate(r["brief"]), d.locks)   # 작가 잠금이 항상 우선
            d.open_questions = r.get("questions", [])
            d.chat.append({"role": "ai", "text": r.get("reply", "")})
            d.last_touched = time.time()
            r["brief"] = d.brief.model_dump()
            r["draft_id"] = did
            r["completeness"] = d.brief.completeness()
        return r

    def _brief_to_seed(self, brief: ConceptBrief) -> ProjectSeed:
        """누적 브리프 → 풍부한 시드. premise 에 설계서 전체를 구조화해 담아 worldgen 품질을 끌어올린다."""
        parts: list[str] = []
        if brief.logline:
            parts.append(brief.logline)
        if brief.premise:
            parts.append(brief.premise)
        if brief.setting:
            parts.append(f"[배경] {brief.setting}")
        if brief.characters:
            parts.append("[주요 인물] " + " / ".join(
                f"{c.name}({c.role}): {c.want}".strip(" :()") for c in brief.characters if c.name))
        if brief.world_rules:
            parts.append("[세계 규칙] " + " / ".join(brief.world_rules))
        if brief.conflicts:
            parts.append("[핵심 갈등] " + " / ".join(brief.conflicts))
        if brief.themes:
            parts.append("[주제] " + ", ".join(brief.themes))
        if getattr(brief, "keywords", None):
            parts.append(
                "[키워드·트로프] " + ", ".join(brief.keywords)
                + " — 이 트로프 관습을 세계·인물·전개에 반영하라. 단 관계형 트로프(후회·복수 등)는 대상(가해자·연적 등)을 "
                "초반에 깔되 회수(후회·복수 실행)는 한참 뒤로 미루는 장기 자산으로 설계하라(매 회차 반복 금지).")
        hint = next((f"{c.name}: {c.want}".strip(" :") for c in brief.characters
                     if c.role and ("주인공" in c.role or "주연" in c.role)), "")
        return ProjectSeed(
            title=brief.title, genre=brief.genre or "현대 판타지", tone=brief.tone,
            premise="\n".join(parts)[:4000] or brief.logline or brief.title,
            protagonist_hint=hint or (brief.characters[0].name if brief.characters else ""),
            target_chapters=brief.target_chapters or 12)

    def finalize_draft(self, did: str, params: dict | None = None, bus=None) -> tuple[ProjectState, dict]:
        """드래프트 → 세계 생성. 누적 브리프를 시드로 기존 파이프라인 실행 후 드래프트 폐기.
        멱등 가드: EventSource 자동 재연결로 finalize 가 중복 진입해도 두 번 생성하지 않는다(중복 과금 차단)."""
        with self._draft_lock:
            d = self._drafts.get(did)
            if d is None:
                raise ValueError("draft not found")
            if did in self._finalizing:
                raise ValueError("already finalizing")
            self._finalizing.add(did)
        try:
            d.locks = self._merge_locks(d.locks, params)
            self._apply_locks(d.brief, d.locks)
            state, delta = self.create_project(self._brief_to_seed(d.brief), bus=bus, brief=d.brief)
            self._drafts.pop(did, None)
            return state, delta
        finally:
            self._finalizing.discard(did)

    # ---- 프로젝트 ----
    def create_project(self, seed: ProjectSeed, bus=None, brief=None) -> tuple[ProjectState, dict]:
        # bus: 선택적 EventBus(SSE). brief: 선택적 ConceptBrief — 첫 설계(build_spine)에 대화 핵심을 충실 주입(컨텍스트 보강).
        def _emit(ev, **kw):
            if bus is not None:
                bus.emit("worldgen", ev, **kw)
        seed.target_chapters = max(1, min(200, int(seed.target_chapters or 12)))   # 방어 클램프(API 외 직접호출 보호)
        before = self.wg_provider.usage.as_dict()
        _emit("world_start")
        world = WorldGenerator(self.wg_provider).generate(seed)
        if not seed.title:
            seed.title = world.title
        _emit("world_done", title=world.title,
              entities=[e.name for e in world.entities if e.etype == "character"])
        # R4: 엔딩-주도 아크/에피소드 spine 설계(실패 시 None=평면 모드 폴백)
        _emit("spine_start")
        try:
            world.spine = ArcPlanner(self.wg_provider).build_spine(
                world, seed.target_chapters, brief=brief, bus=bus)   # G8: bus 로 spine 미완 가시화
            _emit("spine_done", arcs=len((world.spine.arcs if world.spine else []) or []),
                  ending_ok=bool(world.spine and world.spine.ending
                                 and (world.spine.ending.ending or "").strip()))
        except Exception:
            world.spine = None
            _emit("spine_skip")
        # R2: 장르 카테고리별 설정집 산문 생성(가장 느린 단계 — 카테고리별 실시간 노출, 실패 시 빈 설정집)
        _emit("bible_start")
        try:
            bible = StoryBible(entries=BibleGenerator(self.wg_provider).generate(world, seed, bus=bus))
            _emit("bible_done", entries=len(bible.entries))
        except Exception:
            bible = StoryBible()
            _emit("bible_skip")
        _emit("saving")
        pid = uuid.uuid4().hex[:12]
        state = ProjectState(id=pid, seed=seed, world=world, bible=bible,
                             created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        sess = self.sessions.get_or_create(state)
        sess.snapshot_into(state)            # 시드 위키 등 초기 메모리 영속
        delta = _usage_delta(before, self.wg_provider.usage.as_dict())
        state.usage_total = _accumulate(state.usage_total, delta)
        self.repo.save(state)
        _emit("done", pid=state.id, title=world.title)
        return state, delta

    def get_project(self, pid: str) -> ProjectState | None:
        return self.repo.get(pid)

    def list_projects(self) -> list[dict]:
        return self.repo.list_summaries()

    def delete_project(self, pid: str) -> bool:
        self.sessions.evict(pid)
        return self.repo.delete(pid)

    def add_directive(self, pid: str, text: str) -> AuthorDirective | None:
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                return None
            d = AuthorDirective(directive_id=f"d{len(state.directives) + 1}", text=text,
                                from_chapter=state.current_chapter + 1)
            state.directives.append(d)
            self.repo.save(state)
            return d

    # ---- 퇴고(회차 본문 사후 다듬기 — 사실 불변) ----
    @staticmethod
    def _norm_claim_map(claims: list[dict], vocab) -> dict:
        """CheckResult.claims → (entity_id, key) → 정규화 토큰값 맵. G-B 표면 비교 원료.
        캐논성 키(범주형·수치·상태)만 뽑고 evidence 키·appears_as 는 제외(표면 표현 차이는 정규화로 흡수)."""
        canon_keys = (set(vocab.categorical_keys) | set(vocab.numeric_keys)
                      | {a.key for a in vocab.state_specs() if a.key != "status"})
        out: dict = {}
        for c in claims or []:
            eid = c.get("id")
            if not eid:
                continue
            for k in canon_keys:
                v = c.get(k)
                if v in (None, "", "null", False):     # 값 없음 = 클레임 없음(누락은 비교 대상 아님)
                    continue
                out[(eid, k)] = str(v).strip().lower()  # 표면 표현 차이 흡수
        return out

    def _guardrail(self, before_text: str, after_text: str, before_res, ids, ont,
                   checker, chapter: int) -> tuple[dict, object]:
        """사실 불변 가드레일 전체 판정 — revise_chapter·accept_revision 양쪽에서 재사용.

        G-A(신규 하드 델타 0) AND G-B(클레임 표면값 불변) AND 길이가드. 반환=(판정 dict, after_res).
        involved_ids 는 호출부가 before_text 전체 스캔으로 고정(D2) — before·after 동일 주입(roster 대칭).
        """
        # (1) G-A 신규 하드 델타 — before 에 이미 있던 하드는 퇴고 책임 아니므로 무시
        before_hard_keys = {(v.entity, v.kind) for v in before_res.hard}
        after_res = checker.check_text(after_text, ont, chapter, ids)
        after_hard_keys = {(v.entity, v.kind) for v in after_res.hard}
        new_hard = after_hard_keys - before_hard_keys
        g_a_passed = len(new_hard) == 0

        # (2) G-B 클레임 표면 델타 — before 존재 (entity,key) 교집합에서 값이 바뀐 것만 차단
        vocab = checker.extractor.vocab
        before_map = self._norm_claim_map(before_res.claims, vocab)
        after_map = self._norm_claim_map(after_res.claims, vocab)
        changed = [(e, k, before_map[(e, k)], after_map[(e, k)])
                   for (e, k) in before_map
                   if (e, k) in after_map and before_map[(e, k)] != after_map[(e, k)]]
        new_keys = [(e, k) for (e, k) in after_map if (e, k) not in before_map]   # advisory(신규 단정)
        g_b_passed = len(changed) == 0

        # (3) 길이 가드(전체 대 전체 — span replace 후 before/after 모두 회차 전체 본문)
        ratio = len(after_text) / max(1, len(before_text))
        length_ok = 0.5 <= ratio <= 1.8

        def nm(eid):   # 엔티티 id → 표시명(작가 언어; 실패 시 id 폴백)
            try:
                return ont.name(eid)
            except Exception:
                return eid
        reasons = []
        if not g_a_passed:
            reasons.append("기존 설정과 충돌하는 표현이 생겼습니다")
        if not g_b_passed:
            reasons.append("이름·수치가 바뀌었습니다")
        if not length_ok:
            reasons.append("분량이 너무 많이 바뀌었습니다")
        result = {
            "passed": g_a_passed and g_b_passed and length_ok,
            "G_A_passed": g_a_passed, "G_B_passed": g_b_passed, "length_ok": length_ok,
            "new_hard": [{"entity": e, "kind": k} for (e, k) in sorted(new_hard)],
            "claim_changes": [{"entity": nm(e), "key": k, "before": bv, "after": av}
                              for (e, k, bv, av) in changed],
            "new_keys_advisory": [{"entity": nm(e), "key": k} for (e, k) in new_keys[:8]],
            "reason": " / ".join(reasons),
        }
        return result, after_res

    def _sweep_revise_drafts_locked(self) -> None:
        """만료(TTL 초과) 퇴고 후보 폐기. _revise_lock 보유 상태에서."""
        now = time.time()
        for k in [k for k, d in self._revise_drafts.items()
                  if now - (d.get("created_at") or 0) > self._revise_ttl]:
            self._revise_drafts.pop(k, None)

    def revise_chapter(self, pid: str, chapter_no: int, directive: str,
                       span_text: str = "", passes: list[str] | None = None) -> dict | None:
        """후보 생성(저장 안 함) — directive+span 으로 다듬은 after 와 가드레일 결과 반환.
        before 기준선(involved_ids·check)을 1회 계산해 캐시 동봉(accept 산발 차단). 423=생성 중이면 None."""
        passes = [p for p in (passes or []) if p in ("reformat", "fix_tense")]   # D1: 허용 pass 만
        directive = (directive or "").strip()
        if not directive:
            raise ValueError("작가 지시가 비었습니다")
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        if ch.status not in (ChapterStatus.FINALIZED, ChapterStatus.ESCALATED):
            raise ValueError("대상 회차가 아닙니다")
        sess = self.sessions.get_or_create(state)
        # ── (1) 검증+스냅샷(빠름) — sess.lock 을 non-blocking 으로 잡아 '생성 중 즉시 423' 계약 보장.
        #     locked() 체크 후 with 진입까지의 경쟁창 제거(이슈4): acquire(blocking=False) 가 실패하면 곧 None.
        if not sess.lock.acquire(blocking=False):   # 회차 생성 중(lost-update 방지) → 423
            return None
        try:
            state = self.repo.get(pid)    # 권위 재읽기
            if not state:
                raise KeyError(pid)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            # TOCTOU 재검증(락 안) — 락 밖 481행 status 검사는 stale 스냅샷.
            # 두 읽기 사이 다른 스레드가 채택/삭제로 본문·상태를 바꿨으면 before_text 가 잘못된 버전으로 캐시됨.
            if ch.status not in (ChapterStatus.FINALIZED, ChapterStatus.ESCALATED):
                raise ValueError("대상 회차가 아닙니다")
            before_text = ch.text
            ont = sess.bundle.ontology
            checker = sess.bundle.checker
            generator = sess.bundle.generator
            # span 정규화 검증(있으면 정확히 1회 매칭)
            if span_text:
                normalized = re.sub(r"\s+", " ", span_text).strip()
                from ..engine.harness import ChapterGenerator as _CG
                if _CG._find_span(before_text, normalized) is None:
                    raise ValueError("span_not_found")
            # involved_ids 고정(D2) — before_text 전체 스캔 1회, before·after 동일 주입
            ids = sorted(set(ont.scan_present_ids(before_text)))
        finally:
            sess.lock.release()           # ── LLM 콜 전에 락 해제(이슈1): 생성 스레드 블로킹 방지
        # ── (2) LLM 콜(락 없음) — before check_text(1콜) + revise_prose(1콜) + 가드레일 after check_text(1콜).
        #     sess.lock 을 보유하지 않으므로 동시에 'generate_next_chapter' 가 진행 가능.
        before_res = checker.check_text(before_text, ont, chapter_no, ids)   # before 1회 계산(캐시)
        after_text = generator.revise_prose(
            directive, before_text, span_text, passes, ids, ont, chapter_no)
        guardrail, _after_res = self._guardrail(before_text, after_text, before_res,
                                                ids, ont, checker, chapter_no)
        # ── (2.5) 무변경 감지(이슈: revise_prose 가 LLM 실패·길이가드·빈 살균 시 before_text 그대로 폴백).
        #     after==before 면 가드레일은 ratio=1.0 으로 통과하나 '성공한 퇴고'가 아니다.
        #     후보를 캐시하지 않고 changed:false 로 명시 반환 → 프론트가 '효과 없음'을 작가에게 고지(채택 무의미).
        if after_text == before_text:
            sess.bus.emit("revise", "no_change")   # CopilotService 엔 bus 없음 — 세션 bus 사용(락 밖 로컬 sess 유효)
            return {
                "revision_id": None, "before_text": before_text,
                "after_text": after_text, "span_text": span_text,
                "changed": False,
                "guardrail": {k: guardrail[k] for k in
                              ("passed", "G_A_passed", "G_B_passed", "length_ok",
                               "new_hard", "claim_changes", "new_keys_advisory", "reason")},
                "passes_used": passes,
            }
        # ── (3) 캐시 저장 — _revise_lock 으로만 최소 보호(sess.lock 불필요).
        revision_id = uuid.uuid4().hex[:12]
        with self._revise_lock:
            self._sweep_revise_drafts_locked()
            self._revise_drafts[revision_id] = {
                "before_text": before_text, "after_text": after_text,
                "before_res": before_res, "ids": ids, "passes": passes,
                "directive": directive, "span_text": span_text,
                "chapter_no": chapter_no, "pid": pid, "created_at": time.time(),
            }
        return {
            "revision_id": revision_id, "before_text": before_text,
            "after_text": after_text, "span_text": span_text,
            "changed": True,
            "guardrail": {k: guardrail[k] for k in
                          ("passed", "G_A_passed", "G_B_passed", "length_ok",
                           "new_hard", "claim_changes", "new_keys_advisory", "reason")},
            "passes_used": passes,
        }

    def accept_revision(self, pid: str, chapter_no: int, revision_id: str,
                        after_text_fb: str | None = None, span_text_fb: str | None = None,
                        passes_fb: list[str] | None = None) -> dict | None:
        """후보 채택 → 새 버전 저장. 서버 가드레일 재검증(before 캐시 신뢰·after 만 재계산).
        멀티워커 캐시 미스 시 req 의 after_text 폴백을 쓰되 before 는 현재 repo 본문에서 재계산(아직 채택 전=before).

        락 규율(revise_chapter 와 동일 — 이슈1/2/3): LLM 콜(_guardrail·폴백 check_text·_summarize)을
        sess.lock 밖에서 수행한다. sess.lock 은 (1) 스냅샷·더블-accept 검사 (2) 최종 기록 두 번만 진입하고,
        _revise_lock 은 sess.lock 진입 *전*에만 단독으로 잡아 pop(중첩 획득 제거 → 락 순서 역전 차단)."""
        # ── (0) 캐시 pop — sess.lock 진입 전 _revise_lock 단독. 중첩 획득(sess.lock 안 _revise_lock) 제거.
        #     get+pop 을 한 번에 처리: 만료 후보 정리도 같은 락 구간에서.
        draft = None
        with self._revise_lock:
            self._sweep_revise_drafts_locked()
            draft = self._revise_drafts.pop(revision_id, None)
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        # ── (1) 검증+스냅샷(빠름) — sess.lock 안에서 더블-accept 검사·before/after 값 확정까지만.
        if not sess.lock.acquire(blocking=False):   # 회차 생성/타 채택 진행 중 → 라우트가 423
            # 락 실패로 채택 미완료 → (0)에서 pop 한 후보를 복원해 재시도가 캐시히트하도록(이슈: 423 재시도 영구실패).
            #   setdefault: 같은 사이 다른 스레드가 정식 채택을 끝냈으면 덮지 않음(이미 소비된 id 재삽입 방지).
            if draft is not None:
                with self._revise_lock:
                    self._revise_drafts.setdefault(revision_id, draft)
            return None
        try:
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            # 더블-accept 방어(락 안) — 이력에 같은 revision_id 가 이미 있으면 거절(이중 기록 방지).
            if any(r.revision_id == revision_id for r in ch.revisions):
                raise ValueError("이미 채택된 퇴고입니다")
            ont = sess.bundle.ontology
            checker = sess.bundle.checker
            before_text = draft["before_text"] if draft else ch.text   # 폴백: 현재 본문=아직 채택 전이라 before
            after_text = draft["after_text"] if draft else after_text_fb
            if after_text is None:
                raise ValueError("후보가 만료되었습니다(after_text 폴백 필요)")
            # 락 밖 LLM 콜 사이 ch.text 가 교체되면(생성 완료) before 캐시가 구버전이 됨(lost-update).
            # 채택 직전 현재 본문을 고정해 두고 (3)에서 재대조 → 다르면 ValueError 로 재시도 안내(이슈4).
            current_text_at_snapshot = ch.text
            ids = draft["ids"] if draft else sorted(set(ont.scan_present_ids(before_text)))
            before_res_cached = draft["before_res"] if draft else None
            directive = draft["directive"] if draft else "(폴백)"
            span_text = draft["span_text"] if draft else (span_text_fb or "")
            passes_used = draft["passes"] if draft else [p for p in (passes_fb or [])
                                                         if p in ("reformat", "fix_tense")]
        finally:
            sess.lock.release()               # ── LLM 콜 전에 락 해제(이슈1/2): 생성 스레드 블로킹 방지
        # ── (2) LLM 콜(락 없음) — 폴백 before check_text(고비용 extractor→LLM)·가드레일 after check_text·요약.
        #     sess.lock 을 보유하지 않으므로 동시에 'generate_next_chapter' 가 진행 가능.
        before_res = (before_res_cached if before_res_cached is not None
                      else checker.check_text(before_text, ont, chapter_no, ids))
        # 서버 가드레일 재검증(클라이언트 불신) — 실패 시 ValueError(라우트가 409)
        guardrail, after_res = self._guardrail(before_text, after_text, before_res,
                                               ids, ont, checker, chapter_no)
        if not guardrail["passed"]:
            raise ValueError(f"가드레일 재검증 실패: {guardrail['reason']}")
        new_summary, new_detail = sess.bundle.generator._summarize(after_text, "")   # 요약 재생성(1콜)
        # ── (3) 최종 기록 — sess.lock 재진입. 더블-accept·본문 변경 재검(락 밖 콜 사이 경쟁 차단).
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            if any(r.revision_id == revision_id for r in ch.revisions):
                raise ValueError("이미 채택된 퇴고입니다")    # (1)~(3) 사이 동시 accept 가 먼저 기록
            if ch.text != current_text_at_snapshot:    # 락 밖 LLM 콜 사이 본문 교체됨 → before 캐시 무효(이슈4)
                raise ValueError("본문이 생성 사이 변경됨")
            # 본문 교체 + 이력 레코드 push(append-only) + 요약 반영 + RAG 재색인
            ch.revisions.append(ChapterRevision(
                revision_id=revision_id, directive=directive, span_text=span_text,
                before_text=before_text, after_text=after_text,
                before_summary=ch.summary, before_detail_synopsis=ch.detail_synopsis,
                passes_used=passes_used,
                violations_before=list(before_res.hard), violations_after=list(after_res.hard),
                claim_changes=guardrail["claim_changes"], guardrail_passed=True,
                guardrail_reason=guardrail["reason"],
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
            ch.text = after_text
            ch.summary, ch.detail_synopsis = new_summary, new_detail   # 락 밖 재생성분 반영
            ch.ai_tell = self._recompute_ai_tell(state, sess, after_text)   # 본문 교체 → 문체 신호 재계산(stale 추세 차단)
            sess.bundle.rag.index_chapter(chapter_no, after_text)   # RAG 재색인(멱등)
            sess.snapshot_into(state)
            self.repo.save(state)
        # 캐시는 (0)에서 이미 소비됨(누수 창 제거).
        return {"accepted": True, "chapter": ch.model_dump(), "revision_count": len(ch.revisions)}

    def _recompute_ai_tell(self, state, sess, text: str) -> dict:
        """본문이 바뀌는 모든 경로(생성·퇴고 accept·undo)에서 ai_tell 을 동일 방식으로 재계산 — stale 추세 소스 차단.
        결정론·LLM 0콜. roster(인명·고유어)는 어휘다양성 오염 방지."""
        try:
            from ..engine.quality_gates import ai_tell_profile
            roster = {e.name for e in sess.bundle.ontology.entities.values()}
            roster |= {k for ent in state.bible.entries for k in (ent.keywords or [])}
            return ai_tell_profile(text, roster)
        except Exception:
            return {}

    def undo_revision(self, pid: str, chapter_no: int) -> dict:
        """마지막 채택 되돌리기 — text/summary/detail_synopsis 복원 + RAG 재색인(결정론 복원)."""
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            last_rev = next((r for r in reversed(ch.revisions) if not r.reverted), None)
            if last_rev is None:
                raise ValueError("되돌릴 퇴고 이력이 없습니다")
            ch.text = last_rev.before_text
            ch.summary = last_rev.before_summary
            ch.detail_synopsis = last_rev.before_detail_synopsis
            ch.ai_tell = self._recompute_ai_tell(state, sess, last_rev.before_text)   # 복원 본문 → 문체 신호 재계산
            last_rev.reverted = True
            sess.bundle.rag.index_chapter(chapter_no, last_rev.before_text)   # RAG 복원(멱등)
            sess.snapshot_into(state)
            self.repo.save(state)
        return {"reverted": True, "chapter": ch.model_dump(), "revision_id": last_rev.revision_id}

    # ---- R1: 작가 직접 입력(엔티티/관계) — 개입지점(R5 일부 선반영) ----
    def add_entity(self, pid: str, name: str, etype: str = "character",
                   aliases: list[str] | None = None) -> dict | None:
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)            # 권위 재읽기(lost update 방지)
            if not state:
                return None
            from ..engine.ontology import Entity
            ont = sess.bundle.ontology
            name = (name or "").strip()
            if not name:
                raise ValueError("이름이 비었습니다")
            amap = ont.alias_map()
            if name in amap:
                return {"id": amap[name], "name": name, "created": False}
            etype = etype or "character"
            unknown = etype not in ont.entity_types
            sid = _slug(name, set(ont.entities))
            clean_aliases = [a for a in (aliases or []) if a]
            ont.add(Entity(id=sid, name=name, etype=etype, attrs={}, aliases=clean_aliases,
                           provisional=True))
            state.runtime_entities.append(EntitySpec(id=sid, name=name, etype=etype,
                                                      aliases=clean_aliases, attrs={}, provisional=True))
            sess.snapshot_into(state)
            self.repo.save(state)
            return {"id": sid, "name": name, "etype": etype, "created": True, "unknown_type": unknown}

    def add_relation(self, pid: str, src_id: str, dst_id: str, rel_id: str,
                     eff_from: int = 1, reason: str = "", role: str = "",
                     state: str = "", pov: str | None = None) -> dict | None:
        st = self.repo.get(pid)
        if not st:
            return None
        sess = self.sessions.get_or_create(st)
        with sess.lock:
            st = self.repo.get(pid)               # 권위 재읽기(lost update 방지)
            if not st:
                return None
            ont = sess.bundle.ontology
            rel_id = (rel_id or "").strip()
            if not rel_id:
                raise ValueError("관계 타입(rel_id)이 비었습니다")
            if src_id not in ont.entities or dst_id not in ont.entities:
                raise ValueError("존재하지 않는 엔티티")
            if src_id == dst_id:
                raise ValueError("자기참조 관계는 만들 수 없습니다")
            # 미등록 타입도 동작(자유). 제약(끝점타입·상태어휘)은 '선언된 경우에만' 게이팅(opt-in).
            rspec = ont.rel_spec(rel_id)
            s_etype, d_etype = ont.entities[src_id].etype, ont.entities[dst_id].etype
            if rspec.allowed_src_types and s_etype not in rspec.allowed_src_types:
                raise ValueError(f"'{rspec.label}' 관계의 출발 타입은 {rspec.allowed_src_types} 여야 합니다(현재 {s_etype})")
            if rspec.allowed_dst_types and d_etype not in rspec.allowed_dst_types:
                raise ValueError(f"'{rspec.label}' 관계의 도착 타입은 {rspec.allowed_dst_types} 여야 합니다(현재 {d_etype})")
            if rspec.states and state and state not in rspec.states:
                raise ValueError(f"'{rspec.label}' 관계 상태는 {rspec.states} 중 하나여야 합니다(현재 {state})")
            # cardinality 1:1 강제(opt-in): 배우자/약혼 등 배타 관계 — 두 당사자 중 누구든 다른 활성 1:1 관계가 있으면 거부
            if rspec.cardinality == "1:1":
                pair = {src_id, dst_id}
                act = [e for e in ont.edges if e.rel_id == rel_id and e.eff_to is None
                       and e.pov is None and e.trust_tier == "ground_truth"]
                for person in (src_id, dst_id):
                    if any(person in (e.src_id, e.dst_id) and {e.src_id, e.dst_id} != pair for e in act):
                        raise ValueError(f"'{rspec.label}'은(는) 1:1 관계입니다 — {ont.name(person)}에게 이미 다른 활성 관계가 "
                                         f"있습니다(기존 관계를 먼저 종료하세요)")
            pov = (pov or "").strip() or None
            if pov is not None and pov not in ont.entities:
                raise ValueError("관점(pov) 주체가 존재하지 않습니다")
            src_id, dst_id = ont.order_edge(rel_id, src_id, dst_id)   # 대칭 관계 정렬 → A↔B 중복 방지
            eff = int(eff_from) if eff_from else 1
            edge_id = f"{rel_id}:{src_id}->{dst_id}:{eff}" + (f":pov={pov}" if pov else "")
            if any(e.edge_id == edge_id for e in ont.edges):
                return {"edge_id": edge_id, "created": False}
            # 결정론 게이트: 새 '객관' 엣지가 그 시점에 시간선 모순(사망 후 관계 등)을 만들면 reject.
            # 관점(pov) 엣지는 믿음/인식(거짓 가능)이라 게이트 비대상 → 모순 검사 통과(delta 0).
            before = len([v for v in ont.ontology_internal_check(eff) if v.kind.startswith("edge_")])
            edge = RelationEdge(edge_id=edge_id, rel_id=rel_id, src_id=src_id, dst_id=dst_id,
                                role=role or "", state=state or "", pov=pov,
                                eff_from=eff, reason=reason or "",
                                trust_tier="ground_truth", provenance=["author"])
            ont.add_edge(edge)
            new_edge_viols = [v for v in ont.ontology_internal_check(eff) if v.kind.startswith("edge_")]
            if len(new_edge_viols) > before:
                ont.edges.pop()
                raise ValueError("이 관계는 시간선 모순을 만듭니다(예: 사망 이후 새 관계). eff_from을 조정하세요.")
            st.runtime_edges.append(edge)
            sess.snapshot_into(st)
            self.repo.save(st)
            return {"edge_id": edge_id, "created": True, "label": rspec.label}

    def end_relation(self, pid: str, src_id: str, dst_id: str, rel_id: str, eff_to: int) -> dict | None:
        """관계 종료(eff_to 설정) — 배신/탈퇴/이동 등 '관계 변화' 표현. 작가가 추가한 관계만 대상(시드 제외)."""
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)            # 권위 재읽기(lost update 방지)
            if not state:
                return None
            ont = sess.bundle.ontology
            eff_to = int(eff_to)
            src_id, dst_id = ont.order_edge(rel_id, src_id, dst_id)   # 대칭 관계는 정렬 — 비정렬 입력으로도 종료 매칭(저장 정규화와 일치)
            target = None
            for e in state.runtime_edges:
                if (e.src_id == src_id and e.dst_id == dst_id and e.rel_id == rel_id
                        and e.eff_to is None and e.eff_from < eff_to):
                    if target is None or e.eff_from > target.eff_from:
                        target = e
            if target is None:
                raise ValueError("종료할 활성 관계가 없습니다(작가가 추가한 관계만, eff_to는 시작 이후여야 함).")
            target.eff_to = eff_to                       # runtime_edges 와 ont.edges 는 동일 객체(공유 참조)
            for e in ont.edges:
                if e.edge_id == target.edge_id:
                    e.eff_to = eff_to
            sess.snapshot_into(state)
            self.repo.save(state)
            return {"edge_id": target.edge_id, "eff_to": eff_to, "ended": True}

    def get_session(self, pid: str):
        """SSE 구독용 — 세션을 미리 보장(같은 bus 를 generate 가 재사용)."""
        state = self.repo.get(pid)
        if not state:
            return None, None
        return self.sessions.get_or_create(state), state

    # ---- 회차 생성(핵심 유스케이스) ----
    def generate_next_chapter(self, pid: str, directive_text: str | None = None) -> dict:
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기(lock 안에서 — lost update 방지: 동시 bible 편집/promote 가 안 덮임)
            if not state:
                raise KeyError(pid)
            next_ch = state.current_chapter + 1
            spine = state.world.spine
            cap = (state.seed.target_chapters or 12) * 2     # 하드캡: 예산 2배 초과 시 강제 완결(런어웨이 차단)

            # R4 완결 종료 — 무한 생성 금지(엔딩 도달 or 하드캡)
            if spine and (state.narrative_progress.completed or state.current_chapter >= cap):
                if not state.narrative_progress.completed:
                    state.narrative_progress.completed = True
                    self.repo.save(state)
                return {"completed": True,
                        "reason": ("hard_cap" if state.current_chapter >= cap else "ending_reached"),
                        "current_chapter": state.current_chapter,
                        "total_beats": (state.seed.target_chapters or len(state.world.beats)),
                        "usage_total": state.usage_total}

            if directive_text and not any(
                    d.text == directive_text and d.from_chapter == next_ch for d in state.directives):
                state.directives.append(AuthorDirective(                # #9: 재시도 시 동일 지시 중복 방지
                    directive_id=f"d{len(state.directives) + 1}", text=directive_text, from_chapter=next_ch))

            active = [d for d in state.directives if d.from_chapter <= next_ch]
            prior = [c for c in state.chapters if c.chapter < next_ch]
            # 비트 설계용 맥락: 과거=한줄, 직전 화=상세 시놉시스+말미(클리프행어 인계) — 설계 단계 기아 방지
            summaries = [f"{c.chapter}화 {c.title}: {c.summary or c.text[:120]}" for c in prior[-4:-1]]
            if prior:
                pv = prior[-1]
                summaries.append(f"{pv.chapter}화(직전) 상세: {getattr(pv, 'detail_synopsis', '') or pv.summary or pv.text[:300]}")
                if pv.text:
                    summaries.append(f"직전 화 말미(이번 화 도입에서 즉시 이어받아 회수할 것): …{pv.text[-280:]}")

            arc = ep = None
            is_finale = False
            anchors, bible_dropped = [], 0   # 설정집 다이제스트는 beat 확정 후 '관련 카드 선별'로(아래)
            prog_snap = spine_snap = None
            def _fail_rollback():   # 예외 경로: 메모리 커서/spine 복원 + 세션 evict(다음 요청은 디스크=클린에서 재수화)
                if spine_snap is not None:
                    state.narrative_progress = prog_snap
                    state.world.spine = spine_snap
                try:
                    sess.bus.emit('narrative', 'generate_failed', chapter=next_ch)
                except Exception:
                    pass
                self.sessions.evict(pid)
            saved = False
            try:
                if spine and spine.arcs:   # R4 spine 모드: 에피소드(절정 backward) 단위로 beat 파생
                    # B: 커서/spine 변이를 트랜잭션으로 — FINALIZED 아니면 롤백(재시도 결정성·orphan/조기 arc.done 방지)
                    prog_snap = state.narrative_progress.model_copy(deep=True)
                    spine_snap = spine.model_copy(deep=True)
                    planner = ArcPlanner(sess.provider)
                    ep = planner.current_episode(state.world, state.narrative_progress, summaries,
                                                 remaining=max(2, (state.seed.target_chapters or 12) - next_ch + 1))
                    for _e in state.world.entities:   # 캐스트 플랜 레이어 동기화 — lazy 아크 설계가 낳은 인물(등장 전 설계)
                        if _e.id not in sess.bundle.ontology.entities:
                            from ..engine.ontology import Entity as _OntEntity
                            sess.bundle.ontology.add(_OntEntity(id=_e.id, name=_e.name, etype=_e.etype,
                                                                attrs=dict(_e.attrs), aliases=list(_e.aliases),
                                                                provisional=_e.provisional))
                            sess.bus.emit("cast_plan", "registered", chapter=next_ch, entity=_e.name)
                    if ep is None:                                  # 해소 중 완결 도달
                        self.repo.save(state)
                        return {"completed": True, "reason": "ending_reached",
                                "current_chapter": state.current_chapter,
                                "total_beats": (state.seed.target_chapters or len(state.world.beats)),
                                "usage_total": state.usage_total}
                    arc = spine.arc(state.narrative_progress.current_arc_id)
                    is_finale = (state.narrative_progress.chapters_in_episode + 1) >= ep.target_chapters
                    # G1: 약속 원장 동기화(설계 라벨 미러, 가산적) + 텔레메트리(미지불 잔고·지불 경과 가시화)
                    # 원장은 '데이터+작가 가시화'다 — 생성 프롬프트에 회수 지시를 주입하지 않는다(억지 회수=전개 붕괴, 작가가 빨간펜으로 조향).
                    from ..engine.ledger_ops import sync_ledger_from_spine, ledger_telemetry
                    sync_ledger_from_spine(state.promise_ledger, spine, state.current_chapter)
                    tele = ledger_telemetry(state.promise_ledger, next_ch)   # 가시화 emit 은 bus.reset 이후로(아래)
                    # 떡밥 리마인더 — 작가가 plant_reminder 로 '명시 opt-in' 했을 때만 비트에 참고 주입(기본 off=주입 안 함).
                    # 시스템이 기본으로 떡밥을 밀어넣지 않는다(비강제). 켰을 때도 '억지 회수 금지' 슬롯(작가 지시 위장 금지).
                    plant_notes = ""
                    policy = getattr(state.world, "plant_reminder", "off")
                    outstanding = _outstanding_plants(spine)
                    if outstanding and policy != "off":
                        plant_notes = (f"{outstanding[:self.settings.plant_inject_cap]} — 회수는 절정과 자연스럽게 맞물릴 때만. "
                                       "억지 회수 금지(슬로우번은 정당한 기법)")
                        if policy == "active" and is_finale:
                            plant_notes += ". finale: 자연스럽다면 이번 회차 회수를 고려"
                    # G6: 설계 콜에 인물 스토리 컨텍스트(이름·프로필·현재 상태·관계) 주입.
                    # I-2 교정: 우선순위 = 에피소드 필수 캐스트 > 직전 회차에 실제 등장한 actor(자동추출 NPC 포함) > 주연.
                    # 미등장 시드 인물이 슬롯을 먹고 정작 갈등 끄는 NPC(연결자·그림자)가 빠지던 문제 해소. actor 전체(잠정 포함)에서 선별.
                    ont = sess.bundle.ontology
                    recent_present = []
                    if prior:
                        try:
                            recent_present = ont.scan_present_ids(prior[-1].text or "")
                        except Exception:
                            recent_present = []
                    actor_ids = [e.id for e in state.world.entities if ont.is_actor(e.etype)]
                    cast_ids = list(dict.fromkeys((ep.required_cast or []) + recent_present + actor_ids))[:8]
                    cast_context = _cast_context(ont, state.world, cast_ids, next_ch)
                    beat = planner.beat_for_episode(state.world, arc, ep, next_ch, is_finale,
                                                    summaries, [d.text for d in active],
                                                    plant_notes=plant_notes, cast_context=cast_context)
                    # ---- 계획 하네스: 비트도 본문처럼 생성→결정론 lint(캐논 정합만)→교정 1회 ----
                    from ..engine.plan_lint import lint_beat, beat_repeat_score
                    from ..domain.types import Violation as _V, SignalGrade as _SG
                    plan_viols = lint_beat(beat.model_dump(), sess.bundle.ontology, next_ch)
                    prev_beat_summaries = [c.title + " " + (getattr(c, "detail_synopsis", "") or c.summary)[:200]
                                           for c in prior[-4:]]
                    rep = beat_repeat_score(sess.provider, f"{beat.title} {beat.summary}", prev_beat_summaries)
                    if rep > 0.86:   # 비트 재탕('같은 절벽') — 설계 단계 차단(본문 생성 전)
                        plan_viols.append(_V(entity="beat", kind="plan_beat_repeat", grade=_SG.DETERMINISTIC,
                                             canon="직전 비트들", text=f"유사도 {rep:.2f}",
                                             evidence="계획 lint: 재탕"))
                    if plan_viols:
                        sess.bus.emit("plan_lint", "violations", chapter=next_ch,
                                      kinds=[v.kind for v in plan_viols])
                        fix_note = "; ".join(f"[{v.kind}] {v.text}" for v in plan_viols)
                        beat = planner.beat_for_episode(   # 위반 명시 재계획 1회(M4식 — 무한 루프 금지)
                            state.world, arc, ep, next_ch, is_finale, summaries,
                            [d.text for d in active] + [f"(계획 결함 교정 필수) {fix_note}"],
                            plant_notes=plant_notes, cast_context=cast_context)
                        remaining = lint_beat(beat.model_dump(), sess.bundle.ontology, next_ch)
                        if remaining:   # 잔존 → 가시화 + 보수 폴백(무효/제거 인물만 제외하고 진행 — 정지 금지)
                            sess.bus.emit("plan_lint", "non_convergence", chapter=next_ch,
                                          kinds=[v.kind for v in remaining])
                            bad = {v.entity for v in remaining}
                            beat.entities = [e for e in beat.entities
                                             if e in sess.bundle.ontology.entities and
                                             sess.bundle.ontology.name(e) not in bad] or beat.entities[:1]
                    # T1 측정(매직버퍼 기각 교훈 — 가정 말고 계측): 회차 비트 사건 수 + 밀도 플래그(advisory·강제 아님, G4).
                    _nev = len(beat.key_events)
                    sess.bus.emit("plan_beat", "events", chapter=next_ch, n=_nev,
                                  target_chars=state.world.style.target_chars_per_chapter, finale=is_finale,
                                  density=("over" if (not is_finale and _nev > 6) else "thin" if _nev < 2 else "ok"))
                    hint = f"{beat.title} {beat.summary} {' '.join(beat.key_events)} {ep.climax}"
                    anchors, bible_dropped = bible_digest(state.bible, self.settings.bible_digest_chars, hint)
                    anchors = anchors + _arc_anchors(spine, arc, ep)
                    espec = {e.id: e for e in state.world.entities}
                    for eid in beat.entities:   # 데뷔 집행: 설계 완료된 인물의 첫 등장 — 콜드 드롭 소스 차단
                        e = espec.get(eid)
                        if e is not None and not getattr(e, "introduced", False) and getattr(e, "profile", ""):
                            anchors.append(RetrievedItem(source="cast_debut", ref=eid,
                                           text=f"[신규 등장 인물 — 이번 화 첫 도입. 첫 등장 시 정체·관계를 알 수 있는 "
                                                f"소개 앵커 1문장 의무, 기지 인물처럼 다루지 마라] {e.name}: {e.profile[:300]}"))
                            sess.bus.emit("cast_plan", "debut", chapter=next_ch, entity=e.name)
                    story_so_far, dropped = _build_story_so_far_hier(state, next_ch, self.settings.story_so_far_chars)
                else:                      # 평면 모드(하위호환)
                    beat = BeatPlanner(sess.provider).beat_for(
                        state.world, next_ch, summaries, [d.text for d in active])
                    anchors, bible_dropped = bible_digest(state.bible, self.settings.bible_digest_chars,
                                                          f"{beat.title} {beat.summary}")
                    story_so_far, dropped = _build_story_so_far(prior, self.settings.story_so_far_chars)

                prev = state.chapter(next_ch - 1)
                prev_text = prev.text if prev else ""

                before = sess.provider.usage.as_dict()
                sess.bus.reset()
                if dropped:   # 오래된 맥락이 예산에서 잘림 — 조용한 정지 금지(가시화)
                    sess.bus.emit("assemble_memory", "story_truncated", chapter=next_ch, dropped=dropped)
                # I-1: 누적 줄거리 예산 사용률 가시화 — 경계 직후 silent 미달(예산 큰데 콘텐츠 1줄)을 작가가 보게
                _ssf_budget = self.settings.story_so_far_chars
                if spine and spine.arcs and len(story_so_far) < _ssf_budget * 0.4:
                    sess.bus.emit("assemble_memory", "story_underfilled", chapter=next_ch,
                                  used=len(story_so_far), budget=_ssf_budget)
                if bible_dropped:   # 설정집 다이제스트 예산 컷 가시화(silent drop 금지)
                    sess.bus.emit("assemble_memory", "bible_truncated", chapter=next_ch, dropped=bible_dropped)
                if spine and spine.arcs:
                    _out = _outstanding_plants(spine)
                    if len(_out) >= self.settings.plant_backlog_threshold:   # 복선 적체 경보(advisory — 작가 가시화)
                        sess.bus.emit("narrative", "plant_backlog", chapter=next_ch, outstanding=_out[:8])
                    if tele["open"]:   # G1: 약속 원장 텔레메트리(미지불 잔고·지불 경과 — 회차 이벤트 스트림에 노출)
                        sess.bus.emit("ledger", "promise_state", chapter=next_ch, **tele)
                # 작품 완결 화 감지: ①스파인 소진(마지막 아크의 마지막 에피소드 finale) 또는 ②목표 회차 도달
                # — 어느 쪽이든 마지막으로 나가는 화는 절단신공 대신 '닫는' 회차(미결 선택은 결행, 새 떡밥 금지)
                target = state.seed.target_chapters or 12
                closing = bool(next_ch >= target or (spine and ep and is_finale
                               and not any((not a.done) and a.arc_id != ep.arc_id for a in spine.arcs)
                               and not any((not e2.done) and e2.episode_id != ep.episode_id
                                           for a in spine.arcs for e2 in a.episodes)))
                recent_tails = [c.text[-160:] for c in prior[-3:] if c.text]   # 훅 유형 로테이션 재료
                # 전권 틱 원장(작품-전역 품질 상태): 지난 회차 전체에서 과용된 습관구 → 이번 화 절제 목록(예방측)
                from ..engine.quality_gates import word_tics as _wt
                roster_names = {e.name for e in sess.bundle.ontology.entities.values()}
                roster_names |= {k for ent in state.bible.entries for k in (ent.keywords or [])}   # 세계관 고유어 제외(데이터 주도 — 코드 사전 금지)
                corpus = " ".join(c.text for c in prior[-8:] if c.text)
                restraint = [p for p, n in _wt(corpus, roster_names, cap=12)][:8]
                if prior:   # 틱 모방-증폭 루프 차단(재설계): 직전 화 원문 주입이 말버릇을 '문체'로 학습시키는 고리를 명시 절제로 끊는다
                    restraint += [w for w, _ in _wt(prior[-1].text or "", roster_names, cap=3)]
                    restraint = list(dict.fromkeys(restraint))[:10]
                record = sess.bundle.generator.generate(
                    next_ch, beat.model_dump(), sess.bundle.ontology, sess.bundle.rag,
                    sess.bundle.wiki, directives=active, prev_chapter_text=prev_text,
                    story_so_far=story_so_far, anchors=anchors, closing=closing,
                    recent_tails=recent_tails, restraint=restraint)

                # 디버그: 계획(설계) 입력을 회차 컨텍스트에 합침 — '어떤 정보로 설계·집필했는지' 추적
                if spine and spine.arcs and isinstance(record.gen_context, dict):
                    _gc = getattr(state.world, "genre_contract", None)
                    record.gen_context["plan"] = {
                        "arc": (arc.title if arc else ""), "episode": (ep.title if ep else ""),
                        "is_finale": is_finale, "recent": summaries,
                        "cast_context": (cast_context or "")[:1400],
                        "plant_notes": (plant_notes or "")[:400],
                        "restraint": list(restraint or [])[:10],
                        "genre_contract": (_gc.model_dump() if _gc else None),
                    }

                # 동적 온톨로지 업데이트(엔진 고도화) — FINALIZED 회차에만
                if record.status == ChapterStatus.FINALIZED:
                    _t0 = sess.provider.usage.chat_tokens
                    for eid in sess.bundle.ontology.scan_present_ids(record.text):   # 데뷔 완료 마킹(이후 앵커 중복 방지)
                        e = next((x for x in state.world.entities if x.id == eid), None)
                        if e is not None and not getattr(e, "introduced", False):
                            e.introduced = True
                    proposal = sess.bundle.updater.propose(
                        record.text, sess.bundle.ontology, next_ch,
                        existing_setting_titles=[b.title for b in state.bible.entries if b.status != "deprecated"])
                    record.usage_by_stage["ontology_propose"] = sess.provider.usage.chat_tokens - _t0
                    changes, new_specs, new_tl, new_edges = sess.bundle.updater.apply(
                        proposal, sess.bundle.ontology, next_ch)
                    record.ontology_changes = changes
                    state.runtime_entities += new_specs
                    state.runtime_timeline += new_tl
                    state.runtime_edges += new_edges          # 자동추출 관계(narrative_inferred) 영속
                    # 설정집 연재 증분: 회차에서 드러난 세계 설정 → 미승인 초안으로만 append(작가 promote 게이트 보존)
                    existing_titles = {b.title.strip() for b in state.bible.entries}
                    for ns in (proposal.get("new_settings") or [])[:2]:
                        t = (ns.get("title") or "").strip()
                        if t and t not in existing_titles:
                            existing_titles.add(t)
                            state.bible.entries.append(BibleEntry(
                                entry_id=_slug(t, {e.entry_id for e in state.bible.entries}),
                                category=normalize_category(ns.get("category")), title=t,
                                prose=(ns.get("prose") or "").strip(),
                                keywords=[k for k in (ns.get("keywords") or []) if k][:5],
                                provenance="ai_worldgen", status="ai_unreviewed"))
                    state.current_chapter = next_ch
                    self._esc.pop((pid, next_ch), None)       # 성공 → 연속 escalation 카운터 리셋
                    # G1-P2+P3: 본문 한 번 읽고 (지불된 기존 약속 + 새로 연 약속) 정산(측정 — 생성 주입 아님, 추가 콜 0).
                    # 원장을 설계 라벨이 아니라 '본문이 실제로 한 약속'으로 채운다 → since_payoff·잔고가 실데이터.
                    if spine:
                        from ..engine.ledger_ops import reconcile_ledger_from_prose, mark_paid, add_opened_promises
                        _tp = sess.provider.usage.chat_tokens
                        recon = reconcile_ledger_from_prose(sess.provider, record.text,
                                                            state.promise_ledger.open_promises(), next_ch)
                        record.usage_by_stage["ledger_reconcile"] = sess.provider.usage.chat_tokens - _tp
                        n_paid = mark_paid(state.promise_ledger, recon["paid"], next_ch)
                        n_open = add_opened_promises(state.promise_ledger, recon["opened"], next_ch)
                        if n_paid or n_open:
                            sess.bus.emit("ledger", "reconciled", chapter=next_ch,
                                          paid=n_paid, opened=n_open)
                    # G2: 블라인드 장르 독자 행동 예측(advisory — 비차단·비강제, 작가 가시화). 비트/설계 미공개로 본문만 읽음.
                    if getattr(self.settings, "reader_desk", True):
                        from ..engine.reader_desk import reader_prediction
                        _tr = sess.provider.usage.chat_tokens
                        _gc = getattr(state.world, "genre_contract", None)
                        pred = reader_prediction(sess.provider, record.text, story_so_far, state.world.genre,
                                                 expectations=(_gc.reader_expectations if _gc else None))
                        record.usage_by_stage["reader_desk"] = sess.provider.usage.chat_tokens - _tr
                        if pred:
                            record.reader_feedback = pred           # 작가가 나중에 검토(원장처럼 가시화)
                            sess.bus.emit("reader_desk", "prediction", chapter=next_ch, **pred)
                    # AI 티 분포 신호(결정론·LLM 0콜·advisory 추세) — 판정·차단 아님, 작가 가시화(측정-주도, no-whack-a-mole)
                    record.ai_tell = self._recompute_ai_tell(state, sess, record.text)
                    # G7: 신규 고유명사 커밋 가시화(인플레 추세 — 작가 신호, 차단 아님)
                    new_ents = [c for c in changes if getattr(c, "op", "") == "new_entity"]
                    if new_ents:
                        sess.bus.emit("naming", "new_commits", chapter=next_ch, count=len(new_ents),
                                      names=[c.entity for c in new_ents][:8])
                    # G3-텔레메트리: 롤링 윈도 페이싱 지표(측정·가시화만 — 작가가 추세 보고 빨간펜, 강제 없음)
                    if spine:
                        from ..engine.pacing import pacing_window
                        sess.bus.emit("pacing", "window", chapter=next_ch,
                                      **pacing_window(prior + [record], state.promise_ledger, next_ch))
                    # R4: 에피소드 커서 전진 + finale 시 롤업 요약·결정론 드리프트(advisory)
                    if spine and ep:
                        record.arc_id, record.episode_id = ep.arc_id, ep.episode_id
                        state.narrative_progress.chapters_in_episode += 1
                        if is_finale:
                            ep.done = True
                            ep_chs = [c for c in state.chapters if c.episode_id == ep.episode_id] + [record]
                            ep.summary = _rollup_episode(sess.provider, ep, ep_chs)
                            record.drift_signals = episode_drift_signals(
                                ep, [c.text for c in ep_chs], sess.bundle.ontology)
                            for dsig in record.drift_signals:
                                sess.bus.emit("drift", "signal", chapter=next_ch, detail=dsig)
                            # G3: 아크 완결 → 회고 권유(작가 가시화 nudge, 강제 아님). 회고는 작가가 받고 개정 승인.
                            arc_obj = spine.arc(ep.arc_id)
                            if arc_obj and arc_obj.episodes and all(e.done for e in arc_obj.episodes):
                                sess.bus.emit("narrative", "retrospective_available",
                                              chapter=next_ch, arc=arc_obj.title)
                elif spine and spine_snap is not None:
                    # ESCALATED: 커서/spine 변이 롤백(재시도 결정성·orphan 에피소드/조기 arc.done 영속 방지)
                    state.narrative_progress = prog_snap
                    state.world.spine = spine_snap
                    k = (pid, next_ch)                         # #11: 같은 회차 연속 ESCALATED → 갇힘 경보(가시화)
                    self._esc[k] = self._esc.get(k, 0) + 1
                    if self._esc[k] >= 2:
                        sess.bus.emit("narrative", "episode_stuck", chapter=next_ch,
                                      attempts=self._esc[k], episode=(ep.episode_id if ep else None),
                                      recovery=getattr(record, "recovery_hints", []))   # 갇힘 경보에 회복 안내 동봉

                # 기존 회차 재생성이면 교체, 아니면 추가
                state.chapters = [c for c in state.chapters if c.chapter != next_ch] + [record]
                state.chapters.sort(key=lambda c: c.chapter)

                delta = _usage_delta(before, sess.provider.usage.as_dict())
                state.usage_total = _accumulate(state.usage_total, delta)
                sess.snapshot_into(state)
                self.repo.save(state)
                saved = True

                failures = sess.bus.failures()
                return {"record": record, "events": list(sess.bus.buffer), "usage_delta": delta,
                        "usage_total": state.usage_total, "failures": failures, "completed": False,
                        "current_chapter": state.current_chapter,
                        "total_beats": (state.seed.target_chapters or len(state.world.beats))}
            except Exception:
                if not saved:   # save 전 실패만 롤백 — save 후 예외는 디스크가 권위(롤백 금지)
                    _fail_rollback()
                raise

    # ---- 인스펙터 ----
    def ontology_snapshot(self, pid: str) -> dict | None:
        sess_state = self.get_session(pid)
        sess, state = sess_state
        if not sess:
            return None
        ont = sess.bundle.ontology
        chapter = state.current_chapter + 1
        chars = []
        for e in ont.entities.values():
            if e.etype != "character":
                continue
            facts = {a: ont.state_as_of(e.id, a, chapter) for a in e.attrs}
            chars.append({"id": e.id, "name": e.name, "aliases": e.aliases,
                          "status": ont.state_as_of(e.id, "status", chapter),
                          "attrs": facts, "provisional": e.provisional})
        return {"as_of_chapter": chapter, "characters": chars, "rules": ont.rules,
                "timeline": [{"entity": ont.entities[t[0]].name if t[0] in ont.entities else t[0],
                              "attr": t[1], "value": t[2], "eff_from": t[3], "reason": t[4],
                              "trust_tier": (t[5] if len(t) > 5 else "ground_truth")}
                             for t in ont.timeline],
                "graph": self._graph_payload(ont, chapter)}

    def _graph_payload(self, ont, chapter: int) -> dict:
        """R1 시각화 payload — nodes/edges + 타입·관계 카탈로그(스타일 데이터주도). 현재 시점 그래프."""
        types = ont.entity_types
        nodes = []
        for e in ont.entities.values():
            t = types.get(e.etype)
            nodes.append({"id": e.id, "name": e.name, "etype": e.etype,
                          "type_label": t.label if t else e.etype,
                          "color": t.color if t else "#6aa9ff",
                          "shape": t.shape if t else "ellipse",
                          "dead": ont.state_as_of(e.id, "status", chapter) == "dead",
                          "provisional": e.provisional})
        edges = []
        for ed in ont.active_edges_deduped(chapter):   # (src,dst,rel)당 1엣지로 접어 그래프 노이즈 억제
            spec = ont.rel_catalog.get(ed.rel_id)
            edges.append({"id": ed.edge_id or f"{ed.rel_id}:{ed.src_id}->{ed.dst_id}",
                          "src": ed.src_id, "dst": ed.dst_id, "rel_id": ed.rel_id,
                          "label": spec.label if spec else ed.rel_id,
                          "color": spec.color if spec else "#888888",
                          "line_style": spec.line_style if spec else "solid",
                          "directed": spec.directed if spec else True,
                          "trust_tier": ed.trust_tier, "eff_from": ed.eff_from})
        return {"nodes": nodes, "edges": edges,
                "types": [t.model_dump() for t in types.values()],
                "relations": [r.model_dump() for r in ont.rel_catalog.values()],
                "max_chapter": chapter}

    def spine_snapshot(self, pid: str) -> dict | None:
        """서사 구조(R4) — 엔딩·아크·에피소드·현재 커서. UI '서사 구조' 뷰용."""
        sess, state = self.get_session(pid)
        if not sess:
            return None
        spine = state.world.spine
        if not spine:
            return {"has_spine": False}
        prog = state.narrative_progress
        from ..engine.ledger_ops import ledger_telemetry, outstanding as _outstanding
        cur1 = state.current_chapter + 1
        return {
            "has_spine": True, "completed": prog.completed,
            "ending": spine.ending.model_dump() if spine.ending else None,
            "current_arc_id": prog.current_arc_id, "current_episode_id": prog.current_episode_id,
            "chapters_in_episode": prog.chapters_in_episode,
            "promise_ledger": ledger_telemetry(state.promise_ledger, cur1),   # G1: 재미 회계 가시화(요약)
            "open_promises": [{"text": p.text, "opened_chapter": p.opened_chapter,
                               "age": cur1 - p.opened_chapter, "kind": p.kind}
                              for p in _outstanding(state.promise_ledger, cur1)][:12],
            "promises_all": [{"o": p.opened_chapter, "p": p.paid_chapter}
                             for p in state.promise_ledger.promises],   # 잔고 추세 차트용(개설/지불 회차)
            "genre_contract": (state.world.genre_contract.model_dump()
                               if getattr(state.world, "genre_contract", None) else None),   # G5 가시화
            "arcs": [{"arc_id": a.arc_id, "title": a.title, "goal": a.goal, "done": a.done,
                      "episodes": [{"episode_id": e.episode_id, "title": e.title, "climax": e.climax,
                                    "target_chapters": e.target_chapters, "done": e.done,
                                    "required_cast": [sess.bundle.ontology.name(c) for c in e.required_cast],
                                    "summary": e.summary} for e in a.episodes]}
                     for a in sorted(spine.arcs, key=lambda x: x.order)],
        }

    # ---- G3: 연재 회고·스파인 개정(거버넌스 — 제안은 시스템, 적용은 작가 승인) ----
    def arc_retrospective(self, pid: str) -> dict | None:
        """연재 회고 제안 — 페이싱 지표+완결 아크로 진단하고 '남은 아크/엔딩' 개정안을 제안(미적용·읽기 전용)."""
        state = self.repo.get(pid)
        if not state:
            return None
        spine = state.world.spine
        if not spine or not spine.arcs:
            return {"has_spine": False, "diagnosis": "", "revisions": []}
        from ..engine.pacing import pacing_window
        from ..engine.retrospective import generate_retrospective
        sess = self.sessions.get_or_create(state)
        cur = state.current_chapter
        done_arcs = [{"arc_id": a.arc_id, "title": a.title, "goal": a.goal, "summary": a.summary}
                     for a in sorted(spine.arcs, key=lambda x: x.order) if a.done]
        upcoming = [{"arc_id": a.arc_id, "title": a.title, "goal": a.goal,
                     "central_conflict": a.central_conflict, "turning_point": a.turning_point}
                    for a in sorted(spine.arcs, key=lambda x: x.order) if not a.done]
        pacing = pacing_window(state.chapters, state.promise_ledger, cur + 1, window=8)
        ledger_open = [p.text for p in state.promise_ledger.open_promises()][:10]
        reader_trend = [c.reader_feedback for c in state.chapters[-5:] if getattr(c, "reader_feedback", None)]
        prop = generate_retrospective(
            sess.provider, genre=state.world.genre,
            ending=(spine.ending.ending if spine.ending else ""),
            done_arcs=done_arcs, upcoming_arcs=upcoming, pacing=pacing,
            ledger_open=ledger_open, reader_trend=reader_trend)
        prop["has_spine"] = True
        prop["pacing"] = pacing
        return prop

    def backfill_genre_contract(self, pid: str) -> dict | None:
        """M-2: G5 이전 작품에 장르 계약이 없으면 추론해 채운다(작가 요청 시 1회). narrative 컨텍스트 — 캐논 아님."""
        state = self.repo.get(pid)
        if not state:
            return None
        if getattr(state.world, "genre_contract", None):
            return {"already": True, "genre_contract": state.world.genre_contract.model_dump()}
        from ..worldgen.genre_contract import infer_genre_contract
        gc = infer_genre_contract(self.wg_provider, state.world)
        if gc is None:
            raise ValueError("장르 계약 추론에 실패했습니다(다시 시도)")
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)               # 권위 재읽기(lost update 방지)
            if not state:
                return None
            if getattr(state.world, "genre_contract", None):
                return {"already": True, "genre_contract": state.world.genre_contract.model_dump()}
            state.world.genre_contract = gc
            self.repo.save(state)
            return {"created": True, "genre_contract": gc.model_dump()}

    def revise_spine(self, pid: str, revisions: list[dict]) -> dict | None:
        """작가 승인된 개정만 반영 — 미집필(미완) 아크 카드/엔딩만(과거·집필분 보호). 트랜잭션 안전.
        narrative 슬롯(서사 의도)이라 결정론 게이트/캐논 무접촉. 다음 lazy 에피소드 생성이 개정된 목표를 본다."""
        from ..engine.retrospective import ARC_FIELDS, ENDING_FIELDS
        from ..domain.narrative import EndingSpec
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)               # 권위 재읽기(lost update 방지)
            if not state:
                return None
            spine = state.world.spine
            if not spine:
                raise ValueError("스파인이 없습니다")
            applied, rejected = [], []
            for rv in (revisions or []):
                target, field, nv = (rv.get("target") or ""), (rv.get("field") or ""), (rv.get("new_value") or "").strip()
                if not nv:
                    rejected.append({**rv, "why": "빈 값"}); continue
                if target == "ending" and field in ENDING_FIELDS:
                    if spine.ending is None:
                        spine.ending = EndingSpec()
                    setattr(spine.ending, field, nv)
                    applied.append({"target": "ending", "field": field})
                elif target.startswith("arc:") and field in ARC_FIELDS:
                    arc = spine.arc(target[4:])
                    if arc is None:
                        rejected.append({**rv, "why": "아크 없음"})
                    elif arc.done:
                        rejected.append({**rv, "why": "이미 집필된 아크는 개정 불가(미래만)"})
                    else:
                        setattr(arc, field, nv)
                        applied.append({"target": target, "field": field})
                else:
                    rejected.append({**rv, "why": "허용되지 않은 대상/필드"})
            if applied:
                self.repo.save(state)
            return {"applied": applied, "rejected": rejected}

    # ---- R2 설정집 ----
    def _ensure_migrated(self, state) -> bool:
        """기존 프로젝트 1회 부트스트랩(이미 캐논인 world_rules 를 설정집에 표시). 변경 시 True."""
        if not state.bible_migrated:
            if not state.bible.entries and state.world.world_rules:
                state.bible.entries = migrate_world_to_bible(state.world)
            state.bible_migrated = True
            return True
        return False

    def bible_snapshot(self, pid: str) -> dict | None:
        state = self.repo.get(pid)
        if not state:
            return None
        if not state.bible_migrated:     # 최초 1회 부트스트랩만 lock 안에서(GET 경로 무락 쓰기 = 동시 promote 와 lost-update 위험 제거)
            sess = self.sessions.get_or_create(state)
            with sess.lock:
                state = self.repo.get(pid)
                if not state:
                    return None
                if self._ensure_migrated(state):
                    self.repo.save(state)
        return {"genre": state.world.genre, "template": template_for(state.world.genre),
                "category_labels": CATEGORY_LABEL,
                "entries": [{"entry_id": e.entry_id, "category": e.category,
                             "category_label": CATEGORY_LABEL.get(e.category, e.category),
                             "title": e.title, "prose": e.prose, "promoted": e.promoted,
                             "keywords": list(getattr(e, "keywords", []) or []),
                             "provenance": e.provenance, "status": e.status} for e in state.bible.entries]}

    def add_bible_entry(self, pid: str, category: str, title: str, prose: str = "") -> dict | None:
        state = self.repo.get(pid)
        if not state:
            return None
        title = (title or "").strip()
        if not title:
            raise ValueError("제목이 비었습니다")
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                return None
            eid = _slug(title, {e.entry_id for e in state.bible.entries})
            entry = BibleEntry(entry_id=eid, category=normalize_category(category), title=title, prose=prose,
                               provenance="author", status="author_approved", promoted=False)
            state.bible.entries.append(entry)
            self.repo.save(state)
            return entry.model_dump()

    def update_bible_entry(self, pid: str, entry_id: str, title=None, prose=None, category=None) -> dict | None:
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                return None
            e = state.bible.get(entry_id)
            if not e:
                raise ValueError("설정집 항목 없음")
            if title is not None:
                e.title = title
            if prose is not None:
                e.prose = prose
            if category:
                e.category = normalize_category(category)
            e.status = "author_approved"
            self.repo.save(state)
            return e.model_dump()

    def _demote_rule(self, state, sess, rule_id: str, exclude_entry_id: str | None = None) -> bool:
        """promote 역연산 — world_rules + 라이브 엔진 3미러에서 해당 규칙 제거(orphan 캐논 방지).
        refcount: 같은 world_rule_id 를 참조하는 '다른' promoted 설정집 항목이 남아 있으면 캐논을 제거하지 않는다
        (공유룰 재사용 후 한 항목 삭제가 나머지 항목의 캐논을 빼앗던 orphan 결함 교정). 실제 제거 시 True."""
        still_referenced = any(b.promoted and b.world_rule_id == rule_id and b.entry_id != exclude_entry_id
                               for b in state.bible.entries)
        if still_referenced:
            return False
        rule = next((r for r in state.world.world_rules if r.rule_id == rule_id), None)
        state.world.world_rules = [r for r in state.world.world_rules if r.rule_id != rule_id]
        if rule is not None:
            sess.bundle.ontology.remove_rule(rule.text)
        sess.bundle.checker.rule_engine.remove_rule(rule_id)
        sess.bundle.checker.extractor.remove_world_rule(rule_id)
        return rule is not None

    def delete_bible_entry(self, pid: str, entry_id: str) -> dict:
        state = self.repo.get(pid)
        if not state:
            return {"deleted": False}
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                return {"deleted": False}
            e = state.bible.get(entry_id)
            demoted = False
            if e and e.promoted and e.world_rule_id:     # 캐논으로 박힌 항목 → 연결 world_rule 까지(공유 시 refcount)
                demoted = self._demote_rule(state, sess, e.world_rule_id, exclude_entry_id=entry_id)
            n0 = len(state.bible.entries)
            state.bible.entries = [x for x in state.bible.entries if x.entry_id != entry_id]
            self.repo.save(state)
            return {"deleted": len(state.bible.entries) < n0, "demoted": demoted}   # 실제 캐논 제거 여부(거짓보고 제거)

    def promote_bible_entry(self, pid: str, entry_id: str) -> dict | None:
        """'캐논으로 박기' — 설정집 항목 → world_rule 승격(작가 승인 게이트, 비대칭 보존).
        주의(강제력): 세계규칙 위반은 SignalGrade.SEMANTIC(LLM 판단)이라 '하드 게이트'(자동 재작성/ESCALATED)가
        아니라 추적·프롬프트 주입(advisory)이다. 하드 캐논(위반 시 재작성/차단)은 관계 엣지·상태 등 det/quasi 신호뿐."""
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                return None
            e = state.bible.get(entry_id)
            if not e:
                raise ValueError("설정집 항목 없음")
            if e.promoted:
                return {"promoted": True, "already": True}
            # 동일 text 의 기존 world_rule 재사용(orphan 재promote 중복 방지)
            existing = next((r for r in state.world.world_rules if r.text == (e.prose or e.title).strip()), None)
            rule = existing or entry_to_world_rule(e, {r.rule_id for r in state.world.world_rules})
            if existing is None:
                state.world.world_rules.append(rule)
            e.promoted, e.promote_target, e.status, e.world_rule_id = True, "world_rule", "author_approved", rule.rule_id
            # 라이브 엔진 즉시 반영 — build_engine 의 world_rule 처리 미러(멱등 가드)
            rule_ids = {r.rule_id for r in sess.bundle.checker.rule_engine.rules}
            if rule.rule_id not in rule_ids:
                sess.bundle.ontology.add_rule(rule.text)
                sess.bundle.checker.rule_engine.rules.append(RuleSpec(
                    rule_id=rule.rule_id, layer="worldrule", predicate_kind="worldrule_flag",
                    grade=SignalGrade.SEMANTIC, params={"flag": rule.flag, "rule_keywords": rule.keywords}))
                sess.bundle.checker.extractor.world_rules.append(rule)
            sess.snapshot_into(state)
            self.repo.save(state)
            return {"promoted": True, "rule_id": rule.rule_id, "title": e.title}

    # ---- 작가 상태 정정(③ 입력 전용) — 낡은/틀린 캐논 속성을 작가가 직접 박는 레버 ----
    def set_entity_state(self, pid: str, entity_id: str, attr: str, value, eff_from: int = 1,
                         reason: str = "작가 정정") -> dict | None:
        st = self.repo.get(pid)
        if not st:
            return None
        sess = self.sessions.get_or_create(st)
        with sess.lock:
            st = self.repo.get(pid)
            if not st:
                return None
            ont = sess.bundle.ontology
            if entity_id not in ont.entities:
                raise ValueError("존재하지 않는 엔티티")
            spec = sess.bundle.vocab.attr(attr)
            sval = str(value).strip()
            if spec and spec.kind == "categorical" and spec.vocab and sval not in spec.vocab:
                raise ValueError(f"'{attr}' 값은 {spec.vocab} 중 하나여야 합니다")
            if spec and spec.kind in ("state", "status") and spec.states and sval not in spec.states:
                raise ValueError(f"'{attr}' 상태는 {spec.states} 중 하나여야 합니다")
            eff = max(1, int(eff_from))
            cur = ont.binding_state_as_of(entity_id, attr, eff)
            irr = sess.bundle.vocab.irreversible_states(attr)
            if (cur is not None and str(cur) in irr and sval != str(cur)
                    and not getattr(st.world, "allow_state_reversal", False)):
                raise ValueError(f"'{cur}'은(는) 비가역 상태입니다(allow_state_reversal 세계에서만 정정 가능)")
            ont.entities[entity_id].attrs.setdefault(attr, None)   # 주입집합=게이트집합
            ont.set_state(entity_id, attr, sval, eff, reason=reason, trust_tier="ground_truth")
            from ..domain.world import TimelineEntry
            st.runtime_timeline.append(TimelineEntry(entity_id=entity_id, attr=attr, value=sval,
                                                     eff_from=eff, reason=reason, trust_tier="ground_truth"))
            sess.snapshot_into(st)
            self.repo.save(st)
            return {"updated": True, "entity": ont.name(entity_id), "attr": attr,
                    "value": sval, "eff_from": eff}

    # ---- 문체/생성 정책 편집(③ 작가 입력 전용 — 시스템 스티어링 제어 경로) ----
    def update_style_policy(self, pid: str, patch: dict) -> dict | None:
        """ending_hook/plant_reminder/persona/분량/장면수/문체규칙을 작가가 제품에서 직접 제어.
        적용 후 세션 evict → 다음 요청이 새 정책으로 엔진 재구성(생성 중에는 lock 이 직렬화)."""
        st = self.repo.get(pid)
        if not st:
            return None
        sess = self.sessions.get_or_create(st)
        with sess.lock:
            st = self.repo.get(pid)
            if not st:
                return None
            style = st.world.style
            if patch.get("ending_hook") is not None:
                if patch["ending_hook"] not in ("cliffhanger", "soft", "none"):
                    raise ValueError("ending_hook 은 cliffhanger|soft|none")
                style.ending_hook = patch["ending_hook"]
            if patch.get("plant_reminder") is not None:
                if patch["plant_reminder"] not in ("off", "gentle", "active"):
                    raise ValueError("plant_reminder 는 off|gentle|active")
                st.world.plant_reminder = patch["plant_reminder"]
            if patch.get("system_persona") is not None:
                style.system_persona = patch["system_persona"]
            if patch.get("author_style") is not None:
                style.author_style = (patch["author_style"] or "").strip()[:2000]   # 빈 문자열=오버레이 해제, 매 draft 헤더 주입이라 길이 cap

            if patch.get("target_chars_per_chapter") is not None:
                style.target_chars_per_chapter = max(500, min(20000, int(patch["target_chars_per_chapter"])))
            if patch.get("scenes_per_chapter") is not None:
                style.scenes_per_chapter = max(1, min(8, int(patch["scenes_per_chapter"])))
            if patch.get("rules") is not None:
                style.rules = [r for r in patch["rules"] if r]
            if patch.get("allow_state_reversal") is not None:
                st.world.allow_state_reversal = bool(patch["allow_state_reversal"])
            self.repo.save(st)
        self.sessions.evict(pid)   # 다음 요청부터 새 정책으로 엔진 재구성
        return {"updated": True, "style": style.model_dump(),
                "plant_reminder": st.world.plant_reminder,
                "allow_state_reversal": st.world.allow_state_reversal}

    # ---- R3 협업형 월드젠 대화 ----
    def worldgen_chat_log(self, pid: str) -> dict | None:
        state = self.repo.get(pid)
        return None if not state else {"chat": state.worldgen_chat}

    def worldgen_turn(self, pid: str, message: str) -> dict | None:
        """대화 한 턴 — AI 응답 + 신규 엔티티/관계/설정집 제안을 결정론 게이트로 커밋(genesis=캐논). 모순은 blocked."""
        from ..engine.ontology import Entity
        message = (message or "").strip()
        if not message:
            raise ValueError("메시지가 비었습니다")
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                return None
            ont = sess.bundle.ontology
            res = WorldgenChat(sess.provider).turn(state.world, ont, state.bible, state.worldgen_chat, message)
            applied, blocked = [], []
            added_eids: list[str] = []        # 저장 실패 시 캐시 온톨로지 롤백용(유령 노드/엣지 방지)
            added_edge_ids: list[str] = []
            amap = ont.alias_map()
            eff = max(1, state.current_chapter)   # 효력 시점(genesis=1, 진행 중이면 현재 회차부터)
            # 1) 신규 엔티티 → provisional(AI 제안 = 잠정). 작가가 그래프에서 확정(promote)하면 캐논화. 비대칭 보존.
            for ne in (res.get("new_entities") or [])[:8]:
                name = (ne.get("name") or "").strip()
                if not name:
                    continue
                if name in amap:   # 이미 존재 → 정산에 명시(applied/blocked 양쪽에서 증발 방지 — 관측성)
                    blocked.append({"kind": "entity", "reason": "이미 존재하는 엔티티", "detail": name})
                    continue
                etype = (ne.get("etype") or "character").strip() or "character"
                unknown = etype not in ont.entity_types
                sid = _slug(name, set(ont.entities))
                ont.add(Entity(id=sid, name=name, etype=etype, attrs={}, aliases=[], provisional=True))
                added_eids.append(sid)
                amap[name] = sid
                state.runtime_entities.append(EntitySpec(id=sid, name=name, etype=etype, attrs={}, provisional=True))
                applied.append({"kind": "entity", "name": name, "etype": etype, "unknown_type": unknown})
            # 2) 관계 → narrative_inferred(AI 제안 = 비binding). 작가가 그래프에서 직접 그으면 ground_truth 승격.
            def resolve(x):
                x = (x or "").strip()
                return x if x in ont.entities else amap.get(x)
            for nr in (res.get("new_relations") or [])[:8]:
                rel = (nr.get("rel_id") or "").strip()
                src, dst = resolve(nr.get("src")), resolve(nr.get("dst"))
                if not rel:   # 자유 타입 허용 — 카탈로그 FK 검사 폐기(미등록 타입도 동작)
                    blocked.append({"kind": "relation", "reason": "관계 타입 누락", "detail": str(nr)}); continue
                if not src or not dst:
                    blocked.append({"kind": "relation", "reason": "엔티티 미해결(명부에 없음)", "detail": str(nr)}); continue
                src, dst = ont.order_edge(rel, src, dst)        # 대칭 관계 정렬 → A↔B 중복 방지
                if src == dst:
                    blocked.append({"kind": "relation", "reason": "자기참조 관계", "detail": str(nr)}); continue
                rstate = (nr.get("state") or "").strip()
                edge_id = f"{rel}:{src}->{dst}:{eff}"
                if any(e.edge_id == edge_id for e in ont.edges):
                    blocked.append({"kind": "relation", "reason": "이미 존재하는 관계", "detail": str(nr)})
                    continue
                edge = RelationEdge(edge_id=edge_id, rel_id=rel, src_id=src, dst_id=dst, state=rstate, eff_from=eff,
                                    trust_tier="narrative_inferred", provenance=["ai_worldgen"])
                ont.add_edge(edge)
                added_edge_ids.append(edge.edge_id)
                state.runtime_edges.append(edge)
                applied.append({"kind": "relation", "label": ont.rel_spec(rel).label,
                                "src": ont.name(src), "dst": ont.name(dst), "state": rstate})
            # 3) 설정집 항목(narrative, 작가가 promote 하면 캐논). 동일 제목 중복 방지.
            existing_titles = {b.title.strip() for b in state.bible.entries if b.status != "deprecated"}
            for nb in (res.get("new_bible") or [])[:6]:
                title = (nb.get("title") or "").strip()
                if not title or title in existing_titles:
                    continue
                existing_titles.add(title)
                eid = _slug(title, {e.entry_id for e in state.bible.entries})
                state.bible.entries.append(BibleEntry(entry_id=eid, category=normalize_category(nb.get("category")),
                                                      title=title, prose=(nb.get("prose") or "").strip(),
                                                      provenance="ai_worldgen", status="ai_unreviewed"))
                applied.append({"kind": "bible", "title": title})
            reply = res.get("reply", "")
            state.worldgen_chat.append({"role": "author", "text": message})
            state.worldgen_chat.append({"role": "ai", "text": reply})
            state.worldgen_chat = state.worldgen_chat[-60:]   # 영속 로그 cap(무한 누적 방지)
            try:
                sess.snapshot_into(state)
                self.repo.save(state)
            except Exception:   # 저장 실패 → 캐시 온톨로지에서 방금 추가분 제거(메모리↔디스크 불일치/유령 방지)
                for eid in added_eids:
                    ont.entities.pop(eid, None)
                if added_edge_ids:
                    drop = set(added_edge_ids)
                    ont.edges = [e for e in ont.edges if e.edge_id not in drop]
                raise
            return {"reply": reply, "applied": applied, "blocked": blocked,
                    "questions": res.get("questions", []) or []}

    def wiki_snapshot(self, pid: str) -> dict | None:
        sess, state = self.get_session(pid)
        if not sess:
            return None
        wm = state.current_chapter
        pages = [WikiPage.model_validate(p).model_dump() if not isinstance(p, WikiPage) else p.model_dump()
                 for p in sess.bundle.wiki.export_pages()]
        lint = [v.model_dump() for v in sess.bundle.wiki.lint(wm)]
        return {"watermark": wm, "pages": pages, "lint": lint}
