# -*- coding: utf-8 -*-
"""골든셋 — 게이트 유효성 측정용 라벨 데이터(결정론 템플릿 생성, LLM 0콜).

위반을 '우리가 심으므로' 정답 라벨이 무오류. 유형 5종 + 하드네거티브(회상/환영/언급/정합값).
각 문단은 필러 문장을 섞어 실제 소설 문단에 가깝게 구성(추출기 난이도 현실화).
"""
from __future__ import annotations
import itertools

from novelcopilot.domain.world import WorldConfig, AttributeSpec, EntitySpec, TimelineEntry, Beat
from novelcopilot.domain.types import RelationEdge


def golden_world() -> WorldConfig:
    """기준 세계 W — 캐논: 강혁 3화 사망, 한월 눈=붉은색, 도현 등급=5, 도현↔한월 동맹(gt), 재각성 불가."""
    return WorldConfig(
        title="골든셋", genre="현대 판타지",
        attributes=[
            AttributeSpec(key="eye_color", label="눈 색", kind="categorical",
                          vocab=["붉은색", "금색", "푸른색"], mutable=False),
            AttributeSpec(key="rank", label="등급", kind="numeric", monotonic="non_decreasing", mutable=True),
        ],
        entities=[
            EntitySpec(id="hero", name="도현", attrs={"eye_color": "푸른색", "rank": 5}),
            EntitySpec(id="rival", name="강혁", attrs={"rank": 6}),
            EntitySpec(id="mentor", name="한월", attrs={"eye_color": "붉은색", "rank": 7}),
        ],
        seed_edges=[RelationEdge(edge_id="ally:hero->mentor:1", rel_id="ally_of",
                                 src_id="hero", dst_id="mentor", eff_from=1)],
        timeline=[TimelineEntry(entity_id="rival", attr="status", value="dead", eff_from=3)],
        world_rules=[{"rule_id": "no_reawaken", "text": "각성은 일생에 한 번뿐, 재각성은 불가능하다.",
                      "flag": "reawakening", "keywords": ["재각성", "두 번째 각성"],
                      "extract_hint": "이미 각성한 인물의 '새로운 두 번째 각성 사건'"}],
        beats=[Beat(chapter=1, title="t", summary="s", entities=["hero"])],
    )


_FILLER = ["밤공기가 무겁게 가라앉아 있었다.", "도시의 불빛이 멀리서 흔들렸다.",
           "낡은 간판이 바람에 삐걱거렸다.", "골목 끝에서 비릿한 냄새가 풍겼다."]

