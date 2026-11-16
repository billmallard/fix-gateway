# FIS-B text products and the store that ages them.
#
# A text product is only as good as its age: a METAR shown as current an
# hour after it was observed is the hazard this module exists to prevent.
# So every product is dated from its own text (observation, issue, report or
# valid time), resolved against the receive clock, and its state -- current,
# future or old -- is computed when it is read, never stored.  A product whose
# text cannot be dated is not stored at all.
#
# The age limits below are Bill's ruling of 2026-11-16 (AER-5651 design
# note).  DO-358B gives retention minimums after last reception, not display
# ages.

import calendar
import re
import time
import zlib
from collections import namedtuple

from fixgw.plugins.stratux import fisb

MIN = 60.0

# METAR and SPECI: old one missed hourly report (plus a margin) after the
# observation, gone at two hours.
METAR_OLD_S = 75 * MIN
METAR_PURGE_S = 120 * MIN
# PIREP: old 90 minutes after the report time, gone at two hours.
PIREP_OLD_S = 90 * MIN
PIREP_PURGE_S = 120 * MIN
# TAF and winds aloft are old at the end of their valid (for-use) period, and
# kept marked old this long after it.
FORECAST_PURGE_S = 60 * MIN
# TWGO products (NOTAM, AIRMET, SIGMET, CWA) are dropped this long after they
# were last received: DO-358B's minimum retention after last reception.  The
# FIS-B-unavailable notice is refreshed every 10 minutes and dropped after 20.
TWGO_RETAIN_S = 60 * MIN
UNAVAILABLE_RETAIN_S = 20 * MIN
# A product dated this far after the receive clock means the clock is wrong
# (a Pi without a time source boots at its last shutdown).  Its age cannot be
# known, so it is old.
CLOCK_SLACK_S = 10 * MIN

CURRENT = "current"
FUTURE = "future"
OLD = "old"

# One stored product.  Times are UTC epoch seconds.  issued is the product's
# own time (observation, issue, report, or forecast valid time); old_at and
# valid_from may be None.
Product = namedtuple(
    "Product",
    [
        "key",
        "kind",  # METAR, SPECI, TAF, TAF.AMD, PIREP, WINDS, NOTAM-D, ...
        "station",  # "" for a product with no station (a TFR, a SIGMET)
        "text",
        "product_id",
        "issued",
        "valid_from",
        "old_at",
        "purge_at",
        "retain_s",  # drop this long after last_rx; None for dated products
        "first_rx",
        "last_rx",
    ],
)


def state(product, now):
    """current, future or old, at time now."""
    if product.issued is not None and product.issued > now + CLOCK_SLACK_S:
        return OLD
    if product.old_at is not None and now >= product.old_at:
        return OLD
    if product.valid_from is not None and now < product.valid_from:
        return FUTURE
    return CURRENT


def _utc(year, month, day, hour, minute):
    return float(calendar.timegm((year, month, day, hour, minute, 0)))


def resolve_ddhhmm(day, hour, minute, rx):
    """The UTC time nearest rx with this day of month, hour and minute.

    FIS-B text carries no month or year.  The candidates are that day in the
    month before, of and after rx; one that does not exist (31 in a 30-day
    month) is skipped.  None if no candidate exists or a field is out of range.
    """
    if not (1 <= day <= 31 and 0 <= hour <= 24 and 0 <= minute <= 59):
        return None
    t = time.gmtime(rx)
    best = None
    for dm in (-1, 0, 1):
        m = t.tm_mon + dm
        y = t.tm_year
        if m < 1:
            m += 12
            y -= 1
        elif m > 12:
            m -= 12
            y += 1
        if day > calendar.monthrange(y, m)[1]:
            continue
        # hour 24 is the end of the day (TAF valid periods use 2424)
        cand = _utc(y, m, day, 0, minute) + hour * 3600.0
        if best is None or abs(cand - rx) < abs(best - rx):
            best = cand
    return best


def _ddhhmm(s, rx):
    return resolve_ddhhmm(int(s[0:2]), int(s[2:4]), int(s[4:6]), rx)


def _ddhh(s, rx):
    return resolve_ddhhmm(int(s[0:2]), int(s[2:4]), 0, rx)


