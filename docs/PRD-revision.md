# 퇴고 (회차 본문 사후 다듬기) — MVP 확정 PRD (v2, 적대검증 반영)

> 상태: **확정(buildable)**. 적대검증 `needs-rework` 의 high/med gap 을 전부 반영해
> 사실불변 가드레일의 **레이어 정직성**, **LLM 콜 결정성**, **요약/RAG 동기화 비대칭**, **사실변경 패스 제외**를
> 명세에 못박았다. 코드 근거는 인라인 파일:라인으로 표기.

---

## 1. 문제 (problem)

코파일럿에는 집필(새 회차 생성)·회고(미래 설계 수정) 두 축만 있고, 이미 FINALIZED/ESCALATED 된 회차의
**산문 자체**를 작가 지시로 사후에 다듬는 세 번째 축이 없다. 기존 6종 산문 교정기
(`_rewrite/_reformat/_fix_tense/_continuity_polish/_fix_tics/_regen_tail`, `engine/harness.py`)는
**생성 루프 안에서만 자동으로** 돌고, 작가가 완성본을 골라 의도적으로 지시할 진입점·후보 비교·되돌리기가 없다.
`ChapterRecord`(`domain/types.py:93-119`)에는 버전·이력 필드가 전무하고(최신본 1개),
`AuthorDirective`(`types.py:48-52`)는 `from_chapter` 기반 **미래 지시**만 가능하다.

핵심 제약은 **사실 불변** — 표현·문체만 바꾸고 캐논은 불변임을 가드레일로 검증해
RAG/온톨로지 대규모 재동기화를 피한다.

### 1.1 사실불변의 정직한 재정의 (★ 적대검증 high #1·#9 반영)

적대검증이 옳다: **`ontology_internal_check`(`checker.py:59`, `ontology.py:232`)는 SSOT(timeline/edges)만 보고
prose 를 입력으로 받지 않는다.** 퇴고는 `ontology_updater` 를 실행하지 않으므로 SSOT 가 안 변하고,
따라서 그 델타는 **항상 0** 이다. prose 에 반응하는 층은 LLM extractor 의 QUASI 클레임
(`checker.py:55-58`, `extractor.extract_full`)뿐이다.
그러므로 PRD v1 의 등식 **"신규 하드 0 = 사실 불변"은 거짓**이다 — 캐논에 *없던* 새 사실을 단정해도
SSOT 모순이 0이라 통과할 수 있다.

따라서 본 PRD 는 가드레일의 약속을 **정직하게** 다음 두 축으로 분리한다:

- **G-A (기존 캐논과의 신규 충돌 차단)** — `check_text` 의 신규-하드-델타. 퇴고가 *기존* 캐논 사실/관계와
  **모순되는 새 단정**을 넣으면 차단. (예: 캐논이 '동맹'인 둘을 '원수'로 단정 → `relation_contradiction`.)
  이건 실제로 작동하는 영역이다.
- **G-B (본문 사실 표면의 직접 보존)** — before/after **클레임 값 비교**. extractor 가 추출한
  `canon_facts/canon_relations` 표면값(인명·수치·상태·관계)이 before↔after 에서 **변하면 플래그**.
  이게 "캐논에 없던 새 사실 추가/기존 수치 변조"를 잡는 **유일하게 정직한** 메커니즘이다.

