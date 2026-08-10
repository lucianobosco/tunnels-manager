"""Small GTK helpers shared by the widgets."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


def set_clipboard(widget: Gtk.Widget, text: str) -> None:
    """Copy text using the clipboard of the display the widget is on."""
    clipboard = widget.get_clipboard()
    try:
        clipboard.set_text(text)
    except AttributeError:  # pragma: no cover - older PyGObject
        clipboard.set(text)


def clear_box(box: Gtk.Box) -> None:
    """Remove every child of a box."""
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following
