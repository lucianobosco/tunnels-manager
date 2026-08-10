"""Shared fixtures.

Every test that touches the configuration gets its own XDG_CONFIG_HOME, so the developer's
own tunnels.yaml is never read or written.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tunnels_manager import config
from tunnels_manager.manager import TunnelManager
from tunnels_manager.model import Tunnel

SAMPLE_CONFIG = textwrap.dedent(
    """
    tunnels:
      - key: shop
        label: Shop
        instance: my-bastion
        remote_port: 3306
        zone: europe-west1-d
        project: my-project-pro
        local_host: 127.0.0.1
        local_port: 15001
        group: Databases
      - key: reports
        label: Reports
        instance: my-bastion
        remote_port: 3307
        zone: europe-west1-d
        project: my-project-pre
        local_host: 0.0.0.0
        local_port: 15002
        group: Databases
      - key: dashboard
        label: Dashboard
        type: command
        command: kubectl -n team port-forward svc/dash 15003:80
        local_host: 127.0.0.1
        local_port: 15003
        target_label: svc/dash:80 (team)
        group: Services

    bundles:
      Daily work:
        - shop
        - reports
    """
).strip()


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    """Point XDG_CONFIG_HOME at a temporary directory."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def written_config(config_home):
    """A ready-made tunnels.yaml with three tunnels and one shortcut."""
    config.config_dir().mkdir(parents=True, exist_ok=True)
    config.config_file().write_text(SAMPLE_CONFIG, encoding="utf-8")
    return config.config_file()


@pytest.fixture
def manager(written_config):
    instance = TunnelManager()
    instance.load_config()
    return instance


@pytest.fixture
def tunnel() -> Tunnel:
    return Tunnel(
        key="shop",
        label="Shop",
        instance="my-bastion",
        remote_port=3306,
        zone="europe-west1-d",
        project="my-project-pro",
        local_port=15001,
        group="Databases",
    )


@pytest.fixture
def free_port() -> int:
    """A port that nobody is listening on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def listener():
    """Start throwaway processes that hold a port, and clean them up afterwards."""
    started: list[subprocess.Popen] = []

    def start(port: int) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", HOLD_PORT_SCRIPT, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own session: the manager kills process groups, and this must never
            # take the test runner down with it.
            start_new_session=True,
        )
        started.append(proc)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    return proc
            time.sleep(0.02)
        raise AssertionError(f"the listener never claimed port {port}")  # pragma: no cover

    yield start

    for proc in started:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


HOLD_PORT_SCRIPT = textwrap.dedent(
    """
    import socket, sys, time
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", int(sys.argv[1])))
    server.listen(1)
    while True:
        time.sleep(1)
    """
).strip()
