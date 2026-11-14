#!/usr/bin/env python

#  Copyright (c) 2014 Phil Birkelbach, Neil Domalik
#  Copyright (c) 2026 Bill Mallard — X-Plane key parity, receive-only default
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
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

"""FlightGear fix-gateway plugin.

Speaks FlightGear's *generic* protocol: a text sentence per update whose
fields are laid out by a protocol XML file that both programs read. The
``<output>`` section is what FlightGear sends us (FlightGear -> FIX); the
``<input>`` section is what we would send FlightGear (FIX -> FlightGear).
Each ``<chunk>``'s ``<name>`` starts with the FIX key, up to the first
``:``. The shipped file is ``connections/fgfs/fix_fgfs.xml`` and it writes
the same keys, in the same units and signs, as the ``xplane`` plugin, so
nothing downstream knows which simulator is running.

Two rules on top of FlightGear's own format, both needed because the
generic protocol can only scale and offset one property per field:

* Several chunks may name the same key. Their values are summed. This is
  how the XML builds the X-Plane TO/FROM encoding (``to + 2 * from``) and a
  magnetic VOR bearing (true bearing - magnetic variation).
* A key declared as a compass angle in the database (min 0, max 359.x) is
  wrapped into [0, 360) before it is written, so a sum such as 5 - 13 lands
  on 352 instead of being clamped to 0.

A sentence whose field count does not match the ``<output>`` section is
dropped whole. A mismatch means the two programs are reading different
XML files, and writing the fields we have would put values on the wrong
keys.

FIX -> FlightGear is OFF unless ``send: true``. It sends every ``<input>``
chunk at ``rate``, and a key nobody publishes still reads its initial
value: the X-Plane plugin's send path commanded the throttle closed the
first time it reached a real port.

One key, one writer: this plugin refuses to start when the ``xplane`` or
``demo`` plugin is also loaded, because all three write attitude, air data
and position and fix-gateway has no arbitration between them.
"""

import math
import os
import select
import socket
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict

import fixgw.plugin as plugin

# Plugins that write the same flight keys. Loading one of them alongside this
# plugin gives each key two writers, and the last write silently wins.
CONFLICTING_MODULES = ("fixgw.plugins.xplane", "fixgw.plugins.demo")

# FlightGear's named separators (src/Network/generic.cxx). Anything else in
# <var_separator> / <line_separator> is used literally.
NAMED_SEPARATORS = {
    "newline": "\n",
    "tab": "\t",
    "space": " ",
    "formfeed": "\f",
    "carriagereturn": "\r",
    "verticaltab": "\v",
}

# The XML shipped with the plugin, used when fg_root does not have it.
PACKAGED_PROTOCOL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "connections",
    "fgfs",
)

# A partial sentence longer than this has lost its line separator; drop it
# rather than grow the buffer forever.
MAX_BUFFER = 65536


class Chunk(object):
    def __init__(self, key, fmt="%.2f", node=""):
        self.key = key
        self.format = fmt
        self.node = node

    def __repr__(self):
        return "Chunk({!r}, {!r}, {!r})".format(self.key, self.format, self.node)


class Section(object):
    """One direction of a generic protocol: its chunks and separators."""

    def __init__(self, chunks=None, var_separator=",", line_separator="\n"):
        self.chunks = chunks or []
        self.var_separator = var_separator
        self.line_separator = line_separator

    @property
    def keys(self):
        return [c.key for c in self.chunks]


def _separator(element, tag, default):
    if element is None:
        return default
    text = element.findtext(tag)
    if text is None or text == "":
        return default
    return NAMED_SEPARATORS.get(text.strip(), text)


def _parse_section(element):
    section = Section(
        var_separator=_separator(element, "var_separator", ","),
        line_separator=_separator(element, "line_separator", "\n"),
    )
    if element is None:
        return section
    for chunk in element.findall("chunk"):
        name = chunk.findtext("name")
        if name is None:
            continue
        key = name.split(":")[0].strip().upper()
        if not key:
            continue
        fmt = (chunk.findtext("format") or "%.2f").strip()
        node = (chunk.findtext("node") or "").strip()
        section.chunks.append(Chunk(key, fmt, node))
    return section


