# fix-gateway #131 (AER-5091): every shipped connection file is wired in.  A
# plugin is reachable from preferences.yaml.custom alone only when
# preferences.yaml maps an include name to its file, default.yaml lists that
# include name, and its load: flag is a key under enabled:.  Five plugins once
# shipped with none of the three, so their documented flag did nothing.

import os

import pytest
import yaml

CONFIG = "src/fixgw/config"
# test.yaml is the unit-test fixture plugin, not a shipped connection.
UNWIRED = {"test.yaml"}


def load(name):
    with open(os.path.join(CONFIG, name)) as f:
        return yaml.safe_load(f)


PREFS = load("preferences.yaml")
DEFAULT = load("default.yaml")
FILES = sorted(
    f
    for f in os.listdir(os.path.join(CONFIG, "connections"))
    if f.endswith(".yaml") and f not in UNWIRED
)


@pytest.mark.parametrize("name", FILES)
def test_connection_file_is_wired(name):
    path = "connections/" + name
    includes = [k for k, v in PREFS["includes"].items() if v == path]
    assert len(includes) == 1, "no preferences.yaml include for " + path
    assert includes[0] in DEFAULT["connections"]["include"]
    for con, body in load(path).items():
        flag = body["load"]
        # load: yes is an always-on plugin (netfix, state_persist); load: no
        # is a file nothing can turn on short of editing it.
        if flag is True:
            continue
        assert isinstance(flag, str), "{} has load: {}".format(con, flag)
        assert flag in PREFS["enabled"], "{} not under enabled:".format(flag)


def test_issue_131_plugins_ship_off():
    for flag in ["NAVIGATOR", "GARMIN_GNX375", "CANAEROSPACE", "DRONECAN", "ONSPEED"]:
        assert PREFS["enabled"][flag] is False
