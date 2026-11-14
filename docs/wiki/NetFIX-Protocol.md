# NetFIX Protocol

**NetFIX** is how anything off-box talks to the database. It is a line-based
ASCII protocol over TCP, served by the `netfix` plugin on port **3490** by
default. pyEfis, moving maps, control heads, and the debug client all speak it.

The shipped config binds **`127.0.0.1`**, so only clients on the same host can
connect. NetFIX has no authentication, and a sentence with its flags omitted
is written as valid. A client on another host needs `host:` set to the one
interface that faces it; see [`doc/plugins/netfix.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/netfix.rst), "Network exposure".

Full spec: [`doc/appendix/netfixascii.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/appendix/netfixascii.rst).

Two kinds of line cross the socket: **data sentences** (a value + its flags) and
**commands** (a request, prefixed with `@`). Every line ends with `\n`.

## Data sentences

A value is a semicolon-delimited line: the key, the value, then the quality
flags.

**Server → client:**
```
ID;value;aobfs
```
- `ID` — the database key (e.g. `IAS`), same names as the [[The FIX Database]].
- `value` — a **float** (has a decimal point), **int** (no point), **bool**
  (`True`/`False`), or **string** (sent as is, with no prefix; may not contain
  `;`).
- `aobfs` — five quality flags, each `1`/`0`: **a**nnunciate, **o**ld, **b**ad,
  **f**ailed, **s**econdary-fail.

```
IAS;122.4;00000        # 122.4 kt, all good
OILP1;18.0;00110       # 18 psi, bad + failed -> don't display
```

**Client → server** (writing a value) is the same but **without the `o` flag** —
`old` is the server's to decide (from the key's `tol`); a client that thinks its
data is stale sets `bad` instead:

```
ID;value;abfs
```
Omitted flags are assumed `0`: `IAS;122.4` writes 122.4 with every flag clear,
and `IAS;122.4;1` sets annunciate and clears the rest. A line with only a key,
or with more than three fields, is ignored.

On a write, a **bool** is true for `true`, `yes` or `1` in any case, or any
nonzero integer, and false for anything else, so `T` writes false. A **string**
is stored exactly as sent: a leading `&` becomes part of the value.

## Commands

Commands begin with `@`, a single command letter, then any `;`-separated
arguments. If a command expects a reply, the server answers with a line starting
`@` + the same letter; every command gets one. Errors come back as `!` + a code
after the request (e.g. `@rXYZ!001`):

| Code | Meaning |
|---|---|
| `001` | key not found, or unknown `@x` subcommand |
| `002` | already subscribed, or unknown flag letter |
| `003` | bad flag value, or missing or bad value on `@w` |
| `004` | unknown command |

| Command | Form | Purpose |
|---|---|---|
| **Read** | `@r<ID>` | return the current value + flags of one key: `@r<ID>;<value>;<aobfs>` |
| **Write** | `@w<ID>;<value>` | set a value, acknowledged with `@w<ID>;<value>;<aobfs>` — or send a bare data sentence to write without a reply. `@w<ID>.<aux>;<value>` writes an aux value |
| **Subscribe** | `@s<ID>` | acknowledged with `@s<ID>`; from then on, a data sentence every time `ID` is written or its flags change |
| **Unsubscribe** | `@u<ID>` | stop receiving updates for `ID` |
| **List** | `@l` | list all keys, as `@l<total>;<offset>;<key>,<key>,...` (long lists come in several lines) |
| **Report** | `@q<ID>` | metadata for a key (description, type, min/max, units, tol, aux…) |
| **Flag** | `@f<ID>;<flag>;<0\|1>` | set/clear a quality flag on a key |
| **Status / admin** | `@xstatus`, `@xkill` | server status as JSON; shut down the whole gateway |

**Subscribe** is the normal mode for a display: subscribe to the keys it shows,
then react to the update stream — no polling. The subscription does not send the
current value; read it once with `@r` if you need it before the next write.

## A session, by hand

```
$ nc localhost 3490
@rIAS              ← read airspeed
@rIAS;122.4;00000  → reply
@sALT              ← subscribe to altitude
@sALT              → acknowledgement
ALT;5500.0;00000   → then one line on every write…
ALT;5501.0;00000
@qIAS              ← ask for airspeed's metadata
@qIAS;Indicated Airspeed;float;0.0;1000.0;knots;2000;Min,Max,V1,V2,Vne,Vfe,Vmc,Va,Vno,Vs,Vs0,Vx,Vy,Vgnd
```

## From code

You rarely hand-write NetFIX. Use a client library:

- **In pyEfis:** `pyavtools.fix` — the shared FIX client every widget uses
  (`fix.db.get_item("IAS")`, `.valueChanged`, `.set_value(...)`).
- **In FIX-Gateway's own tooling:** `fixgw.netfix.Client`:

```python
from fixgw import netfix

def on_data(sentence):        # [id, value, flags], flags as letters
    print(sentence)

c = netfix.Client("127.0.0.1", 3490)   # timeout=1.0 by default
c.connect()                   # returns False if it could not connect
print(c.read("IAS"))          # ('IAS', '122.4', '')  -> (id, value, flags)
c.writeValue("NAVSRC", 0)     # write and wait for the acknowledgement
c.setDataCallback(on_data)    # where subscribed sentences are delivered
c.subscribe("ALT")
c.disconnect()
```

`read()` returns `(id, value, flags)`: the value as a string, and `flags` as the
letters of the flags that are set, from `aobfs` (`''` when all are clear). On an
error it returns the error code instead. `write(id, value)` sends a bare data
sentence with no acknowledgement. The client also has `unsubscribe`, `getList`,
`getReport`, `flag`, `getStatus` and `stop` (`@xkill`).

## Notes

- The `netfix` plugin is a **consumer** from the database's point of view but a
  full **two-way** interface for clients — a client can both read and write keys,
  so a control head can publish selections (`COURSE`, `HEADBUG`, `NAVSRC`) back
  into the bus.
- The `netfix` plugin can also run as `type: client` or `both`, pushing the keys
  listed in its `outputs` to another gateway with `@w`.
- The debug client `./fixGwClient.py` (console, installed as `fixgwc`) and
  `--gui` are the quickest way
  to watch and poke the database live. See
  [`doc/client.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/client.rst).
