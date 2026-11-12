# Plugins

Every connection FIX-Gateway makes — to a simulator, a sensor, a bus, a display,
a log file — is a **plugin**. They all share one shape (a `Plugin` class talking
to the database) and are turned on/off in the config. This page is the
catalogue; see **[[Configuration]]** to enable them and **[[Writing a Plugin]]**
to add your own.

Per-plugin reference docs live under
[`doc/plugins/`](https://github.com/billmallard/fix-gateway/tree/dev/doc/plugins).

## How a plugin connects

A plugin does exactly one of three things with the database (a few do more than
one): **write** keys (a source), **read/subscribe** to keys (a consumer), or
**derive** keys from other keys (compute). It never talks to another plugin —
only to the database, which notifies whoever subscribed. That is what lets you
add a data source without changing anything downstream.

Plugins on in the shipped config: `netfix`, `compute`, `annunciate`, `system`,
`demo`, `flightplan` and `state_persist`. Turn others on with their `enabled:`
flag (**[[Configuration]]**). The one plugin marked *not wired* ships no
connection file; write one and wire it in as **[[Configuration]]** describes.

## Sources — bring data in

### Simulators
| Plugin | Brings in |
|---|---|
| `xplane` | X-Plane 11/12 over UDP — air data, attitude, position, nav radios and the X-Plane GPS; writes the HSI source back. See **[[X-Plane]]** |
| `fgfs` | FlightGear generic protocol — the same keys as `xplane` |
| `demo` | canned values and a scripted attitude/altitude/heading sequence — drives a display with no hardware |
| `data_playback` | replays `data_recorder` files (`--playback-start-time`, or `data_playback.yaml`) |

### GPS, navigators & ADS-B
| Plugin | Brings in |
|---|---|
| `gpsd` | position, track, ground speed, GPS altitude, fix type and accuracy from a `gpsd` daemon |
| `stratux` | GDL90 from a Stratux: ADS-B traffic (`TFC*`), AHRS and ground speed; no position |
| `navigator_adapter` | an external navigator's RS-232 NMEA: position and `EXT*` guidance, see **[[Navigator Adapter]]** |
| `garmin_gnx375` | Garmin GNX 375 NMEA: position and `EXT*` guidance (GPS and nav only) |
| `ifly` | next waypoint (`WPNAME`, `WPLAT`, `WPLON`) from the iFly EFB over UDP; experimental |

### Buses & autopilots
| Plugin | Brings in |
|---|---|
| `canfix` | the **CAN-FIX** bus — bidirectional. See **[[CAN-FIX]]** |
| `canaerospace` | CANaerospace engine data (Rotax 912iS / 915iS ECU), listen-only, see **[[CANaerospace Input]]** |
| `dronecan` | DroneCAN v0 sensors; listen-only unless given a node id |
| `avionicsduino` | an AvionicsDuino CAN bus (AHRS, wingtip magnetometer, RDAM air data), listen-only, on its own adapter |
| `mavlink` | an ArduPilot / Pixhawk link, mainly as a GPS, AHRS and air-data source; can send mode commands |
| `megasquirt` | MegaSquirt ECU over CAN: `TACH1`, `FUELF1`, `H2OT1`, `MAP1`, `THR1` |
| `mgl` | MGL RDAC over CAN |

### Serial EMS & avionics
| Plugin | Brings in |
|---|---|
| `dynon` | Dynon D10/D100 serial stream |
| `mgl_serial` | MGL Avionics serial |
| `grand_rapids_eis` | Grand Rapids Technologies EIS |
| `shadin` | Shadin fuel protocol: `FUELF1`, `FUELF2`, `FUELQT` |
| `onspeed` | OnSpeed Gen2 AOA: `AOA` and `AOA.Warn` |

### Raspberry Pi & panel hardware
| Plugin | Brings in |
|---|---|
| `rpi_bno055` | IMU attitude (BNO055) |
| `rpi_bmp085` | barometric pressure and altitude (BMP085) |
| `rpi_mcp3008` | 8-channel SPI ADC, to `VKEY1`…`VKEY8` |
| `rpi_rotary_encoder` | one GPIO encoder writing a counter key, with an optional button-held second key |
| `rpi_button` | GPIO buttons |
| `rpi_virtualwire` | VirtualWire radio receiver for `IAS` / `OAT` |
| `rtl_433` | sensors decoded by `rtl_433` on an RTL-SDR; shipped for tire pressure |
| `knobster` | Sim Innovations Knobster USB encoder |
| `rais` | a RAIS sensor pipeline |
| `X728` / `strom_pi` | Pi UPS boards: power-fail and battery keys, and shutdown after a delay |

## Consumers — send data out
| Plugin | Sends to |
|---|---|
| `netfix` | **the network** — pyEfis, moving maps, control heads, debug clients, over the [[NetFIX Protocol]] (TCP :3490). Loopback only by default; a client on another host needs `host:` set (see the plugin doc) |
| `canfix` | out to CAN-FIX bus nodes (same plugin, both directions) |
| `data_recorder` | every key (or those matching `key_prefixes`) to hourly JSON files |
| `db_persister` | keys matching a regex to an HDF5 file — *not wired* |
| `dimmer` | `DIM` to a backlight brightness file |

## Compute & infrastructure
| Plugin | Does |
|---|---|
| `compute` | **derives keys** — pressure and density altitude, `wrap360` track, the `NAVSRC` and `GPSSRC` source selects, and more (see [[Architecture]] → *The compute plugin*) |
| `flightplan` | the GPS navigator: flies a route and writes guidance — see **[[Flight Plan Engine]]** |
| `quorum` | leader election between redundant gateways |
| `annunciate` | sets a key's `annunciate` flag when it crosses its alarm limits (`lowAlarm`/`highAlarm` by default) |
| `command` | an interactive console on the gateway's terminal (read, write, list, subscribe, flag) |
| `state_persist` | persists pilot **selections** (`NAVSRC`, `GPSSRC`) across restarts |
| `system` | the clock: `TIMEZ*` and `TIMEL` keys, once a second |

## Templates
- **`skel.py`** — the annotated starting point for a new plugin.
- **`test.py`** — a minimal plugin used in the test suite.

## Enabling & the golden rule

Set a plugin's flag under `enabled:` in `preferences.yaml.custom` to load it,
or clear it to unload it (**[[Configuration]]**). And before enabling two
**sources**, confirm they don't write the same keys — two writers on one key
fight (last-writer-wins).
