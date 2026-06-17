이 작업은 컨텍스트 어셈블리 리뷰 최종 보고서를 한국어 마크다운으로 작성하는 것이다. 이미 모든 진단 데이터(확정 갭, 의도된 비주입, 기각 발견)가 제공되어 있고, 보고서 구조도 명시되어 있다. 구현 금지·진단/권고만 요구되므로 코드 탐색 없이 제공된 데이터를 종합해 보고서를 작성한다.

제공된 데이터를 분석해 영향×난이도 순으로 종합하겠다.

# 컨텍스트 어셈블리 리뷰 — 최종 보고서

> 질문: "우리가 보유한 정보와 디버깅(gen_context)으로 드러난 정보를 종합할 때, 생성에 들어가는 컨텍스트에 '빠진 정보·불충분·틀림·중복'이 있는가?"
> 범위: 진단·권고만. 구현·코드 변경 없음. 모든 교정안은 무강제 원칙(컨텍스트 주입 / 예산 재배분 / 가시화 / 작가 승인 게이트) 범위 내. 작법·규칙 강제 지시 금지.

---

## 1. 한 줄 평결

**골격은 작동하나, '재미·서사 반구'로 가야 할 컨텍스트가 결정론 캐논 슬롯의 비대칭 규율에 막혀 narrative 레일로 충분히 흐르지 못하고, 디버그(gen_context)가 실제 주입의 절반(상수 system 헤더·교정 파이프라인·이어쓰기 콜)을 가린다.** 충분성은 '대체로 충분'이나 두 개의 진짜 구멍이 있다 — (a) **에피소드 경계 직후 story_so_far가 1줄로 붕괴(high)**, (b) **세계규칙(world_rules) 텍스트가 생성 프롬프트에 사실상 미주입인데 헤더는 '세계규칙 절대 위반 금지'라고 약속**(이 작품 한정 마이그레이션 갭). 나머지는 med~low. 단, **중요 단서**: 확정 갭 중 다수가 "이 작품(83020455e387)이 G5 이전 생성이라 genre_contract=None, bible 54항목 전부 promoted=False, world_rules가 bible에 미마이그레이션"이라는 **단일 신구-마이그레이션 root cause로 수렴**한다. 신규(G5 이후) 작품은 이 구멍 대부분을 구조적으로 받지 않는다.

---

## 2. 빠진 정보 (Missing) — 보유했으나 주입 안 됨, 영향 큰 순

### M-1. 세계규칙(world_rules) 본문이 생성 프롬프트에 전혀 주입되지 않음 — 헤더는 '세계규칙 절대 위반 금지'라 약속 (severity: med, but 영향 큼)
- **무엇**: `prompts.py:36` 헤더가 `[확정 설정 — 절대 위반 금지(눈색·소속·생사·등급·관계·세계규칙)]`라고 약속하지만, 그 블록(`gt`)은 `canon_facts`(속성)+`canon_relations`(관계)로만 채워진다(`harness.py:337-338`). 세계규칙 텍스트는 `ontology.rules`(별도 list)에 들어가지만 프롬프트 어디에도 직렬화되지 않는다. bible_digest로 들어가려면 promoted여야 하는데 이 작품 bible 54항목 전부 promoted=False.
- **증거**: `ontology.py:33` `self.rules: list[str]=[]` / `:43-44` add_rule은 append만, 직렬화 호출 없음. 8·9화 `final_violations=['worldrule(no_reawaken)','worldrule(gate_random)']` 두 화 연속, 그런데 anchors/ground_truth 어디에도 두 규칙 문구가 없음.
- **영향**: 모델은 `no_reawaken`('각성은 일생에 한 번뿐')·`gate_random` 규칙을 한 번도 보지 못한 채 집필 → 두 화 연속 동일 위반 → 비주입→사후검출→advisory(비차단)이라 교정 루프가 닫히지 않는다.
- **어디서 고치나(무강제)**: ① **헤더 진실화** — `prompts.py:36`에서 '세계규칙'을 빼거나 별도 저신뢰 슬롯('세계 규칙(참고 — 가능하면 어기지 마라)')으로 분리. ② **사장 데이터 재활용** — `ontology.rules`(또는 원천 `world_rules.text`)를 `RetrievedItem(source='worldrule')`로 narrative 상단에 무예산 고정(advisory). ③ **온보딩 동기화가 root cause** — `migrate_world_to_bible`(`bible_compiler.py:64-72`)는 world_rule을 `promoted=True`로 BibleEntry화하도록 이미 작성됨. 이 마이그레이션이 기존 프로젝트에 한 번도 안 돈 것이 핵심 → `copilot.py:978` 부트스트랩 경로 점검·1회 실행 보강. 그러면 룰이 digest score=100으로 상시 포함.

