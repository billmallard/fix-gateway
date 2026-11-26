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
bus-to-database writes; a compute function's `output` is its write).  For
pyEfis (ARCHG2b): a wrapper over such a getter, a function that returns a key
name built from its parameters, a loop over a module-level tuple of rows, a
`BugBinding("KEY")`'s `.item`, and a `# writes: KEY` comment on a write whose
item comes from elsewhere.  Static only: nothing under `plugins/` is imported.  The key catalog comes from the
real database loader, so templated keys (`CHTec` -> `CHT11`) expand as the
running gateway expands them.

Usage:
    python tools/check_key_writers.py                 # check; exit 1 on a failure
    python tools/check_key_writers.py --generate [--pyefis PYEFIS_CHECKOUT]

and, for the provider declaration (SRC4, AER-6455), the per-writer `datum`,
`quality`, `on_loss` and `rate_hz` columns of the ARCH2 section 3.2 source
keys, with `tests/arch/datum_baseline.yaml`: the keys whose writers declare
more than one datum today.  It may only shrink.

`--generate` rewrites the records from the scan.  It keeps what a scan cannot
know -- the provider columns and the pyEfis writers -- from the existing register,
or takes the pyEfis writers from a scan of a pyEfis checkout when one is given.
The ratchet is the check; regenerating to make it pass is the thing a reviewer
looks for.
"""
import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
from collections import defaultdict

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REGISTER = os.path.join("src", "fixgw", "database", "writers.yaml")
BASELINE = os.path.join("tests", "arch", "key_writers_baseline.yaml")
CONCEPTS = os.path.join("tests", "arch", "concept_names.yaml")
DATUM_BASELINE = os.path.join("tests", "arch", "datum_baseline.yaml")
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
# Objects made for one key whose `.item` is that key's item: pyEfis's
# `bugs.BugBinding("BANKBUG", self)`.
BINDINGS = {"BugBinding"}
# A write whose item the scan cannot name -- it comes from the panel config,
# through another module -- says which keys it reaches (ARCHG2b):
#     item.output_value()  # writes: HEADBUG, NAV1CRS
# on the write's line, or alone on the line above it.
WRITES_COMMENT = re.compile(r"#\s*writes:\s*(.*)$")

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

# ---- the provider declaration (SRC4, AER-6455) ---------------------------
# The source keys of ARCH2 section 3.2 (maos-workspace
# makerplane/briefs/architecture_audit.md): position, GPS altitude and
# quality, indicated altitude, attitude, accelerations, airspeed, the baro
# setting, the active waypoint and the navigation guidance.  Each writer of
# one of these declares, per key, what it signals about quality and what it
# does when it loses its input; altitude, position, heading, track and
# accuracy keys also declare the datum or statistic it writes.
PROVIDER_KEYS = (
    "LAT", "LONG", "GS", "TRACK",
    "GPS_ELLIPSOID_ALT", "TALT",
    "GPS_FIX_TYPE", "GPS_ACCURACY_HORIZ", "GPS_ACCURACY_VERTICAL", "GPS_ACCURACY_SPEED",
    "GPS_SATS_VISIBLE", "GPS_SATS_TRACKED",
    "ALT",
    "PITCH", "ROLL", "HEAD",
    "ALAT", "ANORM", "ROT",
    "IAS", "TAS",
    "BARO",
    "WPLAT", "WPLON", "WPNAME",
    "EXTCRS", "EXTCDI", "EXTTF", "EXTXTK", "GPSCRS", "GPSCDI", "COURSE", "CDI",
)
DIRECTION_KEYS = {"HEAD", "TRACK"}
STATISTIC_KEYS = {"GPS_ACCURACY_HORIZ", "GPS_ACCURACY_VERTICAL", "GPS_ACCURACY_SPEED"}
COLUMNS = ("datum", "quality", "on_loss", "rate_hz")

# A cell is "<token>[, <token>...][: note]".
DATUMS = {
    # altitude
    "indicated", "pressure", "msl", "ellipsoid", "above_home", "above_ground", "density",
    # position
    "wgs84",
    # heading and track
    "magnetic", "true",
    # the statistic of an accuracy key: a 95 % bound, one sigma, a constant
    # the writer assumes, or a source that does not say
    "p95", "one_sigma", "assumed", "unstated",
    # nobody has read the writer's code
    "unrecorded",
}
# These two cannot be shown to agree with anything, even each other.
UNKNOWN_DATUMS = {"unrecorded", "unstated"}
# A flag the writer sets on the key itself, `passthrough` for one that copies
# its source's flags (the canfix sender's, a compute select's), or the FIX
# keys it writes that qualify this one (GPS_FIX_TYPE, GPS_ACCURACY_HORIZ).
QUALITY_FLAGS = {"none", "bad", "fail", "passthrough"}
ON_LOSS = {
    "silence",      # stops writing; the key goes old after its tol
    "fail",         # sets the fail flag
    "holds_last",   # keeps writing its last value, so a dead source stays fresh
    "writes_zero",  # writes a valid 0 or a default
    "on_change",    # a setting or an engine output, written when it changes
    "follows",      # a compute select: the chosen source's value and flags
}

FIX_DECLARE = ("declare it in src/fixgw/database/writers.yaml and add the roadmap "
               "9.1 block (doc/key_writers.md)")
FIX_SELECTOR = ("add a selector: each source writes its own key and one compute "
                "`select` writes {key} (the bearing-pointer pattern, roadmap 9.1; "
                "doc/key_writers.md)")


class ScanError(ValueError):
    """A `# writes:` comment the scan cannot honour."""


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
            return {"map": "canfix", "two_lane_map": "canfix", "fix_fgfs": "fgfs"}.get(stem, stem)
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


# A placeholder in a key name: any short instance (`QVOTE{n}`), or digits
# where the format says so (`"FPLIMP%dID" % i`).
ANY_PART = r"[A-Za-z0-9]{1,3}"
DIGIT_PART = r"[0-9]{1,3}"
INT = "int"                    # a loop counter: range() or enumerate()
PCT_SPEC = re.compile(r"%(?:\([^)]*\))?[-#0 +]*[0-9*]*(?:\.[0-9*]+)?([diouxXeEfFgGcrsa%])")
BRACE_SPEC = re.compile(r"\{([^{}!:]*)(?:![rsa])?(?::[^{}]*)?\}")


def template_match(parts, catalog):
    """The keys a built name can be: `parts` alternates literal text with
    placeholder regexes.  None unless it starts with literal text."""
    if not parts or not isinstance(parts[0], str) or not parts[0]:
        return []
    rx = re.compile("^" + "".join(re.escape(p) if isinstance(p, str) else p[0]
                                  for p in parts) + "$")
    return sorted(k for k in catalog if rx.match(k))


# ---- python writes ---------------------------------------------------------
class KeyVisitor(ast.NodeVisitor):
    def __init__(self, catalog, consts, wrappers=None, tables=None, getters=None,
                 builders=None, literals=None, declared=None):
        self.wrappers = wrappers or {}
        self.cat = catalog
        self.consts = consts          # module-level NAME = "KEY"
        self.tables = tables or {}    # module-level NAME = [keys]
        self.getters = getters or {}  # functions returning the item for a key
        self.builders = builders or {}  # functions returning a key name: params, expr
        self.literals = literals or {}  # module-level NAME = (literal rows)
        self.declared = declared or {}  # line -> keys of its `# writes:` comment
        self.declared_used = {}       # line -> "declared" or "named" (the scan knew it)
        self.subst = [{}]             # a name function's parameters -> the call's arguments
        self.refs = []                # (line, key, how)
        self.items = [{}]             # scopes: var/attr name -> key
        self.loops = [{}]             # `for x in ("A", "B")` or a table: x -> its strings; a counter -> INT

    def keys_of(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in self.cat:
                return [node.value], "literal"
        if isinstance(node, ast.Name) and node.id in self.consts:
            return [self.consts[node.id]], "constant"
        if isinstance(node, ast.Attribute) and node.attr in self.consts:
            return [self.consts[node.attr]], "constant"
        if isinstance(node, ast.Name) and isinstance(self.loop_values(node), list):
            ks = [k for k in self.loop_values(node) if k in self.cat]
            if ks:
                return ks, "loop over %s" % (self.loop_values(node, "/table") or "literals")
        if isinstance(node, ast.Call) and call_name(node) in self.builders:
            params, expr = self.builders[call_name(node)]
            sub = dict(zip(params, node.args))
            sub.update((k.arg, k.value) for k in node.keywords if k.arg in params)
            self.subst.append(sub)
            try:
                ks, how = self.keys_of(expr)
            finally:
                self.subst.pop()
            return ks, ("%s() %s" % (call_name(node), how)) if ks else ""
        parts, how = self.name_parts(node)
        if parts:
            ks = template_match(parts, self.cat)
            if ks:
                return ks, "%s %s" % (how, "".join(p if isinstance(p, str) else p[1] for p in parts))
        return [], ""

    def loop_values(self, node, suffix=""):
        for scope in reversed(self.loops):
            if node.id in scope:
                return scope.get(node.id + suffix)
        return None

    def placeholder(self, node, spec_char=""):
        """(regex, shown) for one placeholder: the strings a loop binds it
        to, else an instance.  In a name function, a parameter stands for the
        call's argument."""
        if isinstance(node, ast.Name) and node.id in self.subst[-1]:
            node = self.subst[-1][node.id]
            if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
                return re.escape(str(node.value)), str(node.value)
        vals = self.loop_values(node) if isinstance(node, ast.Name) else None
        if vals == INT or (spec_char and spec_char in "diu"):
            return DIGIT_PART, "{n}"
        if vals:
            return "(?:%s)" % "|".join(re.escape(v) for v in vals), "{%s}" % node.id
        return ANY_PART, "{}"

    def name_parts(self, node):
        """A key name built at run time, as literal text and placeholders:
        an f-string, `"FPL%dID" % i`, or `"FPL{}ID".format(i)`."""
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    parts.append(self.placeholder(v.value))
            return parts, "f-string"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and \
                isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            args = list(node.right.elts) if isinstance(node.right, ast.Tuple) else [node.right]
            parts, pos, text = [], 0, node.left.value
            for m in PCT_SPEC.finditer(text):
                parts.append(text[pos:m.start()])
                if m.group(1) == "%":
                    parts.append("%")
                else:
                    arg = args.pop(0) if args else None
                    parts.append(self.placeholder(arg, m.group(1)))
                pos = m.end()
            parts.append(text[pos:])
            return merge_literals(parts), "%-format"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr == "format" and isinstance(node.func.value, ast.Constant) and \
                isinstance(node.func.value.value, str):
            text = node.func.value.value.replace("{{", "\0").replace("}}", "\1")
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            parts, pos, auto = [], 0, 0
            for m in BRACE_SPEC.finditer(text):
                parts.append(text[pos:m.start()])
                field = m.group(1)
                if field == "":
                    arg = node.args[auto] if auto < len(node.args) else None
                    auto += 1
                elif field.isdigit():
                    arg = node.args[int(field)] if int(field) < len(node.args) else None
                else:
                    arg = kw.get(field)
                parts.append(self.placeholder(arg))
                pos = m.end()
            parts.append(text[pos:])
            parts = [p.replace("\0", "{").replace("\1", "}") if isinstance(p, str) else p
                     for p in parts]
            return merge_literals(parts), ".format"
        return [], ""

    def visit_For(self, node):
        # for key in ("A", "B"): / for key, v in (("A", 1), ("B", 2)):
        bound = {}
        elts = node.iter.elts if isinstance(node.iter, (ast.Tuple, ast.List)) else \
            self.literals.get(node.iter.id, []) if isinstance(node.iter, ast.Name) else []
        counter = isinstance(node.iter, ast.Call) and call_name(node.iter) in ("range", "enumerate")
        if counter and call_name(node.iter) == "range" and isinstance(node.target, ast.Name):
            bound[node.target.id] = INT
        if counter and call_name(node.iter) == "enumerate" and isinstance(node.target, ast.Tuple) \
                and node.target.elts and isinstance(node.target.elts[0], ast.Name):
            bound[node.target.elts[0].id] = INT
        if isinstance(node.target, ast.Name):
            vals = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if elts and len(vals) == len(elts):
                bound[node.target.id] = vals
        elif isinstance(node.target, ast.Tuple):
            for i, t in enumerate(node.target.elts):
                if not isinstance(t, ast.Name):
                    continue
                vals = [e.elts[i].value for e in elts
                        if isinstance(e, (ast.Tuple, ast.List)) and len(e.elts) > i
                        and isinstance(e.elts[i], ast.Constant) and isinstance(e.elts[i].value, str)]
                if elts and len(vals) == len(elts):
                    bound[t.id] = vals
        # for key in TABLE / for key, v in zip(TABLE, ...) / enumerate(TABLE):
        # the loop variable stands for each key of a module-level table.
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
        # the bindings hold in the body only
        self.visit(node.iter)
        self.loops.append(bound)
        for n in node.body:
            self.visit(n)
        self.loops.pop()
        for n in node.orelse:
            self.visit(n)

    def lookup_item(self, node):
        name = dotted(node)
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
        # x = get_item("KEY")  -> remember x (and self.x) for later .value writes;
        # b = BugBinding("KEY") -> remember b.item; y = x / y = b.item -> y too.
        ks, suffix = None, ""
        if isinstance(node.value, ast.Call):
            fn = call_name(node.value)
            idx = 0 if fn in READ_CALLS or fn in BINDINGS else self.getters.get(fn)
            if idx is not None and len(node.value.args) > idx:
                ks, _ = self.keys_of(node.value.args[idx])
                suffix = ".item" if fn in BINDINGS else ""
        elif isinstance(node.value, (ast.Name, ast.Attribute)):
            ks = self.lookup_item(node.value)
        if ks:
            for t in node.targets:
                n = dotted(t) if isinstance(t, (ast.Name, ast.Attribute)) else None
                if n and n.count(".") <= 1:
                    scope = self.items[0] if n.startswith("self.") else self.items[-1]
                    scope[n + suffix] = ks
        for t in node.targets:
            if isinstance(t, ast.Attribute) and t.attr == "value":
                self.item_write(node.lineno, self.lookup_item(t.value), "item.value =")
        self.generic_visit(node)

    def item_write(self, line, keys, how):
        """A write to an item: the keys the scan names, else the keys a
        `# writes:` comment on the line declares."""
        if keys:
            if line in self.declared:
                self.declared_used.setdefault(line, "named")
            for k in keys:
                self.refs.append((line, k, how))
        elif line in self.declared:
            self.declared_used[line] = "declared"
            for k in self.declared[line]:
                self.refs.append((line, k, how + " (# writes:)"))

    def visit_Call(self, node):
        fn = call_name(node)
        idx = self.wrappers.get(fn, 0)
        if (fn in WRITE_CALLS or fn in self.wrappers) and len(node.args) > idx:
            ks, how = self.keys_of(node.args[idx])
            if ks:
                for k in ks:
                    self.item_write(node.lineno, [k], "%s(%s)" % (fn, how))
            else:
                self.item_write(node.lineno, [], "%s()" % fn)
        elif fn in ITEM_WRITES and isinstance(node.func, ast.Attribute):
            self.item_write(node.lineno, self.lookup_item(node.func.value), "item.%s()" % fn)
        self.generic_visit(node)


