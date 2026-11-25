"""The gdl90_out plugin on the real database (AER-6142, BACKLOG EFB1).

Validity maps through (a key that is old, bad, failed or never written goes
out as the ICD's invalid value), discovery is strict, nothing is sent without
a client, and the plugin writes no FIX key.  The end-to-end tests run the real
plugin thread on loopback sockets and decode what it sends with the stratux
plugin's decoder.
"""

import json
import logging
import socket
import struct
import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

import fixgw
import fixgw.plugins.gdl90_out as out
from fixgw.plugins.gdl90_out import messages
from fixgw.plugins.stratux import gdl90

LOG = logging.getLogger("test.gdl90_out")
ANNOUNCE = json.dumps({"App": "ForeFlight", "GDL90": {"port": 4000}}).encode()

GOOD = {
    "LAT": 44.5,
    "LONG": -93.25,
    "PALT": 4500.0,
    "GS": 120.0,
    "VS": -500.0,
    "TRACK": 87.0,
    "GPS_FIX_TYPE": 3,
    "GPS_ACCURACY_HORIZ": 20.0,  # ft = 6.1 m -> NACp 10
    "GPS_ELLIPSOID_ALT": 4380.0,
    "ROLL": 10.0,
    "PITCH": 2.5,
    "HEAD": 91.0,
    "IAS": 110.0,
    "TAS": 118.0,
}


def write(database, values):
    for k, v in values.items():
        database.get_raw_item(k).set(v, old=False, bad=False, fail=False)


def encoder(database, **config):
    return out.Encoder(
        out.FixReader(database.get_raw_item, LOG),
        config,
        {},
        LOG,
        keys=database.listkeys(),
    )


def decode(msgs):
    """{id: [clear message, ...]} for a list of clear messages, through the
    framer and the stratux decoder."""
    found = {}
    for msg in gdl90.decode_datagram(b"".join(gdl90.encode(x) for x in msgs)):
        found.setdefault(msg[0], []).append(msg)
    return found


# --- validity -------------------------------------------------------------------


def test_good_keys_go_out(database):
    write(database, GOOD)
    msgs = decode(encoder(database).second(3600))
    hb = gdl90.parse_heartbeat(msgs[0x00][0])
    r = gdl90.parse_report(msgs[0x0A][0])
    g = gdl90.parse_geo_altitude(msgs[0x0B][0])
    assert hb.gps_valid and hb.timestamp == 3600
    assert r.lat == pytest.approx(44.5, abs=3e-5)
    assert r.lon == pytest.approx(-93.25, abs=3e-5)
    assert (r.alt, r.gs, r.vs) == (4500, 120, -512)  # -500 ft/min -> -8 x 64
    assert r.trk == pytest.approx(87.0, abs=360 / 512)
    assert (r.nic, r.nacp) == (0, 10)
    assert r.airborne and not r.pos_bad
    assert g.alt == 4380
    assert msgs[0x65][0][1] == messages.FF_ID


@pytest.mark.parametrize("key", ["LAT", "LONG"])
def test_flagged_position_clears_gps_valid(database, key):
    for flag in ("old", "bad", "fail"):
        write(database, GOOD)
        database.get_raw_item(key).set(GOOD[key], **{flag: True})
        msgs = decode(encoder(database).second(0))
        assert not gdl90.parse_heartbeat(msgs[0x00][0]).gps_valid, flag
        r = gdl90.parse_report(msgs[0x0A][0])
        assert r.pos_bad and r.nic == 0 and r.nacp == 0, flag


@pytest.mark.parametrize("key", ["LAT", "LONG"])
def test_expired_position_clears_gps_valid(database, key):
    write(database, GOOD)
    item = database.get_raw_item(key)
    item.timestamp -= (item.tol + 1) / 1000.0  # aged past its tol, no flag set
    msgs = decode(encoder(database).second(0))
    assert not gdl90.parse_heartbeat(msgs[0x00][0]).gps_valid
    assert gdl90.parse_report(msgs[0x0A][0]).pos_bad


def test_never_written_position_is_invalid(database):
    # The database's initial 0.0 is not a position (LAT/LONG are untouched).
    write(database, {k: v for k, v in GOOD.items() if k not in ("LAT", "LONG")})
    msgs = decode(encoder(database).second(0))
    assert not gdl90.parse_heartbeat(msgs[0x00][0]).gps_valid
    assert gdl90.parse_report(msgs[0x0A][0]).pos_bad


