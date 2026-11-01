#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Evaluate a profile's checks: against a recorded bus log (SYN1).
#
"""Reads a bus log -- ``data_recorder`` JSON-lines files, or rows held in
memory -- and evaluates the profile's ``checks:`` on it. Tier-independent:
it compares two keys on the log and knows nothing about who wrote either.

A recorder row holds only the keys that changed in that interval, so the
log is forward-filled: each row sees the last value written to every key.
Row ``n`` of a file is at ``n * frequency`` seconds. A value written as
``[value, annunciate, old, bad, fail, secfail]`` also carries its flags:
the log keeps, per row, whether each key was last written bad or failed.
And since a row holds exactly the keys written in its interval, the log
also keeps when each key was last written, which is how a check sees an
input go silent. (The first row of a recorder file is a snapshot of every
key, not a write, and does not count as one.)
"""

import json
from dataclasses import dataclass, field

DEFAULT_SEQUENCE_KEYS = ("WPNAME", "REFWPT")

# An input counts as live while it has been written within the last second:
# 1 Hz is the slowest position output AC 20-138D contemplates for an IFR
# navigation source (15-7.3: "If the equipment provides a 1 Hz output").
LIVE_S = 1.0


def _val(v):
    return v[0] if isinstance(v, list) else v


def _flagged(v):
    # [value, annunciate, old, bad, fail, secfail]: bad or failed
    return isinstance(v, list) and len(v) >= 5 and bool(v[3] or v[4])


class Log:
    def __init__(self, keys):
        self.keys = set(keys)
        self.t = []
        self.rows = []
        self.flags = []     # per row: {key: True} for each key last written bad or failed
        self.written = []   # per row: {key: t of its last write}

    def _add(self, t, cur, flags, written):
        self.t.append(t)
        self.rows.append(dict(cur))
        self.flags.append({k for k, f in flags.items() if f})
        self.written.append(dict(written))

    def silent_s(self, i, key):
        """Seconds since ``key`` was last written, as of row ``i`` (inf if never)."""
        w = self.written[i].get(key)
        return float("inf") if w is None else round(self.t[i] - w, 6)

    @classmethod
    def from_rows(cls, rows, keys):
        log = cls(keys)
        cur, flags, written = {}, {}, {}
        for t, row in rows:
            for k, v in row.items():
                if k in log.keys:
                    cur[k] = _val(v)
                    flags[k] = _flagged(v)
                    written[k] = t
            log._add(t, cur, flags, written)
        return log

    @classmethod
    def from_recorder_files(cls, paths, keys):
        log = cls(keys)
        cur, flags, written = {}, {}, {}
        t = 0.0
        freq = 0.5
        first = True
        snapshot = False
        for path in paths:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    j = json.loads(line)
                    if "frequency" in j and "starttime" in j:
                        freq = int(j["frequency"]) / 1000.0
                        snapshot = True
                        continue
                    if not first:
                        t += freq
                    first = False
                    for k, v in j.items():
                        if k in log.keys:
                            cur[k] = _val(v)
                            flags[k] = _flagged(v)
                            if not snapshot:
                                written[k] = round(t, 3)
                    snapshot = False
                    log._add(round(t, 3), cur, flags, written)
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
    flagged: int = 0             # rows skipped because the engine key was flagged (unless_flagged)
    transients: int = 0          # failing runs no longer than tolerate_s, not counted as failures
    silent: int = 0              # rows skipped because an input had gone silent (skip_when_silent)


def _fmt(v):
    return f"{v:.4f}" if isinstance(v, float) else repr(v)


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


def _leg_labels(log, leg_keys=("REFWPT", "REFPT")):
    # The reference's TO fix and leg type: a hold at the fix the leg before
    # it flew to is its own leg ("WINCH HM").
    ident_key, pt_key = leg_keys
    labels = []
    prev, cur = "", None
    for row in log.rows:
        w = (row.get(ident_key), row.get(pt_key) if pt_key else None)
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
        if "flagged" in c:
            keys.update(c["flagged"])
            keys.add("GPS_FIX_TYPE")
            keys.update(c.get("while_silent") or ())
        keys.update((c.get("skip_when") or {}).keys())
        keys.update((c.get("skip_when_silent") or {}).keys())
        keys.update(c.get("skip_when_differ") or ())
    return keys