def merge_literals(parts):
    out = []
    for p in parts:
        if isinstance(p, str) and out and isinstance(out[-1], str):
            out[-1] += p
        elif p != "":
            out.append(p)
    return out


def find_wrappers(tree, getters=None):
    """name -> positional index of the key argument, for module functions that
    hand a parameter to a write (`_setval(key, v)` -> db_write(key, v),
    `_set(key, v)` -> items[key].set(v), or `item = self._get(key)` from a
    getter, then `item.value = v` / `item.output_value()`).  Two passes, so a
    wrapper of a wrapper is found too."""
    getters = getters or {}
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
                    k = subscript_name(n.value) or getter_arg(n.value, getters)
                    if k in params:
                        alias[n.targets[0].id] = k
            for n in ast.walk(fn):
                key = None
                if isinstance(n, ast.Call):
                    name = call_name(n)
                    idx = wr.get(name, 0) if (name in WRITE_CALLS or name in wr) else None
                    if idx is not None and len(n.args) > idx and isinstance(n.args[idx], ast.Name):
                        key = n.args[idx].id
                    if name in ITEM_WRITES and isinstance(n.func, ast.Attribute):
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


def getter_arg(node, getters):
    """`self._get(key)` -> 'key', for a getter's key argument that is a bare name."""
    if isinstance(node, ast.Call) and call_name(node) in getters:
        idx = getters[call_name(node)]
        if len(node.args) > idx and isinstance(node.args[idx], ast.Name):
            return node.args[idx].id
    return None


