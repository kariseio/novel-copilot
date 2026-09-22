# -*- coding: utf-8 -*-
"""ST-12c 비앵커 재실현 패스 — 사실 뼈대에서 웹소설 레지스터로 다시 실현(원문 프로즈 미주입).

설계 SSOT: docs/design-st12-style-register.md §5 ST-12c · §9 검증 결과(이론 최종 판정: '-었다'
지배의 지배항은 *컨텍스트 내 프로즈 앵커* — 지시는 형태 무관하게 앵커를 못 이긴다. 비앵커 재실현만
분포+질을 동시에 달성). 프로브 실물: app/tools/reports/st12_probe_ch10/(P4V_음성카드.txt = 목표 산출물).

이 모듈은 *순수 엔진 부품*이다:
  · 입력 조립(대사·수치·고유명사 결정론 추출) — LLM 0콜.
  · P4-V 프롬프트 구조 조립(검증된 문안 최대 보존) — 긍정형 메뉴만(pink-elephant: 피할 어미·나쁜 예문 호명 0).
  · BoN 후보 결정론 리랭크 + 하드 실격(대사 유실/수치 누락/분량) — LLM 0콜.

**엔진 로드타임 tools/kiwipiepy import 금지(핵심)**: Kiwi 계측(style_pipeline.kiwi_style_metrics)은
  *리랭크 함수 안에서* lazy import 로만 태운다(style_pipeline 패턴과 동형). 부품 부재 시 리랭크 불가면
  '첫 유효 후보'로 강등(결측 정직).

**무강제**: 실현 콜·리랭크·가드는 전부 서비스 계층(copilot.rerender_chapter)이 오케스트레이션한다. 이 모듈은
  판정 라벨·색상·자동 임계 트리거를 만들지 않는다 — 값(대역 최근접 점수·실격 사유)과 순위만 낸다.
"""
from __future__ import annotations

import re

# ── 대사 추출: 따옴표/대시로 시작하는 행(순서 보존·원형 그대로). 프로브 dialogue_lines 방식.
#   웹소설 관습: 큰따옴표("...", 만연·굽은따옴표 포함)·대시(— / -) 시작 행이 발화. 홑따옴표(')는 속생각·
#   인용이 섞여 오탐이 크므로 대사 목록(불변 계약 대상)에서는 제외 — 속생각은 지문 레지스터로 재실현된다.
_DIALOGUE_START = ('"', '“', '”', '—', '―')

# ── 수치 토큰: 아라비아 숫자 + '한글 수사 + 단위' 결합 토큰. P2-A 에서 수치 4종 유실이 실측된 리스크의
#   소스 차단 — 뼈대에 "그대로 등장" 명시 + 리랭크 누락 하드 실격. 정밀도 우선(재현율보다) 설계 이유:
#   과추출(false positive)은 정당한 재실현 후보를 '수치 누락'으로 오실격시켜 무강제·품질을 해친다. 특히
#   맨 한글 수사(세·한·열…)를 greedy 접미로 뽑으면 '세상에·한나(인명)·열었다' 를 오탐한다(교착어 substring
#   결함 — MEMORY korean-agglutination-substring-pitfall). 그래서 한글 수사는 *바로 뒤에 단위/수량 명사가
#   붙을 때만* 그 결합구를 통째 토큰으로 뽑는다(예 '세 갈래'·'다섯 시'·'열두 점'). 단위 없는 맨 수사는 제외.
# 고유어/한자어 수관형사 어간(수사) — 이 뒤에 공백?+단위 명사가 올 때만 결합 추출.
_NUM_STEM = (r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열두|열세|열|스무|스물|서른|마흔|쉰|예순|"
             r"몇|여러|첫|둘째|셋째|넷째|반)")
# 흔한 단위/수량 명사(웹소설 서사에 실제 등장) — 이 어휘가 뒤따를 때 '수사/숫자+단위'를 한 토큰으로 확정한다.
_UNIT = (r"(?:점|시|분|초|시간|날|달|년|월|주|번|개|명|사람|마리|채|대|잔|병|장|권|갈래|겹|뼘|걸음|"
         r"발짝|층|칸|줄|가지|종|판|모금|모|자루|만|억|천|백|원|달러|미터|센티|밀리|킬로|그램|도|퍼센트|살|라인)")
# 아라비아 숫자는 *항상* 수치 토큰이다(숫자=사실 — P2-A 수치 유실 소스 차단). 단위가 뒤따르면 결합(45도·3만·3라인),
#   없으면 숫자만(예 'A4'→'4'). 단위 결합은 관형 접미('로'·'쯤')를 흡수하지 않아 '45도로'가 아니라 '45도'로 정규화된다
#   (후보가 '45도'만 써도 누락 판정 안 되게 — 과실격 방지). 맨 아라비아 숫자 추출은 의도된 계약(사실 보존 우선)이다.
_ARABIC_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?(?:\s*" + _UNIT + r")?")
_KOREAN_NUM = re.compile(_NUM_STEM + r"\s*" + _UNIT)


def extract_dialogue_lines(text: str) -> list[str]:
    """따옴표/대시로 시작하는 행을 순서 보존·원형 그대로 추출(프로브 dialogue_lines 방식).

    반환 목록은 재실현 프롬프트의 '대사 목록'(불변 계약)과 리랭크의 대사 보존률 판정 양쪽에 쓴다.
    행 단위(strip 후 시작 문자로 판정) — 렌더 공백 차이를 흡수하되 원형(strip 전 아님, strip 후 원문 행)을 담는다."""
    out: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s and s[0] in _DIALOGUE_START:
            out.append(s)
    return out


