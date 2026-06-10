# 디자인 패턴·아키텍처 강화 권고서

> 대상 시스템: AI 웹소설 에이전틱 코파일럿 (PoC 단계 — `harness.py`/`checker.py`/`rag.py`/`ontology.py`/`run_poc.py`, 5화·인물 3·규칙 1 규모)
> 작성 관점: "트렌디하되 구조적으로 정당한 것만." 적대 검증(ADOPT 0 / ADAPT 6 / REJECT 6)을 1급 필터로 채택. **미구현 PRD를 근거로 새 인프라를 정당화하는 순환 논증을 금지**하고, 지금 코드에 실재하는 결함과 자백된 부채만 손댄다.

---

## 1. 요약 — 두 트렌드가 우리에게 주는 핵심 시사

| 트렌드 | 2026 핵심 명제 | 우리 시스템에 정당한 적용 | 우리 시스템에 과한 적용(cargo-cult) |
|---|---|---|---|
| **Harness Engineering** ("harness over model") | 루프 구조·외부 검증·타입드 핸드오프가 모델 능력보다 행동을 좌우 | 타입드 경계 검증, 구조화 출력 강제, 장면 미니루프, eval 게이트 영속화, 경량 관측 훅 | 동적 토폴로지 플래너, 풀 OTel+Langfuse 스택, hibernate-wake durable 백본 선투자 |
| **LLM Wiki** (Karpathy 컴파운딩 위키) | LLM은 지치지 않으므로 raw 재검색 대신 교차링크된 영속 지식 아티팩트를 유지 | (현 단계 없음) — **전제인 finalize 팬아웃 다중 컨슈머가 코드에 부재** | 위키 5종 전부. 5화에 lifecycle 5상태·typed 엣지·index.md·단일 writer governance |

**가장 중요한 한 문장:** 제출된 12개 패턴의 근거는 대부분 PRD 섹션(finalize 팬아웃 / 멱등 saga / trust_tier / writer_lock / durable 복원)을 "우리가 이미 요구한다"고 인용하지만, **그 인프라는 PoC에 단 하나도 존재하지 않는다.** `harness.py`는 인메모리 dict를 흘려보내는 단일 선형 함수이고, finalize는 `rag.index_chapter` 단일 동기 호출이 전부다. 따라서 **LLM Wiki 5종은 전부 "없는 토대 위 부트스트랩"으로 현 단계 REJECT**하고, 진짜 손댈 것은 코드에 실재하는 결함 6건(ADAPT)으로 좁힌다.

**검증을 통과한 6개 ADAPT의 우선순위(정당성 순):**

1. `validation1` 결정론 룰 테이블 — PoC가 명시적으로 자백한 임박 부채 (`checker.py` rule_no_reawaken substring 매칭)
2. `memory3` 멱등 색인 — 위키 일반론에 묻혀 있던 **진짜 버그** (`rag.py` index_chapter 중복 색인)
3. `harness1`+`validation5` 타입드 핸드오프 / ContextBoard — ontology/rag 경계의 런타임 미강제 (동일 처방, 1건으로 통합)
4. `harness5`+`validation4` eval 게이트 영속화 — `run_poc.py`가 90% 구현, 영속화만 필요 (중복, 1건으로 통합)
5. `harness3` 장면 미니루프 — 통짜 재생성을 장면 단위로 (M2 착수)
6. `harness2` 구조화 출력 + `harness6` 관측 훅 — 조용한 정지 가시화

---

## 2. ADOPT (즉시 채택 권고)

**없음.** 적대 검증 결론과 동일하게, 무조건 즉시 채택할 패턴은 0건이다. 이유는 두 가지다.

