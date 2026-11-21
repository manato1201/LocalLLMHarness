# 他のツールとの接続(導入チェックリスト Step 4〜6)

この文書は、**他のリポジトリを書き換えずに**、窓口(`http://127.0.0.1:8800/v1`)へつなぐ手順と、差し込むコードの案です。
適用は、各リポジトリの持ち主(自分)が確認してから行ってください。どれも**未適用・未検証**です(本物の Unsloth が未導入のため)。

前提: `llh serve` が動いていて、`llh status` で、使うモデルが「●読み込み済み」になっていること。
窓口にトークンを付けた場合(`LLH_TOKEN`)は、呼び出し側の API キーに同じ値を入れます。付けない場合、API キーは何でも構いません(窓口は見ません)。

## 1. Step 4: DevelopmentRAGEnvironment

### 1-a. `local-chat`(すぐ使える)

`local-chat/config.json`:

```json
{ "llm": { "base_url": "http://127.0.0.1:8800/v1", "model": "llh-main", "api_key": "" } }
```

`local-chat` は Unsloth のサーバー側ツールを要求しない作りで、窓口もそれを外すので整合します。

### 1-b. `scripts/rag_local_bridge.py`(コードの変更が要る)

確認した現状(2026-11-21): LLM の選択肢は `_LLM_BACKEND = "claude" | "gemini"` の 2 つだけで、**ローカルの OpenAI 互換につなぐ道が無い**(`/api/llm-backend` も 2 つだけ許す)。
つまり設計書の「接続先をローカルAPIへ変更」は、設定の書き換えでは済まず、**`local` の選択肢を足す**必要があります。差し込み案:

```python
# ─── ローカル LLM(LocalLLMHarness の窓口)呼び出し ───────────────────────────
LOCAL_LLM_URL = os.environ.get("RAG_LOCAL_LLM_URL", "http://127.0.0.1:8800/v1")


def _call_local(context_texts: list[str], query: str, history: list[dict]) -> str:
    """_call_claude と同じ形で、窓口(OpenAI 互換)へ。参考資料は外へ出ない。"""
    context = "\n\n".join(context_texts) if context_texts else "（参考ドキュメントなし）"
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": f"以下の参考ドキュメントを確認しました。\n\n{context}"})
    messages.append({"role": "assistant", "content": "参考ドキュメントを確認しました。ご質問にお答えします。"})
    for h in history[-6:]:
        role = "assistant" if h.get("role") in ("bot", "assistant") else "user"
        text = h.get("text", h.get("content", ""))
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": query})

    payload = json.dumps({
        "model": "llh-main", "messages": messages, "temperature": 0.3, "max_tokens": 1024,
        "llh": {"task": "rag"},          # 窓口が、機密度の判定と記録をする
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("LLH_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['LLH_TOKEN']}"
    req = urllib.request.Request(f"{LOCAL_LLM_URL}/chat/completions", data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:   # 9B のローカル推論は遅い。Claude の 30 秒では足りない
        return json.loads(resp.read())["choices"][0]["message"]["content"]
```

あわせて、変更が要る 3 か所:

1. `_LLM_BACKEND` の説明コメントに `"local"` を足す(`RAG_LLM_BACKEND=local` で既定にできる)。
2. `POST /api/llm-backend` の許可リスト `("claude", "gemini")` に `"local"` を足す。
3. `/query` の分岐(`if _LLM_BACKEND == "gemini": … else: _call_claude`)に `elif _LLM_BACKEND == "local": answer = _call_local(...)`。

注意: `Enterprises/AXTechCare/RAGEnvironment/scripts/rag_local_bridge.py` に**同名の別コピー**があります。設計書の「外に出さない」区分(Enterprises 配下)に当たるので、そちらは**ローカル(`local`)を既定にする**ことを検討してください。

## 2. Step 5: VLMAutoReplayTool

`vlm_auto_replay.prompts.openai_compat_client` は、loopback の http か https だけを許す作りなので、窓口(127.0.0.1)にそのままつながります。コードの変更は要りません。環境変数だけ:

```powershell
$env:VLM_LLM_BASE_URL    = "http://127.0.0.1:8800/v1"
$env:VLM_LLM_MODEL       = "llh-vision"        # 画像が付いていれば、モデル名を省いても vision に振り分けられる
$env:VLM_LLM_API_KEY_ENV = "LLH_TOKEN"         # 窓口にトークンを付けたときだけ(キーの「値」でなく、環境変数の「名前」)
```

