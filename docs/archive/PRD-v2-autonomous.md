# PRD v3 (final) — 에이전틱 웹소설 코파일럿
## "기계가 회차를 써내려가고, 작가가 옆에서 조향·수정한다" — 회차단위 RAG 생성 시스템

> 문서 상태: **개발 착수 조건부 가능** — §16의 M1 선결 스파이크를 MVP GO 게이트로 둠. 단 완결성 점검이 지적한 **4개 Blocker/High(① 디폴트 모드 liveness 계약 §4.4, ② 무응답 단일 종료조건 §10.8, ③ 미검토 회차 RAG 오염 격리 §8.5/§8.6, ④ 세 최소선 비양립 시 분기 의사결정 §16.0)** 을 **M1 GO 게이트 산출물로 명문화하기 전에는 코딩 착수를 보류**한다.
> 작성: 리드 PM 겸 에이전트 시스템 테크리드
> 전제: 수익성·단위경제·WTP·구독가 비고려(§18 비목표). 단 **회차당 LLM 호출 수·토큰 비용은 "수익성"이 아니라 "기술 설계 제약(지연·레이트리밋·장애전파)"으로 1급 취급**.
> 핵심 정체성: 이 제품은 **"무인 완주 보장 머신"이 아니라 "회차 루프 안에서 기계가 집필하고 작가가 능동 조력하는 비대칭 코파일럿"** 이다.

> **이 v3 final 문서의 성격**: 본 PRD는 4라운드 비평(기획·아키텍처·협업/에이전트루프 실패추적 + 완결성 점검)을 본문 곳곳에 인용·반영해, "좋은 약속"이 아니라 "깨지는 지점과 그 방어"를 명시한다. 비평이 지적한 구조적 모순(효율 vs 일관성, 자동진행 vs 정체성, 결정론 오염, seam 책임 공백, **디폴트 모드 liveness 공백·무응답 종료조건 모순·미검토 RAG 오염·세 최소선 비양립**)은 회피하지 않고 §3·§4.4·§8·§10.8·§13·§14·§16·§18에 노출한다.

---

## 0. v2→v3 전환 선언 — 무엇을 우경량화했고 무엇을 새로 도입했나 (TL;DR)

v2는 **완전 무인 자동(autonomous-by-default)** 이었고, "인간 안전망 제거"를 전제로 무인 장기실행용 중장비(§0 사전등록 4종, 72h liveness 불변식, escalation 전역 예산 KILL, 인간 닻 KILL 게이트)를 깔았다. v3는 **인간이 능동 조력자(안전망)로 회차 루프 내부에 복귀**하므로, 이 무인 전용 중장비를 폐기하지 않고 **"선택적 고자율 모드(opt-in)"로 우경량화(right-size)** 한다.

> ⚠️ **완결성 점검의 핵심 경고(verdict 수용)**: "인간이 안전망으로 복귀했으니 무인용 안전장치는 불필요"라는 가정에는 빈틈이 있다. **정작 인간이 부재할 때(auto_advance의 본질) 안전망이 사라진다.** v2가 무인 모드를 위해 깔았던 **work-레벨 liveness 불변식(72h hard·progress-monotone·deadlock-free)을 통째로 '무인 모드 전용'으로 강등**하면서 디폴트(유인) 모드의 종료·비교착 보장을 신설하지 않은 것은 Blocker다. v3 final은 이를 **§4.4 디폴트 모드 liveness 계약**으로 신설한다 — KILL이 아니라 **강제 작가 surface로 재배선**한다.

| 축 | v1 (human-in-loop) | v2 (autonomous) | **v3 (asymmetric copilot)** |
|---|---|---|---|
| 누가 쓰나 | 작가가 씀, 기계가 교정 | 기계가 씀, 기계가 자기 교정 | **기계가 씀, 인간이 능동 조향·수정** |
| 인간 위치 | 최종 안전망(펜) | 시스템 외부 메타 튜너(추방) | **회차 루프 내부 in-the-loop 협력 노드** |
| 생성 단위 | 작가 페이스 | 시드→완결 일괄 자율 | **회차(에피소드) 단위 점진 누적** |
| 최종 게이트 | 작가 | KILL 사전등록 + 익명 패널 α≥0.7 | **작가 본인 승인 (+ 외부 타당성 1회 객관 닻)** |
| liveness(종료 보장) | 작가가 멈추면 멈춤 | 72h hard·progress·deadlock-free **(KILL 트리거)** | **§4.4 디폴트 liveness 계약 (강제 작가 surface 트리거)** |
| 핵심 명제 | — | 무인 L0 완주 보장 | **회차 RAG 연속성 + 저마찰 인간 개입 + 협업 효율** |

**우경량화(relaxed) — 무인 전용 중장비를 선택적 고자율 모드로 강등**:
- §0 사전등록 4종 세트 → opt-in 고자율 모드에서만 적용하는 옵션 프로토콜
- KILL 임계 + gray zone 강제 KILL 디폴트 → **gray zone = 인간 결정 라우팅** (request_human_input)
- 72h liveness wall-clock hard **KILL** 트리거 → 무인 배치 모드에서만. **단 progress-monotone·deadlock-free는 폐기가 아니라 §4.4에서 '강제 작가 surface' 트리거로 재배선해 디폴트 모드에 존속**(완결성 Blocker 수용)
- 엔딩 수렴 progress 강제전이 → **정체 신호를 작가에게 강제로 알리고(auto_advance 강제 해제) 방향전환 제안**
- escalation 전역 예산 KILL(작품당 ≥10회=무산) → **UX 마찰 모니터링 지표**
- 인간 닻 KILL 게이트(익명 3명 α≥0.7 + 완독률<60% KILL) → 작가 본인 승인. **단 익명 1회 객관 닻은 judge recall·작가 편향 보정용으로 잔존**(아키텍처 비평 Blocker 수용, §13.4)

**신규 도입(added)**:
1. **에이전틱 하네스** — 회차 생성을 Plan→Act(tool)→Observe→Reflect 루프로 (§6)
2. **회차 단위 RAG 루프를 1급 백본으로** — 회차 N = f(RAG(1..N-1), SSOT, 골격, 작가지시) (§7)
3. **장면 경계 인간 개입 인터럽트** — (토큰 단위 폐기, 정직한 우경량화, §10). 장면 경계의 결정론 정의 §6.7 신설.
4. **협업형 worldgen** — AI 제안 → 작가 조정·승인·재지시 (§9.4)
5. **AuthorDirective 레저 + 이후 회차 전파 엔진 + 작가 자기모순 회수 UX** (§4·§11)
6. **디폴트(유인) 모드 liveness 계약** — progress-monotone → 강제 작가 surface, defect_debt 절대 상한, '작가 부재 + 정체' 데드락 상태 모델·종료조건 (§4.4, 완결성 Blocker 신설)
7. **무응답 단일 종료조건 직교 매트릭스** — request_human_input·interrupt·골격락 3경로 통합, severity별 (타임아웃, 타임아웃 후 행동) (§10.8, 완결성 Blocker 신설)
8. **미검토 회차 RAG 오염 격리** — `unreviewed_machine` trust 등급 + provisional 색인 플래그 + 감사 실패 시 quarantine 강등 (§8.5/§8.6, 완결성 High 신설)
9. **콘텐츠 안전 게이트 + auto_advance 책임 분리** (§9.5, 완결성 Med 수용)

**계승(kept)** — v2의 견고한 기술 토대 전부: 결정론 일관성 코어, 온톨로지(결정론 lookup)↔RAG(서사 검색) 신뢰등급 분리, narrative_order 단일 정렬키, Chapter 단일 writer, 확정 워터마크 3종, 확정→가역 재색인 단방향 루프, Temporal durable workflow, 구조화 출력, 하이브리드 검색, 자기섭취 차단, 비대칭 자율 등급(signal_grade), 이종 judge 앙상블, 복선 터치체인. **추가 계승: progress-monotone·deadlock-free liveness 개념(§4.4에서 트리거만 KILL→surface로 재배선) + 에이전트 루프 재현성 분산 측정(§16, blindSpot 수용).**

---

## 1. 개요 & 비전 (에이전틱 코파일럿)

### 1.1 한 문장 정의
**시드(장르·로그라인·톤)로부터 기계가 세계관과 회차를 1차 집필하고, 작가가 회차 루프 내부에서 장르·세계관·방향·승인을 능동적으로 조향·수정하며, 회차 N이 이전 회차들의 RAG 기억 + 세계관 SSOT + 골격 + 누적 작가지시로 점진 생성되는, 100~200화 완결형 웹소설 협업 생성 시스템.**

### 1.2 비전과 가치의 삼위일체
v3의 핵심 가치는 더 이상 '무인 완주 신뢰성'이 아니라 다음 셋의 결합이다:

```
       회차 단위 점진 생성
       (기계가 백본을 쥠)
              ▲
             ╱ ╲
            ╱   ╲
   RAG 연속성 ─── 저마찰 인간 개입
 (회차 간 기억)   (자연스러운 조향)
```

- **회차 단위 점진 생성**: 100~200화를 한 번에 뽑지 않고 회차 단위로 굴린다.
- **RAG 연속성**: 회차가 늘어도 이전 회차 기억을 정확히 회수해 모순이 발산하지 않게(보장이 아니라 **비발산 + 인간 조기교정**).
- **저마찰 인간 개입**: 작가가 적은 노력으로 자연스럽게 조향·수정·재지시.

> ⚠️ **이 가치 삼각형의 전제(완결성 High 수용)**: 본 삼각형은 **세 최소선 — 정체성 최소선(§3.5)·저작물성 최소선(§3.6)·인지 부하 하드캡(§4.2) — 이 우연히 양립 가능**하다는 미검증 가설 위에 서 있다. 세 값이 M1에서 비양립으로 나오면 이 삼각형이 무너진다. 그 경우의 fallback 제품 형태를 §16.0 분기 의사결정 트리에 명문화한다(코딩 착수 전 결론 의무).

### 1.3 무엇이 아닌가 (정체성 경계)
- ❌ 무인 완주 보장 머신 (v2 정체성, 폐기 — 선택적 고자율 모드의 부차 실험으로만)
- ❌ 작가가 백지에서 타이핑하는 집필 에디터 (v1 정체성, 폐기)
- ⚠️ **"효율 좋은 대필 서비스"가 되어선 안 됨** — 기획 비평이 지적한 치명적 함정. 작가가 기계 초안을 "내 작품"으로 못 느끼면 v3는 자기 정체성을 배반한다. 이 긴장은 §3.4·§16.0·§17·§18에서 정면으로 다룬다.

---

## 2. v1→v2→v3 포지셔닝 (기계집필·작가보조의 정확한 의미)

### 2.1 역할 반전의 정확한 정의
| 차원 | v1 | v2 | **v3** |
|---|---|---|---|
| 산문 첫 글자~마지막 글자 | 인간 타이핑 | 기계 | **기계 (인간은 백지 타이핑 안 함)** |
| 인간의 노동 형태 | authoring(쓰기) | 메타 튜닝(격리) | **steering/curating/redirecting(조향·큐레이션·재지시)** |
| 인간 개입 시점 | 상시(쓰는 동안) | 시드 1회 후 이탈 | **회차마다 밀착(루프 내부)** |
| 인간 결합 방식 | 펜 그 자체 | 시스템 외부 운영자 | **루프 내부 in-the-loop 노드 + 외부 도구(request_human_input) 양쪽** |

### 2.2 주도-결정-보조 3분 모델
| 영역 | 주체 | 내용 |
|---|---|---|
| **주도(drive)** | 기계 | 회차별 산문 생성, RAG 조립, 온톨로지 lookup, 자가 비평·일관성 점검, 비트→장면 분해, 복선 추적, 드리프트 측정. **디폴트 = 끝까지 써내려간다 (단 §4.4 liveness 계약의 상한 안에서)** |
| **결정(decide)** | 인간 | 장르·세계관 정체성 승인/거부, 엔딩 HARD invariant, 회차 방향 전환 채택, escalation 분기점, **"여기서 멈추고 다르게" 거부권**, **정체/데드락 시 강제 호출되는 진행/중단 결정(§4.4)**. 의미적 모순·테마 표류의 최종 판단 |
| **보조(assist)** | 인간 | 초안 미세 수정, 캐릭터 보이스 힌트, 선호 신호, worldgen 항목 조정 |

핵심 비대칭: **"기계가 빈 캔버스를 채우고, 인간이 그 캔버스 위에서 빨간 펜을 든다."** 기본 흐름은 자동 진행이되, 인간 개입이 들어오면 그 개입이 우선권을 갖고 이후 회차 생성 파라미터·RAG 가중·지시 컨텍스트에 전파된다. **단 인간이 부재해도 시스템이 영원히 회차를 찍어내지 않도록 §4.4가 종료/비교착을 결정론으로 보장한다.**

### 2.3 v2의 "sub-linear 강박" 폐기
v2는 "인간 부하가 N편에 선형이면 병목"이라 N편 병렬을 P0로 봤다. v3는 인간이 능동 노드로 복귀하므로 이 무인 배치 가정이 무효. **N편 병렬은 부차 목표로 강등**되고, **"개입이 얼마나 저마찰인가(interaction friction)"가 새 1급 협업 품질 지표**가 된다.

---

## 3. v2 대비 변경 (kept / relaxed / added)

### 3.1 계승 (Kept) — v2 기술 토대 전부 유지
| 자산 | v3에서의 역할 |
|---|---|
| 결정론 일관성 코어(충돌 4종 + EntityStateTimeline 시점 + evidence) | 에이전트 `consistency_check`, **유일 신뢰 ground truth** |
| 온톨로지↔RAG 분리 (박기 vs 찾기) | `ontology_lookup`(상단 고정·누락0) vs `rag_search`(cap·trust가중) |
| narrative_order 단일 정렬키 | 회차 점진 생성의 시점 좌표계 |
| Chapter 단일 writer 계약 | writer=오케스트레이터, 인간 수정분도 단일 writer 경유 |
| 확정 워터마크 3종 | finalize→전진→메모리 반영 핸드오프 |
| 확정→가역 재색인 단방향 루프(versionId, is_active 단일) | 작가 수정분 RAG 반영, stale=0 |
| Temporal durable workflow | 에이전트 루프 기질 + HITL interrupt 토대 |
| 구조화 출력 (Pydantic v2) | 모든 도구 I/O·캐논 제안 |
| 하이브리드 검색 (FTS Kiwi + 벡터 RRF) | `rag_search` 본체 |
| 자기섭취 차단 (quarantine trust 하향, style_anchor 동결 시드) | RAG 연속성 핵심 방어 |
| 비대칭 자율 등급 (signal_grade) | 자동확정 범위 규칙 |
| 이종 judge 앙상블 (generator≠judge, N≥3) | `critique` (단독 KILL 권위는 우경량화) |
| 복선 터치체인 (PlotThread plant→touch→payoff) | `payoff_check` |
| **progress-monotone·deadlock-free liveness 개념(v2 §0.4)** | **§4.4에서 KILL→강제 작가 surface로 트리거만 재배선해 디폴트 모드 존속** |
| **에이전트 루프 재현성 분산 측정(v2 §15.1, n≥10)** | **§16 M1 측정 항목으로 복원(partial_rewrite monotone 판정 안정성 검증)** |

