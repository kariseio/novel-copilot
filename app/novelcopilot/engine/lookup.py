# -*- coding: utf-8 -*-
"""AG-1 T2 — 캐논 조회 툴(에이전틱 pull 의 손). 결정론·LLM 0콜.

집필 모델이 글을 만들다 사실이 필요할 때 부르는 단일 조회 진입점. 설계 계약(보드 AG-1):
· 개체 해소는 정확 일치(이름·별칭)만 자동 — 실패 시 부분 일치 **후보 목록만** 반환(자동 선택 금지,
  교착어 함정). · 개체 히트 = 이 화 시점 전 속성(state_as_of) + 비구속 관측 표기 + 관계 1홉.
· 키워드 히트 = bible(keywords·title) 상위 2건. · **미등재는 "미등재"로 정직 반환** — 이 한 줄이
  발명 차단의 본체다(없는 사실을 있는 것처럼 돌려주지 않는다).
· RAG 프로즈 원문 검색은 조회 대상이 아니다(verbatim 자기앵커 계보 + embed 콜 금지 계약)."""
from __future__ import annotations

TOOL_SCHEMA = [{
    "name": "lookup_canon",
    "description": ("작품 캐논 조회: 인물·물건·건(사건)·세계 상수(물가·제도)의 확정 상태와 설정 지식을 "
                    "돌려준다. 돈·재고·경력·시세·규칙·과거 상태 등 사실이 필요한 순간마다 조회하고, "
                    "돌아온 값 그대로 쓴다. '미등재'가 오면 그 사실은 캐논에 없는 것이다."),
    "input_schema": {"type": "object",
                     "properties": {"query": {"type": "string",
                                              "description": "개체 이름·별칭 또는 키워드(예: 서준호, 라면 값, 통금, 규격지)"}},
                     "required": ["query"]},
}]


def _voice_trim(v: str, cap: int = 600) -> str:
    """CX-11: 보이스 카드 위생 상한 — cap 이내면 전문, 넘으면 마지막 문장 경계에서 끊는다(중간 절단 금지)."""
    if len(v) <= cap:
        return v
    cut = v[:cap]
    j = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    return cut[:j + 1] if j >= 40 else cut


