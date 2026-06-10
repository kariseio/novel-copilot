# AI 기반 웹소설 창작 지원 툴 — 제품 요구사항 명세서(PRD)

문서 버전: v1.0 (개발 착수용) · 작성: 리드 PM 겸 테크리드 · 기준일: 2026-06-04

---

## 0. 이 문서의 사용법과 비평 반영 요약

본 PRD는 4개 도메인(온톨로지/RAG/스토리구조/워크플로) 회의 산출물과 두 비평(planning-critic, code-critic)을 종합한 **개발 착수 가능 수준**의 명세다. 비평이 지적한 다음 사항을 본문 전반에 반영했다.

| 비평 출처 | 핵심 지적 | 본 PRD 반영 위치 |
|---|---|---|
| planning-critic (critical) | 검증할 작가/원고 조달이 미정 | §4.3 검증 가능성 스파이크(코딩 前 게이트), §12 M0 |
| planning-critic (critical) | 수익 모델·단위경제 부재 | §3.4, §13 지불의사·변동비 지표 |
| planning-critic (critical) | 가설 합격선/kill criteria 없음 | §13.1 단일 합격선 & 폐기 기준 |
| planning-critic (high) | 페르소나 미스핏(신인 사실상 제외) | §2, §4.1 검증 대상 페르소나 1인 고정 |
| code-critic (Blocker) | Chapter 소유권 split-brain | §7.1 단일 writer 계약, §8.3 |
| code-critic (Blocker) | 확정 이벤트 3중 컨슈머 부분실패 | §8.4 워터마크·보상 설계 |
| code-critic (Blocker) | 가설 증명 책임 공백, A/B 단위 불일치 | §9.5 단일 A/B 프로토콜·단일 측정 단위 |
| code-critic (High) | 콜드스타트 시나리오 도메인별 충돌 | §4.4, §5 통일 검증 시나리오 |
| code-critic (High) | 시점 키(chapter_id/No/seq) 불일치 | §7.5 narrative_order 단일 정렬 키 |
| code-critic (High) | end-to-end 지연 예산 미합산 | §8.6 지연 예산 |
| code-critic (blindspot) | 외부 LLM 원고 전송 동의/DPA/파기 | §4.5 데이터 거버넌스, §11 |

---

## 1. 개요 & 비전

### 1.1 한 줄 정의
웹소설 작가의 **설정 일관성**과 **연재 속도** 부담을 덜어주는, 작가 주도형 AI 창작 지원 툴. 작가를 대체하는 자동 집필기가 아니라 **설정 관리자·구조 설계 비서·장기 기억 보조 장치**다.

### 1.2 비전
작가가 머릿속에만 있던 세계관·인물·사건을 온톨로지로 구조화해 한 번 정한 설정이 작품 전체에서 어긋나지 않게 지키고(RAG 기반 일관성), 막·장 단위 골격을 자동 생성해 백지 공포 없이 초안을 확장하도록 돕는다. **창작의 통제권은 항상 작가에게 남긴다.**

### 1.3 핵심 기술 3축 + 1
1. **온톨로지 기반 세계관(SSOT)**: 인물·배경·사건·설정의 관계를 구조화하고 결정론적 충돌을 감지.
2. **RAG 기반 콘텐츠 재활용(장기 기억)**: 확정된 회차·설정·대사를 인덱싱해 생성 시 자동 참조.
3. **스토리 구조 자동 생성(top-down)**: 막·장 단위 골격을 먼저 세우고 회차를 그 안에서 채움.
4. **작가 워크플로 & 에디터 통합(그릇)**: 위 셋을 하나의 작업공간에서 묶고, 작가 통제권·측정·원고 내구성을 책임.

### 1.4 가장 중요한 진실 (비평 반영)
- 이 제품의 단 하나의 가설은 **"온톨로지+RAG 주입으로 AI 생성물의 설정 일관성이 일반 LLM 대비 작가가 체감할 만큼 높아져, 작가가 처음부터 다시 쓰지 않고 '다듬어 쓸' 가치를 느낀다"** 이다.
- 이 가설은 **RAG+온톨로지 두 도메인이 공동 책임**진다. 스토리구조·에디터는 "측정 그릇·통제권 보존"을 담당한다.
- **"주입 ≠ 준수"**: RAG가 완벽히 설정을 주입해도 LLM이 어길 수 있다. 따라서 §9.5의 준수율 측정과 §9.4의 후처리 검증이 가설의 본체다.

---

## 2. 타겟 사용자 & 페르소나

| # | 페르소나 | 핵심 니즈 | 핵심 페인 | MVP 검증 대상 |
|---|---|---|---|---|
| P1 | 연재 마감에 쫓기는 기성 작가 (주 5~7회, 회당 5,500자) | 직전 회차와의 톤·설정·복선 연속성, 빠른 초안 골격 | 마감 압박으로 설정 충돌이 댓글로 지적됨. 수백 회차 재확인 시간 없음 | **✅ 1차 검증 대상 (고정)** |
| P2 | 데뷔 준비 신인 작가 (첫 장편) | 장편 구조 설계 가이드, 회차 끊기 감각 | 중반 늘어짐, 떡밥 회수 실패, 피드백 부재 | ⚠️ **MVP 검증에서 명시적 제외** (콜드스타트로 RAG 무력) |
| P3 | 설정 복잡 판타지·이세계물 작가 | 관계 구조화·시각화, 모순 자동 감지, 역추적 | 설정집 분산(엑셀/노션/메모장), SSOT 부재 | ✅ 보조 검증 대상 |

### 2.1 검증 대상 페르소나 단일화 (planning-critic 반영)
- 콜드스타트 구조상 신인(P2)은 RAG 주입 컨텍스트가 빈약해 MVP에서 가치 증명이 불가하다. **MVP는 P1(기성 작가)을 1차 검증 대상으로 고정**하고, P3는 보조로 둔다.
- **신인 가치 + 스토리구조의 "중반 붕괴 방지"는 MVP 가설에서 제외하고, 검증 후 별도 가설로 분리**한다. differentiators의 신인 관련 문구는 MVP 광고에서 톤다운한다.
- **진짜 경쟁자는 "아무것도 안 바꾸기"**(굳은 한글+메모장 워크플로)와 "ChatGPT에 매번 설정 붙여넣기"다. §3.4·§14에서 전환 트리거를 인터뷰로 검증한다.

---

## 3. 문제 정의 & 차별점

### 3.1 문제
1. 마감 압박으로 인물 속성(눈 색·과거사·능력 수치) 충돌이 발생하고 댓글로 지적된다.
2. 수백 회차가 쌓이면 과거 회차 재확인 시간이 없다.
3. 일반 LLM은 분량은 채우지만 "내 작품의 설정·문체"를 기억하지 못해 결국 다 고쳐 써야 한다.
4. 설정집이 엑셀·노션·메모장에 분산되어 단일 진실 공급원(SSOT)이 없다.

