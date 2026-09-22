# -*- coding: utf-8 -*-
"""출력 경계 텍스트 정규화.

설계 계약: 저장 본문(ChapterRecord.text)은 *canonical*(모델이 내는 마크다운 결 — #제목·**강조**·---구분·&nbsp; 스페이서)로 두고,
사람이 보는 *경계마다* 렌더한다. 리더(web/app.js mdToHtml)는 이미 그렇게 한다. 이 모듈은 그 리더와 같은 어휘를
백엔드 경계(.txt 내보내기, 직전 회차 재주입)에서 일관 적용한다 — 본문 삭제(스트립)가 아니라 *표현 정규화*.
  · 줄표 런(——/———) → 단일 em-dash(—)   : 문체 틱을 '한 개로 렌더'(두더지 스트립 아님)
  · 마크다운 표식 → 평문                  : .txt 경계에서만(리더·.md 는 마크다운이 유효)
직전 회차를 다음 프롬프트로 재주입할 때 줄표 런을 정규화하면, 모델이 자기 틱을 *교재로 안 보게* 되어 소스(피드백 루프)가 끊긴다.
"""
from __future__ import annotations
import html
import re

_DASH_RUN = re.compile(r"—{2,}")          # 2개 이상 연속 em-dash
_HR_LINE = re.compile(r"^([-*_]\s*){3,}$")     # 마크다운 구분선
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
_ITALIC = re.compile(r"(^|[^*])\*([^*\n]+?)\*(?!\*)")
_NBSP_LINE = {"&nbsp;", "&nbsp"}


def collapse_dashes(text: str) -> str:
    """——/———(연속 줄표) → 단일 —. '삭제'가 아니라 정규 표기 한 개로 렌더."""
    return _DASH_RUN.sub("—", text or "")


def strip_structural_markup(text: str) -> str:
    """발행/재주입 경계의 '표지성' 구조 마크업만 결정론 정규화 — 라인 단위·위치 기반(의미 판정 아님, 두더지 무저촉).
    표현 마크업(**강조**·*em*·본문 중간 --- 구분선)과 diegetic 본문(시스템 괄호 '(오류:…)'·서사 괄호)은 절대 불변(B-24).
      · 선행 표지 블록: 본문 맨 위의 연속된 #제목/&nbsp;/구분선/빈 줄을 제거 → 이야기 첫 문장부터 시작.
        (모델이 초반 회차를 '웹소설 포스트'로 포맷하며 등록 제목과 다른 자작 표제를 line0 에 얹는 소스 — B-35 3작 ch1~3 실측.
         표제는 등록 제목 필드가 SSOT 라 본문에 별도 표지 줄이 있을 자리가 없다.)
      · 단독 &nbsp; 스페이서 줄 → 빈 줄: 리터럴 엔티티 제거·문단 간격은 빈 줄로 보존(직전 회차 재주입 시 자기 litter 재학습 루프도 차단).
      · 줄표 런(——) → 단일 em-dash.
    선행 표지 제거가 본문을 통째로 비우면(전량이 표지) 원문을 그대로 둔다(본문 파괴 가드)."""
    lines = (text or "").split("\n")
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s == "" or s in _NBSP_LINE or _HEADING.match(s) or _HR_LINE.match(s):
            i += 1
        else:
            break
    body = lines[i:]
    if not any(ln.strip() for ln in body):   # 표지만 있고 본문 없음(degenerate) → 원문 보존(파괴 가드)
        return text
    out = ["" if ln.strip() in _NBSP_LINE else ln for ln in body]   # 본문 중간 단독 스페이서 줄 → 빈 줄
    return collapse_dashes("\n".join(out))


