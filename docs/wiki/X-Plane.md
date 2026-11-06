# X-Plane

The `xplane` plugin feeds the database from X-Plane 11 or 12 over UDP, so a
panel can be flown on the bench before it is in an airplane. It reads X-Plane's
Data Output rows and named datarefs, and can write datarefs back.

Plugin reference:
[`doc/plugins/xplane.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/xplane.rst).
It is off in the shipped config: set `XPLANE: true` and turn `DEMO` off, since
both write attitude (see [[Configuration]]).

## X-Plane settings

- **Data Output:** send the rows below by UDP to the gateway machine's address,
  port `udp_in` (49001).
- **Network:** the gateway sends to `udp_out` (49000), X-Plane's legacy receive
  port. The newer port (49010) does not accept dataref requests.
- The host firewall on the X-Plane machine must allow inbound UDP on 49000, or
  the dataref requests are dropped silently while Data Output still arrives.

## Settings

| Option | Default | Meaning |
|---|---|---|
| `ipaddress` | `127.0.0.1` | X-Plane's address, where requests and writes are sent |
| `udp_in` | `49001` | local port the gateway listens on |
| `udp_out` | `49000` | X-Plane's port |
| `recv` | `{}` | Data Output rows to keys |
| `datarefs` | `{}` | named datarefs to keys |
| `dataref_hz` | `10` | dataref update rate requested |
| `rref_resubscribe` | `5.0` s | how often the dataref requests are resent, so they survive an X-Plane restart |
| `dataref_writes` | `{}` | keys written back to datarefs |
| `send`, `send_interval` | `{}`, `0.1` s | keys sent as Data rows (commented out in the shipped file) |

## Data Output rows (`recv`)

Each row index maps its eight values to keys; `_` skips a value. The shipped
file, laid out for X-Plane 12:

```yaml
recv:
  3:  [IAS, _, TAS, GS, _, _, _, _]
  17: [PITCH, ROLL, _, _, HEAD, MAGVAR, _, _]
  20: [LAT, LONG, ALT, AGL, _, _, _, _]
```

It also maps rows 4 (`VS`, `ALAT`), 16 (`ROT`) and 18 (`AOA`, `TRACK`, `VPATH`).
Values of -999 (X-Plane's "no data") are dropped.

## Named datarefs (`datarefs`)

`KEY: dataref`, or `KEY: [dataref, scale]`:

```yaml
datarefs:
  EXTCRS:  sim/cockpit/radios/gps_course_degtm
  EXTCDI:  [sim/cockpit/radios/gps_hdef_dot, 0.4]
```

The shipped file subscribes the X-Plane GPS as the external navigator
(`EXTCRS`, `EXTCDI`, `EXTTF`; select it with `GPSSRC` = 1, see
[[Flight Plan Engine]]), both NAV radios' course, deviation, glideslope and
TO/FROM, the VOR bearings, and `HEADBUG`.

## Writing back (`dataref_writes`)

```yaml
dataref_writes:
  XPHSISRC: sim/cockpit2/radios/actuators/HSI_source_select_pilot
```

A write is sent only when the key changes. In the shipped entry `XPHSISRC` is
`NAVSRC` (through `compute`), so X-Plane's pilot HSI source follows the source
selected on the panel, and the sim's own autopilot tracks it.

## Staleness

The plugin has no timeout of its own. When X-Plane stops sending, keys go `old`
through their `tol` (`PITCH` 300 ms, `IAS` 2 s, `EXTCRS` 5 s), and the panel
flags them.
