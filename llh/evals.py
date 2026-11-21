"""評価セット(設計書 §8): 「並み」かどうかを、用途ごとに測って決める。

- 問題は `evals/cases/*.jsonl`(1 行 1 問)。採点は**機械でできるものだけ**(正解の語・ラベル・JSON の形・Mermaid の構文・
  テストを通るコード・引用の番号・ツール呼び出し・機密度の判定)。
- 各問を、温度を固定して N 回(既定 3 回)解き、合格の回数を記録する(1 回では運で決まる)。
- Claude(基準)の結果と比べて、区分ごとに「ローカルに回せる」(合格率が基準の 85% 以上)かを出す。
  **問題が少ないと ±15 ポイント程度は誤差**なので、差が小さいときは「同等」とみなす。
- 問題・答えは学習に混ぜない。問題に機密を含めない(結果ファイルには、モデルの出力の抜粋が入るので、ローカルのみ・git に入れない)。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import __version__, mermaid
from .core import Harness, HarnessError

PASS_RATIO_FOR_LOCAL = 0.85   # 設計書 §8.2: ローカルの合格率が、Claude の 85% 以上なら、その区分はローカルに回す
NOISE_POINTS = 0.15           # 問題が少ないときの誤差の目安(±15 ポイント)
REPLY_KEEP_CHARS = 1500       # 結果に残す出力の長さ(デバッグ用。ローカルのみ)

#: 区分 → 既定のロール(case に role があれば、そちらが優先)
CATEGORY_ROLE = {
    "要約・分類": "writer", "文書": "writer",
}
VALID_SCORE_TYPES = {"exact", "contains", "any", "regex", "label", "json", "mermaid", "citation", "python", "tool_call", "policy"}


class EvalError(ValueError):
    pass


# ── 問題の読み込み ─────────────────────────────────────────
@dataclass
class Case:
    id: str
    category: str
    messages: list[dict]
    score: list[dict]
    role: str = ""
    task: str = ""
    max_tokens: int = 512
    temperature: float | None = None
    tools: list | None = None
    tool_choice: object | None = None
    source: str = ""

    @property
    def is_policy(self) -> bool:
        return any(s.get("type") == "policy" for s in self.score)


def _messages(raw: dict, where: str) -> list[dict]:
    if "messages" in raw:
        msgs = raw["messages"]
        if not isinstance(msgs, list) or not msgs:
            raise EvalError(f"{where}: messages は空でないリストにしてください")
        return msgs
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise EvalError(f"{where}: prompt か messages が必要です")
    msgs: list[dict] = []
    if raw.get("system"):
        msgs.append({"role": "system", "content": raw["system"]})
    content: object = prompt
    if raw.get("image"):
        import base64

        img = Path(raw["image"])
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(img.suffix.lower())
        if mime is None or not img.exists():
            raise EvalError(f"{where}: image が読めません: {img}")
        url = f"data:{mime};base64,{base64.b64encode(img.read_bytes()).decode('ascii')}"
        content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]
    msgs.append({"role": "user", "content": content})
    return msgs


def parse_case(raw: dict, source: str = "") -> Case:
    where = f"{source}: {raw.get('id', '?')}"
    for k in ("id", "category", "score"):
        if k not in raw:
            raise EvalError(f"{where}: '{k}' がありません")
    score = raw["score"] if isinstance(raw["score"], list) else [raw["score"]]
    for s in score:
        if not isinstance(s, dict) or s.get("type") not in VALID_SCORE_TYPES:
            raise EvalError(f"{where}: 採点の type は {sorted(VALID_SCORE_TYPES)} のどれか")
    if any(s["type"] == "policy" for s in score):
        messages = _messages(raw, where) if ("prompt" in raw or "messages" in raw) else [{"role": "user", "content": ""}]
    else:
        messages = _messages(raw, where)
    return Case(
        id=str(raw["id"]), category=str(raw["category"]), messages=messages, score=score,
        role=raw.get("role", "") or CATEGORY_ROLE.get(str(raw["category"]), ""), task=raw.get("task", ""),
        max_tokens=int(raw.get("max_tokens", 512)), temperature=raw.get("temperature"),
        tools=raw.get("tools"), tool_choice=raw.get("tool_choice"), source=source,
    )


def load_cases(path: str | Path) -> list[Case]:
    p = Path(path)
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    if not files or not all(f.exists() for f in files):
        raise EvalError(f"問題のファイルが見つかりません: {p}")
    cases: list[Case] = []
    seen: set[str] = set()
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                raw = json.loads(line)
            except ValueError as e:
                raise EvalError(f"{f.name}:{n}: JSON として読めません: {e}") from e
            case = parse_case(raw, f"{f.name}:{n}")
            if case.id in seen:
                raise EvalError(f"{f.name}:{n}: id が重複しています: {case.id}")
            seen.add(case.id)
            cases.append(case)
    return cases


# ── 採点 ────────────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().strip("。.!！ ").casefold()


def _extract_code(reply: str, lang: str = "") -> str | None:
    m = re.search(r"```[ \t]*(?:" + (lang or r"\w*") + r")[ \t]*\r?\n(.*?)```", reply, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else None


def _extract_json(reply: str):
    code = _extract_code(reply, "json")
    candidates = [code] if code else []
    candidates.append(reply)
    for text in candidates:
        text = text.strip()
        starts = [i for i, c in enumerate(text) if c in "{["][:20]
        for start in starts:
            try:
                return json.JSONDecoder().raw_decode(text[start:])[0]
            except ValueError:
                continue
    raise ValueError("JSON が見つかりません")


_JSON_TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}


def _score_one(spec: dict, reply: str, message: dict, ctx: "ScoreContext") -> tuple[bool, str]:
    t = spec["type"]
    low = reply.casefold()
    if t == "exact":
        ok = _norm(reply) == _norm(str(spec["expected"]))
        return ok, "" if ok else f"期待: {spec['expected']!r}"
    if t == "contains":
        miss = [w for w in spec.get("all", []) if str(w).casefold() not in low]
        bad = [w for w in spec.get("none", []) if str(w).casefold() in low]
        return (not miss and not bad), "; ".join(filter(None, [f"含まれない: {miss}" if miss else "", f"含まれてはいけない: {bad}" if bad else ""]))
    if t == "any":
        ok = any(str(w).casefold() in low for w in spec["any"])
        return ok, "" if ok else f"どれも含まれない: {spec['any']}"
    if t == "regex":
        ok = re.search(spec["pattern"], reply, re.IGNORECASE if spec.get("ignore_case", True) else 0) is not None
        return ok, "" if ok else f"一致しない: {spec['pattern']}"
    if t == "label":
        labels = spec["labels"]
        pos = {lab: low.find(str(lab).casefold()) for lab in labels}
        found = {lab: i for lab, i in pos.items() if i >= 0}
        if not found:
            return False, f"ラベルが見つからない: {labels}"
        got = min(found, key=found.get)
        return got == spec["expected"], f"出力のラベル: {got}(期待: {spec['expected']})" if got != spec["expected"] else ""
    if t == "json":
        try:
            data = _extract_json(reply)
        except ValueError as e:
            return False, str(e)
        problems = []
        for k in spec.get("required", []):
            if not isinstance(data, dict) or k not in data:
                problems.append(f"キー '{k}' が無い")
        for k, ty in spec.get("types", {}).items():
            py = _JSON_TYPES.get(ty)
            v = data.get(k) if isinstance(data, dict) else None
            if py is None or k not in (data if isinstance(data, dict) else {}) or not isinstance(v, py) or (ty != "boolean" and isinstance(v, bool)):
                problems.append(f"'{k}' が {ty} でない")
        for k, v in spec.get("equals", {}).items():
            if not isinstance(data, dict) or data.get(k) != v:
                problems.append(f"'{k}' が {v!r} でない")
        return not problems, "; ".join(problems)
    if t == "mermaid":
        code = mermaid.extract_mermaid(reply)
        if code is None:
            return False, "```mermaid のブロックが無い"
        problems = mermaid.check_syntax(code)
        miss = [w for w in spec.get("all", []) if str(w).casefold() not in code.casefold()]
        if miss:
            problems.append(f"図に含まれない: {miss}")
        return not problems, "; ".join(problems)
    if t == "citation":
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", reply)}
        need = set(spec.get("cited", []))
        total = spec.get("sources")
        problems = []
        if need - cited:
            problems.append(f"引用されていない資料: {sorted(need - cited)}")
        if total and any(n < 1 or n > total for n in cited):
            problems.append(f"存在しない番号の引用: {sorted(n for n in cited if n < 1 or n > total)}")
        if not need and not cited:
            problems.append("引用の番号が無い")
        return not problems, "; ".join(problems)
    if t == "tool_call":
        calls = message.get("tool_calls") or []
        if not calls:
            return False, "ツール呼び出しが無い"
        fn = calls[0].get("function", {})
        if fn.get("name") != spec["name"]:
            return False, f"呼んだ関数: {fn.get('name')}(期待: {spec['name']})"
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            return False, "arguments が JSON でない"
        wrong = {k: args.get(k) for k, v in spec.get("args", {}).items() if args.get(k) != v}
        return not wrong, f"引数が違う: {wrong}" if wrong else ""
    if t == "python":
        if not ctx.allow_exec:
            raise _Skip("コードの実行は --allow-exec のときだけ")
        code = _extract_code(reply, "python")
        if code is None:
            return False, "```python のブロックが無い"
        return _run_python(code + "\n\n" + spec["tests"], timeout=float(spec.get("timeout_s", 10)))
    raise EvalError(f"未対応の採点: {t}")  # parse_case で弾いているので、通常は来ない


class _Skip(Exception):
    pass


def _run_python(source: str, timeout: float) -> tuple[bool, str]:
    """生成されたコードを、一時フォルダで実行する。**サンドボックスではない**(--allow-exec を付けたときだけ)。"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "solution.py"
        f.write_text(source, encoding="utf-8")
        try:
            r = subprocess.run([sys.executable, "-I", str(f)], cwd=d, capture_output=True, text=True, timeout=timeout,
                               env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
        except subprocess.TimeoutExpired:
            return False, f"{timeout:.0f} 秒を超えた"
    if r.returncode == 0:
        return True, ""
    tail = (r.stderr or r.stdout).strip().splitlines()
    return False, (tail[-1] if tail else f"終了コード {r.returncode}")[:200]


@dataclass
class ScoreContext:
    allow_exec: bool = False


def score_case(case: Case, message: dict, ctx: ScoreContext) -> tuple[bool, str]:
    reply = message.get("content") or ""
    details = []
    for spec in case.score:
        ok, detail = _score_one(spec, reply, message, ctx)
        if not ok:
            details.append(detail)
    return not details, "; ".join(details)


# ── 実行 ────────────────────────────────────────────────
@dataclass
class Attempt:
    case_id: str
    category: str
    attempt: int
    passed: bool | None          # None = 採点しなかった(skipped)
    detail: str = ""
    seconds: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    role: str = ""
    reply: str = ""
    error: str = ""
    skipped: str = ""


def _policy_attempt(h: Harness, case: Case) -> Attempt:
    spec = next(s for s in case.score if s["type"] == "policy")
    text = spec.get("text") or "\n".join(
        m["content"] for m in case.messages if isinstance(m.get("content"), str))
    v = h.policy.classify(text, spec.get("paths") or [], bool(spec.get("manual")))
    want = spec["expect"]  # "local-only" | "cloud-ok"
    ok = v.destination == want
    return Attempt(case.id, case.category, 1, ok, "" if ok else f"判定: {v.destination}(期待: {want}、理由: {list(v.reasons)})")


def _llm_attempt(h: Harness, case: Case, n: int, temperature: float, ctx: ScoreContext, role: str) -> Attempt:
    spec = h.cfg.model_for_role(role)
    body: dict = {
        "model": spec.alias, "messages": case.messages, "max_tokens": case.max_tokens,
        "temperature": case.temperature if case.temperature is not None else temperature,
    }
    if case.tools:
        body["tools"] = case.tools
    if case.tool_choice is not None:
        body["tool_choice"] = case.tool_choice
    if case.task:
        body["llh"] = {"task": case.task}
    t0 = time.monotonic()
    try:
        res = h.chat(body)
    except HarnessError as e:
        return Attempt(case.id, case.category, n, False, "", None, role=role, error=f"{e.code}: {e.message}")
    try:
        dt = time.monotonic() - t0
        resp = res.body or {}
        message = (resp.get("choices") or [{}])[0].get("message") or {}
        usage = resp.get("usage") or {}
    finally:
        res.close()
    try:
        ok, detail = score_case(case, message, ctx)
    except _Skip as s:
        return Attempt(case.id, case.category, n, None, "", dt, role=role, skipped=str(s))
    reply = (message.get("content") or "")
    if not reply and message.get("tool_calls"):
        reply = json.dumps(message["tool_calls"], ensure_ascii=False)
    if not reply:
        detail = (detail + "; " if detail else "") + "本文が空(thinking が出力を使い切った可能性)"
    return Attempt(case.id, case.category, n, ok, detail, dt, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                   role=role, reply=reply[:REPLY_KEEP_CHARS])


def run_eval(h: Harness, cases: list[Case], repeat: int = 3, temperature: float = 0.0, allow_exec: bool = False,
             default_role: str = "main", on_attempt: Callable[[Attempt], None] | None = None) -> list[Attempt]:
    """ロールごとにまとめて解く(モデルの入れ替えを最小にする。設計書 A11)。"""
    ctx = ScoreContext(allow_exec=allow_exec)
    out: list[Attempt] = []
    by_role: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        by_role["" if c.is_policy else (c.role or default_role)].append(c)
    for role, group in by_role.items():
        if role == "":
            for c in group:
                a = _policy_attempt(h, c)
                out.append(a)
                on_attempt and on_attempt(a)
            continue
        if h.cfg.on_model_mismatch == "load":
            h.ensure_model_loaded(h.cfg.model_for_role(role))
        for c in group:
            for n in range(1, repeat + 1):
                a = _llm_attempt(h, c, n, temperature, ctx, role)
                out.append(a)
                on_attempt and on_attempt(a)
                if a.skipped:
                    break  # 何度やっても同じ
    return out


# ── 集計・比較 ───────────────────────────────────────────
@dataclass
class CaseStat:
    case_id: str
    category: str
    passes: int
    attempts: int

    @property
    def rate(self) -> float:
        return self.passes / self.attempts if self.attempts else 0.0


def case_stats(attempts: list[Attempt]) -> list[CaseStat]:
    d: dict[str, list[Attempt]] = defaultdict(list)
    for a in attempts:
        if a.passed is not None:
            d[a.case_id].append(a)
    return [CaseStat(cid, rows[0].category, sum(1 for a in rows if a.passed), len(rows)) for cid, rows in d.items()]


def category_rates(stats: list[CaseStat]) -> dict[str, tuple[float, int]]:
    """区分ごとの (合格率, 問題数)。問題ごとの合格率の平均。"""
    d: dict[str, list[float]] = defaultdict(list)
    for s in stats:
        d[s.category].append(s.rate)
    return {c: (sum(v) / len(v), len(v)) for c, v in d.items()}


def speed_summary(attempts: list[Attempt]) -> dict:
    rows = [a for a in attempts if a.seconds and a.completion_tokens]
    if not rows:
        return {}
    secs = sorted(a.seconds for a in rows)
    return {
        "attempts": len(rows),
        "median_s": secs[len(secs) // 2],
        "mean_tok_s": sum(a.completion_tokens / a.seconds for a in rows) / len(rows),  # 待ち時間込み
        "completion_tokens": sum(a.completion_tokens for a in rows),
    }


def load_baseline(path: str | Path) -> dict[str, float]:
    """基準(Claude など)の問題ごとの合格率。{id: 0〜1} の JSON か、`llh eval` の結果ファイル(.jsonl)。"""
    p = Path(path)
    if not p.exists():
        raise EvalError(f"基準のファイルが見つかりません: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".jsonl":
        attempts = [Attempt(**{k: v for k, v in r.items() if k in Attempt.__dataclass_fields__})
                    for r in (json.loads(x) for x in text.splitlines() if x.strip()) if r.get("kind") == "attempt"]
        return {s.case_id: s.rate for s in case_stats(attempts)}
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise EvalError(f"基準の JSON を読めません: {e}") from e
    if not isinstance(raw, dict) or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in raw.values()):
        raise EvalError("基準は {問題の id: 合格率(0〜1)} の JSON にしてください")
    return {str(k): float(v) for k, v in raw.items()}


@dataclass
class Comparison:
    category: str
    cases: int
    local: float
    baseline: float
    verdict: str   # "ローカルに回せる" | "Claude に残す" | "同等(差が小さい)"


def compare(stats: list[CaseStat], baseline: dict[str, float]) -> list[Comparison]:
    """基準にも載っている問題だけで、区分ごとに比べる。"""
    d: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s in stats:
        if s.case_id in baseline:
            d[s.category].append((s.rate, baseline[s.case_id]))
    out = []
    for cat, pairs in sorted(d.items()):
        local = sum(p[0] for p in pairs) / len(pairs)
        base = sum(p[1] for p in pairs) / len(pairs)
        if base == 0:
            verdict = "基準も解けていない(問題か採点を見直す)"
        elif local >= base * PASS_RATIO_FOR_LOCAL:
            verdict = "ローカルに回せる" + ("(基準以上)" if local >= base else "")
        elif base - local <= NOISE_POINTS and len(pairs) < 15:
            verdict = "同等(差が小さい。問題が少ないと誤差)"
        else:
            verdict = "Claude に残す"
        out.append(Comparison(cat, len(pairs), local, base, verdict))
    return out


# ── 結果の保存 ───────────────────────────────────────────
def write_results(path: Path, h: Harness, attempts: list[Attempt], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = {"kind": "meta", "llh_version": __version__, "policy_sha256": h.policy.source_hash[:16], **meta,
            "models": {r: s.name for r, s in h.cfg.models.items()}}
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(head, ensure_ascii=False) + "\n")
        for a in attempts:
            f.write(json.dumps({"kind": "attempt", **asdict(a)}, ensure_ascii=False) + "\n")


def cases_fingerprint(cases: list[Case]) -> str:
    """問題の内容のハッシュ(問題を直したあとの結果を、前の結果と混ぜて比べないための印)。"""
    blob = json.dumps([[c.id, c.messages, c.score] for c in cases], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
