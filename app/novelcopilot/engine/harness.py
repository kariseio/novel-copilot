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

from ..config import Settings
from ..domain.types import (ContextBoard, SceneSpec, ChapterRecord, ChapterStatus,
                            RoundTrace, AuthorDirective, SignalGrade)
from ..domain.world import StyleSpec
from ..llm.base import LLMProvider
from .checker import Checker
from .prompts import PromptAssembler, render_style
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
        self.style_block = render_style(style)

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

    _HOOKS = {   # 회차 끝맺음 정책(StyleSpec.ending_hook 데이터 주도 — 장르/작가 제어, 상시 강제 제거)
        "cliffhanger": ("\n\n[이 장면은 회차의 마지막] 끝을 '절단신공'으로 — 깔끔히 닫지 말고 다음 화가 궁금해지는 "
                        "위기·반전·미끼로 끊어라(마지막 한두 줄이 훅)."),
        "soft": ("\n\n[이 장면은 회차의 마지막] 다음 화가 궁금해질 여지는 남기되, 과도한 위기 조성 없이 "
                 "이 회차의 감정·상황을 자연스러운 여운으로 마무리하라."),
        "none": "",
    }
    _CLOSING = ("\n\n[이 장면은 '작품 전체의 마지막' 회차다] 절단신공 금지 — 중심 갈등과 감정선을 매듭짓고 "
                "여운 있게 작품을 닫아라.")

    def _draft(self, board, scene, prev_scenes, last=False, closing=False) -> str:
        hook = ""
        if last:
            hook = self._CLOSING if closing else self._HOOKS.get(self.style.ending_hook, self._HOOKS["cliffhanger"])
        return self.provider.chat(
            [{"role": "system", "content": f"{self.style.system_persona} 확정 설정 절대 위반 금지.\n{self.style_block}"},
             {"role": "user", "content": self.assembler.assemble(board, scene, prev_scenes) + hook
              + "\n\n이 장면 본문만 출력(머리말·설명·메타 금지)."}],
            temperature=0.85, max_tokens=self.settings.gen_max_tokens)

    def _rewrite(self, scene_text, violations, board) -> str:
        vlist = "\n".join(f"- [{v.kind}/{v.grade.value}] {v.entity}: 캐논={v.canon} 위반={v.text}" for v in violations)
        gt = "\n".join(f"- {f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth)
        return self.provider.chat(
            [{"role": "system", "content":
              "교정 작가. 지적된 설정 위반만 고치고 문체·줄바꿈·분량은 보존. 확정 설정·세계규칙이 비트보다 우선. "
              "'제거 상태'(사망·소멸 등) 인물의 현재 행동은 회상/환영/삭제로, 속성·관계는 캐논값으로 교정. 본문만.\n" + self.style_block},
             {"role": "user", "content": f"[확정 설정]\n{gt}\n[설정 위반]\n{vlist}\n[원본 본문]\n{scene_text}"}],
            temperature=0.4, max_tokens=self.settings.gen_max_tokens)

    # ---- 회차 생성 ----
    def _summarize(self, chapter_text: str, prior_summary: str = "") -> str:
        """회차 줄거리 요약(누적 story_so_far 재료). 핵심 사건·결정/변화·미결 긴장만."""
        try:
            out = self.provider.chat(
                [{"role": "system", "content":
                  "웹소설 회차를 3~5문장으로 요약하라. 핵심 사건·인물의 결정/변화·새로 생긴 상황·아직 풀리지 않은 긴장(미결)만 담아라. "
                  "감상·군더더기·문체 묘사 금지. 다음 회차 집필자가 '지금까지 줄거리'로 읽을 사실 위주."},
                 {"role": "user", "content":
                  (f"[직전까지 줄거리]\n{prior_summary[-1500:]}\n\n" if prior_summary else "")
                  + f"[이번 회차 본문]\n{chapter_text}\n\n이번 회차 요약:"}],
                temperature=0.2, max_tokens=400).strip()
            if out:
                return out
            self.bus.emit("summarize", "empty_response")
        except Exception:
            self.bus.emit("summarize", "parse_failure")
        return chapter_text[:200]   # 빈/실패 시 본문 앞부분 fallback(요약 누락→story_so_far 구멍·영구망각 방지)

    def generate(self, ch_no, beat, ontology, rag, wiki, directives=None,
                 prev_chapter_text: str = "", story_so_far: str = "", anchors=None,
                 closing: bool = False) -> ChapterRecord:
        directives = directives or []
        involved = beat.get("entities") or list(ontology.entities.keys())
        self.bus.emit("plan_chapter", "start", chapter=ch_no, title=beat.get("title", ""))
        # 단계별 토큰 계측(단위경제 — 일관성 오버헤드율 재료)
        stage_usage: dict = {}

        def _track(stage, before_tokens):
            stage_usage[stage] = stage_usage.get(stage, 0) + (self.provider.usage.chat_tokens - before_tokens)
        # 보이스 카드: 등장 인물의 말투 시그니처(스타일 지침 — 캐논 아님)
        voice_cards = "\n".join(f"- {e.name}: {e.voice}"
                                for e in (ontology.entities.get(i) for i in involved)
                                if e is not None and getattr(e, "voice", ""))

        narrative = list(anchors or [])   # 엔딩/아크 앵커(narrative, ground_truth 아님) 상단
        if ch_no > 1:
            narrative += rag.search(beat.get("summary", ""), ch_no - 1, k=3)
            narrative += wiki.retrieve(beat.get("summary", ""), ch_no - 1, k=2)
        ground_truth = (ontology.canon_facts(involved, ch_no)
                        + ontology.canon_relations(involved, ch_no))   # 확정 관계도 '박기'(ground_truth)
        board = ContextBoard(chapter=ch_no, ground_truth=ground_truth,
                             narrative=narrative, authority=directives,
                             prev_chapter=prev_chapter_text, story_so_far=story_so_far,
                             voice_cards=voice_cards)
        self.bus.emit("assemble_memory", "done", chapter=ch_no,
                      ground_truth=len(board.ground_truth), retrieved=len(narrative))

        _t = self.provider.usage.chat_tokens
        scenes = self.plan_scenes({**beat, "chapter": ch_no}, directives)
        _track("plan", _t)
        self.bus.emit("plan_scenes", "done", chapter=ch_no, scenes=len(scenes))

        rounds: list[RoundTrace] = []
        initial_caught = None
        scene_texts: list[str] = []
        for sc in scenes:
            prev = "\n".join(scene_texts)
            self.bus.emit("draft_scene", "start", chapter=ch_no, scene=sc.index, goal=sc.goal[:60])
            _t = self.provider.usage.chat_tokens
            text = self._draft(board, sc, prev, last=(sc is scenes[-1]), closing=closing)
            _track("draft", _t)
            best_text, best_hard = text, None     # M5: hard 위반 최소 텍스트 보존
            for r in range(self.settings.max_rewrite_rounds + 1):
                _t = self.provider.usage.chat_tokens
                res = self.checker.check_text(text, ontology, ch_no, involved)
                _track("check", _t)
                hard = res.hard
                # 본문 재작성으로 고칠 수 있는 위반(QUASI=추출+코드비교)과 SSOT 자기모순(DETERMINISTIC=그래프/시점 내부모순) 분리.
                # SSOT 모순은 어떤 본문을 써도 안 사라지므로 재작성 루프를 돌리지 않는다(무의미한 LLM 소모·결정론적 영구 ESCALATED 방지) — M4.
                text_hard = [v for v in hard if v.grade == SignalGrade.QUASI]
                ssot_hard = [v for v in hard if v.grade == SignalGrade.DETERMINISTIC]
                rounds.append(RoundTrace(round=r, scene=sc.index, n_violations=len(res.violations),
                                         n_hard=len(hard), kinds=[v.kind for v in res.violations]))
                self.bus.emit("consistency_check", "done", chapter=ch_no, scene=sc.index, round=r,
                              violations=len(res.violations), hard=len(hard),
                              kinds=[v.kind for v in res.violations])
                if initial_caught is None:
                    initial_caught = list(res.violations)
                if best_hard is None or len(hard) < best_hard:   # M5: 최선본 갱신(마지막 재작성이 악화시켜도 회귀 방지)
                    best_text, best_hard = text, len(hard)
                if not text_hard or r == self.settings.max_rewrite_rounds:
                    if ssot_hard:   # 본문으로 못 고치는 SSOT 자기모순 — 작가에게 가시화(프롬프트 재작성 무의미)
                        self.bus.emit("scene_loop", "ssot_contradiction", chapter=ch_no, scene=sc.index,
                                      kinds=[v.kind for v in ssot_hard])
                    if text_hard and r == self.settings.max_rewrite_rounds:
                        self.bus.emit("scene_loop", "non_convergence", chapter=ch_no, scene=sc.index,
                                      hard=[v.kind for v in text_hard])
                    break
                self.bus.emit("partial_rewrite", "start", chapter=ch_no, scene=sc.index, round=r,
                              fixing=[v.kind for v in text_hard])
                _t = self.provider.usage.chat_tokens
                text = self._rewrite(text, text_hard, board)
                _track("rewrite", _t)
            scene_texts.append(best_text)     # M5: 최선본 채택(악화 텍스트 확정 금지)

        chapter_text = "\n\n".join(scene_texts)
        _t = self.provider.usage.chat_tokens
        final = self.checker.check_text(chapter_text, ontology, ch_no, involved)
        _track("check", _t)
        hard_remaining = final.hard
        status = ChapterStatus.FINALIZED if not hard_remaining else ChapterStatus.ESCALATED

        indexed = pages = 0
        summary = ""
        if status == ChapterStatus.FINALIZED:
            indexed = rag.index_chapter(ch_no, chapter_text)
            _t = self.provider.usage.chat_tokens
            pages = wiki.ingest_chapter(ch_no, chapter_text, ontology, reviewed=True)
            _track("wiki", _t)
            _t = self.provider.usage.chat_tokens
            summary = self._summarize(chapter_text, story_so_far)     # 누적 요약 갱신
            _track("summarize", _t)
            self.bus.emit("finalize", "done", chapter=ch_no, indexed=indexed, wiki_pages=pages,
                          summarized=bool(summary))
        else:
            self.bus.emit("finalize", "escalation", chapter=ch_no, hard=[v.kind for v in hard_remaining])

        return ChapterRecord(
            chapter=ch_no, title=beat.get("title", ""), status=status, text=chapter_text,
            summary=summary, scenes=len(scenes), n_retrieved=len(narrative), indexed_chunks=indexed,
            wiki_pages_touched=pages, initial_violations=initial_caught or [],
            final_violations=final.violations, rounds=rounds, usage_by_stage=stage_usage)