def extract_numeric_tokens(text: str, *, arabic_only: bool = True) -> list[str]:
    """원문에서 수치 토큰을 결정론 추출 — 순서 보존·중복 제거(첫 등장 순).

    P2-A 수치 유실 리스크의 소스 차단용. 반환 목록은 뼈대에 "그대로 등장" 명시 + 리랭크 누락 실격에 쓴다.
    **기본 = 아라비아 숫자만(파일럿 1차 보정 2026-07-14)**: 한글 수사 관용구('한 줄'·'네 번'·'일곱 개')까지
    하드 계약에 넣으면 표현 재구성이 본질인 재실현에서 전 후보가 구조적으로 실격된다(ch1·ch2 6/6 전멸 실측).
    플롯 수치는 아라비아 표기(1,240,000원 등)가 고정밀 신호이고, 한글 수사의 의미 보존은 G-B 클레임 표면
    비교·정독 게이트 소관. arabic_only=False 는 advisory 용도로만(하드 게이트 사용 금지)."""
    t = text or ""
    found: list[str] = []
    seen: set[str] = set()
    # 위치 순서 보존: 매치를 (start, token) 로 모아 정렬 후 중복 제거.
    hits: list[tuple[int, str]] = []
    patterns = (_ARABIC_NUM,) if arabic_only else (_ARABIC_NUM, _KOREAN_NUM)
    for rx in patterns:
        for m in rx.finditer(t):
            tok = re.sub(r"\s+", " ", m.group(0)).strip()   # 내부 공백 정규화(다섯  시→다섯 시)
            tok = tok.rstrip(",")   # SP-3 정독 적발: 꼬리 쉼표('내림 1,'→'1,')가 계약 토큰에 실리면
            #   표현을 바꾼 정당 후보('내림 1과')가 수치 누락으로 억울 실격된다. 내부 쉼표(1,240,000)는 유지.
            if tok:
                hits.append((m.start(), tok))
    for _, tok in sorted(hits, key=lambda x: x[0]):
        if tok not in seen:
            seen.add(tok)
            found.append(tok)
    return found


def entity_tokens_in_source(ontology, source: str) -> list[str]:
    """RR-1 소스 차단 — 재실현 대상 본문(source)에 *실제 등장하는* 명부 토큰만, 사람 단위로 그룹핑해 반환.

    괴담작 1화 캐논 파괴(6화 이후 데뷔 예정 인물이 재실현 후보 전원에 유출)의 소스 차단. 규칙:
      ① 엔티티 포함 조건 = **사람 단위 존재**(_entity_in_source — name(≥2자) 또는 별칭(≥2자) 중 하나라도
         본문에 존재). 어떤 토큰도 본문에 없는 인물은 목록에 절대 오르지 못한다(부재 인물 주입이 구조적으로
         불가능). name 이 1자인 엔티티는 라벨 대상에서 제외(보수 — 흔한 형태소와 substring 충돌).
      ② 그 위에, 같은 엔티티의 별칭 중 *본문에 발견된 것*만 병기 → "서준호(=준호)" 사람 단위 표기로 역할 뒤섞임 차단.
      ③ 별칭 매칭도 name 과 동일한 순수 substring(공백 무시 아님·원형). 짧은 별칭(예 '지연')이 '지연되었다'에
         걸리는 일반어 오탐이 남을 수 있으나(트레이드오프), 부재 인물 주입으로는 이어지지 않는다(핵심은 ①).

    반환: 사람 단위 라벨 목록(등록 순서·중복 제거). name 만 발견되면 "name", 별칭도 발견되면 "name(=alias1, alias2)".
    본문 텍스트가 비면 [](주입할 이름 없음). 하드 실격(B)·프롬프트 주입(A) 양쪽이 이 함수 하나를 공유한다."""
    hay = source or ""
    out: list[str] = []
    seen_labels: set[str] = set()
    for e in ontology.entities.values():
        name = (getattr(e, "name", "") or "").strip()
        # ① 존재 판정은 *사람 단위* — name(≥2자) 또는 별칭(≥2자) 중 하나라도 본문에 있으면 등장 인물이다.
        #   RR-1 초판은 '정식 name 전체 문자열'의 본문 존재를 요구했는데, 본문은 자연히 이름의 일부·별칭으로
        #   부르므로("카일 무쇠늑대"는 없고 "카일"·"단장"만 있음) 실제 등장 인물이 명부에서 통째로 빠지고,
        #   그 별칭들이 하드 실격(B)에서 '발명'으로 오탐돼 후보 전원이 실격되는 결함이 실측됨(2026-07-16
        #   퇴근작 1화 — korean-agglutination-substring-pitfall 의 정식명칭판). 부재 인물 차단 계약은 불변:
        #   어떤 토큰도 본문에 없는 인물은 여전히 구조적으로 제외된다.
        if len(name) < 2 or not _entity_in_source(e, hay):
            continue
        # ② 같은 엔티티의 별칭 중 본문에 실제 발견된 것만(≥2자 가드로 짧은 별칭 일반어 오탐 최소화) 병기.
        found_aliases: list[str] = []
        seen_alias: set[str] = {name}
        for al in (getattr(e, "aliases", None) or []):
            al = (al or "").strip()
            if len(al) >= 2 and al not in seen_alias and al in hay:
                seen_alias.add(al)
                found_aliases.append(al)
        label = name if not found_aliases else f"{name}(=" + ", ".join(found_aliases) + ")"
        if label not in seen_labels:
            seen_labels.add(label)
            out.append(label)
    return out


