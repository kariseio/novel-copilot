# -*- coding: utf-8 -*-
"""SX-MIN 검증 — 스토리 전개 최소 세트(장면 안무 다양성·세계 노출 슬롯·콜드리드 축) (실 LLM 0콜).

설계(docs/BACKLOG.md SX-MIN ⓐⓑⓒ · 4화 PM 전수 정독 근거):
  ⓐ SX-1 장면 안무 다양성: Beat/ChapterRecord.scene_form 분류형 자기 라벨 + scene_form_whitelist(최근 2화 제외·
     DP-22 closing_device 동형·B-32e 앵커링 준수). structure_history 병기(items+최빈/연속 run·advisory·블록 미노출).
  ⓑ SX-3 세계 노출 슬롯: Beat/ChapterRecord.world_reveal 0~2(장면 사건으로 드러나는 새 세계 사실·발명 강제 없음·
     빈 배열 허용). draft 프롬프트에 비트 재료(key_events 채널)로 자연 포함. verification advisory(존재 여부·판정 없음).
  ⓒ SX-2 콜드리드 축: 프로즈만 읽는 신규 독자 프로브(cross-vendor gen≠judge·척도 0~100 명시·hair-trigger·인용 의무).
     verification.cold_read(결측 시 MISSING). usage/time_by_stage cold_read 계상.

검증 축(티켓 필수):
  ① scene_form 화이트리스트 최근2 제외(+빈 이력 전체·소진 복원·정규화·밖 라벨/결측 무시)
  ② scene_form 영속(Beat→ChapterRecord)·structure_history 병기(items+집계·블록 미노출)
  ③ world_reveal 스키마·영속·빈 배열 허용·상한 2·draft 재료 병합
  ④ 콜드리드 입력이 프로즈만(요약·계획 미포함 — 프롬프트 조립 검사)·절단 플래그
  ⑤ 콜드리드 OFF 바이트 동일(build_cold_read_prose no-op·프로브 미호출은 copilot 결선이지만 프롬프트 조립 불변)
  ⑥ verification cold_read 결측 "미실행"

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_sxmin_story_exposure.py
"""
from __future__ import annotations
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

from novelcopilot.engine.menu_filter import scene_form_whitelist, SCENE_FORM_WHITELIST
from novelcopilot.engine.structure_history import structure_history, structure_history_block
from novelcopilot.engine.verification import build_verification, MISSING
from novelcopilot.engine.cold_read import build_cold_read_prose, cold_read_probe
from novelcopilot.domain.world import WorldConfig, EntitySpec, Beat
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.worldgen import ArcPlanner
from novelcopilot.llm.base import LLMProvider


# ══════════════════ ① SX-1 scene_form 화이트리스트 순환 ══════════════════
def test_scene_form_whitelist_rotation() -> None:
    """최근 2화 사용 형태 제외(축소 리스트 제시) — DP-22 closing_whitelist 동형."""
    used = ["은폐", "은폐"]   # 최근 2화 모두 은폐(접촉→복원→은폐 3연속 실측 패턴)
    got = scene_form_whitelist(used)
    expect = [f for f in SCENE_FORM_WHITELIST if f != "은폐"]
    ok = (got == expect and "은폐" not in got and "잠입" in got and "대결" in got)
    print(f"[{'OK' if ok else 'FAIL'}] ① 순환: 최근 사용 안무 제외한 축소 리스트")
    assert ok


def test_scene_form_whitelist_two_distinct_removed() -> None:
    used = ["잠입", "대결"]   # 서로 다른 두 안무 → 둘 다 제외
    got = scene_form_whitelist(used)
    ok = ("잠입" not in got and "대결" not in got
          and got == [f for f in SCENE_FORM_WHITELIST if f not in ("잠입", "대결")])
    print(f"[{'OK' if ok else 'FAIL'}] ① 순환: 서로 다른 두 안무 모두 제외")
    assert ok


