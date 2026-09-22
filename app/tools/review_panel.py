# -*- coding: utf-8 -*-
"""매화 블라인드 독자 패널 — 4 페르소나 적대 리뷰(하차 기본값·원문 인용 의무·잔존율).

사용자 지시(2026-07-27 "매화마다 리뷰 검증 해야해")로 상설화. 3패널 교차 검증(Opus4.8·Fable5·Opus5,
쌍대 12:0 일치)에서 검증된 페르소나·프롬프트 구성을 단독 리뷰 모드로 도구화. 심판 기본=claude-opus-5
(3패널 중 최예리 실측 — 축 분리 판정·잠언 틱 신규 적발·판 조어 사후 검증 / 최저가 $5/$25).

역할 경계:
 · 게이트(gen_gated LLM 정독 — cross-vendor 1심 PASS/FAIL)와 별개의 **advisory 다인 관점 리뷰**다.
   판정·차단·자동수정 0 — 산출은 PM 정독 보고에 병기되는 참고 자료(해석·최종 판정=사용자).
 · 콜드리드(cold_read 독자 예측)·빨간펜(RP-1)과도 별개 축.
 · 리뷰어는 본문+이전 회차 요약만 받는 블라인드(세션 컨텍스트·출처 정보 0).

실행(app/ 에서):
  py -3.12 -X utf8 tools/review_panel.py --pid <pid> --chapter <N|last>
  옵션: --model anthropic:claude-opus-5(기본) · --out-dir tools/reports(기본)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novelcopilot.config import get_settings
from novelcopilot.repository.filesystem import FilesystemProjectRepository
from novelcopilot.llm.factory import create_role_provider

PERSONAS = [
    ("snack", "출퇴근 지하철에서 폰으로 읽는 스낵 독자. 첫 세 문단 안에 재미없으면 뒤로가기. 속도·사이다·다음 화 궁금증이 전부. 문학성 관심 없음."),
    ("heavy", "현대판타지·괴담물 수백 편을 완독한 장르 헤비 독자. 클리셰 감별사. 설정 구멍·개연성 결함·어디서 본 전개에 가차없음."),
    ("prose", "문장에 결벽이 있는 독자. 같은 문말 반복·번역투·AI로 쓴 티·기계적 리듬이 보이면 바로 하차하고 댓글로 지적하는 타입."),
    ("pay", "직전 화까지 읽고 이번 화부터 유료인 플랫폼에서, 이 화를 보고 결제를 계속할지 정하는 독자. 돈 낸 만큼 아까우면 바로 접는다."),
]

FMT = """반드시 아래 키를 가진 JSON 하나만 출력하라:
{"verdict": "계속" 또는 "하차", "drop_point": "하차했거나 닫고 싶었던 지점의 본문 원문 인용(없으면 빈 문자열)", "retention_pct": 0~100 숫자(이 화까지 온 독자 중 다음 화를 여는 비율 추정), "comment": "실제로 달 댓글(솔직하게, 악플이면 악플 그대로)", "best": "제일 좋았던 것 하나(없으면 '없음')", "worst": "제일 거슬린 것 하나 — 본문 원문 인용 포함", "style_note": "문장/문체에서 느낀 점 한 줄(AI티·번역투·반복 등 느꼈다면 그대로)"}"""


def _prompt(desc: str, recap: str, title: str, text: str) -> str:
    recap_block = f"[지금까지 줄거리 — 너는 여기까지 읽고 온 독자다]\n{recap}\n\n" if recap else ""
    return f"""너는 다음 성향의 한국 웹소설 독자다: {desc}

{recap_block}아래는 이 작품의 새 회차 원고다.

[제목] {title}
────
{text}
────

평가 태도(엄수):
- 기본값은 하차다. 계속 읽을 이유를 본문이 스스로 증명해야 한다. 관대하게 봐주지 마라.
- 닫고 싶었던 순간이 있었다면 그 지점의 본문 문장을 원문 그대로 인용하라.
- 댓글은 실제 플랫폼에 달듯 솔직하게. 악플이 나올 내용이면 악플 그대로.
- 잔존율은 네 성향의 독자군 기준으로 냉정하게 추정하라.
- 이 원고가 누가 어떻게 쓴 것인지에 대한 정보는 일절 없다. 오직 본문만으로 판단하라.

너의 최종 응답은 사람이 아니라 데이터 수집기로 간다.

{FMT}"""


def run_panel(pid: str, chapter: str, model: str, out_dir: Path) -> dict:
    s = get_settings()
    repo = FilesystemProjectRepository(s.resolved_data_dir())
    state = repo.get(pid)
    if state is None:
        raise SystemExit(f"작품 없음: {pid}")
    chs = sorted(state.chapters, key=lambda c: c.chapter)
    ch_no = chs[-1].chapter if chapter == "last" else int(chapter)
    target = next((c for c in chs if c.chapter == ch_no), None)
    if target is None or not (target.text or "").strip():
        raise SystemExit(f"{ch_no}화 본문 없음")
    recap = "\n".join(f"{c.chapter}화: {c.summary}" for c in chs if c.chapter < ch_no and (c.summary or "").strip())

    def call(key: str, desc: str):
        p = create_role_provider(s, model)
        try:
            r = p.chat_json([{"role": "user", "content": _prompt(desc, recap, target.title or f"{ch_no}화", target.text)}],
                            temperature=0.7)
            return {"persona": key, "result": r, "error": None}
        except Exception as e:  # 정직 기록 — 패널 일부 실패가 전체를 죽이지 않는다
            return {"persona": key, "result": None, "error": f"{type(e).__name__}: {e}"[:300]}

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda pd: call(*pd), PERSONAS))

    ok = [x for x in results if x["result"]]
    drops = [x for x in ok if x["result"].get("verdict") == "하차"]
    ret = round(sum(float(x["result"].get("retention_pct") or 0) for x in ok) / max(1, len(ok)))
    doc = {"pid": pid, "chapter": ch_no, "title": target.title, "model": model,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "summary": {"n": len(ok), "drop": len(drops), "retention_avg": ret,
                       "errors": [x["persona"] for x in results if x["error"]]},
           "reviews": results}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"review_panel_{pid}_ch{ch_no}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"== 패널({model}) {pid} {ch_no}화 「{target.title}」 — 하차 {len(drops)}/{len(ok)} · 잔존평균 {ret}%")
    for x in results:
        if x["error"]:
            print(f"  [{x['persona']}] ERR {x['error']}")
            continue
        r = x["result"]
        print(f"  [{x['persona']}] {r.get('verdict')} {r.get('retention_pct')}% | 최악: {(r.get('worst') or '')[:90]}")
    print("저장:", out)
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", required=True)
    ap.add_argument("--chapter", default="last")
    ap.add_argument("--model", default="anthropic:claude-opus-5")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "reports"))
    a = ap.parse_args()
    run_panel(a.pid, a.chapter, a.model, Path(a.out_dir))
