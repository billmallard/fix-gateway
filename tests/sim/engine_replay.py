"""Replay a generated stream through the navigation engine, headless.

The one place in the synthetic tier that touches the engine under test --
which is why it lives in tests/ and not in ``fixgw.sim``. It does what the
``flightplan`` plugin does on the bus, with simulated time instead of the
wall clock: load the route on an ``FPLSEQ`` write, run ``FPLCMD``, and one
guidance update per row after the row's writes, writing the outputs under
their bus key names. The guidance outputs the plugin flags carry the flags
the same way, as ``[value, annunciate, old, bad, fail, secfail]``.

The inputs' validity is read the way the plugin reads it: the fix type gate
is ``GPS_FIX_TYPE >= 3``, and ``LAT``/``LONG`` count as good until their
database ``tol`` (20 s) passes with no write -- the "old" flag. The bench replay (real gateway, data_playback) is the
end-to-end version of the same thing.
"""

from fixgw.plugins.flightplan.engine import Engine, RouteRejected, Segment, Waypoint

ENGINE_CONFIG = {"terminal_nm": 30.0, "approach_ramp_nm": 2.0, "turn_rate_deg_s": 3.0,
                 "alert_s": 10.0, "hal_nm": {"enr": 2.0, "term": 1.0, "lnav": 0.3}}

OUT_KEYS = {"state": "FPLSTATE", "act_leg": "FPLACTLEG", "phase": "FPLPHASE", "apr": "FPLAPR",
            "cdiscale": "CDISCALE", "crs": "FPLCRS", "xtk": "FPLXTK", "cdi": "FPLCDI", "tf": "FPLTF",
            "wp_from": "WPFROM", "wp_next": "WPNEXT", "wp_name": "WPNAME", "wp_dis": "WPDIS",
            "act_seg": "FPLACTSEG"}


def _route(db):
    wps = []
    for i in range(1, int(db["FPLCOUNT"]) + 1):
        g = lambda f: db[f"FPL{i}{f}"]  # noqa: E731
        wps.append(Waypoint(id=g("ID"), lat=g("LAT"), lon=g("LON"), type=int(g("TYPE")), pt=g("PT"),
                            crs=g("CRS"), dst=g("DST"), alt=g("ALT"), spd=int(g("SPD")), sgi=int(g("SGI")),
                            flags=int(g("FLAGS")), ctrlat=g("CTRLAT"), ctrlon=g("CTRLON"), turn=g("TURN")))
    segs = []
    for k in range(1, int(db["FPLSEGCNT"]) + 1):
        g = lambda f: db[f"FPLS{k}{f}"]  # noqa: E731
        segs.append(Segment(kind=int(g("KIND")), id=g("ID"), ent=g("ENT"), ext=g("EXT"),
                            first=int(g("FIRST")), last=int(g("LAST"))))
    return wps, segs


# the plugin's _write_outputs: these carry fail/bad, the rest never do
FLAGGED_KEYS = ("FPLCRS", "FPLXTK", "FPLCDI", "FPLTF", "WPDIS")
POSITION_TOL_S = 20.0    # LAT/LONG tol in config/database/ahrs.yaml


def replay(rows, t0=1000.0):
    """Rows of the generated run, each extended with the engine's outputs
    and its FPLMSG/FPLCMDACK as they stood after that row."""
    e = Engine(dict(ENGINE_CONFIG))
    db = {}
    out_rows = []
    msg, ack = "", 0
    last_pos_t = None
    for t, row in rows:
        db.update(row)
        if "LAT" in row:
            last_pos_t = t
        if "FPLSEQ" in row:
            wps, segs = _route(db)
            try:
                e.load_route(wps, db["FPLNAME"], int(db["FPLSEQ"]), segs,
                             aprtype=db["FPLAPRTYPE"], dbcyc=db["FPLDBCYC"])
            except RouteRejected as r:
                msg = r.reason
        if "FPLCMD" in row:
            ack, m = e.handle_command(row["FPLCMD"], (db["LAT"], db["LONG"], True), None)
            msg = m or msg
        position_ok = last_pos_t is not None and t - last_pos_t <= POSITION_TOL_S
        fix_type_ok = int(db.get("GPS_FIX_TYPE", 3)) >= 3
        out = e.update(db["LAT"], db["LONG"], db["GS"], db["MAGVAR"], position_ok, fix_type_ok,
                       db["GPS_ACCURACY_HORIZ"] / 6076.12, t0 + t, db["ALT"])
        msg = out.get("msg") or msg
        ext = dict(row)
        fail, bad = bool(out.get("fail", False)), bool(out.get("bad", False))
        for k, key in OUT_KEYS.items():
            ext[key] = [out[k], 0, 0, int(bad), int(fail), 0] if key in FLAGGED_KEYS and (fail or bad) else out[k]
        ext["FPLMSG"] = msg
        ext["FPLCMDACK"] = ack
        out_rows.append((t, ext))
    return out_rows
