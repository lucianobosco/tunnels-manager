"""Dialogs: live log, tunnel editor, and the shortcut manager."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from .. import presenter
from ..model import SERVICE_HTTP, SERVICE_LABELS, SERVICE_MYSQL, SERVICE_TCP, Tunnel
from .util import set_clipboard

LOG_REFRESH_MS = 600


class LogWindow(Adw.Window):
    """Live output of the process behind one tunnel."""

    def __init__(self, parent: Gtk.Window, tunnel: Tunnel):
        super().__init__(transient_for=parent, modal=False, title=f"Log · {tunnel.label}")
        self.tunnel = tunnel
        self.set_default_size(760, 460)

        self.view = Gtk.TextView(
            editable=False,
            cursor_visible=False,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        self.view.add_css_class("logview")
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True, child=self.view)

        copy_button = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy the log")
        copy_button.connect("clicked", self.on_copy)
        header = Adw.HeaderBar()
        header.pack_end(copy_button)

        toolbar = Adw.ToolbarView(content=scroller)
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        self.shown = 0
        self.refresh()
        self.timer = GLib.timeout_add(LOG_REFRESH_MS, self.refresh)
        self.connect("close-request", self.on_close)

    def refresh(self) -> bool:
        lines = list(self.tunnel.log)
        if len(lines) == self.shown:
            return True
        buffer = self.view.get_buffer()
        buffer.set_text("\n".join(lines) + "\n")
        self.shown = len(lines)
        buffer.place_cursor(buffer.get_end_iter())
        self.view.scroll_to_iter(buffer.get_end_iter(), 0.0, False, 0.0, 0.0)
        return True

    def on_copy(self, _button: Gtk.Button) -> None:
        set_clipboard(self, "\n".join(self.tunnel.log))

    def on_close(self, *_args) -> bool:
        GLib.source_remove(self.timer)
        return False


class TunnelDialog(Adw.Dialog):
    """Create or edit an IAP tunnel."""

    SERVICES = (SERVICE_MYSQL, SERVICE_HTTP, SERVICE_TCP)
    ENVIRONMENTS = ("", "pro", "pre", "dev")

    def __init__(self, parent, tunnel: Tunnel | None, on_save):
        super().__init__()
        self.on_save = on_save
        self.original = tunnel
        self.manager = parent.manager
        self.set_title("Edit tunnel" if tunnel else "New tunnel")
        self.set_content_width(480)

        page = Adw.PreferencesPage()

        identity = Adw.PreferencesGroup(
            title="Identity",
            description="The service sets the row badge and the fields the panel offers.",
        )
        self.entry_label = Adw.EntryRow(title="Name")
        self.entry_service = Adw.ComboRow(
            title="Service",
            model=Gtk.StringList.new([SERVICE_LABELS[key] for key in self.SERVICES]),
        )
        self.entry_database = Adw.EntryRow(title="Database (optional)")
        self.entry_env = Adw.ComboRow(
            title="Environment",
            model=Gtk.StringList.new(["Guess from the project", "PRO", "PRE", "DEV"]),
        )
        for row in (self.entry_label, self.entry_service, self.entry_database, self.entry_env):
            identity.add(row)

        remote = Adw.PreferencesGroup(title="Target in GCP")
        self.entry_instance = Adw.EntryRow(title="Instance")
        self.entry_remote_port = Adw.EntryRow(title="Remote port")
        self.entry_zone = Adw.EntryRow(title="Zone")
        self.entry_project = Adw.EntryRow(title="Project")
        for row in (
            self.entry_instance,
            self.entry_remote_port,
            self.entry_zone,
            self.entry_project,
        ):
            remote.add(row)

        local = Adw.PreferencesGroup(
            title="Local side",
            description=(
                "No other tunnel may use the same local port. 0.0.0.0 exposes the tunnel "
                "to your whole network; 127.0.0.1 only to this machine."
            ),
        )
        self.entry_local_host = Adw.EntryRow(title="Local host")
        self.entry_local_port = Adw.EntryRow(title="Local port")
        local.add(self.entry_local_host)
        local.add(self.entry_local_port)

        for group in (identity, remote, local):
            page.add(group)

        if tunnel is not None:
            self.entry_label.set_text(tunnel.label)
            self.entry_database.set_text(tunnel.database)
            if tunnel.service in self.SERVICES:
                self.entry_service.set_selected(self.SERVICES.index(tunnel.service))
            env = tunnel.env.lower()
            self.entry_env.set_selected(
                self.ENVIRONMENTS.index(env) if env in self.ENVIRONMENTS else 0
            )
            self.entry_instance.set_text(tunnel.instance)
            self.entry_remote_port.set_text(str(tunnel.remote_port))
            self.entry_zone.set_text(tunnel.zone)
            self.entry_project.set_text(tunnel.project)
            self.entry_local_host.set_text(tunnel.local_host)
            self.entry_local_port.set_text(str(tunnel.local_port))
        else:
            self.entry_zone.set_text("europe-west1-d")
            self.entry_local_host.set_text("127.0.0.1")

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", self.on_cancel)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.on_save_clicked)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(save)

        # A banner instead of a label: it wraps rather than widening the dialog.
        self.banner = Adw.Banner(revealed=False)

        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)
        self.set_child(toolbar)

    def on_cancel(self, _button: Gtk.Button) -> None:
        self.close()

    def read_form(self) -> presenter.TunnelForm:
        return presenter.TunnelForm(
            label=self.entry_label.get_text(),
            service=self.SERVICES[self.entry_service.get_selected()],
            database=self.entry_database.get_text(),
            env=self.ENVIRONMENTS[self.entry_env.get_selected()],
            instance=self.entry_instance.get_text(),
            remote_port=self.entry_remote_port.get_text(),
            zone=self.entry_zone.get_text(),
            project=self.entry_project.get_text(),
            local_host=self.entry_local_host.get_text(),
            local_port=self.entry_local_port.get_text(),
        )

    def on_save_clicked(self, _button: Gtk.Button | None) -> None:
        tunnel, error = presenter.validate_tunnel_form(
            self.read_form(),
            self.manager.ordered(),
            self.original,
            self.manager.next_free_port,
        )
        if tunnel is None:
            self.fail(error)
            return
        self.on_save(tunnel, self.original)
        self.close()

    def fail(self, message: str) -> None:
        self.banner.set_title(message)
        self.banner.set_revealed(True)


class BundleDialog(Adw.Dialog):
    """Create or edit one shortcut: a name and the tunnels it opens."""

    def __init__(self, parent, manager, name: str | None, on_save):
        super().__init__()
        self.manager = manager
        self.original_name = name
        self.on_save = on_save
        self.switches: dict[str, Adw.SwitchRow] = {}

        self.set_title("Edit shortcut" if name else "New shortcut")
        self.set_content_width(460)
        self.set_content_height(560)

        page = Adw.PreferencesPage()
        naming = Adw.PreferencesGroup(
            description="The name is what shows up in the main menu, under Shortcuts.",
        )
        self.entry_name = Adw.EntryRow(title="Shortcut name")
        naming.add(self.entry_name)
        page.add(naming)

        selected = set(manager.bundles.get(name, [])) if name else set()
        for group_name, items in presenter.group_tunnels(manager.ordered()).items():
            group = Adw.PreferencesGroup(title=group_name)
            for tunnel in items:
                row = Adw.SwitchRow(
                    title=GLib.markup_escape_text(tunnel.label),
                    subtitle=f"port {tunnel.local_port}",
                    active=tunnel.key in selected,
                )
                self.switches[tunnel.key] = row
                group.add(row)
            page.add(group)

        if name:
            self.entry_name.set_text(name)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", self.on_cancel)
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self.on_save_clicked)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(save)

        self.banner = Adw.Banner(revealed=False)
        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)
        self.set_child(toolbar)

    def on_cancel(self, _button: Gtk.Button) -> None:
        self.close()

    def on_save_clicked(self, _button: Gtk.Button | None) -> None:
        name = self.entry_name.get_text().strip()
        keys = [key for key, row in self.switches.items() if row.get_active()]
        error = presenter.validate_bundle(name, keys, self.manager.bundles, self.original_name)
        if error:
            self.fail(error)
            return
        self.on_save(name, keys, self.original_name)
        self.close()

    def fail(self, message: str) -> None:
        self.banner.set_title(message)
        self.banner.set_revealed(True)


class BundlesDialog(Adw.Dialog):
    """The list of shortcuts, with add, edit and delete."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.manager = window.manager
        self.set_title("Shortcuts")
        self.set_content_width(500)
        self.set_content_height(440)

        self.page = Adw.PreferencesPage()
        close = Gtk.Button(label="Close")
        close.connect("clicked", self.on_close_clicked)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New shortcut")
        add.connect("clicked", self.on_add_clicked)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        header.pack_start(close)
        header.pack_end(add)

        self.toasts = Adw.ToastOverlay(child=self.page)
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        self.groups: list[Adw.PreferencesGroup] = []
        self.refresh()

    def on_close_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    def on_add_clicked(self, _button: Gtk.Button) -> None:
        self.edit(None)

    def refresh(self) -> None:
        for group in self.groups:
            self.page.remove(group)
        self.groups = []

        group = Adw.PreferencesGroup(
            title="Shortcuts",
            description="Each shortcut opens several tunnels at once from the main menu.",
        )
        if not self.manager.bundles:
            group.add(
                Adw.ActionRow(
                    title="No shortcuts yet",
                    subtitle="Use the + button to create the first one.",
                )
            )
        for name, keys in self.manager.bundles.items():
            labels = presenter.bundle_summary(keys, self.manager.tunnels)
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(name),
                subtitle=GLib.markup_escape_text(labels),
            )
            row.set_subtitle_lines(2)

            edit = Gtk.Button(
                icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Edit"
            )
            edit.add_css_class("flat")
            edit.connect("clicked", self.on_edit_clicked, name)
            row.add_suffix(edit)

            delete = Gtk.Button(
                icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Delete"
            )
            delete.add_css_class("flat")
            delete.connect("clicked", self.on_delete_clicked, name)
            row.add_suffix(delete)

            group.add(row)

        self.page.add(group)
        self.groups.append(group)

    def on_edit_clicked(self, _button: Gtk.Button, name: str) -> None:
        self.edit(name)

    def on_delete_clicked(self, _button: Gtk.Button, name: str) -> None:
        self.delete(name)

    def edit(self, name: str | None) -> None:
        BundleDialog(self.window, self.manager, name, self.save).present(self)

    def save(self, name: str, keys: list[str], original: str | None) -> None:
        if original is not None and original != name:
            self.manager.bundles.pop(original, None)
        self.manager.bundles[name] = keys
        self.manager.save_config()
        self.window.rebuild()
        self.refresh()
        self.toasts.add_toast(Adw.Toast(title=f"Shortcut '{name}' saved", timeout=3))

    def delete(self, name: str) -> None:
        self.manager.bundles.pop(name, None)
        self.manager.save_config()
        self.window.rebuild()
        self.refresh()
        self.toasts.add_toast(Adw.Toast(title=f"Shortcut '{name}' deleted", timeout=3))
