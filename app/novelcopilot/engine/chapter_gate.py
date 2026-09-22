# -*- coding: utf-8 -*-
"""회차 정독 게이트 엔진 부품 — 러너(gen_gated)와 제품 경로(generate_next_chapter) 공용 SSOT.

배경(design-pr1-pr2-product-gate.md §PR-1): 매화 정독 게이트 루프(심사→fix_note→국소 revise/regen→
최선 보존)는 DP-5 러너(tools/gen_gated.py)에만 있었다. PR-1 은 그 루프를 웹 "다음 화 생성"에도 옵션으로
제공한다 — 두 경로가 같은 부품을 쓰도록 게이트의 tools-독립 코어를 engine 으로 승격 이동한다(이중 구현
소멸). tools 의존부(dp4b 계측·st11 스팬·gen_longrun 러너 골격)는 gen_gated 에 남고, 이 코어에 measure_fn·
rhythm_fn(호출부 주입)으로 관통한다 — 엔진은 tools 를 import 하지 않는다(레이어 규율).

여기에 이관되는 것(DP-21 §2~§3 계약 그대로):
  · CallBudget/LLMBudgetExceeded — run 당 심사 콜 상한(비용 하드 가드).
  · anchor_drop_trigger — drop_trigger(심사 원문 인용) 본문 앵커 → 국소 revise span 산출(정확 substring+공백
    정규화 1단계만·교착어 fuzzy 금지). 실패면 None → regen 폴백.
  · gen_step/gate_committed_chapter/gate_one_chapter — 생성(step)·게이트(계측→수리→심사→FAIL시 국소/regen)
    루프. DP-21 국소 revise 라우팅·undo 최선 보존·R 상한·콜 예산·gate_rounds 영속 포함.
  · GATE_SYSTEM/llm_gate/make_judge/_measure_digest/_layer_digest — cross-vendor 정독 심사(hair-trigger +
    measure-then-cite). make_judge 만 llm.factory(엔진 계층 안)를 import 한다.

무강제 불변식(러너·제품 공통): OFF 기본(제품) · fix_note 재생성은 R 상한 · 사실 불변 revise 우선 ·
  은폐 없는 기록(gate_rounds·fail_exhausted 표면화) · hair-trigger 불변. DP-5/DP-21 에서 검증된 계약 그대로.
"""
from __future__ import annotations
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)

import inspect
import re
import time
import uuid

# 게이트 파라미터(설계 §2·§3 등록값) — gen_gated 가 이 상수를 재노출(러너 CLI 기본값 불변).
GATE_MAX_RETRIES = 2          # R — FAIL 시 fix_note 재생성 상한(재시도. 최초 생성 제외)
FAIL_ABORT_STREAK = 3         # 연속 FAIL-소진 3화 = arm 중단(kill criteria)
DEFAULT_LLM_CALL_CAP = 60     # run 당 LLM 게이트 콜 상한 기본값(비용 가드 — CLI --llm-cap 로 조정)
RHYTHM_RUN_THRESHOLD = 6      # 리듬 스팬 검출 임계(dp4b/ST-11 기본과 동일 — '~다' run≥6)


# ─────────────────────────────────────────────────────────────────────────────
# LLM 콜 예산 — run 당 상한. 초과 시 LLMBudgetExceeded 를 던져 arm 을 즉시 중단시킨다.
# ─────────────────────────────────────────────────────────────────────────────
class LLMBudgetExceeded(RuntimeError):
    pass


class CallBudget:
    """LLM 게이트 콜 카운터(run 전역 공유). charge() 가 상한 초과 시 예외 — 비용 하드 가드.
    게이트 콜만 계상한다(gen/regen 은 하네스 usage 로 별도 계측 — 여기 상한은 '심사' 예산)."""

    def __init__(self, cap: int):
        self.cap = int(cap)
        self.used = 0

    def charge(self, n: int = 1) -> None:
        self.used += int(n)
        if self.cap >= 0 and self.used > self.cap:
            raise LLMBudgetExceeded(
                f"LLM 게이트 콜 상한 초과: used={self.used} > cap={self.cap} (run 중단 — 비용 가드)")


