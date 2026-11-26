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
`demo`, `flightplan`, `state_persist` and `cas`. Turn others on with their `enabled:`
flag (**[[Configuration]]**). The one plugin marked *not wired* ships no
connection file; write one and wire it in as **[[Configuration]]** describes.

## Sources — bring data in

### Simulators
| Plugin | Brings in |
|---|---|
| `xplane` | X-Plane 11/12 over UDP — air data, attitude, position, nav radios and the X-Plane GPS; writes the HSI source back, and is the autopilot source when its `autopilot:` block is on (as shipped). See **[[X-Plane]]** |
| `fgfs` | FlightGear generic protocol — the same keys as `xplane` |
| `demo` | a simulated flight, looped: the default profile climbs out of KSBA, flies V27 with 30 s of no GPS fix, then the KSMX RNAV (GPS) RWY 30 approach, the missed approach and the hold, and the real flight-plan engine guides it. It also writes engine-monitor values and, with `traffic: true` (the default), a few ADS-B targets around the hold. Needs `pip install 'fixgw[sim]'`, and refuses to run beside another source of its keys |
| `data_playback` | replays `data_recorder` files (`--playback-start-time`, or `data_playback.yaml`) |

### GPS, navigators & ADS-B
| Plugin | Brings in |
|---|---|
| `gpsd` | position, track, ground speed, GPS altitude, fix type and accuracy from a `gpsd` daemon |
| `stratux` | GDL90 from a Stratux: ADS-B traffic (`TFC*`), AHRS and ground speed. The receiver's own position, integrity and GPS altitude go to its `STX*` keys, never `LAT`/`LONG`, and receiver health and GPS quality come from its `/getStatus` page. AHRS over Wi-Fi drives the attitude display: set `source:` to the receiver's address (the health poll needs it too). Traffic matching `ownship_address` (in the connection file, or top-level in `preferences.yaml.custom`) is not shown. With `fisb: true` (off by default; the receiver needs UAT enabled) it also decodes FIS-B text weather and NEXRAD into the plugin, not into FIX keys; a NetFIX client asks for one station's text weather with `@xweather` ([[NetFIX Protocol]]). See [`doc/plugins/stratux.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/stratux.md) |
| `navigator_adapter` | an external navigator's RS-232 NMEA: position and `EXT*` guidance, see **[[Navigator Adapter]]** |
| `garmin_gnx375` | an alias for `navigator_adapter` with the `garmin_nmea` adapter, kept for existing installs; a new install enables `navigator_adapter` |
| `ifly` | next waypoint (`WPNAME`, `WPLAT`, `WPLON`) from the iFly EFB over UDP; experimental |

### Buses & autopilots
| Plugin | Brings in |
|---|---|
| `canfix` | the **CAN-FIX** bus — bidirectional. See **[[CAN-FIX]]** |
| `canaerospace` | CANaerospace engine data (Rotax 912iS / 915iS ECU), listen-only, see **[[CANaerospace Input]]** |
| `dronecan` | DroneCAN v0 sensors; listen-only unless given a node id |
| `avionicsduino` | an AvionicsDuino CAN bus (AHRS, wingtip magnetometer, RDAM air data), listen-only, on its own adapter |
| `mavlink` | an ArduPilot / Pixhawk link, mainly as a GPS, AHRS and air-data source. `ALT` is indicated altitude from the static pressure and `BARO`; the autopilot's MSL height goes to `TALT`. With `autopilot: true` (shipped) it is also the autopilot source: it writes the autopilot state keys from the ArduPlane heartbeat and turns `APCMD` requests into mode changes (`AP`, `NAV` and `DISC` only). See [`doc/plugins/mavlink.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/mavlink.md) |
| `megasquirt` | MegaSquirt ECU over CAN: `TACH1`, `FUELF1`, `H2OT1`, `MAP1`, `THR1` |
| `mgl` | MGL RDAC over CAN |

