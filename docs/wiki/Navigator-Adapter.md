# Navigator Adapter

The `navigator_adapter` plugin reads an external GPS navigator's RS-232 output
and writes its position and guidance into the database. The panel can then fly
the navigator's course instead of the [[Flight Plan Engine]]: set `GPSSRC` to
`1`.

Plugin reference:
[`doc/plugins/navigator_adapter.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/navigator_adapter.md).

## Turning it on

The plugin ships off. In `preferences.yaml.custom`:

```yaml
enabled:
  NAVIGATOR: true
```

See [[Configuration]].

## Settings

`connections/navigator_adapter.yaml`:

| Option | Default | Meaning |
|---|---|---|
| `port` | none, required (`/dev/ttyUSB0` in the shipped file) | serial device |
| `baud` | `4800` | serial speed |
| `adapter` | none, required (`garmin_nmea` in the shipped file) | which parser to use; an unknown name fails with the list of known ones |
| `cdi_full_scale_nm` | `5.0` | cross-track that gives full CDI deflection; the shipped file suggests `0.3` for a GPS approach |

The one built-in adapter, `garmin_nmea`, reads Garmin Aviation Output 1 and
standard NMEA 0183: `RMC`, `GGA`, `GSA`, `RMB` and `APB`. It needs the
`pynmea2` Python package; without it every line is ignored.

## Keys written

| Key | From |
|---|---|
| `LAT`, `LONG` | `RMC` (valid fixes) and `GGA` (when there is a fix) |
| `GS`, `TRACK` | `RMC`, ground speed and true track |
| `GPS_ELLIPSOID_ALT` | `GGA` altitude, converted to feet |
| `GPS_FIX_TYPE` | `GGA`, with the 2D/3D mode from the preceding `GSA` |
| `EXTXTK` | `RMB` cross-track, nm, positive right of track (steer left) |
| `EXTCDI` | `-EXTXTK / cdi_full_scale_nm`, clamped to -1..+1 |
| `EXTCRS` | `APB` desired track, degrees magnetic; a true bearing is converted with the `RMC` variation, or dropped if there is none |
| `EXTTF` | `APB` TO/FROM |

The plugin reads no keys and never writes `COURSE`, `CDI` or `XTRACK`; the
`compute` `select` on `GPSSRC` does (see [[Flight Plan Engine]] → *Internal
plan or external navigator*).

## When the navigator goes quiet

The plugin does not set quality flags itself. When sentences stop, the keys go
`old` through their `tol`: 2 s for `LAT`, `LONG`, `GS`, `TRACK` and
`GPS_FIX_TYPE`, 5 s for the `EXT*` keys. If the serial port cannot be opened,
or a read fails, the plugin logs the error and stops reading; it does not
reconnect until the gateway restarts.

## Things to know

- **One writer per key.** The `garmin_gnx375` plugin writes the same `EXT*`
  keys, and `gpsd`, `xplane` and other GPS sources write `LAT`/`LONG`. Enable
  one of them.
- **The GPS bearing pointer** (`GPSBRG`) is computed from the internal plan's
  `WPLAT`/`WPLON`, not from the navigator.
