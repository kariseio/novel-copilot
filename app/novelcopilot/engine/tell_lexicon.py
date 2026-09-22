# -*- coding: utf-8 -*-
"""TL-1 사전식 AI티 검출기(따온 검증법·한국어 이식) — 기존 ai_tell 과 병렬 A/B 축 (LLM 0콜).

출처: lingfengQAQ/webnovel-writer(★5.9k)의 Anti-AI 7층 규범 중 '제1층 고위험 어휘 사전'(중국어
14범주 200+ 어휘)을 한국어 웹소설 AI티로 번안 이식. 기존 축(quality_gates.ai_tell_profile —
KatFishNet 무사전 분포 신호)은 **그대로 두고**, 사전(辭典) 기반 표면형 계수라는 이질 검증법을
별도 축으로 추가해 두 방법의 판별력을 A/B 대조한다(러너 tools/ab_tell_lexicon.py).

무강제 계약(기존 축과 동일):
  · 판정기 아님 — 절대 임계·이진 판정·라벨·자동 교정 트리거 0. 값(분수+n)만 낸다.
  · 절대값 판정 금물 — 인간 대역·작품 코퍼스 대비 상대 추세로만 해석.

핑크 엘리펀트 금지(이식 시 뒤집은 설계): 원 레포는 이 사전을 **윤문 프롬프트에 통째로 탑재**하지만,
우리 실측(자기 이력 앵커링 역설·트로프 호명 오염)상 회피 대상의 컨텍스트 노출은 앵커로 역작동한다.
따라서 이 사전은 **계측 전용 데이터** — 생성·윤문·재실현 어떤 프롬프트에도 needle 을 노출하지
않는다(노출이 필요하면 '적중 스팬'만, 사전 전체 금지).

두더지잡기 금지(사전 동결): 원 레포의 "首批(첫 배치)" 식 무한 증식이 정확히 검출기-추가 트레드밀이다.
본 사전은 LEXICON_VERSION 으로 동결 — 항목 추가/삭제는 버전 갱신 + A/B 재기준선 산출로만 한다.

매칭 계약(교착어 substring 결함 패턴 대응):
  · needle 은 활용 변이를 흡수하도록 굴절 직전에서 자른 표면형 접두("미간을 찌푸" ← 찌푸렸다/리며/린).
    전 needle 길이 ≥2(어간≥2 가드) — 로더가 검증한다.
  · 겹침 등장 계수 맹점(DE-2 형제 패턴): 긴 needle 부터 스팬 마스킹 — "바로 그 순간"이 잡은 구간을
    "그 순간"이 이중 계수하지 않는다.
  · 측정 범위는 **대사 포함 전문**(원 검증법 충실 — 기존 축 다수가 지문 한정인 것과 다름을 명시).
"""
from __future__ import annotations
import re

LEXICON_VERSION = "tl1-v1"   # 동결 버전 — 증식은 버전 갱신+재기준선으로만(두더지잡기 금지)

