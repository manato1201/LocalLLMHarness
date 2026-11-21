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
    ask(harness, enable_tools=True, enabled_tools=["python", "terminal"], run_tools_locally=True,
        confirm_tool_calls=False, llh={"task": "code"}).close()
    sent = backend.requests[-1]
    # 設計書 S1: 外すだけでなく、false に固定する(無いと Unsloth の画面のトグルが効くため)
    assert sent["enable_tools"] is False and "llh" not in sent
    for k in ("enabled_tools", "run_tools_locally", "confirm_tool_calls"):
        assert k not in sent


def test_server_tools_flag_can_be_left_alone(cfg, backend):
    from dataclasses import replace
    from llh.policy import load_policy

    h = Harness(replace(cfg, disable_server_tools=False), policy=load_policy(cfg.policy_path))
    ask(h, enable_tools=True).close()
    assert "enable_tools" not in backend.requests[-1]  # 外すだけ(false は付けない)


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


# ── モデルの自動読み込み(on_model_mismatch = "load")─────────
@pytest.fixture
def load_harness(cfg):
    from dataclasses import replace
    from llh.policy import load_policy

    return Harness(replace(cfg, on_model_mismatch="load"), policy=load_policy(cfg.policy_path))


def test_load_switches_model_and_audits(load_harness, backend, cfg):
    backend.loaded[:] = [WRITER]
    r = ask(load_harness, llh={"task": "code"})  # main へ
    r.close()
    assert backend.load_calls == [{"model_path": MAIN, "alongside": False, "max_seq_length": 2000}]
    assert backend.loaded == [MAIN]
    assert r.headers["X-LLH-Model-Switched"] == "1"
    assert backend.requests[-1]["model"] == MAIN  # 読み込んだあとで送っている
    assert "model_load_s" in audit_records(cfg)[-1]


def test_load_skipped_when_already_loaded(load_harness, backend):
    backend.loaded[:] = [MAIN]
    r = ask(load_harness)
    r.close()
    assert backend.load_calls == [] and "X-LLH-Model-Switched" not in r.headers


def test_load_splits_repo_and_variant(cfg, backend):
    from dataclasses import replace
    from llh.config import ModelSpec
    from llh.policy import load_policy

    backend.available.append("org/m-GGUF")  # 裏側の id は、変種なし
    models = dict(cfg.models)
    models["main"] = ModelSpec("main", "llh-main", "org/m-GGUF:Q4_K_M", tasks=("code",))
    h = Harness(replace(cfg, models=models, on_model_mismatch="load"), policy=load_policy(cfg.policy_path))
    backend.loaded[:] = [WRITER]
    ask(h).close()
    call = backend.load_calls[-1]
    assert call["model_path"] == "org/m-GGUF" and call["gguf_variant"] == "Q4_K_M" and call["alongside"] is False


def test_load_refused_returns_error_and_releases_lock(load_harness, backend, cfg):
    backend.loaded[:] = [WRITER]
    backend.load_status = 500
    with pytest.raises(HarnessError) as e:
        ask(load_harness)
    assert e.value.code == "model_load_failed" and e.value.status == 502
    assert load_harness._gate.acquire(blocking=False)
    load_harness._gate.release()
    assert audit_records(cfg)[-1]["error"] == "model_load_failed"


def test_load_busy_maps_to_409(load_harness, backend):
    backend.loaded[:] = [WRITER]
    backend.load_status = 409
    with pytest.raises(HarnessError) as e:
        ask(load_harness)
    assert e.value.status == 409 and e.value.code == "model_busy"


def test_load_not_trusted_unless_verified(load_harness, backend):
    backend.loaded[:] = [WRITER]
    backend.load_effective = False  # 200 を返すが、読み込まれない
    n = len(backend.requests)
    with pytest.raises(HarnessError) as e:
        ask(load_harness)
    assert e.value.code == "model_load_failed" and "読み込み済みになりません" in e.value.message
    assert len(backend.requests) == n  # 違うモデルに質問を送っていない


def test_load_unknown_loaded_flag_sends_anyway(load_harness, backend):
    # 裏側が `loaded` を返さない(Unsloth 以外)なら、判断できないので、読み込みは試さずに送る
    load_harness.backend.list_models = lambda cache_s=0.0: [{"id": MAIN}]
    ask(load_harness).close()
    assert backend.load_calls == [] and backend.requests[-1]["model"] == MAIN


def test_model_matches_variants():
    from llh.config import ModelSpec

    s = ModelSpec("main", "llh-main", "unsloth/Q-GGUF:Q4_K_M")
    assert s.matches("unsloth/Q-GGUF") and s.matches("UNSLOTH/q-gguf:q4_k_m") and not s.matches("other")
    w = ModelSpec("main", "x", r"C:\models\a.gguf")  # ドライブの「:」は変種ではない
    assert w.load_path == r"C:\models\a.gguf" and w.load_variant == ""
