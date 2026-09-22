# -*- coding: utf-8 -*-
"""표지 이미지 프롬프트 합성(CV-1/CV-3) — 설정·주인공 → 영문 이미지 프롬프트 1개(wg_provider 재사용).

입력: world(title·genre·tone·synopsis·genre_contract) + 주인공 엔티티(protagonist 우선, profile 원문).
출력: 한국 웹소설 표지 관습을 담은 영문 프롬프트 1개. LLM 은 인물·장면 묘사만 담당하고,
      화풍(애니 일러스트풍·클로즈업·단순 배경·고채도)과 장르 액센트는 코드가 결정론으로 덧붙인다
      — CV-3: 화풍을 LLM 재량에 두면 이미지 모델 prior(서양 실사/유화)로 흘러가는 실측 때문(no-text 와 같은 보증 패턴).
CV-4: 기본값으로 한글 제목 타이포 블록(하단 1/3·외곽선+그라데이션)을 부착하고 잔여 텍스트는
      전면 금지한다(제목 무오탈 실측 채택 — gpt-image-2 한글 렌더). include_title=False 면
      종전 no-text 무텍스트 일러스트.
증거 게이트 계보: 프로필에 외형 정보가 없으면 발명을 최소화하고 장르 관습 수준의 중립 묘사만 한다.
합성 프롬프트는 그대로 응답·메타에 노출(작가 열람)."""
from __future__ import annotations
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)

_NO_TEXT = "no text, no letters, no words, no title, no watermark, no signature, no logo"

# CV-3 화풍 계약 — 한국 웹소설 표지 관습. LLM 출력에 관계없이 코드가 항상 부착한다(화풍 = 협상 불가 계약).
# v2(2026-07-23 사용자 정독): 반실사 웹툰체 → 애니 일러스트풍.
# v3(2026-07-31, 노벨피아 실측 14장 + NAI 커뮤니티 관행 조사 반영): 노벨피아 주류 결로 조정 —
#   라노벨/웹소설 표지 블렌딩(단일 작가 지목 금지 — 커뮤니티 복수 혼합 관행·ST-12 다형 교훈),
#   큰 애니 눈·밝은 발광 팔레트·고채도. 시안 2장 정독 통과 후 채택.
_STYLE_ANCHOR = (
    "Premium Japanese anime illustration blending modern light-novel cover art and "
    "Korean web novel cover conventions: vivid cel shading with soft airbrushed gradients, "
    "crisp clean lineart, large expressive sparkling anime eyes, a beautiful idealized youthful "
    "character in natural adult proportions, bright luminous color palette, high saturation, "
    "polished professional finish, bust or waist-up close-up with the character filling most of "
    "the frame, simple softly-blurred atmospheric background"
)

# 장르 액센트 — 장르 문자열 결정론 매칭(첫 일치 승). 구체 장르를 앞에 둔다
# ('로맨스판타지'·'코믹 판타지'가 '판타지' 폴백에 먹히지 않게 — substring 우선순위).
# v3: 분위기·광원 어휘로 한정(의상·소품 지시 제거 — v3 시안에서 액센트 의상어가
#   인물 설정을 덮는 실측: 갈대마을 소녀가 ornate costume 로 화려해짐. 의상은 프로필 몫).
_GENRE_ACCENTS = (
    (("로판", "로맨스"), "romance-fantasy mood: soft pastel light with gold sparkle accents, dreamy warm glow"),
    (("무협", "무림"), "East Asian martial-arts mood: ink-wash mist, wind-swept atmosphere, "
                      "dynamic diagonal light"),
    (("호러", "괴담", "공포"), "stylish urban-horror mood: cold teal-and-violet light with one warm accent, "
                             "faint supernatural glow, eerie but attractive"),
    (("미스터리", "추리"), "mystery-thriller mood: moody cool light with one warm accent, "
                          "subtle enigmatic atmosphere"),
    (("코믹", "개그", "일상"), "bright comedic mood: warm sunny light, light cheerful energy"),
    (("헌터", "게이트", "시스템", "현대"), "modern-fantasy action mood: glowing magic energy effects, "
                                        "blue-violet light accents"),
    (("판타지", "이세계"), "high-fantasy mood: magical light effects, jewel-tone light accents"),
)

# CV-4 제목 렌더 — 한글 제목을 이미지 모델이 직접 타이포로 그린다(실측: 제목 무오탈 4/4·6/6,
# 장르 맞춤 레터링·하단 배치·외곽선+그라데이션까지 재현 — 커뮤니티 표준(NAI+미리캔버스 분리)이
# 못 하는 우리 백엔드 차별점). 잔여 배경 텍스트는 오탈자 소스라 전면 금지 가드(고블린→고볼린 실측).
_TITLE_BLOCK = (
    "The cover MUST display the Korean title text 「{title}」 in large stylish bold Korean "
    "typography with outline and gradient across the LOWER THIRD of the image, every hangul "
    "character rendered accurately and legibly"
)
_NO_OTHER_TEXT = ("Apart from this title, absolutely no other text, letters, signs or writing "
                  "of any kind. no watermark, no signature")


def _clean_title(title: str) -> str:
    """표지 렌더용 제목 정리 — 선두 대괄호 태그([실험 ...] 등 운영 라벨) 제거(결정론)."""
    import re
    return re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", title or "").strip()


def _genre_accent(genre: str, tone: str = "") -> str:
    """장르(+톤 폴백) → 액센트 문구(결정론). 무일치 시 빈 문자열(앵커만으로 충분 — 발명 금지)."""
    hay = f"{genre or ''} {tone or ''}"
    for keys, accent in _GENRE_ACCENTS:
        if any(k in hay for k in keys):
            return accent
    return ""


