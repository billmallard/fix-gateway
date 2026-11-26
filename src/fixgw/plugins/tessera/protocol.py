#  Copyright (c) 2026 MakerPlane
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

"""The Tessera panel's serial line protocol, as far as this plugin uses it.

Source: github.com/catorendal-a11y/tessera-deck at 5ab2486e (firmware 0.9.0,
protocol 4): docs/PROTOCOL.md, main/main.cpp, main/deck_model.c and the
reference host's host/panel_serial.py.  Constants name the firmware symbol
they mirror.
"""

import json

# The GUITION JC1060P470C, under the two ids Tessera's builds report for it:
# `tessera-p4` for an ESP32-P4 chip below revision v3, `tessera-p4-v3` for a
# v3.x chip (TESS2's board 1 is a v3.2).  Tessera's own host/panel_serial.py
# (v1.2.12) lists four more ids, for other boards with other screens; the page
# here is laid out for this board's 1024x600 grid, so those stay refused.
BOARD_IDS = frozenset({"tessera-p4", "tessera-p4-v3"})
# BACKLOG TESS: accept protocol 4 only.  Protocol 4 is the firmware this
# plugin was written against; an older one lacks fields we may use later and
# a newer one is unknown.
SUPPORTED_PROTOCOLS = (4,)
BAUD = 115200

HANDSHAKE_SECONDS = 2.5  # panel_serial.py HANDSHAKE_SECONDS
PING_INTERVAL = 1.0  # PROTOCOL.md: AD_PING every second; NO PC after 3.5 s
CFG_ACK_SECONDS = 4.0  # PROTOCOL.md Layout: resend once after 4 s
CFG_CHUNK = 380  # characters per AD_CFG_DATA line
LAYOUT_MAX = 8192  # main.cpp LAYOUT_MAX: bytes of one layout
LINE_MAX = 1099  # main.cpp line[1100]: a longer line is dropped unanswered
MAX_KEYS = 18  # deck_model.h DECK_MAX_BUTTONS
LABEL_MAX = 24  # deck_model.h DECK_BUTTON_LABEL_MAX
GAUGE_TEXT_MAX = 7  # deck_model.h DECK_GAUGE_TEXT_MAX
COLUMNS = 6  # ui.cpp build_keys: 6 x 3 tiles of 138 x 160 px

GET_INFO = '{"cmd":"get_info"}'
PING = "AD_PING"
CFG_BEGIN = "AD_CFG_BEGIN"
CFG_END = "AD_CFG_END"

# The panel's reset reasons that mean it crashed or lost power.
CRASH_RESETS = {"panic", "int_wdt", "task_wdt", "wdt", "brownout"}


class Rejected(Exception):
    """The port answered, but not as a panel this plugin supports."""


def check_info(info):
    """Raise Rejected unless `info` is a supported panel's get_info answer."""
    if info.get("boardId") not in BOARD_IDS:
        raise Rejected("a different device (board {!r})".format(info.get("boardId")))
    protocol = info.get("protocol")
    if type(protocol) is not int or protocol not in SUPPORTED_PROTOCOLS:
        raise Rejected(
            "firmware protocol {!r}; this plugin speaks {}".format(
                protocol, ", ".join(str(p) for p in SUPPORTED_PROTOCOLS)
            )
        )


def layout_payload(buttons):
    """The JSON the panel parses at AD_CFG_END, as the reference host builds it."""
    return json.dumps({"buttons": buttons}, ensure_ascii=True, separators=(",", ":"))


def layout_lines(payload):
    """Every line of one layout push, newline not included."""
    lines = [CFG_BEGIN]
    for i in range(0, len(payload), CFG_CHUNK):
        lines.append("AD_CFG_DATA " + payload[i : i + CFG_CHUNK])
    lines.append(CFG_END)
    return lines


def feedback(button_id, ok):
    return "AD_FEEDBACK {} {}".format("ok" if ok else "alert", button_id)


def parse_line(line):
    """A panel line as a dict for JSON events, else None (text answers)."""
    if not line.startswith("{"):
        return None
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


def wire_seconds(n_bytes, baud=BAUD):
    """Time to clock n_bytes through a real 8N1 UART at baud."""
    return n_bytes * 10.0 / baud
