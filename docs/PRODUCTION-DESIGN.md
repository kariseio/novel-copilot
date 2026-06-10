# AI 웹소설 창작 코파일럿 — 프로덕션 설계서 (v4, 적대비평 통합판)

> "기계가 쓰고 작가가 조향한다." 무인완결기가 아니라 **비대칭 코파일럿**.
> 본 문서는 R1~R6 6개 도메인 초안을 두 건의 적대비평(needs_work x2)을 **전부 반영**해 하나로 종합한 것이다. 핵심 변경: (a) **6개 동시 착수 폐기 → 측정-게이트 단일 수직 슬라이스**, (b) **미실증 분기(ESCALATED/contradiction) 위 인프라 금지 → 오염주입 실행증명 선결**, (c) **R6 P0 동시성 장치(fence/lease/work_run) 컷 → SQLite WAL + BEGIN IMMEDIATE + 멱등키로 축소**, (d) **선결 합의 3종(narrative_order 좌표계 / rel_type·provenance 단일 enum SSOT / 추출 정확도 측정)**, (e) **과설계 컷**(narrative_inferred 엣지, 의미 드리프트 판정자, 생성중 개입 inbox, 3층 bible 스키마, as-of 슬라이더, 4액션 escalation 등 전부 트리거 충족 전 금지).

---

## 0. 개요 & 현 구현의 정확한 상태(코드 실측)

대상 코드베이스: `D:\study\ai-web-novel\app\novelcopilot` (FastAPI + vanilla web, OpenAI 백엔드).

비평이 인용한 코드 사실을 직접 대조해 **전부 정확함**을 확인했다(핵심만):

- **온톨로지는 절반만 그래프다.** `engine/ontology.py`: `Ontology = {entities: dict[str,Entity], timeline: list[tuple(eid,attr,value,eff_from,reason)], rules: list[str]}`. **엔티티↔엔티티 1급 엣지 구조 전무.** 소속/혈연/적대는 `EntitySpec.attrs["affiliation"]` 같은 categorical **문자열**일 뿐 — 대상 resolve·방향·종료(eff_to)·순회 불가.
- **etype은 닫힌 Literal.** `domain/world.py:27` `EntitySpec.etype: Literal["character","item","faction","worldrule","place"]`. R2 도감 카테고리(magic_system/monster/race/region…) 데이터주도 확장 불가.
- **escalation은 휘발 emit뿐.** `engine/ontology_updater.py:108/124/141` 의 contradiction 분기는 `OntologyChange(op="contradiction", applied=False)` 한 줄 + `bus.emit("ontology_update","escalation",...)`. **영속 큐·해결 워크플로 0.** `harness.py:122` 의 `ChapterStatus.ESCALATED` 도 표시일 뿐, `copilot.py` 어디에도 enqueue 없음.
- **결정론 ESCALATED는 구조적 near-dead 분기.** `checker.py:33` 의 `ontology.ontology_internal_check()` 는 **본문 text를 인자로 받지 않는다** — `self.timeline` 만 검사한다(동시값/사후속성변경). 즉 deterministic SSOT 모순은 timeline 에 나쁜 튜플이 이미 있어야 발생하고, 본문을 고치는 `_rewrite`(harness.py:63)로는 **절대 해소 불가**. 정상 생성 경로에서 이 분기가 실제 점등한 적이 있는지 **미증명**(비평2 B2/B3 지적, 메모리 PoC 3런 escalation_rate=0).
- **락은 회차 전체 점유.** `services/session.py:26` `self.lock=threading.Lock()`, `copilot.py:94` `with sess.lock:` 가 plan→scene루프→finalize 전체를 감싼다. 프로세스-로컬이라 멀티워커 무효.
- **directive는 영원 누적.** `copilot.py:100` `active = [d for d in state.directives if d.from_chapter <= next_ch]`. 만료/일회성 불가.
- **재생성 멱등 교체 경로 존재.** `copilot.py:125` `state.chapters = [c for c in ... if c.chapter != next_ch] + [record]`. (R4 REGEN/R5 regenerate 재사용 가능.)
- **영속화 = 16.8MB 단일 JSON.** `app/data/projects/2dab6befbef6.json` 실측 **16,866,491 bytes**. `repository/filesystem.py:19-22` 가 매 회차 `model_dump_json` 통째 직렬화 + `tmp.replace`(O(전체) write). `domain/project.py:25` `PersistedChunk.emb: list[float]` 가 임베딩을 본문에 인라인.
- **임베딩 모델 버전 추적 없음.** `engine/rag.py:41` 는 정수 `version` 만 둘 뿐 임베딩을 만든 모델명을 저장하지 않음 → BLOB 분리 시 재색인 불가 리스크.
- **predicates는 Strategy+Registry.** `engine/rules/predicates.py:114` `register()`. 새 술어 = 새 클래스 + 한 줄. `factory.build_rules`(factory.py:26)가 AttributeSpec/WorldRuleSpec→RuleSpec 을 데이터로 굽는다 — **관계 술어·bible 컴파일 delta 의 자연 합류점.**
- **beat_planner는 국소 확장기.** `worldgen/beat_planner.py:34` `recent_summaries[-4:]` 만 보고 다음 비트 생성 — **엔딩 전역 앵커 0**(R4 드리프트 진단 확인).
- **worldgen은 one-shot.** `worldgen/generator.py:71-84` 시드→`chat_json` 1콜→validate→normalize. 대화 턴·승인·되돌리기 없음.
- **SSE 패턴.** `api/routes.py:92-133` `queue + run_in_executor + _sse`. worldgen/run 스트림이 복제할 원형.

이 실측이 본 설계의 토대다. 모든 변경은 **위 자산을 폐기하지 않고 가산(additive) delta** 로만 얹는다.

---

## 1. 목표 & 비-목표 (성공/실패 지표를 숫자로)

비평1 핵심: "운영레벨/설정집수준/드리프트방지는 측정 불가능한 형용사다." 이를 닫는다.

### 1.1 타깃 작가 페르소나 (단일 확정)
비평1·비평2 공통 누락 지적 반영. **1차 페르소나: "다작 프로 연재 작가"** — 하루 1~3화를 연재 압박 속에 쳐내는 사람. 현재 노션/한글/노벨파이를 병행. **회차당 도구 허용 시간 ≤ 3분**(집필 시간 대비 부수적이어야 함). 신인 작가(R3 대화월드젠이 가치)는 **2차 페르소나로 후순위** — R3는 로드맵 최후미.

근거: 비평1이 "신인(R3 필요) vs 프로(R6·R4 필요)는 상충"을 지적. 프로를 1차로 잡으면 **R6(영속/롤백) + R5(escalation 안 잃기) + R4(드리프트 게이트)** 가 우선이고 R3 대화월드젠은 자연히 뒤로 간다 — 이것이 로드맵 순서의 근거.

