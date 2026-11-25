#!/usr/bin/env python3
"""Who writes each FIX key, and the CI ratchet on it (ARCHG1, AER-6350).

A static scan of this tree for proven writes to FIX keys, checked against two
committed records:

  src/fixgw/database/writers.yaml    the writer register: every key a module
                                     writes, its writers, its units, the datum
                                     of an altitude or position key, and the
                                     selector of a selected key.  A design
                                     record; the gateway never loads it.
  tests/arch/key_writers_baseline.yaml
                                     the keys allowed more than one live
                                     writer today, each with its writers.  It
                                     may only shrink.

and a third, `tests/arch/concept_names.yaml`, which pins where the classes
named for the route concepts (`Waypoint`, `Leg`, ...) are defined.

The write detection is a port of the ARCH0 inventory (maos-workspace
`makerplane/briefs/architecture_audit/inventory.py`): an AST walk over the
write calls, wrapper functions that hand a parameter to a write, module
constants that name a key, `item = get_item(KEY); item.value = v` (and
`.set()`, `.output_value()`, and an item from a function that returns one for
a key parameter), a loop over a module-level table of keys, and the
`outputs:`/`inputs:` paths of the shipped YAML (a canfix map's `inputs` are
bus-to-database writes; a compute function's `output` is its write).  Static
only: nothing under `plugins/` is imported.  The key catalog comes from the
real database loader, so templated keys (`CHTec` -> `CHT11`) expand as the
running gateway expands them.

Usage:
    python tools/check_key_writers.py                 # check; exit 1 on a failure
    python tools/check_key_writers.py --generate [--pyefis PYEFIS_CHECKOUT]

`--generate` rewrites both records from the scan.  It keeps what a scan cannot
know -- the datum entries and the pyEfis writers -- from the existing register,
or takes the pyEfis writers from a scan of a pyEfis checkout when one is given.
The ratchet is the check; regenerating to make it pass is the thing a reviewer
looks for.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REGISTER = os.path.join("src", "fixgw", "database", "writers.yaml")
BASELINE = os.path.join("tests", "arch", "key_writers_baseline.yaml")
CONCEPTS = os.path.join("tests", "arch", "concept_names.yaml")
COMPUTE_CONFIG = os.path.join("src", "fixgw", "config", "connections", "compute.yaml")

SKIP_DIRS = {".git", "node_modules", "tests", "test", "doc", "docs", "evidence",
             "__pycache__", "build", "dist", ".venv", "venv"}

# ---- FIX access API, by call name ----------------------------------------
WRITE_CALLS = {"db_write", "write", "set_value", "setValue"}
# Methods of an item that write it: `set(v)`, and pyEfis's `output_value()`,
# which sends the item's value to the gateway (a local `.value =` alone does
# not reach the bus in pyEfis).
ITEM_WRITES = {"set", "output_value"}
READ_CALLS = {"db_read", "read", "db_get_item", "get_item", "get_raw_item",
              "getItem", "db_get_value", "get_value"}

# ---- writer categories (fix-gateway module -> category) ------------------
SIM = {"xplane", "fgfs", "demo", "data_playback", "test", "sim", "flown_baseline"}
RELAY = {"netfix", "command", "state_persist", "db_persister", "data_recorder",
         "client", "server", "database", "plugins", "tools"}
COMPUTE = {"compute", "quorum", "cas", "annunciate", "flightplan", "system",
           "dimmer", "gdl90_out"}
# Categories whose writers count toward "more than one live writer".  A
# simulator or a relay (netfix carrying a client's write) stands in for, or
# passes on, a live writer; it is declared, but it is not a second source.
LIVE = ("source", "compute", "display")
REGISTER_LISTS = ("writers", "pyefis", "sim", "relay")   # register field per category
FIELD_OF = {"source": "writers", "compute": "writers", "display": "pyefis",
            "sim": "sim", "relay": "relay"}

TOOL_FILE = re.compile(r"(^|/)(tools|scripts|bench|examples?)/|(^|/)conftest\.py$|"
                       r"(^|/)(render_|bench_|make_|svs_capture)[^/]*\.py$")

# Altitude and position keys, by the catalog's description: these carry a datum.
DATUM_KEY = re.compile(r"altitude|latitude|longitude|el+ipsoid|above ground", re.I)

FIX_DECLARE = ("declare it in src/fixgw/database/writers.yaml and add the roadmap "
               "9.1 block (doc/key_writers.md)")
FIX_SELECTOR = ("add a selector: each source writes its own key and one compute "
                "`select` writes {key} (the bearing-pointer pattern, roadmap 9.1; "
                "doc/key_writers.md)")


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def walk(root, exts):
    for d, subdirs, files in os.walk(root):
        subdirs[:] = sorted(s for s in subdirs if s not in SKIP_DIRS)
        for f in sorted(files):
            if f.startswith("test_") or f.endswith("_test.py"):
                continue
            if os.path.splitext(f)[1] in exts:
                yield os.path.join(d, f)


def module_of(repo, rel):
    """fix-gateway: the plugin directory or file; pyEfis: the instrument."""
    parts = rel.split("/")
    if repo == "fix-gateway":
        if "plugins" in parts:
            i = parts.index("plugins")
            nxt = parts[i + 1] if len(parts) > i + 1 else ""
            return os.path.splitext(nxt)[0]
        if "sim" in parts:
            return "sim"
        if parts[0] == "tools":
            return "tools"
        if "config" in parts:
            stem = os.path.splitext(parts[-1])[0]
            return {"map": "canfix", "two_lane_map": "canfix"}.get(stem, stem)
        return parts[-2] if len(parts) > 1 else parts[0]
    if "instruments" in parts:
        i = parts.index("instruments")
        return "pyefis/instruments/" + os.path.splitext(parts[i + 1])[0]
    return "pyefis/" + (parts[-2] if len(parts) > 2 else parts[-1])


def category(repo, module, rel=""):
    if TOOL_FILE.search(rel):
        return "tool"
    if repo == "pyEfis":
        return "display"
    if module in SIM:
        return "sim"
    if module in RELAY:
        return "relay"
    if module in COMPUTE:
        return "compute"
    return "source"


# ---- catalog ---------------------------------------------------------------
def load_catalog(root):
    """key -> {"key", "description", "type", "units"}, from the real database
    loader (tools/dump_fix_keys.py), run in a child process so the caller's
    fixgw.database is left alone."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "fix_keys.json")
        subprocess.run([sys.executable, os.path.join(root, "tools", "dump_fix_keys.py"), "-o", out],
                       cwd=root, check=True, stdout=subprocess.DEVNULL)
        with open(out) as f:
            return {k["key"]: k for k in json.load(f)["keys"]}


