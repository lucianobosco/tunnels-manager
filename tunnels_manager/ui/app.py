"""The GTK application object."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk

from .. import app_id
from ..manager import TunnelManager
from .css import CSS
from .window import MainWindow

ACCELERATORS = {
    "app.quit": ["<Primary>q"],
    "win.reload": ["<Primary>r"],
    "win.add": ["<Primary>n"],
    "win.toggle-panel": ["<Primary>i"],
}


class TunnelsApp(Adw.Application):
    """One instance per session: opening the app again focuses the existing window."""

    def __init__(self, application_id: str | None = None):
        super().__init__(
            application_id=application_id or app_id(),
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.manager = TunnelManager()
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        if display is not None:
            provider = Gtk.CssProvider()
            provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", self.on_quit)
        self.add_action(quit_action)
        for action, accels in ACCELERATORS.items():
            self.set_accels_for_action(action, accels)

    def on_quit(self, *_args) -> None:
        if self.window is not None:
            self.window.close()

    def do_activate(self) -> None:
        if self.window is None:
            warnings = self.manager.load_config()
            self.window = MainWindow(self, self.manager)
            for warning in warnings:
                self.window.toast(warning)
        self.window.present()

    def do_shutdown(self) -> None:
        # Covers the exits that skip the close button (Ctrl+Q, SIGTERM, logout).
        if self.window is not None:
            self.window.shutdown()
        else:
            self.manager.stop_all()
        Adw.Application.do_shutdown(self)
