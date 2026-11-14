#!/usr/bin/env python3
"""DEMO7 evidence: pyEfis's P3b traffic layer drawing the demo's traffic,
read over netfix from a running fix-gateway (demo plugin on, stratux off).

Nothing writes a TFC key here. The layer is pyEfis's own TrafficLayer,
configured on the pyavtools FIX client exactly as the map configures it;
this script only supplies the canvas, the projection about ownship (read
from the bus) and, in dim grey, a range ring at each orbit radius and an
ownship cross so the picture can be read.

  render_demo_traffic.py HOST PORT OUT_PREFIX [SECONDS_BETWEEN]
"""
import sys
import time

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

import pyavtools.fix as fix
from pyefis.instruments import map as moving_map
from pyefis.instruments.map.layers import traffic as tfc
from pyefis.instruments.map.perf import MapPerfStats

host, port, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
gap = float(sys.argv[4]) if len(sys.argv) > 4 else 10.0
W = H = 800
RANGE_NM = 10.0

app = QApplication(sys.argv)
fix.initialize({"main": {"FixServer": host, "FixPort": port, "FixInitTimeout": 20}})


class Owner(QWidget):
    def __init__(self):
        super().__init__()
        self.perf = MapPerfStats()
        self._frame_dirty = False


owner = Owner()
layer = tfc.TrafficLayer()
layer.configure(owner)


def pump(s):
    end = time.monotonic() + s
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def render(path):
    lat = fix.db.get_item("LAT").value
    lon = fix.db.get_item("LONG").value
    x = moving_map.MapTransform(lat, lon, RANGE_NM, 0.0, W, H, 0.5)
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(16, 24, 32))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = x.to_screen(lat, lon)
    p.setPen(QPen(QColor(70, 80, 90), 1))
    f = QFont("DejaVu Sans")
    f.setPixelSize(13)
    p.setFont(f)
    for r in (3.0, 5.0, 7.0):
        e = x.to_screen(lat + r / 60.0, lon)
        rad = c.y() - e.y()
        p.drawEllipse(c, rad, rad)
        p.drawText(QPointF(c.x() + 4, c.y() - rad - 4), "%g nm" % r)
    p.drawLine(QPointF(c.x() - 8, c.y()), QPointF(c.x() + 8, c.y()))
    p.drawLine(QPointF(c.x(), c.y() - 8), QPointF(c.x(), c.y() + 8))
    layer.paint(p, x)
    p.end()
    img.save(path)
    ts = time.strftime("%H:%M:%S", time.gmtime())
    print("render %s at %sZ: ownship %.5f %.5f, PALT %s, %d targets, source failed %s"
          % (path, ts, lat, lon, fix.db.get_item("PALT").value,
             len(layer.targets()), layer.source_failed()))
    for t in sorted(layer.targets(), key=lambda t: t.callsign):
        print("  %-6s ident %08X  %.5f %.5f  alt %s  vs %s  trk %s  rel %s  stale %s"
              % (t.callsign, t.ident, t.lat, t.lon, t.alt_ft, t.vs_fpm, t.trk_deg,
                 tfc.relative_alt_ft(t, layer.palt_ft()), t.stale))


pump(4.0)
render(out + "_t0.png")
pump(gap)
render(out + "_t%d.png" % int(gap))
fix.db.stop()
