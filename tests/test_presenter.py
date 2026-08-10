"""Tests for the presenter: every decision the window makes, without a window."""

from __future__ import annotations

import time

import pytest

from tunnels_manager import presenter
from tunnels_manager.model import (
    STATE_ERROR,
    STATE_STARTING,
    STATE_UP,
    TYPE_COMMAND,
    PortOwner,
    Tunnel,
)


def make(key: str, label: str, port: int, group: str = "Databases", **kwargs) -> Tunnel:
    return Tunnel(
        key=key,
        label=label,
        instance="host",
        remote_port=3306,
        zone="zone",
        project="project",
        local_port=port,
        group=group,
        **kwargs,
    )


# -- the table -------------------------------------------------------------- #


def test_group_tunnels_keeps_the_configured_order():
    tunnels = [
        make("a", "A", 1),
        make("b", "B", 2, group="Services"),
        make("c", "C", 3),
    ]
    grouped = presenter.group_tunnels(tunnels)
    assert list(grouped) == ["Databases", "Services"]
    assert [tunnel.key for tunnel in grouped["Databases"]] == ["a", "c"]


def test_group_tunnels_with_nothing():
    assert presenter.group_tunnels([]) == {}


@pytest.mark.parametrize(
    ("active", "total", "expected"),
    [(0, 3, "3 tunnels · none open"), (1, 3, "1 of 3 open"), (3, 3, "3 of 3 open")],
)
def test_status_subtitle(active, total, expected):
    assert presenter.status_subtitle(active, total) == expected


def test_state_card_when_up():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_UP
    tunnel.started_at = time.time() - 90
    assert presenter.state_card(tunnel) == (presenter.KIND_UP, "ESTABLISHED", "1m", "Up for 1m")


def test_state_card_when_up_without_a_start_time():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_UP
    kind, word, second, _ = presenter.state_card(tunnel)
    assert (kind, word, second) == (presenter.KIND_UP, "ESTABLISHED", "0s")


def test_state_card_when_starting():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_STARTING
    assert presenter.state_card(tunnel)[:3] == (presenter.KIND_BUSY, "CONNECTING", "opening")


def test_state_card_when_failed():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_ERROR
    tunnel.detail = "Address already in use"
    assert presenter.state_card(tunnel) == (
        presenter.KIND_ERR,
        "FAILED",
        "port in use",
        "Address already in use",
    )
    tunnel.detail = ""
    assert presenter.state_card(tunnel)[3] == "The tunnel failed"


def test_state_card_when_stopped():
    """The second line is an em dash and never empty: the word must not jump when a
    stopped tunnel starts connecting and a counter appears under it."""
    assert presenter.state_card(make("a", "A", 1)) == (
        presenter.KIND_OFF,
        "STOPPED",
        "\u2014",
        "",
    )


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("Address already in use", "port in use"),
        ("bind: already in use", "port in use"),
        ("Your credentials are invalid", "no credentials"),
        ("You do not have permission", "denied"),
        ("Permission denied", "denied"),
        ("Connection timed out", "timeout"),
        ("timeout while dialing", "timeout"),
        ("could not resolve host", "unknown host"),
        ("instance was not found", "not found"),
        ("something nobody has seen before", "see the log"),
    ],
)
def test_error_hint(detail, expected):
    assert presenter.error_hint(detail) == expected


def test_row_tooltip_with_and_without_a_detail():
    tunnel = make("a", "A", 1)
    assert presenter.row_tooltip(tunnel).startswith("host:3306")
    tunnel.detail = "went wrong"
    assert presenter.row_tooltip(tunnel).startswith("went wrong\n\n")


def test_clash_tooltip():
    first = make("a", "A", 15001)
    second = make("b", "B", 15001)
    conflicts = {15001: [first, second]}
    assert presenter.clash_tooltip(first, conflicts) == "Port 15001 is shared with: B"
    assert presenter.clash_tooltip(make("c", "C", 15002), conflicts) == ""


def test_clash_tooltip_ignores_a_group_of_one():
    tunnel = make("a", "A", 15001)
    assert presenter.clash_tooltip(tunnel, {15001: [tunnel]}) == ""


def test_exposure_tooltip_names_the_host():
    tunnel = make("a", "A", 1, local_host="0.0.0.0")
    assert "0.0.0.0" in presenter.exposure_tooltip(tunnel)


def test_banner_text_without_conflicts():
    assert presenter.banner_text({}) == ""


def test_banner_text_for_one_conflict():
    text = presenter.banner_text({15001: [make("a", "Shop", 15001), make("b", "Reports", 15001)]})
    assert "Local port 15001" in text
    assert "'Shop' and 'Reports'" in text


