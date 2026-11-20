"""Plot the OBS2 TONIE flight (tests/sim/test_obs_tonie.py).

Run from the repo root:

    PYTHONPATH=src:. python3 doc/evidence/aer-6084-obs/plot_tonie.py

Writes tonie_plan_view.png, tonie_strips.png and tonie_flight.csv beside
this file. The flight is the test's own fly(), so the picture is the run the
test checks.
"""

import csv
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import fixgw.geo as geo  # noqa: E402
from fixgw.plugins.flightplan.engine import STATE_OBS  # noqa: E402
from tests.sim.test_obs_tonie import FLOUT, MAGVAR, OBS_CRS, OSH, TONIE, fly  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NORMAL = "#2a78d6"   # categorical slot 1: track in normal mode
OBS = "#eb6834"      # categorical slot 2: track in OBS
INK, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dc"


def xy(lat, lon):
    """nm east and north of TONIE (flat, fine at this size)."""
    d = geo.distance_nm(*TONIE, lat, lon)
    b = math.radians(geo.bearing_deg(*TONIE, lat, lon))
    return d * math.sin(b), d * math.cos(b)


def style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def main():
    rows = fly()
    with open(os.path.join(HERE, "tonie_flight.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "lat", "lon", "FPLSTATE", "FPLACTLEG", "WPNAME", "FPLCRS", "FPLXTK", "FPLTF",
                    "WPDIS", "FPLCMD", "FPLCMDACK", "FPLMSG"])
        for r in rows:
            c = r["cmd"] or ("", "", "")
            w.writerow([r["t"], "%.6f" % r["lat"], "%.6f" % r["lon"], r["state"], r["act_leg"], r["wp_name"],
                        "%.2f" % r["crs"], "%.4f" % r["xtk"], r["tf"], "%.3f" % r["wp_dis"], *c])

    # --- plan view ----------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8.0, 7.0), dpi=150)
    style(ax)
    plan = [xy(*FLOUT), (0.0, 0.0), xy(*OSH)]
    ax.plot([p[0] for p in plan], [p[1] for p in plan], color=MUTED, linewidth=1.2, linestyle=(0, (4, 3)),
            zorder=1)
    true = math.radians(OBS_CRS - MAGVAR)
    line = [(-k * math.sin(true), -k * math.cos(true)) for k in (34.0, -8.0)]
    ax.plot([p[0] for p in line], [p[1] for p in line], color=OBS, linewidth=1.0, linestyle=(0, (1, 2)), zorder=1)
    ax.text(line[0][0], line[0][1] - 2.2, "the 270 course through TONIE", color=MUTED, fontsize=9, ha="right")

    seg, cur = [], None
    for r in rows:
        mode = r["state"] == STATE_OBS
        p = xy(r["lat"], r["lon"])
        if cur is None or mode != cur:
            if seg:
                ax.plot(*zip(*seg, p), color=OBS if cur else NORMAL, linewidth=2.0, solid_capstyle="round",
                        zorder=3)
            seg, cur = [p], mode
        else:
            seg.append(p)
    ax.plot(*zip(*seg), color=OBS if cur else NORMAL, linewidth=2.0, solid_capstyle="round", zorder=3)

    for name, p in (("FLOUT", plan[0]), ("TONIE", plan[1]), ("OSH", plan[2])):
        ax.plot(*p, marker="^", markersize=9, color=INK, markeredgecolor="white", zorder=4)
        ax.annotate(name, p, xytext=(-44, 2) if name == "OSH" else (6, 6), textcoords="offset points",
                    fontsize=10, color=INK)

    def mark(r, text, dx, dy):
        p = xy(r["lat"], r["lon"])
        ax.plot(*p, marker="o", markersize=8, color="white", markeredgecolor=INK, markeredgewidth=1.5, zorder=5)
        ax.annotate(text, p, xytext=(dx, dy), textcoords="offset points", fontsize=9, color=INK,
                    arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.8))

    obs_on = next(r for r in rows if r["cmd"] and "OBS 270" in r["cmd"][0])
    flip = next(b for a, b in zip(rows, rows[1:]) if a["state"] == STATE_OBS and a["tf"] != b["tf"])
    obs_off = next(r for r in rows if r["cmd"] and r["cmd"][0].endswith("OBS OFF"))
    mark(obs_on, "OBS 270 set\n(t = %d s)" % obs_on["t"], 10, -30)
    mark(flip, "TO -> FROM over TONIE,\nTONIE stays active", -40, -45)
    mark(obs_off, "OBS OFF 4 nm past:\nSEQ TONIE -> OSH", -150, 20)

    ax.plot([], [], color=NORMAL, linewidth=2.0, label="track, FPLSTATE 1 (LEG)")
    ax.plot([], [], color=OBS, linewidth=2.0, label="track, FPLSTATE 4 (OBS)")
    ax.plot([], [], color=MUTED, linewidth=1.2, linestyle=(0, (4, 3)), label="flight plan FLOUT - TONIE - OSH")
    ax.legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK)
    ax.set_aspect("equal")
    ax.set_xlabel("nm east of TONIE", color=MUTED, fontsize=9)
    ax.set_ylabel("nm north of TONIE", color=MUTED, fontsize=9)
    ax.set_title("OBS2: \"intercept the 270 course inbound to TONIE\"",
                 color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "tonie_plan_view.png"))
    plt.close(fig)

    # --- strips: one measure per panel, shared time axis --------------------
    t = [r["t"] / 60.0 for r in rows]
    fig, axes = plt.subplots(4, 1, figsize=(8.0, 7.5), dpi=150, sharex=True)
    panels = (
        ("FPLSTATE", [r["state"] for r in rows], (0.5, 4.5), [1, 2, 3, 4]),
        ("FPLTF (1 TO, 2 FROM)", [r["tf"] for r in rows], (0.5, 2.5), [1, 2]),
        ("FPLCRS, deg mag", [r["crs"] for r in rows], None, None),
        ("FPLXTK, nm (+ right)", [r["xtk"] for r in rows], None, None),
    )
    for ax, (label, vals, ylim, ticks) in zip(axes, panels):
        style(ax)
        if ticks:
            ax.step(t, vals, where="post", color=NORMAL, linewidth=1.6)
        else:
            ax.plot(t, vals, color=NORMAL, linewidth=1.6)
        ax.set_ylabel(label, color=MUTED, fontsize=9)
        if ylim:
            ax.set_ylim(*ylim)
            ax.set_yticks(ticks)
        for r, txt in ((obs_on, "OBS 270"), (flip, "TONIE"), (obs_off, "OBS OFF")):
            ax.axvline(r["t"] / 60.0, color=MUTED, linewidth=0.8, linestyle=(0, (2, 2)))
            if ax is axes[0]:
                ax.text(r["t"] / 60.0, 4.6, txt, color=INK, fontsize=8, va="bottom",
                        ha="right" if txt == "TONIE" else "left" if txt == "OBS OFF" else "center")
    axes[-1].set_xlabel("minutes", color=MUTED, fontsize=9)
    fig.suptitle("Engine outputs through the TONIE flight", color=INK, fontsize=11, x=0.02, ha="left")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "tonie_strips.png"))
    plt.close(fig)


if __name__ == "__main__":
    main()
