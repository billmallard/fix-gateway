# GDL-90 output plugin (`gdl90_out`)

`fixgw.plugins.gdl90_out` sends the panel's own position, attitude and
(optionally) traffic to EFB apps such as ForeFlight, as GDL-90 over Wi-Fi UDP.
It is the feed half of CAP-720 (BACKLOG EFB1, AER-6142).

It is **supplemental, situational awareness only.** It reads FIX keys and
writes none, so one-key-one-writer is untouched, and nothing the tablet sends
reaches the bus: the only input is the discovery listener below, which only
decides where to send. The plugin ships off (`GDL90_OUT: false`).

Sources, read clean-room: the FAA *GDL 90 Data Interface Specification*,
560-1058-00 Rev A (cited G§), and ForeFlight's public *GDL 90 Extended
Specification* (foreflight.com/connect/spec, cited FF). Framing and CRC are the
`stratux` plugin's (`plugins/stratux/gdl90.py`), so one implementation encodes
and decodes, and the decoder is the encoder's test oracle. The ICD's worked
examples (the G§2.2.4 Heartbeat frame and the G§3.5.2 Traffic Report, Table
12) are independent goldens, so the two cannot be wrong together.

## Who it sends to

Never broadcast. GDL-90 goes by UDP unicast to each client, on the port the
client asks for (4000 by default). FF: iOS apps cannot reliably receive UDP
broadcast. The send socket never sets `SO_BROADCAST`, so Linux refuses a send
to a broadcast address even if one is configured by mistake.

Of the three paths, the plugin does (a) and (b):

- **(a) ForeFlight discovery.** ForeFlight broadcasts a JSON datagram on UDP
  63093 every 5 s while it is in the foreground, at least
  `{"App":"ForeFlight","GDL90":{"port":4000}}` (FF "ForeFlight Broadcast").
  The plugin listens, and sends to the sender's address on the advertised
  port. A client that stops announcing is dropped after `client_timeout_s`
  (60 s), so a tablet that sleeps stops getting data a minute later and gets
  it again within 5 s of waking.
- **(b) Configured clients.** `clients:` lists addresses (`"192.168.1.50"` or
  `"192.168.1.50:4000"`), for apps that do not announce themselves.
- **(c) DHCP leases** is not done. It works only when the box is the access
  point, and a fix-gateway box usually is not: it sits on the cockpit's
  network.