### 3.2 우경량화 (Relaxed) — §0 표 참조 (인간 안전망 복귀로 right-size)

### 3.3 신규 도입 (Added) — §0 표 참조

### 3.4 ⚠️ 전환이 만든 구조적 모순 (비평이 짚은 핵심 — 회피하지 않음)
v3가 인간 복귀로 자동 해결되리라 가정했으나 **풀리지 않은** 모순 4종.

1. **효율 vs 일관성 (동일 변수 역방향 요구)** — auto_advance 디폴트(효율)면 작가가 회차를 안 보고, 안 보면 "인간 조기교정"(일관성)이 안 일어나 p(1-r) 모순이 v2처럼 누적. **같은 변수(작가 검토 여부)를 효율은 "안 보게", 일관성은 "보게" 요구.** → §13.2·§17 명제 범위 축소. **단 이 완화책(명제 범위 축소·정체성 최소선) 자체가 미검증 가설**이므로 §16.0에서 비양립 시 fallback을 명문화.
2. **자동진행 vs 정체성** — 시스템이 가장 잘 작동할 때(작가 거의 개입 안 함) 결과물이 순수 자동생성 품질로 수렴 → 작가 정체성 기여도 0 수렴 → "내 작품 느낌" 파괴. → §3.5.
3. **무결성 vs 효율** — 캐논 역추출 recall<1을 막으려 재확인 강제하면 마찰↑로 효율 붕괴. → §8.5·§13.5.
4. **자동진행 vs 종료(liveness)** — auto_advance 디폴트에서 '작가 부재 + 엔딩 비수렴 + defect_debt 무한 적재'가 동시 성립하면 시스템이 영원히 회차를 찍어내며 엔딩에 영영 도달하지 않을 수 있다. convergence_probe는 D14로 강제전이 금지라 이 경로를 못 끊는다. → **§4.4 디폴트 liveness 계약으로 결정론 종료조건 신설**(완결성 Blocker).

### 3.5 정체성 최소선 가설 (기획 비평 치명적 지적 수용)
> 비평: "의뢰인 핵심 질문('작가가 이걸 내 작품으로 느끼는가')에 답이 없다."

**가설(M1 검증)**: *작가가 회차당 최소 1개 능동 결정(비트/캐릭터/방향 지시 중 하나)을 내려야 소유감이 유지된다. 미만이면 "대필 인식" 전락.*
- auto_advance 디폴트와 충돌 → **디폴트를 "효율 우선"이 아니라 "소유감 유지 최소선 위에서 효율"로 재정의.**
- 이 최소선이 §3.6 법적 저작물성 최소선과 같은 숫자로 수렴하는지 M1 확인 → 수렴하면 그것이 진짜 디폴트 개입 수준. **비수렴(세 최소선 비양립) 시 §16.0 분기 트리로.**
- **⚠️ '능동 결정' 진정성 게이밍 경로(완결성 blindSpot 수용)**: 작가가 소유감을 유지하려고 회차당 1개 무의미한 토글만 누르는 것(§17.4 Goodhart의 정체성 버전)을 방어. '능동 결정' 카운트를 §4.2 형식 통과율과 교차 검증 — **형식 통과율이 높은데 결정 카운트만 채워지면 그 결정은 무의미 토글로 간주**해 소유감 지표에서 제외(measure를 게임하면 지표가 그 게이밍을 흡수하지 못하게 함). 이 교차는 게이트가 아니라 모니터(§17.4).

### 3.6 저작권 P0 게이트 (기획 비평 치명적 지적 수용, MVP 선결)
> 비평: "기계가 1차 집필 주체인데 저작물성을 nonGoals로 추방. 한국 저작권법상 인간 창작 기여 없는 AI 생성물은 보호 불가 → 등록·플랫폼 게재 막힘."

- **'법무 협의 범위 밖'이 아니라 제품 형태 결정 선행 질문.** "기계 1차 집필 + 작가 승인"이 작가 저작물로 인정 안 되면 작가는 자기 이름으로 연재·출간·2차판권 불가.
- **MVP 선결 변호사 자문 범위**:
  1. 작가가 저작자로 인정받는 **최소 기여 형태** (그 법적 최소선이 §3.5 정체성 최소선과 일치하는지 교차 확인).
  2. **⚠️ auto_advance 미검토 회차의 콘텐츠 책임 귀속(완결성 Med 수용)**: §9.5가 '작가 승인 = 안전 책임'을 명문화하나, auto_advance에서 작가가 안 본 회차도 finalize·게재 후보가 된다. "작가가 통제하지 못한 자동 생성물"의 책임 귀속이 모순(통제 안 함=저작 기여 약함=저작자성 약함인데, 책임은 짐). 변호사 자문에 **이 모순(통제·기여·책임의 동시 귀속)을 명시 포함**해, '작가 미검토 회차'의 안전·저작권 책임 경계를 확정.
- 이 게이트 NO면 제품 형태(개입 디폴트)를 §16.0 분기 트리로 재설계.

---

## 4. 협업 모델 & 인간 개입 지점

### 4.1 7대 인간 개입 지점
| # | 지점 | 무엇을 | 어떻게 |
|---|---|---|---|
| 1 | 장르·서브장르·톤 시드 | 1차 장르·톤·로그라인 결정 또는 AI 후보 선택·혼합 | AI 후보 → 선택/편집/재지시. style_anchor는 확정 톤 시드에서만 동결 |
| 2 | 협업형 worldgen | 6종 엔티티·관계·연표·세계규칙 항목별 조정·승인·반려 | AI 제안 → 수정/추가/삭제 → 재생성 → 승인(human_approved_at) |
| 3 | 회차 골격/비트 사전 검토 | 회차 N 비트(목표·사건·종료상태·클리프행어) 조정 | 비트 카드 → 수정/순서변경. **MVP 디폴트=auto_advance**, 근경 1~2화만 검토 |
| 4 | 회차 생성 중 인터럽트 | "이 장면 다시"/"톤 바꿔"/"멈춰" | **장면 경계**(§6.7 정의) PAUSED → 흡수 → 재계획 (MVP는 직후 개입, 실시간 M2 §10·§16) |
| 5 | 회차 직후 승인/거부/수정 | 초안 finalize/재생성/미세수정 | 검토 뷰 → 승인(워터마크 전진)/거부/직접 수정(새 ChapterVersion) |
| 6 | 방향 전환(redirect) | "주인공 좌절시켜"/"이 조연 주역으로"/"톤 어둡게" | AuthorDirective 기록 → SOFT invariant·골격·비트 갱신 → 이후 회차 누적 전파 |
| 7 | escalation/분기점·**정체 강제 호출** | 기계가 자율 해결 불가로 올린 모순·분기, **또는 §4.4 liveness 트리거로 강제 surface** | request_human_input → EscalationBrief(근본원인·영향범위·캐논 diff·**충돌 시 과거 지시 출처 인용**·권장 3안) → 선택/재지시 |

### 4.2 ⚠️ 인지 부하 합산 — 도메인 가로질러 폭발 (기획 비평 높음 수용)
> 비평: "각 도메인이 '저마찰'을 약속하지만 부하는 도메인을 가로질러 합산. 한 회차 승인하려 앉은 작가는 review surface + 플래그 + 캐논 확인 카드 + provenance를 한꺼번에 마주한다."

**대책 — 회차당 의사결정 총량 하드캡 (1급 설계 제약)**:
- 도메인별로 따로 노는 InteractionFrictionMetric을 **"한 회차 승인에 작가가 마주하는 총 의사결정 수"** 단일 지표로 합산 계측.
- 회차당 능동 검토 항목을 **N개 이하로 하드캡**(N값은 M1 결정, §18.2 Q13), 나머지는 **막 경계로 배치 이연**.
- M1에서 "형식 통과율"(읽지 않고 승인) 측정 → 임계 이상이면 협업이 아니라 **책임 떠넘기기**이며 미검토 회차로 라우팅(§13.2). **형식 통과율은 §3.5 능동 결정 진정성 검증의 교차축으로도 사용**(무의미 토글 탐지).
- ⚠️ **인지 부하 하드캡과 세 최소선의 비양립 가능성**: 만약 M1에서 인지 부하 하드캡(예: 회차당 1결정)이 정체성 최소선(예: 3결정)·저작물성 최소선(예: 2결정)보다 작게 나오면 세 제약 동시 만족 불가. 이 경우 §16.0 분기 트리로.

### 4.3 협업 디폴트 정책 (확정)
- **request_human_input 발동 = 보수적 고정 디폴트**: critical 결정론 위반 + HARD invariant 침범 후보 + 지시 충돌 + **SOFT 복선 도달성 단절**(실패추적 시나리오2 수용)만 자동으로 물음.
- **DelegationLevel 슬라이더**(수동): 비트까지 자동 / 검토 후 진행 / 장면마다 확인.
- 적응형 학습 정책은 P2.
- **gray zone PAUSED 무한 정체 방어(확정)**: 작가 부재/미결정 시 자동 KILL 아님. 단 **"무한 잠정 진행"도 아니다** — §4.4 liveness 계약이 잠정 진행에 상한을 둔다. 회차 내부 잠정 진행은 best-so-far + 사후 정정 표기 + defect_debt 적재이되, **작품 단위 정체·defect_debt 절대 상한·HARD 침범 후보는 §4.4·§10.8 매트릭스가 우선 적용**한다.
- ⚠️ **무응답 정책의 단일 종료조건은 §10.8 직교 매트릭스로 통합**(잠정 진행 vs 동결 vs HARD 차단의 혼재 해소). **HARD invariant 침범 후보는 무응답이어도 절대 잠정 진행 금지·영구 동결**(hard rule).

### 4.4 ⚠️ 디폴트(유인) 모드 liveness 계약 (완결성 Blocker 신설 — 신규)
> 완결성 점검 Blocker: "v2의 work-레벨 liveness 불변식(72h hard·progress-monotone·deadlock-free)을 통째로 무인 모드 전용으로 강등하면서 디폴트 모드의 종료·비교착 보장을 신설하지 않음. auto_advance에서 '작가 부재 + 정체 + defect_debt 무한 적재 + 엔딩 비수렴'이 동시 성립하는 데드락 경로가 결정론적으로 열려 있고, convergence_probe는 D14로 강제전이 금지라 못 끊는다."

**원리**: MAX_ROUND/MAX_INTERRUPT(§6.4)는 **회차 내부** 진동만 막는다. 회차가 100편 흘러도 엔딩 required thread가 한 개도 안 줄어드는 **작품 단위 비수렴**은 못 잡는다. v3 final은 이를 막되, **KILL이 아니라 "강제 작가 surface"로 트리거를 재배선**한다 — liveness 침해는 시스템을 죽이는 사건이 아니라 **반드시 작가가 결정해야 하는 사건**이다.

**디폴트 모드 liveness 3대 계약 (전부 결정론, escalation 예산·KILL과 독립)**:

| # | 계약 | 지표 | 트리거 시 행동 (KILL 아님) |
|---|---|---|---|
| L1 | **progress-monotone → 강제 작가 surface** | `progress = 1 − (남은 required_thread 수 / 잔여 회차 예산)`. **K=15회차** 동안 `Δprogress ≈ 0` | **auto_advance 강제 해제 + request_human_input 발동**(EscalationBrief: 정체 진단 + major replan 제안 + "방향 전환/중단/계속" 3안). 작가 결정 전까지 자동 진행 차단 |
| L2 | **defect_debt 절대 상한 → 자동 진행 차단** | 막경계 정산(§9.5)과 별개로 `defect_debt 누적 ≥ ABS_CAP`(절대 상한) | **auto_advance 강제 해제 + 작가 결정 라우팅**(정산하거나 진행 승인하거나). 정산·승인 전까지 자동 진행 차단 |
| L3 | **'작가 부재 + 정체' 데드락 상태 모델 + 종료조건** | L1 또는 L2 트리거 발동 **AND** request_human_input 무응답 타임아웃(§10.8) 경과 | 상태 = `STALLED_AWAITING_AUTHOR`(명시 상태로 모델링). 종료조건 = §10.8 매트릭스 적용 → **동결(frozen, 작업물·세션 durable 보존) + 작가 알림(§10.9)**. 무인 모드만 72h hard KILL |

- **L1과 §18.2 Q5(의미적 충돌이 인간 라우팅 빈도를 잠식)의 결합**: '작가 부재 + 정체'가 동시 성립하면 그것을 `STALLED_AWAITING_AUTHOR`라는 **명시적 데드락 상태로 모델링**한다(은닉 무한루프 금지). 이 상태의 종료조건은 §10.8 무응답 매트릭스가 단일하게 정의한다 — **잠정 진행이 아니라 동결**이며, 작가 복귀 시에만 해제된다.
- **convergence_probe(D14)와의 관계**: convergence_probe는 여전히 "측정/제안만, 강제전이·KILL 금지"다. L1은 convergence_probe를 KILL 트리거로 승격하는 것이 **아니라**, 동일 지표를 **강제 작가 surface 트리거**로 쓴다(강제전이가 아니라 강제 호출). 둘은 직교한다.
- **ABS_CAP·K값 동결**: defect_debt 절대 상한과 K=15는 M1에서 데이터 보기 전 동결(타임스탬프 규약, §15.3).

---

## 5. 대상 작품 정의 (완결 100~200화, 점진 생성)

### 5.1 작품 스펙
| 항목 | 값 |
|---|---|
| 분량 | 100~200화 유한·완결형 중·장편 |
| 회당 분량 | 약 5,000~5,500자 (총 50만~110만 자) |
| 1차 장르 | **회귀·시간역행·변신 없는 단일 타임라인 현대 판타지** |
| timeline_branch_id | MVP 미도입 |

> ⚠️ **장르 제약 = 외부 타당성 한계(완결성 blindSpot 수용)**: '단일 타임라인'은 narrative_order 좌표계가 깨끗해서 **RAG 비발산이 가장 쉬운 best case**다. MVP가 여기서 성공해도 '회귀물'(시간 역행 = narrative_order 비단조 = RAG `as_of` 시점필터 붕괴) 같은 실제 웹소설 주류 장르로의 확장은 미평가다. 이는 §18.1 비목표의 'timeline_branch_id 미도입'을 넘어 **"현재 검증이 가장 쉬운 케이스라는 외부 타당성 한계"** 로 명시 표기한다 — successMetric(특히 r(회차) 비발산)을 회귀물 등으로 **일반화할 수 없음**을 successMetric 해석 시 동반 고지.

