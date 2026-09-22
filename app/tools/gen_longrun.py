# -*- coding: utf-8 -*-
"""LR-0 롱런 러너+가드 — 다작품×N화 롱런 하네스(결정론 가드 + manifest 계측).

기존 러너 패턴(gen_salaryman/gen_genres: 설정→작품 생성→N화 루프) 재사용 + 가드/계측 레이어.

가드(전부 결정론·LLM 0콜·advisory 기록 — 자동 리플래닝/재작성 없음):
 ① arm 시작: spine 검증(아크≥1 AND 엔딩 비지 않음) 위반 → arm 중단+기록
 ② 매 회차 후: 커서 정합(current_chapter == len(chapters)) 불일치 → arm 중단+플래그
 ③ ESCALATED 연속 2회 → 플래그(중단 아님, 루프 계속)
 ④ manifest JSON(작품id·화수·가드 이벤트·경과·계측 파일 경로) → app/tools/reports/
 ⑤ step 예외 1회 재시도(transient 타임아웃 등 흡수·무한 재시도 금지) — 재시도 flag 기록,
   재실패 시 arm 중단+manifest 에 기존 가드 이벤트 형식으로 기록

발사 방지(2중 잠금 — CLI 게이트 + run() 프로그래매틱 잠금):
 --dry-run : LLM 0콜. 임시폴더에 합성 도메인 상태로 가드·루프·manifest 를 검증(가드 셀프테스트 포함).
             라이브 data_dir 미접촉(CopilotService/provider 자체를 생성하지 않음 — 구조적 0콜).
 --live    : 실발사(LLM 실비용 + 웹앱 데이터 폴더 기록). ⓐ--live 명시 플래그 AND
             ⓑTTY 프롬프트 'LAUNCH' 정확 입력 또는 --fire 명시 플래그(스크립트/백그라운드 발사용).
             --fire 없는 비TTY(파이프/CI)는 무조건 차단.
 run()     : mode='live' 는 confirm='FIRE' 명시 인자 없으면 PermissionError(2차 잠금 —
             프로그래매틱 import 경로도 CLI 게이트와 독립으로 잠김). dry-run 경로 무영향.
 무플래그  : 안내 출력 후 종료(기본값 발사 없음).

실행(app 디렉토리에서):
  PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/gen_longrun.py --dry-run --works 2 --chapters 5
"""
from __future__ import annotations
import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

from novelcopilot.domain.project import ProjectSeed, ProjectState
from novelcopilot.domain.world import WorldConfig, EntitySpec
from novelcopilot.domain.narrative import NarrativeSpine, Arc, Episode, EndingSpec
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.repository import FilesystemProjectRepository

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
ESCALATED_STREAK_FLAG = 2      # ③ 연속 ESCALATED 플래그 임계(중단 아님)
LIVE_CONFIRM = "FIRE"          # run(mode='live') 2차 잠금 — 정확 일치 필수(대소문자 구분)


# ---------------------------------------------------------------------------
# 가드(순수·결정론) — dry-run/live 가 '같은 함수'를 탄다(검증 대상 = 실경로)
# ---------------------------------------------------------------------------

def check_spine(state: ProjectState) -> str | None:
    """가드 ①: arm 시작 spine 검증. 위반 상세 문자열 반환(정상=None).
    조건: spine 존재 AND 아크≥1 AND 엔딩 텍스트 비지 않음."""
    spine = state.world.spine
    if spine is None:
        return "spine 없음(평면 beats 모드) — 롱런 arm 은 spine 필수"
    if len(spine.arcs) < 1:
        return "아크 0개(arcs 비어 있음)"
    if spine.ending is None or not (spine.ending.ending or "").strip():
        return "엔딩 비어 있음(EndingSpec.ending 공백)"
    return None


def check_cursor(state: ProjectState) -> str | None:
    """가드 ②: 커서 정합 불변식 current_chapter == len(chapters). 위반 상세 반환(정상=None)."""
    if state.current_chapter != len(state.chapters):
        return (f"커서 불일치 current_chapter={state.current_chapter} "
                f"!= len(chapters)={len(state.chapters)}")
    return None


def guard_event(guard: str, chapter: int, action: str, detail: str) -> dict:
    """가드 이벤트 1건(manifest 직렬화 형). action: 'abort'(arm 중단) | 'flag'(기록만)."""
    return {"guard": guard, "chapter": chapter, "action": action, "detail": detail}


