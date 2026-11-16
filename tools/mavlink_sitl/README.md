# ArduPlane SITL under the MAVLink plugin (AFC7, AER-5822)

These scripts fly ArduPlane's software-in-the-loop simulator under the real
`mavlink` plugin in the real fixgw server. A netfix client plays the control
head (`APCMD`/`APCMDSEQ`, and one `MAVREQ*` button write), and a second
MAVLink link plays the pilot's transmitter (mode switch changes the head did
not ask for). They produced the test fixture
`tests/plugins/mavlink/data/arduplane_4.7.2_modes.tlog` and the two logs here.
No X-Plane and no hardware are involved.

| File | What it is |
|---|---|
| `bridge.py` | A pty for the plugin's serial port, relayed to SITL's TCP port. Records every message the autopilot sends as a tlog. Touching `<out>/ctl/pause` cuts the link both ways. |
| `session.py` | The session: start SITL, the bridge and a private fixgw (`fixgw.server:main`, MAVLINK on, netfix on a free port), then run the sequence below and log every key with its old flag. |
| `cut_fixture.py` | Cuts the recorded `fc.tlog` to the autopilot's own fixed-wing heartbeats, keeping each record's exact wire bytes. |
| `disarm_probe.py` | A separate SITL instance with no plugin: does a disarmed ArduPlane still drive its control surfaces? |
| `reengage.py` | The same session cut short to show a pre-existing hazard: after the transmitter takes ArduPlane out of GUIDED, the next waypoint change puts it back in GUIDED. |
| `session_4.7.2.log` | The session that made the fixture. |
| `reengage_4.7.2.log` | `reengage.py`'s run. The autopilot's own heartbeats read MANUAL until 0.39 s after the waypoint write, then GUIDED. |
| `disarm_probe_4.7.2.txt` | The probe's output. |

## Regenerate

```sh
cd tools/mavlink_sitl
B=https://firmware.ardupilot.org/Plane/stable/SITL_x86_64_linux_gnu
curl -sO $B/arduplane && curl -sO $B/firmware-version.txt && chmod +x arduplane
curl -s -o plane.parm https://raw.githubusercontent.com/ArduPilot/ardupilot/fb773567896014da007bca58358704d4a0d8516f/Tools/autotest/models/plane.parm
python3 -m pip install --target deps pymavlink==2.4.42 pyserial==3.5
PYTHONPATH=deps python3 session.py ../.. out
PYTHONPATH=deps python3 cut_fixture.py out/fc.tlog ../../tests/plugins/mavlink/data/arduplane_4.7.2_modes.tlog
PYTHONPATH=deps python3 disarm_probe.py probe
PYTHONPATH=deps python3 reengage.py ../.. re
```

The recording used `firmware-version.txt` `4.7.2-FIRMWARE_VERSION_TYPE_OFFICIAL`
(ArduPilot commit fb773567). The binary, the params, `deps/` and `out/` are not
committed. SITL instances 7 (TCP 5830/5832) and 8 (5840) are used, so two
sessions can run on one host. A run takes about two minutes, most of it the
takeoff climb.

## The sequence

1. Wait for the plugin's link and for it to arm ArduPlane (it arms by itself
   once the pre-arm checks pass).
2. Head: `HDG` (not available), `NAV` (refused, autopilot not engaged).
3. Transmitter: TAKEOFF, climb to 250 m. Head: `AP` (refused: the plugin's
   status is ERROR in a mode it has no name for).
4. Transmitter: CRUISE. Head: `NAV` (GUIDED to `WPLAT`/`WPLON`), `NAV`
   (back to CRUISE), `VS` (not available).
5. Button path: `MAVREQAUTOTUNE` written directly, as
   `buttons/auto-pilot-*.yaml` do. AUTOTUNE.
6. Head: `AP` (MANUAL; `APDISC` latched), `DISC` (acknowledged), `AP`
   (CRUISE).
7. Transmitter: FBWA. Head: `DISC` (refused, status ERROR). Transmitter:
   CRUISE, then MANUAL (`APDISC` latched by a disconnect the head did not
   make). Head: `DISC`, `AP` (CRUISE again, although the plugin last set
   CRUISE itself).
8. Cut the link for 8 s, press `AP` during the cut (refused), restore it.

A second netfix connection rewrites `WPLAT`/`WPLON` every 5 s, as the
flight-plan engine does; without it they go old (50 s tol) and `NAV` is
refused for want of a waypoint.
