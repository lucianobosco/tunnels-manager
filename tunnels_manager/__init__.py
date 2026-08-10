"""Tunnels Manager: a small GTK4 front end for gcloud IAP tunnels and port-forwards."""

from __future__ import annotations

import os

__version__ = "0.1.0"

APP_NAME = "Tunnels Manager"
PROJECT_URL = "https://github.com/lucianobosco/tunnels-manager"

#: Desktop application id. It identifies the project, the way org.gnome.Nautilus does, and
#: it has to be globally unique: the desktop uses it for the icon name, the D-Bus name that
#: keeps a single instance, and to match a window to its launcher.
#:
#: A fork can install under its own id without touching the source:
#:   APP_ID=com.example.MyTunnels ./install.sh
#: install.sh names the installed files after it and passes it back through this variable.
DEFAULT_APP_ID = "io.github.lucianobosco.TunnelsManager"
APP_ID_ENV = "TUNNELS_MANAGER_APP_ID"


def app_id() -> str:
    """The id this instance runs under."""
    return os.environ.get(APP_ID_ENV) or DEFAULT_APP_ID