def key_pattern_match(prefix, suffix, catalog):
    rx = re.compile("^" + re.escape(prefix) + r"[A-Za-z0-9]{1,3}" + re.escape(suffix) + "$")
    return sorted(k for k in catalog if rx.match(k))


# ---- python writes ---------------------------------------------------------
class KeyVisitor(ast.NodeVisitor):
    def __init__(self, catalog, consts, wrappers=None, tables=None, getters=None):
        self.wrappers = wrappers or {}
        self.cat = catalog
        self.consts = consts          # module-level NAME = "KEY"
        self.tables = tables or {}    # module-level NAME = [keys]
        self.getters = getters or {}  # functions returning the item for a key
        self.refs = []                # (line, key, how)
        self.items = [{}]             # scopes: var/attr name -> key
        self.loops = []               # loop variable -> keys of its table

    def keys_of(self, node):
        if isinstance(node, ast.Name):
            for scope in reversed(self.loops):
                if node.id in scope:
                    return scope[node.id], "loop over %s" % scope[node.id + "/table"]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in self.cat:
                return [node.value], "literal"
        if isinstance(node, ast.Name) and node.id in self.consts:
            return [self.consts[node.id]], "constant"
        if isinstance(node, ast.Attribute) and node.attr in self.consts:
            return [self.consts[node.attr]], "constant"
        if isinstance(node, ast.JoinedStr):
            vals = node.values
            pre = vals[0].value if vals and isinstance(vals[0], ast.Constant) else ""
            suf = vals[-1].value if len(vals) > 1 and isinstance(vals[-1], ast.Constant) else ""
            if pre:
                ks = key_pattern_match(pre, suf, self.cat)
                if ks:
                    return ks, "f-string %s{}%s" % (pre, suf)
        return [], ""

    def lookup_item(self, node):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            name = node.value.id + "." + node.attr
        for scope in reversed(self.items):
            if name in scope:
                return scope[name]
        return None

    def visit_FunctionDef(self, node):
        self.items.append({})
        self.generic_visit(node)
        self.items.pop()
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        # x = get_item("KEY")  -> remember x (and self.x) for later .value writes
        if isinstance(node.value, ast.Call):
            fn = call_name(node.value)
            idx = 0 if fn in READ_CALLS else self.getters.get(fn)
            if idx is not None and len(node.value.args) > idx:
                ks, _ = self.keys_of(node.value.args[idx])
                if ks:
                    for t in node.targets:
                        n = (t.id if isinstance(t, ast.Name) else
                             t.value.id + "." + t.attr
                             if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                             else None)
                        if n:
                            scope = self.items[0] if n.startswith("self.") else self.items[-1]
                            scope[n] = ks
        for t in node.targets:
            if isinstance(t, ast.Attribute) and t.attr == "value":
                for k in self.lookup_item(t.value) or []:
                    self.refs.append((node.lineno, k, "item.value ="))
        self.generic_visit(node)

    def visit_Call(self, node):
        fn = call_name(node)
        idx = self.wrappers.get(fn, 0)
        if (fn in WRITE_CALLS or fn in self.wrappers) and len(node.args) > idx:
            ks, how = self.keys_of(node.args[idx])
            for k in ks:
                self.refs.append((node.lineno, k, "%s(%s)" % (fn, how)))
        elif fn in ITEM_WRITES and isinstance(node.func, ast.Attribute):
            for k in self.lookup_item(node.func.value) or []:
                self.refs.append((node.lineno, k, "item.%s()" % fn))
        self.generic_visit(node)

    def visit_For(self, node):
        # for key in TABLE / for key, v in zip(TABLE, ...) / enumerate(TABLE):
        # the loop variable stands for each key of the table, in the body only.
        bound = {}
        it, tgt = node.iter, node.target
        if isinstance(it, ast.Call) and call_name(it) == "enumerate" and it.args \
                and isinstance(tgt, ast.Tuple) and len(tgt.elts) == 2:
            it, tgt = it.args[0], tgt.elts[1]
        if isinstance(it, ast.Call) and call_name(it) == "zip" and isinstance(tgt, ast.Tuple):
            pairs = zip(it.args, tgt.elts)
        else:
            pairs = [(it, tgt)]
        for src, var in pairs:
            if isinstance(src, ast.Name) and src.id in self.tables and isinstance(var, ast.Name):
                bound[var.id] = self.tables[src.id]
                bound[var.id + "/table"] = src.id
        self.visit(node.iter)
        self.loops.append(bound)
        for n in node.body:
            self.visit(n)
        self.loops.pop()
        for n in node.orelse:
            self.visit(n)