def find_protocol_file(fg_root, xml_file):
    """Return the first existing path for xml_file: FG_ROOT/Protocol, FG_ROOT,
    then the copy shipped with the plugin."""
    root = os.path.expanduser(str(fg_root or ""))
    candidates = [
        os.path.join(root, "Protocol", xml_file),
        os.path.join(root, xml_file),
        os.path.join(PACKAGED_PROTOCOL_DIR, xml_file),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(
        "{} not found in any of: {}".format(xml_file, ", ".join(candidates))
    )


def parse_protocol_file(path):
    """Parse a generic protocol file into (output Section, input Section)."""
    root = ET.parse(path).getroot()
    if root.tag != "PropertyList":
        raise ValueError("Root Tag is not PropertyList")
    generic = root.find("generic")
    if generic is None:
        raise ValueError("No <generic> section in {}".format(path))
    if (generic.findtext("binary_mode") or "").strip().lower() in ("true", "1"):
        raise ValueError("binary_mode is not supported; use the text protocol")
    return _parse_section(generic.find("output")), _parse_section(generic.find("input"))


def _is_compass(item):
    """True when a database item is a compass angle: 0 up to just under 360."""
    try:
        return float(item.min) == 0.0 and 359.0 <= float(item.max) < 360.0
    except (AttributeError, TypeError, ValueError):
        return False


def decode_sentence(sentence, section):
    """Turn one received sentence into {key: value}.

    Returns None when the field count does not match the section (the whole
    sentence is suspect). Fields that are not finite numbers are left out,
    and so is any key one of whose summed fields was left out."""
    fields = sentence.split(section.var_separator)
    if len(fields) != len(section.chunks):
        return None
    sums = OrderedDict()
    invalid = set()
    for chunk, text in zip(section.chunks, fields):
        try:
            v = float(text)
        except ValueError:
            invalid.add(chunk.key)
            continue
        if not math.isfinite(v):
            invalid.add(chunk.key)
            continue
        sums[chunk.key] = sums.get(chunk.key, 0.0) + v
    for key in invalid:
        sums.pop(key, None)
    return sums


class MainThread(threading.Thread):
    def __init__(self, parent, output, input_):
        super(MainThread, self).__init__()
        self.daemon = True
        self.getout = False
        self.parent = parent
        self.log = parent.log
        cfg = parent.config
        self.output = output
        self.input = input_

        self.recv_host = cfg.get("recv_host", "localhost")
        self.recv_port = int(cfg.get("recv_port", 5500))
        self.send_host = cfg.get("send_host", "localhost")
        self.send_port = int(cfg.get("send_port", 5501))
        self.rate = float(cfg.get("rate", 10))
        self.send_enabled = bool(cfg.get("send", False)) and bool(self.input.chunks)

        self.msg_recv = 0
        self.msg_bad = 0
        self.msg_sent = 0
        self._buffer = ""
        self._compass = set()
        self._known = set()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.recv_host, self.recv_port))
        self.sock.setblocking(False)
        self.sock_closed = False

    def _db_item(self, key):
        try:
            return self.parent.db_get_item(key)
        except KeyError:
            return None

    def resolve_keys(self):
        """Check every protocol key against the database. Unknown receive keys
        are skipped; an unknown send key turns the send path off."""
        for key in dict.fromkeys(self.output.keys):
            item = self._db_item(key)
            if item is None:
                self.log.warning(
                    "fgfs: %s is in the protocol file but not in the database; "
                    "ignoring it", key)
                continue
            self._known.add(key)
            if _is_compass(item):
                self._compass.add(key)
        if self.send_enabled:
            missing = [k for k in self.input.keys if self._db_item(k) is None]
            if missing:
                self.log.critical(
                    "fgfs: send disabled: <input> keys not in the database: %s",
                    ", ".join(missing))
                self.send_enabled = False

    # ------------------------------------------------------------------
    # FlightGear -> FIX
    # ------------------------------------------------------------------
    def ingest(self, data):
        self._buffer += data.decode("utf-8", errors="replace")
        sep = self.output.line_separator
        while sep in self._buffer:
            sentence, self._buffer = self._buffer.split(sep, 1)
            if sentence.strip():
                self.save_sentence(sentence.strip("\r\n"))
        if len(self._buffer) > MAX_BUFFER:
            self.log.warning("fgfs: dropping %d bytes with no line separator",
                             len(self._buffer))
            self._buffer = ""

    def save_sentence(self, sentence):
        values = decode_sentence(sentence, self.output)
        if values is None:
            self.msg_bad += 1
            # Every sentence is bad while the files differ: log the first and
            # then one in a hundred, not one per update.
            if self.msg_bad % 100 == 1:
                self.log.warning(
                    "fgfs: dropped a sentence with %d fields, the protocol file "
                    "has %d. Are FlightGear and fix-gateway reading the same XML?",
                    len(sentence.split(self.output.var_separator)),
                    len(self.output.chunks))
            return
        self.msg_recv += 1
        for key, v in values.items():
            if key not in self._known:
                continue
            if key in self._compass:
                v = v % 360.0
            try:
                self.parent.db_write(key, v)
            except Exception as e:
                self.log.warning("fgfs: db_write %s=%r failed (%s)", key, v, e)

    # ------------------------------------------------------------------
    # FIX -> FlightGear
    # ------------------------------------------------------------------
    def build_input_sentence(self):
        fields = []
        for chunk in self.input.chunks:
            v = self.parent.db_read(chunk.key)
            if isinstance(v, tuple):
                v = v[0]
            try:
                fields.append(chunk.format % v)
            except (TypeError, ValueError):
                fields.append(chunk.format % float(v))
        return self.input.var_separator.join(fields) + self.input.line_separator

    def senddata(self):
        msg = self.build_input_sentence()
        try:
            self.sock.sendto(msg.encode("utf-8"), (self.send_host, self.send_port))
            self.msg_sent += 1
        except OSError as e:
            self.log.warning("fgfs: sendto failed (%s)", e)

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------
    def run(self):
        interval = 1.0 / self.rate if self.rate > 0 else 0.1
        next_send = time.monotonic() + interval
        while not self.getout:
            timeout = max(0.0, next_send - time.monotonic()) if self.send_enabled else 0.5
            try:
                ready, _, _ = select.select([self.sock], [], [], timeout)
            except (OSError, ValueError):
                break  # socket closed under us by stop()
            if ready:
                try:
                    data = self.sock.recv(65535)
                except OSError as e:
                    self.log.warning("fgfs: recv error (%s)", e)
                    data = b""
                if data:
                    self.ingest(data)
            # Wall clock, not loop passes: every received packet is a pass.
            if self.send_enabled and time.monotonic() >= next_send:
                self.senddata()
                next_send = time.monotonic() + interval

    def stop(self):
        self.getout = True

    def close(self):
        if not self.sock_closed:
            self.sock.close()
            self.sock_closed = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.thread = None
        self.protocol_path = None

    def conflicting_plugins(self):
        """Names of other loaded plugins that write the same flight keys."""
        server = sys.modules.get("fixgw.server")
        loaded = getattr(server, "plugins", None) or {}
        return sorted(
            name for name, p in loaded.items()
            if p is not self and type(p).__module__ in CONFLICTING_MODULES)

    def run(self):
        conflicts = self.conflicting_plugins()
        if conflicts:
            msg = ("fgfs: not starting: {} also loaded and writes the same "
                   "keys. Enable one simulator source at a time.".format(
                       ", ".join(conflicts)))
            self.log.critical(msg)
            raise plugin.PluginFail(msg)
        try:
            self.protocol_path = find_protocol_file(
                self.config.get("fg_root", ""), self.config.get("xml_file", "fix_fgfs.xml"))
            output, input_ = parse_protocol_file(self.protocol_path)
        except Exception as e:
            self.log.critical("fgfs: protocol file: %s", e)
            raise plugin.PluginFail(str(e))
        self.thread = MainThread(self, output, input_)
        self.thread.resolve_keys()
        self.log.info(
            "fgfs: %s; listening udp %s:%d (%d fields); send %s",
            self.protocol_path, self.thread.recv_host, self.thread.recv_port,
            len(output.chunks),
            "to {}:{}".format(self.thread.send_host, self.thread.send_port)
            if self.thread.send_enabled else "disabled")
        self.thread.start()

    def stop(self):
        if self.thread is None:
            return
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(2.0)
        if self.thread.is_alive():
            raise plugin.PluginFail
        self.thread.close()

    def get_status(self):
        d = OrderedDict()
        t = self.thread
        if t is None:
            return d
        d["Protocol file"] = self.protocol_path
        d["Listening on"] = "{}:{}".format(t.recv_host, t.recv_port)
        d["Sending to"] = (
            "{}:{}".format(t.send_host, t.send_port) if t.send_enabled else "disabled")
        d["Properties"] = OrderedDict(
            [("Receiving", len(t.output.chunks)), ("Sending", len(t.input.chunks))])
        d["Messages"] = OrderedDict(
            [("Received", t.msg_recv), ("Dropped", t.msg_bad), ("Sent", t.msg_sent)])
        return d
