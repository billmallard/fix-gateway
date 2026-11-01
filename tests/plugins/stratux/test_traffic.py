"""GDL90 traffic writer: tests T1-T10 of pyEfis docs/fix_dynamic_list_spec.md
section 7 (AER-4579, BACKLOG P3a).

Every vector is built from the GDL 90 Data Interface Specification
(560-1058-00 Rev A), cited G§.  Injection is a UDP datagram to the stratux
plugin's port on 127.0.0.1; the database is the real one loaded from
src/fixgw/config/database.yaml, traffic block included; the traffic writer
runs on a fake monotonic clock except in T4, which is about the database's
own wall-clock aging.
"""

import random
import socket
import struct
import threading
import time

import pytest

import fixgw.plugins.netfix
import fixgw.plugins.stratux as stratux
from fixgw import cfg, geo
from fixgw.plugins.stratux import gdl90
from fixgw.plugins.stratux.traffic import (
    FIELDS,
    TrafficWriter,
    parse_ownship_address,
)

# G§2.2.4: a valid Heartbeat, FCS 0x8BB3.
HEARTBEAT_FRAMED = bytes.fromhex("7E0081 41DBD00802B38B7E".replace(" ", ""))
HEARTBEAT = bytes.fromhex("008141DBD00802")

# G§3.5.2 worked example, framed (FCS 0xD657, sent 57 D6).
EXAMPLE_FRAMED = bytes.fromhex(
    "7E 14 00 AB 45 49 1F EF 15 A8 89 78 0F 09 A9 07 B0 01 20 01 4E 38 32 35"
    " 56 20 20 20 00 57 D6 7E".replace(" ", "")
)

OWN_LAT, OWN_LON = 45.0, -122.0


def latlon_raw(deg):
    return round(deg * (1 << 23) / 180.0) & 0xFFFFFF


def report(
    msg_id=gdl90.TRAFFIC,
    s=0,
    t=0,
    addr=0xAB4549,
    lat=44.90708,
    lon=-122.99488,
    lat_raw=None,
    lon_raw=None,
    ddd=0x0F0,
    misc=0x9,
    nic=10,
    nacp=9,
    hhh=123,
    vvv=0x001,
    tt=0x20,
    ee=1,
    cs="N825V",
    p=0,
):
    """A clear 0x14 (or 0x0A) message from raw G§3.5.1 field values."""
    la = latlon_raw(lat) if lat_raw is None else lat_raw
    lo = latlon_raw(lon) if lon_raw is None else lon_raw
    b = bytearray(28)
    b[0] = msg_id
    b[1] = (s << 4) | t
    b[2:5] = addr.to_bytes(3, "big")
    b[5:8] = la.to_bytes(3, "big")
    b[8:11] = lo.to_bytes(3, "big")
    b[11] = ddd >> 4
    b[12] = ((ddd & 0xF) << 4) | misc
    b[13] = (nic << 4) | nacp
    b[14] = hhh >> 4
    b[15] = ((hhh & 0xF) << 4) | (vvv >> 8)
    b[16] = vvv & 0xFF
    b[17] = tt
    b[18] = ee
    b[19:27] = cs.ljust(8).encode("ascii")
    b[27] = p << 4
    return bytes(b)


def ownship(**kw):
    return report(msg_id=gdl90.OWNSHIP, **kw)


def ahrs(seq):
    """A Stratux AHRS (0x4C) message whose PITCH is seq/10: the sync marker."""
    return struct.pack(">B3xhhhhhhhhh", 0x4C, 0, seq, 0, 0, 0, 10, 0, 6000, 0)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Recorder:
    """Every callback on the given keys, in order: (key, value tuple)."""

    def __init__(self, database, keys):
        self.events = []
        self.lock = threading.Lock()
        for k in keys:
            database.callback_add("rec", k, self.cb, None)

    def cb(self, key, value, udata):
        with self.lock:
            self.events.append((key, value))

    def take(self):
        with self.lock:
            ev, self.events = self.events, []
        return ev


def slot_keys(n):
    return ["TFC{}{}".format(n, f) for f in FIELDS] + ["TFC{}TGT".format(n)]


