# -*- coding: utf-8 -*-
"""ArcPlanner (R4) — 엔딩을 먼저 고정하고 역순(backward)으로 아크/에피소드 설계 + 회차 beat 파생.

'사람 작가의 사고를 더 탄탄하게': 인간은 다다음 에피소드까지만 보지만 AI는 엔딩을 고정하고
아크를 역설계한 뒤, 에피소드/회차를 lazy 하게 채운다. 절정(climax)을 먼저 정하고 거기로 수렴.
복선은 추적만(plants/payoffs), 마감 강제 없음. 아크/에피소드 목표는 narrative(서사 의도)지 ground_truth 아님.
"""
from __future__ import annotations
import json

import re as _re

from ..domain.world import WorldConfig, Beat, EntitySpec
from ..domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec, NarrativeProgress
from ..llm.base import LLMProvider


def _contract_block(world: WorldConfig) -> str:
    """G5: 장르 계약을 '서술 정보'로 렌더(강제 아님) — 설계가 같은 쾌감 엔진·전제 자산을 보게(드리프트·전제소모 차단)."""
    gc = getattr(world, "genre_contract", None)
    if not gc:
        return ""
    parts = []
    if gc.pleasure_engine:
        parts.append(f"독자 쾌감: {gc.pleasure_engine}")
    if gc.reader_expectations:
        parts.append("독자 기대: " + ", ".join(gc.reader_expectations[:5]))
    if gc.vocabulary_tone:
        parts.append(f"어휘·톤: {gc.vocabulary_tone}")
    if gc.premise_asset:
        parts.append(f"핵심 동력 전제(장기 자산): {gc.premise_asset}")
    return ("[이 작품의 장르 정체성 — 참고]\n" + "\n".join(parts) + "\n") if parts else ""


