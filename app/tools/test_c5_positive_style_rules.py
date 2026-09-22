# -*- coding: utf-8 -*-
"""C-5 DEFAULT_STYLE_RULES 부정명령 선별 긍정 전환 검증 — 기본 문체 규칙 스냅샷 가드(LLM 0콜). C-4(0cccfcc) 파생.

pink-elephant(B-23, C-3·C-4 계보): 부정명령("~하지 마라/금지")과 기피 대상 호명(감정 라벨·자각 요약·
막연어·헤지·봉합 예문 인용)이 그 토큰을 오히려 프라이밍 → 소프트 디폴트 성격(미학) 규칙만 긍정 전환하고
재유입을 여기서 잠근다(docs/style-layering.md Layer 1).

하드 바닥(안전 계약) 부정형은 '남아 있음'을 함께 잠근다(전환 대상 아님 — FLOOR_CONSTRAINTS·floor_only 관행):
  · 규칙1 조판 '벽' 삼지 마라(모바일 가독), 규칙7 호칭 라벨 노출하지 말고(호칭 자연화),
    규칙8 끊긴 문장 금지(분량/완결 — 결정론 게이트 norm 병행)
  · 규칙4 "번역투 대신"(E-02 번역투 소스차단 — 실측 효과 입증 안전 계약, 전환 금지)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.world import DEFAULT_STYLE_RULES, StyleSpec
from novelcopilot.engine.prompts import render_style

# ST-2: 표적 재작성(대사·행동 주도 + 모바일 문단 리듬 + 화자 표지 인칭분기). 항목 수·floor 위치·양성 원칙 불변.


# 전환으로 제거된 프라이밍 토큰(기피 예문 인용·기피 행위 호명) — 전 규칙 부재를 잠근다
_PRIMERS = (
    "분노했다", "두려웠다", "깨달았다", "직감했다", "이름표",     # 규칙2 감정 라벨·자각 요약 예문
    "지문 설명이 아니라", "'말했다' 태그", "정보 대사는 금지",     # 규칙3 기피 호명·부정명령
    "무언가", "뭔가", "모종의", "공백어",                          # 규칙4 막연어 예문(그 자체가 최빈 AI 틱)
    "듯했다", "처럼 보였다", "헤지 없이",                          # 규칙5 헤지 예문
    "끝났다'", "한 뼘 성장", "봉합", "교과서 정석",                # 규칙6 봉합 예문·기피 호명
    "낭독",                                                        # 규칙7 설정 낭독 오프닝 호명
    "아니라 B였다", "부정-대조", "삼단 병렬",                      # SR-1: 규칙5 잔존 금지 예문·범주 호명(실측 유발원) 재유입 잠금
)

# 부정명령 토큰 — 하드 바닥 규칙(허용 목록)에서만 등장해야 한다
_NEG_TOKENS = ("하지 마", "쓰지 마", "말 것", "말라", "지 말고", "삼지 마", "금지")
_FLOOR_ALLOW = {
    # VP-1(2026-08-13): 규칙1의 '벽 삼지 마라' 부정형은 2문장 문단 벽의 소스로 실측(작가 정독+패널)돼 제거 —
    #   규칙1은 이제 부정형 0(1~4문장 변주 긍정형만). floor 허용 목록에서 0 삭제.
    6: {"하지 마", "지 말고"},  # 규칙7 '노출하지 말고' — Layer 0 호칭 자연화(FLOOR_CONSTRAINTS)
    7: {"금지"},               # 규칙8 끊긴 문장 — Layer 0 분량/완결 계약(결정론 게이트 병행)
}


def test_rule_count_fixed():
    # 양성 원칙 헌법: 항목 수 고정(규칙 +1 두더지잡기 금지 설계)
    assert len(DEFAULT_STYLE_RULES) == 8


def test_no_primer_tokens_anywhere():
    for i, rule in enumerate(DEFAULT_STYLE_RULES):
        for tok in _PRIMERS:
            assert tok not in rule, f"프라이밍 토큰 재유입(규칙{i + 1}): {tok!r}"


def test_negative_imperatives_only_on_floor():
    for i, rule in enumerate(DEFAULT_STYLE_RULES):
        allowed = _FLOOR_ALLOW.get(i, set())
        for tok in _NEG_TOKENS:
            if tok in allowed:
                continue
            assert tok not in rule, f"소프트 디폴트에 부정명령 재유입(규칙{i + 1}): {tok!r}"


def test_floor_negatives_preserved():
    # 하드 바닥 부정형이 '전환되지 않고 남아 있음'을 잠근다(과잉 전환 방지 — 원칙 ②). floor 위치 3/6/7.
    # VP-1: 규칙1은 '벽 삼지 마라' 부정형 제거 + 1~4문장 변주로 개정(작가 정독 4연속 지적·패널 합치) — 긍정형만 잠근다.
    assert "1~4문장" in DEFAULT_STYLE_RULES[0] and "삼지 마" not in DEFAULT_STYLE_RULES[0]
    assert "노출하지 말고" in DEFAULT_STYLE_RULES[6]
    assert "끝내기 금지" in DEFAULT_STYLE_RULES[7] and "4,500~5,500" in DEFAULT_STYLE_RULES[7]  # LN-1: 상한 포함 긍정형(공백 포함 4,500~5,500자 목표)
    # 규칙4 번역투 소스차단(E-02) — 안전 계약 문구 유지
    assert "번역투 대신" in DEFAULT_STYLE_RULES[3]


def test_converted_rules_keep_intent():
    # 규칙1 조판: 모바일 문단 리듬(VP-1: 1~4문장 변주·대사 단독 문단)
    r1 = DEFAULT_STYLE_RULES[0]
    assert "1~4문장" in r1 and "단독 문단" in r1
    # 규칙2 보여주기(①): 정보·감정을 행동·대사·선택으로 + 구체 신체 목록 + 해석 위임
    r2 = DEFAULT_STYLE_RULES[1]
    assert "행동·대사·선택으로 드러내라" in r2 and "마른침" in r2 and "독자에게 맡겨라" in r2
    # 규칙3 대사주도(①)+화자 표지(③): 티키타카 + 행동 비트 + 인칭 분기(3인칭 비트 동반/1인칭 무태그 허용) + 대화 whitelist
    r3 = DEFAULT_STYLE_RULES[2]
    assert "티키타카" in r3 and "행동 비트" in r3
    assert "3인칭" in r3 and "1인칭" in r3     # 인칭 분기 화자 전략(중립 문안 — 인칭 필드 부재)
    assert "상대가 모르는 것과 지금 결정할 것만" in r3
    # 규칙4 어휘(④): 구체 명사·동사로 세우기 + 구체 감각 채널 못박기(막연어 차단의 긍정형)
    r4 = DEFAULT_STYLE_RULES[3]
    assert "구체적인 명사와 동사" in r4 and "소리·색·온도·무게" in r4
    # 규칙5(④): 확정 서술 긍정형 + 문장 변주 보존 + 직진 단언(SR-1: 종전 quota 형이 금지 예문을 문자 그대로
    #   호명("'A가 아니라 B였다'식") → pink-elephant 로 그 문형을 오히려 유발, 라이브 1화 실측 7건/5.2천자.
    #   예문·범주 호명 전부 삭제하고 긍정 지향으로 전환 — C-5 당시 '이미 quota 형이라 보존' 판단의 실측 번복)
    # VP-1: 규칙5는 '들쭉날쭉·타격감' 리듬 공학 문안 → 정보-완결문 원칙 + 종결 다양화로 개정(적대 리뷰 치명 1 —
    #   파편↔'-다' 밸브 실측의 대체 수단 동봉 조건). 확정 서술 앵커는 유지.
    r5 = DEFAULT_STYLE_RULES[4]
    assert "완결된 한 문장" in r5 and "세 문장 이어지기 전에" in r5 and "확정 서술" in r5
    assert "아니라" not in r5      # 금지 예문 재유입 잠금(pink-elephant 소스차단)
    # 규칙6 장면 개시(⑤)+절단: 사건 한복판 개시 + 예상 비틀기 + 미해결 절단(봉합 차단의 긍정형)
    r6 = DEFAULT_STYLE_RULES[5]
    assert "한복판에서 열고" in r6 and "예상을 비트는" in r6
    assert "미해결" in r6 and "장면 '안'의 사건·대사·이미지로만" in r6 and "궁금해지게 끊어라" in r6
    # 규칙7 호칭 자연화(floor) + 회차 내 수치/연속성 정합
    r7 = DEFAULT_STYLE_RULES[6]
    assert "자연스러운 작중 호칭" in r7 and "회차 안에서" in r7


def test_persona_positive_and_targeted():
    # ST-2: persona 도 같은 표적(대사·행동 주도·모바일 문단)으로, 긍정 전용(틱 이름 호명·부정-대조 제거)
    p = StyleSpec().system_persona
    assert "대사와 행동으로 장면을 끌고" in p and "말·몸짓·선택으로 드러낸다" in p
    assert "AI가 쓴 티" not in p          # pink-elephant 소스차단(틱 자체 호명 제거)
    for tok in _NEG_TOKENS:
        assert tok not in p, f"persona 부정명령: {tok!r}"


def test_render_style_serializes_all_rules():
    out = render_style(StyleSpec())
    assert "[웹소설 문체 규칙" in out
    for i, rule in enumerate(DEFAULT_STYLE_RULES):
        assert f"{i + 1}) {rule}" in out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"C-5 검증: ALL GREEN ({len(fns)} tests)")


def test_vl1_everyday_dialogue_clause():
    """VL-1(2026-08-15 사용자 정정): 규칙 3 증보 — 인물은 낯선 현상도 아는 쉬운 말로 말한다.
    규칙 신설이 아니라 증보(항목 수 8·인덱스 불변 — build_enforce rules[4] 안전)."""
    from novelcopilot.domain.world import DEFAULT_STYLE_RULES
    assert "쉬운 말로 옮겨 말한다" in DEFAULT_STYLE_RULES[2]
    assert len(DEFAULT_STYLE_RULES) == 8