> 결론: 사실불변 가드레일 = **G-A(하드 델타) AND G-B(클레임 표면 델타)**. G-B 가 v1 의 거짓 등식을 대체한다.
> 이 정의로 "톤/간결화 지시"(사실 안 건드림)에서도 G-B 가 능동적으로 "값이 안 바뀌었음"을 *증명*하므로
> 가드레일이 무의미해지지 않는다(적대검증 low #9 응답).

---

## 2. 포크 결정 (forksResolved) — v1 유지 + 적대검증 반영 수정분

| Fork | 결정 | 비고 |
|---|---|---|
| F1 구간 타겟 | 자유지시 + 선택적 `span_text`(원문 부분문자열) | 유지 |
| F2 퇴고 단위 | 구간/전체 둘 다, 저장은 항상 회차 전체 텍스트 | 유지 + **경계윈도 보강**(§7 med #6) |
| F3 가드레일 강도 | 하드 차단 + **클레임 표면 차단** 추가 | ★수정: G-B 추가 |
| F4 버전·undo | append-only `revisions` 무제한 + 마지막 1건 즉시 undo | 유지 + **undo 부수효과 복원 명시**(§7 med #8) |
| F5 diff UX | 인라인 단어-LCS 기본 + 토글 side-by-side | 유지 |
| F6 UI 진입점 | 작업실 리더 회차 헤더 버튼 1개 | ★수정: **FINALIZED + ESCALATED 둘 다**(med #7) |
| F7 동시성/저장 | `sess.lock` 안에서 repo 재읽기→수정→save | 유지 |
| F8 LLM 호출 | 후보 생성 동기 POST(SSE 아님) | ★수정: **콜 수 못박음**(§4, high #3) — before 1콜은 후보생성 때만, accept 는 after 1콜만 |

### 2.1 적대검증으로 새로 확정된 결정

- **D1 (high #4) 사실변경 패스 1차 제외.** `_continuity_polish`(`harness.py:210-236`)는
  '다섯 겹→셋' 수량 변경·중복사건 단락 치환을 **명시 수행**하고,
  `_regen_tail`(`harness.py:287-304`)은 말미 1~2문단을 `temperature=0.8` 로 **통째 재작성**(새 훅=새 사건)한다.
  → 둘 다 **사실 변경 패스**이므로 1차 opt-in 토글에서 **제외**. 노출하는 선택 교정은
  **`_reformat`(공백/행갈이만, 코드검증)·`_fix_tense`(종결어미만)** 둘로 한정.
  (`_fix_tics` 는 '인명·사건·수치 불변'을 프롬프트로 약속하나 LLM 자유치환이라 1차에선 **제외**, P5 실증 후 후속 검토.)
- **D2 (high #2) `check_text` 는 standalone 아님.** 시그니처
  `check_text(text, ontology, chapter, involved_ids)`(`checker.py:54`)에 살아있는 `ontology`·`involved_ids` 필수.
  퇴고엔 beat 가 없다. → **before/after 에 동일한 고정 `involved_ids` 강제**
  (= `회차_원본.scan_present_ids() ∪ span 관련 ids`를 **한 번** 계산해 before·after 양쪽에 같은 집합 주입).
  v1 의 'standalone 호출 가능' 주장은 **정정**(아래 §3·§4 에 반영).
  주의: `extract_full` 이 내부에서 `involved_ids + scan_present_ids(text)` 합집합을 다시 만들므로
  (`extractor.py:75`), after 텍스트에 새 엔티티명이 등장하면 id 집합이 커진다 →
  이 비대칭이 거짓 신규위반의 구조적 원인. G-B(클레임 표면 비교)는 **before 에 존재한 엔티티 키로 교집합**해
  비교하여 이 비대칭을 흡수한다(§3.3).
- **D3 (high #3) LLM 콜 결정성.** before 체크는 **후보 생성 시 1회만** 계산해 후보 캐시에 동봉.
  accept 는 **after 만** 재추출(또는 캐시 신뢰). temp=0.0 도 완전 결정은 아니므로
  "후보 통과인데 accept 409" 산발을 막기 위해 **accept 의 가드레일 재검증은 캐시된 before 기준선을 신뢰**하고
  after 만 재계산한다. 멀티워커 `after_text` 폴백 경로는 §5 에서 before 기준선까지 폴백시키지 않도록 명시.
- **D4 (high #5) 요약/위키/RAG 동기화 정책 명시.** 아래 §6.

---

## 3. 가드레일 명세 (guardrailSpec) — ★전면 개정

### 3.1 involved_ids 고정 (D2)

```
ids = sorted(set(ontology.scan_present_ids(before_text)))     # 회차 원본 기준 1회
# span 모드여도 before_text 전체로 스캔(구간만 보내면 등장인물 누락)
```
이 `ids` 를 before·after **양쪽 check_text 에 동일하게** 넘긴다.

### 3.2 G-A: 신규 하드 델타 (기존 캐논과 신규 충돌 차단)

```
before_res = checker.check_text(before_text, ontology, chapter, ids)   # 후보생성 시 1회
after_res  = checker.check_text(after_text,  ontology, chapter, ids)
new_hard = { (v.entity, v.kind) for v in after_res.hard }
         - { (v.entity, v.kind) for v in before_res.hard }
G_A_passed = (len(new_hard) == 0)
```
`before` 에 이미 있던 하드 위반은 퇴고 책임이 아니므로 무시.
(ontology_internal_check 델타는 SSOT 불변이라 항상 0 — G-A 의 실효 신호는 quasi `relation_contradiction`·
rule_engine quasi 위반에서 온다. 이를 **정직하게 문서화**.)

### 3.3 G-B: 클레임 표면 델타 (본문 사실 직접 보존) — ★신규, v1 거짓등식 대체

before/after 각각의 `CheckResult.claims`(extractor 정규화 엔티티 클레임)에서
**캐논성 키**(numeric_keys ∪ categorical_keys ∪ state keys, evidence 통과분)만 뽑아
(entity_id, key) → value 맵을 만든 뒤 **before 에 존재한 (entity,key) 교집합**에서 값 변화를 검사:

```
before_map = claim_surface(before_res.claims)   # {(eid,key): value}
after_map  = claim_surface(after_res.claims)
changed = [(k, before_map[k], after_map[k])
           for k in before_map if k in after_map and after_map[k] != before_map[k]]
G_B_passed = (len(changed) == 0)
```
- **교집합만 비교**: after 에만 새로 생긴 키(표현 변경으로 추출기가 새로 잡은 것)는 *추가* 단정일 수 있으나,
  before 에 없던 키이므로 "기존 사실 변조"는 아님 → G-B 는 **변조(값 바뀜)** 만 하드 차단,
  **신규 키 출현**은 advisory 플래그(가시화, 비차단)로 분리. 이유: extractor 비결정으로 신규 키가 노이즈일 확률이 높고,
  진짜 새 사실 단정은 보통 *기존 캐논과 충돌*해 G-A 에서 걸린다. (P5 에서 거짓양성률 실측 후 임계 조정.)
- 비교는 **정규화 토큰 값** 기준(통제어휘 대표토큰). 표면 표현 변화는 같은 토큰으로 정규화되므로 흡수.

### 3.4 길이 가드

`len(after) < len(before)*0.5` 또는 `> len(before)*1.8` 이면 거절(`_rewrite` 의 0.6 가드 정신 계승).
span 모드면 **치환 후 전체 텍스트** 길이로 판정.

### 3.5 span 정확일치 가드

`span_text` 가 **공백 collapse 정규화 후** 원문에 정확히 1회 매칭되지 않으면 400 거절.
원본 오프셋 보관 메커니즘은 §7 med #6.

### 3.6 최종 판정·거절 방식

```
guardrail.passed = G_A_passed AND G_B_passed AND length_ok
```
- 후보는 **항상 만들되**, `passed=false` + 사유(new_hard / changed 목록 / 길이)를 반환.
  diff 는 보여주되 **채택 버튼 비활성**(가시화·조용한 정지 금지).
- **accept API 는 서버에서 가드레일 재검증**(클라이언트 불신). 실패면 **409**.
- **우회 금지**: G-A/G-B 임계를 설정 플래그로 노출하지 않고 '무시하고 적용' 경로를 두지 않는다.
  사실 불변 = 기능 정체성(CLAUDE.md). '사실 변경 허용' 모드는 명시적 scope-out(§9).

---

## 4. LLM 콜 예산 (★ high #3 — 못박음)

| 단계 | 콜 | 내용 |
|---|---|---|
| 후보 생성 `/revise` | 1콜 | `revise_prose` 다듬기(directive+사실불변 프롬프트) |
| 후보 생성 `/revise` | 1콜 | `extract_full(before_text)` → G-A/G-B before 기준선 (**후보 캐시에 동봉**) |
| 후보 생성 `/revise` | 1콜 | `extract_full(after_text)` → after 결과 |
| 선택 교정 토글 ON | 0콜 | `_reformat`(코드만)·`_fix_tense`(현재형 누출 있을 때만 ≤1콜) |
| 채택 `/accept` | 1콜 | `extract_full(after_text)` 재추출(또는 캐시 신뢰 시 0콜). before 는 **캐시 신뢰**(재추출 금지) |

→ 후보 생성 **최대 3콜**, accept **최대 1콜**. F8 의 '수초' 충족.
`_fix_tense` 는 결정론 `tense_leak_ratio>0.05` 게이트로만 발동(`harness.py:475`) → 대부분 0콜.

---

## 5. 데이터 모델 (dataModel)

`domain/types.py` 에 신규 모델 추가:

```python
class ChapterRevision(BaseModel):
    revision_id: str
    directive: str
    span_text: str = ""
    before_text: str
    after_text: str
    passes_used: list[str] = Field(default_factory=list)   # ["reformat","fix_tense"]
    violations_before: list[Violation] = Field(default_factory=list)   # 하드만
    violations_after: list[Violation] = Field(default_factory=list)    # 하드만
    claim_changes: list[dict] = Field(default_factory=list)            # G-B: [{entity,key,before,after}]
    guardrail_passed: bool = False
    guardrail_reason: str = ""
    reverted: bool = False
    created_at: str = ""   # 파이썬 서버 time.strftime — 클라이언트 생성 금지
```

`ChapterRecord` 에 1줄 추가:
```python
revisions: list[ChapterRevision] = Field(default_factory=list)
```

- 회차 본문은 여전히 `ChapterRecord.text` **단 하나**(최신본). 버전 모델 단순화.
- 이력은 **가산만(append)**. accept 시 새 `ChapterRevision` push.
- **undo**: 새 레코드를 만들지 않고 **마지막 비-reverted 레코드**를 `reverted=true` 마킹 + `text=before_text` 복원.
- 모든 신규 필드 default 보유 → **구 JSON 무중단 마이그레이션**(repo.save 원자성 그대로, `filesystem.save()`).
- **휘발 후보 캐시**: `_revise_drafts: dict[revision_id, dict]` (TTL, `_drafts` 패턴 차용 `copilot.py:183-220`).
  캐시에 `before_text/after_text/before_res(하드+claims)/ids/passes` 동봉.
  **멀티워커 폴백(high #3)**: accept 가 다른 워커로 가 캐시 미스면 req 의 `after_text` 폴백을 쓰되,
  **before 기준선은 폴백시키지 않는다** — 캐시 미스면 서버가 `before = 현재 repo 의 chapter.text` 로 재계산
  (현재 본문 = 아직 채택 전이므로 before 와 동일). 즉 before 는 항상 '현재 저장된 본문'에서 도출 가능.

---

## 6. 요약/위키/RAG 동기화 정책 (★ high #5 — 명시)

생성 finalize 는 `rag.index_chapter`(`harness.py:489`) + `wiki.ingest_chapter`(492) +
`_summarize`(498, summary+detail_synopsis) 를 모두 돈다. 이 산출물이 후속 `story_so_far`
(`copilot._build_story_so_far`, `detail_synopsis or summary or text[:120]`, `copilot.py:43`)로 미래 회차에 주입된다.
**본문을 고쳤는데 요약을 안 고치면 옛 사건이 미래 회차에 계속 주입**된다.

확정 정책:

1. **RAG 재색인: 채택 시 1회.** `rag.index_chapter(n, after_text)` (멱등 — 동일 chapter 청크 제거 후 재삽입,
   `rag.py:40-43`). 후보 생성 땐 **안 함**(재색인 비용 제한, openRisks).
2. **요약 재생성: 채택 시 조건부 1회.**
   - **사실불변이 보장**되므로(G-A∧G-B) 사건/인과/수치는 안 바뀐다 → detail_synopsis 의 *사건 골자*는 유효.
   - 그러나 표현이 바뀌면 요약 표면이 미세하게 stale 할 수 있다. **비대칭(RAG만 갱신, 요약 미갱신)을 제거**하기 위해
     **채택 시 `_summarize(after_text, prior_summary)` 1회 재호출**해 summary/detail_synopsis 동기화.
     (콜 1회 추가 = §4 의 accept 예산에 포함되지 않은 별도 1콜 — accept 총 최대 2콜로 정정.)
   - 단, 이 호출은 **사실을 새로 만들지 않는다**(요약은 narrative, SSOT 아님). 안전.
3. **위키: 채택 시 재수집 안 함(1차).** `wiki.ingest_chapter` 는 narrative(비구속, `harness.py:493` 주석)이고
   사실불변이라 위키 페이지의 *사실*은 동일. 표현 stale 은 advisory 수용(scope-out 아님, **명시 부채**로 기록).
   P5 에서 위키 stale 영향 실측.
4. **`ontology_updater`: 실행 안 함.** 사실불변이므로 SSOT 변경 없음 — 실행하면 오히려 잘못된 델타 위험.

### 6.1 undo 부수효과 복원 (★ med #8)

undo 시:
- **RAG 재색인 복원**: `rag.index_chapter(n, before_text)` 재호출(멱등). **필수**.
- **요약 복원**: undo 가 복원하는 before_text 가 *직전 채택 이전*의 본문이므로,
  그 시점의 summary/detail_synopsis 를 `ChapterRevision.before_text` 와 함께 **스냅샷으로 보관**해 복원하거나,
  `_summarize(before_text)` 1회 재호출. → **before 스냅샷 보관 방식 채택**(콜 0, 결정론):
  `ChapterRevision` 에 `before_summary`/`before_detail_synopsis` 도 저장(채택 시점에 기존 값 캡처).
- **측정필드(reader_feedback/drift_signals/pacing, `copilot.py:769-788`)**: advisory(비구속)이므로
  **미복원 명시** — 다음 회차 생성/회고 시 자연 갱신. UI 에 "퇴고 후 독자반응 지표는 재측정 전까지 이전 값" 주석.

---

## 7. 구간(span) 경계 처리 (★ med #6)

1. **앵커 매칭**: `span_text` 와 원문 모두 공백 collapse(`\s+`→` `) 정규화 후 매칭.
   정규화본에서 1회 매칭되면 **원본 오프셋(start,end) 을 정규화 전 위치로 역산**해 보관.
2. **경계 윈도**: 구간만 LLM 에 보내면 양끝 문장이 잘려 접합부 조사/접속 불일치 발생.
   → 앞뒤 문맥 윈도(각 ~200자)를 **함께** LLM 에 주되, "다듬을 대상은 [[...]] 표시 구간만, 나머지는 그대로 출력" 지시.
   LLM 출력에서 표시 구간만 추출, **보관한 원본 오프셋 위치에 replace**.
3. **경계 이탈 방지**: LLM 이 경계를 넘어 다듬으면 표시 구간 추출 실패 → 그 경우 **전체 모드로 폴백 안 하고**
   400/안내("구간 다듬기 실패, 지시를 회차 전체로 적용하시겠습니까?")로 작가 재선택.

---

## 8. API 설계 (apiDesign)

| Method | Path | 목적 | req → resp |
|---|---|---|---|
| POST | `/api/projects/{pid}/chapters/{n}/revise` | 후보 생성(저장 안 함) | `{directive, span_text?, passes?}` → `{revision_id, before_text, after_text, span_text, guardrail:{passed, new_hard, claim_changes, reason}, passes_used}` |
| POST | `/api/projects/{pid}/chapters/{n}/revise/accept` | 후보 채택→저장(서버 가드레일 재검증·RAG 재색인·요약 재생성) | `{revision_id}` 또는 폴백 `{after_text, span_text?, passes?}` → `{accepted, chapter, revision_count}`; 가드레일 실패 **409** |
| POST | `/api/projects/{pid}/chapters/{n}/revise/undo` | 마지막 채택 되돌리기(before_text·요약·RAG 복원) | `{}` → `{reverted, chapter, revision_id}` |
| GET | `/api/projects/{pid}/chapters/{n}/revisions` | 퇴고 이력(읽기전용) | → `{revisions:[{revision_id, directive, created_at, reverted, guardrail_passed}]}` |

- **대상 상태**: FINALIZED **및 ESCALATED** 회차 모두 허용(F6 수정, med #7).
  ESCALATED 회차는 before 에 이미 하드 위반이 있으므로 G-A 의 "before 에 있던 하드는 무시" 규칙이 그대로 적용 —
  퇴고가 **새 위반을 더하지 않는 한** 허용(오히려 손질 가장 필요한 대상). ESCALATED 는 status 유지(퇴고가 escalate 해소를
  보장하지 않음; 사건 변경 불가하므로). 단 after 가 우연히 하드 0 이 되면 FINALIZED 승격 허용.
- 404(프로젝트/회차 없음), 400(span 불일치·directive 빈값), 409(accept 재검증 실패), 423(락 점유).

---

## 9. UI 설계 (uiDesign)

- **진입점**: 작업실 리더(`#sec-write` → `#chapter-body`) 회차 헤더의 '퇴고' 버튼(FINALIZED·ESCALATED 회차).
  몰입형 뷰어(`#view-viewer`)는 1차 제외(DESIGN: 독자 시뮬레이션 크롬 숨김).
- **용어 §5 준수**: '퇴고'·'공식 설정'·'작가 지시'만. 내부 용어(온톨로지/checker/violation) 노출 금지.
  가드레일 사유도 작가 언어로: "이름/수치가 바뀌었습니다", "기존 설정과 충돌하는 표현이 생겼습니다".
- **퇴고 모달(openModal)** 흐름:
  1. 지시 입력 textarea + 예시 칩('더 간결하게'/'대사 톤 차갑게'/'묘사 줄이기') + 선택적 다듬을 구간(붙여넣기→span_text)
     + opt-in 토글 **연속성·반복·시제 중 행갈이/시제 2개만**(연속성·말미·틱 토글 **제거** — D1).
  2. 후보 생성 `POST /revise`.
  3. before→after diff **인라인 하이라이트**(JS 단어 LCS, 삭제=`--bad-soft` 취소선/추가=`--ok-soft`) + 토글 나란히 보기.
     본문은 명조 리더 톤(§5 보존).
  4. **가드레일 배지**: 통과=초록 pill '설정 그대로 유지됨' / 실패=빨강 pill + 사유(바뀐 이름·수치 목록) + **채택 비활성**.
     G-B 의 advisory(신규 키 출현)는 노랑 pill '새 표현 추가됨(설정 변경 아님)'으로 분리 표시(비차단).
  5. **채택**(1차 잉크 버튼)→`POST /accept`→STATE 갱신·`renderReader`·toast / **취소**(2차 아웃라인)→`closeModal`·후보 폐기.
  6. **되돌리기 링크**(undo).
- **app.js 패턴 준수**: `api.post`/`openModal`/`closeModal`/`toast`/`esc`/`cssVar`. diff 는 **의존성 0 순수함수**.
- **style.css**: `.diff-del`/`.diff-add` 2개만 추가. 기존 모달·칩·토큰 재사용. Esc 취소(기존 트랩).

---

## 10. 단계 (phases)

1. **P1 데이터·엔진**: `domain/types.py` 에 `ChapterRevision`(+`before_summary`/`before_detail_synopsis`) +
   `ChapterRecord.revisions`. `ChapterGenerator.revise_prose(directive, before_text, span_text?, passes?, ids, ontology)`
   신설 — 다듬기 1콜(사실불변 프롬프트·길이 가드) + 선택 `_reformat`/`_fix_tense` 만.
   `python -c "import novelcopilot.main"` 자가검증.
2. **P2 가드레일·서비스**: `CopilotService.revise_chapter`/`accept_revision`/`undo_revision` +
   `_revise_drafts`(TTL). **involved_ids 고정(D2)**·**G-A 신규하드델타**·**G-B 클레임표면델타**·
   **서버 재검증**·**accept 시 RAG 재색인+요약 재생성**·**undo 시 RAG/요약 복원**. 멀티워커 before 폴백(현재 본문 도출).
3. **P3 API**: `schemas.py` DTO(`ReviseRequest`/`ReviseAcceptRequest`/`ReviseResponse`) + `routes.py` 4개 라우트.
   FINALIZED+ESCALATED·404·400/409/423 처리.
4. **P4 프론트**: 퇴고 버튼·모달·인라인 diff·가드레일 배지(통과/실패/advisory 3종)·채택/취소·undo.
   `style.css` diff 클래스 2개. `node --check app.js`.
5. **P5 검증**:
   - **사실불변 시나리오**: 인명 변경 지시('주인공 이름을 X로')→**G-B 차단**, 수치 변경('산소 3시간→8시간')→**G-B 차단**,
     기존 동맹을 원수로 단정→**G-A 차단**.
   - **한계 실증(정직성)**: "캐논에 *없던* 새 사실 단정"(G-A 통과·G-B 신규키 advisory) 케이스로 **가드레일 한계 문서화**.
   - **거짓양성 실측**: 톤/간결화 지시 N건에서 G-B `claim_changes` 거짓양성률 측정(목표 <5%). 과하면 정규화 토큰 비교
     정밀화(검출기 신설 금지 — 비교 로직 보강만).
   - **구간 모드**: 1회 치환·경계윈도 접합·경계이탈 폴백.
   - **undo 왕복**: text/요약/RAG 3종 복원 확인.
   - **구 JSON 로드 호환**: revisions 없는 기존 프로젝트 로드→빈 리스트.

---

## 11. Scope-out (scopeOut)

- 다단(N단계) undo/redo UI·버전 트리 탐색
- 몰입형 뷰어(`#view-viewer`)에서의 퇴고 진입
- 여러 구간 동시 퇴고·배치 퇴고·작품 전체 일괄 퇴고
- **'사실 변경 허용' 모드(가드레일 완화) — 금지**
- LLM 스트리밍(SSE)으로 후보 점진 표시
- 작가 지시 없이 AI 가 다듬을 곳 추천하는 자동 제안
- **`_continuity_polish`·`_regen_tail`·`_fix_tics` opt-in 노출(D1, 사실변경 패스)** — 1차 제외
- 위키 페이지 표현 재동기화(명시 부채로 수용, P5 영향 실측 후 후속)

---

## 12. Open Risks (openRisks)

- **G-B 거짓양성**: extractor 비결정으로 before↔after 추출 클레임 키가 흔들려 거짓 `claim_changes` 발생.
  → before 교집합 비교 + 정규화 토큰값 비교로 흡수했으나 P5 실측 필수. 과하면 정밀화(검출기 남발 금지).
- **G-A 실효 신호 빈약**: ontology_internal_check 는 항상 0, quasi relation_contradiction 만 능동 →
  "기존 캐논과 충돌하는 새 단정"이 관계가 아닌 **속성**일 때 G-A 가 비고 G-B(신규키 advisory)로 떨어질 수 있음.
  이건 §1.1 의 정직한 한계 — P5 한계 실증으로 문서화하고 작가에게 advisory 노출.
- **구간 앵커 정확일치 실패**: 렌더 본문(`<br>`/문단분할)에서 복사 시 공백 상이 → 공백 collapse 정규화 매칭 +
  실패 안내. 원본 오프셋 보관(§7).
- **휘발 캐시 vs 멀티워커**: accept 가 다른 워커→캐시 미스 → `after_text` 폴백 + before 는 현재 repo 본문에서 재도출
  (§5). before 기준선 폴백 금지 준수.
- **재색인/요약 비용**: 퇴고 연타 시 임베딩·요약 콜 누적 → **accept 시에만**(후보 생성 땐 안 함).
- **요약 재생성 사실주입 위험**: `_summarize` 는 narrative 라 SSOT 변경 없음. 그러나 LLM 이 본문에 없는 사건을
  요약에 환각하면 미래 주입 오염 → `_summarize` 의 기존 evidence-less 환각은 생성 루프와 동일 리스크(신규 아님), 수용.

---

## 13. 코드 근거 인덱스 (조사 확인)

- `engine/checker.py:54` `check_text(text, ontology, chapter, involved_ids)` — **standalone 아님**, LLM 콜 발생.
- `engine/checker.py:59` `ontology_internal_check` 합류 — `engine/ontology.py:232-282` **SSOT만, prose 미입력** → 델타 0.
- `engine/checker.py:30-52` `_relation_contradictions`(quasi) — G-A 의 실효 신호.
- `engine/extractor.py:75` `extract_full` 내부 `involved_ids ∪ scan_present_ids(text)` — id 비대칭 원인(D2).
- `engine/extractor.py:93` `chat_json` — extract 1콜(콜 예산 근거).
- `engine/harness.py:210-236` `_continuity_polish`(수량·중복사건 변경) — **사실변경 패스, 제외**(D1).
- `engine/harness.py:287-304` `_regen_tail`(temp0.8 말미 재작성) — **사실변경 패스, 제외**(D1).
- `engine/harness.py:264-285` `_fix_tics`(LLM 자유치환) — 1차 제외.
- `engine/harness.py:475-479` `_fix_tense` 결정론 게이트(`tense_leak_ratio>0.05`) — 콜 0 대부분.
- `engine/harness.py:489,492,498` finalize: `index_chapter`/`wiki.ingest_chapter`/`_summarize` — §6 동기화 근거.
- `services/copilot.py:43` `_build_story_so_far`(`detail_synopsis or summary or text[:120]`) — stale 요약 주입 경로.
- `services/copilot.py:183-220` `_drafts` TTL 패턴 — `_revise_drafts` 차용 원형.
- `engine/rag.py:40-43` `index_chapter` 멱등 — 재색인/복원 안전성 근거.
- `engine/ontology.py:94` `scan_present_ids`(결정론 substring) — involved_ids 고정·G-B 엔티티 키원.
- `domain/types.py:93-119` `ChapterRecord` **버전 필드 전무** / `:48-52` `AuthorDirective` 미래 지시만.
