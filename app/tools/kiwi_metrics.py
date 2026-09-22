# -*- coding: utf-8 -*-
"""ST-10 Kiwi 형태소 정밀 계측 — 정규식 근사의 소스 교체(설계 docs/design-st10-kiwi.md §2).

정규식 문장 분리·종결 substring 의 3중 한계(①인용문 내부 마침표를 문장 경계로 오인 — ch8 절단
②연결형 어미 뒤 종결을 tail 로 미인식 — ST-11c MED-1 누수 ③스팬 경계가 실문장 경계와 어긋남 — ch12 계보)를
Kiwi(kiwipiepy) 문장 분리기(인용 인식)와 세종 태그셋(EP/EF 분리 — '했다'→하+었(EP)+다(EF))으로 정밀화한다.

**의존성 격리(설계 §2 핵심 결정)**: kiwipiepy 는 tools/ 전용이다. novelcopilot/(엔진)은 의존성0 불변 —
이 모듈은 어떤 엔진 코드도 import 하지 않으며(폴백 정규식만 자체 보유), 엔진 내 계측(ai_tell_profile 등)에
접촉하지 않는다. 미설치/로드 실패 시 **기존 정규식 경로로 우아하게 강등**(러너 사망 금지, 강등 사실은 로그 1줄).
Kiwi 인스턴스는 모듈 레벨 lazy 싱글턴(로딩 ~수 초 1회).

**무강제**: 산출은 전부 원자료다 — 판정기·임계·이진 판정·자동 재작성 트리거 0. 작가/실험 게이트가 추세로만 해석.

제공(설계 §2 ①):
  split_sents(text)     — 인용문 인식 문장 분리(오프셋 보존). 강등 시 기존 정규식과 **바이트 동일**.
  ending_profile(text)  — 문말 EP+EF 열(run-length·압축률·템플릿률, 조사 §3 방법론). 강등 시 EF 근사.
  da_streak_kiwi(text)  — 형태소 기반 정밀 '~다 종결 run'(과거 EP+평서 EF). 강등 시 정규식 종성ㅆ 근사.

강등 경로가 기존 정규식과 바이트 동일해야 하는 이유: extract_rhythm_spans(st11)·_prose_sentences(quality_gates)와
동형인 문장열을 내야 DP-4b/T2 캘리브레이션 연속성이 보존된다(설계 §2 하위호환·검증 '기존 정규식 축 값 불변').
"""
from __future__ import annotations

import re
import sys
import threading

# ─────────────────────────────────────────────────────────────────────────────
# 강등(폴백) 정규식 — quality_gates._prose_sentences / st11.prose_sentences_with_offsets 와 **동일 규칙**.
#   이 모듈은 엔진(novelcopilot)을 import 하지 않으므로 규칙을 자체 복제한다(의존성 격리 §2). 규칙이
#   어긋나면 강등 경로의 문장열이 기존 정규식 축과 달라져 캘리브레이션이 깨진다 — 아래 상수/분할은
#   quality_gates._prose_sentences(대사행 제외·문장부호 분할·len>=2) 와 1:1 동형을 보장한다.
# ─────────────────────────────────────────────────────────────────────────────
_DLG_PREFIX = ('"', '“', '”', '—', '-')     # 따옴표/대시/하이픈 시작 = 대사행
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")            # 문장부호 뒤 공백 = 문장 경계(정규식 근사)
_TRAIL = re.compile(r'[\s.!?…"“”\'’·—\-)\]]+$')   # 말미 구두점/따옴표 제거


# ─────────────────────────────────────────────────────────────────────────────
# Kiwi lazy 싱글턴 — 로딩(~수 초)은 최초 1회. 미설치/로드 실패는 폴백 강등(사망 금지, 로그 1줄).
#   _STATE: None=미시도 / "ok"=로드됨 / "fallback"=강등확정. 강등 로그는 프로세스당 1회만.
# ─────────────────────────────────────────────────────────────────────────────
_kiwi = None
_state = None            # None | "ok" | "fallback"
_lock = threading.Lock()
_logged_fallback = False