# ── VX-1: 대사 태그 디태거(생성 경계 — 저장·하류 처리·리더 앞) ─────────────────────────
# structured_prompt 작품은 대사를 <대사 화자="이름">…말…</대사> 로 출력한다. 저장 본문(canonical)·
# 하류 처리(시제·틱·조판·요약·누출 스윕·RAG)·리더는 전부 평문 대사(따옴표)를 가정하므로, 생성 직후
# 이 함수가 태그를 정규 대사로 '낮춘다' — 삭제가 아니라 표기 하강(내용 무손실). 부수로 (화자, 대사) 쌍을
# 순서대로 뽑아 대사 원장이 LLM 귀속 콜 없이 화자를 확정하게 한다(dialogue_ledger.build_ledger tagged).
# 비정형·미종결 마커는 무조건 제거해 ⓐ 리터럴 노출(리더 mdToHtml _mdEsc 가 꺾쇠를 이스케이프해 글자로 뜸)
# ⓑ 다음 화 재주입 앵커 오염(strip_structural_markup 이 대시·nbsp 에 대해 닫은 self-history 루프의 새 채널,
# B-35 계보)을 원천 차단한다. structured_prompt OFF 작품 본문엔 태그가 없으므로 이 함수는 no-op(무변경).
_DIALOGUE_TAG = re.compile(r'<\s*대사(?:\s+화자\s*=\s*"([^"\n]*)")?[^>]*>(.*?)<\s*/\s*대사\s*>', re.S)
_DIALOGUE_TAG_MARK = re.compile(r'<\s*/?\s*대사(?:\s[^>\n]*)?\s*>')
_LEAD_QUOTE = ('"', '“', "'", "‘")


def lower_dialogue_tags(text: str):
    """<대사 화자="이름">…</대사> → 정규 대사(따옴표) + (화자, 대사) 쌍 목록. 반환 (clean_text, pairs).
    화자 속성 부재=''(원장에서 '미상' 강등). 잔여(미매칭·미종결) 마커는 무조건 제거."""
    pairs: list[tuple[str, str]] = []

    def _repl(m: "re.Match[str]") -> str:
        speaker = (m.group(1) or "").strip()
        inner = (m.group(2) or "").strip()
        if inner and inner[0] in _LEAD_QUOTE:
            quoted = inner                     # 모델이 이미 따옴표를 붙인 경우 중복 방지
        elif inner:
            quoted = f'"{inner}"'
        else:
            quoted = ""
        pairs.append((speaker, quoted))
        return quoted

    out = _DIALOGUE_TAG.sub(_repl, text or "")
    out = _DIALOGUE_TAG_MARK.sub("", out)      # 미매칭 잔여 마커(미종결·중첩) 안전 제거 — 리터럴 노출 0 보증
    return out, pairs


def dialogue_tag_residue(text: str) -> int:
    """발행 경계 결정론 검사 — 본문에 남은 대사 태그 마커 수(계약값 0). 디태거 통과분은 항상 0."""
    return len(_DIALOGUE_TAG_MARK.findall(text or ""))


# ── ST-3: 문단 조판(내용 무손실 순수 분할) ──────────────────────────────────────────
# 실측 근거(st1b_reference_style): 실작품 문단당 1.7~2.0문장 vs 우리 3.3 — '가벼움'은 짧은 문장이 아니라
# 짧은 문단에서 온다. 발행 경계에서 (①)3문장 초과 지문 문단을 문장 경계로 1~2문장씩 분할, (②)지문과 한 문단에
# 섞인 대사 라인을 단독 문단으로 분리한다. 재배열·삭제·병합 없이 '분할만'(비공백 문자 집합·순서 불변).
# 판정기 아님·자동 재작성 아님 — 조판(줄바꿈)일 뿐. 문장 경계는 리포 정합(ai_tell_profile / lightness_metrics 의
# `[.!?…]+공백`)이되 '보호 스팬' 내부 종결부호로는 오분할하지 않는다(대사 '"안녕. 반가워."' = 한 문장).
# 보호 스팬 = 겹따옴표 대사 + diegetic 괄호/브래킷(B-24 '절대 불변') + 강조 마크업 — 이 안이면 문장 경계·마커 고아화를 원천 차단.
# 또한 내부 개행(\n)이 있는 세그먼트(운문·목록·상태창·행별 대사)는 이미 줄 구조가 있으므로 재조판 대상에서 제외(병합·빈 줄 정책 변경 금지).
_SENT_END = ".!?…"
_QUOTE_SPAN = re.compile(r'["“][^"”\n]+["”]')   # 대사 스팬(직선·굽은 겹따옴표) — lightness_metrics._QUOTE 와 동일 어휘
# 보호 스팬(내부 [.!?…]는 문장 경계 아님 — 스팬을 통째로 한 문장에 보존): 대사·시스템창·서사괄호·전각브래킷·볼드·이탤릭.
#   "안녕. 반가워." · [레벨업! 힘 +5.] · (퀘스트 완료. 보상.) · 【알림. 확인.】 · **중요. 필독.** · *속삭임. 조용히.*
# _QUOTE_SPAN(겹따옴표)와 정합하되 괄호/브래킷/강조까지 대칭 확장 — 스팬 밖의 종결부호만 경계가 된다(마스크 안이면 무효).
_PROTECT_SPANS = re.compile(
    r'"[^"\n]*"'                                        # 직선 겹따옴표 대사
    r'|[“”][^“”\n]*[“”]'  # 굽은 겹따옴표 대사
    r'|\([^()\n]*\)'                                    # 서사/시스템 괄호
    r'|\[[^\[\]\n]*\]'                                  # 시스템 브래킷 [상태창]
    r'|【[^【】\n]*】'                                    # 전각 브래킷
    r'|\*\*[^*\n]+\*\*'                                 # 강조(볼드)
    r'|\*[^*\n]+\*'                                     # 강조(이탤릭)
)
_PARA_SPLIT = re.compile(r"(\n\s*\n)")          # 빈 줄 경계(구분자 캡처 → 미변경 문단은 바이트 보존)


