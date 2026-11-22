"""The Garmin FlightPlan v1 reader and writer (AER-6321, BACKLOG EFB4).

The schema is Garmin's published FlightPlanv1.xsd, kept in data/ (see the
README there). What the writer produces is validated against it with lxml,
and the reader is checked against it both ways: every mutation the schema
refuses, the reader refuses, and every document the reader accepts, the
schema accepts, once the ForeFlight departures Bill ruled in (AER-6321,
2026-11-22) are undone by ``foreflight_to_schema``.
"""

import glob
import os
import random
import re

import pytest

from fixgw.plugins.fpl_exchange import fpl

DATA = os.path.join(os.path.dirname(__file__), "data")
HANDMADE = os.path.join(DATA, "handmade_full.fpl")
FOREFLIGHT = sorted(glob.glob(os.path.join(DATA, "foreflight", "*.fpl")))

if os.environ.get("CI"):
    import lxml.etree  # noqa: F401  CI installs it; a skip there would hide the schema checks
etree = pytest.importorskip("lxml.etree")
SCHEMA = etree.XMLSchema(etree.parse(os.path.join(DATA, "FlightPlanv1.xsd")))

#: Refusals the reader adds to the schema's: no plan to post, more than the
#: route block holds, and XML that libxml2 tolerates (a processing
#: instruction, a declaration such as version="1.").
EXTRA_REFUSALS = {"NO ROUTE", "TOO MANY POINTS", "PROCESSING INSTRUCTION", "BAD XML DECLARATION"}


def schema_ok(data):
    try:
        doc = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return False
    return SCHEMA.validate(doc)


G = "{%s}" % fpl.NS


def foreflight_to_schema(data):
    """The ForeFlight departures (Bill, 2026-11-22, AER-6321), undone with
    lxml, independently of the reader, so the schema stays the oracle."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            data = data.decode("utf-16").encode("utf-8")
        except UnicodeError:
            return data
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError:
        return data
    for name in fpl.FOREFLIGHT_TOP:
        for e in root.findall(G + name):
            root.remove(e)

    def ensure(parent, before, name):
        if parent.find(G + name) is None and parent.find(G + before) is not None:
            parent.find(G + before).addnext(etree.Element(G + name))

    created = root.find(G + "created")
    if created is not None and created.text:
        created.text = re.sub(r"\A(\d{4})(\d{2})(\d{2})T", r"\1-\2-\3T", created.text.strip())
    for wp in root.findall("%swaypoint-table/%swaypoint" % (G, G)):
        for e in wp.findall(G + "altitude-ft"):
            wp.remove(e)
        ensure(wp, "type", "country-code")
        ensure(wp, "lon", "comment")
    for rp in root.findall("%sroute/%sroute-point" % (G, G)):
        ensure(rp, "waypoint-type", "waypoint-country-code")
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def accepted_ok(data):
    """What the reader may accept: schema-valid once ForeFlight's departures are undone."""
    return schema_ok(data) or schema_ok(foreflight_to_schema(data))


def reader(data):
    try:
        return fpl.parse(data)
    except fpl.FplError as e:
        return e.reason


def handmade():
    with open(HANDMADE, "rb") as f:
        return f.read()


def plan(*points, name="TEST"):
    return fpl.Plan(name, [fpl.Point(*p) for p in points])


ROUTE = plan(("KSBA", "AIRPORT", 34.426211, -119.840372, "K2"),
             ("RZS", "VOR", 34.5095, -119.770833, "K2"),
             ("AVOLS", "INT", 34.7, -120.3, "K2"),
             ("USR1", "USER WAYPOINT", 34.85, -120.42),
             ("KSMX", "AIRPORT", 34.898917, -120.457583, "K2"),
             name="KSBA TO KSMX")


# --- the writer ---------------------------------------------------------------


def test_written_plan_is_valid_against_the_schema():
    data = fpl.write(ROUTE)
    assert schema_ok(data), SCHEMA.error_log


