"""The fpl_exchange plugin on the real database (AER-6321, BACKLOG EFB4).

An upload becomes a pending plan on FPLIMP*, with FPLIMPSEQ written last, and
nothing else: no route slot, no FPLSEQ, no FPLCMD. A refused upload leaves
the bus as it was. The loaded plan goes out as a .fpl only when the engine
accepted it. The HTTP tests run the real server on loopback.
"""

import http.client
import logging
import os
import socket
import uuid

import pytest

import fixgw.plugins.fpl_exchange as fx
from fixgw.plugins.fpl_exchange import fpl

LOG = logging.getLogger("test.fpl_exchange")
DATA = os.path.join(os.path.dirname(__file__), "data")


def handmade():
    with open(os.path.join(DATA, "handmade_full.fpl"), "rb") as f:
        return f.read()


def exchange(database, max_upload_bytes=fpl.MAX_BYTES):
    return fx.Exchange(database.read, database.write, LOG, max_upload_bytes)


def snapshot(database, prefix):
    return {k: database.read(k)[0] for k in database.listkeys() if k.startswith(prefix)}


def load_route(database, points, name="KSBA TO KSMX", seq=7, loaded=True):
    """Write a route block as the editor does, and FPLLDSEQ as the engine
    does when it accepts it."""
    for i, (ident, typ, lat, lon) in enumerate(points, start=1):
        database.write("FPL%dID" % i, ident)
        database.write("FPL%dTYPE" % i, typ)
        database.write("FPL%dLAT" % i, lat)
        database.write("FPL%dLON" % i, lon)
    database.write("FPLCOUNT", len(points))
    database.write("FPLNAME", name)
    database.write("FPLSEQ", seq)
    database.write("FPLLDSEQ", seq if loaded else 0)


ROUTE = [("KSBA", 1, 34.426211, -119.840372), ("RZS", 2, 34.5095, -119.770833),
         ("AVOLS", 4, 34.7, -120.3), ("KSMX", 1, 34.898917, -120.457583)]


# --- in: the pending plan -----------------------------------------------------------


def test_upload_becomes_the_pending_plan(database):
    ex = exchange(database)
    assert not ex.pending()
    ok, plan = ex.receive(handmade(), "127.0.0.1")
    assert ok
    assert database.read("FPLIMPCOUNT")[0] == 7
    assert database.read("FPLIMPNAME")[0] == "KSBA TO KSMX"
    assert database.read("FPLIMPSEQ")[0] == 1
    got = [(database.read("FPLIMP%dID" % i)[0], database.read("FPLIMP%dTYPE" % i)[0],
            database.read("FPLIMP%dLAT" % i)[0], database.read("FPLIMP%dLON" % i)[0]) for i in range(1, 8)]
    assert got[0] == ("KSBA", 1, 34.426211, -119.840372)
    assert [g[0] for g in got] == ["KSBA", "RZS", "GVO", "AVOLS", "LAKE", "USR001", "KSMX"]
    assert [g[1] for g in got] == [1, 2, 2, 4, 4, 5, 1]
    assert ex.pending()


def test_the_sequence_lands_after_the_block_is_complete(database):
    seen = []

    def on_seq(key, value, udata):
        seen.append((value[0], database.read("FPLIMPCOUNT")[0], database.read("FPLIMPNAME")[0],
                     database.read("FPLIMP7ID")[0]))

    database.callback_add("test", "FPLIMPSEQ", on_seq, None)
    exchange(database).receive(handmade(), "127.0.0.1")
    assert seen == [(1, 7, "KSBA TO KSMX", "KSMX")]


def test_the_editor_finishing_clears_pending_and_the_next_upload_is_newer(database):
    ex = exchange(database)
    ex.receive(handmade(), "127.0.0.1")
    database.write("FPLIMPDONE", 1)   # the pilot accepted or rejected it
    assert not ex.pending()
    ex.receive(handmade(), "127.0.0.1")
    assert database.read("FPLIMPSEQ")[0] == 2 and ex.pending()


def test_a_refused_upload_leaves_the_bus_as_it_was(database):
    ex = exchange(database)
    ex.receive(handmade(), "127.0.0.1")
    before = snapshot(database, "FPL")
    for bad in [b"", b"<x/>", handmade()[:-40], handmade().replace(b"KSBA", b"ksba", 1),
                handmade() + b" " * fpl.MAX_BYTES]:
        ok, reason = ex.receive(bad, "127.0.0.1")
        assert not ok and isinstance(reason, str)
    assert snapshot(database, "FPL") == before
    assert ex.refused == 5 and ex.accepted == 1


