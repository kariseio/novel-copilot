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