def test_round_trip_is_exact():
    first = fpl.write(ROUTE)
    back = fpl.parse(first)
    assert back.name == "KSBA TO KSMX"
    assert [(p.ident, p.type, p.country) for p in back.points] == [
        (p.ident, p.type, p.country) for p in ROUTE.points]
    for a, b in zip(back.points, ROUTE.points):
        assert a.lat == pytest.approx(b.lat, abs=5e-7) and a.lon == pytest.approx(b.lon, abs=5e-7)
    created = re.search(rb"<created>.*</created>", first).group(0)
    assert fpl.write(back).replace(re.search(rb"<created>.*</created>", fpl.write(back)).group(0),
                                   created) == first


def test_a_revisited_fix_is_one_waypoint_and_two_route_points():
    p = plan(("GVO", "VOR", 34.531111, -120.091111, "K2"), ("RZS", "VOR", 34.5095, -119.770833, "K2"),
             ("GVO", "VOR", 34.531111, -120.091111, "K2"))
    data = fpl.write(p)
    assert schema_ok(data)
    assert data.count(b"<waypoint>") == 2 and data.count(b"<route-point>") == 3


def test_two_places_under_one_name_cannot_be_written():
    p = plan(("USR1", "USER WAYPOINT", 34.0, -120.0), ("USR1", "USER WAYPOINT", 35.0, -120.0))
    with pytest.raises(fpl.FplError, match="TWO PLACES NAMED USR1"):
        fpl.write(p)


def test_one_fix_a_hair_apart_is_one_waypoint():
    # The same VOR from two sources (bench, 2026-11-22), and a revisit.
    p = plan(("VTU", "VOR", 34.11506389, -119.0495, "K2"), ("HENER", "INT", 34.331, -119.228, "K2"),
             ("VTU", "VOR", 34.11506461, -119.04949866, "K2"))
    data = fpl.write(p)
    assert schema_ok(data) and data.count(b"<waypoint>") == 2 and data.count(b"<route-point>") == 3
    assert fpl.parse(data).points[2].lat == 34.115064


def test_two_places_a_little_apart_are_still_two():
    p = plan(("USR1", "USER WAYPOINT", 34.0, -120.0), ("USR1", "USER WAYPOINT", 34.002, -120.0))  # 0.12 nm
    with pytest.raises(fpl.FplError, match="TWO PLACES NAMED USR1"):
        fpl.write(p)


def test_names_and_idents_are_cleaned_to_the_schema():
    assert fpl.clean_ident("usr-1") == "USR1"
    assert fpl.clean_ident("ABCDEFGHIJKLMNOP") == "ABCDEFGHIJKL"
    assert fpl.clean_name("ksba -> ksmx; via v27") == "KSBA KSMX VIA V27"
    assert len(fpl.clean_name("X" * 40)) == 25
    data = fpl.write(plan(("KSBA", "AIRPORT", 34.4, -119.8, "K2"), name="a;b<c>&d " * 5))
    assert schema_ok(data)


def test_every_fix_type_maps_to_a_garmin_type_and_back():
    for fix_type, gtype in fpl.FIX_TO_TYPE.items():
        assert gtype in fpl.TYPE_TO_FIX
    # The types the route block can carry round-trip; unknown and map points
    # become user waypoints (doc/plugins/fpl_exchange.md).
    for fix_type in (1, 2, 3, 4, 5):
        assert fpl.TYPE_TO_FIX[fpl.FIX_TO_TYPE[fix_type]] == fix_type


# --- the reader against the schema -----------------------------------------------


def test_handmade_fixture_is_valid_and_reads():
    data = handmade()
    assert schema_ok(data), SCHEMA.error_log
    p = fpl.parse(data)
    assert p.name == "KSBA TO KSMX"
    assert [x.ident for x in p.points] == ["KSBA", "RZS", "GVO", "AVOLS", "LAKE", "USR001", "KSMX"]
    assert [x.fix_type for x in p.points] == [1, 2, 2, 4, 4, 5, 1]
    assert p.points[5].country == "" and p.points[0].lat == 34.426211