@pytest.mark.parametrize("fix, valid", [(0, False), (1, False), (2, True), (3, True)])
def test_written_fix_type_gates_the_position(database, fix, valid):
    write(database, dict(GOOD, GPS_FIX_TYPE=fix))
    msgs = decode(encoder(database).second(0))
    assert gdl90.parse_heartbeat(msgs[0x00][0]).gps_valid is valid


def test_unwritten_fix_type_is_not_consulted(database):
    # X-Plane writes LAT/LONG but no GPS_FIX_TYPE.
    write(database, {k: v for k, v in GOOD.items() if k != "GPS_FIX_TYPE"})
    msgs = decode(encoder(database).second(0))
    assert gdl90.parse_heartbeat(msgs[0x00][0]).gps_valid


def test_bad_fix_type_invalidates(database):
    write(database, GOOD)
    database.get_raw_item("GPS_FIX_TYPE").set(3, bad=True)
    msgs = decode(encoder(database).second(0))
    assert not gdl90.parse_heartbeat(msgs[0x00][0]).gps_valid


@pytest.mark.parametrize(
    "key, check",
    [
        ("PALT", lambda r: r.alt_bad),
        ("GS", lambda r: r.gs_bad),
        ("VS", lambda r: r.vs_bad),
        ("TRACK", lambda r: r.trk_bad),
        ("GPS_ACCURACY_HORIZ", lambda r: r.nacp == 0 and not r.pos_bad),
    ],
)
def test_each_bad_field_goes_out_invalid(database, key, check):
    write(database, GOOD)
    database.get_raw_item(key).set(GOOD[key], bad=True)
    r = gdl90.parse_report(decode(encoder(database).second(0))[0x0A][0])
    assert check(r)


# --- assumed_accuracy_ft (AER-6452, BACKLOG EFB5) -----------------------------
# X-Plane and an FMS write a position but no GPS_ACCURACY_HORIZ.  ForeFlight
# drops an ownship report with NACp 0 (EFB2), so a sim or FMS panel may opt in
# to an assumed accuracy.  Nothing is written to the bus.

NO_ACCURACY = {k: v for k, v in GOOD.items() if k != "GPS_ACCURACY_HORIZ"}


def ownship_nacp(database, **config):
    return gdl90.parse_report(decode(encoder(database, **config).second(0))[0x0A][0]).nacp


@pytest.mark.parametrize("unset", [{}, {"assumed_accuracy_ft": None}, {"assumed_accuracy_ft": ""}])
def test_unset_assumed_accuracy_sends_nacp_0(database, unset):
    write(database, NO_ACCURACY)
    assert ownship_nacp(database, **unset) == 0


@pytest.mark.parametrize(
    "ft, nacp",
    [
        (5.0, 11),  # 1.5 m < 3 m
        (16.0, 10),  # 4.9 m < 10 m, the EFB2 bench stand-in
        (32.0, 10),  # 9.75 m < 10 m
        (33.0, 9),  # 10.06 m: the bin is strict
        (300.0, 8),  # 91.4 m < 92.6 m
        ("50", 9),  # a quoted number is a number
        (70000.0, 0),  # 21.3 km: worse than 10 NM is "unknown"
    ],
)
def test_assumed_accuracy_bins_when_no_source_writes_the_key(database, ft, nacp):
    write(database, NO_ACCURACY)
    assert ownship_nacp(database, assumed_accuracy_ft=ft) == nacp


def test_a_valid_accuracy_key_always_wins(database):
    write(database, dict(GOOD, GPS_ACCURACY_HORIZ=400.0))  # 122 m -> NACp 7
    assert ownship_nacp(database, assumed_accuracy_ft=5.0) == 7
    assert ownship_nacp(database) == 7


@pytest.mark.parametrize("flag", ["old", "bad", "fail"])
def test_a_written_accuracy_key_that_lost_validity_is_not_replaced(database, flag):
    # A real receiver that has dropped its estimate is NACp 0 with or without
    # the setting: the setting is for a source that has no accuracy at all.
    write(database, GOOD)
    database.get_raw_item("GPS_ACCURACY_HORIZ").set(20.0, **{flag: True})
    assert ownship_nacp(database, assumed_accuracy_ft=16.0) == 0


