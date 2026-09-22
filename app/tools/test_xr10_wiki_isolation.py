# -*- coding: utf-8 -*-
"""XR-10/15/16/17 검증 — stale 위키 격리 + Wiki Projection candidate 재구축 (실 LLM 0콜).

계약(cross-review/007 §3 · 009 §5~7):
  ① 단기 격리: wiki-stale(또는 지문 불일치)이 있으면 생성에 넘어가는 wiki 의 retrieve 가 빈 결과.
  ② 재구축 v2: candidate 빌드(라이브 무변이) → 원천 보존(seed·비인물 페이지·인물 수동 edge) →
     입력 재검증(회차 집합·본문 지문) → 락 안 원자 교체(객체 동일성 유지). 성공 시에만 wiki stale 소거.
  ③ 실패·입력 변경: candidate 폐기 — 라이브 위키·stale 표식 무변(복원 자체가 불필요한 구조).
  ④ 하위호환: source_fingerprints 없는 구 JSON 무변경 로드.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr10_wiki_isolation.py
"""
from __future__ import annotations
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from novelcopilot.domain.types import ChapterStatus, WikiPage, text_fingerprint
from novelcopilot.domain.world import WikiSeed
from novelcopilot.engine.wiki import Wiki
from novelcopilot.services.copilot import _WikiRetrievalDisabled, _wiki_for_generation

from test_xr7_stale_derivatives import _FakeProvider, _FakeOnt, _Ent, _ch, _svc


def test_isolation_proxy_and_selector() -> bool:
    w = Wiki(_FakeProvider())
    w.seed_page(WikiPage(page_id="hero", page_type="character", body="카드"))
    w.log.append("[1][hero][update]")
    prox = _WikiRetrievalDisabled(w)
    ok = prox.retrieve("아무 질의", 3) == []                     # 격리: 검색만 무효
    ok &= prox.pages is w.pages and prox.log is w.log            # 나머지는 원본 위임
    sel, chs, mm = _wiki_for_generation(w, [{"chapter": 2, "names": ["wiki", "claim_audit"]}])
    ok &= isinstance(sel, _WikiRetrievalDisabled) and chs == [2] and mm == []
    sel2, chs2, mm2 = _wiki_for_generation(w, [{"chapter": 2, "names": ["claim_audit"]}])
    ok &= sel2 is w and chs2 == [] and mm2 == []                 # wiki-stale 없으면 원본(바이트 동일)
    sel3, chs3, _ = _wiki_for_generation(w, [])
    ok &= sel3 is w and chs3 == []
    print(f"[{'OK' if ok else 'FAIL'}] 격리 프록시: retrieve 만 무효·위임 보존 · 선택기: wiki-stale 시에만 치환")
    assert ok
    return ok


def _seeded_svc(chapters, seeds=(), plot_pages=(), ont=None):
    """seed 딸린 서비스 픽스처 — world.wiki_seeds 주입 + 라이브 위키에 진화분 페이지 시드."""
    ont = ont or _FakeOnt([_Ent("hero", "진우")])
    svc, sess, prov = _svc(chapters, ont=ont)
    st = svc.repo.get("p1")
    st.world.wiki_seeds = list(seeds)
    svc.repo.save(st)
    for p in plot_pages:
        sess.bundle.wiki.pages[p.page_id] = p
    return svc, sess, prov