class Gateway:
    """The stratux plugin on a real UDP socket, its writer on a fake clock."""

    def __init__(self, database, clock=None, **config):
        self.db = database
        config = dict({"host": "127.0.0.1", "port": 0}, **config)
        self.plugin = stratux.Plugin("stratux", config, {})
        t = self.plugin.thread
        if clock is not None:
            # Same construction as the plugin's, on the fake clock.
            t.traffic = TrafficWriter(
                self.plugin.db_get_item,
                t.log,
                evict_s=config.get("evict_s", 10),
                ownship_address=parse_ownship_address(
                    config.get("ownship_address"), t.log
                ),
                clock=clock,
            )
        self.writer = t.traffic
        self.addr = t.s.getsockname()
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0
        self.plugin.run()

    def send_raw(self, datagram):
        self.tx.sendto(datagram, self.addr)

    def send(self, *msgs):
        """All messages framed back to back in one datagram, then sync."""
        self.send_raw(b"".join(gdl90.encode(m) for m in msgs))
        self.settle()

    def sync(self):
        self.seq = self.seq % 1000 + 1
        self.send_raw(gdl90.encode(ahrs(self.seq)))
        deadline = time.time() + 5
        while self.db.read("PITCH")[0] != pytest.approx(self.seq / 10.0):
            assert time.time() < deadline, "plugin did not process the sync marker"
            time.sleep(0.002)

    def settle(self):
        # The loop dispatches a datagram, then ticks.  The second marker is
        # dispatched only after the first one's tick has finished.
        self.sync()
        self.sync()

    def stop(self):
        self.tx.close()
        self.plugin.stop()

    def val(self, key):
        return self.db.read(key)[0]

    def flags(self, key):
        v = self.db.read(key)
        return {"old": v[2], "bad": v[3], "fail": v[4]}

    def occupied(self):
        """{identity: slot} for every occupied slot."""
        out = {}
        for n in range(1, 33):
            tgt = self.val("TFC{}TGT".format(n))
            if tgt != -1:
                assert tgt not in out, "identity in two slots"
                out[tgt] = n
        return out


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def gw(database, clock):
    g = Gateway(database, clock)
    yield g
    g.stop()


@pytest.fixture
def make_gw(database, clock):
    made = []

    def make(**config):
        g = Gateway(database, clock, **config)
        made.append(g)
        return g

    yield make
    for g in made:
        g.stop()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- T1 Framing and FCS ------------------------------------------------------


def test_t1_heartbeat_vector():
    assert gdl90.calc_crc(HEARTBEAT) == 0x8BB3
    assert gdl90.decode_datagram(HEARTBEAT_FRAMED) == [HEARTBEAT]
    assert gdl90.encode(HEARTBEAT) == HEARTBEAT_FRAMED


def test_t1_worked_traffic_example():
    msgs = gdl90.decode_datagram(EXAMPLE_FRAMED)
    assert len(msgs) == 1
    assert gdl90.calc_crc(msgs[0]) == 0xD657
    assert gdl90.encode(msgs[0]) == EXAMPLE_FRAMED
    r = gdl90.parse_report(msgs[0])
    assert r.alert == 0 and r.atype == 0
    assert r.address == 0xAB4549 == 0o52642511
    # Within one LSB (180/2^23 = 2.1e-5 deg) of the document's figures.
    assert r.lat == pytest.approx(44.90708, abs=2.2e-5)
    assert r.lon == pytest.approx(-122.99488, abs=2.2e-5)
    assert r.lat == pytest.approx(44.90707, abs=1e-5)
    assert r.lon == pytest.approx(-122.99486, abs=1e-5)
    assert (r.alt, r.alt_bad) == (5000, False)
    assert r.airborne and not r.extrapolated and r.ttype == 1
    assert (r.nic, r.nacp) == (10, 9)
    assert (r.gs, r.vs, r.trk) == (123, 64, 45.0)
    assert r.cat == 1 and r.callsign == "N825V" and r.emergency == 0


def test_t1_worked_example_through_the_writer(gw):
    gw.send_raw(HEARTBEAT_FRAMED + EXAMPLE_FRAMED)
    gw.settle()
    assert gw.occupied() == {0xAB4549: 1}
    assert gw.val("TFC1ALT") == 5000
    assert gw.val("TFC1CS") == "N825V"
    assert gw.val("TFC1LAT") == pytest.approx(44.90707, abs=1e-5)


