# -*- coding: utf-8 -*-
"""SX-2 콜드리드 독자 축 — '프로즈만' 읽는 신규 독자 프로브(cross-vendor·advisory).

배경(4화 정독 실측): 기존 reader_desk(G2)는 '지금까지 줄거리 요약'을 입력으로 받아 실제 신규 독자와
다르다 — 요약이 콜드리드 혼란(설정이 본문 안에서 충분히 설명됐는가·인물/장치가 처음 보는 독자에게
이해되는가)을 메꿔 버려 이 사각을 못 본다(콜드리드 혼란 4~5건이 상존하는데 시뮬은 통과). 이 축의
존재 이유가 그 사각 해소이므로 입력은 **프로즈만**이다: 1화부터 현재 화까지 본문을 연결해 넘기고,
요약·설정·계획은 일절 주입하지 않는다(어셈블리 검사로 잠금 — build_cold_read_prose).

원칙(reader_desk 계보):
  · hair-trigger 기본값 — 기본=하차·원문 인용 의무(칭찬·편집자 시점 금지). 아첨 포화 방지.
  · 척도 명시(0~100) — 과거 프로브가 척도 누락으로 0~10 오답을 낸 실측이 있어 comprehension_0_100 은
    질문·스키마·정규화 셋 다 0~100 을 못박는다(재발 방지).
  · gen≠judge — 심사 모델은 make_judge 계보의 cross-vendor(프로즈가 anthropic 이면 openai) provider 를
    호출부가 주입한다(이 모듈은 provider 를 모른다 — 레이어 규율·reader_desk 와 동형).
  · advisory·비차단·무강제 — 회차 확정을 막지 않는다(작가 가시화용 참고 신호). 실독자 아님(LLM 시뮬).
  · 실패 시 None(비차단) — 호출부가 verification.cold_read 를 "미실행"으로 남긴다(결측 정직).
"""
from __future__ import annotations
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)


def build_cold_read_prose(chapter_texts: list[str], max_chars: int | None = None) -> dict:
    """1화~현재 화 본문을 연결한 '프로즈만' 입력을 조립한다(요약·설정·계획 일절 미포함 — 이 축의 존재 이유).

    chapter_texts: 1화부터 현재 화까지의 FINALIZED 본문(호출부가 chapter 오름차순으로 넘긴다).
    max_chars: 연결 프로즈 상한. 절단 전면 제거(2026-08-21)로 기본 None=무절단(전 회차 전문 — 구 40,000
      머리 유지는 16화 시점에 누적의 절반을 버려 신규 독자 프로브가 최근 회차를 아예 못 읽었다). 값을 주면
      종전대로 앞에서부터 유지·초과분 끝 절단 + truncated=True 정직 기록(opt-in 하위호환).

    반환: {"prose": 연결·절단된 본문, "truncated": bool, "chapters": 포함 회차 수, "chars": 실제 글자수}.
    본문이 전무하면 prose=""·chapters=0(호출부가 이 경우 프로브를 건너뛴다).
    """
    parts = [(t or "") for t in (chapter_texts or []) if (t or "").strip()]
    joined = "\n\n".join(parts)
    truncated = False
    if max_chars is not None and max_chars >= 0 and len(joined) > max_chars:
        joined = joined[:max_chars]      # 앞부분(도입) 유지·초과분 절단 — 신규 독자 이해 형성이 앞에서 온다
        truncated = True
    return {"prose": joined, "truncated": truncated,
            "chapters": len(parts), "chars": len(joined)}