def test_rebuild_preserves_sources_and_regenerates_characters() -> bool:
    seeds = [WikiSeed(page_id="thread_a", page_type="plot_thread", body="원문 seed", payoff_deadline=9),
             WikiSeed(page_id="thread_gone", page_type="plot_thread", body="유실됐던 seed", payoff_deadline=5)]
    evolved = WikiPage(page_id="thread_a", page_type="plot_thread", body="진화한 스레드 본문",
                       payoff_deadline=9)
    place = WikiPage(page_id="office", page_type="place", body="관리국 로비")
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _seeded_svc(chs, seeds=seeds, plot_pages=[evolved, place])
    w = sess.bundle.wiki
    w.seed_page(WikiPage(page_id="hero", page_type="character", body="퇴고 전 원고 기준 오염 카드"))
    w.log += ["[1][hero][update]"]
    res = svc.rebuild_wiki("p1")
    ok = res["rebuilt_chapters"] == 1 and res["stale_cleared"] == 1
    ok &= w.pages["thread_a"].body == "진화한 스레드 본문"           # 진화분 보존(seed 원문으로 되감지 않음)
    ok &= w.pages["thread_a"].payoff_deadline == 9
    ok &= w.pages["thread_gone"].body == "유실됐던 seed"             # 라이브에서 유실된 seed 복원
    ok &= w.pages["office"].page_type == "place"                     # 비인물 페이지 전량 보존
    ok &= "오염 카드" not in w.pages["hero"].body                    # 파생(character)만 신선 재합성
    ok &= w.pages["hero"].source_fingerprints.get("1") == text_fingerprint("진우가 문을 열었다.")
    st = svc.repo.get("p1")
    ok &= st.chapter(1).derivatives_revised_stale == {}
    print(f"[{'OK' if ok else 'FAIL'}] 재구축 v2: seed·비인물·진화분 보존 + 유실 seed 복원 + character 재합성·지문")
    assert ok
    return ok


def test_live_wiki_untouched_during_replay() -> bool:
    """XR-16: replay(LLM 콜) 동안 공유 라이브 위키가 1바이트도 변하지 않는다 — 콜 시점 프로브로 검증."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _svc(chs, ont=ont)
    w = sess.bundle.wiki
    w.seed_page(WikiPage(page_id="hero", page_type="character", body="라이브 카드"))
    w.log.append("[1][hero][update]")
    probes = []
    orig = prov.chat_json

    def _probe(messages, **k):
        probes.append((dict(w.pages) and w.pages["hero"].body, list(w.log)))   # 콜 중 라이브 상태 캡처
        return orig(messages, **k)
    prov.chat_json = _probe
    svc.rebuild_wiki("p1")
    ok = probes and all(body == "라이브 카드" and "[1][hero][update]" in log for body, log in probes)
    ok &= w.pages["hero"].body == "상태: 재계산된 카드"               # 교체는 커밋 시점에만
    print(f"[{'OK' if ok else 'FAIL'}] XR-16: replay 중 라이브 무변이 · 교체는 락 안 커밋에서만")
    assert ok
    return ok


def test_rebuild_aborts_on_midflight_edit() -> bool:
    """XR-17: replay 중 원고가 바뀌면 candidate 폐기 + 라이브·표식 유지(lost-update 차단)."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _svc(chs, ont=ont)
    w = sess.bundle.wiki
    w.seed_page(WikiPage(page_id="hero", page_type="character", body="라이브 카드"))
    orig = prov.chat_json

    def _edit_during_replay(messages, **k):
        st = svc.repo.get("p1")
        st.chapter(1).text = "퇴고로 바뀐 본문이다."                  # replay 도중 편집 발생 시뮬
        svc.repo.save(st)
        return orig(messages, **k)
    prov.chat_json = _edit_during_replay
    try:
        svc.rebuild_wiki("p1")
        ok = False
    except ValueError as e:
        ok = "candidate 를 폐기" in str(e)
    ok &= w.pages["hero"].body == "라이브 카드"                       # 라이브 무변
    st = svc.repo.get("p1")
    ok &= st.chapter(1).derivatives_revised_stale == {"wiki": True}   # stale 유지 = 격리 지속
    print(f"[{'OK' if ok else 'FAIL'}] XR-17: 중도 편집 → 폐기·라이브 무변·stale 유지")
    assert ok
    return ok


