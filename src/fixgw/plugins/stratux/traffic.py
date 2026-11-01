# ADS-B traffic writer: GDL90 Heartbeat (0x00), Ownship (0x0A) and Traffic
# (0x14) reports into the TFC key block (config/database/traffic.yaml).
#
# The contract is pyEfis docs/fix_dynamic_list_spec.md section 4 (pyEfis
# #344, AER-4336); this implements it (AER-4579, BACKLOG P3a).  In short:
#
# - Slot commit.  Every update writes the slot's fields, then TFCkTGT last;
#   TFCkTGT is the slot's commit key and -1 means empty.  A slot is emptied
#   (TGT = -1, written first) before it is filled for another target, so a
#   reader never pairs one target's identity with another's fields (2.6).
# - Eviction runs on a monotonic deadline from tick(), which the plugin calls
#   on every socket timeout as well as after every datagram, so a target is
#   evicted after evict_s even when nothing arrives (2.7, 4.6).
# - A source with no Heartbeat for 3 s is failed: fail on TFCCOUNT and on
#   every occupied TGT, cleared by the next Heartbeat (4.7).
# - Ownship is removed by identity only -- the configured ownship_address and
#   the 0x0A report -- never by geometry (Bill's F6 ruling, 4.8).
#
# No threat logic: the source's own alert bit is passed through as TFCkALRT.

import time

from fixgw import geo
from fixgw.plugins.stratux import gdl90

EMPTY = -1
HEARTBEAT_TIMEOUT = 3.0
TICK_INTERVAL = 1.0
MAX_SLOTS = 1000  # bounds the TFCnTGT probe, far above any sane k

# Slot fields in write order; TGT is not here, it is always written last.
FIELDS = (
    "ADDR",
    "ATYP",
    "LAT",
    "LON",
    "ALT",
    "GS",
    "VS",
    "TRK",
    "TTYP",
    "EXTR",
    "AIR",
    "NIC",
    "NACP",
    "CAT",
    "CS",
    "ALRT",
    "EMRG",
)


def identity(report):
    return (report.atype << 24) | report.address


# (value, bad) for each field, from a decoded report.  A field the source marks
# unavailable is written as 0 with bad set; the display dashes it.
def field_values(r):
    return {
        "ADDR": (r.address, False),
        "ATYP": (r.atype, False),
        "LAT": (0.0 if r.pos_bad else r.lat, r.pos_bad),
        "LON": (0.0 if r.pos_bad else r.lon, r.pos_bad),
        "ALT": (0.0 if r.alt_bad else r.alt, r.alt_bad),
        "GS": (0.0 if r.gs_bad else r.gs, r.gs_bad),
        "VS": (0.0 if r.vs_bad else r.vs, r.vs_bad),
        "TRK": (0.0 if r.trk_bad else r.trk, r.trk_bad),
        "TTYP": (r.ttype, False),
        "EXTR": (r.extrapolated, False),
        "AIR": (r.airborne, False),
        "NIC": (r.nic, False),
        "NACP": (r.nacp, False),
        "CAT": (r.cat, False),
        "CS": (r.callsign, False),
        "ALRT": (r.alert, False),
        "EMRG": (r.emergency, False),
    }


# The 24-bit address from the plugin's ownship_address setting: hex, with or
# without 0x.  It must be a quoted string in the YAML -- an unquoted 123456
# loads as a decimal int and 0xA1B2C3 as a hex one, and the two cannot be told
# apart afterwards -- so anything else is refused, loudly, and only the 0x0A
# identity is used.
def parse_ownship_address(value, log):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        log.error(
            "ownship_address must be a quoted hex string, got {!r}; "
            "ignoring it".format(value)
        )
        return None
    try:
        address = int(value, 16)
    except ValueError:
        address = -1
    if not 0 <= address <= 0xFFFFFF:
        log.error("ownship_address {!r} is not a 24-bit hex address".format(value))
        return None
    return address


class Slot(object):
    def __init__(self, n):
        self.n = n
        self.ident = EMPTY
        self.seen = 0.0  # monotonic time of the last report
        self.lat = None  # last valid position, for the block-full rule
        self.lon = None


