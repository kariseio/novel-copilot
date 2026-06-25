# 프롬프트 편향 원리 — 딥리서치 (2026-06, 106 에이전트·출처 검증)

우리 안티패턴 진단이 **peer-reviewed 근거로 확증**됨. 핵심: 장르 미상이면 **맥락에서만 도출, 특정 트로프를 호명·예시·부정 모두 회피.** 3대 실패모드 모두 문헌화.

## 1. 부정지시 priming (풍선효과/pink-elephant) — confidence HIGH
- LLM은 부정(negation)을 잘 못 처리. "X 하지 마라"가 X를 호명하면 X가 활성화됨.
- **"Do not think about pink elephant!"** (arXiv 2404.15154): negation-억제 공격 **75.54% 성공** — "X 없이 그려라"가 여전히 X 생성. 모델이 "not X를 X처럼 처리", 어텐션은 "직접 빼기 불가".
- NegBench (CVPR 2025, arXiv 2501.09425): 긍정문·부정문 임베딩이 유사로 붕괴, 부정에서 "chance 수준".
- "이전 맥락에 있는 것을 반복해 쉽게 prime됨"(Kassner & Schütze ACL 2020).
- **프롬프트만으론 ~10–23pp 개선에 그치고 완전 해결 못 함.** 긍정 프레이밍·규모가 완화하나 제거 못 함.

## 2. few-shot/예시 앵커링 — confidence HIGH
- Zhao et al. "Calibrate Before Use" (ICML 2021, arXiv 2102.09690): 예시 선택·순서·형식이 정확도를 "거의 chance↔거의 SOTA"로 흔듦. majority-label·recency·common-token bias가 "정답성과 무관하게" 작동.
- 위치만으로도: 동일 예시 블록을 앞→뒤로 옮기면 정확도 ±20%, **예측 절반이 뒤집힘**(arXiv 2507.22887).
- 불균형 예시셋(파워판타지만)은 그쪽으로 편향. 불가피하면 장르 균형 + 앞배치.

## 3. 예시편향은 완화 가능·모델의존 — confidence MEDIUM
- Gupta et al. (Amazon, arXiv 2312.16549): "robustness boundary가 모델·과제마다 크게 다름", 일부 LLM은 majority-label에 ~90% robust.
- **큰 모델은 informative task 지시가 없으면 majority-label bias에 더 민감**(30B/40B −27.9% vs 7B −8.3%). → 정보성 작업지시가 편향을 크게 줄임.

## 4. mode collapse
- RLHF 정렬 모델은 다양성을 줄이고 전형(최빈)으로 회귀 → 지배 트로프 디폴트(전투·파워판타지). gpt-5.5의 배틀 디폴트가 이것.

## 종합 처방 (근거기반)
- 긍정·affirmative 프레이밍(부정지시 금지).
- 정보성 task-specific 지시(편향 크게 완화, 특히 큰 모델).
- 균형 또는 0 예시, 불가피하면 앞배치.
- **단 편향은 줄지 제거 안 됨 — 잔여 robustness는 모델의존.**

## 우리 적용 (삼각검증 일치)
- 프롬프트 중립화(audit 39건) = **필요**(priming 제거).
- 그러나 프롬프트만으론 부족(~10–23pp) — gpt-5.5는 중립 프롬프트로도 장르맹목 → **장르결정 역할(worldgen·설계)은 robust 모델 claude 필수**(model-dependent robustness).
- 즉 **프롬프트 중립화 + claude 라우팅 둘 다**가 정답. 한쪽만으론 안 됨.

**Caveat(정직):** 정량 근거 대부분이 텍스트분류 ICL → 개방형 창작 생성으로의 전이는 원리적 외삽(직접 측정 아님). [[prompt-antipattern-audit-2026-06]] [[model-routing-prose-lever-2026-06]]