- **위키 계열 5종**은 토대(finalize 팬아웃 다중 컨슈머)가 부재해 "정당화 근거 자체가 미구현"이다 → REJECT.
- **ADAPT 6종**은 전부 "지금 코드의 실재 결함을 고치는 변경"이지만, 그중 둘(`validation1` 룰 테이블, `harness1`/`validation5` ContextBoard)은 **기능 추가가 아니라 구조 리팩터**다. 사용자 전역 규칙(편집은 명시적 진행 동사가 있을 때만)에 따라 **본 권고서는 제안까지이고, 코드 변경은 사용자 승인 후 진행**한다.

> 즉 "즉시 무조건"이 아니라 "승인 시 가장 먼저"라는 의미에서, 아래 ADAPT 1·2가 사실상의 1순위다.

---

## 3. ADAPT (변형 채택) — 검증 통과한 6건

### 3.1 결정론 룰 테이블 — 룰을 데이터로 (`validation1`, validation topPick)

| 항목 | 내용 |
|---|---|
| **무엇** | consistency 위반 판정 로직을 코드 if-분기가 아니라 **닫힌 룰 테이블 + 디스패처**로 외부화. `RuleSpec` 타입드 레코드를 엔진이 순회. |
| **왜 트렌디** | 2026 하네스의 "결정론 linter를 외부검증 1급, 룰을 스키마로 박는다"(outlines/Instructor 계열)의 정확한 적용. |
| **왜 정당** | **PoC가 스스로 자백한 임박 부채.** `run_poc.py`가 "규칙 다수화 시 키워드 매칭 확장 불가, 규칙 컴파일러 필요"라고 명시. `checker.py`의 `rule_no_reawaken = '재각성' in rule_text and ('불가'/'1회'/'한번')` substring 매칭은 **규칙 2개째에서 깨진다.** 외부 결정론 검증은 룰이 코드에 흩어지는 순간 무너진다(검증기 자체가 검증 불가). |
| **우리 어디에** | `checker.py` compare()/ontology_internal_check() · PRD §6.3 consistency_check · §9.5 DeterministicStructureChecker · §13.2 결정론 게이트 · §14 M1 GO 게이트 |
| **구체 변경** | `RuleSpec` Pydantic 모델 신설 `{rule_id, layer, predicate_kind(enum), params, grade, reversibility, evidence_template}`. checker를 RuleSpec 순회 단일 엔진으로 축소. CATEGORICAL_VOCAB·NUMERIC_ATTRS·rule_no_reawaken을 RuleSpec row로 이동. |
| **간소 버전 (cargo-cult 경계)** | **"컴파일러" 아님.** predicate_kind를 PoC가 이미 가진 **4종 닫힌 enum**(`categorical_eq` / `numeric_monotone` / `timeline_state` / `worldrule_flag`)으로 고정하고, 그 밖은 전부 semantic 등급으로 강등 → judge+escalation. 튜링완전 DSL 금지(그러면 엔진 자체가 검증 불가). |
| **무시 시 심각도** | 중간~높음. 규칙 수십 개가 되면 substring이 if-분기 스파게티화되어 결정론 코어가 붕괴. **M4 worldgen 전 필수.** |
| **승인 요건** | 구조 리팩터이므로 사용자 승인 후 진행. |

### 3.2 멱등 색인 — `rag.py` 중복 색인 버그 (`memory3`, 위키 일반론에서 추출)

| 항목 | 내용 |
|---|---|
| **무엇** | `rag.py` index_chapter를 멱등으로. 위키 saga 일반론은 버리고, 그 밑에 **묻혀 있던 진짜 버그**만 고친다. |
| **왜 트렌디** | durable workflow의 "도구 멱등성"이 본질이지만, 여기선 트렌드가 아니라 **버그 수정**이다. |
| **왜 정당** | `rag.py` index_chapter는 멱등이 아니다 — 같은 회차를 두 번 색인하면 `self.chunks.append`로 **중복 청크가 쌓인다.** 제안이 "이미 멱등키를 요구한다"고 한 것과 코드가 **정반대.** |
| **우리 어디에** | `rag.py` index_chapter() · PRD §6.5 도구 멱등 · §7.5 finalize 멱등키 |
| **구체 변경** | 재색인 가드 3~4줄: 같은 chapter면 기존 청크 제거 후 재색인. |
| **간소 버전** | 별도 dedup 테이블·버전 테이블 불필요. chapter 키 기준 제거-후-삽입 한 블록. |
| **무시 시 심각도** | 중간. M2 재시도/크래시 복원이 들어오면 중복 청크가 RAG 검색을 오염시킨다. |
| **승인 요건** | 버그 수정이라 리스크 낮음. 단 편집은 사용자 지시 후. |

