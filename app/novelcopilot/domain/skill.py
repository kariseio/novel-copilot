# -*- coding: utf-8 -*-
"""스킬(Skill) — 파이프라인 지점(worldgen/chapter/revise)에 *꽂는* 토글 가능한 증강 묶음.

설계: 검증된 레버(예시>지시, 모델>프롬프트)를 '이름 붙고·켜고끄고·작가가 만들/가져올 수 있는' 단위로 패키징.
- 회차 스킬 = instructions(쓰는 법) + examples(결·리듬 few-shot) + (선택)model 라우팅.
- 항상-on 전역 프롬프트가 아니라 *opt-in 이름 묶음* → 과제약(풍선효과) 회피. 켠 것만 그 지점 프롬프트에 주입.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class Skill(BaseModel):
    id: str
    name: str
    point: Literal["worldgen", "chapter", "revise"] = "chapter"
    enabled: bool = False
    instructions: str = ""               # 쓰는 법(간결한 지시)
    examples: list[str] = Field(default_factory=list)   # few-shot — '결·리듬'만(내용 베끼기 금지 프레이밍은 주입부)
    model: str = ""                      # 선택 모델 라우팅("provider:model", 빈값=기본). 슬라이스 단계 미적용(다음 증분)
    builtin: bool = False                # 내장 프리셋(삭제 불가)
    description: str = ""                 # 작가용 한 줄 설명


class SkillLibrary(BaseModel):
    """앱-전역 스킬 카탈로그(작품 간 공유). 작품은 injected_skills(id 목록)로 참조 —
    라이브러리에서 한 번 고치면 그 스킬을 주입한 모든 작품의 *다음* 회차부터 반영(참조형 live SSOT).
    이미 생성된 회차는 ChapterRecord.gen_context['skills'] 에 적용 텍스트가 동결되어 과거는 안 바뀜."""
    skills: list[Skill] = Field(default_factory=list)


# 내장 프리셋 — 기본 OFF(작가가 켠다). 회차 "문체" 스킬: 가벼운 웹소설 결.
def default_skills() -> list[Skill]:
    return [
        Skill(
            id="builtin_light_prose", name="가벼운 문체", point="chapter", builtin=True, enabled=False,
            description="문예체 대신 웹소설 특유의 가볍고 빠른 결(짧은 호흡·1인칭 능청·대사 중심)",
            instructions=(
                "이 회차는 *가벼운 웹소설 결*로 써라 — 정갈한 문예체가 아니라 빠르고 가벼운 톤. "
                "짧은 호흡(문장을 길게 늘이지 말 것), 1인칭 내면의 능청·자조, 대사 중심의 티키타카, "
                "균형 잡힌 장문·과한 직유/은유는 자제. 감정은 길게 설명하지 말고 짧은 반응·행동·혼잣말로 보여라."),
            examples=[
                "‘아, 망했다.’\n그 생각부터 들었다. 도망칠 데도 없고, 변명도 다 떨어졌다. 그냥… 망한 거다.",
                "“그래서, 네가 책임질 거야?”\n“…아니요.”\n“그럼 닥쳐.”\n할 말이 없었다. 맞는 말이라 더 짜증났다.",
                "분명 어제까지는 평범한 회사원이었는데.\n눈을 떠 보니 칼 들고 몬스터랑 마주 보고 있었다. 이게 뭐야 진짜.",
            ]),
        Skill(
            id="builtin_cider", name="사이다 전개", point="chapter", builtin=True, enabled=False,
            description="고구마(답답함) 줄이고 통쾌한 해소를 회차 안에서 한 번은 터뜨리는 결",
            instructions=(
                "이 회차는 답답함을 길게 끌지 말고, 쌓인 긴장·무시·억울함을 *이 회차 안에서 한 번은* 통쾌하게 해소(사이다)하라. "
                "주인공이 당하기만 하고 끝나지 않게 — 작더라도 분명한 되갚음·반전·인정의 순간을 둬라(억지 회수는 금지)."),
            examples=[]),
        Skill(
            id="builtin_dialogue_punch", name="대사 강화(퇴고)", point="revise", builtin=True, enabled=False,
            description="설명조 지문을 줄이고 대사의 결·리듬·티키타카를 살리는 퇴고",
            instructions=(
                "이 퇴고는 *사실은 단 하나도 바꾸지 말고*, 대사 위주로 결을 살려라 — 늘어진 설명 지문을 줄이고, "
                "대사를 짧고 또렷한 티키타카로, 인물 말투를 분화해 생동감 있게. 새 사건·설정·수치는 절대 추가 금지."),
            examples=[]),
    ]
