# -*- coding: utf-8 -*-
"""CB-1 명명 인물 상한 제거 검증 — worldgen 프롬프트 조립 스냅샷 가드(LLM 0콜).

실측 결함(2026-07-15): brief.characters 의 작가 명명 인물이 `[:5]` 로 조용히 잘려(경고·기록 0)
앙상블 작품(용병단·길드·팀물)의 6번째 이후 인물이 세계관에서 증발 + 시스템 프롬프트의
"핵심 2~3명만" 지시가 명명 캐스트보다 우선 해석될 여지 → 라이브 신작이 3인극 세계관으로 축소.

잠그는 계약:
 ① 명명 인물은 수 제한 없이 *전원* 프롬프트 캐스트 블록에 등재(silent drop 금지).
 ② 플레이스홀더 이름("주인공"·"미정" 등)은 종전대로 제외.
 ③ 시스템 프롬프트가 '명명 인물 전원 등재 > 발명 인물 2~3명 절제'의 우선순위를 명문화.
실행: PYTHONPATH=app py -3.12 -m pytest tools/test_cb1_named_cast_uncapped.py -q
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.draft import ConceptBrief, BriefCharacter
from novelcopilot.domain.project import ProjectSeed
from novelcopilot.worldgen.generator import WorldGenerator


_NAMES = ["박효빈", "카일", "레오하르트", "돌프", "미레유", "그림자", "한세아", "바스코", "일리나"]


def _brief(n_named: int) -> ConceptBrief:
    chars = [BriefCharacter(name=_NAMES[i], role=f"역할{i}", want=f"욕망{i}") for i in range(n_named)]
    chars.append(BriefCharacter(name="주인공", role="플레이스홀더", want=""))   # ② 제외 대상
    return ConceptBrief(title="t", characters=chars)


def test_named_cast_all_included_beyond_five():
    # ① 9명 전원 등재 — 종전 [:5] 캡이면 6번째(그림자)부터 증발했다
    gen = WorldGenerator.__new__(WorldGenerator)      # provider 불요(프롬프트 조립만)
    user = gen._user(ProjectSeed(premise="p"), brief=_brief(9))
    for name in _NAMES:
        assert f"- {name}" in user, f"명명 인물 누락(silent drop 재발): {name}"


def test_placeholder_names_still_excluded():
    gen = WorldGenerator.__new__(WorldGenerator)
    user = gen._user(ProjectSeed(premise="p"), brief=_brief(3))
    assert "- 주인공" not in user                      # ② 플레이스홀더는 캐스트 블록 제외 유지


def test_system_prompt_priority_named_over_invented():
    gen = WorldGenerator.__new__(WorldGenerator)
    sys_prompt = gen._system(24)
    # ③ 명명 인물 전원 등재가 먼저, 2~3명 절제는 '새로 발명'하는 인물에만
    assert "전원" in sys_prompt and "수 제한 없음" in sys_prompt
    assert "새로 발명" in sys_prompt and "2~3명" in sys_prompt
    assert "'주인공과 핵심 관계 인물 2~3명만'" not in sys_prompt   # 종전 전면 상한 문구 재유입 잠금


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"CB-1 검증: ALL GREEN ({len(fns)} tests)")
