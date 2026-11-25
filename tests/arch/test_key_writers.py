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


def test_the_declaration_records_what_arch2_found():
    # SRC4 seeds the columns from ARCH2 section 3.1, wrong parts included.
    # These are the findings a consumer would otherwise rebuild for itself.
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)

    def tok(key, col, m):
        return ckw.parse_cell(reg[key][col][m])[0]

    assert {m: tok("GPS_ELLIPSOID_ALT", "datum", m)[0] for m in reg["GPS_ELLIPSOID_ALT"]["datum"]} \
        == {"garmin_gnx375": "msl", "gpsd": "msl", "mavlink": "ellipsoid", "navigator_adapter": "msl"}
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
    assert ("GPS_ELLIPSOID_ALT", "navigator_adapter") in seen
    # Writers that loop over a key table (SRC4, AER-6455).
    assert ("ALT", "onspeed") in seen                # OUTPUT_GROUPS
    assert ("ALT", "demo") in seen                   # SOURCE_KEYS
    assert ("OILT1", "demo") in seen                 # ENGINE_KEYS
    assert ("ALT", "xplane") in seen                 # xplane.yaml recv row
    assert ("EXTCRS", "xplane") in seen              # xplane.yaml datarefs
    assert ("ALT", "fgfs") in seen                   # fix_fgfs.xml <output>
    # Names built at run time, and the rows demo streams (FPA6, AER-6518).
    assert ("FPLIMP100ID", "fpl_exchange") in seen   # "FPLIMP%dID" % i
    assert ("FPLSEQ", "demo") in seen                # loop over literal pairs
    assert ("FPL1LAT", "demo") in seen               # fplroute row, f-string FPL{i}{field}
    assert ("FPLS1KIND", "demo") in seen
    assert ("FPLCMD", "demo") in seen                # generator row["FPLCMD"]
    assert ("FPLFRLAT", "demo") not in seen          # an engine output: {i} is a counter
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


CAT_FPL = {k: {"key": k, "units": "", "type": t, "description": d} for k, t, d in
           [("FPL%d%s" % (i, f), t, d) for i in (1, 2, 10)
            for f, t, d in (("ID", "str", "Slot ident"), ("LAT", "float", "Slot latitude"),
                            ("ALT", "str", "Slot altitude constraint"))]
           + [("FPLFRLAT", "float", "FROM point latitude"), ("FPLSEQ", "int", "Commit counter"),
              ("FPLNAME", "str", "Route name")]}


def scanned(root, cat=CAT_FPL):
    return sorted({(w["key"], w["how"]) for w in ckw.scan_writes(root, cat)})