### 3.3 타입드 핸드오프 + ContextBoard (`harness1` = `validation5`, 통합 1건)

> 적대 검증이 "harness1과 validation5는 같은 실재 결함의 같은 처방"으로 명시 → **1건으로 통합.** blackboard 명명은 60년대 AI 용어 인플레이션이므로 버리고, 본질만 채택.

| 항목 | 내용 |
|---|---|
| **무엇** | 노드 간 페이로드를 untyped dict가 아니라 Pydantic `GraphState`로 동결하고, **ontology(상단·누락0)와 rag(하단·cap)를 같은 dict의 다른 키가 아니라 별도 타입 필드**로 분리해 혼합을 타입 레벨에서 불가능하게. |
| **왜 트렌디** | "타입드 스키마 핸드오프 + 경계 검증", "intent > syntax". |
| **왜 정당** | **두 실재 결함을 동시에 친다.** (1) `harness.py`가 beat/scenes/violations를 untyped dict로 전달하고 `checker.compare`도 grade를 자유 문자열로 넣는다 → 신뢰등급 혼합 누수. (2) `draft_chapter`가 `canon_block`(str)과 retrieved를 **단순 문자열 병합**한다 → ontology 상단과 rag 하단 경계가 런타임에 강제되지 않아, 프롬프트 조립 실수 하나로 RAG 텍스트가 ground truth 슬롯에 새면 §3.1 비대칭 RAG 불변식이 깨진다. |
| **우리 어디에** | `harness.py` draft_chapter 프롬프트 조립 · `checker.compare` grade 필드 · PRD §6.3 신뢰등급 혼합 금지 · §7.3 비대칭 주입 · §11.2 RagIndexUnit(signal_grade/trust_tier) |
| **구체 변경** | (1) `GraphState` Pydantic 1개. (2) `signal_grade: Literal['deterministic','quasi-deterministic','semantic']` enum 1개. (3) `ContextBoard` 3필드 — `authority_slot(AuthorDirective[])` / `ground_truth_slot(OntologyFact[], 누락0 어서션)` / `narrative_slot(RagChunk[], cap·trust_weight)` — 슬롯마다 **타입이 다른 별개 필드**. (4) `assemble_context(board)->prompt` 단일 함수만 직렬화 권한을 갖고 슬롯→프롬프트 위치를 결정론 고정(상단/상단/하단). |
| **간소 버전 (cargo-cult 경계)** | **load-bearing은 단 2개**: signal_grade enum, ontology/rag 분리 필드. 노드 내부 지역 변수까지 타입화 금지. 범용 blackboard 프레임워크·동적 게시판 금지 — "타입 분리가 강제된 불변 컨텍스트 객체"로만. |
| **무시 시 심각도** | 중간. 오타 하나로 `hard_remaining` 필터가 위반을 누락해 **silent ship**(C4 게이트 우회). 리팩터 한 번에 등급2가 등급1 슬롯에 새는 회귀. |
| **승인 요건** | 구조 리팩터이므로 사용자 승인 후. |

### 3.4 eval 게이트 영속화 (`harness5` = `validation4`, 통합 1건)

> 두 렌즈의 동일 재제출 → **1건으로 통합.** "중복이 정당성을 만드는 게 아니라 끼워넣기 신호"라는 검증 지적을 수용.

