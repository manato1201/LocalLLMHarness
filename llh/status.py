"""状態の確認(設計書 §9 S5・S13): 裏側の死活・読み込み済みモデル・VRAM・待ち受けの範囲。"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .backend import Backend, BackendError
from .config import LOOPBACK_HOSTS, Config


@dataclass
class Gpu:
    name: str
    used_mib: int
    total_mib: int

    @property
    def free_mib(self) -> int:
        return self.total_mib - self.used_mib


def gpu_status() -> Gpu | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        name, used, total = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        return Gpu(name, int(used), int(total))
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def collect(cfg: Config) -> dict:
    info: dict = {"listen_host": cfg.host, "listen_loopback_only": cfg.host in LOOPBACK_HOSTS,
                  "local_only": cfg.local_only, "backend_url": cfg.backend_base_url,
                  "backend": "down", "loaded": [], "available": [], "configured": {}}
    try:
        models = Backend(cfg).list_models()
        info["backend"] = "up"
        info["loaded"] = [str(m.get("id")) for m in models if m.get("loaded")]
        info["available"] = [str(m.get("id")) for m in models]
    except BackendError as e:
        info["backend_error"] = e.message
    for role, spec in cfg.models.items():
        info["configured"][role] = {
            "alias": spec.alias, "name": spec.name,
            "known_to_backend": any(spec.matches(i) for i in info["available"]) if info["available"] else None,
            "loaded": any(spec.matches(i) for i in info["loaded"]) if info["available"] else None,
        }
    gpu = gpu_status()
    info["gpu"] = None if gpu is None else {"name": gpu.name, "used_mib": gpu.used_mib, "total_mib": gpu.total_mib, "free_mib": gpu.free_mib}
    return info


def startup_checks(cfg: Config) -> list[str]:
    """`llh serve` の起動時の確認。問題があれば、直し方つきの警告の一覧(空なら問題なし)。キーの値は出さない。"""
    warns: list[str] = []
    env = cfg.backend_api_key_env
    if not cfg.backend_api_key:
        warns.append(
            f"環境変数 {env}(Unsloth の API キー)が未設定です。`setx` で保存したあとは、新しく開いた PowerShell で起動し直してください"
            "(開いていたウィンドウには効きません)。"
        )
    try:
        Backend(cfg).list_models()
    except BackendError as e:
        if e.status in (401, 403):
            warns.append(
                f"Unsloth({cfg.backend_base_url})が認証を拒否しました。{env} のキーが未設定か、違います。"
                "Settings > API で作ったキーを確かめてください。"
            )
        else:
            warns.append(f"Unsloth({cfg.backend_base_url})に届きません: {e.message}")
    return warns
