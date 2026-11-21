"""テスト用の偽の Unsloth(OpenAI 互換)。受け取ったリクエストを記録する。"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeBackend:
    def __init__(self, loaded: list[str] | None = None, reply: str = "2", delay: float = 0.0):
        self.loaded = loaded if loaded is not None else []
        self.available = list(self.loaded)
        self.reply = reply
        self.delay = delay
        self.requests: list[dict] = []
        self.auth_headers: list[str | None] = []
        self.replies: list[str] = []  # 先頭から順に返す(空なら reply)
        self.load_calls: list[dict] = []
        self.embed_calls: list[dict] = []
        self.embed_status = 200
        self.load_status = 200        # /load が返す HTTP ステータス
        self.load_effective = True    # False なら、200 を返しても、実際には読み込まない
        self.require_key: str | None = None  # 設定すると、このキーの Bearer が無い要求を 401 で断る(Unsloth の挙動)
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _denied(self) -> bool:
                if outer.require_key and self.headers.get("Authorization") != f"Bearer {outer.require_key}":
                    self._send(401, {"error": {"message": "Not authenticated", "type": "authentication_error"}})
                    return True
                return False

            def do_GET(self):
                outer.auth_headers.append(self.headers.get("Authorization"))
                if self._denied():
                    return
                if self.path.endswith("/models"):
                    data = [{"id": m, "object": "model", "loaded": m in outer.loaded} for m in outer.available]
                    self._send(200, {"object": "list", "data": data})
                else:
                    self._send(404, {"error": "nf"})

            def do_POST(self):
                outer.auth_headers.append(self.headers.get("Authorization"))
                if self._denied():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/embeddings"):
                    outer.embed_calls.append(body)
                    if outer.embed_status != 200:
                        self._send(outer.embed_status, {"error": {"message": "no embedding model"}})
                        return
                    inputs = body["input"]
                    self._send(200, {"data": [{"index": i, "embedding": [float(i), 0.5, -0.25]} for i in reversed(range(len(inputs)))]})
                    return
                if self.path.endswith("/load"):
                    outer.load_calls.append(body)
                    if outer.load_status != 200:
                        self._send(outer.load_status, {"error": {"message": "load refused"}})
                        return
                    if outer.load_effective:
                        match = [m for m in outer.available if m.lower() == body["model_path"].lower()]
                        outer.loaded[:] = match[:1] if not body.get("alongside") else outer.loaded + match[:1]
                    self._send(200, {"status": "loaded", "model": body["model_path"]})
                    return
                outer.requests.append(body)
                if outer.delay:
                    time.sleep(outer.delay)
                text = outer.replies.pop(0) if outer.replies else outer.reply
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for piece in [text[: len(text) // 2], text[len(text) // 2 :]]:
                        chunk = {"choices": [{"delta": {"content": piece}}]}
                        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    self._send(200, {
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    })

            def _send(self, status, obj):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._t.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
