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
| サーバー側ツールの遮断 | Unsloth の `enable_tools`(Python 実行・ターミナル)は**必ず外して**中継 | S1 |
| **監査ログ** | `logs/audit.jsonl`。**内容でなくハッシュ・件数・モデル・秒数**だけ。各行が直前の行のハッシュを持ち、削除・改ざんに気づける(`llh audit-verify`) | S4 |
| 入口の守り | 127.0.0.1 だけで待ち受け(それ以外の `host` は起動しない)・Host の許可リスト・Origin の検査・任意のトークン | S13 |
| 行き先の表示 | `POST /v1/route`・`llh route`:送らずに、モデル・機密度・行き先だけを返す | Z4 |
| git diff の要約 | `llh diff-summary`:差分の要約とコミットメッセージ案。**コミット・push はしない**(読み取り専用の git だけ) | §4 |
| Mermaid の生成 | `llh mermaid`:生成 → 構文検査(`mmdc` があれば本物のパーサーも)→ 落ちたら問題を渡して作り直し(最大 3 回)。**通ったものだけ**出力・保存 | §4・A1 |

## クイックスタート

```powershell
uv sync
Copy-Item config.example.toml config.toml
Copy-Item policy.example.toml policy.toml     # 許可するパスを自分の環境に合わせて直す
$env:LLH_BACKEND_API_KEY = "sk-unsloth-…"      # Unsloth Desktop の Settings > API で作ったキー
uv run llh models                              # 裏側のモデル id を確認 → config.toml の [models.*].name を合わせる
uv run llh status                              # 裏側・読み込み済みモデル・VRAM・待ち受けの範囲
uv run llh smoke --role main                   # 単体動作と速さ(導入チェックリスト Step 2)
uv run llh serve                               # http://127.0.0.1:8800/v1
```

秘密(Unsloth の API キー・窓口のトークン)は**環境変数だけ**で渡します(`LLH_BACKEND_API_KEY`・`LLH_TOKEN`)。`config.toml`・`policy.toml`・`logs/` は `.gitignore` 済みです。

**1 度に 1 つのモデルしか載りません**(VRAM 8GB)。Unsloth は API からモデルを切り替えられないので、`vision` や `writer` を使うときは、Unsloth Desktop で手で読み込みます。読み込まれていないモデルへ送ると、既定では応答ヘッダー `X-LLH-Warning` で知らせます(`backend.on_model_mismatch = "error"` で 409 にもできます)。自動アンロード(TTL)は Unsloth Desktop 側で有効にしてください。

## 使い方

```powershell
uv run llh route "このログを分類して" --task log     # 送らずに行き先と機密度だけ見る
uv run llh policy-check C:\path\to\file               # 外に出せるか(0=出せる / 1=外に出さない)
uv run llh diff-summary --repo . [--staged]           # 差分の要約 + コミットメッセージ案(コミットはしない)
uv run llh mermaid "ユーザー→窓口→Unsloth の流れ" --out docs\flow.mmd
uv run llh audit-verify                               # 監査ログの連鎖を確かめる
```

呼び出し側(Python などの OpenAI クライアント)は、`base_url="http://127.0.0.1:8800/v1"`、`model="llh-main"`(または `auto`)にします。追加の指定は、リクエストの本体の `llh` で:

```json
{ "model": "auto", "messages": [...],
  "llh": { "task": "doc", "paths": ["C:/…/file.md"], "local_only": true } }
```

`llh` は裏側へ渡しません。`X-LLH-Local-Only: 1` のヘッダーでも「外に出さない」にできます。応答ヘッダー `X-LLH-Route` / `X-LLH-Model` / `X-LLH-Destination` / `X-LLH-Sensitivity` で、実際の行き先が分かります。

## 他のツールとの接続

導入チェックリストの Step 4〜6(`rag_local_bridge.py`・`VLMAutoReplayTool`・`ToolOrchestrationHub`)の手順と差し込むコードは [docs/INTEGRATION.md](docs/INTEGRATION.md)。**他のリポジトリは、まだ変更していません。**

## 進み具合(2026-11-21 時点)

| 導入チェックリスト | 状態 |
|---|---|
| Step 1 Unsloth Desktop の導入・API の有効化 | **未**(このPCに未導入。手作業) |
| Step 2・3 モデルの導入と単体確認 | **未**(導入後に `llh models` → `llh smoke --role all`) |
| 窓口・機密度の判定・振り分け・監査ログ・CLI(この repo) | **実装済み**。テストは**偽の Unsloth**で通し(`uv run pytest -q`)。**本物の Unsloth との接続は未確認** |
| Step 4 `rag_local_bridge.py` を窓口へ | **未**(今は `claude` / `gemini` だけで、ローカルの選択肢が無い。[INTEGRATION.md](docs/INTEGRATION.md) §1) |
| Step 5 `VLMAutoReplayTool` を窓口へ | 環境変数だけで可能(§2)。**疎通は未確認** |
| Step 6 `ToolOrchestrationHub` への登録 | 台帳の差し込み例あり(§3)。**未登録** |

### まだ無いもの(正直に)

- **クラウドへの経路**: 窓口はローカルの裏側にしか送りません(「ローカルのみ」モード=常時 ON)。機密度の判定は**記録と表示**に使われ、振り分けのエスカレーション(Claude への回し)は未実装です。経路を足すときは、必ず `Policy.check_egress` を通します。
- **Phase 0 の実測**(tok/s・VRAM・読み込み時間・B1/B2 の比較)・**評価セット `evals/`**・ツール呼び出しの正答率: 未着手。`llh smoke` は動作確認と、待ち時間込みの目安だけです。
- **利用者ごとのトークン・管理者/デバッグの画面分け**(設計書「機能の範囲」): 未実装。今は単一の任意トークンだけ。
- **優先度つきの順番待ち**: 直列化(先着順)だけ。
- **トークン数の見積もり**は「3 文字 ≒ 1 トークン」の目安で、厳密ではありません。
- **Mermaid の簡易検査**は、描画できることを保証しません。`mmdc`(PATH にあれば自動で使う)を併用してください。

## テスト

```powershell
uv run pytest -q
```

偽の Unsloth(`tests/fake_backend.py`)を立てて、振り分け・機密度・直列化・ストリーム・監査ログの連鎖・入口の守り・CLI を確認します。疑似の秘密 20 件がすべて止まり、通常の入力 20 件が誤って止まらないこと(設計書 Phase 2 の Go)も、ここで確認しています。
