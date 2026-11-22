# MAVLink plugin

`fixgw.plugins.mavlink` talks to an ArduPilot (Pixhawk) flight controller over
a serial MAVLink link. It is mainly a GPS, AHRS and air-data source. It also
sends mode commands, and with `autopilot` on it is the autopilot source for
the keys in [`doc/autopilot_keys.md`](../autopilot_keys.md).

## Configuration (`connections/mavlink.yaml`)

| Option | Shipped | What it turns on |
|---|---|---|
| `airspeed` | `true` | `IAS`, from `VFR_HUD` |
| `groundspeed` | `true` | `GS`, from `VFR_HUD` |
| `gps` | `true` | `LAT`, `LONG` and the `GPS_*` quality keys |
| `ahrs` | `true` | attitude, `HEAD`, `TRACK`, `TAS`, `VS`, `AGL`, `TALT`, and `ALT` when `pressure` is on too |
| `accel` | `true` | `ALAT`, `ALONG`, `ANORM`, from `SCALED_IMU` |
| `pressure` | `true` | `AIRPRESS` and `DIFFAIRPRESS`, from `SCALED_PRESSURE` |
| `pascal_offset` | `2800` | Pa added to the static pressure, a calibration for a sensor that reads the change well but the absolute value poorly |
| `autopilot` | `true` | the autopilot state keys, and `APCMD` requests |

With `ahrs` off the plugin owns no attitude or altitude key, so another source
can (AER-6068).

## Altitude

| Key | From | Meaning |
|---|---|---|
| `ALT` | `SCALED_PRESSURE.press_abs` plus `pascal_offset`, and `BARO` | indicated altitude, what the altimeter tape draws |
| `TALT` | `GLOBAL_POSITION_INT.alt` | true altitude: the autopilot's GPS/EKF height above MSL |
| `AGL` | `GLOBAL_POSITION_INT.relative_alt` | height above the autopilot's home |
| `AIRPRESS` | `SCALED_PRESSURE.press_abs` plus `pascal_offset` | static pressure, Pa |

`ALT` is the ISA pressure altitude of the static pressure, read against the
altimeter setting the way an altimeter's setting window shifts its scale:

    ALT = pressure_altitude_ft(AIRPRESS) - baro_offset_ft(BARO)

Both functions are in `fixgw/airdata.py`, and `baro_offset_ft` is the one
compute's `PALT` uses, so `PALT` comes back as the pressure altitude. It
reads 0 ft when `BARO` equals the static pressure. Turning the baro knob moves
`ALT` on the next `SCALED_PRESSURE` (the plugin asks for 5 a second), and
does not move `TALT`.

- `ALT` is published `bad` while `BARO` is bad, failed or outside 25 to
  33 inHg, or when the value is outside `ALT`'s range. Without the flag the
  database would clamp it to a plausible-looking limit.
- **No pressure, no `ALT`.** With `pressure` off, or before the first
  `SCALED_PRESSURE`, the plugin does not write `ALT`. It never writes the GPS
  height there. Unwritten, `ALT` goes old on its own tol, and the tape shows
  that. A connect zeroes the attitude keys but not `ALT`, because a fresh 0 ft
  would read as a good altitude.

Before AER-6351 the plugin wrote `GLOBAL_POSITION_INT.alt` into both `ALT` and
`TALT`, so the tape showed GPS height and ignored the baro knob.

## GUIDED and the reposition altitude

A `GUIDED` request (the `NAV` button, or `MAVREQGUIDED`) sends
`MAV_CMD_DO_REPOSITION` to `WPLAT`/`WPLON` in `MAV_FRAME_GLOBAL`, whose
altitude is MSL. The plugin sends `TALT`, converted to metres, so the autopilot
holds the altitude it is at. It does not send `ALT`, which differs from MSL by
the baro correction and by the temperature error.

If `TALT` is not a usable reading (never written, old, bad or failed), the
plugin sends nothing and writes `No MSL Altitude` to `MAVMSG`. That happens
with `ahrs` off when nothing else writes `TALT`.
