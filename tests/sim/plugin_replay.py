"""Replay a generated stream through the real ``flightplan`` plugin, headless.

``engine_replay`` drives the engine and models the plugin around it: which
input counts as stale, when a cycle runs. That model is exactly what a
dead GPS link exercises, so for those profiles it would be judging a copy
of the logic under test. This replay instead runs the plugin itself on the
real ``fixgw.database``, with the wall clock both of them read
(``time.time``) replaced by the stream's own time:

- each row's keys are written the way ``data_playback`` writes them, as
  ``[value, annunciate, old, bad, fail, secfail]`` tuples, in row order --
  so the plugin's own callbacks run (route commit on ``FPLSEQ``, commands
  on ``FPLCMD``, a guidance cycle on a ``LAT``/``LONG`` write, rate
  limited as the plugin limits it);
- once a second of stream time, one guidance cycle with no write -- the
  plugin thread's own 1 s tick;
- after each row, the guidance outputs and their flags are read back from
  the database.

What it still does not have that a gateway has: real threads and real
time, so no jitter between a write and the cycle it triggers. The live
replay (``gateway-replay.yaml``) is the end-to-end version.
"""

import contextlib
import importlib
import tempfile
import time
from pathlib import Path

import fixgw.database as fixdatabase
import fixgw.quorum as quorum

DATABASE = Path(__file__).resolve().parents[2] / "src/fixgw/config/database.yaml"

# gateway-replay.yaml's flightplan section
PLUGIN_CONFIG = {"restore_delay": 0.0, "rate_hz": 5, "turn_rate_deg_s": 3.0, "terminal_nm": 30,
                 "approach_ramp_nm": 2.0, "alert_s": 10, "integrity_key": "GPS_ACCURACY_HORIZ",
                 "hal_nm": {"enr": 2.0, "term": 1.0, "lnav": 0.3}, "altitude_key": "ALT"}

OUT_KEYS = ("FPLSTATE", "FPLACTLEG", "FPLPHASE", "FPLAPR", "CDISCALE", "FPLCRS", "FPLXTK", "FPLCDI",
            "FPLTF", "WPFROM", "WPNEXT", "WPNAME", "WPDIS", "FPLACTSEG", "FPLMSG", "FPLCMDACK")
TICK_S = 1.0     # the plugin thread's sleep between timer cycles


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@contextlib.contextmanager
def _clock(t):
    clock = _Clock(t)
    real = time.time
    time.time = clock
    try:
        yield clock
    finally:
        time.time = real


def _fresh_database():
    importlib.reload(fixdatabase)
    importlib.reload(quorum)
    quorum.nodeid = 1
    quorum.vote_key = f"QVOTE{quorum.nodeid}"
    fixdatabase.init(str(DATABASE))
    return fixdatabase


def replay(rows, t0=None):
    """Rows of the generated run, each extended with the plugin's outputs as
    they stood after that row, flagged keys as
    ``[value, annunciate, old, bad, fail, secfail]``."""
    import fixgw.plugins.flightplan as flightplan

    out_rows = []
    with tempfile.TemporaryDirectory() as tmp, _clock(t0 if t0 is not None else time.time()) as clock:
        db = _fresh_database()
        config = dict(PLUGIN_CONFIG, CONFIGPATH=tmp, state_file=str(Path(tmp) / "flightplan_state.json"))
        pl = flightplan.Plugin("flightplan", config, {})
        start = clock.t
        next_tick = start + TICK_S
        for t, row in rows:
            while next_tick <= start + t:
                clock.t = next_tick
                pl.thread._run_update_cycle()
                next_tick += TICK_S
            clock.t = start + t
            for key, value in row.items():
                try:
                    item = db.get_raw_item(key)
                except KeyError:
                    continue          # REF* and other keys the gateway database does not carry
                item.value = (value, 0, 0, 0, 0, 0)
            ext = dict(row)
            for key in OUT_KEYS:
                value, _, _, bad, fail, _ = db.get_raw_item(key).value
                ext[key] = [value, 0, 0, int(bad), int(fail), 0] if (bad or fail) else value
            out_rows.append((t, ext))
    return out_rows
