"""推論の裏側(B1: Unsloth Desktop)への接続。OpenAI 互換 API。標準ライブラリだけ。

使うのは `/models` と `/chat/completions` だけ。サーバー側ツール(`enable_tools` など)は、窓口が外して false に固定する(core.py)。
"""
from __future__ import annotations

import http.client
import json
import time
from collections.abc import Iterator
from urllib.parse import urlsplit

from .config import Config


class BackendError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Backend:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        u = urlsplit(cfg.backend_base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise BackendError(500, f"backend.base_url が不正です: {cfg.backend_base_url}")
        self._https = u.scheme == "https"
        self._host = u.hostname
        self._port = u.port or (443 if self._https else 80)
        self._prefix = u.path.rstrip("/")
        self._models_cache: tuple[float, list[dict]] | None = None

    # ── 下請け ────────────────────────────────────────────
    def _conn(self, timeout: float | None = None) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        return cls(self._host, self._port, timeout=timeout or self.cfg.backend_timeout_s)

    def _headers(self, json_body: bool) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if json_body:
            h["Content-Type"] = "application/json"
        key = self.cfg.backend_api_key
        if key:
            # HTTP ヘッダーに入れられない文字(説明文の「…」を、そのまま入れた場合など)は、トレースバックでなく
            # 原因が分かるエラーにする。キーの中身は、メッセージに出さない。
            if not (key.isascii() and key.isprintable() and key == key.strip()):
                raise BackendError(
                    500,
                    f"環境変数 {self.cfg.backend_api_key_env} の値が不正です(英数字と記号だけ、前後に空白なし)。"
                    "説明文の「sk-unsloth-…」を、そのまま入れていませんか? Unsloth の Settings > API で作った、本物のキーを入れてください",
                )
            h["Authorization"] = f"Bearer {key}"
        return h

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        conn = self._conn(timeout)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        try:
            conn.request(method, self._prefix + path, body=payload, headers=self._headers(body is not None))
            return conn, conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            raise BackendError(502, f"推論の裏側({self.cfg.backend_base_url})に接続できません: {e}") from e

    @staticmethod
    def _error_message(status: int, raw: bytes) -> str:
        try:
            err = json.loads(raw).get("error")
            if isinstance(err, dict):
                err = err.get("message")
            if err:
                return str(err)[:500]
        except (ValueError, AttributeError):
            pass
        return f"推論の裏側がエラーを返しました(HTTP {status})"

    # ── 公開 ──────────────────────────────────────────────
    def list_models(self, cache_s: float = 0.0) -> list[dict]:
        now = time.monotonic()
        if cache_s and self._models_cache and now - self._models_cache[0] < cache_s:
            return self._models_cache[1]
        conn, resp = self._request("GET", "/models")
        try:
            raw = resp.read()
            if resp.status != 200:
                # 認証の拒否(401/403)は、原因が分かるように、そのまま伝える(窓口の利用者への 401 とは別物)
                raise BackendError(resp.status if resp.status in (401, 403) else 502, self._error_message(resp.status, raw))
            data = json.loads(raw).get("data", [])
        except ValueError as e:
            raise BackendError(502, "推論の裏側の /models の応答が JSON ではありません") from e
        finally:
            conn.close()
        self._models_cache = (now, data)
        return data

    def load_model(self, model_path: str, gguf_variant: str = "", max_seq_length: int = 0) -> dict:
        """`POST /v1/load`。`alongside` は常に false(VRAM 8GB。読み込み済みのモデルは入れ替わる)。

        `force_cancel_active` は付けない: 他のクライアントが生成中なら、止めずに 409 で断られる。
        """
        body: dict = {"model_path": model_path, "alongside": False}
        if gguf_variant:
            body["gguf_variant"] = gguf_variant
        if max_seq_length:
            body["max_seq_length"] = max_seq_length
        conn, resp = self._request("POST", "/load", body, timeout=self.cfg.load_timeout_s)
        try:
            raw = resp.read()
            if resp.status != 200:
                raise BackendError(resp.status if 400 <= resp.status < 600 else 502, self._error_message(resp.status, raw))
            try:
                return json.loads(raw) if raw.strip() else {}
            except ValueError:
                return {}  # 応答の形は、読み込み後に /models で確かめるので、ここでは問わない
        except (OSError, http.client.HTTPException) as e:  # 読み込み中のタイムアウトなど
            raise BackendError(504, f"モデルの読み込みの応答を待てませんでした: {e}") from e
        finally:
            conn.close()
            self._models_cache = None

    def chat(self, body: dict) -> dict:
        conn, resp = self._request("POST", "/chat/completions", body)
        try:
            raw = resp.read()
            if resp.status != 200:
                raise BackendError(resp.status if 400 <= resp.status < 600 else 502, self._error_message(resp.status, raw))
            try:
                return json.loads(raw)
            except ValueError as e:
                raise BackendError(502, "推論の裏側の応答が JSON ではありません") from e
        finally:
            conn.close()

    def chat_stream(self, body: dict) -> Iterator[bytes]:
        """SSE の行を、そのまま(改行つきで)流す。最後に接続を閉じる。"""
        conn, resp = self._request("POST", "/chat/completions", body)
        if resp.status != 200:
            raw = resp.read()
            conn.close()
            raise BackendError(resp.status if 400 <= resp.status < 600 else 502, self._error_message(resp.status, raw))

        def gen() -> Iterator[bytes]:
            try:
                while True:
                    line = resp.readline()
                    if not line:
                        return
                    yield line
            finally:
                conn.close()

        return gen()
