# NovelCopilot — AI 웹소설 코파일럿

시드 한 줄에서 세계관을 설계하고, 회차 단위로 본문을 생성·검증·퇴고하는 웹 서비스입니다.
FastAPI 백엔드 + 빌드가 필요 없는 정적 프론트엔드로 구성되어 있고, 브라우저에서 바로 씁니다.

- 세계관 설계(worldgen): 시드 → 인물·속성·세계 규칙·아크·복선을 데이터(WorldConfig)로 생성
- 회차 생성 하네스: plan → draft → 일관성 검사 → 부분 재집필 → 최종화, 진행 상황은 SSE 로 실시간 표시
- 일관성 엔진: 온톨로지(결정론 SSOT) + RAG/Wiki(참조) 로 설정 위반을 잡고, 비수렴이면 작가에게 escalation
- 작가 조향: 작가 지시(누적 전파), 퇴고, 설정집·스킬 편집, 회차별 생성 트레이스 열람

## 구성

```
app/
  run.py                  개발 서버 런처 (python run.py)
  smoke.py                임포트 + 결정론 코어 스모크 (LLM 0콜) / --live 로 실제 생성 1회
  requirements.txt        실행 의존성
  requirements-dev.txt    테스트·도구 의존성
  .env.example            환경변수 템플릿 → app/.env 로 복사
  novelcopilot/           서비스 본체
    main.py               FastAPI 앱 — /api 라우트 + 정적 프론트(/) 마운트
    config.py             Settings — 모든 항목이 NOVEL_* 환경변수로 덮어써짐
    api/                  REST 라우트(routes.py)·요청 스키마·SSE
    domain/               타입 계약 (WorldConfig·ProjectState·스킬 등)
    engine/               온톨로지·룰·RAG·프롬프트·하네스(회차 생성 루프)·휴머나이즈
    llm/                  프로바이더(anthropic / openai / gemini) + 프롬프트 로그
    worldgen/             시드 → 세계관·아크·회차 설계
    repository/           파일시스템 영속화 (app/data/)
    services/             세션 재수화·코파일럿 유스케이스(Facade)
    web/                  프론트엔드 (index.html·manual.html·app.js·style.css)
  tools/                  테스트(test_*.py)와 계측·실험 스크립트
docs/guide/               앱 내 사용 설명서 원문 (/manual.html 이 /api/docs/{name} 으로 읽음)
docs/PIPELINE.md          회차 생성 파이프라인 기술 문서
docs/ARCHITECTURE.md      레이어 구조 기술 문서
scripts/                  운영 스크립트 (Windows 상시 구동 등록, GitHub 공개 브랜치 내보내기)
```

## 요구 사항

- Python 3.12 (3.11 이상이면 동작하지만 3.12 에서 검증했습니다)
- API 키
  - `ANTHROPIC_API_KEY` — 본문 집필·세계관 설계 (기본 프로바이더)
  - `OPENAI_API_KEY` — 임베딩(RAG 검색)·교차 벤더 심사·표지 이미지. Anthropic 만 쓸 때도 임베딩 때문에 필요합니다.
  - `GEMINI_API_KEY` — 선택. 모델 라우팅에 `gemini:` 를 지정할 때만

## 설치

