"""Unsloth Desktop の起動を見て、窓口と他のサービスを自動で起動する(llh autostart)。本物のプロセスは起動せず、偽の道具で確かめる。"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from llh.autostart import (
    Plan,
    Service,
    Supervisor,
    acquire_lock,
    install_startup,
    load_plan,
    uninstall_startup,
)
from llh.config import ConfigError


# ── 偽の道具 ─────────────────────────────────────────────────
class World:
    """開いているポート・起動したプロセス・実行したコマンドを、記録する偽の環境。"""

    def __init__(self) -> None:
        self.open: set[int] = set()
        self.spawned: list[tuple[str, tuple[str, ...]]] = []
        self.killed: list[str] = []
        self.ran: list[tuple[str, ...]] = []
        self.logs: list[str] = []
        self.now = 0.0
        self.opens_on_spawn: dict[str, int] = {}   # サービス名 → 起動したら開くポート
        self.never_opens: set[str] = set()         # 起動しても、ポートを開かないサービス
        self.run_codes: dict[tuple[str, ...], int] = {}
        self.dead: set[str] = set()

    def probe(self, host: str, port: int) -> bool:
        return port in self.open

    def spawn(self, svc: Service, log_path: Path):
        self.spawned.append((svc.name, svc.command))
        if svc.name not in self.never_opens:
            self.open.add(svc.port)
        return FakeHandle(self, svc.name, svc.port)

    def kill(self, handle) -> None:
        self.killed.append(handle.name)
        self.open.discard(handle.port)

    def run(self, command: tuple[str, ...], cwd: Path, log_path: Path, timeout_s: float) -> int:
        self.ran.append(command)
        return self.run_codes.get(command, 0)

    def sleep(self, s: float) -> None:
        self.now += s

    def clock(self) -> float:
        return self.now

    def log(self, msg: str) -> None:
        self.logs.append(msg)


class FakeHandle:
    def __init__(self, world: World, name: str, port: int) -> None:
        self.world, self.name, self.port = world, name, port

    def poll(self):
        return 1 if self.name in self.world.dead else None


SERVE = Service(name="llh", command=("py", "-m", "llh", "serve"), cwd=Path("."), port=8800)
ENGINE = Service(
    name="chapterlens", command=("uv", "run", "chapterlens", "serve"), cwd=Path("."), port=8787,
    pre=(("uv", "run", "chapterlens", "admin", "llm", "on"),), wait_s=10.0,
)
UNSLOTH = 8888


def make(world: World, *, services=(SERVE, ENGINE), stop_with_unsloth=True, load_role="", log_dir=Path("logs")) -> Supervisor:
    plan = Plan(
        unsloth_host="127.0.0.1", unsloth_port=UNSLOTH, services=tuple(services), poll_s=5.0,
        stop_with_unsloth=stop_with_unsloth, load_role=load_role, log_dir=log_dir, load_command=("py", "-m", "llh", "load", "--role", load_role),
        lock_port=0,
    )
    return Supervisor(plan, probe=world.probe, spawn=world.spawn, kill=world.kill, run=world.run, sleep=world.sleep, clock=world.clock, log=world.log)


# ── 起動・停止の流れ ─────────────────────────────────────────
def test_nothing_starts_while_unsloth_is_down():
    w = World()
    make(w).tick()
    assert w.spawned == [] and w.ran == []


def test_starts_services_in_order_when_unsloth_is_up():
    w = World()
    s = make(w)
    w.open.add(UNSLOTH)
    s.tick()
    assert [n for n, _ in w.spawned] == ["llh", "chapterlens"]
    assert w.ran == [("uv", "run", "chapterlens", "admin", "llm", "on")]   # 前処理は、そのサービスの起動の前に 1 回
    s.tick(); s.tick()
    assert len(w.spawned) == 2, "起動済みなら、もう一度起動しない"


def test_service_already_listening_is_not_started_nor_stopped():
    w = World()
    w.open.update({UNSLOTH, 8787})          # エンジンは、手で起動済み
    s = make(w)
    s.tick()
    assert [n for n, _ in w.spawned] == ["llh"]
    w.open.discard(UNSLOTH)
    s.tick(); s.tick()
    assert w.killed == ["llh"], "自分で起動したものだけを止める"


def test_stops_owned_services_only_after_unsloth_stays_down_for_two_polls():
    w = World()
    s = make(w)
    w.open.add(UNSLOTH)
    s.tick()
    w.open.discard(UNSLOTH)
    s.tick()
    assert w.killed == [], "1 回見えないだけでは止めない(読み込み中の一瞬の欠け)"
    s.tick()
    assert sorted(w.killed) == ["chapterlens", "llh"]


def test_one_missed_poll_does_not_count_when_unsloth_comes_back():
    w = World()
    s = make(w)
    w.open.add(UNSLOTH); s.tick()
    w.open.discard(UNSLOTH); s.tick()
    w.open.add(UNSLOTH); s.tick(); w.open.discard(UNSLOTH); s.tick()
    assert w.killed == []


def test_restarts_everything_when_unsloth_is_opened_again():
    w = World()
    s = make(w)
    w.open.add(UNSLOTH); s.tick()
    w.open.discard(UNSLOTH); s.tick(); s.tick()
    w.open.add(UNSLOTH); s.tick()
    assert [n for n, _ in w.spawned] == ["llh", "chapterlens", "llh", "chapterlens"]


def test_stop_with_unsloth_false_leaves_services_running():
    w = World()
    s = make(w, stop_with_unsloth=False)
    w.open.add(UNSLOTH); s.tick()
    w.open.discard(UNSLOTH); s.tick(); s.tick(); s.tick()
    assert w.killed == []


def test_shutdown_stops_owned_services():
    w = World()
    s = make(w)
    w.open.add(UNSLOTH); s.tick()
    s.shutdown()
    assert sorted(w.killed) == ["chapterlens", "llh"]
    s.shutdown()
    assert len(w.killed) == 2, "2 回呼んでも、二重に止めない"


# ── 失敗への備え ─────────────────────────────────────────────
def test_service_that_never_listens_is_stopped_and_reported():
    w = World()
    w.never_opens.add("chapterlens")
    s = make(w)
    w.open.add(UNSLOTH); s.tick()
    assert "chapterlens" in w.killed
    assert any("chapterlens" in m and "待ち受け" in m for m in w.logs)
    assert w.now >= 10.0, "待ち時間(wait_s)を使い切ってから、諦める"


def test_pre_command_failure_does_not_block_the_service():
    w = World()
    w.run_codes[("uv", "run", "chapterlens", "admin", "llm", "on")] = 1
    s = make(w)
    w.open.add(UNSLOTH); s.tick()
    assert [n for n, _ in w.spawned] == ["llh", "chapterlens"]
    assert any("前処理" in m for m in w.logs)


def test_dead_service_is_restarted_up_to_three_times():
    w = World()
    s = make(w, services=(SERVE,))
    w.open.add(UNSLOTH); s.tick()
    for _ in range(6):
        w.dead.add("llh"); w.open.discard(8800)
        s.tick()
        w.dead.discard("llh")   # 再起動した新しいプロセスは、生きている(次の tick で、また死ぬ)
    assert len([1 for n, _ in w.spawned if n == "llh"]) == 1 + 3
    assert any("諦め" in m for m in w.logs)


def test_load_role_runs_once_per_session_after_services_are_up():
    w = World()
    s = make(w, load_role="main")
    w.open.add(UNSLOTH); s.tick(); s.tick(); s.tick()
    loads = [c for c in w.ran if c[-2:] == ("--role", "main")]
    assert len(loads) == 1
    assert w.ran.index(loads[0]) > w.ran.index(("uv", "run", "chapterlens", "admin", "llm", "on")), "読み込みは、サービスの起動のあと"
    w.open.discard(UNSLOTH); s.tick(); s.tick()
    w.open.add(UNSLOTH); s.tick()
    assert len([c for c in w.ran if c[-2:] == ("--role", "main")]) == 2, "Unsloth を開き直したら、もう一度"


def test_load_failure_is_logged_not_fatal():
    w = World()
    w.run_codes[("py", "-m", "llh", "load", "--role", "main")] = 2
    s = make(w, load_role="main")
    w.open.add(UNSLOTH); s.tick()
    assert [n for n, _ in w.spawned] == ["llh", "chapterlens"]
    assert any("読み込み" in m for m in w.logs)


def test_no_load_when_load_role_is_empty():
    w = World()
    s = make(w)
    w.open.add(UNSLOTH); s.tick()
    assert not any("load" in c for c in w.ran)


# ── 設定の読み込み ───────────────────────────────────────────
def write_cfg(tmp_path: Path, extra: str = "", backend_url: str = "http://127.0.0.1:8888/v1") -> Path:
    p = tmp_path / "config.toml"
    p.write_text(f'[backend]\nbase_url = "{backend_url}"\n[server]\nport = 8801\n[models.main]\nname = "main-model"\n{extra}', encoding="utf-8")
    return p


def test_load_plan_defaults_put_the_harness_first(tmp_path):
    plan = load_plan(write_cfg(tmp_path))
    assert (plan.unsloth_host, plan.unsloth_port) == ("127.0.0.1", 8888)
    assert [s.name for s in plan.services] == ["llh"]
    assert plan.services[0].port == 8801
    assert plan.services[0].command[:3] == (sys.executable, "-m", "llh")
    assert plan.services[0].command[-1] == "serve"
    assert plan.stop_with_unsloth is True and plan.load_role == "" and plan.poll_s == 5.0


def test_load_plan_reads_services_relative_to_the_config(tmp_path):
    extra = """
