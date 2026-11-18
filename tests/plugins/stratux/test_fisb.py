"""
FIS-B uplink (GDL90 0x07) decode and the text weather store (AER-5651).

The frames are real: dump978's sample-data, RF captures of Bay Area FIS-B
ground stations in January 2015.  The expected texts come from fisb-decode,
an independent decoder written against DO-358B (data/README.md has the
commands).  The age tests drive the store's clock directly.
"""
import calendar
import gzip
import json
import os
from unittest.mock import MagicMock

import pytest

import fixgw.plugins.stratux as stratux
from fixgw.plugins.stratux import MainThread, fisb, gdl90, weather

DATA = os.path.join(os.path.dirname(__file__), "data")


def _lines():
    with gzip.open(os.path.join(DATA, "dump978-sample-2015.txt.gz"), "rt") as f:
        return [ln.strip() for ln in f if ln.startswith("+")]


LINES = _lines()
with open(os.path.join(DATA, "fisb_oracle.json")) as _f:
    ORACLE = json.load(_f)


def _uplink_bytes(i):
    return bytes.fromhex(LINES[i][1:].split(";")[0])


def _gdl90(i):
    # G§3.3: ID 0x07, a 3-byte time of reception, the 432-byte uplink
    return gdl90.encode(bytes([0x07, 0x12, 0x34, 0x00]) + _uplink_bytes(i))


def utc(y, mo, d, h, mi, s=0):
    return float(calendar.timegm((y, mo, d, h, mi, s)))


# The captures are from 23-25 January 2015; the golden runs receive them at
# 04:30 UTC on the 24th.
RX = utc(2015, 1, 24, 4, 30)


# --- wire decode against the corpus ------------------------------------------


def _ours(i):
    out = []
    for a in fisb.parse_uat_uplink(_uplink_bytes(i)).apdus:
        if a.segmented:
            continue
        if a.product_id == fisb.PRODUCT_TEXT:
            text = "".join(fisb.split_records(fisb.dlac_decode(a.payload)))
            out.append({"product_id": 413, "hour": a.hour, "minute": a.minute,
                        "text": fisb.clean_text(text)})
        elif a.product_id in fisb.PRODUCTS_TWGO:
            t = fisb.parse_twgo(a.payload)
            if t.record_format == fisb.RECORD_TEXT:
                out.append({
                    "product_id": a.product_id, "hour": a.hour,
                    "minute": a.minute, "location": t.location,
                    "records": [[r.report_number, r.report_year, r.active,
                                 fisb.clean_text(r.text)] for r in t.records],
                })
    return out


def test_corpus_size():
    assert len(LINES) == ORACLE["uplinks"] == 704
    assert sum(len(u["apdus"]) for u in ORACLE["text_uplinks"]) == 329


def test_every_text_product_matches_fisb_decode():
    expected = {u["line"]: u["apdus"] for u in ORACLE["text_uplinks"]}
    mismatched = [i for i in range(len(LINES)) if _ours(i) != expected.get(i, [])]
    assert mismatched == []


def test_segmented_tfr_joins_to_fisb_decode_text():
    reassembler = fisb.Reassembler()
    joined = []
    for i in range(len(LINES)):
        for a in fisb.parse_uat_uplink(_uplink_bytes(i)).apdus:
            if a.segmented:
                done = reassembler.add(a, 0.0)
                if done is not None:
                    joined.append((i, done))
    assert len(joined) == 1
    i, apdu = joined[0]
    want = ORACLE["joined"][0]
    assert i == want["last_line"]
    t = fisb.parse_twgo(apdu.payload)
    assert [[r.report_number, r.report_year, r.active, fisb.clean_text(r.text)]
            for r in t.records] == want["records"]
    assert t.records[0].text.startswith("NOTAM-TFR 5/4342 152352Z PART 1 OF 2")


def test_apdu_header_fields_of_a_real_metar():
    # line 18 carries METAR KCXP 240355Z; its APDU is stamped 03:55
    a = [a for a in fisb.parse_uat_uplink(_uplink_bytes(18)).apdus
         if a.product_id == 413][0]
    assert (a.t_opt, a.month, a.day, a.hour, a.minute) == (0, None, None, 3, 55)
    assert a.segmented is False


