# -*- coding: utf-8 -*-
"""HZ-1 판단형 휴머나이즈 검증 — 조건부 발동(스타일 지각 판정)·같은 펜·수술 금기·cold_read 이월. LLM 0콜(스텁)·결정론.

설계(사용자 결정 2026-07-15):
  ① 스타일 지각 판정 스테이지(매화 무조건·+1콜) — 결정론 임계 발동 폐기("두더지잡기"). 판정자가 회차 프로즈를
     낭독 체감으로 읽고 N-4 문말 수리 필요 여부·문제 구간 인용을 낸다(measure-then-cite). 결정론 계측(무중단
     동일 어미 run)은 [참고 자료]로만 주입(판정 기준은 체감). 판정 실패→수리 스킵(보수·결측 정직).
  ② 판정 인용→본문 위치 매칭→결정론 리듬 스팬 정밀화→사실 밀집 금기(수치+엔티티≥4 스킵)→최대 humanize_n4_max_spans.
  ③ 같은 펜: humanize_model 기본 "" → 집도 provider == gen provider(스왑 0). 명시값 → 스왑 유지.
  ④ 재실현 최종화 경로도 동일 판정(이중 구현 금지 — apply_n4_judgment 공용 헬퍼).
  ⑤ 부수: 러너 게이트 영속(persist_gate_verification)의 cold_read 이월 누락 수리(MD-1 4개 화 실측 소실).

검증 축(사전 등록·코디네이터 교체 명세):
  1) needs_repair=false → N-4 수리 0(판정 기록만).
  2) needs_repair=true + 인용 2건 → 해당 창만 수리 시도(창 좌표 정확).
  3) 인용 위치 매칭 실패 → 스킵 기록(quote_unmatched).
  4) 판정 콜 실패(예외) → judge_style=None → N-4 수리 스킵(judge_missing·보수).
  5) 판정 프롬프트에 [참고 자료] 블록 + '판정 기준은 낭독 체감' 문안 포함.
  6) style_judge 스테이지 usage/time 계상(harness e2e).
  7) 매화 무조건 실행 — 임계 무관(무중단 run 없는 회차도 판정 콜 발생).
  8) 같은 펜: humanize_model="" → 집도 provider == gen provider / 명시값 → 스왑.
  9) 사실 밀집 스팬 금기(fallback=fact_dense 기록).
  10) 재실현 경로(_run_style_judgment_svc) 동일 판정 — apply_n4_judgment 재사용.
  11) cold_read 이월(러너 persist 후 dict 보존·"미실행"은 이월 안 함).
  12) 판정 콜 스텁에 style_judge_model 반영(create_role_provider 라우팅).

실행: (app/ 에서) py -3.12 -X utf8 -m pytest tools/test_hz1_conditional_humanize.py -q
"""
from __future__ import annotations
import pathlib
import sys
from types import SimpleNamespace

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from novelcopilot.config import Settings
from novelcopilot.domain.world import StyleSpec
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine import humanize_detect as hd
from novelcopilot.engine import style_judge as sj


class _Bus:
    def emit(self, *a, **k):
        pass


# 무중단 '~다' run 벽(N-4 검출 대상) + 사실 밀집 문장 하나.
WALL = ("그는 문을 열었다. 계단을 내려갔다. 벽을 짚었다. 손전등을 켰다. "
        "어둠이 물러났다. 발을 디뎠다. 숨을 골랐다. 앞으로 나아갔다.\n"
        "철수와 영희가 3개의 검을 45도로 들었다.")


def _judge(needs, quotes):
    """style_judge 산출 형식 스텁 — quotes = [(quote, why)…]."""
    return {"needs_repair": needs,
            "spans": [{"quote": q, "why": w} for (q, w) in quotes],
            "reason": "stub", "reference": {"runs": [], "backend": "kiwi", "n_runs": 0}}


# ═════════════════ ① 발동 판정(스타일 지각) ═════════════════

def test_no_repair_skips_all() -> bool:
    """1) needs_repair=false → N-4 수리 0·스킵 기록(판정: 수리 불필요)."""
    s = Settings()
    n4, skips = hd.select_n4_findings(WALL, _judge(False, []), s, roster={"철수", "영희"})
    ok = (n4 == [])
    ok &= (len(skips) == 1 and skips[0]["fallback"] == "no_repair")
    ok &= (skips[0]["changed"] is False)
    print(f"[{'OK' if ok else 'FAIL'}] 1: needs_repair=false → N-4 수리 0·스킵 기록")
    return bool(ok)


