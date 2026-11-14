import datetime
import json
from collections import OrderedDict
from unittest.mock import MagicMock, patch

import pytest

from fixgw import database
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


def _raw_item():
    item = MagicMock()
    item.value = None
    return item


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
    items = {"ENG1": _raw_item(), "ALT": _raw_item()}
    sleeps = []

    def stop_after_second_snapshot(duration):
        sleeps.append(duration)
        if len(sleeps) == 2:
            thread.getout = True

    with patch.object(data_playback.database, "get_raw_item", side_effect=lambda key: items[key]), patch.object(
        data_playback.time, "sleep", side_effect=stop_after_second_snapshot
    ):
        thread.run()

    assert items["ENG1"].value == (101.5, 1, 0, 0, 0)
    assert items["ALT"].value == (2500, 0, 0, 0, 0)
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
    item = _raw_item()
    sleeps = []

    def stop_after_playback_or_quit_wait(duration):
        sleeps.append(duration)
        if duration == 5:
            thread.getout = True

    with patch.object(data_playback.database, "get_raw_item", return_value=item), patch.object(
        data_playback.time, "sleep", side_effect=stop_after_playback_or_quit_wait
    ):
        thread.run()

    assert item.value == (300.0, 0, 0, 0, 0)
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

    with patch.object(data_playback.database, "get_raw_item") as get_raw_item, patch.object(
        data_playback.time, "sleep", side_effect=stop_after_quit_wait
    ):
        thread.run()

    get_raw_item.assert_not_called()
    parent.quit.assert_called_once()


def _db_item(tol, old=False, flags=(False, False, False, False)):
    item = database.db_item("LAT")
    item.tol = tol
    item.value = (51.5,) + flags
    if old:
        if tol:
            item.timestamp -= (tol + 1000) / 1000
        else:
            item.old = True
    return item


FLAGS = ["none", "annunciate", "old", "bad", "fail", "secfail"]


@pytest.mark.parametrize("path", ["callback", "snapshot"])
@pytest.mark.parametrize("tol", [0, 100])
@pytest.mark.parametrize("flag", FLAGS)
def test_each_flag_round_trips_through_record_and_playback(tmp_path, flag, tol, path):
    # Recorded as the database reads it, (value, annunciate, old, bad, fail,
    # secfail); played back onto an item with every flag set, so a flag
    # that does not come back is as visible as one that comes back extra.
    flags = tuple(f == flag for f in ["annunciate", "bad", "fail", "secfail"])
    src = _db_item(tol, old=flag == "old", flags=flags)
    recorded = src.value
    assert recorded[FLAGS.index(flag)] if flag != "none" else not any(recorded[1:])

    parent = MagicMock()
    parent.config = {"key_prefixes": "all"}
    parent.db_read = MagicMock(return_value=recorded)
    with patch.object(data_recorder.database, "listkeys", return_value=["LAT"]):
        recorder = data_recorder.MainThread(parent)
        if path == "callback":
            recorder.persist("LAT", recorded)
        else:
            recorder.get_all_data(callbacks=False)
    _write_playback_file(tmp_path / "playback.json", snapshots=[json.loads(json.dumps(recorder.data))])

    dst = _db_item(tol, old=True, flags=(True, True, True, True))
    thread = data_playback.MainThread(_parent(tmp_path, ["{CONFIG}/playback.json"]))

    def stop(duration):
        thread.getout = True

    with patch.object(data_playback.database, "get_raw_item", return_value=dst), patch.object(
        data_playback.time, "sleep", side_effect=stop
    ):
        thread.run()

    assert dst.value == recorded


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