def test_segment_header_fields():
    segs = [a for i in range(len(LINES))
            for a in fisb.parse_uat_uplink(_uplink_bytes(i)).apdus if a.segmented]
    assert sorted((a.product_id, a.file_length, a.apdu_number) for a in segs) == [
        (8, 3, 1), (8, 3, 2), (8, 3, 3)]
    assert len({a.file_id for a in segs}) == 1


def test_time_options_with_seconds_are_refused():
    # DO-267A and DO-358B disagree on t_opt 1 and 3; FIS-B sends 0 and 2.
    for t_opt in (1, 3):
        frame = bytes([0x00, (413 & 0x3F) << 2 | (t_opt >> 1), (t_opt & 1) << 7, 0, 0, 0, 0])
        assert fisb.parse_apdu(frame) is None


def test_dlac_tab_is_a_run_of_spaces():
    # A, TAB, 5, B: codes 1, 28, 5, 2 -> "A     B"
    codes = [1, 28, 5, 2]
    bits = 0
    for c in codes:
        bits = (bits << 6) | c
    assert fisb.dlac_decode(bits.to_bytes(3, "big")) == "A     B"


def test_short_or_wrong_message_is_none():
    assert fisb.parse_uplink_message(b"\x07" + bytes(100)) is None
    assert fisb.parse_uplink_message(b"\x14" + bytes(435)) is None


def test_corrupt_frame_length_stops_the_walk():
    data = bytearray(_uplink_bytes(18))
    data[8] = 0xFF  # first frame claims 511 bytes
    data[9] |= 0x80
    up = fisb.parse_uat_uplink(bytes(data))
    assert up.apdus == []


# --- golden: one real uplink per text product, through the plugin -------------


class FakeSocket:
    def __init__(self, packets):
        self.packets = list(packets)

    def bind(self, address):
        pass

    def settimeout(self, timeout):
        pass

    def close(self):
        pass

    def recvfrom(self, _size):
        packet = self.packets.pop(0)
        if isinstance(packet, tuple):
            return packet
        return packet, ("192.168.10.1", 4000)


def _run(monkeypatch, packets, config=None, rx=RX):
    """The real receive loop over packets, with the wall clock at rx."""
    sock = FakeSocket(packets)
    parent = MagicMock()
    parent.log = MagicMock()
    parent.config = dict({"traffic": False, "status": False, "fisb": True}, **(config or {}))
    parent.status = {}
    monkeypatch.setattr(stratux.socket, "socket", lambda *_args: sock)
    thread = MainThread(parent)
    thread.wall_clock = lambda: rx
    real = sock.recvfrom

    def stop_after_last(size):
        result = real(size)
        if not sock.packets:
            thread.getout = True
        return result

    sock.recvfrom = stop_after_last
    thread.run()
    return thread, parent


GOLDEN = [
    # (line, kind, station, text)
    (18, "METAR", "KCXP",
     "METAR KCXP 240355Z AUTO 32003KT 10SM CLR 03/M10 A3051 RMK AO2="),
    (300, "SPECI", "KSCK",
     "SPECI KSCK 240402Z 13006KT 3SM BR OVC006 08/07 A3028 RMK AO2="),
    (136, "TAF", "KNID",
     "TAF KNID 2323/2423 11008KT 9999 SKC 520009 520903 QNH3022INS WND\n"
     "     070V140\n"
     "     BECMG 2401/2403 21006KT 520309 QNH3025INS WND VRB04KT AFT\n"
     "     2314\n"
     "     BECMG 2417/2419 08010KT 520009 520906 QNH3018INS WND\n"
     "     10014G20KT AFT 2421 T00/2414Z T20/2423Z\n"
     "     LAST NO AMDS AFT 2406 NEXT 2612="),
    (170, "TAF.AMD", "KLAS",
     "TAF.AMD KLAS 240247Z 2403/2424 24007KT P6SM SKC\n"
     "     FM240800 35008KT P6SM SKC\n"
     "     FM241700 02012G18KT P6SM SKC="),
    (158, "PIREP", "FAT",
     "PIREP KFAT 240325Z FAT UA /OV KFAT/TM 0325/FL340/TP B738/TB NEG/RM AWC-WEB:ASA"),
    (0, "WINDS", "BCE",
     "WINDS BCE 250000Z  FT                       12000       18000   24000   30000"
     "    34000  39000\n"
     "                        3522+00 3631-13 0141-27 035046 046656 365258"),
    (33, "NOTAM-D", "KLHM",
     "NOTAM-D KLHM.01/124 230125Z !RIU 01/124 LHM RWY 15 PAPI OUT OF SERVICE"
     " 1501230125-1501300122"),
]


