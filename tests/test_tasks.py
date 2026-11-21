from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from llh import mermaid
from llh.tasks import generate_mermaid, run_git, summarize_diff

from conftest import ROOT


# ── Mermaid の検査 ─────────────────────────────────────────
def test_mermaid_extract():
    assert mermaid.extract_mermaid("説明\n```mermaid\nflowchart LR\n  A --> B\n```\n以上") == "flowchart LR\n  A --> B"
    assert mermaid.extract_mermaid("flowchart TD\n  A --> B") is not None
    assert mermaid.extract_mermaid("図はありません") is None


def test_mermaid_design_doc_diagrams_pass():
    """設計書(ROOT 内)の実在する図が、簡易検査で誤って落ちない。"""
    text = (ROOT / "LocalLLMHarness_DESIGN.md").read_text(encoding="utf-8")
    blocks = [b for b in mermaid._FENCE.findall(text)]
    for b in blocks:
        assert mermaid.check_syntax(b) == []


@pytest.mark.parametrize("code,needle", [
    ("hello world", "図の種類"),
    ("flowchart XX\n A --> B", "向き"),
    ("flowchart LR\n  A[開始 --> B", "括弧"),
    ('flowchart LR\n  A["開始 --> B', "引用符"),
    ("flowchart LR\n  subgraph X\n    A --> B", "閉じていない"),
    ("flowchart LR\n  A --> B\n  end", "end"),
    ("sequenceDiagram\n  loop 毎回\n    A->>B: hi", "閉じていない"),
    ("", "空"),
])
def test_mermaid_detects_problems(code, needle):
    problems = mermaid.check_syntax(code)
    assert problems and any(needle in p for p in problems), problems


def test_mermaid_valid_samples():
    assert mermaid.check_syntax('flowchart LR\n  subgraph S["グループ (a)"]\n    A[開始] --> B{判断}\n  end') == []
    assert mermaid.check_syntax("sequenceDiagram\n  alt ok\n    A->>B: hi\n  else ng\n    B-->>A: no\n  end") == []
    assert mermaid.check_syntax("stateDiagram-v2\n  [*] --> A\n  state A {\n    [*] --> B\n  }") == []


# ── 生成 → 検査 → 作り直し ────────────────────────────────
def test_generate_mermaid_retries_until_valid(harness, backend):
    backend.replies = [
        "```mermaid\nflowchart LR\n  A[開始 --> B\n```",          # 括弧が合わない
        "```mermaid\nflowchart LR\n  A[開始] --> B[終了]\n```",  # 合格
    ]
    r = generate_mermaid(harness, "開始から終了までの図", use_mmdc=False)
    assert r.ok and r.attempts == 2 and "A[開始] --> B[終了]" in r.code
    # 2 回目の依頼に、見つかった問題が含まれている
    second = backend.requests[-1]["messages"][-1]["content"]
    assert "括弧" in second


def test_generate_mermaid_gives_up(harness, backend):
    backend.reply = "図は作れません"
    r = generate_mermaid(harness, "x", max_attempts=2, use_mmdc=False)
    assert not r.ok and r.attempts == 2 and r.problems


# ── git diff の要約 ───────────────────────────────────────
def git(repo: Path, *a: str):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "a.txt").write_text("one\n", encoding="utf-8")
    git(r, "add", ".")
    git(r, "commit", "-qm", "feat: initial")
    return r


def test_diff_summary_none_when_clean(harness, repo):
    assert summarize_diff(harness, repo) is None


def test_diff_summary_sends_diff_and_never_commits(harness, backend, repo):
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    backend.reply = "要約: two を追加\nコミットメッセージ案: feat: add two"
    before = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    r = summarize_diff(harness, repo)
    after = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    assert before == after and r and "two" in r.summary
    prompt = backend.requests[-1]["messages"][0]["content"]
    assert "+two" in prompt and "feat: initial" in prompt  # 差分と、最近のコミットの書き方
    assert backend.requests[-1]["model"] == "main-model"


def test_diff_summary_truncates_long_diff(harness, repo):
    (repo / "a.txt").write_text("x\n" * 20000, encoding="utf-8")
    r = summarize_diff(harness, repo)
    assert r.truncated


def test_only_readonly_git_subcommands(repo):
    for bad in ("commit", "push", "reset", "checkout", "config"):
        with pytest.raises(ValueError):
            run_git(repo, bad)