def test_an_expired_accuracy_key_is_not_replaced(database):
    write(database, GOOD)
    item = database.get_raw_item("GPS_ACCURACY_HORIZ")
    item.timestamp -= (item.tol + 1) / 1000.0
    assert ownship_nacp(database, assumed_accuracy_ft=16.0) == 0


def test_assumed_accuracy_does_not_make_a_position(database):
    # No valid position: lat, lon, NIC and NACp all 0 (G§3.5.1.3).
    write(database, {k: v for k, v in NO_ACCURACY.items() if k not in ("LAT", "LONG")})
    msgs = decode(encoder(database, assumed_accuracy_ft=16.0).second(0))
    assert not gdl90.parse_heartbeat(msgs[0x00][0]).gps_valid
    r = gdl90.parse_report(msgs[0x0A][0])
    assert r.pos_bad and r.nacp == 0


@pytest.mark.parametrize("value", [0, -16.0, "sixteen", float("nan"), float("inf"), True, [16]])
def test_a_bad_assumed_accuracy_is_refused_and_left_unset(database, value, caplog):
    write(database, NO_ACCURACY)
    with caplog.at_level(logging.ERROR, logger=LOG.name):
        assert ownship_nacp(database, assumed_accuracy_ft=value) == 0
    assert "assumed_accuracy_ft" in caplog.text


def test_a_set_assumed_accuracy_is_logged_as_a_warning(database, caplog):
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        encoder(database, assumed_accuracy_ft=16.0)
    assert "does not report" in caplog.text


def test_assumed_accuracy_writes_nothing_to_the_bus(database):
    write(database, NO_ACCURACY)
    item = database.get_raw_item("GPS_ACCURACY_HORIZ")
    encoder(database, assumed_accuracy_ft=16.0).second(0)
    assert not item.written


def test_shipped_config_leaves_assumed_accuracy_unset():
    import pathlib

    import yaml

    path = pathlib.Path(fixgw.__file__).parent / "config" / "connections" / "gdl90_out.yaml"
    cfg = yaml.safe_load(path.read_text())["gdl90_out"]
    assert "assumed_accuracy_ft" in cfg  # documented where an installer looks
    assert out.parse_assumed_accuracy(cfg["assumed_accuracy_ft"], LOG) is None


def test_no_geo_altitude_message_without_a_valid_key(database):
    write(database, GOOD)
    database.get_raw_item("GPS_ELLIPSOID_ALT").set(4380.0, old=True)
    assert 0x0B not in decode(encoder(database).second(0))


def test_geo_altitude_key_and_datum_are_settings(database):
    write(database, dict(GOOD, ALT=4400.0))
    msgs = decode(encoder(database, geo_altitude_key="ALT", geo_altitude_datum="msl").second(0))
    assert gdl90.parse_geo_altitude(msgs[0x0B][0]).alt == 4400
    assert msgs[0x65][0][35:39] == bytes([0, 0, 0, 1])  # MSL datum bit


def gnx_panel(database):
    """A GNX 375, gpsd or navigator panel: TALT written, GPS_ELLIPSOID_ALT
    never written (GALT1/TALTSRC1, AER-6458)."""
    good = {k: v for k, v in GOOD.items() if k != "GPS_ELLIPSOID_ALT"}
    write(database, dict(good, TALT=4410.0))
    assert not database.get_raw_item("GPS_ELLIPSOID_ALT").written


def test_gnx_panel_on_the_shipped_config_still_sends_geo_altitude_as_msl(database):
    # The shipped (and every copied) config names GPS_ELLIPSOID_ALT, which
    # only mavlink writes now.  The panel still sends 0x0B, from TALT, and
    # the ID message says MSL instead of calling MSL an ellipsoid height.
    gnx_panel(database)
    msgs = decode(encoder(database, geo_altitude_key="GPS_ELLIPSOID_ALT",
                          geo_altitude_datum="ellipsoid").second(0))
    assert gdl90.parse_geo_altitude(msgs[0x0B][0]).alt == 4410
    assert msgs[0x65][0][35:39] == bytes([0, 0, 0, 1])  # MSL datum bit


def test_a_written_ellipsoid_key_is_kept_over_talt(database):
    # A mavlink panel writes both: the configured ellipsoid key wins.
    write(database, dict(GOOD, TALT=4410.0))
    msgs = decode(encoder(database).second(0))
    assert gdl90.parse_geo_altitude(msgs[0x0B][0]).alt == 4380
    assert msgs[0x65][0][35:39] == bytes([0, 0, 0, 0])  # ellipsoid


