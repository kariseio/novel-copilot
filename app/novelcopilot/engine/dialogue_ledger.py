# -*- coding: utf-8 -*-
"""DG-1/DG-4 — 인물별 대사 원장(관측 전용). 사용자 승인 설계(2026-08-10 · ID 는 PM 재배정 — DL-1 은 기점유).

집필은 불변이다(본문 출력 계약 무접촉). 회차 확정 후 관측 단계에서만 돈다:
  ① 대사 결정론 파싱 — 따옴표로 시작하는 행을 순번과 함께 추출(모델이 대사를 다시 쓰지 않는다).
  ② 화자 표기 콜 1회 — 회차 본문 + 번호 대사 목록만 주고, 각 대사의 화자를 **본문이 그 인물을 부르는
     표현**으로 받는다(이름 명시 인물=이름 · 무명 인물=본문의 지칭 그대로 · 서술자 발화='서술자').
     입력에 이름 명부를 싣지 않는다 — DG-1 검수에서 명부 메뉴가 프라이밍 소스로 실측됨(1화 본문에
     명부 11명 전원 등장 0인데 14줄이 명부 이름으로 강제 배분·오귀속 28.6%). 스토리가 답 공간이다.
  ③ 캐논 정합(결정론·LLM 0) — '서술자'는 POV 개체로, 지칭이 온톨로지 actor 의 이름·별칭과 일치하고
     **그 문자열이 회차 본문에 실제 등장**할 때만 캐논 승격(canon=True). 그 외 지칭은 그대로 보존하며
     canon=False — 명부는 입력 메뉴가 아니라 **출력 검증층**이다(본문 미등장 이름의 승격 거부).
     결측·판정 불가는 '미상' 정직. 실패는 비차단(빈 원장+가시화).

**생성 입력 주입 금지** — 이 원장은 순수 관측(측정→가시화→작가)이다. 대사 전량을 프롬프트에 넣는 것은
AP-1 이 차단한 시연 재주입 루프의 대형판이므로, 소비처는 검증(어체 분포 밥상)·계측·열람에 한정한다.
지칭→인물 연결의 승격은 작가 확정·별칭 등록 계보로만(자동 추론 승격 금지 — 확신 오신호 방지).
"""
from __future__ import annotations
from ..llm import promptlog   # XR-3: consumer 태그(관측 전용 — 위임·바이트 불변)

import re

# 대사 행 판정: 조판 규칙상 대사는 단독 문단("한 줄에 하나")이므로 따옴표로 시작하는 행을 대사로 본다.
_QUOTE_OPEN = ('"', '“', "'", "‘")
_INNER = re.compile(r'^[“"\'‘]\s*(.+?)\s*[”"\'’]?\s*$')

UNKNOWN = "미상"
NARRATOR = "서술자"


def extract_quotes(text: str) -> list[str]:
    """결정론 대사 추출 — 따옴표 시작 행의 내부 텍스트(순서 보존). 파싱만, 재작성 0."""
    out: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or not s.startswith(_QUOTE_OPEN):
            continue
        m = _INNER.match(s)
        out.append((m.group(1) if m else s).strip())
    return out


@promptlog.stage("dialogue_ledger")
def attribute_speakers(provider, text: str, quotes: list[str]) -> list[str]:
    """화자 표기 콜 1회 — 스토리 네이티브 지칭. 반환은 quotes 와 같은 길이(결측='미상').

    입력에 이름 명부 없음(DG-4 — 메뉴 프라이밍 소스 차단). 캐논 정합은 resolve_speakers 가 결정론으로.
    provider: aux(기계 스테이지) provider — chat_json. 실패 시 예외를 올린다(호출부가 비차단 처리).
    """
    numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(quotes))
    res = provider.chat_json(
        [{"role": "system", "content":
          "너는 소설 본문의 대사 화자 표기만 한다. 본문을 읽고 번호 매긴 각 대사를 누가 말했는지 답하라. "
          "화자 표기는 본문이 그 인물을 부르는 표현에서 그대로 가져온다 — 이름이 명시된 인물은 그 이름, "
          "이름 없는 인물은 본문의 지칭(역할·외양 표현), 서술자('나')의 발화는 '서술자'. "
          "표기를 정할 수 없으면 '미상'. 대사 원문은 그대로 둔다. "
          'JSON 만: {"speakers": {"1": "표기", "2": "표기", ...}}'},
         {"role": "user", "content":
          f"[대사 목록]\n{numbered}\n[본문]\n{text}"}],
        temperature=0.0)
    raw = res.get("speakers") or {}
    out: list[str] = []
    for i in range(len(quotes)):
        name = str(raw.get(str(i + 1), "") or "").strip()
        out.append(name or UNKNOWN)
    return out


