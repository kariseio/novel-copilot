# -*- coding: utf-8 -*-
"""계획 하네스 — 비트(설계 산출물)의 결정론 lint (LLM 0콜, 임베딩 1콜 선택).

본문 검증만 있고 계획은 무검증 통과하던 갭을 닫는다: 계획 결함(죽은 인물 배정·무효 id silent drop·
비트 재탕)이 본문으로 흘러내려와 산문 게이트가 뒷수습하던 구조 — 계획 단계에서 잡으면 draft 콜이 통째로 절약된다.
검증은 본문과 같은 비대칭: 캐논(온톨로지) 대조는 binding, 재미·전개 품질은 비구속(여기서 다루지 않음).
"""
from __future__ import annotations

from ..domain.types import Violation, SignalGrade
from .drift import _stem, _event_keywords   # 어간(조사≥2 가드)·내용어 추출 — T2/DP-6 커버리지와 단일 출처


def _stem_set(s: str) -> set[str]:
    """사건 태그 → 내용어 어간 집합. 조사 substring 결함 회피(_stem, 어간≥2 가드)로 조사 변이 흡수.
    한 글자 잔재는 배제(교착어 형태소 오매칭 방지 — cast dangling 가드와 동일 원칙)."""
    return {st for st in (_stem(k) for k in _event_keywords(s)) if len(st) >= 2}


