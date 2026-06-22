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

    llm_provider: str = "openai"
    gen_model: str = "gpt-4.1"
    embed_model: str = "text-embedding-3-small"

    # 컨텍스트 예산 — '기아 해소' 재배분(docs/context-redesign.md): 입력 ~40k토큰 목표, 헤드룸 60% 유지.
    gen_max_tokens: int = 3000
    chapter_max_tokens: int = 9000          # 단일 패스 회차 집필 출력 예산(장면 이어붙이기 폐기 — 재설계)
    max_rewrite_rounds: int = 3
    prev_chapter_context_chars: int = 8000   # 직전 회차 '전문' 수준(실전 1순위 관행 — 기존 4,000자는 56%만 전달)
    story_so_far_chars: int = 12000          # 누적 줄거리(현재 에피소드=상세 시놉시스 1,500자/화 + 과거=한줄·롤업)
    bible_digest_chars: int = 3500           # 설정집 카드(키워드 선별 — 심층 설정집 41+항목 대응)
    rag_k: int = 6                           # 과거 회차 의미 검색 청크 수(기존 3 하드코딩)
    wiki_k: int = 3                          # 인물카드 주입 수(기존 2 하드코딩)
    # 메모리 상한(장수 프로세스 OOM 방지 — 축출/만료해도 정보 손실 0: 세션=디스크 재수화, 드래프트=휘발 명세)
    max_live_sessions: int = 32              # 동시 캐시 세션 수(LRU)
    draft_ttl_sec: int = 6 * 3600            # 미접촉 컨셉 드래프트 폐기 기준(초)
    max_drafts: int = 200                    # 드래프트 하드캡(TTL 내 폭주 방어)
    continuity_polish: bool = True           # 회차 내부(수치·소지품) 연속성 교정 패스(+1콜/화)
    plant_backlog_threshold: int = 3         # 미회수 복선 적체 경보 임계(advisory)
    plant_inject_cap: int = 5                # 비트 설계에 참고로 노출할 미회수 복선 최대 수(plant_reminder opt-in 시)
    reader_desk: bool = True                 # G2: 블라인드 독자 행동 예측(advisory, +1콜/화). 비용 절감 시 off
    event_menu: bool = True                  # T3: 에피소드 활성 시 '적시 사건 메뉴' 생성(+1콜/에피소드 ≈ +0.1~0.3콜/화). off=결정론 폴백
    event_menu_refresh_every: int = 0        # T4: >0 이면 에피소드 중반 N회차마다 메뉴 재생성(긴 EP stale 해소, +비용·비결정성). 0=off(T3 1회 캐시)
    world_obsession: bool = True             # 풍부함: worldgen 전 '집착 벡터' 추출→편중 파생(평균회귀 차단, +1콜). A/B 실측 ON 5:0 OFF·8.0 vs 5.0

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