### M-2. 장르 계약(genre_contract) 이 작품 미생성 — 쾌감엔진·전제자산·독자기대 전 회차·독자평가 비주입 (severity: med)
- **무엇**: 8·9화 모두 '장르 계약 주입 여부: X(이 작품 미생성)'. `_contract_block`은 build_spine·_gen_episodes·beat_for_episode 세 설계 지점에 주입되도록 코드에 있으나, `world.genre_contract`가 None이면 빈 문자열(`arc_planner.py:18-32`). reader_desk도 `expectations=None`(`copilot.py:760`)으로 호출 → 독자 desk가 장르 기대치 없이 평가.
- **영향**: 쾌감 엔진(미래지식 치트·복수 카타르시스)·핵심 동력 전제(회귀)·독자 기대가 설계 3지점+독자평가에 모두 비주입. **G5 이후 작품은 받는 컨텍스트를 이 작품은 구조적으로 못 받음 → 신구 작품 품질 비대칭(마이그레이션 누락).**
- **어디서 고치나(무강제)**: (1) **Backfill** — 로드 시 genre_contract=None이면 world의 genre/premise/synopsis로부터 GenreContract를 LLM 초안 추론(provenance=ai_unreviewed). 강제 적용 금지 — 작가가 promote해야 None→캐논 활성화(bible 초안과 동일 게이트). (2) **가시화** — 대시보드에 'G5 이전 생성 — 장르 계약 미설정(정체성 컨텍스트 비주입 중)' 배지 + '계약 생성/검토' CTA. (3) 승인 전까지 현행 빈값 유지(무강제). 코드 강제 변경 불필요(기존 None 분기 그대로).

### M-3. 예정된 상태변화(미래 timeline) 비주입 — secret '발각'·awakening '초월' 등 다가올 전환을 설계가 모름 (severity: low)
- **무엇**: 온톨로지 timeline은 `state_as_of`로 과거~현재만 조회(`ontology.py:62-66` `f<=chapter`). 작가가 'N화에 비밀 발각' 같은 미래 전환을 박아도 해당 화 전까지 설계 입력에 안 보인다. `beat_for_episode`에 timeline 미래값 전달 없음.
- **영향**: 절정 설계가 '곧 닥칠 전환'을 backward로 활용 못 해 복선·상태변화 정합이 우연에 맡겨짐. **단 현 데이터에선 작가가 미래 timeline을 거의 안 박아 영향 작음.**
- **어디서 고치나(무강제)**: `beat_for_episode` usr 블록에 '예정 전환(읽기 전용)' 슬롯 추가 — current 이후 eff_from을 가진 **ground_truth-tier**(작가 명시=캐논)만 모아 `[예정 전환 — 작가가 박은 미래 캐논(참고용, 억지 수렴 금지)]`로. plant_notes와 동일 advisory 슬롯. narrative_inferred(기계추정 미래)·pov 엣지 제외(비대칭 유지). 게이트/canon_facts(현재시점 박기)는 절대 불변(미래값이 하드게이트로 새는 것 방지).

### (참고) M-1과 동일 root: ground_truth 헤더가 '세계규칙'을 광고하나 gt에 RuleSpec 0건 — 디버그가 누락을 드러내지 못함 (§6에서 디버그 측면으로 재론)

---

## 3. 불충분 (Insufficient) — 주입됐으나 얕거나 예산컷

### I-1. story_so_far 12k 예산이 에피소드 경계 직후 '롤업 1줄'로 과압축 — 누적 줄거리 구조적 붕괴 (severity: HIGH — 최우선)
- **무엇**: `_build_story_so_far_hier`는 완료 에피소드는 1줄 롤업, '현재(미완) 에피소드의 FINALIZED 회차'만 상세. 9화 시점 현재 에피소드 '멸시와 단서'는 막 시작(진행 회차 0)이라 cur_lines가 비고, story_so_far가 완료 에피소드 롤업 **단 1문단**만 남는다. 8화(같은 에피소드 다수 상세)와 정보량 격차 극심.
- **증거**: `copilot.py:149-152`. 9화 story_so_far 전체가 `[회귀의 서막…] …새로운 위협이 시작된다.` 단 1문단 vs 8화는 2500자 빼곡.
- **영향**: 경계 직후 회차는 누적 맥락이 1줄로 줄어 **12k 예산이 거의 사용되지 않은 채(silent 예산 미달)** 모델이 prev_chapter+직전 상세에만 의존 → 장기 복선·먼 회차 사건 사실상 망각. '예산은 큰데 콘텐츠가 없는 구조적 기아.'
- **어디서 고치나(무강제)**: (1) **예산 충전** — cur_lines가 비거나 잔여 예산이 크면, 직전 N개 완료 에피소드를 1줄 롤업이 아니라 '회차 상세 시놉시스'로 격상해 남은 예산까지 채움(최신 우선). (2) **가시화** — cur_lines 비어 dropped=0이어도 'story_so_far가 경계로 1줄 축소됨(예산 X/12000 사용)' assemble_memory 이벤트 발화해 silent 미달 노출. (3) **작가 게이트(선택)** — 경계 회차에서 '직전 에피소드 상세를 더 끌어올릴까요?' 제안만.

