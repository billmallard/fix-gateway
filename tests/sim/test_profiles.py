"""Every committed profile flown headless, its checks evaluated (SYN3).

A new profile in ``src/fixgw/sim/profiles/`` is picked up here and in the
"Simulated flights" CI job with no other change. See ``profile_gate.py``.
"""

import pytest

from fixgw.sim import checks

from . import profile_gate

PROFILES = profile_gate.committed_profiles()
OPEN = profile_gate.open_findings()


def test_there_is_a_profile_to_fly():
    # A gate with nothing to fly would pass forever.
    assert PROFILES


@pytest.mark.parametrize("path", PROFILES, ids=[p.stem for p in PROFILES])
def test_every_check_in_the_profile_passes(path):
    run = profile_gate.fly(path)
    if path.stem in OPEN:
        assert not run.passed, f"{path.stem} passes now -- close the open finding"
        pytest.xfail(OPEN[path.stem])
    assert profile_gate.failure_lines(run) == []


def test_every_open_finding_names_a_committed_profile():
    assert set(OPEN) <= {p.stem for p in PROFILES}


def test_open_findings_are_read_from_the_owning_test_module(tmp_path):
    src = tmp_path / "test_x.py"
    src.write_text('import pytest\nOPEN = {"a": "reason a",\n        "b": "reason " "b"}\nOTHER = {"c": 1}\n')
    assert profile_gate.open_findings([src]) == {"a": "reason a", "b": "reason b"}


def _run(name, passed):
    r = checks.CheckResult("A vs B (equal)", passed)
    return profile_gate.ProfileRun(name, profile_gate.PROFILES_DIR / f"{name}.yaml", [{}], [r], [1], 1, 1.0, 0.1)


def test_an_open_finding_passes_the_gate_only_while_it_still_fails():
    open_ = {"x": "known"}
    assert profile_gate.verdict(_run("x", False), open_) == "XFAIL"
    assert profile_gate.verdict(_run("x", True), open_) == "XPASS"
    assert profile_gate.verdict(_run("y", False), open_) == "FAIL"
    assert profile_gate.verdict(_run("y", True), open_) == "PASS"


@pytest.mark.parametrize("listed, expected", [("fp8-ksmx-rnav30-missed", 1), ("not-a-profile", 0)])
def test_a_listed_profile_that_passes_fails_the_gate(monkeypatch, listed, expected):
    # fp8 passes, so listing it as an open finding must turn the gate red.
    # With explicit paths, a stale entry for another profile is not judged.
    monkeypatch.setattr(profile_gate, "open_findings", lambda: {listed: "test"})
    fp8 = profile_gate.PROFILES_DIR / "fp8-ksmx-rnav30-missed.yaml"
    assert profile_gate.main([str(fp8)]) == expected


def _result(**kw):
    r = checks.CheckResult(kw.pop("label"), False)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_a_failing_pair_names_the_profile_keys_tolerance_and_time():
    r = _result(label="FPLCRS vs REFCRS (+-0.5 deg)", compared=4000, failed=3, max_err=0.71, t_max=812.4,
                first_fail_t=810.2, detail="t=810.2s FPLCRS=301.2 REFCRS=300.6")
    assert profile_gate.failure_line("fp8", r) == (
        "fp8: FPLCRS vs REFCRS (+-0.5 deg) FAILED on 3 of 4000 rows; "
        "first at t=810.2s FPLCRS=301.2 REFCRS=300.6; max error 0.7100 at t=812.4s")


def test_a_check_that_compared_nothing_fails_and_says_so():
    r = _result(label="WPDIS vs REFDIS (+-0.015)", detail="nothing compared")
    assert profile_gate.failure_line("fp8", r) == "fp8: WPDIS vs REFDIS (+-0.015) FAILED: nothing compared"


def test_annotations_point_at_the_check_in_the_profile(tmp_path):
    # The line comes from the profile file, so a reviewer lands on the check.
    p = profile_gate.PROFILES_DIR / "x.yaml"
    results = [checks.CheckResult("A vs B (equal)", True),
               _result(label="C vs D (+-1)", compared=2, failed=1, max_err=2.0, t_max=1.5, detail="t=1.5s C=3 D=1")]
    run = profile_gate.ProfileRun("x", p, [{}, {}], results, [40, 43], 10, 2.0, 0.1)
    assert profile_gate.github_errors(run) == [
        "::error file=src/fixgw/sim/profiles/x.yaml,line=43,title=Simulated flight x::"
        "x: C vs D (+-1) FAILED on 1 of 2 rows; first at t=1.5s C=3 D=1; max error 2.0000 at t=1.5s"]
