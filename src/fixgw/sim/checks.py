#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Evaluate a profile's checks: against a recorded bus log (SYN1).
#
"""Reads a bus log -- ``data_recorder`` JSON-lines files, or rows held in
memory -- and evaluates the profile's ``checks:`` on it. Tier-independent:
it compares two keys on the log and knows nothing about who wrote either.

A recorder row holds only the keys that changed in that interval, so the
log is forward-filled: each row sees the last value written to every key.
Row ``n`` of a file is at ``n * frequency`` seconds.
"""

import json
from dataclasses import dataclass, field

DEFAULT_SEQUENCE_KEYS = ("WPNAME", "REFWPT")


def _val(v):
    return v[0] if isinstance(v, list) else v


class Log:
    def __init__(self, keys):
        self.keys = set(keys)
        self.t = []
        self.rows = []

    @classmethod
    def from_rows(cls, rows, keys):
        log = cls(keys)
        cur = {}
        for t, row in rows:
            for k, v in row.items():
                if k in log.keys:
                    cur[k] = _val(v)
            log.t.append(t)
            log.rows.append(dict(cur))
        return log

    @classmethod
    def from_recorder_files(cls, paths, keys):
        log = cls(keys)
        cur = {}
        t = 0.0
        freq = 0.5
        first = True
        for path in paths:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    j = json.loads(line)
                    if "frequency" in j and "starttime" in j:
                        freq = int(j["frequency"]) / 1000.0
                        continue
                    for k, v in j.items():
                        if k in log.keys:
                            cur[k] = _val(v)
                    if not first:
                        t += freq
                    first = False
                    log.t.append(round(t, 3))
                    log.rows.append(dict(cur))
        return log


@dataclass
class CheckResult:
    label: str
    passed: bool
    compared: int = 0
    failed: int = 0
    max_err: float = None
    t_max: float = None
    first_fail_t: float = None
    detail: str = ""
    by_leg: dict = field(default_factory=dict)   # leg label -> (compared, failed, max_err)


def _wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def _change_times(log, keys):
    out = []
    for k in keys:
        prev = None
        for t, row in zip(log.t, log.rows):
            v = row.get(k)
            if prev is not None and v != prev:
                out.append(t)
            prev = v
    return sorted(out)


def _leg_labels(log):
    # The reference's TO fix and leg type: a hold at the fix the leg before
    # it flew to is its own leg ("WINCH HM").
    labels = []
    prev, cur = "", None
    for row in log.rows:
        w = (row.get("REFWPT"), row.get("REFPT"))
        if w != cur:
            prev, cur = (cur[0] if cur else ""), w
        ident, pt = cur
        labels.append(f"{prev or '-'} > {ident or '(no fix)'} {pt or ''}".rstrip())
    return labels


def _sequence_keys(checks):
    keys = set(DEFAULT_SEQUENCE_KEYS)
    for c in checks:
        if "sequence" in c:
            keys.update(c.get("keys", DEFAULT_SEQUENCE_KEYS))
    return sorted(keys)


def keys_needed(checks):
    keys = set(_sequence_keys(checks)) | {"REFWPT", "REFPT"}
    for c in checks:
        if "key_pair" in c:
            keys.update(c["key_pair"])
        keys.update((c.get("skip_when") or {}).keys())
    return keys


def evaluate(checks, log, start_s=1.0):
    seq_times = _change_times(log, _sequence_keys(checks))
    legs = _leg_labels(log)
    return [_eval_one(c, log, seq_times, legs, start_s) for c in checks]


def _in_window(t, times, s):
    if s <= 0:
        return False
    # times is sorted; linear scan is fine at these sizes
    for c in times:
        if c - s <= t <= c + s:
            return True
        if c - s > t:
            return False
    return False


