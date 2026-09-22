# -*- coding: utf-8 -*-
"""XR-30~33 검증 — 세션 세대 교체/issued handle 경합·의미 digest (실 LLM 0콜).

계약(cross-review/018):
  · 프로젝트 락은 세션 수명보다 길다 — 축출·재수화로 세대가 교체돼도 같은 락 객체 공유(직렬화 유지).
  · 재구축 커밋의 기준·대상은 지역 변수의 옛 세대가 아니라 **현재 매니저 세션** — 새 세대의 지속 변경
    (인물 추가·타입 카탈로그 변경)은 폐기 사유이고, 성공 커밋은 현재 세션 위키에 반영된다.
  · 커밋 창 중 재교체 세대는 축출(다음 요청이 커밋된 디스크로 재수화).
  · digest 는 replay 가 읽는 의미 전부(엔티티 전 필드 + entity_types 카탈로그) — actor 판정 변경 검출.
  · XR-32: 축출된 세션 참조를 먼저 받은 waiter 도 락 획득 뒤 현재 세대로 재해석한다.
  · XR-33: Wiki roster 가 소비하는 엔티티 삽입 순서가 digest 에 포함된다.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr30_session_generation.py
"""
from __future__ import annotations
import sys
import pathlib
import threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from types import SimpleNamespace as NS

from novelcopilot.services.copilot import _wiki_source_digest
from test_xr7_stale_derivatives import (_FakeProvider, _FakeOnt, _Ent, _FakeSession, _ch, _svc)


class _SwappableSessions:
    """세대 교체를 표현하는 매니저 픽스처 — 안정 락 공유·evict 시 디스크(repo) 기준 재수화(실물 semantics)."""

    def __init__(self, sess, repo, provider, ont_factory):
        self._sess = sess
        self._repo = repo
        self._provider = provider
        self._ont_factory = ont_factory
        self._stable_lock = sess.lock          # XR-30: 최초 세대의 락이 정본
        self.generations = 1

    def get_or_create(self, state):
        if self._sess is None:                 # 축출됨 → 디스크 기준 새 세대 재수화
            from test_xr7_stale_derivatives import _FakeRag
            sess = _FakeSession(self._provider, self._ont_factory(), _FakeRag())
            sess.lock = self._stable_lock      # 같은 프로젝트 락 공유(session.py XR-30 과 동일 계약)
            st = self._repo.get(state.id)
            if st is not None and st.wiki_pages:
                sess.bundle.wiki.import_pages(st.wiki_pages, st.wiki_log)
            self._sess = sess
            self.generations += 1
        return self._sess

    def current(self, pid):
        return self._sess

    def evict(self, pid):
        self._sess = None


def _swap_svc(chapters, ont_factory):
    svc, sess, prov = _svc(chapters, ont=ont_factory())
    mgr = _SwappableSessions(sess, svc.repo, prov, ont_factory)
    svc.sessions = mgr
    return svc, mgr, prov


def test_stable_lock_survives_eviction_real_manager() -> bool:
    """실물 SessionManager: 축출·재수화를 가로질러 같은 락 객체가 유지된다."""
    import novelcopilot.services.session as S

    class _DummySession:
        def __init__(self, pid, world, provider, settings):
            self.project_id, self.world, self.lock = pid, world, threading.Lock()

        def rehydrate(self, state):
            pass
    orig_es, orig_cp = S.EngineSession, S.create_provider
    S.EngineSession, S.create_provider = _DummySession, lambda settings: NS()
    try:
        from novelcopilot.config import get_settings
        mgr = S.SessionManager(get_settings())
        st = NS(id="p1", world=NS())
        lock1 = mgr.get_or_create(st).lock
        mgr.evict("p1")
        lock2 = mgr.get_or_create(st).lock
        ok = lock1 is lock2                                        # 락 수명 > 세션 수명
        ok &= mgr.current("p1") is not None and mgr.current("zz") is None
    finally:
        S.EngineSession, S.create_provider = orig_es, orig_cp
    print(f"[{'OK' if ok else 'FAIL'}] XR-30: 안정 프로젝트 락 — 축출·재수화 후에도 동일 객체 · current()")
    assert ok
    return ok


