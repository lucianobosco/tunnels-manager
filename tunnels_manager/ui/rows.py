"""One row of the tunnel table: a card with a state stripe down its left edge.

Every column is a fixed-width slot except the target, which absorbs the window's spare
width. That keeps the columns lined up across rows no matter how wide the window is, and
it is why a state change can never shift anything sideways.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk, Pango

from .. import presenter
from ..model import STATE_ERROR, STATE_UP, TYPE_COMMAND, Tunnel, measure_rtt
from .css import (
    CHEVRON_GAP,
    EDGE,
    GUTTER,
    LED_MARGIN,
    LED_SIZE,
    MENU_WIDTH,
    NAME_GAP,
    NAME_WIDTH,
    PILL_WIDTH,
    PORT_WIDTH,
    ROW_HEIGHT,
    STATE_WIDTH,
    STRIPE_WIDTH,
    TARGET_MIN,
)
from .topology import DEAD, FLOWING, IDLE, LIVE, Node, Sparkline, link
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

#: How often an established tunnel is measured. Each measurement is one TCP connection to
#: the far end, closed without authenticating, which a database counts as an aborted
#: client: 15 s is four a minute per open tunnel, and none at all while it is closed.
PROBE_SECONDS = 15

#: Every look a row can take, so switching state is a matter of removing all of them
#: and adding one.
KINDS = (presenter.KIND_UP, presenter.KIND_BUSY, presenter.KIND_ERR, presenter.KIND_OFF)


def clickable(widget: Gtk.Widget) -> Gtk.Widget:
    """Anything you can click says so under the pointer. GTK4 has no CSS cursor, so the
    widget is told directly."""
    widget.set_cursor_from_name("pointer")
    return widget


def cell_label(text: str, *classes: str) -> Gtk.Label:
    """A column's text: it ellipsises rather than push the column next to it."""
    widget = Gtk.Label(
        label=text,
        xalign=0,
        halign=Gtk.Align.START,
        valign=Gtk.Align.CENTER,
        ellipsize=Pango.EllipsizeMode.END,
    )
    for name in classes:
        widget.add_css_class(name)
    return widget


