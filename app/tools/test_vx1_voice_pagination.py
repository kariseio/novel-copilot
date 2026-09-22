# -*- coding: utf-8 -*-
"""VX-1 검증 — ① 보이스(목소리) 설정집 노출·편집 ② 조회 API 분할(페이지네이션). 실 LLM 0콜.

배경:
  ① 생성이 실제로 읽는 목소리 값 셋(style.narrator_voice · EntitySpec.voice · EntitySpec.voice_stages)이
     어느 화면에도 안 나와 작가가 자기 작품의 목소리를 볼 수도 고칠 수도 없었다.
  ② 프로젝트 GET 이 전 회차 레코드를 통째로 실어 보냈다(실측: 13화 작품 chapters 만 1.2MB / 응답 2.9MB).
     목록·현황 화면은 그 중 메타만 쓴다.

검증 축:
  ① 통합 조회 — GET /voices 가 화자 보이스·서술자 카드·단계 카드·인물 보이스·단계 축 후보를 한 번에.
  ② 서술자 판정 동형 — narrator_id 가 harness/narrator_voice.find_protagonist_id 규칙(삽입순 첫 actor)과 일치,
     3인칭이면 빈 값(집필에 안 들어가는 값을 쓰이는 것처럼 표기하지 않음).
  ③ 편집 영속·미러 — PATCH voice 가 EntitySpec 에 저장되고 세션 재구성 시 ontology 로 흘러 하네스가 읽는다.
  ④ 부분 수정·해제·상한 — voice_stages 만 보내면 voice 불변, 빈 문자열=해제, 길이 cap, 미존재 인물=400.
  ⑤ 단계 축 결속 — PUT /style narrator_voice_stage_attr 저장, 추적 속성에 없는 키는 400(조용한 공회전 방지).
  ⑥ chapters 투영 — full=기존 응답 그대로 / summary=무거운 축 제외 + chars·has_text·lite / none=빈 배열.
  ⑦ 회차 페이지 — offset·limit window·total·has_more, view 별 필드, 단건 view=text.
  ⑧ 설정집·노트 하위호환 — limit 미지정이면 전량(기존 응답), 지정 시 window + total·has_more.

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_vx1_voice_pagination.py
     (또는 pytest tools/test_vx1_voice_pagination.py)
"""
from __future__ import annotations
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.domain.world import WorldConfig, StyleSpec, EntitySpec, AttributeSpec, Beat
from novelcopilot.domain.types import ChapterRecord, publish_state

_CARD = "① 세상을 보는 각도 — 값을 매기기 전에 사람을 본다.\n② 속생각의 결 — 계산이 먼저 새어 나온다."
_STAGES = {"외면": "아직 모른다고 밀어 둔다.", "흠칫": "아는 척을 그만두기 직전이다."}


# ═════════════════ 픽스처 ═════════════════

def _build_app(tmp: pathlib.Path):
    import novelcopilot.config as cfg
    from novelcopilot.main import create_app
    os.environ["NOVEL_DATA_DIR"] = str(tmp)
    cfg._settings = None                     # 싱글톤 재생성(임시 data_dir 반영)
    app = create_app()
    return app, app.state.service


def _world(pov: str = "first") -> WorldConfig:
    return WorldConfig(
        title="[실험] 목소리", genre="현대 판타지", tone="건조",
        premise="감정사가 진짜를 골라낸다.",
        style=StyleSpec(pov=pov, narrator_voice="이 화자는 값보다 사람을 먼저 본다."),
        attributes=[AttributeSpec(key="truth_awareness", label="진실 자각", kind="state",
                                  states=["외면", "흠칫", "직시"], mutable=True),
                    AttributeSpec(key="rank", label="등급", kind="categorical", vocab=["하", "중", "상"])],
        entities=[EntitySpec(id="place1", name="시장", etype="place"),
                  EntitySpec(id="hero", name="한준호", etype="character", profile="감정사.", voice=_CARD),
                  EntitySpec(id="rival", name="정지연", etype="character", profile="경매사.", voice="빠르고 낮게.")],
        beats=[Beat(chapter=1, summary="첫 감정")])


def _chapter(n: int) -> ChapterRecord:
    """무거운 축이 채워진 회차 — 요약 투영이 실제로 무엇을 덜어내는지 재는 표본."""
    return ChapterRecord(
        chapter=n, title=f"{n}화", status="FINALIZED", gen_no=1,
        text="본문" * 100, summary="요약", detail_synopsis="상세" * 50,
        gen_context={"draft": {"persona": "p" * 200}}, dialogue_ledger=[{"line": "대사"}] * 20,
        verification={"checks": ["v"] * 20}, ontology_changes=[], humanize=[],
        reader_feedback={"why": "재밌다"}, ai_tell={"score": 0.5}, wiki_pages_touched=2)


