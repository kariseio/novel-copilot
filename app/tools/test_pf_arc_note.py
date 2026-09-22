# -*- coding: utf-8 -*-
"""PF-1·PF-2 잠금 검증 — 인물 프로필 3중 오염(공개 정체 / 아크·반전 / 세계 제도) 분리의 핵심 계약.
실 LLM 0콜(스텁)·결정론.

계약(사전 등록):
  ① arc_note 는 설계 계층(_cast_context → arc_planner)에 주입된다 — 플래너가 궤적을 보고 인물에서 사건 도출.
  ② arc_note 는 회차 생성(프로즈) 프롬프트에 절대 도달하지 않는다 — 프로즈 유일 프로필 소비처는 데뷔 앵커(profile).
     이 축이 무너지면 최종 반전이 본문 생성 콜로 직행한다(FS-1·quiet_foreshadow 동형). 되돌리지 말 것.
  ③ arc_note 미설정 시 주입 0 — 프롬프트 바이트 동일(하위호환·구 JSON 그대로 로드).

실행: (app/ 에서) py -3.12 -X utf8 tools/test_pf_arc_note.py
"""
from __future__ import annotations
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))   # app/ → novelcopilot

from novelcopilot.config import get_settings
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.engine.factory import build_engine
from novelcopilot.services import copilot as cp
from novelcopilot.services.copilot import _cast_context
from novelcopilot.engine import harness as hn
from novelcopilot.llm.base import LLMProvider

_SENTINEL = "SENTINEL_궤적_최종반전_준호가_본체"


class _Fake(LLMProvider):
    def __init__(self):
        super().__init__()

    def chat(self, *a, **k):
        return ""

    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]

    def chat_json(self, messages, **k):
        return {}


def _world() -> WorldConfig:
    return WorldConfig(title="t", genre="x", entities=[
        EntitySpec(id="hero", name="서준호",
                   profile="반지하 7년차 싸구려 퇴마사. 능청꾼.",
                   arc_note=_SENTINEL),
        EntitySpec(id="watch", name="하지연", profile="관리국 정예 현장요원.")])


# ── ① 설계 계층 주입 + ③ 하위호환 ────────────────────────────────
def test_cast_context_arc_note() -> bool:
    s = get_settings()
    w = _world()
    ont = build_engine(w, _Fake(), s).ontology
    ctx = _cast_context(ont, w, ["hero", "watch"], 5)
    # ① 설계 콜엔 궤적(arc_note)이 프로필과 함께 들어간다
    ok = ("서준호" in ctx and "싸구려 퇴마사" in ctx and _SENTINEL in ctx)
    # ③ arc_note 없는 인물은 궤적 라인 무추가(바이트 동일) — 하지연 세그먼트에 '궤적' 라벨 부재
    hero_seg = [l for l in ctx.splitlines() if "서준호" in l or (l.strip().startswith("(궤적") )]
    watch_only = _cast_context(ont, w, ["watch"], 5)
    ok &= ("궤적" not in watch_only and _SENTINEL not in watch_only)
    print(f"[{'OK' if ok else 'FAIL'}] ① 설계 콜 arc_note 주입 · ③ 미설정 인물 무추가(바이트 동일)")
    return ok


# ── ② 프로즈 미도달(구조 잠금) ────────────────────────────────────
def test_arc_note_never_in_prose() -> bool:
    # 실제 '읽기'(.arc_note 속성 접근·["arc_note"] 키 접근)만 검사 — 주석의 단어 언급은 무해(코드 아님).
    def reads_arc_note(src):
        return (".arc_note" in src) or ('["arc_note"]' in src) or ('"arc_note")' in src) or ("'arc_note'" in src)
    # (a) 프로즈 조립기(harness)는 arc_note 를 읽지 않는다 — 회차 생성 프롬프트 유입 경로 자체가 없다
    ok = not reads_arc_note(inspect.getsource(hn))
    if not ok:
        print("    ! harness(프로즈 조립)가 arc_note 를 읽음 — 반전 유출 위험")
    # (b) 데뷔 앵커(프로즈 유일 프로필 소비처)는 profile 만 읽고 arc_note 미참조
    cp_src = inspect.getsource(cp)
    i = cp_src.index('source="cast_debut"')
    debut_block = cp_src[i:i + 500]
    ok &= (".profile" in debut_block and not reads_arc_note(debut_block))
    # (c) arc_note 는 설계 계층(_cast_context)에서 실제로 읽힌다
    ok &= reads_arc_note(inspect.getsource(_cast_context))
    print(f"[{'OK' if ok else 'FAIL'}] ② arc_note 프로즈 미도달 — harness 0참조·데뷔앵커 profile전용·설계계층 전담")
    return ok


if __name__ == "__main__":
    results = [test_cast_context_arc_note(), test_arc_note_never_in_prose()]
    print("\nPF-1·PF-2(프로필 분리) 잠금:", "ALL GREEN" if all(results) else "FAIL")
    sys.exit(0 if all(results) else 1)
