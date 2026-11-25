# ARCHG1 (AER-6350): the architecture ratchet.  Every FIX key a module writes is
# declared in src/fixgw/database/writers.yaml; no key gains a second live writer
# unless it is on tests/arch/key_writers_baseline.yaml, which only shrinks; and
# no new class takes a route-concept name (tests/arch/concept_names.yaml).
# tools/check_key_writers.py is the scanner; doc/key_writers.md says how to
# declare a writer.  Static: nothing under plugins/ is imported.

import os
import sys
import textwrap

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import check_key_writers as ckw  # noqa: E402


@pytest.fixture(scope="module")
def catalog():
    return ckw.load_catalog(ROOT)


@pytest.fixture(scope="module")
def checks(catalog):
    return ckw.run_checks(ROOT, catalog)


def report(fails):
    return "\n" + "\n".join(fails)


def test_every_write_is_declared_in_writers_yaml(checks):
    register_fails = checks[0]
    assert not register_fails, report(register_fails)


def test_no_new_multi_writer_key_and_the_baseline_only_shrinks(checks):
    baseline_fails = checks[1]
    assert not baseline_fails, report(baseline_fails)


def test_no_new_class_takes_a_route_concept_name(checks):
    concept_fails = checks[2]
    assert not concept_fails, report(concept_fails)


def test_every_source_key_writer_declares_datum_quality_and_on_loss(checks):
    column_fails = checks[3]
    assert not column_fails, report(column_fails)


def test_no_key_gains_a_second_datum_and_the_datum_baseline_only_shrinks(checks):
    datum_fails = checks[4]
    assert not datum_fails, report(datum_fails)


def test_talt_has_the_position_writers():
    """TALT, the geometric MSL height, comes from the receiver that gives the
    position, so its live writers are exactly LAT's and LONG's (Bill, card
    82b0b10b on AER-6458). This is the condition on TALT's place in
    key_writers_baseline.yaml: a source that writes height without position,
    or position without height, fails here. SRC5a's position select, which
    picks the whole group from one receiver, retires this test."""
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)
    live = {k: set(reg[k].get("writers", [])) | set(reg[k].get("pyefis", []))
            for k in ("TALT", "LAT", "LONG")}
    assert live["TALT"] == live["LAT"] == live["LONG"], live
    # The sims too, or a demo, FlightGear or X-Plane panel shows SVS UNAVAIL.
    sim = {k: set(reg[k].get("sim", [])) for k in ("TALT", "LAT", "LONG")}
    assert sim["TALT"] == sim["LAT"] == sim["LONG"], sim


def test_the_declaration_records_what_arch2_found():
    # SRC4 seeds the columns from ARCH2 section 3.1, wrong parts included.
    # These are the findings a consumer would otherwise rebuild for itself.
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)

    def tok(key, col, m):
        return ckw.parse_cell(reg[key][col][m])[0]

    # GALT1/TALTSRC1 (AER-6458): the MSL writers moved to TALT, so the
    # ellipsoid key holds one datum.
    assert {m: tok("GPS_ELLIPSOID_ALT", "datum", m)[0] for m in reg["GPS_ELLIPSOID_ALT"]["datum"]} \
        == {"mavlink": "ellipsoid"}
    # Which position sources say how good the fix is: the AER-4217 question.
    has_acc = {m for m in reg["LAT"]["quality"] if "GPS_ACCURACY_HORIZ" in tok("LAT", "quality", m)}
    assert has_acc == {"gpsd", "mavlink", "demo"}
    assert tok("IAS", "on_loss", "rpi_virtualwire") == ["holds_last"]
    assert "writes_zero" in tok("PITCH", "on_loss", "mavlink")
    assert tok("ALT", "on_loss", "onspeed") == ["fail"]
    assert tok("ALT", "quality", "canfix") == ["passthrough"]


