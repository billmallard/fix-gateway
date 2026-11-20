"""OBS2 (AER-6084): the brief's example flown, coupled to the engine.

"Cleared to TONIE, intercept the 270 course inbound." The plan is FLOUT,
TONIE, OSH, with TONIE active on the 310 course from FLOUT
(makerplane/briefs/gps_obs_mode.md). The synthetic tier's point-mass
aircraft (fixgw.sim.aircraft) steers on the engine's own FPLCRS and FPLXTK,
as a coupled autopilot in GPSS would: the pilot sets OBS 270 30 nm out, the
aircraft intercepts the 270 course, crosses TONIE, flies on FROM it with
TONIE still active, and 4 nm past the fix the pilot cancels OBS and the box
goes on to OSH.

The coordinates are synthetic (TONIE and OSH are the brief's teaching
example, not looked up), with MAGVAR -12 (12 deg east). The synthetic
reference (refnav.py) has no OBS mode, so nothing here compares against it:
the checks are on the flown track and the engine's outputs.
doc/evidence/aer-6084-obs/ plots this flight.
"""

import pytest

import fixgw.geo as geo
from fixgw.plugins.flightplan.engine import STATE_LEG, STATE_OBS, TF_FROM, TF_TO, Engine, Waypoint
from fixgw.sim.aircraft import Aircraft, Performance, State

from ..plugins.flightplan.routes import load

MAGVAR = -12.0
TAS = 120.0
TONIE = (34.70, -120.20)
FLOUT = geo.destination_point(*TONIE, 310.0 - MAGVAR + 180.0, 40.0)
OSH = geo.destination_point(*TONIE, 40.0, 25.0)
OBS_CRS = 270.0
START_NM = 30.0           # out from TONIE on the 310 course
OBS_AT_S = 20              # the pilot sets OBS 270 20 s after the start
OBS_OFF_PAST_NM = 4.0      # and cancels it this far past TONIE


def fly(duration_s=1650):
    """One row a second: t, lat, lon, and the engine's outputs after that
    second's update, plus the command acked that second, if any."""
    e = Engine()
    load(e, [Waypoint("FLOUT", *FLOUT), Waypoint("TONIE", *TONIE), Waypoint("OSH", *OSH)], "OBS", 1)
    lat, lon = geo.destination_point(*TONIE, 310.0 - MAGVAR + 180.0, START_NM)
    ac = Aircraft(Performance(tas_kt=TAS), State(lat, lon, 4000.0, 310.0 - MAGVAR))
    e.handle_command("1 ACT 2", (lat, lon, True))
    seq, rows, obs_off = 1, [], False
    for t in range(duration_s):
        s = ac.s
        cmd = None
        if t == OBS_AT_S:
            seq += 1
            cmd = (f"{seq} OBS {OBS_CRS:g}",) + e.handle_command(f"{seq} OBS {OBS_CRS:g}", (s.lat, s.lon, True))
        out = e.update(s.lat, s.lon, s.gs_kt, MAGVAR, True, True, None, 1000.0 + t, s.alt_ft)
        if (not obs_off and out["state"] == STATE_OBS and out["tf"] == TF_FROM
                and geo.distance_nm(s.lat, s.lon, *TONIE) >= OBS_OFF_PAST_NM):
            seq += 1
            cmd = (f"{seq} OBS OFF",) + e.handle_command(f"{seq} OBS OFF", (s.lat, s.lon, True))
            out = e.update(s.lat, s.lon, s.gs_kt, MAGVAR, True, True, None, 1000.0 + t + 0.5, s.alt_ft)
            obs_off = True
        rows.append({"t": t, "lat": s.lat, "lon": s.lon, "cmd": cmd, **out})
        ac.step(1.0, geo.wrap360(out["crs"] - MAGVAR), out["xtk"], 4000.0)
    return rows


@pytest.fixture(scope="module")
def flight():
    return fly()


def test_obs_270_is_set_and_acked(flight):
    cmds = [r["cmd"] for r in flight if r["cmd"]]
    assert cmds[0] == ("2 OBS 270", 2, "OBS 270")
    assert flight[OBS_AT_S]["state"] == STATE_OBS
    assert flight[OBS_AT_S]["crs"] == pytest.approx(270.0, abs=1e-9)


def test_the_270_course_is_intercepted_before_tonie(flight):
    obs = [r for r in flight if r["state"] == STATE_OBS]
    to_side = [r for r in obs if r["tf"] == TF_TO]
    # 30 nm out on a 40 deg cut it starts about 19 nm left (south) of the
    # line. A 45 deg intercept joins it with about 3 nm to spare: centred
    # within 0.05 nm for the last 2 nm into TONIE.
    assert to_side[0]["xtk"] < -18.0
    last_2nm = [r for r in to_side if r["wp_dis"] <= 2.0]
    assert last_2nm and max(abs(r["xtk"]) for r in last_2nm) < 0.05


def test_tonie_flips_to_from_and_stays_active(flight):
    obs = [r for r in flight if r["state"] == STATE_OBS]
    flips = [(a["tf"], b["tf"]) for a, b in zip(obs, obs[1:]) if a["tf"] != b["tf"]]
    assert flips == [(TF_TO, TF_FROM)]
    assert all(r["wp_name"] == "TONIE" and r["act_leg"] == 2 for r in obs)
    assert not any(r.get("msg", "").startswith("SEQ") for r in obs)
    from_side = [r for r in obs if r["tf"] == TF_FROM]
    # Outbound on the 270 course, still centred, until the pilot cancels.
    assert max(abs(r["xtk"]) for r in from_side) < 0.05
    assert max(r["wp_dis"] for r in from_side) == pytest.approx(OBS_OFF_PAST_NM, abs=0.1)


def test_obs_off_goes_back_to_the_plan(flight):
    off = next(r for r in flight if r["cmd"] and r["cmd"][0].endswith("OBS OFF"))
    assert off["cmd"][1:] == (3, "SEQ TONIE -> OSH")
    assert off["state"] == STATE_LEG and off["wp_name"] == "OSH" and off["act_leg"] == 3
    after = flight[flight.index(off):]
    assert all(r["state"] == STATE_LEG and r["wp_name"] == "OSH" for r in after)
    # The aircraft turns back and joins TONIE -> OSH, and stays on it.
    joined = next(i for i, r in enumerate(after) if abs(r["xtk"]) < 0.05)
    assert max(abs(r["xtk"]) for r in after[joined:]) < 0.05
    assert after[-1]["tf"] == TF_TO
