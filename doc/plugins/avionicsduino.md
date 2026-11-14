# AvionicsDuino plugin

`fixgw.plugins.avionicsduino` reads the CAN bus of an
[AvionicsDuino](https://avionicsduino.com) installation and writes what it
hears into the FIX database. AvionicsDuino is an open-hardware EFIS/EMS (MIT
software, CERN-OHL-W boards) whose modules talk over their own message scheme,
not CAN-FIX or CANaerospace. The main reason to read it is the wingtip remote
module's tilt-compensated magnetic heading and raw magnetometer axes.

The plugin is listen-only. It never transmits.

## Wiring

AvionicsDuino sends 11-bit ids 10-60 at 500 kbit/s. CAN-FIX reserves ids 1-255
for node alarms, so the two can never share a bus. Give this plugin its own
USB-CAN adapter on the AvionicsDuino bus, and put the adapter in listen-only
mode so it never ACKs or sends error frames there:

    ip link set can1 up type can bitrate 500000 listen-only on

## Firmware version set

The ids and layouts are version-locked upstream, so the mapping is one file
per version set. The shipped mapfile,
`config/avionicsduino/ahrs21_rm3_rdam12.yaml`, covers AHRS V2.1, remote module
V3 and RDAM V1.2, and its header records the commit of each source file the
layouts were read from. A different version set needs its own mapfile.

The decode has been tested only against synthetic frames built from those
layouts. It has not been run against a recording of a live installation.

## What it writes

| Id | Node | FIX key | Conversion |
|---|---|---|---|
| 20 | AHRS | `ROLL`, `PITCH` | roll arrives in radians (to degrees); pitch in degrees |
| 22 | AHRS | `ALAT`, `ANORM` | m/s² to g. `ANORM` is load factor, +1 in level flight |
| 24 | AHRS | `TRACK` | -180..+180 to 0..360 (true) |
| 30 | AHRS | `GS` | km/h to kt |
| 34 | AHRS | `TIMEZH`, `TIMEZM`, `TIMEZS`, `GPS_SATS_VISIBLE` | UTC |
| 36, 37 | AHRS | `LAT`, `LONG` | degrees |
| 28 | remote | `HEAD` | magnetic, 0..360 |
| 42 | remote | `OAT` | °C |
| 43, 45 | remote | `MAGX`, `MAGY`, `MAGZ` | raw sensor axes, µT to gauss |
| 48 | RDAM | `IAS`, `TAS` | whole km/h to kt, optionally smoothed |
| 60 | RDAM | `CURRNT` | bus current, A |
| 46 | RDAM | `ALT`, `VS` | **off by default**, see below |

Not written: GNSS altitude and vertical speed, humidity, the UTC date, wind,
density altitude, battery current, and the engine channels (manifold pressure,
CHT, oil, EGT, AFR) and EMS ids. They are decoded, but either no FIX key
exists, the source does not state the unit, or another plugin owns the key.
Each one sits commented out in the mapfile with the reason.

`BARO` is never written. A mapfile row that targets it is refused at load.

## ID 46: altitude and vertical speed

The RDAM computes ID 46's altitude at the QNH its own display sends it over
ESP-NOW. With pyEfis as the display nothing sends one, and the RDAM starts from
0, not 29.92, so the altitude is not usable. Turn `rdam_altitude` on only if
an AvionicsDuino display is on the RDAM and is set to the same QNH as pyEfis.

The vertical speed in ID 46 is a rate of pressure altitude and does not depend
on QNH. It is still off unless `rdam_vertical_speed` is on.

## Configuration (`connections/avionicsduino.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `interface` | `socketcan` | python-can interface |
| `channel` | `can1` | python-can channel |
| `bitrate` | `500000` | used by adapters that set their own (slcan); set socketcan with `ip link` |
| `mapfile` | `{CONFIG}/avionicsduino/ahrs21_rm3_rdam12.yaml` | one per firmware version set |
| `rdam_altitude` | `false` | write `ALT` from ID 46 |
| `rdam_vertical_speed` | `false` | write `VS` from ID 46 |
| `smoothing_tau` | `0.0` | seconds; a first-order low-pass on the mapfile's `smooth: true` rows (IAS, TAS). 0 is off |

The plugin ships off. Turn it on with `AVIONICSDUINO: true` under `enabled:`
in `preferences.yaml.custom`. Unknown options log a warning.

It writes `ROLL`, `PITCH`, `HEAD`, `LAT`, `LONG`, `IAS` and more. Do not run it
beside another source of those keys (MAVLink, Stratux, the GNX 375, DroneCAN
for the magnetometer keys, the demo).

## Quality

Staleness rides on each key's database time-to-live, as with the
`canaerospace` plugin. A node that goes quiet stops writing, and its keys go
`old` once their tol runs out; the next frame clears it. The other nodes' keys
stay fresh. A NaN or infinite value is not written, so the key ages to `old`.
AvionicsDuino has no validity or failure flags on the bus, so the plugin never
sets `bad` or `fail`.

## Status

`Nodes Seen` lists each node (AHRS, remote, RDAM, EMS) with its frame count and
the age of its last frame. `AHRS Counter Gaps` counts jumps in the AHRS's ID 10
counter, which steps by one per 20 Hz cycle, so a gap means lost frames.

## Simulator

    python -m fixgw.tools.avionicsduino_sim --interface virtual --channel tcan0
    python -m fixgw.tools.avionicsduino_sim --without remote --duration 30

It sends a steady 25° right turn with every id at its sender's rate, in the
senders' units. `--without <node>` leaves a node out, to show its keys going
`old`. `--fuzz` sends random frames.
