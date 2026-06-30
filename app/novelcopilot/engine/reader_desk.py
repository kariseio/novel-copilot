# -*- coding: utf-8 -*-
"""재미 검수 데스크 (G2) — 블라인드 장르 독자 행동 예측.

정합성 데스크(checker/quality_gates)와 분리된 '재미' 측정. 핵심 원칙:
- 절대 점수 금지(4개 설계 공통) — '몇 점'이 아니라 '독자가 어떻게 행동할지'(다음 화 결제 의향·이번 화에서 얻은 것).
- **적대적 기본값** — RLHF 독자 LLM 은 무난하면 칭찬하는 아첨 편향이 있다. 그래서 '손절이 기본, 결제는 예외'로 프레이밍해
  끊을 이유부터 찾게 한다(좋은 말만 하는 무용한 advisory 교정). 측정은 작가를 돕는 빨간펜이지 도장이 아니다.
- 블라인드 — 비트/설계/절정을 보여주지 않고 '본문만' 읽힌다(설계 의도가 아니라 실제 독자 경험을 본다).
- advisory(비차단·비구속) — 회차 확정을 막지 않는다. 작가에게 가시화만(빨간펜 조향은 작가). 강제·교정 주입 없음.
"""
from __future__ import annotations


def reader_prediction(provider, chapter_text: str, story_so_far: str, genre: str,
                      expectations: list | None = None) -> dict | None:
    """이 회차를 막 읽은 장르 독자의 '행동 예측'. 점수 아님 — got/pay_next/why. 실패 시 None(비차단).
    expectations: 장르 계약의 독자 기대(G5) — 독자가 무엇을 기대하는 장르인지 알고 판단(advisory, 강제 아님)."""
    if not (chapter_text or "").strip():
        return None
    exp = ("\n이 장르 독자가 보통 기대하는 것: " + ", ".join(str(e) for e in expectations[:5])
           if expectations else "")
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              f"너는 {genre or '웹소설'} 유료 연재를 까다롭게 고르는 독자다. 웹소설 대부분을 1~2화에서 손절하고, "
              "결제는 '안 하는 게 기본'이다. 칭찬하려 들지 말고 **이번 화를 끊을 이유부터 찾아라** — "
              "루즈함·기시감·공감 안 됨·전개 정체·억지 전개·뻔함·문장 늘어짐 등 무엇이든 걸리는 걸 집어라. "
              f"끊을 이유가 정말 없을 때만 다음 화를 결제한다.{exp} "
              "점수·편집자 시점 금지, 독자로서 솔직하게(아첨 금지 — 무난하면 손절이다). JSON: "
              '{"got":"이 회차에서 주인공이 실제로 얻은 것 한 줄(없거나 미미하면 \'없음\' — 보통 손절감)",'
              '"pay_next":false,"why":"결제/손절 여부와 그 이유 한 줄 — 손절이면 무엇이 걸렸는지 구체적으로"}'},
             {"role": "user", "content": f"[지금까지 줄거리]\n{(story_so_far or '')[:1500]}\n\n[이번 회차]\n{chapter_text[:9000]}"}],
            temperature=0.3, max_tokens=400)
        got = (r.get("got") or "").strip()
        why = (r.get("why") or "").strip()
        if not got and not why:
            return None
        return {"got": got, "pay_next": bool(r.get("pay_next")), "why": why}
    except Exception:
        return None
