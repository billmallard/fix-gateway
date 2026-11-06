# CAN-FIX

The `canfix` plugin bridges the CAN-FIX bus and the database in both
directions: parameters on the bus become keys, and selected keys go out on the
bus. Protocol: [canfix-spec](https://github.com/billmallard/canfix-spec).

Plugin reference:
[`doc/plugins/canfix.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/canfix.rst).
It is off in the shipped config: set `CANFIX: true` (see [[Configuration]]).

## Settings

`connections/canfix.yaml`. Every option is required; there are no code defaults.

| Option | Shipped | Meaning |
|---|---|---|
| `interface` | `socketcan` | python-can interface (`serial` for a USB adapter) |
| `channel` | `vcan0` | python-can channel; change to `can0` for a real bus |
| `mapfile` | `{CONFIG}/canfix/map.yaml` | the map, below |
| `node` | `145` | this gateway's CAN-FIX node id |
| `device` | `145` | CAN-FIX device type |

## The map file

**`inputs`** — bus to database. Each entry has `canid`, `index` and `fixid`, and
optionally `nodespecific` (also accept node-specific messages for it) and
`scale`. A value arrives with its annunciate, quality and failure flags, which
set the key's flags. Limit metadata (`Low Warn`, `High Alarm`…) is written to
aux values through `meta replacements`.

```yaml
inputs:
  - { canid: 0x180, index: 0, fixid: "PITCH" }
  - { canid: 0x183, index: 0, fixid: "IAS" }
```

With `ignore_fixid_missing: true` (shipped), entries for keys that do not exist,
such as a second engine's, are skipped.

**`outputs`** — database to bus. `owner: True` sends a normal parameter update,
by default only when the value or flags change (`on_change: False` sends every
write). Without it, the plugin sends a node-specific parameter set. A value just
received from the bus is not echoed back.

```yaml
outputs:
  - { fixid: "BARO", canid: 0x190, index: 0, owner: False }
  - { fixid: "ALTSEL", canid: 0x1CD, index: 0, owner: True }
```

Outputs are sent only while this gateway is the quorum leader, unless the entry
sets `require_leader: False`. Without the `quorum` plugin the gateway is always
the leader.

**`encoders`** — up to two encoders and eight buttons per CAN id:
`fixid: "ENC1, ENC2, BTN11"`. With `sum: true` each message is added to the
key; otherwise it replaces it.

**`switches`** — CAN ids `0x308`…`0x30F`, a comma-separated list of keys per id
(up to 40). Keys listed in `toggle:` flip on each press, for pyEfis toggle
buttons. A comma-separated `fixid` in `outputs` on one of these ids sends the
keys out as one switch message.