The listener is an unauthenticated network input (CERT14/15). The parse is
strict: at most 512 bytes, UTF-8, a JSON object with `App` exactly
`"ForeFlight"` and `GDL90.port` an integer in 1-65535; anything else is counted
and ignored. A sender that is not a unicast IPv4 address is refused.
`discovery_allow` lists the networks a client may come from, such as
`["192.168.1.0/24"]` (like the `stratux` plugin's `source`). Empty accepts any
sender. An entry that does not parse accepts nobody, and is logged. At most
`max_clients` (4) are held at a time; a new one is refused, and logged, while the
table is full.

## Messages and rates

| Msg | ID | Rate | Notes |
|---|---|---|---|
| Heartbeat | 0x00 | 1 Hz | Status 1 bit 7 GPS position valid (FF reads only this bit), bit 0 always 1. Time stamp from the host clock, so UTC OK (status 2 bit 0) stays 0. Message counts 0. |
| Ownship Report | 0x0A | 1 Hz | G§3.4/3.5.1, below. |
| Ownship Geometric Altitude | 0x0B | 1 Hz | Only while its key is valid (G§3.8). |
| ForeFlight ID | 0x65/0 | 1 Hz | Name `MkrPlane`, long name `MkrPlane <version>`, serial invalid. Capability bit 0 is the 0x0B datum. |
| ForeFlight AHRS | 0x65/1 | 5 Hz | FF's own AHRS message. FF does not list the Stratux 0x4C, so 0x4C is not sent. |
| Traffic Report | 0x14 | 1 Hz | Off by default (`traffic: false`), below. |

A second's messages go out together, packed into datagrams of 1,400 bytes or
less (FF: keep under the 1,500-byte MTU).

## Key-to-field mapping

A key is **valid** when the database holds it, a writer has written it, and it
is not `old`, `bad` or `fail`. Validity maps through; nothing is smoothed over.

| Field | Key, unit | Encoding | When the key is not valid |
|---|---|---|---|
| Latitude, longitude | `LAT`, `LONG`, deg | 24-bit signed, 180/2^23 deg, truncated toward zero as the ICD's Table 12 is | Lat = lon = NIC = NACp = 0 (G§3.5.1.3), and heartbeat GPS-valid clear. Also when `GPS_FIX_TYPE` has been written and is not valid or is below 2. |
| NIC | none | 0, "unknown": no key carries a containment radius | 0 |
| NACp | `GPS_ACCURACY_HORIZ`, ft | Table 10 bin of the value in metres, strict `<` | 0, "unknown" |
| Pressure altitude | `PALT`, ft | (ddd x 25) - 1,000, nearest; clamps to 0x000 and 0xFFE | 0xFFF |
| Ground speed | `GS`, kt | 1 kt, nearest; holds at 0xFFE | 0xFFF |
| Vertical speed | `VS`, ft/min | 64 ft/min, nearest; beyond +-32,576 holds at 0x1FE / 0xE02 | 0x800 |
| Track | `TRACK`, deg true | 360/256 deg, nearest; misc type 01, true track | tt 0, misc type 00, "not valid" |
| Air/ground | none | always airborne (misc bit 3): the bus has no air/ground key | |
| Address | `ownship_address` (as the `stratux` plugin resolves it) | type 0, ICAO | 0x000000, FF's "GPS telemetry only" |
| Call sign | `callsign` setting | A-Z, 0-9, space-padded to 8 | spaces |
| Emitter category | `emitter_category` setting | default 1, light | |
| Geometric altitude | `geo_altitude_key` (default `GPS_ELLIPSOID_ALT`), ft | signed, 5 ft | message not sent |
| VFOM | `GPS_ACCURACY_VERTICAL`, ft | metres, holds at 0x7FFE; vertical warning 0 | 0x7FFF, "not available" |
| Roll, pitch | `ROLL`, `PITCH`, deg | 0.1 deg; + right wing down, + nose up | 0x7FFF |
| Heading | `HEAD`, deg magnetic | 0.1 deg, bit 15 set (magnetic) | 0xFFFF |
| IAS, TAS | `IAS`, `TAS`, kt | 1 kt | 0xFFFF |

The 0x0B datum is a setting, `geo_altitude_datum: ellipsoid` or `msl`, sent in
the ID message's capability bit 0. Only `mavlink` writes `GPS_ELLIPSOID_ALT`,
the ellipsoid height. `garmin_gnx375`, `gpsd` and `navigator_adapter` write
their MSL height to `TALT` (GALT1/TALTSRC1). While the configured key has never
been written and `TALT` has, the plugin sends `TALT` and sets the datum bit to
MSL, so a GNX panel on the shipped config still sends 0x0B.

## Traffic, and a Stratux on the same tablet

`traffic: true` re-emits each TFC slot that holds a target as a 0x14 report,
field for field, with `bad` fields sent as the ICD's "not available" codes.
Leave it off when the Stratux (or any ADS-B receiver that feeds the TFC block)
also feeds the tablet directly. Each target would then reach the tablet from
two devices, at two latencies, and an EFB may draw it twice or let it jump
between positions. The box cannot tell whether the tablet also hears the
receiver, so this is a setting, not a rule. Turn it on only when the panel is
the tablet's one source.

## Bluetooth

Not a deliverable; this is the answer to record. ForeFlight's published GDL-90
interface is Wi-Fi UDP only (FF "Connectivity"). Its Bluetooth links are
partner integrations: Sentry, Stratus, Garmin Connext (GI 275, Flight Stream),
each on that vendor's own protocol. FLARM's EFB guide (FTD-115) offers its BLE
data port to other apps, but lists only "GDL 90 (auto-discovery)" over Wi-Fi
for ForeFlight. ADSBee's BLE GDL-90 output names only Android apps (AvareX,
XCSoar 7.45 and later) and records ForeFlight as "published spec is UDP only;
BT = vendor integrations". So a bench device outside the partner program can
reach ForeFlight over Wi-Fi only. Bluetooth GDL-90 works only with the
Android apps above.

## Not done here

- `XGPS`/`XATT` text on UDP 49002 (the optional second output).
- Pinging clients to keep a sleeping iPad's Wi-Fi awake.
- ForeFlight itself. A tablet test is EFB2. With no `GPS_ACCURACY_HORIZ` writer
  (X-Plane writes none) the ownship report carries NACp 0. ForeFlight is
  reported to hide ownship when accuracy is unknown or worse than 100 m. EFB2
  has to settle whether that leaves the sim bench with no ownship.

## Configuration (`connections/gdl90_out.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `discovery` | `true` | listen for ForeFlight's broadcast |
| `discovery_host`, `discovery_port` | `""`, `63093` | where to listen |
| `discovery_allow` | `[]` | networks a discovered client may be on; empty = any |
| `clients` | `[]` | fixed clients, `"ip"` or `"ip:port"` |
| `client_timeout_s` | `60` | drop a discovered client after this long unheard |
| `max_clients` | `4` | discovered clients held at once |
| `ahrs` | `true` | send the 0x65/1 AHRS message at 5 Hz |
| `traffic` | `false` | re-emit the TFC block as 0x14 |
| `geo_altitude_key`, `geo_altitude_datum` | `GPS_ELLIPSOID_ALT`, `ellipsoid` | 0x0B source and datum |
| `callsign`, `emitter_category`, `ownship_address` | `""`, `1`, `""` | ownship identity |
| `internet_policy` | `0` | ID capability bits 1-2: 0 unrestricted, 1 expensive, 2 disallowed |
