# CANaerospace Input

The `canaerospace` plugin decodes CANaerospace V1.7 frames, such as those from a
Rotax 912iS / 915iS engine ECU, and writes mapped values into the database. It
only listens: the plugin never transmits a frame.

Plugin reference:
[`doc/plugins/canaerospace.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/canaerospace.rst).

> **The shipped Rotax map is empty.** `canaerospace/rotax_is_map.yaml` ships
> `inputs: []` and `events: []`, with every entry commented out. Fill in the
> CAN ids from the Rotax manual or a bench capture before you rely on it.

## Turning it on

The plugin ships off. In `preferences.yaml.custom`:

```yaml
enabled:
  CANAEROSPACE: true
```

See [[Configuration]].

## Settings

| Option | Default | Meaning |
|---|---|---|
| `interface` | required (`socketcan` shipped) | python-can interface |
| `channel` | required (`can0` shipped) | python-can channel |
| `mapfile` | required (`{CONFIG}/canaerospace/rotax_is_map.yaml` shipped) | the map, below |
| `node_timeout` | `2.0` s | how long a higher-priority node must be silent before a lower-priority node's data is accepted |

The bit rate is set on the interface, not here: for SocketCAN,
`ip link set can0 up type can bitrate ...`.

## The map file

Only CAN ids listed in the map are decoded. Frames with 29-bit ids, or ids above
2031, are ignored.

```yaml
ignore_fixid_missing: false
strict_types: false
inputs:
  - canid: 0x000          # from the Rotax manual or a bench capture
    fixid: TACH1
    type: USHORT          # expected wire type
    element: 0            # 0..3
    scale: 1.0
    offset: 0.0
    converter: none
    nodes: [16, 17]       # allowed senders, in priority order
events: []
```

| Field | Meaning |
|---|---|
| `canid`, `fixid` | required: the CANaerospace id (0..2031) and the key to write |
| `type` | expected data type; with `strict_types: true`, a frame of another type is dropped |
| `element` | which value in the frame, 0..3 (default 0) |
| `scale`, `offset` | the value written is `value * scale + offset` (defaults 1.0, 0.0), then the converter |
| `converter` | `none`, `k2c`, `c2f`, `kpa2inhg`, `kpa2psi`, `mps2knots`, `m2ft`, `pct` |
| `nodes` | senders allowed for this id, highest priority first; others are ignored |

**Dual-lane failover.** With `nodes: [16, 17]`, frames from node 17 are accepted
only after node 16 has been silent for `node_timeout` seconds.

**Events.** An `events` entry (`canid` 0..127, `fixid`, optional `match_code`)
sets its key true on a matching Emergency Event frame. It latches and never
clears itself. Every Emergency Event frame is logged as a warning, mapped or
not.

## Quality flags

A frame of the ERROR type sets `fail` on the mapped key; the next good value
clears it. The plugin keeps no timer of its own, so when the ECU goes quiet the
keys go `old` through their `tol` (2 s for `TACH1`).

The plugin status shows frames received, decoded, ignored and invalid, type
mismatches, error and no-data frames, sequence gaps, emergency events, and each
node seen with its age.
