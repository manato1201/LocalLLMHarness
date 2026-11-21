from __future__ import annotations

from dataclasses import replace

import pytest

from llh import bench
from llh.cli import main
from llh.core import Harness
from llh.policy import load_policy

from conftest import MAIN


def _h(cfg, **kw):
    return Harness(replace(cfg, **kw), policy=load_policy(cfg.policy_path))


def test_prompt_has_unique_salt_and_requested_length():
    a, b = bench.build_prompt(1000, "aaa"), bench.build_prompt(1000, "bbb")
    assert a != b and a.startswith("[bench aaa]") and b.startswith("[bench bbb]")
    assert len(bench.build_prompt(2000, "x")) > len(bench.build_prompt(500, "x")) * 3


def test_rows_for_each_size_and_warmup_not_reported(cfg, backend):
    backend.reply = "要点です。二文目です。"
    cfg2 = replace(cfg, max_context_tokens=8192)
    rows = bench.run_bench(_h(cfg2), "main", [200, 400], out_tokens=32)
    assert [r.target for r in rows] == [200, 400]
    assert all(r.error is None and r.ttft_s is not None for r in rows)
    assert len(backend.requests) == 3, "ウォームアップ 1 回 + 2 サイズ"
    prompts = [r["messages"][0]["content"] for r in backend.requests]
    assert len(set(p.split("\n", 1)[0] for p in prompts)) == 3, "毎回違う目印(キャッシュに当たらない)"
    assert all(r["stream"] is True and r["max_tokens"] <= 32 for r in backend.requests)


def test_tokens_from_chunks_when_backend_gives_no_usage(cfg, backend):
    backend.reply = "あいうえおかきくけこ"
    rows = bench.run_bench(_h(replace(cfg, max_context_tokens=8192)), "main", [200], out_tokens=32, warmup=False)
    r = rows[0]
    assert r.tokens_from == "chunks" and r.completion_tokens == 2 and r.prefill_tps is None  # 偽の裏側は、2 チャンクで返す


def test_size_over_context_is_skipped_not_sent(cfg, backend):
    cfg2 = replace(cfg, max_context_tokens=1000)
    n = len(backend.requests)
    rows = bench.run_bench(_h(cfg2), "main", [200, 5000], out_tokens=32, warmup=False)
    assert rows[0].error is None
    assert rows[1].error and "文脈の上限" in rows[1].error
    assert len(backend.requests) == n + 1


def test_backend_error_is_reported_per_row(cfg):
    h = _h(replace(cfg, backend_base_url="http://127.0.0.1:9/v1", max_context_tokens=8192))
    rows = bench.run_bench(h, "main", [200], out_tokens=32, warmup=False)
    assert rows[0].error and rows[0].ttft_s is None


def test_load_mode_switches_model_before_measuring(cfg, backend):
    backend.loaded[:] = ["writer-model"]
    h = _h(replace(cfg, on_model_mismatch="load", max_context_tokens=8192))
    bench.run_bench(h, "main", [200], out_tokens=32, warmup=False)
    assert backend.load_calls and backend.load_calls[-1]["model_path"] == MAIN


def test_bench_cli_prints_table(tmp_path, backend, capsys):
    from conftest import ROOT, VL, WRITER

    p = tmp_path / "config.toml"
    p.write_text(f"""
[backend]
base_url = "{backend.base_url}"
[models.main]
name = "{MAIN}"
[limits]
max_context_tokens = 8192
[paths]
policy = "{(ROOT / 'policy.example.toml').as_posix()}"
audit_log = "{(tmp_path / 'a.jsonl').as_posix()}"
""", encoding="utf-8")
    assert main(["--config", str(p), "bench", "--sizes", "200,400", "--out-tokens", "32"]) == 0
    out = capsys.readouterr().out
    assert "TTFT" in out and "200" in out and "400" in out and "目安" in out
    assert main(["--config", str(p), "bench", "--sizes", "abc"]) == 1
    assert main(["--config", str(p), "bench", "--role", "nope"]) == 1
