# -*- coding: utf-8 -*-
"""XR-3 ⑵-a — PL-1 프록시 consumer 태그(관측 additive·프롬프트 바이트 불변·LLM 0콜).

왜: 콜 전문 로그(PL-1)가 kind/model 만 갖고 있어 "이 콜이 어느 스테이지인가"를 사후에 알 수 없었다.
contextvar 로 스테이지 라벨을 실어 로그 헤더·파일명에 남긴다. 태그 없는 콜은 unclassified 로
기록한다(결측 정직 — 침묵 금지).

검사 6축:
  ⓐ 태그 기록 — with consumer("draft") 안의 콜이 헤더 consumer=draft · 파일명 접미로 남는다
  ⓑ 결측 정직 — 태그 밖 콜은 unclassified(빈 값·침묵 아님)
  ⓒ 중첩·복원 — 안쪽 스테이지가 이기고, with 를 빠져나오면 바깥 값으로 정확히 복원된다(예외 경로 포함)
  ⓓ 위임 무변경 — 프록시를 통과한 messages 객체·kwargs·반환값이 원본과 동일(바이트 불변 계약)
  ⓔ 파일명 안전 — 콜론이 든 라벨(story_pass:base)이 Windows 경로 금지문자를 만들지 않는다
  ⓕ NEVER throws — 로그 루트가 쓰기 불가여도 콜은 정상 반환(관측이 생성을 막지 않는다)

실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 py -3.12 tools/test_xr3_consumer_tag.py
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from novelcopilot.llm import promptlog
from novelcopilot.llm.promptlog import PromptLoggingProvider, consumer, stage, current


class _Inner:
    """LLMProvider 대역 — 받은 인자를 그대로 기록하고 고정 응답(실 LLM 콜 0)."""
    gen_model = "fake-model"

    def __init__(self):
        self.seen = []

    def chat(self, messages, **kw):
        self.seen.append(("chat", messages, dict(kw)))
        return "응답 본문"

    def chat_json(self, messages, **kw):
        self.seen.append(("chat_json", messages, dict(kw)))
        return {"ok": True}

    def chat_tools(self, messages, **kw):
        self.seen.append(("chat_tools", messages, dict(kw)))
        return "완료"


def _proxy(tmp: Path) -> PromptLoggingProvider:
    promptlog._LOG_ROOT = tmp                      # 테스트 격리(실 logs/ 오염 금지)
    p = PromptLoggingProvider(_Inner(), tag="xr3test")
    return p


def _files(tmp: Path) -> list[Path]:
    return sorted((tmp / "xr3test").glob("*.txt"))


def _header(f: Path) -> str:
    return f.read_text(encoding="utf-8").splitlines()[0]


# ── ⓐ 태그 기록 ──
def test_consumer_label_in_header_and_filename():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = _proxy(tmp)
        with consumer("draft"):
            p.chat([{"role": "user", "content": "본문 써"}])
        fs = _files(tmp)
        assert len(fs) == 1
        assert "consumer=draft" in _header(fs[0]), _header(fs[0])
        assert fs[0].name.endswith("_chat_draft.txt"), fs[0].name
    print("[OK] ⓐ 태그가 헤더(consumer=draft)와 파일명 접미에 기록")


# ── ⓑ 결측 정직 ──
def test_untagged_call_is_unclassified():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = _proxy(tmp)
        p.chat_json([{"role": "user", "content": "태그 밖"}])
        fs = _files(tmp)
        assert "consumer=unclassified" in _header(fs[0]), _header(fs[0])
        assert fs[0].name.endswith("_chat_json_unclassified.txt"), fs[0].name
    assert current() == "unclassified"             # 컨텍스트 밖 기본값도 정직 라벨
    print("[OK] ⓑ 미태그 콜 = unclassified(빈 값·침묵 아님)")


# ── ⓒ 중첩·복원 ──
def test_nesting_and_restore():
    assert current() == "unclassified"
    with consumer("story_pass:check"):
        assert current() == "story_pass:check"
        with consumer("story_pass:joint"):
            assert current() == "story_pass:joint"   # 안쪽 스테이지가 이긴다
        assert current() == "story_pass:check"       # 정확히 복원
    assert current() == "unclassified"

    @stage("draft")
    def _boom():
        assert current() == "draft"
        raise RuntimeError("스테이지 안 예외")

    try:
        _boom()
    except RuntimeError:
        pass
    assert current() == "unclassified", "예외 경로에서 contextvar 누수"
    print("[OK] ⓒ 중첩 시 안쪽 우선 · 예외 경로 포함 복원")


# ── ⓓ 위임 무변경(프롬프트 바이트 불변) ──
def test_delegation_bytes_unchanged():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = _proxy(tmp)
        msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
        snapshot = [dict(m) for m in msgs]
        with consumer("draft"):
            out = p.chat(msgs, temperature=0.7, max_tokens=99)
        kind, seen_msgs, kw = p._inner.seen[0]
        assert kind == "chat"
        assert seen_msgs is msgs and msgs == snapshot     # 같은 객체 · 내용 무변경
        assert kw == {"temperature": 0.7, "max_tokens": 99}
        assert out == "응답 본문"
    print("[OK] ⓓ 위임 인자·반환 무변경(메시지 객체 동일성까지)")


# ── ⓔ 파일명 안전(콜론 라벨) ──
def test_colon_label_filename_safe():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = _proxy(tmp)
        with consumer("story_pass:base"):
            p.chat([{"role": "user", "content": "x"}])
        fs = _files(tmp)
        assert fs, "콜론 라벨에서 파일 생성 실패(경로 금지문자)"
        assert ":" not in fs[0].name and "story_pass-base" in fs[0].name, fs[0].name
        assert "consumer=story_pass:base" in _header(fs[0])   # 헤더는 원형 라벨 유지
    print("[OK] ⓔ 콜론 라벨 — 파일명은 하이픈 강등, 헤더는 원형")


# ── ⓕ NEVER throws ──
def test_logging_failure_never_blocks_call():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "존재하지-않는" / "\x00잘못된경로"   # mkdir 실패 유도
        promptlog._LOG_ROOT = tmp
        p = PromptLoggingProvider(_Inner(), tag="xr3test")
        with consumer("draft"):
            out = p.chat([{"role": "user", "content": "x"}])
        assert out == "응답 본문"                              # 로깅 실패에도 콜은 정상
    print("[OK] ⓕ 로깅 실패가 콜을 막지 않는다(NEVER throws 유지)")


if __name__ == "__main__":
    fails = 0
    for fn in (test_consumer_label_in_header_and_filename, test_untagged_call_is_unclassified,
               test_nesting_and_restore, test_delegation_bytes_unchanged,
               test_colon_label_filename_safe, test_logging_failure_never_blocks_call):
        try:
            fn()
        except AssertionError as e:
            fails += 1
            print(f"[FAIL] {fn.__name__}: {e}")
    print("=" * 60)
    print("[OK] XR-3 consumer 태그 전 축 통과" if not fails else f"[FAIL] {fails}건")
    sys.exit(1 if fails else 0)
