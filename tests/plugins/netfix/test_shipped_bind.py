# The shipped netfix connection binds loopback only (CERT14, AER-5466).
#
# netfix has no authentication, and a value sentence with its flags omitted
# writes the value with every flag clear.  Bound to 0.0.0.0, any host that
# could reach port 3490 could set PITCH, ROLL or ALT as valid.  The shipped
# default is now 127.0.0.1; an install with a display on another host sets
# `host:` explicitly (doc/plugins/netfix.rst).
import os
import socket
import time

import pytest

import fixgw.plugins.netfix
from fixgw import cfg

SHIPPED = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "src", "fixgw", "config", "connections", "netfix.yaml"
)


def shipped_netfix(port):
    with open(SHIPPED) as f:
        conf, meta = cfg.from_yaml(f.read(), metadata=True)
    conf = conf["netfix"]
    meta = meta["netfix"]
    conf["port"] = port
    return conf, meta


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def non_loopback_address():
    # A connected UDP socket picks the outbound interface without sending
    # anything.  No route, or only loopback, means there is nothing to test.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        addr = s.getsockname()[0]
    except OSError:
        addr = None
    finally:
        s.close()
    if not addr or addr.startswith("127.") or addr == "0.0.0.0":
        return None
    return addr


def test_shipped_netfix_host_is_loopback():
    conf, _ = shipped_netfix(3490)
    assert conf["host"] == "127.0.0.1"


@pytest.fixture
def shipped_plugin(database):
    port = free_port()
    conf, meta = shipped_netfix(port)
    pl = fixgw.plugins.netfix.Plugin("netfix", conf, meta)
    pl.start()
    deadline = time.monotonic() + 3.0
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            if time.monotonic() > deadline:
                pl.stop()
                pytest.fail("shipped netfix never accepted on loopback")
            time.sleep(0.05)
    yield port
    pl.stop()


def test_shipped_netfix_accepts_loopback_and_refuses_other_interfaces(shipped_plugin, database):
    port = shipped_plugin
    # Loopback still works: pyEfis on the same host is unaffected.
    c = socket.create_connection(("127.0.0.1", port), timeout=1.0)
    c.sendall(b"PITCH;7.5\n@rPITCH\n")
    c.settimeout(1.0)
    assert c.recv(1024).decode().strip() == "@rPITCH;7.5;00000"
    c.close()

    # The same write from another interface is refused at connect.
    addr = non_loopback_address()
    if addr is None:
        pytest.skip("no non-loopback IPv4 address on this host")
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((addr, port), timeout=1.0).close()
