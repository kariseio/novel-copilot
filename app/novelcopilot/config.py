# -*- coding: utf-8 -*-
"""런타임 설정 — 전부 환경변수 주입(API 키·모델·경로 하드코딩 금지).

.env 를 프로세스 환경으로도 로드(override=True — .env 가 단일 출처):
pydantic-settings 는 NOVEL_* 필드만 읽으므로, OPENAI_API_KEY 처럼 SDK 가 os.environ 에서
직접 읽는 키는 load_dotenv 없이는 .env 에 넣어도 무시되던 잠재 결함을 교정.
"""
from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOVEL_", env_file=".env", extra="ignore")

    llm_provider: str = "anthropic"   # 기본=Anthropic(집필/핫패스). 임베딩은 OpenAI 위임(create_role_provider/_openai_embed)
    prompt_log: bool = True           # PL-1: 모든 LLM 콜 전문을 logs/prompts/<태그>/ 에 영속(관측 전용·바이트 불변). env NOVEL_PROMPT_LOG
    gen_model: str = "claude-opus-4-6"   # 집필(프로즈)/핫패스. **사용자 확정(2026-08-29) — opus-5→opus-4-6 고정**(구 07-27 opus-5 결정 대체 · 아래 opus-5 실측 주석은 이력):
    #   하네스 컨텍스트 프로브(괴담작 5화 사본 재생성) 실측 — ⓐ맨몸 프롬프트에서 3/3 하드 거부되던 2막 괴담 소재를
    #   하네스 프레이밍에선 정상 집필(결격 해소) ⓑ교차벤더 정독 게이트 1차 통과(fable 판은 1차 FAIL→재생성)
    #   ⓒ블라인드 패널 잔존 64% vs fable 62%, fable 판 3대 확정 결함(손끝 3연타·자기부정 과다·기계 리듬) 전부 소멸
    #   ⓓ캐논·말버릇 봉인·표면 서사 준수, verbatim 겹침 fable 판보다 낮음 ⓔ문말 상투 0.774(맨몸 0.948 → 문체 스택이
    #   opus-5 에도 작동) ⓕ단가 fable 의 절반($5/$25). **잔여 리스크(수리 대기)**: ①거부 시 프로바이더가 빈 문자열로
    #   무음 통과(stop_reason 미확인 — FB-1 서버측 fallbacks 배선과 함께 수리 필요) ②분량 규범 +68% 초과(장문 경향).
    #   thinking 기본 ON(4.8/fable 과 달리 생략=adaptive)·max_tokens 는 사고+본문 합산 상한이라 chapter_max_tokens
    #   여유 필요·temperature/top_p/budget_tokens 는 400(provider 자동 적응). 이전값=claude-fable-5(2026-07-15~).
    # 역할 라우팅(B-22b) — "provider:model" cross-vendor, 빈값→gen_model, 키없으면 안전폴백(create_role_provider).
    worldgen_model: str = "anthropic:claude-opus-4-8"   # 세계관·bible: 최신 4.8(사용자 결정 — 세계관은 4.8)
    planning_model: str = "anthropic:claude-opus-4-8"   # 아크·에피·비트 설계: 4.8(세계관 설계 측이라 동일). gpt-5.5는 장르 mode-collapse라 claude 필수
    story_pass_model: str = "anthropic:claude-opus-4-8"  # 스토리 패스 깔때기 gen(후보·병합·서식수리): 4.8(사용자 결정 2026-08-23 —
    #   스토리 설계 측이라 planning 동일 계열). 심사는 style_judge_model 그대로(gen≠judge 유지). ""=스왑 0(sess.provider 재사용·하위호환)
    story_pass_auto: bool = True             # SP-auto(2026-08-29 사용자 결정 "스토리 패스 항상 있고·무강제 폐기·ST-14 이행"):
    #   생성 시 확정 스토리 부재면 깔때기 자동 실행+승자 variant 자동 확정. False=구 옵트인(수동 run+confirm). 회차당 깔때기 비용 상시.
    # CE-1 ⓑ: 보조 스테이지 다운그레이드 — 기계적 요약/추출 스테이지(재요약·wiki·ontology_propose·ledger_reconcile)를
    #   저가 모델로. create_role_provider('provider:model') 라우팅. 빈값("")이면 스왑 0 = 기존 gen provider 경로 바이트 동일.
    #   제외(보수 유지): draft/polish/continue(프로즈)·check(캐넌 가드 G-A)·reader_desk·cold_read(SX-2 cross-vendor)·
    #   게이트 심사·humanize·rerender — 이들은 aux 로 안 내린다.
    aux_model: str = "anthropic:claude-sonnet-5"        # env NOVEL_AUX_MODEL. $3/$15(프로모 $2/$10). ""=스왑 0(하위호환)
    # 캐논 추출(ClaimExtractor) 전용 라우팅 — 사용자 결정 2026-08-21: 추출이 회차 최대 비용축(16화 실측 18콜·전체의
    #   43%)이라 sonnet-5 로 전환. 재현율 게이트는 사용자 판단으로 생략 — 이후 회차의 가드 판정·정독 검증으로 관찰.
    #   프롬프트 바이트 불변(모델만 교체). ""=스왑 0(gen provider — 종전 경로·계측 동일).
    extract_model: str = "anthropic:claude-sonnet-5"    # env NOVEL_EXTRACT_MODEL
    embed_model: str = "text-embedding-3-small"

    # 컨텍스트 예산 — '기아 해소' 재배분(docs/context-redesign.md): 입력 ~40k토큰 목표, 헤드룸 60% 유지.
    gen_max_tokens: int = 3000
    chapter_max_tokens: int = 12000         # 단일 패스 회차 집필 출력 예산. **fable-5 전환(2026-07-15)으로 6,500→12,000**:
    #   fable 은 thinking 상시라 출력 예산에 추론 토큰이 포함돼(text+thinking) 6,500 이면 프로즈가 절단된다. MD-1 신작
    #   7화가 12,000 으로 검증(절단 0·분량 4~7천자 정상). 실제 회차 길이는 이 상한이 아니라 비트 소진이 정하고(이어쓰기),
    #   상한은 '비트 중간 절단 방지' 헤드룸일 뿐(LN-1 분량 제어는 코드 판정 norm 이 담당·이 값은 하드캡). max_output_cap(16,000) 이내.
    max_output_cap: int = 16000             # provider 출력 토큰 하드캡(모델 실한도 근사). 구형 8192 가 chapter 예산을
    #                                         silently 8192 로 클램프해 회차가 비트 아니라 토큰예산에서 잘리던 결함 교정 — chapter_max_tokens 보다 커야 함
    max_rewrite_rounds: int = 3
    # 생성→검증→실패분류→라우팅(근본 아키텍처): 전역 파손(절단·빈응답·미완결)은 보존할 base 가 없으니 '재생성',
    # 국소 위반은 '국소 교정'(diff 한도 게이트로 본문파괴 차단). 둘 다 두더지잡기(검출기/사전) 0 — 일반 구조 신호만.
    max_regen_attempts: int = 1             # 초안이 '빈 응답'이면 재생성 횟수(0=끔). 절단은 trim 으로 흡수(재생성 아님)
    correction_max_drift: float = 0.5       # _rewrite 가 base 대비 이 비율 초과로 바뀌면 거부→base 유지(본문파괴 차단).
    #   거친 안전망(실제 교정 diff 분포로 추후 보정 — 적대검증 F9). 너무 다름=거부→ESCALATED 노출이지 silent 출고 아님
    prev_chapter_context_chars: int = 8000   # 직전 회차 '전문' 수준(실전 1순위 관행 — 기존 4,000자는 56%만 전달)
    story_so_far_chars: int = 12000          # 누적 줄거리(현재 에피소드=상세 시놉시스 1,500자/화 + 과거=한줄·롤업)
    bible_digest_chars: int = 3500           # 설정집 카드(키워드 선별 — 심층 설정집 41+항목 대응)
    rag_k: int = 6                           # 과거 회차 의미 검색 청크 수(기존 3 하드코딩)
    wiki_k: int = 3                          # 인물카드 주입 수(기존 2 하드코딩)
    # 메모리 상한(장수 프로세스 OOM 방지 — 축출/만료해도 정보 손실 0: 세션=디스크 재수화, 드래프트=휘발 명세)
    max_live_sessions: int = 32              # 동시 캐시 세션 수(LRU)
    draft_ttl_sec: int = 6 * 3600            # 미접촉 컨셉 드래프트 폐기 기준(초)
    max_drafts: int = 200                    # 드래프트 하드캡(TTL 내 폭주 방어)
    gen_job_retain_sec: int = 21600          # 끝난 회차생성 잡 보존(초, 6h) — 회차 1편 10분+ 자리비움 고려: 자리 떠도 결과 패널 재접속 복원(본문은 디스크 영속이라 더 길어도 무손실)
    continuity_polish: bool = True           # 회차 내부(수치·소지품) 연속성 교정 패스(+1콜/화)
    plant_backlog_threshold: int = 3         # 미회수 복선 적체 경보 임계(advisory)
    plant_inject_cap: int = 5                # 비트 설계에 참고로 노출할 미회수 복선 최대 수(plant_reminder opt-in 시)
    ledger_recon_window: int = 20            # B-30: 지불 검출창 크기(LLM 에 넘기는 open 약속 수) — 기본=현행 토큰 비용 동일
    ledger_recon_priority: int = 12          # B-30: 창 중 만기·고령 우선 고정 슬롯 수. 나머지(최소 1)=LRU 순환 슬롯
    #                                          → 모든 open 약속이 ceil(잔고/순환슬롯) 회차 내 반드시 검사(기아 0)
    reader_desk: bool = True                 # G2: 블라인드 독자 행동 예측(advisory, +1콜/화). 비용 절감 시 off
    # SX-2: 콜드리드 독자 축(+1콜/화 ~15K). reader_desk 는 '줄거리 요약'을 받아 실제 독자와 다르다(측정 사각 — 4화 정독 콜드리드
    #   혼란 4~5건 상존). 이 축은 신규 독자가 *프로즈만*(1화~현재 화 본문 연결·요약/설정/계획 일절 미주입)을 읽고 이해도·혼란·
    #   궁금 목록을 낸다(cross-vendor 심사·make_judge 계보 gen≠judge). advisory·비차단·무강제.
    cold_read: bool = True                    # env NOVEL_COLD_READ. off=콜드리드 프로브 미실행(verification.cold_read="미실행"·바이트 동일)
    cold_read_max_chars: int | None = None    # 절단 전면 제거(2026-08-21): 기본 무절단(전 회차 프로즈 전문 — 구 40,000 머리 유지는 16화 시점 누적의 절반을 버림). 값 설정 시에만 상한(opt-in·truncated 정직 기록 유지)
    reader_desk_sofar_chars: int = 12000     # DP-15: 시뮬 독자에게 넘기는 '지금까지 줄거리' 절단 예산. 기존 1,500 하드컷은
    #   호출자가 story_so_far_chars(12,000) 계층 요약을 넘겨도 87%를 버려 실재하지 않는 모순을 지적(retention 오염) → 계층 요약 전체 수용.
    #   비용: 입력 +~10.5k자(≈ +3~4k 토큰/화, advisory 1콜/화에만) 소폭 증가. 절단 시엔 오래된 앞쪽을 버리고 최신 꼬리를 보존(reader_desk.py)
    claim_audit: bool = True                 # CN-2: 자유형 사실 모순 RAG-grounded advisory(+0~1콜/화, ch>1·검색결과 있을 때만). 비차단·non-hard. 비용 절감 시 off
    dialogue_ledger: bool = True             # DG-1: 확정 후 대사 화자 귀속 원장(+1 aux콜/화·관측 전용·생성 주입 금지). off=원장 미축적
    bounded_dangling: bool = False           # CN-6: 비트가 지목한 인물이 현 맥락에 없으면 집필 전 타깃 검색으로 과거 맥락 보강(bounded=최대2·open loop 아님·+0~2 embed콜). 기본 off(선택)
    event_menu: bool = True                  # T3: 에피소드 활성 시 '적시 사건 메뉴' 생성(+1콜/에피소드 ≈ +0.1~0.3콜/화). off=결정론 폴백
    event_menu_refresh_every: int = 0        # T4: >0 이면 에피소드 중반 N회차마다 메뉴 재생성(긴 EP stale 해소, +비용·비결정성). 0=off(T3 1회 캐시)
    rehash_filter: bool = True               # B-37: 사건 메뉴에서 최근 N화 실현 사건과 재탕(술어까지 겹침)하는 후보를 코드로 제거 + 훅 유형 화이트리스트 순환(콜0·결정론·무강제). off=바이트 동일
    rehash_lookback: int = 3                 # B-37: 재탕/훅순환 판정에 쓰는 '최근 회차' 수(design 기본 3). 보존 목록·과차단 가드·폴백은 코드 불변
    chapter_budget: bool = True              # RC-1: 회차 플롯 예산 분배 층. ON=에피소드 활성 시 사건 풀을 회차 슬롯에 배분(+1콜/에피소드·회차당 0) →
    #   **기본 ON 승격(2026-08-30 A/B 통과)**: ch20 OFF(통짜·whole-case·잔존 55%·하차1/4) vs ON(setup+첫조사·잔존 62%·하차0/4)·role 회수→조사·정독 ON승. OFF=통짜 메뉴 경로 바이트 동일(플래그로 되돌림 가능).
    #   스토리패스 재료를 슬롯 몫으로 스코핑·역할을 슬롯 기능 태그에서 파생·줄 수를 슬롯 예산으로 스케일. OFF=통짜 메뉴 경로 바이트 동일(하위호환).
    settlement_gap_k: int = 3                # DP-3' ⓑ: 에피소드 내 연속 무정산(chapter_function=payoff 아님) 회차 상한. 도달 시 재계획 1회(DP-13 경로 재사용·payoff 배치 긍정 신호). 0/1=비활성(바이트 동일)
    # 풍부함①③: worldgen 메타데이터는 풍부해지나(집착 편중·안티클리셰), **중립 프로즈 A/B 는 3:3 무승부** —
    #   풍부한 세계가 회차 본문엔 도달 안 함(초기 5:0/9:0 은 생성 어휘를 심사 루브릭이 보상한 순환 측정, 적대리뷰 적발).
    #   → default OFF(프로즈 미검증). 코드·토글은 보존(작가용 세계 풍부화 보조·장기 아크 주제 일관 가능성, 미검).
    world_obsession: bool = False            # 집착 벡터 추출→편중 파생(+1콜/생성). 프로즈 효과 미검 → 기본 off
    world_weird: bool = False                # 안티-클리셰 weirding(+1콜/생성). 프로즈 효과 미검 → 기본 off
    craft_progress: bool = True              # 회차 집필에 '전개 압력' craft 지시(SL 1순위 패딩 결함 직타; C-3서 부정명령→긍정 구조지시 전환). A/B 가독성 4:2 우세(약신호·콜0)
    gen_tools: bool = False                  # AG-1 T4(M1): 집필 전 선조회 패스 — 모델이 lookup_canon 으로 필요한 사실을 스스로 조회(에이전틱 pull 1단). +1~5콜/화. off=바이트 동일. M2(집필 중 인터리브)는 A/B 후
    scene_style_anchor: bool = False         # 비트 기능(chapter_function)별 장면형 스타일 앵커 *선택적* 주입(블랭킷 지시 과적용 회피). A/B scene>blanket 4:2·ai_tell 변주↑. scene>none 미검 → off
    paragraph_reflow: bool = True            # ST-3: 발행 경계 문단 조판(3문장 초과 지문 문단 분할·대사 라인 단독 문단화). 내용 무손실 순수 조판(콜0·판정기 아님). off=바이트 동일
    narrator_voice: bool = True              # ST-12a: 1인칭 작품에서 worldgen 완료 시 주인공 시트→서술자 음성 카드 파생(+1콜/작품), entity.voice 영속→harness 서술자 프레임 주입. off=파생 스킵(구작·A/B off 팔과 동형·프롬프트 바이트 동일). 3인칭은 무관(무동작)
    # SP-1: 생성 경로 내장 문체 파이프라인(few-shot 분포 조향 + 국소 스팬 수리). 사용자 명시 지시로 기본 ON, 작품/전역 opt-out.
    style_fewshot: bool = False  # 사용자 판정(2026-07-13): few-shot=오염 — 전면 금지, 기본 OFF               # SP-1 Stage A: draft/continue/regen 문체 블록 뒤 순방향 few-shot 예시(sp1_exemplars) 주입(콜0·프롬프트 ~1K tok). off=프롬프트 바이트 동일
    style_repair: bool = True                # SP-1 Stage B: finalize 직전(reflow 후) rhythm_spans(Kiwi) 검출→v2 국소 재작성(사실 불변·폴백 원문·스팬 격리). off=바이트 동일(검출·수리 호출 0)
    style_repair_max_spans: int = 6          # SP-1 Stage B: 회차당 수리 스팬 상한(긴 run 우선 선별). 콜당 ~3~5K tok → 회차 최대 ~30K. 0=수리 없음(검출만·바이트 동일)
    # ST-12b: draft 러닝 카운터 폐루프(CAPEL 패턴 — arXiv:2508.13805). 이어쓰기(_continue) 청크 직전에 누적 본문의
    #   Kiwi 종결 분포(ending_profile)를 계측 → 인간 대역 밖(top_ratio>0.60 or max_run>14)이면 수치 상태블록(잔여
    #   쿼터 k문장)을 다음 청크 프롬프트에 주입해 리듬을 되돌린다. 무강제(재작성·판정 0·이어쓰기 지시 강화일 뿐)·
    #   pink-elephant(수치+긍정 메뉴만·최빈 종결형 실물 미노출)·상태 적응형(대역 안이면 무주입). 초안 콜엔 무주입(상수
    #   블랭킷 금지·적대 §3-3). 기본 OFF — A/B(같은 시드 현행 vs 폐루프) 검증 후 승격. OFF=generate() 경로 프롬프트 바이트 동일.
    style_running_counter: bool = False      # env NOVEL_STYLE_RUNNING_COUNTER. off=상태블록 계측·주입 0(바이트 동일)
    # ST-12c: 비앵커 재실현 패스(작가 opt-in 편집 패스 — 자동 임계 트리거 0). 대상 회차를 원문 프로즈 없이
    #   사실 뼈대(detail_synopsis+대사 목록+수치·고유명사 목록)에서 웹소설 레지스터로 다시 실현하고(음성 카드+레지스터+
    #   기계 규칙), BoN-N 병렬 후보를 Kiwi 대역 최근접으로 결정론 리랭크한다(§9 이론 판정: 분포 레버는 비앵커 재실현 단독).
    #   실현 모델 = humanize_model(anthropic:claude-opus-4-8) 라우팅 재사용. 채택은 기존 퇴고(revision) revise/accept/undo
    #   흐름 재사용(원문 항상 보존·undo 가능). API(POST …/rerender)로만 발동 — 이 값은 후보 수만 정한다(경로 자동화 0).
    rerender_bon_n: int = 3                   # ST-12c: 재실현 BoN 병렬 후보 수(같은 프롬프트 N회·temperature 0.85). 프로브 검증치 3
    rerender_read_gate: bool = True           # ST-12c 정독 게이트(파일럿 2차 보정 2026-07-14): BoN 승자가 정독 쌍대(cross-vendor
    #   양순서 2콜)에서 원문에 완패하면 채택 금지 — no-harm 편집 계약(ch1 실측: kiwi 대역 진입 + 정독 0-2 = '대역≠품질').
    #   비용 +~20K/재실현. env NOVEL_RERENDER_READ_GATE. False=게이트 생략(가드 스택만 — 오프라인 실험용)
    rerender_metric_no_harm: bool = True      # ST-14 라이브 보정(2026-07-15): 재실현/수리 최종본이 *원문보다* 계측 축
    #   (top_ratio 인간 대역 거리·무중단 동일 종결 키 run 최대)에서 후퇴하면 결정론 기각(rerender.metric_no_harm).
    #   실측 구멍: 전 후보 실격 직전의 '유일한 유효 후보'가 원문(top 0.606)보다 나쁜 0.723 인데 채택됨 — 리랭크는
    #   후보 *간* 비교뿐, 원문 대비 무해는 아무도 안 봤다(정독 게이트는 완패만 거름). LLM 0콜·동률 허용·결측 비교
    #   생략(결측 정직). env NOVEL_RERENDER_METRIC_NO_HARM. False=가드 생략(오프라인 실험용)
    # ST-12d: 재실현을 opt-in API(POST …/rerender)로만 두지 않고 회차 생성 파이프라인에 상시 배선("문체 개선은
    #   생성할 때 태워라" — 사용자 지시 2026-07-14). generate_next_chapter 가 FINALIZED 회차 발행·락 해제 직후,
    #   product_gate 훅 앞에서 rerender_chapter 를 자동 실행한다(게이트는 재실현 반영 최종본을 심사). 무강제 정합:
    #   ①원문 보호 게이트(정독 no-harm)·사실 가드(G-A/G-B)가 채택을 결정 ②전 과정 revision 기록·undo 가능
    #   ③이 값으로 끌 수 있음. 발행 무영향(예외/미채택은 이미 발행된 회차를 바꾸지 않음). 방금 발행본이 이미 인간
    #   대역 안(top_ratio≤0.60·max_run≤14)이면 결정론 스킵(재실현 생략 — 무비용).
    #   RR-1(2026-07-16 기본값 ON→OFF): 재실현은 opus-4-6 어트랙터('-었다' 0.79~0.91) 시대의 분포 교정 장치다.
    #   Fable 전환 후 실측 — 최고작 MD-1·효빈 1화 모두 재실현 OFF 로 생성·호평, 유일한 ON 실전(괴담작 1화)에선
    #   수치는 대역 안으로 넣었으나 캐논 파괴(데뷔 전 인물 유출)+디테일 손실로 PM 정독에서 '재실현 전 본문' 채택.
    #   지표 발동 자동 재작성은 Goodhart 위험 → 파이프라인 상시 태우기를 끄고, 재실현은 수동 도구(rerender_chapter
    #   API·CLI 는 불변 — 작가/PM 판단으로 발동)로 유지한다. env NOVEL_RERENDER_IN_PIPELINE 로 재활성 가능.
    #   비용: (ON 시) 회차당 +60~80K 토큰·+3~7분(BoN-3·가드·정독 게이트·재요약).
    rerender_in_pipeline: bool = False

    # HM-1 휴머나이즈 패스(설계 docs/design-hm1-humanize-pass.md). HM-1a 결정론 티 탐지(N-3 모티프·N-4 문말·
    #   N-5 층위·N-6 수식) → HM-1b Claude 윤문(카테고리 처방·사실 불변 체시스·변경률 가드·폴백 원문). SP-1
    #   Stage B(리듬 수리)를 흡수하므로 humanize ON 이면 Stage B 는 이 경로가 대신 태운다(이중 패스 금지).
    humanize: bool = True                    # HM-1: finalize 후 결정론 탐지→윤문 국소 수술. 사용자 노선("생성할 때 태워야지")으로 기본 ON·작품/전역 opt-out. off=Stage B(style_repair) 경로로 폴백(하위호환)
    # HZ-1 ③ 같은 펜 원칙(사용자 결 차이 지적·MD-1 실측): 집도 모델 기본값을 anthropic:claude-opus-4-8 → "" 로 변경.
    #   ""=스왑 없이 generator 기본 provider(gen_model)로 윤문 — 본문을 쓴 펜이 다듬으므로 회차 간 결 혼합이 소멸한다
    #   (MD-1 실측: 채택된 opus-4-8 윤문이 본문 fable-5 와 섞여 1화 17% vs 3화 2%로 회차 간 결 차이·사용자 체감 지적).
    #   명시값('provider:model')을 넣으면 종전대로 스왑(하위호환). ⚠️ ST-12c 재실현 BoN 이 이 값을 재사용하므로
    #   ""=재실현 실현 모델도 gen_model 로 흐른다(의도된 부수효과 — 같은 펜: Fable 작품은 Fable 이 재실현). "" 는
    #   create_role_provider 가 기본 provider 로 안전 폴백(빈값 계약).
    humanize_model: str = ""                  # HM-1b/HZ-1③: 윤문가 모델. create_role_provider('provider:model'). 빈값=스왑 없이 gen_model(같은 펜)
    humanize_max_spans: int = 6              # HM-1b: 회차당 윤문 스팬 상한(S1 우선→긴 run 선별). 콜당 ~3~5K tok. 0=윤문 없음(탐지만·본문 바이트 동일)
    humanize_all_spans: bool = True          # HM-uncap(2026-08-29 사용자 결정 "max_spans 왜 있어·다 해야지"): 윤문 스팬 캡 해제 —
    #   탐지 결함 전건 수술(select_humanize_spans max_spans=None). False=humanize_max_spans 상한 유지. 사실 가드(G-A/G-B)·변경률 가드는 유지.
    # HZ-1 ① 스타일 지각 판정 스테이지(사용자 결정 2026-07-15 "휴머나이즈도 판단해서 적용" + "임계값은 두더지잡기"로
    #   결정론 임계 발동 폐기). 매화 무조건(+1콜/화) 판정자가 회차 프로즈를 낭독 체감으로 읽고 N-4(문말 run) 수리 필요
    #   여부·문제 구간 인용을 낸다(measure-then-cite). 결정론 계측(무중단 동일 어미 run)은 [참고 자료]로만 주입 —
    #   판정 기준은 낭독 체감이지 임계가 아님을 프롬프트에 명시. 판정 실패(파싱/콜)→수리 스킵(보수·결측 정직). gen≠judge.
    #   판정자 능력=지각 판정 품질 그 자체이므로 OpenAI 현 플래그십으로 상향(GPT-5.6 Sol — ST-16 "gpt-5.6"
    #   별칭 API 정상 동작 실측). 기존 게이트 심사(make_judge 계보 gpt-5.2-chat-latest)는 캘리브레이션 연속성
    #   때문에 별도 유지 — 이 판정자만 상위. 신설 스테이지라 기준선 부담 0. OpenAIProvider 가 파라미터 자동 적응.
    style_judge_model: str = "openai:gpt-5.6"   # env NOVEL_STYLE_JUDGE_MODEL. 판정자(교차 벤더 기본·향후 fable 지정 가능). create_role_provider 재사용. ""=스왑 없이 gen provider 재사용(aux/humanize ""=스왑0 관례 동형)
    humanize_n4_max_spans: int = 3            # HZ-1 ②: N-4 문말 수리 회차당 시도 상한(판정 인용 순서 최대 3). 최악 우선·비용 가드
    # HZ-2 지각 판정을 모티프(N-3)까지 확장(사용자 승인 2026-07-15 — 6화 성적표: 휴머나이즈가 N-3 모티프 6건을 매화
    #   재작성 시도·대부분 over_change 폴백=헛수술). ON: 같은 판정 콜에서 모티프 반복도 낭독 체감 판정→확인된 것만
    #   수술(N-3 findings 를 판정 motif_spans 인용 위치로 게이트·미확인=드롭+skip 기록). OFF: 구 동작(N-3 무조건
    #   통과)·바이트 동일(하위호환·롤백 경로). N-5/N-6 은 어느 쪽이든 자체 검출 유지(저비용).
    humanize_motif_judgment: bool = True      # env NOVEL_HUMANIZE_MOTIF_JUDGMENT. HZ-2: N-3 모티프도 지각 판정 게이트. False=N-3 무조건 통과(구동작·바이트 동일)
    # HM-3: N-1(자기해설)·N-2(감정 명명) LLM 탐지(사용자 지시 2026-08-20). 결정론 탐지(humanize_detect, LLM 0)가
    #   원리적으로 못 내는 의미 축(분류학 SSOT §4 "설계 ⓑ②")을 교차 벤더 판정 콜(+1콜/화·style_judge_model 라우팅
    #   재사용)로 보강한다. 산출 N-1/N-2 findings 는 humanize_pass 가 이미 등록한 N-1/N-2 처방으로 국소 윤문(신규
    #   리라이터·게이트 0 — 검증된 체시스 경로 재사용). 기본 OFF: 미설정 시 판정 콜 0 = 프롬프트/토큰/본문 바이트
    #   동일(하위호환). ON 은 humanize ON 을 전제(탐지만 켜도 humanize OFF 면 윤문 경로 없음). 과탐 리스크는
    #   narration_detect 보수 프롬프트(지문-only·한두 번 정상·애매하면 제외) + humanize_max_spans 상한 + S1 우선 선별 이중 가드.
    humanize_llm_detect: bool = False        # env NOVEL_HUMANIZE_LLM_DETECT. HM-3: N-1/N-2 LLM 전수 탐지 실행+가시화(+1콜/화). OFF=탐지 콜 0·바이트 동일
    # HM-3b(2026-08-20 사용자 (b) 결정 "전수 탐지+가시화"): 탐지 findings 를 humanize 로 병합해 자동 재작성할지.
    #   기본 False = 가시화 전용(findings 를 advisory 로 humanize 내역에 기록만·원문 불변 — 보이스 말살·반전 복선
    #   파손 위험 0·무강제 측정→가시화→작가). True = findings 를 detect_chapter findings 에 병합해 humanize_spans 가
    #   등록 처방으로 국소 재작성(공격적·전수 위양성이 자동 삭제로 이어지는 위험 감수). humanize_llm_detect OFF 이면 무의미.
    humanize_llm_detect_rewrite: bool = False  # env NOVEL_HUMANIZE_LLM_DETECT_REWRITE. HM-3b: 탐지 스팬 자동 재작성. 기본 OFF=가시화 전용
    # HM-3 가시화 상한(감사 블로커 수정): 탐지가 낼 수 있는 스팬 총량. humanize_max_spans(수술 예산 6)와 *분리* —
    #   전수 가시화가 목적이라 6 캡이면 catch-all 취지가 무너진다. surface = 이 상한(전수/높게), rewrite 시 실제
    #   재작성 개수는 humanize_spans.select_humanize_spans 가 humanize_max_spans 로 별도 캡한다(수술 예산 유지).
    humanize_llm_detect_max_spans: int = 40  # env NOVEL_HUMANIZE_LLM_DETECT_MAX_SPANS. HM-3: 가시화 탐지 상한(수술 예산과 분리)
    # HM-6(2026-08-20 사용자 결정 "하이브리드" — 비용): rewrite ON 일 때 통짜 이전-전용 변환 1콜을 휴머나이즈
    #   블록 맨 앞에 선행 → 그 결과 위에서 탐지·판정·잔여만 스팬 수술. 전량 스팬(화당 10~20만 tok) 대비
    #   화당 ~2~5만 tok. detect/rewrite OFF 면 무동작(하위호환 바이트 동일).
    humanize_llm_detect_bulk: bool = True    # env NOVEL_HUMANIZE_LLM_DETECT_BULK. HM-6: 하이브리드 통짜 선행. OFF=스팬 전용
    # RC-6: 최종화층 강제 교정 — 사용자가 '항상 결함'으로 *명시 확정*한 두 클래스만 기계 수술(그 외 미감·스타일 축은
    #   계속 advisory). ①스타카토/용언 없는 조각(N-7 고립 여운) 결정론 병합 ②폐기 조어·금지어(StyleSpec.deprecated_terms)
    #   정본 결정론 치환. humanize 플래그와 독립(강제) — humanize OFF 여도 이 두 클래스는 교정(잔존 보장 제거). 사실 가드
    #   G-A/G-B 통과분만 채택(폴백=원문). 무강제 예외 근거=정책 출처가 시스템 아닌 사용자(2026-08-21·08-29 확정).
    #   기본 OFF: 미설정 시 호출부가 finale_force 를 건너뛰어 본문 바이트 동일(하위호환). ②는 목록 비면 no-op.
    finale_force_fixes: bool = False         # env NOVEL_FINALE_FORCE_FIXES. RC-6: 최종화 강제 교정(2클래스). OFF=바이트 동일

    # PR-1: 제품 경로 정독 게이트(웹 "다음 화 생성"에 러너의 게이트 루프를 옵션으로). 기본 OFF — ON 시 회차당
    #   +1~3콜(cross-vendor 정독 심사 + FAIL 시 국소 revise/regen 재시도). 판정·라운드는 verification.gate 로
    #   영속(jsonl 무덤 해소). 무강제: 값·기록만·자동 차단 0 · 사실 불변 revise 우선 · R 상한. OFF 시 기존 경로 바이트 동일.
    product_gate: bool = False               # 제품 게이트 옵션(작가 결정 사안 — 비용 발생). off=FINALIZED 확정 경로 바이트 동일
    product_gate_max_retries: int = 2        # PR-1: FAIL 시 국소 revise/regen 재시도 상한 R(러너 GATE_MAX_RETRIES 와 동일 기본)
    product_gate_llm_cap: int = 12           # PR-1: 회차당 게이트 심사+국소 콜 상한(비용 하드 가드 — 초과 시 그 회차 게이트 중단·기록)

    # GA-1: 생성 트레이스 아카이브 — 회차 생성 파이프의 중간 산출물(첫 초안·재작성 라운드·style_judge 판정 전문·
    #   humanize 스팬 before/after·rerender 후보/평가)을 회차별 append-only 사이드카(`<pid>.trace.<ch>.json`)에
    #   전량 영속한다(본체 JSON·gen_context 무변경 — 순수 부가 관측). 기본 ON(사용자 지시 2026-07-15). env
    #   NOVEL_GEN_TRACE. OFF=save_trace 호출 0·사이드카 미생성·바이트 동일(무강제·비차단 — 저장 실패도 발행 무영향).
    gen_trace: bool = True
    gen_trace_max_runs: int = 50             # GA-1: 회차별 runs 배열 상한(무한 재생성 방어). 초과 시 오래된 것부터 드롭+드롭 카운트 기록(은폐 금지)

    # FI-1: 작가 의도 이벤트 영속(기각된 퇴고 제안·마찰·캐논 정정 원문 자산화 — 설계 docs/design-fi1-author-intent.md).
    #   GA-1 트레이스 사이드카에 신규 kind 'author_intent' 로 append(파일·메커니즘 신설 0·SSOT 단일). 관측 부가·무강제·
    #   비차단(기록 실패가 작가 액션을 안 막음)·LLM 0콜·생성 경로 read 0(이 원장을 프롬프트/후보 선택에서 읽는 것은 헌법 위반).
    #   OFF=emit 0(신규 파일 0·기존 파일 바이트 동일). env NOVEL_AUTHOR_INTENT_TRACE.
    author_intent_trace: bool = True
    intent_trace_max_runs: int = 200         # FI-1: author_intent 이벤트 전용 상한(kind 그룹 분리 — 대형 생성 run 이 소형 의도 이벤트를 축출하지 않게). 초과 시 오래된 것부터 드롭+dropped_intents 카운트(은폐 금지)

    # CV-1: 표지 이미지 생성(작가 발동형 — 자동 0). 파라미터 전부 config(모델 교체 대비 하드코딩 금지).
    #   OpenAI Images API(/v1/images/generations) thin client 가 이 값을 읽는다. 표지는 세로(2:3) 관습.
    image_model: str = "gpt-image-2"         # 이미지 모델(교체 시 이 값만)
    cover_size: str = "1024x1536"            # 세로 표지 비율(2:3 근사) — Images API size 파라미터
    cover_quality: str = "medium"            # 이미지 품질(low/medium/high — Images API quality 파라미터)

    data_dir: str = ""                       # 비우면 패키지 옆 data/

    def resolved_data_dir(self) -> Path:
        p = Path(self.data_dir) if self.data_dir else Path(__file__).resolve().parent.parent / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