def test_t1_stuffing_round_trips():
    framed = gdl90.encode(bytes([0x14, 0x7E, 0x01]))
    assert b"\x7d\x5e" in framed[1:-1] and b"\x7e" not in framed[1:-1]
    framed = gdl90.encode(bytes([0x14, 0x7D, 0x01]))
    assert b"\x7d\x5d" in framed[1:-1]
    for payload in ([0x14, 0x7E, 0x01], [0x14, 0x7D, 0x01], [0x7E], [0x7D, 0x7E]):
        m = bytes(payload)
        assert gdl90.decode_datagram(gdl90.encode(m)) == [m]
    # A message whose FCS is 0x7E7D, sent LSB first as 7D 7E, stuffed.
    m = next(
        bytes([0x14, x >> 8, x & 0xFF])
        for x in range(1 << 16)
        if gdl90.calc_crc(bytes([0x14, x >> 8, x & 0xFF])) == 0x7E7D
    )
    framed = gdl90.encode(m)
    assert framed.endswith(bytes.fromhex("7D5D7D5E7E"))
    assert gdl90.decode_datagram(framed) == [m]


def test_t1_back_to_back_messages():
    a, b, c = HEARTBEAT, report(addr=1), report(addr=2)
    for msgs in ([a, b], [a, b, c]):
        datagram = b"".join(gdl90.encode(m) for m in msgs)
        assert datagram.count(b"\x7e\x7e") == len(msgs) - 1
        assert gdl90.decode_datagram(datagram) == msgs


def test_t1_back_to_back_through_the_writer(gw):
    gw.send(HEARTBEAT, report(addr=1), report(addr=2), report(addr=3))
    assert gw.occupied() == {1: 1, 2: 2, 3: 3}


def test_t1_flipped_bit_and_msb_id_discarded(gw):
    rec = Recorder(gw.db, [k for n in (1, 2) for k in slot_keys(n)])
    good = gdl90.encode(report(addr=5))
    for i in range(1, len(good) - 1):
        for bit in range(8):
            bad = bytearray(good)
            bad[i] ^= 1 << bit
            # Including flips that make or unmake a flag or an escape.
            assert gdl90.decode_datagram(bytes(bad)) == [], (i, bit)
    one = bytearray(good)
    one[10] ^= 0x04
    gw.send_raw(bytes(one))
    msb = bytearray(report(addr=6))
    msb[0] |= 0x80
    gw.send_raw(gdl90.encode(bytes(msb)))
    assert gdl90.decode_datagram(gdl90.encode(bytes(msb))) == []
    gw.settle()
    assert gw.occupied() == {}
    assert rec.take() == []


# --- T2 Field boundaries ------------------------------------------------------


def one_target(gw, **kw):
    gw.send(HEARTBEAT, report(addr=0x123456, **kw))
    assert gw.occupied() == {(kw.get("t", 0) << 24) | 0x123456: 1}


# G§3.5.1.3 table: encoded value -> degrees.
LATLON_TABLE = [
    (0x000000, 0.0),
    (0x200000, 45.0),
    (0x400000, 90.0),
    (0x600000, 135.0),
    (0x800000, -180.0),
    (0xA00000, -135.0),
    (0xC00000, -90.0),
    (0xE00000, -45.0),
]


@pytest.mark.parametrize("raw,deg", LATLON_TABLE)
def test_t2_latlon_table(gw, raw, deg):
    lat_raw = raw if abs(deg) <= 90 else 0x200000
    one_target(gw, lat_raw=lat_raw, lon_raw=raw, nic=1)
    assert gw.val("TFC1LON") == pytest.approx(deg)
    if abs(deg) <= 90:
        assert gw.val("TFC1LAT") == pytest.approx(deg)
    assert not gw.flags("TFC1LAT")["bad"] and not gw.flags("TFC1LON")["bad"]


def test_t2_no_position(gw):
    one_target(gw, lat_raw=0, lon_raw=0, nic=0)
    assert gw.flags("TFC1LAT")["bad"] and gw.flags("TFC1LON")["bad"]
    # Zero position with a non-zero NIC is a real position (0N 0E).
    gw.send(report(addr=0x123456, lat_raw=0, lon_raw=0, nic=1))
    assert not gw.flags("TFC1LAT")["bad"] and gw.val("TFC1LAT") == 0.0


@pytest.mark.parametrize(
    "ddd,alt,bad",
    [(0x000, -1000, False), (0x028, 0, False), (0x050, 1000, False),
     (0xFFE, 101350, False), (0xFFF, None, True)],
)
def test_t2_altitude(gw, ddd, alt, bad):
    one_target(gw, ddd=ddd)
    assert gw.flags("TFC1ALT")["bad"] is bad
    if alt is not None:
        assert gw.val("TFC1ALT") == alt


