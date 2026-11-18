"""
FIS-B NEXRAD (products 63 and 64) block decode and the mosaic (AER-5937).

The frames are real.  tg30 is about 8 minutes of live FIS-B captured by
fisb-decode's author on 2020-10-30, with radar returns in both products; the
2015 dump978 sample adds empty-block runs, including the L = 0 form.  The
expected blocks come from fisb-decode, an independent decoder written against
DO-358B (data/README.md has the commands).  The age tests drive the clock
directly.
"""
import calendar
import gzip
import json
import os
from unittest.mock import MagicMock

import pytest

import fixgw.plugins.stratux as stratux
from fixgw.plugins.stratux import MainThread, fisb, gdl90, nexrad

DATA = os.path.join(os.path.dirname(__file__), "data")


def _lines(name):
    with gzip.open(os.path.join(DATA, name), "rt") as f:
        return [ln.strip() for ln in f if ln.startswith("+")]


def _oracle(name):
    with gzip.open(os.path.join(DATA, name), "rt") as f:
        return json.load(f)


TG30 = _lines("fisb-decode-tg30-2020.978.gz")
S2015 = _lines("dump978-sample-2015.txt.gz")


def _bytes(line):
    return bytes.fromhex(line[1:].split(";")[0])


def _rx(line):
    # the capture's own receive time, "t=<epoch seconds>;"
    return float(line.rsplit("t=", 1)[1].rstrip(";"))


def _gdl90(line):
    # G§3.3: ID 0x07, a 3-byte time of reception, the 432-byte uplink
    return gdl90.encode(bytes([0x07, 0x12, 0x34, 0x00]) + _bytes(line))


def utc(y, mo, d, h, mi, s=0):
    return float(calendar.timegm((y, mo, d, h, mi, s)))


# tg30's images: regional at 08:52, 08:54, 08:56 and 08:58, CONUS at 08:54
CONUS_OBS = utc(2020, 10, 30, 8, 54)


def _row(apdu, b):
    return [
        b.product_id, apdu.hour, apdu.minute, b.scale, b.north, b.west,
        b.north - b.height, b.west + b.width,
        None if b.bins is None else "".join(str(v) for v in b.bins),
    ]


def _ours(lines):
    out = {}
    for i, line in enumerate(lines):
        rows = []
        for a in fisb.parse_uat_uplink(_bytes(line)).apdus:
            if a.product_id in nexrad.PRODUCTS:
                blocks, refused = nexrad.parse_blocks(a.payload, a.product_id)
                assert refused is None
                rows.extend(_row(a, b) for b in blocks)
        if rows:
            out[i] = rows
    return out


# --- every block against fisb-decode -----------------------------------------


@pytest.mark.parametrize("lines,oracle,n_uplinks,n_blocks", [
    (TG30, "nexrad_oracle_tg30.json.gz", 96, 4850),
    (S2015, "nexrad_oracle_2015.json.gz", 6, 1342),
], ids=["tg30", "dump978-2015"])
def test_every_block_matches_fisb_decode(lines, oracle, n_uplinks, n_blocks):
    want = _oracle(oracle)
    assert want["uplinks"] == len(lines)
    expected = {i: rows for i, rows in want["block_uplinks"]}
    assert len(expected) == n_uplinks
    assert sum(len(r) for r in expected.values()) == n_blocks
    assert _ours(lines) == expected


def test_tg30_holds_real_returns_in_both_products():
    # the comparison above would be weak over blank sky: count the bins
    levels = {63: [0] * 8, 64: [0] * 8}
    for rows in _ours(TG30).values():
        for r in rows:
            if r[8] is not None:
                for ch in r[8]:
                    levels[r[0]][int(ch)] += 1
    assert levels[63][2] > 1000 and levels[63][5] > 0
    assert levels[64][5] > 100 and levels[64][7] > 0


# --- golden blocks, by hand --------------------------------------------------
#
# Positions checked against dump978's block_location by hand: block n is row
# n // 450 and column n % 450; its north edge is (row + 1) * 4' and its west
# edge column * 48', less 360 deg past 180.


def _apdus(line, pid):
    return [a for a in fisb.parse_uat_uplink(_bytes(line)).apdus if a.product_id == pid]


