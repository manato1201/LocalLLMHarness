"""窓口の HTTP サーバー(OpenAI 互換)。標準ライブラリだけ。

エンドポイント:
  POST /v1/chat/completions   振り分け・機密度の判定つきで、裏側(Unsloth)へ中継(stream 対応)
  POST /v1/route              送らずに、行き先・モデル・機密度だけを返す(行き先の表示。設計書 Z4)
  GET  /v1/models             使えるモデルの別名(+ 裏側が読み込み済みかどうか)
  GET  /health                死活監視(ToolOrchestrationHub 用)。裏側に届かなくても 200(中身で状態を返す)

守り(設計書 S13): 待ち受けは 127.0.0.1 だけ。Host の許可リスト(DNS rebinding)、Origin の検査(他のサイトからの送信)、
任意のトークン認証。
"""
from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import __version__
from .backend import BackendError
from .config import LOOPBACK_HOSTS, Config
from .core import Harness, HarnessError


def make_server(cfg: Config, harness: Harness | None = None, port: int | None = None) -> ThreadingHTTPServer:
    if cfg.host not in LOOPBACK_HOSTS:
        raise ValueError(f"loopback 以外には待ち受けません: {cfg.host}")
    harness = harness or Harness(cfg)
    listen_port = cfg.port if port is None else port

    class Handler(BaseHTTPRequestHandler):
        server_version = f"llh/{__version__}"
        protocol_version = "HTTP/1.0"  # SSE を、接続を閉じて終える(Content-Length 不要)

        # ── 入口の守り ─────────────────────────────────────
        def _allowed_hosts(self) -> set[str]:
            actual = self.server.server_address[1]
            names = ("127.0.0.1", "localhost", "[::1]")
            return {f"{n}:{actual}" for n in names} | {f"{n}:{cfg.port}" for n in names}

        def _guard(self) -> str | None:
            """通してよければユーザー名("" も可)、止めたら None(応答済み)。"""
            host = (self.headers.get("Host") or "").lower()
            if host not in self._allowed_hosts():
                self._json(403, {"error": {"message": "Host が許可されていません", "type": "forbidden"}})
                return None
            origin = self.headers.get("Origin")
            if origin:
                o = urlsplit(origin)
                origin_host = f"{o.hostname}:{o.port}" if o.port else (o.hostname or "")
                if f"{origin_host}".lower().replace("::1", "[::1]") not in self._allowed_hosts():
                    self._json(403, {"error": {"message": "Origin が許可されていません", "type": "forbidden"}})
                    return None
            token = cfg.token
            if token:
                auth = self.headers.get("Authorization", "")
                given = auth[7:] if auth.lower().startswith("bearer ") else ""
                if not hmac.compare_digest(given.encode(), token.encode()):
                    self._json(401, {"error": {"message": "認証が必要です", "type": "unauthorized"}},
                               extra={"WWW-Authenticate": "Bearer"})
                    return None
                return "token"
            return ""

        # ── 応答の下請け ───────────────────────────────────
        def _json(self, status: int, obj: dict, extra: dict[str, str] | None = None) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:  # 標準エラーに、内容を出さない
            pass

        def _read_json(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0:
                self._json(400, {"error": {"message": "本体(JSON)が必要です", "type": "invalid_request"}})
                return None
            if length > cfg.max_body_bytes:
                self._json(413, {"error": {"message": f"本体が大きすぎます(上限 {cfg.max_body_bytes} バイト)", "type": "invalid_request"}})
                return None
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                self._json(400, {"error": {"message": "本体が JSON ではありません", "type": "invalid_request"}})
                return None
            if not isinstance(body, dict):
                self._json(400, {"error": {"message": "本体は JSON のオブジェクトにしてください", "type": "invalid_request"}})
                return None
            return body

        def _lower_headers(self) -> dict[str, str]:
            return {k.lower(): v for k, v in self.headers.items()}

        # ── ルート ─────────────────────────────────────────
        def do_GET(self) -> None:
            if self._guard() is None:
                return
            path = urlsplit(self.path).path
            if path == "/health":
                self._json(200, self._health())
            elif path == "/v1/models":
                self._json(200, self._models())
            else:
                self._json(404, {"error": {"message": "not found", "type": "not_found"}})

        def do_POST(self) -> None:
            user = self._guard()
            if user is None:
                return
            path = urlsplit(self.path).path
            if path not in ("/v1/chat/completions", "/v1/route"):
                self._json(404, {"error": {"message": "not found", "type": "not_found"}})
                return
            body = self._read_json()
            if body is None:
                return
            try:
                if path == "/v1/route":
                    self._json(200, harness.plan(body, self._lower_headers()))
                    return
                result = harness.chat(body, self._lower_headers(), user=user)
            except HarnessError as e:
                self._json(e.status, e.to_body())
                return
            try:
                if result.body is not None:
                    self._json(200, result.body, extra=result.headers)
                else:
                    self._send_stream(result)
            finally:
                result.close()

        def _send_stream(self, result) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            for k, v in result.headers.items():
                self.send_header(k, v)
            self.end_headers()
            try:
                for line in result.stream:
                    self.wfile.write(line)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                result.stream.close()  # 呼び出し側が切った → 裏側も閉じ、監査ログに 499 を残す
            except BackendError:
                pass

        # ── 中身 ───────────────────────────────────────────
        def _models(self) -> dict:
            loaded: set[str] = set()
            try:
                loaded = {m.get("id") for m in harness.backend.list_models(cache_s=2.0) if m.get("loaded")}
            except BackendError:
                pass
            data = []
            for spec in cfg.models.values():
                data.append({"id": spec.alias, "object": "model", "owned_by": "llh", "role": spec.role,
                             "backend_model": spec.name, "loaded": spec.name in loaded, "purpose": spec.purpose})
            data.append({"id": "auto", "object": "model", "owned_by": "llh", "role": "auto"})
            return {"object": "list", "data": data}

        def _health(self) -> dict:
            info: dict = {"status": "ok", "version": __version__, "local_only": cfg.local_only,
                          "backend": "down", "loaded": [], "policy_sha256": harness.policy.source_hash[:16]}
            try:
                models = harness.backend.list_models()
                info["backend"] = "up"
                info["loaded"] = sorted(str(m.get("id")) for m in models if m.get("loaded"))
            except BackendError as e:
                info["backend_error"] = e.message
                if e.status in (401, 403):
                    info["backend_auth_failed"] = True
                    info["hint"] = harness.backend_error(e).message
            return info

    return ThreadingHTTPServer((cfg.host, listen_port), Handler)


def serve(cfg: Config) -> None:
    server = make_server(cfg)
    host, port = server.server_address[:2]
    print(f"llh: http://{host}:{port}/v1 で待ち受け中(裏側: {cfg.backend_base_url})")
    print(f"     認証: {'トークンあり' if cfg.token else 'なし(loopback のみ)'} / 監査ログ: {cfg.audit_path}")
    from .status import startup_checks

    for w in startup_checks(cfg):
        print(f"     [警告] {w}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def start_in_thread(cfg: Config, harness: Harness | None = None, port: int = 0) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """テスト用: 空きポートで起動して返す。"""
    server = make_server(cfg, harness, port=port)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t
