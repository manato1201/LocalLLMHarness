"""Unsloth Desktop が起動したら、窓口(llh serve)と、設定した他のサービス(例: ChapterLens のエンジン)を自動で起動する。

使い方(常駐。ウィンドウは出さない):
    uv run llh autostart            # 見張りを始める(Ctrl+C で、起動したものを止めて終わる)
    uv run llh autostart install    # ログオン時に、見張りを自動で始める(Windows のスタートアップに登録)
    uv run llh autostart uninstall  # 上の登録を外す

仕組み: Unsloth の API のポート(config.toml の backend.base_url)に、定期的につないでみる。つながったら「Unsloth が起動した」と見なす。
- 起動するのは、窓口(必ず最初)と、`[[autostart.service]]` に書いたサービスだけ(shell は通さない。コマンドは、config.toml に書いた配列)。
- すでに待ち受けているポートのサービスは、起動もしないし、止めもしない(手で起動したものには触らない)。
- Unsloth が見えなくなって 2 回続いたら(読み込み中の一瞬の欠けを無視するため)、**自分で起動したものだけ**を止める(stop_with_unsloth)。
- 起動したサービスが落ちたら、3 回まで起動し直す。
- load_role を書くと、起動のあとにそのロールのモデルを読み込む(既定は、読み込まない。VRAM を使うため)。
標準ライブラリだけ。起動先・待ち受けは、この PC の 127.0.0.1 だけ(Unsloth が loopback 以外なら、設定エラー)。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import LOOPBACK_HOSTS, ROOT, ConfigError, load_config

DEFAULT_POLL_S = 5.0
DEFAULT_LOCK_PORT = 8897
MAX_RESTARTS = 3
MISSES_TO_STOP = 2          # Unsloth が見えない回数が、これに達したら止める
SERVICE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
RESERVED = "llh"            # 窓口の名前
STARTUP_FILE = "llh-autostart.vbs"


@dataclass(frozen=True)
class Service:
    name: str
    command: tuple[str, ...]
    cwd: Path
    port: int                                    # 起動したかを見る、127.0.0.1 のポート
    pre: tuple[tuple[str, ...], ...] = ()        # 起動の前に、1 回ずつ実行するコマンド(失敗しても、起動は続ける)
    wait_s: float = 60.0                         # ポートが開くのを待つ上限(秒)


@dataclass(frozen=True)
class Plan:
    unsloth_host: str
    unsloth_port: int
    services: tuple[Service, ...]
    poll_s: float = DEFAULT_POLL_S
    stop_with_unsloth: bool = True
    load_role: str = ""
    log_dir: Path = Path("logs")
    load_command: tuple[str, ...] = ()
    load_timeout_s: float = 900.0
    lock_port: int = DEFAULT_LOCK_PORT           # 二重起動を防ぐための、127.0.0.1 のポート(0 なら、防がない)


# ── 設定 ─────────────────────────────────────────────────────
def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise ConfigError(f"{where} は、文字列の配列(空でない)です")
    return tuple(value)


def load_plan(config_path: str | Path | None = None) -> Plan:
    """config.toml から、見張りの計画を作る。書き方が不正なら ConfigError。"""
    cfg = load_config(config_path)
    path: Path | None = Path(config_path) if config_path is not None else next(
        (c for c in (ROOT / "config.toml", ROOT / "config.example.toml") if c.exists()), None
    )
    raw: dict = {}
    if path is not None:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    base = path.parent if path is not None else ROOT
    section = raw.get("autostart", {})
    cfg_args = ("--config", str(config_path)) if config_path is not None else ()   # 引数で渡されたときだけ、子にも渡す

    parts = urlsplit(cfg.backend_base_url)
    if (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise ConfigError("autostart は、Unsloth が この PC(127.0.0.1)にあるときだけ使えます(backend.base_url を確認)")
    unsloth_port = parts.port or (443 if parts.scheme == "https" else 80)

    poll_s = float(section.get("poll_s", DEFAULT_POLL_S))
    if poll_s < 1:
        raise ConfigError("autostart.poll_s は 1 秒以上です")

    services = [Service(
        name=RESERVED, command=(sys.executable, "-m", "llh", *cfg_args, "serve"), cwd=base if path is not None else ROOT,
        port=cfg.port, wait_s=30.0,
    )]
    for i, spec in enumerate(section.get("service", [])):
        where = f"autostart.service[{i}]"
        name = spec.get("name")
        if not isinstance(name, str) or not SERVICE_NAME.match(name) or name == RESERVED:
            raise ConfigError(f"{where}.name は、英数字・- ・_ の 40 文字以内で、'{RESERVED}' 以外です")
        if any(s.name == name for s in services):
            raise ConfigError(f"{where}.name '{name}' が重複しています")
        port = spec.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ConfigError(f"{where}.port は 1〜65535 の整数です(起動したかを見るポート)")
        pre_raw = spec.get("pre", [])
        if not isinstance(pre_raw, list):
            raise ConfigError(f"{where}.pre は、コマンド(文字列の配列)の配列です")
        wait_s = float(spec.get("wait_s", 60))
        if wait_s <= 0:
            raise ConfigError(f"{where}.wait_s は正の数です")
        cwd = Path(spec["cwd"]) if "cwd" in spec else Path(".")
        services.append(Service(
            name=name, command=_strings(spec.get("command"), f"{where}.command"),
            cwd=cwd if cwd.is_absolute() else base / cwd, port=port,
            pre=tuple(_strings(p, f"{where}.pre の各要素") for p in pre_raw), wait_s=wait_s,
        ))

    load_role = str(section.get("load_role", ""))
    if load_role and load_role not in cfg.models:
        raise ConfigError(f"autostart.load_role '{load_role}' は config のロールにありません({', '.join(cfg.models)})")
    return Plan(
        unsloth_host=parts.hostname or "127.0.0.1", unsloth_port=unsloth_port, services=tuple(services), poll_s=poll_s,
        stop_with_unsloth=bool(section.get("stop_with_unsloth", True)), load_role=load_role,
        log_dir=base / "logs",
        load_command=(sys.executable, "-m", "llh", *cfg_args, "load", "--role", load_role) if load_role else (),
        load_timeout_s=cfg.load_timeout_s, lock_port=int(section.get("lock_port", DEFAULT_LOCK_PORT)),
    )


# ── 実際の道具(テストでは、偽物に差し替える) ───────────────────────
def tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _flags() -> int:
    return (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0


def spawn_process(svc: Service, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        return subprocess.Popen(list(svc.command), cwd=svc.cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=_flags())


def kill_process(handle: subprocess.Popen) -> None:
    """プロセスと、その子(uv が起こす Python など)を止める。"""
    if handle.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(handle.pid), "/T", "/F"], capture_output=True, creationflags=_flags())
    else:
        handle.terminate()
    try:
        handle.wait(timeout=5)
    except subprocess.TimeoutExpired:
        handle.kill()


def run_command(command: tuple[str, ...], cwd: Path, log_path: Path, timeout_s: float) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "ab") as log:
            return subprocess.run(list(command), cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=timeout_s, creationflags=_flags()).returncode
    except subprocess.TimeoutExpired:
        return -1


def acquire_lock(port: int) -> socket.socket | None:
    """二重起動の防止: 127.0.0.1 のポートを握る。すでに握られていれば None。プロセスが終われば、自動で外れる。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
    except OSError:
        s.close()
        return None
    return s


