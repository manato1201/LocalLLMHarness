# local-llm-mcp(Claude Code 用の MCP サーバー)

Claude Code に、ローカルLLM(このPCの Unsloth)の「安くて速い下働き」を足します(設計書 Phase 5・I9)。標準入出力の MCP サーバーで、標準ライブラリだけで動きます。

## 登録

```powershell
claude mcp add local-llm -- uv --directory C:\Users\matuu\Desktop\GameDevelopment\LocalLLMHarness run llh mcp
```

- **Unsloth の API キー**(`LLH_BACKEND_API_KEY`)は、Claude Code を起動する環境に必要です(`setx` で保存し、新しい PowerShell から Claude Code を起動)。
- Unsloth Desktop は、別に起動しておきます。モデルは、`config.toml` の `on_model_mismatch = "load"` なら、必要に応じて自動で読み込まれます。
- 動作確認: Claude Code で `/mcp` を開き、`local-llm` が接続されていること、5 つのツールが見えることを確かめます。
- **Claude Code からの実接続は、未確認です**(プロトコルの形は、`tests/test_mcp.py` の、本物のサブプロセスを使うテストで確認しています)。

## ツール

| ツール | 内容 | 使うモデル | 検証 |
|---|---|---|---|
| `local_summarize(text \| path, sentences?)` | 文書の要約。長ければ分割して要約(A3) | writer | — |
| `local_classify(items, labels, instruction?)` | 各 item を、labels のどれか 1 つに分類 | writer | ラベルが候補内・件数が同じ。違えば作り直す(最大 2 回。A1) |
| `local_extract(text, fields, instruction?)` | `fields`(`{項目: 型}`)の情報を JSON で抽出 | main | 型・余計なキー。違えば作り直す(最大 2 回。A1) |
| `local_ask_docs(question)` | 資料への質問(RAG ブリッジへ中継) | ブリッジの設定による | ブリッジの権限(namespace) |
| `local_embed(texts, include_vectors?)` | 埋め込み | `[models.embed]` | — |

結果は JSON で、`{"ok": true, "result": {...}, "meta": {"model", "seconds", "attempts", "sensitivity", "escalate", ...}}`。
失敗は `isError: true` で、`{"ok": false, "error": "..."}`。**`meta.escalate` が `true` のときは、ローカルでは解けなかった**(検証が規定の回数でも通らなかった)ので、Claude 側で処理します。

## 守り

- **結果は Claude(クラウド)に渡ります。**そのため、「外に出さない」区分の入力(秘密・禁止パス・許可リストの外のパス)は、**モデルに渡さずに断ります**(理由の名前だけを返し、一致した文字列は返しません)。許可するパスは `policy.toml` の `allow` です。
- 入力の上限: テキスト 6 万文字・items 200 件(1 件 2000 文字)・labels 30 個・fields 30 項目・質問 2000 文字・埋め込み 64 件。
- 実行時間の上限: 既定 180 秒(`timeout_s` で最大 600 秒)。LLM の呼び出しの合間に確かめるので、**1 回の呼び出しの途中では止まりません**(裏側のタイムアウトは `config.toml` の `backend.timeout_s`)。
- 窓口(`llh serve`)と、この MCP サーバーは、**別のプロセス**です。同時に重い処理をすると、Unsloth の側で待たされます。モデルの入れ替え中に他方が生成していると、`model_busy`(409)で断られます。
- `local_ask_docs`: 環境変数 `LLH_RAG_API_KEY`(ブリッジの X-API-Key)と、`LLH_RAG_URL`(既定 `http://127.0.0.1:8766`。**loopback の http だけ**)が要ります。**本物のブリッジとの接続は未確認**です(偽のサーバーでテスト)。
- `local_embed`: `config.toml` に `[models.embed]`(`name` = 埋め込みモデルの id)が要ります。**Unsloth の `/v1/embeddings` との実接続は未確認**です。

## 向いている仕事・向いていない仕事

- 向く: 大量の分類・抽出・要約(Claude の費用と時間を使わずに済む)、外に出せない前提の下処理。
- 向かない: 初見の難しい推論、複数ファイルにまたがる設計、手順が決まらない作業。ツールの説明(`INSTRUCTIONS`)にも、そう書いてあります。