def find_wrappers(tree):
    """name -> positional index of the key argument, for module functions that
    hand a parameter to a write (`_setval(key, v)` -> db_write(key, v),
    `_set(key, v)` -> items[key].set(v)).  Two passes, so a wrapper of a
    wrapper is found too."""
    wr = {}
    for _ in range(2):
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
            alias = {}   # item = db_get_item(key)  ->  item stands for key
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and \
                        isinstance(n.targets[0], ast.Name):
                    k = subscript_name(n.value)
                    if k in params:
                        alias[n.targets[0].id] = k
            for n in ast.walk(fn):
                key = None
                if isinstance(n, ast.Call):
                    name = call_name(n)
                    idx = wr.get(name, 0) if (name in WRITE_CALLS or name in wr) else None
                    if idx is not None and len(n.args) > idx and isinstance(n.args[idx], ast.Name):
                        key = n.args[idx].id
                    if name == "set" and isinstance(n.func, ast.Attribute):
                        v = n.func.value
                        key = subscript_name(v) or (alias.get(v.id) if isinstance(v, ast.Name) else None)
                elif isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Attribute) and t.attr == "value":
                            v = t.value
                            key = key or subscript_name(v) or \
                                (alias.get(v.id) if isinstance(v, ast.Name) else None)
                if key in params and fn.name not in WRITE_CALLS:
                    wr[fn.name] = params.index(key)
    return wr


