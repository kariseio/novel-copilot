# -*- coding: utf-8 -*-
"""ST-3 회귀 가드 — 발행 경계 문단 조판(내용 무손실 순수 분할). 실 LLM 0콜.

실측 근거(st1b_reference_style): 실작품 문단당 1.7~2.0문장 vs 우리 3.3 — '가벼움'은 짧은 문장이 아니라
'짧은 문단'에서 온다. reflow_paragraphs 는 발행 경계에서
  ① 3문장 초과 지문 문단을 문장 경계로 1~2문장씩 분할,
  ② 지문과 한 문단에 섞인 대사 라인("…")을 단독 문단으로 분리한다.
재배열·삭제·병합 없이 '분할만' — 비공백 문자 집합·순서 불변(조판=줄바꿈, 판정기·자동 재작성 아님).
따옴표 스팬 내부 종결부호로는 오분할하지 않는다("안녕. 반가워." = 한 문장). 토글 OFF = 바이트 동일.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_st3_paragraph_reflow.py
"""
import sys, pathlib, re, json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))       # tools/ → style_lightness_baseline 임포트

from novelcopilot.engine.textfmt import reflow_paragraphs, _split_sentences, _reflow_paragraph


def _nows(s: str) -> str:
    """공백 전부 제거 — 조판 무손실 property 비교용(비공백 문자 집합·순서)."""
    return re.sub(r"\s", "", s)


def _paras(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


# ---------- ③ 내용 무손실 property(비공백 문자·순서 불변) ----------
def test_lossless_over3_narration():
    t = "가나다 하나. 라마바 둘. 사아자 셋. 차카타 넷. 파하가 다섯."
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r), f"비공백 문자 손실: {r!r}"


def test_lossless_mixed_dialogue():
    t = '철수는 문을 열었다. "누구세요?" 밖에는 아무도 없었다. 그는 뒤를 돌아봤다.'
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r)


def test_lossless_multi_paragraph_with_separators():
    # 여러 문단·중복 빈 줄(\n\n\n) 혼재 — 미변경 문단·구분자 보존, 변경 문단만 재조판
    t = "짧다.\n\n네 문장 지문. 두번째. 세번째. 네번째.\n\n\n다른 문단 홀로."
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r)


# ---------- 따옴표 스팬 보존(내부 종결부호 오분할 금지) ----------
def test_quote_internal_period_not_split():
    # "안녕. 반가워. 잘 지냈어?" 는 종결부호가 3개지만 따옴표 내부라 한 문장(뒤 지문까지 attribution 으로 묶임)
    p = '그가 말했다. "안녕. 반가워. 잘 지냈어?" 그녀가 웃었다.'
    sents = _split_sentences(p)
    assert sents == ['그가 말했다.', '"안녕. 반가워. 잘 지냈어?" 그녀가 웃었다.'], sents


def test_quote_span_survives_intact_in_output():
    # 대사 스팬 전체가 어느 출력 문단에도 쪼개지지 않고 통째로 살아 있어야
    t = '그는 걸었다. 멈췄다. 돌아봤다. "여긴 어디지? 아무도 없잖아." 바람이 불었다.'
    r = reflow_paragraphs(t)
    assert '"여긴 어디지? 아무도 없잖아."' in r, f"대사 스팬 파손: {r!r}"


def test_dialogue_with_attribution_stays_one_paragraph():
    # 대사+발화자 표지가 한 문장이면 분리하지 않는다(reference '대사 앞뒤 행동 비트' 결 보존)
    t = '"안녕," 하고 그가 손을 내밀며 말했다.'
    r = reflow_paragraphs(t)
    assert r == t, f"attribution 문장을 쪼갬: {r!r}"


# ---------- ① 3문장 초과 지문 문단 분할 ----------
def test_over3_narration_split_to_le2():
    t = "문장 하나. 문장 둘. 문장 셋. 문장 넷. 문장 다섯."
    r = reflow_paragraphs(t)
    paras = _paras(r)
    assert len(paras) >= 3, f"분할 안 됨: {paras}"
    for p in paras:                                  # 결과 문단은 전부 ≤2문장(3문장 초과 0)
        assert len(_split_sentences(p)) <= 2, f"3문장 초과 잔존: {p!r}"


