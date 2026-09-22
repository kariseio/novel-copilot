# -*- coding: utf-8 -*-
"""HM-1b Claude 윤문 패스 + 배선 — 웹소설 휴머나이즈 패스의 실행 2단(윤문·변경률 가드·계측).

설계: docs/design-hm1-humanize-pass.md §2ⓒ(윤문가=Claude 라우팅)·ⓓ(배선)·ⓔ(Stage B 흡수). 탐지 1단은
  HM-1a(engine/humanize_detect.py)가 결정론 스팬 좌표를 공급한다. SSOT: docs/webnovel-ai-tell-taxonomy.md.

**이 모듈은 신규 리라이터/가드레일을 만들지 않는다** — 검증된 기존 스팬 체시스(tools.st11_span_rewrite.
  rewrite_span_via_chassis: 사실 불변 G-A/G-B·커버리지 폴백·스팬 격리)를 그대로 태운다. HM-1a 결정론
  스팬(char 좌표)을 체시스가 요구하는 스팬 dict(span_text·kind·문장인덱스)로 브릿지하고, 카테고리별
  처방(분류학 SSOT 소설판 레시피)을 시스템/지시로 태워 국소 수술한다.

**엔진 의존성0 불변**: novelcopilot/(엔진)은 tools 에 *로드 타임* 의존이 없다. 이 파일의 tools import 는
  전부 함수 *안*에서 lazy·try/except 로 수행된다 — 부품 부재 시 그 스팬만 폴백(원문 유지·기록)하고 전진.

**Claude 라우팅(ⓒ)**: config humanize_model(기본 'anthropic:claude-opus-4-8' — create_role_provider 재사용)로
  윤문 provider 를 만든다. 체시스의 revise_prose 는 generator.provider 를 쓰므로, 윤문 콜 동안만 generator.
  provider 를 스코프 스왑(try/finally 복원)해 Claude 로 태운다 — 사실 불변 시스템 가드·G-B·커버리지는 그대로.

**변경률 가드(③)**: 레벤슈타인/원문 5~30% 결정론 계산. 30% 초과 스팬은 폴백(원문 유지·기록), 잔존 S1은
  "작가 확인 요망" 표기(은폐 금지). 5% 미만은 저윤문 재확인 표식(advisory).

**무강제**: 어떤 카테고리도 차단·자동 재작성을 만들지 않는다 — 윤문은 게이트가 아니라 편집 패스다. 실패/폴백은
  원문 유지 + humanize 내역 기록(침묵 폴백 금지). 계측은 전부 advisory(임계·판정 0).

**few-shot 0·pink-elephant**: 윤문 시스템 프롬프트는 대원칙 6 + 카테고리 처방(규칙 지시문만)으로 구성한다.
  나쁜 예문을 일반화해 나열하지 않는다 — 탐지된 *해당 스팬 원문*만 지시에 인용(체시스가 span_text 로 전달).
"""
from __future__ import annotations

# 변경률 권장 대역(원전 rewriting-playbook.md §2 승계) — 5~30%. 상한 초과=과윤문 폴백, 하한 미만=저윤문 표식.
CHANGE_RATE_MIN = 0.05
CHANGE_RATE_MAX = 0.30

# 회차당 윤문 스팬 총 상한 기본값(config humanize_max_spans 로 노출). 비용 가드 — 긴 run·S1 우선 선별.
DEFAULT_MAX_SPANS = 6

# 대원칙 6(원전 rewriting-playbook.md §0 "The Prime Directives" 소설판) — 윤문 시스템 프롬프트 공통 머리.
#   의미 불변·톤 유지·국소성·자연성>완벽성·span-grounded·과윤문 경보. 사실 불변은 체시스 사실 가드가 상회하나
#   프롬프트로도 사전 바인딩한다(방어선 중첩). 예문 나열 없음(few-shot 0).
_PRIME_DIRECTIVES = (
    "너는 웹소설 교정 작가다. 아래 원칙을 지켜 이 구간의 산문만 다듬어라.\n"
    "1) 의미 불변: 사건·설정·수치·고유명사·인용·인과는 한 글자도 바꾸지 마라. 모호해도 임의로 보강하지 마라.\n"
    "2) 톤 유지: 원문의 문체·화자 목소리·장르 결을 유지하라. 다른 장르로 바꾸지 마라.\n"
    "3) 국소성: 이 구간만 수술적으로 고치고 전면 재작성하지 마라. 원문 대부분은 그대로 흘려라.\n"
    "4) 자연성 우선: 과하게 문학적으로 고치지 마라. 웹소설 필자의 자연스러운 리듬이 목표다.\n"
    "5) 근거 기반: 아래 지목된 티가 있는 부분만 손대라. 지목 없는 표현은 건드리지 마라.\n"
    "6) 과윤문 금지: 절반 이상을 바꾸면 내용이 훼손된다. 최소 변경으로 티만 걷어내라.\n"
    "본문(다듬은 구간)만 출력하라 — 머리말·설명·메타 금지."
)

