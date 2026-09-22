# -*- coding: utf-8 -*-
"""Anthropic(Claude) 프로바이더. chat=Claude, embed=OpenAI 위임(Anthropic 임베딩 API 없음).
모델 라우팅 A/B용 — 문장력·윤문·퇴고 1황 후보. gen_model 예: claude-sonnet-4-6 / claude-opus-4-8."""
from __future__ import annotations
import time
from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self, gen_model: str, embed_provider: LLMProvider, api_key: str | None = None,
                 max_output_cap: int = 16000):
        super().__init__()
        import anthropic
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()  # 없으면 ANTHROPIC_API_KEY env
        # LangSmith 트레이싱(옵트인): LANGSMITH_TRACING+API_KEY 가 환경에 있으면 클라이언트를 래핑 —
        #   모든 messages.create(툴 루프 포함)가 LANGSMITH_PROJECT 로 트레이스된다. 미설정·import 실패 시
        #   원 클라이언트 그대로(하위호환·무동작). 시크릿은 .env(깃 제외)에만 둔다.
        import os as _os
        if (_os.getenv("LANGSMITH_API_KEY")
                and str(_os.getenv("LANGSMITH_TRACING", "")).lower() in ("1", "true", "yes")):
            try:
                from langsmith.wrappers import wrap_anthropic
                self._client = wrap_anthropic(self._client)
            except Exception:
                pass
        self.gen_model = gen_model
        self._embed = embed_provider
        self._cap = max_output_cap
        # temperature: 신형 모델은 거부 → 4xx 를 보고 자동 제거하고 *인스턴스 단위로 기억*(이후 콜 헛 시도 0).
        #   thinking 상시 계열(opus-5·fable-5)은 항상 거부하므로 미리 끈다 — 인스턴스마다 400 왕복 1회를 아끼고,
        #   무엇보다 **호출부의 temperature 인자가 이 계열에서는 아무 효과가 없다는 사실을 코드에 드러낸다**
        #   (draft 가 0.85 를 넘기지만 실제로는 적용되지 않는다 — '온도로 다양성을 준다'는 설계 의도는 죽어 있다).
        #   모르는 모델은 종전대로 적응한다(하드코딩 목록에 의존하지 않음).
        self._thinking_always = any(k in gen_model for k in ("opus-5", "fable-5"))
        self._use_temp = not self._thinking_always
        # CE-1 ⓐ: 충분히 긴 system 을 콘텐츠 블록 배열 + cache_control(ephemeral)로 캐시. SDK/모델이 미지원 4xx 를
        #   내면 *인스턴스 단위로 기억*하고 이후엔 평문 문자열로 폴백(temperature 적응과 동형 — 하드브레이크 금지).
        self._use_cache = True
        # OV-5: 구조화 출력(output_config.format=json_schema) 지원 여부 — 미지원 4xx 시 인스턴스 단위 기억 후
        #   지시-기반 chat_json 폴백(동형 적응 패턴).
        self._use_output_schema = True

    # 이보다 긴 system 문자열만 캐시 블록으로 변환(짧으면 캐시 최소 프리픽스 미달·오버헤드만 → 평문 유지·하위호환).
    _CACHE_SYS_MIN_CHARS = 2000

    def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False) -> str:
        sys = "\n".join(m["content"] for m in messages if m["role"] == "system")
        conv = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        if not conv:
            conv = [{"role": "user", "content": sys or "."}]; sys = ""
        if json_mode:   # Anthropic 네이티브 json 모드 없음 — 지시로(base.chat_json 가 파싱 실패 시 재시도)
            sys = (sys + "\n유효한 JSON 객체만 출력. 설명·코드펜스 금지.").strip()
        # None=무상한(base.py 계약) — Anthropic API 는 max_tokens 필수라 프로바이더 상한으로 대입.
        mt = self._cap if max_tokens is None else min(int(max_tokens), self._cap)
        last = None
        for attempt in range(4):
            try:
                kw = dict(model=self.gen_model, max_tokens=mt, messages=conv)
                if sys:
                    # CE-1 ⓐ: 긴 system 은 cache_control(ephemeral) 블록으로(캐시 히트 입력 ~0.1×). 짧으면 평문 문자열
                    #   유지(하위호환·프리픽스 최소 미달). 미지원 인스턴스(_use_cache=False)면 항상 평문.
                    if self._use_cache and len(sys) >= self._CACHE_SYS_MIN_CHARS:
                        kw["system"] = [{"type": "text", "text": sys,
                                         "cache_control": {"type": "ephemeral"}}]
                    else:
                        kw["system"] = sys
                if self._use_temp:
                    kw["temperature"] = temperature
                r = self._client.messages.create(**kw)
                self.usage.chat_calls += 1
                if getattr(r, "usage", None):
                    # Anthropic 응답 계약: input_tokens 는 '비캐시 잔여'만(캐시 생성/읽기 토큰 미포함 — 세 값 상호배타).
                    #   → chat_tokens=input+output 산식 불변(이중 계상 0). 캐시 토큰은 별도 카운터로만 관측 누적(additive).
                    self.usage.chat_tokens += int(r.usage.input_tokens + r.usage.output_tokens)
                    self.usage.cache_creation_input_tokens += int(getattr(r.usage, "cache_creation_input_tokens", 0) or 0)
                    self.usage.cache_read_input_tokens += int(getattr(r.usage, "cache_read_input_tokens", 0) or 0)
                self.last_truncated = (getattr(r, "stop_reason", None) == "max_tokens")   # 절단 신호(harness 재생성용)
                return "".join(b.text for b in r.content if getattr(b, "type", None) == "text") or ""
            except Exception as e:
                msg = str(e)
                # 파라미터 적응(인스턴스 단위 기억 → 이후 콜 헛 시도 0): temperature 를 이유로 4xx 가 나면 제거.
                #   구형 사유("deprecated/unsupported")뿐 아니라 thinking 상시 모델(claude-fable-5)의
                #   "`temperature` may only be set to 1 when thinking is enabled" 류도 포괄(ST-16 — 문구 종속 제거).
                if self._use_temp and "temperature" in msg:
                    self._use_temp = False
                    continue   # 즉시 재시도(대기 없음)
                # CE-1 ⓐ: SDK/모델이 cache_control 미지원 4xx 를 내면 1회 적응 후 평문 폴백(인스턴스 단위 기억 →
                #   이후 콜 헛 시도 0). temperature 적응과 동형 — 하드브레이크 금지.
                if self._use_cache and "cache_control" in msg:
                    self._use_cache = False
                    continue   # 즉시 재시도(대기 없음)
                last = e
                time.sleep(2 * (attempt + 1))
        raise last

    def chat_json(self, messages, *, temperature=0.0, max_tokens=None, schema=None) -> dict:
        """OV-5: schema 지정 시 구조화 출력(output_config.format=json_schema)으로 유효 JSON 을 API 레벨 강제.

        지시-기반 JSON 의 실패 양식(적응형 사고와 출력 상한 경합·절단·형식 붕괴 → 파싱 실패가 침묵 {} 강등)
        소스 차단 — 온톨로지 제안 스테이지 침묵 사망(신작 2·3화 실측)의 수술.
        · 절단(stop_reason=max_tokens)은 완전성 보장 밖 → 정직 실패(ValueError — 부분 JSON 세탁 금지).
        · 미지원 SDK/모델 4xx 는 인스턴스 단위 기억 후 기존 경로 폴백(temperature 적응과 동형).
        · schema=None 이면 기존 경로 그대로(요청 바이트 동일 — 하위호환)."""
        import json as _json
        if not schema or not self._use_output_schema:
            return super().chat_json(messages, temperature=temperature, max_tokens=max_tokens)
        sys = "\n".join(m["content"] for m in messages if m["role"] == "system")
        conv = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        if not conv:
            conv = [{"role": "user", "content": sys or "."}]; sys = ""
        mt = self._cap if max_tokens is None else min(int(max_tokens), self._cap)
        last = None
        for attempt in range(4):
            # extra_body 경유: 구 SDK 는 output_config 를 명명 인자로 거부(TypeError)하지만 extra_body 는
            #   미지의 본문 필드를 그대로 통과시킨다(SDK 타이핑 지연 시의 공식 우회로) — 서버는 GA 파라미터로 수용.
            kw = dict(model=self.gen_model, max_tokens=mt, messages=conv,
                      extra_body={"output_config": {"format": {"type": "json_schema", "schema": schema}}})
            if sys:
                if self._use_cache and len(sys) >= self._CACHE_SYS_MIN_CHARS:
                    kw["system"] = [{"type": "text", "text": sys, "cache_control": {"type": "ephemeral"}}]
                else:
                    kw["system"] = sys
            if self._use_temp:
                kw["temperature"] = temperature
            try:
                r = self._client.messages.create(**kw)
            except Exception as e:
                msg = str(e)
                if self._use_temp and "temperature" in msg:
                    self._use_temp = False
                    continue
                if self._use_cache and "cache_control" in msg:
                    self._use_cache = False
                    continue
                if any(k in msg for k in ("output_config", "json_schema", "output_format")):
                    self._use_output_schema = False   # 미지원 — 이후 콜 헛 시도 0(지시-기반 폴백)
                    return super().chat_json(messages, temperature=temperature, max_tokens=max_tokens)
                last = e
                time.sleep(2 * (attempt + 1))
                continue
            self.usage.chat_calls += 1
            if getattr(r, "usage", None):
                self.usage.chat_tokens += int(r.usage.input_tokens + r.usage.output_tokens)
                self.usage.cache_creation_input_tokens += int(getattr(r.usage, "cache_creation_input_tokens", 0) or 0)
                self.usage.cache_read_input_tokens += int(getattr(r.usage, "cache_read_input_tokens", 0) or 0)
            self.last_truncated = (getattr(r, "stop_reason", None) == "max_tokens")
            if self.last_truncated:
                raise ValueError("chat_json(schema): 출력 절단(stop_reason=max_tokens) — 상한 상향 후 재실행")
            txt = "".join(b.text for b in r.content if getattr(b, "type", None) == "text") or ""
            return _json.loads(txt)   # 스키마 강제 = 유효 JSON. 실패는 예외로 정직 전파(빈 {} 세탁 금지)
        raise last

    def chat_tools(self, messages, *, tools=None, tool_handler=None,
                   temperature=0.7, max_tokens=None, max_rounds=6) -> str:
        """AG-1 T1 — 툴 인터리브 생성(에이전틱 pull). 설계 계약(보드 AG-1):
        · 라운드별 text 블록을 순서대로 결합(마지막 라운드만 취하면 앞부분 유실)
        · 상한 도달 시 tools 없이 1회 마무리, 예외 시 기존 단발 chat 폴백(안전 강등)
        · 어시스턴트 턴은 응답 content 를 그대로 보존(thinking 상시 모델의 thinking 블록 서명 유지)
        · tools 배열은 매 라운드 동일(캐시 프리픽스 유지) · tool_result 길이 상한."""
        if not tools or not callable(tool_handler):
            return self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        sys = "\n".join(m["content"] for m in messages if m["role"] == "system")
        conv = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
        if not conv:
            conv = [{"role": "user", "content": sys or "."}]; sys = ""
        mt = self._cap if max_tokens is None else min(int(max_tokens), self._cap)   # None=무상한 → 프로바이더 상한
        texts: list[str] = []
        for round_no in range(max_rounds + 1):
            use_tools = round_no < max_rounds
            r = None
            for attempt in range(3):
                kw = dict(model=self.gen_model, max_tokens=mt, messages=conv)
                if sys:
                    if self._use_cache and len(sys) >= self._CACHE_SYS_MIN_CHARS:
                        kw["system"] = [{"type": "text", "text": sys,
                                         "cache_control": {"type": "ephemeral"}}]
                    else:
                        kw["system"] = sys
                if self._use_temp:
                    kw["temperature"] = temperature
                if use_tools:
                    kw["tools"] = tools
                try:
                    r = self._client.messages.create(**kw)
                    break
                except Exception as e:   # chat() 과 동형의 파라미터 적응(인스턴스 단위 기억)
                    msg = str(e)
                    if self._use_temp and "temperature" in msg:
                        self._use_temp = False
                        continue
                    if self._use_cache and "cache_control" in msg:
                        self._use_cache = False
                        continue
                    time.sleep(2 * (attempt + 1))
            if r is None:   # 라운드 실패 — 안전 강등(이미 뽑힌 텍스트가 있으면 보존, 없으면 단발 폴백)
                return "".join(texts) if texts else self.chat(messages, temperature=temperature, max_tokens=max_tokens)
            self.usage.chat_calls += 1
            if getattr(r, "usage", None):
                self.usage.chat_tokens += int(r.usage.input_tokens + r.usage.output_tokens)
                self.usage.cache_creation_input_tokens += int(getattr(r.usage, "cache_creation_input_tokens", 0) or 0)
                self.usage.cache_read_input_tokens += int(getattr(r.usage, "cache_read_input_tokens", 0) or 0)
            texts += [b.text for b in r.content if getattr(b, "type", None) == "text"]
            if getattr(r, "stop_reason", None) != "tool_use":
                self.last_truncated = (getattr(r, "stop_reason", None) == "max_tokens")
                break
            conv.append({"role": "assistant", "content": r.content})   # thinking·tool_use 블록 원형 보존
            results = []
            for b in r.content:
                if getattr(b, "type", None) == "tool_use":
                    self.usage.tool_calls += 1
                    try:
                        out = tool_handler(b.name, dict(b.input or {}))
                    except Exception as e:
                        out = f"(조회 실패: {e})"
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": str(out)[:4000]})
            conv.append({"role": "user", "content": results})
        return "".join(texts)

    def embed(self, texts):
        return self._embed.embed(texts)
