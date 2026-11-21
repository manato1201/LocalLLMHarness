from __future__ import annotations

import io
import json
import subprocess
import sys
from dataclasses import replace

import pytest

from llh.config import ModelSpec
from llh.core import Harness
from llh.mcp_server import LIMITS, PROTOCOL_VERSIONS, TOOLS, McpServer
from llh.policy import load_policy

from conftest import ROOT, WRITER


@pytest.fixture
def big(cfg):
    return Harness(replace(cfg, max_context_tokens=8192, max_output_tokens=2048), policy=load_policy(cfg.policy_path))


@pytest.fixture
def server(big):
    return McpServer(big, rag_url="http://127.0.0.1:9", rag_key="")


def rpc(server, method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "method": method, **({"id": mid} if mid is not None else {})}
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def call(server, name, args):
    r = rpc(server, "tools/call", {"name": name, "arguments": args})["result"]
    return r["isError"], json.loads(r["content"][0]["text"])


# ── プロトコル ───────────────────────────────────────────
def test_initialize_negotiates_version_and_lists_capabilities(server):
    r = rpc(server, "initialize", {"protocolVersion": "2024-11-05"})["result"]
    assert r["protocolVersion"] == "2024-11-05" and r["serverInfo"]["name"] == "local-llm-mcp"
    assert r["capabilities"]["tools"] == {"listChanged": False}
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]


def test_tools_list_has_schemas_for_all_five(server):
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == ["local_summarize", "local_classify", "local_extract", "local_ask_docs", "local_embed"]
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)
    assert tools == TOOLS


