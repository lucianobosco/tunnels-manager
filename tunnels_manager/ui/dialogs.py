"""Dialogs: live log, tunnel editor, and the shortcut manager."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from .. import presenter
from ..model import TYPE_COMMAND, TYPE_IAP, Tunnel
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
    """Create or edit a tunnel: every field the file accepts, and what each one is for."""

    #: The two ways a tunnel can be opened, in the order the selector shows them. This is
    #: what the row's pill reads: IAP or PORT-FWD.
    TYPES = (TYPE_IAP, TYPE_COMMAND)
    TYPE_LABELS = ("Google Cloud IAP tunnel", "A command of my own")

    def __init__(self, parent, tunnel: Tunnel | None, on_save):
        super().__init__()
        self.on_save = on_save
        self.original = tunnel
        self.manager = parent.manager
        self.set_title("Edit tunnel" if tunnel else "New tunnel")
        self.set_content_width(560)

        page = Adw.PreferencesPage()

        identity = Adw.PreferencesGroup(title="What this tunnel is")
        self.entry_label = self.entry(
            "Name", "What the list shows. Yours to choose.", "BI reporting"
        )
        self.entry_key = self.entry(
            "Key",
            "The id used inside the file and by the shortcuts. Left empty on a new "
            "tunnel, it is made from the name.",
            "bi-reporting",
        )
        self.entry_type = self.combo(
            "How it opens",
            self.TYPE_LABELS,
            "IAP builds the gcloud command from the fields below. The other runs whatever "
            "you write and watches the local port. This is the pill the row shows.",
            "Google Cloud IAP tunnel",
        )
        self.entry_type.connect("notify::selected", self.on_type_changed)
        self.entry_group = self.entry(
            "Group",
            "The heading this tunnel is listed under, and the only place that decides it. "
            "Pick one that exists or type a new one; empty means the service decides.",
            "Reporting",
        )
        self.entry_group.add_prefix(self.group_picker())
        for row in (
            self.entry_label,
            self.entry_key,
            self.entry_type,
            self.entry_group,
        ):
            identity.add(row)

        self.remote_group = Adw.PreferencesGroup(
            title="Where it goes",
            description=(
                "The far end. This is what the identity-aware proxy opens for you, and "
                "what you are not allowed to reach directly."
            ),
        )
        self.entry_instance = self.entry(
            "Instance",
            "The machine IAP connects to -- usually a bastion, not the database itself.",
            "my-bastion",
        )
        self.entry_remote_port = self.entry(
            "Remote port", "The port to reach on that machine.", "3306"
        )
        self.entry_zone = self.entry("Zone", "Where that machine lives.", "europe-west1-d")
        self.entry_project = self.entry(
            "Project", "The Google Cloud project it belongs to.", "my-project-pro"
        )
        self.entry_extra_args = self.entry(
            "Extra gcloud flags",
            "Optional, split like a shell would. Appended to the gcloud command as they are.",
            "--iap-tunnel-disable-connection-check",
        )
        for row in (
            self.entry_instance,
            self.entry_remote_port,
            self.entry_zone,
            self.entry_project,
            self.entry_extra_args,
        ):
            self.remote_group.add(row)

        self.command_group = Adw.PreferencesGroup(
            title="The command",
            description=(
                "It has to open the local port below and keep running: the app watches "
                "that port to know whether the tunnel is up."
            ),
        )
        self.entry_command = self.entry(
            "Command",
            "Run as it is written. Anything that opens a local port and stays up.",
            "kubectl -n platform port-forward svc/dashboard 8080:80",
        )
        self.entry_target_label = self.entry(
            "Target, for display",
            "Optional. What the row shows in its target column; the command's first word "
            "is used when this is empty.",
            "svc/dashboard:80",
        )
        self.command_group.add(self.entry_command)
        self.command_group.add(self.entry_target_label)

        local = Adw.PreferencesGroup(
            title="Where you connect",
            description=(
                "No two tunnels may share a local port: the second one to start would "
                "fail, or you would query the wrong database."
            ),
        )
        self.entry_local_host = self.entry(
            "Local host",
            "127.0.0.1 keeps the tunnel on this machine. 0.0.0.0 opens it to everyone on "
            "your network, which the row then spells out.",
            "127.0.0.1",
        )
        self.entry_local_port = self.entry("Local port", "What you point your client at.", "13306")
        local.add(self.entry_local_host)
        local.add(self.entry_local_port)

        speed = Adw.PreferencesGroup(
            title="Throughput",
            description=(
                "An IAP tunnel masks every byte it carries, and gcloud does that in pure "
                "Python unless it can import NumPy. Only matters for large transfers."
            ),
        )
        self.entry_site_packages = Adw.SwitchRow(
            title="Let gcloud use NumPy",
            subtitle="Turn it off if the tunnel fails with an import error.",
            active=True,
        )
        self.entry_site_packages.add_suffix(
            self.help_button(
                "Lets gcloud look outside its own packages, where a NumPy installed for "
                "its interpreter would be. The README has the install line.",
                "on",
            )
        )
        speed.add(self.entry_site_packages)

        for group in (identity, self.remote_group, self.command_group, local, speed):
            page.add(group)

        if tunnel is not None:
            self.entry_label.set_text(tunnel.label)
            self.entry_key.set_text(tunnel.key)
            self.entry_group.set_text(tunnel.group)
            self.entry_instance.set_text(tunnel.instance)
            self.entry_remote_port.set_text(str(tunnel.remote_port))
            self.entry_zone.set_text(tunnel.zone)
            self.entry_project.set_text(tunnel.project)
            self.entry_extra_args.set_text(" ".join(tunnel.extra_args))
            self.entry_command.set_text(tunnel.command_line)
            self.entry_target_label.set_text(tunnel.target_label)
            self.entry_local_host.set_text(tunnel.local_host)
            self.entry_local_port.set_text(str(tunnel.local_port))
            self.entry_site_packages.set_active(tunnel.site_packages)
            if tunnel.type in self.TYPES:
                self.entry_type.set_selected(self.TYPES.index(tunnel.type))
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
        self.on_type_changed()

    # -- building the rows -------------------------------------------------- #

    @staticmethod
    def help_button(text: str, example: str) -> Gtk.MenuButton:
        """The ? beside a field. A popover rather than a tooltip: it can be read on
        purpose, it stays open, and it holds an example."""
        body = Gtk.Label(
            label=text + "\n\nExample:  " + example,
            wrap=True,
            xalign=0,
            max_width_chars=38,
        )
        for setter in (
            body.set_margin_top,
            body.set_margin_bottom,
            body.set_margin_start,
            body.set_margin_end,
        ):
            setter(10)
        button = Gtk.MenuButton(
            icon_name="help-about-symbolic",
            valign=Gtk.Align.CENTER,
            popover=Gtk.Popover(child=body),
            tooltip_text="What this is for",
        )
        button.add_css_class("flat")
        button.set_cursor_from_name("pointer")
        return button

    def group_picker(self) -> Gtk.MenuButton:
        """The groups that already exist, as a shortcut into the entry beside it.

        A dropdown alone could not hold a new name, and an entry alone makes you remember
        how you spelled the others. So: an entry, with the existing ones one click away.
        """
        names = sorted({tunnel.group for tunnel in self.manager.ordered() if tunnel.group})
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for setter in (
            box.set_margin_top,
            box.set_margin_bottom,
            box.set_margin_start,
            box.set_margin_end,
        ):
            setter(6)
        button = Gtk.MenuButton(
            icon_name="pan-down-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Groups that already exist",
        )
        button.add_css_class("flat")
        button.set_cursor_from_name("pointer")
        if not names:
            label = Gtk.Label(label="No groups yet -- type the first one.")
            label.set_margin_end(6)
            box.append(label)
        for name in names:
            item = Gtk.Button(label=name, has_frame=False)
            item.get_child().set_xalign(0)
            item.set_cursor_from_name("pointer")
            item.connect("clicked", self.on_group_picked, name, button)
            box.append(item)
        button.set_popover(Gtk.Popover(child=box))
        return button

    def on_group_picked(self, _button, name: str, menu: Gtk.MenuButton) -> None:
        self.entry_group.set_text(name)
        popover = menu.get_popover()
        if popover is not None:
            popover.popdown()

    def entry(self, title: str, help_text: str, example: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.add_suffix(self.help_button(help_text, example))
        return row

    def combo(self, title: str, options, help_text: str, example: str) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(list(options)))
        row.add_suffix(self.help_button(help_text, example))
        return row

    def on_type_changed(self, *_args) -> None:
        """Only the fields that belong to the chosen kind are on screen."""
        is_command = self.TYPES[self.entry_type.get_selected()] == TYPE_COMMAND
        self.remote_group.set_visible(not is_command)
        self.command_group.set_visible(is_command)

    # -- saving ------------------------------------------------------------- #

    def on_cancel(self, _button: Gtk.Button) -> None:
        self.close()

    def read_form(self) -> presenter.TunnelForm:
        return presenter.TunnelForm(
            label=self.entry_label.get_text(),
            key=self.entry_key.get_text(),
            instance=self.entry_instance.get_text(),
            remote_port=self.entry_remote_port.get_text(),
            zone=self.entry_zone.get_text(),
            project=self.entry_project.get_text(),
            local_host=self.entry_local_host.get_text(),
            local_port=self.entry_local_port.get_text(),
            type=self.TYPES[self.entry_type.get_selected()],
            command=self.entry_command.get_text(),
            target_label=self.entry_target_label.get_text(),
            group=self.entry_group.get_text(),
            extra_args=self.entry_extra_args.get_text(),
            site_packages=self.entry_site_packages.get_active(),
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
