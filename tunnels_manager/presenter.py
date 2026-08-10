"""Every decision the window has to make, as plain functions.

The GTK layer is deliberately thin: it creates widgets and forwards events. Anything that
computes a string, validates a form or picks what to show lives here, where it can be
tested without a display.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    DEFAULT_GROUP,
    SERVICE_GROUPS,
    STATE_ERROR,
    STATE_STARTING,
    STATE_UP,
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


def state_tag(tunnel: Tunnel) -> tuple[str, str, str]:
    """(text, css class, tooltip) for the state pill of a row."""
    if tunnel.state == STATE_UP:
        uptime = tunnel.uptime()
        return uptime or "0s", "state-up", f"Up for {uptime}"
    if tunnel.state == STATE_STARTING:
        return "opening", "state-start", "Waiting for the local port to open"
    if tunnel.state == STATE_ERROR:
        return "error", "state-error", tunnel.detail or "The tunnel failed"
    return "stopped", "state-down", ""


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


def environment_tooltip(tunnel: Tunnel) -> str:
    return "Production" if tunnel.is_production else f"{tunnel.env_label} environment"


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


def first_selectable_key(order: list[str], available: set[str]) -> str | None:
    """Which tunnel to select when nothing is selected yet."""
    return next((key for key in order if key in available), None)


# -- messages --------------------------------------------------------------- #


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
    service: str
    database: str
    env: str
    instance: str
    remote_port: str
    zone: str
    project: str
    local_host: str
    local_port: str


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
    remote_port = parse_port(form.remote_port)
    local_port = parse_port(form.local_port)
    if remote_port is None or local_port is None:
        return None, "Ports must be numbers between 1 and 65535."

    label = form.label.strip()
    if not label:
        return None, "Give the tunnel a name."

    instance = form.instance.strip()
    zone = form.zone.strip()
    project = form.project.strip()
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

    service = form.service
    tunnel = Tunnel(
        key=editing.key if editing else slugify(label),
        label=label,
        instance=instance,
        remote_port=remote_port,
        zone=zone,
        project=project,
        local_host=form.local_host.strip() or "127.0.0.1",
        local_port=local_port,
        group=SERVICE_GROUPS.get(service, DEFAULT_GROUP),
        extra_args=list(editing.extra_args) if editing else [],
        service=service,
        env=form.env,
        database=form.database.strip(),
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
