# 웹소설 도입부(발단) 작법 — 딥리서치 (2026-06, 103 에이전트·출처검증)

문제: 엔진이 전제의 "이후 상태"(좀비세계·각성후)에서 시작, 발단(주인공 일상+전환 극화)을 생략. 사용자 "설명 부족·전개 빈약".

## 핵심 원칙 (cited)
1. **Save the Cat 구조(HIGH):** 첫 ~10%는 변신 전 status quo setup — Opening Image(0-1%)='before' 스냅샷, Setup(1-10%)=일상·결핍·목표·주저, **Catalyst(전환/inciting)=10%지점**. "ground 먼저, 전제는 그 다음." (Brody·Reedsy 동일 수치). 단 soft window(전체 setup 선행 강제는 기각 1-2).
2. **in medias res ≠ 설정 삭제(HIGH):** 캐릭터가 *구체적 장면 목표*를 좇는 중간에서 시작 → 독자가 who/what/why 묻게. 빠른 훅=구체 목표지 grounding 생략 아님. grounding 없으면 호기심→혼란(K.M. Weiland·The Novelry).
3. **"너무 깊게 시작"=실패모드(HIGH):** 앞에 붙인 무관 액션이 구조 진행 방해+캐릭터 투자 굶김. **반복 rewind/backfill 필요=늦게 시작한 신호** → 전환을 극화하라(이미 변신후서 시작+과거 설명 금지). (Weiland)
4. **성공작 실증:** **나혼렙**=성진우를 최약체 E급으로 grounding 후 *각성을 레이드 중 죽음 직전 장면으로 극화*. **전독시**=김독자를 평범한 직장인(웹소설 읽는)으로 grounding 후 *소설이 현실 되는 전환 극화*. 둘 다 전환을 on-page.
5. **exposition=drip-feed:** 행동·대사·선택으로(절대 'XXX년 제국력' 역사덤프 금지). 환상 전제는 일찍 surface, 고구마 짧게→사이다.

## 적용(구현·검증)
beat_for_episode·beat_for 에 `chapter==1` 발단 분기: ① 주인공·일상 짧은 grounding ② 전제의 전환을 장면 극화 ③ 세계규칙 drip-feed. 검증: 좀비 ch1 beat가 payoff→setup, '편의점 알바 일상→상태창 등장(전환)→가족 아파트'로 재설계(이전=이미 아파트 전투).

**Caveat:** soft window(느린 setup 강요 아님), 웹소설 빠른 시작과 균형. [[genre-fidelity-prompt-bias-2026-06]] [[narrative-density-t1t2t3-2026-06]]