# ─────────────────────────────────────────────────────────────────────────────
# LLM 정독 게이트(cross-vendor) — hair-trigger 프로토콜 + dp4b measure-then-cite.
# ─────────────────────────────────────────────────────────────────────────────
# dp4b 게이트 체크리스트(measure-then-cite) — 계측 원자료를 주고 '읽고 나서 인용으로 확증'시킨다.
GATE_SYSTEM = (
    "너는 한국 웹소설 유료 연재를 넘겨보는 독자다. 손가락은 이미 '뒤로가기'에 얹혀 있다 — "
    "한 문장만 루즈해도 즉시 창을 닫는 게 기본이고, 계속 읽는 건 예외다. "
    "칭찬·격려·편집자 시점 전부 금지. **먼저 이 회차를 닫을 이유부터 찾아라.** "
    "판정은 반드시 '읽으면서' 하고, 짚는 대목은 감상이 아니라 본문을 그대로 인용해서 확증하라.\n"
    "정독 체크리스트(measure-then-cite — 아래 계측 수치는 참고일 뿐, 판정은 정독으로):\n"
    " ① 파편문/스타카토가 도입부에 밀집해 숨이 막히는가(있으면 대목 인용)\n"
    " ② [발화 층위 단일성] 연속 10문장에서 발화 층위가 하나뿐인가 — 지문(사건 서술)만 계속되고 대사·인용·화자의 "
    "입말 생각·짧은 의문·문서/화면/소리 인용이 그 사이에 끼어들지 않으면 층위가 단일한 벽 구간이다(있으면 그 구간 인용). "
    "같은 어미가 이어져도 층위가 섞이면 벽이 아니다 — 보는 건 어미 run 길이가 아니라 층위 단일성이다\n"
    " ③ 회차 안에서 같은 정보를 재설명·되풀이하는가(있으면 두 대목 인용)\n"
    " ④ 직전 회차와 오프닝/구조가 재탕인가(있으면 인용)\n"
    " ⑤ 시점(POV) 위반·설정 모순·시간/숫자 역행이 있는가(있으면 인용)\n"
    " ⑥ 장르 계약(사이다·긴장·다음 화 욕구)이 이 회차에서 실제로 '터지는가'\n"
    "verdict 는 행동을 발동한다 — 그 문턱을 지켜라(기준을 낮추라는 게 아니라, 신호를 변별하라는 것이다):\n"
    " · verdict=FAIL 은 **네가 실제로 창을 닫을 하차 트리거가 실재할 때만**. 그 대목의 본문을 그대로 "
    "drop_trigger 에 인용해 확증하라(인용 못 하면 FAIL 아님 — 감상만으로 FAIL 금지). '닫을 이유 먼저 찾기'는 "
    "그대로다. 바뀌는 건 '찾은 이유가 정말 창을 닫게 만드는 수준인가'뿐이다.\n"
    " · 다듬기·압축·케이던스 정리 수준의 개선 지적은 verdict=PASS 로 두고 fix_note 에 담아라(기록·후속 국소 "
    "수리 재료로 쓰인다 — 버려지지 않는다). 읽히긴 하는데 아깝다 = PASS + fix_note.\n"
    "fix_note 는 국소 수리가 바로 고칠 수 있게 '어느 스팬(원문 한 구절 인용)을 어떻게'로 구체적으로. FAIL 이든 "
    "PASS 든 개선 여지가 있으면 남겨라(PASS 면 drop_trigger 는 '없음').\n"
    "JSON 객체로만 답하라: "
    '{"verdict":"PASS 또는 FAIL",'
    '"drop_trigger":"이탈하고 싶어진 바로 그 대목의 본문을 그대로 한 구절 인용(따옴표 안에). 정말 없으면 \'없음\'",'
    '"fix_note":"국소 수리에 넘길 구체 수정 지시(스팬 지목·원문 한 구절 인용). PASS라도 다듬을 여지가 있으면 남겨라(버려지지 않는다). 정말 없으면 빈 문자열",'
    '"hate_comment":"이 화에 달릴 법한 신랄한 악플 한 줄(실제 댓글 말투)",'
    '"retention_est":30}'
)


def _layer_digest(kiwi: dict) -> str:
    """ST-9 ⓒ: kiwi 축(ending_profile=종결 어미 원자료 + layer=발화 층위 원자료)을 digest 한 줄로 병기.

    종결 어미 압축률·최장 run 은 '어미 변주' 축, 대사 비중·무대사 지문 연속 run 은 '층위 단일성' 축 —
    벽 판정은 후자다. 둘을 나란히 줘 정독이 run 길이(무해할 수 있음)와 층위 벽(진짜 벽)을 변별하게 한다.
    전부 advisory(임계·판정 아님). 결측(구 gate·kiwi 실패)은 조용히 생략(하위호환)."""
    if not isinstance(kiwi, dict) or kiwi.get("error"):
        return ""
    parts = []
    ep = kiwi.get("ending_profile") or {}
    if isinstance(ep, dict) and not ep.get("error") and ep.get("n_ending"):
        parts.append(f"종결압축={ep.get('compression')}(최장run {ep.get('max_run')}"
                     f"·최빈 {ep.get('top_ratio')})")
    ly = kiwi.get("layer") or {}
    # 결측(구 gate JSON: layer 키 없음)·실패는 조용히 생략 — 실제 값이 있을 때만 병기(하위호환).
    if isinstance(ly, dict) and not ly.get("error") and ly.get("dialogue_para_ratio") is not None:
        parts.append(f"대사문단비={ly.get('dialogue_para_ratio')} "
                     f"무대사지문연속={ly.get('max_narration_run')}")
    return ("층위원자료[" + " ".join(parts) + "]") if parts else ""


def _measure_digest(meas: dict) -> str:
    """계측 원자료를 심사문에 줄 요약(measure-then-cite 재료 — 판정은 정독).

    DP-19: 재탕 계수가 고계수(0.30은 digest 상단 배치 위치 참조일 뿐 — 자동 판정 아님)이고 근거 문장이
    있으면 digest 맨 위에 '재탕 의심 대목: [현재화 문장]'을 강조해 정독이 그 대목부터 대조하게 한다."""
    if not isinstance(meas, dict) or meas.get("error"):
        return "(계측 없음)"
    rt = meas.get("retread") or {}
    ao = meas.get("active_open") or {}
    body = (f"nws={meas.get('nws')} 밴드ok={meas.get('nws_band_ok')} "
            f"능동개시={ao.get('active')}(agency={ao.get('agency')}) "
            f"재탕계수={rt.get('opening_coef')} keyev재탕={rt.get('keyevents_overlap_prev')} "
            f"drift={meas.get('drift_signals')}")
    # ST-9 ⓒ: 종결(어미) 원자료 옆에 발화 층위 원자료를 병기 — 벽 판정 축은 run 길이가 아니라 층위 단일성이라
    #   정독이 두 축을 나란히 봐야 한다(수치는 measure-then-cite 재료 — 자동 판정 아님·advisory).
    lyr = _layer_digest(meas.get("kiwi") or {})
    if lyr:
        body += " " + lyr
    ev = rt.get("evidence") or {}
    cur_sent = (ev.get("cur_sentence") or "").strip()
    # 강조 게이트는 인용문·직전화 지목을 낳은 그 축의 계수(evidence.prev_coef, 직전 2화 파생)로 발화한다.
    # 전역 최댓값 opening_coef(먼 화일 수 있음)로 게이트하면 강조가 다른 회차로 오귀속된다(축 불일치).
    # 0.30 은 배치 위치 참조일 뿐 — 자동 판정 아님. prev_coef 부재(구 프로젝트)면 opening_coef 로 하위호환 폴백.
    gate_coef = ev.get("prev_coef")
    if gate_coef is None:
        gate_coef = rt.get("opening_coef")
    if cur_sent and (gate_coef or 0) >= 0.30:
        pc = ev.get("prev_chapter")
        lead = f"재탕 의심 대목: “{cur_sent}” (직전 {pc}화 재인스턴스 의심 — 정독으로 대조)"
        return lead + "\n" + body
    return body