### 3.2 차별점

| 차별점 | 일반 LLM / 기존 툴 | 본 제품 |
|---|---|---|
| 설정 SSOT + 일관성 검증 | 대화 길어지면 설정 잊음 / Scrivener는 저장만, 추론 못함 | 온톨로지로 관계 구조화 + 결정론적 모순 자동 감지 |
| 장기 기억(RAG) | 컨텍스트 윈도 벗어난 과거 회차 망각, 매번 재붙여넣기 | 확정 회차·설정·대사 인덱싱, 수백 회차 자동 참조 |
| 구조 우선 설계 | 프롬프트당 단발성 본문 | 막·장 골격 먼저 → 회차를 그 안에서 채움 |
| 작가 통제권 | 대필 자동화 | 골격 제안 → 작가 확정 → 확정분 누적의 인간 주도 루프 |
| 웹소설 도메인 특화 | 범용 글쓰기 | 회차(분량·훅·끊기)·연재·떡밥 추적을 1급 개념으로 |

### 3.3 비목표 (Non-Goals)
완전 자동 집필 / 멀티 플랫폼 자동 발행·정산 연동 / 다인 협업(공동집필·실시간 동시편집) / 이미지·표지·웹툰화 / 독자 반응·연독률 분석 대시보드 / 다국어·모바일 전용 앱 / 다장르 무제한 / 음성·TTS.

### 3.4 수익 모델 가설 (planning-critic critical 반영)
- 본 제품은 **토큰 변동비가 사용량에 정비례하는 LLM SaaS**다. 단위경제는 출시 후 과제가 아니라 **출시 전 생존 조건**이다.
- MVP 가설에 **"지불 의사(WTP)" 축을 추가**한다. 페르소나 인터뷰에서 "이 초안 품질이면 월 얼마까지 내겠는가"를 회차 작업마다 측정한다.
- **작가 1인당 월 LLM 변동비(생성+요약+임베딩+재인덱싱)를 P0 기간 실측**해 단위경제 손익분기 구독가를 산출한다.
- 산출 구독가가 P1 작가 다수의 WTP를 넘으면 **페르소나를 "상위 연재 작가"로 좁혀야 하며, 이는 페르소나 정의 재작성을 의미**한다(§14 후속 결정).

---

## 4. MVP 범위 (In / Out)

### 4.1 MVP 범위 원칙
**단일 기준: "이 항목이 일관성 가설(+지불의사) 검증에 필수인가?"** planning-critic의 "범위 과욕" 지적을 반영해 P0를 가설 검증 최소집합으로 재절단한다. 신뢰성 엔지니어링(아웃박스/멱등 등)은 파일럿 5명 규모에서 **부분적으로 수동 운영으로 대체 가능한지 따져** P0 부담을 줄인다.

### 4.2 In / Out 요약

| 구분 | In (P0) | Out (P1 이후 / 기각) |
|---|---|---|
| 온톨로지 | 6종 엔티티 에디터(핵심필드 ≤5) / 정형 관계 / 결정론 충돌 4종 / StructuredContextSnapshot / 시점데이터(EntityStateTimeline, 판정레벨) / 임포트 한정 자동추출 | 그래프 시각화, LLM 의미충돌 감지, 스키마 커스터마이즈, 변경이력 풀 UI |
| RAG | 청킹·임베딩·인덱싱 / 컨텍스트빌더 / 참조칩 표시·핀/제외 / 확정→재인덱싱 / 기성작품 벌크 임포트 / dialogue few-shot / 평가하니스·골든셋 | GraphRAG 확장, 말투 자동프로파일, 인용 하이라이트(ProvenanceLink), 전용 검색뷰, 재랭커 |
| 스토리구조 | 막 골격 생성(점진) / 회차 비트 분해 / 편집·재정렬·확정 / 핸드오프 / 결정론 점검만 / 수동 떡밥 태깅 / 재생성 안전장치 | LLM 의미모순 감지, 떡밥 자동회수 감지, 자동 재동기화, 페이싱 곡선, 다장르 |
| 워크플로 | 에디터+origin / 골격→장면순차 생성 / 확정→가역 재색인 / **원고 손실 방지** / 계측(GenerationEvent) / 워크스페이스 트리(최소) | 설정참조 핀/끌어쓰기, 진척보드, 버전복원 UI, 연속동선, 인라인 어시스트, export, 5,500자 단발생성(기각) |

### 4.3 코딩 착수 전 게이트 — "검증 가능성 스파이크" (planning-critic critical, 최우선)
**아래 3개 중 하나라도 막히면 8~12주 P0를 시작하지 않는다.** 2~3주.

| # | 게이트 | 통과 기준 |
|---|---|---|
| G1 | 협력 기성 작가 확보 + 법적 처리 | **임포트 동의서·데이터 사용 약관·삭제권 보장을 갖춘 작가 3~5명을 서면 확보** |
| G2 | 골든셋 라벨링 가능성 | 작가가 50~100건("이 장면엔 이 설정/회차가 참조돼야 함")을 **보상 합의 하에 라벨링 가능** 확인 |
| G3 | recall@k 게이트 + 준수율 미니실험 | BGE-M3 vs 외부API, 하이브리드 vs 순수벡터 벤치에서 recall@k 목표 통과 + §9.5 준수율 임계 통과 |

### 4.4 통일 검증 시나리오 (code-critic High 반영)
- 4개 도메인이 **단일 시나리오를 공유**한다: **"이미 수십~수백 회차 연재한 기성 작가(P1) 작품 임포트"**.
- 임포트 시 RAG는 풍부하나 온톨로지는 콜드 상태가 되는 **비대칭**을 해소하기 위해, **임포트 한정 온톨로지 엔티티 자동 추출(원래 P2)을 P0로 끌어올린다**(검토·승인형, 자동확정 금지).
- 임포트 작품은 이미 본문이 존재하므로 **스토리구조의 "신규 막 골격 생성"은 임포트 검증 범위에서 제외**하고, **"다음 막 골격 생성·재동기화 제안"만 검증**한다. 신규 백지 골격 생성은 별도 후속 가설.

### 4.5 데이터 거버넌스 (P0 필수, code-critic blindspot 반영)
- **데이터 격리 보증을 제품 1급 약속으로 명문화**: "작가 원고는 모델 학습/파인튜닝에 쓰지 않으며, 해당 작가 워크스페이스 내 검색에만 쓴다." 기술적으로 테넌트 격리 + 파인튜닝 금지로 못 박는다.
- **외부 LLM 전송 동의/DPA**: 임베딩은 온프레미스(BGE-M3)이나 **회차 본문 전체가 생성 프롬프트로 외부 LLM(Claude/GPT)에 전송**된다. 이에 대한 **동의·DPA·프롬프트 로그 보존/파기 정책을 G1 단계에서 확정**한다(미해결 시 파일럿 자체가 법무에서 막힘).
- **삭제·파기 경로**: append-only 이력(EntityRevision/GenerationProvenance)과 개인정보 파기 의무 충돌을 해소하는 파기 경로(파생 캐시 즉시 삭제 + 원본/이력 익명화 또는 하드삭제 정책)를 §11에 정의.
- **데이터 이식성**: 작가가 떠날 때 원고+온톨로지를 **표준 포맷(JSON/Markdown)으로 온전히 반출**할 수 있는 export를 보장(lock-in 공포 완화).

