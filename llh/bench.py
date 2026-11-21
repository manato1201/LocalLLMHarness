"""入力の長さごとの速度の測定(設計書 Phase 0: 文脈の長さでの変化)。

ストリームで受け、**最初のトークンまでの時間(TTFT ≒ 入力の処理)**と、**その後の生成速度**を分けて測る。
プロンプトの先頭に毎回違う目印を入れる(裏側のプロンプトキャッシュに当たって、2 回目以降が速く見えないように)。
文脈の上限(config の max_context_tokens)を超える長さは測らない(窓口が断るため)。16K・32K を測るには、
config.toml の max_context_tokens を上げ、モデルをその文脈で読み込み直す(VRAM が足りるかは実測)。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from .config import ModelSpec
from .core import Harness, HarnessError

#: 日本語の文章を繰り返して、長さを作る。内容に意味は要らない(長さだけを測る)。
_FILLER = (
    "ローカルLLMの窓口は、機密度の判定、モデルの振り分け、監査ログの記録を担当します。"
    "資料の抜粋や画面のキャプチャは、このPCの外へ出ません。"
    "VRAM が 8GB なので、同時に載せるモデルは 1 つだけで、リクエストは 1 件ずつ処理します。"
)
#: フィラーの 1 トークンあたりの文字数の見積もり(日本語。実測は、結果の「入力」の列に出る)
_CHARS_PER_TOKEN = 1.5
WARMUP_TOKENS = 64


@dataclass
class BenchRow:
    target: int                 # 狙った入力のトークン数
    prompt_tokens: int | None   # 実際の入力(裏側の usage。無ければ None)
    completion_tokens: int | None
    ttft_s: float | None        # 最初のトークンまで
    total_s: float | None
    decode_tps: float | None    # 最初のトークン以降の生成速度
    prefill_tps: float | None   # 入力の処理速度(prompt_tokens / ttft)
    tokens_from: str            # 'usage'(裏側の値)か 'chunks'(受け取ったチャンクの数。目安)
    error: str | None = None


def build_prompt(target_tokens: int, salt: str) -> str:
    chars = max(40, int(target_tokens * _CHARS_PER_TOKEN))
    body = (_FILLER * (chars // len(_FILLER) + 1))[:chars]
    return f"[bench {salt}]\n{body}\n\n上の文章の要点を、日本語で 2 文で書いてください。"


def _measure(h: Harness, spec: ModelSpec, target: int, out_tokens: int) -> BenchRow:
    body = {
        "model": spec.alias, "temperature": 0, "max_tokens": out_tokens, "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": build_prompt(target, uuid.uuid4().hex[:12])}],
    }
    t0 = time.monotonic()
    try:
        res = h.chat(body)
    except HarnessError as e:
        return BenchRow(target, None, None, None, None, None, None, "-", error=f"{e.code}: {e.message}")
    first: float | None = None
    chunks = 0
    usage: dict | None = None
    try:
        for line in res.stream or ():
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == b"[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            usage = obj.get("usage") or usage
            for ch in obj.get("choices", []):
                delta = ch.get("delta") or {}
                if delta.get("content") or delta.get("reasoning_content"):
                    chunks += 1
                    if first is None:
                        first = time.monotonic()
    except Exception as e:  # noqa: BLE001  裏側の切断など。測れた分だけ返す
        return BenchRow(target, None, None, None, None, None, None, "-", error=f"{type(e).__name__}: {e}")
    finally:
        res.close()
    end = time.monotonic()
    if first is None:
        return BenchRow(target, None, None, None, end - t0, None, None, "-", error="応答が空でした")

    from_usage = bool(usage and usage.get("completion_tokens"))
    comp = int(usage["completion_tokens"]) if from_usage else chunks
    prompt = usage.get("prompt_tokens") if usage else None
    ttft = first - t0
    decode_time = end - first
    # 最初のトークンは TTFT に含まれるので、残りの (comp - 1) トークンを、残りの時間で割る
    decode_tps = (comp - 1) / decode_time if comp > 1 and decode_time > 0 else None
    prefill_tps = prompt / ttft if prompt and ttft > 0 else None
    return BenchRow(target, prompt, comp, ttft, end - t0, decode_tps, prefill_tps, "usage" if from_usage else "chunks")


def run_bench(h: Harness, role: str, sizes: list[int], out_tokens: int = 64, warmup: bool = True) -> list[BenchRow]:
    spec = h.cfg.model_for_role(role)
    if h.cfg.on_model_mismatch == "load":
        h.ensure_model_loaded(spec)  # 入れ替えは、測定に混ぜない
    rows: list[BenchRow] = []
    limit = h.cfg.max_context_tokens - out_tokens - 100  # 指示文・区切りの分の余裕
    if warmup:
        _measure(h, spec, WARMUP_TOKENS, 16)  # 初回の遅さ(キャッシュ・コンパイルなど)を、結果から外す
    for size in sizes:
        if size > limit:
            rows.append(BenchRow(size, None, None, None, None, None, None, "-",
                                 error=f"文脈の上限(max_context_tokens={h.cfg.max_context_tokens})を超えるため測りません"))
            continue
        rows.append(_measure(h, spec, size, out_tokens))
    return rows
