# -*- coding: utf-8 -*-
"""에이전틱 하네스 — 타입드 상태머신(클래스, 의존성 주입).

build ContextBoard(타입 분리 슬롯 + 직전 회차 원문) → plan_scenes
→ [scene 미니루프: draft→check→국소 partial_rewrite, best-so-far] → 전체 검사
→ status(FINALIZED/ESCALATED) → FINALIZED면 finalize 팬아웃(rag 멱등 색인 + wiki 멱등 ingest).
'harness over model': 전진·검증·종료·라우팅은 코드가, LLM은 plan/draft/rewrite 에만.
모든 단계는 EventBus 로 방출 → SSE 로 실시간 가시화(조용한 정지 불가).
"""
from __future__ import annotations
import json
import re
import time   # TM-1: 단계별 소요 시간(time.monotonic — 벽시계 아님·시스템 시간 변경 무관)

# G9: 'to be continued' 추가. 줄머리 경로엔 바깥 '회/편'을 안 넣는다(회의/편의점 등 오탐 위험);
# '(다음 회에서 계속)' 류는 아래 괄호-단독행 경로(전체가 괄호=거의 확실히 메타)에서 잡는다(sanitize 동일 범주, 작법 검출 아님).
_META_LINE = re.compile(r"^\s*[\[【(#\-=*]{0,3}\s*(END|끝|다음\s*(화|회차|:)|계속|to\s*be\s*continued|장면\s*(종료|전환)|scene|chapter|메모|note|todo|작가\s*주)"
                        r".{0,80}$", re.IGNORECASE)
_BRACKET_ONLY = re.compile(r"^\s*[\[【(].{0,100}[\]】)]\s*$")   # (괄호)/[대괄호] 단독행 — '(다음 회, …)' 본문 박힘 제거
# 주의(B-24 적대검증 결론): '인라인 괄호 편집메모'를 어휘목록(수정필요·불일치·오류·참고:·주의: 등)으로 잡으려던 시도는
# 두더지잡기로 기각됐다. 시스템물의 diegetic 괄호 '(오류: 접근 거부)·(주의: 함정)·(참고: 일일퀘스트)'와 서사 괄호
# '(그건 모순이었다)'를 작가메모와 구분 못 해 오탐 27건(장르-치명)·미탐 10건(동의어 회피) 실측. 어휘 기반 의미판정 금지.
# → 메타누출의 정공법은 (1) 생성 단계 지시(아래 out_instr/_continue 의 '메모·괄호주석이 끼어들 자리 없음' 선언)와 (2) 근본 원인인
#   엔티티 등록부(인물/인원 모순을 모델이 주석으로 때우게 만드는 상황 제거). 줄단위 안전 패턴만 유지한다.


def sanitize_meta(text: str) -> str:
    """생성물에서 메타텍스트([END…], 장면 표지, 작가 메모류) 라인 제거 — FINALIZED 본문 누출 차단(결정론).
    줄단위 안전 패턴만(인라인 어휘 매칭은 B-24 적대검증서 오탐 과다로 기각 — 서사/시스템 괄호 보존)."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and (_META_LINE.match(s) or (_BRACKET_ONLY.match(s)
                  and re.search(r"END|다음\s*(화|회|회차)|계속(됩니다|\)|\s*$)|회차|to\s*be\s*continued|scene break|chapter", s, re.IGNORECASE))):
            continue
        if s.startswith(("(※", "※", "(* ")) or re.match(r"^\s*[\((]\s*※", s):   # 퇴고 지시문((※ …수정함) 류) 누출 제거
            continue
        if s.startswith(("- \"", "- “", "* \"", "* “")):   # 마크다운 불릿이 대사에 누출된 조판 제거
            ln = ln.replace("- ", "", 1).replace("* ", "", 1)
        out.append(ln)
    return "\n".join(out).strip()


def fragmentation_score(text: str) -> float:
    """비대사 줄의 평균 길이(자) — 토막 행갈이 붕괴 탐지용(결정론). 낮을수록 파편화."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    prose = [ln for ln in lines if not ln.startswith(('"', '“', '—', "'"))]
    if len(prose) < 8:
        return 99.0
    return sum(len(ln) for ln in prose) / len(prose)


