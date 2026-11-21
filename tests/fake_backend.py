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
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.auth_headers.append(self.headers.get("Authorization"))
                if self.path.endswith("/models"):
                    data = [{"id": m, "object": "model", "loaded": m in outer.loaded} for m in outer.available]
                    self._send(200, {"object": "list", "data": data})
                else:
                    self._send(404, {"error": "nf"})

            def do_POST(self):
                outer.auth_headers.append(self.headers.get("Authorization"))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
