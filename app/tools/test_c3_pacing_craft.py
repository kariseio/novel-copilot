# -*- coding: utf-8 -*-
"""C-3 페이싱 지시 정련 검증 — craft 긍정전환(pink-elephant 소스차단) + 재탕 결정론 피처. LLM 0콜.

원칙 가드: ① _CRAFT_PROGRESS 에 부정명령·금지 대상 토큰('반복' 류) 재유입 차단(스냅샷성),
② pacing_window 신규 피처는 원시 숫자만(판정 키 없음 — 키셋 스냅샷), ③ 교착어 substring 함정 회피
(라벨은 전체 동일성만·유사도는 문자 2-gram=어간≥2 가드 동일 원리)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from novelcopilot.engine.harness import _CRAFT_PROGRESS
from novelcopilot.engine.pacing import (pacing_window, label_max_run, prose_rehash, event_echo)
from novelcopilot.domain.types import ChapterRecord, ChapterStatus
from novelcopilot.domain.ledger import PromiseLedger


# ---------- ① craft 지시: 부정명령 0 + '전진' directional 보존(프롬프트 텍스트 가드) ----------
def test_craft_progress_positive_only():
    # pink-elephant(B-23): 부정형("~하지 말고")과 금지 대상 토큰 호명('반복·되풀이')이 그 토큰을 프라이밍 → 재유입 차단
    for banned in ("말고", "하지 마", "말라", "금지", "반복", "되풀이"):
        assert banned not in _CRAFT_PROGRESS, f"부정명령/프라이밍 토큰 재유입: {banned!r}"
    # directional 가치 보존: '전진' + 변화 추적축 문장 유지
    assert "전진" in _CRAFT_PROGRESS
    assert "최소 하나" in _CRAFT_PROGRESS and "바뀌어야" in _CRAFT_PROGRESS
    # 프롬프트 말미 append 형태 유지(선행 개행 — 다른 블록과 이어붙어도 안전)
    assert _CRAFT_PROGRESS.startswith("\n\n[전개 압력]")


# ---------- ② label_max_run: '연속 동일' run(전체 라벨 동일성만 — substring 함정 없음) ----------
def test_label_max_run_basics():
    assert label_max_run([]) == 0
    assert label_max_run(["길드", "길드", "던전", "길드"]) == 2
    assert label_max_run(["길드", "길드", "길드"]) == 3
    assert label_max_run(["a", "b", "c"]) == 1


def test_label_max_run_missing_breaks_and_no_substring():
    # 결측(빈 라벨)은 run 을 끊는다(결측≠동일)
    assert label_max_run(["길드", "", "길드"]) == 1
    # 연속 결측도 run 이 아니다 — ""를 일반 라벨로 취급해 ""끼리 run 을 세는 변이 차단(리뷰 LOW-2a)
    assert label_max_run(["a", "", ""]) == 1
    assert label_max_run(["", ""]) == 0
    # 공백만 다른 표기는 동일 취급(정규화), 부분 포함('길드'⊂'길드 지하')은 별개 라벨(오탐 없음)
    assert label_max_run(["길드  지하", "길드 지하"]) == 2
    assert label_max_run(["길드", "길드 지하"]) == 1


# ---------- ② prose_rehash: 인접 회차 본문 16자-gram 재탕률(길이-불변 비율) ----------
def _text(seed: int, n: int = 400) -> str:
    return "".join(chr(0xAC00 + (seed + i * 7) % 8000) for i in range(n))


def test_prose_rehash_bounds():
    a = _text(0)
    assert prose_rehash(a, a) == 1.0                       # 전문 재탕 = 1.0
    assert prose_rehash(_text(9000), a) == 0.0             # 무관 본문 = 0.0
    assert prose_rehash("짧다", a) == 0.0                  # 16자 미만 = 신호 없음(0)
    assert prose_rehash("", a) == 0.0 and prose_rehash(a, "") == 0.0


def test_prose_rehash_partial_monotone():
    a = _text(0)
    fresh = _text(9000, 300)
    small = prose_rehash(fresh + a[100:150], a)            # 50자 뭉치 복붙
    big = prose_rehash(fresh + a[100:300], a)              # 200자 뭉치 복붙
    assert 0.0 < small < big < 1.0                          # 재탕 분량에 단조 증가(비율 신호)


def test_prose_rehash_denominator_is_cur():
    # 분모 방향 고정(리뷰 LOW-2b): 비율의 분모는 cur(최신 회차) — "이번 회차의 몇 %가 재탕인가".
    # cur ⊂ prev 이면 1.0, 뒤집으면(<1.0) 다른 값 — 인자/분모를 뒤집는 변이를 비대칭 픽스처로 차단.
    a = _text(0)
    chunk = a[100:150]                                      # prev 안에 통째로 있는 50자 뭉치
    assert prose_rehash(chunk, a) == 1.0                    # cur 전체가 prev 의 재탕
    flipped = prose_rehash(a, chunk)                        # prev 관점: 400자 중 50자 뭉치만 겹침
    assert abs(flipped - 35 / 385) < 1e-9                   # (50-16+1) / (400-16+1)
    assert flipped < 1.0


# ---------- ② event_echo: 요약 문자 2-gram 자카드('유사 사건' 근사, 교착어 강건) ----------
def test_event_echo_josa_robust():
    # 조사 변이('광민과의'≠'광민이')에도 어간 연쇄('광민'·'계약')가 겹침으로 잡힌다 — 단어 완전일치라면 0
    j = event_echo("광민과의 계약이 맺어졌다", "광민이 계약을 파기했다")
    unrelated = event_echo("광민과의 계약이 맺어졌다", "던전에서 유물이 나타났다")
    assert j > 0.0
    assert j > unrelated
    assert event_echo("같은 사건 요약", "같은 사건 요약") == 1.0


def test_event_echo_stem_guard_and_empty():
    # 1음절은 2-gram 을 만들지 못한다(어간≥2 가드와 동일 원리 — 한 글자 우연 일치 무시)
    assert event_echo("강", "강") == 0.0
    assert event_echo("", "무언가") == 0.0


# ---------- ② pacing_window 통합: 신규 피처 배선 + 원시 숫자만(판정 키 0) ----------
def _ch(n, hook, place, text="", summary="", status=ChapterStatus.FINALIZED):
    return ChapterRecord(chapter=n, status=status, hook_type=hook, place=place,
                         text=text, summary=summary)


def test_pacing_window_rehash_features():
    a1 = _text(0)
    a2 = _text(9000, 300) + a1[100:250]                     # 2화가 1화 뭉치 일부를 재탕
    a3 = _text(20000)                                       # 3화는 무관
    chs = [
        _ch(1, "action", "길드", a1, "레오가 길드에서 광민과의 계약을 맺었다"),
        _ch(2, "action", "길드", a2, "광민이 계약을 파기하고 사라졌다"),
        _ch(3, "reveal", "던전", a3, "던전 심층에서 유물이 발견됐다"),
        _ch(4, "x", "y", status=ChapterStatus.ESCALATED),   # 비FINALIZED 제외(기존 계약 유지)
    ]
    pw = pacing_window(chs, PromiseLedger(), 5, window=5)
    assert pw["window"] == 3
    assert pw["hook_max_run"] == 2 and pw["place_max_run"] == 2   # '연속' run(단조비율과 별개 신호)
    assert len(pw["prose_echo"]) == 2 and len(pw["event_echo"]) == 2   # 인접쌍 수열(오래된→최신)
    assert pw["prose_echo"][0] > 0.0 and pw["prose_echo"][1] == 0.0    # 1↔2 재탕 감지, 2↔3 무관
    # 배선 방향 고정(리뷰 LOW-2b): 분모 = 최신 회차(cur=2화, 450자→435그램), 겹침 = 복붙 뭉치 내부 135그램.
    # (a,b) 인자를 뒤집는 변이면 round(135/385,3)=0.351 이 나와 여기서 깨진다.
    assert pw["prose_echo"][0] == round(135 / 435, 3)
    assert pw["event_echo"][0] > pw["event_echo"][1]                   # 유사 사건(광민·계약) > 무관
    # 판정기 금지 가드: 반환은 원시 신호/집계 키만 — 라벨·판정·자동교정 키 추가 시 여기서 깨진다(의도)
    assert set(pw.keys()) == {"window", "hooks", "hook_monotony", "hook_max_run",
                              "places", "places_distinct", "place_max_run", "times",
                              "prose_echo", "event_echo", "new_names", "since_payoff"}
    assert all(isinstance(v, float) for v in pw["prose_echo"] + pw["event_echo"])


def test_pacing_window_missing_label_breaks_run():
    # run 은 '회차 순서 그대로' 계산 — 가운데 결측이 있으면 연속이 아니다(결측 제거 리스트의 인접성 왜곡 방지)
    chs = [_ch(1, "action", "길드"), _ch(2, "", ""), _ch(3, "action", "길드")]
    pw = pacing_window(chs, PromiseLedger(), 4, window=5)
    assert pw["hook_max_run"] == 1 and pw["place_max_run"] == 1
    assert pw["hooks"] == ["action", "action"]              # 기존 원시 라벨 리스트(결측 제거)는 불변


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
    print(f"C-3 검증: ALL GREEN ({len(fns)} tests)")
