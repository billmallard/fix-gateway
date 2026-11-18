# FIS-B NEXRAD: the global block products 63 (regional) and 64 (CONUS),
# decoded to blocks on a fixed lat/lon grid and assembled into a mosaic.
#
# A radar picture is built from blocks that arrive over minutes, each carrying
# the observation time of the image it belongs to.  A block shown as current
# after its image has expired is the hazard this module exists to prevent: so
# every block keeps its own observation and receive times, and a block whose
# image is older than NEXRAD_OLD_S is dropped, never returned as current.
#
# Wire layout (the APDU payload, after fisb.parse_apdu):
#
#   byte/bit  7    6    5  4   3  2  1  0
#     0     | E  | NS | scale | block number (MSB, 4 bits)
#     1     |        block number
#     2     |        block number (LSB)
#
#   E = 1: a run-length block.  Each following byte is
#     | run length - 1 (5 bits) | intensity (3 bits) |
#   and the runs fill exactly 128 bins.
#   E = 0: an empty-block run.  The header block is empty, and byte 3 holds
#     | b+4 | b+3 | b+2 | b+1 | L (4 bits) |
#   followed by L bitmap bytes, bit 0 first: | b+12 ... b+5 |, and so on.  A
#   set bit marks the block that many grid steps east of the header block, on
#   the same row, as empty too.
#
# Geometry: block 0 has its south-west corner at 0N 0E.  Blocks are 48
# arcminutes of longitude by 4 of latitude, 450 to a ring of latitude,
# numbered west to east and then south to north.  A block number names the
# block's north-west high-resolution cell.  At scale 1 (medium) a block is 5
# cells wide and 5 tall, at scale 2 (low) 9 by 9, extending east and south of
# that cell.  Each block holds 32 x 4 bins, west to east and then north to
# south.
#
# Sources: dump978 (extract_nexrad.c; Stratux's uatparse/nexrad.go is a port
# of it) and fisb-decode (rand-projects, written against DO-358B) agree on
# the header, the run-length code, the bin order and the block location.  On
# empty-block runs they disagree, and real frames decide (tg30, see
# tests/plugins/stratux/data/README.md):
#   - the bitmap is L bytes after byte 3 (fisb-decode): all 450 empty-block
#     frames in tg30 are exactly 4 + L bytes.  dump978 reads one byte fewer,
#     and with L = 0 it does not mark even the header block.
#   - a bit steps one block of the block's own scale, 5 block numbers at
#     scale 1 (fisb-decode).  Every scale-1 block number in tg30 is on that
#     5 x 5 grid, and stepping by 1 (dump978) marks 4 positions empty that the
#     same image sends with data.
# Above 60N the grid halves (only even block numbers, 96 arcminutes wide) and
# the two sources disagree on the bin order; no capture we hold covers it, so
# those blocks are refused rather than placed by a guess.  FIS-B sends no
# southern-hemisphere blocks, and those are refused too.

import calendar
import time
from collections import namedtuple

from fixgw.plugins.stratux import weather

PRODUCT_REGIONAL = 63
PRODUCT_CONUS = 64
PRODUCTS = (PRODUCT_REGIONAL, PRODUCT_CONUS)
PRODUCT_NAMES = {PRODUCT_REGIONAL: "regional", PRODUCT_CONUS: "CONUS"}

BLOCKS_PER_RING = 450
BLOCK_THRESHOLD = 405000  # the first block at 60N
BLOCK_WIDTH = 48  # arcminutes of longitude, at scale 0
BLOCK_HEIGHT = 4  # arcminutes of latitude, at scale 0
COLS = 32
ROWS = 4
BINS = COLS * ROWS
SCALES = {0: 1, 1: 5, 2: 9}

# Intensity levels (DO-358B, as tabulated by fisb-decode's nextradRL).  The
# two products differ at the bottom: regional 0 is below 5 dBZ and 1 is 5-19;
# CONUS 0 is no data and 1 is below 20.  2-7 are the same: 20-29, 30-39,
# 40-44, 45-49, 50-54 and 55 dBZ and above.
LEVELS = 8

