# -*- coding: utf-8 -*-
"""ST-12c 검증 — 비앵커 재실현 패스 + BoN-N Kiwi 리랭크(설계 docs/design-st12-style-register.md §5·§9).

실 LLM 0콜(전부 mock·결정론). 잠그는 계약:
 ① 입력 조립(대사·수치·고유명사 추출 결정론) — 프로브 dialogue_lines 방식·과추출 차단(교착어 substring).
 ② 리랭크 산술(대역 최근접·tie-break max_run→da_ratio).
 ③ 하드 실격(대사 유실/수치 누락/분량 0.8~1.3x 밖).
 ④ 전 후보 실격 → 원문 유지 + 정직 사유(채택 없음).
 ⑤ 가드 불통과 → 채택 금지(원문 유지+사유).
 ⑥ 채택 시 revision 기록(append-only) + undo 왕복(revise/accept 계약 재사용).
 ⑦ preference jsonl append(프로젝트 JSON 불변).
 ⑧ 파생물 정합 호출(_summarize beat 관통·verification 재집계·stale 표식).

실행: PYTHONPATH=app py -3.12 tools/test_st12c_rerender.py  (또는 pytest)
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
import tempfile
import threading
from pathlib import Path

import pytest

from novelcopilot.domain.project import ProjectSeed, ProjectState
from novelcopilot.domain.world import WorldConfig, StyleSpec
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.engine import rerender as rr


# ═════════════════════════ ① 입력 조립(결정론) ═════════════════════════
def test_extract_dialogue_lines_order_and_form():
    text = '지문 한 줄.\n"첫 대사."\n또 지문.\n"둘째 대사."\n— 대시 대사.\n'
    dl = rr.extract_dialogue_lines(text)
    assert dl == ['"첫 대사."', '"둘째 대사."', '— 대시 대사.']   # 순서 보존·원형·따옴표/대시만


def test_extract_dialogue_ignores_single_quote_and_prose():
    text = "'속생각은 대사 아님'\n그냥 지문.\n\"진짜 대사\""
    dl = rr.extract_dialogue_lines(text)
    assert dl == ['"진짜 대사"']   # 홑따옴표·지문 제외


def test_extract_numeric_tokens_arabic_and_korean_units():
    text = "이거 3만 원. 45도로 잘렸다. 세 갈래 균열. 다섯 시. 열두 점."
    # 기본(하드 게이트) = 아라비아만 — 파일럿 1차 보정(2026-07-14): 한글 수사 관용구를 하드 계약에 넣으면
    #   표현 재구성이 본질인 재실현에서 전 후보가 구조적으로 실격(ch1·ch2 6/6 전멸 실측). 의미 보존은 G-B 소관.
    nt = rr.extract_numeric_tokens(text)
    assert "3만" in nt and "45도" in nt
    assert all(tok not in nt for tok in ("세 갈래", "다섯 시", "열두 점"))
    # advisory 모드(arabic_only=False)에서는 한글 수사+단위도 추출(하드 게이트 사용 금지 용도)
    nt_all = rr.extract_numeric_tokens(text, arabic_only=False)
    assert "세 갈래" in nt_all and "다섯 시" in nt_all and "열두 점" in nt_all


def test_extract_numeric_no_false_positive_on_names_and_verbs():
    # 교착어 substring 결함 회피: 인명(한나)·동사(열었다·세상에)를 수사로 오탐하지 않는다(단위 없는 맨 수사 제외).
    text = "한나가 문을 열었다. 세상에 이런 일이. 반갑진 않았다."
    nt = rr.extract_numeric_tokens(text)
    assert nt == []   # 단위 없는 맨 수사·비수사는 전부 제외


def test_extract_numeric_a4_not_over_captured():
    # 'A4 봉투' 의 4 는 단위 아님 → '4' 만(봉투를 흡수 금지).
    text = "안에서 A4 봉투를 꺼냈다."
    nt = rr.extract_numeric_tokens(text)
    assert nt == ["4"]


def test_prompt_length_contract_absolute_chars():
    # 파일럿 1차 보정: 비앵커 패스에서 "원 회차의 90~110%"는 모델이 계산 불가(원문 미노출) → 절대 자수로.
    sys_p, _ = rr.build_rerender_prompt(narrator_card="", skeleton="뼈대", dialogue_lines=[],
                                        numeric_tokens=[], proper_nouns=[], orig_chars=5000)
    assert "4500~5500자" in sys_p and "원 회차의" not in sys_p
    sys_p0, _ = rr.build_rerender_prompt(narrator_card="", skeleton="뼈대", dialogue_lines=[],
                                         numeric_tokens=[], proper_nouns=[])
    assert "자 안팎" not in sys_p0 and "원 회차의" not in sys_p0   # orig 미상 → 자수 미기재(계산 불가 지시 금지)


def test_sp3_prompt_contracts():
    # SP-3(2026-08-18 감사 C2·M1·m3): 이름 표기 권위 분업·인칭 값 실체화·확정 스토리 층위 라벨·하위호환.
    # ⓐ 하위호환: pov·skeleton_is_story 미전달 = 구 문안(유지 지시·[사실 뼈대] 라벨) 바이트 동일.
    sys0, usr0 = rr.build_rerender_prompt(narrator_card="", skeleton="뼈대 X", dialogue_lines=[],
                                          numeric_tokens=[], proper_nouns=[])
    assert "3) 시점(서술 인칭)을 유지하라." in sys0
    assert usr0.startswith("[사실 뼈대]\n뼈대 X")                     # m3: user 첫 블록 바이트 잠금
    assert "이름 목록에 적힌 표기" not in sys0                        # 이름 목록 없으면 dangling 문장 무추가
    # ⓑ 인칭 값 실체화(M1): 비앵커 콜엔 '유지'의 준거가 없다 — pov 를 값으로.
    sys1, _ = rr.build_rerender_prompt(narrator_card="", skeleton="s", dialogue_lines=[],
                                       numeric_tokens=[], proper_nouns=[], pov="first")
    assert "1인칭 주인공 시점으로 서술한다" in sys1 and "유지하라" not in sys1.split("\n2)")[0]
    sys3, _ = rr.build_rerender_prompt(narrator_card="", skeleton="s", dialogue_lines=[],
                                       numeric_tokens=[], proper_nouns=[], pov="third_limited")
    assert "3인칭 시점으로 서술한다" in sys3
    # ⓒ 이름 표기 권위(C2): 이름 목록이 실릴 때만 분업 문장이 붙고, 계약 1항에서 고유명사가 빠진다.
    sysn, usrn = rr.build_rerender_prompt(narrator_card="", skeleton="s", dialogue_lines=[],
                                          numeric_tokens=[], proper_nouns=["강도윤"])
    assert "이름 목록에 적힌 표기를 쓴다" in sysn and "고유명사" not in sysn
    assert "이름: 강도윤" in usrn
    # ⓓ 확정 스토리 뼈대(C1): 층위 라벨 + 실재 블록 포인터([웹소설 레지스터]는 system 에 실재).
    _, usrs = rr.build_rerender_prompt(narrator_card="", skeleton="- 사건이 벌어진다", dialogue_lines=[],
                                       numeric_tokens=[], proper_nouns=[], skeleton_is_story=True)
    assert usrs.startswith("[사실 뼈대(확정 스토리)]")
    assert "인칭·시제·문장은 위 계약과 [웹소설 레지스터]를 따른다" in usrs


def test_sp3_read_gate_has_citation_fields():
    # SP-3(감사 M2): 정독 게이트 — 거짓 전제("같은 사건·같은 대사") 제거 + 인용 의무 필드.
    system, user = rr.build_read_gate_prompt("판본 하나", "판본 둘")
    assert "같은 사건" not in user and "문체만 다름" not in user
    assert "ㄱ_인용" in user and "ㄴ_인용" in user


# ═══════════ ① RR-1 소스 차단 (명부 × 본문 교집합 · 사람 단위 그룹핑 · 부재 토큰 불포함) ═══════════
class _RREnt:
    def __init__(self, name, aliases=None):
        self.name, self.aliases = name, list(aliases or [])


class _RROnt:
    def __init__(self, entities):
        self.entities = entities


def test_rr1_entity_tokens_intersection_with_source():
    """A: 재실현 대상 본문에 실제 등장하는 명부 토큰만 — 부재(데뷔 전) 인물은 절대 목록에 오르지 못한다."""
    ont = _RROnt({"a": _RREnt("서준호", ["준호"]),
                  "b": _RREnt("하지연", ["지연"]),            # 본문 미등장(6화 이후 데뷔) — 유출되면 안 됨
                  "c": _RREnt("강도윤")})                     # 본문 미등장
    source = "서준호가 문을 열었다. 준호는 뒤를 돌아봤다."   # 서준호/준호만 등장
    toks = rr.entity_tokens_in_source(ont, source)
    assert toks == ["서준호(=준호)"]                          # 사람 단위 그룹핑 + 발견 별칭만 병기
    assert all("하지연" not in t and "지연" not in t and "강도윤" not in t for t in toks)   # 부재 인물 구조적 제외


def test_rr1_name_only_when_alias_absent():
    """A: name 은 본문에 있고 별칭은 본문에 없으면 name 만(그룹핑 괄호 없음).

    별칭('사장님')은 name('윤재석')의 substring 이 아니고 본문에도 없으므로 병기되지 않는다."""
    ont = _RROnt({"a": _RREnt("윤재석", ["사장님"])})
    assert rr.entity_tokens_in_source(ont, "윤재석이 말했다.") == ["윤재석"]   # 별칭 미등장 → 병기 안 함


def test_rr1_alias_only_presence_counts_as_person_present():
    """A/B 수정(2026-07-16 퇴근작 1화 실측): 본문이 인물을 정식 명칭이 아니라 이름 일부·별칭으로만 부르는
    정상 케이스("카일 무쇠늑대"→본문엔 "카일"·"단장"만)에서, 초판의 '정식 name 전체 문자열 존재' 게이트가
    등장 인물을 명부에서 통째로 빼고 그 별칭들을 B에서 '발명'으로 오탐 → 후보 전원 실격.
    수리: 존재 판정=사람 단위(name 또는 별칭 ≥2자 중 하나라도 본문 존재)."""
    ont = _RROnt({"k": _RREnt("카일 무쇠늑대", ["카일", "단장"]),
                  "x": _RREnt("하지연", ["지연 아씨"])})       # 어떤 토큰도 본문에 없음 → 부재(차단 불변)
    source = "카일이 웃었다. 단장은 서류가 무섭다."
    toks = rr.entity_tokens_in_source(ont, source)
    assert toks == ["카일 무쇠늑대(=카일, 단장)"]              # 별칭만 등장해도 사람은 명부에 오른다
    # B: 등장 인물의 다른 표기(정식 명칭)를 후보가 써도 발명이 아니다. 부재 인물은 여전히 실격.
    assert rr.foreign_roster_tokens("카일 무쇠늑대가 진 빚.", ont, source) == []
    assert rr.foreign_roster_tokens("하지연이 나타났다.", ont, source) == ["하지연"]


def test_rr1_one_char_name_and_absent_name_excluded():
    """A: 1자 name(형태소 substring 충돌) 및 본문 부재 name 은 엔티티 선정에서 제외(보수)."""
    ont = _RROnt({"a": _RREnt("강", ["강물"]),                # 1자 name → 제외
                  "b": _RREnt("문석주")})                     # 본문에 없음 → 제외
    assert rr.entity_tokens_in_source(ont, "강물이 흐르고 문이 열렸다.") == []   # 부재 인물 유출 0


# ═══════════ ① RR-1 계약 가드 B (원문에 없는 명부 인물 발명 = 하드 실격) ═══════════
def test_rr1_foreign_roster_tokens_detects_invented_names():
    """B: 후보에 등장하되 원문엔 없는 명부 인물 토큰만 반환(원문에 있던 토큰은 정당·위반 아님)."""
    ont = _RROnt({"a": _RREnt("서준호", ["준호"]),
                  "b": _RREnt("하지연")})                     # 원문에 없음
    source = "서준호가 걸었다."
    cand_ok = "준호가 걸었다."                                # 같은 인물 별칭 — 위반 아님(원문 인물)
    cand_bad = "서준호와 하지연이 함께 걸었다."               # 하지연 발명 — 위반
    assert rr.foreign_roster_tokens(cand_ok, ont, source) == []
    assert rr.foreign_roster_tokens(cand_bad, ont, source) == ["하지연"]


def test_rr1_foreign_word_boundary_guard():
    # SP-3(13화 실측): '열넷째 칸'(서수)이 명부 인물 '넷째'에 물리던 어두 substring 오탐 — 앞 문자가
    #   한글이면 다른 단어의 꼬리(비발명). 어두 등장('넷째가 왔다')은 여전히 발명 판정.
    ont = _RROnt({"a": _RREnt("넷째", aliases=["얼룩"])})
    src = "X" * 100   # 원문에 '넷째' 없음
    assert rr.foreign_roster_tokens("나는 열넷째 칸을 밟았다.", ont, src) == []
    assert rr.foreign_roster_tokens("넷째가 골목에 서 있었다.", ont, src) == ["넷째"]


def test_rr1_foreign_token_hard_disqualifies_candidate():
    """B: foreign_tokens 가 비지 않으면 evaluate_candidate 하드 실격(사실 불변 계약 집행)."""
    orig = "X" * 100
    ev = rr.evaluate_candidate("y" * 100, original=orig, dialogue_lines=[], numeric_tokens=[],
                               foreign_tokens=["하지연", "강도윤"])
    assert ev["disqualified"] and ev["foreign_tokens"] == ["하지연", "강도윤"]
    assert any("명부 인물 발명" in r for r in ev["reasons"])


def test_rr1_rerank_disqualifies_via_foreign_fn():
    """B: rerank_candidates 가 foreign_fn 으로 발명 후보를 실격 — 깨끗한 후보만 승자."""
    ont = _RROnt({"a": _RREnt("서준호"), "b": _RREnt("하지연")})
    source = "X" * 100
    clean = "서준호 이야기. " + "z" * 89 + "."   # 원문 인물만(하지연 없음) + 길이 유효 + 말미 종결
    dirty = "하지연 등장. " + "z" * 90 + "."     # 하지연 발명
    res = rr.rerank_candidates([dirty, clean], original="서준호 " + "X" * 96,
                               dialogue_lines=[], numeric_tokens=[], metrics_fn=None,
                               foreign_fn=lambda c: rr.foreign_roster_tokens(c, ont, "서준호 " + "X" * 96))
    assert res["winner_index"] == 1                          # dirty(0) 실격 → clean(1) 승
    e0 = next(e for e in res["evaluations"] if e["index"] == 0)
    assert e0["disqualified"] and e0["foreign_tokens"] == ["하지연"]


# ═════════════════════════ ② 리랭크 산술 ═════════════════════════
def _metrics(top_ratio=None, max_run=None, ratio=None):
    return {"ending_profile": {"top_ratio": top_ratio, "max_run": max_run},
            "da_streak": {"ratio": ratio}}


def test_rerank_band_nearest_wins():
    # 대역(0.347~0.595) 안 후보가 밖 후보를 이긴다.
    cands = ["A" * 99 + ".", "B" * 99 + "."]
    mfn = lambda c: _metrics(top_ratio=0.52, max_run=6, ratio=0.3) if c[0] == "A" else _metrics(top_ratio=0.85, max_run=20, ratio=0.7)
    res = rr.rerank_candidates(cands, original="X" * 100, dialogue_lines=[], numeric_tokens=[], metrics_fn=mfn)
    assert res["winner_index"] == 0 and res["all_disqualified"] is False


def test_rerank_tiebreak_max_run_then_da_ratio():
    # 두 후보 모두 대역 안(band_distance=0) → max_run 대역 초과분(≤14) 최소 우선.
    cands = ["A" * 99 + ".", "B" * 99 + "."]
    mfn = lambda c: _metrics(top_ratio=0.5, max_run=10, ratio=0.3) if c[0] == "A" else _metrics(top_ratio=0.5, max_run=18, ratio=0.1)
    res = rr.rerank_candidates(cands, original="X" * 100, dialogue_lines=[], numeric_tokens=[], metrics_fn=mfn)
    assert res["winner_index"] == 0   # A: max_run_over=0 < B: 4

    # max_run 동률(둘 다 대역 안) → |da_ratio − 0.233|(인간 대역 평균 근접) 우선 — 파일럿 2차 보정.
    #   구 '최소 우선'은 과거형이 거의 소멸한 극단 후보(da 0.035)를 뽑아 정독 패배(과잉 내면독백)를 낳았다.
    mfn2 = lambda c: _metrics(top_ratio=0.5, max_run=8, ratio=0.28) if c[0] == "A" else _metrics(top_ratio=0.5, max_run=8, ratio=0.03)
    res2 = rr.rerank_candidates(cands, original="X" * 100, dialogue_lines=[], numeric_tokens=[], metrics_fn=mfn2)
    assert res2["winner_index"] == 0   # A: |0.28−0.233|=0.047 < B: |0.03−0.233|=0.203 — 온건 후보 승


def test_rerank_no_metrics_falls_back_first_valid():
    # 계측 불가(metrics_fn None) → 첫 유효 후보 강등(결측 정직).
    cands = ["A" * 99 + ".", "B" * 99 + "."]
    res = rr.rerank_candidates(cands, original="X" * 100, dialogue_lines=[], numeric_tokens=[], metrics_fn=None)
    assert res["winner_index"] == 0 and "첫 유효 후보" in res["reason"]


# ═══════════ ② ST-14 FIX-2 (무중단 종결 키 run 국소 축 — 하드 실격·리랭크) ═══════════
# 현재형 'ㄴ다'(종결 키 'ᆫ다') 무중단 run 텍스트 — 국소 벽. 대사 리셋을 안 겪게 지문만.
_RUN12 = ("그는 문을 연다. 계단을 내려간다. 손전등을 켠다. 어둠이 물러난다. 발을 디딘다. 앞으로 나아간다. "
          "벽을 민다. 고개를 든다. 숨을 고른다. 손을 편다. 몸을 세운다. 눈을 비빈다.")   # 'ᆫ다' 12연속
_VARIED = ("그는 문을 열었다. 계단이 아득하다. 정말 끝이 있을까? 손전등을 켰다. 어둠이 물러났다. "
           "다시, 침묵. 앞으로 나아갔다.")   # 종결 다양(무중단 run 짧음)


def test_fix2_hard_disqualify_uninterrupted_run():
    """⑤ 무중단 동일 종결 키 run ≥10 이고 *원문보다 악화*인 후보는 하드 실격(상대 기준 — 라이브 보정).

    절대 실격은 '원문 12 벽 → 후보 11(개선)'까지 차단해 전 후보 실격→더 큰 벽 유지를 낳았다(실측).
    깨끗한 원문(_VARIED) 대비 12-run 후보는 실격, 12-run 원문 대비 12-run 후보(동률=무악화)는 통과."""
    dq = rr.evaluate_candidate(_RUN12, original=_VARIED, dialogue_lines=[], numeric_tokens=[])
    assert dq["disqualified"] and dq["uninterrupted_run_max"] >= 10
    assert any("무중단" in r for r in dq["reasons"])
    # 상대 기준: 원문도 같은 12-run 이면(동률·무악화) 이 사유로 실격하지 않는다 — metric_no_harm 이 최종 방어
    same = rr.evaluate_candidate(_RUN12, original=_RUN12, dialogue_lines=[], numeric_tokens=[])
    assert not any("무중단" in r for r in same["reasons"])
    ok = rr.evaluate_candidate(_VARIED, original=_VARIED, dialogue_lines=[], numeric_tokens=[])
    # 다양화 후보는 무중단 run 이 10 미만 → 이 사유로는 실격 아님
    assert (ok["uninterrupted_run_max"] is None or ok["uninterrupted_run_max"] < 10)
    assert not any("무중단" in r for r in ok["reasons"])


def test_fix2_all_run_walls_disqualified_keeps_original_contract():
    """⑤ 깨끗한 원문 대비 전 후보가 무중단 run 벽이면 전부 실격(winner None) — '전 후보 실격→원문 유지' 계약."""
    cands = [_RUN12, _RUN12 + " 바람이 분다."]   # 둘 다 ≥10 run(원문 _VARIED 보다 악화)
    res = rr.rerank_candidates(cands, original=_VARIED, dialogue_lines=[], numeric_tokens=[], metrics_fn=None)
    assert res["winner_index"] is None and res["all_disqualified"] is True


def test_fix2_rerank_prefers_lower_uninterrupted_run():
    """⑤ 리랭크 2순위 — top_ratio 대역 동률이면 무중단 종결 키 run 이 작은 후보가 이긴다(국소 벽 최소화).

    두 후보 모두 대역 안(band_distance=0)·max_run 동률이되, 실제 본문의 무중단 run 이 다르면(A=짧음·B=9연속)
    A 가 승. 무중단 run 은 evaluate_candidate 가 후보 본문에서 직접 계측한다(실측 축)."""
    # A: 종결 다양(무중단 run 짧음) / B: 'ᆫ다' 9연속(실격 임계 10 미만이라 유효하되 run 큼)
    a_text = _VARIED
    b_text = ("문을 연다. 계단을 내려간다. 손전등을 켠다. 어둠이 물러난다. 발을 디딘다. "
              "앞으로 나아간다. 벽을 민다. 고개를 든다. 숨을 고른다.")   # 9연속(<10 유효)
    cands = [a_text, b_text]
    # 전역 계측(top_ratio·max_run)은 동일하게 대역 안으로 고정 → 2순위(무중단 run)로만 갈린다.
    mfn = lambda c: _metrics(top_ratio=0.5, max_run=6, ratio=0.3)
    res = rr.rerank_candidates(cands, original="X" * len(a_text), dialogue_lines=[],
                               numeric_tokens=[], metrics_fn=mfn)
    ea = next(e for e in res["evaluations"] if e["index"] == 0)
    eb = next(e for e in res["evaluations"] if e["index"] == 1)
    assert eb["uninterrupted_run_max"] > ea["uninterrupted_run_max"]   # B 가 더 긴 벽
    assert res["winner_index"] == 0   # 무중단 run 작은 A 승(2순위 발화)


# ═════════════════════════ ③ 하드 실격 ═════════════════════════
def test_disqualify_dialogue_loss():
    orig = "X" * 100
    dl = ['"보존해야 할 대사"']
    ev = rr.evaluate_candidate("이 후보엔 그 대사가 없다" + "y" * 80, original=orig, dialogue_lines=dl, numeric_tokens=[])
    assert ev["disqualified"] and any("대사" in r for r in ev["reasons"]) and ev["dialogue_pres"] < 1.0


def test_disqualify_numeric_missing():
    orig = "X" * 100
    ev = rr.evaluate_candidate("y" * 100, original=orig, dialogue_lines=[], numeric_tokens=["3만", "45도"])
    assert ev["disqualified"] and ev["missing_numerics"] == ["3만", "45도"]


def test_disqualify_length_out_of_band():
    orig = "X" * 100
    short = rr.evaluate_candidate("y" * 50, original=orig, dialogue_lines=[], numeric_tokens=[])   # 0.5x
    longc = rr.evaluate_candidate("y" * 150, original=orig, dialogue_lines=[], numeric_tokens=[])  # 1.5x
    assert short["disqualified"] and longc["disqualified"]
    ok = rr.evaluate_candidate("y" * 99 + ".", original=orig, dialogue_lines=[], numeric_tokens=[])   # 1.0x·말미 종결
    assert not ok["disqualified"]


def test_disqualify_unterminated_tail():
    # 2026-08-18 13화 실측(코드 갭): 프로바이더 절단으로 마지막 문장이 잘린 후보가 전 하드 검사를 통과.
    orig = "X" * 100
    cut = rr.evaluate_candidate("y" * 100, original=orig, dialogue_lines=[], numeric_tokens=[])
    assert cut["disqualified"] and any("말미 미종결" in r for r in cut["reasons"])
    closed = rr.evaluate_candidate("y" * 98 + "다.", original=orig, dialogue_lines=[], numeric_tokens=[])
    assert not any("말미 미종결" in r for r in closed["reasons"])
    quoted = rr.evaluate_candidate("y" * 90 + '"마지막 대사."', original=orig, dialogue_lines=[], numeric_tokens=[])
    assert not any("말미 미종결" in r for r in quoted["reasons"])


def test_dialogue_and_numeric_whitespace_insensitive():
    # 공백 무시 매칭: 대사·수치가 렌더 공백 차이가 있어도 보존으로 인정.
    ev = rr.evaluate_candidate("...다섯시에 천장을... \"첫  대사\"..." + "z" * 80,
                               original="X" * 100, dialogue_lines=['"첫 대사"'], numeric_tokens=["다섯 시"])
    assert ev["dialogue_pres"] == 1.0 and ev["missing_numerics"] == []


# ═════════════════════════ 서비스 스캐폴딩(LLM 0콜) ═════════════════════════
class _FakeExtractorVocab:
    categorical_keys: list = []
    numeric_keys: list = []
    def state_specs(self):
        return []


class _FakeChecker:
    def __init__(self, hard_after=None):
        self._hard_after = hard_after or []
        self.extractor = type("X", (), {"vocab": _FakeExtractorVocab()})()

    def check_text(self, text, ont, chapter, ids):
        # after_text 에 '위반마커' 있으면 하드 위반 추가(가드 불통과 시뮬).
        hard = list(self._hard_after) if "위반마커" in text else []
        return type("_R", (), {"hard": hard, "claims": []})()


class _FakeEntity:
    def __init__(self, name, voice="", aliases=None):
        self.name, self.voice, self.aliases = name, voice, aliases or []


class _FakeOnt:
    def __init__(self, entities=None):
        self.entities = entities or {}
    def scan_present_ids(self, text):
        return list(self.entities.keys())
    def name(self, eid):
        e = self.entities.get(eid)
        return e.name if e else eid


class _FakeGen:
    def _summarize(self, text, prior="", beat=None):
        self.last_beat = beat
        return ("요약", "상세 시놉시스", None)


class _FakeRag:
    def __init__(self):
        self.indexed = []
    def index_chapter(self, n, text):
        self.indexed.append((n, text))


class _FakeBundle:
    def __init__(self, gen, checker, ont):
        self.ontology = ont
        self.checker = checker
        self.generator = gen
        self.rag = _FakeRag()


class _FakeProviderUsage:
    """sess.provider.usage 스텁 — 가드 check_text·재요약 토큰 계상(usage_delta additive) 경로 검증용."""
    def as_dict(self):
        return {"chat_calls": 0, "chat_tokens": 0, "embed_calls": 0, "embed_items": 0}


class _FakeSession:
    def __init__(self, gen, checker, ont):
        self.lock = threading.Lock()
        self.bundle = _FakeBundle(gen, checker, ont)
        self.provider = type("P", (), {"usage": _FakeProviderUsage()})()
    def snapshot_into(self, state):
        pass


class _FakeSessions:
    def __init__(self, sess):
        self._sess = sess
    def get_or_create(self, state):
        return self._sess
    def evict(self, pid):
        pass


ORIG = ("밤새 봉투를 뜯지 않았다. " * 8 + '"커피 한 잔 타줄 수 있나." '
        "이거 3만 원. 45도로 잘렸다. 세 갈래 균열. " * 3)


def _make_svc(candidates, *, checker=None, style_pov="third_limited", detail="사실 뼈대: 서도현이 윤재석에게 값을 매긴다.",
              entities=None, tmpdir=None, read_gate=False, finalize=False, no_harm=False):
    """candidates: provider 가 매 chat 호출마다 순서대로 반환할 후보 리스트(BoN 시뮬).

    finalize(기본 False): ST-14 FIX-3 최종화 스택(reflow→휴머나이즈)을 끄고(paragraph_reflow·humanize·style_repair
    OFF) 채택 본문이 승자 원문 그대로가 되게 한다 — 흐름 테스트(revision·undo·preference·verification)는 최종화와
    직교하므로 격리한다. 최종화 스택 자체는 전용 테스트(test_fix3_finalize_pipeline_order)가 스텁 호출 로그로 검증.
    no_harm(기본 False): ST-14 지표 무해 가드 격리 — ORIG/_GOOD 픽스처는 의도적 단조 텍스트라 가드가 흐름 테스트를
    전부 기각시킨다(read_gate 격리와 동일 관행). 가드 자체는 전용 테스트(test_metric_no_harm_*)가 ON 으로 검증."""
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService

    ont = _FakeOnt(entities if entities is not None else {"a": _FakeEntity("서도현", voice="값을 매기는 렌즈"),
                                                          "b": _FakeEntity("윤재석")})
    gen = _FakeGen()
    checker = checker or _FakeChecker()
    sess = _FakeSession(gen, checker, ont)

    tmp = Path(tmpdir or tempfile.mkdtemp(prefix="st12c_"))
    # rerender_read_gate 기본 False: 흐름 테스트는 게이트 밖 축(리랭크·가드·revision·preference)을 격리 검증.
    #   정독 게이트 자체는 전용 테스트(test_read_gate_*)가 make_judge 를 monkeypatch 해 검증한다.
    _upd = {"data_dir": str(tmp), "rerender_bon_n": max(1, len(candidates)),
            "rerender_read_gate": read_gate, "rerender_metric_no_harm": no_harm}
    if not finalize:   # FIX-3 최종화 스택 OFF(격리) — 채택 본문 = 승자 원문(reflow·휴머나이즈 무접촉)
        _upd.update({"paragraph_reflow": False, "humanize": False, "style_repair": False})
    settings = get_settings().model_copy(update=_upd)
    svc = CopilotService(settings, FilesystemProjectRepository(tmp))
    svc.sessions = _FakeSessions(sess)

    # provider 스텁: create_role_provider 를 monkeypatch 해 매 chat 호출이 candidates 를 순차 반환.
    class _Usage:
        def __init__(self):
            self.chat_calls = 0
            self.chat_tokens = 0
        def as_dict(self):
            return {"chat_calls": self.chat_calls, "chat_tokens": self.chat_tokens,
                    "embed_calls": 0, "embed_items": 0}

    class _StubProvider:
        def __init__(self, outs):
            self._outs = list(outs)
            self._i = 0
            self.usage = _Usage()
        def chat(self, messages, temperature=0.0, max_tokens=0):
            out = self._outs[self._i] if self._i < len(self._outs) else ""
            self._i += 1
            self.usage.chat_calls += 1
            self.usage.chat_tokens += 500   # 콜당 토큰 누적(usage_delta·usage_by_stage 계측 검증)
            return out
    import novelcopilot.services.copilot as cop
    svc._stub_provider = _StubProvider(candidates)
    svc._orig_crp = cop.create_role_provider
    cop.create_role_provider = lambda settings, spec: svc._stub_provider

    style = StyleSpec(pov=style_pov)
    world = WorldConfig(title="t", synopsis="s", style=style)
    ch = ChapterRecord(chapter=1, title="1화", status=ChapterStatus.FINALIZED,
                       text=ORIG, detail_synopsis=detail, summary="한 줄 요약")
    state = ProjectState(id="p1", seed=ProjectSeed(title="t"), world=world,
                         current_chapter=1, chapters=[ch])
    svc.repo.save(state)
    return svc, tmp


def _restore(svc):
    import novelcopilot.services.copilot as cop
    if getattr(svc, "_orig_crp", None):
        cop.create_role_provider = svc._orig_crp


# 유효 후보: 원문과 길이 유사(0.8~1.3x) + 대사·수치 보존.
#   서비스가 채택 시 out.strip()+sanitize_meta 로 정규화하므로 후보 자체를 정규화된 형태로 둔다(채택본=이 문자열).
#   ST-14 라이브 보정 뒤 주의: 파편은 run 을 끊지 않으므로 대체 문장은 원문과 같은 '았다' 키를 유지해야
#   무중단 run 이 8(<실격 10)로 남아 유효 후보가 된다('있었다'(었다 키)로 바꾸면 '잘렸다'와 이어져 11-run 실격).
from novelcopilot.engine.harness import sanitize_meta as _san
_GOOD = _san(ORIG.replace("밤새 봉투를 뜯지 않았다.", "봉투는 밤새 그대로 남았다.").strip())


# ═══════════ ④-0 ST-14 라이브 보정 (지표 무해 가드 · repair 모드) ═══════════
def test_metric_no_harm_unit():
    """무해 가드 단위: 대역 거리 후퇴/무중단 run 후퇴 각각 기각, 동률 허용, 결측 비교 생략."""
    ok, r = rr.metric_no_harm({"top_ratio": 0.6}, {"top_ratio": 0.75}, 5, 5)   # 대역 거리 후퇴
    assert not ok and any("대역" in x for x in r)
    ok, r = rr.metric_no_harm({"top_ratio": 0.5}, {"top_ratio": 0.5}, 5, 9)    # run 후퇴
    assert not ok and any("run 후퇴" in x for x in r)
    ok, _ = rr.metric_no_harm({"top_ratio": 0.7}, {"top_ratio": 0.7}, 8, 8)    # 동률 = 무해
    assert ok
    ok, _ = rr.metric_no_harm({"top_ratio": 0.9}, {"top_ratio": 0.5}, 12, 3)   # 개선 = 무해
    assert ok
    ok, _ = rr.metric_no_harm(None, {"top_ratio": 0.9}, None, 15)              # 결측 = 비교 생략(기각 금지)
    assert ok


def test_metric_no_harm_rejects_regressing_output():
    """흐름: 가드 ON 이면 원문보다 계측이 후퇴한 최종본은 기각 — 원문 유지(실측 구멍 재현).

    repair 모드 + 최종화 스텁이 '기다렸다' 12연속을 덧붙인 본문을 반환 → 무중단 run 이 원문(8)보다
    후퇴(≥12) → 지표 무해 가드가 결정론 기각. (BoN 실격 축과 독립적으로 가드 자체를 검증.)"""
    svc, _ = _make_svc([], no_harm=True)
    worse = ORIG + " " + "그리고 기다렸다. " * 12   # '었다' 키 12연속 — urm 8→12+ 후퇴
    svc._finalize_rerender_text = lambda sess, ont, ch_no, text, prev: (
        _san(worse.strip()), {"reflowed": False, "humanize": [], "style_repairs": []})
    try:
        res = svc.rerender_chapter("p1", 1, mode="repair")
    finally:
        _restore(svc)
    assert res["adopted"] is False and "지표 무해 가드" in res["reason"]
    assert svc.repo.get("p1").chapter(1).text == ORIG
    assert len(svc.repo.get("p1").chapter(1).revisions) == 0


def test_repair_mode_adopts_finalize_output():
    """repair 모드: BoN 0콜로 현재 본문을 최종화 스택에만 태워 채택(revision·summary 불변·수리 내역 기록)."""
    svc, _ = _make_svc([])
    calls = {}

    def _stub_finalize(sess, ont, chapter_no, text, prev_texts):
        calls["input"] = text
        return _GOOD, {"reflowed": True, "humanize": [{"category": "N-4", "changed": True}], "style_repairs": []}

    svc._finalize_rerender_text = _stub_finalize
    try:
        res = svc.rerender_chapter("p1", 1, mode="repair")
    finally:
        _restore(svc)
    assert res["adopted"] is True and res["finalize_repairs"]["humanize_spans"] == 1
    assert calls["input"] == ORIG                      # 입력 = 현재 본문(재추첨 아님)
    assert svc._stub_provider.usage.chat_calls == 0    # BoN LLM 0콜
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text == _GOOD
    assert ch.summary == "한 줄 요약"                   # repair 는 재요약 생략(사실 불변 가드 전제)
    assert ch.revisions[-1].directive == "[ST-14 문체 수리]"
    assert ch.revisions[-1].before_text == ORIG        # undo 가능(원문 보존)


def test_repair_mode_no_change_not_adopted():
    """repair 모드 무변경(스팬 없음/전부 폴백) = 정직 미채택 — revision 생성 없음·본문 불변."""
    svc, _ = _make_svc([])
    svc._finalize_rerender_text = lambda sess, ont, ch_no, text, prev: (text, {"reflowed": False,
                                                                               "humanize": [], "style_repairs": []})
    try:
        res = svc.rerender_chapter("p1", 1, mode="repair")
    finally:
        _restore(svc)
    assert res["adopted"] is False and "무변경" in res["reason"]
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text == ORIG and len(ch.revisions) == 0


# ═════════════════════════ ④ 전 후보 실격 → 원문 유지 ═════════════════════════
def test_all_disqualified_keeps_original():
    svc, _ = _make_svc(["짧은 실격 후보 하나", "또 짧은 실격 후보 둘", "세 번째 짧은 후보"])
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is False and "전 후보 실격" in res["reason"]
    assert svc.repo.get("p1").chapter(1).text == ORIG   # 원문 불변
    assert len(svc.repo.get("p1").chapter(1).revisions) == 0


# ═════════════════════════ ⑤ 가드 불통과 → 채택 금지 ═════════════════════════
def test_guard_fail_keeps_original():
    from novelcopilot.domain.types import Violation, SignalGrade
    hard = [Violation(kind="status_conflict", entity="a", grade=SignalGrade.QUASI, text="사망 후 등장")]
    checker = _FakeChecker(hard_after=hard)
    # 유효 후보이지만 '위반마커' 로 after check 가 하드 위반을 냄 → G-A 불통과.
    guard_cand = _GOOD + " 위반마커다."
    svc, _ = _make_svc([guard_cand, guard_cand, guard_cand], checker=checker)
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is False and "가드 불통과" in res["reason"]
    assert svc.repo.get("p1").chapter(1).text == ORIG   # 원문 불변
    assert len(svc.repo.get("p1").chapter(1).revisions) == 0


# ═════════════════════════ ⑥ 채택 + undo 왕복 ═════════════════════════
def test_adopt_records_revision_and_undo_roundtrip():
    svc, _ = _make_svc([_GOOD, _GOOD, _GOOD])
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is True
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text == _GOOD                              # 본문 교체
    assert len(ch.revisions) == 1
    rev = ch.revisions[-1]
    assert rev.directive == "[ST-12c 비앵커 재실현]"
    assert rev.before_text == ORIG and rev.after_text == _GOOD
    assert rev.guardrail_passed is True
    # undo 왕복 — 원문 복원
    svc.undo_revision("p1", 1)
    ch2 = svc.repo.get("p1").chapter(1)
    assert ch2.text == ORIG and ch2.revisions[-1].reverted is True


def test_no_skeleton_refuses_honestly():
    # detail_synopsis·summary 둘 다 비면 패스 거부(정직 사유·원문 불변·채택 없음).
    svc, _ = _make_svc([_GOOD], detail="")
    st = svc.repo.get("p1")
    ch = st.chapter(1); ch.summary = ""; svc.repo.save(st)
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is False and "뼈대" in res["reason"]
    assert svc.repo.get("p1").chapter(1).text == ORIG


# ═════════════════════════ ⑦ preference jsonl append(프로젝트 JSON 불변) ═════════════════════════
def test_preference_jsonl_appended_and_project_json_unchanged():
    svc, tmp = _make_svc([_GOOD, "짧은 탈락 후보", "또 짧은 탈락 후보 둘"])
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is True
    pf = Path(tmp) / "preference_pairs.jsonl"
    assert pf.exists()
    rows = [json.loads(ln) for ln in pf.read_text(encoding="utf-8").splitlines() if ln.strip()]
    kinds = [r["kind"] for r in rows]
    assert "rerender" in kinds            # 재실현 전→후 쌍
    assert kinds.count("bon_reject") == 2  # 탈락 후보 2건
    rerender_row = next(r for r in rows if r["kind"] == "rerender")
    assert rerender_row["before"] == ORIG and rerender_row["after"] == _GOOD
    assert rerender_row["pid"] == "p1" and rerender_row["chapter"] == 1
    # 프로젝트 JSON 에는 preference 쌍이 들어가지 않는다(비대화 방지).
    proj_json = (Path(tmp) / "projects" / "p1.json")
    if proj_json.exists():
        blob = proj_json.read_text(encoding="utf-8")
        assert "preference_pairs" not in blob and "bon_reject" not in blob


# ═══════════ ⑥ ST-14 FIX-3 (관문 단일화 — reflow→휴머나이즈→가드→정독 순) ═══════════
def test_fix3_finalize_pipeline_order():
    """⑥ 관문 단일화 순서 — 재실현 승자 확정 → 최종화 스택(reflow→휴머나이즈) → 가드 → 정독 게이트.

    실 LLM 콜 0(스텁) — 각 단계를 monkeypatch 해 호출 순서를 로그로 검증한다. 최종화가 가드·정독 *앞*에
    돌아 심사 대상이 '최종본'인지(R1 수리)만 확인. 최종화 내부 휴머나이즈 실 콜은 스텁으로 대체(훅 계약)."""
    svc, _ = _make_svc([_GOOD, _GOOD, _GOOD], read_gate=True)
    log: list[str] = []
    # 최종화 스택 — 스텁(호출 순서 기록 + 본문에 마커 부착해 '가드·정독 대상=최종본' 확인)
    FINAL_MARK = " [FINALIZED]"
    def _fake_finalize(sess, ont, chapter_no, text, prev_texts):
        log.append("finalize")
        return text + FINAL_MARK, {"reflowed": True, "humanize": [{"changed": True}], "style_repairs": []}
    def _fake_guard(before_text, after_text, before_res, ids, ont, checker, chapter_no):
        log.append("guard")
        assert after_text.endswith(FINAL_MARK)   # 가드 대상 = 최종본(최종화 반영)
        return ({"passed": True, "G_A_passed": True, "G_B_passed": True, "length_ok": True,
                 "new_hard": [], "claim_changes": [], "claim_flaps": [], "new_keys_advisory": [], "reason": ""},
                type("_R", (), {"hard": []})())
    def _fake_read_gate(before_text, after_text):
        log.append("read_gate")
        assert after_text.endswith(FINAL_MARK)   # 정독 대상 = 최종본
        return {"adopt": True, "reason": "", "rounds": [], "judge": {}, "usage": {}}
    svc._finalize_rerender_text = _fake_finalize
    svc._guardrail = _fake_guard
    svc._rerender_read_gate = _fake_read_gate
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is True
    assert log == ["finalize", "guard", "read_gate"], f"순서 위반: {log}"
    ch = svc.repo.get("p1").chapter(1)
    assert ch.text.endswith(FINAL_MARK)   # 채택 본문 = 최종화 완료본


def test_fix3_finalize_repairs_recorded_in_revision():
    """⑦ 최종화 수리 내역이 채택 revision payload·반환에 additive 기록(은폐 금지)."""
    svc, _ = _make_svc([_GOOD, _GOOD, _GOOD])
    def _fake_finalize(sess, ont, chapter_no, text, prev_texts):
        return text, {"reflowed": True, "humanize": [{"changed": True}, {"changed": False}],
                      "style_repairs": []}
    svc._finalize_rerender_text = _fake_finalize
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is True
    # 반환 payload
    fr = res["finalize_repairs"]
    assert fr["reflowed"] is True and fr["humanize_spans"] == 2 and fr["style_repair_spans"] == 0
    # revision 레코드(영속)
    rev = svc.repo.get("p1").chapter(1).revisions[-1]
    assert rev.finalize_repairs.get("reflowed") is True
    assert len(rev.finalize_repairs.get("humanize") or []) == 2


# ═════════════════════════ ⑧ 파생물 정합 호출 ═════════════════════════
def test_derivative_coherence_summarize_beat_and_verification():
    # gen_context 의 beat 를 _summarize 에 관통(생성 경로 동형) + verification 재집계 + rag 재색인.
    svc, _ = _make_svc([_GOOD, _GOOD, _GOOD])
    st = svc.repo.get("p1")
    ch = st.chapter(1)
    ch.gen_context = {"draft": {"beat": {"summary": "값을 매긴다", "key_events": ["실사 통보", "조립 의뢰"]}}}
    svc.repo.save(st)
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is True
    gen = svc.sessions.get_or_create(svc.repo.get("p1")).bundle.generator
    assert gen.last_beat == {"summary": "값을 매긴다", "key_events": ["실사 통보", "조립 의뢰"]}   # beat 관통
    ch2 = svc.repo.get("p1").chapter(1)
    assert ch2.verification and ch2.verification.get("chapter") == 1   # verification 재집계(SSOT)
    assert ch2.usage_by_stage.get("rerender", 0) > 0                   # rerender 스테이지 계측
    rag = svc.sessions.get_or_create(svc.repo.get("p1")).bundle.rag
    assert (1, _GOOD) in rag.indexed                                    # RAG 재색인


def test_adopt_preserves_persisted_gate_verdict():
    # ⑧ verification 재집계가 기존 gate 실판정을 소실시키지 않는다(b27f7bd 허위 결측 차단 계약).
    svc, _ = _make_svc([_GOOD, _GOOD, _GOOD])
    st = svc.repo.get("p1")
    ch = st.chapter(1)
    ch.verification = {"chapter": 1, "gate": {"verdict": "PASS", "rounds": 2}}   # 러너/제품 게이트 판정 영속
    svc.repo.save(st)
    try:
        res = svc.rerender_chapter("p1", 1)
    finally:
        _restore(svc)
    assert res["adopted"] is True
    ch2 = svc.repo.get("p1").chapter(1)
    assert ch2.verification.get("gate") == {"verdict": "PASS", "rounds": 2}   # gate 이월(MISSING 로 덮이지 않음)


def test_first_person_injects_voice_card_third_person_skips():
    # 1인칭+주인공 voice → 서술자 카드 주입(프롬프트에 반영), 3인칭 → 카드 없음(하위호환).
    # 프롬프트 조립 자체는 순수 함수로 직접 검증.
    sys1, _ = rr.build_rerender_prompt(narrator_card="값을 매기는 렌즈로 본다", skeleton="뼈대",
                                       dialogue_lines=[], numeric_tokens=[], proper_nouns=[])
    assert "서술자 음성" in sys1 and "값을 매기는 렌즈" in sys1
    sys2, _ = rr.build_rerender_prompt(narrator_card="", skeleton="뼈대",
                                       dialogue_lines=[], numeric_tokens=[], proper_nouns=[])
    assert "서술자 음성" not in sys2


# ═════════════════════════ 정독 게이트(파일럿 2차 보정 2026-07-14) ═════════════════════════
#   근거: ch1 실측 — kiwi 대역 진입(0.417) + 정독 쌍대 0-2 완패("과잉 내면독백") = '대역≠품질' 재현.
#   채택 조건에 1차 척도(정독)를 배선: 원문 양순서 완승만 기각(no-harm) — 판정 규칙은 엔진 순수 함수.
def test_read_gate_verdict_rules():
    V = rr.read_gate_verdict
    assert V([{"pick_original": True}, {"pick_original": True}])["adopt"] is False    # 원문 완승 → 기각
    assert V([{"pick_original": True}, {"pick_original": False}])["adopt"] is True    # 1-1 → 채택
    assert V([{"pick_original": False}, {"pick_original": False}])["adopt"] is True   # 재실현 우세 → 채택
    assert V([{"pick_original": None}, {"pick_original": None}])["adopt"] is False    # 전건 판정 불가 → 보수 기각
    assert V([{"pick_original": None}, {"pick_original": True}])["adopt"] is True     # 완패 입증 불가 → 채택
    s, u = rr.build_read_gate_prompt("원문X", "후보Y")
    assert "초비판" in s and "판본 ㄱ" in u and "keep_reading" in u


class _JudgeStub:
    """스크립트 심사 — chat_json 이 keep_reading 값을 순서대로 반환. make_judge 대체용(LLM 0콜)."""
    class _U:
        def as_dict(self):
            return {"chat_calls": 0, "chat_tokens": 0, "embed_calls": 0, "embed_items": 0}

    def __init__(self, picks):
        self._picks = list(picks)
        self._i = 0
        self.usage = self._U()

    def chat_json(self, messages, temperature=0.0, max_tokens=0):
        # SP-3(감사 M2): 인용 의무 계약 — 스텁도 각 판본 원문에서 인용을 만들어 cite_ok 대조를 통과시킨다
        #   (인용 없는 응답은 이제 '판정 불가' 처리되는 것이 계약이므로, 유효 판정 스텁은 인용을 실어야 한다).
        k = self._picks[self._i] if self._i < len(self._picks) else "무승부"
        self._i += 1
        user = messages[-1]["content"]
        gx = user.split("[판본 ㄱ]\n", 1)[1].split("\n\n[판본 ㄴ]\n", 1)[0]
        gy = user.split("\n\n[판본 ㄴ]\n", 1)[1].rsplit("\n\n다음을", 1)[0]
        return {"keep_reading": k, "ㄱ_인용": gx[:40], "ㄴ_인용": gy[:40], "why": "스텁"}


def _with_judge_stub(picks, fn):
    """make_judge 를 스텁으로 치환하고 fn 실행(수동 monkeypatch — 표준 main() 직접 실행 호환)."""
    import novelcopilot.engine.chapter_gate as cg
    orig = cg.make_judge
    cg.make_judge = lambda settings, prose: (_JudgeStub(picks), "stub:judge")
    try:
        return fn()
    finally:
        cg.make_judge = orig


def test_read_gate_service_rejects_on_original_sweep():
    # 라운드1(원문=ㄱ): 'ㄱ' = 원문 승 / 라운드2(원문=ㄴ): 'ㄴ' = 원문 승 → 완승 → 채택 금지·원문 불변
    svc, _ = _make_svc([_GOOD, _GOOD, _GOOD], read_gate=True)
    try:
        res = _with_judge_stub(["ㄱ", "ㄴ"], lambda: svc.rerender_chapter("p1", 1))
    finally:
        _restore(svc)
    assert res["adopted"] is False and "정독 게이트" in res["reason"]
    assert "완승" in res["read_gate"]["reason"]
    assert svc.repo.get("p1").chapter(1).text == ORIG
    assert len(svc.repo.get("p1").chapter(1).revisions) == 0


def test_read_gate_service_adopts_on_split():
    # 라운드1 'ㄱ'(원문 승) / 라운드2 'ㄱ'(재실현 승) = 1-1 → 채택(완패 아님) + read_gate 투명 페이로드
    svc, _ = _make_svc([_GOOD, _GOOD, _GOOD], read_gate=True)
    try:
        res = _with_judge_stub(["ㄱ", "ㄱ"], lambda: svc.rerender_chapter("p1", 1))
    finally:
        _restore(svc)
    assert res["adopted"] is True
    assert res["read_gate"]["adopt"] is True and len(res["read_gate"]["rounds"]) == 2
    assert svc.repo.get("p1").chapter(1).text == _GOOD


def main() -> int:
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"[OK] {t.__name__}")
        except Exception:
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} ST-12c tests passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