def evaluate(checks, log, start_s=1.0, leg_keys=("REFWPT", "REFPT")):
    """``leg_keys``: the (TO fix, leg type) keys that label each row's leg
    in the per-leg report; the leg type key may be None."""
    seq_times = _change_times(log, _sequence_keys(checks))
    legs = _leg_labels(log, leg_keys)
    out = []
    for c in checks:
        around = c.get("around_keys")
        out.append(_eval_one(c, log, _change_times(log, around) if around else seq_times, legs, start_s))
    return out


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
    if "flagged" in c and "while_silent" in c:
        return _eval_flagged_silent(c, log, start_s)
    if "flagged" in c:
        return _eval_flagged(c, log, start_s)
    a_key, b_key = c["key_pair"]
    tol = c.get("tol")
    tol_deg = c.get("tol_deg")
    tol_rel = float(c.get("tol_rel", 0.0))
    same_sign = bool(c.get("same_sign", False))
    window = float(c.get("except_s_around_sequence", 0.0))
    skip = c.get("skip_when") or {}
    skip_silent = c.get("skip_when_silent") or {}
    differ = c.get("skip_when_differ") or ()
    after = float(c.get("after_s", start_s))
    unless_flagged = bool(c.get("unless_flagged", False))
    tolerate = float(c.get("tolerate_s", 0.0))
    label = f"{a_key} vs {b_key}"
    if tol_deg is not None:
        label += f" (+-{tol_deg} deg)"
    elif tol is not None:
        label += f" (+-{tol}" + (f" + {tol_rel:.1%}" if tol_rel else "") + ")"
    else:
        label += " (equal)"
    res = CheckResult(label, True)
    bad_rows = []      # (index among compared rows, t, a, b, leg)
    errs = []          # per compared row: (t, error or None, leg)
    for i, (t, row, leg, flags) in enumerate(zip(log.t, log.rows, legs, log.flags)):
        if t < after or _in_window(t, seq_times, window):
            continue
        if any(row.get(k) == v for k, v in skip.items()):
            continue
        if differ and len({str(row.get(k)) for k in differ}) > 1:
            continue
        if any(log.silent_s(i, k) > float(s) for k, s in skip_silent.items()):
            res.silent += 1
            continue
        if a_key not in row or b_key not in row:
            continue
        if unless_flagged and a_key in flags:
            # annunciated: a flagged value is not presented as guidance
            res.flagged += 1
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
        n, f, m = res.by_leg.get(leg, (0, 0, None))
        errs.append((t, err, leg))
        if bad:
            bad_rows.append((res.compared, t, a, b, leg))
        res.compared += 1
        res.by_leg[leg] = (n + 1, f, m)
    tolerated = set()
    for run in _runs(bad_rows):
        if tolerate > 0 and run[-1][1] - run[0][1] < tolerate:
            res.transients += 1
            tolerated.update(r[0] for r in run)
            continue
        for _, t, a, b, leg in run:
            res.failed += 1
            n, f, m = res.by_leg[leg]
            res.by_leg[leg] = (n, f + 1, m)
            if res.first_fail_t is None or t < res.first_fail_t:
                res.first_fail_t = t
                res.detail = f"t={t:.1f}s {a_key}={_fmt(a)} {b_key}={_fmt(b)}"
    # The largest error is over the rows that count: a tolerated transient
    # (two navigators switching a phase one update apart) is reported apart
    # from it, so a 150 deg one-row switch does not read as the finding.
    worst_transient = None
    for k, (t, err, leg) in enumerate(errs):
        if err is None:
            continue
        if k in tolerated:
            if worst_transient is None or err > worst_transient[0]:
                worst_transient = (err, t)
            continue
        if res.max_err is None or err > res.max_err:
            res.max_err, res.t_max = err, t
        n, f, m = res.by_leg[leg]
        if m is None or err > m:
            res.by_leg[leg] = (n, f, err)
    if unless_flagged:
        label += ", unless flagged"
    if tolerate:
        label += f", runs < {tolerate:g} s tolerated"
    res.label = label
    res.passed = res.failed == 0 and res.compared > 0
    if res.compared == 0:
        res.detail = "nothing compared"
    extra = []
    if res.flagged:
        extra.append(f"{res.flagged} rows flagged")
    if res.transients:
        extra.append(f"{res.transients} transient runs"
                     + (f" (largest {worst_transient[0]:.4f} at t={worst_transient[1]:.1f}s)" if worst_transient else ""))
    if res.silent:
        extra.append(f"{res.silent} rows with an input silent")
    if extra:
        res.detail = "; ".join(([res.detail] if res.detail else []) + extra)
    return res


