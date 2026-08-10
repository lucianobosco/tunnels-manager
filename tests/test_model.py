"""Tests for the data model: no processes, no GTK."""

from __future__ import annotations

import errno
import socket
import time
from pathlib import Path

import pytest

from tunnels_manager import model
from tunnels_manager.model import (
    SERVICE_HTTP,
    SERVICE_TCP,
    STATE_STARTING,
    STATE_UP,
    PortOwner,
    Tunnel,
    find_port_owner,
    port_is_free,
    slugify,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Shop (production)", "shop-production"),
        ('Read "main"', "read-main"),
        ("---", "tunnel"),
        ("", "tunnel"),
    ],
)
def test_slugify(text, expected):
    assert slugify(text) == expected


def test_port_is_free_true_and_false(free_port, listener):
    assert port_is_free("127.0.0.1", free_port) is True
    listener(free_port)
    assert port_is_free("127.0.0.1", free_port) is False


def test_port_is_free_accepts_star_as_all_interfaces(free_port):
    assert port_is_free("*", free_port) is True


def test_port_is_free_ipv6_host(free_port):
    assert port_is_free("::1", free_port) is True


def test_port_is_free_ignores_unrelated_errors(monkeypatch):
    class Boom:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def bind(self, _address):
            raise OSError(errno.EINVAL, "not a listening problem")

    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: Boom())
    assert port_is_free("127.0.0.1", 1234) is True


def test_port_is_free_reports_permission_denied_as_taken(monkeypatch):
    class Denied:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def bind(self, _address):
            raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: Denied())
    assert port_is_free("127.0.0.1", 80) is False


# -- Tunnel presentation ---------------------------------------------------- #


def test_endpoints_and_target(tunnel):
    assert tunnel.endpoint == "127.0.0.1:15001"
    assert tunnel.short_endpoint == ":15001"
    assert tunnel.target == "my-bastion:3306"
    assert tunnel.connect_host == "127.0.0.1"
    assert tunnel.exposed is False


def test_short_endpoint_keeps_the_host_when_exposed(tunnel):
    tunnel.local_host = "0.0.0.0"
    assert tunnel.short_endpoint == "0.0.0.0:15001"
    assert tunnel.exposed is True
    # You still connect through loopback.
    assert tunnel.connect_host == "127.0.0.1"


def test_localhost_is_treated_as_loopback(tunnel):
    tunnel.local_host = "localhost"
    assert tunnel.short_endpoint == ":15001"


def test_command_tunnel_target_uses_the_label_then_the_binary():
    command = Tunnel(
        key="dash",
        label="Dash",
        type=model.TYPE_COMMAND,
        command_line="kubectl -n team port-forward svc/dash 8080:80",
        local_port=8080,
        target_label="svc/dash:80",
    )
    assert command.target == "svc/dash:80"
    command.target_label = ""
    assert command.target == "kubectl"
    command.command_line = ""
    assert command.target == ""


def test_service_label_falls_back_to_uppercase(tunnel):
    assert tunnel.service_label == "MySQL"
    tunnel.service = "postgres"
    assert tunnel.service_label == "POSTGRES"


@pytest.mark.parametrize(
    ("env", "project", "expected"),
    [
        ("pro", "", "PRO"),
        ("pre", "", "PRE"),
        ("dev", "", "DEV"),
        ("staging", "", "STAGING"),
        ("", "my-project-pro", "PRO"),
        ("", "my-project-pre", "PRE"),
        ("", "pre-things", "PRE"),
        ("", "pro-things", "PRO"),
        ("", "neutral", ""),
    ],
)
def test_env_label(tunnel, env, project, expected):
    tunnel.env = env
    tunnel.project = project
    tunnel.instance = ""
    assert tunnel.env_label == expected


def test_is_production(tunnel):
    tunnel.env = "pro"
    assert tunnel.is_production is True
    tunnel.env = "pre"
    assert tunnel.is_production is False


def test_active_states(tunnel):
    assert tunnel.active is False
    tunnel.state = STATE_STARTING
    assert tunnel.active is True
    tunnel.state = STATE_UP
    assert tunnel.active is True


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (45, "45s"), (60, "1m"), (3599, "59m"), (3600, "1h 00m"), (3725, "1h 02m")],
)
def test_uptime_formats(tunnel, seconds, expected):
    tunnel.state = STATE_UP
    tunnel.started_at = time.time() - seconds
    assert tunnel.uptime() == expected


def test_uptime_is_empty_unless_up(tunnel):
    assert tunnel.uptime() == ""
    tunnel.state = STATE_UP
    tunnel.started_at = None
    assert tunnel.uptime() == ""


# -- connection fields ------------------------------------------------------ #


