# 評価セット(`evals/`)

設計書 §8 の評価です。「並み」かどうかを、**用途ごとに測って決めます**(全体で並ぶとは言いません)。

```powershell
uv run llh eval                                  # evals/cases の全問を、3 回ずつ解く(温度 0)
uv run llh eval --category 要約・分類 --repeat 3
uv run llh eval --allow-exec                     # 生成コードを実行して採点する(下の注意を読む)
uv run llh eval --baseline evals\baseline.json   # 基準(Claude)と比べて、ローカルに回せる区分を出す
```

結果は `evals/results/<日時>-<ロール>.jsonl`(`.gitignore` 済み)。モデルの出力の抜粋を含むので、**ローカルのみ**です。

## 問題の書き方(`evals/cases/*.jsonl`、1 行 1 問)

```json
{"id": "cls-001", "category": "要約・分類", "role": "writer", "max_tokens": 60,
 "prompt": "次のログの重大度を error / warning / info のどれかで答えて。\n\n…",
 "score": [{"type": "label", "labels": ["error", "warning", "info"], "expected": "error"}]}
```

| フィールド | 意味 |
|---|---|
| `id` | 一意な名前(基準との対応に使う) |
| `category` | 区分。下の表の名前にそろえると、集計が見やすい |
| `prompt` / `messages` | 質問(`system` を足せる)。`messages` なら、会話をそのまま書ける。`image`(png/jpg/webp)で画像も付けられる |
| `role` | `main` / `vision` / `writer`。無ければ区分の既定(要約・分類は writer)、それも無ければ `--role` |
| `task` | 窓口への `llh.task`(`rag` / `code` / `mermaid` など) |
| `max_tokens` / `temperature` | 出力の上限(既定 512)。温度は `--temperature`(既定 0)を、問題ごとに上書きできる |
| `tools` / `tool_choice` | 関数呼び出しの定義(呼び出し側が実行する普通の形) |
| `score` | 採点(1 つ、または配列。**配列は、全部通って合格**) |

### 採点の種類(機械でできるものだけ)

| `type` | 合格の条件 |
|---|---|
| `exact` | `expected` と一致(空白・句点・大文字小文字は無視) |
| `contains` | `all` の語を全部含み、`none` の語を含まない |
| `any` | `any` のどれかを含む |
| `regex` | `pattern` に一致 |
| `label` | `labels` のうち、**最初に出てくるもの**が `expected` |
| `json` | JSON を取り出せて、`required` のキー・`types`・`equals` を満たす |
| `mermaid` | ` ```mermaid ` の図が構文検査を通り、`all` の語を含む |
| `citation` | 回答に `[n]` の引用があり、`cited` の番号を全部含み、`sources` を超える番号が無い |
| `tool_call` | 最初のツール呼び出しが `name` で、`args` が一致 |
| `python` | ` ```python ` のコードに `tests`(assert)を足して、実行が成功(`--allow-exec` のときだけ) |
| `policy` | **LLM を使わず**、窓口の機密度の判定が `expect`(`local-only` / `cloud-ok`)になる。`text` / `paths` / `manual` を渡せる |

### 注意

- **`--allow-exec` は、モデルが書いたコードを、この PC で実行します。サンドボックスではありません**(一時フォルダ・環境変数なし・10 秒の制限だけ)。自分で問題とテストを確認したときだけ付けてください(設計書 S9)。付けない間、`python` の問題は「採点しなかった」になり、合格率に数えません。
- **問題に機密を入れない**。入れるなら、ローカルのモデルだけで採点します。
- **問題と答えは、学習に混ぜない**(設計書 §8.2)。
- 考える(thinking)モードが出力を使い切ると、本文が空になって不合格になります(結果に「本文が空」と出ます)。区分ごとに `enable_thinking` を `config.toml` で調整してください。

## 基準(Claude)との比べ方

1. 同じ問題を Claude で解き、問題ごとの合格率を `evals/baseline.json` に書きます(`{"rag-001": 1.0, "code-001": 0.67}`)。
   `llh eval` の結果(`.jsonl`)も、そのまま基準にできます(`--baseline` に渡す)。
