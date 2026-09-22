# -*- coding: utf-8 -*-
"""XR-11 검증 — 모델 제안 속성의 안전 티어 기본값 (LLM 0콜).

계약(cross-review/007 §5):
  · worldgen 적재(_normalize)에서 auto_commit 미지정 속성은 non_binding 착지 — 안전이 opt-in 이 아니라 기본.
  · 데이터가 명시한 선언은 보존(코드가 덮지 않음).
  · 구 JSON 로드 경로는 무변경(미선언 ""=현행 — _normalize 는 worldgen 산출에만 돈다).
실행: PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/test_xr11_worldgen_tier_default.py
"""
from __future__ import annotations
import sys

from novelcopilot.domain.project import ProjectSeed
from novelcopilot.domain.world import WorldConfig, AttributeSpec, EntitySpec
from novelcopilot.worldgen.generator import WorldGenerator


def test_normalize_defaults_unset_to_non_binding() -> bool:
    w = WorldConfig(title="t", genre="g",
                    attributes=[AttributeSpec(key="money_won", label="소지금", kind="numeric", mutable=True),
                                AttributeSpec(key="status", label="생사", kind="state",
                                              states=["alive", "dead"], mutable=True),
                                AttributeSpec(key="truth", label="자각", kind="categorical",
                                              vocab=["외면", "인정"], mutable=True, auto_commit="non_binding"),
                                AttributeSpec(key="declared", label="선언축", kind="numeric",
                                              mutable=True, auto_commit="binding")],
                    entities=[EntitySpec(id="hero", name="주인공")])
    out = WorldGenerator._normalize(w, ProjectSeed(title="t"))
    tiers = {a.key: a.auto_commit for a in out.attributes}
    ok = tiers["money_won"] == "non_binding" and tiers["status"] == "non_binding"   # 미지정 → 안전 기본값
    ok &= tiers["truth"] == "non_binding" and tiers["declared"] == "binding"        # 명시 선언 보존
    print(f"[{'OK' if ok else 'FAIL'}] 적재 기본값: 미지정=non_binding · 명시 선언 보존")
    assert ok
    return ok


def test_load_path_unchanged_for_old_json() -> bool:
    """구 JSON(auto_commit 부재) 로드는 ''(미선언=현행) — _normalize 는 로드 경로에 없다."""
    w = WorldConfig.model_validate({"title": "t", "genre": "g",
                                    "attributes": [{"key": "k", "label": "l", "kind": "numeric",
                                                    "mutable": True}]})
    ok = w.attributes[0].auto_commit == ""
    dumped = w.attributes[0].model_dump(exclude_defaults=True)
    ok &= "auto_commit" not in dumped                       # 미선언은 직렬화 미출현(바이트 동일)
    print(f"[{'OK' if ok else 'FAIL'}] 로드 경로 무변경: 구 JSON=미선언 유지·직렬화 미출현")
    assert ok
    return ok


if __name__ == "__main__":
    results = [test_normalize_defaults_unset_to_non_binding(), test_load_path_unchanged_for_old_json()]
    print("\nXR-11 검증:", "ALL GREEN ✅" if all(results) else "FAIL ❌")
    sys.exit(0 if all(results) else 1)