def _runs(bad_rows):
    """Group failing rows into runs of consecutive compared rows."""
    runs = []
    for r in bad_rows:
        if runs and r[0] == runs[-1][-1][0] + 1:
            runs[-1].append(r)
        else:
            runs.append([r])
    return runs


def _eval_flagged(c, log, start_s):
    """Each key in ``flagged`` must be flagged (bad or failed) on every row
    where the GPS has had no fix (``GPS_FIX_TYPE`` < 3) for at least
    ``within_s``, and must not be flagged on any row where it has had a fix
    for at least ``clear_within_s``. Rows in between are not judged."""
    keys = list(c["flagged"])
    within = float(c.get("within_s", 0.0))
    clear = float(c.get("clear_within_s", 0.0))
    after = float(c.get("after_s", start_s))
    res = CheckResult(f"{', '.join(keys)} flagged without a GPS fix (within {within:g} s, "
                      f"clear within {clear:g} s)", True)
    since, state = None, None
    lost = []
    misses = []
    for t, row, flags in zip(log.t, log.rows, log.flags):
        fix = row.get("GPS_FIX_TYPE")
        no_fix = fix is not None and fix < 3
        if no_fix != state:
            state, since = no_fix, t
            if no_fix:
                lost.append(t)
        if t < after or fix is None:
            continue
        held = t - since
        if no_fix and held >= within:
            want = True
        elif not no_fix and held >= clear:
            want = False
        else:
            continue
        for k in keys:
            if k not in row:
                continue
            res.compared += 1
            if (k in flags) != want:
                res.failed += 1
                misses.append((t, k, want))
    res.passed = res.failed == 0 and res.compared > 0 and bool(lost)
    if not lost:
        res.detail = "no loss of fix on this log"
    elif misses:
        t, k, want = misses[0]
        res.first_fail_t = t
        res.detail = f"t={t:.1f}s {k} {'not flagged' if want else 'still flagged'}"
    else:
        res.detail = "losses of fix at " + ", ".join(f"{t:.1f}s" for t in lost)
    return res


def _eval_flagged_silent(c, log, start_s):
    """Each key in ``flagged`` must be flagged (bad or failed) on every row
    where one of the ``while_silent`` inputs has not been written for at
    least ``within_s``, and must not be flagged on any row where all of them
    have been live (written within ``LIVE_S``) for at least
    ``clear_within_s``. Rows in between are not judged. The value an input
    last had does not matter: a dead link leaves a good-looking last value
    behind, and that is the case this check is for."""
    keys = list(c["flagged"])
    inputs = list(c["while_silent"])
    within = float(c.get("within_s", 0.0))
    clear = float(c.get("clear_within_s", 0.0))
    after = float(c.get("after_s", start_s))
    res = CheckResult(f"{', '.join(keys)} flagged when {', '.join(inputs)} silent "
                      f"(within {within:g} s, clear within {clear:g} s)", True)
    live_since, silences, in_silence = None, [], False
    misses = []
    for i, (t, row, flags) in enumerate(zip(log.t, log.rows, log.flags)):
        age = max(log.silent_s(i, k) for k in inputs)
        if age < LIVE_S:
            live_since = t if live_since is None else live_since
        else:
            live_since = None
        if age >= within and age != float("inf"):
            if not in_silence:
                silences.append(t - age)
            in_silence = True
        elif age < LIVE_S:
            in_silence = False
        if t < after:
            continue
        if age >= within and age != float("inf"):
            want = True
        elif live_since is not None and t - live_since >= clear:
            want = False
        else:
            continue
        for k in keys:
            if k not in row:
                continue
            res.compared += 1
            if (k in flags) != want:
                res.failed += 1
                misses.append((t, k, want, age))
    res.passed = res.failed == 0 and res.compared > 0 and bool(silences)
    if not silences:
        res.detail = f"no silence of {within:g} s on this log"
    elif misses:
        t, k, want, age = misses[0]
        res.first_fail_t = t
        res.detail = (f"t={t:.1f}s {k} not flagged, inputs silent {age:.1f} s" if want
                      else f"t={t:.1f}s {k} still flagged")
        if want:
            res.detail += f"; unflagged at up to {max(m[3] for m in misses if m[2]):.1f} s of silence"
    else:
        res.detail = "silences from " + ", ".join(f"{t:.1f}s" for t in silences)
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