| 항목 | 내용 |
|---|---|
| **무엇** | `run_poc.py`의 C0~C6를 일회성 스크립트가 아니라 **영속 eval 패키지 + CI 게이트**로 승격. 신뢰등급별 게이트(결정론/준결정론은 binding, semantic은 non-binding 보고). |
| **왜 트렌디** | "unit-test형 eval 하네스(출력 품질 아닌 **행동** 측정)", "self-eval 한계 → 외부검증 1급". |
| **왜 정당** | **신규가 아니라 영속화.** `run_poc.py`가 이미 90% 구현했다 — 신뢰등급 분리 게이트, silent_ship=0 게이트, 재현성 n회. PRD §14 M1 GO 게이트가 본질적으로 eval 게이트 요구. |
| **우리 어디에** | `run_poc.py` 전체 · PRD §13 품질전략 · §14.1 재현성 분산 · §15 성공지표(루프완주·수렴안정성·결정론잔존·silent_ship) |
| **구체 변경** | C0~C6를 `eval/` 패키지로 구조화. silent_ship=0을 hard CI 게이트로 고정. binding = 결정론/준결정론 + silent_ship 0 + clear-재현성 100%만. **LLM-judge(critique)는 게이트 입력 금지, 별도 관측 지표로만.** |
| **간소 버전 (cargo-cult 경계)** | LLM-judge 게이트 승격 금지(self-eval 한계 정면 위반), semantic recall에 숫자 임계 박기 금지(환각 게이트). `run_poc.py`가 이미 그은 선을 그대로 고정. |
| **무시 시 심각도** | 중간. 단 `run_poc.py`가 이미 핵심을 하고 있어 급하진 않다. |

### 3.5 장면 미니루프 + 국소 재계획 (`harness3`, harness topPick)

| 항목 | 내용 |
|---|---|
| **무엇** | 회차를 통짜 함수가 아니라 **scene_plan 노드의 for 루프**로. 한 장면이 위반→재집필이면 그 장면에서만 국소 재계획(partial_rewrite + seam_reweave), 통과한 장면은 건드리지 않음. |
| **왜 트렌디** | "decoupled sub-task 그래프(국소 재계획, 연쇄실패 방지)", "harness over model". |
| **왜 정당** | `harness.py`는 회차 **전체** text를 통으로 draft→check→rewrite한다. `plan_scenes`로 scenes를 만들지만 `draft_chapter`는 그걸 힌트로만 쓰고 분할하지 않는다 = **죽은 구조.** 통짜 재생성은 (a) 통과한 장면을 악화시키고(과교정), (b) 100~200화에서 토큰·지연 폭발이다. |
| **우리 어디에** | `harness.py` generate_chapter 통짜 루프 · PRD §6.2 draft_scene 순회 · §6.7 scene_plan 결정론 분할 · §6.8 자가교정 미니루프 · §6.4 best-so-far monotone |
| **구체 변경** | `for scene in scene_plan`: draft_scene → check(scene) → if critical: partial_rewrite(scene만) → seam_reweave(인접 경계 재검증). per-scene `best_so_far`(결정론 위반 카운트 1급 축) monotone 가드. |
| **간소 버전 (cargo-cult 경계)** | **동적 토폴로지 플래너 금지.** scene_plan은 §6.7대로 결정론 고정 리스트, LLM이 그래프를 못 바꾼다 = 고정 sequential. parallel 장면 생성 금지(장면 N은 N-1 의존). "그래프 엔진"이 아니라 "for 루프 + 장면별 monotone 가드". |
| **무시 시 심각도** | 중간~높음(장기). 통짜 재생성은 토큰/지연 폭발 + 과교정 온상. |
| **착수 시점** | **M2.** PoC 단발 검증 목적엔 통짜로 충분하나, 다른 모든 패턴(체크포인트·인터럽트·국소 monotone·장면 단위 관측)이 여기에 의존한다. |