---

## 5. 핵심 사용자 여정 (end-to-end)

통일 검증 시나리오(기성 작가 임포트) 기준 주 여정.

```
[0] 온보딩: 협력 작가 계약·데이터 동의 → 기존 회차/설정집 임포트
        ↓ (벌크 인덱싱 + 임포트 한정 온톨로지 자동추출→작가 승인)
[1] 작품 설계 확인: 장르(현대 판타지 고정)·톤·타깃 메타 확인
        ↓
[2] 세계관 정립: 인물·세력·아이템·지명·사건·설정 카드 + 정형관계 → SSOT
        ↓
[3] 다음 막 골격 생성: 템플릿 선택 → 막 시놉시스 → 선택 막의 회차 비트 분해(점진)
        ↓ (작가 편집·재정렬·확정)
[4] 회차 초안 확장: 확정 골격 → 장면 비트 단위 순차 생성
        ├─ 온톨로지 정형필드 = 결정론 제약블록 주입
        └─ 서사 배경 = RAG 검색 주입
        ↓
[5] 작가 퇴고·확정: origin 구분 표시 / 채택·재생성·폐기 → finalized
        ↓ (가역적 단방향 루프)
[6] 일관성 검증: 결정론 충돌 4종 + 구조 점검(High 신뢰만) 경고
        ↓
[7] 확정분 누적: RagIndexUnit 재색인 + 온톨로지 반영 제안 + 골격 divergence 계산
        ↓
[8] 회고·역추적: 과거 설정·떡밥 검색, 다음 막 골격 갱신
```

**측정 삽입 지점**: [4]~[5]에서 §9.5의 3-arm 블라인드 A/B와 "다시 쓴 비율" 태깅이 작동한다.

---

## 6. 기능 명세 (도메인 4축)

표기: 동작 / 화면 / 우선순위. P0=MVP 필수, P1=가설 입증 후, P2=장기.

### 6.1 온톨로지 기반 세계관 (Worldbuilding Ontology / SSOT)

| 기능 | 동작 | 화면 | 우선 |
|---|---|---|---|
| 구조화 엔티티 에디터 | 6종(Character/Faction/Item/Location/Event/Worldrule) 타입별 정형필드 **≤5개** + 자유서술 노트, JSON Schema 기반 폼 자동생성 | 엔티티 상세/편집 패널 | P0 |
| 정형 관계 정의 | 소속/적대/동맹/혈연/사제/소유/위치포함/선후 + 방향·메모, 인접테이블 저장, valid_from/to 시점성 데이터 | 관계 추가/편집 모달 | P0 |
| EntityStateTimeline | 시점기반 상태(데이터·판정 레벨). UI는 "현재 상태" 단일 입력으로 단순화 | 엔티티 상세 내 상태 입력 | P0 |
| 결정론 충돌 감지 4종 | ①필드값 불일치 ②시점기준 상태모순 ③관계모순 ④중복엔티티 의심. evidence 강제, dismiss 기억 | 충돌 인박스 | P0 |
| 충돌 실행 경로 분리 | 엔티티 저장=동기 검사 / 회차 확정=비동기 배치 | (백엔드) | P0 |
| StructuredContextSnapshot | 엔티티+1-hop 관계를 **결정론적 템플릿 직렬화**, entity_revision_refs 포함, **동기경로 LLM 배제** | (내부 API) | P0 |
| 엔티티 목록/검색 + 고립·미완성 배지 | 타입필터·검색, 빈 핵심필드/연결없음 표시, 1-hop 인접 표시 | 세계관 대시보드 | P0 |
| EntityRevision 이력(append-only) | 데이터 적재만(풀 UI 제외) | (백엔드) | P0 |
| EntityReference 역참조 | 작가 수동태깅 + name/alias 매칭 후보제안 + **승인(자동확정 금지)** | 엔티티 상세 내 등장회차 | P0 |
| 임포트 한정 자동추출 | 임포트 본문에서 엔티티 후보 추출→작가 승인 | 임포트 마법사 | **P0(임포트 한정)** |
| 관계 그래프 시각화 | 노드-엣지 줌·드래그·필터 | 그래프 뷰 | P1 |
| LLM 의미충돌 감지 | 과거사 모순·능력한계 위반 등 **'low-신뢰 제안'**, detection_method=llm | 충돌 인박스(분리표기) | P1 |
| 스키마 커스터마이즈 / 변경이력 풀 UI | 사용자정의 타입·관계 / 시점별 상태 조회 화면 | - | P2 |

### 6.2 RAG 기반 콘텐츠 재활용 (장기 기억)

| 기능 | 동작 | 화면 | 우선 |
|---|---|---|---|
| 청킹·임베딩·인덱싱 | 장면 1차분할 → 과대장면만 토큰(300~500, 오버랩 10~15%) 슬라이딩. 대사는 화자태깅 **dialogue 트랙** 별도 색인. 메타(chapterNo, sourceType, relatedCharacterIds) 부착 | 인덱싱 상태 인디케이터 | P0 |
| 세계관 SSOT 즉시 인덱싱 + 벌크 임포트 | 회차 0부터 설정 RAG 작동 + 기성 회차 대량 임포트 | 임포트 마법사 | P0 |
| 컨텍스트 빌더 | 직전 N회차(**구조화 요약**, 가변 N) 우선슬롯 예약 + 하이브리드(벡터+BM25 RRF) + 메타 사전필터 + 슬롯별 토큰 cap. **자체 얇은 ContextBuilder** | (백엔드) | P0 |
| 참조 칩 표시 + 핀/제외 | 청크단위 참조칩(includedInPrompt), 제외/핀 토글, 토큰사용량 표시 | 참조 컨텍스트 패널 | P0 |
| 확정→재인덱싱 단방향 루프 | 트랜잭셔널 아웃박스 + 멱등 워커, contentHash/version 증분, sourceId 단위 전량삭제 후 재삽입. 미확정 초안=휘발성 단기주입 | (백엔드) + 상태표시 | P0 |
| 엔티티ID 심기 | ChunkEntityRef에 ID 미리 부착(GraphRAG는 미루되) | (백엔드) | P0 |
| dialogue few-shot 주입 | 캐릭터 대표대사를 few-shot으로 우선주입(자동프로파일 없이) | (생성경로) | P0 |
| 검색 신호 노출 | 근거 약함(낮은 score)/충돌·중복 후보 플래깅까지만. **충돌 판정은 검증 도메인** | 참조 패널 경고 | P0 |
| GraphRAG 관계확장 | 1~2홉 확장검색 | - | P1 |
| 말투 자동프로파일 / 전용 검색뷰 / 재랭커 | styleTraits 추출 / 떡밥추적 UI / cross-encoder | 검색 뷰(P1) | P1 |
| 인용 하이라이트(ProvenanceLink) | 생성문↔근거 정밀매핑 | - | **P2(약속지표 제외)** |

