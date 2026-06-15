# -*- coding: utf-8 -*-
"""약속-지불 원장 (G1) — '재미의 회계 상태'를 1급 영속 데이터로.

정합성에 온톨로지(SSOT)가 있듯, 재미에는 이 원장이 있다. 작품이 독자에게 '연 약속'(복선·예고된 보상·
미해결 질문)과 그 '지불'(사이다·회수·공개)을 추적한다 — 마감 강제가 아니라 '잔고가 항상 보이게'가 목표.

비대칭 보존: 이 원장은 narrative(서사 회계)지 ground_truth(캐논)가 아니다 — 회차 확정/검증을 막지 않는다.
P1 범위: 데이터 모델 + 결정론 카운터. 설계 라벨(spine plants/payoffs)을 미러해 생애주기를 부여한다.
본문에서 '실제로 지불됐는가'의 추출 검출은 P2(ClaimExtractor 패턴)에서 배선한다.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Promise(BaseModel):
    """독자에게 연 약속 1건. plants(설계 라벨)에 id·개설/만기 회차·지불형태·상태를 부여한 1급 항목."""
    id: str
    text: str
    opened_chapter: int = 1                 # 약속이 처음 추적된 회차
    due_chapter: Optional[int] = None        # 만기(있으면) — 강제 아님, 가시화용
    kind: str = ""                           # 지불 형태(자유 라벨): power/status/relation/info/mystery …
    status: Literal["open", "paid"] = "open"
    paid_chapter: Optional[int] = None        # 지불된 회차(P1=설계 라벨 일치, P2=본문 추출)


class PromiseLedger(BaseModel):
    """작품의 재미 회계 장부. ProjectState 에 가산적으로 얹힌다(구 JSON 무변경 로드)."""
    promises: list[Promise] = Field(default_factory=list)
    last_payoff_chapter: int = 0             # 마지막 확정 지불 회차(결정론 카운터 재료)

    def open_promises(self) -> list[Promise]:
        return [p for p in self.promises if p.status == "open"]

    def by_id(self, pid: str) -> Optional[Promise]:
        return next((p for p in self.promises if p.id == pid), None)