### I-2. cast_context 6명 고정 슬라이스 — 미등장 시드 인물(윤세라·박지민)이 슬롯 점유, 실제 등장 NPC(연결자·그림자)는 profile 없어 누락 (severity: med)
- **무엇**: `cast_ids = (ep.required_cast + 전체 character id)[:6]`. 8·9화 cast_context 5명(서진우/윤세라/강현식/이도훈/박지민) 중 윤세라·박지민은 본문 미등장. 반대로 이번 화 주역 NPC '연결자/그림자 속 존재'는 profile 없는 잠정 엔티티라 `if prof:` 분기에서 누락.
- **영향**: 설계 콜이 정작 갈등을 끄는 NPC 동기를 못 보고 미등장 시드 프로필로 슬롯 낭비. '욕망 있는 인물에서 사건이 나오게'(G6)가 핵심 적대자에 미작동.
- **어디서 고치나(무강제)**: (A) cast_ids 후보를 'ont의 actor 엔티티(자동추출 NPC 포함)'로 확장, 우선순위 `required_cast > 이번 화 beat.entities·직전 등장 actor > 시드 주연` 재정렬 후 [:6]. (B) profile 없는 잠정 엔티티는 wiki_page의 상태·감정·목표 1~2줄을 메타로 끌어와 채움(추가 LLM 콜 0). (C) 가시화 — cast_context와 beat.entities 교집합/차집합을 bus.emit으로 노출.

### I-3. 인물 욕망·비밀이 프로필 자유텍스트에만 — 구조화된 desire/secret 필드 없이 profile[:220]로 절단 주입 (severity: med)
- **무엇**: 온톨로지는 secret 상태값('숨김/발각')을 추적·주입하나, '무엇이 비밀인지'(회귀자라는 사실)는 `EntitySpec.profile`(`world.py:71`) 자유텍스트에만 있고 구조화된 desire/secret 필드 없음. `_cast_context`는 `prof[:220]`로 절단.
- **영향**: 조연(이도훈·박지민 등 profile 짧음)은 동기가 얕게만 전달돼 '반사판' 위험. secret 상태값은 가는데 '무엇이 걸려있나'(드라마 재료)는 구조적으로 미주입.
- **어디서 고치나(무강제)**: (A) 집필 board에 '인물 비밀/욕망 한 줄'을 narrative tier(서사 의도, ground_truth 아님 — 비대칭 유지)로 추가. (B) `plot_thread regression_secret` 위키 body를 worldgen이 채워 wiki.retrieve 경로로 자연 주입(기존 채널, 신규 게이트 0). (C) 회차 등장 인물카드에 bible_digest '등장 가산점' 부여.

### I-4. cast_context attrs[:4] 슬라이스 — rank 등 핵심 추적축이 비결정적으로 누락, 같은 회차 ground_truth와 불일치 (severity: low)
- **무엇**: `_cast_context`는 `for a in list(ent.attrs)[:4]`로 status 제외 후 앞 4개만. 서진우 attrs 6개라 knowledge/secret 중 1개 누락 가능. dict 순서가 흔들리면 핵심 rank가 창 밖으로 밀림 — **8화 cast_context엔 rank 없음, 9화엔 rank=F 등장(들쭉날쭉)**. 같은 회차 ground_truth는 'rank=0/F'를 박는데 cast_context는 누락. 이 작품 핵심 동력이 'F급의 반격'인데 설계자가 등급을 못 보는 회차 발생.
- **추가 버그**: `copilot.py:115` falsy 가드 `if v:`가 정수 0 같은 캐논값을 묵음 탈락시킴.
- **어디서 고치나(무강제)**: `if v:` → `if v is not None:`(ground_truth의 str(v) 정책과 일치). 임의 [:4] 대신 '추적 속성(state/numeric vocab 선언) 우선' 정렬 후 상한, 또는 캡을 6으로 상향. 결정론 조회 결과만 노출하는 표기 일관성 교정.