_SLOT_RE = re.compile(r"^\[(\d+)화차\]\s*")


def slot_event(required_events, slot: int) -> str:
    """EB-3 결정론 보조 — [N화차] 접두 필수사건에서 이 화차 몫을 꺼낸다. 분해 없는 스파인이면 ''(하위호환)."""
    for evt in (required_events or []):
        m = _SLOT_RE.match(str(evt).strip())
        if m and int(m.group(1)) == int(slot):
            return _SLOT_RE.sub("", str(evt).strip())
    return ""


def slot_central(episode, slot: int) -> str:
    """RC-1: 회차 예산 분배(episode.slots)에서 이 화차의 중심 사건을 꺼낸다.
    미분배(빈 slots·플래그 OFF·구 JSON)면 ''(호출부가 slot_event [N화차] 폴백 — 바이트 동일)."""
    for sp in (getattr(episode, "slots", None) or []):
        if int(getattr(sp, "slot", 0) or 0) == int(slot):
            return (getattr(sp, "central", "") or "").strip()
    return ""


def planned_event_for(state, chapter_no: int) -> str:
    """EB-3 — 이 회차가 소진해야 할 설계 사건(스파인 [N화차] 분해 기준). 미분해·미배정이면 ''(주입 0·바이트 불변).

    화차 = 같은 에피소드에서 이 회차보다 앞선 회차 수 + 1. 존재 이유: 앞 화들이 에피 사건을 다 소진하면
    마지막 화가 굶는데(구 12화 무사건 화 실측) 게이트 루브릭에 그 감시 축이 없었다."""
    ch = next((c for c in getattr(state, "chapters", []) or []
               if getattr(c, "chapter", None) == chapter_no), None)
    ep_id = getattr(ch, "episode_id", "") if ch else ""
    if not ep_id:
        return ""
    slot = 1 + sum(1 for c in state.chapters
                   if getattr(c, "episode_id", "") == ep_id and getattr(c, "chapter", 0) < chapter_no)
    spine = getattr(getattr(state, "world", None), "spine", None)
    for arc in (getattr(spine, "arcs", None) or []):
        for ep in (getattr(arc, "episodes", None) or []):
            if getattr(ep, "episode_id", "") == ep_id:
                # RC-1: 슬롯 분배가 있으면 슬롯 중심 사건, 없으면 [N화차] 접두 폴백(미분배 시 바이트 동일).
                return slot_central(ep, slot) or slot_event(getattr(ep, "required_events", None), slot)
    return ""


@promptlog.stage("gate_judge")
def llm_gate(judge, chapter_text: str, meas: dict, story_so_far: str = "",
             genre: str = "", *, planned_event: str = "") -> dict:
    """cross-vendor LLM 정독 게이트 1콜. 산출 {verdict, drop_trigger, fix_note, retention_est, ...}.
    judge = LLMProvider(다른 벤더). 실패/파싱불가는 예외로 올려 러너가 재시도 정책으로 흡수.

    planned_event(EB-3): 이 화의 설계 사건이 있으면 대조 문항을 사용자 메시지에만 덧붙인다 — verdict 를
    바꾸지 않는 advisory 진단(자동 재생성 발동 금지 — VI-1 kill criteria ⓐ). 비면 프롬프트 바이트 동일."""
    user = (f"[작품] {genre or '웹소설'}\n"
            f"[계측 원자료(참고 — 판정은 정독으로)] {_measure_digest(meas)}\n\n"
            f"[지금까지 줄거리]\n{story_so_far or ''}\n\n"
            f"[방금 읽은 회차 전문]\n{chapter_text or ''}")   # 절단 전면 제거(2026-08-21): 구 truncate=15000 소거 — 줄거리는 상류 예산(12k)이, 회차는 전문이 계약
    if planned_event:
        user += ("\n\n[설계 사건 대조(참고 — verdict 를 바꾸지 않는다)] 이 화가 소진해야 할 설계 사건: "
                 f"「{planned_event}」\n이 사건(갈등·선택·상태 변화)이 본문에서 실제로 일어났는지 정독으로 "
                 "확인하라. 일어나지 않았거나 절반만 일어났으면 fix_note 맨 앞에 '설계 사건 미소진: '으로 "
                 "시작하는 진단을 본문 인용과 함께 남겨라.")
    r = judge.chat_json([{"role": "system", "content": GATE_SYSTEM},
                         {"role": "user", "content": user}],
                        temperature=0.0)
    verdict = str(r.get("verdict") or "").strip().upper()
    if verdict not in ("PASS", "FAIL"):
        # 방어적 정규화: 'FAIL' 부재 시 보수적으로 PASS 취급하지 않고 텍스트 신호로 판정
        verdict = "FAIL" if "FAIL" in verdict else ("PASS" if "PASS" in verdict else "FAIL")
    drop = (r.get("drop_trigger") or "").strip()
    fix = (r.get("fix_note") or "").strip()
    ret = r.get("retention_est")
    try:
        ret = max(0, min(100, int(round(float(ret)))))
    except (TypeError, ValueError):
        ret = None
    return {"verdict": verdict, "drop_trigger": drop or "없음",
            "fix_note": fix, "retention_est": ret,
            "hate_comment": (r.get("hate_comment") or "").strip()}