@pytest.mark.parametrize("hhh,gs,bad", [(0, 0, False), (0xFFE, 4094, False), (0xFFF, None, True)])
def test_t2_horizontal_velocity(gw, hhh, gs, bad):
    one_target(gw, hhh=hhh)
    assert gw.flags("TFC1GS")["bad"] is bad
    if gs is not None:
        assert gw.val("TFC1GS") == gs


@pytest.mark.parametrize(
    "vvv,vs,bad",
    [(0x000, 0, False), (0x001, 64, False), (0xFFF, -64, False),
     (0x1FD, 32576, False), (0x1FE, 32640, False), (0xE03, -32576, False),
     (0xE02, -32640, False), (0x800, None, True)],
)
def test_t2_vertical_velocity(gw, vvv, vs, bad):
    one_target(gw, vvv=vvv)
    assert gw.flags("TFC1VS")["bad"] is bad
    if vs is not None:
        assert gw.val("TFC1VS") == vs


@pytest.mark.parametrize("vvv", [0x1FF, 0x7FF, 0x801, 0xE01])
def test_t2_vertical_velocity_unused_codes_are_bad(gw, vvv):
    # G§3.5.1.8 leaves these unused; clamped they would read +-32,640 fpm.
    one_target(gw, vvv=vvv)
    assert gw.flags("TFC1VS")["bad"]


@pytest.mark.parametrize("tt,trk", [(0, 0.0), (128, 180.0), (255, 358.59375)])
def test_t2_track(gw, tt, trk):
    one_target(gw, tt=tt, misc=0x9)
    assert gw.val("TFC1TRK") == pytest.approx(trk)
    assert not gw.flags("TFC1TRK")["bad"]


@pytest.mark.parametrize("misc", range(16))
def test_t2_misc_all_combinations(gw, misc):
    one_target(gw, misc=misc, tt=64)
    assert gw.val("TFC1TTYP") == misc & 3
    assert gw.val("TFC1EXTR") is bool(misc & 4)
    assert gw.val("TFC1AIR") is bool(misc & 8)
    assert gw.flags("TFC1TRK")["bad"] is (misc & 3 == 0)
    if misc & 3:
        assert gw.val("TFC1TRK") == 90.0


@pytest.mark.parametrize("cs,want", [("        ", ""), ("N825V", "N825V"), ("N8-5V", "N8-5V")])
def test_t2_call_sign(gw, cs, want):
    one_target(gw, cs=cs)
    assert gw.val("TFC1CS") == want


def test_t2_identity_and_small_fields(gw):
    one_target(gw, s=1, t=2, nic=11, nacp=10, ee=39, p=6)
    assert gw.val("TFC1TGT") == (2 << 24) | 0x123456
    assert gw.val("TFC1ADDR") == 0x123456
    assert gw.val("TFC1ATYP") == 2
    assert (gw.val("TFC1ALRT"), gw.val("TFC1NIC"), gw.val("TFC1NACP")) == (1, 11, 10)
    assert (gw.val("TFC1CAT"), gw.val("TFC1EMRG")) == (39, 6)
    for f in FIELDS:
        if f not in ("LAT", "LON", "ALT", "GS", "VS", "TRK"):
            assert not gw.flags("TFC1" + f)["bad"]
    assert not gw.flags("TFC1TGT")["bad"]


# --- T3 Slot lifecycle --------------------------------------------------------


def assert_slot_commits(events, n):
    """Every TGT write of slot n other than -1 follows a write of all its
    fields since the slot's previous TGT write; every -1 precedes the next
    field write.  Returns the TGT values in order."""
    tgts = []
    since = set()
    for key, value in events:
        if not key.startswith("TFC{}".format(n)) or key[len("TFC{}".format(n))].isdigit():
            continue
        field = key[len("TFC{}".format(n)):]
        if field == "TGT":
            if value[0] != -1:
                assert since == set(FIELDS), "TGT {} before fields {}".format(
                    value[0], sorted(set(FIELDS) - since)
                )
            tgts.append(value[0])
            since = set()
        else:
            since.add(field)
    assert since == set(), "fields written after the last TGT: {}".format(since)
    return tgts


