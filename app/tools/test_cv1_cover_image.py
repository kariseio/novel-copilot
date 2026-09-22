# -*- coding: utf-8 -*-
"""CV-1 표지 이미지 생성 — 결정론 검증(합성 LLM 콜·이미지 콜 전부 모킹, 라이브 0).

설계 docs/design-cv1-cover-image.md §2~§4 계약:
  ⓐ config 기본값(image_model/cover_size/cover_quality) 하드코딩 아님·값 고정
  ⓑ 프롬프트 합성 입력 조립 — 주인공 선정(protagonist>character>첫엔티티)·genre_contract 필드 포함·no-text 보증
  ⓒ CoverMeta 영속/구 JSON 바이트 동일(additive·기본 None) + 재생성 history push
  ⓓ 이미지 thin client — b64→PNG 디코드·usage.image_calls 계측·빈 프롬프트 거부
  ⓔ 서비스 generate_cover — 성공 저장·중복/생성중 락(False)·없는 작품(None)·실패 시 무변경(예외 전파)
  ⓕ 라우트 — POST 성공/404/423/500(실패 무변경)·GET PNG/404·usage_total image_calls additive

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_cv1_cover_image.py
     (또는 pytest tools/test_cv1_cover_image.py)
"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
import base64
import tempfile
import threading
from pathlib import Path

from novelcopilot.config import get_settings, Settings
from novelcopilot.domain.project import ProjectState, ProjectSeed, CoverMeta
from novelcopilot.domain.world import WorldConfig, EntitySpec, GenreContract
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.base import LLMProvider
from novelcopilot.llm.image_client import ImageClient
from novelcopilot.worldgen.cover_prompt import (
    build_cover_context, synthesize_cover_prompt, _pick_protagonist,
    _genre_accent, _STYLE_ANCHOR, _NO_TEXT, _NO_OTHER_TEXT)


# ===== 모의 provider / 이미지 client (LLM·이미지 콜 0) =====
class _FakeWG(LLMProvider):
    """chat 을 캡처하고 지정 prompt JSON 을 반환하는 Fake wg_provider."""
    def __init__(self, prompt: str = "A lone swordsman on a cliff, cinematic dusk light."):
        super().__init__(); self._prompt = prompt; self.sys = ""; self.user = ""
    def chat(self, msgs, **k):
        self.sys = msgs[0]["content"]; self.user = msgs[-1]["content"]
        self.usage.chat_calls += 1; self.usage.chat_tokens += 42
        return json.dumps({"prompt": self._prompt}, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


_PNG = b"\x89PNG\r\n\x1a\n" + b"FAKEDATA"     # 실제 PNG 시그니처 접두(디코드 왕복 확인용)


class _FakeImagesAPI:
    """OpenAI SDK 의 client.images.generate 대체 — b64_json 응답 스텁."""
    def __init__(self, png: bytes = _PNG, fail: bool = False):
        self._png = png; self.fail = fail; self.calls = []
    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("image api down")
        class _D: pass
        d = _D(); d.b64_json = base64.b64encode(self._png).decode()
        class _R: pass
        r = _R(); r.data = [d]
        return r


class _FakeOpenAI:
    def __init__(self, images):
        self.images = images


def _fake_image_client(png: bytes = _PNG, fail: bool = False) -> ImageClient:
    api = _FakeImagesAPI(png, fail)
    return ImageClient(client=_FakeOpenAI(api))


# ===== 공용 픽스처 =====
def _world() -> WorldConfig:
    return WorldConfig(
        title="검성의 귀환", genre="무협", tone="비장",
        synopsis="산에서 내려온 검객이 문파를 재건한다.",
        entities=[
            EntitySpec(id="hero", name="한서린", etype="character",
                       profile="냉정한 검객. 사문의 원수를 쫓는다."),
            EntitySpec(id="rival", name="적운", etype="character", profile="숙적."),
        ],
        genre_contract=GenreContract(pleasure_engine="성장·복수의 쾌감",
                                     vocabulary_tone="무협 고전 어휘", premise_asset="사문 재건"))


def _svc(tmp: Path, wg: _FakeWG | None = None, img: ImageClient | None = None) -> CopilotService:
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    # 실 세션(sess.lock 만 사용) — worldgen provider 빌드 없이 lock 확보되도록 FakeSessions 주입
    svc.sessions = _FakeSessions()
    if wg is not None:
        svc._wg_provider = wg
    if img is not None:
        svc._image_client = img
    return svc


class _FakeSession:
    def __init__(self): self.lock = threading.Lock()
class _FakeSessions:
    def __init__(self): self._s = {}
    def get_or_create(self, state): return self._s.setdefault(state.id, _FakeSession())
    def evict(self, pid): self._s.pop(pid, None)


def _seed_project(svc: CopilotService, pid: str = "cv1") -> ProjectState:
    state = ProjectState(id=pid, seed=ProjectSeed(title="검성의 귀환"), world=_world())
    svc.repo.save(state)
    return state


# ---------- ⓐ config 기본값 ----------
def test_config_defaults():
    s = Settings()
    ok = (s.image_model == "gpt-image-2" and s.cover_size == "1024x1536"
          and s.cover_quality == "medium")
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ config 기본값(gpt-image-2·1024x1536·medium)")
    assert ok


# ---------- ⓑ 프롬프트 합성 입력 조립 ----------
def test_pick_protagonist_priority():
    # protagonist etype 우선
    w = WorldConfig(title="t", entities=[
        EntitySpec(id="c1", name="조연", etype="character"),
        EntitySpec(id="p1", name="주인공", etype="protagonist")])
    ok = (_pick_protagonist(w).id == "p1")
    # protagonist 없으면 첫 character
    w2 = WorldConfig(title="t", entities=[
        EntitySpec(id="c1", name="첫", etype="character"),
        EntitySpec(id="c2", name="둘", etype="character")])
    ok &= (_pick_protagonist(w2).id == "c1")
    # 엔티티 없으면 None
    ok &= (_pick_protagonist(WorldConfig(title="t")) is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 주인공 선정(protagonist>character>첫·없으면 None)")
    assert ok


def test_build_cover_context_includes_contract_and_profile():
    ctx = build_cover_context(_world())
    ok = (ctx["title"] == "검성의 귀환" and ctx["genre"] == "무협" and ctx["tone"] == "비장"
          and "검객" in ctx["synopsis"]
          and ctx["vocabulary_tone"] == "무협 고전 어휘"        # genre_contract 필드 포함
          and ctx["pleasure_engine"] == "성장·복수의 쾌감"
          and ctx["protagonist_name"] == "한서린"               # 주인공 우선
          and "사문의 원수" in ctx["protagonist_profile"])       # profile 원문
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 합성 입력 조립: 계약 필드·주인공 profile 포함")
    assert ok


def test_synthesize_prompt_forces_no_text():
    # include_title=False(무텍스트 일러스트 모드) — 종전 no-text 계약 유지 확인
    wg = _FakeWG(prompt="A brooding swordsman under a blood moon")   # no-text 누락 응답
    p = synthesize_cover_prompt(wg, _world(), include_title=False)
    ok = ("no text" in p.lower() and "swordsman" in p)              # 보증 부착
    ok &= (wg.usage.chat_calls == 1)                                # 합성 1콜
    # 시스템 프롬프트에 웹소설 표지 관습·no-text·영문 지시가 담겼는가(계약 문자열)
    ok &= ("web-novel cover" in wg.sys.lower() and "no text" in wg.sys.lower())
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 합성 프롬프트 no-text 보증(제목 제외 모드)·1콜·표지 관습 지시")
    assert ok


def test_synthesize_style_anchor_always_appended():
    # CV-3: 화풍 앵커는 LLM 출력과 무관하게 코드가 항상 부착(애니 일러스트·클로즈업·고채도 계약).
    wg = _FakeWG(prompt="A lone swordsman on a cliff")
    p = synthesize_cover_prompt(wg, _world(), include_title=False)
    ok = (_STYLE_ANCHOR in p and "anime" in p.lower())
    # 장르 액센트(무협) 포함 + no-text 는 항상 맨 꼬리
    ok &= ("martial-arts" in p and p.endswith(_NO_TEXT))
    # LLM 이 지시대로 no-text 꼬리를 단 경우 → 중복 없이 1회(떼고 재부착)
    wg2 = _FakeWG(prompt=f"A brooding swordsman. {_NO_TEXT}")
    p2 = synthesize_cover_prompt(wg2, _world(), include_title=False)
    ok &= (p2.count("no watermark") == 1 and p2.endswith(_NO_TEXT) and _STYLE_ANCHOR in p2)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ CV-3 화풍 앵커 항상 부착·장르 액센트·no-text 꼬리 1회")
    assert ok


def test_synthesize_title_block_default_on():
    # CV-4: 기본값(include_title 미지정)이면 한글 제목 타이포 블록+잔여 텍스트 금지 가드 부착.
    wg = _FakeWG(prompt="A lone swordsman on a cliff")
    p = synthesize_cover_prompt(wg, _world())
    ok = ("「검성의 귀환」" in p and "LOWER THIRD" in p)             # 제목 블록(하단 배치)
    ok &= p.endswith(_NO_OTHER_TEXT)                                 # 잔여 텍스트 금지 가드가 꼬리
    ok &= (_STYLE_ANCHOR in p and "martial-arts" in p)               # 앵커·액센트 공존
    ok &= (_NO_TEXT not in p)                                        # 무텍스트 꼬리와 상호 배타
    # 선두 대괄호 태그는 렌더 제목에서 제거([실험] 등 운영 라벨 — 결정론)
    w2 = _world(); w2.title = "[실험 DP-9] 검성의 귀환"
    p2 = synthesize_cover_prompt(_FakeWG(prompt="x"), w2)
    ok &= ("「검성의 귀환」" in p2 and "[실험" not in p2)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ CV-4 제목 블록 기본 부착·하단 배치·태그 제거·가드 꼬리")
    assert ok


def test_genre_accent_specificity_and_fallback():
    # 구체 장르가 '판타지' 폴백보다 우선(substring 우선순위) — 교착어 substring 교훈의 표지판 버전.
    ok = ("comedic" in _genre_accent("코믹 판타지"))          # 코믹 > 판타지
    ok &= ("romance" in _genre_accent("로맨스판타지"))         # 로판 > 판타지
    ok &= ("urban-horror" in _genre_accent("호러 미스터리"))   # 호러 > 미스터리(선순위)
    ok &= ("martial-arts" in _genre_accent("무협"))
    ok &= ("high-fantasy" in _genre_accent("판타지"))
    ok &= (_genre_accent("SF 스릴러") == "")                  # 무일치 → 빈 문자열(발명 금지)
    ok &= ("mystery" in _genre_accent("", "추리 스릴러"))      # tone 폴백 매칭
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 장르 액센트: 구체 우선·폴백·무일치 빈값")
    assert ok


def test_synthesize_prompt_override_not_used_by_synth():
    # 오버라이드는 서비스 경로에서 합성을 스킵(합성 함수 자체는 항상 world 로 — 분리 확인)
    wg = _FakeWG(prompt="synth output")
    p = synthesize_cover_prompt(wg, _world())
    ok = (p.startswith("synth output") or "synth output" in p)
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 합성 함수는 world 기반(오버라이드 스킵은 서비스가 담당)")
    assert ok


# ---------- ⓒ CoverMeta 영속 / 구 JSON 바이트 동일 / history ----------
def test_covermeta_additive_default_none():
    st = ProjectState(id="p", seed=ProjectSeed(title="t"), world=WorldConfig(title="t"))
    ok = (st.cover is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ ProjectState.cover 기본 None(additive)")
    assert ok


def test_old_json_loads_without_cover():
    # cover 필드 없는 구 JSON 이 그대로 로드되고, 기존 필드 값 불변(무변경 로드).
    st = ProjectState(id="p", seed=ProjectSeed(title="옛작품"), world=WorldConfig(title="옛작품"),
                      current_chapter=3, usage_total={"chat_calls": 9})
    d = json.loads(st.model_dump_json()); d.pop("cover", None)      # 구 JSON = cover 키 부재
    st2 = ProjectState.model_validate(d)
    ok = (st2.cover is None and st2.current_chapter == 3
          and st2.usage_total == {"chat_calls": 9} and st2.world.title == "옛작품")
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ 구 JSON(cover 결측) 로드·기존 필드 불변")
    assert ok


def test_covermeta_history_roundtrip():
    m1 = CoverMeta(filename="p.cover.png", prompt="v1", model="gpt-image-2", size="1024x1536",
                   created_at="2026-07-13T10:00:00")
    prior = m1.model_copy(update={"history": []})
    m2 = CoverMeta(filename="p.cover.png", prompt="v2", model="gpt-image-2", size="1024x1536",
                   created_at="2026-07-13T11:00:00", history=[prior])
    # 직렬화 왕복 — history 보존·중첩 history 없음
    r = CoverMeta.model_validate(json.loads(m2.model_dump_json()))
    ok = (r.prompt == "v2" and len(r.history) == 1 and r.history[0].prompt == "v1"
          and r.history[0].history == [])
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ CoverMeta history push·왕복(중첩 없음)")
    assert ok


# ---------- ⓓ 이미지 thin client ----------
def test_image_client_b64_to_png():
    c = _fake_image_client()
    out = c.generate_png("a cat", model="gpt-image-2", size="1024x1536", quality="medium")
    ok = (out == _PNG and out[:8] == b"\x89PNG\r\n\x1a\n" and c.image_calls == 1)
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ 이미지 client b64→PNG 디코드·image_calls 계측")
    assert ok


def test_image_client_passes_config_params():
    api = _FakeImagesAPI()
    c = ImageClient(client=_FakeOpenAI(api))
    c.generate_png("x", model="M", size="S", quality="Q")
    kw = api.calls[0]
    ok = (kw["model"] == "M" and kw["size"] == "S" and kw["quality"] == "Q" and kw["n"] == 1)
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ 이미지 client config 파라미터 전달(하드코딩 아님)")
    assert ok


def test_image_client_empty_prompt_rejected():
    c = _fake_image_client()
    try:
        c.generate_png("   ", model="m", size="s", quality="q")
        ok = False
    except ValueError:
        ok = True
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ 빈 프롬프트 거부(ValueError)")
    assert ok


# ---------- ⓔ 서비스 generate_cover ----------
def test_service_generate_success_persists_meta_and_usage():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    wg = _FakeWG(); img = _fake_image_client()
    svc = _svc(tmp, wg=wg, img=img)
    _seed_project(svc, "cv1")
    meta = svc.generate_cover("cv1")
    # CV-2: 파일명은 타임스탬프({pid}.cover.<ts>.png) — legacy 고정명 아님(접두/접미만 계약)
    ok = bool(isinstance(meta, dict)
              and meta["filename"].startswith("cv1.cover.") and meta["filename"].endswith(".png")
              # CV-4 기본: 제목 타이포 블록 + 잔여 텍스트 금지 가드(구 no-text 아님)
              and "「" in meta["prompt"] and "no watermark" in meta["prompt"].lower()
              and meta["model"] == "gpt-image-2"
              and meta["size"] == "1024x1536" and meta["created_at"])
    # 파일 저장됨(PNG 바이트) — 보관함 개별 파일 경로
    ok &= (svc.repo.read_cover_file("cv1", meta["filename"]) == _PNG)
    # 상태 영속 — cover(적용본) 메타 + 보관함 1건 + usage_total image_calls + 합성 chat_calls additive
    st = svc.repo.get("cv1")
    ok &= (st.cover is not None and st.cover.filename == meta["filename"]
           and len(st.covers) == 1 and st.covers[0].filename == meta["filename"])
    ok &= (st.usage_total.get("image_calls") == 1 and st.usage_total.get("chat_calls") == 1)
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 서비스 성공: 파일·메타·usage(image_calls+chat) 영속")
    assert ok


def test_service_override_skips_synthesis():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    wg = _FakeWG(); img = _fake_image_client()
    svc = _svc(tmp, wg=wg, img=img)
    _seed_project(svc, "cv1")
    meta = svc.generate_cover("cv1", prompt_override="  custom art, no text  ")
    ok = (meta["prompt"] == "custom art, no text" and wg.usage.chat_calls == 0)   # 합성 스킵
    st = svc.repo.get("cv1")
    ok &= (st.usage_total.get("chat_calls", 0) == 0 and st.usage_total.get("image_calls") == 1)
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 오버라이드 프롬프트 → 합성 스킵(chat 0콜)·이미지 1콜")
    assert ok


def test_service_regen_pushes_history_and_overwrites():
    # CV-2 재작성: 재생성은 덮어쓰기·history push 가 아니라 보관함(covers) append + 적용본 최신화.
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    _seed_project(svc, "cv1")
    svc.generate_cover("cv1", prompt_override="first, no text")
    png2 = b"\x89PNG\r\n\x1a\nSECOND"
    svc._image_client = _fake_image_client(png=png2)
    svc.generate_cover("cv1", prompt_override="second, no text")
    st = svc.repo.get("cv1")
    # 보관함 2건(둘 다 보존) + 적용본 = 최신
    ok = (len(st.covers) == 2 and st.covers[0].prompt == "first, no text"
          and st.covers[1].prompt == "second, no text")
    ok &= (st.cover.prompt == "second, no text" and st.cover.filename == st.covers[-1].filename)
    ok &= (st.covers[0].filename != st.covers[1].filename)      # 같은 초라도 파일명 충돌 없음(-N suffix)
    # 각 표지 바이너리 개별 보존(비덮어쓰기)
    ok &= (svc.repo.read_cover_file("cv1", st.covers[1].filename) == png2)
    ok &= (svc.repo.read_cover_file("cv1", st.covers[0].filename) == _PNG)
    ok &= (st.usage_total.get("image_calls") == 2)             # 누적
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 재생성: 보관함 append·개별 파일 보존·적용본 최신·image_calls 누적")
    assert ok


def test_service_missing_project_returns_none():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    ok = (svc.generate_cover("nope") is None and svc.get_cover_bytes("nope") is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 없는 작품 → None(404)")
    assert ok


def test_service_locked_when_generation_running():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    _seed_project(svc, "cv1")
    # 회차 생성 중 시뮬 — sess.lock 을 다른 스레드가 보유 → non-blocking 실패 → False(423)
    sess = svc.sessions.get_or_create(svc.repo.get("cv1"))
    sess.lock.acquire()
    try:
        res = svc.generate_cover("cv1")
    finally:
        sess.lock.release()
    ok = (res is False and svc.repo.get("cv1").cover is None)   # 무변경
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 회차 생성 중 → False(423)·무변경")
    assert ok


def test_service_failure_leaves_state_unchanged():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client(fail=True))   # 이미지 콜 실패
    _seed_project(svc, "cv1")
    raised = False
    try:
        svc.generate_cover("cv1")
    except Exception:
        raised = True
    st = svc.repo.get("cv1")
    ok = (raised and st.cover is None and svc.repo.read_cover_bytes("cv1") is None
          and st.usage_total == {})                            # 무변경(저장·메타·usage 불변)
    # 실패 후 락 해제되어 재시도 가능(진행중 pid 잔류 없음)
    ok &= ("cv1" not in svc._cover_pids)
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 이미지 실패 → 예외·상태 무변경·락 해제(P-2)")
    assert ok


def test_service_duplicate_cover_locked():
    # _cover_pids 에 이미 들어있으면(다른 생성 진행 중) 즉시 False(423)
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    _seed_project(svc, "cv1")
    svc._cover_pids.add("cv1")
    res = svc.generate_cover("cv1")
    ok = (res is False)
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ 중복 표지 생성 → False(423)")
    assert ok


# ---------- ⓕ 라우트(TestClient) ----------
def _client(svc: CopilotService):
    from fastapi import FastAPI
    from novelcopilot.api.routes import router
    from fastapi.testclient import TestClient
    app = FastAPI(); app.state.service = svc; app.include_router(router)
    return TestClient(app)


def test_route_post_success_and_get_png():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    _seed_project(svc, "cv1")
    c = _client(svc)
    r = c.post("/api/projects/cv1/cover", json={})
    fn = r.json().get("filename", "")
    ok = (r.status_code == 200 and fn.startswith("cv1.cover.") and fn.endswith(".png"))
    g = c.get("/api/projects/cv1/cover")
    ok &= (g.status_code == 200 and g.headers["content-type"] == "image/png" and g.content == _PNG)
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ POST 성공(메타)·GET PNG(image/png)")
    assert ok


def test_route_get_404_when_no_cover():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    _seed_project(svc, "cv1")
    c = _client(svc)
    ok = (c.get("/api/projects/cv1/cover").status_code == 404)        # 표지 없음
    ok &= (c.get("/api/projects/nope/cover").status_code == 404)      # 작품 없음
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ GET 404(표지 없음·작품 없음)")
    assert ok


def test_route_post_404_when_no_project():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    c = _client(svc)
    ok = (c.post("/api/projects/nope/cover", json={}).status_code == 404)
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ POST 404(없는 작품)")
    assert ok


def test_route_post_423_when_locked():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    _seed_project(svc, "cv1")
    svc._cover_pids.add("cv1")                                        # 중복 생성 진행 중 시뮬
    c = _client(svc)
    ok = (c.post("/api/projects/cv1/cover", json={}).status_code == 423)
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ POST 423(중복/생성 중 락)")
    assert ok


def test_route_post_500_on_failure_no_change():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client(fail=True))
    _seed_project(svc, "cv1")
    c = _client(svc)
    r = c.post("/api/projects/cv1/cover", json={})
    ok = (r.status_code == 500 and "표지 생성 실패" in r.json()["detail"])
    ok &= (svc.repo.get("cv1").cover is None and svc.repo.read_cover_bytes("cv1") is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ POST 500(실패)·상태 무변경(명시 에러)")
    assert ok


def test_route_get_project_exposes_cover_meta():
    tmp = Path(tempfile.mkdtemp(prefix="cv1_"))
    svc = _svc(tmp, wg=_FakeWG(), img=_fake_image_client())
    _seed_project(svc, "cv1")
    c = _client(svc)
    # 생성 전 cover=None
    ok = (c.get("/api/projects/cv1").json()["cover"] is None)
    c.post("/api/projects/cv1/cover", json={"prompt": "abc, no text"})
    body = c.get("/api/projects/cv1").json()
    ok &= (body["cover"] and body["cover"]["prompt"] == "abc, no text")
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ GET project 가 cover 메타 노출(없으면 None)")
    assert ok


_TESTS = [
    test_config_defaults,
    test_pick_protagonist_priority, test_build_cover_context_includes_contract_and_profile,
    test_synthesize_prompt_forces_no_text, test_synthesize_style_anchor_always_appended,
    test_synthesize_title_block_default_on,
    test_genre_accent_specificity_and_fallback, test_synthesize_prompt_override_not_used_by_synth,
    test_covermeta_additive_default_none, test_old_json_loads_without_cover,
    test_covermeta_history_roundtrip,
    test_image_client_b64_to_png, test_image_client_passes_config_params,
    test_image_client_empty_prompt_rejected,
    test_service_generate_success_persists_meta_and_usage, test_service_override_skips_synthesis,
    test_service_regen_pushes_history_and_overwrites, test_service_missing_project_returns_none,
    test_service_locked_when_generation_running, test_service_failure_leaves_state_unchanged,
    test_service_duplicate_cover_locked,
    test_route_post_success_and_get_png, test_route_get_404_when_no_cover,
    test_route_post_404_when_no_project, test_route_post_423_when_locked,
    test_route_post_500_on_failure_no_change, test_route_get_project_exposes_cover_meta,
]

if __name__ == "__main__":
    results = []
    for t in _TESTS:
        try:
            t(); results.append(True)
        except AssertionError:
            results.append(False)
        except Exception as e:
            print(f"[ERR] {t.__name__}: {type(e).__name__}: {e}")
            results.append(False)
    print(f"\n{sum(results)}/{len(results)} PASS")
    sys.exit(0 if all(results) else 1)
