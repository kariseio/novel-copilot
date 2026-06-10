# PRD v3 (최종본) — 에이전틱 웹소설 코파일럿
## "기계가 회차를 써내려가고, 작가가 옆에서 빨간 펜을 든다" — 회차 단위 RAG 생성 + Human-in-the-Loop 코파일럿

> **문서 상태:** 개발 착수 **조건부 가능 (M1 GATE 조건부)**. §16의 M1 선결 스파이크를 MVP GO 게이트로 둔다. 완결성 점검이 지적한 **4개 Blocker/High** — ① 디폴트(유인) 모드 liveness 계약(§4.4), ② 무응답 단일 종료조건 직교 매트릭스(§10.8), ③ 미검토 회차 RAG 오염 격리(§8.5/§8.6), ④ 세 최소선(정체성·저작물성·인지부하) 비양립 시 분기 의사결정(§16.0) — 을 **M1 GO 게이트 산출물로 실제 스키마/상태머신으로 동결하기 전에는 코딩 착수를 보류**한다.
> **작성:** 리드 PM 겸 에이전트 시스템 테크리드
> **전제:** 수익성·단위경제·WTP·구독가 비고려(§18 비목표). 단 **회차당 LLM 호출 수·토큰은 "수익성"이 아니라 "기술 설계 제약(지연·레이트리밋·장애전파)"으로 1급 취급**한다.
> **핵심 정체성:** 이 제품은 **"무인 완주 보장 머신"이 아니라, 회차 루프 안에서 기계가 집필하고 작가가 능동 조향·수정하는 비대칭 코파일럿(asymmetric copilot)**이다.
> **이 문서의 성격:** 본 PRD는 4라운드 비평(기획비평 · 아키텍처비평 · 협업/에이전트 루프 실패추적 8건 · 완결성 점검 8건 + 블라인드스팟)을 본문 곳곳에 인용·반영해, "좋은 약속"이 아니라 "깨지는 지점과 그 방어"를 명시한다. 비평이 노출한 구조적 모순(효율 vs 일관성, 자동진행 vs 정체성, 결정론 오염, seam 책임 공백, 디폴트 모드 liveness 공백, 무응답 종료조건 모순, 미검토 RAG 오염, 세 최소선 비양립)은 회피하지 않고 §3·§4.4·§8·§10.8·§13·§15·§16·§18에 그대로 드러낸다.

---

## 0. 비평 → 반영 위치 매핑 (감사 추적)

PRD를 읽기 전에, 4개 비평 묶음이 어디서 닫혔는지 한눈에 본다. "닫음"은 (a) 본문 계약화, (b) M1 측정 위임, (c) 명시적 잔존 리스크 인수 중 하나다.

### 0.1 기획비평(planCritique) → 반영

| # | 기획비평 지적 | 반영 위치 | 처리 방식 |
|---|---|---|---|
| P1 | "기계가 쓰고 인간이 조향"이 v1(인간이 쓰고 기계 교정)으로 회귀할 위험 — 누가 진짜 저자인가 | §2, §3.5, §13.6 | 정체성 최소선(회차당 ≥1 능동 결정) 계약화. 산문 백지 타이핑 금지·조향/큐레이션/재지시로 한정 |
| P2 | 효율(저마찰)과 일관성(검증)이 정면 충돌 | §4.2, §13.1 | "인지부하 vs 검증" 트레이드오프를 1급 지표로. critique 회차당 상수화(§13.3)로 균형 |
| P3 | auto_advance 디폴트가 정체성/저작물성과 모순 | §3.5, §3.6, §4.4, §16.0 | 정체성 최소선 + §4.4 liveness 강제 surface + 세 최소선 비양립 분기 결론 M1 조건부 |
| P4 | 작가 자기모순 지시(30화 "죽여" vs 50화 "등장") | §4.5, §11.3, §10.7 | conflict_with_directive_id 과거 지시 출처 회수 + conflict_origin 분리 라우팅 |
| P5 | 협업 worldgen에서 SSOT 상호충돌 | §9.4, §11.2 | propose-adjust-approve + 상호충돌 매트릭스(M4) |

### 0.2 아키텍처비평(archCritique) → 반영

| # | 아키텍처비평 지적 | 반영 위치 | 처리 방식 |
|---|---|---|---|
| A1 | LangGraph×Temporal 2026 통합 실험단계 = 단일 장애점 | §6.6, §12.2, §14-D2 | **MVP에서 LangGraph interrupt 미사용**. 인터럽트 의미론 100% Temporal Signal+장면 경계 폴링 단일 귀속. LangGraph는 순수 그래프 실행기 |
| A2 | LangGraph checkpointer를 durable 권위로 쓰면 이중 권위 | §6.5, §12.2 | LangGraph는 Temporal activity 내부 단명 실행. 크래시 복원 권위 = Temporal 단일 |
| A3 | RAG/벡터/온톨로지 분산 트랜잭션 위험 | §8.1, §12.3 | pgvector + PG FTS + 온톨로지를 단일 PostgreSQL로 통합. 재색인 단방향 루프 ACID |
| A4 | best-so-far monotone이 temperature>0 위에 섬 | §6.4, §14-D1, §16.2 | monotone 1급 축 = 결정론 위반 카운트(LLM-free). 의미 점수는 보조축. fallback 순서 데이터 전 동결 |
| A5 | finalize 팬아웃 워터마크 desync | §7.5, §12.4, §14-D5 | state_applied=모든 컨슈머 ack 후 전진 + 멱등키 + 보상 트랜잭션 + 컨슈머 성격별 차등 차단 |

### 0.3 실패추적(failTrace, 협업·에이전트 루프 8건) → 반영

| # | 실패 시나리오 | 반영 위치 | 방어 |
|---|---|---|---|
| F1 | 회차 내부 무한루프/진동 | §6.4 | MAX_ROUND(3~4) + best-so-far monotone + oscillation_signature EWMA + MAX_INTERRUPT |
| F2 | 작품 단위 비수렴(데드락) — 회차 내부 가드 못 잡음 | §4.4 | L1 progress-monotone / L2 defect_debt ABS_CAP / L3 STALLED_AWAITING_AUTHOR |
| F3 | writer 권위 split-brain | §10.3, §10.4 | 물리 단일 소유자=orchestration 1곳 + lease 만료 + append-only ChapterVersion |
| F4 | never-interrupt-running-tool 위반 | §10.1 | 토큰 인터럽트 폐기, 장면 경계 흡수만, best-so-far 저장(중간 절단 금지) |
| F5 | durable 의미적 복원 불일치(replay≠동일출력) | §10.5 | 작가 노출분=즉시 멱등 체크포인트, in-flight만 재실행, 재개 고지+재확인 게이트 |
| F6 | 무응답 종료조건 모순(잠정진행 vs 동결 vs HARD차단) | §10.8 | 직교 매트릭스(severity별 단일 행동, HARD 침범 후보=영구 동결) |
| F7 | 도구 오용 — 신뢰등급 혼합 | §6.3, §8.3 | signal_grade 코드레벨 분리, 비대칭 RAG 주입 혼합 불가 |
| F8 | 장면 경계 비결정 자유도 | §6.7 | 결정론 scene_plan 사전 분할, draft_scene 순회만, over-run 이월 |

### 0.4 완결성 점검(completeness 8건 + 블라인드스팟) → 반영

| # | 완결성 지적 | 반영 위치 | 처리 |
|---|---|---|---|
| C1 (Blocker) | 디폴트(유인) 모드 liveness 공백 — 인간 부재 시 안전망 증발 | §4.4 | **신설**. v2 무인 불변식을 KILL→강제 작가 surface로 재배선 |
| C2 (Blocker) | 무응답 종료조건 3경로 모순 | §10.8 | **신설** 직교 매트릭스 |
| C3 (High) | 미검토 회차 RAG 오염 격리 부재 | §8.5, §8.6 | trust_tier='unreviewed_machine' + provisional + 막경계 정산 |
| C4 (High) | 세 최소선 비양립 시 분기 미결정 | §16.0 | M1 GO 게이트 분기 결론 조건부 |
| C5 | finalize 팬아웃 함께 전진 미보장 | §7.5, §12.4 | state_applied 모든 ack 후 전진 |
| C6 | judge p95 폭발(장면 수 선형) | §13.3 | critique 회차당 상수화 |
| C7 | 계층B 보조 LLM 오도 | §6.6, §10.7 | EscalationBrief = 결정론 증거 1차 + LLM 요약 보조 분리 |
| C8 | seam 책임 공백 (장면 재집필 후 봉합) | §6.4, §6.8 | seam_reweave 노드 + 재검증 의무화 |
| BS1 (blindspot) | 미검토 회차 안전 책임 귀속(법적) | §3.6, §13.5, §18 | 안전 축 1슬롯 무조건 실행 + 변호사 자문 M1 선결 |
| BS2 (blindspot) | M1 표본 N이 monotone/세 최소선 판정 검정력 부족 | §16.4 | 최소 N·강등 규칙 사전 동결 |

---

## 1. 개요 & 비전 (에이전틱 코파일럿)

### 1.1 비전

> **"긴 호흡을 일관되게"** — 웹소설 작가의 가장 큰 부담을 덜어주는 AI 창작 동반자. 작가 머릿속에만 있던 세계관·인물·복선을 구조화(SSOT)해 몇십~몇백 회차에 걸쳐 작품 전체가 어긋나지 않게 지키고(RAG 기반 일관성), 작가가 짠 상위 스토리 골격을 회차 단위로 점진 집필하되 매 회차가 이전 흐름에서 끊기지 않도록 보장한다.

핵심은 **역할 역전(role inversion)**이다. v1은 작가가 쓰고 기계가 교정했다. v2는 기계가 쓰고 기계가 자기 교정했다(인간 추방). **v3는 기계가 쓰고 인간이 능동적으로 조향·수정한다.** 인간은 시스템 외부의 메타 튜너가 아니라 **회차 루프 내부의 in-the-loop 협력 노드**다.