def _log_fallback(reason: str) -> None:
    global _logged_fallback
    if not _logged_fallback:
        print(f"[kiwi_metrics] Kiwi 미사용 — 정규식 폴백으로 강등: {reason}", file=sys.stderr, flush=True)
        _logged_fallback = True


def _get_kiwi():
    """Kiwi 싱글턴을 돌려주거나(로드 성공) None(강등). thread-safe·1회 로드·강등 로그 1회."""
    global _kiwi, _state
    if _state is not None:
        return _kiwi
    with _lock:
        if _state is not None:      # 락 경합 사이 타 스레드가 확정했을 수 있음
            return _kiwi
        try:
            from kiwipiepy import Kiwi   # tools 전용 개발 의존성(엔진 아님)
            _kiwi = Kiwi()
            _state = "ok"
        except Exception as e:          # 미설치(ImportError)·모델 로드 실패 등 — 전부 강등(사망 금지)
            _kiwi = None
            _state = "fallback"
            _log_fallback(f"{type(e).__name__}: {e}")
    return _kiwi


def kiwi_available() -> bool:
    """Kiwi 로드 성공 여부(계측 산출의 backend 표기·테스트 분기용). 최초 호출 시 로드 시도."""
    return _get_kiwi() is not None


def reset_for_test(force_state: str | None = None) -> None:
    """테스트 전용 — 싱글턴/강등 로그 상태 초기화. force_state='fallback' 이면 강등 경로를 강제
    (설치 환경에서도 폴백 바이트 동일성 검증). None 이면 다음 호출이 실제 로드를 재시도한다.

    force_state='ok' 은 상태를 미시도로 되돌린 뒤 **락 밖에서** _get_kiwi() 로 실제 로드를 강제한다
    (미설치면 fallback 으로 귀결). 로드를 락 안에서 부르면 비재진입 _lock 을 재획득해 자기
    데드락이 되므로(P-7), 상태 초기화만 락 안에서 하고 로드는 락 해제 후에 트리거한다."""
    global _kiwi, _state, _logged_fallback
    with _lock:
        _logged_fallback = False
        if force_state == "fallback":
            _kiwi, _state = None, "fallback"
        else:
            # 'ok' 와 None 모두 상태를 미시도로 리셋(다음 로드 시도가 재시도).
            _kiwi, _state = None, None
    if force_state == "ok":
        # 실제 로드 강제(미설치면 fallback 로 귀결) — 락 밖에서 호출(재진입 데드락 회피, P-7).
        _get_kiwi()