def test_repair_two_quotes_windows() -> bool:
    """2) needs_repair=true + 인용 2건(서로 다른 구간) → 각 인용 창만 N-4 수리 대상(창 좌표가 원문 슬라이스).

    같은 무중단 run 안의 두 인용은 같은 벽 창으로 병합(중복 제거)되므로, 대사로 run 이 리셋된 *두 벽*을
    쓴다 — 각 벽이 독립 창이라 인용 2건이 각각 살아남는다."""
    s = Settings()
    # 대사가 두 벽을 끊어 서로 다른 ending_run 창을 만든다.
    text = ("그는 문을 열었다. 계단을 내려갔다. 벽을 짚었다. 손전등을 켰다. "
            "어둠이 물러났다. 발을 디뎠다. 숨을 골랐다. 앞으로 나아갔다.\n"
            '"거기 누구야?"\n'
            "복도는 길었다. 천장이 낮았다. 공기가 찼다. 발소리가 울렸다. "
            "문이 보였다. 손잡이를 돌렸다. 방은 비었다. 창이 열렸다.")
    q1 = "그는 문을 열었다. 계단을 내려갔다."
    q2 = "복도는 길었다. 천장이 낮았다."
    n4, skips = hd.select_n4_findings(text, _judge(True, [(q1, "단조"), (q2, "단조")]), s, roster=set())
    ok = (len(n4) == 2)
    for f in n4:
        cs, ce = f["span"]["char_start"], f["span"]["char_end"]
        ok &= (text[cs:ce] == f["span"]["text"])          # 좌표↔텍스트 정합
        ok &= (f["category"] == "N-4" and f["metric"]["source"] == "style_judge")
    ok &= (n4[0]["span"]["char_start"] != n4[1]["span"]["char_start"])   # 서로 다른 벽
    print(f"[{'OK' if ok else 'FAIL'}] 2: 인용 2건(서로 다른 벽) → 각 인용 창만 수리 대상(좌표 정합)")
    return bool(ok)


def test_quote_unmatched_skip() -> bool:
    """3) 인용 위치 매칭 실패 → 스킵 기록(quote_unmatched)·수리 대상 아님."""
    s = Settings()
    n4, skips = hd.select_n4_findings(WALL, _judge(True, [("본문에 없는 문장입니다", "x")]), s, roster=set())
    ok = (n4 == [])
    ok &= (any(sk["fallback"] == "quote_unmatched" for sk in skips))
    # 공백 정규화 부분 매칭도 커버 — 공백만 다른 인용은 매칭 성공(스킵 아님)
    q_ws = "그는  문을   열었다.\n계단을 내려갔다."   # 공백/줄바꿈 변형
    n4b, _ = hd.select_n4_findings(WALL, _judge(True, [(q_ws, "x")]), s, roster=set())
    ok &= (len(n4b) == 1)                                  # 정규화 매칭 성공
    print(f"[{'OK' if ok else 'FAIL'}] 3: 인용 매칭 실패=스킵·공백정규화 부분매칭 성공")
    return bool(ok)


def test_judge_call_failure_conservative() -> bool:
    """4) 판정 콜 실패(provider 예외) → judge_style=None → apply/select 가 N-4 수리 스킵(judge_missing·보수)."""
    class Boom:
        usage = SimpleNamespace(chat_tokens=0)
        def chat_json(self, *a, **k):
            raise RuntimeError("판정 콜 실패")
    judgment = sj.judge_style(Boom(), WALL)
    ok = (judgment is None)                                # 실패=None(보수)
    n4, skips = hd.select_n4_findings(WALL, None, Settings(), roster=set())
    ok &= (n4 == [] and skips[0]["fallback"] == "judge_missing")
    print(f"[{'OK' if ok else 'FAIL'}] 4: 판정 콜 실패 → None → N-4 수리 스킵(judge_missing·보수)")
    return bool(ok)


