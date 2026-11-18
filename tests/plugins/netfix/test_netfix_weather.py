"""
The netfix FIS-B weather request, @xweather;<station> (AER-5936).

Every test goes through a real netfix server and socket.  The products are
built by the store's own parsers (text_product, twgo_product), dated from the
real clock, because the server computes each product's state at the moment it
replies.
"""
import calendar
import json
import time
from collections import namedtuple

import pytest

from fixgw.plugins.stratux import fisb, weather


def _ask(sock, line):
    """Send one request and return the one reply line, without its newline."""
    sock.sendall(line.encode())
    res = b""
    while not res.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:  # the server closed the connection
            break
        res += chunk
    assert res.endswith(b"\n"), res
    assert res.count(b"\n") == 1, res
    return res[:-1].decode()


def _reply(sock, station):
    line = _ask(sock, "@xweather;%s\n" % station)
    prefix = "@xweather;%s;" % station
    assert line.startswith(prefix), line
    return json.loads(line[len(prefix):])


def _ddhhmm(t):
    return time.strftime("%d%H%M", time.gmtime(t))


def _yymmddhhmm(t):
    return time.strftime("%y%m%d%H%M", time.gmtime(t))


Apdu = namedtuple("Apdu", ["product_id", "hour"])
Record = namedtuple("Record", ["text", "report_year", "report_number"])


@pytest.fixture
def store():
    s = weather.WeatherStore()
    weather.register(s)
    yield s
    weather.unregister(s)


def _put_text(store, text, rx):
    p = weather.text_product(text, Apdu(fisb.PRODUCT_TEXT, None), rx)
    assert p is not None, text
    store._put(p, text)
    return p


def _put_notam(store, text, location, rx):
    p = weather.twgo_product(Apdu(8, None), location, Record(text, 26, 101), rx)
    assert p is not None, text
    store._put(p, text)
    return p


def _taf(now):
    # A real TAF's layout (KNID, the GDL2 golden): continuation lines indented
    # five spaces after a line break.
    start, end = now - 3600, now + 23 * 3600
    period = "%s/%s" % (time.strftime("%d%H", time.gmtime(start)),
                        time.strftime("%d%H", time.gmtime(end)))
    return ("TAF KSCK %sZ %s 13006KT P6SM SCT250\n"
            "     FM%s 31010KT P6SM SKC\n"
            "     TEMPO %s 3SM BR=" % (_ddhhmm(now - 1800), period,
                                       _ddhhmm(now + 3 * 3600), period))


def test_no_store_is_an_explicit_reply(plugin):
    assert weather._stores == []
    assert _ask(plugin.sock, "@xweather;KSCK\n") == "@xweather;KSCK!005"


def test_taf_line_breaks_come_back_byte_identical(plugin, store):
    now = time.time()
    taf = _put_text(store, _taf(now), now)
    assert "\n" in taf.text
    r = _reply(plugin.sock, "KSCK")
    (p,) = r["products"]
    assert p["kind"] == "TAF"
    assert p["station"] == "KSCK"
    assert p["text"] == taf.text
    assert p["text"].encode() == taf.text.encode()
    assert p["state"] == "current"


def test_notam_semicolon_comes_back_byte_identical(plugin, store):
    now = time.time()
    text = ("NOTAM-D KSCK.11/101 %sZ !SCK 11/101 SCK RWY 11R/29L CLSD; "
            "TWY A CLSD; ALL OTHER TWY OPEN %s-%s"
            % (_ddhhmm(now - 600), _yymmddhhmm(now - 3600), _yymmddhhmm(now + 3600)))
    notam = _put_notam(store, text, "KSCK", now)
    r = _reply(plugin.sock, "KSCK")
    (p,) = r["products"]
    assert p["kind"] == "NOTAM-D"
    assert p["text"] == notam.text == text
    assert p["text"].count(";") == 2
    assert p["state"] == "current"
    assert p["issued"] is None
    assert p["valid_from"] == weather._iso(notam.valid_from)
    assert p["old_at"] == weather._iso(notam.old_at)


