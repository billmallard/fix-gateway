# Stratux / GDL90 plugin

`fixgw.plugins.stratux` listens for GDL90 on UDP (port 4000 by default, where
a Stratux sends it). It decodes:

| Message | ID | Writes |
|---|---|---|
| Stratux AHRS (extension, not GDL90) | `0x4C` | `PITCH`, `ROLL`, `HEAD`, `ALAT`, `ALT`, `VS` |
| Ownship Report | `0x0A` | the `STX` ownship keys; `GS`; ownship identity for the traffic filter |
| Ownship Geometric Altitude | `0x0B` | `STXGALT`, `STXVFOM`, `STXVWARN` |
| Heartbeat | `0x00` | traffic source liveness |
| Traffic Report | `0x14` | the `TFC` traffic block |

A datagram may carry several messages back to back. Byte stuffing follows
GDL90 section 2.2.1 (only `0x7D` escapes; a bare `0x7E` is a frame boundary),
and a frame whose FCS does not match is discarded.

## Configuration (`connections/stratux.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `host` | `""` | address to bind |
| `port` | `4000` | UDP port |
| `source` | `""` | the receiver's IPv4 address, e.g. `"192.168.10.1"`. When set, a datagram from any other sender is dropped and counted in the log. Empty accepts every sender. See *AHRS is a network input* |
| `ownship` | `true` | write the receiver's ownship data into the `STX` keys. See *Ownship* |
| `traffic` | `true` | write ADS-B traffic into the `TFC` block. Exactly one traffic writer per installation |
| `evict_s` | `10` | seconds without a report before a target's slot is emptied |
| `ownship_address` | `""` | our 24-bit ICAO address as a **quoted** hex string, e.g. `"A1B2C3"`. Set by the configurator from the aircraft registration |

## AHRS is a network input to the attitude display

The `0x4C` AHRS message writes `PITCH`, `ROLL` and `HEAD`, the attitude and
heading the PFD shows. It arrives as UDP over the receiver's Wi-Fi, and GDL90
has no authentication. With `source` empty the plugin accepts it from any
host that sends to the port: a second receiver, a flight simulator
broadcasting GDL90, or anything else on that network can drive the attitude
display.

Set `source` to the receiver's address. Datagrams from any other address are
then dropped. The first drop from each sender is logged with its address, the
running count is logged once a minute while drops continue, and the plugin
status shows `Dropped datagrams`. A `source` that is not an IPv4 address
drops everything and logs an error, so attitude goes stale rather than the
filter quietly turning off.

The filter is an address check, not authentication. A host on the same
network can forge the receiver's source address. It stops the ordinary case,
a second device sending GDL90 to the port, and it makes a foreign sender
visible in the log. It does not make Wi-Fi AHRS a trusted attitude source.

## Ownship