def test_notifications_get_no_response_and_unknown_method_errors(server):
    assert rpc(server, "notifications/initialized", mid=None) is None
    assert rpc(server, "ping")["result"] == {}
    assert rpc(server, "no/such")["error"]["code"] == -32601
    assert rpc(server, "tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
    assert server.handle({"method": "x"})["error"]["code"] == -32600
    assert server.handle([1])["error"]["code"] == -32600


def test_stdio_loop_handles_garbage_and_batches(server):
    inp = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n[]\n\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    out = io.StringIO()
    server.serve(inp, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["result"] == {} and lines[1]["error"]["code"] == -32700 and lines[2]["error"]["code"] == -32600
    assert len(lines) == 3, "通知には返さない"


def test_real_subprocess_speaks_json_rpc_over_stdio(tmp_path, backend):
    p = tmp_path / "config.toml"
    p.write_text(f'[backend]\nbase_url = "{backend.base_url}"\n[models.main]\nname = "m"\n[paths]\npolicy = "{(ROOT / "policy.example.toml").as_posix()}"\naudit_log = "{(tmp_path / "a.jsonl").as_posix()}"\n', encoding="utf-8")
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    r = subprocess.run([sys.executable, "-m", "llh", "--config", str(p), "mcp"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=ROOT)
    out = [json.loads(x) for x in r.stdout.splitlines()]
    assert [o["id"] for o in out] == [1, 2] and len(out[1]["result"]["tools"]) == 5
    assert "待ち受け中" in r.stderr and r.stdout.count("\n") == 2, "標準出力には、プロトコルのメッセージだけ"


# ── local_summarize ──────────────────────────────────────
def test_summarize_text_with_meta(server, backend):
    backend.reply = "要約です"
    err, out = call(server, "local_summarize", {"text": "長い文章です。" * 10, "sentences": 2})
    assert not err and out["ok"] and out["result"]["summary"] == "要約です"
    assert out["meta"]["model"] == WRITER and out["meta"]["seconds"] >= 0 and out["meta"]["escalate"] is False


def test_summarize_argument_errors(server):
    for args in ({}, {"text": "a", "path": "b"}, {"text": "x" * (LIMITS["text_chars"] + 1)}, {"text": "a", "sentences": 99}, {"text": "a", "timeout_s": -1}):
        err, out = call(server, "local_summarize", args)
        assert err and not out["ok"] and out["error"]


def test_sensitive_input_is_refused_and_never_reaches_the_model(server, backend):
    n = len(backend.requests)
    err, out = call(server, "local_summarize", {"text": "キーは sk-test-abcdefghijklmnopqrstuvwx です"})
    assert err and "外に出さない" in out["error"] and "content:openai_key" in out["error"]
    assert "abcdefghijkl" not in out["error"], "一致した文字列は出さない"
    assert len(backend.requests) == n


def test_sensitive_path_is_refused(server, backend, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("本文", encoding="utf-8")
    n = len(backend.requests)
    err, out = call(server, "local_summarize", {"path": str(f)})   # 許可リストの外 = 既定は拒否
    assert err and "path:not-in-allowlist" in out["error"] and len(backend.requests) == n


def test_timeout_is_enforced_between_calls(server, monkeypatch):
    import llh.mcp_server as ms

    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 60.0   # 呼ばれるたびに、60 秒進む(時間のかかる処理の代わり)
        return clock["t"]

    monkeypatch.setattr(ms.time, "monotonic", fake_monotonic)
    long_text = "\n\n".join("段落" + "あ" * 7000 for _ in range(5))   # 複数の断片 = 複数回の呼び出し
    err, out = call(server, "local_summarize", {"text": long_text, "timeout_s": 150})
    assert err and "実行時間の上限" in out["error"]


# ── local_classify ───────────────────────────────────────
def test_classify_returns_labels_in_order(server, backend):
    backend.reply = '{"labels": ["error", "info"]}'
    err, out = call(server, "local_classify", {"items": ["NullReference", "started"], "labels": ["error", "warning", "info"]})
    assert not err and out["result"]["results"] == [{"index": 0, "label": "error"}, {"index": 1, "label": "info"}]
    assert out["meta"]["escalate"] is False and out["meta"]["attempts"] == 1
    assert backend.requests[-1]["messages"][0]["content"].count("\n") >= 2


def test_classify_retries_on_invalid_label_then_passes(server, backend):
    backend.replies = ['{"labels": ["fatal", "info"]}', '{"labels": ["error", "info"]}']
    err, out = call(server, "local_classify", {"items": ["a", "b"], "labels": ["error", "info"]})
    assert not err and out["meta"]["attempts"] == 2 and out["result"]["results"][0]["label"] == "error"
    assert "候補にないラベル" in backend.requests[1]["messages"][-1]["content"]


def test_classify_escalates_when_never_valid(server, backend):
    backend.reply = "分類できません"
    err, out = call(server, "local_classify", {"items": ["a"], "labels": ["x", "y"]})
    assert not err and out["meta"]["escalate"] is True and out["result"]["results"] == [{"index": 0, "label": None}]


def test_classify_batches_large_inputs(server, backend):
    items = [f"item{i}" for i in range(95)]
    backend.reply = json.dumps({"labels": ["x"] * 40})
    backend.replies = [json.dumps({"labels": ["x"] * 40}), json.dumps({"labels": ["x"] * 40}), json.dumps({"labels": ["x"] * 15})]
    err, out = call(server, "local_classify", {"items": items, "labels": ["x", "y"]})
    assert not err and len(out["result"]["results"]) == 95 and out["result"]["results"][94]["index"] == 94
    assert out["meta"]["calls"] == 3


@pytest.mark.parametrize("args", [
    {"items": [], "labels": ["a"]}, {"items": ["a"], "labels": []}, {"items": ["a"], "labels": ["a", "a"]},
    {"items": [1], "labels": ["a"]}, {"items": ["a"] * (LIMITS["items"] + 1), "labels": ["a"]},
])
def test_classify_input_validation(server, args):
    err, out = call(server, "local_classify", args)
    assert err and not out["ok"]


# ── local_extract ────────────────────────────────────────
def test_extract_validates_types(server, backend):
    backend.replies = ['{"date": "2026-11-03", "total": "12800"}', '```json\n{"date": "2026-11-03", "total": 12800}\n```']
    err, out = call(server, "local_extract", {"text": "11/3 に 12,800 円", "fields": {"date": "string", "total": "integer"}})
    assert not err and out["result"]["data"] == {"date": "2026-11-03", "total": 12800} and out["meta"]["attempts"] == 2
    assert "'total' が integer でない" in backend.requests[1]["messages"][-1]["content"]


def test_extract_rejects_extra_keys_and_escalates(server, backend):
    backend.reply = '{"date": "x", "total": 1, "extra": 2}'
    err, out = call(server, "local_extract", {"text": "t", "fields": {"date": "string", "total": "integer"}})
    assert not err and out["meta"]["escalate"] is True and out["result"]["data"] is None and "余計なキー" in out["result"]["detail"]


def test_extract_null_is_allowed_for_missing_values(server, backend):
    backend.reply = '{"date": null}'
    err, out = call(server, "local_extract", {"text": "日付なし", "fields": {"date": "string"}})
    assert not err and out["result"]["data"] == {"date": None}


@pytest.mark.parametrize("fields", [{}, {"a": "date"}, "x", {f"k{i}": "string" for i in range(LIMITS["fields"] + 1)}])
def test_extract_field_validation(server, fields):
    err, out = call(server, "local_extract", {"text": "t", "fields": fields})
    assert err and not out["ok"]


def test_extract_refuses_sensitive_text(server, backend):
    n = len(backend.requests)
    err, out = call(server, "local_extract", {"text": "連絡先 someone@example.com", "fields": {"mail": "string"}})
    assert err and "外に出さない" in out["error"] and len(backend.requests) == n


# ── local_ask_docs ───────────────────────────────────────
def test_ask_docs_requires_key_and_loopback(big):
    s = McpServer(big, rag_url="http://127.0.0.1:9", rag_key="")
    err, out = call(s, "local_ask_docs", {"question": "q"})
    assert err and "LLH_RAG_API_KEY" in out["error"]
    for bad in ("http://example.com", "https://127.0.0.1", "http://u:p@127.0.0.1"):
        err, out = call(McpServer(big, rag_url=bad, rag_key="k"), "local_ask_docs", {"question": "q"})
        assert err and "loopback" in out["error"]
    err, out = call(McpServer(big, rag_url="http://127.0.0.1:9", rag_key="k"), "local_ask_docs", {"question": "q"})
    assert err and "接続できません" in out["error"]


def test_ask_docs_roundtrip(big):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen["key"] = self.headers.get("X-API-Key")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            out = json.dumps({"answer": "答え [1]", "sources": [{"file": "a.md", "cited": True}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = McpServer(big, rag_url=f"http://127.0.0.1:{srv.server_address[1]}", rag_key="secret-key")
        err, out = call(s, "local_ask_docs", {"question": "Zorb の既定値は?"})
    finally:
        srv.shutdown()
        srv.server_close()
    assert not err and out["result"]["answer"] == "答え [1]" and out["result"]["sources"] == [{"file": "a.md", "cited": True}]
    assert seen["key"] == "secret-key" and seen["body"]["query"] == "Zorb の既定値は?"
    assert "secret-key" not in json.dumps(out), "キーは、結果に出さない"


# ── local_embed ──────────────────────────────────────────
def test_embed_needs_a_configured_model(server):
    err, out = call(server, "local_embed", {"texts": ["a"]})
    assert err and "[models.embed]" in out["error"]


def test_embed_returns_dimensions_and_optionally_vectors(cfg, backend):
    models = dict(cfg.models)
    models["embed"] = ModelSpec("embed", "llh-embed", "embed-model")
    h = Harness(replace(cfg, models=models, max_context_tokens=8192), policy=load_policy(cfg.policy_path))
    s = McpServer(h)
    err, out = call(s, "local_embed", {"texts": ["a", "b"]})
    assert not err and out["result"] == {"count": 2, "dimensions": 3} and "vectors" not in out["result"]
    err, out = call(s, "local_embed", {"texts": ["a", "b"], "include_vectors": True})
    assert out["result"]["vectors"][0][0] == 0.0 and out["result"]["vectors"][1][0] == 1.0, "入力と同じ順に並べ直す"
    assert backend.embed_calls[-1] == {"model": "embed-model", "input": ["a", "b"]}


def test_embed_backend_without_model_is_a_readable_error(cfg, backend):
    models = dict(cfg.models)
    models["embed"] = ModelSpec("embed", "llh-embed", "embed-model")
    backend.embed_status = 404
    s = McpServer(Harness(replace(cfg, models=models, max_context_tokens=8192), policy=load_policy(cfg.policy_path)))
    err, out = call(s, "local_embed", {"texts": ["a"]})
    assert err and "裏側のエラー" in out["error"]


def test_unexpected_exceptions_do_not_crash_the_server(server, monkeypatch):
    monkeypatch.setattr(server, "_summarize", lambda a, d: 1 / 0)
    server._tools["local_summarize"] = server._summarize
    err, out = call(server, "local_summarize", {"text": "a"})
    assert err and "内部エラー" in out["error"]
    assert rpc(server, "ping")["result"] == {}