def test_judge_prompt_has_reference_and_feel_clause() -> bool:
    """5) 판정 프롬프트에 [참고 자료] 블록 + '판정 기준은 낭독 체감' 문안 포함(measure-then-cite·참고 자료 격하)."""
    captured = {"msgs": None}

    class Cap:
        usage = SimpleNamespace(chat_tokens=0)
        def chat_json(self, messages, *a, **k):
            captured["msgs"] = messages
            return {"needs_repair": False, "spans": [], "reason": ""}
    sj.judge_style(Cap(), WALL, genre="판타지")
    joined = "\n".join(m["content"] for m in (captured["msgs"] or []))
    ok = ("참고 자료" in joined)                           # 참고 자료 블록 존재
    ok &= ("낭독 체감" in joined)                          # 판정 기준=체감 문안
    ok &= ("정확" in joined and "인용" in joined)           # measure-then-cite(정확 인용 의무)
    # 참고 자료에 무중단 run 목록(WALL 은 8연속 '~다' run)
    ok &= ("연속" in joined)
    print(f"[{'OK' if ok else 'FAIL'}] 5: 판정 프롬프트=참고 자료 블록+낭독 체감+정확 인용 의무")
    return bool(ok)


def test_reference_block_deterministic() -> bool:
    """5보강) build_reference_block — 무중단 run 목록(kiwi)·계측 불가 시 unavailable(결측 정직)."""
    ref = sj.build_reference_block(WALL)
    ok = (ref["backend"] == "kiwi")
    ok &= (len(ref["runs"]) >= 1)                          # 8연속 run 검출
    ok &= (all("ending_key" in r and "n_sent" in r and "first_sentence" in r for r in ref["runs"]))
    # 빈 본문 → runs 빈(판정은 프로즈만으로)
    ref0 = sj.build_reference_block("")
    ok &= (ref0["runs"] == [])
    print(f"[{'OK' if ok else 'FAIL'}] 5b: 참고 자료 블록 결정론(run 목록·빈 본문=빈)")
    return bool(ok)


# ═════════════════ ② 사실 밀집 금기 ═════════════════

def test_fact_dense_taboo() -> bool:
    """9) 사실 밀집 스팬(수치+엔티티≥4) → 수술 금기·fallback=fact_dense 기록(G-B 기각 낭비 사전 제외)."""
    s = Settings()
    # 철수+영희(엔티티 2) + 3개+45도(아라비아 수치 2) = 4 >= FACT_DENSE_TOKEN_MIN
    q = "철수와 영희가 3개의 검을 45도로 들었다."
    n4, skips = hd.select_n4_findings(WALL, _judge(True, [(q, "x")]), s, roster={"철수", "영희"})
    ok = (n4 == [])                                       # 금기 → 수리 대상 아님
    fd = [sk for sk in skips if sk["fallback"] == "fact_dense"]
    ok &= (len(fd) == 1 and fd[0]["changed"] is False)
    ok &= ("사실 밀집" in (fd[0]["note"] or ""))            # 기록 투명성(은폐 금지)
    # 임계 미만(수치 1개만) → 금기 아님
    n_num, n_name = hd._fact_token_count("철수가 검을 들었다", {"철수"})
    ok &= ((n_num + n_name) < hd.FACT_DENSE_TOKEN_MIN)
    print(f"[{'OK' if ok else 'FAIL'}] 9: 사실 밀집 금기=fact_dense 기록·임계 미만 통과")
    return bool(ok)


def test_max_spans_cap() -> bool:
    """② 상한 — 판정 인용이 많아도 humanize_n4_max_spans(기본 3)까지만 시도.

    무중단 run 이 없는 본문(대사로 매 문장 리셋 → 인용 창이 리듬 스팬에 병합되지 않아 각 인용이 독립 창)에서
    6개 인용을 넣어도 상한 3에서 절단됨을 확인한다."""
    s = Settings()   # humanize_n4_max_spans=3
    # 서로 다른 6개 지문 문장(사이 대사로 run 리셋 → 병합 없음·전부 독립 매칭·비-사실밀집).
    text = ('그는 갔다.\n"어."\n그녀는 왔다.\n"응."\n바람이 불었다.\n"음."\n'
            '비가 내렸다.\n"아."\n해가 떴다.\n"오."\n달이 졌다.\n"흠."\n별이 빛났다.')
    segs = ["그는 갔다.", "그녀는 왔다.", "바람이 불었다.", "비가 내렸다.", "해가 떴다.", "달이 졌다."]
    n4, _ = hd.select_n4_findings(text, _judge(True, [(seg, "x") for seg in segs]), s, roster=set())
    ok = (len(n4) == 3)                                   # 상한 3(6개 인용 중 앞 3만)
    print(f"[{'OK' if ok else 'FAIL'}] ②: N-4 수리 상한 humanize_n4_max_spans(3)")
    return bool(ok)