@promptlog.stage("cold_read")
def cold_read_probe(provider, chapter_texts: list[str], genre: str = "",
                    max_chars: int | None = None) -> dict | None:
    """프로즈만 읽는 신규 독자의 이해도·혼란·궁금 목록·하차 위험(advisory). 실패 시 None(비차단).

    provider: cross-vendor 심사 provider(호출부가 make_judge 로 라우팅 — gen≠judge). 이 모듈은 provider 종류를 모른다.
    chapter_texts: 1화~현재 화 FINALIZED 본문(오름차순). build_cold_read_prose 로 프로즈만 연결·절단.
    genre: 장르 라벨(프레이밍용·advisory). max_chars: 프로즈 상한(기본 None=무절단 — 절단 전면 제거 2026-08-21).

    산출 JSON:
      · comprehension_0_100 : 처음 읽는 독자가 지금까지 얼마나 따라왔는가(0~100 정수 — 척도 명시·필수)
      · confusions          : [{what, quote}] — 이해 안 된 대목(원문 인용 의무)
      · curiosities         : [str] — 더 알고 싶어진 것(열린 궁금증 — 좋은 훅의 신호)
      · drop_risk           : 처음 읽는 독자가 이탈할 위험(기본 True — hair-trigger)
    반환: 위 필드 + truncated/chapters(입력 메타·정직 기록). 본문 전무·실패 시 None.
    """
    built = build_cold_read_prose(chapter_texts, max_chars=max_chars)
    prose = built["prose"]
    if not prose.strip():
        return None
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              # hair-trigger + 콜드리드 프레이밍: 요약·설정 없이 '본문만' 처음 읽는 독자 — 설정이 본문 안에서
              #   충분히 서지 않으면 즉시 혼란·이탈한다. 척도(0~100)를 질문에서 못박아 0~10 오답 재발을 막는다.
              f"너는 {genre or '웹소설'}을 지금 처음 펼친 신규 독자다. 줄거리 요약도, 설정집도, 작가 노트도 없다 — "
              "네가 가진 건 1화부터 지금까지의 '본문'뿐이다. 설정·인물·장치가 본문 안에서 충분히 서지 않으면 "
              "너는 즉시 헷갈리고, 헷갈리는 채로 두 번 이상 넘어가면 창을 닫는다. 칭찬·격려·편집자 시점 전부 금지 — "
              "처음 읽는 사람으로서 '무엇이 이해되고 무엇이 안 되는지'를 냉정하게, 본문을 그대로 인용해서 짚어라. "
              "JSON 으로만 답하라(comprehension_0_100 은 0~100 사이 정수다 — 0~10 이 아니다): "
              '{"comprehension_0_100":70,'
              '"confusions":[{"what":"이해 안 된 것 한 줄","quote":"그 대목의 본문을 그대로 한 구절 인용"}],'
              '"curiosities":["처음 읽는 독자로서 더 알고 싶어진 것 한 줄"],'
              '"drop_risk":true}'},
             {"role": "user", "content": f"[본문(1화부터 현재까지 — 이게 전부다)]\n{prose}"}],
            temperature=0.3)
    except Exception:
        return None
    if not isinstance(r, dict):
        return None
    # comprehension 0~100 정수 정규화(문자/실수/None/범위 방어 — 척도 보장)
    comp = r.get("comprehension_0_100")
    try:
        comp = max(0, min(100, int(round(float(comp)))))
    except (TypeError, ValueError):
        comp = None
    # confusions: [{what, quote}] 정규화(원문 인용 의무 — 빈 항목 제거)
    confusions = []
    for c in (r.get("confusions") or []):
        if isinstance(c, dict):
            what = (c.get("what") or "").strip()
            quote = (c.get("quote") or "").strip()
            if what or quote:
                confusions.append({"what": what, "quote": quote})
        elif isinstance(c, str) and c.strip():
            confusions.append({"what": c.strip(), "quote": ""})
    curiosities = [str(x).strip() for x in (r.get("curiosities") or []) if str(x).strip()]
    if comp is None and not confusions and not curiosities:
        return None                       # 아무 신호도 없음(파싱 실패 근사) — 미실행으로 정직 강등
    return {"comprehension_0_100": comp,
            "confusions": confusions,
            "curiosities": curiosities,
            "drop_risk": bool(r.get("drop_risk", True)),   # 기본 하차(hair-trigger)
            "truncated": built["truncated"],               # 입력 절단 여부(정직 기록)
            "chapters": built["chapters"]}                 # 프로즈에 포함된 회차 수(입력 메타)
