#!/usr/bin/env python3
"""Standalone prototype: the row card from designs/app-panel, in real GTK4 widgets.

Nothing here touches the app. Mock data only. Geometry copied from
row-tunnels-dark.svg, whose 880x54 row places things at these x offsets:

    3  stripe   26 led (centre)   42 title/subtitle   318 pill
  424  port    516 target        716 state           824 switch    880 edge
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

# --------------------------------------------------------------------------- geometry
STRIPE = 3
LED_MARGIN = 19  # 3 + 19 -> dot starts at 22, centre 25.5 (svg: 26)
LED = 7
TITLE_GAP = 12  # dot ends at 29, + 12 -> 41 (svg: 42)
TITLE_W = 276  # 42 -> 318
PILL_W = 46  # 318 -> 364
PORT_W = 152  # 364 -> 516, port text offset 60 -> 424
PORT_OFFSET = 60
TARGET_W = 200  # 516 -> 716
STATE_W = 108  # 716 -> 824
EDGE = 22  # 824 + 34 switch -> 880

CSS = b"""
window { background: #16161a; }

.card {
  background: #26262c;
  border: 1px solid alpha(#ffffff, 0.12);
  border-radius: 10px;
  min-height: 54px;
}
.card.up   { border-left: 3px solid #2ec27e; }
.card.busy { border-left: 3px solid #ffbe6f; }
.card.err  { border-left: 3px solid #ff7b63; }
.card.off  { border-left: 3px solid #5e5c64; }

/* The LED: a 7px dot with its halo painted as a spread-only shadow. */
.led {
  min-width: 7px;
  min-height: 7px;
  border-radius: 9999px;
}
.led.up   { background: #2ec27e; box-shadow: 0 0 0 4px alpha(#2ec27e, 0.22); }
.led.busy { background: #ffbe6f; box-shadow: 0 0 0 4px alpha(#ffbe6f, 0.22); }
.led.err  { background: #ff7b63; box-shadow: 0 0 0 4px alpha(#ff7b63, 0.22); }
.led.off  { background: #5e5c64; box-shadow: 0 0 0 4px alpha(#5e5c64, 0.18); }

.row-title {
  font-family: monospace;
  font-weight: 700;
  font-size: 12.5px;
  color: #eceaee;
}
.row-sub { font-size: 10px; color: #94949f; }

.pill {
  font-family: monospace;
  font-weight: 600;
  font-size: 9px;
  border-radius: 5px;
  min-height: 18px;
  padding: 0 6px;
}
.pill.iap  { color: #62a0ea; background: alpha(#62a0ea, 0.15); }
.pill.fwd  { color: #b0aeb5; background: alpha(#ffffff, 0.09); }

.port   { font-family: monospace; font-size: 11px; color: #eceaee; }
.target { font-family: monospace; font-size: 11px; color: #94949f; }

.state       { font-family: monospace; font-weight: 600; font-size: 10.5px; }
.state.up   { color: #2ec27e; }
.state.busy { color: #ffbe6f; }
.state.err  { color: #ff7b63; }
.state.off  { color: #94949f; }
.substate    { font-family: monospace; font-size: 9px; color: #94949f; }
.substate.link { color: #62a0ea; }

/* 34x18 with a 14px knob, the size the design draws. */
switch {
  min-width: 34px;
  min-height: 18px;
  border-radius: 9px;
  background: #3a3a40;
  border: none;
  box-shadow: none;
  padding: 0;
}
switch:checked { background: #3584e4; }
switch > slider {
  min-width: 14px;
  min-height: 14px;
  margin: 2px;
  border-radius: 9999px;
  background: #ffffff;
  border: none;
  box-shadow: none;
}
/* GTK keeps two icons inside the switch for the on/off marks; at this size they
   are the reason the widget refuses to shrink. */
switch > image { -gtk-icon-size: 0; min-width: 0; min-height: 0; }
"""

# label, subtitle, kind, pill, port, target, state, substate, on
MOCK = [
    (
        "catalog-db-prod",
        "mysql 8.4 · primary · europe-west1",
        "up",
        "IAP",
        ":13306",
        "bastion-prod:3306",
        "ESTABLISHED",
        "2h 39m",
        True,
    ),
    (
        "reports-db-prod",
        "mysql 8.4 · read replica · europe-west1",
        "busy",
        "IAP",
        ":13307",
        "bastion-prod:3306",
        "CONNECTING",
        "0:03",
        True,
    ),
    (
        "metrics-db-pre",
        "mysql 8.4 · staging · europe-west1",
        "err",
        "IAP",
        ":13308",
        "bastion-pre:3306",
        "FAILED",
        "port in use",
        False,
    ),
    (
        "internal-dashboard",
        "http · kubernetes · svc/dashboard",
        "off",
        "PORT-FWD",
        ":8080",
        "svc/dashboard:80",
        "STOPPED",
        "",
        False,
    ),
]


def cell(width: int, child: Gtk.Widget, offset: int = 0) -> Gtk.Box:
    """A fixed-width slot, so a state change can never shift the row."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    box.set_size_request(width, -1)
    child.set_margin_start(offset)
    box.append(child)
    return box


def label(text: str, *classes: str, xalign: float = 0.0) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, valign=Gtk.Align.CENTER)
    for name in classes:
        widget.add_css_class(name)
    return widget


def build_row(label_text, sub, kind, pill, port, target, state, substate, on) -> Gtk.Widget:
    card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    card.add_css_class("card")
    card.add_css_class(kind)

    led = Gtk.Box(valign=Gtk.Align.CENTER)
    led.add_css_class("led")
    led.add_css_class(kind)
    led.set_size_request(LED, LED)
    led.set_margin_start(LED_MARGIN)
    card.append(led)

    heading = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER, hexpand=False
    )
    heading.set_size_request(TITLE_W, -1)
    heading.set_margin_start(TITLE_GAP)
    heading.append(label(label_text, "row-title"))
    heading.append(label(sub, "row-sub"))
    card.append(heading)

    chip = label(pill, "pill", "iap" if pill == "IAP" else "fwd", xalign=0.5)
    chip.set_size_request(PILL_W, -1)
    card.append(chip)

    card.append(cell(PORT_W, label(port, "port"), PORT_OFFSET))
    card.append(cell(TARGET_W, label(target, "target")))

    stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER)
    stack.append(label(state, "state", kind))
    if substate:
        classes = ("substate", "link") if "↗" in substate else ("substate",)
        stack.append(label(substate, *classes))
    card.append(cell(STATE_W, stack))

    switch = Gtk.Switch(active=on, valign=Gtk.Align.CENTER)
    switch.set_margin_end(EDGE)
    card.append(switch)
    return card


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Row prototype")
        self.set_default_size(880 + 2 * 24, 340)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for setter in (
            page.set_margin_top,
            page.set_margin_bottom,
            page.set_margin_start,
            page.set_margin_end,
        ):
            setter(24)
        for mock in MOCK:
            page.append(build_row(*mock))

        header = Adw.HeaderBar(show_title=False)
        header.add_css_class("flat")
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(header)
        root.append(page)
        self.set_content(root)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.demo.RowProto")

    def do_activate(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        Window(self).present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