def short_line_ratio(text: str) -> float:
    """비대사 줄 중 15자 미만 비율 — 평균은 정상인데 토막이 17~25% 섞이는 패턴 탐지(편집자 실측 보완)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    prose = [ln for ln in lines if not ln.startswith(('"', '“', '—', "'"))]
    if len(prose) < 8:
        return 0.0
    return sum(1 for ln in prose if len(ln) < 15) / len(prose)


def _narr_advisory_entry(f: dict) -> dict:
    """HM-3 가시화: N-1/N-2 탐지 finding → advisory humanize 내역(원문 불변·changed=False·surface). 작가 판단용.
    humanize 엔트리 스키마와 동형 필드 + source='llm_advisory'·fallback='surfaced_advisory'·note=사유(why)·text=원문 스팬."""
    sp = f.get("span") or {}
    txt = sp.get("text") or ""
    return {
        "category": f.get("category"), "severity": f.get("severity"),
        "char_start": sp.get("char_start"), "char_end": sp.get("char_end"),
        "before_len": len(txt), "after_len": len(txt), "changed": False, "change_rate": 0.0,
        "rate_band": None, "fallback": "surfaced_advisory", "coverage_passed": None,
        "guardrail_ok": None, "author_review": False,
        "note": (f.get("metric") or {}).get("why"), "source": "llm_advisory",
        "mode": (f.get("metric") or {}).get("mode", "catchall"), "text": txt,   # 역할 라벨(가시화 vs 수술 큐 구분)
    }


def _collect_humanize_span_texts(before_full: str, after_full: str, entries: list) -> list[dict]:
    """GA-1: humanize 스팬별 before/after 텍스트를 trace 용으로 수집(본체 엔트리엔 좌표·변경률만 남는다).

    before = '판정 시점(수술 전) 본문'을 스팬 char 범위로 슬라이스한 원문 창(정확 before는 이 스냅샷에서).
    after  = 수술 결과(변경) or 원문(폴백=원문). humanize_pass 가 스팬을 순차 적용해 후속 스팬 좌표가 밀리므로
             원 좌표로 after_full 을 다시 슬라이스하면 부정확할 수 있다 — 그래서 재-앵커(humanize_pass 자신의
             re-anchor 관례)로 정직하게 판정한다: changed=False(폴백=원문)면 after=before. changed=True 면 그
             원문 창이 후처리 본문에 그대로 남아 있는지(occ==1)로 확인해, 남아 있으면(사실상 미변경) after=before,
             사라졌으면(재작성됨) 정확 슬라이스 불가라 after=None + note(은폐 금지·결측 정직 — 최종 재작성 결과는
             run['final_text'] 전문에 보존됨). 좌표 무효/전범위(not_local·char 결측)는 before=None.
    반환: [{category, severity, char_start, char_end, changed, fallback, author_review, before, after, note}].
    """
    out: list[dict] = []
    bf = before_full or ""
    af = after_full or ""
    for e in (entries or []):
        cs = e.get("char_start")
        ce = e.get("char_end")
        changed = bool(e.get("changed"))
        rec: dict = {
            "category": e.get("category"), "severity": e.get("severity"),
            "char_start": cs, "char_end": ce, "changed": changed,
            "fallback": e.get("fallback"), "author_review": bool(e.get("author_review")),
            "before": None, "after": None, "note": None,
        }
        # CE-7: 반려 증거(막힌 재작성문·가드 상세)를 트레이스 rec 에 실어 나른다 — guardrail 폴백 시에만 채워지고
        #   아니면 미포함(구 트레이스 rec 바이트 동일·하위호환). 현행은 반려 스팬이 after==before 로만 남아
        #   「무엇이 왜 막혔나」가 트레이스에서 소실됐다(반려 28/28 증거 없음의 소스). 이제 rec 에 남는다.
        if e.get("rejected_after_span") is not None or e.get("guardrail_detail") is not None:
            rec["rejected_after_span"] = e.get("rejected_after_span")
            rec["guardrail_detail"] = e.get("guardrail_detail")
        before_span = None
        if isinstance(cs, int) and isinstance(ce, int) and 0 <= cs < ce <= len(bf):
            before_span = bf[cs:ce]
        rec["before"] = before_span
        if before_span is None:
            rec["note"] = "좌표 무효/전범위 — 국소 before 슬라이스 불가(전범위 밀도 신호 등)"
        elif not changed:
            rec["after"] = before_span            # 폴백=원문(수술 미채택) — after=before
        else:
            # 변경 채택 — 원문 창이 후처리 본문에 남아 있으면 사실상 미변경, 사라졌으면 재작성(정확 슬라이스 불가)
            if af.count(before_span) >= 1:
                rec["after"] = before_span
            else:
                rec["note"] = "재작성 채택 — 순차 스팬 오프셋 밀림으로 정확 after 슬라이스 불가(최종본은 final_text 전문에 보존)"
        out.append(rec)
    return out


from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)
from ..config import Settings
from ..domain.types import (ContextBoard, SceneSpec, ChapterRecord, ChapterStatus,
                            RoundTrace, AuthorDirective, SignalGrade)
from ..domain.world import StyleSpec
from ..llm.base import LLMProvider
from .checker import Checker
from .prompts import PromptAssembler, render_style, floor_only, style_fewshot_block
from .textfmt import strip_structural_markup, reflow_paragraphs, lower_dialogue_tags
from .observability import EventBus
from .correction import trim_dangling, within_correction_bounds, drift_ratio
from .drift import uncovered as _uncovered   # DP-12: 이어쓰기 트리거의 '비트 미소진' 판정 자산(결정론·보수적) 재사용
from . import style_counter   # ST-12b: draft 러닝 카운터(순수 엔진 모듈 — tools/kiwipiepy 는 이 모듈 함수 안 lazy import). 로드타임 tools 의존 0


# 페이싱/반복 craft 지시(회차 집필 프롬프트 말미) — '한 장면 늘려쓰기(패딩)·동일 비트 반복'을 전진 압력으로 차단.
# 기존 스타일 규칙('같은 사건 두 번 금지')의 강화판. A/B 가독성 4:2(전개 빨라짐). 반복지표 개선은 미확인.
# C-3: 부정명령 3건("되풀이하지 말고"·"반복하지 말고"·"분량을 채우지 말고")을 긍정 구조지시로 전환 —
#      pink-elephant 교훈(B-23: 부정형이 금지 대상 토큰을 오히려 프라이밍) 소스차단. '전진' directional 가치와
#      중앙 문장(변화 추적축 최소 1개)은 보존. 문구만 교체라 기존 A/B(가독성 4:2)는 신문구로 재검증 대상(후속).
_CRAFT_PROGRESS = ("\n\n[전개 압력] 이 회차는 직전 회차가 멈춘 지점을 출발선 삼아 새 국면으로 전진시켜라. "
                   "이 작품의 전제·직전 상황이 변화를 추적하는 축(장소·관계·정보·정황 등 작품이 정한 것) 중 최소 하나가 회차 안에서 실제로 바뀌어야 한다. "
                   "장면·묘사·대사·회상은 매번 직전에 없던 새 재료(새 사실·새 행동·새 정황)를 담아 앞으로 나아가게 쓰고, "
                   "분량은 사건이 요구하는 만큼이다. 필요한 만큼 쓰고 다음 국면으로 넘어가라.")
# LG-1: '자연히 길어지게 하라' 는 한쪽 방향으로만 걸린 압력이었다(상한 문장 부재). opus-5 는 기본 산출이
#   길고(가이드: "files Claude Opus 5 writes are often longer"), 실측도 규범 5,000자 대비 +31.7~46.3%.
#   길이 코드강제는 이미 기각됐으므로(분량≠밀도 B-02ⓐ) 강제를 얹지 않고 **편향만 중립화**한다.
# P-1②: ch1 발단 전용 craft 변형 — 기본 _CRAFT_PROGRESS 의 '직전 회차가 멈춘 지점을 출발선으로' 프레이밍은
#   1화에 참조할 '직전 회차'가 없어 발단 grounding(일상·인물 세우기)과 정면 긴장(U16). 1화는 '전진 압력'의
#   directional 가치는 보존하되 출발점을 발단 자체(인물·일상 세우기)로 옮긴다 — 긍정 전용(pink-elephant:
#   금지 문구 0, 무엇을 '하라'만). 도입 장면 자체가 전진이라는 결로, 발단 안에서도 상황이 한 칸씩 움직이게 한다.
_CRAFT_PROGRESS_OPENING = ("\n\n[전개 압력] 이 회차는 작품의 첫머리다. 인물과 일상을 손에 잡히게 세우는 장면 자체가 전진이다. "
                          "도입 안에서도 상황이 한 단계씩 실제로 움직이게 하라(관계·정보·정황·처지 중 하나가 장면 안에서 바뀌게). "
                          "장면·묘사·대사는 매번 직전에 없던 새 재료(새 사실·새 행동·새 정황)를 담아 앞으로 나아가게 쓰고, "
                          "분량은 인물과 상황이 서고 전환이 발발하는 데 필요한 만큼이다. 세워졌으면 다음으로 넘어가라.")   # LG-1 동형 중립화
def opening_move_line(moves, prev_chapter_text: str, chapter_no: int) -> str:
    """OP-1(VI-1) — 오프닝 수법 결정론 낙점. 풀("태그|지시문")에서 직전 화 오프닝 유형(대사/비대사)과
    같은 태그를 제외하고 회차 번호로 하나를 고른다. 긍정 지시 한 줄만 — 피할 유형은 어디에도 안 적는다
    (차단=후보 풀 제외, 핑크 엘리펀트 금지). moves 비면 ""(프롬프트 바이트 동일)·ch1 은 발단 결 유지로 제외."""
    if not moves or int(chapter_no or 0) <= 1:
        return ""
    first = next((ln.strip() for ln in (prev_chapter_text or "").splitlines() if ln.strip()), "")
    prev_kind = "대사" if first.startswith(('"', '“', "'")) else "사물"
    pool = []
    for mv in moves:
        tag, _, instr = str(mv).partition("|")
        pool.append((tag.strip(), (instr or tag).strip()))
    filtered = [(t, i) for (t, i) in pool if t != prev_kind] or pool
    _, instr = filtered[int(chapter_no) % len(filtered)]
    return f"\n\n[오프닝] 이 회차의 첫 문장은 {instr}"


# GN-2: 발단(ch1) 전용 — 교정 패스(_rewrite·_regen_tail·_continuity_polish·_fix_tics)에 얹는 발단 grounding 보존 한 줄.
#   근거(audit_e2e_0.md A-1): 교정 패스는 ch 번호를 몰라 DP-7/P-1이 심은 발단 결(rule⑤ in-medias-res 억제·grounding)을
#   교정 시 소실할 수 있다 — 1화가 캐논 rewrite/훅 regen_tail/polish/tics 를 타면 발단이 흔들린다. _draft/_continue 의
#   ch1 처리와 동형으로 교정 콜에도 짧게 관통한다. *교정 패스 전용 결*: 이 패스들은 '수정만' 하는 자리라 새 in-medias-res
#   개시를 만들라는 게 아니라, 이미 세워진 발단 grounding(주인공이 '누구'인지 세우는 도입·전환의 결)을 흩뜨리지 말고
#   유지하라는 보존 지시다. 긍정 전용(pink-elephant: 금지 문구 0, '유지하라'만) · floor-only 계약(B-10) 불침해 —
#   미학·문체를 새로 얹지 않고 발단 결의 보존만 요구한다. 비ch1(ch!=1)엔 아무것도 붙이지 않아 프롬프트 바이트 동일.
_OPENING_CORR_HINT = ("\n이 회차는 작품의 첫머리(발단)다. 주인공이 '누구'인지 세우는 도입과 그 위로 전제의 전환이 발발하는 "
                      "발단의 결을 그대로 유지한 채 교정하라(도입의 grounding 은 그대로 살려 두고 교정 지점만 손대라).")

# 장면형 스타일 앵커(실험) — 블랭킷 지시의 '과적용(진자)'을 피해, 비트 기능(chapter_function, 결정론)에 *선택*을 묶는다.
# 전역으로 최대화되는 지시가 아니라 '이 장면의 결'만 짧게 — 필요한 장면에만 맞게.
_SCENE_ANCHORS = {
    "escalation": "[이 장면의 결: 속도] 짧은 동작·대사를 부딪치며 이 장면의 긴장을 한 칸씩 올려라.",
    "payoff": "[이 장면의 결: 터뜨림] 헤지('~듯한/~같은') 없이 확정 서술로 쌓아온 것의 무게가 한 방에 드러나게.",
    "setup": "[이 장면의 결: 심기] 나중에 터질 단서를 장면 속 사물·행동에 자연스럽게 깔아라. '복선이다' 티 내는 설명조 금지.",
    "relation": "[이 장면의 결: 관계] 대사가 장면을 굴린다. 인물 말투를 분화해 짧은 티키타카로, 지문 설명은 최소.",
    "respite": "[이 장면의 결: 완급] 숨을 돌려라. 감각 디테일 한둘과 여백으로, 단 사건은 정체시키지 말고 한 뼘은 전진.",
}
_SCENE_DEFAULT = "[이 장면] 장면의 목적에 맞는 결로. 균일하게 묘사를 깔지 말고 필요한 곳만 또렷이."
# few-shot 예시 앵커(연구 #1 레버) — 지시(최대화→진자) 대신 '결·리듬'을 *모방*시킨다. 내용 복사(자기표절) 금지 프레이밍.
_EX_PREFIX = "\n\n[이 장면의 결: 아래는 호흡·리듬 '참고용'. 내용·인물·문장을 절대 베끼지 말고 이 *결*만 가져와라]\n"
_SCENE_EXAMPLES = {
    "escalation": "발소리가 가까워졌다. 한 걸음. 또 한 걸음.\n“…거기 있는 거 알아.”\n숨을 멈췄다. 문고리가 천천히 돌아갔다.",
    "payoff": "문이 열렸다. 십 년을 기다린 문이.\n그가 안으로 들어섰다. 더는 떨지 않았다.",
    "setup": "그는 식탁 밑으로 반지를 밀어 넣었다. 아무도 보지 못했다.\n어머니가 국그릇을 내려놓으며 물었다. “오늘은 늦니?”",
    "relation": "“돈은?”\n“없어.”\n“그럼 왜 왔어.”\n“너 보러.”\n사내가 픽 웃었다. 여자는 웃지 않았다.",
    "respite": "창밖으로 눈이 내렸다. 그는 식은 커피를 쥔 채 한참을 그렇게 있었다.\n멀리서 개가 짖었다. 다시 조용해졌다.",
}
# 블랭킷(대조군) — 전역 문체 지시(어디나 최대화돼 과적용되는 형태)
_STYLE_BLANKET = ("\n\n[문체] 감각(소리·온도·무게)으로 보여주고, 한국어 입말로 능동·짧게 쓰고, "
                  "문장 길이와 종결어미를 변주하고, 거창한 추상 수식 대신 구체로 단언하라.")

# RC-5 F3ⓐ(prompt-auditor): 재요약 산출 synopsis 길이 하한. 미달 = 저사건·고재고 회차에서 1,200자를 재고
#   나열로 채우는 대신 얇게 끝난 산출 신호 → degraded 로 가시화(신뢰 강등·폴백 계약과 정합). 목표 1,200~1,600 의 보수 하한.
_SUMMARY_SYNOPSIS_FLOOR = 900


class ChapterGenerator:
    def __init__(self, provider: LLMProvider, checker: Checker, style: StyleSpec,
                 event_bus: EventBus, settings: Settings, aux_provider: LLMProvider | None = None):
        self.provider = provider
        # CE-1 ⓑ: 보조(재요약 등 기계 스테이지)용 저가 provider. 미주입이면 gen provider 로 폴백(aux_model="" 또는
        #   레거시 호출부 → 스왑 0·기존 경로 바이트 동일). 프로즈(draft/continue/polish)·check 는 항상 self.provider.
        self.aux_provider = aux_provider or provider
        self.checker = checker
        self.style = style
        self.bus = event_bus
        self.settings = settings
        self.assembler = PromptAssembler(style, settings.prev_chapter_context_chars)
        self.style_block = render_style(style)   # rules+author_style 렌더. 정책 패치는 update_style_policy 가
        #   세션을 evict→ 다음 요청에 generator 재구성하므로 캐시여도 스테일 없음(라이브 참조 불필요).
        # DP-7: 발단(1화) 전용 변형 — rule⑤(in-medias-res 개시)를 grounding→전환 결로 렌더 시점 치환.
        #   비1화는 self.style_block 그대로 사용 → 프롬프트 바이트 동일(무회귀). 저장 rules 데이터 불변.
        self.style_block_opening = render_style(style, opening=True)
        # SP-1 Stage A: 순방향 few-shot 문체 예시 블록(문체 블록 뒤 주입). config style_fewshot 기본 False —
        #   사용자 판정(2026-07-13) "few-shot=오염, 전면 금지"로 프로덕션 OFF 다(VP-1 적대 리뷰에서 stale 주석
        #   "실 Settings 는 True" 오기가 오진을 유발해 정정). OFF 면 "" → system 프롬프트 바이트 동일(무회귀).
        #   예시 파일 부재 시에도 ""(강등=OFF 동형). getattr 폴백=False: 속성 부재 mock 도 바이트 동일.
        self.style_fewshot_block = (style_fewshot_block()
                                    if getattr(settings, "style_fewshot", False) else "")
        self.floor_block = floor_only()          # B-10: 교정 패스(_rewrite)용 — 미학 오버레이 없이 바닥 제약만(재문체화 차단).
        self.obsession_block = ""                # 풍부함 다리(실험): 비면 회차 집필 프롬프트에 '집착 렌즈·감각물' 주입(프로즈 A/B 검증용). 기본 무동작
        # 페이싱 craft(긍정 구조지시로 전진 압력, C-3) — SL비교 1순위 결함(4회차=단일던전 패딩) 직타. A/B 4:2 가독성 우세(모처럼 비null,
        # 단 약한신호·반복지표 미개선·단일시드). config.craft_progress 토글(기본 ON·저위험 craft). 일반화 확인은 후속.
        self.craft_block = _CRAFT_PROGRESS if getattr(settings, "craft_progress", True) else ""
        # P-1②: ch1 발단에서만 craft_block 을 발단 호환 문안으로 교체(비1화 바이트 동일). 토글 OFF면 둘 다 ""(무동작).
        self.craft_block_opening = _CRAFT_PROGRESS_OPENING if getattr(settings, "craft_progress", True) else ""
        # 장면형 앵커: 비트 기능(chapter_function)으로 그 장면 결에 맞는 짧은 앵커를 *선택적* 주입(블랭킷 지시의 과적용 회피).
        # A/B: scene > blanket 4:2 + ai_tell 변주 0.59 vs 0.49(블랭킷 '변주하라'가 역설적으로 변주↓=과적용 입증). scene>none·craft스택 미검 → 기본 off.
        self.style_mode = "scene" if getattr(settings, "scene_style_anchor", False) else "none"  # none/blanket/scene(A/B는 인스턴스 오버라이드)
        self._cur_scene_inject = ""              # generate()가 비트 기능으로 채우는 장면 앵커(_draft/_continue 주입)
        self._cur_skill_inject = ""              # 켜진 회차 스킬(문체 등)의 주입 텍스트 — generate()가 회차마다 채움
        # RV-1②: 마지막 revise_prose 호출의 무변경/성공 원인 코드(사이드채널 — 반환 시그니처는 str 불변으로 st11·mock 호환).
        #   호출부(copilot.revise_chapter)가 콜 직후 읽어 changed:false 응답에 additive 로 싣는다. 값 domain:
        #   "ok"(변경 산출)·"llm_failure"(provider 예외)·"length_guard"(길이가드 폴백=캡절단/과확장/무응답)·
        #   "empty_after_sanitize"(메타살균 후 공백)·"unchanged"(길이가드 통과했으나 after==before 실질 무변경).
        self._last_revise_cause = "ok"
        # SP-1b ①(G3/G-B): 스팬 수리(Stage B)의 사실 불변 가드레일(G-B 클레임 표면 비교)에 쓸 CopilotService
        #   참조. 엔진(ChapterGenerator)은 _guardrail 을 갖지 않으므로(그 메서드는 서비스 계층 소유) 호출부
        #   (CopilotService.generate_next_chapter)가 generate() 직전에 self 를 주입한다. 부재(None)면
        #   st11.rewrite_span_via_chassis 의 G-B 비교가 생략(기존 no-op 계약) — 러너/단위 테스트/구 세션 호환.
        #   *중요*: 여기 클래스 속성 default 를 두어 '속성 부재' 상태를 없앤다(getattr 폴백 불필요·정직 결측).
        self.service = None

    def _style_inject(self, beat: dict) -> str:
        if self.style_mode == "blanket":
            return _STYLE_BLANKET
        fn = (beat.get("chapter_function") or "").strip().lower()
        if self.style_mode == "scene":
            return "\n\n" + _SCENE_ANCHORS.get(fn, _SCENE_DEFAULT)
        if self.style_mode == "example":
            ex = _SCENE_EXAMPLES.get(fn)
            return (_EX_PREFIX + ex) if ex else ("\n\n" + _SCENE_DEFAULT)
        return ""

    def _pov_entity_id(self, ontology) -> str:
        """DP-8: 1인칭(style.pov=='first')일 때 서술자 '나'로 결속할 주인공 id — 없으면 ""(3인칭·미검출).
        도출 규칙: worldgen 규약상 entities 선언 순서의 첫 actor 가 주인공이다(스키마 예시 id 'hero' 가
        entities[0]; ontology.entities 는 삽입순 dict 로 그 선언순을 보존). 3인칭이면 결속 자체가 무동작이라
        빈 문자열을 반환해 추출기 경로를 기존과 바이트 동일하게 둔다(발명·과추출 없음)."""
        if getattr(self.style, "pov", "third_limited") != "first":
            return ""
        for e in ontology.entities.values():
            if ontology.is_actor(e.etype):
                return e.id
        return ""

    # ---- LLM 격리 지점 ----
    @promptlog.stage("scene_plan")
    def plan_scenes(self, beat: dict, directives: list[AuthorDirective]) -> list[SceneSpec]:
        n = self.style.scenes_per_chapter
        approx = max(800, self.style.target_chars_per_chapter // max(1, n))
        msg = [{"role": "system",
                "content": f"웹소설 회차를 {n}개 장면으로 분해(각 장면 ~{approx}자 분량 상정). JSON만."},
               {"role": "user", "content":
                f"[비트]{json.dumps(beat, ensure_ascii=False)}\n"
                f"[지시]{json.dumps([d.text for d in directives], ensure_ascii=False)}\n"
                f'{{"scenes":[{{"goal":"..","key_events":[".."]}}]}}'}]
        try:
            scenes = self.provider.chat_json(msg, temperature=0.3).get("scenes", [])
            out = [SceneSpec(index=i, goal=s.get("goal", ""), key_events=s.get("key_events", []))
                   for i, s in enumerate(scenes)]
            return out or [SceneSpec(index=0, goal=beat.get("summary", ""),
                                     key_events=beat.get("key_events", []))]
        except Exception:
            self.bus.emit("plan_scenes", "parse_failure", chapter=beat.get("chapter"))
            return [SceneSpec(index=0, goal=beat.get("summary", ""), key_events=beat.get("key_events", []))]

    _HOOKS = {   # 회차 끝맺음 정책(StyleSpec.ending_hook 데이터 주도). 작법 '명칭'(절단신공/훅 등)은 모델 입력 금지
        # C-4: 부정명령("깔끔히 닫지 말고") 긍정 전환 — pink-elephant(B-23) 소스차단. '미결의 순간에서 끊기' 의도 보존.
        "cliffhanger": ("\n\n[이 장면은 회차의 마지막] 이 회차가 쌓아온 긴장·물음에서 자연스럽게 자라난 "
                        "미결의 순간에서 끊어 다음 화가 궁금해지게 하라. 끝은 열린 채로 두고, 마지막 한두 줄에서 멈춰라."),   # 은어 누출('절단.' 단독행) 소스 제거
        "soft": ("\n\n[이 장면은 회차의 마지막] 다음 화가 궁금해질 여지는 남기되, 과도한 위기 조성 없이 "
                 "이 회차의 감정·상황을 자연스러운 여운으로 마무리하라."),
        "none": "",
    }
    # C-4: "예고 미끼로 끊지 말고"·"도입 금지" → 긍정 전환("매듭지어라"·기존 요소 whitelist). 완결 의도 보존.
    _CLOSING = ("\n\n[이 장면은 '작품 전체의 마지막' 회차다] 중심 갈등과 감정선을 매듭지어 완결로 끝내라. "
                "본문에서 제시된 미결 선택은 반드시 결행하고, 이미 등장한 인물·사건·요소만으로 이야기를 정리해 "
                "여운 있게 작품을 닫아라.")

    @promptlog.stage("draft")
    def _draft(self, board, scene, prev_scenes, last=False, closing=False, recent_tails=None,
               chapter_mode=False, style_state: str = "") -> str:
        # ST-12b-2: style_state 는 '직전 회차 계측' 상태블록(대역 밖일 때만 호출부가 채움 — "" 면 user 메시지
        #   바이트가 기존과 완전 동일, additive·off 무회귀). _continue 의 동명 인자와 동일 계약.
        ch = getattr(board, "chapter", 0)   # DP-7: 발단 분기(1화만) — 스타일 블록·out_instr 결 선택. 비1화 경로 불변.
        hook = ""
        if last:
            hook = self._CLOSING if closing else self._HOOKS.get(self.style.ending_hook, self._HOOKS["cliffhanger"])
            if hook and not closing and recent_tails:
                tails = "\n".join(f"- …{t[-70:]}" for t in recent_tails[-3:] if t)
                # C-4: "반복하지 말고" → "위 목록과 다른 수법·다른 결로"(긍정 방향지시). 재탕 회피 의도 보존.
                hook += ("\n(최근 회차들의 끝:\n" + tails +
                         "\n— 이번 화의 끝은 위 목록과 다른 수법·다른 결로, "
                         "이 회차가 실제로 쌓은 것에서 자라난 끝으로 끊어라.)")
            if hook and not closing:
                # C-4: '예고 서술' 기피 예문(그 자체가 프라이밍)을 지우고 '장면 안에 머물기' 긍정 지시로.
                hook += "\n(마지막 줄까지 장면 '안'에 머물러, 지금 눈앞에서 벌어지는 구체 사건·대사로 끊어라.)"
        if chapter_mode:   # 회차 집필: 분량·장면 어휘는 모델 입력 금지(절단점은 글자 수가 아니라 극적 순간의 문제)
            # C-4: 부정명령 4건(머리말·메타 '금지'·묘사늘리기 '금지'·"두 번 쓰지 마라"·영어부호 "쓰지 마라") 긍정 전환 —
            #      출력계약은 _rewrite 검증 문구('출력은 … 그것 하나뿐')로, 부호는 whitelist('한국어 관습만')로. 의도 전량 보존.
            #      마지막 선언문('끼어들 자리는 없다')은 부정 '명령'형이 아닌 B-24 메타누출 소스차단 프레이밍이라 유지.
            if getattr(self.style, "structured_prompt", False):
                # VX-1(감사관 C2): 대사 태그 출력과 이 형식 계약을 한 곳에서 정합 — '문단 산문 하나뿐'은 대사만
                #   <대사 화자> 로 감싸는 것으로, '한국어 관습 부호'는 지문·서술로 범위를 좁혀 모순을 없앤다.
                #   '따옴표 글리프 일관' 절은 태그가 대사 구획을 맡으므로 제거(디태거가 저장 시 정규 대사로 낮춘다).
                out_instr = ("\n\n출력은 이번 회차의 소설 본문 그것 하나뿐이다. 첫 줄이 곧 이야기의 첫 문장이고, 거기서부터 마지막 문장까지 독자가 읽는 문단 산문으로 이어 쓰되, 인물이 소리 내어 하는 말은 각각 <대사 화자=\"이름\">…말…</대사> 로 감싸 쓴다. "
                             "(회차 제목은 따로 관리되므로 본문은 이야기 자체로만 이뤄진다.) "
                             "묘사는 사건이 요구하는 만큼만 두고, 이야기가 자연스러운 절단점에 이르면 거기서 멈춰라. "
                             "회차 전체에서 시제(과거형 기조)를 일관되게 유지하고, 각 사건·클라이맥스는 한 번씩만 서술한 뒤 다음으로 전진하라. "
                             "지문·서술의 문장부호는 한국어 관습만 쓴다. 끊어 읽기·삽입구·여운은 쉼표·마침표·말줄임표(…)로 처리하라. "
                             "설정 모순·인물/인원 불일치를 발견하면 한쪽으로 자연스럽게 확정해 서술하라. "
                             "본문은 독자가 그대로 읽는 완성된 소설이며, 작가의 작업 메모·괄호 주석·편집 코멘트가 끼어들 자리는 없다.")
            else:
                out_instr = ("\n\n출력은 이번 회차의 소설 본문 그것 하나뿐이다. 첫 줄이 곧 이야기의 첫 문장이고, 거기서부터 마지막 문장까지 독자가 읽는 문단 산문으로 이어 쓴다. "
                             "(회차 제목은 따로 관리되므로 본문은 이야기 자체로만 이뤄진다.) "
                             "묘사는 사건이 요구하는 만큼만 두고, 이야기가 자연스러운 절단점에 이르면 거기서 멈춰라. "
                             "회차 전체에서 시제(과거형 기조)·따옴표 글리프를 일관되게 유지하고, 각 사건·클라이맥스는 한 번씩만 서술한 뒤 다음으로 전진하라. "
                             "문장부호는 한국어 관습만 쓴다. 끊어 읽기·삽입구·여운은 쉼표·마침표·말줄임표(…)로 처리하라. "
                             "설정 모순·인물/인원 불일치를 발견하면 한쪽으로 자연스럽게 확정해 서술하라. "
                             "본문은 독자가 그대로 읽는 완성된 소설이며, 작가의 작업 메모·괄호 주석·편집 코멘트가 끼어들 자리는 없다.")
            if ch == 1:   # DP-7②: arc_planner 발단 hook(:429대) 요지를 본문 집필 콜에 관통 — 짧게·긍정형(T2: 강제 아닌 결).
                out_instr += ("\n\n(이 회차는 작품의 첫머리다 — 주인공이 '누구'인지 손에 잡히는 구체 장면으로 세운 뒤 "
                              "그 위로 전제의 전환이 발발하는 데까지 굴리고, 첫 전환의 여운이나 미결의 물음을 남겨 이어질 여지를 두라.)")
        else:
            out_instr = "\n\n출력은 소설 본문 그것 하나뿐이다. 첫 글자부터 산문으로 시작하라."   # C-4: '(…금지)' 나열 → 긍정 계약
        style_block = self.style_block_opening if ch == 1 else self.style_block   # DP-7①: 1화만 발단 변형 rule⑤ (비1화 바이트 동일)
        craft_block = self.craft_block_opening if ch == 1 else self.craft_block    # P-1②: 1화만 발단 호환 craft (비1화 바이트 동일)
        # OP-1: 오프닝 수법 낙점(풀 미설정·ch1 이면 "" → 바이트 동일). 회차 첫 세그먼트에만 의미가 있어 _draft 전용.
        opening_line = opening_move_line(getattr(self.style, "opening_moves", None),
                                         getattr(board, "prev_chapter", "") or "", ch)
        return self.provider.chat(
            [{"role": "system", "content": f"{self.style.system_persona} 확정 설정 절대 위반 금지.\n{style_block}{self.style_fewshot_block}"},   # SP-1 Stage A: 문체 블록 뒤 few-shot(OFF면 "" → 바이트 동일)
             {"role": "user", "content": self.assembler.assemble(board, scene, prev_scenes) + hook + out_instr + self.obsession_block + craft_block + opening_line + self._cur_scene_inject + self._cur_skill_inject + style_state}],   # ST-12b-2: ""면 바이트 동일
            temperature=0.85)   # 온도↓는 클리셰(고확률 토큰)를 오히려 늘릴 수 있어 보류 — 측정 후 재검토

    @promptlog.stage("continue")
    def _continue(self, board, sofar: str, closing=False, recent_tails=None, key_events=None,
                  style_state: str = "", story_remaining: str = "") -> str:
        """진행 이어쓰기 — 출고 분량은 지시가 아니라 '이야기 전진'으로 채운다(하한 지시=물 타기 차단).
        전문 말미를 보고 절단점에서 잇는 순차 연속(화 경계 인계와 동일 메커니즘 — 병렬 블록 접합 아님).

        ST-12b: style_state 는 러닝 카운터 상태블록(수치 현황+긍정 전환 메뉴)이다 — 대역 밖일 때만 호출부가
        채워 전달하고, 그 외에는 "" 다. 빈 문자열이면 user 메시지 바이트가 기존과 완전 동일하다(additive·off 무회귀)."""
        ch = getattr(board, "chapter", 0)   # P-1①: 발단 분기(1화만) — 이어쓰기 세그먼트에도 발단 결(style/craft/out_instr) 관통. 비1화 바이트 동일.
        hook = self._CLOSING if closing else self._HOOKS.get(self.style.ending_hook, self._HOOKS["cliffhanger"])
        if hook and not closing and recent_tails:
            tails = "\n".join(f"- …{t[-70:]}" for t in recent_tails[-3:] if t)
            # C-4: "반복 금지" → "다른 결로 끊어라"(긍정 방향지시) — _draft recent_tails 절과 동일 결.
            hook += "\n(최근 회차들의 끝:\n" + tails + "\n— 이번 화의 끝은 위 목록과 다른 결로 끊어라.)"
        # G9: 이 회차에 계획된 사건 목록을 참고로(아직 안 일어난 쪽으로 자연히 전진 — 회차 종결 마커 넘어 계속 쓰는 사고 방지)
        # C-4: "'다음 회에 계속'을 쓰지 마라" → "끝맺음까지 이야기 서술로만"(긍정) — 종결 마커 예문 자체가 프라이밍이라 제거.
        plan_ctx = (f"\n(이 회차의 계획 사건: {key_events} · 아직 다뤄지지 않은 것이 있으면 그쪽으로 전진. "
                    "끝맺음까지 포함해 모든 문장은 이야기 서술로만 쓴다.)" if key_events else "")
        # C-4: "재연·요약 금지, 새 인물 창조 금지" → 긍정 전환(새 재료 전진 + 등장인물 whitelist). 의도 보존.
        spec = SceneSpec(index=1, goal=(
            "위 '지금까지 쓴 본문'의 마지막 문장에서 '즉시' 이어서, 이야기를 다음 국면으로 한 단계 전진시켜 계속 써라. "
            "장면마다 직전에 없던 새 사건·새 정보로 나아가고, 인물은 이미 등장한 이들만으로 끌고 가라. "
            "새 절단점에 이르면 멈춰라." + plan_ctx), key_events=[])
        # 이어쓰기 프롬프트에는 직전 회차 전문·누적 줄거리를 빼고 '지금 쓴 본문 말미'만 준다 —
        # 둘이 함께 들어가면 모델이 요약된 과거를 '재서술'하는 압력(재설계 리뷰 P0-2). 캐논(ground_truth)·보이스는 유지.
        # SY-1 §5(2차 감사 M-D): confirmed_story 명시 배타 — 이어쓰기에 전량 블록+잔여 블록이
        #   동시에 붙는 2중 노출 차단. 잔여 블록(story_remaining)은 generate() 계산분만 싣는다.
        cont_board = board.model_copy(update={"prev_chapter": "", "story_so_far": "",
                                              "confirmed_story": "", "story_remaining": story_remaining})
        # C-4: "(재출력 금지, 머리말·메타 금지)" → 긍정 계약("마지막 문장 바로 다음부터 새로 잇는 본문 그것 하나뿐").
        #      부호도 whitelist 로(영어식 부호 호명 제거). 선언문('끼어들 자리가 없다')은 B-24 프레이밍이라 유지.
        if getattr(self.style, "structured_prompt", False):
            # VX-1(감사관 C2·M4): 이어쓰기 콜에도 대사 태그 계약을 동일 축으로 재명시(형식 계약 모든 변환 콜 유지).
            out_instr = ("\n\n출력은 위 본문의 '마지막 문장 바로 다음'부터 새로 잇는 본문 그것 하나뿐이다. 첫 글자부터 산문으로 시작하되, 인물이 소리 내어 하는 말은 각각 <대사 화자=\"이름\">…말…</대사> 로 감싸 쓴다. "
                         "지문·서술의 문장부호는 한국어 관습만 쓴다. 끊어 읽기·삽입구·여운은 쉼표·마침표·말줄임표(…)로 처리하라. "
                         "모순·인물/인원 불일치를 발견하면 한쪽으로 자연스럽게 확정해 서술하라. 본문에는 작업 메모·괄호 주석이 끼어들 자리가 없다.")
        else:
            out_instr = ("\n\n출력은 위 본문의 '마지막 문장 바로 다음'부터 새로 잇는 본문 그것 하나뿐이다. 첫 글자부터 산문으로 시작하라. "
                         "문장부호는 한국어 관습만 쓴다. 끊어 읽기·삽입구·여운은 쉼표·마침표·말줄임표(…)로 처리하라. "
                         "모순·인물/인원 불일치를 발견하면 한쪽으로 자연스럽게 확정해 서술하라. 본문에는 작업 메모·괄호 주석이 끼어들 자리가 없다.")
        if ch == 1:   # P-1①: ch1 이어쓰기에도 발단 결 관통 — _draft out_instr 의 ch1 hook 과 동형·짧게(T2: 강제 아닌 결). '이어쓰는 중에도' 발단 grounding 유지.
            out_instr += ("\n\n(이 회차는 작품의 첫머리다 — 이어지는 이 대목에서도 주인공이 '누구'인지 손에 잡히는 구체 장면으로 세우는 결을 유지하고, "
                          "그 위로 전제의 전환이 발발하는 데까지 굴려라. 첫 전환의 여운이나 미결의 물음을 남겨 이어질 여지를 두라.)")
        style_block = self.style_block_opening if ch == 1 else self.style_block   # P-1①: 1화 이어쓰기도 발단 변형 rule⑤ (비1화 바이트 동일)
        craft_block = self.craft_block_opening if ch == 1 else self.craft_block   # P-1②: 1화 이어쓰기도 발단 호환 craft (비1화 바이트 동일)
        return self.provider.chat(
            [{"role": "system", "content": f"{self.style.system_persona} 확정 설정 절대 위반 금지.\n{style_block}{self.style_fewshot_block}"},   # SP-1 Stage A: 이어쓰기(continue)에도 동일 few-shot 주입(OFF면 "" → 바이트 동일)
             {"role": "user", "content": self.assembler.assemble(cont_board, spec, sofar[-3500:]) + hook
              + out_instr
              + self.obsession_block + craft_block + self._cur_scene_inject + self._cur_skill_inject
              + style_state}],   # ST-12b: 러닝 카운터 상태블록(대역 밖일 때만 비어있지 않음 — ""면 바이트 동일)
            temperature=0.85)   # 온도↓는 클리셰(고확률 토큰)를 오히려 늘릴 수 있어 보류 — 측정 후 재검토

    @promptlog.stage("rewrite")
    def _rewrite(self, scene_text, violations, board) -> str:
        vlist = "\n".join(f"- [{v.kind}/{v.grade.value}] {v.entity}: 캐논={v.canon} 위반={v.text}" for v in violations)
        gt = "\n".join(f"- {f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth)
        # GN-2: ch1 발단 결 관통 — 캐논 교정이 발단 grounding 을 사건 한복판 개시로 흩뜨리지 않게 보존 한 줄(비ch1 "" → 바이트 동일).
        opening_hint = _OPENING_CORR_HINT if getattr(board, "chapter", 0) == 1 else ""
        out = self.provider.chat(
            [{"role": "system", "content":
              "교정 작가. **출력은 정정된 '소설 본문 전문' 그것 하나뿐이다 — 첫 글자부터 소설 산문으로 시작하고, "
              "위반 분석·점검·판단·설명·머리말·체크리스트·메모를 절대 붙이지 마라(그런 사고 과정은 머릿속에서만).** "
              "지적된 설정 위반만 고치고 문체·줄바꿈·분량은 보존. 확정 설정·세계규칙이 비트보다 우선. "
              "'제거 상태'(사망·소멸 등) 인물의 현재 행동은 회상/환영/삭제로, 속성·관계는 캐논값으로 교정.\n"
              + self.floor_block + opening_hint},   # B-10: 미학 오버레이(작가 문체) 주입 제거 — 최소 교정이 재문체화로 번지는 것 차단
             {"role": "user", "content": f"[확정 설정]\n{gt}\n[설정 위반]\n{vlist}\n[원본 본문]\n{scene_text}"}],
            temperature=0.4)
        # diff 한도 게이트(R2 '본문파괴' 교훈을 코드 게이트로): 절단/메타응답(너무 짧음)뿐 아니라
        # 지정 위반 범위를 넘어 좋은 본문까지 갈아엎는 과잉 재작성(너무 다름)도 거부 → 원본 유지.
        if within_correction_bounds(scene_text, out or "",
                                    max_drift=getattr(self.settings, "correction_max_drift", 0.6)):
            return out
        self.bus.emit("partial_rewrite", "correction_rejected", chapter=getattr(board, "chapter", 0),
                      drift=round(drift_ratio(scene_text, out or ""), 2),
                      in_chars=len(scene_text), out_chars=len(out or ""))
        return scene_text

    @promptlog.stage("reformat")
    def _reformat(self, text: str) -> str:
        """토막 행갈이 붕괴 복구 — 내용 불변, 조판만 정상 산문으로(문체 드리프트 루프 차단).
        출력이 원문의 60% 미만이면 절단/메타응답으로 보고 원문 유지(본문 파괴 가드)."""
        out = self.provider.chat(
            [{"role": "system", "content":
              "조판 교정자. 내용·문장·대사를 단 한 글자도 바꾸지 말고, 과도하게 토막난 행갈이만 정상 산문으로 재조판하라. "
              "대사는 한 줄에 하나 유지, 지문은 2~4문장 문단으로 묶어라. 본문만 출력."},
             {"role": "user", "content": text}],
            temperature=0.0)
        # 계약(공백/행갈이만 변경)은 코드로 '완전' 검증 가능 — 길이 60% 차용 가드(40% 손실 허용) 폐기
        if out and re.sub(r"\s+", "", out) == re.sub(r"\s+", "", text):
            return out
        self.bus.emit("draft_chapter", "reformat_rejected", out_chars=len(out or ""), in_chars=len(text))
        return text

    @promptlog.stage("fix_tense")
    def _fix_tense(self, chapter_text: str) -> str:
        """현재형 종결('~ㄴ다.') → 과거형 교정. 패치 방식(find 정확일치·count==1) — 한국어 시제는
        형태론적 변환(간다→갔다, 불규칙 다수)이라 맹목 정규식 치환 금지, 변환만 LLM·적용은 코드."""
        from .quality_gates import tense_leak_ratio
        prose = [ln for ln in chapter_text.splitlines()
                 if ln.strip() and not ln.strip().startswith(('"', '\u201c', '\u2014'))]
        sents = [t for ln in prose for t in re.split(r"(?<=다\.)\s+", ln)
                 if t.strip().endswith("다.") and re.search(r"(?<![었았])[는한온간운인난된친낀]다\.$", t.strip())][:25]
        if not sents:
            return chapter_text
        try:
            res = self.provider.chat_json(
                [{"role": "system", "content":
                  "한국어 시제 교정기. 아래 문장들의 종결을 과거형으로 바꿔라(내용·어순 불변, 종결어미만). "
                  '본문 구절 정확 치환 쌍으로. JSON: {"fixes":[{"find":"","replace":""}]}'},
                 {"role": "user", "content": "\n".join(f"- {t.strip()}" for t in sents)}],
                temperature=0.0)
            n = 0
            for fx in (res.get("fixes") or [])[:25]:
                f, r = fx.get("find") or "", fx.get("replace") or ""
                if f and r and f != r and chapter_text.count(f) == 1:
                    chapter_text = chapter_text.replace(f, r, 1)
                    n += 1
            self.bus.emit("quality_gate", "tense_fixes", applied=n,
                          leak_after=round(tense_leak_ratio(chapter_text), 3))
        except Exception:
            self.bus.emit("quality_gate", "tense_fix_failed")
        return chapter_text

    @promptlog.stage("continuity_polish")
    def _continuity_polish(self, chapter_text: str, names: list[str] | None = None, ch: int = 0) -> str:
        """회차 내부 연속성 교정 — 패치 방식: LLM 은 '수정 쌍'만 내고 코드가 정확일치 치환만 적용.
        (전량 재작성 방식은 메타응답이 본문을 덮어쓰거나 max_tokens 절단으로 본문을 파괴했음 — 시뮬 실측 결함.
        패치 방식은 본문이 원형 그대로라 증발·절단·분량손실이 구조적으로 불가능.)
        GN-2: ch(회차 번호) 관통 — ch==1 이면 발단 grounding 보존 한 줄을 검수 지시에 얹는다(비ch1 기본 0 → "" → 바이트 동일)."""
        try:
            roster = f"[공식 인명: {', '.join(names or [])}]\n" if names else ""
            opening_hint = _OPENING_CORR_HINT if ch == 1 else ""   # GN-2: 발단 결 보존(비ch1 "" → 프롬프트 바이트 동일)
            res = self.provider.chat_json(
                [{"role": "system", "content":
                  "출고 검수자. 이 회차에서 다음 결함만 찾아라: ①수치(배터리·산소·식량·GPU·시간)·소지품·위치, "
                  "그리고 인원수/개수/횟수 등 '수량 주장과 실제 묘사의 불일치'(예: '다섯 겹'이라 했는데 셋만 등장, 'N명'이라 선언하고 실제 등장 수가 다름)의 회차 내 모순 "
                  "②공식 인명의 오염(한 글자 다른 오타) ③문두/문중 절단된 깨진 문장 ④화자의 자기 언급 메타문장(예: '이 장의 끝에') "
                  "⑤같은 부사구·관용구('짧게 말했다','그 순간' 등)의 과도 반복(가장 잦은 것 일부를 동의 표현으로) ⑥현재형 시제 누출(과거형으로) ⑦장면 이어붙임 중복 — 같은 사건/탈출이 두 번 재생되거나 파괴된 것이 무설명 부활하면 중복 단락을 짧은 경과 문장으로 치환. "
                  "수정은 본문 구절의 정확한 치환 쌍으로만(find=본문에 실제 있는 구절 그대로). "
                  '없으면 빈 배열. JSON: {"fixes":[{"find":"","replace":""}]}'
                  + opening_hint},
                 {"role": "user", "content": roster + chapter_text}],
                temperature=0.0)
            n = 0
            for fx in (res.get("fixes") or [])[:20]:
                f, r = (fx.get("find") or ""), (fx.get("replace") or "")
                if len(f) >= 6 and f in chapter_text and r and r != f:
                    chapter_text = chapter_text.replace(f, r, 1)
                    n += 1
            if n:
                self.bus.emit("finalize", "continuity_fixes", count=n)
        except Exception:
            pass
        return chapter_text

    # ---- 퇴고(작가지시 프로즈 다듬기 — 사실 불변) ----
    @promptlog.stage("revise")
    def revise_prose(self, directive: str, before_text: str, span_text: str = "",
                     passes: list[str] | None = None, ids: list[str] | None = None,
                     ontology=None, chapter_no: int = 0, skills_inject: str = "") -> str:
        """작가 지시에 따라 회차 본문(또는 구간)의 *산문*만 다듬는다 — 사실 불변.

        설정·사건·수치·관계·캐논은 단 하나도 바꾸지 않고 표현·문체·가독성·대사 톤·반복·조판만 개선한다.
        D1 준수: _continuity_polish·_regen_tail·_fix_tics·_rewrite 는 '사실 변경 패스'라 여기서 절대 호출 금지.
        opt-in 선택 교정은 _reformat(공백/조판)·_fix_tense(종결어미)만 허용한다.
        span_text 가 있으면 그 구간만 다듬어 원문의 해당 위치에 replace(저장 단위는 회차 전체 텍스트).
        """
        passes = passes or []
        self._last_revise_cause = "ok"   # RV-1②: 원인 사이드채널 리셋(각 반환점에서 갱신)
        # 1) span 모드: 공백 collapse 정규화로 before_text 에서 정확히 1회 매칭 확인(렌더 복사 공백 차이 흡수)
        span_norm = ""
        anchor = ""               # before_text 안의 실제(원형) 매칭 구절 — replace 대상
        ctx_prefix = ctx_suffix = ""
        if span_text:
            span_norm = re.sub(r"\s+", " ", span_text).strip()
            anchor = self._find_span(before_text, span_norm)
            if anchor is None:
                raise ValueError("span_not_found")
            i = before_text.find(anchor)
            ctx_prefix = before_text[max(0, i - 200):i]                 # 앞뒤 문맥 윈도(접합부 조사/접속 불일치 보강)
            ctx_suffix = before_text[i + len(anchor): i + len(anchor) + 200]
            target_text = anchor
        else:
            target_text = before_text

        # 2) LLM 다듬기 1콜(사실 불변 프롬프트)
        scope = "구간" if span_text else "전체"
        sys = (f"교정 작가. 작가 지시에 따라 회차 산문의 {scope}을(를) 다듬어라. "
               "이 작업은 '문체 다듬기'이지 '설정 변경'이 아니다. "
               "인물·사건·수치·관계·설정은 단 하나도 바꾸지 마라(이름·날짜·숫자·생사·소속·등급·능력·관계 불변). "
               "원문에 없던 사실을 새로 단정하지도 마라 — 등급·수치·소속·생사·능력·관계를 본문·대사·시스템 메시지·경고창·상태표시·배경묘사 등 어떤 형태로도 추가·암시하지 마라. "
               "작가 지시가 이런 사실을 바꾸거나 새로 박으라고 요구하더라도 그 요구는 따르지 말고, 지시 중 '사실을 건드리지 않는' 표현·가독성 개선만 반영하라. "
               "표현·문체·가독성·대사 톤·반복·조판만 개선하라. 본문만 출력(머리말·설명·메타 금지).")
        # 사실불변 1차 방어선: 확정 캐논 사실을 프롬프트에 결정론 주입(가드레일은 사후 거절, 이건 사전 바인딩)
        if ontology is not None and ids:
            try:
                facts = ontology.canon_facts(ids, chapter_no)
            except Exception:
                facts = []
            if facts:
                sys += ("\n[확정 캐논 — 이 값들은 절대 바꾸지 마라]\n"
                        + "\n".join(f"{f.entity}: {f.attr_label}={f.value}" for f in facts))
        if span_text:   # 구간 모드: 앞뒤 문맥은 참고만, 출력은 구간 다듬은 것만(문맥 재출력 금지)
            sys += " 제공된 앞뒤 문맥은 톤·연결을 맞추기 위한 참고일 뿐, 출력에 포함하지 마라."
        user = (f"[작가 지시] {directive}\n"
                + (f"[앞 문맥(참고)]\n…{ctx_prefix}\n[뒤 문맥(참고)]\n{ctx_suffix}…\n" if span_text else "")
                + f"[원문]\n{target_text}" + (skills_inject or ""))   # 켠 퇴고 스킬(어댑터) — 사실불변 sys 가드는 유지
        try:
            out = self.provider.chat(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": user}],
                temperature=0.4)
        except Exception:
            self.bus.emit("revise", "llm_failure")
            self._last_revise_cause = "llm_failure"
            return before_text

        # 3) 길이 가드(구간 기준) — 절단/메타응답이 본문을 갉아먹는 회귀 차단. 위반 시 원문 유지
        out = (out or "").strip()
        if not out or len(out) < len(target_text) * 0.5 or len(out) > len(target_text) * 1.8:
            self.bus.emit("revise", "length_guard_triggered",
                          in_chars=len(target_text), out_chars=len(out))
            self._last_revise_cause = "length_guard"
            return before_text

        # 4) 메타텍스트 라인 제거(생성물과 동일 결정론 살균)
        out = sanitize_meta(out)
        if not out:
            self._last_revise_cause = "empty_after_sanitize"
            return before_text

        # 5) opt-in 선택 교정(D1: reformat·fix_tense 만. continuity/regen_tail/tics 절대 호출 금지)
        if "reformat" in passes:
            out = self._reformat(out)
        if "fix_tense" in passes:
            out = self._fix_tense(out)

        # 6) span 모드면 원문의 해당 위치에 replace → 전체 텍스트 복원, 전체 길이 가드 재검
        if span_text:
            result = before_text.replace(anchor, out, 1)
        else:
            result = out
        if (not result.strip() or len(result) < len(before_text) * 0.5
                or len(result) > len(before_text) * 1.8):
            self.bus.emit("revise", "length_guard_triggered",
                          in_chars=len(before_text), out_chars=len(result))
            self._last_revise_cause = "length_guard"
            return before_text
        # RV-1②: 길이가드 통과했으나 실질 무변경(모델이 near-identical 반환 — copilot.py:659 after==before 분기와 정합).
        #   "length_guard" 폴백과 구분해 '지시가 원문에 이미 반영/변경할 여지 없음'을 정직하게 표면화.
        self._last_revise_cause = "unchanged" if result == before_text else "ok"
        return result

    @staticmethod
    def _find_span(before_text: str, span_norm: str) -> str | None:
        """공백 collapse 정규화 기준으로 before_text 에서 span_norm 과 일치하는 '원형' 구절을 찾는다(정확히 1회만).
        렌더 복사로 공백/행갈이가 달라진 span 도 매칭하되, 0회·2회 이상이면 None(모호 → 호출부가 400)."""
        if not span_norm:
            return None
        # 먼저 정확 부분문자열(가장 흔한 경우 — 빠른 경로)
        if before_text.count(span_norm) == 1:
            return span_norm
        # 공백 차이 흡수: before_text 의 공백류를 \s+ 로 푼 정규식으로 원형 구절 역추적.
        # re.DOTALL 명시 — \s 는 이미 \n 을 포함하나, 행갈이·탭 포함 구간 매칭 의도를 유지보수상 명시.
        pat = re.compile(r"\s+".join(re.escape(tok) for tok in span_norm.split(" ") if tok), re.DOTALL)
        matches = pat.findall(before_text)
        if len(matches) == 1:
            return matches[0]
        return None

    # ---- 회차 생성 ----
    @promptlog.stage("summarize")
    def _summarize(self, chapter_text: str, prior_summary: str = "",
                   beat: dict | None = None) -> tuple[str, str, dict | None]:
        """2계층 요약(컨텍스트 기아 해소 ①): (한 줄, 상세 시놉시스 ~1500자, degraded_info).
        기존 348자 단일 요약은 보존율 5.4% — 상세 레이어가 사건 인과·감정 변화·물리 디테일·미결을 보존(~25%).

        P-2 실패 폴백 소스차단: LLM 실패(절단/빈응답/파싱) 시 폴백을 '본문 서두 슬라이스'가 아니라 이 회차 beat 계획의
        summary+key_events(하네스가 이미 보유·프로즈 서두 아님)로 합성한다 → story_so_far 재유입돼도 재상연 불가.
        beat 부재(퇴고 재요약 등) 시에만 최후로 본문 슬라이스 폴백을 쓰되, 어느 경우든 degraded_info 를 반환해 소비부가 신뢰 강등.
        반환 degraded_info: 성공 시 None, 실패 시 {"degraded": True, "failure": "truncated|empty|parse_failure"}."""
        failure = None
        try:
            r = self.aux_provider.chat_json(   # CE-1 ⓑ: 재요약=aux(저가). 미주입이면 aux_provider==provider(바이트 동일)
                [{"role": "system", "content":
                  "웹소설 회차 기록자. 두 가지를 만들라:\n"
                  # RC-5: oneliner 도 rollup·에피소드 요약을 거쳐 story_so_far 로 재주입되는 프로즈 앵커라
                  #   (harness _rollup·copilot _chapter_gist_for_ssf), 한 문장이 '무엇이 바뀌었나'(핵심 사건·전환)를
                  #   담게 긍정 지정 — 수사+단위 나열로 한 문장이 채워지는 되먹임(원장 12번·15화 한줄 요약에 수사+단위
                  #   5건 실측)의 소스를 사건 축 앵커로 옮긴다.
                  "1) oneliner: 이 회차에서 무엇이 바뀌었는지, 핵심 사건과 전환을 한 문장으로 담아라(나중 회차에서 원거리 기억용).\n"
                  # PA-1 감사 반영: 설계 용어('복선')와 대시 시연 제거 — 시놉시스는 story_so_far 로 매 화
                  #   재주입되는 프로즈 앵커라 기획서 말투·구두법이 본문으로 전이된다(EM-1·기획서 말투 실측).
                  # RC-5: 구 계약의 "물리 디테일(자원·소지품·위치·시각) 열거" 지시가 수 기반 세계 장치 작품(원장 12번)에서
                  #   요약을 세기 나열체로 수렴시킨다 → story_so_far·syn_prev·메뉴 3경로로 재주입돼 다음 회차 본문·계획을 재앵커.
                  #   조직 축을 '무엇이 왜 일어났나'(사건 인과·인물 결정)로 두고, 물리·수치 사실은 그것을 낳은 사건의
                  #   인과 안에서 밝히게 한다. P-2 기아 해소는 '범주 호명'이 아니라 기능형 절("상태를 오인하지 않도록 필요한
                  #   값을 사실 그대로 보존")이 담당한다.
                  #   prompt-auditor F1: '자원 상태' 같은 차단 대상 범주를 문안에 호명하면 이 작품처럼 재고가 강한 어트랙터일 때
                  #     모델이 도구 카운트를 '값 자체가 정보'로 판정해 보존하고(핑크엘리펀트 백도어), rerender:201 '수치는 뼈대
                  #     그대로 실현'이 그 나열형 시놉시스를 프로즈로 결정론 전이시킨다 → 범주 호명 삭제(P-2 손실 없음).
                  #   prompt-auditor F2: '본문 없이 이어 쓸 밀도'(상한 없는 완전 재구성)를 '사건 흐름을 정확히 이어받도록'으로
                  #     완화 — 저사건·고재고 회차에서 1,200자 채우는 최단 경로가 재고 나열이 되는 것을 차단.
                  #   긍정형만·기피어 0·특정 도구/수 하드코딩 0(genre-blind).
                  "2) synopsis: 상세 시놉시스 1,200~1,600자. 이 회차에서 무슨 일이 왜 일어났는지를 축으로, "
                  "사건의 인과 순서, 인물별 결정과 그 동기, 감정 변화, 대화로 오간 핵심 판단, "
                  "그리고 아직 답이 나오지 않은 채 남은 문제를 이어 써라. "
                  "이 회차가 무엇을 어떻게 바꿔 놓았는지, 그 변화가 언제 어디서 일어났는지를 그것을 낳은 사건의 "
                  "인과 안에서 밝히고, 다음 화가 상태를 오인하지 않도록 필요한 값을 사실 그대로 보존하라. "
                  "다음 회차 집필자가 이 회차의 사건 흐름을 정확히 이어받도록, 사실·사건 정보만 담아라. "
                  # VP-1: 시놉시스가 story_so_far 로 매 회차 재주입되는 프로즈 앵커라, 본문이 조각체여도
                  #   요약은 완결문 산문으로 고정(파편체 되먹임 차단 — 적대 리뷰 경미 9).
                  "두 항목 모두 서술어로 완결된 문장의 산문으로 이어 써라.\n"
                  'JSON: {"oneliner":"","synopsis":""}'},
                 {"role": "user", "content":
                  (f"[직전까지 줄거리]\n{prior_summary[-1200:]}\n\n" if prior_summary else "")
                  + f"[이번 회차 본문]\n{chapter_text}"}],
                temperature=0.2)
            one = (r.get("oneliner") or "").strip()
            syn = (r.get("synopsis") or "").strip()
            if one or syn:
                one_eff, syn_eff = (one or syn[:160]), (syn or chapter_text[:400])
                # RC-5 F3ⓑ(prompt-auditor): 산출 synopsis 의 수사+단위 밀도를 결정론 사후 계측해 advisory 관측.
                #   프롬프트에 검출어를 덧대는 두더지잡기가 아니라 *산출물 계측*이다(판정·임계·자동수정 0). RC-5 kill
                #   criteria(요약 세기 나열 재발)의 계측 축 — 얇게 끝난 산출도 재고 밀도를 봐야 하므로 길이 게이트 앞에서 잰다.
                self._sweep_counting_density(syn_eff, beat)
                # RC-5 F3ⓐ(prompt-auditor): 산출 길이 하한 게이트 — 저사건·고재고 회차의 얇은 나열형 산출을 degraded
                #   신호로 가시화(폴백 계약과 정합·신뢰 강등). 내용은 보존하되 소비부(_chapter_recap)가 detail 을 신뢰 강등.
                if len(syn) < _SUMMARY_SYNOPSIS_FLOOR:
                    self.bus.emit("summarize", "underlength", chars=len(syn), floor=_SUMMARY_SYNOPSIS_FLOOR)
                    return one_eff, syn_eff, {"degraded": True, "failure": "short"}
                return one_eff, syn_eff, None
            # 빈 응답 — 절단(max_tokens)로 내용이 안 나온 경우와 진짜 빈 응답을 provider 신호로 구분(③ 실패 유형 영속).
            failure = "truncated" if getattr(self.aux_provider, "last_truncated", False) else "empty"   # CE-1 ⓑ: aux 신호(재요약 콜 소유자)
            self.bus.emit("summarize", "empty_response", failure=failure)
        except Exception:
            failure = "parse_failure"
            self.bus.emit("summarize", "parse_failure")
        # ── P-2 ① 폴백 합성: 프로즈 서두(text[:N]) 대신 beat 계획으로 사건 요지 문자열 합성(LLM 0콜) ──
        syn_txt = self._synth_summary_from_beat(beat)
        if syn_txt:
            return syn_txt[:160], syn_txt, {"degraded": True, "failure": failure}
        # beat 부재(퇴고 재요약 등) → 최후 폴백은 기존 본문 슬라이스 유지하되 degraded 플래그 필수(②의 소비부가 신뢰 강등)
        return chapter_text[:200], chapter_text[:400], {"degraded": True, "failure": failure}

    @staticmethod
    def _synth_summary_from_beat(beat: dict | None) -> str:
        """P-2 ①: 요약 LLM 실패 시 이 회차 beat 계획(summary+key_events)에서 사건 요지 문자열을 결정론 합성.
        프로즈 서두가 아니므로 story_so_far 로 재유입돼도 모델이 '이미 쓴 문장'으로 재상연할 소스가 없다.
        beat 부재·요지 부재 시 빈 문자열(호출부가 최후 폴백 판단)."""
        if not beat:
            return ""
        summary = (beat.get("summary") or "").strip()
        key_events = [e.strip() for e in (beat.get("key_events") or []) if e and e.strip()]
        parts: list[str] = []
        if summary:
            parts.append(summary)
        if key_events:
            parts.append("주요 사건: " + "; ".join(key_events))
        return " / ".join(parts)

    def _sweep_counting_density(self, text: str, beat: dict | None = None) -> None:
        """RC-5 F3ⓑ(prompt-auditor): 산출 synopsis 의 수사+단위 토큰 밀도를 결정론 계측해 advisory 로 관측(bus).
        프롬프트에 검출어를 덧대는 두더지잡기가 아니라 *산출물 사후 계측*이다 — 판정·임계·자동수정 0, 헤드라인 금지.
        rerender 수치 토큰 계보(extract_numeric_tokens, arabic_only=False = advisory 전용 계약) 재사용 → 신규 regex 0.
        RC-5 kill criteria(요약 세기 나열 재발)의 계측 원자료. 관측 실패가 요약 산출을 막지 않도록 흡수."""
        try:
            from .rerender import extract_numeric_tokens
            chars = len(text or "")
            n = len(extract_numeric_tokens(text or "", arabic_only=False))   # 수사+단위·아라비아 결합 토큰(중복 제거)
            per_1k = round(n / chars * 1000, 2) if chars else 0.0
            self.bus.emit("summarize", "counting_density",
                          chapter=(beat or {}).get("chapter"),
                          unique_num_tokens=n, per_1k=per_1k, chars=chars)
        except Exception:
            pass

    @promptlog.stage("fix_tics")
    def _fix_tics(self, chapter_text: str, offenders: list[tuple[str, int]], ch: int = 0) -> str:
        """틱 과용 국소 교정 — 위반 구절을 '명시'하고 패치(고유 구절 치환)로만 다양화. 전량 재생성 금지.
        GN-2: ch 관통 — ch==1 이면 발단 grounding 보존 한 줄을 다양화 지시에 얹는다(비ch1 기본 0 → "" → 바이트 동일)."""
        try:
            lst = ", ".join(f"'{p}'({n}회)" for p, n in offenders)
            opening_hint = _OPENING_CORR_HINT if ch == 1 else ""   # GN-2: 발단 결 보존(비ch1 "" → 프롬프트 바이트 동일)
            res = self.provider.chat_json(
                [{"role": "system", "content":
                  "문장 다양화 검수자. 다음 습관구가 과용됐다: " + lst + ". "
                  "각 습관구의 출현 대부분을 문맥에 맞는 다양한 표현으로 바꿔라(치환어끼리도 서로 다르게 골라라). "
                  "수정은 치환 쌍으로만 — find 는 본문에 실제로 1번만 나오는 7자 이상 고유 구절(습관구 포함)을 그대로. "
                  '인명·사건·수치 불변. JSON: {"fixes":[{"find":"","replace":""}]}'
                  + opening_hint},
                 {"role": "user", "content": chapter_text}],
                temperature=0.3)
            n = 0
            for fx in (res.get("fixes") or [])[:30]:
                f, r = (fx.get("find") or ""), (fx.get("replace") or "")
                if len(f) >= 7 and chapter_text.count(f) == 1 and r and r != f:
                    chapter_text = chapter_text.replace(f, r, 1)
                    n += 1
            self.bus.emit("quality", "tic_fixes", applied=n, offenders=[p for p, _ in offenders])
        except Exception:
            pass
        return chapter_text

    @promptlog.stage("regen_tail")
    def _regen_tail(self, chapter_text: str, recent_tails: list[str], ch: int = 0) -> str:
        """말미 훅 재탕 → 마지막 1~2문단만 재작성해 접합(국소) — 본문 보존.
        GN-2: ch 관통 — ch==1 이면 발단 grounding 보존 한 줄을 말미 교체 지시에 얹는다(비ch1 기본 0 → "" → 바이트 동일).
        (1화는 앞 문맥이 발단 도입이므로, 말미를 다른 축으로 바꿔도 발단의 결에서 자란 끝이 되게 한다.)"""
        lines = chapter_text.splitlines()
        keep, tail = "\n".join(lines[:-8]), "\n".join(lines[-8:])
        try:
            prev = "\n".join(f"- …{t[-70:]}" for t in recent_tails[-4:])
            opening_hint = _OPENING_CORR_HINT if ch == 1 else ""   # GN-2: 발단 결 보존(비ch1 "" → 프롬프트 바이트 동일)
            out = self.provider.chat(
                [{"role": "system", "content":
                  "회차 말미 교체 작가. 주어진 끝부분을, 직전 회차들과 '다른 축'의 긴장(위기/반전/폭로/결단/관계 균열 중 "
                  "최근 미사용 축)으로 다시 써라. 길이 비슷하게, 앞 내용과 자연 연결. 본문만.\n[최근 회차들의 끝]\n" + prev
                  + opening_hint},
                 {"role": "user", "content": f"[앞 문맥(마지막 부분)]\n{keep[-800:]}\n\n[교체할 끝부분]\n{tail}"}],
                temperature=0.8)
            if out and len(out.strip()) > 80:
                self.bus.emit("quality", "tail_regen")
                return keep + "\n" + sanitize_meta(out.strip())
        except Exception:
            pass
        return chapter_text

    @staticmethod
    def _name_anchor_cards(entities, involved=None) -> str:
        """T5-R1: 등록 고유명 표기-고정 앵커. **등록된 이름은 tier 무관 '같은 표기 유지'**(드리프트 소스 차단) —
        provisional 은 거버넌스 tier(캐논 단정 가부)일 뿐 호명 억제 신호가 아니다. 융합하면 AI 발명 조연이
        '반복 호명 마라'로 프롬프트에서 드리프트를 유발했음. 확정=캐논 단정 가능, 미확정=표기만 고정하되 새 정체·설정 단정 금지(떡밥 개방).

        RR-1 프로즈 노출 차단: 이 카드는 *프로즈 생성 입력*이다. 아직 미등장(introduced=False)이고 이번 회차 비트
        캐스트(involved)에도 없는 인물은 명부에서 제외한다 — 데뷔 전 인물의 이름/역할이 본문 생성 프롬프트에 새 나가면
        엔진이 미리 등장시켜 콜드 드롭·캐논 유출을 낳는다(괴담작 재실현 사건의 프로즈 채널 대응). 필터는 프로즈 입력에만
        걸린다(플래너·검증은 별도 경로로 전체 명부 접근 유지 — 여기서 안 건드림). provisional(이미 본문에 등장·자동
        커밋)은 등장한 대상이므로 introduced 취급(유지). involved 미제공(None)이면 필터 무동작(하위호환·바이트 동일)."""
        cast = set(involved or [])

        def _visible(e):
            # 데뷔 전(introduced=False)이고 이번 화 캐스트도 아니면 프로즈 명부에서 제외. provisional=등장한 대상(유지).
            if involved is None:
                return True
            if getattr(e, "introduced", False) or getattr(e, "provisional", False):
                return True
            return getattr(e, "id", None) in cast

        vis = [e for e in entities if _visible(e)]
        confirmed = sorted({e.name for e in vis if e.name and not getattr(e, "provisional", False)})
        prov = sorted({e.name for e in vis if e.name and getattr(e, "provisional", False)})
        out = ""
        if confirmed:
            out += ("\n[이미 등록된 고유명사(참고: 같은 대상이면 이 표기를 그대로 쓰라)]\n" + ", ".join(confirmed[:60]))
        if prov:
            out += ("\n[미확정 명사(이미 본문에 등장·등록된 대상. 다시 가리킬 땐 이 표기를 그대로 유지하고 새 이름을 발명하지 마라. "
                    "다만 아직 정체·설정 미확정이니 새 배경·정체를 단정하거나 강제로 전개하지는 마라)]\n" + ", ".join(prov[:30]))
        return out

    @staticmethod
    def _resolve_dangling(involved, ontology, ctx_text, rag, ch_no, cap: int = 2, per_k: int = 2):
        """CN-6: 비트가 지목한 인물 중 이름이 현 집필맥락(ctx_text)에 부재한 dangling 을 과거 검색으로 보강.
        반환 (list[RetrievedItem] 추가청크, list[str] 해소된 인물명). 트리거=이름 부재(구조 신호)이지 사전/어휘 아님.
        bounded: 최대 cap 인·인당 ≤per_k 청크. 이름 ≥2자 가드(memory: korean-agglutination-substring-pitfall) —
        1글자 이름은 흔한 형태소('강'⊂'강물')와 substring 충돌해 무관 청크를 유입시키므로 트리거 대상서 제외."""
        names = [nm for nm in (getattr(ontology.entities.get(i), "name", "") for i in involved)
                 if nm and len(nm) >= 2 and nm not in ctx_text]
        out, resolved = [], []
        for nm in list(dict.fromkeys(names))[:cap]:          # 중복 제거·등장순 유지·상한 cap
            hits = [r for r in rag.search(nm, ch_no - 1, k=per_k + 1) if nm in (r.text or "")][:per_k]
            if hits:                                          # 이름이 실제로 들어간 과거 청크만(top-k 유사도 잡음 배제)
                out += hits
                resolved.append(nm)
        return out, resolved

    def _run_style_judgment(self, text: str, ch_no: int,
                            stage_usage: dict, stage_time: dict, genre: str = "",
                            motif_candidates=None):
        """HZ-1 ①: 스타일 지각 판정 스테이지(매화 무조건·+1콜) — 낭독 체감으로 N-4 문말 수리 필요 여부·인용.

        판정 provider = config style_judge_model(교차 벤더 기본·향후 fable 지정 가능·create_role_provider 재사용).
        usage/time 를 'style_judge' 키로 계상(TM-1 대칭). 실패(콜/파싱)=None → 호출부(apply_style_judgment)가 N-4
        수리 스킵(보수·결측 정직). 판정 결과(needs_repair·인용 수·사유)를 이벤트로 가시화(은폐 금지·advisory).

        motif_candidates(HZ-2·선택): 회차 간 반복 모티프 후보 구절(detect_chapter N-3 findings 표층). 같은 판정
        콜에서 모티프도 판정(추가 콜 0) — judge_style 이 [참고 자료]로 동봉. 미전달이면 문말만(하위호환).

        반환 (judgment|None, skips) — skips 는 현재 apply_style_judgment 가 자체 기록하므로 빈 리스트(자리 유지)."""
        try:
            from .style_judge import judge_style
            from ..llm.factory import create_role_provider
            spec = (getattr(self.settings, "style_judge_model", "") or "").strip()
            #   빈값(""): 스왑 없이 gen provider(self.provider) 재사용 — aux_model/humanize_model 의 ""=스왑0 관례 동형
            #   (테스트 실 LLM 0 계약도 이 폴백으로 성립: 판정 provider 를 새로 세우지 않고 주입된 provider 를 쓴다).
            jp = self.provider if not spec else create_role_provider(self.settings, spec)
            _jb = jp.usage.chat_tokens
            import time as _time
            _jts = _time.monotonic()
            judgment = judge_style(jp, text, genre=genre, motif_candidates=motif_candidates)
            # style_judge 스테이지 usage/time 계상(TM-1 대칭). aux 합산과 무관한 별도 provider 델타(직접 키 기입).
            stage_usage["style_judge"] = stage_usage.get("style_judge", 0) + (jp.usage.chat_tokens - _jb)
            stage_time["style_judge"] = round(stage_time.get("style_judge", 0.0)
                                              + (_time.monotonic() - _jts), 1)
            self.bus.emit("style_judge", "done", chapter=ch_no,
                          needs_repair=bool(judgment and judgment.get("needs_repair")),
                          cited=len((judgment or {}).get("spans") or []),
                          reason=((judgment or {}).get("reason") or "")[:200])
            return judgment, []
        except Exception as e:
            self.bus.emit("style_judge", "failure", chapter=ch_no, error=str(e)[:200])
            return None, []

    def _run_narration_detect(self, text: str, ch_no: int,
                              stage_usage: dict, stage_time: dict, mode: str = "catchall") -> list:
        """HM-3: N-1(자기해설)·N-2(감정 명명) LLM 탐지 스테이지 — config humanize_llm_detect ON 시에만 호출.

        결정론 탐지(humanize_detect, LLM 0)가 못 내는 의미 축을 교차 벤더 판정 콜로 보강한다(+1콜/화).
        판정 provider = style_judge_model(gen≠judge·create_role_provider 재사용). usage/time 을
        'narration_detect' 키로 직접 계상(TM-1 대칭·humanize baseline 앞에서 호출돼 이중 계상 없음).
        산출 findings(N-1/N-2 스팬)를 반환 — 호출부가 detect_chapter findings 에 이어 붙여 humanize_spans 가
        기존 처방으로 국소 윤문한다. 실패(콜/파싱)→[](비차단·보수·결측 정직). advisory·무강제."""
        try:
            from .narration_detect import detect_narration_tells
            from ..llm.factory import create_role_provider
            spec = (getattr(self.settings, "style_judge_model", "") or "").strip()
            if not spec:   # gen≠judge 보장 불가(빈값=gen provider 재사용) → 스킵(은폐 금지·측정 4원칙)
                self.bus.emit("narration_detect", "skipped", chapter=ch_no, reason="no_cross_vendor_judge")
                return []
            jp = create_role_provider(self.settings, spec)
            _jb = jp.usage.chat_tokens
            import time as _time
            _jts = _time.monotonic()
            cap = int(getattr(self.settings, "humanize_llm_detect_max_spans", 40) or 40)   # 가시화 상한(수술 예산과 분리)
            found = detect_narration_tells(jp, text, max_spans=cap, mode=mode)
            _sk = "narration_detect" if mode == "catchall" else "narration_detect_precise"   # 모드별 비용 귀속(TM-1)
            stage_usage[_sk] = stage_usage.get(_sk, 0) + (jp.usage.chat_tokens - _jb)
            stage_time[_sk] = round(stage_time.get(_sk, 0.0) + (_time.monotonic() - _jts), 1)
            self.bus.emit("narration_detect", "done", chapter=ch_no, mode=mode, found=len(found),
                          n1=sum(1 for f in found if f.get("category") == "N-1"),
                          n2=sum(1 for f in found if f.get("category") == "N-2"))
            return found
        except Exception as e:
            self.bus.emit("narration_detect", "failure", chapter=ch_no, error=str(e)[:200])
            return []

    # ── XR-3 블록 계보(관측 전용·결정론·LLM 0) ──────────────────────────────────────
    # 왜: gen_context 는 '무엇이 들어갔나'의 내용 스냅샷이지 '어디서 왔나'가 아니다(005 §3 —
    #   "데이터의 전 소비 지점을 추적하지 않았다"가 4회 우회의 공통 소스). 각 draft 블록의 재료가
    #   어느 필드·ID·시점에서 왔는지만 컴팩트하게 남긴다(전문 중복 금지 — 전문은 draft_ctx 가 이미 보유).
    # 계약: ⓐ 쓰기 전용 — 어떤 프롬프트에도 유입되지 않는다(K3, test_xr3_no_backflow 가 강제).
    #      ⓑ 조립 경로 불변 — 계보를 위해 주입값 계산을 고쳐야 하는 지점은 기록을 포기하고 gap 으로
    #        결측을 정직 표기한다(K1). ⓒ LLM 콜 0(K4) — 전부 이미 메모리에 있는 값의 재정렬.
    # 배선 범위(002 §5 순서): 이번 티켓은 집필기 draft 콜만. continue/rewrite·플래너·Story Pass·
    #   worldgen·요약/추출·심사는 미배선이며 docs/llm-call-inventory.md 의 '계보' 열에 그대로 표기된다.
    @staticmethod
    def _lineage_gt(ontology, involved, ch_no, pull_actors, extra_facts, lookup_facts, lookups_log):
        """[확정 설정] 블록 계보 — 주입 리스트와 같은 순서(시계앵커 → 캐논사실 → 캐논관계 → 조회).

        주입값 계산(위 ground_truth 조립)은 한 줄도 건드리지 않고, 같은 인자로 with_lineage 재질의만
        한다(결정론 재계산 — 조립 경로 불변 계약 K1). 구 온톨로지 구현·테스트 페이크가 kwarg 를
        모르면 그 부분만 결측(gap)으로 남긴다."""
        items: list[dict] = []
        for f in (extra_facts or []):   # B-33 시계 파생 산술(story_clock 결정론 — LLM 산수 0)
            items.append({"src": "time_anchor", "id": getattr(f, "entity", ""),
                          "reason": "story_clock(time_delta 체인·결정론)"})
        gap = ""
        try:
            _, cf_lin = (ontology.canon_facts(involved, ch_no, actors_status_only=True, with_lineage=True)
                         if pull_actors else ontology.canon_facts(involved, ch_no, with_lineage=True))
            _, rel_lin = ontology.canon_relations(involved, ch_no, with_lineage=True)
            items += cf_lin + rel_lin
        except TypeError:
            gap = "canon_facts/canon_relations 가 with_lineage 미지원(구 구현체·페이크) — 캐논 항목 계보 결측"
        for q, _v in (lookup_facts or []):   # AG-1 선조회(gen_tools ON 일 때만 — OFF 면 빈 목록)
            idx = next((i for i, it in enumerate(lookups_log or []) if it.get("query") == q), None)
            items.append({"src": "lookup", "id": f"lookups_log[{idx}]" if idx is not None else "",
                          "query": q, "reason": "조회"})
        blk = {"block": "ground_truth", "items": items}
        # K1 결측 정직: 런타임 timeline 튜플이 TimelineEntry.provenance(machine|author)를 싣지 않는다
        #   (factory.build_ontology·session.rehydrate 직렬화에서 소실). 담으려면 튜플 형태를 바꿔야 하고
        #   그건 조립 경로 변경이라 기록을 포기한다 — 관계 엣지만 provenance 를 보존한다.
        blk["gap"] = ((gap + " · ") if gap else "") + \
            "노드 provenance(machine|author) 는 런타임 timeline 튜플에 미보존 — 결측 정직(엣지는 보존)"
        return blk

    def _draft_lineage(self, board, beat, ontology, narrative, involved, ch_no, pull_actors,
                       extra_facts, lookup_facts, lookups_log, world_rules, narrator, prev_chapter_text,
                       confirmed_story) -> dict:
        """draft 프롬프트(engine/prompts.py PromptAssembler.assemble)의 블록별 계보. 블록 순서는 조립 순서."""
        pv = prev_chapter_text or ""
        blocks: list[dict] = [
            {"block": "brief",
             "sources": [s for s in ("story_clock(time_delta 체인·결정론)" if board.story_time else "",
                                     "beat.key_events" if beat.get("key_events") else "") if s],
             "story_mode": bool(board.confirmed_story or board.story_remaining)},
            self._lineage_gt(ontology, involved, ch_no, pull_actors, extra_facts, lookup_facts, lookups_log),
            {"block": "world_rules",
             "items": [{"idx": i, "head": (r or "")[:20]} for i, r in enumerate(world_rules or [])],
             # K1: WorldRuleSpec.rule_id 는 factory.build_ontology 가 텍스트만 add_rule 하며 소실된다.
             #   되살리려면 ontology.rules 자료형을 바꿔야 하고 그건 조립 경로 변경 → 기록 포기·결측 정직.
             "gap": "rule_id 는 factory 직렬화에서 소실 — 결측 정직(K1: 조립 경로 불변)"},
            {"block": "authority",
             "items": [{"id": getattr(d, "directive_id", ""), "from_chapter": getattr(d, "from_chapter", None),
                        "head": (getattr(d, "text", "") or "")[:40]} for d in (board.authority or [])]},
            {"block": "voice_cards", "narrator": narrator,
             "cast": [i for i in involved
                      if getattr(ontology.entities.get(i), "voice", "") and i != narrator.get("eid", "")],
             "names_block": "표기 명부" in (board.voice_cards or "")},
            # contributors(기여 회차)는 서비스 계층(_build_story_so_far_hier)만 아는 값이라 여기선 결측 —
            #   copilot.generate_next_chapter 가 생성 직후 같은 블록에 채운다(엔진 단독 호출은 결측 유지).
            {"block": "story_so_far", "chars": len(board.story_so_far or ""),
             "contributors": None, "dropped": None,
             "gap": "기여 회차·트림 계수는 서비스 조립 소관 — 엔진 단독 호출(러너·테스트)에서는 결측"},
            {"block": "prev_chapter", "chapter": (ch_no - 1) if pv else None,
             "chars_total": len(pv), "chars_injected": min(len(pv), self.assembler.prev_chapter_chars),
             "gen_no": None, "revisions": None,
             "gap": "gen_no·리비전 수는 서비스가 회차 레코드에서 채운다(엔진은 원문 문자열만 받음)"},
            {"block": "narrative",
             "items": [{"source": r.source, "ref": r.ref} for r in (narrative or [])]},
            # 중복 기록 금지 — 확정 스토리 전문·digest 는 gen_context['story_pass'] 가 이미 보유(참조만).
            {"block": "confirmed_story", "present": bool(confirmed_story),
             "ref": "gen_context.story_pass"},
        ]
        return {"blocks": blocks, "wired": "draft", "scope": "XR-3 1차 배선 — draft 콜만(나머지 호출 미배선)"}

    def generate(self, ch_no, beat, ontology, rag, wiki, directives=None,
                 prev_chapter_text: str = "", story_so_far: str = "", anchors=None,
                 closing: bool = False, recent_tails=None, restraint=None, skills_inject: str = "",
                 story_time: str = "", extra_facts=None, prev_texts=None,
                 bible_entries=None, prev_ledgers=None, confirmed_story: str = "") -> ChapterRecord:
        # prev_texts(선택): HM-1 N-3 모티프 원장(회차 간 반복 구절) 입력용 선행 회차 본문 목록. 미제공(None)이면
        #   N-3 생략(작품 원장 없음 — cross-chapter 판별 불가·결측 정직), N-4/N-5/N-6 단일 회차 축은 계속 산출.
        #   호출부(서비스 계층)가 state 에서 선행 FINALIZED 본문을 넘길 수 있고, 러너·테스트는 미제공(하위호환).
        directives = directives or []
        self._cur_skill_inject = skills_inject or ""   # 켜진 회차 스킬(opt-in) → draft/continue 프롬프트 말미 주입
        # AG-1 T3 가드: 비트 캐스트가 없을 때의 전 개체 폴백에서 lookup 전용 etype(상수·물건·건)은 제외 —
        #   이들은 pull(조회) 대상이지 상시 push 대상이 아니다(폴백 canon_facts 비대 방지). 비트가 명시하면 포함.
        _PULL_ONLY = ("item", "worldrule", "case")
        involved = beat.get("entities") or [eid for eid, _e in ontology.entities.items()
                                            if getattr(_e, "etype", "") not in _PULL_ONLY]
        pov_id = self._pov_entity_id(ontology)   # DP-8: 1인칭이면 서술자 '나' 결속 대상 주인공 id(3인칭이면 "")
        self.bus.emit("plan_chapter", "start", chapter=ch_no, title=beat.get("title", ""))
        # 단계별 토큰 계측(단위경제 — 일관성 오버헤드율 재료)
        stage_usage: dict = {}
        # TM-1: 단계별 소요 시간 계측(usage 의 시간판 대칭 미러) — time.monotonic 기반(벽시계 아님).
        #   _mark() 가 stage 시작 시각을, _track() 이 종료 시각과의 델타(초·소수 1자리)를 같은 stage 키로 쌓는다.
        #   콜 없는 단계는 _track 이 호출되지 않으므로 애초에 안 들어간다(0초 노이즈 금지 계약).
        stage_time: dict = {}

        # CE-1 ⓑ: 스테이지 토큰 계측을 aux provider 까지 합산(델타 방식·계측 연속성). aux_model 이 설정되면 재요약·wiki
        #   콜은 self.aux_provider 로 도는데, 기존 _track 은 self.provider 델타만 봐서 그 스테이지 비용을 언더카운트한다.
        #   → baseline 을 (gen, aux) 쌍으로 스냅하고 두 델타를 같은 stage 키로 합산(aux==provider 면 동일 객체 델타를
        #   두 번 세지 않게 aux 델타를 0 으로). aux 미설정(폴백)이면 aux is provider → 기존 계측치와 바이트 동일.
        _aux_is_gen = (self.aux_provider is self.provider)
        # 캐논 추출 라우팅: extract_model 설정 시 checker.extractor 가 별도 provider 로 돈다 — aux 와 동일하게
        #   델타를 해당 stage(check/humanize 등)에 합산해 계측 연속성을 유지한다(별도 객체일 때만 — 이중계상 0).
        _ex_provider = getattr(getattr(self.checker, "extractor", None), "provider", None) or self.provider
        _ex_is_counted = (_ex_provider is self.provider) or (_ex_provider is self.aux_provider)

        def _mark():
            """stage 시작점 — (gen, aux, extract) 토큰 baseline 과 monotonic 시각을 함께 스냅(호출부 1줄 유지)."""
            return (self.provider.usage.chat_tokens, self.aux_provider.usage.chat_tokens,
                    _ex_provider.usage.chat_tokens), time.monotonic()

        def _track(stage, before_tokens, before_ts=None):
            gen_before, aux_before, ex_before = before_tokens
            delta = (self.provider.usage.chat_tokens - gen_before)
            if not _aux_is_gen:   # aux 가 별도 객체일 때만 aux 델타를 더한다(같은 객체면 gen 델타에 이미 포함 — 이중계상 방지)
                delta += (self.aux_provider.usage.chat_tokens - aux_before)
            if not _ex_is_counted:   # 추출 provider 도 동일 규율(별도 객체일 때만 합산)
                delta += (_ex_provider.usage.chat_tokens - ex_before)
            stage_usage[stage] = stage_usage.get(stage, 0) + delta
            if before_ts is not None:   # 시간 baseline 이 있으면 경과(초·소수1)도 같은 키로 누적(누적 후 1자리 반올림)
                stage_time[stage] = round(stage_time.get(stage, 0.0) + (time.monotonic() - before_ts), 1)
        # 보이스 카드: 등장 인물 말투의 '결'(스타일 지침 — 캐논 아님). 시그니처 문구의 기계 반복은 틱 생산기(소스 차단)
        # ST-12a: 1인칭(pov_id 있음 = style.pov=='first')에서 주인공 entity.voice 가 있으면 그 카드는 인물 '말투' 목록에서
        #   분리해 **서술자 프레임**(지문 서술 지시)으로 앞세운다 — 나머지 인물 voice·C-4 quota 는 기존과 동일. config
        #   narrator_voice=False / 비-first(pov_id 부재) / 주인공 voice 빈 값이면 _narrator_frame=""·제외 대상 없음 →
        #   프롬프트 바이트 완전 동일(구작·기존 테스트 무회귀). 긍정형·간결(금지문 0 — pink-elephant).
        _narrator_id = pov_id if getattr(self.settings, "narrator_voice", True) else ""
        _pov_ent = ontology.entities.get(_narrator_id) if _narrator_id else None
        _narrator_frame = ""
        # VB-1: 상태 연동 보이스 — 주인공의 서사 상태(예 정체자각 외면→흠칫→…)를 회차 시점으로 조회해
        #   그 단계 카드를 쓴다. stage_attr 미설정/단계 카드 없음이면 기존 voice 그대로 = 프롬프트 바이트 동일.
        #   state_as_of(모든 tier)를 쓴다 — 목소리는 게이트 캐논이 아니라 서사 인지 상태를 따라가는 게 맞다.
        _voice = getattr(_pov_ent, "voice", "") if _pov_ent is not None else ""
        _stage_attr = (getattr(self.style, "narrator_voice_stage_attr", "") or "").strip()
        _lin_narrator = {"eid": "", "stage_attr": _stage_attr, "stage": None}   # XR-3 계보(기록 전용)
        if _pov_ent is not None and _stage_attr:
            _stage = ontology.state_as_of(_pov_ent.id, _stage_attr, ch_no)
            _staged = (getattr(_pov_ent, "voice_stages", None) or {}).get(str(_stage), "")
            _lin_narrator["stage"] = str(_stage) if _stage is not None else None
            if _staged:
                # 8화 실측: 카드 문장이 그대로 지문에 실려 나왔다("안 맞은 자리는 그냥 지나쳤다"). 카드도 앵커다.
                #   → 카드를 '옮겨 적을 문장'이 아니라 '실현할 이해관계'로 자리매김(긍정형 — 금지문 0).
                _voice = _staged + "\n이것은 이 단계의 이해관계다. 장면의 행동·선택으로 실현하라."   # CX-6ⓓ: '거래' 상인 프레임 어휘 제거
                self.bus.emit("assemble_memory", "voice_stage", chapter=ch_no, attr=_stage_attr, stage=str(_stage))
        if _pov_ent is not None and _voice:
            _narrator_frame = ("[서술자 음성(참조 전용): 이 태도로 이번 회차의 지문을 새 문장으로 서술하라]\n" + _voice + "\n\n")   # VL-1
        else:
            _narrator_id = ""   # 서술자 프레임 미발화 → 아래 인물 목록에서 아무도 제외하지 않음(바이트 동일)
        _lin_narrator["eid"] = _narrator_id   # XR-3: 실제로 서술자 프레임을 발화한 개체(미발화면 "")
        # 인물 말투 목록(서술자로 뽑힌 주인공은 제외 — _narrator_id 가 "" 면 제외 대상 없음 = 기존과 동일)
        voice_cards = "\n".join(f"- {e.name}: {e.voice}"
                                for e in (ontology.entities.get(i) for i in involved)
                                if e is not None and getattr(e, "voice", "") and e.id != _narrator_id)
        if voice_cards:
            # CX-6ⓒ: 시그니처 어미·문구 quota 문장 삭제(VC-1 이 카드 스키마에서 지운 것의 렌더 잔재) — 카드는 결(어체)만.
            voice_cards += "\n(보이스는 태도·어휘의 '결'로 스며들게 하라.)"
        voice_cards = _narrator_frame + voice_cards   # 서술자 프레임 앞세움(_narrator_frame="" 이면 바이트 동일)
        # CX-6ⓐ: [표현 절제] 과용 표현 목록의 draft 주입 철거 — 피할 표현을 컨텍스트에 노출하는 설계는 헌법
        #   3형제(pink-elephant·T2·자기 이력 앵커) 위반(B-32e 폐기 전례)이고 호칭·직함까지 억제했다.
        #   목록은 관측 원장으로만(이벤트 가시화 — 측정→가시화→작가). 차단은 여기서 하지 않는다.
        if restraint:
            self.bus.emit("assemble_memory", "restraint_observed", chapter=ch_no, terms=list(restraint[:8]))
        # CX-6ⓑ: 고유명·미확정 명부는 보이스(어체) 블록과 무관한 표기 정합 재료 — 별도 헤더로 분리해
        #   보이스 헤더의 지시가 명부에 오적용되지 않게 한다(렌더는 같은 슬롯, 블록 경계만 명시).
        _names_block = self._name_anchor_cards(ontology.entities.values(), involved=involved)   # T5-R1 + RR-1
        if _names_block:
            voice_cards += "\n\n[아래는 보이스와 무관한 표기 명부다. 같은 대상을 다시 가리킬 때 표기만 맞춰라]" + _names_block

        narrative = list(anchors or [])   # 엔딩/아크 앵커(narrative, ground_truth 아님) 상단
        # M-1: 세계 규칙 텍스트 주입(사장돼 있던 ontology.rules). 헤더가 '확정 설정'에 세계규칙을 약속하므로
        #      낮은 신뢰 narrative 가 아니라 고신뢰 world_rules 슬롯으로(확정 설정 블록에 직렬화 — 권위 일치).
        world_rules = list(getattr(ontology, "rules", None) or [])
        if ch_no > 1:
            # N-1: RAG 후보에서 '직전 회차' 제외(as_of=ch_no-2) — prev_chapter 전문이 직전을 전담하므로 검색 슬롯은 먼 회차 복선 회수에 쓴다.
            narrative += rag.search(beat.get("summary", ""), max(0, ch_no - 2), k=self.settings.rag_k)
            narrative += wiki.retrieve(beat.get("summary", ""), ch_no - 1, k=self.settings.wiki_k)
            # CN-6: 미해소 과거참조(dangling) 결정론 해소 — 비트가 지목한 인물명이 현 집필맥락(직전 회차·누적줄거리·검색결과)
            #        어디에도 없으면(컨텍스트 기아) 그 이름으로 집필 전 타깃 검색 보강. 트리거=구조(이름 부재)이지 사전/어휘 아님. 기본 off.
            if getattr(self.settings, "bounded_dangling", False):
                _ctx = (prev_chapter_text or "") + (story_so_far or "") + " ".join(r.text for r in narrative)
                _hits, _resolved = self._resolve_dangling(involved, ontology, _ctx, rag, ch_no)
                narrative += _hits
                for _nm in _resolved:
                    self.bus.emit("assemble_memory", "dangling_resolved", chapter=ch_no, entity=_nm)
        # B-33: 시계 파생 산술(계약 만기 잔여·현재 나이)을 최상단 고신뢰 팩트로 '박기' — 결정론 값(copilot 이 story_clock 로 계산, LLM 산수 0).
        #        캐논 위에 배치해 "삼 년 만기가 도래"(day 6) 류 파생 산술 발명을 [확정 설정]이 직접 반증.
        # CX-3: gen_tools ON 이면 인물 속성 push 를 조회 단일 경로로 이관(canon_facts 는 생사 중대 상태·세계 상수만).
        #   OFF 면 기존 push 그대로(하위호환 — 조회가 없으니 인물 사실 0 방지). 관계 push 는 유지(스포 축 아님·최소 개입).
        _pull_actors = bool(getattr(self.settings, "gen_tools", False))
        # OFF 경로는 구 시그니처 그대로 호출(하위호환 — 테스트 페이크·구 구현체 무변경), ON 일 때만 kwarg.
        _cf = (ontology.canon_facts(involved, ch_no, actors_status_only=True) if _pull_actors
               else ontology.canon_facts(involved, ch_no))
        ground_truth = (list(extra_facts or [])
                        + _cf
                        + ontology.canon_relations(involved, ch_no))   # 확정 관계도 '박기'(ground_truth)
        # AG-1 T4(M1): 선조회 패스 — 집필 직전, 모델이 이 회차에 필요한 사실을 lookup_canon 으로 스스로 조회
        #   (tools 전용 짧은 턴·프로즈 금지). 결과는 ground_truth 에 얹혀 기존 단일 패스 집필은 무변경.
        #   gen_tools 기본 OFF — 미설정 시 이 블록 전체 무동작(프롬프트 바이트 동일). 실패는 집필을 막지 않는다.
        lookups_log: list[dict] = []
        _lin_lookup_facts: list = []   # XR-3 계보용 (query, value) 목록 — gen_tools OFF 면 빈 목록
        if getattr(self.settings, "gen_tools", False):
            from ..domain.types import OntologyFact
            from .lookup import CanonLookup, TOOL_SCHEMA
            # DG-6: 선행 확정 원장을 조회 실행기에 물린다 — 인물 조회 응답에 관계 어체 실물 앵커 동봉.
            #   prev_ledgers 미제공(러너·테스트·구 호출부)이면 기존 응답 바이트 동일(하위호환).
            _lk = CanonLookup(ontology, bible_entries, ch_no, ledgers=prev_ledgers)
            _names = [getattr(ontology.entities.get(e), "name", e) for e in involved[:8]]
            try:
                with promptlog.consumer("lookup_prepass"):   # XR-3: 스테이지 태그(메시지 인자 무변경)
                    self.provider.chat_tools(
                        [{"role": "system", "content":
                          "너는 집필 준비 사서다. 회차 계획을 읽고, 집필에 필요한 사실(돈·재고·시세·경력·규칙·"
                          "건 상태·과거 상태)을 lookup_canon 으로 조회하라. 산문을 쓰지 말고, 조회를 마치면 "
                          "'완료'라고만 답하라."},
                         {"role": "user", "content":
                          f"[회차 계획] {beat.get('title', '')} · {beat.get('goal', '')}\n"
                          f"[계획 사건] {' / '.join(beat.get('key_events') or [])}\n"
                          f"[등장] {', '.join(_names)}"}],
                        tools=TOOL_SCHEMA, tool_handler=_lk.handle, max_rounds=5)
            except Exception:
                pass
            lookups_log = _lk.log
            # PL-2(2026-08-17 감사 F1~F4): [확정 설정] 직렬화는 캐논 줄만 — 프로토콜 줄(미등재·재조회 안내)은
            #   사서 대화 안에서만 소비(지시 충돌·부정 선언 노출 차단), 같은 문서 줄은 1회만(중복 앵커 차단).
            #   줄 단위 판정 — 결과 단위 제외는 실패 응답에 동반된 캐논 문서 3종을 함께 버렸다(라이브 9화 재현).
            #   lookups_log 원본은 아래 draft_ctx["lookups"] 에 무필터 보존(감사 채널 — 미등재는 '캐논에 없는
            #   소재로 스토리가 사건 축을 세웠다'는 신호라 삼키지 않고 emit 으로 가시화한다).
            _facts, _misses, _deduped = CanonLookup.canon_facts_from_log(lookups_log)
            _lin_lookup_facts = list(_facts)   # XR-3: 계보 기록용 참조(주입 루프는 아래 그대로)
            for _q, _v in _facts:
                # CX-11: 구 220자 캡이 조회 응답을 이중 절단해 보이스·발췌가 컨텍스트에 못 오르던 소스 —
                #   전문 보이스(≤600)+발췌가 살아남게 완화. 짧은 사실 조회는 종전과 바이트 동일.
                ground_truth.append(OntologyFact(entity="[조회]", attr_label=_q, value=_v))   # 절단 전면 제거(2026-08-21): 조회 라벨 전문
            self.bus.emit("assemble_memory", "lookups", chapter=ch_no, count=len(lookups_log),
                          misses=_misses, deduped=_deduped)
        board = ContextBoard(chapter=ch_no, ground_truth=ground_truth, world_rules=world_rules[:12],
                             story_time=story_time, narrative=narrative, authority=directives,
                             prev_chapter=prev_chapter_text, story_so_far=story_so_far,
                             voice_cards=voice_cards,
                             confirmed_story=confirmed_story)   # SY-1: ""(기본)이면 assemble 바이트 동일
        self.bus.emit("assemble_memory", "done", chapter=ch_no,
                      ground_truth=len(board.ground_truth), retrieved=len(narrative))
        # 디버그(D-1~9): 집필에 실제로 들어간 입력 슬롯을 폭넓게 캡처 — system 헤더(문체/세계규칙)·끝맺음 정책·이어쓰기·교정까지.
        pv = prev_chapter_text or ""
        draft_ctx = {
            "persona": self.style.system_persona or "",   # 절단 전면 제거(2026-08-21): 디버그 캡처=실주입분 전문(뷰 절단이 '프롬프트가 잘렸다' 오독 소스였음)
            "style_rules": list(self.style.rules) or ["(코드 기본 규칙 — CX-7 SSOT 폴백)"],   # D-1: 실효 규칙 관측(빈 값=코드 상수 사용을 정직 표기)
            "author_style": self.style.author_style or "",       # Layer 2 작가 문체 오버레이(설정 시만) — 전문 캡처
            "world_rules": world_rules,                                  # D-6/M-1: 실제 주입되는 세계 규칙
            "story_time": story_time,                                    # CN-1: 결정론 누적 절대시점(고신뢰 주입)
            "time_anchors": [f"{f.entity}: {f.value}" for f in (extra_facts or [])],  # B-33: 시계 파생 산술(잔여·나이) 주입값 가시화
            "ending_hook_mode": self.style.ending_hook,                  # D-3: 끝맺음 정책
            "recent_tails": [(t or "")[-80:] for t in (recent_tails or [])],
            "ground_truth": [f"{f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth],
            "lookups": lookups_log,                                      # AG-1 T5: 선조회 기록(감사·재현·검증 대조 입력)
            "anchors": [{"source": a.source, "ref": a.ref, "text": a.text or ""} for a in narrative],   # 전문 캡처
            "story_so_far": story_so_far or "",
            "story_so_far_chars": len(story_so_far or ""),               # D-7: 실제 길이(트림 전)
            "directives": [d.text for d in directives],
            "voice_roster": voice_cards or "",   # 전문 캡처
            "prev_chapter_chars": len(pv),
            "prev_chapter_excerpt": (pv[:200] + (" …(중략)… " + pv[-200:] if len(pv) > 420 else "")),  # D-7: 머리/꼬리 발췌
            "continuations": 0,                                          # D-4: 이어쓰기 콜 수(아래서 갱신)
            "corrections": [],                                           # D-8: 교정 단계 발화 목록(아래서 갱신)
            "beat": {k: beat.get(k) for k in ("title", "summary", "key_events", "entities",
                                              "chapter_function", "hook_type", "closing_device",
                                              "scene_form", "world_reveal", "time_advance", "place")},
        }
        # XR-3: 블록 계보(쓰기 전용 관측 — ID·출처·사유만. 전문은 위 슬롯이 이미 보유). 실패해도 생성을 막지 않는다.
        try:
            draft_ctx["lineage"] = self._draft_lineage(
                board, beat, ontology, narrative, involved, ch_no, _pull_actors,
                extra_facts, _lin_lookup_facts, lookups_log, world_rules, _lin_narrator,
                prev_chapter_text, confirmed_story)
        except Exception as _e:
            draft_ctx["lineage"] = {"blocks": [], "gap": f"계보 기록 실패(관측 전용·생성 무영향): {str(_e)[:120]}"}

        # ---- 비트 단위 생성 · 코드 조립(재설계: 장면 개념·분량 지시 폐기) ----
        # 장면 분해(scenes_per_chapter)는 구 토큰 한계 시절 3콜 분할의 유물 — 장면 수는 설계 입력이 아니라 결과다.
        # 분량도 모델 입력에서 제거: 지시로 누르면 절단점이 글자 수에 종속되고(급결말), 하한을 지시하면 물 타기가 된다.
        # 모델은 비트 하나를 '자연스러운 절단점'까지 쓰고, 출고 규범(분량)은 코드가 '진행 이어쓰기'로 채운다
        # — 생성 단위(비트=극적 곡선)와 출고 단위(회차=플랫폼 규범)의 분리. 이어쓰기는 전문을 보고 절단점에서
        # 잇는 순차 연속(검증된 화 경계 인계와 동일 메커니즘)이지, 실패했던 병렬 블록 접합이 아니다.
        # SX-3: 세계 노출 슬롯(world_reveal)을 별도 강제 지시가 아니라 '비트 재료의 일부'로 key_events 채널에 자연 포함한다
        #   (T2 회피 — 계획 재료로만 흐르고 인포덤프 지시를 프롬프트에 넣지 않는다). 결측/빈 배열이면 아무것도 안 붙어 바이트 동일(하위호환).
        # SY-1 story 모드(설계 §1-D·F-A): 사건 표면 봉쇄 — spec.key_events=[] 로 브리프('핵심 사건')와
        #   장면 목표의 사건 채널을 닫고(사건 노출은 확정 스토리 블록 하나), world_reveal 승격도 함께 봉쇄.
        #   스토리 줄은 프롬프트가 아니라 이어쓰기 트리거·커버리지 계측 소스(_story_bodies)로만 쓴다.
        if confirmed_story:
            from . import story_pass_prompts as _spp
            _story_bodies = [l[2:] if l.startswith("- ") else l for l in _spp.story_lines(confirmed_story)]
            _draft_events = []
        else:
            _spp = None
            _story_bodies = []
            _draft_events = list(beat.get("key_events") or [])
            for _wr in (beat.get("world_reveal") or []):
                if isinstance(_wr, str) and _wr.strip() and _wr not in _draft_events:
                    _draft_events.append(_wr.strip())
        spec = SceneSpec(index=0, goal=beat.get("summary", ""), key_events=_draft_events)
        self._cur_scene_inject = self._style_inject(beat)   # 장면형 앵커 실험(비트 기능 라우팅)
        rounds: list[RoundTrace] = []
        initial_caught = None
        self.bus.emit("draft_chapter", "start", chapter=ch_no)
        _t, _ts = _mark()   # TM-1: 토큰+시각 baseline(draft 단계 시작)
        norm = int(self.style.target_chars_per_chapter * 0.85)   # 출고 규범(코드 판정 — 모델은 분량을 모른다)
        # VX-1: 분량 규범 판정은 '태그 제거 후 보이는 길이'로 한다. structured_prompt 작품은 대사가 <대사 화자>
        #   태그로 감싸여 생성 중 텍스트 길이가 태그 문자만큼 과대계상되는데, 그 팽창분으로 이어쓰기가 조기 종료되면
        #   발행(디태거 통과) 본문이 규범 미달로 떨어진다(태그≈19자/대사 × 수십 줄). OFF 면 len 그대로(무비용).
        _sp_tags = bool(getattr(self.style, "structured_prompt", False))
        _vlen = (lambda s: len(lower_dialogue_tags(s)[0])) if _sp_tags else len
        draft_ctx["length_norm"] = norm                          # D-2: 출고 규범(문체규칙의 '5천자' 지시와 대조 가능)
        # ST-12b: draft 러닝 카운터 폐루프(토글 종속). ON 이면 이어쓰기 청크 직전에 누적 종결 분포를 계측해
        #   대역 밖이면 수치 상태블록을 _continue 프롬프트에 주입한다. OFF(기본)면 계측·주입 0 — draft_ctx 에
        #   style_counter 키 자체를 넣지 않아 구 레코드 바이트가 동일하고, _continue 는 style_state="" 로 호출돼
        #   프롬프트 바이트가 완전 동일하다(무회귀 계약). 디버그 레코드는 ON 일 때만 A/B 포렌식용으로 append.
        _run_counter = bool(getattr(self.settings, "style_running_counter", False))
        _style_state_draft = ""
        if _run_counter:
            draft_ctx["style_counter"] = []
            # ST-12b-2(회차 간 폐루프): 이어쓰기 주입은 개입 표면이 없다(12b 쌍 A/B 실측 — cont 0~1회·잔여
            #   클램프로 무효량). 직전 회차 계측이 대역 밖이면 '초안' 프롬프트에 상태블록을 주입한다(표면 100%·
            #   상태 적응형 — ch1/in-band/결측이면 "" = 바이트 동일). 자기 문체 앵커(prev tail)와 같은 지점,
            #   반대 방향의 폐루프.
            _scp = style_counter.compute_prev(prev_chapter_text or "")
            _style_state_draft = _scp["block"]
            if _scp.get("debug") is not None:
                draft_ctx["style_counter"].append({"ext": 0, **_scp["debug"]})
        ext_total = 0
        # 생성→검증→실패분류→라우팅(적대검증 반영):
        #  · 절단(max_tokens): 좋은 본문 + 잘린 꼬리 → 세그먼트별로 호출 직후 last_truncated 를 즉시 읽어 trim_dangling
        #    으로 '잘린 꼬리만' 제거(전량 폐기=본문파괴 R2 위반, 빈/짧은 재추첨 위험 → 안 함). 깨끗한 종결에서 이어쓰기.
        #  · 빈 응답: 보존할 게 없는 유일한 전역 파손 → 재생성(bounded). 절단은 여기로 안 옴(위에서 trim 처리됨).
        _regen_cap = max(0, getattr(self.settings, "max_regen_attempts", 1))
        for _regen in range(_regen_cap + 1):
            self.provider.last_truncated = False
            text = sanitize_meta(self._draft(board, spec, "", last=True, closing=closing,
                                             recent_tails=recent_tails, chapter_mode=True,
                                             style_state=_style_state_draft))   # ST-12b-2: ""면 바이트 동일
            if self.provider.last_truncated:        # 절단 → 잘린 꼬리만 제거(본문 보존), 깨끗한 종결에서 이어쓰기
                text = trim_dangling(text)
            ext = 0
            # 보강 상한 2→4: 건조한 author_style(또는 짧은 비트)은 세그먼트가 짧아 2회로 norm(5천자) 미달(페르소나 실측 3,618자).
            # '이야기 전진'으로만 채우는 원칙은 유지 — 아래 진행 가드(<200자 중단)가 '진짜 더 쓸 게 없으면' 알아서 멈춰 물타기·낭비를 막는다.
            # 정상 문체는 0~2회로 norm 도달해 루프를 빠져나가므로 추가 비용 없음(짧게 나오는 회차만 3~4회 사용).
            # DP-12: 이어쓰기 트리거를 'norm 하한 강제'에서 '비트 미소진'으로 — 전달된 key_events 대비
            #        아직 지면에 실현되지 않은 재료가 남았을 때만 이어쓴다. norm 미달 시 무조건 _continue 하던
            #        기존 조건은 사실상 분량 하한 강제(B-02 '분량≠밀도' 상충)라, 재료가 소진되면 재설명·물타기로
            #        채우던 소스를 차단한다. 판정은 기존 커버리지 자산(drift.uncovered — 어간 과반·보수적)을 재사용:
            #        확실히 실현된 것만 소진으로 보고, 미실현이 남으면 이어쓴다. 재료 소진 시엔 짧은 회차를 그대로
            #        수용하고 아래 under_norm 이벤트로만 가시화한다(차단 아님). key_events 부재 시엔 판정 근거가
            #        없어 보수적으로 기존 norm 기준을 유지(하위호환). DP-11 이 required_events/메뉴로 재료를 늘리면
            #        이 하류 트리거가 자연히 더 오래 돌아 자연 해소된다.
            _beat_events = [e for e in (beat.get("key_events") or []) if (e or "").strip()]
            if confirmed_story:
                # SY-1 §4(M-E 확정): DP-12 트리거 소스를 확정 스토리 줄로 교체 — len<norm 연접 유지,
                #   잔여(미실현 의심) 0이면 norm 미달이어도 정지(재료 소진 시 짧은 회차 수용 보존·B-02 준수).
                _beat_events = _story_bodies
            while (text.strip() and _vlen(text) < norm and ext < 4      # 빈 초안은 이어쓰기 말고 재생성으로(아래)·분량은 태그 제거 후 길이(VX-1)
                   and (not _beat_events or _uncovered(_beat_events, text))):   # 비트 미소진일 때만 전진(소진 시 짧은 회차 수용)
                ext += 1
                self.bus.emit("draft_chapter", "extend", chapter=ch_no, chars=len(text), round=ext)
                self.provider.last_truncated = False
                # ST-12b: 이어쓰기 직전 누적 본문 계측 → 대역 밖이면 상태블록(수치+긍정 메뉴). 토글 OFF면 style_state=""
                #   (프롬프트 바이트 동일). 계측은 lazy import·try/except 로 격리(부품 부재 시 조용히 무주입·생성 계속).
                _style_state = ""
                if _run_counter:
                    _sc = style_counter.compute(text, norm)
                    _style_state = _sc["block"]
                    if _sc.get("debug") is not None:
                        draft_ctx["style_counter"].append({"ext": ext, **_sc["debug"]})
                # G9: 이어쓰기에 '이 회차의 계획 사건'을 컨텍스트로(아직 못 다룬 쪽으로 전진하게 — 정보 제공, 분량 지시 아님)
                _story_rem = ""
                if confirmed_story:
                    # SY-1 §5: 잔여 계산은 generate() 가 수행(계산 지점 단일화). 목록 지위는 '미실현 의심'
                    #   후보 풀(어간 판정은 시제 층위를 못 가로질러 과소 소거가 상례 — 차례 판단은 모델).
                    _rem_lines = _spp.remaining_story_lines(confirmed_story, text, _uncovered)
                    _story_rem = _spp.build_story_block_continue(
                        _rem_lines, xml=getattr(self.style, "structured_prompt", False))
                    self.bus.emit("draft_chapter", "story_remaining", chapter=ch_no, ext=ext,
                                  suspect=len(_rem_lines), total=len(_story_bodies))
                more = sanitize_meta(self._continue(board, text, closing=closing, recent_tails=recent_tails,
                                                    key_events=([] if confirmed_story else (beat.get("key_events") or [])),
                                                    style_state=_style_state, story_remaining=_story_rem))
                if self.provider.last_truncated:    # 이어쓰기도 절단되면 꼬리만 제거(seam 방지)
                    more = trim_dangling(more)
                if len(more.strip()) < 200:      # 무의미 연장 → 중단(짧은 회차로 출고가 낫다)
                    break
                text = text.rstrip() + "\n\n" + more.strip()
            ext_total += ext
            if text.strip() or _regen == _regen_cap:   # 본문 있으면 채택(절단은 trim 으로 처리됨). 빈 응답만 재생성
                break
            self.bus.emit("draft_chapter", "regenerated", chapter=ch_no, attempt=_regen + 1, reason="empty")
        draft_ctx["continuations"] = ext_total   # D-4: 이어쓰기 콜 수(재생성 시 누적) — 후반부가 '다른 컨텍스트'로 쓰였는지 가시화
        # VX-1: 대사 태그 디태거(생성 완료 직후·모든 수술 앞) — <대사 화자="…"> 를 정규 대사로 낮추고 (화자,대사)
        #   쌍을 뽑는다. reformat/check/rewrite/finalize·재주입·리더는 전부 평문 대사를 가정하므로 반드시 여기서
        #   낮춘다(태그 리터럴 노출·재주입 앵커 오염 차단). OFF 면 태그가 없어 no-op(text 불변). 미준수(태그 0)면
        #   쌍 목록이 비어 원장이 종전 LLM 귀속 경로로 폴백한다(무강제 — 폴백 실재).
        _tagged_pairs = None
        if _sp_tags:
            text, _pairs = lower_dialogue_tags(text)
            _tagged_pairs = _pairs if _pairs else None
            draft_ctx["dialogue_tags"] = {"count": len(_pairs)}
            self.bus.emit("draft_chapter", "dialogue_tags", chapter=ch_no, count=len(_pairs))
        if confirmed_story:
            # SY-1 §5-bis ⓑ: 초안 단계 커버리지 가시화(라벨='미실현 의심' — 사실 주장 아님, 차단 0).
            #   최종본 축은 서비스의 verification["story_pass"] 가 다시 계산한다(수술 패스 이후 기준).
            _rem_draft = _spp.remaining_story_lines(confirmed_story, text, _uncovered)
            draft_ctx["story_uncovered"] = {"suspect": len(_rem_draft), "total": len(_story_bodies),
                                            "lines": _rem_draft[:20]}
            self.bus.emit("draft_chapter", "story_uncovered", chapter=ch_no,
                          suspect=len(_rem_draft), total=len(_story_bodies))
        # GA-1: '첫 초안 원문' 스냅샷 — 이어쓰기 완료 후·수술 전(reformat/rewrite/fix_tics/humanize 등 어떤 변형보다 앞)의
        #   본문 = 모델이 실제로 생성한 원본 회차. 이 시점 이후 어떤 패스도 제자리 변형하므로 여기서만 잡을 수 있다.
        _trace_first_draft = text
        if len(text) < norm:   # G9: 규범 미달 출고 가시화(silent 금지) — 작가·G3 회고 입력(차단 아님)
            self.bus.emit("draft_chapter", "under_norm", chapter=ch_no, chars=len(text), norm=norm)
        tail_seg = "\n".join(text.splitlines()[-15:])
        if (fragmentation_score(text) < 22 or fragmentation_score(tail_seg) < 14
                or short_line_ratio(text) > 0.15):
            # 토막 행갈이 붕괴(전체 또는 말미 국소) → 조판 복구
            self.bus.emit("draft_chapter", "reformat", chapter=ch_no)
            draft_ctx["corrections"].append("reformat")   # D-8
            text = sanitize_meta(self._reformat(text))
        _track("draft", _t, _ts)
        best_text, best_hard = text, None     # M5: hard 위반 최소 텍스트 보존
        _trace_rewrite_rounds: list[dict] = []   # GA-1: 각 재작성 라운드 산출 전문 보존(RoundTrace 는 카운트만 — 전문 소실)
        for r in range(self.settings.max_rewrite_rounds + 1):
            _t, _ts = _mark()
            res = self.checker.check_text(text, ontology, ch_no, involved, pov_id)
            _track("check", _t, _ts)
            hard = res.hard
            # 본문 재작성으로 고칠 수 있는 위반(QUASI)과 SSOT 자기모순(DETERMINISTIC) 분리 — M4.
            text_hard = [v for v in hard if v.grade == SignalGrade.QUASI]
            ssot_hard = [v for v in hard if v.grade == SignalGrade.DETERMINISTIC]
            rounds.append(RoundTrace(round=r, scene=0, n_violations=len(res.violations),
                                     n_hard=len(hard), kinds=[v.kind for v in res.violations]))
            self.bus.emit("consistency_check", "done", chapter=ch_no, scene=0, round=r,
                          violations=len(res.violations), hard=len(hard),
                          kinds=[v.kind for v in res.violations])
            if initial_caught is None:
                initial_caught = list(res.violations)
            if best_hard is None or len(hard) < best_hard:   # M5: 최선본 갱신
                best_text, best_hard = text, len(hard)
            if not text_hard or r == self.settings.max_rewrite_rounds:
                if ssot_hard:   # 본문으로 못 고치는 SSOT 자기모순 — 작가에게 가시화
                    self.bus.emit("scene_loop", "ssot_contradiction", chapter=ch_no, scene=0,
                                  kinds=[v.kind for v in ssot_hard])
                if text_hard and r == self.settings.max_rewrite_rounds:
                    self.bus.emit("scene_loop", "non_convergence", chapter=ch_no, scene=0,
                                  hard=[v.kind for v in text_hard])
                break
            self.bus.emit("partial_rewrite", "start", chapter=ch_no, scene=0, round=r,
                          fixing=[v.kind for v in text_hard])
            _t, _ts = _mark()
            text = sanitize_meta(self._rewrite(text, text_hard, board))
            draft_ctx["corrections"].append(f"rewrite#{r}({','.join(v.kind for v in text_hard[:3])})")   # D-8
            _trace_rewrite_rounds.append({"round": r, "fixing": [v.kind for v in text_hard],
                                          "text": text})   # GA-1: 재작성 라운드 전문(사유=fixing kinds)
            _track("rewrite", _t, _ts)

        chapter_text = best_text     # M5: 최선본 채택
        # ---- 품질 결정론 게이트(닫힌 루프): 검출=코드, 교정=국소 ----
        from .quality_gates import word_tics, hook_repeat_semantic, strip_directive_leak
        chapter_text = strip_directive_leak(chapter_text)              # 지시어 누출(R7 '절단.') 결정론 제거
        roster = {ontology.entities[i].name for i in involved if i in ontology.entities}
        offenders = [(p, n) for p, n in word_tics(chapter_text, roster, cap=4)][:5]
        if offenders:
            _t, _ts = _mark()
            chapter_text = self._fix_tics(chapter_text, offenders, ch=ch_no)   # GN-2: ch 관통(ch1 발단 결 보존)
            draft_ctx["corrections"].append(f"fix_tics({','.join(p for p,_ in offenders[:3])})")   # D-8
            _track("quality", _t, _ts)
            residual = word_tics(chapter_text, roster, cap=4)
            if residual:   # 교정 후 재검(닫힌 루프) — 잔존은 무음 통과 금지
                self.bus.emit("quality_gate", "tics_residual", chapter=ch_no, tics=residual[:3])
        if recent_tails and not closing:
            tail_now = " ".join([ln for ln in chapter_text.splitlines() if ln.strip()][-3:])
            if hook_repeat_semantic(self.provider, tail_now, recent_tails) > 0.82:   # 의미 유사(템플릿 재탕)
                _t, _ts = _mark()
                chapter_text = self._regen_tail(chapter_text, recent_tails, ch=ch_no)   # GN-2: ch 관통(ch1 발단 결 보존)
                draft_ctx["corrections"].append("regen_tail(훅 재탕)")   # D-8: 말미가 교체됐음(독자평가 통과해도 말미는 다른 컨텍스트)
                _track("quality", _t, _ts)
        if getattr(self.settings, "continuity_polish", True):   # 출고 검수(수치·인명·절단·메타·틱·시제) — 패치 후 게이트 재검
            _t, _ts = _mark()
            names = [ontology.entities[i].name for i in involved if i in ontology.entities]
            chapter_text = sanitize_meta(self._continuity_polish(chapter_text, names=names, ch=ch_no))   # GN-2: ch 관통(ch1 발단 결 보존)
            draft_ctx["corrections"].append("continuity_polish")   # D-8
            _track("polish", _t, _ts)
        from .quality_gates import tense_leak_ratio
        if tense_leak_ratio(chapter_text) > 0.05:   # 시제 누출(현재형 종결 혼입) — 폴리시 ⑥의 결정론 백스톱
            _t, _ts = _mark()
            chapter_text = self._fix_tense(chapter_text)
            draft_ctx["corrections"].append("fix_tense")   # D-8
            _track("quality", _t, _ts)
        chapter_text = sanitize_meta(chapter_text)   # 발행 경계 최종 보증: 어느 패스가 만든 메타든 published 본문엔 없게(구조 불변식)
        # B-35: 초반 회차가 '웹소설 포스트'로 포맷돼 line0 자작 표제(등록 제목과 불일치)·&nbsp; 스페이서를 얹는 소스 잔재를
        # 발행 경계에서 결정론 정규화(선행 표지 블록 제거·단독 &nbsp;→빈 줄). 라인 단위·위치 기반이라 diegetic 괄호(B-24)·표현 마크업 불변.
        chapter_text = strip_structural_markup(chapter_text)
        # ST-3: 발행 경계 문단 조판(B-35 곁·저장 직전). 실측 최상위 '가벼움' 레버 — 3문장 초과 지문 문단을 문장
        # 경계로 1~2문장씩 분할, 지문과 섞인 대사 라인을 단독 문단화. 내용 무손실(비공백 불변)·판정기 아님(조판=줄바꿈).
        # 토글 OFF 시 바이트 동일(호출 자체를 건너뜀). 기존 저장 작품은 무변경(이 경로는 신규 생성분만 통과).
        if getattr(self.settings, "paragraph_reflow", True):
            # VP-1: 지문 문단 최대 문장 수를 작품 데이터 노브로(기본 2=종전 바이트 동일). 강제를 줄이는 방향의
            #   완화 노브라 무강제 정합 — '2문장 문단 벽'의 결정론 소스 실측(적대 리뷰 2026-08-13) 대응.
            _pmax = int(getattr(self.style, "paragraph_max_sents", 2) or 2)
            chapter_text = reflow_paragraphs(chapter_text, narr_chunk=max(2, _pmax))
        # ---- HM-1 휴머나이즈 패스(finalize 직전·reflow 후·최종 체크 전) — SP-1 Stage B 흡수 ----
        # 설계 §2ⓓⓔ: 결정론 티 탐지(HM-1a: N-3 모티프·N-4 문말·N-5 층위·N-6 수식) → Claude 윤문(카테고리 처방·
        #   사실 불변 체시스·변경률 가드·폴백=원문·스팬별 격리). 이 경로가 Stage B(리듬 수리)를 흡수한다 — humanize
        #   ON 이면 Stage B 를 따로 태우지 않는다(이중 패스 금지·수리 역설 소스 통합). 윤문 본문이 아래 최종
        #   check_text/색인/요약의 입력이 되도록 여기서 태운다. 무강제: 실패/폴백은 원문 유지 + 내역 기록(침묵 금지).
        # 하위호환: humanize OFF(또는 속성 부재) 이면 기존 Stage B(style_repair)로 폴백. Stage B 마저 OFF·부품
        #   부재면 no-op(본문 바이트 불변) — "OFF 시 바이트 동일" 계약 정합.
        style_repairs: list[dict] = []
        humanize_entries: list[dict] = []
        _trace_style_judgment = None       # GA-1: style_judge 판정 전문(humanize ON·정상 경로에서만 채워짐)
        _trace_humanize_spans: list[dict] = []   # GA-1: humanize 스팬별 before/after
        _humanize_on = bool(getattr(self.settings, "humanize", False))   # ON 이면 Stage B 흡수(상한 0이라도 Stage B 로 폴백 안 함)
        if _humanize_on and getattr(self.settings, "humanize_max_spans", 6) > 0:
            try:
                from .humanize_detect import (build_motif_ledger, detect_chapter,
                                              apply_style_judgment, motif_candidates_from_findings)
                from .humanize_pass import humanize_spans, build_humanize_provider, bulk_transfer_pass
                # HM-6 하이브리드 1단(감사 통과 2026-08-20): rewrite ON 이면 통짜 이전-전용 변환 1콜을 블록 맨 앞에
                #   선행 — 이후 탐지·판정·스팬 수술이 전부 통짜 결과 위에서 돌아 좌표 스테일·이중 변환이 없다.
                #   실패/무변경/검사 걸림(대사 원형·축소율·G-B)은 원문 유지(무강제).
                if (getattr(self.settings, "humanize_llm_detect", False)
                        and getattr(self.settings, "humanize_llm_detect_rewrite", False)
                        and getattr(self.settings, "humanize_llm_detect_bulk", True)):
                    _t, _ts = _mark()
                    _hp0 = build_humanize_provider(self.settings)
                    chapter_text, _bulk_info = bulk_transfer_pass(
                        self, ontology, self.checker, ch_no, chapter_text,
                        service=self.service, humanize_provider=_hp0)
                    _track("humanize_bulk", _t, _ts)
                    self.bus.emit("humanize_bulk", "done", chapter=ch_no,
                                  bulk=_bulk_info.get("bulk"), delta=_bulk_info.get("delta"))
                else:
                    _bulk_info = None
                # N-3 모티프 원장(선택): 선행 회차 본문이 있으면 회차 간 반복 구절 원장을 만든다(없으면 N-3 생략).
                roster = {ontology.entities[i].name for i in involved if i in ontology.entities}
                ledger = None
                if prev_texts:
                    chs = [{"chapter": None, "text": t} for t in prev_texts if (t or "").strip()]
                    chs.append({"chapter": ch_no, "text": chapter_text})
                    ledger = build_motif_ledger(chs, roster=roster)
                findings = detect_chapter(chapter_text, ledger=ledger, roster=roster)
                # HZ-1 ①: 스타일 지각 판정(매화 무조건·+1콜) — 낭독 체감으로 N-4 문말 수리 필요 여부·인용을 낸다.
                #   결정론 임계 발동 폐기(사용자 "두더지잡기"). 판정 콜은 style_judge_model(교차 벤더 기본)로 라우팅.
                #   실패=None → apply_style_judgment 가 N-4 수리 스킵(보수·결측 정직). 판정 인용→위치 매칭→사실 밀집
                #   금기→최대 humanize_n4_max_spans. HZ-2: N-3 모티프 후보(detect_chapter N-3 findings)를 *같은 판정
                #   콜*에 동봉(추가 콜 0)해 모티프 반복도 낭독 체감 판정 → 확인된 것만 수술. N-5/N-6 은 불변(자체
                #   검출). 판정 usage/time 은 _run_style_judgment 가 'style_judge' 키로 *직접* 계상한다(별도 스테이지 —
                #   humanize baseline 은 판정 *뒤*에 찍혀 판정 provider=="" 재사용 경로에서도 이중 계상이 없다).
                _motif_cands = motif_candidates_from_findings(findings)   # HZ-2: N-3 findings 표층 → 판정 참고 자료
                _sj_judgment, _sj_skips = self._run_style_judgment(
                    chapter_text, ch_no, stage_usage, stage_time, motif_candidates=_motif_cands)
                _trace_style_judgment = _sj_judgment   # GA-1: 판정 전문(needs_repair·spans·motif_spans·reason·reference)
                # HM-3: N-1/N-2 LLM 탐지(opt-in) — catch-all=가시화 전수(advisory), precise=잔여 자동 수술 선별
                #   (HM-6 정밀 전환 2026-08-21: catch-all 은 내용 구간까지 물어 자동 수술 소비 시 헛수술 비용
                #   역류 실측 — 수술은 '옮겨도 잃는 게 없는 잉여만'). 둘 다 humanize baseline 앞(이중 계상 방지).
                _narr_detect_on = bool(getattr(self.settings, "humanize_llm_detect", False))
                _narr_rewrite = bool(getattr(self.settings, "humanize_llm_detect_rewrite", False))
                _narr_findings = (self._run_narration_detect(chapter_text, ch_no, stage_usage, stage_time)
                                  if _narr_detect_on else [])
                _narr_precise = (self._run_narration_detect(chapter_text, ch_no, stage_usage, stage_time,
                                                            mode="precise")
                                 if (_narr_detect_on and _narr_rewrite) else [])
                _t, _ts = _mark()   # humanize 스테이지 baseline — 판정 콜 *뒤*(이중 계상 방지)
                try:
                    findings, _n4_skips = apply_style_judgment(
                        chapter_text, findings, _sj_judgment, self.settings, roster=roster)
                    if _narr_precise and _narr_rewrite:
                        findings = list(findings) + _narr_precise   # HM-6: 정밀 선별분만 자동 수술(잉여 한정)
                    # 윤문가 라우팅(HZ-1③ 같은 펜: humanize_model 기본 "" → 스왑 없이 gen_model). 명시값이면 스왑.
                    hp = build_humanize_provider(self.settings)
                    _hz_before_full = chapter_text   # GA-1: '판정 시점 chapter_text'(수술 전) — 스팬 before 슬라이스 원본
                    chapter_text, humanize_entries = humanize_spans(
                        self, ontology, self.checker, ch_no, chapter_text, findings,
                        max_spans=(None if getattr(self.settings, "humanize_all_spans", False)
                                   else int(getattr(self.settings, "humanize_max_spans", 6))),
                        service=self.service, humanize_provider=hp)
                    # GA-1: 스팬별 before/after 텍스트 수집(사이드카 trace 용 — 본체 humanize 엔트리엔 좌표·변경률만 남아
                    #   원문/재작성문 소실). before=판정 시점 원문 창 슬라이스, after=수술 결과(변경) or 원문(폴백=원문).
                    _trace_humanize_spans = _collect_humanize_span_texts(
                        _hz_before_full, chapter_text, humanize_entries)
                    # HZ-1 은폐 금지: N-4 판정 스킵/금기(수리 안 함) 기록을 humanize 내역에 additive(투명성 영속).
                    humanize_entries = list(humanize_entries) + list(_n4_skips)
                    # HM-3 가시화: catch-all 전수 목록은 rewrite 여부와 무관하게 advisory 로 기록(사용자 결정
                    #   "보는 건 전수" — 기계로 못 빼는 비잉여 구간을 매화 정독으로 확인하는 원장 감시의 원자료).
                    if _narr_findings:
                        humanize_entries = humanize_entries + [_narr_advisory_entry(f) for f in _narr_findings]
                    # HM-6: 통짜 변환 결과를 meta 로 영속(채택/폴백 사유·delta — 은폐 금지).
                    if _bulk_info is not None:
                        humanize_entries = humanize_entries + [{
                            "category": "meta:bulk", "severity": None, "char_start": None, "char_end": None,
                            "before_len": None, "after_len": None,
                            "changed": (_bulk_info.get("bulk") == "changed"), "change_rate": None,
                            "rate_band": None, "fallback": (None if _bulk_info.get("bulk") == "changed"
                                                            else _bulk_info.get("bulk")),
                            "coverage_passed": None, "guardrail_ok": None, "author_review": False,
                            "note": "HM-6 통짜 이전-전용 변환", "bulk_info": _bulk_info,
                        }]
                finally:
                    _track("humanize", _t, _ts)   # 판정 뒤 baseline 기준 humanize 델타(예외 경로 포함·이중 계상 0)
            except Exception as e:
                # 브릿지/부품 예외는 무강제 — 원문 유지, 가시화(침묵 금지). 회차 확정을 막지 않는다.
                self.bus.emit("humanize", "failure", chapter=ch_no, error=str(e)[:200])
                humanize_entries = []
            if humanize_entries:
                _changed = sum(1 for r in humanize_entries if r.get("changed"))
                _review = sum(1 for r in humanize_entries if r.get("author_review"))
                self.bus.emit("humanize", "done", chapter=ch_no,
                              spans=len(humanize_entries), changed=_changed, author_review=_review)
                draft_ctx["corrections"].append(f"humanize({_changed}/{len(humanize_entries)})")   # D-8 가시화
        elif (not _humanize_on) and getattr(self.settings, "style_repair", False) \
                and getattr(self.settings, "style_repair_max_spans", 6) > 0:
            # 하위호환 폴백: humanize 자체가 OFF 일 때만 기존 SP-1 Stage B(리듬 수리)를 태운다(경로 보존·바이트 계약
            #   정합). humanize ON + 상한 0(탐지만) 상황에서 Stage B 로 새지 않게 _humanize_on 으로 명시 가드(흡수 불변).
            _t, _ts = _mark()
            try:
                from .style_pipeline import repair_spans
                # SP-1b ①(G3): service=self.service 를 관통 배선 — 스팬 수리 G-B 사실 표면 비교 실배선.
                chapter_text, style_repairs = repair_spans(
                    self, ontology, self.checker, ch_no, chapter_text,
                    max_spans=int(getattr(self.settings, "style_repair_max_spans", 6)),
                    service=self.service)
            except Exception as e:
                self.bus.emit("style_repair", "failure", chapter=ch_no, error=str(e)[:200])
                style_repairs = []
            if style_repairs:
                _changed = sum(1 for r in style_repairs if r.get("changed"))
                self.bus.emit("style_repair", "done", chapter=ch_no,
                              spans=len(style_repairs), changed=_changed)
                draft_ctx["corrections"].append(f"style_repair({_changed}/{len(style_repairs)})")   # D-8 가시화
            _track("style_repair", _t, _ts)
        # ---- RC-6 최종화 강제 교정(사용자 확정 '항상 결함' 2클래스만) — humanize/style_repair 뒤·최종 check 전 ----
        # 무강제 예외 근거: 헌법 1조 프로즈 준수 게이트 금지는 유지하되, 사용자가 '항상 결함'으로 *명시 확정*한
        #   ①스타카토/용언 없는 조각(2026-08-29 "예외 없이")·②폐기 조어·금지어(v4 정본·StyleSpec.deprecated_terms)만
        #   기계 수술 대상(정책 출처=시스템 아닌 사용자·2026-08-21 "삭제 말고 수정, 전량"). 그 외 미감·스타일 축은 계속
        #   advisory. humanize 플래그와 독립(강제) — humanize OFF 여도 이 두 클래스는 교정(폴백해 잔존한 조각까지 보장
        #   제거). 사실 가드 G-A/G-B 통과분만 채택(폴백=원문). 플래그 OFF(기본)=호출 생략 → 본문 바이트 동일(하위호환).
        if getattr(self.settings, "finale_force_fixes", False):
            _t, _ts = _mark()
            try:
                from .finale_force import apply_forced_finale_fixes
                _subs = dict(getattr(self.style, "deprecated_terms", None) or {})
                chapter_text, _force_entries = apply_forced_finale_fixes(
                    ontology, self.checker, ch_no, chapter_text,
                    subs_map=_subs, service=self.service)
                if _force_entries:
                    _fc = sum(1 for r in _force_entries if r.get("changed"))
                    self.bus.emit("finale_force", "done", chapter=ch_no,
                                  entries=len(_force_entries), changed=_fc)
                    humanize_entries = list(humanize_entries) + _force_entries   # 투명성 영속(rec.humanize)
                    if _fc:
                        draft_ctx["corrections"].append(f"finale_force({_fc})")   # D-8 가시화
            except Exception as e:
                self.bus.emit("finale_force", "failure", chapter=ch_no, error=str(e)[:200])
            _track("finale_force", _t, _ts)
        _t, _ts = _mark()
        final = self.checker.check_text(chapter_text, ontology, ch_no, involved, pov_id)
        _track("check", _t, _ts)
        if getattr(final, "missing_appears_as", None):   # CE-4 ⓐ: appears_as 누락 가시화(advisory·비차단·자동 채움 없음)
            self.bus.emit("consistency_check", "appears_as_missing", chapter=ch_no,
                          entities=list(final.missing_appears_as))
        hard_remaining = final.hard
        # 빈 본문(이중 빈응답·거부 등)을 FINALIZED 로 발행하면 rag 색인·요약이 story_so_far 를 영구 오염하고
        # current_chapter 가 전진해 발행본에 빈 구멍이 남는다(적대검증). '조용한 정지 불가' 불변식대로 ESCALATED 로.
        _empty = not (chapter_text or "").strip()
        status = ChapterStatus.FINALIZED if (not hard_remaining and not _empty) else ChapterStatus.ESCALATED

        indexed = pages = 0
        summary = detail_synopsis = ""
        summary_degraded = False
        claim_findings: list = []
        dialogue_ledger: list = []   # DG-1 기본(비확정·OFF·실패 = 빈 원장) — ESCALATED/flag-off 경로 안전 초기화
        if status == ChapterStatus.FINALIZED:
            indexed = rag.index_chapter(ch_no, chapter_text)
            _t, _ts = _mark()
            try:
                # CX-4: 1인칭이면 화자 id 를 넘겨 로스터 강제 포함(화자 서술이 곁 인물 카드로 새는 오염 루프 차단).
                pages = wiki.ingest_chapter(ch_no, chapter_text, ontology, reviewed=True,
                                            pov_entity_id=pov_id)
            except Exception:   # 위키는 narrative(비구속) — 실패가 회차 확정을 막으면 안 됨(가시화 후 계속)
                pages = 0
                self.bus.emit("finalize", "wiki_failure", chapter=ch_no)
            _track("wiki", _t, _ts)
            _t, _ts = _mark()
            # SY-1 M1(3차 감사 확정): story 모드 요약 폴백에는 summary 한 문장 단독 — key_events(라벨
            #   summary 1줄)를 넘기면 폴백이 같은 문장을 두 번+"주요 사건:" 라벨로 합성해 story_so_far 로
            #   영속된다(파편체 되먹임 실측 계보). 레거시 경로는 beat 그대로(바이트 동일).
            _sum_beat = {**beat, "key_events": []} if confirmed_story else beat
            summary, detail_synopsis, _degraded = self._summarize(chapter_text, story_so_far, _sum_beat)   # 2계층 요약(한줄+상세)
            if _degraded:   # P-2 ②③: 요약 실패 → detail 이 beat 합성 요지(프로즈 아님). 신뢰 강등 플래그 영속 + 실패 유형 로그.
                summary_degraded = True
                self.bus.emit("summarize", "degraded", chapter=ch_no, failure=_degraded.get("failure"))
            _track("summarize", _t, _ts)
            # CN-2 claim-audit: 자유형 사실 모순을 과거 회차 프로즈와 대조(RAG-grounded·비차단·non-hard).
            #   status 는 위에서 이미 확정됨 — 여기서 무엇을 찾든 발행을 막지 않는다(작가 가시화 advisory).
            if getattr(self.settings, "claim_audit", True):
                _t, _ts = _mark()
                from .claim_audit import audit_chapter
                _q = f"{beat.get('summary', '')} {' '.join(beat.get('key_events', []) or [])} {' '.join(roster)}"
                claim_findings = audit_chapter(self.provider, rag, chapter_text, ch_no, query_hint=_q)
                _track("claim_audit", _t, _ts)
                for a in claim_findings:
                    self.bus.emit("claim_audit", "contradiction", chapter=ch_no, claim=a.get("claim", ""),
                                  canon=a.get("canon", ""), ref=a.get("ref", ""), why=a.get("why", ""))
            # DG-1: 인물별 대사 원장(확정 후 관측 전용·비차단·+1 aux콜). 생성 입력 주입 금지 계약 — 소비는 검증·계측·열람만.
            if getattr(self.settings, "dialogue_ledger", True):
                _t, _ts = _mark()
                try:
                    from .dialogue_ledger import build_ledger
                    # DG-4: pov_entity_id 로 '서술자' 표기를 POV 개체로 결정론 승격(CX-4 배선 재사용)
                    # VX-1: 대사 태그에서 뽑은 (화자,대사) 쌍이 있으면 화자 귀속 LLM 콜을 건너뛴다(_tagged_pairs).
                    #   None(태그 없는 작품·미준수 폴백)이면 종전 경로(따옴표 파싱 + LLM 귀속) 그대로.
                    dialogue_ledger = build_ledger(self.aux_provider, chapter_text, ontology,
                                                   bus=self.bus, chapter=ch_no, pov_entity_id=pov_id,
                                                   tagged=_tagged_pairs)
                except Exception:
                    dialogue_ledger = []   # 관측 실패가 확정을 막으면 안 됨(빈 원장 정직)
                _track("dialogue_ledger", _t, _ts)
            self.bus.emit("finalize", "done", chapter=ch_no, indexed=indexed, wiki_pages=pages,
                          summarized=bool(summary))
        recovery_hints = []
        if status != ChapterStatus.FINALIZED:
            from .recovery import recovery_report
            recovery_hints = recovery_report(hard_remaining)   # 작가용 자연어 진단+회복 레버(결정론·LLM 0콜)
            if _empty:   # 빈 생성 — hard 가 없어 recovery_report 가 비므로 전용 안내(silent 금지)
                recovery_hints = [{"kind": "empty_generation", "entity": "",
                                   "diagnosis": "이번 회차 본문이 비어 있습니다(생성 실패·거부로 추정).",
                                   "fix": ["‘이대로 다시 생성’으로 재시도하세요(작가 지시를 더하면 도움이 됩니다).",
                                           "반복되면 다른 지시·비트로 바꿔 보세요."], "levers": ["rewrite"]}]
            self.bus.emit("finalize", "escalation", chapter=ch_no,
                          hard=([v.kind for v in hard_remaining] or ["empty_generation"]), recovery=recovery_hints)

        rec = ChapterRecord(
            chapter=ch_no, title=beat.get("title", ""), status=status, text=chapter_text,
            summary=summary, detail_synopsis=detail_synopsis, summary_degraded=summary_degraded,
            scenes=1 + ext, n_retrieved=len(narrative), indexed_chunks=indexed,
            wiki_pages_touched=pages, initial_violations=initial_caught or [], recovery_hints=recovery_hints,
            dialogue_ledger=dialogue_ledger,   # DG-1: 관측 원장(생성 입력 주입 금지)
            chapter_function=beat.get("chapter_function", ""), hook_type=beat.get("hook_type", ""),
            closing_device=beat.get("closing_device", ""),   # DP-22: 마무리 장치 자기 라벨 영속(다음 회차 순환 입력)
            scene_form=beat.get("scene_form", ""),   # SX-1: 장면 안무 자기 라벨 영속(다음 회차 순환 입력)
            world_reveal=list(beat.get("world_reveal") or []),   # SX-3: 세계 노출 슬롯 영속(advisory·계획 재료)
            time_advance=beat.get("time_advance", ""), time_delta=beat.get("time_delta"),   # G4 기능차원 + CN-1 구조화 델타 영속
            place=beat.get("place", ""), claim_audit=claim_findings,   # CN-2: 자유형 모순 advisory 영속(비구속)
            gen_context={"draft": draft_ctx},   # 디버그: 집필 입력(계획 입력은 copilot 가 'plan' 키로 합침)
            style_repairs=style_repairs,   # SP-1 Stage B: 국소 리듬 수리 내역(additive·투명성 영속). HM-1 ON 시 빈 리스트(HM-1이 흡수)
            humanize=humanize_entries,     # HM-1b: 결정론 탐지→Claude 윤문 국소 수술 내역(additive·투명성 영속·작가 확인 요망 표기)
            final_violations=final.violations, rounds=rounds, usage_by_stage=stage_usage,
            time_by_stage=stage_time)   # TM-1: 단계별 소요 시간(usage 시간판 대칭 — 콜 없는 단계는 미포함)
        rec._final_claims = list(final.claims)   # OV-2: 정규화+증거강제 클레임을 propose 소비용으로 실음(휘발·같은 회차 내에서만)
        # GA-1: 생성 트레이스(중간 산출물 전량) — 첫 초안(수술 전)·재작성 라운드 전문·style_judge 판정 전문·
        #   humanize 스팬 before/after·최종본. 사이드카 영속은 서비스가 generate() 반환 직후 repo.save_trace 로
        #   수행(본체 JSON 무팽창). OFF(config gen_trace) 면 _gen_trace=None → 서비스가 save_trace 미호출(바이트 동일).
        #   ts 는 서버 주입 불가(엔진은 시각 소스 없음·harness 규율) → None(Date.now 금지·결측 정직). 서비스가 채운다.
        if getattr(self.settings, "gen_trace", True):
            rec._gen_trace = {
                "kind": "generate",
                "chapter": ch_no,
                "ts": None,   # 서비스 계층이 발행 직후 서버 시각으로 채움(엔진은 시각 소스 없음)
                "status": status.value if hasattr(status, "value") else str(status),
                "first_draft": _trace_first_draft,        # (a) 수술 전 원본 회차(이어쓰기 완료 후)
                "rewrite_rounds": _trace_rewrite_rounds,  # (b) 각 재작성 라운드 전문+사유(RoundTrace 는 카운트만)
                "style_judgment": _trace_style_judgment,  # (c) 판정 전문(needs_repair·spans·reason·reference)
                "humanize_spans": _trace_humanize_spans,  # (d) 스팬별 before/after
                "final_text": chapter_text,               # (e) 발행 최종본(diff 기준)
                "corrections": list(draft_ctx.get("corrections") or []),
                "usage_by_stage": dict(stage_usage),
                "time_by_stage": dict(stage_time),
            }
        return rec
