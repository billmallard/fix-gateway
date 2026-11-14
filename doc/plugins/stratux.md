# Stratux / GDL90 plugin

`fixgw.plugins.stratux` listens for GDL90 on UDP (port 4000 by default, where
a Stratux sends it). It decodes:

| Message | ID | Writes |
|---|---|---|
| Stratux AHRS (extension, not GDL90) | `0x4C` | `PITCH`, `ROLL`, `HEAD`, `ALAT`, `ALT`, `VS` |
| Ownship Report | `0x0A` | `GS`; ownship identity for the traffic filter |
| Heartbeat | `0x00` | traffic source liveness |
| Traffic Report | `0x14` | the `TFC` traffic block |
| Uplink (FIS-B) | `0x07` | no keys: text weather into the plugin's store, with `fisb: true` |

A datagram may carry several messages back to back. Byte stuffing follows
GDL90 section 2.2.1 (only `0x7D` escapes; a bare `0x7E` is a frame boundary),
and a frame whose FCS does not match is discarded.

## Configuration (`connections/stratux.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `host` | `""` | address to bind |
| `port` | `4000` | UDP port |
| `source` | `""` | the receiver's IPv4 address, e.g. `"192.168.10.1"`. When set, a datagram from any other sender is dropped and counted in the log. Empty accepts every sender. See *AHRS is a network input* |
| `traffic` | `true` | write ADS-B traffic into the `TFC` block. Exactly one traffic writer per installation |
| `evict_s` | `10` | seconds without a report before a target's slot is emptied |
| `ownship_address` | `""` | our 24-bit ICAO address as a **quoted** hex string, e.g. `"A1B2C3"`. Set by the configurator from the aircraft registration |
| `fisb` | `false` | decode FIS-B text weather from `0x07` Uplink messages. The receiver must have UAT (978 MHz) enabled. See *FIS-B text weather* |

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

## FIS-B text weather

With `fisb: true` the plugin decodes the `0x07` Uplink message: the 432-byte
UAT ground uplink, its information frames, and the FIS-B APDUs in them
(`plugins/stratux/fisb.py`). It keeps the text products in a store
(`plugins/stratux/weather.py`):

| Product | APDU | Kept per | Dated by | Old at | Dropped at |
|---|---|---|---|---|---|
| METAR, SPECI | 413 | station (newest observation wins) | observation time | +75 min | +120 min |
| TAF, TAF.AMD, TAF COR | 413 | station (newest issue wins) | issue time | end of valid period | +60 min after it |
| PIREP | 413 | report | report time | +90 min | +120 min |
| Winds and temperatures aloft | 413 | station and valid time | availability time | end of for-use period (DO-358B table A-9) | +60 min after it |
| NOTAM-D, -FDC, -TFR, -TRA, -TMOA | 8, 16, 17 | report | effective period in the text | end of the period | +60 min after it, or 60 min after last received |
| AIRMET, SIGMET, CWA | 11, 12, 15 | report | issue time | `VALID UNTIL` | +60 min, or 60 min after last received |

A product's state, `current`, `future` (a forecast or NOTAM not yet in
effect) or `old`, is computed when it is read, from the host's UTC clock.
Nothing is ever marked current at receipt and left that way. A product that
cannot be dated from its own text is not stored, and a product dated more
than 10 minutes after the clock is `old`: the clock is wrong (a host without
a time source), so its age is unknown. A TWGO cancellation removes the
report. Segmented TWGO products are joined; a file not complete within 10
minutes of its first segment is dropped. The age limits are proposals awaiting
a ruling (AER-5651).

The plugin status shows `FIS-B uplinks`, `FIS-B products`, `FIS-B current`
(how many are current now) and the count of each kind. Nothing reads the store yet: how weather reaches pyEfis is an open
decision. Graphical products (NEXRAD, AIRMET/SIGMET and TFR geometry) are not
decoded.
