"""The path a tunnel takes, drawn: what a row reveals when you open it.

Three nodes -- this machine, the proxy in the middle, the thing on the far end -- joined
by wires with packets crawling along them. Everything here is drawn with Cairo rather than
assembled from widgets, because a dashed wire with dots moving along it is a drawing, and
because the animation then costs one tick callback per open row and nothing at all while
the rows are closed.

PLACEHOLDERS: the round-trip figure and the series under it are invented, and marked
FAKE_ wherever they appear. Nothing else on screen is: the states, the ports and the
addresses all come from the tunnel.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Pango

#: How many measurements the sparkline keeps.
RTT_HISTORY = 14

#: Palette, the design's own values (see css.py: the app forces the dark scheme).
WIRE = (1, 1, 1, 0.14)
PACKET_OUT = (0.384, 0.627, 0.918)  # #62a0ea
PACKET_BACK = (0.180, 0.760, 0.494)  # #2ec27e
BROKEN = (0.929, 0.200, 0.231)  # #ed333b
SPARK = (0.180, 0.760, 0.494)

#: Pulses of light in relay. A pulse crosses the first leg, and when it gets there the
#: next leg lights up: the path is walked once, not animated twice out of step. Several
#: are in flight at a time, evenly spaced, so with two legs there is normally one on each.
DASH = (3.0, 5.0)
PULSES = 1  # one pulse walks the whole path, lighting one leg at a time
CYCLE = 2.4  # seconds for one pulse to walk the whole path
GLOW = 0.34  # how much of a leg the pulse covers
GLOW_STEPS = 14

# Connecting and established move the same way. What tells them apart is the box at the
# far end, which carries the colour of the state.
WIRE_H = 26
SPARK_W, SPARK_H = 74, 26


#: What a link can be doing. Two of them move -- the handshake and the established link
#: -- and they look the same on purpose: telling the states apart is not the wire's job.
#: The box at the far end carries that, in colour, where it reads at a glance.
IDLE, FLOWING, LIVE, DEAD = "idle", "flowing", "live", "dead"


class Wire(Gtk.DrawingArea):
    """One hop of the path, drawn according to what that hop is doing.

    idle     dashed and grey, still: nothing has been attempted
    flowing  dashed and grey, packets crawling: the handshake is in progress
    live     the same: the hop is carrying a connection
    dead     dashed and red, still: this is where it broke
    """

    def __init__(self, width: int = 120, leg: int = 0, legs: int = 1):
        super().__init__()
        self.leg = leg
        self.legs = legs
        self.set_size_request(width, WIRE_H)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.phase = 0.0
        self.tick_id = 0
        self.mode = IDLE
        self.watched = False
        self.set_draw_func(self.draw)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.retick()
        self.queue_draw()

    def set_watched(self, watched: bool) -> None:
        """The row that owns this is open, so the animation is worth running."""
        self.watched = watched
        self.retick()

    def retick(self) -> None:
        """Tick only while a handshake is in progress and somebody is looking at it."""
        wanted = self.watched and self.mode in (FLOWING, LIVE)
        if wanted and not self.tick_id:
            self.tick_id = self.add_tick_callback(self.on_tick)
        elif not wanted and self.tick_id:
            self.remove_tick_callback(self.tick_id)
            self.tick_id = 0
            self.phase = 0.0
            self.queue_draw()

    def on_tick(self, _widget, clock) -> bool:
        # microseconds since the clock started, folded into one crossing
        self.phase = (clock.get_frame_time() / 1_000_000.0 % CYCLE) / CYCLE
        self.queue_draw()
        return True

    def draw(self, _area, cr, width: int, height: int) -> None:
        y = height / 2
        cr.set_line_width(2)
        cr.set_dash(list(DASH))
        base = (*BROKEN, 0.75) if self.mode == DEAD else WIRE
        cr.set_source_rgba(*base)
        cr.move_to(0, y)
        cr.line_to(width, y)
        cr.stroke()
        cr.set_dash([])

        if self.mode not in (FLOWING, LIVE) or not self.tick_id:
            return

        for index in range(PULSES):
            along = (self.phase + index / PULSES) % 1.0  # 0..1 over the whole path
            leg = min(int(along * self.legs), self.legs - 1)
            if leg != self.leg:
                continue  # that pulse is on another leg right now
            self.draw_pulse(cr, width, y, along * self.legs - leg)

    def draw_pulse(self, cr, width: int, y: float, local: float) -> None:
        """A stretch of bright line whose alpha rises and falls, in short segments."""
        span = GLOW * width
        head = local * (width + span) - span / 2
        cr.set_line_width(2.4)
        for step in range(GLOW_STEPS):
            at = step / GLOW_STEPS
            x0 = head - span / 2 + at * span
            x1 = x0 + span / GLOW_STEPS + 0.6
            if x1 <= 0 or x0 >= width:
                continue
            alpha = 0.95 * (1.0 - abs(at - 0.5) * 2.0) ** 1.5
            cr.set_source_rgba(*PACKET_OUT, alpha)
            cr.move_to(max(x0, 0), y)
            cr.line_to(min(x1, width), y)
            cr.stroke()


class Sparkline(Gtk.DrawingArea):
    """The round-trip measurements, oldest to newest. Empty until something measures."""

    def __init__(self) -> None:
        super().__init__()
        self.series: list[float] = []
        self.set_size_request(SPARK_W, SPARK_H)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self.draw)

    def push(self, value: float) -> None:
        self.series.append(value)
        del self.series[:-RTT_HISTORY]
        self.queue_draw()

    def draw(self, _area, cr, width: int, height: int) -> None:
        if not self.series:
            return
        if len(self.series) == 1:
            # One measurement is still a fact: draw it flat, across the middle.
            cr.set_line_width(1.4)
            cr.set_source_rgba(*SPARK, 0.95)
            cr.move_to(0, height / 2)
            cr.line_to(width, height / 2)
            cr.stroke()
            cr.arc(width - 1, height / 2, 2.1, 0, 6.2832)
            cr.fill()
            return
        low, high = min(self.series), max(self.series)
        span = high - low or 1
        step = width / (len(self.series) - 1)

        cr.set_line_width(1.4)
        cr.set_source_rgba(*SPARK, 0.95)
        for index, value in enumerate(self.series):
            x = index * step
            y = height - 3 - (value - low) / span * (height - 6)
            cr.line_to(x, y) if index else cr.move_to(x, y)
        cr.stroke()

        # the last point, emphasised: it is the number written next to it
        x = (len(self.series) - 1) * step
        y = height - 3 - (self.series[-1] - low) / span * (height - 6)
        cr.arc(x - 1, y, 2.1, 0, 6.2832)
        cr.fill()


#: The state classes a node can wear, so setting one means clearing these first.
NODE_KINDS = ("up", "busy", "err", "off")


class Node(Gtk.Box):
    """One box in the path: a name, a line of detail, and a state it can be given.

    The far end is the one that changes: green while the tunnel carries a connection,
    amber while it is being made, red when it broke, grey when nothing is running.
    """

    def __init__(self, title: str, subtitle: str, locked: bool = False, stateful: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.add_css_class("node")
        if locked:
            self.add_css_class("node-proxy")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.led: Gtk.Box | None = None
        if locked:
            head.append(Gtk.Image(icon_name="changes-prevent-symbolic", valign=Gtk.Align.CENTER))
        elif stateful:
            self.led = Gtk.Box(valign=Gtk.Align.CENTER)
            self.led.add_css_class("led")
            self.led.set_size_request(7, 7)
            head.append(self.led)
        label = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        label.add_css_class("node-title")
        head.append(label)
        self.append(head)

        self.detail = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.detail.add_css_class("node-sub")
        self.append(self.detail)

    def set_subtitle(self, text: str) -> None:
        self.detail.set_text(text)

    def set_kind(self, kind: str) -> None:
        for name in NODE_KINDS:
            self.remove_css_class(f"node-{name}")
            if self.led is not None:
                self.led.remove_css_class(name)
        self.add_css_class(f"node-{kind}")
        if self.led is not None:
            self.led.add_css_class(kind)


def link(above: str, below: str, leg: int = 0, legs: int = 2) -> tuple[Gtk.Widget, Wire]:
    """A wire with what it is written above it and where it goes written below."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
    top = Gtk.Label(label=above, xalign=0.5)
    top.add_css_class("wire-label")
    wire = Wire(leg=leg, legs=legs)
    bottom = Gtk.Label(label=below, xalign=0.5)
    bottom.add_css_class("wire-label")
    box.append(top)
    box.append(wire)
    box.append(bottom)
    return box, wire
