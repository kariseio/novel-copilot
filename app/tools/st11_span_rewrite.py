# -*- coding: utf-8 -*-
"""ST-11 검출기-피드백 스팬 재작성 — '~다 종결 벽'·파편문 클러스터의 국소 리듬 퇴고.

근거: docs/research-ending-monotony-2026-07.md 처방 랭킹 1위(검출기 피드백 주입형 2패스 —
스팬 단위 대체 재작성, 전면 재생성 아님). 우리 결정론 검출기(past_tense_run·fragment_ratio,
quality_gates)가 요구되는 verifier 입력이고, "결정론 verifier 를 둔 generate→verify→rewrite 루프의
산문 리듬 실효"는 미측정 가설 — 이 도구가 실측한다.

**엔진 무강제**: 이 모듈은 실험 도구다. 검출은 quality_gates 재사용(측정 피처 계보), 재작성 콜은
기존 revise 섀시(harness.ChapterGenerator.revise_prose — 사실 불변 가드)를 그대로 태운다. 엔진 상시
자동 패스는 만들지 않는다(제품 경로는 opt-in '리듬 퇴고' 스킬 = builtin·기본 OFF).

두 모드(mode 인자 — ST-11c 이후 rewrite 기본 v2):
  · **v1(span)** — 재작성 창 = 검출 스팬 그 자체. 문장 경계 보존, 종결어미만 다양화. 한계: 하나의
    연속 체험이 여러 스타카토 단문으로 남는다("숨을 들이쉬려 했다. 폐가 움직이지 않는다. 시야가 좁아진다.…").
    ST-11c 에서는 v2 실패 시 **폴백**(문장 경계 보존이라 내용 소실이 구조적으로 불가).
  · **v2(paragraph)** — ST-11b. 재작성 창 = 검출 스팬을 *포함하는 문단(들) 전체*(빈 줄 경계로 확장),
    앞뒤 문단은 read-only 앵커. 문장 경계 재구성 전면 허용 — 하나의 연속 체험이면 연결어미·종속절로
    엮어 한 문장으로 흘리되, 극적 순간의 단문 펀치 1~2개는 남긴다. 하나의 비트 = 하나의 문장이 담을 수 있다.
    사건·사실·정보·화자 목소리는 그대로.

**ST-11c 프로즈 표면 커버리지 가드(S~M)**: v2 는 문장 경계를 재구성하다 *캐논 클레임이 아닌 자유 감각
  묘사*("시야가 좁아졌다")를 통째로 떨어뜨릴 수 있다(ST-11b CONFIRMED 리스크). G-B 캐논 가드는 클레임 표면만
  검사해 이 소실에 구조적으로 눈이 멀다. 커버리지 가드는 원문 창 각 문장의 *명사(내용어) 어간*(T2 _stem
  재사용·서술어 *종결* 활용형 제외)이 재작성 창 전체에 substring 으로 남아 있나를 결정론으로 검사한다(창 대 창 —
  문장 결합·재배치는 통과). 소실 후보(문장 명사 어간 과반 미만 커버) 발견 시 원문 문장을 순방향 인용해 1회
  재시도, 재실패 시 v2→v1 폴백(어미만) 또는 원문 유지 — **소실 상태로 통과 금지**. 이 가드 통과가 오프라인
  실측 도구(cmd_rewrite)의 기본 모드 v2 승격 전제. builtin '리듬 퇴고' 스킬은 이 v2 결(문단 리팩토링) 지시를
  담되, 결정론 가드는 이 실험 도구에만 산다(엔진 무강제 — 제품 경로는 프롬프트 결만 v2, 상시 자동 패스 없음).

  **근사 한계(형태소분석기 부재 — 적대검증 MED, 양방향·문서화):** (a) *과제외* — 서술어 종결 음절로 끝나는
  명사('그림자'→'자', '오해'→'해')는 _PREDICATE_TAIL 에 걸려 검사에서 빠져, 그 명사가 문장 유일 내용어면
  자유 감각 비트 삭제가 미검출(가드 주 기능의 부분 사각지대·fail-safe 방향). (b) *과포함(leak)* —
  _PREDICATE_TAIL 은 *종결* 어미만 잡아 연결·명사화·관형형 동사('반응하지'·'다가오지'·'미끄러진')를 놓치므로
  이들이 명사로 계수, 재작성이 정당히 의역하면 소실로 오계수돼 정당 압축을 폴백으로 죽일 수 있다(동사꼬리
  tail-regex 로 안전 차단 불가 — 어떤 패턴도 동수의 명사를 오제외, 두더지잡기 금지). (c) 단음절 명사+1글자
  조사('숨이'·'빛이'·'손이')의 조사 과탐만 _cover_needles(단음절 몸통 대체 바늘)로 소스 차단.

스팬 단위 = 검출된 run 스팬 *전체*(문장 하나 아님). 재작성 콜엔 앞뒤 문맥/문단을 read-only 앵커로
동봉(접합 보장·수정 금지 명시). 내용·사실·화자 목소리 불변, 리듬만 재구성.

결정론 코어(LLM 0콜):
  extract_rhythm_spans(text, ...) → 검출된 '~다 run'·파편 클러스터의 문장 인덱스 범위 + 문자 오프셋
  expand_span_to_paragraphs(text, span) → 스팬을 포함하는 문단(빈 줄 경계) 전체로 확장(v2 창)
  build_rewrite_payload(text, span, mode) → [앞 앵커(read-only)] + [대상 창] + [뒤 앵커(read-only)]
  redetect_span(text) → 스팬 텍스트 자체의 재검출 수치(해소 확인)
  sentence_length_stats(text) → 지문 문장 길이 분포(평균·표준편차·최장/최단) — v2 리듬 이동 참고 측정
  content_word_stems(sent) / sentence_coverage(sent, win) / coverage_loss_candidates(orig_win, rw_win)
    → ST-11c 표면 커버리지 — 원문 문장 명사 어간이 재작성 창에 남아 있나(자유 묘사 소실 검출·결정론)

LLM 경로(offline 실측 — 리포트 생성):
  py -3.12 tools/st11_span_rewrite.py detect  <pid> <chapter> [--mode v1|v2]  : (LLM 0콜) 창 추출·페이로드 덤프
  py -3.12 tools/st11_span_rewrite.py rewrite <pid> <chapter> [--mode v1|v2]  : 검출 창 재작성 → 전/후 리포트(md)
  py -3.12 tools/st11_span_rewrite.py compare <pid> <chapter>                 : v1·v2 3원 대비(원문/v1/v2) → st11b_paragraph.md
      대상 회차 원본은 무수정(산출은 tools/reports/ 리포트로만).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # app/ → novelcopilot 임포트

APP = pathlib.Path(__file__).resolve().parents[1]
PROJ = APP / "data" / "projects"
REPORTS = APP / "tools" / "reports"

# 검출기는 엔진 재사용(측정 피처 계보 — 판정기 아님)
from novelcopilot.engine.quality_gates import (  # noqa: E402
    past_tense_run, fragment_ratio, _has_ss_jong, _TRAIL, _PREDICATE_TAIL, _VOCATIVE_TAIL)
# ST-11c 표면 커버리지 가드 — T2 어간 정규화 자산 재사용(조사 접미 결정론 제거, 어간≥2 가드).
#   드리프트 T2 의 _stem 은 교착어 substring 실패(조사형 불일치)를 흡수하는 검증된 자산 —
#   커버리지 검사도 같은 substring 매칭 계보이므로 그대로 태운다(새 정규화기 신설 금지).
from novelcopilot.engine.drift import _stem  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# 오프셋-보존 지문 문장 분할 — quality_gates._prose_sentences 와 *동일 규칙*이되 문자 오프셋을 함께 낸다.
#   _prose_sentences 는 strip 으로 오프셋을 버린다. 스팬을 원문 위치에 replace 하려면 (start,end) 가 필요.
#   규칙 동형 보장: 대사행(따옴표·대시·하이픈 시작) 제외 → 문장부호 분할 → len>=2 필터. 카운터가 세는
#   문장 시퀀스와 인덱스가 1:1 로 맞아야 run/파편 인덱스를 오프셋으로 되돌릴 수 있다.
# ─────────────────────────────────────────────────────────────────────────────
_DLG_PREFIX = ('"', '“', '”', '—', '-')


def prose_sentences_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """지문 문장 리스트를 (문장, start, end) 로. quality_gates._prose_sentences 와 동일한 문장 집합/순서.

    반환 문장열은 _prose_sentences(text) 와 정확히 같다(같은 필터·같은 분할). start/end 는 원문(text)의
    문자 오프셋 — text[start:end] 가 그 문장의 원형(공백 trim 전 조각 안에서의 stripped 위치)."""
    out: list[tuple[str, int, int]] = []
    line_start = 0
    for line in (text or "").splitlines(keepends=True):
        raw = line
        stripped = raw.strip()
        # 대사행 제외(_prose_sentences 와 동일: 따옴표/대시/하이픈으로 시작하는 행)
        if stripped and not stripped.startswith(_DLG_PREFIX):
            # 행 내부 문장부호 분할 — re.split(r"(?<=[.!?…])\s+") 과 동형이되 오프셋 추적
            # 행의 stripped 시작 오프셋
            lead = len(raw) - len(raw.lstrip())
            body = raw.strip()
            # 분할: 문장부호 뒤 공백 경계. finditer 로 세그먼트 경계 오프셋을 body 기준으로 잡는다.
            seg_start = 0
            # 경계 = (?<=[.!?…])\s+ 의 매칭 구간
            for mb in re.finditer(r"(?<=[.!?…])\s+", body):
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


def _past_flag(sent: str) -> bool:
    """quality_gates.past_tense_run 과 동일한 과거형 종결 판별(종성 ㅆ 가드).

    NOTE(ST-14 FIX-1): extract_rhythm_spans 의 run 축은 이제 이 과거형 전용 flag 가 아니라 *무중단 동일
    종결 키*(형태 불문 — _ending_key)로 일반화됐다. 이 헬퍼는 하위호환·근사 계보용으로 잔존(직접 소비처 없음)."""
    c = _TRAIL.sub("", sent)
    return len(c) >= 2 and c.endswith("다") and _has_ss_jong(c[-2])


def _dialogue_line_ranges(text: str) -> list[tuple[int, int]]:
    """원문에서 대사행(따옴표/대시/하이픈 시작 줄)의 (char_start, char_end) 목록 — run 대사 리셋 판정 전용.
    prose_sentences_with_offsets 의 지문 제외 규칙(_DLG_PREFIX)과 동일 기준(strip 후 시작 문자·결정론).
    kiwi_metrics._dialogue_line_ranges 와 동형(FIX-1: 엔진/tools 각자 자체 규칙 복제 — 로드타임 결합 회피)."""
    out: list[tuple[int, int]] = []
    line_start = 0
    for raw in (text or "").splitlines(keepends=True):
        stripped = raw.strip()
        if stripped and stripped.startswith(_DLG_PREFIX):
            lead = len(raw) - len(raw.lstrip())
            out.append((line_start + lead, line_start + lead + len(stripped)))
        line_start += len(raw)
    return out


def _is_fragment(sent: str) -> bool:
    """quality_gates.fragment_ratio 와 동일한 무동사 파편문 판별(§4 오탐 가드)."""
    if sent.rstrip().endswith(("?", "？")):
        return False
    core = _TRAIL.sub("", sent)
    if len(core) < 2:
        return False
    if _PREDICATE_TAIL.search(core):
        return False
    if _VOCATIVE_TAIL.search(core):
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 스팬 추출 — run(과거형 종결 연속) + 파편 클러스터를 문장 인덱스 범위로.
#   인간 기저선 3.0(docs 연구) 참조 → 기본 run 임계 6(≥6 연속이면 '벽' 후보). 파편은 인접 밀집을 클러스터로.
#   임계·자동교정 없음(측정 피처) — 이 도구는 '재작성 후보 스팬'을 산출할 뿐, 엔진이 강제 실행하지 않는다.
# ─────────────────────────────────────────────────────────────────────────────
def _ending_key(sent: str) -> str | None:
    """한 지문 문장의 문말 종결 키(형태 불문 — 과거형 '…ㅆ다'뿐 아니라 현재형 'ㄴ다'·명사문 등).

    kiwi_metrics._ending_key_kiwi(EP*+EF 열)을 쓰되, Kiwi 부재 시 _ending_key_regex 로 강등(기존 강등 계약).
    종결 키가 없으면(파편/체언 종결) None — run 에 세지 않되 *끊지도 않는다*(ST-14 라이브 보정 — 지각 축 정합).
    FIX-1: '과거형 flag 연속'을 '무중단 동일 종결 키 연속'으로 일반화하는 축(ㄴ다 벽 검출).
    kiwi_metrics 와 동일 키 체계(재사용·중복 구현 금지)."""
    try:
        import tools.kiwi_metrics as km
    except Exception:
        return None
    k = km._get_kiwi()
    return km._ending_key_kiwi(sent, k) if k is not None else km._ending_key_regex(sent)


def extract_rhythm_spans(text: str, run_threshold: int = 6,
                         frag_cluster_min: int = 3, frag_cluster_gap: int = 2,
                         sent_splitter=None) -> list[dict]:
    """검출된 '무중단 동일 종결 키 run'·파편 클러스터의 문장 인덱스 범위 + 문자 오프셋 스팬을 산출(결정론).

    · run(ST-14 FIX-1): 지문 종결 키가 **무중단 동일**하게 run_threshold 이상 연속 → 그 구간이 한 스팬.
      과거형('…ㅆ다')뿐 아니라 현재형('ㄴ다')·명사문 등 *형태 불문* — 같은 종결 키가 이어지면 벽으로 잡는다.
      **대사 리셋**: 두 지문 문장 사이 원문에 대사행(_DLG_PREFIX 시작 줄)이 끼면 run 을 끊는다(대사가 끊어주는
      호흡 = 지문 벽 아님). kind='ending_run'(구 'past_run' 대체 — 소비처 전수 갱신). metric 에 run_len·ending_key.
    · frag: 파편문이 frag_cluster_gap 이내 간격으로 frag_cluster_min 이상 뭉친 구간 → 한 스팬(양끝 파편 포함).
    반환 스팬: {kind, sent_start, sent_end(포함), char_start, char_end, span_text, n_sent, metric}.
    두 종류가 겹치면(같은 구간) run 을 우선(더 넓은 벽) — 중복 스팬 제거.

    sent_splitter (ST-10 §2ⓐ): 문장 분리기 주입점 — (text)->[(문장, char_start, char_end)]. 미지정(기본)이면
      prose_sentences_with_offsets(정규식 — 기존 축 값 불변, 캘리브레이션 연속성). tools.kiwi_metrics.split_sents
      를 넘기면 인용문 내부 마침표를 경계로 오인하지 않아(ch8 계보) 스팬 경계가 실문장 경계와 일치한다(ch12 계보
      원위 수리). 주입 분리기도 (문장, start, end)·text[start:end]==문장 계약을 지켜야 한다(오프셋 replace 정합).
      Kiwi 미설치 시 split_sents 는 정규식으로 강등되므로 이 경로도 기본 경로와 바이트 동일로 수렴한다."""
    sents = (sent_splitter or prose_sentences_with_offsets)(text)
    n = len(sents)
    spans: list[dict] = []

    # ── run 스팬 (무중단 동일 종결 키 + 대사 리셋) ──
    keys = [_ending_key(s) for s, _, _ in sents]
    dlg_ranges = _dialogue_line_ranges(text)

    def _dlg_between(prev_end: int, next_start: int) -> bool:
        return any(prev_end <= ds and de <= next_start for (ds, de) in dlg_ranges)

    # ST-14 라이브 보정: 파편(키 None)은 run 을 *끊지 않고 건너뛴다* — 사용자 정독 실측(파편 개입 12연속도
    #   벽으로 읽힘·파편-리셋 의미는 검출 무발화). 단 **인용 시작 문장은 리셋 장벽**(인라인 대사도 체감상 벽을
    #   끊는다). kiwi_metrics.uninterrupted_ending_runs 와 의미 동일 유지(의미 분화 재발 금지).
    #   run_len = 키 보유 문장 수 / sent_start~sent_end·char 범위 = 첫~끝 키 보유 문장(사이 파편 포함 창).
    _QUOTE_HEADS = ('"', '“', '「', '『', "'", '‘')
    _cur_key = None
    _cur: list[int] = []

    def _close_run():
        nonlocal _cur, _cur_key
        if _cur_key is not None and len(_cur) >= run_threshold:
            i, j = _cur[0], _cur[-1]
            cs, ce = sents[i][1], sents[j][2]
            spans.append({
                "kind": "ending_run", "sent_start": i, "sent_end": j,
                "char_start": cs, "char_end": ce, "span_text": text[cs:ce],
                "n_sent": len(_cur), "metric": {"run_len": len(_cur), "ending_key": _cur_key},
            })
        _cur, _cur_key = [], None

    for k2 in range(n):
        if keys[k2] is None:
            if sents[k2][0].lstrip().startswith(_QUOTE_HEADS):
                _close_run()                   # 인라인 인용 — 대사 장벽
            continue                           # 파편 — 세지 않되 끊지도 않음
        if _cur and (keys[k2] != _cur_key or _dlg_between(sents[_cur[-1]][2], sents[k2][1])):
            _close_run()
        _cur_key = keys[k2]
        _cur.append(k2)
    _close_run()

    # ── 파편 클러스터 스팬 ──
    frag_idx = [k for k in range(n) if _is_fragment(sents[k][0])]
    clusters: list[list[int]] = []
    for k in frag_idx:
        if clusters and k - clusters[-1][-1] <= frag_cluster_gap:
            clusters[-1].append(k)
        else:
            clusters.append([k])
    for cl in clusters:
        if len(cl) >= frag_cluster_min:
            a, b = cl[0], cl[-1]          # 클러스터 양끝(사이 비-파편 문장 포함해 접합 유지)
            cs, ce = sents[a][1], sents[b][2]
            spans.append({
                "kind": "fragment_cluster", "sent_start": a, "sent_end": b,
                "char_start": cs, "char_end": ce, "span_text": text[cs:ce],
                "n_sent": b - a + 1, "metric": {"n_fragment": len(cl)},
            })

    # ── 중복/포함 제거 — 문장 인덱스 범위가 겹치면 run(벽) 우선, 그다음 더 넓은 스팬 ──
    spans.sort(key=lambda s: (s["sent_start"], -(s["sent_end"] - s["sent_start"])))
    kept: list[dict] = []
    for sp in spans:
        overlap = False
        for k in kept:
            if not (sp["sent_end"] < k["sent_start"] or sp["sent_start"] > k["sent_end"]):
                # 겹침: run 우선. 이미 run 을 잡았으면 파편 스팬 폐기.
                if k["kind"] == "ending_run" or sp["kind"] != "ending_run":
                    overlap = True
                    break
        if not overlap:
            kept.append(sp)
    kept.sort(key=lambda s: s["sent_start"])
    return kept


# ─────────────────────────────────────────────────────────────────────────────
# v2 문단 확장 — 검출 스팬을 *포함하는 문단(들) 전체*로 확장(빈 줄 경계). 문장 경계 재구성을
#   허용하려면 재작성 창이 완결된 문단이어야(한 문단 안에서 문장을 엮어 흘릴 수 있음). 문단은
#   '빈 줄(\n[공백]*\n)'로 분리되는 텍스트 블록 — 이 회차 조판이 그러함(DP-4b).
#   스팬이 문단 하나에 들어가면 그 문단, 여러 문단에 걸치면 첫~끝 문단 전체를 창으로 한다.
#   확장 창 밖(앞/뒤 문단)은 read-only 앵커. 반환 오프셋은 원문 기준(replace 정합).
# ─────────────────────────────────────────────────────────────────────────────
_PARA_SEP = re.compile(r"\n[ \t]*\n")   # 빈 줄(공백만 있는 줄 포함) = 문단 경계


def paragraph_spans(text: str) -> list[tuple[int, int]]:
    """원문을 빈 줄 경계로 나눈 문단들의 (char_start, char_end) 목록(빈 문단 제외).
    end 는 문단 본문 끝(뒤따르는 빈 줄/구분자는 미포함) — 원문 슬라이스 text[s:e] 가 문단 원형."""
    out: list[tuple[int, int]] = []
    pos = 0
    n = len(text or "")
    for m in _PARA_SEP.finditer(text or ""):
        block = text[pos:m.start()]
        s = block.strip()
        if s:
            lead = len(block) - len(block.lstrip())
            out.append((pos + lead, pos + lead + len(s)))
        pos = m.end()
    # 마지막 블록
    block = text[pos:n]
    s = block.strip()
    if s:
        lead = len(block) - len(block.lstrip())
        out.append((pos + lead, pos + lead + len(s)))
    return out


def _is_dialogue_paragraph(para_text: str) -> bool:
    """문단이 대사행(따옴표·대시·하이픈으로 시작하는 행)을 하나라도 포함하면 True.
    v2 문단 리팩토링의 '벽' — 대사 문단은 재작성 창에서 제외되고 read-only 앵커로만 남는다.
    (prose_sentences_with_offsets 의 대사 제외 규칙 _DLG_PREFIX 와 동일 기준.)"""
    for line in (para_text or "").splitlines():
        s = line.strip()
        if s and s.startswith(_DLG_PREFIX):
            return True
    return False


def expand_span_to_paragraphs(text: str, span: dict) -> dict:
    """검출 스팬을 포함하는 *지문 문단(들)* 전체로 확장(v2 재작성 창). span 을 얕게 복사해
    char_start/char_end/span_text 를 문단 경계로 넓히고, 확장 메타를 얹어 반환.

    **대사 문단 보호(ST-11b HIGH 수리)**: 대사행을 포함하는 문단은 재작성 창에서 제외하고 '벽'으로
    삼는다 — 창은 검출 스팬을 담는 *지문 문단들의 연속 블록*으로 확장하되, 대사 문단을 만나면 멈춘다.
    검출 run 스팬이 대사 문단을 관통해도(엔진 run 카운터는 지문만 세므로 대사 문단이 run 중간에 낄 수
    있음) 창은 대사 문단 이쪽/저쪽 중 스팬 본체(char 범위)와 겹치는 지문 블록만 취한다. 이로써 v2
    PARAGRAPH_DIRECTIVE("한 문장으로 흘려도 좋다")가 대사를 지문으로 흡수/의역하는 사고를 원천 차단.
    화자 목소리·대사 원형 불변 보장.

    스팬 char 범위와 겹치는 지문 문단을 못 찾으면(조판에 빈 줄 없거나 스팬이 대사 문단에만 걸림 등)
    원 스팬을 그대로 반환(확장 없음·하위 안전)."""
    paras = paragraph_spans(text)
    cs, ce = span["char_start"], span["char_end"]
    # 지문 문단만 후보(대사 문단은 벽) + 스팬 char 범위와 겹치는 문단
    hit = [(a, b) for (a, b) in paras
           if not (b <= cs or a >= ce) and not _is_dialogue_paragraph(text[a:b])]
    if not hit:
        # 지문 문단 매핑 실패(대사 문단에만 걸림·빈 줄 없음 등) → 원 스팬 유지(확장 없음)
        out = dict(span)
        out.update(orig_char_start=cs, orig_char_end=ce, n_para=0, expanded=False,
                   dialogue_clamped=False)
        return out
    # 대사 문단 벽으로 창을 '연속 지문 블록'으로 클램프 — 스팬 본체(char 범위)의 시작을 포함하는
    # 문단부터 대사 문단(또는 겹침 종료)까지의 연속 지문 문단만. hit 이 대사 벽으로 쪼개져 있으면
    # 스팬 시작(cs)에 가장 가까운 지문 문단이 속한 연속 블록만 취한다(대사 넘어 far-side 배제).
    hit.sort(key=lambda ab: ab[0])
    # 원문 문단 인덱스에서 대사 문단을 벽으로 하는 연속 지문 그룹을 만든다
    prose_paras = [(a, b) for (a, b) in paras if not _is_dialogue_paragraph(text[a:b])]
    # (a,b) → 원문 순서상 인접 지문 문단인지: 사이에 대사 문단이 없으면 같은 그룹
    groups: list[list[tuple[int, int]]] = []
    for (a, b) in prose_paras:
        if groups:
            pa, pb = groups[-1][-1]
            between = [q for (q, r) in paras if pb <= q and r <= a and _is_dialogue_paragraph(text[q:r])]
            if not between:
                groups[-1].append((a, b))
                continue
        groups.append([(a, b)])
    # 스팬 본체와 겹치는 지문 문단을 담은 그룹 중, 스팬 시작(cs)에 가장 가까운 문단이 속한 그룹 채택
    hit_set = set(hit)
    anchor_para = min(hit, key=lambda ab: abs(ab[0] - cs))
    chosen = next((g for g in groups if anchor_para in g), [anchor_para])
    win_paras = [ab for ab in chosen if ab in hit_set]   # 그룹 안에서도 스팬과 겹치는 문단만
    if not win_paras:
        win_paras = [anchor_para]
    new_cs = min(a for a, _ in win_paras)
    new_ce = max(b for _, b in win_paras)
    dialogue_clamped = len(win_paras) < len(hit)   # 대사 벽/far-side 때문에 창이 hit 보다 좁아졌나
    out = dict(span)
    out.update(char_start=new_cs, char_end=new_ce, span_text=text[new_cs:new_ce],
               orig_char_start=cs, orig_char_end=ce, n_para=len(win_paras),
               expanded=(new_cs != cs or new_ce != ce),
               dialogue_clamped=dialogue_clamped)
    return out


def sentence_length_stats(text: str) -> dict:
    """지문 문장 길이(문자수) 분포 — v2 리듬 이동 참고 측정(판정 아님, advisory).
    긴 만연문과 짧은 강조 단문이 교차하면 표준편차↑·최장↑. 스타카토 균질이면 표준편차↓."""
    sents = [s for (s, _, _) in prose_sentences_with_offsets(text)]
    lens = [len(s) for s in sents]
    n = len(lens)
    if n == 0:
        return {"n_sent": 0, "mean": 0.0, "stdev": 0.0, "min": 0, "max": 0}
    mean = sum(lens) / n
    var = sum((x - mean) ** 2 for x in lens) / n
    return {"n_sent": n, "mean": round(mean, 1), "stdev": round(var ** 0.5, 1),
            "min": min(lens), "max": max(lens)}


# ─────────────────────────────────────────────────────────────────────────────
# 앵커 동봉 페이로드 — [앞 문맥 2~3문장(read-only)] + [대상 스팬] + [뒤 문맥 2~3문장(read-only)].
#   앵커는 지문·대사 무관하게 *원문 순서상* 스팬 앞/뒤의 실제 문장(대사도 접합 대상이므로 포함).
#   그래서 여긴 지문 필터를 쓰지 않고 원문 전체를 문장 단위로 잘라 창 오프셋 기준 앞/뒤를 취한다.
#   v2 에서는 확장된 문단 창의 char_start/char_end 기준으로 앵커를 잡아 앵커가 창(문단)과 겹치지 않는다.
# ─────────────────────────────────────────────────────────────────────────────
def _all_sentences_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """앵커용 — 대사 포함 전체 문장(행 경계·문장부호 경계). 오프셋 보존."""
    out: list[tuple[str, int, int]] = []
    line_start = 0
    for line in (text or "").splitlines(keepends=True):
        body = line.strip()
        if body:
            lead = len(line) - len(line.lstrip())
            seg_start = 0
            for mb in re.finditer(r"(?<=[.!?…])\s+", body):
                seg = body[seg_start:mb.start()]
                s = seg.strip()
                if s:
                    off = line_start + lead + seg_start + (len(seg) - len(seg.lstrip()))
                    out.append((s, off, off + len(s)))
                seg_start = mb.end()
            seg = body[seg_start:]
            s = seg.strip()
            if s:
                off = line_start + lead + seg_start + (len(seg) - len(seg.lstrip()))
                out.append((s, off, off + len(s)))
        line_start += len(line)
    return out


def build_rewrite_payload(text: str, span: dict, anchor_n: int = 3, mode: str = "v1") -> dict:
    """재작성 창 1개의 페이로드 — 앞/뒤 앵커(read-only) + 대상 창.

    v1: 창 = 검출 스팬 그대로. 앵커 = 창 앞/뒤 각 anchor_n 문장(대사 포함).
    v2: 창 = 스팬을 포함하는 문단(들) 전체(호출부가 expand_span_to_paragraphs 로 확장해 넘김).
        앵커 = 창 앞/뒤 각 anchor_n 문장(=인접 문단의 문장) — v1 과 형식 동일, 다만 창 오프셋이 문단 경계라
        앵커가 문단 밖에서 잡혀 창(문단)과 겹치지 않는다.
    반환: {before_anchor, span_text, after_anchor, ...} — LLM 콜은 상위(rewrite_span_via_chassis)가 조립.
    """
    all_sents = _all_sentences_with_offsets(text)
    cs, ce = span["char_start"], span["char_end"]
    before = [s for (s, a, b) in all_sents if b <= cs][-anchor_n:]
    after = [s for (s, a, b) in all_sents if a >= ce][:anchor_n]
    return {
        "kind": span["kind"], "mode": mode,
        "before_anchor": before,
        "span_text": span["span_text"],
        "after_anchor": after,
        "sent_start": span["sent_start"], "sent_end": span["sent_end"],
        "char_start": cs, "char_end": ce,
        "n_sent": span["n_sent"], "n_para": span.get("n_para"),
        "metric": span.get("metric", {}),
    }


# 긍정 전용 재작성 지시 — '피하라'/틱 호명 없음. 5축 변주(문형·시제·발화층위·술어) 긍정 지시.
# v1: 문장 경계 보존(종결의 결만 다양화).
RHYTHM_DIRECTIVE = (
    "이 구간을 화자의 목소리를 유지한 채 리듬이 살아 있게 재구성하라 — "
    "문형(의문·감탄)·시제(현재형 판단)·발화 층위(입말 생각·짧은 대사)·술어 교체를 활용. "
    "사건·사실·정보는 그대로 두고, 문장의 호흡과 종결의 결만 다양하게."
)

# v2(ST-11b): 문단 리팩토링 — 문장 경계 재구성 전면 허용. 만연 기반 + 순간 강조 단문 교차(결로 지시,
#   수치 하드코딩 없음). 사용자 확정 설계: "하나의 비트는 하나의 문장이 담을 수 있다."
PARAGRAPH_DIRECTIVE = (
    "이 문단을 화자의 목소리를 유지한 채 하나의 매끄러운 흐름으로 다시 써라. "
    "이 문단이 하나의 연속된 체험·동작이라면 짧게 끊긴 문장들을 연결어미·종속절로 엮어 한 문장으로 흘려도 좋다. "
    "호흡이 긴 문장과 짧은 강조 문장이 교차하게 — 극적 순간의 단문 펀치 한둘은 남겨라. "
    "사건·사실·정보·수치·화자 목소리는 그대로 두고, 문장의 이음새와 호흡만 자연스럽게 재구성하라."
)


def _uninterrupted_ending_run_max(text: str) -> int:
    """무중단 동일 종결 키 run 최대값(형태 불문·대사 리셋) — extract_rhythm_spans 의 run 축과 동일 계보.
    kiwi_metrics.uninterrupted_ending_runs(threshold=1) 재사용(중복 구현 금지). tools 부재/실패 시 0(결측)."""
    try:
        import tools.kiwi_metrics as km
        runs = km.uninterrupted_ending_runs(text or "", threshold=1)
    except Exception:
        return 0
    return max((r["n_sent"] for r in runs), default=0)


def redetect_span(span_text: str, run_threshold: int = 6) -> dict:
    """재작성된 스팬 텍스트 자체의 재검출 수치 — run 해소·파편 밀도 확인(결정론).

    스팬 단위이므로 지문 문장 수가 적을 수 있음 — 절대 판정 아니라 전/후 비교용 수치.

    ST-14 FIX-1(급소): run 축을 검출과 동일한 **무중단 동일 종결 키 run**(형태 불문·대사 리셋)으로 교체한다.
    구판은 과거형 전용(past_tense_run) 이라 현재형 'ㄴ다' 벽을 '이미 해소'로 오판했다(R2/R3). ending_run_max·
    run_resolved 는 이제 검출 축과 정합한다. past_run_max/past_ratio 는 과거형 계보 참고값으로 병기(하위호환).
    수리 성공 판정(run_resolved)·재시도 채택 비교는 ending_run_max 를 본다."""
    ptr = past_tense_run(span_text)
    fr = fragment_ratio(span_text)
    ls = sentence_length_stats(span_text)
    ending_run_max = _uninterrupted_ending_run_max(span_text)
    return {
        "ending_run_max": ending_run_max,                       # FIX-1: 형태 불문 무중단 종결 키 run 최대(검출 축 정합)
        "past_run_max": ptr["max_run"], "past_ratio": ptr["ratio"],   # 과거형 계보 참고값(하위호환·advisory)
        "frag_ratio": fr["ratio"], "n_fragment": fr["n_fragment"],
        "n_sent": ptr["n_sent"],
        "run_resolved": ending_run_max < run_threshold,         # FIX-1: 해소 판정도 형태 불문 축
        "len_mean": ls["mean"], "len_stdev": ls["stdev"], "len_max": ls["max"], "len_min": ls["min"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# ST-11c 프로즈 표면 커버리지 가드 — v2(문단 리팩토링)가 자유 묘사 문장을 소실하는 리스크 차단.
#
#   ST-11b CONFIRMED 리스크: v2 는 문장 경계를 재구성해 하나의 연속 체험을 흘리는데, 그 과정에서
#   *캐논 클레임이 아닌 자유 감각 묘사*("시야가 좁아졌다")를 통째로 떨어뜨릴 수 있다. G-B(캐논 가드)는
#   등급·수치·소속 같은 클레임 표면만 검사하므로 자유 묘사 삭제에 구조적으로 눈이 멀다 → 별도 가드 필요.
#
#   설계 원칙(무강제·보수적):
#     · **명사(내용어) 중심** — 원문 문장의 명사·구체 심상(시야·폐·무릎·고리·가로등)이 재작성 창 전체에
#       substring 으로 남아 있나를 검사한다. 동사·형용사 활용형은 제외 — 재작성이 정당히 패러프레이즈/시제
#       전환하는 대상이라 활용 변화가 곧 소실이 아니다(캘리브레이션: 활용형 포함 시 '반응하지→움직이지'
#       같은 정당 의역이 오탐). 판별은 quality_gates._PREDICATE_TAIL(서술어/연결어미 종결) 재사용.
#     · **창 대 창** — 원문 문장의 내용어가 *재작성 창 전체* 어디에든 있으면 커버(문장 대 문장 아님).
#       문장 결합·어순 재배치는 반드시 통과해야 한다(v2 의 존재 이유).
#     · **어간 substring** — T2 자산 _stem 으로 조사 접미 제거 후 substring(교착어 조사 변이 흡수).
#     · **보수적 임계** — 문장의 내용어 스템이 과반 미만(< 0.5) 커버 시에만 '소실 후보'. 명사가 하나도 없는
#       문장(순수 동사문 '걸었다')은 검사 대상 제외(구체 심상 없음 → 소실 추적 대상 아님·의역 오탐 회피).
#   캘리브레이션(실측): 질식 v2 의 "시야가 좁아졌다"=0.00(잡힘) · 질식 span0/v1 정당 결합=최저 0.50(통과).
# ─────────────────────────────────────────────────────────────────────────────
_CONTENT_TOK = re.compile(r"[가-힣A-Za-z0-9]{2,}")   # 내용어 후보(2자+ 한글/영숫자) — drift._event_keywords 동형
# 단음절 명사 몸통에 붙는 1글자 문법 조사(주격·목적격·주제격 등) — _stem 의 어간≥2 가드가 이 형태의
#   조사를 못 떼어 스템이 '숨이'/'빛이'/'손이' 로 굳는다(ST-11c 적대검증 MED). 커버리지에서 이 굳은 스템만
#   substring 매칭하면 정당한 조사 교체('숨이'→'숨은')·재구성('손이'→'손끝')이 소실로 오계수되어 정당한 v2
#   압축을 폴백으로 죽인다(티켓 레드라인 위반). 아래 _cover_needles 로 '단음절 몸통'을 커버 대체 바늘로 추가.
_MONO_PARTICLE = ("은", "는", "이", "가", "을", "를", "와", "과", "의", "에", "도", "로", "만")
_HANGUL_SYL = re.compile(r"[가-힣]")


def _cover_needles(stem: str) -> list[str]:
    """커버리지 매칭용 바늘 — 기본은 스템 자체. 단, _stem 이 어간≥2 가드 때문에 1글자 명사 몸통의 조사를
    못 뗀 형태(2글자 = [한글 1] + [1글자 조사], 예 '숨이'·'빛이'·'손이')면 *단음절 몸통*('숨'·'빛'·'손')을
    대체 바늘로 추가한다.

    **오직 커버 크레딧을 늘릴 뿐(제거 없음)** — 정당한 조사 교체·재구성이 소실로 오계수되는 MED 과탐만
    줄이고, 새로운 오차단(false block)은 구조적으로 만들지 않는다. 대가: 단음절 몸통이 무관한 낱말 안에
    우연히 나타나면('피'가 '피부'에) 과크레딧(under-detect) — 방향이 fail-safe(정당 압축을 죽이지 않음)라
    보수적 원칙에 부합. 다음절 명사('시야'·'무릎')는 _stem 이 조사를 정상 제거하므로 이 경로를 타지 않는다."""
    needles = [stem]
    if len(stem) == 2 and _HANGUL_SYL.match(stem[0]) and stem[1:] in _MONO_PARTICLE:
        needles.append(stem[0])
    return needles


def _stem_covered(stem: str, window_text: str) -> bool:
    """스템이 재작성 창에 substring 으로 남아 있나 — 단음절 명사 몸통 대체 바늘 포함(_cover_needles)."""
    return any(n in (window_text or "") for n in _cover_needles(stem))


def content_word_stems(sent: str) -> list[str]:
    """문장의 *명사(구체 내용어)* 어간 목록 — 커버리지 검사 단위(결정론).

    1) 2자+ 토큰 추출 → 2) _stem 으로 조사 접미 제거(어간≥2 가드) → 3) 서술어/연결어미 종결 토큰 제외
    (_PREDICATE_TAIL — 동사·형용사 활용형은 재작성이 정당히 바꾸는 대상이라 커버리지 검사에서 뺀다).
    중복 스템은 유지(문장 내 반복 심상의 가중 반영 — 커버리지 비율 계산 시 분모).

    한계(적대검증 MED — 형태소분석기 부재의 근사 오차, *양방향*):
      · 과제외(under-detect): 명사가 서술어 종결 음절로 끝나면(예 '오해'→'해', '그림자'→'자') _PREDICATE_TAIL
        에 걸려 검사에서 빠진다. 그 명사가 문장의 유일한 내용어면 그 자유 감각 비트(예 '그림자가 짙어졌다')의
        삭제가 미검출 — 이 가드의 *주 기능(단일 명사 자유 감각 비트 소실 검출)에 부분 사각지대*다(주변부 아님).
        방향은 fail-safe(정당 압축을 죽이지 않음).
      · 과포함(leak, over-detect 원인): _PREDICATE_TAIL 은 문장 *종결* 어미만 잡고 연결·명사화·관형 활용형은
        놓친다 → 연결형 동사('반응하지'·'다가오지'·'미끄러진'·'좁아지기')가 '명사'로 계수된다. 이 활용형이
        재작성에서 정당히 의역되면(반응하지→움직이지) 소실로 오계수 → 정당 압축을 폴백으로 죽일 수 있다.
        이 leak 은 tail-regex 로 안전 차단 불가(어떤 동사꼬리 패턴도 동수의 명사를 오제외 — 실측 확인, 두더지
        잡기 금지). 단음절 조사 과탐(숨이/빛이)만 _cover_needles 로 소스 차단, 동사 leak 은 리포트에 정직 공개."""
    out: list[str] = []
    for tok in _CONTENT_TOK.findall(sent or ""):
        st = _stem(tok)
        if len(st) < 2:
            continue
        if _PREDICATE_TAIL.search(st):   # 동사·형용사 *종결* 활용형 → 커버리지 검사 대상 아님(연결형은 leak, 위 한계)
            continue
        out.append(st)
    return out


def sentence_coverage(orig_sent: str, window_text: str) -> dict:
    """원문 한 문장의 명사 어간이 재작성 창 전체에서 substring 으로 몇 % 커버되나(결정론).

    반환 {stems, n_stem, covered, missing, ratio}. n_stem==0(명사 없는 순수 동사문)이면 ratio=None
    (검사 대상 아님 — 호출부가 스킵). 창 대 창 검사이므로 문장 결합·재배치는 통과한다.
    매칭은 _stem_covered — 단음절 명사 몸통 대체 바늘 포함(조사 교체 과탐 소스 차단, MED 수리)."""
    stems = content_word_stems(orig_sent)
    if not stems:
        return {"stems": [], "n_stem": 0, "covered": [], "missing": [], "ratio": None}
    missing = [st for st in stems if not _stem_covered(st, window_text)]
    covered = [st for st in stems if _stem_covered(st, window_text)]
    return {"stems": stems, "n_stem": len(stems), "covered": covered, "missing": missing,
            "ratio": round(1 - len(missing) / len(stems), 3)}


def coverage_loss_candidates(orig_window_text: str, rewrite_window_text: str,
                             thresh: float = 0.5) -> list[dict]:
    """원문 창의 각 *지문* 문장 중, 명사 어간이 재작성 창에서 과반 미만(< thresh) 커버되는 '소실 후보'.

    · 원문 창의 지문 문장(대사 제외 — prose_sentences_with_offsets)만 검사(자유 묘사 소실이 대상).
    · 각 문장 대 *재작성 창 전체* 커버리지(창 대 창) — 결합·재배치는 통과.
    · thresh 보수적 기본 0.5(과반 미만만 소실 후보) · 명사 없는 문장은 스킵.
    반환: [{sent, ratio, missing, stems}] — fix 지시에 원문 문장을 순방향 인용해 유지 요청하는 재료."""
    out: list[dict] = []
    for (s, _a, _b) in prose_sentences_with_offsets(orig_window_text):
        cov = sentence_coverage(s, rewrite_window_text)
        if cov["ratio"] is None:
            continue
        if cov["ratio"] < thresh:
            out.append({"sent": s, "ratio": cov["ratio"], "missing": cov["missing"],
                        "stems": cov["stems"]})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 재작성 콜 — 기존 revise 섀시(harness.ChapterGenerator.revise_prose) 재사용(사실 불변 가드).
#   revise_prose 는 span_text 를 받아 자체적으로 앞뒤 200자 문맥을 참고로 주입하고, 사실불변 sys 가드 +
#   확정 캐논 주입 + 길이가드 + span replace 까지 수행한다. ST-11 은 그 위에 (a) 검출 스팬 자동 산출
#   (b) 긍정 리듬 지시 (c) 재검출(미해소 1회 재시도) (d) 사실 표면 비교(_guardrail G-B) 를 얹는다.
# ─────────────────────────────────────────────────────────────────────────────
def rewrite_span_via_chassis(generator, ontology, checker, chapter_no: int,
                             full_text: str, span: dict, run_threshold: int = 6,
                             directive: str | None = None, service=None, mode: str = "v1",
                             threaded_before_res=None, threaded_before_ids=None) -> dict:
    """검출 창 1개 재작성(LLM 1콜, 미해소 시 1회 재시도 → 최대 2콜) + 재검출 + G-B 사실 표면 비교.

    mode='v1': 창 = 검출 스팬 그대로, 지시 = RHYTHM_DIRECTIVE(문장 경계 보존·종결 다양화).
    mode='v2': 창 = 스팬을 포함하는 문단(들) 전체로 확장(expand_span_to_paragraphs),
               지시 = PARAGRAPH_DIRECTIVE(문장 경계 재구성 허용·만연+강조 단문 교차).
    directive 를 명시하면 mode 기본 지시를 덮어쓴다(하위호환: 기존 v1 호출은 directive 미지정 → RHYTHM_DIRECTIVE).

    service: 사실 표면 비교(G-B)에 쓸 CopilotService 인스턴스(있으면 _guardrail 재사용). None 이면 비교 생략.
    threaded_before_res/threaded_before_ids(CE-5): 직전 스팬의 after 추출을 이 스팬 before_res 로 재사용(ids 동일 시).
      기본 None = 현행 경로(전문 재추출) 바이트 동일. 반환의 before_res/after_res/check_ids 를 호출부가 스레딩한다.
    반환: {span_text_before, span_text_after, full_after, redetect_before, redetect_after,
           guardrail, retried, changed, mode, expanded, n_para, before_res, after_res, check_ids}.
      full_after 는 원문에 창 replace 한 전체 텍스트. before_res/after_res 는 CheckResult(스레딩 전용·비직렬화)."""
    if mode == "v2":
        span = expand_span_to_paragraphs(full_text, span)
        if directive is None:
            directive = PARAGRAPH_DIRECTIVE
    if directive is None:
        directive = RHYTHM_DIRECTIVE
    span_text = span["span_text"]
    ids = sorted(set(ontology.scan_present_ids(full_text))) if ontology is not None else []
    # CE-5(가드 결과 스레딩): 직전 스팬의 after_res 를 이 스팬 before_res 로 재사용 — 단 roster(ids)가 동일할 때만.
    #   누적 적용이라 텍스트는 정합하나, 편집이 인명 표기를 넣/빼면 scan_present_ids 가 달라져(추출은 involved_ids
    #   조건부) 재사용이 오추출이 된다 → ids 불일치 시 전문 재추출로 폴백(결측 정직·침묵 재활용 금지).
    #   check_text 캐시가 아니라 오케스트레이션 층 결과 전달이므로 VP-3 요동 판정(같은 텍스트 재추출)을 침범하지 않는다.
    if threaded_before_res is not None and threaded_before_ids == ids:
        before_res = threaded_before_res
    else:
        before_res = checker.check_text(full_text, ontology, chapter_no, ids) if checker is not None else None

    def _one_call(instr: str) -> str:
        return generator.revise_prose(
            instr, full_text, span_text=span_text, passes=[],
            ids=ids, ontology=ontology, chapter_no=chapter_no)

    full_after = _one_call(directive)
    # revise_prose 는 실패/무변경 시 before_text(=full_text) 그대로 반환 → span 부분만 추출해 재검출
    def _extract_after_span(full_after_text: str) -> str:
        # replace 이후 스팬 위치가 달라졌을 수 있음 — 앞부분(char_start 이전)이 불변이므로 그 지점부터
        # 뒤 앵커 시작까지를 새 스팬으로 근사. 뒤 앵커 첫 문장으로 경계를 잡는다.
        pre = full_text[:span["char_start"]]
        post = full_text[span["char_end"]:]
        if full_after_text.startswith(pre) and full_after_text.endswith(post):
            return full_after_text[len(pre):len(full_after_text) - len(post)]
        return span_text   # 무변경/불일치 → 원본 스팬

    after_span = _extract_after_span(full_after)
    redet = redetect_span(after_span, run_threshold)
    retried = False
    # 미해소(run 여전) 시 1회 재시도 — 더 강한 긍정 지시.
    #   v1: 발화 층위·현재형 판단으로 종결 다양화(문장 유지). v2: 연속 체험을 한 문장으로 더 엮기(만연 강화).
    if not redet["run_resolved"] and full_after != full_text:
        retried = True
        if mode == "v2":
            retry_instr = (directive + " 특히 같은 동작·감각이 연달아 짧은 문장으로 끊긴 부분은 "
                           "연결어미·종속절로 한 문장에 담아 흐름을 이어라(단, 강조 단문 한둘은 남겨라).")
        else:
            retry_instr = (directive + " 특히 같은 종결이 잇따르는 구간은 입말 생각·짧은 대사·의문·명사문·"
                           "현재형 악센트 중 문맥에 맞는 결로 갈아, 같은 종결이 세 번 이상 잇따르지 않게 하라.")
        full_after2 = _one_call(retry_instr)
        after_span2 = _extract_after_span(full_after2)
        redet2 = redetect_span(after_span2, run_threshold)
        # 재시도가 run 을 더 낮췄으면 채택 (FIX-1: 형태 불문 무중단 종결 키 run 축)
        if redet2["ending_run_max"] <= redet["ending_run_max"]:
            full_after, after_span, redet = full_after2, after_span2, redet2

    # ── ST-11c 표면 커버리지 가드 — 자유 묘사 소실 차단(소실 상태로 통과 금지) ──
    #   원문 창(span_text) 대 재작성 창(after_span) 커버리지. 소실 후보(명사 어간 과반 미달) 발견 시
    #   1회 재시도(원문 문장 순방향 인용 "다음 내용이 유지되게: …"), 재실패 시 폴백:
    #     v2 → v1(어미만·문장 경계 보존이라 내용 소실 구조적 불가) 재시도, v1 도 소실이면 원문 유지.
    #   폴백은 '리듬 개선 포기'가 아니라 '소실 없는 최선'을 취하는 것 — 무강제 원칙(정보 보존 > 리듬).
    coverage_guard = {"checked": False}
    if full_after != full_text:   # 실제 재작성이 일어난 창만 검사(무변경은 소실 없음)
        losses = coverage_loss_candidates(span_text, after_span)
        coverage_guard = {"checked": True, "losses_before": [l["sent"] for l in losses],
                          "retried_coverage": False, "fallback": None,
                          "losses_after": [l["sent"] for l in losses]}
        if losses:
            coverage_guard["retried_coverage"] = True
            keep_lines = "  ".join(f"«{l['sent']}»" for l in losses)
            cov_instr = (directive + " 단, 다음 내용(감각·묘사)이 반드시 재작성 결과에 유지되게 하라 — "
                         "삭제하거나 건너뛰지 말고 흐름 안에 녹여라: " + keep_lines)
            full_after_c = _one_call(cov_instr)
            after_span_c = _extract_after_span(full_after_c)
            losses_c = coverage_loss_candidates(span_text, after_span_c) if full_after_c != full_text else losses
            if len(losses_c) < len(losses):
                # 커버리지 재시도가 소실을 줄였으면 채택(run 은 부차 — 정보 보존 우선)
                full_after, after_span, redet = full_after_c, after_span_c, redetect_span(after_span_c, run_threshold)
                losses = losses_c
                coverage_guard["losses_after"] = [l["sent"] for l in losses]
            if losses:
                # 여전히 소실 → 폴백. v2 는 v1(어미만) 로, v1 은 원문 유지.
                #   v1 은 문장 경계를 보존(어미만 변주)하므로 명사 소실이 구조적으로 불가 — 대개 loss-free.
                #   단 v1 도 실패/무변경이면(그 자체 내부 가드가 원문 유지) fb["changed"]=False → 원문으로 귀결.
                fb_ok = False
                if mode == "v2":
                    fb = rewrite_span_via_chassis(
                        generator, ontology, checker, chapter_no, full_text, dict(span),
                        run_threshold=run_threshold, directive=None, service=None, mode="v1")
                    if fb["changed"]:
                        fb_losses = coverage_loss_candidates(fb["span_text_before"], fb["span_text_after"])
                        if not fb_losses:
                            fb_ok = True
                            coverage_guard["fallback"] = "v1"
                            full_after, after_span = fb["full_after"], fb["span_text_after"]
                            redet = redetect_span(after_span, run_threshold)
                            losses = []
                if not fb_ok:
                    # v1 폴백이 없거나(=v1 모드) 실패/무변경/소실 → 원문 유지(소실 없음 보장)
                    coverage_guard["fallback"] = "original"
                    full_after, after_span = full_text, span_text
                    redet = redetect_span(span_text, run_threshold)
                    losses = []
                coverage_guard["losses_after"] = [l["sent"] for l in losses]
        coverage_guard["passed"] = (len(coverage_guard["losses_after"]) == 0)

    # G-B 사실 표면 비교(전체 텍스트 대 전체 텍스트) — 기존 _guardrail 계보 재사용(실 서비스 인스턴스)
    guardrail = None
    after_res = None   # CE-5: _guardrail 이 계산한 after 추출을 다음 스팬 before 로 스레딩(현행은 버려지던 값)
    if service is not None and checker is not None and before_res is not None and full_after != full_text:
        try:
            guardrail, after_res = service._guardrail(
                full_text, full_after, before_res, ids, ontology, checker, chapter_no)
        except Exception as e:
            guardrail = {"error": str(e)}

    return {
        "span_text_before": span_text,
        "span_text_after": after_span,
        "full_after": full_after,
        "redetect_before": redetect_span(span_text, run_threshold),
        "redetect_after": redet,
        "guardrail": guardrail,
        "coverage_guard": coverage_guard,
        "retried": retried,
        "changed": full_after != full_text,
        "kind": span["kind"], "sent_start": span["sent_start"], "sent_end": span["sent_end"],
        "n_sent": span["n_sent"], "mode": mode,
        "expanded": span.get("expanded", False), "n_para": span.get("n_para"),
        "char_start": span["char_start"], "char_end": span["char_end"],
        # CE-5(스레딩용 — 직렬화 대상 아님): 이 스팬이 쓴 before 추출·계산한 after 추출·roster(ids).
        #   호출부(humanize_pass)가 다음 스팬 before_res 로만 재활용한다(after_res 는 채택 시, before_res 는 미채택 시).
        "before_res": before_res, "after_res": after_res, "check_ids": ids,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 서비스 / CLI (offline 실측)
# ─────────────────────────────────────────────────────────────────────────────
def _svc():
    from novelcopilot.config import get_settings
    from novelcopilot.repository import FilesystemProjectRepository
    from novelcopilot.services import CopilotService
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    return CopilotService(s, repo), repo


def _chapter_text(repo, pid: str, chapter: int) -> tuple[str, object]:
    state = repo.get(pid)
    if not state:
        raise KeyError(pid)
    ch = state.chapter(chapter)
    if not ch:
        raise KeyError(chapter)
    return ch.text or "", state


def cmd_detect(pid: str, chapter: int, run_threshold: int, mode: str = "v1") -> int:
    _, repo = _svc()
    text, _state = _chapter_text(repo, pid, chapter)
    spans = extract_rhythm_spans(text, run_threshold=run_threshold)
    print(f"=== ST-11 창 추출 — {pid} ch{chapter} (run_threshold={run_threshold}, mode={mode}) ===")
    print(f"전체 지문: past_run_max={past_tense_run(text)['max_run']} "
          f"frag_ratio={fragment_ratio(text)['ratio']}")
    print(f"검출 스팬 {len(spans)}개:")
    for k, sp in enumerate(spans):
        win = expand_span_to_paragraphs(text, sp) if mode == "v2" else sp
        pl = build_rewrite_payload(text, win, mode=mode)
        note = (f" → 문단확장 {win.get('n_para')}문단"
                f"{' (확장됨)' if win.get('expanded') else ' (경계일치)'}") if mode == "v2" else ""
        print(f"\n[{k}] kind={sp['kind']} 문장인덱스 {sp['sent_start']}~{sp['sent_end']} "
              f"({sp['n_sent']}문장) metric={sp['metric']}{note}")
        print(f"    앞 앵커(read-only): {' | '.join(pl['before_anchor'])}")
        print(f"    ▼ 대상 창:\n    {win['span_text']}")
        print(f"    뒤 앵커(read-only): {' | '.join(pl['after_anchor'])}")
    return 0


def _select_spans(spans: list[dict], max_spans: int | None) -> list[dict]:
    """실측 예산 제한 — 최고 임팩트 스팬 선택(run 길이·파편 밀도 내림차순). None 이면 전부."""
    if max_spans is None or len(spans) <= max_spans:
        return spans
    def _impact(s):
        m = s.get("metric", {})
        return m.get("run_len", 0) + m.get("n_fragment", 0)
    picked = sorted(spans, key=lambda s: -_impact(s))[:max_spans]
    return sorted(picked, key=lambda s: s["sent_start"])   # 원문 순서로 복원


def cmd_rewrite(pid: str, chapter: int, run_threshold: int, max_spans: int | None = None,
                mode: str = "v2") -> int:
    """검출 창 재작성(기본 mode=v2 — ST-11c 표면 커버리지 가드 통과를 조건으로 v2 승격. v1 은 폴백)."""
    svc, repo = _svc()
    text, state = _chapter_text(repo, pid, chapter)
    sess = svc.sessions.get_or_create(state)
    generator = sess.bundle.generator
    ontology = sess.bundle.ontology
    checker = sess.bundle.checker
    all_spans = extract_rhythm_spans(text, run_threshold=run_threshold)
    spans = _select_spans(all_spans, max_spans)
    print(f"[st11] {pid} ch{chapter} 검출 스팬 {len(all_spans)}개 중 {len(spans)}개 재작성 "
          f"(mode={mode}, LLM {len(spans)}~{4*len(spans)}콜 — 커버리지 가드 재시도·폴백 포함)…", flush=True)

    results = []
    for k, sp in enumerate(spans):
        print(f"[st11] 창 {k} ({sp['kind']}, {sp['n_sent']}문장) 재작성…", flush=True)
        r = rewrite_span_via_chassis(generator, ontology, checker, chapter, text, sp,
                                     run_threshold=run_threshold, service=svc, mode=mode)
        results.append((sp, r))
        rb, ra = r["redetect_before"], r["redetect_after"]
        gp = (r["guardrail"] or {}).get("passed") if isinstance(r["guardrail"], dict) else None
        cg = r.get("coverage_guard") or {}
        cg_note = ""
        if cg.get("checked"):
            cg_note = (f" cov_passed={cg.get('passed')}"
                       f"{' 소실차단→'+str(cg.get('fallback')) if cg.get('fallback') else ''}"
                       f"{' (소실'+str(len(cg.get('losses_before', [])))+'건'+('재시도해소' if cg.get('retried_coverage') and cg.get('passed') and not cg.get('fallback') else '')+')' if cg.get('losses_before') else ''}")
        print(f"    run {rb['ending_run_max']}→{ra['ending_run_max']} "
              f"frag {rb['frag_ratio']}→{ra['frag_ratio']} "
              f"σ {rb['len_stdev']}→{ra['len_stdev']} "
              f"changed={r['changed']} retried={r['retried']} guardrail_passed={gp}{cg_note}", flush=True)

    # 전/후 리포트(md) — 회차 원본 무수정, 산출은 리포트만
    _write_report(pid, chapter, text, results, run_threshold, n_total_spans=len(all_spans), mode=mode)
    return 0


def _write_report(pid: str, chapter: int, orig_text: str, results, run_threshold: int,
                  n_total_spans: int | None = None, mode: str = "v1") -> None:
    ptr0 = past_tense_run(orig_text)
    fr0 = fragment_ratio(orig_text)
    span_line = (f"- 검출 스팬 {n_total_spans}개 중 {len(results)}개 재작성(실측 예산 제한)"
                 if n_total_spans and n_total_spans != len(results)
                 else f"- 검출 스팬 {len(results)}개")
    mode_note = ("문단 리팩토링(문장 경계 재구성 허용)" if mode == "v2"
                 else "스팬 재작성(문장 경계 보존)")
    L = [f"# ST-11 스팬 재작성 실측 — {pid} ch{chapter} (mode={mode})", "",
         "> 검출기 피드백 주입형 2패스(처방 랭킹 1위)의 오프라인 실측. 회차 원본 **무수정** — 산출은 이 리포트로만.",
         f"> 재작성 창 = {mode_note}. "
         f"검출: quality_gates.past_tense_run·fragment_ratio(run_threshold={run_threshold}). "
         f"재작성: harness.revise_prose(사실 불변 가드) + G-B 표면 비교.", "",
         "## 회차 전체 검출(재작성 전)", "",
         f"- 과거형 종결 최장 run: **{ptr0['max_run']}** (비율 {ptr0['ratio']}, {ptr0['n_past']}/{ptr0['n_sent']})",
         f"- 무동사 파편문 비율: **{fr0['ratio']}** ({fr0['n_fragment']}/{fr0['n_sent']}) — DP-9 판별축(대조 5.1%)",
         span_line, ""]
    for k, (sp, r) in enumerate(results):
        rb, ra = r["redetect_before"], r["redetect_after"]
        gd = r["guardrail"] if isinstance(r["guardrail"], dict) else {}
        para_note = (f", {r.get('n_para')}문단" if mode == "v2" and r.get("n_para") else "")
        L += [f"## 창 {k} — {sp['kind']} (문장 {sp['sent_start']}~{sp['sent_end']}, {sp['n_sent']}문장{para_note})", "",
              f"재검출: run {rb['ending_run_max']}→**{ra['ending_run_max']}** · "
              f"frag {rb['frag_ratio']}→**{ra['frag_ratio']}** · "
              f"문장길이 μ {rb['len_mean']}→{ra['len_mean']} σ {rb['len_stdev']}→**{ra['len_stdev']}** "
              f"(max {rb['len_max']}→{ra['len_max']}) · "
              f"changed={r['changed']} · retried={r['retried']} · "
              f"guardrail_passed={gd.get('passed')} (G_A={gd.get('G_A_passed')} G_B={gd.get('G_B_passed')})", ""]
        if gd.get("claim_changes"):
            L.append(f"⚠ claim_changes: {json.dumps(gd['claim_changes'], ensure_ascii=False)}")
        cg = r.get("coverage_guard") or {}
        if cg.get("checked"):
            lb = cg.get("losses_before") or []
            la = cg.get("losses_after") or []
            fb = cg.get("fallback")
            cg_line = (f"표면 커버리지 가드(ST-11c): passed=**{cg.get('passed')}** · "
                       f"소실 후보 {len(lb)}건→{len(la)}건"
                       + (f" · 커버리지 재시도={cg.get('retried_coverage')}" if lb else "")
                       + (f" · **폴백={fb}**" if fb else ""))
            L.append(cg_line)
            if lb:
                L.append(f"  - 재작성 결과에서 소실 감지된 원문 문장(재시도/폴백 전): "
                         + ", ".join(f"«{s}»" for s in lb))
            if la:
                L.append(f"  - ⚠ 최종 잔존 소실(가드 실패): " + ", ".join(f"«{s}»" for s in la))
            L.append("")
        L += ["**[전]**", "```", r["span_text_before"], "```",
              "**[후]**", "```", r["span_text_after"], "```", ""]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "st11_span_rewrite.md").write_text("\n".join(L), encoding="utf-8")
    print(f"[st11] 리포트: {REPORTS / 'st11_span_rewrite.md'}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# ST-11b 3원 대비 — 같은 검출 스팬에 대해 [원문 / v1(문장 유지) / v2(문단 리팩토링)] 나란히.
#   각 스팬을 v1(스팬 창)·v2(문단 창)로 각각 재작성해 세 텍스트를 대비. LLM 콜은 스팬당 최대 4(각 모드 최대 2).
#   질식 문단(사용자 지적 스팬 — '숨을 들이쉬려 했다…')은 반드시 포함되게 스팬 선택.
# ─────────────────────────────────────────────────────────────────────────────
CHOKE_NEEDLE = "숨을 들이쉬려 했다"   # 사용자 지적 질식 스팬 앵커(3원 대비 필수 포함)


def _select_spans_including_choke(text: str, spans: list[dict], max_spans: int) -> list[dict]:
    """임팩트 상위 max_spans 선택하되, 질식 문단(CHOKE_NEEDLE 포함) 스팬은 반드시 포함."""
    def _impact(s):
        m = s.get("metric", {})
        return m.get("run_len", 0) + m.get("n_fragment", 0)
    choke = [s for s in spans if CHOKE_NEEDLE in (s.get("span_text") or "")]
    # v1 스팬 경계가 질식 첫 문장을 놓칠 수 있으니, 문단 확장 창에 needle 이 들어오는 스팬도 포함
    if not choke:
        choke = [s for s in spans
                 if CHOKE_NEEDLE in (expand_span_to_paragraphs(text, s).get("span_text") or "")]
    picked = sorted(spans, key=lambda s: -_impact(s))[:max_spans]
    for c in choke:
        if c not in picked:
            picked.append(c)
    return sorted(picked, key=lambda s: s["sent_start"])


def cmd_compare(pid: str, chapter: int, run_threshold: int, max_spans: int = 2) -> int:
    """3원 대비 — 대표 스팬(질식 문단 필수 포함) 각각을 v1·v2 로 재작성해 [원문/v1/v2] 나란히.
    max_spans = 질식 외 임팩트 상위 스팬 수(질식은 항상 추가). LLM ~ (스팬수)×4 콜."""
    svc, repo = _svc()
    text, state = _chapter_text(repo, pid, chapter)
    sess = svc.sessions.get_or_create(state)
    generator = sess.bundle.generator
    ontology = sess.bundle.ontology
    checker = sess.bundle.checker
    all_spans = extract_rhythm_spans(text, run_threshold=run_threshold)
    spans = _select_spans_including_choke(text, all_spans, max_spans)
    print(f"[st11b] {pid} ch{chapter} 3원 대비 — 검출 {len(all_spans)}개 중 {len(spans)}개 "
          f"(질식 문단 필수) × (v1·v2) 재작성…", flush=True)

    rows = []
    for k, sp in enumerate(spans):
        is_choke = (CHOKE_NEEDLE in (sp.get("span_text") or "")
                    or CHOKE_NEEDLE in (expand_span_to_paragraphs(text, sp).get("span_text") or ""))
        tag = " [질식 문단]" if is_choke else ""
        print(f"[st11b] 스팬 {k} ({sp['kind']}, {sp['n_sent']}문장){tag} — v1…", flush=True)
        r1 = rewrite_span_via_chassis(generator, ontology, checker, chapter, text, sp,
                                      run_threshold=run_threshold, service=svc, mode="v1")
        print(f"[st11b] 스팬 {k}{tag} — v2(문단)…", flush=True)
        r2 = rewrite_span_via_chassis(generator, ontology, checker, chapter, text, dict(sp),
                                      run_threshold=run_threshold, service=svc, mode="v2")
        rows.append((sp, r1, r2, is_choke))
        ra1, ra2 = r1["redetect_after"], r2["redetect_after"]
        print(f"    v1 run→{ra1['ending_run_max']} σ={ra1['len_stdev']} | "
              f"v2 run→{ra2['ending_run_max']} σ={ra2['len_stdev']} "
              f"({r2.get('n_para')}문단 확장={r2.get('expanded')})", flush=True)

    _write_report_3way(pid, chapter, text, rows, run_threshold, n_total_spans=len(all_spans))
    return 0


def _write_report_3way(pid: str, chapter: int, orig_text: str, rows, run_threshold: int,
                       n_total_spans: int | None = None) -> None:
    ptr0 = past_tense_run(orig_text)
    fr0 = fragment_ratio(orig_text)
    L = [f"# ST-11b 문단 리팩토링 3원 대비 — {pid} ch{chapter}", "",
         "> 사용자 확정 설계(v1 한계 지적 기반): v1 은 어미를 고치나 하나의 연속 체험이 여러 스타카토 단문으로 남는다.",
         "> v2 = 재작성 창을 검출 스팬을 포함하는 **문단(들) 전체**로 확장(앞뒤 문단 read-only 앵커) + 문장 경계 재구성 전면 허용",
         "> (연속 체험이면 연결어미·종속절로 한 문장에 흘리되 강조 단문 한둘은 남김). 사실·화자 목소리 불변.", "",
         f"> 검출: quality_gates(run_threshold={run_threshold}). 재작성: harness.revise_prose(사실 불변 가드)+G-B 표면 비교.",
         "> 회차 원본 **무수정** — 산출은 이 리포트로만. σ = 지문 문장 길이(문자수) 표준편차(만연/강조 교차 지표, advisory).", "",
         "## 회차 전체 검출(재작성 전)", "",
         f"- 과거형 종결 최장 run: **{ptr0['max_run']}** (비율 {ptr0['ratio']}, {ptr0['n_past']}/{ptr0['n_sent']})",
         f"- 무동사 파편문 비율: **{fr0['ratio']}** ({fr0['n_fragment']}/{fr0['n_sent']}) — DP-9 판별축(대조 5.1%)",
         (f"- 검출 스팬 {n_total_spans}개 중 {len(rows)}개를 v1·v2 양쪽으로 재작성(질식 문단 필수 포함)"
          if n_total_spans else f"- {len(rows)}개 스팬 3원 대비"), ""]
    for k, (sp, r1, r2, is_choke) in enumerate(rows):
        rb = r1["redetect_before"]
        ra1, ra2 = r1["redetect_after"], r2["redetect_after"]
        g1 = r1["guardrail"] if isinstance(r1["guardrail"], dict) else {}
        g2 = r2["guardrail"] if isinstance(r2["guardrail"], dict) else {}
        tag = " — 🫁 질식 문단(사용자 지적)" if is_choke else ""
        L += [f"## 스팬 {k} — {sp['kind']} (문장 {sp['sent_start']}~{sp['sent_end']}, {sp['n_sent']}문장){tag}", "",
              "| | run | frag | 문장길이 μ/σ/max | changed | guardrail |",
              "|---|---|---|---|---|---|",
              f"| 원문 | {rb['ending_run_max']} | {rb['frag_ratio']} | "
              f"{rb['len_mean']}/{rb['len_stdev']}/{rb['len_max']} | — | — |",
              f"| v1(문장 유지) | {ra1['ending_run_max']} | {ra1['frag_ratio']} | "
              f"{ra1['len_mean']}/{ra1['len_stdev']}/{ra1['len_max']} | {r1['changed']} | "
              f"{g1.get('passed')} (A={g1.get('G_A_passed')} B={g1.get('G_B_passed')}) |",
              f"| v2(문단 리팩토링) | {ra2['ending_run_max']} | {ra2['frag_ratio']} | "
              f"{ra2['len_mean']}/{ra2['len_stdev']}/{ra2['len_max']} | {r2['changed']} | "
              f"{g2.get('passed')} (A={g2.get('G_A_passed')} B={g2.get('G_B_passed')}) |",
              f"\nv2 문단 확장: {r2.get('n_para')}문단 (확장됨={r2.get('expanded')})", ""]
        for gd, nm in ((g1, "v1"), (g2, "v2")):
            if gd.get("claim_changes"):
                L.append(f"⚠ {nm} claim_changes: {json.dumps(gd['claim_changes'], ensure_ascii=False)}")
        # [원문] 은 창 범위별로 정렬해 표시 — v1 스팬 창과 v2 문단 창은 범위가 다르므로(v2 확장)
        #   같은 [원문]을 쓰면 v2 창이 정당히 포함한 인접 원문 문장이 '발명'으로 오독된다(ST-11b MED 수리).
        #   v1 은 v1 스팬 원문과, v2 는 v2 문단 창 원문과 각각 대비.
        orig_v1 = r1["span_text_before"]
        orig_v2 = r2["span_text_before"]
        L += ["**[원문 — v1 스팬 창]**", "```", orig_v1, "```",
              "**[v1 — 문장 경계 유지]**", "```", r1["span_text_after"], "```"]
        if orig_v2 != orig_v1:
            L += ["**[원문 — v2 문단 창(확장)]** — v1 스팬보다 넓음(인접 문단 포함). "
                  "v2 [후]와 이 창을 대비해야 소실/발명을 정확히 판정.",
                  "```", orig_v2, "```"]
        L += ["**[v2 — 문단 리팩토링]**", "```", r2["span_text_after"], "```", ""]
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "st11b_paragraph.md").write_text("\n".join(L), encoding="utf-8")
    print(f"[st11b] 3원 대비 리포트: {REPORTS / 'st11b_paragraph.md'}", flush=True)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ST-11 스팬 재작성")
    ap.add_argument("cmd", choices=["detect", "rewrite", "compare"])
    ap.add_argument("pid")
    ap.add_argument("chapter", type=int)
    ap.add_argument("--run-threshold", type=int, default=6)
    ap.add_argument("--max-spans", type=int, default=None,
                    help="실측 예산 제한 — 최고 임팩트 N개만 재작성(rewrite=상한, compare=질식 외 스팬 수)")
    ap.add_argument("--mode", choices=["v1", "v2"], default="v2",
                    help="v2=문단 리팩토링(기본·ST-11c 커버리지 가드로 승격) · v1=스팬 재작성(문장 경계 유지·폴백). detect/rewrite 전용")
    args = ap.parse_args(argv)
    if args.cmd == "detect":
        return cmd_detect(args.pid, args.chapter, args.run_threshold, args.mode)
    if args.cmd == "compare":
        return cmd_compare(args.pid, args.chapter, args.run_threshold,
                           args.max_spans if args.max_spans is not None else 2)
    return cmd_rewrite(args.pid, args.chapter, args.run_threshold, args.max_spans, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
