"""Styling and the fixed column widths.

The named libadwaita colours (@success_color, @error_color, …) already come tuned for the
light and the dark theme, so there is no second palette to maintain.
"""

#: Widths of the columns whose content changes while running. Left at their natural size,
#: "stopped" -> "opening" would resize them and shift the whole table sideways.
PORT_WIDTH = 62
TAG_WIDTH = 74  #: fits the longest word, "opening"
STATE_WIDTH = TAG_WIDTH + 19  #: plus the spinner and its spacing

CSS = """
.tag {
  font-size: 0.80em;
  font-weight: 700;
  padding: 3px 0;
  border-radius: 9999px;
  margin-right: 2px;
}
.tag.state-up    { color: @success_color; background-color: alpha(@success_color, 0.16); }
.tag.state-start { color: @warning_color; background-color: alpha(@warning_color, 0.18); }
.tag.state-error { color: @error_color;   background-color: alpha(@error_color, 0.16); }
.tag.state-down  { background-color: alpha(currentColor, 0.12); }

.logview {
  font-family: monospace;
  font-size: 0.86em;
  padding: 10px;
}
.mono { font-family: monospace; }

/* Badges. Colour is reserved for risk (production in red); the service type stays neutral,
   because with an orange accent an accent badge and an error badge look alike. */
.badge {
  font-size: 0.72em;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 5px;
}
.badge-service,
.badge-generic { background-color: alpha(currentColor, 0.13); opacity: 0.85; }
.badge-production { color: @error_color; background-color: alpha(@error_color, 0.15); }
.badge-staging {
  border: 1px solid alpha(currentColor, 0.28);
  padding: 0 6px;
  opacity: 0.7;
}
/* Bound to 0.0.0.0: not an error, but worth noticing. */
.badge-open {
  color: @warning_color;
  background-color: alpha(@warning_color, 0.18);
  font-family: monospace;
}

.group-title {
  font-size: 0.80em;
  font-weight: 700;
  padding-left: 4px;
}
/* Compact rows: without this GTK reserves a full button height per row. */
.port-button, .tunnel-row button { min-height: 0; min-width: 0; }
.port-button { padding: 1px 7px; }
.port-button label { font-family: monospace; }
.tunnel-row switch { min-height: 0; }

.panel-title { font-size: 1.05em; font-weight: 700; }
.panel-subtitle { font-size: 0.85em; }
.field-caption {
  font-size: 0.68em;
  font-weight: 700;
  letter-spacing: 0.08em;
  opacity: 0.65;
  padding-left: 2px;
}
.field {
  border: 1px solid @borders;
  border-radius: 7px;
  padding: 2px 3px 2px 8px;
}
.field label { font-size: 0.86em; }
"""