### 3.6 구조화 출력 + 관측 훅 (`harness2` + `harness6`, 묶음)

> 둘 다 "조용한 정지를 가시화"라는 같은 목표 → 묶어서 제시. 둘 다 작고 load-bearing.

| 항목 | 내용 |
|---|---|
| **무엇** | (a) extract_claims를 Literal enum response_model로 디코드-타임 강제. (b) `plan_scenes`의 조용한 except 폴백을 관측되는 degraded 경로로. (c) 경량 `emit()` 훅 + `failure_mode` enum + JSONL append. |
| **왜 트렌디** | "구조화 출력 보장(decode-time JSON Schema)", "관측 훅 파이프라인(decision-flow 캡처)", "조용한 정지를 구조적으로 불가능하게". |
| **왜 정당** | `plan_scenes`의 except 폴백이 chat_json 실패를 **조용히 삼킨다**(§10.9 '조용한 정지 금지' 미니 위반). `harness.py`는 rounds에 위반만 append할 뿐 decision-flow가 없다 — plan_queries가 뭘 검색했는지, 왜 ESCALATED됐는지 추적 불가. |
| **우리 어디에** | `harness.py` plan_scenes except · rounds 로깅 · `checker.py` extract_claims · PRD §10.9 조용한 정지 금지 · §12.1 OTel+Langfuse(단일 trace_id) |
| **구체 변경** | (1) extract_claims를 `Literal[*CATEGORICAL_VOCAB]` response_model로 — 단 **'기타' 탈출구를 enum에 반드시 포함.** (2) plan_scenes 폴백 시 `emit(parse_failure)` 1줄 기록. (3) `emit(node, event_type, payload)` 1개 + `failure_mode` enum + JSONL append-only. |
| **간소 버전 (cargo-cult 경계)** | **산문 생성(draft_scene)에 CFG 금지** — 문체가 죽는다. 구조화는 결정론 코어 입력(extract_claims, critique 점수)에만. **풀 OTel collector+Langfuse 스택 금지** — emit 함수 1개 + JSONL. load-bearing 분기(reflect 진입/monotone 판정/finalize watermark)만 trace. |
| **⚠ 핵심 트레이드오프 (검증 지적)** | extract_claims에 enum을 강제하면 **`checker.compare`의 "기타(매핑불가)→semantic 위반 surface" 안전장치가 무력화될 위험.** 모델이 '진홍색'을 강제로 '붉은색'에 욱여넣으면 진짜 모순을 놓친다. → **'기타' 탈출구를 enum에 보존**하고, enum 강제 vs 탈출구의 손익을 **M1에서 측정**해야 한다. |
| **무시 시 심각도** | 중간. 폴백이 조용하면 분해 실패 원인을 사후 추적 불가. |

---

## 4. REJECT & cargo-cult 경계 — 트렌디하지만 우리엔 과한 것

> 공통 근본 결함: **근거의 순환성.** 전부 미구현 PRD를 인용해 새 인프라를 정당화한다. 위키 계열은 트리거(finalize 팬아웃 다중 컨슈머)부터 없고, 이벤트소싱/saga는 DB·분산 step부터 없다.

