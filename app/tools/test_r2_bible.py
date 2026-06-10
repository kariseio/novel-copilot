# -*- coding: utf-8 -*-
"""R2 검증 — 설정집 모델/컴파일(promote→world_rule)/digest/migrate/서비스 promote (LLM 0콜).
실행: PYTHONPATH=app python tools/test_r2_bible.py
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.domain.bible import BibleEntry, StoryBible, template_for, GENRE_TEMPLATES
from novelcopilot.domain.world import WorldConfig, WorldRuleSpec, EntitySpec
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.engine.bible_compiler import entry_to_world_rule, bible_digest, migrate_world_to_bible
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService


def test_compile() -> bool:
    ok = True
    ok &= "magic_system" in template_for("정통 판타지") and len(template_for("듣보장르")) > 0  # 기본 폴백
    e = BibleEntry(entry_id="x", category="taboo_worldrule", title="각성은 일생 한 번", prose="재각성 불가.")
    wr = entry_to_world_rule(e, set())
    ok &= wr.text == "재각성 불가." and wr.flag == wr.rule_id and len(wr.keywords) >= 1
    b = StoryBible(entries=[
        BibleEntry(entry_id="a", category="glossary", title="A", prose="x" * 50, promoted=False),
        BibleEntry(entry_id="b", category="magic_system", title="B", prose="y" * 50, promoted=True)])
    dg, dropped = bible_digest(b, 2000)
    ok &= len(dg) == 1 and dg[0].source == "bible" and dg[0].text.index("B:") < dg[0].text.index("A:")  # promoted 먼저
    w = WorldConfig(title="t", genre="x", world_rules=[WorldRuleSpec(rule_id="r1", text="규칙1", flag="r1")])
    migr = migrate_world_to_bible(w)
    ok &= len(migr) == 1 and migr[0].promoted and migr[0].category == "taboo_worldrule"
    print(f"[{'OK' if ok else 'FAIL'}] 컴파일: template_for / entry→world_rule / digest(promoted우선) / migrate")
    return ok


def test_promote_service() -> bool:
    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    w = WorldConfig(title="t", genre="현대 판타지", entities=[EntitySpec(id="hero", name="주인공")])
    st = ProjectState(id="t", seed=ProjectSeed(), world=w, created_at="t",
                      bible=StoryBible(entries=[BibleEntry(entry_id="e1", category="taboo_worldrule",
                                                           title="각성은 한 번", prose="재각성 불가.",
                                                           provenance="author")]))
    svc.repo.save(st)
    n0 = len(w.world_rules)
    res = svc.promote_bible_entry("t", "e1")
    st2 = svc.get_project("t")
    sess, _ = svc.get_session("t")
    rule_ids = {r.rule_id for r in sess.bundle.checker.rule_engine.rules}
    ont_rules = " ".join(sess.bundle.ontology.rules)
    ok = (res and res["promoted"] and len(st2.world.world_rules) == n0 + 1
          and st2.bible.get("e1").promoted and res["rule_id"] in rule_ids       # 라이브 엔진 즉시 반영
          and "재각성 불가." in ont_rules                                       # 프롬프트 주입용 rules 에도
          and st2.world.world_rules[-1].text == "재각성 불가.")
    res2 = svc.promote_bible_entry("t", "e1")                                   # 재승격 멱등
    ok &= res2.get("already") is True and len(svc.get_project("t").world.world_rules) == n0 + 1
    print(f"[{'OK' if ok else 'FAIL'}] 서비스 promote: world_rule 승격 + 라이브엔진/ontology.rules 반영 + 멱등")
    return ok


def test_worldrule_fires() -> bool:
    """#1/#7 회귀 — 등록된 world_rule 은 키워드-텍스트 일치와 무관하게 추출기 flag 로 발화."""
    from novelcopilot.engine.rules.predicates import get as get_pred
    from novelcopilot.domain.types import RuleSpec, SignalGrade
    ev = get_pred("worldrule_flag")
    rule = RuleSpec(rule_id="bible_x", layer="worldrule", predicate_kind="worldrule_flag",
                    grade=SignalGrade.SEMANTIC, params={"flag": "bible_x", "rule_keywords": ["전혀무관한키워드"]})
    fire = ev.evaluate(rule, {"__id": "a", "__name": "가", "bible_x": True}, None, 5, None)
    fire_str = ev.evaluate(rule, {"__id": "a", "__name": "가", "bible_x": "true"}, None, 5, None)  # 문자열 bool 도 발화
    nofire = ev.evaluate(rule, {"__id": "a", "__name": "가", "bible_x": False}, None, 5, None)
    ok = len(fire) == 1 and fire[0].kind == "worldrule(bible_x)" and len(fire_str) == 1 and len(nofire) == 0
    ok &= not fire[0].is_hard      # M3 계약: 세계규칙 위반은 SEMANTIC=advisory(하드 게이트 아님)
    print(f"[{'OK' if ok else 'FAIL'}] world_rule 발화: flag truthy→위반(자기참조게이트 제거·문자열bool), flag=False→무위반, advisory(non-hard)")
    return ok