2. `uv run llh eval --baseline evals\baseline.json` で、区分ごとに「ローカルに回せる(合格率が基準の 85% 以上)」「同等(差が小さい)」「Claude に残す」が出ます。
3. **問題が少ないと、±15 ポイント程度は誤差**です。区分ごとに 8 問前後から始め、差が分からなければ 15 問以上に増やします。

## 区分と、最初の目標(設計書 §8.1)

| 区分 | 例 | 採点 |
|---|---|---|
| 資料への質問 | Houdini/VEX の使い方、Unity の API | `contains` + `citation` |
| コード生成(検証できる) | VEX・Python・UdonSharp の小さなもの | `python`(テスト) |
| 要約・分類・タグ付け | ログの分類、記事のラベル | `label` / `json` |
| ツール呼び出し | ファイル検索 → 読む → 答える | `tool_call` |
| 構造の把握 | リポジトリの構造を図にする | `mermaid` |
| 長い作業 | 3 ファイルにまたがる修正 | (機械で採点しにくい。**負ける前提で記録**) |
| 機密度の判定 | ダミーの秘密・禁止パス・通常の入力 | `policy` |

`evals/cases/starter.jsonl` は、**枠組みの動作確認用**の 24 問(題材は架空)です。実際の仕事の問題を、自分の `evals/cases/mine.jsonl` などに足し、40 問前後まで増やしてください(スターターは、そのまま残しても、消しても構いません)。


## 実際の仕事の問題の「案」(`llh draft-cases`)

自分で問題を書く前に、**公開リポジトリの実際のコミット・文書から、機械で採点できる問題の案**を作れます。

```powershell
uv run llh draft-cases                       # evals/drafts/*.jsonl と、確認用の evals/drafts/REVIEW.md ができる
uv run llh eval --cases evals/drafts --repeat 1   # 案を、ローカルのモデルで試しに解く(採点が壊れていないかの確認にもなる)
```

| 区分 | 元になるもの | 採点(近似) |
|---|---|---|
| コミット文案 | 実際のコミットの差分 | 元のメッセージにある語(差分にも出てくるもの)に触れる・日本語・リポジトリの書式 |
| 変更の分類 | 接頭辞つき(feat / fix / docs …)の実際のコミット | 元の接頭辞と一致 |
| 文書の要約 | README・設計書の 1 節 | 節の重要な用語(`` `…` `` で書かれたもの)を 2 個以上そのまま使う |
| 資料への質問 | README などの記述(答えが実在することを、生成時に確かめる) | 答えの語 + 引用の番号 |
| 図の作成 | 文書にある Mermaid の flowchart(ノード名と矢印を取り出す) | 構文検査 + ノード名を 8 割以上そのまま使う |
| ツール選択 | あなたの依頼の言い方 + 計画中の安全なラッパー関数 | 期待する関数・引数(`git_push` をいきなり呼ばない) |
| Git の知識 | 基本のコマンド | 答えにコマンドを含む |

- **材料にするのは、公開(PUBLIC)で、コミット済みで、作業ツリーで変更されていないファイル・コミットだけ**です。未コミットのファイル、非公開のリポジトリ(Enterprises 配下など)、セッションの文章は、材料にしません。外に出さない区分(秘密・個人のパス・メール)を含むものも、問題文全体を検査して除きます。
- **正解は、元のコミット・文書にある事実から取ります**(私が作りません)。ただし採点は近似です。確認用の `evals/drafts/REVIEW.md` に、問題ごとの「元」「確かめていること」「確認してほしい点」があります。
- 使う問題は、行を `evals/cases/mine.jsonl` にコピーします(`_source` の項目は無視されます)。案は `evals/drafts` にあるうちは、`llh eval` の既定の対象になりません。
- 図のノード名・語の一致は、**全角・半角・空白の違いを無視**します(`metricsを` と `metrics を` は同じ)。
- 設計の相談・長い自律作業など、機械で採点しにくい依頼は、**Claude に残す前提で、案を作っていません**。
- `evals/private/request_review.md`(git に入れない)は、あなた自身の依頼文を、区分ごとに「ローカル向きか Claude 向きか」で仮置きした確認シートです。