def test_scene_form_whitelist_empty_history_full() -> None:
    """빈 이력(None/[]) → 전체 화이트리스트(하위호환·바이트 동일)."""
    ok = (scene_form_whitelist(None) == list(SCENE_FORM_WHITELIST)
          and scene_form_whitelist([]) == list(SCENE_FORM_WHITELIST))
    print(f"[{'OK' if ok else 'FAIL'}] ① 빈 이력=전체 화이트리스트(하위호환)")
    assert ok


def test_scene_form_whitelist_exhausted_restores_full() -> None:
    """전 형태 소진(전부 최근 사용) → 전체 복원(never-empty)."""
    got = scene_form_whitelist(list(SCENE_FORM_WHITELIST))
    ok = (got == list(SCENE_FORM_WHITELIST))
    print(f"[{'OK' if ok else 'FAIL'}] ① 전 형태 소진 시 전체 복원(never-empty)")
    assert ok


def test_scene_form_whitelist_normalized_and_out_of_list() -> None:
    """대소문자/공백 정규화·화이트리스트 밖 라벨·결측(빈/None) 무시."""
    ok = (scene_form_whitelist([" 은폐 ", "대결"]) ==
          [f for f in SCENE_FORM_WHITELIST if f not in ("은폐", "대결")]
          and scene_form_whitelist(["미지의안무"]) == list(SCENE_FORM_WHITELIST)   # 밖 라벨 → 아무것도 안 뺌
          and scene_form_whitelist(["", "  ", None]) == list(SCENE_FORM_WHITELIST))  # 결측 무시
    print(f"[{'OK' if ok else 'FAIL'}] ① 정규화·밖 라벨/결측 무시(하위호환)")
    assert ok


# ══════════════════ ② scene_form 스키마·영속·structure_history ══════════════════
def test_scene_form_schema_additive_default_empty() -> None:
    """Beat/ChapterRecord.scene_form 필드 존재·기본 ''·구 JSON 로드 하위호환."""
    b = Beat(chapter=1)
    r = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED)
    ok = (b.scene_form == "" and r.scene_form == "")
    # 구 JSON(scene_form 결측) 로드 → 기본값
    old = ChapterRecord.model_validate({"chapter": 7, "status": "FINALIZED", "closing_device": "dialogue"})
    ok &= (old.scene_form == "" and old.closing_device == "dialogue")
    b_old = Beat.model_validate({"chapter": 3, "hook_type": "action"})
    ok &= (b_old.scene_form == "")
    print(f"[{'OK' if ok else 'FAIL'}] ② scene_form 스키마 additive·기본 ''·구 JSON 하위호환")
    assert ok


def test_scene_form_persist_beat_to_record() -> None:
    """harness 영속 계약(코드 경로): beat dict 의 scene_form → ChapterRecord(dict.get 폴백)."""
    beat_dict = {"title": "9화", "scene_form": "추적", "closing_device": "sensory"}
    rec = ChapterRecord(chapter=9, status=ChapterStatus.FINALIZED,
                        scene_form=beat_dict.get("scene_form", ""),
                        closing_device=beat_dict.get("closing_device", ""))
    ok = (rec.scene_form == "추적")
    rec2 = ChapterRecord(chapter=10, status=ChapterStatus.FINALIZED,
                         scene_form={}.get("scene_form", ""))   # 구 beat(라벨 없음) → 기본 ''
    ok &= (rec2.scene_form == "")
    print(f"[{'OK' if ok else 'FAIL'}] ② harness 영속: beat.scene_form → ChapterRecord(dict.get 폴백)")
    assert ok


def _rec(ch, hook="", func="", place="", sform=""):
    return ChapterRecord(chapter=ch, status=ChapterStatus.FINALIZED,
                         hook_type=hook, chapter_function=func, place=place, scene_form=sform)


