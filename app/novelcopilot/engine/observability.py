# -*- coding: utf-8 -*-
"""관측 이벤트 버스 — Observer 패턴. '조용한 정지'를 구조적으로 불가능하게.

엔진은 emit 만 한다. 구독자(SSE 스트리머·로그 수집기)가 알아서 받는다.
버퍼에도 항상 남겨 회차 생성 후 타임라인을 응답에 실어 보낼 수 있다.
"""
from __future__ import annotations
from typing import Callable

FAILURE_MODES = {"parse_failure", "non_convergence", "escalation", "degraded_judge", "tool_error"}


class EventBus:
    def __init__(self) -> None:
        self.buffer: list[dict] = []
        self._subscribers: list[Callable[[dict], None]] = []

    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        self._subscribers.append(cb)
        return lambda: self._subscribers.remove(cb) if cb in self._subscribers else None

    def emit(self, node: str, event: str, **payload) -> None:
        evt = {"node": node, "event": event, **payload}
        self.buffer.append(evt)
        for cb in list(self._subscribers):
            try:
                cb(evt)
            except Exception:
                pass

    def reset(self) -> None:
        self.buffer.clear()

    def failures(self) -> list[dict]:
        return [e for e in self.buffer if e.get("event") in FAILURE_MODES]