# ── 見張り ───────────────────────────────────────────────────
class Supervisor:
    def __init__(self, plan: Plan, *, probe=tcp_open, spawn=spawn_process, kill=kill_process, run=run_command,
                 sleep=time.sleep, clock=time.monotonic, log=print) -> None:
        self.plan = plan
        self._probe, self._spawn, self._kill, self._run = probe, spawn, kill, run
        self._sleep, self._clock, self._log = sleep, clock, log
        self.owned: dict[str, object] = {}
        self.active = False
        self._misses = 0
        self._restarts: dict[str, int] = {}
        self._gave_up: set[str] = set()
        self._loaded = False

    def _log_path(self, name: str) -> Path:
        return self.plan.log_dir / f"autostart-{name}.log"

    def tick(self) -> None:
        up = self._probe(self.plan.unsloth_host, self.plan.unsloth_port)
        if up:
            self._misses = 0
            if not self.active:
                self.active = True
                self._restarts.clear()
                self._gave_up.clear()
                self._loaded = False
                self._log("Unsloth を検出しました。窓口と、設定したサービスを起動します。")
                self._start_all()
            else:
                self._watch()
        elif self.active:
            self._misses += 1
            if self._misses >= MISSES_TO_STOP:
                self.active = False
                self._log("Unsloth が見えなくなりました。" + ("起動したものを止めます。" if self.plan.stop_with_unsloth else "起動したものは、そのままにします。"))
                if self.plan.stop_with_unsloth:
                    self._stop_all()

    def shutdown(self) -> None:
        self._stop_all()

    def _start_all(self) -> None:
        for svc in self.plan.services:
            self._start_service(svc)
        self._maybe_load()

    def _start_service(self, svc: Service) -> bool:
        if self._probe("127.0.0.1", svc.port):
            self._log(f"[{svc.name}] ポート {svc.port} は、すでに待ち受けています(起動済み。自分では止めません)。")
            return True
        log_path = self._log_path(svc.name)
        for cmd in svc.pre:
            try:
                code = self._run(cmd, svc.cwd, log_path, 60.0)
            except OSError as e:
                code = -2
                self._log(f"[{svc.name}] 前処理を実行できません: {e.__class__.__name__}")
            if code != 0:
                self._log(f"[{svc.name}] 前処理が失敗しました(終了コード {code})。起動は続けます。ログ: {log_path}")
        try:
            handle = self._spawn(svc, log_path)
        except OSError as e:
            self._log(f"[{svc.name}] 起動できません({e.__class__.__name__})。cwd とコマンドを確認してください: {svc.cwd}")
            return False
        self.owned[svc.name] = handle
        start = self._clock()
        while self._clock() - start < svc.wait_s:
            if self._probe("127.0.0.1", svc.port):
                self._log(f"[{svc.name}] 起動しました(ポート {svc.port})。")
                return True
            if handle.poll() is not None:
                break
            self._sleep(1.0)
        self._log(f"[{svc.name}] ポート {svc.port} で待ち受けませんでした。止めます。ログ: {log_path}")
        self._kill(handle)
        self.owned.pop(svc.name, None)
        return False

    def _maybe_load(self) -> None:
        if not self.plan.load_role or self._loaded:
            return
        self._loaded = True
        harness = self.plan.services[0]
        if not self._probe("127.0.0.1", harness.port):
            self._log(f"モデルの読み込みを見送ります(窓口が待ち受けていません)。")
            return
        self._log(f"モデル [{self.plan.load_role}] を読み込みます(数十秒〜数分かかります)。")
        try:
            code = self._run(self.plan.load_command, harness.cwd, self._log_path("load"), self.plan.load_timeout_s)
        except OSError as e:
            code = -2
            self._log(f"読み込みを実行できません: {e.__class__.__name__}")
        self._log("モデルを読み込みました。" if code == 0 else f"モデルの読み込みに失敗しました(終了コード {code})。ログ: {self._log_path('load')}")

    def _watch(self) -> None:
        for svc in self.plan.services:
            handle = self.owned.get(svc.name)
            if handle is None or handle.poll() is None:
                continue
            n = self._restarts.get(svc.name, 0)
            if n >= MAX_RESTARTS:
                if svc.name not in self._gave_up:
                    self._gave_up.add(svc.name)
                    self._log(f"[{svc.name}] が {MAX_RESTARTS} 回落ちたので、起動し直すのを諦めました。ログ: {self._log_path(svc.name)}")
                continue
            self._restarts[svc.name] = n + 1
            self._log(f"[{svc.name}] が止まっていました。起動し直します({n + 1}/{MAX_RESTARTS})。")
            self.owned.pop(svc.name, None)
            self._start_service(svc)

    def _stop_all(self) -> None:
        for name, handle in list(self.owned.items()):
            self._kill(handle)
            self._log(f"[{name}] を止めました。")
        self.owned.clear()


# ── ログオン時の自動起動(Windows のスタートアップに、窓を出さない .vbs を置く) ──
def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        raise RuntimeError("スタートアップへの登録は Windows 専用です(他の OS では、systemd や launchd などに `llh autostart` を登録してください)")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def install_startup(*, startup_dir: Path | None = None, python: str | None = None, repo: Path | None = None, config: Path | None = None) -> Path:
    folder = startup_dir or _startup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    exe = python or sys.executable
    cfg_part = f' --config ""{config}""' if config is not None else ""
    text = (
        "' LocalLLMHarness: ログオン時に、Unsloth の起動を見張る(llh autostart)。外すときは llh autostart uninstall\r\n"
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{repo or ROOT}"\r\n'
        f'sh.Run """{exe}"" -m llh{cfg_part} autostart", 0, False\r\n'
    )
    path = folder / STARTUP_FILE
    path.write_text(text, encoding="utf-16")   # BOM つきの UTF-16(日本語のパスでも、WSH が読める)
    return path


def uninstall_startup(*, startup_dir: Path | None = None) -> bool:
    path = (startup_dir or _startup_dir()) / STARTUP_FILE
    if not path.exists():
        return False
    path.unlink()
    return True
