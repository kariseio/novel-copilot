# 프롬프트 안티패턴 감사 (2026-06) — 다중에이전트 18

**총 39건 / 11파일.** 패턴별: 트로프강제 14 · 부정priming(풍선효과) 9 · 편향예시 9 · 과잉제약 5 · 메타누출 2.

## TL;DR — 계통(systemic) 패턴
개별 버그가 아니라 **한 실수의 세 형태**가 파이프라인 전 단계(세계관→아크→비트→회차→추출)에 반복:

| 계통 패턴 | 파일 | 증상 |
|---|---|---|
| **A. 파워판타지/전투 트로프 priming** | 7 (concept_chat·arc_planner·generator·copilot·harness·bible·ledger) | 회귀·빙의·먼치킨·후회·복수·위협·무기·등급을 장르 무관 하드코딩 → 전 작품 갈등형 균질화 |
| **B. 부정지시 풍선효과(pink-elephant)** | 6 (generator·arc_planner·copilot·wiki·concept_chat·harness) | "레벨/사망/게이트/시스템창/후회·복수 넣지 마라"가 그 토큰을 환기 → prime |
| **C. 편향 few-shot** | 7 (concept_chat·generator·copilot·extractor·wiki·worldgen_chat·ontology) | 예시가 단일 모티프(죽은 스승·검투·짝사랑·복수로맨스)로만 → 그 패턴에 앵커링 |

**한 뿌리:** "장르를 시드/전제에서 도출하지 않고 코드에 박힌 웹소설 클리셰로 미리 결정." 진앙=`harness.py`+`arc_planner.py`(회차본문·아크/비트), 전파원=`concept_chat.py`(키워드 오염→하류 전체).

## HIGH (6) — 즉시
- **H1+H6 concept_chat keywords 필드**: 예시(아카데미·회귀·후회·먼치킨·빙의) 전부 파워판타지 → 키워드와 하류 전체 앵커링. → "작품 언어에서 도출, 미리 정한 장르목록에 끼워맞추지 말 것"
- **H2 arc_planner build_spine brief_block**: '후회·복수' 하드코딩+'후반배치' 규칙 → 아크 굴절(negative_priming M5 중복). → 트로프명 비호명, 회수 시점은 작품 페이싱에서
- **H3 arc_planner beat_for_episode**: '무기·기술 체계' 예시 → 비전투 작품도 무기로 앵커링. → "세계규칙·전제가 정한 고유 체계(사회·제도·관계·자원 등)"
- **H4 generator attributes**: "레벨/사망 넣지 말 것" = pink-elephant. → "전제·갈등에서 변화 추적할 축만 도출"
- **H5 generator obsession_block**: '게이트·E~S·시스템창'(헌터 간판어) 부정 호명. → "기성 간판어 기대지 말고 이 작품 집착의 고유 어휘로"

## MEDIUM (12) — 핵심
- **M-그룹1 harness 회차본문 (4건·전부 기본 ON, 빈도최다):** `_CRAFT_PROGRESS`('위협·대치·적·판세'+심화금지가 관계·치유물 억압) / `ending_hook` 폴백=cliffhanger(전작 절단신공 균질화) / `recent_tails`('접근 신호' 호명+스릴러 대체메뉴) → 진전·훅을 작품 톤에서 도출
- **M-그룹2 generator:** attributes 장르매핑 주석(로맨스=관계/무협=경지/현판=각성) / pleasure_engine 예시 편향 / obsession '불편·비대칭' 어두운톤 강제(잔잔물에 음울 부과)
- **M-그룹3 arc_planner·copilot:** '초반 남발 금지'(후회·복수 부정호명) / `_brief_to_seed` 후회·복수·가해자·연적 예시 고정 / '즉시 이어받아 회수' 클리프행어 라벨
- **M-그룹4:** ledger '위협·떡밥' 고정enum / bible '갈등소재' 전항목 강제 / wiki 말투 negative priming / extractor '죽은스승/환영/꿈' 무협 단일 few-shot

## LOW (20) — 일괄 청소
harness `_style_inject`(효과음·기합)·`_SCENE_EXAMPLES`(검투)·`_STYLE_BLANKET`(과잉제약·이미 대조군)·out_instr 메타누출 / generator terminal 'none' / arc key_events·event_menu '직전 위기' / copilot 데뷔앵커·plant_notes(부정호명) / concept target_chapters(100~300 앵커) / genre_contract '결제 쾌감' / bible 5슬롯·'웹소설 설정집 작성자' / ontology 동맹·적대/짝사랑 예시 / wiki 대사카드·괄호 메타누출 / beat_planner 추진력 예시(서스펜스) / worldgen_chat 관계 state(소원·상실)

## 가장 먼저 3가지
1. **concept_chat keywords 한 문장**(H1+H6) — 진원지, 하류 전체 차단
2. **arc_planner 후회·복수 트로프 제거**(H2+M5, copilot 동일패턴) — 아크 골격 굴절
3. **harness 회차본문 craft 전투어휘 제거**(M-그룹1, 4건 기본ON) — 빈도·영향 최대, 작가 체감 산출물(프로즈) 직결

**공통 처방(한 줄):** 장르·톤·진전축을 코드에 박지 말고 **작품의 시드·전제·직전 상황에서 도출**. 부정지시→긍정 작업지시. 편향 few-shot→장르중립 placeholder 또는 실제 직전 회차에서 추출.

**검증 근거:** arc_planner를 이 원칙(완전 중립)으로 고쳤을 때 claude는 학원물 spine을 계절·축제·첫사랑으로 충실 생성(배틀단어 0). 단 gpt-5.5는 중립 프롬프트로도 회귀/흑막 발명 → 장르맹목은 모델prior, 설계는 claude 필수. [[model-routing-prose-lever-2026-06]]
