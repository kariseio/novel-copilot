# -*- coding: utf-8 -*-
"""재미 검수 데스크 (G2) — 블라인드 장르 독자 행동 예측.

정합성 데스크(checker/quality_gates)와 분리된 '재미' 측정. 핵심 원칙:
- 절대 점수 금지(4개 설계 공통) — '몇 점'이 아니라 '독자가 어떻게 행동할지'(하차 여부·이탈을 유발한 대목·잔존 추정).
- **hair-trigger 기본값(DP-10)** — 구 스키마의 이진 pay_next 는 저온 RLHF 아첨과 만나 98.7% 포화(신호 소멸)였다.
  그래서 '손가락이 이미 뒤로가기에 얹혀 있다'로 프레이밍하고, 스키마가 '이탈 대목을 본문 그대로 인용(kill_trigger)'
  하도록 강제한다(자유서술 아첨을 원문 증거로 대체). 측정은 작가를 돕는 빨간펜이지 도장이 아니다.
- 블라인드 — 비트/설계/절정을 보여주지 않고 '본문만' 읽힌다(설계 의도가 아니라 실제 독자 경험을 본다).
- advisory(비차단·비구속) — 회차 확정을 막지 않는다. 작가에게 가시화만(빨간펜 조향은 작가). 강제·교정 주입 없음.
- **실독자가 아니라 LLM 시뮬**이다 — UI 는 이를 정직하게 라벨링한다('시뮬 독자(참고)').
"""
from __future__ import annotations
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)


def _tail_budget(story_so_far: str, budget: int) -> str:
    """DP-15: '지금까지 줄거리'를 예산 내로 절단하되 **최신 맥락(꼬리)을 보존**한다.

    story_so_far 는 계층 요약이 시간순(오래된 것 앞·최신 뒤)으로 조립된다(_build_story_so_far / _hier:
    reversed 로 채운 뒤 다시 시간순 join → 최근 회차 상세가 문자열 끝에 온다). 그런데 기존 [:budget] 은
    '앞(오래된 쪽)'을 남기고 '뒤(최신 쪽)'를 버렸다 → 방금 벌어진 사건을 시뮬 독자가 못 봐 실재하지 않는
    모순을 지적(DP-4b 7화 retention 오염). 그래서 예산 초과 시 오래된 앞쪽을 잘라내고 최신 꼬리를 남긴다.
    잘렸음을 표시(…앞부분 생략)해 독자가 '이야기 처음부터'로 오독하지 않게 한다."""
    s = story_so_far or ""
    if budget < 0 or len(s) <= budget:
        return s
    if budget == 0:
        return ""
    mark = "…(앞부분 줄거리 생략)…\n"
    keep = budget - len(mark)
    if keep <= 0:          # 예산이 마커보다도 작으면 마커 없이 순수 꼬리만(예산 정확 보장)
        return s[-budget:]
    return mark + s[-keep:]


@promptlog.stage("reader_desk")
def reader_prediction(provider, chapter_text: str, story_so_far: str, genre: str,
                      expectations: list | None = None, sofar_budget: int = 12000) -> dict | None:
    """이 회차를 막 읽은 장르 독자의 '이탈 행동 예측'. 점수 아님. 실패 시 None(비차단).

    DP-10 확정 원인: 구 스키마({got, pay_next, why})의 이진 bool 이 저온 RLHF 아첨과 만나 pay_next 98.7% 포화
    (78/79) → 신호 소멸. 스키마가 '끊을 지점을 원문으로 짚으라'고 시키지 않아서였다. 그래서 스키마를 교체한다:
      · drop           : 하차 여부(기본 True — hair-trigger 프레이밍의 결과값)
      · kill_trigger   : 이탈하고 싶어진 '바로 그 대목'을 본문에서 그대로 인용(없으면 '없음' 명시) — 인용 의무
      · hate_comment   : 이 화에 달릴 법한 신랄한 악플 한 줄(실제 댓글 말투)
      · retention_est  : 이 화를 끝까지 읽고 다음 화로 넘어갈 독자 비율 추정(0~100 정수 — 잔존 곡선 복원)
      · why            : 판단 이유 한 줄
    expectations: 장르 계약의 독자 기대(G5) — 무엇을 기대하는 장르인지 알고 판단(advisory, 강제 아님).
    sofar_budget: '지금까지 줄거리' 절단 예산(DP-15, config.reader_desk_sofar_chars). 초과 시 오래된 앞쪽을
      버리고 최신 꼬리를 보존한다(_tail_budget) — 기존 1,500 하드컷이 계층 요약 87%를 버려 최신 사건 부재로
      실재하지 않는 모순 지적(retention 오염)하던 결함 교정.
    이것은 '실독자'가 아니라 LLM 시뮬이다(advisory·비차단) — 작가 가시화용 참고 신호."""
    if not (chapter_text or "").strip():
        return None
    exp = ("\n이 장르 독자가 보통 기대하는 것: " + ", ".join(str(e) for e in expectations[:5])
           if expectations else "")
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              # hair-trigger 프레이밍(조사 §2): 손가락이 이미 '뒤로가기'에 얹혀 있다 — 한 문장만 지루해도 즉시 이탈.
              f"너는 {genre or '웹소설'} 유료 연재를 넘겨보는 독자다. 손가락은 이미 '뒤로가기'에 얹혀 있다 — "
              "웹소설은 한 문장만 루즈해도 즉시 창을 닫는 게 기본이고, 계속 읽는 건 예외다. "
              "칭찬·격려·편집자 시점 전부 금지. 읽다가 손이 멈칫하거나 이탈하고 싶어진 '바로 그 지점'을 냉정하게 짚어라 — "
              "감상평이 아니라 본문을 그대로 인용해서."
              f"{exp} "
              "JSON 으로만 답하라: "
              '{"drop":true,'
              '"kill_trigger":"이탈하고 싶어진 바로 그 대목의 본문을 그대로 한 구절 인용(따옴표 안에). 정말 없으면 \'없음\'",'
              '"hate_comment":"이 화에 달릴 법한 신랄한 악플 한 줄(실제 댓글 말투로)",'
              '"retention_est":30,'
              '"why":"하차/잔존 판단의 이유 한 줄"}'},
             {"role": "user", "content": f"[지금까지 줄거리]\n{_tail_budget(story_so_far, sofar_budget)}\n\n[이번 회차]\n{chapter_text}"}],   # 절단 전면 제거(2026-08-21): 구 9,000자 머리 절단 = 시뮬 독자가 회차말 훅을 못 읽던 사각
            temperature=0.3)
        kill = (r.get("kill_trigger") or "").strip()
        hate = (r.get("hate_comment") or "").strip()
        why = (r.get("why") or "").strip()
        if not kill and not hate and not why:
            return None
        # retention_est 정수 정규화(문자/실수/None/범위 방어 — 곡선 복원용 정수 보장)
        ret = r.get("retention_est")
        try:
            ret = max(0, min(100, int(round(float(ret)))))
        except (TypeError, ValueError):
            ret = None
        return {"drop": bool(r.get("drop", True)),   # 기본 하차(hair-trigger)
                "kill_trigger": kill or "없음",       # 인용 의무 — 미기재 시 '없음' 명시
                "hate_comment": hate,
                "retention_est": ret,
                "why": why}
    except Exception:
        return None
