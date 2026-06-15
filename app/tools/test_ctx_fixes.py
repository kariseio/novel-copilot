# -*- coding: utf-8 -*-
"""컨텍스트 리뷰 개선 검증 — I-1 누적줄거리 경계기아 / I-4 cast attrs / M-2 장르계약 백필. LLM 0콜.
실행: PYTHONPATH=app python tools/test_ctx_fixes.py
"""
from __future__ import annotations
import sys

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec, AttributeSpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec, NarrativeProgress
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine.factory import build_engine
from novelcopilot.services.copilot import _build_story_so_far_hier, _cast_context
from novelcopilot.worldgen.genre_contract import infer_genre_contract
from novelcopilot.llm.base import LLMProvider


class Fake(LLMProvider):
    def __init__(self, responses=None):
        super().__init__(); self.responses = responses or []; self.calls = 0
    def chat(self, *a, **k): return ""
    def embed(self, texts): return [[0.0] * 4 for _ in texts]
    def chat_json(self, messages, **k):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)] if self.responses else {}


def test_story_so_far_boundary() -> bool:
    """I-1: 에피소드 경계 직후(현재 에피소드 회차 0)에도 직전 완료 에피소드 상세로 예산을 채운다(롤업 1줄 붕괴 방지)."""
    w = WorldConfig(title="t", genre="x", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(ending="E"), arcs=[
        Arc(arc_id="arc1", order=1, title="A1", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, title="E1", done=True, summary="EP1롤업요약"),
            Episode(episode_id="arc1_ep2", arc_id="arc1", order=2, title="E2")])])  # ep2=현재, 회차 0(경계)
    st = ProjectState(id="t", seed=ProjectSeed(), world=w, created_at="t")
    st.narrative_progress = NarrativeProgress(current_arc_id="arc1", current_episode_id="arc1_ep2")
    st.chapters = [
        ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, episode_id="arc1_ep1",
                      detail_synopsis="1화 상세: 진우가 회귀해 각성한다."),
        ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, episode_id="arc1_ep1",
                      detail_synopsis="2화 상세: 첫 던전에서 위기를 넘긴다.")]
    txt, dropped = _build_story_so_far_hier(st, 3, 12000)
    # 경계 직후라도 직전 완료 에피소드 회차 상세가 채워져야(예전엔 롤업 1줄만)
    ok = ("1화 상세" in txt and "2화 상세" in txt)
    # 큰 예산이면 회차가 상세로 들어가 롤업은 중복 생략(covered) → dropped 0
    ok &= (dropped == 0)
    # 작은 예산이면 먼 회차는 롤업으로 압축(여기선 ep1이 covered라 롤업 안 나오지만, 최소 최신 1화는 상세)
    txt2, _ = _build_story_so_far_hier(st, 3, 30)
    ok &= ("2화 상세" in txt2)
    print(f"[{'OK' if ok else 'FAIL'}] I-1 경계 기아: 경계 직후에도 직전 에피소드 상세로 예산 충전(dropped={dropped})")
    return ok


def test_cast_context_attrs() -> bool:
    """I-4: 캐논값 0(F급 rank=0)이 'if v:'로 묵음 탈락하지 않고, 핵심 추적축이 [:4]에 안 잘린다."""
    s = get_settings()
    w = WorldConfig(title="t", genre="x",
                    attributes=[AttributeSpec(key="rank", label="등급", kind="numeric", mutable=True),
                                AttributeSpec(key="a2", label="a2", kind="categorical", vocab=["x"]),
                                AttributeSpec(key="a3", label="a3", kind="categorical", vocab=["x"]),
                                AttributeSpec(key="a4", label="a4", kind="categorical", vocab=["x"]),
                                AttributeSpec(key="a5", label="a5", kind="categorical", vocab=["x"]),
                                AttributeSpec(key="secret", label="비밀", kind="state", states=["숨김", "발각"])],
                    entities=[EntitySpec(id="hero", name="진우",
                                         attrs={"rank": 0, "a2": "x", "a3": "x", "a4": "x", "a5": "x", "secret": "숨김"})])
    ont = build_engine(w, Fake(), s).ontology
    ctx = _cast_context(ont, w, ["hero"], 5)
    ok = ("rank=0" in ctx                # 0이 묵음 탈락 안 함(if v is not None)
          and "secret=숨김" in ctx)       # 6번째 attr도 [:8]로 포함(예전 [:4]면 누락)
    print(f"[{'OK' if ok else 'FAIL'}] I-4 cast attrs: rank=0 노출·6번째축 secret 포함 → {('rank=0' in ctx)}/{('secret=숨김' in ctx)}")
    return ok


def test_genre_contract_infer() -> bool:
    """M-2: G5 이전 작품의 장르 계약 추론(백필)."""
    fake = Fake([{"pleasure_engine": "회귀 정보우위 통쾌함",
                  "reader_expectations": ["사이다", "성장", ""],
                  "vocabulary_tone": "헌터물 어휘", "premise_asset": "회귀+미래지식 장기자산"}])
    w = WorldConfig(title="t", genre="헌터/회귀", premise="만년 F급이 회귀", synopsis="...")
    gc = infer_genre_contract(fake, w)
    ok = (gc is not None and gc.pleasure_engine == "회귀 정보우위 통쾌함"
          and gc.reader_expectations == ["사이다", "성장"]   # 빈 항목 제거
          and gc.premise_asset == "회귀+미래지식 장기자산")
    # 빈 응답 → None(비차단)
    ok &= (infer_genre_contract(Fake([{}]), w) is None)
    print(f"[{'OK' if ok else 'FAIL'}] M-2 장르계약 추론: 필드 파싱·빈항목 제거·빈응답 None")
    return ok


if __name__ == "__main__":
    results = [test_story_so_far_boundary(), test_cast_context_attrs(), test_genre_contract_infer()]
    print("\n컨텍스트 개선 검증:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