목표 사용자는 "버튼만 누르면 소설이 나오길 바라는 사람"이 **아니라**, **"자기 작품을 작가로서 소유하면서 더 빠르고 일관되게 집필하고 싶은 창작자"**다. 그러므로 이 제품은 **저자를 대체하는 자동 노예기**가 아니라, 작가를 **편집자·총괄 디렉터·빨간 펜**의 위치로 끌어올리면서 창작의 주도권은 작가에게 두고 생산성과 품질을 끌어올린다.

### 1.2 무엇이 다른가 (차별화)

1. **구조화된 단일 진실 공급원(SSOT) + 일관성 검증.** 일반 LLM 채팅은 대화 맥락이 흘러가면 과거 설정을 잃고 충돌을 일으키지만, 이 제품은 인물/세계관/관계를 온톨로지로 구조화해 결정론적으로 검증한다. Scrivener 같은 정적 자료 정리 도구가 '저장'만 한다면, 이 제품은 능동 추론·충돌 탐지·작가 surface를 한다.
2. **장기 기억으로서의 RAG.** ChatGPT는 컨텍스트 윈도/요약의 한계로 과거 회차를 잊지만, 이 제품은 확정된 모든 회차를 인덱싱해 회차 N을 그 위에서 점진 생성한다(회차 N = f(RAG(1..N−1), SSOT, 골격, 작가지시)).
3. **구조 우선(top-down) 생성.** 일반 LLM이 프롬프트당 단발 생성이라면, 이 제품은 작가가 정한 전체 골격·비트를 회차가 그 안에서 채우는 방식으로 일관성과 페이싱을 보장한다.
4. **작가 주도의 점진적 워크플로.** 완전 자동화가 아니라 "기계 초안 → 작가 검토 → 확정분이 다시 RAG·SSOT에 반영"되는 인간 주도 루프.
5. **웹소설 특화.** 회차(연재) 단위 작업, 점진 누적, 한국어 종결어미/문체, 복선-회수 등 범용 도구가 다루지 않는 웹소설 도메인 1급 지원.

### 1.3 MVP 명제 (단 하나의 검증)

> **"작가가 상위 구조와 회차를 점진 검토하면, AI가 생성하는 회차 본문이 '회차 단위 RAG 연속성'을 일반 LLM 대비 작가 체감만큼 보장하고, 작가가 처음부터 다시 쓰지 않고 '빨간 펜' 위치에서 작업한다."**

즉 MVP의 본체는 **"회차 1편을 에이전트 루프로 생성 + 작가 개입 + RAG 연속성"**이다. 무인 완결이 아니라 **비발산 방지 + 인간 조기교정 보완**이 핵심이다.

---

## 2. v1 → v2 → v3 포지셔닝

| 축 | v1 (human-in-loop) | v2 (autonomous) | **v3 (asymmetric copilot)** |
|---|---|---|---|
| 누가 쓰나 | 작가가 씀, 기계가 교정 | 기계가 씀, 기계가 자기 교정 | **기계가 씀, 인간이 능동 조향·수정** |
| 인간 위치 | 최종 안전망(펜) | 시스템 외부 메타 튜너(추방) | **회차 루프 내부 in-the-loop 협력 노드 + 외부 도구 응답자** |
| 생성 단위 | 작가 페이스 | 시드→완결 일괄 자율 | **회차(에피소드) 단위 점진 누적** |
| 최종 게이트 | 작가 | KILL 사전등록 + 익명 패널 α≥0.7 | **작가 본인 승인 (+ 외부 타당성 1회 객관 닻)** |
| liveness(종료 보장) | 작가가 멈추면 멈춤 | 72h hard·progress·deadlock-free **(KILL 트리거)** | **§4.4 디폴트 liveness 계약 (강제 작가 surface 트리거)** |
| 핵심 명제 | — | 무인 L0 완주 보장 | **회차 RAG 연속성 + 저마찰 인간 개입 + 협업 효율** |

**"기계 집필 · 작가 보조"의 정확한 의미:**
- **기계 집필(machine drives):** 에이전트가 회차 산문 초안을 주도 생성한다. 작가는 백지에서 타이핑하지 않는다.
- **작가 보조(human assists/decides):** 작가는 (1) **결정(decide)** — 비트 검토, 직후 승인/거부/미세수정, 방향전환, escalation 분기 선택, 정체 시 강제 호출에 응답, (2) **보조(assist)** — 비트 카드 조정, 캐릭터 보이스 힌트, 선호 신호. **"작가가 옆에서 쓴다"가 아니라 "기계 초안 위에서 조향·큐레이션·재지시한다"**가 정확한 그림이다.

> **기획비평 P1 수용:** "기계가 쓰고 인간이 조향"이 실무에서 v1(인간이 쓰고 기계 교정)으로 미끄러질 위험을 §3.5 정체성 최소선과 §13.6 소유감 측정으로 명시 방어한다.

---

## 3. v2 대비 변경 (kept / relaxed / added)

v2는 **완전 무인 자동(autonomous-by-default)**이었고 "인간 안전망 제거"를 전제로 무인 장기실행용 중장비(사전등록 4종, 72h liveness 불변식, escalation 전역 예산 KILL, 인간 닻 KILL 게이트)를 깔았다. v3는 **인간이 능동 조력자(안전망)로 회차 루프 내부에 복귀**하므로, 무인 전용 중장비를 폐기하지 않고 **선택적 고자율 모드(opt-in)로 우경량화(right-size)**한다.

> ⚠️ **완결성 점검 C1(Blocker) 수용 — 우경량화의 함정:** "인간이 안전망으로 복귀했으니 무인용 안전장치는 불필요"라는 가정에는 빈틈이 있다. **정작 인간이 부재할 때(auto_advance의 본질) 안전망이 사라진다.** v2의 work-레벨 liveness 불변식(progress-monotone·deadlock-free)을 통째로 '무인 전용'으로 강등하면서 디폴트(유인) 모드의 종료·비교착 보장을 신설하지 않는 것은 Blocker다. v3 final은 이를 **§4.4 디폴트 모드 liveness 계약**으로 신설하되, **KILL이 아니라 강제 작가 surface로 재배선**한다.

### 3.1 KEPT (v2에서 유지 — 인간 복귀와 무관하게 옳았던 것)

| 유지 항목 | 이유 |
|---|---|
| 결정론 코어(consistency_check / prose_lint / 워터마크 / writer_lock) LLM 완전 배제 | 신뢰등급 분리의 토대. 인간 유무와 무관하게 ground truth 검증은 결정론이어야 함 |
| 온톨로지 SSOT + EntityStateTimeline 시점 일관성 | 일관성 1급 자산 |
| 이종 judge 앙상블 N≥3, generator≠judge | 자기평가 편향 방어 |
| append-only 버전 모델(supersedes 체인 + is_active 단일) | lost update 구조적 불가 |
| 비대칭 RAG 주입(ontology 박기 vs rag 찾기) | 결정론 오염 방어의 핵심 불변식 |
| 안전 축 검증(폭력·혐오 누적 드리프트) | 미검토 회차에서도 절대 끄지 않음(§13.5) |

### 3.2 RELAXED (무인 전용 중장비를 선택적 고자율 모드로 강등 — 인간 안전망 복귀로 우경량화)

| v2 (무인) | v3 (우경량화) |
|---|---|
| §0 사전등록 4종 세트 | **opt-in 고자율 모드에서만 적용하는 옵션 프로토콜** |
| KILL 임계 + gray zone 강제 KILL 디폴트 | **gray zone = 인간 결정 라우팅(request_human_input)** |
| 72h liveness wall-clock hard **KILL** 트리거 | **무인 배치 모드에서만. 단 progress-monotone·deadlock-free는 폐기가 아니라 §4.4에서 '강제 작가 surface' 트리거로 재배선해 디폴트 모드에 존속** (C1 수용) |
| 엔딩 수렴 progress 강제전이 | **정체 신호를 작가에게 강제 surface(auto_advance 강제 해제) + 방향전환 제안** |
| escalation 전역 예산 KILL(작품당 ≥10회=무산) | **UX 마찰 모니터링 지표(KILL 아님)** |
| 인간 닻 KILL 게이트(익명 3명 α≥0.7 + 완독률<60% KILL) | **작가 본인 승인. 단 익명 1회 객관 닻은 judge recall·작가 편향 보정용으로 잔존(§13.4)** |

### 3.3 ADDED (v3 신규 도입)

1. **에이전틱 하네스** — 회차 생성을 Plan→Act(tool)→Observe→Reflect 루프로 (§6)
2. **회차 단위 RAG 루프를 1급 백본으로** — 회차 N = f(RAG(1..N−1), SSOT, 골격, 작가지시) (§7)
3. **장면 경계 인간 개입 인터럽트** — 토큰 단위 폐기(정직한 우경량화), 장면 경계의 결정론 정의 §6.7 신설 (§10)
4. **협업형 worldgen** — AI 제안 → 작가 조정·승인·재지시 (§9.4, M4)
5. **AuthorDirective 레저** — 작가 조향을 누적 기억으로(자기모순 회수 포함) (§4.5, §11.3)
6. **§4.4 디폴트 모드 liveness 계약** (C1)
7. **§10.8 무응답 단일 종료조건 직교 매트릭스** (C2)
8. **§8.5 미검토 회차 RAG 오염 격리** (C3)

### 3.4 Non-Goals (비목표)

- 완전 무인 완결(작가는 골격과 회차를 검토; 노예 노예기 아님)
- 출판 플랫폼 자동 연재/정산(카카페/노벨피아/네이버 등 외부 연동)
- 실시간 협업 편집(동시 커서, 라이브 댓글, 동시 편집, 실시간 음성)
- 영화화·웹툰화·이미지/멀티미디어 생성
- 매출/조회수/회차별 수익 분석 대시보드, 수익성 모델
- 다국어 번역·해외 동시 연재(MVP는 한국어 우선)
- 장르 무제한 지원(MVP는 현대 판타지 1개 장르로 시작, 점진 확장)
- 음성 입력/TTS, 코뮤니티 기능

### 3.5 정체성 최소선 (Identity Floor) — 기획비평 P1/P3 수용

> auto_advance 디폴트가 "기계가 다 쓰고 작가는 도장만 찍는" v1 회귀를 유발한다는 비평을 정면으로 닫는다.

