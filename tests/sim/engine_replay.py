"""Replay a generated stream through the navigation engine, headless.

The one place in the synthetic tier that touches the engine under test --
which is why it lives in tests/ and not in ``fixgw.sim``. It does what the
``flightplan`` plugin does on the bus, with simulated time instead of the
wall clock: load the route on an ``FPLSEQ`` write, run ``FPLCMD``, and one
guidance update per row after the row's writes, writing the outputs under
their bus key names. The bench replay (real gateway, data_playback) is the
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


def replay(rows, t0=1000.0):
    """Rows of the generated run, each extended with the engine's outputs
    and its FPLMSG/FPLCMDACK as they stood after that row."""
    e = Engine(dict(ENGINE_CONFIG))
    db = {}
    out_rows = []
    msg, ack = "", 0
    for t, row in rows:
        db.update(row)
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
        out = e.update(db["LAT"], db["LONG"], db["GS"], db["MAGVAR"], True, True,
                       db["GPS_ACCURACY_HORIZ"] / 6076.12, t0 + t, db["ALT"])
        msg = out.get("msg") or msg
        ext = dict(row)
        for k, key in OUT_KEYS.items():
            ext[key] = out[k]
        ext["FPLMSG"] = msg
        ext["FPLCMDACK"] = ack
        out_rows.append((t, ext))
    return out_rows