### 1.2 도메인별 선행지표 + Kill Criteria (숫자)
| 도메인 | 성공 선행지표 | Kill Criteria(접는다) |
|---|---|---|
| **공통** | 도구 도입 전후 회차당 작가 집필+조작 시간 **감소** | 회차당 도구 조작 시간 > 집필 시간 |
| **추출 정확도(토대)** | `ontology_updater.propose` 신규인물 precision ≥ 0.8, state_change 오추출률 ≤ 회차당 1건 | 회차당 작가 교정 > 3건 → backfill·자동관계추출 출시 제외 |
| **R5 escalation** | escalation 해결 평균 클릭 ≤ 2, 미해결 잔존율 ≤ 20% | 작가가 escalation 90%+ 무시 → 큐 UI 강등 |
| **R6 영속/롤백** | 메타 read 16.8MB → ≤ 100KB, 회차 write O(delta), 롤백 사용 ≥ 0건/주 | round-trip 동등성 실패 |
| **R4 드리프트** | coverage_gap 적중(작가가 "맞다" 판정) ≥ 60%, 20화 시점 엔딩 정합 유지 | false coverage_gap > 50% → 게이트 비구속화 |
| **R1 그래프** | 작가가 직접 입력한 ground_truth 엣지 ≥ 회차당 1, 그래프 조회 ≥ 1회/세션 | 작가가 그래프 탭을 안 엶 |
| **R2 설정집** | 작가가 promote한 필드 수 > 0/주 | 편집 0건 → 읽기전용 회귀 |
| **R3 대화월드젠** | 대화 1세션 첫회차 도달 턴 수 ≤ 8, reject 제안 비율 ≤ 30% | reject > 50% 또는 신인 페르소나 미확정 |

### 1.3 실패 시그널 & 롤백 (비평1 누락 반영)
- **실패 시그널**: 작가가 도구를 안 켜기 시작 / 회차당 도구 시간 > 집필 시간 / escalation 전량 무시.
- **롤백 경로**: 각 도메인은 **feature flag** 뒤에 둔다. 기본 OFF(레거시 평면 모드 = 현 동작 100% 보존). 지표 미달 시 flag OFF로 즉시 원복. R6 SQLite는 **JSON↔SQLite round-trip 동등성 테스트** 통과 + JSON 재-export 경로 보존(되돌리기 가능).

---

## 2. 비대칭 불변식 (계승 — 깨지 말 것)

1. **ground_truth(온톨로지=결정론 '박기') vs narrative(RAG/Wiki='찾기')** 타입 분리. `ContextBoard` 3슬롯(`OntologyFact`/`RetrievedItem`/`AuthorDirective`)이 타입으로 강제(types.py:55).
2. **승격 불가.** narrative → ground_truth 자동 승격 금지. **작가 명시 승인 게이트에서만** 전환.
3. **결정론 코어 LLM 0콜.** `ontology_internal_check`, `wiki.lint` 는 LLM 호출 없음. det/quasi 만 hard 게이트, semantic 은 escalation(게이트 비구속).
4. **외부검증.** 생성 모델이 자기 산출을 채점하지 않는다(자가평가 불가). 등급 판정은 코드(`vocabulary` + `ontology.state_as_of`)가 한다 — LLM 제안의 `grade` 필드를 신뢰하지 않고 코드가 재계산해 덮어쓴다.
5. **append-only / 출처추적.** 덮어쓰기 없음, 추가/전진만, 모순은 사람에게.

**비평2가 지적한 비대칭의 새 구멍(반드시 닫음):** 생성본문 → OntologyUpdater 가 "명시적 관계"를 추출해 ground_truth 엣지로 박는 경로([설계 C])는 **narrative→ground_truth 세탁 통로**가 될 수 있다. → **관계 자동추출은 MVP에서 만들지 않는다.** 작가 직접 입력 ground_truth 엣지만. 자동추출을 나중에 도입할 때는 `trust_tier="narrative_inferred"` 로만 적재하고 본체 `Ontology.edges` 에 미투입 + 작가 승인 게이트 필수. **"비대칭은 '나중에 narrative 추가'로 보존되지, '지금 narrative부터 만들기'가 준수가 아니다"**(비평1 과설계 지적 그대로).

**작가=최고권위 vs det 게이트 충돌의 UX 경계(비평1 누락 반영):** 작가는 **정책(mutable/단조/immutable/사망→생존 escalation)은 override 가능**(=캐논 분기, 감사로그 기록). 그러나 **SSOT 내부 자기모순(`ontology_internal_check`: 동시값/사망후변경)은 reject** — 작가도 못 깬다. UX: override 거부 시 "이 변경은 9화 사망과 12화 속성변경이 동시에 참이 되어 시간선이 모순됩니다. 사망 시점을 함께 바꾸시겠어요?" 같은 **모순의 구체적 원인 + 동반 수정 제안**을 보여준다(빈 "안 됨" 금지).

---

## 3. 선결 합의 (Stage 0 — 어느 도메인도 머지 전 필수)

비평2 Blocker: cross-domain 의존이 미합의면 통합 비용 폭증. 3종을 코드로 못박는다.

### 3.1 단일 시간 좌표계 = `narrative_order: int`
현 `current_chapter:int`, `eff_from:int`, `eff_to`, 엣지 시점, drift 신호가 전부 같은 축을 써야 한다. **결정: `narrative_order` 단일 int 좌표계.** R4 아크/챕터 계층은 `(arc,chapter)` 복합키를 **쓰지 않고**, `narrative_order` 위에 **뷰(NarrativeProgress 커서)**로만 얹는다. `current_chapter` 는 물리 진실로 보존, 아크 커서는 그 위 파생. → R1 엣지 eff_from/eff_to, R5 timeline, R4 drift 가 동일 축 공유.

### 3.2 `rel_type` / `provenance` 단일 enum SSOT
- **`rel_type`**: 신규 `domain/relations.py` 의 **`REL_CATALOG: dict[str, RelationSpec]`** 단일 출처. R1 `RelationEdge.rel_id`, R3 `RelationProposal.rel_type`, R5 `RelationEdit.rel_type` 가 전부 이 키를 FK로 참조. 직접 문자열 리터럴 금지.
- **`provenance`**: 단일 enum `Provenance = Literal["seed","author","ai_worldgen","ai_backfill","machine","human_edit"]`. R5(human_edit/machine), R3(ai_worldgen), R6(author/ai_worldgen) 가 전부 이 enum 사용. 직렬화 호환 보장.

### 3.3 추출 정확도 측정 (빌드전 필수, 비평1·2 공통 Blocker)
**`ontology_updater.propose` 를 기존 16.8MB 프로젝트(2dab…json)의 실제 회차들에 돌려** precision/recall 을 숫자로 박는다(신규인물 오탐, state_change 오추출, 관계 짝짓기 오류). 이 숫자가 R1 자동관계추출 / R2 backfill / R3 제안 / R4 coverage_gap 의 **출시 여부를 결정**. 임계: **회차당 작가 교정 > 3건이면 자동추출류는 전부 출시 제외, 작가 수동 입력만.**

### 3.4 ESCALATED/contradiction 실행 증명 (빌드전 필수, 비평2 Blocker)
R5 escalation 큐·R6 enqueue 착수 **전에** 의도적 오염주입 테스트를 green 으로:
- (a) `_rewrite` 를 항등함수로 패치하거나 monotonic 위반/사망자 행동/immutable 변경을 강제하는 비트를 주입해 **`ChapterStatus.ESCALATED` 가 실제 1회 이상 발생**하는지.
- (b) `ontology_updater` 의 3개 contradiction 분기(사망→생존 line 105, 단조 line 121, immutable line 138)가 각각 `bus.emit` 까지 실행되는지 단위 검증.
이 둘이 green 이 된 뒤에만 `EscalationItem` 영속 큐를 그 emit 지점에 연결한다. **증명 전에는 escalation 을 '표시'로만 두고 큐는 defer.**

---

## 4. 데이터 모델

### 4.1 온톨로지 속성그래프 (R1 — 가산 delta)
현 `Ontology` 의 '엔티티-속성 + 시간 timeline' 절반을 무손상 보존하고, **동격의 '엣지 + 엣지 timeline' 절반**을 더한다. 노드 `state_as_of` 패턴을 엣지에 거울복제.

