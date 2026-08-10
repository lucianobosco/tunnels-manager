"""Starting, watching and killing the tunnel processes.

No GTK here either: the window subscribes through the three callbacks.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import threading
import time

from . import config
from .model import (
    READY_RE,
    STATE_DOWN,
    STATE_ERROR,
    STATE_STARTING,
    STATE_UP,
    PortOwner,
    Tunnel,
    find_port_owner,
    port_is_free,
)

#: Seconds to wait for the local port to open before giving up on a tunnel.
START_TIMEOUT = 45
#: Grace period between SIGTERM and SIGKILL when reaping our own child.
KILL_GRACE = 5
#: While freeing a foreign port: how long to wait for SIGTERM to work, and how long to
#: let the kernel release the port after SIGKILL. Small enough for tests to override.
TERM_WAIT_STEPS = 30
TERM_WAIT_SLEEP = 0.1
KILL_SETTLE = 0.4
#: Restarting: how long to wait for the old process to let go of the port.
RESTART_WAIT_STEPS = 40
RESTART_WAIT_SLEEP = 0.05


class TunnelManager:
    """Owns the tunnels and their processes."""

    def __init__(self) -> None:
        self.tunnels: dict[str, Tunnel] = {}
        self.order: list[str] = []
        self.bundles: dict[str, list[str]] = {}
        self.on_change = lambda: None
        self.on_event = lambda message: None
        self.on_port_busy = lambda tunnel: None

    # -- configuration ------------------------------------------------------ #

    def load_config(self) -> list[str]:
        """(Re)read the config file. Returns warnings worth showing the user."""
        raw, warnings = config.read_raw()
        parsed, parse_warnings = config.parse_tunnels(raw)
        warnings += parse_warnings

        merged: dict[str, Tunnel] = {}
        order: list[str] = []
        for tunnel in parsed:
            existing = self.tunnels.get(tunnel.key)
            if existing is not None and existing.active:
                # A running tunnel survives the reload untouched.
                if existing.command() != tunnel.command():
                    warnings.append(
                        f"'{existing.label}' is running: changes apply when you restart it."
                    )
                merged[tunnel.key] = existing
            else:
                merged[tunnel.key] = tunnel
            order.append(tunnel.key)

        # Running tunnels that vanished from the file stay alive until they are stopped.
        for key, tunnel in self.tunnels.items():
            if key not in merged and tunnel.active:
                merged[key] = tunnel
                order.append(key)
                warnings.append(f"'{tunnel.label}' is no longer in the file but is still running.")

        self.tunnels = merged
        self.order = order

        bundles, bundle_warnings = config.parse_bundles(raw, set(self.tunnels))
        self.bundles = bundles
        return warnings + bundle_warnings

    def save_config(self) -> None:
        config.write(self.ordered(), self.bundles)

    def ordered(self) -> list[Tunnel]:
        return [self.tunnels[key] for key in self.order if key in self.tunnels]

    def active_count(self) -> int:
        return sum(1 for tunnel in self.tunnels.values() if tunnel.active)

    # -- port validation ---------------------------------------------------- #

    def port_conflicts(self) -> dict[int, list[Tunnel]]:
        """Local ports claimed by more than one tunnel.

        Only the port number is compared, never local_host: 0.0.0.0 covers 127.0.0.1,
        so two tunnels on the same port collide either way.
        """
        by_port: dict[int, list[Tunnel]] = {}
        for tunnel in self.ordered():
            by_port.setdefault(tunnel.local_port, []).append(tunnel)
        return {port: items for port, items in by_port.items() if len(items) > 1}

    def clash_for(self, local_port: int, ignore_key: str | None = None) -> Tunnel | None:
        """The tunnel already using that port, if any."""
        for tunnel in self.ordered():
            if tunnel.local_port == local_port and tunnel.key != ignore_key:
                return tunnel
        return None

    def next_free_port(self, start: int) -> int | None:
        """First port from `start` that is unused in the config and free on the machine."""
        used = {tunnel.local_port for tunnel in self.tunnels.values()}
        for port in range(max(start, 1024), 65536):
            if port not in used and port_is_free("127.0.0.1", port):
                return port
        return None  # pragma: no cover - would need 64k busy ports

    # -- freeing a busy port ------------------------------------------------ #

    def port_owner(self, port: int) -> PortOwner | None:
        """Like find_port_owner(), but flags processes that are our own tunnels."""
        owner = find_port_owner(port)
        if owner is None:
            return None
        for tunnel in self.tunnels.values():
            proc = tunnel.proc
            if proc is not None and proc.pid == owner.pid:
                owner.own_tunnel = tunnel.key
                break
        return owner

    @staticmethod
    def signal_pid(pid: int, sig: int) -> None:
        """Signal one process. A seam so tests do not have to patch os.kill globally."""
        os.kill(pid, sig)

    def free_port(self, port: int) -> tuple[bool, str]:
        """Close whatever holds the port. Returns (succeeded, explanation)."""
        owner = self.port_owner(port)
        if owner is None:
            return True, f"Port {port} was already free."

        if owner.own_tunnel:
            tunnel = self.tunnels[owner.own_tunnel]
            self.stop(tunnel)
            return True, f"Stopped '{tunnel.label}', which was holding port {port}."

        if not owner.mine:
            return False, (
                f"Port {port} belongs to another user's process (PID {owner.pid}); "
                "that would need sudo."
            )

        try:
            self.signal_pid(owner.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True, f"The process on port {port} was already gone."
        except PermissionError:
            return False, f"Not allowed to signal PID {owner.pid}."

        for _ in range(TERM_WAIT_STEPS):
            if port_is_free("127.0.0.1", port):
                return True, f"Closed {owner.name} (PID {owner.pid}); port {port} is free."
            time.sleep(TERM_WAIT_SLEEP)

        # pragma: no cover reason: the process could vanish between the two signals.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            self.signal_pid(owner.pid, signal.SIGKILL)
        time.sleep(KILL_SETTLE)
        if port_is_free("127.0.0.1", port):
            return True, f"Force-closed {owner.name} (PID {owner.pid})."
        return False, f"Port {port} is still held by PID {owner.pid}."

    # -- lifecycle ---------------------------------------------------------- #

    def start(self, tunnel: Tunnel) -> None:
        if tunnel.active:
            return

        argv = tunnel.command()
        if not argv or not shutil.which(argv[0]):
            binary = argv[0] if argv else "(empty)"
            self._fail(tunnel, f"Could not find '{binary}' in PATH.")
            return

        if not port_is_free(tunnel.local_host, tunnel.local_port):
            owner = next(
                (
                    other
                    for other in self.ordered()
                    if other.key != tunnel.key
                    and other.local_port == tunnel.local_port
                    and other.active
                ),
                None,
            )
            if owner is not None:
                tunnel.detail = (
                    f"Port {tunnel.local_port} is used by '{owner.label}': "
                    "two tunnels cannot share a local port."
                )
            else:
                tunnel.detail = f"Port {tunnel.local_port} is already taken by another process."
            tunnel.state = STATE_ERROR
            # The window offers to free it instead of making you hunt for the process.
            self.on_port_busy(tunnel)
            self.on_change()
            return

        tunnel.log.clear()
        tunnel.detail = ""
        tunnel.stopping = False
        tunnel.started_at = None
        tunnel.launched_at = time.time()
        tunnel.log.append(f"$ {tunnel.command_str()}")

        try:
            tunnel.proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
                bufsize=1,
                start_new_session=True,
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
            )
        except OSError as exc:
            self._fail(tunnel, f"Could not launch the command: {exc}")
            return

        tunnel.state = STATE_STARTING
        threading.Thread(target=self._read_output, args=(tunnel, tunnel.proc), daemon=True).start()
        self.on_change()

    def _fail(self, tunnel: Tunnel, detail: str) -> None:
        tunnel.state = STATE_ERROR
        tunnel.detail = detail
        self.on_event(detail)
        self.on_change()

    def _read_output(self, tunnel: Tunnel, proc: subprocess.Popen) -> None:
        """Collect the command output in a background thread."""
        stream = proc.stdout
        assert stream is not None, "the process is always started with stdout=PIPE"
        for line in stream:
            line = line.rstrip("\n")
            if not line:
                continue
            tunnel.log.append(line)
            if tunnel.proc is not proc:
                continue
            if READY_RE.search(line):
                self.mark_up(tunnel)
            else:
                hint = tunnel.hint_for(line)
                if hint:
                    tunnel.detail = hint
        stream.close()

    def mark_up(self, tunnel: Tunnel) -> None:
        if tunnel.state == STATE_STARTING:
            tunnel.state = STATE_UP
            tunnel.started_at = time.time()
            self.on_change()

    def stop(self, tunnel: Tunnel, quiet: bool = False) -> None:
        proc = tunnel.proc
        tunnel.stopping = True
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
            threading.Thread(target=self._reap, args=(proc,), daemon=True).start()
        tunnel.proc = None
        tunnel.state = STATE_DOWN
        tunnel.started_at = None
        tunnel.launched_at = None
        tunnel.detail = ""
        if not quiet:
            self.on_change()

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        """Wait for a stopped process, escalating to SIGKILL if it lingers."""
        try:
            proc.wait(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=3)

    def toggle(self, tunnel: Tunnel) -> None:
        if tunnel.active:
            self.stop(tunnel)
        else:
            self.start(tunnel)

    def restart(self, tunnel: Tunnel) -> None:
        """Stop and start again, waiting for the old process to release the port.

        Without the wait, the fresh process would find its own port still taken.
        """
        self.stop(tunnel, quiet=True)
        for _ in range(RESTART_WAIT_STEPS):
            if port_is_free(tunnel.local_host, tunnel.local_port):
                break
            time.sleep(RESTART_WAIT_SLEEP)
        self.start(tunnel)

    def start_all(self) -> None:
        for tunnel in self.ordered():
            if not tunnel.active:
                self.start(tunnel)

    def stop_all(self) -> None:
        for tunnel in self.ordered():
            if tunnel.active or tunnel.state == STATE_ERROR:
                self.stop(tunnel, quiet=True)
        self.on_change()

    def start_bundle(self, name: str) -> int:
        """Open every tunnel of a shortcut, skipping the ones already up."""
        started = 0
        for key in self.bundles.get(name, []):
            tunnel = self.tunnels.get(key)
            if tunnel is not None and not tunnel.active:
                self.start(tunnel)
                started += 1
        return started

    # -- watchdog ----------------------------------------------------------- #

    def poll(self) -> None:
        """Refresh states: detect the port opening, crashes and timeouts."""
        changed = False
        for tunnel in list(self.tunnels.values()):
            proc = tunnel.proc
            if tunnel.state == STATE_STARTING:
                if proc is not None and proc.poll() is not None:
                    tunnel.state = STATE_ERROR
                    tunnel.detail = tunnel.detail or tunnel.last_meaningful_line()
                    tunnel.proc = None
                    self.on_event(f"'{tunnel.label}' did not start: {tunnel.detail}")
                    changed = True
                elif not port_is_free(tunnel.local_host, tunnel.local_port):
                    tunnel.state = STATE_UP
                    tunnel.started_at = time.time()
                    changed = True
                elif tunnel.launched_at and time.time() - tunnel.launched_at > START_TIMEOUT:
                    tunnel.detail = tunnel.detail or "Timed out waiting for the port to open."
                    detail = tunnel.detail
                    self.stop(tunnel, quiet=True)
                    tunnel.state = STATE_ERROR
                    tunnel.detail = detail
                    self.on_event(f"'{tunnel.label}': {detail}")
                    changed = True
            elif tunnel.state == STATE_UP and (proc is None or proc.poll() is not None):
                tunnel.state = STATE_ERROR
                tunnel.detail = tunnel.detail or tunnel.last_meaningful_line()
                tunnel.proc = None
                tunnel.started_at = None
                self.on_event(f"'{tunnel.label}' died: {tunnel.detail}")
                changed = True
        if changed:
            self.on_change()