def test_a_percent_formatted_key_name_is_a_write(tmp_path):
    # fpl_exchange writes its 400 point keys as "FPLIMP%dID" % i, and the
    # scan saw none of them (FPA6).
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": """
        def run(parent, points):
            for i, p in enumerate(points, start=1):
                parent.write("FPL%dID" % i, p)
            parent.write("FPL%dLAT" % (2,), 0.0)
            parent.write("FPL%sALT" % "10", "")
    """})
    hows = {h for _, h in scanned(root)}
    assert hows == {"write(%-format FPL{n}ID)", "write(%-format FPL{n}LAT)",
                    "write(%-format FPL{}ALT)"}
    keys = {k for k, _ in scanned(root)}
    assert keys == {"FPL1ID", "FPL2ID", "FPL10ID", "FPL1LAT", "FPL2LAT", "FPL10LAT",
                    "FPL1ALT", "FPL2ALT", "FPL10ALT"}


def test_format_and_loop_names_resolve_and_a_counter_is_digits(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": """
        def run(parent, slots):
            parent.write("FPL{}ID".format(1), "")
            parent.write("FPL{n}ALT".format(n=2), "")
            for key, value in (("FPLSEQ", 1), ("FPLNAME", "")):
                parent.write(key, value)
            for i, slot in enumerate(slots, start=1):
                for field in ("LAT",):
                    parent.write(f"FPL{i}{field}", slot)
    """})
    keys = {k for k, _ in scanned(root)}
    # {i} counts slots, so FPLFRLAT (an engine output) is not one of them.
    assert keys == {"FPL1ID", "FPL2ID", "FPL10ID", "FPL1ALT", "FPL2ALT", "FPL10ALT",
                    "FPLSEQ", "FPLNAME", "FPL1LAT", "FPL2LAT", "FPL10LAT"}


def test_demo_writes_the_rows_it_streams(tmp_path):
    root = tree(tmp_path, {"src/fixgw/sim/fplroute.py": """
        def route_block_writes(slots, name, seq):
            writes = []
            for i, slot in enumerate(slots, start=1):
                for field in ("ID", "LAT"):
                    writes.append((f"FPL{i}{field}", slot[field]))
            writes += [("FPLNAME", name), ("FPLSEQ", seq)]
            return writes
    """, "src/fixgw/sim/generator.py": """
        def rows(route):
            row = {}
            row["REFLAT"] = 0.0
            row["FPLSEQ"] = 1
            return row
    """})
    cat = dict(CAT_FPL, REFLAT={"key": "REFLAT", "units": "", "type": "float", "description": ""})
    w = ckw.scan_writes(root, cat)
    assert {(x["key"], x["module"], x["category"]) for x in w} == \
        {(k, "demo", "sim") for k in ("FPL1ID", "FPL2ID", "FPL10ID", "FPL1LAT", "FPL2LAT",
                                      "FPL10LAT", "FPLNAME", "FPLSEQ")}


def test_row_streams_names_what_demo_streams():
    # ROW_STREAMS is a declaration the scan trusts: demo streams the sim
    # generator's rows through live.stream, all but the REF* keys.
    src = ckw.read(os.path.join(ROOT, "src/fixgw/plugins/demo/__init__.py"))
    assert "live.stream(_Run(renumber(rows" in src
    assert "k.startswith(REF_PREFIX)" in src and 'REF_PREFIX = "%s"' % ckw.ROW_SKIP in src
    gen = ckw.read(os.path.join(ROOT, "src/fixgw/sim/generator.py"))
    assert "fplroute.route_block_writes(" in gen
    for rel in ckw.ROW_STREAMS["demo"]:
        assert os.path.exists(os.path.join(ROOT, rel)), rel


def test_a_datum_on_a_text_key_fails(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py":
                           'def run(parent):\n    parent.write("FPL1ALT", "")\n'})
    writes = ckw.scan_writes(root, CAT_FPL)
    reg = {"FPL1ALT": {"units": "", "datum": "geodetic degrees", "writers": ["aaa"]}}
    assert ckw.check_register(CAT_FPL, writes, {}, reg) == [
        "FPL1ALT: writers.yaml gives a datum for a str key; a datum describes an altitude or "
        "position number, so remove it"]
    # --generate drops it.
    assert "datum" not in ckw.build_register(CAT_FPL, writes, {}, reg)["FPL1ALT"]


def test_the_register_records_the_flight_plan_writers():
    # FPA6 (AER-6518): ARCH1 section 2.3 finding 6, on the real register.
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)
    imp = [k for k in reg if k.startswith("FPLIMP") and k[6:7].isdigit()]
    assert len(imp) == 400 and all(reg[k]["writers"] == ["fpl_exchange"] for k in imp)
    assert reg["FPLIMP1LAT"]["datum"]["fpl_exchange"].startswith("wgs84:")
    for i in range(1, 101):
        for f in ("ALT", "PT"):
            assert "datum" not in reg["FPL%d%s" % (i, f)]
    for k in ("FPL1ID", "FPL100TURN", "FPLS1KIND", "FPLCOUNT", "FPLSEQ"):
        assert reg[k]["pyefis"] == ["pyefis/flightplan"] and reg[k]["sim"] == ["demo"], k
    # The HSI sends FPLCMD too (OUT1 found it; ARCHG2b declares it).
    assert reg["FPLCMD"]["pyefis"] == ["pyefis/flightplan", "pyefis/instruments/hsi"]
    for k in ("DTOID", "DTOLAT", "DTOLON", "DTOTYPE"):
        assert reg[k]["pyefis"] == ["pyefis/flightplan"], k
    assert reg["BRG1SRC"]["pyefis"] == ["pyefis/instruments/hsi"]
    # The pyEfis editor has not been built to write FPLIMPDONE yet.
    assert "FPLIMPDONE" not in reg


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


# The pyEfis CAS model's acknowledge (pyEfis instruments/cas/model.py, #552):
# an item from an injected lookup, behind a caching method, sent with
# output_value().  fix-gateway's CI has no pyEfis checkout, so the shape is
# copied here.
CAS_MODEL = """
    ACK_KEY = "CASACK"

    class CASModel:
        def __init__(self, lookup):
            self.lookup = lookup
            self._items = {}

        def _item(self, key):
            item = self._items.get(key)
            if item is None:
                item = self.lookup(key)
                self._items[key] = item
            return item

        def acknowledge(self, seq):
            item = self._item(ACK_KEY)
            item.value = seq
            item.output_value()