class ArcPlanner:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    # ---- 1) 엔딩-주도 spine 생성(작품 시작 시 1회) ----
    @staticmethod
    def _spine_gaps(raw: dict) -> list[str]:
        """'엔딩 먼저' 계약 완결성 검사(G8) — 비어 있는 필수 항목을 반환(구조 검증, 창작 강제 아님).
        엔딩/중심질문/아크 목표/첫 아크 에피소드 절정이 빈 값으로 통과하면 매 회차가 '결말 없는 질문'만 보고 쓴다."""
        gaps: list[str] = []
        end = raw.get("ending") or {}
        if not (end.get("ending") or "").strip():
            gaps.append("ending.ending(확정 결말)")
        if not (end.get("central_question") or "").strip():
            gaps.append("ending.central_question(중심 질문)")
        arcs = raw.get("arcs") or []
        if not arcs:
            gaps.append("arcs(아크 0개)")
            return gaps
        for i, a in enumerate(arcs, 1):
            if not (a.get("goal") or "").strip():
                gaps.append(f"arc{i}.goal")
        eps = (arcs[0].get("episodes") or [])
        if not eps:
            gaps.append("arc1.episodes(첫 아크 미분해)")
        else:
            for j, e in enumerate(eps, 1):
                if not (e.get("climax") or "").strip():
                    gaps.append(f"arc1.ep{j}.climax")
        return gaps

    def build_spine(self, world: WorldConfig, target_chapters: int, brief=None, bus=None) -> NarrativeSpine:
        chars = [{"id": e.id, "name": e.name} for e in world.entities if e.etype == "character"]
        # 아크 수를 목표에 비례(상한 8) — 4 고정 시 share/arc 가 에피소드 천장(4×10=40)을 넘어
        # 200화가 ~168화에서 조기완결되던 페이싱 결함 해소(분모 18=아크당 ~17~38화, 웹소설 아크 길이대).
        n_arcs = max(2, min(8, round((target_chapters or 12) / 18)))
        sys = ("너는 웹소설 아크 설계자다. '엔딩을 먼저 확정'하고 거기서 역순(backward)으로 아크를 설계하라. "
               "이 작품이 어떤 이야기인지 — 갈등의 성격·무대·이해관계·정서적 정점이 무엇인지 — 는 오직 아래 주어진 "
               "장르·톤·전제·시놉시스·세계규칙·인물에서 도출하라. 주어진 세계가 실제로 품은 결을 그대로 키울 뿐, "
               "다른 작품·다른 장르의 관습이나 장치를 끌어오지 마라. "
               "각 에피소드는 그 절정(climax)을 먼저 정하고 그리로 수렴하게 짜라 — 절정은 이 작품의 톤이 약속하는 "
               "정서적 정점이며, 그 정점이 무엇인지는 이 작품 자신이 정한다. "
               "복선(plants)은 미리 심고 payoffs로 회수하되 마감 강제는 없다(슬로우번 허용). JSON만.")
        # 설계 컨텍스트 충실 주입(harness over model): 세계규칙 + 작가가 대화로 정한 핵심(브리프)까지 — 빈약하면 정의적 장치가 척추에서 누락됨
        rules = "\n".join(f"- {r.text}" for r in world.world_rules) or "(없음)"
        brief_block = ""
        if brief is not None:
            bp = []
            if getattr(brief, "logline", ""): bp.append(f"로그라인: {brief.logline}")
            if getattr(brief, "conflicts", None): bp.append("핵심 갈등: " + " / ".join(brief.conflicts))
            if getattr(brief, "themes", None): bp.append("주제: " + ", ".join(brief.themes))
            if getattr(brief, "keywords", None):
                bp.append("키워드·트로프: " + ", ".join(brief.keywords)
                          + " (각 키워드의 회수 시점은 이 작품의 톤·페이싱이 약속하는 정서적 곡선에서 정하라)")
            cw = "; ".join(f"{c.name}({c.role}): {c.want}".strip(" :()")
                           for c in (brief.characters or []) if getattr(c, "name", ""))
            if cw: bp.append("인물 동기: " + cw)
            if bp: brief_block = "[작가가 대화로 정한 핵심 설계]\n" + "\n".join(bp) + "\n"
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n전제: {world.premise}\n시놉시스: {world.synopsis}\n"
               f"[세계 규칙]\n{rules}\n{brief_block}{_contract_block(world)}"
               f"[인물]{json.dumps(chars, ensure_ascii=False)}\n[목표 회차수]{target_chapters}\n"
               f"아크 {n_arcs}개(각 goal/central_conflict/turning_point). **첫 아크만** 에피소드 3~4개로 분해하고 "
               f"나머지 아크는 episodes 를 빈 배열로 둬라(진행하며 생성). 각 에피소드: title/premise/climax/"
               f"required_events(통제 태그)/required_cast(인물 id)/plants/payoffs/target_chapters(3~8).\n"
               "첫 아크의 new_cast: 이 아크에 필요한 조연·적대·조력 인물 0~4명을 '등장 전에 설계'하라 — "
               "name/profile(배경·성격·욕망·기존 인물과의 관계 — 말투 지정 금지)/debut_episode(데뷔 에피소드 순번 1..n). "
               "이야기가 요구하는 인물만(억지 채우기 금지).\n"
               '{"ending":{"central_question":"","ending":"","thematic_payoff":""},'
               '"arcs":[{"title":"","goal":"","central_conflict":"","turning_point":"",'
               '"episodes":[{"title":"","premise":"","climax":"","required_events":[],"required_cast":[],'
               '"plants":[],"payoffs":[],"target_chapters":4}],'
               '"new_cast":[{"name":"","profile":"","debut_episode":1}]}]}')
        # G8: max_tokens 를 아크 수에 비례(고정 3000 이 다아크 spine+new_cast 를 절단 → 엔딩/아크 빈 값으로 통과하던 결함)
        max_tokens = min(8000, 2200 + n_arcs * 500)

        def _emit(ev, **kw):
            if bus is not None:
                try:
                    bus.emit("worldgen", ev, **kw)
                except Exception:
                    pass
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}],
                                          temperature=0.5, max_tokens=max_tokens)
        except Exception:
            _emit("spine_gen_failed")
            return NarrativeSpine()
        # G8: '엔딩 먼저' 계약 검증 → 빈 필수 항목만 교정 재호출 1회(worldgen 의 검증→교정 패턴 복제, silent 빈 폴백 제거)
        gaps = self._spine_gaps(raw)
        if gaps:
            try:
                fix = self.provider.chat_json(
                    [{"role": "system", "content":
                      "다음 웹소설 설계 JSON에서 '비어 있는 필수 항목'만 채워 완전한 설계로 출력하라. "
                      "이미 채워진 값은 그대로 보존하고, 빈 ending/central_question/goal/climax 만 작품에 맞게 완성하라. JSON만."},
                     {"role": "user", "content": f"[비어 있는 필수 항목]\n{gaps}\n[원본 설계]\n"
                      f"{json.dumps(raw, ensure_ascii=False)}"}],
                    temperature=0.3, max_tokens=max_tokens)
                if isinstance(fix, dict) and len(self._spine_gaps(fix)) < len(gaps):   # 개선됐을 때만 채택
                    raw = fix
                    gaps = self._spine_gaps(raw)
            except Exception:
                pass
        if gaps:   # 잔존 누락 — 조용한 빈 폴백 금지(작가 가시화)
            _emit("spine_incomplete", missing=gaps[:6])
        spine = NarrativeSpine(ending=EndingSpec(**(raw.get("ending") or {})))
        for ai, a in enumerate(raw.get("arcs", []) or [], start=1):
            arc = Arc(arc_id=f"arc{ai}", order=ai, title=a.get("title", ""), goal=a.get("goal", ""),
                      central_conflict=a.get("central_conflict", ""), turning_point=a.get("turning_point", ""))
            for ei, e in enumerate(a.get("episodes", []) or [], start=1):
                arc.episodes.append(self._mk_episode(arc.arc_id, ei, e, {c["id"] for c in chars}))
            self._register_cast(world, arc, a.get("new_cast") or [])   # 캐스트 플랜 레이어: 등장 전 설계 완비
            spine.arcs.append(arc)
        self._rebalance(spine, target_chapters)   # 예산 정합: 프롬프트 1줄 지시가 아니라 산술(결정론)
        return spine

    @staticmethod
    def _rebalance(spine: NarrativeSpine, target: int) -> None:
        """분해된 에피소드 예산 합을 '아크 몫'(목표/아크수)에 산술 정합 — 회차수 권위 3개의 무조정 공존 해소.
        몫보다 에피소드가 많아 최소치(2화)로도 초과하면 에피소드 수 자체를 줄인다(정합 불가능 상태 제거)."""
        if not target or not spine.arcs:
            return
        share = max(2, round(target / max(1, len(spine.arcs))))
        for arc in spine.arcs:
            if not arc.episodes:
                continue
            while len(arc.episodes) > 1 and len(arc.episodes) * 2 > share:   # 최소 2화×개수 > 몫 → 병합(축소)
                arc.episodes.pop()
            total = sum(e.target_chapters for e in arc.episodes)
            if total <= 0:
                continue
            for e in arc.episodes:
                e.target_chapters = max(2, min(10, round(e.target_chapters * share / total)))
            diff = share - sum(e.target_chapters for e in arc.episodes)
            e_last = arc.episodes[-1]
            e_last.target_chapters = max(2, min(12, e_last.target_chapters + diff))

    @staticmethod
    def _register_cast(world: WorldConfig, arc, cast: list) -> None:
        """아크 설계가 낳은 신규 인물을 '등장 전에' 등록(잠정) — 즉흥 발명 금지의 집행 지점.
        말투는 받지 않는다(설정에서 창발). 데뷔는 에피소드 id 로 박아 비트가 집행."""
        existing = {e.id for e in world.entities} | {e.name for e in world.entities}
        for c in cast[:5]:
            name = (c.get("name") or "").strip()
            if not name or name in existing:
                continue
            base = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or f"cast_{len(world.entities)}"
            sid, i = base, 2
            while sid in {e.id for e in world.entities}:
                sid, i = f"{base}_{i}", i + 1
            try:
                ep_no = max(1, min(len(arc.episodes) or 1, int(c.get("debut_episode") or 1)))
            except (ValueError, TypeError):
                ep_no = 1
            world.entities.append(EntitySpec(
                id=sid, name=name, etype="character", attrs={},
                profile=(c.get("profile") or "").strip(),
                debut_episode=f"{arc.arc_id}_ep{ep_no}", provisional=True))
            existing |= {sid, name}

    def _mk_episode(self, arc_id, order, e, valid_ids) -> Episode:
        cast = [c for c in (e.get("required_cast") or []) if c in valid_ids]
        try:
            tgt = int(e.get("target_chapters", 4))
        except (ValueError, TypeError):
            tgt = 4
        return Episode(episode_id=f"{arc_id}_ep{order}", arc_id=arc_id, order=order,
                       title=e.get("title", ""), premise=e.get("premise", ""), climax=e.get("climax", ""),
                       required_events=e.get("required_events", []) or [], required_cast=cast,
                       plants=e.get("plants", []) or [], payoffs=e.get("payoffs", []) or [],
                       target_chapters=max(3, min(10, tgt)))

    # ---- 2) lazy 에피소드 생성(아크에 에피소드가 없을 때) ----
    def _gen_episodes(self, world: WorldConfig, arc: Arc, recent: list[str],
                      remaining: int | None = None) -> None:
        # G6: 인물을 id+이름만이 아니라 프로필(배경·성격·욕망·관계)까지 보고 분해 — 인물에서 사건이 나오게
        chars = [{"id": e.id, "name": e.name, "profile": ((e.profile or "")[:150])}
                 for e in world.entities if e.etype == "character"]
        end = world.spine.ending if world.spine and world.spine.ending else None
        ending = end.ending if end else ""
        cq = end.central_question if end else ""
        # G6: 지나온 에피소드 롤업 — lazy 아크가 '직전 3~4줄'만 보던 기아 해소(전체 흐름 위에서 다음 아크 설계)
        done_rollups = ([f"[{a.title}] {ep.summary}"
                         for a in sorted(world.spine.arcs, key=lambda x: x.order)
                         for ep in a.episodes if ep.done and ep.summary]
                        if world.spine else [])
        rollup_block = ("[지나온 에피소드 요약]\n" + "\n".join(done_rollups[-6:]) + "\n") if done_rollups else ""
        sys = ("아크를 에피소드(3~4개)로 분해하라. 각 에피소드는 절정(climax)을 먼저 정하고 수렴하게. 엔딩을 향해 전진. "
               "이 아크에 필요한 신규 인물(조연·적대) 0~4명은 new_cast 로 '등장 전에 설계'하라 — "
               "지금까지의 이야기 상태에서 태어나야 한다. profile=배경·성격·욕망·기존 인물과의 관계(말투 지정 금지). JSON만.")
        budget_line = f"[남은 회차 예산]{remaining}화 — 에피소드 target_chapters 합이 이 예산에 맞게.\n" if remaining else ""
        # 작품 척추(전제·중심질문)를 lazy 단계에도 물려줌 — 엔딩 한 줄만 보고 핵심 장치를 잃지 않게
        usr = (budget_line +
               f"[작품 전제]{(world.premise or '')[:300]}\n[중심 질문]{cq}\n"
               f"[엔딩]{ending}\n[아크]{arc.title} / 목표:{arc.goal} / 갈등:{arc.central_conflict} / 전환:{arc.turning_point}\n"
               f"{rollup_block}"
               f"[인물]{json.dumps(chars, ensure_ascii=False)}\n[최근 줄거리]\n" + "\n".join(recent) +
               '\n{"episodes":[{"title":"","premise":"","climax":"","required_events":[],"required_cast":[],'
               '"plants":[],"payoffs":[],"target_chapters":4}],'
               '"new_cast":[{"name":"","profile":"","debut_episode":1}]}')
        raw = {}
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}], temperature=0.5)
            eps = raw.get("episodes", []) or []
        except Exception:
            eps = []
        valid = {c["id"] for c in chars}
        for ei, e in enumerate(eps, start=1):
            arc.episodes.append(self._mk_episode(arc.arc_id, len(arc.episodes) + 1, e, valid))
        self._register_cast(world, arc, (raw.get("new_cast") or []) if eps else [])
        if remaining and arc.episodes:   # 코드 정합(지시는 보조): 잔여 예산으로 산술 클램프
            total = sum(e.target_chapters for e in arc.episodes)
            if total > 0 and total != remaining:
                for e in arc.episodes:
                    e.target_chapters = max(2, min(10, round(e.target_chapters * remaining / total)))
        if not arc.episodes:   # LLM 실패 시 최소 1개 보장(정지 방지)
            arc.episodes.append(Episode(episode_id=f"{arc.arc_id}_ep1", arc_id=arc.arc_id, order=1,
                                        title=arc.title or "전개", premise="", climax=arc.goal or "전개",
                                        target_chapters=4))

    # ---- 3) 현재 에피소드(커서) — 없으면 전진/연장 ----
    def current_episode(self, world: WorldConfig, progress: NarrativeProgress, recent: list[str],
                        remaining: int | None = None) -> Episode | None:
        spine = world.spine
        if not spine or not spine.arcs or progress.completed:
            return None
        if not progress.current_arc_id:
            progress.current_arc_id = spine.arcs[0].arc_id
        arc = spine.arc(progress.current_arc_id) or spine.arcs[0]
        if progress.current_episode_id:
            ep = next((e for e in arc.episodes if e.episode_id == progress.current_episode_id), None)
            if ep and not ep.done:
                return ep
        ep = next((e for e in arc.episodes if not e.done), None)   # 현재 아크의 다음 미완 에피소드
        if ep:
            progress.current_episode_id, progress.chapters_in_episode = ep.episode_id, 0
            return ep
        arc.done = True                                            # 아크 소진 → 다음 아크
        nxt = next((a for a in sorted(spine.arcs, key=lambda x: x.order) if not a.done), None)
        if nxt is None:                                            # 모든 아크 소진 → 완결(무한 lazy-gen 금지)
            progress.completed = True
            return None
        if not nxt.episodes:
            self._gen_episodes(world, nxt, recent, remaining=remaining)   # 다음 아크: 잔여 예산 내에서 분해
        ep = next((e for e in nxt.episodes if not e.done), None)
        if ep is None:                                             # gen 실패로도 못 채우면 완결 처리(정지 방지)
            progress.completed = True
            return None
        progress.current_arc_id, progress.current_episode_id, progress.chapters_in_episode = \
            nxt.arc_id, ep.episode_id, 0
        return ep

    # ---- 3.5) T3: 에피소드 활성 시 '적시 사건 메뉴'(신선 컨텍스트로 8~12 사건 풀) ----
    def generate_event_menu(self, world: WorldConfig, arc: Arc, episode: Episode,
                            recent: list[str], cast_context: str = "", plant_notes: str = "",
                            outstanding: list[str] | None = None,
                            required_override: list[str] | None = None) -> list[str]:
        """에피소드가 활성화되는 시점에 '구체적 한 줄 사건' 풀(8~12)을 신선 컨텍스트로 생성한다.
        T1의 천장(에피소드 required_events 가 빈약하면 비트가 끌어올 재료가 없음)을 올리는 게 목적.

        설계 불변식:
        - NEVER throws · NEVER empty — LLM 실패 시 결정론 폴백(required_events·climax·만기약속·payoffs).
          (활성 가드가 회귀 테스트의 Fake provider 경로를 타므로 예외/빈 반환 금지가 필수.)
        - required_events 는 '코드로' 무조건 맨 앞에 보존 — 프롬프트 지시만으론 LLM 이 풍부한 메뉴 쪽으로
          치우쳐 빈약한 required_events 를 누락 → 본문이 메뉴만 실현하고 required 미실현 → T2 event_uncovered
          역증가(T2 역설). no-whack-a-mole: '프롬프트 지시 한계'는 이미 입증됨 → 코드 강제.
        - 메뉴는 advisory '후보 풀'이지 '지시'가 아니다(억지 회수·온레일 금지). 약속/복선 라벨은 원문 그대로(ledger _key 정합)."""
        # required_override(T4 refresh): 이미 실현된 required 를 뺀 '미실현만' 전달 → 소진 사건 재투입 방지.
        _req_src = required_override if required_override is not None else (episode.required_events or [])
        req = [e for e in _req_src if (e or "").strip()]
        due = [o for o in (outstanding or []) if (o or "").strip()][:6]
        menu: list[str] = []
        try:
            end = world.spine.ending if world.spine and world.spine.ending else None
            sys = ("이 에피소드(3~10화 분량) 전체에서 '실제로 일어나는 구체적 한 줄 사건' 8~12개를 만들어라 — "
                   "추상 주제·요약 금지, 장면으로 바로 쓸 수 있는 사건만. "
                   "① 에피소드 '필수 사건'을 맨 앞에 모두 포함하고 더 구체화한다. "
                   "② 만기된 미회수 약속/복선이 있으면 그 '회수(지불) 사건'을 다음에 배치(억지 회수 아닌 자연스러운 정산). "
                   "③ 인물의 욕망·직전 화가 남긴 미결 상태·세계 고유 설정에서 신선한 사건을 더한다. ④ 에피소드 절정으로 수렴한다. "
                   "이건 '지시'가 아니라 비트가 골라 쓸 '후보 메뉴'다. 약속/복선 라벨은 원문 그대로 노출(변형·풀어쓰기 금지). "
                   '{"event_menu":["사건1","사건2","..."]} JSON만.')
            usr = ((f"[중심 질문]{end.central_question}\n" if end else "") +
                   f"[아크 목표]{arc.goal}{(' · 갈등:'+arc.central_conflict) if arc.central_conflict else ''}"
                   f"{(' · 전환점:'+arc.turning_point) if arc.turning_point else ''}\n"
                   f"[에피소드]{episode.title} / 도입:{episode.premise}\n[에피소드 절정]{episode.climax}\n"
                   f"[필수 사건 — 맨 앞에 모두 포함·구체화]{req}\n"
                   f"[만기 약속/복선 — 자연스러우면 회수]{due}\n"
                   f"[심은 복선]{list(episode.plants or [])}\n[회수 예정]{list(episode.payoffs or [])}\n" +
                   (f"[등장 인물 — 이름·설정·현재 상태]\n{cast_context}\n" if cast_context else "") +
                   (f"[참고 미회수 복선]{plant_notes}\n" if plant_notes else "") +
                   "[최근 줄거리]\n" + "\n".join(recent[-3:])[-280:] + "\n"
                   '{"event_menu":[]}')
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.4)
            menu = [str(s).strip() for s in (d.get("event_menu") or []) if str(s).strip()][:12]
        except Exception:
            menu = []   # 결정론 폴백 — 아래 seed 합성이 빈 메뉴를 required·climax·약속으로 메운다(never empty)
        # 코드 강제: required_events 를 무조건 맨 앞에 보존 + (LLM 실패 시) 결정론 재료 합성 → dedup·cap 12.
        seed = req + menu + due + ([episode.climax] if episode.climax else []) + list(episode.payoffs or [])
        out = list(dict.fromkeys(s.strip() for s in seed if (s or "").strip()))[:12]
        return out or [episode.premise or episode.climax or f"{episode.title} 전개"]

    # ---- 4) 에피소드 → 회차 beat 파생(절정으로 수렴, finale면 절단신공) ----
    def beat_for_episode(self, world: WorldConfig, arc: Arc, episode: Episode, chapter: int,
                         is_finale: bool, recent: list[str], directives: list[str],
                         plant_notes: str = "", cast_context: str = "",
                         event_menu: list[str] | None = None) -> Beat:
        char_ids = [e.id for e in world.entities if e.etype == "character"]
        is_opening = (chapter == 1)   # 발단(도입부). 연구근거(Save the Cat·나혼렙/전독시·in-medias-res): 짧은 grounding + 전환의 순간을 장면으로 극화
        if is_opening:
            hook = ("이번은 작품의 '첫 회차(발단)'다. 절정으로 직행하지 말고 도입부를 제대로 펼쳐라 — ① 주인공이 누구이고 "
                    "어떤 상황·결핍에 놓였는지 짧지만 구체적으로 보여주고(현재 일상·정체), ② 전제의 핵심 전환(이 작품의 훅이 "
                    "시작되는 바로 그 순간 — 각성·회귀·세계의 변화·사태 발발 등 무엇이든)을 *장면으로 직접 극화*하라. "
                    "이미 '전환이 끝난 세계'에서 시작해 그 과정을 회상·설명으로 때우지 마라(늦게 시작한 신호). "
                    "이 전환의 순간 자체가 이번 회차의 중심 사건이 되게 — 독자가 '이 사람이 누구이고 무슨 일이 시작됐는지' 알고 빠져들게.")
        elif is_finale:
            hook = "이번 회차가 에피소드 절정(finale): 아래 climax 를 이번 회차에서 터뜨려라(끝맺음 방식은 작품 문체 정책을 따름)."
        else:
            hook = "에피소드 절정으로 한 걸음 전진. 아직 절정을 다 터뜨리지 말 것."
        cont = ("" if is_opening else
                " 직전 화 말미에 열린 미결 상태(그 화의 톤이 무엇으로 끝났든)를 이번 화 도입에서 이어받아 다루라 — 리셋·동일 장면 재연·비트 반복 금지, 공간 또는 상황을 반드시 한 단계 전진.")
        sys = ("에피소드 안에서 다음 회차 1개의 beat 를 설계하라. 절정으로 수렴하되 기존 설정과 모순 금지. " + hook + cont +
               " 세계규칙·전제가 정한 이 작품 고유의 체계(사회·제도·관계·자원 등 무엇이든)를 사건의 구체 디테일로 쓰라(인포덤프 금지, 행동·대사·선택으로 흘려라). "
               f"key_events 는 이 회차를 약 {world.style.target_chars_per_chapter}자로 자연스럽게 채울 만큼의 '구체적으로 일어나는 사건'을 담아라 — "
               "보통 3~5개(도입·휴지 회차는 적게, escalation·finale 회차는 절정 사건을 더 몰아서). 억지로 2개로 줄이지 마라(회차가 빈약·저밀도가 되는 원인). "
               "에피소드 필수 사건 중 이번 회차가 다룰 것을 골라 분배하되, 한 회차에 과밀(8개 이상)도 금지. "
               "'적시 사건 메뉴'가 제공되면 그 풀에서 골라 key_events 를 풍부하게 구성하라 — 단 '필수 사건'을 반드시 우선 실현하고(메뉴가 필수 사건을 밀어내지 마라), 메뉴는 보강 재료다. "
               # G4: chapter_function/hook_type/time_advance/place 는 '강제'가 아니라 '네 계획을 그대로 라벨링'(서술 메타데이터).
               # 이 라벨로 회차 내용을 바꾸라는 게 아니라, 설계한 회차가 어떤 기능·끝맺음·시간·장소인지 자기 기술하라는 것(작가 가시화·분석용).
               "끝으로 설계한 이 회차를 자기 기술하라(내용을 바꾸지 말고 있는 그대로 짧은 라벨만 — 분석용 메타데이터라 한 단어로) — "
               "chapter_function(독자에게 주는 것: payoff/setup/escalation/relation/respite 중 하나), "
               "hook_type(회차말 끊는 방식 한 단어: action/reveal/emotion/decision/threat/question/twist/cliffhanger 중 가장 가까운 것), "
               "time_advance(직전 화 대비 시간 경과 짧게: 예 '없음'/'몇 분'/'다음날'/'사흘 후'), place(주요 장소 짧게). "
               "title 은 회차 제목만(시리즈명·화수 붙이지 마라). JSON만.")
        # plant_notes 는 시스템 '참고' 정보 — 작가 지시(authority)와 분리된 슬롯(시스템 개입의 지시 위장 금지, 모드 계약 §1)
        notes_block = f"\n[미회수 복선 — 참고용]{plant_notes}" if plant_notes else ""
        end = world.spine.ending if world.spine and world.spine.ending else None
        spine_block = (f"[작품 전제]{(world.premise or '')[:240]}\n[중심 질문]{end.central_question}\n"
                       if end else "")   # 회차 비트도 작품 척추(전제·중심질문)를 보게 — 핵심 장치 유실 방지
        # G6: 인물을 id 문자열이 아니라 '이름·설정·현재 상태·관계'로 보게(컨텍스트 기아 해소 — 욕망 있는 인물에서 사건이 나오게)
        cast_block = (f"[등장 인물 — 이름·설정(배경·성격·욕망·관계)·현재 상태]\n{cast_context}\n[유효 인물 id]{char_ids}\n"
                      if cast_context else f"[인물 id]{char_ids}\n")
        # T3: 적시 사건 메뉴 = 후보 풀(지시 아님). 필수 사건 슬롯은 그대로 두고 그 '아래'에 배치(필수 우선·메뉴 보강).
        menu_block = (f"[적시 사건 메뉴 — key_events 채울 후보 풀(지시 아님, 필수 사건 우선 실현 후 보강)]{list(event_menu)}\n"
                      if event_menu else "")
        usr = (spine_block + _contract_block(world) +
               f"[아크 목표]{arc.goal}{(' · 중심 갈등:'+arc.central_conflict) if arc.central_conflict else ''}{(' · 전환점:'+arc.turning_point) if arc.turning_point else ''}\n"
               f"[에피소드]{episode.title} / 도입:{episode.premise}\n[에피소드 절정]{episode.climax}\n"
               f"[필수 사건]{episode.required_events}\n[등장해야 할 인물 id]{episode.required_cast}\n" + menu_block +
               f"{cast_block}[최근 줄거리]\n" + "\n".join(recent) +
               f"\n[작가 지시]{json.dumps(directives, ensure_ascii=False)}{notes_block}\n"
               f"[회차]{chapter} (에피소드 내 finale={is_finale})\n"
               '{"title":"","summary":"","key_events":["구체 사건1","구체 사건2","구체 사건3","(분량 채울 만큼 더)"],"entities":["인물 id"],'
               '"chapter_function":"","hook_type":"","time_advance":"","place":""}')
        try:
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.5)
            ents = [e for e in (d.get("entities") or []) if e in set(char_ids)] or \
                   (episode.required_cast or char_ids[:2])
            beat = Beat(chapter=chapter, title=d.get("title", f"{chapter}화"), summary=d.get("summary", ""),
                        key_events=d.get("key_events", []) or [], entities=ents,
                        arc_id=arc.arc_id, episode_id=episode.episode_id, is_episode_finale=is_finale,
                        chapter_function=(d.get("chapter_function") or "").strip(),
                        hook_type=(d.get("hook_type") or "").strip(),
                        time_advance=(d.get("time_advance") or "").strip(),
                        place=(d.get("place") or "").strip())
        except Exception:
            # T1+T3 폴백: 필수 사건을 앞에 두고 적시 메뉴로 보강(빈약 회차 방지) → dedup·cap 8.
            _fb = list(dict.fromkeys(e.strip() for e in
                       ((episode.required_events or []) + list(event_menu or [])) if (e or "").strip()))[:8]
            beat = Beat(chapter=chapter, title=f"{chapter}화", summary=episode.climax or episode.premise,
                        key_events=(_fb or [episode.climax or episode.premise]),
                        entities=(episode.required_cast or char_ids[:2]),
                        arc_id=arc.arc_id, episode_id=episode.episode_id, is_episode_finale=is_finale)
        return beat