def _split_sentences(para: str) -> list[str]:
    """문단 1개를 문장 리스트로(보호-스팬 인식 결정론 분할). 각 문장은 strip 되지만 비공백 문자는 전부·순서대로 보존.
    경계 = 보호 스팬(_PROTECT_SPANS) 밖의 종결부호([.!?…]) 뒤 공백/문단끝. 스팬 내부 종결부호는 경계 아님(오분할 방지).
    개행(\\n)은 경계로 삼지 않는다 — 줄 구조가 있는 세그먼트는 상위(_reflow_paragraph)에서 아예 재조판 대상에서 제외한다(병합 금지).
    보호 스팬은 대사뿐 아니라 diegetic 괄호/브래킷(B-24)·강조까지 포함 — 마스크 위치를 미리 계산해 그 안에선 절대 자르지 않는다."""
    protected = bytearray(len(para))
    for m in _PROTECT_SPANS.finditer(para):              # 보호 스팬 위치 마스크(대사·괄호·브래킷·강조)
        protected[m.start():m.end()] = b"\x01" * (m.end() - m.start())
    sents: list[str] = []
    buf: list[str] = []
    i, n = 0, len(para)
    while i < n:
        ch = para[i]
        buf.append(ch)
        if not protected[i] and ch in _SENT_END:
            j = i + 1
            if j >= n or para[j].isspace():     # 종결부호 뒤 공백/문단끝일 때만(=정규식 `\s+` 정합)
                s = "".join(buf).strip()
                if s:
                    sents.append(s)
                buf = []
                while j < n and para[j].isspace():   # 경계 공백 run 소거(어느 문장에도 안 들어감 = 정규식과 동일)
                    j += 1
                i = j
                continue
        i += 1
    tail = "".join(buf).strip()
    if tail:
        sents.append(tail)
    return sents


