# -*- coding: utf-8 -*-
"""LLM Wiki 레이어 — 서사/의미 절반을 점진 유지(컴파운딩 위키).

- finalize 시 인물 페이지 '점진 갱신'(전체 재생성 금지). 멱등 ingest(log grep).
- lint(): orphan/dangling/stale 을 순수 결정론 그래프 순회로(LLM 0콜) = 등급1.
- retrieve(): narrative 슬롯으로만. 결정론 코어는 위키를 1바이트도 안 읽는다.
provider 주입. 영속화 export/import(인물 페이지·로그).
"""
from __future__ import annotations
import json
import re
import numpy as np

# VH-1(2026-08-16 감사 F-3ⓑ): retrieve 앵커에서 따옴표 인용 스팬 제거용 — '대사:' 줄 프리픽스 필터를
# 우회해 '말투:' 줄 괄호 안에 실린 인용("2형 장당 8만입니다" 실측)이 few-shot 앵커로 새는 구멍 봉합.
_QUOTE_SPAN_RE = re.compile(r'"[^"\n]{2,}"|“[^”\n]{2,}”')

from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..domain.types import (WikiPage, WikiLifecycle, TypedEdge, Violation, SignalGrade, RetrievedItem,
                            text_fingerprint)
from ..llm.base import LLMProvider


