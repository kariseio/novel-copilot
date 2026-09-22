# -*- coding: utf-8 -*-
"""프롬프트 빌더 — Builder 패턴. 슬롯 직렬화의 '단일 지점'(비대칭 주입 위치 고정).

문체(StyleSpec)는 데이터로 주입(WEBNOVEL_STYLE 하드코딩 제거).
서사 흐름 강화: 직전 회차 원문 + 회차 내 직전 장면을 함께 노출하고 '이어가기'를 명령.
"""
from __future__ import annotations
import json
import pathlib
from ..domain.types import ContextBoard, SceneSpec
from ..domain.world import StyleSpec
from .textfmt import collapse_dashes, strip_structural_markup
from .fact_sheet import build_brief


# 바닥(floor) 제약 — 미학과 무관한 비협상 조판/시장 제약. 미학 오버레이가 못 덮고, 교정 패스도 이것만 유지(단일 출처).
FLOOR_CONSTRAINTS = "모바일 가독 줄바꿈·호칭 자연화·분량·시점/시제 일관"


# ─────────────────────────────────────────────────────────────────────────────
# SP-1 Stage A — 순방향 few-shot 문체 예시(분포 조향). PM 집필·Kiwi 인간 대역 검증 완료(EX1/EX2)한
#   목표 결(발화 층위 혼합·입말 개입·'-었다' 비율 낮음)의 짧은 지문 예시를 문체 블록 뒤에 주입한다.
#   예시 데이터 SSOT = app/tools/reports/sp1_exemplars.json (원문 바이트 그대로 사용·수정 금지).
#   이 파일 읽기는 '데이터 로드'이지 tools/ 코드 의존이 아니다(엔진 의존성0 불변 — kiwipiepy·tools 모듈 import 0).
#   파일 부재/파싱 실패 시 빈 문자열로 강등(fewshot OFF 와 동일 — 침묵 폴백 아님: 아래 loaded 플래그로 관측 가능).
#
#   무강제·B-32e 정합: 순방향 긍정 예시만(피할 예시 0 — pink-elephant). "결만 참고" 라벨로 내용·인물·설정
#   전이 차단(genre-blind — 예시는 장르 중립). config style_fewshot(기본 True)로 작품/전역 OFF(작가 opt-out).
#   OFF 시 이 블록을 스택하지 않아 프롬프트 바이트 동일(무회귀).
_SP1_EXEMPLARS_PATH = (pathlib.Path(__file__).resolve().parents[2]
                       / "tools" / "reports" / "sp1_exemplars.json")
_SP1_FEWSHOT_LABEL = "[문장 결 예시: 결만 참고(내용·인물·설정 무관)]"


def _load_sp1_exemplars() -> list[str]:
    """sp1_exemplars.json 을 원문 바이트 그대로 로드(EX1·EX2 순서 유지). 부재/손상 시 빈 리스트(강등)."""
    try:
        raw = _SP1_EXEMPLARS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return []
    return [str(data[k]) for k in ("EX1", "EX2") if k in data and str(data.get(k) or "").strip()]


SP1_EXEMPLARS: list[str] = _load_sp1_exemplars()


def style_fewshot_block(exemplars: list[str] | None = None) -> str:
    """Stage A 주입 블록 — 문체 블록 뒤에 붙는 순방향 few-shot 예시(라벨 + 예시 텍스트).

    exemplars 미지정이면 모듈 로드 SP1_EXEMPLARS 사용(작가 스킬 편집분을 주입하려면 목록을 넘긴다).
    예시가 없으면 빈 문자열(주입 없음 = OFF 바이트 동일). 예시는 구분선으로만 잇고 원문 바이트 불변."""
    ex = [e for e in (exemplars if exemplars is not None else SP1_EXEMPLARS) if e and e.strip()]
    if not ex:
        return ""
    return "\n\n" + _SP1_FEWSHOT_LABEL + "\n" + "\n◇\n".join(ex)