- **회차당 최소 1개 능동 결정 보장:** auto_advance 디폴트에서도 작가는 회차당 최소 1개의 능동 결정(승인이 아닌 조향·재지시·선택)을 마주하도록 시스템이 강제 surface한다. 이 "능동 결정"은 "읽지 않고 승인" 클릭과 구분되며, §13.6에서 형식 통과율로 추적한다.
- **§4.4 liveness 상한이 작가를 강제로 surface로 끌어들인다:** L1/L2 트리거가 auto_advance를 강제 해제하므로, 작가가 완전히 손을 떼도 시스템이 무한히 헛것을 찍어내지 않는다.

### 3.6 저작물성 · 안전 책임 귀속 (법적 모순 명시) — BS1 수용

- **저작물성:** auto_advance 미검토 회차에서 "작가의 창작적 기여"가 희박할 경우 저작권 귀속이 모호해진다. 이는 기술 설계로 완전히 닫히지 않는다. **저작권 변호사 자문을 M1 선결**로 둔다(§16, §18).
- **안전 책임:** 미검토 회차도 안전 축 검증(폭력·혐오 누적 드리프트)을 **무조건 1슬롯 실행**(§13.5)한다. 안전 축마저 끄면 미검토 회차의 안전 책임 귀속이 기술적으로 무방비가 되어 법적 모순이 심화된다.

---

## 4. 협업 모델 & 인간 개입 지점

### 4.1 협업 모델 — 비대칭 코파일럿

```
            ┌─────────────────────────── 작가 (in-the-loop 협력 노드) ───────────────────────────┐
            │ decide: 비트검토 · 직후승인/거부/미세수정 · 방향전환 · escalation분기 · 정체응답      │
            │ assist: 비트카드조정 · 보이스힌트 · 선호신호                                          │
            └───────▲───────────────────────────────────────────────────────────────────▲────────┘
                    │ surface / request_human_input                                       │ AuthorDirective
        ┌───────────┴───────────┐                                          ┌──────────────┴──────────────┐
        │  계층B 오케스트레이터  │  ←── 인터럽트 라우팅·권위 중재·무응답 집행  │  계층A 회차 에이전트 루프    │
        │  (결정론 중재 + 보조LLM) │                                          │  (LangGraph StateGraph)      │
        └───────────────────────┘                                          └──────────────────────────────┘
                    ▲                                                                       ▲
                    │                                       Temporal durable backbone (단일 권위) │
        ┌───────────┴───────────────────────────────────────────────────────────────────────┴────────┐
        │  PostgreSQL SSOT(온톨로지·워터마크·directive·liveness) + pgvector RAG + PG FTS (단일 DB)     │
        └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

**기계 = drive, 인간 = decide/assist.** 산문은 기계가, 빨간 펜은 인간이.

### 4.2 인지부하 vs 검증 트레이드오프 (기획비평 P2 수용)

저마찰(효율)과 검증(일관성)은 정면 충돌한다. 모든 모순을 작가에게 띄우면 마찰이 폭발하고(작가가 떠남), 안 띄우면 일관성이 붕괴한다. 이 트레이드오프를 **1급 지표(interaction friction, §17)**로 추적하고, **critique 회차당 상수화(§13.3)**와 **EscalationBrief 우선순위 산출(§6.6)**으로 작가가 마주하는 의사결정 총량을 하드캡 N 이하로 묶는다.

### 4.3 인간 개입 지점 (8개 + liveness 강제 호출)

| 지점 | 시점 | 작가 행동 | MVP 포함 | 메모 |
|---|---|---|---|---|
| 지점1 | 시드 인테이크 | 장르·세계관·톤 입력 | M4(worldgen) | MVP는 단일 회차 우선 |
| 지점2 | 세계관 부트스트랩 | 인물/관계/규칙 propose-adjust-approve | M4 | |
| **지점3** | 회차 골격/비트 사전 검토 | 비트 카드(목표·사건·종료상태·클리프행어) 수정/순서변경 | **MVP** | 디폴트=auto_advance, 근경 1~2화만 검토 |
| **지점4** | 회차 생성 중 인터럽트(장면 경계) | '이 장면 다시'/'톤 바꿔'/'멈춰' | **MVP는 직후 개입만** | 실시간 인터럽트는 M2 |
| **지점5** | 회차 직후 승인/거부/미세수정 | finalize / 거부(재생성) / 직접 수정(human_edited) | **MVP** | writer 권위 human 핸드오프 |
| **지점6** | 방향전환(redirect) | '주인공 좌절시켜'/'이 조연 주역으로'/'톤 어둡게' → AuthorDirective | **MVP** | 이후 회차 누적 전파 |
| **지점7** | escalation/분기점 | request_human_input → EscalationBrief 선택/재지시 | **MVP** | |
| **L1** | progress 정체(K=15회차 Δ≈0) | auto_advance 강제 해제 + 3안(방향전환/중단/계속) | **MVP** | §4.4 |
| **L2** | defect_debt ABS_CAP 도달 | 자동 진행 차단 + 정산 or 진행 승인 | **MVP** | §4.4 |
| **L3** | STALLED_AWAITING_AUTHOR | 동결(frozen, durable 보존) + 작가 알림 | **MVP** | §4.4 |
| 자기모순 회수 | directive 충돌 검출 시 | 과거 지시 출처 인용 노출 후 선택 | **MVP** | §4.5 |
| MAX_INTERRUPT 초과 | 같은 장면 거부 상한 초과 | '직접 집필 or 3안 강제 선택' | **MVP** | 재생성 룰렛 차단 |
| degraded/에러 | judge 2개+ 장애·컨슈머 stuck | 시스템 상태 배지 + 즉시 알림 | **MVP** | 조용한 정지 금지 |
| durable 재개 | 크래시 복원 후 | '마지막 본 장면 재생성됨' 고지 + 재확인 | M1/P1 골격 | §10.5 |

### 4.4 §4.4 디폴트(유인) 모드 liveness 계약 (C1 Blocker — 신설)

**문제:** auto_advance에서 "작가 부재 + 정체 + defect_debt 무한 적재 + 엔딩 비수렴"이 동시 성립하면 회차를 영원히 찍어낸다. 회차 내부 가드(§6.4)는 이 작품 단위 비수렴을 못 잡는다(F2). **부모 Temporal workflow가 work-레벨에서 결정론으로 평가한다. KILL이 아니라 강제 작가 surface.**

| 레벨 | 트리거(결정론) | 액션 | 데이터 |
|---|---|---|---|
| **L1 progress-monotone** | 최근 K=15회차 Δprogress ≈ 0 | auto_advance 강제 해제 + request_human_input(정체 진단 + major replan 제안 + 3안: 방향전환/중단/계속). 작가 결정 전까지 자동 진행 차단 | `progress_scalar`, `delta_window(K)` |
| **L2 defect_debt 절대 상한** | `defect_debt_total ≥ ABS_CAP` | auto_advance 강제 해제 + 작가 결정 라우팅(정산 or 진행 승인). 정산·승인 전까지 차단 | `DefectDebtEntry`, `abs_cap` |
| **L3 STALLED_AWAITING_AUTHOR** | L1/L2 트리거 + 무응답 타임아웃 경과 | 동결(frozen, durable 보존) + 작가 알림. 작가 복귀 시에만 해제 | `stall_state` 상태머신 |

**상수 동결 정책 (D3, 아키텍처비평 A4·기획비평 P3 연계):** K=15·ABS_CAP·MAX_CONSECUTIVE_UNREVIEWED는 **단일 하드코딩이 아니라 '관측 후 즉시 재조정 가능한 config'**로 설계하되, 초기값은 **보수적(데드락을 늦게라도 확실히 잡는 쪽)**으로 동결한다. 그 값이 "근거 있는 추정"이 아니라 **"보수적 안전판"**임을 명시 기록한다.

**무응답 타임아웃 정의 (D4):** L3 STALLED 진입 무응답 타임아웃은 **wall-clock이 아니라 '작가 활동 신호 부재 구간'**으로 정의한다. 유인 모드에서 작가의 정상적 장기 자리비움(수일)과 진짜 정체를 분리하기 위함이다.

> 잔존 리스크(§15): '작가 활동 신호 부재 구간'으로 정의해도 작가 패턴마다 경계가 달라 절대 단위 동결 근거는 여전히 약하다.

### 4.5 작가 자기모순 directive 회수 (기획비평 P4 수용)

30화 "이 조연 죽여"와 50화 "이 조연 등장" 충돌 시, **되묻는 대상이 모순을 만든 작가 본인**이다. EscalationBrief에 과거 지시 출처를 회수해 인용한다:

```
EscalationBrief:
  당신의 과거 지시 #N (narrative_order 30): "이 조연을 죽여" 와 충돌합니다.
  conflict_with_directive_id = dir_0030
  conflict_origin = ?  (변심 / 전파오류)
  recommended_options[3]: ...
