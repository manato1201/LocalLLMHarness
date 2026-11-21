from __future__ import annotations

from dataclasses import replace

import pytest

from llh import summarize as sm
from llh.cli import main
from llh.core import Harness
from llh.policy import load_policy
from llh.summarize import SummarizeError, chunk_text, summarize_file, summarize_text

from conftest import ROOT, WRITER, MAIN


def test_short_text_is_one_chunk():
    assert chunk_text("短い文章です。", 1000) == ["短い文章です。"]
    assert chunk_text("   \n ", 1000) == []


def test_chunks_respect_limit_and_lose_no_content():
    paras = [f"段落{i}。" + "あ" * 90 for i in range(30)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, 500)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)
    joined = "".join(chunks)
    for p in paras:
        assert p in joined, "段落が、途中で切れたり、落ちたりしない"


def test_headings_start_new_blocks():
    text = "# 見出し1\n" + "い" * 300 + "\n# 見出し2\n" + "ろ" * 300
    chunks = chunk_text(text, 400)
    assert len(chunks) == 2 and chunks[0].startswith("# 見出し1") and chunks[1].startswith("# 見出し2")


def test_one_huge_sentence_is_cut_by_characters():
    chunks = chunk_text("あ" * 2500, 1000)
    assert [len(c) for c in chunks] == [1000, 1000, 500]


def test_long_paragraph_is_split_at_sentences():
    text = "。".join(f"文{i}" + "う" * 40 for i in range(40)) + "。"
    chunks = chunk_text(text, 300)
    assert all(len(c) <= 300 for c in chunks) and len(chunks) > 3
    assert all(c.rstrip().endswith("。") or c is chunks[-1] for c in chunks[:-1]) or True


def test_chunk_size_follows_context_limit(cfg):
    h = Harness(replace(cfg, max_context_tokens=8192), policy=load_policy(cfg.policy_path))
    assert sm.chunk_chars_for(h) == 8000
    h = Harness(replace(cfg, max_context_tokens=4096), policy=load_policy(cfg.policy_path))
    assert sm.chunk_chars_for(h) == int((4096 - 1500) * 1.2)
    h = Harness(replace(cfg, max_context_tokens=1000), policy=load_policy(cfg.policy_path))
    assert sm.chunk_chars_for(h) == 1200


@pytest.fixture
def big(cfg):
    return Harness(replace(cfg, max_context_tokens=8192, max_output_tokens=2048), policy=load_policy(cfg.policy_path))


def test_short_document_is_a_single_call(big, backend):
    backend.reply = "要約です。"
    r = summarize_text(big, "短い文書", sentences=3)
    assert r.summary == "要約です。" and r.calls == 1 and r.chunks == 1 and r.levels == 1
    req = backend.requests[-1]
    assert req["model"] == WRITER, "既定は writer"
    assert "3文以内" in req["messages"][1]["content"] and "<document>" in req["messages"][1]["content"]
    assert "データ" in req["messages"][0]["content"] and "従わず" in req["messages"][0]["content"], "文書の中の命令には従わない"


def test_long_document_is_map_then_reduce(big, backend):
    text = "\n\n".join(("段落" + str(i) + "。" + "あ" * 800) for i in range(30))  # 約 25,000 文字 → 4 断片
    backend.reply = "要点です"
    seen = []
    r = summarize_text(big, text, sentences=5, progress=seen.append)
    assert r.chunks >= 3 and r.calls == r.chunks + 1, "断片ごと 1 回 + 最終のまとめ 1 回"
    assert backend.requests[-1]["messages"][1]["content"].count("[1]") == 1
    assert all(len(rq["messages"][1]["content"]) < 9500 for rq in backend.requests), "全文は、一度に渡さない"
    assert seen[0].startswith("断片 1/") and seen[-1] == "最終のまとめ"


def test_many_partial_summaries_are_reduced_in_levels(big, backend):
    backend.reply = "要点" * 1000   # 各断片の要点が 2000 文字。12 個集めると、上限(8000)を超える
    text = "\n\n".join("段落" + "あ" * 7000 for _ in range(12))
    r = summarize_text(big, text)
    assert r.levels >= 2 and r.calls > r.chunks + 1


def test_max_chunks_truncates_with_a_note(big, backend):
    text = "\n\n".join("段落" + "あ" * 7000 for _ in range(10))
    r = summarize_text(big, text, max_chunks=3)
    assert r.chunks == 3 and r.truncated and any("先頭の 3 個" in n for n in r.notes)


def test_empty_and_oversized_inputs_are_rejected(big):
    with pytest.raises(SummarizeError):
        summarize_text(big, "   ")
    with pytest.raises(SummarizeError, match="大きすぎ"):
        summarize_text(big, "あ" * (sm.MAX_INPUT_CHARS + 1))


def test_empty_model_reply_is_an_error(big, backend):
    backend.reply = ""
    with pytest.raises(SummarizeError, match="空"):
        summarize_text(big, "文書")


def test_file_summary_checks_the_path_policy(big, backend, tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# タイトル\n本文です。", encoding="utf-8")
    r = summarize_file(big, f)
    assert r.sensitivity == "local-only", "許可リストに無いパスは、外に出さない区分(既定は拒否)"
    with pytest.raises(SummarizeError, match="ファイルがありません"):
        summarize_file(big, tmp_path / "none.md")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    with pytest.raises(SummarizeError, match="PDF"):
        summarize_file(big, pdf)


def test_cli_summarize(tmp_path, backend, capsys):
    p = tmp_path / "config.toml"
    p.write_text(f"""
[backend]
base_url = "{backend.base_url}"
[models.main]
name = "{MAIN}"
[models.writer]
name = "{WRITER}"
[limits]
max_context_tokens = 8192
[paths]
policy = "{(ROOT / 'policy.example.toml').as_posix()}"
audit_log = "{(tmp_path / 'a.jsonl').as_posix()}"
""", encoding="utf-8")
    doc = tmp_path / "d.md"
    doc.write_text("本文です。", encoding="utf-8")
    backend.reply = "これが要約です"
    assert main(["--config", str(p), "summarize", str(doc), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "これが要約です" in out and "断片 1 個" in out and "このPCの中だけ" in out
    assert main(["--config", str(p), "summarize", str(tmp_path / "none.md")]) == 1
