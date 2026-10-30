import datetime
import json
from collections import OrderedDict
from unittest.mock import MagicMock, patch

import pytest

from fixgw import plugin as plugin_base
from fixgw.plugins import data_playback, data_recorder


def _parent(tmp_path, files, config=None):
    parent = MagicMock()
    parent.config = {
        "CONFIGPATH": str(tmp_path),
        "files": files,
    }
    if config:
        parent.config.update(config)
    parent.log = MagicMock()
    parent.quit = MagicMock()
    return parent


def _write_playback_file(path, frequency=1000, starttime=None, snapshots=None):
    if starttime is None:
        starttime = datetime.datetime(2026, 5, 1, 12, 0, 0)
    if snapshots is None:
        snapshots = []

    lines = [
        {
            "frequency": str(frequency),
            "starttime": starttime.isoformat(),
        }
    ]
    lines.extend(snapshots)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return starttime


def test_run_replays_snapshot_values_from_config_relative_file(tmp_path):
    playback_file = tmp_path / "playback.json"
    _write_playback_file(
        playback_file,
        frequency=250,
        snapshots=[
            {"ENG1": [100.0, 0, 0, 0, 0, 0]},
            {"ENG1": [101.5, 1, 0, 0, 0, 0], "ALT": [2500, 0, 0, 0, 0, 0]},
        ],
    )
    parent = _parent(tmp_path, ["{CONFIG}/playback.json"])
    thread = data_playback.MainThread(parent)
    sleeps = []

    def stop_after_second_snapshot(duration):
        sleeps.append(duration)
        if len(sleeps) == 2:
            thread.getout = True

    with patch.object(data_playback, "replay") as replay, patch.object(
        data_playback.time, "sleep", side_effect=stop_after_second_snapshot
    ):
        thread.run()

    assert [c.args for c in replay.call_args_list] == [
        ("ENG1", [100.0, 0, 0, 0, 0, 0]),
        ("ENG1", [101.5, 1, 0, 0, 0, 0]),
        ("ALT", [2500, 0, 0, 0, 0, 0]),
    ]
    assert len(sleeps) == 2
    parent.quit.assert_not_called()


def test_run_skips_snapshots_until_start_time(tmp_path):
    playback_file = tmp_path / "playback.json"
    start = _write_playback_file(
        playback_file,
        frequency=1000,
        snapshots=[
            {"ENG1": [100.0, 0, 0, 0, 0, 0]},
            {"ENG1": [200.0, 0, 0, 0, 0, 0]},
            {"ENG1": [300.0, 0, 0, 0, 0, 0]},
        ],
    )
    parent = _parent(
        tmp_path,
        ["{CONFIG}/playback.json"],
        {"start_time": start + datetime.timedelta(seconds=2)},
    )
    thread = data_playback.MainThread(parent)
    sleeps = []

    def stop_after_playback_or_quit_wait(duration):
        sleeps.append(duration)
        if duration == 5:
            thread.getout = True

    with patch.object(data_playback, "replay") as replay, patch.object(
        data_playback.time, "sleep", side_effect=stop_after_playback_or_quit_wait
    ):
        thread.run()

    assert [c.args for c in replay.call_args_list] == [
        ("ENG1", [200.0, 0, 0, 0, 0, 0]),
        ("ENG1", [300.0, 0, 0, 0, 0, 0]),
    ]
    parent.quit.assert_called_once()
    assert sleeps[-1] == 5


def test_run_with_start_time_after_file_quits_without_writing_values(tmp_path):
    playback_file = tmp_path / "playback.json"
    start = _write_playback_file(
        playback_file,
        frequency=1000,
        snapshots=[{"ENG1": [100.0, 0, 0, 0, 0, 0]}],
    )
    parent = _parent(
        tmp_path,
        ["{CONFIG}/playback.json"],
        {"start_time": start + datetime.timedelta(minutes=5)},
    )
    thread = data_playback.MainThread(parent)

    def stop_after_quit_wait(duration):
        if duration == 5:
            thread.getout = True

    with patch.object(data_playback, "replay") as replay, patch.object(
        data_playback.time, "sleep", side_effect=stop_after_quit_wait
    ):
        thread.run()

    replay.assert_not_called()
    parent.quit.assert_called_once()