```python
# domain/world.py 신규 — 엔티티 타입 카탈로그(데이터주도)
class EntityTypeSpec(BaseModel):
    key: str                 # PK, 'magic_system'
    label: str               # '마법체계'
    category: Literal['actor','group','place','object','abstract','event']
    color: str = "#6aa9ff"   # 시각화 (vocabulary 가 라벨 파생하듯 factory 가 파생)
    shape: Literal['ellipse','round-rectangle','diamond','hexagon','triangle','star'] = 'ellipse'
    icon: str = ""
    is_builtin: bool = False
# EntitySpec.etype / Entity.etype: Literal[...] → str 완화.
#   ★ factory 에서 카탈로그 멤버십 검증 강제 + unknown-type → fallback shape + 경고 emit (비평2 빌드전필수)

# domain/relations.py 신규 — '관계=데이터' 단일 SSOT (3.2)
class RelationSpec(BaseModel):
    rel_id: str              # PK, 'member_of'
    label: str               # '소속'
    category: Literal['kinship','affiliation','alliance','hostility','mentorship',
                      'romance','ownership','usage','location','involvement','custom']
    directed: bool = True
    symmetric: bool = False
    inverse_label: str = ""
    allowed_src_types: list[str] = []
    allowed_dst_types: list[str] = []
    mutable: bool = True     # False=불변 혈연
    grade: SignalGrade = SignalGrade.QUASI
    cardinality: Literal['1:1','1:N','N:1','N:N'] = 'N:N'
    color: str = "#888"
    line_style: Literal['solid','dashed','dotted'] = 'solid'
REL_CATALOG: dict[str, RelationSpec] = { ... }   # 단일 출처

# domain/types.py 신규 — 엣지 인스턴스 (eff_to = 관계 종료, 반열림 [eff_from,eff_to))
class RelationEdge(BaseModel):
    edge_id: str             # '{rel_id}:{src}->{dst}:{eff_from}'
    rel_id: str              # FK → REL_CATALOG
    src_id: str; dst_id: str # FK → Entity
    role: str = ""           # 'father'/'mother' 세분
    attrs: dict = {}
    eff_from: int            # narrative_order (3.1)
    eff_to: Optional[int] = None   # None=현재 유효
    reason: str = ""
    trust_tier: Literal['ground_truth','narrative_inferred'] = 'ground_truth'
    provenance: list[str] = []     # Provenance enum 값들
```

`engine/ontology.py` 확장(거울복제):
- `edges: list[RelationEdge]` + `rel_catalog: dict[str,RelationSpec]`.
- `add_edge(e)`; `edge_state_as_of(src,dst,rel_id,ch)` → active|None (`eff_from<=ch and (eff_to is None or ch<eff_to)`, 최신 eff_from 승); `edges_as_of(ch)` → list[active]; `neighbors(eid,ch,direction)`.
- `canon_relations(eids,ch)` → list[OntologyFact] (**ground_truth 슬롯**; trust_tier=='ground_truth' & active 만 → '확정 관계' 박기). `canon_facts` 와 병합되어 `ContextBoard.ground_truth` → `prompts.py` '확정 관계' 블록.
- **`ontology_internal_check` 엣지 검사 — MVP는 3종만**(비평2 과설계 컷): `dangling`(src/dst 부재), `self_loop`(src==dst 금지), `post_death_edge`(기존 death 맵 재사용, death[eid] 이후 eff_from). **cardinality 위반 / symmetric 모순 / 혈연 순환 DFS 는 엣지 수십 개 이상 실데이터 발생 후 추가**(LLM 0콜 유지). 모두 deterministic.
- predicates.py 에 `RelationEvaluator(kind="relation_state")` 한 클래스 추가(Strategy 그대로) — 본문 관계위반 quasi 검사(이후 단계).

`domain/project.py`: `runtime_edges: list[RelationEdge] = []` (append-only, runtime_entities 동형).
`domain/world.py WorldConfig`: `entity_types: list[EntityTypeSpec] = []`(빈→factory가 BUILTIN 5종 시드=하위호환), `relations: list[RelationSpec] = []`, `seed_edges: list[RelationEdge] = []`.

**MVP 컷(비평1·2):** `narrative_inferred` 엣지 + 작가 승인 게이트 + 점선 시각화 + 자동관계추출 **전부 만들지 않는다.** 작가 직접 입력 ground_truth 엣지만. as-of-chapter 슬라이더도 **현재 시점 그래프 하나만**(슬라이더는 작가가 과거 시점을 본다는 증거 나온 뒤).

### 4.2 설정집(Story Bible) (R2 — descriptive layer)
**MVP는 3층 스키마 컷, prose + 단일 promote 버튼.** (비평1 단순화: "promotable vs promoted 2단계는 작가가 같은 항목을 두 번 승인하게 만든다 — 한 번에 박거나 narrative로 두거나.")

```python
# domain/bible.py 신규 (MVP 최소)
class BibleEntry(BaseModel):
    entry_id: str
    category: Literal['magic_system','ability_system','bestiary','race','geography',
                      'faction_politics','chronology','artifact','character',
                      'culture_religion','taboo_worldrule','glossary']  # glossary=escape
    title: str
    prose: str = ""          # 층1: 작가/AI 자유서술
    promoted: bool = False   # 단일 게이트: 캐논으로 박혔는가
    promote_target: Optional[Literal['attribute','world_rule','timeline','relation']] = None
    promote_hint: str = ""
    provenance: Provenance = "author"
    status: Literal['author_approved','ai_unreviewed','draft','deprecated'] = 'author_approved'
    as_of_narrative_order: int = 0
class StoryBible(BaseModel):
    entries: list[BibleEntry] = []
# domain/genre_templates.py: GENRE_TEMPLATES dict (장르=데이터 row; 로판/현판/무협/회귀/헌터 — 비평1 누락 반영)
```

- `ProjectState.bible: StoryBible = StoryBible()` (옵셔널, 빈 컨테이너 기본 → 하위호환).
- **컴파일 게이트**: `engine/bible_compiler.py` `compile_bible(bible)` → `{attributes_delta, world_rules_delta, timeline_delta, warnings}`. **`promoted==True` 인 항목만** 컴파일. `factory.build_rules`/`build_ontology`(factory.py:26,48)가 delta 를 merge. 컴파일 충돌(vocab 토큰 불일치 등)은 **`ontology_internal_check` 동형 LLM 0콜 결정론 린트**로 warnings + 컴파일 거부(박기 전 게이트).
- **마이그레이션**: `migrate_world_to_bible(world)` — 기존 WorldConfig.attributes/world_rules/entities 를 bible 섹션으로 역투영(round-trip)해 빈 설정집 자동 부트스트랩. bible 은 world 의 superset 뷰. **SSOT는 항상 compile 결과의 함수**(dual source of truth 방지).
- **MVP 컷(비평2)**: 4값 constraint_role / 3층 structured 필드 / revision append-only / RAG bible 색인(`RetrievedItem.source += 'bible_entry'`) / backfill 자동 역추출 — **전부 Later.** backfill 은 추출 정확도 측정(3.3) 통과 후에만, status='ai_unreviewed' + trust_weight=0.3 + 검토 큐 필수.

