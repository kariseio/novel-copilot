# -*- coding: utf-8 -*-
"""재미 검수 데스크 (G2) — 블라인드 장르 독자 행동 예측.

정합성 데스크(checker/quality_gates)와 분리된 '재미' 측정. 핵심 원칙:
- 절대 점수 금지(4개 설계 공통) — '몇 점'이 아니라 '독자가 어떻게 행동할지'(다음 화 결제 의향·이번 화에서 얻은 것).
- 블라인드 — 비트/설계/절정을 보여주지 않고 '본문만' 읽힌다(설계 의도가 아니라 실제 독자 경험을 본다).
- advisory(비차단·비구속) — 회차 확정을 막지 않는다. 작가에게 가시화만(빨간펜 조향은 작가). 강제·교정 주입 없음.
"""
from __future__ import annotations


def reader_prediction(provider, chapter_text: str, story_so_far: str, genre: str) -> dict | None:
    """이 회차를 막 읽은 장르 독자의 '행동 예측'. 점수 아님 — got/pay_next/why. 실패 시 None(비차단)."""
    if not (chapter_text or "").strip():
        return None
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              f"너는 {genre or '웹소설'} 유료 연재를 매일 보는 독자다. 방금 이 회차를 읽었다. "
              "점수를 매기지 말고, 솔직한 '독자 반응'만 답하라(편집자 시점 금지, 독자 시점). JSON: "
              '{"got":"이 회차에서 주인공이 실제로 얻은 것 한 줄(없으면 \'없음\')",'
              '"pay_next":true,"why":"다음 화를 결제할지/안 할지와 그 이유 한 줄"}'},
             {"role": "user", "content": f"[지금까지 줄거리]\n{(story_so_far or '')[:1500]}\n\n[이번 회차]\n{chapter_text[:9000]}"}],
            temperature=0.3, max_tokens=400)
        got = (r.get("got") or "").strip()
        why = (r.get("why") or "").strip()
        if not got and not why:
            return None
        return {"got": got, "pay_next": bool(r.get("pay_next")), "why": why}
    except Exception:
        return None
