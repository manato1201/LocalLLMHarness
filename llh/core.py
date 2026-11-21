"""窓口の中身(HTTP に依存しない)。サーバーも CLI も、ここを呼ぶ。

流れ: 検証 → ① 機密度の判定 → ② 振り分け → 文脈の上限 → 直列化 → 裏側へ送る → ⑥ 監査ログ。
(設計書 §2 の ①②⑥。③手順・④検証は tasks.py、⑤出口のフィルターはクラウド経路が無いので policy.check_egress に用意だけ)
"""
from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from .audit import AuditLog, sha256_text
from .backend import Backend, BackendError
from .config import Config
from .policy import Policy, Verdict, load_policy
from .router import Route, RouteError, resolve

IMAGE_TOKEN_ESTIMATE = 1000
#: 裏側へ渡さないフィールド。サーバー側ツール(Python 実行・ターミナル)は、窓口が許さない(設計書 S1)。
STRIPPED_FIELDS = ("enable_tools", "llh")


class HarnessError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def to_body(self) -> dict:
        return {"error": {"message": self.message, "type": self.code, "code": self.code}}


@dataclass
class ChatResult:
    """`body` か `stream` のどちらかが入る。`close()` を必ず呼ぶ(直列化のロックを返す)。"""

    headers: dict[str, str]
    body: dict | None = None
    stream: Iterator[bytes] | None = None
    _close: Callable[[], None] = field(default=lambda: None, repr=False)

    def close(self) -> None:
        self._close()


def message_text(messages: list) -> str:
    """policy に見せるテキスト(画像のデータは含めない)。"""
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    parts.append(p["text"])
        for call in m.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                parts.append(fn["arguments"])
    return "\n".join(parts)


def estimate_tokens(messages: list) -> int:
    """トークン数の見積もり(厳密ではない)。日本語は 1 文字 ≒ 1 トークン前後、英語は 4 文字 ≒ 1 トークン。
    安全側に、3 文字 ≒ 1 トークンとして数える。"""
    text = message_text(messages)
    images = sum(
        1
        for m in messages
        if isinstance(m, dict) and isinstance(m.get("content"), list)
        for p in m["content"]
        if isinstance(p, dict) and p.get("type") in ("image_url", "input_image", "image")
    )
    return math.ceil(len(text) / 3) + images * IMAGE_TOKEN_ESTIMATE + 4 * len(messages)