```powershell
git clone <repo-url>
cd ai-web-novel/app
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS/Linux 는 `python3.12 -m venv .venv && source .venv/bin/activate` 로 같은 순서입니다.

선택 의존성은 필요할 때만 설치합니다.

| 용도 | 설치 |
|---|---|
| 테스트·도구 스크립트 | `pip install -r requirements-dev.txt` |
| Gemini 프로바이더 | `pip install google-genai` |
| LangSmith 트레이싱 | `pip install langsmith` |
| 형태소 기반 문체 계측(tools/kiwi_metrics.py) | `pip install -r tools/requirements-metrics.txt` |

## 환경변수 (.env)

`app/.env.example` 을 `app/.env` 로 복사하고 키를 채웁니다. `.env` 는 git 에서 제외되어 있습니다.

```powershell
Copy-Item .env.example .env
```

`novelcopilot/config.py` 가 import 시 `app/.env` 를 프로세스 환경으로 로드하므로, SDK 가 직접 읽는
`OPENAI_API_KEY` 같은 키도 `.env` 에 넣으면 됩니다. 셸 환경변수로 넣어도 동작합니다.

주요 항목:

| 변수 | 의미 | 기본값 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic 키 (필수) | — |
| `OPENAI_API_KEY` | OpenAI 키 (필수 — 임베딩·심사) | — |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini 키 (선택) | — |
| `NOVEL_HOST` / `NOVEL_PORT` | 서버 바인드 주소·포트 (`run.py`) | `127.0.0.1` / `8000` |
| `NOVEL_RELOAD` | 코드 변경 시 자동 재시작 (개발용, `1` 로 켬) | 꺼짐 |
| `NOVEL_DATA_DIR` | 작품 데이터 저장 위치 | `app/data/` |
| `NOVEL_LLM_PROVIDER` | 프로바이더 접두어를 생략한 모델의 기본 프로바이더 | `anthropic` |
| `NOVEL_GEN_MODEL` | 본문 집필 모델 | `config.py` 참조 |
| `NOVEL_WORLDGEN_MODEL` / `NOVEL_PLANNING_MODEL` | 세계관·아크 설계 모델 | `config.py` 참조 |
| `NOVEL_AUX_MODEL` / `NOVEL_EXTRACT_MODEL` | 보조·추출 스테이지 모델 | `config.py` 참조 |
| `NOVEL_STYLE_JUDGE_MODEL` | 문체 심사 모델 (집필과 다른 벤더 권장) | `config.py` 참조 |
| `NOVEL_EMBED_MODEL` | 임베딩 모델 (OpenAI) | `text-embedding-3-small` |
| `NOVEL_PROMPT_LOG` | LLM 콜 전문을 `app/logs/prompts/` 에 저장 | 켜짐 (`0` 으로 끔) |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | LangSmith 트레이싱 (선택) | 꺼짐 |

모델 지정 형식은 `프로바이더:모델` 입니다 (예: `anthropic:claude-opus-4-8`, `openai:gpt-5.6`).
프로바이더를 생략하면 `NOVEL_LLM_PROVIDER` 가 적용됩니다.

이 표에 없는 항목(재집필 횟수, 컨텍스트 길이, 각 스테이지 on/off 등)도 전부 `config.py` 의 `Settings`
필드이며, 필드명을 대문자로 바꾸고 `NOVEL_` 을 붙인 환경변수로 덮어쓸 수 있습니다
(예: `max_rewrite_rounds` → `NOVEL_MAX_REWRITE_ROUNDS=2`).

## 실행

```powershell
cd app
python run.py
```

브라우저에서 http://127.0.0.1:8000 을 엽니다. uvicorn 을 직접 띄워도 됩니다.

```powershell
python -m uvicorn novelcopilot.main:app --host 127.0.0.1 --port 8000
```

- 상태 확인: `GET /api/health`
- API 문서(Swagger): http://127.0.0.1:8000/docs
- 사용 설명서: http://127.0.0.1:8000/manual.html
- 정적 프론트는 디스크 변경을 재시작 없이 반영합니다. 파이썬 코드 변경은 재시작(또는 `NOVEL_RELOAD=1`)이 필요합니다.

### Windows 에서 상시 구동

로그온 시 자동 시작되는 작업 스케줄러 태스크(`novelcopilot-server`)로 등록합니다. 콘솔 창 없이 돌고
로그는 `app/logs/server_stdout.log` 에 쌓입니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\server_task_setup.ps1
```

스크립트는 `app/logs/` 를 만들고, `app/.venv` 가 있으면 그 파이썬으로, 없으면 전역 `py -3.12` 로 uvicorn 을 띄웁니다.
이후 조작은 `Start-ScheduledTask` / `Stop-ScheduledTask -TaskName novelcopilot-server` 로 합니다.

> ⚠️ 이 태스크는 `0.0.0.0` 에 바인드하고, 앱에는 로그인·인증이 없습니다. 같은 네트워크의 누구나 접속해
> 여러분의 API 키로 생성을 돌릴 수 있습니다. 신뢰할 수 없는 네트워크에서는 스크립트의 `--host` 를
> `127.0.0.1` 로 바꾸거나 방화벽으로 포트를 막으세요. `python run.py` 는 기본이 `127.0.0.1` 이라 로컬 전용입니다.

## 확인

```powershell
cd app
python smoke.py          # 임포트 + 결정론 코어 (LLM 호출 없음, 키 불필요)
python smoke.py --live   # 실제 세계관 설계 + 1회차 생성 (API 비용 발생)
```

## 테스트

```powershell
cd app
pip install -r requirements-dev.txt
python -m pytest tools -q
```

테스트는 실제 LLM 을 호출하지 않는 계약입니다(`tools/conftest.py` 가 실 프로바이더 경로를 스텁으로 고정).
API 키 없이 돌아갑니다.

## 데이터와 로그

- `app/data/` — 작품·회차·설정집 JSON. 백업은 이 디렉터리를 통째로 복사하면 됩니다. git 제외.
- `app/logs/prompts/` — 프롬프트 전문 로그(`NOVEL_PROMPT_LOG`). git 제외.
- `app/logs/server_stdout.log` — 상시 구동 태스크의 서버 로그.

## GitHub 공개 브랜치 내보내기

내부 작업 저장소에는 설계 문서·리뷰 기록·발표 자료가 함께 있습니다. 공개용으로는 코드와 사용 설명서만
추려 별도 브랜치(`github-public`)를 만듭니다. 작업 트리와 현재 브랜치는 건드리지 않습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\export_github.ps1
git push origin github-public:main
```

실행할 때마다 `github-public` 위에 커밋이 하나 쌓이고(트리가 같으면 건너뜀), 원격 `main` 은 fast-forward 로 따라갑니다.
브랜치를 처음 만들 때 기존 원격 이력을 이으려면 먼저 `git branch -f github-public origin/main` 을 실행합니다.

GitLab(`gitlab-origin`) 에는 같은 트리를 별도 브랜치로 잇습니다. 원격 이력이 서로 달라 브랜치를 나눈 것이며, 두 브랜치의 트리는 같습니다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\export_github.ps1 -Branch gitlab-public
git push gitlab-origin gitlab-public:main
```
제외·재포함 규칙은 스크립트 상단의 `$Exclude` / `$Reinclude` 에 있습니다.

## 라이선스

MIT — [LICENSE](LICENSE)
