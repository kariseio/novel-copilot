# -*- coding: utf-8 -*-
"""XR-5 상태 자동 승격 클래스별 티어링 — 동적 감지 커밋 티어를 속성 선언(AttributeSpec.auto_commit)으로. 실 LLM 0콜.

배경(설계 docs/design-xr-batch.md §2 · cross-review/005 §2.3): 동적 감지 커밋 티어가 두 갈래로 비대칭이었다 —
state 분기는 비가역·terminal 전이만 비구속, mutable 분기는 순서형 후진만 비구속, 나머지는 전부 ground_truth
자동커밋. 그래서 '관찰 가능성'과 무관하게 오추출 1건이 이후 전 회차를 [확정 설정: 절대 위반 금지]로 구속했다.

검증 축(설계 §2.6 계약 9건):
  ① 하위호환 — auto_commit 없는 구 JSON 라운드트립 무변경 · 미선언 축 커밋 티어 현행 동일
  ② non_binding categorical(mutable) 전진 전이 → narrative_inferred · canon_facts 미주입 · state_as_of 는 반환
  ③ non_binding state 가역 전이 → narrative_inferred(현행이면 ground_truth 였던 자리)
  ④ binding 선언 + 비가역 전이 → 여전히 narrative_inferred(강등 단방향 — 선언이 안전장치를 못 푼다)
  ⑤ ordered 후진 + non_binding → narrative_inferred(기존 강등과 합류)
  ⑥ set_entity_state 승격 → ground_truth · provenance ["author"] · canon_facts 주입(기존 계약 재확인)
  ⑦ machine_binding_report — 계수·표본·선언 상태 정확(advisory·수정 0)
  ⑧ PATCH 오버라이드 — 값 검증·저장·이후 커밋 반영
  ⑨ worldgen 적재 — auto_commit 포함 attributes JSON 이 WorldConfig 로 보존 파싱
  ⑩ K1 — updater 에 속성명 사전·정규식·장르 라벨 0(소스 grep assert)

실행: (app/ 에서) PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_xr5_attr_tier.py
     (또는 pytest tools/test_xr5_attr_tier.py)
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.domain.world import (WorldConfig, AttributeSpec, EntitySpec, TimelineEntry,
                                       StyleSpec, Beat)
from novelcopilot.domain.project import ProjectState, ProjectSeed
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.engine.ontology import Ontology, Entity
from novelcopilot.engine.ontology_updater import OntologyUpdater
from novelcopilot.engine.ontology_ops import machine_binding_report


# ═════════════════ 픽스처 ═════════════════

# 의미 기준(설계 §2.2ⓒ): 외적으로 관찰 가능한 사실 = binding / 내면·인지·자각 = non_binding.
#   코드는 이 의미를 모른다 — 선언 값만 읽는다(K1).
def _attrs(**over) -> list[AttributeSpec]:
    a = [
        # 외적 관찰 가능(소속) — 선언별로 갈아 끼우며 시험
        AttributeSpec(key="affiliation", label="소속", kind="categorical",
                      vocab=["관리국", "무소속"], mutable=True,
                      auto_commit=over.get("affiliation", "")),
        # 순서형 — 후진 강등(기존)과의 합류 시험
        AttributeSpec(key="closeness", label="관계 단계", kind="categorical",
                      vocab=["남", "동료", "동행"], mutable=True, ordered=True,
                      auto_commit=over.get("closeness", "")),
        # 내면·자각(가역 생애주기) — non_binding 의 주 대상
        AttributeSpec(key="truth_awareness", label="진실 자각", kind="state",
                      states=["외면", "흠칫", "직시"], mutable=True,
                      auto_commit=over.get("truth_awareness", "")),
        # 생사(비가역·terminal) — 강등 단방향 시험 대상
        AttributeSpec(key="status", label="생사", kind="state", states=["alive", "dead"],
                      irreversible=["dead"], terminal=["dead"], mutable=True,
                      auto_commit=over.get("status", "")),
    ]
    return a


def _onto(attrs: list[AttributeSpec]) -> Ontology:
    o = Ontology(Vocabulary(attrs))
    o.add(Entity(id="hero", name="서준호", etype="character",
                 attrs={"affiliation": "무소속", "closeness": "동행", "truth_awareness": "외면"}))
    return o


class _Bus:
    def __init__(self): self.events = []
    def emit(self, *a, **k): self.events.append((a, k))


def _updater(attrs: list[AttributeSpec], bus=None) -> OntologyUpdater:
    return OntologyUpdater(None, Vocabulary(attrs), bus or _Bus())


def _sc(eid, attr, value):
    return {"state_changes": [{"id": eid, "attr": attr, "value": value}]}


def _tier_of(new_tl, attr: str) -> str | None:
    for t in new_tl:
        if t.attr == attr:
            return t.trust_tier
    return None


# ═════════════════ ① 하위호환(K4) ═════════════════

def test_backcompat_old_json_and_default_tier():
    ok = True
    # 구 JSON(필드 부재) 로드 → 기본 ""(미선언), 라운드트립 무변경
    raw = {"key": "affiliation", "label": "소속", "kind": "categorical",
           "vocab": ["관리국", "무소속"], "mutable": True}
    spec = AttributeSpec.model_validate(raw)
    ok &= spec.auto_commit == ""
    round2 = AttributeSpec.model_validate(spec.model_dump()).model_dump()
    ok &= round2 == spec.model_dump()
    ok &= all(raw[k] == round2[k] for k in raw)                 # 구 필드 전부 그대로

    old_world = {"title": "구작", "attributes": [raw],
                 "entities": [{"id": "hero", "name": "서준호"}]}
    w = WorldConfig.model_validate(old_world)
    ok &= w.attributes[0].auto_commit == ""

    # 미선언 축: mutable 전진 = ground_truth, 비가역 전이 = narrative_inferred (현행 그대로)
    attrs = _attrs()
    upd = _updater(attrs)
    _, _, tl, _ = upd.apply(_sc("hero", "affiliation", "관리국"), _onto(attrs), 5)
    ok &= _tier_of(tl, "affiliation") == "ground_truth"
    attrs2 = _attrs()
    _, _, tl2, _ = _updater(attrs2).apply(_sc("hero", "status", "dead"), _onto(attrs2), 5)
    ok &= _tier_of(tl2, "status") == "narrative_inferred"
    print(f"[{'OK' if ok else 'FAIL'}] ① 구 JSON 무변경 로드·라운드트립 + 미선언 축 커밋 티어 현행 동일")
    assert ok


# ═════════════════ ② non_binding mutable categorical ═════════════════

def test_non_binding_mutable_not_injected_but_visible():
    attrs = _attrs(affiliation="non_binding")
    ont = _onto(attrs)
    changes, _, tl, _ = _updater(attrs).apply(_sc("hero", "affiliation", "관리국"), ont, 5)
    ok = _tier_of(tl, "affiliation") == "narrative_inferred"
    ok &= ont.binding_state_as_of("hero", "affiliation", 6) == "무소속"   # binding 캐논은 안 밀림
    ok &= ont.state_as_of("hero", "affiliation", 6) == "관리국"           # 서사 인지 상태로는 보인다(불변 소비처)
    facts = ont.canon_facts(["hero"], 6)
    ok &= not any(f.value == "관리국" for f in facts)                     # [확정 설정] 미주입
    ok &= any("작가 확정 시 캐논" in (c.detail or "") for c in changes)   # 작가에게 '확정 대기' 가시화
    print(f"[{'OK' if ok else 'FAIL'}] ② non_binding 가변 축 — 비구속 착지·캐논 미주입·state_as_of 는 반환")
    assert ok


# ═════════════════ ③ non_binding state(가역 전이) ═════════════════

def test_non_binding_state_reversible_transition():
    attrs = _attrs(truth_awareness="non_binding")
    ont = _onto(attrs)
    _, _, tl, _ = _updater(attrs).apply(_sc("hero", "truth_awareness", "직시"), ont, 12)
    ok = _tier_of(tl, "truth_awareness") == "narrative_inferred"
    ok &= "(추정)" in (tl[0].reason or "")                     # 비구속 착지 표기 규약 일치
    # 대조군: 같은 전이를 미선언으로 태우면 현행대로 ground_truth
    attrs0 = _attrs()
    _, _, tl0, _ = _updater(attrs0).apply(_sc("hero", "truth_awareness", "직시"), _onto(attrs0), 12)
    ok &= _tier_of(tl0, "truth_awareness") == "ground_truth"
    print(f"[{'OK' if ok else 'FAIL'}] ③ non_binding 생애주기 — 가역 전이도 비구속(미선언 대조군은 현행)")
    assert ok


# ═════════════════ ④ 강등 단방향(K2 구조 근거) ═════════════════

def test_binding_declaration_cannot_unlock_safeguards():
    ok = True
    # ⓐ binding 선언 + 비가역·terminal 전이 → 여전히 비구속(작가 확정 전)
    attrs = _attrs(status="binding")
    _, _, tl, _ = _updater(attrs).apply(_sc("hero", "status", "dead"), _onto(attrs), 7)
    ok &= _tier_of(tl, "status") == "narrative_inferred"
    # ⓑ binding 선언 + 순서형 후진 → 여전히 비구속
    attrs2 = _attrs(closeness="binding")
    ont2 = _onto(attrs2)
    ont2.set_state("hero", "closeness", "동행", 1, reason="시드", trust_tier="ground_truth")
    _, _, tl2, _ = _updater(attrs2).apply(_sc("hero", "closeness", "동료"), ont2, 7)
    ok &= _tier_of(tl2, "closeness") == "narrative_inferred"
    # ⓒ binding 선언은 미선언과 완전히 동일한 산출(승격 경로 부재)
    attrs3, attrs4 = _attrs(affiliation="binding"), _attrs()
    _, _, a, _ = _updater(attrs3).apply(_sc("hero", "affiliation", "관리국"), _onto(attrs3), 5)
    _, _, b, _ = _updater(attrs4).apply(_sc("hero", "affiliation", "관리국"), _onto(attrs4), 5)
    ok &= [t.model_dump() for t in a] == [t.model_dump() for t in b]
    print(f"[{'OK' if ok else 'FAIL'}] ④ 강등 단방향 — 'binding' 선언이 비가역·후진 안전장치를 해제하지 못함")
    assert ok


# ═════════════════ ⑤ ordered 후진 + non_binding 합류 ═════════════════

def test_ordered_backward_and_non_binding_merge():
    attrs = _attrs(closeness="non_binding")
    ont = _onto(attrs)
    ont.set_state("hero", "closeness", "동행", 1, reason="시드", trust_tier="ground_truth")
    changes, _, tl, _ = _updater(attrs).apply(_sc("hero", "closeness", "동료"), ont, 9)
    ok = _tier_of(tl, "closeness") == "narrative_inferred"
    ok &= any("후진(비구속)" in (c.detail or "") for c in changes)          # 기존 라벨 유지(이중 표기 없음)
    ok &= not any("후진(비구속) · 작가 확정 시 캐논" in (c.detail or "") for c in changes)
    print(f"[{'OK' if ok else 'FAIL'}] ⑤ 순서형 후진 + non_binding — 기존 강등과 합류(라벨 중복 0)")
    assert ok


# ═════════════════ ⑥ 작가 승인 승격(기존 레버 재사용) ═════════════════

def _build_app(tmp: pathlib.Path):
    import novelcopilot.config as cfg
    from novelcopilot.main import create_app
    os.environ["NOVEL_DATA_DIR"] = str(tmp)
    cfg._settings = None
    app = create_app()
    return app, app.state.service


class _Env:
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


def _seed(svc, pid="xr5", **over) -> ProjectState:
    st = ProjectState(
        id=pid, seed=ProjectSeed(title="[실험] 티어링", target_chapters=12),
        world=WorldConfig(title="[실험] 티어링", synopsis="s", style=StyleSpec(),
                          attributes=_attrs(**over),
                          entities=[EntitySpec(id="hero", name="서준호",
                                               attrs={"affiliation": "무소속",
                                                      "truth_awareness": "외면"})],
                          beats=[Beat(chapter=1, summary="첫 화")]),
        created_at="2026-08-22T00:00:00")
    st.current_chapter = 3
    svc.repo.save(st)
    return st


def test_author_promotion_via_set_entity_state(tmp_path):
    with _Env(tmp_path) as env:
        _seed(env.svc, truth_awareness="non_binding")
        r = env.svc.set_entity_state("xr5", "hero", "truth_awareness", "직시", eff_from=4)
        ok = r["updated"] is True
        st = env.svc.repo.get("xr5")
        ev = [t for t in st.runtime_timeline if t.attr == "truth_awareness"][-1]
        ok &= ev.trust_tier == "ground_truth" and ev.provenance == ["author"]
        sess, st2 = env.svc.get_session("xr5")
        ont = sess.bundle.ontology
        ok &= ont.binding_state_as_of("hero", "truth_awareness", 5) == "직시"
        ok &= any(f.value == "직시" for f in ont.canon_facts(["hero"], 5))   # 승인 후 [확정 설정] 재진입
        print(f"[{'OK' if ok else 'FAIL'}] ⑥ 작가 승인 승격 — set_entity_state 가 ground_truth·author·캐논 주입")
        assert ok


# ═════════════════ ⑦ 마이그레이션 리포트(advisory·수정 0) ═════════════════

def test_machine_binding_report():
    world = WorldConfig(title="t", attributes=_attrs(truth_awareness="non_binding"),
                        entities=[EntitySpec(id="hero", name="서준호")])
    tl = [
        TimelineEntry(entity_id="hero", attr="truth_awareness", value="흠칫", eff_from=3,
                      reason="2화 동적 감지", trust_tier="ground_truth"),                 # machine(기본)
        TimelineEntry(entity_id="hero", attr="truth_awareness", value="직시", eff_from=9,
                      reason="작가 정정", trust_tier="ground_truth", provenance=["author"]),
        TimelineEntry(entity_id="hero", attr="affiliation", value="관리국", eff_from=4,
                      reason="3화 동적 감지", trust_tier="ground_truth"),
        TimelineEntry(entity_id="hero", attr="affiliation", value="무소속", eff_from=7,
                      reason="6화 동적 감지(추정)", trust_tier="narrative_inferred"),
        TimelineEntry(entity_id="ghost", attr="mood", value="분노", eff_from=2,          # 미선언 축
                      reason="1화 동적 감지", trust_tier="ground_truth"),
    ]
    st = ProjectState(id="p", seed=ProjectSeed(title="t"), world=world, runtime_timeline=list(tl))
    before = [t.model_dump() for t in st.runtime_timeline]
    rep = machine_binding_report(st)
    by = {a["attr"]: a for a in rep["attributes"]}
    ok = set(by) == {"affiliation", "closeness", "truth_awareness", "status", "mood"}
    ok &= by["truth_awareness"]["machine_binding"] == 1 and by["truth_awareness"]["author_binding"] == 1
    ok &= by["truth_awareness"]["auto_commit"] == "non_binding" and by["truth_awareness"]["declared"] is True
    ok &= by["affiliation"]["machine_binding"] == 1 and by["affiliation"]["non_binding"] == 1
    ok &= by["affiliation"]["auto_commit"] == ""
    ok &= by["mood"]["declared"] is False and by["mood"]["machine_binding"] == 1
    ok &= by["closeness"]["machine_binding"] == 0                    # 잔량 0 인 선언 축도 목록에 남는다
    ok &= by["affiliation"]["samples"][0]["entity"] == "서준호"
    ok &= by["affiliation"]["samples"][0]["eff_from"] == 4
    ok &= rep["totals"]["machine_binding"] == 3 and rep["totals"]["author_binding"] == 1
    ok &= rep["totals"]["undeclared_tier_with_machine_binding"] == 2   # affiliation·mood
    ok &= [t.model_dump() for t in st.runtime_timeline] == before      # 수정 0(advisory)
    # 결정론: 같은 입력 두 번 = 같은 산출(정렬 포함)
    ok &= json.dumps(rep, ensure_ascii=False) == json.dumps(machine_binding_report(st), ensure_ascii=False)
    print(f"[{'OK' if ok else 'FAIL'}] ⑦ 마이그레이션 리포트 — 계수·표본·선언 상태 정확·수정 0·결정론")
    assert ok


def test_readiness_exposes_tier_report(tmp_path):
    with _Env(tmp_path) as env:
        st = _seed(env.svc)
        st.runtime_timeline = [TimelineEntry(entity_id="hero", attr="truth_awareness", value="흠칫",
                                             eff_from=3, reason="2화 동적 감지", trust_tier="ground_truth")]
        env.svc.repo.save(st)
        rep = env.client.get("/api/projects/xr5/readiness").json()
        ok = "tier_report" in rep and {"level", "signals", "flags"} <= set(rep)
        by = {a["attr"]: a for a in rep["tier_report"]["attributes"]}
        ok &= by["truth_awareness"]["machine_binding"] == 1
        ok &= rep["tier_report"]["totals"]["machine_binding"] == 1
        print(f"[{'OK' if ok else 'FAIL'}] ⑦b GET /readiness 참고 키 tier_report(additive·게이트 아님)")
        assert ok


# ═════════════════ ⑧ PATCH 오버라이드 ═════════════════

def test_patch_override_validates_persists_and_applies(tmp_path):
    with _Env(tmp_path) as env:
        _seed(env.svc)
        c = env.client
        ok = c.patch("/api/projects/xr5/attributes/truth_awareness",
                     json={"auto_commit": "sometimes"}).status_code == 400      # 3값 밖
        ok &= c.patch("/api/projects/xr5/attributes/nope", json={"auto_commit": ""}).status_code == 400
        ok &= c.patch("/api/projects/nada/attributes/truth_awareness",
                      json={"auto_commit": ""}).status_code == 404
        r = c.patch("/api/projects/xr5/attributes/truth_awareness", json={"auto_commit": "non_binding"})
        ok &= r.status_code == 200 and r.json()["auto_commit"] == "non_binding"
        st = env.svc.repo.get("xr5")
        ok &= {a.key: a.auto_commit for a in st.world.attributes}["truth_awareness"] == "non_binding"
        # 이후 커밋 반영 — 저장된 선언으로 세션을 다시 세워 updater 를 태운다
        sess, st2 = env.svc.get_session("xr5")
        upd = OntologyUpdater(None, sess.bundle.vocab, _Bus())
        _, _, tl, _ = upd.apply(_sc("hero", "truth_awareness", "직시"), sess.bundle.ontology, 4)
        ok &= _tier_of(tl, "truth_awareness") == "narrative_inferred"
        # 되돌리기(""=미선언)도 되고, 소급 강등은 없다(기존 엔트리 tier 불변)
        ok &= c.patch("/api/projects/xr5/attributes/truth_awareness",
                      json={"auto_commit": ""}).json()["auto_commit"] == ""
        print(f"[{'OK' if ok else 'FAIL'}] ⑧ PATCH 오버라이드 — 값 검증·저장·이후 커밋 반영·해제")
        assert ok


def test_patch_no_retroactive_downgrade(tmp_path):
    with _Env(tmp_path) as env:
        st = _seed(env.svc)
        st.runtime_timeline = [TimelineEntry(entity_id="hero", attr="truth_awareness", value="흠칫",
                                             eff_from=3, reason="2화 동적 감지", trust_tier="ground_truth")]
        env.svc.repo.save(st)
        env.client.patch("/api/projects/xr5/attributes/truth_awareness", json={"auto_commit": "non_binding"})
        after = env.svc.repo.get("xr5")
        ok = [t.trust_tier for t in after.runtime_timeline] == ["ground_truth"]   # 소급 강등 0
        rep = machine_binding_report(after)
        by = {a["attr"]: a for a in rep["attributes"]}
        ok &= by["truth_awareness"]["machine_binding"] == 1                       # 잔량이 리포트에 남아 보인다
        print(f"[{'OK' if ok else 'FAIL'}] ⑧b 소급 강등 없음 — 기존 엔트리 불변, 잔량은 리포트로 가시화")
        assert ok


# ═════════════════ ⑨ worldgen 적재(보존 파싱) ═════════════════

def test_worldgen_json_roundtrip_preserves_auto_commit():
    payload = {
        "title": "적재 시험", "genre": "현대", "tone": "건조", "premise": "p", "synopsis": "s",
        "attributes": [
            {"key": "carrying", "label": "소지품", "kind": "categorical",
             "vocab": ["부적", "없음"], "mutable": True, "auto_commit": "binding"},
            {"key": "truth_awareness", "label": "진실 자각", "kind": "state",
             "states": ["외면", "직시"], "mutable": True, "auto_commit": "non_binding"},
            {"key": "legacy", "label": "구축", "kind": "numeric"},
        ],
        "entities": [{"id": "hero", "name": "서준호"}],
    }
    w = WorldConfig.model_validate(payload)
    got = {a.key: a.auto_commit for a in w.attributes}
    ok = got == {"carrying": "binding", "truth_awareness": "non_binding", "legacy": ""}
    # 저장→로드 라운드트립에서도 보존(작품 JSON 영속 경로)
    w2 = WorldConfig.model_validate(json.loads(w.model_dump_json()))
    ok &= {a.key: a.auto_commit for a in w2.attributes} == got
    # 그 선언이 실제 커밋 티어로 이어진다(적재 → 정책까지 한 줄)
    ont = Ontology(Vocabulary(w.attributes))
    ont.add(Entity(id="hero", name="서준호", etype="character",
                   attrs={"carrying": "없음", "truth_awareness": "외면"}))
    _, _, tl, _ = _updater(w.attributes).apply(_sc("hero", "carrying", "부적"), ont, 2)
    ok &= _tier_of(tl, "carrying") == "ground_truth"
    _, _, tl2, _ = _updater(w.attributes).apply(_sc("hero", "truth_awareness", "직시"), ont, 2)
    ok &= _tier_of(tl2, "truth_awareness") == "narrative_inferred"
    print(f"[{'OK' if ok else 'FAIL'}] ⑨ worldgen 적재 — auto_commit 보존 파싱·라운드트립·커밋 티어 연결")
    assert ok


# ═════════════════ ⑩ K1 — 이름 매칭 0 ═════════════════

def test_k1_no_attribute_name_dictionary_in_updater():
    """티어 판별이 속성명 사전·정규식·장르 라벨을 요구하는 순간 그 설계는 폐기(헌법 2·3조).

    updater 의 XR-5 분기가 읽는 것은 spec.auto_commit 선언뿐이어야 한다. 기존 하드코딩 잔재
    (attr == "status" 하위호환 분기)는 XR-5 이전부터 있던 생애주기 기본값 경로라 셈에서 제외하고,
    **auto_commit 을 읽는 줄 주변에 이름 리터럴이 없음**을 직접 본다."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "novelcopilot" / "engine" / "ontology_updater.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    hits = [ln for ln in lines if 'getattr(spec, "auto_commit"' in ln]   # 실행 라인만(주석·독스트링 제외)
    ok = len(hits) == 2                                             # state 분기 1 · mutable 분기 1
    for ln in hits:
        ok &= '"non_binding"' in ln                                 # 비교 대상은 선언값뿐
        ok &= not re.search(r'attr\s*==\s*"', ln)                    # 속성명 비교 0
        ok &= not re.search(r"re\.(search|match|fullmatch|compile)", ln)   # 정규식 0
        ok &= not re.search(r"\bin\s*[\[{(]", ln)                    # 속성명 목록 멤버십 0
    # 파일 전체의 속성명 리터럴 비교는 XR-5 이전부터 있던 생애주기 기본값(status) 하위호환 경로뿐이어야 한다
    ok &= set(re.findall(r'attr\s*==\s*"([^"]+)"', src)) == {"status"}
    # 장르 라벨·트로프 어휘 0
    ok &= not re.search(r"(무협|판타지|로맨스|romance|fantasy|genre_label)", src)
    print(f"[{'OK' if ok else 'FAIL'}] ⑩ K1 — updater 티어 판별에 속성명 사전·정규식·장르 라벨 0")
    assert ok


def main() -> int:
    import tempfile
    plain = [test_backcompat_old_json_and_default_tier,
             test_non_binding_mutable_not_injected_but_visible,
             test_non_binding_state_reversible_transition,
             test_binding_declaration_cannot_unlock_safeguards,
             test_ordered_backward_and_non_binding_merge,
             test_machine_binding_report,
             test_worldgen_json_roundtrip_preserves_auto_commit,
             test_k1_no_attribute_name_dictionary_in_updater]
    tmpd = [test_author_promotion_via_set_entity_state,
            test_readiness_exposes_tier_report,
            test_patch_override_validates_persists_and_applies,
            test_patch_no_retroactive_downgrade]
    for fn in plain:
        fn()
    for fn in tmpd:
        with tempfile.TemporaryDirectory() as td:
            fn(pathlib.Path(td))
    print("\nXR-5(상태 자동 승격 클래스별 티어링) 검증: ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