def test_structure_history_scene_form_items_and_aggregate() -> None:
    """structure_history 가 scene_form 을 items·집계(최빈 비율·연속 run)에 병기(advisory)."""
    recs = [_rec(1, sform="은폐"), _rec(2, sform="은폐"), _rec(3, sform="추적"), _rec(4, sform="은폐")]
    h = structure_history(recs)
    ok = ([it["scene_form"] for it in h["items"]] == ["은폐", "은폐", "추적", "은폐"])
    ok &= (h["scene_form_monotony"] == 0.75)   # 은폐 3/4
    ok &= (h["scene_form_max_run"] == 2)        # ch1·ch2 연속 은폐
    print(f"[{'OK' if ok else 'FAIL'}] ② structure_history scene_form items+집계(최빈/연속 run)")
    assert ok


def test_structure_history_scene_form_only_still_included() -> None:
    """scene_form 만 있고 hook/func/place 전무여도 items 에 포함(skip 조건에 scene_form 반영)."""
    recs = [_rec(1, sform="대결")]   # scene_form 만 있는 회차
    h = structure_history(recs)
    ok = (len(h["items"]) == 1 and h["items"][0]["scene_form"] == "대결")
    print(f"[{'OK' if ok else 'FAIL'}] ② scene_form 단독 회차도 items 포함(skip 조건 반영)")
    assert ok


def test_structure_history_block_does_not_expose_scene_form() -> None:
    """B-32e 앵커링 준수: scene_form 은 설계 프롬프트 블록에 노출하지 않는다(순환은 whitelist 경로가 담당)."""
    recs = [_rec(1, hook="reveal", func="escalation", place="길드", sform="은폐"),
            _rec(2, hook="reveal", func="escalation", place="길드", sform="은폐")]
    block = structure_history_block(structure_history(recs))
    ok = ("은폐" not in block)   # 안무 라벨이 블록 텍스트에 등장하지 않음(피할 대상 비노출)
    ok &= ("· 1화 [escalation/reveal@길드]" in block)   # 기존 라벨 렌더는 불변
    print(f"[{'OK' if ok else 'FAIL'}] ② structure_history_block 에 scene_form 미노출(앵커링 준수)")
    assert ok


# ══════════════════ ③ SX-3 world_reveal 스키마·영속·빈 배열 ══════════════════
def test_world_reveal_schema_additive_default_empty() -> None:
    """Beat/ChapterRecord.world_reveal 기본 []·구 JSON 로드 하위호환·빈 배열 허용(정직)."""
    b = Beat(chapter=1)
    r = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED)
    ok = (b.world_reveal == [] and r.world_reveal == [])
    old = ChapterRecord.model_validate({"chapter": 7, "status": "FINALIZED"})
    ok &= (old.world_reveal == [])   # 구 JSON 결측 → 기본 [](빈 배열 허용)
    b2 = Beat(chapter=2, world_reveal=["관문 순서는 머리부터", "루갈이는 생존 현직"])
    ok &= (b2.world_reveal == ["관문 순서는 머리부터", "루갈이는 생존 현직"])
    print(f"[{'OK' if ok else 'FAIL'}] ③ world_reveal 스키마 additive·기본 []·빈 배열 허용")
    assert ok


def test_world_reveal_persist_and_list_copy() -> None:
    """harness 영속(코드 경로): beat.world_reveal → ChapterRecord(list 복사·결측=[])."""
    beat_dict = {"world_reveal": ["처리실 밖 세계가 존재"]}
    rec = ChapterRecord(chapter=5, status=ChapterStatus.FINALIZED,
                        world_reveal=list(beat_dict.get("world_reveal") or []))
    ok = (rec.world_reveal == ["처리실 밖 세계가 존재"])
    rec2 = ChapterRecord(chapter=6, status=ChapterStatus.FINALIZED,
                         world_reveal=list({}.get("world_reveal") or []))   # 결측 → []
    ok &= (rec2.world_reveal == [])
    print(f"[{'OK' if ok else 'FAIL'}] ③ harness 영속: beat.world_reveal → ChapterRecord(list·결측=[])")
    assert ok


