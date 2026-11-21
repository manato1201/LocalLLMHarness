from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from llh import draft, evals
from llh.cli import main
from llh.policy import load_policy

from conftest import ROOT


@pytest.fixture(scope="module")
def policy():
    return load_policy(ROOT / "policy.example.toml")


@pytest.fixture(scope="module")
def real(policy):
    """実在の公開リポジトリから、案を作った結果(1 回だけ。数十秒かかる)。"""
    import time

    t = time.monotonic()
    ds = draft.build_all(ROOT.parent, policy)
    return ds, time.monotonic() - t


def git(repo: Path, *a: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *a],
                       check=True, capture_output=True, text=True, encoding="utf-8")
    return r.stdout


def make_repo(root: Path, name: str, commits: list[tuple[str, dict[str, str]]]) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    for msg, files in commits:
        for rel, text in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", msg)
    return repo


BODY = "\n".join(f"def func_{i}(x):\n    return x + {i}\n" for i in range(40))


# ── Mermaid の読み取り ─────────────────────────────────────
def test_parse_flowchart_keeps_parentheses_inside_labels():
    code = "flowchart TD\n  A[本文(§0〜§7)] --> B{追補が上書きしている?}\n  B -->|はい| C([採用])\n  B -->|いいえ| D[(DB)]\n"
    labels, edges = draft.parse_flowchart(code)
    assert labels == {"A": "本文(§0〜§7)", "B": "追補が上書きしている?", "C": "採用", "D": "DB"}
    assert ("A", "B", "") in edges and ("B", "C", "はい") in edges and ("B", "D", "いいえ") in edges


def test_parse_flowchart_handles_br_and_quotes_and_skips_directives():
    code = 'flowchart LR\n  classDef x fill:#fff\n  subgraph G\n  A["一行目<br/>二行目"] --> B[終わり]\n  end\n  style A fill:#000\n'
    labels, edges = draft.parse_flowchart(code)
    assert labels == {"A": "一行目 二行目", "B": "終わり"} and edges == [("A", "B", "")]


# ── 採点の許容(全角・半角・空白) ──────────────────────────────
def test_count_ignores_width_and_whitespace():
    case = evals.parse_case({"id": "x", "category": "X", "prompt": "q",
                             "score": {"type": "count", "words": ["metricsを自動記録", "承認制・最大10件", "確認?"], "min": 3}})
    ok, _ = evals.score_case(case, {"content": "metrics を 自動記録 / 承認制・最大 10 件 / 確認？"}, evals.ScoreContext())
    assert ok


# ── コミットから ───────────────────────────────────────────
def test_commit_drafts_use_identifiers_from_the_real_message(tmp_path, policy, monkeypatch):
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Alpha"])
    make_repo(tmp_path, "Alpha", [
        ("feat: 初期化", {"a.py": BODY}),
        ("feat: 窓口に enable_thinking の指定を足し、README.md に説明を書く", {"a.py": BODY + "\nenable_thinking = False\n" * 8, "README.md": "# t\nenable_thinking の説明\n" * 6}),
    ])
    ds = draft.commit_drafts(tmp_path, policy)
    assert len(ds.drafts) == 1
    c = ds.drafts[0].case
    words = c["score"][0]["words"]
    assert "enable_thinking" in words and c["category"] == "コミット文案"
    assert any(s.get("pattern", "").startswith("^(feat|fix") for s in c["score"]), "リポジトリが Conventional なら、接頭辞も確かめる"
    assert "feat: 初期化" in c["prompt"] and "enable_thinking" in c["prompt"]


def test_commits_with_secrets_or_binaries_are_skipped(tmp_path, policy, monkeypatch):
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Beta"])
    make_repo(tmp_path, "Beta", [
        ("feat: 初期化", {"a.py": BODY}),
        ("feat: 設定の token を secret_value に足す", {"a.py": BODY + "api_key = 'abcdefghijklmnop1234'\n" * 8}),
    ])
    ds = draft.commit_drafts(tmp_path, policy)
    assert ds.drafts == [] and any("外に出さない" in s.reason for s in ds.skipped)


def test_missing_repo_is_reported_not_crashing(tmp_path, policy, monkeypatch):
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Nope"])
    ds = draft.commit_drafts(tmp_path, policy)
    assert ds.drafts == [] and ds.skipped[0].reason == "リポジトリが見つからない"


