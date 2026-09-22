# -*- coding: utf-8 -*-
"""CX-1 생성 컨텍스트 개편 — 결정론 잠금 테스트(LLM 0콜). 설계 SSOT docs/design-cx1-gen-context.md §5.

잠금 4축: ①노출 스캔(internal 속성·internal 규칙·위키 '비밀:' 줄이 생성 입력 경로에 0건)
②하위호환(exposure 미설정 = 기존 동작·프롬프트 재료 동일) ③채널 일관(같은 속성이 push/lookup
두 값으로 동시에 나가지 않음 — binding 우선 단일값) ④위키 화자 결속(1인칭 로스터 포함·3인칭 무변화).
+ CX-3(인물 push 이관) · CX-5(직전 상세 제외) · CX-7(스타일 SSOT 폴백) 계약."""
import sys, json
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.domain.world import (WorldConfig, EntitySpec, AttributeSpec, WorldRuleSpec,
                                       TimelineEntry, StyleSpec, DEFAULT_STYLE_RULES)
from novelcopilot.engine.vocabulary import Vocabulary
from novelcopilot.engine.factory import build_ontology
from novelcopilot.engine.lookup import CanonLookup
from novelcopilot.engine.prompts import render_style
from novelcopilot.llm.base import LLMProvider


def _world():
    return WorldConfig(
        title="[실험] CX 잠금", genre="현판", tone="건조", premise="t.",
        attributes=[
            AttributeSpec(key="money_won", label="소지금(원)", kind="numeric"),
            AttributeSpec(key="truth_awareness", label="정체자각", kind="categorical",
                          vocab=["외면", "흠칫"], exposure="internal"),
            AttributeSpec(key="insight", label="괴담통찰", kind="numeric", exposure="internal"),
        ],
        entities=[
            EntitySpec(id="hero", name="주인공", etype="character", voice="능청 요체로 말한다.",
                       attrs={"money_won": None, "truth_awareness": None, "insight": None}),
            EntitySpec(id="const", name="도시 상수", etype="worldrule", attrs={"money_won": None}),
        ],
        world_rules=[
            WorldRuleSpec(rule_id="pub", text="공개 규칙이다."),
            WorldRuleSpec(rule_id="twist", text="최종 반전은 주인공이 본체라는 것이다.", exposure="internal"),
        ],
        timeline=[
            TimelineEntry(entity_id="hero", attr="money_won", value=12000, eff_from=1),
            TimelineEntry(entity_id="hero", attr="truth_awareness", value="흠칫", eff_from=1),
            TimelineEntry(entity_id="hero", attr="insight", value=2, eff_from=1),
            TimelineEntry(entity_id="const", attr="money_won", value=3000, eff_from=1),
        ])


def _ont(world=None):
    w = world or _world()
    return build_ontology(w, Vocabulary.from_world(w))


# ── ① 노출 스캔 ──
def test_exposure_scan_internal_zero():
    o = _ont()
    facts = " / ".join(f"{f.entity}:{f.attr_label}={f.value}" for f in o.canon_facts(["hero", "const"], 5))
    assert "정체자각" not in facts and "괴담통찰" not in facts and "흠칫" not in facts
    assert "소지금(원)=12000" in facts                       # public 은 그대로
    rules = " ".join(o.rules)
    assert "최종 반전" not in rules and "공개 규칙이다." in rules   # internal 규칙 주입 제외
    lk = CanonLookup(o, [], 5)
    out = lk.handle("lookup_canon", {"query": "주인공"})
    assert "정체자각" not in out and "괴담통찰" not in out
    assert "소지금(원)=12000" in out


# ── ② 하위호환(미설정=public 기본) ──
def test_backcompat_unmarked_world_identical():
    w = _world()
    for a in w.attributes:
        a.exposure = "public"                                # 전부 미설정과 동일 취급
    for r in w.world_rules:
        r.exposure = "public"
    o = build_ontology(w, Vocabulary.from_world(w))
    facts = " / ".join(f"{f.attr_label}={f.value}" for f in o.canon_facts(["hero"], 5))
    assert "정체자각=흠칫" in facts and "괴담통찰=2" in facts and "소지금(원)=12000" in facts
    assert any("최종 반전" in r for r in o.rules)            # public 이면 종전대로 주입