def test_connection_fields_for_mysql_with_database(tunnel):
    fields = {caption: value for caption, value, _ in tunnel.connection_fields()}
    assert fields["Host"] == "127.0.0.1"
    assert fields["Port"] == "15001"
    assert fields["mysql client"] == "mysql -h 127.0.0.1 -P 15001 -u user -p shop"
    assert fields["JDBC"] == "jdbc:mysql://127.0.0.1:15001/shop"
    assert fields["Database"] == "shop"
    assert fields["Target"] == "my-bastion:3306"


def test_connection_fields_for_mysql_without_database(tunnel):
    tunnel.database = ""
    captions = [caption for caption, _, _ in tunnel.connection_fields()]
    assert "Database" not in captions
    fields = {caption: value for caption, value, _ in tunnel.connection_fields()}
    assert fields["mysql client"] == "mysql -h 127.0.0.1 -P 15001 -u user -p"
    assert fields["JDBC"] == "jdbc:mysql://127.0.0.1:15001/"


def test_connection_fields_for_http(tunnel):
    tunnel.service = SERVICE_HTTP
    fields = {caption: value for caption, value, _ in tunnel.connection_fields()}
    assert fields["URL"] == "http://127.0.0.1:15001"
    assert "JDBC" not in fields


def test_connection_fields_for_plain_tcp(tunnel):
    tunnel.service = SERVICE_TCP
    fields = {caption: value for caption, value, _ in tunnel.connection_fields()}
    assert fields["host:port"] == "127.0.0.1:15001"


def test_target_field_is_not_copyable(tunnel):
    copyable = {caption: flag for caption, _, flag in tunnel.connection_fields()}
    assert copyable["Target"] is False
    assert copyable["Host"] is True


# -- commands --------------------------------------------------------------- #


def test_iap_command(tunnel):
    assert tunnel.command() == [
        "gcloud",
        "compute",
        "start-iap-tunnel",
        "my-bastion",
        "3306",
        "--zone=europe-west1-d",
        "--project=my-project-pro",
        "--local-host-port=127.0.0.1:15001",
    ]
    assert tunnel.command_str().startswith("gcloud compute start-iap-tunnel my-bastion 3306")


def test_iap_command_appends_extra_args(tunnel):
    tunnel.extra_args = ["--verbosity=debug", 7]
    assert tunnel.command()[-2:] == ["--verbosity=debug", "7"]


def test_command_tunnel_is_split_with_shell_rules():
    command = Tunnel(
        key="dash",
        label="Dash",
        type=model.TYPE_COMMAND,
        command_line='kubectl -n "my team" port-forward svc/dash 8080:80',
        local_port=8080,
    )
    assert command.command() == [
        "kubectl",
        "-n",
        "my team",
        "port-forward",
        "svc/dash",
        "8080:80",
    ]


def test_hint_for_known_and_unknown_lines(tunnel):
    assert "gcloud auth login" in tunnel.hint_for("ERROR: Reauthentication required")
    assert tunnel.hint_for("just a log line") == ""


def test_last_meaningful_line_skips_noise(tunnel):
    assert tunnel.last_meaningful_line() == "no output from the command"
    tunnel.log.append("$ gcloud compute start-iap-tunnel …")
    tunnel.log.append("Testing if tunnel connection works.")
    tunnel.log.append("   ")
    assert tunnel.last_meaningful_line() == "no output from the command"
    tunnel.log.append("ERROR: something broke")
    assert tunnel.last_meaningful_line() == "ERROR: something broke"


def test_last_meaningful_line_is_truncated(tunnel):
    tunnel.log.append("x" * 400)
    assert len(tunnel.last_meaningful_line()) == 200


# -- config dict ------------------------------------------------------------ #


def test_config_dict_for_iap(tunnel):
    tunnel.env = "pro"
    tunnel.extra_args = ["--flag"]
    data = tunnel.config_dict()
    assert data["instance"] == "my-bastion"
    assert data["extra_args"] == ["--flag"]
    assert data["database"] == "shop"
    assert data["env"] == "pro"
    assert data["group"] == "Databases"
    assert "command" not in data


def test_config_dict_for_command_tunnel():
    command = Tunnel(
        key="dash",
        label="Dash",
        type=model.TYPE_COMMAND,
        command_line="kubectl port-forward svc/dash 8080:80",
        local_port=8080,
        target_label="svc/dash:80",
        service=SERVICE_HTTP,
        group="Services",
    )
    data = command.config_dict()
    assert data["type"] == "command"
    assert data["command"] == "kubectl port-forward svc/dash 8080:80"
    assert data["target_label"] == "svc/dash:80"
    assert "instance" not in data
    assert "env" not in data


