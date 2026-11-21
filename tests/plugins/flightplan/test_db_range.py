"""OBS against an installed database that cannot carry it (AER-6144).

OBS2 publishes FPLSTATE = 4 and raised FPLSTATE's ``max`` to 4 in
database/flightplan.yaml. An install whose copy of that file was edited keeps
the old one (``max: 3``) across an upgrade, and the database clamps an
out-of-range write without a flag: on the OBS3 bench run the bus read 3, and
the display showed SUSP while the engine flew OBS. These tests load the
database from a copy of the shipped config tree with that one line put back,
the way such an install does.
"""

import importlib
import json
import logging
import shutil
from pathlib import Path

import pytest

import fixgw.database as fixdatabase
import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.flightplan.engine as engine
import fixgw.quorum as quorum

from .routes import load
from .test_plugin import make_config, write_route

CONFIG = Path(__file__).resolve().parents[3] / "src" / "fixgw" / "config"
FPLSTATE_MAX_4 = "- key: FPLSTATE\n"


def _init_database(config_dir):
    importlib.reload(fixdatabase)
    importlib.reload(quorum)
    quorum.nodeid = 1
    quorum.vote_key = f"QVOTE{quorum.nodeid}"
    fixdatabase.init(str(config_dir / "database.yaml"))
    return fixdatabase


def _pre_obs2_config(tmp_path):
    """The shipped config tree with FPLSTATE's range as it was before OBS2."""
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG, config_dir)
    path = config_dir / "database" / "flightplan.yaml"
    text = path.read_text()
    start = text.index(FPLSTATE_MAX_4)
    end = text.index("\n- key:", start + 1)
    entry = text[start:end]
    assert "max: 4" in entry
    path.write_text(text[:start] + entry.replace("max: 4", "max: 3") + text[end:])
    return config_dir


@pytest.fixture
def old_database(tmp_path):
    return _init_database(_pre_obs2_config(tmp_path))


@pytest.fixture
def new_database(tmp_path):
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG, config_dir)
    return _init_database(config_dir)


def _fly_a_leg(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    write_route(database, [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)], seq=1)
    database.write("GPS_FIX_TYPE", 3)
    database.write("LAT", 0.0)
    database.write("LONG", 0.2)
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 2")
    assert database.read("FPLCMDACK")[0] == 1
    return pl


def test_shipped_database_carries_obs(new_database, tmp_path):
    # The control: the fixture's config copy is otherwise the shipped one,
    # and with it OBS reaches the bus as 4.
    database = new_database
    pl = _fly_a_leg(database, tmp_path)

    database.write("FPLCMD", "2 OBS 310")

    assert database.read("FPLCMDACK")[0] == 2
    assert database.read("FPLSTATE")[0] == engine.STATE_OBS
    assert pl.get_status()["Database"] == "OK"


def test_obs_against_a_pre_obs2_database_is_not_a_silent_susp(old_database, tmp_path):
    database = old_database
    pl = _fly_a_leg(database, tmp_path)

    database.write("FPLCMD", "2 OBS 310")
    pl.thread._run_update_cycle()

    # Whatever the engine does, the bus says the same thing: never OBS
    # flown under FPLSTATE 3.
    assert database.read("FPLSTATE")[0] == pl.thread.engine._display_state()
    # It refuses OBS, with a reason the pilot can be shown.
    assert database.read("FPLCMDACK")[0] == -2
    assert database.read("FPLMSG")[0] == engine.OBS_DATABASE_REASON
    assert pl.thread.engine.obs_crs is None
    assert database.read("FPLSTATE")[0] == engine.STATE_LEG


def test_pre_obs2_database_is_reported_at_startup(old_database, tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "FPLSTATE min 0, max 3" in errors[0]
    assert "database/flightplan.yaml" in errors[0]
    assert pl.get_status()["Database"] == "FPLSTATE min 0, max 3: OBS unavailable"


def test_shipped_database_reports_nothing_at_startup(new_database, tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        flightplan.Plugin("flightplan", make_config(tmp_path), {})

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_obs_off_still_works_against_a_pre_obs2_database(old_database, tmp_path):
    # Nothing can be in OBS here, so OBS OFF answers as it does out of OBS.
    database = old_database
    _fly_a_leg(database, tmp_path)

    database.write("FPLCMD", "2 OBS OFF")

    assert database.read("FPLCMDACK")[0] == -2
    assert database.read("FPLMSG")[0] == "NOT IN OBS"


def test_persisted_obs_restored_onto_a_pre_obs2_database_is_not_flown_as_susp(old_database, tmp_path):
    # A plan saved in OBS by the new gateway, then a restart on the old
    # database file (a rollback of the config, or a gateway on a second box).
    database = old_database
    state_file = tmp_path / "flightplan_state.json"
    e = engine.Engine()
    load(e, [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)], "R", 1)
    e.handle_command("1 ACT 2", (0.0, 0.2, True))
    e.update(0.0, 0.2, 120.0, 0.0, True, True, None, 0.0)
    assert e.handle_command("2 OBS 310", (0.0, 0.2, True))[0] == 2
    state_file.write_text(json.dumps(e.to_persisted_dict()))

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, state_file=str(state_file)), {})
    pl.thread._restore()
    database.write("GPS_FIX_TYPE", 3)
    database.write("GS", 120.0)
    database.write("LAT", 0.0)
    database.write("LONG", 0.2)
    pl.thread._run_update_cycle()

    assert pl.thread.engine.obs_crs is None
    assert database.read("FPLSTATE")[0] == pl.thread.engine._display_state() == engine.STATE_LEG