```

- `conflict_origin = 변심` → 신규 지시 채택 + 과거 지시 `superseded_by` 설정
- `conflict_origin = 전파오류` → 채택 보류 + 전파 경로 정정
- 작가가 자기 과거 지시를 망각해도 컨텍스트를 회수해 자기모순을 surface한다.

---

## 5. 대상 작품 정의

| 항목 | 정의 |
|---|---|
| 분량 | 완결 기준 **100~200화** (회차당 5,000~5,500자 한국어) |
| 생성 방식 | **점진 생성** — 시드→완결 일괄이 아니라 회차 단위 점진 누적(continue-as-new) |
| 장르 | MVP = 현대 판타지 1개 장르(차후 확장). 헌터/회귀/SSOT 충돌이 빈번한 장르가 가치 큼 |
| 페이싱 | 작가 골격(Act)→비트(Beat)→장면(scene_plan)→회차 본문. 클리프행어 매 회차 |
| 일관성 요구 | 생사·소속·능력수치·관계·복선 회수가 100~200화 걸쳐 어긋나면 안 됨 |
| 비목표 | 단편/연작/장르 무제한은 MVP 제외 |

**대상 작업 정의(targetWorkDefinition):** "한 작품(work)"은 동일 timeline_branch_id를 공유하는 회차 시퀀스. 회귀물 분기(timeline_branch_id 다중)는 P2.

---

## 6. [핵심] 에이전틱 하네스 설계

> **설계 제1원칙: "에이전트 자유도가 높을수록 안정성 보장이 어렵다."** LLM 자유도를 **5개 핀포인트(plan_queries · draft_scene · extract_canon · critique · EscalationBrief 요약)에만 격리**하고, **전진·검증·라우팅·종료는 전부 결정론**으로 박는다.

### 6.1 2계층 + Temporal durable 백본

| 계층 | 구성 | 역할 |
|---|---|---|
| **계층A** | LangGraph StateGraph (Temporal activity 내부 단명 실행) | 회차 집필 루프 주도(drive) |
| **계층B** | collaboration orchestrator (결정론 중재기 + 소형 LLM 보조) | 인터럽트 라우팅·writer 권위 중재·지시 충돌 판정·request_human_input 발동·§4.4 강제 surface·§10.8 매트릭스 집행 — **전부 결정론**. LLM은 EscalationBrief 요약·권장3안 best-effort 보조만 |
| **백본** | Temporal | 크래시 복원 권위 단일. 회차=child workflow, Signal=인터럽트, Update=동기 승인, continue-as-new=회차 누적, 부모 workflow=§4.4 liveness 평가 |

### 6.2 회차 에이전트 루프 다이어그램 (Plan→Act→Observe→Reflect)

```
                         ┌──────────────────────────────────────────────────────────────┐
   [부모 Temporal WF]    │              §4.4 liveness 평가기 (L1/L2/L3)                   │
   work-레벨             │   progress Δ · defect_debt · STALLED_AWAITING_AUTHOR           │
                         └───────────────────────────┬──────────────────────────────────┘
                                  continue-as-new     │ child workflow (회차 1편)
                                                       ▼
  ┌──────────────────────────────── 계층A: LangGraph StateGraph (Temporal activity 내부 단명) ───────────────────────┐
  │                                                                                                                  │
  │  plan_chapter ──► assemble_memory(ContextBuilder) ──► forget_gate ──► draft_scene (scene_plan 순회) ──┐          │
  │   (LLM:없음;        │ select_skeleton                  │ required_facts        │ [scene 미니루프]        │          │
  │    골격→비트)       │ plan_queries (★LLM)              │ 100% 커버리지 검증     │  draft (★LLM 스트림)    │          │
  │                     │ retrieve(rag_search)             │ PRE-GEN 결정론         │     │                   │          │
  │                     │ forget_gate                      └───────────────────────┘     ▼                   │          │
  │                     │ assemble (ontology 상단·rag 하단)                      consistency_check ∥ prose_lint│          │
  │                     │ emit_provenance                                          (매 장면, LLM 배제)        │          │
  │                     └──────────────────────────────────────────┐                    │                   │          │
  │                                                                 │       (critical 발생 시) critique       │          │
  │                                                                 │            (★LLM judge N≥3)              │          │
  │                                                                 │                    │                   │          │
  │                                                                 │              reflect (위반 시)           │          │
  │                                                                 │     partial_rewrite ─► seam_reweave ─► 재검증 ──┘   │
  │                                                                 │     [best-so-far monotone · MAX_ROUND · 과교정 가드] │
  │                                                                 ▼                                                   │
  │                                          critique (finalize 직전 1회 — 회차당 상수)                                  │
  │                                                                 │                                                   │
  │                                                       human_review (지점5)                                          │
  │                                                                 │                                                   │
  │                                                          finalize ──► 워터마크 3종 전진 (§7.5 팬아웃 계약)            │
  └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  종료조건 = 회차 비트 충족 ∧ 결정론 critical=0 ∧ judge 핵심축 통과 ∧ 작가 승인
```

### 6.3 도구셋 (toolset) — 신뢰등급 분리 (F7 방어)

| 도구 | 신뢰등급 | 트리거 | I/O | 핵심 계약 |
|---|---|---|---|---|
| `rag_search` | 검색/**서사(낮음)** | assemble_memory | Pydantic v2 | 이전 회차 하이브리드 검색(Kiwi BM25+벡터 RRF), `as_of=last_indexed` 시점필터, trust_tier별 가중, **하단 주입** |
| `ontology_lookup` | **결정론/ground truth** | assemble_memory | Pydantic v2 | 생사·소속·능력수치·관계 lookup, **상단 고정·누락0**(절단금지 어서션), 동기경로 LLM 금지 |
| `draft_scene` | **생성(★LLM)** | draft_scene 순회 | 스트리밍 | scene_plan 순회 장면 단위 산문, 인터럽트 단위=장면 경계, over-run 다음 장면 이월 |
| `critique` | **judge(★LLM)/제안** | finalize 직전 1회 + critical 장면 | Pydantic v2 | 이종 N≥3, generator≠judge, 다축(안전 축 포함), **단독 KILL 권위 없음→작가 라우팅** |
| `consistency_check` | **결정론/ground truth** | 매 장면 | Pydantic v2 | 충돌 4종 + EntityStateTimeline 시점 + 워터마크 게이트, **LLM 완전 배제** |
| `prose_lint` | **결정론** | 매 장면 | Pydantic v2 | Kiwi/KSS 종결어미 반복·n-gram·금칙어, **무LLM** |
| `payoff_check` | **결정론+RAG** | finalize 전 | Pydantic v2 | PlotThread 터치체인(plant→touch→payoff), ClueSpanAnchor 대조, false payoff 방어, `intentionally_incidental` 영구 제외 |
| `request_human_input` | **HITL** | escalation/liveness | EscalationBrief | 분기 라우팅, 무응답 종료조건=§10.8, **빈도=마찰 지표(KILL 아님)** |
| `apply_author_directive` | 주입/**권위 채널** | plan | Pydantic v2 | 활성 directive를 plan 컨텍스트 **상단 고정**(슬롯 cap 경쟁 배제) |
| `detect_directive_conflict` | **결정론** | redirect/plan | Pydantic v2 | 지시 충돌·HARD/승인분/finalized 침범 **100% 검출**→자동 진행 금지, conflict_origin 분리 |
| `emit_canon_proposal` | **구조화** | finalize 후 | CanonStagingRecord | staged 제안, 순수 결정론분만 자동확정, **semantic은 requires_author_confirm** |
| `acquire/release_writer_lock` | **결정론** | 핸드오프 | advisory lock | Chapter 단일 writer 런타임 집행, lease 만료=§10.8 |

> **F7 방어 — 신뢰등급 혼합 금지:** `signal_grade`를 코드 레벨로 강제 — **LLM 산출물이 1바이트라도 결정론 코어에 섞이면 등급2(semantic)**로 강등. ontology(상단고정·누락0) vs rag(하단·cap·trust가중)는 스키마상 분리필드로 혼합 불가.

### 6.4 회차 내부 안정성 가드 (F1·C8 — 전부 결정론, reflect 분기를 코드가 제어)

| 가드 | 메커니즘 | 비고 |
|---|---|---|
| `MAX_ROUND` | 3~4 (config, M1 실측) | partial_rewrite 하드캡 |
| **best-so-far monotone** | **1급 축 = 결정론 위반 카운트(consistency critical 수 + prose_lint 위반 수)** | **의미 점수(critique)는 보조축**. 비악화 강제 |
| 과교정 가드 | regression / new_violation / divergence 탐지 | 통과 부분 악화 차단 |
| `oscillation_signature` | 해시 EWMA | 같은 장면 반복 재집필 탐지 |
| `MAX_INTERRUPT` | 같은 장면 거부 상한 | 초과 시 **'직접 집필 or 3안 강제 선택'**(재생성 룰렛 차단) |
| **seam_reweave (C8)** | partial_rewrite 후 인접 장면 경계 봉합 노드 + **재검증 의무화** | seam 책임 공백 방어 |

> **아키텍처비평 A4 / D1 수용 — monotone 1급 축 동결:** P0 명제(루프 완주) 종료조건이 monotone(비악화)인데 의미 점수를 1급 축으로 쓰면 temperature>0 분산이 기준선을 매 라운드 흔들어 가드가 무의미해진다. **결정론 위반 카운트는 재실행 간 안정적**이라 기준선이 흔들리지 않는다. M1에서 output_divergence가 사전 동결 임계 초과 시 **fallback 순서 = (1) monotone을 결정론 축 단독으로 강등 → (2) draft_scene temperature 낮춤 → (3) 최후에 monotone 로직 재설계.** 이 순서를 **데이터 보기 전 동결**한다(§16.2).

### 6.5 LangGraph durable 권위 강등 (아키텍처비평 A2)

- LangGraph는 **Temporal activity 내부에서만 단명 실행**. Postgres checkpointer를 durable 권위로 쓰지 **않음**(activity 수명 내로만).
- 재시도 시 **처음부터 재실행** — 도구 멱등 + journaling으로 보장.
- **크래시 복원 권위 = Temporal 단일.** checkpointer 이중화 금지(§12.2).

### 6.6 계층B — 인터럽트 의미론 단일 귀속 (아키텍처비평 A1 / D2 / C7)

> **D2 결정 — MVP에서 LangGraph interrupt 미사용.** 2026 LangGraph×Temporal 통합이 실험단계라 interrupt 누락/중복은 '얇은 어댑터 격리'로 봉인되지 않고 **두 시스템 의미론 충돌**(LangGraph 노드 경계 interrupt vs Temporal Signal observe 시점)이다. 인터럽트를 **100% Temporal Signal + 장면 경계 폴링**으로만 처리하는 단방향 설계로 귀속시키고, LangGraph는 순수 그래프 실행기로만 쓴다. **어댑터 자체가 사라진다.**

**EscalationBrief 오도 방어 (C7 / D6):** EscalationBrief는 **'결정론 증거 1차 표면 + LLM 요약 보조 레이어'**로 분리한다.
- **결정론 산출(검증 불필요한 ground truth):** 근본원인 후보·캐논diff·과거 지시 인용(conflict_with_directive_id) — `detect_directive_conflict`·`consistency_check` 출력을 **그대로 노출**.
- **LLM 보조(best-effort):** 권장 3안 생성 + '읽기 보조 요약'만 위에 얹음.
- 작가는 **항상 raw 결정론 증거를 한 클릭으로** 볼 수 있다(검증 게이트 대신 raw 증거 상시 접근으로 환각 요약 신뢰를 구조적 차단).

### 6.7 장면 경계 결정론 정의 (scene_plan) (F8 방어)

- `plan_chapter`에서 비트→장면 사전 분해를 **결정론 규칙**(이벤트 경계·시공간 전환·시점 인물 전환)으로 수행.
- draft 전 `scene_plan`(고정 리스트) 확정. **`draft_scene`은 순회만, 경계 재정의 금지.** LLM 동적 판정 금지.
- over-run(장면 크기 상한 초과) → **다음 장면 이월 강제 분절.**
- **장면 크기 상·하한**(인터럽트 반응성 vs judge 비용)은 **M1 임계로 동결**(§16).

### 6.8 자가교정 미니루프 상세

```
draft_scene(scene_i)
   → consistency_check ∥ prose_lint   (매 장면, 결정론)
   → IF critical > 0:
        critique(scene_i)             (★LLM judge, critical 장면만)
        → reflect → partial_rewrite(scene_i)
        → seam_reweave(scene_{i-1} 경계 · scene_{i+1} 경계)   (C8)
        → 재검증 (consistency_check ∥ prose_lint)
        → best-so-far monotone 판정 (결정론 위반 카운트 1급 축)
        → IF round ≥ MAX_ROUND or oscillation 탐지: surface (MAX_INTERRUPT 분기)
   → ELSE: 다음 장면