def test_a_second_datum_on_alt_fails_the_real_register():
    # The definition of done's throwaway commit, in a test: one ALT writer
    # declares another datum, or a clean key gains a second one.
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)
    base = ckw.load_yaml(ROOT, ckw.DATUM_BASELINE)
    assert ckw.check_datum_baseline(reg, base) == []
    reg["ALT"]["datum"]["canfix"] = "pressure: a mutant"
    assert ckw.check_datum_baseline(reg, base) == [
        "ALT: canfix now declares `pressure`, and the datum baseline has `indicated`"]
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)
    reg["LAT"]["datum"]["gpsd"] = "msl: a mutant"
    fails = ckw.check_datum_baseline(reg, base)
    assert len(fails) == 1 and fails[0].startswith(
        "LAT: its writers declare more than one datum (")


def test_the_scan_sees_the_known_writers(catalog):
    # The ratchet proves nothing if the scan goes blind: these are ARCH0's
    # known answers, one per detection path.
    writes = ckw.scan_writes(ROOT, catalog)
    seen = {(w["key"], w["module"]) for w in writes}
    assert ("ALT", "mavlink") in seen                # db_write literal
    assert ("ALT", "canfix") in seen                 # canfix map.yaml inputs
    assert ("CHT16", "canfix") in seen               # canfix `index: c` input
    assert ("CDI", "compute") in seen                # compute.yaml output
    assert ("WPLAT", "flightplan") in seen           # _setval wrapper
    assert ("QVOTE1", "canfix") in seen              # f-string QVOTE{}
    assert ("APLAT", "xplane") in seen               # _write wrapper, sim
    assert ("TALT", "navigator_adapter") in seen    # fix_map db_write
    assert ("TALT", "xplane") in seen                # xplane.yaml datarefs, sim
    # Writers that loop over a key table (SRC4, AER-6455).
    assert ("ALT", "onspeed") in seen                # OUTPUT_GROUPS
    assert ("ALT", "demo") in seen                   # SOURCE_KEYS
    assert ("OILT1", "demo") in seen                 # ENGINE_KEYS
    assert ("ALT", "xplane") in seen                 # xplane.yaml recv row
    assert ("EXTCRS", "xplane") in seen              # xplane.yaml datarefs
    assert ("ALT", "fgfs") in seen                   # fix_fgfs.xml <output>
    selectors = ckw.scan_selectors(ROOT)
    assert selectors["CDI"]["by"] == "NAVSRC"
    assert selectors["BRG1"]["sources"] == ["VOR1BRG", "VOR2BRG", "GPSBRG"]


# ---- the rules, on a small tree ------------------------------------------
CAT = {k: {"key": k, "units": "ft", "type": "float", "description": d} for k, d in [
    ("ALT", "Indicated Altitude"), ("VS", "Vertical speed"), ("HEAD", "Heading"),
    ("NAVSRC", "Nav source"), ("NAV1CDI", "Nav 1 CDI"), ("GPSCDI", "GPS CDI"),
    ("CDI", "CDI"), ("BARO", "Altimeter setting")]}


def tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))
    return str(tmp_path)


def plugin(key, wrapper=False):
    if wrapper:
        return """
            KEY = "%s"
            def _put(parent, key, v):
                parent.db_write(key, v)
            def run(parent):
                _put(parent, KEY, 1)
        """ % key
    return 'def run(parent):\n    parent.db_write("%s", 1)\n' % key


def rules(root, register, baseline, selectors=None):
    writes = ckw.scan_writes(root, CAT)
    sel = ckw.scan_selectors(root) if selectors is None else selectors
    return ckw.check_register(CAT, writes, sel, register) + ckw.check_baseline(register, baseline)


def test_an_undeclared_write_fails_and_names_the_fix(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("VS")})
    fails = rules(root, {}, {})
    assert len(fails) == 1
    assert "VS: aaa writes it (src/fixgw/plugins/aaa/__init__.py:2)" in fails[0]
    assert "declare it in src/fixgw/database/writers.yaml and add the roadmap 9.1 block" in fails[0]


def test_a_wrapper_and_a_module_constant_are_writes(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("VS", wrapper=True)})
    assert [(w["key"], w["module"], w["how"]) for w in ckw.scan_writes(root, CAT)] == \
        [("VS", "aaa", "_put(constant)")]


