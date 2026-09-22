# -*- coding: utf-8 -*-
"""ArcPlanner (R4) — 엔딩을 먼저 고정하고 역순(backward)으로 아크/에피소드 설계 + 회차 beat 파생.

'사람 작가의 사고를 더 탄탄하게': 인간은 다다음 에피소드까지만 보지만 AI는 엔딩을 고정하고
아크를 역설계한 뒤, 에피소드/회차를 lazy 하게 채운다. 절정(climax)을 먼저 정하고 거기로 수렴.
복선은 추적만(plants/payoffs), 마감 강제 없음. 아크/에피소드 목표는 narrative(서사 의도)지 ground_truth 아님.
"""
from __future__ import annotations
import json

import re as _re

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..domain.world import WorldConfig, Beat, EntitySpec
from ..domain.types import TimeDelta
from ..domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec, NarrativeProgress
from ..engine.structure_history import structure_history_block as _structure_history_block
from ..engine.menu_filter import event_menu_filter, hook_whitelist, closing_whitelist, scene_form_whitelist
from ..llm.base import LLMProvider


# SP-2(감사 M9): 에피소드 규칙 예산 — 규칙 인플레(매화 새 규칙 발명, 15화 실측)의 계획 층 소스 차단.
#   두 분해 경로(build_spine·_gen_episodes)에 '동일 바이트'로 실린다(한쪽 누락 시 아크 2부터 재발).
#   이행은 new_rule 산출 필드가 받는다(검사 가능한 계약 — 필드 없는 지시는 장식).
NEW_RULE_LINE = "각 에피소드가 새로 세우는 규칙은 하나이고, 그것을 new_rule 에 담아라."
_NEW_RULE_SCHEMA = '"new_rule":"이 에피소드가 새로 세우는 규칙 한 줄(없으면 빈 문자열)"'

# RC-1: 회차 슬롯 서사기능 태그 — chapter_function 어휘(독자에게 주는 것)를 재사용한다(신규 기피어 0·genre-blind·긍정형).
#   '피할 대상'을 노출하는 부정 태그 없이 '이 회차가 무엇을 여는가/조이는가/맺는가'를 긍정으로만 라벨한다.
SLOT_FUNCTIONS = ("setup", "escalation", "payoff", "relation", "respite")