### Serial EMS & avionics
| Plugin | Brings in |
|---|---|
| `dynon` | Dynon D10/D100 serial stream |
| `mgl_serial` | MGL Avionics serial |
| `grand_rapids_eis` | Grand Rapids Technologies EIS |
| `shadin` | Shadin fuel protocol: `FUELF1`, `FUELF2`, `FUELQT` |
| `onspeed` | OnSpeed Gen2 AOA: `AOA` and `AOA.Warn`. Options `attitude` (`PITCH`, `ROLL`), `airspeed` (`IAS`) and `altitude` (`ALT`, corrected to `BARO`) add air data for an installation where OnSpeed is the source; all off by default. See [`doc/plugins/onspeed.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/onspeed.md) |

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
| `sdr_vor` | a VOR or localizer decoded from an RTL-SDR (`rtl_sdr`) into one NAV slot, NAV2 by default: needle, TO/FROM, type and bearing, tuned from `NAV2FREQ`. **Supplemental, not a certified nav receiver, day VFR situational awareness**; never presented as a certified, approach or IFR source, and marked supplemental on the HSI (`NAVSUPP`); no glideslope. See [`docs/sdr_vor.md`](https://github.com/billmallard/fix-gateway/blob/dev/docs/sdr_vor.md) |
| `knobster` | Sim Innovations Knobster USB encoder |
| `tessera` | a Tessera touch panel over USB serial as a control head: its keys show FIX values, and a press writes the FIX keys its page file names; experimental, see [`doc/plugins/tessera.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/tessera.md) |
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
| `gdl90_out` | EFB apps (ForeFlight and others) over Wi-Fi: GDL-90 by unicast UDP with the panel's position, ForeFlight's AHRS message (`ahrs: true`), and the `TFC` traffic block if `traffic: true`. It sends to tablets found by ForeFlight's discovery broadcast (UDP 63093) and to those listed in `clients:` (port 4000 by default). Reads keys, writes none; supplemental only; off by default. See [`doc/plugins/gdl90_out.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/gdl90_out.md) |
| `fpl_exchange` | an EFB app, as a Garmin `.fpl` file: a small web page (port 8080) offers the loaded plan as a download and takes an upload. An upload becomes a pending plan on the `FPLIMP*` keys, which is never flown until the pilot accepts it (see [[Flight Plan Engine]]). The page has no login: set `allow:` to the Wi-Fi network. Off by default. See [`doc/plugins/fpl_exchange.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/fpl_exchange.md) |

## Compute & infrastructure
| Plugin | Does |
|---|---|
| `compute` | **derives keys** — pressure and density altitude, `wrap360` track, the `NAVSRC` and `GPSSRC` source selects, and more (see [[Architecture]] → *The compute plugin*) |
| `flightplan` | the GPS navigator: flies a route and writes guidance — see **[[Flight Plan Engine]]** |
| `quorum` | leader election between redundant gateways |
| `annunciate` | sets a key's `annunciate` flag when it crosses its alarm limits (`lowAlarm`/`highAlarm` by default). A key may be named with a database variable (`CHTec` is every cylinder of every engine). In the shipped config the low CHT and EGT alarms wait `TACH1.warmup` seconds (30) after engine start; high alarms do not wait |
| `cas` | **crew alerting**: watches the keys its catalogue names and publishes the sorted warning, caution and advisory list (`CASMSG1`..`CASMSG12`, `CASCNT`, `CASTOP`, `CASSEQ`) and `NAVFAIL`. A display acknowledges by writing the `CASSEQ` it shows to `CASACK`, and a panel button can write `CASACKBTN`; on by default, see [`doc/plugins/cas.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/cas.md) |
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
[`src/fixgw/database/writers.yaml`](https://github.com/billmallard/fix-gateway/blob/dev/src/fixgw/database/writers.yaml)
lists, for each key, the plugins that write it. It is a record only: the
gateway does not load it.
