# -*- coding: utf-8 -*-
"""C-4 잔여 부정명령 긍정 전환 검증 — 회차 생성 프롬프트 스냅샷성 가드. 실 LLM 0콜(fake provider 캡처).

pink-elephant(B-23, C-3 후속): 부정명령("~하지 마라/말 것")과 기피 대상 호명(반복·재서술·'다음 회에 계속' 예문)이
그 토큰을 오히려 프라이밍 → 회차 생성 경로(_HOOKS·_CLOSING·_draft out_instr/recent_tails·_continue·
_summarize·_fix_tics·PromptAssembler)에서 소스차단하고 재유입을 여기서 잠근다.

보존 예외 2종은 '남아 있음'을 함께 잠근다(전환 대상 아님):
  ① 캐논/바닥 안전 계약 — system '확정 설정 절대 위반 금지'·assemble 의 [확정 설정]/[세계 규칙] 헤더
     (floor_only 는 test_b10, revise_prose 사실불변 계약은 퇴고 테스트가 별도 잠금)
  ② 떡밥 개방 절 — test_t5_entity_ssot 가 별도 잠금('새 정체 단정 금지' 류 유지)
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

from novelcopilot.engine.harness import ChapterGenerator
from novelcopilot.engine.prompts import PromptAssembler
from novelcopilot.domain.types import ContextBoard, SceneSpec, RetrievedItem
from novelcopilot.domain.world import StyleSpec


class _Bus:
    def emit(self, *a, **k):
        pass


class CaptureProvider:
    """프롬프트 캡처 전용 — chat/chat_json 의 messages 를 기록만 하고 무해한 응답."""
    def __init__(self):
        self.captured = []
        self.last_truncated = False

    def chat(self, messages, temperature=0.0, max_tokens=0):
        self.captured.append(messages)
        return "본문."

    def chat_json(self, messages, temperature=0.0, max_tokens=0):
        self.captured.append(messages)
        return {}


def _gen():
    settings = SimpleNamespace(prev_chapter_context_chars=4000, chapter_max_tokens=100,
                               gen_max_tokens=100, craft_progress=True, scene_style_anchor=False)
    prov = CaptureProvider()
    g = ChapterGenerator(prov, checker=None, style=StyleSpec(), event_bus=_Bus(), settings=settings)
    return g, prov


def _board(**kw):
    return ContextBoard(chapter=3, **kw)


# 전환된 문자열에 재유입되면 안 되는 부정명령·프라이밍 토큰(유저 프롬프트 기준 — [확정 설정] 캐논 헤더의
# '위반 금지'는 보존 예외 ①이라 '금지' 단독은 여기서 못 잠그고, 구체 문구 단위로 잠근다)
_BANNED_USER = ("하지 마", "쓰지 마", "말 것", "말라", "지 말고", "반복", "되풀이", "재연",
                "예고문", "번역투", "영어식", "재출력", "도배", "되묻는", "잊지 말",
                "재서술", "되감기", "다시 소개", "다음 회에 계속")


# ---------- 클래스 상수: _HOOKS(cliffhanger) · _CLOSING ----------
def test_hooks_and_closing_positive_only():
    hook = ChapterGenerator._HOOKS["cliffhanger"]
    for banned in ("말고", "하지 마", "말라", "금지", "닫지"):
        assert banned not in hook, f"부정명령 재유입(_HOOKS): {banned!r}"
    assert "미결의 순간" in hook and "궁금해지게" in hook       # 절단 의도(긍정 방향지시) 보존
    closing = ChapterGenerator._CLOSING
    for banned in ("말고", "하지 마", "말라", "금지", "끊지"):
        assert banned not in closing, f"부정명령 재유입(_CLOSING): {banned!r}"
    # 완결 의도 보존: 매듭 + 미결 결행 + '이미 등장한 요소' whitelist(신규 떡밥 차단의 긍정형)
    assert "매듭지어" in closing and "결행" in closing and "이미 등장한" in closing


# ---------- _draft(chapter_mode): out_instr·recent_tails 절 ----------
def test_draft_chapter_prompt_positive_only():
    g, prov = _gen()
    board = _board(prev_chapter="그는 문 앞에 섰다.", story_so_far="그는 도시를 떠났다.")
    scene = SceneSpec(index=0, goal="주인공이 문을 연다", key_events=["문이 열린다"])
    g._draft(board, scene, "", last=True, closing=False,
             recent_tails=["…어둠이 내려앉았다"], chapter_mode=True)
    sys_msg, user = prov.captured[0][0]["content"], prov.captured[0][1]["content"]
    for banned in _BANNED_USER:
        assert banned not in user, f"부정명령/프라이밍 토큰 재유입(_draft user): {banned!r}"
    # 출력 계약(긍정형)·절단점·부호 whitelist·사건 1회 서술 — 전환 의도 보존
    assert "출력은 이번 회차의 소설 본문 그것 하나뿐이다" in user
    assert "자연스러운 절단점" in user and "한 번씩만 서술한 뒤" in user
    assert "쉼표·마침표·말줄임표" in user
    # recent_tails 절: 재탕 회피가 '다른 수법·다른 결' 긍정 방향지시로
    assert "다른 수법·다른 결" in user and "장면 '안'에 머물러" in user
    # B-24 선언문(부정 '명령'형 아님)은 유지 — 메타누출 소스차단 프레이밍
    assert "끼어들 자리는 없다" in user
    # 보존 예외 ①: 캐논 바인딩은 부정형 그대로(안전 계약)
    assert "확정 설정 절대 위반 금지" in sys_msg
    assert "[확정 설정: 절대 위반 금지" in user


def test_draft_nonchapter_out_instr_positive():
    g, prov = _gen()
    g._draft(_board(), SceneSpec(index=0, goal="장면", key_events=[]), "", chapter_mode=False)
    user = prov.captured[0][1]["content"]
    assert "출력은 소설 본문 그것 하나뿐이다" in user
    assert "머리말·설명·메타 금지" not in user


# ---------- _continue: 이어쓰기 goal·plan_ctx·출력 계약 ----------
def test_continue_prompt_positive_only():
    g, prov = _gen()
    board = _board(prev_chapter="직전 회차 원문", story_so_far="누적 줄거리")
    g._continue(board, "그는 걸었다. 문이 닫혔다.", closing=False,
                recent_tails=["…그는 쓰러졌다"], key_events=["탈출"])
    user = prov.captured[0][1]["content"]
    for banned in _BANNED_USER:
        assert banned not in user, f"부정명령/프라이밍 토큰 재유입(_continue user): {banned!r}"
    # 전환 의도 보존: 새 재료 전진 + 등장인물 whitelist + 종결마커 차단의 긍정형 + 접합 계약
    assert "직전에 없던 새 사건·새 정보" in user
    assert "이미 등장한 이들만" in user
    assert "끝맺음까지 포함해 모든 문장은 이야기 서술로만" in user
    assert "마지막 문장 바로 다음" in user
    assert "다른 결로 끊어라" in user                       # recent_tails 재탕 회피(긍정 방향지시)
    # C-4 적대검증 MED-1: 이어쓰기 프롬프트에 화 경계 진입 명령이 들어가면
    # '마지막 문장 바로 다음' 접합 계약과 정면 모순(되감기·강제 장면전환 유도) — 부재를 잠근다
    # (HK-1 로 문안이 "'이후'에서 시작"으로 바뀜 — 잠금도 새 마커로 이전)
    assert "'이후'에서 시작" not in user and "'이후'의 새 장면" not in user
    assert "끊김 없이 이어서" in user


# ---------- _summarize · _fix_tics: 보조 패스 지시문 ----------
def test_summarize_and_fixtics_positive():
    g, prov = _gen()
    g._summarize("본문이다.")
    sys_msg = prov.captured[0][0]["content"]
    assert "감상·문체 묘사 금지" not in sys_msg and "사실·사건 정보만" in sys_msg
    g2, prov2 = _gen()
    g2._fix_tics("본문이다.", [("짧게 말했다", 5)])
    sys2 = prov2.captured[0][0]["content"]
    assert "말 것" not in sys2 and "서로 다르게" in sys2


# ---------- PromptAssembler: sofar 헤더·보이스 헤더·대화 전진·연속성 지시 ----------
def test_assembler_positive_only():
    asm = PromptAssembler(StyleSpec())
    board = _board(world_rules=["마법은 대가를 요구한다"], prev_chapter="직전 본문",
                   story_so_far="지금까지 이야기", voice_cards="- 레오: 짧고 건조한 말투")
    out = asm.assemble(board, SceneSpec(index=0, goal="장면 목표", key_events=["사건"]), "")
    for banned in ("잊지 말", "도배", "되묻는", "다시 소개", "재서술", "되감기", "반복"):
        assert banned not in out, f"부정명령/프라이밍 토큰 재유입(assemble): {banned!r}"
    assert "기억하고 이어라" in out                          # sofar 헤더 긍정 전환
    # VL-1(2026-08-15): 시그니처 quota 문장은 형태 메뉴 호명이라 삭제 — 참조 전용 라벨이 신 계약
    assert "인물 보이스(참조 전용)" in out and "새 문장과 새 대사로 드러내라" in out
    assert "새 정보나 새 결정" in out                        # 대화 전진 기능(정보 재탕 차단의 긍정형)
    # HK-1: 화 경계 첫 콜 계약 = '이후'에서 시작 + 말미 미해결 순간 소화(훅 미회수 실측 수술)
    assert "시간은 앞으로만 흐른다" in out and "'이후'에서 시작하라" in out
    assert "미해결 순간" in out                              # 이월 훅 소화 계약 보존
    # 보존 예외 ①: 캐논/세계규칙 헤더의 부정형은 안전 계약이라 유지
    assert "[확정 설정: 절대 위반 금지" in out
    assert "[세계 규칙: 이 작품의 불변 규칙. 어기지 마라]" in out


def test_assembler_continuity_branches_med1():
    """C-4 적대검증 MED-1 잠금: '직전 회차 이후의 새 장면' 진입 명령은 화 경계 첫 콜에만 —
    이어쓰기(prev_chapter="" + 직전 본문)·1화(둘 다 없음)에서는 되감기/강제 장면전환·dangling 참조를
    유발하므로 맥락별 분기와 그 부재를 잠근다."""
    asm = PromptAssembler(StyleSpec())
    scene = SceneSpec(index=0, goal="장면 목표", key_events=["사건"])
    # ① 새 회차 첫 콜(직전 회차 원문 O, 회차 내 직전 본문 X) → 경계 진입 + 말미 미해결 소화(HK-1)
    out = asm.assemble(_board(prev_chapter="직전 본문"), scene, "")
    assert "'이후'에서 시작하라" in out and "미해결 순간" in out and "시간은 앞으로만 흐른다" in out
    # ② 이어쓰기(_continue 경로: prev_chapter="" + 직전 본문 O) → 접합 모순 명령 부재, 끊김 없는 잇기
    out = asm.assemble(_board(), scene, "그는 걸었다. 문이 닫혔다.")
    assert "'이후'에서 시작" not in out and "직전 회차가 끝난 지점" not in out
    assert "끊김 없이 이어서" in out and "시간은 앞으로만 흐른다" in out
    # ③ 1화 첫 콜(둘 다 X) → 존재하지 않는 '직전 회차' 참조 없이 전방 진행만(발단 grounding 무간섭)
    out = asm.assemble(_board(), scene, "")
    assert "'이후'에서 시작" not in out and "직전 회차가 끝난 지점" not in out
    assert "앞으로 나아가게 전개하라" in out and "시간은 앞으로만 흐른다" in out


# ---------- AN-1a: [참조 맥락] 프로즈 발췌에 참조-전용 지시 병기(자기 이력 앵커 완화) ----------
def test_reference_context_use_only_directive():
    """과거 회차 프로즈 원문(rag_chunk)이 [참조 맥락]에 실릴 때만 '참조 자료 — 새 문장으로 다시 써라'
    긍정 지시를 병기(few-shot 앵커 완화). 발췌가 없으면 지시도 없다(토큰 0 하위호환)."""
    asm = PromptAssembler(StyleSpec())
    scene = SceneSpec(index=0, goal="장면 목표", key_events=["사건"])
    # ① 프로즈 발췌 존재 → 참조-전용 지시 병기(긍정형만)
    board = _board(narrative=[RetrievedItem(source="rag_chunk", ref="1",
                                            text='"넌 이미 늦었어." 그가 속삭였다.')])
    out = asm.assemble(board, scene, "")
    assert "참조 자료" in out
    assert "새 문장과 새 대사로 다시 써라" in out
    # 프로즈 발췌 원문은 그대로 실려 있고(참조 기능 보존), 지시는 그 앞에 온다
    assert '"넌 이미 늦었어."' in out
    assert out.index("참조 자료") < out.index('"넌 이미 늦었어."')
    # 긍정형만 — 피할 행위/나쁜 예 호명 금지(pink-elephant 헌법)
    for banned in ("그대로", "재사용", "복사", "베끼", "베껴", "하지 마", "말 것", "말라",
                   "반복", "재서술", "금지"):
        assert banned not in out.split("[참조 맥락")[1], f"부정명령/기피 호명 재유입(참조 지시): {banned!r}"
    # ② 발췌 없음 → 지시도 없음(불필요 토큰 0·하위호환)
    out0 = asm.assemble(_board(), scene, "")
    assert "참조 자료" not in out0
    assert "새 문장과 새 대사로 다시 써라" not in out0
    assert "(이전 맥락 없음)" in out0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"C-4 검증: ALL GREEN ({len(fns)} tests)")
