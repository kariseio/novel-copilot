# -*- coding: utf-8 -*-
"""테스트 전역 환경 — 실 LLM 콜 0 계약.

ST-12d/RR-1: 재실현 파이프라인(config.rerender_in_pipeline)의 제품 기본값은 2026-07-16(RR-1)부로 OFF 다
(재실현은 opus-4-6 어트랙터 시대 장치 — Fable 전환 후 수동 도구로 강등. 상세: config.py 주석). 이 훅은
generate_next_chapter 발행 직후 rerender_chapter(BoN-N 실 LLM 콜)를 자동 실행하므로, 서비스를 실제로 세우고
generate_next_chapter 를 태우는 기존 테스트가 만약 ON 이면 매 회차 실 프로바이더(create_role_provider)를
호출하게 된다(테스트 실 LLM 콜 금지 위반·초저속·과금).

그래서 *테스트 프로세스에 한해* env NOVEL_RERENDER_IN_PIPELINE 미지정 시 OFF 로 고정한다(이제 제품 기본과 동일 —
env 가 상위 셸에서 켜져 있어도 테스트만은 OFF 를 보장하는 방어). 파이프라인 자체를 검증하는 ST-12d 테스트는
settings.model_copy(update={"rerender_in_pipeline": True}) 로 per-test 명시 활성화하고 rerender_chapter 를
스텁으로 대체해 실 LLM 콜 없이 훅 계약만 검증한다.

주의: config.get_settings() 는 최초 1회만 Settings() 를 만들어 캐시하므로, 이 설정은 어떤 테스트가
settings 를 만들기 전(=conftest 임포트 시점, pytest 컬렉션보다 앞)에 반드시 자리해야 한다.
"""
import os

# 사용자가 명시로 켜지 않은 한(=env 미지정), 테스트에서는 OFF. 제품 기본(config 기본값 True)은 건드리지 않는다.
os.environ.setdefault("NOVEL_RERENDER_IN_PIPELINE", "0")

# CE-1 ⓑ: aux_model 제품 기본은 "anthropic:claude-sonnet-5"(보조 스테이지 다운그레이드). 그대로 두면 세션 생성 시
#   create_role_provider 가 실 Anthropic client 를 세우려 해(키·네트워크) 테스트 실 LLM 콜 금지 계약과 충돌한다.
#   → *테스트 프로세스에 한해* NOVEL_AUX_MODEL 미지정 시 ""(스왑 0)로 강제 = aux is gen provider → 기존 경로 바이트
#   동일(계측·프롬프트 무회귀). aux 라우팅 자체를 검증하는 CE-1 테스트는 per-test 로 settings/스텁을 명시 주입한다.
os.environ.setdefault("NOVEL_AUX_MODEL", "")

# HZ-1 ①: style_judge_model 제품 기본은 "openai:gpt-5.2-chat-latest"(스타일 지각 판정·교차 벤더). 그대로 두면
#   harness finalize 의 스타일 지각 판정 스테이지가 실 OpenAI provider(create_role_provider)를 세워 실 chat_json
#   콜을 낸다(테스트 실 LLM 콜 금지 위반). → *테스트 프로세스에 한해* NOVEL_STYLE_JUDGE_MODEL 미지정 시 ""(스왑 0)로
#   강제 = 판정 provider = 주입된 gen provider(테스트의 스텁) → 실 LLM 0. 판정 라우팅·계약 자체는 per-test 스텁으로 검증.
os.environ.setdefault("NOVEL_STYLE_JUDGE_MODEL", "")

# TI-1 격리 핀(AG-1 실전 후 실측): 운영 .env 의 NOVEL_GEN_TOOLS=true 가 config 의 load_dotenv(override=True)로
#   테스트 환경까지 덮는다 — setdefault 는 .env 실재 키에 안 통하므로, config 임포트(=dotenv 로드 트리거) '후'에
#   강제 복원한다. 테스트 기본은 제품 기본(OFF)과 동일 — 툴 경로를 검증하는 테스트는 per-test 로 명시 활성화.
import novelcopilot.config as _cfg   # noqa: F401,E402  (dotenv 로드를 이 시점에 고정)
os.environ["NOVEL_GEN_TOOLS"] = "0"
