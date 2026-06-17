Code confirms the key claims: `bible_compiler.py:42` `s = 100 if e.promoted else 0`, the `bible_truncated` emit at `copilot.py:681-682` carries only the integer `dropped` count, and the cast_debut anchor at line 656 gates on `getattr(e, "profile", "")` truthy. The verification holds. The provided judgments are sound and internally consistent. I'll synthesize the final report.

# 보류 8건 최종 분석 — 넣을 가치 판단

## 1. 한 줄 결론

**지금 당장 넣을 가치가 있는 건 0건이다.** 8건 중 어느 것도 8·9·10화 실제 출력에서 증상이 관측되지 않았고(observed 0건), 5건은 조건부로만 발현하는 잠재(latent)·이론(theoretical) 갭, 3건은 명백히 skip(YAGNI/원칙위반/이미 완화). **`optional` 4건(C-2·C-3·I-3·I-5)은 모두 "단독 구현 금지, 다른 위생 작업에 묶을 때만 가치"** 인 후속 번들감이지 독립 작업거리가 아니다. **`skip` 4건(C-4·C-6·N-2·M-3)** 은 갭이 없거나(C-4·N-2 gap_real=false), 원칙(no-whackamole)에 저촉되거나(C-6), 입력이 0건인 죽은 코드(M-3)다. 다 넣자고 할 이유가 없다.

## 2. 한눈에 보는 표

| 항목 | gap_real | 발현 | 가치 | 비용 | 판정 | 핵심 이유(한 줄) |
|------|----------|------|------|------|------|------------------|
| **C-2** | true | latent | low | low | optional | orphan bible 인물카드 누수 능력은 실존하나, voice_roster가 정답 인명을 매 화 주입해 부분 완화 — C-1과 묶을 때만 |
| **C-3** | true | theoretical | low | low | optional | wiki 서사상태가 cast에 안 들어가나 recent 채널이 이미 동일 정보 전달 — I-2 리팩터에 묶을 때만 |
| **C-4** | **false** | theoretical | low | med | **skip** | '라벨 발산'은 코드상 발생 구조가 없음(rival_of=등록키). 모든 출력 경로에서 디둡되어 비가시 |
| **C-6** | true | latent | low | low | **skip** | uncast NPC 데뷔앵커 미수령은 실재하나, 빈 앵커는 미스터리를 조기 고정명사화 → 원칙위반 |
| **N-2** | **false** | theoretical | low | low | **skip** | rag(산문)·wiki(내면상태)는 다른 코퍼스 — 중복이 아니라 상보. N-1이 진짜 중복은 이미 해소 |
| **I-3** | true | latent | low | low | optional | secret 내용이 profile 채널로만 도달하나 실측 프로필 ≤173자로 절단 미발동 — 캡 상향은 기회 시 |
| **I-5** | true | latent | low | low | optional | promote 미사용으로 +100 부스트 전량 사망 실재. 단 가시화 절반만 채택, 키워드 디프라이오리티 절반은 원칙위반 |
| **M-3** | true | theoretical | low | low | **skip** | 미래 eff_from 게이트 부재는 사실이나 입력 0건·상류 UI 부재 = 죽은 코드 |

## 3. 넣자(implement) — **해당 없음**

8건 중 **출력 증상이 실측된 항목이 0건**이다. 모든 항목이 8·9·10화 실제 산출물에서 결함으로 드러나지 않았고(C-3는 9화 출력이 오히려 상태를 정확히 반영, I-5는 산문에서 규칙 유실 미실증), 따라서 "출력 품질을 지금 떨어뜨리고 있으니 고쳐야 한다"는 근거를 가진 항목이 없다. 강제 구현 대상 없음.

## 4. 선택(optional) — opt-in/후속 번들로만 정당한 4건

이 4건의 공통점은 **"갭은 실재하고 fix는 원칙적·저비용이지만, 단독으로 넣을 만큼 가치가 크지 않다"** 는 것이다. 모두 *다른 위생 작업에 묶을 때 추가비용≈0* 이라는 조건부 정당성만 가진다.

- **C-2 (orphan 인물카드 누수) — C-1과 묶을 때만.**
  bible_digest가 ontology roster에 없는 인물카드(윤서희/최강우)를 본문에 흘릴 *구조적 능력*은 실존(`bible_compiler.py:42`의 score 경로 확인). 그러나 ① 해당 심볼이 context_hint에 들어갈 출처가 사실상 부재(거의 theoretical), ② voice_roster가 정답 인명(윤세라/강현식)을 매 화 카운터시그널로 주입해 부분 완화. 무강제 fix: roster에 없는 카드 후보 시 `orphan_character_card` emit(기존 `copilot.py:681` 패턴 재사용)으로 작가 리뷰 큐에 가시화. **런타임 강제 불요.** 비용: low(기존 인프라).

- **C-3 (wiki 서사상태가 cast에 미도달) — I-2 캐스트 리팩터와 묶을 때만.**
  `_cast_context`가 wiki.pages를 안 읽는 건 사실이나, recent 채널(직전화 detail_synopsis ~300자+)이 같은 서사상태를 이미 설계 프롬프트에 전달 중 → 9화 출력이 새 상태 정확 반영, 증상 미관측. 무강제 fix: `sess.bundle.wiki`가 이미 call-site 스코프에 있으므로 active 페이지 '상태:' 첫 문장을 `[서사상태(잠정)]` cap80 1줄(narrative_inferred, LLM 콜 0)로 주입. **단독/강제 금지.** 비용: low.