def _fallback_slot_function(idx0: int, target: int) -> str:
    """분배 실패·미태그 슬롯의 위치 기반 폴백 태그(긍정형·genre-blind). 첫 화=열기, 마지막 화=맺기, 중간=쌓기."""
    if target <= 1:
        return "payoff"
    if idx0 == 0:
        return "setup"
    if idx0 >= target - 1:
        return "payoff"
    return "escalation"


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

    @promptlog.stage("worldgen:spine")
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
               # DP-11: required_events gloss 를 상태 명사(자각·각인)를 유도하던 구 문구에서
               #        '지면에서 일어나는 사건 — 인물이 무엇을 하는가'로 긍정 재정의(상태 명사 차단).
               # DP-20 ⓒ: gloss 확장(긍정형·트로프 호명 0) — 필수 사건의 절반 이상을 '주인공의 행동이 세계를 바꾸는 사건'으로.
               f"required_events(구체적으로 일어나는 사건 — 인물이 무엇을 하는가; 상태 명사 아님. "
               f"이 중 절반 이상은 주인공의 행동이 세계를 바꾸는 사건으로 두라 — 얻어내고·뒤집고·움직이게 만드는 사건)"
               f"/required_cast(인물 id)/plants/payoffs/target_chapters(3~8).\n"
               # DP-3' ⓐ: 정산 배치 — 에피소드마다 독자에게 지불되는 사건(payoff)을 최소 1개, 그 위치와 함께 둔다.
               #   긍정형 배치 지시(강도·종류는 작품 시드가 정함 — genre-blind). 기존 payoffs 필드 소비 강화.
               "각 에피소드에는 독자에게 실제로 지불되는 사건(payoff — 승리·응징·획득·폭로 등 이 작품의 톤이 약속한 보상의 '실현')을 "
               "최소 하나 두고, 그것을 어디에 터뜨릴지 payoffs 에 담고 그 위치를 payoff_at 으로 밝혀라(early|mid|climax 중 하나 — "
               "이 작품이 정한 보상의 결에 맞게). 무엇이 보상인지·얼마나 큰지는 이 작품의 장르·전제가 정한다(다른 작품 관습 이식 금지).\n"
               # DP-20 ⓑ: 이자 트랙(2층 구조·긍정형) — 대정산(아크 핵심 쾌감)과 별개로 각 에피소드 안에서 닫히는 소정산을 두고,
               #   핵심 쾌감의 첫 집행은 아크 전반부 안에 배치(원금/이자 2층·배치 규칙이라 genre-blind).
               "대정산(아크의 핵심 쾌감 집행)과 별개로, 각 에피소드에는 그 에피소드 안에서 닫히는 소정산 — "
               "주인공의 우위가 관측 가능한 결과로 환금되는 사건 — 을 하나 두라. "
               "그리고 이 아크가 약속한 핵심 쾌감의 첫 집행은 아크의 전반부 에피소드 안에 배치하라(끝까지 미루지 마라).\n"
               f"{NEW_RULE_LINE}\n"
               "첫 아크의 new_cast: 이 아크에 필요한 조연·적대·조력 인물 0~4명을 '등장 전에 설계'하라 — "
               "name/profile(공개·현재 정체: 배경·성격·현재 욕망·현재 관계. 말투 슬롯은 비워 둔다)/arc_note(선택: 앞으로의 궤적·뒤에 드러날 면모, 있을 때만)/debut_episode(데뷔 에피소드 순번 1..n). "
               "이야기가 요구하는 인물만(억지 채우기 금지).\n"
               '{"ending":{"central_question":"","ending":"","thematic_payoff":""},'
               '"arcs":[{"title":"","goal":"","central_conflict":"","turning_point":"",'
               '"episodes":[{"title":"","premise":"","climax":"","required_events":[],"required_cast":[],'
               '"plants":[],"payoffs":[],"payoff_at":"climax",' + _NEW_RULE_SCHEMA + ',"target_chapters":4}],'
               '"new_cast":[{"name":"","profile":"","arc_note":"","debut_episode":1}]}]}')
        def _emit(ev, **kw):
            if bus is not None:
                try:
                    bus.emit("worldgen", ev, **kw)
                except Exception:
                    pass
        def _gap_rank(d):
            try:
                return len(self._spine_gaps(d))
            except Exception:
                return 99                       # 비정형(파싱 잔해) — 최하 순위(정상 부분 결과를 덮지 못함)

        # B-26: spine 콜 1회 transient 실패가 곧장 '빈 spine(아크 0=평면 모드)'으로 조용히 굳던 결함 —
        # 실패(예외)·빈 결과(아크 0)에 1회 retry. 재실패 시 폴백(빈 spine)은 유지하되 경고를 가시화(무강제).
        # (구 캡 상향 재시도 경로는 무상한 전환으로 제거 — 절단 자체가 프로바이더 하드캡에서만 가능)
        raw: dict = {}
        for attempt in (1, 2):
            self.provider.last_truncated = False
            try:
                got = self.provider.chat_json([{"role": "system", "content": sys},
                                               {"role": "user", "content": usr}],
                                              temperature=0.5)
            except Exception:
                got = None
            truncated = bool(getattr(self.provider, "last_truncated", False))
            if isinstance(got, dict) and got and (
                    not raw or got.get("arcs") or _gap_rank(got) <= _gap_rank(raw)):
                raw = got                       # 부분 결과 보존 — 덜 빈(gaps 적은) 쪽 유지, retry 잔해가 G8 재료를 덮지 않게
            if raw.get("arcs"):
                break                           # 골격(아크) 확보 — 세부 누락은 아래 G8 교정 경로가 처리
            if attempt == 1:
                _emit("spine_retry", reason=("truncated" if truncated else "transient"))   # 가시화: 1회 재시도
        if not raw:
            _emit("spine_gen_failed", retried=True)   # 재시도까지 실패 — 빈 폴백 유지 + 가시 경고(조용한 flat 진행 금지)
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
                    temperature=0.3)
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
        self._ensure_final_settlement(spine)      # B-31: 최종 아크가 이미 분해됐다면 정산 회차 보장(보통은 lazy 분해 시 부착)
        return spine

    # ---- B-31: 최종 아크 '절정 회차'와 '정산/에필로그 회차' 분리(완결 클라이맥스 절단 소스 차단) ----
    @staticmethod
    def _ensure_final_settlement(spine: NarrativeSpine) -> None:
        """마지막(최고 order) 아크의 climax 에피소드 뒤에 전용 '정산/여운(에필로그)' 에피소드를 1회 보장한다.

        실측 소스(3작 24화): 작품 전체의 절정+엔딩 정산이 '마지막 단일 finale 회차'에 과밀 배정돼
        절정 한중간에서 완결로 절단됐다(붕괴 24화='공개 발현' 필수사건 미실현, 서리꽃 24화 물리 절단,
        봄 축제 당일 생략). 원인은 최종 에피소드가 climax 실현과 엔딩 정산을 같은 finale 회차에 얹은 구조 —
        그 finale 는 closing=True 라 '중심갈등 매듭+미결 결행'까지 요구받아 절정이 지면에 다 실리지 못한다.

        해소(계획 레이어 산술 — 무강제, 검출기 0): climax 에피소드에서 회차를 이월해 뒤에 정산 에피소드를 둔다.
        그러면 climax 에피소드의 finale 는 closing=False(정산 에피소드가 아직 미완)로 '절정만' 터뜨리고,
        정산 에피소드의 finale 가 진짜 closing 회차가 되어 엔딩 정산·여운을 전담한다. 완결(ending_reached)도
        정산 회차 '이후'로 미뤄져 절정 직후 조기완결이 소스 차단된다. 예산은 이월이라 총량 보존(climax<2 로는
        깎지 않음 → 그 드문 경우만 +1 회차). 멱등(중복 부착 금지). LR-0 러너/ EC-1 정산과 정합 유지."""
        if not spine or not spine.arcs:
            return
        arc = max(spine.arcs, key=lambda a: a.order)          # 최종 아크
        if not arc.episodes:
            return                                            # 아직 미분해 — lazy 분해 시 재호출됨(멱등)
        if any(e.episode_id.endswith("_settle") for e in arc.episodes):
            return                                            # 이미 보장됨
        climax_ep = arc.episodes[-1]                          # 마지막 설계 에피소드 = 아크(=작품) 절정 담지
        settle_ch = 2 if climax_ep.target_chapters >= 5 else 1
        shrink = min(settle_ch, max(0, climax_ep.target_chapters - 2))   # climax 최소 2 보존(절정 실현 여지)
        climax_ep.target_chapters -= shrink                   # 이월(총 예산 보존; shrink<settle_ch 인 드문 경우만 +overflow)
        end = spine.ending
        settle_climax = (((end.thematic_payoff or end.ending) if end else "") or
                         "중심 갈등과 감정선을 갈무리하는 마지막 장면")
        arc.episodes.append(Episode(
            episode_id=f"{arc.arc_id}_settle", arc_id=arc.arc_id, order=len(arc.episodes) + 1,
            title="여운과 정산",
            premise=("절정 이후의 여파를 다루고 중심 질문에 답하며 인물들이 다다른 새 자리를 보여준다 — "
                     "이미 열린 것을 닫고 여운을 남긴다."),
            climax=settle_climax, target_chapters=settle_ch))   # 절단 전면 제거(2026-08-21): 엔딩 서술 전문

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
                arc_note=(c.get("arc_note") or "").strip(),   # PF-2: 궤적(설계 전용) 라운드트립 — 없으면 ""(하위호환)
                debut_episode=f"{arc.arc_id}_ep{ep_no}", provisional=True))
            existing |= {sid, name}

    @staticmethod
    def _coerce_texts(items) -> list[str]:
        """LLM 반환 list[str] 필드의 신뢰 경계 관용 변환(ST-12a 검증 런 적발 2026-07-14) —
        DP-3' 프롬프트가 payoff_at 을 함께 요구하자 모델이 payoffs 를 [{'what':…,'payoff_at':…}]
        객체 배열로 반환 → pydantic string_type 사망 → spine 전체 유실. 문자열은 그대로, dict 는
        대표 텍스트 필드(what/text/event/…) 또는 첫 문자열 값만 회수, 그 외는 버린다(발명 0)."""
        out: list[str] = []
        for it in (items or []):
            s = ""
            if isinstance(it, str):
                s = it.strip()
            elif isinstance(it, dict):
                for k in ("what", "text", "event", "content", "desc", "description", "payoff", "plant"):
                    v = it.get(k)
                    if isinstance(v, str) and v.strip():
                        s = v.strip()
                        break
                if not s:
                    s = next((v.strip() for v in it.values()
                              if isinstance(v, str) and v.strip()), "")
            if s:
                out.append(s)
        return out

    def _mk_episode(self, arc_id, order, e, valid_ids) -> Episode:
        cast = [c for c in (e.get("required_cast") or []) if c in valid_ids]
        try:
            tgt = int(e.get("target_chapters", 4))
        except (ValueError, TypeError):
            tgt = 4
        raw_payoffs = e.get("payoffs", []) or []
        payoff_at = (e.get("payoff_at") or "").strip()
        if not payoff_at and isinstance(raw_payoffs, list):   # 중첩 반환 관용: payoffs 객체 안의 payoff_at 채택
            payoff_at = next((str(p.get("payoff_at") or "").strip() for p in raw_payoffs
                              if isinstance(p, dict) and str(p.get("payoff_at") or "").strip()), "")
        return Episode(episode_id=f"{arc_id}_ep{order}", arc_id=arc_id, order=order,
                       title=e.get("title", ""), premise=e.get("premise", ""), climax=e.get("climax", ""),
                       required_events=self._coerce_texts(e.get("required_events")), required_cast=cast,
                       plants=self._coerce_texts(e.get("plants")), payoffs=self._coerce_texts(raw_payoffs),
                       payoff_at=payoff_at,   # DP-3' ⓐ: 정산 위치 라벨(advisory)
                       new_rule=str(e.get("new_rule") or "").strip(),   # SP-2 M9: 규칙 예산 원장(advisory)
                       target_chapters=max(3, min(10, tgt)))

    # ---- 2) lazy 에피소드 생성(아크에 에피소드가 없을 때) ----
    @promptlog.stage("worldgen:episodes")
    def _gen_episodes(self, world: WorldConfig, arc: Arc, recent: list[str],
                      remaining: int | None = None) -> None:
        # G6: 인물을 id+이름만이 아니라 프로필(배경·성격·욕망·관계)까지 보고 분해 — 인물에서 사건이 나오게
        # PF-2: 아크 노트(궤적·설계 전용)를 분해 콜에 동봉 — 이 콜은 설계 계층이라 미주입 통로 아님. 빈 값이면 키 부재(바이트 동일).
        chars = []
        for e in world.entities:
            if e.etype != "character":
                continue
            c = {"id": e.id, "name": e.name, "profile": (e.profile or "")}   # PF-3: 150 매직 컷 제거(profile=공개·현재만·유계)
            if (getattr(e, "arc_note", "") or "").strip():
                c["arc_note"] = e.arc_note.strip()
            chars.append(c)
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
               # DP-11: lazy 분해 경로도 required_events 를 '구체적으로 일어나는 사건'으로 긍정 정의(상태 명사 차단) — build_spine 과 대칭.
               # DP-20 ⓒ: gloss 확장(긍정형) — 필수 사건의 절반 이상을 '주인공의 행동이 세계를 바꾸는 사건'으로(build_spine 과 대칭).
               "각 에피소드의 required_events 는 '구체적으로 일어나는 사건 — 인물이 무엇을 하는가'로 담아라(상태 명사 아님) — "
               "이 중 절반 이상은 주인공의 행동이 세계를 바꾸는 사건으로(얻어내고·뒤집고·움직이게 만드는 사건). "
               # DP-3' ⓐ: 정산 배치(lazy 경로도 대칭) — 에피소드마다 payoff 최소 1개 + 위치(early|mid|climax). genre-blind.
               "각 에피소드에는 독자에게 실제로 지불되는 사건(payoff — 이 작품의 톤이 약속한 보상의 실현)을 최소 하나 두고, "
               "그것을 payoffs 에 담고 위치를 payoff_at(early|mid|climax)으로 밝혀라(보상의 종류·크기는 이 작품이 정함). "
               # DP-20 ⓑ: 이자 트랙(lazy 경로도 대칭·긍정형) — 대정산과 별개의 에피소드 내 소정산 + 핵심 쾌감 첫 집행을 아크 전반부에.
               "대정산(아크의 핵심 쾌감 집행)과 별개로, 각 에피소드에는 그 에피소드 안에서 닫히는 소정산 — "
               "주인공의 우위가 관측 가능한 결과로 환금되는 사건 — 을 하나 두라. "
               "이 아크가 약속한 핵심 쾌감의 첫 집행은 아크의 전반부 에피소드 안에 배치하라(끝까지 미루지 마라). "
               # SP-2 M9: 규칙 예산 — build_spine 과 동일 바이트(한쪽 누락 시 아크 2부터 규칙 인플레 재발).
               f"{NEW_RULE_LINE} "
               "이 아크에 필요한 신규 인물(조연·적대) 0~4명은 new_cast 로 '등장 전에 설계'하라 — "
               "지금까지의 이야기 상태에서 태어나야 한다. profile=공개·현재 정체(배경·성격·현재 욕망·현재 관계, 말투 슬롯은 비워 둔다), arc_note=앞으로의 궤적·뒤에 드러날 면모(선택·있을 때만). JSON만.")
        budget_line = f"[남은 회차 예산]{remaining}화 — 에피소드 target_chapters 합이 이 예산에 맞게.\n" if remaining else ""
        # 작품 척추(전제·중심질문)를 lazy 단계에도 물려줌 — 엔딩 한 줄만 보고 핵심 장치를 잃지 않게
        usr = (budget_line +
               f"[작품 전제]{world.premise or ''}\n[중심 질문]{cq}\n"   # 절단 전면 제거(2026-08-21): 전제 전문
               # DP-20 ⓐ: 계약 주입 비대칭 해소 — lazy 분해 경로도 쾌감 엔진·전제 자산을 보게(build_spine 과 대칭).
               f"{_contract_block(world)}"
               f"[엔딩]{ending}\n[아크]{arc.title} / 목표:{arc.goal} / 갈등:{arc.central_conflict} / 전환:{arc.turning_point}\n"
               f"{rollup_block}"
               f"[인물]{json.dumps(chars, ensure_ascii=False)}\n[최근 줄거리]\n" + "\n".join(recent) +
               '\n{"episodes":[{"title":"","premise":"","climax":"","required_events":[],"required_cast":[],'
               '"plants":[],"payoffs":[],"payoff_at":"climax",' + _NEW_RULE_SCHEMA + ',"target_chapters":4}],'
               '"new_cast":[{"name":"","profile":"","arc_note":"","debut_episode":1}]}')
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
        # B-31: 방금 분해한 아크가 '최종 아크'면 정산/여운 에피소드를 절정 에피소드 뒤에 보장(멱등·중립).
        #        (헬퍼가 최종 아크만 대상 — 중간 아크 분해 시엔 무동작). 완결 클라이맥스 절단 소스 차단.
        self._ensure_final_settlement(world.spine)

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
    @promptlog.stage("event_menu")
    def generate_event_menu(self, world: WorldConfig, arc: Arc, episode: Episode,
                            recent: list[str], cast_context: str = "", plant_notes: str = "",
                            outstanding: list[str] | None = None,
                            required_override: list[str] | None = None,
                            time_facts: list[str] | None = None,
                            structure_history: dict | None = None,
                            recent_key_events: list[str] | None = None,
                            bus=None) -> list[str]:
        """에피소드가 활성화되는 시점에 '구체적 한 줄 사건' 풀(8~12)을 신선 컨텍스트로 생성한다.
        T1의 천장(에피소드 required_events 가 빈약하면 비트가 끌어올 재료가 없음)을 올리는 게 목적.

        설계 불변식:
        - NEVER throws · NEVER empty — LLM 실패 시 결정론 폴백(required_events·climax·만기약속·payoffs).
          (활성 가드가 회귀 테스트의 Fake provider 경로를 타므로 예외/빈 반환 금지가 필수.)
        - required_events 는 '코드로' 무조건 맨 앞에 보존 — 프롬프트 지시만으론 LLM 이 풍부한 메뉴 쪽으로
          치우쳐 빈약한 required_events 를 누락 → 본문이 메뉴만 실현하고 required 미실현 → T2 event_uncovered
          역증가(T2 역설). no-whack-a-mole: '프롬프트 지시 한계'는 이미 입증됨 → 코드 강제.
        - 메뉴는 advisory '후보 풀'이지 '지시'가 아니다(억지 회수·온레일 금지). 약속/복선 라벨은 원문 그대로(ledger _key 정합).
        - B-37: recent_key_events(최근 N화 실현 사건)가 전달되면, LLM 메뉴 후보 중 그 실현 사건과 사건 술어까지
          겹치는(어간 containment≥0.4·공유≥3) 재탕 후보를 **코드로 제거**한다(보이지 않게 — 이력 주입 아님, B-32e 안전).
          보존 목록(미실현 required·climax·만기약속·payoffs)은 불가침(DP-13 HIGH 교훈). 미전달(None) → no-op(하위호환)."""
        # required_override(T4 refresh): 이미 실현된 required 를 뺀 '미실현만' 전달 → 소진 사건 재투입 방지.
        _req_src = required_override if required_override is not None else (episode.required_events or [])
        req = [e for e in _req_src if (e or "").strip()]
        due = [o for o in (outstanding or []) if (o or "").strip()][:6]
        # SP-2 M8: '이미 등장한 것' 정본 목록 — 재조합 계약의 소스를 라이브 캐논에서 렌더(소스 없는
        #   재조합 지시는 모델이 '이미 등장한 것'을 발명한다 — 프라이어 재발명 실측 계보).
        #   도구=장르 계약 정본 열거 파싱 · 규칙=world_rules · 인물=캐스트. 전부 빈 작품이면 블록·계약 무추가(바이트 동일).
        appeared: list[str] = []
        # RC-3: 도구 정본 이중 소스 — object 엔티티 우선, vocabulary_tone 정규식 폴백(단일 문자열 단일 장애점 해소).
        #   엔티티 없는 작품 = 정규식 경로 바이트 동일(하위호환).
        from ..domain.world import object_entity_names, canon_tool_vocab_match
        _tool_names = object_entity_names(world)
        if _tool_names:
            appeared += _tool_names
        else:
            _mt = canon_tool_vocab_match(world)
            if _mt:
                appeared += [t.strip() for t in _re.split(r"[·,]", _mt.group(1)) if t.strip()]
        appeared += [r.text.strip() for r in (world.world_rules or []) if (r.text or "").strip()][:8]
        appeared += [e.name for e in world.entities if e.etype == "character" and (e.name or "").strip()]
        appeared_block = ("[이미 등장한 것]\n" + "\n".join(f"· {x}" for x in appeared) + "\n") if appeared else ""
        recomb_line = ("후보의 절반 이상은 [이미 등장한 것]의 항목을 둘 이상 엮어 만든다. "
                       "같은 도구·규칙이라도 이번에는 다른 행동으로 쓴다. ") if appeared else ""
        menu: list[str] = []
        try:
            # B-33: 결정론 클록 파생값(계약 만기 잔여·현재 나이) — 메뉴 생성기가 '삼 년 기한=임박' 류 거짓 임박 사건을
            #        발명하던 소스 차단(에피소드 시작 시점 기준. 앵커 미선언 → 빈 블록·프롬프트 바이트 동일). 산수는 코드가 함.
            time_block = ("[시간 기준(결정론 — 이 에피소드 시작 시점. 이 값과 모순되는 기한 도래·나이 서술 금지)]\n"
                          + "\n".join(f"· {t}" for t in time_facts) + "\n") if time_facts else ""
            # B-32: 최근 회차 구조 이력(훅/기능/장소/key_events)을 참고로 노출 — 메뉴가 과거 구도·장치를 다시 골라
            #        구조 재탕(같은 난입·같은 원패턴)하던 소스 차단. 미전달/빈 이력 → "" (프롬프트 바이트 동일).
            sh_block = _structure_history_block(structure_history)
            # SP-2 m3: 대시 시연·부정 지시를 긍정형으로 — 이 콜의 산출(event_menu)이 스토리 패스의
            #   [사건 재료]로 들어가고, 그 재료의 대시를 story_pass 위생 축이 센다(위생 카운터 소스 차단).
            sys = ("이 에피소드(3~10화 분량) 전체에서 '실제로 일어나는 구체적 한 줄 사건' 8~12개를 만들어라. "
                   "각 후보는 장면으로 바로 쓸 수 있는 행동 한 줄로 쓴다. "
                   "① 에피소드 '필수 사건'을 맨 앞에 모두 포함하고 더 구체화한다. "
                   "② 만기된 미회수 약속/복선이 있으면 그 '회수(지불) 사건'을 다음에 배치(억지 회수 아닌 자연스러운 정산). "
                   "③ 인물의 욕망·직전 화가 남긴 미결 상태·세계 고유 설정에서 신선한 사건을 더한다. ④ 에피소드 절정으로 수렴한다. "
                   + recomb_line +
                   "이건 '지시'가 아니라 비트가 골라 쓸 '후보 메뉴'다. 약속/복선 라벨은 원문 그대로 옮겨 적는다. "
                   '{"event_menu":["사건1","사건2","..."]} JSON만.')
            # FS-1: [중심 질문] 주입 제거 — 중심 질문은 정의상 최종 반전을 담는다. 회차 단위 계층(메뉴·비트)이 매번
            #   반전을 보면 plants 스케줄 밖 떡밥 사건을 발명한다(DP-5 4화 실측). 반전 유입은 plants 슬롯만.
            usr = (time_block +
                   # CX-9: 적시 사건 메뉴에서도 장르 정체성 블록 제거 — 회차 인접 계층이라 비트와 같은 스티어링
                   #   경로다(DP-20 ⓐ의 계약 대칭 주입은 CX-9 의 '회차 인접 계층 제거' 원칙으로 대체).
                   f"[아크 목표]{arc.goal}{(' · 갈등:'+arc.central_conflict) if arc.central_conflict else ''}"
                   f"{(' · 전환점:'+arc.turning_point) if arc.turning_point else ''}\n"
                   f"[에피소드]{episode.title} / 도입:{episode.premise}\n[에피소드 절정]{episode.climax}\n"
                   f"[필수 사건 — 맨 앞에 모두 포함·구체화]{req}\n"
                   f"[만기 약속/복선 — 자연스러우면 회수]{due}\n"
                   f"[심은 복선]{list(episode.plants or [])}\n[회수 예정]{list(episode.payoffs or [])}\n" +
                   (f"[등장 인물 — 이름·설정·현재 상태]\n{cast_context}\n" if cast_context else "") +
                   (f"[참고 미회수 복선]{plant_notes}\n" if plant_notes else "") +
                   appeared_block +
                   sh_block +
                   "[최근 줄거리]\n" + "\n".join(recent[-3:]) + "\n"   # 절단 전면 제거(2026-08-21): 구 [-280:] 문장 중간 머리 절단 소거(회차 수 선택은 [-3:] 유지)
                   '{"event_menu":[]}')
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.4)
            menu = [str(s).strip() for s in (d.get("event_menu") or []) if str(s).strip()][:12]
        except Exception:
            menu = []   # 결정론 폴백 — 아래 seed 합성이 빈 메뉴를 required·climax·약속으로 메운다(never empty)
        # B-37 ⓐ: LLM 메뉴 후보에서 '최근 실현 사건 재탕'을 코드로 제거(seed 합성 前 — 재탕이 seed 로 다시 들어와도
        #   보존 대상이라 안 빠질 수 있으므로, 필터는 순수 LLM 신선 후보(menu)에만 적용한다). 보존 목록은 seed 재료
        #   자체(req/due/climax/payoffs)이므로 여기 preserve 로 넘겨 '재탕처럼 보이는 정상 실현 재료'도 안전하게 지킨다.
        #   recent_key_events 미전달(None) → event_menu_filter no-op(원본 반환·하위호환).
        _preserve = req + due + ([episode.climax] if episode.climax else []) + list(episode.payoffs or [])
        menu = event_menu_filter(menu, recent_key_events, preserve=_preserve)
        # 코드 강제: required_events 를 무조건 맨 앞에 보존 + (LLM 실패 시) 결정론 재료 합성 → dedup·cap 12.
        seed = req + menu + due + ([episode.climax] if episode.climax else []) + list(episode.payoffs or [])
        out = list(dict.fromkeys(s.strip() for s in seed if (s or "").strip()))[:12]
        out = out or [episode.premise or episode.climax or f"{episode.title} 전개"]
        # SP-2 검사 5: 재조합 계수 — [이미 등장한 것] 항목 2개 이상을 엮은 후보 비율(결정론 카운트,
        #   임계 없이 emit만 — 무강제. 규칙 전문은 후보에 원문 재현이 드물어 도구·인명 위주로 세는 보수치).
        if bus is not None and appeared:
            try:
                rc = sum(1 for c in out if sum(1 for a in appeared if a and a in c) >= 2)
                bus.emit("worldgen", "menu_recombination",
                         episode=episode.episode_id, recombined=rc, total=len(out))
            except Exception:
                pass
        return out

    # ---- 3.6) RC-1: 회차 플롯 예산 분배(에피소드 활성 시 1콜 — 회차당 0) ----
    @promptlog.stage("slot_budget")
    def distribute_slots(self, world: WorldConfig, arc: Arc, episode: Episode,
                         menu: list[str] | None = None,
                         required_override: list[str] | None = None, bus=None) -> list:
        """에피소드의 사건 풀(메뉴 + 필수 사건 + 절정)을 회차 슬롯 1..target_chapters 의 몫으로 배분한다.

        설계(RC-1 ⓐ):
        - 절정(climax)은 마지막 슬롯에 수렴, 슬롯별 중심 사건 1 + 보조 n.
        - 슬롯별 서사기능 태그(긍정형 — chapter_function 어휘 setup/escalation/payoff/relation/respite 재사용).
        - 산출은 SlotPlan 리스트(신규 스키마 최소). slot_event/planned_event(중심)·재료 스코핑(중심+보조)·
          역할 파생(function)·줄 수 스케일(사건 수)의 단일 소스.

        불변식: NEVER throws · NEVER empty — LLM 실패/빈 산출 시 결정론 폴백(필수·메뉴·절정을 라운드로빈 배분).
        분배는 후보 풀 결정론 조정이지 프로즈 준수 게이트가 아니다(무강제·본문 게이트 0)."""
        from ..domain.narrative import SlotPlan
        target = max(1, int(getattr(episode, "target_chapters", 0) or 1))
        _req_src = required_override if required_override is not None else (episode.required_events or [])
        req = [str(e).strip() for e in _req_src if (str(e) or "").strip()]
        pool = [str(x).strip() for x in (menu or episode.event_menu or []) if str(x).strip()]
        climax = (episode.climax or "").strip()
        # [치명 1a] climax 는 배치 라벨([에피소드 절정])에만 남기고 pool·fill 에서 결정론 제외(정확 일치) —
        #   generate_event_menu 가 seed 에 climax 를 항상 붙여 pool 에 원문 상존하므로, 그대로 두면 모델이
        #   climax 를 '평범한 배분 대상 pool 사건'으로도 봐서 앞 화차 central 로 앞당긴다(조기절정 어트랙터 배관 소스).
        pool = [x for x in pool if x != climax]
        # [경미 4] target≤1: '앞은 열고 뒤는 맺기'가 단일 슬롯 자기모순 → LLM 콜 없이 결정론 단일 슬롯(전량 배분).
        if target <= 1:
            central = climax or (pool[0] if pool else (req[0] if req else (episode.premise or f"{episode.title} 전개")))
            support = [x for x in (req + pool) if x and x != central]
            return [SlotPlan(slot=1, function="payoff", central=central, support=support)]

        rows: list[dict] = []
        try:
            sys = (
                "너는 웹소설 에피소드를 회차별 몫으로 나누는 설계자다. 아래 에피소드의 사건 풀을 "
                f"1화차부터 {target}화차까지 각 회차의 몫으로 배분하라. "
                "각 회차에는 그 회차의 중심 사건 하나(central)와 이를 받치는 보조 사건 몇(support)을 둔다. "
                "central·support 에 담는 사건은 [필수 사건]·[사건 풀]의 항목을 원문 그대로 옮긴 것이다"
                "(있는 사건을 고르고 나누는 일이지 새 문구를 짓는 일이 아니다). "
                "이 에피소드의 절정(climax)은 마지막 화차에서 터지게 배치하라. "
                "앞 화차는 상황을 열고 쌓고, 뒤로 갈수록 조여 마지막 화차에서 맺힌다. "
                "각 회차에는 그 회차가 독자에게 주는 서사 기능을 function 으로 태그하라. "
                f"function 은 {'/'.join(SLOT_FUNCTIONS)} 중 이 회차가 독자에게 주는 것 하나로 정한다. "
                "필수 사건은 빠짐없이 어느 회차엔가 배분하고, 각 사건은 한 회차의 몫으로만 둔다(회차마다 다른 사건). JSON만.")
            usr = (
                f"[에피소드]{episode.title} / 도입:{episode.premise}\n"
                f"[에피소드 절정]{climax}\n"
                f"[회차 수]{target}\n"
                f"[필수 사건]{req}\n"
                f"[사건 풀]{pool}\n"
                '{"slots":[{"slot":1,"function":"setup","central":"이 회차의 중심 사건 하나","support":["보조 사건"]}]}')
            d = self.provider.chat_json([{"role": "system", "content": sys},
                                         {"role": "user", "content": usr}], temperature=0.4)
            rows = [r for r in (d.get("slots") or []) if isinstance(r, dict)]
        except Exception:
            rows = []   # 결정론 폴백 — 아래가 필수·메뉴·절정으로 슬롯을 채운다(never empty)

        plans: list[dict] = []
        for i in range(1, target + 1):
            row = next((r for r in rows if int(r.get("slot", 0) or 0) == i), None)
            central = str((row or {}).get("central") or "").strip()
            support = [str(s).strip() for s in ((row or {}).get("support") or []) if str(s).strip()]
            fn = str((row or {}).get("function") or "").strip()
            plans.append({"slot": i, "function": fn, "central": central, "support": support})

        # [치명 1b] 절정 위치 강제(코드 보정·결정론): climax 는 오직 마지막 슬롯에만 실린다 — 존재 검사만으론
        #   비마지막 슬롯의 조기절정을 통과시킨다. 비마지막 슬롯의 central/support 에 climax 가 있으면 거기서
        #   빼고(중심은 비워 아래 fill 이 재채움) 마지막 슬롯으로 옮긴다(기존 존재 검사와 대칭).
        if climax:
            last_idx = len(plans) - 1
            for i, p in enumerate(plans):
                if i == last_idx:
                    continue
                if p["central"] == climax:
                    p["central"] = ""
                p["support"] = [s for s in p["support"] if s != climax]
            last = plans[last_idx]
            if climax != last["central"] and climax not in last["support"]:
                if not last["central"]:
                    last["central"] = climax
                else:
                    last["support"] = [climax] + last["support"]

        # 결정론 채움: 중심 빈 슬롯을 '아직 안 쓴' 필수·메뉴로 채우고, 남은 재료는 보조로 라운드로빈 분배(never empty).
        used = {p["central"] for p in plans if p["central"]}
        used |= {s for p in plans for s in p["support"]}
        fill = [x for x in (req + pool) if x not in used]
        fi = 0
        for p in plans:
            if not p["central"]:
                if fi < len(fill):
                    p["central"] = fill[fi]; used.add(fill[fi]); fi += 1
                else:
                    p["central"] = climax or episode.premise or f"{episode.title} 전개"
        # 남은 재료를 보조로 순환 분배(스코핑이 이 슬롯 몫을 갖게 — 폴백에서도 밀도 확보)
        rest = [x for x in fill[fi:] if x not in used]
        for k, x in enumerate(rest):
            plans[k % target]["support"].append(x)
        # 서사기능 폴백(빈·이상 태그 → 위치 기반 긍정 태그)
        for idx, p in enumerate(plans):
            if p["function"] not in SLOT_FUNCTIONS:
                p["function"] = _fallback_slot_function(idx, target)

        if bus is not None:
            try:
                bus.emit("worldgen", "slot_budget", episode=episode.episode_id, target=target,
                         functions=[p["function"] for p in plans],
                         slot_sizes=[1 + len(p["support"]) for p in plans])
            except Exception:
                pass
        return [SlotPlan(**p) for p in plans]

    # ---- 4) 에피소드 → 회차 beat 파생(절정으로 수렴, finale면 절단신공) ----
    @promptlog.stage("beat_plan")
    def beat_for_episode(self, world: WorldConfig, arc: Arc, episode: Episode, chapter: int,
                         is_finale: bool, recent: list[str], directives: list[str],
                         plant_notes: str = "", cast_context: str = "",
                         event_menu: list[str] | None = None,
                         time_facts: list[str] | None = None,
                         structure_history: dict | None = None,
                         required_override: list[str] | None = None,
                         climax_override: str | None = None,
                         recent_hook_types: list[str] | None = None,
                         recent_closing_devices: list[str] | None = None,
                         recent_scene_forms: list[str] | None = None) -> Beat:
        char_ids = [e.id for e in world.entities if e.etype == "character"]
        # DP-6: 에피소드 소비 원장 — 이미 지면에 실현된 required/climax 는 호출자(copilot)가 결정론 커버리지로 차감해
        #       override 로 넘긴다. 제외는 '보이지 않게' 코드로(override 로 슬롯 자체를 줄임) — '이미 한 장면 반복 금지' 류
        #       지시를 프롬프트에 넣지 않는다(앵커링·pink-elephant: 피할 대상을 노출하면 오히려 끌려간다).
        #       override 미전달(None) → episode 값 그대로 사용 = 하위호환.
        eff_required = required_override if required_override is not None else (episode.required_events or [])
        eff_climax = climax_override if climax_override is not None else episode.climax
        # 발단(도입부): 1화 압축 금지 + 구체적 grounding. 단 '도입부 길이는 작품이 정한다' — 고정 N화 강제 금지(사용자 지적).
        # '첫 에피소드(arc1/ep1) 안'이라는 상대 위치로만 판단하고, 몇 화로 펼칠지는 에피소드 플래너(target_chapters)가 정함.
        in_opening = (getattr(arc, "order", 1) == 1 and getattr(episode, "order", 1) == 1)
        if chapter == 1:
            hook = ("이번은 작품의 '첫 회차(도입부 시작)'다. 본격 엔진(레벨업·공략·각성 후 활약 등)으로 직행하지 마라. "
                    "주인공이 '누구'인지를 한 줄 라벨('10년 게임한 고인물이다' 식)이 아니라 *구체적인 장면*(직업·하루의 결·처지·결핍·관계)으로 보여줘라 — "
                    "손에 잡히는 디테일로(전독시가 김독자를 '지하철 통근하며 웹소설 읽는 미노소프트 계약직, 그 소설의 유일한 독자'로 그린 식). "
                    "그 일상 위로 전제의 전환이 발발하는 데까지. 도입부를 1화에 다 욱여넣지 말고(전환의 충격·첫 의문의 미결 훅으로 끝맺어도 좋다) "
                    "이어질 여지를 남겨라. 다만 억지로 늘리지도 마라 — 도입부 길이는 작품의 호흡이 정한다.")
        elif in_opening and not is_finale:
            hook = ("아직 작품 '도입부'(첫 에피소드 초반) 안이다 — 1화에서 연 도입을 이어받아 전환의 여파와 세계 규칙을 주인공의 행동으로 풀어내며 "
                    "인물·세계가 자리잡게 하라(직전 화 사건 반복 금지, 한 단계 전진). 도입이 무르익기 전에 곧장 본격 엔진을 최대치로 폭주시키진 말되, "
                    "엔진은 이야기 흐름에 맞게 자연히 가동되기 시작해도 된다(언제 본격화할지는 작품이 정함).")
        elif is_finale:
            hook = "이번 회차가 에피소드 절정(finale): 아래 climax 를 이번 회차에서 터뜨려라(끝맺음 방식은 작품 문체 정책을 따름)."
        else:
            hook = "에피소드 절정으로 한 걸음 전진. 아직 절정을 다 터뜨리지 말 것."
        # B-32: pink-elephant 부정명령(리셋·재연·반복 '금지') → 긍정형 대체. 아래 [최근 회차 구조 이력]을 참고로
        #        '구별되는' 새 구도·전개를 우선 선택하게(정보 제공+긍정 지시, 강제 아님).
        cont = ("" if chapter == 1 else
                " 직전 화 말미에 열린 미결 상태(그 화의 톤이 무엇으로 끝났든)를 이번 화 도입에서 이어받아 다루라 — "
                "최근 회차들과 구별되는 새 구도·장치·전개 방식을 우선 선택하고, 같은 장치를 다시 쓸 때는 이전과 다른 결과가 나게 하라. "
                "공간 또는 상황을 한 단계 전진시켜라.")
        # B-37 ⓑ: hook_type 화이트리스트에서 '최근 N화 사용 유형'을 코드로 뺀 축소 리스트를 제시(순환 — 금지문 아님,
        #   보이는 선택지 자체를 줄여 앵커링 안전). 미전달(None) → 전체 화이트리스트(하위호환·바이트 동일). 전 유형 소진 시 전체 복원.
        _hook_choices = hook_whitelist(recent_hook_types)
        # DP-22 ⓑ: closing_device(회차가 물리적으로 닫히는 장치) 화이트리스트에서 '최근 2화 사용 장치'를 코드로 뺀 축소
        #   리스트를 제시(순환 — 금지문 아님, 보이는 선택지 자체를 줄여 앵커링 안전). 미전달(None) → 전체(하위호환·바이트 동일).
        #   전 장치 소진 시 전체 복원. 과거 회차 분류는 하지 않는다(자기 라벨만 순환 입력 — 검출기 0).
        _closing_choices = closing_whitelist(recent_closing_devices)
        # 뜻풀이도 '제시된 선택지'로만 한정한다 — 제외된 장치를 뜻풀이에 노출하면 순환(보이는 선택지 축소)의 앵커링 안전이
        #   깨진다(pink-elephant). 아래 매핑은 CLOSING_WHITELIST 와 단일 출처(누락/추가 시 함께 갱신).
        _closing_gloss = {"dialogue": "대사로 끊기", "action": "행동/동작", "sensory": "감각/이미지",
                          "object": "사물/화면/문서", "arrival": "인물 등장/신호 도착", "interior": "화자 내면 한 줄"}
        _closing_desc = "·".join(f"{c}={_closing_gloss[c]}" for c in _closing_choices if c in _closing_gloss)
        # SX-1: scene_form(이 회차 중심 장면 안무) 화이트리스트에서 '최근 2화 사용 형태'를 코드로 뺀 축소 리스트를 제시(순환 —
        #   금지문 아님, 보이는 선택지 자체를 줄여 앵커링 안전). 미전달(None) → 전체(하위호환·바이트 동일). 전 형태 소진 시 전체 복원.
        #   과거 회차 분류는 하지 않는다(자기 라벨만 순환 입력 — 검출기 0·B-32e 준수: 이력 텍스트를 프롬프트에 노출 안 함).
        _scene_form_choices = scene_form_whitelist(recent_scene_forms)
        sys = ("에피소드 안에서 다음 회차 1개의 beat 를 설계하라. 절정으로 수렴하되 기존 설정과 모순 금지. " + hook + cont +
               " 세계규칙·전제가 정한 이 작품 고유의 체계(사회·제도·관계·자원 등 무엇이든)를 사건의 구체 디테일로 쓰라(인포덤프 금지, 행동·대사·선택으로 흘려라). "
               f"key_events 는 이 회차를 약 {world.style.target_chars_per_chapter}자로 자연스럽게 채울 만큼의 '구체적으로 일어나는 사건'을 담아라 — "
               "보통 3~5개(도입·휴지 회차는 적게, escalation·finale 회차는 절정 사건을 더 몰아서). 억지로 2개로 줄이지 마라(회차가 빈약·저밀도가 되는 원인). "
               "에피소드 필수 사건 중 이번 회차가 다룰 것을 골라 분배하되, 한 회차에 과밀(8개 이상)도 금지. "
               "'적시 사건 메뉴'가 제공되면 그 풀에서 골라 key_events 를 풍부하게 구성하라 — 단 '필수 사건'을 반드시 우선 실현하고(메뉴가 필수 사건을 밀어내지 마라), 메뉴는 보강 재료다. "
               # DP-2: 주인공 선수(先手) — '이 회차에서 주인공이 스스로 여는 수' 슬롯. 긍정형 단문(피할 대상 비노출·강제 게이트 없음).
               #        인물의 욕망(cast_context)에서 도출하게 하고 그 수를 key_events 에 담게 한다(반응 일변도로 주인공 증발 소스 차단).
               #        예시는 장르중립 행위태(결정·선택·먼저 나섬)만 — 갈등톤 예시(역습·판 설계)는 잔잔물 앵커링 위험이라 배제(genre-blind 소스 차단).
               "그리고 매 회차 주인공의 의지가 최소 한 번 사건을 연다(결정·선택·먼저 나섬) — 인물의 욕망에서 도출해 그 수를 key_events 에 담아라. "
               # DP-20 ⓓ: 이자 집행(비트 레벨·긍정형) — 장기 자산(핵심 동력 전제)의 우위 하나를 이 회차 안에서 관측 가능한
               #          결과로 '닫히게' 하는 비트를 우선 배치(자산 원금은 보존 — 소모하지 말고 이번 회차 몫만 환금).
               "이번 회차에는 이 작품의 핵심 동력 전제(장기 자산)가 준 우위 하나를 이 회차 안에서 관측 가능한 결과로 닫히게 하는 사건을 우선 배치하라 — "
               "그 자산 자체(원금)는 보존하고 이번 회차 몫의 이득만 환금해 key_events 에 담아라. "
               # DP-17: 대사 회복(비트 레벨) — 지문 '벽'의 절반은 장면에 사람이 없어서다(design-dp17-voice.md §2).
               #        긍정형('상대를 배치하라' — 독백 회차 금지가 아님)·DP-11 사건성 계보(상호작용 사건 우선). 강제 게이트 없음.
               "이번 회차에 주인공이 다른 인물과 말을 주고받는 장면을 최소 한 개 배치하라 — 상대의 대사·반응이 사건을 굴리는 상호작용 장면을 key_events 에 우선 담아라(인물이 있어야 대사가 산다). "
               # G4: chapter_function/hook_type/time_advance/place 는 '강제'가 아니라 '네 계획을 그대로 라벨링'(서술 메타데이터).
               # 이 라벨로 회차 내용을 바꾸라는 게 아니라, 설계한 회차가 어떤 기능·끝맺음·시간·장소인지 자기 기술하라는 것(작가 가시화·분석용).
               "끝으로 설계한 이 회차를 자기 기술하라(내용을 바꾸지 말고 있는 그대로 짧은 라벨만 — 분석용 메타데이터라 한 단어로) — "
               "chapter_function(독자에게 주는 것: payoff/setup/escalation/relation/respite 중 하나), "
               # B-37 ⓑ: 제시 유형 목록은 최근 사용분을 뺀 축소 화이트리스트(_hook_choices) — 순환 제시(강제 아님).
               f"hook_type(회차말 끊는 방식 한 단어: {'/'.join(_hook_choices)} 중 가장 가까운 것), "
               # DP-22: closing_device — 회차가 '물리적으로 닫히는 장치'(끊는 '방식'인 hook_type 과 직교: 마지막 순간을
               #   무엇으로 닫느냐). 선언+이행 문안(그 장치로 닫아라). 제시 선택지는 최근 2화 사용분을 뺀 축소 화이트리스트
               #   (_closing_choices) — 순환 제시(강제 아님·보이는 선택지 축소로 마무리 다양성 유도).
               f"closing_device(이 회차의 마지막 순간이 어느 장치로 닫히는지 한 단어로 선언하고 그 장치로 닫아라 — "
               f"{'/'.join(_closing_choices)} 중 가장 가까운 것; {_closing_desc}), "
               # SX-1: scene_form — 이 회차의 '중심 장면 안무'(무엇을 하는 회차인가). closing_device(닫는 장치)·hook_type(끊는 방식)과
               #   직교. 선언+이행 문안(그 안무로 전개하라). 제시 선택지는 최근 2화 사용분을 뺀 축소 화이트리스트(_scene_form_choices) —
               #   순환 제시(강제 아님·보이는 선택지 축소로 장면 안무 다양성 유도). 접촉→복원→은폐 3연속·심부름 트릭 재사용 소스 차단(4화 정독).
               f"scene_form(이 회차의 중심 장면 안무를 다음 중에서 선언하고 그 안무로 전개하라 — "
               f"{'/'.join(_scene_form_choices)} 중 가장 가까운 것), "
               "time_advance(직전 화 대비 시간 경과 짧게: 예 '없음'/'몇 분'/'다음날'/'사흘 후'), "
               "time_delta(같은 경과를 구조화 — amount(숫자)·unit(minute|hour|day|week|month|year)·mode(보통 advance, "
               "이 회차가 과거 회상이면 flashback, 같은 시각 다른 장소면 parallel). 예 '사흘 후'→{\"amount\":3,\"unit\":\"day\",\"mode\":\"advance\"}, "
               "'없음'→{\"amount\":0,\"unit\":\"minute\",\"mode\":\"advance\"}, '몇 시간 뒤'→{\"amount\":3,\"unit\":\"hour\",\"mode\":\"advance\"}), "
               "place(주요 장소 짧게), "
               # DP-11: move 를 '지면에서 실제로 실행되는 수'로 강화 — 내면의 결심·설계(선언·예고)가 아니라 장면에서
               #        행동·대사·선택으로 실현된 수만 담게 한다(그 결심의 결과는 key_events/summary 에서 드러남).
               "protagonist_move(이 회차에서 주인공이 스스로 연 수 한 줄 — 지면에서 실제로 실행되는 수(장면에서 행동·대사·선택으로 일어난 것)를 담아라. 위 key_events 중 주인공의 의지가 사건을 연 대목을 결정·선택·먼저 나섬 관점에서 짧게; 없으면 빈 문자열). "
               # SX-3: world_reveal — 이 회차에서 독자가 *처음* 알게 되는 세계 사실 0~2개. 발명 강제 없음(밝힐 게 없으면 빈 배열 — 정직).
               #   각 항목은 설명 문단 지시가 아니라 '장면 사건으로 드러나게'(인물이 부딪히거나 목격하는 방식) — 인포덤프 차단(긍정형만·트로프 호명 없음).
               "world_reveal(이 회차에서 독자가 처음 알게 되는 세계 사실 0~2개를 배열로 — 각각 인물이 부딪히거나 목격하는 장면 사건으로 드러나게 하라. 새로 밝힐 세계 사실이 없으면 빈 배열 []). "
               "title 은 회차 제목만(시리즈명·화수 붙이지 마라). JSON만.")
        # plant_notes 는 시스템 '참고' 정보 — 작가 지시(authority)와 분리된 슬롯(시스템 개입의 지시 위장 금지, 모드 계약 §1)
        notes_block = f"\n[미회수 복선 — 참고용]{plant_notes}" if plant_notes else ""
        # FS-1: [중심 질문] 주입 제거(전제만 유지) — 중심 질문은 정의상 최종 반전을 담아, 회차 비트가 매번 반전을
        #   보면 plants 스케줄 밖 떡밥을 발명한다(DP-5 4화 실측). 전제는 표면 서사만 담는 것이 저작 규칙(FS-1 lint 예정).
        spine_block = (f"[작품 전제]{world.premise}\n"   # 절단 전면 제거(2026-08-21): 전제 전문
                       if (world.premise or "").strip() else "")
        # G6: 인물을 id 문자열이 아니라 '이름·설정·현재 상태·관계'로 보게(컨텍스트 기아 해소 — 욕망 있는 인물에서 사건이 나오게)
        cast_block = (f"[등장 인물 — 이름·설정(배경·성격·욕망·관계)·현재 상태]\n{cast_context}\n[유효 인물 id]{char_ids}\n"
                      if cast_context else f"[인물 id]{char_ids}\n")
        # T3: 적시 사건 메뉴 = 후보 풀(지시 아님). 필수 사건 슬롯은 그대로 두고 그 '아래'에 배치(필수 우선·메뉴 보강).
        menu_block = (f"[적시 사건 메뉴 — key_events 채울 후보 풀(지시 아님, 필수 사건 우선 실현 후 보강)]{list(event_menu)}\n"
                      if event_menu else "")
        # B-33: 결정론 클록 파생값(계약 만기 잔여·현재 나이) — 설계가 시간맹에서 벗어나게(예: 6일차에 '만기 도래' 발명 차단).
        #        이 값은 코드가 story_clock 로 계산한 사실이다(모델이 산수 발명 금지). time_delta 자기 라벨링 계약은 그대로.
        time_block = ("[시간 기준(결정론 — 이 값과 모순되는 기한·나이·경과 서술 금지)]\n"
                      + "\n".join(f"· {t}" for t in time_facts) + "\n") if time_facts else ""
        # B-32: 최근 회차 구조 이력(훅/기능/장소/key_events) 참고 블록 — 위 cont 의 '구별되는 전개' 긍정 지시의 근거.
        #        미전달/빈 이력 → "" (프롬프트 바이트 동일 하위호환).
        sh_block = _structure_history_block(structure_history)
        # CX-9: 장르 정체성 블록을 회차 비트 콜에서 제거 — '서늘함' 류 한 단어 스티어링이 매 화 같은 온도를
        #   강제하는 간접 압박(사용자 실측). 계약은 작품 1회(build_spine)·에피 분해(_gen_episodes)에만 남고,
        #   드리프트는 심사(gate) 축이 감시한다(생성 조향 아님 — 무강제).
        usr = (spine_block + time_block +
               f"[아크 목표]{arc.goal}{(' · 중심 갈등:'+arc.central_conflict) if arc.central_conflict else ''}{(' · 전환점:'+arc.turning_point) if arc.turning_point else ''}\n"
               f"[에피소드]{episode.title} / 도입:{episode.premise}\n[에피소드 절정]{eff_climax}\n"
               f"[필수 사건]{eff_required}\n[등장해야 할 인물 id]{episode.required_cast}\n" + menu_block +
               f"{cast_block}{sh_block}[최근 줄거리]\n" + "\n".join(recent) +
               f"\n[작가 지시]{json.dumps(directives, ensure_ascii=False)}{notes_block}\n"
               f"[회차]{chapter} (에피소드 내 finale={is_finale})\n"
               '{"title":"","summary":"","key_events":["구체 사건1","구체 사건2","구체 사건3","(분량 채울 만큼 더)"],"entities":["인물 id"],'
               '"chapter_function":"","hook_type":"","closing_device":"","scene_form":"","world_reveal":[],"time_advance":"",'
               '"time_delta":{"amount":0,"unit":"minute","mode":"advance"},"place":"","protagonist_move":""}')
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
                        closing_device=(d.get("closing_device") or "").strip(),   # DP-22: 마무리 장치 자기 라벨(결측=미기술)
                        scene_form=(d.get("scene_form") or "").strip(),   # SX-1: 장면 안무 자기 라벨(결측=미기술)
                        # SX-3: 독자가 처음 알게 되는 세계 사실 0~2개(계획 재료). 배열 아니면 []·항목 strip·빈 항목 제거·상한 2(발명 강제 없음).
                        world_reveal=[e.strip() for e in (d.get("world_reveal") or []) if isinstance(e, str) and e.strip()][:2],
                        time_advance=(d.get("time_advance") or "").strip(),
                        time_delta=TimeDelta.parse(d.get("time_delta")),
                        place=(d.get("place") or "").strip(),
                        protagonist_move=(d.get("protagonist_move") or "").strip())
        except Exception:
            # T1+T3 폴백: 필수 사건을 앞에 두고 적시 메뉴로 보강(빈약 회차 방지) → dedup·cap 8.
            #   DP-6: 폴백도 소진 차감된 eff_required/eff_climax 를 써야 재탕(소진 사건 재기획) 봉쇄가 폴백 경로에서 새지 않음.
            _fb = list(dict.fromkeys(e.strip() for e in
                       (list(eff_required) + list(event_menu or [])) if (e or "").strip()))[:8]
            beat = Beat(chapter=chapter, title=f"{chapter}화", summary=eff_climax or episode.premise,
                        key_events=(_fb or [eff_climax or episode.premise]),
                        entities=(episode.required_cast or char_ids[:2]),
                        arc_id=arc.arc_id, episode_id=episode.episode_id, is_episode_finale=is_finale)
        return beat
