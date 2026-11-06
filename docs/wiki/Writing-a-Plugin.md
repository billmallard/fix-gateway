# Writing a Plugin

A plugin is a Python module with a `Plugin` class that talks to the database.
Because everything goes through the database, a new plugin needs to know nothing
about any other plugin — read the keys you need, write the keys you produce.

Start from [`src/fixgw/plugins/skel.py`](https://github.com/billmallard/fix-gateway/blob/dev/src/fixgw/plugins/skel.py)
and read [`doc/plugin.rst`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugin.rst).

## The shape

```python
import threading, time
import fixgw.plugin as plugin

class MainThread(threading.Thread):
    def __init__(self, parent):
        super().__init__()
        self.getout = False
        self.parent = parent
        self.log = parent.log

    def run(self):
        while not self.getout:
            time.sleep(1)
            # ...read a device, then write to the database:
            # self.parent.db_write("IAS", value)
        self.parent.running = False

    def stop(self):
        self.getout = True

class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super().__init__(name, config, config_meta)
        self.thread = MainThread(self)

    def run(self):
        self.thread.start()          # MUST return promptly

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(1.0)
        if self.thread.is_alive():
            raise plugin.PluginFail   # couldn't stop cleanly
```

Rules that matter:

- **`run()` must return quickly.** The server blocks on it during startup — put
  continuous work on a thread and start it here. (A pure callback plugin needs no
  thread at all: register callbacks in `run()` and return.)
- **`stop()` must stop cleanly and promptly.** Use short I/O timeouts + a flag.
  Raise `plugin.PluginFail` if a thread won't join, so the server can exit and be
  restarted rather than hang.
- Set **`self.running = False`** when your work loop ends. The server checks
  it about every four seconds and exits when no plugin is running.
- If you override `__init__`, call `super().__init__(...)` first.

## The database API (from `PluginBase`)

| Call | Does |
|---|---|
| `self.db_read(key)` | read the current value |
| `self.db_write(key, value)` | write a value (a source's main job) |
| `self.db_get_item(key)` | get the raw item (to set `.bad`/`.fail`/aux directly) |
| `self.db_callback_add(key, fn, udata=None)` | subscribe: `fn` is called when `key` changes |
| `self.db_callback_del(key, fn, udata=None)` | unsubscribe (do this in `stop()`) |
| `self.config` | your plugin's settings dict (from its connection YAML) |
| `self.log` | a logger (`.info/.debug/.warning/.error`) |

**Callback signature** — `fn(key, value, udata)` — where `value` is the item
tuple `(value, annunciate, old, bad, fail, secfail)`:

```python
def on_ias(key, value, udata):
    if not isinstance(value, tuple):
        return                    # meta/aux updates arrive as non-tuples; skip
    v, annunc, old, bad, fail, secfail = value
    ...
```

## Two idioms worth copying

**A source** reads a device and writes keys — and flags them honestly. Prefer
letting `tol` handle staleness (just stop writing when the device is quiet), and
set `bad`/`fail` when you *know* the data is wrong:

```python
self.db_write("OILP1", psi)
self.db_get_item("OILP1").fail = sensor_open_circuit
```

**A consumer / derived plugin** subscribes and reacts. Register in `run()`,
unregister in `stop()`:

```python
def run(self):
    for key in self.config.get("keys", []):
        self.db_callback_add(key, self.on_change)
```

Two house patterns you'll see throughout the codebase, worth following:

- **Construct-never-raises:** a missing device / bad config should degrade
  gracefully (log + no-op), never crash the gateway on startup.
- **Ignore non-tuple callbacks:** metadata/aux updates fire your callback with a
  non-tuple `value` — guard with `isinstance(value, tuple)`.

## `get_status()`

Optionally return a dict of status the user can see (connection counts, message
counters). An `OrderedDict` keeps your fields in order:

```python
def get_status(self):
    return OrderedDict([("connected", self.connected),
                        ("messages", self.msg_count)])
```

## Wiring it in

Four coordinated edits load your plugin (same as any other — see
**[[Configuration]]**):

1. `connections/<name>.yaml` — `{ load: NAME, module: fixgw.plugins.<name>, ... }`
2. `preferences.yaml.custom` — `includes: { NAME_CONFIG: connections/<name>.yaml }`
3. `preferences.yaml.custom` — `enabled: { NAME: true }`
4. `default.yaml` — add `- NAME_CONFIG` to the `connections: include:` list

Put the module under `src/fixgw/plugins/`, add a test under `tests/`, and run
`make test`. The suite includes `tests/test_one_writer.py`, which starts the
gateway from each shipped configuration and fails if any key has two writers.