def _containment(a: set[str], b: set[str]) -> float:
    """두 어간 집합의 '작은 쪽 대비 겹침'(대칭 — min 기준). 한쪽이 다른쪽 재진술이면(부분집합) 1.0.
    Jaccard 대신 containment 를 쓰는 이유: 정당하게 '전진'하는 사건은 앞 사건 재료 위에 *새 내용어*를
    얹어 큰 쪽이 커지므로 Jaccard 는 희석되지만, '같은 사건 재발주'는 작은 쪽이 큰 쪽에 통째로 담겨
    containment≈1 로 또렷이 분리된다(재탕 대 전진의 변별력). min 분모라 방향 대칭(양방향 검사)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# 임계(두 조건 AND — 실측 캘리브레이션, 양방향): 작은 쪽 내용어 어간의 ratio 만으로는 부족하다. 짧은 태그에서
#  ubiquitous 엔티티(주인공명·상시 장소)가 분모를 지배해 '한 이름만 공유'해도 ratio 가 임계를 넘기 때문(오탐).
#  그래서 (a) containment≥_DUP_THRESHOLD *그리고* (b) 공유 어간 수≥_MIN_SHARED 를 둘 다 요구한다.
#  실측 분리(공유 어간 수):
#   · DP-4b 5화 실측 재발주(①계산↔⑤정리) = 6개 공유(벌금·오십만·월세·이십팔만·구십삼만 …), ratio 0.46 → FLAG
#   · 조사 변이 동일 사건 = 5개 공유(벌금·월세·잔고·목표·자금), ratio 0.71 → FLAG
#   · 인물명만 공유('이하린'·'도현' 류 별개 비트) = 1개 공유, ratio 0.50 → 오탐, min_shared 로 차단
#   · 인물+장소 공유(보스방 처치 vs 함정 해제 = 별개 동작) = 2개 공유, ratio 0.50 → 오탐, min_shared 로 차단
#   · 정당 '전진' 연속 사건 = 1개 공유, ratio 0.11 → 안전
#  → 실측 재발주는 5~6개 공유, 모든 오탐은 ≤2개 공유. _MIN_SHARED=3 이 그 사이를 가른다(넓은 마진).
#    보수적 방향: 내용어 2개만 겹치는 짧은 진짜 재발주는 놓칠 수 있으나(recall 손해) 정당 사건 오차단(precision)을
#    우선한다(ticket: 정당 연속 사건 오차단 금지 · '판정 아닌 신호'). ratio 는 재탕/전진 변별(전진은 새 내용어로
#    큰 쪽이 커져 ratio 희석)에, min_shared 는 '한 엔티티만 우연 공유' 오탐 차단에 각각 기여한다.
_DUP_THRESHOLD = 0.4
_MIN_KEYWORDS = 2   # 내용어 어간 2개 미만인 짧은 태그('전개' 류)는 판정 근거 부족 → 검사 제외(오탐 소스)
_MIN_SHARED = 3     # 공유 어간 3개 미만(=엔티티 1~2개 우연 공유)은 재발주 신호로 보지 않는다(짧은 태그 오탐 차단)


def event_dup_pairs(key_events: list[str],
                    required: list[str] | None = None,   # 하위호환 시그니처(무시 — 아래 주석)
                    threshold: float = _DUP_THRESHOLD) -> list[tuple[str, str, float]]:
    """계획층 이벤트 중복 탐지(결정론·LLM 0콜) — DP-13.

    검사 축은 **key_events 상호 1축뿐**이다: 같은 회차 비트가 같은 사건을 두 슬롯에 이중 발주(DP-4b 5화
    ①계산↔⑤정리 = 회차내 반복 소스). 어간≥2·경계 가드로 조사 변이 흡수, 동의어는 못 잡음 → 판정 아닌 신호.

    **required/climax 축은 제거됨(설계계약 역행이라 폐기)**: 시스템은 비트가 required_events/climax 를 이 지면에서
    '실현하도록' 코드로 강제한다(arc_planner: required 를 맨 앞에 무조건 보존; finale 은 climax 를 반드시 터뜨림).
    그러므로 '비트 key_events 가 required/climax 를 재진술'하는 것은 결함이 아니라 **설계 목표**다. 이를 중복으로
    보면 정상 실현을 결함으로 오판해 재계획이 required 실현 자체를 떨어뜨린다(미실현 필수사건 사망 = ticket 금지).
    required 를 '자기 key_events 안에서' 두 번 나열하는 경우는 축(1)이 이미 잡으므로 별도 축이 불필요하다.
    `required` 인자는 하위호환 위해 시그니처에 남기되 **무시**한다(호출부도 더는 전달하지 않음).

    반환: (event_a, event_b, ratio) 리스트 — containment≥threshold **그리고** 공유 어간 수≥_MIN_SHARED 인 쌍만.
    각 대상은 어간 집합이 _MIN_KEYWORDS 미만이면 검사 제외(짧은 태그 오탐 차단).
    """
    kev = [e for e in (key_events or []) if (e or "").strip()]
    stems = [(_stem_set(e), e) for e in kev]
    stems = [(s, e) for s, e in stems if len(s) >= _MIN_KEYWORDS]   # 근거 있는 태그만
    out: list[tuple[str, str, float]] = []
    # key_events 상호(양방향 — containment 는 min 분모라 순서 무관, i<j 로 1회만)
    for i in range(len(stems)):
        for j in range(i + 1, len(stems)):
            shared = len(stems[i][0] & stems[j][0])
            if shared < _MIN_SHARED:
                continue   # 엔티티 1~2개만 우연 공유 → 재발주 아님(짧은 태그 오탐 차단)
            r = _containment(stems[i][0], stems[j][0])
            if r >= threshold:
                out.append((stems[i][1], stems[j][1], r))
    return out


def lint_events(key_events: list[str], required: list[str] | None = None,
                threshold: float = _DUP_THRESHOLD) -> list[Violation]:
    """event_dup_pairs 를 Violation(plan_event_dup)으로 승격 — 재계획 1회의 입력(lint_beat 위반과 동일 경로).
    DETERMINISTIC 등급(입력에 LLM 산출물 0 — 순수 문자열 어간 비교). `required` 인자는 하위호환용(무시)."""
    viols: list[Violation] = []
    for a, b, r in event_dup_pairs(key_events, required, threshold):
        viols.append(Violation(entity="beat", kind="plan_event_dup", grade=SignalGrade.DETERMINISTIC,
                               canon="계획 사건 유일성",
                               text=f"중복 사건(겹침 {r:.2f}): '{a[:40]}' ↔ '{b[:40]}'",
                               evidence="계획 lint: 이벤트 중복"))
    return viols


def lint_beat(beat: dict, ontology, ch_no: int) -> list[Violation]:
    """비트 1개의 결정론 lint — 캐논 정합(엔티티 명부·생애주기)만. 위반은 재계획 1회의 입력이 된다(조용한 드롭 금지).

    재미·페이싱(훅 유형·시간 경과·장소)은 여기서 다루지 않는다 — 그건 '안 어긋남'(정합)이 아니라 '작법'이라
    시스템이 교정 지시로 강제하지 않고, 측정값을 작가에게 가시화만 한다(작가가 빨간펜으로 조향). 비대칭 보존.
    """
    viols: list[Violation] = []
    valid = set(ontology.entities.keys())
    for eid in beat.get("entities") or []:
        if eid not in valid:
            # 기존엔 무효 id 를 조용히 필터(silent drop) → 비트 의도가 소리 없이 증발(콜드 드롭의 숨은 원인)
            viols.append(Violation(entity=str(eid), kind="plan_unknown_entity",
                                   grade=SignalGrade.DETERMINISTIC, canon="엔티티 명부",
                                   text=f"비트가 명부에 없는 id '{eid}' 배정",
                                   evidence="계획 lint: id 정합"))
        elif ontology._in_terminal_state(eid, ch_no):
            # 제거 상태 인물 배정 — 본문 생성 '후'에야 잡히던 위반을 설계 단계로 전진(draft 콜 절약)
            viols.append(Violation(entity=ontology.name(eid), kind="plan_dead_cast",
                                   grade=SignalGrade.DETERMINISTIC, canon=f"{ch_no}화 시점 제거 상태",
                                   text=f"제거 상태 인물 '{ontology.name(eid)}'을 비트에 배정",
                                   evidence="계획 lint: 생애주기"))
    return viols


def beat_repeat_score(provider, summary: str, prev_summaries: list[str]) -> float:
    """비트 요약의 직전 비트들 대비 의미 유사도 최대값 — 재탕('같은 절벽 3번')을 설계 단계에서 차단.
    hook_repeat_semantic 과 동일 메커니즘(임베딩 코사인), 대상만 계획."""
    from .quality_gates import hook_repeat_semantic
    return hook_repeat_semantic(provider, summary, prev_summaries)
