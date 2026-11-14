"""Plots for the FP8 X-Plane comparison (the recorded flight of 2026-10-29).

Writes two PNGs beside this file:

- ``fp8_engine_vs_xplane.png``: the engine's course and CDI minus X-Plane's
  navigator, row by row, along X-Plane's own trajectory, with the gate's
  tolerances and the spans the gate skips (the same skips as
  ``tests/sim/fixtures/fp8_xplane_2026-10-29.yaml``).
- ``fp8_sequence_points.png``: how far before PAMJI, CIMSO and ANIBY each
  navigator switched to the next waypoint. X-Plane and the engine from the
  recorded flight; the synthetic reference navigator from its own flight of
  the same profile.

Run from the repo root:  PYTHONPATH=src:. python3 doc/evidence/fp8-xplane/plot_fp8_xplane.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from fixgw.sim import generator  # noqa: E402
from fixgw.sim.profile import load_profile  # noqa: E402
from tests.sim import xplane_replay as xr  # noqa: E402

HERE = Path(__file__).resolve().parent
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
SKIP = "#ecebe7"
ENGINE = "#2a78d6"
XPLANE = "#eb6834"
REF = "#1baf7a"
FIXES = ("PAMJI", "CIMSO", "ANIBY")


def ext_cdi(r):
    """EXTCDI as the bench writes it: gps_hdef_dot x 0.4, held to +-1."""
    return max(-1.0, min(1.0, 0.4 * r["xp_hdef_dot"]))


def switch_points(rows, key, fixes, pos):
    """-> {fix: nm before the fix} at the first row whose ``key`` moves off it."""
    out = {}
    for a, b in zip(rows, rows[1:]):
        if a[key] in fixes and b[key] != a[key] and a[key] not in out:
            lat, lon = fixes[a[key]]
            out[a[key]] = xr.nm(pos(b)[0], pos(b)[1], lat, lon)
    return out


def main():
    samples, events = xr.fly()
    wps, _, _, _ = xr.build_route()
    fixes = {w.id: (w.lat, w.lon) for w in wps if w.id in FIXES}

    seq_t = [ev[0] for ev in events if ev[1] == "sequence"]
    seq_t += [b["t"] for a, b in zip(samples, samples[1:]) if a["xp_wpt"] != b["xp_wpt"]]

    def skipped(r):
        return (r["state"] == 3 or r["phase"] == "HOLD" or r["xp_fromto"] == 2
                or any(abs(r["t"] - t) <= 5 for t in seq_t))

    t = [r["t"] for r in samples]
    dcrs = [((r["crs"] - r["xp_crs"] + 180) % 360) - 180 for r in samples]
    dcdi = [r["cdi"] - ext_cdi(r) for r in samples]
    keep = [not skipped(r) for r in samples]

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "font.family": "DejaVu Sans"})
    fig, axes = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True, facecolor=SURFACE)
    panels = ((axes[0], dcrs, 2.0, "Course, engine minus X-Plane (deg)"),
              (axes[1], dcdi, 0.1, "CDI, engine minus X-Plane (full scale)"))
    for ax, d, tol, label in panels:
        ax.set_facecolor(SURFACE)
        # shade the spans the gate skips
        start = None
        for i, k in enumerate(keep + [True]):
            if not k and start is None:
                start = t[i]
            elif k and start is not None:
                ax.axvspan(start, t[i - 1], color=SKIP, lw=0)
                start = None
        ax.plot([ti for ti, k in zip(t, keep) if k], [di if k else None for di, k in zip(d, keep) if k],
                ".", ms=1.6, color=ENGINE)
        for s in (tol, -tol):
            ax.axhline(s, color=INK2, lw=1, ls=(0, (4, 3)))
        ax.text(t[0] + 4, tol * 0.82, f"gate tolerance +/-{tol:g}", color=INK2, fontsize=9, va="top")
        ax.set_ylim(-tol * 1.25, tol * 1.25)
        ax.set_ylabel(label, fontsize=9)
        ax.grid(axis="y", color=GRID, lw=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    worst_c = max(abs(d) for d, k in zip(dcrs, keep) if k)
    worst_d = max(abs(d) for d, k in zip(dcdi, keep) if k)
    axes[0].set_title(f"Engine against X-Plane's navigator on compared rows: worst {worst_c:.3f} deg course, "
                      f"{worst_d:.3f} CDI\nGrey: not compared (5 s either side of a sequence, SUSP, hold, "
                      "X-Plane FROM)", fontsize=9.5, color=INK, loc="left")
    names = [ev for ev in events if ev[1] == "sequence"]
    for i, ev in enumerate(names):
        label = "WINCH hold" if ev[2] == "WINCH -> WINCH" else ev[2].split(" -> ")[0]
        axes[0].text(ev[0], -1.45 - (0.3 if i % 2 else 0.0), label, fontsize=8, color=INK2, ha="center")
    axes[1].set_xlabel("Time into the recorded flight (s)")
    fig.tight_layout()
    fig.savefig(HERE / "fp8_engine_vs_xplane.png", dpi=150, facecolor=SURFACE)

    # sequence points
    xp = switch_points(samples, "xp_wpt", fixes, lambda r: (r["lat"], r["lon"]))
    en = switch_points(samples, "wp_name", fixes, lambda r: (r["lat"], r["lon"]))
    prof = load_profile(str(xr.PROFILE))
    ref_rows = [r for _, r in generator.generate(prof).rows]
    rf = switch_points(ref_rows, "REFWPT", fixes, lambda r: (r["LAT"], r["LONG"]))

    fig, ax = plt.subplots(figsize=(8, 3.8), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    series = (("X-Plane 12 navigator", xp, XPLANE, -0.22), ("fix-gateway engine", en, ENGINE, 0.0),
              ("Synthetic reference navigator", rf, REF, 0.22))
    for name, vals, col, off in series:
        ys = [i + off for i in range(len(FIXES))]
        xs = [vals[f] for f in FIXES]
        ax.hlines(ys, 0, xs, color=col, lw=2)
        ax.plot(xs, ys, "o", ms=8, color=col, mec=SURFACE, mew=2, label=name)
        for x, y in zip(xs, ys):
            ax.text(x + 0.006, y, f"{x:.3f}", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(FIXES)))
    ax.set_yticklabels([f"{f}\n(collinear)" for f in FIXES])
    ax.invert_yaxis()
    ax.set_xlim(0, 0.26)
    ax.set_xlabel("Distance before the fix where the active waypoint changed (nm)")
    ax.grid(axis="x", color=GRID, lw=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3, frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(HERE / "fp8_sequence_points.png", dpi=150, facecolor=SURFACE)

    print("worst course %.3f deg, worst CDI %.3f, compared rows %d" % (worst_c, worst_d, sum(keep)))
    for f in FIXES:
        print("%-6s X-Plane %.3f  engine %.3f  reference %.3f nm" % (f, xp[f], en[f], rf[f]))


if __name__ == "__main__":
    main()