def test_old_metar_is_marked_old_and_a_fresh_one_current(plugin, store):
    now = time.time()
    # 80 minutes after its observation a METAR is past the 75-minute limit.
    old = _put_text(store, "METAR KSCK %sZ 13006KT 10SM CLR 08/07 A3028=" % _ddhhmm(now - 80 * 60),
                    now - 79 * 60)
    fresh = _put_text(store, "METAR KMOD %sZ 31005KT 10SM CLR 10/04 A3027=" % _ddhhmm(now - 10 * 60),
                      now - 9 * 60)
    r = _reply(plugin.sock, "KSCK")
    (p,) = r["products"]
    assert p["text"] == old.text
    assert p["state"] == "old"
    assert p["issued"] == weather._iso(old.issued)
    assert p["first_received"] == p["last_received"] == weather._iso(old.first_rx)
    r = _reply(plugin.sock, "KMOD")
    (p,) = r["products"]
    assert p["text"] == fresh.text
    assert p["state"] == "current"


def test_reply_carries_every_field_for_the_station_only(plugin, store):
    now = time.time()
    _put_text(store, "METAR KSCK %sZ 13006KT 10SM CLR 08/07 A3028=" % _ddhhmm(now - 600), now)
    _put_text(store, _taf(now), now)
    _put_text(store, "METAR KMOD %sZ 31005KT 10SM CLR 10/04 A3027=" % _ddhhmm(now - 600), now)
    before = time.time()
    r = _reply(plugin.sock, "KSCK")
    assert r["station"] == "KSCK"
    t = calendar.timegm(time.strptime(r["now"], "%Y-%m-%dT%H:%M:%SZ"))
    assert before - 1 <= t <= time.time() + 1
    assert [p["kind"] for p in r["products"]] == ["METAR", "TAF"]
    for p in r["products"]:
        assert set(p) == {"kind", "station", "text", "issued", "valid_from", "old_at",
                          "first_received", "last_received", "state"}
        assert p["station"] == "KSCK"


def test_station_with_no_products_is_an_empty_list(plugin, store):
    r = _reply(plugin.sock, "KXXX")
    assert r["station"] == "KXXX"
    assert r["products"] == []


def test_station_is_upper_cased(plugin, store):
    now = time.time()
    _put_text(store, "METAR KSCK %sZ 13006KT 10SM CLR 08/07 A3028=" % _ddhhmm(now - 600), now)
    line = _ask(plugin.sock, "@xweather;ksck\n")
    assert line.startswith("@xweather;KSCK;{")


@pytest.mark.parametrize("request_line,reply", [
    ("@xweather\n", "@xweather;!003"),
    ("@xweather;\n", "@xweather;!003"),
    ("@xweather;KS;CK\n", "@xweather;KS;CK!003"),
    ("@xweather;TOOLONG\n", "@xweather;TOOLONG!003"),
])
def test_bad_station_is_refused(plugin, store, request_line, reply):
    assert _ask(plugin.sock, request_line) == reply


def test_products_from_every_registered_store(plugin, store):
    now = time.time()
    second = weather.WeatherStore()
    weather.register(second)
    try:
        _put_text(store, "METAR KSCK %sZ 13006KT 10SM CLR 08/07 A3028=" % _ddhhmm(now - 600), now)
        _put_text(second, _taf(now), now)
        assert [p["kind"] for p in _reply(plugin.sock, "KSCK")["products"]] == ["METAR", "TAF"]
    finally:
        weather.unregister(second)
    assert [p["kind"] for p in _reply(plugin.sock, "KSCK")["products"]] == ["METAR"]


def test_unknown_x_command_reply_ends_its_line(plugin):
    # The reply had no newline, so a line-reading client waited for the next
    # line: a new client asking an older gateway for weather would hang.
    assert _ask(plugin.sock, "@xbogus\n") == "@xbogus!001"