def slot(width: int, child: Gtk.Widget, align: str = "start", grow: bool = False) -> Gtk.Box:
    """A column. The slot owns the width; the content is always centred vertically.

    A centred column keeps no gutter: a margin on one side only would push it off centre
    by half of itself.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=grow)
    box.set_size_request(width, -1)
    child.set_valign(Gtk.Align.CENTER)
    child.set_hexpand(True)

    if align == "center":
        child.set_halign(Gtk.Align.CENTER)
    elif align == "end":
        child.set_margin_end(GUTTER)
        child.set_halign(Gtk.Align.FILL)
        if isinstance(child, Gtk.Label):
            child.set_xalign(1)
    else:
        child.set_margin_end(GUTTER)
        child.set_halign(Gtk.Align.START)
    box.append(child)
    return box


class TunnelRow(Gtk.ListBoxRow):
    """Name, pills, copyable port, target, state and switch."""

    def __init__(self, window, tunnel: Tunnel):
        super().__init__()
        self.window = window
        self.tunnel = tunnel
        self.add_css_class("tunnel-row")
        # Not activatable: the click that opens the row is listened for on the header
        # alone, so clicking inside the panel it reveals -- to select a connection
        # string, to press Copy -- cannot close it again.
        self.set_activatable(False)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.set_child(outer)

        self.stripe = Gtk.Box(vexpand=True)
        self.stripe.add_css_class("stripe")
        self.stripe.set_size_request(STRIPE_WIDTH, -1)
        outer.append(self.stripe)

        # The card is a column: the header, and under it the details the row reveals.
        self.shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        self.shell.add_css_class("tunnel-card")
        outer.append(self.shell)

        self.card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, hexpand=True)
        self.card.set_size_request(-1, ROW_HEIGHT)
        self.shell.append(self.card)
        clickable(self.card)
        header_click = Gtk.GestureClick()
        header_click.connect("released", self.on_header_clicked)
        self.card.add_controller(header_click)

        self.led = Gtk.Box(valign=Gtk.Align.CENTER)
        self.led.add_css_class("led")
        self.led.set_size_request(LED_SIZE, LED_SIZE)
        self.led.set_margin_start(LED_MARGIN)
        self.card.append(self.led)

        self.card.append(self.build_name())
        self.card.append(self.build_pills())
        self.card.append(self.build_port())

        self.target = cell_label(tunnel.target, "target")
        self.card.append(slot(TARGET_MIN, self.target, grow=True))

        self.card.append(self.build_state())
        self.card.append(self.build_menu_button())

        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.switch.connect("notify::active", self.on_switch)
        clickable(self.switch)
        self.card.append(self.switch)

        # Says the row opens, and which way it is now. pan-end / pan-down is what
        # libadwaita's own expander uses.
        self.chevron = Gtk.Image(icon_name="pan-end-symbolic", valign=Gtk.Align.CENTER)
        self.chevron.add_css_class("chevron")
        self.chevron.set_margin_start(CHEVRON_GAP)
        self.chevron.set_margin_end(EDGE)
        self.card.append(self.chevron)

        self.probing = False
        self.probe_id = 0
        self.last_rtt: float | None = None
        self.details = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.details.set_child(self.build_details())
        self.shell.append(self.details)

        self.sync()

    def on_header_clicked(self, _gesture, n_press: int, _x: float, _y: float) -> None:
        # Buttons and the switch claim the click before it reaches here.
        if n_press == 1:
            self.toggle_details()

    def toggle_details(self) -> None:
        """Clicking the row opens the path the tunnel takes."""
        opening = not self.details.get_reveal_child()
        self.details.set_reveal_child(opening)
        self.chevron.set_from_icon_name("pan-down-symbolic" if opening else "pan-end-symbolic")
        # A handshake is only worth animating while somebody is looking at it.
        for wire in self.wires:
            wire.set_watched(opening)

    # -- the columns -------------------------------------------------------- #

    def build_name(self) -> Gtk.Widget:
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=1,
            valign=Gtk.Align.CENTER,
            hexpand=False,
        )
        box.set_size_request(NAME_WIDTH, -1)
        box.set_margin_start(NAME_GAP)

        box.append(cell_label(self.tunnel.label, "row-title"))
        self.subtitle = cell_label(presenter.row_subtitle(self.tunnel), "row-sub")
        box.append(self.subtitle)
        return box

    def build_pills(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5, halign=Gtk.Align.CENTER)
        box.set_size_request(PILL_WIDTH, -1)
        box.set_valign(Gtk.Align.CENTER)

        kind = "PORT-FWD" if self.tunnel.type == TYPE_COMMAND else "IAP"
        pill = Gtk.Label(label=kind, valign=Gtk.Align.CENTER)
        pill.add_css_class("pill")
        pill.add_css_class("pill-iap" if self.tunnel.type != TYPE_COMMAND else "pill-fwd")
        pill.set_tooltip_text(presenter.route_tooltip(self.tunnel))
        box.append(pill)

        return box

    def build_port(self) -> Gtk.Widget:
        self.port_button = Gtk.Button(valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.port_button.add_css_class("flat")
        self.port_button.add_css_class("port-button")
        port_label = Gtk.Label(label=f":{self.tunnel.local_port}", xalign=1)
        port_label.add_css_class("port")
        self.port_button.set_child(port_label)
        self.port_button.set_tooltip_text(f"Copy port {self.tunnel.local_port}")
        self.port_button.connect("clicked", self.on_copy_port)
        clickable(self.port_button)
        return slot(PORT_WIDTH, self.port_button, align="end")

    def build_state(self) -> Gtk.Widget:
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER)
        self.state_label = cell_label("", "state")
        self.substate_label = cell_label("", "substate")
        stack.append(self.state_label)
        # The second line always exists, an em dash when there is nothing to say, so the
        # state word cannot jump the moment a stopped tunnel starts connecting.
        stack.append(self.substate_label)
        return slot(STATE_WIDTH, stack)

    def build_details(self) -> Gtk.Widget:
        """The path: this machine, the proxy, the far end -- and the string to paste."""
        tunnel = self.tunnel
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.add_css_class("details")

        self.warning_label = cell_label("", "details-warning")
        self.warning_label.set_wrap(True)
        self.warning_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.warning_label.set_visible(False)
        box.append(self.warning_label)

        self.hint_box = self.build_hint()
        box.append(self.hint_box)

        path = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        path.append(Node("localhost", f"{tunnel.local_host} · :{tunnel.local_port}"))

        if tunnel.type == TYPE_COMMAND:
            first, self.wire_left = link("port-forward", tunnel.command_line.split(" ")[0], leg=0)
            middle = Node("local process", "no proxy in the middle", locked=True)
            second, self.wire_right = link("", tunnel.target, leg=1)
            self.far_node = Node(tunnel.target_label or tunnel.target, "kubernetes", stateful=True)
        else:
            first, self.wire_left = link("gcloud iap", "oauth \u2197 tcp:443", leg=0)
            # FAKE_RTT_MS: invented until something measures the hop.
            middle = Node("identity-aware proxy", "gcloud iap", locked=True)
            second, self.wire_right = link("", f":{tunnel.remote_port}", leg=1)
            self.far_node = Node(
                tunnel.instance, f"{tunnel.project} · {tunnel.zone}", stateful=True
            )

        self.proxy_node = middle
        self.wires = [self.wire_left, self.wire_right]
        path.append(first)
        path.append(middle)
        path.append(second)
        path.append(self.far_node)
        box.append(path)

        box.append(self.build_connection_line())
        return box

    def build_hint(self) -> Gtk.Widget:
        """What gcloud asked for, with the command that gives it to it."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, visible=False)
        text = cell_label(
            "gcloud is masking every byte in pure Python. NumPy is missing for the "
            "interpreter it runs:",
            "details-hint",
        )
        text.set_wrap(True)
        text.set_ellipsize(Pango.EllipsizeMode.NONE)
        box.append(text)

        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        command = Gtk.Label(
            label=presenter.NUMPY_INSTALL,
            xalign=0,
            hexpand=True,
            selectable=True,
            valign=Gtk.Align.CENTER,
            ellipsize=Pango.EllipsizeMode.END,
        )
        command.add_css_class("details-hint-cmd")
        command.set_tooltip_text(presenter.NUMPY_INSTALL)
        line.append(command)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
        copy.add_css_class("flat")
        copy.set_tooltip_text("Copy the command")
        copy.connect("clicked", self.on_copy_field, presenter.NUMPY_INSTALL, "Command")
        clickable(copy)
        line.append(copy)
        box.append(line)
        return box

    def build_connection_line(self) -> Gtk.Widget:
        caption, value = presenter.headline_field(self.tunnel)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, hexpand=True)
        field.add_css_class("conn-field")
        text = Gtk.Label(
            label=value,
            xalign=0,
            hexpand=True,
            selectable=True,
            valign=Gtk.Align.CENTER,
            ellipsize=Pango.EllipsizeMode.END,
        )
        text.add_css_class("field-value")
        text.set_tooltip_text(value)
        field.append(text)
        self.conn_state = Gtk.Label(label="", valign=Gtk.Align.CENTER)
        self.conn_state.add_css_class("conn-state")
        field.append(self.conn_state)
        line.append(field)

        copy = Gtk.Button(label="Copy", valign=Gtk.Align.CENTER)
        copy.add_css_class("copy-button")
        copy.set_tooltip_text(f"Copy the {caption}")
        copy.connect("clicked", self.on_copy_field, value, caption)
        clickable(copy)
        line.append(copy)

        self.rtt_box = rtt = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0, valign=Gtk.Align.CENTER
        )
        self.rtt_label = Gtk.Label(label="rtt \u2014", xalign=1)
        self.rtt_label.add_css_class("wire-label")
        self.rtt_label.set_tooltip_text(
            f"Time for the far end to answer, measured through the tunnel every {PROBE_SECONDS} s"
        )
        self.spark = Sparkline()
        rtt.append(self.spark)
        rtt.append(self.rtt_label)
        line.append(rtt)
        return line

    # -- measuring ---------------------------------------------------------- #

    def probe_now(self) -> None:
        """Measure off the main loop: a blocking socket in it would freeze the window."""
        if self.probing:
            return
        self.probing = True
        args = (self.tunnel.connect_host, self.tunnel.local_port)
        threading.Thread(target=self.probe_worker, args=args, daemon=True).start()

    def probe_worker(self, host: str, port: int) -> None:
        result = measure_rtt(host, port)
        GLib.idle_add(self.probe_done, result)

    def sync_proxy_subtitle(self) -> None:
        """The box in the middle reports the last figure measured through it."""
        kind, word, _second, _tooltip = presenter.state_card(self.tunnel)
        if kind == presenter.KIND_OFF:
            self.proxy_node.set_subtitle("gcloud iap")
        elif kind == presenter.KIND_UP and self.last_rtt is not None:
            # What the design writes here: the tunnel's state and the last figure.
            self.proxy_node.set_subtitle(f"{word.lower()} · {presenter.format_rtt(self.last_rtt)}")
        else:
            self.proxy_node.set_subtitle(word.lower())

    def probe_done(self, result) -> bool:
        self.probing = False
        self.last_rtt = result
        if result is None:
            # A server that greets nobody, or one that has stopped answering.
            self.rtt_label.set_text("rtt \u2014")
        else:
            self.rtt_label.set_text(f"rtt {presenter.format_rtt(result)}")
            self.spark.push(result)
        self.sync_proxy_subtitle()
        return GLib.SOURCE_REMOVE

    def probe_tick(self) -> bool:
        if self.tunnel.state != STATE_UP:
            self.probe_id = 0
            return GLib.SOURCE_REMOVE
        self.probe_now()
        return GLib.SOURCE_CONTINUE

    def set_probing_enabled(self, enabled: bool) -> None:
        """Measure while the tunnel is up, and stop the moment it is not."""
        if enabled and not self.probe_id:
            self.probe_now()
            self.probe_id = GLib.timeout_add_seconds(PROBE_SECONDS, self.probe_tick)
        elif not enabled and self.probe_id:
            GLib.source_remove(self.probe_id)
            self.probe_id = 0
            self.rtt_label.set_text("rtt \u2014")
            self.last_rtt = None
            self.spark.series.clear()
            self.spark.queue_draw()

    def on_copy_field(self, _button: Gtk.Button, value: str, caption: str) -> None:
        set_clipboard(self, value)
        self.window.toast(f"{caption} copied")

    def build_menu_button(self) -> Gtk.Widget:
        button = Gtk.MenuButton(
            icon_name="view-more-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="More actions",
        )
        button.add_css_class("flat")
        button.set_menu_model(self.build_menu())
        clickable(button)
        return slot(MENU_WIDTH, button, align="center")

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

    # -- events ------------------------------------------------------------- #

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

    # -- state -------------------------------------------------------------- #

    def sync(self) -> None:
        """Push the state computed by the presenter into the widgets."""
        tunnel = self.tunnel

        kind, word, second, tooltip = presenter.state_card(tunnel)
        for widget in (self.stripe, self.led, self.card, self.state_label):
            for name in KINDS:
                widget.remove_css_class(name)
            widget.add_css_class(kind)

        self.state_label.set_text(word)
        self.state_label.set_tooltip_text(tooltip or None)
        self.substate_label.set_text(second)
        self.conn_state.set_text("\u2713 established" if tunnel.active else "")
        # The path says what is happening on it: packets only while the handshake runs,
        # a lit line once it is up, and the broken hop marked when it fails.
        left, right = {
            presenter.KIND_UP: (LIVE, LIVE),
            presenter.KIND_BUSY: (FLOWING, IDLE),
            presenter.KIND_ERR: (DEAD, IDLE),
        }.get(kind, (IDLE, IDLE))
        self.wire_left.set_mode(left)
        self.wire_right.set_mode(right)
        # The wires only say whether anything is moving; this box says what state it is in.
        self.far_node.set_kind(kind)
        self.set_probing_enabled(kind == presenter.KIND_UP)
        self.sync_proxy_subtitle()

        warning = presenter.sitepackages_warning(tunnel)
        self.warning_label.set_text(warning)
        self.warning_label.set_visible(bool(warning))
        self.hint_box.set_visible(bool(presenter.numpy_hint(tunnel)))
        # A round trip means nothing until there is one to measure, but hiding the widget
        # would give the field next to it a different width before and after connecting.
        # It keeps its space and loses its ink.
        self.rtt_box.set_opacity(1.0 if kind == presenter.KIND_UP else 0.0)
        # active is what the user sees, state is what the switch believes; when the two
        # disagree GTK draws the knob half way, which is what it kept doing here.
        if self.switch.get_active() != tunnel.active:
            self.switch.set_active(tunnel.active)
        self.switch.set_state(tunnel.active)
        self.set_tooltip_text(presenter.row_tooltip(tunnel))
