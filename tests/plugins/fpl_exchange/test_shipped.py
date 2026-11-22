"""The shipped configuration serves nothing (AER-6321, BACKLOG EFB4).

The real gateway runs from a copy of the shipped config, with the plugin's
port moved to a free one, and probes that port from inside the process once
the plugins have started. Off by default: the connection is refused and the
pending block is never written. Turned on in preferences.yaml.custom, as a
user would: the page answers and an upload reaches FPLIMPSEQ.
"""

import json
import os
import shutil
import socket
import subprocess
import sys

import yaml

DATA = os.path.join(os.path.dirname(__file__), "data")

DRIVER = """
import http.client, json, sys, threading, time
import fixgw.server as server, fixgw.plugin as plugin, fixgw.database as database
port = int(sys.argv[2])
body = open(sys.argv[3], "rb").read()
sys.argv = ["fixGw.py", "--config-file", sys.argv[1]]
args = server.main_setup()
def probe():
    time.sleep(4)
    result = {}
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/upload", body=body)
        result["status"] = c.getresponse().status
    except OSError as e:
        result["error"] = type(e).__name__
    time.sleep(0.5)
    result["FPLIMPSEQ"] = database.read("FPLIMPSEQ")[0]
    result["writers"] = sorted(k for k, v in database.writers().items() if "fpl_exchange" in v)
    print("PROBE " + json.dumps(result), flush=True)
    plugin.jobQueue.put("QUIT")
threading.Thread(target=probe, daemon=True).start()
server.run(args)
"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(tmp_path, custom):
    config = tmp_path / "config"
    shutil.copytree("src/fixgw/config", config)
    port = free_port()
    conn = config / "connections" / "fpl_exchange.yaml"
    c = yaml.safe_load(conn.read_text())
    c["fpl_exchange"]["port"] = port
    c["fpl_exchange"]["host"] = "127.0.0.1"
    conn.write_text(yaml.safe_dump(c))
    (config / "preferences.yaml.custom").write_text(custom)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([os.path.abspath("src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    p = subprocess.run([sys.executable, "-c", DRIVER, str(config / "default.yaml"), str(port),
                        os.path.join(DATA, "handmade_full.fpl")],
                       capture_output=True, text=True, timeout=60, env=env)
    lines = [line for line in p.stdout.splitlines() if line.startswith("PROBE ")]
    assert lines, p.stdout + p.stderr
    return json.loads(lines[-1][len("PROBE "):])


def test_shipped_off_serves_and_accepts_nothing(tmp_path):
    with open("src/fixgw/config/preferences.yaml") as f:
        assert yaml.safe_load(f)["enabled"]["FPL_EXCHANGE"] is False
    r = run(tmp_path, "{}\n")
    assert r == {"error": "ConnectionRefusedError", "FPLIMPSEQ": 0, "writers": []}


def test_turned_on_it_takes_an_upload(tmp_path):
    r = run(tmp_path, "enabled:\n  FPL_EXCHANGE: true\n")
    assert r["status"] == 200 and r["FPLIMPSEQ"] == 1
    assert "FPLIMPSEQ" in r["writers"] and all(k.startswith("FPLIMP") for k in r["writers"])
