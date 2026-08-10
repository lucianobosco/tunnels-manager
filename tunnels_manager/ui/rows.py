"""One row of the tunnel table."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk, Pango

from .. import presenter
from ..model import SERVICE_MYSQL, STATE_ERROR, Tunnel
from .css import PORT_WIDTH, STATE_WIDTH, TAG_WIDTH
from .util import set_clipboard

#: Row menu: (label, action name).
ROW_ACTIONS = (
    (
        ("View log…", "win.logs"),
        ("Restart", "win.restart"),
        ("Free the port and open…", "win.free-port"),
    ),
    (("Copy host:port", "win.copy-endpoint"), ("Copy gcloud command", "win.copy-command")),
    (("Edit…", "win.edit"), ("Delete…", "win.delete")),
)


class TunnelRow(Gtk.ListBoxRow):
    """Name, badges, copyable port, state and switch."""

    def __init__(self, window, tunnel: Tunnel):
        super().__init__()
        self.window = window
        self.tunnel = tunnel
        self.add_css_class("tunnel-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        box.set_margin_top(3)
        box.set_margin_bottom(3)
        box.set_margin_start(11)
        box.set_margin_end(7)
        self.set_child(box)

        self.name = Gtk.Label(
            label=tunnel.label,
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        box.append(self.name)

        self.service_badge = Gtk.Label(label=tunnel.service_label, valign=Gtk.Align.CENTER)
        self.service_badge.add_css_class("badge")
        self.service_badge.add_css_class(
            "badge-service" if tunnel.service == SERVICE_MYSQL else "badge-generic"
        )
        self.service_badge.set_tooltip_text(f"Service: {tunnel.service_label}")
        box.append(self.service_badge)

        env = tunnel.env_label
        self.env_badge = Gtk.Label(label=env, valign=Gtk.Align.CENTER, visible=bool(env))
        self.env_badge.add_css_class("badge")
        self.env_badge.add_css_class(
            "badge-production" if tunnel.is_production else "badge-staging"
        )
        if env:
            self.env_badge.set_tooltip_text(presenter.environment_tooltip(tunnel))
        box.append(self.env_badge)

        if tunnel.exposed:
            # Spelling out "0.0.0.0" beats an icon: whoever reads it understands the risk.
            open_badge = Gtk.Label(label=tunnel.local_host, valign=Gtk.Align.CENTER)
            open_badge.add_css_class("badge")
            open_badge.add_css_class("badge-open")
            open_badge.set_tooltip_text(presenter.exposure_tooltip(tunnel))
            box.append(open_badge)

        # Always takes its slot and fades in: appearing and disappearing would move the row.
        self.clash_icon = Gtk.Image(
            icon_name="dialog-warning-symbolic", valign=Gtk.Align.CENTER, opacity=0
        )
        self.clash_icon.add_css_class("warning")
        box.append(self.clash_icon)

        self.port_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.port_button.add_css_class("flat")
        self.port_button.add_css_class("port-button")
        port_label = Gtk.Label(label=str(tunnel.local_port), xalign=1)
        port_label.add_css_class("mono")
        self.port_button.set_child(port_label)
        self.port_button.set_tooltip_text(f"Copy port {tunnel.local_port}")
        self.port_button.connect("clicked", self.on_copy_port)
        self.port_button.set_size_request(PORT_WIDTH, -1)
        box.append(self.port_button)

        menu_button = Gtk.MenuButton(
            icon_name="view-more-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="More actions",
        )
        menu_button.add_css_class("flat")
        menu_button.set_menu_model(self.build_menu())
        box.append(menu_button)

        # State next to the switch, with fixed widths: whatever the state does, no row moves.
        state_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        state_box.set_size_request(STATE_WIDTH, -1)
        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER, opacity=0)
        self.spinner.set_size_request(14, 14)
        self.tag = Gtk.Label(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.tag.set_size_request(TAG_WIDTH, -1)
        self.tag.add_css_class("tag")
        state_box.append(self.spinner)
        state_box.append(self.tag)
        box.append(state_box)

        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self.on_switch)
        box.append(self.switch)

        self.sync()

    def build_menu(self) -> Gio.Menu:
        key = GLib.Variant("s", self.tunnel.key)
        menu = Gio.Menu()
        for section_items in ROW_ACTIONS:
            section = Gio.Menu()
            for label, action in section_items:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value(action, key)
                section.append_item(item)
            menu.append_section(None, section)
        return menu

    def on_copy_port(self, _button: Gtk.Button) -> None:
        set_clipboard(self, str(self.tunnel.local_port))
        self.window.toast(f"Copied {self.tunnel.local_port}")

    def on_switch(self, switch: Gtk.Switch, _param) -> None:
        if self.window.syncing:
            return
        wants_on = switch.get_active()
        if wants_on and not self.tunnel.active:
            self.window.manager.start(self.tunnel)
        elif not wants_on and (self.tunnel.active or self.tunnel.state == STATE_ERROR):
            self.window.manager.stop(self.tunnel)

    def sync(self) -> None:
        """Push the state computed by the presenter into the widgets."""
        tunnel = self.tunnel

        clash = presenter.clash_tooltip(tunnel, self.window.conflicts)
        self.clash_icon.set_opacity(1 if clash else 0)
        if clash:
            self.clash_icon.set_tooltip_text(clash)

        text, css_class, tooltip = presenter.state_tag(tunnel)
        for css in ("state-up", "state-start", "state-error", "state-down"):
            self.tag.remove_css_class(css)
        self.tag.set_text(text)
        self.tag.add_css_class(css_class)
        self.tag.set_tooltip_text(tooltip or None)

        starting = css_class == "state-start"
        self.spinner.set_opacity(1 if starting else 0)
        self.spinner.set_spinning(starting)
        self.switch.set_active(tunnel.active)
        self.set_tooltip_text(presenter.row_tooltip(tunnel))