def find_name_builders(tree):
    """name -> (params, expr), for functions whose whole body returns a key
    name built from their parameters (`_slot_key(n, name)`: `return
    f"FPL{n}{name}"`), so `_set(_slot_key(i, name), v)` resolves as the
    f-string would at the call."""
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = [n for n in fn.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            continue
        v = body[0].value
        built = isinstance(v, ast.JoinedStr) or \
            (isinstance(v, ast.BinOp) and isinstance(v.op, ast.Mod) and isinstance(v.left, ast.Constant)
             and isinstance(v.left.value, str)) or \
            (isinstance(v, ast.Call) and call_name(v) == "format" and isinstance(v.func, ast.Attribute)
             and isinstance(v.func.value, ast.Constant) and isinstance(v.func.value.value, str))
        if built:
            out[fn.name] = ([a.arg for a in fn.args.args if a.arg not in ("self", "cls")], v)
    return out


def module_literals(tree):
    """NAME -> rows, for module-level tuples and lists written out in the
    source (`SLOT_FIELDS = (("ID", "str", ""), ...)`), so `for name, _, _ in
    SLOT_FIELDS` binds name to the first column."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.elts
    return out


def writes_comments(text):
    """line -> (comment line, keys) for each `# writes:` comment: on the line
    of the code it follows, or alone on the line above the next code line."""
    out, pending = {}, []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                m = WRITES_COMMENT.match(tok.string)
                if m:
                    keys = [k for k in re.split(r"[,\s]+", m.group(1).strip()) if k]
                    if tok.line[:tok.start[1]].strip():
                        out[tok.start[0]] = (tok.start[0], keys)
                    else:
                        pending.append((tok.start[0], keys))
            elif pending and tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                                              tokenize.DEDENT, tokenize.COMMENT):
                line, keys = pending[-1][0], [k for _, ks in pending for k in ks]
                out[tok.start[0]] = (line, keys)
                pending = []
    except (tokenize.TokenError, IndentationError):
        pass
    for line, keys in pending:
        out[-line] = (line, keys)     # a comment with no code after it: on no write
    return out


def check_writes_comments(rel, comments, used, catalog):
    for line, (cline, keys) in sorted(comments.items(), key=lambda kv: kv[1][0]):
        where = "%s:%d: `# writes: %s`" % (rel, cline, ", ".join(keys))
        bad = [k for k in keys if k not in catalog]
        if not keys or bad:
            raise ScanError("%s: %s is not a key in the database" % (where, ", ".join(bad) or "(none)"))
        if used.get(line) == "named":
            raise ScanError("%s: the scan already names the key this line writes; remove the "
                            "comment" % where)
        if used.get(line) != "declared":
            raise ScanError("%s: the comment is on no write; put it on the line that writes the "
                            "item (`.output_value()`, `.value =`, `.set()`), or remove it" % where)


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


def dotted(node):
    """`a` or `a.b.c` for a name or a chain of attributes on one, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id] + parts[::-1])


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