# ═════════════════ ③ 같은 펜(집도 provider == gen) ═════════════════

def test_same_pen_default_no_swap() -> bool:
    """8) humanize_model="" → build_humanize_provider=None(스왑 없음·집도=gen provider). 명시값 → 스왑."""
    from novelcopilot.engine.humanize_pass import build_humanize_provider
    s = Settings()
    ok = (s.humanize_model == "")                         # HZ-1③ 기본값 변경
    ok &= (build_humanize_provider(s) is None)            # ""→None(스왑 없음·gen provider 사용)
    # 명시값이면 스왑 provider 생성(create_role_provider — 키 부재 시 기본 provider 폴백이나 None 아님)
    s2 = s.model_copy(update={"humanize_model": "anthropic:claude-opus-4-8"})
    hp = build_humanize_provider(s2)
    ok &= (hp is not None)                                # 명시값 → 스왑 provider(None 아님)
    print(f"[{'OK' if ok else 'FAIL'}] 8: humanize_model 기본 \"\"=스왑없음(같은 펜)·명시값=스왑")
    return bool(ok)


# ═════════════════ ④ 재실현 경로 동일 판정 ═════════════════

def test_apply_n4_judgment_recombination() -> bool:
    """10) apply_n4_judgment(공용 헬퍼) — N-4 는 판정 대체·N-3/N-5/N-6 은 자체 검출 불변(양 경로 재사용)."""
    s = Settings()
    findings = hd.detect_chapter(WALL, ledger=None, roster={"철수", "영희"})
    cats_in = {f["category"] for f in findings}
    # 판정 없음(None) → N-4 전부 제거·나머지 보존
    rec, skips = hd.apply_n4_judgment(WALL, findings, None, s, roster={"철수", "영희"})
    cats_out = {f["category"] for f in rec}
    ok = ("N-4" not in cats_out)                          # 판정 없으면 N-4 수리 대상 0
    ok &= (cats_out == (cats_in - {"N-4"}))               # N-3/N-5/N-6 은 불변(있던 것 유지)
    ok &= (any(sk["fallback"] == "judge_missing" for sk in skips))
    # 판정 있음 + 유효 인용 → N-4 복귀(순서 = 판정 N-4 먼저)
    rec2, _ = hd.apply_n4_judgment(WALL, findings, _judge(True, [("그는 문을 열었다. 계단을 내려갔다.", "x")]),
                                   s, roster=set())
    ok &= (rec2[0]["category"] == "N-4")                  # 판정 N-4 가 앞
    print(f"[{'OK' if ok else 'FAIL'}] 10: apply_n4_judgment 공용 헬퍼(N-4 판정 대체·나머지 불변)")
    return bool(ok)


# ═════════════════ ⑤ cold_read 이월(러너 persist) ═════════════════

def test_cold_read_carryover_runner_persist() -> bool:
    """11) 러너 persist_gate_verification 재집계 시 기존 cold_read(dict) 이월·"미실행"은 이월 안 함."""
    from novelcopilot.engine.verification import build_verification, MISSING

    cr = {"comprehension_0_100": 65, "confusions": [{"what": "루갈이?", "quote": "그"}],
          "curiosities": ["처리실 밖은?"], "drop_risk": True, "truncated": False, "chapters": 2}
    rec = ChapterRecord(chapter=4, status=ChapterStatus.FINALIZED, text="본문 " * 200)
    # 앞서 발행 시 cold_read 가 verification 에 실렸다고 가정
    rec.verification = build_verification(rec, prev_texts=[], target_chars=5000, cold_read=cr)
    ok = (rec.verification["cold_read"] == cr)

    # persist_gate_verification 의 재집계 로직(러너 경로) 모사 — 기존 dict 이월.
    _prior_cr = (rec.verification or {}).get("cold_read")
    _prior_cr = _prior_cr if isinstance(_prior_cr, dict) else None
    gate = {"verdict": "PASS", "rounds": 1, "source": "runner"}
    rec.verification = build_verification(rec, prev_texts=[], target_chars=5000,
                                          gate=gate, cold_read=_prior_cr)
    ok &= (rec.verification["cold_read"] == cr)           # 이월됨(소실 방지)
    ok &= (rec.verification["gate"] == gate)              # gate 도 동형 이월

    # "미실행"(MISSING 문자열)은 이월 안 함 → 재집계 후에도 MISSING(결측 정직)
    rec2 = ChapterRecord(chapter=5, status=ChapterStatus.FINALIZED, text="본문 " * 200)
    rec2.verification = build_verification(rec2, prev_texts=[], target_chars=5000)   # cold_read 미실행
    ok &= (rec2.verification["cold_read"] == MISSING)
    _pc2 = (rec2.verification or {}).get("cold_read")
    _pc2 = _pc2 if isinstance(_pc2, dict) else None
    ok &= (_pc2 is None)                                  # "미실행" 문자열 → None(이월 안 함)
    rec2.verification = build_verification(rec2, prev_texts=[], target_chars=5000,
                                          gate=gate, cold_read=_pc2)
    ok &= (rec2.verification["cold_read"] == MISSING)     # 여전히 MISSING(가짜 dict 안 만듦)
    print(f"[{'OK' if ok else 'FAIL'}] 11: cold_read 이월(dict 보존·미실행은 이월 안 함)")
    return bool(ok)


