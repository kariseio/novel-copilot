# -*- coding: utf-8 -*-
"""CV-2 표지 보관함(갤러리) — 결정론 검증(합성 LLM 콜·이미지 콜 전부 모킹, 라이브 0).

CV-1(단일 표지 덮어쓰기)에서 보관함(ProjectState.covers)으로 확장:
  ① 구 JSON(covers 결측) 로드 호환(=[] 기본·무변경)
  ② legacy 적용본(cover 만·covers 빈)에서 generate → lazy migration + 신규 append(보관함 2건)
  ③ generate 2회 → 파일 2개 개별 보존·적용본=최신
  ④ apply 로 적용본 전환(state.cover SSOT)
  ⑤ 적용본 delete → 최신본으로 적용 이전(폴백)
  ⑥ 비적용본 delete → 적용본 불변
  ⑦ 파일명 경로 탈출 차단(../ ·\\ ·타 프로젝트 접두 → None/404)
  ⑧ delete_project 가 표지 파일(legacy+보관함) 전부 정리
  ⑨ list_summaries 에 cover 요약 포함(없으면 None)
  ⑩ 기존 GET /cover 가 새 구조에서도 적용본 서빙

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_cv2_cover_gallery.py
     (또는 pytest tools/test_cv2_cover_gallery.py)
"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
import base64
import tempfile
import threading
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.project import ProjectState, ProjectSeed, CoverMeta
from novelcopilot.domain.world import WorldConfig, EntitySpec, GenreContract
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.llm.base import LLMProvider
from novelcopilot.llm.image_client import ImageClient


# ===== 모의 provider / 이미지 client (LLM·이미지 콜 0) =====
class _FakeWG(LLMProvider):
    def __init__(self, prompt: str = "A lone swordsman on a cliff, cinematic dusk light."):
        super().__init__(); self._prompt = prompt
    def chat(self, msgs, **k):
        self.usage.chat_calls += 1; self.usage.chat_tokens += 42
        return json.dumps({"prompt": self._prompt}, ensure_ascii=False)
    def embed(self, texts): return [[0.0] * 4 for _ in texts]


_PNG = b"\x89PNG\r\n\x1a\n" + b"FAKEDATA"


class _FakeImagesAPI:
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
    def __init__(self, images): self.images = images


def _fake_image_client(png: bytes = _PNG, fail: bool = False) -> ImageClient:
    return ImageClient(client=_FakeOpenAI(_FakeImagesAPI(png, fail)))


class _FakeSession:
    def __init__(self): self.lock = threading.Lock()
class _FakeSessions:
    def __init__(self): self._s = {}
    def get_or_create(self, state): return self._s.setdefault(state.id, _FakeSession())
    def evict(self, pid): self._s.pop(pid, None)
    def delete_project(self, pid, repo_delete):   # XR-34: 삭제 primitive(픽스처=락 경합 없음)
        self._s.pop(pid, None)
        return repo_delete()


def _world() -> WorldConfig:
    return WorldConfig(
        title="검성의 귀환", genre="무협", tone="비장",
        synopsis="산에서 내려온 검객이 문파를 재건한다.",
        entities=[EntitySpec(id="hero", name="한서린", etype="character",
                             profile="냉정한 검객.")],
        genre_contract=GenreContract(pleasure_engine="복수", vocabulary_tone="무협",
                                     premise_asset="사문 재건"))


def _svc(tmp: Path, img: ImageClient | None = None) -> CopilotService:
    settings = get_settings().model_copy(update={"data_dir": str(tmp)})
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions()
    svc._wg_provider = _FakeWG()
    svc._image_client = img or _fake_image_client()
    return svc


def _seed(svc: CopilotService, pid: str = "cv2") -> ProjectState:
    state = ProjectState(id=pid, seed=ProjectSeed(title="검성의 귀환"), world=_world())
    svc.repo.save(state)
    return state


def _client(svc: CopilotService):
    from fastapi import FastAPI
    from novelcopilot.api.routes import router
    from fastapi.testclient import TestClient
    app = FastAPI(); app.state.service = svc; app.include_router(router)
    return TestClient(app)


# ---------- ① 구 JSON 로드 호환 ----------
def test_old_json_loads_without_covers():
    st = ProjectState(id="p", seed=ProjectSeed(title="옛작품"), world=WorldConfig(title="옛작품"),
                      current_chapter=2)
    d = json.loads(st.model_dump_json()); d.pop("covers", None)   # 구 JSON = covers 키 부재
    st2 = ProjectState.model_validate(d)
    ok = (st2.covers == [] and st2.cover is None and st2.current_chapter == 2)
    print(f"[{'OK' if ok else 'FAIL'}] ① 구 JSON(covers 결측) 로드·[] 기본·기존 필드 불변")
    assert ok


# ---------- ② legacy 적용본 → generate 시 마이그레이션 + append ----------
def test_generate_migrates_legacy_cover_into_gallery():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    st = _seed(svc, "cv2")
    # CV-1 legacy 상태 수제작: 적용본 cover=legacy 파일명, covers 는 빈 채로 저장 + legacy 바이너리
    legacy_png = b"\x89PNG\r\n\x1a\nLEGACY"
    svc.repo.save_cover_bytes("cv2", legacy_png)                  # filename 미지정 → {pid}.cover.png
    st.cover = CoverMeta(filename="cv2.cover.png", prompt="legacy", model="m", size="s",
                         created_at="2026-07-14T09:00:00", history=[])
    svc.repo.save(st)
    # 신규 생성 → 마이그레이션(legacy 승격) + 신규 append = 보관함 2건
    svc.generate_cover("cv2", prompt_override="new art, no text")
    st = svc.repo.get("cv2")
    ok = (len(st.covers) == 2 and st.covers[0].filename == "cv2.cover.png"
          and st.covers[0].prompt == "legacy" and st.covers[1].prompt == "new art, no text")
    ok &= (st.cover.filename == st.covers[1].filename)           # 적용본 = 최신
    # legacy 파일도 보관함 파일로 읽힌다(접두/접미 검증 통과)
    ok &= (svc.repo.read_cover_file("cv2", "cv2.cover.png") == legacy_png)
    print(f"[{'OK' if ok else 'FAIL'}] ② legacy 적용본 → generate 시 마이그레이션 + append(보관함 2)")
    assert ok


# ---------- ③ generate 2회 → 파일 2개·적용=최신 ----------
def test_generate_twice_keeps_both_files_applied_latest():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    svc.generate_cover("cv2", prompt_override="first, no text")
    svc._image_client = _fake_image_client(png=b"\x89PNG\r\n\x1a\nTWO")
    svc.generate_cover("cv2", prompt_override="second, no text")
    st = svc.repo.get("cv2")
    ok = (len(st.covers) == 2 and st.covers[0].filename != st.covers[1].filename)   # 같은 초라도 충돌 없음
    ok &= (st.cover.filename == st.covers[-1].filename and st.cover.prompt == "second, no text")
    ok &= (svc.repo.read_cover_file("cv2", st.covers[0].filename) == _PNG)
    ok &= (svc.repo.read_cover_file("cv2", st.covers[1].filename) == b"\x89PNG\r\n\x1a\nTWO")
    print(f"[{'OK' if ok else 'FAIL'}] ③ generate 2회 → 파일 2개 개별 보존·적용본=최신")
    assert ok


# ---------- ④ apply 전환 ----------
def test_apply_switches_applied_cover():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    svc.generate_cover("cv2", prompt_override="first, no text")
    svc.generate_cover("cv2", prompt_override="second, no text")
    st = svc.repo.get("cv2")
    first_fn = st.covers[0].filename
    res = svc.apply_cover("cv2", first_fn)
    ok = (isinstance(res, dict) and res["filename"] == first_fn)
    st = svc.repo.get("cv2")
    ok &= (st.cover.filename == first_fn and st.cover.prompt == "first, no text")
    ok &= (len(st.covers) == 2)                                  # 보관함 불변
    # 미존재 파일명 → ValueError
    raised = False
    try:
        svc.apply_cover("cv2", "cv2.cover.nope.png")
    except ValueError:
        raised = True
    ok &= raised
    ok &= (svc.apply_cover("nope", first_fn) is None)           # 없는 작품
    print(f"[{'OK' if ok else 'FAIL'}] ④ apply 전환(적용본 SSOT)·미존재 ValueError·없는 작품 None")
    assert ok


# ---------- ⑤ 적용본 delete → 폴백 ----------
def test_delete_applied_cover_falls_back():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    svc.generate_cover("cv2", prompt_override="first, no text")
    svc._image_client = _fake_image_client(png=b"\x89PNG\r\n\x1a\nTWO")
    svc.generate_cover("cv2", prompt_override="second, no text")
    st = svc.repo.get("cv2")
    applied_fn = st.cover.filename                              # = 최신(second)
    prev_fn = st.covers[0].filename
    res = svc.delete_cover("cv2", applied_fn)
    ok = (res["applied"] == prev_fn and len(res["covers"]) == 1)   # 적용본 삭제 → 이전(최신 남은 것)으로
    st = svc.repo.get("cv2")
    ok &= (st.cover.filename == prev_fn)
    ok &= (svc.repo.read_cover_file("cv2", applied_fn) is None)  # 바이너리 삭제
    ok &= (svc.get_cover_bytes("cv2") == _PNG)                   # 적용본(first) 서빙
    # 마지막 하나까지 삭제 → 적용본 해제(None)
    svc.delete_cover("cv2", prev_fn)
    st = svc.repo.get("cv2")
    ok &= (st.cover is None and st.covers == [])
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ 적용본 delete → 최신본 폴백·전량 삭제 시 해제")
    assert ok


# ---------- ⑥ 비적용본 delete ----------
def test_delete_non_applied_cover_keeps_applied():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    svc.generate_cover("cv2", prompt_override="first, no text")
    svc.generate_cover("cv2", prompt_override="second, no text")
    st = svc.repo.get("cv2")
    applied_fn = st.cover.filename                              # second(적용)
    non_applied = st.covers[0].filename                         # first(비적용)
    res = svc.delete_cover("cv2", non_applied)
    ok = (res["applied"] == applied_fn and len(res["covers"]) == 1
          and res["covers"][0]["filename"] == applied_fn)
    ok &= (svc.repo.read_cover_file("cv2", non_applied) is None)
    ok &= (svc.repo.read_cover_file("cv2", applied_fn) is not None)   # 적용본 파일 불변
    # 미존재 파일명 → ValueError, 없는 작품 → None
    raised = False
    try:
        svc.delete_cover("cv2", "cv2.cover.nope.png")
    except ValueError:
        raised = True
    ok &= raised and (svc.delete_cover("nope", applied_fn) is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⑥ 비적용본 delete → 적용본 불변")
    assert ok


# ---------- ⑦ 파일명 경로 탈출 차단 ----------
def test_filename_path_traversal_blocked():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    svc.generate_cover("cv2", prompt_override="first, no text")
    bad = ["../cv2.cover.png", "cv2.cover.png/../x.png", "..\\cv2.cover.png",
           "other.cover.png", "cv2.cover.txt", "cv2.png", "", "cv2.cover./etc/x.png"]
    ok = all(svc.repo.read_cover_file("cv2", b) is None for b in bad)   # 전부 차단
    ok &= all(svc.repo.delete_cover_file("cv2", b) is False for b in bad)
    ok &= all(svc.get_cover_file_bytes("cv2", b) is None for b in bad)
    # save_cover_bytes(filename=부적합) → ValueError(원자성 보호)
    raised = False
    try:
        svc.repo.save_cover_bytes("cv2", _PNG, filename="../evil.png")
    except ValueError:
        raised = True
    ok &= raised
    # 라우트: 접두 불일치 파일명 → 404(경로 세그먼트, slash 없음)
    c = _client(svc)
    ok &= (c.get("/api/projects/cv2/covers/other.cover.png/file").status_code == 404)
    print(f"[{'OK' if ok else 'FAIL'}] ⑦ 경로 탈출·타 프로젝트 접두 차단(None/False/ValueError/404)")
    assert ok


# ---------- ⑧ delete_project 가 표지 파일 전부 정리 ----------
def test_delete_project_cleans_all_cover_files():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    svc.repo.save_cover_bytes("cv2", b"\x89PNG\r\n\x1a\nL")           # legacy 파일도 존재 케이스
    svc.generate_cover("cv2", prompt_override="a, no text")
    svc.generate_cover("cv2", prompt_override="b, no text")
    before = list(Path(tmp, "projects").glob("cv2.cover*.png"))
    svc.delete_project("cv2")
    after = list(Path(tmp, "projects").glob("cv2.cover*.png"))
    ok = (len(before) >= 2 and after == [] and svc.repo.get("cv2") is None)
    print(f"[{'OK' if ok else 'FAIL'}] ⑧ delete_project → 표지 파일(legacy+보관함) 전량 정리")
    assert ok


# ---------- ⑨ list_summaries 에 cover 포함 ----------
def test_list_summaries_includes_cover():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "withcov")
    _seed(svc, "nocov")
    svc.generate_cover("withcov", prompt_override="a, no text")
    summ = {s["id"]: s for s in svc.repo.list_summaries()}
    ok = bool("cover" in summ["withcov"] and summ["withcov"]["cover"]
              and summ["withcov"]["cover"]["filename"].startswith("withcov.cover.")
              and summ["withcov"]["cover"]["created_at"])
    ok &= (summ["nocov"]["cover"] is None)                       # 표지 없음 → None
    print(f"[{'OK' if ok else 'FAIL'}] ⑨ list_summaries 에 적용본 표지 요약 포함(없으면 None)")
    assert ok


# ---------- ⑩ 기존 GET /cover 가 적용본 서빙 ----------
def test_legacy_get_cover_serves_applied():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    c = _client(svc)
    svc.generate_cover("cv2", prompt_override="first, no text")
    svc._image_client = _fake_image_client(png=b"\x89PNG\r\n\x1a\nTWO")
    svc.generate_cover("cv2", prompt_override="second, no text")
    g = c.get("/api/projects/cv2/cover")
    ok = (g.status_code == 200 and g.headers["content-type"] == "image/png"
          and g.content == b"\x89PNG\r\n\x1a\nTWO")             # 적용본=최신
    # apply 로 first 전환 → GET /cover 도 first 서빙
    st = svc.repo.get("cv2")
    svc.apply_cover("cv2", st.covers[0].filename)
    ok &= (c.get("/api/projects/cv2/cover").content == _PNG)
    print(f"[{'OK' if ok else 'FAIL'}] ⑩ 기존 GET /cover 가 새 구조에서 적용본 서빙")
    assert ok


# ---------- 라우트: 보관함 CRUD ----------
def test_route_covers_crud():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    c = _client(svc)
    svc.generate_cover("cv2", prompt_override="first, no text")
    svc.generate_cover("cv2", prompt_override="second, no text")
    # GET /covers
    lst = c.get("/api/projects/cv2/covers").json()
    ok = (len(lst["covers"]) == 2 and lst["applied"] == lst["covers"][-1]["filename"])
    first_fn = lst["covers"][0]["filename"]
    # 개별 파일 서빙(+캐시 헤더: 타임스탬프 파일 = max-age)
    f = c.get(f"/api/projects/cv2/covers/{first_fn}/file")
    ok &= (f.status_code == 200 and f.content == _PNG and "max-age" in f.headers.get("cache-control", ""))
    # apply
    ap = c.post(f"/api/projects/cv2/covers/{first_fn}/apply")
    ok &= (ap.status_code == 200 and ap.json()["filename"] == first_fn)
    ok &= (c.get("/api/projects/cv2/covers").json()["applied"] == first_fn)
    # delete
    d = c.delete(f"/api/projects/cv2/covers/{first_fn}")
    ok &= (d.status_code == 200 and len(d.json()["covers"]) == 1)
    # 404: 없는 작품·미존재 파일명
    ok &= (c.get("/api/projects/nope/covers").status_code == 404)
    ok &= (c.post("/api/projects/cv2/covers/cv2.cover.nope.png/apply").status_code == 404)
    ok &= (c.delete("/api/projects/cv2/covers/cv2.cover.nope.png").status_code == 404)
    print(f"[{'OK' if ok else 'FAIL'}] 라우트 보관함 CRUD(GET/apply/delete/file)·404")
    assert ok


def test_route_apply_delete_423_when_locked():
    tmp = Path(tempfile.mkdtemp(prefix="cv2_"))
    svc = _svc(tmp)
    _seed(svc, "cv2")
    svc.generate_cover("cv2", prompt_override="first, no text")
    st = svc.repo.get("cv2")
    fn = st.covers[0].filename
    c = _client(svc)
    sess = svc.sessions.get_or_create(svc.repo.get("cv2"))
    sess.lock.acquire()                                          # 생성 중 시뮬(락 보유)
    try:
        ap = c.post(f"/api/projects/cv2/covers/{fn}/apply")
        de = c.delete(f"/api/projects/cv2/covers/{fn}")
    finally:
        sess.lock.release()
    ok = (ap.status_code == 423 and de.status_code == 423)
    print(f"[{'OK' if ok else 'FAIL'}] 라우트 apply/delete 423(생성 중 락)·무변경")
    assert ok


_TESTS = [
    test_old_json_loads_without_covers,
    test_generate_migrates_legacy_cover_into_gallery,
    test_generate_twice_keeps_both_files_applied_latest,
    test_apply_switches_applied_cover,
    test_delete_applied_cover_falls_back,
    test_delete_non_applied_cover_keeps_applied,
    test_filename_path_traversal_blocked,
    test_delete_project_cleans_all_cover_files,
    test_list_summaries_includes_cover,
    test_legacy_get_cover_serves_applied,
    test_route_covers_crud,
    test_route_apply_delete_423_when_locked,
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