def test_classify_drafts_label_from_prefix(tmp_path, policy, monkeypatch):
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Gamma"])
    make_repo(tmp_path, "Gamma", [
        ("chore: 初期化", {"a.py": BODY}),
        ("fix: 境界の判定を直す", {"a.py": BODY.replace("x + 1", "x - 1") + "\n# fix\n" * 6}),
    ])
    ds = draft.classify_drafts(tmp_path, policy, per_label={"fix": 2, "chore": 2})
    labels = sorted(d.case["score"][0]["expected"] for d in ds.drafts)
    assert labels == ["chore", "fix"] or labels == ["fix"], labels
    assert all("fix: " not in d.case["prompt"] and "境界の判定" not in d.case["prompt"] for d in ds.drafts), "メッセージ(答え)は、問題に入れない"


# ── 文書から ───────────────────────────────────────────────
def test_qa_drafts_verify_the_answer_exists_in_the_real_document(tmp_path, policy, monkeypatch):
    make_repo(tmp_path, "Doc", [("docs: 説明", {"README.md": "# T\n\n" + "前置き。\n" * 20 + "\n起動は `uv run hub` で、http://127.0.0.1:8790 に出ます。\n\n" + "後書き。\n" * 20})])
    monkeypatch.setattr(draft, "QA_SPECS", [
        ("Doc", "README.md", "uv run hub", "起動したときの URL は?", ["127.0.0.1:8790"]),
        ("Doc", "README.md", "uv run hub", "答えが文書に無い質問", ["9999"]),
        ("Doc", "README.md", "存在しない語", "根拠が無い質問", ["x"]),
        ("Doc", "NONE.md", "x", "ファイルが無い質問", ["x"]),
    ])
    ds = draft.qa_drafts(tmp_path, policy)
    assert len(ds.drafts) == 1 and len(ds.skipped) == 3
    c = ds.drafts[0].case
    assert "127.0.0.1:8790" in c["prompt"] and c["score"][0]["all"] == ["127.0.0.1:8790"]
    cited = c["score"][1]["cited"][0]
    assert cited in (1, 2) and f"[{cited}] " in c["prompt"]
    reasons = " ".join(s.reason for s in ds.skipped)
    assert "答えの語" in reasons and "根拠の語" in reasons and "ファイルが見つからない" in reasons


def test_real_qa_specs_still_match_the_current_documents(policy):
    """文書が変わって、根拠が無くなったら、案が静かに減る。減ったことに気づけるように、実物で確かめる。"""
    ds = draft.qa_drafts(ROOT.parent, policy)
    # 作業ツリーで変更中(未コミット)のファイルは、設計どおり材料にしない。それ以外が除外されたら、文書が変わって根拠が消えている
    expected_skips = {
        f"{repo}/{rel}" for repo, rel, *_ in draft.QA_SPECS
        if rel in git(ROOT.parent / repo, "diff", "--name-only", "HEAD").splitlines()
        or rel not in git(ROOT.parent / repo, "ls-files").splitlines()
    }
    assert {s.source for s in ds.skipped} == expected_skips, [s.reason for s in ds.skipped]
    assert len(ds.drafts) == len(draft.QA_SPECS) - len(expected_skips)


def test_summary_drafts_pick_terms_from_the_section(tmp_path, policy, monkeypatch):
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Doc"])
    sec = "## 使い方\n\n" + "`llh eval` で `--repeat` と `--baseline` を使う。`policy.toml` と `config.toml` も。" * 8
    make_repo(tmp_path, "Doc", [("docs: 使い方", {"README.md": "# T\n\n" + sec})])
    ds = draft.summary_drafts(tmp_path, policy)
    assert len(ds.drafts) == 1
    words = ds.drafts[0].case["score"][0]["words"]
    assert set(words) <= {"llh", "eval", "--repeat", "--baseline", "policy.toml", "config.toml"}
    assert all(" " not in w for w in words) and ds.drafts[0].case["score"][0]["min"] == 2


def test_untracked_and_modified_files_are_never_used_as_material(tmp_path, policy, monkeypatch):
    """公開されていない内容(未コミットのファイル・変更中のファイル)は、材料にしない。"""
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Doc"])
    sec = "## 使い方\n\n" + "`llh eval` で `--repeat` と `--baseline` を使う。`policy.toml` と `config.toml` も。" * 8
    repo = make_repo(tmp_path, "Doc", [("docs: 使い方", {"README.md": "# T\n\n" + sec})])
    (repo / "PRIVATE_DESIGN.md").write_text("# P\n\n" + sec.replace("llh", "secretcmd"), encoding="utf-8")   # 未コミット
    assert [d.case["id"] for d in draft.summary_drafts(tmp_path, policy).drafts] != []
    assert all("secretcmd" not in json.dumps(d.case) for d in draft.summary_drafts(tmp_path, policy).drafts)
    (repo / "README.md").write_text("# T\n\n" + sec + "\n変更中\n", encoding="utf-8")   # 作業ツリーで変更中
    assert draft.summary_drafts(tmp_path, policy).drafts == [], "変更中のファイルは、使わない"
    monkeypatch.setattr(draft, "QA_SPECS", [("Doc", "PRIVATE_DESIGN.md", "llh", "未コミットの質問", ["llh"])])
    qa = draft.qa_drafts(tmp_path, policy)
    assert qa.drafts == [] and "コミットされていない" in qa.skipped[0].reason


