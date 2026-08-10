"""The side panel with everything you need to paste into a database client."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, Pango

from ..model import Tunnel
from .util import clear_box, set_clipboard


class ConnectionPanel(Gtk.Box):
    """Host, port and connection strings for the selected tunnel."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.window = window
        self.tunnel: Tunnel | None = None
        self.set_size_request(292, -1)
        for setter in (
            self.set_margin_top,
            self.set_margin_bottom,
            self.set_margin_start,
            self.set_margin_end,
        ):
            setter(14)

        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.title = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("panel-title")
        self.subtitle = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.subtitle.add_css_class("dim-label")
        self.subtitle.add_css_class("panel-subtitle")
        heading.append(self.title)
        heading.append(self.subtitle)
        self.append(heading)

        self.fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.append(self.fields)

        self.empty = Adw.StatusPage(
            icon_name="network-transmit-receive-symbolic",
            title="Connection details",
            description=(
                "Pick a tunnel from the list to see its host, port and connection "
                "string, each with a copy button."
            ),
            vexpand=True,
        )
        self.append(self.empty)

        self.show_tunnel(None)

    def show_tunnel(self, tunnel: Tunnel | None) -> None:
        self.tunnel = tunnel
        clear_box(self.fields)

        empty = tunnel is None
        self.empty.set_visible(empty)
        self.title.set_visible(not empty)
        self.subtitle.set_visible(not empty)
        self.fields.set_visible(not empty)
        if tunnel is None:
            return

        self.title.set_text(tunnel.label)
        self.subtitle.set_text(f"{tunnel.service_label} · {tunnel.project or 'kubernetes'}")
        for caption, value, copyable in tunnel.connection_fields():
            self.fields.append(self.build_field(caption, value, copyable))

    def build_field(self, caption: str, value: str, copyable: bool) -> Gtk.Widget:
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        label = Gtk.Label(label=caption.upper(), xalign=0)
        label.add_css_class("field-caption")
        wrapper.append(label)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.add_css_class("field")
        text = Gtk.Label(
            label=value,
            xalign=0,
            hexpand=True,
            selectable=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        text.add_css_class("mono")
        text.set_tooltip_text(value)
        row.append(text)

        if copyable:
            button = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.set_tooltip_text(f"Copy {caption.lower()}")
            button.connect("clicked", self.on_copy, value, caption)
            row.append(button)

        wrapper.append(row)
        return wrapper

    def on_copy(self, _button: Gtk.Button, value: str, caption: str) -> None:
        set_clipboard(self, value)
        self.window.toast(f"{caption} copied")
