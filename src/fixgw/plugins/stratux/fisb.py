# FIS-B wire decode: the GDL90 Uplink message (0x07) down to text records.
#
# Layers, outermost first:
#   GDL90 0x07 (G§3.3): ID, 3-byte time of reception, 432-byte UAT uplink.
#   UAT ground uplink (DO-282B): 8-byte header (ground station position,
#     application-data-valid flag), then 424 bytes of information frames.
#   Information frame: 9-bit length, 4-bit type; type 0 is a FIS-B APDU.
#   FIS-B APDU header (DO-267A, as amended by DO-358B): product id, time,
#     optional segmentation block.
#   Payload: product 413 is DLAC text (METAR, SPECI, TAF, PIREP, WINDS);
#     products 8-17 are TWGO (text with graphic overlay) records, of which
#     this module decodes the text records.  Graphic records are GDL3.
#
# The bit layouts follow three independent open decoders that agree on every
# field used here: dump978 (uat_decode.c), Stratux (uatparse.go) and
# fisb-decode (rand-projects, written against DO-358B).  The golden tests run
# real 2015 RF captures (dump978's sample-data) and compare every text against
# fisb-decode's output.

from collections import namedtuple

UPLINK = 0x07
UPLINK_LEN = 432
UAT_HEADER_LEN = 8

FRAME_FISB = 0

PRODUCT_TEXT = 413
# TWGO products (DO-358B): 8 NOTAM-D, -FDC, -TFR and FIS-B unavailable;
# 11 AIRMET; 12 SIGMET and convective SIGMET; 13 SUA; 14 G-AIRMET
# (graphics only); 15 CWA; 16 NOTAM-TRA; 17 NOTAM-TMOA.
PRODUCTS_TWGO = (8, 11, 12, 13, 14, 15, 16, 17)

RECORD_TEXT = 2
RECORD_GRAPHIC = 8

# Six-bit DLAC.  Code 0 is ETX, 27 NC (null), 28 TAB (the next code is a
# count of spaces), 29 RS (record separator), 30 LF, 31 the change-cipher
# code, which FIS-B does not use.  ETX, NC and RS are returned as themselves
# so a caller can split on them.
ETX = "\x03"
NC = "\x1a"
RS = "\x1e"
DLAC = ETX + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + NC + "\t" + RS + "\n|" + \
    " !\"#$%&'()*+,-./0123456789:;<=>?"
DLAC_TAB = 28


def dlac_decode(data: bytes):
    """Text of DLAC-packed bytes: four 6-bit codes in every three bytes."""
    out = []
    tab = False
    bits = 0
    nbits = 0
    for b in data:
        bits = (bits << 8) | b
        nbits += 8
        while nbits >= 6:
            nbits -= 6
            code = (bits >> nbits) & 0x3F
            if tab:
                out.append(" " * code)
                tab = False
            elif code == DLAC_TAB:
                tab = True
            else:
                out.append(DLAC[code])
        bits &= (1 << nbits) - 1
    return "".join(out)


def split_records(text):
    """Records of DLAC text: RS separates records, ETX ends the text, and NC
    is padding.  Empty records are dropped."""
    out = []
    for part in text.replace(ETX, RS).split(RS):
        part = part.replace(NC, "")
        if part.strip():
            out.append(part)
    return out


def clean_text(text):
    """FAA text with the trailing whitespace of every line removed, and no
    trailing newline."""
    return "\n".join(line.rstrip() for line in text.rstrip().split("\n"))


# One FIS-B APDU.  month and day are None unless the time option sends them
# (t_opt 2); seconds are not sent under DO-358B.  The segment fields are None
# unless the S flag is set.
Apdu = namedtuple(
    "Apdu",
    [
        "product_id",
        "t_opt",
        "month",
        "day",
        "hour",
        "minute",
        "segmented",
        "file_id",  # product file id: links the segments of one file
        "file_length",  # how many segments make up the file
        "apdu_number",  # this segment's place in the file, from 1
        "payload",
    ],
)


def parse_apdu(frame: bytes):
    """An Apdu from the data of a type-0 information frame, or None if it is
    too short or uses a time option this decoder does not handle.

    The header is a bit field: 3 flag bits (reserved under DO-358B), the
    11-bit product id, the S flag, the 2-bit time option, an optional 9-bit
    month and day, the 11-bit hours and minutes, and, when S is set, the
    28-bit segmentation block (10-bit file id, 9-bit file length, 9-bit
    APDU number).  The payload starts at the next whole byte.

    DO-267A gave t_opt 1 and 3 seconds; DO-358B dropped seconds, and FIS-B
    sends only 0 and 2.  The two standards disagree on the other two, so they
    are refused rather than guessed.
    """
    if len(frame) < 4:
        return None
    # the longest header is 65 bits
    bits = int.from_bytes(frame[:9].ljust(9, b"\0"), "big")
    total = 72

    def take(pos, n):
        return (bits >> (total - pos - n)) & ((1 << n) - 1)

    product_id = take(3, 11)
    segmented = bool(take(14, 1))
    t_opt = take(15, 2)
    if t_opt not in (0, 2):
        return None
    pos = 17
    month = day = None
    if t_opt == 2:
        month = take(pos, 4)
        day = take(pos + 4, 5)
        pos += 9
    hour = take(pos, 5)
    minute = take(pos + 5, 6)
    pos += 11
    file_id = file_length = apdu_number = None
    if segmented:
        file_id = take(pos, 10)
        file_length = take(pos + 10, 9)
        apdu_number = take(pos + 19, 9)
        pos += 28
    start = (pos + 7) // 8
    if start > len(frame):
        return None
    return Apdu(
        product_id=product_id,
        t_opt=t_opt,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        segmented=segmented,
        file_id=file_id,
        file_length=file_length,
        apdu_number=apdu_number,
        payload=bytes(frame[start:]),
    )