def test_config_dict_omits_empty_optionals(tunnel):
    tunnel.database = ""
    tunnel.env = ""
    tunnel.extra_args = []
    data = tunnel.config_dict()
    assert "database" not in data
    assert "env" not in data
    assert "extra_args" not in data


def test_config_dict_for_command_without_target_label():
    command = Tunnel(
        key="dash",
        label="Dash",
        type=model.TYPE_COMMAND,
        command_line="kubectl port-forward svc/dash 8080:80",
        local_port=8080,
    )
    assert "target_label" not in command.config_dict()


# -- PortOwner and find_port_owner ------------------------------------------ #


def test_port_owner_looks_like_tunnel():
    tunnel_like = PortOwner(1, "python3", "gcloud compute start-iap-tunnel foo 3306", True)
    other = PortOwner(2, "python3", "python3 -m http.server 8000", True)
    assert tunnel_like.looks_like_tunnel is True
    assert other.looks_like_tunnel is False


def test_port_owner_short_cmd_truncates():
    owner = PortOwner(1, "x", "y" * 400, True)
    assert len(owner.short_cmd) == 158
    assert owner.short_cmd.endswith("…")
    short = PortOwner(1, "x", "ssh -L 1:2:3", True)
    assert short.short_cmd == "ssh -L 1:2:3"


def test_find_port_owner_reads_ss_and_proc(free_port, listener):
    proc = listener(free_port)
    owner = find_port_owner(free_port)
    assert owner is not None
    assert owner.pid == proc.pid
    assert owner.mine is True
    assert "socket" in owner.cmdline or "python" in owner.cmdline.lower()


def test_find_port_owner_returns_none_when_free(free_port):
    assert find_port_owner(free_port) is None


def test_find_port_owner_survives_a_missing_ss(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("no ss here")

    monkeypatch.setattr(model.subprocess, "run", boom)
    assert find_port_owner(1234) is None


def test_find_port_owner_falls_back_when_proc_is_gone(monkeypatch):
    class Result:
        stdout = 'LISTEN 0 5 127.0.0.1:9 0.0.0.0:* users:(("ghost",pid=999999,fd=7))'

    monkeypatch.setattr(model.subprocess, "run", lambda *_a, **_k: Result())
    owner = find_port_owner(9)
    assert owner is not None
    assert owner.pid == 999999
    assert owner.cmdline == "ghost"
    assert owner.mine is True


def test_find_port_owner_detects_another_user(monkeypatch, tmp_path):
    class Result:
        stdout = 'LISTEN 0 5 127.0.0.1:9 0.0.0.0:* users:(("root-thing",pid=4242,fd=7))'

    monkeypatch.setattr(model.subprocess, "run", lambda *_a, **_k: Result())

    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text

    def fake_read_bytes(self, *args, **kwargs):
        if str(self) == "/proc/4242/cmdline":
            return b"/usr/sbin/some-daemon\0--flag\0"
        return real_read_bytes(self, *args, **kwargs)

    def fake_read_text(self, *args, **kwargs):
        if str(self) == "/proc/4242/status":
            return "Name:\tsome-daemon\nUid:\t0\t0\t0\t0\n"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    monkeypatch.setattr(Path, "read_text", fake_read_text)

    owner = find_port_owner(9)
    assert owner is not None
    assert owner.mine is False
    assert owner.cmdline == "/usr/sbin/some-daemon --flag"


def test_find_port_owner_handles_unreadable_status(monkeypatch):
    class Result:
        stdout = 'LISTEN 0 5 127.0.0.1:9 0.0.0.0:* users:(("thing",pid=4243,fd=7))'

    monkeypatch.setattr(model.subprocess, "run", lambda *_a, **_k: Result())

    real_read_text = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if str(self) == "/proc/4243/status":
            return "Uid:\tnot-a-number\n"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    owner = find_port_owner(9)
    assert owner is not None
    assert owner.mine is True


def test_find_port_owner_ignores_output_without_a_pid(monkeypatch):
    class Result:
        stdout = "LISTEN 0 5 127.0.0.1:9 0.0.0.0:*"

    monkeypatch.setattr(model.subprocess, "run", lambda *_a, **_k: Result())
    assert find_port_owner(9) is None


def test_find_port_owner_when_status_has_no_uid_line(monkeypatch):
    """A /proc status without a Uid line leaves ownership at its optimistic default."""

    class Result:
        stdout = 'LISTEN 0 5 127.0.0.1:9 0.0.0.0:* users:(("thing",pid=4244,fd=7))'

    monkeypatch.setattr(model.subprocess, "run", lambda *_a, **_k: Result())

    real_read_text = Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if str(self) == "/proc/4244/status":
            return "Name:\tthing\nState:\tS (sleeping)\n"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    owner = find_port_owner(9)
    assert owner is not None
    assert owner.mine is True
