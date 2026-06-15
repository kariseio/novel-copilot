# -*- coding: utf-8 -*-
"""장르 계약 백필(M-2) — G5 도입 전 생성된 작품에 genre_contract 가 None 일 때, 작가 요청으로 1회 추론.

신규 작품은 worldgen 출력에 genre_contract 가 포함되나, 구작은 None → 쾌감엔진·전제자산·독자기대가
설계·독자평가에 비주입. 이 함수가 genre/premise/synopsis 로부터 계약 초안을 추론한다(narrative — 캐논 아님).
강제 적용 아님: 작가가 대시보드에서 '계약 생성'을 눌렀을 때만 호출된다.
"""
from __future__ import annotations

from ..domain.world import GenreContract


def infer_genre_contract(provider, world) -> GenreContract | None:
    """장르/전제/시놉시스 → GenreContract 초안. 실패 시 None(비차단)."""
    try:
        r = provider.chat_json(
            [{"role": "system", "content":
              "너는 한국 웹소설 편집자다. 아래 작품의 '장르 정체성'을 뽑아라(서술 정보 — 규칙 강제 아님). JSON: "
              '{"pleasure_engine":"이 장르 독자가 결제하는 핵심 쾌감 한 줄",'
              '"reader_expectations":["독자 기대 3~5개"],'
              '"vocabulary_tone":"이 장르다운 어휘·톤 한 줄",'
              '"premise_asset":"이 작품의 핵심 동력 전제와 그 역할(장기 자산이면 그렇게)"}'},
             {"role": "user", "content":
              f"[장르]{world.genre}\n[전제]{(world.premise or '')[:600]}\n[시놉시스]{(world.synopsis or '')[:600]}"}],
            temperature=0.4, max_tokens=900)
        gc = GenreContract(
            pleasure_engine=(r.get("pleasure_engine") or "").strip(),
            reader_expectations=[str(x).strip() for x in (r.get("reader_expectations") or []) if str(x).strip()][:6],
            vocabulary_tone=(r.get("vocabulary_tone") or "").strip(),
            premise_asset=(r.get("premise_asset") or "").strip())
        return gc if (gc.pleasure_engine or gc.premise_asset) else None
    except Exception:
        return None
