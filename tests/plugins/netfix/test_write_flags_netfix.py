"""AER-6386: a client's write must not get the key's previous value sent back
to it when its flags set off another plugin.

On the bench (AFC2b glass check, pyEfis AFC3 panel) one ALT SEL notch in five
was lost. The BARO edit had moved ALT past the altitude alerter, so ALTSEL's
annunciate flag was set and pyEfis held it. pyAvTools sends the client's own
copy of that flag with every value. pyEfis's first notch was a new target, so
the alerter cleared the flag inside the write, where netfix sends the writer
nothing, and pyEfis kept the stale flag. On the second notch netfix applied the
stale flag BEFORE the value and outside the writer's echo inhibit. The alerter
ran on the old target, cleared the flag again, and ALTSEL was published to
pyEfis with the previous value. pyEfis took that as its copy, and the third
notch wrote the second notch's value again. This drives the real netfix server
socket, the real database and the real alerter, in the pyAvTools wire format.
"""

import socket
import time

import pytest

import fixgw.plugins.annunciate as annunciate
import fixgw.plugins.netfix
from fixgw import cfg
from fixgw import database as fixdatabase

PORT = 34933

NETFIX_CONFIG = f"""
type: server
host: 127.0.0.1
port: {PORT}
buffer_size: 1024
timeout: 1.0
"""

# As shipped (connections/annunciate.yaml).
ALTDEV = {"key": "ALTSEL", "deviation_from": "ALT", "threshold": 200,
          "deadband": 20, "arm_on_capture": True}


@pytest.fixture
def client(database):
    nc, nc_meta = cfg.from_yaml(NETFIX_CONFIG, metadata=True)
    nf = fixgw.plugins.netfix.Plugin("netfix", nc, nc_meta)
    nf.start()
    alerter = annunciate.Plugin("annunciate", {"defaults": {}, "items": [ALTDEV]}, None)
    alerter.run()
    for key in ("ALT", "ALTSEL"):
        item = fixdatabase.get_raw_item(key)
        item.bad = item.fail = item.old = False
    fixdatabase.write("ALTSEL", 7500)
    fixdatabase.write("ALT", 7500.0)          # within 200 ft: the alerter arms
    time.sleep(0.1)
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=0.2)
    yield sock
    sock.close()
    alerter.stop()
    nf.stop()


def _lines(sock, seconds):
    out, buf, end = [], b"", time.time() + seconds
    while time.time() < end:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            continue
        if not data:
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            out.append(line.decode())
    return out


def _altsel(lines):
    return [x.split(";") for x in lines if x.startswith("ALTSEL;")]


def _alert_set(sock):
    sock.sendall(b"@sALTSEL\n")
    _lines(sock, 0.2)
    fixdatabase.write("ALT", 7730.0)          # 230 ft off: the alerter latches
    got = _altsel(_lines(sock, 0.3))
    assert got and got[-1][1] == "7500" and got[-1][2][0] == "1", got


def test_stale_alert_flag_does_not_send_the_writer_its_previous_value(client):
    _alert_set(client)
    # Two notches. The client still holds the alert flag (pyAvTools sends the
    # client's copy), so the second write carries it as well.
    client.sendall(b"ALTSEL;7600;1000\n")
    _lines(client, 0.15)
    client.sendall(b"ALTSEL;7700;1000\n")
    after = _altsel(_lines(client, 0.3))

    assert fixdatabase.read("ALTSEL")[0] == 7700
    assert [x for x in after if int(x[1]) != 7700] == [], after


def test_writer_is_told_when_its_write_cleared_the_alert(client):
    _alert_set(client)
    client.sendall(b"ALTSEL;7600;1000\n")      # a new target disarms the alerter
    got = _altsel(_lines(client, 0.3))

    assert fixdatabase.read("ALTSEL")[1] is False
    assert got == [["ALTSEL", "7600", "00000"]], got


def test_write_that_leaves_the_flags_as_sent_is_not_echoed(client):
    client.sendall(b"@sALTSEL\n")
    _lines(client, 0.2)
    client.sendall(b"ALTSEL;7600;0000\n")
    assert _altsel(_lines(client, 0.3)) == []
