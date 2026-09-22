# -*- coding: utf-8 -*-
"""RC-5 — 회차 요약 되먹임 증폭 차단(재요약 계약 재계약 + prompt-auditor F1/F2/F3). 실 LLM 0콜(캡처 provider).

되먹임 어트랙터의 요약 문면 실체 = 셈·세기 나열(수사+단위). 구 `_summarize` 계약의
"물리 디테일(자원·소지품·위치·시각) 열거" 지시가 수 기반 세계 장치 작품(원장 12번)에서
요약을 세기 나열체로 수렴시켜 story_so_far·syn_prev·메뉴 3경로로 재주입 → 다음 회차 재앵커.

잠그는 계약(프롬프트는 STRING 기준 — # 주석은 프롬프트에 들어가지 않는다):
① 소스 차단 — 구 계약의 열거 지시("물리 디테일(자원·소지품…)")가 재요약 system 프롬프트에서 사라짐.
② 긍정형 재계약 — 조직 축이 '무엇이 왜 일어났나'(사건 인과·인물 결정) 이고 oneliner 도 '무엇이 바뀌었나' 앵커.
③ F1(치명·감사) — 차단 대상 범주('자원 상태'·"값 자체가 정보인 수량·자원 상태") 호명 삭제. P-2 보존은
   범주 호명이 아니라 기능형 절("상태를 오인하지 않도록 필요한 값을 사실 그대로 보존")이 담당(정보 폐기 없음).
④ F2(중대·감사) — '본문 없이도 이어 쓸 밀도'(상한 없는 완전 재구성) → '사건 흐름을 정확히 이어받도록'으로 완화.
⑤ 핑크엘리펀트 — 재요약 프롬프트에 기피어·부정지시("~하지 마"·"금지"·"나열하지 말라" 류) 0.
⑥ 스키마·반환 시그니처 불변 — (oneliner, synopsis, degraded) 3튜플·충분 길이 산출 degraded=None·temperature 0.2.
⑦ F3ⓐ(중대·소비부) — 산출 synopsis 길이 하한(≥900) 미달 시 degraded 신호(폴백 계약과 정합·신뢰 강등).
⑧ F3ⓑ(중대·소비부) — 산출 synopsis 의 수사+단위 밀도 결정론 사후 계측 → advisory(bus) 관측(판정·임계 0).
⑨ 폴백(P-2) 무회귀 — LLM 실패 시 beat 합성 요지 폴백·degraded 반환(구 동작 유지).

실행: PYTHONPATH=app py -3.12 -m pytest tools/test_rc5_summary_recontract.py -q
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

from novelcopilot.engine.harness import ChapterGenerator, _SUMMARY_SYNOPSIS_FLOOR
from novelcopilot.domain.world import StyleSpec


# 충분 길이(≥900) 산출 — 정상 성공 경로(degraded=None)를 대표. "상세" 부분문자열 포함.
_LONG_SYN = "상세" * 500   # 1000자


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, node, event, **payload):
        self.events.append({"node": node, "event": event, **payload})


class CaptureProvider:
    """재요약 콜의 messages·temperature 를 기록만 하고 지정 응답 반환."""

    def __init__(self, response=None):
        self.captured = []
        self.temps = []
        self.last_truncated = False
        self._response = response if response is not None else {
            "oneliner": "진우가 문을 열고 대가를 치른다",
            "synopsis": _LONG_SYN,
        }

    def chat(self, messages, temperature=0.0, max_tokens=0):
        self.captured.append(messages)
        self.temps.append(temperature)
        return "본문."

    def chat_json(self, messages, temperature=0.0, max_tokens=0):
        self.captured.append(messages)
        self.temps.append(temperature)
        return self._response


def _gen(response=None):
    settings = SimpleNamespace(prev_chapter_context_chars=4000, craft_progress=False,
                               scene_style_anchor=False)
    prov = CaptureProvider(response)
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    return g, prov


def _summarize_sys():
    """재요약 system 프롬프트 문자열(캡처)."""
    g, prov = _gen()
    g._summarize("진우는 여섯 번째 하중을 확인했다. 모래주머니는 스물두 개였다.")
    return prov.captured[0][0]["content"], prov


# ① 소스 차단 — 구 계약의 열거 지시가 사라졌다
def test_old_enumeration_clause_removed():
    sys_msg, _ = _summarize_sys()
    assert "물리 디테일" not in sys_msg
    assert "자원·소지품" not in sys_msg
    assert "소지품" not in sys_msg
    assert "인물별 결정/감정 변화" not in sys_msg   # 구 문면(슬래시 열거) 자체도 대체


# ② 긍정형 재계약 — 사건 인과·인물 결정 축 + oneliner '무엇이 바뀌었나'
def test_positive_event_axis():
    sys_msg, _ = _summarize_sys()
    assert "무슨 일이 왜 일어났는지를 축으로" in sys_msg
    assert "사건의 인과 순서" in sys_msg
    assert "인물별 결정과 그 동기" in sys_msg
    assert "무엇이 바뀌었는지, 핵심 사건과 전환을 한 문장으로" in sys_msg   # oneliner 앵커


# ③ F1 — 차단 대상 범주 호명 삭제 + P-2 는 기능형 절이 담당
def test_f1_resource_category_not_named():
    sys_msg, _ = _summarize_sys()
    # 감사 F1: 재고 어트랙터 백도어가 되는 범주 호명이 프롬프트에서 사라졌다
    assert "값 자체가 정보인 수량·자원 상태" not in sys_msg
    assert "자원 상태" not in sys_msg
    assert "수량" not in sys_msg
    # P-2 보존은 기능형 절이 담당(정보 폐기 금지 — 값은 사실 그대로 보존)
    assert "상태를 오인하지 않도록 필요한 값을 사실 그대로 보존" in sys_msg
    assert "사실·사건 정보만" in sys_msg   # test_c4 와 정합


# ④ F2 — '본문 없이 이어 쓸 밀도' → '사건 흐름 이어받도록' 완화
def test_f2_density_clause_relaxed():
    sys_msg, _ = _summarize_sys()
    assert "본문 없이도 이어 쓸 수 있을 밀도" not in sys_msg          # 상한 없는 완전 재구성 요구 삭제
    assert "이 회차의 사건 흐름을 정확히 이어받도록" in sys_msg        # 완화된 계약


# ⑤ 핑크엘리펀트 — 기피어·부정지시 0(프롬프트 STRING 기준)
def test_no_negative_instructions():
    sys_msg, _ = _summarize_sys()
    for banned in ("하지 마", "지 마라", "말 것", "말라", "금지", "나열하지", "열거하지",
                   "쓰지 마", "담지 마"):
        assert banned not in sys_msg, f"부정지시 재유입(재요약 system): {banned!r}"


# ⑥ 스키마·반환 시그니처·온도 불변(충분 길이 산출 = degraded None)
def test_schema_and_temperature_unchanged():
    g, prov = _gen()
    out = g._summarize("본문이다.")
    assert isinstance(out, tuple) and len(out) == 3
    one, syn, degraded = out
    assert one == "진우가 문을 열고 대가를 치른다"
    assert "상세" in syn
    assert degraded is None                     # ≥900 산출 → 성공(무회귀)
    assert prov.temps[0] == 0.2                 # 재요약 온도 불변
    sys_msg = prov.captured[0][0]["content"]
    assert '{"oneliner":"","synopsis":""}' in sys_msg


# ⑦ F3ⓐ — 산출 길이 하한 게이트(짧은 산출 → degraded short, ≥900 → None)
def test_f3a_underlength_gate():
    # 짧은 synopsis(하한 미달) → 내용 보존 + degraded{short} + underlength 이벤트
    g, prov = _gen(response={"oneliner": "한줄", "synopsis": "짧은 요약."})
    one, syn, degraded = g._summarize("본문.")
    assert one == "한줄" and syn == "짧은 요약."          # 내용은 보존
    assert degraded is not None and degraded.get("degraded") is True
    assert degraded.get("failure") == "short"
    assert any(e["event"] == "underlength" and e.get("floor") == _SUMMARY_SYNOPSIS_FLOOR
               for e in g.bus.events)
    # 경계: 하한 이상이면 degraded None
    g2, _ = _gen(response={"oneliner": "한줄", "synopsis": "가" * _SUMMARY_SYNOPSIS_FLOOR})
    _, _, degraded2 = g2._summarize("본문.")
    assert degraded2 is None


# ⑧ F3ⓑ — 수사+단위 밀도 사후 계측 advisory(bus·판정 언어 0)
def test_f3b_counting_density_swept():
    # synopsis 에 수사+단위 토큰이 있으면 counting_density 이벤트에 결정론 계측됨
    syn = "세 갈래로 갈라진 길에서 다섯 시에 손거울 두 개를 확인했다. " + "상세" * 480
    g, _ = _gen(response={"oneliner": "한줄요약", "synopsis": syn})
    g._summarize("본문.")
    ev = [e for e in g.bus.events if e["event"] == "counting_density"]
    assert len(ev) == 1
    e = ev[0]
    assert e.get("unique_num_tokens", 0) >= 3        # '세 갈래'·'다섯 시'·'두 개'
    assert e.get("chars") == len(syn)
    assert isinstance(e.get("per_1k"), float)
    # 토큰 0 산출은 밀도 0.0(계측은 항상 돈다 — 판정·임계 없음)
    g2, _ = _gen(response={"oneliner": "한줄요약", "synopsis": "인과만 담은 서술. " + "상세" * 490})
    g2._summarize("본문.")
    e2 = [x for x in g2.bus.events if x["event"] == "counting_density"][0]
    assert e2.get("unique_num_tokens") == 0 and e2.get("per_1k") == 0.0


# ⑨ 폴백(P-2) 무회귀 — LLM 빈 응답 시 beat 합성 요지 + degraded
def test_fallback_unchanged():
    g, prov = _gen(response={"oneliner": "", "synopsis": ""})
    beat = {"title": "각성", "summary": "진우가 각성한다",
            "key_events": ["던전 진입", "첫 각성"]}
    one, syn, degraded = g._summarize("진우는 검을 뽑았다. " * 30, "", beat)
    assert "진우가 각성한다" in syn and "던전 진입" in syn
    assert "진우는 검을 뽑았다" not in syn
    assert degraded is not None and degraded.get("degraded") is True
    assert degraded.get("failure") == "empty"       # 빈 응답 경로(하한 게이트 아님)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"RC-5 검증: ALL GREEN ({len(fns)} tests)")
