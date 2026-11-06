"""Tests for fixgw.plugins.state_persist configuration handling."""

import json

import fixgw.plugins.state_persist as state_persist


def test_state_file_config_placeholder_resolves_under_the_config_directory(database, tmp_path, monkeypatch):
    # fix-gateway#129 (G64): connections/state_persist.yaml documents
    # `state_file: "{CONFIG}/persist_state.json"` as the default. Uncommented
    # as shown, it must read and write in the config directory, not in a
    # literal ./{CONFIG}/ directory under the working directory.
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    state_file = config_dir / "x.json"
    state_file.write_text(json.dumps({"NAVSRC": 2}))

    config = {
        "CONFIGPATH": str(config_dir),
        "state_file": "{CONFIG}/x.json",
        "keys": ["NAVSRC"],
        "restore_delay": 0.0,
    }
    pl = state_persist.Plugin("state_persist", config, {})

    assert pl.thread.path == str(state_file)
    assert pl.thread._to_restore == {"NAVSRC": 2}

    pl.thread.state["NAVSRC"] = 1
    pl.thread.dirty = True
    pl.thread._flush()
    assert json.loads(state_file.read_text()) == {"NAVSRC": 1}
    assert not (tmp_path / "{CONFIG}").exists()
