from __future__ import annotations

import json

import pytest

from llh import sft
from llh.cli import main
from llh.policy import load_policy

from conftest import ROOT, MAIN


@pytest.fixture(scope="module")
def policy():
    return load_policy(ROOT / "policy.example.toml")


def row(q, a, label="code-1"):
    return {"label": label, "messages": [{"role": "user", "content": q}], "reply": a}


def write(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def read(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_exports_chat_format(tmp_path, policy):
    src, out = tmp_path / "v.jsonl", tmp_path / "sft.jsonl"
    write(src, [row("質問 1", "答え 1"), row("質問 2", "答え 2", "cls-2:x")])
    rep = sft.export_sft(policy, out, [src], None)
    assert (rep.read, rep.written) == (2, 2) and rep.by_label == {"code-1": 1, "cls-2": 1}
    assert read(out)[0] == {"messages": [{"role": "user", "content": "質問 1"}, {"role": "assistant", "content": "答え 1"}]}


def test_sensitive_examples_are_excluded_from_training_data(tmp_path, policy):
    src, out = tmp_path / "v.jsonl", tmp_path / "o.jsonl"
    write(src, [row("普通の質問", "普通の答え"), row("キーは sk-test-abcdefghijklmnopqrstuvwx", "了解"),
                row("質問", "連絡先は someone@example.com です")])
    rep = sft.export_sft(policy, out, [src], None)
    assert rep.written == 1 and rep.skipped_sensitive == 2
    assert "abcdefghijkl" not in out.read_text(encoding="utf-8")


def test_eval_questions_never_enter_training_data(tmp_path, policy):
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "c.jsonl").write_text(json.dumps({"id": "e1", "category": "X", "prompt": "評価の質問です",
                                               "score": {"type": "exact", "expected": "a"}}, ensure_ascii=False) + "\n", encoding="utf-8")
    src, out = tmp_path / "v.jsonl", tmp_path / "o.jsonl"
    write(src, [row("評価の質問です", "答え"), row("  評価の質問です \n", "答え"), row("別の質問", "答え")])
    rep = sft.export_sft(policy, out, [src], cases)
    assert rep.written == 1 and rep.skipped_eval_leak == 2, "空白だけが違う同じ質問も、除外する"
    assert [r["messages"][0]["content"] for r in read(out)] == ["別の質問"]


def test_starter_eval_prompts_are_guarded(tmp_path, policy):
    starter = json.loads([x for x in (ROOT / "evals" / "cases" / "starter.jsonl").read_text(encoding="utf-8").splitlines() if x.startswith("{")][3])
    src, out = tmp_path / "v.jsonl", tmp_path / "o.jsonl"
    write(src, [row(starter["prompt"], "def reverse_str(s): return s[::-1]")])
    rep = sft.export_sft(policy, out, [src], ROOT / "evals" / "cases")
    assert rep.written == 0 and rep.skipped_eval_leak == 1


def test_duplicates_and_invalid_rows(tmp_path, policy):
    src, out = tmp_path / "v.jsonl", tmp_path / "o.jsonl"
    img = {"label": "v", "messages": [{"role": "user", "content": [{"type": "text", "text": "画像"}]}], "reply": "説明"}
    write(src, [row("同じ", "同じ答え"), row("同じ", "同じ答え"), {"messages": []}, {"label": "x"}, row("空", ""), img])
    src.write_text(src.read_text(encoding="utf-8") + "壊れた行\n", encoding="utf-8")
    rep = sft.export_sft(policy, out, [src], None)
    assert rep.written == 1 and rep.skipped_duplicate == 1 and rep.skipped_invalid == 5


def test_chat_format_input_is_accepted_for_extra_sources(tmp_path, policy):
    extra, out = tmp_path / "claude.jsonl", tmp_path / "o.jsonl"
    write(extra, [{"messages": [{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}]}])
    assert sft.export_sft(policy, out, [extra], None).written == 1
    assert read(out)[0]["messages"][-1] == {"role": "assistant", "content": "A"}


def test_readiness_threshold():
    r = sft.ExportReport(written=sft.MIN_EXAMPLES_TO_TRAIN - 1)
    assert not r.ready
    r.written = sft.MIN_EXAMPLES_TO_TRAIN
    assert r.ready


def test_cli_export_sft(tmp_path, backend, capsys):
    cfgp = tmp_path / "config.toml"
    cfgp.write_text(f'''[backend]
base_url = "{backend.base_url}"
[models.main]
name = "{MAIN}"
[training]
save_verified = true
verified_log = "{(tmp_path / 'verified.jsonl').as_posix()}"
[paths]
policy = "{(ROOT / 'policy.example.toml').as_posix()}"
audit_log = "{(tmp_path / 'a.jsonl').as_posix()}"
''', encoding="utf-8")
    write(tmp_path / "verified.jsonl", [row("普通の質問", "普通の答え")])
    out = tmp_path / "sft.jsonl"
    assert main(["--config", str(cfgp), "export-sft", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "書き出し 1 件" in text and "足りません" in text and "docs/PHASE6.md" in text
    assert len(read(out)) == 1
    assert main(["--config", str(cfgp), "export-sft", "--no-verified", "--out", str(out)]) == 1