METAR_RE = re.compile(r"^(METAR|SPECI) ([0-9A-Z]{3,4}) ([0-9]{6})Z")
TAF_RE = re.compile(
    r"^(TAF|TAF\.AMD|TAF COR) ([0-9A-Z]{3,4}) (?:([0-9]{6})Z )?([0-9]{4})/([0-9]{4})"
)
# The FIS-B PIREP header is "PIREP <made-up id> ddhhmmZ <station> UA|UUA".
# The first id is the provider's mangling of the /OV field (fisb-decode
# documents the cases); the station after the time is the reporting location.
PIREP_RE = re.compile(r"^PIREP ([^ ]+) ([0-9]{6})Z ([^ ]+) (UA|UUA) ")
WINDS_RE = re.compile(r"^WINDS ([0-9A-Z]{3}) ([0-9]{6})Z")
NOTAM_TIMES_RE = re.compile(r"\b([0-9]{10})-([0-9]{10}|PERM)")
VALID_UNTIL_RE = re.compile(r"\bVALID UNTIL ([0-9]{6})Z?\b")
# AIRMET, SIGMET and CWA text opens "<kind> <office> ddhhmm", the issue time.
ISSUED_RE = re.compile(r"^[A-Z]+ [0-9A-Z]{3,4} ([0-9]{6})\b")

# Winds aloft: which forecast (6, 12 or 24 hours) a report is, from the APDU
# hour (the product's availability time) and the report's valid hour, and its
# for-use window around the valid time.  DO-358B table A-9, as tabulated by
# fisb-decode (msg413.py).
WINDS_AVAIL = {(1, 3): 0, (7, 9): 1, (13, 15): 2, (19, 21): 3}
WINDS_VALID = {6: 0, 12: 1, 18: 2, 0: 3}
WINDS_MATRIX = [[6, 12, None, 24], [24, 6, 12, None], [None, 24, 6, 12], [12, None, 24, 6]]
WINDS_USE = {6: (-4, 3), 12: (-3, 6), 24: (-6, 6)}
# Product availability time, hours before the valid time.
WINDS_AVAILABLE = {6: -4, 12: -10, 24: -22}


def winds_window(apdu_hour, valid_hour):
    """(available, start, end) offsets in hours from the valid time, or None
    if the pair is not one of the table's."""
    avail = None
    for (lo, hi), i in WINDS_AVAIL.items():
        if apdu_hour is not None and lo <= apdu_hour < hi:
            avail = i
    valid = WINDS_VALID.get(valid_hour)
    if avail is None or valid is None:
        return None
    hours = WINDS_MATRIX[avail][valid]
    if not hours:
        return None
    return (WINDS_AVAILABLE[hours],) + WINDS_USE[hours]


def _notam_time(s):
    # YYMMDDHHMM, UTC
    try:
        return _utc(2000 + int(s[0:2]), int(s[2:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]))
    except (ValueError, OverflowError):
        return None


def _key_hash(text):
    return "%08x" % zlib.crc32(text.encode("latin-1", "replace"))


def text_product(text, apdu, rx):
    """A Product from one product-413 report, or None if it cannot be dated."""
    text = fisb.clean_text(text)
    pid = fisb.PRODUCT_TEXT
    m = METAR_RE.match(text)
    if m:
        issued = _ddhhmm(m.group(3), rx)
        if issued is None:
            return None
        station = m.group(2)
        return Product(
            ("METAR", station), m.group(1), station, text, pid, issued, None,
            issued + METAR_OLD_S, issued + METAR_PURGE_S, None, rx, rx,
        )
    m = TAF_RE.match(text)
    if m:
        start = _ddhh(m.group(4), rx)
        end = _ddhh(m.group(5), rx)
        # Each end resolves to the nearest date, so a period across a month
        # end still comes out in order; one that does not is not datable.
        if start is None or end is None or end < start:
            return None
        # A TAF without an issue time (the naval air station form) is dated
        # by the start of its valid period.
        issued = _ddhhmm(m.group(3), rx) if m.group(3) else start
        if issued is None:
            return None
        station = m.group(2)
        return Product(
            ("TAF", station), m.group(1), station, text, pid, issued, None,
            end, end + FORECAST_PURGE_S, None, rx, rx,
        )
    m = PIREP_RE.match(text)
    if m:
        issued = _ddhhmm(m.group(2), rx)
        if issued is None:
            return None
        station = m.group(3)
        return Product(
            ("PIREP", station, issued, _key_hash(text)), "PIREP", station, text,
            pid, issued, None, issued + PIREP_OLD_S, issued + PIREP_PURGE_S,
            None, rx, rx,
        )
    m = WINDS_RE.match(text)
    if m:
        valid = _ddhhmm(m.group(2), rx)
        window = winds_window(apdu.hour, int(m.group(2)[2:4]))
        if valid is None or window is None:
            return None
        # Dated by its availability time: the valid time is hours ahead by
        # design, and would trip the clock check.
        station = m.group(1)
        available, start, end = (valid + h * 3600.0 for h in window)
        return Product(
            ("WINDS", station, valid), "WINDS", station, text, pid, available,
            start, end, end + FORECAST_PURGE_S, None, rx, rx,
        )
    return None


def twgo_kind(text):
    first = text.split(" ", 1)[0]
    return first if first else "TEXT"


