# -*- coding: utf-8 -*-
"""문서 포털 서빙 API — GET /api/docs/{name} 화이트리스트 검증(라이브 0·서비스 무의존).

계약(2026-07-14 IA 개편 — 5섹션·17문서):
  ⓐ 화이트리스트 17키가 라우트 상수와 1:1(키·경로) 일치 — 경로 상호검증(파일 존재 무관)
  ⓑ 매핑 밖 이름(제거된 user-guide·changelog 포함) 404
  ⓒ path traversal 시도(../ · 절대경로 등) 404 — 사용자 입력은 dict 키 조회에만 쓰이므로 원천 차단
  ⓓ 매핑에 있으나 파일 부재 시 404(정직) — monkeypatch 로 시뮬
  ⓔ docs 루트 경로가 레포 docs/ 로 결정론 산출됨
  ⓕ 존재하는 파일은 200 + {name, markdown}(실제 내용) 반환. 부재 파일은 404 + 스킵 로그(결측 정직)
     — 콘텐츠 워커가 아직 안 만든 문서가 있어도 배선 테스트는 통과.

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_docs_portal.py
     (또는 pytest tools/test_docs_portal.py)
"""
from __future__ import annotations
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelcopilot.api import routes as R


def _client() -> TestClient:
    """docs 라우트만 필요 — 서비스 없이 라우터를 얹는다(GET /api/docs/{name} 은 svc 미참조)."""
    app = FastAPI()
    app.include_router(R.router)
    return TestClient(app)


# 화이트리스트 키 → 레포 실제 파일 경로(라우트 상수와 독립적으로 재계산해 상호검증).
# IA 계약(하드): 키·경로·순서 그대로. 라벨은 사이드바(manual.js) 계약이라 여기선 경로만 검증.
_EXPECTED = {
    # 시작하기
    "overview": R._DOCS_ROOT / "guide" / "introduction.md",
    "getting-started": R._DOCS_ROOT / "guide" / "getting-started.md",
    "setup": R._DOCS_ROOT / "guide" / "setup.md",
    # 사용 가이드
    "create-work": R._DOCS_ROOT / "guide" / "create-work.md",
    "write-chapters": R._DOCS_ROOT / "guide" / "write-chapters.md",
    "read-verification": R._DOCS_ROOT / "guide" / "read-verification.md",
    "refine-chapters": R._DOCS_ROOT / "guide" / "refine-chapters.md",
    "manage-assets": R._DOCS_ROOT / "guide" / "manage-assets.md",
    "serial-operations": R._DOCS_ROOT / "guide" / "serial-operations.md",
    "export-backup": R._DOCS_ROOT / "guide" / "export-backup.md",
    # 개념
    "how-it-works": R._DOCS_ROOT / "guide" / "how-it-works.md",
    # 레퍼런스
    "settings": R._DOCS_ROOT / "guide" / "settings-reference.md",
    "faq": R._DOCS_ROOT / "guide" / "faq.md",
    "troubleshooting": R._DOCS_ROOT / "guide" / "troubleshooting.md",
    "release-notes": R._DOCS_ROOT / "guide" / "release-notes.md",
    # 기술 문서(심화)
    "pipeline": R._DOCS_ROOT / "PIPELINE.md",
    "architecture": R._DOCS_ROOT / "ARCHITECTURE.md",
}


# ---------- ⓔ docs 루트 결정론 산출 ----------
def test_docs_root_resolves_to_repo_docs():
    # 이관·분리로 특정 콘텐츠 파일 존재를 전제하지 않는다(배선 테스트). 루트가 레포 docs/ 이고
    # 존속이 확실한 심화 문서(PIPELINE.md/ARCHITECTURE.md — 콘텐츠 워커 대상 아님) 존재로만 판정.
    root = R._DOCS_ROOT
    ok = (root.name == "docs" and (root / "PIPELINE.md").is_file()
          and (root / "ARCHITECTURE.md").is_file())
    print(f"[{'OK' if ok else 'FAIL'}] ⓔ docs 루트가 레포 docs/ 로 결정론 산출")
    assert ok


# ---------- ⓐ 화이트리스트 17키가 라우트 상수와 1:1(키·경로) ----------
def test_whitelist_matches_contract_keys_and_paths():
    got = R._DOCS_WHITELIST
    # 키 집합 일치(추가/누락 0)
    keys_ok = set(got.keys()) == set(_EXPECTED.keys())
    if not keys_ok:
        print(f"    - keys diff: extra={set(got)-set(_EXPECTED)} missing={set(_EXPECTED)-set(got)}")
    # 각 경로 일치(resolve 로 정규화 비교)
    paths_ok = True
    for name, path in _EXPECTED.items():
        rp = got.get(name)
        this = (rp is not None and pathlib.Path(rp).as_posix() == pathlib.Path(path).as_posix())
        if not this:
            print(f"    - {name}: route={rp} expected={path}")
        paths_ok &= this
    ok = keys_ok and paths_ok and len(_EXPECTED) == 17
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 화이트리스트 17키·경로가 IA 계약과 1:1")
    assert ok


