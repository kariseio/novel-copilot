# -*- coding: utf-8 -*-
"""스킬 적용 엔진 — Adapter/Strategy 패턴.

한 파이프라인 지점(worldgen/chapter/revise)에 *켜진 스킬들*을 그 지점 고유의 '효과'(SkillEffect)로 변환한다.
- 새 지점 = 새 SkillAdapter 를 _ADAPTERS 에 등록(호출부 불변).
- 새 효과(예: 모델 라우팅 외 추가) = SkillEffect 필드 추가(어댑터만 수정).
- 합성 상한·자기표절 방지 프레이밍은 여기 한 곳에서 보장(주입 정책 단일 출처).
호출부(copilot/harness)는 apply_skills(skills, point) 만 안다 — 어떤 스킬이 어떻게 적용되는지는 어댑터가 캡슐화.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..domain.skill import Skill

SKILL_COMPOSE_CAP = 3   # 한 지점에 동시 적용 상한(프롬프트 비대=풍선효과 방지)


@dataclass
class SkillEffect:
    """켜진 스킬들을 한 지점에 적용한 결과(지점 무관 공통 인터페이스)."""
    prompt_inject: str = ""                       # 프롬프트 말미 주입(지시 + 결·리듬 예시)
    model: str = ""                               # 모델 라우팅 오버라이드("provider:model", 빈=기본)
    applied: list[str] = field(default_factory=list)   # 적용된 스킬명(작가 가시화)

    def __bool__(self) -> bool:
        return bool(self.prompt_inject or self.model)


class SkillAdapter(ABC):
    point: str = ""
    example_label: str = "[아래는 결·리듬 참고 — 내용을 베끼지 말고 톤·호흡만]"

    def _inject(self, skills: list[Skill]) -> str:
        out = []
        for s in skills:
            b = f"\n\n[적용 스킬 — {s.name}]\n{(s.instructions or '').strip()}".rstrip()
            ex = [e for e in (s.examples or []) if e and e.strip()][:3]
            if ex:
                b += "\n" + self.example_label + "\n" + "\n— — —\n".join(ex)
            out.append(b)
        return "".join(out)

    def apply(self, skills: list[Skill]) -> SkillEffect:
        return SkillEffect(prompt_inject=self._inject(skills),
                           model=next((s.model for s in skills if (s.model or "").strip()), ""),
                           applied=[s.name for s in skills])


class ChapterSkillAdapter(SkillAdapter):
    point = "chapter"
    example_label = ("[아래는 이 스킬의 *결·리듬* 참고 — 내용·인물·문장을 절대 베끼지 말고 "
                     "톤·호흡·길이감만 가져와라(자기표절 금지)]")


class ReviseSkillAdapter(SkillAdapter):
    point = "revise"
    example_label = "[아래는 이 스킬의 결 참고 — 베끼지 말고 다듬는 방향·톤만]"


class WorldgenSkillAdapter(SkillAdapter):
    point = "worldgen"
    example_label = "[참고 — 결·접근만, 그대로 베끼지 마라]"


_ADAPTERS: dict[str, SkillAdapter] = {a.point: a for a in (
    ChapterSkillAdapter(), ReviseSkillAdapter(), WorldgenSkillAdapter())}


def register_adapter(adapter: SkillAdapter) -> None:
    """확장점 — 외부에서 새 지점 어댑터 등록(플러그인)."""
    _ADAPTERS[adapter.point] = adapter


def apply_skills(skills, point: str) -> SkillEffect:
    """해당 지점의 *켜진* 스킬들을 어댑터로 적용해 효과를 반환(켠 게 없으면 빈 효과)."""
    enabled = [s for s in (skills or []) if getattr(s, "enabled", False) and getattr(s, "point", "") == point]
    adapter = _ADAPTERS.get(point)
    if not adapter or not enabled:
        return SkillEffect()
    return adapter.apply(enabled[:SKILL_COMPOSE_CAP])