@pytest.mark.parametrize("line,kind,station,text", GOLDEN, ids=[g[1] for g in GOLDEN])
def test_golden_product(monkeypatch, line, kind, station, text):
    thread, _ = _run(monkeypatch, [_gdl90(line)])
    found = [p for p, _s in thread.weather.by_station(station, RX)
             if p.kind == kind and p.text == text]
    assert len(found) == 1
    p = found[0]
    assert p.first_rx == p.last_rx == RX


def test_golden_tfr_from_three_segments(monkeypatch):
    segs = [i for i in range(len(LINES))
            if any(a.segmented for a in fisb.parse_uat_uplink(_uplink_bytes(i)).apdus)]
    thread, _ = _run(monkeypatch, [_gdl90(i) for i in segs], rx=utc(2015, 1, 24, 23, 0))
    tfr = [p for p, _s in thread.weather.products(utc(2015, 1, 24, 23, 0))
           if p.kind == "NOTAM-TFR"]
    assert len(tfr) == 1
    assert tfr[0].text == ORACLE["joined"][0]["records"][0][3]
    assert tfr[0].station == ""
    # EFFECTIVE 1501242200 UNTIL 1501252000
    assert tfr[0].valid_from == utc(2015, 1, 24, 22, 0)
    assert tfr[0].old_at == utc(2015, 1, 25, 20, 0)


@pytest.mark.parametrize("line,kind,rx,old_at", [
    # SIGMET KSFO 240223 ... VALID UNTIL 240623
    (277, "SIGMET", utc(2015, 1, 24, 2, 30), utc(2015, 1, 24, 6, 23)),
    # AIRMET KSFO 241445 ... VALID UNTIL 242100
    (682, "AIRMET", utc(2015, 1, 24, 14, 50), utc(2015, 1, 24, 21, 0)),
], ids=["SIGMET", "AIRMET"])
def test_golden_sigmet_and_airmet_dated_by_valid_until(monkeypatch, line, kind, rx, old_at):
    thread, _ = _run(monkeypatch, [_gdl90(line)], rx=rx)
    found = [p for p, _s in thread.weather.products(rx) if p.kind == kind]
    assert len(found) == 1
    assert found[0].old_at == old_at
    assert found[0].first_rx == rx


def test_whole_corpus_through_the_plugin(monkeypatch):
    thread, parent = _run(monkeypatch, [_gdl90(i) for i in range(len(LINES))])
    thread.fisb_tick_at = 0.0
    thread.fisb_tick()  # purges what is past its time at RX
    counts = thread.weather.kind_counts()
    # one per station for METAR/SPECI and TAF; PIREPs and winds per report
    assert counts["METAR"] + counts.get("SPECI", 0) > 50
    assert {"TAF", "TAF.AMD", "PIREP", "WINDS", "NOTAM-D", "NOTAM-TFR"} <= set(counts)
    assert parent.status["FIS-B uplinks"] == 704
    assert parent.status["FIS-B products"] == sum(counts.values())
    current = [p for p, s in thread.weather.products(RX) if s == weather.CURRENT]
    assert parent.status["FIS-B current"] == len(current) > 0


def test_status_counts_none_current_when_the_clock_is_years_on(monkeypatch):
    # 2015 reports received by a 2026 clock date to the nearest month: never
    # current
    rx = utc(2026, 11, 14, 18, 0)
    thread, parent = _run(monkeypatch, [_gdl90(i) for i in range(len(LINES))], rx=rx)
    thread.fisb_tick_at = 0.0
    thread.fisb_tick()
    assert parent.status["FIS-B products"] > 0
    assert parent.status["FIS-B current"] == 0


def test_fisb_off_by_default(monkeypatch):
    thread, parent = _run(monkeypatch, [_gdl90(18)], config={"fisb": False})
    assert thread.weather is None
    assert "FIS-B uplinks" not in parent.status


