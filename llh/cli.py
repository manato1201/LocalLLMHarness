"""`llh` コマンド。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from . import __version__
from .audit import verify_chain
from .backend import Backend, BackendError
from .config import ConfigError, load_config
from .core import Harness, HarnessError
from .policy import PolicyError, load_policy
from . import status as status_mod


def _out(*a) -> None:
    print(*a, flush=True)


def _harness(args) -> Harness:
    return Harness(load_config(args.config))


# ── コマンド ─────────────────────────────────────────────────
def cmd_serve(args) -> int:
    from .server import serve

    serve(load_config(args.config))
    return 0


def cmd_models(args) -> int:
    cfg = load_config(args.config)
    try:
        models = Backend(cfg).list_models()
    except BackendError as e:
        _out(f"裏側に届きません: {e.message}")
        return 2
    _out(f"{cfg.backend_base_url} のモデル({len(models)} 件。`loaded` は読み込み済み):")
    for m in models:
        _out(f"  {'●' if m.get('loaded') else '○'} {m.get('id')}")
    _out("\nconfig.toml の [models.*].name を、上の id に合わせてください。")
    return 0


def cmd_load(args) -> int:
    """ロールのモデルを、Unsloth に読み込ませる(`POST /v1/load`)。読み込み済みなら何もしない。"""
    h = _harness(args)
    spec = h.cfg.models.get(args.role)
    if spec is None:
        _out(f"[{args.role}] config に無いロールです({', '.join(h.cfg.models)})")
        return 1
    _out(f"[{args.role}] {spec.load_path}"
         + (f" ({spec.load_variant})" if spec.load_variant else "")
         + " を読み込みます(初回は時間がかかります。読み込み済みのモデルは入れ替わります)…")
    try:
        sec = h.ensure_model_loaded(spec)
    except HarnessError as e:
        _out(f"NG ({e.status} {e.code}): {e.message}")
        return 2
    _out("すでに読み込み済みです。" if sec is None else f"読み込みました({sec:.1f} 秒)。")
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config)
    s = status_mod.collect(cfg)
    if args.json:
        _out(json.dumps(s, ensure_ascii=False, indent=2))
        return 0 if s["backend"] == "up" else 2
    _out(f"待ち受け: {s['listen_host']}  loopback のみ: {'はい' if s['listen_loopback_only'] else '**いいえ(危険)**'}")
    _out(f"ローカルのみモード: {'ON' if s['local_only'] else 'OFF'}")
    _out(f"裏側: {s['backend_url']} → {'接続OK' if s['backend'] == 'up' else '**届きません** ' + s.get('backend_error', '')}")
    if s["backend"] == "up":
        _out(f"読み込み済み: {', '.join(s['loaded']) or 'なし'}")
    for role, c in s["configured"].items():
        mark = {True: "●読み込み済み", False: "○未読み込み", None: "?(不明)"}[c["loaded"]]
        known = {True: "", False: "  ※裏側に無い id です", None: ""}[c["known_to_backend"]]
        _out(f"  [{role}] {c['alias']} → {c['name']}  {mark}{known}")
    g = s["gpu"]
    _out("GPU: " + (f"{g['name']}  使用 {g['used_mib']} / {g['total_mib']} MiB(空き {g['free_mib']} MiB)" if g else "取得できません"))
    return 0 if s["backend"] == "up" else 2


_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
_IMAGE_MAX_BYTES = 4 * 1024 * 1024  # 窓口の文脈の上限(8192 トークン)に対し、画像は 1 枚 約 1000 トークンの見積もり。大きすぎる画像は縮小してから


def _image_content(path: str) -> list[dict]:
    p = Path(path)
    mime = _IMAGE_MIME.get(p.suffix.lower())
    if mime is None:
        raise ValueError(f"対応していない拡張子です({', '.join(_IMAGE_MIME)})")
    data = p.read_bytes()
    if len(data) > _IMAGE_MAX_BYTES:
        raise ValueError(f"画像が大きすぎます({len(data) // 1024} KB。上限 {_IMAGE_MAX_BYTES // 1024} KB)。縮小してください")
    import base64

    url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return [{"type": "text", "text": "この画像に何が写っているか、日本語で 2〜3 文で説明してください。"},
            {"type": "image_url", "image_url": {"url": url}}]


def cmd_smoke(args) -> int:
    """単体動作の確認(導入チェックリスト Step 2・3)。ロールごとに 1 回だけ、短い質問を送って速さを測る。"""
    h = _harness(args)
    roles = list(h.cfg.models) if args.role == "all" else [args.role]
    bad = 0
    for role in roles:
        spec = h.cfg.models.get(role)
        if spec is None:
            _out(f"[{role}] config に無いロールです")
            bad += 1
            continue
        if args.long:
            # 速度の測定用: 長めに出力させる(1 トークンだけでは、準備時間が大半で、tok/s にならない)
            prompt, max_tok = "ローカルLLMを使う利点を、日本語で 6 つ挙げ、それぞれ 2 文で説明してください。", 256
        else:
            prompt, max_tok = "1+1 は? 数字だけで答えて。", 32
        content: object = prompt
        if args.image:
            try:
                content = _image_content(args.image)
            except (OSError, ValueError) as e:
                _out(f"[{role}] 画像を読めません: {e}")
                bad += 1
                continue
            max_tok = max(max_tok, 200)
        body = {"model": spec.alias, "temperature": 0, "max_tokens": max_tok,
                "messages": [{"role": "user", "content": content}]}
        try:
            if h.cfg.on_model_mismatch == "load":
                # モデルの入れ替えは別に測る(生成速度に混ぜない)
                sec = h.ensure_model_loaded(spec)
                if sec is not None:
                    _out(f"[{role}] モデルを入れ替えました({sec:.1f} 秒)")
            t0 = time.monotonic()
            res = h.chat(body)
        except HarnessError as e:
            _out(f"[{role}] {spec.name}: NG ({e.status} {e.code}) {e.message}")
            bad += 1
            continue
        try:
            dt = time.monotonic() - t0
            choice = (res.body.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            reply = (message.get("content") or "").strip()
            usage = res.body.get("usage") or {}
            ct = usage.get("completion_tokens")
            note = "(プロンプトの処理を含む)" if args.long else "(出力が短いので目安にならない。--long で測る)"
            speed = f"{ct / dt:.1f} tok/s{note}" if ct and dt > 0 else "tok/s 不明"
            _out(f"[{role}] {spec.name}: OK {dt:.1f}s  出力 {ct} トークン  {speed}  応答: {reply[:40]!r}")
            if not reply:
                # 考える(thinking)モードが、出力の枠を使い切った可能性。原因の手がかりを出す
                extra = [k for k in message if k not in ("role", "content")]
                _out(f"    注意: 応答の本文が空です(finish_reason={choice.get('finish_reason')}、他のフィールド: {extra or 'なし'})。"
                     f"考える(thinking)モードが出力を使い切った可能性があります。config.toml の [models.{role}] に "
                     "enable_thinking = false を試してください")
            gpu = status_mod.gpu_status()
            if gpu:
                _out(f"    GPU: 使用 {gpu.used_mib} / {gpu.total_mib} MiB(空き {gpu.free_mib} MiB)")
            if "X-LLH-Warning" in res.headers:
                _out(f"    注意: {unquote(res.headers['X-LLH-Warning'])}")
        finally:
            res.close()
    return 1 if bad else 0


def cmd_bench(args) -> int:
    """入力の長さごとの速度(TTFT・入力の処理速度・生成速度)。Phase 0 の実測用。"""
    from .bench import run_bench

    h = _harness(args)
    try:
        sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    except ValueError:
        _out("--sizes は、カンマ区切りの整数(トークン数)にしてください。例: 512,2048,4096")
        return 1
    if args.role not in h.cfg.models:
        _out(f"[{args.role}] config に無いロールです({', '.join(h.cfg.models)})")
        return 1
    _out(f"[{args.role}] {h.cfg.models[args.role].name}  出力は最大 {args.out_tokens} トークン。入力の長さごとに測ります…")
    try:
        rows = run_bench(h, args.role, sizes, out_tokens=args.out_tokens, warmup=not args.no_warmup)
    except HarnessError as e:
        _out(f"NG ({e.status} {e.code}): {e.message}")
        return 2

    def f(v, spec):
        return "-" if v is None else format(v, spec)

    _out(f"{'狙い':>6} {'入力':>6} {'出力':>5} {'TTFT(s)':>8} {'入力処理 tok/s':>14} {'生成 tok/s':>10} {'合計(s)':>8}")
    bad = 0
    for r in rows:
        if r.error:
            bad += 1
            _out(f"{r.target:>6}  測れません: {r.error}")
            continue
        _out(f"{r.target:>6} {f(r.prompt_tokens, 'd'):>6} {f(r.completion_tokens, 'd'):>5} {f(r.ttft_s, '.2f'):>8} "
             f"{f(r.prefill_tps, '.0f'):>14} {f(r.decode_tps, '.1f'):>10} {f(r.total_s, '.2f'):>8}")
    if any(r.tokens_from == "chunks" for r in rows):
        _out("注: 裏側が usage を返さないので、出力のトークン数は受け取ったチャンクの数(目安)です。入力処理の速度は出せません。")
    g = status_mod.gpu_status()
    if g:
        _out(f"GPU: 使用 {g.used_mib} / {g.total_mib} MiB(空き {g.free_mib} MiB)")
    _out("設計書 Phase 0 の仮の基準: 出力 10 tok/s 以上、8K の入力処理が数十秒以内。")
    return 1 if bad == len(rows) else 0


def cmd_eval(args) -> int:
    """評価セットを解いて、区分ごとの合格率・速度を出す(設計書 §8)。基準(Claude)があれば、ローカルに回せるかも出す。"""
    import time as _time

    from . import evals
    from .config import ROOT

    h = _harness(args)
    try:
        cases = evals.load_cases(args.cases or ROOT / "evals" / "cases")
        baseline = evals.load_baseline(args.baseline) if args.baseline else None
    except evals.EvalError as e:
        _out(f"評価セットのエラー: {e}")
        return 1
    if args.category:
        cases = [c for c in cases if c.category in args.category]
    if args.id:
        cases = [c for c in cases if c.id in args.id]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        _out("該当する問題がありません。")
        return 1
    llm_cases = [c for c in cases if not c.is_policy]
    _out(f"{len(cases)} 問(うち LLM に解かせるのは {len(llm_cases)} 問)× {args.repeat} 回、温度 {args.temperature}。"
         + ("コードの実行: ON(サンドボックスではありません)" if args.allow_exec else ""))

    def tick(a) -> None:
        print("." if a.passed else ("s" if a.passed is None else "x"), end="", flush=True)

    try:
        attempts = evals.run_eval(h, cases, repeat=args.repeat, temperature=args.temperature, allow_exec=args.allow_exec,
                                  default_role=args.role, on_attempt=tick, verify_retries=args.verify_retries)
    except HarnessError as e:
        print()
        _out(f"NG ({e.status} {e.code}): {e.message}")
        return 2
    print()
    stats = evals.case_stats(attempts)
    rates = evals.category_rates(stats)
    _out(f"\n{'区分':<14}{'問題数':>6}{'合格率':>8}")
    for cat, (rate, n) in sorted(rates.items()):
        _out(f"{cat:<14}{n:>6}{rate:>8.0%}")
    errors = [a for a in attempts if a.error]
    fails = [a for a in attempts if a.passed is False and not a.error]
    for a in (errors + fails)[:15]:
        _out(f"  × {a.case_id} #{a.attempt}: {a.error or a.detail}")
    if len(errors) + len(fails) > 15:
        _out(f"  … ほか {len(errors) + len(fails) - 15} 件")
    skipped = {a.case_id: a.skipped for a in attempts if a.skipped}
    if skipped:
        _out(f"採点しなかった問題: {len(skipped)} 件({next(iter(skipped.values()))})")
    if args.verify_retries:
        vs = evals.verify_summary(attempts)
        if vs:
            _out(f"検証つき生成(再試行 {args.verify_retries} 回まで): 平均 {vs['mean_tries']:.2f} 回の生成、"
                 f"1 回目で通った {vs['first_try_pass']:.0%}、エスカレーション(通らず) {vs['escalation_rate']:.0%}")
    sp = evals.speed_summary(attempts)
    if sp:
        _out(f"速度: 1 問の中央値 {sp['median_s']:.1f} 秒、{sp['mean_tok_s']:.0f} tok/s(待ち時間込み)")
    if baseline is not None:
        comps = evals.compare(stats, baseline)
        _out(f"\n基準との比較(合格率が基準の {evals.PASS_RATIO_FOR_LOCAL:.0%} 以上なら、その区分はローカルに回す):")
        for c in comps:
            _out(f"  {c.category:<12} 問題 {c.cases:>2}  ローカル {c.local:>4.0%}  基準 {c.baseline:>4.0%}  → {c.verdict}")
        if not comps:
            _out("  基準にも載っている問題がありません(id を合わせてください)。")
    out_dir = Path(args.out) if args.out else ROOT / "evals" / "results"
    path = out_dir / f"{_time.strftime('%Y%m%d-%H%M%S')}-{args.role}.jsonl"
    evals.write_results(path, h, attempts, {
        "ts": _time.strftime("%Y-%m-%dT%H:%M:%S%z"), "repeat": args.repeat, "temperature": args.temperature,
        "cases_sha256": evals.cases_fingerprint(cases), "cases": len(cases), "default_role": args.role,
        "verify_retries": args.verify_retries,
    })
    _out(f"\n結果: {path}(出力の抜粋を含むので、ローカルのみ。git には入れない)")
    return 0 if not errors else 1


def cmd_summarize(args) -> int:
    """長い文書を、分割して要約する(設計書 A3)。全文を一度に渡さない。"""
    from .summarize import SummarizeError, summarize_file, summarize_text

    h = _harness(args)
    progress = None if args.quiet else (lambda m: print(f"  … {m}", file=sys.stderr, flush=True))
    try:
        if args.path == "-":
            r = summarize_text(h, sys.stdin.read(), sentences=args.sentences, role=args.role,
                               max_chunks=args.max_chunks, progress=progress)
        else:
            r = summarize_file(h, args.path, sentences=args.sentences, role=args.role,
                               max_chunks=args.max_chunks, progress=progress)
    except SummarizeError as e:
        _out(f"要約できません: {e}")
        return 1
    except HarnessError as e:
        _out(f"NG ({e.status} {e.code}): {e.message}")
        return 2
    _out(r.summary)
    _out("\n---")
    _out(f"モデル: {r.model} / 断片 {r.chunks} 個・呼び出し {r.calls} 回・{r.levels} 段 / {r.seconds:.1f} 秒 / "
         f"入力 {r.prompt_tokens}・出力 {r.completion_tokens} トークン / 機密度: {r.sensitivity or '-'}(処理はこのPCの中だけ)")
    for n in r.notes:
        _out(f"注: {n}")
    return 0


def cmd_mcp(args) -> int:
    """Claude Code 用の MCP サーバー(標準入出力)。登録: claude mcp add local-llm -- uv --directory <このフォルダ> run llh mcp"""
    from .mcp_server import run_stdio

    run_stdio(_harness(args))
    return 0


def cmd_export_sft(args) -> int:
    """検証に通った生成を、学習データ(chat 形式の JSONL)にまとめる(Phase 6 の準備。学習はしない)。"""
    from . import sft
    from .config import ROOT

    cfg = load_config(args.config)
    policy = load_policy(cfg.policy_path)
    sources = [cfg.verified_path, *args.extra] if not args.no_verified else list(args.extra)
    if not sources:
        _out("入力がありません。")
        return 1
    out = Path(args.out) if args.out else ROOT / "logs" / "sft.jsonl"
    rep = sft.export_sft(policy, out, sources, None if args.no_eval_guard else ROOT / "evals" / "cases")
    _out(f"読んだ {rep.read} 件 → 書き出し {rep.written} 件({out})")
    _out(f"  除外: 外に出さない区分 {rep.skipped_sensitive}・評価セットと同じ質問 {rep.skipped_eval_leak}・重複 {rep.skipped_duplicate}・形式が対象外 {rep.skipped_invalid}")
    if rep.by_label:
        _out("  区分: " + "、".join(f"{k} {v}" for k, v in sorted(rep.by_label.items())))
    if not cfg.save_verified and not args.extra:
        _out("注: config.toml の [training] save_verified = true にすると、検証に通った生成が logs/verified.jsonl に溜まります(既定は保存しない)。")
    _out(f"微調整を始める目安: {sft.MIN_EXAMPLES_TO_TRAIN} 件以上({'満たしています' if rep.ready else '足りません'})。"
         "その前に、評価で『効かせたい区分が負けている』ことを確かめてください(docs/PHASE6.md)。")
    return 0


def cmd_route(args) -> int:
    h = _harness(args)
    text = sys.stdin.read() if args.text == "-" else args.text
    paths = [str(Path(p).resolve()) for p in args.path or []]
    body = {"messages": [{"role": "user", "content": text}], "llh": {"paths": paths, "local_only": args.local_only}}
    if args.task:
        body["llh"]["task"] = args.task
    try:
        _out(json.dumps(h.plan(body), ensure_ascii=False, indent=2))
    except HarnessError as e:
        _out(f"{e.code}: {e.message}")
        return 1
    return 0


def cmd_policy_check(args) -> int:
    """その場所が外に出せるか。終了コード 0=出せる(判定上)、1=外に出さない。一致した文字列は表示しない。"""
    cfg = load_config(args.config)
    policy = load_policy(cfg.policy_path)
    p = Path(args.path).resolve()  # 相対パスは、絶対パスにしてから判定する(許可リストは絶対パスで書く)
    reasons = list(policy.check_path(str(p)))
    note = ""
    if p.is_file():
        try:
            if p.stat().st_size <= 2_000_000:
                reasons += policy.check_text(p.read_text(encoding="utf-8", errors="replace"))
            else:
                note = "(2MB を超えるため、内容の検査は省略)"
        except OSError as e:
            note = f"(読めませんでした: {e.__class__.__name__})"
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        _out(f"外に出さない: {args.path}\n  理由: {', '.join(reasons)} {note}")
        return 1
    _out(f"外に出せる(判定上): {args.path} {note}")
    return 0


def cmd_diff_summary(args) -> int:
    from .tasks import summarize_diff

    h = _harness(args)
    try:
        r = summarize_diff(h, Path(args.repo).resolve(), staged=args.staged, with_commit_message=not args.no_commit_message)
    except (RuntimeError, ValueError) as e:
        _out(f"git でエラー: {e}")
        return 1
    except HarnessError as e:
        _out(f"{e.code}: {e.message}")
        return 2
    if r is None:
        _out("差分がありません。")
        return 0
    _out(r.summary)
    _out(f"\n---\nモデル: {r.model} / 差分{'(長いため先頭のみ)' if r.truncated else ''} / 機密度: {r.sensitivity}")
    _out("※ これは案です。コミット・push はしていません。内容を確認してから、自分で実行してください。")
    return 0


def cmd_mermaid(args) -> int:
    from .tasks import generate_mermaid

    h = _harness(args)
    text = sys.stdin.read() if args.request == "-" else args.request
    try:
        r = generate_mermaid(h, text, max_attempts=args.attempts, use_mmdc=not args.no_mmdc)
    except HarnessError as e:
        _out(f"{e.code}: {e.message}")
        return 2
    if not r.ok:
        _out(f"構文検査を通せませんでした({r.attempts} 回試行)。問題:")
        for p in r.problems:
            _out(f"  - {p}")
        if r.code:
            _out("\n--- 最後の案(未検証。使わないでください) ---\n" + r.code)
        return 1
    if args.out:
        Path(args.out).write_text(r.code + "\n", encoding="utf-8")
        _out(f"書き出しました: {args.out}({r.attempts} 回目で合格 / 検査: {r.checked_with})")
    else:
        _out(f"```mermaid\n{r.code}\n```")
        _out(f"({r.attempts} 回目で合格 / 検査: {r.checked_with})")
    if r.checked_with == "syntax":
        _out("注意: 簡易検査のみです。確実にするには `mmdc`(mermaid-cli)を入れてください。")
    return 0


def cmd_audit_verify(args) -> int:
    cfg = load_config(args.config)
    ok, n, bad = verify_chain(cfg.audit_path)
    if ok:
        _out(f"監査ログの連鎖は正しい({n} 行): {cfg.audit_path}")
        return 0
    _out(f"監査ログの連鎖が {bad} 行目で食い違っています(途中の行が消された/書き換えられた可能性): {cfg.audit_path}")
    return 1


# ── 入口 ────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="llh", description="LocalLLMHarness: ローカルLLMの窓口")
    p.add_argument("--config", help="config.toml のパス(既定: ./config.toml → config.example.toml)")
    p.add_argument("--version", action="version", version=f"llh {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="窓口(OpenAI 互換)を起動する").set_defaults(func=cmd_serve)
    sub.add_parser("models", help="裏側のモデルの id を一覧する").set_defaults(func=cmd_models)

    s = sub.add_parser("load", help="ロールのモデルを Unsloth に読み込ませる(読み込み済みなら何もしない)")
    s.add_argument("--role", default="main", help="main / vision / writer")
    s.set_defaults(func=cmd_load)

    s = sub.add_parser("status", help="裏側・読み込み済みモデル・VRAM・待ち受けの範囲を確認する")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("smoke", help="ロールごとに短い質問を送り、単体動作と速さを確認する")
    s.add_argument("--role", default="main", help="main / vision / writer / all(既定 main)。1 度に 1 モデルしか載らないので、手で読み込んでから")
    s.add_argument("--long", action="store_true", help="長めに出力させて、生成速度(tok/s)を測る")
    s.add_argument("--image", help="画像ファイル(png/jpg/webp/gif)を付けて質問する。vision の確認用")
    s.set_defaults(func=cmd_smoke)

    s = sub.add_parser("bench", help="入力の長さごとの速度(TTFT・入力処理・生成速度)を測る")
    s.add_argument("--role", default="main", help="main / vision / writer")
    s.add_argument("--sizes", default="512,2048,4096,6000", help="狙う入力のトークン数(カンマ区切り)。文脈の上限を超える長さは測らない")
    s.add_argument("--out-tokens", type=int, default=64, help="出力の最大トークン数(既定 64)")
    s.add_argument("--no-warmup", action="store_true", help="最初の捨て測定をしない")
    s.set_defaults(func=cmd_bench)

    s = sub.add_parser("eval", help="評価セット(evals/cases)を解いて、区分ごとの合格率・速度を出す")
    s.add_argument("--cases", help="問題のファイル(.jsonl)かフォルダ(既定: evals/cases)")
    s.add_argument("--category", action="append", help="この区分だけ(複数可)")
    s.add_argument("--id", action="append", help="この id だけ(複数可)")
    s.add_argument("--role", default="main", help="区分に既定のロールが無い問題を解くロール(既定 main)")
    s.add_argument("--repeat", type=int, default=3, help="各問を解く回数(既定 3)")
    s.add_argument("--temperature", type=float, default=0.0, help="温度(既定 0。固定する)")
    s.add_argument("--limit", type=int, help="先頭の N 問だけ(試し用)")
    s.add_argument("--baseline", help="基準(Claude など)の問題ごとの合格率。{id: 0〜1} の JSON か、この `eval` の結果(.jsonl)")
    s.add_argument("--verify-retries", type=int, default=0, help="検証つき生成(A1): 採点に通らなければ、失敗の内容を渡して作り直す回数(既定 0=しない)")
    s.add_argument("--allow-exec", action="store_true", help="生成されたコードを実行して採点する(サンドボックスではない。自分で確認した問題だけに)")
    s.add_argument("--out", help="結果の保存先フォルダ(既定: evals/results)")
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("summarize", help="長い文書を、分割して要約する(全文を一度に渡さない)")
    s.add_argument("path", help="文書(テキスト・Markdown)。'-' で標準入力")
    s.add_argument("--sentences", type=int, default=5, help="要約の文の数の目安(既定 5)")
    s.add_argument("--role", default="writer", help="使うロール(既定 writer)")
    s.add_argument("--max-chunks", type=int, default=40, help="分ける断片の数の上限(超えたら、先頭だけ)")
    s.add_argument("--quiet", action="store_true", help="進み具合を出さない")
    s.set_defaults(func=cmd_summarize)

    sub.add_parser("mcp", help="local-llm-mcp(Claude Code 用の MCP サーバー。標準入出力)を起動する").set_defaults(func=cmd_mcp)

    s = sub.add_parser("export-sft", help="検証に通った生成を、学習データ(chat 形式の JSONL)にまとめる(Phase 6 の準備)")
    s.add_argument("--out", help="書き出し先(既定: logs/sft.jsonl)")
    s.add_argument("--extra", action="append", default=[], help="追加のデータ(chat 形式の JSONL。Claude が解いた答えなど。利用規約を確認のうえ)")
    s.add_argument("--no-verified", action="store_true", help="logs/verified.jsonl を使わない(--extra だけ)")
    s.add_argument("--no-eval-guard", action="store_true", help="評価セットとの重複の除外をしない(非推奨)")
    s.set_defaults(func=cmd_export_sft)

    s = sub.add_parser("route", help="送らずに、行き先・モデル・機密度だけを表示する")
    s.add_argument("text", help="質問の文('-' で標準入力)")
    s.add_argument("--task", help="code/json/mermaid/diff/log/rag/vision/doc/summary/note")
    s.add_argument("--path", action="append", help="この質問が触れるパス(複数可)")
    s.add_argument("--local-only", action="store_true", help="手動で『外に出さない』にする")
    s.set_defaults(func=cmd_route)

    s = sub.add_parser("policy-check", help="そのパスが外に出せるか(終了コード 0=出せる / 1=外に出さない)")
    s.add_argument("path")
    s.set_defaults(func=cmd_policy_check)

    s = sub.add_parser("diff-summary", help="git diff を要約し、コミットメッセージ案を作る(コミットはしない)")
    s.add_argument("--repo", default=".")
    s.add_argument("--staged", action="store_true")
    s.add_argument("--no-commit-message", action="store_true")
    s.set_defaults(func=cmd_diff_summary)

    s = sub.add_parser("mermaid", help="Mermaid 図を生成し、構文検査を通ったものだけ出す")
    s.add_argument("request", help="図にしたい内容('-' で標準入力)")
    s.add_argument("--out", help="検査に通ったら、このファイルに書く")
    s.add_argument("--attempts", type=int, default=3)
    s.add_argument("--no-mmdc", action="store_true", help="mmdc での検査を使わない")
    s.set_defaults(func=cmd_mermaid)

    sub.add_parser("audit-verify", help="監査ログの連鎖(改ざんの検知)を確かめる").set_defaults(func=cmd_audit_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows のコンソールで、日本語が化けないように
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, PolicyError) as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