### I-5. bible 디제스트 — 3500자 단일 항목, promoted=0이라 회차 무관 일반카드가 상단 점유 가능 (severity: low)
- **무엇**: bible_digest는 promoted 항목을 100점 최상단 고정하는데 이 작품 54항목 전부 promoted=False → 정렬이 context_hint 키워드 매칭에만 의존. 약매칭이면 '[능력 체계] Overview' 같은 일반 설정이 상단 점유.
- **주의(검증 결과)**: 적대 검증에서 이 발견의 '과장 부분'이 확인됨 — bible은 13개가 아니라 **단일 패킹 RetrievedItem 1개**이고, 디버그의 240자 트림이 '1카드만 노출'로 보이게 한 것. 8화 시뮬레이션상 worldrule 카드가 오히려 최상위 3줄로 선별됨(밀려나지 않음). **즉 '예산컷으로 회차규칙 유실'은 이 작품에서 실측되지 않음.** 남는 실재 우려는 promoted 부스트가 사실상 죽어 동점 시 일반 overview가 상단을 먹을 수 있다는 점뿐 → low.
- **어디서 고치나(무강제)**: 키워드 매칭 0 + title='Overview'인 일반카드 약한 디프라이오리티. bible_dropped 가시화에 '컷된 카드 제목 목록'까지 emit(현 훅 payload 확장). 작가가 회차 핵심 설정을 promote하도록 UI 유도(비대칭 일치).

---

## 4. 틀림 / Stale (Correctness) — 우선 직시

### C-1. 자동 발명 잠정 엔티티('그림자 속 존재')가 명부(voice_roster)에 캐논 인명으로 평평하게 주입 — 미스터리 조기 확정 압박 (severity: low, 비대칭 위반)
- **무엇**: 8화에서 발명된 '그림자 속 존재'가 provisional=True로 커밋된 뒤, 9화 voice_roster '이미 등록된 고유명사'에 들어감. `harness.py:328` `roster_names`는 provisional을 구분 않고 모든 entities.name을 '같은 표기 쓰라'로 주입. 이 작품 12 엔티티 중 9개가 잠정.
- **영향**: 아직 정체불명 떡밥이 확정 인명처럼 '이 표기 그대로'로 주입 → 모델이 미스터리를 고정 명사로 조기 확정·반복 호명(슬로우번 잠식).
- **어디서 고치나(무강제)**: 명부를 분층 — 확정(provisional=False)은 현행 '같은 대상이면 이 표기'(참고). 잠정(provisional=True)은 `[잠정 떡밥(미확정 — 새 이름 발명만 피하고, 고정 명사로 조기 확정 금지)]` 별도 라벨. 재발명 방지 목적은 유지하되 조기 고정 압박 제거.

### C-2. 설정집 [인물]에 본 작품과 어긋난 캐논(윤서희·최강우) 잔존 — anchors 주입 시 명부 모순 위험 (severity: med)
- **무엇**: bible [인물] 카테고리에 실제 등장(윤세라·강현식)이 아닌 '윤서희'·'최강우'가 캐논으로 박힘(worldgen 초기 생성물, promoted=False). bible_digest가 인물 hint로 이 카드를 뽑으면 명부와 충돌하는 인명을 흘림.
- **영향**: 모델이 윤세라/강현식과 다른 인물로 오인하거나 새 이름 발명 단서. 8·9화엔 우연히 안 뽑혔으나 **인물 중심 회차에서 발화 위험.**
- **어디서 고치나(무강제)**: (a) 가시화 — bible_digest가 [인물] 카드 주입 시 인명 토큰이 roster에 없으면 'orphan_character_card' bus.emit(현 bible_truncated 패턴). (b) score에 roster 정합 가중(미일치 인물카드 동점 시 후순위, 하드 배제 아님). (c) 승인 게이트 — 작가 리뷰 큐에 '명부 불일치' 배지로 묶어 deprecate/교정.