def test_a_key_table_is_read_as_the_plugin_s_writes(tmp_path):
    # demo, onspeed, xplane and fgfs write in a loop over a table; the scan
    # reads the table.  A key the sim sends the other way is not a write.
    root = tree(tmp_path, {
        "src/fixgw/plugins/aaa/__init__.py":
            'OUTPUT_GROUPS = (("air", ("VS",)), ("alt", ("ALT",)))\n'
            'OTHER = ("HEAD",)\n',
        "src/fixgw/config/connections/xplane.yaml": """
            xplane:
                recv:
                    3: [_, ALT, _]
                datarefs:
                    VS: sim/vvi
                dataref_writes:
                    BARO: sim/baro
        """,
        "src/fixgw/config/connections/fgfs/fix_fgfs.xml": """
            <PropertyList><generic>
              <output><chunk><name>HEAD: Heading</name></chunk></output>
              <input><chunk><name>BARO: setting</name></chunk></input>
            </generic></PropertyList>
        """})
    assert sorted((w["key"], w["module"], w["how"]) for w in ckw.scan_writes(root, CAT)) == [
        ("ALT", "aaa", "table OUTPUT_GROUPS"), ("ALT", "xplane", "yaml:recv"),
        ("HEAD", "fgfs", "xml:output"), ("VS", "aaa", "table OUTPUT_GROUPS"),
        ("VS", "xplane", "yaml:datarefs")]


def test_a_second_live_writer_fails_even_when_declared(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("VS"),
                           "src/fixgw/plugins/bbb/__init__.py": plugin("VS")})
    register = {"VS": {"units": "ft", "writers": ["aaa", "bbb"]}}
    fails = rules(root, register, {})
    assert len(fails) == 1
    assert fails[0].startswith("VS: 2 live writers (aaa, bbb), and it is not on the baseline")
    assert "add a selector" in fails[0]


def test_a_pyefis_writer_counts_as_a_live_writer(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("BARO")})
    register = {"BARO": {"units": "inHg", "writers": ["aaa"],
                         "pyefis": ["pyefis/instruments/altimeter"]}}
    assert "not on the baseline" in rules(root, register, {})[0]
    assert rules(root, register, {"BARO": ["aaa", "pyefis/instruments/altimeter"]}) == []


def test_a_sim_writer_is_declared_but_is_not_a_second_writer(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("VS"),
                           "src/fixgw/plugins/xplane/__init__.py": plugin("VS")})
    assert rules(root, {"VS": {"units": "ft", "writers": ["aaa"]}}, {})[0].startswith(
        "VS: xplane writes it")
    assert rules(root, {"VS": {"units": "ft", "writers": ["aaa"], "sim": ["xplane"]}}, {}) == []


def test_a_baseline_key_cannot_gain_a_writer(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/%s/__init__.py" % m: plugin("HEAD") for m in "abc"})
    register = {"HEAD": {"units": "deg", "writers": ["a", "b", "c"]}}
    fails = rules(root, register, {"HEAD": ["a", "b"]})
    assert len(fails) == 1
    assert fails[0].startswith("HEAD: gained live writer(s) c; the baseline allows only a, b")


def test_the_baseline_only_shrinks(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/a/__init__.py": plugin("HEAD"),
                           "src/fixgw/plugins/b/__init__.py": plugin("HEAD")})
    register = {"HEAD": {"units": "deg", "writers": ["a", "b"]}}
    assert rules(root, register, {"HEAD": ["a", "b", "c"]}) == \
        ["HEAD: lost writer(s) c; remove them from its baseline entry"]
    os.remove(os.path.join(root, "src/fixgw/plugins/b/__init__.py"))
    fails = rules(root, {"HEAD": {"units": "deg", "writers": ["a"]}}, {"HEAD": ["a", "b"]})
    assert fails == ["HEAD: now has 1 live writer(s) and is still on the baseline; remove "
                     "it from tests/arch/key_writers_baseline.yaml"]


def test_a_writer_that_stopped_writing_must_leave_the_register(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("VS")})
    fails = rules(root, {"VS": {"units": "ft", "writers": ["aaa", "gone"]}}, {"VS": ["aaa", "gone"]})
    assert fails == ["VS: writers.yaml lists gone under `writers`, and the scan finds no "
                     "write from it; remove it from writers.yaml"]