def test_cold_read_carryover_persist_gate_source() -> bool:
    """11보강) 실제 gen_gated.persist_gate_verification 소스가 cold_read=_prior_cr 를 넘기는지(회귀 방지 grep-lock)."""
    import inspect
    import tools.gen_gated as gg
    src = inspect.getsource(gg.persist_gate_verification)
    ok = ("cold_read" in src)                             # cold_read 이월 로직 존재
    ok &= ("_prior_cr" in src or "cold_read=" in src)
    print(f"[{'OK' if ok else 'FAIL'}] 11b: persist_gate_verification 소스에 cold_read 이월 배선")
    return bool(ok)


# ═════════════════ 판정자 라우팅·스텁 모델명 ═════════════════

def test_style_judge_model_routing() -> bool:
    """12) 판정 콜 스텁 — style_judge_model 기본값(openai:gpt-5.6) 반영·create_role_provider 라우팅.

    ⚠️ conftest 가 테스트 프로세스에서 NOVEL_STYLE_JUDGE_MODEL="" 로 강제(실 LLM 0)하므로, Settings() 인스턴스
    값이 아니라 *config 클래스 필드 기본값*(env 미개입)을 검증한다 — 제품 기본이 gpt-5.6 임을 못박는다."""
    default = Settings.model_fields["style_judge_model"].default
    ok = (default == "openai:gpt-5.6")                    # 코디네이터 상향(GPT-5.6 Sol·제품 기본)
    # create_role_provider 가 스펙을 파싱해 provider/model 을 라우팅(키 부재 시 기본 폴백 — crash 없음)
    from novelcopilot.llm.factory import create_role_provider
    s = Settings()
    p = create_role_provider(s, "openai:gpt-5.6")
    ok &= (p is not None)                                 # 라우팅 crash 0(폴백 안전)
    _core = getattr(p, "_inner", p)                       # PL-1: 로깅 프록시는 관측 전용 — 라우팅 판정은 내핵 기준
    ok &= (type(_core).__name__ == "OpenAIProvider")      # openai: 스펙 → OpenAI provider 라우팅
    # 빈값 → 기본 provider(gen)로 폴백(스왑 0 관례)
    p_empty = create_role_provider(s, "")
    ok &= (p_empty is not None)
    print(f"[{'OK' if ok else 'FAIL'}] 12: style_judge_model 기본=openai:gpt-5.6·라우팅 폴백 안전")
    return bool(ok)


# ═════════════════ ⑥⑦ harness e2e(usage 계상·매화 무조건) ═════════════════

class _Ont:
    entities = {}
    rules = []
    def is_actor(self, et): return False
    def canon_facts(self, ids, ch): return []
    def canon_relations(self, ids, ch): return []
    def scan_present_ids(self, text): return []
class _Chk:
    def check_text(self, *a, **k): return SimpleNamespace(violations=[], hard=[], claims=[])
class _Rag:
    def index_chapter(self, *a, **k): return 1
    def search(self, *a, **k): return []
class _Wiki:
    def ingest_chapter(self, *a, **k): return 0
    def retrieve(self, *a, **k): return []


