# -*- coding: utf-8 -*-
"""B-24 회귀 가드 — sanitize_meta 장르-안전성 (LLM 0콜).
적대검증(wf b24) 결론을 박제: 어휘목록 기반 '인라인 괄호 메타제거'는 두더지잡기로 기각됐다.
시스템물 diegetic 괄호('(오류:…)'·'(주의:…)'·'(참고:…)')와 서사 괄호('(그건 모순이었다)')는 작가메모가 아니라
'본문'이므로 절대 제거 금지(오탐=장르치명). 줄단위 안전 메타(전체가 [END]/(다음 회 계속)/※ 류)만 제거.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_b24_meta_sanitize.py
"""
from __future__ import annotations
import sys

from novelcopilot.engine.harness import sanitize_meta


def test_diegetic_and_narrative_parens_preserved() -> bool:
    # 적대검증서 오탐(27건)으로 드러난 '절대 제거하면 안 되는' 본문 괄호들 — 어휘목록 검출기 재도입 차단 가드
    must_keep = [
        "눈앞에 붉은 글자가 떠올랐다. (오류: 접근 거부)",      # 시스템물 diegetic — 장르치명 오탐 사례
        "(주의: 함정 감지)", "(참고: 일일 퀘스트)", "(경고: 마나 부족)",
        "(오류율 0퍼센트 달성)", "(오타쿠 길드 가입 환영)",       # 단어경계 오탐(오류율/오타쿠)
        "그는 생각했다. (그건 모순이었다.)",                   # 내면독백 — '모순'을 서사로 사용
        "(그날의 오류가 모든 걸 바꿨다.)", "(앞서 언급한 그 남자가 다시 나타났다.)",
        "(그 인원수로는 도시를 지킬 수 없었다.)", "(설정 상 그는 왕이어야 했다.)",
        "보고서엔 (수정 필요)라고 빨갛게 적혀 있었다.",        # '수정 필요'조차 본문 인용일 수 있음
        "그는 웃었다. (사실 그땐 아무것도 몰랐다.)", "(쿵)", "(웃음)",
    ]
    bad = [s for s in must_keep if sanitize_meta(s) != s.strip()]
    ok = not bad
    print(f"[{'OK' if ok else 'FAIL'}] 본문 괄호 보존(diegetic/서사) — {len(must_keep)}건 중 오탐 {len(bad)}건")
    for b in bad[:5]:
        print("   ***오탐(제거됨)***:", repr(b), "→", repr(sanitize_meta(b)))
    return ok


def test_safe_wholeline_meta_stripped() -> bool:
    # 줄단위 안전 패턴(전체가 메타)은 여전히 제거 — 보존되면 누출
    must_strip = ["[END]", "(다음 회에 계속)", "to be continued", "(※ 톤 수정함)", "--- 장면 전환 ---", "[다음 화 계속]"]
    leaked = [s for s in must_strip if sanitize_meta(s) != ""]
    ok = not leaked
    print(f"[{'OK' if ok else 'FAIL'}] 안전 메타 제거 — {len(must_strip)}건 중 누출 {len(leaked)}건 {leaked[:3]}")
    return ok


if __name__ == "__main__":
    results = [test_diegetic_and_narrative_parens_preserved(), test_safe_wholeline_meta_stripped()]
    print("\nB-24 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