SELECT = """
    compute:
      functions:
        - function: select
          inputs: ["NAVSRC", "NAV1CDI", "GPSCDI"]
          output: CDI
"""


def test_the_bearing_pointer_pattern_passes(tmp_path):
    # Sources write their own keys, one compute select writes the shared key.
    root = tree(tmp_path, {"src/fixgw/plugins/nav/__init__.py": plugin("NAV1CDI"),
                           "src/fixgw/plugins/gps/__init__.py": plugin("GPSCDI"),
                           "src/fixgw/config/connections/compute.yaml": SELECT})
    sel = {"module": "compute", "by": "NAVSRC", "sources": ["NAV1CDI", "GPSCDI"]}
    register = {"NAV1CDI": {"units": "", "writers": ["nav"]},
                "GPSCDI": {"units": "", "writers": ["gps"]},
                "CDI": {"units": "", "writers": ["compute"], "selector": sel}}
    assert rules(root, register, {}) == []
    # A source that writes the shared key as well breaks the pattern.
    with open(os.path.join(root, "src/fixgw/plugins/gps/__init__.py"), "a") as f:
        f.write('    parent.db_write("CDI", 1)\n')
    register["CDI"]["writers"] = ["compute", "gps"]
    fails = rules(root, register, {})
    assert fails == ["CDI: has a selector, so compute must be its only live writer; gps also "
                     "write it.  Write a source key instead and add it to the selector's inputs"]


def test_an_altitude_key_needs_a_datum(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("ALT")})
    fails = rules(root, {"ALT": {"units": "ft", "writers": ["aaa"]}}, {})
    assert fails == ["ALT: an altitude or position key needs a `datum` in writers.yaml "
                     "(`unrecorded` if nobody has read the writer)"]
    assert rules(root, {"ALT": {"units": "ft", "datum": {"aaa": "MSL"}, "writers": ["aaa"]}}, {}) == []


def test_a_new_waypoint_class_fails(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/model.py": "class Waypoint:\n    pass\n",
                           "src/fixgw/plugins/bbb/model.py": "import collections\n"
                                                             "Leg = collections.namedtuple('Leg', 'a b')\n"
                                                             "class Waypoint:\n    pass\n"})
    fails = ckw.check_concepts(root, {"Waypoint": ["src/fixgw/plugins/aaa/model.py"],
                                      "Leg": [], "Route": ["src/fixgw/plugins/gone.py"]})
    assert len(fails) == 3
    assert fails[0].startswith("class Leg: defined in src/fixgw/plugins/bbb/model.py")
    assert fails[1] == ("class Route: concept_names.yaml lists src/fixgw/plugins/gone.py, which "
                        "no longer defines it; remove the file from the list")
    assert fails[2].startswith("class Waypoint: defined in src/fixgw/plugins/bbb/model.py, and "
                               "concept_names.yaml allows it only in src/fixgw/plugins/aaa/model.py")


def test_generate_reproduces_the_committed_records(catalog, tmp_path):
    # The committed register and baseline are exactly what the scan states,
    # with the datum and pyEfis entries carried over.
    import yaml
    writes = ckw.scan_writes(ROOT, catalog)
    old = ckw.load_yaml(ROOT, ckw.REGISTER)
    reg = ckw.build_register(catalog, writes, ckw.scan_selectors(ROOT), old)
    assert reg == old
    assert ckw.build_baseline(reg) == ckw.load_yaml(ROOT, ckw.BASELINE)
    assert ckw.build_datum_baseline(reg) == ckw.load_yaml(ROOT, ckw.DATUM_BASELINE)
    assert yaml.safe_load(ckw.dump_register(reg)) == reg


# ---- the provider declaration, on a small register (SRC4) ------------------
def columns(register, keys=("ALT",)):
    return ckw.check_columns(CAT, register, provider_keys=keys)


def alt(**cols):
    e = {"units": "ft", "writers": ["aaa", "bbb"]}
    e.update(cols)
    return {"ALT": e, "VS": {"units": "ft", "writers": ["bbb"]}}


