from __future__ import annotations

import http.client
import json

import pytest

from llh.config import Config
from llh.server import start_in_thread

from conftest import MAIN


@pytest.fixture
def srv(cfg, harness):
    server, _ = start_in_thread(cfg, harness)
    yield server
    server.shutdown()
    server.server_close()


def call(server, method, path, body=None, headers=None, host=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, dict(r.getheaders()), raw


def chat_body(**kw):
    return {"messages": [{"role": "user", "content": "hi"}], **kw}


def test_health_ok(srv):
    s, _, raw = call(srv, "GET", "/health")
    d = json.loads(raw)
    assert (
        s == 200
        and d["status"] == "ok"
        and d["backend"] == "up"
        and MAIN in d["loaded"]
    )


def test_health_when_backend_down(cfg, harness):
    from dataclasses import replace
    from llh.core import Harness
    from llh.policy import load_policy

    h = Harness(
        replace(cfg, backend_base_url="http://127.0.0.1:9/v1"),
        policy=load_policy(cfg.policy_path),
    )
    server, _ = start_in_thread(cfg, h)
    try:
        s, _, raw = call(server, "GET", "/health")
        assert s == 200 and json.loads(raw)["backend"] == "down"
    finally:
        server.shutdown()
        server.server_close()


def test_models_lists_aliases(srv):
    _, _, raw = call(srv, "GET", "/v1/models")
    ids = [m["id"] for m in json.loads(raw)["data"]]
    assert {"llh-main", "llh-vision", "llh-writer", "auto"} <= set(ids)


def test_chat_roundtrip_with_headers(srv, backend):
    backend.reply = "答え"
    s, hdr, raw = call(srv, "POST", "/v1/chat/completions", chat_body(model="llh-main"))
    assert s == 200
    assert json.loads(raw)["choices"][0]["message"]["content"] == "答え"
    assert hdr["X-LLH-Route"] == "main" and hdr["X-LLH-Destination"] == "local"


def test_stream_roundtrip(srv, backend):
    backend.reply = "ストリーム"
    s, hdr, raw = call(srv, "POST", "/v1/chat/completions", chat_body(stream=True))
    assert s == 200 and "text/event-stream" in hdr["Content-Type"] and b"[DONE]" in raw


def test_route_endpoint_does_not_call_backend(srv, backend):
    n = len(backend.requests)
    s, _, raw = call(srv, "POST", "/v1/route", chat_body(llh={"task": "doc"}))
    assert (
        s == 200 and json.loads(raw)["role"] == "writer" and len(backend.requests) == n
    )


def test_error_shapes(srv):
    s, _, raw = call(srv, "POST", "/v1/chat/completions", chat_body(model="gpt-4"))
    assert s == 400 and json.loads(raw)["error"]["code"] == "model_not_found"
    s, _, _ = call(srv, "POST", "/v1/nope", chat_body())
    assert s == 404


def test_bad_host_rejected_dns_rebinding(srv):
    s, _, _ = call(srv, "GET", "/health", host="evil.example.com:80")
    assert s == 403


def test_foreign_origin_rejected(srv):
    s, _, _ = call(
        srv,
        "POST",
        "/v1/chat/completions",
        chat_body(),
        headers={"Origin": "https://evil.example.com"},
    )
    assert s == 403
    port = srv.server_address[1]
    s, _, _ = call(
        srv,
        "POST",
        "/v1/chat/completions",
        chat_body(),
        headers={"Origin": f"http://localhost:{port}"},
    )
    assert s == 200


def test_token_auth(srv, monkeypatch):
    monkeypatch.setenv("LLH_TEST_TOKEN", "secret-token-123")
    s, hdr, _ = call(srv, "GET", "/health")
    assert s == 401 and hdr.get("WWW-Authenticate") == "Bearer"
    s, _, _ = call(srv, "GET", "/health", headers={"Authorization": "Bearer wrong"})
    assert s == 401
    s, _, _ = call(
        srv, "GET", "/health", headers={"Authorization": "Bearer secret-token-123"}
    )
    assert s == 200


def test_oversized_and_malformed_bodies(srv):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request(
        "POST",
        "/v1/chat/completions",
        body=b"not json",
        headers={"Host": f"127.0.0.1:{port}", "Content-Length": "8"},
    )
    assert conn.getresponse().status == 400
    conn.close()


def test_non_loopback_host_refused():
    from llh.server import make_server

    with pytest.raises(ValueError):
        make_server(Config(host="0.0.0.0"))


def test_health_reports_auth_failure_with_a_hint(srv, backend, monkeypatch):
    backend.require_key = "sk-right"
    monkeypatch.delenv("LLH_TEST_KEY", raising=False)
    s, _, raw = call(srv, "GET", "/health")
    d = json.loads(raw)
    assert s == 200 and d["backend"] == "down" and d["backend_auth_failed"] is True
    assert "LLH_TEST_KEY" in d["hint"]