def test_banner_text_for_several_conflicts():
    conflicts = {
        15002: [make("c", "C", 15002), make("d", "D", 15002)],
        15001: [make("a", "A", 15001), make("b", "B", 15001)],
    }
    text = presenter.banner_text(conflicts)
    assert text == "2 local ports are used twice: 15001, 15002."


# -- messages --------------------------------------------------------------- #


def test_bundle_message():
    assert presenter.bundle_message("Daily", 2) == "'Daily': opening 2 tunnel(s)"
    assert presenter.bundle_message("Daily", 0) == "'Daily' was already open"


def test_busy_port_toast_for_one_of_our_tunnels():
    tunnel = make("a", "A", 15001)
    other = make("b", "Reports", 15001)
    owner = PortOwner(1, "python3", "cmd", mine=True, own_tunnel="b")
    message = presenter.busy_port_toast(tunnel, owner, {"b": other})
    assert message.text == "Port 15001 is used by 'Reports'"
    assert message.offer_to_free is True


def test_busy_port_toast_for_a_foreign_process():
    tunnel = make("a", "A", 15001)
    owner = PortOwner(42, "python3", "cmd", mine=True)
    message = presenter.busy_port_toast(tunnel, owner, {})
    assert message.text == "Port 15001 is held by python3 (PID 42)"
    assert message.offer_to_free is True


def test_busy_port_toast_does_not_offer_to_kill_another_users_process():
    tunnel = make("a", "A", 15001)
    owner = PortOwner(42, "root-thing", "cmd", mine=False)
    assert presenter.busy_port_toast(tunnel, owner, {}).offer_to_free is False


def test_busy_port_toast_without_an_owner():
    tunnel = make("a", "A", 15001)
    message = presenter.busy_port_toast(tunnel, None, {})
    assert message.text == "Port 15001 is busy"
    assert message.offer_to_free is True


def test_free_port_prompt_for_one_of_our_tunnels():
    tunnel = make("a", "Shop", 15001)
    other = make("b", "Reports", 15001)
    owner = PortOwner(1, "python3", "cmd", mine=True, own_tunnel="b")
    prompt = presenter.free_port_prompt(tunnel, owner, other)
    assert prompt.heading == "Close 'Reports'?"
    assert "'Shop' needs" in prompt.body


def test_free_port_prompt_for_a_tunnel_like_process():
    tunnel = make("a", "Shop", 15001)
    owner = PortOwner(42, "python3", "gcloud compute start-iap-tunnel host 3306", mine=True)
    prompt = presenter.free_port_prompt(tunnel, owner, None)
    assert prompt.heading == "Close the process on port 15001?"
    assert "PID 42" in prompt.body
    assert "does not look like a tunnel" not in prompt.body


def test_free_port_prompt_warns_about_an_unrelated_process():
    tunnel = make("a", "Shop", 15001)
    owner = PortOwner(42, "python3", "python3 -m http.server", mine=True)
    assert "does not look like a tunnel" in presenter.free_port_prompt(tunnel, owner, None).body


def test_delete_and_quit_prompts():
    assert "Shop" in presenter.delete_prompt(make("a", "Shop", 1)).body
    assert presenter.quit_prompt(2).body.startswith("2 tunnel(s)")


# -- forms ------------------------------------------------------------------ #


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3306", 3306), (" 80 ", 80), ("0", None), ("65536", None), ("nope", None), ("", None)],
)
def test_parse_port(text, expected):
    assert presenter.parse_port(text) == expected


def test_unique_key():
    assert presenter.unique_key("shop", set()) == "shop"
    assert presenter.unique_key("shop", {"shop"}) == "shop-2"
    assert presenter.unique_key("shop", {"shop", "shop-2"}) == "shop-3"


def form(**overrides) -> presenter.TunnelForm:
    values = {
        "label": "Shop",
        "key": "",
        "instance": "my-bastion",
        "remote_port": "3306",
        "zone": "europe-west1-d",
        "project": "my-project",
        "local_host": "127.0.0.1",
        "local_port": "15001",
    }
    values.update(overrides)
    return presenter.TunnelForm(**values)


def test_validate_tunnel_form_builds_a_tunnel():
    tunnel, error = presenter.validate_tunnel_form(form(), [])
    assert error == ""
    assert tunnel.key == "shop"
    assert tunnel.site_packages is True


def test_validate_tunnel_form_makes_the_key_from_the_name():
    tunnel, _ = presenter.validate_tunnel_form(form(label="BI Reporting"), [])
    assert tunnel.key == "bi-reporting"


def test_validate_tunnel_form_takes_the_key_it_is_given():
    tunnel, _ = presenter.validate_tunnel_form(form(key="Whatever I Type"), [])
    assert tunnel.key == "whatever-i-type"


def test_validate_tunnel_form_rejects_a_key_somebody_else_has():
    tunnel, error = presenter.validate_tunnel_form(
        form(key="taken", local_port="15009"), [make("taken", "Taken", 15002)]
    )
    assert tunnel is None
    assert "already taken" in error


