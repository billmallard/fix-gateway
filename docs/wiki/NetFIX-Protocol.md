# NetFIX Protocol

**NetFIX** is how anything off-box talks to the database. It is a line-based
ASCII protocol over TCP, served by the `netfix` plugin on port **3490** by
default. pyEfis, moving maps, control heads, and the debug client all speak it.

Full spec: [`doc/appendix/netfixascii.rst`](https://github.com/billmallard/fix-gateway/blob/master/doc/appendix/netfixascii.rst).

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
  (`T`/`F`), or **string** (begins with `&`; may not contain `;`).
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
Omitted flags are assumed `0`.

## Commands

Commands begin with `@`, a single command letter, then any `;`-separated
arguments. If a command expects a reply, the server answers with a line starting
`@` + the same letter. Errors come back as `!` + a code (e.g. `@rIAS!001` =
key not found).

| Command | Form | Purpose |
|---|---|---|
| **Read** | `@r<ID>` | return the current value + flags of one key |
| **Write** | `@w<ID>;<value>` | set a value (with acknowledgement) — or send a bare data sentence to write without a reply |
| **Subscribe** | `@s<ID>` | start receiving a data sentence every time `ID` changes |
| **Unsubscribe** | `@u<ID>` | stop receiving updates for `ID` |
| **List** | `@l` | list all keys in the database |
| **Report** | `@q<ID>` | metadata for a key (description, type, min/max, units, tol, aux…) |
| **Flag** | `@f<ID>;<flag>;<0\|1>` | set/clear a quality flag on a key |
| **Status / admin** | `@xstatus`, `@xkill` | server status; shut down |

**Subscribe** is the normal mode for a display: subscribe to the keys it shows,
then react to the update stream — no polling.

## A session, by hand

```
$ nc localhost 3490
@rIAS              ← read airspeed
IAS;122.4;00000    → reply
@sALT              ← subscribe to altitude
ALT;5500.0;00000   → immediate value, then one line on every change…
ALT;5501.0;00000
@qIAS              ← ask for airspeed's metadata
IAS;Indicated Airspeed;float;0.0;1000.0;knots;...
```

## From code

You rarely hand-write NetFIX. Use a client library:

- **In pyEfis:** `pyavtools.fix` — the shared FIX client every widget uses
  (`fix.db.get_item("IAS")`, `.valueChanged`, `.set_value(...)`).
- **In FIX-Gateway's own tooling:** `fixgw.netfix.Client`:

```python
from fixgw import netfix
c = netfix.Client("127.0.0.1", 3490)
c.connect()
print(c.read("IAS"))          # ('IAS', '122.4', '')  -> (id, value, flags)
c.write("NAVSRC", 0)          # select a nav source
c.subscribe("ALT")            # stream ALT changes
c.disconnect()
```

`read()` returns `(id, value, flags)` where the flag letters are the same
`aobfs` set (`o`=old, `b`=bad, `f`=fail). This is exactly the interface used to
probe and script a running gateway during bench testing.

## Notes

- The `netfix` plugin is a **consumer** from the database's point of view but a
  full **two-way** interface for clients — a client can both read and write keys,
  so a control head can publish selections (`COURSE`, `HEADBUG`, `NAVSRC`) back
  into the bus.
- The debug client `./fixGwClient.py` (console) and `--gui` are the quickest way
  to watch and poke the database live. See
  [`doc/client.rst`](https://github.com/billmallard/fix-gateway/blob/master/doc/client.rst).