class CanonLookup:
    """조회 실행기 — harness 가 (ontology, state, chapter) 로 만들어 tool_handler 로 넘긴다.
    모든 호출을 self.log 에 남긴다(T5: gen_context 영속 + T7 검증 대조 입력)."""

    def __init__(self, ontology, bible_entries, chapter: int, ledgers=None):
        self.ont = ontology
        self.bible_entries = list(bible_entries or [])
        self.chapter = int(chapter)
        # DG-6: 선행 확정 회차의 대사 원장 [(회차번호, rows)] — 관계 어체 실물 앵커의 선별 인덱스.
        #   미제공(None)=기존 응답 바이트 동일(하위호환).
        self.ledgers = list(ledgers or [])
        self.log: list[dict] = []

    # ---- 내부 ----
    def _alias_map(self) -> dict[str, str]:
        amap: dict[str, str] = {}
        for eid, e in self.ont.entities.items():
            for a in [getattr(e, "name", "")] + list(getattr(e, "aliases", None) or []):
                a = (a or "").strip()
                if a:
                    amap.setdefault(a, eid)
        return amap

    def _entity_payload(self, eid: str) -> str:
        e = self.ont.entities.get(eid)
        parts = [f"[{getattr(e, 'name', eid)}]"]
        # CX-2/CX-3: 노출 등급·채널 일관을 온톨로지 단일 질의점(public_attrs)에 위임 —
        #   internal 계측 축 비노출 + binding 있으면 binding 만(푸시 채널과 값 모순 소스 차단).
        for a, v, is_binding in self.ont.public_attrs(eid, self.chapter):
            tag = "" if is_binding else "(비구속 관측)"
            parts.append(f"{self.ont.vocab.label(a)}={v}{tag}")
        # CX-3: 인물이 조회되면 보이스 카드가 따라온다(기승인 설계) — 어체·결이 사실과 함께 단일 응답으로.
        # CX-11: 카드는 전문 동봉 — 구 160자 절단이 다항 카드를 문장 중간에서 끊어 ③④가 조회 경로에서
        #   증발했다(서준호 425자 실측). 위생 상한만 문장 경계로 남긴다.
        voice = (getattr(e, "voice", "") or "").strip()
        if voice and self.ont.is_actor(getattr(e, "etype", "")):
            # VL-1: 조회 응답이 '절대 위반 금지' 헤더 밑에 실리므로 참조 층위를 필드 라벨로 명시(데이터 축소 0 — DG-6 불변)
            # VH-1(2026-08-16 감사 F-1): 구 라벨 "지금 하는 말과 행동으로 드러낸다"는 조건부 카드의 조건절을
            #   무조건 실행으로 승격시켰다(고권위 블록이라 draft 헤더 계약을 이긴다) — '해당 대목에서'로 층위 정합.
            parts.append(f"보이스(참조 전용. 카드에 적힌 대로, 이번 장면에 해당하는 대목에서 말과 행동으로 드러낸다)={_voice_trim(voice)}")
            ex = self._recent_exchange(eid)
            if ex:
                parts.append(ex)
        hops = []
        for ed in getattr(self.ont, "edges", None) or []:
            if ed.src_id == eid or ed.dst_id == eid:
                other = ed.dst_id if ed.src_id == eid else ed.src_id
                oe = self.ont.entities.get(other)
                if oe is not None and ed.eff_from <= self.chapter:
                    hops.append(f"{self.ont.rel_spec(ed.rel_id).label}:{getattr(oe, 'name', other)}")
        if hops:
            parts.append("관계=" + ", ".join(sorted(set(hops))[:6]))
        return " · ".join(parts)

    def _recent_exchange(self, eid: str) -> str:
        """DG-6: 관계 어체 실물 앵커 — 이 인물이 낀 가장 최근 확정 대화의 짧은 원문(상대 발화 포함).
        어체는 라벨이 아니라 실물 문장에만 실린다(슬롯 실험 3변형 실측 + ST-12 '지시는 앵커를 못 이김').
        DG-1 카브아웃 유지 조건(보드 DG 섹션 SSOT): ⒜화자 라벨 미동봉 — 원장 귀속은 선별 인덱스로만 쓰고
        생성면에 노출하지 않는다(잔여 오귀속이 '확신 있는 오신호'로 승격되는 소스 차단) ⒝고정 프레임을 뺀
        주입 바이트 전량이 확정 회차 본문의 부분 문자열. canon 행만 신뢰, 줄 60자·총 3줄 상한(B-32e 완충).
        원장 없으면 ""(하위호환)."""
        e = self.ont.entities.get(eid)
        names = {n.strip() for n in [getattr(e, "name", "")] + list(getattr(e, "aliases", None) or []) if n}

        def _short(r) -> bool:
            return bool(r.get("canon")) and 0 < len(str(r.get("text", ""))) <= 60

        for ch_no, rows in sorted(self.ledgers, key=lambda t: t[0], reverse=True):
            if int(ch_no) >= self.chapter:
                continue
            rows = list(rows or [])
            for i in reversed(range(len(rows))):
                if not (_short(rows[i]) and str(rows[i].get("speaker", "")) in names):
                    continue
                win = [w for w in rows[max(0, i - 1):i + 2] if _short(w)][:3]
                if len({str(w["speaker"]) for w in win}) >= 2:   # 상대 발화가 껴야 '관계' 앵커다
                    lines = " / ".join(f"\"{w['text']}\"" for w in win)   # ⒜ 라벨 미동봉 — 원문만
                    return f"최근 대화({int(ch_no)}화)={lines}"
        return ""

    def _bible_hits(self, q: str) -> tuple[list[str], list[str], list[dict]]:
        """XR-8(cross-review/007 §6): 설정집 조회의 상태 필터 — 무필터 시절엔 미검수·폐기 항목까지 캐논으로
        직렬화돼 [확정 설정: 절대 위반 금지] 블록에 실렸다(다섯 번째 우회 채널·라이브 실측).
        · deprecated·draft: 전면 제외. · author_approved: 캐논(canon — [확정 설정] 직렬화 대상).
        · ai_unreviewed: 비구속 참고(ref — 응답에는 싣되 canon 직렬화 제외. 표기는 기존 '(비구속 관측)' 계열).
        status 결측(덕타이핑)은 도메인 기본값과 동일하게 미검수 취급(정직 폴백). 반환 (canon, ref, meta) —
        meta 는 조회 로그용 {id, status, tier}. 총 반환 수는 종전 상한(2건·승인 우선) 유지."""
        canon_all: list[tuple[str, dict]] = []
        ref_all: list[tuple[str, dict]] = []
        for be in self.bible_entries:
            status = (getattr(be, "status", "") or "ai_unreviewed")
            if status in ("deprecated", "draft"):
                continue
            pool = [be.title or ""] + list(be.keywords or [])
            if not any(q in k or (len(k) >= 2 and k in q) for k in pool if k):
                continue
            m = {"id": getattr(be, "entry_id", ""), "status": status,
                 "tier": "binding" if status == "author_approved" else "narrative"}
            if status == "author_approved":
                canon_all.append((f"〔{be.title}〕 {be.prose or ''}", m))   # 절단 전면 제거(2026-08-21): 프로즈 전문
            else:
                ref_all.append((f"〔{be.title}〕(비구속 참고 — 작가 미확정) {be.prose or ''}", m))
        canon_sel = canon_all[:2]
        ref_sel = ref_all[:max(0, 2 - len(canon_sel))]      # 종전 총 2건 상한 — 승인 우선
        # 009 §3 경미: meta 는 '실제 응답에 실린' 항목만(계보=응답 정합 — 후보 초과분 미기록)
        return ([t for t, _ in canon_sel], [t for t, _ in ref_sel],
                [m for _, m in canon_sel + ref_sel])

    # ---- 진입점 ----
    @staticmethod
    def canon_facts_from_log(lookups_log) -> tuple[list[tuple[str, str]], int, int]:
        """PL-2: [확정 설정] 직렬화 정본 — 캐논 줄만, 전 로그 줄 단위 중복 제거.
        반환 ([(query, value)], 미등재 수, 중복 제거 수). 구 로그(canon 표식 없음)는 result 전문 폴백."""
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        misses = deduped = 0
        for item in lookups_log:
            canon = item.get("canon")
            if canon is None:                    # 하위호환: 표식 없는 구 로그는 종전 동작(전문)
                canon = [item.get("result", "")]
            if not canon:
                misses += 1
                continue
            kept = []
            for ln in canon:
                if ln in seen:
                    deduped += 1
                    continue
                seen.add(ln)
                kept.append(ln)
            if kept:
                out.append((item.get("query", ""), "\n".join(kept)))   # 절단 전면 제거(2026-08-21): [확정 설정] 재유입분 전문
        return out, misses, deduped

    def handle(self, name: str, args: dict) -> str:
        q = str((args or {}).get("query", "")).strip()
        if not q:
            return "질의가 비었습니다."
        amap = self._alias_map()
        # PL-2(2026-08-17 감사 F1·F3): 캐논 줄과 프로토콜 줄(실패·재조회 안내)을 생산 지점에서 분리 표식.
        #   프로토콜 줄은 사서 대화(chat_tools 턴) 안에서만 소비한다 — 발명 차단 계약(test_ag1)의 소비자는
        #   그 턴이고, 집필 콜 [확정 설정] 직렬화에 실리면 확정 스토리 재료를 "캐논에 없다"고 선언하는
        #   지시 충돌 소스가 된다(9화 전문 6건 실측). 소비자 쪽 문자열 매칭 검출기 대신 생산 표식(F17 계보).
        canon: list[str] = []
        proto: list[str] = []
        if q in amap:
            canon.append(self._entity_payload(amap[q]))
        else:
            cands = sorted({amap[k] for k in amap if len(q) >= 2 and (q in k or k in q)})
            if cands:
                names = [getattr(self.ont.entities.get(c), "name", c) for c in cands[:5]]
                proto.append("정확 일치 없음. 후보: " + ", ".join(names) + " (정확한 이름으로 재조회)")
        # XR-8: 설정집 히트를 상태별 채널로 — 승인=canon([확정 설정] 직렬화), 미검수=비구속 참고(응답 전용).
        _b_canon, _b_ref, _b_meta = self._bible_hits(q)
        canon += _b_canon
        if not canon and not proto and not _b_ref:
            proto.append(f"미등재. '{q}' 는 캐논에 없는 사실입니다.")
        res = "\n".join(proto + canon + _b_ref)
        # 절단 전면 제거(2026-08-21): 구 CX-11 로그 캡(500→800)도 소거 — 전문 영속(T5·선조회 ground_truth 승계 무손실).
        # canon 필드가 직렬화 정본(구 데이터는 result 만 있음 — 소비자 폴백). bible 필드=XR-8 상태 감사 채널.
        self.log.append({"query": q, "result": res,
                         "canon": list(canon), "bible": _b_meta})
        return res
