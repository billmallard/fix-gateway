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

"""One Tessera page from a YAML file: what each key shows, what a press writes.

Every key is drawn as a gauge tile with an empty ring: its centre text is the
FIX value (or fixed text) and its label sits underneath.  A key with an `icon`
is drawn as that icon instead, and never changes.  The firmware updates
a tile's label, colour and gauge text in place, but rebuilds the whole grid
when anything else changes, which drops a press that is waiting for feedback
(deck_model.c same_structure, ui.cpp build_keys).  So a refresh only ever
changes those fields: a bad value changes the text and colour, never the
tile's shape.
"""

import yaml

from . import protocol

_KEY_FIELDS = {
    "id",
    "label",
    "show",
    "format",
    "enum",
    "text",
    "press",
    "color",
    "confirm",
    "icon",
}

# The names the firmware draws as a symbol (ui.cpp fallback_icon, the same in
# 0.9.0 and 1.2.12).  It draws any other name as a tick, so a typo is refused
# here rather than shown.
ICONS = frozenset(
    {
        "ok",
        "close",
        "settings",
        "home",
        "folder",
        "download",
        "refresh",
        "play",
        "pause",
        "stop",
        "plus",
        "minus",
        "loop",
        "list",
        "upload",
        "bolt",
        "bell",
        "wifi",
        "trash",
        "edit",
    }
)

VALUE_COLOR = "#FFFFFF"
OLD_COLOR = "#FFB000"  # a stale value: still shown, in amber
BAD_COLOR = "#FF3030"  # bad or failed: dashes, in red
BAD_TEXT = "----"


class PageError(ValueError):
    """The page file is not a page this plugin can show."""


class Key:
    def __init__(self, entry):
        unknown = set(entry) - _KEY_FIELDS
        if unknown:
            raise PageError(
                "key {}: unknown field(s) {}".format(entry.get("id"), sorted(unknown))
            )
        self.id = entry.get("id")
        if type(self.id) is not int or not 1 <= self.id <= protocol.MAX_KEYS:
            raise PageError(
                "key id {!r}: must be 1 to {}".format(self.id, protocol.MAX_KEYS)
            )
        self.label = entry.get("label")
        if not isinstance(self.label, str) or not self.label:
            raise PageError("key {}: needs a label".format(self.id))
        if len(self.label) > protocol.LABEL_MAX:
            raise PageError(
                "key {}: label over {} characters".format(self.id, protocol.LABEL_MAX)
            )
        self.show = entry.get("show")
        self.format = entry.get("format", "{}")
        self.text = str(entry.get("text", ""))
        if self.show is not None and "text" in entry:
            raise PageError("key {}: show and text are exclusive".format(self.id))
        self.enum = _enum(self.id, entry)
        self.icon = _icon(self.id, entry)
        self.color = entry.get("color", VALUE_COLOR)
        # Passed to the panel as given: the panel, not this plugin, is the
        # judge of a colour (an INVALID answer is handled like any other).
        self.confirm = bool(entry.get("confirm", False))
        self.press = _press(self.id, entry.get("press"))

    def keys(self):
        """Every FIX key this page key names."""
        named = [self.show] if self.show else []
        if self.press:
            named.append(self.press[1])
        return named

    def tile(self, read):
        """The key's layout entry, the shown value read through `read`."""
        if self.icon:
            return {
                "id": self.id,
                "label": self.label,
                "color": self.color,
                "icon": self.icon,
            }
        text, color = self.text, self.color
        if self.show:
            value, _annunciate, old, bad, fail = read(self.show)[:5]
            if bad or fail:
                text, color = BAD_TEXT, BAD_COLOR
            else:
                text = self._text(value)
                if old:
                    color = OLD_COLOR
        tile = {
            "id": self.id,
            "label": self.label,
            "color": color,
            "gauge": {"p": 0, "t": text[: protocol.GAUGE_TEXT_MAX]},
        }
        if self.confirm:
            tile["cf"] = 1
        return tile

    def _text(self, value):
        if self.enum is not None:
            # A value is named by its nearest whole number, so a float key
            # such as NAVSRC (1.0) finds its name; one with no name falls
            # through to `format`.
            try:
                return self.enum[int(round(float(value)))]
            except (KeyError, ValueError, TypeError, OverflowError):
                pass
        try:
            return self.format.format(value)
        except (ValueError, TypeError):
            return str(value)

    def writes(self):
        """The (key, value) writes one press makes, in order."""
        if not self.press:
            return []
        kind, key, value = self.press
        if kind == "pulse":
            # A button press as pyEfis and the Knobster write it: True then
            # False, so a pyEfis button bound to the key acts once.
            return [(key, True), (key, False)]
        return [(key, value)]


def _enum(key_id, entry):
    """`enum: {0: VLOC1, ...}`: the text for each whole-number value of
    `show`, as pyEfis's annunciator lamps name NAVSRC."""
    enum = entry.get("enum")
    if enum is None:
        return None
    if entry.get("show") is None:
        raise PageError("key {}: enum needs show".format(key_id))
    if not isinstance(enum, dict) or not enum or not all(
        type(v) is int and isinstance(t, str) for v, t in enum.items()
    ):
        raise PageError("key {}: enum maps whole numbers to text".format(key_id))
    return dict(enum)


def _icon(key_id, entry):
    """`icon: settings`: the key shows a symbol instead of a value.  The
    firmware draws a key's icon only when it has no gauge (ui.cpp
    build_keys), so an icon key has no `show` or `text`."""
    icon = entry.get("icon")
    if icon is None:
        return None
    if icon not in ICONS:
        raise PageError(
            "key {}: icon {!r} is not one of {}".format(key_id, icon, sorted(ICONS))
        )
    if "show" in entry or "text" in entry:
        raise PageError("key {}: icon excludes show and text".format(key_id))
    return icon


def _press(key_id, press):
    if press is None:
        return None
    if not isinstance(press, dict):
        raise PageError("key {}: press must be a mapping".format(key_id))
    if set(press) == {"pulse"} and isinstance(press["pulse"], str):
        return ("pulse", press["pulse"], None)
    if set(press) == {"set", "value"} and isinstance(press["set"], str):
        return ("set", press["set"], press["value"])
    raise PageError(
        "key {}: press is {{pulse: KEY}} or {{set: KEY, value: V}}".format(key_id)
    )


class Page:
    def __init__(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            raise PageError("a page is a mapping with a 'keys' list")
        self.name = str(data.get("name", "TESSERA"))
        self.keys = [
            Key(entry if isinstance(entry, dict) else {}) for entry in data["keys"]
        ]
        if len(self.keys) > protocol.MAX_KEYS:
            raise PageError("more than {} keys".format(protocol.MAX_KEYS))
        ids = [k.id for k in self.keys]
        if len(set(ids)) != len(ids):
            raise PageError("duplicate key ids")
        self.by_id = {k.id: k for k in self.keys}

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(yaml.safe_load(f))

    def named_keys(self):
        """Every FIX key the page file names, shown or written."""
        return sorted({key for k in self.keys for key in k.keys()})

    def shown_keys(self):
        return sorted({k.show for k in self.keys if k.show})

    def written_keys(self):
        return sorted({k.press[1] for k in self.keys if k.press})

    def pulsed_keys(self):
        return sorted(
            {k.press[1] for k in self.keys if k.press and k.press[0] == "pulse"}
        )

    def buttons(self, read):
        return [k.tile(read) for k in self.keys]

    def writes(self, key_id):
        key = self.by_id.get(key_id)
        return key.writes() if key else []