### 6.3 스토리 구조 자동 생성

| 기능 | 동작 | 화면 | 우선 |
|---|---|---|---|
| 막 골격 생성(점진) | 템플릿(영웅의여정/3막/기승전결) + 세계관 요약 → 막 골격. **"다음 막만 점진 생성"** 기본. 전체는 시놉시스 수준 1회 조망만 | 구조 템플릿 마법사 | P0 |
| 막→회차 비트 분해 | 선택 막만, **회차 수만 제어**(분량은 힌트). 목표·등장인물·핵심사건·시작/끝 상태·끊기 훅 | 골격 보드 | P0 |
| 편집·재정렬·확정 | 인라인 수정, 드래그 재정렬, 추가/삭제, 확정 상태전환. 낙관적 동기화 | 골격 보드 + 상세 패널 | P0 |
| 골격→초안 핸드오프 | confirmed beat만 구조화 입력 전달, BeatToDraftLink + beat_snapshot 동결 | (백엔드) | P0 |
| 온톨로지 참조 골격생성 | 읽기 API로 인물·상태를 **소프트 제약** 주입, 생성 후 **EntityReference ID 매칭 강제** | (백엔드) | P0 |
| 결정론 일관성 점검만 | ID 무결성·죽은/미등장 인물·떡밥 open 도달성을 **High 신뢰 경고** | 구조 점검 패널 | P0 |
| 수동 떡밥 태깅 | seed/reinforce/resolve 역할 태깅, open/resolved 카운트, 미회수 목록 | 떡밥 트래킹 뷰 | P0 |
| 재생성 안전장치 | confirmed 덮어쓰기 금지, 새 proposed 후보 병치, 낙관적 락(version) + append-only provenance + 승인 머지 | (백엔드) | P0 |
| 구조화 출력 강제 | tool-calling/JSON schema + Pydantic 검증 + 1회 교정 리트라이, 프롬프트 캐싱 | (백엔드) | P0 |
| LLM 의미모순 감지 | 'low-신뢰 제안', detection_method 분리 | 점검 패널(분리) | P1 |
| 떡밥 자동회수 / 자동 재동기화 | 회수 시점 자동감지 / 확정 즉시 자동 diff | - | P1 |
| 페이싱 곡선 / 다장르 / 분기 A/B UI | 긴장도 그래프 / 템플릿 확장 / 대체골격 비교 | - | P2 |

### 6.4 작가 워크플로 & 에디터 통합

| 기능 | 동작 | 화면 | 우선 |
|---|---|---|---|
| 워크스페이스 + 위계 트리(최소) | 세계관-스토리라인(막·장)-회차 트리 3-pane. 트리 CRUD 최소 | 워크스페이스 홈 | P0 |
| 회차 에디터 | **TipTap/ProseMirror 제한 리치텍스트**(문단·강조·구분선), 자수표시, 자동저장, 회차메타 패널 | 집필 에디터 뷰 | P0 |
| AI 초안 생성 호출 | 골격 트리거 → 컨텍스트 조립 → **장면 비트 단위 순차** 스트리밍(SSE) → 제안블록 삽입 | 생성 패널 | P0 |
| origin 표시 + 채택/재생성/폐기 | ProseMirror mark, **스팬 단위 3-state**(ai_generated/author_written/ai_then_edited) | 에디터 인라인 | P0 |
| 확정→가역 재색인 루프 | RagIndexUnit(versionId 키잉, is_active 단일활성) 멱등 재색인, 확정후 수정시 재색인/비활성 | (백엔드) | P0 |
| **원고 손실 방지** | IndexedDB 로컬미러 + doc_rev 낙관적 동시성 + 확정시 불변 스냅샷 + 저장상태 가시화 + 멀티탭 충돌 감지·경고·머지 | 에디터 전역 | **P0(격상)** |
| 계측(GenerationEvent) | 채택/폐기/재생성/복원 + 원형생존 글자수. **이벤트 스키마 초기부터** | (백엔드) | P0 |
| 설정참조(읽기 전용) | 에디터 떠나지 않는 최소 조회 | 사이드 패널 | P0(얇게) |
| 설정참조 핀/끌어쓰기 / 진척보드 / 버전복원 UI / 연속동선 | - | - | P1 |
| 인라인 부분재생성 / export | 문장 톤보정 / 외부 포맷 | - | P2(export 원칙만 P0 확정) |

---

## 7. 데이터 모델 & 온톨로지 스키마

### 7.1 도메인 경계와 **Chapter 단일 writer 계약** (code-critic Blocker)

| 자원 | 단일 Writer(권위) | 읽기 소비자 |
|---|---|---|
| **Chapter / ChapterVersion / finalized 상태** | **워크플로(에디터) 도메인** | RAG·스토리구조·온톨로지 (외래참조만, **절대 Chapter 상태 쓰기 금지**) |
| RagIndexUnit (= ContentChunk 통합) | RAG 도메인 | 스토리구조(검색 호출) |
| Entity/Relationship/ConflictFlag/StateTimeline | 온톨로지 도메인 | RAG·스토리구조·워크플로(읽기) |
| Storyline/Act/ChapterBeat/PlotThread | 스토리구조 도메인 | 워크플로(핸드오프 표시) |

- **RagIndexUnit과 ContentChunk는 단일 스키마로 통합**하고 키 전략을 **versionId 키잉으로 일원화**(contentHash는 변경감지 보조 컬럼). 이중 인덱싱 경로 금지.
- **회차 확정 이벤트는 에디터가 단독 발행**, RAG/온톨로지/스토리구조는 컨슈머. 이 원칙을 OpenAPI 계약 문서 최상단에 박는다.

### 7.2 온톨로지 스키마

| 엔티티 | 핵심 필드 | 관계/주석 |
|---|---|---|
| Entity | id(uuid), project_id, type(Character\|Faction\|Item\|Location\|Event\|Worldrule), name, aliases[], summary, structured_attributes(jsonb), freeform_notes, current_revision_id | type별 핵심필드 ≤5, 충돌감지 키필드는 생성컬럼 승격+인덱스 |
| EntityRevision | id, entity_id, changed_fields(jsonb diff), full_snapshot, changed_at, source(작가수동\|회차반영\|자동추출승인), related_chapter_id | append-only, 시점충돌·재현의 원천 |
| Relationship | id, project_id, source/target_entity_id, relation_type, direction, note, valid_from/to_chapter_id | 시점성. 역추적=재귀 CTE |
| EntityStateTimeline | id, entity_id, attribute_key, value, effective_from_chapter_id, effective_to, reason | **사망후 활동 오탐 방지의 핵심** |
| ConflictFlag | id, kind(4종+서사의심), severity, **detection_method(deterministic\|llm)**, involved_entity/chapter_ids, evidence(jsonb), message, resolution_status | evidence 강제, UI 신뢰도 차등 |
| EntityReference | id, entity_id, chapter_id, reference_type, confidence, confirmed_by_author | 자동확정 금지 |
| StructuredContextSnapshot | id, project_id, requested_entity_ids[], serialized_cards(jsonb), relation_summary, entity_revision_refs(jsonb), as_of_chapter_id | 결정론 직렬화, 주입 버전 재현 |

