# -*- coding: utf-8 -*-
"""LLM Wiki 레이어 — 서사/의미 절반을 점진 유지(컴파운딩 위키).

- finalize 시 인물 페이지 '점진 갱신'(전체 재생성 금지). 멱등 ingest(log grep).
- lint(): orphan/dangling/stale 을 순수 결정론 그래프 순회로(LLM 0콜) = 등급1.
- retrieve(): narrative 슬롯으로만. 결정론 코어는 위키를 1바이트도 안 읽는다.
provider 주입. 영속화 export/import(인물 페이지·로그).
"""
from __future__ import annotations
import json
import numpy as np

from ..domain.types import WikiPage, WikiLifecycle, TypedEdge, Violation, SignalGrade, RetrievedItem
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

    def ingest_chapter(self, chapter: int, text: str, ontology, reviewed: bool = True) -> int:
        if self._already(chapter):
            return 0
        present = [ontology.entities[i] for i in ontology.scan_present_ids(text)]
        chars = [e for e in present if e.etype == "character"]
        if not chars:
            self.log.append(f"[{chapter}][-][noop]")
            return 0
        current = {e.id: (self.pages[e.id].body if e.id in self.pages else "") for e in chars}
        roster = [{"id": e.id, "name": e.name} for e in chars]
        msg = [
            {"role": "system", "content":
             "너는 작품 바이블 위키 관리자다. 각 인물의 기존 페이지에 '이번 회차에서 새로 드러난 서사 사실'만 "
             "1~3문장 누적 추가한다(전체 재작성 금지, 기존 내용 보존). 설정 수치(눈색·등급 등)는 적지 마라(온톨로지 소유). "
             "성격·동기·관계·서사 사건만. JSON: {\"pages\":[{\"id\":..,\"body\":\"갱신된 전체 본문\"}]}"},
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
            self.pages[pid] = page
            self.log.append(f"[{chapter}][{pid}][update]")
            n += 1
        return n

    def add_edge(self, page_id: str, edge: TypedEdge) -> None:
        if page_id in self.pages:
            self.pages[page_id].typed_edges.append(edge)
            self.log.append(f"[{edge.source_narrative_order}][{page_id}][edge:{edge.type}]")

    def lint(self, watermark: int) -> list[Violation]:
        """순수 결정론(등급1, LLM 0콜): orphan 복선 / dangling 엣지 / stale."""
        viols: list[Violation] = []
        for p in self.pages.values():
            for e in p.typed_edges:
                if e.target_page_id not in self.pages:
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
        return [RetrievedItem(source="wiki_page", ref=p.page_id, text=p.body,
                              trust_weight=(1.0 if p.trust_tier == "wiki_synthesized" else 0.3))
                for _s, p in scored[:k]]

    # ---- 영속화 ----
    def export_pages(self) -> list[WikiPage]:
        return list(self.pages.values())

    def import_pages(self, pages: list[WikiPage], log: list[str]) -> None:
        self.pages = {p.page_id: p for p in pages}
        self.log = list(log)