MIN = 60.0
# A block is old, and dropped, this long after its image's observation time:
# the 30-minute expiry Garmin gives FIS-B NEXRAD (AXIS Pilot's Guide for
# Experimental Aircraft, table 33).  DO-358B's 75 minutes is a retention
# minimum, not a display age.  Bill's ruling of 2026-11-18 (AER-5937).
NEXRAD_OLD_S = 30 * MIN

# Why a block was refused, for the counts.
REFUSED_MALFORMED = "malformed"
REFUSED_POLAR = "above 60N"
REFUSED_SOUTH = "southern hemisphere"
REFUSED_SCALE = "reserved scale"

# One decoded block.  north and west are the block's north-west corner and
# height and width its size, all in whole arcminutes; west is east-positive,
# -10800 to 10800.  bins is 128 intensities (bytes), or None for an empty
# block: radar coverage and no precipitation, whose level the two products
# do not define the same way.
Block = namedtuple(
    "Block",
    ["product_id", "block_number", "scale", "north", "west", "height", "width", "bins"],
)


def block_location(block_number, scale):
    """(north, west, height, width) in arcminutes for a northern-hemisphere
    block below 60N, or None if the block number or scale is out of range."""
    n = SCALES.get(scale)
    if n is None or not 0 <= block_number < BLOCK_THRESHOLD:
        return None
    row, col = divmod(block_number, BLOCKS_PER_RING)
    west = col * BLOCK_WIDTH
    if west >= 180 * 60:
        west -= 360 * 60
    north = (row + 1) * BLOCK_HEIGHT
    return north, west, BLOCK_HEIGHT * n, BLOCK_WIDTH * n


def bin_center(block, index):
    """(lat, lon) in degrees of the centre of bin index (0-127) of block."""
    row, col = divmod(index, COLS)
    lat = (block.north - (row + 0.5) * block.height / ROWS) / 60.0
    lon = (block.west + (col + 0.5) * block.width / COLS) / 60.0
    return lat, lon


def decode_bins(data):
    """128 intensities from the run-length bytes, or None unless the runs fill
    exactly 128 bins with exactly the bytes given."""
    out = bytearray()
    used = 0
    while used < len(data) and len(out) < BINS:
        b = data[used]
        out.extend(bytes([b & 0x07]) * ((b >> 3) + 1))
        used += 1
    if len(out) != BINS or used != len(data):
        return None
    return bytes(out)


def empty_offsets(data):
    """Grid steps east of the header block that an empty-block run marks
    empty, header block (0) included, or None if the bitmap is short."""
    if len(data) < 1:
        return None
    length = data[0] & 0x0F
    if len(data) != 1 + length:
        return None
    out = [0]
    for j in range(4):
        if data[0] & (0x10 << j):
            out.append(1 + j)
    for k in range(length):
        for j in range(8):
            if data[1 + k] & (1 << j):
                out.append(5 + 8 * k + j)
    return out


def parse_blocks(payload, product_id):
    """(blocks, refused) for one product-63 or -64 APDU payload: a list of
    Block, and None or the reason the payload was refused."""
    if len(payload) < 4:
        return [], REFUSED_MALFORMED
    rle = bool(payload[0] & 0x80)
    south = bool(payload[0] & 0x40)
    scale = (payload[0] >> 4) & 0x03
    number = ((payload[0] & 0x0F) << 16) | (payload[1] << 8) | payload[2]
    if south:
        return [], REFUSED_SOUTH
    if scale not in SCALES:
        return [], REFUSED_SCALE
    if number >= BLOCK_THRESHOLD:
        return [], REFUSED_POLAR
    if rle:
        bins = decode_bins(payload[3:])
        if bins is None:
            return [], REFUSED_MALFORMED
        north, west, height, width = block_location(number, scale)
        return [Block(product_id, number, scale, north, west, height, width, bins)], None
    offsets = empty_offsets(payload[3:])
    if offsets is None:
        return [], REFUSED_MALFORMED
    step = SCALES[scale]
    row, col = divmod(number, BLOCKS_PER_RING)
    blocks = []
    for k in offsets:
        # the run stays on the header block's row, across the 0E meridian
        bn = row * BLOCKS_PER_RING + (col + k * step) % BLOCKS_PER_RING
        north, west, height, width = block_location(bn, scale)
        blocks.append(Block(product_id, bn, scale, north, west, height, width, None))
    return blocks, None


