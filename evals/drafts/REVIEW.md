# 評価問題の案(確認用)

公開リポジトリの**実際のコミット・文書**から、機械で採点できる区分の問題の**案**を作りました(`uv run llh draft-cases`)。
**そのままでは、評価に使われません**(`evals/cases` ではなく、`evals/drafts` にあります)。確認して、使うものを `evals/cases/mine.jsonl` に移してください。

## 注意(採点は近似です)
- 正解は、元のコミット・文書にある**事実**から取っています(私が作っていません)。ただし、採点は『語を含むか・形式に合うか』の**近似**で、良い答えを取りこぼすことも、悪い答えを通すこともあります。
- 元のコミットメッセージには、Claude が書いたものが含まれます。『あなたが良いと思う答え』とは限りません。
- **外に出さない区分**(秘密・個人のパス・メール)を含むコミット・文書は、材料にしていません。非公開のリポジトリ(Enterprises 配下など)と、セッションの文章は、この一覧に入れていません。

## 件数

| 区分 | 案の数 |
|---|---|
| コミット文案 | 13 |
| 変更の分類 | 9 |
| 文書の要約 | 8 |
| 資料への質問 | 7 |
| 図の作成 | 8 |
| ツール選択 | 7 |
| Git の知識 | 6 |

合計 58 件。除外したもの: 10 件(下の「除外」)。

## コミット文案

