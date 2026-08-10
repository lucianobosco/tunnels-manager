"""Tests for reading and writing tunnels.yaml."""

from __future__ import annotations

import textwrap

import yaml

from tunnels_manager import config
from tunnels_manager.model import TYPE_COMMAND, Tunnel


def write_config(text: str) -> None:
    config.config_dir().mkdir(parents=True, exist_ok=True)
    config.config_file().write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def test_paths_follow_xdg_config_home(config_home):
    assert config.config_home() == config_home
    assert config.config_dir() == config_home / "tunnels-manager"
    assert config.config_file().name == "tunnels.yaml"


def test_config_home_defaults_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: tmp_path))
    assert config.config_home() == tmp_path / ".config"


def test_example_file_sits_next_to_the_package():
    assert config.example_file().name == "tunnels.dist.yaml"
    assert config.example_file().exists()


def test_ensure_config_file_seeds_from_the_example(config_home):
    note = config.ensure_config_file()
    assert note is not None
    assert "tunnels.dist.yaml" in note
    assert config.config_file().exists()
    assert "tunnels:" in config.config_file().read_text()
    # Second call finds the file and says nothing.
    assert config.ensure_config_file() is None


def test_ensure_config_file_falls_back_to_the_template(config_home, monkeypatch):
    monkeypatch.setattr(config, "example_file", lambda: config_home / "missing.yaml")
    note = config.ensure_config_file()
    assert note is not None
    assert "built-in template" in note
    assert "my-database" in config.config_file().read_text()


def test_read_raw_returns_data_and_the_creation_note(config_home):
    data, warnings = config.read_raw()
    assert "tunnels" in data
    assert any("Created" in warning for warning in warnings)


def test_read_raw_reports_broken_yaml(config_home):
    write_config("tunnels: [oops")
    data, warnings = config.read_raw()
    assert data == {}
    assert any("could not be read" in warning for warning in warnings)


def test_read_raw_rejects_a_non_mapping(config_home):
    write_config("- just\n- a\n- list")
    data, warnings = config.read_raw()
    assert data == {}
    assert any("mapping" in warning for warning in warnings)


def test_read_raw_accepts_an_empty_file(config_home):
    write_config("")
    data, warnings = config.read_raw()
    assert data == {}
    assert warnings == []


def test_parse_tunnels_reads_every_field(written_config):
    raw, _ = config.read_raw()
    tunnels, warnings = config.parse_tunnels(raw)
    assert warnings == []
    assert [tunnel.key for tunnel in tunnels] == ["shop", "reports", "dashboard"]

    shop = tunnels[0]
    assert shop.group == "Databases"

    dashboard = tunnels[2]
    assert dashboard.type == TYPE_COMMAND
    assert dashboard.group == "Services"
    assert dashboard.target_label == "svc/dash:80 (team)"


def test_parse_tunnels_defaults_service_by_type(config_home):
    write_config(
        """
        tunnels:
          - key: db
            instance: bastion
            remote_port: 3306
            zone: z
            project: p
            local_port: 1111
          - key: cmd
            type: command
            command: kubectl port-forward svc/x 2222:80
            local_port: 2222
        """
    )
    tunnels, warnings = config.parse_tunnels(config.read_raw()[0])
    assert warnings == []
    # Nothing infers a group any more: without one in the file, both land in the same
    # place, and the form is where a tunnel is given its heading.
    assert tunnels[0].group == "Tunnels"
    assert tunnels[1].group == "Tunnels"


def test_parse_tunnels_group_override(config_home):
    write_config(
        """
        tunnels:
          - key: db
            instance: bastion
            remote_port: 3306
            zone: z
            project: p
            local_port: 1111
            group: My own group
        """
    )
    tunnels, _ = config.parse_tunnels(config.read_raw()[0])
    assert tunnels[0].group == "My own group"


def test_parse_tunnels_uses_fallback_names(config_home):
    write_config(
        """
        tunnels:
          - instance: bastion
            remote_port: 3306
            zone: z
            project: p
            local_port: 1111
        """
    )
    tunnels, _ = config.parse_tunnels(config.read_raw()[0])
    assert tunnels[0].key == "tunnel-1"
    assert tunnels[0].label == "Tunnel 1"


def test_parse_tunnels_label_falls_back_to_the_key(config_home):
    write_config(
        """
        tunnels:
          - key: only-key
            instance: bastion
            remote_port: 3306
            zone: z
            project: p
            local_port: 1111
        """
    )
    tunnels, _ = config.parse_tunnels(config.read_raw()[0])
    assert tunnels[0].label == "only-key"