- 先に、Unsloth Desktop で **Qwen2.5-VL (7B)** を読み込みます(他のモデルは、アンロードされます)。
- 画像は `image_url`(data URI)で来るので、窓口は**画像のデータを機密度の検査に使わず**(文字だけ検査)、`llh-vision` へ振り分けます。画面キャプチャに写った秘密は、この検査では見つけられません。**機密の画面は、窓口の外(クラウド)へ出さない運用で守ってください**(窓口にクラウドの経路は無い)。
- 疎通確認: `uv run python -m vlm_auto_replay.ask --base-url http://127.0.0.1:8800/v1 …`(引数は `ask.py` の `--help` で確認)。

## 3. Step 6: ToolOrchestrationHub への登録

`ToolOrchestrationHub/hub/registry.yaml` の `tools:` に足す案(ホットリロードされます。壊れた台帳は拒否され、古いまま動きます):

```yaml
  - tool_id: local_llm_harness
    display_name: "LocalLLMHarness"
    symbol: "Lh"
    color: "#40636d"
    transport: http_bridge
    endpoint: "http://127.0.0.1:8800/health"
    health_check: http_bridge_check
    category: monitored
    isolation: none
    description: "ローカルLLM(Unsloth Desktop)の窓口。機密度の判定・モデルの振り分け・監査ログ。"
    owner_rules:
      - "窓口は 1 度に 1 リクエストだけ通す(VRAM 8GB)。同じ GPU の重い作業(Houdini の ML 学習など)と同時に動かさない"
      - "Hub が叩くのは未認証の /health だけ(トークンを付けた場合は、/health にも要る点に注意)"
    poll_interval_s: 30
    check_params:
      timeout_s: 3.0
```

- `symbol` / `color` は、他と重ならない値に直してください(台帳の規則は `registry.yaml` の冒頭)。
- Step 4 を入れたあとは、`dev_rag_environment` の `depends_on` に `local_llm_harness` を足します(`depends_on` は「このツールが使っているツール」。障害の波及の表示に使われます)。
- 窓口に `LLH_TOKEN` を付けると、`/health` も 401 になり、Hub の能動監視が通りません。Hub の監視を使うなら、トークンなし(127.0.0.1 だけの待ち受け)で運用するのが簡単です。
- `GET /health` は、**裏側(Unsloth)が止まっていても 200** を返し、中身の `backend: "up" | "down"` で伝えます(窓口自体は生きているため)。Hub が HTTP の 200 だけで「正常」と見ると、Unsloth が止まっていても緑になります。**裏側の死活も見たい場合は、Hub 側の判定に `backend == "up"` の確認を足す**か、別に `http://127.0.0.1:8888/v1/models` を能動監視にしてください。
- 起動・停止(`Start` / `Stop`)を Hub から行うなら、`hub/repos.yaml` 側に固定のコマンド(`uv run llh serve`)を登録します(Hub の「許可リストの固定コマンドだけ」の方針に合わせる)。
- 定期実行(Windows タスクスケジューラ → 窓口)は、直接シェルを操作させず、窓口の API か `llh` の固定のサブコマンドだけを呼びます。例: 毎朝の差分要約

  ```powershell
  schtasks /Create /SC DAILY /ST 09:00 /TN "llh-diff-summary" /TR "cmd /c cd /d C:\Users\matuu\Desktop\GameDevelopment\LocalLLMHarness && uv run llh diff-summary --repo C:\path\to\repo > logs\diff-summary.txt"
  ```

  出力は**案**です。コミット・push・デプロイは、人の承認か固定のスクリプトで行います(設計書 §4 の「実行領域」)。

## 4. DocsPipeline との関係

- `llh mermaid` の検査は、`mmdc` が PATH にあれば本物のパーサー(このPCには入っています)、無ければ簡易検査です。
- DocsPipeline の標準の `%%{init}%%` の行を入れたいときは、`config.toml` の `[mermaid] require_init_line = true`(DocsPipeline が import できる環境で動かす)。
- **生成した図を文書に入れたあとは、必ず `docspipeline check-mermaid <文書>` と `docspipeline build` を通してください**(設計書 §4)。`llh mermaid` の合格は、その代わりにはなりません。