def _pick_protagonist(world):
    """주인공 엔티티 선정 — etype=='protagonist' 우선, 없으면 첫 번째 character 엔티티, 그것도 없으면 None.
    (엔티티 카탈로그는 character 가 기본이라 등장 순서 첫 인물을 주인공 근사로 — worldgen 이 주인공을 앞에 둔다.)"""
    ents = list(getattr(world, "entities", []) or [])
    for e in ents:
        if (getattr(e, "etype", "") or "").lower() == "protagonist":
            return e
    for e in ents:
        if (getattr(e, "etype", "") or "character").lower() == "character":
            return e
    return ents[0] if ents else None


def build_cover_context(world) -> dict:
    """LLM 입력 조립(결정론) — 합성 콜에 넘길 필드 dict. 테스트가 조립 자체를 고정한다(주인공 선정·계약 필드 포함)."""
    gc = getattr(world, "genre_contract", None)
    hero = _pick_protagonist(world)
    ctx = {
        "title": (getattr(world, "title", "") or "").strip(),
        "genre": (getattr(world, "genre", "") or "").strip(),
        "tone": (getattr(world, "tone", "") or "").strip(),
        "synopsis": (getattr(world, "synopsis", "") or "").strip(),   # 절단 전면 제거(2026-08-21): 전문
        "vocabulary_tone": ((getattr(gc, "vocabulary_tone", "") if gc else "") or "").strip(),
        "pleasure_engine": ((getattr(gc, "pleasure_engine", "") if gc else "") or "").strip(),
        "protagonist_name": (getattr(hero, "name", "") if hero else "").strip(),
        "protagonist_profile": ((getattr(hero, "profile", "") if hero else "") or "").strip(),   # 절단 전면 제거(2026-08-21): 전문(실측 최대 879자가 구 900 경계에 밀착)
    }
    return ctx


def _user_block(ctx: dict) -> str:
    lines = [f"[title] {ctx['title']}", f"[genre] {ctx['genre']}"]
    if ctx["tone"]:
        lines.append(f"[tone] {ctx['tone']}")
    if ctx["vocabulary_tone"]:
        lines.append(f"[vocabulary/tone] {ctx['vocabulary_tone']}")
    if ctx["pleasure_engine"]:
        lines.append(f"[reader appeal] {ctx['pleasure_engine']}")
    if ctx["synopsis"]:
        lines.append(f"[synopsis] {ctx['synopsis']}")
    if ctx["protagonist_name"]:
        lines.append(f"[protagonist] {ctx['protagonist_name']}")
    if ctx["protagonist_profile"]:
        lines.append(f"[protagonist profile] {ctx['protagonist_profile']}")
    return "\n".join(lines)


@promptlog.stage("cover_prompt")
def synthesize_cover_prompt(provider, world, include_title: bool = True) -> str:
    """설정·주인공 → 영문 이미지 프롬프트 1개(1콜). 실패 시 예외 전파(호출자가 무변경 유지·명시 에러).

    CV-3: LLM 은 인물·장면(외형·의상·포즈·표정·소품)만 묘사한다. 화풍 앵커·장르 액센트는
    코드가 항상 뒤에 부착 — LLM 이 화풍을 쓰거나 빼먹어도 최종 프롬프트의 화풍 계약은 불변.
    CV-4: include_title(기본 True)이면 한글 제목 타이포 블록+잔여 텍스트 금지 가드를 부착,
    False 면 종전 no-text 꼬리(무텍스트 일러스트)."""
    ctx = build_cover_context(world)
    system = (
        "You are an art director writing an English prompt for a Korean web-novel cover illustration. "
        "Compose ONE vivid English image prompt (2-4 sentences) describing ONLY the character and scene "
        "for a vertical 2:3 web-novel cover: appearance, costume, pose, facial expression, mood, "
        "and one or two symbolic props drawn from the synopsis. "
        "Composition: a bust or waist-up close-up — the character fills most of the frame; "
        "keep the background simple and atmospheric, never cluttered. "
        "Do NOT specify any art style or medium (no photorealism, no oil painting, no 3D render, "
        "no 'cinematic') — the art style is appended separately by the system. "
        "If the protagonist profile gives no physical appearance, do NOT invent specific features — "
        "describe only at a neutral, genre-typical level (silhouette, posture, atmosphere). "
        "The cover must contain NO text of any kind. "
        f"End the prompt with: {_NO_TEXT}. "
        'Return JSON only: {"prompt":"<the english image prompt>"}'
    )
    r = provider.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": _user_block(ctx)}],
        temperature=0.6)
    prompt = (r.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("표지 프롬프트 합성 결과가 비었습니다")
    # 조립(위치=강도 — 뒤가 강함): 장면(LLM) → 화풍 앵커 → 장르 액센트 → [제목 블록+가드 | no-text 꼬리].
    #   LLM 이 지시대로 no-text 꼬리를 달았으면 떼고 재부착(중복·순서 안정화 — best effort).
    body = prompt.strip().rstrip(". ")
    if body.lower().endswith(_NO_TEXT):
        body = body[: -len(_NO_TEXT)].rstrip(" .,;")
    parts = [body, _STYLE_ANCHOR]
    accent = _genre_accent(ctx["genre"], ctx["tone"])
    if accent:
        parts.append(accent)
    title = _clean_title(ctx["title"]) if include_title else ""
    if title:
        parts.append(_TITLE_BLOCK.format(title=title))
        return ". ".join(parts) + f". {_NO_OTHER_TEXT}"
    return ". ".join(parts) + f". {_NO_TEXT}"
