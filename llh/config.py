"""設定(config.toml)の読み込み。秘密は環境変数からだけ読み、ファイルには置かない。"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    role: str
    alias: str
    name: str
    purpose: str = ""
    tasks: tuple[str, ...] = ()
    model_path: str = ""     # POST /v1/load に渡す識別子。空なら name の「:」より前
    gguf_variant: str = ""   # 例 Q4_K_M。空なら name の「:」より後
    enable_thinking: bool | None = None  # 考える(thinking)モードの既定。None=モデルまかせ。リクエストで指定があればそちらが優先

    def _split(self) -> tuple[str, str]:
        # `C:\models\x.gguf` のようなドライブの「:」は、変種の区切りとみなさない
        if ":" in self.name and not re.match(r"^[A-Za-z]:[\\/]", self.name):
            path, _, variant = self.name.rpartition(":")
            return path, variant
        return self.name, ""

    @property
    def load_path(self) -> str:
        return self.model_path or self._split()[0]

    @property
    def load_variant(self) -> str:
        return self.gguf_variant or self._split()[1]

    def matches(self, backend_id: object) -> bool:
        """裏側の `/v1/models` の id が、このモデルを指すか。id が変種つき・なしのどちらでも一致とみなす。"""
        n = str(backend_id).lower()
        cands = {self.name.lower(), self.load_path.lower()}
        if self.load_variant:
            cands.add(f"{self.load_path}:{self.load_variant}".lower())
        return n in cands


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8800
    token_env: str = "LLH_TOKEN"
    backend_base_url: str = "http://127.0.0.1:8888/v1"
    backend_api_key_env: str = "LLH_BACKEND_API_KEY"
    backend_timeout_s: float = 300.0
    on_model_mismatch: str = "warn"
    load_timeout_s: float = 900.0
    disable_server_tools: bool = True
    models: dict[str, ModelSpec] = field(default_factory=dict)
    max_context_tokens: int = 8192
    max_output_tokens: int = 2048
    default_max_tokens: int = 1024
    max_body_bytes: int = 16 * 1024 * 1024
    queue_timeout_s: float = 120.0
    policy_path: Path = ROOT / "policy.toml"
    audit_path: Path = ROOT / "logs" / "audit.jsonl"
    escalation_path: Path = ROOT / "logs" / "escalations.jsonl"   # 検証に通らなかった依頼の記録(中身は残さない)
    save_verified: bool = False                                    # 検証に通った生成を、学習用に保存する(Phase 6。中身を含む。既定は保存しない)
    verified_path: Path = ROOT / "logs" / "verified.jsonl"
    local_only: bool = True
    mermaid_require_init_line: bool = False

    @property
    def backend_api_key(self) -> str:
        return os.environ.get(self.backend_api_key_env, "")

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "")

    def model_for_role(self, role: str) -> ModelSpec:
        try:
            return self.models[role]
        except KeyError:
            raise ConfigError(f"ロール '{role}' のモデルが config にありません") from None


def _default_models() -> dict[str, ModelSpec]:
    # config.example.toml と同じ。仮の名前なので、実機の id(`llh models`)に直す。
    return {
        "main": ModelSpec("main", "llh-main", "unsloth/Qwen3.5-9B-GGUF:Q4_K_M", tasks=("code", "json", "mermaid", "diff", "log", "rag")),
        "vision": ModelSpec("vision", "llh-vision", "unsloth/Qwen2.5-VL-7B-Instruct-GGUF:Q4_K_M", tasks=("vision", "screen")),
        "writer": ModelSpec("writer", "llh-writer", "unsloth/gemma-4-9b-it-GGUF:Q4_K_M", tasks=("doc", "summary", "note")),
    }


def _resolve(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else base / p


def load_config(path: str | Path | None = None) -> Config:
    """config.toml を読む。無ければ config.example.toml、それも無ければ組み込みの既定値。"""
    if path is None:
        for candidate in (ROOT / "config.toml", ROOT / "config.example.toml"):
            if candidate.exists():
                path = candidate
                break
    if path is None:
        return Config(models=_default_models(), policy_path=_policy_default())
    path = Path(path)
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} の書き方が不正です: {e}") from e
    base = path.parent

    server = raw.get("server", {})
    backend = raw.get("backend", {})
    limits = raw.get("limits", {})
    paths = raw.get("paths", {})
    mode = raw.get("mode", {})
    mermaid = raw.get("mermaid", {})

    models: dict[str, ModelSpec] = {}
    for role, spec in raw.get("models", {}).items():
        if "name" not in spec:
            raise ConfigError(f"[models.{role}] に name がありません")
        models[role] = ModelSpec(
            role=role,
            alias=spec.get("alias", f"llh-{role}"),
            name=spec["name"],
            purpose=spec.get("purpose", ""),
            tasks=tuple(spec.get("tasks", ())),
            model_path=spec.get("model_path", ""),
            gguf_variant=spec.get("gguf_variant", ""),
            enable_thinking=_opt_bool(spec.get("enable_thinking"), f"[models.{role}].enable_thinking"),
        )
    if "main" not in models:
        raise ConfigError("[models.main] が必要です(既定の行き先)")

    on_mismatch = backend.get("on_model_mismatch", "warn")
    if on_mismatch not in ("off", "warn", "error", "load"):
        raise ConfigError("backend.on_model_mismatch は off / warn / error / load のどれか")

    host = server.get("host", "127.0.0.1")
    if host not in LOOPBACK_HOSTS:
        raise ConfigError(
            f"server.host は {', '.join(LOOPBACK_HOSTS)} だけ使えます(設計書 S13。'{host}' では、他のPCから届いてしまう)"
        )

    policy_path = _resolve(base, paths.get("policy", "policy.toml"))
    if not policy_path.exists():
        policy_path = _policy_default()

    return Config(
        host=host,
        port=int(server.get("port", 8800)),
        token_env=server.get("token_env", "LLH_TOKEN"),
        backend_base_url=backend.get("base_url", "http://127.0.0.1:8888/v1").rstrip("/"),
        backend_api_key_env=backend.get("api_key_env", "LLH_BACKEND_API_KEY"),
        backend_timeout_s=float(backend.get("timeout_s", 300)),
        on_model_mismatch=on_mismatch,
        load_timeout_s=float(backend.get("load_timeout_s", 900)),
        disable_server_tools=bool(backend.get("disable_server_tools", True)),
        models=models,
        max_context_tokens=int(limits.get("max_context_tokens", 8192)),
        max_output_tokens=int(limits.get("max_output_tokens", 2048)),
        default_max_tokens=int(limits.get("default_max_tokens", 1024)),
        max_body_bytes=int(limits.get("max_body_bytes", 16 * 1024 * 1024)),
        queue_timeout_s=float(limits.get("queue_timeout_s", 120)),
        policy_path=policy_path,
        audit_path=_resolve(base, paths.get("audit_log", "logs/audit.jsonl")),
        escalation_path=_resolve(base, paths.get("escalation_log", "logs/escalations.jsonl")),
        save_verified=bool(raw.get("training", {}).get("save_verified", False)),
        verified_path=_resolve(base, raw.get("training", {}).get("verified_log", "logs/verified.jsonl")),
        local_only=bool(mode.get("local_only", True)),
        mermaid_require_init_line=bool(mermaid.get("require_init_line", False)),
    )


def _opt_bool(value: object, where: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ConfigError(f"{where} は true / false のどちらかです")


def _policy_default() -> Path:
    p = ROOT / "policy.toml"
    return p if p.exists() else ROOT / "policy.example.toml"
