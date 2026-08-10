"""Styling and the fixed column widths.

The row is read as a table, so every column is a fixed-width slot and the slot owns the
width, never the text: a slot narrower than its content grows and pushes every column
after it, and only in that row. One column, the target, takes the slack, so the window
stays resizable while the columns still line up across rows.

The palette is the design's own, and it is a dark palette: the app forces the dark scheme
(see ui/app.py) rather than keep two sets of values that have to agree.
"""

#: Column widths. Anything that can change while a tunnel runs lives in a fixed slot.
STRIPE_WIDTH = 3
NAME_WIDTH = 280
PILL_WIDTH = 100
PORT_WIDTH = 70  #: a port is five digits and a colon
TARGET_MIN = 176  #: the only flexible column: it absorbs whatever the window has spare
CHEVRON_GAP = 12  #: from the switch to the chevron that says the row opens
STATE_WIDTH = 108  #: fits "ESTABLISHED" and its second line
MENU_WIDTH = 30  #: the three dots, the only control the design does not draw
GUTTER = 14  #: kept clear on the right of every column, so an ellipsis never touches
LED_SIZE = 7
LED_MARGIN = 19
NAME_GAP = 12
ROW_HEIGHT = 58
EDGE = 22  #: from the switch to the right edge

CSS = """
/* The rows are separate cards on a darker ground, so the list itself is invisible. */
window { background: #1a1a1e; }
.tunnel-list { background: none; }
.tunnel-row { margin-bottom: 8px; }

/* The stripe is a sibling of the card, not a border on it: a border follows the corner
   radius and comes out as a crescent instead of a straight bar. So the card is rounded
   on its right side only, and the stripe covers its left edge. */
.tunnel-row {
  background: none;
  border: none;
  padding: 0;
}
.tunnel-card {
  background: #26262c;
  border: 1px solid alpha(#ffffff, 0.12);
  border-left: none;
  border-radius: 0 10px 10px 0;
}
/* Anything not established is dimmer as a whole. */
.tunnel-card.busy, .tunnel-card.err, .tunnel-card.off { background: #212126; }

.stripe      { min-width: 3px; border-radius: 0; }
.stripe.up   { background: #2ec27e; }
.stripe.busy { background: #ffbe6f; }
.stripe.err  { background: #ed333b; }
.stripe.off  { background: #5e5c64; }

/* A 7px dot; the halo is a spread-only shadow. */
.led { min-width: 7px; min-height: 7px; border-radius: 9999px; }
.led.up   { background: #2ec27e; box-shadow: 0 0 0 4px alpha(#2ec27e, 0.22); }
.led.busy { background: #ffbe6f; box-shadow: 0 0 0 4px alpha(#ffbe6f, 0.22); }
.led.err  { background: #ed333b; box-shadow: 0 0 0 4px alpha(#ed333b, 0.22); }
.led.off  { background: #5e5c64; box-shadow: 0 0 0 4px alpha(#5e5c64, 0.18); }

.row-title { font-family: monospace; font-weight: 700; font-size: 14px; color: #eceaee; }
.row-sub   { font-size: 12px; color: #94949f; }
.tunnel-card.busy .row-title, .tunnel-card.err .row-title,
.tunnel-card.off .row-title { color: #c9c9d1; }
.tunnel-card.busy .row-sub, .tunnel-card.err .row-sub, .tunnel-card.off .row-sub,
.tunnel-card.busy .target, .tunnel-card.err .target,
.tunnel-card.off .target { color: #7c7c88; }
.tunnel-card.busy .port, .tunnel-card.err .port, .tunnel-card.off .port { color: #c9c9d1; }

/* Two pills, two meanings: blue says how the tunnel is opened, red says production. */
.pill {
  font-family: monospace;
  font-weight: 600;
  font-size: 10.5px;
  border-radius: 5px;
  min-height: 20px;
  padding: 0 7px;
}
.pill-iap { color: #62a0ea; background: alpha(#62a0ea, 0.15); }
.pill-fwd { color: #b0aeb5; background: alpha(#ffffff, 0.09); }

/* What a row reveals when it is clicked: the path the tunnel takes. */
.details { padding: 14px 22px 16px 31px; }
.node {
  background: #2b2b33;
  border: 1px solid alpha(#ffffff, 0.10);
  border-radius: 8px;
  padding: 7px 12px;
}
.node-proxy { border-color: alpha(#62a0ea, 0.45); }
/* The far end wears the state, so the wires do not have to. */
.node-up   { border-color: alpha(#2ec27e, 0.55); background: alpha(#2ec27e, 0.07); }
.node-busy { border-color: alpha(#ffbe6f, 0.55); background: alpha(#ffbe6f, 0.07); }
.node-err  { border-color: alpha(#ed333b, 0.55); background: alpha(#ed333b, 0.07); }
.node-off  { border-color: alpha(#ffffff, 0.10); }
.node-title { font-weight: 700; font-size: 13px; color: #eceaee; }
.node-sub { font-family: monospace; font-size: 11px; color: #7c7c88; }
.wire-label { font-family: monospace; font-size: 10.5px; color: #7c7c88; }

.conn-field {
  background: #16161a;
  border: 1px solid alpha(#ffffff, 0.12);
  border-radius: 8px;
  padding: 7px 12px;
}
.conn-state { font-family: monospace; font-size: 11.5px; color: #2ec27e; }
.copy-button {
  background: #3584e4;
  color: #ffffff;
  font-weight: 700;
  border: none;
  border-radius: 8px;
  padding: 7px 16px;
}
.copy-button:hover { background: #4a90e6; }
.field-caption {
  font-size: 0.68em;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #7c7c88;
}
.field-value { font-family: monospace; font-size: 12.5px; color: #c9c9d1; }

.port   { font-family: monospace; font-size: 13px; color: #eceaee; }
.target { font-family: monospace; font-size: 13px; color: #94949f; }

.state      { font-family: monospace; font-weight: 600; font-size: 12px; }
.state.up   { color: #2ec27e; }
.state.busy { color: #ffbe6f; }
.state.err  { color: #ed333b; }
.state.off  { color: #94949f; }
.substate   { font-family: monospace; font-size: 10.5px; color: #94949f; }
/* The affordance: this row opens. */
.chevron { color: #7c7c88; }
/* Something the open row has to say, and it is not good news. */
.details-warning { color: #ed333b; font-size: 12px; }
/* A suggestion, not a failure: dimmer than the warning, and it carries a command. */
.details-hint { color: #94949f; font-size: 11.5px; }
.details-hint-cmd { font-family: monospace; font-size: 11.5px; color: #c9c9d1; }
.tunnel-row:hover .chevron { color: #c9c9d1; }

/* Compact controls: without this GTK reserves a full button height per row. */
.tunnel-card button { min-height: 0; min-width: 0; padding: 2px 4px; }
.port-button { padding: 1px 4px; background: none; }

/* Adwaita paints the switch in the desktop's accent colour, whatever the user picked.
   The design specifies blue, so the row states it. */
/* Geometry is left to Adwaita on purpose. Forcing a 34x18 trough with a 14px knob is
   what left the knob parked half way: GTK sizes the slider as a fraction of the trough
   it is given, so pinning both ends makes them disagree about where the travel stops.
   The design's smaller switch is not worth a control that looks broken. */
.tunnel-card switch:checked { background: #3584e4; }

.group-title {
  font-size: 0.80em;
  font-weight: 700;
  padding-left: 4px;
}
.logview {
  font-family: monospace;
  font-size: 0.86em;
  padding: 10px;
}
.mono { font-family: monospace; }

/* Badges outside the row: the dialogs still use them. */
.badge {
  font-size: 0.72em;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 5px;
}
.badge-service,
.badge-generic { background-color: alpha(currentColor, 0.13); opacity: 0.85; }
.badge-production { color: @error_color; background-color: alpha(@error_color, 0.15); }

.panel-title { font-size: 1.05em; font-weight: 700; }
.panel-subtitle { font-size: 0.85em; }
"""
