"""Tests for the process lifecycle, the watchdog and the port rules.

Real child processes are used, but only harmless ones: a tiny Python script that holds a
port, and `sleep`. Nothing here talks to Google Cloud.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from tunnels_manager import config
from tunnels_manager import manager as manager_module
from tunnels_manager.manager import TunnelManager
from tunnels_manager.model import (
    STATE_DOWN,
    STATE_ERROR,
    STATE_STARTING,
    STATE_UP,
    PortOwner,
    Tunnel,
)

#: A stand-in for gcloud: opens the port and prints the line the manager watches for.
FAKE_TUNNEL = textwrap.dedent(
    """
    import socket, sys, time
    port = int(sys.argv[1])
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(1)
    print("Testing if tunnel connection works.", flush=True)
    print(f"Listening on port [{port}].", flush=True)
    while True:
        time.sleep(1)
    """
).strip()


def fake_tunnel(port: int) -> Tunnel:
    """A tunnel whose command is our fake gcloud."""
    return Tunnel(
        key="fake",
        label="Fake tunnel",
        type="command",
        command_line=f"{sys.executable} -c {shell_quote(FAKE_TUNNEL)} {port}",
        local_port=port,
    )


def shell_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)


def wait_for(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# -- configuration merging -------------------------------------------------- #


def test_load_config_reads_tunnels_and_bundles(manager):
    assert [tunnel.key for tunnel in manager.ordered()] == ["shop", "reports", "dashboard"]
    assert manager.bundles == {"Daily work": ["shop", "reports"]}
    assert manager.active_count() == 0


def test_ordered_skips_keys_that_disappeared(manager):
    manager.tunnels.pop("shop")
    assert [tunnel.key for tunnel in manager.ordered()] == ["reports", "dashboard"]


def test_reload_keeps_a_running_tunnel_object(manager):
    running = manager.tunnels["shop"]
    running.state = STATE_UP
    manager.load_config()
    assert manager.tunnels["shop"] is running


def test_reload_warns_when_a_running_tunnel_changed(manager, written_config):
    running = manager.tunnels["shop"]
    running.state = STATE_UP
    written_config.write_text(
        written_config.read_text().replace("local_port: 15001", "local_port: 15009"),
        encoding="utf-8",
    )
    warnings = manager.load_config()
    assert any("changes apply when you restart it" in warning for warning in warnings)
    assert manager.tunnels["shop"].local_port == 15001


def test_reload_keeps_a_running_tunnel_that_left_the_file(manager, written_config):
    running = manager.tunnels["dashboard"]
    running.state = STATE_UP
    written_config.write_text(
        written_config.read_text().split("  - key: dashboard")[0], encoding="utf-8"
    )
    warnings = manager.load_config()
    assert "dashboard" in manager.tunnels
    assert any("no longer in the file" in warning for warning in warnings)


def test_reload_drops_a_stopped_tunnel_that_left_the_file(manager, written_config):
    written_config.write_text(
        written_config.read_text().split("  - key: dashboard")[0], encoding="utf-8"
    )
    manager.load_config()
    assert "dashboard" not in manager.tunnels


def test_save_config_round_trip(manager):
    manager.tunnels["shop"].database = "changed"
    manager.save_config()
    fresh = TunnelManager()
    fresh.load_config()
    assert fresh.tunnels["shop"].database == "changed"
    assert fresh.bundles == {"Daily work": ["shop", "reports"]}


# -- port rules ------------------------------------------------------------- #


def test_port_conflicts_finds_duplicates(manager):
    manager.tunnels["reports"].local_port = 15001
    conflicts = manager.port_conflicts()
    assert list(conflicts) == [15001]
    assert {tunnel.key for tunnel in conflicts[15001]} == {"shop", "reports"}


def test_port_conflicts_ignores_the_host(manager):
    # 0.0.0.0 covers 127.0.0.1, so the same number is a conflict either way.
    manager.tunnels["reports"].local_port = 15001
    manager.tunnels["reports"].local_host = "0.0.0.0"
    assert 15001 in manager.port_conflicts()


def test_port_conflicts_empty_when_unique(manager):
    assert manager.port_conflicts() == {}


def test_clash_for(manager):
    assert manager.clash_for(15001).key == "shop"
    assert manager.clash_for(15001, ignore_key="shop") is None
    assert manager.clash_for(9999) is None


def test_next_free_port_skips_configured_and_busy_ports(manager, free_port, listener):
    listener(free_port)
    manager.tunnels["shop"].local_port = free_port + 1
    found = manager.next_free_port(free_port)
    assert found > free_port + 1


def test_next_free_port_raises_the_floor_to_1024(manager):
    assert manager.next_free_port(1) >= 1024


# -- freeing a port --------------------------------------------------------- #


def test_port_owner_flags_our_own_tunnel(manager, free_port, listener):
    proc = listener(free_port)
    manager.tunnels["shop"].proc = proc
    owner = manager.port_owner(free_port)
    assert owner is not None
    assert owner.own_tunnel == "shop"


def test_port_owner_none_when_free(manager, free_port):
    assert manager.port_owner(free_port) is None


def test_free_port_when_already_free(manager, free_port):
    freed, message = manager.free_port(free_port)
    assert freed is True
    assert "already free" in message


def test_free_port_stops_our_own_tunnel(manager, free_port, listener):
    proc = listener(free_port)
    tunnel = manager.tunnels["shop"]
    tunnel.proc = proc
    tunnel.state = STATE_UP
    freed, message = manager.free_port(free_port)
    assert freed is True
    assert "Stopped 'Shop'" in message
    assert tunnel.state == STATE_DOWN


def test_free_port_kills_a_foreign_process(manager, free_port, listener):
    proc = listener(free_port)
    freed, message = manager.free_port(free_port)
    assert freed is True
    assert str(proc.pid) in message
    assert wait_for(lambda: proc.poll() is not None)


def test_free_port_refuses_another_user(manager, monkeypatch):
    monkeypatch.setattr(
        manager, "port_owner", lambda _port: PortOwner(1, "root-thing", "cmd", mine=False)
    )
    freed, message = manager.free_port(80)
    assert freed is False
    assert "sudo" in message


def test_free_port_handles_a_process_that_vanished(manager, monkeypatch):
    monkeypatch.setattr(
        manager, "port_owner", lambda _port: PortOwner(4242, "ghost", "cmd", mine=True)
    )

    def gone(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(manager, "signal_pid", gone)
    freed, message = manager.free_port(1234)
    assert freed is True
    assert "already gone" in message


def test_free_port_reports_a_permission_error(manager, monkeypatch):
    monkeypatch.setattr(
        manager, "port_owner", lambda _port: PortOwner(4242, "thing", "cmd", mine=True)
    )

    def denied(_pid, _sig):
        raise PermissionError

    monkeypatch.setattr(manager, "signal_pid", denied)
    freed, message = manager.free_port(1234)
    assert freed is False
    assert "Not allowed" in message


def test_free_port_escalates_to_sigkill(manager, free_port, monkeypatch):
    """A process that ignores SIGTERM still gets closed."""
    script = (
        "import signal, socket, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {free_port})); s.listen(1)\n"
        "while True: time.sleep(0.1)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        assert wait_for(lambda: manager.port_owner(free_port) is not None)
        # Shorten the grace period so the test does not sit for three seconds.
        monkeypatch.setattr(manager_module, "TERM_WAIT_STEPS", 3)
        monkeypatch.setattr(manager_module, "TERM_WAIT_SLEEP", 0.05)
        freed, message = manager.free_port(free_port)
        assert freed is True
        assert "Force-closed" in message
    finally:
        if proc.poll() is None:  # pragma: no cover - only if the kill failed
            proc.kill()
        proc.wait(timeout=5)


def test_free_port_gives_up_when_the_port_stays_busy(manager, monkeypatch, free_port, listener):
    listener(free_port)
    monkeypatch.setattr(manager, "signal_pid", lambda _pid, _sig: None)
    monkeypatch.setattr(manager_module, "TERM_WAIT_STEPS", 2)
    monkeypatch.setattr(manager_module, "TERM_WAIT_SLEEP", 0.01)
    monkeypatch.setattr(manager_module, "KILL_SETTLE", 0.01)
    freed, message = manager.free_port(free_port)
    assert freed is False
    assert "still held" in message


# -- starting and stopping -------------------------------------------------- #


def test_start_reports_a_missing_binary(manager):
    tunnel = manager.tunnels["dashboard"]
    tunnel.command_line = "definitely-not-a-real-binary --flag"
    events: list[str] = []
    manager.on_event = events.append
    manager.start(tunnel)
    assert tunnel.state == STATE_ERROR
    assert "Could not find" in tunnel.detail
    assert events


def test_start_reports_an_empty_command(manager):
    tunnel = manager.tunnels["dashboard"]
    tunnel.command_line = ""
    manager.start(tunnel)
    assert tunnel.state == STATE_ERROR
    assert "(empty)" in tunnel.detail


def test_start_refuses_a_busy_port(manager, free_port, listener):
    listener(free_port)
    tunnel = manager.tunnels["shop"]
    tunnel.local_port = free_port
    busy: list[Tunnel] = []
    manager.on_port_busy = busy.append
    manager.start(tunnel)
    assert tunnel.state == STATE_ERROR
    assert "already taken" in tunnel.detail
    assert busy == [tunnel]


def test_start_names_the_sibling_tunnel_holding_the_port(manager, free_port, listener):
    proc = listener(free_port)
    other = manager.tunnels["reports"]
    other.local_port = free_port
    other.state = STATE_UP
    other.proc = proc

    tunnel = manager.tunnels["shop"]
    tunnel.local_port = free_port
    manager.start(tunnel)
    assert "'Reports'" in tunnel.detail


def test_start_reports_a_launch_failure(manager, monkeypatch, free_port):
    tunnel = fake_tunnel(free_port)
    manager.tunnels[tunnel.key] = tunnel
    manager.order.append(tunnel.key)

    def boom(*_args, **_kwargs):
        raise OSError("no fork for you")

    monkeypatch.setattr(manager_module.subprocess, "Popen", boom)
    manager.start(tunnel)
    assert tunnel.state == STATE_ERROR
    assert "Could not launch" in tunnel.detail


def test_start_does_nothing_when_already_active(manager):
    tunnel = manager.tunnels["shop"]
    tunnel.state = STATE_UP
    manager.start(tunnel)
    assert tunnel.proc is None


def test_full_lifecycle_with_a_fake_tunnel(manager, free_port):
    tunnel = fake_tunnel(free_port)
    manager.tunnels[tunnel.key] = tunnel
    manager.order.append(tunnel.key)

    manager.start(tunnel)
    assert tunnel.state == STATE_STARTING
    assert tunnel.log[0].startswith("$ ")

    # The output line alone marks it up, without waiting for the poll.
    assert wait_for(lambda: tunnel.state == STATE_UP)
    assert tunnel.started_at is not None
    pid = tunnel.proc.pid

    manager.stop(tunnel)
    assert tunnel.state == STATE_DOWN
    assert tunnel.proc is None
    assert wait_for(lambda: not process_alive(pid))


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def test_stop_is_safe_on_a_stopped_tunnel(manager):
    tunnel = manager.tunnels["shop"]
    manager.stop(tunnel, quiet=True)
    assert tunnel.state == STATE_DOWN


def test_stop_falls_back_to_terminate(manager, monkeypatch, free_port):
    tunnel = fake_tunnel(free_port)
    manager.start(tunnel)
    assert wait_for(lambda: tunnel.state == STATE_UP)

    def no_group(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(manager_module.os, "killpg", no_group)
    proc = tunnel.proc
    manager.stop(tunnel)
    assert wait_for(lambda: proc.poll() is not None)


def test_reap_escalates_to_sigkill(free_port):
    """_reap sends SIGKILL when the process outlives the grace period."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "while True: time.sleep(0.1)",
        ],
        start_new_session=True,
    )
    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    original = manager_module.KILL_GRACE
    manager_module.KILL_GRACE = 0.2
    try:
        TunnelManager._reap(proc)
    finally:
        manager_module.KILL_GRACE = original
    assert proc.poll() is not None