### 4.3 서사 아크/챕터 (R4 — 엔딩 주도 계층 뷰)
```python
# domain/narrative.py 신규
class EndingCandidate(BaseModel):
    ending_id: str; premise_resolution: str; protagonist_end_state: str; thematic_payoff: str
class ArcBeat(BaseModel):
    arc_beat_id: str; goal: str
    required_events: list[str] = []   # 통제 태그(임베딩 유사도 아닌 명시 set 비교 — 비평 noisy 완화)
    required_cast: list[str] = []
    plants: list[str] = []; payoffs: list[str] = []   # wiki plot_thread page_id (닫힘성 lint)
class Arc(BaseModel):
    arc_id: str; order: int; arc_goal: str; central_conflict: str; turning_point: str = ""
    arc_beats: list[ArcBeat] = []
    target_chapters_hint: dict = {"min":3,"max":8}
    plants: list[str] = []; payoffs: list[str] = []
class NarrativeSpine(BaseModel):
    ending_candidates: list[EndingCandidate] = []
    confirmed_ending_id: Optional[str] = None
    arcs: list[Arc] = []
class NarrativeProgress(BaseModel):
    current_arc_id: Optional[str] = None; current_arc_beat_id: Optional[str] = None
    chapters_in_arc: int = 0
class NarrativePlanRevision(BaseModel):   # append-only
    revision_id: str; base_revision_id: Optional[str]; action: str
    reason: str = ""; trigger_signal_kinds: list[str] = []; at_chapter: int = 0
# 델타: Beat.arc_id/arc_beat_id (nullable); WorldConfig.spine: Optional[NarrativeSpine]=None
#       ProjectState.narrative_progress / plan_revisions; ChapterRecord.arc_id/arc_beat_id/drift_signals
#       OntologyChange.op += 'arc_transition'; DriftSignal = Violation 재사용(새 enum 금지, kind 확장)
```
- **`spine=None` = 레거시 평면 모드 100% 호환.** 계층 모드도 결국 평면 `Beat`(arc_id 스탬프만)를 산출하므로 하류 harness/checker 는 **단일 경로 유지.**
- **드리프트 = 결정론 신호만(MVP, 비평1·2 과설계 컷).** `engine/drift.py` DriftMonitor: `coverage_gap`(required_events 미커버; 태그 set 비교=quasi), `plant_overdue`(wiki payoff_deadline < current_chapter 미회수=deterministic, wiki.lint stale 재사용), `cast_mismatch`(required_cast 미등장; `scan_present_ids` 재사용=deterministic), `arc_overrun`(chapters_in_arc > max=deterministic). **의미 드리프트 외부 LLM 판정자(theme_drift/tone_break/arc_goal_distance)는 '필요 증명 전 금지'** — escalation-only·게이트 비구속이라 비용+노이즈만 늘림.
- **재계획 = REGEN_CHAPTER만(MVP).** `copilot.py:125` 멱등 교체 재사용. ADJUST_ARC/REPLAN_FORWARD 는 아크 모델 안정화 후. 자율 재계획 금지 — escalation 표시 후 **작가 명시 선택**(사용자 글로벌 규칙 준수).
- **엔딩 앵커는 ground_truth 오염 금지.** `RetrievedItem(source='arc_anchor', trust_weight 높게)` 로 **narrative 슬롯**에 주입(아크 목표는 '서사 의도'지 '결정론 사실'이 아니다).

### 4.4 작가 개입 (R5 — 단일 엔벨로프, payload는 점진)
```python
# domain/intervention.py 신규
class InterventionScope(BaseModel):
    mode: Literal['this_chapter_only','from_chapter','arc','retroactive_canon']
    from_chapter: Optional[int] = None; arc_id: Optional[str] = None
    target_chapter: Optional[int] = None
class Intervention(BaseModel):
    intervention_id: str
    kind: Literal['canon_edit','chapter_inject','directive','escalation_resolution']
    created_at: str; created_by: str = "author"
    scope: InterventionScope
    status: Literal['pending','active','consumed','revoked','rejected'] = 'active'
    payload: dict = {}        # kind별
    causes: list[int] = []    # 유발한 timeline seq (revert 단위)
    note: str = ""
class EscalationItem(BaseModel):   # ★ 영속 큐 (현재 휘발)
    escalation_id: str; raised_at_chapter: int
    source: Literal['ontology_updater','harness_hard_violation']
    op: Literal['contradiction','immutable_change','monotonic_violation','death_revive','hard_violation']
    entity: str; attr: Optional[str] = None
    from_value: Optional[str] = None; to_value: Optional[str] = None
    reason: str = ""
    status: Literal['PENDING','RESOLVED','DISMISSED'] = 'PENDING'
    resolved_by: Optional[str] = None
# 델타: ProjectState.interventions / escalations (둘 다 append-only)
#       OntologyChange.op += 'supersede','human_edit','relation_change'; +provenance, +seq, +intervention_id
#       AuthorDirective.intervention_id(역참조), expires_after_chapter(this_chapter_only 사상)
```
- **MVP 범위(비평1·2 강한 컷):** `chapter_inject(this_chapter_only)` + `escalation_resolution(approve/reject)` **2종 + 영속 큐만.** 이것만으로 '표시→해결' 공백의 80%가 닫힌다.
- **escalation MVP = 영속화 + 2액션(승인/무시).** `ontology_updater` 의 기존 emit 지점(108/124/141) + harness ESCALATED(122)에 **enqueue 한 줄** 추가(단, 3.4 실행증명 green 후). 4액션(alternative/regenerate)은 ESCALATED 증명 이후.
- **canon_edit 직접편집 / directive 흡수 / revert tombstone / 생성중 inbox(intervention_inbox+cancel_event+scene-boundary absorb+urgent_restart)는 전부 Later.** 비평1·2: 생성중 개입은 동시성 재설계급인데 작가 수요 근거 0, 회차는 수분짜리라 **after_chapter(기다림)로 충분**. 단일 엔벨로프 타입은 유지하되 payload 4종을 동시 구현하지 않는다.
- **state_as_of 정렬키 확장(`eff_from` → `(eff_from, provenance_rank, seq)`)은 do-now 아님 — 회귀 테스트 동반 조건부.** 기존 timeline 을 `provenance='machine', seq=append순서` 로 마이그레이션 + **기존 회차 재현 골든 테스트** 통과 후에만 적용(비평2 빌드전필수).

### 4.5 운영 영속화 (R6 — P0 축소판)
비평2 핵심 컷: fence_token CAS + lease_expiry 자동회수 + work_run 폴링 워커 = **'작가 1인·단일 uvicorn'에 과조기**. **P0는 SQLite WAL + 단일 BEGIN IMMEDIATE + 멱등키 + 임베딩 BLOB 분리 + model_version 태깅으로 축소.**

```sql
-- P0 스키마 (SQLite WAL). domain 모델은 불변, Repository 어댑터가 분해/재조립.
project(id PK, title, genre, created_at, current_chapter,
        seed_json, world_json, usage_total_json)             -- 핫 메타, read 16.8MB → ~수KB
chapter_version(version_id PK, project_id FK, narrative_order INT,
        source TEXT, status TEXT, supersedes_id FK NULL, is_active BOOL,
        text, scenes INT, rounds_json, final_violations_json,
        ontology_changes_json, created_at)
        -- PARTIAL UNIQUE(project_id, narrative_order) WHERE is_active  ← lost update 구조 차단 + 롤백
rag_chunk(chunk_id PK, project_id FK, chapter INT, version INT, text,
        embedding BLOB,                  -- float32 packed (본문에서 분리)
        embedding_model_version TEXT)    -- ★ P0 필수 태깅 (비평2 누락 — 없으면 재색인 불가)
wiki_page(project_id, page_id, page_type, body, typed_edges_json, lifecycle,
        trust_tier, as_of_narrative_order, provenance_json, payoff_deadline,
        PRIMARY KEY(project_id, page_id))
event_log(seq PK AUTOINC, project_id FK, run_id, chapter INT, node, event,
        is_failure BOOL, payload_json, ts)   -- 관측 영속화(조용한정지 사후추적)
usage_ledger(entry_id PK, project_id FK, run_id, chapter INT, node,
        chat_calls, chat_tokens, embed_calls, embed_items, est_cost_usd, ts)
-- world_revision(append-only 설정집/온톨로지 이력)은 P1 버전관리 착수 시.
--   ★ 전체 스냅샷 매 turn 적재 금지(JSON 비대를 DB로 옮길 뿐) — change_summary+delta 저장,
--     N revision 마다 체크포인트 (비평2 단순화).
```

