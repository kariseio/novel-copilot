# -*- coding: utf-8 -*-
"""claim-audit (CN-2) — 자유형 사실 모순 advisory. RAG-grounded·비차단·non-hard.

구조화 체커(det/quasi)는 어휘에 선언된 속성(등급·소속·생사·관계·world-rule)만 본다. 그러나 연속성 결함의
긴 꼬리(검 색·날씨·목격·인과·소지품·장소 같은 *서술 디테일*)는 어휘 밖이라 체커가 못 잡는다. 이 패스는
새 회차를 키로 과거 회차 *프로즈*를 RAG 검색해, 정면으로 양립 불가한 구체 사실만 보수적으로 surface 한다.

원칙(B-24 교훈·두더지금지): (1) 비차단 — status 결정(hard_remaining)은 이 패스 *이전*에 끝난다. 여기서 무엇을
찾든 회차 발행을 막지 않는다(작가 가시화 advisory). (2) 보수성=정밀도 임계 — 확실한 충돌만(의도적 변화·반전·
모호·새 정보는 제외), 거짓양성보다 거짓음성을 택한다. (3) 자기참조 회피 — as_of=ch-1 로 새 회차 자신은 검색 제외.
(4) +0~1콜 — 과거 회차/검색결과 없으면 LLM 콜 0.
"""
from __future__ import annotations


def audit_chapter(provider, rag, chapter_text: str, ch_no: int, *,
                  query_hint: str = "", k: int = 4, excerpt_chars: int = 3500, cap: int = 6) -> list[dict]:
    """새 회차 vs 과거 회차 프로즈의 구체 사실 모순을 보수적으로 탐지. 모순 없으면 [] (advisory)."""
    text = (chapter_text or "").strip()
    if ch_no <= 1 or not text:
        return []
    query = (query_hint or text[:600]).strip()
    past = rag.search(query, ch_no - 1, k=k)          # 과거 회차만(새 회차 자신 제외) — 검색결과 0이면 콜 0
    if not past:
        return []
    refs = "\n".join(f"[{p.ref}화] {p.text}" for p in past if (p.text or "").strip())
    if not refs.strip():
        return []
    sys = ("너는 웹소설 연속성 감수자다. '새 회차'가 '이전 회차 발췌'의 *구체적 서술 사실*과 정면으로 양립 불가한 곳만 찾아라. "
           "등급·소속·생사·관계 같은 설정 항목은 별도 시스템이 점검하니 *제외*하고, 설정에 없는 '서술 디테일'의 충돌에 집중하라 — "
           "사물·외형·색, 소지품·무기 상태, 장소·동선, 날씨·시간대, 인물이 한 말·약속, 목격·정황 같은 것이 같은 대상에 대해 서로 어긋나는 경우만. "
           "보수적으로 판정하라 — 의도적 변화·반전·회상·모호한 서술·단순히 새로 추가된 정보는 모순이 아니다(제외). "
           "확신이 없으면 보고하지 마라(거짓경보보다 누락이 낫다). 모순이 없으면 빈 배열.\n"
           'JSON만: {"contradictions":[{"claim":"새 회차의 진술(짧게 인용/요약)","canon":"이전 회차의 진술(짧게)",'
           '"ref":"이전 회차 번호","why":"왜 양립 불가한지 한 줄"}]}')
    msg = [{"role": "system", "content": sys},
           {"role": "user", "content": f"[이전 회차 발췌]\n{refs}\n\n[새 회차({ch_no}화) 본문]\n{text[:excerpt_chars]}"}]
    try:
        res = provider.chat_json(msg, temperature=0.0)
    except Exception:
        return []                                      # 비차단: 실패해도 회차 발행 막지 않음
    out: list[dict] = []
    for c in (res.get("contradictions") or [])[:cap]:
        claim = (str(c.get("claim") or "")).strip()
        canon = (str(c.get("canon") or "")).strip()
        if claim and canon:                            # 양쪽 진술이 다 있어야 advisory(편측은 폐기)
            out.append({"claim": claim[:200], "canon": canon[:200],
                        "ref": str(c.get("ref") or "").strip(), "why": (str(c.get("why") or "")).strip()[:200]})
    return out
