"""Demo traffic through the real GDL90 path (DEMO7, AER-5462).

The emitter is anchored to the GDL 90 Data Interface Specification
(560-1058-00 Rev A, cited G§) worked example, not to the decoder it feeds, so
a bug the two shared would still fail here.  The rest runs on the real
database loaded from src/fixgw/config/database.yaml, traffic block included.
"""

import pytest

from fixgw import geo
from fixgw.plugins.demo import traffic
from fixgw.plugins.stratux import gdl90

# G§3.5.2 worked example, clear (ID through emergency code).
EXAMPLE = bytes.fromhex(
    "14 00 AB 45 49 1F EF 15 A8 89 78 0F 09 A9 07 B0 01 20 01 4E 38 32 35"
    " 56 20 20 20 00".replace(" ", "")
)

LAT, LON, ALT = 34.8989, -120.4575, 4000.0


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Log:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warning(self, msg):
        self.warnings.append(msg)

    def debug(self, msg):
        pass


def ident(target):
    return (traffic.ADDR_SELF_ASSIGNED << 24) | target.address


def occupied(database):
    out = {}
    n = 1
    while True:
        try:
            tgt = database.read("TFC{}TGT".format(n))[0]
        except KeyError:
            return out
        if tgt != -1:
            out[tgt] = n
        n += 1


def ownship(database, lat=LAT, lon=LON, alt=ALT):
    database.write("LAT", lat)
    database.write("LONG", lon)
    database.write("ALT", alt)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def scene(database, clock):
    return traffic.DemoTraffic(database.get_raw_item, Log(), clock=clock)


# --- the emitter against the spec ---------------------------------------------


def test_encode_report_reproduces_the_spec_worked_example():
    msg = traffic.encode_report(
        0xAB4549,
        44.90708,
        -122.99488,
        5000,
        123,
        64,
        45.0,
        "N825V",
        atype=0,
        misc=0x9,
        nic=10,
        nacp=9,
        cat=1,
    )
    assert msg == EXAMPLE


def test_encode_report_clamps_to_codes_that_are_not_no_data():
    r = gdl90.parse_report(
        traffic.encode_report(1, 0.0, 0.0, 120000, 9999, 99999, 359.9, "DEMO1")
    )
    assert not r.alt_bad and not r.gs_bad and not r.vs_bad
    assert (r.alt, r.gs, r.vs, r.trk) == (0xFFE * 25 - 1000, 0xFFE, 510 * 64, 0.0)
    r = gdl90.parse_report(
        traffic.encode_report(1, 0.0, 0.0, -5000, -5, -99999, 0.0, "DEMO1")
    )
    assert (r.alt, r.gs, r.vs) == (-1000, 0, -510 * 64)


def test_heartbeat_is_seven_bytes_with_the_utc_timestamp():
    ts = 70000  # past 65535 s, so bit 16 is used
    hb = traffic.encode_heartbeat(ts)
    assert len(hb) == 7 and hb[0] == gdl90.HEARTBEAT
    assert hb[1] & 0x80  # GPS position valid
    assert hb[2] & 0x01  # UTC OK
    assert ((hb[2] >> 7) << 16) | (hb[4] << 8) | hb[3] == ts
    assert gdl90.decode_datagram(gdl90.encode(hb)) == [hb]


def test_targets_orbit_at_their_radius_on_a_tangent_track():
    for target in traffic.TARGETS:
        for t in (0, 37, 600, 3600):
            lat, lon, alt, gs, vs, trk = target.state(t, LAT, LON, ALT)
            assert geo.distance_nm(LAT, LON, lat, lon) == pytest.approx(
                target.radius_nm, rel=1e-6
            )
            brg = geo.bearing_deg(LAT, LON, lat, lon)
            turn = 90.0 if target.clockwise else -90.0
            assert (trk - brg - turn + 180) % 360 - 180 == pytest.approx(0.0, abs=0.01)
            assert abs(alt - ALT - target.alt_offset_ft) <= target.alt_swing_ft + 1e-9


def test_one_target_climbs_and_descends_past_the_trend_arrow():
    # pyEfis draws a trend arrow at 500 fpm (map/layers/traffic.py TREND_FPM).
    peak = max(abs(t.state(0, LAT, LON, ALT)[4]) for t in traffic.TARGETS)
    assert peak > 500


