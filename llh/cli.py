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
        body = {"model": spec.alias, "temperature": 0, "max_tokens": max_tok,
                "messages": [{"role": "user", "content": prompt}]}
        t0 = time.monotonic()
        try:
            res = h.chat(body)
        except HarnessError as e:
            _out(f"[{role}] {spec.name}: NG ({e.status} {e.code}) {e.message}")
            bad += 1
            continue
        try:
            dt = time.monotonic() - t0
            reply = (res.body["choices"][0]["message"].get("content") or "").strip()
            usage = res.body.get("usage") or {}
            ct = usage.get("completion_tokens")
            note = "(待ち時間込み)" if args.long else "(出力が短いので目安にならない。--long で測る)"
            speed = f"{ct / dt:.1f} tok/s{note}" if ct and dt > 0 else "tok/s 不明"
            _out(f"[{role}] {spec.name}: OK {dt:.1f}s  出力 {ct} トークン  {speed}  応答: {reply[:40]!r}")
            gpu = status_mod.gpu_status()
            if gpu:
                _out(f"    GPU: 使用 {gpu.used_mib} / {gpu.total_mib} MiB(空き {gpu.free_mib} MiB)")
            if "X-LLH-Warning" in res.headers:
                _out(f"    注意: {unquote(res.headers['X-LLH-Warning'])}")
        finally:
            res.close()
    return 1 if bad else 0


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
    s.set_defaults(func=cmd_smoke)

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