# ─────────────────────────────────────────────────────────────────────────────
# 폴백 분할(정규식) — quality_gates._prose_sentences / st11.prose_sentences_with_offsets 동형(오프셋 보존).
#   반환 (문장, char_start, char_end). text[start:end] == 문장. 대사행 제외·len>=2 필터 동일.
# ─────────────────────────────────────────────────────────────────────────────
def _split_sents_regex(text: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    line_start = 0
    for raw in (text or "").splitlines(keepends=True):
        stripped = raw.strip()
        if stripped and not stripped.startswith(_DLG_PREFIX):
            lead = len(raw) - len(raw.lstrip())
            body = raw.strip()
            seg_start = 0
            for mb in _SENT_SPLIT.finditer(body):
                seg = body[seg_start:mb.start()]
                s = seg.strip()
                if len(s) >= 2:
                    off = line_start + lead + seg_start + (len(seg) - len(seg.lstrip()))
                    out.append((s, off, off + len(s)))
                seg_start = mb.end()
            seg = body[seg_start:]
            s = seg.strip()
            if len(s) >= 2:
                off = line_start + lead + seg_start + (len(seg) - len(seg.lstrip()))
                out.append((s, off, off + len(s)))
        line_start += len(raw)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# ① split_sents — 인용문 인식 문장 분리(오프셋 보존). Kiwi 로드 시 정밀 경계, 강등 시 정규식 바이트 동일.
#
#   Kiwi 경로: 대사행(따옴표/대시 시작 줄)을 정규식과 동일 규칙으로 제외한 '지문' 줄들에 대해서만
#   Kiwi 문장 분리를 돌린다(대사 원형·화자 목소리 불변 — st11 의 지문 필터와 동형). Kiwi 의 top-level
#   Sentence 는 인용문 내부 마침표를 경계로 삼지 않으므로(ch8 계보 수리), 문단 중간 인용 절단이 사라진다.
#   len>=2 필터·오프셋 슬라이스 정합은 정규식 경로와 동일 계약을 유지한다.
# ─────────────────────────────────────────────────────────────────────────────
def split_sents(text: str) -> list[tuple[str, int, int]]:
    """지문 문장 분리 → [(문장, char_start, char_end)]. text[start:end]==문장.

    Kiwi 로드 시: 인용문 내부 마침표를 문장 경계로 오인하지 않는다(ch8 계보 — 문단 중간 절단 소스 수리).
    강등 시: 기존 정규식(_prose_sentences 동형)과 **바이트 동일**(캘리브레이션 연속성).
    대사행(따옴표/대시/하이픈 시작 줄)은 두 경로 모두 제외(지문 문체 신호만 — st11 지문 필터 동형)."""
    k = _get_kiwi()
    if k is None:
        return _split_sents_regex(text)
    out: list[tuple[str, int, int]] = []
    line_start = 0
    for raw in (text or "").splitlines(keepends=True):
        stripped = raw.strip()
        if stripped and not stripped.startswith(_DLG_PREFIX):
            lead = len(raw) - len(raw.lstrip())
            body = raw.strip()
            try:
                sents = k.split_into_sents(body)
            except Exception as e:
                # 런타임 분석 예외 — 이 줄만 정규식 근사로(전체 강등 아님), 로그 1회
                _log_fallback(f"split_into_sents 예외 {type(e).__name__}: {e}")
                for s, a, b in _split_sents_regex(raw):
                    out.append((s, a, b))
                line_start += len(raw)
                continue
            for sent in sents:
                # Kiwi Sentence.text 는 body[sent.start:sent.end] 와 동일 — 원문 오프셋으로 환산
                s = (sent.text or "").strip()
                if len(s) < 2:
                    continue
                seg = body[sent.start:sent.end]
                inner_lead = len(seg) - len(seg.lstrip())
                off = line_start + lead + sent.start + inner_lead
                out.append((s, off, off + len(s)))
        line_start += len(raw)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 문말 종결 태그 열 추출(Kiwi EP+EF) — 조사 §3 방법론(EMNLP24/CHI25/ACL25 계열의 압축률·run-length·템플릿률).
#   각 지문 문장의 마지막 EP*+EF 형태소 열('었다'·'ㄴ다'·'았다'·'다')을 종결 템플릿 키로 뽑는다.
#   ST-11c MED-1 수리: 연결형 어미(EC, '-고/-며/-지') 뒤에 오는 진짜 종결(EF)을 EF 태그로 인식 —
#   정규식 종결 substring 이 연결형 뒤 종결을 tail 로 놓치던 누수를 형태소 태그로 닫는다.
# ─────────────────────────────────────────────────────────────────────────────
def _ending_key_kiwi(sent_text: str, kiwi) -> str | None:
    """한 문장의 문말 종결 템플릿 키(EP*+EF 형태소 열). EF 없으면 None(파편/체언종결 — 종결 열 아님)."""
    try:
        toks = kiwi.tokenize(sent_text)
    except Exception:
        return None
    # 뒤에서부터: 마지막 EF 를 찾고, 그 앞에 붙은 연속 EP(선어말어미) 열을 함께 묶는다.
    last_ef = None
    for i in range(len(toks) - 1, -1, -1):
        if toks[i].tag == "EF":
            last_ef = i
            break
    if last_ef is None:
        return None
    parts = [toks[last_ef].form]
    j = last_ef - 1
    while j >= 0 and toks[j].tag == "EP":     # 선어말어미(과거 었/았/였·미래 겠 등) 연속 흡수
        parts.insert(0, toks[j].form)
        j -= 1
    return "".join(parts)


def _ending_key_regex(sent_text: str) -> str | None:
    """강등 근사 — 종결 '다' 앞 1~2음절을 종결 키로(EP+EF 정밀 분해 불가, 거친 근사)."""
    c = _TRAIL.sub("", sent_text)
    if len(c) < 2 or not c.endswith("다"):
        return None
    # '었다/았다/였다/ㄴ다/는다' 등 말미 최대 2음절을 키로(형태소 아님 — 근사)
    return c[-2:]


def ending_profile(text: str) -> dict:
    """문말 종결 템플릿(EP+EF 열) 분포 — 압축률·최장 run·템플릿률(원자료·advisory, 판정 아님).

    반환:
      n_ending      : 종결(EF 보유) 지문 문장 수 — 압축률/템플릿률의 분모
      unique        : 서로 다른 종결 키 수
      compression   : unique/n_ending (낮을수록 종결 템플릿이 소수로 압축=단조. 조사 §3 압축률)
      max_run       : 동일 종결 키 최장 연속 run(문장열 순서 — 종결 단일성 벽)
      top_template  : 최빈 종결 키
      top_ratio     : 최빈 키 비율(템플릿률 — 한 종결형이 회차를 얼마나 지배하나)
      keys          : 종결 키 열(디버그·advisory)
      backend       : "kiwi" | "regex"(강등)
    무강제: 어떤 값도 임계·판정에 쓰지 않는다. 작가/게이트가 추세로만 해석."""
    k = _get_kiwi()
    sents = [s for (s, _a, _b) in split_sents(text)]
    keys: list[str] = []
    if k is not None:
        for s in sents:
            key = _ending_key_kiwi(s, k)
            if key:
                keys.append(key)
        backend = "kiwi"
    else:
        for s in sents:
            key = _ending_key_regex(s)
            if key:
                keys.append(key)
        backend = "regex"
    n = len(keys)
    if n == 0:
        return {"n_ending": 0, "unique": 0, "compression": None, "max_run": 0,
                "top_template": None, "top_ratio": None, "keys": [], "backend": backend}
    uniq = len(set(keys))
    # 최장 동일 종결 run
    max_run = cur = 1
    for i in range(1, n):
        cur = cur + 1 if keys[i] == keys[i - 1] else 1
        if cur > max_run:
            max_run = cur
    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    top_template, top_n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return {"n_ending": n, "unique": uniq,
            "compression": round(uniq / n, 3), "max_run": max_run,
            "top_template": top_template, "top_ratio": round(top_n / n, 3),
            "keys": keys, "backend": backend}


# ─────────────────────────────────────────────────────────────────────────────
# ③ da_streak_kiwi — 형태소 기반 '~다 종결 run'(과거 EP + 평서 EF '다'). quality_gates.past_tense_run 정밀판.
#   정규식판은 종결 '다' 앞 음절 종성이 ㅆ 인가(았/었/였/했)로 근사 → 존재사 '있다'(종성ㅆ·현재) 오계수.
#   Kiwi 판은 EP(과거 었/았/였) + EF('다') 태그로 판별 → 존재사·형용사 현재 '다'를 정확히 배제(연결형 뒤
#   종결도 EF 로 인식 — ST-11c MED-1 계보). 강등 시 정규식 종성ㅆ 근사로 폴백(값 동일 계보).
# ─────────────────────────────────────────────────────────────────────────────
_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3


def _has_ss_jong(ch: str) -> bool:
    """음절 종성이 ㅆ(쌍시옷)인가 — 정규식 폴백판 과거 판별(quality_gates._has_ss_jong 동형)."""
    if not ch:
        return False
    o = ord(ch)
    return _HANGUL_BASE <= o <= _HANGUL_LAST and (o - _HANGUL_BASE) % 28 == 20


def _da_flag_regex(sent: str) -> bool:
    c = _TRAIL.sub("", sent)
    return len(c) >= 2 and c.endswith("다") and _has_ss_jong(c[-2])


def _da_flag_kiwi(sent: str, kiwi) -> bool:
    """과거 종결 판별 — 마지막 EF 가 '다'(평서 해라체)이고 그 앞에 과거 EP(었/았/였)가 붙는가."""
    try:
        toks = kiwi.tokenize(sent)
    except Exception:
        return _da_flag_regex(sent)
    last_ef = None
    for i in range(len(toks) - 1, -1, -1):
        if toks[i].tag == "EF":
            last_ef = i
            break
    if last_ef is None or toks[last_ef].form != "다":   # '다'
        return False
    j = last_ef - 1
    while j >= 0 and toks[j].tag == "EP":
        if toks[j].form in ("었", "았", "였"):  # 었/았/였 (과거 선어말어미)
            return True
        j -= 1
    return False


# ─────────────────────────────────────────────────────────────────────────────
# ST-14 FIX-1: 무중단 동일 종결 키 run — '과거형 전용' past_run 을 형태 불문 축으로 일반화한 공용 함수.
#   ending_profile 이 이미 종결 키 열(EP*+EF, Kiwi 부재 시 정규식 강등)과 최장 run 을 낸다. 이 함수는 그
#   위에 (a) *어느 위치의* run 인지(문자 오프셋) (b) **대사 리셋**(두 지문 문장 사이 원문에 대사행이 끼면
#   run 분절 — 지문 리듬 벽만 세고 대사가 끊어주는 호흡은 벽이 아님)을 얹어 [{key, n_sent, char_start, char_end}]
#   목록으로 낸다. 과거형('…ㅆ다')뿐 아니라 현재형('ㄴ다')·명사문 등 *어떤* 종결이든 같은 키가 무중단 연속하면
#   run 으로 잡는다(현재형 'ㄴ다' 벽 검출 — ST-14 배경 R2 수리). 무강제·advisory(임계·판정 0).
#
#   FIX-2(재실현 채택 국소 축)·FIX-5(검증 SSOT ending_runs)가 이 함수를 재사용한다(중복 구현 금지).
# ─────────────────────────────────────────────────────────────────────────────
def uninterrupted_ending_runs(text: str, threshold: int = 6) -> list[dict]:
    """무중단 동일 종결 키 run(threshold 이상) 목록 — 지문 문장열의 종결 키가 연속 동일하되 사이에 대사행이
    끼지 않은 구간. 반환 [{key, n_sent, char_start, char_end}](원문 순서·char_start 오름차순).

    · 종결 키 = _ending_key_kiwi(EP*+EF 열, Kiwi 부재 시 _ending_key_regex 강등 — 기존 강등 계약 유지).
    · **파편은 건너뛴다(끊지 않음 — ST-14 라이브 보정)**: 종결 키가 None(파편/체언 종결)인 문장은 run 에
      *세지 않되 run 을 끊지도 않는다*. 근거=사용자 정독 실측: 파편이 사이에 껴도 동일 어미 망치질의 체감은
      계속된다(판타지 ch1 클라이맥스 — 파편 개입 12연속을 벽으로 정독). 파편-리셋 의미는 그 벽을 5로 낮게
      재서 검출·수리가 발화하지 않았다(휴머나이즈 무발화 실측). 지각 축이 ground truth.
    · **대사 리셋**: 키 보유 문장 N 의 끝과 다음 키 보유 문장의 시작 사이 원문에 대사행(_DLG_PREFIX 시작 줄)이
      있으면 두 문장은 같은 run 에 들지 않는다(대사가 끊어주는 호흡 = 지문 벽 아님). 지문 문장 좌표는
      split_sents 오프셋으로, 대사행 char 범위는 원문 라인 스캔으로 결정론 산출한다.
    n_sent = run 에 든 *키 보유* 문장 수(사이 파편은 미포함). char_start/char_end 는 첫/끝 키 보유 문장 좌표
    (사이 파편 포함 구간 — 재작성 창으로 그대로 쓸 수 있게).
    무강제: 값(위치·길이)만 — 임계·판정·자동교정 0."""
    k = _get_kiwi()
    sents = split_sents(text)   # [(문장, char_start, char_end)] — 지문만(대사행 제외)
    # 각 지문 문장의 종결 키(None=파편/체언 — 건너뜀)
    keys: list[str | None] = []
    for (s, _a, _b) in sents:
        keys.append(_ending_key_kiwi(s, k) if k is not None else _ending_key_regex(s))
    # 대사행 char 범위(원문 라인 스캔·결정론) — 두 키 보유 문장 사이에 대사행이 끼면 run 리셋.
    dlg_ranges = _dialogue_line_ranges(text)

    def _dlg_between(prev_end: int, next_start: int) -> bool:
        return any(prev_end <= ds and de <= next_start for (ds, de) in dlg_ranges)

    # 문장 역할 분류: 키 보유(run 요소) / 인용 시작(리셋 장벽 — 인라인 대사도 체감상 벽을 끊는다.
    #   대사행은 split_sents 가 이미 제외하지만, 한 줄 안 인용문은 여기로 온다) / 그 외 파편(skip).
    _QUOTE_HEADS = ('"', '“', '「', '『', "'", '‘')

    def _role(i: int) -> str:
        if keys[i] is not None:
            return "key"
        return "reset" if sents[i][0].lstrip().startswith(_QUOTE_HEADS) else "skip"

    runs: list[dict] = []
    cur_key = None
    cur: list[int] = []   # 현재 run 의 키 보유 문장 인덱스

    def _close():
        nonlocal cur, cur_key
        if cur_key is not None and len(cur) >= threshold:
            runs.append({"key": cur_key, "n_sent": len(cur),
                         "char_start": sents[cur[0]][1], "char_end": sents[cur[-1]][2]})
        cur, cur_key = [], None

    for i in range(len(sents)):
        role = _role(i)
        if role == "skip":
            continue                                   # 파편 — 세지 않되 끊지도 않음
        if role == "reset":
            _close()                                   # 인라인 인용 — 대사 장벽(run 종료)
            continue
        if cur and (keys[i] != cur_key or _dlg_between(sents[cur[-1]][2], sents[i][1])):
            _close()                                   # 키 변경 또는 대사행 개입 — run 종료
        cur_key = keys[i]
        cur.append(i)
    _close()
    return runs


def _dialogue_line_ranges(text: str) -> list[tuple[int, int]]:
    """원문에서 대사행(따옴표/대시/하이픈 시작 줄)의 (char_start, char_end) 목록(strip 후 시작 문자 판정).
    split_sents 의 지문 제외 규칙(_DLG_PREFIX)과 동일 기준 — 대사 리셋 판정 전용(결정론)."""
    out: list[tuple[int, int]] = []
    line_start = 0
    for raw in (text or "").splitlines(keepends=True):
        stripped = raw.strip()
        if stripped and stripped.startswith(_DLG_PREFIX):
            lead = len(raw) - len(raw.lstrip())
            out.append((line_start + lead, line_start + lead + len(stripped)))
        line_start += len(raw)
    return out


def da_streak_kiwi(text: str) -> dict:
    """'~다(과거) 종결' 문장 최장 연속 run·비율 — 지문만. Kiwi EP+EF 판별(강등 시 정규식 종성ㅆ 근사).

    반환 {max_run, n_past, n_sent, ratio, backend}. quality_gates.past_tense_run 과 같은 축의 정밀판 —
    dp4b 계측에 additive 병기용(기존 past_tense_run 축은 보존). 무강제·advisory."""
    k = _get_kiwi()
    sents = [s for (s, _a, _b) in split_sents(text)]
    if k is not None:
        flags = [_da_flag_kiwi(s, k) for s in sents]
        backend = "kiwi"
    else:
        flags = [_da_flag_regex(s) for s in sents]
        backend = "regex"
    n = len(flags)
    max_run = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        if cur > max_run:
            max_run = cur
    n_past = sum(flags)
    return {"max_run": max_run, "n_past": n_past, "n_sent": n,
            "ratio": round(n_past / n, 3) if n else 0.0, "backend": backend}