def test_the_plugin_writes_only_the_pending_block(database):
    """The writer registry books every write to the innermost plugin module:
    an upload must be fpl_exchange's, and only on FPLIMP* keys."""
    before = {k: list(v) for k, v in database.writers().items()}
    p = fx.Plugin("fpl_exchange", {"port": 0}, {})
    p.exchange.receive(handmade(), "127.0.0.1")
    after = database.writers()
    mine = {k for k, v in after.items() if "fpl_exchange" in v and "fpl_exchange" not in before.get(k, [])}
    assert mine == {"FPLIMP%d%s" % (i, f) for i in range(1, 8) for f in fx.PENDING_FIELDS} | {
        "FPLIMPCOUNT", "FPLIMPNAME", "FPLIMPSEQ"}
    assert not any(k.startswith("FPL") and not k.startswith("FPLIMP") for k in mine)
    assert "FPLCMD" not in mine and "FPLSEQ" not in mine


def test_a_database_without_the_pending_block_turns_uploads_off(database, caplog):
    class Old(fx.Plugin):
        def db_list(self):
            return [k for k in super().db_list() if not k.startswith("FPLIMP")]

    with caplog.at_level(logging.ERROR):
        p = Old("fpl_exchange", {"port": 0}, {})
    assert not p.upload_on and p.download_on
    assert "predates EFB4" in caplog.text
    assert p.get_status()["Upload"] == "off: database predates EFB4"


# --- out: the loaded plan -------------------------------------------------------


def test_the_loaded_plan_goes_out_as_fpl(database):
    load_route(database, ROUTE)
    name, data = exchange(database).export()
    assert name == "KSBA_TO_KSMX.fpl"
    plan = fpl.parse(data)
    assert [(p.ident, p.fix_type) for p in plan.points] == [(r[0], r[1]) for r in ROUTE]
    assert plan.points[0].lat == 34.426211 and plan.name == "KSBA TO KSMX"


def test_a_refused_or_missing_plan_is_not_offered(database):
    ex = exchange(database)
    assert ex.export() == (None, "No flight plan is loaded.")
    load_route(database, ROUTE, loaded=False)
    assert ex.export() == (None, "The flight plan on the bus was refused by the engine.")


def test_legs_without_a_fix_and_repeats_are_dropped(database):
    load_route(database, [("KSBA", 1, 34.4, -119.8), ("", 0, 0.0, 0.0), ("GVO", 2, 34.5, -120.1),
                          ("GVO", 2, 34.5, -120.1), ("KSMX", 1, 34.9, -120.5)])
    plan = fpl.parse(exchange(database).export()[1])
    assert [p.ident for p in plan.points] == ["KSBA", "GVO", "KSMX"]


def test_an_upload_does_not_change_what_is_offered(database):
    load_route(database, ROUTE)
    ex = exchange(database)
    before = ex.export()[1]
    ex.receive(handmade(), "127.0.0.1")
    after = ex.export()[1]
    assert after.split(b"</created>", 1)[1] == before.split(b"</created>", 1)[1]


# --- HTTP on loopback -------------------------------------------------------


@pytest.fixture
def served(database):
    plugins = []

    def start(**config):
        config.setdefault("host", "127.0.0.1")
        config.setdefault("port", 0)
        p = fx.Plugin("fpl_exchange", config, {})
        p.run()
        plugins.append(p)
        return p, p.server.server_address[1]

    yield start
    for p in plugins:
        p.stop()