| 패턴 | 판정 | REJECT 이유 (cargo-cult 경계) | 언제 다시 보나 |
|---|---|---|---|
| **memory1 / harness7** 작품 바이블 위키 승격 (*중복 재제출*) | REJECT | 승격 대상 finalize 3번 컨슈머가 코드에 없음(`rag.index_chapter` 단일 호출이 전부). HierarchicalSummary/trust_tier/워터마크 전부 부재 = 신규 백본. 5화엔 비용곡선 미발생, 손익분기도 모른다고 자인. **memory1=harness7은 동일 제안 중복 → 다중렌즈 합의 착시.** | M3에서 재합성 토큰 계측 후, 인물 페이지 1종 plain markdown. 단 finalize 다중 컨슈머가 M2에 실재해야. |
| **memory2** 타입드 wikilink 복선 그래프 | REJECT | PlotThread/payoff_check가 PoC에 0. orphan lint는 **빈 그래프 순회.** 환각 엣지 위험 자인. 복선은 100~200화 명제이지 5화 검증 대상 아님. | M5 PlotThread 실재 후 payoff_of 엣지 1종 + 결정론 orphan lint. 미회수=경고이지 차단 아님. |
| **memory4** 위키 lifecycle 5상태 | REJECT | 5상태 머신을 5화에 도입 = 상태 폭발. trust_tier/막경계/auto_advance·contradicted 트리거가 전부 0이라 **채울 상태가 없다.** | M3 이후 draft/active 2상태 + stale 워터마크 비교 한 줄. |
| **memory5** index.md 단일 writer governance | REJECT | writer_lock이 PoC에 없음. 단일 작가·단일 프로세스라 **split-brain 발생 자체가 불가** → 막을 대상 없음. 페이지 0개에 Karpathy "임베딩 불필요" 휴리스틱은 측정 불가. | 위키 도입 시 index.md 단순 테이블 자동생성, lock 불필요. |
| **harness4** Hibernate-wake 체크포인트 | REJECT | 복원 부재는 사실이나 **PoC 목적이 아님**(검증기+RAG+자가교정 동작 증명 단발 스크립트). 장기 재개는 M2 Temporal 책임. JSON 체크포인트 선투자는 검증 목적 없음. | M2 Temporal 실재 시 author_observed 플래그 + wake 재확인 고지. |
| **validation2** Append-only 이벤트 로그(Event Sourcing) | REJECT | **PoC에 DB가 없음** — 상태는 인메모리 dict + report.json. chapter_event PG/CQRS/prev_event_hash 서명은 단발 스크립트에 과설계. `rounds`가 이미 이벤트 시퀀스로 충분. | M2 PG 실재 시 insert-only 감사 테이블 + SQL fold 뷰. |
| **validation3** Saga 보상 트랜잭션 | REJECT | finalize 팬아웃 자체가 없어 **보상할 분산 step이 0.** 단일 프로세스·DB 부재 → compensation 적용 대상 없음. 모놀리스에 마이크로서비스 어휘 이식. | M2 다중 컨슈머 분리 시, **외부·비ACID 자원(임베딩 색인) 1개에만** 멱등키+롤백. 온톨로지 반영은 로컬 트랜잭션. |

**cargo-cult 명명 인플레이션 적발:** "blackboard"(60년대 AI 용어로 Pydantic 3필드를 포장), "distributed saga"(모놀리스에 마이크로서비스 어휘), "event sourcing PG + 서명 체인"(DB도 없는데). 정당한 본질만 남기고 명명은 버린다.

---

## 5. 통합 청사진 — 강화 아키텍처 한 장 요약

### 5.1 계층 다이어그램 (ADAPT만 반영, 위키층은 점선=미래)

