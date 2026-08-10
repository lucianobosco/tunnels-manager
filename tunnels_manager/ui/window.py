"""The main window: the tunnel table plus the connection panel."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .. import APP_NAME, PROJECT_URL, __version__, presenter
from ..config import config_file
from ..manager import TunnelManager
from ..model import TYPE_IAP, Tunnel, port_is_free
from .dialogs import BundleDialog, BundlesDialog, LogWindow, TunnelDialog
from .panel import ConnectionPanel
from .rows import TunnelRow
from .util import clear_box, set_clipboard

#: How often the watchdog runs and the labels refresh.
TICK_MS = 500
#: Below this width the panel overlays the table instead of shrinking it.
NARROW_WIDTH = 760


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, manager: TunnelManager):
        super().__init__(application=app, title=APP_NAME)
        self.manager = manager
        self.syncing = False
        self.rows: dict[str, TunnelRow] = {}
        self.log_windows: dict[str, LogWindow] = {}
        self.conflicts: dict[int, list[Tunnel]] = {}
        self.lists: list[Gtk.ListBox] = []
        self.selected_key: str | None = None
        self.groups: list[Gtk.Widget] = []

        # Wide enough that long names are not cut with the panel open, tall enough for
        # a handful of tunnels with compact rows.
        self.set_default_size(1100, 540)
        self.set_size_request(420, 320)

        self.title_widget = Adw.WindowTitle(title=APP_NAME, subtitle="")
        header = Adw.HeaderBar(title_widget=self.title_widget)

        start_all = Gtk.Button(
            icon_name="media-playback-start-symbolic", tooltip_text="Open every tunnel"
        )
        start_all.connect("clicked", self.on_start_all)
        stop_all = Gtk.Button(
            icon_name="media-playback-stop-symbolic", tooltip_text="Close every tunnel"
        )
        stop_all.connect("clicked", self.on_stop_all)
        header.pack_start(start_all)
        header.pack_start(stop_all)

        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Main menu")
        header.pack_end(self.menu_button)
        add_button = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New tunnel")
        add_button.connect("clicked", self.on_add_clicked)
        header.pack_end(add_button)

        self.panel_toggle = Gtk.ToggleButton(
            icon_name="dialog-information-symbolic",
            tooltip_text="Connection details (Ctrl+I)",
            active=True,
        )
        header.pack_end(self.panel_toggle)

        self.groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.groups_box.set_margin_top(12)
        self.groups_box.set_margin_bottom(16)
        self.groups_box.set_margin_start(14)
        self.groups_box.set_margin_end(14)
        scroller = Gtk.ScrolledWindow(
            child=self.groups_box,
            hexpand=True,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )

        self.panel = ConnectionPanel(self)
        self.split = Adw.OverlaySplitView(
            content=scroller,
            sidebar=self.panel,
            sidebar_position=Gtk.PackType.END,
            max_sidebar_width=320,
        )
        self.panel_toggle.bind_property(
            "active",
            self.split,
            "show-sidebar",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        self.toasts = Adw.ToastOverlay(child=self.split)

        # A duplicated port cannot be waved through: the second tunnel to start would
        # fail, or worse, you would connect to the wrong database.
        self.banner = Adw.Banner(revealed=False, button_label="Open tunnels.yaml")
        self.banner.connect("button-clicked", self.on_banner_clicked)

        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)
        self.set_content(toolbar)

        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(f"max-width: {NARROW_WIDTH}px")
        )
        breakpoint_.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint_)

        self.install_actions()
        self.rebuild()

        manager.on_change = lambda: GLib.idle_add(self.sync)
        manager.on_event = lambda message: GLib.idle_add(self.toast, message)
        manager.on_port_busy = lambda tunnel: GLib.idle_add(self.toast_port_busy, tunnel)
        self.tick_id = GLib.timeout_add(TICK_MS, self.tick)
        self.connect("close-request", self.on_close_request)

    # -- actions ------------------------------------------------------------ #

    def install_actions(self) -> None:
        simple = {
            "start-all": self.act_start_all,
            "stop-all": self.act_stop_all,
            "reload": self.act_reload,
            "add": self.act_add,
            "open-config": self.act_open_config,
            "about": self.act_about,
            "bundles": self.act_bundles,
            "toggle-panel": self.act_toggle_panel,
        }
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        with_key = {
            "logs": self.act_logs,
            "restart": self.act_restart,
            "free-port": self.act_free_port,
            "copy-endpoint": self.act_copy_endpoint,
            "copy-command": self.act_copy_command,
            "edit": self.act_edit,
            "delete": self.act_delete,
            "bundle": self.act_bundle,
        }
        for name, callback in with_key.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType("s"))
            action.connect("activate", callback)
            self.add_action(action)

    def tunnel_from(self, param: GLib.Variant) -> Tunnel | None:
        return self.manager.tunnels.get(param.get_string())

    def on_start_all(self, _button: Gtk.Button) -> None:
        self.manager.start_all()

    def on_stop_all(self, _button: Gtk.Button) -> None:
        self.manager.stop_all()

    def on_add_clicked(self, _button: Gtk.Button) -> None:
        self.open_tunnel_dialog(None)

    def on_banner_clicked(self, _banner: Adw.Banner) -> None:
        self.open_config_file()

    def act_start_all(self, *_args) -> None:
        self.manager.start_all()

    def act_stop_all(self, *_args) -> None:
        self.manager.stop_all()

    def act_reload(self, *_args) -> None:
        self.reload_config(notify=True)

    def act_add(self, *_args) -> None:
        self.open_tunnel_dialog(None)

    def act_open_config(self, *_args) -> None:
        self.open_config_file()

    def act_bundles(self, *_args) -> None:
        BundlesDialog(self).present(self)

    def act_toggle_panel(self, *_args) -> None:
        self.panel_toggle.set_active(not self.panel_toggle.get_active())

    def act_about(self, *_args) -> None:
        about = Adw.AboutWindow(
            transient_for=self,
            application_name=APP_NAME,
            version=__version__,
            developer_name="Luciano Bosco",
            website=PROJECT_URL,
            issue_url=f"{PROJECT_URL}/issues",
            comments=(
                "A small manager for Google Cloud IAP tunnels and port-forwards.\n\n"
                f"Configuration: {config_file()}"
            ),
            license_type=Gtk.License.MIT_X11,
        )
        about.present()

    def act_logs(self, _action, param) -> None:
        tunnel = self.tunnel_from(param)
        if tunnel is None:
            return
        existing = self.log_windows.get(tunnel.key)
        if existing is not None:
            existing.present()
            return
        window = LogWindow(self, tunnel)
        self.log_windows[tunnel.key] = window
        window.connect("close-request", self.on_log_closed, tunnel.key)
        window.present()

    def on_log_closed(self, _window: LogWindow, key: str) -> bool:
        self.log_windows.pop(key, None)
        return False

    def act_restart(self, _action, param) -> None:
        tunnel = self.tunnel_from(param)
        if tunnel is not None:
            self.manager.restart(tunnel)

    def act_copy_endpoint(self, _action, param) -> None:
        tunnel = self.tunnel_from(param)
        if tunnel is not None:
            set_clipboard(self, tunnel.endpoint)
            self.toast(f"Copied {tunnel.endpoint}")

    def act_copy_command(self, _action, param) -> None:
        tunnel = self.tunnel_from(param)
        if tunnel is not None:
            set_clipboard(self, tunnel.command_str())
            self.toast("Command copied")

    def act_edit(self, _action, param) -> None:
        tunnel = self.tunnel_from(param)
        if tunnel is None:
            return
        if tunnel.active:
            self.toast("Close the tunnel before editing it")
            return
        if tunnel.type != TYPE_IAP:
            self.toast("This one is a free-form command: edit it in tunnels.yaml")
            return
        self.open_tunnel_dialog(tunnel)

    def act_delete(self, _action, param) -> None:
        tunnel = self.tunnel_from(param)
        if tunnel is None:
            return
        prompt = presenter.delete_prompt(tunnel)
        dialog = Adw.AlertDialog(heading=prompt.heading, body=prompt.body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, self.on_delete_answered, tunnel)

    def on_delete_answered(self, dialog: Adw.AlertDialog, result, tunnel: Tunnel) -> None:
        if dialog.choose_finish(result) != "delete":
            return
        self.manager.stop(tunnel, quiet=True)
        self.manager.tunnels.pop(tunnel.key, None)
        if tunnel.key in self.manager.order:
            self.manager.order.remove(tunnel.key)
        for keys in self.manager.bundles.values():
            if tunnel.key in keys:
                keys.remove(tunnel.key)
        self.manager.save_config()
        self.rebuild()
        self.toast(f"'{tunnel.label}' deleted")

    def act_free_port(self, _action, param) -> None:
        """Close whatever holds the tunnel's port, then open the tunnel."""
        tunnel = self.tunnel_from(param)
        if tunnel is None:
            return
        port = tunnel.local_port
        owner = self.manager.port_owner(port)

        if owner is None:
            if port_is_free(tunnel.local_host, port):
                self.toast(f"Port {port} is already free")
                self.manager.start(tunnel)
            else:
                self.toast(f"Port {port} is busy and I could not tell which process holds it")
            return

        if not owner.mine:
            self.toast(f"PID {owner.pid} belongs to another user: that would need sudo")
            return

        other = self.manager.tunnels.get(owner.own_tunnel) if owner.own_tunnel else None
        prompt = presenter.free_port_prompt(tunnel, owner, other)
        dialog = Adw.AlertDialog(heading=prompt.heading, body=prompt.body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("kill", "Close it and open the tunnel")
        dialog.set_response_appearance("kill", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, self.on_free_port_answered, (tunnel, port))

    def on_free_port_answered(self, dialog: Adw.AlertDialog, result, data) -> None:
        tunnel, port = data
        if dialog.choose_finish(result) != "kill":
            return
        freed, message = self.manager.free_port(port)
        self.toast(message)
        if freed:
            self.manager.start(tunnel)

    def act_bundle(self, _action, param) -> None:
        name = param.get_string()
        started = self.manager.start_bundle(name)
        self.toast(presenter.bundle_message(name, started))

    # -- dialogs and files -------------------------------------------------- #

    def open_tunnel_dialog(self, tunnel: Tunnel | None) -> None:
        TunnelDialog(self, tunnel, self.save_tunnel).present(self)

    def save_tunnel(self, tunnel: Tunnel, original: Tunnel | None) -> None:
        if original is None:
            key = presenter.unique_key(tunnel.key, set(self.manager.tunnels))
            tunnel.key = key
            self.manager.tunnels[key] = tunnel
            self.manager.order.append(key)
        else:
            tunnel.state = original.state
            self.manager.tunnels[tunnel.key] = tunnel
        self.manager.save_config()
        self.rebuild()
        self.toast("Saved to tunnels.yaml")

    def open_config_file(self) -> None:
        path = config_file()
        if not path.exists():
            self.manager.save_config()
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))
        launcher.launch(self, None, None)
        self.toast("After saving the file, use 'Reload configuration'")

    # -- building the list -------------------------------------------------- #

    def rebuild(self) -> None:
        clear_box(self.groups_box)
        self.rows = {}
        self.lists = []

        tunnels = self.manager.ordered()
        if not tunnels:
            self.groups_box.append(
                Adw.StatusPage(
                    icon_name="list-add-symbolic",
                    title="No tunnels yet",
                    description="Use the + button in the header to create the first one.",
                    vexpand=True,
                )
            )
        else:
            for index, (name, items) in enumerate(presenter.group_tunnels(tunnels).items()):
                title = Gtk.Label(label=name, xalign=0)
                title.add_css_class("group-title")
                title.set_margin_top(0 if index == 0 else 14)
                title.set_margin_bottom(4)
                self.groups_box.append(title)

                listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
                listbox.add_css_class("boxed-list")
                listbox.connect("row-selected", self.on_row_selected)
                for tunnel in items:
                    row = TunnelRow(self, tunnel)
                    self.rows[tunnel.key] = row
                    listbox.append(row)
                self.groups_box.append(listbox)
                self.lists.append(listbox)

        self.menu_button.set_menu_model(self.build_menu())
        self.restore_selection()
        self.sync()

    def on_row_selected(self, listbox: Gtk.ListBox, row: TunnelRow | None) -> None:
        if self.syncing or row is None:
            return
        # Groups are separate list boxes, so the others must let go of their selection.
        self.syncing = True
        try:
            for other in self.lists:
                if other is not listbox:
                    other.unselect_all()
        finally:
            self.syncing = False
        self.selected_key = row.tunnel.key
        self.panel.show_tunnel(row.tunnel)

    def restore_selection(self) -> None:
        """Keep the same tunnel selected across a rebuild.

        With nothing selected, pick the first row: GTK focuses it anyway, and the panel
        then starts with something useful instead of an empty state.
        """
        row = self.rows.get(self.selected_key) if self.selected_key else None
        if row is None:
            first = presenter.first_selectable_key(self.manager.order, set(self.rows))
            row = self.rows.get(first) if first else None
            self.selected_key = first
        self.syncing = True
        try:
            if row is not None:
                parent = row.get_parent()
                if isinstance(parent, Gtk.ListBox):
                    parent.select_row(row)
        finally:
            self.syncing = False
        self.panel.show_tunnel(row.tunnel if row is not None else None)
        if row is None:
            self.selected_key = None

    def build_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        bulk = Gio.Menu()
        bulk.append("Open every tunnel", "win.start-all")
        bulk.append("Close every tunnel", "win.stop-all")
        menu.append_section(None, bulk)

        # "Shortcuts", not "Groups": groups are the sections of the table.
        shortcuts = Gio.Menu()
        for name, keys in self.manager.bundles.items():
            item = Gio.MenuItem.new(presenter.bundle_menu_label(name, keys), None)
            item.set_action_and_target_value("win.bundle", GLib.Variant("s", name))
            shortcuts.append_item(item)
        shortcuts.append("Manage shortcuts…", "win.bundles")
        menu.append_section("Shortcuts", shortcuts)

        configuration = Gio.Menu()
        configuration.append("New tunnel…", "win.add")
        configuration.append("Open tunnels.yaml", "win.open-config")
        configuration.append("Reload configuration", "win.reload")
        menu.append_section(None, configuration)

        about = Gio.Menu()
        about.append("About", "win.about")
        menu.append_section(None, about)
        return menu

    def reload_config(self, notify: bool = False) -> None:
        warnings = self.manager.load_config()
        self.rebuild()
        if warnings:
            self.toast(warnings[0])
        elif notify:
            self.toast("Configuration reloaded")

    # -- refresh ------------------------------------------------------------ #

    def tick(self) -> bool:
        self.manager.poll()
        self.sync()
        return True

    def sync(self) -> bool:
        if set(self.manager.tunnels) != set(self.rows):
            self.rebuild()
            return False

        self.conflicts = self.manager.port_conflicts()
        self.update_banner()

        self.syncing = True
        try:
            for row in self.rows.values():
                row.sync()
        finally:
            self.syncing = False

        self.title_widget.set_subtitle(
            presenter.status_subtitle(self.manager.active_count(), len(self.manager.tunnels))
        )
        return False

    def update_banner(self) -> None:
        text = presenter.banner_text(self.conflicts)
        self.banner.set_title(text or "")
        self.banner.set_revealed(bool(text))

    def toast(self, message: str) -> bool:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))
        return False

    def toast_port_busy(self, tunnel: Tunnel) -> bool:
        """A toast with a button: freeing the port is the only thing you want here."""
        owner = self.manager.port_owner(tunnel.local_port)
        message = presenter.busy_port_toast(tunnel, owner, self.manager.tunnels)
        toast = Adw.Toast(title=message.text, timeout=8)
        if message.offer_to_free:
            toast.set_button_label("Free it")
            toast.set_action_name("win.free-port")
            toast.set_action_target_value(GLib.Variant("s", tunnel.key))
        self.toasts.add_toast(toast)
        return False

    # -- closing ------------------------------------------------------------ #

    def on_close_request(self, *_args) -> bool:
        active = self.manager.active_count()
        if not active:
            self.shutdown()
            return False

        prompt = presenter.quit_prompt(active)
        dialog = Adw.AlertDialog(heading=prompt.heading, body=prompt.body)
        dialog.add_response("cancel", "Stay open")
        dialog.add_response("quit", "Close everything")
        dialog.set_response_appearance("quit", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None, self.on_close_answered)
        return True

    def on_close_answered(self, dialog: Adw.AlertDialog, result) -> None:
        if dialog.choose_finish(result) == "quit":
            self.shutdown()
            self.destroy()

    def shutdown(self) -> None:
        if getattr(self, "tick_id", None):
            GLib.source_remove(self.tick_id)
            self.tick_id = None
        for window in list(self.log_windows.values()):
            window.destroy()
        self.log_windows.clear()
        self.manager.stop_all()


__all__ = ["BundleDialog", "BundlesDialog", "LogWindow", "MainWindow", "TunnelDialog"]