# ── 14범주 사전(원 레포 A~N 1:1 대응·한국어 번안) ─────────────────────────────
# 번안 원칙: ⓐ 한국어 LLM 산문에서 실제 과잉 관측되는 표면형 위주 ⓑ 장르 기능어(각성·헌터 등) 배제
# ⓒ substring 안전(다른 낱말 내부에 우연히 포함되기 어려운 형태) ⓓ 굴절어는 접두로 절단.
LEXICON: dict[str, tuple[str, ...]] = {
    # A 총괄·요약어(结论/总结) — 요약 메타 발화
    "총괄요약": ("요컨대", "정리하자면", "말하자면", "한마디로", "다시 말해",
               "결론적으로", "종합하면", "결국에는", "그렇게 보면", "어찌 보면"),
    # B 열거 템플릿(首先/其次/最后)
    "열거템플릿": ("첫째", "둘째", "셋째", "다음으로", "마지막으로", "또한",
                "게다가", "나아가", "더욱이", "덧붙이자면"),
    # C 서면·학술강(书面学术腔)
    "서면학술": ("본질적으로", "근본적으로", "일종의", "모종의", "존재했다", "의미했다",
               "형성했다", "증명하듯", "여실히", "고스란히", "명실상부"),
    # D 논리 연결 과다(逻辑连接滥用) — 초고빈도 접속사(그러나/하지만)는 인간 기저가 높아 제외,
    #   기계 산문에서 밀도 편중이 뚜렷한 중장형만
    "논리연결": ("왜냐하면", "그러므로", "그럼에도", "불구하고", "그와 동시에",
               "이와 동시에", "뿐만 아니라", "그로 인해", "그리하여", "그에 따라"),
    # E 감정 직서(情绪直述) — 라벨 붙이기("분노가 치밀었다")
    "감정직서": ("분노가 치밀", "만감이 교차", "복잡한 감정", "알 수 없는 감정",
               "형언할 수 없", "형용할 수 없", "가슴이 철렁", "심장이 철렁",
               "가슴이 먹먹", "코끝이 찡", "울컥했다", "북받쳤다", "묘한 감정"),
    # F 동작 상투구(动作套话)
    "동작상투": ("미간을 찌푸", "한숨을 내쉬", "한숨을 쉬었", "심호흡을", "고개를 끄덕",
               "고개를 저었", "어깨를 으쓱", "입꼬리를 올", "주먹을 불끈", "몸을 부르르",
               "마른침을", "침을 꿀꺽", "헛기침을", "입술을 깨물", "혀를 찼"),
    # G 환경 상투구(环境套话)
    "환경상투": ("공기가 얼어붙", "정적이 흘렀", "정적이 감돌", "침묵이 흘렀", "침묵이 감돌",
               "무거운 침묵", "팽팽한 긴장", "긴장감이 감돌", "시간이 멈춘 듯", "공기가 무겁",
               "적막이 감돌", "숨 막히는 정적", "서늘한 기운"),
    # H 서사 필러(叙事填充)
    "서사필러": ("사실상", "어떻게 보면", "그런 의미에서", "이런 상황에서", "에게 있어",
               "부인할 수 없", "의심할 여지", "두말할 것도", "당연하게도",
               "아이러니하게도", "공교롭게도"),
    # I 추상 공허어(抽象空泛) — 장르 기능어 충돌 회피, '마음 한켠' 계열 국소화
    "추상공허": ("마음 한구석", "마음 한켠", "마음 한편", "가슴 한켠", "가슴 한편",
               "존재의 이유", "삶의 무게", "진정한 의미", "알 수 없는 무언가", "미지의 무언가"),
    # J 기계적 개장·수미(机械开场/收尾) — 극적 아이러니 클로저·예고
    "개장수미": ("한편 그 시각", "같은 시각", "알지 못했다", "알 리 없었다", "알 턱이 없었",
               "운명의 톱니바퀴", "시작에 불과했", "이것은 시작", "폭풍전야", "서막에 불과",
               "그렇게 시작되", "예감이 들었다"),
    # K 표정 템플릿(神态模板) — 전 인물 동일 표정 세트
    "표정템플릿": ("눈빛이 흔들", "눈동자가 흔들", "동공이 흔들", "눈이 커졌", "눈을 가늘게",
                "눈썹을 치켜", "표정이 굳었", "낯빛이 어두워", "얼굴이 창백", "얼굴이 굳었",
                "입꼬리가 씰룩", "눈을 빛냈", "눈빛이 날카로워"),
    # L 만능 부사(万能副词) — 부사+동사 고정쌍의 부사부
    "만능부사": ("천천히", "조용히", "슬며시", "살며시", "지그시", "가만히", "문득",
               "서서히", "넌지시", "묵묵히", "우두커니", "물끄러미"),
    # M 내심 활동 상투구(内心活动套话)
    "내심상투": ("속으로 생각", "속으로 되뇌", "마음속으로", "내심", "왠지 모르게",
               "왜인지 모르게", "이유 모를", "알 수 없는 기분", "직감했다", "직감적으로",
               "본능적으로"),
    # N 전환·반전 템플릿(转折/递进模板) — 독자가 반전 예고를 학습해버리는 고정구
    "전환템플릿": ("바로 그때", "바로 그 순간", "그 순간", "다음 순간", "그러나 그때",
                "아무도 몰랐", "뜻밖에도", "놀랍게도", "순간이었다", "찰나였다",
                "그때였다", "때마침"),
}