- **동시성 = 단일 `BEGIN IMMEDIATE`.** SQLite 가 단일 writer 강제 → 단일 워커에선 lost-update 구조적 불가. `sess.lock`(프로세스 로컬)은 유지하되 DB 트랜잭션이 권위.
- **멱등키**: generate 진입에 `idempotency_key=pid:next_ch:attempt` → SSE 끊겨 재시도해도 중복 회차 생성 안 함(실사용 시나리오).
- **마이그레이션 + 롤백 안전(비평2 누락 반영):** `app/data/projects/*.json` → INSERT 일회 backfill(model_version 태깅 강제). **JSON↔SQLite round-trip 동등성 테스트 + JSON 재-export 경로**를 P0에 포함(되돌리기 가능).
- **defer**: `fence_token` CAS / `lease_expiry` 자동회수 / `work_run` 폴링 워커 → **P2(멀티워커/멀티유저 동시쓰기 트리거)** 와 함께. **PgProjectRepository + pgvector HNSW + PG FTS(Kiwi)** → P2. **Temporal**(30~50화 장기실행 크래시복원이 실 병목일 때만) / **Redis pub/sub**(SSE fan-out 워커경계 넘을 때만) → P3. 트리거 충족 전 도입 금지(cargo-cult 경계).

---

## 5. 협업 월드젠 루프 (R3 — 로드맵 최후미)

비평1·2 공통: R3는 **가장 마지막.** R1·R2가 안정화돼야 write 타깃이 생기고, **신인 작가 페르소나가 확정돼야 가치가 증명**된다. 현 one-shot `generator.generate`(generator.py:71)는 폐기하지 않고 genesis 세션의 **'시드 부트스트랩 첫 제안'**으로 재활용.

- **genesis MVP는 Proposal 4종만**(비평2 컷): `entity` + `relation` + `bible_entry` + `question`. value_change/beats_change/attribute_def 동적제안은 enrichment(Later)와 묶음(vocab 충돌 검증 부담).
- **비대칭 분류는 코드가**(LLM 출력 `grade` 불신): `auto_commit`(순수 추가·충돌0) / `needs_approval`(기존 값 변경) / `blocked`(immutable·단조·사망→생존·vocab밖). `OntologyUpdater.classify/apply` 정책을 `WorldgenApplier` 로 추출·일반화(코드 재사용).
- **InterviewQuestion 역질문은 신인 페르소나 확정 후 가치 판정**(비평1: 프로엔 짜증, 신인엔 자산). MVP에서 questions cap=3, intent 우선순위(consistency_check > fill_gap > deepen).
- **SSE**: `routes.py:92` 패턴 복제, `node='worldgen'` 필터(혼선 방지). committed diff(added 초록/changed 노랑/contradiction 빨강)로 그래프·설정집 점등 — R1 그래프 컴포넌트 공유.
- enrichment 모드(진행중 강화)는 R5 '진행 중 직접 주입'과 동일 엔드포인트로 수렴 — **Later.**

---

## 6. 영속화·운영·시각화·관측

- **시각화(R1)**: `ontology_snapshot`(copilot.py:140)에 `graph:{nodes,edges,types,relations,max_chapter}` 키 추가(**기존 characters/rules/timeline 유지=하위호환**). cytoscape.js **CDN 1줄**(빌드 무첨가, 현 vanilla 기조). stylesheet 를 types/relations 카탈로그에서 **동적 생성**(하드코딩 금지). **레이아웃은 가시 서브그래프에만 한정**(전체 노드 cose 회피 — 후반 지연 완화, 비평2 단순화 MVP부터 적용).
- **관측**: `EventBus.emit` 에 sink 추가 → `event_log` append. SSE 는 P1에서 `event_log` tail 구독으로 전환(브라우저 끊겨도 생성 지속·재구독). `system_state_badge` 상시 노출(조용한 정지 금지).
- **비용**: `usage_delta` 를 `usage_ledger` 에 회차·노드 분해(회차당 LLM 호출/토큰 1급 계측). 비용 대시보드.

---

## 7. 마이그레이션 경로 (현 app/ 진화, 그린필드 금지)

| 영역 | 현재 | delta | 하위호환 |
|---|---|---|---|
| etype | `Literal[5종]` | `str` + EntityTypeSpec 카탈로그 | 빈 카탈로그→BUILTIN 5종 시드 |
| 엣지 | 없음(문자열 위장) | `Ontology.edges` + RelationEdge | runtime_edges 빈 리스트=현 동작 |
| escalation | 휘발 emit | `state.escalations[]` 영속 큐 | enqueue 추가만(emit 유지) |
| bible | 없음 | `ProjectState.bible` | 빈 컨테이너 기본 |
| spine | 평면 beats | `WorldConfig.spine` + Beat.arc_id | spine=None=평면 모드 |
| 영속화 | 16.8MB JSON | SqliteProjectRepository(WAL) | 동일 Repository 인터페이스, JSON backfill+재export |
| 락 | sess.lock | + BEGIN IMMEDIATE + 멱등키 | sess.lock 유지 |

모든 도메인은 **feature flag** 뒤(기본 OFF=레거시). 지표 미달 시 즉시 원복.

---

## 8. 비대칭 불변식 보존 체크리스트 (머지 게이트)

- [ ] ground_truth/narrative 타입 분리 유지(엣지도 trust_tier로).
- [ ] narrative→ground_truth 자동 승격 코드 경로 0건(관계 자동추출 MVP 제외).
- [ ] LLM 제안 grade/classification 을 코드가 재계산(vocabulary+state_as_of).
- [ ] 의미 신호 hard 게이트 승격 0건(드리프트·worldrule_flag=semantic=escalation-only).
- [ ] 결정론 코어 LLM 0콜(ontology_internal_check·wiki.lint·엣지검사 3종).
- [ ] 작가 override는 정책만, SSOT 자기모순은 reject(원인+동반수정 제안 UX).
- [ ] append-only/seq/provenance/intervention_id 상관관계 완전 감사.

---

## 9. 결론

비평이 정확히 짚은 두 균열 — **(1) 미실증 분기 위 인프라(escalation), (2) 과조기 동시성 장치(fence/lease/work_run)** — 를 각각 **실행증명 선결**과 **P0 축소**로 닫았다. 6개 L/XL 동시 착수의 순환 의존은 **측정-게이트 단일 수직 슬라이스(R6 P0 + R5 escalation 영속화)**로 대체했다. 추출 정확도·작가 인지부하·도메인별 kill criteria를 숫자로 박아, "도구가 소설보다 무거워지는 역전"을 측정 가능하게 만들었다. 모든 변경은 현 `app/novelcopilot` 자산의 가산 delta이며 비대칭 불변식은 타입·게이트·감사로 보존된다.


---

## 부록 A. 데이터 모델 요약