def _record(database, keys):
    """Run the real data_recorder callback and return its rows as JSON would."""
    parent = MagicMock()
    parent.config = {
        "CONFIGPATH": "",
        "filepath": "{CONFIG}",
        "frequency": 1000,
        "key_prefixes": keys,
    }
    parent.db_read = database.read
    parent.db_callback_add = lambda key, function: database.callback_add(
        "recorder", key, function, None
    )
    return data_recorder.MainThread(parent)


def _make_stale(database, key):
    # The way a key goes old live: it stops being written, its age passes
    # tol, and the update thread flips old.
    item = database.get_raw_item(key)
    item.timestamp -= (item.tol + 1) / 1000
    database.update()


def test_round_trip_reproduces_recorded_flags(database):
    """Record flagged values, replay them into a fresh database, compare db_read."""
    recorder = _record(database, ["IAS", "ALT", "VS", "TAS", "GS"])
    database.write("IAS", (120.0, True, False, False, False))
    database.write("ALT", (4500.0, False, True, False, False))
    database.write("VS", (-500.0, False, False, True, False))
    database.write("TAS", (130.0, False, False, False, True))
    database.write("GS", (110.0, True, False, True, False))
    _make_stale(database, "GS")
    expected = {key: database.read(key) for key in recorder.data}
    rows = json.loads(json.dumps(recorder.data))

    assert expected["GS"][2] is True  # the recording carries a real old
    assert rows["GS"] == [110.0, 1, 1, 0, 1, 0]

    database.init("src/fixgw/config/database.yaml")
    for key, recorded in rows.items():
        data_playback.replay(key, recorded)

    assert {key: database.read(key) for key in rows} == expected


def test_replay_of_old_alone_sends_one_stale_callback(database):
    database.write("ALT", (4500.0, False, False, False, False))
    seen = []
    database.callback_add("t", "ALT", lambda k, v, u: seen.append(v), None)

    data_playback.replay("ALT", [4500.0, 0, 1, 0, 0, 0])

    assert seen == [(4500.0, False, True, False, False, False)]


def test_replay_clears_old_on_a_key_without_tol(database):
    item = database.get_raw_item("MAPRANGE")
    assert item.tol == 0

    data_playback.replay("MAPRANGE", [3, 0, 1, 0, 0, 0])
    assert database.read("MAPRANGE") == (3, False, True, False, False, False)

    data_playback.replay("MAPRANGE", [4, 0, 0, 0, 0, 0])
    assert database.read("MAPRANGE") == (4, False, False, False, False, False)


def test_replay_rejects_a_row_without_six_fields(database):
    with pytest.raises(ValueError, match="ALT has 5 fields"):
        data_playback.replay("ALT", [4500.0, 0, 0, 0, 0])


def test_stop_sets_getout_flag():
    thread = data_playback.MainThread(_parent(None, []))

    thread.stop()

    assert thread.getout is True


def test_plugin_lifecycle_starts_and_stops_thread():
    with patch.object(data_playback, "MainThread") as thread_cls:
        thread = thread_cls.return_value
        thread.is_alive.return_value = False
        plugin = data_playback.Plugin("data_playback", {}, None)

        plugin.run()
        plugin.stop()

    thread.start.assert_called_once()
    thread.stop.assert_called_once()
    thread.join.assert_not_called()
    assert plugin.get_status() == OrderedDict()


def test_plugin_stop_raises_when_thread_will_not_stop():
    with patch.object(data_playback, "MainThread") as thread_cls:
        thread = thread_cls.return_value
        thread.is_alive.return_value = True
        plugin = data_playback.Plugin("data_playback", {}, None)

        with pytest.raises(plugin_base.PluginFail):
            plugin.stop()

    thread.stop.assert_called_once()
    thread.join.assert_called_once_with(1.0)