# ---- key tables (SRC4) -----------------------------------------------------
# Some writers take their keys from a table and write in a loop
# (`for key in ...: db_write(key, v)`), so no write call names a key.  The
# scan reads the table instead: a module constant that declares the outputs,
# the X-Plane connection's `recv` rows and `datarefs` map, and the FlightGear
# protocol file's <output> chunk names.
OUTPUT_TABLES = {"SOURCE_KEYS", "ENGINE_KEYS", "OUTPUT_GROUPS"}


def table_writes(tree, catalog):
    """(line, key, how) for each key named in a module-level OUTPUT_TABLES
    constant."""
    out = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name) and t.id in OUTPUT_TABLES]
        for name in names:
            for n in ast.walk(node.value):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in catalog:
                    out.append((n.lineno, n.value, "table %s" % name))
    return out


# A writer that streams rows another module builds.  demo flies the
# fixgw.sim generator's rows through `live.stream(..., self._write)`, all but
# the REF* keys (`route_rows`), so the keys a row is given in these files
# are demo's writes: the route block (`fplroute.route_block_writes`) and
# FPLCMD (generator.py).  The sensors go through demo's SOURCE_KEYS table.
ROW_STREAMS = {"demo": ("src/fixgw/sim/generator.py", "src/fixgw/sim/fplroute.py")}
ROW_NAMES = {"row", "writes"}
ROW_SKIP = "REF"


