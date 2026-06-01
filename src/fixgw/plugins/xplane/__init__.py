#!/usr/bin/env python

#  Copyright (c) 2014 Phil Birkelbach
#  Copyright (c) 2026 Bill Mallard — data-driven rewrite
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

"""X-Plane fix-gateway plugin.

Speaks X-Plane's UDP "DATA" packet protocol used by
``Settings → Network → Data Output → Send network data output via UDP``.
Each packet carries a 5-byte header (``"DATA\\0"``) followed by 36-byte
rows: an int32 index plus eight float32 slots. The slot layout per
index is defined by X-Plane; this plugin maps row index -> list of
eight FIX keys via YAML, so adding a new index means a single config
edit, not a code patch.

Config schema (``connections/xplane.yaml``):

    xplane:
        load: XPLANE
        module: fixgw.plugins.xplane
        ipaddress: 127.0.0.1     # X-Plane host (for outgoing DREF writes)
        udp_in:  49001           # local UDP port we receive on
        udp_out: 49002           # X-Plane UDP port we send to
        send_interval: 0.1       # seconds between FIX -> X-Plane bursts

        # X-Plane -> FIX. Eight slots per index. ``_`` / ``x`` / blank skip.
        recv:
            3:  [IAS,   _,   TAS, GS,   _, _, _, _]      # speeds
            4:  [_,     _,   VS,  _,    _, _, _, _]      # mach/VVI/g
            17: [PITCH, ROLL, _,  HEAD, _, _, _, _]      # pitch/roll/headings
            18: [AOA,   _,   TRACK, _,  _, _, _, _]      # AOA/sideslip/paths
            20: [LAT,   LONG, ALT, AGL, _, _, _, _]      # position

        # FIX -> X-Plane. Same format. (Optional; omit for read-only.)
        send:
            25: [THR1, _, _, _, _, _, _, _]              # throttle command

Legacy ``idxN: KEY1,KEY2,...`` entries at the top level are still
honoured and treated as ``send`` mappings (matching the old plugin's
behaviour).
"""
import threading
import socket
import select
import struct

import fixgw.plugin as plugin


# X-Plane packet framing.
HEADER = b"DATA"
HEADER_LEN = 5                 # "DATA" + one reserved byte
ROW_SIZE = 36                  # i32 index + 8 * f32
SLOTS_PER_ROW = 8

# X-Plane's "no value" sentinel float (negative NaN-ish). Lifted from
# the original plugin so existing X-Plane UI / aircraft code that
# checks for "this slot intentionally not sent" still works.
NO_VALUE = b"\x00\xc0\x79\xc4"

# X-Plane fills disabled slots in a data row with -999.0. Writing that
# value into the FIX database would publish a bogus reading downstream
# (e.g. MAGVAR=-999 silently turning HEAD into HEAD+999 wherever it's
# used as a true-heading correction). Anything magnitude-comparable
# to the sentinel is treated as "no data" and skipped.
NO_VALUE_SENTINEL = -999.0
NO_VALUE_TOL = 0.5


def _normalise_slot(value):
    """Return the FIX key for a slot, or None when it should be skipped."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("_", "x"):
        return None
    return s.upper()


def _parse_slot_list(value):
    """Accept either a YAML list or the legacy comma-separated string."""
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [s for s in str(value).split(",")]
    items = (items + [None] * SLOTS_PER_ROW)[:SLOTS_PER_ROW]
    return [_normalise_slot(s) for s in items]


def _build_index_map(spec):
    """Turn a {idx: slot_list} dict (with int or str keys) into
    {int: [8 keys or None]}."""
    out = {}
    if not isinstance(spec, dict):
        return out
    for raw_key, raw_val in spec.items():
        try:
            idx = int(raw_key)
        except (TypeError, ValueError):
            continue
        out[idx] = _parse_slot_list(raw_val)
    return out


def _legacy_send_map(config):
    """Top-level ``idxN`` entries in the legacy schema meant "send to
    X-Plane at index N". Promote them into the new send_map shape."""
    legacy = {}
    for k, v in config.items():
        ks = str(k)
        if ks.lower().startswith("idx") and ks[3:].isdigit():
            legacy[int(ks[3:])] = v
    return _build_index_map(legacy)


