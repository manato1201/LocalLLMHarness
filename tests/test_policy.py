"""機密度の判定(設計書 Phase 2 の Go: 疑似の秘密 20 件がすべて止まり、通常の入力 20 件が誤って止まらない)。"""
from __future__ import annotations

import pytest

from llh.policy import load_policy, normalize_path

from conftest import ROOT


@pytest.fixture(scope="module")
def policy():
    return load_policy(ROOT / "policy.example.toml")


# 20 件の疑似の秘密(本物ではない。形式だけ合わせた文字列)
FAKE_SECRETS = [
    "key is sk-unsloth-abcdefghijklmnopqrstuv",
    "ANTHROPIC_API_KEY=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA",
    "OPENAI sk-proj-0123456789abcdefghij",
    "aws AKIAABCDEFGHIJKLMNOP here",
    "ghp_" + "a" * 36,
    "github_pat_" + "A1b2C3d4E5" * 5,
    "AIza" + "SyA-abcdefghijklmnopqrstuvwxyz01234"[:35],
    "xoxb-1234567890-abcdefghijkl",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIE...",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
    "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    'password = "hunter2hunter2hunter2"',
    "api_key: abcd1234efgh5678ijkl",
    "SECRET_TOKEN=Zm9vYmFyYmF6cXV4MTIzNDU2",
    "連絡先は someone@example.com まで",
    r"ファイルは C:\Users\matuu\Desktop\Portfolio\resume.docx",
    "ls /home/alice/secrets",
    "open /Users/bob/Documents/x",
    "decoy sk-decoy-00000000000000000000 leaked",
]

NORMAL_INPUTS = [
    "git diff を要約してください",
    "Qwen と Gemma の違いを教えて",
    "def add(a, b):\n    return a + b",
    "flowchart LR\n  A[開始] --> B[終了]",
    "VEX で点群を回転させる方法は?",
    "Unity の UdonSharp でボタンを作りたい",
    "エラーログ: NullReferenceException at Foo.Bar()",
    "RTX 3070 の VRAM は 8GB です",
    "今日の天気をまとめて",
    "pip install requests",
    "SELECT * FROM users WHERE id = 1",
    "https://unsloth.ai/docs/desktop を読んで要約",
    "ポートは 8888 か 8000 です",
    "tokenizer の設定を直す",  # 'token' を含むが秘密ではない
    "the password policy requires 12 characters",  # 説明文
    "Hello world",
    "コミットメッセージ案を作って",
    "model: Q4_K_M",
    "x = {'a': [1, 2, (3)]}",
    "1+1 は?",
]


def test_fake_secrets_are_all_blocked(policy):
    assert len(FAKE_SECRETS) == 20
    missed = [s for s in FAKE_SECRETS if not policy.classify(s).sensitive]
    assert missed == []


def test_normal_inputs_are_not_blocked(policy):
    assert len(NORMAL_INPUTS) == 20
    false_positive = [s for s in NORMAL_INPUTS if policy.classify(s).sensitive]
    assert false_positive == []


def test_verdict_never_contains_matched_text(policy):
    v = policy.classify("key sk-unsloth-abcdefghijklmnopqrstuv")
    assert v.sensitive and all("abcdefgh" not in r for r in v.reasons)


@pytest.mark.parametrize("path", [
    "C:/Users/matuu/Desktop/Portfolio/resume.docx",
    "C:/Users/matuu/Desktop/GameDevelopment/Enterprises/Acme/code.py",
    "C:/Users/matuu/Desktop/GameDevelopment/LocalLLMHarness/.env",
    "C:/Users/matuu/Desktop/GameDevelopment/LocalLLMHarness/.env.local",
    "C:/Users/matuu/.ssh/id_rsa",
    "C:/Users/matuu/Desktop/GameDevelopment/x/server.pem",
])
def test_deny_paths(policy, path):
    assert policy.check_path(path) == ("path:deny",)


def test_path_default_deny_when_not_allowlisted(policy):
    assert policy.check_path("D:/other/project/a.py") == ("path:not-in-allowlist",)
    assert policy.check_path("C:/Windows/System32/x") == ("path:not-in-allowlist",)


def test_allowed_path(policy):
    assert policy.check_path("C:/Users/matuu/Desktop/GameDevelopment/LocalLLMHarness/llh/core.py") == ()
    assert policy.check_path(r"C:\Users\matuu\Desktop\GameDevelopment\ToolOrchestrationHub\README.md") == ()  # 区切りと大文字小文字


def test_dotdot_cannot_escape_allowlist(policy):
    assert policy.check_path("C:/Users/matuu/Desktop/GameDevelopment/../Portfolio/a.docx") != ()
    assert normalize_path("C:/a/b/../c/./d") == "C:/a/c/d"


def test_manual_flag_forces_local_only(policy):
    v = policy.classify("ただの質問", manual=True)
    assert v.sensitive and v.reasons == ("manual",)


def test_egress_filter_blocks_secret(policy):
    assert policy.check_egress("hello").sensitive is False
    assert policy.check_egress("sk-unsloth-abcdefghijklmnopqrstuv").sensitive is True
