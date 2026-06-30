# -*- coding: utf-8 -*-
"""프롬프트 빌더 — Builder 패턴. 슬롯 직렬화의 '단일 지점'(비대칭 주입 위치 고정).

문체(StyleSpec)는 데이터로 주입(WEBNOVEL_STYLE 하드코딩 제거).
서사 흐름 강화: 직전 회차 원문 + 회차 내 직전 장면을 함께 노출하고 '이어가기'를 명령.
"""
from __future__ import annotations
from ..domain.types import ContextBoard, SceneSpec
from ..domain.world import StyleSpec
from .textfmt import collapse_dashes
from .fact_sheet import build_brief


# 바닥(floor) 제약 — 미학과 무관한 비협상 조판/시장 제약. 미학 오버레이가 못 덮고, 교정 패스도 이것만 유지(단일 출처).
FLOOR_CONSTRAINTS = "모바일 가독 줄바꿈·호칭 자연화·분량·시점/시제 일관"


def floor_only() -> str:
    """교정/재작성 패스 전용 floor-only 블록 — 미학(기본 규칙·작가 오버레이) 0, 바닥 제약만.
    B-10: 최소 교정(설정 위반 수정)이 미학 오버레이 주입으로 '재문체화'로 번지는 것을 차단."""
    return (f"[조판 바닥 제약만 유지 — 미학 변경 금지]\n{FLOOR_CONSTRAINTS} 만 지키고, "
            "문장 리듬·길이·비유 밀도·서술 거리·어휘 격 같은 미학은 원문 그대로 보존하라(재문체화·재서술 금지).")


def render_style(style: StyleSpec) -> str:
    rules = "\n".join(f"{i + 1}) {r}" for i, r in enumerate(style.rules))
    block = f"[웹소설 문체 규칙 — 반드시 준수]\n{rules}"
    # Layer 2 작가 문체 오버레이 — 설정 시 위 기본 규칙의 '미학 축'을 작가 지정으로 덮어쓴다(precedence 명시).
    # 빈 값이면 추가 0 = 기존 동작과 동일(무회귀). '문체는 작가마다 다르다' → 기본 규칙은 디폴트일 뿐, 작가가 갈아끼운다.
    overlay = (style.author_style or "").strip()
    if overlay:
        # 오버레이는 '미학 축'만 덮어쓴다. 바닥 제약은 재나열하지 않고 위 규칙을 참조만(중복 부정 명령 = 두더지잡기).
        # 분량·시점/시제는 프롬프트가 아니라 결정론 게이트(norm·_fix_tense·tense_leak_ratio)가 실제 방어선이다.
        block += (
            "\n\n[작가 지정 문체 — 위 기본 규칙의 미학 축보다 우선]\n"
            f"{overlay}\n"
            "※ 문장 리듬·길이 변주·감정 처리 방식·직유/비유 밀도·서술 거리·어휘 격 같은 미학 축이 "
            f"위 기본 규칙과 충돌하면 이 작가 문체를 따른다. 단 위 규칙의 바닥 제약({FLOOR_CONSTRAINTS})은 "
            "작가 문체와 무관하게 유지하라."
        )
    return block


class PromptAssembler:
    def __init__(self, style: StyleSpec, prev_chapter_chars: int = 4000):
        self.style = style
        self.prev_chapter_chars = prev_chapter_chars

    def assemble(self, board: ContextBoard, scene: SceneSpec, prev_scenes: str) -> str:
        gt = "\n".join(f"- {f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth) or "(없음)"
        # 세계 규칙은 '낮은 신뢰 참조 맥락'이 아니라 '확정 설정'과 동급 고신뢰 — 헤더가 약속한 위치(세계규칙)에 직렬화(M-1).
        wr = ("\n[세계 규칙 — 이 작품의 불변 규칙. 어기지 마라]\n"
              + "\n".join(f"- {r}" for r in board.world_rules)) if board.world_rules else ""
        # CN-3: 집필 직전 초점화 브리프(시점+이번 사건)를 최상단에 재표면화(key_events 는 원래 20k 덤프 뒤에야 등장 —
        #       lost-in-the-middle 대응). 캐논은 바로 아래 [확정 설정]에 이미 있어 복제 않고 가리킨다. story_time(CN-1)은 여기로 통합.
        brief = build_brief(board.story_time, scene.key_events)
        brief_block = (brief + "\n\n") if brief else ""
        auth = "\n".join(f"- {d.text}" for d in board.authority) or "(없음)"
        narr = "\n".join(f"- [{r.source}:{r.ref}] {r.text}" for r in board.narrative) or "(이전 맥락 없음)"
        prev_ch = collapse_dashes(board.prev_chapter[-self.prev_chapter_chars:]) if board.prev_chapter else ""   # 재주입 시 줄표 런 정규화 → 모델이 자기 틱을 교재로 안 봄(소스 루프 차단)
        sofar_block = (f"[지금까지 줄거리(누적 요약 — 전체 흐름·미결 사건을 잊지 말 것)]\n{board.story_so_far}\n\n"
                       if board.story_so_far else "")
        flow_block = (f"[직전 회차 원문(이어쓰기 기준 — 어조·문체·상황을 매끄럽게 이어라)]\n{prev_ch}\n\n"
                      if prev_ch else "")
        within = (f"[이번 회차 직전 장면들(바로 이어서)]\n{prev_scenes[-2500:]}\n\n"
                  if prev_scenes else "")
        voice_block = (f"[인물 보이스 — 서로 구분되는 말투. 단 시그니처(어미·감탄사)는 대사 서너 개 중 한 번 정도만, "
                       f"모든 대사에 도배 금지(자기 패러디화 방지)]\n{board.voice_cards}\n\n" if board.voice_cards else "")
        return (
            f"{brief_block}"
            f"[확정 설정 — 절대 위반 금지(눈색·소속·생사·등급·관계·세계규칙)]\n{gt}{wr}\n\n"
            f"[작가 지시 — 우선]\n{auth}\n\n"
            f"{voice_block}"
            f"{sofar_block}"
            f"{flow_block}"
            f"[이번 장면 목표]\n{scene.goal}\n핵심사건: {', '.join(scene.key_events)}\n"
            "(아래 참조 맥락의 세계 고유 설정 — 경제·무기·기술 — 을 사건의 구체 디테일(거래·전술·사물)로 최소 1회 구현하라. "
            "직전 내용과 같은 정보를 되묻는 대화 반복 금지.)\n\n"
            f"{within}"
            f"[참조 맥락 — 서사 배경(낮은 신뢰, 설정은 위 '확정 설정'이 우선)]\n{narr}\n\n"
            "[연속성 지시] 새 회차를 처음부터 다시 소개하지 말 것. 직전 상황에서 자연스럽게 이어서 전개하라. "
            "직전 내용을 재서술·되감기·재시작하지 마라 — 시간은 앞으로만 흐른다."
        )
