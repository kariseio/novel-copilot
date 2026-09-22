# -*- coding: utf-8 -*-
"""DG-1/DG-4 — 인물별 대사 원장 잠금(LLM 0콜). 계약: 결정론 파싱·스토리 지칭·캐논 정합·비차단·생성 주입 0.

잠그는 계약:
① 결정론 파싱 — 따옴표 행 순서 보존·지문 무시·빈 본문 [].
② 표기 콜 계약(DG-4) — 입력에 이름 명부 0(메뉴 프라이밍 소스 차단)·대사 수=표기 수 강제·결측='미상'.
③ 캐논 정합(DG-4·결정론) — '서술자'→POV 개체 승격·본문 등장 지칭만 캐논 승격·본문 미등장 이름 승격 거부
   (모델 prior 누출 차단)·그 외 지칭 보존(canon=False).
④ 어체 분류·화자 프로필 — 결정론 집계(판정 라벨·임계 0 — 오탐 가능 원자료).
⑤ 구 JSON 하위호환 — dialogue_ledger 필드 없는 레코드 로드 기본 []·canon 필드 없는 구 원장 행 무해.
⑥ harness 경로 안전(PM 필수 보정 ①) — ESCALATED(빈 본문)·flag off 에서 확정이 죽지 않고 원장 [],
   build_ledger 미호출. FINALIZED+ON 은 aux provider·pov_entity_id 로 1회 호출·rec 에 영속.
⑦ 주입 0 구조 잠금(PM 수용 기준 ⑵ + DG-6 카브아웃) — prompts·rag·rerender 참조 0 절대 유지,
   harness 조립부는 행동 잠금(발췌=라벨 미동봉·프레임 제외 전량이 본문 부분 문자열)으로 교체.
⑧ 검증 축(PM 필수 보정 ③) — build_verification.dialogue_ledger: 원장 재집계(LLM 0)·원장 없으면 MISSING.

실행: PYTHONPATH=app py -3.12 -m pytest tools/test_dg1_dialogue_ledger.py -q
"""
import sys, json
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.engine.dialogue_ledger import (extract_quotes, attribute_speakers,
                                                resolve_speakers, build_ledger, ending_class,
                                                speaker_profile, UNKNOWN, NARRATOR)
from novelcopilot.llm.base import LLMProvider

_TEXT = ('나는 문을 밀었다.\n\n"안 뗍니다. 가만 계세요."\n\n하지연이 나를 봤다.\n\n'
         '"제 손입니다."\n\n“…그러네요.”\n\n집주인은 몸을 돌렸다.')


def test_extract_quotes_deterministic():
    q = extract_quotes(_TEXT)
    assert q == ["안 뗍니다. 가만 계세요.", "제 손입니다.", "…그러네요."]
    assert extract_quotes("") == [] and extract_quotes("지문뿐인 본문이다.") == []


class _Map(LLMProvider):
    def __init__(self, mapping):
        super().__init__()
        self._m = mapping
        self.last_messages = None
    def chat(self, messages, *, temperature=0.7, max_tokens=None, json_mode=False):
        self.last_messages = messages
        return json.dumps({"speakers": self._m}, ensure_ascii=False)
    def embed(self, texts):
        return [[0.0] for _ in texts]


def _ont():
    ents = {"j": NS(name="서준호", etype="character", aliases=[]),
            "y": NS(name="하지연", etype="character", aliases=[]),
            "g": NS(name="정문규", etype="character", aliases=[]),
            "c": NS(name="도시 상수", etype="worldrule", aliases=[])}
    return NS(entities=ents, is_actor=lambda t: t == "character")


def test_attribution_no_roster_and_count_contract():
    """② DG-4: 콜 입력에 온톨로지 이름 명부 0(프라이밍 소스 차단) + 대사 수=표기 수·결측 미상."""
    q = extract_quotes(_TEXT)
    p = _Map({"1": "서술자", "3": "집주인"})       # 2번 결측
    out = attribute_speakers(p, _TEXT, q)
    assert out == ["서술자", UNKNOWN, "집주인"]      # 결측 번호=미상·순서 보존
    sent = json.dumps(p.last_messages, ensure_ascii=False)
    assert "명부" not in sent                        # 명부 섹션 자체가 없다
    for name in ("서준호", "정문규"):                # 본문 밖 캐논 이름이 입력에 노출되지 않는다
        assert name not in sent