"""


def test_the_cas_contract_is_in_the_register(catalog, tmp_path):
    # OUT1 (AER-6521): every CAS key has its writers recorded, so ORG-018's
    # ratchet sees a second writer of any of them.
    seen = {(w["key"], w["module"]) for w in ckw.scan_writes(ROOT, catalog)}
    slots = ["CASMSG%d" % n for n in range(1, 13)]
    assert {k for k, m in seen if m == "cas" and k.startswith("CASMSG")} == set(slots)
    pyefis = tree(tmp_path, {"src/pyefis/instruments/cas/model.py": CAS_MODEL})
    assert {(w["key"], w["module"], w["how"]) for w in ckw.scan_writes(pyefis, catalog, repo="pyEfis")} == \
        {("CASACK", "pyefis/instruments/cas", "item.value ="),
         ("CASACK", "pyefis/instruments/cas", "item.output_value()")}
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)
    for k in slots + ["CASCNT", "CASTOP", "CASSEQ"]:
        assert reg[k]["writers"] == ["cas"], k
    assert reg["CASACK"]["pyefis"] == ["pyefis/instruments/cas"]
    assert "rpi_button" in reg["CASACKBTN"]["no_writer"]
    assert not any(reg["CASACKBTN"].get(f) for f in ckw.REGISTER_LISTS)
    # And the ratchet now sees a second writer: a plugin that also wrote
    # CASACK would make it a two-writer key that is not on the baseline.
    two = dict(reg, CASACK=dict(reg["CASACK"], writers=["aaa"]))
    assert any(f.startswith("CASACK: 2 live writers (aaa, pyefis/instruments/cas)")
               for f in ckw.check_baseline(two, ckw.load_yaml(ROOT, ckw.BASELINE)))


def test_a_loop_over_a_key_table_is_a_write(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": """
        OUT = ["VS", "HEAD"]
        BOTH = OUT + list(("BARO",))
        READ = ["ALT"]
        def run(parent, vals):
            for key, v in zip(OUT, vals):
                parent.db_write(key, v)
            for key in READ:
                parent.db_read(key)
            for i, key in enumerate(BOTH):
                parent.db_write(key, i)
            parent.db_write(key, 0)
    """})
    assert sorted((w["key"], w["line"]) for w in ckw.scan_writes(root, CAT)) == \
        [("BARO", 11), ("HEAD", 7), ("HEAD", 11), ("VS", 7), ("VS", 11)]


def test_output_value_on_a_fetched_item_is_a_write(tmp_path):
    root = tree(tmp_path, {"src/pyefis/instruments/knob/__init__.py": """
        def turn(db, v):
            item = db.get_item("BARO")
            item.output_value()
            other = make("VS")
            other.output_value()
    """})
    # make() is not known to return an item, so VS is not claimed.
    assert [(w["key"], w["how"]) for w in ckw.scan_writes(root, CAT, repo="pyEfis")] == \
        [("BARO", "item.output_value()")]


def test_a_declared_key_with_no_writer_needs_a_reason(tmp_path):
    root = tree(tmp_path, {"src/fixgw/plugins/aaa/__init__.py": plugin("VS")})
    reg = {"VS": {"units": "ft", "writers": ["aaa"]}, "HEAD": {"units": "deg"}}
    assert rules(root, reg, {}) == ["HEAD: writers.yaml lists no writer; remove the entry, or "
                                    "say why it has none in `no_writer`"]
    reg["HEAD"]["no_writer"] = "a panel button's key; no shipped config wires one"
    assert rules(root, reg, {}) == []
    # Once something writes it, the reason is stale.
    with open(os.path.join(root, "src/fixgw/plugins/aaa/__init__.py"), "a") as f:
        f.write('    parent.db_write("HEAD", 1)\n')
    reg["HEAD"]["writers"] = ["aaa"]
    assert rules(root, reg, {}) == ["HEAD: writers.yaml says `no_writer`, and aaa writes it; "
                                    "remove `no_writer`"]


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


# ---- pyEfis's item helpers (ARCHG2b, AER-6541) -----------------------------
# Shapes copied from pyEfis `dev` (fix-gateway's CI has no pyEfis checkout):
# the flight plan editor's bridge (flightplan/fixbridge.py), a widget's
# BugBinding (instruments/ai/ai_widget.py) and an editor over a binding it is
# handed (instruments/altimeter/altsel.py).
CAT_UI = dict(CAT_FPL, **{k: {"key": k, "units": "", "type": "float", "description": k}
                          for k in ("DTOID", "FPLS1KIND", "FPLS2KIND", "FPLS1ID", "FPLS2ID",
                                    "ALTSEL", "BANKBUG", "HEADBUG")})

FIXBRIDGE = """
    SLOT_FIELDS = (("ID", "str", ""), ("LAT", "float", 0.0))
    SEGMENT_FIELDS = (("KIND", "int", 0), ("ID", "str", ""))


    def _slot_key(n, name):
        return f"FPL{n}{name}"


    def _seg_key(k, name):
        return "FPLS%d%s" % (k, name)


    class FixBridge:
        def _get(self, key):
            db = self._fix.db
            item = self._items.get(key)
            if item is None:
                item = self._items[key] = db.get_item(key)
            return item

        def _set(self, key, value):
            item = self._get(key)
            item.value = value
            item.output_value()

        def publish(self, plan):
            for i in range(1, len(plan) + 1):
                for name, _, blank in SLOT_FIELDS:
                    self._set(_slot_key(i, name), blank)
            for k in range(1, 3):
                for name, _, _ in SEGMENT_FIELDS:
                    self._set(_seg_key(k, name), plan)
            self._set("FPLSEQ", int(self._get("FPLSEQ").value) + 1)
            self._set("DTOID", "KSFO")
