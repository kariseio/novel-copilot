# -*- coding: utf-8 -*-
"""통제 어휘 + 라벨 — WorldConfig.attributes 에서 파생(하드코딩 CATEGORICAL_VOCAB/ATTR_LABEL 제거).

추출기와 룰 엔진이 '단일 출처'로 공유. '기타' 탈출구는 엔진이 보장(통제어휘 밖 명시언급이
침묵 통과하지 않고 escalation 되도록).
"""
from __future__ import annotations
from ..domain.world import WorldConfig, AttributeSpec, DEFAULT_STATUS_ATTR

OTHER = "기타"


class Vocabulary:
    def __init__(self, attributes: list[AttributeSpec]):
        self._attrs = {a.key: a for a in attributes}

    @classmethod
    def from_world(cls, world: WorldConfig) -> "Vocabulary":
        return cls(world.attributes)

    def attr(self, key: str) -> AttributeSpec | None:
        return self._attrs.get(key)

    def label(self, key: str) -> str:
        a = self._attrs.get(key)
        return a.label if a else key

    def categorical(self, key: str) -> list[str]:
        a = self._attrs.get(key)
        if not a or a.kind != "categorical":
            return []
        return list(a.vocab) + ([OTHER] if OTHER not in a.vocab else [])

    @property
    def categorical_keys(self) -> list[str]:
        return [a.key for a in self._attrs.values() if a.kind == "categorical"]

    @property
    def numeric_keys(self) -> list[str]:
        return [a.key for a in self._attrs.values() if a.kind == "numeric"]

    # ---- 생애주기(state/status) — death 는 기본 status 의 한 인스턴스(하드코딩 제거) ----
    def state_specs(self) -> list[AttributeSpec]:
        """선언된 state/status 속성. 'status'(생사) 미선언 시 기본을 보장(하위호환)."""
        decl = [a for a in self._attrs.values() if a.kind in ("state", "status")]
        if not any(a.key == "status" for a in decl):
            decl = decl + [DEFAULT_STATUS_ATTR]
        return decl

    def terminal_states(self, key: str) -> set[str]:
        a = self._attrs.get(key)
        if a and a.terminal:
            return set(a.terminal)
        return {"dead"} if key == "status" else set()        # 미선언 status 의 기본 terminal

    def irreversible_states(self, key: str) -> set[str]:
        a = self._attrs.get(key)
        if a and a.irreversible:
            return set(a.irreversible)
        return {"dead"} if key == "status" else set()        # 미선언 status 의 기본 irreversible

    def values(self) -> list[AttributeSpec]:
        return list(self._attrs.values())