def _entity_in_source(e, hay: str) -> bool:
    """RR-1 사람 단위 존재 판정 — name(≥2자) 또는 별칭(≥2자) 중 하나라도 본문에 있으면 True.

    A(프롬프트 명부)·B(하드 실격) 공용 단일 규칙(SSOT). 본문이 인물을 정식 명칭이 아니라 이름 일부·별칭으로
    부르는 정상 케이스("카일 무쇠늑대"→"카일"·"단장")를 등장으로 인정한다. 어떤 토큰도 없는 인물=부재(차단 불변)."""
    name = (getattr(e, "name", "") or "").strip()
    if len(name) >= 2 and name in hay:
        return True
    for al in (getattr(e, "aliases", None) or []):
        al = (al or "").strip()
        if len(al) >= 2 and al in hay:
            return True
    return False


def source_names_present(ontology, source: str) -> set[str]:
    """RR-1 하드 실격의 매칭 재료 — 본문(source)에 *등장하는 인물*의 명부 토큰 전체 집합(name+별칭, ≥2자).

    존재 판정은 _entity_in_source(사람 단위) 공유. 등장 인물이면 그 사람의 모든 표기(정식 name·전체 별칭)를
    정당 토큰으로 반환한다 — 사실 불변 계약은 '사람'에 대한 것이지 표기에 대한 것이 아니므로, 원문이 '백작'이라
    부른 인물을 후보가 '레오하르트 백작'으로 실현해도 발명이 아니다. 부재 인물의 토큰은 여전히 전부 실격 재료."""
    present: set[str] = set()
    hay = source or ""
    for e in ontology.entities.values():
        if not _entity_in_source(e, hay):
            continue
        name = (getattr(e, "name", "") or "").strip()
        if len(name) >= 2:
            present.add(name)
        for al in (getattr(e, "aliases", None) or []):
            al = (al or "").strip()
            if len(al) >= 2:
                present.add(al)
    return present


def foreign_roster_tokens(candidate: str, ontology, source: str) -> list[str]:
    """RR-1 계약 가드(B) — 후보(candidate)에 등장하는 명부 토큰 중 *원문(source)에는 없는* 것(순서·중복 제거).

    이는 검출기 증식이 아니라 재실현의 기존 '사실 불변 계약' 집행이다: 재실현은 뼈대·대사·수치·고유명사를 원문
    그대로 실현해야 하며(rerender._CONTRACT_HEAD 1항), 원문에 없던 명부 인물이 후보에 나타나면 캐논 위반(발명)이다.
    매칭 규칙은 A(entity_tokens_in_source)와 공유 — name ≥2자만 검사(1자·짧은 토큰 오탐 방지). 빈 목록=위반 없음."""
    src_present = source_names_present(ontology, source)
    cand = candidate or ""
    out: list[str] = []
    seen: set[str] = set()
    for e in ontology.entities.values():
        for nm in [getattr(e, "name", "")] + list(getattr(e, "aliases", None) or []):
            nm = (nm or "").strip()
            if len(nm) < 2 or nm in seen:
                continue
            if nm in src_present:            # 원문에 있던 토큰은 계약상 정당(실현 대상) — 위반 아님
                continue
            # SP-3 실측(13화 재파일럿): '열넷째 칸'(서수)이 명부 인물 '넷째'에 순수 substring 으로 물려
            #   전 후보 오실격 — 교착어 substring 함정의 어두판. 이름은 어두에 서므로 매칭 위치 앞 문자가
            #   한글이면 다른 단어의 꼬리다(발명 판정=실격을 늘리는 방향에만 경계 가드 — 존재 판정은 불변).
            if re.search(r"(?<![가-힣])" + re.escape(nm), cand):   # 원문엔 없는데 후보에 등장 = 발명(캐논 위반)
                seen.add(nm)
                out.append(nm)
    return out


# ── P4-V 프롬프트(검증된 문안 최대 보존, design §5 ST-12c · §2 승인 분해) ───────────────────────
# 불변 계약: 사건·사실·인과·순서·수치 그대로 / 대사 전부 순서대로 한 글자도 바꾸지 말 것 /
#   시점 유지 / 분량 90~110%. (사실 축 하드 계약)
# SP-3(2026-08-18 감사 C2): 고유명사 '표기' 권위를 뼈대에서 이름 목록으로 이관 — 뼈대가 계획 층
#   (confirmed_story)일 수 있게 되면서 계획 오기("이도윤")가 계약 1항으로 정당화되던 채널 차단.
#   이름 문장은 build 에서 이름 목록이 실릴 때만 조립(실재하지 않는 목록을 가리키는 dangling 금지 — M1 계보).
_CONTRACT_HEAD = (
    "너는 웹소설 작가다. 아래 '사실 뼈대'와 '대사 목록'을 재료로, 이 회차를 웹소설 본문으로 다시 써라.\n"
    "다음은 절대 계약이다:\n"
    "1) 사건·사실·인과·순서·수치는 뼈대 그대로 실현하라. 뼈대에 없는 사건·설정·수치를 지어내지 마라."
)
_NAME_AUTHORITY = " 인물 이름의 표기는 아래 이름 목록에 적힌 표기를 쓴다."
_CONTRACT_DIALOGUE = "\n2) 아래 대사 목록의 모든 대사를, 목록에 적힌 순서 그대로, 한 글자도 바꾸지 말고 본문에 그대로 넣어라.\n"
# SP-3(감사 M1): 비앵커 콜에는 '유지'의 준거(문체 규칙 블록)가 없다 — 인칭을 값으로 실체화.
#   pov 미전달("")은 구 문안 유지(하위호환 — 구 호출 시그니처 바이트 동일).
_POV_LINES = {
    "first": "3) 이 회차는 1인칭 주인공 시점으로 서술한다.\n",
    "": "3) 시점(서술 인칭)을 유지하라.\n",
}
_POV_LINE_DEFAULT = "3) 이 회차는 3인칭 시점으로 서술한다.\n"
# 분량 계약은 절대 자수로 조립한다(파일럿 1차 보정 2026-07-14): 구 문안 "원 회차의 90~110%"는 모델이
#   원문을 못 보는 비앵커 패스에서 **계산 불가능한 지시**였다(후보 분량 표류 → 6/6 실격 실측). 프로브가
#   절대 수치("4000~4800자")를 쓴 이유 — build_rerender_prompt 가 원문 길이에서 90~110% 자수를 산출한다.
_CONTRACT_TAIL = "본문만 출력하라 — 머리말·설명·메타·장면 표지 금지."