def test_resolve_canon_promotion_rules():
    """③ DG-4 정합: 서술자→POV 승격·본문 등장 지칭만 승격·본문 미등장 이름 승격 거부·지칭 보존."""
    ont = _ont()
    out = resolve_speakers(["서술자", "하지연", "집주인", "정문규", ""], _TEXT, ont, pov_entity_id="j")
    assert out[0] == ("서준호", True)                # 서술자 → POV 개체(캐논)
    assert out[1] == ("하지연", True)                # 본문 등장('하지연이') + actor 일치 → 승격
    assert out[2] == ("집주인", False)               # 무명 지칭 보존(발명 승격 없음)
    assert out[3] == ("정문규", False)               # 명부엔 있으나 본문 미등장 → 승격 거부(prior 누출 차단)
    assert out[4] == (UNKNOWN, False)                # 빈 표기=미상
    # POV 미지정이면 '서술자' 보존(비캐논 정직)
    assert resolve_speakers([NARRATOR], _TEXT, ont)[0] == (NARRATOR, False)


def test_resolve_alias_scope_name_fragments_only():
    """③ 별칭 승격 축소(PM ⑥ '오탐 1건=조건 축소' 이행): 이름-조각 별칭만 승격·역할 별칭 제외.
    실측 근거: 1화 원세계 잡화상 영감(무명 별인)이 헐값의 역할 별칭 '잡화상 영감'으로 오승격."""
    ents = {"y": NS(name="하지연", etype="character", aliases=["지연", "관리국 요원"]),
            "h": NS(name="헐값", etype="character", aliases=["잡화상 영감", "정보상"])}
    ont = NS(entities=ents, is_actor=lambda t: t == "character")
    text = "지연이 웃었다. 잡화상 영감은 관리국 요원 옆에서 정보상 노릇을 했다."
    out = resolve_speakers(["지연", "잡화상 영감", "관리국 요원", "정보상"], text, ont)
    assert out[0] == ("하지연", True)                # 이름 조각('지연'⊂'하지연') → 승격
    assert out[1] == ("잡화상 영감", False)          # 역할 별칭 — 본문 등장이어도 승격 금지(교차 정체성)
    assert out[2] == ("관리국 요원", False)
    assert out[3] == ("정보상", False)


def test_resolve_sentinel_and_hygiene_caps():
    """③ PM 보정 ④⑤: sentinel 동명 개체 승격 금지 + 개행·40자 초과 표기 미상 강등."""
    ents = {"x": NS(name="미상", etype="character", aliases=[]),
            "n": NS(name="서술자", etype="character", aliases=[])}
    ont = NS(entities=ents, is_actor=lambda t: t == "character")
    text = "미상은 서술자를 봤다."                          # 동명 개체가 본문에 등장해도
    out = resolve_speakers(["미상", "서술자"], text, ont)
    assert out[0] == (UNKNOWN, False)                       # '미상' 승격 금지(정직 결측 유지)
    assert out[1] == (NARRATOR, False)                      # '서술자' 는 POV 배선으로만 승격
    # 형식 위생: 개행 포함·40자 초과 표기(스키마 오염) → 미상
    long_d = "이 대사는 아마도 상황상 잡화상 영감이 말한 것으로 보이는데 확실하지는 않다"
    out2 = resolve_speakers(["집주인\n설명", long_d], _TEXT, _ont())
    assert out2 == [(UNKNOWN, False), (UNKNOWN, False)]


def test_build_ledger_nonblocking_and_rows():
    led = build_ledger(_Map({"1": "서술자", "2": "하지연", "3": "하지연"}), _TEXT, _ont(),
                       pov_entity_id="j")
    assert [(r["speaker"], r["canon"]) for r in led] == [("서준호", True), ("하지연", True), ("하지연", True)]
    assert led[0]["idx"] == 1 and led[0]["text"].startswith("안 뗍니다")

    class _Boom(LLMProvider):
        def chat(self, *a, **k):
            raise RuntimeError("x")
        def embed(self, t):
            return [[0.0] for _ in t]
    assert build_ledger(_Boom(), _TEXT, _ont()) == []      # 실패=빈 원장(비차단)


def test_ending_class_and_profile():
    assert ending_class("정산 말씀드립니다.") == "합쇼체"
    assert ending_class("좌표 읊어보시오.") == "하오체"
    assert ending_class("내려가도 돼요?") == "해요체"
    assert ending_class("뭐.") == "반말/기타"
    prof = speaker_profile([{"speaker": "A", "text": "갑니다."}, {"speaker": "A", "text": "가요."}])
    assert prof["A"]["합쇼체"] == 1 and prof["A"]["해요체"] == 1
    # ⑤ canon 필드 없는 구 원장 행도 프로필 집계 무해(하위호환)
    assert speaker_profile([{"speaker": "B", "text": "간다."}])["B"]["반말/기타"] == 1


