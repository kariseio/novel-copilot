"""AI 웹소설 코파일럿 — 실제 서비스 패키지.

설계 원칙(요약):
- 하드코딩 배제: 세계관·어휘·룰·문체는 모두 WorldConfig 데이터로 외부화(코드 분기 금지).
- 디자인 패턴: Strategy(LLM/룰 술어), Factory(세계→엔진/프로바이더), Registry(룰/술어/프로바이더),
  Repository(영속화), Observer(이벤트 버스→SSE), Builder(프롬프트), DI(모듈 전역 제거).
- 비대칭 일관성 불변식은 PoC v2-engine 을 계승하되 타입/주입으로 강제.
"""
__all__ = ["__version__"]
__version__ = "1.0.0"