### C-3. cast_context 인물 상태가 표층 메타만 — 본문이 만든 실제 변화(신상 43% 소거·정체성 붕괴)는 미반영, 위키와 stale (severity: low)
- **무엇**: `_cast_context`가 주입하는 서진우 상태는 정적 profile + rank/affiliation/awakening뿐. 위키 노트는 '신상 43% 잔존, 각인 열화, 정체성 붕괴 직전'까지 추적하나 설계(beat_for_episode)엔 이 시점 상태가 없다(추적축 6개만 순회).
- **영향**: 설계가 '이름 절반 버린, 정체성 붕괴 중' 현재 진우가 아니라 '만년 F급' 원형으로 비트를 짬 → 설계 레이어가 본문이 일으킨 거대 변화에 둔감. **단 recent로 같은 정보가 설계에 일부 도달하므로 ROI 낮음.**
- **어디서 고치나(무강제)**: `_cast_context`에 active wiki_page '상태:' 첫 문장을 narrative_inferred 표식 1줄(`[서사상태(잠정): …]`)로 cap ~80자 주입. 캐논 박기 아님(비대칭 유지).

### C-4. 동일 라벨 관계가 ground_truth와 narrative_inferred로 중복 추출 — 박기 노이즈, 매 회차 재발견 (severity: low)
- **무엇**: 강현식→서진우 '라이벌'이 시드 ground_truth와 자동추출 narrative_inferred로 2건 공존. 주입은 1건만(비대칭 보존) 가지만 매 회차 ontology_propose가 같은 관계를 또 뽑아 보유 엣지 51건을 부풀림. `ontology_updater.py:249-251` skip이 src/dst/rel_id만 비교, **tier 무관**이라 ground_truth 엣지가 있어도 narrative_inferred 신규 생성을 못 막음.
- **영향**: 주입 비대칭은 보존되나 추출 토큰 낭비(8·9화 5683~5798)·엣지 인플레, 작가 텔레메트리 신호 대 잡음 저하.
- **어디서 고치나(무강제)**: dedup 비교 키를 'rel_id 의미 정규화'로 승격(자유라벨 rival ↔ 등록키 rival_of 매핑 테이블, 데이터 주도). seed_edges 적재 시 symmetric 스펙을 order_edge로 정규화. 텔레메트리 기본 뷰를 tier-aware deduped로, 의미 중복 추정 엣지는 회색 처리·작가 승인 시만 승격.

### C-5. 매 회차 동일 worldrule 위반(no_reawaken·gate_random)이 final_violations로 누적되나 교정 컨텍스트로 환류 안 됨 + 회차 UI에 미표시 (severity: med)
- **무엇**: 8·9화 모두 같은 위반 반복인데 status=FINALIZED(worldrule이 SEMANTIC=비차단). 다음 회차 ground_truth/anchors에 '이 규칙 또 어겼다' 환류 경로 없음. **추가(검증 확인)**: `final_violations`는 ChapterRecord에 저장되나 `app.js`가 이 필드를 렌더하지 않음 → 측정은 됐는데 작가에게 advisory로조차 안 보임.
- **영향**: 비주입→사후검출→advisory(비차단)→미표시로 교정 루프가 닫히지 않아 영구 반복.
- **어디서 고치나(무강제, 두더지잡기 금지 — 새 검출기 X, 출력 배선만)**: ① 가시화 — `app.js` consistency_check 렌더를 `ev.hard>0` 대신 `ev.violations>0`로 보강해 비구속 위반도 '세계규칙 충돌 N건(비구속)'으로(차단 아님). ② 반복 텔레메트리 — prior 회차 worldrule 위반 집계해 '최근 K화 연속 동일 규칙 위반' 이벤트(worldrule.recurrent). ③ 선택적 예방 — anchors에 `[worldrule_reminder]` advisory 1줄(ground_truth로는 안 박음). 작가가 회귀물 예외로 판단하면 demote(`_demote_rule` 경로 존재). **단 C-5는 M-1(룰 텍스트 비주입)이 root이므로 M-1과 묶어 해소.**

### C-6. 자동 발명(uncast) 인물이 cast_debut 앵커 없이 콜드 드롭 / scan_present_ids 표면 매칭 한계 (severity: low)
- **무엇**: cast_debut 앵커는 'profile 있는 미도입 인물'에만 걸림(`copilot.py:647`). '그림자 속 존재'처럼 설계 없이 발명된 uncast 인물(profile 없음)은 첫 등장 소개 앵커를 못 받음. 또 introduced 마킹이 `scan_present_ids`(이름 표면 스캔) 단일 경로라 별칭·표기 변형 시 중복 부착/영구 False.
- **영향**: 설계 밖 출생 인물이 콜드드롭 방지 장치 우회 → 독자가 맥락 없는 미확인 인물 반복 마주침. **단 현 회차들은 주연만 등장해 미표출.**
- **어디서 고치나(무강제)**: cast_debut profile 필수 조건 완화 — uncast 인물도 profile 대신 wiki_page state/직전 등장 맥락을 debut 재료로(빈 앵커 금지). 데뷔 앵커에 등록 별칭 동반 노출. 'N회 연속 재부착=데뷔 미정착' advisory. 자동 introduced 플립 강제는 안 함.