# 레지스터·기계 규칙 블록(전부 긍정형 — 승인 분해상 '분포를 움직이는 활성 성분'). design §2:
#   속생각 입말 혼합 리듬·현재형·명사문 + 동일 종결 3연속 전 전환. pink-elephant 비저촉(피할 어미·나쁜
#   예문 호명 0 — 긍정 메뉴만). 프로브 P4-V 문안 준수.
_REGISTER_RULES = (
    "[웹소설 레지스터 — 이 결로 서술하라]\n"
    "· 서술 사이사이에 인물의 속생각을 입말로 섞어 리듬을 만들어라(지문과 속생각이 교차하는 호흡).\n"
    # ST-14 FIX-4: 구 문안이 '판단·반응의 순간은 현재형으로' 단일 대체형을 지목 → 모델이 장면 단위로 현재형을
    #   과적용(ㄴ다 벽). 대체형을 *다형 메뉴*로 열고, 시제 전환은 문장 단위 악센트로만 제한한다(긍정형·pink-elephant
    #   준수: 피할 형태 호명 없음). 장면의 기본 시제는 과거 유지 — 순간을 짚는 수단은 여러 결 중에서 고른다.
    "· 판단·반응의 순간은 그 결에 맞는 수단으로 짚어라 — 입말 판단('~겠지'·'~구나')"
    ", 또는 한 문장짜리 현재형 악센트. 여러 결을 섞어 순간마다 다르게.\n"
    "· 시제 전환은 문장 단위 악센트로만 써라 — 장면의 기본 시제는 과거로 유지하라(순간을 짚는 한 문장만 결을 바꾼다).\n"
    "· 같은 종결이 세 문장 연속되기 전에 다른 종결·다른 층위(속생각·짧은 대사·현재형 악센트)로 전환하라.\n"
    # 파일럿 2차 보정: 카드 렌즈(계산·자평)가 저대사 회차에서 벽면 독백으로 과적용 → 정독 패배("과잉
    #   내면독백·계산 반복·자기해설"). 쿼터는 긍정형(C-4 계보) — 같은 비유의 기계 반복만 조인다.
    "· 속생각의 비유·자기 평은 매번 다른 결로, 결정적 순간에 아껴 써라 — 장면 자체(행동·감각·대사)가"
    " 말하게 하고, 속생각은 그 사이를 짧게 잇는 숨이다.\n"
    # MS-2(2026-07-16): 다형 메뉴가 '수단 선택'까지만 다형이고 수단 내부 형태가 한 꼴에 수렴하는 실측
    #   ('-로.' 명사구 파편 3회/화 — 새 틱). 비유 순환 룰(위)의 대칭 확장 — 수단과 맺음꼴 양쪽을 순환.
    #   서술형·비열거(특정 꼴 호명 0 — priming 차단).
    "· 장면을 끊는 수단은 한 가지에 기대지 말고 번갈아 써라 — 같은 수단을 다시 집기 전에 다른 수단을"
    " 먼저 거치고, 짧은 맺음말의 꼴도 매번 새로 골라라."
)