```
┌────────────────────────────────────────────────────────────────────────┐
│ [외부 검증 백본]  eval/ 패키지 = run_poc C0~C6 영속화 (3.4)               │
│   binding: 결정론·준결정론 + silent_ship=0 + clear재현성100%             │
│   non-binding: semantic / LLM-judge(게이트 입력 금지)                     │
└───────────────────────────────▲────────────────────────────────────────┘
                                 │ VerdictRecord / 행동 측정
┌────────────────────────────────┴───────────────────────────────────────┐
│ [계층B 결정론 오케스트레이터]  GraphState (Pydantic, 3.3)                 │
│                                                                          │
│   ContextBoard ─ 타입 분리 강제 (3.3)                                    │
│   ┌──────────────┬───────────────────┬──────────────────────────────┐  │
│   │ authority    │ ground_truth      │ narrative                    │  │
│   │ Directive[]  │ OntologyFact[]    │ RagChunk[] (cap·trust_weight)│  │
│   │ (상단)       │ (상단·누락0)      │ (하단)  ← 멱등 색인 (3.2)     │  │
│   └──────────────┴───────────────────┴──────────────────────────────┘  │
│         │ assemble_context() 단일 직렬화 (위치 결정론 고정)              │
│         ▼                                                                │
│   [장면 미니루프] for scene in scene_plan (3.5, M2):                     │
│     draft_scene → check(scene) → if critical: partial_rewrite           │
│                                   → seam_reweave → 재검증                │
│     per-scene best_so_far monotone 가드 (결정론 위반 카운트 1급 축)      │
│         │                                                                │
│         ▼  signal_grade: Literal[det|quasi|semantic] (3.3)              │
│   [결정론 룰 엔진] RuleSpec 순회 (3.1) ── predicate 4종 닫힌 enum         │
│         │  그 밖 → semantic 강등 → judge+escalation                      │
│         ▼                                                                │
│   [구조화 출력] extract_claims = Literal enum + '기타' 탈출구 (3.6)      │
│   [관측 훅] emit(node,event_type,payload) + failure_mode enum (3.6)      │
└──────────────────────────────────────────────────────────────────────── ┘
        ┊ (미래 M3+) LLM Wiki 기억층 — finalize 다중 컨슈머 실재 후에만 ┊
        ┊  인물 페이지 1종 → typed 엣지 → lifecycle → governance        ┊  ← REJECT(현 단계)
```

### 5.2 "왜 트렌디 × 왜 정당" 교차 요약표

| 강화 | 왜 트렌디한가 (2026) | 왜 정당한가 (우리 규모) | 실재 결함 위치 |
|---|---|---|---|
| 룰 테이블 (3.1) | 룰을 스키마로, linter 외부검증 1급 | PoC가 자백한 부채, 규칙 2개째 깨짐 | `checker.py` rule_no_reawaken |
| 멱등 색인 (3.2) | durable 도구 멱등성 | **실제 버그** — append 중복 | `rag.py` index_chapter |
| 타입드/ContextBoard (3.3) | 타입드 핸드오프, intent>syntax | RAG가 ground truth 슬롯에 새는 회귀 차단 | `harness.py` draft_chapter 문자열 병합 |
| eval 게이트 (3.4) | unit-test형 eval, self-eval 한계 대응 | 이미 90% 구현, 영속화만 | `run_poc.py` C0~C6 |
| 장면 미니루프 (3.5) | decoupled sub-task, harness over model | 통짜 재생성=과교정·토큰폭발 | `harness.py` 통짜 루프 |
| 구조화+관측 (3.6) | decode-time 보장, decision-flow 캡처 | 조용한 정지 가시화 | `plan_scenes` except 폴백 |

---

## 6. 적용 로드맵 — M0~M3에 끼우기

| 마일스톤 | ADAPT 패턴 | 근거 / 게이트 |
|---|---|---|
| **M0~M1 (현 PoC, 승인 시 즉시)** | **3.2 멱등 색인** (버그, 3~4줄) · **3.6 구조화+관측** (extract_claims enum + emit/JSONL) | 작고 load-bearing. 3.6의 enum '기타' 탈출구 손익을 **M1에서 측정**(GO 게이트 데이터). |
| **M1 (구조 동결 게이트)** | **3.1 룰 테이블** · **3.3 타입드/ContextBoard** | 둘 다 구조 리팩터 → **사용자 승인 필수.** M1 GO 게이트 "결정론 산출물 스키마 동결"을 직접 구현. 룰 테이블은 M4 worldgen 전 필수이나 enum 4종을 M1에 동결. |
| **M1 (eval)** | **3.4 eval 게이트 영속화** | `run_poc.py`를 `eval/` 패키지 + CI 게이트로. binding 임계는 데이터 보기 전 동결(§14.2). |
| **M2 (2계층 골격 등장)** | **3.5 장면 미니루프** | scene_plan이 진짜 노드가 되는 시점. 체크포인트·인터럽트·국소 monotone의 전제. |
| **M3 (30~50화 실증, 데이터 후)** | (위키 재검토 시작점) | 재합성 토큰 계측 → 손익분기 확인 후에만 인물 페이지 1종 plain markdown. **finalize 다중 컨슈머가 M2에 실재해야 함이 선결.** |