class RowVisitor(KeyVisitor):
    """The keys a row is given: `row[KEY] = v`, and the `(KEY, v)` pairs
    put on a `writes` list."""

    def visit_Assign(self, node):
        for t in node.targets:
            if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id in ROW_NAMES:
                self.add(node.lineno, t.slice, "row[]")
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if isinstance(node.target, ast.Name) and node.target.id in ROW_NAMES:
            self.pairs(node.value, "+=")
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("append", "extend") and \
                isinstance(f.value, ast.Name) and f.value.id in ROW_NAMES and node.args:
            if f.attr == "append":
                self.pairs(ast.List(elts=[node.args[0]]), "append")
            else:
                self.pairs(node.args[0], "extend")
        self.generic_visit(node)

    def pairs(self, seq, how):
        for e in getattr(seq, "elts", []):
            if isinstance(e, ast.Tuple) and len(e.elts) == 2:
                self.add(e.lineno, e.elts[0], how)

    def add(self, line, node, how):
        ks, what = self.keys_of(node)
        for k in ks:
            if not k.startswith(ROW_SKIP):
                self.refs.append((line, k, "row stream %s(%s)" % (how, what)))


def row_stream_writes(root, catalog):
    """(module, rel, line, key, how) for each ROW_STREAMS row key."""
    out = []
    for mod, files in sorted(ROW_STREAMS.items()):
        for rel in files:
            path = os.path.join(root, rel)
            if not os.path.exists(path):
                continue
            tree = ast.parse(read(path))
            v = RowVisitor(catalog, module_consts(tree, catalog))
            v.visit(tree)
            out += [(mod, rel, line, key, how) for line, key, how in v.refs]
    return out


# A function that writes back a commit another client made.  After a gateway
# restart the flight plan engine republishes the route block the editor last
# committed, unchanged and with the same FPLSEQ, and ignores its own FPLSEQ
# while it does so: "mirroring state_persist restoring NAVSRC"
# (doc/flightplan_keys.md).  Its route-block writes are a relay, like
# state_persist's; any other key it writes (FPLLDSEQ) stays the module's own.
ROUTE_BLOCK = re.compile(r"FPL(?:\d+(?:ID|LAT|LON|TYPE|PT|CRS|DST|ALT|SPD|SGI|FLAGS|CTRLAT"
                         r"|CTRLON|TURN)|S\d+(?:KIND|ID|ENT|EXT|FIRST|LAST)"
                         r"|COUNT|NAME|SEGCNT|APRTYPE|DBCYC|SEQ)")
RELAY_SITES = {("src/fixgw/plugins/flightplan/__init__.py", "_republish_route"): ROUTE_BLOCK}


