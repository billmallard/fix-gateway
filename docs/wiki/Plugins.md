# Plugins

Every connection FIX-Gateway makes — to a simulator, a sensor, a bus, a display,
a log file — is a **plugin**. They all share one shape (a `Plugin` class talking
to the database) and are turned on/off in the config. This page is the
catalogue; see **[[Configuration]]** to enable them and **[[Writing a Plugin]]**
to add your own.

Per-plugin reference docs live under
[`doc/plugins/`](https://github.com/billmallard/fix-gateway/tree/master/doc/plugins).

## How a plugin connects

A plugin does exactly one of three things with the database (a few do more than
one): **write** keys (a source), **read/subscribe** to keys (a consumer), or
**derive** keys from other keys (compute). It never talks to another plugin —
only to the database, which notifies whoever subscribed. That is what lets you
add a data source without changing anything downstream.

## Sources — bring data in

### Simulators
| Plugin | Brings in |
|---|---|
| `xplane` | X-Plane 11/12 — attitude, position, engine, nav-radio data (UDP / named datarefs) |
| `fgfs` | FlightGear |

### GPS & ADS-B
| Plugin | Brings in |
|---|---|
| `gpsd` | position + ground track from a `gpsd` daemon |
| `stratux` | ADS-B traffic + GPS from a Stratux receiver |

### Buses & autopilots
| Plugin | Brings in |
|---|---|
| `canfix` | the **CAN-FIX** hardware bus (SocketCAN / MCP2515) — bidirectional |
| `mavlink` | a MAVLink autopilot / flight-control system |

### Serial EMS & avionics
| Plugin | Brings in |
|---|---|
| `dynon` | Dynon serial |
| `mgl` / `mgl_serial` | MGL Avionics serial |
| `grand_rapids_eis` | Grand Rapids Technologies EIS |
| `megasquirt` | MegaSquirt ECU |
| `shadin` | Shadin air-data |
| `ifly` | iFly |
| `garmin_gnx375` | Garmin GNX-375 (GPS/NAV/XPDR) |

### Raspberry Pi hardware
| Plugin | Brings in |
|---|---|
| `rpi_bno055` | IMU attitude (BNO055) |
| `rpi_bmp085` | barometric pressure |
| `rpi_mcp3008` | 8-channel SPI ADC (analog sensors) |
| `rpi_rotary_encoder` | encoders (altimeter baro, heading/course bugs) |
| `rpi_button` | GPIO buttons |
| `rpi_virtualwire` | 433 MHz VirtualWire receiver |
| `rtl_433` | RTL-SDR 433 MHz sensors |
| `X728` / `strom_pi` | Pi UPS / power HATs |

## Consumers — send data out
| Plugin | Sends to |
|---|---|
| `netfix` | **the network** — pyEfis, moving maps, control heads, debug clients, over the [[NetFIX Protocol]] (TCP :3490). The main way anything off-box reads the database |
| `canfix` | out to CAN-FIX bus nodes (same plugin, both directions) |

## Compute & infrastructure
| Plugin | Does |
|---|---|
| `compute` | **derives keys** — TAS/density altitude, `wrap360` track, the `NAVSRC` nav-source `select`, and more (see [[Architecture]] → *The compute plugin*) |
| `quorum` | multi-source **arbitration / voting** — the right way to have more than one source for a key |
| `annunciate` | generates warning/caution/advisory annunciations from parameter limits |
| `command` | a control/command channel |
| `state_persist` | persists pilot **selections** (e.g. `NAVSRC`) across restarts |
| `data_recorder` / `data_playback` | log every parameter to file / replay it |
| `db_persister` | records values to an HDF5 file (data logging) |
| `dimmer` | display brightness control |
| `system` | host/system status into the database |
| `navigator_adapter` | flight-plan / navigator integration |
| `demo` | simulated flight data — drive a display with no hardware |

## Templates
- **`skel.py`** — the annotated starting point for a new plugin.
- **`test.py`** — a minimal plugin used in the test suite.

## Enabling & the golden rule

Add a plugin's `*_CONFIG` name to the `default.yaml` include list to load it,
remove it to unload it (**[[Configuration]]**). And before enabling two
**sources**, confirm they don't write the same keys — two writers on one key
fight (last-writer-wins). When you truly need multiple sources for a key, that's
what `quorum` is for.