현 app/novelcopilot 자산을 폐기하지 않는 가산(additive) delta. 시간축은 narrative_order:int 단일 좌표계로 통일(아크/챕터는 그 위 뷰). [R1] Ontology(entities dict + timeline tuple) 옆에 edges:list[RelationEdge] + rel_catalog 거울복제 — RelationEdge{edge_id,rel_id(FK→REL_CATALOG 단일SSOT),src_id,dst_id,role,eff_from,eff_to(반열림),trust_tier,provenance}; EntityTypeSpec 카탈로그 + etype Literal→str 완화(factory 멤버십 검증 강제+unknown fallback); MVP는 작가 직접입력 ground_truth 엣지만(narrative_inferred·자동추출 제외). ontology_internal_check 엣지검사 3종(dangling/self-loop/post-death)만 LLM0콜. [R2] StoryBible{entries:[BibleEntry{prose,promoted(단일게이트),promote_target,provenance,status}]} descriptive layer; compile_bible(promoted만)→factory.build_rules/build_ontology delta merge; 3층 스키마·constraint_role 4값·backfill 컷. [R4] NarrativeSpine{ending_candidates,confirmed_ending,arcs[Arc[ArcBeat{required_events 통제태그,plants/payoffs}]]} + Beat.arc_id 델타(spine=None=평면 호환); drift=결정론 신호만(coverage_gap/plant_overdue/cast_mismatch/arc_overrun, DriftSignal=Violation 재사용); 의미판정자 컷. [R5] Intervention 단일 엔벨로프(4 kind, MVP는 chapter_inject+escalation_resolution 2종); EscalationItem 영속 큐(state.escalations[], 현재 휘발 emit→enqueue); OntologyChange+provenance/seq/intervention_id; canon_edit·revert·생성중 inbox·state_as_of 정렬키 확장은 회귀테스트/실행증명 후. [R6] P0: SQLite WAL repo, project(핫메타)/chapter_version(append-only is_active 부분유니크=롤백)/rag_chunk(embedding BLOB 분리+embedding_model_version 태깅 필수)/event_log/usage_ledger; 동시성=단일 BEGIN IMMEDIATE+멱등키; fence/lease/work_run·PG·Temporal·Redis는 트리거 충족(P2/P3) 전 컷. 선결 enum SSOT: domain/relations.py REL_CATALOG(rel_type), Provenance Literal(seed/author/ai_worldgen/ai_backfill/machine/human_edit). 전 도메인 feature flag 뒤(기본 OFF=레거시 100% 보존).


---

## 부록 B. 구현 로드맵 (의존순서)


### Stage 0 — 선결 합의 & 측정 (머지 차단 게이트)  ·  effort=M

**목표**: 어느 도메인도 머지 전에 cross-domain 좌표계·enum SSOT를 못박고, 자동추출 정확도와 ESCALATED/contradiction 분기의 실행을 숫자/green으로 증명한다. 이 결과가 이후 모든 자동추출류의 출시 여부를 결정한다.

**사용자 체감**: 직접 체감 없음(내부 게이트). 단 측정 리포트가 이후 어떤 자동기능을 작가에게 노출할지를 결정 — 잘못된 자동추출로 작가를 괴롭히는 것을 사전 차단.

**산출물**:
- narrative_order:int 단일 시간 좌표계 합의 문서화(current_chapter/eff_from/eff_to/drift 동일 축; 아크는 뷰)
- domain/relations.py REL_CATALOG(rel_type 단일 SSOT) + Provenance Literal enum 정의
- 추출 정확도 측정: ontology_updater.propose를 2dab…json 실제 회차에 돌려 신규인물 precision/recall·state_change 오추출률·관계 짝짓기 오류율 숫자 산출
- 오염주입 테스트: _rewrite 항등 패치 또는 위반 비트 주입으로 ChapterStatus.ESCALATED 1회+ 실발생 증명 + ontology_updater 3개 contradiction 분기(105/121/138) bus.emit 단위 검증
- 도메인별 kill criteria + 작가 세션 분단위 시뮬레이션(도구시간<집필시간) 문서

_파일_: `app/novelcopilot/domain/relations.py(신규)`, `app/novelcopilot/engine/ontology_updater.py(테스트 대상)`, `app/novelcopilot/engine/harness.py(테스트 대상)`, `tests/test_escalation_injection.py(신규)`, `tests/test_extraction_accuracy.py(신규)`


### Stage 1 — 단일 수직 슬라이스: R6 P0 영속화 + R5 escalation 영속 큐  ·  effort=L  ·  depends_on=Stage 0

**목표**: 의존성이 가장 적고 작가 가치가 즉각적인 둘을 먼저 출시: 16.8MB 통짜 write 해소(SQLite WAL + 임베딩 BLOB 분리) + 회차 롤백 + 현재 휘발되는 escalation을 안 잃기(영속 큐 + 승인/무시 2액션). 동시성 장치(fence/lease/work_run)는 컷.

**사용자 체감**: 작가가 '회차 롤백'을 즉시 사용. 큰 프로젝트 로딩이 수초→즉시(메타 16.8MB→수KB). 모순 경고(escalation)가 사라지지 않고 인박스에 쌓여 승인/무시 가능. SSE 끊겨 재시도해도 회차 중복 생성 안 됨.

**산출물**:
- SqliteProjectRepository(WAL) — base.py 인터페이스 동일, project/chapter_version/rag_chunk(embedding BLOB+model_version)/wiki_page/event_log/usage_ledger 스키마
- JSON→SQLite 일회 backfill + JSON↔SQLite round-trip 동등성 테스트 + JSON 재-export 롤백 경로
- 회차 generate 진입에 단일 BEGIN IMMEDIATE + idempotency_key(pid:ch:attempt)
- chapter_version is_active 부분유니크 + 롤백 API(과거 버전 토글)
- EventBus.emit sink→event_log, usage_delta→usage_ledger
- EscalationItem 영속 큐: ontology_updater 108/124/141 + harness 122 ESCALATED에 enqueue(Stage0 증명 green 후) + state.escalations[]
- escalation 인박스 패널(PENDING 리스트 + 승인/무시 2액션) + chapter_inject(this_chapter_only)

_파일_: `app/novelcopilot/repository/filesystem.py`, `app/novelcopilot/repository/sqlite_repo.py(신규)`, `app/novelcopilot/repository/base.py`, `app/novelcopilot/domain/project.py`, `app/novelcopilot/domain/intervention.py(신규)`, `app/novelcopilot/engine/ontology_updater.py`, `app/novelcopilot/engine/harness.py`, `app/novelcopilot/services/copilot.py`, `app/novelcopilot/api/routes.py`, `app/novelcopilot/web/app.js`


### Stage 2 — 측정 정지점  ·  effort=S  ·  depends_on=Stage 1

**목표**: Stage 1 출시 후 추출 정확도(실가동)와 작가 인지부하를 실측한다. 이 결과가 R1/R4/R2 진입 여부와 자동기능 노출 범위를 결정. 지표 미달 도메인은 feature flag OFF로 강등.

**사용자 체감**: 직접 체감 없음. 다음 단계 기능이 작가에게 실제 도움이 되는 것만 추려서 노출되도록 보장.

**산출물**:
- 회차당 작가 escalation 해결 클릭 수·미해결 잔존율 계측
- 회차당 작가 교정 건수 측정(>3이면 backfill·자동관계추출 출시 제외)
- 롤백 사용 빈도·도구 조작 시간 vs 집필 시간 비교
- go/no-go 결정 문서(도메인별 kill criteria 대조)


### Stage 3 — R1 그래프 시각화 + 작가 직접입력 엣지(자동추출 제외)  ·  effort=L  ·  depends_on=Stage 2

**목표**: 온톨로지를 리스트→속성그래프로. etype 데이터주도화 + 작가 직접 입력 ground_truth 엣지 + cytoscape 현재시점 그래프. narrative_inferred·자동추출·as-of 슬라이더는 컷.

