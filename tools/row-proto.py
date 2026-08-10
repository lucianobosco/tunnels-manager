#!/usr/bin/env python3
"""Standalone prototype of the card row. Mock data, no wiring to the manager.

Layout rules, all of them learned by getting them wrong first:

  * Every column is a fixed-width slot, and so is the window. Nothing expands, so no
    row can sit a pixel off its neighbours -- which is the whole point, because the
    row is read as a table.
  * The slot owns the width, never the text. `set_size_request` is a MINIMUM: a slot
    narrower than its content grows and pushes every column after it. That is what
    put the PORT-FWD row 13 px right of the others, and no amount of staring at the
    screenshot found it -- printing the real allocations did, in one run.
  * Inside a slot, content is left-aligned and vertically centred. Always.
  * The switch is pinned to the right edge.
  * Every label ellipsises, so a long name or a long target can never move a column.

Run it on your desktop:      python3 tools/row-proto.py
Render it with no desktop:   gtk4-broadwayd :7 &
                             GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 python3 tools/row-proto.py
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk, Pango

# ---------------------------------------------------------------------- geometry
STRIPE = 3  # the state bar down the left edge
LED_MARGIN = 19  # from the stripe to the dot
LED = 7
TITLE_GAP = 12  # from the dot to the name
TITLE_W = 320  # column 1, the name and its subtitle
PILL_W = 100  # column 2, fits "PORT-FWD", the longest word in the vocabulary
PORT_W = 130  # column 3
TARGET_W = 200  # column 4, the widest, and it ellipsises when a target is longer
STATE_W = 108  # column 5
SWITCH_W = 46  # column 6, the size Adwaita insists on
EDGE = 22  # from the switch to the right edge
ROW_MIN = 58
PAGE_PAD = 18

#: Every column is fixed, so the window is too: nothing can reflow and nothing can
#: shift. 967 px of row.
ROW_W = (
    STRIPE
    + LED_MARGIN
    + LED
    + TITLE_GAP
    + TITLE_W
    + PILL_W
    + PORT_W
    + TARGET_W
    + STATE_W
    + SWITCH_W
    + EDGE
)

PALETTE = {
    "up": "#2ec27e",  # Adwaita green 4
    "busy": "#ffbe6f",  # Adwaita orange 1
    "err": "#ed333b",  # Adwaita red 2, the value the design uses
    "off": "#5e5c64",  # Adwaita dark 2
}

CSS = b"""
window { background: #16161a; }

/* The stripe is a sibling of the card, not a border on it: a border follows the
   corner radius and comes out as a crescent. The design draws a straight bar and
   lets it cover the card's left corners, so the card is only rounded on the right. */
.card {
  background: #26262c;
  border: 1px solid alpha(#ffffff, 0.12);
  border-left: none;
  border-radius: 0 10px 10px 0;
}
/* Anything not established is dimmer as a whole, as in the design. */
.card.busy, .card.err, .card.off { background: #212126; }

.stripe      { min-width: 3px; border-radius: 0; }
.stripe.up   { background: #2ec27e; }
.stripe.busy { background: #ffbe6f; }
.stripe.err  { background: #ed333b; }
.stripe.off  { background: #5e5c64; }

/* A 7px dot; its halo is a spread-only shadow. */
.led { min-width: 7px; min-height: 7px; border-radius: 9999px; }
.led.up   { background: #2ec27e; box-shadow: 0 0 0 4px alpha(#2ec27e, 0.22); }
.led.busy { background: #ffbe6f; box-shadow: 0 0 0 4px alpha(#ffbe6f, 0.22); }
.led.err  { background: #ed333b; box-shadow: 0 0 0 4px alpha(#ed333b, 0.22); }
.led.off  { background: #5e5c64; box-shadow: 0 0 0 4px alpha(#5e5c64, 0.18); }

.row-title { font-family: monospace; font-weight: 700; font-size: 14px; color: #eceaee; }
.row-sub   { font-size: 12px; color: #94949f; }
.card.busy .row-title, .card.err .row-title, .card.off .row-title { color: #c9c9d1; }
.card.busy .row-sub, .card.err .row-sub, .card.off .row-sub,
.card.busy .target, .card.err .target, .card.off .target { color: #7c7c88; }
.card.busy .port, .card.err .port, .card.off .port { color: #c9c9d1; }

.pill {
  font-family: monospace;
  font-weight: 600;
  font-size: 10.5px;
  border-radius: 5px;
  min-height: 20px;
  padding: 0 7px;
}
.pill.iap { color: #62a0ea; background: alpha(#62a0ea, 0.15); }
.pill.fwd { color: #b0aeb5; background: alpha(#ffffff, 0.09); }

.port   { font-family: monospace; font-size: 13px; color: #eceaee; }
.target { font-family: monospace; font-size: 13px; color: #94949f; }

.state      { font-family: monospace; font-weight: 600; font-size: 12px; }
.state.up   { color: #2ec27e; }
.state.busy { color: #ffbe6f; }
.state.err  { color: #ed333b; }
.state.off  { color: #94949f; }
.substate   { font-family: monospace; font-size: 10.5px; color: #94949f; }

/* Adwaita paints the switch in the desktop's accent colour, which is whatever the
   user picked -- orange here. The design specifies blue, so the row states it. */
switch { background: #3a3a40; border: none; box-shadow: none; }
switch:checked { background: #3584e4; }
switch > slider { background: #ffffff; border: none; box-shadow: none; }
"""

# name, subtitle, state kind, pill, port, target, state word, second line, switch
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


def label(text: str, *classes: str) -> Gtk.Label:
    """Left-aligned, vertically centred, and it ellipsises rather than push a column."""
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


def slot(width: int, child: Gtk.Widget, offset: int = 0) -> Gtk.Box:
    """A fixed-width column. The slot owns the width; the content sits left and centred."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=False)
    box.set_size_request(width, -1)
    child.set_margin_start(offset)
    child.set_halign(Gtk.Align.START)
    child.set_valign(Gtk.Align.CENTER)
    child.set_hexpand(True)
    box.append(child)
    return box


def build_row(name, sub, kind, pill, port, target, state, substate, on) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

    stripe = Gtk.Box()
    stripe.add_css_class("stripe")
    stripe.add_css_class(kind)
    stripe.set_size_request(STRIPE, -1)
    row.append(stripe)

    card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, hexpand=False)
    card.add_css_class("card")
    card.add_css_class(kind)
    card.set_size_request(-1, ROW_MIN)
    row.append(card)

    led = Gtk.Box(valign=Gtk.Align.CENTER)
    led.add_css_class("led")
    led.add_css_class(kind)
    led.set_size_request(LED, LED)
    led.set_margin_start(LED_MARGIN)
    card.append(led)

    # Nothing expands, not even this: with a fixed window, a fixed slot per column is
    # the only layout where no row can ever sit a pixel off its neighbours.
    heading = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=1,
        valign=Gtk.Align.CENTER,
        hexpand=False,
    )
    heading.set_size_request(TITLE_W, -1)
    heading.set_margin_start(TITLE_GAP)
    heading.append(label(name, "row-title"))
    heading.append(label(sub, "row-sub"))
    card.append(heading)

    chip = label(pill, "pill", "iap" if pill == "IAP" else "fwd")
    chip.set_ellipsize(Pango.EllipsizeMode.NONE)  # a closed vocabulary never truncates
    card.append(slot(PILL_W, chip))

    card.append(slot(PORT_W, label(port, "port")))
    card.append(slot(TARGET_W, label(target, "target")))

    stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER)
    stack.append(label(state, "state", kind))
    if substate:
        stack.append(label(substate, "substate"))
    card.append(slot(STATE_W, stack))

    switch = Gtk.Switch(active=on, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
    switch.set_margin_end(EDGE)
    switch.set_size_request(SWITCH_W, -1)
    card.append(switch)
    return row


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Row prototype")
        self.set_default_size(ROW_W + 2 * PAGE_PAD, 360)
        self.set_resizable(False)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for setter in (
            page.set_margin_top,
            page.set_margin_bottom,
            page.set_margin_start,
            page.set_margin_end,
        ):
            setter(PAGE_PAD)
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