[autostart]
poll_s = 2
stop_with_unsloth = false
load_role = "main"
[[autostart.service]]
name = "chapterlens"
cwd = "../ChapterLens/engine"
command = ["uv", "run", "python", "-m", "chapterlens", "serve"]
pre = [["uv", "run", "python", "-m", "chapterlens", "admin", "llm", "on"]]
port = 8787
wait_s = 90
"""
    plan = load_plan(write_cfg(tmp_path, extra))
    assert [s.name for s in plan.services] == ["llh", "chapterlens"]
    svc = plan.services[1]
    assert svc.cwd == (tmp_path / "../ChapterLens/engine") and svc.port == 8787 and svc.wait_s == 90.0
    assert svc.pre == (("uv", "run", "python", "-m", "chapterlens", "admin", "llm", "on"),)
    assert plan.poll_s == 2.0 and plan.stop_with_unsloth is False and plan.load_role == "main"
    assert plan.load_command[-3:] == ("load", "--role", "main")


@pytest.mark.parametrize("snippet", [
    '[[autostart.service]]\nname = "bad name!"\ncommand = ["x"]\nport = 9000\n',
    '[[autostart.service]]\nname = "x"\ncommand = []\nport = 9000\n',
    '[[autostart.service]]\nname = "x"\ncommand = "uv run x"\nport = 9000\n',
    '[[autostart.service]]\nname = "x"\ncommand = ["x"]\nport = 70000\n',
    '[[autostart.service]]\nname = "x"\ncommand = ["x"]\n',
    '[[autostart.service]]\nname = "llh"\ncommand = ["x"]\nport = 9000\n',
    '[[autostart.service]]\nname = "x"\ncommand = ["x"]\nport = 9000\npre = ["not a list"]\n',
    '[autostart]\npoll_s = 0\n',
    '[autostart]\nload_role = "nonexistent-role"\n',
])
def test_load_plan_rejects_bad_settings(tmp_path, snippet):
    with pytest.raises(ConfigError):
        load_plan(write_cfg(tmp_path, snippet))


def test_load_plan_refuses_a_non_loopback_unsloth(tmp_path):
    with pytest.raises(ConfigError):
        load_plan(write_cfg(tmp_path, backend_url="http://192.168.0.5:8888/v1"))


# ── 二重起動の防止 ───────────────────────────────────────────
def test_lock_allows_only_one_instance():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    first = acquire_lock(port)
    assert first is not None
    assert acquire_lock(port) is None
    first.close()
    again = acquire_lock(port)
    assert again is not None
    again.close()


# ── ログオン時の自動起動(Windows のスタートアップ) ─────────────
def test_install_and_uninstall_startup_entry(tmp_path):
    path = install_startup(startup_dir=tmp_path, python=r"C:\py\python.exe", repo=tmp_path, config=None)
    assert path.parent == tmp_path and path.suffix == ".vbs"
    text = path.read_text(encoding="utf-16")
    assert "python.exe" in text and "-m llh autostart" in text and ", 0," in text   # 窓を出さない
    assert "--config" not in text
    assert uninstall_startup(startup_dir=tmp_path) is True
    assert not path.exists()
    assert uninstall_startup(startup_dir=tmp_path) is False


def test_install_startup_passes_the_config_path(tmp_path):
    cfg = tmp_path / "my config.toml"
    path = install_startup(startup_dir=tmp_path, python="python.exe", repo=tmp_path, config=cfg)
    assert "--config" in path.read_text(encoding="utf-16") and "my config.toml" in path.read_text(encoding="utf-16")
