# 他のツールとの接続(導入チェックリスト Step 4〜6)

窓口(`http://127.0.0.1:8800/v1`)に、他のツールをつなぐ手順です。**2026-11-21 に Step 4〜6 を適用・確認しました**(下の「確認の範囲」)。
窓口にトークンを付けた場合(`LLH_TOKEN`)は、呼び出し側にも同じ値を渡します。付けなければ、API キーは何でも構いません(窓口は見ません)。

## 1. Step 4: DevelopmentRAGEnvironment

### 1-a. `scripts/rag_local_bridge.py`(適用済み)

`RAG_LLM_BACKEND=local`(または `POST /api/llm-backend {"backend": "local"}`)で、回答の生成を窓口に切り替えます。資料の抜粋は、このPCの外へ出ません。

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `RAG_LLM_BACKEND` | `claude` | `claude` / `gemini` / `local` |
| `RAG_LOCAL_LLM_URL` | `http://127.0.0.1:8800/v1` | 窓口。**loopback の http だけ**(それ以外は、送る前に断る) |
| `RAG_LOCAL_LLM_MODEL` | `llh-main` | 窓口のモデルの別名 |
| `RAG_LOCAL_CONTEXT_CHARS` | `12000` | 資料に使う文字数の上限。超えたら、後ろの資料から落とす(ローカルの文脈は 8K トークン前後) |
| `LLH_TOKEN` | なし | 窓口にトークンを付けたときだけ |

- 既定は `claude` のままです(動作を変えていません)。`local` のときは `ANTHROPIC_API_KEY` が要りません。
- 利用者の権限(namespace)の絞り込みは、バックエンドによらず同じです(権限の無い資料は、ローカルのモデルにも渡しません)。テストで確認しています。
- 窓口が止まっているときは、「`uv run llh serve` を起動してください」と出ます(500)。
- **Houdini のパネル(`rag_chatbot.py`)の切り替え欄は、まだ `claude` / `gemini` だけです。**`local` を選ぶには、環境変数か `/api/llm-backend` を使います。
- 変更: `scripts/rag_local_bridge.py`、テスト `houdini/tests/test_local_backend.py`(11 件)。
- **`Enterprises/AXTechCare/RAGEnvironment/scripts/rag_local_bridge.py` は、触っていません**(同名の別コピー)。「外に出さない」区分(Enterprises 配下)に当たるので、`local` を既定にすることを検討してください。

### 1-b. `local-chat`

`local-chat/config.json`:

```json
{ "llm": { "base_url": "http://127.0.0.1:8800/v1", "model": "llh-main", "api_key": "" } }
```

(未確認。`local-chat` を窓口につなぐ試験は、していません。)

## 2. Step 5: VLMAutoReplayTool(コードの変更なし・確認済み)

`vlm_auto_replay.prompts.openai_compat_client` は、loopback の http か https だけを許す作りなので、窓口にそのままつながります。環境変数だけ:

```powershell
$env:VLM_LLM_BASE_URL    = "http://127.0.0.1:8800/v1"
$env:VLM_LLM_MODEL       = "llh-vision"
$env:VLM_LLM_API_KEY_ENV = "LLH_TOKEN"         # 窓口にトークンを付けたときだけ(キーの「値」でなく、環境変数の「名前」)
```

通しの確認(2026-11-21): `python -m vlm_auto_replay.ask --image <画面のキャプチャ> "この画面の状態を、1 文で説明して"` が、窓口 → Qwen3.5-9B で、画面の内容を正しく説明しました(監査ログ: `role: vision`・`status: 200`・約 34 秒)。

- **遅さに注意**: 4K 級の画面全体の画像は、入力が約 2000 トークンになり、約 34 秒かかります。自動操作で何度も呼ぶなら、**画像を縮小して**(幅 1280px 程度)から渡してください。
- 画像は `image_url`(data URI)で来るので、窓口は**画像のデータを機密度の検査に使いません**(文字だけ検査)。画面キャプチャに写った秘密は、この検査では見つけられません。窓口にクラウドの経路は無いので、画像はこのPCの外へ出ません。
- `ask` の既定の待ち時間は 120 秒です。モデルの入れ替えが起きると、さらに 10 秒前後かかります。

## 3. Step 6: ToolOrchestrationHub(適用済み)

- `hub/registry.yaml` に `local_llm_harness`(13 件目)を追加: `http://127.0.0.1:8800/health` を 30 秒ごとに能動監視(`http_bridge_check`)。
- `hub/repos.yaml` に `local_llm_harness`(14 件目)を追加: `git fetch` の対象と、`uv run llh serve` の起動コマンド。
- 件数を固定しているテストと文書(12→13 ツール・13→14 リポジトリ)を更新しました。Hub のテストは 568 件すべて通ります。
- 実際の窓口に対する死活監視で `is_up=True` を確認しました。

注意点:

- **窓口を止めている間は、「停止」になり、通知(トースト)が出ます。**使うときだけ起動する運用なら、通知が多くなります。常用しないなら、`category` を見直してください。
- `/health` は、Unsloth が止まっていても 200 を返します(中身の `backend: "up" | "down"` で伝えます)。Hub は 200 だけを見るので、**Unsloth の死活は映りません**。
- 窓口に `LLH_TOKEN` を付けると、`/health` も 401 になり、Hub の監視が通りません。Hub を使うなら、トークンなし(127.0.0.1 だけの待ち受け)で運用するのが簡単です。
- Hub から窓口を起動する(`Start`)と、Hub の環境変数を引き継ぎます。`LLH_BACKEND_API_KEY` は `setx` で保存し、**Hub を再起動してから**使ってください。Unsloth Desktop は別に起動しておきます。
- 定期実行(Windows タスクスケジューラ → 窓口)は、直接シェルを操作させず、窓口の API か `llh` の固定のサブコマンドだけを呼びます。例: 毎朝の差分要約

  ```powershell
  schtasks /Create /SC DAILY /ST 09:00 /TN "llh-diff-summary" /TR "cmd /c cd /d C:\Users\matuu\Desktop\GameDevelopment\LocalLLMHarness && uv run llh diff-summary --repo C:\path\to\repo > logs\diff-summary.txt"
  ```

  出力は**案**です。コミット・push・デプロイは、人の承認か固定のスクリプトで行います(設計書 §4 の「実行領域」)。

## 4. DocsPipeline との関係

- `llh mermaid` の検査は、`mmdc` が PATH にあれば本物のパーサー、無ければ簡易検査です。
- DocsPipeline の標準の `%%{init}%%` の行を入れたいときは、`config.toml` の `[mermaid] require_init_line = true`(DocsPipeline が import できる環境で動かす)。
- **生成した図を文書に入れたあとは、必ず `docspipeline check-mermaid <文書>` と `docspipeline build` を通してください**(設計書 §4)。`llh mermaid` の合格は、その代わりにはなりません。

## 確認の範囲(2026-11-21)

| 項目 | 状態 |
|---|---|
| Step 4 `rag_local_bridge.py` の `local` | 偽の窓口でテスト(11 件)。**本物の窓口を通した `/query` は未確認**(RAG の索引が要る) |
| Step 5 VLMAutoReplayTool | **本物の窓口・Unsloth で通し確認済み** |
| Step 6 Hub | テスト 568 件通過。本物の窓口への死活監視を確認。**Hub の画面での表示・Start/Stop は未確認** |