def test_t3_lifecycle(database, gw, clock):
    rec = Recorder(database, [k for n in (1, 2) for k in slot_keys(n)])
    gw.send(HEARTBEAT, report(addr=0xA))
    assert gw.occupied() == {0xA: 1}
    clock.t += 1
    gw.send(HEARTBEAT, report(addr=0xB))
    assert gw.occupied() == {0xA: 1, 0xB: 2}
    clock.t += 1
    gw.send(HEARTBEAT, report(addr=0xA, ddd=0x100))
    assert gw.occupied() == {0xA: 1, 0xB: 2}
    assert gw.val("TFC1ALT") == 0x100 * 25 - 1000

    # B keeps reporting; A goes silent at t0 + 2 and is evicted 10 s later
    # by the timer: nothing arrives in the window where it happens.
    a_seen = clock.t
    clock.t = a_seen + 9.5
    gw.send(HEARTBEAT, report(addr=0xB))
    assert gw.occupied() == {0xA: 1, 0xB: 2}
    clock.t = a_seen + 10.6
    deadline = time.time() + 3
    while gw.val("TFC1TGT") != -1:
        assert time.time() < deadline, "no eviction without input"
        time.sleep(0.01)
    assert gw.occupied() == {0xB: 2}

    # Reuse: C takes the lowest empty slot, through -1.
    gw.send(HEARTBEAT, report(addr=0xC))
    assert gw.occupied() == {0xB: 2, 0xC: 1}

    events = rec.take()
    assert assert_slot_commits(events, 1) == [0xA, 0xA, -1, 0xC]
    assert assert_slot_commits(events, 2) == [0xB, 0xB]


# --- T4 Staleness (wall clock) ----------------------------------------------


def test_t4_slot_keys_go_old_before_eviction(database):
    g = Gateway(database)  # real monotonic clock, evict_s 10
    try:
        g.send(HEARTBEAT, report(addr=0x77))
        assert g.occupied() == {0x77: 1}
        assert not any(g.flags(k)["old"] for k in slot_keys(1))
        time.sleep(3.2)
        assert all(g.flags(k)["old"] for k in slot_keys(1))
        assert g.val("TFC1TGT") == 0x77  # still there: eviction is at 10 s
    finally:
        g.stop()


# --- T5 Capacity ---------------------------------------------------------------


def own_position(database, bad=False):
    database.write("LAT", (OWN_LAT, False, bad, False))
    database.write("LONG", (OWN_LON, False, bad, False))


def ranged(i):
    """Target i, about 0.6 nm x i north of ownship."""
    return report(addr=0x100 + i, lat=OWN_LAT + 0.01 * i, lon=OWN_LON, nic=8)


def test_t5_capacity(database, gw, clock):
    order = list(range(1, 41))
    for _ in range(2):
        random.Random(4579).shuffle(order)
        own_position(database)
        gw.send(HEARTBEAT, *[ranged(i) for i in order])
        clock.t += 1
        gw.settle()
    assert set(gw.occupied()) == {0x100 + i for i in range(1, 33)}
    assert gw.val("TFCCOUNT") == 32
    assert gw.val("TFCDROP") == 8

    # A nearer 41st evicts the farthest held (target 32).
    own_position(database)
    gw.send(report(addr=0x200, lat=OWN_LAT + 0.001, lon=OWN_LON, nic=8))
    held = gw.occupied()
    assert 0x200 in held and 0x100 + 32 not in held and len(held) == 32

    # With ownship position bad, a newcomer is dropped however near.
    own_position(database, bad=True)
    gw.send(report(addr=0x201, lat=OWN_LAT, lon=OWN_LON + 0.0001, nic=8))
    assert 0x201 not in gw.occupied()
    clock.t += 1
    gw.settle()
    assert gw.val("TFCDROP") == 1


# --- T6 Ownship filter ----------------------------------------------------------

A, B = 0xA1B2C3, 0xB4C5D6


def test_t6_ownship_report_filters_icao_types(gw):
    gw.send(HEARTBEAT, ownship(t=0, addr=A))
    gw.send(report(t=0, addr=A), report(t=2, addr=A))
    assert gw.occupied() == {}
    gw.send(report(t=3, addr=A))
    assert gw.occupied() == {(3 << 24) | A: 1}