def test_record_field_backcompat():
    from novelcopilot.domain.types import ChapterRecord, ChapterStatus
    r = ChapterRecord(chapter=1, title="t", status=ChapterStatus.FINALIZED, text="x")
    assert r.dialogue_ledger == []                          # 기본 [] — 구 데이터 로드 하위호환


# ═════════ ⑥ harness 경로 안전(ESCALATED·flag off·FINALIZED 배선) ═════════

class _Bus:
    def __init__(self):
        self.events = []
    def emit(self, node, event, **payload):
        self.events.append({"node": node, "event": event, **payload})


class _Cap:
    """harness 관통용 fake — chat=지정 본문, chat_json={}(기계 스테이지 전부 무해 통과)."""
    def __init__(self, body="본문."):
        self.body = body
        self.last_truncated = False
        self.usage = NS(chat_tokens=0, chat_calls=0)
    def chat(self, messages, *a, **k):
        return self.body
    def chat_json(self, messages, *a, **k):
        return {}
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


class _HOnt:
    entities = {}
    rules = []
    def is_actor(self, et): return False
    def canon_facts(self, ids, ch): return []
    def canon_relations(self, ids, ch): return []
    def scan_present_ids(self, text): return []
class _HChk:
    def check_text(self, *a, **k): return NS(violations=[], hard=[], claims=[])
class _HRag:
    def index_chapter(self, *a, **k): return 1
    def search(self, *a, **k): return []
class _HWiki:
    def ingest_chapter(self, *a, **k): return 0
    def retrieve(self, *a, **k): return []


def _run_harness(body, *, flag=True, ledger_ret=None):
    """실 ChapterGenerator.generate 관통 + build_ledger 스파이. 반환 (rec, spy)."""
    from novelcopilot.config import Settings
    from novelcopilot.domain.world import StyleSpec
    from novelcopilot.engine.harness import ChapterGenerator
    import novelcopilot.engine.dialogue_ledger as dlmod
    settings = Settings()
    object.__setattr__(settings, "dialogue_ledger", flag)
    prov = _Cap(body)
    spy = {"called": 0, "provider": None, "kwargs": None}

    def fake_build(provider, text, ontology, bus=None, chapter=0, pov_entity_id="", tagged=None):
        spy["called"] += 1
        spy["provider"] = provider
        spy["kwargs"] = {"chapter": chapter, "pov_entity_id": pov_entity_id, "tagged": tagged}
        return list(ledger_ret or [])

    real = dlmod.build_ledger
    dlmod.build_ledger = fake_build
    try:
        g = ChapterGenerator(prov, checker=_HChk(), style=StyleSpec(), event_bus=_Bus(),
                             settings=settings)
        beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
        rec = g.generate(2, beat, _HOnt(), _HRag(), _HWiki())
    finally:
        dlmod.build_ledger = real
    return rec, spy


def test_harness_finalized_wires_ledger():
    """FINALIZED+ON → build_ledger 정확히 1회(pov_entity_id 관통)·결과가 rec 에 영속."""
    from novelcopilot.domain.types import ChapterStatus
    rows = [{"idx": 1, "speaker": "미상", "text": "간다.", "canon": False}]
    rec, spy = _run_harness("본문 문장이다.", flag=True, ledger_ret=rows)
    assert rec.status == ChapterStatus.FINALIZED
    assert spy["called"] == 1
    assert spy["kwargs"]["chapter"] == 2                    # 배선 인자 관통(pov 는 _HOnt 빈 명부라 "")
    assert rec.dialogue_ledger == rows


def test_harness_flag_off_safe():
    """flag off → build_ledger 미호출·rec.dialogue_ledger []·확정은 정상(UnboundLocalError 0)."""
    from novelcopilot.domain.types import ChapterStatus
    rec, spy = _run_harness("본문 문장이다.", flag=False)
    assert rec.status == ChapterStatus.FINALIZED
    assert spy["called"] == 0
    assert rec.dialogue_ledger == []


def test_harness_escalated_safe():
    """ESCALATED(빈 본문) → build_ledger 미호출·rec.dialogue_ledger []·확정 경로 생존(비차단)."""
    from novelcopilot.domain.types import ChapterStatus
    rec, spy = _run_harness("", flag=True)
    assert rec.status == ChapterStatus.ESCALATED
    assert spy["called"] == 0
    assert rec.dialogue_ledger == []