def floor_only() -> str:
    """교정/재작성 패스 전용 floor-only 블록 — 미학(기본 규칙·작가 오버레이) 0, 바닥 제약만.
    B-10: 최소 교정(설정 위반 수정)이 미학 오버레이 주입으로 '재문체화'로 번지는 것을 차단."""
    return (f"[조판 바닥 제약만 유지. 미학 변경 금지]\n{FLOOR_CONSTRAINTS} 만 지키고, "
            "문장 리듬·길이·비유 밀도·서술 거리·어휘 격 같은 미학은 원문 그대로 보존하라(재문체화·재서술 금지).")


# DP-7: 발단(작품 1화) 전용 — rule⑤(장면을 '지금 벌어지는 사건의 한복판'에서 여는 in-medias-res 개시)를
#   렌더 시점에만 grounding→전환 결로 교체한다. 저장된 style.rules 데이터는 불변(기존 작품 무변경·비1화 바이트 동일).
#   근거(design-dp-repair.md §DP-7·wf_3db091ee): system 고권위 rule⑤가 매 회차 발단 hook(비트 레이어·저권위)을
#   눌러 회귀물이 죽음/각성 한복판에서 개시(발단 grounding 소실)했음이 실데이터로 확정. 긍정 교체(pink-elephant:
#   금지 문구 없이 '무엇을 하라'만) — in-medias-res 지시 자체를 제거하고 '누구인지 세우고 곧 전환으로 굴려라'로 대체.
#   default rule⑤를 시그니처로 식별해 그 항목만 치환(커스텀 rules 로 시그니처 부재 시 무동작 — 안전 무회귀).
_OPENING_RULE_SIGNATURE = "사건의 한복판에서 열고"
_OPENING_RULE = (
    "이 회차는 작품의 첫머리(발단)다. 장면을 주인공이 '누구'인지 손에 잡히는 구체 장면(직업·하루의 결·처지·결핍·관계)으로 "
    "세우는 데서 열고, 그 일상 위로 전제의 전환이 발발하는 데까지 곧장 굴려라. 자원·수치·설정·상태는 그 장면 속 행동과 대사로 "
    "드러내고, 한 번은 예상을 비트는 변수(헛디딤·오판·대가·돌발)를 넣어 이야기를 굴려라. "
    "끝은 전환의 충격이나 첫 의문의 미해결을 구체적 이미지 한 줄로 남겨, 장면 '안'의 사건·대사·이미지로만 말하며 다음이 궁금해지게 끊어라."
)


# DP-8: 서술 시점(StyleSpec.pov) 파생 지시 — 렌더 시점에만 결을 얹는다(데이터는 pov 필드 하나로 SSOT).
#   기본값 third_limited 는 아무것도 덧붙이지 않아(빈 문자열) 기존 프롬프트와 **바이트 동일**(무회귀) —
#   밀착 3인칭 내면은 기본 8규칙(보여주기·대사 주도·화자 표지)이 이미 담고 있어 별도 문안이 불요하다.
#   first/third_omniscient 만 시점 결을 '긍정 교체'로 주입한다(금지 문구·틱 이름 호명 없음 — pink-elephant).
_POV_DIRECTIVE = {
    "first": (
        "\n\n[서술 시점: 1인칭 주인공] 이 작품은 주인공의 1인칭 시점으로 쓴다. 주인공의 생각·감정·판단은 "
        "따로 설명하는 대신 '나는 ~했다'처럼 서술 문장 안에 직접 실어 흘려라. 주인공의 목소리가 장면을 이끄는 "
        "대목에서는 태그 없는 대사를 몇 마디 이어 붙여 리듬을 살려도 좋다. "
        # DP-17→ST-9: 서술은 화자의 '목소리'다 — 사건을 계측·판정만 하는 카메라가 되지 않게(design-dp17-voice.md §2).
        #   긍정 전용(금지문 0·티 목록 0): 무엇을 '하라'만. ST-9 5축 정밀화 — 화자 정체성 지시의 정밀화이지 신규 채널이
        #   아니다(D-29 블랭킷 금지 준수). 벽을 깨는 건 어미 변주가 아니라 발화 '층위'를 오가는 것(research §④).
        "서술은 화자의 목소리다. 사건을 있는 그대로 보고하는 문장 사이사이에 화자의 즉석 판단·입말 생각·혼잣말 같은 "
        "의문을 현재형으로 섞어 넣어 목소리가 장면을 끌게 하라. 한 장면 안에서 서술의 층위를 오가라. "
        "사건 서술, 화자의 즉석 입말 판단, 의문, 그리고 (장면에 있다면) 문서·화면·소리의 인용까지 층위를 옮겨 가며 "
        "리듬을 만들라. 세계·상황 정보도 중립적으로 설명하기보다 "
        "화자가 그것을 어떻게 보는지, 태도·반응·평가에 실어 전달하라. 감정은 화자의 행동과 생각의 결로 드러내라."
    ),
    "third_omniscient": (
        "\n\n[서술 시점: 3인칭 전지] 서술자는 장면 밖에서 여러 인물의 속내를 두루 조망할 수 있다. 필요한 "
        "대목에서 인물들의 내면을 서술에 담되, 초점 인물을 한 장면 안에서 어지럽게 갈아타지는 마라."
    ),
}


