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

# G9: 'to be continued' 추가. 줄머리 경로엔 바깥 '회/편'을 안 넣는다(회의/편의점 등 오탐 위험);
# '(다음 회에서 계속)' 류는 아래 괄호-단독행 경로(전체가 괄호=거의 확실히 메타)에서 잡는다(sanitize 동일 범주, 작법 검출 아님).
_META_LINE = re.compile(r"^\s*[\[【(#\-=*]{0,3}\s*(END|끝|다음\s*(화|회차|:)|계속|to\s*be\s*continued|장면\s*(종료|전환)|scene|chapter|메모|note|todo|작가\s*주)"
                        r".{0,80}$", re.IGNORECASE)
_BRACKET_ONLY = re.compile(r"^\s*[\[【(].{0,100}[\]】)]\s*$")   # (괄호)/[대괄호] 단독행 — '(다음 회, …)' 본문 박힘 제거


def sanitize_meta(text: str) -> str:
    """생성물에서 메타텍스트([END…], 장면 표지, 작가 메모류) 라인 제거 — FINALIZED 본문 누출 차단(결정론)."""
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

from ..config import Settings
from ..domain.types import (ContextBoard, SceneSpec, ChapterRecord, ChapterStatus,
                            RoundTrace, AuthorDirective, SignalGrade, RetrievedItem)
from ..domain.world import StyleSpec
from ..llm.base import LLMProvider
from .checker import Checker
from .prompts import PromptAssembler, render_style, floor_only
from .observability import EventBus


