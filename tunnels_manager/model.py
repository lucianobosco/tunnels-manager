"""Data model: what a tunnel is, and what is holding a local port.

This module deliberately has no GTK imports, so it can be unit-tested on its own.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shlex
import socket
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

#: How a tunnel is started.
TYPE_IAP = "iap"
TYPE_COMMAND = "command"

#: What is on the far end. Decides the row badge and the connection panel fields.
DEFAULT_GROUP = "Tunnels"


STATE_DOWN = "down"
STATE_STARTING = "starting"
STATE_UP = "up"
STATE_ERROR = "error"

LOG_LINES = 600

#: gcloud prints this once the local port is open.
READY_RE = re.compile(r"Listening on port \[(\d+)\]")

#: Substrings we can turn into an actionable message instead of raw tool output.
FAILURE_HINTS = (
    (
        "do not currently have an active account",
        "No active account: run `gcloud auth login`.",
    ),
    ("Reauthentication required", "Session expired: run `gcloud auth login`."),
    ("was not found", "The instance or the zone does not exist."),
    ("Permission denied", "No IAP permission on that instance."),
    ("PERMISSION_DENIED", "No IAP permission on that instance."),
    ("Address already in use", "The local port is already taken."),
    ("failed to connect to backend", "The backend refused the connection (instance stopped?)."),
    # kubectl port-forward
    ("Unable to connect to the server", "Cannot reach the cluster (VPN or credentials?)."),
    ("context was not found", "That kubectl context is not in your kubeconfig."),
    ("no such host", "The cluster host does not resolve."),
    ("unable to forward port", "The pod or service refused the port-forward."),
)

#: Command lines that look like a tunnel, so killing them is probably safe.
TUNNEL_HINTS = ("start-iap-tunnel", "port-forward", "cloud-sql-proxy", "ssh ")


def slugify(text: str) -> str:
    """Turn a label into a config key."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "tunnel"


