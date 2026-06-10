# -*- coding: utf-8 -*-
"""RAG — 이전 회차 '서사 배경' 찾기. 멱등 색인 + as_of 시점 필터 + 하이브리드(vec+BM25 RRF).

provider 는 주입(DI). 검색 결과는 RetrievedItem(narrative 슬롯)으로만 — ground_truth 승격 불가.
영속화를 위해 청크(임베딩 포함)를 export/import 한다(재수화 시 재임베딩 회피).
"""
from __future__ import annotations
import re
import numpy as np

from ..domain.types import RetrievedItem
from ..domain.project import PersistedChunk
from ..llm.base import LLMProvider

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except Exception:
    _HAS_BM25 = False


def _tok(s: str):
    words = re.findall(r"[가-힣A-Za-z0-9]+", s)
    grams = [w[i:i + 2] for w in words if len(w) >= 2 for i in range(len(w) - 1)]
    return words + grams


class RAG:
    def __init__(self, provider: LLMProvider, hybrid: bool = True):
        self.provider = provider
        self.chunks: list[dict] = []          # {chapter, version, text, emb(np)}
        self.hybrid = hybrid and _HAS_BM25
        self._bm25 = None                     # 색인 캐시(검색마다 재구축 O(N) 제거 — 회차 선형 레이턴시 방지)
        self._mat = None                      # 정규화 임베딩 행렬 캐시(벡터화 dot)

    def _invalidate(self) -> None:
        self._bm25 = None
        self._mat = None

    def index_chapter(self, chapter: int, text: str, version: int = 1) -> int:
        """멱등: 같은 chapter 기존 청크 제거 후 재삽입."""
        self._invalidate()
        self.chunks = [c for c in self.chunks if c["chapter"] != chapter]
        paras = [p.strip() for p in re.split(r"\n+", text) if len(p.strip()) >= 15]
        if not paras:
            return 0
        for p, e in zip(paras, self.provider.embed(paras)):
            self.chunks.append({"chapter": chapter, "version": version, "text": p,
                                "emb": np.array(e, dtype=np.float32)})
        return len(paras)

    def _ensure_index(self) -> None:
        """전 청크 기준 색인 1회 구축(검색은 as_of 마스크만) — 색인은 finalize 시에만 무효화."""
        if self._mat is None and self.chunks:
            m = np.stack([c["emb"] for c in self.chunks])
            self._mat = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
        if self.hybrid and self._bm25 is None and self.chunks:
            self._bm25 = BM25Okapi([_tok(c["text"]) for c in self.chunks])

    def search(self, query: str, as_of_chapter: int, k: int = 4) -> list[RetrievedItem]:
        idxs = [i for i, c in enumerate(self.chunks) if c["chapter"] <= as_of_chapter]
        if not idxs:
            return []
        self._ensure_index()
        cand = [self.chunks[i] for i in idxs]
        q = np.array(self.provider.embed([query])[0], dtype=np.float32)
        qn = q / (np.linalg.norm(q) + 1e-9)
        sims = self._mat[idxs] @ qn                                  # 벡터화 dot(캐시 행렬)
        vec_rank = sorted(range(len(cand)), key=lambda i: float(sims[i]), reverse=True)
        if not self.hybrid:
            order = vec_rank
        else:
            lex_all = self._bm25.get_scores(_tok(query))             # 캐시 색인 재사용
            lex = [lex_all[i] for i in idxs]
            lex_rank = sorted(range(len(cand)), key=lambda i: lex[i], reverse=True)
            rrf: dict = {}
            for rl in (vec_rank, lex_rank):
                for r, idx in enumerate(rl):
                    rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (60 + r)
            order = sorted(rrf, key=lambda i: rrf[i], reverse=True)
        return [RetrievedItem(source="rag_chunk", ref=str(cand[i]["chapter"]), text=cand[i]["text"])
                for i in order[:k]]

    # ---- 영속화 ----
    def export_chunks(self) -> list[PersistedChunk]:
        return [PersistedChunk(chapter=c["chapter"], version=c["version"], text=c["text"],
                               emb=c["emb"].tolist()) for c in self.chunks]

    def import_chunks(self, chunks: list[PersistedChunk]) -> None:
        self._invalidate()
        self.chunks = [{"chapter": c.chapter, "version": c.version, "text": c.text,
                        "emb": np.array(c.emb, dtype=np.float32)} for c in chunks]