GOOD = dict(datum={"aaa": "indicated", "bbb": "indicated: note"},
            quality={"aaa": "none", "bbb": "bad, VS: note"},
            on_loss={"aaa": "silence", "bbb": "fail, writes_zero"},
            rate_hz={"aaa": 10})


def test_a_complete_declaration_passes():
    assert columns(alt(**GOOD)) == []


def test_each_writer_of_a_source_key_declares_each_column():
    reg = alt(**dict(GOOD, quality={"aaa": "none"}))
    assert columns(reg) == ["ALT: bbb writes it and declares no `quality` in writers.yaml"]
    reg = alt(**{k: v for k, v in GOOD.items() if k != "on_loss"})
    assert columns(reg) == ["ALT: a source key needs a `on_loss` mapping, one cell per writer, in "
                            "writers.yaml (doc/key_writers.md)"]
    # A key that is not a source key may leave the columns out.
    assert columns(alt(), keys=()) == []


def test_a_cell_for_a_module_that_does_not_write_the_key_fails():
    reg = alt(**dict(GOOD, on_loss=dict(GOOD["on_loss"], ccc="silence")))
    assert columns(reg) == ["ALT: writers.yaml gives `on_loss` for ccc, which is not one of its writers"]


def test_a_cell_outside_its_vocabulary_fails():
    reg = alt(**dict(GOOD, datum={"aaa": "indicated", "bbb": "baro"},
                     on_loss={"aaa": "silence", "bbb": "explodes"},
                     quality={"aaa": "none, bad", "bbb": "HEAD"},
                     rate_hz={"aaa": "fast"}))
    fails = columns(reg)
    assert len(fails) == 5
    assert fails[0].startswith("ALT: writers.yaml `datum` for bbb is 'baro'; a datum is one of")
    assert fails[1] == "ALT: writers.yaml `quality` for aaa says `none` and also bad"
    assert fails[2] == "ALT: writers.yaml `quality` for bbb names HEAD, which bbb does not write"
    assert fails[3].startswith("ALT: writers.yaml `on_loss` for bbb names explodes; on_loss is")
    assert fails[4].startswith("ALT: writers.yaml `rate_hz` for aaa is 'fast'")


def test_two_datums_fail_unless_on_the_datum_baseline():
    reg = alt(datum={"aaa": "indicated", "bbb": "msl"})
    assert ckw.check_datum_baseline(reg, {}) == [
        "ALT: its writers declare more than one datum (aaa indicated, bbb msl), and it is not on "
        "tests/arch/datum_baseline.yaml.  A key has one datum: give the other datum its own key, "
        "as GALT1 does, or fix the writer, as MAVALT1 did"]
    assert ckw.check_datum_baseline(reg, {"ALT": {"aaa": "indicated", "bbb": "msl"}}) == []
    # Two writers that cannot be shown to agree count as two datums.
    assert ckw.check_datum_baseline(alt(datum={"aaa": "unrecorded", "bbb": "unrecorded"}), {})
    assert ckw.check_datum_baseline(alt(datum={"aaa": "msl", "bbb": "msl"}), {}) == []


def test_the_datum_baseline_only_shrinks():
    base = {"ALT": {"aaa": "indicated", "bbb": "msl"}}
    reg = alt(datum={"aaa": "indicated", "bbb": "msl", "ccc": "pressure"})
    assert ckw.check_datum_baseline(reg, base) == [
        "ALT: ccc declares `pressure` and the datum baseline does not list it; a new writer takes "
        "the key's one datum"]
    assert ckw.check_datum_baseline(alt(datum={"aaa": "indicated", "bbb": "indicated"}), base) == [
        "ALT: its writers now agree on one datum and it is still on the datum baseline; remove it "
        "from tests/arch/datum_baseline.yaml"]
    base3 = {"ALT": {"aaa": "indicated", "bbb": "msl", "ccc": "pressure"}}
    assert ckw.check_datum_baseline(alt(datum={"aaa": "indicated", "bbb": "msl"}), base3) == [
        "ALT: the datum baseline lists ccc, which no longer declares a datum; remove it from the entry"]