def _seed_project(svc, pid: str = "vx", pov: str = "first", n_chapters: int = 3) -> ProjectState:
    st = ProjectState(id=pid, seed=ProjectSeed(title="[실험] 목소리", target_chapters=12),
                      world=_world(pov), created_at="2026-08-11T00:00:00")
    st.chapters = [_chapter(i) for i in range(1, n_chapters + 1)]
    st.current_chapter = n_chapters
    for e in st.world.entities:
        if e.id == "hero":
            e.voice_stages = dict(_STAGES)
    svc.repo.save(st)
    return st


class _Env:
    """임시 data_dir + 앱 1회 구성(테스트 간 설정 싱글톤 격리)."""
    def __init__(self, tmp): self.tmp = tmp
    def __enter__(self):
        self._old = os.environ.get("NOVEL_DATA_DIR")
        self.app, self.svc = _build_app(self.tmp)
        from fastapi.testclient import TestClient
        self.client = TestClient(self.app)
        return self
    def __exit__(self, *a):
        import novelcopilot.config as cfg
        cfg._settings = None
        if self._old is None:
            os.environ.pop("NOVEL_DATA_DIR", None)
        else:
            os.environ["NOVEL_DATA_DIR"] = self._old


# ═════════════════ ① 통합 조회 ═════════════════

def test_voice_snapshot(tmp_path):
    with _Env(tmp_path) as env:
        _seed_project(env.svc)
        v = env.client.get("/api/projects/vx/voices").json()
        ents = {e["id"]: e for e in v["entities"]}
        ok = v["pov"] == "first"
        ok &= v["narrator_voice"].startswith("이 화자는")            # 작품 문체의 화자 보이스
        ok &= ents["hero"]["voice"] == _CARD                        # 주인공 음성 카드
        ok &= ents["hero"]["voice_stages"] == _STAGES               # 상태 단계 카드
        ok &= ents["rival"]["voice"] == "빠르고 낮게."               # 그 밖 인물 보이스
        ok &= ents["place1"]["is_actor"] is False                   # 장소는 행동주체 아님
        ok &= [o["key"] for o in v["stage_attr_options"]] == ["truth_awareness", "rank"]
        ok &= env.client.get("/api/projects/nope/voices").status_code == 404
        print(f"[{'OK' if ok else 'FAIL'}] ① 목소리 통합 조회(화자·카드·단계·인물·단계축 후보)")
        assert ok


def test_narrator_id_matches_engine_rule(tmp_path):
    """② 서술자 판정이 하네스 규칙(삽입순 첫 actor)과 동형 — 장소는 건너뛰고, 3인칭이면 빈 값."""
    from novelcopilot.worldgen.narrator_voice import find_protagonist_id
    from novelcopilot.engine.factory import build_engine
    from novelcopilot.config import get_settings
    from novelcopilot.llm.base import LLMProvider

    class _P(LLMProvider):
        def chat(self, msgs, **k): return "{}"
        def embed(self, texts): return [[0.0] * 4 for _ in texts]

    with _Env(tmp_path) as env:
        _seed_project(env.svc, pid="vx")
        _seed_project(env.svc, pid="vx3", pov="third_limited")
        first = env.client.get("/api/projects/vx/voices").json()
        third = env.client.get("/api/projects/vx3/voices").json()
        bundle = build_engine(_world(), _P(), get_settings())
        ok = first["narrator_id"] == find_protagonist_id(bundle.ontology) == "hero"
        ok &= third["narrator_id"] == ""                            # 3인칭 — 서술자 카드 미발화
        ok &= any(e["is_narrator"] for e in first["entities"])
        ok &= not any(e["is_narrator"] for e in third["entities"])
        print(f"[{'OK' if ok else 'FAIL'}] ② 서술자 판정 동형(첫 actor·3인칭 빈 값)")
        assert ok


# ═════════════════ ③④ 편집 ═════════════════

def test_edit_persists_and_reaches_engine(tmp_path):
    """③ 편집한 카드가 저장되고, 세션 재구성 시 하네스가 읽는 ontology 로 흘러간다."""
    with _Env(tmp_path) as env:
        _seed_project(env.svc)
        r = env.client.patch("/api/projects/vx/entities/hero/voice", json={"voice": "새 목소리다."})
        ok = r.status_code == 200 and r.json()["voice"] == "새 목소리다."
        st = env.svc.repo.get("vx")
        ok &= next(e for e in st.world.entities if e.id == "hero").voice == "새 목소리다."   # 영속
        sess = env.svc.sessions.get_or_create(st)                    # evict 후 재구성 → factory 가 EntitySpec 에서 채움
        ok &= sess.bundle.ontology.entities["hero"].voice == "새 목소리다."
        ok &= env.client.get("/api/projects/vx/voices").json()["entities"][1]["voice"] == "새 목소리다."
        print(f"[{'OK' if ok else 'FAIL'}] ③ 편집 영속 + 엔진 미러(하네스가 읽는 값)")
        assert ok