# ── ③ 채널 일관(binding 우선 단일값) ──
def test_channel_consistency_binding_wins():
    o = _ont()
    # 비구속(narrative_inferred) 값을 늦은 회차에 얹어도 binding 이 있으면 그 값만 나간다
    o.set_state("hero", "money_won", 99999, 3, trust_tier="narrative_inferred")
    vals = {a: (v, b) for a, v, b in o.public_attrs("hero", 5)}
    assert vals["money_won"][0] == 12000 and vals["money_won"][1] is True
    lk = CanonLookup(o, [], 5)
    out = lk.handle("lookup_canon", {"query": "주인공"})
    assert "12000" in out and "99999" not in out and "비구속" not in out


# ── ③b 조회에 보이스 동봉(CX-3) ──
def test_lookup_carries_voice():
    lk = CanonLookup(_ont(), [], 5)
    out = lk.handle("lookup_canon", {"query": "주인공"})
    # VL-1: 조회 보이스 필드에 참조 전용 라벨(데이터 축소 0 — 값은 그대로 실린다)
    assert "보이스(참조 전용. 카드에 적힌 대로, 이번 장면에 해당하는 대목에서 말과 행동으로 드러낸다)=능청 요체로 말한다." in out   # VH-1 라벨


# ── CX-3 인물 push 이관 ──
def test_canon_facts_actors_status_only():
    o = _ont()
    facts = o.canon_facts(["hero", "const"], 5, actors_status_only=True)
    txt = " / ".join(f"{f.entity}:{f.attr_label}={f.value}" for f in facts)
    assert "주인공" not in txt                                # 인물 속성 push 0(생사 중대 상태 없음)
    assert "도시 상수" in txt and "3000" in txt               # 비행동주체(세계 상수)는 계속 push


# ── ④ 위키 화자 결속 ──
class _WikiProv(LLMProvider):
    def __init__(self):
        super().__init__()
        self.captured = []
    def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
        self.captured.append(messages)
        return json.dumps({"pages": [{"id": "hero", "body": "상태: t"}]}, ensure_ascii=False)
    def embed(self, texts):
        return [[0.0] for _ in texts]


def test_wiki_pov_binding():
    from novelcopilot.engine.wiki import Wiki
    o = _ont()
    p = _WikiProv()
    w = Wiki(p)
    # 1인칭: 본문에 화자 이름이 없어도 로스터에 강제 포함 + 프롬프트에 서술자 명시
    n = w.ingest_chapter(3, "나는 골목을 걸었다.", o, pov_entity_id="hero")
    assert n == 1
    sysmsg = p.captured[-1][0]["content"]
    assert "서술자('나')는 주인공" in sysmsg
    # 3인칭(미전달): 화자 명시 0 — 이름 없는 본문이면 noop(기존 동작)
    p2 = _WikiProv()
    w2 = Wiki(p2)
    n2 = w2.ingest_chapter(4, "아무 인물 이름 없는 본문.", o)
    assert n2 == 0 and not p2.captured                        # 콜 자체가 없음(바이트 동일 이상)


def test_wiki_retrieve_strips_secret_lines():
    from novelcopilot.engine.wiki import Wiki
    from novelcopilot.domain.types import WikiPage, WikiLifecycle
    p = _WikiProv()
    w = Wiki(p)
    w.seed_page(WikiPage(page_id="hero", page_type="character", lifecycle=WikiLifecycle.ACTIVE,
                         body="상태: 무사\n비밀: 반전의 재료다\n목표: 생존", as_of_narrative_order=1))
    items = w.retrieve("주인공", 5, k=1)
    assert items and "비밀" not in items[0].text and "상태: 무사" in items[0].text
    assert "비밀" in w.pages["hero"].body                     # 저장은 원형 유지