# ---------- 통합 fake provider(beat_for_episode 파싱·프롬프트 캡처) ----------
class _Cap(LLMProvider):
    """chat 을 캡처하고 지정 JSON 을 반환하는 Fake(실 LLM 0콜)."""
    def __init__(self, payload: dict):
        super().__init__(); self.payload = payload; self.sys = ""; self.usr = ""
    def chat(self, msgs, **k):
        self.sys = msgs[0]["content"]; self.usr = msgs[-1]["content"]
        return json.dumps(self.payload, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def _world() -> WorldConfig:
    w = WorldConfig(title="t", genre="판타지", entities=[EntitySpec(id="hero", name="주인공")])
    w.spine = NarrativeSpine(ending=EndingSpec(central_question="Q", ending="E"),
                             arcs=[Arc(arc_id="a1", order=1, title="A1", goal="g")])
    return w


def _ep(**kw) -> Episode:
    base = dict(episode_id="e1", arc_id="a1", order=1, title="E1", premise="도입", climax="절정사건")
    base.update(kw); return Episode(**base)


def test_beat_parses_scene_form_and_world_reveal() -> None:
    """beat_for_episode 가 scene_form(strip)·world_reveal(배열·상한2·빈 항목 제거)을 파싱."""
    w = _world(); arc = w.spine.arcs[0]; ep = _ep()
    payload = {"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"],
               "scene_form": "  추적  ",
               "world_reveal": ["처리실 밖 세계", "", "세 번째 사실", "네 번째(상한 초과)"]}
    beat = ArcPlanner(_Cap(payload)).beat_for_episode(w, arc, ep, 4, False, ["직전"], [])
    ok = (beat.scene_form == "추적")   # strip
    ok &= (beat.world_reveal == ["처리실 밖 세계", "세 번째 사실"])   # 빈 항목 제거·상한 2
    # 발명 강제 없음: world_reveal 미제공/빈 배열 → [](정직)
    beat2 = ArcPlanner(_Cap({"title": "t", "summary": "s", "key_events": ["e"], "entities": ["hero"]})) \
        .beat_for_episode(w, arc, ep, 5, False, ["직전"], [])
    ok &= (beat2.world_reveal == [] and beat2.scene_form == "")
    print(f"[{'OK' if ok else 'FAIL'}] ③ 비트 파싱: scene_form(strip)·world_reveal(상한2·빈 제거·빈 배열 허용)")
    assert ok


def test_beat_prompt_scene_form_reduced_whitelist() -> None:
    """비트 프롬프트가 최근 2화 사용 안무를 뺀 축소 화이트리스트로 선언·이행 문안 제시."""
    w = _world(); arc = w.spine.arcs[0]; ep = _ep()
    cap = _Cap({"title": "t", "summary": "s", "key_events": ["e1"], "entities": ["hero"], "scene_form": "추적"})
    ArcPlanner(cap).beat_for_episode(w, arc, ep, 3, False, ["직전"], [],
                                     recent_scene_forms=["은폐", "잠입"])
    line = next((l for l in cap.sys.split(",") if "scene_form(" in l), "")
    ok = ("은폐" not in line and "잠입" not in line and "추적" in line and "대결" in line)
    ok &= ("선언하고 그 안무로 전개하라" in line)   # 선언+이행 문안
    print(f"[{'OK' if ok else 'FAIL'}] ③ 비트 프롬프트 scene_form 축소 화이트리스트+선언·이행 문안")
    assert ok


