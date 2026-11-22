#  SPDX-License-Identifier: GPL-2.0-or-later
#
# Garmin FlightPlan v1 (.fpl) reader and writer (AER-6321, BACKLOG EFB4).
#
# Built from Garmin's published schema only,
# https://www8.garmin.com/xmlschemas/FlightPlanv1.xsd (fetched 2026-11-22).
# Every restriction the schema states is checked on read; nothing beyond it is
# assumed. The reader is for an unauthenticated network input, so it refuses a
# document type declaration, entities and processing instructions outright,
# and is bounded in size, depth and element count before the schema checks.
"""Parse and write Garmin FlightPlan v1 documents.

``parse(data)`` returns a :class:`Plan` or raises :class:`FplError` with a
short reason. ``write(plan)`` returns the document as UTF-8 bytes, valid
against the schema.
"""

import math
import re
import xml.parsers.expat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.sax.saxutils import escape

NS = "http://www8.garmin.com/xmlschemas/FlightPlan/v1"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

#: The largest document read at all, before any parse.
MAX_BYTES = 131072
#: Route points the FIX route block holds (``f: 100``).
MAX_POINTS = 100
MAX_DEPTH = 12
MAX_ELEMENTS = 40000
MAX_TEXT = 4096

# Schema limits.
SCHEMA_WAYPOINTS = 3000
SCHEMA_ROUTE_POINTS = 300

#: WaypointType_t to the FIX slot type (FPLfTYPE: 0 unknown, 1 airport,
#: 2 VOR, 3 NDB, 4 fix, 5 user, 6 map point).
TYPE_TO_FIX = {
    "AIRPORT": 1,
    "VOR": 2,
    "NDB": 3,
    "INT": 4,
    "INT-VRP": 4,
    "USER WAYPOINT": 5,
}
#: The FIX slot type to WaypointType_t. Unknown and map points have no
#: Garmin type of their own and go out as user waypoints.
FIX_TO_TYPE = {0: "USER WAYPOINT", 1: "AIRPORT", 2: "VOR", 3: "NDB", 4: "INT",
               5: "USER WAYPOINT", 6: "USER WAYPOINT"}

