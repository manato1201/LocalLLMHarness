from __future__ import annotations

import json

import pytest

from llh.audit import verify_chain
from llh.cli import main
from llh.config import ConfigError, load_config

from conftest import MAIN, ROOT, VL, WRITER


@pytest.fixture
def cfg_file(tmp_path, backend):
    p = tmp_path / "config.toml"
    p.write_text(
        f"""
[backend]
base_url = "{backend.base_url}"
[models.main]
name = "{MAIN}"
tasks = ["code", "diff", "mermaid"]
[models.vision]
name = "{VL}"
tasks = ["vision"]
[models.writer]
name = "{WRITER}"
tasks = ["doc"]
[paths]
policy = "{(ROOT / "policy.example.toml").as_posix()}"
audit_log = "{(tmp_path / "audit.jsonl").as_posix()}"
""",
        encoding="utf-8",
    )
    return str(p)


def run(cfg_file, *args):
    return main(["--config", cfg_file, *args])


def test_config_example_loads():
    cfg = load_config(ROOT / "config.example.toml")
    assert set(cfg.models) == {"main", "vision", "writer"} and cfg.host == "127.0.0.1"
    assert cfg.local_only and cfg.max_context_tokens <= 8192


def test_config_rejects_non_loopback(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        '[server]\nhost = "0.0.0.0"\n[models.main]\nname = "x"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_config_requires_main_model(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[models.vision]\nname = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_status_json(cfg_file, capsys):
    assert run(cfg_file, "status", "--json") == 0
    s = json.loads(capsys.readouterr().out)
    assert (
        s["backend"] == "up"
        and s["configured"]["main"]["loaded"] is True
        and s["listen_loopback_only"]
    )


def test_models_lists(cfg_file, capsys):
    assert run(cfg_file, "models") == 0
    assert MAIN in capsys.readouterr().out


def test_smoke_all_roles(cfg_file, capsys):
    assert run(cfg_file, "smoke", "--role", "all") == 0
    out = capsys.readouterr().out
    assert out.count("OK") == 3


def test_smoke_unknown_role_fails(cfg_file):
    assert run(cfg_file, "smoke", "--role", "nope") == 1


def test_policy_check_relative_path_is_resolved(cfg_file, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    # ROOT は GameDevelopment 配下(許可リスト)にある想定の開発環境。無い環境では、拒否(既定は拒否)になるだけ
    code = run(cfg_file, "policy-check", "llh/core.py")
    out = capsys.readouterr().out
    assert code in (0, 1)
    assert ("path:not-in-allowlist" in out) == (code == 1)
    assert "llh/core.py" in out or "core.py" in out


def test_policy_check_deny_and_secret_content(cfg_file, tmp_path, capsys):
    assert run(cfg_file, "policy-check", "C:/Users/x/Desktop/Portfolio/a.docx") == 1
    f = tmp_path / "notes.txt"
    f.write_text("token sk-unsloth-abcdefghijklmnopqrstuv", encoding="utf-8")
    assert run(cfg_file, "policy-check", str(f)) == 1
    out = capsys.readouterr().out
    assert (
        "content:openai_key" in out and "abcdefghij" not in out
    )  # 一致した文字列は出さない


def test_route_prints_plan(cfg_file, capsys):
    assert run(cfg_file, "route", "要約して", "--task", "doc") == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["role"] == "writer" and plan["destination"] == "local"


def test_audit_verify_cli(cfg_file, backend, capsys):
    run(cfg_file, "smoke")
    assert run(cfg_file, "audit-verify") == 0
    cfg = load_config(cfg_file)
    assert verify_chain(cfg.audit_path)[0]


def test_mermaid_cli_writes_only_valid(cfg_file, backend, tmp_path, capsys):
    out = tmp_path / "d.mmd"
    backend.reply = "```mermaid\nflowchart LR\n  A --> B\n```"
    assert run(cfg_file, "mermaid", "AからB", "--out", str(out), "--no-mmdc") == 0
    assert out.read_text(encoding="utf-8").startswith("flowchart LR")
    bad = tmp_path / "bad.mmd"
    backend.reply = "```mermaid\nflowchart LR\n  A[x --> B\n```"
    assert (
        run(cfg_file, "mermaid", "x", "--out", str(bad), "--attempts", "2", "--no-mmdc")
        == 1
    )
    assert not bad.exists()  # 検査に通らないものは書かない


def test_load_cli(cfg_file, backend, capsys):
    backend.loaded[:] = [WRITER]
    assert run(cfg_file, "load", "--role", "main") == 0
    assert backend.loaded == [MAIN] and "読み込みました" in capsys.readouterr().out
    assert run(cfg_file, "load", "--role", "main") == 0
    assert "すでに" in capsys.readouterr().out
    assert run(cfg_file, "load", "--role", "nope") == 1
    backend.load_status = 500
    backend.loaded[:] = [WRITER]
    assert run(cfg_file, "load", "--role", "main") == 2


@pytest.mark.parametrize("bad", ["sk-unsloth-…", "sk-unsloth-日本語", " sk-abc ", "sk-abc\n"])
def test_invalid_api_key_gives_readable_error(cfg_file, monkeypatch, capsys, bad):
    monkeypatch.setenv("LLH_BACKEND_API_KEY", bad)
    assert run(cfg_file, "models") == 2  # トレースバックでなく、メッセージ
    out = capsys.readouterr().out
    assert "LLH_BACKEND_API_KEY" in out and "不正" in out
    assert "SECRETMARK" not in out  # キーの中身は出さない


def test_config_thinking_must_be_bool(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[models.main]\nname = "x"\nenable_thinking = "no"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text('[models.main]\nname = "x"\nenable_thinking = false\n', encoding="utf-8")
    assert load_config(p).models["main"].enable_thinking is False


def test_smoke_warns_on_empty_reply(cfg_file, backend, capsys):
    backend.reply = ""
    assert run(cfg_file, "smoke") == 0
    assert "本文が空" in capsys.readouterr().out


def test_smoke_with_image_routes_as_image_message(cfg_file, backend, tmp_path, capsys):
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    assert run(cfg_file, "smoke", "--role", "vision", "--image", str(img)) == 0
    content = backend.requests[-1]["messages"][0]["content"]
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_smoke_image_errors(cfg_file, tmp_path, capsys):
    assert run(cfg_file, "smoke", "--role", "vision", "--image", str(tmp_path / "none.png")) == 1
    bad = tmp_path / "a.txt"
    bad.write_text("x")
    assert run(cfg_file, "smoke", "--role", "vision", "--image", str(bad)) == 1
    assert "対応していない拡張子" in capsys.readouterr().out