# 카테고리별 처방(분류학 SSOT docs/webnovel-ai-tell-taxonomy.md 각 축 '처방' 문안의 지시화 + 원전
#   rewriting-playbook.md 관련 레시피 소설판). 규칙 지시문만 — 나쁜 예문 나열 없음(pink-elephant 금지).
#   해당 스팬 원문 인용은 체시스(revise_prose)가 span_text 로 전달하므로 여기 지시엔 원문을 넣지 않는다.
_CATEGORY_DIRECTIVES = {
    # N-1 자기 해설 [S1] — HM-4(2026-08-20 사용자 결정 "전량 변환·삭제 금지"): 처방을 이전-전용으로 개정.
    #   내용(유머·정보·복선)은 보존하고 층위만 지문→대사/행동으로 옮긴다. 잠언(옮길 내용 없는 일반론)은
    #   동작 치환 제3경로(감사: 대사로 이주 시 원장 16 잠언형 응수 재발). 말투 접지(원장 8 혼잣말 수렴 방지).
    "N-1": ("이 구간에서 서술자가 장면 위에 얹은 정리·자평·논평을 그 자리에서 옮겨라. 그 내용이 인물이 할 법한 "
            "말이면 화자의 입말 대사나 속말로, 몸으로 보일 수 있으면 행동·감각으로 옮긴다. 옮길 것이 사실·정보·"
            "유머가 아니라 일반론이면, 그 자리를 인물의 구체적인 동작 하나로 대신한다. 새로 세우는 말은 이 인물이 "
            "평소 쓰는 말투 안에서 고른다. 유머·정보·복선은 잃지 말고 층위만 바꾼다. 문장을 통째로 들어내지 마라. "
            "새 사건·새 사실은 더하지 마라."),
    # N-2 감정 명명 [S2] — HM-4 동반 개정: 이전-전용(들어내기 금지)·말투 접지. '회차 1회 허용' 조항은 제거
    #   (스팬 단위 콜엔 회차 정보가 없어 계약 불가 + 전량 변환 정책과 모순 — 감사 경미 6).
    "N-2": ("이 구간에서 감정을 이름으로 선언하는 문장을, 신체 반응·행동·대사의 결로 옮겨라. "
            "감정어 자체를 다른 감정어로 갈지 말고, 그 감정이 드러나는 몸짓·호흡·시선·목소리로 이전하라. "
            "새로 세우는 말은 이 인물이 평소 쓰는 말투 안에서 고른다. 문장을 통째로 들어내지 마라."),
    # N-3 모티프 우려먹기 [S2] — 회차 간 반복 구절. 처방: 회차별 변주 or 밀도 감축.
    "N-3": ("이 구간에서 작품이 여러 회차에 걸쳐 반복해 온 이미지·구문을, 같은 뜻 다른 결로 변주하라. "
            "반복된 표현을 그 자리에서 다른 감각·다른 각도의 묘사로 바꾸되, 가리키는 대상·사실은 그대로 두어라. "
            "기계적으로 굳은 상투 구문만 풀고, 원문의 정보는 보존하라."),
    # N-4 문말 템플릿 밀도 [S2] — '~다' 지배·과거형 run 벽. 처방: PE13 서법 다양화 + 층위 개입(단독 목표 금지).
    "N-4": ("이 구간에서 같은 종결('~다')이 연달아 지배하는 리듬을 풀어라. 연속되는 평서 과거형을 "
            "'~었던·~었다가·~더라·~었으니' 같은 서법·시제 변주와, 입말 생각·짧은 대사·현재형 판단을 섞어 다양화하라. "
            "인접한 단문 두셋은 연결어미(-며·-고·-는데·-면서·-자)나 관형절로 한 문장에 엮어 단·복문을 혼합하라. "
            "문말만 기계적으로 갈지 말고 문장 층위 자체를 재구성하라."),
    # N-5 발화 층위 벽 [S1] — 지문 단일 층위 연속. 처방: 층위 전환 삽입(대사·입말·의문).
    "N-5": ("이 구간이 대사·입말·의문 없이 지문만 길게 이어지는 벽이라면, 층위 전환을 삽입해 끊어라. "
            "인물의 속말·짧은 의문·감각의 전환으로 지문 벽에 숨구멍을 내되, 새 사건·새 사실을 지어내지 말고 "
            "이미 있는 장면의 결을 다른 층위(생각·입말)로 옮겨 담아라."),
    # N-6 클리셰 직유·수식 [S3] — 상투 직유·수식 반복. 처방: 구체 감각·행동으로 대체 or 삭제.
    "N-6": ("이 구간의 상투적인 직유·과잉 수식을 구체 감각·행동으로 대체하거나 덜어내라. "
            "정도부사·동의어 이중 수식·'~적 N' 체인은 하나로 줄이거나 구체 동사·명사로 풀어라. "
            "원문이 가리키는 대상·강도는 유지하되, 관습적으로 굳은 비유 표면만 갈아라."),
    # N-7 선언-조각 여운 [S3] — 완결문 뒤 용언 없는 조각 문장으로 여운을 박는 리듬(사용자 재발 실측 2026-08-11).
    "N-7": ("이 구간은 완결된 문장 뒤에 용언 없는 짧은 조각 문장을 떼어 여운을 만드는 리듬이다. "
            "조각을 앞 문장에 이어 붙여 하나의 완결문으로 만들거나, 조각에 서술어를 주어 온전한 문장으로 "
            "세워라. 가리키는 사실·정보는 그대로 두고 리듬만 잇는다."),
}

# 에세이형 직수입 처방(설계 §1 "관련 처방 직수입") — 소설 본문에서도 실효 있는 원전 처방. HM-1a 결정론 축은
#   소설 고유 N-* 만 좌표를 내므로 이들은 LLM 탐지(설계 ⓑ②·후속) 스팬이 붙을 때 참조된다. 지금은 등록만.
_ESSAY_DIRECTIVES = {
    # I-3/I-1 '것이다' 직결 [S2] — 형식명사 결산을 종결어미 직결로.
    "I-3": "'~라는 것이다/~다는 뜻이다' 형식명사 결산을 '~다' 종결로 직결하라.",
    # D 계열 결산구 삭제 [S1] — 결론적으로·요약하면 등 결산 상투구 삭제.
    "D-1": "'결론적으로·요약하면·~라고 할 수 있다' 류 결산 상투구를 대부분 삭제하라.",
    # E-4 단문 일변도 [S2] — 단·복문 혼합.
    "E-4": "인접 단문 두셋을 연결어미·관형절로 묶어 복문화하라.",
}


