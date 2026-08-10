"""Data model: what a tunnel is, and what is holding a local port.

This module deliberately has no GTK imports, so it can be unit-tested on its own.
"""

from __future__ import annotations

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
SERVICE_MYSQL = "mysql"
SERVICE_HTTP = "http"
SERVICE_TCP = "tcp"

SERVICE_LABELS = {SERVICE_MYSQL: "MySQL", SERVICE_HTTP: "HTTP", SERVICE_TCP: "TCP"}
SERVICE_GROUPS = {
    SERVICE_MYSQL: "Databases",
    SERVICE_HTTP: "Services",
    SERVICE_TCP: "Services",
}
DEFAULT_GROUP = "Tunnels"

ENV_LABELS = {"pro": "PRO", "pre": "PRE", "dev": "DEV"}

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
    service: str = SERVICE_MYSQL
    env: str = ""
    database: str = ""

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
    def service_label(self) -> str:
        return SERVICE_LABELS.get(self.service, self.service.upper())

    @property
    def env_label(self) -> str:
        """PRO / PRE. Guessed from the project or instance name when not set."""
        if self.env:
            return ENV_LABELS.get(self.env.lower(), self.env.upper())
        haystack = f"{self.project} {self.instance} {self.command_line}".lower()
        for needle, key in (("-pre", "pre"), ("pre-", "pre"), ("-pro", "pro"), ("pro-", "pro")):
            if needle in haystack:
                return ENV_LABELS[key]
        return ""

    @property
    def is_production(self) -> bool:
        return self.env_label == "PRO"

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
        host, port = self.connect_host, self.local_port
        fields = [("Host", host, True), ("Port", str(port), True)]

        if self.service == SERVICE_MYSQL:
            client = f"mysql -h {host} -P {port} -u user -p"
            if self.database:
                client += f" {self.database}"
            fields.append(("mysql client", client, True))
            fields.append(("JDBC", f"jdbc:mysql://{host}:{port}/{self.database}", True))
            if self.database:
                fields.append(("Database", self.database, True))
        elif self.service == SERVICE_HTTP:
            fields.append(("URL", f"http://{host}:{port}", True))
        else:
            fields.append(("host:port", f"{host}:{port}", True))

        fields.append(("Target", self.target, False))
        return fields

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

        data["service"] = self.service
        if self.database:
            data["database"] = self.database
        if self.env:
            data["env"] = self.env
        data["group"] = self.group
        return data