def test_a_stale_ellipsoid_key_does_not_fall_back_to_talt(database):
    # Written once, then old: no 0x0B, rather than a datum that flips.
    write(database, dict(GOOD, TALT=4410.0))
    database.get_raw_item("GPS_ELLIPSOID_ALT").set(4380.0, old=True)
    msgs = decode(encoder(database).second(0))
    assert 0x0B not in msgs
    assert msgs[0x65][0][35:39] == bytes([0, 0, 0, 0])


def test_no_geo_altitude_when_neither_key_was_written(database):
    good = {k: v for k, v in GOOD.items() if k != "GPS_ELLIPSOID_ALT"}
    write(database, good)
    assert 0x0B not in decode(encoder(database).second(0))


@pytest.mark.parametrize("key, field", [("ROLL", 0), ("PITCH", 1), ("HEAD", 2), ("IAS", 3), ("TAS", 4)])
def test_flagged_attitude_goes_out_invalid(database, key, field):
    invalid = (0x7FFF, 0x7FFF, 0xFFFF, 0xFFFF, 0xFFFF)
    for flag in ("old", "bad", "fail"):
        write(database, GOOD)
        database.get_raw_item(key).set(GOOD[key], **{flag: True})
        vals = struct.unpack(">HHHHH", encoder(database).ahrs()[2:])
        assert vals[field] == invalid[field], flag
        for i, v in enumerate(vals):
            if i != field:
                assert v != invalid[i], (flag, i)


def test_good_attitude(database):
    write(database, GOOD)
    roll, pitch, hdg, ias, tas = struct.unpack(">hhHHH", encoder(database).ahrs()[2:])
    assert (roll, pitch, hdg, ias, tas) == (100, 25, 0x8000 | 910, 110, 118)


def test_ownship_identity(database):
    write(database, GOOD)
    e = encoder(database, ownship_address="A1B2C3", callsign="n123ab", emitter_category=2)
    r = gdl90.parse_report(decode(e.second(0))[0x0A][0])
    assert (r.address, r.atype, r.callsign, r.cat) == (0xA1B2C3, 0, "N123AB", 2)


def test_no_ownship_address_is_zero(database):
    # FF: 0x000000 marks an Ownship Report as GPS telemetry only.
    write(database, GOOD)
    r = gdl90.parse_report(decode(encoder(database).second(0))[0x0A][0])
    assert r.address == 0


def test_long_name_carries_the_version(database):
    msg = decode(encoder(database).second(0))[0x65][0]
    assert msg[19:35].decode().strip() == "MkrPlane " + fixgw.__version__


# --- traffic ----------------------------------------------------------------------


def fill_slot(database, n, **fields):
    p = "TFC%d" % n
    base = dict(ADDR=0xABCDEF, ATYP=0, LAT=45.0, LON=-93.0, ALT=5500, GS=140, VS=640,
                TRK=270.0, TTYP=1, EXTR=False, AIR=True, NIC=8, NACP=9, CAT=1,
                CS="N12345", ALRT=0, EMRG=0)
    bad = fields.pop("bad", ())
    base.update(fields)
    for k, v in base.items():
        database.get_raw_item(p + k).set(v, old=False, bad=k in bad, fail=False)
    database.get_raw_item(p + "TGT").set((base["ATYP"] << 24) | base["ADDR"], old=False, bad=False, fail=False)


def test_traffic_is_off_by_default(database):
    fill_slot(database, 1)
    assert 0x14 not in decode(encoder(database).second(0))


def test_traffic_round_trips(database):
    fill_slot(database, 3)
    fill_slot(database, 7, ADDR=0x123456, CS="UAL1", ALRT=1, bad=("ALT", "VS"))
    msgs = decode(encoder(database, traffic=True).second(0))
    reports = {r.address: r for r in map(gdl90.parse_report, msgs[0x14])}
    assert set(reports) == {0xABCDEF, 0x123456}
    a = reports[0xABCDEF]
    assert a.lat == pytest.approx(45.0) and a.lon == pytest.approx(-93.0)
    assert (a.alt, a.gs, a.vs, a.trk, a.nic, a.nacp, a.callsign) == (5500, 140, 640, 270.0, 8, 9, "N12345")
    b = reports[0x123456]
    assert b.alt_bad and b.vs_bad and b.alert == 1 and b.callsign == "UAL1"