---

## 5. 중복 / 노이즈 (Redundancy) — 예산 희석 지점

핵심 root는 **`narrative = anchors + rag(k6) + wiki(k3)`를 dedup/rerank 없이 무가공 concat**(N-0)이라는 점. 아래는 그로 인한 구체 중복.

### N-1. prev_chapter 전문(5359~5912자)과 rag_chunk가 같은 직전 회차 문단을 이중 주입 (severity: med — 중복 중 최우선)
- **무엇**: prev_chapter 슬롯에 직전 회차 원문을 통째(8000자 예산) 넣는데, `rag.search`는 '모든 회차(chapter<=as_of)' 후보로 query=beat.summary(직전 사건 중심) top-6을 또 뽑아 narrative에 넣음. 직전 회차 문단이 유사도 상위라 prev_chapter 일부를 복제(9화 rag_chunk 6 == prev tail 마지막 문단).
- **영향**: 6개 rag 슬롯 절반가량이 prev_chapter가 이미 준 직전 장면 재전달 → 먼 회차 세계설정·복선 회수에 쓸 검색 슬롯이 근거리 중복으로 소진(토큰은 쓰고 새 정보 0).
- **어디서 고치나(무강제)**: rag.search 후보에서 **직전 회차 배제**(`as_of_chapter=ch_no-2` 또는 `exclude_chapter=ch_no-1` 마스크). 직전은 prev_chapter+recent 말미가 전담하므로 RAG는 모듈 docstring 의도('이전 회차 서사 배경')대로 먼 회차에 슬롯을 씀. 반환 직전 prev_text와 자카드>0.9 청크 dedup. `bus.emit('rag_prev_overlap', dropped=N)` 가시화. **주의**: prev/recent 말미는 의도적 '연속성 인계'이므로 건드리지 않음 — 제거 대상은 'RAG가 같은 직전 장면을 3번째로 또 전달'하는 부분뿐.

### N-2. rag_chunk와 wiki_page가 같은 query(beat.summary)로 같은 사건을 두 어휘로 중복 회수 (severity: low)
- **무엇**: narrative가 `rag.search(beat.summary)`와 `wiki.retrieve(beat.summary)`를 같은 쿼리로 호출. 둘 다 직전 1~2회차 같은 장면(비가시계 탈출·신상 소거)으로 수렴. rag_k6+wiki_k3=9 슬롯이 실질 3~4 사건만 9가지 표현으로 채움.
- **어디서 고치나(무강제)**: 교차 토픽 soft de-emphasis(제거 아닌 하위 정렬, gen_context에 'demoted' 플래그 가시화). wiki.retrieve query를 인물 지향(involved 인물명+hook/감정축)으로 살짝 분리해 동시 수렴 편향 완화.

### N-3. 직전 회차 정보가 prev_chapter·story_so_far·recent·rag 4경로 반복 (설계/집필 분리로 부분 완화) (severity: low)
- **무엇**: (a)집필 prev_chapter 전문 (b)집필 story_so_far 현재 에피소드 상세 (c)설계 recent (d)집필 rag_chunk로 최대 4번. (c)는 설계 프롬프트라 토큰 분리되나 (a)(b)(d)는 같은 집필 프롬프트에서 3중 공존.
- **영향**: 직전 장면 과대표현이 모델의 재서술·동일 클라이맥스 재연 경향 키움(worldrule 위반 반복과 정합). `_continue`가 prev/sofar를 빼는 건 부분 대응이나 1차 _draft엔 3중 공존.
- **어디서 고치나(무강제)**: N-1 직전 회차 rag 디랭크와 동일. prev_chapter 있는 회차는 story_so_far_hier의 직전 화 detail_synopsis를 롤업만 유지. 'prev ∩ rag 중복 청크 수' 텔레메트리.

### N-4. wiki_page와 ground_truth가 같은 인물 현재 상태 중복 기술 (캐논 vs 설정 경계 흐림) (severity: none)
- **무엇**: ground_truth가 'rank=F/knowledge=부분' 결정론 캐논을 박는데, wiki_page도 같은 상태를 산문으로 재진술. 다른 신뢰등급이나 표면 정보 겹침.
- **어디서 고치나(무강제)**: **어셈블리에서 wiki 산문을 정규식으로 잘라내지 말 것**(작가 산문 훼손). write-time 비주입 규칙(`wiki.py:50`)을 강화 — ingest 프롬프트에 '등급/소속/생사 같은 온톨로지 소유 수치는 상태 문장에서도 언급 금지, 변화는 산문 맥락으로만' 한 줄 추가. 측정 카운터만 노출.