"""


def pyefis_writes(root, cat=CAT_UI):
    return sorted({(w["key"], w["module"], w["how"])
                   for w in ckw.scan_writes(root, cat, repo="pyEfis")})


def test_a_write_through_a_method_that_returns_the_item_is_a_write(tmp_path):
    # fixbridge's _set(key, v) gets its item from self._get(key), not from
    # get_item(); the scan saw none of the editor's literal writes.
    root = tree(tmp_path, {"src/pyefis/flightplan/fixbridge.py": FIXBRIDGE})
    lit = {(k, h) for k, _, h in pyefis_writes(root) if "literal" in h}
    assert lit == {("FPLSEQ", "_set(literal)"), ("DTOID", "_set(literal)")}


def test_a_key_built_by_a_name_function_is_a_write(tmp_path):
    # _slot_key(i, name) builds FPL<i><name> for each name of SLOT_FIELDS.
    root = tree(tmp_path, {"src/pyefis/flightplan/fixbridge.py": FIXBRIDGE})
    built = {k for k, _, h in pyefis_writes(root) if "literal" not in h}
    assert built == {"FPL1ID", "FPL2ID", "FPL10ID", "FPL1LAT", "FPL2LAT", "FPL10LAT",
                     "FPLS1KIND", "FPLS2KIND", "FPLS1ID", "FPLS2ID"}
    # FPL<n>ALT is not a slot field here, and FPLFRLAT is no slot at all.
    assert {h for k, _, h in pyefis_writes(root) if k == "FPL1ID"} == \
        {"_set(_slot_key() f-string FPL{n}{name})"}


def test_an_item_from_a_binding_the_module_makes_is_a_write(tmp_path):
    root = tree(tmp_path, {"src/pyefis/instruments/ai/ai_widget.py": """
        class AI:
            def __init__(self):
                self._bank_bug = bugs.BugBinding("BANKBUG", self)
                self._other = bugs.BugBinding("HEADBUG", self)

            def step(self, v):
                item = self._bank_bug.item
                item.value = v
                item.output_value()
                return self._other.value
    """})
    assert pyefis_writes(root) == [
        ("BANKBUG", "pyefis/instruments/ai", "item.output_value()"),
        ("BANKBUG", "pyefis/instruments/ai", "item.value =")]


EDITOR = """
    class AltSelEditor:
        def __init__(self, binding):
            self.binding = binding

        def write(self, value):
            item = self.binding.item
            item.value = value
            %s