def build_rerender_prompt(
    *,
    narrator_card: str,
    skeleton: str,
    dialogue_lines: list[str],
    numeric_tokens: list[str],
    proper_nouns: list[str],
    orig_chars: int = 0,
    pov: str = "",
    skeleton_is_story: bool = False,
) -> tuple[str, str]:
    """P4-V 프롬프트(system, user) 조립 — 검증된 프로브 문안 구조 보존.

    구성: 불변 계약(분량은 orig_chars 에서 90~110% 절대 자수로 — 비앵커라 상대 지시는 계산 불가) +
    (음성 카드) + 레지스터·기계 규칙 + 뼈대 + 대사 목록 + 수치·고유명사 목록.
    narrator_card 빈 값이면 음성 카드 블록 생략(3인칭·voice 부재 — 하위호환·바이트 최소). 긍정형 전용.

    RR-1: proper_nouns 는 이제 *재실현 대상 본문에 실제 등장하는* 명부 토큰만(entity_tokens_in_source 산출·사람
    단위 그룹핑 "이름(=별칭)"). 온톨로지 전 명부를 평평히 주입하던 구 경로가 미등장(데뷔 전) 인물을 후보에 유출시켜
    캐논을 파괴한 괴담작 1화 사건의 소스 차단 — 호출부(copilot.rerender_chapter)가 before_text 로 산출해 넘긴다.

    SP-3(2026-08-18 감사 반영): pov 는 인칭 값 실체화(M1 — 비앵커 콜엔 '유지'의 준거가 없다),
    skeleton_is_story=True 면 뼈대가 확정 스토리(3인칭 현재형 불릿)라 층위 라벨을 얹는다.
    둘 다 미전달이면 구 프롬프트 바이트 동일(하위호환)."""
    if orig_chars > 0:
        length_line = (f"4) 분량은 {int(orig_chars * 0.9)}~{int(orig_chars * 1.1)}자 안팎으로 써라"
                       "(뼈대의 사건 밀도에 맞게 충실히 실현).\n")
    else:
        length_line = "4) 분량은 뼈대의 사건 밀도에 맞게 충실히 실현하라.\n"
    pov_line = _POV_LINES.get((pov or "").strip(), _POV_LINE_DEFAULT)
    head = (_CONTRACT_HEAD + (_NAME_AUTHORITY if proper_nouns else "")
            + _CONTRACT_DIALOGUE + pov_line)
    sys_parts = [head + length_line + _CONTRACT_TAIL]
    if (narrator_card or "").strip():
        sys_parts.append("[서술자 음성(참조 전용): 이 태도로 이번 회차의 지문을 새 문장으로 서술하라]\n" + narrator_card.strip())   # VL-1(대시 시연도 제거)
    sys_parts.append(_REGISTER_RULES)
    system = "\n\n".join(sys_parts)

    if skeleton_is_story:
        # 포인터는 이 프롬프트에 실재하는 이름만(감사 M1 — 존재하지 않는 블록을 가리키는 바이트 동일성은 착시).
        user_parts = ["[사실 뼈대(확정 스토리)]\n"
                      "이 목록은 사건과 순서를 지정한다. 인칭·시제·문장은 위 계약과 [웹소설 레지스터]를 따른다.\n"
                      + (skeleton or "").strip()]
    else:
        user_parts = ["[사실 뼈대]\n" + (skeleton or "").strip()]
    if dialogue_lines:
        user_parts.append(
            "[대사 목록 — 이 대사를 순서 그대로, 한 글자도 바꾸지 말고 전부 넣어라]\n"
            + "\n".join(dialogue_lines))
    # 수치·고유명사 목록 병기("아래 수치와 이름은 전부 그대로 등장해야 한다")
    ref_lines = []
    if numeric_tokens:
        ref_lines.append("수치: " + ", ".join(numeric_tokens))
    if proper_nouns:
        ref_lines.append("이름: " + ", ".join(proper_nouns))
    if ref_lines:
        user_parts.append(
            "[아래 수치와 이름은 본문에 전부 그대로 등장해야 한다]\n" + "\n".join(ref_lines))
    user = "\n\n".join(user_parts)
    return system, user


# ── 리랭크 하드 실격 + 대역 최근접 점수(결정론) ─────────────────────────────────────────────
def _norm_ws(s: str) -> str:
    """공백 무시 매칭용 — 모든 공백류 제거(대사 보존률·수치 누락 판정의 렌더 공백 차이 흡수)."""
    return re.sub(r"\s+", "", s or "")


def dialogue_preservation(candidate: str, dialogue_lines: list[str]) -> float:
    """대사 보존률 — 원문 대사 각각이 후보 본문에 (공백 무시) 부분문자열로 존재하는 비율. 1.0=전부 보존.

    대사 목록이 비면 1.0(보존할 대사 없음 = 위반 없음). 순서는 이 척도에서 판정하지 않는다(존재만) —
    순서 계약은 프롬프트가 지고, 리랭크는 '유실 0'을 하드 실격으로 강제한다(설계 §5 하드 실격)."""
    if not dialogue_lines:
        return 1.0
    hay = _norm_ws(candidate)
    hit = sum(1 for d in dialogue_lines if _norm_ws(d) and _norm_ws(d) in hay)
    return hit / len(dialogue_lines)


def missing_numerics(candidate: str, numeric_tokens: list[str]) -> list[str]:
    """후보 본문에서 (공백 무시) 누락된 수치 토큰 목록. 빈 목록=전부 보존(수치 무결)."""
    hay = _norm_ws(candidate)
    return [t for t in numeric_tokens if _norm_ws(t) and _norm_ws(t) not in hay]


def length_ratio(candidate: str, original: str) -> float:
    """분량 비율 = len(후보)/len(원문). 원문 0 이면 1.0(비교 불가·안전)."""
    return len(candidate or "") / max(1, len(original or ""))


# 인간 대역(design §1·kiwi_human_band.HUMAN_BAND) — 리랭크 점수의 대역 상수. tools 없이도 산술이 돌게
#   여기 동결 수치를 둔다(계측값 top_ratio/max_run/da_ratio 만 tools 로 산출·부재 시 강등).
_TOP_RATIO_BAND = (0.347, 0.595)   # ending_profile.top_ratio 인간 대역
_MAX_RUN_BAND_HI = 14              # ending_profile.max_run 인간 대역 상한(tie-break)
_DA_BAND_MEAN = 0.233              # da_streak.ratio 인간 대역 평균(HUMAN_BAND) — 온건화 tie-break 기준점


def _band_distance(value, lo: float, hi: float) -> float:
    """대역 최근접 거리 — 대역 안이면 0, 밖이면 경계까지의 절대 거리. value None 이면 큰 페널티(계측 불가)."""
    if value is None:
        return float("inf")
    if lo <= value <= hi:
        return 0.0
    return (lo - value) if value < lo else (value - hi)


# 하드 실격 임계(설계 §5) — 대사 보존률 100% 미만 / 수치 누락 존재 / 분량 0.8x 미만·1.3x 초과.
LEN_MIN = 0.8
LEN_MAX = 1.3

