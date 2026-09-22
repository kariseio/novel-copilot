# -*- coding: utf-8 -*-
"""CopilotService — 유스케이스 오케스트레이션(Facade).

create_project(worldgen) → generate_next_chapter(하네스 + 동적 온톨로지) → directive 주입.
영속화/세션 재수화/비용 계측을 한데 묶되, 엔진 내부는 모른다(레이어 분리).
"""
from __future__ import annotations
import time
import uuid
import json
import threading

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..config import Settings
import re

from ..domain.project import ProjectSeed, ProjectState, RegenEvent, CoverMeta
from ..domain.draft import WorldDraft, ConceptBrief
from ..domain.world import EntitySpec, WorldRuleSpec, BUILTIN_ENTITY_TYPES
from ..domain.types import (AuthorDirective, ChapterStatus, WikiPage, WikiLifecycle, RelationEdge, RetrievedItem,
                            RuleSpec, SignalGrade, ChapterRevision, Violation, RedpenMark,
                            text_fingerprint, publish_state)
from ..domain.bible import StoryBible, BibleEntry, CATEGORY_LABEL, template_for, normalize_category
from ..llm.base import LLMProvider
from ..llm.factory import create_provider, create_role_provider
from ..repository.base import ProjectRepository
from ..worldgen import WorldGenerator, BeatPlanner, ArcPlanner, BibleGenerator, WorldgenChat, ConceptChat
from ..engine.drift import episode_drift_signals
from ..engine.skills import apply_skills, SKILL_COMPOSE_CAP
from ..engine.bible_compiler import bible_digest, entry_to_world_rule, migrate_world_to_bible
from ..engine.readiness import stale_derivatives as _readiness_stale_derivatives   # XR-7①(결정론·LLM 0)
from .session import SessionManager
from .jobs import GenerationJobManager, GenerationJob
from ..telemetry import emit_intent, revise_core_diff, trim_text   # FI-1: 작가 의도 이벤트 관측(무강제·비차단)


def _usage_delta(before: dict, after: dict) -> dict:
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}


def _occ_capped(text: str, needle: str) -> int:
    """겹침 포함 등장 수(상한 2 — 0/1/2+ 판정만 필요해 전체 스캔은 하지 않는다).

    유일성 판정은 str.count(겹침 배제 — 일치 후 길이만큼 전진)가 아니라 겹침 포함(+1 전진)으로 센다.
    "X\\nX\\nX" 에서 "X\\nX" 는 count 로 1회지만 실제 위치는 2곳(0·중간) — 첫 위치 적용이 작가 의도(둘째
    위치)와 어긋날 수 있다(DE-2 클라 퍼즈 실측). edit_chapter(직접 편집)·add_redpen_mark(빨간펜 앵커
    유일성)·suggest_redpen_directions(파생 유효 판정)가 이 한 계수기를 공유한다(같은 규칙·소스 단일화)."""
    k = text.find(needle)
    if k < 0:
        return 0
    return 1 if text.find(needle, k + 1) < 0 else 2


def _accumulate(total: dict, delta: dict) -> dict:
    out = dict(total)
    for k, v in delta.items():
        out[k] = out.get(k, 0) + v
    return out


def _persisted_beat(ch) -> dict | None:
    """RV-2①: 영속 회차의 gen_context 에서 이 회차 생성 시 쓰인 beat 계획을 결정론 복원한다.
    accept 재요약이 생성 경로(harness._summarize(text, story_so_far, beat))와 동형이 되도록 — 요약 LLM 실패 시
    프로즈 서두 슬라이스가 아니라 beat.summary+key_events 로 폴백(P-2 재유입 차단). 후보 위치는
    structure_history._key_events 와 동일 우선순위(('draft','beat')·('plan',)·('beat',))로 맞춘다. 단, copilot 이
    합치는 gen_context['plan'] 은 설계 메타(arc/episode/recent…)라 summary/key_events 가 없으므로 그 경로는
    summary/key_events 를 실제로 담은 노드만 채택한다. 부재/구 레코드면 None(호출부가 beat=None → 최후 프로즈 폴백)."""
    gc = getattr(ch, "gen_context", None)
    if not isinstance(gc, dict):
        return None
    for path in (("draft", "beat"), ("plan",), ("beat",)):
        node = gc
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, dict) and ((node.get("summary") or "").strip()
                                       or [e for e in (node.get("key_events") or []) if (e or "").strip()]):
            return node
    return None


def _verified_prev_ledgers(prior, bus=None, next_ch: int = 0) -> list[tuple]:
    """XR-7②: DG-6 발췌 앵커 재료에서 '유령 인용'을 결정론으로 걸러 낸다(LLM 0콜).

    DG-6 계약 ⒝("고정 프레임을 뺀 주입 바이트 전량이 확정 회차 본문의 부분 문자열" — lookup.py `_recent_exchange`)는
    원장을 *빌드한 시점*에만 참이다. 이후 퇴고 accept·직접 편집으로 그 회차 본문이 바뀌면 원장 행의 인용문이 현재
    본문에 더는 없을 수 있고, 그대로 두면 '실재하지 않는 원문'이 다음 화 조회 응답에 앵커로 실린다. 여기서 **현재
    본문 대조**로 검증한다 — 컨텍스트 기아가 아니라 실재하지 않는 재료의 제외다(K1).

    제외 단위는 **회차 전체**다: 행 단위로 빼면 `_recent_exchange` 의 인접 창(rows[i-1:i+2])이 비인접 발화를 한
    '대화'로 오결합할 수 있어, 한 행이라도 어긋난 회차는 보수적으로 통째 뺀다. 제외 시 emit 으로 가시화한다
    (침묵 공백 금지 — K1). 전부 정합이면 반환값이 기존 조립과 동일해 프롬프트 바이트가 한 글자도 안 바뀐다."""
    from ..domain.types import ChapterStatus
    out: list[tuple] = []
    for c in sorted(prior, key=lambda c: c.chapter):
        if not (c.status == ChapterStatus.FINALIZED and c.dialogue_ledger):
            continue
        rows = list(c.dialogue_ledger or [])
        cur = c.text or ""
        ghosts = sum(1 for r in rows if str((r or {}).get("text", "")) not in cur)
        if ghosts:
            if bus is not None:
                bus.emit("assemble_memory", "ledger_quote_stale", chapter=next_ch,
                         source_chapter=c.chapter, rows=ghosts, total=len(rows))
            continue
        out.append((c.chapter, c.dialogue_ledger))
    return out


class _RebuildPreservationError(RuntimeError):
    """XR-15/21 보존 불변식 위반 — candidate 폐기 사유(원천 seed·비인물 페이지·수동 edge 손실·변형)."""


class _WikiRetrievalDisabled:
    """XR-10 단기(cross-review/007 §3): stale 위키의 생성 입력 격리 프록시 — retrieve 만 빈 결과로 무효화,
    나머지는 원본 위임(갱신·영속 경로 불변). stale 파생물의 생성 입력 유입 금지 불변식(005 하드 불변식 5)의
    소비 지점 집행이며 침묵이 아니다(호출부 wiki_excluded emit + UI 안내). 해제는 위키 재구축(rebuild_wiki)
    성공으로 stale 표식이 걷힐 때뿐이다."""

    def __init__(self, wiki):
        self._wiki = wiki

    def retrieve(self, *a, **k):
        return []

    def __getattr__(self, name):
        return getattr(self._wiki, name)


def _wiki_for_generation(wiki, stale_items, state=None) -> tuple[object, list[int], list[dict]]:
    """XR-10 단기 + XR-18: 생성에 넘길 위키 게이트 — 다음 중 하나라도 있으면 격리 프록시로 치환.
    ⑴ stale 표식(사전 점검 목록에 wiki) ⑵ 지문 불일치(기록된 반영-회차 지문 ≠ 현재 본문 — 표식 우회
    변경의 이중 방어. state 미제공/기록 없음 = 빈 목록·바이트 동일 하위호환).
    반환 (생성에 넘길 wiki, wiki-stale 회차 목록, 지문 불일치 목록). 둘 다 비면 원본 그대로."""
    chs = [d.get("chapter") for d in (stale_items or []) if "wiki" in (d.get("names") or [])]
    mm: list[dict] = []
    if state is not None:
        from ..engine.readiness import wiki_fingerprint_mismatches
        mm = wiki_fingerprint_mismatches(state, (getattr(wiki, "pages", None) or {}).values())
    return (wiki if not (chs or mm) else _WikiRetrievalDisabled(wiki)), chs, mm


def _semantic_dump(o) -> str:
    """XR-31: 객체의 '의미 전 필드' canonical 직렬화 — pydantic 은 model_dump, 평범 객체는 vars 전체.
    수동 필드 열거가 새 의미 필드마다 같은 누락을 반복하는 클래스(018 §7 — entity_types 누락)의 완화:
    필드가 늘면 자동 포섭된다. dict 값은 키 정렬로 canonical(삽입 순서 비의미)."""
    try:
        return json.dumps(o.model_dump(), sort_keys=True, ensure_ascii=False, default=repr)
    except AttributeError:
        return repr(sorted((k, repr(sorted(v.items())) if isinstance(v, dict) else repr(v))
                           for k, v in vars(o).items()))


def _wiki_source_digest(state, ontology, pov_id, wiki_pages) -> str:
    """XR-22/31(cross-review/012 §4·018 §7): 재구축 입력 중 '회차 본문 외 원천'의 결정론 digest.

    포함 = replay 가 실제로 읽는 의미 전부: ⑴ 명부 엔티티 **전 필드**(이름·별칭·타입 + 신규 필드 자동 포섭)
    ⑵ 엔티티 타입 카탈로그 **전 필드**(actor 판정 — category 변경은 같은 본문에서 탐지 대상 자체를 바꾼다·
    018 실증) ⑶ world.wiki_seeds ⑷ 비인물 위키 페이지(본문·edge·기한) ⑸ 인물 페이지 수동 edge(합성 본문은
    파생 — 제외) ⑹ 화자 결속(pov_id). **과민 방향 의도**: 무관해 보이는 필드 변경도 폐기·재시도를 낼 수
    있으나 비용은 재시도뿐이고, 과소는 015·018 이 재현한 침묵 오염이다. 필드 내부 dict는 키 정렬하되,
    엔티티 선언 순서는 roster 프롬프트·첫 actor 결속이 소비하므로 보존한다(XR-33).

    replay 는 락 밖이라 이들이 도중에 바뀌면 candidate 앞뒤가 다른 기준으로 합성된다 — 커밋 직전 **현재
    세대 세션** 기준으로 재계산해 불일치면 폐기(XR-17 동형·XR-30 세대 재해석과 짝)."""
    import hashlib
    # XR-33(cross-review/021): 엔티티 선언/삽입 순서는 Wiki.scan_present_ids→roster 프롬프트와
    # 1인칭 첫 actor 결속이 실제로 소비한다. 정렬하면 서로 다른 replay material 이 같은 digest 가 된다.
    # dict 삽입 순서를 그대로 보존해 "digest 동일 ⇒ replay 입력 동일" 방향을 지킨다.
    ents = tuple((eid, _semantic_dump(e))
                 for eid, e in (getattr(ontology, "entities", None) or {}).items())
    etypes = sorted((k, _semantic_dump(t))
                    for k, t in (getattr(ontology, "entity_types", None) or {}).items())
    seeds = sorted(_semantic_dump(s)
                   for s in (getattr(getattr(state, "world", None), "wiki_seeds", None) or []))
    pages = []
    for p in (wiki_pages or {}).values():
        edges = tuple(sorted(json.dumps(e.model_dump(), sort_keys=True, ensure_ascii=False)
                             for e in (p.typed_edges or [])))
        if p.page_type != "character":
            pages.append((p.page_id, p.page_type, p.body, p.payoff_deadline or 0, edges))
        elif edges:                     # 인물 페이지는 수동 edge 만 원천(합성 본문은 파생 — digest 제외)
            pages.append((p.page_id, "character_edges", "", 0, edges))
    return hashlib.sha256(repr((ents, etypes, seeds, sorted(pages), pov_id))
                          .encode("utf-8")).hexdigest()[:16]


def _dedup_timeline(entries: list) -> list:
    """영속 runtime_timeline 을 (entity_id,attr,eff_from,trust_tier) 키로 last-writer-wins 정리.
    재생성/작가오버라이드가 같은 시점에 다른 값을 *누적*해 ssot_ambiguous 영구 봉인을 만들던 결함 차단
    (ontology.set_state 의 in-memory dedup 과 짝 — 영속 파일도 한 시점 한 값으로). 순서(최근=뒤) 보존."""
    seen, out = {}, []
    for e in entries:
        key = (e.entity_id, e.attr, e.eff_from, getattr(e, "trust_tier", "ground_truth"))
        if key in seen:
            out[seen[key]] = e          # 같은 키 → 최신으로 교체(자리 유지)
        else:
            seen[key] = len(out)
            out.append(e)
    return out


def _recap_tag(n: int) -> str:
    """누적/최근 줄거리에서 회차를 가리키는 out-of-band 메타 태그(단일 지점 — 라벨 형식의 SSOT).
    B-34 소스차단: 출판 인덱스 'N화:'는 그 자체로 한국어 산문에 그대로 낄 수 있는 라벨이라, 모델이 지문에 옮겨 적기 쉬웠다
    (실측 — 서리꽃 7cba74b80209 22화 지문이 컨텍스트의 'N화:' 라벨을 '14화 …'로 복사, 실제 14화에 없는 장면까지 날조 콜백).
    '회차 번호 쓰지 마라' 부정명령(pink-elephant)이나 사후 스크러버(두더지잡기) 대신, 라벨 형식 자체를
    서술자가 본문에 옮겨 적지 않는 각주형 항목 마커('[#N]')로 바꿔 소스에서 끊는다 — '화' 산문 토큰만 제거하고
    순서·작가 가시화(번호 대응)는 보존."""
    return f"[#{n}]"


def _chapter_recap(c, near: bool = False) -> str:
    """회차를 story_so_far/최근요약에 넣을 요지 문자열 — degraded 회차의 detail 을 신뢰 강등(P-2 ②).
    정상 회차: detail_synopsis(near=True) 또는 summary → 없으면 본문 슬라이스 폴백(요약 구멍 방지, 기존 동작).
    degraded 회차: 요약 LLM 이 실패해 detail 이 beat 합성 요지(또는 최후 프로즈 슬라이스)다. 프로즈 슬라이스 재유입을
      막으려 detail_synopsis→summary 만 신뢰하고, 둘 다 비면 c.text 프로즈로 되돌아가지 않는다(재상연 소스 차단)."""
    detail = getattr(c, "detail_synopsis", "") or ""
    summary = getattr(c, "summary", "") or ""
    if getattr(c, "summary_degraded", False):
        return detail or summary   # 프로즈 슬라이스 폴백 금지(신뢰 강등)
    slice_n = 300 if near else 120
    return detail or summary or (c.text[:slice_n] if getattr(c, "text", "") else "")


def _recent_summaries(prior) -> list[str]:
    """비트 설계용 최근 회차 요약(과거=한줄, 직전=상세+말미) — 설계 단계 컨텍스트 기아 방지.
    회차 식별은 out-of-band 메타 태그(_recap_tag)로 — 출판 인덱스 'N화'를 설계 콜에 흘리지 않는다(B-34, 프롬프트 누출 방지)."""
    out = [f"{_recap_tag(c.chapter)} {c.title}: {_chapter_recap(c)}" for c in prior[-4:-1]]
    if prior:
        pv = prior[-1]
        out.append(f"{_recap_tag(pv.chapter)}(직전) 상세: {_chapter_recap(pv, near=True)}")
        if pv.text:
            out.append(f"직전 회차 말미(이번 회차 도입이 이 흐름을 자연스럽게 이어받도록 — 연결 방식은 작품 톤이 정한다): …{pv.text[-280:]}")
    return out


def _build_story_so_far(chapters, budget: int) -> tuple[str, int]:
    """누적 줄거리 요약 — FINALIZED 회차만, 최신부터 예산 내로 채운 뒤 시간순 제시. 반환=(text, 드롭된 회차수).
    요약이 비어도(요약 실패) 본문 앞부분 fallback → FINALIZED 회차가 줄거리에서 통째 누락(영구망각)되지 않게."""
    lines = [f"{_recap_tag(c.chapter)} {_chapter_recap(c)}"
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


def _plants_for_menu(spine, policy: str) -> list[str]:
    """XR-2(cross-review/005 §2.2): 이벤트 메뉴 생성기로 가는 미회수 복선 데이터 게이트.
    plant_reminder OFF 는 문자열(plant_notes)만이 아니라 데이터 인자(outstanding→메뉴 due·seed)도 끈다 —
    'off=주입 안 함' 주석 계약에 코드를 일치시키는 수리. 가시화 경보(plant_backlog emit)는 별도 채널로 유지."""
    return [] if policy == "off" else _outstanding_plants(spine)


def _arc_anchors(spine, arc, ep) -> list[RetrievedItem]:
    """아크/에피소드 방향을 narrative 앵커로(서사 의도 — ground_truth 아님).

    FS-1: spine.ending(결말·중심질문) 앵커는 소스 제거 — 본문 생성기가 매화 최종 반전을 보면 플랜에 없는
    떡밥을 자유 발명한다(DP-5 4화 실측: 조연 입 단정·복선 명시 호명 등 5건 살포. pink-elephant/자기앵커 계보).
    반전의 회차 유입 통로는 에피소드 plants 슬롯만(아크→에피소드 분해 단계는 종전대로 엔딩을 본다)."""
    items: list[RetrievedItem] = []
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
            seg += f": {prof}"   # PF-3: 220 매직 컷 제거 — profile 은 공개·현재만 담게 정리됨(반전·궤적은 arc_note). 전량 주입(설계 계층·유계)
        meta = []
        if ent is not None:
            st = ontology.state_as_of(eid, "status", chapter)
            if st and st != "alive":
                meta.append(f"현재상태={st}")
            # CX-2: 노출 등급 단일 질의점(public_attrs) 경유 — internal 계측 축 비노출 + 영문 내부 키 대신
            #   vocab 라벨로(내부 키 원문 유출 실측 수리). [:8] 상한은 기존 계약 유지(I-4).
            for a, v, _b in ontology.public_attrs(eid, chapter)[:8]:
                if a == "status":
                    continue
                meta.append(f"{ontology.vocab.label(a)}={v}")
        rels = rel_by.get(name) or []
        if rels:
            meta.append(", ".join(rels[:4]))   # rel 라벨이 이미 '관계:..' 자기기술 형태 → 접두어 생략
        if meta:
            seg += " [" + " / ".join(meta) + "]"
        # PF-2: 아크 노트(설계 전용 궤적)를 설계 계층에만 얹는다 — 이 함수 산출은 arc_planner(에피소드 분해·이벤트
        #   메뉴·비트)로만 흐르고 회차 생성 프롬프트엔 도달하지 않는다(프로즈 소비처는 데뷔 앵커=profile 뿐).
        #   빈 값이면 무주입(바이트 동일·하위호환). 플래너가 '앞으로 어디로 가는가'를 보고 인물에서 사건을 도출하게.
        arc = ((spec.arc_note if spec else "") or "").strip()
        if arc:
            seg += f"\n  (궤적·설계 참고: {arc})"
        lines.append(seg)
    return "\n".join(lines)


@promptlog.stage("episode_rollup")
def _rollup_episode(provider, episode, chapters) -> str:
    """완료 에피소드의 회차 요약들을 1~2문장 에피소드 요약으로 압축(계층 story_so_far 재료)."""
    parts = [f"{_recap_tag(c.chapter)} {c.summary or c.text}" for c in chapters]   # B-34: 'N화:' 대신 메타 태그 → 압축 요약이 회차 라벨을 되받아 story_so_far 로 재유입하지 않게. 절단 전면 제거(2026-08-21): 요약 결측 폴백도 전문(압축은 아래 LLM 콜 소관)
    try:
        out = provider.chat(
            [{"role": "system", "content": "여러 회차를 1~2문장 에피소드 요약으로 압축. 핵심 사건·결과·미결만. 군더더기 금지."},
             {"role": "user", "content": f"[에피소드]{episode.title}\n" + "\n".join(parts) + "\n에피소드 요약:"}],
            temperature=0.2).strip()
        return out or (episode.climax or episode.title)
    except Exception:
        return episode.climax or episode.title


def _build_story_so_far_hier(state, next_ch: int, budget: int,
                             exclude_last_detail: bool = False,
                             with_contributors: bool = False) -> tuple:
    """계층 누적 줄거리(spine 모드) — 예산을 '최근 회차 상세'로 채우고(최신 우선), 예산 밖 먼 에피소드만 1줄 롤업으로 압축.
    (I-1 교정) 예전 버전은 현재 에피소드 회차에만 상세를 썼다 → 에피소드 경계 직후엔 현재 회차가 0이라 롤업 1줄로 붕괴(예산 12k인데 1문단).
    이제 경계 무관 직전 완료 에피소드 상세까지 끌어와 예산을 채운다 → '경계 기아' 해소. 먼 과거는 롤업으로 압축(토큰 선형)."""
    spine = state.world.spine
    cur_ep = state.narrative_progress.current_episode_id
    prior = sorted([c for c in state.chapters if c.chapter < next_ch and c.status == ChapterStatus.FINALIZED],
                   key=lambda c: c.chapter)
    # 1) 최근 회차 상세로 예산 채움(최신 우선) — 직전 완료 에피소드까지 끌어와 경계 직후에도 예산 활용
    kept, covered, used = [], set(), 0
    last_ch_no = prior[-1].chapter if prior else -1
    for c in reversed(prior):
        if exclude_last_detail and c.chapter == last_ch_no:
            continue   # CX-5: 직전 1화 상세 제외 — prev_chapter 원문 전문이 전담(이중 주입 해소), 예산은 그 앞 회차로
        line = f"{_recap_tag(c.chapter)} {_chapter_recap(c)}"
        if kept and used + len(line) + 1 > budget:
            break
        kept.append(c); covered.add(c.episode_id); used += len(line) + 1
    detail = [f"{_recap_tag(c.chapter)} {_chapter_recap(c)}"
              for c in sorted(kept, key=lambda c: c.chapter)]
    # 2) 상세에 안 든(예산 밖) 완료 에피소드는 1줄 롤업으로 — 엔딩 backward 인과의 먼 시작점 보존(부분 포함 에피소드는 롤업 생략=중복 방지)
    rollups = [f"[{arc.title}·{ep.title}] {ep.summary}"
               for arc in sorted(spine.arcs, key=lambda a: a.order)
               for ep in arc.episodes
               if ep.done and ep.summary and ep.episode_id != cur_ep and ep.episode_id not in covered]
    # XR-3: with_contributors=True 면 (text, dropped, contributors) — contributors = 상세로 실린 회차 번호와
    #   롤업으로 실린 에피소드 id. 조립 텍스트는 한 글자도 달라지지 않고(위 두 목록 그대로) 기본 False 는
    #   기존 2-튜플 반환 그대로다(호출부·기존 테스트 무변경).
    contrib = {"detail_chapters": [c.chapter for c in sorted(kept, key=lambda c: c.chapter)],
               "rollup_episodes": [ep.episode_id
                                   for arc in sorted(spine.arcs, key=lambda a: a.order)
                                   for ep in arc.episodes
                                   if ep.done and ep.summary and ep.episode_id != cur_ep
                                   and ep.episode_id not in covered],
               "excluded_last_detail": bool(exclude_last_detail)}
    if not rollups and not detail:
        return ("", 0, contrib) if with_contributors else ("", 0)
    text, dropped = "\n".join(rollups + detail), len(prior) - len(kept)   # dropped = 상세에서 빠져 롤업/생략된 회차 수
    return (text, dropped, contrib) if with_contributors else (text, dropped)


def _slug(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")   # 내부 id(엔티티/설정항목) — ascii
    if not re.search(r"[a-z0-9]", base):
        base = "node"
    sid, i = base, 2
    while sid in existing:
        sid, i = f"{base}_{i}", i + 1
    return sid


# FI-3 ⓑ: preference_pairs.jsonl append 직렬화(모듈 뮤텍스 — _TRACE_WRITE_LOCK 선례).
#   공유 단일 파일이라 pid 락 비대상·이 락 안에서 다른 락을 잡지 않는다(데드락 불가).
_PREF_PAIR_LOCK = threading.Lock()


class CopilotService:
    def __init__(self, settings: Settings, repo: ProjectRepository, registry=None):
        self.settings = settings
        self.repo = repo
        # 앱-전역 스킬 라이브러리(작품 간 공유 카탈로그). 미주입 시(테스트·직접 생성) 기본 구현 지연 생성.
        if registry is None:
            from ..repository import FilesystemSkillRegistry
            registry = FilesystemSkillRegistry(settings.resolved_data_dir())
        self.registry = registry
        self.sessions = SessionManager(settings, repo.get)
        # 회차 생성 잡 — 요청(SSE) 수명과 생성 수명 분리: 연결이 끊겨도 백그라운드로 끝까지 진행,
        # 새로고침/재접속 시 진행 중 잡에 다시 붙는다(멱등 시작 → 중복 회차 방지).
        self.gen_jobs = GenerationJobManager(getattr(settings, "gen_job_retain_sec", 1800))
        self._wg_provider: LLMProvider | None = None
        self._esc: dict = {}          # (pid,chapter)→연속 ESCALATED 횟수(무한 갇힘 경보용, 휘발)
        self._drafts: dict = {}       # 생성 전 컨셉 드래프트(휘발 — finalize 시 ProjectState 로 승격)
        self._finalizing: set = set() # finalize 진행 중인 draft id(EventSource 재연결 중복 생성 차단)
        self._draft_lock = threading.Lock()
        self._revise_drafts: dict = {}     # 퇴고 후보 캐시: revision_id → draft dict(휘발, TTL — accept 시 소비)
        self._revise_lock = threading.Lock()
        self._revise_ttl: float = 1800     # 30분(만료 후보 정리)
        self._image_client = None          # CV-1: 이미지 thin client 지연 생성(표지 생성 시에만 — API 키/HTTP 재사용)
        self._cover_pids: set = set()      # CV-1: 표지 생성 진행 중 pid(중복 생성 락 — 423)
        self._cover_lock = threading.Lock()   # _cover_pids 보호(짧은 임계구역만 — LLM/이미지 콜은 락 밖)
        self._product_gate_active: set = set()   # PR-1: 게이트 regen 폴백이 generate_next_chapter 를 재진입할 때
        #   중첩 게이트를 막는 재진입 가드(pid 단위) — 게이트 안의 회차 재생성은 게이트를 다시 돌지 않는다(무한 재귀 차단)

    def _resolve_locked_session(self, sess, state):
        """XR-32: 안정 프로젝트 락을 잡은 뒤 현재 세대를 재해석해 writer lease를 완결한다.

        테스트의 경량 fake manager는 resolve_locked 가 없으므로 락 안에서 get_or_create 를 다시 호출한다.
        프로덕션 SessionManager에서는 get_or_create→lock 사이 축출로 생긴 stale handle을 여기서 제거한다.
        """
        resolve = getattr(self.sessions, "resolve_locked", None)
        return resolve(state) if resolve is not None else self.sessions.get_or_create(state)

    @property
    def wg_provider(self) -> LLMProvider:
        # B-22b: worldgen·bible(창의·구조) — A/B 1위 claude(gpt-5.2-chat 최약). worldgen_model 라우팅.
        if self._wg_provider is None:
            self._wg_provider = create_role_provider(self.settings, self.settings.worldgen_model)
        return self._wg_provider

    @property
    def planning_provider(self) -> LLMProvider:
        # B-22b: 아크/에피/비트 설계(추론) — A/B 1위 gpt-5.2(추론). planning_model 라우팅(worldgen과 분리).
        if getattr(self, "_planning_provider", None) is None:
            self._planning_provider = create_role_provider(self.settings, self.settings.planning_model)
        return self._planning_provider

    @property
    def image_client(self):
        # CV-1: 표지 이미지 thin client(OpenAI Images) — chat 스택 무접촉·지연 생성(키 없는 env 도 표지 미사용이면 무영향).
        if self._image_client is None:
            from ..llm.image_client import ImageClient
            self._image_client = ImageClient()
        return self._image_client

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
                + " — 이 키워드가 가리키는 관습을 작품의 톤·전제에 맞게 세계·인물·전개로 구현하라. "
                "장기 회수가 필요한 키워드라면 그 단서를 초반에 심어 두되, 정산 시점은 이 작품의 페이싱이 정하게 하라"
                "(매 회차로 소진하지 말고 자산으로 운용).")
        hint = next((f"{c.name}: {c.want}".strip(" :") for c in brief.characters
                     if c.role and ("주인공" in c.role or "주연" in c.role)), "")
        return ProjectSeed(
            title=brief.title, genre=brief.genre or "현대 판타지", tone=brief.tone,
            premise="\n".join(parts) or brief.logline or brief.title,   # 절단 전면 제거(2026-08-21): 컨셉 산출 전문
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
            world_skill_ids = list((params or {}).get("world_skills") or [])   # 라이브러리에서 고른 세계관 스킬(잠금 아님 — 별도 전달)
            d.locks = self._merge_locks(d.locks, params)
            self._apply_locks(d.brief, d.locks)
            state, delta = self.create_project(self._brief_to_seed(d.brief), bus=bus, brief=d.brief,
                                               world_skill_ids=world_skill_ids)
            self._drafts.pop(did, None)
            return state, delta
        finally:
            self._finalizing.discard(did)

    # ---- 프로젝트 ----
    def create_project(self, seed: ProjectSeed, bus=None, brief=None, world_skill_ids=None) -> tuple[ProjectState, dict]:
        # bus: 선택적 EventBus(SSE). brief: 선택적 ConceptBrief — 첫 설계(build_spine)에 대화 핵심을 충실 주입(컨텍스트 보강).
        # world_skill_ids: 작가가 라이브러리에서 *이 세계 생성에* 적용하기로 고른 worldgen 스킬 id(참조형 — 생성 시점 해소·주입).
        def _emit(ev, **kw):
            if bus is not None:
                bus.emit("worldgen", ev, **kw)
        seed.target_chapters = max(1, min(200, int(seed.target_chapters or 12)))   # 방어 클램프(API 외 직접호출 보호)
        world_skill_ids = list(world_skill_ids or [])
        _wskills = [s.model_copy(update={"enabled": True}) for s in self.registry.resolve(world_skill_ids)]
        _weff = apply_skills(_wskills, "worldgen")     # 상한(SKILL_COMPOSE_CAP) 적용된 *실제 반영분*
        _wg_inject = _weff.prompt_inject               # 어댑터로 주입된 세계관 스킬(결·접근)
        _applied_wids = [s.id for s in _wskills if s.point == "worldgen"][:SKILL_COMPOSE_CAP]   # 실제 적용된 id만(상한 초과분 제외)
        if _wg_inject:
            _emit("world_skills", applied=_weff.applied)   # 캡 적용된 이름만 보고(초과분을 '적용됨'으로 거짓보고 금지)
        before = self.wg_provider.usage.as_dict()
        _emit("world_start")
        _gen = WorldGenerator(self.wg_provider)
        # 풍부함(검증됨, A/B ON 5:0): worldgen 전 '집착 벡터'를 먼저 추출해 세계를 균등 슬롯이 아니라 하나의 집착에서 편중 파생.
        _obs = _gen.obsession(seed, skills_inject=_wg_inject) if getattr(self.settings, "world_obsession", True) else {}
        if _obs.get("obsession_vector"):
            _emit("obsession", vector=_obs["obsession_vector"], lens=_obs.get("sensory_lens", []))
        world = _gen.generate(seed, obs=(_obs or None), skills_inject=_wg_inject, brief=brief)   # 브리프 명명 인물 바인딩(이름 발명 방지)
        world.obsession_vector = _obs.get("obsession_vector", "")
        if world.time_anchors:   # B-36: 전제에서 파생된 시간 앵커(계약 만기·나이) 가시화(advisory — 무강제)
            _emit("time_anchors", n=len(world.time_anchors),
                  labels=[a.label for a in world.time_anchors][:6])
        if getattr(self.settings, "world_weird", True):   # 풍부함③(A/B 검증 +2.6): 진부한 디폴트를 집착에 맞게 구체·비자명하게 비틈
            world = _gen.weird(world, _obs or None, skills_inject=_wg_inject)
            _emit("world_weird")
        if not seed.title:
            seed.title = world.title
        _emit("world_done", title=world.title,
              entities=[e.name for e in world.entities if e.etype == "character"])
        # R4: 엔딩-주도 아크/에피소드 spine 설계(실패 시 None=평면 모드 폴백)
        _emit("spine_start")
        try:
            world.spine = ArcPlanner(self.planning_provider).build_spine(   # B-22b: spine 설계=planning_model
                world, seed.target_chapters, brief=brief, bus=bus)   # G8: bus 로 spine 미완 가시화 · B-26: 내부 1회 retry
            if world.spine and world.spine.arcs:
                _emit("spine_done", arcs=len(world.spine.arcs),
                      ending_ok=bool(world.spine.ending and (world.spine.ending.ending or "").strip()))
            else:   # B-26: retry 후에도 빈 spine(아크 0=평면 모드) — 폴백은 유지, '완성 0개' 위장 대신 가시 경고
                _emit("spine_skip", reason="empty_after_retry")
        except Exception:
            world.spine = None
            _emit("spine_skip", reason="error")
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
                             created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                             injected_skills=_applied_wids,   # 생성에 *실제 반영된* worldgen 스킬만 기록(상한 초과분 제외 — 정직한 동결)
                             skills_migrated=True)              # 신규 작품은 인라인 레거시 없음 → 이관 불필요
        sess = self.sessions.get_or_create(state)
        sess.snapshot_into(state)            # 시드 위키 등 초기 메모리 영속
        # ST-12a: 서술자 음성 카드 파생(작품 생성 완료 시 1콜, wg_provider — worldgen 라우팅과 동일) — EC-1 과 동일
        #   계층·패턴(실패는 조용히 스킵+이벤트·생성 차단 금지·무강제). 1인칭(style.pov=='first')일 때만 — 3인칭은
        #   이번 티켓 범위 밖(무동작). 주인공 = ontology.entities 삽입순 첫 actor(harness._pov_entity_id 동형 규칙 —
        #   narrator_voice.find_protagonist_id 로 단일 구현). 파생 카드는 주인공 entity.voice(기존 빈 필드)에 영속하되
        #   이미 voice 가 비어있지 않으면 덮어쓰지 않는다(작가 수정 존중). config narrator_voice=False 면 스킵(구작·A/B off).
        if getattr(self.settings, "narrator_voice", True) and getattr(world.style, "pov", "") == "first":
            _emit("voice_card_start")
            try:
                from ..worldgen.narrator_voice import derive_voice_card, find_protagonist_id
                _pid = find_protagonist_id(sess.bundle.ontology)
                _ent = next((e for e in world.entities if e.id == _pid), None) if _pid else None
                if _ent is None:
                    _emit("voice_card_skip", reason="no_protagonist")
                elif (getattr(_ent, "voice", "") or "").strip():
                    _emit("voice_card_skip", reason="voice_present")   # 작가 수정 존중 — 덮어쓰지 않음
                else:
                    _card = derive_voice_card(self.wg_provider, world, _pid)
                    if _card:
                        _ent.voice = _card                              # 영속(state.world — repo.save 로 디스크)
                        _live = sess.bundle.ontology.entities.get(_pid)
                        if _live is not None:                           # 라이브 세션 ontology 도 즉시 반영(EC-1 동형)
                            _live.voice = _card
                        _emit("voice_card_done", entity=_pid, chars=len(_card))
                    else:
                        _emit("voice_card_skip", reason="empty_derivation")
            except Exception as e:            # derive_voice_card 는 자체 격리 — 이중 방어(어떤 경우에도 생성 계속)
                _emit("voice_card_skip", reason=type(e).__name__)
        # EC-1: 엔딩 술어계약 컴파일(작품 생성 완료 시 1콜, planning_provider) — 실패는 조용히 스킵+이벤트(생성 차단 금지)
        if world.spine and world.spine.ending:
            _emit("contract_start")
            try:
                from ..engine.ending_contract import compile_contract
                state.ending_contract = compile_contract(self.planning_provider, state,
                                                         sess.bundle.ontology, source="worldgen")
                if state.ending_contract.error:
                    _emit("contract_skip", reason=state.ending_contract.error[:120])
                else:
                    _emit("contract_done", predicates=len(state.ending_contract.predicates),
                          unexpressed=len(state.ending_contract.unexpressed))
            except Exception as e:            # compile_contract 는 자체 격리 — 이중 방어(어떤 경우에도 생성 계속)
                _emit("contract_skip", reason=type(e).__name__)
        delta = _usage_delta(before, self.wg_provider.usage.as_dict())
        state.usage_total = _accumulate(state.usage_total, delta)
        self.repo.save(state)
        _emit("done", pid=state.id, title=world.title)
        return state, delta

    def get_project(self, pid: str) -> ProjectState | None:
        return self.repo.get(pid)

    # ---- 조회 분할(화면 로딩) — 회차 레코드는 회차당 ~90KB(퇴고 이력·생성 컨텍스트·판정 원문이 대부분)라
    #   프로젝트 GET 한 번에 전 회차가 실리면 목록 한 줄 그리자고 수 MB 를 받는다. 목록·현황 화면이 쓰는 축만
    #   남긴 요약본을 view 로 잘라 보내고, 무거운 축은 회차 단건 조회로 미룬다.
    #   요약은 '제외 목록'으로 정의한다 — 새 필드가 늘어도 자동으로 요약에 포함돼 화면이 조용히 비지 않게(누락 방지).
    #   기본값(view="full")은 기존 응답과 바이트 동일 — 구 클라이언트·도구 무회귀.
    CHAPTER_HEAVY_FIELDS = ("text", "detail_synopsis", "revisions", "gen_context", "dialogue_ledger",
                            "verification", "ending_contract_eval", "ontology_changes", "humanize",
                            "redpen", "claim_audit", "recovery_hints")

    @staticmethod
    def chapter_view(ch, view: str = "full") -> dict:
        """회차 레코드 투영. full=전체 / text=요약+본문(뷰어·내보내기) / summary=본문 없는 메타(목록·현황)."""
        d = ch.model_dump()
        # EP-PUB: 발행 파생 상태(저장 안 함)를 모든 뷰에 동봉 — 목록·뷰어·작업실이 재조회 없이 배지를 그린다.
        #   published_* 필드는 경량이라 heavy 제외 목록에 없어 summary/text 뷰에도 그대로 실린다.
        d["publish_state"] = publish_state(ch)
        if view == "full":
            return d
        text = d.get("text") or ""
        light = {k: v for k, v in d.items() if k not in CopilotService.CHAPTER_HEAVY_FIELDS}
        light["chars"] = len(text)                        # 본문 없이도 분량 표기·유무 판정이 되게(목록·뷰어 목차)
        light["has_text"] = bool(text.strip())
        light["n_revisions"] = len(d.get("revisions") or [])
        light["lite"] = True                              # 요약본 표식 — 화면이 단건 조회로 채워야 할 레코드
        if view == "text":
            light["text"] = text
        return light

    def chapters_page(self, pid: str, offset: int = 0, limit: int = 20,
                      view: str = "summary") -> dict | None:
        """회차 목록 페이지 — 회차 번호 오름차순 window. 없는 작품=None(라우트가 404)."""
        state = self.repo.get(pid)
        if not state:
            return None
        chs = sorted(state.chapters, key=lambda c: c.chapter)
        offset = max(0, int(offset))
        limit = max(1, min(200, int(limit)))
        window = chs[offset:offset + limit]
        return {"total": len(chs), "offset": offset, "limit": limit,
                "has_more": offset + len(window) < len(chs),
                "items": [self.chapter_view(c, view) for c in window]}

    def list_projects(self) -> list[dict]:
        return self.repo.list_summaries()

    def _pid_artifact_sidecars(self, pid: str) -> list:
        """FI-3(031 §1.3): repo `projects/` 밖에서 같은 pid 로 보존되는 저장 root 전수 — 삭제 registry.
        artifact 전수는 한 구현체의 메서드 목록이 아니라 **전 저장 root** 에서 수집한다(031 규율 —
        repo.delete 만 검수한 030 의 누락 소스). 새 pid별 사이드카 root 를 만들면 여기 등록이 의무."""
        return [self._regen_backup_path(pid), self._regen_snapshot_path(pid)]

    def _pid_artifact_dirs(self, pid: str) -> list:
        """삭제 registry 의 디렉토리 항목(033 §2 — 삭제 계약 결정 반영): 프롬프트 로그 pid 폴더.
        요청·응답 전문이 담기는 프로젝트 귀속 관측물이라 삭제에 편입한다. 저장소별 계약 전수는
        docs/storage-ledger.md 원장이 정본(preference_pairs 행은 계약상 보존 — 작품 횡단 학습 자산)."""
        from ..llm import promptlog as _pl
        return [_pl._LOG_ROOT / pid]      # 모듈 속성 지연 참조 — 테스트의 _LOG_ROOT 패치가 그대로 적용

    def delete_project(self, pid: str) -> bool:
        """XR-34(cross-review/025)+FI-3(031)+삭제 계약(033): 삭제 트랜잭션 — 매니저 primitive 로 직렬화.

        구 경로(evict+repo.delete 조합)는 삭제 성공 응답 뒤 진행 중 writer 의 save 가 프로젝트를
        부활시켰다(025 결정론 재현 — '발급된 참조는 무효화되지 않는다'가 상태 스냅샷에도 적용).
        현행: 진행 중 writer 는 ProjectBusyError(라우트 423 즉시 거부 계약), 성공 시 양층 tombstone
        (매니저=세션 발급 금지 / repo=save sink 거부)이 부활·유령 세션을 구조적으로 차단한다.

        삭제 계약(033 §2 결정·정본=docs/storage-ledger.md): **운영 상태 + 등록 artifact 전수 삭제** —
        repo 파일(본체·RAG·trace·표지) + 재생성 백업·스냅샷 + 프롬프트 로그 pid 폴더를 지운 뒤에만
        반환한다. preference_pairs 행은 계약상 보존(공유 학습 자산 — 원장에 명시). 반환 계약: **pid
        artifact 가 하나라도 삭제됐는가**(레포 primitive 도 삭제 계수 기반 — 사이드카-only 고아 정리도
        True). 중간 I/O 실패는 fail-closed(tombstone 유지·예외 전파·재삭제로 잔여 정리)."""
        def _cleanup() -> bool:
            import shutil
            removed = self.repo.delete(pid)
            for p in self._pid_artifact_sidecars(pid):
                if p.exists():
                    p.unlink()
                    removed = True
            for d in self._pid_artifact_dirs(pid):
                if d.is_dir():
                    shutil.rmtree(d)      # 실패는 전파(fail-closed — tombstone 유지·재삭제로 정리)
                    removed = True
            return removed
        return self.sessions.delete_project(pid, _cleanup)

    def add_directive(self, pid: str, text: str) -> AuthorDirective | None:
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                return None
            sess = self._resolve_locked_session(sess, state)
            d = AuthorDirective(directive_id=f"d{len(state.directives) + 1}", text=text,
                                from_chapter=state.current_chapter + 1)
            state.directives.append(d)
            self.repo.save(state)
            # FI-1: 캐논 정정 표면(연재 지시 추가) — chapter=0 작품 스코프 샤드·approx_chapter 스냅샷(회차 밖 이벤트).
            #   커밋 직후·sess.lock 보유 → §5 성공 경로(sess.lock→trace lock 순서).
            emit_intent(self.repo, self.settings, pid, 0, "directive_add",
                        payload={"approx_chapter": state.current_chapter, "directive_id": d.directive_id,
                                 "text": trim_text(text or ""), "from_chapter": d.from_chapter})
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
        # VP-3(2026-08-18 실측 다발 — 수술 배치 반려 6건·재실현 채택 2회 무산, 매회 *다른* 키 요동):
        #   클레임 추출은 LLM 이라 borderline 판단이 흔들린다. 차단은 '편집이 사실을 바꿨다'가 입증될 때만.
        claim_flaps: list = []
        if changed:
            # 1단(결정론·LLM 0): 편집 변경 영역에 표기가 한 번도 등장하지 않는 엔티티의 클레임 변화는
            #   이번 편집의 결과일 수 없다(추출 요동) — 강등. 전면 재작성이면 변경 영역이 본문 대부분이라
            #   자연히 강등 0(별도 분기 불요). 표기를 모르는 엔티티는 보수적으로 잔류.
            #   diff 는 라인(문단) 단위 — 문자 단위는 조각이 잘게 나와 편집된 문단의 인명이 영역에서
            #   빠지고, 전면 재작성에서도 이름 문자열이 우연히 equal 매칭돼 새는 것이 테스트 실측.
            import difflib
            b_lines = before_text.splitlines()
            a_lines = after_text.splitlines()
            sm = difflib.SequenceMatcher(None, b_lines, a_lines, autojunk=False)
            zones = []
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != "equal":
                    zones += b_lines[i1:i2] + a_lines[j1:j2]
            edit_zone = "\n".join(z for z in zones if z)

            def _in_zone(eid):
                e = ont.entities.get(eid)
                toks = ([getattr(e, "name", "")] + list(getattr(e, "aliases", None) or [])) if e is not None else []
                toks = [t.strip() for t in toks if t and len(t.strip()) >= 2]
                return (not toks) or any(t in edit_zone for t in toks)
            claim_flaps += [c for c in changed if not _in_zone(c[0])]
            changed = [c for c in changed if _in_zone(c[0])]
        if changed:
            # 2단(재측정 다수결·+2콜): 남은 변화는 before/after 를 각 1회 재추출해 **양쪽 다 재현될 때만**
            #   차단. 요동은 비재현이 정의다 — 반려 후 전량 재발사(수십만 토큰 실측)보다 재측정 2콜이 싸다.
            #   재측정 실패(예외)는 원판정 유지(보수 — 없는 측정으로 통과시키지 않는다).
            try:
                b2 = self._norm_claim_map(checker.check_text(before_text, ont, chapter, ids).claims, vocab)
                a2 = self._norm_claim_map(checker.check_text(after_text, ont, chapter, ids).claims, vocab)
                kept = []
                for (e, k, bv, av) in changed:
                    if b2.get((e, k)) == bv and a2.get((e, k)) == av:
                        kept.append((e, k, bv, av))
                    else:
                        claim_flaps.append((e, k, bv, av))
                changed = kept
            except Exception:
                pass
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
            # VP-3: 요동 강등분 정직 표면화(은폐 금지) — 차단하지 않되 무엇이 흔들렸는지 남긴다
            "claim_flaps": [{"entity": nm(e), "key": k, "before": bv, "after": av}
                            for (e, k, bv, av) in claim_flaps[:8]],
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
        _t0 = time.monotonic()   # FI-1: revise_propose 지연시간(초) 계측 baseline
        if not directive:
            # FI-1 마찰: 빈 지시(작가가 지시 없이 눌렀나) — 라우트 400 전 원장에 기록(어디에도 안 남던 마찰).
            emit_intent(self.repo, self.settings, pid, chapter_no, "friction_empty_directive",
                        payload={"directive": ""})
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
            # FI-1 마찰: 잠김(423) — 무엇을 하려다 막혔나(directive 원문). sess.lock 미보유(acquire 실패) → 트레이스 락만.
            emit_intent(self.repo, self.settings, pid, chapter_no, "friction_locked",
                        payload={"directive": trim_text(directive)}, gen_no=getattr(ch, "gen_no", 1))
            return None
        try:
            state = self.repo.get(pid)    # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            # TOCTOU 재검증(락 안) — 락 밖 481행 status 검사는 stale 스냅샷.
            # 두 읽기 사이 다른 스레드가 채택/삭제로 본문·상태를 바꿨으면 before_text 가 잘못된 버전으로 캐시됨.
            if ch.status not in (ChapterStatus.FINALIZED, ChapterStatus.ESCALATED):
                raise ValueError("대상 회차가 아닙니다")
            before_text = ch.text
            _gen_no = getattr(ch, "gen_no", 1)   # FI-1: 이 회차 세대 스냅샷(락 안 — 신뢰 값. 이벤트가 폐기 세대에 오조인되지 않게)
            ont = sess.bundle.ontology
            checker = sess.bundle.checker
            generator = sess.bundle.generator
            # span 정규화 검증(있으면 정확히 1회 매칭)
            if span_text:
                normalized = re.sub(r"\s+", " ", span_text).strip()
                from ..engine.harness import ChapterGenerator as _CG
                if _CG._find_span(before_text, normalized) is None:
                    # FI-1 마찰: 구간 못 찾음(작가가 지목한 구간이 본문에 없다). sess.lock 보유 중 emit → sess.lock→trace lock 순서(허용).
                    emit_intent(self.repo, self.settings, pid, chapter_no, "friction_span_not_found",
                                payload={"directive": trim_text(directive), "span_text": trim_text(span_text),
                                         "reason": "span_not_found"}, gen_no=_gen_no)
                    raise ValueError("span_not_found")
            # involved_ids 고정(D2) — before_text 전체 스캔 1회, before·after 동일 주입
            ids = sorted(set(ont.scan_present_ids(before_text)))
        finally:
            sess.lock.release()           # ── LLM 콜 전에 락 해제(이슈1): 생성 스레드 블로킹 방지
        # ── (2) LLM 콜(락 없음) — before check_text(1콜) + revise_prose(1콜) + 가드레일 after check_text(1콜).
        #     sess.lock 을 보유하지 않으므로 동시에 'generate_next_chapter' 가 진행 가능.
        try:
            before_res = checker.check_text(before_text, ont, chapter_no, ids)   # before 1회 계산(캐시)
            after_text = generator.revise_prose(
                directive, before_text, span_text, passes, ids, ont, chapter_no,
                skills_inject=apply_skills(self._effective_skills(state), "revise").prompt_inject)   # 라이브러리에서 주입된 퇴고 스킬
            guardrail, _after_res = self._guardrail(before_text, after_text, before_res,
                                                    ids, ont, checker, chapter_no)
        except Exception as _e:
            # FI-1 마찰: 퇴고 도중 오류(revise_prose/가드 계산 실패 등) — 사유 트림 기록 후 원래대로 전파(순수 부가·무해).
            emit_intent(self.repo, self.settings, pid, chapter_no, "friction_error",
                        payload={"directive": trim_text(directive),
                                 "reason": f"{type(_e).__name__}: {str(_e)[:200]}"}, gen_no=_gen_no)
            raise
        # ── (2.5) 무변경 감지(이슈: revise_prose 가 LLM 실패·길이가드·빈 살균 시 before_text 그대로 폴백).
        #     after==before 면 가드레일은 ratio=1.0 으로 통과하나 '성공한 퇴고'가 아니다.
        #     후보를 캐시하지 않고 changed:false 로 명시 반환 → 프론트가 '효과 없음'을 작가에게 고지(채택 무의미).
        if after_text == before_text:
            sess.bus.emit("revise", "no_change")   # CopilotService 엔 bus 없음 — 세션 bus 사용(락 밖 로컬 sess 유효)
            # RV-1②: 무변경 원인 정직화 — revise_prose 가 사이드채널에 남긴 원인 코드를 그대로 싣는다.
            #   폴백(길이가드=출력 절단·과확장)·빈 살균·실제 무변경(after==before)을 프론트가 구분해
            #   '지시를 더 구체적으로' 오귀속 대신 원인별 정직 문안을 표시. 값 domain 은 harness 사이드채널과 동일.
            #   getattr 폴백(mock generator 는 이 속성이 없을 수 있음): 원인 미상 → "unchanged"(안전 기본).
            cause = getattr(generator, "_last_revise_cause", "unchanged")
            return {
                "revision_id": None, "before_text": before_text,
                "after_text": after_text, "span_text": span_text,
                "changed": False, "no_change_cause": cause,
                "guardrail": {k: guardrail[k] for k in
                              ("passed", "G_A_passed", "G_B_passed", "length_ok",
                               "new_hard", "claim_changes", "claim_flaps", "new_keys_advisory", "reason")},
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
        # FI-1: revise_propose — 기각될 수 있는 후보(지시 원문+기계 산출)를 원장에 보존(채택률 분모이자 '기계가 뭘
        #   못했나'의 유일 사본). after 전문 통저장 대신 변경 코어만(§2.1). 정상 반환 = sess.lock 미보유 → 트레이스 락만.
        emit_intent(self.repo, self.settings, pid, chapter_no, "revise_propose",
                    payload={"directive": trim_text(directive), "span_text": trim_text(span_text),
                             "passes": passes, "guardrail_passed": guardrail["passed"],
                             "guardrail_reason": guardrail["reason"],
                             "core": revise_core_diff(before_text, after_text),
                             "latency_sec": round(time.monotonic() - _t0, 1)},
                    ref={"revision_id": revision_id}, gen_no=_gen_no)
        return {
            "revision_id": revision_id, "before_text": before_text,
            "after_text": after_text, "span_text": span_text,
            "changed": True,
            "guardrail": {k: guardrail[k] for k in
                          ("passed", "G_A_passed", "G_B_passed", "length_ok",
                           "new_hard", "claim_changes", "claim_flaps", "new_keys_advisory", "reason")},
            "passes_used": passes,
        }

    def accept_revision(self, pid: str, chapter_no: int, revision_id: str,
                        after_text_fb: str | None = None, span_text_fb: str | None = None,
                        passes_fb: list[str] | None = None,
                        directive_fb: str = "(폴백)") -> dict | None:
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
        # ⓓ(DP-21): 캐시 만료 폴백(draft 미스) 시 revision_id 가 None/빈값이면 새 id 생성.
        #   ChapterRevision.revision_id 는 str(default_factory) — 명시 None 은 default 를 우회해
        #   pydantic ValidationError(실측 크래시). 폴백은 클라이언트/러너가 정식 id 없이 after_text 만
        #   들고 오는 경로이므로 여기서 서버가 고유 id 를 부여해 이력 정합을 유지한다(캐시 히트 경로는
        #   기존 id 신뢰 — 더블-accept 방어 불변). draft 존재 시엔 pop 키(=원 id)를 그대로 쓴다.
        if draft is None and not revision_id:
            revision_id = uuid.uuid4().hex[:12]
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
            sess = self._resolve_locked_session(sess, state)
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
            # 폴백 경로 record.directive — DE-1: 기본값 "(폴백)"(하위호환·기존 호출 바이트 동등).
            #   직접 편집(edit_chapter)은 directive_fb="[직접 편집]" 을 넘겨 이력에 편집 출처를 정직 기록.
            directive = draft["directive"] if draft else directive_fb
            span_text = draft["span_text"] if draft else (span_text_fb or "")
            passes_used = draft["passes"] if draft else [p for p in (passes_fb or [])
                                                         if p in ("reformat", "fix_tense")]
            # RV-2①: 재요약 폴백을 생성 경로와 동형으로 — 이 회차 gen_context 의 beat 계획을 락 안에서 스냅샷.
            beat_snapshot = _persisted_beat(ch)
            _gen_no = getattr(ch, "gen_no", 1)   # FI-1: 이 회차 세대 스냅샷(락 안 — 마찰·채택 이벤트 gen_no 동봉용)
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
            # FI-1 마찰: 가드레일 재검증 실패(사실 충돌·이름/수치 변경·분량 이탈로 채택 기각). sess.lock 미보유 → 트레이스 락만.
            emit_intent(self.repo, self.settings, pid, chapter_no, "friction_guardrail",
                        payload={"directive": trim_text(directive), "reason": trim_text(guardrail["reason"]),
                                 # VP-3 잔여(PM 검수): 어느 엔티티·키가 막았고 무엇이 요동으로 강등됐는지 —
                                 #   사유 문자열만으로는 재현 검증·누적 가시화 불가하던 구멍.
                                 "claim_changes": guardrail.get("claim_changes") or [],
                                 "claim_flaps": guardrail.get("claim_flaps") or []},
                        gen_no=_gen_no)
            raise ValueError(f"가드레일 재검증 실패: {guardrail['reason']}")
        # 요약 재생성(1콜) — RV-2①: 생성 경로(harness.py:910 _summarize(text, story_so_far, beat))와 동형으로 beat 전달.
        #   요약 LLM 실패 시 폴백이 '본문 서두 슬라이스'(P-2 프로즈 재유입)가 아니라 beat.summary+key_events 합성이 되도록.
        #   beat 부재(구 레코드·gen_context 미주입)면 None → 종전대로 최후 프로즈 슬라이스 폴백(degraded 플래그 기록).
        new_summary, new_detail, _rev_degraded = sess.bundle.generator._summarize(after_text, "", beat_snapshot)
        # ── (3) 최종 기록 — sess.lock 재진입. 더블-accept·본문 변경 재검(락 밖 콜 사이 경쟁 차단).
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            if any(r.revision_id == revision_id for r in ch.revisions):
                raise ValueError("이미 채택된 퇴고입니다")    # (1)~(3) 사이 동시 accept 가 먼저 기록
            if ch.text != current_text_at_snapshot:    # 락 밖 LLM 콜 사이 본문 교체됨 → before 캐시 무효(이슈4)
                raise ValueError("본문이 생성 사이 변경됨")
            # RV-2②: 본문 교체 전(옛 프로즈 파생물 상태 기준)에 stale 대상을 산정한다. 이 회차에 실제로 존재하는
            #   LLM 콜 파생물(위키 인물카드·약속원장 정산·연속성 점검·독자예측)만 골라 표식(자동 재콜 없음 — 비용·무강제).
            #   기존에 남아 있던 stale(직전 미해소분)과 합집합(퇴고 반복 시 누적 유지). undo 복원용으로 직전 스냅샷 사용.
            authoritative_prior_stale = dict(getattr(ch, "derivatives_revised_stale", {}) or {})
            new_stale = self._stale_derivatives_for_accept(state, ch)
            merged_stale = {**authoritative_prior_stale, **new_stale}
            # 본문 교체 + 이력 레코드 push(append-only) + 요약 반영 + RAG 재색인
            ch.revisions.append(ChapterRevision(
                revision_id=revision_id, directive=directive, span_text=span_text,
                before_text=before_text, after_text=after_text,
                before_summary=ch.summary, before_detail_synopsis=ch.detail_synopsis,
                before_summary_degraded=ch.summary_degraded,   # P-2: undo 복원용 스냅샷(퇴고 전 신뢰강등 상태)
                before_derivatives_revised_stale=authoritative_prior_stale,   # RV-2②: undo 대칭 복원용(퇴고 전 stale 표식)
                passes_used=passes_used,
                violations_before=list(before_res.hard), violations_after=list(after_res.hard),
                claim_changes=guardrail["claim_changes"],
                claim_flaps=guardrail.get("claim_flaps") or [],   # VP-3: 요동 강등분 영속(정직 기록)
                guardrail_passed=True,
                guardrail_reason=guardrail["reason"],
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
            ch.text = after_text
            ch.summary, ch.detail_synopsis = new_summary, new_detail   # 락 밖 재생성분 반영
            ch.summary_degraded = bool(_rev_degraded)   # P-2: 재요약 실패 시 신뢰 강등 플래그 갱신(성공 시 False 로 복구)
            ch.ai_tell = self._recompute_ai_tell(state, sess, after_text)   # 본문 교체 → 문체 신호 재계산(stale 추세 차단)
            ch.derivatives_revised_stale = merged_stale   # RV-2②: LLM 콜 파생물 stale 표식(재계산 아님 — UI 배지·무강제)
            sess.bundle.rag.index_chapter(chapter_no, after_text)   # RAG 재색인(멱등)
            sess.snapshot_into(state)
            self.repo.save(state)
            # FI-1: 채택 성공(전문은 ChapterRevision SSOT — 여기선 참조만). edit- 접두 revision_id 는 작가 직접
            #   편집 위임(edit_chapter → accept_revision)이므로 surface=direct_edit 로 구분한다(1액션=1이벤트 —
            #   위임처 edit_chapter 는 재차 emit 하지 않는다·설계 §9①). 최종 기록 블록(sess.lock 보유) → §5 성공 경로.
            _surface = "direct_edit" if str(revision_id).startswith("edit-") else "revise_accept"
            emit_intent(self.repo, self.settings, pid, chapter_no, _surface,
                        ref={"revision_id": revision_id}, gen_no=getattr(ch, "gen_no", 1))
        # 캐시는 (0)에서 이미 소비됨(누수 창 제거).
        return {"accepted": True, "chapter": ch.model_dump(), "revision_count": len(ch.revisions)}

    def edit_chapter(self, pid: str, chapter_no: int, new_text: str | None = None,
                     span_text: str = "", replacement: str | None = None,
                     edits: list[dict] | None = None) -> dict | None:
        """DE-1: 작가 직접 편집 — 작가가 문장을 직접 타이핑해 회차 본문을 고치는 정식 경로.

        [왜 정식 승격인가] 지금까지 본문 수정 경로는 퇴고(AI revise)→채택→undo 뿐이었고, 작가가
        문장을 직접 고쳐 넣는 정식 기능이 없었다(사용자 요구·승인 완료). 그리고 accept_revision 의
        폴백 경로(after_text_fb)가 사실상 '작가가 만든 after_text 를 그대로 채택'하는 기계를 이미
        갖고 있음이 라이브로 실증됐다 — 가드 재검증 → 재요약 → 파생물 stale 표식 → ai_tell 재계산 →
        RAG 재색인 → 리비전 기록 → undo 가 전부 그 경로 한 번에 돈다. 이 메서드는 그 폴백을 정식
        계약으로 승격만 한다: 기계는 재구현하지 않고 accept_revision 에 위임한다(중복 구현 금지).

        [무강제 정합] 편집을 여기서 강제 확정하지 않는다. accept_revision 의 서버 가드레일이 채택을
        결정하고(사실 충돌이면 그 안에서 ValueError), undo 로 언제든 되돌릴 수 있다.

        [의도된 계약 — 최종화 스택 미실행] 재실현(rerender)·휴머나이즈·조판 같은 기계 최종화 패스는
        타지 않는다. 이는 누락이 아니라 의도다 — 작가가 직접 친 문장을 기계가 다시 손대지 않고 그대로
        발행한다(작가 문장 그대로). 문체 다듬기가 필요하면 작가가 퇴고/재실현을 따로 호출한다.

        모드(셋 중 하나만):
          (a) 전체 교체 — new_text 지정(span_text/replacement/edits 는 비운다).
          (b) 구간 교체 — span_text(현재 본문에 정확히 1회 등장) + replacement 지정.
          (c) 다중 구간 교체(DE-2) — edits=[{span_text, replacement}, ...]. 좌 원본/우 편집 에디터가
              바뀐 대목(hunk)들을 구간 교체 목록으로 모아 한 요청으로 보내면, 서버가 전부 검증(각 구간
              span 1회 등장·무변경 아님·구간 간 미겹침)한 뒤 원자 적용한다 — 다른 모드 필드와는 배타.
        둘 이상 모드를 함께 주면 ValueError. 구간이 0회/2회+ 등장하거나 무변경(after==before)이면
        ValueError(정직 사유; (c)는 어느 구간인지 1-기반 번호를 접두로 붙인다).
        423(회차 생성 중 락)은 accept_revision 이 반환하는 None 을 그대로 전파한다.

        [(c) 원자성·오른쪽부터] 다중 구간은 모든 구간을 먼저 검증하고(하나라도 실패면 전무 — 전부 아니면
        전무), 통과분만 위치 내림차순(오른쪽부터) 교체해 적용한다. 왼쪽부터 바꾸면 앞 교체가 뒤 구간의
        시작 오프셋을 밀어 인덱스가 어긋나므로, 오른쪽 끝부터 적용해 아직 손대지 않은 왼쪽 구간의
        오프셋을 불변으로 유지한다.

        [겹침 포함 등장 계수] 유일성 판정은 str.count(겹침 배제 — 일치 후 길이만큼 전진)가 아니라 겹침
        포함(+1 전진)으로 센다. "X\\nX\\nX" 에서 "X\\nX" 는 count 로 1회지만 실제 위치는 2곳(0·중간) —
        첫 위치 적용이 작가 의도(둘째 위치)와 어긋날 수 있다(DE-2 클라 퍼즈 실측). 클라 hunk 추출과 동일 규칙.
        (계수기는 모듈 레벨 _occ_capped 로 승격 — RP-1 빨간펜 앵커 유일성 판정과 소스 단일화.)"""
        if edits is not None:
            # ── 모드 (c) DE-2: 다중 구간 교체. 배타 — edits 지정 시 다른 모드 필드는 전부 비어 있어야
            #    한다(기존 (a)↔(b) 배타 검사와 같은 톤).
            if new_text is not None or span_text or replacement is not None:
                raise ValueError("구간 목록(edits)과 다른 편집 모드를 함께 지정할 수 없습니다")
            if not edits:
                raise ValueError("구간 목록(edits)이 비어 있습니다")
            state = self.repo.get(pid)
            if not state:
                raise KeyError(pid)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            before_text = ch.text
            # (1) 원소별 검증 — 각 구간의 위치를 확정한다. 사유에 1-기반 구간 번호를 접두로 붙여
            #     어느 구간이 문제인지 정직히 알린다((b) 단일 구간 메시지 문구를 그대로 재사용).
            #     하나라도 실패하면 이 아래 적용에 도달하지 않는다(원자성: 전부 아니면 전무).
            spans = []   # (start, end, replacement, num)
            for i, e in enumerate(edits, start=1):
                s = e["span_text"]
                r = e["replacement"]
                if not s:
                    raise ValueError(f"구간 {i}: 다듬을 구간(span_text)이 비어 있습니다")
                occ = _occ_capped(before_text, s)
                if occ == 0:
                    raise ValueError(f"구간 {i}: 본문에 없는 구간입니다 — 원문에서 그대로 붙여넣어 주세요")
                if occ > 1:
                    raise ValueError(f"구간 {i}: 구간이 여러 곳과 일치합니다 — 더 길게 지정해 주세요")
                if s == r:
                    raise ValueError(f"구간 {i}: 변경 없음 — 구간이 그대로입니다")
                start = before_text.index(s)   # occ==1 보장 → 유일 위치
                spans.append((start, start + len(s), r, i))
            # (2) 겹침 검사 — 시작 오프셋 정렬 후 이웃쌍의 구간이 겹치면(다음 시작 < 이전 끝) 거절하고
            #     어느 구간끼리 겹치는지 번호를 명시한다. 동일 span 중복 원소도 같은 시작 오프셋으로
            #     여기 자연히 걸린다(각자 occ==1 은 통과하지만 서로 겹침).
            spans.sort(key=lambda x: x[0])
            for (s0, e0, _r0, n0), (s1, _e1, _r1, n1) in zip(spans, spans[1:]):
                if s1 < e0:
                    a, b = sorted((n0, n1))
                    raise ValueError(f"구간 {a}과(와) 구간 {b}이(가) 서로 겹칩니다 — 겹치지 않게 지정해 주세요")
            # (3) 원자 적용 — 위치 내림차순(오른쪽부터) 교체. 아직 손대지 않은 왼쪽 구간의 오프셋을
            #     불변으로 유지하려면 오른쪽 끝부터 바꿔야 한다(왼쪽부터면 앞 교체가 뒤 인덱스를 밈).
            after_text = before_text
            for start, end, r, _num in sorted(spans, key=lambda x: x[0], reverse=True):
                after_text = after_text[:start] + r + after_text[end:]
            if after_text == before_text:   # 최종 가드(기존 것 유지) — 각 구간이 변경이므로 사실상 도달 X
                raise ValueError("변경 없음 — 본문이 그대로입니다")
            # 폴백 채택 기계 재사용(중복 구현 금지) — 단일 원소면 그 span 을, 다중이면 곳 수를 이력에 정직 기록.
            return self.accept_revision(
                pid, chapter_no, revision_id=f"edit-{uuid.uuid4().hex[:12]}",
                after_text_fb=after_text,
                span_text_fb=(edits[0]["span_text"] if len(edits) == 1 else ""),
                directive_fb=("[직접 편집]" if len(edits) == 1 else f"[직접 편집] 구간 {len(edits)}곳"))
        if new_text is not None and (span_text or replacement is not None):
            raise ValueError("전체 교체(new_text)와 구간 교체(span_text)를 함께 지정할 수 없습니다")
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        before_text = ch.text
        if new_text is not None:
            after_text = new_text
            span_out = ""
        else:
            if replacement is None:
                raise ValueError("교체문(replacement)을 지정해 주세요")
            if not span_text:
                raise ValueError("다듬을 구간(span_text) 또는 전체 본문(new_text)을 지정해 주세요")
            occurrences = _occ_capped(before_text, span_text)   # 겹침 포함(위 docstring) — (b)도 동일 규칙
            if occurrences == 0:
                raise ValueError("본문에 없는 구간입니다 — 원문에서 그대로 붙여넣어 주세요")
            if occurrences > 1:
                raise ValueError("구간이 여러 곳과 일치합니다 — 더 길게 지정해 주세요")
            after_text = before_text.replace(span_text, replacement, 1)   # 정확히 1회(위에서 보장)
            span_out = span_text
        if after_text == before_text:
            raise ValueError("변경 없음 — 본문이 그대로입니다")
        # 폴백 채택 기계 재사용(중복 구현 금지): edit- 접두 revision_id·직접 편집 directive 로 위임.
        #   가드 재검증·재요약·stale·ai_tell·RAG 재색인·이력·undo 전부 accept_revision 이 처리.
        return self.accept_revision(
            pid, chapter_no, revision_id=f"edit-{uuid.uuid4().hex[:12]}",
            after_text_fb=after_text, span_text_fb=span_out, directive_fb="[직접 편집]")

    # ───────────────────────── RP-1 '빨간펜' — 표시(무강제·본문 불변)+방향 제안(작가 발동 1콜) ─────────────────────────
    def add_redpen_mark(self, pid: str, chapter_no: int, anchor_text: str,
                        span_start: int, span_len: int, note: str = "") -> dict | None:
        """RP-1: 작가가 읽다가 '마음에 안 드는' 구간을 표시(빨간펜)+이유 메모를 append.

        [무강제·본문 불변] 마크는 본문 바이트를 절대 만지지 않는다. 그래서 여기엔 가드 재검증·재요약·ai_tell
        재계산·RAG 재색인·파생물 stale 표식이 전부 없다 — 본문이 바뀌지 않으므로 어떤 파생물도 stale 이 되지
        않는다(edit_chapter/accept 와의 결정적 차이). LLM 콜 0(수집·표시 경로는 LLM 0; 방향 제안만 별도 발동).

        [앵커 유일성] anchor_text 는 현재 본문에서 겹침 포함 계수(_occ_capped)로 정확히 1회여야 한다 —
        직접 편집(edit_chapter)과 같은 계수기를 공유(소스 단일화). 0회/2회+면 정직 사유로 거절한다.

        검증 순서(423 보다 400 우선 — 잘못된 요청은 생성 중이어도 사유를 돌려준다): anchor 비었음/span_len<=0/
        오프셋 범위 밖/앵커 0회·2회+ 를 락 진입 전에 판정. 통과 후 sess.lock non-blocking 실패 → None(423)."""
        anchor_text = anchor_text or ""
        if not anchor_text:
            raise ValueError("표시할 앵커(anchor_text)가 비어 있습니다")
        if span_len <= 0:
            raise ValueError("표시 구간 길이(span_len)가 0 이하입니다")
        if span_start < 0 or span_start + span_len > len(anchor_text):
            raise ValueError("표시 구간이 앵커 범위를 벗어났습니다")
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        occ = _occ_capped(ch.text, anchor_text)
        if occ == 0:
            raise ValueError("본문에 없는 구간입니다 — 원문에서 그대로 붙여넣어 주세요")
        if occ > 1:
            raise ValueError("앵커가 여러 곳과 일치합니다 — 더 길게 지정해 주세요")
        sess = self.sessions.get_or_create(state)
        if not sess.lock.acquire(blocking=False):   # 회차 생성/타 편집 중(lost-update 방지) → 라우트 423
            return None
        try:
            state = self.repo.get(pid)              # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            mark = RedpenMark(anchor_text=anchor_text, span_start=span_start, span_len=span_len,
                              note=(note or ""), created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            ch.redpen.append(mark)
            sess.snapshot_into(state)
            self.repo.save(state)
            return {"mark": mark.model_dump(), "count": len(ch.redpen)}
        finally:
            sess.lock.release()

    def delete_redpen_mark(self, pid: str, chapter_no: int, mark_id: str) -> dict | None:
        """RP-1: 빨간펜 마크 1건 삭제(본문 불변). 미존재 mark_id 는 KeyError(라우트 404).

        락 관행은 add 와 동일(sess.lock non-blocking → None=423). 마크 존재 확인·제거는 락 안에서(권위 재읽기
        기준) 하므로 생성 중이면 마크 유무와 무관하게 먼저 423 을 돌려준다(락 경합 우선)."""
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        sess = self.sessions.get_or_create(state)
        if not sess.lock.acquire(blocking=False):
            return None
        try:
            state = self.repo.get(pid)              # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            idx = next((i for i, m in enumerate(ch.redpen) if m.mark_id == mark_id), None)
            if idx is None:
                raise KeyError(mark_id)
            ch.redpen.pop(idx)
            sess.snapshot_into(state)
            self.repo.save(state)
            return {"deleted": True, "count": len(ch.redpen)}
        finally:
            sess.lock.release()

    # ---- EP-PUB: 외부 플랫폼 수동 발행 원장(툴은 업로드하지 않음 — 발행 사실+지문만 기록) ----
    def mark_published(self, pid: str, chapter_no: int, note: str | None = None) -> dict | None:
        """EP-PUB: 이 회차를 '외부 플랫폼에 발행함'으로 표시. 이미 발행돼 있으면 재발행 — 지문·시각을 현재 본문으로
        갱신한다(수정본을 다시 올린 뒤 누름). 본문 바이트 불변·LLM 0콜.

        [무업로드] 툴은 어디에도 올리지 않는다 — 작가가 플랫폼에 올린 사실과 그 순간 본문 지문만 남긴다.
        빈 본문은 발행 불가(400). note 는 선택 — None 이면 기존 메모 유지(재발행 시 편의), 문자열이면 덮어씀(""=지움).
        락 관행은 redpen 과 동일(sess.lock non-blocking 실패 → None=423, 생성/타 편집 중 lost-update 방지)."""
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        if not (ch.text or "").strip():
            raise ValueError("본문이 없어 발행할 수 없습니다")
        sess = self.sessions.get_or_create(state)
        if not sess.lock.acquire(blocking=False):
            return None
        try:
            state = self.repo.get(pid)              # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            first = not (ch.published_at or "").strip()
            ch.published_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            ch.published_fingerprint = text_fingerprint(ch.text)
            if note is not None:
                ch.published_note = note.strip()
            sess.snapshot_into(state)
            self.repo.save(state)
            return {"chapter": chapter_no, "published_at": ch.published_at,
                    "published_fingerprint": ch.published_fingerprint,
                    "published_note": ch.published_note,
                    "publish_state": publish_state(ch), "first_publish": first}
        finally:
            sess.lock.release()

    def unmark_published(self, pid: str, chapter_no: int) -> dict | None:
        """EP-PUB: 발행 표시 해제(미발행으로 되돌림 — 발행 원장 3필드 초기화). 본문 불변·LLM 0.
        락 관행은 mark_published 와 동일(sess.lock non-blocking 실패 → None=423)."""
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        sess = self.sessions.get_or_create(state)
        if not sess.lock.acquire(blocking=False):
            return None
        try:
            state = self.repo.get(pid)              # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            ch.published_at = ""
            ch.published_fingerprint = ""
            ch.published_note = ""
            sess.snapshot_into(state)
            self.repo.save(state)
            return {"chapter": chapter_no, "publish_state": publish_state(ch)}
        finally:
            sess.lock.release()

    @promptlog.stage("redpen_suggest")
    def suggest_redpen_directions(self, pid: str, chapter_no: int,
                                  mark_ids: list[str] | None = None) -> dict | None:
        """RP-1: 옵트인 방향 제안 — 작가가 표시해 둔 빨간펜 구간마다 수정 '방향'을 2~3개 배치 제안(LLM 1콜).

        [방향만·대체 문장 금지] 출력은 '접근을 서술하는 한 문장'(방향)뿐이며 대체 본문을 직접 쓰지 않는다.
        본문은 여기서도 절대 바뀌지 않는다 — 제안은 마크의 suggestions 에만 저장되는 advisory 이고, 실제
        수정은 작가가 그 방향을 보고 edit/revise 로 스스로 발동한다(무강제).

        [파생 유효 판정] 대상 마크 중 현재 본문에서 앵커가 유일(_occ_capped==1)한 것만 유효다. stale(0회/2회+)
        마크는 제외한다 — stale 은 저장 상태가 아니라 이 순간 본문 대비 파생 판정이다(RedpenMark docstring ②).

        [락 규율 — accept_revision 계보] sess.lock 은 (1) 대상 스냅샷 (2) 최종 기록 두 구간만 진입하고,
        LLM 콜은 그 사이 락 밖에서 돈다(생성 스레드 블로킹 방지). (2)에서 락 밖 콜 사이 마크가 삭제됐으면 조용히 건너뛴다.

        [폴백 조작 0] 응답 형식 위반(미지 mark_id·directions 비배열/빈배열/비문자열/개당 200자 초과)이나 예외 시
        ValueError 를 올리고 어떤 마크의 suggestions 도 건드리지 않는다 — 저장은 전 검증 통과 후 (2) 한 곳뿐(빈 메뉴 저장 금지).

        [컨텍스트 경계] redpen 을 읽는 곳은 이 메서드와 직렬화뿐이다 — 생성/퇴고/재실현 컨텍스트로 유입시키지 않는다(앵커링 가드)."""
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        sess = self.sessions.get_or_create(state)
        # ── (1) 대상 스냅샷 — sess.lock 안에서 유효 마크와 LLM 입력 재료(±300자 문맥)만 확정(빠름). LLM 콜은 락 밖.
        if not sess.lock.acquire(blocking=False):   # 생성 중 → 423
            return None
        try:
            state = self.repo.get(pid)              # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            text = ch.text
            by_id = {m.mark_id: m for m in ch.redpen}
            if mark_ids is not None:
                targets = []
                for mid in mark_ids:
                    if mid not in by_id:
                        raise KeyError(mid)             # 지정 마크 중 하나라도 미존재 → 404
                    targets.append(by_id[mid])
            else:
                targets = list(ch.redpen)
            # 파생 유효 판정: 현재 본문에서 앵커 유일 탐색 성공한 마크만(stale 제외).
            valid = [m for m in targets if _occ_capped(text, m.anchor_text) == 1]
            if not valid:
                raise ValueError("방향을 제안할 유효한 빨간펜이 없습니다")
            payload = []
            for m in valid:
                start = text.find(m.anchor_text)       # occ==1 보장 → 유일 위치
                segment = m.anchor_text[m.span_start:m.span_start + m.span_len]
                lo = max(0, start - 300)
                hi = min(len(text), start + len(m.anchor_text) + 300)
                payload.append({"mark_id": m.mark_id, "segment": segment,
                                "note": m.note, "context": text[lo:hi]})
            valid_ids = {m.mark_id for m in valid}
        finally:
            sess.lock.release()                        # ── LLM 콜 전에 락 해제(생성 스레드 블로킹 방지)
        # ── (2) LLM 콜(락 없음) — 배치 1콜. provider 는 세션 생성 provider(rerender/menu 경로와 동일 획득).
        #     프롬프트는 중립(장르·트로프 호명 0·예시 나열 0 — 프롬프트 편향 교훈): 방향만, 대체 문장 금지.
        provider = sess.provider
        u0 = provider.usage.as_dict()
        system = (
            "너는 작가의 원고 검토 보조다. 작가가 직접 표시한 각 구간에 대해, 그 구간을 어떻게 고칠지 "
            "수정 '방향'을 2~3개 제안한다. 각 방향은 '접근을 서술하는 한 문장'이며, 대체할 문장을 직접 써 주지 "
            "않는다(대체 문장 작성 금지 — 방향만). 작가가 남긴 이유 메모가 있으면 그 의도를 최우선으로 존중한다. "
            "여러 구간에서 공통된 문제 패턴이 보이면 cross 에 그것을 한 문장으로 요약한다(없으면 빈 문자열). "
            "출력은 다음 JSON 객체로만: "
            '{"marks":[{"mark_id":"...","directions":["...","..."]}],"cross":""}')
        user = json.dumps({"marks": payload}, ensure_ascii=False)
        r = provider.chat_json([{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                               temperature=0.4)
        # ── 응답 형식 검증(정직) — 위반 시 ValueError, suggestions 는 불변(아래 (3) 저장 이전에 중단).
        raw_marks = r.get("marks")
        if not isinstance(raw_marks, list):
            raise ValueError("제안 응답 형식 오류: marks 가 배열이 아닙니다")
        parsed: dict[str, list[str]] = {}
        for item in raw_marks:
            if not isinstance(item, dict):
                raise ValueError("제안 응답 형식 오류: marks 원소가 객체가 아닙니다")
            mid = item.get("mark_id")
            if mid not in valid_ids:
                raise ValueError("제안 응답 형식 오류: 알 수 없는 mark_id")
            dirs = item.get("directions")
            if not isinstance(dirs, list) or not dirs:
                raise ValueError("제안 응답 형식 오류: directions 가 비어 있거나 배열이 아닙니다")
            for d in dirs:
                if not isinstance(d, str):
                    raise ValueError("제안 응답 형식 오류: directions 원소가 문자열이 아닙니다")
                if len(d) > 200:
                    raise ValueError("제안 응답 형식 오류: 방향 한 개가 200자를 초과합니다")
            parsed[mid] = list(dirs)
        cross_raw = r.get("cross", "")
        cross = cross_raw if isinstance(cross_raw, str) else ""
        # ── (3) 최종 기록 — sess.lock 재진입. 유효 마크의 suggestions/suggested_at 만 갱신(락 밖 콜 사이 삭제분은 건너뜀).
        with sess.lock:
            state = self.repo.get(pid)              # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            live = {m.mark_id: m for m in ch.redpen}
            updated = []
            for mid, dirs in parsed.items():
                m = live.get(mid)
                if m is None:                        # 락 밖 콜 사이 삭제됨 → 정직 건너뜀
                    continue
                m.suggestions = list(dirs)
                m.suggested_at = now
                updated.append(m.model_dump())
            sess.snapshot_into(state)
            self.repo.save(state)
        return {"marks": updated, "cross": cross, "usage": _usage_delta(u0, provider.usage.as_dict())}

    def _stale_derivatives_for_accept(self, state, ch) -> dict:
        """RV-2②: 퇴고 accept 로 본문이 바뀌면 '옛 본문 기준' LLM 콜 파생물이 stale 이 된다. 이 함수는 *실제로
        이 회차에 존재하는*(생성 경로가 이 회차용으로 만들어 둔) LLM 콜 파생물만 골라 {name: True} 로 표식한다.

        정직성: 존재하지 않는 파생물(설정 off·spine 없음·이 회차에 findings 0)은 넣지 않는다 — '없는데 stale' 오표식
        금지. 자동 재콜은 하지 않는다(비용·무강제) — 표식만 하고 UI 가 작가에게 재생성/수동검토를 권한다.
          · wiki: ingest 는 present-character 있을 때만 페이지를 만든다(wiki.py). 이 회차가 wiki_pages_touched>0 이면
            그 인물카드가 옛 프로즈로 합성됐으므로 stale.
          · promise_ledger: 이 회차가 약속을 열거나(opened_chapter) 지불했으면(paid_chapter) 그 정산이 옛 프로즈 기준 → stale.
          · claim_audit: 이 회차 record.claim_audit 에 findings 가 있으면 옛 프로즈 대조 결과 → stale.
          · reader_feedback: 이 회차 record.reader_feedback 이 있으면 옛 프로즈 예측 → stale.
          · dialogue_ledger: DG-1 원장이 있으면 옛 본문의 대사·귀속 → stale(재구축은 +1 aux 콜이라 표식만).
        전부 부재면 {} 반환(표식할 stale 없음)."""
        stale: dict = {}
        if getattr(ch, "wiki_pages_touched", 0):
            stale["wiki"] = True
        led = getattr(state, "promise_ledger", None)
        if led is not None and any(
                (p.opened_chapter == ch.chapter) or (p.paid_chapter == ch.chapter)
                for p in (led.promises or [])):
            stale["promise_ledger"] = True
        if getattr(ch, "claim_audit", None):
            stale["claim_audit"] = True
        if getattr(ch, "reader_feedback", None):
            stale["reader_feedback"] = True
        if getattr(ch, "dialogue_ledger", None):
            stale["dialogue_ledger"] = True
        return stale

    @staticmethod
    def _skeleton_char_mismatch(ont, before_text: str, skeleton: str) -> list[str]:
        """SP-3(감사 C1-1): 본문에 등장하는 '인물'(actor) 중 확정 스토리에 표기(정식명·별칭)가 하나도
        없는 이름 목록 — 계획 층 누락·오기의 발사 전 가시화(무강제: 자동 수리 아님·폴백+trace 기록).

        한계(2026-08-18 실측): 오기가 별칭을 품으면('이도윤'⊃별칭 '도윤') 통과한다 — 기계 대조는
        보조이고, 정본 방어선은 발사 전 조립 전문 정독(런북 2-bis 확대)이다. 사물·기관 엔티티는
        확정 스토리에 안 실리는 게 상례라 대조 대상에서 제외(전 화 실측: 노이즈 전부 비인물).
        서술형 라벨('계단에서 죽은 이'·'얼굴 없는 여자')도 제외 — 계획 층이 다른 말('실족사자')로
        지칭하는 게 정상이라 대조가 무의미하고(13화 오탐 실측), 표기 오기 리스크는 인명형에서만 실측됐다."""
        import re as _re
        from ..engine import rerender as _rr
        out = []
        for e in ont.entities.values():
            if not ont.is_actor(getattr(e, "etype", "")):
                continue
            if not _re.fullmatch(r"[가-힣]{2,4}", (getattr(e, "name", "") or "").strip()):
                continue   # 인명형(공백 없는 2~4자)만 — 서술형 라벨은 표기 계약 대상 아님
            if not _rr._entity_in_source(e, before_text):
                continue
            name = (getattr(e, "name", "") or "").strip()
            toks = [t for t in [name] + [(a or "").strip() for a in (getattr(e, "aliases", None) or [])]
                    if len(t) >= 2]
            if toks and not any(t in skeleton for t in toks):
                out.append(name)
        return out

    @promptlog.stage("rerender_gate")
    def _rerender_read_gate(self, before_text: str, after_text: str) -> dict:
        """재실현 채택의 정독 게이트(1차 척도) — cross-vendor 심사 양순서 2콜, '원문 완승'만 기각(no-harm).

        심사 provider = chapter_gate.make_judge(gen≠judge — prose 가 anthropic 계열이면 openai 로).
        라운드별 콜 실패/파싱 불가는 pick_original=None(전건 불가 = 보수 기각 — read_gate_verdict 계약).
        반환: {adopt, reason, rounds, judge, usage}. usage 는 호출부가 usage_delta 에 계상."""
        from ..engine.chapter_gate import make_judge
        from ..engine import rerender as _rr
        provider, spec = make_judge(self.settings, getattr(self.settings, "llm_provider", ""))
        u0 = provider.usage.as_dict()
        rounds = []
        from ..engine.story_pass_prompts import cite_ok
        for orig_first in (True, False):
            x, y = (before_text, after_text) if orig_first else (after_text, before_text)
            pick = None
            why = ""
            cite = True
            try:
                system, user = _rr.build_read_gate_prompt(x, y)
                r = provider.chat_json([{"role": "system", "content": system},
                                        {"role": "user", "content": user}],
                                       temperature=0.0)
                k = (r.get("keep_reading") or "").strip()
                if k in ("ㄱ", "ㄴ"):
                    pick = (k == "ㄱ") if orig_first else (k == "ㄴ")   # True = 그 라운드 원문 승
                elif "무승부" in k:
                    pick = False                                        # 무승부 = 원문 승 아님
                why = r.get("why") or ""   # 절단 전면 제거(2026-08-21): 판정 사유 전문
                # SP-3(감사 M2): 인용 의무 — 두 판본 인용이 각자의 원문과 대조 실패하면 그 라운드는
                #   판정 불가(None). 인용 없는 판정은 무해 판정기가 된다(VJ-1 실측 계보).
                cite = (cite_ok(r.get("ㄱ_인용", ""), x) and cite_ok(r.get("ㄴ_인용", ""), y))
                if pick is not None and not cite:
                    pick = None
                    why = "인용 원문 불일치 — 판정 불가. " + why
            except Exception as e:
                why = f"심사 실패: {type(e).__name__}"
            rounds.append({"orig_first": orig_first, "pick_original": pick, "why": why, "cite_ok": cite})
        verdict = _rr.read_gate_verdict(rounds)
        verdict["rounds"] = rounds
        verdict["judge"] = spec
        verdict["usage"] = _usage_delta(u0, provider.usage.as_dict())
        return verdict

    def _recompute_ai_tell(self, state, sess, text: str) -> dict:
        """본문이 바뀌는 모든 경로(생성·퇴고 accept·undo)에서 ai_tell 을 동일 방식으로 재계산 — stale 추세 소스 차단.
        결정론·LLM 0콜. roster(인명·고유어)는 어휘다양성 오염 방지.

        SP-1 Stage C: 기존 KatFishNet 축(quality_gates.ai_tell_profile) 위에 Kiwi 문말 계측(ending_profile·
        daStreak)을 additive 확장한다 — 판정 라벨 0·advisory. 부품(kiwipiepy·tools) 부재 시 확장 없이 기존
        dict 만 반환(구 경로 바이트 동일·결측 정직). ai_tell_profile 자체가 실패하면 빈 dict(기존 계약)."""
        try:
            from ..engine.quality_gates import ai_tell_profile
            roster = {e.name for e in sess.bundle.ontology.entities.values()}
            roster |= {k for ent in state.bible.entries for k in (ent.keywords or [])}
            profile = ai_tell_profile(text, roster)
        except Exception:
            return {}
        try:
            from ..engine.style_pipeline import kiwi_style_metrics
            kiwi = kiwi_style_metrics(text)
            if kiwi:   # 부품 가용 시에만 확장(부재 시 기존 축만 — advisory 결측 정직)
                profile = {**profile, "kiwi": kiwi}
        except Exception:
            pass
        return profile

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
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            last_rev = next((r for r in reversed(ch.revisions) if not r.reverted), None)
            if last_rev is None:
                raise ValueError("되돌릴 퇴고 이력이 없습니다")
            ch.text = last_rev.before_text
            ch.summary = last_rev.before_summary
            ch.detail_synopsis = last_rev.before_detail_synopsis
            ch.summary_degraded = last_rev.before_summary_degraded   # P-2: 신뢰강등 플래그도 결정론 복원(스냅샷)
            # RV-2②: 파생물 stale 표식도 accept 대칭으로 결정론 복원 — undo 는 본문을 퇴고 전으로 되돌리므로 그때의
            #   위키·원장·점검·예측이 다시 '현재 본문과 정합'인 상태로 돌아간다(퇴고가 부여한 stale 표식 제거).
            #   getattr 폴백 {}: before_derivatives_revised_stale 없는 구 revision 레코드는 빈 dict 로 복원(하위호환).
            ch.derivatives_revised_stale = dict(getattr(last_rev, "before_derivatives_revised_stale", {}) or {})
            # XR-7④: 작가 발동 재계산(recompute_derivative)이 있었다면 스냅샷 복원만으로는 비대칭이 남는다 —
            #   재계산은 '현재(퇴고 후) 본문' 기준으로 파생물을 새로 만들고 stale 표식을 지웠는데, undo 는 그 본문을
            #   버리기 때문이다. 재계산 시점의 활성 리비전 수 N 이 '되돌리기 직전 활성 수'와 같으면 그 파생물은 지금
            #   버리는 본문 기준이므로 다시 stale 로 표식하고 각인을 제거한다(재계산 이력이 없으면 아래 블록 무동작
            #   = 기존 동작 바이트 동일). last_rev.reverted 는 아직 True 가 아니므로 활성 수에 이 리비전이 포함된다.
            _recomputed = dict(getattr(ch, "derivatives_recomputed", {}) or {})
            if _recomputed:
                _active_before = sum(1 for r in ch.revisions if not r.reverted)
                _kept = {}
                for _name, _at_rev in _recomputed.items():
                    if _at_rev == _active_before:
                        ch.derivatives_revised_stale[_name] = True   # 버려지는 본문 기준 파생물 → 재표식
                    else:
                        _kept[_name] = _at_rev                        # 더 앞선 본문 기준 → 복원본과 여전히 정합
                ch.derivatives_recomputed = _kept
            ch.ai_tell = self._recompute_ai_tell(state, sess, last_rev.before_text)   # 복원 본문 → 문체 신호 재계산
            # verification SSOT 도 복원 본문 기준으로 재집계(결정론·LLM 0) — accept/rerender 가 남긴 after 기준
            #   검증 리포트가 undo 후 복원 본문과 어긋나 stale 로 남는 비대칭 차단(ai_tell 재계산과 대칭). 기존
            #   verification 이 비어있던(구 회차) 경우엔 재집계 결과도 채워 넣는다(SSOT 항상 현재 본문 정합).
            if getattr(ch, "verification", None):
                try:
                    from ..engine.verification import build_verification
                    prev_texts = [c.text for c in state.chapters
                                  if c.chapter < chapter_no and c.status == ChapterStatus.FINALIZED]
                    _prior_gate = (ch.verification or {}).get("gate")   # gate 는 재집계 대상 아님 — 실판정 이월(허위 결측 차단)
                    _prior_gate = _prior_gate if isinstance(_prior_gate, dict) else None
                    ch.verification = build_verification(
                        ch, prev_texts=prev_texts,
                        target_chars=getattr(state.world.style, "target_chars_per_chapter", None),
                        gate=_prior_gate,
                        leak_sources=self._leak_sources_for(state, chapter_no),   # VL-1
                        ending_sources=self._ending_sources_for(state, ch))       # XR-1
                except Exception:
                    pass   # 재집계 실패는 undo 를 막지 않는다(SSOT advisory)
            last_rev.reverted = True
            last_rev.reverted_at = time.strftime("%Y-%m-%dT%H:%M:%S")   # FI-1 §4: undo 마킹 시각(서버 — 클라 생성 금지)
            sess.bundle.rag.index_chapter(chapter_no, last_rev.before_text)   # RAG 복원(멱등)
            sess.snapshot_into(state)
            self.repo.save(state)
            # FI-1: 되돌리기(작가가 채택을 물렸다 — 채택률 신호의 짝). 마킹 직후·최종 기록 블록(sess.lock 보유) → §5 성공 경로.
            emit_intent(self.repo, self.settings, pid, chapter_no, "revise_undo",
                        ref={"revision_id": last_rev.revision_id}, gen_no=getattr(ch, "gen_no", 1))
        return {"reverted": True, "chapter": ch.model_dump(), "revision_id": last_rev.revision_id}

    # ---- XR-7③: stale 파생물 재계산(작가 발동 전용 — 자동 호출·자동 LLM 콜 0) ----
    #   RV-2② 의 "비용 때문에 자동 재계산하지 않는다" 결정은 그대로 둔다. 이 진입점은 작가가 버튼을 눌렀을
    #   때만 도는 유일한 재계산 경로이고(승인=클릭), 회차당 0~1콜이며, 기계는 전부 기존 함수를 재사용한다.
    RECOMPUTABLE_DERIVATIVES = ("wiki", "claim_audit", "reader_feedback", "dialogue_ledger")

    @staticmethod
    def _derivative_present(ch, name: str) -> bool:
        """이 회차에 그 파생물이 실제로 있는지 — `_stale_derivatives_for_accept` 의 존재 규칙과 같은 기준.
        없는 것을 '재계산'하면 없던 파생물을 새로 만드는 셈이라 작가 의도 밖이다(400 으로 정직하게 반려)."""
        if name == "wiki":
            return bool(getattr(ch, "wiki_pages_touched", 0))
        return bool(getattr(ch, name, None))

    def _story_so_far_upto(self, state, chapter_no: int) -> str:
        """이 회차 직전까지의 누적 줄거리 — 생성 경로와 같은 빌더를 재사용한다(이중 구현 금지)."""
        budget = self.settings.story_so_far_chars
        spine = getattr(state.world, "spine", None)
        if spine and spine.arcs:
            return _build_story_so_far_hier(state, chapter_no, budget)[0]
        prior = sorted([c for c in state.chapters if c.chapter < chapter_no], key=lambda c: c.chapter)
        return _build_story_so_far(prior, budget)[0]

    def recompute_derivative(self, pid: str, chapter_no: int, name: str) -> dict:
        """퇴고로 stale 이 된 파생물 하나를 현재 본문 기준으로 다시 만든다(작가 발동·회차당 0~1콜).

        지원: wiki(인물카드 재적재) · claim_audit(연속성 점검) · reader_feedback(독자 예측) · dialogue_ledger(대사 원장).
        promise_ledger 는 **미지원**이다 — 원장 재정산은 후행 회차의 지불 이력과 얽혀(ch 에 열린 약속이 ch+3 에서
        지불된 경우의 연쇄) append 원장 정신과 함께 별도 설계가 필요하다. 400 + 사유로 정직하게 돌려보낸다.

        성공 시: 파생물 갱신 · stale 표식에서 그 키 제거 · `derivatives_recomputed[name]=활성 리비전 수`(undo
        대칭 각인 ④) · save · emit. LLM 콜은 락 밖에서 돈다(생성 스레드 블로킹 방지 — accept 경로와 같은 규율)."""
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            if name == "promise_ledger":
                raise ValueError("약속 원장 재정산은 후행 회차의 지불 이력과 얽혀 별도 설계가 필요합니다 — "
                                 "연재 관리의 약속 원장에서 직접 정리해 주세요.")
            if name not in self.RECOMPUTABLE_DERIVATIVES:
                raise ValueError(f"다시 계산할 수 없는 항목입니다: {name}")
            if not (ch.text or "").strip():
                raise ValueError("본문이 비어 있어 다시 계산할 수 없습니다")
            if not self._derivative_present(ch, name):
                raise ValueError("이 회차에는 그 항목이 없어 다시 계산할 대상이 아닙니다")
            text = ch.text
            # ④ 각인용 기준: 지금 본문을 만든 활성(비-reverted) 리비전 수. undo 가 이 수를 되돌리면 재표식된다.
            at_rev = sum(1 for r in ch.revisions if not r.reverted)
            beat = _persisted_beat(ch)
            story_so_far = self._story_so_far_upto(state, chapter_no)
            ont = sess.bundle.ontology
            pov_id = sess.bundle.generator._pov_entity_id(ont)   # DP-8/CX-4 결속(생성 경로와 동일 도출)
            genre = state.world.genre
            _gc = getattr(state.world, "genre_contract", None)
            expectations = _gc.reader_expectations if _gc else None
        # ── LLM 콜(락 밖) — 전부 기존 함수 재사용. 실패는 예외로 올려 라우트가 정직하게 알린다(침묵 성공 금지).
        prov = sess.provider
        if name == "wiki":
            prov = sess.bundle.wiki.provider
        elif name == "dialogue_ledger":
            prov = getattr(sess, "aux_provider", None) or sess.provider
        _u0 = prov.usage.as_dict()
        if name == "wiki":
            # force=True: 멱등 가드(_already) 우회 — 이 회차 로그 정리 후 현재 본문으로 재적재(작가 발동 전용).
            value = sess.bundle.wiki.ingest_chapter(chapter_no, text, ont, reviewed=True,
                                                    pov_entity_id=pov_id, force=True)
        elif name == "claim_audit":
            from ..engine.claim_audit import audit_chapter
            roster = {ont.entities[i].name for i in ont.scan_present_ids(text) if i in ont.entities}
            _q = (f"{(beat or {}).get('summary', '')} "
                  f"{' '.join((beat or {}).get('key_events', []) or [])} {' '.join(sorted(roster))}").strip()
            # as_of=chapter_no-1 은 audit_chapter 내부 규약(rag.search(query, ch_no-1)) — 자기 회차 자동 제외.
            value = audit_chapter(prov, sess.bundle.rag, text, chapter_no, query_hint=_q)
        elif name == "reader_feedback":
            from ..engine.reader_desk import reader_prediction
            value = reader_prediction(prov, text, story_so_far, genre, expectations=expectations,
                                      sofar_budget=getattr(self.settings, "reader_desk_sofar_chars", 12000))
            if not value:
                raise ValueError("독자 예측을 다시 받지 못했습니다(잠시 후 다시 시도해 주세요)")
        else:   # dialogue_ledger
            from ..engine.dialogue_ledger import build_ledger
            # tagged=None: 영속 본문은 이미 디태거를 거친 평문이라 태그 쌍을 복원할 수 없다 → 종전 경로(따옴표
            #   파싱 + 귀속) 폴백. canon 정합은 두 경로 공통으로 resolve_speakers 가 결정론 판정한다.
            value = build_ledger(prov, text, ont, bus=sess.bus, chapter=chapter_no,
                                 pov_entity_id=pov_id, tagged=None)
        _delta = _usage_delta(_u0, prov.usage.as_dict())
        # ── 기록(락 재진입) — 콜 사이 본문이 바뀌었으면 옛 본문 기준 결과를 쓰지 않는다(lost-update 차단).
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            if ch.text != text:
                raise ValueError("본문이 다시 계산하는 사이 변경됐습니다 — 다시 시도해 주세요")
            if name == "wiki":
                ch.wiki_pages_touched = value
            else:
                setattr(ch, name, value)
            ch.derivatives_revised_stale = {k: v for k, v in
                                            (getattr(ch, "derivatives_revised_stale", {}) or {}).items()
                                            if k != name}
            ch.derivatives_recomputed = {**(getattr(ch, "derivatives_recomputed", {}) or {}), name: at_rev}
            state.usage_total = _accumulate(state.usage_total, _delta)   # 정직 총계(작가 발동 콜도 계상)
            sess.snapshot_into(state)
            self.repo.save(state)
            sess.bus.emit("derivatives", "recomputed", chapter=chapter_no, name=name, at_revision=at_rev)
        return {"recomputed": name, "chapter": chapter_no, "at_revision": at_rev,
                "stale": dict(ch.derivatives_revised_stale),
                "derivatives_recomputed": dict(ch.derivatives_recomputed),
                "usage_delta": _delta, "usage_total": state.usage_total}

    def rebuild_wiki(self, pid: str) -> dict:
        """Wiki Projection 전체 재구축 v2 — 작가 발동(자동 호출 금지). XR-10(007 §3) + XR-15/16/17(009 §5~7).

        원칙(원천 vs 파생 — 001 의 Source/Projection 구분을 재구축에 적용):
        · 파생(회차 원고에서 합성된 character 페이지)만 확정 회차 시간순 replay 로 재생성 — 회차별 force
          재적재의 누적 오염 한계 해소(007 실측). 반영 지문은 ingest 가 기록(XR-18 소비 불변식의 원자료).
        · 원천(world.wiki_seeds·비인물 페이지 전량(plot_thread/faction/place/timeline — 진화분 포함)·인물
          페이지 수동 typed_edges)은 candidate 에 보존 이식 + **교체 전 보존 불변식 검사**(XR-15 — v1 은
          seed 5장을 침묵 삭제하는 결함이 있었다·009 실측).
        · 공유 라이브 위키는 replay 동안 1바이트도 변이하지 않는다 — **candidate 빌드 후 락 안 원자 교체**
          (XR-16 — import_pages 로 객체 동일성 유지). 실패는 candidate 폐기로 끝난다(복원 자체가 불필요).
        · 커밋 직전 **입력 재검증**(XR-17): 확정 회차 집합·회차별 본문 지문이 캡처 시점과 다르면 candidate
          폐기 + stale 유지(구버전 Projection 이 '깨끗함'으로 승인되는 lost-update 차단).
        LLM 콜은 락 밖(회차당 1콜 — 작가 발동 비용)."""
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:   # XR-30: 이 락은 매니저 소유 '프로젝트 락'(세션 수명보다 김) — 세대 무관 직렬화
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            # XR-30: 락 획득 사이에 세대가 교체됐을 수 있다(무락 evict 8개 호출부) — 현재 세대로 재해석.
            sess = self._resolve_locked_session(sess, state)
            wiki = sess.bundle.wiki
            # XR-27: 입력 자격은 소비 검증과 '같은 함수'(단일 술어 — 두 정의가 어긋나는 재발 클래스 차단)
            from ..engine.readiness import eligible_wiki_source_chapters
            eligible = eligible_wiki_source_chapters(state)
            fins = [c for c in sorted(state.chapters, key=lambda c: c.chapter)
                    if str(c.chapter) in eligible]
            if not fins:
                raise ValueError("재구축할 확정 회차가 없습니다")
            inputs = [(c.chapter, c.text, eligible[str(c.chapter)]) for c in fins]   # XR-17 캡처(지문 동봉)
            ont = sess.bundle.ontology
            # XR-26(015 §3): replay 가 읽을 명부는 공유 라이브 참조가 아니라 **락 안 독립 스냅샷** — 중간에
            #   바뀌었다 돌아오는 ABA 편집이 있어도 candidate 는 처음부터 끝까지 같은 명부로 합성된다(혼합
            #   Projection 원천 불가). 지속된 변경은 아래 digest 장벽이 커밋 직전에 따로 잡는다(이중 방어 —
            #   digest 동등성은 '무변경 증명'이 아니므로 스냅샷 없이 단독으론 불충분·015 ABA 재현).
            #   Ontology 는 순수 데이터(버스·락·프로바이더 무보유 — deepcopy 안전 실측).
            import copy as _copy
            ont_snap = _copy.deepcopy(ont)
            pov_id = sess.bundle.generator._pov_entity_id(ont)   # 생성 경로와 동일 화자 결속(CX-4·캡처 시점)
            # XR-15 원천 스냅샷: 비인물 페이지 전량(진화분 — seed 이후 몸통·edge 변화 포함) + 인물 수동 edge
            #   (+shell 복원용 메타 — XR-21: 본문 미등장 인물도 정체·edge 는 원천이라 보존).
            preserved = {k: v.model_copy(deep=True) for k, v in wiki.pages.items()
                         if v.page_type != "character"}
            char_shell_src = {k: {"edges": [e.model_copy(deep=True) for e in v.typed_edges],
                                  "as_of": v.as_of_narrative_order, "deadline": v.payoff_deadline}
                              for k, v in wiki.pages.items()
                              if v.page_type == "character" and v.typed_edges}
            # XR-28 target 정책 판별용 — 캡처 시점의 인물 페이지 id 집합(락 밖 라이브 재조회 금지).
            char_page_ids = {k for k, v in wiki.pages.items() if v.page_type == "character"}
            seeds = [s.model_copy(deep=True) for s in (getattr(state.world, "wiki_seeds", None) or [])]
            # XR-22 원천 digest 캡처: 명부·seed·보존 위키·수동 edge·화자 결속 — 커밋 직전 재계산·대조.
            src_digest = _wiki_source_digest(state, ont, pov_id, wiki.pages)
        # ── candidate 빌드(락 밖 — 공유 위키 무변이) ─────────────────────────────
        prov = wiki.provider
        # 정직 잔여(XR-22·013 §2): usage 델타는 공유 provider 창 계측이라 replay 와 겹친 동시 요청의 콜이
        #   섞일 수 있다 — 콜 단위 귀속은 프로바이더가 노출하지 않아 보류(혼입 시나리오 대부분은 회차·원천
        #   장벽이 candidate 폐기로 종결). 재개봉 트리거: usage 오귀속이 비용 보고를 실제로 왜곡한 실측 1건.
        _u0 = prov.usage.as_dict()
        from ..engine.wiki import Wiki as _Wiki
        cand = _Wiki(prov)
        for s in seeds:                       # 원천 재시딩 — 라이브에서 유실된 seed 도 복원
            if s.page_id not in preserved:
                cand.seed_page(WikiPage(page_id=s.page_id, page_type=s.page_type, body=s.body,
                                        payoff_deadline=s.payoff_deadline,
                                        as_of_narrative_order=s.as_of_narrative_order,
                                        provenance=[f"{s.as_of_narrative_order}화"]))
        for k, v in preserved.items():        # 비인물 페이지 이식(진화분 우선 — seed 원문보다 최신)
            cand.pages[k] = v
        done = 0
        shells = 0
        try:
            for ch_no, text, _fp in inputs:   # 파생(character)만 replay 재합성(지문 기록은 ingest 단일 지점)
                cand.ingest_chapter(ch_no, text, ont_snap, reviewed=True, pov_entity_id=pov_id)   # XR-26: 스냅샷만 읽음
                done += 1
            # XR-21(012 §3): 인물 수동 edge 는 '보존', 삭제 아님. 재합성 페이지엔 이식하고, 본문 미등장 인물은
            #   source shell(정체·edge 만 — 옛 합성 본문·지문 미이월·body 빈 값=retrieve 자동 제외·ARCHIVED=
            #   lint 소음 0. 재등장 시 ingest 가 본문을 다시 합성하며 ACTIVE 로 승격 — 자연 병합)로 남긴다.
            for k, meta in char_shell_src.items():
                if k in cand.pages:
                    cand.pages[k].typed_edges = meta["edges"]
                else:
                    cand.pages[k] = WikiPage(page_id=k, page_type="character", body="",
                                             typed_edges=meta["edges"],
                                             as_of_narrative_order=meta["as_of"],
                                             payoff_deadline=meta["deadline"],
                                             lifecycle=WikiLifecycle.ARCHIVED,
                                             provenance=["source_shell"])
                    shells += 1
            # XR-28(015 §5): edge target 은 '보존'하되 '발명'하지 않는다 — 캡처 시점에 실재했던 인물 페이지만
            #   동일 타입 shell 로 복원(비인물 target 은 preserved 로 이미 실재). 캡처 시점에도 없던 target 은
            #   기존 dangling 상태 그대로 보존한다(정체 불명 참조에 임의 character 정본을 만들어 lint 경고만
            #   지우던 동작 제거 — 결측은 lint 가 노출하는 것이 정직). 판별은 캡처 집합(char_page_ids)만 —
            #   락 밖 라이브 위키 재조회 금지(XR-16 무변이 계약).
            for k, meta in char_shell_src.items():
                for e in meta["edges"]:
                    tgt = getattr(e, "target_page_id", "") or ""
                    if tgt and tgt not in cand.pages and tgt in char_page_ids:
                        cand.pages[tgt] = WikiPage(page_id=tgt, page_type="character",
                                                   body="", lifecycle=WikiLifecycle.ARCHIVED,
                                                   provenance=["source_shell"])
                        shells += 1
            # XR-15 보존 불변식(교체 전): seed id·type·deadline / 비인물 페이지 전량 / 수동 edge 소유 페이지·target.
            for s in seeds:
                p = cand.pages.get(s.page_id)
                if p is None or p.page_type != s.page_type or p.payoff_deadline != s.payoff_deadline:
                    raise _RebuildPreservationError(f"원천 seed '{s.page_id}' 손실·변형")
            for k in preserved:
                if k not in cand.pages:
                    raise _RebuildPreservationError(f"비인물 페이지 '{k}' 손실")
            for k, meta in char_shell_src.items():
                p = cand.pages.get(k)
                if p is None or [e.model_dump() for e in p.typed_edges] != [e.model_dump() for e in meta["edges"]]:
                    raise _RebuildPreservationError(f"인물 '{k}' 수동 edge 손실")
        except Exception as e:
            # XR-24(012 §6): candidate 작업 전 구간(replay·이식·불변식)이 단일 실패·비용 계측 경계 —
            #   보존 검사 실패도 이미 쓴 LLM 비용을 정직 계상하고 실패 emit 을 남긴다.
            _delta = _usage_delta(_u0, prov.usage.as_dict())
            with sess.lock:                   # candidate 폐기 — 라이브 위키·표식 무변(복원 불필요)
                state = self.repo.get(pid)
                if state:
                    sess = self._resolve_locked_session(sess, state)
                    state.usage_total = _accumulate(state.usage_total, _delta)
                    self.repo.save(state)
                sess.bus.emit("derivatives", "wiki_rebuild_failed", done=done, total=len(inputs),
                              error=(str(e)[:80] if isinstance(e, _RebuildPreservationError)
                                     else type(e).__name__))
            if isinstance(e, _RebuildPreservationError):
                raise ValueError(f"재구축 보존 검사 실패: {e} — candidate 를 폐기했습니다(기존 위키 무변)") from e
            raise ValueError(f"위키 재구축 실패({done}/{len(inputs)}화 진행) — 기존 위키는 변경되지 않았고 "
                             "생성 제외는 유지됩니다. 잠시 후 다시 시도해 주세요.") from e
        _delta = _usage_delta(_u0, prov.usage.as_dict())
        # ── 커밋(안정 프로젝트 락 안 — XR-17 입력 재검증 + XR-16 원자 교체 + XR-30 세대 재해석) ──
        # sess.lock 은 매니저 소유 프로젝트 락(세션 수명보다 김)이라 replay 중 세대가 교체됐어도 새 세대의
        # 변이와 이 커밋이 직렬화된다. 커밋의 기준·대상은 지역 변수의 옛 세대가 아니라 **현재 매니저 세션**
        # (sess_now) — 018 §4(옛 명부로 digest 계산)·§5(옛 세대에 커밋해 현재 세션이 옛 위키 유지) 봉합.
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess_now = self._resolve_locked_session(sess, state)   # 교체됐다면 현재 세대(같은 프로젝트 락 공유)
            cur = eligible_wiki_source_chapters(state)   # XR-27: 커밋 장벽도 같은 단일 술어
            captured = {str(ch): fp for ch, _t, fp in inputs}
            if cur != captured:               # 집합 변화(신규 확정·해제)든 본문 변화(퇴고·편집)든 전부 폐기
                state.usage_total = _accumulate(state.usage_total, _delta)   # 쓴 비용은 정직 계상
                self.repo.save(state)
                sess_now.bus.emit("derivatives", "wiki_rebuild_stale_input",
                                  chapters=(sorted(int(c) for c in (set(cur) ^ set(captured))) or
                                            sorted(int(c) for c in cur if cur[c] != captured.get(c)))[:8])
                raise ValueError("재구축 중 원고·확정 회차가 변경되어 candidate 를 폐기했습니다"
                                 "(기존 위키·표식 유지) — 다시 실행해 주세요.")
            # XR-22/31: 원천 장벽 — **현재 세대**의 명부·타입 카탈로그·seed·노트 원천·화자 결속으로 재계산.
            #   세대가 교체됐다면 sess_now 는 최신 디스크 재수화분이라 새 세대의 지속 변경(인물 추가 등)이
            #   여기서 잡힌다(018 §4 봉합 — 옛 명부 비교는 교체를 못 봤다).
            cur_pov = sess_now.bundle.generator._pov_entity_id(sess_now.bundle.ontology)
            if _wiki_source_digest(state, sess_now.bundle.ontology, cur_pov,
                                   sess_now.bundle.wiki.pages) != src_digest:
                state.usage_total = _accumulate(state.usage_total, _delta)
                self.repo.save(state)
                sess_now.bus.emit("derivatives", "wiki_rebuild_stale_source")
                raise ValueError("재구축 중 작품 원천(명부·seed·노트 원천)이 변경되어 candidate 를 폐기했습니다"
                                 "(기존 위키·표식 유지) — 다시 실행해 주세요.")
            sess_now.bundle.wiki.import_pages(cand.export_pages(), cand.log)   # 현재 세대에 원자 교체
            cleared = 0
            for c in state.chapters:
                marks = getattr(c, "derivatives_revised_stale", None) or {}
                if marks.get("wiki"):
                    c.derivatives_revised_stale = {k: v for k, v in marks.items() if k != "wiki"}
                    # undo 대칭 각인(recompute ④와 동일 규칙): 되돌리기가 이 수를 깨면 재-stale 된다.
                    c.derivatives_recomputed = {**(getattr(c, "derivatives_recomputed", {}) or {}),
                                                "wiki": sum(1 for r in c.revisions if not r.reverted)}
                    cleared += 1
            state.usage_total = _accumulate(state.usage_total, _delta)
            sess_now.snapshot_into(state)
            self.repo.save(state)
            # XR-30 잔여 창 봉합: 커밋 창 중에도 무락 evict+재수화로 세대가 또 바뀌었을 수 있다 — 그 세대는
            #   커밋 전 디스크 기준이므로 폐기해 다음 요청이 방금 저장된 정본으로 재수화하게 한다.
            if self.sessions.current(pid) is not sess_now:
                self.sessions.evict(pid)
            sess_now.bus.emit("derivatives", "wiki_rebuilt", chapters=len(inputs), stale_cleared=cleared,
                              pages=len(sess_now.bundle.wiki.pages), source_shells=shells)
        return {"rebuilt_chapters": len(inputs), "stale_cleared": cleared,
                "pages": len(sess_now.bundle.wiki.pages),
                "preserved_pages": len(preserved), "source_shells": shells, "usage_delta": _delta,
                "usage_total": state.usage_total}

    # ---- ST-12c: 비앵커 재실현 패스 + BoN-N Kiwi 리랭크(작가 opt-in 편집 패스 — 자동 임계 트리거 0) ----
    def _kiwi_metrics_fn(self):
        """리랭크 계측 함수 — style_pipeline.kiwi_style_metrics 를 lazy 로 감싼다(엔진 로드타임 tools import 금지).
        부품(kiwipiepy·tools) 부재/실패 시 None → rerender.rerank_candidates 가 '첫 유효 후보'로 강등(결측 정직)."""
        def _fn(text: str):
            try:
                from ..engine.style_pipeline import kiwi_style_metrics
                m = kiwi_style_metrics(text)
                return m or None
            except Exception:
                return None
        return _fn

    def _run_style_judgment_svc(self, text: str, chapter_no: int, info: dict,
                                gen_provider, motif_candidates=None) -> dict | None:
        """HZ-1 ①: 재실현 최종화 경로용 스타일 지각 판정(harness._run_style_judgment 와 동일 계약).

        판정 provider = config style_judge_model(교차 벤더 기본·create_role_provider 재사용). 빈값("")이면
        스왑 없이 gen_provider(sess.provider) 재사용 — aux_model/humanize_model 의 ""=스왑0 관례 동형(테스트 실
        LLM 0 계약도 이 폴백으로 성립). 판정 콜 usage/time 을 info['style_judge_usage']/[…time]에 기록(TM-1 대칭·
        finalize_repairs 로 영속·은폐 금지). 실패(콜/파싱)=None → apply_style_judgment 가 N-4 수리 스킵(보수·결측 정직).
        motif_candidates(HZ-2·선택): N-3 모티프 후보 구절 — 같은 판정 콜에 동봉(harness 경로와 동일·이중 구현 금지)."""
        try:
            from ..engine.style_judge import judge_style
            from ..llm.factory import create_role_provider
            spec = (getattr(self.settings, "style_judge_model", "") or "").strip()
            jp = gen_provider if not spec else create_role_provider(self.settings, spec)
            _jb = jp.usage.chat_tokens
            _jts = time.monotonic()
            judgment = judge_style(jp, text, motif_candidates=motif_candidates)
            info["style_judge_usage"] = jp.usage.chat_tokens - _jb   # 별도 provider 델타(sess.provider 밖 — 정직 계상)
            info["style_judge_time"] = round(time.monotonic() - _jts, 1)
            return judgment
        except Exception:
            return None   # 판정 실패는 채택을 막지 않는다(보수·무강제)

    def _run_narration_detect_svc(self, text: str, chapter_no: int, info: dict, gen_provider,
                                  mode: str = "catchall") -> list:
        """HM-3: N-1(자기해설)·N-2(감정 명명) LLM 탐지(재실현 최종화 경로용 — harness._run_narration_detect 와
        동일 계약·이중 구현 금지). 판정 provider = style_judge_model(교차 벤더). 빈값이면 gen=judge 방지 위해
        탐지 스킵(→[]·info['narration_detect_skipped'] 기록). usage/time 을 info['narration_detect_usage']/
        […time]에 기록(TM-1 대칭·은폐 금지). 실패→[](보수·무강제)."""
        try:
            from ..engine.narration_detect import detect_narration_tells
            from ..llm.factory import create_role_provider
            spec = (getattr(self.settings, "style_judge_model", "") or "").strip()
            if not spec:   # gen≠judge 보장 불가(빈값=gen provider) → 스킵(은폐 금지·측정 4원칙)
                info["narration_detect_skipped"] = "no_cross_vendor_judge"
                return []
            jp = create_role_provider(self.settings, spec)
            _jb = jp.usage.chat_tokens
            _jts = time.monotonic()
            cap = int(getattr(self.settings, "humanize_llm_detect_max_spans", 40) or 40)   # 가시화 상한(수술 예산과 분리)
            found = detect_narration_tells(jp, text, max_spans=cap, mode=mode)
            _sk = "narration_detect" if mode == "catchall" else "narration_detect_precise"   # 모드별 비용 귀속(TM-1)
            info[f"{_sk}_usage"] = info.get(f"{_sk}_usage", 0) + (jp.usage.chat_tokens - _jb)
            info[f"{_sk}_time"] = round(info.get(f"{_sk}_time", 0.0) + (time.monotonic() - _jts), 1)
            return found
        except Exception:
            return []   # 탐지 실패는 채택을 막지 않는다(보수·무강제)

    def _finalize_rerender_text(self, sess, ont, chapter_no: int, text: str,
                               prev_texts: list[str]) -> tuple[str, dict]:
        """ST-14 FIX-3: 재실현 승자 본문을 생성 경로와 *동일한* 최종화 스택으로 통과시킨다 — reflow(ST-3 조판) →
        휴머나이즈 스택(harness finalize 와 동일 분기: humanize ON=HM-1b, OFF=style_repair Stage B 폴백).

        기존 부품 재사용(이중 구현 금지): engine.textfmt.reflow_paragraphs · engine.humanize_pass.humanize_spans /
        engine.style_pipeline.repair_spans. 실패/폴백은 원문 유지(무강제) + 내역 반환(은폐 금지). LLM 콜(휴머나이즈)은
        전부 *락 밖*에서 부른다(호출부 rerender_chapter 가 락 해제 후 이 메서드를 부른다 — 락 규율 불변).

        반환 (finalized_text, repair_info). repair_info = {reflowed, humanize:[…], style_repairs:[…]} — 채택
        revision payload 에 additive 로 기록(수리 내역 투명화). 작가 발동 퇴고(revise)는 이 경로를 타지 않는다
        (작가 의도 존중 — 재실현 자동 파이프만 최종화 스택을 붙인다)."""
        info: dict = {"reflowed": False, "humanize": [], "style_repairs": []}
        cur = text
        # ── reflow(ST-3 조판) — settings.paragraph_reflow 게이트 동일(OFF 면 바이트 동일·호출 생략) ──
        if getattr(self.settings, "paragraph_reflow", True):
            try:
                from ..engine.textfmt import reflow_paragraphs
                new = reflow_paragraphs(cur)
                info["reflowed"] = (new != cur)
                cur = new
            except Exception:
                pass   # 조판 실패는 최종화를 막지 않는다(원문 유지·무강제)
        generator = sess.bundle.generator
        checker = sess.bundle.checker
        roster = {e.name for e in ont.entities.values()}
        _humanize_on = bool(getattr(self.settings, "humanize", False))
        if _humanize_on and getattr(self.settings, "humanize_max_spans", 6) > 0:
            # HM-1b 휴머나이즈 스택(harness 와 동일 함수 재사용·이중 구현 금지) — LLM 콜은 락 밖.
            try:
                from ..engine.humanize_detect import (build_motif_ledger, detect_chapter,
                                                      apply_style_judgment, motif_candidates_from_findings)
                from ..engine.humanize_pass import humanize_spans, build_humanize_provider, bulk_transfer_pass
                # HM-6 하이브리드 1단(harness 와 동형·이중 구현 금지): rewrite ON 이면 통짜 이전-전용 변환 선행.
                if (getattr(self.settings, "humanize_llm_detect", False)
                        and getattr(self.settings, "humanize_llm_detect_rewrite", False)
                        and getattr(self.settings, "humanize_llm_detect_bulk", True)):
                    _hp0 = build_humanize_provider(self.settings)
                    cur, info["bulk"] = bulk_transfer_pass(
                        generator, ont, checker, chapter_no, cur,
                        service=self, humanize_provider=_hp0)
                ledger = None
                if prev_texts:
                    chs = [{"chapter": None, "text": t} for t in prev_texts if (t or "").strip()]
                    chs.append({"chapter": chapter_no, "text": cur})
                    ledger = build_motif_ledger(chs, roster=roster)
                findings = detect_chapter(cur, ledger=ledger, roster=roster)
                # HZ-1 ①④: 재실현 최종화 경로도 harness finalize 와 *동일* 판정을 탄다(이중 구현 금지) —
                #   스타일 지각 판정(매화 무조건·+1콜) → 판정 인용 기반 N-4 선별(사실 밀집 금기·같은 펜). 판정
                #   콜(교차 벤더)은 락 밖. 실패=None → apply_style_judgment 가 N-4 스킵(보수). HZ-2: N-3 모티프
                #   후보도 같은 판정 콜에 동봉(추가 콜 0)해 확인된 것만 수술(harness 경로와 동형). N-5/N-6 불변.
                _motif_cands = motif_candidates_from_findings(findings)
                _sj_judgment = self._run_style_judgment_svc(cur, chapter_no, info, sess.provider,
                                                            motif_candidates=_motif_cands)
                findings, _n4_skips = apply_style_judgment(cur, findings, _sj_judgment, self.settings, roster=roster)
                _narr_on = bool(getattr(self.settings, "humanize_llm_detect", False))
                _narr_rw = bool(getattr(self.settings, "humanize_llm_detect_rewrite", False))
                _narr = (self._run_narration_detect_svc(cur, chapter_no, info, sess.provider)
                         if _narr_on else [])   # HM-3: catch-all = 가시화 전수(advisory)
                _narr_precise = (self._run_narration_detect_svc(cur, chapter_no, info, sess.provider, mode="precise")
                                 if (_narr_on and _narr_rw) else [])   # HM-6: 정밀 = 잔여 자동 수술 선별(잉여만)
                if _narr_precise and _narr_rw:
                    findings = list(findings) + _narr_precise
                hp = build_humanize_provider(self.settings)
                cur, info["humanize"] = humanize_spans(
                    generator, ont, checker, chapter_no, cur, findings,
                    max_spans=(None if getattr(self.settings, "humanize_all_spans", False)
                               else int(getattr(self.settings, "humanize_max_spans", 6))),
                    service=self, humanize_provider=hp)
                info["humanize"] = list(info["humanize"]) + list(_n4_skips)   # HZ-1 은폐 금지: N-4 스킵/금기 기록 additive
                if _narr:   # HM-3 가시화: catch-all 전수를 rewrite 여부 무관 advisory 기록(원장 매화 확인 원자료)
                    from ..engine.harness import _narr_advisory_entry
                    info["humanize"] = list(info["humanize"]) + [_narr_advisory_entry(f) for f in _narr]
                if _sj_judgment is not None:
                    info["style_judge"] = {"needs_repair": bool(_sj_judgment.get("needs_repair")),
                                           "cited": len(_sj_judgment.get("spans") or []),
                                           "reason": (_sj_judgment.get("reason") or "")}
            except Exception:
                info["humanize"] = []   # 브릿지/부품 예외는 무강제 — 원문 유지·가시화(빈 리스트)
        elif (not _humanize_on) and getattr(self.settings, "style_repair", False) \
                and getattr(self.settings, "style_repair_max_spans", 6) > 0:
            # 하위호환 폴백: humanize OFF 일 때만 SP-1 Stage B(리듬 수리) — harness 분기 동형.
            try:
                from ..engine.style_pipeline import repair_spans
                cur, info["style_repairs"] = repair_spans(
                    generator, ont, checker, chapter_no, cur,
                    max_spans=int(getattr(self.settings, "style_repair_max_spans", 6)),
                    service=self)
            except Exception:
                info["style_repairs"] = []
        # RC-6: 재실현 최종화 경로도 강제 교정 스택을 탄다(harness finalize 와 동일 함수·이중 구현 금지).
        #   플래그 OFF(기본)=호출 생략 → 바이트 동일. subs_map 은 harness 와 같은 StyleSpec(generator.style) 에서.
        if getattr(self.settings, "finale_force_fixes", False):
            try:
                from ..engine.finale_force import apply_forced_finale_fixes
                _subs = dict(getattr(getattr(generator, "style", None), "deprecated_terms", None) or {})
                cur, _force_entries = apply_forced_finale_fixes(
                    ont, checker, chapter_no, cur, subs_map=_subs, service=self)
                if _force_entries:
                    info["finale_force"] = _force_entries
                    info["humanize"] = list(info.get("humanize") or []) + _force_entries   # 투명성 영속
            except Exception:
                pass   # 강제 교정 실패가 최종화를 막지 않는다(무강제·원문 유지)
        return cur, info

    def _append_preference_pair(self, pid: str, chapter: int, kind: str,
                                before: str, after: str, meta: dict) -> None:
        """ST-13 대비 preference 쌍 축적(무비용) — data 디렉터리에 append-only jsonl. 프로젝트 JSON 에는 넣지
        않는다(비대화 방지). kind: 'rerender'(재실현 전→후) | 'bon_reject'(리랭크 탈락→선택). 실패는 조용히 스킵
        (기록 실패가 채택을 막지 않음 — 무강제·정직).
        FI-3 ⓑ: append 를 프로세스 내 뮤텍스로 직렬화 — 8KB+ 라인의 동시 append 인터리빙 소스 차단
        (공유 단일 jsonl — 트레이스 락 선례와 동형·이 락 안에서 다른 락 획득 없음)."""
        import os
        try:
            path = self.settings.resolved_data_dir() / "preference_pairs.jsonl"
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": pid, "chapter": chapter,
                   "kind": kind, "before": before, "after": after, "meta": meta}
            line = json.dumps(row, ensure_ascii=False)
            with _PREF_PAIR_LOCK:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
        except Exception:
            pass   # 기록 실패는 채택 흐름을 막지 않는다(무강제·정직 — jsonl 부재는 advisory 손실일 뿐)

    def _save_rerender_trace(self, pid: str, chapter_no: int, *, mode: str, adopted: bool,
                             reason: str, before_text: str, after_text: str,
                             candidates: list, rr: dict | None,
                             guardrail: dict | None = None, read_gate: dict | None = None,
                             repair_info: dict | None = None, extra: dict | None = None) -> None:
        """GA-1: 재실현(rerender) 트레이스를 회차 생성 trace 사이드카에 append(kind='rerender').

        BoN 후보 **전체 텍스트** + 리랭크 evaluations(각 후보 실격 사유·점수) + 최종화 스택 수리 내역 +
        정독 게이트·가드 판정을 같은 사이드카(`<pid>.trace.<ch>.json`)의 runs 에 append(회차 생성 trace 와
        같은 무덤). 현재 후보 전체는 preference jsonl 에만 있고 회차 trace 엔 없다(GA-1 이 그 손실을 닫는다).
        무강제(try/except)·OFF(config gen_trace)면 미저장. adopted 여부와 무관하게 저장('싹 다 저장')."""
        if not getattr(self.settings, "gen_trace", True):
            return
        try:
            evals = None
            if rr is not None:
                evals = [{k: e.get(k) for k in
                          ("index", "disqualified", "reasons", "dialogue_pres", "missing_numerics",
                           "length_ratio", "top_ratio", "max_run", "da_ratio", "uninterrupted_run_max")}
                         for e in (rr.get("evaluations") or [])]
            run = {
                "kind": "rerender",
                "chapter": chapter_no,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "mode": mode,
                "adopted": bool(adopted),
                "reason": reason,
                "before_text": before_text,
                "after_text": after_text,
                "candidates": list(candidates or []),   # BoN 후보 전문(전체 텍스트 — preference jsonl 밖 회차 trace 보존)
                "winner_index": (rr.get("winner_index") if rr else None),
                "evaluations": evals,                   # 각 후보 실격 사유·점수
                "guardrail": guardrail,                 # 캐넌/G-B 가드 판정
                "read_gate": read_gate,                 # 정독 게이트 판정(OFF/생략 시 None)
                "finalize_repairs": repair_info,        # 최종화 스택(reflow→휴머나이즈/style_repair) 수리 내역
                **(extra or {}),                        # SP-3: 뼈대 소스·사전 대조·추가 대사 계수 등 관측 축
            }
            self.repo.save_trace(pid, chapter_no, run,
                                 max_runs=int(getattr(self.settings, "gen_trace_max_runs", 50)))
        except Exception:
            pass   # 관측 사이드카 — 저장 실패가 재실현 흐름을 막지 않는다(무강제·정직)

    @promptlog.stage("rerender")
    def rerender_chapter(self, pid: str, chapter_no: int, mode: str = "bon") -> dict | None:
        """ST-12c: 기존 FINALIZED 회차를 원문 프로즈 없이(비앵커) 사실 뼈대에서 웹소설 레지스터로 다시 실현한다.

        흐름(설계 §5): 입력 조립(결정론) → 프롬프트(P4-V) → BoN-N 병렬 후보(humanize_model) → 결정론 리랭크+실격
        → 최종화 스택(ST-14) → 가드 스택(캐넌 check_text·G-B 표면 비교) → 채택=revision 제안(revise/accept 계약
        재사용·undo 가능). 원문은 항상 보존(무강제) — 실패·전 후보 실격·가드 불통과 = 원문 유지 + 정직 사유.
        423=회차 생성 중이면 None.

        mode (ST-14 라이브 보정):
          "bon"(기본)   — 위 전체 흐름(비앵커 재실현). 기존 계약 불변.
          "repair"      — BoN 재추첨 없이 *현재 본문*을 최종화 스택(reflow→휴머나이즈/문말 수리)에만 태워
                          국소 벽을 수리한다. 기존 회차의 문체 수리 재적용용(웹 버튼 백엔드 겸). 재추첨이
                          아니라서 프로즈 재작성 리스크·가드 기각 반복이 없고, 무변경이면 정직하게 미채택.
                          정독 게이트는 생략(스팬 수리는 자체 커버리지·사실 가드 보유 + 아래 지표 무해 가드),
                          재요약도 생략(사실 불변이 가드로 강제됨 — summary 유효 유지).
        두 모드 공통(ST-14): 채택 전 *지표 무해 가드* — 최종본이 원문보다 top_ratio 대역 거리·무중단 동일 종결
        run 에서 후퇴하면 기각(rerender.metric_no_harm — '유일한 유효 후보'가 원문보다 나쁜데 채택되던 실측 구멍).

        락 규율(revise/accept 계보): LLM 콜(재실현·가드 check_text·재요약)은 sess.lock 밖. sess.lock 은
        (1) 스냅샷·상태검사 (2) 최종 기록 두 번만 짧게 진입한다."""
        if mode not in ("bon", "repair"):
            raise ValueError(f"알 수 없는 mode: {mode}")
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        ch = state.chapter(chapter_no)
        if not ch:
            raise KeyError(chapter_no)
        if ch.status not in (ChapterStatus.FINALIZED, ChapterStatus.ESCALATED):
            raise ValueError("대상 회차가 아닙니다")
        sess = self.sessions.get_or_create(state)
        # ── (1) 스냅샷(빠름) — sess.lock 을 non-blocking 으로. 회차 생성 중이면 즉시 423(lost-update 방지).
        if not sess.lock.acquire(blocking=False):
            return None
        no_skeleton = False
        try:
            state = self.repo.get(pid)          # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            if ch.status not in (ChapterStatus.FINALIZED, ChapterStatus.ESCALATED):
                raise ValueError("대상 회차가 아닙니다")
            before_text = ch.text
            # SP-3(감사 C1): 뼈대 1순위 = 작가 확정 스토리(공개 순서 내장·해설 레지스터 0 — 13화 재실현
            #   실패 원인 ①② 소스 차단). 단 계획↔본문 어긋남(인물 표기 부재·미실현 의심 줄)이 있으면
            #   그 뼈대의 재실현은 문체 패스가 아니라 사실 변경 패스가 되므로 요약 뼈대로 폴백(정직 사유·
            #   no_skeleton 폴백과 동형). 대조는 결정론·보조 — 정본 방어선은 발사 전 전문 정독(런북 2-bis).
            skeleton = ""
            skeleton_source = ""
            skeleton_precheck: dict = {}
            if mode == "bon":
                _confirmed = (self._confirmed_story_for(state, chapter_no) or "").strip()
                if _confirmed:
                    _mismatch = self._skeleton_char_mismatch(sess.bundle.ontology, before_text, _confirmed)
                    try:
                        from ..engine import story_pass_prompts as _spp
                        from ..engine.drift import uncovered as _unc
                        _unreal = len(_spp.remaining_story_lines(_confirmed, before_text, _unc))
                    except Exception:
                        _unreal = None   # 계측 불가 = 결측 정직(이 축으로 폴백시키지 않음)
                    skeleton_precheck = {"mismatch_names": _mismatch, "unrealized_lines": _unreal,
                                         "skeleton_dashes": _confirmed.count("—")}   # m2: EM-1 가시화
                    if not _mismatch and not _unreal:
                        skeleton, skeleton_source = _confirmed, "confirmed_story"
                    else:
                        skeleton_precheck["fallback_reason"] = (
                            f"확정 스토리↔본문 어긋남(표기 부재 {len(_mismatch)}명·미실현 의심 {_unreal}줄) — 요약 뼈대 폴백")
            # 폴백 = detail_synopsis(부재 시 summary — 그것도 없으면 패스 거부·정직 사유).
            #   repair 모드는 뼈대 불요(현재 본문이 입력) — 뼈대 없어도 진행.
            if not skeleton:
                skeleton = (getattr(ch, "detail_synopsis", "") or "").strip() or (getattr(ch, "summary", "") or "").strip()
                if skeleton:
                    skeleton_source = "detail_synopsis"
            if not skeleton and mode == "bon":
                no_skeleton = True              # 락은 finally 에서 정확히 1회 해제(locked() TOCTOU 회피 — release 는 이 스레드만)
            else:
                ont = sess.bundle.ontology
                # 대사·수치·고유명사 결정론 추출 + 서술자 음성 카드(1인칭·주인공 voice 있을 때)
                from ..engine import rerender as _rr
                dialogue_lines = _rr.extract_dialogue_lines(before_text)
                numeric_tokens = _rr.extract_numeric_tokens(before_text)
                # RR-1: 명부 전체가 아니라 *재실현 대상 본문에 실제 등장하는* 토큰만(사람 단위 그룹핑). 부재(데뷔 전)
                #   인물 주입을 구조적으로 불가능하게 하는 소스 차단 — 괴담작 1화 캐논 파괴 사건 직타.
                proper_nouns = _rr.entity_tokens_in_source(ont, before_text)
                narrator_card = ""
                if getattr(state.world.style, "pov", "") == "first":
                    from ..worldgen.narrator_voice import find_protagonist_id
                    _pid = find_protagonist_id(ont)
                    _ent = ont.entities.get(_pid) if _pid else None
                    if _ent is not None:
                        narrator_card = getattr(_ent, "voice", "") or ""
                    if not narrator_card.strip():
                        # 주인공 entity voice 를 비워 이중 주입을 폐쇄한 프로젝트에서도 P4-V 의
                        # 서술자 음성 블록은 유지돼야 한다(음성 0 + 형태 지시만 남는 구성은
                        # ST-12c 파일럿 정독 패배 방향) — 화자 정체성의 단일 소스로 폴백.
                        narrator_card = (getattr(state.world.style, "narrator_voice", "") or "")
                ids = sorted(set(ont.scan_present_ids(before_text)))
                beat_snapshot = _persisted_beat(ch)   # RV-2①: 재요약 폴백을 생성 경로 동형으로
                # FIX-3: 최종화 스택(휴머나이즈 N-3 모티프 원장)이 쓸 선행 회차 본문 — 스냅샷 시점 캡처(락 안).
                prev_texts = [c.text for c in state.chapters
                              if c.chapter < chapter_no and c.status == ChapterStatus.FINALIZED]
        finally:
            sess.lock.release()                 # ── LLM 콜 전에 락 해제(이 스레드가 잡은 락만 정확히 1회 — 무조건)
        if no_skeleton:
            return {"adopted": False, "reason": "사실 뼈대(detail_synopsis/summary)가 없어 재실현할 수 없습니다",
                    "chapter": chapter_no}

        from ..engine import rerender as _rr    # (락 밖) 프롬프트 조립·리랭크에 사용
        _rr_t0 = time.monotonic()   # TM-1: rerender 소요 시간 baseline(usage_before 대칭 — BoN·가드·재요약 전 구간)
        rr = None
        win_i = None
        candidates: list[str] = []
        usage_delta: dict = {}
        if mode == "repair":
            # ST-14 repair 모드: 재추첨 없이 현재 본문이 최종화 스택의 입력 — (2)(3) 생략(LLM 0·실격 0).
            after_text = before_text
        else:
            system, user = _rr.build_rerender_prompt(
                narrator_card=narrator_card, skeleton=skeleton, dialogue_lines=dialogue_lines,
                numeric_tokens=numeric_tokens, proper_nouns=proper_nouns,
                orig_chars=len(before_text),   # 파일럿 1차 보정: 비앵커라 분량은 절대 자수로 지시
                pov=getattr(state.world.style, "pov", "") or "",   # SP-3 M1: 인칭 값 실체화
                skeleton_is_story=(skeleton_source == "confirmed_story"))   # SP-3 C1: 층위 라벨

            # ── (2) BoN-N 병렬 후보(같은 프롬프트 N회·temperature 0.85) — humanize_model 라우팅 재사용(create_role_provider).
            #   create_role_provider 는 모듈 상단 import 를 그대로 쓴다(테스트가 이 이름을 monkeypatch 로 스텁 교체 가능).
            provider = create_role_provider(self.settings, getattr(self.settings, "humanize_model", "") or "")
            n = max(1, int(getattr(self.settings, "rerender_bon_n", 3)))
            usage_before = provider.usage.as_dict()
            from ..engine.harness import sanitize_meta   # 생성물 메타 라인 살균(revise_prose 동형·lazy)
            for _ in range(n):
                try:
                    out = provider.chat([{"role": "system", "content": system},
                                         {"role": "user", "content": user}],
                                        temperature=0.85)
                except Exception:
                    out = ""
                out = sanitize_meta((out or "").strip())
                if out:
                    candidates.append(out)
            usage_delta = _usage_delta(usage_before, provider.usage.as_dict())

            if not candidates:
                self._save_rerender_trace(pid, chapter_no, mode=mode, adopted=False,
                                          reason="재실현 후보를 하나도 생성하지 못했습니다(LLM 실패)",
                                          before_text=before_text, after_text=before_text,
                                          candidates=[], rr=None)   # GA-1: 실패도 정직 기록
                return {"adopted": False, "reason": "재실현 후보를 하나도 생성하지 못했습니다(LLM 실패)",
                        "chapter": chapter_no, "usage": usage_delta}

            # ── (3) 결정론 리랭크 + 하드 실격(대사 유실/수치 누락/분량 + RR-1 명부 발명). Kiwi 계측은 lazy(엔진 로드타임 import 0).
            #   RR-1 B: 원문에 없는 명부 인물이 후보에 등장하면 하드 실격(사실 불변 계약 집행). A(주입)와 같은 매칭 규칙 재사용.
            # SP-3(13화 재파일럿 실측): 확정 뼈대 경로에서는 발명 판정의 정당 토큰에 뼈대 표기를 병합 —
            #   계약이 "뼈대 그대로 실현하라"고 명령한 문면('포획 구슬' 정식명)을 가드가 발명으로 실격시키던
            #   계약·가드 자기모순 해소. 사전 대조를 통과한 확정 뼈대는 작가 승인 캐논이다(요약 폴백은 원문 단독).
            _foreign_src = (before_text + "\n" + skeleton) if skeleton_source == "confirmed_story" else before_text
            rr = _rr.rerank_candidates(candidates, original=before_text, dialogue_lines=dialogue_lines,
                                       numeric_tokens=numeric_tokens, metrics_fn=self._kiwi_metrics_fn(),
                                       foreign_fn=lambda c: _rr.foreign_roster_tokens(c, ont, _foreign_src))
            # preference 쌍은 *실제 채택*이 확정된 뒤에만 기록한다(ST-13 clean 데이터 오염 차단 — 가드 불통과·미채택
            #   승자를 'after'로 남기면 학습 데이터가 거절된 후보를 정답으로 오학습). 아래서 채택 성공 시 일괄 flush.
            win_i = rr["winner_index"]
            if win_i is None:
                self._save_rerender_trace(pid, chapter_no, mode=mode, adopted=False,
                                          reason=rr["reason"], before_text=before_text,
                                          after_text=before_text, candidates=candidates, rr=rr)   # GA-1: 전 후보 실격
                return {"adopted": False, "reason": rr["reason"], "chapter": chapter_no,
                        "usage": usage_delta,
                        "evaluations": [{k: e.get(k) for k in
                                         ("index", "disqualified", "reasons", "dialogue_pres",
                                          "missing_numerics", "length_ratio", "top_ratio", "max_run", "da_ratio",
                                          "uninterrupted_run_max", "foreign_tokens")}   # FIX-2 무중단 run·RR-1 명부 발명 투명화
                                        for e in rr["evaluations"]]}
            after_text = rr["winner"]

        # ── (3.5) 관문 단일화(ST-14 FIX-3) — 재실현 승자를 생성 경로와 동일한 최종화 스택으로 통과시킨다:
        #   reflow(ST-3 조판) → 휴머나이즈 스택(HM-1b, OFF 시 style_repair 폴백). 이래야 심사·가드·정독의
        #   대상이 '최종본'(휴머나이즈까지 반영)이 된다 — R1(재실현이 최종화 스택 밖 배선) 급소를 닫는다.
        #   LLM 콜(휴머나이즈)은 이 시점(락 밖)에 돈다(락 규율 불변). 실패/폴백은 원문(승자) 유지·무강제.
        #   sess.provider 토큰 계상 baseline 은 이 콜 *앞*에 찍어(휴머나이즈 토큰도 usage_delta 에 포함) 언더카운트 차단.
        _sess_usage_before = sess.provider.usage.as_dict()
        after_text, repair_info = self._finalize_rerender_text(sess, ont, chapter_no, after_text, prev_texts)
        # SP-3(감사 M3): 최종화 스택(LLM 수술) 이후 대사·수치 계약을 재검사 — 사슬 마지막에 계약 검사가
        #   없던 구멍. 깨졌으면 최종화 전 승자(이미 검사 통과분)로 롤백(미채택 아님 — 개선분만 포기·무강제).
        if mode == "bon" and rr is not None and after_text != rr["winner"]:
            if (_rr.dialogue_preservation(after_text, dialogue_lines) < 1.0
                    or _rr.missing_numerics(after_text, numeric_tokens)):
                after_text = rr["winner"]
                repair_info = {**(repair_info or {}),
                               "finalize_rollback": "최종화 후 대사·수치 계약 재검사 불통과 — 최종화 전 승자로 롤백"}
        # SP-3 관측 축(trace 동봉): 뼈대 소스·사전 대조 + 추가 대사 계수(M4 — 서술-대사 이중 실현 가시화).
        _sp3_extra = {"skeleton_source": skeleton_source, "skeleton_precheck": skeleton_precheck}
        if mode == "bon":
            _sp3_extra["dialogue_added"] = len(_rr.extract_dialogue_lines(after_text)) - len(dialogue_lines)
        if mode == "repair" and after_text == before_text:
            # repair 모드 무변경 = 수리 스팬이 없거나 전부 폴백 — 정직 미채택(revision 생성하지 않음).
            usage_delta = _accumulate(usage_delta, _usage_delta(_sess_usage_before, sess.provider.usage.as_dict()))
            self._save_rerender_trace(pid, chapter_no, mode=mode, adopted=False,
                                      reason="수리 무변경(스팬 없음/전부 폴백)", before_text=before_text,
                                      after_text=after_text, candidates=candidates, rr=rr,
                                      repair_info=repair_info)   # GA-1: repair 무변경도 기록
            return {"adopted": False, "reason": "수리 무변경(스팬 없음/전부 폴백)", "chapter": chapter_no,
                    "usage": usage_delta,
                    "finalize_repairs": {"reflowed": repair_info.get("reflowed"),
                                         "humanize_spans": len(repair_info.get("humanize") or []),
                                         "style_repair_spans": len(repair_info.get("style_repairs") or [])}}

        # ── (4) 가드 스택 — 승자(최종화 완료본)에 기존 부품 재사용(humanize_pass 소비 방식과 동형: 불통과/오류 = 채택 금지).
        #   정직 계측: 가드 check_text·_summarize·휴머나이즈는 sess.provider 로 돈다 → 그 토큰도 usage_delta 에
        #   additive 계상(비용 언더카운트 차단·MEMORY 고비용 결재 정합). baseline(_sess_usage_before)은 3.5 앞에 찍음.
        checker = sess.bundle.checker
        before_res = checker.check_text(before_text, ont, chapter_no, ids)
        guardrail, after_res = self._guardrail(before_text, after_text, before_res, ids, ont, checker, chapter_no)
        # 승자 kiwi 계측(전/후 SSOT 병기용) — lazy·부재 시 None
        kiwi_before = self._kiwi_metrics_fn()(before_text)
        kiwi_after = self._kiwi_metrics_fn()(after_text)
        if not guardrail["passed"]:
            usage_delta = _accumulate(usage_delta, _usage_delta(_sess_usage_before, sess.provider.usage.as_dict()))
            self._save_rerender_trace(pid, chapter_no, mode=mode, adopted=False,
                                      reason="가드 불통과: " + guardrail["reason"], before_text=before_text,
                                      after_text=after_text, candidates=candidates, rr=rr,
                                      guardrail=guardrail, repair_info=repair_info,
                                      extra=_sp3_extra)   # GA-1: 가드 불통과 후보 기록
            return {"adopted": False, "reason": "가드 불통과: " + guardrail["reason"], "chapter": chapter_no,
                    "usage": usage_delta,
                    "guardrail": {k: guardrail[k] for k in
                                  ("passed", "G_A_passed", "G_B_passed", "length_ok",
                                   "new_hard", "claim_changes", "claim_flaps", "new_keys_advisory", "reason")},
                    "before_kiwi": (kiwi_before or {}).get("ending_profile") if kiwi_before else None,
                    "after_kiwi": (kiwi_after or {}).get("ending_profile") if kiwi_after else None}

        # ── (4.4) 지표 무해 가드(ST-14 라이브 보정 — 결정론·config 게이트): 최종본이 *원문보다* top_ratio 대역
        #   거리 또는 무중단 동일 종결 키 run 에서 후퇴하면 기각. 실측 구멍: '유일한 유효 후보'가 원문(0.606)보다
        #   나쁜 0.723 으로 채택됨 — 리랭크는 후보 간 비교뿐, 원문 대비 무해는 아무도 안 봤다. 결측(None)은 비교 생략.
        _ep_b = (kiwi_before or {}).get("ending_profile") if kiwi_before else None
        _ep_a = (kiwi_after or {}).get("ending_profile") if kiwi_after else None
        if bool(getattr(self.settings, "rerender_metric_no_harm", True)):
            _nh_ok, _nh_reasons = _rr.metric_no_harm(_ep_b, _ep_a,
                                                     _rr._uninterrupted_run_max(before_text),
                                                     _rr._uninterrupted_run_max(after_text))
            if not _nh_ok:
                usage_delta = _accumulate(usage_delta, _usage_delta(_sess_usage_before, sess.provider.usage.as_dict()))
                self._save_rerender_trace(pid, chapter_no, mode=mode, adopted=False,
                                          reason="지표 무해 가드: " + " / ".join(_nh_reasons),
                                          before_text=before_text, after_text=after_text,
                                          candidates=candidates, rr=rr, guardrail=guardrail,
                                          repair_info=repair_info, extra=_sp3_extra)   # GA-1: 무해 가드 기각 기록
                return {"adopted": False, "reason": "지표 무해 가드: " + " / ".join(_nh_reasons),
                        "chapter": chapter_no, "usage": usage_delta,
                        "before_kiwi": _ep_b, "after_kiwi": _ep_a}

        # ── (4.5) 정독 게이트(1차 척도 이행 — 파일럿 2차 보정 2026-07-14): BoN 승자가 kiwi 대역에 들어도
        #   정독에서 원문에 완패(양순서 keep_reading 열세)하면 채택 금지 — 패스를 no-harm 편집으로.
        #   근거: ch1 실측(top 0.417 대역 진입 + 정독 0-2 — '대역≠품질' Goodhart 의 제품 경로 재현).
        #   cross-vendor(make_judge — gen≠judge) 양순서 2콜, 판정 불가 전건이면 보수 기각. 토큰은 usage 계상.
        #   repair 모드는 생략: 국소 스팬 수리(자체 커버리지·사실 가드)라 전체 쌍대 정독은 과잉·비용 낭비.
        read_gate = None
        if mode == "bon" and bool(getattr(self.settings, "rerender_read_gate", True)):
            read_gate = self._rerender_read_gate(before_text, after_text)
            usage_delta = _accumulate(usage_delta, read_gate.pop("usage", {}) or {})
            if not read_gate.get("adopt"):
                self._save_rerender_trace(pid, chapter_no, mode=mode, adopted=False,
                                          reason="정독 게이트: " + (read_gate.get("reason") or ""),
                                          before_text=before_text, after_text=after_text,
                                          candidates=candidates, rr=rr, guardrail=guardrail,
                                          read_gate=read_gate, repair_info=repair_info,
                                          extra=_sp3_extra)   # GA-1: 정독 기각 기록
                return {"adopted": False, "reason": "정독 게이트: " + (read_gate.get("reason") or ""),
                        "chapter": chapter_no, "usage": usage_delta, "read_gate": read_gate,
                        "skeleton_source": skeleton_source, "skeleton_precheck": skeleton_precheck,
                        "before_kiwi": (kiwi_before or {}).get("ending_profile") if kiwi_before else None,
                        "after_kiwi": (kiwi_after or {}).get("ending_profile") if kiwi_after else None}

        # ── (5) 채택 = revision 제안(기존 accept 계약 재사용). 재요약(1콜)은 생성 경로 동형(beat 관통·P-2 폴백 차단).
        #   repair 모드는 재요약 생략(국소 수리 — 사실 불변이 G-A/G-B 로 강제됨·summary 유효 유지·1콜 절약).
        if mode == "repair":
            new_summary, new_detail, _rev_degraded = ch.summary, ch.detail_synopsis, ch.summary_degraded
        else:
            # CE-1 ⓑ: 재요약(_summarize)은 aux provider 로 돈다 → 그 토큰은 sess.provider 델타에 안 잡힌다.
            #   aux 델타를 별도로 additive 합산(정직 계측·언더카운트 차단). 폴백(aux is provider)이면 이 콜은 이미
            #   sess.provider 델타에 포함 → aux==provider 판정으로 0 을 더해 이중계상 방지.
            _aux_r = getattr(sess, "aux_provider", None) or sess.provider
            _aux_r_before = _aux_r.usage.as_dict()
            new_summary, new_detail, _rev_degraded = sess.bundle.generator._summarize(after_text, "", beat_snapshot)
            if _aux_r is not sess.provider:
                usage_delta = _accumulate(usage_delta, _usage_delta(_aux_r_before, _aux_r.usage.as_dict()))
        # sess.provider(가드 check_text·재요약) 토큰을 usage_delta 에 additive 계상(정직 계측 — BoN provider 외 비용 포함).
        usage_delta = _accumulate(usage_delta, _usage_delta(_sess_usage_before, sess.provider.usage.as_dict()))
        revision_id = uuid.uuid4().hex[:12]
        # ── (6) 최종 기록 — sess.lock 재진입(짧게). 본문 변경·더블 방어(락 밖 콜 사이 경쟁 차단).
        with sess.lock:
            state = self.repo.get(pid)          # 권위 재읽기
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            ch = state.chapter(chapter_no)
            if not ch:
                raise KeyError(chapter_no)
            if ch.text != before_text:          # 락 밖 LLM 콜 사이 본문 교체됨 → before 캐시 무효(lost-update)
                raise ValueError("본문이 생성 사이 변경됨")
            authoritative_prior_stale = dict(getattr(ch, "derivatives_revised_stale", {}) or {})
            new_stale = self._stale_derivatives_for_accept(state, ch)
            merged_stale = {**authoritative_prior_stale, **new_stale}
            ch.revisions.append(ChapterRevision(
                revision_id=revision_id,
                directive=("[ST-14 문체 수리]" if mode == "repair" else "[ST-12c 비앵커 재실현]"), span_text="",
                before_text=before_text, after_text=after_text,
                before_summary=ch.summary, before_detail_synopsis=ch.detail_synopsis,
                before_summary_degraded=ch.summary_degraded,
                before_derivatives_revised_stale=authoritative_prior_stale,
                passes_used=[],
                violations_before=list(before_res.hard), violations_after=list(after_res.hard),
                claim_changes=guardrail["claim_changes"],
                claim_flaps=guardrail.get("claim_flaps") or [],   # VP-3: 요동 강등분 영속(정직 기록)
                guardrail_passed=True,
                guardrail_reason=guardrail["reason"],
                finalize_repairs=repair_info,   # FIX-3: 최종화 스택(reflow→휴머나이즈) 내역 additive 기록(은폐 금지)
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
            ch.text = after_text
            ch.summary, ch.detail_synopsis = new_summary, new_detail
            ch.summary_degraded = bool(_rev_degraded)
            ch.ai_tell = self._recompute_ai_tell(state, sess, after_text)
            ch.derivatives_revised_stale = merged_stale
            # usage_by_stage 에 'rerender' 스테이지 기록(비용 계측 관행)
            ch.usage_by_stage = {**(ch.usage_by_stage or {}),
                                 "rerender": ch.usage_by_stage.get("rerender", 0) + usage_delta.get("chat_tokens", 0)}
            # TM-1: time_by_stage 에도 'rerender' 소요 시간 대칭 누적(usage 와 동일 키·초·소수1). 구 dict 값을 먼저 읽고 재대입.
            ch.time_by_stage = {**(ch.time_by_stage or {}),
                                "rerender": round((ch.time_by_stage or {}).get("rerender", 0.0)
                                                  + (time.monotonic() - _rr_t0), 1)}
            state.usage_total = _accumulate(state.usage_total, usage_delta)
            # verification 재집계(build_verification)로 전/후 kiwi 가 SSOT 에 남게. gate 는 재실현이 돌리지 않는
            #   축이므로 기존 영속 판정을 그대로 이월한다(b27f7bd '허위 결측 차단' 계약 — gate 미전달 시 MISSING 로 덮여
            #   러너/제품 게이트 판정이 소실되던 회귀 방지). 기존 gate 가 실판정(dict)일 때만 이월(MISSING 문자열은 무시).
            try:
                from ..engine.verification import build_verification
                prev_texts = [c.text for c in state.chapters
                              if c.chapter < chapter_no and c.status == ChapterStatus.FINALIZED]
                _prior_gate = (ch.verification or {}).get("gate")
                _prior_gate = _prior_gate if isinstance(_prior_gate, dict) else None
                # SX-2: cold_read 도 재실현이 새로 돌리지 않는 축 — 기존 실판정(dict)을 그대로 이월(MISSING/결측=None → MISSING).
                _prior_cr = (ch.verification or {}).get("cold_read")
                _prior_cr = _prior_cr if isinstance(_prior_cr, dict) else None
                ch.verification = build_verification(
                    ch, prev_texts=prev_texts,
                    target_chars=getattr(state.world.style, "target_chars_per_chapter", None),
                    gate=_prior_gate, cold_read=_prior_cr,
                    leak_sources=self._leak_sources_for(state, chapter_no),   # VL-1
                    ending_sources=self._ending_sources_for(state, ch))       # XR-1
            except Exception:
                pass   # 검증 집계 실패는 채택을 막지 않는다(SSOT advisory)
            sess.bundle.rag.index_chapter(chapter_no, after_text)
            sess.snapshot_into(state)
            self.repo.save(state)
        # GA-1: 재실현 채택 trace(BoN 후보 전체 + 리랭크 eval + 최종화 수리 + 정독/가드 판정)를 회차 사이드카에 append.
        self._save_rerender_trace(pid, chapter_no, mode=mode, adopted=True,
                                  reason=(rr["reason"] if rr else "문체 수리(최종화 스택) 채택"),
                                  before_text=before_text, after_text=after_text,
                                  candidates=candidates, rr=rr, guardrail=guardrail,
                                  read_gate=read_gate, repair_info=repair_info, extra=_sp3_extra)
        # preference 쌍 일괄 flush(무비용 — ST-13 clean 데이터). *채택 성공 후에만* — 거절된 승자를 정답으로
        #   오학습하는 오염 차단. (a) 재실현 전→후(rerender) (b) 탈락 후보→채택 승자(bon_reject).
        #   repair 모드는 국소 수리 쌍(kind=repair)만 — 재실현 preference 와 데이터 종류 분리(오염 방지).
        self._append_preference_pair(
            pid, chapter_no, ("repair" if mode == "repair" else "rerender"),
            before=before_text, after=after_text,
            meta={"revision_id": revision_id,
                  "before_kiwi": (kiwi_before or {}).get("ending_profile") if kiwi_before else None,
                  "after_kiwi": (kiwi_after or {}).get("ending_profile") if kiwi_after else None})
        for ev in (rr["evaluations"] if rr else []):
            if ev["index"] != win_i:
                self._append_preference_pair(
                    pid, chapter_no, "bon_reject",
                    before=candidates[ev["index"]], after=after_text,
                    meta={"reasons": ev.get("reasons"), "disqualified": ev.get("disqualified"),
                          "top_ratio": ev.get("top_ratio"), "max_run": ev.get("max_run"),
                          "da_ratio": ev.get("da_ratio")})
        return {
            "adopted": True,
            "reason": (rr["reason"] if rr else "문체 수리(최종화 스택) 채택"), "chapter": chapter_no,
            "skeleton_source": skeleton_source, "skeleton_precheck": skeleton_precheck,
            "revision_id": revision_id, "revision_count": len(ch.revisions),
            "before_text": before_text, "after_text": after_text, "span_text": "",
            "before_kiwi": (kiwi_before or {}).get("ending_profile") if kiwi_before else None,
            "after_kiwi": (kiwi_after or {}).get("ending_profile") if kiwi_after else None,
            "usage": usage_delta,
            "guardrail": {k: guardrail[k] for k in
                          ("passed", "G_A_passed", "G_B_passed", "length_ok",
                           "new_hard", "claim_changes", "claim_flaps", "new_keys_advisory", "reason")},
            "read_gate": read_gate,   # 정독 게이트 판정 투명화(OFF 면 None)
            "finalize_repairs": {"reflowed": repair_info.get("reflowed"),   # FIX-3: 최종화 스택 내역 투명화
                                 "humanize_spans": len(repair_info.get("humanize") or []),
                                 "style_repair_spans": len(repair_info.get("style_repairs") or [])},
        }

    # ---- CV-1/CV-2: 표지 이미지 생성·보관함(작가 발동형 — 자동 0·회차 파이프 무접촉) ----
    @staticmethod
    def _ensure_cover_gallery(state: ProjectState) -> None:
        """CV-2 lazy migration: 적용본(state.cover)만 있고 보관함(covers)이 비면 1건짜리 갤러리로 승격(멱등).
        legacy 파일명({pid}.cover.png)은 그대로 — read_cover_file 이 접두/접미 검증을 통과해 읽는다.
        history 는 비워 append(보관함이 이력을 대체 — 중첩 메타 방지)."""
        if state.cover is not None and not state.covers:
            state.covers = [state.cover.model_copy(update={"history": []})]

    def get_cover_bytes(self, pid: str) -> bytes | None:
        """'적용된' 표지 PNG 바이트(없으면 None → 라우트 404). 메타가 있어도 파일이 없으면 legacy 폴백·그마저 없으면 None(정직)."""
        state = self.repo.get(pid)
        if not state:
            return None
        if state.cover is not None:
            b = self.repo.read_cover_file(pid, state.cover.filename)
            if b is not None:
                return b
        return self.repo.read_cover_bytes(pid)          # legacy {pid}.cover.png 폴백

    def get_cover_file_bytes(self, pid: str, filename: str) -> bytes | None:
        """보관함 개별 표지 PNG 바이트(없는 작품·검증 실패·부재 → None → 라우트 404)."""
        if not self.repo.get(pid):
            return None
        return self.repo.read_cover_file(pid, filename)

    def list_covers(self, pid: str) -> dict | None:
        """표지 보관함 목록 + 현재 적용본. 없는 작품=None(404).
        구 CV-1 데이터(cover 만 있고 covers 빈) 는 lazy migration(멱등)해 영속한다.
        단 락 경합(회차/표지 생성 중)이면 영속은 미루고 뷰만 승격해 반환 — 조회 GET 이 생성 종료까지 블로킹되지 않게."""
        state = self.repo.get(pid)
        if not state:
            return None
        if state.cover is not None and not state.covers:     # lazy migration(영속 — 멱등이라 다음 기회에 해도 안전)
            sess = self.sessions.get_or_create(state)
            if sess.lock.acquire(blocking=False):
                try:
                    state = self.repo.get(pid)               # 권위 재읽기(lost update 방지)
                    if not state:
                        return None
                    sess = self._resolve_locked_session(sess, state)
                    self._ensure_cover_gallery(state)
                    self.repo.save(state)
                finally:
                    sess.lock.release()
            else:
                self._ensure_cover_gallery(state)            # 뷰 전용 승격(무영속) — 응답은 정확·저장은 다음 호출
        return {"covers": [c.model_dump() for c in state.covers],
                "applied": (state.cover.filename if state.cover else None)}

    def apply_cover(self, pid: str, filename: str):
        """보관함에서 표지 하나를 '적용본'으로 전환(state.cover 갱신). 없는 작품=None(404),
        생성 중(락 보유)=False(423), 파일명 미존재=ValueError(라우트 404). 반환=적용 메타 dict."""
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        if not sess.lock.acquire(blocking=False):            # 회차/표지 생성 중이면 즉시 False(423)
            return False
        try:
            state = self.repo.get(pid)                       # 권위 재읽기
            if not state:
                return None
            sess = self._resolve_locked_session(sess, state)
            self._ensure_cover_gallery(state)
            target = next((c for c in state.covers if c.filename == filename), None)
            if target is None:
                raise ValueError(f"표지를 찾을 수 없습니다: {filename}")
            state.cover = target
            self.repo.save(state)
            return target.model_dump()
        finally:
            sess.lock.release()

    def delete_cover(self, pid: str, filename: str):
        """보관함에서 표지 하나 삭제(메타 + 바이너리). 없는 작품=None(404), 생성 중=False(423),
        파일명 미존재=ValueError(라우트 404). 적용 중이던 표지를 지우면 최신본으로 적용 이전(없으면 None).
        반환={"covers":[...], "applied":...}."""
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        if not sess.lock.acquire(blocking=False):            # 생성 중이면 즉시 False(423)
            return False
        try:
            state = self.repo.get(pid)                       # 권위 재읽기
            if not state:
                return None
            sess = self._resolve_locked_session(sess, state)
            self._ensure_cover_gallery(state)
            target = next((c for c in state.covers if c.filename == filename), None)
            if target is None:
                raise ValueError(f"표지를 찾을 수 없습니다: {filename}")
            state.covers = [c for c in state.covers if c.filename != filename]
            if state.cover is not None and state.cover.filename == filename:
                state.cover = state.covers[-1] if state.covers else None   # 적용본 삭제 → 최신본 폴백(없으면 해제)
            self.repo.delete_cover_file(pid, filename)
            self.repo.save(state)
            return {"covers": [c.model_dump() for c in state.covers],
                    "applied": (state.cover.filename if state.cover else None)}
        finally:
            sess.lock.release()

    def generate_cover(self, pid: str, prompt_override: str | None = None,
                       include_title: bool = True) -> dict | None:
        """설정·주인공 → 프롬프트 합성(오버라이드 시 스킵) → 이미지 1콜 → PNG 저장 + CoverMeta 영속.
        CV-2: 덮어쓰기 대신 보관함(state.covers)에 append, 새 메타를 즉시 state.cover 로 적용(기존 UX 유지).
        CV-4: include_title(기본 True)=한글 제목 타이포 포함 합성(오버라이드 프롬프트에는 무개입).

        반환: 적용 메타 dict / 없는 프로젝트=None(라우트 404) / 중복 생성·회차 생성 중=False(라우트 423).
        락 규율(revise 계보): LLM·이미지 콜은 sess.lock 밖. sess.lock 은 (1) 스냅샷 (2) 최종 저장만 짧게.
        중복 생성은 _cover_pids 로 차단(같은 작품 표지 2콜 동시 진입 방지). 실패 시 저장·메타 무변경 + 예외 전파(P-2)."""
        override = (prompt_override or "").strip() or None
        state = self.repo.get(pid)
        if not state:
            return None
        # ── 중복 생성 락 획득(짧은 임계구역) — 이미 진행 중이면 즉시 False(423)
        with self._cover_lock:
            if pid in self._cover_pids:
                return False
            self._cover_pids.add(pid)
        try:
            sess = self.sessions.get_or_create(state)
            # ── (1) 스냅샷 — 회차 생성 중이면 즉시 False(423, lost-update 방지). world 만 읽고 곧 해제.
            if not sess.lock.acquire(blocking=False):
                return False
            try:
                state = self.repo.get(pid)         # 권위 재읽기
                if not state:
                    return None
                sess = self._resolve_locked_session(sess, state)
                world = state.world
                before_usage = self.wg_provider.usage.as_dict()
            finally:
                sess.lock.release()                # ── LLM/이미지 콜 전에 해제(생성 스레드 블로킹 방지)
            # ── (2) 프롬프트 합성(오버라이드면 스킵) — wg_provider 1콜(락 밖)
            from ..worldgen.cover_prompt import synthesize_cover_prompt
            prompt = override or synthesize_cover_prompt(self.wg_provider, world,
                                                         include_title=include_title)
            # ── (3) 이미지 1콜(락 밖) — 실패 시 예외 전파(저장·메타 무변경)
            img = self.image_client
            calls_before = img.image_calls
            png = img.generate_png(prompt, model=self.settings.image_model,
                                   size=self.settings.cover_size, quality=self.settings.cover_quality)
            image_calls = img.image_calls - calls_before
            # ── (4) 파일 저장 + 메타/usage 영속 — sess.lock 재진입(짧게). 성공 후에만 상태 변경.
            #   CV-2: 덮어쓰기 아님 — 회차별 타임스탬프 파일로 저장하고 보관함(covers)에 append. 파일명 결정은
            #   보관함 상태(마이그레이션 후)에 의존하므로 락 안에서 수행(같은 초 재생성 충돌 시 -N suffix).
            with sess.lock:
                state = self.repo.get(pid)         # 권위 재읽기(콜 사이 변경 흡수)
                if not state:
                    return None
                sess = self._resolve_locked_session(sess, state)
                self._ensure_cover_gallery(state)  # 구 CV-1 적용본을 보관함으로 승격(멱등)
                ts = time.strftime("%Y%m%d%H%M%S")
                existing = {c.filename for c in state.covers}
                filename = f"{pid}.cover.{ts}.png"
                n = 2
                while filename in existing:        # 같은 초 재생성 대비 suffix(파일 유실 방지)
                    filename = f"{pid}.cover.{ts}-{n}.png"
                    n += 1
                self.repo.save_cover_bytes(pid, png, filename=filename)
                new_meta = CoverMeta(filename=filename, prompt=prompt,
                                     model=self.settings.image_model, size=self.settings.cover_size,
                                     created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
                state.covers.append(new_meta)      # 보관함 적재(이력 = 보관함 그 자체 — history push 없음)
                state.cover = new_meta             # 즉시 적용(기존 UX 유지)
                # usage_total 에 image_calls + 합성 chat 콜을 additive 계상(정직 계측)
                chat_delta = _usage_delta(before_usage, self.wg_provider.usage.as_dict())
                delta = dict(chat_delta)
                delta["image_calls"] = image_calls
                state.usage_total = _accumulate(state.usage_total, delta)
                self.repo.save(state)
                return state.cover.model_dump()
        finally:
            with self._cover_lock:
                self._cover_pids.discard(pid)

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
            sess = self._resolve_locked_session(sess, state)
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
            # T5-R3: 작가가 직접 추가한 엔티티는 '작가 확정' 행위 → confirmed(provisional=False)로 등록(AI 자동커밋만 provisional).
            #   copilot.py 의 미구현 promote 약속을 작가-추가 경로에서 이행 — 확정 표기고정·캐논 단정 슬롯을 부여.
            ont.add(Entity(id=sid, name=name, etype=etype, attrs={}, aliases=clean_aliases,
                           provisional=False))
            state.runtime_entities.append(EntitySpec(id=sid, name=name, etype=etype,
                                                      aliases=clean_aliases, attrs={}, provisional=False))
            sess.snapshot_into(state)
            self.repo.save(state)
            # FI-1: 캐논 정정 표면(엔티티 추가 — 작가 확정). 생성 브랜치만 emit(이미 존재하면 위에서 early return, 무변경).
            emit_intent(self.repo, self.settings, pid, 0, "entity_add",
                        payload={"approx_chapter": state.current_chapter, "id": sid, "name": name, "etype": etype})
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
            sess = self._resolve_locked_session(sess, st)
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
            # FI-1: 캐논 정정 표면(관계 추가). 생성 브랜치만 emit(이미 존재=early return, 모순=raise). `state` 는 관계 상태 문자열.
            emit_intent(self.repo, self.settings, pid, 0, "relation_add",
                        payload={"approx_chapter": st.current_chapter, "edge_id": edge_id, "rel_id": rel_id,
                                 "src_id": src_id, "dst_id": dst_id, "role": role or "", "state": state or "",
                                 "eff_from": eff, "reason": trim_text(reason or "")})
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
            sess = self._resolve_locked_session(sess, state)
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
            # FI-1: 캐논 정정 표면(관계 종료 — 배신/탈퇴/이동).
            emit_intent(self.repo, self.settings, pid, 0, "relation_end",
                        payload={"approx_chapter": state.current_chapter, "edge_id": target.edge_id,
                                 "rel_id": rel_id, "src_id": src_id, "dst_id": dst_id, "eff_to": eff_to})
            return {"edge_id": target.edge_id, "eff_to": eff_to, "ended": True}

    def get_session(self, pid: str):
        """SSE 구독용 — 세션을 미리 보장(같은 bus 를 generate 가 재사용)."""
        state = self.repo.get(pid)
        if not state:
            return None, None
        return self.sessions.get_or_create(state), state

    # ---- 회차 생성 잡(연결 끊겨도 백그라운드 진행 · 재접속 리플레이) ----
    def readiness(self, pid: str) -> dict | None:
        """T6: 회차 집필 진입 전 준비도 advisory(결정론·LLM0·비차단·읽기전용). 생성을 막지 않는다 — 작가 가시화만."""
        state = self.repo.get(pid)
        if not state:
            return None
        from ..engine.readiness import chapter_readiness, stale_derivatives, wiki_fingerprint_mismatches
        rep = chapter_readiness(state)
        # XR-7①: '퇴고 반영 안 된 파생물' 사전 점검(additive 키 — 구 클라이언트 무영향·게이트 아님).
        #   목록만 준다: 재계산은 작가가 버튼으로 발동하는 recompute_derivative 뿐이다(자동 LLM 콜 0).
        rep["stale_derivatives"] = stale_derivatives(state)
        # XR-18: 위키 반영-지문 불일치(표식 우회 변경의 이중 방어 — 있으면 생성 게이트가 위키를 격리).
        #   영속 페이지 기준(디스크=권위 — 세션 생성 없이 결정론 조회). 기록 없는 구 데이터는 빈 목록(하위호환).
        rep["wiki_fingerprint_mismatch"] = wiki_fingerprint_mismatches(
            state, getattr(state, "wiki_pages", None) or [])
        return rep

    # ---- EC-1: 엔딩 술어계약 감시(결정론·LLM 0콜·advisory — 어떤 것도 차단하지 않음) ----
    def _ec_eval(self, state, sess, chapter: int):
        """계약 결정론 평가(gt/ni 병렬 — LR-1 arm1). 계약 없으면 None. 예외는 호출부가 흡수(무강제)."""
        contract = state.ending_contract
        if not contract.predicates:
            return None
        from ..engine.ending_contract import evaluate_contract
        deltas = [c.time_delta for c in sorted(state.chapters, key=lambda c: c.chapter)
                  if c.status == ChapterStatus.FINALIZED]
        return evaluate_contract(contract, sess.bundle.ontology, state.promise_ledger,
                                 deltas, max(1, chapter), bool(state.world.allow_state_reversal))

    def _ec_settle(self, state, sess, ev: dict, trigger: str) -> None:
        """완결(아크 소진 등) 시점 정산 스냅샷 영속 + advisory 이벤트 — 완결 차단·자동 에필로그 0(표시만)."""
        from ..engine.ending_contract import settlement_snapshot
        snap = settlement_snapshot(ev, trigger)
        state.ending_contract.settlement = snap
        event = "contract_unsettled" if snap["unsettled_gt"] else "contract_settled"
        sess.bus.emit("ending_contract", event, chapter=snap["chapter"], trigger=trigger,
                      unsettled_gt=snap["unsettled_gt"], open_gt=snap["open_gt"],
                      blocked_gt=snap["blocked_gt"], satisfied_gt=snap["satisfied_gt"],
                      promotion_hints=snap["promotion_hints"])

    def _ec_after_finalize(self, state, sess, record, chapter: int) -> None:
        """FINALIZED 회차마다 계약 평가(LLM 0콜) → ChapterRecord 영속 + bus advisory.
        아크 전량 소진 시 미정산이면 '미정산 엔딩' advisory. 예외는 전량 흡수 — 회차 확정을 절대 막지 않는다."""
        try:
            ev = self._ec_eval(state, sess, chapter)
            if ev is None:
                return
            record.ending_contract_eval = ev
            g, n = ev["tiers"]["ground_truth_only"], ev["tiers"]["with_narrative_inferred"]
            sess.bus.emit("ending_contract", "contract_eval", chapter=chapter,
                          total=len(ev["predicates"]), gt_satisfied=g["satisfied"],
                          gt_open=g["open"], gt_blocked=g["blocked"],
                          ni_satisfied=n["satisfied"], promotion_hints=ev["promotion_hints"])
            spine = state.world.spine
            arcs = spine.arcs if spine else []
            # H1(적대검증): spine 은 lazy 분해다 — build_spine 이 '첫 아크만' 에피소드로 분해하고(n_arcs>=2)
            #  후속 아크는 episodes=[] 로 두었다가 진행하며 채운다(arc_planner). 미분해(빈) 아크가 하나라도 있으면
            #  '연재 중'이지 아크 소진이 아니다 — 전 아크가 실체화되고 전부 done 일 때만 정산(가짜 중간 정산이
            #  _ec_on_complete 의 진짜 hard_cap/ending_reached 정산을 선점 차단하던 오탐의 소스 차단).
            #  (마지막 아크 분해 실패로 빈 채 완결되는 경로는 _ec_on_complete(ending_reached)가 정산한다.)
            if arcs and all(a.episodes and all(e.done for e in a.episodes) for a in arcs):
                self._ec_settle(state, sess, ev, trigger="arcs_exhausted")
        except Exception:
            pass                                                  # advisory — 생성 흐름 절대 비차단

    def _ec_on_complete(self, state, sess, trigger: str) -> None:
        """완결 전이(ending_reached/hard_cap) 시 정산 — 이미 기록됐으면 보존(멱등). 예외 전량 흡수."""
        try:
            if state.ending_contract.settlement:                  # 아크 소진 시점 스냅샷이 권위(첫 기록 보존)
                return
            ev = self._ec_eval(state, sess, state.current_chapter)
            if ev is not None:
                self._ec_settle(state, sess, ev, trigger)
        except Exception:
            pass

    def ending_contract_status(self, pid: str) -> dict | None:
        """EC-1 GET: 계약 현황(LLM 0콜·읽기 전용). gt/ni 병렬 — 단일 판정 없음(arm1).
        satisfied 는 SSOT '상태 기준'이며 지면(프로즈) 실현 보장이 아니다(arm2 — notes 로 함께 노출).
        미표현 요소(arm3)·완결 정산 스냅샷·stale(엔딩 개정 후 미갱신) 여부 포함."""
        state = self.repo.get(pid)
        if not state:
            return None
        from ..engine.ending_contract import ending_fingerprint, CONTRACT_NOTES
        c = state.ending_contract
        ending = (state.world.spine.ending.model_dump()
                  if state.world.spine and state.world.spine.ending else {})
        has = bool(c.predicates or c.unexpressed)
        out = {
            "has_contract": has, "error": c.error, "source": c.source,
            "compiled_at": c.compiled_at, "compiled_chapter": c.compiled_chapter,
            "stale": bool(has and c.ending_fingerprint
                          and c.ending_fingerprint != ending_fingerprint(ending)),
            "unexpressed": [u.model_dump() for u in c.unexpressed],
            "settlement": dict(c.settlement),
            "notes": list(CONTRACT_NOTES),
            "predicates": [], "tiers": None, "settled": None, "promotion_hints": 0,
            "eval_chapter": max(1, state.current_chapter),
        }
        if c.predicates:
            sess = self.sessions.get_or_create(state)             # 라이브 온톨로지 재사용(읽기 전용·LLM 0콜)
            ev = self._ec_eval(state, sess, state.current_chapter)
            if ev is not None:
                out.update(predicates=ev["predicates"], tiers=ev["tiers"], settled=ev["settled"],
                           promotion_hints=ev["promotion_hints"], eval_chapter=ev["chapter"])
        return out

    def start_generation(self, pid: str, directive_text: str | None = None,
                         fix_instruction: str | None = None) -> tuple[GenerationJob, bool] | None:
        """진행 중 잡이 있으면 그대로 반환(멱등 — 새로고침/중복요청이 회차를 두 번 만들지 않음).
        없으면 새 잡을 띄워 백그라운드 데몬 스레드에서 generate_next_chapter 를 끝까지 수행한다.
        반환=(job, created) — created=False 면 기존 진행 잡에 '합류'(이번 요청의 directive 는 반영 안 됨).
        fix_instruction: '점검 반영 재생성'의 1회성 교정 지시(영구 directive 아님)."""
        sess, state = self.get_session(pid)
        if not sess:
            return None
        chapter = state.current_chapter + 1
        directive = (directive_text or "").strip()

        def runner(job: GenerationJob) -> None:
            unsub = sess.bus.subscribe(lambda e: job.append_event(e))   # 하네스 이벤트 → 잡 버퍼(리플레이용)
            try:
                result = self.generate_next_chapter(pid, directive or None, fix_instruction=fix_instruction)
            finally:
                unsub()     # 종료 설정 '전에' 구독 해제 — 종료 후 늦은 이벤트가 끼어들 창 자체를 없앤다(불변식: status!=running ⇒ events 최종)
            job.set_done(self._gen_payload(result))   # unsub 이후에만 종료 설정(예외 시 _thread_main 이 set_failed)

        return self.gen_jobs.start_or_get(pid, chapter, directive, runner)

    # ---- 마지막 회차 재생성 (반영 전 백업 → 되돌리기 가능) ----
    def _regen_backup_path(self, pid: str):
        d = self.settings.resolved_data_dir() / "regen_backups"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{pid}.json"

    def _regen_snapshot_path(self, pid: str):
        d = self.settings.resolved_data_dir() / "regen_snapshots"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{pid}.json"

    def _save_pregen_snapshot(self, state) -> None:
        """회차 생성 '직전'에 되돌릴 수 있는 *가변 파생필드*를 경량 스냅샷한다(무거운 rag 임베딩·chapters 본문 제외).
        in-place 변이되는 것(world: introduced/spine done·beats / bible.entries / runtime_* / narrative_progress /
        promise_ledger / wiki)을 모두 담아, 마지막 회차 재생성 시 '회차 직전' 상태로 *완전* 복원할 수 있게 한다."""
        d = state.model_dump()
        # regen_events(IN-12)는 파생 서사상태가 아니라 append-only 계측 로그 — 스냅샷 복원이 로그를 되감으면 안 됨(제외)
        for k in ("rag_chunks", "chapters", "has_regen_backup", "regen_events"):
            d.pop(k, None)
        try:
            self._regen_snapshot_path(state.id).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass   # 스냅샷 실패가 생성을 막지 않게(되돌림은 폴백 표면복원으로 degrade)

    @staticmethod
    def _augment_fix_with_gate(chN, fix_instruction: str | None) -> str | None:
        """PR-1 ③(감사 A-2): 마지막 회차의 verification.gate 가 있으면 재생성 fix 지시에 게이트 drop_trigger
        인용·국소 앵커 우선 규율을 덧붙인다(게이트 결과 있을 때만 — 없으면 원본 그대로 반환=바이트 동일).

        무강제: '이 대목을 참고해 다듬어라'는 참고 지시일 뿐(강제·자동 재작성 아님). 작가 fix 가 이미 있으면
        그 앞에 게이트 근거를 덧대고, 없으면 게이트 근거만으로 1회성 지시를 만든다. drop_trigger='없음'이거나
        게이트가 PASS 로 지적이 없으면(가장 흔함) 덧대지 않는다(불필요한 지시 주입 금지)."""
        gate = None
        ver = getattr(chN, "verification", None)
        if isinstance(ver, dict):
            g = ver.get("gate")
            if isinstance(g, dict):
                gate = g
        if not gate:
            return fix_instruction
        drop = (gate.get("drop_trigger") or "").strip().strip("“”\"'").strip()
        note = (gate.get("fix_note") or "").strip()
        if (not drop or drop in ("없음", "none", "None")) and not note:
            return fix_instruction   # 게이트가 짚은 대목이 없음(PASS·지적 없음) → 지시 무증강
        parts = []
        if drop and drop not in ("없음", "none", "None"):
            parts.append(f"정독 게이트가 이 대목에서 이탈감을 느꼈다: “{drop}”. "   # 절단 전면 제거(2026-08-21): 게이트 인용 전문
                         "그 대목(과 앞뒤 문단)을 우선 국소로 손보되, 나머지 흐름은 최대한 유지하라"
                         "(장면·사건·설정을 갈아엎지 말고 그 지점의 리듬·묘사만 다듬는다).")
        if note:
            parts.append(f"게이트 수정 메모: {note}")   # 절단 전면 제거(2026-08-21)
        gate_directive = " ".join(parts)
        base = (fix_instruction or "").strip()
        if not base:
            return gate_directive
        # 작가 지시 우선(앞), 게이트 근거는 참고로 뒤에 — 작가 의도를 게이트가 덮지 않게.
        return f"{base}\n[게이트 참고] {gate_directive}"

    def regenerate_last_chapter(self, pid: str, fix_instruction: str | None = None):
        """마지막 회차만 재생성. 반영 전 전체 상태를 백업(되돌리기용)하고 '회차 직전' 스냅샷으로 파생상태를
        완전 복원한 뒤, 기존 생성 잡으로 그 회차를 다시 뽑는다. *마지막 회차 한정*이라 고아(orphan) 0.
        fix_instruction: '점검 반영 재생성'의 1회성 교정 지시(선택). 반환=(job, created) | None."""
        job = self.gen_jobs.get(pid)
        if job is not None and job.status == "running":     # 생성 진행 중엔 거부(동시 변이/lost-update 방지)
            return None
        state = self.repo.get(pid)
        if not state or not state.chapters:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)                       # 권위 재읽기(lock 안 — lost update 방지)
            if not state or not state.chapters:
                return None
            sess = self._resolve_locked_session(sess, state)
            chN = max(state.chapters, key=lambda c: c.chapter)
            N = chN.chapter
            # PR-1 ③(감사 A-2): 제품 게이트 결과가 있으면 재생성 fix directive 에 게이트 drop_trigger(정독이 실제로
            #   창을 닫은 대목의 본문 인용)를 인용하고 '그 대목을 국소로 먼저 손보라'는 앵커 우선 규율을 이관한다.
            #   러너의 국소 앵커 우선(스팬 ±1 창만 손보고 나머지 불변) 정신을 웹 재생성 지시로 옮긴 것 — 게이트
            #   결과가 있을 때만(없으면 no-op → 기존 fix_instruction 바이트 동일). 무강제: 지시일 뿐 강제 아님.
            fix_instruction = self._augment_fix_with_gate(chN, fix_instruction)
            # IN-12: 재생성 실행 이벤트 영속 — 순수 계측(LLM0·결정론. 분석·판정·자동반응 0).
            #  백업 dump '전에' append → '실행했다'는 사실은 되돌리기(undo)로도 안 지워진다(백업에 포함돼 복원 후 유지).
            #  아래 2)의 스냅샷 복원은 regen_events 를 안 담으므로(_save_pregen_snapshot 제외) 로그를 되감지 않는다.
            state.regen_events.append(RegenEvent(
                chapter=N, seq=1 + sum(1 for e in state.regen_events if e.chapter == N),
                fix_selected=bool(fix_instruction and fix_instruction.strip()),   # FE-2 fix 그룹 선택 여부(공백=FE-1 취급 — generate 의 strip 게이트와 동일 기준)
                at=time.strftime("%Y-%m-%dT%H:%M:%S")))
            # 1) 반영 전 전체 백업(되돌리기) — 별도 파일
            self._regen_backup_path(pid).write_text(
                json.dumps(state.model_dump(), ensure_ascii=False), encoding="utf-8")
            # 2) ch N '직전' 스냅샷이 있으면 그것으로 가변 파생필드 *완전* 복원(정확). rag/chapters 는 스냅샷에 없으니
            #    현재 값 유지(아래서 truncate/필터). 없으면(구 회차) 표면 spine/progress 복원으로 degrade.
            snap_p = self._regen_snapshot_path(pid)
            snap = None
            if snap_p.exists():
                try:
                    snap = json.loads(snap_p.read_text(encoding="utf-8"))
                    cur = state.model_dump()
                    cur.update(snap)                         # snap=rag/chapters 제외 → 현재 rag/chapters 유지, 나머지 pre-chN
                    state = ProjectState.model_validate(cur)
                except Exception:
                    snap = None
            if snap is None:                                 # 폴백(스냅샷 없는 구 회차): 표면 복원 + eff_from 필터
                sp = state.world.spine
                if sp and chN.episode_id:
                    tarc, tep = sp.arc(chN.arc_id), sp.episode(chN.arc_id, chN.episode_id)
                    if tarc and tep:
                        for arc in sp.arcs:
                            if arc.order >= tarc.order:
                                arc.done = False
                                for ep in arc.episodes:
                                    if arc.order > tarc.order or ep.order >= tep.order:
                                        ep.done = False
                        state.narrative_progress.current_arc_id = chN.arc_id
                        state.narrative_progress.current_episode_id = chN.episode_id
                        state.narrative_progress.chapters_in_episode = sum(
                            1 for c in state.chapters if c.episode_id == chN.episode_id and c.chapter < N)
                state.runtime_timeline = [t for t in state.runtime_timeline if getattr(t, "eff_from", 0) <= N]
                state.runtime_edges = [e for e in state.runtime_edges if getattr(e, "eff_from", 0) < N]
            # 3) 항상: 마지막 회차 truncate + rag 샤드 필터(스냅샷이 안 담는 무거운 둘)
            state.chapters = [c for c in state.chapters if c.chapter < N]
            state.current_chapter = max((c.chapter for c in state.chapters), default=0)
            state.rag_chunks = [c for c in state.rag_chunks if c.chapter < N]
            state.narrative_progress.completed = False
            state.has_regen_backup = True
            self.repo.save(state)
        # 4) 세션 evict → start_generation 이 되돌린 상태에서 fresh 재수화 후 ch N 재생성(+1회성 교정 지시)
        self.sessions.evict(pid)
        return self.start_generation(pid, fix_instruction=fix_instruction)

    def restore_last_regen(self, pid: str) -> dict | None:
        """마지막 재생성 '되돌리기' — 반영 전 백업(전체 상태)으로 복원하고 백업을 소거. lock 으로 동시 생성과 직렬화."""
        p = self._regen_backup_path(pid)
        if not p.exists():
            return None
        state = self.repo.get(pid)
        if state is None:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            if not p.exists():
                return None
            state = self.repo.get(pid)
            if not state:
                return None
            sess = self._resolve_locked_session(sess, state)
            try:
                restored = ProjectState.model_validate(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                return None
            restored.has_regen_backup = False
            self.repo.save(restored)
        self.sessions.evict(pid)
        p.unlink(missing_ok=True)
        return {"restored": True, "current_chapter": restored.current_chapter,
                "chapters": len(restored.chapters)}

    def get_generation_job(self, pid: str) -> GenerationJob | None:
        return self.gen_jobs.get(pid)

    def generation_status(self, pid: str) -> dict:
        """페이지 로드/폴링용 — 진행 중인 생성이 있는지, 끝났으면 결과까지."""
        job = self.gen_jobs.get(pid)
        if job is None:
            return {"status": "idle"}
        return job.status_view()

    @staticmethod
    def _gen_payload(result: dict) -> dict:
        """generate_next_chapter 결과 → SSE 'complete' 페이로드(기존 라우트와 동일 스키마)."""
        if result.get("completed"):
            return {"completed": True, "reason": result.get("reason"),
                    "current_chapter": result.get("current_chapter"),
                    "total_beats": result.get("total_beats")}
        return {"completed": False, "record": result["record"].model_dump(),
                "usage_delta": result["usage_delta"], "usage_total": result["usage_total"],
                "failures": result["failures"], "current_chapter": result["current_chapter"],
                "total_beats": result["total_beats"],
                "events": result.get("events", [])}   # 아크완결 회고 nudge 등 onComplete 전용 배너용(스키마 보존)

    # ---- 회차 생성(핵심 유스케이스) ----
    @staticmethod
    def _leak_sources_for(state, chapter_no: int) -> dict:
        """VL-1: 사후 누출 스윕 소스 풀 — {이름: (원문, 대조 범위)}. 지문 누출(카드·서술자
        음성)은 본문 전체, 설정 언어의 대사 직역(설정집·확정 스토리)은 따옴표 스팬만."""
        src: dict = {}
        nv = (getattr(getattr(state.world, "style", None), "narrator_voice", "") or "").strip()
        if nv:
            src["narrator_voice"] = (nv, "body")
        for e in (state.world.entities or []):
            t = ((getattr(e, "voice", "") or "") + " "
                 + " ".join((getattr(e, "voice_stages", None) or {}).values())).strip()
            if t:
                src[f"voice:{e.name}"] = (t, "body")
        bp = "\n".join((getattr(b, "prose", "") or "") for b in state.bible.entries)
        if bp.strip():
            src["bible"] = (bp, "quotes")
        sp = next((r.confirmed_story for r in (getattr(state, "story_passes", None) or [])
                   if r.chapter == chapter_no and r.status == "confirmed"), "")
        if sp:
            src["confirmed_story"] = (sp, "quotes")
        return src

    @staticmethod
    def _ending_sources_for(state, record) -> dict:
        """XR-1(cross-review/005 §2.1): 결말 누출 스윕 재료 — needles(작품 데이터 정본 필드 파생만)와
        targets(이 회차 집필 체인에 실제로 들어간 입력 조립물 스냅샷). 스토리 패스 '재료'는 회차에 미영속이라
        여기 대상 밖 — 그 채널은 조립 시점 결정론 가드(engine/story_pass.assemble_materials)가 맡는다.
        결측 정직: gen_context 없는 구 회차·결말 미설정 작품은 빈 dict → 축 MISSING."""
        needles: dict = {}
        end = getattr(getattr(state.world, "spine", None), "ending", None)
        for f in ("ending", "thematic_payoff", "central_question"):
            v = ((getattr(end, f, "") or "").strip() if end else "")
            if v:
                needles[f"spine.ending.{f}"] = v
        if (state.world.synopsis or "").strip():
            needles["world.synopsis"] = state.world.synopsis
        targets: dict = {}
        gc = getattr(record, "gen_context", None)
        if isinstance(gc, dict):
            d = gc.get("draft") if isinstance(gc.get("draft"), dict) else {}
            parts = [d.get("persona") or "", d.get("author_style") or "",
                     d.get("story_so_far") or "", d.get("voice_roster") or ""]
            parts += [(a.get("text") or "") for a in (d.get("anchors") or []) if isinstance(a, dict)]
            parts += [str(x) for x in (d.get("ground_truth") or [])]
            b = d.get("beat") if isinstance(d.get("beat"), dict) else {}
            parts += [str(b.get("title") or ""), str(b.get("summary") or ""),
                      " ".join(str(k) for k in (b.get("key_events") or []))]
            t = "\n".join(p for p in parts if p.strip())
            if t:
                targets["draft_inputs"] = t
            p = gc.get("plan") if isinstance(gc.get("plan"), dict) else {}
            parts = [p.get("cast_context") or "", p.get("plant_notes") or ""]
            parts += [str(x) for x in (p.get("event_menu") or [])]
            parts += [str(x) for x in (p.get("recent") or [])]
            t = "\n".join(x for x in parts if x.strip())
            if t:
                targets["plan_inputs"] = t
            spx = gc.get("story_pass") if isinstance(gc.get("story_pass"), dict) else {}
            if (spx.get("story") or "").strip():
                targets["confirmed_story"] = spx["story"]
        if not needles or not targets:
            return {}
        return {"needles": needles, "targets": targets}

    # ---- SY-1 스토리 패스(설계 docs/design-sy1-story-pass-wiring.md §1-C·§6) ----
    def _confirmed_story_for(self, state, n: int) -> str:
        """status=='confirmed' 인 회차 레코드만 스토리를 반환 — 그 외 전부 ""(자동 승격 0 계약)."""
        rec = next((r for r in (getattr(state, "story_passes", None) or []) if r.chapter == n), None)
        if rec is None or rec.status != "confirmed":
            return ""
        return rec.confirmed_story or ""

    def _story_pass_record(self, state, n: int):
        return next((r for r in (getattr(state, "story_passes", None) or []) if r.chapter == n), None)

    def run_story_pass(self, pid: str, chapter: int | None = None, pillar: str | None = None,
                       role: str | None = None) -> dict:
        """깔때기 실행 → candidate 영속(승격 없음 — 확정은 confirm_story 만).
        SP-2: role 은 작가 오버라이드 — 미지정이면 배정기(최근 역할 제외 로테이션)가 정한다."""
        from ..engine.story_pass import StoryPassDeps, run_funnel
        from ..domain.types import StoryPassRecord
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            judge = create_role_provider(self.settings, self.settings.style_judge_model)
            # 스토리 패스 gen 라우팅(사용자 결정 2026-08-23): 서사 설계 콜(후보·병합·서식수리)은 story_pass_model
            #   (기본 opus-4-8 — planning 동일 계열)로. ""=스왑 0(sess.provider — 종전 경로 바이트 동일).
            _sp_spec = (getattr(self.settings, "story_pass_model", "") or "").strip()
            gen = create_role_provider(self.settings, _sp_spec) if _sp_spec else sess.provider
            deps = StoryPassDeps(gen=gen, judge=judge, bus=sess.bus)
            before = sess.provider.usage.as_dict()
            # CE-3: 심사 콜(judge — cross-vendor 별도 프로바이더)은 sess.provider 델타에 안 잡힌다 —
            #   리뷰·심문·쌍대·스캔·훅 검사 전부 judge 로 돌므로 이 델타 누락이 미계상의 절반이었다.
            #   gen 이 role provider 로 스왑된 경우도 동형 — 별도 델타로 계상한다.
            judge_before = judge.usage.as_dict() if judge is not sess.provider else None
            gen_before = gen.usage.as_dict() if gen is not sess.provider else None
            ending = getattr(state.world.style, "ending_hook", "") or ""
            res = run_funnel(deps, state, chapter, pillar=pillar, ending_hook=ending, role=role)
            n = res["materials"]["chapter_no"]
            rec = StoryPassRecord(chapter=n, status="candidate",
                                  variants=res["variants"],
                                  checks={"audits": res["audits"], "pairwise": res["pairwise"],
                                          "pillar": res["pillar"], "grafts": res["grafts"],
                                          "role": res["role"], "events": res["events"]},
                                  materials_digest=res["materials"]["digest"],
                                  created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                                  trace_ref=f"story_pass/ch{n}")
            # SP-2 감사 m4ⓑ: 아크 계획의 회차 라벨과 배정 역할이 어긋나면 advisory 이벤트만(무강제).
            _role_fn = {"위기": "escalation", "꼼수": "payoff", "회수": "payoff",
                        "조사": "setup", "관계": "relation", "휴지": "respite"}
            _planned_fn = ""
            _ch = next((c for c in state.chapters if getattr(c, "chapter", None) == n), None)
            if _ch is not None:
                _planned_fn = (getattr(_ch, "chapter_function", "") or "").strip().lower()
            if _planned_fn and _role_fn.get(res["role"], "") != _planned_fn:
                sess.bus.emit("story_pass", "role_function_mismatch", chapter=n,
                              role=res["role"], planned=_planned_fn)
            # 회차당 1레코드(§2) — 이전 레코드는 교체, 전체 이력은 사이드카 트레이스가 보관
            state.story_passes = [r for r in state.story_passes if r.chapter != n] + [rec]
            try:
                self.repo.save_trace(pid, n, {"kind": "story_pass", "digest": rec.materials_digest,
                                              **{k: res[k] for k in ("candidates", "finals", "reviews",
                                                                     "audits", "pairwise", "pillar",
                                                                     "grafts", "variants",
                                                                     "role", "events")}},
                                     kind="story_pass")
            except Exception:
                pass
            _u = _usage_delta(before, sess.provider.usage.as_dict())
            if judge_before is not None:
                _u = _accumulate(_u, _usage_delta(judge_before, judge.usage.as_dict()))
            if gen_before is not None:
                _u = _accumulate(_u, _usage_delta(gen_before, gen.usage.as_dict()))
            # PM 검수(SY-1 kill ⓔ): 깔때기 토큰을 레코드에 영속 — 측정기 없는 kill 기준은 발동 불능
            rec.checks["usage"] = _u
            # CE-3(2026-08-18): 깔때기 토큰을 전역 총비용에도 계상 — 레코드에만 영속되고 usage_total 에
            #   안 잡혀 화당 비용 추정·결재 게이트가 깔때기 몫(자율 런 실측 1.14M)을 통째로 빼먹던 구멍.
            state.usage_total = _accumulate(state.usage_total, _u)
            sess.bus.emit("story_pass", "funnel_done", chapter=n,
                          tokens=_u.get("chat_tokens", 0), variants=len(rec.variants))
            self.repo.save(state)
            return {"chapter": n, "pillar": res["pillar"], "variants": rec.variants,
                    "checks": rec.checks, "materials_digest": rec.materials_digest,
                    "usage": _u}

    def _story_inject_check(self, state, sess, next_ch: int, story: str) -> dict:
        """SY-1 §5-bis ⓐ: 주입 직전 확정 스토리 재검사(fmt·BAN) — emit·차단 0(가시화만·line_budget 인자 M3).
        작가 확정분과 RC-2 auto 확정분이 같은 검사를 공유한다(단일 출처). 반환 {fmt_errs, bans}."""
        from ..engine import story_pass_prompts as _spp_chk
        _sp_rec = self._story_pass_record(state, next_ch)
        _b = {"lo": 12, "hi": 20}
        if _sp_rec:
            _b = (_sp_rec.checks or {}).get("line_budget") or next(
                (v.get("line_budget") for v in (_sp_rec.variants or [])
                 if isinstance(v, dict) and isinstance(v.get("line_budget"), dict)), _b)
        _inj_errs = _spp_chk.fmt_check(story, lo=int(_b.get("lo", 12)), hi=int(_b.get("hi", 20)))
        _inj_bans = [b for b in _spp_chk.BAN if b in story]
        if _inj_errs or _inj_bans:
            sess.bus.emit("story_pass", "inject_check", chapter=next_ch,
                          errs=_inj_errs[:4], bans=_inj_bans)
        return {"fmt_errs": _inj_errs, "bans": _inj_bans}

    @staticmethod
    def _select_auto_variant(variants) -> dict | None:
        """auto 승자 선정 — fmt 통과 variant 우선(merged > solo), 없으면 solo, 최후 첫 variant.
        RC-4 재사용 경로와 fresh funnel 경로가 같은 로직을 공유한다(단일 출처). 무강제 폐기 —
        fmt 잔여는 inject_check advisory 로 표면화."""
        variants = variants or []
        for _pref in ("merged", "solo"):
            v = next((x for x in variants if isinstance(x, dict) and x.get("name") == _pref), None)
            if v and not v.get("fmt_errs"):
                return v
        return (next((x for x in variants if isinstance(x, dict) and x.get("name") == "solo"), None)
                or (variants[0] if variants else None))

    def _persist_story_pass_independent(self, state, prog_snap, spine_snap) -> None:
        """RC-2 ⓐ: 깔때기 산출(story_passes)을 회차 커밋과 '독립'으로 디스크에 보존한다 — 저장 시점 명시.
        저장 시 커서/spine 을 current_episode '전진 이전' 스냅샷으로 되돌려 저장하고 즉시 복원한다:
        전진 커서를 회차 append 없이 디스크에 남기지 않아 B-25/CU-1 원자성 불변식과 정합한다. 후속 생성
        실패(_fail_rollback 은 story_passes 를 되돌리지 않고 세션 evict)에도 디스크 재수화로 깔때기 산출이
        보존되고 RC-4 재생성이 그 레코드를 재사용한다. 저장 실패는 흡수(비차단 — 관측이 생성을 막지 않는다)."""
        if prog_snap is None or spine_snap is None:   # 스냅샷 미전달(방어) — 커서 되돌림 없이 그대로 저장
            try:
                self.repo.save(state)
            except Exception:
                pass
            return
        adv_prog, adv_spine = state.narrative_progress, state.world.spine
        try:
            state.narrative_progress, state.world.spine = prog_snap, spine_snap
            self.repo.save(state)
        except Exception:
            pass
        finally:
            state.narrative_progress, state.world.spine = adv_prog, adv_spine

    def _persist_failed_story_pass(self, state, sess, n: int, event: str, reason: str,
                                   prog_snap, spine_snap) -> None:
        """RC-2 ⓒ: 깔때기 실패를 emit(휘발)로만 두지 않고 StoryPassRecord(status='failed'·사유)로 영속한다.
        기존 candidate/confirmed 레코드(작가·auto 재료)는 실패로 덮지 않는다(작가 판본 보호·RC-4 정합) — 그때는
        emit 만. 실패 레코드도 회차 커밋과 독립 영속(B-25 안전)."""
        from ..domain.types import StoryPassRecord
        sess.bus.emit("story_pass", event, chapter=n, reason=reason)
        existing = self._story_pass_record(state, n)
        if existing is not None and existing.status in ("candidate", "confirmed"):
            return
        rec = StoryPassRecord(chapter=n, status="failed",
                              checks={"auto_failure": {"event": event, "reason": reason}},
                              created_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        state.story_passes = [r for r in state.story_passes if r.chapter != n] + [rec]
        self._persist_story_pass_independent(state, prog_snap, spine_snap)

    def _auto_story_pass_locked(self, pid: str, state, sess, next_ch: int,
                                prog_snap=None, spine_snap=None) -> str | None:
        """SP-auto: 생성 락 안에서 깔때기 자동 실행 + 승자 variant 자동 확정. 반환=확정 스토리(실패=None·비트 폴백).
        run_story_pass 와 동형이나 sess.lock 재획득 없이(호출자가 보유) — 재진입 교착 방지. 무강제 폐기·ST-14.

        RC-2: 호출부(generate_next_chapter)가 커서 전진(current_episode)·메뉴 생성 뒤에 부른다 — 에피소드 경계
        첫 화도 새 에피소드 재료(cursor·menu)로 조립된다. prog_snap/spine_snap 은 전진 이전 스냅샷(독립 저장용).
        RC-4: 작가 수동 candidate 가 있으면 깔때기를 다시 돌리지 않고 그 variants 에서 승자만 선정해 확정(콜 0)."""
        from ..engine.story_pass import run_funnel, StoryPassDeps, StoryPassNotReady
        from ..domain.types import StoryPassRecord
        n = next_ch
        # RC-4: 작가 수동 candidate 존재 시 깔때기 재실행 금지 — variants 승자 선정만 재사용해 확정(콜 0·작가 재료 보존).
        #   auto 산 레코드(source='auto')·discarded 는 대상 아님(재생성 경로는 fresh funnel). 작가 명시 discard 만이 판본 폐기.
        _existing = self._story_pass_record(state, n)
        if (_existing is not None and _existing.status == "candidate"
                and _existing.source != "auto" and (_existing.variants or [])):
            _reuse = self._select_auto_variant(_existing.variants)
            if _reuse and (_reuse.get("story") or "").strip():
                _existing.status = "confirmed"
                _existing.confirmed_story = _reuse["story"]
                _existing.source = "auto"
                _existing.confirmed_at = time.strftime("%Y-%m-%d %H:%M:%S")
                _existing.checks = {**(_existing.checks or {}), "auto_variant": _reuse.get("name"),
                                    "auto_reused_candidate": True}
                if isinstance(_reuse.get("line_budget"), dict):
                    _existing.checks["line_budget"] = _reuse["line_budget"]
                self._persist_story_pass_independent(state, prog_snap, spine_snap)   # RC-2: 산출 독립 영속
                sess.bus.emit("story_pass", "auto_confirmed", chapter=n,
                              variant=_reuse.get("name"), reused=True)
                return _reuse["story"]
            # candidate 에 유효 스토리 없음 → fresh funnel 로 폴백(아래)
        try:
            judge = create_role_provider(self.settings, self.settings.style_judge_model)
            _sp_spec = (getattr(self.settings, "story_pass_model", "") or "").strip()
            gen = create_role_provider(self.settings, _sp_spec) if _sp_spec else sess.provider
            deps = StoryPassDeps(gen=gen, judge=judge, bus=sess.bus)
            before = sess.provider.usage.as_dict()
            judge_before = judge.usage.as_dict() if judge is not sess.provider else None
            gen_before = gen.usage.as_dict() if gen is not sess.provider else None
            ending = getattr(state.world.style, "ending_hook", "") or ""
            res = run_funnel(deps, state, next_ch, ending_hook=ending)
        except StoryPassNotReady as e:   # RC-2 ⓒ: 재료 부재 실패 영속(emit 휘발 금지)
            self._persist_failed_story_pass(state, sess, n, "auto_not_ready", str(e)[:200],
                                            prog_snap, spine_snap)
            return None
        except Exception as e:   # 깔때기 실패는 생성을 죽이지 않는다(비트 폴백·결측 정직) — 사유 영속
            self._persist_failed_story_pass(state, sess, n, "auto_failed", str(e)[:200],
                                            prog_snap, spine_snap)
            return None
        chosen = self._select_auto_variant(res.get("variants") or [])
        if not chosen or not (chosen.get("story") or "").strip():
            self._persist_failed_story_pass(state, sess, n, "auto_no_variant",
                                            "유효 variant 부재", prog_snap, spine_snap)
            return None
        n = res["materials"]["chapter_no"]
        rec = StoryPassRecord(chapter=n, status="confirmed",
                              variants=res["variants"],
                              checks={"audits": res["audits"], "pairwise": res["pairwise"],
                                      "pillar": res["pillar"], "grafts": res["grafts"],
                                      "role": res["role"], "events": res["events"],
                                      "line_budget": chosen.get("line_budget"),
                                      "auto_variant": chosen.get("name")},
                              materials_digest=res["materials"]["digest"],
                              created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                              trace_ref=f"story_pass/ch{n}")
        rec.confirmed_story = chosen["story"]
        rec.source = "auto"
        rec.confirmed_at = time.strftime("%Y-%m-%d %H:%M:%S")
        state.story_passes = [r for r in state.story_passes if r.chapter != n] + [rec]
        try:
            self.repo.save_trace(pid, n, {"kind": "story_pass", "digest": rec.materials_digest,
                                          **{k: res[k] for k in ("candidates", "finals", "reviews",
                                                                 "audits", "pairwise", "pillar",
                                                                 "grafts", "variants", "role", "events")}},
                                 kind="story_pass")
        except Exception:
            pass
        _u = _usage_delta(before, sess.provider.usage.as_dict())
        if judge_before is not None:
            _u = _accumulate(_u, _usage_delta(judge_before, judge.usage.as_dict()))
        if gen_before is not None:
            _u = _accumulate(_u, _usage_delta(gen_before, gen.usage.as_dict()))
        rec.checks["usage"] = _u
        state.usage_total = _accumulate(state.usage_total, _u)   # CE-3: 깔때기 몫 전역 총비용 계상(결재 게이트 누락 방지)
        self._persist_story_pass_independent(state, prog_snap, spine_snap)   # RC-2 ⓐ: 산출 독립 영속(후속 실패 시 증발 방지)
        sess.bus.emit("story_pass", "auto_confirmed", chapter=n,
                      variant=chosen.get("name"), tokens=_u.get("chat_tokens", 0))
        return chosen["story"]

    def get_story_pass(self, pid: str, n: int) -> dict:
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        rec = self._story_pass_record(state, n)
        return rec.model_dump() if rec else {}

    def confirm_story(self, pid: str, n: int, story: str, source: str = "solo") -> dict:
        """작가 게이트 — 유일한 승격 경로. 형식(fmt_check)만 강제하고 내용 판정은 하지 않는다(§6)."""
        from ..engine import story_pass_prompts as _spp
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            rec = self._story_pass_record(state, n)
            budget = {"lo": 12, "hi": 20}
            if rec:
                for v in (rec.variants or []):
                    if isinstance(v, dict) and isinstance(v.get("line_budget"), dict):
                        budget = v["line_budget"]
                        break
            errs = _spp.fmt_check(story, lo=int(budget.get("lo", 12)), hi=int(budget.get("hi", 20)))
            if errs:
                return {"ok": False, "errors": errs, "line_budget": budget}
            if rec is None:
                from ..domain.types import StoryPassRecord
                rec = StoryPassRecord(chapter=n, created_at=time.strftime("%Y-%m-%d %H:%M:%S"))
                state.story_passes = state.story_passes + [rec]
            rec.status = "confirmed"
            rec.confirmed_story = story
            rec.source = source
            rec.confirmed_at = time.strftime("%Y-%m-%d %H:%M:%S")
            rec.checks = {**(rec.checks or {}), "confirm_fmt": "통과", "line_budget": budget}
            sess.bus.emit("story_pass", "confirmed", chapter=n, source=source,
                          lines=len(_spp.story_lines(story)))
            self.repo.save(state)
            return {"ok": True, "chapter": n, "source": source,
                    "lines": len(_spp.story_lines(story))}

    def discard_story_pass(self, pid: str, n: int) -> dict:
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            rec = self._story_pass_record(state, n)
            if rec is None:
                return {"ok": False, "reason": "레코드 없음"}
            rec.status = "discarded"
            self.repo.save(state)
            return {"ok": True, "chapter": n}

    @promptlog.stage("story_labels")
    def _beat_from_story_labels(self, state, sess, arc, ep, next_ch: int, is_finale: bool,
                                story: str, time_facts: str):
        """SY-1 §1-D: story 모드 비트 = 설계기가 아니라 라벨 도출기(사건 설계 0·기술만).
        반환 (Beat, time_source_cited). hook_type·closing_device 는 프로즈 속성이라 결측(M-C),
        key_events 는 레코드·필터 층 1줄(라벨 summary — 프롬프트 표면은 harness 가 봉쇄)."""
        from ..engine import story_pass_prompts as _spp
        from ..engine.menu_filter import scene_form_whitelist
        from ..domain.world import Beat, TimeDelta
        try:
            labels = self.planning_provider.chat_json(
                [{"role": "system", "content": _spp.build_label_sys(scene_form_whitelist(None))},
                 {"role": "user", "content": _spp.build_label_user(time_facts or "", story)}],   # 절단 전면 제거(2026-08-21): 시간 팩트 전문(설계 비트 콜과 대칭)
                temperature=0.0)
            if not isinstance(labels, dict):
                labels = {}
        except Exception:
            labels = {}
        _td = None
        td = labels.get("time_delta")
        if isinstance(td, dict) and td.get("unit"):
            try:
                _td = TimeDelta(**{k: td[k] for k in ("amount", "unit", "mode") if k in td})
            except Exception:
                _td = None
        # §5-bis ⓓ: time_source 인용 원문 대조 — 경과를 주장하면서 인용이 대조 실패면 emit(차단 0).
        #   time_delta 는 story_clock 결정론 누적 입력(CN-1 캐논)이라 근거 없는 시간 판단을 보이게 한다.
        _src = (labels.get("time_source") or "").strip()
        _cited = bool(_src) and _spp.cite_ok(_src, story)
        if _td is not None and int(getattr(_td, "amount", 0) or 0) > 0 and not _cited:
            sess.bus.emit("story_pass", "time_source_uncited", chapter=next_ch)
        # 라벨 값의 화이트리스트 완전일치 검사 — 불일치는 "" 강등 + emit(순환 원장에 허구 값 금지)
        _sf = (labels.get("scene_form") or "").strip()
        if _sf and _sf not in set(scene_form_whitelist(None)):
            sess.bus.emit("story_pass", "label_offlist", chapter=next_ch, field="scene_form", value=_sf[:24])
            _sf = ""
        _cf = (labels.get("chapter_function") or "").strip()
        if _cf and _cf not in ("payoff", "setup", "escalation", "relation", "respite"):
            sess.bus.emit("story_pass", "label_offlist", chapter=next_ch, field="chapter_function", value=_cf[:24])
            _cf = ""
        ents = list(sess.bundle.ontology.scan_present_ids(story)) or list(getattr(ep, "required_cast", None) or [])
        _first = next((l for l in _spp.story_lines(story)), "")
        summary1 = (labels.get("summary") or "").strip() or (_first[2:] if _first.startswith("- ") else _first)
        beat = Beat(chapter=next_ch, title=(labels.get("title") or "").strip()[:80], summary=summary1,
                    key_events=[summary1], entities=ents,
                    arc_id=getattr(arc, "arc_id", None), episode_id=getattr(ep, "episode_id", None),
                    is_episode_finale=is_finale, chapter_function=_cf,
                    hook_type="", closing_device="", scene_form=_sf,
                    time_advance=(labels.get("time_advance") or "").strip(), time_delta=_td,
                    place=(labels.get("place") or "").strip(),
                    world_reveal=[w for w in (labels.get("world_reveal") or []) if isinstance(w, str)][:2])
        return beat, _cited

    @staticmethod
    def _fill_draft_lineage(record, ssf_contrib, dropped, prev) -> None:
        """XR-3 — 엔진이 결측(gap)으로 남긴 두 블록을 서비스 계층 값으로 채운다(관측 전용·LLM 0).

        엔진(harness)은 story_so_far 를 '완성된 문자열'로만 받고 직전 회차도 원문 문자열로만 받으므로
        기여 회차·세대(gen_no)·리비전 수를 알 수 없다. 그 결측을 여기서 닫는다. 값이 없으면 gap 을
        그대로 남긴다(침묵 채움 금지 — 결측 정직). 실패는 흡수한다(관측이 생성을 막지 않는다)."""
        try:
            lin = ((getattr(record, "gen_context", None) or {}).get("draft") or {}).get("lineage")
            if not isinstance(lin, dict):
                return
            for b in (lin.get("blocks") or []):
                if b.get("block") == "story_so_far":
                    b["dropped"] = dropped
                    if ssf_contrib is not None:
                        b["contributors"] = ssf_contrib
                        b.pop("gap", None)
                elif b.get("block") == "prev_chapter" and prev is not None:
                    b["gen_no"] = getattr(prev, "gen_no", None)
                    b["revisions"] = len(getattr(prev, "revisions", None) or [])
                    b.pop("gap", None)
        except Exception:
            pass

    def generate_next_chapter(self, pid: str, directive_text: str | None = None,
                              fix_instruction: str | None = None) -> dict:
        state = self.repo.get(pid)
        if not state:
            raise KeyError(pid)
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)        # 권위 재읽기(lock 안에서 — lost update 방지: 동시 bible 편집/promote 가 안 덮임)
            if not state:
                raise KeyError(pid)
            sess = self._resolve_locked_session(sess, state)
            next_ch = state.current_chapter + 1
            spine = state.world.spine
            cap = (state.seed.target_chapters or 12) * 2     # 하드캡: 예산 2배 초과 시 강제 완결(런어웨이 차단)
            # SY-1: 작가 게이트 통과분만 ""가 아니다(자동 승격 0). §5-bis ⓐ 주입 직전 재검사(emit·차단 0)
            #   — 확정 이후 BAN 목록 확장·레코드 외부 편집을 주입 시점에 보이게 한다(line_budget 인자 — M3).
            _story_confirmed = self._confirmed_story_for(state, next_ch)
            # RC-2: SP-auto(깔때기 자동 실행)는 여기(커서 전진 전)가 아니라 spine 브랜치의 커서 전진(current_episode)·
            #   메뉴 생성 뒤에서 돈다 — 에피소드 경계 첫 화도 새 에피소드 재료(cursor·menu)로 조립되게 하고, 산출을
            #   회차 커밋과 독립 영속한다(구 위치는 소진 에피소드 slot=target+1 로 assemble raise → 조용한 비트 폴백·레코드 0).
            #   이 시점 _story_confirmed 는 '작가 확정분'만 — 메뉴 생략 게이트(아래 not _story_confirmed)의 소스가 auto 산출로
            #   오염되지 않는다(auto 는 그 게이트 뒤에서 실행). 실패(재료 부재·예외)는 None → 비트 폴백(결측 정직·생성 비차단).
            _story_cited = None   # 라벨 콜 time_source 대조 결과(story 모드에서만 채워짐 — 스냅샷용)
            _story_inject = {}
            if _story_confirmed:   # 작가 확정분(pre-existing) — auto 확정은 spine 브랜치에서 별도 재검사
                _story_inject = self._story_inject_check(state, sess, next_ch, _story_confirmed)
            elif self._story_pass_record(state, next_ch) is not None:
                # 후보가 있는데 미확정 상태로 생성이 돌면 — 차단하지 않고 가시화만(§6 무강제)
                sess.bus.emit("story_pass", "unconfirmed", chapter=next_ch)

            # R4 완결 종료 — 무한 생성 금지(엔딩 도달 or 하드캡)
            if spine and (state.narrative_progress.completed or state.current_chapter >= cap):
                if not state.narrative_progress.completed:
                    state.narrative_progress.completed = True
                    self._ec_on_complete(state, sess, trigger="hard_cap")   # EC-1: 강제 완결 전이 시 정산 advisory(비차단)
                    self.repo.save(state)
                return {"completed": True,
                        "reason": ("hard_cap" if state.current_chapter >= cap else "ending_reached"),
                        "current_chapter": state.current_chapter,
                        "total_beats": (state.seed.target_chapters or len(state.world.beats)),
                        "usage_total": state.usage_total}

            # 회차 '직전' 파생상태 스냅샷(마지막 회차 재생성 시 완전 되돌림용 — rag/chapters 제외 경량, 매 회차 덮어씀)
            self._save_pregen_snapshot(state)
            if directive_text and not any(
                    d.text == directive_text and d.from_chapter == next_ch for d in state.directives):
                state.directives.append(AuthorDirective(                # #9: 재시도 시 동일 지시 중복 방지
                    directive_id=f"d{len(state.directives) + 1}", text=directive_text, from_chapter=next_ch))

            active = [d for d in state.directives if d.from_chapter <= next_ch]
            if fix_instruction and fix_instruction.strip():   # 점검 반영 재생성: '이번 회차에만' 쓰는 1회성 교정 지시
                active = active + [AuthorDirective(directive_id="__fix_once__",   # state.directives 에 *저장 안 함* → 영구전파 0
                                                   text=fix_instruction.strip(), from_chapter=next_ch)]   # 절단 전면 제거(2026-08-21): 작가 지시 전문
            prior = [c for c in state.chapters if c.chapter < next_ch]
            # 비트 설계용 맥락: 과거=한줄, 직전=상세 시놉시스+말미(클리프행어 인계) — 설계 단계 기아 방지.
            # 회차 식별은 out-of-band 메타 태그(_recent_summaries → _recap_tag)로 — 출판 인덱스 'N화' 미주입(B-34).
            summaries = _recent_summaries(prior)

            arc = ep = None
            is_finale = False
            anchors, bible_dropped = [], 0   # 설정집 다이제스트는 beat 확정 후 '관련 카드 선별'로(아래)
            prog_snap = spine_snap = None
            # B-25: 커서·회차목록 진입 스냅샷 — FINALIZED 블록 '중간' 예외(예: 커서 전진 후 후속 단계 타임아웃)가
            #   메모리에 '전진한 커서 + 미append 회차'라는 반쪽 상태를 남기지 않게, 예외 롤백이 둘을 함께 되돌린다.
            #   (chapters 는 아래서 재바인딩만 하므로 얕은 복사로 충분. save 전 실패 한정 롤백 → 디스크 권위와 일치.)
            cur_snap, chs_snap = state.current_chapter, list(state.chapters)
            def _fail_rollback():   # 예외 경로: 메모리 커서/spine 복원 + 세션 evict(다음 요청은 디스크=클린에서 재수화)
                if spine_snap is not None:
                    state.narrative_progress = prog_snap
                    state.world.spine = spine_snap
                state.current_chapter = cur_snap   # B-25: 회차 append 와 커서 전진은 함께 커밋되거나 함께 롤백된다
                state.chapters = chs_snap
                try:
                    sess.bus.emit('narrative', 'generate_failed', chapter=next_ch)
                except Exception:
                    pass
                self.sessions.evict(pid)
            saved = False
            try:
                _menu_used = {}   # T3: 적시 메뉴 LLM 사용량(생성 시만 채워짐) — before 차감으로 usage_total 정직 반영
                _slot_used = {}   # RC-1: 회차 예산 분배 콜 사용량(별도 스테이지 — event_menu 버킷과 섞지 않음)
                _menu_sec = 0.0   # TM-1: 적시 메뉴 소요 시간(time.monotonic — usage 대칭·생성 시만 채워짐)
                if spine and spine.arcs:   # R4 spine 모드: 에피소드(절정 backward) 단위로 beat 파생
                    # B: 커서/spine 변이를 트랜잭션으로 — FINALIZED 아니면 롤백(재시도 결정성·orphan/조기 arc.done 방지)
                    prog_snap = state.narrative_progress.model_copy(deep=True)
                    spine_snap = spine.model_copy(deep=True)
                    planner = ArcPlanner(self.planning_provider)   # B-22b: 설계=planning_model(추론) 라우팅
                    ep = planner.current_episode(state.world, state.narrative_progress, summaries,
                                                 remaining=max(2, (state.seed.target_chapters or 12) - next_ch + 1))
                    for _e in state.world.entities:   # 캐스트 플랜 레이어 동기화 — lazy 아크 설계가 낳은 인물(등장 전 설계)
                        if _e.id not in sess.bundle.ontology.entities:
                            from ..engine.ontology import Entity as _OntEntity
                            sess.bundle.ontology.add(_OntEntity(id=_e.id, name=_e.name, etype=_e.etype,
                                                                attrs=dict(_e.attrs), aliases=list(_e.aliases),
                                                                provisional=_e.provisional,
                                                                cardinality=dict(getattr(_e, "cardinality", None) or {})))   # CN-4 상한 보존(팩토리/rehydrate 와 일관)
                            sess.bus.emit("cast_plan", "registered", chapter=next_ch, entity=_e.name)
                    if ep is None:                                  # 해소 중 완결 도달
                        self._ec_on_complete(state, sess, trigger="ending_reached")   # EC-1: 미정산 advisory(비차단)
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
                    outstanding = _plants_for_menu(spine, policy)   # XR-2: OFF=데이터 인자까지 차단(메뉴 due 우회 봉합)
                    if outstanding and policy != "off":
                        plant_notes = (f"{outstanding[:self.settings.plant_inject_cap]} — 회수는 절정과 자연스럽게 맞물릴 때 다루고, "
                                       "아직 무르익지 않았다면 자산으로 그대로 둔다(천천히 쌓는 전개도 정당한 기법)")
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
                    # B-37: 재탕 후보 풀 결정론 제외 재료 — 최근 N화(rehash_lookback)의 '실현된 key_events'와 'hook_type'을
                    #   structure_history(FINALIZED만·gen_context 계획비트에서 key_events 파생)로 단일 조립한다(신규 상태 0).
                    #   이 값은 '주입'이 아니라 코드 필터/화이트리스트 축소 재료다(B-32e 안전 — 이력을 프롬프트에 보이지 않는다).
                    #   off/미전달 → 아래 두 파생이 [](메뉴 필터·훅 순환 no-op = 바이트 동일 하위호환).
                    _recent_kev: list[str] = []
                    _recent_hooks: list[str] = []
                    if self.settings.rehash_filter:
                        from ..engine.structure_history import structure_history as _shist
                        _sh = _shist(prior, n=max(1, self.settings.rehash_lookback))
                        for _it in (_sh.get("items") or []):
                            _recent_kev.extend(_it.get("key_events") or [])
                            if _it.get("hook_type"):
                                _recent_hooks.append(_it["hook_type"])
                    # DP-22: 마무리 장치 순환 입력 — '최근 2화'의 자기 라벨 closing_device 를 결정론 수집(과거 회차 분류 없음).
                    #   설계(§DP-22) 그대로 최근 2화 고정(rehash_lookback 과 별개 — 마무리 장치는 더 좁은 창으로 순환). 결측 라벨(구 회차·
                    #   구 JSON)은 여기서 자연히 빈 문자열이라 closing_whitelist 가 무시한다(하위호환). rehash_filter 게이트와 무관하게
                    #   동작(별도 feature)하되, 이력 없으면 아래 파생이 [](whitelist no-op = 바이트 동일).
                    _recent_closing = [(_c.closing_device or "").strip()
                                       for _c in sorted(prior, key=lambda c: c.chapter)
                                       if _c.status == ChapterStatus.FINALIZED and (_c.closing_device or "").strip()][-2:]
                    # SX-1: 장면 안무 순환 입력 — '최근 2화'의 자기 라벨 scene_form 을 결정론 수집(DP-22 closing 과 동형·과거 회차 분류 없음).
                    #   최근 2화 고정(안무는 더 좁은 창으로 순환)·결측 라벨(구 회차·구 JSON)은 빈 문자열이라 scene_form_whitelist 가 무시(하위호환).
                    _recent_scene_forms = [(_c.scene_form or "").strip()
                                           for _c in sorted(prior, key=lambda c: c.chapter)
                                           if _c.status == ChapterStatus.FINALIZED and (_c.scene_form or "").strip()][-2:]
                    # T3: 에피소드 활성(첫 회차 & 미생성) 시 '적시 사건 메뉴' 1회 생성 — 비트가 끌어쓸 풍부한 풀.
                    # 스냅샷(spine_snap, 위)·ep 확정 이후 & current_episode 밖에서 단일 적재 → ESCALATED/예외 자동 롤백·정상시 영속.
                    _cie = state.narrative_progress.chapters_in_episode
                    _re = self.settings.event_menu_refresh_every
                    _activate = (self.settings.event_menu and ep is not None and not ep.event_menu and _cie == 0)
                    # T4: 긴 EP stale 해소 — refresh_every>0 이면 중반 N회차마다 신선 컨텍스트로 재생성(기본 off).
                    _refresh = (self.settings.event_menu and ep is not None and bool(ep.event_menu)
                                and _re > 0 and _cie > 0 and _cie % _re == 0)
                    # B-33: 플래너 시간맹(blindness) 해소 — 설계 단계(메뉴 생성·비트 파생) 둘 다 결정론 클록 파생값
                    #        (만기 잔여·나이)을 보게 한다. 서리꽃 실데이터상 '삼 년 기한=임박'을 *최초로* 발명한 곳이
                    #        event_menu 생성 콜이라, 비트뿐 아니라 메뉴 생성기에도 같은 사실을 주입해 소스 차단한다.
                    #        자기 라벨링(time_delta) 계약은 그대로. 앵커 미선언 세계 → [](주입 생략·프롬프트 바이트 동일).
                    from ..engine.story_clock import anchor_facts as _afacts
                    _plan_deltas = [c.time_delta for c in sorted(prior, key=lambda c: c.chapter)
                                    if c.status == ChapterStatus.FINALIZED]   # 직전 회차까지(이번 비트는 아직 없음)
                    _plan_time_facts = [f"{f.entity}: {f.value}"
                                        for f in _afacts(getattr(state.world, "time_anchors", None), _plan_deltas)]
                    # DP-6: 에피소드 소비 원장 — 이 에피소드에서 '이미 지면에 실현된' required/climax 를 결정론(T2 어간
                    #       커버리지)으로 차감해, 후속 비트가 같은 재료를 다시 기획(재탕)하는 소스를 차단한다. setup 회차가
                    #       required/climax 를 선소비했는데 payoff 비트가 같은 것을 재기획하던 DP-1 주범(무기억 재탕, drift/
                    #       claim_audit 무플래그 통과). 제외는 '보이지 않게' 코드로 — '반복 금지' 지시는 넣지 않는다(앵커링·
                    #       pink-elephant). 보수적: uncovered 는 어간≥2 가드·과반 임계라 확실히 실현된 것만 소진 판정한다
                    #       (과차감이 미실현 필수사건을 죽이면 더 나쁨). prior 필터(episode_id==ep.episode_id)는 finale 의
                    #       ep_chs·T4 refresh 와 동일 = 커버리지 기준 단일(episode_id 는 FINALIZED 회차에만 박힘 → ESCALATED 자동 제외).
                    from ..engine.drift import uncovered as _uncovered
                    _ep_prior_body = " ".join(c.text for c in prior if c.episode_id == ep.episode_id and c.text)
                    _unrealized_required = (_uncovered(ep.required_events or [], _ep_prior_body)
                                            if _ep_prior_body else (ep.required_events or []))   # 빈 본문=차감 없음(하위호환 no-op)
                    # 소진분 차감(required·climax 재조준)은 '보수적으로' finale 이 아닌 회차에서만 한다. finale 은 에피소드의
                    #   마지막 지면이라 절정도 필수 사건도 반드시 여기서 터져야 하는 '지면 약속'이다 — uncovered 는 어간≥2·과반
                    #   임계라 prose 성 태그(인명+장소 명사만으로 과반 충족)에서 오탐이 나는데, 그 오탐이 '마지막 기회'인 finale
                    #   슬롯에서 미실현 required 를 떨어뜨리면 약속이 영구 미실현된다(§3: 과차감이 미실현 필수사건을 죽이면 더 나쁨).
                    #   그래서 finale 은 required·climax 를 둘 다 차감/재조준하지 않고 원본(full)을 유지해 M2 안전망을 복원한다
                    #   (재탕 리스크는 climax 와 대칭으로 수용). 비-finale 만 소진분을 뺀다. 전부 소진 시 커서 전진 검토는 상위
                    #   정합에 위임 — 여기서 강제하지 않음(B-31 정산 분리와 정합).
                    _beat_required = None if is_finale else _unrealized_required   # finale=full(안전망 복원), 비-finale=미실현만
                    # climax 재조준: 비-finale 에서 절정이 이미 과반 실현됐고 + 잔여 미실현 required 가 있으면 절정 슬롯을
                    #   '잔여 미실현 요소'로 재조준(중립 필러 아님 — 실물 잔여 목표). 잔여 없음(전부 소진)/finale=원본 climax 유지.
                    _beat_climax = None
                    if (_ep_prior_body and not is_finale and (ep.climax or "").strip()
                            and not _uncovered([ep.climax], _ep_prior_body) and _unrealized_required):
                        _beat_climax = " · ".join(_unrealized_required)
                    if (_activate or _refresh) and not _story_confirmed:   # SY-1: 사건은 확정 스토리가 쥠 — 메뉴 재생성 생략(콜 절약)
                        _mb0 = sess.provider.usage.as_dict()
                        _mt0 = time.monotonic()   # TM-1: 메뉴 생성 소요 시간 baseline(usage _mb0 대칭)
                        # refresh: 메뉴 required 시딩도 동일 차감(소진 required 제외). activate(첫 회차)면 본문 없어 no-op.
                        #   안전망: required 는 메뉴와 무관하게 beat 의 [필수 사건] 슬롯으로도 전달되므로 메뉴에서 일부 빠져도
                        #   '약속 누락'이 아니라 '풀 패딩 최적화'(substring 부정확성 영향 국한, M2).
                        _req_override = _unrealized_required if _refresh else None
                        ep.event_menu = planner.generate_event_menu(
                            state.world, arc, ep, summaries, cast_context, plant_notes, outstanding,
                            required_override=_req_override, time_facts=_plan_time_facts,
                            recent_key_events=_recent_kev,   # B-37 ⓐ: 메뉴에서 최근 실현 재탕 후보 코드 제거(보존 목록 불가침)
                            bus=sess.bus)                    # SP-2 검사 5: 재조합 계수 emit(무강제)
                        _menu_used = _usage_delta(_mb0, sess.provider.usage.as_dict())
                        _menu_sec = round(time.monotonic() - _mt0, 1)   # TM-1: 메뉴 생성 경과(초·소수1)
                        # RC-1 ⓐ: 회차 플롯 예산 분배 — 메뉴 생성 직후·같은 자리(+1콜/에피소드·회차당 0). 메뉴+필수+절정을
                        #   슬롯 1..target 에 배분해 ep.slots 에 싣는다(멱등 — not ep.slots). 이 산출이 스토리패스 재료 스코핑·
                        #   역할 파생·줄 수 스케일·slot_event/planned_event 소생의 단일 소스. chapter_budget OFF → 미실행(통짜 경로).
                        if (getattr(self.settings, "chapter_budget", False)
                                and ep is not None and ep.event_menu and not ep.slots):
                            _sb0 = sess.provider.usage.as_dict()
                            ep.slots = planner.distribute_slots(
                                state.world, arc, ep, ep.event_menu,
                                required_override=_unrealized_required, bus=sess.bus)
                            _slot_used = _usage_delta(_sb0, sess.provider.usage.as_dict())
                            _menu_used = _accumulate(_menu_used or {}, _slot_used)   # 총비용 정직 반영(before 차감 대상)
                    # RC-2: 깔때기 자동 실행 — 커서 전진(current_episode)·메뉴 생성 뒤·비트 파생 앞. 에피소드 경계
                    #   첫 화도 새 에피소드 재료(cursor·menu)로 조립된다(구 위치는 소진 에피소드 slot=target+1 로 raise).
                    #   prog_snap/spine_snap(전진 이전 스냅샷)으로 산출을 회차 커밋과 독립 영속(B-25 안전·RC-4 재사용 정합).
                    #   실패/재료부재는 None(사유는 failed 레코드로 영속) → 아래 비트 폴백(결측 정직·생성 비차단).
                    if not _story_confirmed and getattr(self.settings, "story_pass_auto", False):
                        _auto_story = self._auto_story_pass_locked(pid, state, sess, next_ch, prog_snap, spine_snap)
                        if _auto_story:
                            _story_confirmed = _auto_story
                            _story_inject = self._story_inject_check(state, sess, next_ch, _story_confirmed)
                    if _story_confirmed:
                        # SY-1 §1-D: 설계 콜 → 라벨 콜 치환(콜 ±0·사건 설계 0). 순환 축소 목록은 분류
                        #   콜에 거짓 라벨을 강제하므로 전체 목록(M-B — §4 정직 기록: 순환 압력 소멸).
                        beat, _story_cited = self._beat_from_story_labels(
                            state, sess, arc, ep, next_ch, is_finale, _story_confirmed, _plan_time_facts)
                    else:
                        beat = planner.beat_for_episode(state.world, arc, ep, next_ch, is_finale,
                                                        summaries, [d.text for d in active],
                                                        plant_notes=plant_notes, cast_context=cast_context,
                                                        event_menu=ep.event_menu, time_facts=_plan_time_facts,
                                                        required_override=_beat_required, climax_override=_beat_climax,
                                                        recent_hook_types=_recent_hooks,   # B-37 ⓑ: 최근 사용 훅 유형 제외한 화이트리스트 제시
                                                        recent_closing_devices=_recent_closing,   # DP-22: 최근 2화 마무리 장치 제외한 화이트리스트 제시
                                                        recent_scene_forms=_recent_scene_forms)   # SX-1: 최근 2화 장면 안무 제외한 화이트리스트 제시
                    # ---- 계획 하네스: 비트도 본문처럼 생성→결정론 lint(캐논 정합만)→교정 1회 ----
                    from ..engine.plan_lint import lint_beat, beat_repeat_score, lint_events
                    from ..domain.types import Violation as _V, SignalGrade as _SG
                    plan_viols = lint_beat(beat.model_dump(), sess.bundle.ontology, next_ch)
                    # DP-13: 계획층 이벤트 중복(같은 사건 이중 발주) 결정론 검사 — DP-4b 5화 회차내 반복의 소스.
                    #   key_events '상호' 어간 containment 초과 쌍만 위반으로(재계획 1회 입력). required/climax 축은
                    #   두지 않는다 — 비트가 required/climax 를 이 지면에서 실현하는 건 설계 목표(코드 강제)이지 중복이
                    #   아니므로, 그걸 잡으면 정상 실현을 결함으로 오판해 필수사건을 죽인다(설계계약 역행). 보수적 임계
                    #   (0.4·공유 어간≥3, 실측: 재발주 6개공유 vs 오탐 ≤2개)로 정당 연속 사건 오차단 회피. 판정 아닌 신호.
                    plan_viols += lint_events(list(beat.key_events))
                    # DP-3' ⓑ: 무정산 연속 상한 K — 에피소드 내에서 chapter_function=payoff 없이 K화 연속되면
                    #   재계획 1회(DP-13 과 동일 Violation 입력 경로 재사용). 이 신호는 어떤 재료도 죽이지 않는다 —
                    #   'payoff 를 보태라'는 긍정 배치 신호이고 실현은 LLM·후속 게이트 몫이다(DP-13 HIGH 교훈 정합).
                    #   소진 추적은 DP-6 계보(chapter_function 자기 라벨). finale 은 절정 지면이라 대개 payoff 로 자연 정산되고,
                    #   run 은 '같은 에피소드'로 좁혀(에피소드 경계=리듬 리셋, B-31 정산 분리와 정합) 세므로 별도 finale 예외 불필요.
                    _settle_k = getattr(self.settings, "settlement_gap_k", 0)
                    if _settle_k and _settle_k > 1:
                        from ..engine.settlement_plan import settlement_gap
                        _ep_prev_funcs = [c.chapter_function for c in prior
                                          if c.episode_id == ep.episode_id and c.status == ChapterStatus.FINALIZED]
                        _gap = settlement_gap(_ep_prev_funcs, beat.chapter_function, _settle_k)
                        if _gap is not None:
                            sess.bus.emit("plan_settle", "gap", chapter=next_ch, run=_gap, k=_settle_k)
                            # text 는 재계획 directive 로도 흐르므로 '결핍 진술'이 아니라 '긍정 배치 지시'로 —
                            #   독자에게 지불되는 사건(payoff)을 이 회차에 자연스럽게 하나 배치하라(억지·온레일 아님, 작품 톤이 정함).
                            plan_viols.append(_V(entity="beat", kind="plan_settlement_gap", grade=_SG.DETERMINISTIC,
                                                 canon="정산 리듬",
                                                 text=("독자에게 실제로 지불되는 사건(payoff — 이 작품의 톤이 약속한 보상의 실현) 하나를 "
                                                       "이번 회차에 자연스럽게 배치하고 그 회차 기능을 payoff 로 라벨하라(억지 회수 아님·작품 결에 맞게)"),
                                                 evidence="계획 산술: 정산 배치"))
                    prev_beat_summaries = [c.title + " " + (getattr(c, "detail_synopsis", "") or c.summary)
                                           for c in prior[-4:]]   # 절단 전면 제거(2026-08-21): 재탕 판정 입력 전문(회차 수 선택 [-4:] 유지)
                    # SY-1: story 모드 재탕 판정은 스토리 층 심문('재탕근접' 인용 축) 소관 — 비트 재탕 콜 생략
                    rep = 0.0 if _story_confirmed else beat_repeat_score(
                        sess.provider, f"{beat.title} {beat.summary}", prev_beat_summaries)
                    if rep > 0.86:   # 비트 재탕('같은 절벽') — 설계 단계 차단(본문 생성 전)
                        plan_viols.append(_V(entity="beat", kind="plan_beat_repeat", grade=_SG.DETERMINISTIC,
                                             canon="직전 비트들", text=f"유사도 {rep:.2f}",
                                             evidence="계획 lint: 재탕"))
                    if plan_viols and _story_confirmed:
                        # SY-1 §4(무강제): 작가 확정 스토리를 시스템이 자동 재설계하지 않는다 — emit 만.
                        #   결함은 작가에게 보이고 작가가 스토리를 고친다(자동 재계획·엔티티 폴백 변형 0).
                        sess.bus.emit("plan_lint", "violations", chapter=next_ch, story_mode=True,
                                      kinds=[v.kind for v in plan_viols])
                    elif plan_viols:
                        sess.bus.emit("plan_lint", "violations", chapter=next_ch,
                                      kinds=[v.kind for v in plan_viols])
                        fix_note = "; ".join(f"[{v.kind}] {v.text}" for v in plan_viols)
                        beat = planner.beat_for_episode(   # 위반 명시 재계획 1회(M4식 — 무한 루프 금지)
                            state.world, arc, ep, next_ch, is_finale, summaries,
                            [d.text for d in active] + [f"(계획 결함 교정 필수) {fix_note}"],
                            plant_notes=plant_notes, cast_context=cast_context,
                            event_menu=ep.event_menu, time_facts=_plan_time_facts,
                            required_override=_beat_required, climax_override=_beat_climax,
                            recent_hook_types=_recent_hooks,   # 재계획도 같은 메뉴·시간·소비원장·훅 화이트리스트(LLM 0콜 — 비대칭 방지)
                            recent_closing_devices=_recent_closing,   # DP-22: 재계획도 같은 마무리 장치 화이트리스트(비대칭 방지)
                            recent_scene_forms=_recent_scene_forms)   # SX-1: 재계획도 같은 장면 안무 화이트리스트(DP-22 비대칭 방지 계보)
                        remaining = lint_beat(beat.model_dump(), sess.bundle.ontology, next_ch)
                        remaining += lint_events(list(beat.key_events))   # DP-13: 재계획 후 이벤트 중복(상호) 잔존도 재검
                        if remaining:   # 잔존 → 가시화 + 보수 폴백(무효/제거 인물만 제외하고 진행 — 정지 금지)
                            sess.bus.emit("plan_lint", "non_convergence", chapter=next_ch,
                                          kinds=[v.kind for v in remaining])
                            # 인물 폴백은 캐논 위반(무효/제거 id)만 대상 — 이벤트 중복은 인물 제외로 못 고치고(발주 슬롯 문제)
                            # 강제 삭제하면 미실현 사건이 죽으므로(§보수적), 재계획으로 안 풀리면 짧은 회차를 수용하고 가시화만 한다.
                            bad = {v.entity for v in remaining if v.kind != "plan_event_dup"}
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
                                           text=f"[신규 등장 인물: 이번 화 첫 도입. 첫 등장 시 정체·관계를 독자가 알 수 있도록 "
                                                f"소개 앵커 1문장을 두어 새 인물로 자리잡게 하라] {e.name}: {e.profile}"))   # PF-3: 300 매직 컷 제거(반전은 arc_note 로 빠져 profile 전량 안전) + F4 em dash→콜론(산문 프롬프트 대시 시연 차단)
                            sess.bus.emit("cast_plan", "debut", chapter=next_ch, entity=e.name)
                    # CX-5: 직전 화 원문(prev_chapter)이 실릴 때만 그 화의 상세를 누적 줄거리에서 제외 —
                    #   재생성 등 prev 가 빈 경로에서는 직전 상세 유지(연속성 결손 방지 분기).
                    _prev_rec = state.chapter(next_ch - 1)
                    # XR-3: with_contributors 는 계보 기록 전용 부가 반환 — 조립 텍스트(story_so_far)는 바이트 동일.
                    story_so_far, dropped, _ssf_contrib = _build_story_so_far_hier(
                        state, next_ch, self.settings.story_so_far_chars,
                        exclude_last_detail=bool(_prev_rec and _prev_rec.text), with_contributors=True)
                else:                      # 평면 모드(하위호환)
                    beat = BeatPlanner(self.planning_provider).beat_for(   # B-22b: 비트 설계=planning_model(추론) 라우팅
                        state.world, next_ch, summaries, [d.text for d in active])
                    anchors, bible_dropped = bible_digest(state.bible, self.settings.bible_digest_chars,
                                                          f"{beat.title} {beat.summary}")
                    story_so_far, dropped = _build_story_so_far(prior, self.settings.story_so_far_chars)
                    _ssf_contrib = None   # XR-3: 평면 모드는 기여 회차 추적 미배선 — 결측 정직(gap 유지)

                prev = state.chapter(next_ch - 1)
                prev_text = prev.text if prev else ""

                before = sess.provider.usage.as_dict()
                # CE-1 ⓑ: aux provider(재요약·wiki·propose·ledger)는 별도 객체라 sess.provider 델타에 안 잡힌다.
                #   이 창의 aux 델타를 usage_total 에 additive 합산(정직 총계 — MD-1 비용표 언더카운트 차단).
                #   폴백(aux is provider)이면 같은 객체라 아래에서 0 을 더한다(이중계상 방지).
                _aux_g = getattr(sess, "aux_provider", None) or sess.provider
                _aux_g_before = _aux_g.usage.as_dict()
                if _menu_used:   # T3: 메뉴 콜은 before 이전(~비트 설계 단계)에 발생 → before 에서 차감해 delta(=usage_total)에 정직 반영
                    before = {k: v - _menu_used.get(k, 0) for k, v in before.items()}
                sess.bus.reset()
                if _menu_used:   # 메뉴 새로 생성됨(캐시 재사용 아님) — reset 이후 emit 해야 작가에게 보임. truthy⟹spine·ep 확정
                    sess.bus.emit("plan_menu", "generated", chapter=next_ch, size=len(ep.event_menu))
                if dropped:   # 오래된 맥락이 예산에서 잘림 — 조용한 정지 금지(가시화)
                    sess.bus.emit("assemble_memory", "story_truncated", chapter=next_ch, dropped=dropped)
                # XR-7①: 이번 화 재료로 흘러드는 '퇴고 반영 안 된 파생물' 가시화(결정론·LLM 0콜·비차단).
                #   생성을 막지 않는다(무강제) — 작가가 보고 필요하면 재계산 버튼을 누른다.
                _stale_derivs = _readiness_stale_derivatives(state)
                if _stale_derivs:
                    sess.bus.emit("derivatives", "stale_consumed", chapter=next_ch, items=_stale_derivs)
                # XR-10 단기(cross-review/007 §3): wiki stale 이 남아 있는 동안 위키 검색을 생성 입력에서 제외 —
                #   stale 파생물의 생성 입력 유입 금지(005 하드 불변식 5)의 소비 지점 집행. 침묵 아님(emit+UI).
                #   해제는 위키 재구축(rebuild_wiki — 작가 발동) 성공으로 stale 표식이 걷힐 때. 다른 파생물은
                #   유입 경로별 방어가 각자 담당(대사 원장=유령 인용 필터 · 나머지=비프롬프트/작가 발동).
                _gen_wiki, _wiki_stale_chs, _wiki_fp_mm = _wiki_for_generation(
                    sess.bundle.wiki, _stale_derivs, state=state)
                if _wiki_stale_chs:
                    sess.bus.emit("derivatives", "wiki_excluded", chapter=next_ch,
                                  stale_chapters=_wiki_stale_chs)
                if _wiki_fp_mm:   # XR-18: 표식 없이도 지문 불일치만으로 격리 — 원인 관측(회차·페이지) 동봉
                    sess.bus.emit("derivatives", "wiki_fingerprint_mismatch", chapter=next_ch,
                                  items=_wiki_fp_mm[:8])
                # I-1: 누적 줄거리 예산 사용률 가시화 — 경계 직후 silent 미달(예산 큰데 콘텐츠 1줄)을 작가가 보게
                _ssf_budget = self.settings.story_so_far_chars
                if spine and spine.arcs and len(story_so_far) < _ssf_budget * 0.4:
                    sess.bus.emit("assemble_memory", "story_underfilled", chapter=next_ch,
                                  used=len(story_so_far), budget=_ssf_budget)
                if bible_dropped:   # 설정집 다이제스트 예산 컷 가시화(silent drop 금지)
                    sess.bus.emit("assemble_memory", "bible_truncated", chapter=next_ch, dropped=bible_dropped)
                # XR-6⒜(감사 조건 3): 이 화 digest 에 실제 실린 작가 확정(✓) 항목 제목 가시화 — 판정기 아님(무강제).
                #   정독 보고에서 "지목된 항목이 사건으로 실현됐나"를 사람이 본다.
                _marked_titles = [m.group(1).strip() for i in anchors if i.source == "bible"
                                  for m in re.finditer(r"\]✓ ([^:\n]+):", i.text or "")]
                if _marked_titles:
                    sess.bus.emit("assemble_memory", "bible_marked", chapter=next_ch, titles=_marked_titles)
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
                chapter_eff = apply_skills(self._effective_skills(state), "chapter")   # 라이브러리에서 주입된 회차 스킬 해소·적용
                # CN-1 스토리 시계: 이전 회차들의 구조화 델타 + 이번 비트 델타를 *코드*가 결정론 누적 → 절대시점(모델은 읽기만, 산수 0).
                from ..engine.story_clock import story_time_for, anchor_facts
                _clock_deltas = [c.time_delta for c in sorted(prior, key=lambda c: c.chapter)
                                 if c.status == ChapterStatus.FINALIZED] + [beat.time_delta]
                story_time = story_time_for(_clock_deltas)
                # B-33: 시계 파생 산술(계약 만기 잔여·현재 나이)을 story_clock 이 결정론 계산 → [확정 설정] 고신뢰 팩트로 주입(LLM 산수 0).
                _anchor_facts = anchor_facts(getattr(state.world, "time_anchors", None), _clock_deltas)
                # SP-1b ①(G3/G-B): 스팬 수리 Stage B 의 사실 불변 가드레일(_guardrail·G-B 클레임 표면 비교)을
                #   실배선한다 — 엔진(generator)은 _guardrail 을 갖지 않으므로(서비스 계층 소유) 생성 직전에 서비스
                #   자신을 주입한다. harness.repair_spans 가 service=self.service 로 관통해 st11 이 G-B 를 태운다.
                #   라이브 온톨로지/체커 세션에서만 주입 → 러너/테스트/구 세션은 None 유지(기존 no-op 계약 불변).
                sess.bundle.generator.service = self
                # HM-1 N-3 모티프 원장 입력: 선행 FINALIZED 회차 본문(회차 간 반복 구절 대조 기저). 선행 없으면
                #   빈 리스트 → generate() 가 N-3 생략(cross-chapter 판별 불가·결측 정직), 단일 회차 축은 계속.
                _prev_texts = [c.text for c in sorted(prior, key=lambda c: c.chapter)
                               if c.status == ChapterStatus.FINALIZED and c.text]
                # DG-6: 선행 확정 대사 원장(DG-1 영속분) → 조회 응답의 관계 어체 실물 앵커 재료.
                #   원장 없는 회차(구 데이터)는 자연히 빠짐 — 전무하면 빈 리스트=기존 응답 바이트 동일.
                # XR-7②: 소스 지점 1곳에서 유령 인용을 결정론으로 걸러 낸다(아래 헬퍼 — 전 소비처 일괄 방어).
                _prev_ledgers = _verified_prev_ledgers(prior, bus=sess.bus, next_ch=next_ch)
                record = sess.bundle.generator.generate(
                    next_ch, beat.model_dump(), sess.bundle.ontology, sess.bundle.rag,
                    _gen_wiki, directives=active, prev_chapter_text=prev_text,   # XR-10: wiki-stale 시 격리 프록시
                    story_so_far=story_so_far, anchors=anchors, closing=closing,
                    recent_tails=recent_tails, restraint=restraint,
                    skills_inject=chapter_eff.prompt_inject, story_time=story_time,
                    extra_facts=_anchor_facts, prev_texts=_prev_texts,
                    bible_entries=state.bible.entries,   # AG-1 T4: 선조회 lookup 의 bible 키워드 조회 재료
                    prev_ledgers=_prev_ledgers,          # DG-6: 관계 어체 실물 앵커(원장 발췌) 재료
                    confirmed_story=_story_confirmed)    # SY-1: ""(레거시)면 하니스·프롬프트 바이트 동일
                # FI-1 §4: 회차 세대 각인(결정론·LLM 0·플래그 무관 — regen_events 와 동급 식별자). 이 회차에 대해
                #   regenerate_last_chapter 가 '생성 전' append 한 RegenEvent.seq 최대+1(최초 생성=1, 재생성마다 +1).
                #   폐기된 세대에 남긴 작가 의도 이벤트가 새 세대 계측과 조인되는 유령 신호를 gen_no 스냅샷으로 차단.
                record.gen_no = 1 + max((e.seq for e in state.regen_events if e.chapter == next_ch), default=0)
                # 감사 스냅샷: 라이브러리(live SSOT) 후속 편집이 *과거* 회차를 되쓰지 않도록 이 회차에 실제 적용된 스킬을 동결.
                if isinstance(record.gen_context, dict) and (chapter_eff.applied or chapter_eff.prompt_inject):
                    record.gen_context["skills"] = {"applied": chapter_eff.applied, "inject": chapter_eff.prompt_inject}

                # XR-3: 엔진이 모르는 두 블록(누적 줄거리 기여 회차·직전 회차 세대/리비전)을 서비스가 채운다.
                #   쓰기 전용 — 이 값을 다시 읽어 프롬프트에 넣는 코드는 존재하지 않는다(K3, 결정론 테스트가 강제).
                self._fill_draft_lineage(record, _ssf_contrib, dropped, prev)

                # SY-1 §2: 생성 시 스냅샷(출력·감사용 — 입력 아님. gen_context 는 자유형 dict, 스키마 변경 0)
                if _story_confirmed and isinstance(record.gen_context, dict):
                    from ..engine import story_pass_prompts as _spp_snap
                    _sp_rec2 = self._story_pass_record(state, next_ch)
                    record.gen_context["story_pass"] = {
                        "status": "confirmed", "story": _story_confirmed,
                        "lines": len(_spp_snap.story_lines(_story_confirmed)),
                        "digest": (_sp_rec2.materials_digest if _sp_rec2 else ""),
                        "source": (_sp_rec2.source if _sp_rec2 else ""),
                        "time_source_cited": _story_cited, **_story_inject}
                # 디버그: 계획(설계) 입력을 회차 컨텍스트에 합침 — '어떤 정보로 설계·집필했는지' 추적
                if spine and spine.arcs and isinstance(record.gen_context, dict):
                    _gc = getattr(state.world, "genre_contract", None)
                    record.gen_context["plan"] = {
                        "arc": (arc.title if arc else ""), "episode": (ep.title if ep else ""),
                        "is_finale": is_finale, "recent": summaries,
                        "cast_context": cast_context or "",   # 절단 전면 제거(2026-08-21): 디버그 스냅샷도 실주입분 전문(뷰가 프롬프트보다 짧아 오독되던 소스)
                        "plant_notes": plant_notes or "",
                        "restraint": list(restraint or [])[:10],
                        "event_menu": (ep.event_menu or [])[:14] if ep else [],   # T3: 어떤 신선 사건 풀이 이 회차를 먹였나(작가 가시화)
                        "menu_generated_this_chapter": bool(_menu_used),          # 새 생성 vs 캐시 재사용(신선도 추적)
                        "genre_contract": (_gc.model_dump() if _gc else None),
                    }

                # SX-2: 콜드리드 결과 지역 변수(아래 build_verification 주입용). FINALIZED 경로에서만 채워지고,
                #   ESCALATED·미실행·실패 시엔 None → build_verification 이 verification.cold_read 를 "미실행"으로 남긴다(결측 정직).
                cold_read_result = None
                # 동적 온톨로지 업데이트(엔진 고도화) — FINALIZED 회차에만
                if record.status == ChapterStatus.FINALIZED:
                    if _menu_used:   # T3 적시 메뉴 비용 가시화(advisory 스테이지 — usage_total 에는 before 차감으로 이미 반영)
                        # RC-1: 분배 콜 토큰은 별도 스테이지로 분리 계상(event_menu 버킷은 순수 메뉴 몫만).
                        record.usage_by_stage["event_menu"] = _menu_used.get("chat_tokens", 0) - _slot_used.get("chat_tokens", 0)
                        record.time_by_stage["event_menu"] = _menu_sec   # TM-1: 메뉴 소요 시간(usage 대칭)
                        if _slot_used.get("chat_tokens", 0):
                            record.usage_by_stage["slot_budget"] = _slot_used.get("chat_tokens", 0)
                    _aux = getattr(sess, "aux_provider", None) or sess.provider   # CE-1 ⓑ: propose=aux(updater 가 aux 로 구성됨). 폴백 시 aux is provider → 델타 동일
                    _t0 = _aux.usage.chat_tokens
                    _ts0 = time.monotonic()   # TM-1: ontology_propose 소요 시간 baseline(_t0 대칭)
                    for eid in sess.bundle.ontology.scan_present_ids(record.text):   # 데뷔 완료 마킹(이후 앵커 중복 방지)
                        e = next((x for x in state.world.entities if x.id == eid), None)
                        if e is not None and not getattr(e, "introduced", False):
                            e.introduced = True
                        # RR-1: 온톨로지 미러도 동기화(장수 세션에서 EntitySpec 만 마킹되면 ontology.Entity.introduced 가
                        #   stale 하게 남아 프로즈 명부 노출 필터가 오작동). 본문에 등장한 시점 = 데뷔 완료(introduced=True).
                        _oe = sess.bundle.ontology.entities.get(eid)
                        if _oe is not None:
                            _oe.introduced = True
                    proposal = sess.bundle.updater.propose(
                        record.text, sess.bundle.ontology, next_ch,
                        existing_setting_titles=[b.title for b in state.bible.entries if b.status != "deprecated"],
                        claims=record._final_claims)   # OV-2: 재추출 대신 최종 check_text 의 정규화 클레임 소비(단일 계약)
                    record.usage_by_stage["ontology_propose"] = _aux.usage.chat_tokens - _t0
                    record.time_by_stage["ontology_propose"] = round(time.monotonic() - _ts0, 1)   # TM-1: 경과(초·소수1)
                    if proposal.get("stage_failed"):
                        # OV-5: 제안 콜 실패의 정직 영속 — 빈 []("변경 없음")와 구분되는 표식을 레코드에 남겨
                        #   검증 축·리포트가 침묵 사망을 드러내고 소급 재실행 대상을 특정할 수 있게 한다.
                        from ..domain.types import OntologyChange
                        record.ontology_changes = [OntologyChange(
                            op="stage_failure", entity="(온톨로지 제안)", applied=False,
                            detail=f"제안 콜 실패({proposal['stage_failed']}) — 이 회차 캐논 갱신 미실행·소급 재실행 필요",
                            reason=str(proposal["stage_failed"]), severity="review")]
                    else:
                        changes, new_specs, new_tl, new_edges = sess.bundle.updater.apply(
                            proposal, sess.bundle.ontology, next_ch)
                        record.ontology_changes = changes
                        state.runtime_entities += new_specs
                        state.runtime_timeline = _dedup_timeline(state.runtime_timeline + new_tl)   # 동시점 중복 누적 차단(재생성)
                        state.runtime_edges += new_edges          # 자동추출 관계(narrative_inferred) 영속
                    # 설정집 연재 증분: 회차에서 드러난 세계 설정 → 미승인 초안으로만 append(작가 promote 게이트 보존)
                    existing_titles = {b.title.strip() for b in state.bible.entries}
                    from ..engine.extractor import _quote_in_text as _qin   # VA-3: 발명 설정 차단
                    for ns in (proposal.get("new_settings") or [])[:2]:
                        # VA-3(치명 3): 본문 인용(evidence) 미통과 항목은 드롭 — 발명 설정이 캐논 초안·
                        #   다음 화 앵커로 승격되는 채널 차단(measure-then-cite, 가시화 동반).
                        if not _qin(str(ns.get("evidence") or ""), record.text or ""):
                            sess.bus.emit("ontology", "setting_uncited", chapter=next_ch,
                                          title=(ns.get("title") or "")[:40])
                            continue
                        t = (ns.get("title") or "").strip()
                        _pr = (ns.get("prose") or "")
                        if _pr.count("—") or len(_pr) > 400:   # VA-3: 증분 위생 가시화(차단 없음)
                            sess.bus.emit("ontology", "setting_hygiene", chapter=next_ch,
                                          title=t[:40], dash=_pr.count("—"), chars=len(_pr))
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
                        _auxl = getattr(sess, "aux_provider", None) or sess.provider   # CE-1 ⓑ: ledger_reconcile=aux. 폴백 시 aux is provider → 델타 동일
                        _tp = _auxl.usage.chat_tokens
                        _tsp = time.monotonic()   # TM-1: ledger_reconcile 소요 시간 baseline(_tp 대칭)
                        recon = reconcile_ledger_from_prose(
                            _auxl, record.text, state.promise_ledger.open_promises(), next_ch,
                            window=getattr(self.settings, "ledger_recon_window", 20),           # B-30 순환 검출창
                            priority_slots=getattr(self.settings, "ledger_recon_priority", 12))
                        record.usage_by_stage["ledger_reconcile"] = _auxl.usage.chat_tokens - _tp
                        record.time_by_stage["ledger_reconcile"] = round(time.monotonic() - _tsp, 1)   # TM-1: 경과(초·소수1)
                        n_paid = mark_paid(state.promise_ledger, recon["paid"], next_ch)
                        n_open = add_opened_promises(state.promise_ledger, recon["opened"], next_ch)
                        if n_paid or n_open:
                            sess.bus.emit("ledger", "reconciled", chapter=next_ch,
                                          paid=n_paid, opened=n_open)
                    # G2: 블라인드 장르 독자 행동 예측(advisory — 비차단·비강제, 작가 가시화). 비트/설계 미공개로 본문만 읽음.
                    if getattr(self.settings, "reader_desk", True):
                        from ..engine.reader_desk import reader_prediction
                        _tr = sess.provider.usage.chat_tokens
                        _tsr = time.monotonic()   # TM-1: reader_desk 소요 시간 baseline(_tr 대칭)
                        _gc = getattr(state.world, "genre_contract", None)
                        pred = reader_prediction(sess.provider, record.text, story_so_far, state.world.genre,
                                                 expectations=(_gc.reader_expectations if _gc else None),
                                                 sofar_budget=getattr(self.settings, "reader_desk_sofar_chars", 12000))
                        record.usage_by_stage["reader_desk"] = sess.provider.usage.chat_tokens - _tr
                        record.time_by_stage["reader_desk"] = round(time.monotonic() - _tsr, 1)   # TM-1: 경과(초·소수1)
                        if pred:
                            record.reader_feedback = pred           # 작가가 나중에 검토(원장처럼 가시화)
                            sess.bus.emit("reader_desk", "prediction", chapter=next_ch, **pred)
                    # SX-2: 콜드리드 독자 축 — reader_desk 인접(락 밖 LLM 규율 동일). 입력은 **프로즈만**(1화~현재 화 본문 연결·
                    #   요약/설정/계획 일절 미주입 — reader_desk 요약 오염 사각 해소). 심사는 cross-vendor(make_judge 계보 gen≠judge).
                    #   advisory·비차단·실패 흡수(None). 결과는 아래 build_verification 에 주입돼 verification.cold_read 로 영속(미실행 시 MISSING).
                    if getattr(self.settings, "cold_read", True):
                        try:
                            from ..engine.cold_read import cold_read_probe
                            from ..engine.chapter_gate import make_judge
                            _cr_provider, _ = make_judge(self.settings, getattr(self.settings, "llm_provider", ""))
                            _crt = _cr_provider.usage.chat_tokens
                            _crts = time.monotonic()   # TM-1: cold_read 소요 시간 baseline(_crt 대칭)
                            _cr_texts = [c.text for c in sorted(state.chapters + [record], key=lambda c: c.chapter)
                                         if c.status == ChapterStatus.FINALIZED and c.text]   # 1화~현재(record 포함)·프로즈만
                            _cr_res = cold_read_probe(
                                _cr_provider, _cr_texts, genre=state.world.genre,
                                max_chars=getattr(self.settings, "cold_read_max_chars", None))   # 절단 전면 제거(2026-08-21): 기본 무절단
                            record.usage_by_stage["cold_read"] = _cr_provider.usage.chat_tokens - _crt   # TM-1 대칭(usage 계상)
                            record.time_by_stage["cold_read"] = round(time.monotonic() - _crts, 1)       # TM-1: 경과(초·소수1)
                            if _cr_res:
                                cold_read_result = _cr_res   # build_verification 주입용(FINALIZED 경로 지역 변수)
                                sess.bus.emit("cold_read", "probe", chapter=next_ch, **_cr_res)
                        except Exception as _e:
                            sess.bus.emit("cold_read", "failure", chapter=next_ch, error=str(_e)[:200])
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
                        # EPT-1(사용자 지시 2026-08-03): 공개 회차 제목 = "에피소드명 (에피 내 순번)" — 노벨피아
                        #   연재 목록 관행(전독시식). 비트의 창작 제목은 스포일러 위험(회차 결말 누설 실측:
                        #   '쇠가 나를 잡았다'·'세 번째 얼룩')이라 공개면에서 제외하되 gen_context.plan 에 원본 보존.
                        #   spine 없는 평면 beats 작품은 비트 제목 유지(하위호환 — 이 분기 자체가 spine 전용).
                        record.title = f"{ep.title} ({state.narrative_progress.chapters_in_episode + 1})"
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

                # B-25 부분실패 트랜잭션 정합: 회차 append 는 커서 전진(위 FINALIZED 블록의 current_chapter=next_ch)과
                # '한 단위'로만 커밋한다. 비FINALIZED(본문 LLM 타임아웃→빈/부분 생성·하드위반 비수렴이 ESCALATED 로
                # 강등된 경우)는 위에서 spine/커서를 롤백했으므로 chapters 에도 영속하지 않는다(함께 롤백 — 반쪽 커밋 금지).
                # 이전엔 ESCALATED 레코드가 chapters 에 저장돼 current_chapter(N)↔chapters(N+1) 불일치가 디스크에
                # 남았다(무협 실측 — LR 러너가 측정하는 커서 불변식 위반). 레코드·회복안내·이벤트는 아래 반환
                # 페이로드로 작가에게 그대로 전달된다(가시화 유지 — 조용한 정지 아님). 재시도=같은 next_ch 재생성.
                if record.status == ChapterStatus.FINALIZED:
                    # 기존 회차 재생성이면 교체, 아니면 추가
                    state.chapters = [c for c in state.chapters if c.chapter != next_ch] + [record]
                    state.chapters.sort(key=lambda c: c.chapter)
                    # EC-1: FINALIZED마다 엔딩 술어계약 결정론 평가(LLM 0콜·gt/ni 병렬) → 회차 영속+advisory 이벤트.
                    #  온톨로지 갱신·원장 정산·커서 전진(에피 done)이 끝난 뒤 = 이 회차의 최종 상태 기준.
                    self._ec_after_finalize(state, sess, record, next_ch)
                else:
                    # 자가치유: 이 수정 전에 영속된 커서-불일치 꼬리(next_ch 자리의 구 ESCALATED 잔재)를 제거해
                    # 레거시 데이터도 저장 시점에 정합으로 수렴시킨다(FINALIZED 발행본은 절대 건드리지 않음).
                    state.chapters = [c for c in state.chapters
                                      if c.chapter != next_ch or c.status == ChapterStatus.FINALIZED]

                # PR-2 §2(감사 G-3): ai_tell 은 advisory 결정론 축(LLM 0콜) — FINALIZED 블록에서만 계산돼
                #   ESCALATED 회차엔 비어 있었다(경로 비대칭). status 분기 밖으로 올려 두 경로 대칭 계산한다
                #   (검토 필요 회차야말로 문체 진단이 더 필요). 이미 채워졌으면(FINALIZED 경로) 재계산 생략(멱등).
                if not record.ai_tell:
                    record.ai_tell = self._recompute_ai_tell(state, sess, record.text)

                # PR-2 §1·§2: 회차 통합 검증 리포트(SSOT) — FINALIZED/ESCALATED 확정 직후 전 축 결정론 집계.
                #   전 축 키가 항상 존재하고 안 돈 축은 "미실행" 명시(결측 정직). 신규 검출기 0(기존 계측 집계).
                #   reader_feedback·ending_contract_eval 은 현재 FINALIZED 경로만 채우므로 ESCALATED 는 MISSING
                #   으로 정직 표기된다(PR-1이 그 축을 분기 밖으로 이관하면 자동으로 값이 실린다). 예외 전량 흡수(비차단).
                try:
                    from ..engine.verification import build_verification
                    _prev_texts = [c.text for c in sorted(state.chapters, key=lambda c: c.chapter)
                                   if c.status == ChapterStatus.FINALIZED and c.chapter < record.chapter and c.text]
                    _target = getattr(getattr(state.world, "style", None), "target_chars_per_chapter", None)
                    record.verification = build_verification(
                        record, prev_texts=_prev_texts, target_chars=_target,
                        cold_read=cold_read_result,   # SX-2: 콜드리드 결과 주입(미실행/실패=None → verification.cold_read="미실행")
                        leak_sources=self._leak_sources_for(state, next_ch),   # VL-1: 누출 스윕(사후 감사·차단 0)
                        ending_sources=self._ending_sources_for(state, record))   # XR-1: 결말 누출 관측 축
                except Exception as e:
                    sess.bus.emit("verification", "failure", chapter=next_ch, error=str(e)[:200])

                delta = _usage_delta(before, sess.provider.usage.as_dict())
                if _aux_g is not sess.provider:   # CE-1 ⓑ: aux 델타 additive(별도 객체일 때만 — 폴백은 이미 delta 에 포함)
                    delta = _accumulate(delta, _usage_delta(_aux_g_before, _aux_g.usage.as_dict()))
                state.usage_total = _accumulate(state.usage_total, delta)
                sess.snapshot_into(state)
                self.repo.save(state)
                saved = True
                # GA-1: 생성 트레이스(중간 산출물 전량) 사이드카 영속 — 발행 직후·append-only. 무강제(try/except):
                #   저장 실패는 발행을 막지 않는다. FINALIZED/ESCALATED 모두 저장('싹 다 저장'). ts 는 서버 시각 주입
                #   (엔진은 시각 소스 없음). OFF(config gen_trace) 면 record._gen_trace=None → save_trace 미호출(바이트 동일).
                # GA-1(스코프 확장 2026-07-15 "모든 로그를 남겨서 디버깅 가능하게 — 어떤 판단을 했고 뭘 했고"):
                #   EventBus 타임라인 전량을 trace 에 편입한다. 회차 시작 시점에 sess.bus.reset()(위 :2290)이 버퍼를
                #   비우므로 지금 buffer 는 *이 회차 구간*만 담는다(다른 회차/작업 미혼입 — reset 경계로 분리). 각
                #   이벤트에 seq(순번) 부여·emit 원형(node·event·payload) 순서 보존 = "무슨 판단을 어떤 순서로 했나"
                #   완전 로그. 실패 이벤트(failures)는 trace 최상위로 승격(디버깅 빠른 진입점).
                _trace = getattr(record, "_gen_trace", None)
                failures = sess.bus.failures()
                if _trace is not None:
                    try:
                        _events = [{"seq": _i, **dict(_e)} for _i, _e in enumerate(sess.bus.buffer)]
                        _trace = {**_trace, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  "events": _events,          # 회차 구간 이벤트 타임라인 전량(순서·payload 보존)
                                  "failures": list(failures)}  # 실패 이벤트 승격(디버깅 빠른 진입점)
                        self.repo.save_trace(pid, next_ch, _trace,
                                             max_runs=int(getattr(self.settings, "gen_trace_max_runs", 50)))
                    except Exception as _te:
                        sess.bus.emit("gen_trace", "save_failure", chapter=next_ch, error=str(_te)[:200])

                _result = {"record": record, "events": list(sess.bus.buffer), "usage_delta": delta,
                           "usage_total": state.usage_total, "failures": failures, "completed": False,
                           "current_chapter": state.current_chapter,
                           "total_beats": (state.seed.target_chapters or len(state.world.beats))}
                # PR-1: 제품 게이트는 FINALIZED 확정·저장 이후(락 해제 후) 실행해야 한다 — 게이트의 국소 revise/
                #   accept/undo·regen 이 서비스 메서드로 위임되고 그 메서드들이 같은 sess.lock 을 (비차단으로) 잡기
                #   때문. 여기선 실행 여부만 표식하고, with 블록을 정상 종료해 락을 푼 뒤 아래에서 게이트를 돈다.
                _run_gate = bool(record.status == ChapterStatus.FINALIZED
                                 and getattr(self.settings, "product_gate", False)
                                 and pid not in self._product_gate_active)   # 게이트 regen 재진입은 중첩 게이트 금지
                # ST-12d: 재실현 파이프라인 내장 — FINALIZED 확정·저장·락 해제 이후 실행 표식만 여기서 잡고,
                #   실제 훅은 with 블록을 정상 종료해 락을 푼 뒤(아래) 돈다. rerender_chapter 가 같은 sess.lock 을
                #   비차단으로 재획득하므로(revise/accept 계보) 반드시 락 밖에서 호출해야 한다(product_gate 훅과 동일 규율).
                #   ESCALATED 는 자동 훅 대상 아님(보수 — rerender_chapter 자체는 받지만 자동은 FINALIZED 만).
                _run_rerender = bool(record.status == ChapterStatus.FINALIZED
                                     and getattr(self.settings, "rerender_in_pipeline", False)
                                     and pid not in self._product_gate_active)   # 게이트 regen 재진입 중엔 자동 재실현도 보류
            except Exception:
                if not saved:   # save 전 실패만 롤백 — save 후 예외는 디스크가 권위(롤백 금지)
                    _fail_rollback()
                raise
        # ── 락 해제 후: 재실현 파이프라인(ST-12d·옵션·RR-1 이후 기본 OFF — env/작품 설정으로 opt-in) ─────
        # 발행 게이트(product_gate)보다 *먼저* 돈다 — 게이트는 재실현이 반영된 최종본을 심사해야 하기 때문.
        # 무강제: 원문 보호 정독 게이트·사실 가드가 채택을 결정하고, 실패/미채택/예외는 회차 발행 상태를 바꾸지
        #   않는다(이미 발행 완료). OFF(또는 비FINALIZED·게이트 재진입)면 no-op → 기존 경로 무접촉.
        if _run_rerender:
            self._apply_rerender_pipeline(pid, record.chapter, sess)
        # ── 락 해제 후: 제품 게이트(옵션·기본 OFF) ──────────────────────────────────
        # OFF(또는 비FINALIZED)면 이 블록은 no-op → 기존 경로 바이트 동일(반환 페이로드 불변).
        # ON이면 커밋된 회차를 정독 게이트(심사→국소 revise/regen→최선 보존)에 태우고 판정·라운드를
        #   verification.gate 로 영속한다(러너 jsonl 무덤 해소). 무강제: FAIL 이어도 회차 발행 상태는 유지(기록·재시도만).
        if _run_gate:
            self._apply_product_gate(pid, record.chapter, sess, _result)
        return _result

    def _apply_rerender_pipeline(self, pid: str, chapter: int, sess) -> None:
        """ST-12d: 방금 발행된 FINALIZED 회차에 문체 재실현을 자동 실행(생성 파이프라인 내장).

        반드시 sess.lock 해제 뒤(=여기)에서만 호출한다 — rerender_chapter 가 같은 sess.lock 을 비차단으로
        재획득하기 때문(product_gate 훅과 동일 락 규율). 훅의 어떤 실패도 회차 발행 상태를 바꾸지 않는다:
        회차는 이미 FINALIZED 로 커밋·저장됐고, 재실현의 예외/미채택/스킵은 전부 여기서 흡수+이벤트로만 가시화한다.

        비용 스킵(결정론): 방금 발행된 본문을 lazy Kiwi(_kiwi_metrics_fn — 엔진 로드타임 tools import 0)로 계측해
          이미 인간 대역 안(top_ratio ≤ 대역 상한 · max_run ≤ 상한)이면 재실현을 생략한다(무비용 — 옮길 게 없음).
          계측 불가(kiwi 부재)면 실행 쪽으로(보수 — 스킵 아님·결측이 스킵 근거가 되지 않게).

        무강제 정합: 자동이지만 채택 여부는 rerender_chapter 안의 원문 보호 정독 게이트·사실 가드(G-A/G-B)가 결정하고,
          전 과정이 revision 으로 기록·undo 가능하며(rerender_chapter 계약), config(rerender_in_pipeline)로 끌 수 있다.
        이벤트(product_gate 네이밍 관행 따름): rerender_pipeline start/skip/adopted/rejected/failure.

        관측 영속(RO-1, 2026-07-15 실측 침묵 교정): sess.bus 는 인메모리 전용이고 gen-trace 이벤트 스냅샷은
          이 훅 *이전*(락 안)에 저장되므로, 여기서 emit 만 하면 훅의 생사가 디스크 어디에도 안 남는다 — 실제로
          라이브 런 4회차가 대역 밖인데 재실현 흔적 0 인 '조용한 침묵'이 관측 불능으로 판별조차 불가했다.
          그래서 모든 라이프사이클(start/skip/failure/adopted/rejected)을 트레이스 사이드카에도 append 한다
          (kind="rerender_pipeline" — rerender_chapter 자체 기록 kind="rerender" 와 구분). 특히 failure 는
          traceback 을 담아 rerender_chapter 가 자기 기록(첫 _save_rerender_trace) 전에 죽어도 사유가 남는다.
          영속 실패는 훅을 막지 않는다(관측은 부가 — 발행·재실현 무영향)."""
        from ..engine import rerender as _rr

        def _persist(event: str, **fields) -> None:
            # RO-1: 버스 emit 과 쌍으로 디스크 영속(사이드카 append-only). 실패 무해(관측 부가).
            try:
                self.repo.save_trace(pid, chapter, {"kind": "rerender_pipeline", "event": event,
                                                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields})
            except Exception:
                pass
        # ── 비용 스킵: 발행된 최신 본문을 디스크 권위에서 읽어 lazy Kiwi 계측(진행 중 데이터 접근 아님 — 방금 발행분).
        try:
            state = self.repo.get(pid)
            ch = state.chapter(chapter) if state else None
            before_text = (getattr(ch, "text", "") or "") if ch is not None else ""
            km = self._kiwi_metrics_fn()(before_text) if before_text else None
            ep = (km.get("ending_profile") or {}) if isinstance(km, dict) else {}
            top_ratio = ep.get("top_ratio")
            max_run = ep.get("max_run")
            # in-band 판정 — 대역 상수는 engine/rerender.py SSOT 재사용(_TOP_RATIO_BAND 상한·_MAX_RUN_BAND_HI).
            #   계측 불가(top_ratio/max_run None)면 in_band=False → 실행 쪽으로(보수). 둘 다 대역 안이면 스킵.
            in_band = (isinstance(top_ratio, (int, float)) and isinstance(max_run, (int, float))
                       and top_ratio <= _rr._TOP_RATIO_BAND[1] and max_run <= _rr._MAX_RUN_BAND_HI)
        except Exception as e:
            in_band = False   # 계측 경로 예외도 스킵 근거가 되지 않게(보수 — 실행 쪽으로)
            top_ratio = max_run = None
            try:
                sess.bus.emit("rerender_pipeline", "failure", chapter=chapter, error=str(e)[:200])
            except Exception:
                pass
            _persist("failure", stage="measure", error=str(e)[:200])
        if in_band:
            try:
                sess.bus.emit("rerender_pipeline", "skip", chapter=chapter,
                              reason="in_band", top_ratio=top_ratio, max_run=max_run)
            except Exception:
                pass
            _persist("skip", reason="in_band", top_ratio=top_ratio, max_run=max_run)
            return
        # ── 실행: 기존 rerender_chapter 재사용(중복 구현 금지). 반환 dict 를 이벤트에 요약해 가시화.
        try:
            sess.bus.emit("rerender_pipeline", "start", chapter=chapter,
                          top_ratio=top_ratio, max_run=max_run)
        except Exception:
            pass
        _persist("start", top_ratio=top_ratio, max_run=max_run)
        try:
            res = self.rerender_chapter(pid, chapter)
        except Exception as e:
            # 어떤 실패도 발행 상태 무영향 — 예외 전량 흡수 + 이벤트로만 가시화.
            try:
                sess.bus.emit("rerender_pipeline", "failure", chapter=chapter, error=str(e)[:200])
            except Exception:
                pass
            import traceback as _tb   # RO-1: 침묵 사인 규명용 — 사유 전문(traceback)을 사이드카에 남긴다
            _persist("failure", stage="rerender_chapter", error=str(e)[:200],
                     traceback=_tb.format_exc()[-2000:])
            return
        if res is None:   # 423(락 경합 — 회차 생성 중) → skip 기록
            try:
                sess.bus.emit("rerender_pipeline", "skip", chapter=chapter, reason="locked")
            except Exception:
                pass
            _persist("skip", reason="locked")
            return
        adopted = bool(res.get("adopted"))
        try:
            sess.bus.emit("rerender_pipeline", ("adopted" if adopted else "rejected"),
                          chapter=chapter, adopted=adopted, reason=res.get("reason"),
                          before_kiwi=res.get("before_kiwi"), after_kiwi=res.get("after_kiwi"))
        except Exception:
            pass
        _persist(("adopted" if adopted else "rejected"), adopted=adopted,
                 reason=(res.get("reason") or "")[:300])

    def _apply_product_gate(self, pid: str, chapter: int, sess, result: dict) -> None:
        """PR-1: 커밋된 FINALIZED 회차에 제품 정독 게이트를 돌리고 판정·재시도 이력을 verification.gate 로 영속.

        게이트 코어(engine.chapter_gate)는 tools 무의존이고, engine.product_gate 가 서비스 어댑터·measure_fn·
        judge_fn 을 조립한다. 국소 revise/accept/undo·regen 은 서비스 메서드로 위임되므로 반드시 sess.lock 해제
        뒤(=여기)에서만 호출한다(비차단 락 경쟁 회피). 게이트가 회차 본문을 바꿨을 수 있으니(국소 퇴고·regen)
        최신 record 를 디스크에서 재로드해 verification.gate 만 갱신하고 재저장한다 — verification 다른 축은
        게이트 전 상태 기준이라 gate 결과가 반영된 최신 본문과 어긋날 수 있으므로 build_verification 을 한 번 더
        돌려 전 축을 gate 포함으로 재집계한다(SSOT 정합). 예외는 전량 흡수(게이트가 회차 발행을 막지 않음 — 무강제).
        반환 result['record'] 도 최신 record 로 교체해 작가 UI 가 게이트 결과·재시도 이력을 바로 보게 한다."""
        self._product_gate_active.add(pid)   # 재진입 가드 — 게이트 안의 regen(회차 재생성)은 게이트를 다시 돌지 않음
        try:
            from ..engine.product_gate import run_product_gate
            from ..engine.verification import build_verification
            gate = run_product_gate(self, pid, chapter, settings=self.settings)
            if not gate:
                return
            sess.bus.emit("product_gate", "result", chapter=chapter,
                          verdict=gate.get("verdict"), retries=gate.get("retries"),
                          fail_exhausted=gate.get("fail_exhausted"),
                          retention=gate.get("retention_est"))
        except Exception as e:
            try:
                sess.bus.emit("product_gate", "failure", chapter=chapter, error=str(e)[:200])
            except Exception:
                pass
            return
        finally:
            self._product_gate_active.discard(pid)
        # 게이트 뒤 최신 상태로 verification 재집계(gate 포함) — 국소 퇴고/regen 이 본문을 바꿨을 수 있음.
        #   게이트 안 regen 은 세션을 evict 하므로(regenerate_last_chapter) 여기선 세션을 새로 받아 그 락으로 직렬화한다.
        try:
            gsess, gstate = self.get_session(pid)
            lock = gsess.lock if gsess is not None else sess.lock
            with lock:
                state = self.repo.get(pid)
                if not state:
                    return
                rec = state.chapter(chapter)
                if rec is None:
                    return
                _prev = [c.text for c in sorted(state.chapters, key=lambda c: c.chapter)
                         if c.status == ChapterStatus.FINALIZED and c.chapter < rec.chapter and c.text]
                _target = getattr(getattr(state.world, "style", None), "target_chars_per_chapter", None)
                # SX-2: 콜드리드는 발행 후 1회만 돈다(재프로브 금지 — 비용). 게이트 재집계 시 앞서 영속된 cold_read 를
                #   그대로 보존(dict 이면 재주입, "미실행"/결측이면 None → 다시 MISSING). 재실행하지 않는다.
                _prev_cr = (rec.verification or {}).get("cold_read")
                _keep_cr = _prev_cr if isinstance(_prev_cr, dict) else None
                rec.verification = build_verification(rec, prev_texts=_prev, target_chars=_target,
                                                      gate=gate, cold_read=_keep_cr,
                                                      leak_sources=self._leak_sources_for(state, rec.chapter),   # VL-1
                                                      ending_sources=self._ending_sources_for(state, rec))       # XR-1
                self.repo.save(state)
            # 반환 페이로드의 record 도 게이트 반영 최신본으로 교체(작가 UI 즉시 가시화 — 게이트 결과·재시도 이력)
            fresh = self.repo.get(pid)
            if fresh is not None:
                fr = fresh.chapter(chapter)
                if fr is not None:
                    result["record"] = fr
        except Exception as e:
            try:
                sess.bus.emit("product_gate", "persist_failure", chapter=chapter, error=str(e)[:200])
            except Exception:
                pass

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
                                    "event_menu": (e.event_menu or [])[:14],   # T3: 에피소드 구조 뷰에 적시 메뉴 노출
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
            sess = self._resolve_locked_session(sess, state)
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
            sess = self._resolve_locked_session(sess, state)
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
            if any(a["target"] == "ending" for a in applied):
                # EC-1: 엔딩 개정 → 계약 재컴파일(planning 1콜 — 드문 작가 액션이라 락 보유 허용).
                #  실패는 기존 계약 유지+이벤트 — 지문 불일치가 stale 로 가시화된다(개정 반영 차단 금지).
                try:
                    from ..engine.ending_contract import compile_contract
                    newc = compile_contract(self.planning_provider, state,
                                            sess.bundle.ontology, source="revise_spine")
                    if newc.error:
                        sess.bus.emit("ending_contract", "compile_failed",
                                      source="revise_spine", reason=newc.error[:120])
                    else:
                        state.ending_contract = newc
                        sess.bus.emit("ending_contract", "compiled", source="revise_spine",
                                      predicates=len(newc.predicates),
                                      unexpressed=len(newc.unexpressed))
                except Exception as e:
                    sess.bus.emit("ending_contract", "compile_failed",
                                  source="revise_spine", reason=type(e).__name__)
            if applied:
                self.repo.save(state)
                # FI-1: 캐논 정정 표면(스파인 개정 — 미집필 아크/엔딩 방향). 실제 반영(applied 있음)만 emit. 본문 전문
                #   금지(applied 는 target/field 소형 요약 — new_value 원문은 비저장, 스팸/팽창 방어). rejected 는 수만.
                emit_intent(self.repo, self.settings, pid, 0, "spine_revise",
                            payload={"approx_chapter": state.current_chapter, "applied": applied,
                                     "rejected": len(rejected)})
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

    def bible_snapshot(self, pid: str, offset: int | None = None,
                       limit: int | None = None) -> dict | None:
        """설정집 스냅샷. offset/limit 미지정=전 항목(기존 응답과 동일 — 구 클라이언트 무회귀),
        지정 시 그 window 만. total 은 항상 실어 화면이 남은 개수를 안다."""
        state = self.repo.get(pid)
        if not state:
            return None
        if not state.bible_migrated:     # 최초 1회 부트스트랩만 lock 안에서(GET 경로 무락 쓰기 = 동시 promote 와 lost-update 위험 제거)
            sess = self.sessions.get_or_create(state)
            with sess.lock:
                state = self.repo.get(pid)
                if not state:
                    return None
                sess = self._resolve_locked_session(sess, state)
                if self._ensure_migrated(state):
                    self.repo.save(state)
        entries = list(state.bible.entries)
        total = len(entries)
        off = max(0, int(offset or 0))
        window = entries[off:off + max(1, min(500, int(limit)))] if limit is not None else entries[off:]
        return {"genre": state.world.genre, "template": template_for(state.world.genre),
                "category_labels": CATEGORY_LABEL,
                "total": total, "offset": off, "has_more": off + len(window) < total,
                "entries": [{"entry_id": e.entry_id, "category": e.category,
                             "category_label": CATEGORY_LABEL.get(e.category, e.category),
                             "title": e.title, "prose": e.prose, "promoted": e.promoted,
                             "keywords": list(getattr(e, "keywords", []) or []),
                             "provenance": e.provenance, "status": e.status} for e in window]}

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
            sess = self._resolve_locked_session(sess, state)
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
            sess = self._resolve_locked_session(sess, state)
            e = state.bible.get(entry_id)
            if not e:
                raise ValueError("설정집 항목 없음")
            _bt, _bc, _bp = e.title, e.category, len(e.prose or "")   # FI-1: before→after 소형 요약용(본문 전문 금지 — 길이만)
            if title is not None:
                e.title = title
            if prose is not None:
                e.prose = prose
            if category:
                e.category = normalize_category(category)
            e.status = "author_approved"
            self.repo.save(state)
            # FI-1: 캐논 정정 표면(설정집 편집 — 작가가 생성 오류를 손수 바로잡는 최고 신호). before→after 소형 요약
            #   (prose 전문 금지 — 길이만·§2). 어느 필드를 건드렸나(changed)도 기록.
            emit_intent(self.repo, self.settings, pid, 0, "bible_edit",
                        payload={"approx_chapter": state.current_chapter, "entry_id": entry_id,
                                 "title_before": _bt, "title_after": e.title,
                                 "category_before": _bc, "category_after": e.category,
                                 "prose_len_before": _bp, "prose_len_after": len(e.prose or ""),
                                 "changed": [k for k, v in (("title", title), ("prose", prose),
                                                            ("category", category)) if v is not None and v != ""]})
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
            sess = self._resolve_locked_session(sess, state)
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
            sess = self._resolve_locked_session(sess, state)
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
            # FI-1: 캐논 정정 표면(설정집 승격 — 항목을 세계규칙 캐논으로 박음). 실제 승격만 emit(이미 promoted=early return).
            emit_intent(self.repo, self.settings, pid, 0, "bible_promote",
                        payload={"approx_chapter": state.current_chapter, "entry_id": entry_id,
                                 "title": e.title, "rule_id": rule.rule_id})
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
            sess = self._resolve_locked_session(sess, st)
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
            # ON-2 갭 수리(2026-08-07 통합 점검 실측): 키 등록이 세션 온톨로지에만 남으면 다음 세션의
            #   canon_facts 가 새 축을 건너뛴다 — 영속 개체(attrs)에도 등록해야 승인 축이 재기동 후에도 주입된다.
            for _e in list(st.world.entities) + list(getattr(st, "runtime_entities", None) or []):
                if getattr(_e, "id", None) == entity_id:
                    if getattr(_e, "attrs", None) is None:
                        _e.attrs = {}
                    _e.attrs.setdefault(attr, None)
                    break
            ont.set_state(entity_id, attr, sval, eff, reason=reason, trust_tier="ground_truth")
            from ..domain.world import TimelineEntry
            st.runtime_timeline = _dedup_timeline(st.runtime_timeline + [TimelineEntry(
                entity_id=entity_id, attr=attr, value=sval, eff_from=eff, reason=reason,
                trust_tier="ground_truth", provenance=["author"])])   # 작가 정정이 같은 시점 충돌값을 *교체*(추가 아님) → ssot 봉인 복구 가능. ON-2 U7: 승인 경유 표기
            sess.snapshot_into(st)
            self.repo.save(st)
            # FI-1: 캐논 정정 표면(엔티티 상태 정정 — 작가가 낡은/틀린 캐논 속성을 직접 박음). before(cur)→after(sval) 요약.
            emit_intent(self.repo, self.settings, pid, 0, "entity_state",
                        payload={"approx_chapter": st.current_chapter, "entity_id": entity_id,
                                 "entity": ont.name(entity_id), "attr": attr,
                                 "before": (None if cur is None else str(cur)), "after": sval,
                                 "eff_from": eff, "reason": trim_text(reason or "")})
            return {"updated": True, "entity": ont.name(entity_id), "attr": attr,
                    "value": sval, "eff_from": eff}

    # ==== 스킬 — 전역 라이브러리(메인 화면 등록) + 작품별 주입(참조형 live SSOT) ====
    # 라이브러리(self.registry)에 정의가 살고, 작품은 ProjectState.injected_skills(id 목록)로 *주입*만 한다.
    # 라이브러리 편집 → 주입한 모든 작품의 다음 회차부터 반영. 과거 회차는 gen_context['skills'] 에 동결(감사).

    def _effective_skills(self, state) -> list:
        """이 작품에 주입된 스킬을 라이브러리에서 해소 → enabled 사본 목록(끊긴 참조는 건너뜀).
        미이관(legacy) 작품은 인라인 enabled 스킬도 폴백 포함 → 이관 전에도 주입이 누락되지 않음."""
        out = [s.model_copy(update={"enabled": True})
               for s in self.registry.resolve(getattr(state, "injected_skills", None) or [])]
        if not getattr(state, "skills_migrated", False):
            have = {s.id for s in out}
            for s in (getattr(state, "skills", None) or []):
                if getattr(s, "enabled", False) and s.id not in have:
                    out.append(s)
        return out

    def _migrate_skills(self, st) -> bool:
        """인라인 skills → 전역 라이브러리 1회 이관(멱등). 커스텀은 라이브러리로 승격, 내장은 이미 id로 존재.
        enabled 였던 것은 injected_skills 로 보존(원래 순서 유지 → apply 의 [:CAP] 선택이 이관 전후 동일). 변경 여부."""
        if getattr(st, "skills_migrated", False):
            return False
        promoted = [s.id for s in (st.skills or []) if getattr(s, "enabled", False)]   # 원래 순서 보존
        for s in (st.skills or []):
            if not getattr(s, "builtin", False):
                self.registry.add_existing(s)        # 커스텀 정의를 라이브러리로 승격(id 충돌 시 기존 유지)
        st.injected_skills = list(dict.fromkeys((st.injected_skills or []) + promoted))
        st.skills_migrated = True
        return True

    def list_skills(self, pid: str) -> dict | None:
        """이 작품의 주입 화면용 — 라이브러리 전체 + 작품별 injected 플래그/슬롯순서."""
        st = self.repo.get(pid)
        if not st:
            return None
        if not getattr(st, "skills_migrated", False):
            self._skills_write(pid, lambda s: None)   # 락 안에서 1회 이관(생성 중이면 False — 다음 진입에 이관)
            st = self.repo.get(pid) or st
        injected = list(getattr(st, "injected_skills", None) or [])
        order = {sid: i for i, sid in enumerate(injected)}
        out = []
        for s in self.registry.list():
            d = s.model_dump()
            d["injected"] = s.id in order
            d["slot"] = order.get(s.id, -1)
            out.append(d)
        return {"skills": out, "injected": injected}

    def _skills_write(self, pid: str, mutate):
        """작품별 주입 상태 변경 — 생성 중(sess.lock 보유)이면 False(라우트 423). lost-update 방지(revise 패턴)."""
        st = self.repo.get(pid)
        if not st:
            return None
        sess = self.sessions.get_or_create(st)
        if not sess.lock.acquire(blocking=False):
            return False
        try:
            st = self.repo.get(pid)
            if not st:
                return None
            sess = self._resolve_locked_session(sess, st)
            self._migrate_skills(st)          # 락 안에서 멱등 이관(공유 라이브러리 승격은 registry 자체 락이 보호)
            out = mutate(st)
            self.repo.save(st)
            return out
        finally:
            sess.lock.release()

    def inject_skill(self, pid: str, sid: str):
        """라이브러리 스킬을 이 작품에 주입(=membership). 합성 상한은 apply 시점 advisory(여기서 하드차단 안 함)."""
        if not self.registry.get(sid):
            raise ValueError("존재하지 않는 스킬")
        def m(st):
            if sid not in (st.injected_skills or []):
                st.injected_skills = list(st.injected_skills or []) + [sid]
            return {"id": sid, "injected": True, "injected_skills": st.injected_skills}
        return self._skills_write(pid, m)

    def eject_skill(self, pid: str, sid: str):
        def m(st):
            st.injected_skills = [x for x in (st.injected_skills or []) if x != sid]
            return {"id": sid, "injected": False, "injected_skills": st.injected_skills}
        return self._skills_write(pid, m)

    # ---- 전역 라이브러리 CRUD(작품 무관 — registry 자체 락, 회차 생성 423에 막히지 않음) ----
    def library_list(self) -> dict:
        return {"skills": [s.model_dump() for s in self.registry.list()]}

    def library_create(self, data: dict) -> dict:
        return self.registry.create(data).model_dump()

    def library_update(self, sid: str, data: dict) -> dict:
        return self.registry.update(sid, data).model_dump()

    def library_delete(self, sid: str) -> dict:
        res = self.registry.delete(sid)       # 내장/미존재면 ValueError
        self._cascade_eject(sid)              # 주입돼 있던 작품에서 제거(끊긴 참조 정리 — resolve 가 이미 dangling-safe라 best-effort)
        return res

    def _cascade_eject(self, sid: str) -> int:
        """삭제된 라이브러리 스킬을 주입했던 작품들에서 제거 — *반드시* 작품 세션 락 경유(_skills_write).
        bare get/save 로 하면 생성 중 작품의 진행본(회차·원장·온톨로지)을 덮어쓰는 lost-update(적대검증 high).
        생성 중이면 _skills_write 가 False(스킵) → dangling id 가 남지만 resolve/_effective_skills 가 건너뛰므로 안전(정리는 best-effort)."""
        n = 0
        def _drop(st):
            if sid in (st.injected_skills or []):
                st.injected_skills = [x for x in st.injected_skills if x != sid]
                return True
            return False
        for summ in self.repo.list_summaries():
            try:
                st = self.repo.get(summ["id"])
                if not (st and sid in (getattr(st, "injected_skills", None) or [])):
                    continue                              # 영향 없는 작품은 건드리지 않음(불필요 저장·이관 회피)
                if self._skills_write(summ["id"], _drop) is True:   # 락 안 재읽기·생성 중이면 False
                    n += 1
            except Exception:
                continue
        return n

    # ---- 레거시 호환(구 프론트/엔드포인트) — 전역 라이브러리 모델로 위임 ----
    def set_skill_enabled(self, pid: str, sid: str, enabled: bool):
        return self.inject_skill(pid, sid) if enabled else self.eject_skill(pid, sid)

    def create_skill(self, pid: str, data: dict):
        sk = self.registry.create(data)       # 라이브러리에 등록 후
        res = self.inject_skill(pid, sk.id)   # 이 작품에 주입(생성 중이면 False — 라이브러리엔 남음)
        if res in (None, False):
            return res
        return {**sk.model_dump(), "injected": True}

    def delete_skill(self, pid: str, sid: str):
        return self.eject_skill(pid, sid)     # 작품에서 빼기(라이브러리 삭제는 /api/skills DELETE)

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
            sess = self._resolve_locked_session(sess, st)
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
                style.author_style = (patch["author_style"] or "").strip()   # 빈 문자열=오버레이 해제. 절단 전면 제거(2026-08-21): 작가 입력 전문 저장(길이는 작가 주권)
            if patch.get("narrator_voice") is not None:
                style.narrator_voice = (patch["narrator_voice"] or "").strip()   # DP-17 화자 보이스 작가 편집(빈 문자열=해제), first 시점에서 render_style 이 소비. 절단 전면 제거(2026-08-21): 작가 입력 전문
            if patch.get("narrator_voice_stage_attr") is not None:
                # VB-1 상태 연동 보이스의 결속 축(빈 문자열=해제 → 단계 조회 자체가 없어 정적 voice 그대로).
                #   추적 속성 목록에 없는 키는 조회가 영구 공회전이라 거절한다(조용한 무동작 방지).
                _sa = (patch["narrator_voice_stage_attr"] or "").strip()
                if _sa and _sa not in {a.key for a in st.world.attributes}:
                    raise ValueError("추적 속성 목록에 없는 키입니다")
                style.narrator_voice_stage_attr = _sa

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

    # ---- 보이스(목소리) 조회·편집 — 설정집 노출 + 작가 편집(③ 작가 입력 전용) ----
    #   생성이 실제로 읽는 값은 셋이고 저장 위치가 서로 다르다:
    #     · 화자 보이스   world.style.narrator_voice        (DP-17 — prompts.render_style, first 시점)
    #     · 음성 카드     EntitySpec.voice                  (ST-12a — harness 서술자 프레임/인물 말투)
    #     · 단계 카드     EntitySpec.voice_stages           (VB-1 — style.narrator_voice_stage_attr 로 조회)
    #   이 셋이 어느 화면에도 안 나와 작가가 자기 작품의 목소리를 볼 수도 고칠 수도 없었다. 한 조회로 모은다.
    VOICE_CAP = 1200          # 카드 1장 길이 상한 — 파생 카드(600자)보다 넉넉하되 매 회차 프롬프트 주입이라 짧게
    VOICE_STAGE_CAP = 24      # 단계 카드 개수 상한(상태 축 하나의 현실적 단계 수)

    def _actor_pred(self, world):
        """Ontology.is_actor 동형 판정을 세션 없이 — 작품 카탈로그(비면 BUILTIN) category=='actor'."""
        types = {t.key: t for t in (world.entity_types or BUILTIN_ENTITY_TYPES)}

        def _is_actor(etype: str) -> bool:
            t = types.get(etype)
            return (t.category == "actor") if t is not None else (etype == "character")
        return _is_actor

    def voice_snapshot(self, pid: str) -> dict | None:
        """작품의 목소리 축 통합 조회(읽기 전용·LLM 0콜). 없는 작품=None.

        영속 SSOT 인 EntitySpec(world.entities + runtime_entities)에서 읽는다 — 라이브 세션 ontology 는
        이 값들의 미러라 편집 시 함께 갱신되고, 세션 재구성 때 factory 가 다시 여기서 채운다.
        서술자 판정은 narrator_voice.find_protagonist_id 와 동형(삽입순 첫 actor) — 1인칭에서만 발화한다."""
        state = self.repo.get(pid)
        if not state:
            return None
        world = state.world
        is_actor = self._actor_pred(world)
        runtime = list(getattr(state, "runtime_entities", None) or [])
        runtime_ids = {e.id for e in runtime}
        specs = list(world.entities) + runtime           # ontology 삽입순과 동일(world → runtime)
        first_actor = next((e.id for e in specs if is_actor(e.etype)), "")
        is_first = getattr(world.style, "pov", "") == "first"
        stage_attr = (getattr(world.style, "narrator_voice_stage_attr", "") or "").strip()
        # 단계 축 후보 — 값 목록이 있는 추적 속성(state/status/categorical)만. 단계 카드의 key 가 이 값이 된다.
        options = [{"key": a.key, "label": a.label, "kind": a.kind,
                    "values": list(a.states or a.vocab or [])}
                   for a in (world.attributes or []) if (a.states or a.vocab)]
        return {
            "pov": getattr(world.style, "pov", ""),
            "narrator_voice": getattr(world.style, "narrator_voice", "") or "",
            "narrator_voice_stage_attr": stage_attr,
            "narrator_id": first_actor if is_first else "",
            "stage_attr_options": options,
            "stage_values": next((o["values"] for o in options if o["key"] == stage_attr), []),
            "voice_card_enabled": bool(getattr(self.settings, "narrator_voice", True)),
            "entities": [{"id": e.id, "name": e.name, "etype": e.etype,
                          "is_actor": is_actor(e.etype),
                          "is_narrator": bool(is_first and e.id == first_actor),
                          "voice": getattr(e, "voice", "") or "",
                          "voice_stages": dict(getattr(e, "voice_stages", None) or {}),
                          "profile": getattr(e, "profile", "") or "",   # 절단 전면 제거(2026-08-21): 작가 열람 전문(실측 프로필 최대 879자 — 구 400 컷 실발동)
                          "runtime": e.id in runtime_ids} for e in specs],
        }

    def update_entity_voice(self, pid: str, eid: str, voice: str | None = None,
                            voice_stages: dict | None = None) -> dict | None:
        """인물 보이스 카드·단계 카드 작가 직접 편집. None 필드는 무변경(부분 수정), 빈 문자열=해제.

        영속 SSOT 는 EntitySpec 이고 라이브 세션 ontology 도 즉시 미러링한다(ST-12a 파생 경로 동형).
        적용 후 evict → 다음 요청이 새 카드로 엔진 재구성(update_style_policy 관행)."""
        state = self.repo.get(pid)
        if not state:
            return None
        sess = self.sessions.get_or_create(state)
        with sess.lock:
            state = self.repo.get(pid)                    # 권위 재읽기(lost update 방지)
            if not state:
                return None
            sess = self._resolve_locked_session(sess, state)
            spec = next((e for e in list(state.world.entities) + list(state.runtime_entities)
                         if e.id == eid), None)
            if spec is None:
                raise ValueError("그 인물을 찾을 수 없습니다")
            if voice is not None:
                spec.voice = (voice or "").strip()[:self.VOICE_CAP]
            if voice_stages is not None:
                if len(voice_stages) > self.VOICE_STAGE_CAP:
                    raise ValueError(f"단계 카드는 최대 {self.VOICE_STAGE_CAP}개까지입니다")
                spec.voice_stages = {str(k): str(v).strip()[:self.VOICE_CAP]
                                     for k, v in voice_stages.items() if str(v).strip()}   # 빈 카드=그 단계 해제
            live = getattr(sess, "bundle", None) and sess.bundle.ontology.entities.get(eid)
            if live is not None:
                if voice is not None:
                    live.voice = spec.voice
                if voice_stages is not None:
                    live.voice_stages = dict(spec.voice_stages)
            self.repo.save(state)
        self.sessions.evict(pid)
        return {"updated": True, "id": eid, "name": spec.name,
                "voice": spec.voice, "voice_stages": dict(spec.voice_stages)}

    def update_project_meta(self, pid: str, title: str | None = None,
                            premise: str | None = None, synopsis: str | None = None) -> dict | None:
        """작품 메타(제목·한 줄 소개·소개) 작가 직접 수정 — 설정집/스타일 PUT 계보(작가 확정 수정·무강제).

        표시·컨텍스트의 SSOT 는 world(title/premise/synopsis)다 — seed 는 최초 시드의 이력이라 불변으로
        남긴다(작명 당시 무엇을 던졌는지의 정직 기록). 자동 검증·재생성 0: premise/synopsis 는 이후 회차
        생성 컨텍스트에 자연 반영되는 것이 의도된 동작이다. None 필드는 무변경(부분 수정), 제목만 빈 값 거절.
        적용 후 세션 evict → 다음 요청이 새 메타로 엔진 재구성(update_style_policy 관행)."""
        st = self.repo.get(pid)
        if not st:
            return None
        sess = self.sessions.get_or_create(st)
        with sess.lock:
            st = self.repo.get(pid)
            if not st:
                return None
            sess = self._resolve_locked_session(sess, st)
            if title is not None:
                t = title.strip()
                if not t:
                    raise ValueError("제목이 비어 있습니다")
                st.world.title = t   # 절단 전면 제거(2026-08-21): 작가 입력 전문(구 200/500/4000 캡 — 특히 premise 500 은 컨셉 경로 4000 과도 불일치)
            if premise is not None:
                st.world.premise = premise.strip()
            if synopsis is not None:
                st.world.synopsis = synopsis.strip()
            self.repo.save(st)
        self.sessions.evict(pid)   # 다음 요청부터 새 메타로 엔진 재구성
        return {"updated": True, "title": st.world.title,
                "premise": st.world.premise, "synopsis": st.world.synopsis}

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
            sess = self._resolve_locked_session(sess, state)
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
            for nb in (res.get("new_bible") or [])[:2]:   # VA-3: 프롬프트 상한(2)과 일치
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

    def wiki_snapshot(self, pid: str, offset: int | None = None,
                      limit: int | None = None) -> dict | None:
        """작품 노트 스냅샷. offset/limit 미지정=전 페이지(기존 응답과 동일), 지정 시 그 window 만.
        자동 점검(lint)은 전량 유지 — 목록이 아니라 작품 전체 진단이라 잘라 보내면 뜻이 달라진다."""
        sess, state = self.get_session(pid)
        if not sess:
            return None
        wm = state.current_chapter
        # XR-29(015 §6): 목록과 lint 가 같은 세대를 보도록 참조를 '한 번' 캡처 — 재구축 교체가 두 읽기 사이에
        #   끼면 화면 목록과 진단이 다른 세대 기준이 되던 창 봉합(무락 유지 — 생성 중 UI 매달림 방지).
        pages_ref = sess.bundle.wiki.pages
        pages = [WikiPage.model_validate(p).model_dump() if not isinstance(p, WikiPage) else p.model_dump()
                 for p in pages_ref.values()]
        total = len(pages)
        off = max(0, int(offset or 0))
        window = pages[off:off + max(1, min(500, int(limit)))] if limit is not None else pages[off:]
        lint = [v.model_dump() for v in sess.bundle.wiki.lint(wm, pages=pages_ref)]
        return {"watermark": wm, "pages": window, "lint": lint,
                "total": total, "offset": off, "has_more": off + len(window) < total}

    # ---- XR-5: 추적 속성의 '자동 확정 기준' 작가 오버라이드(③ 작가 입력 전용) ----
    def set_attribute_auto_commit(self, pid: str, key: str, auto_commit: str) -> dict | None:
        """AttributeSpec.auto_commit 선언(""|binding|non_binding). 저장 + 세션 축출 + 의도 기록.

        기존 set_entity_state(값 하나를 작가가 박음)와 층이 다르다 — 이건 '이 축의 동적 감지를 앞으로
        어떻게 착지시킬지'의 정책 선언이다. **소급 없음**: 이미 박힌 타임라인 엔트리는 그대로 두고 다음
        회차 커밋부터 적용된다(설계 §2.4 — 일괄 강등은 [확정 설정] 대량 이탈로 연속성 파괴 위험).
        개별 소급 정정은 기존 레버(set_entity_state)가 담당한다."""
        st = self.repo.get(pid)
        if not st:
            return None
        val = (auto_commit or "").strip()
        if val not in ("", "binding", "non_binding"):
            raise ValueError("자동 확정 기준은 '', 'binding', 'non_binding' 중 하나여야 합니다")
        sess = self.sessions.get_or_create(st)
        with sess.lock:
            st = self.repo.get(pid)
            if not st:
                return None
            sess = self._resolve_locked_session(sess, st)
            spec = next((a for a in (st.world.attributes or []) if a.key == key), None)
            if spec is None:
                raise ValueError("추적 속성 목록에 없는 키입니다")
            before = getattr(spec, "auto_commit", "")
            spec.auto_commit = val
            self.repo.save(st)
        self.sessions.evict(pid)   # 다음 요청부터 새 선언으로 엔진(Vocabulary) 재구성
        # FI-1: 캐논 정정 표면(속성 티어 선언 — 무엇이 자동으로 [확정 설정]에 박히는지의 정책 변경)
        emit_intent(self.repo, self.settings, pid, 0, "attribute_tier",
                    payload={"approx_chapter": st.current_chapter, "attr": key,
                             "label": spec.label, "before": before, "after": val})
        return {"updated": True, "attr": key, "label": spec.label, "auto_commit": val}

    def record_tier_review(self, pid: str, entity_id: str, attr: str, eff_from: int,
                           decision: str, note: str = "") -> dict | None:
        """XR-19(cross-review/009 §9): 기계 binding 캐논 1건에 대한 작가 판정 기록(append-only 원장).

        decision: approve(이 값이 맞음 — 유지) | dismiss(오추출 — 참고 안 함 표기) | hold(보류).
        **기록일 뿐 값을 자동으로 바꾸지 않는다**(무강제) — 실제 대체·정정은 기존 set_entity_state 가 담당
        (timeline append 가 supersedes 이력). tier_report 가 이 원장을 조인해 검토 진행도를 보여 준다."""
        st = self.repo.get(pid)
        if not st:
            return None
        d = (decision or "").strip()
        if d not in ("approve", "dismiss", "hold", "clear"):
            raise ValueError("판정은 approve, dismiss, hold, clear 중 하나여야 합니다")
        if d == "clear":
            d = ""   # 판정 취소(012 §7 권장 4) — 빈 판정을 append, latest-wins 읽기가 미판정으로 되돌린다
        sess = self.sessions.get_or_create(st)
        with sess.lock:
            st = self.repo.get(pid)
            if not st:
                return None
            sess = self._resolve_locked_session(sess, st)
            # XR-25(012 §7): 검토 대상은 '기계 감지 binding'뿐 — 작가 확정(provenance author)·비구속 엔트리에
            #   기록하면 리포트에 안 나타나는 고아 원장이 된다(같은 3중키에 tier 다른 엔트리가 공존 가능하므로
            #   존재 검사가 아니라 대상 자격 검사). 리포트(machine_binding_report)의 계수 기준과 동일 술어.
            if not any(getattr(t, "entity_id", "") == entity_id and getattr(t, "attr", "") == attr
                       and getattr(t, "eff_from", None) == eff_from
                       and (getattr(t, "trust_tier", "ground_truth") or "ground_truth") == "ground_truth"
                       and "author" not in (list(getattr(t, "provenance", None) or ["machine"]))
                       for t in st.runtime_timeline):
                raise ValueError("검토 대상이 아닙니다 — 판정은 '본문 감지로 확정된(기계 binding)' 값에만 기록합니다")
            st.tier_review.append({"entity_id": entity_id, "attr": attr, "eff_from": eff_from,
                                   "decision": d, "note": (note or "")[:200],
                                   "at": st.current_chapter})
            self.repo.save(st)
            sess.bus.emit("ontology", "tier_review", entity=entity_id, attr=attr,
                          eff_from=eff_from, decision=d)
        return {"recorded": True, "entity_id": entity_id, "attr": attr, "eff_from": eff_from,
                "decision": d, "total_reviews": len(st.tier_review)}