### N-5. (none) arc_anchor와 story_so_far 롤업 헤더가 아크/에피소드 제목 ~15자 echo / restraint 디버그 2중 표기
- 실 프롬프트 토큰 영향 미미. 우선순위 최하 — 교정 불요 권고. restraint는 실주입 1회뿐이고 plan.restraint는 디버그 사본(라벨로 'voice_roster의 실주입 미러'임만 명기하면 감사 오독 차단).

---

## 6. 디버그 커버리지 (메타) — gen_context가 실제 주입을 못 보여주는 부분

> **이 영역이 가장 체계적인 구멍이다.** draft_ctx는 '첫 _draft의 입력 슬롯'만 담아, 실제 모델에 들어가는 것의 절반(상수 system 헤더·정적 지시문·이어쓰기 콜·교정 파이프라인)을 통째로 가린다. 모두 **생성 무변경, 가시화만 추가**.

| # | 가려진 것 | 증상 진단 불가 항목 | sev |
|---|---|---|---|
| D-1 | **style_block(문체 규칙 11개)이 매 draft/rewrite/continue의 system 헤더로 주입되나 draft_ctx에 0글자** | 조판·시점·시제·말버릇 증상의 '절반(상수 헤더)'이 비가시 | med |
| D-2 | **style 규칙 #1의 '1,800자/회차 5,000~5,500자' 지시 ↔ 코드 주석 '모델은 분량을 모른다'가 정면 모순**인데 둘 다 디버그에 없음 | 8화 5359·9화 4757자로 짧아지는 경향의 원인 후보(충돌 지시)를 작가가 발견 못 함 | med |
| D-3 | **회차 끝맺음(hook/closing) 정책 텍스트**(반복 금지+recent_tails 3개+예고편 금지)가 실제 주입되나 draft_ctx는 hook_type '라벨' 한 단어만 | 8·9화 둘 다 '접근하는 목소리/그림자 속삭임'으로 끝나는 훅 재탕 진단의 핵심 입력 비가시 | med |
| D-4 | **분량 미달 시 _continue '이어쓰기' 콜(prev=''·sofar='' 비우고 sofar[-3500:]+key_events로 별도 생성)**이 gen_context에 전무 | 9화 4757자 후반부 수천 자가 '다른 컨텍스트'로 쓰였다는 사실 비가시 → 후반 인과·설정 일탈을 '전체 슬롯'으로 오판 | med |
| D-5 | **assemble가 박는 리터럴 지시문**('절대 위반 금지'·'세계 고유 설정 1회 구현'·'되묻기 금지'·'연속성 지시')이 draft_ctx에 없음 — 슬롯이 '참고'인지 '강한 지시와 함께'인지 구분 불가 | harness-over-model 원칙 준수 여부 감사 불가 | low |
| D-6 | **ground_truth 헤더가 '세계규칙'을 광고하나 gt에 RuleSpec 0건** — 디버그가 이 누락을 가림(M-1의 디버그 측면) | '규칙을 줬는데 왜 어기나'로 작가 오해 | med |
| D-7 | **prev_chapter(최대 8000자)·story_so_far(예산 12000)가 실제 주입되나 draft_ctx는 '글자수'·'[:2500] 트림'만** — 트림 임계 불일치 | '누적 줄거리가 짧아 망각'이라는 오판을 트림이 적극 유발 | low |
| D-8 | **교정 파이프라인(_fix_tics·_regen_tail·_continuity_polish·_fix_tense 5단)**의 입력·diff가 gen_context 밖 — 최종 본문은 초안+5단 패치인데 '주입 vs 출력' 대조 불완전 | reader_desk 통과 회차도 말미가 _regen_tail로 교체됐을 수 있음 | low |
| D-9 | **voice_roster가 보이스카드+restraint+명부 3종을 한 문자열 [:1600] 트림** — 어디서 잘렸는지·restraint 몇 개인지 비가시 | 틱/말버릇 절제 효과를 분리 추적 불가 | low |

**공통 교정(무강제·표시 전용)**: draft_ctx에 (1) `style_block`/`style_rules`+`length_norm`, (2) `hook_instruction`+`recent_tails`+`ending_mode`, (3) `continuations`(라운드별 이어쓰기 메타)+`n_continuations`+`first_draft_context_only:True`, (4) `static_directives`(지시문 래퍼 라벨), (5) `active_world_rules`(룰+promoted 여부), (6) prev_chapter '머리/꼬리 발췌'+`injected_chars`+`budget`, story_so_far `full_chars`+`budget`+`displayed_chars`, (7) `corrections`(단계별 fired/count/diff 발췌) 슬롯 추가. **이상적으로는 `assemble()`/`_draft()`가 (본문 문자열, 사용 지시문/hook 라벨)을 반환하도록 단일 추출점화** — 라벨 역추정·하드코딩 중복 방지. 생성 1바이트 무변경.