# ---- 위반 문단 템플릿(유형별 표면형 변주) ----
_V1_DEAD = [  # 사망자(강혁, 3화 사망) 현재 행동 — 기대: state_timeline @ 강혁
    "강혁이 검을 뽑아 도현의 목을 겨눴다. \"여기까지다.\" 낮은 목소리가 골목을 울렸다.",
    "문이 열리고 강혁이 걸어 들어왔다. 그는 의자를 끌어다 앉으며 도현을 노려보았다.",
    "강혁은 웃으며 말했다. \"오랜만이군.\" 그의 손이 도현의 어깨를 강하게 붙잡았다.",
    "지붕 위에서 강혁이 뛰어내렸다. 착지와 동시에 그의 주먹이 날아들었다.",
    "강혁이 전화를 걸어왔다. \"지금 당장 와라.\" 끊긴 수화기 너머로 그의 숨소리가 남았다.",
]
_V2_CATEG = [  # 한월 눈=붉은색 캐논, 본문은 금색 — 기대: field_value @ 한월
    "한월이 고개를 들었다. 금색 눈동자가 가로등 불빛에 번뜩였다.",
    "그녀의 금빛 눈이 도현을 응시했다. 한월은 아무 말도 하지 않았다.",
    "한월의 눈은 금색이었다. 그 눈이 어둠 속에서 천천히 깜빡였다.",
    "달빛 아래 한월의 황금색 눈동자가 차갑게 빛났다.",
    "한월이 돌아보았다. 금색으로 물든 두 눈이 흔들리고 있었다.",
]
_V3_MONO = [  # 도현 등급=5 캐논, 본문은 3(하락) — 기대: field_value(rank↓) @ 도현
    "측정기가 깜빡였다. 화면에는 '도현 — 3등급'이라는 글자가 떠 있었다.",
    "\"도현, 등급 3.\" 심사관이 무미건조하게 읽었다. 장내가 술렁였다.",
    "도현의 등급은 3으로 떨어져 있었다. 그는 측정 결과지를 구겨 쥐었다.",
    "협회 기록부에 적힌 도현의 등급은 3이었다. 잉크가 아직 마르지 않았다.",
    "\"3등급짜리가 감히.\" 사내가 도현의 면전에서 비웃었다. 도현은 자신의 등급패를 내려다보았다.",
]
_V4_REL = [  # 도현↔한월 = 동맹(gt) 캐논, 본문은 원수 단정 — 기대: relation_contradiction
    "도현과 한월은 오래전부터 서로를 죽이려는 원수 사이였다. 마주칠 때마다 칼끝이 먼저 움직였다.",
    "둘의 악연은 유명했다. 도현과 한월은 불구대천의 적이었다.",
    "도현은 한월의 숙적이었다. 협회의 누구도 둘을 한 방에 두지 않았다.",
    "\"한월과 도현? 걔들은 철천지원수야.\" 정보상이 어깨를 으쓱했다.",
    "도현과 한월의 적대 관계는 십 년을 넘겼다. 화해의 여지는 없었다.",
]
_V5_RULE = [  # 재각성 불가 캐논 — 기대: worldrule(reawakening) (SEMANTIC, advisory 별도 보고)
    "도현의 몸이 빛에 휩싸였다. 두 번째 각성이었다. 불가능하다던 재각성이 일어난 것이다.",
    "\"재각성이라고?\" 심사관의 손이 떨렸다. 도현은 그날 두 번째 각성을 일으켰다.",
    "한 번뿐이라던 각성이 다시 찾아왔다. 도현은 재각성자가 되었다.",
    "측정기가 폭주했다. 도현의 두 번째 각성 — 재각성이 시작되고 있었다.",
    "도현의 재각성 소식은 협회 전체를 뒤집었다. 두 번째 각성은 전례가 없었다.",
]
# ---- 하드 네거티브(위양성 함정 — 어떤 hard 도 점등하면 안 됨) ----
_HN = [
    ("flashback", "도현은 죽은 강혁을 회상했다. 함께 검을 맞대던 날들이 어제처럼 떠올랐다."),
    ("flashback", "강혁과 보냈던 수련의 기억이 스쳤다. 그때 강혁은 늘 먼저 검을 들었었다."),
    ("vision", "강혁의 환영이 어른거렸다. 도현이 손을 뻗자 흩어져 사라졌다."),
    ("vision", "꿈속에서 강혁이 나타나 웃고 있었다. 잠에서 깨자 베개가 젖어 있었다."),
    ("mention", "사람들은 아직도 강혁의 이름을 입에 올렸다. 전설은 쉽게 잊히지 않았다."),
    ("mention", "\"강혁이 살아 있었다면.\" 누군가 한숨처럼 중얼거렸다."),
    ("clean_attr", "한월의 붉은 눈동자가 가로등 아래 빛났다. 그녀는 말없이 서 있었다."),
    ("clean_attr", "한월이 붉은 눈을 가늘게 떴다. 시선 끝에 도현이 있었다."),
    ("clean_rank", "도현은 5등급 헌터였다. 등급패가 가슴에서 흔들렸다."),
    ("clean_rank", "\"등급 5, 도현.\" 호명에 그가 앞으로 나섰다."),
    ("clean_rel", "도현과 한월은 굳건한 동맹이었다. 등을 맡길 수 있는 사이란 그런 것이었다."),
    ("clean_rel", "한월이 도현의 곁에 섰다. 둘은 오랜 동료답게 말없이 진형을 짰다."),
]

TYPES = {
    "dead_acting": {"templates": _V1_DEAD, "expect_kind": "state_timeline", "entity": "강혁", "grade": "quasi"},
    "categorical": {"templates": _V2_CATEG, "expect_kind": "field_value", "entity": "한월", "grade": "quasi"},
    "monotonic": {"templates": _V3_MONO, "expect_kind": "field_value(rank", "entity": "도현", "grade": "quasi"},
    "relation": {"templates": _V4_REL, "expect_kind": "relation_contradiction", "entity": None, "grade": "quasi"},
    "worldrule": {"templates": _V5_RULE, "expect_kind": "worldrule(reawakening)", "entity": None, "grade": "semantic"},
}


def build(n_per_type: int = 5) -> list[dict]:
    """라벨된 문단 목록. [{text, label(유형 or negative), expect_kind, entity}]"""
    out = []
    fill = itertools.cycle(_FILLER)
    for tname, spec in TYPES.items():
        for i in range(n_per_type):
            base = spec["templates"][i % len(spec["templates"])]
            text = f"{next(fill)} {base} {next(fill)}"
            out.append({"text": text, "label": tname,
                        "expect_kind": spec["expect_kind"], "entity": spec["entity"], "grade": spec["grade"]})
    neg_factor = max(1, n_per_type // 4)   # 네거티브도 n 에 비례 확장(FPR CI 폭 축소)
    for rep in range(neg_factor):
        for kind, base in _HN:
            out.append({"text": f"{next(fill)} {base} {next(fill) if rep else ''}".strip(),
                        "label": f"negative:{kind}", "expect_kind": None, "entity": None, "grade": None})
    return out