def make_judge(settings, prose_provider_name: str):
    """gen≠judge cross-vendor 심사 provider — prose 벤더와 '다른' 벤더로 라우팅(create_role_provider 재사용).
    prose 가 anthropic 계열이면 openai(config style_judge_model — 스펙 이중화 금지), 아니면 anthropic 로. 라우팅 실패는 폴백.

    llm.factory 는 엔진과 같은 패키지 계층(tools 아님)이라 import 가능 — 레이어 규율 위배 없음."""
    from ..llm.factory import create_role_provider
    prose = (prose_provider_name or "").lower()
    if "anthropic" in prose or "claude" in prose:
        # 현행 교차 벤더 심사 모델(config SSOT — style_judge_model)을 따른다. 구 하드코딩
        # 'gpt-5.2-chat-latest' 는 모델 소멸(404) 후 모든 정독 게이트가 '판정 불가 → 보수 기각'으로
        # 조용히 죽는 사고를 냈다(2026-08-11 재실현 3연속 기각 실측) — 스펙 이중화 금지.
        spec = (getattr(settings, "style_judge_model", "") or "").strip() or "openai:gpt-5.6"
        if not (spec.startswith("openai:") or spec.startswith("gemini:")):
            spec = "openai:gpt-5.6"   # gen≠judge: prose 가 anthropic 계열이면 심사는 반드시 타 벤더
    else:
        spec = "anthropic:claude-opus-4-8"
    return create_role_provider(settings, spec), spec