def relay_spans(tree, rel):
    """(first line, last line, key pattern) of each RELAY_SITES function in a file."""
    names = {fn: rx for (r, fn), rx in RELAY_SITES.items() if r == rel}
    return [(n.lineno, n.end_lineno, names[n.name]) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]


def xplane_map_writes(path, catalog):
    """The X-Plane connection's sim-to-FIX maps: the keys in each `recv`
    row, and the keys of `datarefs` (`dataref_writes` goes the other way)."""
    try:
        root = yaml.compose(read(path))
    except Exception:
        return []
    out = []

    def rec(node):
        if not isinstance(node, yaml.MappingNode):
            return
        for k, v in node.value:
            if k.value == "recv" and isinstance(v, yaml.MappingNode):
                for _idx, row in v.value:
                    for s in getattr(row, "value", []):
                        if isinstance(s, yaml.ScalarNode) and s.value in catalog:
                            out.append((s.start_mark.line + 1, s.value, "yaml:recv"))
            elif k.value == "datarefs" and isinstance(v, yaml.MappingNode):
                for key, _ref in v.value:
                    if key.value in catalog:
                        out.append((key.start_mark.line + 1, key.value, "yaml:datarefs"))
            else:
                rec(v)
    if root is not None:
        rec(root)
    return out


FG_OUTPUT_NAME = re.compile(r"<name>\s*([A-Za-z0-9_]+)\s*:")


def fgfs_protocol_writes(path, catalog):
    """A FlightGear generic protocol file: each <output> chunk's <name> is
    "FIX_KEY: description", and FlightGear's output is the plugin's input."""
    text = read(path)
    out = []
    for block in re.finditer(r"<output>(.*?)</output>", text, re.S):
        base = text.count("\n", 0, block.start(1)) + 1
        for m in FG_OUTPUT_NAME.finditer(block.group(1)):
            if m.group(1) in catalog:
                out.append((base + block.group(1).count("\n", 0, m.start()), m.group(1), "xml:output"))
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
    for path in walk(root, {".py", ".yaml", ".yml", ".xml"}):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        mod = module_of(repo, rel)
        cat = category(repo, mod, rel)
        if cat == "tool":
            continue
        spans = []
        if path.endswith(".py"):
            text = read(path)
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            getters = find_getters(tree)
            comments = writes_comments(text) if "writes:" in text else {}
            v = KeyVisitor(catalog, module_consts(tree, catalog), find_wrappers(tree, getters),
                           module_tables(tree, catalog), getters, find_name_builders(tree),
                           module_literals(tree), {ln: ks for ln, (_, ks) in comments.items()})
            v.visit(tree)
            check_writes_comments(rel, comments, v.declared_used, catalog)
            found = v.refs + (table_writes(tree, catalog) if repo == "fix-gateway" else [])
            spans = relay_spans(tree, rel) if repo == "fix-gateway" else []
        elif repo != "fix-gateway":
            continue
        elif path.endswith(".xml"):
            if mod != "fgfs":
                continue
            found = fgfs_protocol_writes(path, catalog)
        else:
            found = yaml_writes(path, catalog, rel)
            if mod == "xplane":
                found += xplane_map_writes(path, catalog)
        for line, key, how in found:
            relay = any(a <= line <= b and rx.fullmatch(key) for a, b, rx in spans)
            writes.append(dict(key=key, module=mod, category="relay" if relay else cat,
                               file=rel, line=line, how=how))
    if repo == "fix-gateway":
        for mod, rel, line, key, how in row_stream_writes(root, catalog):
            writes.append(dict(key=key, module=mod, category=category(repo, mod),
                               file=rel, line=line, how=how))
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
    `old`; the pyEfis writers are `old`'s, plus any `pyefis_writes` adds.
    They are never dropped here: a pyEfis scan may miss a write the register
    declares by hand, and ARCHG2 reports a stale one from pyEfis's side."""
    found = writers_by_key(writes)
    pyefis = {k: {"pyefis": set(v["pyefis"])} for k, v in old.items() if v.get("pyefis")}
    for k, v in (writers_by_key(pyefis_writes) if pyefis_writes is not None else {}).items():
        if v.get("pyefis"):
            pyefis.setdefault(k, {"pyefis": set()})["pyefis"] |= set(v["pyefis"])
    pyefis = {k: {"pyefis": sorted(v["pyefis"])} for k, v in pyefis.items()}
    reg = {}
    unwritten = {k for k, v in old.items() if (v or {}).get("no_writer")}
    for key in sorted(set(found) | set(pyefis) | unwritten):
        if key not in catalog:
            continue
        e = {"units": catalog[key].get("units") or ""}
        if (old.get(key) or {}).get("datum") and numeric(key, catalog):
            e["datum"] = old[key]["datum"]
        elif needs_datum(key, catalog):
            e["datum"] = "unrecorded"
        for col in COLUMNS[1:]:
            if (old.get(key) or {}).get(col) is not None:
                e[col] = old[key][col]
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