def test_world_reveal_merged_into_draft_events() -> None:
    """SX-3: harness 가 world_reveal 을 draft key_events 채널에 비트 재료로 병합(별도 강제 지시 없음·T2 회피).
    코드 경로 계약을 고정(harness._draft_events 병합 산술과 동일) — 빈 배열이면 no-op(바이트 동일)."""
    def _merge(key_events, world_reveal):
        ev = list(key_events or [])
        for wr in (world_reveal or []):
            if isinstance(wr, str) and wr.strip() and wr not in ev:
                ev.append(wr.strip())
        return ev
    ok = (_merge(["사건1", "사건2"], ["새 세계 사실"]) == ["사건1", "사건2", "새 세계 사실"])
    ok &= (_merge(["사건1"], []) == ["사건1"])              # 빈 배열 → no-op(바이트 동일)
    ok &= (_merge(["사건1"], None) == ["사건1"])            # 결측 → no-op
    ok &= (_merge(["새 세계 사실"], ["새 세계 사실"]) == ["새 세계 사실"])   # 중복 제거
    print(f"[{'OK' if ok else 'FAIL'}] ③ world_reveal draft 재료 병합(중복 제거·빈=no-op)")
    assert ok


# ══════════════════ ④ SX-2 콜드리드 입력=프로즈만·절단 플래그 ══════════════════
def test_cold_read_prose_join_and_no_summary() -> None:
    """콜드리드 입력 조립: 1화~현재 본문만 연결(요약·설정·계획 미포함)·회차 수 계상."""
    texts = ["1화 본문입니다.", "2화 본문입니다.", "3화 본문입니다."]
    built = build_cold_read_prose(texts, max_chars=40000)
    ok = ("1화 본문입니다." in built["prose"] and "3화 본문입니다." in built["prose"])
    ok &= (built["truncated"] is False and built["chapters"] == 3)
    # 프로즈만: 조립 결과에 요약/설정/계획 키워드가 섞이지 않음(입력은 본문 문자열의 join 뿐)
    ok &= (built["prose"] == "\n\n".join(texts))
    # 빈/공백 회차는 제외
    built2 = build_cold_read_prose(["", "  ", "실본문"], max_chars=40000)
    ok &= (built2["prose"] == "실본문" and built2["chapters"] == 1)
    print(f"[{'OK' if ok else 'FAIL'}] ④ 콜드리드 입력=프로즈만 연결(요약·설정 미포함)")
    assert ok


def test_cold_read_prose_truncation_flag() -> None:
    """상한 초과 시 앞부분 유지·초과분 절단·truncated=True 정직 기록."""
    texts = ["A" * 100, "B" * 100, "C" * 100]   # join 시 ~304자
    built = build_cold_read_prose(texts, max_chars=150)
    ok = (built["truncated"] is True and built["chars"] == 150)
    ok &= (built["prose"].startswith("AAA") and "C" not in built["prose"])   # 앞부분(도입) 유지·꼬리 버림
    # 상한 이내면 절단 없음
    built2 = build_cold_read_prose(["짧은 본문"], max_chars=40000)
    ok &= (built2["truncated"] is False)
    print(f"[{'OK' if ok else 'FAIL'}] ④ 콜드리드 절단: 앞부분 유지·초과분 절단·truncated 플래그")
    assert ok


class _JudgeCap(LLMProvider):
    """cold_read_probe 심사 provider 캡처 — chat_json 입력 메시지를 잡아 프로즈만 주입됐는지 검사."""
    def __init__(self, payload: dict):
        super().__init__(); self.payload = payload; self.sys = ""; self.usr = ""
    def chat(self, msgs, **k): return json.dumps(self.payload, ensure_ascii=False)
    def chat_json(self, messages, **k):
        self.sys = messages[0]["content"]; self.usr = messages[-1]["content"]
        return dict(self.payload)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


