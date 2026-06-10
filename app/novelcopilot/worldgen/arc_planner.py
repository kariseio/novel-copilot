# -*- coding: utf-8 -*-
"""ArcPlanner (R4) — 엔딩을 먼저 고정하고 역순(backward)으로 아크/에피소드 설계 + 회차 beat 파생.

'사람 작가의 사고를 더 탄탄하게': 인간은 다다음 에피소드까지만 보지만 AI는 엔딩을 고정하고
아크를 역설계한 뒤, 에피소드/회차를 lazy 하게 채운다. 절정(climax)을 먼저 정하고 거기로 수렴.
복선은 추적만(plants/payoffs), 마감 강제 없음. 아크/에피소드 목표는 narrative(서사 의도)지 ground_truth 아님.
"""
from __future__ import annotations
import json

from ..domain.world import WorldConfig, Beat
from ..domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec, NarrativeProgress
from ..llm.base import LLMProvider


class ArcPlanner:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    # ---- 1) 엔딩-주도 spine 생성(작품 시작 시 1회) ----
    def build_spine(self, world: WorldConfig, target_chapters: int) -> NarrativeSpine:
        chars = [{"id": e.id, "name": e.name} for e in world.entities if e.etype == "character"]
        n_arcs = max(2, min(4, round((target_chapters or 12) / 12)))
        sys = ("너는 웹소설 아크 설계자다. '엔딩을 먼저 확정'하고 거기서 역순(backward)으로 아크를 설계하라. "
               "각 에피소드는 절정(climax — 그 장르의 카타르시스: 액션의 쾌감, 로맨스의 감정 폭발, 미스터리의 반전 등)을 "
               "먼저 정하고 그 절정으로 수렴하게 짜라. "
               "복선(plants)은 미리 심고 payoffs로 회수하되 마감 강제는 없다(슬로우번 허용). JSON만.")
        usr = (f"[작품] {world.title} / {world.genre} / {world.tone}\n전제: {world.premise}\n시놉시스: {world.synopsis}\n"
               f"[인물]{json.dumps(chars, ensure_ascii=False)}\n[목표 회차수]{target_chapters}\n"
               f"아크 {n_arcs}개(각 goal/central_conflict/turning_point). **첫 아크만** 에피소드 3~4개로 분해하고 "
               f"나머지 아크는 episodes 를 빈 배열로 둬라(진행하며 생성). 각 에피소드: title/premise/climax/"
               f"required_events(통제 태그)/required_cast(인물 id)/plants/payoffs/target_chapters(3~8).\n"
               '{"ending":{"central_question":"","ending":"","thematic_payoff":""},'
               '"arcs":[{"title":"","goal":"","central_conflict":"","turning_point":"",'
               '"episodes":[{"title":"","premise":"","climax":"","required_events":[],"required_cast":[],'
               '"plants":[],"payoffs":[],"target_chapters":4}]}]}')
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}],
                                          temperature=0.5, max_tokens=3000)
        except Exception:
            return NarrativeSpine()
        spine = NarrativeSpine(ending=EndingSpec(**(raw.get("ending") or {})))
        for ai, a in enumerate(raw.get("arcs", []) or [], start=1):
            arc = Arc(arc_id=f"arc{ai}", order=ai, title=a.get("title", ""), goal=a.get("goal", ""),
                      central_conflict=a.get("central_conflict", ""), turning_point=a.get("turning_point", ""))
            for ei, e in enumerate(a.get("episodes", []) or [], start=1):
                arc.episodes.append(self._mk_episode(arc.arc_id, ei, e, {c["id"] for c in chars}))
            spine.arcs.append(arc)
        return spine

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
    def _gen_episodes(self, world: WorldConfig, arc: Arc, recent: list[str]) -> None:
        chars = [{"id": e.id, "name": e.name} for e in world.entities if e.etype == "character"]
        ending = world.spine.ending.ending if world.spine and world.spine.ending else ""
        sys = ("아크를 에피소드(3~4개)로 분해하라. 각 에피소드는 절정(climax)을 먼저 정하고 수렴하게. 엔딩을 향해 전진. JSON만.")
        usr = (f"[엔딩]{ending}\n[아크]{arc.title} / 목표:{arc.goal} / 갈등:{arc.central_conflict} / 전환:{arc.turning_point}\n"
               f"[인물]{json.dumps(chars, ensure_ascii=False)}\n[최근 줄거리]\n" + "\n".join(recent[-3:]) +
               '\n{"episodes":[{"title":"","premise":"","climax":"","required_events":[],"required_cast":[],'
               '"plants":[],"payoffs":[],"target_chapters":4}]}')
        try:
            raw = self.provider.chat_json([{"role": "system", "content": sys},
                                           {"role": "user", "content": usr}], temperature=0.5)
            eps = raw.get("episodes", []) or []
        except Exception:
            eps = []
        valid = {c["id"] for c in chars}
        for ei, e in enumerate(eps, start=1):
            arc.episodes.append(self._mk_episode(arc.arc_id, len(arc.episodes) + 1, e, valid))
        if not arc.episodes:   # LLM 실패 시 최소 1개 보장(정지 방지)
            arc.episodes.append(Episode(episode_id=f"{arc.arc_id}_ep1", arc_id=arc.arc_id, order=1,
                                        title=arc.title or "전개", premise="", climax=arc.goal or "전개",
                                        target_chapters=4))

    # ---- 3) 현재 에피소드(커서) — 없으면 전진/연장 ----
    def current_episode(self, world: WorldConfig, progress: NarrativeProgress, recent: list[str]) -> Episode | None:
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
            self._gen_episodes(world, nxt, recent)                 # 다음 아크 에피소드 lazy 생성
        ep = next((e for e in nxt.episodes if not e.done), None)
        if ep is None:                                             # gen 실패로도 못 채우면 완결 처리(정지 방지)
            progress.completed = True
            return None
        progress.current_arc_id, progress.current_episode_id, progress.chapters_in_episode = \
            nxt.arc_id, ep.episode_id, 0
        return ep

    # ---- 4) 에피소드 → 회차 beat 파생(절정으로 수렴, finale면 절단신공) ----
    def beat_for_episode(self, world: WorldConfig, arc: Arc, episode: Episode, chapter: int,
                         is_finale: bool, recent: list[str], directives: list[str],
                         plant_notes: str = "") -> Beat:
        char_ids = [e.id for e in world.entities if e.etype == "character"]
        hook = ("이번 회차가 에피소드 절정(finale): 아래 climax 를 이번 회차에서 터뜨려라(끝맺음 방식은 작품 문체 정책을 따름)."
                if is_finale else "에피소드 절정으로 한 걸음 전진. 아직 절정을 다 터뜨리지 말 것.")
        sys = ("에피소드 안에서 다음 회차 1개의 beat 를 설계하라. 절정으로 수렴하되 기존 설정과 모순 금지. " + hook + " JSON만.")
        # plant_notes 는 시스템 '참고' 정보 — 작가 지시(authority)와 분리된 슬롯(시스템 개입의 지시 위장 금지, 모드 계약 §1)
        notes_block = f"\n[미회수 복선 — 참고용]{plant_notes}" if plant_notes else ""
        usr = (f"[아크 목표]{arc.goal}\n[에피소드]{episode.title} / 도입:{episode.premise}\n[에피소드 절정]{episode.climax}\n"
               f"[필수 사건]{episode.required_events}\n[등장해야 할 인물 id]{episode.required_cast}\n"
               f"[인물 id]{char_ids}\n[최근 줄거리]\n" + "\n".join(recent[-3:]) +
               f"\n[작가 지시]{json.dumps(directives, ensure_ascii=False)}{notes_block}\n[회차]{chapter} (에피소드 내 finale={is_finale})\n"
               '{"title":"","summary":"","key_events":["",""],"entities":["인물 id"]}')
        try:
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.5)
            ents = [e for e in (d.get("entities") or []) if e in set(char_ids)] or \
                   (episode.required_cast or char_ids[:2])
            beat = Beat(chapter=chapter, title=d.get("title", f"{chapter}화"), summary=d.get("summary", ""),
                        key_events=d.get("key_events", []) or [], entities=ents,
                        arc_id=arc.arc_id, episode_id=episode.episode_id, is_episode_finale=is_finale)
        except Exception:
            beat = Beat(chapter=chapter, title=f"{chapter}화", summary=episode.climax or episode.premise,
                        key_events=episode.required_events[:2], entities=(episode.required_cast or char_ids[:2]),
                        arc_id=arc.arc_id, episode_id=episode.episode_id, is_episode_finale=is_finale)
        return beat
