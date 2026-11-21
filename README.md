# LocalLLMHarness

ローカルLLM(Unsloth Desktop)の**前に置く窓口**です。既存ツールは `base_url` を 1 つ書き換えるだけでつながります。
設計は [LocalLLMHarness_DESIGN.md](LocalLLMHarness_DESIGN.md)(Phase 2「窓口と機密度の判定」+ ハイブリッド運用設計書 2026-11-21 の 3 モデル構成)。

**Python の標準ライブラリだけ**で動きます(要 Python 3.11 以上。LiteLLM などの既製品は使いません)。

## できること

| 機能 | 中身 | 設計 |
|---|---|---|
| OpenAI 互換の窓口 | `POST /v1/chat/completions`(stream 対応)・`GET /v1/models`・`GET /health` | §2 |
| **機密度の判定** | パスの規則(既定は拒否)・内容の検査(キー/秘密鍵/メール/個人のパス)・手動指定。**一致した文字列は、どこにも残さない** | 軸 1 |
| **モデルの振り分け** | `llh-main`(Qwen 3.5 9B)/ `llh-vision`(Qwen2.5-VL 7B)/ `llh-writer`(Gemma 4 9B)。画像があれば vision、`llh.task` で担当ロール、無ければ main。**設定に無いモデル名は断る** | A6・§3 |
| **直列化** | VRAM 8GB なので、1 度に 1 リクエストだけ裏側へ通す。待ち時間の上限(既定 120 秒)で 503 | A13 |
| 文脈の上限 | 入力+出力が 8192 トークン(設定可)を超えるリクエストは、裏側へ送らず 400 | §5 |
| サーバー側ツールの遮断 | Unsloth のサーバー側ツール(Python 実行・ターミナル等)の指定は全部外し、**`enable_tools=false` に固定**して中継(外すだけだと Unsloth の画面のトグルが効くため。Unsloth 0.1.922-beta の OpenAPI で確認) | S1 |
| **監査ログ** | `logs/audit.jsonl`。**内容でなくハッシュ・件数・モデル・秒数**だけ。各行が直前の行のハッシュを持ち、削除・改ざんに気づける(`llh audit-verify`) | S4 |
| 入口の守り | 127.0.0.1 だけで待ち受け(それ以外の `host` は起動しない)・Host の許可リスト・Origin の検査・任意のトークン | S13 |
| 行き先の表示 | `POST /v1/route`・`llh route`:送らずに、モデル・機密度・行き先だけを返す | Z4 |
| git diff の要約 | `llh diff-summary`:差分の要約とコミットメッセージ案。**コミット・push はしない**(読み取り専用の git だけ) | §4 |
| Mermaid の生成 | `llh mermaid`:生成 → 構文検査(`mmdc` があれば本物のパーサーも)→ 落ちたら問題を渡して作り直し(最大 3 回)。**通ったものだけ**出力・保存 | §4・A1 |
| 文書の要約 | `llh summarize`:文脈が 8K トークンなので、**全文を渡さず**、見出し・段落で分割 → 断片ごとの要点 → 段階的にまとめる(map-reduce)。文書の中の命令には従わない | §4・A3 |
| 検証つき生成・エスカレーション | `verified_generate`:生成 → 機械で検証 → 失敗の内容を渡して作り直し(最大 N 回)。通らなければ `escalate`(内容を残さず、ハッシュと検証の詳細だけを `logs/escalations.jsonl` に記録) | A1・A6 |
| Claude Code 用の MCP | `llh mcp`(local-llm-mcp):`local_summarize` / `local_classify` / `local_extract` / `local_ask_docs` / `local_embed`。**結果は Claude に渡るので、外に出さない区分の入力は断る**。[docs/MCP.md](docs/MCP.md) | Phase 5・I9 |
| 微調整の準備 | `llh export-sft`:検証に通った生成を、学習データにまとめる(外に出さない区分・評価セットと同じ質問は除く)。**学習はしない**。[docs/PHASE6.md](docs/PHASE6.md) | Phase 6 |

## クイックスタート

```powershell
uv sync
Copy-Item config.example.toml config.toml
Copy-Item policy.example.toml policy.toml     # 許可するパスを自分の環境に合わせて直す
$env:LLH_BACKEND_API_KEY = "sk-unsloth-…"      # Unsloth Desktop の Settings > API で作ったキー
uv run llh models                              # 裏側のモデル id を確認 → config.toml の [models.*].name を合わせる
uv run llh status                              # 裏側・読み込み済みモデル・VRAM・待ち受けの範囲
uv run llh load --role main                    # モデルを読み込ませる(下の「1 度に 1 つ」参照)
uv run llh smoke --role main                   # 単体動作と速さ(導入チェックリスト Step 2)
uv run llh serve                               # http://127.0.0.1:8800/v1
```

