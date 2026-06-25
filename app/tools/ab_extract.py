# -*- coding: utf-8 -*-
"""A/B — 구조추출(claim-extract) 역할: 핫패스 JSON. 같은 런의 세계+회차(이름 일치)를 6모델이 extract_full.
extract_full = 알려진 엔티티별 속성/상태를 *증거와 함께* 추출(id 키잉, appears_as=부재판정).
측정(객관): JSON유효 / 등장판정 / 속성주장 수 / *증거충실*(주장에 증거동반) / *환각*(증거없는 주장) / 토큰.
싼것⒞이 플래그십과 동급이면 핫패스 비용 절감 라우팅 정당.
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/ab_extract.py
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

from novelcopilot.config import get_settings
from novelcopilot.llm.openai_provider import OpenAIProvider
from novelcopilot.llm.anthropic_provider import AnthropicProvider
from novelcopilot.llm.gemini_provider import GeminiProvider
from novelcopilot.repository import FilesystemProjectRepository
from novelcopilot.services import CopilotService
from novelcopilot.engine.factory import build_engine
from tools.ab_obsession_worldgen import SEED

_emb = OpenAIProvider("gpt-4.1", "text-embedding-3-small")
ARMS = [("gpt5.2-chat", "openai", "gpt-5.2-chat-latest"), ("claude-opus", "anthropic", "claude-opus-4-8"),
        ("gemini-3.1", "gemini", "gemini-3.1-pro-preview"),
        ("gpt5-mini⒞", "openai", "gpt-5-mini"), ("haiku4.5⒞", "anthropic", "claude-haiku-4-5-20251001"),
        ("gemini2.5fl⒞", "gemini", "gemini-2.5-flash")]


def prov(pn, pm):
    if pn == "openai":
        return OpenAIProvider(pm, "text-embedding-3-small")
    if pn == "anthropic":
        return AnthropicProvider(pm, _emb)
    return GeminiProvider(pm, _emb)


def score(res):
    """entity record: {id, appears_as, <attr>, <attr>_evidence, ...}. 비-evidence 속성 non-null=주장,
    동반 <attr>_evidence 비어있지 않으면 충실, 비면 환각(증거없는 상태주장)."""
    ents = res.get("entities") or []
    present = claims = grounded = 0
    for e in ents:
        if not isinstance(e, dict):
            continue
        if e.get("appears_as") and e.get("appears_as") != "absent":
            present += 1
        for k, v in e.items():
            if k in ("id", "appears_as") or k.endswith("_evidence") or v in (None, "", "absent", []):
                continue
            claims += 1
            ev = e.get(f"{k}_evidence")
            if ev:
                grounded += 1
    return dict(ents=len(ents), present=present, claims=claims, grounded=grounded, hall=claims - grounded)


def main():
    s0 = get_settings()
    repo = FilesystemProjectRepository(Path(tempfile.mkdtemp()))
    print("같은 런: 세계 생성 + 1회차 집필(이름 일치 보장)...", flush=True)
    svc = CopilotService(s0, repo)
    st, _ = svc.create_project(SEED.model_copy(deep=True))
    rec = svc.generate_next_chapter(st.id).get("record")
    text = (rec.text if rec else "") or ""
    ids = [e.id for e in st.world.entities]
    print(f"세계={st.world.title} 로스터={[e.name for e in st.world.entities]} / 본문 {len(text)}자\n", flush=True)

    print(f"  {'arm':14}{'JSON':>5}{'ents':>5}{'present':>8}{'claims':>7}{'grounded':>9}{'환각':>5}{'tok':>7}")
    for name, pn, pm in ARMS:
        try:
            p = prov(pn, pm)
            b = build_engine(st.world, p, s0)
            res = b.checker.extractor.extract_full(text, b.ontology, ids)
            m = score(res)
            print(f"  {name:14}{'ok':>5}{m['ents']:>5}{m['present']:>8}{m['claims']:>7}{m['grounded']:>9}{m['hall']:>5}{getattr(p.usage,'chat_tokens',0):>7}", flush=True)
        except Exception as e:
            print(f"  {name:14}{'FAIL':>5}  {type(e).__name__}: {str(e)[:90]}", flush=True)
    print("\n(JSON=유효출력 성공. grounded=증거동반 상태주장(많을수록 추출충실). 환각=증거없는 상태주장(적을수록 좋음).")
    print(" 싼것⒞이 JSON 성공+grounded 동급+환각 낮으면 핫패스 추출=싼 모델 라우팅 정당.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