| id | 元 | 確かめていること | 確認してほしい点 |
|---|---|---|---|
| `d-commit-assetdatai-b3e11a9` | AssetDataInsightSuite@b3e11a9「KPI タイルの順次表示・棒グラフの伸長・エラー表示の横震えを足す」 | 日本語である・接頭辞つき(Conventional)・元のメッセージにある語 ['style.css', 'HTML', 'transform', 'opacity'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-cadgpuinfe-40d3898` | CADGPUInferenceModeling@40d3898「Phase 5: 精度チューニング(CPU側3ノブ中2つ採用)」 | 日本語である・平文・元のメッセージにある語 ['zbuf_size', 'MeshToDxfConfig', '_estimate_wall_thickness', 'DDXF_Sample.dxf', 'wall_snap_tol', 'snap'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-colorencyc-d83252e` | ColorEncyclopedia@d83252e「CSP などのセキュリティヘッダを next.config.ts で付ける」 | 日本語である・平文・元のメッセージにある語 ['next.config.ts', 'default-src', 'self', 'script/style', 'unsafe-inline', 'next'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-flowchartv-e77790d` | FlowchartVisualizerExtension@e77790d「「Mermaid をコピー」を押すと、アイコンが 1.4 秒だけチェックに替わるようにする」 | 日本語である・平文・元のメッセージにある語 ['Mermaid', 'Rider', 'Visual', 'Studio', 'copyText'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-learningfl-995664f` | LearningFluidEngine@995664f「docs: IMPROVEMENT_PLAN.md の各フェーズで測った性能の結果を記録」 | 日本語である・接頭辞つき(Conventional)・元のメッセージにある語 ['IMPROVEMENT_PLAN.md', 'Phase', 'gen_sample.py', 'gzip', 'fp16'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-learningqu-243e387` | LearningQuickDraw@243e387「docs: IMPROVEMENT_PLAN.md の Phase 3/4 のチェックリストを完了にする」 | 日本語である・接頭辞つき(Conventional)・元のメッセージにある語 ['IMPROVEMENT_PLAN.md', 'Phase'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-loredeskto-190fb7a` | LoreDesktopAndWebSystem@190fb7a「ブラウザ側の API プロキシに、空の環境変数ではなく、明示的なフラグを使う」 | 日本語である・平文・元のメッセージにある語 ['NEXT_PUBLIC_API_URL', 'NEXT_PUBLIC_USE_API_PROXY', 'localhost'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-ragreel-cc373c1` | RAGReel@cc373c1「公開のギャラリーでは、開発者向けの比較へのリンクを既定で出さない」 | 日本語である・平文・元のメッセージにある語 ['compare.html', 'localhost', 'README'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-ragreel-dac8c7b` | RAGReel@dac8c7b「ギャラリーのアップロード認証を、定数時間の比較に直す」 | 日本語である・平文・元のメッセージにある語 ['UPLOAD_TOKEN', 'SHA-256', 'safeEqual'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-thealgorit-3967f3b` | The-Algorithm-Illustrated@3967f3b「見出し・ヒーローのタイポグラフィをオーバーサイズ化(サイズでヒエラルキーを作る方向へ)」 | 日本語である・平文・元のメッセージにある語 ['AlgorithmDESIGN.md', 'amber', 'About'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-visualregr-536a3d3` | VisualRegressionQATool@536a3d3「WebUiToastAlertSink の、トーストのポーリングと書き込みの間の競合を修正」 | 日本語である・平文・元のメッセージにある語 ['WebUiToastAlertSink', 'api/alerts/toasts', 'notify_failure', 'notify_recovery', 'FastAPI', 'toasts_since'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-weathergeo-73d407d` | WeatherGeoBridge@73d407d「地図がまだ利用不可と表示される問題を修正: R2 のバケットに CORS ヘッダーが無かった」 | 日本語である・平文・元のメッセージにある語 ['CORS', 'curl', 'PMTiles', 'MapLibre', 'pmtiles', 'fetch'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |
| `d-commit-localllmha-bd9cbd2` | LocalLLMHarness@bd9cbd2「smoke に画像を付けて質問する --image を追加する」 | 日本語である・平文・元のメッセージにある語 ['smoke', 'image', 'vision', 'main', 'Vision', 'role'] のどれかに触れている | 元のメッセージは Claude が書いたものを含みます。『この語に触れていれば良い文案』と言えるか |

## 変更の分類

| id | 元 | 確かめていること | 確認してほしい点 |
|---|---|---|---|
| `d-cls-developmen-4010e6e` | DevelopmentRAGEnvironment@4010e6e「feat: Phase5 Cloud RAG searchMemory/saveMemory 品質向上」 | 元のコミットの接頭辞 `feat` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-developmen-d9b06c1` | DevelopmentRAGEnvironment@d9b06c1「feat: Phase2 RAGPolicyEnforcementPoint (PEP名前空間制御) 実装」 | 元のコミットの接頭辞 `feat` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-developmen-b0d5d52` | DevelopmentRAGEnvironment@b0d5d52「docs: READMEを最新の変更内容に合わせて更新」 | 元のコミットの接頭辞 `docs` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-learningfl-995664f` | LearningFluidEngine@995664f「docs: IMPROVEMENT_PLAN.md の各フェーズで測った性能の結果を記録」 | 元のコミットの接頭辞 `docs` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-learningqu-0fff56f` | LearningQuickDraw@0fff56f「docs: Phase 5 のチェックリストを完了にし、もともとあったレンダラーの癖を注記」 | 元のコミットの接頭辞 `docs` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-developmen-5f34bca` | DevelopmentRAGEnvironment@5f34bca「fix: MCP プロトコルバージョン不一致と null id エラーを修正」 | 元のコミットの接頭辞 `fix` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-developmen-5fe098d` | DevelopmentRAGEnvironment@5fe098d「fix: HyDEの仮説文書重みをドメイン別に調整（固有事実ドメインの精度低下を修正」 | 元のコミットの接頭辞 `fix` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-developmen-6feef23` | DevelopmentRAGEnvironment@6feef23「fix: 左右余白追加・管理サブタブ位置修正」 | 元のコミットの接頭辞 `fix` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |
| `d-cls-learningfl-e9513f0` | LearningFluidEngine@e9513f0「perf: SPH の近傍マスクをベクトル化し、CG による圧力ソルバーの実験を記録」 | 元のコミットの接頭辞 `perf` と一致するか | 接頭辞は、あなた(または Claude)が付けたもの。この diff なら別の種類とも言えるものは、削ってください |

## 文書の要約

| id | 元 | 確かめていること | 確認してほしい点 |
|---|---|---|---|
| `d-sum-assetdatai-ea28c1` | AssetDataInsightSuite/AssetDataInsightSuite_DESIGN.md(第 5 節) | 要約が、文書の重要な用語 ['output/cli_adapter.py', 'output/excel_adapter.py', 'openpyxl', 'DefectRecord', 'report_manifest.json', 'sections'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |
| `d-sum-developmen-f2439a` | DevelopmentRAGEnvironment/IMPROVEMENT_DESIGN_2026-10.md(第 11 節) | 要約が、文書の重要な用語 ['chatUi.ts', 'memory.ts', 'Ctrl+K', '#answer=<id>', 'language'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |
| `d-sum-flowchartv-ce3989` | FlowchartVisualizerExtension/FlowchartVisualizerExtension_DESIGN.md(第 5 節) | 要約が、文書の重要な用語 ['PsiElement', 'PsiTreeUtil', 'が担当する。', 'executeJavaScript', 'window.postMessage', 'plugin.xml'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |
| `d-sum-learningqu-7df045` | LearningQuickDraw/README.md(第 1 節) | 要約が、文書の重要な用語 ['mini-renderer/', 'mini-renderer-gpu/', '.vol', 'blender-fluid-viewer/', 'houdini-fluid-hda/', 'render-optimizer/'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |
| `d-sum-magiccircl-4635b8` | MagicCircleGenerator/DESIGN.md(第 1 節) | 要約が、文書の重要な用語 ['ss01', 'ss02', '{rounded.xxxl}', '{rounded.feature}'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |
| `d-sum-ragreel-fc42da` | RAGReel/IMPROVEMENT_DESIGN_2026-10.md(第 5 節) | 要約が、文書の重要な用語 ['auto-fill', 'auto-fit', 'random.html', 'prefers-color-scheme'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |
| `d-sum-toolorches-0dc355` | ToolOrchestrationHub/IMPROVEMENT_DESIGN_2026-10.md(第 11 節) | 要約が、文書の重要な用語 ['repo_sync', 'graphify', 'graph.html', 'GRAPH_REPORT.md', 'isolation', 'owner_rules'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |
| `d-sum-visualregr-7711c4` | VisualRegressionQATool/README.md(第 1 節) | 要約が、文書の重要な用語 ['UnityHDRPBlackHole', 'capture_agents/', '(文書の検査を含む)・', 'VRQA_AUTH=off', 'VRQA_AUTH=required'] のうち 2 個以上をそのまま含む | 『用語を 2 個以上残す』は、良い要約の必要条件の近似です。言い換えを許したい節は、除いてください |

## 資料への質問

| id | 元 | 確かめていること | 確認してほしい点 |
|---|---|---|---|
| `d-qa-toolorches-4d2755` | ToolOrchestrationHub/README.md(根拠: uv run hub) | 答えに ['127.0.0.1:8790'] を含み、根拠の資料 [2] を引用する(もう一方は、同じ文書の別の場所の抜粋) | 質問の言い方は、私が作りました。実際に聞くときの言い方に直してください |
| `d-qa-toolorches-547247` | ToolOrchestrationHub/README.md(根拠: HUB_AUTH=required) | 答えに ['HUB_AUTH', 'required'] を含み、根拠の資料 [1] を引用する(もう一方は、同じ文書の別の場所の抜粋) | 質問の言い方は、私が作りました。実際に聞くときの言い方に直してください |
| `d-qa-toolorches-f2cc3a` | ToolOrchestrationHub/README.md(根拠: 止められるのは) | 答えに ['Hub が起動した'] を含み、根拠の資料 [1] を引用する(もう一方は、同じ文書の別の場所の抜粋) | 質問の言い方は、私が作りました。実際に聞くときの言い方に直してください |
| `d-qa-toolorches-4a3c9d` | ToolOrchestrationHub/README.md(根拠: HUB_ALLOWED_HOSTS) | 答えに ['HUB_ALLOWED_HOSTS'] を含み、根拠の資料 [2] を引用する(もう一方は、同じ文書の別の場所の抜粋) | 質問の言い方は、私が作りました。実際に聞くときの言い方に直してください |
| `d-qa-docspipeli-36e3c4` | DocsPipeline/README.md(根拠: check-mermaid [PATH) | 答えに ['init'] を含み、根拠の資料 [1] を引用する(もう一方は、同じ文書の別の場所の抜粋) | 質問の言い方は、私が作りました。実際に聞くときの言い方に直してください |
| `d-qa-docspipeli-429aec` | DocsPipeline/README.md(根拠: publish [DOCS]) | 答えに ['local'] を含み、根拠の資料 [2] を引用する(もう一方は、同じ文書の別の場所の抜粋) | 質問の言い方は、私が作りました。実際に聞くときの言い方に直してください |
| `d-qa-vlmautorep-4deef3` | VLMAutoReplayTool/src/vlm_auto_replay/ask.py(根拠: VLM_LLM_BASE_URL) | 答えに ['VLM_LLM_BASE_URL'] を含み、根拠の資料 [1] を引用する(もう一方は、同じ文書の別の場所の抜粋) | 質問の言い方は、私が作りました。実際に聞くときの言い方に直してください |

## 図の作成

| id | 元 | 確かめていること | 確認してほしい点 |
|---|---|---|---|
| `d-mmd-assetdatai-a6bc9d` | AssetDataInsightSuite/docs/reference/setup-and-operations.md(1 個目の図) | Mermaid の構文検査に通る・指定したノード名 7 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |
| `d-mmd-cadgpuinfe-ebbbc2` | CADGPUInferenceModeling/docs/learn/01-overview.md(1 個目の図) | Mermaid の構文検査に通る・指定したノード名 8 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |
| `d-mmd-developmen-c854fd` | DevelopmentRAGEnvironment/MCPdemo/gas-mcp-demo-report.md(5 個目の図) | Mermaid の構文検査に通る・指定したノード名 7 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |
| `d-mmd-docspipeli-4eebbf` | DocsPipeline/docs/lecture/03-files-and-processes.md(1 個目の図) | Mermaid の構文検査に通る・指定したノード名 8 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |
| `d-mmd-learningqu-48c6fb` | LearningQuickDraw/docs/learn/01-overview.md(1 個目の図) | Mermaid の構文検査に通る・指定したノード名 10 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |
| `d-mmd-loredeskto-3e6cd7` | LoreDesktopAndWebSystem/docs/ARCHITECTURE_AND_DESIGN.md(10 個目の図) | Mermaid の構文検査に通る・指定したノード名 9 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |
| `d-mmd-production-00a841` | ProductionTracker/docs/design-index.md(1 個目の図) | Mermaid の構文検査に通る・指定したノード名 9 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |
| `d-mmd-ragreel-2a9994` | RAGReel/README.md(1 個目の図) | Mermaid の構文検査に通る・指定したノード名 4 個のうち 8 割以上を、そのまま使っている | 元の図の矢印の向きは、採点していません(ノード名と構文だけ)。矢印も採点したい場合は、お知らせください |

## ツール選択

| id | 元 | 確かめていること | 確認してほしい点 |
|---|---|---|---|
| `d-tool-commit-push` | あなたの依頼の言い方(セッションの傾向)+ 計画中の安全なラッパー関数 | 期待する関数・引数を呼ぶ(コミット・プッシュ・デプロイは、関数を呼ぶだけ。実行の承認は人が行う設計) | いきなり push せず、まず変更を確かめる(読み取り)のが正しい、という設計にしています。この方針で良いか |
| `d-tool-push-only` | あなたの依頼の言い方(セッションの傾向)+ 計画中の安全なラッパー関数 | 期待する関数・引数を呼ぶ(コミット・プッシュ・デプロイは、関数を呼ぶだけ。実行の承認は人が行う設計) | 『確認済み』と言ったときは、すぐ push 関数(人の承認は実行時)を呼ぶ、という期待で良いか |
| `d-tool-deploy` | あなたの依頼の言い方(セッションの傾向)+ 計画中の安全なラッパー関数 | 期待する関数・引数を呼ぶ(コミット・プッシュ・デプロイは、関数を呼ぶだけ。実行の承認は人が行う設計) | デプロイは関数を呼ぶだけ(実行の承認は別)で良いか |
| `d-tool-hub-start` | あなたの依頼の言い方(セッションの傾向)+ 計画中の安全なラッパー関数 | 期待する関数・引数を呼ぶ(コミット・プッシュ・デプロイは、関数を呼ぶだけ。実行の承認は人が行う設計) | ツール ID の対応(正解)は、Hub の台帳どおり |
| `d-tool-mermaid-fix` | あなたの依頼の言い方(セッションの傾向)+ 計画中の安全なラッパー関数 | 期待する関数・引数を呼ぶ(コミット・プッシュ・デプロイは、関数を呼ぶだけ。実行の承認は人が行う設計) | 『直して』は fix=true、という期待で良いか |
| `d-tool-summarize` | あなたの依頼の言い方(セッションの傾向)+ 計画中の安全なラッパー関数 | 期待する関数・引数を呼ぶ(コミット・プッシュ・デプロイは、関数を呼ぶだけ。実行の承認は人が行う設計) | 要約は、ローカルの関数を使う、という期待で良いか |
| `d-tool-docs-build` | あなたの依頼の言い方(セッションの傾向)+ 計画中の安全なラッパー関数 | 期待する関数・引数を呼ぶ(コミット・プッシュ・デプロイは、関数を呼ぶだけ。実行の承認は人が行う設計) | — |

## Git の知識

| id | 元 | 確かめていること | 確認してほしい点 |
|---|---|---|---|
| `d-git-amend` | Git の初心者向けドキュメントを作った実績(セッション)に沿った、基本の知識 | 答えに ['commit --amend'] のどれかを含む | 一般的な知識で、あなたの仕事に固有ではありません。不要なら除いてください |
| `d-git-stash` | Git の初心者向けドキュメントを作った実績(セッション)に沿った、基本の知識 | 答えに ['stash'] のどれかを含む | 一般的な知識で、あなたの仕事に固有ではありません。不要なら除いてください |
| `d-git-fetch` | Git の初心者向けドキュメントを作った実績(セッション)に沿った、基本の知識 | 答えに ['fetch'] のどれかを含む | 一般的な知識で、あなたの仕事に固有ではありません。不要なら除いてください |
| `d-git-namesonly` | Git の初心者向けドキュメントを作った実績(セッション)に沿った、基本の知識 | 答えに ['--name-only'] のどれかを含む | 一般的な知識で、あなたの仕事に固有ではありません。不要なら除いてください |
| `d-git-reset-soft` | Git の初心者向けドキュメントを作った実績(セッション)に沿った、基本の知識 | 答えに ['reset --soft'] のどれかを含む | 一般的な知識で、あなたの仕事に固有ではありません。不要なら除いてください |
| `d-git-switch-c` | Git の初心者向けドキュメントを作った実績(セッション)に沿った、基本の知識 | 答えに ['switch -c', 'checkout -b', 'switch --create'] のどれかを含む | 一般的な知識で、あなたの仕事に固有ではありません。不要なら除いてください |

## 除外

| 種類 | 元 | 理由 |
|---|---|---|
| commit | DevelopmentRAGEnvironment@61df970 | 外に出さない区分(秘密・個人のパス・メール)を含む |
| commit | DevelopmentRAGEnvironment@debb877 | 外に出さない区分(秘密・個人のパス・メール)を含む |
| commit | DevelopmentRAGEnvironment@8cbf7ba | 外に出さない区分(秘密・個人のパス・メール)を含む |
| commit | DevelopmentRAGEnvironment@2a06ede | 外に出さない区分(秘密・個人のパス・メール)を含む |
| commit | LearningQuickDraw@aa4acea | 外に出さない区分(秘密・個人のパス・メール)を含む |
| classify | DevelopmentRAGEnvironment@8cbf7ba | 外に出さない区分を含む |
| classify | DevelopmentRAGEnvironment@2a06ede | 外に出さない区分を含む |
| classify | LearningQuickDraw@aa4acea | 外に出さない区分を含む |
| qa | LocalLLMHarness/README.md | ファイルが見つからない(コミットされていない・変更中のものは、材料にしない) |
| commit | DevelopmentRAGEnvironment@829910b「CI の lint とテストを、新しいコードに合わせて直す」 | 問題文全体に、外に出さない区分が含まれる(content:email) |

## 使い方

```powershell
uv run llh eval --cases evals\drafts --repeat 1    # 案を、ローカルのモデルで試しに解く(採点が壊れていないかの確認にもなる)
```

- 使う問題は、行を `evals/cases/mine.jsonl` にコピーします(`_source` の項目は、残っていても無視されます)。
- 区分ごとに 8 問前後が目安です。足りない区分(設計の相談・長い作業など、機械で採点しにくいもの)は、Claude に残す前提で、案を作っていません。