타입별 정형필드(예): Character(나이·눈색·능력·소속·현재상태), Faction(목표·우두머리·본거지), Worldrule(규칙명·적용범위·제약).

### 7.3 RAG / 워크플로 핵심 엔티티

| 엔티티 | 핵심 필드 | 주석 |
|---|---|---|
| ContentChunk(=RagIndexUnit) | id, project_id, source_version_id(=ChapterVersion), sourceType(chapter\|setting\|dialogue), narrative_order, text, embedding(vector), sparseTerms, tokenCount, charStart/End, contentHash, embeddingModel, **is_active(bool)**, version | versionId 단일 키잉, is_active 단일 활성 |
| ChunkEntityRef | chunkId, entityType, entityId(온톨로지 소유), refKind(appears\|mentions\|defines) | ID는 온톨로지 발급, RAG 참조만 |
| RetrievalQuery/Result | triggerType, queryText, characterFocusIds[], topK, tokenBudget / chunkId, vector/lexical/graph/hybridScore, includedInPrompt, pinned/excludedByAuthor | 참조칩·정확도 지표 원천 |
| GenerationRun/Event | targetChapterNo, mode, usedTokens, model, latency / type(adopt\|discard\|regenerate\|restore), origin_span_chars, **survived_chars** | 원형유지율 산출 |
| IndexState | project_id, embeddingModel, embeddingDim, lastIndexedChapterNo, chunkCount, reindexNeeded, schemaVersion | 재인덱싱·모델교체 권위 |
| ChapterDocument | chapterId, version, doc(jsonb: ProseMirror), 텍스트노드 origin 마크 + source_generation_id | origin은 mark로 자동추적 |

### 7.4 스토리구조 핵심 엔티티
Storyline / StructureTemplate(stages jsonb) / Act(order_index, stage_key, status) / ChapterBeat(goal, key_events, start/end_state, cliffhanger, target_length=힌트, status, version) / PlotThread(+PlotThreadTouch 조인: beat_id, role) / EntityReference(beat↔온톨로지, cached_entity_state+snapshot_version) / GenerationProvenance(append-only) / BeatToDraftLink(beat_snapshot 동결, divergence_status).

### 7.5 **회차 시점 단일 정렬 키** (code-critic High)
- 시점 판정에 재배열 가능한 정수(chapterNo/seq_no)를 쓰지 않는다.
- **식별 = chapter_id(uuid)**, **시점 비교 = narrative_order(fractional index / lexorank)** 로 통일.
- 모든 도메인(온톨로지 effective_from, RAG chapterNo<=N 필터, 스토리구조 seq_no)은 **narrative_order로 시점 비교**한다.
- 회차 삽입/재정렬 시 narrative_order 갱신과 온톨로지 StateTimeline·RAG 메타 동기 갱신 책임을 에디터 도메인이 진다.

---

## 8. 시스템 아키텍처 & 기술 스택

### 8.1 기술 스택 (4개 도메인 통일)

| 레이어 | 선택 | 근거 |
|---|---|---|
| 주 저장소(SSOT) | **PostgreSQL 16** | 트랜잭션 정합성, jsonb 가변필드, 재귀 CTE 역추적 |
| 벡터 | **pgvector(HNSW)** | 단일DB 통합으로 메타필터+벡터검색 단일쿼리, 멱등·롤백 정합성. 규모 확대 시 Qdrant 이전 |
| 그래프DB | **미도입** (필요 시 동일 인스턴스 Apache AGE) | 단일작품 2~3hop은 재귀 CTE 충분, 이중쓰기 SSOT 분열 회피 |
| 하이브리드 검색 | Postgres FTS(한국어 nori/mecab-ko) BM25 + 벡터 RRF | 고유명사 정확매칭 보강 |
| 임베딩 | **온프레미스 BGE-M3**(벤치 후 확정) vs OpenAI 3-large/Cohere | 원고 프라이버시, 한국어·dense+sparse |
| 생성 LLM | Claude Sonnet급 / GPT-4.1급 + **모델 추상화(LiteLLM)** | 긴 컨텍스트·한국어 서사. 보조호출은 경량모델 |
| 구조화 출력 | tool-calling/JSON schema + Pydantic + 1회 교정 리트라이 | 편집가능 구조 데이터 안정수신 |
| 백엔드 | **FastAPI(Python)** | RAG/LLM 생태계 정합, SSE 스트리밍 |
| 비동기 워커 | Celery/ARQ + Redis (아웃박스) | 확정→재인덱싱 비동기 |
| 프론트 | **Next.js + TS / TipTap(ProseMirror) / TanStack Query / dnd-kit** | origin 추적·드래그·낙관적 동기화 |
| 로컬 내구성 | IndexedDB(Dexie) + doc_rev | 원고 손실 방지 |
| 오케스트레이션 | 얇은 자체 ContextBuilder + LlamaIndex(인덱싱/리트리벌 부분만) | 투명성·디버깅 |
| 관측 | OpenTelemetry + Langfuse + **단일 trace_id 전파** | 도메인 횡단 디버깅 |

### 8.2 전체 구도
- **권위(SSOT)는 PostgreSQL. RAG 인덱스·온톨로지 캐시는 파생 뷰.** 절대 RAG/그래프를 본문 권위 저장소로 쓰지 않는다.
- 이 시스템은 4개 도메인 + **1개 횡단 워크스트림(가설 검증/측정, §9.5)** 으로 구성.

### 8.3 온톨로지 ↔ RAG 결합 (핵심 설계 원칙)
> **정형 필드 = 결정론적 lookup 주입(프롬프트에 "박는다") / 서사 배경 = RAG 벡터 검색 주입("찾는다")**

- (a) 사전 인덱싱: 엔티티 카드를 안정적 자연어 문장으로 직렬화해 벡터DB에 엔티티 청크로 색인.
- (b) 생성 직전 동기 lookup: "회차 등장 엔티티 ID + as_of_chapter" → StructuredContextSnapshot을 **결정론 템플릿 직렬화**로 반환(**LLM 배제**).
- 둘을 섞으면 임베딩 검색이 핵심 설정을 누락해 가설이 흔들리므로 **인터페이스 계약 문서에 명문화**.