def test_handmade_fixture_round_trips_through_the_writer():
    p = fpl.parse(handmade())
    again = fpl.parse(fpl.write(p))
    assert again == fpl.Plan(fpl.clean_name(p.name), p.points)


@pytest.mark.parametrize("path", FOREFLIGHT or [None], ids=lambda p: os.path.basename(p) if p else "none")
def test_foreflight_export_reads_and_round_trips(path):
    if path is None:
        pytest.skip("no ForeFlight export in data/foreflight yet (AER-6321: Bill exports them)")
    with open(path, "rb") as f:
        data = f.read()
    assert schema_ok(foreflight_to_schema(data)), SCHEMA.error_log
    p = fpl.parse(data)
    out = fpl.write(p)
    assert schema_ok(out)
    assert fpl.parse(out).points == p.points


def test_the_first_foreflight_export_reads_as_bill_built_it():
    # Bill's iPad, 2026-11-22: KSBA to KPHX, edited in ForeFlight and shared.
    with open(os.path.join(DATA, "foreflight", "ksba_kphx.fpl"), "rb") as f:
        data = f.read()
    assert data[:2] == b"\xff\xfe" and not schema_ok(data)   # what ForeFlight really sends
    p = fpl.parse(data)
    assert p.name == "KSBA TO KPHX" and len(p.points) == 17
    assert [x.ident for x in p.points[:3]] == ["KSBA", "FLOUT", "VTU"] and p.points[-1].ident == "KPHX"
    assert [x.fix_type for x in p.points[:3]] == [1, 4, 2]
    assert p.points[0].country == "" and p.points[0].lat == 34.426192


def test_foreflight_ksba_kpmd_is_what_reached_the_bench_bus():
    """Bill's second upload at the bench (2026-11-22), rebuilt from the
    hotspot's packet log: FPLIMP* read KSBA TO KPMD, 13 points."""
    with open(os.path.join(DATA, "foreflight", "ksba_kpmd.fpl"), "rb") as f:
        p = fpl.parse(f.read())
    assert p.name == "KSBA TO KPMD"
    assert [x.ident for x in p.points] == ["KSBA", "FLOUT", "VTU", "HENER", "OHIGH", "TTE", "WRING", "AMONT",
                                           "LOPES", "PAIDD", "JEFFY", "LHS", "KPMD"]
    assert [x.fix_type for x in p.points][:3] == [1, 4, 2] and p.points[-1].fix_type == 1


@pytest.mark.parametrize("path", FOREFLIGHT, ids=os.path.basename)
def test_no_fixture_carries_a_tail_number(path):
    with open(path, "rb") as f:
        text = f.read().decode("utf-16")
    assert all(v == "" for v in re.findall(r"<aircraft-tailnumber>([^<]*)<", text))


def ff_variant(old, new):
    s = H.replace(old, new, 1)
    assert s != H, old
    return s.encode()


def test_foreflight_departures_each_read_alone():
    for data in (
        H.encode("utf-16"),                                                    # UTF-16 under a utf-8 declaration
        ff_variant("<created>", "<aircraft><aircraft-tailnumber>N1</aircraft-tailnumber></aircraft>"
                                "<flight-data><altitude-ft>6000</altitude-ft></flight-data><created>"),
        ff_variant("      <comment />\n", ""),                                 # no comment
        # No country codes, in the table or the route (ForeFlight omits both;
        # dropping one side only is a keyref mismatch, refused by both).
        re.sub(r"<(waypoint-)?country-code>[A-Z0-9]*</(waypoint-)?country-code>|<(waypoint-)?country-code ?/>",
               "", H).encode(),
        ff_variant("2026-11-22T15:00:00Z", "20261122T15:00:00Z"),              # compact created
        ff_variant("<comment />", "<comment /><altitude-ft></altitude-ft>"),
        ff_variant("<comment />", "<comment /><altitude-ft>1200.5</altitude-ft>"),
    ):
        assert accepted_ok(data), data[:200]
        assert isinstance(fpl.parse(data), fpl.Plan)