def render_style(style: StyleSpec, opening: bool = False) -> str:
    # CX-7: 작품 rules 가 비면 코드 상수 폴백 — 통용 문체 규칙의 SSOT 는 코드(전 작품 즉시 반영),
    #   작품 값은 명시 커스텀일 때만 우선(기존 작품 무회귀).
    from ..domain.world import DEFAULT_STYLE_RULES
    src = list(style.rules) or list(DEFAULT_STYLE_RULES)
    if opening:
        # 발단 변형: in-medias-res 개시 rule 만 grounding→전환 결로 치환(데이터 불변, 렌더 시점).
        src = [(_OPENING_RULE if _OPENING_RULE_SIGNATURE in r else r) for r in src]
    rules = "\n".join(f"{i + 1}) {r}" for i, r in enumerate(src))
    # VX-1: structured_prompt 면 문체규칙을 <문체규칙> 태그로 실재화(확정 스토리 라우팅이 이 태그를 참조 — 감사관 M1)
    #   하고, 대사 출력 표기 계약(<대사 화자="…">)을 <대사표기>로 못박는다. 대사=생활의 말 기저는 위 규칙 3이
    #   담당하므로 여기 형태 처방 없음(앵커 지정만·긍정형). OFF 면 종전 대괄호 라벨 바이트 동일.
    xml = bool(getattr(style, "structured_prompt", False))
    if xml:
        # C3(감사관): 창작 용어 '태그 없는 대사'(화자 귀속 없는 연속 발화)가 XML '태그'와 충돌 — 규칙 3에도
        #   같은 문구가 있어 함께 치환한다(structured_prompt 에서는 모든 대사가 <대사 화자> 로 귀속되므로).
        rules = rules.replace("태그 없는 대사", "대사")
        block = (f"<문체규칙>\n{rules}\n</문체규칙>\n\n"
                 '<대사표기>\n'
                 '인물이 소리 내어 하는 말은 각각 <대사 화자="이름">…말…</대사> 로 감싸 쓴다. '
                 '화자 이름은 이 장면이 그 인물을 부르는 이름으로 적는다. '
                 '각 인물은 <인물보이스> 카드에 적힌 그 인물의 방식으로 말한다. '
                 '태그는 소리 내어 하는 말에만 두른다.\n'
                 '</대사표기>')
    else:
        block = f"[웹소설 문체 규칙: 반드시 준수]\n{rules}"
    # DP-8: 시점 결 주입(기본 third_limited → "" → 바이트 동일). 시점은 인칭 분기(규칙3·8)의 '결'만 얹고 강제하지 않는다(T2).
    pov = getattr(style, "pov", "third_limited")
    pov_directive = _POV_DIRECTIVE.get(pov, "")
    if xml and pov == "first":
        # C3(감사관): 1인칭 지시의 창작 용어 '태그 없는 대사'(= 화자 귀속 '그가 말했다' 없는 연속 발화)가 XML '태그'와
        #   같은 단어로 충돌. structured_prompt 에서는 모든 대사가 <대사 화자> 로 화자를 달므로 '태그 없는'을 뺀다.
        pov_directive = pov_directive.replace("태그 없는 대사", "대사")
    block += pov_directive
    # DP-17: 화자 보이스 주입 — first 시점의 화자 정체성('누가 말하는가')을 1인칭 지시 안에 얹는다.
    #   voice 는 worldgen 이 주인공 시드에서 증거 게이트로 도출한 자유 텍스트(또는 작가 편집). 빈 값이거나
    #   비-first 시점이면 아무것도 덧붙이지 않아 기존 프롬프트와 바이트 동일(무회귀). 긍정 전용 — 화자의 태도를
    #   '이런 목소리로 말한다'로 제시할 뿐 금지문·틱 이름 호명 없음(D-29 블랭킷 아님 — 작품별 화자 정체성).
    voice = (getattr(style, "narrator_voice", "") or "").strip()
    if pov == "first" and voice:
        block += (
            "\n\n[이 작품 화자의 목소리: 위 1인칭 서술은 이런 태도의 화자가 말한다]\n"
            f"{voice}\n"
            # VL-1(2026-08-15 감사): '논평하게 하라'가 카드 관찰 명제의 잠언 발급 면허였다(5화 실측) — 겪기만.
            "이 화자의 태도와 말투로 사건을 겪게 하라. 같은 사건도 이 화자의 눈과 입을 통과해 나오게."
        )
    # Layer 2 작가 문체 오버레이 — 설정 시 위 기본 규칙의 '미학 축'을 작가 지정으로 덮어쓴다(precedence 명시).
    # 빈 값이면 추가 0 = 기존 동작과 동일(무회귀). '문체는 작가마다 다르다' → 기본 규칙은 디폴트일 뿐, 작가가 갈아끼운다.
    overlay = (style.author_style or "").strip()
    if overlay:
        # 오버레이는 '미학 축'만 덮어쓴다. 바닥 제약은 재나열하지 않고 위 규칙을 참조만(중복 부정 명령 = 두더지잡기).
        # 분량·시점/시제는 프롬프트가 아니라 결정론 게이트(norm·_fix_tense·tense_leak_ratio)가 실제 방어선이다.
        block += (
            "\n\n[작가 지정 문체: 위 기본 규칙의 미학 축보다 우선]\n"
            f"{overlay}\n"
            "※ 문장 리듬·길이 변주·감정 처리 방식·직유/비유 밀도·서술 거리·어휘 격 같은 미학 축이 "
            f"위 기본 규칙과 충돌하면 이 작가 문체를 따른다. 단 위 규칙의 바닥 제약({FLOOR_CONSTRAINTS})은 "
            "작가 문체와 무관하게 유지하라."
        )
    return block