# --- through the decoder into the TFC block -----------------------------------


def test_targets_reach_the_tfc_block_through_the_decoder(database, scene, clock):
    ownship(database)
    scene.step()
    slots = occupied(database)
    assert set(slots) == {ident(t) for t in traffic.TARGETS}
    for target in traffic.TARGETS:
        n = slots[ident(target)]
        lat = database.read("TFC{}LAT".format(n))[0]
        lon = database.read("TFC{}LON".format(n))[0]
        # Within one GDL90 position LSB of the orbit.
        assert geo.distance_nm(LAT, LON, lat, lon) == pytest.approx(
            target.radius_nm, abs=0.01
        )
        assert database.read("TFC{}CS".format(n))[0] == target.callsign
        assert database.read("TFC{}ATYP".format(n))[0] == traffic.ADDR_SELF_ASSIGNED
        assert database.read("TFC{}ALT".format(n))[0] == pytest.approx(
            ALT + target.alt_offset_ft, abs=12.5
        )
        assert database.read("TFC{}TTYP".format(n))[0] == 1  # track: an arrow
        for field in ("LAT", "LON", "ALT", "GS", "VS", "TRK"):
            assert not database.read("TFC{}{}".format(n, field))[3], field
        assert database.read("TFC{}ALRT".format(n))[0] == 0
    clock.t += 1.0
    scene.step()
    count = database.read("TFCCOUNT")
    assert count[0] == len(traffic.TARGETS) and not count[4]


def test_the_registry_names_demo_as_the_tfc_writer(database, scene):
    ownship(database)
    scene.step()
    writers = database.writers()
    n = occupied(database)[ident(traffic.TARGETS[0])]
    for key in ("TFC{}TGT".format(n), "TFC{}LAT".format(n), "TFCCOUNT"):
        assert writers[key] == ["demo"], key


def test_heartbeats_keep_the_source_live_and_targets_move(database, scene, clock):
    ownship(database)
    scene.step()
    slots = occupied(database)
    n = slots[ident(traffic.TARGETS[0])]
    first = database.read("TFC{}LAT".format(n))[0], database.read("TFC{}LON".format(n))[0]
    for _ in range(30):  # the writer fails the source after 3 s with no Heartbeat
        clock.t += 0.5
        scene.step()
    assert occupied(database) == slots  # nothing evicted, nothing reslotted
    assert not database.read("TFCCOUNT")[4]
    assert not database.read("TFC{}TGT".format(n))[4]
    now = database.read("TFC{}LAT".format(n))[0], database.read("TFC{}LON".format(n))[0]
    # 15 s at 100 kt is 0.42 nm along the orbit.
    assert geo.distance_nm(*first, *now) == pytest.approx(100 * 15 / 3600.0, abs=0.05)


def test_sends_once_a_second_on_a_monotonic_deadline(database, scene, clock):
    ownship(database)
    sent = []
    real = scene.datagram
    scene.datagram = lambda t: sent.append(t) or real(t)
    for _ in range(25):
        scene.step()
        clock.t += 0.1
    assert sent == pytest.approx([0.0, 1.0, 2.0])


def test_center_on_puts_the_targets_around_the_hold(database, scene):
    ownship(database)  # ownship elsewhere: the hold wins
    hold = (34.9, -120.1, 6000.0)
    scene.center_on(*hold)
    scene.step()
    for target in traffic.TARGETS:
        n = occupied(database)[ident(target)]
        lat = database.read("TFC{}LAT".format(n))[0]
        lon = database.read("TFC{}LON".format(n))[0]
        assert geo.distance_nm(hold[0], hold[1], lat, lon) == pytest.approx(
            target.radius_nm, abs=0.01
        )


def test_waits_for_a_good_ownship_position(database, scene, clock):
    database.get_raw_item("LAT").set(LAT, fail=True)
    database.write("LONG", LON)
    database.write("ALT", ALT)
    scene.step()
    assert occupied(database) == {}
    clock.t += 1.0
    database.get_raw_item("LAT").set(LAT, fail=False)
    scene.step()
    assert len(occupied(database)) == len(traffic.TARGETS)


# --- in the demo loop ---------------------------------------------------------
# The rewritten demo (DEMO2) flies traffic in its loop; those tests are in
# test_demo.py (the hold centre, traffic off, no TFC block, the TFC refusal).