```

---

## 7. [핵심] 회차 단위 생성 루프 (회차 N = f(RAG(1..N−1), SSOT, 골격, 작가지시))

### 7.1 회차 N 생성 함수

```
회차_N = f(
    RAG(1..N-1)          # as_of=last_indexed 시점필터 (미래 누수 차단)
    SSOT(온톨로지)        # ontology_lookup 결정론 박기
    ChapterSkeleton/Beat  # 작가 골격 (지점3)
    AuthorDirectiveLedger # 누적 작가 조향 (지점6)
)
```

### 7.2 End-to-End 흐름 (단일 회차)

```
[지점3] 골격/비트 사전 검토 (디폴트 auto_advance, 근경 1~2화만)
   ▼
plan_chapter        : 비트 → scene_plan 결정론 분할 (§6.7), required_facts 산출
   ▼
assemble_memory     : select_skeleton → plan_queries(★LLM) → retrieve(rag_search)
   (ContextBuilder)   → forget_gate(결정론 커버리지) → assemble(비대칭 주입) → emit_provenance
   ▼
forget_gate         : required_facts 100% 커버리지 PRE-GEN 검증
                      미커버 → targeted_refetch / force_ontology_inject → K회 실패 시 request_human_input
   ▼
draft_scene 순회     : 장면 미니루프(§6.8) — draft→검증→(critical)critique→partial_rewrite→seam_reweave→재검증
   ▼
critique (finalize 직전 1회) : judge N≥3 다축 (회차당 상수, §13.3)
   ▼
[지점5] human_review : finalize / 거부(재생성) / 직접 수정(human_edited)
   ▼
finalize            : 워터마크 3종 전진 + 팬아웃 (§7.5)
   ▼
continue-as-new     : 회차 N+1
```

### 7.3 비대칭 RAG 주입 (도메인 최중요 불변식)

```
┌──── plan 컨텍스트 (상단) ────┐
│ [상단 고정] AuthorDirective   │ ← 권위 채널, 슬롯 cap 경쟁 배제
│ [상단 고정] ontology_lookup   │ ← ground truth, 누락0 (절단금지 어서션)
├──────────────────────────────┤
│ [하단·슬롯 cap] rag_search    │ ← 서사(낮음), trust_tier별 가중
└──────────────────────────────┘
   혼합 불가 (스키마상 분리필드 강제)
```

### 7.4 RAG는 보장이 아니다 (정직한 한계)

RAG는 **'보장'이 아니라 '비발산 방지 + 인간 조기교정 보완'**이다. **의미 모순은 못 잡음을 정직 인정**한다. 결정론 검증(consistency_check)이 잡는 것은 충돌 4종 + 시점 일관성이고, 의미적 모순(서사 톤·인물 동기 일관성)은 critique judge(보조)와 작가 검토에 의존한다.

### 7.5 finalize 팬아웃 계약 (C5 / 아키텍처비평 A5 / D5)

```
finalize 이벤트 단독 발행
   ▼
워터마크 3종 전진 (last_consistent / last_indexed / state_applied)
   ▼
3중 컨슈머 (멱등키 + 부분실패 보상 트랜잭션)
   ├── ① RAG 색인       (가역)
   ├── ② 온톨로지 상태 반영 (비가역 ground truth)
   └── ③ 계층 요약 갱신   (가역)
   ▼