def test_exactly_3_sentence_narration_unchanged():
    # '3문장 초과'만 대상 — 정확히 3문장 순지문은 무변경(이미 짧음)
    t = "하나다. 둘이다. 셋이다."
    assert reflow_paragraphs(t) == t


def test_short_narration_bytewise_noop():
    t = "첫 문장이다. 둘째 문장이다.\n\n셋째 문단 홀로 있다."
    assert reflow_paragraphs(t) == t                 # ≤3 순지문·단문 문단 = 바이트 동일


# ---------- ② 지문과 섞인 대사 라인 단독 문단화 ----------
def test_mixed_dialogue_separated_into_own_paragraph():
    t = '철수는 문을 열었다. "누구세요?" 밖에는 아무도 없었다.'
    r = reflow_paragraphs(t)
    paras = _paras(r)
    # 지문과 대사가 서로 다른 문단으로 분리(대사 스팬 포함 문단이 지문 첫 문장과 한 문단에 있지 않음)
    assert paras[0] == "철수는 문을 열었다.", paras
    assert any('"누구세요?"' in p for p in paras[1:]), paras


def test_two_sentence_pure_narration_unchanged():
    # 대사 없는 2문장 문단은 혼재 아님 → 무변경(과분절 방지)
    t = "그는 걸었다. 바람이 불었다."
    assert reflow_paragraphs(t) == t


# ---------- 토글 OFF = 바이트 동일(하네스 게이트 계약) ----------
def test_toggle_semantics_off_is_identity():
    # 하네스 게이트: if settings.paragraph_reflow → reflow, else 원문. OFF 경로는 입력 그대로.
    from types import SimpleNamespace
    t = "문장 하나. 문장 둘. 문장 셋. 문장 넷."
    def _publish(text, settings):
        if getattr(settings, "paragraph_reflow", True):
            return reflow_paragraphs(text)
        return text
    assert _publish(t, SimpleNamespace(paragraph_reflow=False)) == t            # OFF = 바이트 동일
    assert _publish(t, SimpleNamespace(paragraph_reflow=True)) != t             # ON = 조판 적용
    assert _nows(_publish(t, SimpleNamespace(paragraph_reflow=True))) == _nows(t)


def test_config_default_on():
    from novelcopilot.config import Settings
    assert Settings().paragraph_reflow is True


# ---------- 조판 무개입 불변식(대사 병합·빈 줄 정책 변경 없음) ----------
def test_pure_dialogue_block_le3_unchanged():
    # ≤3 순대사 블록은 무변경(대사 '병합'이나 재배치 금지 — 조판 이외 개입 없음)
    t = '"안녕." "잘 가."'
    assert reflow_paragraphs(t) == t


def test_empty_and_whitespace_noop():
    assert reflow_paragraphs("") == ""
    assert reflow_paragraphs("   \n\n   ") == "   \n\n   "


def test_ellipsis_and_triple_dots_boundary():
    # 말줄임(…)·마침표 3연(...) 뒤 공백은 경계, 부호는 전부 보존
    t = "그는 망설였다... 결국 문을 열었다. 안은 어두웠다. 그리고 조용했다."
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r)
    assert "..." in r


# ---------- 보호 스팬: 비대사 인라인 스팬(diegetic 괄호/브래킷·강조) 내부 미분할(B-24 '절대 불변') ----------
def test_system_bracket_span_not_split():
    # 시스템 상태창 [ … ] 내부 문장부호로 문단이 쪼개지면 안 됨(B-24·마커 고아화 방지). 주변 문장이 많아 reflow 가 트리거돼도 스팬은 통째.
    t = "그는 로그를 봤다. [레벨업! 힘 +5. 민첩 +3.] 놀라웠다. 정말로 놀라웠다. 믿기지 않았다."
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r)
    assert "[레벨업! 힘 +5. 민첩 +3.]" in r, f"시스템 브래킷 분할됨: {r!r}"
    for p in _paras(r):                              # 어느 문단에도 짝 잃은 브래킷만 남지 않음(고아 마커 = 리더 렌더 깨짐)
        assert p.count("[") == p.count("]"), f"고아 브래킷: {p!r}"