def _reflow_paragraph(seg: str, narr_chunk: int = 2):
    """문단 1개 → 재조판된 문단 리스트. 변경 없음이면 None(호출부가 원문 세그먼트를 바이트 그대로 유지).
    이미 줄 구조가 있는 세그먼트(내부 개행 포함 — 운문·목록·상태창·행별 대사)는 재조판 대상에서 제외 → 병합·빈 줄 정책 변경 원천 차단(MED-1).
    트리거: (①) 순지문 문장 수가 허용 상한을 넘거나, 또는 (②) 대사·지문이 한 문단에 혼재. 그 외는 무변경.
    지문 run 은 narr_chunk 문장씩 묶고, 대사 문장은 각각 단독 문단으로 — 재배열·삭제·병합 없이 문장 경계에만 문단 구분 삽입.

    VP-1(2026-08-13 적대 리뷰 치명 2): 고정 2-stride 가 '2문장 문단 벽'의 결정론 소스였음이 실측돼
    narr_chunk 를 데이터 노브(StyleSpec.paragraph_max_sents)로 승격. 기본 2 = 종전과 바이트 동일(하위호환).
    순지문 무변경 문턱도 상한을 따른다(narr_chunk=4면 4문장 순지문 문단은 그대로) — 멱등 유지."""
    narr_chunk = max(2, int(narr_chunk or 2))
    if "\n" in seg.strip():                      # 내부 개행 = 작가/모델이 의도한 줄 구조 → 재조판 안 함(줄→공백 병합·문단 삽입 금지)
        return None
    sents = _split_sentences(seg)
    if len(sents) <= 1:                          # 단문(또는 빈 세그먼트) → 쪼갤 것 없음
        return None
    is_dlg = [bool(_QUOTE_SPAN.search(s)) for s in sents]
    has_dlg = any(is_dlg)
    has_narr = any(not d for d in is_dlg)
    if len(sents) <= max(3, narr_chunk) and not (has_dlg and has_narr):   # 상한 이내 순지문/순대사 블록 → 무변경
        return None
    out: list[str] = []
    narr: list[str] = []

    def _flush():
        # RF-1(2026-07-16): 고정 2-stride 가 자문자답 한 호흡("예산? / 없다.")을 문단 경계로 찢던 계통
        #   결함(5작품 실측 — 재실현 승자는 멀쩡했고 조판이 절단) 수리. 불변식: 지문 run 안에서 '?'로
        #   끝나는 문장은 바로 뒤 지문 문장과 항상 같은 출력 문단에 든다(질문-답 커플릿 응집). 좌→우
        #   greedy 로 1~2문장씩 묶되, 다음 문장이 '?'-종결이고 그 뒤에 답(비질문 지문)이 있으면 현재
        #   청크를 먼저 닫고 질문+답을 2문장 커플릿으로 묶는다 — 문단은 여전히 ≤2문장(조판 계약 불변),
        #   무손실·멱등(커플릿 문단은 ≤3 순지문이라 재조판 제외).
        i = 0
        chunk: list[str] = []
        n = len(narr)
        while i < n:
            s = narr[i]
            nxt_is_qa = (s.rstrip().endswith("?") and i + 1 < n
                         and not narr[i + 1].rstrip().endswith("?"))
            if nxt_is_qa:
                if chunk:
                    out.append(" ".join(chunk))
                    chunk = []
                out.append(" ".join(narr[i:i + 2]))   # 질문+답 커플릿
                i += 2
                continue
            chunk.append(s)
            if len(chunk) == narr_chunk:
                out.append(" ".join(chunk))
                chunk = []
            i += 1
        if chunk:
            out.append(" ".join(chunk))
        narr.clear()

    for s, d in zip(sents, is_dlg):
        if d:
            _flush()
            out.append(s)                         # 대사 라인 단독 문단화
        else:
            narr.append(s)
    _flush()
    return out


def reflow_paragraphs(text: str, narr_chunk: int = 2) -> str:
    """발행 경계 문단 조판(ST-3). 빈 줄로 구분된 문단마다 _reflow_paragraph 적용, 구분자는 원형 보존.
    미변경 문단은 세그먼트 원문 그대로 이어붙여(변경분만 재조판) 무변경 입력은 사실상 원형 유지. 내용 무손실(비공백 불변).
    narr_chunk: 지문 문단 최대 문장 수(VP-1 데이터 노브 — StyleSpec.paragraph_max_sents). 기본 2=종전 바이트 동일."""
    if not text:
        return text
    parts = _PARA_SPLIT.split(text)
    out: list[str] = []
    for idx, seg in enumerate(parts):
        if idx % 2 == 1:                          # 캡처된 빈 줄 구분자 → 원형 보존
            out.append(seg)
            continue
        reflowed = _reflow_paragraph(seg, narr_chunk=narr_chunk)
        out.append(seg if reflowed is None else "\n\n".join(reflowed))
    return "".join(out)


def md_to_plain(text: str) -> str:
    """canonical 마크다운 결 본문 → 순수 평문(.txt 내보내기 경계). 리더 mdToHtml 와 같은 어휘를 전부 처리.
    단독 &nbsp; 줄→빈 줄, 인라인 엔티티 해제, #제목표식 제거(텍스트 유지), **강조**/*강조* 표식 제거,
    ---/***/___ 구분선→빈 줄, —— → —. (특정 토큰만 잡지 않으므로 다음 마크다운 누수도 자동 커버 — 두더지 회피)"""
    out: list[str] = []
    for raw in (text or "").split("\n"):
        s = raw.rstrip()
        st = s.strip()
        if st in _NBSP_LINE:                 # 단독 스페이서 줄 → 빈 줄
            out.append("")
            continue
        if _HR_LINE.match(st):               # 구분선 → 빈 줄
            out.append("")
            continue
        m = _HEADING.match(st)               # 제목 표식 제거(텍스트 보존)
        if m:
            s = m.group(2)
        s = _BOLD.sub(r"\1", s)
        s = _ITALIC.sub(r"\1\2", s)
        out.append(s)
    txt = html.unescape("\n".join(out))      # &nbsp;·&amp;·&lt; 등 엔티티 일괄 해제(nbsp 만 좁혀 잡지 않음)
    txt = txt.replace(" ", " ")          # 해제된 비분리공백 → 일반 공백(평문 친화)
    return collapse_dashes(txt)
