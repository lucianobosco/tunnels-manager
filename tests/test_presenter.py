"""Tests for the presenter: every decision the window makes, without a window."""

from __future__ import annotations

import time

import pytest

from tunnels_manager import presenter
from tunnels_manager.model import (
    SERVICE_HTTP,
    SERVICE_MYSQL,
    STATE_ERROR,
    STATE_STARTING,
    STATE_UP,
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


def test_state_tag_when_up():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_UP
    tunnel.started_at = time.time() - 90
    text, css, tooltip = presenter.state_tag(tunnel)
    assert (text, css) == ("1m", "state-up")
    assert tooltip == "Up for 1m"


def test_state_tag_when_up_without_a_start_time():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_UP
    text, css, _ = presenter.state_tag(tunnel)
    assert (text, css) == ("0s", "state-up")


def test_state_tag_when_starting():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_STARTING
    assert presenter.state_tag(tunnel)[:2] == ("opening", "state-start")


def test_state_tag_when_failed():
    tunnel = make("a", "A", 1)
    tunnel.state = STATE_ERROR
    tunnel.detail = "the reason"
    assert presenter.state_tag(tunnel) == ("error", "state-error", "the reason")
    tunnel.detail = ""
    assert presenter.state_tag(tunnel)[2] == "The tunnel failed"


def test_state_tag_when_stopped():
    assert presenter.state_tag(make("a", "A", 1)) == ("stopped", "state-down", "")


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


def test_environment_tooltip():
    production = make("a", "A", 1, env="pro")
    staging = make("b", "B", 2, env="pre")
    assert presenter.environment_tooltip(production) == "Production"
    assert presenter.environment_tooltip(staging) == "PRE environment"


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


def test_first_selectable_key():
    assert presenter.first_selectable_key(["a", "b"], {"b"}) == "b"
    assert presenter.first_selectable_key(["a", "b"], {"a", "b"}) == "a"
    assert presenter.first_selectable_key(["a"], set()) is None
    assert presenter.first_selectable_key([], {"a"}) is None


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
        "service": SERVICE_MYSQL,
        "database": "shop",
        "env": "pro",
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
    assert tunnel.group == "Databases"
    assert tunnel.database == "shop"
    assert tunnel.env == "pro"


def test_validate_tunnel_form_uses_the_service_group():
    tunnel, _ = presenter.validate_tunnel_form(form(service=SERVICE_HTTP), [])
    assert tunnel.group == "Services"


def test_validate_tunnel_form_defaults_the_local_host():
    tunnel, _ = presenter.validate_tunnel_form(form(local_host="  "), [])
    assert tunnel.local_host == "127.0.0.1"


@pytest.mark.parametrize("field", ["remote_port", "local_port"])
def test_validate_tunnel_form_rejects_bad_ports(field):
    tunnel, error = presenter.validate_tunnel_form(form(**{field: "nope"}), [])
    assert tunnel is None
    assert "Ports must be numbers" in error


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
    editing = make("shop", "Shop", 15001, extra_args=["--flag"])
    tunnel, error = presenter.validate_tunnel_form(form(), [editing], editing)
    assert error == ""
    assert tunnel.key == "shop"
    assert tunnel.extra_args == ["--flag"]


def test_validate_tunnel_form_keeps_the_key_when_renaming():
    editing = make("shop", "Shop", 15001)
    tunnel, _ = presenter.validate_tunnel_form(form(label="Renamed"), [editing], editing)
    assert tunnel.key == "shop"
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