def test_source_filter_applies_to_uplinks(monkeypatch):
    thread, _ = _run(
        monkeypatch,
        [(_gdl90(18), ("192.168.10.23", 4000))],
        config={"source": "192.168.10.1"},
    )
    assert thread.weather.counts["uplinks"] == 0


# --- ages: a stale product is never current ------------------------------------


def _store_with(line, rx):
    store = weather.WeatherStore()
    store.ingest(fisb.parse_uat_uplink(_uplink_bytes(line)), rx)
    return store


def _state(store, station, kind, now):
    states = [s for p, s in store.by_station(station, now) if p.kind == kind]
    assert len(states) == 1
    return states[0]


OBS = utc(2015, 1, 24, 3, 55)  # METAR KCXP 240355Z


def test_metar_current_then_old_then_gone():
    store = _store_with(18, OBS + 60)
    assert _state(store, "KCXP", "METAR", OBS + 74 * 60) == weather.CURRENT
    assert _state(store, "KCXP", "METAR", OBS + 75 * 60) == weather.OLD
    store.purge(OBS + 119 * 60)
    assert _state(store, "KCXP", "METAR", OBS + 119 * 60) == weather.OLD
    store.purge(OBS + 120 * 60)
    assert store.by_station("KCXP", OBS + 120 * 60) == []


def test_state_is_computed_at_read_not_at_receipt():
    # received fresh, read late: old, with no purge or tick in between
    store = _store_with(18, OBS + 60)
    assert _state(store, "KCXP", "METAR", OBS + 3 * 3600) == weather.OLD


def test_metar_received_already_old_is_old():
    # a rebroadcast heard 80 minutes after the observation
    store = _store_with(18, OBS + 80 * 60)
    assert _state(store, "KCXP", "METAR", OBS + 80 * 60) == weather.OLD


def test_metar_from_the_future_means_the_clock_is_wrong():
    # host clock 3 hours slow: the observation looks 3 hours ahead
    rx = OBS - 3 * 3600
    store = _store_with(18, rx)
    assert _state(store, "KCXP", "METAR", rx) == weather.OLD


def test_older_report_never_replaces_newer():
    store = weather.WeatherStore()
    newer = weather.text_product("METAR KAAA 240455Z 00000KT 10SM CLR 01/M01 A3000=", None, RX)
    older = weather.text_product("METAR KAAA 240355Z 27015KT 1SM OVC002 01/M01 A3000=", None, RX)
    store._put(newer, newer.text)
    store._put(older, older.text)
    (p, _s), = store.by_station("KAAA", RX)
    assert "240455Z" in p.text


def test_speci_replaces_the_metar_it_follows():
    store = weather.WeatherStore()
    for text in ("METAR KAAA 240355Z 00000KT 10SM CLR 01/M01 A3000=",
                 "SPECI KAAA 240412Z 00000KT 2SM BR OVC004 01/M01 A3000="):
        p = weather.text_product(text, None, RX)
        store._put(p, text)
    (p, _s), = store.by_station("KAAA", RX)
    assert p.kind == "SPECI"


def test_taf_old_after_its_valid_period():
    # TAF.AMD KLAS 240247Z 2403/2424
    store = _store_with(170, RX)
    end = utc(2015, 1, 25, 0, 0)
    assert _state(store, "KLAS", "TAF.AMD", end - 60) == weather.CURRENT
    assert _state(store, "KLAS", "TAF.AMD", end) == weather.OLD


def test_winds_future_current_old():
    # WINDS BCE 250000Z in an APDU stamped 02:06: the 24-hour forecast,
    # for use 6 hours either side of 00Z on the 25th (DO-358B table A-9).
    store = _store_with(0, utc(2015, 1, 24, 2, 6))
    valid = utc(2015, 1, 25, 0, 0)
    assert _state(store, "BCE", "WINDS", valid - 6 * 3600 - 60) == weather.FUTURE
    assert _state(store, "BCE", "WINDS", valid) == weather.CURRENT
    assert _state(store, "BCE", "WINDS", valid + 6 * 3600) == weather.OLD


def test_notam_future_current_old():
    # NOTAM-D KLHM ... 1501230125-1501300122
    store = _store_with(33, utc(2015, 1, 23, 1, 0))
    assert _state(store, "KLHM", "NOTAM-D", utc(2015, 1, 23, 1, 0)) == weather.FUTURE
    assert _state(store, "KLHM", "NOTAM-D", utc(2015, 1, 23, 2, 0)) == weather.CURRENT
    assert _state(store, "KLHM", "NOTAM-D", utc(2015, 1, 30, 1, 22)) == weather.OLD