state_applied 는 모든 컨슈머 ack 후에만 전진 (함께 전진 보장 — 단조성만으로는 split-brain 못 막음)
```

> **개발 pushback 3 / D5 수용 — 후속 차단 강도 차등화:** 모든 컨슈머 동급 차단은 데이터 정합성은 정답이나 auto_advance 무인 진행 중 가역 컨슈머 하나(RAG 색인)가 새벽에 stuck되면 작품 전체가 통째 정지해 가용성을 과도 희생한다. 그러므로 **컨슈머별 stuck 임계 + 자동 보상 재시도 횟수 명시 + 차등 차단**:

| 컨슈머 | 성격 | stuck 시 후속 차단 강도 |
|---|---|---|
| ② 온톨로지 상태 반영 | **비가역 ground truth** | **즉시 후속 회차 강하게 차단**(as_of 못 전진) |
| ① RAG 색인 | 가역 | 재시도 예산 소진 전까지 **잠정 진행 허용** + trust_tier 격리 + 배지/알림 |
| ③ 계층 요약 갱신 | 가역 | ① 동일 |

모든 비정상은 §10.9 배지 + 알림으로 '왜 멈췄는지' 투명 노출.

---

## 8. RAG 기억 & 회차 연속성

### 8.1 단일 DB 통합 (아키텍처비평 A3)

pgvector(HNSW halfvec) + PG FTS + 온톨로지를 **단일 PostgreSQL 16**으로 통합. 재색인 단방향 루프가 **ACID 일관**(분산 트랜잭션 회피).

### 8.2 인덱싱

- **finalize된 회차만** 청킹·임베딩(pgvector HNSW). 미검토 auto_advance 회차도 finalize되면 색인되되 `trust_tier='unreviewed_machine'`+`provisional`로 격리(§8.5).
- 메타: `narrative_order`, `versionId`, `signal_grade`, `trust_tier`, `provisional`, `embedding_model_version`, `entity_refs`, `alias_refs`, `tsvector_ko`.
- 임베딩: BGE-M3 온프레미스. `embedding_model_version`으로 단일활성 마이그레이션 강제.

### 8.3 검색 & 주입

- **하이브리드:** Kiwi BM25 + 벡터 RRF + 엔티티/alias 사전필터(고유명사 회수 보강).
- **시점필터:** `as_of=last_indexed` 워터마크로 **미래 회차 누수 차단**.
- **주입:** 하단·슬롯 cap·trust_tier별 가중(§7.3). ontology와 혼합 불가(F7).

### 8.4 작가 편집 재색인 반영 (stale=0)

- 작가 직접 수정 = 새 ChapterVersion(source=human_edited). **단방향 재색인 루프(versionId·is_active 단일)**로 수정분 반영, stale=0.

### 8.5 자기섭취 차단 & 미검토 회차 격리 (C3 High — 신설)

- **자기섭취 차단:** quarantine·저신뢰·미검토(unreviewed_machine) 회차 `trust_weight` 하향. **style_anchor·few-shot은 동결 시드만**. **위력/전투강도는 RAG 재참조 금지(EndingSpec power 앵커만).**
- **미검토 회차 격리:** auto_advance 미검토 회차는 `trust_tier='unreviewed_machine'`+`provisional` 플래그로 격리해 **막경계 감사 통과 전까지 ground truth처럼 후속에 박히지 않게** 한다.

### 8.6 막경계 정산 (DelegationLevel 전환 소급 공백 방어)

위임 상향(scene_by_scene→beats_auto) 후 생긴 미검토 사각지대를 **막(Act)경계 정산**으로 회수한다. 정산 전까지 그 구간은 provisional로 격리.

### 8.7 계층 요약 (HierarchicalSummary)

chapter/arc/work 레벨 요약 + `irreversible_facts`(jsonb 무손실). finalize 팬아웃 3중 컨슈머 중 ③이 갱신. 장기 컨텍스트 압축.

### 8.8 온톨로지 결합

`ontology_lookup`(결정론 박기)의 원천. PostgreSQL 단일 트랜잭션 경계. **recall<1 결정론 오염 방어 = 비가역류 구조화 토글만**(자유 텍스트 추론 금지).

---

## 9. 기능 명세 (6 도메인 P0/P1/P2 표)

### 9.1 도메인1 — collab (협업 인터랙션)

| 기능 | P |
|---|---|
| 회차 직후 개입형 HITL(지점3/5/6/7) | P0 |
| EscalationBrief(결정론 증거 1차 + LLM 요약 보조) | P0 |
| AuthorDirective 레저 + 자기모순 회수 | P0 |
| 실시간 장면 경계 인터럽트 | P2(M2) |
| 적응형 자율등급 학습(explicit_dismiss/implicit_skip 분리) | P2 |

### 9.2 도메인2 — ragmem (RAG 기억)

| 기능 | P |
|---|---|
| RagIndexUnit 색인 + 하이브리드 회수 + as_of 시점필터 | P0 |
| 비대칭 주입(ontology 박기 vs rag 찾기) | P0 |
| 미검토 회차 trust_tier 격리(§8.5) | P0 |
| 단방향 재색인(작가 편집 반영, stale=0) | P0 |
| 계층 요약 | P1 |
| 30~50화 비발산 실증 | M3 |

### 9.3 도메인3 — worldgen (협업 세계관)

| 기능 | P |
|---|---|
| 시드 인테이크 + 부트스트랩 + propose-adjust-approve | M4 |
| 상호충돌 매트릭스 | M4 |
| EndingSpec 3계층 | M4 |
| 작가 자기모순 회수 UX(§11.3) | M4 |

### 9.4 도메인4 — quality (회차 품질·일관성)

| 기능 | P |
|---|---|
| consistency_check(충돌 4종 + EntityStateTimeline + 워터마크 게이트) | P0 |
| prose_lint(Kiwi/KSS) | P0 |
| critique 앙상블 N≥3 + degraded mode | P0 |
| 미검토 회차 안전 축 1슬롯 무조건 실행(§13.5) | P0 |
| critique 회차당 상수화 p95 튜닝 | P1 |
| payoff_check(false payoff 방어) | P1 |

### 9.5 도메인5 — structure (스토리 구조·진행)

| 기능 | P |
|---|---|
| ChapterSkeleton/Beat + scene_plan 결정론 분할 | P0 |
| DeterministicStructureChecker(networkx, LLM 배제) | P0 |
| §4.4 liveness L1/L2/L3 평가기 | P0(M3·M5 풀) |
| PlotThread 터치체인 | P1 |
| 풀아크 진행/엔딩 수렴 | M5 |

### 9.6 도메인6 — harness (에이전틱 하네스 & 회차 생성 루프) ★신규 6번째 도메인

| 기능 | P |
|---|---|
| 회차 에이전트 루프(plan→…→finalize) | **P0** |
| 도구셋 12종(신뢰등급 분리) | **P0** |
| 회차 내부 안정성 가드(MAX_ROUND·monotone·과교정·oscillation·MAX_INTERRUPT) | **P0** |
| 장면 경계 결정론 scene_plan(§6.7) | **P0** |
| 장면 경계 인터럽트(MVP=직후 개입) | **P0** |
| writer 권위 단일 소유 + lease 만료 | **P0** |
| finalize 단독 발행 + 팬아웃 계약 | **P0** |
| §4.4 liveness 계약 | **P0** |
| §10.8 무응답 직교 매트릭스 | **P0** |
| degraded mode + 에러 상태 가시성 | **P0** |
| judge 호출 회차당 상수화 | P1 |
| durable 의미적 복원 + 재확인 게이트 | P1 |
| DelegationLevel 슬라이더 | P1 |
| 에이전트 루프 재현성 분산 측정 | M1 |
| 실시간 장면 경계 인터럽트 | P2(M2) |
| 적응형 자율 등급 학습 | P2 |

---

## 10. 생성 중 인간 개입 동시성 설계

> "생성 중 작가 편집/인터럽트"의 동시성이 이 도메인에서 가장 깨지기 쉽다. **5대 원칙**으로 race를 구조적으로 제거한다.

### 10.1 원칙 1 — never interrupt a running tool (F4)

작가 '멈춰' 신호는 실행 중 `draft_scene` activity를 **죽이지 않는다.** Temporal SignalWorkflow가 in-flight activity를 강제 취소 못 하는 현실을 수용해, 신호는 **현재 장면 activity 완료 체크포인트(=장면 경계)에서만 흡수**한다. best-so-far 저장, **중간 절단 금지(split-brain 방지).** 토큰 단위 인터럽트는 폐기.

### 10.2 MVP 포지셔닝 경고 (개발 pushback 7 / D8)

실시간 인터럽트가 M2인데 MVP를 '실시간 협업'으로 포지셔닝하면 작가가 '배치 거부→재생성 룰렛'을 겪고 v1로 회귀한다 — **기술이 아니라 기대치 관리 실패로 제품이 죽는 경로.** MVP 마케팅 카피에서 '실시간 협업'을 **절대 사용하지 않는다.** MAX_INTERRUPT 초과 '직접 집필 or 3안 강제 선택' UX가 룰렛 차단 마지막 방어선이므로 MVP 필수.

### 10.3 원칙 2 — 본문 writer 정확히 한 시점 하나 (F3)

- `writer_lock_holder ∈ {agent, human, none}`, advisory lock 직렬화.
- **물리적 단일 소유자 = orchestration 도메인 1곳**(협업/memory/arc 각자 소유 주장 해소).
- 핸드오프 시퀀스: **agent → none → human → none → agent**.
- 인간 무응답 시 `writer_lock_lease_expiry` 만료로 **자동 회수**(작업물 보존, agent reacquire) — advisory lock 영구 human 점유 방지(§10.8).

### 10.4 원칙 3 — append-only ChapterVersion (F3)

- supersedes 체인 + is_active 단일(DB unique 제약) → **lost update 구조적 불가.**
- 작가 직접 편집 = 새 ChapterVersion(source=human_edited), 에이전트는 '인간이 만든 새 active 버전' 위에서 재계획.
- **CRDT 본문 미채택**(CRDT는 캐논 옳고그름 판정 못 함, 단일 writer 계약과 충돌). 플래그/검토 메타만 낙관적 락(version CAS, 별개 row).

### 10.5 durable 복원 race — 의미적 손실 (F5)

Temporal replay는 **구조 재현이지 LLM 동일출력 아님(temperature>0).** 작가 노출 부분 = **즉시 멱등 체크포인트(작가가 본 순간=커밋 경계)**, 안 보인 in-flight만 재실행. 재개 시 **'마지막 본 장면 재생성됨' 명시 고지 + 재확인 게이트.** 작가 Signal은 ack 기반 durable 기록(누락·중복 방지).

### 10.6 다중 세션 race

같은 작가 여러 디바이스/탭 = human vs human 충돌. advisory lock 키에 `session_id` 차원 추가, 후속 세션 read-only 또는 명시 takeover.

### 10.7 인터럽트 → resume 시퀀스

```
작가 '멈춰' → control endpoint → Temporal SignalWorkflow(PAUSED)
   → 현재 draft_scene activity 완료 대기(best-so-far 저장)
   → writer 핸드오프 agent → none → human (lease 부여)
   → 인간 지시 Observe 흡수 or 직접 편집
   → detect_directive_conflict (충돌/HARD 침범 시 request_human_input + 과거 지시 출처 회수)
   → writer human → none → agent reacquire (lease 만료 또는 명시 반납)
   → 재계획 → 재개
