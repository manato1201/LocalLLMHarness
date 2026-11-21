"""微調整(Phase 6)の準備: 検証に通った生成を、学習データ(chat 形式の JSONL)にまとめる。

設計書 Phase 6: データは「A1 で**検証に通った生成結果**」と「Claude が解いた問題の答え(利用規約を確認のうえ)」。
**評価セットは学習に混ぜない**。ここでは、次を機械で守る:
  - 「外に出さない」区分(秘密・禁止パス)の入力・出力は、入れない
  - 評価セット(evals/cases)の問題と同じ質問は、入れない(評価の汚染を防ぐ)
  - 重複は 1 件にする
学習そのものは、ここでは行わない(Unsloth Studio の学習画面で行う。docs/PHASE6.md)。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .core import message_text
from .policy import Policy

#: 微調整を始めてよいかの目安(設計書: まず RAG と手順。効かないものは入れない)。実測で見直す。
MIN_EXAMPLES_TO_TRAIN = 300


@dataclass
class ExportReport:
    read: int = 0
    written: int = 0
    skipped_sensitive: int = 0
    skipped_eval_leak: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    by_label: dict[str, int] = field(default_factory=dict)
    out_path: str = ""

    @property
    def ready(self) -> bool:
        return self.written >= MIN_EXAMPLES_TO_TRAIN


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def eval_prompts(cases_dir: str | Path) -> set[str]:
    """評価セットの質問(正規化したもの)。評価の汚染の検査に使う。"""
    from .evals import load_cases

    p = Path(cases_dir)
    if not p.exists():
        return set()
    out: set[str] = set()
    for c in load_cases(p):
        out.add(_norm(message_text(c.messages)))
        for m in c.messages:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                out.add(_norm(m["content"]))
    out.discard("")
    return out


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    rows.append({})
    return rows


def _example(row: dict) -> list[dict] | None:
    """verified.jsonl の 1 行(messages + reply)か、chat 形式の 1 行(messages の最後が assistant)を、会話にする。"""
    msgs = row.get("messages")
    if not isinstance(msgs, list) or not msgs or not all(isinstance(m, dict) and "role" in m and "content" in m for m in msgs):
        return None
    if msgs[-1]["role"] == "assistant":
        return msgs
    reply = row.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None
    return msgs + [{"role": "assistant", "content": reply}]


def export_sft(policy: Policy, out_path: str | Path, sources: list[str | Path], eval_cases_dir: str | Path | None) -> ExportReport:
    rep = ExportReport(out_path=str(out_path))
    leaks = eval_prompts(eval_cases_dir) if eval_cases_dir else set()
    seen: set[str] = set()
    kept: list[dict] = []
    for src in sources:
        for row in _read_jsonl(Path(src)):
            rep.read += 1
            conv = _example(row)
            if conv is None or any(not isinstance(m["content"], str) for m in conv):
                rep.skipped_invalid += 1   # 画像つきなど、文字だけでない会話は、対象外
                continue
            text = message_text(conv)
            if policy.classify(text).sensitive:
                rep.skipped_sensitive += 1
                continue
            users = [_norm(m["content"]) for m in conv if m["role"] == "user"]
            if _norm(message_text(conv[:-1])) in leaks or any(u in leaks for u in users):
                rep.skipped_eval_leak += 1
                continue
            key = hashlib.sha256(json.dumps(conv, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            if key in seen:
                rep.skipped_duplicate += 1
                continue
            seen.add(key)
            kept.append({"messages": conv})
            label = str(row.get("label") or "")
            label = label.split(":")[0] if label else "(なし)"
            rep.by_label[label] = rep.by_label.get(label, 0) + 1
    rep.written = len(kept)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rep