def test_twgo_dropped_when_no_longer_received():
    rx = utc(2015, 1, 23, 2, 0)
    store = _store_with(33, rx)
    store.purge(rx + weather.TWGO_RETAIN_S)
    assert len(store.by_station("KLHM", rx)) == 1
    store.purge(rx + weather.TWGO_RETAIN_S + 1)
    assert store.by_station("KLHM", rx) == []


def test_cancellation_removes_the_notam():
    rx = utc(2015, 1, 23, 2, 0)
    store = _store_with(33, rx)
    (p, _s), = store.by_station("KLHM", rx)
    apdu = [a for a in fisb.parse_uat_uplink(_uplink_bytes(33)).apdus if a.product_id == 8][0]
    payload = bytearray(apdu.payload)
    payload[fisb.TWGO_HEADER_LEN + 4] &= ~0x04  # report status: cancelled
    cancelled = apdu._replace(payload=bytes(payload))
    store.ingest(fisb.Uplink(0, 0, True, 1, [cancelled]), rx + 60)
    assert store.by_station("KLHM", rx + 60) == []
    assert store.counts["cancelled"] == 1


def test_undated_report_is_not_stored():
    log = MagicMock()
    store = weather.WeatherStore(log)
    store._put(weather.text_product("METAR KAAA 99XXXXZ garbage", None, RX), "METAR KAAA")
    store._put(None, "SIGMET KSFO no valid time")
    assert store.items == {}
    assert store.counts["undated"] == 2


def test_month_rollover():
    # 31st 23:50 observed, received 1st 00:05 of the next month
    rx = utc(2015, 2, 1, 0, 5)
    assert weather.resolve_ddhhmm(31, 23, 50, rx) == utc(2015, 1, 31, 23, 50)
    # and the other way: valid on the 1st, received on the 31st
    rx = utc(2015, 1, 31, 23, 0)
    assert weather.resolve_ddhhmm(1, 6, 0, rx) == utc(2015, 2, 1, 6, 0)
    # day 31 never resolves into a 30-day month
    assert weather.resolve_ddhhmm(31, 0, 0, utc(2015, 4, 15, 0, 0)) in (
        utc(2015, 3, 31, 0, 0), utc(2015, 5, 31, 0, 0))


def test_reassembler_drops_a_stale_partial_file():
    r = fisb.Reassembler(expire_s=600)
    a = fisb.Apdu(8, 0, None, None, 0, 0, True, 7, 2, 1, bytes(10))
    b = a._replace(apdu_number=2)
    assert r.add(a, 0.0) is None
    assert r.add(b, 601.0) is None  # the first part expired; still waiting
    assert r.dropped == 1


# --- the netfix weather request's registry (AER-5936) -------------------------


def test_store_answers_requests_only_while_the_thread_runs(monkeypatch):
    seen = []
    sock = FakeSocket([b""])
    parent = MagicMock()
    parent.config = {"traffic": False, "status": False, "fisb": True}
    parent.status = {}
    monkeypatch.setattr(stratux.socket, "socket", lambda *_args: sock)
    thread = MainThread(parent)
    assert weather.station_reply("KCXP", RX) is None

    def recvfrom(_size):
        seen.append(list(weather._stores))
        thread.getout = True
        return b"", ("192.168.10.1", 4000)

    sock.recvfrom = recvfrom
    thread.run()
    assert seen == [[thread.weather]]
    assert weather._stores == []
    assert weather.station_reply("KCXP", RX) is None


def test_fisb_off_registers_no_store(monkeypatch):
    seen = []
    sock = FakeSocket([b""])
    parent = MagicMock()
    parent.config = {"traffic": False, "status": False}
    parent.status = {}
    monkeypatch.setattr(stratux.socket, "socket", lambda *_args: sock)
    thread = MainThread(parent)

    def recvfrom(_size):
        seen.append(list(weather._stores))
        thread.getout = True
        return b"", ("192.168.10.1", 4000)

    sock.recvfrom = recvfrom
    thread.run()
    assert seen == [[]]
