from __future__ import annotations

import json
from dataclasses import replace

import pytest

from llh import evals
from llh.cli import main
from llh.core import Harness
from llh.policy import load_policy

from conftest import MAIN, ROOT, WRITER


def mk(cid="c1", category="X", score=None, prompt="q", **kw):
    return evals.parse_case({"id": cid, "category": category, "prompt": prompt, "score": score or [{"type": "contains", "all": ["a"]}], **kw})


def ctx(allow_exec=False):
    return evals.ScoreContext(allow_exec=allow_exec)


def sc(spec, reply="", message=None, allow_exec=False):
    case = mk(score=[spec])
    return evals.score_case(case, message if message is not None else {"content": reply}, ctx(allow_exec))


# ── 問題の読み込み ────────────────────────────────────────
def test_starter_cases_load_and_have_unique_ids():
    cases = evals.load_cases(ROOT / "evals" / "cases")
    assert len(cases) == 24
    assert len({c.id for c in cases}) == 24
    assert {c.category for c in cases} >= {"資料への質問", "コード生成", "要約・分類", "ツール呼び出し", "構造の把握", "機密度の判定"}


def test_starter_policy_cases_all_pass_with_the_example_policy(harness):
    """設計書 Phase 2 の Go: 疑似の秘密がすべて止まり、通常の入力が誤って止まらない。"""
    cases = [c for c in evals.load_cases(ROOT / "evals" / "cases") if c.is_policy]
    attempts = evals.run_eval(harness, cases, repeat=3)
    assert len(attempts) == len(cases) == 10, "policy の問題は、LLM を使わず、1 回だけ"
    assert [a.detail for a in attempts if not a.passed] == []


@pytest.mark.parametrize("raw,needle", [
    ({"category": "X", "prompt": "q", "score": {"type": "exact", "expected": "a"}}, "'id'"),
    ({"id": "a", "category": "X", "prompt": "q", "score": {"type": "nope"}}, "type"),
    ({"id": "a", "category": "X", "score": {"type": "exact", "expected": "a"}}, "prompt"),
    ({"id": "a", "category": "X", "prompt": "q", "image": "no.png", "score": {"type": "exact", "expected": "a"}}, "image"),
])
def test_bad_cases_are_rejected(raw, needle):
    with pytest.raises(evals.EvalError) as e:
        evals.parse_case(raw, "f.jsonl:1")
    assert needle in str(e.value)


def test_duplicate_ids_and_bad_json_are_rejected(tmp_path):
    f = tmp_path / "a.jsonl"
    good = json.dumps({"id": "a", "category": "X", "prompt": "q", "score": {"type": "exact", "expected": "a"}})
    f.write_text(good + "\n" + good + "\n", encoding="utf-8")
    with pytest.raises(evals.EvalError, match="重複"):
        evals.load_cases(f)
    f.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(evals.EvalError, match="JSON"):
        evals.load_cases(f)
    with pytest.raises(evals.EvalError):
        evals.load_cases(tmp_path / "none.jsonl")