### 5.2 v3 고유 속성 — "살아있는 원고"
작품은 **'한 번에 뽑히는 산출물'이 아니라 '회차 단위로 점진 생성되며 작가 조향을 누적 흡수해 자라나는 원고'**.
- 엔딩 상태·주요 복선·막 구조는 worldgen 단계에서 협업형 사전 설계.
- 엔딩 **3계층**: 정체성 불변량(주제 해소 방향·승리 구조·핵심 인물 생사)만 HARD, 구체 경로·전술·부차 결말은 SOFT, 일시 연출은 best-effort.
- **완결 판정 = 기계 결정론 기준(엔딩 비트 도달 + required 복선 회수 + 아크 종결) AND 작가 본인 승인.**

### 5.3 타깃 작가 페르소나 (기획 비평 치명적 지적 수용)
> 비평: "'작가'가 단일 추상 명사로 50회 등장. 마감 압박형과 작품성 지향형은 정반대 제품을 원한다."

| 페르소나 | 특성 | v3 적합성 |
|---|---|---|
| **A. 마감 압박형 다작 작가** | 연재 3개 동시, 일 1.5만자, 쓰기=병목 | **높음** — v3가 구원 |
| **B. 작품성 지향 작가** | 쓰기=정체성, 기계 1차집필 거부감 | 낮음~중간 — "빨간 펜만 들어라"가 모욕일 수 있음 |

- **결정**: M1 인터뷰에서 "기계가 1차로 써준 회차를 승인·수정하는 방식"의 정서적 거부감 직접 측정. 거부감 임계 이상이면 **MVP 타깃을 A로 좁힌다.** "작가 일반"을 타깃으로 두는 한 누구에게도 정확히 안 맞는다.
- ⚠️ **페르소나 분리 측정의 통계적 검정력(완결성 blindSpot 수용)**: 페르소나 A/B 분리 측정은 각 셀에 충분한 표본이 필요하다. 협력 작가 조달 난이도(공모전 입상자·제휴)를 고려하면 N이 한 자릿수일 가능성이 높고, 그러면 M1 GO/NO-GO 판정이 노이즈에 묻힌다. → **M1 표본 수 N과 검정력은 §16에서 "의사결정 가능한 신뢰구간을 주는 최소 N"으로 사전 정의**하고, N이 부족하면 그 측정 결과를 "정량 판정"이 아니라 "정성 신호"로 강등 표기(결과를 데이터 보기 전 강등 규칙으로 동결).

---

## 6. 핵심: 에이전틱 하네스 설계