_IDENT = re.compile(r"[A-Z0-9]{1,12}\Z")
_COUNTRY = re.compile(r"(?:[A-Z0-9]{2})?\Z")
_COMMENT = re.compile(r"(?:[A-Z0-9 /]{1,25})?\Z")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z")
_NONNEG = re.compile(r"\+?[0-9]+\Z")
# xsd:anyURI is any string that, once escaped, is an RFC 3986 reference: a
# colon before the first "/", "?" or "#" ends a scheme, which must be well
# formed. A namespace name is an unescaped URI, so its characters are bounded.
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z")
_NS_URI = re.compile(r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+\Z")
# The XML declaration, when present: version 1.0, and UTF-8 if it names one.
_XML_DECL = re.compile(
    rb"<\?xml[ \t\r\n]+version[ \t\r\n]*=[ \t\r\n]*(['\"])1\.0\1"
    rb"(?:[ \t\r\n]+encoding[ \t\r\n]*=[ \t\r\n]*(['\"])(?i:utf-?8)\2)?"
    rb"(?:[ \t\r\n]+standalone[ \t\r\n]*=[ \t\r\n]*(['\"])(?:yes|no)\3)?[ \t\r\n]*\?>")
#: The schema-instance attributes a validator reads; any other is refused.
_XSI_ATTRS = ("schemaLocation", "noNamespaceSchemaLocation")
_UTC = re.compile(r"(-?(?:[1-9][0-9]{4,}|[0-9]{4}))-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?Z\Z")


class FplError(ValueError):
    """A document this reader refuses. ``reason`` is short and plain."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Point:
    ident: str
    type: str          # WaypointType_t
    lat: float
    lon: float
    country: str = ""

    @property
    def fix_type(self):
        return TYPE_TO_FIX[self.type]


@dataclass
class Plan:
    name: str = ""
    points: list = field(default_factory=list)


# --------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------
class _Node:
    __slots__ = ("ns", "name", "attrs", "children", "text")

    def __init__(self, ns, name, attrs):
        self.ns = ns
        self.name = name
        self.attrs = attrs
        self.children = []
        self.text = []


def _split(qname):
    ns, _, local = qname.rpartition(" ")
    return ns, local


def _tree(data):
    root = []
    stack = []
    count = [0]

    def refuse(*_args):
        raise FplError("DTD OR ENTITY")

    def refuse_pi(*_args):
        # Legal XML, and nothing in a flight plan needs one.
        raise FplError("PROCESSING INSTRUCTION")

    def start(qname, attrs):
        count[0] += 1
        if count[0] > MAX_ELEMENTS:
            raise FplError("TOO MANY ELEMENTS")
        if len(stack) >= MAX_DEPTH:
            raise FplError("TOO DEEP")
        ns, name = _split(qname)
        node = _Node(ns, name, {_split(k): v for k, v in attrs.items()})
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_qname):
        stack.pop()

    def chars(text):
        if stack:
            node = stack[-1]
            node.text.append(text)
            if sum(len(t) for t in node.text) > MAX_TEXT:
                raise FplError("TEXT TOO LONG")

    def namespace(_prefix, uri):
        if uri is None or not _NS_URI.match(uri) or not _uri_ok(uri):
            raise FplError("BAD NAMESPACE")

    body = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    if re.match(rb"<\?xml[ \t\r\n?]", body) and not _XML_DECL.match(body):
        raise FplError("BAD XML DECLARATION")
    p = xml.parsers.expat.ParserCreate("UTF-8", namespace_separator=" ")
    p.StartNamespaceDeclHandler = namespace
    p.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    p.UnparsedEntityDeclHandler = refuse
    p.NotationDeclHandler = refuse
    p.ExternalEntityRefHandler = refuse
    p.SkippedEntityHandler = refuse
    p.ProcessingInstructionHandler = refuse_pi
    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = chars
    try:
        p.Parse(data, True)
    except xml.parsers.expat.ExpatError as e:
        raise FplError("NOT XML (line %d)" % e.lineno) from None
    return root[0]


def _text(node):
    if node.children:
        raise FplError("UNEXPECTED CHILD IN %s" % node.name.upper())
    return "".join(node.text)


def _attrs(node, allowed=()):
    for (ns, name) in node.attrs:
        if (ns == XSI and name in _XSI_ATTRS) or (ns == "" and name in allowed):
            continue
        raise FplError("UNEXPECTED ATTRIBUTE %s" % name.upper())


def _sequence(node, spec):
    """Match node's children, in order, against ``spec``: a list of
    ``(name, min, max)``. Returns {name: [child, ...]}. Text between child
    elements must be whitespace; ``extensions`` content is skipped."""
    if "".join(node.text).strip():
        raise FplError("TEXT IN %s" % node.name.upper())
    _attrs(node)
    found = {name: [] for name, _, _ in spec}
    children = node.children
    i = 0
    for name, lo, hi in spec:
        while i < len(children) and children[i].ns == NS and children[i].name == name:
            if len(found[name]) == hi:
                raise FplError("TOO MANY %s" % name.upper())
            found[name].append(children[i])
            i += 1
        if len(found[name]) < lo:
            raise FplError("MISSING %s" % name.upper())
    if i < len(children):
        c = children[i]
        raise FplError("UNEXPECTED %s" % (c.name.upper() if c.ns == NS else "ELEMENT"))
    for ext in found.get("extensions", []):
        _extensions(ext)
    return found


def _extensions(node):
    # Extensions_t: any element from another namespace, processed lax.
    if "".join(node.text).strip():
        raise FplError("TEXT IN EXTENSIONS")
    _attrs(node)
    for c in node.children:
        if c.ns in ("", NS):
            raise FplError("BAD EXTENSION")


def _one(found, name):
    return found[name][0] if found[name] else None


def _string(node):
    return _text(node) if node is not None else None


def _uri_ok(value):
    if "[" in value or "]" in value:
        # Brackets only around an IP literal host.
        m = re.match(r"[A-Za-z][A-Za-z0-9+.-]*://(?:[^/?#@]*@)?\[[0-9A-Fa-f:.]+\](?::[0-9]+)?(?=[/?#]|\Z)", value)
        if not m or "[" in value[m.end():] or "]" in value[m.end():]:
            return False
        return True
    head = re.split(r"[/?#]", value, maxsplit=1)[0]
    if ":" not in head:
        return True
    scheme, rest = value.split(":", 1)
    if not _SCHEME.match(scheme):
        return False
    if rest.startswith("//"):
        authority = re.split(r"[/?#]", rest[2:], maxsplit=1)[0]
        host = authority.rsplit("@", 1)[-1]
        if ":" in host.rsplit("]", 1)[-1]:
            return bool(re.fullmatch(r"[0-9]+", host.rsplit(":", 1)[1]))
    return True


def _uri(node):
    if node is None:
        return
    if not _uri_ok(" ".join(_text(node).split())):  # whiteSpace collapse
        raise FplError("BAD LINK")


def _match(regex, value, what):
    if not regex.match(value):
        raise FplError("BAD %s" % what)
    return value


def _decimal(node, lo, hi, what):
    value = _text(node).strip()  # whiteSpace collapse
    if not _DECIMAL.match(value):
        raise FplError("BAD %s" % what)
    number = float(value)
    if not lo <= number <= hi:
        raise FplError("BAD %s" % what)
    return number


def _utc(node):
    m = _UTC.match(_text(node).strip())
    if not m:
        raise FplError("BAD CREATED")
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year == 0 or not 1 <= month <= 12 or not 1 <= day <= days[month - 1] \
            or hour > 23 or minute > 59 or second > 59:
        raise FplError("BAD CREATED")


def _person(node):
    f = _sequence(node, [("author-name", 0, 1), ("email", 0, 1), ("link", 0, 1)])
    _string(_one(f, "author-name"))
    _uri(_one(f, "link"))
    email = _one(f, "email")
    if email is not None:
        if email.children or "".join(email.text).strip():
            raise FplError("BAD EMAIL")
        _attrs(email, allowed=("id", "domain"))
        if ("", "id") not in email.attrs or ("", "domain") not in email.attrs:
            raise FplError("BAD EMAIL")


def _waypoint(node):
    f = _sequence(node, [
        ("identifier", 1, 1), ("type", 1, 1), ("country-code", 1, 1),
        ("lat", 1, 1), ("lon", 1, 1), ("comment", 1, 1), ("elevation", 0, 1),
        ("waypoint-description", 0, 1), ("symbol", 0, 1), ("extensions", 0, 1),
    ])
    ident = _match(_IDENT, _text(_one(f, "identifier")), "IDENTIFIER")
    wtype = _text(_one(f, "type"))
    if wtype not in TYPE_TO_FIX:
        raise FplError("BAD TYPE")
    country = _match(_COUNTRY, _text(_one(f, "country-code")), "COUNTRY CODE")
    lat = _decimal(_one(f, "lat"), -90.0, 90.0, "LAT")
    lon = _decimal(_one(f, "lon"), -180.0, 180.0, "LON")
    _match(_COMMENT, _text(_one(f, "comment")), "COMMENT")
    if _one(f, "elevation") is not None:
        _decimal(_one(f, "elevation"), float("-inf"), float("inf"), "ELEVATION")
    _string(_one(f, "waypoint-description"))
    _string(_one(f, "symbol"))
    return Point(ident, wtype, lat, lon, country)


def _route_point(node):
    f = _sequence(node, [
        ("waypoint-identifier", 1, 1), ("waypoint-type", 1, 1),
        ("waypoint-country-code", 1, 1), ("extensions", 0, 1),
    ])
    ident = _match(_IDENT, _text(_one(f, "waypoint-identifier")), "IDENTIFIER")
    wtype = _text(_one(f, "waypoint-type"))
    if wtype not in TYPE_TO_FIX:
        raise FplError("BAD TYPE")
    country = _match(_COUNTRY, _text(_one(f, "waypoint-country-code")), "COUNTRY CODE")
    return (ident, wtype, country)


def parse(data, max_bytes=MAX_BYTES, max_points=MAX_POINTS):
    """The plan in a FlightPlan v1 document, or FplError.

    A document with no route is valid against the schema but carries no plan,
    so it is refused here (``NO ROUTE``), as is a route longer than the FIX
    route block (``TOO MANY POINTS``)."""
    if not isinstance(data, (bytes, bytearray)):
        raise FplError("NOT BYTES")
    if len(data) > max_bytes:
        raise FplError("TOO LARGE")
    if not data.strip():
        raise FplError("EMPTY")
    root = _tree(bytes(data))
    if root.ns != NS or root.name != "flight-plan":
        raise FplError("NOT A FLIGHT PLAN")
    top = _sequence(root, [
        ("file-description", 0, 1), ("author", 0, 1), ("link", 0, 1),
        ("created", 0, 1), ("waypoint-table", 1, 1), ("route", 0, 1),
        ("extensions", 0, 1),
    ])
    _string(_one(top, "file-description"))
    if _one(top, "author") is not None:
        _person(_one(top, "author"))
    _uri(_one(top, "link"))
    if _one(top, "created") is not None:
        _utc(_one(top, "created"))

    table = _sequence(_one(top, "waypoint-table"), [("waypoint", 1, SCHEMA_WAYPOINTS)])
    waypoints = {}
    for node in table["waypoint"]:
        wp = _waypoint(node)
        key = (wp.ident, wp.type, wp.country)
        if key in waypoints:   # xsd:key WaypointIdKey
            raise FplError("DUPLICATE WAYPOINT %s" % wp.ident)
        waypoints[key] = wp

    route_node = _one(top, "route")
    if route_node is None:
        raise FplError("NO ROUTE")
    route = _sequence(route_node, [
        ("route-name", 1, 1), ("route-description", 0, 1),
        ("flight-plan-index", 1, 1), ("route-point", 1, SCHEMA_ROUTE_POINTS),
        ("extensions", 0, 1),
    ])
    name = _match(_COMMENT, _text(_one(route, "route-name")), "ROUTE NAME")
    _string(_one(route, "route-description"))
    index = _text(_one(route, "flight-plan-index")).strip()
    if not _NONNEG.match(index) or not 1 <= int(index) < 99:
        raise FplError("BAD FLIGHT PLAN INDEX")
    points = []
    for node in route["route-point"]:
        key = _route_point(node)
        if key not in waypoints:   # xsd:keyref WaypointIdKeyRef
            raise FplError("UNKNOWN WAYPOINT %s" % key[0])
        points.append(waypoints[key])
    if len(points) > max_points:
        raise FplError("TOO MANY POINTS")
    return Plan(name=name, points=points)


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------
def clean_ident(text):
    """An identifier the schema accepts, from a FIX slot ident: upper case,
    letters and digits only, at most 12. Empty if nothing is left."""
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())[:12]


def clean_name(text):
    """A route name the schema accepts (``[A-Z0-9 /]{0,25}``)."""
    name = re.sub(r"[^A-Z0-9 /]", "", str(text).upper())
    return re.sub(r" +", " ", name).strip()[:25].strip()


#: Two points under one ident and type this close are one fix: the same VOR
#: from two sources differs in the 6th decimal (the bench's VTU, 0.1 m).
SAME_PLACE_NM = 0.1


def same_place(a, b):
    """True when two points name one fix: the same identifier and type, and
    within SAME_PLACE_NM of each other."""
    if (a.ident, a.type) != (b.ident, b.type):
        return False
    dlat = (a.lat - b.lat) * 60.0
    dlon = (a.lon - b.lon) * 60.0 * math.cos(math.radians((a.lat + b.lat) / 2.0))
    return math.hypot(dlat, dlon) < SAME_PLACE_NM


def _coord(value):
    text = "%.6f" % value
    return "0.000000" if text == "-0.000000" else text


def write(plan, created=None):
    """``plan`` as a FlightPlan v1 document, UTF-8 bytes.

    A route point that repeats an earlier waypoint's identifier, type and
    country code must be the same place (``same_place``), and is written at
    the first one's position: the schema keys the table on those three, so
    two places under one key cannot both be written (``FplError``).
    """
    if not plan.points:
        raise FplError("NO ROUTE")
    if len(plan.points) > SCHEMA_ROUTE_POINTS:
        raise FplError("TOO MANY POINTS")
    created = created or datetime.now(timezone.utc)
    table = {}
    for p in plan.points:
        if not _IDENT.match(p.ident) or p.type not in TYPE_TO_FIX or not _COUNTRY.match(p.country):
            raise FplError("BAD WAYPOINT %s" % p.ident)
        key = (p.ident, p.type, p.country)
        if key in table:
            if not same_place(table[key], p):
                raise FplError("TWO PLACES NAMED %s" % p.ident)
        else:
            table[key] = p
    name = clean_name(plan.name)
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<flight-plan xmlns="%s">' % NS,
           '  <created>%s</created>' % created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           '  <waypoint-table>']
    for p in table.values():
        out += ['    <waypoint>',
                '      <identifier>%s</identifier>' % escape(p.ident),
                '      <type>%s</type>' % escape(p.type),
                '      <country-code>%s</country-code>' % escape(p.country),
                '      <lat>%s</lat>' % _coord(p.lat),
                '      <lon>%s</lon>' % _coord(p.lon),
                '      <comment />',
                '    </waypoint>']
    out += ['  </waypoint-table>',
            '  <route>',
            '    <route-name>%s</route-name>' % escape(name),
            '    <flight-plan-index>1</flight-plan-index>']
    for p in plan.points:
        p = table[(p.ident, p.type, p.country)]
        out += ['    <route-point>',
                '      <waypoint-identifier>%s</waypoint-identifier>' % escape(p.ident),
                '      <waypoint-type>%s</waypoint-type>' % escape(p.type),
                '      <waypoint-country-code>%s</waypoint-country-code>' % escape(p.country),
                '    </route-point>']
    out += ['  </route>', '</flight-plan>', '']
    return "\n".join(out).encode("utf-8")
