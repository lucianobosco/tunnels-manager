"""Tests for the command line entry point."""

from __future__ import annotations

from tunnels_manager import cli


def test_has_display_reads_the_environment():
    assert cli.has_display({"DISPLAY": ":0"}) is True
    assert cli.has_display({"WAYLAND_DISPLAY": "wayland-0"}) is True
    assert cli.has_display({}) is False


def test_has_display_uses_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":42")
    assert cli.has_display() is True
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert cli.has_display() is False


def test_main_refuses_to_run_without_a_display(monkeypatch, capsys):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert cli.main([]) == 1
    assert "graphical session" in capsys.readouterr().err


def test_main_runs_the_application(monkeypatch):
    """The GTK part is replaced: what matters is that main wires it up and returns."""
    monkeypatch.setenv("DISPLAY", ":0")
    calls: dict[str, object] = {}

    class FakeApp:
        def __init__(self) -> None:
            self.manager = FakeManager()

        def run(self, argv):
            calls["argv"] = argv
            return 7

        def quit(self):  # pragma: no cover - only used by the signal handler
            calls["quit"] = True

    class FakeManager:
        def stop_all(self):  # pragma: no cover - only used by the signal handler
            calls["stopped"] = True

    import tunnels_manager.ui.app as app_module

    monkeypatch.setattr(app_module, "TunnelsApp", FakeApp)
    assert cli.main(["tunnels-manager"]) == 7
    assert calls["argv"] == ["tunnels-manager"]


def test_main_defaults_to_sys_argv(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(cli.sys, "argv", ["tunnels-manager", "--whatever"])
    seen: dict[str, object] = {}

    class FakeApp:
        def __init__(self) -> None:
            self.manager = None

        def run(self, argv):
            seen["argv"] = argv
            return 0

    import tunnels_manager.ui.app as app_module

    monkeypatch.setattr(app_module, "TunnelsApp", FakeApp)
    assert cli.main() == 0
    assert seen["argv"] == ["tunnels-manager", "--whatever"]


def test_signal_handler_stops_the_tunnels(monkeypatch):
    """SIGINT and SIGTERM must close the tunnels instead of orphaning them."""
    monkeypatch.setenv("DISPLAY", ":0")
    handlers: list = []
    state: dict[str, bool] = {}

    class FakeApp:
        def __init__(self) -> None:
            self.manager = self

        def stop_all(self):
            state["stopped"] = True

        def quit(self):
            state["quit"] = True

        def run(self, _argv):
            for handler in handlers:
                handler()
            return 0

    from gi.repository import GLib

    import tunnels_manager.ui.app as app_module

    monkeypatch.setattr(app_module, "TunnelsApp", FakeApp)
    monkeypatch.setattr(
        GLib, "unix_signal_add", lambda _priority, _sig, handler: handlers.append(handler)
    )
    assert cli.main([]) == 0
    assert state == {"stopped": True, "quit": True}


# -- the application id ----------------------------------------------------- #


def test_app_id_defaults_to_the_project_id(monkeypatch):
    import tunnels_manager

    monkeypatch.delenv(tunnels_manager.APP_ID_ENV, raising=False)
    assert tunnels_manager.app_id() == tunnels_manager.DEFAULT_APP_ID


def test_app_id_can_be_overridden(monkeypatch):
    """A fork installs under its own id without touching the source."""
    import tunnels_manager

    monkeypatch.setenv(tunnels_manager.APP_ID_ENV, "com.example.MyTunnels")
    assert tunnels_manager.app_id() == "com.example.MyTunnels"


def test_the_installer_default_matches_the_package(tmp_path):
    """install.sh reads the default id from the package: they cannot drift apart."""
    import re
    from pathlib import Path

    import tunnels_manager

    script = Path(__file__).resolve().parent.parent / "install.sh"
    pattern = re.search(r"sed -n 's/\^DEFAULT_APP_ID = \"(.*)\"\$/", script.read_text())
    assert pattern is not None, "install.sh no longer reads DEFAULT_APP_ID from the package"
    assert tunnels_manager.DEFAULT_APP_ID.count(".") >= 2  # reverse-DNS, as the desktop wants
