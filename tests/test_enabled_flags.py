# fix-gateway #147 (AER-5252): every flag under preferences.yaml enabled:
# gates its plugin.  netfix.yaml shipped `load: yes`, a YAML boolean, so the
# server never looked up NETFIX and NETFIX: false did nothing.  These run the
# real main_setup over a copy of the shipped config, with the flag turned off
# the way a user would: in preferences.yaml.custom.

import argparse
import shutil
from unittest.mock import MagicMock, patch

import pytest

import fixgw.server as server

CONFIG = "src/fixgw/config"


@pytest.fixture(autouse=True)
def reset_server_globals(monkeypatch):
    saved = (server.config_path, server.preferences, server.plugin_mods,
             server.plugins, server.log, server.quorum.leader)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    yield
    (server.config_path, server.preferences, server.plugin_mods,
     server.plugins, server.log, server.quorum.leader) = saved


def loaded_plugins(tmp_path, custom):
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG, config_dir)
    (config_dir / "preferences.yaml.custom").write_text(custom)
    config_file = config_dir / "default.yaml"
    args = argparse.Namespace(
        debug=False, verbose=False, daemonize=True,
        config_file=open(config_file), log_config=None,
        playback_start_time=None,
    )
    with (
        patch.object(argparse.ArgumentParser, "parse_known_args",
                     return_value=(args, [])),
        patch("fixgw.server.database.init"),
        patch("fixgw.server.database.write"),
        patch("fixgw.server.load_plugin") as load_plugin,
        patch("fixgw.server.status.initialize"),
        patch("fixgw.server.logging.getLogger", return_value=MagicMock()),
        patch("fixgw.server.logging.config.dictConfig"),
    ):
        server.main_setup()
    return {c.args[0] for c in load_plugin.call_args_list}


@pytest.mark.parametrize("flag,plugin", [
    ("NETFIX", "netfix"),
    ("STATE_PERSIST", "state_persist"),
])
def test_flag_gates_plugin(tmp_path, flag, plugin):
    assert plugin in loaded_plugins(tmp_path / "on", "{}\n")
    off = loaded_plugins(tmp_path / "off", "enabled:\n  {}: false\n".format(flag))
    assert plugin not in off


# AER-5468 (P3f): a top-level preference in preferences.yaml.custom reaches
# the plugins.  config-pull writes ownship_address there for stratux.
def test_custom_preference_reaches_plugins(tmp_path, monkeypatch):
    monkeypatch.setattr(server.plugin, "preferences", {})
    loaded_plugins(tmp_path, 'ownship_address: "AB4549"\n')
    assert server.plugin.preferences["ownship_address"] == "AB4549"
    assert server.plugin.preferences["enabled"]["COMPUTE"] is True
