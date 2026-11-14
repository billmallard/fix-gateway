"""AER-812: a display that writes FPLCMD over netfix must get its own
FPLCMDACK back, with FPLMSG already on the bus when the ack lands.

On the bench (pyEfis FP10 map banner, 2026-10-29) the display never saw the
ack: netfix's echo inhibit was one boolean per connection, armed by the
client's FPLCMD write and consumed by the first callback to reach that
connection -- the flightplan engine's FPLCMDACK, written synchronously from
inside the same db_write -- so the ack was dropped and the FPLCMD echo sent
instead. A separate monitoring connection saw the ack, which is why it hid.
This drives the real netfix server socket in the pyavtools client's wire
format and reads what comes back to the writing connection.
"""

import socket
import time

import pytest

import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.netfix
from fixgw import cfg

PORT = 34932

NETFIX_CONFIG = f"""
type: server
host: 127.0.0.1
port: {PORT}
buffer_size: 1024
timeout: 1.0
"""


@pytest.fixture
def client(database, tmp_path):
    nc, nc_meta = cfg.from_yaml(NETFIX_CONFIG, metadata=True)
    nf = fixgw.plugins.netfix.Plugin("netfix", nc, nc_meta)
    nf.start()
    flightplan.Plugin(
        "flightplan",
        {"CONFIGPATH": str(tmp_path), "state_file": str(tmp_path / "s.json"), "restore_delay": 0.0},
        {},
    )
    time.sleep(0.1)
    sock = socket.create_connection(("127.0.0.1", PORT), timeout=0.2)
    yield sock
    sock.close()
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


def test_writer_gets_its_own_ack_after_the_reason(client):
    for key in ("FPLCMD", "FPLCMDACK", "FPLMSG"):
        client.sendall(f"@s{key}\n".encode())
    _lines(client, 0.3)                       # subscription acks

    client.sendall(b"FPLCMD;7 ACT 9;0000\n")  # no plan on the bus: rejected
    got = _lines(client, 1.0)
    keys = [x.split(";")[0] for x in got]

    assert "FPLCMDACK" in keys, got
    ack = next(x for x in got if x.startswith("FPLCMDACK;"))
    assert ack.split(";")[1] == "-7"
    assert keys.index("FPLMSG") < keys.index("FPLCMDACK"), got
    assert "FPLCMD" not in keys, got         # own write is still not echoed


def test_a_write_that_changes_nothing_does_not_eat_the_next_update(client, database):
    """With the boolean inhibit, a write whose callbacks never reached this
    connection left it armed, and it swallowed the next unrelated update."""
    client.sendall(b"@sFPLMSG\n")
    _lines(client, 0.3)
    client.sendall(b"@wDTOID;RZS\n")         # not subscribed: no echo either way
    _lines(client, 0.3)
    database.write("FPLMSG", "HELLO")         # some other writer
    got = _lines(client, 0.5)
    assert any(x.startswith("FPLMSG;HELLO") for x in got), got