秘密(Unsloth の API キー・窓口のトークン)は**環境変数だけ**で渡します(`LLH_BACKEND_API_KEY`・`LLH_TOKEN`)。`config.toml`・`policy.toml`・`logs/` は `.gitignore` 済みです。

**1 度に 1 つのモデルしか載りません**(VRAM 8GB)。重要: Unsloth の `/v1/chat/completions` の `model` は**参考値で、実際に使われるのは読み込み済みのモデル**です(OpenAPI の記述: "informational; the active model is used")。そのため窓口は、振り分けたモデルが読み込まれていないとき、既定では応答ヘッダー `X-LLH-Warning` で知らせます(`backend.on_model_mismatch = "error"` で 409 にもできます)。今の窓口は、`vision` や `writer` を使うとき、Unsloth Desktop で手で読み込む前提です。

> 設計書 §1.1 の「モデルの切り替えは API からは行えない」は、**この版(0.1.922-beta)では当てはまりません**。`POST /v1/load`(`model_path`・`gguf_variant`・`max_seq_length`・`alongside` など)と `POST /v1/unload` があります。窓口がロールに応じて自動で読み込む機能は**実装済みですが、既定では無効**です(`backend.on_model_mismatch = "load"` で有効。**偽の Unsloth でしか試していません**)。使う前に、次の順で確かめてください:

```powershell
uv run llh models                  # 裏側の id を確認 → config.toml の [models.*].name を合わせる
uv run llh load --role main        # POST /v1/load が通り、本当に読み込まれるか(読み込み後に /v1/models で検証します)
uv run llh smoke --role main
```

`load` のときの動き: 振り分けたモデルが読み込み済みでなければ、**直列化のロックの中で**読み込み(他のリクエストは待つ。`queue_timeout_s` を超えると 503)、読み込み後に `/v1/models` で確かめます。確かめられなければ、質問は**送らずに**502 にします(違うモデルが答えるのを防ぐ)。入れ替えは監査ログの `model_load_s` に残り、応答に `X-LLH-Model-Switched: 1` が付きます。`alongside` は常に false(VRAM 8GB)で、他のクライアントが生成中なら止めずに 409 です。名前が `リポジトリ:変種`(例 `unsloth/…-GGUF:Q4_K_M`)なら、`model_path` と `gguf_variant` に分けて渡します。合わなければ `[models.*]` に `model_path` / `gguf_variant` を明示してください。

自動アンロード(TTL)は Unsloth Desktop 側で有効にしてください。

## 使い方

```powershell
uv run llh route "このログを分類して" --task log     # 送らずに行き先と機密度だけ見る
uv run llh policy-check C:\path\to\file               # 外に出せるか(0=出せる / 1=外に出さない)
uv run llh diff-summary --repo . [--staged]           # 差分の要約 + コミットメッセージ案(コミットはしない)
uv run llh mermaid "ユーザー→窓口→Unsloth の流れ" --out docs\flow.mmd
uv run llh audit-verify                               # 監査ログの連鎖を確かめる
uv run llh bench --role main --sizes 512,2048,4096,6000   # 入力の長さごとの速度(TTFT・入力処理・生成)
uv run llh eval                                       # 評価セット(evals/cases)を 3 回ずつ解いて、区分ごとの合格率(evals/README.md)
uv run llh eval --verify-retries 2                    # 検証つき生成(A1)の効果を測る(検証役は正解を知らない検証だけ)
uv run llh summarize C:\docs\long.md              # 長い文書を、分割して要約する
uv run llh mcp                                        # Claude Code 用の MCP サーバー(登録は docs/MCP.md)
uv run llh export-sft                                 # 検証に通った生成を、学習データにまとめる(Phase 6 の準備)
```

呼び出し側(Python などの OpenAI クライアント)は、`base_url="http://127.0.0.1:8800/v1"`、`model="llh-main"`(または `auto`)にします。追加の指定は、リクエストの本体の `llh` で:

```json
{ "model": "auto", "messages": [...],
  "llh": { "task": "doc", "paths": ["C:/…/file.md"], "local_only": true } }
```

`llh` は裏側へ渡しません。`X-LLH-Local-Only: 1` のヘッダーでも「外に出さない」にできます。応答ヘッダー `X-LLH-Route` / `X-LLH-Model` / `X-LLH-Destination` / `X-LLH-Sensitivity` で、実際の行き先が分かります。

## 他のツールとの接続

導入チェックリストの Step 4〜6(`rag_local_bridge.py`・`VLMAutoReplayTool`・`ToolOrchestrationHub`)の手順と、確認の範囲は [docs/INTEGRATION.md](docs/INTEGRATION.md)。`DevelopmentRAGEnvironment` と `ToolOrchestrationHub` には、2026-11-21 に変更を加えました(それぞれのリポジトリでコミットします)。

## 進み具合(2026-11-21 時点)