def directive_for(category: str) -> str:
    """카테고리 코드(N-1~N-6·에세이 축)의 처방 지시문 — 미등록 축은 대원칙만(안전 폴백·결측 정직)."""
    if category in _CATEGORY_DIRECTIVES:
        return _CATEGORY_DIRECTIVES[category]
    if category in _ESSAY_DIRECTIVES:
        return _ESSAY_DIRECTIVES[category]
    return ""   # 미등록: 체시스 기본 리듬 지시로 폴백(대원칙+체시스 PARAGRAPH_DIRECTIVE)


def build_directive(category: str) -> str:
    """윤문가에게 태울 최종 지시 = 대원칙 6 + 카테고리 처방(규칙 지시문). 원문 인용은 체시스가 span_text 로 전달."""
    presc = directive_for(category)
    if not presc:
        return _PRIME_DIRECTIVES
    return _PRIME_DIRECTIVES + "\n[이 구간의 처방]\n" + presc


# ─────────────────────────────────────────────────────────────────────────────
# 변경률 가드 — 레벤슈타인/원문 길이(결정론). 순수 함수(의존성 0).
# ─────────────────────────────────────────────────────────────────────────────
def _levenshtein(a: str, b: str) -> int:
    """편집 거리(삽입·삭제·치환 1) — 두 행 롤링 DP(O(len(a)*len(b)) 시간·O(min) 공간). LLM 0·결정론."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a          # 짧은 쪽을 내부 루프로(공간 최소화)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def change_rate(before: str, after: str) -> float:
    """변경률 = 레벤슈타인(before, after) / max(1, len(before)). 0.0~(원문 대비 상대 편집량)."""
    before = before or ""
    after = after or ""
    if not before and not after:
        return 0.0
    return round(_levenshtein(before, after) / max(1, len(before)), 4)


def classify_change_rate(rate: float) -> str:
    """변경률 → 대역 라벨(advisory·판정 아님): 'under'(<5%)·'ok'(5~30%)·'over'(>30%)."""
    if rate > CHANGE_RATE_MAX:
        return "over"
    if rate < CHANGE_RATE_MIN:
        return "under"
    return "ok"


# ─────────────────────────────────────────────────────────────────────────────
# HM-1a 결정론 스팬 → 체시스 스팬 브릿지. HM-1a findings 는 {category, severity, span(char_start/end·text),
#   metric}. 체시스(rewrite_span_via_chassis)는 span_text·kind·문장인덱스·n_sent 를 요구한다. char 좌표
#   기반 findings 를 체시스가 실제 replace 할 수 있는 스팬 dict 로 변환한다.
# ─────────────────────────────────────────────────────────────────────────────
def _chassis_span_from_finding(text: str, finding: dict) -> dict | None:
    """HM-1a finding → 체시스 스팬 dict. 좌표가 유효(0<=start<end<=len)하고 원문에서 비지 않은 슬라이스면
    span_text=text[start:end]·kind=category·문장인덱스 자리표(체시스 v2 는 문단 확장이라 실제 미사용)로 채운다.
    좌표가 회차 전범위(N-5·N-6 밀도 신호처럼 char_start=0·char_end=len·text="")면 국소 수술 불가 → None(스킵).
    """
    sp = finding.get("span") or {}
    cs, ce = sp.get("char_start"), sp.get("char_end")
    if not isinstance(cs, int) or not isinstance(ce, int):
        return None
    if cs < 0 or ce <= cs or ce > len(text or ""):
        return None
    span_text = (text or "")[cs:ce]
    if not span_text.strip():
        return None
    # 전범위 신호(회차 단위 밀도 — N-5 layer_wall·N-6 simile) 는 국소 스팬이 아님 → 국소 수술 대상 아님(스킵).
    if cs == 0 and ce == len(text or "") and not (sp.get("text") or "").strip():
        return None
    return {
        "kind": finding.get("category", "N"), "category": finding.get("category", "N"),
        "severity": finding.get("severity", "S2"),
        "char_start": cs, "char_end": ce, "span_text": span_text,
        # 체시스 v2 는 문단 확장(char 오프셋 기반)이라 문장인덱스는 자리표만 필요(replace 는 char 좌표로).
        "sent_start": -1, "sent_end": -1, "n_sent": 1,
        "metric": finding.get("metric", {}),
    }


def _severity_rank(sev: str) -> int:
    """수리 우선순위 정렬 키 — S1 최우선(0) > S2(1) > S3(2). 비용 상한 선별 시 결정적 티부터 채운다."""
    return {"S1": 0, "S2": 1, "S3": 2}.get(sev or "S2", 1)


def select_humanize_spans(findings: list[dict], max_spans: int | None) -> list[dict]:
    """윤문 대상 findings 선별 — 심각도(S1 우선) → 스팬 길이(긴 run 우선) → 좌표(안정) 정렬 후 상한 절단.

    max_spans=None 이면 전부, 0/음수면 빈 리스트(윤문 없음·검출만). 국소 수술 불가 좌표(전범위)는 산술과
    무관하게 제외되지 않는다(여기선 findings 그대로 정렬·절단 — 브릿지 스킵은 실행기가 수행)."""
    if not findings:
        return []
    ordered = sorted(
        findings,
        key=lambda f: (_severity_rank(f.get("severity")),
                       -((f.get("span") or {}).get("char_end", 0) - (f.get("span") or {}).get("char_start", 0)),
                       (f.get("span") or {}).get("char_start", 0), f.get("category", "")),
    )
    if max_spans is None:
        return ordered
    if max_spans <= 0:
        return []
    return ordered[:max_spans]


def _paragraph_ranges(text: str) -> list[tuple[int, int]]:
    """빈 줄 기준 문단 char 범위 — HM-4 그룹핑 원자료(결정론·LLM 0)."""
    out: list[tuple[int, int]] = []
    pos = 0
    for para in (text or "").split("\n\n"):
        out.append((pos, pos + len(para)))
        pos += len(para) + 2
    return out


def group_findings_by_paragraph(text: str, findings: list[dict]) -> list[dict]:
    """HM-4(감사 배선 2026-08-20): 같은 문단의 findings 를 병합 finding 하나로 묶고 문서 순서로 정렬.

    근거: 체시스 v2 는 스팬을 문단으로 확장해 재작성한다 — 한 문단의 두 스팬을 개별 콜로 돌리면 먼저 채택된
    수술이 문단을 바꿔 나머지가 stale_offset 스킵된다('전량 변환'이 심각도 정렬에 따라 비결정론). 묶으면 한
    콜이 그 문단의 티를 함께 수술한다(커버리지 결정론화·콜 수 감소). 전범위/무좌표 finding 은 단독 유지(뒤로).
    병합 finding 은 _group_cats(카테고리 목록)를 실어 실행기가 처방을 합성한다."""
    ranges = _paragraph_ranges(text)

    def _pidx(cs: int) -> int:
        for i, (s, e) in enumerate(ranges):
            if s <= cs <= e:
                return i
        return -1

    buckets: dict[int, list[dict]] = {}
    passthrough: list[dict] = []
    for f in findings or []:
        sp = f.get("span") or {}
        cs, ce = sp.get("char_start"), sp.get("char_end")
        if not isinstance(cs, int) or not isinstance(ce, int) or ce <= cs or (cs == 0 and ce >= len(text or "")):
            passthrough.append(f)   # 전범위/무좌표 — 실행기 not_local 스킵 경로 그대로
            continue
        buckets.setdefault(_pidx(cs), []).append(f)

    out: list[dict] = []
    for idx, fs in buckets.items():
        if idx < 0 or len(fs) == 1:
            out.extend(fs)
            continue
        fs = sorted(fs, key=lambda f: (f.get("span") or {}).get("char_start", 0))
        cats: list[str] = []
        for f in fs:
            c = f.get("category")
            if c and c not in cats:
                cats.append(c)
        sev = min([(f.get("severity") or "S2") for f in fs], key=_severity_rank)
        s0 = min((f.get("span") or {}).get("char_start", 0) for f in fs)
        e0 = max((f.get("span") or {}).get("char_end", 0) for f in fs)
        out.append({"category": fs[0].get("category"), "severity": sev,
                    "span": {"char_start": s0, "char_end": e0, "text": (text or "")[s0:e0]},
                    "metric": {"grouped": len(fs), "categories": cats},
                    "_group_cats": cats})
    out.sort(key=lambda f: (f.get("span") or {}).get("char_start", 10 ** 12))   # 문서 순서(재현 가능)
    return out + passthrough


def build_group_directive(finding: dict) -> str:
    """병합 finding 용 지시 합성 — 대원칙 6 + 그룹의 카테고리별 처방 전부(중복 제거·등재 순)."""
    cats = finding.get("_group_cats") or [finding.get("category", "")]
    prescs: list[str] = []
    for c in cats:
        p = directive_for(c)
        if p and p not in prescs:
            prescs.append(p)
    if not prescs:
        return _PRIME_DIRECTIVES
    return _PRIME_DIRECTIVES + "\n" + "\n".join("[이 구간의 처방]\n" + p for p in prescs)


# HM-6 하이브리드 1단 — 통짜 이전-전용 변환 directive(프롬프트 감사 통과분 2026-08-20: HM-4 스팬 처방 합성 +
#   대사 원형 보존 명문·속말→따옴표 대사 고정(재탐지 이중 변환 차단)·감정 몸 경로 분기·복선 판별자 근접 배치).
BULK_TRANSFER_DIRECTIVE = (
    "이 회차 지문에서 서술자가 장면 위에 얹은 정리·자평·논평과, 감정을 이름으로 선언하는 문장을 그 자리에서 "
    "옮겨라. 다만 이상한 것을 짚지 않고 무심히 흘리는 문장은 복선 장치이니, 해설로 풀지도 극화하지도 말고 "
    "그대로 둔다.\n\n"
    "- 옮기는 길: 내용이 인물이 할 법한 말이면 따옴표를 붙인 대사로, 몸으로 보일 수 있으면 행동·감각으로 "
    "옮긴다. 감정은 그 감정이 드러나는 몸짓·호흡·시선·목소리로 옮기고, 감정어를 다른 감정어로 갈지 마라.\n"
    "- 옮길 것이 사실·정보·유머가 아니라 일반론이면, 그 자리를 인물의 구체적인 동작 하나로 대신한다.\n"
    "- 새로 세우는 말은 이 인물이 평소 쓰는 말투 안에서 고른다.\n"
    "- 사실·설정·수치·인과·고유명사·복선은 한 글자도 바꾸지 마라.\n"
    "- 유머·정보·복선은 잃지 말고 층위만 바꾼다. 문장을 통째로 들어내지 마라.\n"
    "- 이미 있는 대사는 문장부호까지 원문 그대로 두고, 새 사건·새 사실은 더하지 마라.\n"
    "- 분량을 유지하라."
)

# HM-6 검사 상수 — 회차 축소율 하한(이전-전용이면 분량 거의 불변: 8%+ 축소 = 삭제형 의심 폴백. revise_prose
#   자체 길이 가드는 0.5x라 이 계약의 검사가 못 됨 — 감사 중대 5) · 대사 라인 원형 검사(감사 치명 1).
BULK_SHRINK_FLOOR = 0.92


def _dialogue_lines(text: str) -> list[str]:
    """따옴표로 시작하는 대사 라인 목록(공백 정규화) — 통짜 변환의 대사 원형 불변 검사 원자료."""
    out = []
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if s.startswith(('"', "“", "”")):
            out.append(" ".join(s.split()))
    return out


def bulk_transfer_pass(generator, ontology, checker, chapter_no: int, text: str, *,
                       service=None, humanize_provider=None) -> tuple[str, dict]:
    """HM-6 하이브리드 1단 — 회차 통짜 이전-전용 변환 1콜(revise_prose 재사용·래퍼 사실 계약 하류).

    검사(감사 조건): ① 회차 G-A/G-B(service 있으면) ② 기존 대사 라인 원형 불변(하나라도 소실/변형 → 폴백)
    ③ 회차 축소율 하한(8%+ 축소 → 폴백). 실패/무변경 → 원문 유지(무강제·결측 정직). 반환 (text, info)."""
    info: dict = {"bulk": None}
    try:
        ids = sorted(set(ontology.scan_present_ids(text)))
        before_res = checker.check_text(text, ontology, chapter_no, ids) if service is not None else None
        orig = getattr(generator, "provider", None)
        swap = humanize_provider is not None and orig is not None

        # HM-6 재시도(스팬 루프와 동형·SSOT 2회 상한): 검사 실패 사유를 보존 앵커(원문 인용)로 붙여 1회 재시도.
        #   e2e 실측(2026-08-21): 대사 검사가 한 줄 불일치로 통짜 전체를 폴백시켜 전량 스팬 경로(수십만 tok)로
        #   비용이 역류 — 재시도가 그 과민 트리거를 흡수한다. 인용은 항상 원문 쪽(핑크 엘리펀트 방향 감사 준수).
        directive = BULK_TRANSFER_DIRECTIVE
        fail = None
        after = text
        for _attempt in (1, 2):
            if swap:
                generator.provider = humanize_provider
            try:
                after = generator.revise_prose(directive, text, span_text="",
                                               ids=ids, ontology=ontology, chapter_no=chapter_no)
            finally:
                if swap:
                    generator.provider = orig
            if not (after or "").strip() or after == text:
                info["bulk"] = "unchanged"
                return text, info
            # 감사 실무 메모 반영: 결정론 검사(축소율·대사)는 함께 평가해 복수 사유를 한 번의 재시도에 같이
            #   싣는다(재시도 예산 1발 — 하나만 고치고 다시 폴백하는 소진 방지). 인용 절단 시 잔여도 보존 명시.
            fail = None
            _rns = []
            if len(after) < len(text) * BULK_SHRINK_FLOOR:
                fail = "shrink_fallback"
                info["delta"] = len(after) - len(text)
                _rns.append("직전 시도는 분량을 8% 넘게 줄였다. 문장을 들어내지 말고 분량을 유지한 채 다시 옮겨라.")
            _missing = [d for d in _dialogue_lines(text) if d not in set(_dialogue_lines(after))]
            if _missing:
                fail = fail or "dialogue_fallback"
                info["dialogue_missing"] = _missing[:5]
                _dn = ("다음 대사 줄은 문장부호까지 원문 그대로 유지하라: " + " / ".join(_missing[:5]))
                if len(_missing) > 5:
                    _dn += " (그 밖의 대사 줄도 전부 원문 그대로 둔다)"
                _rns.append(_dn)
            _rn = "\n".join(_rns)
            if fail is None:
                if service is not None and before_res is not None:
                    g, _ = service._guardrail(text, after, before_res, ids, ontology, checker, chapter_no)
                    info["guard"] = {k: g.get(k) for k in ("passed", "G_A_passed", "G_B_passed", "length_ok", "reason")}
                    if not g.get("passed"):
                        fail = "guard_fallback"
                        _orig = []
                        for it in (g.get("claim_changes") or []):
                            try:
                                e_, k_, b_, _a = it
                                _orig.append(f"{e_}·{k_}={b_}")
                            except Exception:
                                continue
                        _rn = (("직전 시도가 다음 사실의 표현을 바꿨다. 원문의 표현은 이렇다: "
                                + "; ".join(_orig[:6]) + ". 이 표현을 글자 그대로 유지한 채 다시 옮겨라.")
                               if _orig else "직전 시도가 사실 표면을 바꿨다. 원문의 사실 표현을 글자 그대로 유지한 채 다시 옮겨라.")
            if fail is None or _attempt == 2:
                break
            info["retried"] = fail   # 재시도 기록(은폐 금지)
            directive = BULK_TRANSFER_DIRECTIVE + "\n[재시도 지시]\n" + _rn

        if fail is not None:
            info["bulk"] = fail      # 재시도 소진 후에도 실패 — 원문 유지(무강제)
            return text, info
        info["bulk"] = "changed"
        info["delta"] = len(after) - len(text)
        return after, info
    except Exception as e:
        info["bulk"] = "error"
        info["error"] = str(e)[:120]
        return text, info


def _layer_ratio_snapshot(text: str) -> dict | None:
    """대사 층위 비중 스냅샷 — HM-4 이전-전용 계약 관측 원자료(이전이 실제면 대사 비중이 는다). 부재 시 None."""
    try:
        from .humanize_detect import detect_layer_wall
        lw = detect_layer_wall(text)
        if lw:
            m = lw[0].get("metric") or {}
            return {"dialogue_para_ratio": m.get("dialogue_para_ratio"),
                    "dialogue_char_ratio": m.get("dialogue_char_ratio")}
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Claude provider 스코프 스왑 — 체시스 revise_prose 는 generator.provider 를 쓴다. 윤문 콜 동안만 provider 를
#   Claude 로 바꾸고 try/finally 로 복원한다(부재/실패 시 스왑 없이 기존 provider·결측 정직).
# ─────────────────────────────────────────────────────────────────────────────
def build_humanize_provider(settings):
    """config humanize_model('provider:model' 또는 'model')로 윤문 provider 생성 — create_role_provider 재사용.
    빈 값/키 부재/미등록이면 기본 provider 로 안전 폴백(create_role_provider 계약). 실패 시 None(스왑 생략)."""
    spec = (getattr(settings, "humanize_model", "") or "").strip()
    if not spec:
        return None   # 미지정: 스왑 없이 generator 기본 provider 사용(하위호환·결측 정직)
    try:
        from ..llm.factory import create_role_provider
        return create_role_provider(settings, spec)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 윤문 실행기 — HM-1a 탐지 스팬을 카테고리 처방으로 국소 수술(체시스 경유·사실 불변·폴백 원문·변경률 가드).
# ─────────────────────────────────────────────────────────────────────────────
def humanize_spans(generator, ontology, checker, chapter_no: int, text: str,
                   findings: list[dict], *, max_spans: int | None = DEFAULT_MAX_SPANS,
                   service=None, humanize_provider=None) -> tuple[str, list[dict]]:
    """탐지 findings 를 카테고리 처방으로 국소 윤문(체시스 rewrite_span_via_chassis 경유). 반환 (new_text, entries).

    각 스팬: HM-1a finding → 체시스 스팬 브릿지 → (스왑된 Claude provider 로) revise_prose 국소 수술 →
    변경률 가드 → 채택/폴백. entries = 스팬별 내역(humanize 영속용·투명성):
      {category, severity, char_start/end, before_len, after_len, changed, change_rate, rate_band,
       fallback, coverage_passed, guardrail_ok, author_review, note}
    무강제: 실패/폴백/과윤문은 원문 유지 + 항목 기록(침묵 폴백 금지). 스팬 격리(한 스팬 실패가 회차를 죽이지 않음).
    잔존 S1(윤문 못 걷어낸 결정적 티)은 author_review=True("작가 확인 요망" 표기·은폐 금지).
    """
    try:
        import tools.st11_span_rewrite as st11
    except Exception:
        return text, []   # 체시스 부재 → 윤문 없음(강등·원문 유지·결측 정직)

    selected = select_humanize_spans(findings, max_spans)
    if not selected:
        return text, []
    # HM-4: 문단 그룹핑 + 문서 순서 — 같은 문단 스팬의 stale_offset 연쇄 스킵 제거(전량 변환의 커버리지 결정론화).
    selected = group_findings_by_paragraph(text, selected)

    # Claude provider 스코프 스왑 준비 — 윤문 콜 동안만 generator.provider 를 Claude 로. 부재면 기존 provider.
    orig_provider = getattr(generator, "provider", None)
    swap = humanize_provider is not None and orig_provider is not None

    entries: list[dict] = []
    cur = text
    cur_res, cur_ids = None, None   # CE-5: check_text(cur) 스레딩 캐시 — 첫 스팬은 st11 이 계산, 이후 재사용(ids 동일 시)
    for finding in selected:
        cat = finding.get("category", "N")
        sev = finding.get("severity", "S2")
        entry = {
            "category": cat, "severity": sev,
            "char_start": (finding.get("span") or {}).get("char_start"),
            "char_end": (finding.get("span") or {}).get("char_end"),
            "before_len": 0, "after_len": 0, "changed": False, "change_rate": 0.0,
            "rate_band": None, "fallback": None, "coverage_passed": None, "guardrail_ok": None,
            "author_review": False, "note": None,
            "rejected_after_span": None, "guardrail_detail": None,   # CE-7: 반려 증거(guardrail 폴백 시에만 채움·기록 전용)
        }
        sp = _chassis_span_from_finding(cur, finding)
        if sp is None:
            # 국소 수술 불가(전범위 밀도 신호·좌표 무효) — 윤문 대상 아님(진입조차 안 함).
            #   author_review 는 "윤문 시도→못 걷어낸 결정적 티 잔존"의 정직 표기다(설계 §2③). not_local 은
            #   국소 수술 계약 밖이라 *시도 자체가 없는* 신호 — S1 이라도 author_review 를 켜지 않는다.
            #   (HM-1a detect_layer_wall 은 N-5 S1 을 무임계·상시 방출 → 켜면 매 회차 cry-wolf 로 잔존
            #    정직 신호의 의미가 소실. hair-trigger reader·no-whack-a-mole 원칙 정합.) 전범위 밀도 신호의
            #    정밀 좌표·수술은 후속 LLM 탐지 몫으로 설계상 명시(§2ⓑ②).
            entry["fallback"] = "not_local"
            entry["note"] = "전범위/밀도 신호 — 국소 스팬 아님(윤문 대상 아님·시도 없음)"
            entries.append(entry)
            continue
        # 선행 스팬 수리로 cur 오프셋이 밀렸을 수 있음 — span_text 로 cur 에서 재-앵커(SP-1 repair_spans 계보).
        sp_text = sp["span_text"]
        occ = cur.count(sp_text)
        if occ == 1:
            i = cur.index(sp_text)
            sp = {**sp, "char_start": i, "char_end": i + len(sp_text)}
        elif occ == 0:
            entry["fallback"] = "stale_offset"   # 선행 수리가 이 스팬을 덮음 — 그 스팬만 원문 유지·전진
            entry["before_len"] = len(sp_text)
            entry["after_len"] = len(sp_text)
            entry["author_review"] = (sev == "S1")
            entries.append(entry)
            continue
        # occ>=2(모호)는 원 오프셋 유지 — 체시스 revise_prose _find_span 이 모호성 자체 판정(안전)

        entry["before_len"] = len(sp["span_text"])
        # HM-4: 병합 finding 은 그룹 처방 합성, 단독은 기존 경로(바이트 동일·하위호환).
        directive = (build_group_directive(finding) if finding.get("_group_cats")
                     else build_directive(cat))
        before_span = sp["span_text"]

        # HM-5(수정-검증 루프): SSOT "반복 윤문 최대 2회" 배선 — 수복 가능 실패(over_change/deletion_like/
        #   guardrail)는 실패 사유를 지시 말미에 붙여 1회 재시도한다(총 2회 상한·원전 방침). 물리 실패
        #   (span_not_found)는 즉시 폴백. 재시도 소진 후에도 실패면 기존 폴백 경로 그대로(원문 유지·작가 확인
        #   표기·증거 영속 불변). 재시도 지시는 수술 지시 예외 ⓑ·가드 위반은 measure-then-cite 로 목록 인용.
        _grp_cats = finding.get("_group_cats") or [cat]
        _base_directive = directive
        _span_missing = False
        fail = None          # 최종 실패 분류: "over_change" | "deletion_like" | "guardrail" | None
        for _attempt in (1, 2):
            if swap:
                generator.provider = humanize_provider
            try:
                r = st11.rewrite_span_via_chassis(
                    generator, ontology, checker, chapter_no, cur, sp,
                    directive=directive, service=service, mode="v2",
                    threaded_before_res=cur_res, threaded_before_ids=cur_ids)
            except ValueError as e:
                if "span_not_found" not in str(e):
                    raise                         # 스팬 매칭 외 예외는 전파(무음 흡수 금지)
                _span_missing = True
                break
            finally:
                if swap:
                    generator.provider = orig_provider   # 항상 복원(예외 경로 포함)

            # 변경률 기저 대칭화(적대검증 MED): 체시스는 v2 에서 검출 스팬을 *문단 전체로 확장*(expand_span_to_
            #   paragraphs)한 뒤 재작성하고 span_text_before(확장 문단)·span_text_after(확장 문단의 after)를 반환한다.
            #   before 를 확장 전 run(sp["span_text"])으로 잡으면 분모=run·분자=run↔확장문단 레벤슈타인의 비대칭이 되어
            #   변경률이 체계적으로 과대(정당 최소 수정을 over_change 로 오폴백·SSOT avg 왜곡)된다. 체시스가 반환하는
            #   span_text_before(=실제 재작성 대상 문단)를 기저로 써 after 와 같은 base 로 맞춘다(형제 repair_spans 계보).
            #   체시스가 before 를 안 주면(구 반환·폴백) 확장 전 run 으로 안전 폴백(하위호환·결측 정직).
            before_for_rate = r.get("span_text_before") or before_span
            after_span = r.get("span_text_after", "") or before_for_rate
            rate = change_rate(before_for_rate, after_span)
            band = classify_change_rate(rate)
            cg = r.get("coverage_guard") or {}
            gr = r.get("guardrail")
            guard_ok = (None if gr is None else (not gr.get("error") and bool(gr.get("passed", True))))
            chassis_changed = bool(r.get("changed"))
            # CE-5: 다음 스팬 before 로 스레딩. 기본값 = '이 스팬 cur 불변'(미채택 시) → 이 스팬 before_res.
            #   채택되면 아래에서 after_res 로 덮어쓴다. ids 불일치 시 st11 이 다음 콜에서 전문 재추출로 폴백.
            #   (재시도에도 같은 값 — before 상태는 두 시도에서 동일하다.)
            cur_res, cur_ids = r.get("before_res"), r.get("check_ids")

            fail = None
            if chassis_changed and band == "over":
                fail = "over_change"
            elif chassis_changed and any(c in ("N-1", "N-2") for c in _grp_cats) \
                    and len(after_span) < len(before_for_rate) * 0.75:
                fail = "deletion_like"
            elif chassis_changed and guard_ok is False:
                fail = "guardrail"
            if fail is None or _attempt == 2:
                break
            entry["retried"] = fail   # 재시도 기록(은폐 금지)
            if fail == "over_change":
                _rn = "직전 시도는 이 문단을 30% 넘게 바꿨다. 원문 문장을 대부분 그대로 두고, 지목된 자기해설만 필요한 만큼만 옮겨라."
            elif fail == "deletion_like":
                _rn = "직전 시도는 이 문단을 25% 넘게 줄였다. 문장을 들어내지 말고 길이를 보존하며 층위만 옮겨라."
            else:
                # 감사 치명 3: 바뀐(틀린) 쪽이 아니라 **원문 표면**을 인용한다(보존 앵커 — 캐논 주입 패턴 동형·
                #   measure-then-cite: 인용이 원문에 실재해야 모델이 대조·보존한다).
                _orig = []
                if isinstance(gr, dict):
                    for it in (gr.get("claim_changes") or []):
                        try:
                            e_, k_, b_, _a = it
                            _orig.append(f"{e_}·{k_}={b_}")
                        except Exception:
                            continue
                _orig = _orig[:6]
                _rn = (("직전 시도가 다음 사실의 표현을 바꿨다. 원문의 표현은 이렇다: " + "; ".join(_orig)
                        + ". 이 표현을 글자 그대로 유지한 채 다시 옮겨라.") if _orig
                       else "직전 시도가 사실 표면을 바꿨다. 원문의 사실 표현을 글자 그대로 유지한 채 다시 옮겨라.")
            directive = _base_directive + "\n[재시도 지시]\n" + _rn

        if _span_missing:
            entry["fallback"] = "span_not_found"
            entry["after_len"] = entry["before_len"]
            entry["author_review"] = (sev == "S1")
            entries.append(entry)
            continue

        # before_len/after_len 도 변경률과 같은 확장 문단 기저로 맞춘다(advisory 이나 SSOT 내 정합) — 최종 시도 기준.
        entry["before_len"] = len(before_for_rate)
        entry["after_len"] = len(after_span)
        entry["change_rate"] = rate
        entry["rate_band"] = band
        entry["coverage_passed"] = cg.get("passed")
        entry["guardrail_ok"] = guard_ok

        # 변경률 상한 초과(과윤문) → 그 스팬만 폴백(원문 유지·기록). 체시스가 이미 바꿨어도 채택하지 않는다.
        if fail == "over_change":
            entry["changed"] = False
            entry["fallback"] = "over_change"
            entry["after_len"] = entry["before_len"]
            entry["author_review"] = (sev == "S1")   # 걷어내려다 과윤문 폴백 — S1 잔존 정직
            entry["note"] = "변경률 30% 초과 — 과윤문 폴백(원문 유지)"
            entries.append(entry)
            continue

        # HM-4(이전-전용 계약 검사·감사 중대 4): N-1/N-2 수술이 문단 길이를 25%+ 줄였으면 '이전'이 아니라
        #   '삭제'다 — 폴백(원문 유지·기록). 이전(층위 이동)은 길이가 대체로 보존된다. 거친 하한일 뿐 최종
        #   판정은 정독 몫(무강제 — 이 검사는 계약 위반 의심의 정직 기록+보수 폴백이다).
        if fail == "deletion_like":
            entry["changed"] = False
            entry["fallback"] = "deletion_like"
            entry["after_len"] = entry["before_len"]
            entry["author_review"] = (sev == "S1")
            entry["note"] = "이전-전용 계약 위반 의심 — 문단 길이 25%+ 축소(삭제형 폴백·원문 유지)"
            entry["rejected_after_span"] = r.get("span_text_after")   # CE-7 계보 — 무엇이 삭제형이었나 증거
            entries.append(entry)
            continue

        # G-B 사실 표면 가드 불통과/오류 → 채택 금지(설계 §2ⓒ '의미 불변을 기계 가드로 상회'의 이행).
        #   체시스는 가드 결과를 계산만 하고 되돌리지 않으므로(advisory 반환) 소비부인 여기서 차단해야 한다
        #   — 실측 2026-07-13 ch10: guardrail_ok=False 수술 2건이 본문에 채택된 계약 위반의 소스 수리.
        #   None(가드 미실행 — service 부재 강등)은 결측 정직 기록만 하고 채택 유지(강등≠위반).
        #   HM-5: 여기 도달 = 재시도 1회 후에도 불통과(잔존 증거 영속 — '재시도 0' 시절 주석은 폐기).
        if fail == "guardrail":
            entry["changed"] = False
            entry["fallback"] = "guardrail"
            entry["after_len"] = entry["before_len"]
            entry["author_review"] = (sev == "S1")
            entry["note"] = "G-B 사실 표면 가드 불통과/오류 — 수술 폴백(원문 유지)"
            # CE-7: 반려 증거 영속(VP-3 이 닫지 않은 윤문 스팬 경로) — 막힌 재작성문·바뀐 클레임을 기록만(자동 개입·재시도 0).
            #   현행은 가드 dict 를 guardrail_ok 불리언 하나로 접어 버려 「무엇이 왜 막혔나」가 소실된다(반려 스팬은
            #   본문에 안 남으므로 소급 복원 불가). CE-6 재현율 게이트의 분모이자 정당 반려 vs 추출 요동 위양성 구분의 증거.
            entry["rejected_after_span"] = r.get("span_text_after")
            if isinstance(gr, dict):
                # CE-8: claim_flaps 포함 — _guardrail(copilot.py:755)이 VP-3 2단 요동 강등분을 항상 싣는다.
                #   윤문은 편집 영역이 좁아 1단 영역 필터 강등이 잦은 경로라, 「정당 반려 vs 추출 요동 위양성」
                #   판별 증거가 바로 이 키다(CE-7 목적 그 자체 — 초판 누락을 PM 반증으로 교정).
                entry["guardrail_detail"] = {k: gr.get(k) for k in
                                             ("new_hard", "claim_changes", "claim_flaps", "new_keys_advisory",
                                              "reason", "G_A_passed", "G_B_passed", "length_ok")}
            entries.append(entry)
            continue

        if chassis_changed:
            entry["changed"] = True
            cur = r.get("full_after") or cur          # 채택본을 다음 스팬 기준으로(순차 replace 정합)
            _ar = r.get("after_res")                  # CE-5: 채택본의 추출 == 다음 스팬 before(st11 이 ids 동일 시 재사용)
            cur_res, cur_ids = (_ar, r.get("check_ids")) if _ar is not None else (None, None)
            if band == "under":
                entry["note"] = "변경률 5% 미만 — 저윤문(티 잔존 재확인 요망)"
                entry["author_review"] = (sev == "S1")   # 결정적 티인데 거의 안 바뀜 → 작가 확인
        else:
            # 체시스가 못 바꿈(무변경·길이가드·커버리지 폴백) — S1 잔존 정직 표기.
            entry["fallback"] = cg.get("fallback") or "unchanged"
            entry["author_review"] = (sev == "S1")
        entries.append(entry)

    # HM-4(이전-전용 계약 관측·감사 중대 4): N-1/N-2 수술이 있었으면 대사 층위 비중 before/after 를 meta 로
    #   기록한다 — 이전이 실제면 대사 비중이 는다(안 늘고 탐지만 줄면 삭제 의심). N-1/N-2 미포함 런은
    #   엔트리 불변(하위호환·기존 소비자 바이트 동일). advisory 기록 전용 — 판정·차단 0.
    if any(c in ("N-1", "N-2")
           for f in selected for c in (f.get("_group_cats") or [f.get("category")])):
        _lb, _la = _layer_ratio_snapshot(text), _layer_ratio_snapshot(cur)
        entries.append({
            "category": "meta:layer", "severity": None, "char_start": None, "char_end": None,
            "before_len": len(text), "after_len": len(cur), "changed": (cur != text),
            "change_rate": None, "rate_band": None, "fallback": None,
            "coverage_passed": None, "guardrail_ok": None, "author_review": False,
            "note": "HM-4 이전-전용 관측 — 대사 층위 비중 before/after",
            "layer_before": _lb, "layer_after": _la,
        })

    return cur, entries