def test_partial_release_cap_and_unknown(tmp_path):
    """④ 부분 수정(voice 불변)·빈 문자열 해제·길이 cap·빈 단계 카드 제거·미존재 인물 400."""
    from novelcopilot.services.copilot import CopilotService
    with _Env(tmp_path) as env:
        _seed_project(env.svc)
        env.client.patch("/api/projects/vx/entities/hero/voice",
                         json={"voice_stages": {"외면": "밀어 둔다.", "직시": "  "}})   # 공백 카드=그 단계 제거
        e = next(x for x in env.svc.repo.get("vx").world.entities if x.id == "hero")
        ok = e.voice == _CARD                                        # voice 는 안 보냈으므로 불변
        ok &= e.voice_stages == {"외면": "밀어 둔다."}
        env.client.patch("/api/projects/vx/entities/hero/voice", json={"voice": ""})     # 해제
        ok &= next(x for x in env.svc.repo.get("vx").world.entities if x.id == "hero").voice == ""
        env.client.patch("/api/projects/vx/entities/rival/voice", json={"voice": "가" * 5000})
        ok &= len(next(x for x in env.svc.repo.get("vx").world.entities if x.id == "rival").voice) == CopilotService.VOICE_CAP
        r404 = env.client.patch("/api/projects/nope/entities/hero/voice", json={"voice": "x"})
        r400 = env.client.patch("/api/projects/vx/entities/ghost/voice", json={"voice": "x"})
        ok &= (r404.status_code == 404 and r400.status_code == 400)
        print(f"[{'OK' if ok else 'FAIL'}] ④ 부분 수정·해제·길이 cap·미존재 인물 거절")
        assert ok


def test_stage_attr_binding(tmp_path):
    """⑤ 단계 축 결속 — 저장/해제되고, 추적 속성에 없는 키는 400(조용한 무동작 방지)."""
    with _Env(tmp_path) as env:
        _seed_project(env.svc)
        ok = env.client.put("/api/projects/vx/style",
                            json={"narrator_voice_stage_attr": "truth_awareness"}).status_code == 200
        ok &= env.svc.repo.get("vx").world.style.narrator_voice_stage_attr == "truth_awareness"
        v = env.client.get("/api/projects/vx/voices").json()
        ok &= v["stage_values"] == ["외면", "흠칫", "직시"]           # 단계 카드 칸의 키 목록
        ok &= env.client.put("/api/projects/vx/style", json={"narrator_voice_stage_attr": "없는축"}).status_code == 400
        ok &= env.client.put("/api/projects/vx/style", json={"narrator_voice_stage_attr": ""}).status_code == 200
        ok &= env.svc.repo.get("vx").world.style.narrator_voice_stage_attr == ""
        print(f"[{'OK' if ok else 'FAIL'}] ⑤ 단계 축 결속(저장·해제·미등록 키 400)")
        assert ok


# ═════════════════ ⑥⑦⑧ 조회 분할 ═════════════════

_HEAVY = ("text", "revisions", "gen_context", "dialogue_ledger", "verification", "detail_synopsis")


def test_project_chapters_projection(tmp_path):
    """⑥ chapters 투영 — 기본 full 은 model_dump + 서버 파생 키(publish_state), summary 는 무거운 축 제외, none 은 빈 배열.

    EP-PUB: chapter_view 는 발행 상태(publish_state)를 서버 단일 소스로 모든 뷰에 주입한다 — full 뷰도
    저장 필드(model_dump) 위에 이 파생 키 1개를 얹는다. '무회귀' 계약은 '저장 필드 무손실 + 파생 키만 가산'을
    뜻하지 model_dump 바이트 동일이 아니다(파생 관측 추가가 잠금을 건드린 계보 — 침묵 통과 금지, 계약을 명시 개정)."""
    with _Env(tmp_path) as env:
        st = _seed_project(env.svc)
        full = env.client.get("/api/projects/vx").json()
        summ = env.client.get("/api/projects/vx?chapters=summary").json()
        none = env.client.get("/api/projects/vx?chapters=none").json()
        expect = [{**c.model_dump(), "publish_state": publish_state(c)} for c in st.chapters]
        ok = full["chapters"] == expect                              # 저장 필드 무손실 + 파생 키 publish_state 만 가산
        # 파생 키는 정확히 publish_state 하나여야(다른 저장 필드 누락·유출 없음 — 무손실 잠금)
        ok &= all(set(fc) - set(c.model_dump()) == {"publish_state"}
                  for fc, c in zip(full["chapters"], st.chapters))
        ok &= all(k not in summ["chapters"][0] for k in _HEAVY)
        ok &= summ["chapters"][0]["chars"] == len(st.chapters[0].text)
        ok &= summ["chapters"][0]["has_text"] is True and summ["chapters"][0]["lite"] is True
        ok &= summ["chapters"][0]["ai_tell"] == {"score": 0.5}        # 현황 화면이 쓰는 축은 남긴다
        ok &= summ["chapters"][0]["reader_feedback"]["why"] == "재밌다"
        ok &= none["chapters"] == [] and none["chapters_total"] == 3
        ok &= full["chapters_total"] == summ["chapters_total"] == 3
        ok &= env.client.get("/api/projects/vx?chapters=bogus").status_code == 400
        # 실제로 가벼워졌는가 — 요약본이 전체의 절반 미만(무거운 축이 실제로 빠졌다는 증거)
        import json
        ok &= len(json.dumps(summ["chapters"], ensure_ascii=False)) * 2 < len(json.dumps(full["chapters"], ensure_ascii=False))
        print(f"[{'OK' if ok else 'FAIL'}] ⑥ chapters 투영(full 무회귀·summary 경량·none)")
        assert ok