**사용자 체감**: 작가가 인물·세력 관계를 그래프로 보고, 두 노드를 이어 '동맹/사제/소속' 같은 확정 관계를 직접 박는다. 확정 관계가 다음 회차 프롬프트 ground_truth에 합류해 본문 일관성에 반영.

**산출물**:
- EntityTypeSpec 카탈로그 + etype Literal→str 완화(factory 멤버십 검증 강제 + unknown fallback shape + 경고 emit)
- RelationEdge + Ontology.edges/edge_state_as_of/edges_as_of/canon_relations(ground_truth 슬롯)
- ontology_internal_check 엣지검사 3종(dangling/self-loop/post-death, LLM0콜)
- ontology_snapshot에 graph 키 추가(기존 키 유지) + cytoscape CDN + 가시 서브그래프 한정 레이아웃
- 작가 직접 엣지 주입 API(POST /relations, runtime_edges append, 멱등) + 노드 2선택→관계타입 드롭다운 UI

_파일_: `app/novelcopilot/domain/world.py`, `app/novelcopilot/domain/types.py`, `app/novelcopilot/domain/relations.py`, `app/novelcopilot/engine/ontology.py`, `app/novelcopilot/engine/factory.py`, `app/novelcopilot/engine/rules/predicates.py`, `app/novelcopilot/services/copilot.py`, `app/novelcopilot/api/routes.py`, `app/novelcopilot/web/index.html`, `app/novelcopilot/web/app.js`


### Stage 4 — R4 결정론 드리프트 게이트 + 엔딩주도 아크(REGEN만)  ·  effort=L  ·  depends_on=Stage 2

**목표**: 엔딩 먼저 confirm→아크 역설계, beat_planner 아크 인식, 결정론 드리프트 신호로 회차가 아크 전진을 측정. 의미 판정자·ADJUST_ARC/REPLAN_FORWARD는 컷.

**사용자 체감**: 작가가 엔딩을 먼저 정하고 아크 골격을 본다. 회차가 아크 목표(필수 사건/복선/인물)에서 벗어나면 '이 회차가 미커버: X사건' 경고가 뜨고, 작가가 해당 회차 재생성을 선택할 수 있다. 회차 늘어도 엔딩 표류 방지.

**산출물**:
- domain/narrative.py 3계층 + Beat.arc_id/WorldConfig.spine 델타(spine=None=평면 호환)
- worldgen ArcPlanner: 엔딩 후보 생성+작가 confirm+아크 역설계(backward); plants/payoffs page_id 닫힘성 결정론 lint
- beat_planner.beat_for 아크 인식(arc_anchor를 narrative 슬롯 RetrievedItem으로 주입, ground_truth 오염 금지)
- engine/drift.py 결정론 신호 4종(coverage_gap/plant_overdue/cast_mismatch/arc_overrun, DriftSignal=Violation 재사용)
- 재계획 REGEN_CHAPTER(copilot.py:125 멱등 교체 재사용) + escalation 표시 + NarrativePlanRevision append-only
- 서사 구조 탭: 엔딩 후보 카드/아크 타임라인/drift_signals 섹션

_파일_: `app/novelcopilot/domain/narrative.py(신규)`, `app/novelcopilot/domain/world.py`, `app/novelcopilot/domain/project.py`, `app/novelcopilot/worldgen/generator.py`, `app/novelcopilot/worldgen/beat_planner.py`, `app/novelcopilot/engine/drift.py(신규)`, `app/novelcopilot/services/copilot.py`, `app/novelcopilot/api/routes.py`, `app/novelcopilot/web/app.js`


### Stage 5 — R2 설정집(prose + 단일 promote)  ·  effort=L  ·  depends_on=Stage 3

**목표**: 설정집 탭을 읽기전용→편집 가능. prose 자유서술 + '캐논으로 박기' 단일 버튼. compile_bible이 promoted 항목만 factory delta로 컴파일. 3층 스키마·constraint_role 4값·backfill·RAG 색인은 컷.

**사용자 체감**: 작가가 마법체계/종족/지리/연표 등을 산문으로 자유 작성하고, 특정 항목을 '캐논으로 박기' 한 번으로 일관성 엔진이 추적하는 결정론 규칙/속성으로 승급. 박지 않은 설정은 참조용으로만(승격불가 불변식).

**산출물**:
- domain/bible.py(BibleEntry: prose+promoted 단일 게이트) + genre_templates.py(로판/현판/무협/회귀/헌터)
- engine/bible_compiler.py compile_bible(promoted만)→attributes/world_rules/timeline delta + LLM0콜 충돌 린트
- factory.build_rules/build_ontology가 compile delta merge
- migrate_world_to_bible(하위호환 부트스트랩, SSOT=compile 결과의 함수)
- 설정집 CRUD API + 편집 트리 UI + compile preview 모달(warnings)

_파일_: `app/novelcopilot/domain/bible.py(신규)`, `app/novelcopilot/domain/genre_templates.py(신규)`, `app/novelcopilot/domain/project.py`, `app/novelcopilot/engine/bible_compiler.py(신규)`, `app/novelcopilot/engine/factory.py`, `app/novelcopilot/services/copilot.py`, `app/novelcopilot/api/routes.py`, `app/novelcopilot/web/app.js`


### Stage 6 — R6 P1: 작업큐 + 버전관리  ·  effort=L  ·  depends_on=Stage 1

**목표**: SSE 동기실행→경량 작업큐(work_run 폴링), SSE를 event_log tail 구독으로 전환(끊김 복원), chapter_version/world_revision append-only 버전관리·롤백 완성. fence/lease는 멀티워커 트리거 전까지 미도입.

**사용자 체감**: 브라우저를 닫아도 회차 생성이 계속되고 재접속하면 진행 상황을 다시 본다. 설정집·온톨로지 변경 이력을 보고 과거 시점으로 롤백. 회차·노드별 토큰/비용 대시보드.

**산출물**:
- work_run 테이블 + 경량 폴링 워커(또는 APScheduler); POST /generate→{run_id} enqueue
- SSE GET /runs/{run_id}/events = event_log tail 재구독
- world_revision append-only(change_summary+delta, N마다 체크포인트) + 설정집/온톨로지 리비전 히스토리/diff/롤백
- 비용 대시보드(usage_ledger 회차·노드 분해) + system_state_badge

_파일_: `app/novelcopilot/repository/sqlite_repo.py`, `app/novelcopilot/services/copilot.py`, `app/novelcopilot/api/routes.py`, `app/novelcopilot/engine/observability.py`, `app/novelcopilot/web/app.js`


### Stage 7 — R3 genesis 대화 월드젠(신인 페르소나 확정 후)  ·  effort=XL  ·  depends_on=Stage 3, Stage 5

**목표**: one-shot worldgen을 대화형으로 감싸기. genesis 모드 + Proposal 4종(entity/relation/bible_entry/question) + 비대칭 분류(코드 판정) + accept/reject + SSE committed diff 점등. enrichment·역질문 고도화·value_change는 Later.

**사용자 체감**: 작가(특히 신인)가 시드 한 줄이 아니라 대화로 세계를 만들고, 턴마다 인물·관계·설정 항목이 우측 그래프/설정집에 즉시 자란다. R1·R2·R4가 안정화된 위에서만 가치 발생.

**산출물**:
- WorldgenSession/Proposal/Event 타입 + genesis 모드(generator.generate를 첫 부트스트랩 재활용)
- WorldgenApplier(ontology_updater classify/apply 추출·일반화); auto_commit/needs_approval/blocked 코드 판정
- worldgen SSE 스트림(routes.py:92 복제, node='worldgen' 필터) + commit_genesis 계약 게이트
- 좌측 채팅 / 우측 bible+graph 2분할 UI; committed diff로 그래프·설정집 점등

