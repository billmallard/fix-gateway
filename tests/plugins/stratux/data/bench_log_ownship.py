# Run the branch's stratux plugin on the real database against the live
# FlightBox, and log every STX ownship key beside the FlightBox's own
# /getSituation (what its web UI shows) every 3 s.
import json, sys, time, urllib.request
sys.path.insert(0, "src")
import fixgw.database as database
import fixgw.plugins.stratux as stratux
database.init("src/fixgw/config/database.yaml")
p = stratux.Plugin("stratux", {"port": 4000, "source": "10.110.10.169", "status": False, "traffic": False}, {})
p.run()
keys = ["STXLAT", "STXLONG", "STXPALT", "STXGS", "STXTRACK", "STXVS", "STXAIR",
        "STXNIC", "STXNACP", "STXRC", "STXEPU", "STXGALT", "STXVFOM", "STXVWARN"]
def flags(v):
    return "".join(f for f, on in zip("OBF", v[2:5]) if on) or "-"
try:
    for i in range(10):
        time.sleep(3)
        s = json.loads(urllib.request.urlopen("http://10.110.10.169/getSituation", timeout=2).read())
        print("t=%2ds FIX  " % (3 * i + 3) + "  ".join("%s=%s[%s]" % (k[3:], round(database.read(k)[0], 5) if isinstance(database.read(k)[0], float) else database.read(k)[0], flags(database.read(k))) for k in keys))
        print("      WEB  lat=%.5f lon=%.5f baroPA=%.0f gs=%.1f trk=%.1f NACp=%d hacc=%.1fm MSL=%.0f HAE=%.0f vacc=%.1fm sats=%d" % (
            s["GPSLatitude"], s["GPSLongitude"], s["BaroPressureAltitude"], s["GPSGroundSpeed"], s["GPSTrueCourse"],
            s["GPSNACp"], s["GPSHorizontalAccuracy"], s["GPSAltitudeMSL"], s["GPSHeightAboveEllipsoid"], s["GPSVerticalAccuracy"], s["GPSSatellites"]))
    print("writers:", {k: v for k, v in database.writers().items() if k.startswith("STX") or k in ("ALT", "PALT", "TALT", "LAT", "LONG")})
finally:
    p.stop()