def resolve_speakers(designations: list[str], text: str, ontology,
                     pov_entity_id: str = "") -> list[tuple[str, bool]]:
    """지칭 → 캐논 정합(결정론·LLM 0). 반환 [(speaker, canon)].

    승격 규칙(둘뿐 — 그 외 지칭은 보존·canon=False):
      · '서술자' → POV 개체 이름(CX-4 pov_entity_id 배선 재사용). POV 미상이면 '서술자' 보존.
      · 지칭 == actor 정확 이름 또는 **이름-조각 별칭**(별칭 ⊂ 이름·2자 이상 — '지연'⊂'하지연')이고
        그 문자열이 본문에 실제 등장 — 본문 등장 요건이 모델 prior 로 새어 들어온 이름의 승격을
        차단한다(1화 '정문규' 오염의 소스 차단). 역할·외양 별칭('영감'·'정보상'·'우유 사던 사람' 류)은
        승격 재료에서 제외한다 — 같은 역할의 다른 인물과 교차 충돌하는 오승격 실측(1화 원세계
        잡화상 영감이 별칭 '잡화상 영감'으로 헐값에 승격). 그 지칭은 canon=False 로 정직 보존.
    """
    canon_map: dict[str, str] = {}
    for e in ontology.entities.values():
        if not ontology.is_actor(getattr(e, "etype", "")):
            continue
        name = getattr(e, "name", "")
        # 이름-조각 별칭만 승격 재료(별칭이 이름의 부분 문자열·2자 이상) — 역할 별칭 제외(교차 정체성 차단)
        cands = [name] + [a for a in (getattr(e, "aliases", None) or [])
                          if a and len(a) >= 2 and a != name and a in name]
        for n in cands:
            # sentinel 보호: '미상'·'서술자' 동명 개체는 대조에서 제외(승격 재료로 쓰지 않는다)
            if n and n not in (UNKNOWN, NARRATOR) and n in (text or ""):
                canon_map[n] = e.name
    pov_name = ""
    if pov_entity_id:
        pe = ontology.entities.get(pov_entity_id)
        pov_name = getattr(pe, "name", "") if pe is not None else ""
    out: list[tuple[str, bool]] = []
    for d in designations:
        s = (d or "").strip()
        # 형식 위생 캡: 개행 포함·40자 초과 표기는 미상 강등(모델이 설명문을 반환하는 스키마 오염 대비)
        if not s or s == UNKNOWN or "\n" in s or len(s) > 40:
            out.append((UNKNOWN, False))
        elif s == NARRATOR:
            out.append((pov_name, True) if pov_name else (NARRATOR, False))
        elif s in canon_map:
            out.append((canon_map[s], True))
        else:
            out.append((s, False))
    return out


def build_ledger(provider, text: str, ontology, bus=None, chapter: int = 0,
                 pov_entity_id: str = "", tagged: list[tuple[str, str]] | None = None) -> list[dict]:
    """확정 후 관측 진입점 — [{idx, speaker, text, canon}] 반환. 실패는 빈 리스트(비차단·가시화).

    tagged(VX-1): 대사 태그 디태거(textfmt.lower_dialogue_tags)가 뽑은 (화자, 대사) 쌍. 주어지면 화자를
    **태그에서 직접** 취해 attribute_speakers LLM 귀속 콜을 건너뛴다(미상·오귀속 소멸 — 태그가 화자 근거).
    None(태그 없는 작품·미준수 폴백)이면 종전 경로(따옴표 파싱 + LLM 귀속) 그대로 — 하위호환.
    canon 정합은 두 경로 공통으로 resolve_speakers 가 결정론으로 판정한다(본문 등장 요건 유지)."""
    if tagged is not None:
        # 태그 유래: 대사=태그 내부 텍스트(따옴표 벗김), 지칭=태그 화자(공백=미상). LLM 콜 0.
        quotes = [(_INNER.match(q).group(1) if _INNER.match(q) else q).strip() for (_, q) in tagged]
        designations = [(sp or UNKNOWN) for (sp, _) in tagged]
        if not quotes:
            return []
    else:
        quotes = extract_quotes(text)
        if not quotes:
            return []
        try:
            designations = attribute_speakers(provider, text, quotes)
        except Exception as e:
            if bus is not None:
                bus.emit("dialogue_ledger", "attribution_failed", chapter=chapter, error=type(e).__name__)
            return []
    resolved = resolve_speakers(designations, text, ontology, pov_entity_id=pov_entity_id)
    # designation = 승격 전 모델 원 표기(감사 근거·재정합 재료 — 승격 시 speaker 와 달라진다)
    ledger = [{"idx": i + 1, "speaker": sp, "text": q, "canon": canon, "designation": d.strip()}
              for i, (q, (sp, canon), d) in enumerate(zip(quotes, resolved, designations))]
    if bus is not None:
        unk = sum(1 for r in ledger if r["speaker"] == UNKNOWN)
        ncanon = sum(1 for r in ledger if r["canon"])
        bus.emit("dialogue_ledger", "built", chapter=chapter, quotes=len(ledger),
                 unknown=unk, canon=ncanon)
    return ledger


# ── 어체 분포(결정론 계수 — 검증 밥상용, 판정 없음) ──
def ending_class(quote: str) -> str:
    """대사 한 줄의 종결 어체 분류(휴리스틱·advisory). 말끝 문장부호 제거 후 어미 검사."""
    s = re.sub(r"[.?!…—]+$", "", (quote or "").strip())
    if not s:
        return "기타"
    if re.search(r"(습니다|ㅂ니다|십시오|습니까|ㅂ니까|십니까)$", s) or s.endswith(("니다", "니까")):
        return "합쇼체"
    if s.endswith(("시오", "겠소", "았소", "었소", "이오", "하오", "그렇소")) or re.search(r"[^습]시오$|[가-힣]소$|[가-힣]오$", s):
        return "하오체"
    if s.endswith("요"):
        return "해요체"
    return "반말/기타"


def speaker_profile(ledger: list[dict]) -> dict[str, dict[str, int]]:
    """화자별 어체 분포 {이름: {어체: 건수}} — verify_chapter 밥상 원자료(판정은 사람)."""
    prof: dict[str, dict[str, int]] = {}
    for row in ledger or []:
        sp = row.get("speaker") or UNKNOWN
        cls = ending_class(row.get("text") or "")
        prof.setdefault(sp, {})
        prof[sp][cls] = prof[sp].get(cls, 0) + 1
    return prof