| 導入チェックリスト | 状態 |
|---|---|
| Step 1 Unsloth Desktop の導入・API の有効化 | **済**(0.1.922-beta。API キーを作成) |
| Step 2・3 モデルの導入と単体確認 | **済**。main(Qwen3.5-9B Q4_K_M)53 tok/s、writer(Gemma 4 E4B UD-Q4_K_XL)107 tok/s。vision は main と同じモデルで画像を読めた(Qwen2.5-VL は未導入)。Gemma 4 に 9B は無く、E4B を使用 |
| 窓口・機密度の判定・振り分け・監査ログ・CLI(この repo) | **実装済み**。テストは偽の Unsloth で通し(`uv run pytest -q`)、`llh load` / `smoke` / `serve` と窓口経由の質問は**本物の Unsloth で確認済み** |
| Step 4 `rag_local_bridge.py` を窓口へ | **適用済み**(`RAG_LLM_BACKEND=local`。偽の窓口でテスト 11 件。本物の窓口を通した `/query` は未確認。[INTEGRATION.md](docs/INTEGRATION.md) §1) |
| Step 5 `VLMAutoReplayTool` を窓口へ | **確認済み**(環境変数だけ。画面の説明が窓口 → Qwen3.5 で通る。§2) |
| Step 6 `ToolOrchestrationHub` への登録 | **登録済み**(台帳 13 件目・リポジトリ 14 件目)。Hub の画面で、一覧・オフィス・アラート・リポジトリに出ることを確認。**Start/Stop は未確認**(§3) |

### 設計書の Phase

| Phase | 状態 |
|---|---|
| 0 導入・実測 | 速度・入力の長さごとの速度(入力 5548 トークンで TTFT 2.25 秒)・入れ替え(約 9 秒)は測定済み。**16K・32K、B1/B2 の比較は未了** |
| 1 評価セット | 枠組み(`llh eval`)・スターター 24 問・難問 14 問。**実際の仕事の問題と、Claude の基準は、これから** |
| 2 窓口と機密度 | **完了** |
| 3 縦の線(資料への質問) | RAG ブリッジの `local` は作成済み。**本物での通しと、評価は未了** |
| 4 性能を上げる工夫 | A1 検証つき生成・A3 文脈の節約(`llh summarize`)・A6 エスカレーションの記録を実装。**A1 を実機で測定**([docs/PHASE4.md](docs/PHASE4.md))。A2・A4・A5・A7〜A13 は、効果の大きい順に、これから |
| 5 連携・道具 | `llh mcp`(local-llm-mcp)・CLI(`eval` / `route` / `bench` / `policy-check` / `summarize` / `export-sft`)を実装。**Claude Code からの実接続は未確認** |
| 6 微調整(任意) | **準備だけ**(`export-sft`・開始の条件)。学習は、評価で「負けている区分」が分かってから |

### まだ無いもの(正直に)

- **クラウドへの経路**: 窓口はローカルの裏側にしか送りません(「ローカルのみ」モード=常時 ON)。機密度の判定は**記録と表示**に使われ、振り分けのエスカレーション(Claude への回し)は、**記録まで**(`logs/escalations.jsonl`)で、実際に Claude へ回す経路は未実装です。経路を足すときは、必ず `Policy.check_egress` を通します。
- **評価は、小さい試験です**: スターター 24 問は、Qwen3.5-9B が全問合格で、差が出ません。難問 14 問で A1 を測りましたが、温度 0 で 3 回解くので、実質の標本は 14 問です。**区分ごとに「ローカルに回せる」と決めるには、実際の仕事の問題(40 問前後)と、Claude の基準が要ります。**
- **VRAM は余裕がありません**: main の読み込み中で空き約 0.8GB(他のプロセスの分を含む)。画像付きの質問は重い(4K の画面全体で約 2000 トークン・約 34 秒)ので、VLMAutoReplayTool 側で縮小します(既定 1280px。縮小後の速度と、小さい文字が読めるかは未測定)。
- **利用者ごとのトークン・管理者/デバッグの画面分け**(設計書「機能の範囲」): 未実装。今は単一の任意トークンだけ。
- **優先度つきの順番待ち**: 直列化(先着順)だけ。
- **トークン数の見積もり**は「3 文字 ≒ 1 トークン」の目安で、厳密ではありません。
- **Mermaid の簡易検査**は、描画できることを保証しません。`mmdc`(PATH にあれば自動で使う)を併用してください。

## テスト

```powershell
uv run pytest -q
```

偽の Unsloth(`tests/fake_backend.py`)を立てて、振り分け・機密度・直列化・ストリーム・監査ログの連鎖・入口の守り・CLI を確認します。疑似の秘密 20 件がすべて止まり、通常の入力 20 件が誤って止まらないこと(設計書 Phase 2 の Go)も、ここで確認しています。