_파일_: `app/novelcopilot/worldgen/generator.py`, `app/novelcopilot/domain/worldgen_chat.py(신규)`, `app/novelcopilot/engine/ontology_updater.py`, `app/novelcopilot/services/copilot.py`, `app/novelcopilot/services/session.py`, `app/novelcopilot/api/routes.py`, `app/novelcopilot/web/worldgen.html(신규)`


### Stage 8 — Defer (트리거 대기, 도입 금지선)  ·  effort=XL  ·  depends_on=Stage 6, Stage 7

**목표**: 명시 트리거 충족 전 절대 도입 금지(cargo-cult 경계). 각 항목은 측정된 필요가 증명될 때만.

**사용자 체감**: 각 항목 도입 시 점진적 운영 안정성·심화 기능. 단 트리거 미충족 시 사용자는 아무 변화도 보지 않음(의도된 비-도입).

**산출물**:
- R6 P2: PgProjectRepository+pgvector HNSW+PG FTS(Kiwi) — 트리거: 멀티워커/멀티유저 동시쓰기
- R6 P0 동시성: fence_token CAS+lease_expiry 자동회수+work_run SKIP LOCKED — 트리거: 멀티워커
- R6 P3: Temporal(장기실행 크래시복원)+Redis pub/sub(워커경계 SSE fan-out)
- R1: narrative_inferred 엣지+작가 승인 게이트+자동관계추출(추출정확도 검증 후, 세탁경로 결정론 게이트 동반), cardinality/symmetric/혈연순환 검사, Postgres 인접리스트
- R2: 3층 스키마+constraint_role 4값+bible backfill(검토 큐)+RAG 색인+revision
- R4: 의미 드리프트 외부 LLM 판정자, ADJUST_ARC/REPLAN_FORWARD
- R5: canon_edit 직접편집+revert tombstone+생성중 inbox(absorb/urgent_restart)+state_as_of 정렬키 확장(회귀 골든테스트 동반)
- R3: enrichment 모드(R5 통합)+InterviewQuestion 고도화


---

## 부록 C. 의도적 컷(과설계 방지)

- R1 narrative_inferred 추정 엣지 + 작가 승인 게이트 + 점선 시각화 (추출정확도 검증 전엔 순수 부채; MVP는 작가 직접입력 ground_truth 엣지만)
- R1 관계 자동추출 (생성본문→ground_truth 엣지 세탁 통로 위험; narrative→ground_truth 자동승격 금지 불변식 보호)
- R1 ontology_internal_check 엣지검사 중 cardinality 위반/symmetric 모순/혈연 순환 DFS (엣지 수십개 실데이터 발생 후; MVP는 dangling/self-loop/post-death 3종)
- R1 as-of-chapter 슬라이더 (작가가 과거 시점 스크럽한다는 증거 전; MVP는 현재시점 그래프 하나)
- R2 3층 스키마(prose/structured/ontology_links) + constraint_role 4값(none/narrative_only/promotable/promoted) (작가가 같은 항목 두 번 승인; MVP는 prose + 단일 promote 버튼)
- R2 bible backfill 본문 역추출 + RAG bible 색인 + revision append-only (추출정확도 검증 + 검토 큐 UX 후)
- R4 의미 드리프트 외부 LLM 판정자(theme_drift/tone_break/arc_goal_distance) (escalation-only·게이트 비구속이라 비용+노이즈만; 결정론 신호로 충분)
- R4 재계획 ADJUST_ARC/REPLAN_FORWARD (아크 모델 안정화 후; MVP는 REGEN_CHAPTER만)
- R5 생성중 개입 intervention_inbox + cancel_event + scene-boundary absorb + urgent_restart (동시성 재설계급인데 수요 근거 0; 회차는 수분짜리라 after_chapter로 충분)
- R5 canon_edit 직접편집 + directive 흡수 + revert tombstone + escalation 4액션(alternative/regenerate) (ESCALATED 실행증명 후; MVP는 escalation 영속화+승인/무시 2액션 + chapter_inject만)
- R5 state_as_of 정렬키 확장(eff_from→(eff_from,provenance_rank,seq)) (기존 회차 재현 회귀 위험; 골든테스트 동반 조건부)
- R6 P0 동시성 장치: fence_token CAS + lease_expiry 자동회수 + work_run 폴링 워커 (작가 1인·단일 uvicorn에 과조기; 단일 BEGIN IMMEDIATE+멱등키로 충분, 멀티워커 트리거 시 P2)
- R6 P2 Postgres+pgvector+PG FTS / P3 Temporal / Redis pub/sub (각 명시 트리거 충족 전 금지)
- R3 enrichment 모드 + value_change/beats_change/attribute_def Proposal + InterviewQuestion intent 고도화 (genesis MVP는 entity/relation/bible_entry/question 4종)
- 6개 도메인 동시 착수 (순환 의존으로 통합비용 폭증; 측정-게이트 단일 수직 슬라이스로 대체)

---

## 부록 D. 미해결 결정사항(사용자 확인 필요)

1. 타깃 작가 페르소나를 '다작 프로'로 1차 확정했으나, 실제 베타 작가 풀에서 검증되지 않았다. 프로가 R6 롤백/R4 드리프트를 실제로 쓰는지, 신인용 R3를 끝까지 미루는 게 맞는지는 Stage 2 측정에서 재검토 필요.
2. ESCALATED 결정론 분기가 정상 생성 경로에서 사실상 사문화(checker가 본문을 internal_check에 안 넘김)라면, escalation 영속 큐가 채워지는 실제 소스는 quasi 위반(harness hard_violation)과 ontology_updater contradiction뿐이다. Stage 0 오염주입에서 어느 분기가 실제로 점등하는지에 따라 EscalationItem.source/op enum 범위를 줄여야 할 수 있다.
3. 추출 정확도(Stage 0)가 임계 미달이면 R1 자동관계추출/R2 backfill/R4 coverage_gap 태그 신뢰도가 동시에 흔들린다. coverage_gap의 event_tags 추출이 noisy하면 false coverage_gap이 작가를 괴롭히는데, 통제 태그 set 비교로도 추출 자체가 LLM이라 완전 결정론이 아니다 — quasi 등급 유지가 맞는지 실측 필요.
4. narrative_order 단일 좌표계로 통일했으나, R4 아크 전환 시점과 R1 엣지 eff_from이 같은 회차에 여러 변화가 몰릴 때 순서(intra-chapter ordering)를 어떻게 결정론적으로 정렬할지(seq 보조키 필요 여부)는 미해결.
5. R6 P0에서 JSON→SQLite 마이그레이션 시 기존 16.8MB 프로젝트의 임베딩이 어느 모델로 생성됐는지 메타데이터가 현재 없다(rag.py는 정수 version만). backfill 시 model_version을 '미상(legacy)'으로 태깅하면 향후 재색인 트리거를 어떻게 안전하게 거는지 정책 필요.
6. 작가=최고권위 vs det 게이트(SSOT 자기모순 reject)의 경계에서, 작가가 '내 소설인데 왜 안 돼'라고 할 때 보여줄 동반수정 제안 UX의 구체 카피·플로우가 미설계. override 가능 정책 범위와 reject 고정 범위의 경계선이 작가에게 직관적으로 전달되는지 사용성 테스트 필요.
7. feature flag 기본 OFF 전략에서, 한 작가가 일부 도메인만 켜고 일부는 끈 상태로 장편을 진행하다 중간에 켜면(예: 20화에서 R4 spine 도입) 과거 회차에 대한 소급 일관성을 어떻게 처리할지(평면→계층 모드 전환의 마이그레이션) 미정의.