# ── CX-5 직전 상세 제외 ──
def test_story_so_far_excludes_last_detail():
    from novelcopilot.services.copilot import _build_story_so_far_hier
    from novelcopilot.domain.types import ChapterStatus
    chs = [NS(chapter=i, status=ChapterStatus.FINALIZED, episode_id="ep1",
              summary=f"{i}화 요약", detail_synopsis="", text="본문") for i in (1, 2, 3)]
    state = NS(world=NS(spine=NS(arcs=[])), narrative_progress=NS(current_episode_id="ep1"),
               chapters=chs)
    full, _ = _build_story_so_far_hier(state, 4, 10_000)
    cut, _ = _build_story_so_far_hier(state, 4, 10_000, exclude_last_detail=True)
    assert "3화 요약" in full
    assert "3화 요약" not in cut and "2화 요약" in cut         # 직전만 빠지고 그 앞은 유지


# ── CX-7 스타일 SSOT 폴백 ──
def test_style_rules_ssot_fallback():
    empty = StyleSpec()                                       # 신규 작품: rules=[]
    assert empty.rules == []
    rendered = render_style(empty)
    assert DEFAULT_STYLE_RULES[0][:20] in rendered            # 코드 상수 폴백
    custom = StyleSpec(rules=["오직 이 규칙만."])
    assert "오직 이 규칙만." in render_style(custom)          # 작품 값 우선(무회귀)
    assert DEFAULT_STYLE_RULES[0][:20] not in render_style(custom)


# ── EM-2 엔드투엔드 잠금: 생성 콜(초안) 프롬프트에 em dash 시연 0 ──
def test_no_emdash_in_draft_prompt():
    """EM-1(acda24a)의 소스 차단이 이후 편집으로 재유입되던 결함의 재발 방지 — 지시문·헤더·카드가
    — 를 시연하면 본문 대시가 증식한다(1→7건/화 실측). 픽스처 데이터는 대시 0 → 캡처된 프롬프트의
    — 는 전부 코드 주입분이므로 0건을 단언한다(개별 문자열 나열 잠금이 아니라 조립 결과 전수 잠금)."""
    from novelcopilot.engine.factory import build_engine
    from novelcopilot.config import get_settings

    class _Cap(LLMProvider):
        def __init__(self):
            super().__init__()
            self.captured = []
        def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
            self.captured.append(messages)
            return "본문 문장이 이어진다. " * 120
        def chat_json(self, messages, *, temperature=0.0, max_tokens=None):
            return {}
        def embed(self, texts):
            return [[0.0] for _ in texts]

    w = _world()
    w.style = StyleSpec(pov="first", narrator_voice="겁이 많은데 손이 먼저 움직이는 화자다.")
    prov = _Cap()
    settings = get_settings().model_copy(update={
        "gen_tools": False, "narrator_voice": False, "humanize": False,
        "style_repair": False, "reader_desk": False, "claim_audit": False})
    b = build_engine(w, prov, settings)
    beat = {"title": "t", "summary": "s", "key_events": ["사건 하나"], "entities": ["hero"]}
    try:
        b.generator.generate(3, beat, b.ontology, b.rag, b.wiki)
    except Exception:
        pass                                                   # 후반 스테이지 실패 무관 — 초안 콜 캡처만 필요
    assert prov.captured, "초안 콜 미캡처"
    for msgs in prov.captured[:2]:                             # 초안+이어쓰기 첫 콜
        blob = "\n".join(m.get("content", "") for m in msgs if isinstance(m.get("content"), str))
        assert "—" not in blob, "생성 프롬프트에 em dash 시연 잔존: " + \
            next(ln for ln in blob.splitlines() if "—" in ln)[:80]


# ── CX-6ⓐ 소스 재유입 방지(표현 절제 draft 주입 금지) ──
def test_no_restraint_injection_in_source():
    src = (Path(__file__).resolve().parents[1] / "novelcopilot" / "engine" / "harness.py").read_text(encoding="utf-8")
    assert "[표현 절제 — 이 작품에서" not in src              # 과용 표현 목록의 프롬프트 주입 헤더 재도입 차단(주석 언급은 허용)
    assert "시그니처 어미·문구가 있다면" not in src           # CX-6ⓒ 렌더 잔재 재도입 차단
