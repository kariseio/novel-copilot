# -*- coding: utf-8 -*-
"""B-35 회귀 가드 — 초반 회차 표지 마크업(#자작표제)·&nbsp; 스페이서 소스차단. 실 LLM 0콜.

실측(3작 24화): ch1~3 본문이 '웹소설 포스트'로 포맷돼 line0 에 등록 제목과 다른 자작 표제(# 짐꾼의 값 vs 등록 '짐꾼 몫 3%')와
&nbsp; 스페이서 줄이 얹혔다(4화부터 소멸). 소스는 두 갈래:
  (A) 생성 프라이밍 — assemble()가 등록 제목을 draft 에 주입 안 함(그래서 모델이 '자작' 표제를 얹음) + out_instr 이 '첫 줄=첫 문장'을
      확립 안 함(초반엔 '작품 여는' 게슈탈트가 지배) → out_instr 긍정 앵커로 소스차단.
  (B) 재주입 루프 — 직전 회차 tail 재주입이 collapse_dashes 만 적용(&nbsp; 미정규화)해 모델이 자기 &nbsp; litter 를 교재로 봄
      → strip_structural_markup 로 재주입 정규화(루프 차단).
발행 경계 백스톱 = strip_structural_markup(선행 표지 블록 제거·단독 &nbsp;→빈 줄). 라인 단위·위치 기반이라
diegetic 괄호(B-24)·표현 마크업(**강조**·본문 중간 --- 구분선)은 불변(두더지 무저촉).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_b35_structural_markup.py
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

from novelcopilot.engine.textfmt import strip_structural_markup
from novelcopilot.engine.harness import ChapterGenerator, sanitize_meta
from novelcopilot.engine.prompts import PromptAssembler
from novelcopilot.domain.types import ContextBoard, SceneSpec
from novelcopilot.domain.world import StyleSpec


class _Bus:
    def emit(self, *a, **k):
        pass


class CaptureProvider:
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


# ---------- strip_structural_markup: 선행 표지 제거 ----------
def test_leading_title_and_nbsp_removed():
    # 붕괴 ch1 실측 패턴: # 자작표제 + 빈 줄 + &nbsp; + 본문
    body = "# 짐꾼의 값\n\n&nbsp;\n\n각인이 반응했다. 그는 팔뚝을 문질렀다."
    out = strip_structural_markup(body)
    assert not out.lstrip().startswith("#"), f"선행 표제 미제거: {out!r}"
    assert "&nbsp;" not in out
    assert out.lstrip().startswith("각인이 반응했다"), f"본문 첫 문장부터 시작 안 함: {out!r}"


def test_multiple_leading_headings_removed():
    # 서리꽃 ch1 실측: # 작품명 + ## 회차표제 두 겹
    body = "# 북부의 서리꽃\n\n## 제1화. 거래의 청혼\n\n&nbsp;\n\n그녀는 성문 앞에 섰다."
    out = strip_structural_markup(body)
    assert "#" not in out
    assert out.strip().startswith("그녀는 성문 앞에 섰다")


def test_midbody_nbsp_becomes_blank():
    body = "첫 문단이다.\n\n&nbsp;\n\n둘째 문단이다."
    out = strip_structural_markup(body)
    assert "&nbsp;" not in out
    assert "첫 문단이다" in out and "둘째 문단이다" in out   # 문단은 보존


# ---------- strip_structural_markup: 보존 불변식(두더지 무저촉) ----------
def test_diegetic_and_expressive_preserved():
    # B-24 diegetic 괄호 + 표현 마크업(**강조**) + 본문 중간 --- 구분선 → 전부 불변
    body = ("그는 상태창을 봤다. (오류: 접근 거부)\n\n"
            "**중요한 건 따로 있었다.**\n\n"
            "---\n\n"
            "장면이 바뀌었다. (그건 모순이었다.)")
    out = strip_structural_markup(body)
    assert "(오류: 접근 거부)" in out          # 시스템 diegetic 불변
    assert "(그건 모순이었다.)" in out          # 서사 괄호 불변
    assert "**중요한 건 따로 있었다.**" in out   # 강조 표현 마크업 불변
    assert "\n---\n" in ("\n" + out + "\n")     # 본문 중간 구분선 불변(선행 아님)


def test_clean_prose_is_noop():
    body = "그는 문을 열었다. 바람이 불었다.\n\n그녀가 돌아봤다."
    assert strip_structural_markup(body) == body


def test_leading_dialogue_not_stripped():
    # 대사 오프너 '"' 는 표지(#/hr/nbsp)가 아니므로 선행 제거 대상 아님
    body = '"거기 누구야."\n\n그는 뒤를 돌아봤다.'
    out = strip_structural_markup(body)
    assert out.startswith('"거기 누구야."')


def test_all_preamble_preserved_guard():
    # 표지만 있고 본문이 없으면(degenerate) 원문 보존(파괴 가드)
    body = "# 제목\n\n&nbsp;"
    assert strip_structural_markup(body) == body


# ---------- 생성 지시(A): out_instr 긍정 앵커 ----------
def test_out_instr_positive_first_line_anchor():
    g, prov = _gen()
    board = ContextBoard(chapter=1, prev_chapter="", story_so_far="")
    scene = SceneSpec(index=0, goal="주인공 일상", key_events=["전제 전환"])
    g._draft(board, scene, "", last=True, closing=False, chapter_mode=True)
    user = prov.captured[0][1]["content"]
    assert "첫 줄이 곧 이야기의 첫 문장" in user            # 첫 줄=첫 문장(표지 줄 없음)의 긍정 확립
    assert "회차 제목은 따로 관리되므로" in user            # 표제는 별도 관리(자작 표제 프라이밍의 긍정 재귀속)
    # 옛 프라이밍('첫 글자부터 …산문으로만 채워라')이 표제 얹기를 못 막던 문구는 교체돼 재유입 안 됨
    assert "첫 글자부터 마지막 글자까지 독자가 읽는 산문으로만 채워라" not in user
    # 출력 계약·부호 whitelist 등 기존 의도 보존(회귀 방지)
    assert "출력은 이번 회차의 소설 본문 그것 하나뿐이다" in user
    assert "쉼표·마침표·말줄임표" in user and "끼어들 자리는 없다" in user


# ---------- 재주입 루프(B): 직전 회차 tail 정규화 ----------
def test_prev_chapter_reinjection_normalized():
    asm = PromptAssembler(StyleSpec(), prev_chapter_chars=4000)
    prev = "# 삼킨 것의 값\n\n&nbsp;\n\n그는 게이트를 나섰다. 숨이 찼다.\n\n&nbsp;\n\n끝까지 걸었다."
    board = ContextBoard(chapter=2, prev_chapter=prev)
    # 새 회차 첫 콜: prev_scenes="" → flow_block(직전 회차 원문) 노출
    out = asm.assemble(board, SceneSpec(index=0, goal="목표", key_events=["사건"]), "")
    assert "[직전 회차 원문" in out                # flow_block 존재 확인(전제)
    # 재주입된 직전 회차 원문에 자기 litter(&nbsp;·표지 #)가 재학습 교재로 안 들어감
    assert "&nbsp;" not in out, "재주입 &nbsp; litter 미정규화(루프 미차단)"
    assert "# 삼킨 것의 값" not in out             # 표지도 재주입 안 됨
    assert "그는 게이트를 나섰다" in out           # 본문 자체는 보존


# ---------- 통합: sanitize_meta + strip_structural_markup 스택이 diegetic 보존 ----------
def test_publish_stack_preserves_diegetic():
    body = "# 표제\n\n눈앞에 붉은 글자가 떠올랐다. (오류: 접근 거부)\n\n&nbsp;\n\n(주의: 함정 감지)"
    out = strip_structural_markup(sanitize_meta(body))
    assert "(오류: 접근 거부)" in out and "(주의: 함정 감지)" in out
    assert "#" not in out and "&nbsp;" not in out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"B-35 검증: ALL GREEN ({len(fns)} tests)")