def test_narrative_paren_span_not_split():
    t = "알림이 울렸다. (퀘스트 완료. 보상 지급.) 그는 기뻤다. 정말 기뻤다. 하늘을 봤다."
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r)
    assert "(퀘스트 완료. 보상 지급.)" in r, f"서사 괄호 분할됨: {r!r}"
    for p in _paras(r):
        assert p.count("(") == p.count(")"), f"고아 괄호: {p!r}"


def test_emphasis_span_not_split():
    # **강조** 안의 다문장 종결부호로 분할되면 ** 마커가 두 문단에 고아로 남는다 — 리더 mdToHtml 가 리터럴 별표로 렌더.
    t = "앞 문장이다. **볼드 시작. 볼드 끝**. 뒤 문장이다. 그리고 또 하나. 마지막이다."
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r)
    assert "**볼드 시작. 볼드 끝**" in r, f"강조 스팬 분할됨: {r!r}"
    for p in _paras(r):
        assert p.count("**") % 2 == 0, f"고아 볼드 마커: {p!r}"


# ---------- 줄 구조 세그먼트(내부 개행)는 병합·재조판 금지(병합 금지·빈 줄 정책 불변) ----------
def test_status_window_newlines_unchanged():
    # 상태창(라벨 블록): 내부 개행이 공백으로 병합되거나 빈 줄이 삽입되면 안 됨 — 바이트 동일
    t = "[상태창]\n이름: 김철수\n레벨: 3\n직업: 무직"
    assert reflow_paragraphs(t) == t, f"상태창 줄 병합/변형됨: {reflow_paragraphs(t)!r}"


def test_verse_newlines_unchanged():
    # 운문(종결부호 없는 행): 줄이 공백으로 병합되면 안 됨
    t = "바람이 분다\n꽃이 진다\n밤이 온다\n별이 뜬다"
    assert reflow_paragraphs(t) == t


def test_punctuated_list_newlines_unchanged():
    # 종결부호가 있어도 내부 개행(줄 구조)이면 재조판 안 함 — 목록 항목이 공백으로 병합되면 안 됨
    t = "첫째, 준비한다.\n둘째, 실행한다.\n셋째, 검토한다.\n넷째, 마무리한다."
    assert reflow_paragraphs(t) == t


def test_per_line_dialogue_block_4plus_unchanged():
    # 행별 대사(내부 개행)는 4행 이상이어도 그대로 — 대사 병합·문단 재배치 없음
    t = '"안녕."\n"잘 가."\n"또 봐."\n"응."'
    assert reflow_paragraphs(t) == t


# ---------- 3작 실데이터: para_over3sent_ratio 목표 밴드(읽기 전용·저장 금지) ----------
def test_real_data_over3_ratio_drops_to_band():
    import pytest
    proj = pathlib.Path(__file__).resolve().parents[1] / "data" / "projects"
    from style_lightness_baseline import lightness_metrics
    work_ids = ["6cda5ee883e0", "8f7e8cb966a2", "7cba74b80209"]
    files = [proj / f"{w}.json" for w in work_ids if (proj / f"{w}.json").exists()]
    if not files:
        pytest.skip("실데이터 프로젝트 파일 없음 — 측정 스킵")
    afters = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        chs = [c for c in d.get("chapters", [])
               if c.get("status") == "FINALIZED" and (c.get("text") or "").strip()]
        for c in chs[:8]:
            t = c["text"]
            r = reflow_paragraphs(t)
            assert _nows(t) == _nows(r), f"실데이터 무손실 실패 {f.name} ch{c.get('chapter')}"   # 저장은 안 함(읽기 전용)
            afters.append(lightness_metrics(r)["para_over3sent_ratio"])
    assert afters, "측정 회차 없음"
    assert max(afters) <= 0.15, f"목표 밴드 초과: max={max(afters):.3f}"


