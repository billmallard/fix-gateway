# Stratux / GDL90 plugin

`fixgw.plugins.stratux` listens for GDL90 on UDP (port 4000 by default, where
a Stratux sends it). It decodes:

| Message | ID | Writes |
|---|---|---|
| Stratux AHRS (extension, not GDL90) | `0x4C` | `PITCH`, `ROLL`, `HEAD`, `ALAT`, `ALT`, `VS` |
| Ownship Report | `0x0A` | `GS`; ownship identity for the traffic filter |
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
