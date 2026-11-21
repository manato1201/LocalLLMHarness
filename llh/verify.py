"""検証つき生成(設計書 A1)と、エスカレーションの記録(A6)。

生成 → **機械で検証**(コンパイル・テスト・スキーマ・構文検査など)→ 失敗の内容を渡して再生成(最大 N 回)。
検証役は生成役と別(決定的な検証があるときに使う。検証できない仕事には、使わない)。
N 回で通らなければ、`escalate=True`(Claude か人に回す)にして、記録を残す(次の評価問題の元になる)。

記録には**内容を残さない**(ハッシュ・文字数・区分・検証の詳細だけ。設計書 S4)。
`[training] save_verified = true` のときだけ、検証に通った生成を、学習用に別ファイルへ保存する(Phase 6。ローカルのみ)。
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .audit import sha256_text
from .core import Harness, HarnessError, message_text

Check = Callable[[dict], tuple[bool, str]]

FEEDBACK = ("検証で次の問題が見つかりました。直した答えだけを、もう一度出力してください"
            "(前置き・言い訳は不要。形式の指定は、最初の依頼のとおり)。\n- {detail}")


@dataclass
class Generation:
    message: dict
    passed: bool
    attempts: int
    detail: str = ""                   # 最後の検証の詳細(通ったときは空)
    seconds: float = 0.0               # 全試行の合計
    prompt_tokens: int = 0
    completion_tokens: int = 0
    history: list[str] = field(default_factory=list)   # 各試行の検証の詳細(最後の 1 つ前まで)
    escalate: bool = False
    error: str = ""

    @property
    def reply(self) -> str:
        return self.message.get("content") or ""


def verified_generate(h: Harness, body: dict, check: Check, max_attempts: int = 3, *, label: str = "",
                      record: bool = True) -> Generation:
    """`body`(chat のリクエスト)で生成し、`check(message) -> (通ったか, 詳細)` で検証する。通らなければ、
    詳細を渡して作り直す。`check` が例外を投げたら、そのまま伝える(検証できない=採点しない、などの扱いは呼び出し側)。

    窓口の `llh` フィールド(task など)は、全試行に付ける。ストリームは使えない(検証は全文が要る)。
    """
    if body.get("stream"):
        raise ValueError("verified_generate はストリームに対応しません")
    if max_attempts < 1:
        raise ValueError("max_attempts は 1 以上")
    messages = list(body["messages"])
    t0 = time.monotonic()
    gen = Generation(message={}, passed=False, attempts=0)
    for attempt in range(1, max_attempts + 1):
        try:
            res = h.chat({**body, "messages": messages})
        except HarnessError as e:
            gen.attempts = attempt
            gen.error = f"{e.code}: {e.message}"
            gen.detail = gen.error
            break
        try:
            resp = res.body or {}
        finally:
            res.close()
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        usage = resp.get("usage") or {}
        gen.message = message
        gen.attempts = attempt
        gen.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        gen.completion_tokens += int(usage.get("completion_tokens") or 0)
        ok, detail = check(message)
        if ok:
            gen.passed, gen.detail = True, ""
            break
        gen.detail = detail or "検証に通りませんでした"
        if attempt < max_attempts:
            gen.history.append(gen.detail)
            reply = message.get("content") or ""
            messages = messages + [
                {"role": "assistant", "content": reply or "(空の応答)"},
                {"role": "user", "content": FEEDBACK.format(detail=gen.detail)},
            ]
    gen.seconds = time.monotonic() - t0
    gen.escalate = not gen.passed
    if record:
        if gen.escalate:
            _write_escalation(h, body, gen, label)
        elif h.cfg.save_verified:
            _write_verified(h, body, gen, label)
    return gen


def _append(path: Path, rec: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass  # 記録できなくても、生成の結果は返す


def _write_escalation(h: Harness, body: dict, gen: Generation, label: str) -> None:
    """検証に通らなかった依頼。**内容は残さない**(ハッシュ・文字数・検証の詳細だけ)。"""
    text = message_text(body["messages"])
    _append(h.cfg.escalation_path, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "label": label, "attempts": gen.attempts,
        "reason": gen.error or "verify_failed", "detail": gen.detail[:300],
        "prompt_sha256": sha256_text(text), "prompt_chars": len(text),
        "model": body.get("model"), "task": (body.get("llh") or {}).get("task"),
    })


def _write_verified(h: Harness, body: dict, gen: Generation, label: str) -> None:
    """検証に通った生成を、学習用に保存する(Phase 6)。**内容を含む**ので、ローカルのみ・git に入れない。"""
    text = message_text(body["messages"])
    verdict = h.policy.classify(text + "\n" + gen.reply)
    if verdict.sensitive:
        return  # 「外に出さない」区分は、学習データにも入れない(秘密を覚えさせない)
    _append(h.cfg.verified_path, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "label": label, "attempts": gen.attempts,
        "messages": body["messages"], "reply": gen.reply, "model": body.get("model"),
    })