class PromptAssembler:
    def __init__(self, style: StyleSpec, prev_chapter_chars: int = 4000):
        self.style = style
        self.prev_chapter_chars = prev_chapter_chars

    def assemble(self, board: ContextBoard, scene: SceneSpec, prev_scenes: str) -> str:
        # VX-1: XML 섹션 태그 + 대사 태그 경로 스위치(작품 데이터 · 기본 OFF=종전 대괄호 바이트 동일). 아래 return 이
        #   분기하고, 각 데이터 블록의 XML 변형(_x)은 additive 로만 계산해 OFF 경로 바이트를 구조로 보존한다.
        xml = bool(getattr(self.style, "structured_prompt", False))
        gt = "\n".join(f"- {f.entity}: {f.attr_label}={f.value}" for f in board.ground_truth) or "(없음)"
        # 세계 규칙은 '낮은 신뢰 참조 맥락'이 아니라 '확정 설정'과 동급 고신뢰 — 헤더가 약속한 위치(세계규칙)에 직렬화(M-1).
        wr = ("\n[세계 규칙: 이 작품의 불변 규칙. 어기지 마라]\n"
              + "\n".join(f"- {r}" for r in board.world_rules)) if board.world_rules else ""
        wr_x = ("\n<세계규칙>\n" + "\n".join(f"- {r}" for r in board.world_rules)
                + "\n</세계규칙>") if board.world_rules else ""
        # CN-3: 집필 직전 초점화 브리프(시점+이번 사건)를 최상단에 재표면화(key_events 는 원래 20k 덤프 뒤에야 등장 —
        #       lost-in-the-middle 대응). 캐논은 바로 아래 [확정 설정]에 이미 있어 복제 않고 가리킨다. story_time(CN-1)은 여기로 통합.
        brief = build_brief(board.story_time, scene.key_events,
                            story_mode=bool(board.confirmed_story or board.story_remaining))   # SY-1: 레거시 바이트 보존(중대 3-ⓑ)
        brief_block = (brief + "\n\n") if brief else ""
        auth = "\n".join(f"- {d.text}" for d in board.authority) or "(없음)"
        narr = "\n".join(f"- [{r.source}:{r.ref}] {r.text}" for r in board.narrative) or "(이전 맥락 없음)"
        # AN-1a: 이 블록에는 과거 회차 프로즈 원문 발췌(rag_chunk)가 그대로 실린다(harness rag.search → RetrievedItem.text).
        #   프로즈 원문이 컨텍스트에 노출되면 few-shot 앵커로 작동해 모델이 과거 문장·대사를 그대로 재사용한다
        #   (self-history-anchoring). 참조-전용임을 헤더에서 긍정형으로 못박아 앵커를 완화한다(발췌가 있을 때만 — 토큰 0 하위호환).
        ref_use = ("이 발췌는 사실·흐름·인물의 결의를 확인하는 참조 자료다. 여기서 확인한 내용을 "
                   "이번 회차의 새 문장과 새 대사로 다시 써라.\n") if board.narrative else ""
        prev_ch = strip_structural_markup(board.prev_chapter[-self.prev_chapter_chars:]) if board.prev_chapter else ""   # 재주입 시 구조 마크업(줄표 런·&nbsp; 스페이서·표지)까지 정규화 → 모델이 자기 litter 를 교재로 안 봄(B-35 소스 루프 차단; 표현 마크업·diegetic 불변)
        sofar_block = (f"[지금까지 줄거리(누적 요약. 전체 흐름·미결 사건을 기억하고 이어라)]\n{board.story_so_far}\n\n"
                       if board.story_so_far else "")   # C-4: "잊지 말 것" → 긍정 전환
        sofar_block_x = (f'<줄거리요약 용도="전체흐름·미결사건 기억">\n{board.story_so_far}\n</줄거리요약>\n\n'
                         if board.story_so_far else "")
        flow_block = (f"[직전 회차 원문(이어쓰기 기준. 어조·문체·상황을 매끄럽게 이어라)]\n{prev_ch}\n\n"
                      if prev_ch else "")
        flow_block_x = (f'<직전회차 용도="이어쓰기기준. 어조·문체·상황을 매끄럽게 이어라">\n{prev_ch}\n</직전회차>\n\n'
                        if prev_ch else "")
        within = (f"[이번 회차 직전 장면들(바로 이어서)]\n{prev_scenes[-2500:]}\n\n"
                  if prev_scenes else "")
        within_x = (f'<직전장면들 용도="바로 이어서">\n{prev_scenes[-2500:]}\n</직전장면들>\n\n'
                    if prev_scenes else "")
        # C-4: "도배 금지" → 빈도 quota 긍정지시(서너 개 중 한 번 '정도로만 아껴 써라'). 자기 패러디화 차단 의도 보존.
        # VL-1: 참조 전용 라벨(AN-1a 문형) — 카드 관찰 서술이 지문·대사로 직역되는 채널 차단(5화 3건 실측).
        #   구 헤더의 시그니처(어미·감탄사) 문장은 형태 메뉴 호명이라 삭제(CX-6ⓒ 렌더 잔재의 동족).
        # VH-1(2026-08-16 감사+A/B 프로브 6/6): 조건부 레지스터 카드 읽기 계약 — 카드에 '순간'이 적혀 있으면
        #   그 순간이 온 대목에서만 발화. 기저(대사=생활의 말)는 최상위 문체 규칙 3이 담당하므로 여기 중복 금지
        #   (지시로 문체 조향 반려 전례). 조건 없는 레거시 한 줄 카드에서는 이 문장이 발화되지 않는다(하위호환).
        voice_block = (f"[인물 보이스(참조 전용): 인물이 말하고 움직이는 방식을 적어 둔 카드다.\n"
                       f"카드에 어떤 순간이 함께 적혀 있으면, 그 순간이 이번 장면에 실제로 왔을 때 그 대목에서 드러내라.\n"
                       f"카드에서 확인한 방식은 이번 회차의 새 문장과 새 대사로 드러내라]\n{board.voice_cards}\n\n"
                       if board.voice_cards else "")
        voice_block_x = (f'<인물보이스 용도="참조전용">\n'
                         f"인물이 말하고 움직이는 방식을 적어 둔 카드다. 카드에 어떤 순간이 함께 적혀 있으면, "
                         f"그 순간이 이번 장면에 실제로 왔을 때 그 대목에서 드러내라. "
                         f"카드에서 확인한 방식은 이번 회차의 새 문장과 새 대사로 드러내라.\n{board.voice_cards}\n"
                         f"</인물보이스>\n\n"
                         if board.voice_cards else "")
        # C-4: "다시 소개하지 말 것"·"재서술·되감기·재시작하지 마라" → 긍정 전환. 전방 진행 의도 보존,
        #      기피 행위 호명(재서술·되감기) 프라이밍 제거.
        # C-4 적대검증 MED-1: '직전 회차가 끝난 지점 이후의 새 장면' 명령은 직전 회차 원문으로 새 회차를 여는
        #      첫 콜에만 옳다. 이어쓰기(_continue: prev_chapter="" + 회차 내 직전 본문 존재)에서는 '마지막 문장
        #      바로 다음' 접합 계약과 정면 모순(되감기·강제 장면전환 유도)하고, 1화에서는 존재하지 않는
        #      '직전 회차'를 가리키므로(발단 grounding 과도 긴장) 주입 맥락별로 분기한다.
        # HK-1(2026-08-13, 신작 2→3화 훅 미회수 실측): 비트는 사전 계획이라 직전 화의 실제 절단점(말미에 걸린
        #   미해결 순간)을 모른다. 절단점을 아는 유일한 시점인 생성시에 '말미 소화 → 계획 진행' 순서를 계약으로
        #   명시한다. "새 장면으로 곧장" 문안은 훅 건너뛰기를 밀던 소스라 제거(긍정형 유지·되감기 금지 의도는
        #   '이후에서 시작'이 보존).
        if prev_ch and not prev_scenes:      # 새 회차 첫 콜 — 화 경계 인계(직전 회차 원문 기준)
            continuity = ("[연속성 지시] 직전 상황에서 자연스럽게 이어서 전개하라. 시간은 앞으로만 흐른다. "
                          "첫 문장부터 직전 회차가 끝난 지점 '이후'에서 시작하라. 위 직전 회차 원문 말미에 "
                          "진행 중이던 미해결 순간(끝나지 않은 행동·대사·이상 징후)이 있으면 그 다음 순간부터 "
                          "소화한 뒤 이번 화 계획으로 나아가라.")
        elif prev_scenes:                    # 회차 내 이어쓰기(_continue) — 접합부는 장면 전환 없이 그대로 잇는다
            continuity = "[연속성 지시] 위 직전 본문의 흐름에서 끊김 없이 이어서 전개하라. 시간은 앞으로만 흐른다."
        else:                                # 1화 첫 콜 — 참조할 '직전'이 없다(dangling 참조 제거)
            continuity = "[연속성 지시] 시간은 앞으로만 흐른다. 장면과 사건은 항상 앞으로 나아가게 전개하라."
        # SY-1 story 모드(설계 §5·2차 감사 F-B): 사건 소스 단일화 — 치환 대상은 '핵심사건:'
        #   한 줄뿐(목표 줄의 계약은 보존), 확정 스토리 블록은 말미 배치(B4: 말미 재명시 실측).
        #   두 필드 다 ""(기본)이면 kev_line·story_block 이 기존과 완전 동일(바이트 계약 — 테스트 고정).
        story_mode = bool(board.confirmed_story or board.story_remaining)
        kev_line = "" if story_mode else f"핵심사건: {', '.join(scene.key_events)}\n"
        # XR-6⒜(cross-review/005 §2.4 · prompt-auditor 조건 반영): 세계 설정 구현 명령은 작가 확정(✓) 항목이
        #   이번 화 digest 에 실제 실렸을 때만, '최소 1회' 무조건 쿼터 대신 장면 목표 조건으로 발화한다.
        #   ✓ 없는 화(미검수 전용·예산 컷 포함)는 대화 전진 문장만 — 불가능 요구 금지(K4). 명사 메뉴
        #   (경제·무기…/거래·전술…) 삭제(사용 대상 메뉴 호명=증폭 — 헌법 1조). 조건은 예산 컷 '이후'
        #   실주입 텍스트 기준(결정론 — "]✓ " 마커는 bible_compiler 가 붙인다).
        has_marked = any(i.source == "bible" and "]✓ " in (i.text or "") for i in board.narrative)
        impl_line = ("아래 참조 맥락에서 ✓ 표시된 항목 중 이번 장면 목표에 걸리는 것이 있으면, "
                     "그 설정이 사건 안에서 실제로 쓰이는 장면 하나로 보여라. ") if has_marked else ""
        impl_line_x = ("아래 <참조맥락>에서 ✓ 표시된 항목 중 이번 장면 목표에 걸리는 것이 있으면, "
                       "그 설정이 사건 안에서 실제로 쓰이는 장면 하나로 보여라. ") if has_marked else ""
        if story_mode:
            from . import story_pass_prompts as _spp
            story_block = (_spp.build_story_block_draft(board.confirmed_story, xml=xml)
                           if board.confirmed_story else board.story_remaining)
        else:
            story_block = ""
        return (
            f"{brief_block}"
            f"[확정 설정: 절대 위반 금지(눈색·소속·생사·등급·관계·세계규칙)]\n{gt}{wr}\n\n"
            f"[작가 지시: 우선]\n{auth}\n\n"
            f"{voice_block}"
            f"{sofar_block}"
            f"{flow_block}"
            f"[이번 장면 목표]\n{scene.goal}\n{kev_line}"
            # C-4: "되묻는 대화 반복 금지" → 긍정 전환(대화의 전진 기능 지시). 정보 재탕 차단 의도 보존.
            f"({impl_line}대화는 매번 새 정보나 새 결정을 실어 이야기를 전진시켜라.)\n\n"
            f"{within}"
            f"[참조 맥락: 서사 배경(낮은 신뢰, 설정은 위 '확정 설정'이 우선)]\n{ref_use}{narr}\n\n"
            f"{continuity}"
            f"{story_block}"
        ) if not xml else (
            # VX-1 XML 경로 — 위계(절대·최우선·참조전용·낮음)를 라벨 톤이 아니라 태그·속성으로 못박는다.
            #   확정설정의 위반금지 열거는 캐논 락 등록 부정형(예외 ⓐ)이라 속성으로 바이트 보존. continuity·인라인
            #   구현 지시는 데이터 블록이 아니라 지시라 태그로 감싸지 않는다(태그=콘텐츠 구획, 지시는 산문 유지).
            f"{brief_block}"
            f'<확정설정 우선순위="절대" 위반금지="눈색·소속·생사·등급·관계·세계규칙">\n{gt}{wr_x}\n</확정설정>\n\n'
            f'<작가지시 우선순위="최우선">\n{auth}\n</작가지시>\n\n'
            f"{voice_block_x}"
            f"{sofar_block_x}"
            f"{flow_block_x}"
            f"<장면목표>\n{scene.goal}\n</장면목표>\n{kev_line}"
            f"({impl_line_x}대화는 매번 새 정보나 새 결정을 실어 이야기를 전진시켜라.)\n\n"
            f"{within_x}"
            f'<참조맥락 신뢰도="낮음" 설정우선="확정설정">\n{ref_use}{narr}\n</참조맥락>\n\n'
            f"{continuity}"
            f"{story_block}"
        )
