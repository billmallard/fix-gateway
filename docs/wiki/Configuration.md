# Configuration

FIX-Gateway is configured entirely by YAML files under `src/fixgw/config/`.
Two questions are answered there: **which plugins run** (and how), and **what
keys exist** (and their aircraft-specific values). Getting-started walkthrough:
[`doc/getting_started.rst`](https://github.com/billmallard/fix-gateway/blob/master/doc/getting_started.rst).

## The files

| File | Answers | Edit? |
|---|---|---|
| `default.yaml` | which plugins load; server settings | via includes (below) |
| `preferences.yaml` | maps each `*_CONFIG` name → a connection file | when adding a plugin |
| `connections/<plugin>.yaml` | one plugin's settings | yes, per plugin |
| `database.yaml` | the key namespace (a set of includes) | **no** — use `custom.yaml` |
| `database/custom.yaml` | your added/overridden keys | yes |
| `database/variables.yaml` | engine/cylinder/tank/node counts | yes |
| `init_data/*.ini` | initial + aircraft-specific values (V-speeds, bands) | yes (`custom.ini`) |

`{CONFIG}` inside any file expands to the config directory, so paths stay
portable.

## Enabling a plugin

Plugins are declared in three coordinated places — this is the pattern to copy
when adding one:

**1. The connection file** `connections/<name>.yaml` describes the plugin:

```yaml
xplane:
  load: yes                     # 'yes' loads it; a named flag also works
  module: fixgw.plugins.xplane  # the Python module
  # ...any plugin-specific settings (passed to the plugin as self.config)
```

**2. `preferences.yaml`** maps a stable include-name to that file:

```yaml
includes:
  XPLANE_CONFIG: connections/xplane.yaml
```

**3. `default.yaml`** lists which include-names to actually load:

```yaml
connections:
  include:
    - NETFIX_CONFIG
    - COMPUTE_CONFIG
    - XPLANE_CONFIG      # loaded
    # - STRATUX_CONFIG   # present but off
```

To turn a plugin off, remove (or comment) its line from the `default.yaml`
include list. To turn one on, add its name. The full plugin catalogue is on the
**[[Plugins]]** page.

> **One key, one writer.** Before enabling two source plugins, check they don't
> both write the same keys — e.g. `xplane` and `stratux` both publish attitude
> and will fight. Enable exactly one source per key (see
> [[Architecture]] → *One key, one writer*).

## Initial and aircraft-specific values

`init_data` files set values after the database is built — this is where an
airplane's numbers live. They use a simple `KEY[.aux] = value` syntax:

```ini
IAS.Vs   = 48        # aux value (V-speed) on the IAS key
IAS.Vne  = 165
OILP1.lowAlarm = 25  # gauge band
NAVSRC   = 2         # a plain initial value
```

`default.yaml` lists the init files in order (later overrides earlier), so a
layered setup is idiomatic:

```yaml
initialization files:
  - "{CONFIG}/init_data/default.ini"                 # sensible defaults
  # - "{CONFIG}/init_data/airplanes/cessna/c170b.ini"# per-airframe
  # - "{CONFIG}/init_data/engines/rotax/582.ini"     # per-engine
  - "{CONFIG}/init_data/custom.ini"                   # your edits win
```

Make your edits in `custom.ini` (and `database/custom.yaml`) so software updates
to the shipped files still reach you.

## Runtime state vs. configuration

Most values are *sensed* (written by a source every cycle). A few are *pilot
selections* — e.g. `NAVSRC` (the EFIS nav-source selector). Those aren't sensed,
so left alone they reset to their `init_data` default on every start. The
`state_persist` plugin (if enabled) remembers the last value of selected keys
across restarts, so a selection survives a reboot:

```yaml
state_persist:
  load: yes
  module: fixgw.plugins.state_persist
  keys: [NAVSRC]     # keys whose last value should persist
```

## Running

```bash
./fixGw.py                       # start the server (reads config/default.yaml)
./fixGw.py --config-file PATH     # use a specific config
./fixGwClient.py                 # console debug client
./fixGwClient.py --gui            # GUI debug client
```

On a deployed system the config commonly lives outside the source tree (e.g. a
per-aircraft config directory passed with `--config-file`), so upgrades to the
code don't disturb the airplane's settings.