### 6.1 설계 원칙
**전진·검증·라우팅은 결정론, LLM 자유도는 "생성"과 "의미 추출"에만 핀포인트 격리.** 에이전트 자유도가 높을수록 안정성 보장이 어렵다(hardestProblems #1).

### 6.2 2계층 구조 다이어그램
```
┌─────────────────────────────────────────────────────────────────┐
│ Temporal Workflow (durable 실행 진실원, 단일 권위)                  │
│  회차 1편 = child workflow,  SignalWorkflow=PAUSED,  continue-as-new │
│  ※ work-레벨 liveness(§4.4 L1/L2/L3) = 부모 workflow가 결정론 평가    │
│ ┌───────────────────────────────────────────────────────────────┐ │
│ │ LangGraph StateGraph (계층 A — 회차 에이전트 루프)               │ │
│ │  ※ Temporal activity 내부에서만 단명 실행 (체크포인터 이중화 금지)│ │
│ │                                                                 │ │
│ │  plan_chapter ─► assemble_memory ─► forget_gate ──┐             │ │
│ │   (활성 Directive   (rag_search ∥    (required_facts │            │ │
│ │    + 골격/비트)      ontology_lookup)  커버리지)      │            │ │
│ │        ▲                                            ▼            │ │
│ │        │                                      draft_scene       │ │
│ │        │                                    (장면 단위 스트리밍,  │ │
│ │        │                                     §6.7 장면 경계 정의) │ │
│ │   reflect ◄── critique + consistency_check ◄────┤               │ │
│ │  (위반 시 partial_rewrite,                       │               │ │
│ │   best-so-far monotone,                    [장면 루프]           │ │
│ │   MAX_ROUND/MAX_INTERRUPT 하드캡)                │               │ │
│ │        │                                         ▼               │ │
│ │        └──────────────────────────────► human_review ─► finalize │ │
│ └───────────────────────────────────────────────────────────────┘ │
│                                          │ finalize 이벤트          │
│                                          ▼ (§12.4-5 팬아웃 계약)     │
└─────────────────────────────────────────────────────────────────┘
         ▲ SignalWorkflow                   │ request_human_input
         │ (인터럽트 소스)                    ▼ (외부 도구)
┌─────────────────────────────────────────────────────────────────┐
│ 계층 B — collaboration orchestrator                                │
│  "결정론 중재기 + 소형 LLM 보조" 하이브리드                          │
│  · 인터럽트 라우팅(결정론) · writer 권위 중재(결정론, split-brain 방지) │
│  · 지시 충돌 판정(결정론)   · request_human_input 발동 정책(결정론)    │
│  · §4.4 liveness 트리거 → 강제 작가 surface 라우팅(결정론)            │
│  · §10.8 무응답 매트릭스 집행(결정론)                                 │
│  · LLM은 EscalationBrief 요약·권장3안 같은 best-effort 보조만         │
└─────────────────────────────────────────────────────────────────┘
```

### 6.3 도구셋 (toolset)
| 도구 | 종류 | 신뢰등급 | 역할 |
|---|---|---|---|
| `rag_search` | 검색 | 서사(낮음) | 이전 회차 하이브리드 검색→서사 배경, 슬롯 cap, trust 가중. **미검토 회차 trust 하향(§8.5)** |
| `ontology_lookup` | 결정론 | **ground truth** | 생사·소속·능력수치·관계, 상단 고정·누락0·동기경로 LLM 금지 |
| `draft_scene` | 생성 | — | 장면 단위 산문(스트리밍), 인터럽트 단위=장면 경계(§6.7) |
| `critique` | judge | 의미(제안) | 이종 N≥3, generator≠judge, 다축 비평(**안전 축 포함, §9.5**). **단독 KILL 권위 없음→작가 라우팅** |
| `consistency_check` | 결정론 | ground truth | 충돌 4종 + 시점 + 워터마크 게이트. 유일 신뢰 검증 |
| `prose_lint` | 결정론 | — | Kiwi/KSS 종결어미 반복·n-gram·금칙어. 무LLM |
| `payoff_check` | 결정론+RAG | — | PlotThread 터치체인, ClueSpanAnchor 대조, false payoff 방어 |
| `request_human_input` | HITL | — | EscalationBrief로 작가에게 분기 라우팅. **무응답 종료조건=§10.8.** 빈도=마찰 지표(KILL 아님) |
| `apply_author_directive` | 주입 | 권위 채널 | 활성 지시를 plan 컨텍스트 상단 고정(슬롯 cap 경쟁 배제) |
| `detect_directive_conflict` | 결정론 | — | 지시 충돌·HARD/승인분/finalized 침범 100% 검출→자동 진행 금지. **작가 자기모순 시 과거 지시 출처 회수(§11.2)** |
| `emit_canon_proposal` | 구조화 | 등급별 | 추출 사실 staged 제안, 순수 결정론분만 자동확정 |
| `acquire/release_writer_lock` | 결정론 | — | Chapter 단일 writer 런타임 집행(advisory lock, **lease 만료=§10.8**) |

### 6.4 계획-집필-비평-수정 패턴 + 안정성 가드
- **장면 단위 미니루프**: `draft_scene → consistency_check ∥ prose_lint → critique → (위반 시) partial_rewrite → seam_reweave → 재검증`
- **종료조건**: 회차 비트 충족 + 결정론 critical=0 + judge 핵심축 통과 + 작가 승인
- **안정성 가드(전부 결정론)**: `MAX_ROUND`(3~4), `best-so-far monotone`(비악화), 과교정 가드(regression/new_violation/divergence), `oscillation_signature` 해시 EWMA, `MAX_INTERRUPT`(같은 장면 거부 상한, 초과 시 "직접 집필 or 3안 강제 선택")
- ⚠️ **회차 내부 가드 vs 작품 단위 liveness**: 위 가드는 전부 **회차 내부** 진동·무한루프만 막는다. **회차가 무한히 흘러도 엔딩에 수렴 안 하는 작품 단위 비수렴은 §4.4가 담당**한다(둘은 다른 층위).

### 6.5 ⚠️ judge 호출 비용·지연 (아키텍처 비평 Med 수용)
> "장면이 작을수록 인터럽트 반응성↑이나 judge 앙상블 호출이 장면 수에 선형 증가→p95 지연 폭발."

**결정**: 결정론 검증(`prose_lint`/`consistency_check`)은 **매 장면**, 의미 judge(`critique`)는 **회차 finalize 직전 1회 + 결정론 critical 발생 장면만**. judge 호출 빈도를 장면 수에 선형이 아닌 **회차당 상수**로. 회차당 장면 수 상한·장면당 judge 예산·TTFT·장면 완료 p95를 측정 지표로. **이 비용 모델은 §6.7 장면 경계 정의에 의존**(장면이 너무 짧으면 judge 비용 모델이 깨지므로 장면 크기 하한 필요).

### 6.6 외부 LLM 의존성 장애 처리 (아키텍처 비평 blindSpot 수용)
judge 앙상블 N≥3은 여러 벤더. 한 벤더 장애 시 타임아웃 + 서킷브레이커. **degraded mode**: N=3 중 1개 장애 시 N=2 진행(judge_agreement에 degraded 플래그), 2개 이상 장애 시 회차 PAUSED(**작가 통지=§10.9 에러 상태 가시성 계약**). 무LLM 결정론 게이트는 영향 없음.

### 6.7 ⚠️ 장면(scene) 경계 정의 (완결성 blindSpot 수용 — 신규)
> 완결성 점검: "§10.1이 토큰 인터럽트를 폐기하고 '장면 경계'를 인터럽트/체크포인트 단위로 확정했는데, '장면'을 누가/어떻게 결정하는지가 어디에도 없다. 장면 경계가 LLM 동적 판정이면 그것 자체가 비결정 자유도이고, 장면 수 상한·MAX_INTERRUPT·인터럽트 반응성(p95)이 전부 이 미정의 단위에 의존한다."

**확정 — 장면 경계는 결정론 산출물, LLM 동적 판정 금지**:
- 장면 분할은 **`plan_chapter` 단계에서 비트(Beat)→장면 사전 분해로 결정**된다. 한 비트의 `key_events` 시퀀스를 결정론 규칙(이벤트 경계·시공간 전환·시점 인물 전환)으로 장면 단위로 나누고, **draft 전에 `scene_plan`(고정 리스트)으로 확정**한다. `draft_scene`은 이 사전 확정 리스트를 순회할 뿐 경계를 새로 긋지 않는다.
- **장면 크기 가드(M1 측정 임계)**: 장면 길이 상한(인터럽트 반응성 보장: 작가가 '멈춰'를 눌러도 긴 장면 끝까지 대기하지 않도록 장면당 최대 토큰/자수 상한) + 장면 길이 하한(§6.5 judge 비용 모델 보호). **이 상·하한 트레이드오프 임계는 M1 측정 항목**(§16)으로, 회차당 장면 수 상한·인터럽트 반응성 p95와 함께 동결.
- LLM이 생성 중 비트보다 더 긴 분량을 내려 해도 `scene_plan` 경계를 넘으면 강제 분절(over-run은 다음 장면으로 이월). 따라서 MAX_INTERRUPT·장면 수 상한·반응성 p95가 **결정론 단위 위에서 정의**된다.

---

## 7. 핵심: 회차 단위 생성 루프

### 7.1 점진 생성 함수
```
회차 N = f( RAG(이전 회차 1..N-1),   ← 서사 배경 "찾기" (rag_search, trust 등급별 가중)
            세계관/설정 SSOT,         ← 정형 캐논 "박기" (ontology_lookup)
            해당 회차 골격/비트,       ← arc 도메인 (auto_advance 디폴트)
            누적 AuthorDirective )    ← 작가 조향, 권위 채널 상단 고정
```

### 7.2 End-to-End 흐름 (한 회차 루프)
```
[1] 골격 선택   skeleton_fetch(N) — confirmed beat + required_facts (결정론 select)
       │        (DelegationLevel에 따라 비트 검토 게이트 노출 or 생략)
       ▼
[2] 기억 조립   ContextBuilder (LangGraph 고정 상태그래프, 자유 에이전트 아님)
       │        as_of = last_indexed 워터마크
       │        ├ rag_search        → 서사 배경 (cap, trust_weight 등급별)  [하단]
       │        ├ ontology_lookup    → 정형 캐논 (누락0)                [상단 고정]
       │        └ apply_author_directive → 활성 지시                     [상단·cap 배제]
       ▼
[3] 망각 게이트  forget_gate — required_facts 100% 커버리지 PRE-GEN 검증
       │        미커버 → targeted_refetch / force_ontology_inject → K회 실패 시 request_human_input
       ▼
[4] 산문 생성   draft_scene (scene_plan 순회, 장면 경계 §6.7 = 인터럽트 체크포인트)
       ▼
[5] 자가교정    consistency_check ∥ prose_lint (매 장면) → critique (finalize 직전, 안전 축 포함) → reflect
       │        best-so-far monotone + MAX_ROUND
       ▼
[6] 인간 검토   human_review — 승인 / 거부(재생성) / 미세수정(새 ChapterVersion)
       │        ※ MVP 디폴트 auto_advance: 근경 1~2화만 노출, 나머지 자동
       │        ※ 안전 게이트 발동 시 auto_advance 강제 해제(§9.5) / liveness 트리거 시 강제 surface(§4.4)
       ▼
[7] finalize    finalize 이벤트 단독 발행 → 워터마크 3종 전진
       │        → 메모리 반영 팬아웃 (3중 컨슈머, 멱등 워커, §12.4-5 계약)
       │        → auto_advance(미검토) 회차는 색인 메타에 trust='unreviewed_machine'+provisional(§8.5)
       ▼
       회차 N+1 의 f() 입력  ← 누적 점진 구조
       │
       └─ [부모 workflow] §4.4 liveness L1/L2 평가 (progress Δ, defect_debt 누적)
```

### 7.3 작가 변경의 이후 회차 전파
- (a) AuthorDirective 레저 기록 → 이후 모든 회차 f()의 작가지시 입력에 누적
- (b) 영향받는 EndingSpec **SOFT** invariant·골격·비트 갱신 (HARD는 §7.5 승인 안정성 계약 보호)
- (c) 수정 회차 본문은 단방향 재색인 루프(versionId, is_active 단일)로 다음 회차 기억 기질에 반영
- **"한 번 조향하면 그 키를 잡은 채 항해가 이어진다."**

### 7.4 ⚠️ 회차당 LLM 호출 수 스케일 (아키텍처 비평 blindSpot 수용)
회차당 호출 = plan(1) + retrieve(병렬) + draft_scene(장면 수 × 평균 재집필) + critique(1, §6.5) + reflect. 100~200화 × 회차당 호출 = 곱셈 폭증. **호출 수는 비용이 아니라 지연·레이트리밋·장애전파의 원천.** M1에서 회차당/작품당 호출 수 상한과 스케일 곡선 실측(§16).

### 7.5 승인 안정성 계약 (v2 §7.5 계승)
human_approved_at 마킹분은 retro-repair 자동 대상 제외. HARD invariant·승인 회차는 작가 지시로도 침범 금지(침범 시 자동 진행 금지 → request_human_input → 무응답 시 §10.8 영구 동결).

---

## 8. RAG 기억 & 회차 연속성

### 8.1 목표의 정직한 재프레이밍
> ❌ "연속성을 측정 가능한 수준으로 **보장**한다" (과약속)
> ✅ "연속성 **발산 방지(non-divergence)** + 인간 in-the-loop **조기교정으로 사각지대 보완** + 인간 검토 부하 sub-linear"

세 구조적 천장: (a) **암묵적 캐논의 쿼리 미발생**(검색정확도로 안 풀림), (b) 캐논 역추출 의미추론 신뢰도, (c) r(회차)용 ground-truth 모순 라벨 부재.

### 8.2 ContextBuilder = 결정론 파이프라인 (자유 에이전트 아님)
LangGraph 고정 상태그래프. LLM 자유도는 **2곳만 격리**:
```
select_skeleton → plan_queries★ → retrieve → forget_gate → assemble → emit_provenance
                  (★LLM: 쿼리 생성)              (LLM 없음: 결정론 커버리지)
재색인 서브그래프: extract_canon★ → classify_grade → conflict_check → reindex
                  (★LLM: 캐논 역추출)
```
★ 두 곳 외 전부 결정론. forget_gate 재시도 K회 하드캡 + MAX_ROUND.

### 8.3 인덱싱·검색·주입 (비대칭)
| 단계 | 처리 |
|---|---|
| **인덱싱** | finalize된 회차만 RagIndexUnit≡ContentChunk 단일 스키마 청킹·임베딩(pgvector HNSW). narrative_order·versionId·signal_grade·**trust_tier**·trust_weight·entity/alias 메타. **auto_advance(미검토) 회차는 trust_tier='unreviewed_machine' + provisional 플래그(§8.5)** |
| **검색** | `rag_search` as_of=last_indexed 시점필터. Kiwi BM25 + 벡터 RRF + 메타 사전필터. 엔티티명/alias 사전 자동등록(고유명사 회수 보강). **trust_tier별 가중 적용(§8.5)** |
| **주입(비대칭)** | `ontology_lookup` = **상단 고정·누락0**(스키마상 분리필드 강제). `rag_search` = **슬롯 cap·trust 가중·하단**. 혼합 불가 |
| **자기섭취 차단** | quarantine/저신뢰/미승인 회차 trust_weight 하향. style_anchor·few-shot 동결 시드만. 위력/전투강도 RAG 재참조 금지(EndingSpec power 앵커만) |

### 8.4 불변식 (메트릭 아님 — DB 강제) — 아키텍처 비평 수용
"측정해서 0이길 바라는 게 아니라 0이 아닐 수 없게":
- `stale 청크율 = 0` → **is_active 논리슬롯당 unique DB 제약**
- `ontology lookup 누락 = 0` → **assemble의 ontology 절단금지 어서션**
- 워터마크 3종 단조성 → **체크 제약**
- ⚠️ **단조성 ≠ 함께 전진**: 워터마크 단조성은 'A가 B보다 앞서지 않음'만 보장할 뿐 '셋이 함께 전진'은 보장하지 않는다(한 컨슈머만 멈추면 영구 desync). **'함께 전진' 보장은 §12.4-5 finalize 팬아웃 계약**에서 다룬다(state_applied는 모든 컨슈머 ack 후에만 전진).

반대로 `r(회차)`·`자기섭취 단조하락`은 **진단지표로 강등**(ground-truth 라벨 없이 신뢰성 측정 불가). 의미 모순은 정기 인간/judge 샘플감사로 보정 라벨.

### 8.5 작가 편집 재색인 + 미검토 회차 오염 격리 — "재확인 후 무결성" (Blocker + 완결성 High 수용)
> 자동 무결성 100%는 원리적 불가(LLM 준결정론). "자동"이 아니라 "재확인 후" 보장으로 격하.
> ⚠️ 완결성 High: "auto_advance 미검토 회차도 finalize→RAG 색인→이후 회차 f() 입력이 된다. 막경계 감사가 모순을 탐지한 시점엔 이미 그 미검토 회차가 수십 회차 RAG 기질로 작동해 후속에 의미 오염을 전파한 뒤다. §8.6 결정론 오염(ontology) 방어는 이 '기계 미검토 생성→RAG 의미 오염' 경로를 못 막는다 — 미검토 회차는 quarantine이 아니라 정상 finalize분이라 trust_weight가 정상이기 때문."

**작가 편집 경로 (기존)**:
- 캐논-affecting 편집은 (a) **구조화 편집 표면(엔티티 속성 직접 토글)으로 유도**, (b) 자유 본문 편집 시 diff 기반 캐논 변화 후보를 **staged 강제 확인**(requires_author_confirm).
- **signal_grade=semantic(사망↔생존 등 비가역 전이)은 자동확정 절대 금지.** 미확인 staged 존재 시 다음 회차 ontology_lookup에 **suspect flag** 노출.
- generator≠extractor. **MVP는 자유 본문 자동 캐논 갱신 보류**, recall은 M1 선결 측정(§16).

**미검토 회차 RAG 오염 격리 경로 (신규)**:
1. **trust 등급 신설**: auto_advance(미검토) 회차에 trust_tier=`unreviewed_machine` 부여. 후속 `rag_search`에서 **검토된 회차(`reviewed`) 대비 trust_weight 하향** — 미검토 회차의 서사 진술이 ground truth처럼 후속에 박히지 않도록.
2. **provisional 색인 메타**: 막경계 감사 통과 전까지 색인 메타에 `provisional=true`. 막경계 감사에서 의미 모순 미발견 시 `reviewed`로 승격, **모순 발견 시 quarantine으로 강등 + 단방향 재색인 루프로 RAG 기질에서 격하**(versionId, is_active 단일 — §8.3 단방향 루프 재사용). 이 강등 경로를 §8.6 결정론 오염 방어와 대칭으로 추가.
3. **연속 미검토 회차 수 상한**: `MAX_CONSECUTIVE_UNREVIEWED`(M1 동결). 초과 시 **최소 1회 작가 검토 강제**(§13.3 requires_review 승격 재사용) — RAG 기질 오염의 무한 누적 차단.

### 8.6 ⚠️ 결정론 오염 경로 + 작가 자기모순/실수 방어 (실패추적 시나리오3 + 완결성 High — Blocker)
> recall<1로 작가가 본문에서 '사망→생존'을 우회 표현으로 바꿨는데 추출기가 놓치면, 틀린 사실이 ontology_lookup의 **상단 고정·누락0 ground truth**로 이후 전 회차에 결정론적으로 박힘. RAG trust_weight 하향으로 못 막음(정형 캐논이라).
> ⚠️ 완결성 High: "모든 충돌 방어가 '시스템 오류 vs 작가 의도'에서 작가를 항상 옳다고 전제한다. 작가가 자유편집으로 만든 desync를 막경계 감사가 잡아도, 그 desync가 '작가 의도'인지 '작가 실수'인지 구분하는 계약이 없어 시스템이 임의로 한쪽을 택하게 된다."

**결정론 오염 방어 (기존)**: (1) 비가역 상태전이류(생사·소속·능력영구상실)는 본문 자유 편집 자체를 비활성화, **구조화 토글로만**. (2) 캐논-affecting 변경은 단순 승인 클릭이 아니라 **바뀐 값을 작가가 직접 재입력/확정**하는 능동 확인. (3) 막경계 무작위 감사를 **'본문 진술 vs SSOT 양방향 대조'**(desync 포착)로. (4) ontology_lookup이 최근 작가 편집 span 인접 엔티티 상태를 박을 때 suspect flag 동반.

**작가 실수/변심 방어 (신규)**:
- **(5) 의도-실수 양자택일 게이트**: 막경계 desync 감사 결과를 작가에게 surface할 때 시스템이 임의 판정 금지. **"이것이 의도한 변경입니까, 아니면 수정해야 할 오류입니까?"** 를 명시 질문하는 양자택일 게이트. '의도'면 그 값을 새 ground truth로 채택(능동 재입력 동반), '오류'면 SSOT로 본문 정정 경로.
- **(6) 미검토 회차 모순 발견 → §8.5-2 quarantine 강등 경로 연결**: 막경계 감사가 미검토 회차에서 모순 발견 시 양자택일 게이트 + provisional→quarantine 강등.

### 8.7 계층 요약 + 암묵적 캐논 처방
- **갱신 2단**: 회차요약=매 finalize, 아크/작품요약=경계+N회 증분.
- **비가역 무손실 필드 범위 확장**: 사망·소멸 + **능력 영구상실·관계 비가역 파탄(배신·절연)·핵심 비밀 폭로**(후반 "이미 폭로된 비밀을 다시 비밀처럼" 모순 방지).
- **암묵적 캐논 사각지대 처방(다층)**: plan_queries 엔티티 후보 쿼리 자동확장 + 인물별 누적 사실 카드 + 검토뷰 '이 인물 과거 사실' 패널. 엔티티 그래프는 P2이되 **승격 트리거 사전정의**: 인물 재등장 모순율 임계 초과 시 P2→P1.

---

## 9. 기능 명세 (6 도메인 · P0/P1/P2)

> P0=MVP 필수, P1=MVP 후 1차 강화, P2=후속.

### 9.1 도메인 A — 협업 인터랙션
| 기능 | P |
|---|---|
| 협업형 worldgen 캔버스(제안→조정→점진 승인) | P0 |
| 회차 직후 검토·승인·거부·수정 표면(동기 게이트) | P0 |
| AuthorDirective 레저 + 기본 전파 엔진 + detect_directive_conflict + **과거 지시 출처 회수(§11.2)** | P0 |
| 회차 골격/비트 사전 검토(DelegationLevel, auto_advance 디폴트) | P0 |
| **장면 경계 인터럽트**(토큰 인터럽트 아님, 경계 정의 §6.7) | P0(우경량화) |
| writer 권위 동시성(writer_lock_holder + CAS + **lease 만료 §10.8**, CRDT 본문 미채택) | P0 |
| escalation 협업 결정(EscalationBrief, 충돌 시 과거 지시 인용) | P0 |
| **§4.4 liveness 강제 작가 surface(정체/데드락) + §10.9 에러 상태 가시성** | **P0**(완결성 Blocker) |
| **§10.8 무응답 단일 종료조건 매트릭스 집행** | **P0**(완결성 Blocker) |
| provenance surfacing(ontology vs rag 분리 렌더, **trust_tier 표시**) | **P0**(신뢰붕괴 방지 승격) |
| directive_canon_reconcile(generator≠extractor + 재확인 게이트 + 의도-실수 양자택일) | P0(자동 무결성 폐기) |
| AuthorCognitiveState durable 복원 | P0 |
| 방향전환 풀기능 + HARD/SOFT 동적 가드 정교화 | P1 |
| undo·ChapterVersion diff·지시 히스토리 영향 표시 | P1 |
| 개입 마찰 계측 고도화 + 적응형 빈도 | P1+ |
| 자율도 캘리브레이션·점진 위임 자동확대 / 다회차 일괄 조향·인수인계 | P2 |
| 생성 중 실시간(토큰 인접) 인터럽트 | M2(P0 아님, §10) |

### 9.2 도메인 B — 에이전틱 하네스
| 기능 | P |
|---|---|
| 회차 에이전트 루프(LangGraph × Temporal child) | P0 |
| 도구셋(§6.3) 12종 | P0 |
| 계획-집필-비평-수정 미니루프 + 안정성 가드(§6.4) | P0 |
| **장면 경계 결정론 분할(scene_plan, §6.7)** | P0 |
| **부모 workflow §4.4 liveness L1/L2/L3 평가(결정론)** | P0(완결성 Blocker) |
| degraded mode(벤더 장애 N=2 진행) | P0 |
| 적응형 도구 선택 정책 | P2 |

### 9.3 도메인 C — RAG 기억
| 기능 | P |
|---|---|
| RagIndexUnit 단일스키마 색인 + pgvector HNSW + 한국어 FTS/alias 사전 | P0 |
| 하이브리드 회수 rag_search(**trust_tier별 가중**) | P0 |
| as_of 워터마크 ContextBuilder(고정 상태그래프) | P0 |
| 온톨로지 lookup vs RAG 분리결합 | P0 |
| 망각 게이트(required_facts 커버리지) | P0 |
| 작가편집→캐논역추출→단방향 재색인(semantic 자동확정 금지) | P0 |
| **미검토 회차 trust_tier='unreviewed_machine' + provisional + 연속 상한 + 모순 시 quarantine 강등(§8.5)** | **P0**(완결성 High) |
| 불변식 강제(is_active unique, 워터마크 단조성) | P0 |
| 계층요약 2단 + 비가역 무손실 확장 / ProvenanceEdge 검토뷰 / 핀·제외·trust 조정 / r(회차) 모니터 | P1 |
| 적응형 슬롯 재배분 / 엔티티 그래프 / 임베딩 마이그레이션 | P2 |

### 9.4 도메인 D — 협업 worldgen
| 기능 | P |
|---|---|
| 시드 인테이크 + 장르/톤 후보 제안 | P0 |
| 시드→온톨로지 SSOT 부트스트랩 생성기 | P0 |
| propose-adjust-approve 협업 루프(LangGraph, MAX_ROUND, 국소 재생성) | P0 |
| 결정론 4종 게이트 + **세계규칙 상호 충돌 매트릭스 전수 제시** | P0 |
| judge 패널 = **우선순위 산출기**(차단 권위 없음) | P0 |
| EntityStateTimeline 시점 + ontology_lookup 결정론 공급 | P0 |
| EndingSpec 3계층(HARD work_run fork만 변경) | P0 |
| AuthorDirective validator(commit 전 HARD/승인분 침범 차단) + blast_radius_preview(보수적 과대추정) | P0 |
| 구조화 편집 표면 + 자유본문 staged 강제확인(자동갱신 보류) | P0 |
| escalation 무응답 fallback(**§10.8 매트릭스로 통합** — durable timer + 동결) | P0 |
| 드리프트 모니터 / 깊이·완결성 스코어러 | P1 |
| 관계 그래프 시각화·변경이력·보이스 프로파일 | P2 |

### 9.5 도메인 E — 회차 품질·일관성
| 기능 | P |
|---|---|
| 결정론 검증 코어(충돌 4종 + 시점 + 워터마크, 무LLM) | P0 |
| 한국어 prose 린터(Kiwi/KSS, 무LLM) | P0 |
| 이종 judge 앙상블 critique(generator≠judge, N≥3, 우선순위 라우팅, **안전 축 포함**) | P0 |
| 자가교정 미니루프(monotone + 과교정 가드 + oscillation) | P0 |
| 게이트 판정 pass/regenerate/**surface-to-author** | P0 |
| 회차 직후 review surface + EscalationBrief 생성 | P0 |
| signal_grade 엄격 3등급 분리 | P0 |
| 익명 패널 α≥0.7 **1회 객관 닻**(judge recall·작가편향 보정) | P0(필수 측정) |
| 작가판단 학습 레저(노출만 조정, 탐지 안 끔, critical 제외) | P0 |
| defect_debt + 막경계 정산 게이트 + **절대 상한 §4.4 L2** | P0 |
| **콘텐츠 안전·유해성 게이트(장면 단위 + 막경계 누적 + auto_advance 책임 분리)** | **P0** |
| **회차 간 seam 검증(소유=selfcorrect, §15.4)** | P0 |
| 의미 모순 감지 / 부분 리라이트 재봉합 / 준결정론 누적 감시 | P1 |
| 드리프트·테마표류 측정(알람만, Goodhart 금지) / 표절 근접도 | P2 |

> ⚠️ **콘텐츠 안전 게이트(P0, 완결성 Med 보강)**: v2 §0.2가 1급 트리거로 둔 '콘텐츠 안전 차단 후 재생성 실패'가 v3 초안에 없었음. 100~200화 자동 생성의 폭력·성적·혐오 누적 드리프트는 결정론 4종이 못 잡고 critique 축에도 없었음.
> - **장면 단위 게이트**: `critique` 비대칭 1슬롯(안전 축) + 결정론 금칙어 사전. degraded 시 차단.
> - **⚠️ 막경계 누적 감사(신규)**: 장면 단위 게이트의 false negative(누적 드리프트를 장면 단위로 못 잡음) 방어를 위해 **§13.2 막경계 감사에 안전 축 추가** — 작품 단위 누적 드리프트를 막경계에서 별도 측정.
> - **⚠️ auto_advance 책임 분리(신규)**: '작가 승인이 안전 책임을 진다'와 'auto_advance=작가 미검토'의 충돌 해소. **미검토 회차에서 안전 게이트 발동 시 auto_advance를 강제 해제하고 작가 검토 강제**(§13.3 requires_review 승격 재사용). 즉 미검토 회차의 안전 책임은 **'작가 승인 책임'이 아니라 '시스템이 차단/surface 책임'** 으로 분리. 책임 귀속의 법적 모순은 §3.6 변호사 자문 범위.

### 9.6 도메인 F — 스토리 구조 진행
| 기능 | P |
|---|---|
| 막/아크/회차 골격 점진 생성(근경 정밀/원경 윤곽, 체커 입력 계약) | P0 |
| ChapterSkeletonConsumeLoop(Temporal child, **결정론 상태머신**) | P0 |
| DeterministicStructureChecker(도달성/단조성/ID무결성/HARD·승인 비침범, 무LLM) | P0 |
| 비트 카드 검토(review_mode=**auto_advance 디폴트**) | P0 |
| AuthorDirective 흡수(**local 재계획만**, 3줄 규칙) | P0 |
| payoff_check + ClueSpanAnchor(plant 시점 결정론 못박기) | P0 |
| convergence_probe(**측정/제안만**, 강제전이·KILL 승격 금지). **단 동일 지표를 §4.4 L1 강제 작가 surface 트리거로 사용**(강제전이≠강제호출, 직교) | P0 |
| **draft→arc registered_from_draft 신규 thread 수신 계약 + 0건 누락 불변식** | P0 |
| RePlanner major 재계획 / AuthorDirective 풀 충돌·만료·소급 엔진 | P1 |
| 진행 대시보드 / 골격 분기 시뮬(full) / 장기 아크 감사 | P1~P2 |

---

## 10. 생성 중 인간 개입 동시성 설계

### 10.1 ⚠️ 정직한 우경량화 — 토큰 인터럽트 폐기 (아키텍처/실패추적 Blocker)
> "산문 토큰 스트리밍 도중 일시정지"는 기술 환상. Temporal SignalWorkflow는 in-flight activity(LLM 호출 1건)를 강제 취소 못 하고 signal은 activity 완료 후 관찰됨. LangGraph interrupt도 노드 경계만. 토큰 중단 시 부분 산출물 일관성 보장 불가.

**확정**: 실제 구현 단위 = **장면(scene) 경계 인터럽트**(경계 정의 §6.7). UX 카피도 "다음 장면 경계에서 정지"로. 기본값="현재 장면 완료 후 정지", 옵션="현재 장면 폐기 후 즉시 정지". `draft_scene`을 사전 확정 scene_plan으로 분할, 장면 경계만 체크포인트.

### 10.2 ⚠️ MVP는 "직후 개입"만 — 실시간은 M2 (실패추적 시나리오1)
> 실시간 인터럽트를 MVP P0로 두면 동시성 난이도가 한 자릿수 배 폭증.

- **MVP**: 회차 직후 검토(post-generation) 개입만.
- **M2**: 장면 경계 실시간 인터럽트.
- **단, 동시성 골격(append-only, 단일 writer, never-interrupt-running-tool)은 MVP에 미리 박아 M2 재설계 회피.**
- **기대치 관리**: MVP를 "실시간 협업"으로 마케팅하지 말고 정직하게 **"회차 검토형 코파일럿"**으로 카피를 좁힌다(실패추적 시나리오1: 안 그러면 작가가 '배치 거부→재생성 룰렛'을 겪고 v1 회귀).

### 10.3 동시성 핵심 원칙
| 원칙 | 구현 |
|---|---|
| **never interrupt a running tool** | 작가 신호는 실행 중 도구를 죽이지 않고 도구 완료 체크포인트에서만 흡수 |
| **본문 writer 한 시점 정확히 하나** | writer_lock_holder ∈ {agent, human, none}, advisory lock 직렬화, **lease 만료(§10.8)** |
| **append-only 새 ChapterVersion** | supersedes 체인, is_active 단일 → lost update 구조적 불가 |
| **CRDT 본문 미채택** | CRDT는 의미적 충돌 해소 못 함, 단일 writer 계약과 충돌. worldgen 캔버스/지시 메모에만 선택적 |
| **플래그/검토 메타는 낙관적 락** | version CAS (별개 row라 충돌 적음) |

### 10.4 인터럽트 시퀀스
```
작가 '멈춰' 신호 → control endpoint → Temporal SignalWorkflow(PAUSED)
  → 현재 draft_scene activity 완료 대기 (best-so-far 저장, 중간 절단 금지=split-brain 방지)
  → writer 권위 핸드오프 agent→none→human (advisory lock, lease 부여)
  → 인간 지시 Observe 흡수 / 직접 편집(새 ChapterVersion source=human_edited)
       ※ ⚠️ 인간이 응답 무한 지연 시: §10.8 매트릭스 → writer lease 만료 시 자동 회수
         (작업물 보존, human→none→agent reacquire). advisory lock 영구 human 점유 방지.
  → detect_directive_conflict (충돌/HARD 침범 시 request_human_input → 무응답 시 §10.8)
  → writer 권위 human→none→agent reacquire (lease 만료 또는 작가 명시 반납)
  → 에이전트는 '인간이 만든 새 active 버전' 위에서 재계획 (이전 in-flight 폐기 or supersedes 보존)
  → 재개
```

### 10.5 ⚠️ durable 복원의 의미적 손실 (실패추적 시나리오8)
> Temporal replay는 activity journaling 재현이지 LLM 동일출력 보장 아님(temperature>0). chaos 테스트 '구조적 손실 0'을 통과해도 '작가가 본 것 vs 재생성된 것'의 의미적 불일치는 복원 안 됨.

**방어**: (1) **스트리밍으로 작가에게 노출된 부분 생성물에 즉시 멱등 체크포인트** — 작가가 본 순간 = 커밋 경계. 안 보인 in-flight만 재실행. (2) 작가 Signal은 ack 기반 durable 기록(누락·중복 방지). (3) 재개 시 **"여기서 중단되었고 마지막 본 장면은 다시 생성됨"을 명시 고지(은폐 금지 — 투명성=신뢰 방어)**. (4) **재개 후 작가 재확인 게이트** 필수.

### 10.6 동시 다중 세션 (아키텍처 비평 blindSpot)
같은 작가 여러 디바이스/탭 동시 접속 시 human vs human 충돌. advisory lock 키에 **session_id 차원 추가**, 후속 세션 read-only 또는 명시 takeover.

### 10.7 골격 그래프 동시성 (스토리 구조)
골격은 "공유 가변 트리". 노드별 advisory lock + node_version CAS + 작가 편집 우선권. **major 재계획 시 영향 range = 단일 트랜잭션 경계로 freeze**(부분 freeze 금지 — 실패추적 시나리오5). human 락에 durable timeout(**§10.8 매트릭스로 통합**). seam 연속성을 DeterministicStructureChecker가 commit 전 검증.

### 10.8 ⚠️ 무응답 단일 종료조건 — 직교 매트릭스 (완결성 Blocker 신설 — 신규)
> 완결성 Blocker: "request_human_input·interrupt·골격락 세 경로가 서로 다른 무응답 정책(§4.3 잠정 진행 / §10.7 동결·보류 / §9.4 동결)을 제시하는데, '잠정 진행'과 '동결/보류'는 정반대 행동인데 언제 어느 것이 적용되는지 결정 규칙이 없다. 더 치명적으로 §4.3은 발동 조건에 'HARD invariant 침범 후보'를 넣는데 이건 §7.5에서 '자동 진행 금지'다 — 작가 무응답이면 §4.3 잠정 진행으로 가면 안 되고 §10.7 동결로 가야 하는데 라우팅이 명문화 안 됨. interrupt 계약도 인간이 응답 무한 지연할 때 writer 권위 reacquire 타임아웃이 없어 advisory lock이 영구 human 점유될 수 있다."

**확정 — 세 경로(request_human_input·interrupt·골격락)의 무응답 정책을 단일 직교 매트릭스로 통합(§12.4 계약 동결에 추가)**. 행=위반 severity, 열=(무응답 타임아웃, 타임아웃 후 행동). **타임아웃 후 행동 ∈ {동결(frozen) | 잠정진행(provisional_advance) | HARD차단유지(hard_block)}**.

| severity | 무응답 타임아웃 | 타임아웃 후 행동 | 근거 |
|---|---|---|---|
| **HARD invariant 침범 후보** | 즉시(타임아웃 무관) | **HARD차단유지 → 영구 동결**. 무응답이어도 **절대 잠정진행 금지**(hard rule) | §7.5 자동 진행 금지 + 완결성 Blocker. HARD 보호가 무응답으로 깨지면 안 됨 |
| **critical 결정론 위반** | 짧음 | **동결**(작가 결정 전 자동 진행 차단). 단 결정론 critical은 §13.5에 따라 max_attempts 자동 재생성 후 잔존만 동결 | 결정론 ground truth 침해는 잠정진행 불가 |
| **§4.4 liveness 정체(L1/L2)** | 중간 | **동결**(`STALLED_AWAITING_AUTHOR`, 작업물·세션 durable 보존). 잠정진행 아님 | 정체는 더 찍어내면 악화되므로 동결 |
| **SOFT 복선 단절 / 지시 충돌(비HARD)** | 중간 | **잠정진행 허용**(best-so-far + defect_debt 적재 + 사후 정정 표기). 단 §4.4 L2 절대 상한·MAX_CONSECUTIVE_UNREVIEWED 적용 | 저마찰 디폴트(§4.3). 가역적이라 잠정 가능 |
| **순수 마찰(저severity)** | 길음 | **잠정진행**(auto_advance 지속) | 마찰 최소화 |
| **interrupt 중 writer 점유** | writer lease 만료(중간) | **lease 자동 회수 + 작업물 보존 + agent reacquire**(advisory lock 영구 human 점유 방지) | §10.4 시퀀스 |
| **골격락 human 무응답** | durable timeout | **재계획 보류 + 세션 PAUSED 동결**(§10.7) | 골격은 공유 가변 트리, 잠정 변경 위험 |

- **단일 규칙 요약**: *가역적·저severity = 잠정진행 / 비가역·HARD·정체·결정론critical = 동결 / HARD 침범 후보 = 영구 동결(잠정진행 절대 금지)*.
- **모든 타임아웃·행동 매핑은 §12.4 계약 동결 산출물**(추상 합의 금지, 실제 상태머신 표로 동결).

### 10.9 ⚠️ 에러 상태/저하 상태의 작가 가시성 계약 (완결성 blindSpot 신설 — 신규)
> 완결성 blindSpot: "degraded mode·컨슈머 부분실패·request_human_input 무응답 동결 등 시스템이 비정상 상태에 들어갔을 때 작가 UX에 '시스템이 지금 막혀 있다/저하 상태다'를 어떻게 알리는지가 없다. '시스템이 조용히 멈췄는데 작가는 모른다'는 §10.5 투명성 원칙의 정반대인데, 정상 흐름만 그려져 있다."

**확정 — 모든 비정상 상태는 작가에게 명시 surface(은폐 금지)**:
- 작가 UX에 **시스템 상태 배지**(`정상 / 저하(degraded) / 정체(STALLED) / 동결(frozen) / 무응답대기`)를 상시 노출.
- 트리거별 통지: degraded mode(§6.6) 진입/복구, finalize 팬아웃 컨슈머 stuck(§12.4-5), §4.4 liveness 정체, §10.8 동결, request_human_input 대기 — **각 상태 진입 시 즉시 작가 알림 + 사유 + 권장 행동**.
- "조용한 정지" 금지를 hard rule로. 동결 상태는 작가가 복귀할 때까지 durable 보존 + 복귀 시 상태 복원(§10.5).

---

## 11. 데이터 모델 & 온톨로지 (v2 계승 + v3 신규)

### 11.1 v2 계승 핵심 엔티티
| 엔티티 | 핵심 필드 |
|---|---|
| `Entity`(6종) | id, type(character/location/item/faction/rule/event), name, aliases[], structured_attributes(jsonb≤5), canon_status, confidence_tier, is_seed_anchor, **human_approved_at** |
| `EntityStateTimeline` | entity_id, attribute, value, valid_from/to(narrative_order), effective_from, evidence_chunk_id, **irreversible(bool, 범위 확장 §8.7)**, signal_grade |
| `Relationship` | source/target, relation_type, valid_from/to |
| `ChapterVersion` | versionId, narrative_order, doc, **source(agent_generated/human_edited)**, supersedes_version_id, is_active, quarantine_flag, **trust_tier(reviewed/unreviewed_machine/quarantined)**, trust_label, **writer_lock_holder, writer_lock_lease_expiry(§10.8)** |
| `RagIndexUnit≡ContentChunk` | id, chapter_version_id, narrative_order, chunk_type, text, embedding, **embedding_model_version**, signal_grade, **trust_tier, provisional(bool, §8.5)**, trust_weight, is_active, entity_refs[], alias_refs[], tsvector_ko |
| `Watermark`(3종) | last_consistent / last_indexed / state_applied_narrative_order (단조 증가 체크제약, **state_applied는 §12.4-5 모든 컨슈머 ack 후에만 전진**) |
| `EndingSpec` | ending_state, theme_resolution, victory_structure, required_payoff_thread_ids[], invariants(**HARD/SOFT/best-effort**), anchor_entity_ids[] |
| `PlotThread` | thread_id, kind, state(open/at_risk/closed/**intentionally_open**/**intentionally_incidental**), required_for_ending, payoff_deadline, registered_from_draft |
| `ProvenanceEdge` | from/to, dependency_kind(skeleton_derives/canon_derivation/replan_impact) |