def test_t6_configured_address_alone(make_gw):
    gw = make_gw(ownship_address="A1B2C3")
    gw.send(HEARTBEAT, report(t=0, addr=A), report(t=2, addr=A))
    assert gw.occupied() == {}
    gw.send(report(t=3, addr=A))
    assert gw.occupied() == {(3 << 24) | A: 1}


def test_t6_config_and_report_disagree(make_gw, caplog):
    gw = make_gw(ownship_address="0xB4C5D6")
    with caplog.at_level("WARNING"):
        gw.send(HEARTBEAT, ownship(t=0, addr=A))
        gw.send(ownship(t=0, addr=A))
        gw.send(*[report(t=t, addr=x) for x in (A, B) for t in (0, 2)])
    assert gw.occupied() == {}
    lines = [r for r in caplog.records if "disagrees" in r.getMessage()]
    assert len(lines) == 1


def test_t6_no_call_sign_or_geometric_filter(database, make_gw):
    gw = make_gw(ownship_address="A1B2C3")
    own_position(database)
    gw.send(
        HEARTBEAT,
        ownship(t=0, addr=A, lat=OWN_LAT, lon=OWN_LON, cs="N825V"),
        # A type-3 track file whose call sign is our registration: shown.
        report(t=3, addr=0x00ABCD, cs="N825V"),
        # Two targets at our own position and altitude: shown.
        report(t=0, addr=0x111111, lat=OWN_LAT, lon=OWN_LON, ddd=0x0F0),
        report(t=2, addr=0x222222, lat=OWN_LAT, lon=OWN_LON, ddd=0x0F0),
        report(t=0, addr=A, lat=OWN_LAT, lon=OWN_LON),
    )
    held = gw.occupied()
    assert set(held) == {(3 << 24) | 0x00ABCD, 0x111111, (2 << 24) | 0x222222}
    for n in held.values():
        assert gw.val("TFC{}ADDR".format(n)) != A


def test_t6_self_assigned(make_gw):
    gw = make_gw(ownship_address="A1B2C3")
    gw.send(HEARTBEAT, ownship(t=1, addr=B))
    # Our own self-assigned address is filtered only as type 1.
    gw.send(report(t=1, addr=B))
    assert gw.occupied() == {}
    gw.send(report(t=0, addr=B))
    # A configured ICAO address never matches a self-assigned one.
    gw.send(report(t=1, addr=A))
    assert set(gw.occupied()) == {B, (1 << 24) | A}


def test_ownship_address_must_be_a_hex_string(make_gw, caplog):
    log = __import__("logging").getLogger("t")
    assert parse_ownship_address("a1b2c3", log) == A
    assert parse_ownship_address("0xA1B2C3", log) == A
    assert parse_ownship_address("", log) is None
    assert parse_ownship_address(None, log) is None
    with caplog.at_level("ERROR"):
        assert parse_ownship_address(123456, log) is None
        assert parse_ownship_address("1000000", log) is None
        assert parse_ownship_address("N825V", log) is None
    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 3


# --- T7 Identity collision ---------------------------------------------------------


def test_t7_identity_collision_alternates_in_one_slot(gw):
    north = OWN_LAT + 1 / 60.0  # 1 nm apart
    for lat in (OWN_LAT, north, OWN_LAT, north):
        gw.send(HEARTBEAT, report(addr=0x0C0FFE, lat=lat, lon=OWN_LON, nic=8))
        assert gw.occupied() == {0x0C0FFE: 1}
        assert gw.val("TFC1LAT") == pytest.approx(lat, abs=2.2e-5)
    assert geo.distance_nm(OWN_LAT, OWN_LON, north, OWN_LON) == pytest.approx(1.0, abs=0.01)


# --- T8 End to end through netfix ---------------------------------------------------