def test_validate_tunnel_form_falls_back_to_a_generic_key():
    """slugify always yields something, so a name of punctuation still gets a key."""
    tunnel, error = presenter.validate_tunnel_form(form(label="///"), [])
    assert error == ""
    assert tunnel.key == "tunnel"


def test_validate_tunnel_form_groups_where_it_is_told():
    tunnel, _ = presenter.validate_tunnel_form(form(group="  Reporting  "), [])
    assert tunnel.group == "Reporting"


def test_validate_tunnel_form_falls_back_to_the_default_group():
    """Nothing infers a group any more: the form is the only place that decides."""
    tunnel, _ = presenter.validate_tunnel_form(form(group=""), [])
    assert tunnel.group == "Tunnels"


def test_validate_tunnel_form_builds_a_command_tunnel():
    tunnel, error = presenter.validate_tunnel_form(
        form(type=TYPE_COMMAND, command="kubectl port-forward svc/x 1:2", instance=""), []
    )
    assert error == ""
    assert tunnel.type == TYPE_COMMAND
    assert tunnel.command_line == "kubectl port-forward svc/x 1:2"


def test_validate_tunnel_form_requires_a_command():
    tunnel, error = presenter.validate_tunnel_form(form(type=TYPE_COMMAND, command="   "), [])
    assert tunnel is None
    assert error == "Give the command that opens the port."


def test_validate_tunnel_form_splits_the_extra_flags():
    tunnel, _ = presenter.validate_tunnel_form(form(extra_args="--one --two=3"), [])
    assert tunnel.extra_args == ["--one", "--two=3"]


def test_validate_tunnel_form_defaults_the_local_host():
    tunnel, _ = presenter.validate_tunnel_form(form(local_host="  "), [])
    assert tunnel.local_host == "127.0.0.1"


def test_validate_tunnel_form_rejects_a_bad_local_port():
    tunnel, error = presenter.validate_tunnel_form(form(local_port="nope"), [])
    assert tunnel is None
    assert "local port must be a number" in error


def test_validate_tunnel_form_rejects_a_bad_remote_port():
    tunnel, error = presenter.validate_tunnel_form(form(remote_port="nope"), [])
    assert tunnel is None
    assert "remote port must be a number" in error


def test_validate_tunnel_form_ignores_the_remote_port_of_a_command():
    """A command opens the port itself, so there is no remote port to get wrong."""
    tunnel, error = presenter.validate_tunnel_form(
        form(type=TYPE_COMMAND, command="sleep 1", remote_port="nope"), []
    )
    assert error == ""
    assert tunnel.remote_port == 0


def test_validate_tunnel_form_requires_a_name():
    tunnel, error = presenter.validate_tunnel_form(form(label="  "), [])
    assert tunnel is None
    assert error == "Give the tunnel a name."


@pytest.mark.parametrize("field", ["instance", "zone", "project"])
def test_validate_tunnel_form_requires_the_target(field):
    tunnel, error = presenter.validate_tunnel_form(form(**{field: ""}), [])
    assert tunnel is None
    assert "required" in error


def test_validate_tunnel_form_rejects_a_used_port():
    existing = [make("other", "Reports", 15001)]
    tunnel, error = presenter.validate_tunnel_form(form(), existing)
    assert tunnel is None
    assert "already used by 'Reports'" in error
    assert "first free one" not in error


def test_validate_tunnel_form_suggests_a_free_port():
    existing = [make("other", "Reports", 15001)]
    tunnel, error = presenter.validate_tunnel_form(
        form(), existing, next_free_port=lambda _start: 15010
    )
    assert tunnel is None
    assert "The first free one is 15010." in error


def test_validate_tunnel_form_ignores_the_tunnel_being_edited():
    """Its own port is not a clash with itself. The flags now come from the form, not
    from the tunnel being edited, because the form is where they are typed."""
    editing = make("shop", "Shop", 15001, extra_args=["--old"])
    tunnel, error = presenter.validate_tunnel_form(form(extra_args="--new"), [editing], editing)
    assert error == ""
    assert tunnel.key == "shop"
    assert tunnel.extra_args == ["--new"]


def test_validate_tunnel_form_lets_the_key_be_renamed():
    """The key is editable now; the window is what rewrites the shortcuts that used it."""
    editing = make("shop", "Shop", 15001)
    tunnel, _ = presenter.validate_tunnel_form(
        form(label="Renamed", key="renamed"), [editing], editing
    )
    assert tunnel.key == "renamed"
    assert tunnel.label == "Renamed"


# -- shortcuts -------------------------------------------------------------- #


def test_validate_bundle_accepts_two_tunnels():
    assert presenter.validate_bundle("Pair", ["a", "b"], {}) == ""