# The ground station and APDUs of one UAT uplink.  frames counts every
# information frame, FIS-B or not.
Uplink = namedtuple("Uplink", ["lat", "lon", "position_valid", "frames", "apdus"])


def parse_uat_uplink(data: bytes):
    """An Uplink from the 432-byte UAT ground uplink payload, or None if it is
    short.  An uplink whose application data is not valid has no frames."""
    if len(data) < UPLINK_LEN:
        return None
    raw_lat = (data[0] << 15) | (data[1] << 7) | (data[2] >> 1)
    raw_lon = ((data[2] & 0x01) << 23) | (data[3] << 15) | (data[4] << 7) | (
        data[5] >> 1
    )
    lat = raw_lat * 360.0 / (1 << 24)
    if lat > 90:
        lat -= 180
    lon = raw_lon * 360.0 / (1 << 24)
    if lon > 180:
        lon -= 360
    position_valid = bool(data[5] & 0x01)
    apdus = []
    frames = 0
    if data[6] & 0x20:  # application data valid
        app = data[UAT_HEADER_LEN:UPLINK_LEN]
        pos = 0
        while pos + 2 <= len(app):
            length = (app[pos] << 1) | (app[pos + 1] >> 7)
            ftype = app[pos + 1] & 0x0F
            if length == 0 and ftype == 0:
                break  # the rest is fill
            if pos + 2 + length > len(app):
                break  # overrun: a corrupt length
            frames += 1
            if ftype == FRAME_FISB:
                apdu = parse_apdu(app[pos + 2 : pos + 2 + length])
                if apdu is not None:
                    apdus.append(apdu)
            pos += 2 + length
    return Uplink(lat, lon, position_valid, frames, apdus)


def parse_uplink_message(msg: bytes):
    """An Uplink from a GDL90 Uplink message (ID 0x07), or None.

    G§3.3: the ID, a 3-byte time of reception (80 ns units within the
    second; not a date, so the receive time is the host's clock), then the
    432-byte UAT uplink payload.
    """
    if len(msg) < 4 + UPLINK_LEN or msg[0] != UPLINK:
        return None
    return parse_uat_uplink(msg[4 : 4 + UPLINK_LEN])


# One TWGO text record.  active is the report status bit: False is a
# cancellation, which carries no text.
TextRecord = namedtuple(
    "TextRecord", ["report_number", "report_year", "active", "text"]
)

# The TWGO payload header and its text records.  location is the DLAC
# location identifier (an airport for NOTAM-D, blank for most others).
Twgo = namedtuple("Twgo", ["record_format", "location", "records"])

TWGO_HEADER_LEN = 6


def parse_twgo(payload: bytes):
    """A Twgo from a TWGO payload, or None if it is malformed.  Graphic
    records are not decoded (records is empty for them)."""
    if len(payload) < TWGO_HEADER_LEN:
        return None
    record_format = payload[0] >> 4
    count = payload[1] >> 4
    location = dlac_decode(payload[2:5]).replace(ETX, "").replace(NC, "").strip()
    records = []
    if record_format == RECORD_TEXT:
        pos = TWGO_HEADER_LEN
        for _ in range(count):
            if pos + 5 > len(payload):
                return None
            length = (payload[pos] << 8) | payload[pos + 1]
            if length < 5 or pos + length > len(payload):
                return None
            number = (payload[pos + 2] << 6) | (payload[pos + 3] >> 2)
            year = ((payload[pos + 3] & 0x03) << 5) | (payload[pos + 4] >> 3)
            active = bool(payload[pos + 4] & 0x04)
            text = ""
            if active:
                text = "".join(
                    split_records(dlac_decode(payload[pos + 5 : pos + length]))
                )
            records.append(TextRecord(number, year, active, text))
            pos += length
    return Twgo(record_format, location, records)


class Reassembler:
    """Joins segmented TWGO APDUs into one payload.

    Every segment repeats the 6-byte TWGO header; the first keeps it and the
    rest drop it (DO-358B; fisb-decode's Unsegmenter).  A file is keyed by
    product id and product file id.  A file that has not completed within
    expire_s of its first segment is dropped: a missed segment must not join
    an old file to a new one with the same id.
    """

    def __init__(self, expire_s=600.0):
        self.expire_s = expire_s
        self.pending = {}
        self.dropped = 0

    def add(self, apdu, now):
        """The joined Apdu (segmented False) when apdu completes its file,
        else None."""
        self.expire(now)
        n = apdu.file_length
        i = apdu.apdu_number
        if not n or not i or i > n:
            self.dropped += 1
            return None
        key = (apdu.product_id, apdu.file_id)
        entry = self.pending.get(key)
        if entry is None or entry[1] != n:
            entry = (now, n, [None] * n)
            self.pending[key] = entry
        parts = entry[2]
        parts[i - 1] = apdu
        if any(p is None for p in parts):
            return None
        del self.pending[key]
        payload = parts[0].payload + b"".join(
            p.payload[TWGO_HEADER_LEN:] for p in parts[1:]
        )
        return parts[0]._replace(
            segmented=False,
            file_id=None,
            file_length=None,
            apdu_number=None,
            payload=payload,
        )

    def expire(self, now):
        for key in [k for k, v in self.pending.items() if now - v[0] > self.expire_s]:
            del self.pending[key]
            self.dropped += 1
