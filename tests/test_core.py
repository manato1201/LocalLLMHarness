from __future__ import annotations

import json
import threading
import time

import pytest

from llh.audit import verify_chain
from llh.core import Harness, HarnessError

from conftest import MAIN, VL, WRITER


def ask(h: Harness, text="こんにちは", **extra):
    body = {"messages": [{"role": "user", "content": text}], **extra}
    return h.chat(body)


def audit_records(cfg):
    return [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").splitlines()]


# ── 振り分け ───────────────────────────────────────────────
def test_default_goes_to_main(harness, backend):
    r = ask(harness)
    r.close()
    assert backend.requests[-1]["model"] == MAIN
    assert r.headers["X-LLH-Route"] == "main"


def test_task_routes_to_writer(harness, backend):
    ask(harness, llh={"task": "doc"}).close()
    assert backend.requests[-1]["model"] == WRITER


def test_image_routes_to_vision(harness, backend):
    msg = [{"role": "user", "content": [{"type": "text", "text": "これは?"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    harness.chat({"messages": msg}).close()
    assert backend.requests[-1]["model"] == VL


def test_alias_and_exact_name_accepted_unknown_rejected(harness, backend):
    ask(harness, model="llh-vision").close()
    assert backend.requests[-1]["model"] == VL
    ask(harness, model=WRITER).close()
    assert backend.requests[-1]["model"] == WRITER
    with pytest.raises(HarnessError) as e:
        ask(harness, model="gpt-4o")
    assert e.value.status == 400 and e.value.code == "model_not_found"


def test_unknown_task_rejected(harness):
    with pytest.raises(HarnessError) as e:
        ask(harness, llh={"task": "nonsense"})
    assert e.value.status == 400


# ── 裏側に渡すもの ────────────────────────────────────────
def test_server_side_tools_and_llh_field_are_stripped(harness, backend):
    ask(harness, enable_tools=["python", "terminal"], llh={"task": "code"}).close()
    sent = backend.requests[-1]
    assert "enable_tools" not in sent and "llh" not in sent  # 設計書 S1


def test_client_tools_pass_through(harness, backend):
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    ask(harness, tools=tools).close()
    assert backend.requests[-1]["tools"] == tools


def test_max_tokens_default_and_clamp(harness, backend):
    ask(harness).close()
    assert backend.requests[-1]["max_tokens"] == 100
    ask(harness, max_tokens=99999).close()
    assert backend.requests[-1]["max_tokens"] == 500
    ask(harness, max_completion_tokens=50).close()
    sent = backend.requests[-1]
    assert sent["max_tokens"] == 50 and "max_completion_tokens" not in sent


def test_context_limit_rejects_without_calling_backend(harness, backend):
    n = len(backend.requests)
    with pytest.raises(HarnessError) as e:
        ask(harness, "あ" * 9000)
    assert e.value.code == "context_length_exceeded"
    assert len(backend.requests) == n


def test_backend_api_key_forwarded(harness, backend, monkeypatch):
    monkeypatch.setenv("LLH_TEST_KEY", "sk-unsloth-test")
    ask(harness).close()
    assert "Bearer sk-unsloth-test" in backend.auth_headers


# ── 機密度・監査 ──────────────────────────────────────────
def test_sensitive_request_is_flagged_and_stays_local(harness, backend, cfg):
    r = ask(harness, "my key sk-unsloth-abcdefghijklmnopqrstuv")
    r.close()
    assert r.headers["X-LLH-Sensitivity"] == "local-only"
    assert r.headers["X-LLH-Destination"] == "local"
    rec = audit_records(cfg)[-1]
    assert rec["sensitivity"] == "local-only" and "content:openai_key" in rec["reasons"]


def test_audit_log_has_hashes_not_content(harness, cfg):
    secret_text = "これは内部のプロジェクト名 CODENAME-XYZ の質問です"
    ask(harness, secret_text, llh={"task": "code"}).close()
    raw = cfg.audit_path.read_text(encoding="utf-8")
    assert "CODENAME-XYZ" not in raw and "内部の" not in raw
    rec = json.loads(raw.splitlines()[-1])
    assert len(rec["prompt_sha256"]) == 64 and rec["prompt_chars"] == len(secret_text)
    assert rec["output_chars"] == 1 and rec["role"] == "main" and rec["status"] == 200
    assert rec["prompt_tokens"] == 10 and rec["completion_tokens"] == 5


def test_audit_chain_detects_tampering(harness, cfg):
    for _ in range(3):
        ask(harness).close()
    ok, n, bad = verify_chain(cfg.audit_path)
    assert ok and n == 3
    lines = cfg.audit_path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    cfg.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, _, bad = verify_chain(cfg.audit_path)
    assert not ok and bad == 2


def test_audit_chain_continues_after_restart(cfg, harness):
    ask(harness).close()
    from llh.audit import AuditLog
    from llh.policy import load_policy

    h2 = Harness(cfg, policy=load_policy(cfg.policy_path), audit=AuditLog(cfg.audit_path))
    ask(h2).close()
    assert verify_chain(cfg.audit_path)[:2] == (True, 2)


def test_rejected_request_is_audited(harness, cfg):
    with pytest.raises(HarnessError):
        ask(harness, model="nope")
    assert audit_records(cfg)[-1]["status"] == 400


def test_plan_does_not_call_backend(harness, backend):
    n = len(backend.requests)
    plan = harness.plan({"messages": [{"role": "user", "content": "x"}], "llh": {"task": "doc", "paths": ["D:/x/y.py"]}})
    assert plan["role"] == "writer" and plan["destination"] == "local"
    assert plan["sensitivity"]["sensitive"] and "path:not-in-allowlist" in plan["sensitivity"]["reasons"]
    assert len(backend.requests) == n


def test_manual_header(harness):
    plan = harness.plan({"messages": [{"role": "user", "content": "x"}]}, {"x-llh-local-only": "1"})
    assert plan["sensitivity"]["reasons"] == ["manual"]


# ── モデルの読み込み確認 ──────────────────────────────────
def test_mismatch_warns(harness, backend):
    backend.loaded[:] = [WRITER]
    harness.backend._models_cache = None
    r = ask(harness)
    r.close()
    assert "X-LLH-Warning" in r.headers


def test_mismatch_error_mode(cfg, backend):
    from dataclasses import replace
    from llh.policy import load_policy

    h = Harness(replace(cfg, on_model_mismatch="error"), policy=load_policy(cfg.policy_path))
    backend.loaded[:] = [WRITER]
    with pytest.raises(HarnessError) as e:
        ask(h)
    assert e.value.status == 409


# ── 直列化・ストリーム・エラー ────────────────────────────
def test_requests_are_serialized(harness, backend):
    backend.delay = 0.3
    active, peak = [0], [0]
    lock = threading.Lock()
    orig = harness.backend.chat

    def tracked(body):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            return orig(body)
        finally:
            with lock:
                active[0] -= 1

    harness.backend.chat = tracked
    ts = [threading.Thread(target=lambda: ask(harness).close()) for _ in range(3)]
    t0 = time.monotonic()
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1 and time.monotonic() - t0 >= 0.8


def test_queue_timeout_returns_503(cfg, backend):
    from dataclasses import replace
    from llh.policy import load_policy

    h = Harness(replace(cfg, queue_timeout_s=0.2), policy=load_policy(cfg.policy_path))
    backend.delay = 0.8
    t = threading.Thread(target=lambda: ask(h).close())
    t.start()
    time.sleep(0.2)
    with pytest.raises(HarnessError) as e:
        ask(h)
    t.join()
    assert e.value.status == 503


def test_stream_passthrough_and_audit(harness, backend, cfg):
    backend.reply = "こんにちは世界"
    r = ask(harness, stream=True)
    data = b"".join(r.stream)
    r.close()
    assert b"[DONE]" in data
    rec = audit_records(cfg)[-1]
    assert rec["stream"] is True and rec["output_chars"] == len("こんにちは世界") and rec["status"] == 200
    # ロールが返っていて、次のリクエストが通る(ロック解放)
    ask(harness).close()


def test_stream_closed_early_releases_lock_and_audits_499(harness, backend, cfg):
    backend.reply = "abcdef"
    r = ask(harness, stream=True)
    next(r.stream)
    r.stream.close()
    r.close()
    assert audit_records(cfg)[-1]["status"] == 499
    ask(harness).close()


def test_backend_down_gives_502(cfg):
    from dataclasses import replace
    from llh.policy import load_policy

    h = Harness(replace(cfg, backend_base_url="http://127.0.0.1:9/v1"), policy=load_policy(cfg.policy_path))
    with pytest.raises(HarnessError) as e:
        ask(h)
    assert e.value.status == 502
    # 失敗してもロックが戻っている
    assert h._gate.acquire(blocking=False)
    h._gate.release()


def test_invalid_body(harness):
    for bad in ({}, {"messages": []}, {"messages": "x"}):
        with pytest.raises(HarnessError) as e:
            harness.chat(bad)
        assert e.value.status == 400