def test_empty_and_old_slots_are_not_sent(database):
    fill_slot(database, 1)
    fill_slot(database, 2, ADDR=0x222222)
    database.get_raw_item("TFC1TGT").set(-1)
    database.get_raw_item("TFC2TGT").set(0x222222, old=True)
    assert 0x14 not in decode(encoder(database, traffic=True).second(0))


# --- discovery --------------------------------------------------------------------


@pytest.mark.parametrize(
    "datagram, port",
    [
        (ANNOUNCE, 4000),
        (b'{"App":"ForeFlight","GDL90":{"port":4001},"extra":[1,2]}', 4001),
        (b"", None),
        (b"not json", None),
        (b"\xff\xfe{}", None),
        (b"[]", None),
        (b'{"App":"foreflight","GDL90":{"port":4000}}', None),
        (b'{"App":"ForeFlight"}', None),
        (b'{"App":"ForeFlight","GDL90":4000}', None),
        (b'{"App":"ForeFlight","GDL90":{"port":"4000"}}', None),
        (b'{"App":"ForeFlight","GDL90":{"port":true}}', None),
        (b'{"App":"ForeFlight","GDL90":{"port":4000.0}}', None),
        (b'{"App":"ForeFlight","GDL90":{"port":0}}', None),
        (b'{"App":"ForeFlight","GDL90":{"port":65536}}', None),
        (b'{"App":"ForeFlight","GDL90":{"port":4000},"pad":"' + b"x" * 512 + b'"}', None),
        (b"[" * 400 + b"]" * 400, None),
    ],
)
def test_parse_discovery_is_strict(datagram, port):
    assert out.parse_discovery(datagram) == port


def test_discovery_adds_a_client_and_a_malformed_one_is_ignored():
    c = out.Clients(LOG)
    assert c.targets(0.0) == []
    assert not c.announce(b'{"App":"ForeFlight"}', "192.168.1.20", 0.0)
    assert c.targets(0.0) == [] and c.malformed == 1
    assert c.announce(ANNOUNCE, "192.168.1.20", 0.0)
    assert c.targets(0.0) == [("192.168.1.20", 4000)]


def test_discovered_client_expires_and_is_refreshed():
    c = out.Clients(LOG, timeout_s=60)
    c.announce(ANNOUNCE, "10.0.0.5", 0.0)
    c.announce(ANNOUNCE, "10.0.0.5", 50.0)
    assert c.targets(109.0) == [("10.0.0.5", 4000)]
    assert c.targets(110.0) == []


@pytest.mark.parametrize("sender", ["255.255.255.255", "0.0.0.0", "224.0.0.1", "nonsense"])
def test_discovery_from_a_non_unicast_sender_is_refused(sender):
    c = out.Clients(LOG)
    assert not c.announce(ANNOUNCE, sender, 0.0)
    assert c.targets(0.0) == []


def test_discovery_allow_filters_senders():
    c = out.Clients(LOG, allow=out.parse_allow(["192.168.1.0/24"], LOG))
    assert not c.announce(ANNOUNCE, "10.1.1.1", 0.0)
    assert c.announce(ANNOUNCE, "192.168.1.77", 0.0)
    assert c.targets(0.0) == [("192.168.1.77", 4000)]


def test_unparseable_discovery_allow_accepts_nobody():
    c = out.Clients(LOG, allow=out.parse_allow(["192.168.1.0/24", "bogus"], LOG))
    assert not c.announce(ANNOUNCE, "192.168.1.77", 0.0)


def test_client_table_is_bounded():
    c = out.Clients(LOG, limit=2)
    assert c.announce(ANNOUNCE, "10.0.0.1", 0.0)
    assert c.announce(ANNOUNCE, "10.0.0.2", 0.0)
    assert not c.announce(ANNOUNCE, "10.0.0.3", 0.0)
    assert c.announce(ANNOUNCE, "10.0.0.1", 1.0)  # a refresh is not a new one
    assert len(c.targets(1.0)) == 2


@pytest.mark.parametrize(
    "entry, parsed",
    [("192.168.1.5", ("192.168.1.5", 4000)), ("192.168.1.5:4001", ("192.168.1.5", 4001)),
     ("255.255.255.255", None), ("224.1.1.1", None), ("0.0.0.0", None),
     ("192.168.1.5:0", None), ("192.168.1.5:x", None), ("host.local", None)],
)
def test_parse_client(entry, parsed):
    assert out.parse_client(entry) == parsed