class ChapterGenerator:
    def __init__(self, provider: LLMProvider, checker: Checker, style: StyleSpec,
                 event_bus: EventBus, settings: Settings):
        self.provider = provider
        self.checker = checker
        self.style = style
        self.bus = event_bus
        self.settings = settings
        self.assembler = PromptAssembler(style, settings.prev_chapter_context_chars)
        self.style_block = render_style(style)   # rules+author_style 렌더. 정책 패치는 update_style_policy 가
        #   세션을 evict→ 다음 요청에 generator 재구성하므로 캐시여도 스테일 없음(라이브 참조 불필요).
        self.floor_block = floor_only()          # B-10: 교정 패스(_rewrite)용 — 미학 오버레이 없이 바닥 제약만(재문체화 차단).

    # ---- LLM 격리 지점 ----
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
        "cliffhanger": ("\n\n[이 장면은 회차의 마지막] 깔끔히 닫지 말고 다음 화가 궁금해지는 "
                        "위기·반전·미끼로 끊어라(마지막 한두 줄에서 멈춰라)."),   # 은어 누출('절단.' 단독행) 소스 제거
        "soft": ("\n\n[이 장면은 회차의 마지막] 다음 화가 궁금해질 여지는 남기되, 과도한 위기 조성 없이 "
                 "이 회차의 감정·상황을 자연스러운 여운으로 마무리하라."),
        "none": "",
    }
    _CLOSING = ("\n\n[이 장면은 '작품 전체의 마지막' 회차다] 다음 화를 예고하는 미끼로 끊지 말고 — 중심 갈등과 감정선을 매듭지어라. "
                "본문에서 제시된 미결 선택은 반드시 결행하고, 새로운 미스터리·신호·떡밥·세력 도입 금지. 여운 있게 작품을 닫아라.")

    def _draft(self, board, scene, prev_scenes, last=False, closing=False, recent_tails=None,
               chapter_mode=False) -> str:
        hook = ""
        if last:
            hook = self._CLOSING if closing else self._HOOKS.get(self.style.ending_hook, self._HOOKS["cliffhanger"])
            if hook and not closing and recent_tails:
                tails = "\n".join(f"- …{t[-70:]}" for t in recent_tails[-3:] if t)
                hook += ("\n(최근 회차들의 끝:\n" + tails +
                         "\n— 위와 같은 유형의 끝맺음('접근하는 신호/누군가 부르는 소리/다가오는 무언가' 류 포함) 반복 절대 금지. "
                         "위기·반전·폭로·결단·관계 균열 중 최근에 안 쓴 다른 축으로 끊어라.)")
            if hook and not closing:
                hook += "\n(예고편 문장 금지 — '다음 위기가 모습을 드러낸다' 류 서술 예고 대신, 장면 '안'의 구체 사건·대사로 끊어라.)"
        if chapter_mode:   # 회차 집필: 분량·장면 어휘는 모델 입력 금지(절단점은 글자 수가 아니라 극적 순간의 문제)
            out_instr = ("\n\n이번 회차 본문만 출력(머리말·설명·메타·예고문 금지). "
                         "분량을 채우기 위한 묘사 늘리기 금지 — 이야기가 자연스러운 절단점에 이르면 거기서 멈춰라. "
                         "회차 전체에서 시제(과거형 기조)·따옴표 글리프를 일관되게 유지하고, 같은 사건·클라이맥스를 두 번 쓰지 마라.")
            mt = self.settings.chapter_max_tokens
        else:
            out_instr = "\n\n본문만 출력(머리말·설명·메타 금지)."
            mt = self.settings.gen_max_tokens
        return self.provider.chat(
            [{"role": "system", "content": f"{self.style.system_persona} 확정 설정 절대 위반 금지.\n{self.style_block}"},
             {"role": "user", "content": self.assembler.assemble(board, scene, prev_scenes) + hook + out_instr}],
            temperature=0.85, max_tokens=mt)   # 온도↓는 클리셰(고확률 토큰)를 오히려 늘릴 수 있어 보류 — 측정 후 재검토

    def _continue(self, board, sofar: str, closing=False, recent_tails=None, key_events=None) -> str:
        """진행 이어쓰기 — 출고 분량은 지시가 아니라 '이야기 전진'으로 채운다(하한 지시=물 타기 차단).
        전문 말미를 보고 절단점에서 잇는 순차 연속(화 경계 인계와 동일 메커니즘 — 병렬 블록 접합 아님)."""
        hook = self._CLOSING if closing else self._HOOKS.get(self.style.ending_hook, self._HOOKS["cliffhanger"])
        if hook and not closing and recent_tails:
            tails = "\n".join(f"- …{t[-70:]}" for t in recent_tails[-3:] if t)
            hook += "\n(최근 회차들의 끝:\n" + tails + "\n— 같은 유형의 끝맺음 반복 금지.)"
        # G9: 이 회차에 계획된 사건 목록을 참고로(아직 안 일어난 쪽으로 자연히 전진 — 회차 종결 마커 넘어 계속 쓰는 사고 방지)
        plan_ctx = (f"\n(이 회차의 계획 사건: {key_events} — 아직 다뤄지지 않은 것이 있으면 그쪽으로 전진. "
                    "회차를 닫는 마무리 문장(예: '다음 회에 계속')을 쓰지 마라.)" if key_events else "")
        spec = SceneSpec(index=1, goal=(
            "위 '지금까지 쓴 본문'의 마지막 문장에서 '즉시' 이어서, 이야기를 다음 국면으로 한 단계 전진시켜 계속 써라. "
            "이미 일어난 사건·대화의 재연·요약 금지, 새 인물 창조 금지. 새 절단점에 이르면 멈춰라." + plan_ctx), key_events=[])
        # 이어쓰기 프롬프트에는 직전 회차 전문·누적 줄거리를 빼고 '지금 쓴 본문 말미'만 준다 —
        # 둘이 함께 들어가면 모델이 요약된 과거를 '재서술'하는 압력(재설계 리뷰 P0-2). 캐논(ground_truth)·보이스는 유지.
        cont_board = board.model_copy(update={"prev_chapter": "", "story_so_far": ""})
        return self.provider.chat(
            [{"role": "system", "content": f"{self.style.system_persona} 확정 설정 절대 위반 금지.\n{self.style_block}"},
             {"role": "user", "content": self.assembler.assemble(cont_board, spec, sofar[-3500:]) + hook
              + "\n\n이어지는 본문만 출력(이미 쓴 부분 재출력 금지, 머리말·메타 금지)."}],
            temperature=0.85,   # 온도↓는 클리셰(고확률 토큰)를 오히려 늘릴 수 있어 보류 — 측정 후 재검토
            max_tokens=self.settings.chapter_max_tokens)

    def _rewrite(self, scene_text, violations, board, max_tokens: int | None = None) -> str:
        vlist = "\n".join(f"- [{v.kind}/{v.grade.value}] {v.entity}: 캐논={v.canon} 위반={v.text}" for v in violations)
        gt = "\n".join(f"- {f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth)
        out = self.provider.chat(
            [{"role": "system", "content":
              "교정 작가. 지적된 설정 위반만 고치고 문체·줄바꿈·분량은 보존. 확정 설정·세계규칙이 비트보다 우선. "
              "'제거 상태'(사망·소멸 등) 인물의 현재 행동은 회상/환영/삭제로, 속성·관계는 캐논값으로 교정. 본문만.\n"
              + self.floor_block},   # B-10: 미학 오버레이(작가 문체) 주입 제거 — 최소 교정이 재문체화로 번지는 것 차단
             {"role": "user", "content": f"[확정 설정]\n{gt}\n[설정 위반]\n{vlist}\n[원본 본문]\n{scene_text}"}],
            temperature=0.4, max_tokens=max_tokens or self.settings.gen_max_tokens)
        # 길이 보존 가드: 절단/메타응답이 본문을 갉아먹는 회귀 차단(R2 본문 파괴 교훈 — 전량 재작성 채널의 안전망)
        return out if len(out or "") >= len(scene_text) * 0.6 else scene_text

    def _reformat(self, text: str) -> str:
        """토막 행갈이 붕괴 복구 — 내용 불변, 조판만 정상 산문으로(문체 드리프트 루프 차단).
        출력이 원문의 60% 미만이면 절단/메타응답으로 보고 원문 유지(본문 파괴 가드)."""
        out = self.provider.chat(
            [{"role": "system", "content":
              "조판 교정자. 내용·문장·대사를 단 한 글자도 바꾸지 말고, 과도하게 토막난 행갈이만 정상 산문으로 재조판하라. "
              "대사는 한 줄에 하나 유지, 지문은 2~4문장 문단으로 묶어라. 본문만 출력."},
             {"role": "user", "content": text}],
            temperature=0.0, max_tokens=max(self.settings.gen_max_tokens * 2,
                                            self.settings.chapter_max_tokens))
        # 계약(공백/행갈이만 변경)은 코드로 '완전' 검증 가능 — 길이 60% 차용 가드(40% 손실 허용) 폐기
        if out and re.sub(r"\s+", "", out) == re.sub(r"\s+", "", text):
            return out
        self.bus.emit("draft_chapter", "reformat_rejected", out_chars=len(out or ""), in_chars=len(text))
        return text

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
                temperature=0.0, max_tokens=2500)
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

    def _continuity_polish(self, chapter_text: str, names: list[str] | None = None) -> str:
        """회차 내부 연속성 교정 — 패치 방식: LLM 은 '수정 쌍'만 내고 코드가 정확일치 치환만 적용.
        (전량 재작성 방식은 메타응답이 본문을 덮어쓰거나 max_tokens 절단으로 본문을 파괴했음 — 시뮬 실측 결함.
        패치 방식은 본문이 원형 그대로라 증발·절단·분량손실이 구조적으로 불가능.)"""
        try:
            roster = f"[공식 인명: {', '.join(names or [])}]\n" if names else ""
            res = self.provider.chat_json(
                [{"role": "system", "content":
                  "출고 검수자. 이 회차에서 다음 결함만 찾아라: ①수치(배터리·산소·식량·GPU·시간)·소지품·위치, "
                  "그리고 인원수/개수/횟수 등 '수량 주장과 실제 묘사의 불일치'(예: '다섯 겹'이라 했는데 셋만 등장, 'N명'이라 선언하고 실제 등장 수가 다름)의 회차 내 모순 "
                  "②공식 인명의 오염(한 글자 다른 오타) ③문두/문중 절단된 깨진 문장 ④화자의 자기 언급 메타문장(예: '이 장의 끝에') "
                  "⑤같은 부사구·관용구('짧게 말했다','그 순간' 등)의 과도 반복(가장 잦은 것 일부를 동의 표현으로) ⑥현재형 시제 누출(과거형으로) ⑦장면 이어붙임 중복 — 같은 사건/탈출이 두 번 재생되거나 파괴된 것이 무설명 부활하면 중복 단락을 짧은 경과 문장으로 치환. "
                  "수정은 본문 구절의 정확한 치환 쌍으로만(find=본문에 실제 있는 구절 그대로). "
                  '없으면 빈 배열. JSON: {"fixes":[{"find":"","replace":""}]}'},
                 {"role": "user", "content": roster + chapter_text}],
                temperature=0.0, max_tokens=1600)
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
    def revise_prose(self, directive: str, before_text: str, span_text: str = "",
                     passes: list[str] | None = None, ids: list[str] | None = None,
                     ontology=None, chapter_no: int = 0) -> str:
        """작가 지시에 따라 회차 본문(또는 구간)의 *산문*만 다듬는다 — 사실 불변.

        설정·사건·수치·관계·캐논은 단 하나도 바꾸지 않고 표현·문체·가독성·대사 톤·반복·조판만 개선한다.
        D1 준수: _continuity_polish·_regen_tail·_fix_tics·_rewrite 는 '사실 변경 패스'라 여기서 절대 호출 금지.
        opt-in 선택 교정은 _reformat(공백/조판)·_fix_tense(종결어미)만 허용한다.
        span_text 가 있으면 그 구간만 다듬어 원문의 해당 위치에 replace(저장 단위는 회차 전체 텍스트).
        """
        passes = passes or []
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
                + f"[원문]\n{target_text}")
        try:
            out = self.provider.chat(
                [{"role": "system", "content": sys},
                 {"role": "user", "content": user}],
                temperature=0.4, max_tokens=self.settings.gen_max_tokens)
        except Exception:
            self.bus.emit("revise", "llm_failure")
            return before_text

        # 3) 길이 가드(구간 기준) — 절단/메타응답이 본문을 갉아먹는 회귀 차단. 위반 시 원문 유지
        out = (out or "").strip()
        if not out or len(out) < len(target_text) * 0.5 or len(out) > len(target_text) * 1.8:
            self.bus.emit("revise", "length_guard_triggered",
                          in_chars=len(target_text), out_chars=len(out))
            return before_text

        # 4) 메타텍스트 라인 제거(생성물과 동일 결정론 살균)
        out = sanitize_meta(out)
        if not out:
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
            return before_text
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
    def _summarize(self, chapter_text: str, prior_summary: str = "") -> tuple[str, str]:
        """2계층 요약(컨텍스트 기아 해소 ①): (한 줄, 상세 시놉시스 ~1500자).
        기존 348자 단일 요약은 보존율 5.4% — 상세 레이어가 사건 인과·감정 변화·물리 디테일·미결을 보존(~25%)."""
        try:
            r = self.provider.chat_json(
                [{"role": "system", "content":
                  "웹소설 회차 기록자. 두 가지를 만들라:\n"
                  "1) oneliner: 한 문장 요약(나중 회차에서 원거리 기억용).\n"
                  "2) synopsis: 상세 시놉시스 1,200~1,600자 — 사건의 인과 순서, 인물별 결정/감정 변화, "
                  "물리 디테일(자원·소지품·위치·시각), 대화의 핵심 내용, 미결 긴장/복선. "
                  "다음 회차 집필자가 본문 없이도 이어 쓸 수 있을 밀도로. 감상·문체 묘사 금지.\n"
                  'JSON: {"oneliner":"","synopsis":""}'},
                 {"role": "user", "content":
                  (f"[직전까지 줄거리]\n{prior_summary[-1200:]}\n\n" if prior_summary else "")
                  + f"[이번 회차 본문]\n{chapter_text}"}],
                temperature=0.2, max_tokens=2200)
            one = (r.get("oneliner") or "").strip()
            syn = (r.get("synopsis") or "").strip()
            if one or syn:
                return (one or syn[:160]), (syn or chapter_text[:400])
            self.bus.emit("summarize", "empty_response")
        except Exception:
            self.bus.emit("summarize", "parse_failure")
        return chapter_text[:200], chapter_text[:400]   # 실패 폴백(요약 구멍·영구망각 방지)

    def _fix_tics(self, chapter_text: str, offenders: list[tuple[str, int]]) -> str:
        """틱 과용 국소 교정 — 위반 구절을 '명시'하고 패치(고유 구절 치환)로만 다양화. 전량 재생성 금지."""
        try:
            lst = ", ".join(f"'{p}'({n}회)" for p, n in offenders)
            res = self.provider.chat_json(
                [{"role": "system", "content":
                  "문장 다양화 검수자. 다음 습관구가 과용됐다: " + lst + ". "
                  "각 습관구의 출현 대부분을 문맥에 맞는 다양한 표현으로 바꿔라(전부 같은 단어로 바꾸지 말 것). "
                  "수정은 치환 쌍으로만 — find 는 본문에 실제로 1번만 나오는 7자 이상 고유 구절(습관구 포함)을 그대로. "
                  '인명·사건·수치 불변. JSON: {"fixes":[{"find":"","replace":""}]}'},
                 {"role": "user", "content": chapter_text}],
                temperature=0.3, max_tokens=2500)
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

    def _regen_tail(self, chapter_text: str, recent_tails: list[str]) -> str:
        """말미 훅 재탕 → 마지막 1~2문단만 재작성해 접합(국소) — 본문 보존."""
        lines = chapter_text.splitlines()
        keep, tail = "\n".join(lines[:-8]), "\n".join(lines[-8:])
        try:
            prev = "\n".join(f"- …{t[-70:]}" for t in recent_tails[-4:])
            out = self.provider.chat(
                [{"role": "system", "content":
                  "회차 말미 교체 작가. 주어진 끝부분을, 직전 회차들과 '다른 축'의 긴장(위기/반전/폭로/결단/관계 균열 중 "
                  "최근 미사용 축)으로 다시 써라. 길이 비슷하게, 앞 내용과 자연 연결. 본문만.\n[최근 회차들의 끝]\n" + prev},
                 {"role": "user", "content": f"[앞 문맥(마지막 부분)]\n{keep[-800:]}\n\n[교체할 끝부분]\n{tail}"}],
                temperature=0.8, max_tokens=1200)
            if out and len(out.strip()) > 80:
                self.bus.emit("quality", "tail_regen")
                return keep + "\n" + sanitize_meta(out.strip())
        except Exception:
            pass
        return chapter_text

    def generate(self, ch_no, beat, ontology, rag, wiki, directives=None,
                 prev_chapter_text: str = "", story_so_far: str = "", anchors=None,
                 closing: bool = False, recent_tails=None, restraint=None) -> ChapterRecord:
        directives = directives or []
        involved = beat.get("entities") or list(ontology.entities.keys())
        self.bus.emit("plan_chapter", "start", chapter=ch_no, title=beat.get("title", ""))
        # 단계별 토큰 계측(단위경제 — 일관성 오버헤드율 재료)
        stage_usage: dict = {}

        def _track(stage, before_tokens):
            stage_usage[stage] = stage_usage.get(stage, 0) + (self.provider.usage.chat_tokens - before_tokens)
        # 보이스 카드: 등장 인물 말투의 '결'(스타일 지침 — 캐논 아님). 시그니처 문구의 기계 반복은 틱 생산기(소스 차단)
        voice_cards = "\n".join(f"- {e.name}: {e.voice}"
                                for e in (ontology.entities.get(i) for i in involved)
                                if e is not None and getattr(e, "voice", ""))
        if voice_cards:
            voice_cards += ("\n(보이스는 태도·어휘의 '결'이다 — 특정 어미·문구를 매 대사에 찍지 마라. "
                            "시그니처 표현이 있다면 회차당 1~2회, 결정적 순간에만.)")
        if restraint:   # 전권 과용 표현 절제(작품-전역 원장 → 회차 생성 입력으로, 예방측)
            voice_cards += ("\n[표현 절제 — 이 작품에서 이미 과용된 표현. 이번 화에서는 거의 쓰지 마라]\n"
                            + ", ".join(restraint[:8]))
        # G7+C-1: 고유명사 명부를 '확정/미확정'으로 분층(참고). 확정은 '같은 표기 쓰라', 잠정 떡밥은 '새 이름 발명만 피하되 고정·확정 금지'.
        confirmed_names = sorted({e.name for e in ontology.entities.values() if e.name and not getattr(e, "provisional", False)})
        prov_names = sorted({e.name for e in ontology.entities.values() if e.name and getattr(e, "provisional", False)})
        if confirmed_names:
            voice_cards += ("\n[이미 등록된 고유명사(참고 — 같은 대상이면 이 표기를 그대로 쓰라)]\n"
                            + ", ".join(confirmed_names[:60]))
        if prov_names:
            voice_cards += ("\n[미확정 명사(아직 정체불명 떡밥 — 새 이름 발명만 피하고, 확정 인명처럼 고정·반복 호명하지 마라)]\n"
                            + ", ".join(prov_names[:30]))

        narrative = list(anchors or [])   # 엔딩/아크 앵커(narrative, ground_truth 아님) 상단
        # M-1: 세계 규칙 텍스트 주입(사장돼 있던 ontology.rules) — 헤더는 '세계규칙 위반 금지'라 약속하나 본문이 한 번도 못 보던 결함 해소(advisory).
        world_rules = list(getattr(ontology, "rules", None) or [])
        if world_rules:
            narrative.insert(0, RetrievedItem(source="worldrule", ref="rules",
                text="[세계 규칙 — 이 작품의 불변 규칙. 어기지 마라]\n" + "\n".join(f"- {r}" for r in world_rules[:12])))
        if ch_no > 1:
            # N-1: RAG 후보에서 '직전 회차' 제외(as_of=ch_no-2) — prev_chapter 전문이 직전을 전담하므로 검색 슬롯은 먼 회차 복선 회수에 쓴다.
            narrative += rag.search(beat.get("summary", ""), max(0, ch_no - 2), k=self.settings.rag_k)
            narrative += wiki.retrieve(beat.get("summary", ""), ch_no - 1, k=self.settings.wiki_k)
        ground_truth = (ontology.canon_facts(involved, ch_no)
                        + ontology.canon_relations(involved, ch_no))   # 확정 관계도 '박기'(ground_truth)
        board = ContextBoard(chapter=ch_no, ground_truth=ground_truth,
                             narrative=narrative, authority=directives,
                             prev_chapter=prev_chapter_text, story_so_far=story_so_far,
                             voice_cards=voice_cards)
        self.bus.emit("assemble_memory", "done", chapter=ch_no,
                      ground_truth=len(board.ground_truth), retrieved=len(narrative))
        # 디버그(D-1~9): 집필에 실제로 들어간 입력 슬롯을 폭넓게 캡처 — system 헤더(문체/세계규칙)·끝맺음 정책·이어쓰기·교정까지.
        pv = prev_chapter_text or ""
        draft_ctx = {
            "persona": (self.style.system_persona or "")[:240],
            "style_rules": list(self.style.rules),                       # D-1: 매 draft 의 system 헤더 문체 규칙(상수)
            "author_style": (self.style.author_style or "")[:240],       # Layer 2 작가 문체 오버레이(설정 시만)
            "world_rules": world_rules,                                  # D-6/M-1: 실제 주입되는 세계 규칙
            "ending_hook_mode": self.style.ending_hook,                  # D-3: 끝맺음 정책
            "recent_tails": [(t or "")[-80:] for t in (recent_tails or [])],
            "ground_truth": [f"{f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth],
            "anchors": [{"source": a.source, "ref": a.ref, "text": (a.text or "")[:240]} for a in narrative],
            "story_so_far": (story_so_far or "")[:2500],
            "story_so_far_chars": len(story_so_far or ""),               # D-7: 실제 길이(트림 전)
            "directives": [d.text for d in directives],
            "voice_roster": (voice_cards or "")[:1600],
            "prev_chapter_chars": len(pv),
            "prev_chapter_excerpt": (pv[:200] + (" …(중략)… " + pv[-200:] if len(pv) > 420 else "")),  # D-7: 머리/꼬리 발췌
            "continuations": 0,                                          # D-4: 이어쓰기 콜 수(아래서 갱신)
            "corrections": [],                                           # D-8: 교정 단계 발화 목록(아래서 갱신)
            "beat": {k: beat.get(k) for k in ("title", "summary", "key_events", "entities",
                                              "chapter_function", "hook_type", "time_advance", "place")},
        }

        # ---- 비트 단위 생성 · 코드 조립(재설계: 장면 개념·분량 지시 폐기) ----
        # 장면 분해(scenes_per_chapter)는 구 토큰 한계 시절 3콜 분할의 유물 — 장면 수는 설계 입력이 아니라 결과다.
        # 분량도 모델 입력에서 제거: 지시로 누르면 절단점이 글자 수에 종속되고(급결말), 하한을 지시하면 물 타기가 된다.
        # 모델은 비트 하나를 '자연스러운 절단점'까지 쓰고, 출고 규범(분량)은 코드가 '진행 이어쓰기'로 채운다
        # — 생성 단위(비트=극적 곡선)와 출고 단위(회차=플랫폼 규범)의 분리. 이어쓰기는 전문을 보고 절단점에서
        # 잇는 순차 연속(검증된 화 경계 인계와 동일 메커니즘)이지, 실패했던 병렬 블록 접합이 아니다.
        spec = SceneSpec(index=0, goal=beat.get("summary", ""), key_events=beat.get("key_events", []))
        rounds: list[RoundTrace] = []
        initial_caught = None
        self.bus.emit("draft_chapter", "start", chapter=ch_no)
        _t = self.provider.usage.chat_tokens
        text = sanitize_meta(self._draft(board, spec, "", last=True, closing=closing,
                                         recent_tails=recent_tails, chapter_mode=True))
        norm = int(self.style.target_chars_per_chapter * 0.85)   # 출고 규범(코드 판정 — 모델은 분량을 모른다)
        draft_ctx["length_norm"] = norm                          # D-2: 출고 규범(문체규칙의 '5천자' 지시와 대조 가능)
        ext = 0
        # 보강 상한 2→4: 건조한 author_style(또는 짧은 비트)은 세그먼트가 짧아 2회로 norm(5천자) 미달(페르소나 실측 3,618자).
        # '이야기 전진'으로만 채우는 원칙은 유지 — 아래 진행 가드(<200자 중단)가 '진짜 더 쓸 게 없으면' 알아서 멈춰 물타기·낭비를 막는다.
        # 정상 문체는 0~2회로 norm 도달해 루프를 빠져나가므로 추가 비용 없음(짧게 나오는 회차만 3~4회 사용).
        while len(text) < norm and ext < 4:
            ext += 1
            self.bus.emit("draft_chapter", "extend", chapter=ch_no, chars=len(text), round=ext)
            # G9: 이어쓰기에 '이 회차의 계획 사건'을 컨텍스트로(아직 못 다룬 쪽으로 전진하게 — 정보 제공, 분량 지시 아님)
            more = sanitize_meta(self._continue(board, text, closing=closing, recent_tails=recent_tails,
                                                key_events=beat.get("key_events") or []))
            if len(more.strip()) < 200:      # 무의미 연장 → 중단(짧은 회차로 출고가 낫다)
                break
            text = text.rstrip() + "\n\n" + more.strip()
        draft_ctx["continuations"] = ext   # D-4: 이어쓰기 콜 수(이 회차 후반부가 '다른 컨텍스트'로 쓰였는지 가시화)
        if len(text) < norm:   # G9: 규범 미달 출고 가시화(silent 금지) — 작가·G3 회고 입력(차단 아님)
            self.bus.emit("draft_chapter", "under_norm", chapter=ch_no, chars=len(text), norm=norm)
        tail_seg = "\n".join(text.splitlines()[-15:])
        if (fragmentation_score(text) < 22 or fragmentation_score(tail_seg) < 14
                or short_line_ratio(text) > 0.15):
            # 토막 행갈이 붕괴(전체 또는 말미 국소) → 조판 복구
            self.bus.emit("draft_chapter", "reformat", chapter=ch_no)
            draft_ctx["corrections"].append("reformat")   # D-8
            text = sanitize_meta(self._reformat(text))
        _track("draft", _t)
        best_text, best_hard = text, None     # M5: hard 위반 최소 텍스트 보존
        ch_budget = self.settings.chapter_max_tokens
        for r in range(self.settings.max_rewrite_rounds + 1):
            _t = self.provider.usage.chat_tokens
            res = self.checker.check_text(text, ontology, ch_no, involved)
            _track("check", _t)
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
            _t = self.provider.usage.chat_tokens
            text = sanitize_meta(self._rewrite(text, text_hard, board, max_tokens=ch_budget))
            draft_ctx["corrections"].append(f"rewrite#{r}({','.join(v.kind for v in text_hard[:3])})")   # D-8
            _track("rewrite", _t)

        chapter_text = best_text     # M5: 최선본 채택
        # ---- 품질 결정론 게이트(닫힌 루프): 검출=코드, 교정=국소 ----
        from .quality_gates import word_tics, hook_repeat_semantic, strip_directive_leak
        chapter_text = strip_directive_leak(chapter_text)              # 지시어 누출(R7 '절단.') 결정론 제거
        roster = {ontology.entities[i].name for i in involved if i in ontology.entities}
        offenders = [(p, n) for p, n in word_tics(chapter_text, roster, cap=4)][:5]
        if offenders:
            _t = self.provider.usage.chat_tokens
            chapter_text = self._fix_tics(chapter_text, offenders)
            draft_ctx["corrections"].append(f"fix_tics({','.join(p for p,_ in offenders[:3])})")   # D-8
            _track("quality", _t)
            residual = word_tics(chapter_text, roster, cap=4)
            if residual:   # 교정 후 재검(닫힌 루프) — 잔존은 무음 통과 금지
                self.bus.emit("quality_gate", "tics_residual", chapter=ch_no, tics=residual[:3])
        if recent_tails and not closing:
            tail_now = " ".join([ln for ln in chapter_text.splitlines() if ln.strip()][-3:])
            if hook_repeat_semantic(self.provider, tail_now, recent_tails) > 0.82:   # 의미 유사(템플릿 재탕)
                _t = self.provider.usage.chat_tokens
                chapter_text = self._regen_tail(chapter_text, recent_tails)
                draft_ctx["corrections"].append("regen_tail(훅 재탕)")   # D-8: 말미가 교체됐음(독자평가 통과해도 말미는 다른 컨텍스트)
                _track("quality", _t)
        if getattr(self.settings, "continuity_polish", True):   # 출고 검수(수치·인명·절단·메타·틱·시제) — 패치 후 게이트 재검
            _t = self.provider.usage.chat_tokens
            names = [ontology.entities[i].name for i in involved if i in ontology.entities]
            chapter_text = sanitize_meta(self._continuity_polish(chapter_text, names=names))
            draft_ctx["corrections"].append("continuity_polish")   # D-8
            _track("polish", _t)
        from .quality_gates import tense_leak_ratio
        if tense_leak_ratio(chapter_text) > 0.05:   # 시제 누출(현재형 종결 혼입) — 폴리시 ⑥의 결정론 백스톱
            _t = self.provider.usage.chat_tokens
            chapter_text = self._fix_tense(chapter_text)
            draft_ctx["corrections"].append("fix_tense")   # D-8
            _track("quality", _t)
        _t = self.provider.usage.chat_tokens
        final = self.checker.check_text(chapter_text, ontology, ch_no, involved)
        _track("check", _t)
        hard_remaining = final.hard
        status = ChapterStatus.FINALIZED if not hard_remaining else ChapterStatus.ESCALATED

        indexed = pages = 0
        summary = detail_synopsis = ""
        if status == ChapterStatus.FINALIZED:
            indexed = rag.index_chapter(ch_no, chapter_text)
            _t = self.provider.usage.chat_tokens
            try:
                pages = wiki.ingest_chapter(ch_no, chapter_text, ontology, reviewed=True)
            except Exception:   # 위키는 narrative(비구속) — 실패가 회차 확정을 막으면 안 됨(가시화 후 계속)
                pages = 0
                self.bus.emit("finalize", "wiki_failure", chapter=ch_no)
            _track("wiki", _t)
            _t = self.provider.usage.chat_tokens
            summary, detail_synopsis = self._summarize(chapter_text, story_so_far)   # 2계층 요약(한줄+상세)
            _track("summarize", _t)
            self.bus.emit("finalize", "done", chapter=ch_no, indexed=indexed, wiki_pages=pages,
                          summarized=bool(summary))
        recovery_hints = []
        if status != ChapterStatus.FINALIZED:
            from .recovery import recovery_report
            recovery_hints = recovery_report(hard_remaining)   # 작가용 자연어 진단+회복 레버(결정론·LLM 0콜)
            self.bus.emit("finalize", "escalation", chapter=ch_no,
                          hard=[v.kind for v in hard_remaining], recovery=recovery_hints)

        return ChapterRecord(
            chapter=ch_no, title=beat.get("title", ""), status=status, text=chapter_text,
            summary=summary, detail_synopsis=detail_synopsis, scenes=1 + ext, n_retrieved=len(narrative), indexed_chunks=indexed,
            wiki_pages_touched=pages, initial_violations=initial_caught or [], recovery_hints=recovery_hints,
            chapter_function=beat.get("chapter_function", ""), hook_type=beat.get("hook_type", ""),
            time_advance=beat.get("time_advance", ""), place=beat.get("place", ""),   # G4: 기능 차원 영속(훅 이력)
            gen_context={"draft": draft_ctx},   # 디버그: 집필 입력(계획 입력은 copilot 가 'plan' 키로 합침)
            final_violations=final.violations, rounds=rounds, usage_by_stage=stage_usage)
