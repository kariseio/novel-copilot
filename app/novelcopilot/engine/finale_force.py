# -*- coding: utf-8 -*-
"""RC-6 최종화층 검출→교정 갭 — 사용자 확정 '항상 결함' 클래스만 강제 교정(결정론·LLM 0).

배경(진단): 최종화층 축 다수가 *검출·가시화까지만*(무강제) 배선돼 있다. 그중 사용자가 "무조건 삭제/교정"으로
  **명시 확정**한 두 클래스가 advisory 로 남아 출고된다(실측: ch20 스타카토 잔여 등). 이 모듈은 그 두 클래스만
  advisory→강제 적용으로 승격한다. 그 외 미감·스타일 축은 계속 advisory(측정→가시화→작가 — PM 미감 판정 금지).

**무강제 예외 근거(헌법 1조 정합)**: 프로즈 준수 게이트 신설 금지는 유지한다. 여기서 기계 수술 대상이 되는 것은
  '시스템이 품질을 판정해 강제'하는 것이 아니라, **사용자가 '항상 결함'으로 직접 확정한** 클래스뿐이다 —
  정책 결정의 출처가 시스템이 아니라 사용자다(2026-08-21 "삭제 말고 수정, 전량"·2026-08-29 스타카토 "예외 없이"
  확정·memory `staccato-always-defect-no-beat-defense`·`tool-noun-modernization`). **목록 확장은 사용자 확정 필요**
  — 이 모듈 안에서 새 클래스를 자체 추가하지 않는다(게이트 증식 차단).

**신규 검출기 0**: ①은 기존 검출기 `humanize_detect.detect_fragment_echo`(N-7)를 재사용하고, 병합(교정)만 새로
  더한다("결정론 병합" — 스펙 ⓑ①이 명시 허용). ②는 검출 없이 사용자 확정 목록(작품 데이터) 결정론 치환이다.

**사실 불변**: 강제 교정도 사실 가드 G-A/G-B(service._guardrail) 통과분만 채택한다. 교정이 사실을 바꾸면 전체
  폴백(원문 유지·fail-safe). service 미제공(테스트·강등)이면 가드 생략 채택(결측 정직 — 실경로는 항상 service 주입).

**하위호환**: 플래그(config finale_force_fixes) OFF 면 호출부가 이 모듈을 부르지 않아 본문 바이트 동일. subs_map 이
  비면 ② no-op. detect_fragment_echo 가 빈 결과(Kiwi 부재·조각 없음)면 ① no-op. genre-blind — 치환 목록은
  작품 데이터에서 오고 엔진에 어떤 작품 어휘도 하드코딩하지 않는다.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# ⓐ 갭 인벤토리(결정론·LLM 0) — 최종화층 검출 축별 '교정 배선 유무' 표. 어떤 축이 검출만 하고 교정이
#   미배선(advisory)인지, 어떤 축이 강제 교정(forced)인지, 어떤 축이 발행 게이트(gate)인지 산출한다.
#   판정·수치 아님 — 배선 상태의 정직한 목록(리포트·트레이스 원자료). RC-6 은 이 표에서 forced 로 승격된 두
#   행(force:staccato·force:vocab)을 신설한다.
# ─────────────────────────────────────────────────────────────────────────────
def finale_gap_inventory() -> list[dict]:
    """최종화층 검출 축 × 교정 배선 유무(결정론 표). 각 행:
      axis          — 축 이름
      detector      — 검출 부품(모듈.함수)
      corrector     — 교정 부품(없으면 "")
      wiring        — 'forced'(강제 적용) | 'advisory'(가시화·폴백=원문) | 'gate'(발행 차단) | 'none'
      always_defect — 사용자가 '항상 결함'으로 명시 확정했는가(강제 교정 자격)
      rc6           — RC-6 이 이 축의 배선을 바꾸는가('forced_new' | '')"""
    return [
        # ── 결정론 강제 교정(기존) ─────────────────────────────────────────────
        {"axis": "시제 누출(현재형 종결 혼입)", "detector": "quality_gates.tense_leak_ratio",
         "corrector": "harness._fix_tense", "wiring": "forced", "always_defect": True, "rc6": ""},
        {"axis": "구조 마크업(웹포스트 표지·스페이서)", "detector": "textfmt.strip_structural_markup",
         "corrector": "textfmt.strip_structural_markup", "wiring": "forced", "always_defect": True, "rc6": ""},
        {"axis": "메타 누출(머리말·설명)", "detector": "textfmt.sanitize_meta",
         "corrector": "textfmt.sanitize_meta", "wiring": "forced", "always_defect": True, "rc6": ""},
        {"axis": "장문단 조판(ST-3)", "detector": "textfmt.reflow_paragraphs",
         "corrector": "textfmt.reflow_paragraphs", "wiring": "forced", "always_defect": False, "rc6": ""},
        # ── RC-6 신설 강제 교정 ────────────────────────────────────────────────
        {"axis": "① 스타카토·용언 없는 조각(고립 여운)", "detector": "humanize_detect.detect_fragment_echo(N-7)",
         "corrector": "finale_force.force_staccato_merge(결정론 병합)", "wiring": "forced",
         "always_defect": True, "rc6": "forced_new"},
        {"axis": "② 폐기 조어·금지어(v4 정본)", "detector": "StyleSpec.deprecated_terms(사용자 확정 목록)",
         "corrector": "finale_force.force_vocab_substitution(결정론 치환)", "wiring": "forced",
         "always_defect": True, "rc6": "forced_new"},
        # ── advisory(검출·가시화까지만·폴백=원문) — RC-6 대상 아님(미감·스타일 축) ────────────────
        {"axis": "N-4 문말 템플릿 밀도·파편 클러스터", "detector": "humanize_detect.detect_ending_monotony/_fragment_cluster",
         "corrector": "humanize_pass.humanize_spans(N-4·LLM)", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "N-3 모티프 우려먹기", "detector": "humanize_detect.detect_motif_retread",
         "corrector": "humanize_pass.humanize_spans(N-3·LLM)", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "N-5 발화 층위 벽", "detector": "humanize_detect.detect_layer_wall",
         "corrector": "humanize_pass.humanize_spans(N-5·전범위=not_local 스킵)", "wiring": "advisory",
         "always_defect": False, "rc6": ""},
        {"axis": "N-6 클리셰 직유·수식", "detector": "humanize_detect.detect_cliche_modifier",
         "corrector": "humanize_pass.humanize_spans(N-6·전범위)", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "N-1 자기해설(LLM 탐지)", "detector": "narration_detect(catch-all)",
         "corrector": "humanize_pass(precise 잉여만·HM-3~7)", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "N-2 감정 명명(LLM 탐지)", "detector": "narration_detect(catch-all)",
         "corrector": "humanize_pass(precise 잉여만·HM-3~7)", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "스타일 지각 판정", "detector": "harness._run_style_judgment",
         "corrector": "apply_style_judgment→N-4 선별(LLM)", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "appears_as 누락", "detector": "checker.check_text.missing_appears_as",
         "corrector": "", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "자유형 사실 모순(claim_audit)", "detector": "claim_audit.audit_chapter",
         "corrector": "", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "스토리 주입 fmt/BAN 검사", "detector": "copilot._story_inject_check",
         "corrector": "", "wiring": "advisory", "always_defect": False, "rc6": ""},
        {"axis": "콜드리드", "detector": "cold_read", "corrector": "", "wiring": "advisory",
         "always_defect": False, "rc6": ""},
        {"axis": "캐논가드 반려 증거", "detector": "humanize_pass(guardrail_detail)",
         "corrector": "", "wiring": "advisory", "always_defect": False, "rc6": ""},
        # ── 발행 게이트(차단·자동 교정 아님) ───────────────────────────────────
        {"axis": "하드 위반", "detector": "checker.check_text.hard",
         "corrector": "recovery(작가 레버)", "wiring": "gate", "always_defect": False, "rc6": ""},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# ① 스타카토/용언 없는 조각 — 결정론 연결 병합. 완결문 뒤에 홀로 선 용언 없는 조각(N-7 고립 여운)을 앞 완결문에
#   **전치**로 이어 붙여 한 문장으로 만든다: '완결문P. 조각F.' → 'F(여운부호 제거), P'. 이렇게 하면 병합문이
#   완결문 P 의 용언 종결로 끝나 검출기가 수렴한다(잔존 보장 제거) — 단순 후행 콤마 접합은 병합문 말미가 여전히
#   용언 없이 끝나(F 가 말미) 검출기가 재-플래그한다(실측). 전치는 사용자 수동 병합 구조와도 일치한다
#   (`tools/_staccato_merge_ch19.py` 예 D "울고 있었다. 소리 없이." → "…소리 없이 울고 있었다").
#   **용언을 새로 만들지 않는다**(그건 LLM 몫·pink-elephant) — 결정론이 하는 것은 조각의 전치+경계 재조판뿐.
#   더 정교한 병합(용언 부여)은 기존 휴머나이즈 LLM 처방(N-7) 소관이고, 이 강제 패스는 그것이 폴백해 잔존한
#   조각까지 보장 제거하는 백스톱이다. 채택은 최종적으로 사실 가드·no-harm 정독 게이트가 중재.
# ─────────────────────────────────────────────────────────────────────────────
_P_SKIP_TERMINALS = ("!", "?")   # 선행 완결문이 감탄·의문으로 끝나면 전치가 어조를 뒤집으므로 보류(보수)


def _merge_echo_span(span_text: str, frag: str) -> str | None:
    """N-7 고립 조각 여운 스팬('완결문P. 조각F.')을 결정론 전치 병합 → 'F, P'. 병합 불가/보수 케이스는 None(폴백).

    보수 규칙(어조·여운 보존): 선행 완결문 P 가 감탄('!')·의문('?')·말줄임('…'·'..')으로 끝나면 보류한다
    (전치가 어조를 뒤집음). 조각 F 를 스팬 말미에서 못 찾거나 선행 완결문이 비면 None."""
    frag = (frag or "").strip()
    if not span_text or not frag:
        return None
    trail_len = len(span_text) - len(span_text.rstrip())
    body = span_text[:len(span_text) - trail_len] if trail_len else span_text
    trail = span_text[len(body):]                 # 스팬 말미 공백(원문 보존)
    idx = body.rfind(frag)
    if idx <= 0:                                   # 조각 미발견 or 스팬 맨 앞(선행 완결문 없음)
        return None
    head = body[:idx].rstrip()                     # 선행 완결문 P(+종결부호)
    if not head:
        return None
    if head[-1] in _P_SKIP_TERMINALS or head.endswith("…") or head.endswith(".."):
        return None                                # 감탄·의문·말줄임 어조 보존(보수)
    phrase = body[idx:].strip().rstrip(".!?…—·。")  # 조각을 앞으로 뺄 구(여운 종결부호 제거)
    phrase = phrase.strip()
    if not phrase:
        return None
    merged = phrase + ", " + head + trail          # 전치: F, P — 병합문이 P 의 용언으로 끝난다
    if merged == span_text:
        return None
    return merged


def force_staccato_merge(text: str) -> tuple[str, list[dict]]:
    """① N-7 고립 조각 여운을 결정론 병합. 기존 검출기 detect_fragment_echo 재사용(신규 검출기 0).

    반환 (new_text, entries). Kiwi 부재/조각 없음 → 원문 그대로·빈 entries(no-op). 스팬은 뒤에서 앞으로
    적용해 선행 편집이 후행 좌표를 밀지 않게 한다."""
    text = text or ""
    try:
        from .humanize_detect import detect_fragment_echo
        findings = detect_fragment_echo(text)
    except Exception:
        return text, []
    if not findings:
        return text, []
    result = text
    entries: list[dict] = []
    for f in sorted(findings, key=lambda x: (x.get("span") or {}).get("char_start", 0), reverse=True):
        sp = f.get("span") or {}
        cs, ce = sp.get("char_start"), sp.get("char_end")
        if not isinstance(cs, int) or not isinstance(ce, int) or ce <= cs or ce > len(result):
            continue
        span_text = result[cs:ce]
        frag = (f.get("metric") or {}).get("fragment") or ""
        merged = _merge_echo_span(span_text, frag)
        entry = {
            "category": "force:staccato", "severity": "S3",
            "char_start": cs, "char_end": ce, "changed": False,
            "before": span_text, "after": None, "author_review": False,
            "note": "N-7 고립 조각 여운 결정론 병합",
        }
        if merged is not None and merged != span_text:
            result = result[:cs] + merged + result[ce:]
            entry["changed"] = True
            entry["after"] = merged
        else:
            entry["fallback"] = "unmergeable"
            entry["note"] = "조각 병합 불가(비평서 종결·말줄임·미발견 — 원문 보존)"
        entries.append(entry)
    return result, entries


# ─────────────────────────────────────────────────────────────────────────────
# ② 폐기 조어·금지어 정본 치환(RX-3 계보) — 결정론 문자열 치환. 목록은 작품 데이터(StyleSpec.deprecated_terms)에서
#   오고 엔진에 하드코딩 0(genre-blind). 명사 어간 치환이라 뒤따르는 조사는 보존된다(예: '오랏줄을'→'결박줄을').
#   목록 확장은 사용자 확정 필요 — 이 함수는 주어진 목록만 적용한다.
# ─────────────────────────────────────────────────────────────────────────────
def force_vocab_substitution(text: str, subs_map: dict | None) -> tuple[str, list[dict]]:
    """② 폐기 표기→정본 표기 결정론 치환. subs_map 비면 no-op(바이트 동일). 반환 (new_text, entries)."""
    text = text or ""
    if not subs_map:
        return text, []
    result = text
    entries: list[dict] = []
    for src, dst in dict(subs_map).items():
        src = (src or "").strip()
        dst = (dst or "").strip()
        if not src or not dst or src == dst:
            continue
        n = result.count(src)
        if n == 0:
            continue
        result = result.replace(src, dst)
        entries.append({
            "category": "force:vocab", "severity": None,
            "char_start": None, "char_end": None, "changed": True,
            "before": src, "after": dst, "count": n, "author_review": False,
            "note": "폐기 조어·금지어 정본 치환(결정론)",
        })
    return result, entries


# ─────────────────────────────────────────────────────────────────────────────
# ⓑ 오케스트레이터 — ①②를 순차 적용하고 사실 가드(G-A/G-B) 통과분만 채택. 교정이 사실을 바꾸면 전체 폴백(fail-safe).
# ─────────────────────────────────────────────────────────────────────────────
def apply_forced_finale_fixes(ontology, checker, chapter_no: int, text: str, *,
                              subs_map: dict | None = None, service=None) -> tuple[str, list[dict]]:
    """RC-6 강제 교정 스택. 반환 (new_text, entries).

    ① force_staccato_merge → ② force_vocab_substitution → 사실 가드(service._guardrail). 무변경이면 원문
    그대로 반환(바이트 동일). 가드 불통과/오류면 전체 폴백(원문 유지)+증거 기록(은폐 금지). service 미제공이면
    가드 생략 채택(강등·결측 정직 — 실경로는 항상 service 주입)."""
    text = text or ""
    entries: list[dict] = []
    cur, e1 = force_staccato_merge(text)
    entries += e1
    cur, e2 = force_vocab_substitution(cur, subs_map)
    entries += e2
    if cur == text:
        return text, entries                       # 무변경 — 바이트 동일
    if service is not None and ontology is not None and checker is not None:
        try:
            ids = sorted(set(ontology.scan_present_ids(text)))
            before_res = checker.check_text(text, ontology, chapter_no, ids)
            g, _ = service._guardrail(text, cur, before_res, ids, ontology, checker, chapter_no)
            if not g.get("passed"):
                entries.append({
                    "category": "force:guard", "severity": None, "changed": False,
                    "author_review": False, "fallback": "guard",
                    "note": "사실 가드 불통과 — 강제 교정 전체 폴백(원문 유지)",
                    "guardrail_detail": {k: g.get(k) for k in
                                         ("new_hard", "claim_changes", "claim_flaps", "reason",
                                          "G_A_passed", "G_B_passed", "length_ok")},
                })
                return text, entries
            entries.append({
                "category": "force:guard", "severity": None, "changed": False,
                "author_review": False, "note": "사실 가드 통과",
                "guard": {k: g.get(k) for k in ("passed", "G_A_passed", "G_B_passed", "length_ok")},
            })
        except Exception as ex:
            entries.append({
                "category": "force:guard", "severity": None, "changed": False,
                "author_review": False, "fallback": "guard_error", "note": str(ex)[:120],
            })
            return text, entries                   # 보수적 폴백(없는 측정으로 통과시키지 않는다)
    return cur, entries