### 8.4 회차 확정 이벤트 3중 컨슈머 정합성 (code-critic Blocker)
확정 이벤트는 RAG 재색인 + 온톨로지 상태반영 제안 + 스토리구조 divergence 계산을 트리거한다. 부분 실패 대응:

| 컨슈머 | 동기/비동기 | 부분 실패 시 |
|---|---|---|
| RAG 재색인 | 비동기 best-effort | 멱등 재처리 큐 |
| 온톨로지 상태반영 | 비동기, 단 **충돌판정의 선행조건** | 미반영이면 충돌판정 보류 |
| 스토리구조 divergence | 비동기, 작가 요청 시만 계산 | 지연 무방 |

- **워터마크 도입**: `last_consistent_chapter_no`(narrative_order 기준). **충돌 감지(ConsistencyFlag)는 온톨로지 반영이 완료된 회차까지만 판정**한다. 미반영 회차는 경고를 띄우지 않음 → "방금 확정한 본문과 모순된 헛경고"로 인박스 신뢰가 죽는 사고 방지.
- **통합 이벤트 상태 테이블**: 세 컨슈머의 진행 상태를 한곳에서 관측.

### 8.5 데이터 흐름 (요약)
- **인덱싱**: 확정 → Chapter.finalized 커밋 + 아웃박스(같은 트랜잭션) → 워커가 versionId 키잉 청킹·임베딩 upsert, 이전 version is_active=false → IndexState 갱신 → "기억에 반영됨" 푸시.
- **생성**: 골격 선택 → 컨텍스트 조립([세계관 코어(캐시) : 직전 N요약 : 검색 설정 : 검색 과거회차] 슬롯 cap) → 장면 비트 순차 SSE 생성 → origin 마크 제안블록 → 채택/재생성/폐기를 GenerationEvent로 기록.
- **충돌**: 엔티티 저장=동기 4종 검사 / 회차 확정=비동기 배치, 모두 narrative_order as_of 시점 + 워터마크 게이트.

### 8.6 end-to-end 지연 예산 (code-critic High)
장면 순차 생성으로 회차당 LLM 호출이 다발화하므로 **횡단 지연 예산을 명시**:

| 단위 | 예산(p95) | 배분 |
|---|---|---|
| 회차 1편 | ≤ 90초 | 5장면 가정 |
| 장면 1개 | ≤ 15초 | 검색 2s + 생성 12s + 보조 1s |

- 장면 간 의존 없으면 병렬 생성, 작가 퇴고 중 다음 장면 **prefetch 파이프라이닝**. 프롬프트 캐시 히트율은 **실측 검증 전 비용 곡선 미확정**.
- StructuredContextSnapshot은 **회차 단위로 1회 산출·캐시 고정**, 장면별은 "등장 엔티티 부분집합 강조"만(온톨로지 추가 공수 차단).

### 8.7 보안·테넌트 (code-critic blindspot)
- 파일럿 3~5명 = 즉시 멀티테넌트. **workId/project_id/owner_id를 단일 테넌트 경계로 강제**, 모든 쿼리 스코핑 + IDOR 방어(한 작가 회차가 타 작가 RAG 검색에 섞이면 미공개 원고 유출).
- **LLM 비용 가드레일**: 회차 재생성 rate limit + 일/월 토큰 비용 상한(어뷰징·시행착오 폭증 방지).

---

## 9. AI 파이프라인

### 9.1 인덱싱
장면 1차분할 → 과대장면만 토큰 슬라이딩(300~500, 오버랩 10~15%) → 대사 dialogue 트랙 별도 → 메타(narrative_order, sourceType, relatedCharacterIds, ChunkEntityRef) → 임베딩 upsert(versionId 키잉) → IndexState 갱신. 엔티티 태깅은 **온톨로지 엔티티명 사전 매칭 우선**(자유형 NER 환각 회피).

### 9.2 검색
직전 N회차 **구조화 요약**(가변 N) 우선슬롯 예약 + 하이브리드(벡터+BM25 RRF) + 메타 사전필터(narrative_order <= as_of, characterFocusIds) + 슬롯별 토큰 cap 절단. 채택 청크는 RetrievalResult로 기록(투명성).

### 9.3 생성
컨텍스트 조립 → tool-calling/JSON schema(골격) 또는 산문 스트리밍(초안) → 장면 순차. **결정론 제약블록을 프롬프트 상단·캐시 고정**.

### 9.4 일관성 점검 (환각·일관성 대응)
| 레이어 | 방법 | 노출 |
|---|---|---|
| 결정론 | ID 무결성·시점 상태모순·관계모순·중복의심·떡밥 도달성 | **High 신뢰 경고** (detection_method=deterministic) |
| LLM 추론(P1) | 자연어 사건 명제화 → 온톨로지 대조 | **'low-신뢰 제안'** (detection_method=llm), 근거인용 강제, 자동해결 금지 |

- RAG는 "근거 약함/상충" **신호 노출**까지, **충돌 판정은 온톨로지(일관성 검증) 도메인** 책임. 경계를 코드·계약으로 분리.
- 환각 엔티티: 생성 출력 엔티티 **사후 ID 매칭 강제**, 미매칭은 "신규 후보"로 작가 확인(자동생성 금지).

### 9.5 **단일 A/B 검증 프로토콜 & 단일 측정 단위** (code-critic Blocker · planning-critic critical)
가설 증명 책임 공백·측정 단위 불일치를 해소하기 위해 **독립 5번째 워크스트림(명시 owner: 통합 측정 책임자)** 으로 분리.

- **단일 측정 단위 = 회차**. 에디터 GenerationEvent가 측정 단위의 진실원이며, 온톨로지/RAG의 A/B 정의를 GenerationEvent 스키마에 종속시킨다.
- **3-arm 블라인드 A/B**: 동일 회차에 대해 `[온톨로지+RAG 주입 / RAG만 / 무주입]` 3종 본문을 블라인드로 작가에게 제시.
- **단일 태깅 정의**: "설정 충돌로 다시 써야 함" 비율 + 원형유지율(LCS/문자 유사도).
- **준수율 미니실험(G3)**: 완벽한 설정 박은 프롬프트로 한국어 초안 N개 생성 → "다시 써야 함" 비율 측정. 준수율 임계 미달이면 검색 자원 투입 전 **생성단 제어(설정 위반 자동 재생성·후처리 검증 루프)로 전략 전환**.
- **측정 지표 메타검증**(blindspot): 한국어 조사·어미 변형으로 LCS가 작가 체감과 괴리될 수 있어, 지표-체감 상관을 G3에서 확인.

---

## 10. 핵심 의사결정 로그

