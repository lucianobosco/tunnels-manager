"""Every decision the window has to make, as plain functions.

The GTK layer is deliberately thin: it creates widgets and forwards events. Anything that
computes a string, validates a form or picks what to show lives here, where it can be
tested without a display.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from .model import (
    DEFAULT_GROUP,
    STATE_ERROR,
    STATE_STARTING,
    STATE_UP,
    TYPE_COMMAND,
    TYPE_IAP,
    PortOwner,
    Tunnel,
    slugify,
)

# -- the table -------------------------------------------------------------- #


def group_tunnels(tunnels: list[Tunnel]) -> dict[str, list[Tunnel]]:
    """Split the tunnels into their groups, keeping the configured order."""
    grouped: dict[str, list[Tunnel]] = {}
    for tunnel in tunnels:
        grouped.setdefault(tunnel.group, []).append(tunnel)
    return grouped


def status_subtitle(active: int, total: int) -> str:
    """The line under the window title."""
    if active:
        return f"{active} of {total} open"
    return f"{total} tunnels · none open"


#: The four looks a row can have: the stripe, the LED and the state word share them.
KIND_UP, KIND_BUSY, KIND_ERR, KIND_OFF = "up", "busy", "err", "off"

#: A failure's second line has to fit a narrow column, so the long reason from gcloud is
#: reduced to the handful of things that actually go wrong. The full text is the tooltip.
ERROR_HINTS = (
    ("address already in use", "port in use"),
    ("already in use", "port in use"),
    ("credentials", "no credentials"),
    ("not have permission", "denied"),
    ("permission", "denied"),
    ("timed out", "timeout"),
    ("timeout", "timeout"),
    ("could not resolve", "unknown host"),
    ("not found", "not found"),
)


def error_hint(detail: str) -> str:
    """A failure in two or three words, for the line under the state."""
    lowered = detail.lower()
    for needle, hint in ERROR_HINTS:
        if needle in lowered:
            return hint
    return "see the log"


def state_card(tunnel: Tunnel) -> tuple[str, str, str, str]:
    """(kind, state word, second line, tooltip) for a row.

    The second line is never empty: an em dash holds the space so the state word cannot
    jump the moment a stopped tunnel starts connecting.
    """
    if tunnel.state == STATE_UP:
        uptime = tunnel.uptime() or "0s"
        return KIND_UP, "ESTABLISHED", uptime, f"Up for {uptime}"
    if tunnel.state == STATE_STARTING:
        return KIND_BUSY, "CONNECTING", "opening", "Waiting for the local port to open"
    if tunnel.state == STATE_ERROR:
        detail = tunnel.detail or "The tunnel failed"
        return KIND_ERR, "FAILED", error_hint(detail), detail
    return KIND_OFF, "STOPPED", "\u2014", ""


#: What a Python that cannot find its own dependencies says. If a tunnel dies with one of
#: these while the speed-up is on, the speed-up is the first thing to suspect.
IMPORT_TROUBLE = (
    "ModuleNotFoundError",
    "No module named",
    "ImportError",
    "cannot import name",
)


#: gcloud says this on every run when it cannot import NumPy, and then masks every byte
#: it carries in pure Python. It is the ceiling on a large transfer.
NUMPY_NEEDLE = "consider installing NumPy"

#: Portable on purpose: gcloud names the interpreter it actually runs, which is usually
#: its own bundled Python and not the system one -- a NumPy installed for the system
#: Python is a different version and it will never be imported.
NUMPY_INSTALL = (
    "\"$(gcloud info --format='value(basic.python_location)')\" -m pip install --user numpy"
)


def format_rtt(milliseconds: float) -> str:
    """A round trip, written so it cannot be mistaken for a broken reading.

    Through a tunnel on this machine the answer often comes back in under a millisecond,
    and "0 ms" reads like a failure rather than like the truth.
    """
    if milliseconds < 1:
        return "<1 ms"
    return f"{milliseconds:.0f} ms"


def numpy_hint(tunnel: Tunnel) -> str:
    """The command that answers the warning gcloud writes into its own log."""
    if not any(NUMPY_NEEDLE in line for line in tunnel.log):
        return ""
    return NUMPY_INSTALL


def sitepackages_warning(tunnel: Tunnel) -> str:
    """Red line for the open row: the speed-up is probably what broke this tunnel."""
    if tunnel.state != STATE_ERROR or not tunnel.site_packages:
        return ""
    if not any(needle in line for line in tunnel.log for needle in IMPORT_TROUBLE):
        return ""
    return (
        "gcloud could not import something. The NumPy speed-up lets it see this "
        "machine's Python packages, and one of them may be shadowing a dependency of "
        "the SDK -- turn it off in Edit and try again."
    )


def row_subtitle(tunnel: Tunnel) -> str:
    """The dim line under a tunnel's name.

    The design gives the row one line of free text and no badges, so everything that
    used to be a badge lives here: what it is, where it lives, which environment, and
    the listening address when it is not just localhost.
    """
    parts = [tunnel.project or "kubernetes"]
    if tunnel.exposed:
        parts.append(tunnel.local_host)
    return " \u00b7 ".join(parts)


def headline_field(tunnel: Tunnel) -> tuple[str, str]:
    """The string worth a Copy button: where the port is."""
    return "host:port", f"{tunnel.connect_host}:{tunnel.local_port}"


def route_tooltip(tunnel: Tunnel) -> str:
    """What the IAP / PORT-FWD pill explains: the whole route, in one place.

    The pill itself can only say how the tunnel is opened. Where it ends up is spread
    across the target column and the subtitle, so hovering it spells the route out.
    """
    local = f"{tunnel.local_host}:{tunnel.local_port}"
    if tunnel.type == TYPE_COMMAND:
        return f"A command opens {local} and forwards it to {tunnel.target}"
    where = f"project {tunnel.project}" if tunnel.project else "no project set"
    if tunnel.zone:
        where += f" · zone {tunnel.zone}"
    return (
        f"A Google Cloud IAP tunnel: {local} on this machine goes through the "
        f"identity-aware proxy to {tunnel.target} ({where})"
    )


def row_tooltip(tunnel: Tunnel) -> str:
    """What hovering a row explains."""
    info = f"{tunnel.target}\nproject {tunnel.project} · zone {tunnel.zone}"
    return f"{tunnel.detail}\n\n{info}" if tunnel.detail else info


def clash_tooltip(tunnel: Tunnel, conflicts: dict[int, list[Tunnel]]) -> str:
    """Which other tunnels claim the same local port. Empty when there is no clash."""
    others = [
        other.label for other in conflicts.get(tunnel.local_port, []) if other.key != tunnel.key
    ]
    if not others:
        return ""
    return f"Port {tunnel.local_port} is shared with: {', '.join(others)}"


def exposure_tooltip(tunnel: Tunnel) -> str:
    return (
        f"Listening on {tunnel.local_host}, not only on 127.0.0.1: any machine on your "
        "network can use this tunnel. Set 127.0.0.1 if you did not mean that."
    )


def banner_text(conflicts: dict[int, list[Tunnel]]) -> str:
    """The duplicated-port warning. Empty when there is nothing to warn about."""
    if not conflicts:
        return ""
    if len(conflicts) == 1:
        port, tunnels = next(iter(conflicts.items()))
        names = " and ".join(f"'{tunnel.label}'" for tunnel in tunnels)
        return f"Local port {port} is used twice: {names}. Every tunnel needs its own."
    ports = ", ".join(str(port) for port in sorted(conflicts))
    return f"{len(conflicts)} local ports are used twice: {ports}."


def bundle_message(name: str, started: int) -> str:
    if started:
        return f"'{name}': opening {started} tunnel(s)"
    return f"'{name}' was already open"


@dataclass
class BusyPortToast:
    """What to say, and whether to offer the Free it button."""

    text: str
    offer_to_free: bool


def busy_port_toast(
    tunnel: Tunnel, owner: PortOwner | None, tunnels: dict[str, Tunnel]
) -> BusyPortToast:
    if owner is not None and owner.own_tunnel:
        other = tunnels[owner.own_tunnel]
        return BusyPortToast(f"Port {tunnel.local_port} is used by '{other.label}'", True)
    if owner is not None:
        return BusyPortToast(
            f"Port {tunnel.local_port} is held by {owner.name} (PID {owner.pid})", owner.mine
        )
    return BusyPortToast(f"Port {tunnel.local_port} is busy", True)


@dataclass
class Prompt:
    """A confirmation dialog reduced to its text."""

    heading: str
    body: str


def free_port_prompt(tunnel: Tunnel, owner: PortOwner, other: Tunnel | None) -> Prompt:
    """What to ask before closing whatever holds a port."""
    port = tunnel.local_port
    if other is not None:
        return Prompt(
            f"Close '{other.label}'?",
            f"It holds port {port}, which '{tunnel.label}' needs. "
            "That tunnel is closed and this one is opened.",
        )
    body = f"{owner.name} · PID {owner.pid}\n\n{owner.short_cmd}"
    if not owner.looks_like_tunnel:
        body += (
            "\n\nHeads up: this does not look like a tunnel, so it may be something you "
            "are using for another purpose."
        )
    return Prompt(f"Close the process on port {port}?", body)


def delete_prompt(tunnel: Tunnel) -> Prompt:
    return Prompt("Delete this tunnel?", f"'{tunnel.label}' will be removed from tunnels.yaml.")


def quit_prompt(active: int) -> Prompt:
    return Prompt(
        "Close the window and every tunnel?",
        f"{active} tunnel(s) are open. Leaving closes them.",
    )


# -- forms ------------------------------------------------------------------ #


@dataclass
class TunnelForm:
    """The raw contents of the tunnel dialog."""

    label: str
    #: Empty on a new tunnel: it is then made from the name.
    key: str
    instance: str
    remote_port: str
    zone: str
    project: str
    local_host: str
    local_port: str
    #: How the tunnel is opened, which is what the row's pill shows: an IAP tunnel built
    #: from instance/zone/project, or a command that opens the local port itself.
    type: str = TYPE_IAP
    command: str = ""
    target_label: str = ""
    #: The heading it is listed under. Empty falls back to the default one.
    group: str = ""
    #: Extra gcloud flags, as typed. Split the way a shell would split them.
    extra_args: str = ""
    site_packages: bool = True


def parse_port(text: str) -> int | None:
    """A port number, or None when the text is not one."""
    try:
        value = int(text.strip())
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 65535 else None


def unique_key(base: str, taken: set[str]) -> str:
    """A config key that nobody else is using."""
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def validate_tunnel_form(
    form: TunnelForm,
    existing: list[Tunnel],
    editing: Tunnel | None = None,
    next_free_port=None,
) -> tuple[Tunnel | None, str]:
    """Turn the dialog contents into a Tunnel, or explain what is wrong."""
    is_command = form.type == TYPE_COMMAND

    local_port = parse_port(form.local_port)
    if local_port is None:
        return None, "The local port must be a number between 1 and 65535."

    label = form.label.strip()
    if not label:
        return None, "Give the tunnel a name."

    # slugify always yields something, so there is no empty-key case to guard.
    key = slugify(form.key.strip() or label)
    own_key = editing.key if editing else None
    if any(other.key == key for other in existing if other.key != own_key):
        return None, f"The key '{key}' is already taken by another tunnel."

    # A command opens the local port itself, so it needs no instance and no remote port;
    # an IAP tunnel is built out of them and cannot do without.
    command = form.command.strip()
    instance = form.instance.strip()
    zone = form.zone.strip()
    project = form.project.strip()
    remote_port = 0
    if is_command:
        if not shlex.split(command):
            return None, "Give the command that opens the port."
    else:
        parsed = parse_port(form.remote_port)
        if parsed is None:
            return None, "The remote port must be a number between 1 and 65535."
        remote_port = parsed
        if not instance or not zone or not project:
            return None, "Instance, zone and project are required."

    ignore_key = editing.key if editing else None
    clash = next(
        (
            tunnel
            for tunnel in existing
            if tunnel.local_port == local_port and tunnel.key != ignore_key
        ),
        None,
    )
    if clash is not None:
        message = (
            f"Local port {local_port} is already used by '{clash.label}'. "
            "Every tunnel needs its own."
        )
        free = next_free_port(local_port) if next_free_port else None
        if free:
            message += f" The first free one is {free}."
        return None, message

    tunnel = Tunnel(
        key=key,
        label=label,
        instance=instance,
        remote_port=remote_port,
        zone=zone,
        project=project,
        local_host=form.local_host.strip() or "127.0.0.1",
        local_port=local_port,
        # One place decides where a tunnel is listed, and it is this form.
        group=form.group.strip() or DEFAULT_GROUP,
        extra_args=shlex.split(form.extra_args),
        type=form.type,
        command_line=command,
        target_label=form.target_label.strip(),
        site_packages=form.site_packages,
    )
    return tunnel, ""


def validate_bundle(
    name: str, keys: list[str], bundles: dict[str, list[str]], original: str | None = None
) -> str:
    """Empty string when the shortcut is fine, otherwise the reason it is not."""
    name = name.strip()
    if not name:
        return "Give the shortcut a name."
    if name != original and name in bundles:
        return f"A shortcut called '{name}' already exists."
    if len(keys) < 2:
        return "Pick at least two tunnels: with one, the row switch is enough."
    return ""


def bundle_summary(keys: list[str], tunnels: dict[str, Tunnel]) -> str:
    """The subtitle of a shortcut row: the tunnels it opens."""
    return ", ".join(tunnels[key].label for key in keys if key in tunnels)


def bundle_menu_label(name: str, keys: list[str]) -> str:
    return f"Open '{name}' ({len(keys)} tunnels)"