def test_rebuild_aborts_on_new_finalized() -> bool:
    """XR-17: replay 중 새 회차가 확정되면 candidate 는 전체 Projection 이 아니므로 커밋하지 않는다."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _svc(chs, ont=ont)
    orig = prov.chat_json

    def _finalize_during_replay(messages, **k):
        st = svc.repo.get("p1")
        st.chapters.append(_ch(chapter=2, text="진우가 계단을 올랐다."))
        svc.repo.save(st)
        return orig(messages, **k)
    prov.chat_json = _finalize_during_replay
    try:
        svc.rebuild_wiki("p1")
        ok = False
    except ValueError as e:
        ok = "candidate 를 폐기" in str(e)
    st = svc.repo.get("p1")
    ok &= st.chapter(1).derivatives_revised_stale == {"wiki": True}
    print(f"[{'OK' if ok else 'FAIL'}] XR-17: 신규 확정 회차 → 폐기(부분 Projection 커밋 금지)")
    assert ok
    return ok


def test_rebuild_failure_keeps_live_untouched() -> bool:
    ont = _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _svc(chs, ont=ont)
    w = sess.bundle.wiki
    w.seed_page(WikiPage(page_id="hero", page_type="character", body="이전 카드"))
    w.log.append("[1][hero][update]")

    def _boom(*a, **k):
        raise RuntimeError("ingest 실패")
    prov.chat_json = _boom
    try:
        svc.rebuild_wiki("p1")
        ok = False
    except ValueError as e:
        ok = "재구축 실패" in str(e)
    ok &= w.pages["hero"].body == "이전 카드" and "[1][hero][update]" in w.log   # 라이브 무변(복원 불필요 구조)
    st = svc.repo.get("p1")
    ok &= st.chapter(1).derivatives_revised_stale == {"wiki": True}              # stale 유지=격리 지속(정직)
    print(f"[{'OK' if ok else 'FAIL'}] 실패 경로: candidate 폐기 — 라이브·표식 무변")
    assert ok
    return ok


def test_shell_preserves_manual_edges_of_absent_characters() -> bool:
    """XR-21/28(012 §3·015 §5): 본문 미등장 인물의 수동 edge 는 source shell 보존(옛 합성 본문·지문 미이월·
    retrieve 자동 제외·ARCHIVED). target 은 캡처 시점에 실재했던 인물 페이지만 동일 타입 shell —
    **원래부터 없던 target 은 발명하지 않고 dangling 그대로 보존**(lint 가 노출)."""
    from novelcopilot.domain.types import TypedEdge, WikiLifecycle
    ont = _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _svc(chs, ont=ont)
    w = sess.bundle.wiki
    w.pages["retired"] = WikiPage(page_id="retired", page_type="character", body="은퇴 인물의 옛 합성 카드",
                                  source_fingerprints={"1": "oldfp"},
                                  typed_edges=[TypedEdge(type="extends", target_page_id="mentor",
                                                         source_narrative_order=1),
                                               TypedEdge(type="extends", target_page_id="gone_target",
                                                         source_narrative_order=1)])
    w.pages["mentor"] = WikiPage(page_id="mentor", page_type="character", body="스승의 옛 카드")   # 실재했던 target
    res = svc.rebuild_wiki("p1")
    ok = "retired" in w.pages and len(w.pages["retired"].typed_edges) == 2
    ok &= w.pages["retired"].body == "" and w.pages["retired"].source_fingerprints == {}   # 파생 미이월
    ok &= w.pages["retired"].lifecycle == WikiLifecycle.ARCHIVED
    ok &= all(r.ref != "retired" for r in w.retrieve("아무 질의", 3))
    ok &= "mentor" in w.pages and w.pages["mentor"].body == ""          # 실재했던 target=동일 타입 shell
    ok &= "gone_target" not in w.pages                                  # 없던 target=발명 금지(XR-28)
    ok &= any(v.kind == "wiki_dangling_edge" and "gone_target" in v.text
              for v in w.lint(1))                                       # 결측은 lint 로 정직 노출
    ok &= res["source_shells"] == 2                                     # retired + mentor
    # 재등장 병합: shell 위에 ingest 가 본문을 다시 합성하며 ACTIVE 승격 + edge 유지
    prov.ROUTES = [("인물카드 관리자", {"pages": [{"id": "retired", "body": "상태: 복귀한 카드"}]})]
    w.ingest_chapter(9, "진우가 왔다.", _FakeOnt([_Ent("retired", "진우")]), reviewed=True)
    ok &= w.pages["retired"].body == "상태: 복귀한 카드" and w.pages["retired"].lifecycle == WikiLifecycle.ACTIVE
    ok &= len(w.pages["retired"].typed_edges) == 2
    print(f"[{'OK' if ok else 'FAIL'}] XR-21/28: shell 보존·실재 target shell·결측 target 무발명(lint 노출)·재등장 병합")
    assert ok
    return ok


def test_rebuild_aborts_on_source_change() -> bool:
    """XR-22(012 §4): replay 중 회차 본문 외 '원천'(명부·seed) 변경 → candidate 폐기(혼합 기준 커밋 금지)."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _svc(chs, ont=ont)
    orig = prov.chat_json

    def _add_entity_during_replay(messages, **k):
        sess.bundle.ontology.entities["newbie"] = _Ent("newbie", "신규 인물")   # 작가 인물 추가 시뮬
        return orig(messages, **k)
    prov.chat_json = _add_entity_during_replay
    try:
        svc.rebuild_wiki("p1")
        ok = False
    except ValueError as e:
        ok = "원천" in str(e) and "폐기" in str(e)
    st = svc.repo.get("p1")
    ok &= st.chapter(1).derivatives_revised_stale == {"wiki": True}    # stale 유지=격리 지속

    # seed 변경도 동일 장벽
    ont2 = _FakeOnt([_Ent("hero", "진우")])
    svc2, sess2, prov2 = _svc([_ch(chapter=1, text="진우가 문을 열었다.",
                                   derivatives_revised_stale={"wiki": True})], ont=ont2)
    orig2 = prov2.chat_json

    def _edit_seed_during_replay(messages, **k):
        st2 = svc2.repo.get("p1")
        st2.world.wiki_seeds = [WikiSeed(page_id="late_seed", page_type="plot_thread", body="중도 추가")]
        svc2.repo.save(st2)
        return orig2(messages, **k)
    prov2.chat_json = _edit_seed_during_replay
    try:
        svc2.rebuild_wiki("p1")
        ok = False
    except ValueError as e:
        ok &= "원천" in str(e)
    print(f"[{'OK' if ok else 'FAIL'}] XR-22: 명부·seed 중도 변경 → candidate 폐기·stale 유지")
    assert ok
    return ok