| # | 결정 | 트레이드오프 / 근거 |
|---|---|---|
| D1 | 시점 모델(StateTimeline/valid_from·to) **데이터·판정 P0**, UI는 현재상태만 | 시점 없는 충돌감지는 회상·변신 서사 오탐 폭발 → 인박스 신뢰 붕괴 |
| D2 | 정형필드 **≤5개** + 채움률 1급 선행지표 | 작가는 정형필드 비우고 서술에 묘사 쏟음 → 결정론 주입 토대 붕괴 방지 |
| D3 | 정형=결정론 lookup / 서사=RAG 검색, **동기경로 LLM 배제** | 섞으면 임베딩이 핵심설정 누락 → 가설 흔들림 |
| D4 | PostgreSQL 단일 소스, 그래프DB 미도입 | 이중쓰기 SSOT 분열 회피 |
| D5 | 가설을 **RAG+온톨로지 공동 책임**, RAG KPI는 recall/참조정확도로 한정 | 주입 ≠ 준수, RAG 단독 귀속 불가 |
| D6 | 검증 시나리오 = **기성 작품 임포트** + 임포트 한정 자동추출 P0 | 백지 작품은 RAG ON/OFF 차이 無 |
| D7 | 직전 회차 = 구조화 요약·가변 N·슬롯 cap | 5,500자×N 원문 주입은 토큰 함정 |
| D8 | 스토리: **다음 막만 점진 생성**, 회차 수만 제어(분량은 힌트) | 일괄 펼침은 비용·환각 비선형 |
| D9 | 일관성 점검 **결정론만 High 신뢰**, LLM 의미충돌 P1 'low-신뢰' | 둘 묶으면 결정론 경고까지 불신 |
| D10 | 재생성은 confirmed 덮어쓰기 금지, proposed 병치 | 작가 편집 유실=작가성·신뢰 파괴 |
| D11 | AI 생성 = **장면 비트 단위 순차**, 측정 단위도 장면→**A/B는 회차** | 5,500자 단발은 후반 일탈로 채택률 부당 하락 |
| D12 | 확정은 **가역, RAG는 항상 is_active 최신만** | 이중 사실 검색 환각 차단 |
| D13 | 제한 리치텍스트(ProseMirror) + 결정적 export 시리얼라이저 | 자유서식은 export·origin 복잡도만 증가 |
| D14 | origin = 스팬 단위 3-state, **원형유지율은 LCS로 분리 측정** | 정밀 블록추적 과투자 회피 |
| D15 | **원고 손실 방지 P0 격상**(IndexedDB+불변스냅샷+LWW 금지) | 마감 작가에게 원고 유실=제품 사망 |
| D16 | **Chapter 단일 writer = 에디터**, RagIndexUnit=ContentChunk 통합 | split-brain·stale 청크 차단 |
| D17 | 확정 이벤트 **워터마크**(반영 완료 회차까지만 판정) | 헛경고로 인박스 신뢰 죽는 사고 방지 |
| D18 | **단일 A/B 프로토콜·단일 측정 단위(회차)·독립 측정 owner** | 책임 공백·단위 불일치로 "잘 모르겠다" 결론 방지 |
| D19 | **검증 가능성 스파이크(G1~G3) 코딩 前 게이트** | 표본·법적·recall 미해결 시 P0 무의미 |
| D20 | 수익 모델 = WTP 축 추가 + 1인 변동비 실측 | 가치 체감 ≠ 변동비 이상 지불 |

---

## 11. 리스크 & 완화책

| # | 리스크 | Sev | 완화책 |
|---|---|---|---|
| R1 | 검증 표본(기성 원고) 조달 실패 | Critical | G1 서면 동의 게이트, 데이터 격리 1급 약속, 골든셋 보상 묶음 |
| R2 | 단위경제 적자(WTP < 변동비+CAC) | Critical | WTP 측정 + 1인 변동비 실측, 미달 시 상위작가로 페르소나 축소 |
| R3 | 가설 합격선/책임 공백 → "잘 모르겠다" | Critical | §13.1 단일 합격선·kill criteria, §9.5 독립 측정 owner |
| R4 | Chapter 소유권 split-brain / stale 청크 | Blocker | D16 단일 writer, RagIndexUnit=ContentChunk 통합·versionId 키잉 |
| R5 | 확정 이벤트 부분실패 헛경고 | Blocker | D17 워터마크, 통합 이벤트 상태 테이블 |
| R6 | 주입 ≠ 준수(LLM이 설정 무시·환각) | High | G3 준수율 게이트, constraint 블록, 미달 시 생성단 제어 전환 |
| R7 | 한국어 검색 recall 미달 | High | G3 골든셋 A/B 게이트, BM25/필터 고유명사 보강 |
| R8 | 콜드스타트 / 시나리오 도메인 충돌 | High | D6 임포트 시나리오 통일 + 임포트 자동추출 P0 |
| R9 | 시점 키 불일치(재정렬 시 좌표계 붕괴) | High | D/§7.5 narrative_order 단일 정렬 키 |
| R10 | end-to-end 지연이 "속도" 니즈 정면 위배 | High | §8.6 지연 예산, prefetch 파이프라이닝, 캐시 히트 실측 |
| R11 | 외부 LLM 원고 전송 동의/DPA/파기 미비 | High | §4.5 DPA·파기 경로 G1 확정, 데이터 이식성 보장 |
| R12 | origin 추적 모호(이동·병합) | Med | 스팬 단위 + ai_then_edited 회색지대, 지표는 LCS로 디커플 |
| R13 | 정형필드 채움률 저조 | Med | 필드 ≤5, 임포트 자동추출 승격, 채움률 조기경보 |
| R14 | N=1~5 표본 통계 한계 | Med | 정량+정성 병행, 구간별 측정, "학습 프레임" |
| R15 | 도메인 간 계약 미합의 순환의존 | Med | §12 M0 단일 오너·데드라인 OpenAPI 동결, mock 병렬개발 |
| R16 | 멀티테넌트 IDOR 유출 | Med | 단일 테넌트 경계 강제, 권한 모델 |
| R17 | 측정지표-체감 괴리(LCS) | Med | G3 지표 메타검증 |
| R18 | 어뷰징(표절 임포트·재생성 폭증) | Med | rate limit·비용상한, 임포트 저작권 확인, AI 고지 검토 |
| R19 | 중복 엔티티 판정 오탐/누락 | Low | 경고만(자동병합 금지), 작가 alias 병합, dismiss 기억 |
| R20 | 단일 장르(현대 판타지)가 변신·회귀로 오탐 최다 장르 | Low | 시점 모델·dismiss 학습, 검증 외적타당성 한계 인정 |

---

## 12. 마일스톤 로드맵

### M0 — 검증 가능성 스파이크 & 계약 동결 (코딩 前, 2~3주) **[최우선]**
- 산출물: G1(작가 3~5명 서면 동의 + DPA/파기 정책), G2(골든셋 라벨링 합의), G3(recall@k + 준수율 게이트 통과).
- **도메인 간 인터페이스 계약 단일 문서 동결**(단일 오너=통합 아키텍트, 명시 데드라인): Chapter 단일 writer, 엔티티ID 발급(온톨로지 단독), chapter_id 소유(에디터), 충돌판정 책임(온톨로지), retrieve/upsertIndex·invalidate, narrative_order 정렬 키, 확정이벤트 워터마크 → OpenAPI 스텁 + mock.
- **게이트: G1~G3 또는 계약 동결 중 하나라도 실패 시 P0 미착수.**