def twgo_product(apdu, location, record, rx):
    """A Product from one active TWGO text record, or None if it has no text.

    NOTAMs (TFRs included) carry their effective period as
    YYMMDDHHMM-YYMMDDHHMM (or -PERM): a NOTAM is future before the start and
    old after the end.  AIRMET, SIGMET and CWA text carries its issue time
    and VALID UNTIL ddhhmm, and one without both is not stored.  A NOTAM with no period and
    the FIS-B-unavailable notice are current while they keep being received.
    Every TWGO product is dropped TWGO_RETAIN_S after its last reception.
    """
    text = fisb.clean_text(record.text)
    if not text:
        return None
    kind = twgo_kind(text)
    issued = valid_from = old_at = None
    if kind.startswith("NOTAM"):
        m = NOTAM_TIMES_RE.search(text)
        if m:
            valid_from = _notam_time(m.group(1))
            if m.group(2) != "PERM":
                old_at = _notam_time(m.group(2))
    elif kind != "FIS-B":
        # The issue time is what the clock check needs: without it a years-old
        # SIGMET resolves VALID UNTIL to the nearest month and reads current.
        m = ISSUED_RE.match(text)
        until = VALID_UNTIL_RE.search(text)
        if not m or not until:
            return None
        issued = _ddhhmm(m.group(1), rx)
        old_at = _ddhhmm(until.group(1), rx)
        if issued is None or old_at is None:
            return None
    retain = UNAVAILABLE_RETAIN_S if kind == "FIS-B" else TWGO_RETAIN_S
    purge_at = old_at + FORECAST_PURGE_S if old_at is not None else None
    key = ("TWGO", apdu.product_id, record.report_year, record.report_number, location)
    return Product(
        key, kind, location, text, apdu.product_id, issued, valid_from, old_at,
        purge_at, retain, rx, rx,
    )


class WeatherStore:
    """FIS-B text products keyed by station (and report, where a station has
    several), each with its receive times and expiry.

    ingest() takes a decoded Uplink; query with by_station() or products(),
    which compute each product's state at the time given.  purge() drops
    what has aged out and must be called periodically.
    """

    def __init__(self, log=None):
        self.log = log
        self.items = {}
        self.reassembler = fisb.Reassembler()
        self.counts = {"uplinks": 0, "apdus": 0, "undated": 0, "cancelled": 0}
        self.undated_kinds = set()

    def ingest(self, uplink, rx):
        self.counts["uplinks"] += 1
        for apdu in uplink.apdus:
            self.counts["apdus"] += 1
            if apdu.segmented:
                if apdu.product_id not in fisb.PRODUCTS_TWGO:
                    continue
                apdu = self.reassembler.add(apdu, rx)
                if apdu is None:
                    continue
            if apdu.product_id == fisb.PRODUCT_TEXT:
                for report in fisb.split_records(fisb.dlac_decode(apdu.payload)):
                    self._put(text_product(report, apdu, rx), report)
            elif apdu.product_id in fisb.PRODUCTS_TWGO and apdu.product_id != 13:
                # 13 (SUA status) was withdrawn from FIS-B; its schedules are
                # NOTAM-D now.
                twgo = fisb.parse_twgo(apdu.payload)
                if twgo is None:
                    continue
                for record in twgo.records:
                    key = ("TWGO", apdu.product_id, record.report_year,
                           record.report_number, twgo.location)
                    if not record.active:
                        self.counts["cancelled"] += 1
                        self.items.pop(key, None)
                    elif not record.text.strip():
                        # renewal with no text: the product is still sent
                        old = self.items.get(key)
                        if old is not None:
                            self.items[key] = old._replace(last_rx=rx)
                    else:
                        self._put(twgo_product(apdu, twgo.location, record, rx), record.text)

    def _put(self, product, text):
        if product is None:
            self.counts["undated"] += 1
            kind = text.strip().split(" ", 1)[0][:12]
            if self.log is not None and kind not in self.undated_kinds:
                self.undated_kinds.add(kind)
                self.log.warning("FIS-B: not storing a %r report that cannot be dated" % kind)
            return
        old = self.items.get(product.key)
        if old is not None:
            if old.issued is not None and product.issued is not None:
                if product.issued < old.issued:
                    return  # never replace a newer report with an older one
            product = product._replace(first_rx=old.first_rx)
        self.items[product.key] = product

    def purge(self, now):
        for key, p in list(self.items.items()):
            if p.purge_at is not None and now >= p.purge_at:
                del self.items[key]
            elif p.retain_s is not None and now - p.last_rx > p.retain_s:
                del self.items[key]
        self.reassembler.expire(now)

    def products(self, now):
        """Every stored product with its state at now: [(Product, state)]."""
        return [(p, state(p, now)) for p in self.items.values()]

    def by_station(self, station, now):
        return [(p, state(p, now)) for p in self.items.values() if p.station == station]

    def kind_counts(self):
        out = {}
        for p in self.items.values():
            out[p.kind] = out.get(p.kind, 0) + 1
        return out