**로드맵 1급 원칙:** 위키 5종·이벤트소싱·saga·hibernate-wake는 전부 "토대가 실재한 다음"으로 미룬다. M0~M1에서 토대 없이 부트스트랩하면 split-brain·토큰 폭발만 들인다.

---

## 7. 열린 질문 (M1 측정 또는 설계 동결 필요)

1. **enum 강제 vs '기타' 탈출구 (3.6, 최우선 측정):** extract_claims에 Literal enum을 강제하면 `checker.compare`의 "기타→semantic 위반 surface" 안전장치가 무력화될 수 있다('진홍색'이 '붉은색'으로 오정규화). enum에 '기타'를 포함하는 것으로 충분한가, 아니면 enum 강제 자체를 추출이 아니라 검증 단계로 미뤄야 하는가? → **M1에서 실측.**

2. **RuleSpec predicate enum 확장 경계 (3.1):** 4종 닫힌 enum(categorical_eq/numeric_monotone/timeline_state/worldrule_flag)으로 닫으면, 회귀물(timeline_branch_id)로 확장 시 "분기 타임라인 간 모순" 신규 predicate가 enum을 깨뜨린다. **enum 확장 정책 vs semantic 강등 경계를 어디서 동결**하나?

3. **ContextBoard 우선순위 차원 (3.3):** ground_truth_slot(누락0)과 authority_slot(AuthorDirective)가 충돌할 때(작가 자기모순) 둘 다 "상단"인데 우선순위를 타입 시스템이 표현할지, 런타임 detect_directive_conflict에 위임할지 — 핸드오프 계약의 우선순위 차원이 미정의.

4. **ground_truth_slot의 사각지대 소유 주체 (3.3):** "누락0 어서션"은 **온톨로지에 있는 것의 누락0만** 보장한다. 온톨로지에 애초에 없는 사실(본문↔본문 모순, run_poc가 자백한 사각지대)은 board 밖이다. 이 사각지대의 소유 주체를 누구로 둘지 — 검증 백본의 미해결 공백.

5. **eval 게이트 재현성 임계 동결 (3.4):** binding 임계(clear=100%)는 PoC n=10에서 성립했으나, §14.4가 지적하듯 N=10이 monotone 판정 안정성을 통계적으로 보증하는 검정력이 불확실. "임계를 데이터 보기 전 동결"(§14.2)과 "N 검정력 부족" 사이에서 **초기 게이트를 어떤 보수적 값으로 동결**할지.

6. **장면 미니루프 seam vs 미래 author_observed (3.5):** 장면 단위 seam_reweave가 인접 장면을 수정할 때, 그 인접 장면이 (미래 M2의) author_observed 상태라면 hibernate-wake 재확인 게이트와 충돌. 현 단계엔 무관하나 **M2 착수 전 경계 정의 필요**(author_observed 장면은 seam에서 freeze할지).

---

> **최종 한 줄:** 트렌디함의 80%는 "지금 우리 코드에 토대가 없다"는 이유로 미루고, 검증을 통과한 6건의 ADAPT — 그중에서도 자백된 부채(룰 테이블)와 실제 버그(멱등 색인), 그리고 등급 누수를 막는 타입 경계 — 만 "구조적으로 정당하다." 구조 리팩터 2건(3.1·3.3)은 사용자 승인 후 진행한다.