# ---------- ⓕ 존재 파일 200+내용, 부재 파일 404+스킵 로그(결측 정직) ----------
def test_existing_files_serve_content_missing_are_404():
    c = _client()
    ok, present, missing = True, 0, []
    for name, path in _EXPECTED.items():
        r = c.get(f"/api/docs/{name}")
        if path.is_file():
            body = r.json() if r.status_code == 200 else {}
            expected_md = path.read_text(encoding="utf-8")
            this = (r.status_code == 200 and body.get("name") == name
                    and body.get("markdown") == expected_md and len(expected_md) > 0)
            if not this:
                print(f"    - {name}: status={r.status_code} match={body.get('markdown')==expected_md}")
            ok &= this
            present += 1
        else:
            # 콘텐츠 워커 미완 — 라우트는 404(정직) 계약. 스킵+로그(결측 정직).
            this = (r.status_code == 404)
            if not this:
                print(f"    - {name}: 파일 부재인데 status={r.status_code}(404 여야 함)")
            ok &= this
            missing.append(name)
    if missing:
        print(f"    [skip] 부재 문서 {len(missing)}종(콘텐츠 워커 미작성): {', '.join(missing)}")
    print(f"[{'OK' if ok else 'FAIL'}] ⓕ 존재 {present}종 200+내용 / 부재 {len(missing)}종 404(결측 정직)")
    assert ok


def test_response_shape_is_name_and_markdown():
    """존재하는 아무 문서로 반환 형태 {name, markdown} 검증(내용 선두는 콘텐츠 계약이라 미검증)."""
    c = _client()
    live = next((n for n, p in _EXPECTED.items() if p.is_file()), None)
    if live is None:
        print("[SKIP] ⓕ 존재 문서 0 — 형태 검증 스킵(콘텐츠 워커 미완)")
        return
    body = c.get(f"/api/docs/{live}").json()
    ok = (set(body.keys()) == {"name", "markdown"} and isinstance(body["markdown"], str)
          and body["name"] == live)
    print(f"[{'OK' if ok else 'FAIL'}] ⓐ 반환 형태 {{name, markdown}}({live} 로 검증)")
    assert ok


# ---------- ⓑ 매핑 밖 이름(제거 키 포함) 404 ----------
def test_unknown_name_404():
    c = _client()
    ok = True
    # user-guide·changelog 는 이번 개편으로 화이트리스트에서 제거(분리 소멸/포털 밖 강등) → 404 여야 함.
    # service-overview 는 경로 이관으로 키 아님. 나머지는 애초에 비화이트리스트.
    for bad in ("user-guide", "changelog", "backlog", "readme", "", "PRD",
                "prd", "service-overview", "settings-reference", "introduction"):
        r = c.get(f"/api/docs/{bad}")
        this = (r.status_code == 404)
        if not this:
            print(f"    - {bad!r}: status={r.status_code}")
        ok &= this
    print(f"[{'OK' if ok else 'FAIL'}] ⓑ 비화이트리스트·제거 키(user-guide·changelog) 404")
    assert ok


# ---------- ⓒ path traversal 시도 404 ----------
def test_path_traversal_attempts_404():
    c = _client()
    # 인코딩/미인코딩 traversal·절대경로·다른 확장자 — 전부 매핑에 없으므로 비200(경로 조립 자체가 없음)
    attacks = [
        "../CHANGELOG.md",
        "..%2f..%2fapp%2f.env",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "guide/faq.md",          # 슬래시 포함 — 라우트 {name} 은 단일 세그먼트라 매칭 자체 실패
        "guide/introduction.md",
        "overview.md",
        "SERVICE-OVERVIEW",
    ]
    ok = True
    for a in attacks:
        r = c.get(f"/api/docs/{a}")
        # 어떤 경우든 200 이 나와서는 안 된다(파일 유출 0). 화이트리스트 밖은 비200.
        this = (r.status_code != 200)
        if not this:
            print(f"    - {a!r}: status={r.status_code} LEAK")
        ok &= this
    print(f"[{'OK' if ok else 'FAIL'}] ⓒ traversal·절대경로 시도 → 비200(파일 유출 0)")
    assert ok


# ---------- ⓓ 매핑에 있으나 파일 부재 → 404(정직) ----------
def test_mapped_but_missing_file_404():
    import tempfile
    from pathlib import Path
    c = _client()
    # 존재하지 않는 경로로 화이트리스트 항목을 일시 치환 → is_file()=False → 404.
    # 존속이 확실한 pipeline(콘텐츠 워커 대상 아님)으로 치환·복원(부수효과 없음 확인).
    ghost = Path(tempfile.gettempdir()) / "does-not-exist-docs-portal-xyz.md"
    orig = R._DOCS_WHITELIST.get("pipeline")
    R._DOCS_WHITELIST["pipeline"] = ghost
    try:
        r = c.get("/api/docs/pipeline")
        ok = (r.status_code == 404)
    finally:
        R._DOCS_WHITELIST["pipeline"] = orig
    # 복원 후 다시 200 인지 확인(부수효과 없음)
    ok &= (c.get("/api/docs/pipeline").status_code == 200)
    print(f"[{'OK' if ok else 'FAIL'}] ⓓ 매핑 O·파일 부재 → 404(복원 후 200)")
    assert ok


_TESTS = [
    test_docs_root_resolves_to_repo_docs,
    test_whitelist_matches_contract_keys_and_paths,
    test_existing_files_serve_content_missing_are_404,
    test_response_shape_is_name_and_markdown,
    test_unknown_name_404,
    test_path_traversal_attempts_404,
    test_mapped_but_missing_file_404,
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