def test_parse_tunnels_skips_bad_entries(config_home):
    write_config(
        """
        tunnels:
          - just a string
          - key: no-instance
            local_port: 1111
          - key: bad-port
            instance: bastion
            remote_port: nope
            zone: z
            project: p
            local_port: 1112
          - key: empty-command
            type: command
            command: "   "
            local_port: 1113
          - key: good
            instance: bastion
            remote_port: 3306
            zone: z
            project: p
            local_port: 1114
        """
    )
    tunnels, warnings = config.parse_tunnels(config.read_raw()[0])
    assert [tunnel.key for tunnel in tunnels] == ["good"]
    assert len(warnings) == 4
    assert "not a mapping" in warnings[0]


def test_parse_tunnels_reports_duplicate_keys(config_home):
    write_config(
        """
        tunnels:
          - key: twice
            instance: bastion
            remote_port: 3306
            zone: z
            project: p
            local_port: 1111
          - key: twice
            instance: bastion
            remote_port: 3307
            zone: z
            project: p
            local_port: 1112
        """
    )
    tunnels, warnings = config.parse_tunnels(config.read_raw()[0])
    assert len(tunnels) == 1
    assert any("Duplicate key" in warning for warning in warnings)


def test_parse_tunnels_with_no_tunnels_key(config_home):
    write_config("bundles: {}")
    tunnels, warnings = config.parse_tunnels(config.read_raw()[0])
    assert tunnels == []
    assert warnings == []


def test_parse_bundles(written_config):
    raw, _ = config.read_raw()
    bundles, warnings = config.parse_bundles(raw, {"shop", "reports", "dashboard"})
    assert bundles == {"Daily work": ["shop", "reports"]}
    assert warnings == []


def test_parse_bundles_warns_about_unknown_keys(written_config):
    raw, _ = config.read_raw()
    bundles, warnings = config.parse_bundles(raw, {"shop"})
    assert bundles == {"Daily work": ["shop"]}
    assert any("do not exist" in warning for warning in warnings)


def test_parse_bundles_drops_a_shortcut_with_no_known_keys(written_config):
    raw, _ = config.read_raw()
    bundles, warnings = config.parse_bundles(raw, set())
    assert bundles == {}
    assert warnings


def test_parse_bundles_rejects_a_non_list(config_home):
    write_config("bundles:\n  Broken: not-a-list")
    bundles, warnings = config.parse_bundles(config.read_raw()[0], set())
    assert bundles == {}
    assert any("list of tunnel keys" in warning for warning in warnings)


def test_parse_bundles_with_no_bundles_key(config_home):
    write_config("tunnels: []")
    bundles, warnings = config.parse_bundles(config.read_raw()[0], set())
    assert bundles == {}
    assert warnings == []


def test_dump_keeps_the_header_and_round_trips(tunnel):
    text = config.dump([tunnel], {"Pair": ["shop", "other"]})
    assert text.startswith("# Tunnels Manager configuration")
    data = yaml.safe_load(text)
    assert data["tunnels"][0]["key"] == "shop"
    assert data["bundles"] == {"Pair": ["shop", "other"]}


def test_dump_omits_empty_bundles(tunnel):
    data = yaml.safe_load(config.dump([tunnel], {}))
    assert "bundles" not in data


def test_write_creates_the_directory(config_home, tunnel):
    config.write([tunnel], {})
    assert config.config_file().exists()
    text = config.config_file().read_text()
    assert "shop" in text
    assert text.startswith("# Tunnels Manager configuration")


def test_write_then_parse_is_stable(config_home):
    original = Tunnel(
        key="dash",
        label="Dash",
        type=TYPE_COMMAND,
        command_line="kubectl port-forward svc/dash 8080:80",
        local_port=8080,
        target_label="svc/dash:80",
        group="Services",
    )
    config.write([original], {"Solo": ["dash"]})
    raw, _ = config.read_raw()
    tunnels, warnings = config.parse_tunnels(raw)
    bundles, bundle_warnings = config.parse_bundles(raw, {"dash"})
    assert warnings == []
    assert bundle_warnings == []
    assert tunnels[0].command_line == original.command_line
    assert tunnels[0].group == "Services"
    assert bundles == {"Solo": ["dash"]}