class TrafficWriter(object):
    def __init__(self, get_item, log, evict_s=10.0, ownship_address=None, clock=None):
        self.log = log
        self.clock = clock or time.monotonic
        self.evict_s = float(evict_s)
        self.ownship_config = ownship_address
        self.ownship_report = None  # (atype, address) from the last 0x0A
        self._mismatch = None  # the disagreeing pair last logged
        self._get = get_item
        self.slots = []
        n = 1
        while n <= MAX_SLOTS:
            try:
                get_item("TFC{}TGT".format(n))
            except KeyError:
                break
            self.slots.append(Slot(n))
            n += 1
        self.by_ident = {}
        self.dropped = set()  # identities that got no slot since the last tick
        self.failed = False
        now = self.clock()
        self.last_heartbeat = now
        self.next_tick = now

    def _item(self, n, field):
        return self._get("TFC{}{}".format(n, field))

    # -- input ---------------------------------------------------------------

    def handle(self, msg):
        if not msg:
            return
        if msg[0] == gdl90.HEARTBEAT and len(msg) == 7:
            self.heartbeat()
        elif msg[0] == gdl90.OWNSHIP:
            r = gdl90.parse_report(msg)
            if r is not None:
                self.ownship(r)
        elif msg[0] == gdl90.TRAFFIC:
            r = gdl90.parse_report(msg)
            if r is not None:
                self.report(r)

    def heartbeat(self):
        self.last_heartbeat = self.clock()
        if self.failed:
            self._set_failed(False)

    def ownship(self, r):
        self.ownship_report = (r.atype, r.address)
        self._check_mismatch()

    def _check_mismatch(self):
        pair = None
        if self.ownship_config is not None and self.ownship_report is not None:
            atype, address = self.ownship_report
            if atype in (0, 2) and address != self.ownship_config:
                pair = (self.ownship_config, address)
        if pair is not None and pair != self._mismatch:
            self.log.warning(
                "ownship_address {:06X} disagrees with the receiver's ownship "
                "report {:06X}; filtering both".format(*pair)
            )
        self._mismatch = pair

    def is_ownship(self, r):
        icao = set()
        if self.ownship_config is not None:
            icao.add(self.ownship_config)
        if self.ownship_report is not None:
            atype, address = self.ownship_report
            if atype in (0, 2):
                icao.add(address)
            elif atype == 1 and r.atype == 1 and r.address == address:
                return True
        return r.atype in (0, 2) and r.address in icao

    def report(self, r):
        if self.is_ownship(r):
            return
        ident = identity(r)
        now = self.clock()
        slot = self.by_ident.get(ident)
        if slot is None:
            slot = self._allocate(r)
            if slot is None:
                self.dropped.add(ident)
                return
        slot.seen = now
        if r.pos_bad:
            slot.lat = slot.lon = None
        else:
            slot.lat, slot.lon = r.lat, r.lon
        self._fill(slot, ident, r)

    # -- slots ---------------------------------------------------------------

    def _allocate(self, r):
        for slot in self.slots:
            if slot.ident == EMPTY:
                return slot
        # Block full: keep the nearest (G§2.3).  Needs a valid ownship
        # position and a valid newcomer position; otherwise first come, first
        # kept.  An occupied slot with no valid position counts as farthest.
        here = self._ownship_position()
        if here is None or r.pos_bad:
            return None
        new = geo.distance_nm(here[0], here[1], r.lat, r.lon)
        far, far_d = None, -1.0
        for slot in self.slots:
            d = (
                float("inf")
                if slot.lat is None
                else geo.distance_nm(here[0], here[1], slot.lat, slot.lon)
            )
            if d > far_d:
                far, far_d = slot, d
        if new >= far_d:
            return None
        self._evict(far)
        return far

    def _ownship_position(self):
        try:
            lat = self._get("LAT").value
            lon = self._get("LONG").value
        except KeyError:
            return None
        for v in (lat, lon):
            if v[2] or v[3] or v[4]:  # old, bad, fail
                return None
        return lat[0], lon[0]

    def _fill(self, slot, ident, r):
        for field, (value, bad) in field_values(r).items():
            self._item(slot.n, field).set(value, bad=bad)
        # The commit, last.  Rewritten on every report so its age is the
        # slot's age.
        self._item(slot.n, "TGT").set(ident, bad=False, fail=self.failed)
        slot.ident = ident
        self.by_ident[ident] = slot

    def _evict(self, slot):
        # TGT first; the fields are left alone and mean nothing while it is -1.
        self._item(slot.n, "TGT").set(EMPTY, bad=False, fail=False)
        del self.by_ident[slot.ident]
        slot.ident = EMPTY
        slot.lat = slot.lon = None

    def _set_failed(self, failed):
        self.failed = failed
        for slot in self.slots:
            if slot.ident != EMPTY:
                # The flag only: a write would refresh TGT's age and hide a
                # stale slot.
                self._item(slot.n, "TGT").fail = failed
        self._get("TFCCOUNT").fail = failed

    # -- periodic ------------------------------------------------------------

    def tick(self):
        now = self.clock()
        if now < self.next_tick:
            return
        self.next_tick = now + TICK_INTERVAL
        if not self.failed and now - self.last_heartbeat > HEARTBEAT_TIMEOUT:
            self.log.warning(
                "no GDL90 Heartbeat for {:.0f} s; traffic source failed".format(
                    now - self.last_heartbeat
                )
            )
            self._set_failed(True)
        for slot in self.slots:
            if slot.ident != EMPTY and now - slot.seen >= self.evict_s:
                self._evict(slot)
        count = sum(1 for slot in self.slots if slot.ident != EMPTY)
        self._get("TFCCOUNT").set(count, bad=False, fail=self.failed)
        self._get("TFCDROP").set(len(self.dropped), bad=False, fail=False)
        self.dropped = set()