def _eval_one(c, log, seq_times, legs, start_s):
    if "sequence" in c:
        return _eval_sequence(c, log)
    a_key, b_key = c["key_pair"]
    tol = c.get("tol")
    tol_deg = c.get("tol_deg")
    tol_rel = float(c.get("tol_rel", 0.0))
    same_sign = bool(c.get("same_sign", False))
    window = float(c.get("except_s_around_sequence", 0.0))
    skip = c.get("skip_when") or {}
    after = float(c.get("after_s", start_s))
    label = f"{a_key} vs {b_key}"
    if tol_deg is not None:
        label += f" (+-{tol_deg} deg)"
    elif tol is not None:
        label += f" (+-{tol}" + (f" + {tol_rel:.1%}" if tol_rel else "") + ")"
    else:
        label += " (equal)"
    res = CheckResult(label, True)
    for t, row, leg in zip(log.t, log.rows, legs):
        if t < after or _in_window(t, seq_times, window):
            continue
        if any(row.get(k) == v for k, v in skip.items()):
            continue
        if a_key not in row or b_key not in row:
            continue
        a, b = row[a_key], row[b_key]
        bad = False
        err = None
        if tol_deg is not None:
            err = abs(_wrap180(float(a) - float(b)))
            bad = err > tol_deg
        elif tol is not None:
            err = abs(float(a) - float(b))
            bad = err > float(tol) + tol_rel * abs(float(b))
            if same_sign and abs(float(a)) > tol and abs(float(b)) > tol and (float(a) > 0) != (float(b) > 0):
                bad = True
        else:
            bad = str(a) != str(b)
        res.compared += 1
        n, f, m = res.by_leg.get(leg, (0, 0, None))
        if err is not None and (res.max_err is None or err > res.max_err):
            res.max_err, res.t_max = err, t
        if err is not None and (m is None or err > m):
            m = err
        if bad:
            res.failed += 1
            f += 1
            if res.first_fail_t is None:
                res.first_fail_t = t
                res.detail = f"t={t:.1f}s {a_key}={a!r} {b_key}={b!r}"
        res.by_leg[leg] = (n + 1, f, m)
    res.passed = res.failed == 0 and res.compared > 0
    if res.compared == 0:
        res.detail = "nothing compared"
    return res


def _eval_sequence(c, log):
    expected = list(c["sequence"])
    keys = c.get("keys", list(DEFAULT_SEQUENCE_KEYS))
    res = CheckResult(f"sequence {' '.join(expected)} on {', '.join(keys)}", True)
    parts = []
    firsts = {}
    for k in keys:
        seen = []
        times = []
        for t, row in zip(log.t, log.rows):
            v = row.get(k)
            if v and (not seen or seen[-1] != v):
                seen.append(v)
                times.append(t)
        firsts[k] = dict(zip(seen, times))
        ok = seen == expected
        res.compared += 1
        if not ok:
            res.failed += 1
            res.passed = False
        parts.append(f"{k}: {' '.join(seen)}" + ("" if ok else "  (MISMATCH)"))
    if len(keys) == 2:
        a, b = keys
        diffs = [f"{x} {firsts[a][x] - firsts[b][x]:+.1f}s" for x in expected
                 if x in firsts[a] and x in firsts[b]]
        parts.append(f"{a} minus {b}, time each became the TO fix: " + ", ".join(diffs))
    res.detail = "; ".join(parts)
    return res


def markdown(profile_name, results, source):
    lines = [f"**{profile_name}** -- checks on {source}", "",
             "| Check | Result | Compared | Failed | Max error | at t (s) | First failure |",
             "|---|---|---:|---:|---:|---:|---|"]
    for r in results:
        me = "" if r.max_err is None else f"{r.max_err:.4f}"
        tm = "" if r.t_max is None else f"{r.t_max:.1f}"
        lines.append(f"| {r.label} | {'PASS' if r.passed else 'FAIL'} | {r.compared} | {r.failed} | {me} | {tm} | "
                     f"{r.detail if not r.label.startswith('sequence') else ''} |")
    seqs = [r for r in results if r.label.startswith("sequence")]
    for r in seqs:
        lines += ["", f"Sequence: {r.detail}"]
    pairs = [r for r in results if r.by_leg]
    if pairs:
        legs = []
        for r in pairs:
            for leg in r.by_leg:
                if leg not in legs:
                    legs.append(leg)
        lines += ["", "Max error per leg (failed / compared):", "",
                  "| Leg | " + " | ".join(r.label.split(" (")[0] for r in pairs) + " |",
                  "|---|" + "---:|" * len(pairs)]
        for leg in legs:
            cells = []
            for r in pairs:
                n, f, m = r.by_leg.get(leg, (0, 0, None))
                cells.append("" if n == 0 else (f"{m:.3f}" if m is not None else "") + f" ({f}/{n})")
            lines.append(f"| {leg} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
