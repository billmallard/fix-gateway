"""A scripted Tessera panel: a serial peer on a pty that follows the firmware.

Behaviour is taken from tessera-deck 5ab2486e: docs/PROTOCOL.md for the
messages, main/main.cpp usb_console_task for the line handling, and
main/deck_model.c for layout validation (deck_model_buttons_from_json), the
in-place versus rebuild rule (same_structure) and press events
(record_event).  Each rule cites what it mirrors, so a reader can check it.

The plugin opens a stable symlink, so `unplug()` and `plug()` can swap the
pty underneath it the way a USB re-enumeration swaps the device node.
"""

import json
import os
import re
import select
import threading
import time
import tty

BOARD_ID = "tessera-p4"
LINE_MAX = 1099  # main.cpp: line[1100]
LAYOUT_MAX = 8192  # main.cpp: LAYOUT_MAX
MAX_KEYS = 18  # deck_model.h: DECK_MAX_BUTTONS
LABEL_MAX = 24  # deck_model.h: DECK_BUTTON_LABEL_MAX
GAUGE_TEXT_MAX = 7  # deck_model.h: DECK_GAUGE_TEXT_MAX
_HEX = re.compile(r"^#?[0-9A-Fa-f]{6}$")  # parse_color_hex accepts an optional '#'
# Fields deck_model.c same_structure ignores: they change in place.
LIVE_FIELDS = {"label", "color", "sub", "progress"}


def _valid_color(value):
    return isinstance(value, str) and bool(_HEX.match(value))


