"""Mermaid の検査と取り出し(設計書 §4: 下書き生成のあと、必ず構文検査を通す)。

検査は 2 段:
  1. 組み込みの簡易検査(図の種類・括弧と引用符の対応・subgraph/end の対応)。ネットワークも Node も要らない。
  2. `mmdc`(mermaid-cli)が PATH にあれば、本物のパーサーで描画できるかを確かめる。
簡易検査を通っても、描画できるとは限らない。確実に確かめたいときは mmdc を入れる。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

DIAGRAM_TYPES = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram-v2", "stateDiagram",
    "erDiagram", "journey", "gantt", "pie", "quadrantChart", "requirementDiagram", "gitGraph",
    "mindmap", "timeline", "C4Context", "C4Container", "C4Component", "sankey-beta", "xychart-beta", "block-beta",
)
_DIRECTIONS = {"TB", "TD", "BT", "RL", "LR"}
_BLOCK_OPENERS_SEQ = ("loop", "alt", "opt", "par", "critical", "break", "rect")
_FENCE = re.compile(r"```[ \t]*mermaid[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_mermaid(text: str) -> str | None:
    """応答から、最初の ```mermaid ブロックの中身を取り出す。フェンスが無く、図の種類で始まるときは、全体を図とみなす。"""
    m = _FENCE.search(text)
    if m:
        return m.group(1).strip("\n")
    stripped = text.strip()
    first = stripped.split(None, 1)[0] if stripped else ""
    if first in DIAGRAM_TYPES or first.startswith("%%{init"):
        return stripped
    return None


def _strip_quoted(line: str) -> str:
    return re.sub(r'"[^"\n]*"', '""', line)


def check_syntax(code: str) -> list[str]:
    """簡易検査。問題の一覧を返す(空なら通過)。"""
    problems: list[str] = []
    lines = [ln.rstrip() for ln in code.splitlines()]
    body = [(i + 1, ln) for i, ln in enumerate(lines) if ln.strip() and not ln.strip().startswith("%%")]
    if not body:
        return ["図が空です"]

    first_no, first = body[0]
    head = first.strip().split()[0]
    diagram = next((t for t in DIAGRAM_TYPES if head == t), None)
    if diagram is None:
        return [f"{first_no}行目: 最初の行が図の種類ではありません('{head}')。{', '.join(DIAGRAM_TYPES[:6])} … のどれかで始めてください"]
    if diagram in ("flowchart", "graph"):
        parts = first.strip().split()
        if len(parts) > 1 and parts[1] not in _DIRECTIONS:
            problems.append(f"{first_no}行目: 向きが不正です('{parts[1]}')。TB / TD / BT / RL / LR のどれか")

    depth = 0
    for no, ln in body[1:]:
        s = ln.strip()
        if s.count('"') % 2:
            problems.append(f"{no}行目: 引用符(\")の数が合いません")
        clean = _strip_quoted(ln)
        # stateDiagram の `state X {` は、複数行にまたがる正しい書き方なので、{} の数は見ない
        for o, c in ("[]", "()") if diagram.startswith("stateDiagram") else ("[]", "()", "{}"):
            if clean.count(o) != clean.count(c):
                problems.append(f"{no}行目: 括弧 {o}{c} の数が合いません")
        word = s.split()[0] if s.split() else ""
        if diagram in ("flowchart", "graph") and word == "subgraph":
            depth += 1
        elif diagram == "sequenceDiagram" and word in _BLOCK_OPENERS_SEQ:
            depth += 1
        elif diagram.startswith("stateDiagram") and s.endswith("{"):
            depth += 1
        elif diagram.startswith("stateDiagram") and s == "}":
            depth -= 1
        elif word == "end" and len(s.split()) == 1 and not diagram.startswith("stateDiagram"):
            depth -= 1
            if depth < 0:
                problems.append(f"{no}行目: 対応する開きの無い end です")
                depth = 0
    if depth > 0:
        problems.append(f"閉じていないブロックが {depth} 個あります(subgraph/loop/alt などに end が足りません)")
    return problems


def check_mmdc(code: str, timeout_s: float = 60.0) -> list[str] | None:
    """mmdc があれば本物のパーサーで検査する。mmdc が無ければ None(検査していない)。"""
    exe = shutil.which("mmdc") or shutil.which("mmdc.cmd")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d) / "in.mmd", Path(d) / "out.svg"
        src.write_text(code, encoding="utf-8")
        try:
            r = subprocess.run([exe, "-i", str(src), "-o", str(out), "-q"], capture_output=True, text=True, timeout=timeout_s)
        except (subprocess.TimeoutExpired, OSError) as e:
            return [f"mmdc を実行できませんでした: {e}"]
        if r.returncode != 0 or not out.exists():
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            return [f"mmdc が描画に失敗しました: {msg[0] if msg else '(詳細なし)'}"]
    return []


def validate(code: str, use_mmdc: bool = True) -> tuple[list[str], str]:
    """(問題の一覧, 使った検査: 'syntax' / 'syntax+mmdc')。"""
    problems = check_syntax(code)
    if problems or not use_mmdc:
        return problems, "syntax"
    real = check_mmdc(code)
    if real is None:
        return [], "syntax"
    return real, "syntax+mmdc"