def find_getters(tree):
    """name -> positional index of the key argument, for functions that
    return the item for a key parameter (`_item(key)`: `item =
    self.lookup(key) ... return item`), so `x = self._item(KEY)` binds x the
    way `x = get_item(KEY)` does."""
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
        alias = {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and \
                    isinstance(n.targets[0], ast.Name):
                k = item_of_param(n.value)
                if k in params:
                    alias[n.targets[0].id] = k
        for n in ast.walk(fn):
            if isinstance(n, ast.Return) and n.value is not None:
                v = n.value
                k = alias.get(v.id) if isinstance(v, ast.Name) else item_of_param(v)
                if k in params:
                    out[fn.name] = params.index(k)
    return out


def item_of_param(node):
    """`f(key)` (any call) or `items[key]` / `items.get(key)` -> 'key'."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name):
        return node.slice.id
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Name):
        return node.args[0].id
    return None


def module_tables(tree, catalog):
    """NAME -> keys, for module-level lists of keys: literals, a
    comprehension over `"CASMSG{}".format(n)` or `f"CASMSG{n}"`, and `+` or
    `list()` of tables already found."""
    tables = {}

    def keys(node):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            ks = [e.value for e in node.elts
                  if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if ks and len(ks) == len(node.elts) and all(k in catalog for k in ks):
                return ks
            return None
        if isinstance(node, ast.ListComp):
            e = node.elt
            if isinstance(e, ast.Call) and call_name(e) == "format" and \
                    isinstance(e.func, ast.Attribute) and isinstance(e.func.value, ast.Constant) \
                    and isinstance(e.func.value.value, str) and e.func.value.value.count("{}") == 1:
                pre, suf = e.func.value.value.split("{}")
            elif isinstance(e, ast.JoinedStr) and e.values and isinstance(e.values[0], ast.Constant):
                pre = e.values[0].value
                suf = e.values[-1].value if len(e.values) > 1 and \
                    isinstance(e.values[-1], ast.Constant) else ""
            else:
                return None
            return key_pattern_match(pre, suf, catalog) if pre else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = keys(node.left), keys(node.right)
            return a + b if a is not None and b is not None else None
        if isinstance(node, ast.Call) and call_name(node) in ("list", "tuple") and \
                len(node.args) == 1:
            return keys(node.args[0])
        if isinstance(node, ast.Name):
            return tables.get(node.id)
        return None

    for node in tree.body:
        if isinstance(node, ast.Assign):
            ks = keys(node.value)
            if ks:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        tables[t.id] = ks
    return tables


def subscript_name(node):
    """items[key] or get_item(key) -> 'key' when the index is a bare name."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name):
        return node.slice.id
    if isinstance(node, ast.Call) and call_name(node) in READ_CALLS and node.args \
            and isinstance(node.args[0], ast.Name):
        return node.args[0].id
    return None


def call_name(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def module_consts(tree, catalog):
    consts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and node.value.value in catalog:
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value
                elif isinstance(t, ast.Attribute):
                    consts[t.attr] = node.value.value
    return consts


# ---- yaml writes -----------------------------------------------------------
def yaml_writes(path, catalog, rel):
    """Scalars equal to a key under a write path: a canfix map's `inputs`
    (bus -> database), elsewhere an `output`/`outputs`."""
    if "/config/database" in "/" + rel:
        return []
    try:
        root = yaml.compose(read(path))
    except Exception:
        return []
    out = []
    canfix = "canfix" in rel

    def rec(node, trail):
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                rec(v, trail + [str(getattr(k, "value", ""))])
        elif isinstance(node, yaml.SequenceNode):
            for v in node.value:
                rec(v, trail)
        elif isinstance(node, yaml.ScalarNode):
            keys = [node.value] if node.value in catalog else \
                templated_keys(node.value, catalog) if canfix and trail[-1:] == ["fixid"] else []
            t = "/".join(trail).lower()
            if (canfix and "inputs" in t) or \
                    (not canfix and re.search(r"(^|/)outputs?(/|$)", t)):
                for key in keys:
                    out.append((node.start_mark.line + 1, key, "yaml:" + "/".join(trail[-3:])))
    if root is not None:
        rec(root, [])
    return out


def templated_keys(name, catalog):
    """The keys a canfix input names with a database variable (`index: c`,
    `fixid: CHT1c`): each lower-case letter stands for an instance number."""
    if not re.fullmatch(r"[A-Z][A-Z0-9]*[a-z][A-Za-z0-9]*", name):
        return []
    rx = re.compile("^" + re.sub(r"[a-z]", "[0-9]{1,3}", name) + "$")
    return sorted(k for k in catalog if rx.match(k))


# ---- scan ------------------------------------------------------------------
def scan_writes(root, catalog, repo="fix-gateway"):
    """Proven writes: dicts of key, module, category, file, line, how.
    Tool files are left out: they write to a bench, not to an aircraft."""
    writes = []
    for path in walk(root, {".py", ".yaml", ".yml"}):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        mod = module_of(repo, rel)
        cat = category(repo, mod, rel)
        if cat == "tool":
            continue
        if path.endswith(".py"):
            try:
                tree = ast.parse(read(path))
            except SyntaxError:
                continue
            v = KeyVisitor(catalog, module_consts(tree, catalog), find_wrappers(tree),
                           module_tables(tree, catalog), find_getters(tree))
            v.visit(tree)
            found = v.refs
        elif repo == "fix-gateway":
            found = yaml_writes(path, catalog, rel)
        else:
            continue
        for line, key, how in found:
            writes.append(dict(key=key, module=mod, category=cat, file=rel, line=line, how=how))
    return writes


def scan_selectors(root):
    """key -> selector, for each compute `select` in the shipped compute config:
    inputs[0] is the selector key, the rest are the sources."""
    path = os.path.join(root, COMPUTE_CONFIG)
    if not os.path.exists(path):
        return {}
    cfg = (yaml.safe_load(read(path)) or {}).get("compute") or {}
    out = {}
    for f in (cfg.get("functions") or []):
        if f.get("function") == "select" and f.get("output"):
            ins = f.get("inputs") or []
            out[f["output"]] = {"module": "compute", "by": ins[0] if ins else None,
                                "sources": list(ins[1:])}
    return out


def writers_by_key(writes):
    """key -> field -> sorted module list."""
    by = defaultdict(lambda: defaultdict(set))
    for w in writes:
        by[w["key"]][FIELD_OF[w["category"]]].add(w["module"])
    return {k: {f: sorted(m) for f, m in v.items()} for k, v in by.items()}


def live_writers(entry):
    return set(entry.get("writers") or []) | set(entry.get("pyefis") or [])


# ---- records ---------------------------------------------------------------
def load_yaml(root, rel):
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        return {}
    return yaml.safe_load(read(p)) or {}


def build_register(catalog, writes, selectors, old, pyefis_writes=None):
    """The register from a scan.  Datum and `no_writer` entries are kept from
    `old`; pyEfis writers come from `pyefis_writes` when given, else from
    `old`."""
    found = writers_by_key(writes)
    pyefis = writers_by_key(pyefis_writes) if pyefis_writes is not None else \
        {k: {"pyefis": v["pyefis"]} for k, v in old.items() if v.get("pyefis")}
    reg = {}
    unwritten = {k for k, v in old.items() if (v or {}).get("no_writer")}
    for key in sorted(set(found) | set(pyefis) | unwritten):
        if key not in catalog:
            continue
        e = {"units": catalog[key].get("units") or ""}
        if (old.get(key) or {}).get("datum"):
            e["datum"] = old[key]["datum"]
        elif needs_datum(key, catalog):
            e["datum"] = "unrecorded"
        for field in REGISTER_LISTS:
            mods = (pyefis.get(key, {}) if field == "pyefis" else found.get(key, {})).get(field)
            if mods:
                e[field] = mods
        if key in selectors:
            e["selector"] = selectors[key]
        if key in unwritten and not any(e.get(f) for f in REGISTER_LISTS):
            e["no_writer"] = old[key]["no_writer"]
        reg[key] = e
    return reg


def needs_datum(key, catalog):
    """Altitude and position keys carry a datum."""
    k = catalog.get(key, {})
    return k.get("type") == "float" and bool(DATUM_KEY.search(k.get("description") or ""))


def build_baseline(register):
    return {k: sorted(live_writers(e)) for k, e in sorted(register.items())
            if len(live_writers(e)) > 1}


def dump_register(reg):
    lines = [
        "# FIX key writer register (ARCHG1, AER-6350).  A design record: the gateway",
        "# never loads this file, and the database format does not change with it.",
        "#",
        "# One entry per key that some module writes:",
        "#   units     the catalog's units (database.yaml)",
        "#   datum     altitude and position keys: the datum each writer puts on the",
        "#             bus.  A mapping is per writer, and where they differ the key is",
        "#             wrong today; `unrecorded` means nobody has read the code yet.",
        "#   writers   live writers in fix-gateway: plugins and compute modules",
        "#   pyefis    live writers in pyEfis (ARCHG2 checks these from pyEfis's side)",
        "#   sim       simulators and replays, which stand in for a live writer",
        "#   relay     modules that pass a client's write on (netfix, command, ...)",
        "#   selector  a compute `select`: `by` picks one of `sources`, and the",
        "#             selector is the key's only live writer",
        "#   no_writer a key declared for a writer nothing shipped provides yet, and",
        "#             why (a panel button's key that no shipped config wires up)",
        "#",
        "# tools/check_key_writers.py checks this file against the code; see",
        "# doc/key_writers.md for how to declare a writer.",
        "",
    ]
    for key, e in reg.items():
        lines.append("%s: %s" % (key, flow(e)))
    return "\n".join(lines) + "\n"


def flow(obj):
    return yaml.safe_dump(obj, default_flow_style=True, sort_keys=False, width=10 ** 6).strip()


def dump_baseline(base):
    lines = [
        "# Keys allowed more than one live writer today (ARCHG1, AER-6350), each with",
        "# the writers it has.  tests/arch/test_key_writers.py fails when a key not",
        "# listed here gains a second live writer, when a listed key gains a writer,",
        "# and when a listed key has lost one -- so this file only shrinks.  Remove",
        "# an entry by giving the key one writer (a selector, roadmap 9.1).",
        "# Live writers: `writers` and `pyefis` in src/fixgw/database/writers.yaml.",
        "# %d keys." % len(base),
        "",
    ]
    for key, mods in base.items():
        lines.append("%s: %s" % (key, flow(mods)))
    return "\n".join(lines) + "\n"


# ---- checks ----------------------------------------------------------------
def check_register(catalog, writes, selectors, register):
    """Failures: undeclared writes, stale declarations, selectors, datums."""
    fails = []
    found = writers_by_key(writes)
    where = {}
    for w in writes:
        where.setdefault((w["key"], w["module"]), "%s:%d" % (w["file"], w["line"]))
    for key in sorted(found):
        declared = register.get(key) or {}
        for field, mods in sorted(found[key].items()):
            for m in mods:
                if m not in (declared.get(field) or []):
                    other = [f for f in REGISTER_LISTS if m in (declared.get(f) or [])]
                    fails.append(
                        "%s: %s writes it (%s) and writers.yaml does not list %s under `%s`%s; %s"
                        % (key, m, where[(key, m)], m, field,
                           " (it is under `%s`)" % other[0] if other else "", FIX_DECLARE))
    for key, e in sorted(register.items()):
        if key not in catalog:
            fails.append("%s: in writers.yaml but not in the database; remove the entry" % key)
            continue
        for field in ("writers", "sim", "relay"):
            for m in e.get(field) or []:
                if m not in found.get(key, {}).get(field, []):
                    fails.append("%s: writers.yaml lists %s under `%s`, and the scan finds no "
                                 "write from it; remove it from writers.yaml" % (key, m, field))
        written = [m for f in REGISTER_LISTS for m in e.get(f) or []]
        if not written and not e.get("no_writer"):
            fails.append("%s: writers.yaml lists no writer; remove the entry, or say why it "
                         "has none in `no_writer`" % key)
        if written and e.get("no_writer"):
            fails.append("%s: writers.yaml says `no_writer`, and %s writes it; remove "
                         "`no_writer`" % (key, ", ".join(written)))
        if needs_datum(key, catalog) and not e.get("datum"):
            fails.append("%s: an altitude or position key needs a `datum` in writers.yaml "
                         "(`unrecorded` if nobody has read the writer)" % key)
        if isinstance(e.get("datum"), dict):
            for m in e["datum"]:
                if m not in live_writers(e) | set(e.get("sim") or []):
                    fails.append("%s: writers.yaml gives a datum for %s, which is not one of "
                                 "its writers" % (key, m))
        sel = e.get("selector")
        if sel != selectors.get(key):
            fails.append("%s: writers.yaml selector %r does not match compute.yaml's %r; "
                         "copy it from compute.yaml" % (key, sel, selectors.get(key)))
        if sel and live_writers(e) != {sel["module"]}:
            extra = sorted(live_writers(e) - {sel["module"]})
            fails.append("%s: has a selector, so %s must be its only live writer; %s also "
                         "write it.  Write a source key instead and add it to the "
                         "selector's inputs" % (key, sel["module"], ", ".join(extra)))
    for key in sorted(selectors):
        if key not in register:
            fails.append("%s: compute.yaml selects it and writers.yaml has no entry; %s"
                         % (key, FIX_DECLARE))
    return fails


def upper_first(s):
    return s[:1].upper() + s[1:]


def check_baseline(register, baseline):
    """Failures: a new multi-writer key, a baseline key that gained or lost a
    writer, and an entry for a key no longer multi-written."""
    fails = []
    for key, e in sorted(register.items()):
        live = live_writers(e)
        if len(live) < 2 or e.get("selector"):
            continue
        if key not in baseline:
            fails.append("%s: %d live writers (%s), and it is not on the baseline; %s"
                         % (key, len(live), ", ".join(sorted(live)), FIX_SELECTOR.format(key=key)))
            continue
        allowed = set(baseline[key] or [])
        if live - allowed:
            fails.append("%s: gained live writer(s) %s; the baseline allows only %s.  %s"
                         % (key, ", ".join(sorted(live - allowed)), ", ".join(sorted(allowed)),
                            upper_first(FIX_SELECTOR.format(key=key))))
    for key in sorted(baseline):
        live = live_writers(register.get(key) or {})
        allowed = set(baseline[key] or [])
        if len(live) < 2:
            fails.append("%s: now has %d live writer(s) and is still on the baseline; remove "
                         "it from tests/arch/key_writers_baseline.yaml" % (key, len(live)))
        elif allowed - live:
            fails.append("%s: lost writer(s) %s; remove them from its baseline entry"
                         % (key, ", ".join(sorted(allowed - live))))
    return fails


# ---- concept names ---------------------------------------------------------
TYPE_FACTORIES = {"namedtuple", "NamedTuple", "TypedDict", "make_dataclass"}


def concept_definitions(root, names):
    """name -> sorted files defining a class (or a namedtuple/TypedDict) of
    that name, in src/ and tools/."""
    out = defaultdict(set)
    for top in ("src", "tools"):
        for path in walk(os.path.join(root, top), {".py"}):
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                tree = ast.parse(read(path))
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef) and n.name in names:
                    out[n.name].add(rel)
                elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and \
                        call_name(n.value) in TYPE_FACTORIES:
                    for t in n.targets:
                        if isinstance(t, ast.Name) and t.id in names:
                            out[t.id].add(rel)
    return {k: sorted(v) for k, v in out.items()}


def check_concepts(root, allowed):
    fails = []
    found = concept_definitions(root, set(allowed))
    for name in sorted(allowed):
        have, ok = set(found.get(name, [])), set(allowed[name] or [])
        for f in sorted(have - ok):
            fails.append("class %s: defined in %s, and concept_names.yaml allows it only in %s.  "
                         "Use the existing %s, or name the new shape for what it is; adding "
                         "the file to tests/arch/concept_names.yaml is an architecture "
                         "change (ORG-018) for review" % (name, f, ", ".join(sorted(ok)) or "no file", name))
        for f in sorted(ok - have):
            fails.append("class %s: concept_names.yaml lists %s, which no longer defines it; "
                         "remove the file from the list" % (name, f))
    return fails


# ---- driver ----------------------------------------------------------------
def run_checks(root=ROOT, catalog=None):
    catalog = catalog if catalog is not None else load_catalog(root)
    writes = scan_writes(root, catalog)
    selectors = scan_selectors(root)
    register = load_yaml(root, REGISTER)
    baseline = load_yaml(root, BASELINE)
    return (check_register(catalog, writes, selectors, register),
            check_baseline(register, baseline),
            check_concepts(root, load_yaml(root, CONCEPTS)))


def generate(root, pyefis_root=None):
    catalog = load_catalog(root)
    writes = scan_writes(root, catalog)
    pw = scan_writes(pyefis_root, catalog, repo="pyEfis") if pyefis_root else None
    reg = build_register(catalog, writes, scan_selectors(root), load_yaml(root, REGISTER), pw)
    base = build_baseline(reg)
    for rel, text in ((REGISTER, dump_register(reg)), (BASELINE, dump_baseline(base))):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    print("wrote %s (%d keys) and %s (%d multi-writer keys)" % (REGISTER, len(reg), BASELINE, len(base)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--generate", action="store_true", help="rewrite the register and baseline")
    ap.add_argument("--pyefis", help="pyEfis checkout to take the pyEfis writers from (--generate)")
    a = ap.parse_args()
    if a.generate:
        generate(a.root, a.pyefis)
        return 0
    fails = [f for group in run_checks(a.root) for f in group]
    for f in fails:
        print(f)
    print("%d failure(s)" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