def _apdu(line, pid, payload_hex):
    """The one APDU of product pid in line with this payload."""
    [a] = [a for a in _apdus(line, pid) if a.payload.hex() == payload_hex]
    return a


def test_golden_conus_block_with_extreme_returns():
    # tg30 line 375: CONUS block 249655, row 554 column 355, scale 1 (5 x 5)
    a = _apdu(TG30[375], 64,
              "93cf370904059902030229020507892229030605790a0b12290d79020b041a21")
    assert (a.hour, a.minute) == (8, 54)
    blocks, refused = nexrad.parse_blocks(a.payload, 64)
    assert refused is None and len(blocks) == 1
    b = blocks[0]
    assert (b.block_number, b.scale) == (249655, 1)
    # 37 00'N 76 00'W, 20' tall and 4 deg wide
    assert (b.north, b.west, b.height, b.width) == (2220, -4560, 20, 240)
    assert "".join(str(v) for v in b.bins) == (
        "11451111111111111111111123211111"
        "12571111111111111111112222211111"
        "13651111111111111111223322211111"
        "15511111111111111112334222211111")
    # bin 35 (row 1, column 3) is level 7.  Scale-1 bins are 5' by 7.5', so
    # its centre is 7.5' south and 26.25' east of the corner: 36 52.5'N,
    # 75 33.75'W
    assert b.bins[35] == 7
    lat, lon = nexrad.bin_center(b, 35)
    assert lat == pytest.approx(36 + 52.5 / 60)
    assert lon == pytest.approx(-(75 + 33.75 / 60))


def test_golden_regional_block():
    # tg30 line 122: regional block 278447, row 618 column 347, scale 0
    a = _apdu(TG30[122], 63, (
        "843faf0801200203120912130c2305040b08012002030a1113040b040223041308"
        "012002030a1113040b0402230413380a0312232a33"))
    assert (a.hour, a.minute) == (8, 52)
    b = nexrad.parse_blocks(a.payload, 63)[0][0]
    assert (b.block_number, b.scale) == (278447, 0)
    # 41 16'N 82 24'W, 4' tall and 48' wide: 1.5' x 1' bins
    assert (b.north, b.west, b.height, b.width) == (2476, -4944, 4, 48)
    assert "".join(str(v) for v in b.bins) == (
        "00100000232221122233344333335433"
        "00100000232211133343342333334333"
        "00100000232211133343342333334333"
        "00000000223222333332222223333333")


def test_golden_regional_empty_run():
    # tg30 line 113: 03e984f10f.  Block 256388 and, from the bitmap, L = 1:
    # the nibble F marks b+1..b+4 and the byte 0F b+5..b+8
    a = _apdu(TG30[113], 63, "03e984f10f")
    blocks = nexrad.parse_blocks(a.payload, 63)[0]
    assert [b.block_number for b in blocks] == list(range(256388, 256397))
    assert all(b.bins is None for b in blocks)
    assert [(b.north, b.west) for b in blocks[:2]] == [(2280, -5376), (2280, -5328)]


def test_golden_conus_empty_run_steps_by_its_scale():
    # tg30 line 330: 12c76d70.  Scale 1, block 182125, L = 0, nibble 7: three
    # more blocks, each one 5-cell block east, not one cell
    a = _apdu(TG30[330], 64, "12c76d70")
    blocks = nexrad.parse_blocks(a.payload, 64)[0]
    assert [b.block_number for b in blocks] == [182125, 182130, 182135, 182140]
    assert [b.west for b in blocks] == [-6000, -5760, -5520, -5280]
    assert all((b.north, b.height, b.width) == (1620, 20, 240) for b in blocks)


def test_empty_run_and_data_never_claim_one_position():
    # The real-frame check that chose the scale step: in each image, no
    # position is sent both as empty and with data.  Stepping by 1 cell, as
    # dump978 does, fails it at 4 positions.
    seen = {}
    for line in TG30:
        for a in fisb.parse_uat_uplink(_bytes(line)).apdus:
            if a.product_id in nexrad.PRODUCTS:
                for b in nexrad.parse_blocks(a.payload, a.product_id)[0]:
                    key = (a.product_id, a.hour, a.minute, b.block_number)
                    seen.setdefault(key, set()).add(b.bins is None)
    assert all(len(kinds) == 1 for kinds in seen.values())