def test_mermaid_drafts_need_enough_nodes_and_edges(tmp_path, policy, monkeypatch):
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Doc"])
    good = "```mermaid\nflowchart LR\n  A[入力] --> B[検証]\n  B --> C[保存]\n  C --> D[報告]\n  B --> E[エラー]\n```\n"
    small = "```mermaid\nflowchart LR\n  A[a] --> B[b]\n```\n"
    make_repo(tmp_path, "Doc", [("docs: 図", {"README.md": small + "\n" + good})])
    ds = draft.mermaid_drafts(tmp_path, policy)
    assert len(ds.drafts) == 1
    c = ds.drafts[0].case
    assert c["score"][0] == {"type": "mermaid"} and c["score"][1]["words"] == ["入力", "検証", "保存", "報告", "エラー"]
    assert c["score"][1]["min"] == 4 and "入力 → 検証" in c["prompt"]


# ── 手書き・まとめ ─────────────────────────────────────────
def test_tool_and_git_drafts_are_valid_cases():
    ds = draft.tool_drafts()
    ds.drafts += draft.git_drafts().drafts
    for d in ds.drafts:
        evals.parse_case(d.case)
    tools = [d for d in ds.drafts if d.kind == "tools"]
    assert all(d.case["tools"] == draft.SAFE_TOOLS for d in tools)
    push = next(d for d in tools if d.case["id"] == "d-tool-commit-push")
    assert push.case["score"][0]["not_names"] == ["git_push"], "『コミット・プッシュして』で、いきなり push しない"


def test_build_all_writes_loadable_files_and_a_review(tmp_path, real):
    out = tmp_path / "drafts"
    ds, _ = real
    counts = draft.write_drafts(ds, out)
    cases = evals.load_cases(out)
    assert len(cases) == sum(counts.values()) >= 40
    assert {"コミット文案", "変更の分類", "文書の要約", "資料への質問", "図の作成", "ツール選択", "Git の知識"} <= {c.category for c in cases}
    review = (out / "REVIEW.md").read_text(encoding="utf-8")
    assert "採点は近似" in review and "非公開のリポジトリ" in review and cases[0].id in review
    for kind in draft.KIND_TITLE:
        assert (out / f"{kind}.jsonl").exists()


def test_drafts_never_contain_sensitive_text(policy, real):
    ds, _ = real
    for d in ds.drafts:
        text = json.dumps(d.case, ensure_ascii=False)
        assert not policy.classify(text).sensitive, (d.case["id"], policy.classify(text).reasons)


def test_whole_case_is_rechecked_so_a_leak_in_the_prompt_is_dropped(tmp_path, policy, monkeypatch):
    """材料(メッセージ・差分)が無害でも、問題文に付ける参考(最近のコミット件名など)にメールが混ざれば、落とす。"""
    monkeypatch.setattr(draft, "PUBLIC_REPOS", ["Delta"])
    make_repo(tmp_path, "Delta", [
        ("docs: 連絡は someone@example.com まで", {"a.py": BODY}),
        ("feat: 窓口に enable_thinking の指定を足し、README.md に説明を書く",
         {"a.py": BODY + "\nenable_thinking = False\n" * 8, "README.md": "# t\nenable_thinking の説明\n" * 6}),
    ])
    ds = draft.build_all(tmp_path, policy)
    assert not [d for d in ds.drafts if d.kind == "commit"]
    assert any("問題文全体" in s.reason and "content:email" in s.reason for s in ds.skipped)


def test_build_all_is_deterministic_and_fast_enough(policy, real):
    ds, seconds = real
    again = draft.build_all(ROOT.parent, policy)
    assert [d.case["id"] for d in ds.drafts] == [d.case["id"] for d in again.drafts]
    assert seconds < 60, f"生成に {seconds:.0f} 秒(git を、コミットごとに呼んでいないか)"


def test_cli_draft_cases(tmp_path, capsys):
    out = tmp_path / "d"
    assert main(["draft-cases", "--root", str(ROOT.parent), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "案を作りました" in text and "REVIEW.md" in text and (out / "REVIEW.md").exists()
