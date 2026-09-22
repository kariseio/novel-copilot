# -*- coding: utf-8 -*-
"""CE-4~7 캐논 가드 비용 프로그램 잠금 테스트(LLM 0). 프롬프트·계약 회귀 방어.
- CE-4: 스키마·지시에서 null 스캐폴딩 제거(출력 슬림화) · appears_as 상시 필수(하드룰 침묵 방지) · advisory 필드.
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.engine.extractor import ClaimExtractor
from novelcopilot.engine.checker import CheckResult


class _Spec:
    def __init__(self, key, states):
        self.key, self.states = key, states


class _Vocab:
    categorical_keys = ["affiliation"]
    numeric_keys = ["money"]

    def categorical(self, k):
        return ["관리국", "민간"]

    def label(self, k):
        return "돈"

    def state_specs(self):
        return [_Spec("life", ["alive", "dead"])]


def test_ce4_schema_no_null_scaffolding():
    """CE-4: _schema() 템플릿(user 앵커)에 null 시연이 없어야 한다 — 앵커가 지시를 이기므로(ST-12) 여기 없어야 발화."""
    sch = json.dumps(ClaimExtractor(None, _Vocab(), [])._schema(), ensure_ascii=False)
    assert "또는 null" not in sch, "범주형/수치/상태 필드에 '또는 null' 시연 잔존 — 출력 슬림화 미발화"
    assert "없으면 null" not in sch, "evidence 필드에 '(없으면 null)' 시연 잔존"


def test_ce4_appears_as_required_in_schema_anchor():
    """CE-4 ⓐ: appears_as 는 null 이 유효값인 적 없는 필수 분류값 — 앵커(스키마)에 필수 표식이 실려야 한다.
    생략 시 terminal 상태 하드룰(predicates.py:107 claim.get('appears_as'))이 조용히 안 잡힌다."""
    ent = ClaimExtractor(None, _Vocab(), [])._schema()["entities"][0]
    assert "appears_as" in ent
    assert "필수" in str(ent["appears_as"]), "appears_as 앵커에 필수 표식 없음 — 지시 단독은 앵커에 짐(ST-12)"


def test_ce4_checkresult_missing_appears_as_field():
    """CE-4 ⓐ: CheckResult 가 appears_as 누락 목록을 advisory 로 실어 나른다(기본값 [] 하위호환)."""
    assert CheckResult().missing_appears_as == []                     # 기본값 — 구 생성 경로 무변경
    assert CheckResult(missing_appears_as=["leo"]).missing_appears_as == ["leo"]


def test_ce7_evidence_reaches_trace_rec():
    """CE-7/CE-8: 반려 스팬 증거(막힌 재작성문·claim_flaps 등)가 GA-1 트레이스 rec 까지 도달한다.
    화이트리스트가 신규 2필드를 버리던 것(반려 28/28 증거 없음의 소스)을 이 테스트가 잠근다.
    claim_flaps 는 정당 반려 vs 추출 요동 위양성 판별 증거 — CE-8 로 캡처 추가."""
    from novelcopilot.engine.harness import _collect_humanize_span_texts
    rej = {"category": "N-4", "severity": "S2", "char_start": 0, "char_end": 5,
           "changed": False, "fallback": "guardrail", "author_review": False,
           "rejected_after_span": "막힌 재작성문",
           "guardrail_detail": {"claim_changes": [], "reason": "이름·수치가 바뀌었습니다",
                                "claim_flaps": [{"entity": "준호", "key": "money", "before": 1, "after": 2}]}}
    r = _collect_humanize_span_texts("가나다라마바", "가나다라마바", [rej])[0]
    assert r["rejected_after_span"] == "막힌 재작성문"
    assert r["guardrail_detail"]["claim_flaps"][0]["key"] == "money"   # 요동 증거가 트레이스에 도달
    # 하위호환: 증거 없는 entry → rec 에 신규 키 미포함(구 트레이스 바이트 동일)
    plain = {"category": "N-4", "severity": "S2", "char_start": 0, "char_end": 5,
             "changed": True, "fallback": None, "author_review": False}
    r2 = _collect_humanize_span_texts("가나다라마바", "가나다라마바 X", [plain])[0]
    assert "rejected_after_span" not in r2 and "guardrail_detail" not in r2