"""


def test_a_writes_comment_declares_an_item_handed_in_from_elsewhere(tmp_path):
    # The key comes from the panel config, through another module: the scan
    # cannot follow it, so the write says which keys it reaches.
    root = tree(tmp_path, {
        "src/pyefis/instruments/altimeter/altsel.py":
            EDITOR % "item.output_value()  # writes: ALTSEL",
        "src/pyefis/instruments/autopilot/__init__.py":
            EDITOR % "# writes: HEADBUG, ALTSEL\n            item.output_value()"})
    assert pyefis_writes(root) == [
        ("ALTSEL", "pyefis/instruments/altimeter", "item.output_value() (# writes:)"),
        ("ALTSEL", "pyefis/instruments/autopilot", "item.output_value() (# writes:)"),
        ("HEADBUG", "pyefis/instruments/autopilot", "item.output_value() (# writes:)")]


@pytest.mark.parametrize("line, error", [
    ("item.output_value()  # writes: ALTSEL, NOSUCHKEY",
     "NOSUCHKEY is not a key in the database"),
    ("self.update()  # writes: ALTSEL", "is on no write"),
    ("item = db.get_item('ALTSEL'); item.output_value()  # writes: ALTSEL",
     "the scan already names the key"),
])
def test_a_writes_comment_must_name_keys_on_a_write_the_scan_cannot_name(tmp_path, line, error):
    root = tree(tmp_path, {"src/pyefis/instruments/altimeter/altsel.py": EDITOR % line})
    with pytest.raises(ckw.ScanError, match=error) as e:
        ckw.scan_writes(root, CAT_UI, repo="pyEfis")
    assert "src/pyefis/instruments/altimeter/altsel.py:9" in str(e.value)


def test_the_register_records_the_pyefis_item_helper_writes():
    # ARCHG2b (AER-6541), on the real register: what the scan finds in pyEfis
    # `dev` through fixbridge's _set, a BugBinding and `# writes:`.
    reg = ckw.load_yaml(ROOT, ckw.REGISTER)
    pyefis = {k: e["pyefis"] for k, e in reg.items() if e.get("pyefis")}
    assert pyefis["ALTSEL"] == ["pyefis/instruments/altimeter"]
    assert pyefis["IASSEL"] == ["pyefis/instruments/airspeed", "pyefis/instruments/autopilot"]
    assert pyefis["VSSEL"] == ["pyefis/instruments/autopilot"]
    assert pyefis["HEADBUG"] == ["pyefis/instruments/autopilot", "pyefis/instruments/hsi"]
    assert pyefis["NAV1CRS"] == pyefis["NAV2CRS"] == ["pyefis/instruments/hsi"]
    assert pyefis["BANKBUG"] == ["pyefis/instruments/ai"]
    # X-Plane's autopilot adapter copies its dials into the same keys, from
    # its TARGETS table: a sim writer, so not a second live writer.
    for k in ("ALTSEL", "VSSEL", "IASSEL", "HEADBUG", "NAV1CRS", "NAV2CRS", "BARO"):
        assert "xplane" in reg[k]["sim"], k
    # Two pyEfis surfaces edit one pilot-set key: on the baseline, by Bill's
    # ruling on the ARCHG2b packet (AER-6493), until a selector item says
    # otherwise.
    base = ckw.load_yaml(ROOT, ckw.BASELINE)
    for k in ("FPLCMD", "HEADBUG", "IASSEL"):
        assert base[k] == pyefis[k], k