def _rec_status(res: dict) -> str:
    """step 결과에서 회차 status 문자열 추출(ChapterRecord 객체/dict 양쪽 관용)."""
    rec = res.get("record")
    if rec is None:
        return ""
    raw = rec.get("status") if isinstance(rec, dict) else getattr(rec, "status", "")
    return str(getattr(raw, "value", raw) or "")


def _rec_field(res: dict, key: str, default):
    rec = res.get("record")
    if rec is None:
        return default
    if isinstance(rec, dict):
        return rec.get(key, default)
    return getattr(rec, key, default)


# ---------------------------------------------------------------------------
# 하네스 2종 — 러너 코어(run_arm)는 하네스 계약(create/step/load)만 본다
# ---------------------------------------------------------------------------

class DryRunHarness:
    """LLM 0콜 합성 하네스 — 임시폴더 repo 에 '실제 도메인 모델'로 영속(가드가 읽는 계약면 동일).
    faults 로 결함 시나리오 주입(가드 셀프테스트·pytest 용):
      no_arcs: True          → spine.arcs=[]        (가드 ① 발화)
      no_ending: True        → EndingSpec.ending 공백 (가드 ① 발화)
      cursor_break_at: int   → 해당 회차 저장 시 커서 반쪽 커밋 재구성(B-25 회귀 형상, 가드 ② 발화)
      escalate_at: list[int] → 해당 '시도(attempt)'에서 ESCALATED 반환·미영속(B-25 계약, 가드 ③ 재료)
      raise_at: list[int]    → 해당 '시도(attempt)'에서 step 예외 발생(⑤ 재시도 정책 검증용 —
                               attempt 는 step() 호출마다 증가하므로 재시도 콜도 1시도로 센다)
    """

    def __init__(self, seed: ProjectSeed, data_dir: Path | None = None, faults: dict | None = None):
        self.seed = seed
        self.dir = Path(data_dir) if data_dir else Path(tempfile.mkdtemp(prefix="lr0dry_"))
        self.repo = FilesystemProjectRepository(self.dir)
        self.faults = dict(faults or {})
        self._attempt = 0

    def _spine(self, target: int) -> NarrativeSpine:
        arc = Arc(arc_id="arc1", order=1, title="합성 아크", goal="목표", episodes=[
            Episode(episode_id="arc1_ep1", arc_id="arc1", order=1, climax="절정",
                    target_chapters=max(3, target))])
        if self.faults.get("no_arcs"):
            return NarrativeSpine(ending=EndingSpec(ending="주인공이 답을 얻는다"), arcs=[])
        if self.faults.get("no_ending"):
            return NarrativeSpine(ending=EndingSpec(ending="   "), arcs=[arc])
        return NarrativeSpine(ending=EndingSpec(central_question="질문", ending="주인공이 답을 얻는다",
                                                thematic_payoff="보상"), arcs=[arc])

    def create(self) -> tuple[str, ProjectState]:
        target = max(1, int(self.seed.target_chapters or 12))
        world = WorldConfig(title=f"[LR0 dry] {self.seed.title or self.seed.genre}",
                            genre=self.seed.genre,
                            entities=[EntitySpec(id="hero", name="주인공")],
                            spine=self._spine(target))
        pid = f"lr0dry{uuid.uuid4().hex[:8]}"
        st = ProjectState(id=pid, seed=self.seed.model_copy(deep=True), world=world,
                          created_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.repo.save(st)
        return pid, st

    def step(self, pid: str) -> dict:
        self._attempt += 1
        if self._attempt in set(self.faults.get("raise_at") or ()):
            raise RuntimeError(f"dry-run 주입 step 예외(attempt {self._attempt})")
        st = self.repo.get(pid)
        n = st.current_chapter + 1
        if self._attempt in set(self.faults.get("escalate_at") or ()):
            # B-25 계약 재현: 비FINALIZED 는 영속·커서 전진 없음(페이로드만 반환)
            rec = ChapterRecord(chapter=n, status=ChapterStatus.ESCALATED, text="",
                                recovery_hints=[{"kind": "dry_run_injected",
                                                 "diagnosis": "dry-run 주입 ESCALATED"}])
            return {"completed": False, "record": rec, "current_chapter": st.current_chapter}
        rec = ChapterRecord(chapter=n, status=ChapterStatus.FINALIZED,
                            title=f"{n}화", summary=f"{n}화 요약",
                            text=f"[dry-run] {n}화 합성 본문 — LLM 0콜.")
        st.chapters.append(rec)
        st.current_chapter = n
        if self.faults.get("cursor_break_at") == n:
            st.current_chapter = n - 1      # 반쪽 커밋(커서↔회차 불일치) 재구성
        self.repo.save(st)
        return {"completed": False, "record": rec, "current_chapter": st.current_chapter}

    def load(self, pid: str) -> ProjectState:
        return self.repo.get(pid)


class LiveHarness:
    """실발사 하네스 — 웹앱 데이터 폴더(resolved_data_dir)에 실제 작품 생성(gen_genres 패턴).
    LR-1 승인 전 사용 금지: main() 의 --live 플래그+LAUNCH 확인을 통과해야만 생성된다."""

    def __init__(self, seed: ProjectSeed):
        # 지연 import — dry-run 경로가 서비스/프로바이더를 아예 로드하지 않게(구조적 LLM 0콜)
        from novelcopilot.config import get_settings
        from novelcopilot.services import CopilotService
        s = get_settings()
        self.repo = FilesystemProjectRepository(s.resolved_data_dir())
        self.svc = CopilotService(s, self.repo)
        self.seed = seed

    def create(self) -> tuple[str, ProjectState]:
        st, _ = self.svc.create_project(self.seed.model_copy(deep=True))
        return st.id, st

    def step(self, pid: str) -> dict:
        return self.svc.generate_next_chapter(pid)

    def load(self, pid: str) -> ProjectState:
        return self.repo.get(pid)


# ---------------------------------------------------------------------------
# 러너 코어
# ---------------------------------------------------------------------------

def run_arm(harness, arm_no: int, target_chapters: int, log_path: Path) -> dict:
    """arm 1개 = 작품 생성 + 최대 target_chapters 회 step. 가드 ①②③⑤ 적용, jsonl 계측 로그 기록.
    러너는 계측 하네스 — step 예외는 1회 재시도(⑤ transient 흡수) 후에도 실패하면
    arm 중단 기록으로 수렴시켜 런 전체를 죽이지 않는다(무한 재시도 금지)."""
    t0 = time.monotonic()
    events: list[dict] = []
    log_lines: list[dict] = []
    pid, title = "", ""
    baseline = chapters_done = attempts = 0
    aborted, abort_reason, completed_reason = False, "", ""
    try:
        pid, st0 = harness.create()
        title = st0.world.title
        baseline = st0.current_chapter
        # 가드 ① — arm 시작 spine 검증
        detail = check_spine(st0)
        if detail:
            events.append(guard_event("spine", st0.current_chapter, "abort", detail))
            aborted, abort_reason = True, f"spine: {detail}"
        streak = 0
        while not aborted and attempts < target_chapters and chapters_done < target_chapters:
            attempts += 1
            ts = time.monotonic()
            step_events: list[dict] = []
            try:
                res = harness.step(pid)
            except Exception as e:
                # 가드 ⑤ — step 예외 1회 재시도(transient 타임아웃 등 흡수, 무한 재시도 금지).
                ev = guard_event("step_retry", baseline + chapters_done + 1, "flag",
                                 f"step 예외 1회 재시도: {type(e).__name__}: {str(e)[:200]}")
                events.append(ev)
                step_events.append(ev)
                res = harness.step(pid)   # 재실패 → 바깥 except 가 arm 중단+manifest 기록(기존 형식)
            if res.get("completed"):
                completed_reason = str(res.get("reason") or "completed")
                log_lines.append({"arm": arm_no, "attempt": attempts, "chapter": chapters_done,
                                  "status": "COMPLETED", "chars": 0,
                                  "elapsed_sec": round(time.monotonic() - ts, 3),
                                  "guard_events": step_events})
                break
            status = _rec_status(res)
            rec_ch = int(_rec_field(res, "chapter", chapters_done + baseline + 1) or 0)
            # 가드 ③ — ESCALATED 연속 스트릭(플래그만, 중단 아님)
            if status == ChapterStatus.ESCALATED.value:
                streak += 1
                if streak >= ESCALATED_STREAK_FLAG:
                    ev = guard_event("escalated_streak", rec_ch, "flag",
                                     f"ESCALATED 연속 {streak}회(회차 {rec_ch} 미전진)")
                    events.append(ev)
                    step_events.append(ev)
            else:
                streak = 0
            # 가드 ② — 매 회차 후 디스크 권위 재읽기로 커서 정합
            st = harness.load(pid)
            chapters_done = st.current_chapter - baseline
            detail = check_cursor(st)
            if detail:
                ev = guard_event("cursor", st.current_chapter, "abort", detail)
                events.append(ev)
                step_events.append(ev)
                aborted, abort_reason = True, f"cursor: {detail}"
            chars = len(str(_rec_field(res, "text", "") or ""))
            log_lines.append({"arm": arm_no, "attempt": attempts, "chapter": rec_ch,
                              "status": status, "chars": chars,
                              "elapsed_sec": round(time.monotonic() - ts, 3),
                              "guard_events": step_events})
    except Exception as e:
        aborted = True
        abort_reason = f"exception: {type(e).__name__}: {str(e)[:200]}"
        events.append(guard_event("exception", baseline + chapters_done + 1, "abort", abort_reason))
    finally:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as f:
                for line in log_lines:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass   # 계측 로그 실패가 arm 결과 자체를 무효화하진 않음(manifest 에 결과는 남음)
    return {"arm": arm_no, "project_id": pid, "title": title,
            "target_chapters": target_chapters, "chapters_done": chapters_done,
            "attempts": attempts, "aborted": aborted, "abort_reason": abort_reason,
            "completed_early": completed_reason, "guard_events": events,
            "elapsed_sec": round(time.monotonic() - t0, 3), "log_path": str(log_path)}


def _has_event(arm: dict, guard: str, action: str) -> bool:
    return any(e["guard"] == guard and e["action"] == action for e in arm["guard_events"])


def guard_selftest(out_dir: Path, run_id: str) -> dict:
    """--dry-run 전용: 가드 3종이 실제로 발화하는지 결함 주입으로 검증(전부 LLM 0콜).
    결과는 manifest.guard_selftest 로 영속 — dry-run GREEN = 정상 arm 무중단 AND 셀프테스트 전건 pass."""
    seed = ProjectSeed(title="셀프테스트", genre="셀프테스트", target_chapters=8)
    checks = []

    def _arm(name: str, faults: dict, target: int = 4) -> dict:
        h = DryRunHarness(seed, faults=faults)
        return run_arm(h, 0, target, out_dir / f"longrun_{run_id}_selftest_{name}.jsonl")

    a = _arm("spine_no_ending", {"no_ending": True})
    checks.append({"name": "guard1_spine_no_ending_abort",
                   "pass": bool(a["aborted"] and _has_event(a, "spine", "abort")
                                and a["attempts"] == 0 and a["chapters_done"] == 0),
                   "arm": a})
    b = _arm("spine_no_arcs", {"no_arcs": True})
    checks.append({"name": "guard1_spine_no_arcs_abort",
                   "pass": bool(b["aborted"] and _has_event(b, "spine", "abort") and b["attempts"] == 0),
                   "arm": b})
    c = _arm("cursor_break", {"cursor_break_at": 2})
    checks.append({"name": "guard2_cursor_mismatch_abort",
                   "pass": bool(c["aborted"] and _has_event(c, "cursor", "abort") and c["attempts"] == 2),
                   "arm": c})
    d = _arm("escalated_streak", {"escalate_at": [2, 3]})
    checks.append({"name": "guard3_escalated_streak_flag_no_abort",
                   "pass": bool((not d["aborted"]) and _has_event(d, "escalated_streak", "flag")
                                and d["chapters_done"] == 2),
                   "arm": d})
    e = _arm("normal_green", {})
    checks.append({"name": "normal_arm_no_guard_events",
                   "pass": bool((not e["aborted"]) and not e["guard_events"] and e["chapters_done"] == 4),
                   "arm": e})
    return {"pass": all(ch["pass"] for ch in checks), "checks": checks}


def run(*, mode: str, seed: ProjectSeed, works: int, chapters: int,
        out_dir: Path | None = None, harness_factory=None, selftest: bool = True,
        confirm: str = "") -> dict:
    """롱런 실행 — 작품수(works)만큼 arm 반복, manifest JSON 기록 후 반환.
    harness_factory(arm_no)->harness 주입 가능(테스트용). 기본: dry-run=DryRunHarness / live=LiveHarness.
    2차 잠금: mode='live' 는 confirm=LIVE_CONFIRM('FIRE') 정확 일치 필수 — CLI 게이트를 우회하는
    프로그래매틱 import 경로도 명시 승인 없이는 발사 불가. dry-run 은 confirm 무영향."""
    assert mode in ("dry-run", "live"), f"unknown mode: {mode}"
    if mode == "live" and confirm != LIVE_CONFIRM:
        raise PermissionError(
            "run(mode='live') 는 confirm='FIRE' 명시 인자가 필요합니다"
            "(실발사 2차 잠금 — CLI --live 게이트와 독립). 검증은 mode='dry-run'.")
    out = Path(out_dir) if out_dir else REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    t0 = time.monotonic()
    if harness_factory is None:
        if mode == "dry-run":
            harness_factory = lambda i: DryRunHarness(seed)                # arm 마다 새 임시 repo
        else:
            _live = LiveHarness(seed)                                      # svc 재사용(세션·설정 1회)
            harness_factory = lambda i: _live
    arms = []
    for i in range(1, max(1, works) + 1):
        log_path = out / f"longrun_{run_id}_arm{i}.jsonl"
        arm = run_arm(harness_factory(i), i, chapters, log_path)
        arms.append(arm)
        flag_n = sum(1 for e in arm["guard_events"] if e["action"] == "flag")
        print(f"  arm{i} [{arm['title']}] {arm['chapters_done']}/{chapters}화 "
              f"{'중단(' + arm['abort_reason'][:80] + ')' if arm['aborted'] else '정상'} "
              f"플래그{flag_n} {arm['elapsed_sec']}s", flush=True)
    st_result = guard_selftest(out, run_id) if (mode == "dry-run" and selftest) else None
    manifest_path = out / f"longrun_{run_id}_manifest.json"
    manifest = {
        "ticket": "LR-0", "run_id": run_id, "mode": mode,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {"seed_title": seed.title, "genre": seed.genre,
                   "target_chapters": seed.target_chapters, "works": works, "chapters": chapters},
        "arms": arms,
        "guard_selftest": st_result,
        "green": (all(not a["aborted"] for a in arms)
                  and (st_result is None or bool(st_result["pass"]))),
        "elapsed_sec": round(time.monotonic() - t0, 3),
        "manifest_path": str(manifest_path),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


# ---------------------------------------------------------------------------
# CLI — 발사 방지 게이트 포함
# ---------------------------------------------------------------------------

def _stdin_isatty() -> bool:
    """테스트가 스텁할 수 있게 분리(비TTY=발사 차단)."""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def _confirm_launch(works: int, chapters: int, seed: ProjectSeed) -> bool:
    """실발사 2중 게이트 ⓑ: TTY 에서 'LAUNCH' 정확 입력. 비TTY(스크립트/CI/파이프)는 무조건 거부."""
    if not _stdin_isatty():
        print("[차단] --live 는 TTY 확인 프롬프트가 필요합니다(비대화형 실발사 금지 — LR-1 승인 필요).")
        return False
    print("=" * 60)
    print("경고: 실발사(LLM 실비용 발생 + 웹앱 라이브 데이터 폴더에 작품 기록)")
    print(f"  작품수={works} × 목표회차={chapters} / 장르='{seed.genre}'")
    print("  LR-0 범위는 dry-run 검증까지 — 실발사는 LR-1 별도 승인 사항입니다.")
    print("=" * 60)
    try:
        ans = input("실발사를 승인하려면 LAUNCH 를 정확히 입력(그 외 취소): ")
    except (EOFError, KeyboardInterrupt):
        # Windows NUL 리다이렉트는 isatty()=True 로 보고되는 문자 디바이스 — EOF 도 '거부'로 수렴(발사 방지 우선)
        return False
    return ans.strip() == "LAUNCH"


def build_seed(args) -> ProjectSeed:
    """설정(시드/장르/목표회차) 조립 — --seed-idx 는 gen_genres 와 같은 GENRES 시드 재사용(지연 import)."""
    if args.seed_idx is not None:
        try:
            from tools.ab_genres import GENRES
        except ImportError:
            from ab_genres import GENRES   # app/tools 를 sys.path 에 넣고 실행한 경우
        seed = GENRES[args.seed_idx].model_copy(deep=True)
    else:
        seed = ProjectSeed(title=args.title, genre=args.genre, tone=args.tone,
                           premise=args.premise, protagonist_hint=args.protagonist,
                           target_chapters=args.target_chapters or max(12, args.chapters))
    if args.target_chapters:
        seed.target_chapters = args.target_chapters
    if args.tone:
        seed.tone = args.tone
    return seed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="LR-0 롱런 러너 — 가드(결정론)+manifest. 기본값 발사 없음(--dry-run 또는 --live 명시).")
    p.add_argument("--dry-run", action="store_true", help="LLM 0콜 — 가드·manifest 검증(임시폴더)")
    p.add_argument("--live", action="store_true", help="실발사(LR-1 승인 필요 — TTY 에서 LAUNCH 확인 입력)")
    p.add_argument("--fire", action="store_true",
                   help="--live 와 함께만 유효: TTY 프롬프트 생략 발사(승인된 스크립트/백그라운드 발사용 2중 명시 플래그)")
    p.add_argument("--works", type=int, default=1, help="작품수(arm 수)")
    p.add_argument("--chapters", type=int, default=5, help="arm 당 목표 회차(step 예산)")
    p.add_argument("--genre", default="현대 판타지", help="장르(시드)")
    p.add_argument("--tone", default="", help="톤(시드 — --seed-idx 사용 시에도 지정하면 덮어씀)")
    p.add_argument("--title", default="", help="제목(비우면 worldgen/합성이 정함)")
    p.add_argument("--premise", default="", help="한 줄 전제")
    p.add_argument("--protagonist", default="", help="주인공 힌트")
    p.add_argument("--target-chapters", type=int, default=None, help="작품 목표 총 회차(seed.target_chapters)")
    p.add_argument("--seed-idx", type=int, default=None, help="tools/ab_genres.GENRES 인덱스로 시드 선택")
    p.add_argument("--out-dir", default=None, help="manifest/로그 출력 폴더(기본 tools/reports)")
    args = p.parse_args(argv)

    if args.dry_run and args.live:
        print("[오류] --dry-run 과 --live 는 동시 지정 불가.")
        return 2
    if args.fire and not args.live:
        print("[오류] --fire 는 --live 와 함께만 사용(실발사 스크립트 게이트 — 단독/dry-run 조합 무효).")
        return 2
    if not args.dry_run and not args.live:
        p.print_usage()
        print("모드 미지정 — 발사 방지 기본값. 검증은 --dry-run, 실발사(LR-1 승인 후)는 --live.")
        return 2

    seed = build_seed(args)
    out_dir = Path(args.out_dir) if args.out_dir else None

    if args.live:
        # 발사 방지 2중 게이트: 명시 플래그(--live) + (TTY 'LAUNCH' 확인 또는 --fire 명시 플래그).
        # 어느 쪽이든 통과 전 LiveHarness 미생성. run() 자체도 confirm='FIRE' 2차 잠금.
        if args.fire:
            print("=" * 60)
            print("경고: 실발사(--live --fire 2중 명시 플래그 — TTY 프롬프트 생략)")
            print(f"  작품수={args.works} × 목표회차={args.chapters} / 장르='{seed.genre}'")
            print("=" * 60, flush=True)
        elif not _confirm_launch(args.works, args.chapters, seed):
            print("실발사 취소됨(가드·manifest 검증은 --dry-run 사용).")
            return 3
        mode = "live"
    else:
        mode = "dry-run"

    print(f"LR-0 롱런 시작 mode={mode} works={args.works} chapters={args.chapters} genre='{seed.genre}'",
          flush=True)
    m = run(mode=mode, seed=seed, works=args.works, chapters=args.chapters, out_dir=out_dir,
            confirm=(LIVE_CONFIRM if mode == "live" else ""))
    st = m.get("guard_selftest")
    if st is not None:
        for ch in st["checks"]:
            print(f"  selftest {ch['name']}: {'OK' if ch['pass'] else 'FAIL'}", flush=True)
    print(f"manifest: {m['manifest_path']}")
    print(f"결과: {'GREEN' if m['green'] else 'NOT GREEN'} (arms={len(m['arms'])}, {m['elapsed_sec']}s)")
    return 0 if m["green"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows cp949 콘솔 보호
    except Exception:
        pass
    sys.exit(main())
