# AER-6622 (TESS2): the Tessera plugin on a real panel, Beelink bench

2026-11-25. GUITION JC1060P470C board 1 (ESP32-P4 revision v3.2), Tessera
firmware 1.2.12 (`tessera-p4-v3`, protocol 4), on the Beelink simulator bench
at `/dev/tessera` (udev rule `70-tessera.rules`). fix-gateway at
`aer-6622/tessera-v3` 47483dc, staged with
`bench-deploy.sh --ref fix-gateway=aer-6622/tessera-v3`. Page: the shipped
`config/tessera/page.yaml` (10 keys; a 694-byte layout, 4 lines and 744 bytes
framed). The Knobster ran beside it on the same fixgw.

## Handshake

Before this branch, the plugin refused the panel: `Tessera: a different device
(board 'tessera-p4-v3')`. With it, fixgw's status (`@xstatus` on netfix) read:

```
Connection: tessera {"Running": true, "Port": "/dev/tessera", "Connected": true,
  "Firmware": "1.2.12", "Boot Id": 3317780319, "Connects": 1, "Refused": 0,
  "Pushes": 2, "Acks": 2, "Resends": 0, "Invalid": 0, ...}
```

## Live-value refresh rate (`refresh_probe.py`)

Writes BARO over a raw netfix socket and counts the plugin's page pushes and
the panel's `AD_CFG_END OK` answers across the window. BARO is put back after.

| Run | BARO writes | Pushes | Acks (pages accepted) | Resends |
|---|---|---|---|---|
| `min_refresh: 0.1` (shipped), `python3 refresh_probe.py 50 30` | 1500 at 50 Hz | 287 | 287, 9.57/s | 0 |
| `min_refresh: 0.0` (one run, reverted after), `python3 refresh_probe.py 100 30` | 3000 at 100 Hz | 871 | 871, 29.03/s | 0 |

The shipped page refreshes at the plugin's own cap (0.1 s), and the panel
answers every push. Uncapped, one push and its answer take about 34 ms on
average, so the panel is not the limit at 10/s. An answer means the firmware
parsed and accepted the page; how soon the glass shows it was not measured
(that needs a camera on the panel).

## Press to FIX write (`press_probe.py`)

`AD_PRESS <id>` is the firmware's own synthetic press. It calls
`deck_model_record_press`, which queues the same `record_event` a touch tile's
click does (`main/main.cpp`, `main/deck_model.c`, `main/ui/ui.cpp` at v1.2.12,
f1697322). The probe writes it to the port from a second writer (`os.open`,
no termios or modem-line change, so the panel does not reset) while fixgw's
plugin holds the port, and times the netfix update of the key the press
writes.

| Run | Presses | Timed | ms min / median / p90 / max |
|---|---|---|---|
| key 7 (HDG SYNC), plugin pulses `TSBTN130`: `python3 press_probe.py 7 TSBTN130 20 HEADBUG` | 20 | 20 | 1.7 / 1.9 / 2.0 / 41.0 |
| key 6 (HDG +), pyEfis answers by writing `HEADBUG`: `python3 press_probe.py 6 HEADBUG 10 HEADBUG any` | 10 | 10 | 3.4 / 3.7 / 33.0 / 40.8 |

The second run shows pyEfis acting on the panel: 10 presses moved HEADBUG
from 0 to 10, one step each, and HEADBUG was put back to 0.

Not in these numbers: the touch controller's scan and the finger lift. A tile
fires on `LV_EVENT_CLICKED`, at release. A real touch also waits for the
firmware's USB loop, whose reads return within 20 ms (`main.cpp`). A
`AD_PRESS` arrives through that loop, so it never waits.

## Not measured here

- Press to glass, the refresh as seen on the glass, legibility, gloves: a
  camera and a person at the panel.
- Whether a panel restart re-enumerates USB, and a physical unplug and replug.
- Touch picks the knob's target: pyEfis has no way to take focus from the bus
  (TESS1, AER-5754), so a panel key cannot select what the Knobster turns.

## Run it

On the bench, as `pyefis`, under the bench lock:

```
flock -w 900 /tmp/pyefis-bench.lock python3 refresh_probe.py 50 30
flock -w 900 /tmp/pyefis-bench.lock python3 press_probe.py 7 TSBTN130 20 HEADBUG
flock -w 900 /tmp/pyefis-bench.lock python3 press_probe.py 6 HEADBUG 10 HEADBUG any
```

Both probes talk to netfix on a raw socket. Do not use
`fixgw.netfix.Client.stop()` against a shared gateway: it sends `@xkill`,
which stops fixgw.
