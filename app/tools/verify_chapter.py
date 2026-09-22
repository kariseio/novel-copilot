# -*- coding: utf-8 -*-
"""OT-2 — 재검증 단일 진입점(VI-1 우산). 체크리스트 A/B 기계 수행 + C 밥상 생성. LLM 0콜.

플로우 드리프트 방지가 존재 이유: 재검증을 손으로 재조립하면 매번 달라진다(2026-08-06 사용자 지적).
이 도구가 곧 플로우다 — 프로세스 문서(process-serial-pipeline.md) 8단계의 실행체.

무강제: 아무것도 판정하지 않는다. 값·인용·빈 판정란만 낸다. 사람 판정란이 빈칸으로 남으면
그 축은 안 돈 것이다(조용한 누락 차단).

용법:
  py -3.12 -X utf8 tools/verify_chapter.py --pid o5rewriteall --chapter 12
  py -3.12 -X utf8 tools/verify_chapter.py --pid o5rewriteall --chapter 12 --terms 봉인지,국수
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.config import get_settings
from novelcopilot.repository.filesystem import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.domain.types import ChapterStatus
from novelcopilot.engine.verification import build_verification
from novelcopilot.engine.chapter_gate import planned_event_for

# C 밥상 — 결정론 추출 패턴(판정 아님·재료만)
_MONEY = re.compile(r"[^.\n\"]*(?:\d[\d,]*\s*원|\d+\s*만|[\d,]+\s*그릇|\d+\s*장|\d+\s*냥|잔돈|정산|미수|재고)[^.\n]*[.]")
_VERDICT_WORDS = re.compile(r"\"[^\"\n]*(?:성립|미성립|확정|좌표|판정)[^\"\n]*\"")
_HAO = re.compile(r"\"[^\"]*(?:하오|겠소|았소|었소|잖수|났수|왔수|르쇼|시오)[.]?\"")
_APHORISM = re.compile(r"[^.\n]{4,40}(?:는 법이다|는 거다|가 값이다|이 장사다|는 물건이다)[.]")
_NEG_REDEF = re.compile(r"(?:가 아니라|이 아니라|아니었다\.|아니다\.)")


def _first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def _opening_kind(text: str) -> str:
    fl = _first_line(text)
    return "대사" if fl.startswith(("\"", "“", "'")) else "지문(사물·행동)"


def _fmt_delta(cur, prev, key_path: str) -> str:
    def dig(d):
        node = d or {}
        for k in key_path.split("."):
            node = node.get(k) if isinstance(node, dict) else None
            if node is None:
                return None
        return node
    a, b = dig(prev), dig(cur)
    if a is None or b is None:
        return f"{b}"
    try:
        return f"{b} (직전 화 {a}, Δ{round(float(b) - float(a), 3):+})"
    except (TypeError, ValueError):
        return f"{b} (직전 화 {a})"


def main() -> None:
    ap = argparse.ArgumentParser(description="OT-2 재검증 단일 진입점")
    ap.add_argument("--pid", required=True)
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--terms", default="", help="B 잔존 스캔 용어(쉼표 구분 — 수술 교체 원문 등)")
    ap.add_argument("--no-rebuild", action="store_true", help="A 재빌드 생략(읽기 전용 리포트)")
    ap.add_argument("--no-judge", action="store_true", help="낭독 판정(LLM) 생략 — 생략하면 문체 축은 안 돈 것")
    args = ap.parse_args()

    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    svc = CopilotService(s, repo)
    st = repo.get(args.pid)
    if not st:
        raise SystemExit(f"작품 없음: {args.pid}")
    ch = next((c for c in st.chapters if c.chapter == args.chapter), None)
    if not ch:
        raise SystemExit(f"회차 없음: {args.chapter}")

    lines: list[str] = []
    w = lines.append
    w(f"# 재검증 리포트 — {args.pid} {args.chapter}화 「{ch.title or ''}」")
    w(f"생성: {_dt.datetime.now().isoformat(timespec='seconds')} · 도구: verify_chapter(OT-2)"
      " · 낭독 판정=LLM · 수치=참고 원자료(판정 척도 아님)")
    w("")

    # ── 낭독 판정 (VJ-1 — 판정 본체는 LLM 정독, 사용자 지시 2026-08-13) ─────────
    #   문말·문단 호흡·문장 길이 변주를 낭독 체감으로 판정(원문 인용 의무). 분할·직접 수술 등
    #   파이프라인 밖 경로로 태어난 회차도 이 리포트를 거치므로 판정 공백이 안 생긴다.
    w("## 낭독 판정 (LLM — 판정 본체)")
    rhythm = None
    if args.no_judge:
        w("- 생략(--no-judge) — 판정 없이는 문체 축이 안 돈 것이다")
    else:
        from novelcopilot.engine.style_judge import judge_rhythm
        from novelcopilot.llm.factory import create_role_provider
        spec = (getattr(s, "style_judge_model", "") or "").strip()
        if not spec:
            w("- 실패: style_judge_model 미설정(gen≠judge 라우팅 없음) — 결측 정직")
        else:
            try:
                jp = create_role_provider(s, spec)
                rhythm = judge_rhythm(jp, ch.text)
            except Exception as e:   # 판정 콜 실패 = 결측 정직(리포트는 계속 — 비차단)
                w(f"- 실패: {type(e).__name__} — 결측 정직·재시도 요")
            if rhythm is None and spec:
                w("- 판정 실패(콜/파싱) — 결측 정직·재시도 요")
            elif rhythm is not None:
                verdict = "수리 필요" if rhythm.get("needs_repair") else "자연스러움"
                w(f"- 판정: {verdict} — {rhythm.get('reason', '')}")
                for sp in rhythm.get("spans") or []:
                    ax = f"[{sp['axis']}] " if sp.get("axis") else ""
                    w(f"    · {ax}\"{sp['quote'][:80]}\" — {sp.get('why', '')}")
                _in_text = sum(1 for sp2 in (rhythm.get("spans") or []) if sp2["quote"] in (ch.text or ""))
                w(f"- 인용 정합: {_in_text}/{len(rhythm.get('spans') or [])}건이 본문 부분 문자열")
    w("")

    # ── A. 기계 상시 ──────────────────────────────────────────────
    w("## A. 기계 상시 [참고 원자료 — 판정 척도 아님]")
    if not args.no_rebuild:
        sess = svc.sessions.get_or_create(st)
        sess.bundle.rag.index_chapter(ch.chapter, ch.text)
        ch.ai_tell = svc._recompute_ai_tell(st, sess, ch.text)
        prev_texts = [c.text for c in st.chapters
                      if c.chapter < ch.chapter and c.status == ChapterStatus.FINALIZED]
        pg = (ch.verification or {}).get("gate")
        pg = pg if isinstance(pg, dict) else None
        ch.verification = build_verification(
            ch, prev_texts=prev_texts,
            target_chars=getattr(st.world.style, "target_chars_per_chapter", None), gate=pg,
            leak_sources=svc._leak_sources_for(st, ch.chapter))   # VL-1: 재검증 도구가 축을 '미실행'으로 덮던 구멍(PM 검수)
        if rhythm is not None:   # VJ-1: 낭독 판정 영속(요약만 — 인용 전문은 리포트 파일이 SSOT)
            ch.verification["rhythm_judge"] = {
                "needs_repair": bool(rhythm.get("needs_repair")),
                "n_spans": len(rhythm.get("spans") or []),
                "reason": rhythm.get("reason", "")}
        st.rag_chunks = sess.bundle.rag.export_chunks()
        repo.save(st)
        w("- 재색인·ai_tell·검증 리빌드: 완료(저장됨)")
    else:
        w("- 재빌드 생략(--no-rebuild)")
    v = ch.verification or {}
    gate = v.get("gate") or {}
    if not isinstance(gate, dict):   # 러너 중단 등으로 '미실행' 문자열이 남는 경우 — 결측 정직(크래시 금지)
        w(f"- 게이트: {gate or 'MISSING'}(비정형 — 러너 게이트 미실행)")
        gate = {}
    else:
        w(f"- 게이트: {gate.get('verdict', 'MISSING')} · 잔존 추정 {gate.get('retention_est', '-')}")
    prev_ch = next((c for c in reversed(st.chapters) if c.chapter < ch.chapter), None)
    prev_at = getattr(prev_ch, "ai_tell", None) if prev_ch else None
    at = ch.ai_tell or {}
    w(f"- 종결 최빈 비율: {_fmt_delta(at, prev_at, 'kiwi.ending_profile.top_ratio')}"
      f" · 문장 CV: {_fmt_delta(at, prev_at, 'sent_len_cv')}"
      f" · 직유/1k: {_fmt_delta(at, prev_at, 'simile_per_1k')}")
    lay = v.get("layout") or {}
    w(f"- 조판(TG-1): 문단 {lay.get('n_paras', '-')} · 3줄 초과 {lay.get('over', '-')}"
      + (f" · 최장 {lay.get('worst', [{}])[0].get('lines')}줄 「{lay.get('worst', [{}])[0].get('head', '')}」"
         if lay.get("worst") else ""))
    canon = v.get("canon")
    w(f"- 결정론 캐논 위반: {json.dumps(canon, ensure_ascii=False)[:160]}")
    # VH-1(2026-08-16 감사 F-10): voice_leak 는 계산·영속되면서 리포트에 안 실려 6화 히트 1건이 사람 눈에
    #   안 닿았다(계산되고 묻힌 축) — 매화 전축 검증 의무에 따라 상시 렌더.
    vl = v.get("voice_leak")
    if isinstance(vl, dict):
        w(f"- 보이스·설정 직역 스윕(VL-1): {vl.get('count', 0)}건"
          + "".join(f" · [{h.get('source')}/{h.get('scope')}] 「{str(h.get('match', ''))[:40]}」({h.get('len')}자)"
                    for h in (vl.get("hits") or [])[:4]))
    else:
        w(f"- 보이스·설정 직역 스윕(VL-1): {vl or 'MISSING'}")
    # VX-1: 대사 태그 경로 상시 계측(structured_prompt 작품 또는 이상 발생 시). 잔여 마커·본문 꺾쇠는 계약값 0
    #   (디태거 통과분·입력 XML→출력 마크업 누출 회귀 감시 M2), 화자 확정은 태그 우회 이득(미상 소멸) 가시화.
    _sp_on = bool(getattr(getattr(st.world, "style", None), "structured_prompt", False))
    from novelcopilot.engine.textfmt import dialogue_tag_residue
    _resid = dialogue_tag_residue(ch.text)
    if _sp_on or _resid:
        _markup = len(re.findall(r"[<>]", ch.text))
        _dl = getattr(ch, "dialogue_ledger", None) or []
        _named = sum(1 for r in _dl if r.get("speaker") not in ("", "미상"))
        # 플레이스홀더 리터럴 회귀 가드(계약값 0) — 모델이 예시 <대사 화자="이름">…말…</대사> 를 통째로 베끼면
        #   디태거가 이를 정상 태그로 매칭해 '…말…'을 가시 대사로·화자 '이름'으로 변환하는데 잔여=0 검사는 못 잡는
        #   맹점(감사관 재감사 지적). 사후 감사 전용 — 생성 프롬프트는 불변.
        _ph = ch.text.count("…말…") + sum(1 for r in _dl if r.get("speaker") == "이름")
        w(f"- 대사 태그(VX-1): 잔여 마커 {_resid} · 본문 꺾쇠 {_markup} · 플레이스홀더 {_ph} · 화자 확정 {_named}/{len(_dl)}줄")
    w(f"- 분량: {len(ch.text)}자")
    w("")

    # ── B. 기계 조건부 ────────────────────────────────────────────
    w("## B. 기계 조건부 스캔")
    terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    if terms:
        hits = []
        layers = {"본문": ch.text, "요약": ch.summary or "", "상세시놉": ch.detail_synopsis or ""}
        for wp in (st.wiki_pages or []):
            layers[f"위키:{getattr(wp, 'title', '?')}"] = getattr(wp, "body", "") or ""
        for p in st.promise_ledger.promises:
            layers[f"원장:{p.id[:14]}"] = p.text or ""
        for name, body in layers.items():
            for t in terms:
                n = body.count(t)
                if n:
                    hits.append(f"{name} · {t} ×{n}")
        w("- 용어 잔존: " + ("; ".join(hits) if hits else "0건 (깨끗)"))
    else:
        w("- 용어 잔존: (--terms 미지정 — 수술 시 교체 원문을 넘길 것)")
    oc = [o for o in (ch.ontology_changes or []) if getattr(o, "op", o.get("op") if isinstance(o, dict) else "") == "contradiction"] \
        if ch.ontology_changes else []
    if oc:
        for o in oc:
            d = o if isinstance(o, dict) else o.__dict__
            w(f"- 온톨로지 모순[{d.get('severity')}]: {d.get('entity')} — {str(d.get('detail'))[:80]}")
    else:
        w("- 온톨로지 모순: 0건")
    # ON-2 U3: 달력 SSOT 단일화 — 자체 누적 루프(week/month/year 무시) 대신 story_clock 산식 재사용.
    from novelcopilot.engine.story_clock import elapsed_minutes
    deltas = [c.time_delta for c in st.chapters if c.chapter <= ch.chapter]
    minutes, _known = elapsed_minutes(deltas)
    w(f"- 시간 달력(story_clock): 이 화 시점 경과 {minutes / 1440:.1f}일 (이 화 time_advance: {ch.time_advance or '-'})")
    # ON-2 U6: 상태 대조 — 이 화 말 시점 캐논값 vs 본문(수치 문장은 C절) + 갱신 루프 불변식 + 미승인 계수.
    w("")
    w("### 상태 대조 (ON-2 — 캐논 vs 본문은 정독으로)")
    sess = svc.sessions.get_or_create(st)
    ont = sess.bundle.ontology
    num_keys = [a.key for a in (st.world.attributes or []) if getattr(a, "kind", "") == "numeric"]
    case_ids = [getattr(e, "id", "") for e in (st.runtime_entities or [])
                if getattr(e, "etype", "") == "case"]
    shown = 0
    for e in list(st.world.entities) + list(st.runtime_entities or []):
        eid = getattr(e, "id", "")
        for k in num_keys + (["case_status"] if eid in case_ids else []):
            try:
                val = ont.state_as_of(eid, k, ch.chapter + 1)
            except Exception:
                val = None
            if val is not None:
                w(f"- 캐논[{getattr(e, 'name', eid)}.{k}] = {val} (이 화 말 시점)")
                shown += 1
    if not shown:
        w("- (선언된 수치·건 상태 캐논 없음)")
    from novelcopilot.engine.ontology_ops import validate_timeline
    probs = validate_timeline(st)
    if probs:
        for p in probs[:8]:
            w(f"- 불변식[{p['kind']}] {p['entity']}.{p['attr']}: {p['detail']}")
        if len(probs) > 8:
            w(f"- (외 {len(probs) - 8}건)")
    else:
        w("- 타임라인 불변식: 위반 0")
    tl = st.runtime_timeline or []
    mach = [ev for ev in tl if ev.trust_tier == "ground_truth"
            and "author" not in (getattr(ev, "provenance", None) or ["machine"])]
    this_ch = [ev for ev in mach if ev.eff_from == ch.chapter + 1]
    w(f"- 미승인 기계 캐논: 전체 {len(mach)}건 / 이 화 발생 {len(this_ch)}건"
      + (" → tools/approve_state.py 로 승인·정정" if mach else ""))
    w("")

    # ── C. 사람 판정 밥상 ─────────────────────────────────────────
    w("## C. 정독 밥상 (판정은 사람)")
    pe = planned_event_for(st, ch.chapter)
    w(f"- 설계 사건([N화차]): {pe or '(분해 없음)'}")
    openings = [(c.chapter, _opening_kind(c.text), _first_line(c.text)[:30])
                for c in st.chapters if 0 < ch.chapter - c.chapter <= 2 or c.chapter == ch.chapter]
    for n, kind, fl in openings:
        w(f"- 오프닝 {n}화 [{kind}] {fl}")
    money = _MONEY.findall(ch.text)[:12]
    w(f"- 수치 문장 {len(money)}건(대조용):")
    for m in money:
        w(f"    · {m.strip()[:70]}")
    vd = _VERDICT_WORDS.findall(ch.text)
    if vd:
        w(f"- 대사 속 판정어 후보 {len(vd)}건: " + " / ".join(x[:40] for x in vd[:5]))
    hao = _HAO.findall(ch.text)
    if hao:
        w(f"- 하오체류 대사 후보 {len(hao)}건: " + " / ".join(x[:30] for x in hao[:5]))
    w(f"- 경구꼴 {len(_APHORISM.findall(ch.text))}건 · 부정-재정의 {len(_NEG_REDEF.findall(ch.text))}건 · '뼘' {ch.text.count('뼘')}건")
    # OV-5: 온톨로지 갱신 축 — 캐논 갱신 실측(스테이지 실패의 침묵 통과 차단)
    _ov = v.get("ontology")
    if isinstance(_ov, dict):
        _flag = " · ⚠ 제안 스테이지 실패(소급 재실행 필요)" if _ov.get("stage_failed") else ""
        w(f"- 온톨로지 갱신: 적용 {_ov.get('applied', 0)}/{_ov.get('changes', 0)}건{_flag}")
    else:
        w("- 온톨로지 갱신: 기록 없음(변경 0 제안 또는 구 회차 — 결측 정직)")
    # DG-1: 화자별 어체 분포 — PR-2 SSOT 축(verification.dialogue_ledger)을 읽어 표시(advisory·판정은 사람)
    _dl = v.get("dialogue_ledger")
    if isinstance(_dl, dict):
        w(f"- 화자별 어체 분포(대사 원장 {_dl.get('quotes', 0)}줄 · 캐논 {_dl.get('canon', 0)} · 미상 {_dl.get('unknown', 0)}):")
        _canon_sp = set(_dl.get("canon_speakers") or [])
        for sp, dist in sorted((_dl.get("speakers") or {}).items(), key=lambda x: -sum(x[1].values())):
            total = sum(dist.values())
            body = " · ".join(f"{k} {v2}" for k, v2 in sorted(dist.items(), key=lambda x: -x[1]))
            # DG-4 ⑦: 캐논 인물과 본문 지칭을 구분 표기(자동 합산 없음 — 연결은 작가 확정 계보)
            tag = "" if (sp in _canon_sp or sp == "미상") else "〔지칭〕"
            w(f"    · {sp}{tag}({total}줄): {body}")
    else:
        w("- 화자별 어체 분포: 원장 없음(구 회차 백필 필요·OFF·귀속 실패·대사 0 — 결측 정직)")
    w("")
    w("## 사람 판정란 (빈칸이면 그 축은 안 돈 것)")
    # VH-2(2026-08-17 사용자 지시 "피드백 사항 따로 기록해서 다음번에 계속 검사"): 사용자 정독 지적 원장의
    #   전 항목 재발 검사를 판정란에 상시 게시 — 원장은 docs/user-feedback-ledger.md (사후 검증 전용·프롬프트 노출 금지).
    for item in ("화 단위 사건 유무(최우선)", "전 화 비트 중복", "실물 정합(소품·신체)",
                 "이음새(수술 시)", "어체 캐논", "AI티 층위 3→2→1", "낭독 가독성",
                 "사용자 지적 재발(docs/user-feedback-ledger.md 전 항목 — 항목별 판정·인용)",
                 "조립 프롬프트 전문 정독(logs/prompts/<pid>/ 본문 생성 콜 — PL-1)", "최종 판정"):
        w(f"- [ ] {item}: ")
    out_dir = Path(__file__).resolve().parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"verify_{args.pid}_ch{args.chapter}_{_dt.datetime.now():%Y%m%d_%H%M%S}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