def request(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    out = r.status, dict(r.getheaders()), r.read()
    c.close()
    return out


def multipart(data, field="fpl", filename="plan.fpl"):
    boundary = uuid.uuid4().hex
    body = (("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n" % (boundary, field, filename)).encode()
            + data + ("\r\n--%s--\r\n" % boundary).encode())
    return body, {"Content-Type": "multipart/form-data; boundary=" + boundary}


def test_http_raw_upload_posts_the_pending_plan(database, served):
    p, port = served()
    status, _, body = request(port, "POST", "/upload", handmade(), {"Content-Type": "application/xml"})
    assert status == 200, body
    assert b"KSBA TO KSMX, 7 waypoints" in body
    assert database.read("FPLIMPSEQ")[0] == 1 and database.read("FPLIMPCOUNT")[0] == 7
    assert p.get_status()["Pending plan"] == "yes"


def test_http_form_upload_posts_the_pending_plan(database, served):
    _, port = served()
    body, headers = multipart(handmade())
    status, h, page = request(port, "POST", "/upload", body, headers)
    assert status == 200 and h["Content-Type"].startswith("text/html")
    assert database.read("FPLIMPCOUNT")[0] == 7


def test_http_form_needs_one_fpl_field(database, served):
    _, port = served()
    body, headers = multipart(handmade(), field="other")
    assert request(port, "POST", "/upload", body, headers)[0] == 400
    assert database.read("FPLIMPSEQ")[0] == 0


def test_http_oversized_upload_is_refused_unread(database, served):
    _, port = served(max_upload_bytes=1000)
    status, _, body = request(port, "POST", "/upload", handmade(), {"Content-Type": "application/xml"})
    assert status == 413 and b"1000 bytes" in body
    assert database.read("FPLIMPSEQ")[0] == 0


def test_http_oversized_length_is_refused_before_the_body(database, served):
    """A declared length over the limit is answered at once: the server does
    not wait for a body it will not read."""
    _, port = served(max_upload_bytes=1000)
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(b"POST /upload HTTP/1.0\r\nContent-Length: 1000000000\r\n\r\n")
    assert s.recv(100).startswith(b"HTTP/1.0 413")
    s.close()


def test_http_oversized_form_is_refused(database, served):
    _, port = served(max_upload_bytes=1000)
    body, headers = multipart(handmade())
    assert request(port, "POST", "/upload", body, headers)[0] == 413
    assert database.read("FPLIMPSEQ")[0] == 0


def test_http_malformed_upload_is_refused(database, served):
    _, port = served()
    status, _, body = request(port, "POST", "/upload", handmade().replace(b"KSBA", b"ksba", 1))
    assert status == 400 and b"BAD IDENTIFIER" in body
    assert database.read("FPLIMPSEQ")[0] == 0


def test_http_upload_needs_a_length(database, served):
    _, port = served()
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"POST /upload HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
    assert s.recv(100).startswith(b"HTTP/1.0 411")
    s.close()
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"POST /upload HTTP/1.0\r\n\r\n")
    assert s.recv(100).startswith(b"HTTP/1.0 411")
    s.close()


def test_http_download_serves_the_loaded_plan(database, served):
    load_route(database, ROUTE)
    _, port = served()
    status, h, body = request(port, "GET", "/plan.fpl")
    assert status == 200 and h["Content-Disposition"] == 'attachment; filename="KSBA_TO_KSMX.fpl"'
    assert [p.ident for p in fpl.parse(body).points] == ["KSBA", "RZS", "AVOLS", "KSMX"]
    status, h, page = request(port, "GET", "/")
    assert status == 200 and b"KSBA RZS AVOLS KSMX" in page and b'href="/plan.fpl"' in page


def test_http_no_plan_is_404(database, served):
    _, port = served()
    assert request(port, "GET", "/plan.fpl")[0] == 404


def test_http_each_direction_turns_off(database, served):
    load_route(database, ROUTE)
    _, port = served(download=False, upload=False)
    assert request(port, "GET", "/plan.fpl")[0] == 404
    assert request(port, "POST", "/upload", handmade())[0] == 404
    page = request(port, "GET", "/")[2]
    assert b"plan.fpl" not in page and b"/upload" not in page
    assert database.read("FPLIMPSEQ")[0] == 0


def test_http_other_paths_are_404(database, served):
    _, port = served()
    for method, path in [("GET", "/etc/passwd"), ("GET", "/../config"), ("PUT", "/upload"),
                         ("POST", "/plan.fpl"), ("GET", "/upload")]:
        assert request(port, method, path)[0] in (404, 501), path


def refused(port):
    """True when the server closes the connection without an answer."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.sendall(b"GET / HTTP/1.0\r\n\r\n")
        return s.recv(100) == b""
    except ConnectionResetError:
        return True
    finally:
        s.close()


def test_http_source_filter_drops_other_networks(database, served):
    p, port = served(allow=["10.0.0.0/8"])
    assert refused(port)
    assert p.server.dropped == 1
    _, port2 = served(allow=["127.0.0.0/8"])
    assert not refused(port2)


def test_http_unparseable_filter_accepts_nobody(database, served, caplog):
    with caplog.at_level(logging.ERROR):
        p, port = served(allow=["127.0.0.0/8", "wifi"])
    assert refused(port)
    assert "refusing every client" in caplog.text


def test_parse_allow():
    assert fx.parse_allow([], LOG) is None
    assert fx.parse_allow(None, LOG) is None
    assert fx.parse_allow("192.168.1.0/24", LOG)[0].prefixlen == 24
    assert fx.parse_allow(["192.168.1.5"], LOG)[0].prefixlen == 32
    assert fx.parse_allow(["192.168.1.0/24", "x"], LOG) == []
    assert not fx.allowed([], "127.0.0.1")
    assert fx.allowed(None, "127.0.0.1")