def test_chapters_page_and_single_view(tmp_path):
    """⑦ 회차 페이지 window·total·has_more, view 별 필드, 단건 view=text."""
    with _Env(tmp_path) as env:
        _seed_project(env.svc, n_chapters=5)
        p1 = env.client.get("/api/projects/vx/chapters?offset=0&limit=2").json()
        p3 = env.client.get("/api/projects/vx/chapters?offset=4&limit=2").json()
        ok = [c["chapter"] for c in p1["items"]] == [1, 2] and p1["total"] == 5 and p1["has_more"] is True
        ok &= [c["chapter"] for c in p3["items"]] == [5] and p3["has_more"] is False
        ok &= "text" not in p1["items"][0]                            # 기본 summary
        txt = env.client.get("/api/projects/vx/chapters?offset=0&limit=1&view=text").json()["items"][0]
        ok &= txt["text"].startswith("본문") and "revisions" not in txt and "gen_context" not in txt
        one = env.client.get("/api/projects/vx/chapters/2?view=text").json()
        ok &= one["chapter"] == 2 and one["text"].startswith("본문") and "gen_context" not in one
        onef = env.client.get("/api/projects/vx/chapters/2").json()   # 기본 full=기존 응답
        ok &= "gen_context" in onef and "revisions" in onef
        ok &= env.client.get("/api/projects/vx/chapters/9").status_code == 404
        ok &= env.client.get("/api/projects/vx/chapters?view=bogus").status_code == 400
        ok &= env.client.get("/api/projects/nope/chapters").status_code == 404
        print(f"[{'OK' if ok else 'FAIL'}] ⑦ 회차 페이지 window·view·단건 view")
        assert ok


def test_bible_wiki_pagination_backcompat(tmp_path):
    """⑧ 설정집·작품 노트 — limit 미지정=전량(기존 응답), 지정 시 window + total·has_more."""
    from novelcopilot.domain.bible import BibleEntry
    with _Env(tmp_path) as env:
        st = _seed_project(env.svc)
        st.bible.entries = [BibleEntry(entry_id=f"b{i}", category="glossary", title=f"항목{i}",
                                       prose="설명", provenance="ai_worldgen") for i in range(5)]
        env.svc.repo.save(st)
        allb = env.client.get("/api/projects/vx/bible").json()
        pg = env.client.get("/api/projects/vx/bible?offset=2&limit=2").json()
        ok = len(allb["entries"]) == 5 and allb["total"] == 5 and allb["has_more"] is False
        ok &= [e["entry_id"] for e in pg["entries"]] == ["b2", "b3"] and pg["has_more"] is True
        w = env.client.get("/api/projects/vx/wiki").json()
        wp = env.client.get("/api/projects/vx/wiki?offset=0&limit=1").json()
        ok &= ("pages" in w and "lint" in w and w["total"] == len(w["pages"]))
        ok &= len(wp["pages"]) <= 1 and "lint" in wp        # 자동 점검은 잘라도 전량 유지
        print(f"[{'OK' if ok else 'FAIL'}] ⑧ 설정집·노트 페이지네이션(하위호환)")
        assert ok


def main() -> int:
    import tempfile
    fns = [test_voice_snapshot, test_narrator_id_matches_engine_rule,
           test_edit_persists_and_reaches_engine, test_partial_release_cap_and_unknown,
           test_stage_attr_binding, test_project_chapters_projection,
           test_chapters_page_and_single_view, test_bible_wiki_pagination_backcompat]
    for fn in fns:
        with tempfile.TemporaryDirectory() as td:
            fn(pathlib.Path(td))
    print("\nVX-1(보이스 노출·편집 + 조회 분할) 검증: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
