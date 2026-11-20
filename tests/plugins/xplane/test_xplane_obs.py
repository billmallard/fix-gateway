"""X-Plane's GPS OBS state and course reach the bus (OBS2, AER-6084).

X-Plane 12 reports whether its GPS is sequencing normally, in OBS, or
suspended (``sim/cockpit/radios/gps_sequencing``, 0/1/2, 12.0.0), and the
OBS course (``sim/cockpit/radios/gps_obs_degm``, 12.1.0). The shipped
``connections/xplane.yaml`` maps them to EXTSEQ and EXTOBSCRS so a bench
flight can compare X-Plane's state and course with the engine's FPLSTATE
and FPLCRS. Read only: nothing here writes them back to the sim.
"""

from .test_xplane_exttf import gateway, rref, shipped  # noqa: F401  (gateway is a fixture)

import fixgw.database as database


def test_obs_state_and_course_are_mapped():
    refs = shipped("xplane.yaml")["xplane"]["datarefs"]
    assert refs["EXTSEQ"] == "sim/cockpit/radios/gps_sequencing"
    assert refs["EXTOBSCRS"] == "sim/cockpit/radios/gps_obs_degm"


def test_xplane_obs_state_and_course_reach_the_database(gateway):  # noqa: F811
    for seq, crs in ((0.0, 310.0), (1.0, 270.0), (2.0, 270.0), (0.0, 40.0)):
        gateway._ingest_rref(rref(gateway, **{"sim/cockpit/radios/gps_sequencing": seq,
                                              "sim/cockpit/radios/gps_obs_degm": crs}))
        assert database.read("EXTSEQ")[0] == int(seq)
        assert database.read("EXTOBSCRS")[0] == crs