# --- the plugin thread ------------------------------------------------------------


def make_plugin(database, **config):
    config = dict({"discovery_host": "127.0.0.1", "discovery_port": 0}, **config)
    return out.Plugin("gdl90_out", config, {})


def test_no_output_and_no_bus_read_without_clients(database):
    write(database, GOOD)
    p = make_plugin(database)
    t = p.thread
    t.tx.close()
    t.tx = MagicMock()
    t.encoder = MagicMock(wraps=t.encoder)
    for i in range(10):
        t.tick_second(float(i))
        t.send(t.encoder.ahrs, float(i))
    t.tx.sendto.assert_not_called()
    t.encoder.second.assert_not_called()
    t.encoder.ahrs.assert_not_called()
    t.rx.close()


def test_writes_no_fix_key(database):
    before = database.writers()
    write(database, GOOD)
    p = make_plugin(database, clients=["127.0.0.1:9"])
    for i in range(3):
        p.thread.tick_second(float(i))
        p.thread.send(p.thread.encoder.ahrs, float(i))
    after = database.writers()
    assert not any("gdl90_out" in w for ws in after.values() for w in ws)
    assert set(after) - set(before) <= set(GOOD)
    p.thread.tx.close()
    p.thread.rx.close()


@contextmanager
def feeding(database, values, period=0.2):
    """Keep writing values, as a live source does, so nothing ages out."""
    stop = threading.Event()

    def run():
        while not stop.is_set():
            write(database, values)
            stop.wait(period)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(1.0)


def recv_all(sock, seconds):
    end = time.monotonic() + seconds
    got = []
    while time.monotonic() < end:
        sock.settimeout(max(0.01, end - time.monotonic()))
        try:
            got.append(sock.recvfrom(4096)[0])
        except socket.timeout:
            break
    return got


def test_end_to_end_discovery_then_stream(database):
    """ForeFlight's announcement on the discovery socket makes the real thread
    stream to the announcer's port; the stream decodes with the stratux
    decoder, at the specified rates."""
    write(database, GOOD)
    tablet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tablet.bind(("127.0.0.1", 0))
    port = tablet.getsockname()[1]
    p = make_plugin(database)
    disc = p.thread.rx.getsockname()
    p.run()
    try:
        with feeding(database, GOOD):
            assert recv_all(tablet, 1.2) == []  # nobody announced: silence
            announce = json.dumps({"App": "ForeFlight", "GDL90": {"port": port}}).encode()
            tablet.sendto(announce, disc)
            datagrams = recv_all(tablet, 2.1)
    finally:
        p.stop()
        tablet.close()
    msgs = [m for d in datagrams for m in gdl90.decode_datagram(d)]
    ids = [m[0] for m in msgs]
    assert ids.count(0x00) >= 2 and ids.count(0x0A) >= 2 and ids.count(0x0B) >= 2
    ahrs = [m for m in msgs if m[:2] == b"\x65\x01"]
    assert 9 <= len(ahrs) <= 12  # 5 Hz over about 2 s
    assert all(len(d) <= out.DATAGRAM_MAX for d in datagrams)
    assert gdl90.parse_heartbeat(msgs[ids.index(0x00)]).gps_valid
    r = gdl90.parse_report(msgs[ids.index(0x0A)])
    assert r.lat == pytest.approx(44.5, abs=3e-5)
    assert p.status["Discovery accepted"] == 1


def test_fixed_client_streams_without_discovery(database):
    write(database, GOOD)
    tablet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tablet.bind(("127.0.0.1", 0))
    p = make_plugin(database, discovery=False, clients=["127.0.0.1:%d" % tablet.getsockname()[1]])
    assert p.thread.rx is None
    p.run()
    try:
        datagrams = recv_all(tablet, 1.1)
    finally:
        p.stop()
        tablet.close()
    ids = [m[0] for d in datagrams for m in gdl90.decode_datagram(d)]
    assert 0x00 in ids and 0x0A in ids


def test_pack_splits_under_the_limit():
    frames = [bytes(100)] * 30
    dgrams = out.pack(frames, limit=1400)
    assert [len(d) for d in dgrams] == [1400, 1400, 200]
    assert b"".join(dgrams) == b"".join(frames)