def port_is_free(host: str, port: int) -> bool:
    """True when nobody is listening on host:port."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    bind_host = "0.0.0.0" if host == "*" else host
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((bind_host, port))
        return True
    except OSError as exc:
        # Anything other than "taken" or "not allowed" (bad host, unsupported family) does
        # not mean somebody is listening.
        return exc.errno not in (errno.EADDRINUSE, errno.EACCES)


#: How long a probe waits for the far end to say something before giving up.
PROBE_TIMEOUT = 3.0
#: How long it then spends emptying the socket, so the close is orderly.
DRAIN_TIMEOUT = 0.2


def measure_rtt(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> float | None:
    """Milliseconds for the far end of a tunnel to answer, or None when it says nothing.

    A tunnel forwards one TCP stream and offers no channel of its own to interrogate, so
    the only honest measurement is a byte that comes back. Timing the local connect()
    would measure nothing: gcloud accepts on this machine and only then opens the stream
    to the far side. What does traverse the whole path is the greeting a server sends on
    its own -- MySQL does, which is what makes this work for a database tunnel.

    The cost, stated plainly because it lands on somebody else's server: this opens a TCP
    connection and closes it without authenticating, which MySQL counts as an aborted
    client and may write to its error log.

    Servers that greet nobody (an HTTP service waiting for a request) return None rather
    than a number that would have to be invented.
    """
    started = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return None
    try:
        sock.settimeout(timeout)
        if not sock.recv(1):
            return None
        elapsed = (time.perf_counter() - started) * 1000.0
        # Closing a socket that still holds unread data makes the kernel send RST instead
        # of FIN, and the tunnel logs a traceback for every reset. So the rest of the
        # greeting is read and thrown away, and the close is an orderly one.
        sock.settimeout(DRAIN_TIMEOUT)
        try:
            while sock.recv(4096):
                pass
        except OSError:
            pass
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        return None
    finally:
        sock.close()
    return elapsed


@dataclass
class PortOwner:
    """The process currently listening on a local port."""

    pid: int
    name: str
    cmdline: str
    mine: bool  #: owned by the current user, so we may signal it without sudo
    own_tunnel: str = ""  #: key of one of our tunnels, when the process is ours

    @property
    def looks_like_tunnel(self) -> bool:
        lowered = self.cmdline.lower()
        return any(hint in lowered for hint in TUNNEL_HINTS)

    @property
    def short_cmd(self) -> str:
        return self.cmdline if len(self.cmdline) <= 160 else self.cmdline[:157] + "…"


def find_port_owner(port: int) -> PortOwner | None:
    """Ask `ss` who is listening on a local port."""
    try:
        result = subprocess.run(
            ["ss", "-H", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    match = re.search(r'users:\(\("([^"]+)",pid=(\d+)', result.stdout)
    if not match:
        return None
    name, pid = match.group(1), int(match.group(2))

    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        cmdline = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip() or name
    except OSError:
        cmdline = name

    mine = True
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Uid:"):
                mine = int(line.split()[1]) == os.getuid()
                break
    except (OSError, ValueError, IndexError):
        pass

    return PortOwner(pid=pid, name=name, cmdline=cmdline, mine=mine)


@dataclass
class Tunnel:
    """One row of the table: a local port forwarded somewhere."""

    key: str
    label: str
    # type "iap": the command is built from instance/remote_port/zone/project.
    # type "command": command_line is run as it is (kubectl port-forward, ssh -L, …).
    instance: str = ""
    remote_port: int = 0
    zone: str = ""
    project: str = ""
    local_host: str = "127.0.0.1"
    local_port: int = 0
    group: str = DEFAULT_GROUP
    extra_args: list = field(default_factory=list)
    type: str = TYPE_IAP
    command_line: str = ""
    target_label: str = ""
    #: Let gcloud see the interpreter's site-packages, so it finds NumPy and stops
    #: masking every WebSocket frame in pure Python. On by default; a system package
    #: shadowing one of the SDK's dependencies is the reason it can be turned off.
    site_packages: bool = True

    # Runtime state, never written to the config file.
    state: str = STATE_DOWN
    proc: subprocess.Popen | None = None
    started_at: float | None = None
    launched_at: float | None = None
    stopping: bool = False
    detail: str = ""
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES))

    # -- presentation ------------------------------------------------------- #

    @property
    def endpoint(self) -> str:
        return f"{self.local_host}:{self.local_port}"

    @property
    def short_endpoint(self) -> str:
        """Like endpoint, without the 127.0.0.1 that every row would repeat."""
        if self.local_host in ("127.0.0.1", "localhost"):
            return f":{self.local_port}"
        return self.endpoint

    @property
    def target(self) -> str:
        if self.type == TYPE_COMMAND:
            if self.target_label:
                return self.target_label
            parts = shlex.split(self.command_line)
            return parts[0] if parts else ""
        return f"{self.instance}:{self.remote_port}"

    @property
    def exposed(self) -> bool:
        """Listening on every interface, not just on loopback."""
        return self.local_host in ("0.0.0.0", "::", "*")

    @property
    def active(self) -> bool:
        return self.state in (STATE_UP, STATE_STARTING)

    @property
    def connect_host(self) -> str:
        """The host you connect to: even bound to 0.0.0.0, you go through loopback."""
        return "127.0.0.1" if self.exposed else self.local_host

    def uptime(self) -> str:
        if self.state != STATE_UP or not self.started_at:
            return ""
        secs = int(time.time() - self.started_at)
        if secs < 60:
            return f"{secs}s"
        mins, secs = divmod(secs, 60)
        if mins < 60:
            return f"{mins}m"
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins:02d}m"

    def connection_fields(self) -> list[tuple[str, str, bool]]:
        """(caption, value, copyable) rows for the connection panel."""
        # Where the port is, and what is on the other end of it. What you do with it --
        # which database, which user, which client -- is not a tunnel manager's business.
        host, port = self.connect_host, self.local_port
        return [
            ("Host", host, True),
            ("Port", str(port), True),
            ("host:port", f"{host}:{port}", True),
            ("Target", self.target, False),
        ]

    # -- process ------------------------------------------------------------ #

    def command(self) -> list[str]:
        if self.type == TYPE_COMMAND:
            return shlex.split(self.command_line)
        return [
            "gcloud",
            "compute",
            "start-iap-tunnel",
            self.instance,
            str(self.remote_port),
            f"--zone={self.zone}",
            f"--project={self.project}",
            f"--local-host-port={self.local_host}:{self.local_port}",
            *[str(arg) for arg in self.extra_args],
        ]

    def command_str(self) -> str:
        return shlex.join(self.command())

    def hint_for(self, line: str) -> str:
        """Turn a line of tool output into an actionable message, when we know one."""
        for needle, hint in FAILURE_HINTS:
            if needle in line:
                return hint
        return ""

    def last_meaningful_line(self) -> str:
        for line in reversed(self.log):
            clean = line.strip()
            if clean and not clean.startswith("$ ") and "Testing if tunnel" not in clean:
                return clean[:200]
        return "no output from the command"

    # -- config ------------------------------------------------------------- #

    def config_dict(self) -> dict:
        """The subset that belongs in tunnels.yaml."""
        if self.type == TYPE_COMMAND:
            data: dict = {
                "key": self.key,
                "label": self.label,
                "type": TYPE_COMMAND,
                "command": self.command_line,
                "local_host": self.local_host,
                "local_port": self.local_port,
            }
            if self.target_label:
                data["target_label"] = self.target_label
            if not self.site_packages:
                data["site_packages"] = False
        else:
            data = {
                "key": self.key,
                "label": self.label,
                "instance": self.instance,
                "remote_port": self.remote_port,
                "zone": self.zone,
                "project": self.project,
                "local_host": self.local_host,
                "local_port": self.local_port,
            }
            if self.extra_args:
                data["extra_args"] = list(self.extra_args)
            if not self.site_packages:
                data["site_packages"] = False

        data["group"] = self.group
        return data