def numeric(key, catalog):
    """A datum describes a number: a text or flag key never has one (an
    altitude constraint string, a path terminator)."""
    return catalog.get(key, {}).get("type") in ("float", "int")


def parse_cell(cell):
    """'msl: NMEA GGA altitude' -> (['msl'], 'NMEA GGA altitude')."""
    head, _, note = str(cell).partition(": ")
    return [t.strip() for t in head.split(",")], note


def declared_writers(e):
    """Every module the register says writes the key, but the relays."""
    return live_writers(e) | set(e.get("sim") or [])


def needs_provider_datum(key, catalog):
    return needs_datum(key, catalog) or key in DIRECTION_KEYS or key in STATISTIC_KEYS


def datum_classes(e):
    """writer -> datum token, from a per-writer datum mapping."""
    d = e.get("datum")
    if not isinstance(d, dict):
        return {}
    return {m: parse_cell(v)[0][0] for m, v in sorted(d.items())}


def disagrees(classes):
    toks = list(classes.values())
    return len(toks) > 1 and (len(set(toks)) > 1 or bool(set(toks) & UNKNOWN_DATUMS))


def build_datum_baseline(register):
    return {k: datum_classes(e) for k, e in sorted(register.items()) if disagrees(datum_classes(e))}


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
        "#   datum     altitude, position, heading, track and accuracy keys: the datum",
        "#             (or, for accuracy, the statistic) each writer puts on the bus.",
        "#             A mapping is per writer, and where they differ the key is wrong",
        "#             today (tests/arch/datum_baseline.yaml); `unrecorded` means",
        "#             nobody has read the code yet.",
        "#   quality   per writer (SRC4): the flags it sets on the key, `passthrough`,",
        "#             the FIX keys it writes that qualify this one, or `none`",
        "#   on_loss   per writer: silence, fail, holds_last, writes_zero, on_change",
        "#             or follows -- what the key does when the writer loses its input",
        "#   rate_hz   per writer, where the code or its doc states it",
        "#   writers   live writers in fix-gateway: plugins and compute modules",
        "#   pyefis    live writers in pyEfis (ARCHG2 checks these from pyEfis's side)",
        "#   sim       simulators and replays, which stand in for a live writer",
        "#   relay     modules that pass a client's write on (netfix, command, ...)",
        "#   selector  a compute `select`: `by` picks one of `sources`, and the",
        "#             selector is the key's only live writer",
        "#   no_writer a key declared for a writer nothing shipped provides yet, and",
        "#             why (a panel button's key that no shipped config wires up)",
        "#",
        "# A cell is \"<token>[, <token>]: note\".  Every writer of an ARCH2 section",
        "# 3.2 source key fills quality and on_loss, and datum where it applies.",
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


def dump_datum_baseline(base):
    lines = [
        "# Keys whose writers put more than one datum on the bus today (SRC4,",
        "# AER-6455): each writer and the datum it declares in",
        "# src/fixgw/database/writers.yaml.  `unrecorded` and `unstated` never",
        "# agree, so a key with one of them and a second writer is here too.",
        "# tests/arch/test_key_writers.py fails when a key not listed here",
        "# declares two datums, and when a listed key's writers or datums change",
        "# -- so this file only shrinks.  Remove an entry by giving each datum its",
        "# own key (GALT1) or by fixing the writer (MAVALT1).",
        "# %d keys." % len(base),
        "",
    ]
    for key, classes in base.items():
        lines.append("%s: %s" % (key, flow(classes)))
    return "\n".join(lines) + "\n"


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
        if e.get("datum") and not numeric(key, catalog):
            fails.append("%s: writers.yaml gives a datum for a %s key; a datum describes an "
                         "altitude or position number, so remove it"
                         % (key, catalog[key].get("type")))
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


def cell_fails(key, m, col, cell, register, catalog):
    where = "%s: writers.yaml `%s` for %s" % (key, col, m)
    if col == "rate_hz":
        if isinstance(cell, bool) or not isinstance(cell, (int, float)) or cell <= 0:
            return ["%s is %r; give a rate in Hz, or leave the writer out when it is not known"
                    % (where, cell)]
        return []
    if not isinstance(cell, str) or not cell.strip():
        return ["%s is %r; write \"<token>: note\" (doc/key_writers.md)" % (where, cell)]
    toks, _note = parse_cell(cell)
    fails = []
    if col == "datum":
        if len(toks) != 1 or toks[0] not in DATUMS:
            fails.append("%s is %r; a datum is one of %s, then \": note\""
                         % (where, ", ".join(toks), ", ".join(sorted(DATUMS))))
    elif col == "on_loss":
        bad = [t for t in toks if t not in ON_LOSS]
        if bad:
            fails.append("%s names %s; on_loss is one or more of %s"
                         % (where, ", ".join(bad), ", ".join(sorted(ON_LOSS))))
    elif col == "quality":
        if "none" in toks and len(toks) > 1:
            fails.append("%s says `none` and also %s" % (where, ", ".join(t for t in toks if t != "none")))
        for t in toks:
            if t in QUALITY_FLAGS:
                continue
            if t not in catalog:
                fails.append("%s names %s, which is neither a flag (%s) nor a FIX key"
                             % (where, t, ", ".join(sorted(QUALITY_FLAGS))))
            elif m not in declared_writers(register.get(t) or {}):
                fails.append("%s names %s, which %s does not write" % (where, t, m))
    return fails