# 어간≥2 가드 — 로드타임 강제(짧은 needle 의 substring 오탐 원천 차단)
assert all(len(n) >= 2 for ns in LEXICON.values() for n in ns), "needle 길이 ≥2 위반"
assert len(LEXICON) == 14, "14범주 계약(원 레포 A~N 1:1) 위반"

# 스팬 마스킹 순서: 긴 needle 우선(겹침 이중 계수 차단) — (범주, needle) 평탄화·동결
_ORDERED: list[tuple[str, str]] = sorted(
    ((cat, n) for cat, ns in LEXICON.items() for n in ns),
    key=lambda cn: -len(cn[1]))
_MASK = "\x00"


def tell_lexicon_profile(text: str) -> dict:
    """14범주 사전 적중 계수 — 결정론·LLM 0콜·의존 0. 대사 포함 전문 스캔(원 검증법 충실).

    반환(전부 원자료·판정 없음):
      total_hits     : 전 범주 적중 총수
      hits_per_1k    : 1000자당 적중(길이-불변 밀도 — 회차 간 비교축)
      categories_hit : 14범주 중 1건 이상 적중한 범주 수
      by_category    : {범주: {"hits": n, "per_1k": x, "top": [[needle, n]…≤3]}} — 적중 범주만
                       (미적중 범주 생략 = 적중 0 정직 표기, categories_hit 로 전체 파악)
      top_hits       : 전 범주 통합 상위 적중 [[needle, n]…≤12]
      n_chars        : 공백 제외 글자수(밀도 분모)
      lexicon_version: 사전 동결 버전(값 비교는 동일 버전끼리만)
    """
    body = text or ""
    chars = len(re.findall(r"\S", body))
    if not chars:
        return {"total_hits": 0, "hits_per_1k": 0.0, "categories_hit": 0,
                "by_category": {}, "top_hits": [], "n_chars": 0,
                "lexicon_version": LEXICON_VERSION}
    masked = body
    counts: dict[str, list[tuple[str, int]]] = {}
    for cat, needle in _ORDERED:                      # 긴 needle 부터 — 스팬 마스킹
        n = masked.count(needle)
        if n:
            counts.setdefault(cat, []).append((needle, n))
            masked = masked.replace(needle, _MASK * len(needle))
    by_cat = {}
    for cat in LEXICON:                               # 사전 선언 순서 유지(출력 안정)
        hits = counts.get(cat)
        if not hits:
            continue
        tot = sum(c for _, c in hits)
        top = sorted(hits, key=lambda x: -x[1])[:3]
        by_cat[cat] = {"hits": tot, "per_1k": round(tot / chars * 1000, 2),
                       "top": [[n, c] for n, c in top]}
    total = sum(v["hits"] for v in by_cat.values())
    all_hits = sorted((p for hs in counts.values() for p in hs), key=lambda x: -x[1])
    return {
        "total_hits": total,
        "hits_per_1k": round(total / chars * 1000, 2),
        "categories_hit": len(by_cat),
        "by_category": by_cat,
        "top_hits": [[n, c] for n, c in all_hits[:12]],
        "n_chars": chars,
        "lexicon_version": LEXICON_VERSION,
    }


def summarize_for_verification(text: str) -> dict:
    """verification 축 요약 — build_verification 이 소비하는 압축판(SSOT 가독 유지).
    범주별 상세 top 은 러너/도구가 tell_lexicon_profile 직접 호출로 본다. 결정론·의존 0이라
    결측(MISSING) 경로 없음 — 빈 본문이면 0 집계(측정했으나 적중 0/본문 0 을 정직 표기)."""
    p = tell_lexicon_profile(text)
    return {"total_hits": p["total_hits"], "hits_per_1k": p["hits_per_1k"],
            "categories_hit": p["categories_hit"],
            "by_category": {c: v["hits"] for c, v in p["by_category"].items()},
            "top_hits": p["top_hits"][:5],
            "lexicon_version": p["lexicon_version"]}