def test_demote_on_delete() -> bool:
    """C — promoted 항목 삭제 시 연결 world_rule 까지 제거(orphan 캐논 방지)."""
    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    w = WorldConfig(title="t", genre="현대 판타지", entities=[EntitySpec(id="hero", name="주인공")])
    st = ProjectState(id="t", seed=ProjectSeed(), world=w, created_at="t",
                      bible=StoryBible(entries=[BibleEntry(entry_id="e1", category="taboo_worldrule",
                                                           title="금기", prose="금기 위반 시 대가.")]))
    svc.repo.save(st)
    pr = svc.promote_bible_entry("t", "e1")
    n1 = len(svc.get_project("t").world.world_rules)
    svc.delete_bible_entry("t", "e1")
    st2 = svc.get_project("t")
    sess, _ = svc.get_session("t")
    rule_ids = {r.rule_id for r in sess.bundle.checker.rule_engine.rules}
    ok = (n1 == 1 and len(st2.world.world_rules) == 0          # 연결 캐논 제거
          and pr["rule_id"] not in rule_ids                    # 라이브 엔진에서도 제거
          and not st2.bible.get("e1"))                          # 항목 삭제
    print(f"[{'OK' if ok else 'FAIL'}] demote-on-delete: promoted 삭제→world_rule {n1}→0·라이브 제거·orphan 없음")
    return ok


def test_shared_rule_refcount() -> bool:
    """M7 — 동일 prose 두 항목 promote(공유 rule_id). 한 항목 삭제 시 나머지 캐논 유지(orphan 방지), 둘 다 삭제 시 제거."""
    s = get_settings()
    svc = CopilotService(s, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
    w = WorldConfig(title="t", genre="현대 판타지", entities=[EntitySpec(id="hero", name="주인공")])
    st = ProjectState(id="t", seed=ProjectSeed(), world=w, created_at="t",
                      bible=StoryBible(entries=[
                          BibleEntry(entry_id="e1", category="taboo_worldrule", title="금기A", prose="공유규칙.", provenance="author"),
                          BibleEntry(entry_id="e2", category="taboo_worldrule", title="금기B", prose="공유규칙.", provenance="author")]))
    svc.repo.save(st)
    r1 = svc.promote_bible_entry("t", "e1")
    r2 = svc.promote_bible_entry("t", "e2")
    ok = (r1["rule_id"] == r2["rule_id"]) and len(svc.get_project("t").world.world_rules) == 1   # 동일 text→공유 1룰
    d1 = svc.delete_bible_entry("t", "e1")
    sess, _ = svc.get_session("t")
    ok &= (d1["demoted"] is False and len(svc.get_project("t").world.world_rules) == 1            # e2 참조 중 → 유지
           and r1["rule_id"] in {r.rule_id for r in sess.bundle.checker.rule_engine.rules})
    d2 = svc.delete_bible_entry("t", "e2")
    ok &= d2["demoted"] is True and len(svc.get_project("t").world.world_rules) == 0              # 마지막 참조 삭제→제거
    print(f"[{'OK' if ok else 'FAIL'}] M7 공유룰 refcount: 공유 promote→한쪽 삭제 캐논 유지, 둘 다 삭제 시 제거")
    return ok


if __name__ == "__main__":
    results = [test_compile(), test_promote_service(), test_worldrule_fires(), test_demote_on_delete(),
               test_shared_rule_refcount()]
    print("\nR2 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