def check_columns(catalog, register, provider_keys=PROVIDER_KEYS):
    """Failures in the provider declaration: a provider key whose writers do
    not each declare quality, on_loss and (where it applies) a datum; and a
    cell that names a module that does not write the key, or a token outside
    its column's vocabulary."""
    fails = []
    for key in provider_keys:
        e = register.get(key)
        if e is None:
            fails.append("%s: an ARCH2 section 3.2 source key with no entry in writers.yaml" % key)
            continue
        need = ["quality", "on_loss"] + (["datum"] if needs_provider_datum(key, catalog) else [])
        for col in need:
            got = e.get(col)
            if not isinstance(got, dict):
                fails.append("%s: a source key needs a `%s` mapping, one cell per writer, in "
                             "writers.yaml (doc/key_writers.md)" % (key, col))
                continue
            for m in sorted(declared_writers(e) - set(got)):
                fails.append("%s: %s writes it and declares no `%s` in writers.yaml" % (key, m, col))
    for key, e in sorted(register.items()):
        writers = declared_writers(e)
        for col in COLUMNS:
            got = e.get(col)
            if got is None or (col == "datum" and not isinstance(got, dict)):
                continue   # a scalar datum is a note on the key, as before SRC4
            if not isinstance(got, dict):
                fails.append("%s: writers.yaml `%s` must map each writer to its cell" % (key, col))
                continue
            for m, cell in sorted(got.items()):
                if col != "datum" and m not in writers:   # check_register covers datum
                    fails.append("%s: writers.yaml gives `%s` for %s, which is not one of its "
                                 "writers" % (key, col, m))
                fails += cell_fails(key, m, col, cell, register, catalog)
    return fails


def check_datum_baseline(register, base):
    """Failures: a key whose writers declare two datums and is not on the
    datum baseline; a listed key whose writers or datums changed; a listed key
    that now has one datum."""
    fails = []
    for key, e in sorted(register.items()):
        classes = datum_classes(e)
        if not disagrees(classes):
            continue
        said = ", ".join("%s %s" % (m, t) for m, t in classes.items())
        if key not in base:
            fails.append("%s: its writers declare more than one datum (%s), and it is not on "
                         "tests/arch/datum_baseline.yaml.  A key has one datum: give the other "
                         "datum its own key, as GALT1 does, or fix the writer, as MAVALT1 did" % (key, said))
            continue
        allowed = base[key] or {}
        for m, t in classes.items():
            if m not in allowed:
                fails.append("%s: %s declares `%s` and the datum baseline does not list it; a "
                             "new writer takes the key's one datum" % (key, m, t))
            elif allowed[m] != t:
                fails.append("%s: %s now declares `%s`, and the datum baseline has `%s`"
                             % (key, m, t, allowed[m]))
    for key in sorted(base):
        classes = datum_classes(register.get(key) or {})
        if not disagrees(classes):
            fails.append("%s: its writers now agree on one datum and it is still on the datum "
                         "baseline; remove it from tests/arch/datum_baseline.yaml" % key)
            continue
        gone = sorted(set(base[key] or {}) - set(classes))
        if gone:
            fails.append("%s: the datum baseline lists %s, which no longer declares a datum; "
                         "remove it from the entry" % (key, ", ".join(gone)))
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
            check_concepts(root, load_yaml(root, CONCEPTS)),
            check_columns(catalog, register),
            check_datum_baseline(register, load_yaml(root, DATUM_BASELINE)))


def generate(root, pyefis_root=None):
    catalog = load_catalog(root)
    writes = scan_writes(root, catalog)
    pw = scan_writes(pyefis_root, catalog, repo="pyEfis") if pyefis_root else None
    reg = build_register(catalog, writes, scan_selectors(root), load_yaml(root, REGISTER), pw)
    base = build_baseline(reg)
    for rel, text in ((REGISTER, dump_register(reg)), (BASELINE, dump_baseline(base)),
                      (DATUM_BASELINE, dump_datum_baseline(build_datum_baseline(reg)))):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)   # never overwrite in place: the NAS kept stale tail bytes
        with open(path, "w") as f:
            f.write(text)
    print("wrote %s (%d keys), %s (%d multi-writer keys) and %s"
          % (REGISTER, len(reg), BASELINE, len(base), DATUM_BASELINE))


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