---

## 7. 권고 우선순위 (영향 × 난이도, 무강제 준수)

> 모든 권고는 컨텍스트 주입 / 예산 재배분 / 가시화 / 작가 승인 게이트 범위. '강제 지시'(작법·규칙을 모델에 명령) 없음.

| 우선 | 권고 | 해소 항목 | 영향 | 난이도 |
|---|---|---|---|---|
| **1** | **story_so_far 경계-기아 충전 + silent 미달 가시화** — cur_lines 비면 직전 완료 에피소드 회차 상세로 예산 backfill, 'X/12000 사용' 이벤트 | I-1 | 高(누적 맥락 붕괴 직접 해소) | 低 |
| **2** | **마이그레이션 갭 일괄 부트스트랩** — 기존 프로젝트 로드 시 `migrate_world_to_bible`(world_rules→promoted bible) 1회 실행 점검 + genre_contract=None backfill 초안(작가 promote 게이트) + 대시보드 배지 | M-1·M-2·C-5 root | 高(세계규칙 주입·신구 비대칭·룰 위반 반복을 한 번에) | 中 |
| **3** | **디버그 커버리지 일괄 보강(표시 전용)** — draft_ctx에 style_block·hook_instruction·continuations·static_directives·active_world_rules·prev 발췌·corrections 슬롯 추가, `assemble`/`_draft` 단일 추출점화 | D-1~D-9 | 高(진단 신뢰 회복 — 다른 모든 수정의 효과 추적 전제) | 中 |
| **4** | **RAG 직전 회차 배제 + 근거리 dedup** — rag.search 후보에서 ch_no-1 제외, prev_text와 자카드>0.9 청크 드롭, dropped 가시화 | N-1·N-2·N-3 | 中(검색 슬롯을 먼 회차 복선 회수로 회복) | 低 |
| **5** | **cast 선정·욕망/비밀 컨텍스트 보강** — cast_ids를 actor 엔티티로 확장·required_cast/beat.entities 우선 재정렬, profile 없는 잠정 엔티티는 wiki state로 채움, attrs[:4]→status-aware/캡 상향+`if v is not None` | I-2·I-3·I-4·C-3 | 中(설계가 실제 갈등 NPC·핵심 등급을 보게) | 低 |
| **6** | **명부·설정집 위생** — voice_roster를 확정/잠정 분층 라벨, orphan 인물카드(윤서희·최강우) roster 정합 가중·작가 리뷰 큐 배지 | C-1·C-2 | 中(인물 중심 회차의 stale 인명 발화 위험 차단) | 低 |
| **7** | **final_violations 회차 UI 가시화** — `app.js`를 `ev.violations>0` 기준으로 보강(비구속 위반 advisory 표시)+반복 위반 텔레메트리 | C-5 가시화부 | 中(검출만 되고 사장된 신호를 작가에게) | 低 |

---

## 의도된 비주입은 결함이 아니다 (정상 — 1줄)

**약속 원장(13건/open 9)·페이싱 윈도·독자평가(reader_feedback)·온톨로지 narrative_inferred 관계 49건·plot_thread 위키 2페이지·세계규칙 SEMANTIC 비차단·knowledge·secret의 narrative_inferred 비박기는 모두 시스템 확정 원칙(측정·가시화·제안=시스템 / 회수·교정 시점=작가, 비대칭: 기계추출=잠정·비구속)에 따른 의도된 비주입이며 결함이 아니다** — 이들을 ground_truth로 박거나 회수를 강제 주입하면 무강제·비대칭 원칙 위반. 단, 그 안에서 정당한 잔여 우려(가시성/넛지 갭)는 위 §6·§7의 가시화 권고로만 보완한다(주입 강제 아님). 또한 적대 검증에서 다수의 '디버그 240자 트림을 실제 주입 누락으로 오독한' 발견(wiki 카드 절단·bible 13자리 점유·죽은 NPC 카드 등)이 기각됐다 — **실제 프롬프트(`prompts.py:25` `{r.text}` 풀텍스트)는 트림되지 않으므로**, 디버그 화면의 트림은 §6의 가시화 보강 대상이지 컨텍스트 결함이 아니다.