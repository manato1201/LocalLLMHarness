from __future__ import annotations

import json
from dataclasses import replace

import pytest

from llh.core import Harness
from llh.policy import load_policy
from llh.verify import verified_generate


def body(text="q", **kw):
    return {"messages": [{"role": "user", "content": text}], "max_tokens": 50, **kw}


def has_ok(message):
    ok = "OK" in (message.get("content") or "")
    return ok, "" if ok else "OK という語がありません"


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_passes_first_time_without_retry(harness, backend):
    backend.reply = "OK です"
    g = verified_generate(harness, body(), has_ok)
    assert g.passed and g.attempts == 1 and not g.escalate and g.reply == "OK です" and g.history == []
    assert g.prompt_tokens == 10 and g.completion_tokens == 5


def test_retries_with_feedback_until_it_passes(harness, backend):
    backend.replies = ["だめ", "まだだめ", "OK!"]
    g = verified_generate(harness, body(), has_ok, max_attempts=3)
    assert g.passed and g.attempts == 3 and g.history == ["OK という語がありません"] * 2
    assert g.prompt_tokens == 30 and g.completion_tokens == 15, "全試行の合計"
    second = backend.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == "だめ" and "OK という語がありません" in second[2]["content"]
    assert [m["role"] for m in backend.requests[2]["messages"]] == ["user", "assistant", "user", "assistant", "user"]


def test_gives_up_and_escalates_with_a_content_free_record(cfg, harness, backend):
    secret_prompt = "社外秘のプロジェクト CODENAME-ZZZ の質問"
    backend.reply = "ずっとだめ"
    g = verified_generate(harness, body(secret_prompt, llh={"task": "code"}), has_ok, max_attempts=2, label="code-xyz")
    assert not g.passed and g.escalate and g.attempts == 2 and "OK" in g.detail
    recs = lines(cfg.escalation_path)
    assert len(recs) == 1
    r = recs[0]
    assert r["label"] == "code-xyz" and r["attempts"] == 2 and r["reason"] == "verify_failed" and r["task"] == "code"
    assert len(r["prompt_sha256"]) == 64 and r["prompt_chars"] == len(secret_prompt)
    raw = cfg.escalation_path.read_text(encoding="utf-8")
    assert "CODENAME-ZZZ" not in raw and "ずっとだめ" not in raw, "内容は残さない"


def test_backend_error_escalates_without_crashing(cfg):
    h = Harness(replace(cfg, backend_base_url="http://127.0.0.1:9/v1"), policy=load_policy(cfg.policy_path))
    g = verified_generate(h, body(), has_ok)
    assert g.escalate and g.error and g.attempts == 1
    assert lines(cfg.escalation_path)[0]["reason"] == g.error


def test_check_exception_propagates(harness):
    def boom(_m):
        raise RuntimeError("検証できない")

    with pytest.raises(RuntimeError):
        verified_generate(harness, body(), boom)


def test_stream_and_bad_attempts_are_rejected(harness):
    with pytest.raises(ValueError):
        verified_generate(harness, body(stream=True), has_ok)
    with pytest.raises(ValueError):
        verified_generate(harness, body(), has_ok, max_attempts=0)


def test_record_false_writes_nothing(cfg, harness, backend):
    backend.reply = "x"
    verified_generate(harness, body(), has_ok, max_attempts=1, record=False)
    assert not cfg.escalation_path.exists()


def test_verified_generations_saved_only_when_enabled_and_not_sensitive(cfg, backend):
    h_off = Harness(cfg, policy=load_policy(cfg.policy_path))
    backend.reply = "OK"
    verified_generate(h_off, body("普通の質問"), has_ok)
    assert not cfg.verified_path.exists(), "既定は保存しない"

    h = Harness(replace(cfg, save_verified=True), policy=load_policy(cfg.policy_path))
    verified_generate(h, body("普通の質問"), has_ok, label="ok-1")
    verified_generate(h, body("キーは sk-test-abcdefghijklmnopqrstuvwx"), has_ok, label="secret")
    recs = lines(cfg.verified_path)
    assert [r["label"] for r in recs] == ["ok-1"], "「外に出さない」区分は、学習データにも入れない"
    assert recs[0]["reply"] == "OK" and recs[0]["messages"][0]["content"] == "普通の質問"