def parse_layout(text):
    """deck_model_buttons_from_json: the key list, or None for INVALID."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    buttons = data.get("buttons") if isinstance(data, dict) else None
    if not isinstance(buttons, list) or len(buttons) > MAX_KEYS:
        return None
    seen = set()
    keys = []
    for item in buttons:
        if not isinstance(item, dict):
            return None
        key_id, label = item.get("id"), item.get("label")
        if (
            isinstance(key_id, bool)
            or not isinstance(key_id, (int, float))
            or not 1 <= key_id <= MAX_KEYS
            or key_id != int(key_id)
            or not isinstance(label, str)
            or not label
            or int(key_id) in seen
        ):
            return None
        seen.add(int(key_id))
        if "color" in item and not _valid_color(item["color"]):
            return None
        gauge = item.get("gauge")
        if isinstance(gauge, dict):
            if not isinstance(gauge.get("p"), (int, float)) or not isinstance(
                gauge.get("t"), str
            ):
                return None
        for style in ("bg", "text_color", "border"):
            if style in item and not _valid_color(item[style]):
                return None
        key = dict(item)
        key["id"] = int(key_id)
        key["label"] = label[:LABEL_MAX]  # copy_text truncates
        if isinstance(gauge, dict):
            key["gauge"] = dict(gauge, t=gauge["t"][:GAUGE_TEXT_MAX])
        keys.append(key)
    return keys


def structure(keys):
    """What same_structure compares: everything but the live fields."""
    shaped = []
    for key in keys:
        shape = {k: v for k, v in key.items() if k not in LIVE_FIELDS}
        if "gauge" in shape:
            shape["gauge"] = {
                k: v for k, v in shape["gauge"].items() if k not in ("p", "t")
            }
        shaped.append(shape)
    return shaped


class ScriptedPanel:
    def __init__(
        self,
        link_path,
        boot_id=1234,
        board=BOARD_ID,
        protocol=4,
        reset="poweron",
        layout_max=LAYOUT_MAX,
    ):
        self.link_path = link_path
        self.boot_id = boot_id
        self.board = board
        self.protocol = protocol
        self.reset = reset
        self.layout_max = layout_max
        # Script knobs
        self.silent = False  # answer nothing at all (a hung panel, or another device)
        self.ignore_cfg_end = 0  # swallow this many AD_CFG_END lines unanswered
        self.press_before_info = (
            None  # a key id pressed just before the next info answer
        )
        # Records
        self.received = []  # (monotonic time, line)
        self.layout = None  # the keys the panel shows
        self.layouts_accepted = 0
        self.rebuilds = 0  # accepted layouts that changed structure (a full redraw)
        self.feedback = []  # (kind, id)
        self.answers = []  # every line the panel sent
        self._event_id = 0
        self._cfg = None
        self._cfg_too_big = False
        self._master = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.plug()

    # -- the cable -------------------------------------------------------

    def plug(self):
        master, slave = os.openpty()
        tty.setraw(slave)
        path = os.ttyname(slave)
        # Held open: with no slave fd open, a read on the master fails (EIO)
        # before the plugin has opened the port.
        self._slave = slave
        if os.path.lexists(self.link_path):
            os.unlink(self.link_path)
        os.symlink(path, self.link_path)
        self._master = master
        self._rx = bytearray()
        self._overflow = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def unplug(self):
        self._stop.set()
        self._thread.join(2)
        os.close(self._master)
        os.close(self._slave)
        self._master = None
        os.unlink(self.link_path)

    def reboot(self, boot_id, reset="sw", replug=True):
        """A restart: new bootId, the layout reverts to the saved one."""
        if replug:
            self.unplug()
        self.boot_id, self.reset = boot_id, reset
        self._event_id = 0  # deck_events_emitted is per boot
        if replug:
            self.plug()

    def close(self):
        if self._master is not None:
            self.unplug()

    # -- the panel -------------------------------------------------------

    def send(self, line):
        self.answers.append(line)
        os.write(self._master, (line + "\n").encode())

    def press(self, button, hold=False):
        """record_event: a press on a key not in the layout makes no event."""
        with self._lock:
            key = next((k for k in self.layout or [] if k["id"] == button), None)
            if key is None:
                return False
            self._event_id += 1
            event = {
                "type": "deck_event",
                "id": self._event_id,
                "button": button,
                "label": key["label"],
            }
            if hold:
                event["hold"] = True
            self.send(json.dumps(event, separators=(",", ":")))
            return True

    def _run(self):
        while not self._stop.is_set():
            ready, _, _ = select.select([self._master], [], [], 0.02)
            if not ready:
                continue
            try:
                data = os.read(self._master, 4096)
            except OSError:
                return
            for ch in data:
                if ch == 0x0D:
                    continue
                if ch != 0x0A:
                    if len(self._rx) < LINE_MAX:
                        self._rx.append(ch)
                    else:
                        self._overflow = True
                    continue
                line, overflow = self._rx.decode("utf-8", "replace"), self._overflow
                self._rx, self._overflow = bytearray(), False
                if not overflow:  # main.cpp drops an overlong line unanswered
                    with self._lock:
                        self.received.append((time.monotonic(), line))
                        if not self.silent:
                            self._line(line)

    def _line(self, line):
        if line == '{"cmd":"get_info"}':
            if self.press_before_info is not None:
                # A press left over from before this get_info, sent ahead of the answer.
                self._event_id += 1
                self.send(
                    json.dumps(
                        {
                            "type": "deck_event",
                            "id": self._event_id,
                            "button": self.press_before_info,
                            "label": "OLD",
                        }
                    )
                )
                self.press_before_info = None
            self.send(
                json.dumps(
                    {
                        "type": "info",
                        "boardId": self.board,
                        "firmwareVersion": "0.9.0",
                        "protocol": self.protocol,
                        "bootId": self.boot_id,
                        "uptime": 5,
                        "heap": 100000,
                        "psramFree": 1000000,
                        "reset": self.reset,
                        "ota": 1,
                        "layoutSaved": 0,
                    },
                    separators=(",", ":"),
                )
            )
        elif line == "AD_CFG_BEGIN":
            self._cfg, self._cfg_too_big = "", False
            self.send("AD_CFG_BEGIN OK")
        elif line.startswith("AD_CFG_DATA ") and self._cfg is not None:
            chunk = line[12:]
            if len(self._cfg) + len(chunk) > self.layout_max:
                self._cfg, self._cfg_too_big = None, True
            else:
                self._cfg += chunk
        elif line == "AD_CFG_END":
            if self.ignore_cfg_end > 0:
                self.ignore_cfg_end -= 1
                self._cfg = None
                return
            keys = parse_layout(self._cfg) if self._cfg is not None else None
            if keys is not None:
                if self.layout is None or structure(keys) != structure(self.layout):
                    self.rebuilds += 1
                self.layout = keys
                self.layouts_accepted += 1
                self.send("AD_CFG_END OK {}".format(len(keys)))
            elif self._cfg_too_big:
                self.send("AD_CFG_END TOO_BIG {}".format(self.layout_max))
            else:
                self.send("AD_CFG_END INVALID")
            self._cfg, self._cfg_too_big = None, False
        elif line.startswith("AD_FEEDBACK "):
            parts = line[12:].split()
            if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) > 0:
                self.feedback.append((parts[0], int(parts[1])))
                self.send("AD_FEEDBACK OK")
            else:
                self.send("AD_FEEDBACK INVALID")
        # AD_PING has no answer; any other line is ignored here.

    # -- what the tests read ---------------------------------------------

    def lines(self, prefix=""):
        with self._lock:
            return [(t, line) for t, line in self.received if line.startswith(prefix)]