def test_commit_lands_on_current_generation() -> bool:
    """018 §5 의 역: replay 중 세대 교체(변경 없음) → 커밋이 **현재** 세션 위키에 반영·stale 소거 정합."""
    ont_factory = lambda: _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, mgr, prov = _swap_svc(chs, ont_factory)
    old_wiki = mgr.current("p1").bundle.wiki
    from novelcopilot.domain.types import WikiPage
    old_wiki.pages["hero"] = WikiPage(page_id="hero", page_type="character", body="오염된 옛 카드")
    st0 = svc.repo.get("p1")
    mgr.current("p1").snapshot_into(st0)      # 디스크에도 옛 위키(재수화 기준)
    svc.repo.save(st0)
    orig = prov.chat_json

    def _swap_mid_replay(messages, **k):
        mgr.evict("p1")                        # replay 중 축출 — 다음 접근이 새 세대 재수화
        return orig(messages, **k)
    prov.chat_json = _swap_mid_replay
    res = svc.rebuild_wiki("p1")
    now = mgr.current("p1")
    ok = res["rebuilt_chapters"] == 1
    ok &= now.bundle.wiki.pages["hero"].body == "상태: 재계산된 카드"   # 현재 세대에 커밋(옛 세대 아님)
    ok &= "오염된 옛 카드" not in now.bundle.wiki.pages["hero"].body
    st = svc.repo.get("p1")
    ok &= st.chapter(1).derivatives_revised_stale == {}                  # 디스크 stale 소거와 세션이 정합
    ok &= next(p.body for p in st.wiki_pages if p.page_id == "hero") == "상태: 재계산된 카드"
    print(f"[{'OK' if ok else 'FAIL'}] XR-30: 세대 교체 후 커밋 → 현재 세션·디스크 정합(옛 위키 잔존 0)")
    assert ok
    return ok


def test_new_generation_source_change_aborts() -> bool:
    """018 §4 의 역: 세대 교체 + 새 세대에서 인물 추가(영속) → 커밋 폐기·stale 유지."""
    ont_factory = lambda: _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, mgr, prov = _swap_svc(chs, ont_factory)
    orig = prov.chat_json

    def _swap_and_add(messages, **k):
        mgr.evict("p1")
        new_sess = mgr.get_or_create(svc.repo.get("p1"))          # 새 세대 즉시 재수화
        new_sess.bundle.ontology.entities["newbie"] = _Ent("newbie", "신규 인물")   # 새 세대의 지속 변경
        return orig(messages, **k)
    prov.chat_json = _swap_and_add
    try:
        svc.rebuild_wiki("p1")
        ok = False
    except ValueError as e:
        ok = "원천" in str(e) and "폐기" in str(e)
    st = svc.repo.get("p1")
    ok &= st.chapter(1).derivatives_revised_stale == {"wiki": True}    # stale 유지=격리 지속
    print(f"[{'OK' if ok else 'FAIL'}] XR-30: 새 세대의 지속 변경 → 폐기(옛 명부 비교 맹점 봉합)")
    assert ok
    return ok


def test_entity_type_semantics_in_digest() -> bool:
    """XR-31(018 §7): actor 판정 카탈로그(entity_types) 변경이 digest 에 잡힌다 — 018 재현의 역."""
    ents = {"m1": _Ent("m1", "괴수", etype="monster")}
    a = NS(entities=ents, entity_types={"monster": NS(category="actor")})
    b = NS(entities=ents, entity_types={"monster": NS(category="object")})
    st = NS(world=NS(wiki_seeds=[]))
    ok = _wiki_source_digest(st, a, "", {}) != _wiki_source_digest(st, b, "", {})   # category 변경 검출
    ok &= _wiki_source_digest(st, a, "", {}) == _wiki_source_digest(st, a, "", {})  # 결정론
    # 통합: replay 중 카탈로그 변경(지속) → 폐기
    def ont_factory():
        o = _FakeOnt([_Ent("hero", "진우")])
        o.entity_types = {"character": NS(category="actor")}
        return o
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, mgr, prov = _swap_svc(chs, ont_factory)
    orig = prov.chat_json

    def _mutate_catalog(messages, **k):
        mgr.current("p1").bundle.ontology.entity_types["character"] = NS(category="object")
        return orig(messages, **k)
    prov.chat_json = _mutate_catalog
    try:
        svc.rebuild_wiki("p1")
        ok = False
    except ValueError as e:
        ok &= "원천" in str(e)
    print(f"[{'OK' if ok else 'FAIL'}] XR-31: 카탈로그 의미 변경 digest 검출·지속 변경 폐기")
    assert ok
    return ok