def test_toggle_starts_and_stops(manager, free_port):
    tunnel = fake_tunnel(free_port)
    manager.toggle(tunnel)
    assert wait_for(lambda: tunnel.state == STATE_UP)
    manager.toggle(tunnel)
    assert tunnel.state == STATE_DOWN


def test_restart_reopens_the_tunnel(manager, free_port):
    tunnel = fake_tunnel(free_port)
    manager.start(tunnel)
    assert wait_for(lambda: tunnel.state == STATE_UP)
    first_pid = tunnel.proc.pid
    manager.restart(tunnel)
    assert wait_for(lambda: tunnel.state == STATE_UP and tunnel.proc.pid != first_pid)


def test_start_all_and_stop_all(manager, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(manager, "start", lambda tunnel: started.append(tunnel.key))
    manager.start_all()
    assert started == ["shop", "reports", "dashboard"]

    for tunnel in manager.ordered():
        tunnel.state = STATE_UP
    stopped: list[str] = []
    monkeypatch.setattr(manager, "stop", lambda tunnel, quiet=False: stopped.append(tunnel.key))
    manager.stop_all()
    assert stopped == ["shop", "reports", "dashboard"]


def test_stop_all_also_clears_errors(manager, monkeypatch):
    manager.tunnels["shop"].state = STATE_ERROR
    stopped: list[str] = []
    monkeypatch.setattr(manager, "stop", lambda tunnel, quiet=False: stopped.append(tunnel.key))
    manager.stop_all()
    assert stopped == ["shop"]


def test_start_bundle_skips_open_tunnels(manager, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(manager, "start", lambda tunnel: started.append(tunnel.key))
    manager.tunnels["shop"].state = STATE_UP
    assert manager.start_bundle("Daily work") == 1
    assert started == ["reports"]


def test_start_bundle_with_an_unknown_name(manager):
    assert manager.start_bundle("nope") == 0


def test_start_bundle_ignores_missing_keys(manager, monkeypatch):
    manager.bundles["Broken"] = ["ghost"]
    assert manager.start_bundle("Broken") == 0


def test_mark_up_only_from_starting(manager):
    tunnel = manager.tunnels["shop"]
    manager.mark_up(tunnel)
    assert tunnel.state == STATE_DOWN
    tunnel.state = STATE_STARTING
    manager.mark_up(tunnel)
    assert tunnel.state == STATE_UP


def test_read_output_collects_hints(manager, free_port):
    """A hint from the output ends up in detail without touching the state."""
    program = 'print("ERROR: Reauthentication required", flush=True)'
    tunnel = Tunnel(
        key="hinting",
        label="Hinting",
        type="command",
        command_line=f"{sys.executable} -c {shell_quote(program)}",
        local_port=free_port,
    )
    manager.start(tunnel)
    assert wait_for(lambda: "gcloud auth login" in tunnel.detail)


# -- watchdog --------------------------------------------------------------- #


def test_poll_marks_up_when_the_port_opens(manager, free_port, listener):
    tunnel = manager.tunnels["shop"]
    tunnel.local_port = free_port
    tunnel.state = STATE_STARTING
    tunnel.launched_at = time.time()
    listener(free_port)
    changes: list[int] = []
    manager.on_change = lambda: changes.append(1)
    manager.poll()
    assert tunnel.state == STATE_UP
    assert changes


def test_poll_detects_a_process_that_died_while_starting(manager, free_port):
    tunnel = Tunnel(
        key="quick",
        label="Quick",
        type="command",
        command_line=f"{sys.executable} -c {shell_quote('raise SystemExit(3)')}",
        local_port=free_port,
    )
    manager.tunnels[tunnel.key] = tunnel
    manager.order.append(tunnel.key)
    events: list[str] = []
    manager.on_event = events.append

    manager.start(tunnel)
    assert wait_for(lambda: tunnel.proc is not None and tunnel.proc.poll() is not None)
    manager.poll()
    assert tunnel.state == STATE_ERROR
    assert any("did not start" in event for event in events)


def test_poll_times_out(manager, free_port):
    tunnel = fake_tunnel(free_port + 1)  # opens another port, never this one
    tunnel.local_port = free_port
    manager.tunnels[tunnel.key] = tunnel
    manager.order.append(tunnel.key)
    events: list[str] = []
    manager.on_event = events.append

    manager.start(tunnel)
    tunnel.state = STATE_STARTING
    tunnel.launched_at = time.time() - manager_module.START_TIMEOUT - 1
    manager.poll()
    assert tunnel.state == STATE_ERROR
    assert "Timed out" in tunnel.detail
    assert any("Timed out" in event for event in events)


def test_poll_detects_a_tunnel_that_died_while_up(manager):
    tunnel = manager.tunnels["shop"]
    tunnel.state = STATE_UP
    tunnel.proc = None
    events: list[str] = []
    manager.on_event = events.append
    manager.poll()
    assert tunnel.state == STATE_ERROR
    assert any("died" in event for event in events)


def test_poll_is_quiet_when_nothing_changed(manager):
    changes: list[int] = []
    manager.on_change = lambda: changes.append(1)
    manager.poll()
    assert changes == []


def test_default_callbacks_are_harmless():
    plain = TunnelManager()
    plain.on_change()
    plain.on_event("something")
    plain.on_port_busy(None)


@pytest.mark.parametrize("quiet", [True, False])
def test_stop_change_notification(manager, quiet, free_port):
    tunnel = fake_tunnel(free_port)
    manager.start(tunnel)
    assert wait_for(lambda: tunnel.state == STATE_UP)
    changes: list[int] = []
    manager.on_change = lambda: changes.append(1)
    manager.stop(tunnel, quiet=quiet)
    assert bool(changes) is (not quiet)


def test_config_file_is_the_temporary_one(config_home):
    """Guard rail: the suite must never touch the developer's real configuration."""
    assert str(config.config_file()).startswith(str(config_home))


# -- output reader, driven directly ----------------------------------------- #


class FakeStdout:
    """A stdout that yields prepared lines."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self.lines)

    def close(self) -> None:
        self.closed = True


class FakeProc:
    def __init__(self, lines: list[str]):
        self.stdout = FakeStdout(lines)
        self.pid = -1

    def poll(self):
        return None


def test_read_output_marks_up_skips_blanks_and_keeps_hints(manager):
    """Driving the reader directly covers every branch without a real process."""
    tunnel = manager.tunnels["shop"]
    tunnel.state = STATE_STARTING
    proc = FakeProc(
        [
            "\n",  # blank: ignored, not logged
            "Testing if tunnel connection works.\n",
            "ERROR: Reauthentication required\n",
            "Listening on port [15001].\n",
        ]
    )
    tunnel.proc = proc

    manager._read_output(tunnel, proc)

    assert "" not in list(tunnel.log)
    assert tunnel.detail == "Session expired: run `gcloud auth login`."
    assert tunnel.state == STATE_UP
    assert proc.stdout.closed is True


def test_read_output_ignores_a_replaced_process(manager):
    """Output from a process the tunnel no longer owns is logged but acted on no further."""
    tunnel = manager.tunnels["shop"]
    tunnel.state = STATE_STARTING
    stale = FakeProc(["Listening on port [15001].\n"])
    tunnel.proc = FakeProc([])  # a newer process took over

    manager._read_output(tunnel, stale)

    assert tunnel.state == STATE_STARTING
    assert "Listening on port [15001]." in list(tunnel.log)


def test_reap_kills_a_process_that_outlives_the_grace_period(monkeypatch):
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    monkeypatch.setattr(manager_module, "KILL_GRACE", 0.2)
    TunnelManager._reap(proc)
    assert proc.poll() is not None


def test_restart_gives_up_waiting_for_the_port(manager, monkeypatch, free_port, listener):
    """When the port stays busy, restart still tries to start and reports the clash."""
    listener(free_port)
    tunnel = manager.tunnels["shop"]
    tunnel.local_port = free_port
    monkeypatch.setattr(manager_module, "RESTART_WAIT_STEPS", 2)
    monkeypatch.setattr(manager_module, "RESTART_WAIT_SLEEP", 0.01)
    manager.restart(tunnel)
    assert tunnel.state == STATE_ERROR
    assert "already taken" in tunnel.detail


def test_start_all_skips_the_tunnels_already_open(manager, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(manager, "start", lambda tunnel: started.append(tunnel.key))
    manager.tunnels["shop"].state = STATE_UP
    manager.start_all()
    assert started == ["reports", "dashboard"]


def test_poll_leaves_a_healthy_tunnel_alone(manager):
    tunnel = manager.tunnels["shop"]
    tunnel.state = STATE_UP
    tunnel.proc = FakeProc([])  # poll() returns None: still running
    changes: list[int] = []
    manager.on_change = lambda: changes.append(1)
    manager.poll()
    assert tunnel.state == STATE_UP
    assert changes == []


def test_poll_waits_while_a_tunnel_is_still_opening(manager, free_port):
    """Alive process, port not open yet, no timeout: nothing to report."""
    tunnel = manager.tunnels["shop"]
    tunnel.local_port = free_port
    tunnel.state = STATE_STARTING
    tunnel.launched_at = time.time()
    tunnel.proc = FakeProc([])
    changes: list[int] = []
    manager.on_change = lambda: changes.append(1)
    manager.poll()
    assert tunnel.state == STATE_STARTING
    assert changes == []