def test_comments_and_blank_lines_are_ignored(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text("# コメント\n\n" + json.dumps({"id": "a", "category": "X", "prompt": "q", "score": {"type": "exact", "expected": "a"}}) + "\n", encoding="utf-8")
    assert [c.id for c in evals.load_cases(f)] == ["a"]


def test_messages_form_and_image_are_supported(tmp_path):
    c = evals.parse_case({"id": "m", "category": "X", "score": {"type": "exact", "expected": "a"},
                          "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]})
    assert [m["role"] for m in c.messages] == ["system", "user"]
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n0000")
    c = evals.parse_case({"id": "i", "category": "X", "prompt": "これは?", "image": str(img), "score": {"type": "exact", "expected": "a"}})
    assert c.messages[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


# ── 採点 ───────────────────────────────────────────────
def test_exact_normalizes_space_case_and_period():
    assert sc({"type": "exact", "expected": "Yes"}, "  yes。 ")[0]
    assert not sc({"type": "exact", "expected": "Yes"}, "yes, indeed")[0]


def test_contains_all_and_none():
    assert sc({"type": "contains", "all": ["a", "B"], "none": ["x"]}, "Ab")[0]
    ok, d = sc({"type": "contains", "all": ["a", "z"], "none": ["b"]}, "ab")
    assert not ok and "z" in d and "b" in d


def test_any_and_regex():
    assert sc({"type": "any", "any": ["foo", "bar"]}, "xBARx")[0]
    assert not sc({"type": "any", "any": ["foo"]}, "x")[0]
    assert sc({"type": "regex", "pattern": r"\d{3}-\d{4}"}, "tel 123-4567")[0]
    assert not sc({"type": "regex", "pattern": r"^\d+$"}, "abc")[0]


def test_label_uses_first_occurrence():
    spec = {"type": "label", "labels": ["error", "warning", "info"], "expected": "error"}
    assert sc(spec, "error です。warning ではない")[0]
    assert not sc(spec, "これは warning です(error ではない)")[0]
    assert not sc(spec, "分かりません")[0]


def test_json_required_types_equals_and_fences():
    spec = {"type": "json", "required": ["a", "b"], "types": {"a": "string", "b": "integer"}, "equals": {"a": "x"}}
    assert sc(spec, '```json\n{"a": "x", "b": 2}\n```')[0]
    assert sc(spec, '結果: {"a": "x", "b": 2} です')[0]
    ok, d = sc(spec, '{"a": "y", "b": true}')
    assert not ok and "b" in d and "a" in d
    assert not sc(spec, "JSON ではない")[0]


def test_mermaid_scoring():
    good = "```mermaid\nflowchart LR\n  A[窓口] --> B[Unsloth]\n```"
    assert sc({"type": "mermaid", "all": ["窓口", "Unsloth"]}, good)[0]
    assert not sc({"type": "mermaid", "all": ["存在しない"]}, good)[0]
    assert not sc({"type": "mermaid"}, "```mermaid\nflowchart LR\n  A[x --> B\n```")[0]
    assert not sc({"type": "mermaid"}, "図なし")[0]


def test_citation_scoring():
    spec = {"type": "citation", "cited": [1, 2], "sources": 2}
    assert sc(spec, "答え [1][2]")[0]
    ok, d = sc(spec, "答え [1]")
    assert not ok and "2" in d
    assert not sc(spec, "答え [1][2][9]")[0], "存在しない番号"
    assert not sc({"type": "citation", "sources": 2}, "引用なし")[0]


def test_tool_call_scoring():
    call = lambda name, args: {"content": None, "tool_calls": [{"function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
    spec = {"type": "tool_call", "name": "get_weather", "args": {"city": "東京"}}
    assert sc(spec, message=call("get_weather", {"city": "東京", "unit": "c"}))[0]
    assert not sc(spec, message=call("read_file", {"path": "x"}))[0]
    ok, d = sc(spec, message=call("get_weather", {"city": "大阪"}))
    assert not ok and "大阪" in d
    assert not sc(spec, "ただの文章")[0]


def test_python_skipped_without_allow_exec_and_runs_with_it():
    spec = {"type": "python", "tests": "assert f(2) == 4"}
    with pytest.raises(evals._Skip):
        sc(spec, "```python\ndef f(x): return x*2\n```")
    assert sc(spec, "```python\ndef f(x): return x*2\n```", allow_exec=True)[0]
    ok, d = sc(spec, "```python\ndef f(x): return x+1\n```", allow_exec=True)
    assert not ok and "AssertionError" in d
    assert not sc(spec, "コードなし", allow_exec=True)[0]
    ok, d = sc({"type": "python", "tests": "", "timeout_s": 1}, "```python\nwhile True: pass\n```", allow_exec=True)
    assert not ok and "秒を超えた" in d


def test_all_specs_in_a_list_must_pass():
    case = mk(score=[{"type": "contains", "all": ["a"]}, {"type": "contains", "all": ["z"]}])
    assert not evals.score_case(case, {"content": "a"}, ctx())[0]
    assert evals.score_case(case, {"content": "az"}, ctx())[0]


# ── 実行 ───────────────────────────────────────────────
def test_runner_repeats_and_records(harness, backend):
    backend.replies = ["a", "b", "a"]
    attempts = evals.run_eval(harness, [mk()], repeat=3)
    assert [a.passed for a in attempts] == [True, False, True]
    assert all(a.prompt_tokens == 10 and a.completion_tokens == 5 and a.seconds for a in attempts)
    stat = evals.case_stats(attempts)[0]
    assert (stat.passes, stat.attempts) == (2, 3)
    assert backend.requests[0]["temperature"] == 0


def test_temperature_and_per_case_override(harness, backend):
    evals.run_eval(harness, [mk("a"), mk("b", temperature=0.7)], repeat=1, temperature=0.2)
    assert [r["temperature"] for r in backend.requests] == [0.2, 0.7]


def test_roles_are_grouped_to_minimize_model_swaps(cfg, backend):
    h = Harness(replace(cfg, on_model_mismatch="load"), policy=load_policy(cfg.policy_path))
    backend.loaded[:] = [WRITER]
    cases = [mk("m1", role="main"), mk("w1", role="writer"), mk("m2", role="main"), mk("w2", role="writer")]
    evals.run_eval(h, cases, repeat=1)
    assert len(backend.load_calls) == 2, "ロールごとに、読み込みの確認は 1 回(writer が載っていたので、main を読み込み、writer を読み込み直す)"
    models = [r["model"] for r in backend.requests]
    assert models.index(MAIN) < len(models) and models.count(MAIN) == 2 and models.count(WRITER) == 2
    # 同じモデルの問題が、続けて解かれている
    assert models in ([MAIN, MAIN, WRITER, WRITER], [WRITER, WRITER, MAIN, MAIN])


def test_python_cases_are_skipped_and_excluded_from_rates(harness):
    case = mk("p", score=[{"type": "python", "tests": "assert True"}])
    attempts = evals.run_eval(harness, [case, mk("ok")], repeat=3, allow_exec=False)
    skipped = [a for a in attempts if a.case_id == "p"]
    assert len(skipped) == 1 and skipped[0].passed is None and skipped[0].skipped
    assert [s.case_id for s in evals.case_stats(attempts)] == ["ok"]


def test_backend_error_is_an_error_attempt_not_a_crash(cfg):
    h = Harness(replace(cfg, backend_base_url="http://127.0.0.1:9/v1"), policy=load_policy(cfg.policy_path))
    attempts = evals.run_eval(h, [mk()], repeat=1)
    assert attempts[0].passed is False and attempts[0].error


def test_empty_reply_is_flagged(harness, backend):
    backend.reply = ""
    a = evals.run_eval(harness, [mk()], repeat=1)[0]
    assert a.passed is False and "本文が空" in a.detail


def test_tools_and_task_are_sent(harness, backend):
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    evals.run_eval(harness, [mk(tools=tools, task="code")], repeat=1)
    assert backend.requests[-1]["tools"] == tools and "llh" not in backend.requests[-1]


def test_vision_role_cases_use_the_vision_model(harness, backend):
    evals.run_eval(harness, [mk(role="vision")], repeat=1)
    assert backend.requests[-1]["model"] == "vl-model"


# ── 集計・比較 ───────────────────────────────────────────
def stats(rows):
    return [evals.CaseStat(cid, cat, p, 3) for cid, cat, p in rows]


def test_category_rates_average_per_case():
    rates = evals.category_rates(stats([("a", "X", 3), ("b", "X", 0), ("c", "Y", 3)]))
    assert rates["X"] == (0.5, 2) and rates["Y"] == (1.0, 1)


def test_compare_verdicts():
    s = stats([("a", "R", 3), ("b", "R", 3), ("c", "W", 2), ("d", "W", 2), ("e", "L", 0), ("f", "L", 0), ("g", "Z", 1)])
    base = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0, "e": 1.0, "f": 1.0, "g": 0.0, "nobase": 1.0}
    got = {c.category: c for c in evals.compare(s, base)}
    assert "ローカルに回せる" in got["R"].verdict
    assert got["W"].local == pytest.approx(2 / 3) and got["W"].verdict == "Claude に残す", "差が 33 ポイントなので、誤差とはみなさない"
    assert got["L"].verdict == "Claude に残す"
    assert "基準も解けていない" in got["Z"].verdict
    assert set(got) == {"R", "W", "L", "Z"}, "基準に無い問題(nobase)は、比べない"


def test_compare_threshold_and_noise():
    # 85% 以上 → 回せる / 差が小さく問題が少ない → 同等 / それ以外 → 残す
    base = {f"q{i}": 1.0 for i in range(10)}
    s = stats([(f"q{i}", "A", 3 if i < 9 else 0) for i in range(10)])  # 90%
    assert "ローカルに回せる" in evals.compare(s, base)[0].verdict
    s = stats([(f"q{i}", "A", 3 if i < 6 else 0) for i in range(10)])  # 60%
    assert evals.compare(s, base)[0].verdict == "Claude に残す"
    base2 = {f"q{i}": 1.0 for i in range(10)}
    s = stats([(f"q{i}", "A", 3 if i < 8 else 0) for i in range(10)])  # 80%: 85% 未満だが、差は 20 ポイント
    assert evals.compare(s, base2)[0].verdict == "Claude に残す"
    s = stats([("q0", "A", 3), ("q1", "A", 2), ("q2", "A", 3), ("q3", "A", 3), ("q4", "A", 3), ("q5", "A", 3), ("q6", "A", 3), ("q7", "A", 3)])
    base3 = {f"q{i}": 1.0 for i in range(8)}  # 約 96%
    assert "ローカルに回せる" in evals.compare(s, base3)[0].verdict


def test_compare_zero_baseline():
    s = stats([("a", "A", 3)])
    assert "基準も解けていない" in evals.compare(s, {"a": 0.0})[0].verdict


def test_baseline_formats(tmp_path, harness, backend):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"a": 1.0, "b": 0.5}), encoding="utf-8")
    assert evals.load_baseline(f) == {"a": 1.0, "b": 0.5}
    f.write_text(json.dumps({"a": 2}), encoding="utf-8")
    with pytest.raises(evals.EvalError):
        evals.load_baseline(f)
    with pytest.raises(evals.EvalError):
        evals.load_baseline(tmp_path / "none.json")
    # 結果ファイル(.jsonl)を、そのまま基準にできる
    backend.replies = ["a", "a", "b"]
    attempts = evals.run_eval(harness, [mk()], repeat=3)
    out = tmp_path / "r.jsonl"
    evals.write_results(out, harness, attempts, {"repeat": 3})
    assert evals.load_baseline(out) == {"c1": pytest.approx(2 / 3)}


def test_results_file_has_meta_and_attempts(tmp_path, harness, backend):
    attempts = evals.run_eval(harness, [mk()], repeat=2)
    out = tmp_path / "sub" / "r.jsonl"
    evals.write_results(out, harness, attempts, {"repeat": 2, "temperature": 0})
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["kind"] == "meta" and lines[0]["models"]["main"] == MAIN and lines[0]["repeat"] == 2
    assert [x["kind"] for x in lines[1:]] == ["attempt", "attempt"]


def test_fingerprint_changes_when_a_case_changes():
    a, b = mk(prompt="q1"), mk(prompt="q2")
    assert evals.cases_fingerprint([a]) != evals.cases_fingerprint([b])
    assert evals.cases_fingerprint([a]) == evals.cases_fingerprint([mk(prompt="q1")])


def test_speed_summary(harness):
    attempts = evals.run_eval(harness, [mk()], repeat=3)
    sp = evals.speed_summary(attempts)
    assert sp["attempts"] == 3 and sp["mean_tok_s"] > 0 and sp["completion_tokens"] == 15


# ── CLI ────────────────────────────────────────────────
@pytest.fixture
def eval_cfg(tmp_path, backend):
    p = tmp_path / "config.toml"
    p.write_text(f"""
[backend]
base_url = "{backend.base_url}"
[models.main]
name = "{MAIN}"
[models.writer]
name = "{WRITER}"
[paths]
policy = "{(ROOT / 'policy.example.toml').as_posix()}"
audit_log = "{(tmp_path / 'a.jsonl').as_posix()}"
""", encoding="utf-8")
    return str(p)


def write_cases(tmp_path, rows):
    f = tmp_path / "cases.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return str(f)


def test_eval_cli_end_to_end(eval_cfg, backend, tmp_path, capsys):
    backend.reply = "答えは a です"
    cases = write_cases(tmp_path, [
        {"id": "k1", "category": "区分A", "prompt": "q", "score": {"type": "contains", "all": ["a"]}},
        {"id": "k2", "category": "区分A", "prompt": "q", "score": {"type": "contains", "all": ["zzz"]}},
        {"id": "s1", "category": "機密度の判定", "score": {"type": "policy", "expect": "local-only", "text": "sk-test-abcdefghijklmnopqrstuvwx"}},
    ])
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"k1": 1.0, "k2": 1.0, "s1": 1.0}), encoding="utf-8")
    out = tmp_path / "res"
    rc = main(["--config", eval_cfg, "eval", "--cases", cases, "--repeat", "2", "--baseline", str(base), "--out", str(out)])
    text = capsys.readouterr().out
    assert rc == 0
    assert "区分A" in text and "50%" in text and "機密度の判定" in text and "100%" in text
    assert "k2" in text and "基準との比較" in text and "Claude に残す" in text
    files = list(out.glob("*.jsonl"))
    assert len(files) == 1 and files[0].name.endswith("-main.jsonl")


def test_eval_cli_filters_and_errors(eval_cfg, tmp_path, backend, capsys):
    cases = write_cases(tmp_path, [{"id": "k1", "category": "A", "prompt": "q", "score": {"type": "contains", "all": ["2"]}}])
    n = len(backend.requests)
    assert main(["--config", eval_cfg, "eval", "--cases", cases, "--category", "B", "--out", str(tmp_path / "o")]) == 1
    assert len(backend.requests) == n
    assert main(["--config", eval_cfg, "eval", "--cases", str(tmp_path / "none.jsonl")]) == 1
    assert main(["--config", eval_cfg, "eval", "--cases", cases, "--baseline", str(tmp_path / "none.json")]) == 1
    assert "見つかりません" in capsys.readouterr().out


def test_eval_cli_skipped_python_message(eval_cfg, tmp_path, capsys):
    cases = write_cases(tmp_path, [{"id": "p1", "category": "コード", "prompt": "q", "score": {"type": "python", "tests": "assert True"}}])
    main(["--config", eval_cfg, "eval", "--cases", cases, "--repeat", "1", "--out", str(tmp_path / "o")])
    assert "採点しなかった問題" in capsys.readouterr().out