# ═════════ ⑦ 주입 0 구조 잠금(생성 경로 dialogue_ledger 참조 0 + DG-6 카브아웃 행동 잠금) ═════════

def test_no_generation_path_injection_structural():
    """생성 경로 소스에 dialogue_ledger 참조 0 — prompts/rag/rerender 는 절대 유지(RP-1 grep 잠금).
    harness 조립부는 DG-6 카브아웃(보드 DG 섹션 SSOT)으로 원장이 '선별 인덱스'로만 들어온다 →
    구 텍스트 잠금을 행동 잠금으로 교체: 일부러 틀린 speaker 픽스처를 넣어도 발췌 블록(고정 프레임
    제외)의 전량이 확정 본문의 부분 문자열이고 화자 라벨은 미동봉 — 원장이 만들어낸 '판단'(귀속)이
    생성면으로 승격될 바이트 자체가 없다."""
    root = Path(__file__).resolve().parents[1] / "novelcopilot" / "engine"
    for rel in ("prompts.py", "rag.py", "rerender.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "dialogue_ledger" not in src, f"{rel} 에 생성 경로 주입 흔적"
    hsrc = (root / "harness.py").read_text(encoding="utf-8")
    assert "dialogue_ledger" in hsrc                       # 확정 이후 배선은 존재(⑥이 행동 잠금)
    # DG-6 행동 잠금: 오귀속 픽스처(화자 맞바꿈) → 발췌에 라벨 0·프레임 제외 전량이 본문 부분 문자열
    import re
    from novelcopilot.engine.lookup import CanonLookup
    chapter_text = _TEXT                                    # 확정 본문(따옴표 대사 3개)
    rows = [{"idx": 1, "speaker": "하지연", "text": "안 뗍니다. 가만 계세요.", "canon": True},
            {"idx": 2, "speaker": "서준호", "text": "제 손입니다.", "canon": True}]   # 일부러 뒤바뀐 귀속
    ont = NS(entities={"y": NS(name="하지연", aliases=[])})
    ex = CanonLookup(ont, [], 5, ledgers=[(2, rows)])._recent_exchange("y")
    assert ex.startswith("최근 대화(2화)=")
    body = ex.split("=", 1)[1]
    assert "하지연" not in body and "서준호" not in body    # ⒜ 라벨 미동봉 → 오귀속이 실릴 자리가 없다
    segs = re.findall(r'"([^"]+)"', body)
    assert segs and all(seg in chapter_text for seg in segs)   # ⒝ 주입 바이트 = 본문 부분 문자열


# ═════════ ⑧ 검증 축(build_verification SSOT·LLM 0) ═════════

def test_verification_axis_recompute_and_missing():
    from novelcopilot.domain.types import ChapterRecord, ChapterStatus
    from novelcopilot.engine.verification import build_verification, MISSING
    # 원장 없음(구 회차·OFF·귀속 실패·대사 0) → MISSING(결측 정직·null 금지)
    r0 = ChapterRecord(chapter=1, status=ChapterStatus.FINALIZED, text="지문뿐.")
    assert build_verification(r0, prev_texts=[], target_chars=5000)["dialogue_ledger"] == MISSING
    # 원장 있음 → 저장 원장 재집계(quotes·unknown·canon·화자별 어체 분포)
    rows = [{"idx": 1, "speaker": "서준호", "text": "정산 말씀드립니다.", "canon": True},
            {"idx": 2, "speaker": "의뢰인", "text": "들으셨어요?", "canon": False},
            {"idx": 3, "speaker": "미상", "text": "뭐.", "canon": False},
            {"idx": 4, "speaker": "하지연", "text": "가요."}]   # canon 무표기(구 원장 행)
    r1 = ChapterRecord(chapter=2, status=ChapterStatus.FINALIZED, text="본문.", dialogue_ledger=rows)
    ax = build_verification(r1, prev_texts=[], target_chars=5000)["dialogue_ledger"]
    assert ax["quotes"] == 4 and ax["unknown"] == 1
    assert ax["canon"] == 1                                 # PM 보정 ③: canon 무표기=비캐논 취급
    assert ax["canon_speakers"] == ["서준호"]                # ⑦: 캐논·지칭 구분 재료(자동 합산 없음)
    assert ax["speakers"]["서준호"]["합쇼체"] == 1 and ax["speakers"]["의뢰인"]["해요체"] == 1