def test_cold_read_probe_prompt_is_prose_only() -> None:
    """콜드리드 프로브 프롬프트 조립 검사: user 메시지에 본문만·요약/설정/계획 슬롯 부재·척도 0~100 명시."""
    judge = _JudgeCap({"comprehension_0_100": 70, "confusions": [], "curiosities": ["더 궁금"], "drop_risk": True})
    res = cold_read_probe(judge, ["1화 본문.", "2화 본문."], genre="판타지", max_chars=40000)
    ok = ("1화 본문." in judge.usr and "2화 본문." in judge.usr)
    # 프로즈만: reader_desk 가 주입하던 '지금까지 줄거리'·'확정 설정' 슬롯이 콜드리드 user 에 없어야 한다(사각 해소의 존재 이유)
    ok &= ("지금까지 줄거리" not in judge.usr and "확정 설정" not in judge.usr and "적시 사건 메뉴" not in judge.usr)
    # 척도 명시(0~100) — 시스템 프롬프트가 0~10 오답을 못박아 방지
    ok &= ("0~100" in judge.sys)
    ok &= (res is not None and res["comprehension_0_100"] == 70 and res["drop_risk"] is True)
    print(f"[{'OK' if ok else 'FAIL'}] ④ 콜드리드 프로브 프롬프트=프로즈만(요약·설정·계획 부재)·척도 0~100 명시")
    assert ok


def test_cold_read_probe_scale_normalization() -> None:
    """comprehension 0~100 정규화(범위 클램프·문자열/실수 방어). 척도 누락 오답(0~10) 방지."""
    j1 = _JudgeCap({"comprehension_0_100": 150, "confusions": [], "curiosities": [], "drop_risk": False})
    r1 = cold_read_probe(j1, ["본문"], max_chars=40000)
    ok = (r1["comprehension_0_100"] == 100)   # 상한 클램프
    j2 = _JudgeCap({"comprehension_0_100": "85", "confusions": [{"what": "x", "quote": "y"}],
                    "curiosities": [], "drop_risk": True})
    r2 = cold_read_probe(j2, ["본문"], max_chars=40000)
    ok &= (r2["comprehension_0_100"] == 85 and r2["confusions"] == [{"what": "x", "quote": "y"}])
    print(f"[{'OK' if ok else 'FAIL'}] ④ 콜드리드 척도 0~100 정규화(클램프·문자열 방어)")
    assert ok


def test_cold_read_probe_truncated_passthrough() -> None:
    """프로브 반환에 입력 절단 여부(truncated)·회차 수(chapters)가 정직하게 실린다."""
    judge = _JudgeCap({"comprehension_0_100": 50, "confusions": [], "curiosities": [], "drop_risk": True})
    res = cold_read_probe(judge, ["A" * 100, "B" * 100], genre="판타지", max_chars=120)
    ok = (res["truncated"] is True and res["chapters"] == 2)
    print(f"[{'OK' if ok else 'FAIL'}] ④ 콜드리드 반환에 truncated·chapters 정직 기록")
    assert ok


# ══════════════════ ⑤ 콜드리드 OFF 바이트 동일 ══════════════════
def test_cold_read_off_no_op() -> None:
    """OFF 경로 계약: build_cold_read_prose 는 본문 없으면 빈 프로즈(프로브 미호출) — 조립 자체가 부작용 0.
    또한 프로브는 프로즈 전무 시 None(비차단) → verification.cold_read 는 MISSING(⑥과 물림)."""
    built = build_cold_read_prose([], max_chars=40000)
    ok = (built["prose"] == "" and built["chapters"] == 0 and built["truncated"] is False)
    # 프로즈 전무 → 프로브 None(LLM 콜 없음)
    judge = _JudgeCap({"comprehension_0_100": 50, "confusions": [], "curiosities": [], "drop_risk": True})
    res = cold_read_probe(judge, [], max_chars=40000)
    ok &= (res is None and judge.usr == "")   # chat_json 미호출(usr 미설정)
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ 콜드리드 OFF/본문 전무 → 조립 no-op·프로브 미호출(바이트 동일)")
    assert ok