class Wiki:
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.pages: dict[str, WikiPage] = {}
        self.log: list[str] = []
        self._emb_cache: dict[str, tuple] = {}

    def seed_page(self, page: WikiPage) -> None:
        self.pages[page.page_id] = page

    def _already(self, chapter: int) -> bool:
        return any(e.startswith(f"[{chapter}]") for e in self.log)

    @promptlog.stage("wiki_ingest")
    def ingest_chapter(self, chapter: int, text: str, ontology, reviewed: bool = True,
                       pov_entity_id: str = "", force: bool = False) -> int:
        """CX-4 화자 결속: 1인칭 화자('나')는 이름이 본문에 안 나오면 scan_present_ids 로스터에서 빠져
        갱신기가 화자의 행동·감정·비밀을 곁 인물 카드에 적는다(오염 루프 — 재구축 재현으로 확정된 코드 결함).
        pov_entity_id 가 오면 화자를 로스터에 무조건 포함하고 프롬프트에 화자 명시 한 줄을 얹는다.
        미전달("")=기존 동작·프롬프트 바이트 동일(3인칭 하위호환).

        force(XR-7③): True 면 멱등 가드(_already)를 우회해 같은 회차를 다시 적재한다 — 퇴고로 본문이 바뀐
        회차의 인물카드를 *작가가 발동해* 최신 본문으로 다시 합성하는 유일한 경로다(자동 호출 금지). 이 회차의
        기존 로그 항목은 걷어내고 새로 적는다(멱등 기록 유지). 기본 False = 기존 동작·프롬프트 바이트 동일."""
        if self._already(chapter):
            if not force:
                return 0
            self.log = [e for e in self.log if not e.startswith(f"[{chapter}]")]
        present = [ontology.entities[i] for i in ontology.scan_present_ids(text)]
        chars = [e for e in present if e.etype == "character"]
        pov_e = ontology.entities.get(pov_entity_id) if pov_entity_id else None
        if pov_e is not None and all(e.id != pov_e.id for e in chars):
            chars = [pov_e] + chars   # 화자 강제 포함(선두 — 카드 귀속 우선순위 신호)
        if not chars:
            self.log.append(f"[{chapter}][-][noop]")
            return 0
        # 페이지 본문 비대 통제: 입력은 최근 1,200자만 노출 + 출력 800자 유지 지시(무한 누적 → JSON 절단 방지)
        current = {e.id: (self.pages[e.id].body[-1600:] if e.id in self.pages else "") for e in chars}
        roster = [{"id": e.id, "name": e.name} for e in chars]
        pov_line = (f"이 회차의 서술자('나')는 {pov_e.name} 이다 — '나'의 행동·감정·비밀은 "
                    f"반드시 {pov_e.name} 카드에만 적고 다른 인물 카드에 적지 마라. "
                    if pov_e is not None else "")
        msg = [
            {"role": "system", "content":
             "너는 작품 바이블의 인물카드 관리자다. 각 인물 카드를 이번 회차를 반영해 갱신하라. " + pov_line +
             "카드는 반드시 이 구조(줄 단위, 인물당 1,000자 이내 — 넘으면 오래된 디테일 압축):\n"
             "상태: <현재 처지·위치·몸 상태 한 줄>\n감정: <현재 감정과 그 원인>\n목표: <지금 원하는 것/두려운 것>\n"
             "관계: <주요 인물별 현재 긴장·온도 — 예: A에게 경계가 누그러짐>\n"
             # VH-1(2026-08-16 감사 F-3ⓐ): 관찰 명제 문형('보고체 유지'류)+'문장 길이' 축(VS-1 위반 소스)을
             #   연기 지시 문형으로 교체 — 카드가 컨텍스트에 실리는 순간 관찰 명제는 지문이 된다(VL-1 실측).
             # PL-2(2026-08-17 감사 F10): 긍정 문형만으로 형태어 재발("짧고 끊기는") — 답안 꼴을 지정해 잠근다.
             #   생성면 주입은 retrieve 가 '말투:' 줄을 결정론 제외하므로(F12) 이 줄은 작가 열람 전용이다.
             "말투: <두 가지. 각 항은 '…할 때는 …한다' 꼴로, 상대와 상황을 앞에 적고 그 자리에서 인물이 하는 일을 뒤에 적는다: 무엇부터 꺼내는지, 무엇을 되묻는지, 대답을 미룰 때 무엇을 하는지>\n"
             "대사: <이 인물다운 실제 대사 인용 2~3개(본문에서) — 서로 다른 장면·상대에서 고른 다양한 문형으로>\n"
             "비밀: <아는 것/모르는 것 비대칭>\n"
             "설정 수치(눈색·등급 등)는 적지 마라(온톨로지 소유). JSON: {\"pages\":[{\"id\":..,\"body\":\"갱신된 카드 전문\"}]}"},
            {"role": "user", "content":
             f"[인물]\n{json.dumps(roster, ensure_ascii=False)}\n[기존 페이지]\n{json.dumps(current, ensure_ascii=False)}\n"
             f"[{chapter}화 본문]\n{text}"},
        ]
        res = self.provider.chat_json(msg, temperature=0.2)
        n = 0
        ids = {e.id for e in chars}
        for pg in res.get("pages", []):
            pid = pg.get("id")
            if pid not in ids:
                continue
            page = self.pages.get(pid) or WikiPage(page_id=pid, page_type="character")
            page.body = pg.get("body", page.body)
            page.as_of_narrative_order = chapter
            page.lifecycle = WikiLifecycle.ACTIVE if reviewed else WikiLifecycle.DRAFT
            page.trust_tier = "wiki_synthesized" if reviewed else "unreviewed_machine"
            if f"{chapter}화" not in " ".join(page.provenance):
                page.provenance.append(f"{chapter}화")
            # XR-18(cross-review/009 §8): 반영 회차의 본문 지문을 페이지에 기록 — 재구축·확정·재계산 전 경로
            #   단일 지점(여기). 소비 불변식(생성 전 현재 본문과 대조)의 원자료. additive — 구 페이지는 빈 dict.
            page.source_fingerprints[str(chapter)] = text_fingerprint(text)
            self.pages[pid] = page
            self.log.append(f"[{chapter}][{pid}][update]")
            n += 1
        return n

    def add_edge(self, page_id: str, edge: TypedEdge) -> None:
        if page_id in self.pages:
            self.pages[page_id].typed_edges.append(edge)
            self.log.append(f"[{edge.source_narrative_order}][{page_id}][edge:{edge.type}]")

    def lint(self, watermark: int, pages: dict | None = None) -> list[Violation]:
        """순수 결정론(등급1, LLM 0콜): orphan 복선 / dangling 엣지 / stale.

        pages(XR-29·015 §6): 복합 조회(목록+lint)가 같은 세대를 보도록 호출부가 캡처한 참조를 넘길 수 있다
        — 미전달=self.pages(종전 동작·바이트 동일). 본문 내 읽기도 전부 이 단일 참조를 쓴다(재읽기 0)."""
        pages = self.pages if pages is None else pages
        viols: list[Violation] = []
        for p in pages.values():
            for e in p.typed_edges:
                if e.target_page_id not in pages:
                    viols.append(Violation(entity=p.page_id, kind="wiki_dangling_edge",
                                           grade=SignalGrade.DETERMINISTIC, canon=p.page_id,
                                           text=f"{e.type}→{e.target_page_id}(없음)",
                                           evidence="엣지 target 페이지 부재"))
            if p.page_type == "plot_thread":
                has_payoff = any(e.type == "payoff_of" for e in p.typed_edges)
                pd = p.payoff_deadline
                if not has_payoff and pd is not None and watermark >= pd:
                    viols.append(Violation(entity=p.page_id, kind="wiki_orphan_thread",
                                           grade=SignalGrade.DETERMINISTIC, canon=f"회수기한 {pd}화",
                                           text=f"watermark {watermark}화, payoff 엣지 없음",
                                           evidence="미회수 복선(경고)"))
            if p.lifecycle == WikiLifecycle.ACTIVE and watermark - p.as_of_narrative_order > 3:
                viols.append(Violation(entity=p.page_id, kind="wiki_stale",
                                       grade=SignalGrade.DETERMINISTIC, canon=f"as_of {p.as_of_narrative_order}화",
                                       text=f"watermark {watermark}화", evidence="3화 이상 미갱신"))
        return viols

    def retrieve(self, query: str, as_of: int, k: int = 3) -> list[RetrievedItem]:
        active = [p for p in self.pages.values()
                  if p.body and p.as_of_narrative_order <= as_of and p.lifecycle == WikiLifecycle.ACTIVE]
        if not active:
            return []
        q = np.array(self.provider.embed([query])[0], dtype=np.float32)
        qn = q / (np.linalg.norm(q) + 1e-9)
        scored = []
        for p in active:
            h = hash(p.body)
            if self._emb_cache.get(p.page_id, (None,))[0] != h:
                self._emb_cache[p.page_id] = (h, np.array(self.provider.embed([p.body])[0], dtype=np.float32))
            e = self._emb_cache[p.page_id][1]
            scored.append((float(np.dot(e, qn) / (np.linalg.norm(e) + 1e-9)), p))
        scored.sort(key=lambda x: x[0], reverse=True)
        # CX-2: 카드의 '비밀:' 줄은 생성 앵커 반환에서 제거(카드 저장은 원형 유지 — 작가·플래너 소관).
        #   반전 재료가 집필 프롬프트로 새는 3번째 소스 차단.
        # AP-1: '대사:' 인용 줄도 앵커에서 제거 — 수집기가 '이 인물다운 대사'로 경구·펀치라인만 골라 모으고
        #   그것이 few-shot 시연으로 재주입돼 잠언투 대사가 증식하는 닫힌 루프(사용자 정독 실측). 카드 저장은
        #   유지(작가 열람용).
        # VH-1(2026-08-16 감사 F-3ⓑ): 줄 프리픽스 필터만으로는 '말투:' 줄 안에 괄호로 실린 인용이 그대로
        #   샜다(하지연 페이지 "2형 장당 8만입니다" 실측) — 잔여 전 줄에서 따옴표 스팬을 추가 제거(결정론).
        # PL-2(2026-08-17 감사 F12): '말투:' 줄도 생성면 앵커에서 결정론 제외 — 어체·연기는 온톨로지 보이스
        #   카드(작가 소유·감사 대상)가 단독 전담한다. LLM 자가 갱신 사본(위키)이 같은 축을 무감사로 되돌려
        #   놓는 이중 소스 충돌 실측(유채원 "짧고 끊기는 존댓말" vs 카드 "해요체"). 카드 저장은 유지(작가 열람).
        def _no_secret(body: str) -> str:
            kept = "\n".join(ln for ln in (body or "").splitlines()
                             if not ln.strip().startswith(("비밀", "대사", "말투")))
            return _QUOTE_SPAN_RE.sub("", kept)
        return [RetrievedItem(source="wiki_page", ref=p.page_id, text=_no_secret(p.body),
                              trust_weight=(1.0 if p.trust_tier == "wiki_synthesized" else 0.3))
                for _s, p in scored[:k]]

    # ---- 영속화 ----
    def export_pages(self) -> list[WikiPage]:
        return list(self.pages.values())

    def import_pages(self, pages: list[WikiPage], log: list[str]) -> None:
        # XR-24/29(012 §6·015 §6): 새 구조를 전부 만든 뒤 교체. 단일 대입이 완전한 원자 상태 교체는 아니므로
        #   일관성은 소비자 계약으로 보증한다 — pages+log 를 함께 읽는 소비자(snapshot_into·rebuild 커밋)는
        #   전부 세션 락 안, 무락 복합 조회(스냅샷 API 의 목록+lint)는 참조를 '한 번' 캡처해 양쪽에 같은 세대를
        #   쓴다(lint 의 pages 인자 — 013 의 "재읽기 없음" 주장은 스냅샷 API 이중 읽기로 반증돼 이 계약으로 수리).
        new_pages = {p.page_id: p for p in pages}
        new_log = list(log)
        self.pages, self.log = new_pages, new_log