def test_validate_bundle_requires_a_name():
    assert "name" in presenter.validate_bundle("  ", ["a", "b"], {})


def test_validate_bundle_rejects_a_duplicate_name():
    error = presenter.validate_bundle("Daily", ["a", "b"], {"Daily": ["a"]})
    assert "already exists" in error


def test_validate_bundle_allows_keeping_its_own_name():
    assert presenter.validate_bundle("Daily", ["a", "b"], {"Daily": ["a"]}, "Daily") == ""


def test_validate_bundle_needs_at_least_two_tunnels():
    assert "at least two" in presenter.validate_bundle("Solo", ["a"], {})


def test_bundle_summary_skips_unknown_keys():
    tunnels = {"a": make("a", "Shop", 1), "b": make("b", "Reports", 2)}
    assert presenter.bundle_summary(["a", "ghost", "b"], tunnels) == "Shop, Reports"


def test_bundle_menu_label():
    assert presenter.bundle_menu_label("Daily", ["a", "b"]) == "Open 'Daily' (2 tunnels)"


# -- the open row ----------------------------------------------------------- #


def test_row_subtitle_is_the_project():
    assert presenter.row_subtitle(make("a", "A", 1)) == "project"


def test_row_subtitle_says_kubernetes_when_there_is_no_project():
    tunnel = make("a", "A", 1)
    tunnel.project = ""
    assert presenter.row_subtitle(tunnel) == "kubernetes"


def test_row_subtitle_spells_out_a_tunnel_open_to_the_network():
    tunnel = make("a", "A", 1, local_host="0.0.0.0")
    assert presenter.row_subtitle(tunnel) == "project · 0.0.0.0"


def test_headline_field_is_the_local_endpoint():
    assert presenter.headline_field(make("a", "A", 15001)) == ("host:port", "127.0.0.1:15001")


def test_headline_field_uses_loopback_even_when_bound_to_everything():
    """Bound to 0.0.0.0 you still connect through loopback, so that is what you copy."""
    tunnel = make("a", "A", 15001, local_host="0.0.0.0")
    assert presenter.headline_field(tunnel)[1] == "127.0.0.1:15001"


def test_route_tooltip_for_an_iap_tunnel():
    text = presenter.route_tooltip(make("a", "A", 15001))
    assert "127.0.0.1:15001" in text
    assert "host:3306" in text
    assert "project project · zone zone" in text


def test_route_tooltip_without_a_project():
    tunnel = make("a", "A", 1)
    tunnel.project = ""
    tunnel.zone = ""
    assert "no project set" in presenter.route_tooltip(tunnel)


def test_route_tooltip_for_a_command():
    tunnel = make("a", "A", 15001, type=TYPE_COMMAND, command_line="kubectl port-forward x 1:2")
    tunnel.target_label = "svc/x:2"
    text = presenter.route_tooltip(tunnel)
    assert text == "A command opens 127.0.0.1:15001 and forwards it to svc/x:2"


# -- the NumPy speed-up ----------------------------------------------------- #


def test_numpy_hint_only_once_gcloud_has_asked():
    tunnel = make("a", "A", 1)
    assert presenter.numpy_hint(tunnel) == ""
    tunnel.log.append("WARNING: consider installing NumPy. For instructions,")
    assert presenter.numpy_hint(tunnel) == presenter.NUMPY_INSTALL


def test_numpy_install_asks_gcloud_for_its_own_interpreter():
    """Not a hardcoded path: the interpreter gcloud runs is usually its own bundled one,
    and a NumPy installed for the system Python would never be imported."""
    assert "gcloud info" in presenter.NUMPY_INSTALL
    assert "pip install --user numpy" in presenter.NUMPY_INSTALL


def failing_import_tunnel() -> Tunnel:
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_ERROR
    tunnel.log.append("ModuleNotFoundError: No module named 'six'")
    return tunnel


def test_sitepackages_warning_when_an_import_broke_the_tunnel():
    assert "shadowing" in presenter.sitepackages_warning(failing_import_tunnel())


def test_sitepackages_warning_stays_quiet_when_the_tunnel_is_fine():
    tunnel = failing_import_tunnel()
    tunnel.state = STATE_UP
    assert presenter.sitepackages_warning(tunnel) == ""


def test_sitepackages_warning_stays_quiet_when_the_speed_up_is_off():
    """No point blaming a switch that is already off."""
    tunnel = failing_import_tunnel()
    tunnel.site_packages = False
    assert presenter.sitepackages_warning(tunnel) == ""


def test_sitepackages_warning_stays_quiet_for_other_failures():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_ERROR
    tunnel.log.append("ERROR: (gcloud.compute.start-iap-tunnel) Permission denied")
    assert presenter.sitepackages_warning(tunnel) == ""