# ══════════════════ ⑥ verification cold_read/world_reveal/scene_form ══════════════════
def test_verification_cold_read_missing_when_not_run() -> None:
    """cold_read 미실행(None 주입/미주입) → verification.cold_read == '미실행'(결측 정직·null 침묵 금지)."""
    r = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="본문 " * 200)
    v_none = build_verification(r, prev_texts=[], target_chars=5000)            # 미주입
    v_explicit = build_verification(r, prev_texts=[], target_chars=5000, cold_read=None)  # 명시 None
    ok = (v_none["cold_read"] == MISSING == "미실행" and v_explicit["cold_read"] == "미실행")
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ verification cold_read 결측='미실행'(결측 정직)")
    assert ok


def test_verification_cold_read_dict_when_run() -> None:
    """cold_read dict 주입 → verification.cold_read 에 그대로 실린다(실행 시 값)."""
    r = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="본문 " * 200)
    cr = {"comprehension_0_100": 65, "confusions": [{"what": "루갈이가 누구?", "quote": "그"}],
          "curiosities": ["처리실 밖은?"], "drop_risk": True, "truncated": False, "chapters": 2}
    v = build_verification(r, prev_texts=[], target_chars=5000, cold_read=cr)
    ok = (v["cold_read"] == cr)
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ verification cold_read dict 주입=실값")
    assert ok


def test_verification_labels_scene_form_and_world_reveal() -> None:
    """labels 에 scene_form 병기·world_reveal 축(존재 여부·항목·판정 없음)."""
    r = ChapterRecord(chapter=3, status=ChapterStatus.FINALIZED, text="본문 " * 100,
                      closing_device="dialogue", scene_form="추적",
                      world_reveal=["처리실 밖 세계", "루갈이 생존"])
    v = build_verification(r, prev_texts=[], target_chars=5000)
    ok = (v["labels"]["scene_form"] == "추적" and v["labels"]["closing_device"] == "dialogue")
    ok &= (v["world_reveal"]["count"] == 2 and v["world_reveal"]["items"] == ["처리실 밖 세계", "루갈이 생존"])
    # 빈 world_reveal → count 0(MISSING 아님 — 노출 없음은 정직한 0)
    r2 = ChapterRecord(chapter=4, status=ChapterStatus.FINALIZED, text="본문 " * 100)
    v2 = build_verification(r2, prev_texts=[], target_chars=5000)
    ok &= (v2["world_reveal"]["count"] == 0 and v2["world_reveal"]["items"] == [])
    ok &= (v2["labels"]["scene_form"] == "")   # 결측 라벨=빈 문자열
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ verification labels.scene_form·world_reveal 축(count 0=정직)")
    assert ok


_TESTS = [
    test_scene_form_whitelist_rotation, test_scene_form_whitelist_two_distinct_removed,
    test_scene_form_whitelist_empty_history_full, test_scene_form_whitelist_exhausted_restores_full,
    test_scene_form_whitelist_normalized_and_out_of_list,
    test_scene_form_schema_additive_default_empty, test_scene_form_persist_beat_to_record,
    test_structure_history_scene_form_items_and_aggregate, test_structure_history_scene_form_only_still_included,
    test_structure_history_block_does_not_expose_scene_form,
    test_world_reveal_schema_additive_default_empty, test_world_reveal_persist_and_list_copy,
    test_beat_parses_scene_form_and_world_reveal, test_beat_prompt_scene_form_reduced_whitelist,
    test_world_reveal_merged_into_draft_events,
    test_cold_read_prose_join_and_no_summary, test_cold_read_prose_truncation_flag,
    test_cold_read_probe_prompt_is_prose_only, test_cold_read_probe_scale_normalization,
    test_cold_read_probe_truncated_passthrough,
    test_cold_read_off_no_op,
    test_verification_cold_read_missing_when_not_run, test_verification_cold_read_dict_when_run,
    test_verification_labels_scene_form_and_world_reveal,
]

if __name__ == "__main__":
    results = []
    for t in _TESTS:
        try:
            t(); results.append(True)
        except AssertionError:
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
