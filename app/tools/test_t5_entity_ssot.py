# -*- coding: utf-8 -*-
"""T5 엔티티 SSOT — R1 등록명 표기고정(드리프트 소스차단)·R2 게이트 정합(커스텀 actor admit·race 하위호환)·R4 인원수 nudge. 실 LLM 0콜.

정직 범위: A(등록명 드리프트)=소스차단 교정. B(집단 인원수 결정론 추적)=whack-a-mole 불가→CN-2 advisory nudge(R4).
C(메타주석)=A+B 파생·기존 예방 커버. 임의 고유명 NER=한글 대문자 부재로 결정론 불가→propose LLM+작가."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace
from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.engine.ontology import Ontology, Entity
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.domain.world import EntityTypeSpec, BUILTIN_ENTITY_TYPES
from novelcopilot.engine.ontology_updater import OntologyUpdater
from novelcopilot.engine.claim_audit import audit_chapter
from novelcopilot.domain.types import RetrievedItem


def _ent(name, prov):
    return SimpleNamespace(name=name, provisional=prov)


# ---- R1: 등록명 표기고정 앵커(tier 무관)·떡밥 개방 유지·드리프트 문구 제거 ----
def test_r1_confirmed_and_prov_both_get_spelling_anchor():
    cards = ChapterGenerator._name_anchor_cards([_ent("레오", False), _ent("김동수", True)])
    assert "레오" in cards and "김동수" in cards
    # 확정: '그대로 쓰라', 미확정: '표기를 그대로 유지'(둘 다 표기고정 앵커 — 드리프트 소스차단)
    assert "그대로 쓰라" in cards and "표기를 그대로 유지" in cards


def test_r1_prov_keeps_mystery_openness_clause():
    # 비판 요구: '새 캐논 단정 금지' 절이 반드시 남아야(떡밥 조기 확정 방지)
    cards = ChapterGenerator._name_anchor_cards([_ent("정체불명자", True)])
    assert "새 배경·정체를 단정하거나 강제로 전개하지는 마라" in cards
    assert "새 이름을 발명하지 마라" in cards


def test_r1_drift_inducing_phrase_removed():
    # 제거된 드리프트 유발 문구('반복 호명하지 마라')가 더는 나오지 않아야
    cards = ChapterGenerator._name_anchor_cards([_ent("김동수", True)])
    assert "반복 호명" not in cards


def test_r1_empty_and_confirmed_only():
    assert ChapterGenerator._name_anchor_cards([]) == ""
    only_conf = ChapterGenerator._name_anchor_cards([_ent("레오", False)])
    assert "레오" in only_conf and "미확정 명사" not in only_conf


# ---- RR-1: 프로즈 명부 노출 차단(데뷔 전·미캐스트 인물 제외) ----
def _rrent(eid, name, introduced=False, provisional=False):
    return SimpleNamespace(id=eid, name=name, introduced=introduced, provisional=provisional)


def test_rr1_prose_roster_excludes_unintroduced_noncast():
    """데뷔 전(introduced=False)이고 이번 화 비트 캐스트(involved)에도 없는 인물은 프로즈 명부에서 제외."""
    ents = [_rrent("a", "서준호", introduced=True),        # 이미 등장 → 유지
            _rrent("b", "하지연", introduced=False),        # 데뷔 전·미캐스트 → 제외
            _rrent("c", "강도윤", introduced=False)]        # 이번 화 캐스트 → 유지(데뷔 화)
    cards = ChapterGenerator._name_anchor_cards(ents, involved=["a", "c"])
    assert "서준호" in cards and "강도윤" in cards          # 등장했거나 이번 화 캐스트
    assert "하지연" not in cards                            # 데뷔 전 + 미캐스트 = 유출 차단


def test_rr1_provisional_treated_as_introduced():
    """provisional(본문에 이미 등장·자동 커밋)은 introduced 취급 — involved 밖이어도 유지(표기 고정 필요)."""
    ents = [_rrent("x", "동적조연", introduced=False, provisional=True)]
    cards = ChapterGenerator._name_anchor_cards(ents, involved=["other"])
    assert "동적조연" in cards


def test_rr1_no_involved_is_noop():
    """involved 미제공(None)이면 필터 무동작 — 하위호환·기존 호출 바이트 동일."""
    ents = [_rrent("b", "하지연", introduced=False)]
    assert "하지연" in ChapterGenerator._name_anchor_cards(ents)   # 필터 안 걸림(구 동작)


# ---- R2: 등록 게이트를 소비 게이트(is_actor category)와 정합 ----
class _Bus:
    def __init__(self): self.events = []
    def emit(self, *a, **k): self.events.append((a, k))


def _onto_with_types(extra_types=None):
    o = Ontology(Vocabulary([]))
    types = list(BUILTIN_ENTITY_TYPES) + list(extra_types or [])
    o.entity_types = {t.key: t for t in types}
    return o


def _apply(onto, new_entities):
    upd = OntologyUpdater(None, Vocabulary([]), _Bus())
    changes, new_specs, _, _ = upd.apply({"new_entities": new_entities}, onto, 3)
    return {s.name for s in new_specs}


def test_r2_custom_actor_type_registers():
    # 장르 선언 커스텀 actor 타입(괴수) — 전엔 STRUCTURAL 영어 리터럴 밖이라 무관계 시 prop_skip. 이제 category=actor 로 admit
    onto = _onto_with_types([EntityTypeSpec(key="괴수", label="괴수", category="actor")])
    assert "심연포식자" in _apply(onto, [{"name": "심연포식자", "etype": "괴수"}])


def test_r2_race_literal_backward_compat():
    # 'race'는 builtin 카탈로그 키가 아니나 하위호환 리터럴로 유지돼야(회귀 방지)
    onto = _onto_with_types()
    assert "고대엘프족" in _apply(onto, [{"name": "고대엘프족", "etype": "race"}])


def test_r2_prop_excluded_but_rel_connected_registers():
    onto = _onto_with_types()
    # 소품(item, category=object)·무관계 → prop_skip(노드 미생성)
    assert _apply(onto, [{"name": "녹슨단검", "etype": "item"}]) == set()
    # 그러나 관계에 연결되면 등록(기존 rel_names 경로 유지)
    upd = OntologyUpdater(None, Vocabulary([]), _Bus())
    _, specs, _, _ = upd.apply(
        {"new_entities": [{"name": "성검", "etype": "item"}],
         "relations": [{"src": "성검", "dst": "성검", "rel_id": "x"}]}, onto, 3)
    assert "성검" in {s.name for s in specs}


# ---- R4: claim_audit 에 집단 인원수 recall nudge(비추적·기존 스코프 내 예시) ----
class _CapProv:
    def __init__(self): self.msg = None
    def chat_json(self, m, temperature=0.0):
        self.msg = m
        return {"contradictions": []}


class _Rag:
    def search(self, q, as_of, k=4):
        return [RetrievedItem(source="rag_chunk", ref="2", text="생존자는 이제 셋뿐이었다는 서술이 있었다")]


def test_r4_headcount_nudge_in_prompt():
    p = _CapProv()
    audit_chapter(p, _Rag(), "다섯이 둘러앉아 회의를 시작했다.", 5)
    sysmsg = p.msg[0]["content"]
    assert "인원수" in sysmsg and "생존자 수" in sysmsg          # recall nudge 주입 확인


# ---- R3: 작가 직접 추가 엔티티 = confirmed(provisional=False) — 실 add_entity 경로(fake provider) ----
def test_r3_author_added_entity_is_confirmed():
    import tempfile, pathlib
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import session as _S
    from novelcopilot.services.copilot import CopilotService
    from novelcopilot.domain.project import ProjectState, ProjectSeed
    from novelcopilot.domain.world import WorldConfig, EntitySpec as _ES

    class _FakeProv:
        def embed(self, texts): return [[0.0] * 8 for _ in texts]
        def chat_json(self, m, temperature=0.0): return {}
        class usage:
            chat_tokens = 0
    _orig = _S.create_provider
    _S.create_provider = lambda settings: _FakeProv()
    try:
        repo = FilesystemProjectRepository(pathlib.Path(tempfile.mkdtemp()))
        w = WorldConfig(title="t", synopsis="s", entities=[_ES(id="hero", name="레오")])
        repo.save(ProjectState(id="p1", seed=ProjectSeed(title="t"), world=w))
        svc = CopilotService(get_settings(), repo)
        assert svc.add_entity("p1", "김동수")["created"] is True
        added = [e for e in repo.get("p1").runtime_entities if e.name == "김동수"]
        assert added and added[0].provisional is False       # 작가 추가 → 확정 슬롯(promote 약속 이행)
    finally:
        _S.create_provider = _orig