```

### 10.8 §10.8 무응답 단일 종료조건 직교 매트릭스 (C2 Blocker / F6 — 신설)

세 경로(`request_human_input` · interrupt · 골격락)의 무응답 정책을 **단일 상태머신 표**로 통합.

| severity | 가역성 | 무응답 타임아웃 경과 시 행동 |
|---|---|---|
| 저 | 가역 | **잠정 진행**(provisional 격리) |
| 중 | 가역 | 잠정 진행 (배지 노출) |
| 고 | 비가역 / HARD / 정체 / 결정론 critical | **동결(frozen)** |
| **HARD invariant 침범 후보** | 비가역 | **영구 동결 (잠정 진행 절대 금지)** |

**단일 규칙:** 가역·저severity = 잠정진행 / 비가역·HARD·정체·결정론critical = 동결 / **HARD 침범 후보 = 영구 동결(잠정진행 절대 금지).** interrupt 중 writer 점유 = lease 만료 자동 회수.

> **F6 방어:** 잠정진행 vs 동결 vs HARD차단이 모드별로 충돌해 HARD 침범 후보를 잠정진행으로 보내면 보호가 붕괴한다. 직교 매트릭스(severity별 단일 행동)로 모순 제거. 무응답 타임아웃은 §4.4 D4와 동일하게 **'작가 활동 신호 부재 구간'**으로 정의.

### 10.9 조용한 정지 금지 (degraded/에러 상태 가시성 — hard rule)

모든 비정상 상태(degraded / STALLED / frozen / 무응답대기)는 **시스템 상태 배지(정상/저하/정체/동결/무응답대기) 상시 노출 + 진입 즉시 알림 + 사유 + 권장 행동.** degraded mode: judge N≥3 중 1개 장애=N=2 degraded 플래그 진행, 2개+=회차 PAUSED + 작가 통지. 무LLM 결정론 게이트는 무영향. **조용한 정지 금지 = hard rule.**

### 10.10 골격 그래프 동시성

공유 가변 트리. 노드별 advisory lock + `node_version` CAS + 작가 편집 우선권. major 재계획 시 영향 range = 단일 트랜잭션 경계 freeze(부분 freeze 금지).

---

## 11. 데이터 모델 & 온톨로지

### 11.1 v2 계승

- **Entity (6종) + EntityStateTimeline:** 인물/장소/아이템/세력/사건/규칙 + 상태 타임라인 결정론 ground truth. PostgreSQL 단일 트랜잭션 경계.

### 11.2 v3 신규 — 회차 루프 / 협업 / liveness

| 엔티티 | 핵심 필드 |
|---|---|
| `ChapterVersion` | versionId, narrative_order, source(agent_generated/human_edited), supersedes_version_id, is_active(unique), trust_tier(reviewed/unreviewed_machine/quarantined), writer_lock_holder, writer_lock_lease_expiry |
| `scene_plan` | 결정론 장면 분할(§6.7) — 비트→장면 고정 리스트, 인터럽트/체크포인트 단위 |
| `Beat/ChapterSkeleton` | goal, key_events, start/end_state, cliffhanger, required_facts, review_mode, node_version(CAS) |
| `LivenessState` | work_run_id, progress_scalar, delta_window(K), stall_state(active/STALLED_AWAITING_AUTHOR/frozen), defect_debt_total, abs_cap, consecutive_unreviewed_count |
| `DefectDebtEntry` | unresolved_flags, severity, debt_kind, settle_at_act_boundary, counts_toward_abs_cap |
| `Watermark 3종` | last_consistent / last_indexed / state_applied_narrative_order, 단조 증가 체크제약, state_applied=모든 컨슈머 ack 후 전진 |
| `AuthorDirective` | kind, issued_at_narrative_order, scope, priority, expires_at, is_retroactive, target_invariant_layer, conflict_status, conflict_with_directive_id, conflict_origin, superseded_by |
| `AuthorDirectiveLedger` | active/expired/conflicting ids, last_propagated_narrative_order |
| `EscalationBrief` | root_cause_candidates, impacted_narrative_range, canon_diff, conflicting_past_directive_ref, recommended_options[3] |
| `InterventionEvent` | point(seed/worldgen/beat/in_gen/post_gen/redirect/escalation/liveness_stall), human_seconds_spent, decision_type(explicit_dismiss/implicit_skip), conflict_origin |
| `VerdictRecord` | gate_result, deterministic_critical_count, judge_axis_scores(안전축 포함), attempt_count, config_matrix_id |
| `RagIndexUnit≡ContentChunk` | chunk_type, embedding, embedding_model_version, signal_grade, trust_tier, provisional, trust_weight, is_active, entity_refs, alias_refs, tsvector_ko |
| `CanonStagingRecord` | extracted_fact, signal_grade, conflict_status, commit_state, judge_votes, requires_author_confirm, intent_or_error |
| `DelegationLevel/AutonomyMode` | default_level(beats_auto/review_then_proceed/scene_by_scene), high_autonomy_mode, per_chapter_overrides |
| `ReviewSession/AuthorCognitiveState` | last_reviewed_narrative_order, unresolved_directive_ids, last_streamed_scene_pos, durable_restore_key, system_state_badge |
| `ReproducibilityRecord` | seed, run_index, completion_outcome, partial_rewrite_round_count, output_divergence |
| `ProvenanceEdge` | dependency_kind(skeleton_derives/canon_derivation/replan_impact) |
| 상수 | oscillation_signature(해시 EWMA), MAX_ROUND/MAX_INTERRUPT 하드캡, K=15·ABS_CAP·MAX_CONSECUTIVE_UNREVIEWED·장면 크기 상하한(M1 동결 config) |

### 11.3 작가 자기모순 회수 UX (worldgen M4 연계)

`conflict_with_directive_id` 과거 지시 출처 회수 + `conflict_origin`(변심→채택+superseded_by / 전파오류→채택 보류+경로 정정) 분리 처리. EndingSpec 3계층(power 앵커 포함)과 결합.

---

## 12. 시스템 아키텍처 & 기술 스택

### 12.1 권장 스택

| 레이어 | 기술 | 메모 |
|---|---|---|
| 에이전트 루프 | **LangGraph StateGraph + checkpointer** | 단 Temporal activity 내부 단명 실행, durable 권위 아님 |
| durable 실행 | **Temporal** | 크래시 복원 권위 단일. child=회차, Signal=인터럽트, Update=동기 승인, continue-as-new, 부모=§4.4 평가 |
| 권위 SSOT | **PostgreSQL 16 + jsonb + 재귀 CTE** | EntityStateTimeline·워터마크·directive·CanonStaging·LivenessState 단일 트랜잭션 |
| 벡터/검색 | **pgvector 0.7+ (HNSW halfvec) + PG FTS** | 단일 DB 통합 → 재색인 단방향 루프 ACID |
| 한국어 FTS | **Kiwi 주력 + KSS + alias 사용자사전** | 고유명사 회수 보강 |
| 임베딩 | **BGE-M3 온프레미스** | embedding_model_version 단일활성 마이그레이션 |
| 구조 검증 | **순수 Python(networkx)** | DeterministicStructureChecker, LLM 완전 배제 |
| LLM/judge | **최강 모델 + LiteLLM 라우팅** | judge 다른 family N≥3, generator≠judge |
| 실시간 | **WebSocket/SSE + Redis pub/sub** | 토큰 스트리밍(단방향) ↔ 인터럽트 제어채널 분리 + 배지 푸시 |
| 구조화 출력 | **Pydantic v2 + tool-calling** | signal_grade enum 고정 |
| 관측 | **OTel + Langfuse(단일 trace_id↔workflow_id) + Temporal Web UI** | 루프 진동·마찰·liveness 정체·재현성 분산 추적 |

### 12.2 durable 권위 단일 (아키텍처비평 A1/A2)

- 크래시 복원 권위 = **Temporal 단일.** LangGraph checkpointer 이중화 금지.
- 인터럽트 의미론도 **Temporal에 단일 귀속**(MVP에서 LangGraph interrupt 미사용, §6.6).

### 12.3 단일 DB 통합 (A3)

pgvector + PG FTS + 온톨로지 단일 PostgreSQL. 분산 트랜잭션 회피, 재색인 단방향 ACID.

### 12.4 계약 동결 산출물 (통합공수 폭발 R13 방어 — 코딩 전 실제 스키마로 동결)

1. **writer_lock 단일 소유자** = orchestration 1곳 (§10.3)
2. **4-홉 핸드오프 시퀀스** agent→none→human→none→agent (§10.3)
3. **finalize 팬아웃 계약** state_applied=모든 ack 후 전진 + 컨슈머 성격별 차등 차단 (§7.5)
4. **§10.8 무응답 직교 매트릭스** 상태머신 표 (§10.8)
5. **§4.4 liveness 계약** L1/L2/L3 + 상수 config (§4.4)
6. **§8.5 trust 격리** 미검토 회차 trust_tier 스키마 (§8.5)

---

## 13. 회차 일관성·품질 전략 (인간 협업 전제로 우경량화, 작가 surfacing)

### 13.1 효율 vs 일관성 균형 (P2)

검증 비용을 작가에게 다 전가하면 마찰 폭발. 결정론 검증(매 장면)은 무LLM이라 싸고, 의미 judge는 회차당 상수화(§13.3)로 묶고, 작가 surface는 §6.6 우선순위로 하드캡.

### 13.2 결정론 게이트 (매 장면)

`consistency_check`(충돌 4종 + EntityStateTimeline 시점 + 워터마크 게이트) + `prose_lint`(Kiwi/KSS 종결어미 반복·n-gram·금칙어). **LLM 완전 배제** → 결정론 보장이 코드·DB 제약으로 성립.

### 13.3 judge 회차당 상수화 (C6 / P1)

의미 judge(critique)는 **finalize 직전 1회 + 결정론 critical 발생 장면만** 호출 → judge 호출을 **장면 수 선형이 아닌 회차당 상수**로 묶어 p95 지연 폭발 방지.

### 13.4 외부 타당성 1회 객관 닻 (아키텍처비평 Blocker 잔존)

작가 본인 승인이 최종 게이트지만, judge recall·작가 편향 보정용으로 **익명 1회 객관 닻**은 잔존(KILL 게이트 아님).

### 13.5 미검토 회차 안전 축 1슬롯 무조건 실행 (C7 / 개발 pushback 5 / D7 / BS1)

> auto_advance 미검토 회차는 작가도 안 보고 critique도 안 도는 **이중 사각지대**. trust 격리는 RAG 전파만 막을 뿐 그 회차 자체의 안전 품질은 아무도 안 본다.

- 미검토 회차에 **critique 앙상블 전체는 비용상 생략**하되, **'안전 축 1슬롯'만은 무인 구간에서도 무조건 실행**.
- 의미 품질 축(서사/문체)은 생략하지만 **폭력·혐오·안전 누적 드리프트 탐지는 절대 끄지 않는다.**
- 안전 축마저 끄면 미검토 회차의 안전 책임 귀속(§3.6 법적 모순)이 기술적으로 무방비가 된다.

### 13.6 소유감(정체성) 측정 (P1 → 기획비평 P1 연계)

회차당 능동 결정 수, 형식 통과율(읽지 않고 승인 비율, 임계 이상이면 책임 떠넘기기 신호)을 추적해 v1 회귀를 조기 탐지.

---

## 14. 핵심 의사결정 로그

| ID | 결정 | 근거(비평 연계) |
|---|---|---|
| **D1** | best-so-far monotone 1급 축 = **결정론 위반 카운트**(의미 점수는 보조축). fallback 순서(결정론 강등→temperature 낮춤→로직 재설계) 데이터 전 동결 | A4 / 개발 1순위 pushback. temperature>0 분산이 기준선 흔드는 P0 최대 리스크 봉인 |
| **D2** | MVP에서 **LangGraph interrupt 미사용.** 인터럽트 100% Temporal Signal+장면 경계 폴링 단일 귀속 | A1 / 개발 pushback 4. 2026 통합 실험단계 단일 장애점 제거 |
| **D3** | §4.4 liveness 상수 = **보수적 초기값 + 재조정 config.** L3 무응답 타임아웃 = wall-clock 대신 '작가 활동 신호 부재 구간' | C1 / 개발 pushback 2 / P3. false positive vs 데드락 늦은 탐지 비대칭 완화 |
| **D4** | L3 타임아웃 '작가 활동 신호 부재 구간' 정의 | C1 / openQuestion 4 |
| **D5** | finalize 팬아웃 후속 차단 **비가역(온톨로지)/가역(RAG 색인) 차등** | A5 / C5 / 개발 pushback 3. 정합성 유지하며 가용성 회복 |
| **D6** | EscalationBrief = **결정론 증거 1차 표면 + LLM 요약 보조 레이어** 분리 | C7 / 개발 pushback 6. 보조 LLM 오도 구조적 차단 |
| **D7** | 미검토 회차 **안전 축 1슬롯 무조건 실행** | BS1 / 개발 pushback 5. 이중 사각지대·법적 모순 방어 |
| **D8** | MVP = **'회차 검토형(post-generation) 코파일럿'**으로 좁힘. '실시간 협업' 카피 금지. 단 동시성 골격은 미리 박음 | 개발 pushback 7. 기대치 관리 실패로 인한 v1 회귀 방어 |

---

## 15. 리스크 & 완화책

| # | 리스크 | severity | 완화책 | 잔존 |
|---|---|---|---|---|
| R1 | best-so-far monotone이 temperature>0 위에 서 있어 판정 자체가 흔들림 | **High** | D1(결정론 1급 축) + M1 재현성 측정(n≥10) + fallback 순서 동결 | N=10이 monotone 안정성 통계 검정력 주는지 불확실(openQuestion 11) |
| R2 | §4.4 liveness 상수 보수적 초기값이 false positive(작가 과호출) vs 데드락 늦은 탐지 균형 못 맞춤 | **High** | D3(config화 + 보수적 초기값) | 운영 데이터로 재조정 필요, 첫 작가가 비용 부담 |
| R3 | L3 무응답 타임아웃, 정상 자리비움 vs 정체 경계 작가마다 다름 | Medium | D4('활동 신호 부재 구간') | 절대 시간 단위 근거 여전히 약함 |
| R4 | LangGraph×Temporal 의미론 충돌(M2 실시간 인터럽트 확장 시 재부상) | **High** | D2(MVP 단일 귀속) | MVP 동시성 골격 M2 재사용 가능성은 chaos 테스트 전 미검증 |
| R5 | finalize 팬아웃 컨슈머 차등 차단의 비가역/가역 오분류 | Medium | §12.4 계약 동결 시 컨슈머 성격 명시 스키마 | 오분류 시 정합성/가용성 트레이드오프 붕괴 |
| R6 | 미검토 회차 서사/의미 품질 사각지대 | Medium | D7(안전 축 1슬롯) | 서사/의미 저하는 아무도 안 봄, 신뢰 라벨러 부재로 1급 게이트 불가(정성 강등) |
| R7 | 세 최소선 비양립 분기((a)페르소나 A vs (b)소유감 포기)가 제품 정체성 변경 | **High** | §16.0 M1 GO 게이트 조건부 | M1 협력 작가 표본 한 자릿수면 분기 판정이 노이즈에 묻힘 |
| R8 | auto_advance 책임 귀속 법적 모순(§3.6) | **High** | 안전 축 1슬롯 + 변호사 자문 M1 선결 | 기술 설계로 완전히 닫히지 않음 |
| R9 | 계층B 보조 LLM 오도 | Medium | D6(결정론 증거 1차) | 작가가 요약만 읽고 raw 증거 안 보면 잔존 |
| R10 | judge 벤더 장애로 critique 마비 | Medium | 타임아웃+서킷브레이커, N=2 degraded, 2개+ PAUSED | 무LLM 게이트는 무영향 |
| R13 | §12.4 계약 미동결 시 통합공수 1.5~2배 폭발 | **High** | 6개 계약 코딩 전 실제 스키마 동결 | — |

---

## 16. 마일스톤 로드맵

### 16.0 M1 GO 게이트 (코딩 착수 조건부 — C4 Blocker)

> **P0 경계 최종 확정 = §16.0 M1 게이트의 세 최소선(정체성 §3.5 · 저작물성 §3.6 · 인지부하 §4.2) 양립 측정 결과에 조건부.**

- **세 최소선 양립 측정.** 비양립 시 코딩 전 택일:
  - **(a)** 타깃을 페르소나 A로 좁히고 auto_advance 강등, 또는
  - **(b)** 소유감 비목표 포기.
- **4개 Blocker/High 산출물 실제 스키마/상태머신 동결:** §4.4 liveness 계약 · §10.8 매트릭스 · §8.5 trust 격리 · §16.0 분기 결론.
- **저작권 변호사 자문(선결).**

### 16.1 M1 — 선결 스파이크 (코딩 전, 2~3주)

- 에이전트 루프 재현성 분산 측정(같은 시드 **n≥10** 재실행 완주율·partial_rewrite 라운드 분산·output_divergence → ReproducibilityRecord).
- 장면 경계 크기 임계 측정(인터럽트 반응성 p95 vs judge 비용 트레이드오프).
- 회차당 LLM 호출 수·토큰 실측.
- K·ABS_CAP·MAX_ROUND·MAX_INTERRUPT·장면 크기 상하한 **보수적 동결 근거** 수립.

### 16.2 M1 fallback 순서 동결 (D1)

output_divergence가 사전 동결 임계 초과 시: **(1) monotone을 결정론 축 단독으로 강등 → (2) draft_scene temperature 낮춤 → (3) 최후에 monotone 로직 재설계.**

### 16.3 마일스톤 단계

| M | 범위 | 본 도메인(harness) 기여 |
|---|---|---|
| **M1** | 선결 스파이크 + GO 게이트 | 재현성 분산·장면 임계·상수 동결 (2~3주) |
| **M2** | **단일 회차 코파일럿 루프** (본체) | LangGraph×Temporal 2계층 골격(3~4주, +1주 버퍼) + 도구셋 12종(2주) + 안정성 가드(2주) + scene_plan+review surface+EscalationBrief(1.5주) + §10.8/§10.9/동시성 골격(2주) + degraded(0.5주) |
| **M3** | 연속 회차 RAG 루프 (30~50화 비발산 실증) | finalize 팬아웃 saga + 부모 workflow §4.4 평가기 일부 (3~4주, M5 분담) |
| **M4** | 협업 worldgen (시드→부트스트랩→propose-adjust-approve→상호충돌→EndingSpec→자기모순 회수 UX) | — |
| **M5** | 풀아크 진행 / 엔딩 수렴 | §4.4 L1/L2/L3 풀 평가기 |
| **M6+** | 실시간 인터럽트, 표절 근접도, 어뷰징 방어, 3인 협업, 회귀물(timeline_branch_id) | 실시간 장면 경계 인터럽트 |

**본 도메인 순 구현:** 약 **11~15주**(M1 측정 제외). M1 스파이크 포함 **13~18주.** 최대 일정 리스크 = (1) LangGraph×Temporal interrupt 어댑터 안정성(chaos 테스트 누락/중복 시 +2주 — D2로 어댑터 자체 제거해 완화), (2) §12.4 계약 미동결 시 통합공수 1.5~2배(R13).

### 16.4 M1 표본 검정력 (BS2)

N=10이 monotone 판정 안정성·세 최소선 양립을 통계적으로 판정할 검정력을 주는지 불확실. **'정량 판정→정성 신호' 강등 규칙과 최소 N을 데이터 보기 전 동결**한다(§16 의사결정 가능 최소 N과 정합).

---

## 17. 성공 지표 (협업효율·일관성·품질·작가만족 — 수익성 제외)

| 범주 | 지표 | 목표/판정 |
|---|---|---|
| **루프 완주** | 회차 1편 루프 완주율(같은 시드 n≥10, ReproducibilityRecord) | M1 선결 측정 |
| **수렴 안정성** | partial_rewrite 라운드 분포 + best-so-far monotone 판정 안정성(output_divergence) | 재실행 간 안 흔들림 |
| **비용=제약** | 회차당/작품당 LLM 호출 수 + 스케일 곡선 | 지연·레이트리밋·장애전파 제약으로 1급 |
| **반응성** | 장면 완료 p95 + TTFT + 인터럽트 반응성 p95('멈춰' 후 장면 경계 정지까지) | 장면 크기 상한 트레이드오프 검증 |
| **judge 상수성** | critique 호출이 장면 수 선형 아닌 회차당 상수 유지 | — |
| **결정론 잔존** | consistency_check critical이 max_attempts 자동 재생성 후 작가 surface로 남는 비율 | 낮을수록 좋음 |
| **liveness 적정성** | L1/L2 강제 surface 발동 빈도 + 실제 정체 포착 여부(false positive로 작가 과호출 안 하는지) | — |
| **복원 정합** | STALLED→동결→복귀 시 작업물·세션 durable 100% 복원(구조 손실 0) + 의미적 손실 투명 고지율 | — |
| **lease 회수** | 무응답 시 lease 만료 자동 회수가 작업물 보존하며 split-brain 0건 | 0건 |
| **팬아웃** | 워터마크 3종 desync 0건, state_applied 모든 ack 후 전진, 컨슈머 stuck 시 후속 차단 동작 | desync 0 |
| **인지부하** | interaction friction(회차당 의사결정 총량) ≤ 하드캡 N + 형식 통과율(읽지 않고 승인 비율) | 임계 이상=책임 떠넘기기 신호 |
| **정체성** | 회차당 능동 결정 수(§3.5 최소선) | ≥1 |
| **가시성** | 비정상 상태 진입 시 작가 알림 도달율 | 100%, 조용한 정지 0건 |

---

## 18. 열린 질문

1. **장면 크기 상·하한 임계값:** 인터럽트 반응성(짧게)과 judge 비용 모델 보호(길게)의 균형점을 M1에서 어떻게 동결하는가? (회차당 장면 수 상한·p95와 함께)
2. **MAX_ROUND·MAX_INTERRUPT 구체 상수:** 3~4가 적정한지, 장르/회차 길이별로 달라야 하는지 M1 실측 필요.
3. **K=15·ABS_CAP·MAX_CONSECUTIVE_UNREVIEWED 동결값:** 데이터 보기 전 동결하기로 했으나 초기 추정 근거는? 너무 크면 데드락 늦게 잡고 너무 작으면 작가 과호출.
4. **§4.4 L3 무응답 타임아웃 절대 시간 단위:** 유인 모드에서 작가의 정상적 자리비움(수일)과 진짜 정체를 어떻게 구분하는가? '활동 신호 부재 구간'으로 정의해도 패턴마다 경계가 다름.
5. **temperature>0 비결정성이 monotone 판정을 흔들 경우 fallback:** D1 순서(결정론 강등→temperature 낮춤→로직 재설계)로 닫았으나, 분산이 임계 초과면 실제 어느 단계에서 멈추는지는 M1 데이터 의존.
6. **auto_advance 미검토 회차 critique:** 안전 축 1슬롯은 D7로 확정. 서사/문체 의미 품질 사각지대는 신뢰 라벨러 부재로 1급 게이트 불가 — 정성 관찰 강등 여부?
7. **장면 경계 즉시 폐기 정지 옵션의 best-so-far 보존 정책:** 폐기 장면 부분 산출물을 supersedes 체인에 남기는가 버리는가?
8. **LangGraph×Temporal interrupt 어댑터(2026 실험단계):** MVP에서 D2로 제거했으나 M2 실시간 인터럽트 확장 시 재부상 — chaos 테스트에서 interrupt 누락/중복 대응?
9. **DelegationLevel 전환 소급 공백:** 위임 상향 후 미검토 사각지대를 막경계 정산으로만 회수하면 충분한가?
10. **계층B 보조 LLM 오도:** D6(결정론 증거 1차)으로 닫았으나, 작가가 raw 증거를 실제로 보는지는 UX 행동 측정 의존 — 별도 검증 게이트 필요?
11. **M1 재현성 측정 표본 N=10 검정력:** monotone 판정 안정성·세 최소선 양립을 통계적으로 판정할 검정력을 주는가? (§16.4 최소 N·강등 규칙 사전 동결과 정합)
12. **세 최소선 비양립 분기((a)페르소나 A 좁힘 vs (b)소유감 포기):** 제품 정체성을 바꾸는 결정인데 M1 협력 작가 표본이 한 자릿수면 분기 판정 자체가 노이즈에 묻힘 — 어떻게 신뢰도를 확보하는가?