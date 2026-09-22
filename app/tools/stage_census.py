# -*- coding: utf-8 -*-
"""스테이지 census 공용 AST 스캐너 (XR-36 — cross-review/029 §2.3).

왜: XR-14 census 가 자체 정규식(쌍따옴표 한정)을 갖고 있어 single-quote 스테이지가 사각지대였다
(029 §2.2 — XR-3 인벤토리는 AST 라 읽는데 XR-14 만 못 읽음). 스캐너를 하나로 승격해 두 테스트가
같은 수집기를 쓴다(따옴표 문법 무관·주석/문자열 오탐 0).

수집 대상: `promptlog.stage(<상수>)` · `promptlog.consumer(<상수>)` 의 상수 라벨(호출 형태 불문 —
데코레이터·with·직접 호출 전부 ast.Call 로 잡힌다). 상수가 아닌 인자(변수·f-string)는 census 가
볼 수 없으므로 별도 목록으로 반환한다 — 호출부는 이를 0 으로 강제해야 census 전수성이 성립한다.
"""
from __future__ import annotations
import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "novelcopilot"


def _label_calls(tree: ast.AST):
    """promptlog.stage/consumer 호출 노드 전수."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("stage", "consumer")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "promptlog" and node.args):
            yield node


def declared_labels(root: Path = _PKG) -> set[str]:
    """코드에 선언된 상수 스테이지 라벨 전수(따옴표 문법 무관)."""
    out: set[str] = set()
    for p in sorted(Path(root).rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        for call in _label_calls(tree):
            if isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                out.add(call.args[0].value)
    return out


def dynamic_label_sites(root: Path = _PKG) -> list[str]:
    """상수가 아닌 라벨 인자 지점(census 사각) — 전수성 계약은 이 목록 0 을 요구한다."""
    out: list[str] = []
    for p in sorted(Path(root).rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        for call in _label_calls(tree):
            if not (isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                out.append(f"{p.relative_to(root).as_posix()}:{call.lineno}")
    return out


# ── XR-3 인벤토리용 호출 지점 스캔(기존 test_xr3_inventory_sync 구현의 승격 — 동작 동일) ──
_KINDS = {"chat", "chat_json", "chat_tools"}


def _stage_label(deco) -> str | None:
    """@promptlog.stage("label") 데코레이터의 라벨."""
    if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
            and deco.func.attr == "stage" and isinstance(deco.func.value, ast.Name)
            and deco.func.value.id == "promptlog" and deco.args
            and isinstance(deco.args[0], ast.Constant)):
        return deco.args[0].value
    return None


def _with_label(item) -> str | None:
    """with promptlog.consumer("label"): 의 라벨."""
    cx = item.context_expr
    if (isinstance(cx, ast.Call) and isinstance(cx.func, ast.Attribute)
            and cx.func.attr == "consumer" and isinstance(cx.func.value, ast.Name)
            and cx.func.value.id == "promptlog" and cx.args
            and isinstance(cx.args[0], ast.Constant)):
        return cx.args[0].value
    return None


def scan_call_sites(root: Path = _PKG) -> list[tuple[str, str, str]]:
    """[(파일:함수, kind, consumer 라벨 or '')] — llm/ 은 인프라라 제외(프록시·폴백 위임)."""
    out: list[tuple[str, str, str]] = []
    for p in sorted(Path(root).rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("llm/"):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))

        def walk(node, chain: list[str], tag: str) -> None:
            for ch in ast.iter_child_nodes(node):
                c, t = chain, tag
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    c = chain + [ch.name]
                    for d in getattr(ch, "decorator_list", []):
                        t = _stage_label(d) or t
                if isinstance(ch, (ast.With, ast.AsyncWith)):
                    for it in ch.items:
                        t = _with_label(it) or t
                if (isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute)
                        and ch.func.attr in _KINDS):
                    out.append((f"{rel}:{'.'.join(c) or '<module>'}", ch.func.attr, t))
                walk(ch, c, t)

        walk(tree, [], "")
    return out
