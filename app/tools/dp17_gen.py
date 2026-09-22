# -*- coding: utf-8 -*-
"""DP-17 화자 보이스 정독 검증 — DP-4b 작품(c6e39900882a·pov=first·8화)에 9화 1개 생성.

DP-17(43ea7a0)의 narrator_voice 가 작가 편집 경로로 설정된 뒤, 실제 집필 콜에 voice 블록+first 확장
지시가 주입됐는지 gen_context 로 확인하고, 9화를 생성해 정독 판정용 원문을 덤프한다. dp4b_loop 의
프롬프트 캡처 훅(집필 _draft 콜의 system+user 포착)을 임의 회차용으로 재사용한다. 자체 파일(dp17_*)만 쓴다.

명령(app/ 에서):
  gen   <pid>                     : 다음 회차 생성 + 집필 프롬프트 캡처 + voice 게이트 + 덤프
  regen <pid> "<fix>"             : 마지막 회차 재생성(정련 루프) + 캡처 + 게이트 + 덤프

산출(tools/reports/): dp17_ch<N>.txt · dp17_genctx_ch<N>.txt · dp17_gate.json
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

APP = pathlib.Path(__file__).resolve().parents[1]
REPORTS = APP / "tools" / "reports"

_PROMPTS: list[dict] = []
_STYLE_MARK = "[웹소설 문체 규칙"


def _install_prompt_capture():
    from novelcopilot.llm.anthropic_provider import AnthropicProvider
    if getattr(AnthropicProvider, "_dp17_wrapped", False):
        return
    orig = AnthropicProvider.chat

    def wrapped(self, messages, *, temperature=0.7, max_tokens=2200, json_mode=False):
        try:
            sysm = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
            usrm = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
            if _STYLE_MARK in sysm:      # 집필(_draft) 콜만
                _PROMPTS.append({"system": sysm, "user": usrm})
        except Exception:
            pass
        return orig(self, messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode)

    AnthropicProvider.chat = wrapped
    AnthropicProvider._dp17_wrapped = True


def _dump_genctx(chapter: int) -> dict | None:
    if not _PROMPTS:
        return None
    p = _PROMPTS[0]
    sysm, usrm = p["system"], p["user"]
    checks = {
        # DP-8 1인칭 지시 실주입
        "pov_first_directive": "[서술 시점 — 1인칭 주인공]" in sysm,
        # DP-17 first 확장 지시(긍정 전용) 실주입
        "pov_first_voice_expansion": "서술은 화자의 목소리다" in sysm,
        # DP-17 voice 블록(작가 설정분) 실주입 — 핵심 검증
        "narrator_voice_block": "[이 작품 화자의 목소리" in sysm,
        "narrator_voice_seed_phrase": "마른 이죽거림" in sysm,
        "narrator_voice_consume_tail": "이 화자의 눈과 입을 통과해 나오게" in sysm,
    }
    L = ["## DP-17 gen_context 게이트 — 9화 집필 프롬프트 실주입 확인", ""]
    for k, v in checks.items():
        L.append(f"[{'PASS' if v else 'FAIL'}] {k}")
    L += ["", "=" * 72, "## 포착된 system(집필 문체 블록 — style + pov + voice)", sysm,
          "", "=" * 72, "## 포착된 user(집필 지시) 앞 4000자", usrm[:4000]]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"dp17_genctx_ch{chapter}.txt").write_text("\n".join(L), encoding="utf-8")
    return checks


def _svc():
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    return CopilotService(s, repo), repo


def _reader_feedback(state, chapter: int):
    c = next((x for x in state.chapters if x.chapter == chapter), None)
    return getattr(c, "reader_feedback", None) if c else None


def _dump_chapter(state, chapter: int, genctx: dict | None):
    c = next((x for x in state.chapters if x.chapter == chapter), None)
    if c is None:
        return
    rf = getattr(c, "reader_feedback", None)
    out = []
    out.append(f"# DP-17 회차 {chapter} — {c.title}")
    out.append(f"status={c.status.value} hook={c.hook_type} place={c.place} "
               f"pov={state.world.style.pov} voice_set={bool(state.world.style.narrator_voice)}")
    if genctx is not None:
        out.append(f"gen_context(voice 게이트): {json.dumps(genctx, ensure_ascii=False)}")
    out.append("")
    out.append("## reader_feedback (DP-10 신 스키마 — 시뮬 독자, 참고)")
    out.append(json.dumps(rf, ensure_ascii=False, indent=2) if rf else "(없음)")
    out.append("")
    out.append("## 본문")
    out.append(c.text or "(빈 본문)")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"dp17_ch{chapter}.txt").write_text("\n".join(out), encoding="utf-8")


def _log_gate(chapter: int, genctx: dict | None, rf, nws: int):
    p = REPORTS / "dp17_gate.json"
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[str(chapter)] = {"chapter": chapter, "gen_context": genctx, "nws": nws,
                          "reader_feedback": rf}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_gen(pid: str) -> int:
    _PROMPTS.clear()
    _install_prompt_capture()
    svc, repo = _svc()
    st = repo.get(pid)
    next_ch = st.current_chapter + 1
    print(f"[gen] {pid} 회차 {next_ch} 생성 시작…(pov={st.world.style.pov} "
          f"voice_set={bool(st.world.style.narrator_voice)})", flush=True)
    t0 = time.monotonic()
    res = svc.generate_next_chapter(pid)
    if res.get("completed"):
        print(f"[completed] {res.get('reason')} current={res.get('current_chapter')}", flush=True)
        return 0
    rec = res.get("record")
    status = getattr(getattr(rec, "status", ""), "value", "")
    st = repo.get(pid)
    ch = st.current_chapter
    genctx = _dump_genctx(ch)
    c = next((x for x in st.chapters if x.chapter == ch), None)
    nws = len((c.text or "").split()) if c else 0
    rf = _reader_feedback(st, ch)
    _dump_chapter(st, ch, genctx)
    _log_gate(ch, genctx, rf, nws)
    print(f"[gen-done] 회차 {ch} status={status} nchars={len(c.text or '') if c else 0} "
          f"({round(time.monotonic()-t0,1)}s)", flush=True)
    print(f"[gen_context] {json.dumps(genctx, ensure_ascii=False)}", flush=True)
    print(f"[reader_feedback] {json.dumps(rf, ensure_ascii=False)}", flush=True)
    print(f"[usage_total] {json.dumps(st.usage_total)}", flush=True)
    print(f"[dump] {REPORTS / f'dp17_ch{ch}.txt'}", flush=True)
    return 0


def cmd_regen(pid: str, fix: str) -> int:
    _PROMPTS.clear()
    _install_prompt_capture()
    svc, repo = _svc()
    st = repo.get(pid)
    tgt = max((c.chapter for c in st.chapters), default=0)
    print(f"[regen] {pid} 회차 {tgt} 재생성(fix {len(fix)}자)…", flush=True)
    t0 = time.monotonic()
    out = svc.regenerate_last_chapter(pid, fix_instruction=fix)
    if out is None:
        print("[regen] 거부(진행 중 잡/회차 없음)", flush=True)
        return 1
    job, _created = out
    while job.status == "running":
        time.sleep(2.0)
    if job.status == "failed":
        print(f"[regen] 실패: {job.error}", flush=True)
        return 1
    st = repo.get(pid)
    genctx = _dump_genctx(tgt)
    c = next((x for x in st.chapters if x.chapter == tgt), None)
    nws = len((c.text or "").split()) if c else 0
    rf = _reader_feedback(st, tgt)
    _dump_chapter(st, tgt, genctx)
    _log_gate(tgt, genctx, rf, nws)
    print(f"[regen-done] 회차 {tgt} nchars={len(c.text or '') if c else 0} "
          f"({round(time.monotonic()-t0,1)}s)", flush=True)
    print(f"[gen_context] {json.dumps(genctx, ensure_ascii=False)}", flush=True)
    print(f"[reader_feedback] {json.dumps(rf, ensure_ascii=False)}", flush=True)
    print(f"[usage_total] {json.dumps(st.usage_total)}", flush=True)
    print(f"[dump] {REPORTS / f'dp17_ch{tgt}.txt'}", flush=True)
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "gen":
        return cmd_gen(argv[1])
    if cmd == "regen":
        return cmd_regen(argv[1], argv[2])
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
