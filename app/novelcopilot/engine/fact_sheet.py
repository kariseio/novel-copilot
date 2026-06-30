# -*- coding: utf-8 -*-
"""회차 집필 브리프(CN-3) — 집필 직전 *초점화된* 사실시트 조립. LLM 0콜.

'정보 덤프 → 초점화된 브리프'(생성아키텍처 분석 결론). 핵심 가치 = 이 회차의 '미션'(언제·무엇)을
프롬프트 최상단(고살리언스)에 재표면화 — 특히 key_events 는 원래 ~20k 덤프(prev/요약) *뒤*에야
처음 등장(lost-in-the-middle, 2307.03172)하므로 최상단 재배치가 실효 있다.

설계(적대검증 반영): 캐논(등장 고정값)은 *바로 아래* 인접한 [확정 설정] 블록에 이미 최상단 배치돼 있어
브리프에 또 넣으면 중복(>엔티티 다수 시 lossy 절단까지) — 그래서 브리프는 캐논을 복제하지 않고 *가리킨다*.
브리프 = 시점(CN-1, 여기로 통합) + 이번 회차 핵심 사건 + '아래 확정 설정·세계 규칙을 어기지 마라' 바인딩.

원칙: (1) 프로즈 덤프 금지 — 짧게(cap). (2) 새 사실 발명 0 — 결정론 조립값(story_clock·beat)만 초점화.
(3) I-1 예산경합 주의 — 길이 상한으로 본문 예산 잠식 차단.
"""
from __future__ import annotations


def build_brief(story_time: str, key_events, *, cap: int = 600) -> str:
    """집필 직전 '이 회차 집필 기준' 브리프(시점 + 이번 사건). 비면 ''(주입 생략). cap=본문 길이 상한."""
    lines: list[str] = []
    if story_time:
        lines.append(f"· 시점: {story_time}(시간 역행·시점 모순 금지)")
    evs = [e.strip() for e in (key_events or []) if (e or "").strip()]
    if evs:
        lines.append("· 이번 회차 핵심 사건: " + " → ".join(evs))
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > cap:
        body = body[:cap].rstrip() + " …"
    return "[이 회차 집필 기준 — 이 사건들을 아래 확정 설정·세계 규칙을 하나도 어기지 않고 써라]\n" + body