- **I-3 (secret 내용 구조화 결손) — opt-in 후속.**
  비밀이 "무엇인지"가 profile 채널로만 설계에 도달하고 `prof[:220]` 절단 위험이 있으나, 실측 프로필 전부 ≤173자라 절단 미발동(현재 무해). 무강제 fix: 캡을 ~400 상향(기회 시 저렴). 핵심 비밀 구조화는 worldgen이 `regression_secret` plot_thread body를 채우되 **lifecycle=ACTIVE로 승격해야** wiki.retrieve(`wiki.py:105`가 ACTIVE만 통과)가 주입 — draft면 무시됨. 새 ground_truth/검출기 없이 narrative tier로만. 비용: low.

- **I-5 (promote 미사용으로 부스트 전량 사망) — 가시화 절반만.**
  `bible_compiler.py:42` 확인: 작품 데이터 56항목 전부 promoted=0이라 +100 부스트 전량 사망, 정렬이 keyword exact-substring(brittle)에만 의존. 9화 전용 worldrule 카드가 near-miss로 드롭되는 brittleness 실재. **단, 채택은 절반만**: bible_digest 시그니처를 `(items, dropped_count, dropped_titles)`로 소폭 확장해 `bible_truncated` emit(`copilot.py:681-682`, 현재 정수만 전달 확인)에 컷된 카드 제목 추가 → 작가 promote 유도. **'Overview 디프라이오리티' 같은 키워드/검출기 절반은 no-whackamole 위반으로 채택 금지.** 비용: low.

## 5. 불필요(skip) — 직시 4건

- **C-4 (관계 라벨 발산/중복) — gap_real=false. 존재하지 않는 문제.**
  항목이 주장한 '자유라벨 rival ↔ 등록키 rival_of 발산'은 코드상 발생 구조가 없다. `rival_of`는 카탈로그 등록키이고 증거의 중복 두 엣지가 둘 다 `라이벌`로 렌더 = 둘 다 동일 rel_id. 차이는 라벨이 아니라 trust_tier뿐이며, 모든 출력 경로(`canon_relations`/cast/그래프)에서 ground_truth만 통과·디둡되어 비가시. 후보 fix(동의어 매핑 테이블)는 **no-whackamole 정면 위반**이며 실재하지 않는 발산을 겨냥. 비용 대비 효익 음수.

- **C-6 (uncast NPC 데뷔앵커 미수령) — 원칙위반.**
  uncast NPC가 데뷔앵커를 영구 미수령하는 건 사실(`copilot.py:656` profile truthy 게이트 확인)이나, 후보 fix(uncast도 '신규 인물'로 재소개)는 자연 데뷔한 미확정 떡밥을 고정명사로 **조기 확정 압박** → 비대칭 원칙·C-1과 충돌. introduced 별칭 강건화는 검출기 튜닝 = 두더지잡기 금지 위반. 진짜 콜드드롭은 이미 `uncast_character` 이벤트로 가시화됨. value≈0 + 원칙위반 = skip.

- **N-2 (rag·wiki 중복 회수) — gap_real=false. 중복이 아니라 상보.**
  rag.search는 회차 산문 청크(source=rag_chunk), wiki.retrieve는 인물 상태/감정/목표 카드(source=wiki_page) — **다른 코퍼스**다. 9화 증거가 정확히 이를 보임(산문 6 vs 내면상태 카드 3). 슬롯 낭비형 중복이 아니라 직교축 추가. 진짜 고비용 중복(직전회차 전문↔rag)은 N-1(`as_of=ch_no-2`)이 이미 해소. 증상 미관측 + 효익 미미 = skip.

- **M-3 (미래 eff_from 게이트 부재) — 죽은 코드.**
  state/relation 게이트가 과거~현재만 보는 건 사실이고 fix 자체는 원칙 부합·저비용이다. **그러나 미래 eff_from으로 상태를 박는 집필 워크플로(UI)가 아직 없어 입력이 0건** — 지금 넣으면 죽은 코드. backward 수렴 설계의도는 이미 episode.climax/finale 훅 + spine premise + plant_notes가 충족 중. 미래 워크플로가 생기고 데이터가 1건이라도 쌓이면 그때 opt-in.

## 6. 권고 (영향순)

1. **지금은 아무것도 단독 구현하지 마라.** observed 0건이 결정적이다 — 8건 전부 실제 출력을 망치고 있다는 증거가 없다. 강제 투입은 전부 YAGNI/gold-plating이다.

2. **C-1(명부·설정집 위생) 또는 I-2(캐스트 리팩터)를 *별도로* 착수하게 되면, 그때 비로소 묶음으로 처리하라.** C-2는 C-1에, C-3·I-3는 I-2/wiki 리팩터에 추가비용≈0으로 얹힌다. 단독 트리거로 삼지 말 것.

3. **유일하게 독립적으로 싼 가시화 1건만 후보**: I-5의 `bible_truncated` emit에 컷된 카드 제목 추가(시그니처 3-튜플 확장). promote 미사용 레짐의 brittleness를 작가가 보게 하는 측정·가시화이며 원칙 부합·비용≈0. **단 키워드 디프라이오리티 절반은 절대 넣지 말 것**(no-whackamole 위반). 이것도 "지금 꼭"은 아니고, 다음 bible 관련 작업 시 곁들이면 충분하다.

**요약**: implement 0, optional 4(전부 묶음 조건부), skip 4. 넣어야 하는 건 없고, 위생 작업이 독립적으로 발생할 때 곁다리로 처리할 후보 4건과, 폐기해도 되는 4건이 있을 뿐이다.

관련 코드 위치(load-bearing): `D:\study\ai-web-novel\app\novelcopilot\engine\bible_compiler.py:42` (promote 부스트), `D:\study\ai-web-novel\app\novelcopilot\services\copilot.py:656` (cast_debut profile 게이트), `D:\study\ai-web-novel\app\novelcopilot\services\copilot.py:681-682` (bible_truncated emit — 현재 정수만 전달).