# ─────────────────────────────────────────────────────────────────────────────
# 게이트 코어 헬퍼 — 하네스(gen)+judge_fn+fix_fn+measure_fn+rhythm_fn 주입(전부 모의 가능 → LLM 0콜).
#   measure_fn/rhythm_fn 은 tools-바운드(dp4b/st11)라 호출부(gen_gated 러너·제품 경로)가 주입한다 —
#   엔진은 그 구현을 모른다(레이어 규율). 미주입 시 계측 없음(빈 dict)·수리 없음([])으로 안전 강등.
# ─────────────────────────────────────────────────────────────────────────────
def _positional_arity(fn) -> int:
    """judge_fn 이 받을 수 있는 위치 인자 수(VAR_POSITIONAL 이면 넉넉히 3). ctx 전달 여부 판별용."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return 2
    n = 0
    for pr in params:
        if pr.kind == inspect.Parameter.VAR_POSITIONAL:
            return 3
        if pr.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            n += 1
    return n


def _chapter_text(harness, pid: str, chapter: int) -> str:
    st = harness.load(pid)
    c = next((x for x in st.chapters if x.chapter == chapter), None)
    return (getattr(c, "text", "") or "") if c is not None else ""


def _norm_ws(s: str) -> str:
    """공백류를 단일 공백으로 접고 양끝 정리(정확 substring 실패 시의 1단계 정규화만 — 교착어 fuzzy 금지)."""
    return re.sub(r"\s+", " ", s or "").strip()


def _split_paragraphs(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    """빈 줄(\\n\\s*\\n) 경계로 문단 분리 — (문단리스트, 각 문단의 [start,end) 오프셋). 경계 없으면 전체가 1문단.
    오프셋은 원문 기준(구분자 포함 구간을 인접 문단에 흡수시키지 않고 문단 텍스트 경계만) — span 슬라이스가 원형 유지."""
    seps = list(re.finditer(r"\n[ \t]*\n", text))
    paras: list[str] = []
    spans: list[tuple[int, int]] = []
    start = 0
    for m in seps:
        paras.append(text[start:m.start()])
        spans.append((start, m.start()))
        start = m.end()
    paras.append(text[start:])
    spans.append((start, len(text)))
    return paras, spans


def anchor_drop_trigger(text: str, drop_trigger: str) -> dict | None:
    """DP-21 ⓐ 국소 경로 앵커 — drop_trigger(심사자 원문 인용)가 본문에 실재하는지 정확 substring 으로 확인하고,
    앵커되면 revise 에 넘길 span(앵커 문단 ±1문단 창)을 산출한다. 실패(부재/모호/구조적 지적)면 None → regen 폴백.

    매칭 규율(정확 substring + 공백 정규화 1단계만 — 교착어 fuzzy 매칭 금지):
      1) 정확 부분문자열이 딱 1회면 그 자리를 앵커(가장 흔한 빠른 경로).
      2) 아니면 본문·트리거의 공백류를 단일 공백으로 접고(_norm_ws) 그 정규화면에서 딱 1회면 앵커.
         (렌더 복사로 개행/공백만 달라진 인용 흡수 — 어간·조사 무관 fuzzy 는 하지 않는다.)
      3) 0회 또는 2회 이상(모호)이면 None — 러너가 regen 으로 폴백(이중 폴백 없음).

    반환(성공): {'anchor': <본문에 실재하는 원형 구절>, 'span_text': <앵커 문단 ±1 창 원문>, 'para_idx': i}.
    span_text 는 copilot.revise_chapter 의 span 검증(_CG._find_span 정확 1회 매칭)을 통과하도록 '원문 구절 그대로'.
    """
    dt = (drop_trigger or "").strip().strip("“”\"'").strip()
    if not dt or dt in ("없음", "none", "None"):
        return None
    if not text:
        return None
    # 1) 정확 substring 1회
    idx = None
    if text.count(dt) == 1:
        idx = text.index(dt)
    else:
        # 2) 공백 정규화 1단계 — 정규화면에서 1회면, 원문에서 해당 위치를 역추적.
        ntext, ndt = _norm_ws(text), _norm_ws(dt)
        if not ndt or ntext.count(ndt) != 1:
            return None
        # 원문에서 개행/공백만 다른 구절을 \s+ 패턴으로 역추적(정확 1회일 때만 앵커).
        pat = re.compile(r"\s+".join(re.escape(tok) for tok in ndt.split(" ") if tok), re.DOTALL)
        ms = list(pat.finditer(text))
        if len(ms) != 1:
            return None
        idx = ms[0].start()
    # 문단 분리(빈 줄 경계) — 앵커가 속한 문단 인덱스와 ±1 창을 원문 그대로 슬라이스.
    paras, spans = _split_paragraphs(text)
    hit = next((i for i, (s, e) in enumerate(spans) if s <= idx < e), None)
    if hit is None:
        return None
    lo, hi = max(0, hit - 1), min(len(paras) - 1, hit + 1)
    span_text = text[spans[lo][0]:spans[hi][1]]
    return {"anchor": dt if text.count(dt) == 1 else text[idx:idx + len(dt)],
            "span_text": span_text, "para_idx": hit}


def _apply_fix_spans(fix_fn, harness, pid: str, chapter: int, text: str, rhythm_fn) -> dict | None:
    """리듬 스팬이 있으면 fix_spans(v2) 로 국소 재작성 시도. fix_fn=None 이면 스킵.
    rhythm_fn(text)->spans 는 호출부 주입(tools st11 계열 — 엔진은 구현 모름). 미주입/None 이면 스팬 0(수리 없음).
    반환: {'spans': n, 'applied': bool} 또는 None(스팬 없음/스킵)."""
    spans = rhythm_fn(text) if callable(rhythm_fn) else []
    if not spans:
        return None
    if fix_fn is None:
        return {"spans": len(spans), "applied": False}
    res = fix_fn(harness, pid, chapter, text, spans)
    return {"spans": len(spans), "applied": bool(res)}


def _regen(harness, pid: str, fix_note: str) -> dict | None:
    """fix_note 재생성 — harness.regen(pid,fix) 우선, 없으면 step(pid,fix=...) 폴백(dp4b cmd_regen 계보)."""
    if hasattr(harness, "regen"):
        return harness.regen(pid, fix_note)
    try:
        return harness.step(pid, fix=fix_note)          # 시그니처 지원 하네스
    except TypeError:
        return harness.step(pid)                          # 최소 계약(재생성=재시도 step)


def _llm_call_count(harness, pid: str) -> int | None:
    """P-8: 하네스가 노출하면 세션 provider 의 누적 chat_calls(정수)를, 아니면 None(계측 불가 — 드라이런)."""
    fn = getattr(harness, "llm_call_count", None)
    if not callable(fn):
        return None
    try:
        n = fn(pid)
        return int(n) if isinstance(n, (int, float)) else None
    except Exception:
        return None


def _revise(harness, pid: str, chapter: int, directive: str, span_text: str) -> dict | None:
    """DP-21 ⓐ 국소 경로 — harness.revise(pid,ch,directive,span)→accept 로 회차 본문을 스팬 국소 퇴고(사실 불변).
    반환: {'changed': bool, 'accepted': bool, 'guardrail_passed': bool, 'llm_calls': int} 또는 None(하네스 미지원).

    revise_chapter 는 후보만 만들고(저장 안 함), accept_revision 이 가드레일 재검증 후 본문 교체·이력 push.
    · changed=False(revise 무변경 — 보수 편향)      → accept 안 함(국소 실패 신호 → regen 폴백).
    · 가드레일 미통과(accept ValueError)             → 국소 실패 신호(regen 폴백).
    · accept 성공                                    → 본문 in-place 교체(커서 불변, RAG/요약 정합).

    P-8: 라이브 revise/accept 가 실제 소비한 LLM 콜 수를 revise+accept 전후 chat_calls 델타로 계측해
    'llm_calls' 로 반환한다(호출부가 CallBudget 에 계상 — DP-21 국소 경로 비용 가드 정합). LLM 콜은 무변경·
    가드레일 불통과 경로에서도 이미 소비되므로(revise_prose·check_text 는 그 판정 전에 실행됨) 모든 실패 경로도
    소비분을 계상한다. 계측 불가(드라이런 — 훅 없음/None)면 llm_calls=0 → 모의 경로 산술 불변."""
    if not hasattr(harness, "revise") or not hasattr(harness, "accept"):
        return None
    calls_before = _llm_call_count(harness, pid)

    def _spent() -> int:
        # revise+accept 전후 누적 콜 델타(둘 다 측정 가능할 때만). 계측 불가면 0(드라이런).
        after = _llm_call_count(harness, pid)
        if calls_before is None or after is None:
            return 0
        return max(0, after - calls_before)

    try:
        rv = harness.revise(pid, chapter, directive, span_text)
    except ValueError:
        # 라이브 revise_chapter 의 span_not_found(창이 본문에 모호·부재) 등 — 국소 실패로 흡수해
        #   호출부가 regen 1회 폴백하도록(예외를 arm 중단으로 번지게 하지 않음 — 이중 폴백 아님).
        return {"changed": False, "accepted": False, "guardrail_passed": False, "llm_calls": _spent()}
    if rv is None:                                   # 423(생성 중) 등 — 국소 경로 포기
        return {"changed": False, "accepted": False, "guardrail_passed": False, "llm_calls": _spent()}
    if not rv.get("changed"):                        # revise 무변경(보수 폴백) → 국소 실패
        return {"changed": False, "accepted": False,
                "guardrail_passed": bool((rv.get("guardrail") or {}).get("passed")),
                "llm_calls": _spent()}
    if not (rv.get("guardrail") or {}).get("passed"):  # 후보 가드레일 불통과 → accept 안 함
        return {"changed": True, "accepted": False, "guardrail_passed": False, "llm_calls": _spent()}
    try:
        ac = harness.accept(pid, chapter, rv.get("revision_id"),
                            after_text_fb=rv.get("after_text"), span_text_fb=span_text)
    except ValueError:                               # 서버 가드레일 재검증 실패(409 계열) → 국소 실패
        return {"changed": True, "accepted": False, "guardrail_passed": False, "llm_calls": _spent()}
    accepted = bool(ac and ac.get("accepted"))
    return {"changed": True, "accepted": accepted, "guardrail_passed": True, "llm_calls": _spent()}


def _undo(harness, pid: str, chapter: int) -> bool:
    """DP-21 ⓑ 최선 판본 보존 — 국소 라운드가 retention 을 악화시켰으면 마지막 채택 퇴고를 정식 복원(undo_revision).
    text·summary·RAG 정합으로 되돌린다. 하네스 미지원이면 False(복원 불가 — 호출부가 최선 판본 판단)."""
    if not hasattr(harness, "undo"):
        return False
    try:
        r = harness.undo(pid, chapter)
        return bool(r and r.get("reverted"))
    except ValueError:                               # 되돌릴 이력 없음 등 — 복원 불가
        return False


def _story_so_far(state, chapter: int) -> str:
    """직전까지 회차 요약을 이어붙인 '지금까지 줄거리'(심사 컨텍스트 — 읽기 전용)."""
    chs = sorted((c for c in state.chapters if getattr(c, "chapter", 0) < chapter),
                 key=lambda c: c.chapter)
    return "\n".join(f"{c.chapter}화: {(getattr(c, 'summary', '') or '').strip()}" for c in chs)


# ─────────────────────────────────────────────────────────────────────────────
# 게이트 단계 — gen(step)·게이트(계측→수리→심사→국소/regen). guard_event 는 호출부 주입(러너는
#   gen_longrun.guard_event, 제품은 engine 로컬 이벤트 어댑터) — 이벤트 형식을 엔진이 고정하지 않는다.
# ─────────────────────────────────────────────────────────────────────────────
def _default_event(guard: str, chapter: int, action: str, detail: str) -> dict:
    """게이트 이벤트 기본 형식(gen_longrun.guard_event 와 동형 키). 러너는 자기 guard_event 를 주입한다."""
    return {"guard": guard, "chapter": chapter, "action": action, "detail": detail}


def gen_step(*, harness, pid: str, rec_status_fn=None, event_fn=None) -> dict:
    """회차 생성 단계(step)만 — 커밋 이전의 취약 구간. 예외 시 회차 미커밋(재시도 안전).

    rec_status_fn(res)->status 는 호출부 주입(러너=gen_longrun._rec_status). 미주입 시 res['status'] 폴백.

    반환:
      {'completed': True, ...}                         작품 완료 신호(상위로 전달)
      {'status':'ESCALATED', 'chapter':n, ...}         생성 실패(게이트 콜 없이 상위 반환)
      {'status':<st>, 'chapter':n, 'committed': True}  회차 커밋됨 → 게이트 단계로

    ★HIGH 수리(멱등성): step 은 여기서 '단 한 번'만 호출된다. step 이 예외를 던지면
    회차는 아직 커밋되지 않았으므로(harness.step 은 save 이전에 raise) 상위가 이 함수 전체를
    안전히 1회 재시도할 수 있다. 그러나 step 성공 이후의 계측/수리/심사 예외는 gate_committed_chapter
    쪽에서만 재시도되어 step 을 다시 부르지 않는다 — 미심사 회차 중복 생성/커서 부풀림 차단.
    """
    events: list[dict] = []
    res = harness.step(pid)
    if res.get("completed"):
        return {"completed": True, "reason": str(res.get("reason") or "completed"),
                "events": events}
    status = rec_status_fn(res) if callable(rec_status_fn) else str(res.get("status") or "")
    st = harness.load(pid)
    chapter = st.current_chapter
    if status == "ESCALATED":
        return {"completed": False, "status": "ESCALATED", "chapter": chapter,
                "verdict": None, "retries": 0, "fail_exhausted": False, "committed": False,
                "events": events}
    return {"completed": False, "status": status, "chapter": chapter, "committed": True,
            "events": events}


def gate_committed_chapter(*, harness, pid: str, chapter: int, status: str,
                           judge_fn, fix_fn, budget: CallBudget,
                           measure_fn=None, rhythm_fn=None, event_fn=None,
                           max_retries: int = GATE_MAX_RETRIES) -> dict:
    """이미 '커밋된' 회차(chapter)에 대한 게이트 단계 — 계측 → (스팬수리) → LLM 게이트 →
    FAIL 시 fix_note regen(≤max_retries). **step 을 부르지 않는다**(회차 중복 생성 없음 — regen 은 in-place).

    judge_fn(text, meas[, ctx]) -> {'verdict','fix_note','drop_trigger','retention_est'}  (콜 계상 여기서)
      ctx={'story_so_far','genre','chapter'} 는 3-arg judge 에만 전달(2-arg 모의 judge 는 그대로 호출).
    fix_fn(harness,pid,ch,text,spans) -> truthy(적용)  또는 None(스킵)
    measure_fn(state,chapter)->dict, rhythm_fn(text)->spans 는 호출부 주입(tools 바운드 — 엔진은 구현 모름).
      미주입 시 계측=빈 dict(digest '계측 없음')·수리=스팬0(수리 없음)로 안전 강등.
    event_fn(guard,chapter,action,detail)->dict 는 이벤트 형식 어댑터(미주입 시 _default_event).
    harness 계약: load(pid)·regen(pid,fix)(옵션). regen 미구현 하네스는 step(pid,fix=...) 로 폴백.

    P-3: 반환에 'gate_rounds':[{round,verdict,drop_trigger,fix_note,retention,hate_comment,repair_path,reverted}]
    누적(초회 round=0, 재시도 round=1..R). 호출부가 이 배열을 로그/영속에 그대로 싣는다. **기존 톱레벨 키는 불변**
    (verdict/fail_exhausted/chapter 등 — resume_point 호환). fix_spans 이벤트는 events 에만(gate_rounds 중복 금지).
    """
    ev = event_fn if callable(event_fn) else _default_event
    _measure = measure_fn if callable(measure_fn) else (lambda st, ch: {})
    events: list[dict] = []
    st = harness.load(pid)
    ctx = {"story_so_far": _story_so_far(st, chapter),
           "genre": getattr(st.world, "genre", "") or "", "chapter": chapter,
           # EB-3: 스파인 [N화차] 분해가 있으면 이 화 몫 설계 사건을 심사 컨텍스트로(없으면 '' — 바이트 불변).
           "planned_event": planned_event_for(st, chapter)}

    judge_arity = _positional_arity(judge_fn)
    gate_rounds: list[dict] = []   # P-3: 라운드별 심사문 영속(초회=0, 재시도=1..R). 톱레벨 키 불변.

    def _judge(text: str, meas: dict) -> dict:
        # 3-arg judge(live)에는 ctx 전달, 2-arg judge(모의)는 그대로 — 시그니처로 판별(내부 TypeError 오검출 방지).
        return judge_fn(text, meas, ctx) if judge_arity >= 3 else judge_fn(text, meas)

    def _record_round(round_no: int, v: dict, *, repair_path: str = "none",
                      reverted: bool = False) -> None:
        # P-3: 한 심사 라운드의 판정 원문을 gate_rounds 에 누적(반환·영속 공통). fix_spans 이벤트는 events 유지(중복 금지).
        # DP-21: repair_path("revise"|"regen"|"none")·reverted(bool) additive — 이 라운드로 '진입시킨 수리 경로'와
        #   국소 악화 복원 여부를 영속(작가 가시화). 초회(round=0)는 수리 진입 없음 → "none"/False.
        gate_rounds.append({"round": round_no, "verdict": str(v.get("verdict")),
                            "drop_trigger": v.get("drop_trigger"), "fix_note": v.get("fix_note"),
                            "retention": v.get("retention_est"), "hate_comment": v.get("hate_comment"),
                            "repair_path": repair_path, "reverted": bool(reverted)})

    def _measure_and_fix(ch_no: int) -> tuple[dict, str]:
        st_i = harness.load(pid)
        meas = _measure(st_i, ch_no)
        text = _chapter_text(harness, pid, ch_no)
        fx = _apply_fix_spans(fix_fn, harness, pid, ch_no, text, rhythm_fn)
        if fx is not None:
            events.append(ev("fix_spans", ch_no, "flag",
                             f"리듬 스팬 {fx['spans']}건 검출 — v2 국소수리 "
                             f"{'적용' if fx['applied'] else '스킵'}"))
            if fx["applied"]:
                text = _chapter_text(harness, pid, ch_no)   # 수리 후 본문 재로드
        return meas, text

    def _ret(v: dict):
        r = v.get("retention_est")
        return r if isinstance(r, (int, float)) else None

    # ── 게이트 라운드(최초 + 재시도 R회) ──
    meas, text = _measure_and_fix(chapter)
    budget.charge(1)
    verdict = _judge(text, meas)
    _record_round(0, verdict)          # P-3: 초회 심사문 영속(수리 경로 없음)
    retries = 0
    reverted_stop = False              # ⓑ: 국소 악화 복원으로 재시도를 끊었는지(최선 판본 전진)
    while str(verdict.get("verdict")) == "FAIL" and retries < max_retries:
        retries += 1
        prev_verdict = verdict         # 이 라운드가 악화하면 되돌아갈 '최선 직전 판본'의 판정
        prev_ret = _ret(prev_verdict)
        fix_note = (verdict.get("fix_note") or "").strip() or "정독 게이트 FAIL — 앞 라운드 지적 대목을 수정하라."

        # ── ⓐ 재시도 라우팅: drop_trigger 가 본문에 앵커되면 국소(revise) 경로, 아니면 regen 폴백(이중 폴백 없음). ──
        anchor = anchor_drop_trigger(text, verdict.get("drop_trigger") or "")
        local = _revise(harness, pid, chapter, fix_note, anchor["span_text"]) if anchor else None
        # P-8: 라이브 국소 경로가 소비한 revise/accept LLM 콜을 심사 예산에 계상(DP-21 실비용 축 정합).
        #   콜은 accept 성공/실패·무변경·가드레일 불통과 어느 경로에서든 이미 소비됐으므로 결과 분기 전에 charge.
        #   드라이런은 llm_calls=0(계측 불가) → 무계상(모의 경로 산술 불변). 상한 초과 시 기존 LLMBudgetExceeded 전파.
        if local is not None and local.get("llm_calls"):
            budget.charge(local["llm_calls"])
        repair_path = "none"
        if anchor and local is not None and local.get("accepted"):
            # 국소 경로 성공 — 스팬 ±1 창만 퇴고(나머지 본문 불변 = churn 소스 제거). 재계측·재심사.
            repair_path = "revise"
            events.append(ev("gate_revise", chapter, "flag",
                             f"정독 FAIL(retry {retries}/{max_retries}) — 앵커 성공, 스팬 국소 퇴고"
                             f"(문단#{anchor['para_idx']}±1)"))
            meas, text = _measure_and_fix(chapter)
            budget.charge(1)
            verdict = _judge(text, meas)
            cur_ret = _ret(verdict)
            # ── ⓑ 최선 판본 보존: 재심사 retention 이 직전보다 악화하면 undo 로 정식 복원 + 재시도 중단. ──
            if prev_ret is not None and cur_ret is not None and cur_ret < prev_ret:
                undone = _undo(harness, pid, chapter)
                # undo 성공 시에만 '최선 판본 전진'(직전 판본 복원). 실패 시 악화 본문이 잔존하므로 문구를 정직화.
                revert_msg = (f"국소 퇴고가 retention 악화({prev_ret}→{cur_ret}) — undo 복원·재시도 중단·최선 판본 전진"
                              if undone else
                              f"국소 퇴고가 retention 악화({prev_ret}→{cur_ret}) — undo 불가·악화 판본 잔존(플래그)·재시도 중단")
                events.append(ev("gate_revert", chapter, "flag", revert_msg))
                _record_round(retries, verdict, repair_path="revise", reverted=undone)
                if undone:
                    verdict = prev_verdict          # 최선(직전) 판본으로 판정 승격
                    reverted_stop = True
                break
            _record_round(retries, verdict, repair_path="revise")
            continue

        # ── 전체 경로(폴백): 앵커 실패(구조적 지적)·revise 무변경/가드레일 불통과 → regen 1회(이중 폴백 없음). ──
        why = ("앵커 실패(구조적 지적)" if not anchor
               else ("revise 무변경(보수 폴백)" if (local and not local.get("changed"))
                     else "국소 가드레일 불통과"))
        events.append(ev("gate_regen", chapter, "flag",
                         f"정독 FAIL(retry {retries}/{max_retries}) — {why} → regen 재생성"))
        rres = _regen(harness, pid, fix_note)
        if rres is not None and rres.get("completed"):
            # 재생성 자체가 completed 신호를 낼 일은 없으나 계약 방어
            return {"completed": True, "reason": str(rres.get("reason") or "completed"),
                    "events": events, "gate_rounds": gate_rounds}
        meas, text = _measure_and_fix(chapter)
        budget.charge(1)
        verdict = _judge(text, meas)   # ★MED 수리: 재시도도 _judge(ctx 관통) — judge_fn 직접 호출 금지
        _record_round(retries, verdict, repair_path="regen")   # P-3: 재시도 라운드 심사문 영속

    final = str(verdict.get("verdict"))
    fail_exhausted = final == "FAIL" and not reverted_stop  # R 소진 후 FAIL → 플래그 전진. 단 ⓑ 복원 중단은 소진 아님(최선 판본).
    if fail_exhausted:
        events.append(ev("gate_fail_exhausted", chapter, "flag",
                         f"R({max_retries}) 소진 후에도 FAIL — 플래그로 전진(은폐 금지). "
                         f"drop={verdict.get('drop_trigger','')[:80]}"))
    return {"completed": False, "status": status, "chapter": chapter,
            "verdict": final, "retries": retries, "fail_exhausted": fail_exhausted,
            "drop_trigger": verdict.get("drop_trigger"), "fix_note": verdict.get("fix_note"),
            "retention": verdict.get("retention_est"),
            "hate_comment": verdict.get("hate_comment"), "events": events,
            "gate_rounds": gate_rounds}   # P-3: 라운드별 심사문 누적(톱레벨 기존 키 불변 — 추가만)


def gate_one_chapter(*, harness, pid: str, judge_fn, fix_fn, budget: CallBudget,
                     measure_fn=None, rhythm_fn=None, event_fn=None, rec_status_fn=None,
                     max_retries: int = GATE_MAX_RETRIES) -> dict:
    """한 회차 = gen(step) → 게이트(계측→수리→심사→FAIL시 regen). gen_step + gate_committed_chapter 합성.

    ⚠ 이 합성 함수는 step 예외와 게이트 예외를 구분하지 않는다 — 전체 재시도 시 step 을 다시 불러
    회차를 중복 생성할 수 있다(HIGH). run_gated_arm 은 이 함수를 쓰지 않고 gen_step/gate_committed_chapter
    를 단계별로 호출해 재시도 경계를 step 이후로 좁힌다. 이 함수는 단발 테스트/외부 호출 편의용으로만 유지.
    """
    step = gen_step(harness=harness, pid=pid, rec_status_fn=rec_status_fn, event_fn=event_fn)
    if step.get("completed") or step.get("status") == "ESCALATED":
        return step
    gate = gate_committed_chapter(harness=harness, pid=pid, chapter=step["chapter"],
                                  status=step["status"], judge_fn=judge_fn, fix_fn=fix_fn,
                                  budget=budget, measure_fn=measure_fn, rhythm_fn=rhythm_fn,
                                  event_fn=event_fn, max_retries=max_retries)
    gate["events"] = (step.get("events") or []) + (gate.get("events") or [])
    return gate