# 말미 종결 문자(story_pass_prompts._TERMINALS 동일 바이트 — 2026-08-18 13화 실측: 프로바이더 절단으로
#   마지막 문장이 잘린 후보가 대사 보존 100%·분량 0.95x 로 전 하드 검사를 통과해 승자까지 됐다.
#   스토리 패스의 prose_ok 에는 있던 검사가 재실현 실격 축에 미배선이던 코드 갭 — 소스 차단).
_TERMINALS = '.!?…"”」』'

# ST-14 FIX-2: 무중단 동일 종결 키 run 하드 실격 임계 — 국소 무중단 run 이 이 값 이상인 후보는 실격.
#   전역 지표(top_ratio·max_run)만으로는 국소 무중단 벽(12연속 등)이 12≤14 로 통과하던 R3 급소를 닫는다.
#   기존 하드 실격 계보(대사·수치·분량)와 동형 — 재실현 내부 채택 기준이므로 헌법 무강제 예외 허용(티켓 명시).
UNINTERRUPTED_RUN_DQ = 10


def _uninterrupted_run_max(text: str):
    """후보 본문의 무중단 동일 종결 키 run 최대값(형태 불문·대사 리셋) — FIX-1 공용 함수 재사용(중복 구현 금지).

    엔진 로드타임 tools import 0 계약 유지: import 는 함수 안·lazy·try/except. 계측 불가(부품 부재·실패) 시
    None(결측 정직 — 하드 실격에 쓰지 않고, 리랭크 tie-break 에서 결측 페널티로만 취급)."""
    try:
        import tools.kiwi_metrics as km
        runs = km.uninterrupted_ending_runs(text or "", threshold=1)
    except Exception:
        return None
    return max((r["n_sent"] for r in runs), default=0)


def metric_no_harm(before_ep: dict | None, after_ep: dict | None,
                   before_urm, after_urm) -> tuple[bool, list[str]]:
    """ST-14 라이브 보정: 채택 후보(최종화 완료본)가 *원문보다* 계측 축에서 후퇴하면 기각 — 결정론 무해 가드.

    배경(실측 2026-07-15): --force 재적용에서 '유일한 유효 후보'가 원문(top 0.606)보다 나쁜 0.723 으로
    채택됨 — 리랭크는 후보 *간* 비교만 하고 원문 대비 무해는 아무도 확인하지 않았다(정독 게이트는 완패만
    거른다). 이 가드가 그 구멍을 닫는다: 후보는 원문 대비 ①top_ratio 인간 대역 거리 ②무중단 동일 종결 키
    run 최대, 두 축 모두 *나빠지지 않아야* 채택 가능(동률 허용 — 분포 동률이어도 정독·질 개선 채택 가치 있음).

    결측 정직: 어느 쪽이든 값이 없으면(None) 그 축 비교는 생략한다(없는 계측으로 기각하지 않는다).
    반환 (ok, reasons)."""
    reasons: list[str] = []
    b_top = (before_ep or {}).get("top_ratio")
    a_top = (after_ep or {}).get("top_ratio")
    if isinstance(b_top, (int, float)) and isinstance(a_top, (int, float)):
        b_d = _band_distance(b_top, *_TOP_RATIO_BAND)
        a_d = _band_distance(a_top, *_TOP_RATIO_BAND)
        if a_d > b_d + 1e-9:
            reasons.append(f"대역 거리 후퇴(top {b_top}→{a_top})")
    if isinstance(before_urm, (int, float)) and isinstance(after_urm, (int, float)) and after_urm > before_urm:
        reasons.append(f"무중단 종결 run 후퇴({before_urm}→{after_urm})")
    return (not reasons), reasons


_UNSET = object()   # evaluate_candidate original_urm 미주입 표식(None=계측 불가와 구분)


