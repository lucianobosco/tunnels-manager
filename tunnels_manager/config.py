"""Reading and writing tunnels.yaml.

The user's own configuration lives in ~/.config/tunnels-manager/tunnels.yaml and is never
part of the repository; tunnels.dist.yaml ships as an example and seeds the first run.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import yaml

from .model import (
    DEFAULT_GROUP,
    TYPE_COMMAND,
    TYPE_IAP,
    Tunnel,
)

APP_DIR_NAME = "tunnels-manager"


def config_home() -> Path:
    """XDG config home, read at call time so tests can point it elsewhere."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def config_dir() -> Path:
    return config_home() / APP_DIR_NAME


def config_file() -> Path:
    return config_dir() / "tunnels.yaml"


def example_file() -> Path:
    """The example shipped with the source tree, resolved through the ~/.local/bin symlink."""
    return Path(__file__).resolve().parent.parent / "tunnels.dist.yaml"


#: Rewritten on every save, so editing from the app never leaves the file undocumented.
CONFIG_HEADER = """\
# Tunnels Manager configuration. Safe to edit by hand: press Ctrl+R in the app to reload.
#
# type: iap (the default) is equivalent to:
#   gcloud compute start-iap-tunnel <instance> <remote_port> \\
#     --zone=<zone> --project=<project> --local-host-port=<local_host>:<local_port>
# type: command runs 'command' as it is (kubectl port-forward, ssh -L, …) and watches
#   'local_port' to know whether the tunnel is up.
#
# group: the heading the tunnel is listed under. Absent, it goes under "Tunnels".
# site_packages: false keeps gcloud from using NumPy for this tunnel.
# bundles: shortcuts that open several tunnels at once, by their 'key'.
#   Manage them in the app: main menu -> Shortcuts -> Manage shortcuts…

"""

#: Minimal generic template, used when tunnels.dist.yaml is not next to the package.
FALLBACK_CONFIG = (
    CONFIG_HEADER
    + """\
tunnels:
  - key: my-database
    label: My database
    instance: my-bastion
    remote_port: 3306
    zone: europe-west1-d
    project: my-gcp-project
    local_host: 127.0.0.1
    local_port: 13306
"""
)


def ensure_config_file() -> str | None:
    """Create the config file on first run. Returns a note to show, or None."""
    target = config_file()
    if target.exists():
        return None
    config_dir().mkdir(parents=True, exist_ok=True)
    try:
        seed = example_file().read_text(encoding="utf-8")
        source = example_file().name
    except OSError:
        seed = FALLBACK_CONFIG
        source = "the built-in template"
    target.write_text(seed, encoding="utf-8")
    return f"Created {target} from {source}: replace the example tunnels with yours."


def read_raw() -> tuple[dict, list[str]]:
    """Parse the YAML file. Returns (data, warnings)."""
    warnings: list[str] = []
    note = ensure_config_file()
    if note:
        warnings.append(note)
    try:
        data = yaml.safe_load(config_file().read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        return {}, [*warnings, f"tunnels.yaml could not be read: {exc}"]
    if not isinstance(data, dict):
        return {}, [*warnings, "tunnels.yaml should contain a mapping with a 'tunnels' key."]
    return data, warnings


def parse_tunnels(raw: dict) -> tuple[list[Tunnel], list[str]]:
    """Build Tunnel objects from raw YAML data, collecting warnings for bad entries."""
    warnings: list[str] = []
    tunnels: list[Tunnel] = []
    seen_keys: set[str] = set()

    for index, entry in enumerate(raw.get("tunnels") or [], start=1):
        if not isinstance(entry, dict):
            warnings.append(f"Entry #{index} ignored: it is not a mapping.")
            continue

        kind = str(entry.get("type") or TYPE_IAP).lower()
        # Spelled out rather than **kwargs so the types are checkable.
        key = str(entry.get("key") or f"tunnel-{index}")
        label = str(entry.get("label") or entry.get("key") or f"Tunnel {index}")
        local_host = str(entry.get("local_host", "127.0.0.1"))
        # Whatever heading the file says, or the default one.
        group = str(entry.get("group") or DEFAULT_GROUP)
        # Absent means on: the speed-up is the default, and the file only records a no.
        site_packages = bool(entry.get("site_packages", True))

        try:
            if kind == TYPE_COMMAND:
                command_line = str(entry["command"]).strip()
                if not shlex.split(command_line):
                    raise ValueError("an empty command")
                tunnel = Tunnel(
                    key=key,
                    label=label,
                    local_host=local_host,
                    group=group,
                    type=TYPE_COMMAND,
                    site_packages=site_packages,
                    command_line=command_line,
                    local_port=int(entry["local_port"]),
                    target_label=str(entry.get("target_label") or ""),
                )
            else:
                tunnel = Tunnel(
                    key=key,
                    label=label,
                    local_host=local_host,
                    group=group,
                    site_packages=site_packages,
                    instance=str(entry["instance"]),
                    remote_port=int(entry["remote_port"]),
                    zone=str(entry["zone"]),
                    project=str(entry["project"]),
                    local_port=int(entry["local_port"]),
                    extra_args=list(entry.get("extra_args") or []),
                )
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"Entry #{index} ignored: missing or invalid {exc}.")
            continue

        if tunnel.key in seen_keys:
            warnings.append(f"Duplicate key '{tunnel.key}': the repeated entry is ignored.")
            continue
        seen_keys.add(tunnel.key)
        tunnels.append(tunnel)

    return tunnels, warnings


def parse_bundles(raw: dict, known_keys: set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Read the shortcuts, warning about keys that do not exist."""
    warnings: list[str] = []
    bundles: dict[str, list[str]] = {}

    for name, keys in (raw.get("bundles") or {}).items():
        if not isinstance(keys, list):
            warnings.append(f"Shortcut '{name}' must be a list of tunnel keys.")
            continue
        known = [str(key) for key in keys if str(key) in known_keys]
        unknown = [str(key) for key in keys if str(key) not in known_keys]
        if unknown:
            # Mistyping a key is the easy mistake to make when editing by hand.
            warnings.append(
                f"Shortcut '{name}' points at keys that do not exist: {', '.join(unknown)}."
            )
        if known:
            bundles[str(name)] = known

    return bundles, warnings


def dump(tunnels: list[Tunnel], bundles: dict[str, list[str]]) -> str:
    """Serialise the configuration, keeping the documented header."""
    payload: dict = {"tunnels": [tunnel.config_dict() for tunnel in tunnels]}
    if bundles:
        payload["bundles"] = {name: list(keys) for name, keys in bundles.items()}
    body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=100)
    return CONFIG_HEADER + body


def write(tunnels: list[Tunnel], bundles: dict[str, list[str]]) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    config_file().write_text(dump(tunnels, bundles), encoding="utf-8")