class Harness:
    def __init__(self, cfg: Config, policy: Policy | None = None, backend: Backend | None = None, audit: AuditLog | None = None):
        self.cfg = cfg
        self.policy = policy or load_policy(cfg.policy_path)
        self.backend = backend or Backend(cfg)
        self.audit = audit or AuditLog(cfg.audit_path)
        # VRAM 8GB・Unsloth は同時実行数が不明(設計書 A13)なので、窓口が 1 度に 1 件だけ通す。
        self._gate = threading.Lock()

    # ── 判定だけ(送らない)────────────────────────────────
    def plan(self, body: dict, headers: dict[str, str] | None = None) -> dict:
        """行き先の表示(設計書 Z4)用。実際には送らず、振り分けと機密度だけを返す。"""
        req = self._prepare(body, headers or {})
        return {
            "role": req.route.role,
            "model": req.route.model.name,
            "reason": req.route.reason,
            "destination": "local",
            "destination_text": "この質問は、このPCの中だけで処理されます。",
            "sensitivity": req.verdict.to_dict(),
            "estimated_prompt_tokens": req.est_tokens,
        }

    # ── 本体 ───────────────────────────────────────────────
    def chat(self, body: dict, headers: dict[str, str] | None = None, user: str = "") -> ChatResult:
        request_id = uuid.uuid4().hex[:12]
        t0 = time.monotonic()
        headers = headers or {}
        try:
            req = self._prepare(body, headers)
        except HarnessError as e:
            self._audit(request_id, user, status=e.status, error=e.code, seconds=time.monotonic() - t0, body=body)
            raise

        out_headers = {
            "X-LLH-Request-Id": request_id,
            "X-LLH-Route": req.route.role,
            "X-LLH-Model": req.route.model.name,
            "X-LLH-Destination": "local",
            "X-LLH-Sensitivity": req.verdict.destination,
        }
        warning = self._mismatch_warning(req.route)  # 'error' のときは例外
        if warning:
            out_headers["X-LLH-Warning"] = warning

        if not self._gate.acquire(timeout=self.cfg.queue_timeout_s):
            e = HarnessError(503, "busy", "前のリクエストが終わらないため、待ち時間の上限を超えました")
            self._audit(request_id, user, req=req, status=503, error=e.code, seconds=time.monotonic() - t0)
            raise e
        released = threading.Event()

        def release() -> None:
            if not released.is_set():
                released.set()
                self._gate.release()

        try:
            if req.forward.get("stream"):
                raw_iter = self.backend.chat_stream(req.forward)
                return ChatResult(
                    headers=out_headers,
                    stream=self._tap_stream(raw_iter, req, request_id, user, t0, release),
                    _close=release,
                )
            resp = self.backend.chat(req.forward)
        except BackendError as e:
            release()
            self._audit(request_id, user, req=req, status=e.status, error="backend", seconds=time.monotonic() - t0)
            raise HarnessError(e.status, "backend_error", e.message) from e
        except BaseException:
            release()
            raise
        release()
        content = _first_message(resp)
        self._audit(
            request_id, user, req=req, status=200, seconds=time.monotonic() - t0,
            output=content, usage=resp.get("usage"),
        )
        return ChatResult(headers=out_headers, body=resp)

    # ── 内部 ───────────────────────────────────────────────
    def _prepare(self, body: dict, headers: dict[str, str]) -> "_Prepared":
        if not isinstance(body, dict):
            raise HarnessError(400, "invalid_request", "リクエストの本体が JSON のオブジェクトではありません")
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HarnessError(400, "invalid_request", "messages が必要です")
        meta = body.get("llh") if isinstance(body.get("llh"), dict) else {}

        manual = bool(meta.get("local_only")) or headers.get(self.policy.manual_header.lower(), "").strip().lower() in ("1", "true", "yes")
        paths = [p for p in meta.get("paths", []) if isinstance(p, str)] if isinstance(meta.get("paths"), list) else []
        verdict = self.policy.classify(message_text(messages), paths, manual)

        try:
            route = resolve(self.cfg, body.get("model"), meta.get("task"), messages)
        except RouteError as e:
            raise HarnessError(400, "model_not_found", str(e)) from e

        est = estimate_tokens(messages)
        forward = {k: v for k, v in body.items() if k not in STRIPPED_FIELDS}
        forward["model"] = route.model.name
        max_out = forward.get("max_tokens", forward.get("max_completion_tokens"))
        if not isinstance(max_out, int) or max_out <= 0:
            max_out = self.cfg.default_max_tokens
        max_out = min(max_out, self.cfg.max_output_tokens)
        forward.pop("max_completion_tokens", None)
        forward["max_tokens"] = max_out

        if est + max_out > self.cfg.max_context_tokens:
            raise HarnessError(
                400, "context_length_exceeded",
                f"入力(約 {est} トークン)と出力の上限({max_out})の合計が、文脈の上限 {self.cfg.max_context_tokens} を超えます。"
                "入力を短くするか、必要な箇所だけ渡してください(設計書 A3)",
            )
        return _Prepared(route=route, verdict=verdict, forward=forward, est_tokens=est, messages=messages)

    def _mismatch_warning(self, route: Route) -> str:
        mode = self.cfg.on_model_mismatch
        if mode == "off":
            return ""
        try:
            models = self.backend.list_models(cache_s=5.0)
        except BackendError:
            return ""  # 到達できないことは、送るときのエラーで分かる
        if not any("loaded" in m for m in models):
            return ""  # 裏側が loaded を返さない → 判断できない
        loaded = {m.get("id") for m in models if m.get("loaded")}
        if route.model.name in loaded:
            return ""
        msg = (
            f"モデル '{route.model.name}' は読み込まれていません(読み込み済み: {', '.join(sorted(map(str, loaded))) or 'なし'})。"
            "Unsloth Desktop で読み込んでください(API からは切り替えられません)"
        )
        if mode == "error":
            raise HarnessError(409, "model_not_loaded", msg)
        return _ascii_header(msg)

    def _tap_stream(self, raw: Iterator[bytes], req: "_Prepared", request_id: str, user: str, t0: float, release: Callable[[], None]) -> Iterator[bytes]:
        chunks: list[str] = []
        usage = None
        status = 200
        error = None
        try:
            for line in raw:
                if line.startswith(b"data:"):
                    payload = line[5:].strip()
                    if payload and payload != b"[DONE]":
                        try:
                            obj = json.loads(payload)
                            for ch in obj.get("choices", []):
                                piece = (ch.get("delta") or {}).get("content")
                                if piece:
                                    chunks.append(piece)
                            usage = obj.get("usage") or usage
                        except ValueError:
                            pass
                yield line
        except GeneratorExit:
            status, error = 499, "client_closed"
            raise
        except BackendError as e:
            status, error = e.status, "backend"
            raise
        except Exception:
            status, error = 502, "stream_error"
            raise
        finally:
            release()
            self._audit(request_id, user, req=req, status=status, error=error, seconds=time.monotonic() - t0,
                        output="".join(chunks), usage=usage)

    def _audit(self, request_id: str, user: str, *, status: int, seconds: float, req: "_Prepared | None" = None,
               error: str | None = None, output: str | None = None, usage: dict | None = None, body: dict | None = None) -> None:
        rec: dict = {"id": request_id, "user": user or None, "status": status, "seconds": round(seconds, 3),
                     "destination": "local", "policy_sha256": self.policy.source_hash[:16]}
        if error:
            rec["error"] = error
        if req is not None:
            text = message_text(req.messages)
            rec.update(
                role=req.route.role, model=req.route.model.name, reason=req.route.reason,
                sensitivity=req.verdict.destination, reasons=list(req.verdict.reasons),
                prompt_sha256=sha256_text(text), prompt_chars=len(text), messages=len(req.messages),
                est_prompt_tokens=req.est_tokens, stream=bool(req.forward.get("stream")),
            )
        elif isinstance(body, dict) and isinstance(body.get("messages"), list):
            text = message_text(body["messages"])
            rec.update(prompt_sha256=sha256_text(text), prompt_chars=len(text))
        if output is not None:
            rec.update(output_sha256=sha256_text(output), output_chars=len(output))
        if isinstance(usage, dict):
            rec.update(prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
        try:
            self.audit.write(**rec)
        except OSError:
            pass  # 記録できなくても、応答は止めない(ディスクの問題は status コマンドで見る)


@dataclass
class _Prepared:
    route: Route
    verdict: Verdict
    forward: dict
    est_tokens: int
    messages: list


def _first_message(resp: dict) -> str:
    try:
        content = resp["choices"][0]["message"].get("content")
        return content if isinstance(content, str) else ""
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _ascii_header(text: str) -> str:
    """HTTP ヘッダーに日本語は入れられないので、パーセントエンコードする。"""
    from urllib.parse import quote

    return quote(text, safe=" ,:'()-._")[:900]