def resolve_hhmm(hour, minute, rx):
    """The UTC time nearest rx with this hour and minute, or None if a field
    is out of range.  The APDU carries no date."""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    t = time.gmtime(rx)
    day = float(calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0)))
    cand = day + hour * 3600.0 + minute * 60.0
    if cand - rx > 43200.0:
        cand -= 86400.0
    elif rx - cand > 43200.0:
        cand += 86400.0
    return cand


def is_old(observed, now, old_s=NEXRAD_OLD_S):
    """True once the image is old_s past its observation time, or when it is
    dated so far after now that the clock must be wrong (its age cannot be
    known)."""
    return now - observed >= old_s or observed > now + weather.CLOCK_SLACK_S


# One mosaic cell: the latest block at a position, the observation time of
# the image it came from, and when it was first and last received.
Cell = namedtuple("Cell", ["block", "observed", "first_rx", "last_rx"])


class Mosaic:
    """The NEXRAD picture: the latest block at every position, per product.

    ingest() takes a decoded fisb.Uplink.  current() returns only blocks
    whose image is not old at the time given, each with its age, whether or
    not purge() has run; purge() drops old blocks and must be called
    periodically.  A block never replaces one from a newer image, and an
    empty block replaces a block with data from an older image: the rain has
    moved on.
    """

    def __init__(self, log=None, old_s=NEXRAD_OLD_S):
        self.log = log
        self.old_s = old_s
        self.cells = {}
        self.counts = {"apdus": 0, "blocks": 0, "stale": 0, "undated": 0}
        self.refused = {}
        self.refused_logged = set()

    def ingest(self, uplink, rx):
        for apdu in uplink.apdus:
            if apdu.product_id not in PRODUCTS:
                continue
            self.counts["apdus"] += 1
            # global block products are never segmented, and DO-358B sends
            # them with the hours-and-minutes time option
            observed = None
            if not apdu.segmented and apdu.t_opt == 0:
                observed = resolve_hhmm(apdu.hour, apdu.minute, rx)
            if observed is None:
                self.counts["undated"] += 1
                continue
            blocks, refused = parse_blocks(apdu.payload, apdu.product_id)
            if refused is not None:
                self._refuse(refused)
                continue
            if is_old(observed, rx, self.old_s):
                self.counts["stale"] += 1
                continue
            for block in blocks:
                self._put(block, observed, rx)

    def _refuse(self, reason):
        self.refused[reason] = self.refused.get(reason, 0) + 1
        if self.log is not None and reason not in self.refused_logged:
            self.refused_logged.add(reason)
            self.log.warning("FIS-B NEXRAD: refusing a block (%s)" % reason)

    def _put(self, block, observed, rx):
        key = (block.product_id, block.scale, block.block_number)
        old = self.cells.get(key)
        first_rx = rx
        if old is not None:
            if observed < old.observed:
                return  # never replace a newer image with an older one
            if observed == old.observed:
                first_rx = old.first_rx
        self.counts["blocks"] += 1
        self.cells[key] = Cell(block, observed, first_rx, rx)

    def purge(self, now):
        for key, cell in list(self.cells.items()):
            if is_old(cell.observed, now, self.old_s):
                del self.cells[key]

    def current(self, now, product_id=None):
        """[(Cell, age_s)] for every block whose image is not old at now; age
        is seconds since the image's observation time."""
        return [
            (c, now - c.observed)
            for c in self.cells.values()
            if (product_id is None or c.block.product_id == product_id)
            and not is_old(c.observed, now, self.old_s)
        ]

    def product_counts(self):
        out = {}
        for c in self.cells.values():
            name = PRODUCT_NAMES[c.block.product_id]
            out[name] = out.get(name, 0) + 1
        return out
