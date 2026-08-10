"""Command line entry points: the app itself and the Ásbrú importer."""

from __future__ import annotations

import os
import sys


def has_display(environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    """Run the GTK application."""
    args = sys.argv if argv is None else argv
    # It is a graphical app: with no display, a clear line beats a GTK traceback.
    if not has_display():
        print(
            "tunnels-manager needs a graphical session and there is none here "
            "(neither DISPLAY nor WAYLAND_DISPLAY).",
            file=sys.stderr,
        )
        return 1

    import signal

    from gi.repository import GLib

    from .ui.app import TunnelsApp

    app = TunnelsApp()

    def on_signal(*_args):
        app.manager.stop_all()
        app.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, on_signal)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, on_signal)
    return app.run(args)