class MainThread(threading.Thread):
    def __init__(self, parent):
        super().__init__()
        self.getout = False
        self.parent = parent
        self.log = parent.log
        cfg = parent.config

        self.xplane_ip = cfg.get("ipaddress", "127.0.0.1")
        self.udp_in = int(cfg.get("udp_in", 49001))
        self.udp_out = int(cfg.get("udp_out", 49002))
        self.send_interval = float(cfg.get("send_interval", 0.1))

        self.recv_map = _build_index_map(cfg.get("recv", {}))
        self.send_map = _build_index_map(cfg.get("send", {}))
        if not self.send_map:
            self.send_map = _legacy_send_map(cfg)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", self.udp_in))
        self.sock.setblocking(False)

        self.log.info(
            "xplane: listening udp:%d, dest %s:%d; recv indices=%s, send indices=%s",
            self.udp_in, self.xplane_ip, self.udp_out,
            sorted(self.recv_map.keys()), sorted(self.send_map.keys()))

    # ------------------------------------------------------------------
    # X-Plane -> FIX
    # ------------------------------------------------------------------
    def writedata(self, index, values):
        slots = self.recv_map.get(index)
        if not slots:
            self.log.debug("xplane: unmapped index %d", index)
            return
        for slot_i, key in enumerate(slots):
            if key is None or slot_i >= len(values):
                continue
            v = float(values[slot_i])
            # Skip X-Plane's "this slot is disabled" sentinel — don't
            # let -999 reach the FIX database where it would corrupt
            # any downstream consumer that takes the value at face
            # value (e.g. ``HEAD - MAGVAR`` for true-heading correction).
            if abs(v - NO_VALUE_SENTINEL) < NO_VALUE_TOL:
                continue
            try:
                self.parent.db_write(key, v)
            except Exception as e:
                self.log.warning(
                    "xplane: db_write %s=%r failed (%s)", key, v, e)

    def _ingest_packet(self, data):
        if len(data) < HEADER_LEN or data[:4] != HEADER:
            self.log.error("xplane: bad header on packet length %d", len(data))
            return
        body = data[HEADER_LEN:]
        if len(body) % ROW_SIZE != 0:
            self.log.error(
                "xplane: bad packet length %d (body %d, row %d)",
                len(data), len(body), ROW_SIZE)
            return
        for r in range(len(body) // ROW_SIZE):
            base = r * ROW_SIZE
            idx = struct.unpack_from("<i", body, base)[0]
            slots = struct.unpack_from("<8f", body, base + 4)
            self.writedata(idx, list(slots))

    # ------------------------------------------------------------------
    # FIX -> X-Plane
    # ------------------------------------------------------------------
    def senddata(self):
        for index, slots in self.send_map.items():
            buf = HEADER + b"\x00" + struct.pack("<i", int(index))
            for slot_i in range(SLOTS_PER_ROW):
                key = slots[slot_i] if slot_i < len(slots) else None
                if key is None:
                    buf += NO_VALUE
                else:
                    try:
                        v = float(self.parent.db_read(key)[0])
                    except (TypeError, IndexError, ValueError):
                        # db_read can return a scalar in some plugin builds —
                        # accept that too.
                        try:
                            v = float(self.parent.db_read(key))
                        except Exception:
                            v = 0.0
                    except Exception:
                        v = 0.0
                    buf += struct.pack("<f", v)
            try:
                self.sock.sendto(buf, (self.xplane_ip, self.udp_out))
            except OSError as e:
                self.log.warning("xplane: sendto failed (%s)", e)

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------
    def run(self):
        while not self.getout:
            ready, _, _ = select.select(
                [self.sock], [], [], self.send_interval)
            if ready:
                try:
                    data, _addr = self.sock.recvfrom(4096)
                except OSError as e:
                    self.log.warning("xplane: recv error (%s)", e)
                    continue
                self._ingest_packet(data)
            if self.send_map:
                self.senddata()

    def stop(self):
        self.getout = True
        try:
            self.sock.close()
        except Exception:
            pass


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super().__init__(name, config, config_meta)
        self.thread = MainThread(self)

    def run(self):
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(2.0)
        if self.thread.is_alive():
            raise plugin.PluginFail