def test_issued_handle_waiter_cannot_restore_old_wiki() -> bool:
    """XR-32(021 §2): 커밋 중 evict+재수화 waiter가 있어도 옛 Wiki를 재저장할 수 없다."""
    import novelcopilot.services.session as S
    from novelcopilot.domain.types import WikiPage
    from test_xr7_stale_derivatives import _FakeRag

    ont_factory = lambda: _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, _unused, prov = _svc(chs, ont=ont_factory())

    class _ManagedFakeSession(_FakeSession):
        def __init__(self, pid, world, provider, settings):
            super().__init__(provider, ont_factory(), _FakeRag())
            self.project_id, self.world = pid, world

        def rehydrate(self, state):
            if state.wiki_pages:
                self.bundle.wiki.import_pages(state.wiki_pages, state.wiki_log)

    orig_es, orig_cp = S.EngineSession, S.create_provider
    S.EngineSession, S.create_provider = _ManagedFakeSession, lambda settings: prov
    arrived, evicted = threading.Event(), threading.Event()
    errors: list[tuple[str, str]] = []
    seen: dict = {}
    try:
        mgr = S.SessionManager(svc.settings, svc.repo.get)
        svc.sessions = mgr
        live = mgr.get_or_create(svc.repo.get("p1"))
        live.bundle.wiki.pages["hero"] = WikiPage(page_id="hero", page_type="character", body="오래된 카드")
        st0 = svc.repo.get("p1")
        live.snapshot_into(st0)
        svc.repo.save(st0)

        real_save = svc.repo.save

        def _save_barrier(state):
            if threading.current_thread().name == "xr32-rebuild":
                arrived.set()                 # candidate import 뒤, 디스크 커밋 직전
                if not evicted.wait(5):
                    raise RuntimeError("waiter eviction timeout")
            return real_save(state)

        svc.repo.save = _save_barrier

        def _rebuild():
            try:
                seen["result"] = svc.rebuild_wiki("p1")
            except Exception as e:
                errors.append(("rebuild", repr(e)))

        def _waiter():
            try:
                if not arrived.wait(5):
                    raise RuntimeError("commit barrier timeout")
                mgr.evict("p1")               # active writer → pending eviction, 즉시 세대 분기 금지
                evicted.set()
                held = mgr.get_or_create(svc.repo.get("p1"))   # writer 종료 뒤 커밋된 디스크로 재수화
                seen["waiter_body"] = held.bundle.wiki.pages["hero"].body
                with held.lock:
                    state = svc.repo.get("p1")
                    held = mgr.resolve_locked(state)
                    held.snapshot_into(state)
                    real_save(state)
            except Exception as e:
                errors.append(("waiter", repr(e)))

        tr = threading.Thread(target=_rebuild, name="xr32-rebuild")
        tw = threading.Thread(target=_waiter, name="xr32-waiter")
        tr.start(); tw.start(); tr.join(10); tw.join(10)
        st = svc.repo.get("p1")
        disk_body = next(p.body for p in st.wiki_pages if p.page_id == "hero")
        ok = not tr.is_alive() and not tw.is_alive() and not errors
        ok &= seen.get("result", {}).get("rebuilt_chapters") == 1
        ok &= seen.get("waiter_body") == "상태: 재계산된 카드"
        ok &= disk_body == "상태: 재계산된 카드"
        ok &= st.chapter(1).derivatives_revised_stale == {}
    finally:
        S.EngineSession, S.create_provider = orig_es, orig_cp
    print(f"[{'OK' if ok else 'FAIL'}] XR-32: issued-handle waiter → 현재 세대 재해석·옛 Wiki 재덮기 차단")
    assert ok, {"seen": seen, "errors": errors}
    return ok


def test_entity_order_semantics_in_digest() -> bool:
    """XR-33(021 §3): roster가 소비하는 엔티티 삽입 순서 변화는 digest 변화다."""
    a = _FakeOnt([_Ent("a", "Alpha"), _Ent("b", "Beta")])
    b = _FakeOnt([_Ent("b", "Beta"), _Ent("a", "Alpha")])
    st = NS(world=NS(wiki_seeds=[]))
    scan_a = a.scan_present_ids("Alpha Beta")
    scan_b = b.scan_present_ids("Alpha Beta")
    ok = scan_a != scan_b
    ok &= _wiki_source_digest(st, a, "", {}) != _wiki_source_digest(st, b, "", {})
    print(f"[{'OK' if ok else 'FAIL'}] XR-33: roster 순서 변화 → source digest 변화")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_stable_lock_survives_eviction_real_manager(),
               test_commit_lands_on_current_generation(),
               test_new_generation_source_change_aborts(),
               test_entity_type_semantics_in_digest(),
               test_issued_handle_waiter_cannot_restore_old_wiki(),
               test_entity_order_semantics_in_digest()]
    print("\nXR-30~33 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
