# AI 웹소설 코파일럿 (실제 서비스)

PoC `v2-engine`(비대칭 일관성 엔진)을 **하드코딩 배제 + 디자인 패턴 + 엔진 고도화**로 재구성하고,
**브라우저에서 직접 체험**할 수 있는 웹 서비스로 만든 것.

> "기계가 쓰고, 작가가 조향한다." 시드 한 줄 → LLM 세계관 설계(worldgen) → 회차 단위 점진 생성.
> 설정이 어긋나면 일관성 엔진이 잡아 **자동 재집필**하거나, 비수렴이면 **작가에게 escalation**.

## 실행

```powershell
# 1) 의존성
pip install -r requirements.txt
# 2) 환경변수 (.env.example 복사)
#    OPENAI_API_KEY, NOVEL_GEN_MODEL 등 — 코드에 키/모델 하드코딩 없음
$env:OPENAI_API_KEY = "sk-..."
# 3) 실행
python run.py            # http://127.0.0.1:8000  (브라우저로 열기)
```

스모크 테스트:
```powershell
$env:PYTHONPATH="."; python smoke.py          # 임포트 + 결정론 코어(LLM 0콜)
$env:PYTHONPATH="."; python smoke.py --live    # 실제 worldgen + 1회차 생성
```

## 체험 흐름 (웹페이지)

1. **새 작품**: 장르/톤/전제 한 줄 입력 → `worldgen` 이 추적 가능한 설정집(속성·인물·세계규칙·골격·복선) 생성.
2. **회차 생성**: "다음 회차 생성 ▶" → 에이전틱 하네스 루프가 **실시간(SSE)** 으로 흐른다
   (plan → draft → consistency_check → partial_rewrite → finalize). 위반 탐지·재집필·escalation 가시화.
3. **작가 지시(빨간 펜)**: 지시를 넣으면 이번 회차부터 누적 전파.
4. **인스펙터**: 🧭 온톨로지 SSOT(결정론 '박기') / 🔗 LLM Wiki(컴파운딩 '찾기' + 결정론 lint) 실시간 관찰.
5. **소설/설정집 탭**: 생성된 회차를 읽고, 동적으로 갱신되는 세계관을 본다.

## 무엇이 바뀌었나 (PoC 대비)

### 1) 하드코딩 배제 — 세계관이 데이터
- `scenario.py`(붉은 눈 세계 하드코딩)·`CATEGORICAL_VOCAB`·`ATTR_LABEL` 전부 제거.
- 모든 세계관은 `WorldConfig`(속성/인물/룰/어휘/문체/골격) **데이터**. 룰 레지스트리·통제어휘·추출 스키마가
  전부 여기서 **파생**(`engine/factory.py`). 새 세계관 = 새 데이터, 코드 변경 0.

### 2) 디자인 패턴
| 패턴 | 위치 |
|---|---|
| Strategy | `llm/`(프로바이더 교체) · `engine/rules/predicates.py`(술어별 평가기, if/elif 제거) |
| Factory | `engine/factory.py`(World→엔진) · `llm/factory.py`(프로바이더) |
| Registry | 룰/술어/프로바이더 레지스트리 |
| Repository | `repository/`(파일시스템, DB 교체 가능) |
| Observer | `engine/observability.py`(EventBus → SSE) |
| Builder | `engine/prompts.py`(프롬프트 단일 직렬화 지점) |
| DI | 모듈 전역 client/STATS 제거, 전부 주입 |
| Facade | `services/copilot.py` |

### 3) 엔진 고도화
- **동적 온톨로지 업데이트**(`engine/ontology_updater.py`): 회차 finalize 후 본문에서 신규 인물·상태 변화 추출.
  정책(데이터 주도, `AttributeSpec.mutable`): **신규 인물 자동 커밋 / 가변 속성 변화는 timeline 전진 /
  불변 속성·단조 위반·사망자 부활은 모순 → escalation(미적용)**. 덮어쓰기 없음.
- **서사 흐름 연속성**(`engine/prompts.py`): 직전 회차 **원문**(700자 꼬리 → 설정값 길이 4,000자)과
  회차 내 직전 장면을 함께 주입 + "도입 재설명 금지, 이어쓰기" 명령.

## 레이어 구조

```
domain/      타입 계약(엔진 + WorldConfig + ProjectState)
llm/         LLMProvider(Strategy) + factory
engine/      vocabulary·ontology·rules(predicates)·rag·extractor·wiki·checker
             ·prompts·harness·ontology_updater·factory·observability
worldgen/    시드→WorldConfig 생성 + 비트 자동 연장
repository/   영속화(파일시스템)
services/    session(재수화/스냅샷) + copilot(유스케이스 Facade)
api/         FastAPI 라우트 + SSE
web/         체험 프론트엔드(빌드 불필요)
```

## 비대칭 일관성 불변식 (계승)
- ground_truth(온톨로지) = 결정론 '박기' / narrative(RAG·Wiki) = '찾기'(cap·신뢰가중, 승격 불가) — **타입으로 분리 강제**.
- 결정론 코어(`ontology_internal_check`, `wiki.lint`)는 LLM 산출물을 입력으로 받지 않음(LLM 0콜).
- det/quasi 위반만 hard(게이트 구속), semantic 은 보고/escalation.

## 한계 (정직)
일관성 엔진은 "안 어긋남"만 보장한다. **"재미"는 별도 레이어**(PoC 결과보고서 참조)로 본 서비스의 다음 과제다.
의미층은 LLM 판단(우회 가능), OOV escalation 부분, 본문↔본문 모순 사각지대는 그대로 남아있다.
