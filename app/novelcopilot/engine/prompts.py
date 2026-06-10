# -*- coding: utf-8 -*-
"""프롬프트 빌더 — Builder 패턴. 슬롯 직렬화의 '단일 지점'(비대칭 주입 위치 고정).

문체(StyleSpec)는 데이터로 주입(WEBNOVEL_STYLE 하드코딩 제거).
서사 흐름 강화: 직전 회차 원문 + 회차 내 직전 장면을 함께 노출하고 '이어가기'를 명령.
"""
from __future__ import annotations
from ..domain.types import ContextBoard, SceneSpec
from ..domain.world import StyleSpec


def render_style(style: StyleSpec) -> str:
    rules = "\n".join(f"{i + 1}) {r}" for i, r in enumerate(style.rules))
    return f"[웹소설 문체 규칙 — 반드시 준수]\n{rules}"


class PromptAssembler:
    def __init__(self, style: StyleSpec, prev_chapter_chars: int = 4000):
        self.style = style
        self.prev_chapter_chars = prev_chapter_chars

    def assemble(self, board: ContextBoard, scene: SceneSpec, prev_scenes: str) -> str:
        gt = "\n".join(f"- {f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth) or "(없음)"
        auth = "\n".join(f"- {d.text}" for d in board.authority) or "(없음)"
        narr = "\n".join(f"- [{r.source}:{r.ref}] {r.text}" for r in board.narrative) or "(이전 맥락 없음)"
        prev_ch = board.prev_chapter[-self.prev_chapter_chars:] if board.prev_chapter else ""
        sofar_block = (f"[지금까지 줄거리(누적 요약 — 전체 흐름·미결 사건을 잊지 말 것)]\n{board.story_so_far}\n\n"
                       if board.story_so_far else "")
        flow_block = (f"[직전 회차 원문(이어쓰기 기준 — 어조·문체·상황을 매끄럽게 이어라)]\n{prev_ch}\n\n"
                      if prev_ch else "")
        within = (f"[이번 회차 직전 장면들(바로 이어서)]\n{prev_scenes[-2500:]}\n\n"
                  if prev_scenes else "")
        voice_block = (f"[인물 보이스 — 각 인물의 대사는 이 말투 시그니처를 일관되게(서로 구분되게)]\n"
                       f"{board.voice_cards}\n\n" if board.voice_cards else "")
        return (
            f"[확정 설정 — 절대 위반 금지(눈색·소속·생사·등급·관계·세계규칙)]\n{gt}\n\n"
            f"[작가 지시 — 우선]\n{auth}\n\n"
            f"{voice_block}"
            f"{sofar_block}"
            f"{flow_block}"
            f"[이번 장면 목표]\n{scene.goal}\n핵심사건: {', '.join(scene.key_events)}\n\n"
            f"{within}"
            f"[참조 맥락 — 서사 배경(낮은 신뢰, 설정은 위 '확정 설정'이 우선)]\n{narr}\n\n"
            "[연속성 지시] 새 회차를 처음부터 다시 소개하지 말 것. 직전 상황에서 자연스럽게 이어서 전개하라."
        )