def evaluate_candidate(candidate: str, *, original: str,
                       dialogue_lines: list[str], numeric_tokens: list[str],
                       kiwi_metrics=None, original_urm=_UNSET, foreign_tokens=None) -> dict:
    """후보 1개의 실격 사유 + 대역 점수를 결정론 산출(LLM 0콜).

    kiwi_metrics: style_pipeline.kiwi_style_metrics(candidate) 결과(dict) 또는 None(계측 불가·강등).
      리랭크 서비스가 lazy import 로 산출해 주입한다(엔진 로드타임 tools import 금지 계약).
    foreign_tokens(RR-1 B): 이 후보에 등장하되 *원문에는 없는* 명부 인물 토큰 목록(foreign_roster_tokens 산출).
      서비스가 ontology+source 로 산출해 주입한다(엔진은 도메인 온톨로지 타입에 의존하지 않음 — kiwi_metrics 주입 동형).
      비지 않으면 하드 실격("원문에 없는 명부 인물 발명") — 검출기 증식이 아니라 재실현의 사실 불변 계약 집행.

    반환: {disqualified: bool, reasons: [str], dialogue_pres, missing_numerics: [str], length_ratio,
           top_ratio, max_run, da_ratio, band_distance, max_run_over, uninterrupted_run_max, foreign_tokens}.
    점수(정렬 키)는 서비스가 rerank_candidates 로 조합한다.
    ST-14 FIX-2(라이브 보정으로 상대화): uninterrupted_run_max(무중단 동일 종결 키 run·형태 불문)가
    ≥10 이고 **원문(original_urm)보다 나쁠 때만** 하드 실격 — 절대 실격은 벽 있는 원문의 단계적 개선
    후보(예: 원문 12 → 후보 11)까지 차단해 '전 후보 실격→원문(더 큰 벽) 유지'를 낳는다(실측). 원문
    이하로 유지·개선하는 후보는 통과시키고, 잔여 벽은 최종화 스택(휴머나이즈)이 수리하며, 원문 대비
    무해는 metric_no_harm 이 최종 강제한다."""
    reasons: list[str] = []
    pres = dialogue_preservation(candidate, dialogue_lines)
    if pres < 1.0:
        reasons.append("대사 보존률 100% 미만")
    miss = missing_numerics(candidate, numeric_tokens)
    if miss:
        reasons.append("수치 토큰 누락: " + ", ".join(miss[:6]))
    ratio = length_ratio(candidate, original)
    if ratio < LEN_MIN or ratio > LEN_MAX:
        reasons.append("분량 이탈(0.8~1.3x 밖)")
    _tail = (candidate or "").rstrip()
    if not _tail or _tail[-1] not in _TERMINALS:
        reasons.append(f"말미 미종결(끝 문자 {_tail[-3:]!r} — 절단 의심)")
    # RR-1 B: 원문에 없는 명부 인물 토큰이 후보에 등장하면 하드 실격(사실 불변 계약 집행 — 발명 차단).
    foreign = list(foreign_tokens or [])
    if foreign:
        reasons.append("원문에 없는 명부 인물 발명: " + ", ".join(foreign[:6]))

    # ST-14 FIX-2: 무중단 동일 종결 키 run 국소 축 — 상대 하드 실격(run≥10 이고 원문보다 악화일 때만).
    #   계측 불가(None)면 실격 판정 보류(결측 정직 — 없는 계측으로 실격시키지 않는다). 리랭크 tie-break 에도 사용.
    uninterrupted_run_max = _uninterrupted_run_max(candidate)
    if original_urm is _UNSET:
        original_urm = _uninterrupted_run_max(original)
    if (uninterrupted_run_max is not None and uninterrupted_run_max >= UNINTERRUPTED_RUN_DQ
            and (original_urm is None or uninterrupted_run_max > original_urm)):
        reasons.append(f"무중단 동일 종결 run 과다(≥{UNINTERRUPTED_RUN_DQ}: {uninterrupted_run_max}"
                       f"{'' if original_urm is None else f' > 원문 {original_urm}'})")

    km = kiwi_metrics or {}
    ep = (km.get("ending_profile") or {}) if isinstance(km, dict) else {}
    da = (km.get("da_streak") or {}) if isinstance(km, dict) else {}
    top_ratio = ep.get("top_ratio")
    max_run = ep.get("max_run")
    da_ratio = da.get("ratio")
    band_dist = _band_distance(top_ratio, *_TOP_RATIO_BAND)
    max_run_over = None
    if isinstance(max_run, (int, float)):
        max_run_over = max(0, max_run - _MAX_RUN_BAND_HI)   # 대역 상한 초과분(작을수록 좋음·tie-break)
    return {
        "disqualified": bool(reasons),
        "reasons": reasons,
        "dialogue_pres": round(pres, 4),
        "missing_numerics": miss,
        "length_ratio": round(ratio, 4),
        "top_ratio": top_ratio,
        "max_run": max_run,
        "da_ratio": da_ratio,
        "band_distance": band_dist,
        "max_run_over": max_run_over,
        "uninterrupted_run_max": uninterrupted_run_max,   # FIX-2: 국소 무중단 종결 키 run(하드 실격·리랭크 tie-break)
        "foreign_tokens": foreign,                        # RR-1 B: 원문에 없는 명부 인물 발명 토큰(실격 사유·투명화)
    }


# ── 정독 게이트(채택 조건 — 1차 척도 이행, 파일럿 2차 보정 2026-07-14) ─────────────────────────
#   근거: ch1 실측 — BoN 승자가 kiwi 대역(0.417)에 들어갔는데 쌍대 정독에서 원문에 0-2 완패("과잉
#   내면독백·계산 반복"). '대역 진입 ≠ 품질'(P2-B Goodhart 실증)의 제품 경로 재현 → 채택 조건에 1차
#   척도(정독)를 직접 배선해 패스를 no-harm 편집으로 만든다: 원문 완승(양순서)일 때만 기각.
def build_read_gate_prompt(x: str, y: str) -> tuple[str, str]:
    """쌍대 정독 심사 프롬프트(system, user) — 초비판 독자(hair-trigger)·JSON 강제.
    순서 편향은 호출부가 양순서 2콜로 상쇄한다(단일 순서 판정 금지).

    SP-3(감사 M2): 구 전제("같은 사건·같은 대사, 문체만 다름")는 뼈대가 계획 층일 수 있게 되면
    참이 아닐 수 있는 프라이밍(사건 차이를 보지 말라는 선입력) — 사실대로 좁힘. 인용 필드 신설
    (PAIR_SYS 의 A_인용/B_인용 동형) — 인용 없는 판정은 무해 판정기가 된다(VJ-1 실측).
    인용 정합 대조는 호출부가 cite_ok 로 하고, 불일치 라운드는 판정 불가 처리."""
    system = "너는 웹소설 유료 연재 플랫폼의 초비판적 독자다. 관대함 없음. 돈과 시간을 아까워한다."
    user = (
        "같은 회차를 다시 쓴 두 판본이다. 처음부터 끝까지 읽고 계속 읽고 싶은 쪽을 골라라. 판정 기준은 정독 체감이다.\n\n"
        "[판본 ㄱ]\n" + (x or "") + "\n\n[판본 ㄴ]\n" + (y or "") + "\n\n"
        "다음을 JSON 으로만 답하라:\n"
        '{"keep_reading": "ㄱ|ㄴ|무승부", "ㄱ_인용": "판단을 가른 ㄱ의 한 대목", '
        '"ㄴ_인용": "판단을 가른 ㄴ의 한 대목", "why": "두 문장 이내"}'
    )
    return system, user