def test_every_scale_1_block_is_on_the_5_by_5_grid():
    numbers = [nexrad.parse_blocks(a.payload, 64)[0][0].block_number
               for line in TG30 for a in _apdus(line, 64)]
    assert len(numbers) == 1087
    assert all(n % 450 % 5 == 0 and n // 450 % 5 == 4 for n in numbers)


# --- wire rules ---------------------------------------------------------------


def _rle(scale, number, runs, south=False):
    head = 0x80 | (0x40 if south else 0) | (scale << 4) | (number >> 16)
    return bytes([head, (number >> 8) & 0xFF, number & 0xFF]) + bytes(runs)


# 4 runs of 32 bins: 0xF8 is a run of 32 at level 0, 0xFB at level 3
FULL = [0xF8, 0xFB, 0xF8, 0xFF]


def test_block_zero_and_the_meridian():
    assert nexrad.block_location(0, 0) == (4, 0, 4, 48)
    assert nexrad.block_location(449, 0) == (4, -48, 4, 48)
    assert nexrad.block_location(225, 0) == (4, -10800, 4, 48)
    assert nexrad.block_location(4, 1) == (4, 192, 20, 240)


def test_run_lengths_fill_rows_west_to_east_then_north_to_south():
    b = nexrad.parse_blocks(_rle(0, 0, FULL), 63)[0][0]
    assert b.bins == bytes([0] * 32 + [3] * 32 + [0] * 32 + [7] * 32)


@pytest.mark.parametrize("runs", [
    FULL[:3],  # 96 bins
    FULL + [0x00],  # 128 bins and a byte left over
    [0xF8, 0xFB, 0xF8, 0xF8, 0xF8],  # past 128
], ids=["short", "trailing", "overlong"])
def test_bins_that_do_not_fill_128_exactly_are_refused(runs):
    assert nexrad.parse_blocks(_rle(0, 0, runs), 63) == ([], nexrad.REFUSED_MALFORMED)


def test_empty_bitmap_length_must_match():
    # L = 2 but one bitmap byte
    assert nexrad.parse_blocks(bytes([0x00, 0, 0, 0x02, 0xFF]), 63) == (
        [], nexrad.REFUSED_MALFORMED)


def test_empty_run_stays_on_its_row_across_the_meridian():
    # block 448 (row 0), b+1..b+4: 449, then 0, 1, 2 of row 0
    blocks = nexrad.parse_blocks(bytes([0x00, 0x01, 0xC0, 0xF0]), 63)[0]
    assert [b.block_number for b in blocks] == [448, 449, 0, 1, 2]
    assert [b.west for b in blocks] == [-96, -48, 0, 48, 96]


@pytest.mark.parametrize("payload,reason", [
    (_rle(0, 0, FULL, south=True), nexrad.REFUSED_SOUTH),
    (_rle(0, 405000, FULL), nexrad.REFUSED_POLAR),
    (_rle(3, 0, FULL), nexrad.REFUSED_SCALE),
    (b"\x80\x00\x00", nexrad.REFUSED_MALFORMED),
], ids=["south", "above-60N", "reserved-scale", "short"])
def test_blocks_not_placed_by_a_known_rule_are_refused(payload, reason):
    assert nexrad.parse_blocks(payload, 63) == ([], reason)


def test_hhmm_resolves_to_the_nearest_day():
    rx = utc(2020, 10, 30, 0, 5)
    assert nexrad.resolve_hhmm(23, 58, rx) == utc(2020, 10, 29, 23, 58)
    assert nexrad.resolve_hhmm(0, 1, utc(2020, 10, 29, 23, 59)) == utc(2020, 10, 30, 0, 1)
    assert nexrad.resolve_hhmm(24, 0, rx) is None


# --- the mosaic ----------------------------------------------------------------


def _uplink(scale, number, runs, hour, minute, pid=64):
    apdu = fisb.Apdu(pid, 0, None, None, hour, minute, False, None, None, None,
                     _rle(scale, number, runs))
    return fisb.Uplink(0.0, 0.0, True, 1, [apdu])


def _empty_uplink(scale, number, hour, minute, pid=64):
    payload = bytes([(scale << 4) | (number >> 16), (number >> 8) & 0xFF,
                     number & 0xFF, 0x00])
    apdu = fisb.Apdu(pid, 0, None, None, hour, minute, False, None, None, None, payload)
    return fisb.Uplink(0.0, 0.0, True, 1, [apdu])


def _current_numbers(mosaic, now):
    return sorted(c.block.block_number for c, _age in mosaic.current(now))


def test_block_current_until_its_image_is_30_minutes_old_then_excluded():
    obs = utc(2020, 10, 30, 8, 54)
    m = nexrad.Mosaic()
    m.ingest(_uplink(1, 249655, FULL, 8, 54), obs + 120)
    assert nexrad.NEXRAD_OLD_S == 30 * 60
    [(cell, age)] = m.current(obs + 30 * 60 - 1)
    assert age == 30 * 60 - 1
    assert (cell.observed, cell.first_rx, cell.last_rx) == (obs, obs + 120, obs + 120)
    # a stale block is never returned as current, whether or not purge ran
    assert m.current(obs + 30 * 60) == []
    assert len(m.cells) == 1
    m.purge(obs + 30 * 60)
    assert m.cells == {}


def test_every_block_carries_its_own_age():
    m = nexrad.Mosaic()
    m.ingest(_uplink(1, 249655, FULL, 8, 40), utc(2020, 10, 30, 8, 42))
    m.ingest(_uplink(1, 249660, FULL, 8, 54), utc(2020, 10, 30, 8, 56))
    now = utc(2020, 10, 30, 9, 0)
    ages = {c.block.block_number: age for c, age in m.current(now)}
    assert ages == {249655: 20 * 60, 249660: 6 * 60}
    # ten minutes on, the 08:40 block is gone and the 08:54 one stays
    assert _current_numbers(m, utc(2020, 10, 30, 9, 10)) == [249660]


def test_block_already_old_when_received_is_not_stored():
    m = nexrad.Mosaic()
    m.ingest(_uplink(1, 249655, FULL, 8, 0), utc(2020, 10, 30, 8, 31))
    assert m.cells == {} and m.counts["stale"] == 1


def test_block_dated_ahead_of_the_clock_is_old():
    # 09:30 received at 09:00 (more than the 10-minute slack): the clock is wrong
    m = nexrad.Mosaic()
    m.ingest(_uplink(1, 249655, FULL, 9, 30), utc(2020, 10, 30, 9, 0))
    assert m.current(utc(2020, 10, 30, 9, 0)) == []


def test_older_image_never_replaces_newer():
    m = nexrad.Mosaic()
    m.ingest(_uplink(1, 249655, FULL, 8, 54), utc(2020, 10, 30, 8, 55))
    m.ingest(_uplink(1, 249655, [0xF9] * 4, 8, 50), utc(2020, 10, 30, 8, 56))
    [(cell, _)] = m.current(utc(2020, 10, 30, 8, 57))
    assert cell.observed == utc(2020, 10, 30, 8, 54)
    assert cell.block.bins[32] == 3


def test_resent_block_keeps_first_receive_time():
    m = nexrad.Mosaic()
    m.ingest(_uplink(1, 249655, FULL, 8, 54), utc(2020, 10, 30, 8, 55))
    m.ingest(_uplink(1, 249655, FULL, 8, 54), utc(2020, 10, 30, 9, 5))
    [(cell, _)] = m.current(utc(2020, 10, 30, 9, 6))
    assert (cell.first_rx, cell.last_rx) == (utc(2020, 10, 30, 8, 55), utc(2020, 10, 30, 9, 5))


def test_empty_block_from_a_newer_image_clears_old_rain():
    m = nexrad.Mosaic()
    m.ingest(_uplink(1, 249655, FULL, 8, 40), utc(2020, 10, 30, 8, 41))
    m.ingest(_empty_uplink(1, 249655, 8, 54), utc(2020, 10, 30, 8, 55))
    [(cell, _)] = m.current(utc(2020, 10, 30, 8, 56))
    assert cell.block.bins is None


def test_products_are_separate_mosaics():
    m = nexrad.Mosaic()
    m.ingest(_uplink(0, 249655, FULL, 8, 54, pid=63), utc(2020, 10, 30, 8, 55))
    m.ingest(_uplink(1, 249655, FULL, 8, 54, pid=64), utc(2020, 10, 30, 8, 55))
    now = utc(2020, 10, 30, 8, 56)
    assert len(m.current(now)) == 2
    assert len(m.current(now, nexrad.PRODUCT_CONUS)) == 1
    assert m.product_counts() == {"regional": 1, "CONUS": 1}


def test_refusals_are_counted_and_logged_once():
    log = MagicMock()
    m = nexrad.Mosaic(log)
    for _ in range(3):
        m.ingest(_uplink(0, 405000, FULL, 8, 54), utc(2020, 10, 30, 8, 55))
    assert m.refused == {nexrad.REFUSED_POLAR: 3}
    assert log.warning.call_count == 1
    assert m.cells == {}


# --- the whole capture, through the plugin ---------------------------------------


class FakeSocket:
    def __init__(self, packets):
        self.packets = list(packets)

    def bind(self, address):
        pass

    def settimeout(self, timeout):
        pass

    def close(self):
        pass

    def recvfrom(self, _size):
        return self.packets.pop(0), ("192.168.10.1", 4000)


def _run(monkeypatch, lines, config=None):
    """The real receive loop over lines, each received at its own captured
    time."""
    sock = FakeSocket([_gdl90(ln) for ln in lines])
    times = [_rx(ln) for ln in lines]
    parent = MagicMock()
    parent.log = MagicMock()
    parent.config = dict({"traffic": False, "status": False, "fisb": True}, **(config or {}))
    parent.status = {}
    monkeypatch.setattr(stratux.socket, "socket", lambda *_args: sock)
    thread = MainThread(parent)
    clock = [times[0]]
    thread.wall_clock = lambda: clock[0]
    real = sock.recvfrom

    def recv(size):
        clock[0] = times[len(times) - len(sock.packets)]
        result = real(size)
        if not sock.packets:
            thread.getout = True
        return result

    sock.recvfrom = recv
    thread.run()
    return thread, parent


def test_whole_capture_builds_the_mosaic(monkeypatch):
    thread, parent = _run(monkeypatch, TG30)
    end = _rx(TG30[-1])
    m = thread.nexrad
    current = m.current(end)
    assert len(current) == len(m.cells) > 0
    conus = [(c, age) for c, age in current if c.block.product_id == 64]
    regional = [(c, age) for c, age in current if c.block.product_id == 63]
    # one CONUS image: every block observed 08:54 and aged from it
    assert {c.observed for c, _ in conus} == {CONUS_OBS}
    assert all(age == end - CONUS_OBS for _, age in conus)
    # regional blocks come from the four images, the newest replacing older
    assert {c.observed for c, _ in regional} <= {
        utc(2020, 10, 30, 8, m_) for m_ in (52, 54, 56, 58)}
    assert all(c.first_rx <= c.last_rx <= end for c, _ in current)
    thread.fisb_tick_at = 0.0
    thread.fisb_tick()
    counts = m.product_counts()
    assert parent.status["NEXRAD blocks"] == "regional %d, CONUS %d" % (
        counts["regional"], counts["CONUS"])
    assert "NEXRAD refused" not in parent.status


def test_whole_capture_is_gone_half_an_hour_after_the_last_image(monkeypatch):
    thread, parent = _run(monkeypatch, TG30)
    newest = max(c.observed for c in thread.nexrad.cells.values())
    assert thread.nexrad.current(newest + nexrad.NEXRAD_OLD_S) == []
    thread.wall_clock = lambda: newest + nexrad.NEXRAD_OLD_S
    thread.fisb_tick_at = 0.0
    thread.fisb_tick()
    assert thread.nexrad.cells == {}
    assert parent.status["NEXRAD blocks"] == "regional 0, CONUS 0"


def test_nexrad_off_with_fisb(monkeypatch):
    thread, parent = _run(monkeypatch, TG30[:5], config={"fisb": False})
    assert thread.nexrad is None
    assert "NEXRAD blocks" not in parent.status