The receiver's own position, integrity and GPS altitude go into the `STX`
block (`config/database/stratux.yaml`), and nowhere else. The plugin does not
write `LAT`, `LONG`, `TRACK` or `ALT` from these messages. Position is
source-selected, so a second GPS source never fights the Stratux for a key
(Bill's ruling on GDL1, AER-5615). Routing `STXLAT`/`STXLONG` into
`LAT`/`LONG` is a separate step.

Field references are to the GDL 90 Data Interface Specification 560-1058-00
Rev A. All keys have a 2 s `tol`: both messages come once a second, and a
receiver with no fix stops sending them, so every key goes `old`.

| Key | Units | From | `bad` when |
|---|---|---|---|
| `STXLAT`, `STXLONG` | deg | `0x0A` §3.5.1.3 | latitude, longitude and NIC are all 0 (no valid position) |
| `STXPALT` | ft | `0x0A` §3.5.1.4, pressure altitude | `0xFFF` |
| `STXGS` | kt | `0x0A` §3.5.1.7 | `0xFFF` |
| `STXTRACK` | deg true | `0x0A` §3.5.1.9 | the misc field does not mark it a true track (not valid, or a heading) |
| `STXVS` | ft/min | `0x0A` §3.5.1.8 | `0x800`, or the unused codes `0x1FF`-`0x7FF` and `0x801`-`0xE01` |
| `STXAIR` | bool | `0x0A` §3.5.1.5, true = airborne | never |
| `STXNIC`, `STXNACP` | code | `0x0A` §3.5.1.6 | 12-15 (unused) |
| `STXRC` | m | NIC through Table 10: the containment radius bound | NIC 0 (unknown), 12-15, or no valid position |
| `STXEPU` | m | NACp through Table 10: the estimated position uncertainty bound | NACp 0 (unknown) or 12-15 |
| `STXGALT` | ft | `0x0B` §3.8, height above the WGS-84 ellipsoid | never: the message is absent when there is no GPS altitude |
| `STXVFOM` | m | `0x0B` §3.8, vertical figure of merit | `0x7FFF` (not available). `0x7FFE` is written as 32766 |
| `STXVWARN` | bool | `0x0B` §3.8 vertical warning: a position alarm, or no fault detection | never |

`STXRC` and `STXEPU` are each code's upper bound in metres (1 NM = 1852 m):

| Code | `STXRC` (NIC) | `STXEPU` (NACp) |
|---|---|---|
| 1 | 37040 (< 20 NM) | 18520 (< 10 NM) |
| 2 | 14816 (< 8 NM) | 7408 (< 4 NM) |
| 3 | 7408 (< 4 NM) | 3704 (< 2 NM) |
| 4 | 3704 (< 2 NM) | 1852 (< 1 NM) |
| 5 | 1852 (< 1 NM) | 926 (< 0.5 NM) |
| 6 | 1111.2 (< 0.6 NM) | 555.6 (< 0.3 NM) |
| 7 | 370.4 (< 0.2 NM) | 185.2 (< 0.1 NM) |
| 8 | 185.2 (< 0.1 NM) | 92.6 (< 0.05 NM) |
| 9 | 75 | 30 |
| 10 | 25 | 10 |
| 11 | 7.5 | 3 |

A `bad` key holds 0, except the three uncertainty keys (`STXRC`, `STXEPU`,
`STXVFOM`), which hold their maximum. A reader that ignores the flag then
sees the worst figure rather than a perfect one. Never use a `bad` value.

`GS` is still written from the Ownship Report as before, so existing screens
are unchanged. `STXGS` carries the same field as a source-prefixed key.

The receiver's address, call sign, emitter category and emergency code are
decoded but not published. The address feeds the traffic filter below.

### What a Stratux really sends

Stratux v1.6r1-eu023 (the build on the bench FlightBox), read from its
`main/gen_gdl90.go` and `main/gps.go`. Other Stratux builds may differ.

- **NIC is always 8** (< 0.1 NM), whatever the GPS reports. It is not a
  measured containment radius, so `STXRC` from a Stratux is not an
  integrity figure. An integrity gate must not treat it as one.
- **NACp is real**: the GPS's horizontal accuracy estimate, binned with the
  thresholds in the table above (3, 10, 30, 92.6, 185.2 and 555.6 m). Above
  555.6 m it sends 0, so `STXEPU` goes `bad`.
- **The altitude field is not always pressure altitude.** With no pressure
  sensor (or an invalid one), Stratux puts GPS MSL altitude there. When it
  hears its own ADS-B Out, it puts the transponder's reported altitude there.
  The wire does not say which. The FlightBox V3 has a cabin-only baro.
- **Vertical velocity is always `0x800`**, so `STXVS` is always `bad`.
- **With no valid ground track**, Stratux sends speed 0, not `0xFFF`. That is
  a valid 0 kt by the ICD, and `STXGS` reads 0 without `bad`. `STXTRACK` goes
  `bad` in the same report.
- **`0x0B` is MSL, not ellipsoid height, when the Stratux "GDL90 MSL
  altitude" setting is on.** The wire does not say which. **VFOM is always
  10 m** and the vertical warning is never set. Neither is measured.
- With neither a GPS fix nor its own ADS-B Out heard, Stratux sends no
  Ownship Report, and the keys go `old`.

## Traffic

The key contract is pyEfis `docs/fix_dynamic_list_spec.md` section 4; the
keys are in `config/database/traffic.yaml` (32 slots, `TFC1..32`, index letter
`k`). For a consumer, the rules that matter:

- A slot holds a target exactly when `TFCkTGT != -1` and `TFCkTGT` is neither
  `old` nor `fail`. `TFCkTGT` is written last on every report; act on its
  write, and key any display state by its value, not by the slot number.
- A field marked `bad` is unavailable at the source. Dash it.
- `TFCkALT` is pressure altitude (29.92 inHg). Relative altitude is
  `TFCkALT - PALT`, never `ALT`.
- `TFCCOUNT` carries `fail` when the receiver has sent no Heartbeat for 3 s;
  every occupied `TFCkTGT` carries it too. `TFCDROP` counts targets that got no
  slot in the last second because the block was full (the nearest are kept).
- Ownship is removed by identity only: `ownship_address`, and the address in
  the receiver's Ownship Report. A TIS-B track-file shadow of ownship (address
  type 3) is not removed and can appear beside the ownship symbol.

The gateway applies no threat logic: `TFCkALRT` is the receiver's own alert
bit.
