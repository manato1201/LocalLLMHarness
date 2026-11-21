"""監査ログ(設計書 §9 S4)。追記のみの JSON Lines。

残すのは、**内容そのものでなく、ハッシュ・件数・行き先・モデル・秒数**だけ。
各行に直前の行のハッシュ(`prev`)を入れ、途中の行を消す・書き換える改ざんに気づけるようにする
(誰でも作り直せるので、改ざんを防ぐものではなく、気づくためのもの)。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

GENESIS = "0" * 64


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._prev = self._load_prev()

    def _load_prev(self) -> str:
        if not self.path.exists():
            return GENESIS
        last = b""
        with self.path.open("rb") as f:
            for line in f:
                if line.strip():
                    last = line.rstrip(b"\r\n")
        return hashlib.sha256(last).hexdigest() if last else GENESIS

    def write(self, **fields) -> dict:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}
        with self._lock:
            record["prev"] = self._prev
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as f:
                f.write(line.encode("utf-8") + b"\n")
            self._prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
        return record


def verify_chain(path: str | Path) -> tuple[bool, int, int | None]:
    """ログの連鎖を確かめる。戻り値: (正しいか, 検査した行数, 最初に食い違った行番号(1 始まり))。"""
    prev = GENESIS
    count = 0
    p = Path(path)
    if not p.exists():
        return True, 0, None
    with p.open("rb") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip(b"\r\n")
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                return False, count, n
            if rec.get("prev") != prev:
                return False, count, n
            prev = hashlib.sha256(line).hexdigest()
            count += 1
    return True, count, None
