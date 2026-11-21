"""機密度の判定(設計書 軸 1)。

どれか 1 つでも該当すれば「外に出さない」(sensitive)。判定の結果には、**理由の名前だけ**を入れ、
一致した文字列そのものは、戻り値にもログにも残さない。
"""
from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# 組み込みの検出器。名前は policy.toml の content.detectors で選ぶ。
DETECTORS: dict[str, re.Pattern[str]] = {
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{15,}"),
    "aws_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "google_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    "private_key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY(?: BLOCK)?-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    "secret_assignment": re.compile(
        r"(?i)(?:api[_\-]?key|secret|token|passwd|password|access[_\-]?key)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+=.]{12,}"
    ),
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+\b"),
    "personal_path": re.compile(r"(?i)(?:\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s\"']+|/home/[^/\s\"']+|/Users/[^/\s\"']+)"),
}


class PolicyError(ValueError):
    pass


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """`**` は階層をまたぎ、`*` は 1 階層。大文字小文字は区別しない。"""
    p = pattern.replace("\\", "/")
    out: list[str] = []
    i = 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def normalize_path(path: str) -> str:
    p = path.replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    # `.` と `..` を畳む(`allow/../deny` で許可リストを抜けられないように)
    drive = ""
    if re.match(r"^[A-Za-z]:", p):
        drive, p = p[:2], p[2:]
    absolute = p.startswith("/")
    parts: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return drive + ("/" if absolute else "") + "/".join(parts)


@dataclass(frozen=True)
class Verdict:
    sensitive: bool
    reasons: tuple[str, ...] = ()

    @property
    def destination(self) -> str:
        return "local-only" if self.sensitive else "cloud-ok"

    def to_dict(self) -> dict:
        return {"sensitive": self.sensitive, "reasons": list(self.reasons), "destination": self.destination}


@dataclass
class Policy:
    allow: list[re.Pattern[str]] = field(default_factory=list)
    deny: list[re.Pattern[str]] = field(default_factory=list)
    detectors: dict[str, re.Pattern[str]] = field(default_factory=dict)
    decoys: list[str] = field(default_factory=list)
    manual_header: str = "X-LLH-Local-Only"
    source_hash: str = ""

    # ── パスの規則 ────────────────────────────────────────────
    def check_path(self, path: str) -> tuple[str, ...]:
        norm = normalize_path(path)
        if any(r.match(norm) for r in self.deny):
            return ("path:deny",)
        if not any(r.match(norm) for r in self.allow):
            return ("path:not-in-allowlist",)  # 既定は拒否
        return ()

    # ── 内容の検査 ────────────────────────────────────────────
    def check_text(self, text: str) -> tuple[str, ...]:
        reasons: list[str] = []
        if any(d and d in text for d in self.decoys):
            reasons.append("content:decoy")
        for name, rx in self.detectors.items():
            if rx.search(text):
                reasons.append(f"content:{name}")
        return tuple(dict.fromkeys(reasons))

    # ── まとめ ───────────────────────────────────────────────
    def classify(self, text: str = "", paths: list[str] | None = None, manual: bool = False) -> Verdict:
        reasons: list[str] = []
        if manual:
            reasons.append("manual")
        for p in paths or []:
            reasons.extend(self.check_path(p))
        reasons.extend(self.check_text(text))
        reasons = list(dict.fromkeys(reasons))
        return Verdict(bool(reasons), tuple(reasons))

    def check_egress(self, text: str) -> Verdict:
        """クラウドへ送る直前の出口のフィルター。内容の検査だけをかける(パスは入口で済んでいる)。

        現在の窓口にはクラウドへの経路が無いので、使われない。経路を足すときは、必ずここを通す。
        """
        return self.classify(text=text)


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    data = path.read_bytes()
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise PolicyError(f"{path} の書き方が不正です: {e}") from e

    paths = raw.get("paths", {})
    content = raw.get("content", {})

    detectors: dict[str, re.Pattern[str]] = {}
    for name in content.get("detectors", list(DETECTORS)):
        if name not in DETECTORS:
            raise PolicyError(f"未知の検出器です: {name}(使えるのは {', '.join(DETECTORS)})")
        detectors[name] = DETECTORS[name]
    for extra in content.get("extra", []):
        try:
            detectors[f"extra:{extra['name']}"] = re.compile(extra["regex"])
        except (KeyError, re.error) as e:
            raise PolicyError(f"content.extra の指定が不正です: {e}") from e

    return Policy(
        allow=[_glob_to_regex(p) for p in paths.get("allow", [])],
        deny=[_glob_to_regex(p) for p in paths.get("deny", [])],
        detectors=detectors,
        decoys=list(content.get("decoys", [])),
        manual_header=raw.get("manual", {}).get("header", "X-LLH-Local-Only"),
        source_hash=hashlib.sha256(data).hexdigest(),
    )