def test_foreflight_leniency_is_only_what_it_sends():
    for data, why in (
        (ff_variant("<comment />", "<comment /><altitude-ft>high</altitude-ft>"), "BAD ALTITUDE"),
        (ff_variant("<created>", "<aircraft><x/></aircraft><speed>1</speed><created>"), None),
        (ff_variant("2026-11-22T15:00:00Z", "20261322T15:00:00Z"), "BAD CREATED"),  # month 13
        (b"\xff\xfe<\x00", None),                                              # truncated UTF-16
        (b"\xff\xfe" + b"\x00\xd8<\x00", None),                                # lone surrogate
    ):
        got = reader(data)
        assert isinstance(got, str) and (why is None or got == why), got


H = handmade().decode()


def sub(old, new, count=1):
    assert old in H, old
    return H.replace(old, new, count).encode()


INVALID = {
    "lower-case ident": sub("<identifier>KSBA<", "<identifier>ksba<"),
    "13-char ident": sub("<identifier>KSBA<", "<identifier>KSBAKSBAKSBAK<"),
    "ident with space": sub("<identifier>KSBA<", "<identifier>KSBA <"),
    "unknown type": sub("<type>VOR<", "<type>FIX<"),
    "one-char country": sub("<country-code>K2<", "<country-code>K<"),
    "lat over 90": sub("<lat>34.426211<", "<lat>90.000001<"),
    "lon under -180": sub("<lon>-119.840372<", "<lon>-180.5<"),
    "exponent lat": sub("<lat>34.426211<", "<lat>3.4e1<"),
    "empty lat": sub("<lat>34.426211<", "<lat><"),
    "lower-case comment": sub("<comment>SANTA BARBARA<", "<comment>Santa Barbara<"),
    "26-char route name": sub("<route-name>KSBA TO KSMX<", "<route-name>%s<" % ("A" * 26)),
    "index 0": sub("<flight-plan-index>1<", "<flight-plan-index>0<"),
    "index 99": sub("<flight-plan-index>1<", "<flight-plan-index>99<"),
    "created without Z": sub("2026-11-22T15:00:00Z", "2026-11-22T15:00:00"),
    "no waypoint table": re.sub(r"(?s)<waypoint-table>.*</waypoint-table>", "", H).encode(),
    "lon before lat": sub("<lat>34.509500</lat>\n      <lon>-119.770833</lon>",
                          "<lon>-119.770833</lon>\n      <lat>34.509500</lat>"),
    "unknown element": sub("<comment />", "<comment /><speed>120</speed>"),
    "duplicate waypoint": sub("<identifier>KLPC<", "<identifier>KSBA<").replace(
        b"<lat>34.665000", b"<lat>34.665000"),
    "route point not in table": sub("<waypoint-identifier>RZS<", "<waypoint-identifier>RZZ<"),
    "text in a waypoint": sub("<waypoint>", "<waypoint>stray"),
    "attribute on a waypoint": sub("<waypoint>", '<waypoint kind="x">'),
    "wrong root": sub("<flight-plan ", "<flightplan ").replace(b"</flight-plan>", b"</flightplan>"),
    "wrong namespace": sub("FlightPlan/v1\"", "FlightPlan/v2\""),
    "extension in our namespace": sub("<x:note>ignored</x:note></extensions>\n    </waypoint>",
                                      "<identifier>X</identifier></extensions>\n    </waypoint>"),
    "email without domain": sub(' domain="example.org"', ""),
    "two route names": sub("<route-name>KSBA TO KSMX</route-name>",
                           "<route-name>A</route-name><route-name>B</route-name>"),
    "no route points": re.sub(r"(?s)<route-point>.*</route-point>", "", H).encode(),
    "not well formed": H[:-40].encode(),
    "unknown xsi attribute": sub("xsi:schemaLocation=", "xsi:schemaLoc="),
    "link with a space": sub("<link>https://www8", "<link>htt s://www8"),
    "link with a bad scheme": sub("<link>https://git", "<link>h'tps://git"),
    "xml version 1A0": sub('version="1.0"', 'version="1A0"'),
    "unknown encoding": sub('encoding="utf-8"', 'encoding="ut-8"'),
    "bad namespace uri": sub('xmlns:x="urn:example:extension"', 'xmlns:x="urn:e>xample"'),
    "wrong ref type": sub("<waypoint-type>VOR<", "<waypoint-type>NDB<"),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_reader_refuses_what_the_schema_refuses(name):
    data = INVALID[name]
    assert not accepted_ok(data), "the mutation is acceptable; fix the test"
    assert isinstance(reader(data), str)


def test_no_route_is_schema_valid_but_refused():
    data = re.sub(r"(?s)\s*<route>.*</route>", "", H).encode()
    assert schema_ok(data)
    assert reader(data) == "NO ROUTE"


def test_a_route_longer_than_the_route_block_is_refused():
    points = "".join(
        "<route-point><waypoint-identifier>KSBA</waypoint-identifier><waypoint-type>AIRPORT"
        "</waypoint-type><waypoint-country-code>K2</waypoint-country-code></route-point>"
        for _ in range(101))
    data = re.sub(r"(?s)<route-point>.*</route-point>", points, H).encode()
    assert schema_ok(data)
    assert reader(data) == "TOO MANY POINTS"
    assert isinstance(fpl.parse(data, max_points=101), fpl.Plan)


def test_reader_never_accepts_what_the_schema_refuses():
    """Random single edits to a valid document: whatever the reader accepts,
    the schema accepts. The other direction is reported too, so a reader
    stricter than the schema shows up here, not on the bench."""
    rng = random.Random(6321)
    data = handmade()
    alphabet = b"<>/ =\"'&;AZaz09.-:x\n"
    stricter = []
    for _ in range(3000):
        b = bytearray(data)
        i = rng.randrange(len(b))
        op = rng.randrange(3)
        if op == 0:
            del b[i]
        elif op == 1:
            b[i] = alphabet[rng.randrange(len(alphabet))]
        else:
            b.insert(i, alphabet[rng.randrange(len(alphabet))])
        b = bytes(b)
        got = reader(b)
        valid = accepted_ok(b)
        if isinstance(got, fpl.Plan):
            assert valid, b.decode(errors="replace")
        elif valid and got not in EXTRA_REFUSALS:
            stricter.append((got, b))
    assert not stricter, stricter[:3]


# --- the reader against hostile input ------------------------------------------------


@pytest.mark.parametrize("doc", [
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
    b'<flight-plan xmlns="http://www8.garmin.com/xmlschemas/FlightPlan/v1">&lol2;</flight-plan>',
    b'<?xml version="1.0"?><!DOCTYPE f [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    b'<flight-plan xmlns="http://www8.garmin.com/xmlschemas/FlightPlan/v1">&x;</flight-plan>',
    b'<?xml version="1.0"?><!DOCTYPE flight-plan SYSTEM "http://example.org/x.dtd">'
    b'<flight-plan xmlns="http://www8.garmin.com/xmlschemas/FlightPlan/v1"/>',
], ids=["entity expansion", "external entity", "external dtd"])
def test_dtd_and_entities_are_refused(doc):
    assert reader(doc) == "DTD OR ENTITY"


def test_processing_instruction_is_refused():
    assert reader(H.replace("<waypoint-table>", "<?evil x?><waypoint-table>", 1).encode()) == "PROCESSING INSTRUCTION"


def test_size_is_bounded_before_parsing():
    data = handmade() + b" " * (fpl.MAX_BYTES - len(handmade()) + 1)
    assert reader(data) == "TOO LARGE"
    assert isinstance(fpl.parse(handmade(), max_bytes=len(handmade())), fpl.Plan)


def test_depth_is_bounded():
    deep = "<x:a>" * 50 + "</x:a>" * 50
    data = sub("<x:note>ignored</x:note>", deep)
    assert reader(data) == "TOO DEEP"


def test_empty_and_non_bytes_are_refused():
    assert reader(b"") == "EMPTY"
    assert reader(b"   \n") == "EMPTY"
    assert reader(H) == "NOT BYTES"