def read_gate_verdict(rounds: list[dict]) -> dict:
    """양순서 심사 → 채택 판정(결정론). rounds[i]["pick_original"] ∈ {True(원문 승), False(재실현 승/무승부), None(판정 불가)}.

    규칙(사전 등록·no-harm): 기각 = ①전 라운드 판정 가능 + 전부 원문 승(완패) ②전 라운드 판정 불가(보수).
    그 외(1승1패·무승부 포함·부분 판정)는 채택 — 기각은 '원문이 명백히 낫다'가 입증될 때만."""
    picks = [r.get("pick_original") for r in (rounds or [])]
    known = [p for p in picks if p is not None]
    if not known:
        return {"adopt": False, "reason": "정독 심사 판정 불가(보수 기각)"}
    if len(known) == len(picks) and all(known):
        return {"adopt": False, "reason": "정독 열세(원문 양순서 완승)"}
    return {"adopt": True, "reason": ""}


def rerank_candidates(candidates: list[str], *, original: str,
                      dialogue_lines: list[str], numeric_tokens: list[str],
                      metrics_fn=None, foreign_fn=None) -> dict:
    """BoN 후보들을 평가·정렬해 승자를 고른다(결정론).

    metrics_fn: callable(text)->kiwi_metrics dict | None. 서비스가 style_pipeline.kiwi_style_metrics 를
      lazy 로 감싸 넘긴다(이 모듈은 tools 를 로드타임에 import 하지 않는다). None 이면 계측 없이 평가만.
    foreign_fn(RR-1 B): callable(candidate)->list[str] | None. 후보에 등장하되 원문에 없는 명부 인물 토큰.
      서비스가 foreign_roster_tokens(cand, ontology, source) 로 감싸 넘긴다(엔진↔온톨로지 타입 비의존).
      비지 않으면 그 후보는 하드 실격(원문에 없는 인물 발명 = 사실 불변 계약 위반). None 이면 검사 생략(하위호환).

    정렬 규칙(ST-14 FIX-2 재정렬): 유효(비실격) 후보 중
      1순위 = kiwi top_ratio 인간 대역 최근접(대역 안=0)
      2순위 = uninterrupted_run_max 최소(무중단 동일 종결 키 run — 국소 벽 최소화, FIX-2 신설). 결측(None)은
              큰 페널티(inf — 계측된 낮은 run 후보를 우선).
      3순위 = max_run 대역 상한(≤14) 초과분 최소
      4순위 = |da_ratio − 0.233|(인간 대역 평균 근접 — 온건화). 구 '최소'는 과거형이 거의 소멸한
              극단 후보(da 0.035·최빈 종결이 현재형으로 반전)를 뽑아 정독 패배(과잉 내면독백)를 낳았다.
      (동률 시 원 후보 순서로 안정 정렬 — 인덱스 tie-break)
    계측 불가(metrics_fn None·전 후보 top_ratio None)로 리랭크가 무의미하면 '첫 유효 후보' 강등.

    반환: {winner_index, winner, evaluations, all_disqualified, reason}.
      winner_index None = 전 후보 실격(채택 없음 — 원문 유지·사유). evaluations 는 후보별 평가 dict(사유 포함)."""
    evals: list[dict] = []
    _orig_urm = _uninterrupted_run_max(original)   # 상대 하드 실격 기준(후보마다 재계측 방지 — 1회)
    for i, cand in enumerate(candidates):
        km = None
        if metrics_fn is not None:
            try:
                km = metrics_fn(cand)
            except Exception:
                km = None
        foreign = None
        if foreign_fn is not None:
            try:
                foreign = foreign_fn(cand)
            except Exception:
                foreign = None   # 산출 실패=결측 정직(없는 검사로 실격시키지 않음)
        ev = evaluate_candidate(cand, original=original, dialogue_lines=dialogue_lines,
                                numeric_tokens=numeric_tokens, kiwi_metrics=km, original_urm=_orig_urm,
                                foreign_tokens=foreign)
        ev["index"] = i
        evals.append(ev)

    valid = [e for e in evals if not e["disqualified"]]
    if not valid:
        reasons = "; ".join(f"후보{e['index']}: {' / '.join(e['reasons'])}" for e in evals) or "후보 없음"
        return {"winner_index": None, "winner": None, "evaluations": evals,
                "all_disqualified": True, "reason": "전 후보 실격 — " + reasons}

    # 계측 가용성: 하나라도 top_ratio 가 있으면 대역 정렬, 전무하면 첫 유효 후보 강등(결측 정직).
    measurable = any(e.get("top_ratio") is not None for e in valid)
    if measurable:
        def _key(e):
            da = e["da_ratio"]
            urm = e.get("uninterrupted_run_max")
            return (e["band_distance"],
                    (urm if urm is not None else float("inf")),   # FIX-2: 무중단 종결 키 run 최소(결측=페널티)
                    (e["max_run_over"] if e["max_run_over"] is not None else float("inf")),
                    (abs(da - _DA_BAND_MEAN) if da is not None else float("inf")),   # 온건화(평균 근접)
                    e["index"])
        winner = min(valid, key=_key)
        reason = "대역 최근접 리랭크"
    else:
        winner = min(valid, key=lambda e: e["index"])   # 첫 유효 후보
        reason = "계측 불가 — 첫 유효 후보 강등(결측 정직)"
    return {"winner_index": winner["index"], "winner": candidates[winner["index"]],
            "evaluations": evals, "all_disqualified": False, "reason": reason}
