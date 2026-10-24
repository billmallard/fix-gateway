"""RS1 (fix-gateway#39) netfix frame-size check at a full commit.

The flight-plan block was designed for netfix's ~1 KB frames (PA3,
procedures_and_airways_plan.md section 3.2): scalar keys, one per line, no
JSON. RS1 adds 30 segment rows of six keys each. This drives the largest
commit the contract allows -- 100 slots, 30 segments, every string at its
length limit -- through a real netfix server socket in the pyavtools
client's write format (``KEY;value;flags\\n``, ``fix/__init__.py``
``output_value``), sent as one burst, and checks two things:

1. No single line comes near the 1024-byte receive buffer both netfix ends
   use (they reassemble on newline, so this is the design margin, not a hard
   protocol limit).
2. The engine loads the whole commit exactly -- every slot's segment index
   and every segment row -- on the FPLSEQ that lands last.
"""

import socket
import time

import pytest

import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.flightplan.engine as engine
import fixgw.plugins.netfix
from fixgw import cfg

PORT = 34931
BUFFER = 1024

NETFIX_CONFIG = f"""
type: server
host: 127.0.0.1
port: {PORT}
buffer_size: {BUFFER}
timeout: 1.0
"""


def worst_case_commit(seq):
    """(lines, slots, segments) for the largest legal commit: 100 slots,
    30 airway segments (29 of 3 slots, one of 13), 24-char segment idents,
    6-char fix idents, 32-char route name, long float reprs."""
    sizes = [3] * 29 + [13]
    assert sum(sizes) == 100
    slots = []
    segments = []
    slot = 1
    for k, size in enumerate(sizes, start=1):
        first = slot
        for _ in range(size):
            slots.append(
                engine.Waypoint(
                    id=f"F{slot:05d}"[:6], lat=-33.123456789012 - slot * 1e-9, lon=-119.84037000000001 - slot * 1e-9,
                    type=6, pt="RF", crs=359.9, dst=999.9, alt="B12000,14000", spd=400, sgi=k, flags=63,
                    ctrlat=-33.987654321098, ctrlon=-118.123456789012, turn="R",
                )
            )
            slot += 1
        segments.append(
            engine.Segment(engine.SEGKIND_AIRWAY, f"{k:02d}".ljust(24, "Z"), slots[first - 1].id, slots[slot - 2].id,
                           first, slot - 1)
        )

    def line(key, value):
        return f"{key};{value};0000\n"

    lines = []
    for i, w in enumerate(slots, start=1):
        for field, value in (
            ("ID", w.id), ("LAT", w.lat), ("LON", w.lon), ("TYPE", w.type), ("PT", w.pt), ("CRS", w.crs),
            ("DST", w.dst), ("ALT", w.alt), ("SPD", w.spd), ("SGI", w.sgi), ("FLAGS", w.flags),
            ("CTRLAT", w.ctrlat), ("CTRLON", w.ctrlon), ("TURN", w.turn),
        ):
            lines.append(line(f"FPL{i}{field}", value))
    for k, s in enumerate(segments, start=1):
        for field, value in (
            ("KIND", s.kind), ("ID", s.id), ("ENT", s.ent), ("EXT", s.ext), ("FIRST", s.first), ("LAST", s.last),
        ):
            lines.append(line(f"FPLS{k}{field}", value))
    lines.append(line("FPLSEGCNT", len(segments)))
    lines.append(line("FPLCOUNT", len(slots)))
    lines.append(line("FPLNAME", "R" * 32))
    lines.append(line("FPLAPRTYPE", "RNAV"))
    lines.append(line("FPLDBCYC", "2611"))
    lines.append(line("FPLSEQ", seq))
    return lines, slots, segments


@pytest.fixture
def netfix_and_engine(database, tmp_path):
    nc, nc_meta = cfg.from_yaml(NETFIX_CONFIG, metadata=True)
    nf = fixgw.plugins.netfix.Plugin("netfix", nc, nc_meta)
    nf.start()
    fp = flightplan.Plugin(
        "flightplan",
        {"CONFIGPATH": str(tmp_path), "state_file": str(tmp_path / "s.json"), "restore_delay": 0.0},
        {},
    )
    time.sleep(0.1)
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=2.0)
    yield sock, fp
    sock.close()
    nf.stop()


def test_full_commit_fits_netfix_frames_and_loads_whole(netfix_and_engine, database):
    sock, fp = netfix_and_engine
    lines, slots, segments = worst_case_commit(seq=4242)
    payload = "".join(lines).encode()

    longest = max(len(x.encode()) for x in lines)
    seg_lines = [x for x in lines if x.startswith("FPLS") and x[4].isdigit()]
    print(
        f"\nfull commit: {len(lines)} lines, {len(payload)} bytes; "
        f"segment table {len(seg_lines)} lines, {sum(len(x) for x in seg_lines)} bytes; "
        f"longest line {longest} bytes (buffer {BUFFER})"
    )
    assert len(lines) == 100 * 14 + 30 * 6 + 6
    assert longest < BUFFER // 8  # one line is a small fraction of a frame

    sock.sendall(payload)
    deadline = time.time() + 10.0
    while fp.thread.engine.seq != 4242 and time.time() < deadline:
        time.sleep(0.05)

    e = fp.thread.engine
    assert e.seq == 4242, f"commit not loaded; FPLMSG={database.read('FPLMSG')[0]!r}"
    assert e.count == 100 and len(e.segments) == 30
    assert [w.sgi for w in e.route] == [w.sgi for w in slots]
    assert [w.id for w in e.route] == [w.id for w in slots]
    assert [s.to_dict() for s in e.segments] == [s.to_dict() for s in segments]
    assert e.route[99].lat == pytest.approx(slots[99].lat, abs=1e-12)
