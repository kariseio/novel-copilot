# -*- coding: utf-8 -*-
"""이미지 생성 thin client — OpenAI Images API(/v1/images/generations) 전용(chat 스택 무접촉).

CV-1: 표지 이미지 1콜. 기존 OpenAIProvider 의 키/HTTP 클라이언트 패턴(OpenAI SDK, env OPENAI_API_KEY,
retry)을 재사용하되 LLMProvider(chat/embed)와는 분리한다 — 표지는 회차 파이프와 완전 격리.
파라미터(model/size/quality)는 전부 호출자가 config 에서 넘긴다(하드코딩 금지 — 모델 교체 대비).
b64 응답 → PNG 바이트로 디코드. 실패는 예외로 전파(침묵 폴백 금지 — P-2 교훈 · 호출자가 무변경 유지).
"""
from __future__ import annotations
import base64
import time


class ImageClient:
    """OpenAI Images thin client. 전역 client 금지(DI) — 테스트는 fake client 주입.
    usage.image_calls 로 건당 계측(usage_total 에 additive 계상)."""

    def __init__(self, client=None, timeout: float = 300.0):
        if client is None:
            from openai import OpenAI
            client = OpenAI(timeout=timeout)     # env OPENAI_API_KEY(config.load_dotenv 로 프로세스에 주입) 재사용
        self._client = client
        self.image_calls = 0

    def generate_png(self, prompt: str, *, model: str, size: str, quality: str) -> bytes:
        """이미지 1콜 → PNG 바이트. b64_json 응답을 디코드. 실패 시 예외 전파(무변경 계약은 호출자 몫).

        파라미터는 전부 호출자 주입(config SSOT). 신형/구형 파라미터 차이는 chat 스택과 동일한 적응 재시도로 흡수:
        일부 모델이 quality 값을 거부하면 quality 없이 1회 재시도(모델 교체 견고성 — 코드 무변경).
        """
        if not (prompt or "").strip():
            raise ValueError("이미지 프롬프트가 비었습니다")
        kwargs = dict(model=model, prompt=prompt, size=size, quality=quality, n=1)
        last = None
        for attempt in range(3):
            try:
                try:
                    r = self._client.images.generate(**kwargs)
                except Exception as e:   # 모델별 파라미터 차이 자동 적응(quality 미지원 등) — 1회 완화 후 재시도
                    msg = str(e).lower()
                    if "quality" in msg and ("unsupported" in msg or "does not support" in msg
                                             or "invalid" in msg) and "quality" in kwargs:
                        kwargs.pop("quality", None)
                        r = self._client.images.generate(**kwargs)
                    else:
                        raise
                b64 = r.data[0].b64_json
                if not b64:
                    raise ValueError("이미지 응답에 b64_json 이 없습니다")
                self.image_calls += 1
                return base64.b64decode(b64)
            except Exception as e:
                last = e
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
        raise last