class _JudgeCap:
    """판정 콜을 관측하는 스텁 provider — chat_json 호출 시 needs_repair·인용 여부 기록·usage 증가."""
    def __init__(self, needs=False):
        self.last_truncated = False
        self.usage = SimpleNamespace(chat_tokens=0, chat_calls=0)
        self.judge_calls = 0
        self._needs = needs
    def chat(self, messages, *a, **k):
        return "본문."
    def chat_json(self, messages, *a, **k):
        # 스타일 판정 콜만 needs_repair 스키마를 요구 — 참고 자료 블록으로 판별.
        joined = "\n".join(m.get("content", "") for m in messages)
        if "낭독 체감" in joined:
            self.judge_calls += 1
            self.usage.chat_tokens += 50   # style_judge usage 계상 관측용
            return {"needs_repair": self._needs, "spans": [], "reason": "stub"}
        return {}
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def _run_harness_generate(prov):
    """harness generate() 를 스텁 provider 로 1회 태워 record 반환(humanize_spans 는 실물 — 판정만 관측).

    style_judge_model="" 을 명시 강제 — 판정 provider = 주입된 스텁(prov) 재사용(실 LLM 0·env 무관 결정론)."""
    from novelcopilot.engine.harness import ChapterGenerator
    settings = Settings().model_copy(update={"style_judge_model": ""})   # ""=스왑0 → prov(스텁) 재사용
    g = ChapterGenerator(prov, checker=_Chk(), style=StyleSpec(), event_bus=_Bus(), settings=settings)
    g.service = None
    beat = {"chapter": 2, "title": "t", "summary": "요약", "key_events": [], "entities": []}
    return g.generate(2, beat, _Ont(), _Rag(), _Wiki())


def test_harness_style_judge_usage_charged() -> bool:
    """6) harness e2e — style_judge 스테이지가 usage_by_stage/time_by_stage 에 계상(TM-1 대칭)."""
    prov = _JudgeCap(needs=False)
    rec = _run_harness_generate(prov)
    ok = ("style_judge" in rec.usage_by_stage)            # usage 계상
    ok &= ("style_judge" in rec.time_by_stage)            # time 계상(TM-1 대칭)
    ok &= (rec.usage_by_stage["style_judge"] == 50)       # 판정 콜 델타(스텁 +50)
    # 이중 계상 0: 판정 provider=""(=self.provider) 재사용 경로에서도 판정 50 토큰이 humanize 델타에 안 샌다.
    ok &= (rec.usage_by_stage.get("humanize", 0) == 0)    # humanize 스테이지엔 판정 토큰 미포함
    ok &= (prov.judge_calls == 1)                         # 판정 콜 정확히 1회(매화)
    print(f"[{'OK' if ok else 'FAIL'}] 6: harness style_judge usage/time 계상(이중 계상 0·TM-1 대칭)")
    return bool(ok)


def test_harness_judge_runs_unconditionally() -> bool:
    """7) 매화 무조건 실행 — 무중단 run 이 없는(단조롭지 않은) 회차도 판정 콜이 발생(임계 무관)."""
    # 스텁 provider 는 본문 생성이 "본문."이라 무중단 run 이 없다 → 임계 발동이라면 판정 안 함.
    #   HZ-1 은 매화 무조건이므로 이런 회차에도 판정 콜이 발생해야 한다.
    prov = _JudgeCap(needs=False)
    rec = _run_harness_generate(prov)
    ok = (prov.judge_calls == 1)                          # 임계 무관·무조건 1회
    ok &= ("style_judge" in rec.usage_by_stage)
    print(f"[{'OK' if ok else 'FAIL'}] 7: 매화 무조건 판정(임계 무관·무중단 run 없어도 실행)")
    return bool(ok)


def main() -> int:
    results = [
        test_no_repair_skips_all(),
        test_repair_two_quotes_windows(),
        test_quote_unmatched_skip(),
        test_judge_call_failure_conservative(),
        test_judge_prompt_has_reference_and_feel_clause(),
        test_reference_block_deterministic(),
        test_fact_dense_taboo(),
        test_max_spans_cap(),
        test_same_pen_default_no_swap(),
        test_apply_n4_judgment_recombination(),
        test_cold_read_carryover_runner_persist(),
        test_cold_read_carryover_persist_gate_source(),
        test_style_judge_model_routing(),
        test_harness_style_judge_usage_charged(),
        test_harness_judge_runs_unconditionally(),
    ]
    print("\nHZ-1(판단형 휴머나이즈) 검증:", "ALL GREEN" if all(results) else "FAIL")
    return 0 if all(results) else 1


def test_hz1_conditional_humanize_all_green():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
