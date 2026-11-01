# AER-4476: start the real gateway from each shipped configuration that has a
# data source, let it run, and check that no FIX key was written by more than
# one plugin.  bench_gcu.yaml loads only the netfix server, which writes
# nothing until a client does; data_playback.yaml needs a recording.

import json
import os
import shutil
import subprocess
import sys

import pytest

DRIVER = """
import json, sys, threading, time
import fixgw.server as server, fixgw.plugin as plugin, fixgw.database as database
seconds = float(sys.argv[2])
sys.argv = ["fixGw.py", "--config-file", sys.argv[1]]
args = server.main_setup()
def stop():
    time.sleep(seconds)
    print("WRITERS " + json.dumps(database.writers()), flush=True)
    plugin.jobQueue.put("QUIT")
threading.Thread(target=stop, daemon=True).start()
server.run(args)
"""

# Keys the shipped configs are known to give two writers, each with the issue
# that removes it.  The test asserts this set exactly, so fixing one fails
# here until its entry is deleted.
KNOWN = {
    # demo writes the canonical nav keys that compute's NAVSRC select owns.
    "default.yaml": {
        "CDI": ["demo", "compute"],  # AER-4475
        "COURSE": ["demo", "compute"],  # AER-4475
        "GSI": ["demo", "compute"],  # AER-4475
    },
}


def run_gateway(config, seconds):
    env = dict(os.environ)
    src = os.path.abspath("src")
    env["PYTHONPATH"] = os.pathsep.join(
        [src] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    p = subprocess.run(
        [sys.executable, "-c", DRIVER, config, str(seconds)],
        capture_output=True,
        text=True,
        timeout=seconds + 30,
        env=env,
    )
    lines = [l for l in p.stdout.splitlines() if l.startswith("WRITERS ")]
    assert lines, "gateway did not report: " + p.stdout + p.stderr
    return json.loads(lines[-1][len("WRITERS ") :]), p.stderr


@pytest.mark.parametrize("name", ["default.yaml"])
def test_shipped_config_has_one_writer_per_key(name, tmp_path):
    # Run from a copy: plugins write state files beside their config.
    config = tmp_path / "config"
    shutil.copytree("src/fixgw/config", config)
    writers, log = run_gateway(str(config / name), 5)
    # The check only proves something if it saw writes: the default config's
    # demo, compute and flightplan plugins must all have been attributed.
    seen = {w for ws in writers.values() for w in ws}
    assert {"demo", "compute", "flightplan"} <= seen, writers
    dual = {k: v for k, v in writers.items() if len(v) > 1}
    assert dual == KNOWN.get(name, {})
    for key in dual:
        assert "FIX key {} has a second writer".format(key) in log