def test_rebuild_immune_to_aba_ontology_edit() -> bool:
    """XR-26(015 §3): 1화 콜 중 임시 인물 추가 → 2화 콜 중 제거(ABA — 시작·종료 digest 동일).
    replay 는 락 안 스냅샷만 읽으므로 유령 페이지가 생기지 않고, candidate 는 일관 명부로 커밋된다."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True}),
           _ch(chapter=2, text="진우와 임시가 마주쳤다.", derivatives_revised_stale={"wiki": True})]
    svc, sess, prov = _svc(chs, ont=ont)
    calls = {"n": 0}

    def _aba(messages, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            sess.bundle.ontology.entities["temp"] = _Ent("temp", "임시")   # A→B
        elif calls["n"] == 2:
            del sess.bundle.ontology.entities["temp"]                      # B→A(digest 원상)
        blob = "\n".join(str(m.get("content", "")) for m in messages)
        if "인물카드 관리자" in blob:
            return {"pages": [{"id": "hero", "body": "상태: 카드"},
                              {"id": "temp", "body": "상태: 유령 카드"}]}
        return {}
    prov.chat_json = _aba
    res = svc.rebuild_wiki("p1")                                           # 스냅샷 일관 → 정상 커밋
    w = sess.bundle.wiki
    ok = res["rebuilt_chapters"] == 2
    ok &= "temp" not in w.pages                                            # 유령 페이지 0(015 재현의 역)
    ok &= "temp" not in sess.bundle.ontology.entities
    print(f"[{'OK' if ok else 'FAIL'}] XR-26: ABA 편집에도 유령 페이지 0 — replay 는 스냅샷 명부만")
    assert ok
    return ok


def test_rebuild_inputs_share_eligibility_predicate() -> bool:
    """XR-27: 재구축 입력 자격 = 소비 검증과 같은 단일 술어 — ESCALATED·빈 본문 회차는 replay 되지 않는다."""
    ont = _FakeOnt([_Ent("hero", "진우")])
    esc = _ch(chapter=2, text="검토 대기 본문.", status=ChapterStatus.ESCALATED)
    empty = _ch(chapter=3, text="   ")
    chs = [_ch(chapter=1, text="진우가 문을 열었다.", derivatives_revised_stale={"wiki": True}), esc, empty]
    svc, sess, prov = _svc(chs, ont=ont)
    res = svc.rebuild_wiki("p1")
    ok = res["rebuilt_chapters"] == 1 and prov.usage.chat_calls == 1       # FINALIZED+본문 실재 1건만 replay
    print(f"[{'OK' if ok else 'FAIL'}] XR-27: 비자격 회차(ESCALATED·빈 본문) replay 제외 — 단일 술어")
    assert ok
    return ok


def test_lint_single_reference_capture() -> bool:
    """XR-29(015 §6): 복합 조회가 캡처한 참조 하나로 목록·lint 를 계산 — 교체가 끼어도 같은 세대 유지."""
    w = Wiki(_FakeProvider())
    w.seed_page(WikiPage(page_id="old_thread", page_type="plot_thread", body="b", payoff_deadline=1))
    ref = w.pages                                                          # 조회 시작: 참조 캡처
    w.import_pages([WikiPage(page_id="new_thread", page_type="plot_thread", body="b2",
                             payoff_deadline=1)], [])                      # 도중 교체(새 세대도 위반 보유)
    viols = w.lint(5, pages=ref)                                           # lint 는 캡처 세대 기준
    ok = any(v.entity == "old_thread" for v in viols) and all(v.entity != "new_thread" for v in viols)
    ok &= [p.page_id for p in ref.values()] == ["old_thread"]              # 목록도 같은 세대
    ok &= any(v.entity == "new_thread" for v in w.lint(5))                 # 미전달=현행 self.pages(종전 동작)
    print(f"[{'OK' if ok else 'FAIL'}] XR-29: 단일 참조 캡처 — 목록·lint 동세대 · 미전달=종전 동작")
    assert ok
    return ok


def test_old_json_roundtrip() -> bool:
    pg = WikiPage.model_validate({"page_id": "x", "page_type": "character", "body": "b"})
    ok = pg.source_fingerprints == {}                              # 구 JSON(필드 부재) 기본값 로드
    dumped = pg.model_dump(exclude_defaults=True)
    ok &= "source_fingerprints" not in dumped                      # 미사용 시 직렬화 미출현(바이트 동일)
    print(f"[{'OK' if ok else 'FAIL'}] 하위호환: 필드 부재 로드·미사용 직렬화 미출현")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_isolation_proxy_and_selector(),
               test_rebuild_preserves_sources_and_regenerates_characters(),
               test_live_wiki_untouched_during_replay(),
               test_rebuild_aborts_on_midflight_edit(),
               test_rebuild_aborts_on_new_finalized(),
               test_rebuild_failure_keeps_live_untouched(),
               test_shell_preserves_manual_edges_of_absent_characters(),
               test_rebuild_aborts_on_source_change(),
               test_rebuild_immune_to_aba_ontology_edit(),
               test_rebuild_inputs_share_eligibility_predicate(),
               test_lint_single_reference_capture(),
               test_old_json_roundtrip()]
    print("\nXR-10/15~17/21/22/26~29 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
