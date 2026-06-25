# -*- coding: utf-8 -*-
"""장르 일반화 테스트 — 유명 장르별로 세계 생성 + 5화 집필 후 장르충실도·자연스러움·AI티 블라인드 평가.
현행 기본 라우팅(get_settings: worldgen=claude / 설계=planning_model / 본문=gen_model) 그대로 사용 → 최종 모델로 일반화 검증.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_genres.py
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.openai_provider import OpenAIProvider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.quality_gates import ai_tell_profile
from novelcopilot.domain.project import ProjectSeed

OUT = Path(r"C:\Users\owner\AppData\Local\Temp\sl_compare\genres")
N_CH = 5

GENRES = [
    ProjectSeed(title="", genre="정통 판타지", tone="웅장한 모험과 성장, 비장하면서 통쾌",
                premise=("검과 마법의 대륙 아르카디아. 몰락한 변경 귀족의 서자가 금기시된 고대 마법의 재능을 숨긴 채 살아간다. "
                         "제국의 음모로 가문이 멸문당하자, 그는 정체를 감추고 모험가 길드에 들어가 힘을 키우며 복수와 대륙의 비밀을 좇는다."),
                protagonist_hint="몰락 귀족 서자, 봉인된 고대 마법 재능, 멸문 복수와 정체 은닉", target_chapters=30),
    ProjectSeed(title="", genre="학원물(잔잔한 일상)", tone="잔잔하고 따뜻한 일상, 섬세한 감정선과 성장",
                premise=("평범한 고등학교의 2학년 교실. 봄에 전학 온 주인공이 낯선 학교와 반에 서서히 스며든다. "
                         "특별한 능력도 거대한 사건도 없이 동아리·점심시간·축제 준비·진로 고민·서툰 첫 우정과 짝사랑 같은 "
                         "일상의 결을 따라가며, 작은 오해와 화해 속에서 천천히 자란다."),
                protagonist_hint="봄 전학생, 능력·초자연 전혀 없는 평범한 고교생, 일상의 서툰 감정과 관계", target_chapters=30),
    ProjectSeed(title="", genre="현대 로맨스", tone="설렘과 긴장, 감정선 중심, 사이다",
                premise=("대기업 비서로 위장 취업한 주인공과, 차갑고 완벽주의인 젊은 본부장. 계약 연애로 얽힌 두 사람이 "
                         "서로의 상처와 비밀을 알아가며 진심에 빠진다. 사내 정치와 과거의 약혼자가 둘 사이를 흔든다."),
                protagonist_hint="위장 취업 비서, 차가운 본부장과 계약 연애, 상처와 진심", target_chapters=30),
    ProjectSeed(title="", genre="정통 무협", tone="강호의 비장함과 쾌감, 고풍스러운 어휘",
                premise=("멸문한 명문 정파의 마지막 후예가 절벽에서 떨어져 사라진 마교 교주의 절세 내공심법을 얻는다. "
                         "신분을 감추고 강호에 나선 그는 가문을 멸한 흑막을 쫓으며 정사대전의 한가운데로 빨려든다."),
                protagonist_hint="멸문 정파 후예, 마교 절세심법 기연, 신분 은닉 복수", target_chapters=30),
    ProjectSeed(title="", genre="회귀물", tone="사이다 복수와 전략, 미래지식 활용",
                premise=("대기업의 버림받은 토사구팽 임원이 모든 걸 잃고 죽는 순간, 20년 전 신입사원 시절로 회귀한다. "
                         "미래의 기억을 무기로 그는 자신을 배신한 자들을 거꾸러뜨리고 정점에 오르기 위한 판을 짠다."),
                protagonist_hint="토사구팽 임원, 20년 전 회귀, 미래지식으로 복수와 정점", target_chapters=30),
]


def main():
    s0 = get_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    judge = OpenAIProvider("gpt-4.1", "text-embedding-3-small")
    print(f"라우팅: worldgen={s0.worldgen_model} / 설계={s0.planning_model} / 본문={s0.gen_model}\n", flush=True)

    rows = []
    for seed in GENRES:
        g = seed.genre
        print(f"=== [{g}] 세계 생성 + {N_CH}화 ===", flush=True)
        try:
            svc = CopilotService(s0, FilesystemProjectRepository(Path(tempfile.mkdtemp())))
            st, _ = svc.create_project(seed.model_copy(deep=True))
            chs, fails = [], 0
            for i in range(N_CH):
                try:
                    rec = svc.generate_next_chapter(st.id).get("record")
                    t = (rec.text if rec else "") or ""
                    chs.append(t)
                    if len(t) < 800:
                        fails += 1
                except Exception as e:
                    fails += 1; print(f"  ch{i+1} ERR {type(e).__name__} {str(e)[:90]}", flush=True)
            body = "\n\n".join(chs)
            (OUT / f"{g}.txt").write_text(body, encoding="utf-8")
            roster = {e.name for e in st.world.entities}
            prof = ai_tell_profile(body, roster) if body else {}
            avg = (sum(len(c) for c in chs) // max(1, len(chs)))
            print(f"  세계='{st.world.title}' 엔티티{len(st.world.entities)} 아크{len(st.world.spine.arcs) if st.world.spine else 0} | {len(chs)}화 평균 {avg}자 실패 {fails}", flush=True)

            # 블라인드 평가: 장르충실도·자연스러움·훅 + 약점
            sysj = (f'너는 한국 웹소설 편집자다. 다음은 "{g}" 장르로 생성된 연재 {len(chs)}화다. '
                    '장르 관습 충실도·문장 자연스러움(덜 AI같음)·연재 훅을 각 0~10으로 채점하고 약점 한 줄. '
                    '{"genre_fidelity":0~10,"naturalness":0~10,"hook":0~10,"weakness":"한 줄"} JSON만.')
            try:
                d = judge.chat_json([{"role": "system", "content": sysj},
                                     {"role": "user", "content": body[:11000]}], temperature=0.2)
            except Exception as e:
                d = {"genre_fidelity": "?", "naturalness": "?", "hook": "?", "weakness": f"심사실패 {str(e)[:50]}"}
            rows.append((g, len(chs), fails, avg, prof, d))
            print(f"  평가: 장르충실={d.get('genre_fidelity')} 자연={d.get('naturalness')} 훅={d.get('hook')} | {str(d.get('weakness',''))[:70]}", flush=True)
        except Exception as e:
            print(f"  [{g}] 치명 실패: {type(e).__name__} {str(e)[:140]}", flush=True)
            rows.append((g, 0, N_CH, 0, {}, {"weakness": f"생성실패 {str(e)[:50]}"}))

    print("\n========== 장르 일반화 요약 ==========")
    print(f"  {'장르':16}{'화':>3}{'실패':>4}{'평균자':>7}{'comma':>7}{'sentCV':>7}{'장르':>5}{'자연':>5}{'훅':>4}  약점")
    for g, n, f, avg, prof, d in rows:
        cm = f"{prof.get('comma_per_100',0):.2f}" if prof else "-"
        cv = f"{prof.get('sent_len_cv',0):.2f}" if prof else "-"
        print(f"  {g:16}{n:>3}{f:>4}{avg:>7}{cm:>7}{cv:>7}{str(d.get('genre_fidelity','-')):>5}{str(d.get('naturalness','-')):>5}{str(d.get('hook','-')):>4}  {str(d.get('weakness',''))[:50]}")
    print(f"\n본문 저장: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