def test_t8_netfix_line_order(database, clock):
    port = free_port()
    nc, meta = cfg.from_yaml(
        "type: server\nhost: 127.0.0.1\nport: {}\ntimeout: 0.2\n".format(port),
        metadata=True,
    )
    nf = fixgw.plugins.netfix.Plugin("netfix", nc, meta)
    nf.start()
    gw = Gateway(database, clock)
    sock = None
    try:
        deadline = time.time() + 5
        while sock is None:
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            except ConnectionRefusedError:
                assert time.time() < deadline
                time.sleep(0.05)
        keys = slot_keys(1)
        sock.sendall("".join("@s{}\n".format(k) for k in keys).encode())
        buf = b""
        lines = []

        def read_until(pred):
            nonlocal buf
            deadline = time.time() + 5
            while True:
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    lines.append(line.decode())
                    if pred(lines[-1]):
                        return
                assert time.time() < deadline, lines
                buf += sock.recv(4096)

        acks = set()
        read_until(lambda ln: acks.add(ln) or len([a for a in acks if a.startswith("@s")]) == len(keys))
        del lines[:]

        a, b = 0x0AAAAA, 0x0BBBBB
        gw.send(HEARTBEAT, report(addr=a))
        read_until(lambda ln: ln.startswith("TFC1TGT;{};".format(a)))
        clock.t += 10.6  # evict, by the timer
        read_until(lambda ln: ln.startswith("TFC1TGT;-1;"))
        gw.send(HEARTBEAT, report(addr=b, ddd=0x200, cs="N1"))
        read_until(lambda ln: ln.startswith("TFC1TGT;{};".format(b)))
    finally:
        if sock is not None:
            sock.close()
        gw.stop()
        nf.stop()

    seen = [(ln.split(";")[0][len("TFC1"):], ln.split(";")[1]) for ln in lines]
    i_a = seen.index(("TGT", str(a)))
    i_e = seen.index(("TGT", "-1"))
    i_b = seen.index(("TGT", str(b)))
    fields = set(FIELDS)
    # Fill: every field, then TGT.
    assert {f for f, _ in seen[:i_a]} == fields
    # Eviction: TGT = -1 before any field of the next fill; nothing but TGT
    # (a fail-flag change) between the fill and the eviction.
    assert {f for f, _ in seen[i_a:i_e]} == {"TGT"}
    assert {f for f, _ in seen[i_e + 1:i_b]} == fields
    assert ("ALT", str(float(0x200 * 25 - 1000))) in seen[i_e:i_b]
    assert ("CS", "N1") in seen[i_e:i_b]


# --- T9 Source failure ----------------------------------------------------------------


def test_t9_heartbeat_liveness(gw, clock):
    gw.send(HEARTBEAT, report(addr=1), report(addr=2))
    clock.t += 1
    gw.settle()
    assert not gw.flags("TFCCOUNT")["fail"]
    # Traffic keeps coming, Heartbeats stop.
    for _ in range(4):
        clock.t += 1
        gw.send(report(addr=1), report(addr=2))
    assert gw.flags("TFCCOUNT")["fail"]
    assert gw.flags("TFC1TGT")["fail"] and gw.flags("TFC2TGT")["fail"]
    assert gw.val("TFCCOUNT") == 2
    # A newcomer while failed is failed too.
    gw.send(report(addr=3))
    assert gw.flags("TFC3TGT")["fail"]
    # Empty slots are not touched.
    assert not gw.flags("TFC4TGT")["fail"]
    gw.send(HEARTBEAT)
    for k in ("TFCCOUNT", "TFC1TGT", "TFC2TGT", "TFC3TGT"):
        assert not gw.flags(k)["fail"], k
    clock.t += 1
    gw.send(report(addr=1))
    assert not gw.flags("TFC1TGT")["fail"]


def test_t9_failed_slots_still_evict(gw, clock):
    gw.send(HEARTBEAT, report(addr=1))
    clock.t += 4
    gw.settle()
    assert gw.flags("TFC1TGT")["fail"]
    clock.t += 7
    gw.settle()
    assert gw.occupied() == {}
    assert gw.val("TFCCOUNT") == 0 and gw.flags("TFCCOUNT")["fail"]


# --- T10 Clamp ---------------------------------------------------------------------------


def test_t10_top_altitude_is_not_clamped(gw):
    gw.send(HEARTBEAT, report(addr=0x99, ddd=0xFFE))
    assert gw.val("TFC1ALT") == 101350.0
    assert not gw.flags("TFC1ALT")["bad"]


# --- Plugin configuration ----------------------------------------------------------------


def test_traffic_false_writes_nothing(database):
    g = Gateway(database, traffic=False)
    try:
        assert g.writer is None
        g.send(HEARTBEAT, report(addr=1))
        assert g.occupied() == {}
    finally:
        g.stop()


def test_block_has_spec_shape(database):
    keys = [k for k in database.listkeys() if k.startswith("TFC")]
    assert len(keys) == 18 * 32 + 2
    assert database.read("TFC32TGT")[0] == -1
    for f in FIELDS + ("TGT",):
        assert database.get_raw_item("TFC1" + f).tol == 3000