# ---------- RF-1(2026-07-16) 자문자답 커플릿 응집 — 고정 2-stride 절단 결함 수리 ----------
# 실측: "예산? / 없다. 결재선? / 나 하나."가 문단 3조각(5작품 계통 재발). 불변식: 지문 run 안에서
# '?'-종결 문장은 바로 뒤 지문 문장(비질문)과 항상 같은 출력 문단에 든다. 문단 ≤2문장 계약 불변.
def test_rf1_qa_couplet_stays_together():
    t = "그날부터 사흘, 나는 썼다. 통구이를 시켰고 포도주를 시켰다. 예산? 없다. 결재선? 나 하나. 그거면 충분한 근거였다."
    r = reflow_paragraphs(t)
    paras = _paras(r)
    assert any("예산? 없다." in p for p in paras), f"질문-답 찢김: {paras}"
    assert any("결재선? 나 하나." in p for p in paras), f"질문-답 찢김: {paras}"
    for p in paras:
        assert len(_split_sentences(p)) <= 2                 # 조판 계약(≤2문장) 불변
    assert _nows(t) == _nows(r)                              # 무손실


def test_rf1_consecutive_couplets_each_intact():
    t = "서론 문장이다. 예산? 없다. 결재선? 나 하나. 마무리 문장이다. 덧붙임 문장이다."
    r = reflow_paragraphs(t)
    paras = _paras(r)
    assert any(p.strip() == "예산? 없다." for p in paras)
    assert any(p.strip() == "결재선? 나 하나." for p in paras)


def test_rf1_trailing_question_without_answer_unforced():
    # run 말미의 답 없는 질문 → 병합 강제 없음(자연 종료), 무손실만 보장
    t = "문장 하나. 문장 둘. 문장 셋. 문장 넷. 그런데 왜?"
    r = reflow_paragraphs(t)
    assert _nows(t) == _nows(r)
    for p in _paras(r):
        assert len(_split_sentences(p)) <= 2


def test_rf1_question_then_dialogue_keeps_dialogue_alone():
    # 커플링은 지문 run 내부에서만 동작 — 질문이 대사(포함 라인)를 같은 문단으로 끌어오지 않는다.
    # (따옴표 문장의 분할·단독화 자체는 기존 ST-3 계약 — 여기선 커플링 비간섭만 잠근다.)
    t = '나는 생각했다. 이게 맞나? "맞습니다." 그가 답했다. 나는 고개를 끄덕였다.'
    r = reflow_paragraphs(t)
    paras = _paras(r)
    assert not any(("이게 맞나?" in p) and ("맞습니다" in p) for p in paras), f"질문이 대사를 끌어옴: {paras}"
    assert _nows(t) == _nows(r)


def test_rf1_double_question_not_overmerged():
    # 질문 2연타(Q1? Q2? A.)는 커플링을 '뒤 문장이 비질문일 때'로 한정 — Q2+A만 커플릿
    t = "문장 하나. 문장 둘. 어디까지? 어느 항목이지? 열세 번째다. 마무리 문장이다."
    r = reflow_paragraphs(t)
    paras = _paras(r)
    assert any("어느 항목이지? 열세 번째다." in p for p in paras), f"{paras}"
    assert _nows(t) == _nows(r)


def test_rf1_idempotent():
    t = "그날부터 사흘, 나는 썼다. 통구이를 시켰고 포도주를 시켰다. 예산? 없다. 결재선? 나 하나. 그거면 충분한 근거였다."
    once = reflow_paragraphs(t)
    assert reflow_paragraphs(once) == once                   # 재조판이 다시 찢지 않음


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for f in fns:
        try:
            f()
            passed += 1
        except Exception as e:
            import traceback
            print(f"FAIL {f.__name__}: {e}")
            traceback.print_exc()
    print(f"ST-3 검증: {passed}/{len(fns)} GREEN")