### M1 — P0 핵심 루프 (8~12주, 백엔드 1~2 + 프론트 1 + AI 1 + 측정/작가 리에종 1)
| 주차 | 산출물 |
|---|---|
| W1~2 | PG 스키마·CRUD·상태머신·위계 트리·이벤트 상태 테이블 |
| W3~5 | **에디터 코어**(ProseMirror origin 마크 + IndexedDB 내구성 + doc_rev) — 최대 덩어리 |
| W3~5(병렬) | 온톨로지 에디터·관계·결정론 충돌 4종·StateTimeline·StructuredContextSnapshot |
| W4~6 | RAG 인덱싱·ContextBuilder·임포트 벌크·dialogue 트랙·평가하니스 |
| W6~8 | 골격 생성·비트 분해·핸드오프·결정론 점검·재생성 안전장치 |
| W6~8 | AI 생성 경로(장면 순차 SSE)·채택/재생성/폐기·GenerationEvent |
| W9~10 | 확정→가역 재색인·워터마크·온톨로지 반영 제안 연동 |
| W11~12 | **3-arm 블라인드 A/B 측정 인프라**·구간별 채택률·변동비 실측·WTP 인터뷰 |
| 상시 | trace_id 전파·Langfuse·골든셋 회귀 평가·파일럿 온보딩 |

### M2 — 가설 판정 게이트
- §13.1 합격선/kill criteria로 **GO / PIVOT / KILL** 판정. PIVOT 시 생성단 제어 또는 페르소나 축소.

### M3 — 후속 (가설 입증 후)
- P1: GraphRAG, LLM 의미충돌 감지, 떡밥 자동회수, 자동 재동기화, 그래프 시각화, 말투 자동프로파일, 설정참조 핀, 진척보드, 버전복원 UI.
- P2: 인용 하이라이트, 페이싱 곡선, 다장르/스키마 커스터마이즈, export 기능, 멀티테넌트 본격화(Qdrant 분리).

---

## 13. 성공 지표

### 13.1 **단일 합격선 & Kill Criteria** (planning-critic critical)
- **GO(가설 입증)**: 파일럿 작가 5명 중 **4명 이상**이 (a) RAG ON 초안의 **원형 유지율 중앙값 ≥ X%**, (b) 3-arm 블라인드에서 **온톨로지+RAG arm을 Y회 중 Z회 이상 "설정 일치"로 선택**, (c) WTP ≥ 1인 변동비 손익분기 구독가.
- **KILL(접기)**: 원형 유지율 중앙값 **< W%** 또는 준수율 미니실험(G3) 임계 미달 → RAG 접근 폐기/전략 전환.
- (X/Y/Z/W는 M0 G3 베이스라인과 작가 인터뷰로 수치 확정 — §14 즉시 결정 항목.)

### 13.2 도메인별 지표

| 도메인 | 지표 |
|---|---|
| 공동(가설) | 원형유지율(LCS), 3-arm 선택 분포, "다시 쓴 비율"(단일 정의, 회차 단위), WTP |
| 온톨로지 | 정형필드 채움률(**1급 선행**), SSOT 참조율, 충돌 수용률 vs 무시율, SSOT 최신성 |
| RAG | recall@k(**게이트**), 참조 정확도(적절/부적절 제외율), 검색 p95, 인덱싱 지연 |
| 스토리구조 | 골격 채택률 + **구조 수정 깊이 + BeatToDraftLink divergence 교차**, 골격→초안 전환율, 결정론 모순 조기발견 수 |
| 워크플로 | 초안 채택률(구간별: 초반/중반/장기), 루프 완주율, 재생성 횟수 분포, 이탈없는 동선 비율 |
| 사업 | 작가 1인당 월 LLM 변동비, 단위경제 손익분기 구독가 |

---

## 14. 열린 질문 / 후속 결정 필요 사항

### 14.1 코딩 착수 전 즉시 결정 (M0 내)
1. **합격선 수치(X/Y/Z/W)** 확정 — G3 베이스라인 + 작가 인터뷰 기반.
2. **MVP 단일 장르 확정**(현대 판타지 가정) — 에디터·골격 UX·온톨로지 스키마·골든셋 종속. 단, 현대 판타지는 회귀·각성·변신으로 결정론 오탐이 가장 심한 장르임을 인지하고 시점 모델·dismiss 학습으로 대응.
3. **DPA/파기 정책 + 외부 LLM 전송 동의** 확정(G1과 묶음).
4. **도메인 간 계약 동결**(Chapter writer, 엔티티ID, 시점 키, 워터마크, retrieve/upsert API).
5. **임베딩/청킹/하이브리드 가중치** A/B로 확정(G3).

### 14.2 가설 판정(M2) 전 결정
6. WTP < 변동비 손익분기 시 **페르소나를 상위작가로 축소**할지 + 페르소나 정의 재작성.
7. 준수율 미달 시 **생성단 제어(설정 위반 자동 재생성/후처리 검증)** 전환 범위.
8. 골든셋 50~100건이 도메인 대표성 충분한지, 라벨링 보상 구조.

### 14.3 후속(M3+)
9. 신인 작가(P2) 가치·중반 붕괴 방지를 **별도 가설로 분리** 검증할 시점.
10. AI 생성물 저작권 귀속·플랫폼 AI 고지 의무(카카오페이지·네이버시리즈) 검토.
11. 미확정 초안 휘발성 주입의 작가 멘탈모델 혼란("방금 쓴 걸 왜 기억 못 하지") UX 처리.
12. 멀티테넌트 본격화 시 Qdrant 분리 시점, 임베딩 모델 교체 마이그레이션 신·구 인덱스 호환 정책.

---

### 부록 A. 도메인 간 계약 체크리스트 (M0 동결 대상)
- [ ] Chapter/ChapterVersion/finalized **단일 writer = 에디터** (RAG·온톨로지·스토리구조는 읽기/외래참조만)
- [ ] RagIndexUnit ≡ ContentChunk **단일 스키마**, **versionId 단일 키잉**
- [ ] 엔티티ID **온톨로지 단독 발급**, 타 도메인 참조만
- [ ] 회차 시점 = **chapter_id(식별) + narrative_order(시점비교)**, 정수 키 시점판정 금지
- [ ] 확정 이벤트 **에디터 단독 발행** + 3중 컨슈머 + **워터마크(반영완료 회차까지만 충돌판정)**
- [ ] retrieve(chapterId, skeleton) / upsertIndex(versionId) / invalidate(prevVersionId) / read-only entity 조회
- [ ] 정형=결정론 주입 / 서사=RAG 검색 주입 분리 명문화
- [ ] 단일 A/B 프로토콜·단일 측정 단위(회차)·측정 owner
- [ ] 단일 trace_id 횡단 전파 규약