### 11.2 v3 신규 엔티티
| 엔티티 | 핵심 필드 | 출처 |
|---|---|---|
| `AuthorDirective` | id, kind(redirect/edit/preference/approve/reject), issued_at_narrative_order, scope(this_scene/this_chapter/from_now/range/until_event), priority, expires_at, **is_retroactive(기본 false)**, target_invariant_layer(HARD/SOFT), conflict_status, **conflict_with_directive_id(작가 자기모순 출처 인용, §11.3)**, superseded_by | 협업 |
| `AuthorDirectiveLedger` | active/expired/conflicting ids, last_propagated_narrative_order | 협업 |
| `InterventionEvent` | point(seed/worldgen/beat/in_gen/post_gen/redirect/escalation/**liveness_stall**), human_seconds_spent, input_form, result, **decision_type(explicit_dismiss / implicit_skip)**, **conflict_origin(author_change_of_mind / system_propagation_error, §11.3)** | 협업·품질 |
| `ReviewSession`/`AuthorCognitiveState` | last_reviewed_narrative_order, unresolved_directive_ids[], **last_streamed_scene_pos**, durable_restore_key, **system_state_badge(§10.9)** | 협업 |
| `EscalationBrief` | root_cause_candidates[], impacted_narrative_range, canon_diff(출처 인용), **conflicting_past_directive_ref(§11.3)**, recommended_options[3] | 협업 |
| `DelegationLevel`/`AutonomyMode` | default_level(beats_auto/review_then_proceed/scene_by_scene), high_autonomy_mode(bool), per_chapter_overrides | 협업 |
| `CanonStagingRecord` | extracted_fact, signal_grade, conflict_status, commit_state, judge_votes, **requires_author_confirm**, **intent_or_error(§8.6-5)** | RAG·worldgen |
| `ClueSpanAnchor` | thread_id, plant_chapter_version_id, span_locator(결정론 고정), clue_hash, is_active | 스토리 구조 |
| `HierarchicalSummary` | level(chapter/arc/work), scope_range, text, embedding, **irreversible_facts(jsonb 무손실)** | RAG |
| `VerdictRecord` | gate_result, deterministic_critical_count, judge_axis_scores(**safety 축 포함**), attempt_count, **config_matrix_id** | 품질 |
| `DefectDebtEntry` | unresolved_flags[], severity, debt_kind, settle_at_act_boundary, **counts_toward_abs_cap(§4.4 L2)** | 품질 |
| `LivenessState` | work_run_id, progress_scalar, delta_window(K), stall_state(active/STALLED_AWAITING_AUTHOR/frozen), defect_debt_total, abs_cap, consecutive_unreviewed_count | 스토리구조·orchestration(§4.4) |
| `InteractionFrictionMetric` | intervention_count, total_human_minutes, directive_compliance_rate, first_pass_approval_rate, **per_chapter_decision_count(§4.2 합산)**, **active_decision_authenticity(§3.5 교차)** | 전역 |
| `RandomAuditRecord` | sampled_chapter_id, human_precision_check, undetected_false_negative_estimate, **ssot_body_desync_check**, **safety_drift_check(§9.5)**, **context_canon_cards_provided(§17.2 라벨링)** | 품질·RAG |
| `ConvergenceProgress` | ending_beat_reach_rate, payoff_rate, arc_close_rate, progress_scalar, delta | 스토리 구조 |
| `StoryBlueprint`/`Act`/`Arc`/`ChapterSkeleton`/`Beat`/`scene_plan` | 계층 트리, narrative_order, goal·key_events·start/end_state·cliffhanger·required_facts, consumed, review_mode, node_version(CAS), **scene_plan(결정론 장면 분할 §6.7)** | 스토리 구조 |
| `ReproducibilityRecord` | seed, run_index, completion_outcome, partial_rewrite_round_count, output_divergence(§16 blindSpot) | 품질(M1) |

### 11.3 ⚠️ AuthorDirective 충돌 규칙 — 단일 도메인 소유 + 작가 자기모순 회수 (아키텍처 High + 완결성 High 수용)
> 아키텍처 비평: "4개 도메인이 충돌 규칙을 미묘하게 다르게 정의 → 같은 지시가 도메인마다 다르게 전파 = 분산 일관성 붕괴."
> ⚠️ 완결성 High: "작가가 30화에 '이 조연을 죽여'라 하고 50화에 잊고 '그 조연 등장'을 지시하면 의미적 충돌이라 결정론 환원 불가. 되물음의 대상이 그 모순을 만든 작가 자신인데, 작가는 자기가 30화에 뭘 지시했는지 기억 못 할 수 있다. AuthorDirectiveLedger는 active/expired만 추적할 뿐 '과거 자기 지시와 모순되는 새 지시를 냈을 때 과거 지시 컨텍스트를 회수해 보여주는' UX 계약이 없다. 분리 라벨링은 있으나 분리 후 각각을 어떻게 다르게 처리하는지가 없다."

**확정**:
- **단일 권위 도메인**: 충돌 해소 규칙을 **스토리 구조/arc 도메인이 소유**하고 나머지는 판정 결과를 **읽기만** 한다. MVP 공통 표준 = **3줄 규칙**: ① 최신(latest) 우선 ② HARD 결정론 보호(침범 시 자동금지) ③ 모호하면 request_human_input. 소급 = 4개 도메인 모두 **future_only 디폴트 + finalized 소급 금지**. retroactive는 명시 인간 승인 시만.
- **(신규) 작가 자기모순 출처 회수 UX**: AuthorDirective 충돌 검출 시 EscalationBrief에 **"당신의 과거 지시 #N(narrative_order X): '이 조연을 죽여'와 충돌합니다"** 형태로 과거 지시 출처를 인용 노출(`conflict_with_directive_id` 필드). 작가가 자기 과거 지시를 잊었어도 컨텍스트를 회수해 보여줌.
- **(신규) 충돌 유형 분리 + 처리 분기 명세**: `conflict_origin`을 `author_change_of_mind`(작가 변심)와 `system_propagation_error`(시스템 전파 오류)로 분리 라벨링하고, 분리 후 **각각 다르게 처리**:
  - `author_change_of_mind` → **새 지시를 채택**하되 과거 지시를 `superseded_by`로 명시 만료(작가가 의식적으로 바꿨음을 확인). EscalationBrief에 과거 지시 인용 동반.
  - `system_propagation_error` → **새 지시 채택 보류 + 시스템 전파 경로 정정**(작가 의도가 아니라 시스템이 잘못 전파한 것이므로). detect_directive_conflict 경로 점검.

---

## 12. 시스템 아키텍처 & 기술 스택

### 12.1 스택 표
| 레이어 | 선택 | 근거 |
|---|---|---|
| 에이전트 루프 | **LangGraph StateGraph** + checkpointer | interrupt/스트리밍/체크포인트 1급. 단 **Temporal activity 내부 단명 실행** |
| durable 실행 | **Temporal** (v2 계승) | **크래시 복원 권위 단일.** child=회차, Signal=인터럽트, Update=동기 승인 핸드셰이크, continue-as-new. **부모 workflow=§4.4 liveness 평가** |
| 권위 SSOT | **PostgreSQL 16** + jsonb + 재귀 CTE | EntityStateTimeline·워터마크·AuthorDirective·CanonStaging·LivenessState 단일 트랜잭션 경계 |
| 벡터/검색 | **pgvector 0.7+ (HNSW halfvec)** + PG FTS | 단일 DB 통합 → 재색인 단방향 루프와 ACID 일관 |
| 한국어 FTS | **Kiwi 주력 + KSS** + alias 사용자사전 | 고유명사 회수 보강 (속도·사전등록 우위) |
| 임베딩 | **BGE-M3 온프레미스** (버전 락) | 한국어 장문 + embedding_model_version 메타로 마이그레이션 단일활성 강제 |
| 구조 검증 | **순수 Python(networkx)** | DeterministicStructureChecker, LLM 완전 배제 |
| LLM/judge | 최강 모델 + **LiteLLM** / judge 다른 family N≥3 | generator≠judge, judge 1슬롯 규칙 적대 탐지 비대칭 |
| 실시간 협업 | **WebSocket/SSE** + Redis pub/sub | 토큰 스트리밍(단방향) ↔ 인터럽트 제어채널 **분리**. **시스템 상태 배지(§10.9) 푸시** |
| 구조화 출력 | **Pydantic v2** + tool-calling | signal_grade enum 고정 |
| 관측 | **OTel + Langfuse(단일 trace_id ↔ workflow_id) + Temporal Web UI** | 루프 진동·마찰·escalation·**liveness 정체·재현성 분산** 추적 |

### 12.2 ⚠️ 체크포인터 이중화 금지 (아키텍처 비평 High 수용)
4개 도메인이 LangGraph checkpointer 권위 모델을 다르게 기술. **공통 표준 통일**:
- **크래시 복원 권위 = Temporal 단일.**
- LangGraph 그래프 = activity 내부 단명, 재시도 시 처음부터 재실행(도구 멱등 + journaling).
- **LangGraph Postgres checkpointer를 durable 권위로 쓰는 것 금지**(activity 수명 내로만).
- 인간 컨텍스트(검토 커서·AuthorDirective·PAUSED 위치·LivenessState)는 LLM 출력과 분리해 PG 결정론 영속화.
- interrupt 전파는 **얇은 어댑터로 격리**(2026 LangGraph×Temporal 통합 실험단계 리스크 봉인).

### 12.3 임베딩 마이그레이션 가시성 (아키텍처 비평 blindSpot)
100~200화 = 수개월 → 임베딩 버전업 거의 확실. is_active_embedding 단일활성 + **부분 재임베딩 동안 ContextBuilder는 활성 버전 셋만 가시(워터마크 가시성 게이트 확장)**. 작품단위 재임베딩 워크플로(continue-as-new).

### 12.4 계약 명세표 — 코딩 전 동결 (아키텍처 Blocker + 완결성 Med)
> R13: 도메인 경계 권위 충돌, 미동결 시 통합공수 1.5~2배.

코딩 착수 전 **실제 산출물로** 동결(추상 합의 금지):
1. **writer_lock 물리적 단일 소유자 = orchestration 도메인 1곳** 확정(협업/memory/arc가 각자 소유 주장하던 것 해소). 나머지는 위임. **writer lease 만료(§10.8) 포함**.
2. **directive_canon_reconcile staged 제안 4-홉 경로**(협업→memory→worldgen→selfcorrect)를 **시퀀스 다이어그램 + 각 홉 멱등키·순서보장·부분실패 보상 트랜잭션**으로 명세.
3. **arc의 SOFT 갱신 '요청자' vs HARD 보존 '검증자' 자기참조 권위 분리**(같은 도메인 내 별도 컴포넌트로 명시).
4. arc→draft beat_snapshot 단방향, draft→arc registered_from_draft 회수, arc↔selfcorrect 게이트 권위=selfcorrect.
5. **⚠️ finalize→메모리 반영 팬아웃 계약(완결성 Med 신설)**: §7.2 [7]의 3중 컨슈머(RAG 색인 / 온톨로지 상태 반영 / 계층 요약 갱신)를 **시퀀스로 명세** — (a) 각 컨슈머의 **멱등키**, (b) 부분 실패 시 **보상 트랜잭션**(예: RAG 색인 성공·온톨로지 반영 실패 시 롤백/재시도), (c) **워터마크 3종 '함께 전진' 보장** = `state_applied`는 **모든 컨슈머 ack 후에만 전진**(단조성만으로는 split-brain·영구 desync 못 막음, §8.4), (d) **컨슈머 stuck 시 후속 회차 진행 차단 여부** = stuck이면 `last_indexed` 정체 → 다음 회차 `as_of`가 못 전진 → 후속 회차 차단 + §10.9 작가 통지.
6. **⚠️ §10.8 무응답 종료조건 직교 매트릭스**: severity별 (타임아웃, 타임아웃 후 행동={동결|잠정진행|HARD차단}) 상태머신 표를 실제 산출물로 동결.

---

## 13. 회차 일관성·품질 전략 (인간 협업 전제로 우경량화)

### 13.1 3층 신뢰 구조
| 등급 | 도구 | 자동확정 | 비발산 |
|---|---|---|---|
| **결정론(1)** | consistency_check, prose_lint | 허용 | **코드 레벨 보장** |
| **준결정론(2)** | displayed_power_tier 누적 | **금지**, K회차 누적 surface | — |
| **의미** | critique judge | **금지**, 작가 surfacing | — |

> ⚠️ **결정론 외피 차단**: 입력에 LLM 산출물 1바이트라도 섞이면 등급2. "결정론 검증 코어"와 "준결정론 감시"를 signal_grade로 코드 레벨 엄격 분리. consistency_check/prose_lint는 LLM 완전 배제.

### 13.2 ⚠️ r(회차) 검토/미검토 분리 + auto_advance 사각지대 (Blocker·시나리오4)
> "인간 조기교정"은 작가가 그 회차를 봤을 때만 작동. auto_advance면 안 봄 → 거짓 안심.

- r(회차) 곡선을 **'검토된 회차' vs '미검토(auto_advance) 회차' 분리 측정**. 후자의 사각지대 모순 누적률을 **별도 1급 지표**(단 측정 도구 한계는 §17.2에서 정직 표기).
- 미검토 회차 누적 방어는 **결정론 4종 + ClueSpanAnchor + 막경계 무작위 감사 + trust 격리(§8.5) + 연속 미검토 상한**(의미 모순은 못 잡음 인정).
- **막경계 무작위 감사 주기 정량 고정**: 매 막 경계 + 매 N회차 강제 샘플. **안전 축 포함(§9.5)**.
- **명제 범위 축소 명문화**: "결정론 일관성 = 비발산 보장 / 의미 일관성 = 막경계 감사로 탐지(보장 아님)". "인간 조기교정 효과"를 successMetric 긍정 지표로 쓰는 것 **금지** → "검토된 회차에서만 측정되는 조건부 효과"로 강등.

### 13.3 ⚠️ 학습 레저 over-suppression — "안 봄"≠"괜찮다" (시나리오4 핵심)
- author_judgment_ledger 학습 신호를 **`explicit_dismiss`(작가가 플래그 열어보고 닫음)와 `implicit_skip`(auto_advance로 안 봄) 엄격 분리** — **skip은 절대 suppression 학습 입력이 안 됨.**
- 학습은 **노출 우선순위만** 낮추고 **탐지는 절대 안 끔**(hard rule). consistency critical은 학습 제외.
- 막경계 강제 종합 surface에서 하향 유형 1회 재노출 + **'auto_advance로 한 번도 검토 안 된 회차 구간' 명시 표기**(작가가 spot-check 대상 선택).
- 미검토 비율 임계 초과 시 '검토 부채' 경고 + 일부 회차 강제 requires_review 승격. **안전 게이트 발동·연속 미검토 상한 초과 시에도 requires_review 승격(§9.5·§8.5 재사용).**

### 13.4 ⚠️ 외부 타당성 닻 — 작가 본인 승인의 한계 (Blocker)
> §0.3 비대칭(judge는 의심하면서 인간 닻은 무비판 채택)의 재발. 작가는 자기 세계 트로프에 동조하므로 judge 상관 맹점(시나리오4)을 교정 못 함. 작가 승인을 ground truth로 쓰면 judge recall이 자기참조.

**확정**: 익명 패널 Krippendorff α≥0.7 calibration을 **4개 도메인 공통 단일 MVP 선결 측정**으로 통합(도메인마다 재발명 금지). 목적 = ① judge recall·임계 고정 ② 작가 승인의 외부 타당성 보정. 작가 승인을 ground truth로 쓰는 모든 successMetric에 **ρ_max ≈ √α 정규화** 적용(작가 편향 vs 시스템 결함 분리). **테마·정서·트로프 동조는 작가 승인으로 외부 타당성 부여 불가**를 명문 인정. 작가 거부/수정/변심도 InterventionEvent로 로깅(무비판 ground truth 채택 금지).

### 13.5 게이트 판정 재구성
v2 pass/regenerate/accept-with-debt → **pass / regenerate / surface-to-author**. severity 차등:
- 결정론 critical = **조용히 자동 재생성**(작가 무방해) → max_attempts 후 잔존만 surface(§10.8 동결)
- 준결정론 = K회차 누적 시만 surface
- 의미 저신뢰/judge 불합치 = **즉시 surface**(작가 결정 라우팅)
- gray zone = KILL 아님, **작가 결정 라우팅**(무응답 종료조건=§10.8)

---

## 14. 핵심 의사결정 로그

| # | 결정 | 근거 / 비평 반영 |
|---|---|---|
| D1 | 2계층(Temporal child × LangGraph activity-단명) | 분산 내구성 + 루프 의미론 결합, 체크포인터 이중화 회피 |
| D2 | ContextBuilder = 결정론 파이프라인(LLM 2곳만 격리) | "에이전트" 명명이 잘못된 자유도 유도 → 시점누수·required_facts 누락 차단 |
| D3 | 토큰 인터럽트 → **장면 경계 인터럽트** | 기술 환상 폐기(Blocker), 정직한 우경량화 |
| D4 | 실시간 인터럽트 **M2 연기**, MVP는 직후 개입 | 동시성 난이도 한 자릿수 배 차이(시나리오1) |
| D5 | 인간 수정분 캐논 = "재확인 후 무결성", semantic 자동확정 금지 | 자동 무결성 100% 원리적 불가(Blocker) |
| D6 | 본문 = 비관적 단일 writer + CAS, **CRDT 본문 미채택** | CRDT는 캐논 옳고 그름 판정 못 함 |
| D7 | judge = **우선순위 산출기**(단독 KILL 권위 폐기) | judge 트로프 동조 상관 맹점 |
| D8 | 작가 승인 + **익명 1회 객관 닻 잔존** | 외부 타당성 닻 제거 위험(Blocker, §13.4) |
| D9 | auto_advance 디폴트(arc) | 효율 명제. **단 §3.5 정체성 최소선 위에서 + §4.4 liveness 상한 안에서** |
| D10 | r(회차) 검토/미검토 분리, 명제 범위 축소 | 거짓 안심 차단 |
| D11 | implicit_skip ≠ suppression 학습 입력 | over-suppression 역설 방어(시나리오4) |
| D12 | AuthorDirective 3줄 규칙, 충돌 단일 도메인 소유 | 분산 일관성 붕괴 방지(High) |
| D13 | HARD 변경 = **work_run fork만** | HARD/SOFT 경계 무력화 방지 |
| D14 | convergence_probe = 측정/제안만(강제전이·KILL 금지) | Goodhart 차단. **단 동일 지표를 §4.4 L1 강제 작가 surface로 사용**(강제전이≠강제호출) |
| D15 | 비가역 상태전이류 자유본문 편집 비활성, 구조화 토글만 | 결정론 오염(시나리오3) |
| D16 | 저작권 P0 게이트 + 정체성 최소선 가설 신설 | 기획 비평 치명적 2종 |
| D17 | 콘텐츠 안전 게이트 P0 신설 + auto_advance 책임 분리 | 아키텍처 blindSpot + 완결성 Med |
| D18 | 회차당 의사결정 총량 하드캡 + 배치 이연 | 인지 부하 합산 폭발(높음) |
| D19 | 회차 간 seam 책임 도메인=selfcorrect 명시 | 실패추적 — 회차 간 seam 책임 공백 |
| D20 | §12.4 계약 코딩 전 실제 스키마 동결(+ finalize 팬아웃 + §10.8 매트릭스) | R13 통합공수 1.5~2배 + 완결성 Med |
| D21 | 타깃 페르소나 A/B 분리, M1 거부감 측정 | 기획 비평 blindSpot |
| **D22** | **디폴트 모드 liveness 계약 신설(§4.4): progress-monotone·defect_debt 절대 상한·데드락 상태 모델 → KILL 아닌 강제 작가 surface** | **완결성 Blocker — 유인 모드 종료·비교착 보장 공백** |
| **D23** | **무응답 단일 종료조건 직교 매트릭스(§10.8): severity별 (타임아웃, 동결/잠정진행/HARD차단). HARD 침범 후보=영구 동결, 잠정진행 절대 금지** | **완결성 Blocker — 모드별 무응답 정책 모순** |
| **D24** | **미검토 회차 RAG 오염 격리(§8.5): trust_tier='unreviewed_machine' + provisional + 연속 상한 + 모순 시 quarantine 강등** | **완결성 High — 미검토 생성→RAG 의미 오염 무방비** |
| **D25** | **세 최소선 비양립 시 §16.0 분기 의사결정 트리(제품 형태 fallback) 코딩 전 결론** | **완결성 High — P0 경계가 미검증 전제 위** |
| **D26** | **작가 자기모순 회수 UX(§11.3): conflict_with_directive_id 과거 지시 인용 + conflict_origin 분리 처리** | **완결성 High — 작가 human error 방어 부재** |
| **D27** | **장면 경계 결정론 정의(§6.7, scene_plan) + 에러 상태 작가 가시성(§10.9) + 재현성 분산 측정(§16)** | **완결성 blindSpot 3종** |

---

## 15. 리스크 & 완화책

### 15.1 Blocker
| 리스크 | 완화 |
|---|---|
| **효율(auto_advance) vs 일관성(인간 조기교정) 구조 모순** — 같은 변수 역방향, 작동점 미증명 | 명제 범위 축소(§13.2) + 막경계 감사 주기 고정 + 정체성 최소선(§3.5). MVP는 a·b 동시 주장 금지. **완화책 자체가 미검증 → §16.0 비양립 fallback** |
| **디폴트 모드 liveness 공백** — auto_advance에서 '작가 부재 + 정체 + defect_debt 무한 적재 + 엔딩 비수렴' 데드락 | **§4.4 liveness 계약**: progress-monotone→강제 작가 surface(L1) + defect_debt 절대 상한(L2) + STALLED 데드락 상태 모델·종료조건(L3). convergence_probe 트리거 재배선(강제호출, 강제전이 아님) |
| **무응답 종료조건 모순** — 잠정진행 vs 동결 vs HARD차단이 모드별 모순, 단일 답 없음 | **§10.8 직교 매트릭스**: severity별 (타임아웃, 행동) 단일화. HARD 침범 후보=영구 동결(잠정진행 절대 금지). writer lease 만료(§10.4). §12.4-6 동결 |
| **결정론 오염** — 작가 편집 recall<1이 ontology ground truth 오염, 자기교정 불가 | 비가역류 구조화 토글만(D15) + 능동 재입력 확인 + 양방향 막경계 감사 + suspect flag + **의도-실수 양자택일 게이트(§8.6-5)** |
| **미검토 회차 RAG 의미 오염** — auto_advance 회차가 정상 finalize라 trust 정상, 수십 회차 전파 후에야 막경계 감사 탐지 | **trust_tier='unreviewed_machine' + provisional + 연속 미검토 상한 + 모순 시 quarantine 강등(§8.5/§8.6)** |
| **외부 타당성 닻 제거** — 작가 승인 자기참조 | 익명 1회 객관 닻 통합(§13.4) + ρ_max 정규화 |
| **§12.4 계약 미동결** — writer_lock 3도메인 동시 소유 = split-brain | 코딩 전 단일 소유자 확정 + 4-홉 시퀀스 + finalize 팬아웃 + §10.8 매트릭스 명세(§12.4) |
| **저작권/저작물성** — 작가 저작자 인정 불가 시 연재 불가 | 변호사 자문 P0 게이트(§3.6, auto_advance 책임 귀속 포함), NO면 §16.0 제품 형태 재설계 |
| **세 최소선 비양립** — 정체성·저작물성·인지부하 하드캡 동시 만족 불가 시 가치 삼각형 붕괴 | **§16.0 M1 GO 게이트 분기 의사결정 트리**(양립→값 고정 / 비양립→제품 형태 재설계: 타깃 A 축소·auto_advance 디폴트 강등 또는 소유감 비목표 포기). 코딩 전 결론 |

### 15.2 High
| 리스크 | 완화 |
|---|---|
| RePlanner 폭주(골격 진동) | thrash_count 상한 + dryrun→작가 채택 강제 + MVP local만 |
| 엔딩 표류(SOFT 누적 HARD 침식) | deterministic_structure_check 매 재계획 HARD 검증 + HARD=fork만 |
| 복선 미회수/false payoff | ClueSpanAnchor 결정론 + payoff_check + **SOFT 복선 단절도 request_human_input 라우팅**(시나리오2) |
| 동시성 split-brain | writer_lock 단일 소스 + append-only + 골격 range freeze + lease 만료 |
| 협업 마찰 과다(v1 회귀) | auto_advance 디폴트 + 회차당 의사결정 하드캡 + 마찰 1급 지표 |
| 학습 over-suppression | implicit_skip 분리 + 탐지 안 끔 + 막경계 재노출 |
| **작가 human error / 모순 directive 누적** — 되묻는 대상이 모순 만든 작가 본인, 과거 지시 망각 | **§11.3 과거 지시 출처 회수(conflict_with_directive_id) + 의도-실수 양자택일 + conflict_origin 분리 처리** |
| LangGraph×Temporal 통합 실험단계 | 얇은 어댑터 격리 + chaos 테스트 + +1주 버퍼 |
| 자기섭취 단조하락 | trust_weight 하향 + 동결 시드 anchor + 위력 RAG 재참조 금지 |
| 회차당 LLM 호출/지연 폭증 | judge 회차당 상수화(§6.5) + 호출 수 M1 실측 + degraded mode |
| **에이전트 루프 비결정성** — partial_rewrite temperature>0 분산이 크면 best-so-far monotone 판정 불안정 | **재현성 분산 측정(§16, 같은 시드 n≥10 재실행 완주율·재집필 라운드 분산)** + monotone 판정 안정성 검증 |

### 15.3 Med / blindSpot
| 리스크 | 완화 |
|---|---|
| 진위 부트스트랩 모순 | 세계규칙 상호 충돌 매트릭스 전수 제시(P0) + M1 조기발견율 실측 |
| 콘텐츠 안전 누적 드리프트 | 안전 게이트 P0(장면+막경계 누적) + auto_advance 책임 분리(§9.5) |
| durable 의미적 복원 손실 | 작가 본 순간=커밋 경계 + 투명 고지 + 재확인 게이트(§10.5) |
| 임베딩 마이그레이션 이종 벡터 혼재 | is_active_embedding + 가시성 게이트(§12.3) |
| 다중 세션 human-human 충돌 | advisory lock session_id 차원(§10.6) |
| **finalize 팬아웃 부분 실패** — 컨슈머별 desync, 워터마크 갈라짐 | **§12.4-5 팬아웃 계약**(멱등키·보상 트랜잭션·함께 전진·stuck 시 후속 차단) |
| **에러/저하 상태 작가 미인지** — 조용한 정지 | **§10.9 시스템 상태 배지 + 진입 즉시 알림(은폐 금지 hard rule)** |
| **장면 경계 미정의** — LLM 동적 판정이면 비결정 자유도 | **§6.7 결정론 scene_plan 분할 + 크기 상·하한 M1 임계** |
| DelegationLevel 전환 소급 공백 | 위임 상향 후 미검토 사각지대를 막경계 정산으로 회수 |
| **장르 외부 타당성 한계** — 단일 타임라인 = 비발산 best case | **§5.1 successMetric 일반화 불가 명시 고지**(회귀물 등 미평가) |
| **M1 통계적 검정력** — N 한 자릿수면 판정 노이즈 | **§16 의사결정 가능 최소 N 사전 정의 + 부족 시 정량→정성 강등 규칙 동결** |
| 타임스탬프 선후 규약 소실 | M1 임계(recall·마찰·콜드스타트·**K·ABS_CAP·장면 크기·최소 N**)를 **데이터 보기 전 동결** 절차 재도입 |

### 15.4 ⚠️ 회차 간 seam — 책임 도메인 공백 (실패추적 — 명시)
> 회차 **내** seam(partial_rewrite 재봉합)은 챙기지만 회차 **간** seam(톤·골격·복선 전제·human_edited 회차→다음 기계 회차)은 도메인 간 책임 공백.

**확정**: 회차 간 seam 검증 **명시적 소유 도메인 = selfcorrect**. (1) human_edited 회차와 후속 기계 회차의 pairwise voice distance를 drift 알람에 포함. (2) registered_from_draft를 '추출기 방출 0건 누락'이 아니라 **'본문 명명 엔티티/사물 중 온톨로지·thread 어디에도 없는 것 = 미분류 후보'의 차집합 검사**(추출 재현율 비의존, 실패추적 시나리오6)로 확장. (3) `intentionally_incidental` 상태로 작가가 '무시' 결정한 디테일은 false payoff 후보 영구 제외. (4) style_anchor를 **동결 시드 + 작가 명시 승인 톤 샘플(human_edited 핀)** 2채널로 분리(자기섭취 안전 유지하며 작가 선호 반영 — 실패추적 시나리오7).

---

## 16. 마일스톤 로드맵

```
M1 (선결 스파이크 + GO 게이트) ──► M2 ──► M3 ──► M4 ──► M5
   §16.0 분기 의사결정 트리        단일회차   연속회차   협업       풀아크
                                  코파일럿   RAG루프   worldgen   진행
```

### 16.0 ⚠️ M1 GO 게이트 — 분기 의사결정 트리 (완결성 High 신설 — 코딩 착수 전 결론 의무)
> 완결성 High: "MVP P0 기능 전부가 '세 최소선(정체성·저작물성·인지부하 하드캡)이 우연히 양립 가능'이라는 미검증 전제 위에 서 있다. PRD는 '세 숫자가 수렴하는지 M1 확인'이라고만 하고 비수렴 시 fallback 제품 형태를 정의하지 않았다. '비양립 시 무엇을 포기할지'를 지금 명문화해야 P0 경계가 확정된다."

**M1에서 (정체성 최소선 §3.5, 저작물성 최소선 §3.6, 인지 부하 하드캡 §4.2) 세 값을 측정한 뒤, 코딩 착수 전 다음 트리로 분기**:

```
세 최소선 측정
   │
   ├─ (a) 양립(세 값이 동시 만족 가능한 개입 수준 존재)
   │       → 그 값으로 디폴트 개입 수준 고정 → P0 경계 확정 → M2 착수
   │
   └─ (b) 비양립(동시 만족 불가)
           → 제품 형태 재설계 (둘 중 택일, 코딩 전 결론):
              ① 타깃을 페르소나 A(마감 압박형, §5.3)로 좁히고
                 auto_advance를 디폴트에서 강등 (개입 수준을 저작물성·정체성 최소선에 맞춤)
              ② '소유감'을 비목표로 명시 포기 (효율 단독 제품으로 재정의,
                 §3.5 정체성 최소선 가설 폐기 + §1.2 가치 삼각형 축소)
           → 어느 쪽도 코딩 착수 전 결론. P0 경계는 이 결론으로 확정
```

**추가 GO 게이트 산출물(코딩 전 명문화 의무, 완결성 Blocker/High 4종)**:
1. **§4.4 디폴트 모드 liveness 계약** — L1/L2/L3 + K·ABS_CAP 동결값.
2. **§10.8 무응답 단일 종료조건 직교 매트릭스** — severity별 (타임아웃, 행동) 상태머신.
3. **§8.5 미검토 회차 RAG 오염 격리** — trust_tier·provisional·연속 상한·강등 경로.
4. **§16.0 세 최소선 분기 결론** — (a) 고정값 또는 (b) 재설계 택일.

### M1 — 선결 스파이크 (MVP GO/NO-GO 게이트)
> 코딩 전 측정. 이 측정 없이 효율·무결성 명제 단정 불가.
- **협력 작가 N명 조달**(기획 비평 blindSpot): 채널(공모전 입상자·플랫폼 제휴·지인)·인센티브·기존 원고 사용 동의 양식 — **별도 선결 과제**. **⚠️ 통계적 검정력**: 정체성 최소선·거부감·judge recall을 의사결정 가능한 신뢰구간으로 추정할 **최소 N과 페르소나 셀당 표본 수를 사전 정의**. N 부족 시 그 측정을 "정량 판정"이 아니라 "정성 신호"로 강등하는 규칙을 데이터 보기 전 동결.
- 인간 수정분 캐논 역추출 recall 실측(비가역 상태전이류 한정)
- 세계관 승인 인간-시간 baseline / 협업 마찰 baseline / **회차당 의사결정 총량 + 형식 통과율(§3.5 능동 결정 진정성 교차)**
- **회차당 LLM 호출 수·토큰 실측**(judge 3 + 자가교정 루프 + RAG 끝까지 1회전)
- **에이전트 루프 재현성 분산 측정**(완결성 blindSpot 수용): 같은 시드 **n≥10 재실행** → 완주율·partial_rewrite 라운드 수·출력 발산 분산이 best-so-far monotone 판정을 불안정하게 만들 수준인지 검증
- **장면 경계 크기 임계 측정**(§6.7): 장면 길이 상·하한 트레이드오프(인터럽트 반응성 p95 vs judge 비용 모델) + 회차당 장면 수 상한
- **정체성 최소선 가설 검증**(자동진행 vs 개입 회차 블라인드 "이게 당신 작품 같은가")
- **타깃 페르소나 정서적 거부감 측정**(A로 좁힐지)
- **저작권 변호사 자문**(저작물성 최소선 + auto_advance 책임 귀속)
- 익명 패널 α≥0.7 1회 객관 닻 / 부트스트랩 모순 조기발견율
- **M1 임계 전부 데이터 보기 전 동결**(타임스탬프 규약): recall·마찰·콜드스타트·K(§4.4)·ABS_CAP·장면 크기·최소 N·강등 규칙.

### M2 — 단일 회차 코파일럿 루프
LangGraph×Temporal 2계층, 도구셋, scene_plan 결정론 분할(§6.7), 자가교정 미니루프, 회차 직후 review surface, EscalationBrief, 결정론 코어 + critique + prose_lint + 안전 게이트, **§10.8 무응답 매트릭스 + §10.9 에러 가시성**, 동시성 안전 골격(직후 개입). **단막 20~30화 1개로 1차 검증.**

### M3 — 연속 회차 RAG 루프
RagIndexUnit 색인 + 하이브리드 회수 + ContextBuilder(고정 상태그래프) + 망각 게이트 + 단방향 재색인 + 불변식 강제 + 워터마크 + **finalize 팬아웃 계약(§12.4-5) + 미검토 회차 trust 격리(§8.5)**. r(회차) 검토/미검토 분리 측정 개시(코드 완성과 별개로 30~50화 누적 후 비발산 실증).

### M4 — 협업 worldgen
시드 인테이크 + 부트스트랩 생성기 + propose-adjust-approve 루프 + 상호충돌 매트릭스 + EntityStateTimeline + EndingSpec 3계층 + AuthorDirective validator + blast_radius_preview + 구조화 편집 표면 + **작가 자기모순 회수 UX(§11.3)**.

### M5 — 풀 아크 진행
StoryBlueprint 점진 생성 + ChapterSkeletonConsumeLoop + DeterministicStructureChecker + payoff_check/ClueSpanAnchor + convergence_probe + **§4.4 liveness L1/L2/L3 평가** + registered_from_draft 계약 + local 재계획. 100~200화 풀 길이 r(회차) 곡선 검증.

### 후속 (M6+)
실시간 장면 경계 인터럽트, RePlanner major, AuthorDirective 풀 엔진, 자유본문 자동 캐논 역추출(recall 충족 후), 적응형 정책, 선택적 고자율 모드(72h hard KILL 포함), N편 병렬, 회귀물 등 장르 확장(timeline_branch_id).

---

## 17. 성공 지표 (수익성 제외)

### 17.1 협업 효율 (명제 b)
| 지표 | 정의 | 주의 |
|---|---|---|
| 작품당 인간 메타개입 시간 | 시드→완결 인간-분 | **'증명' 아닌 '계측·관찰'.** A/B 통제 불가 → **약한 baseline**(같은 작가 과거 작품 페이스) |
| 회차당 의사결정 총량 | 도메인 합산(§4.2) | 하드캡 준수 + 형식통과율 |
| 개입 마찰 | 1건당 클릭/시간/재지시 | 추세 하락 = 학습 작동 |
| 1차 통과율 | 수정 없이 승인 비율 | — |

### 17.2 일관성 (명제 a, 범위 축소)
| 지표 | 위상 |
|---|---|
| stale=0 / lookup누락=0 / 멱등 중복=0 / **워터마크 함께 전진**(§12.4-5) | **불변식(DB 강제), 측정 아님** |
| 결정론 critical 잔존율 | 0 |
| r(회차) 검토/미검토 분리 | **진단지표**(게이트 아님) |
| 미검토 회차 사각지대 모순 추정 | ⚠️ **측정 도구 한계 정직 표기**(아래) |
| 복선 회수율 / HARD 보존율 | 100% 목표 |
| 지시 충돌·HARD 침범 안전처리율 | 100% 검출 + 안전 라우팅 |
| **§4.4 liveness 정체 발생률·강제 surface 후 종료율** | 진단(데드락 종료조건 작동 확인) |

> ⚠️ **'미검토 회차 사각지대 모순 추정'의 측정 도구 한계(완결성 Med 수용)**: §8.1 천장 (c)(r(회차) ground-truth 모순 라벨 부재) + judge recall 자기참조(§13.4) + 익명 패널의 작품 전체 맥락 무지(30화 전 복선 모름) + 작가 미검토로 인해, 이 지표를 측정할 **신뢰 가능한 라벨러가 사실상 없다.**
> - **(부분 보정)** 막경계 감사 시 감사자에게 **그 막의 핵심 캐논/복선 카드(ontology 결정론분 + ClueSpanAnchor)를 컨텍스트로 제공**해 '맥락 무지' 천장을 부분 보정(`RandomAuditRecord.context_canon_cards_provided`).
> - **(그래도 측정 불가 시 강등)** 부분 보정으로도 신뢰 라벨이 안 나오면 **이 지표를 1급에서 '정성 관찰'로 강등하고 정직하게 표기**(자기참조 1급 지표를 게이트로 쓰지 않음).

### 17.3 품질 (외부 타당성 닻 동반)
| 지표 | 주의 |
|---|---|
| judge 모순 탐지 recall | **익명 1회 객관 닻으로만 측정**(작가 승인 ground truth 금지, §13.4) |
| 발견 오확정율 + 미발견 FN 추정 | 둘 다 1급 |
| 문장 품질(종결어미·n-gram 임계) | 결정론 |
| 콘텐츠 안전 차단율 + **막경계 누적 드리프트**(§9.5) | 안전 게이트(장면+막경계) |

### 17.4 작가 만족·정체성 (기획 비평 수용 — 1급)
| 지표 | 정의 |
|---|---|
| **소유감("내 작품 같은가")** | M1 가설 + 운영 측정. **⚠️ 운영 정량 측정 정의(완결성 Med 수용)**: 행동 프록시 = (작가 능동 결정 빈도 · 수정 비율 · 거부율 · §3.5 능동 결정 진정성). **Goodhart 방지를 위해 게이트가 아니라 모니터로만.** 설문은 보조 |
| 주도권·신뢰 인식 | "내가 통제하고 있다" 비율, 자율도 위임 점진 확대 |
| 정서적 거부감 | 페르소나 분리(§5.3) |
| **⚠️ Goodhart 2차 경고(효율·정체성 양면)** | (1) 마찰 지표 자체를 최적화 목표로 삼아 surfacing 과도 억제(조향력 상실) 금지 — 마찰↓와 조향력↑ 동시 모니터. (2) **소유감 지표 게이밍** — 작가가 무의미 토글로 능동 결정 카운트만 채우는 경로(§3.5)를 형식 통과율 교차로 탐지, 소유감 지표에서 제외 |

---

## 18. 열린 질문

### 18.1 비목표 (명시적 제외)
- 수익성·단위경제·WTP·손익분기·구독가 (단 토큰 비용은 §7.4 기술 제약)
- 회귀/시간역행/변신/멀티 타임라인 (timeline_branch_id 미도입). **⚠️ 단 이는 §5.1 외부 타당성 한계로도 표기 — 현재 검증이 가장 쉬운 best case**
- 완전 무인 L0 완주 보장 (선택적 고자율 모드 부차 실험만)
- v1형 백지 집필 에디터
- 서사 재미·테마 정서 자동 정량 측정 (측정 불가 인정, 작가 판단 부분 대체)
- 상업 완독·시장성 보장

### 18.2 미해결 질문 (운영 데이터 필요)
1. **request_human_input 최적점** — 작가·작품·장르별 상이, MVP는 보수적 고정 디폴트 + 수동 슬라이더, 적응형 P2
2. **정체성 최소선의 실제 값** — 회차당 1결정이 맞나? 페르소나별 다른가? (M1)
3. **저작물성 최소선 = 정체성 최소선 수렴 여부** (M1 + 변호사). **비수렴 시 §16.0 분기 트리**
4. **타깃 페르소나 확정** — A로 좁힐지, A/B 분기 제품 필요한지 (M1)
5. **AuthorDirective 의미적 충돌**(각성 vs 좌절)은 결정론 환원 불가 → 인간 라우팅 빈도가 효율 잠식. 작가 변심이 충돌 주원인. **§4.4 L1과 결합해 '작가 부재 + 정체' 데드락으로 모델링·종료조건 정의**
6. **HARD/SOFT 경계 동적 흔들림** — 누적 redirect로 fork 빈도, 긴 세션 중반 fork 시 RAG/온톨로지 시드 이관 비용·무결성 (미설계)
7. **암묵적 캐논 회수 천장** — 엔티티 그래프 P2→P1 승격 트리거 발동 가능성 높음
8. **온보딩/콜드스타트 작가**(기획 비평 blindSpot) — 첫 작품 작가가 worldgen 캔버스 압도감, 학습 곡선 = 이탈 1순위, 미분석
9. **경쟁 대체재**(기획 비평 blindSpot) — "그냥 ChatGPT에 회차별 프롬프트" 우회 대비 RAG 연속성·일관성 코어의 실제 우위 미측정
10. **실패의 정서적 비용**(기획 비평 blindSpot) — 30화 후 "처음부터 틀렸다" 시 work_run fork로 기술 처리되나 "승인한 30화가 헛것" 의욕 상실 = 이탈 실제 트리거
11. **어뷰징/오남용**(기획 비평 blindSpot) — 타 작가 문체 도용, 히트작 설정 복제 변형, 표절 세탁. 표절 근접도 게이트(P2) 외 0개
12. **편집자/PD/공동작가 3인 협업** — 단일 writer 계약과 충돌? (인수인계 P2만)
13. **회차당 의사결정 하드캡 N값** — 인지 부하 임계 (M1). **세 최소선과의 양립 §16.0**
14. **장르 확장 외부 타당성** — 단일 타임라인 MVP 성공이 회귀물 등으로 일반화되는가 (§5.1, 미평가)

---

> **부록 참조**: v2 PRD는 `docs/archive/PRD-v2-autonomous.md`, v1은 `docs/archive/PRD-v1-human-in-loop.md`. 회의록·원자료는 `docs/appendix/`.
