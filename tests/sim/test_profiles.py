"""Every committed profile flown headless, its checks evaluated (SYN3).

A new profile in ``src/fixgw/sim/profiles/`` is picked up here and in the
"Simulated flights" CI job with no other change. See ``profile_gate.py``.
A profile on QA's open-findings list is a strict expected failure.
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
    run = profile_gate.fly(path, OPEN)
    if run.finding is not None:
        assert not run.passed, profile_gate.xpass_line(run)
        pytest.xfail(run.finding)
    assert profile_gate.failure_lines(run) == []


def test_every_open_finding_names_a_committed_profile():
    assert profile_gate.unknown_findings(OPEN, PROFILES) == []


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


# -- QA's open findings: strict expected failures -------------------------------

def _run(passed, finding):
    results = [checks.CheckResult("A vs B (equal)", True),
               _result(label="C vs D (+-1)", compared=2, failed=1, max_err=2.0, t_max=1.5, detail="t=1.5s C=3 D=1")]
    return profile_gate.ProfileRun("x", profile_gate.PROFILES_DIR / "x.yaml", [{}, {}],
                                   results[:1] if passed else results, [40, 43], 10, 2.0, 0.1, finding)


def test_a_listed_profile_that_fails_is_reported_and_does_not_fail_the_gate():
    run = _run(passed=False, finding="F1: C is off")
    assert (run.status, run.ok) == ("XFAIL", True)
    assert profile_gate.github_errors(run) == [
        "::warning file=src/fixgw/sim/profiles/x.yaml,line=43,title=Simulated flight x (open finding)::"
        "x: C vs D (+-1) FAILED on 1 of 2 rows; first at t=1.5s C=3 D=1; max error 2.0000 at t=1.5s"]


def test_a_listed_profile_that_passes_fails_the_gate():
    run = _run(passed=True, finding="F1: C is off")
    assert (run.status, run.ok) == ("XPASS", False)
    [err] = profile_gate.github_errors(run)
    assert err.startswith("::error file=src/fixgw/sim/profiles/x.yaml,title=Simulated flight x (open finding)::")
    assert "remove it from tests/sim/open_findings.yaml" in err


def test_an_unlisted_profile_is_judged_on_its_checks_alone():
    assert (_run(True, None).status, _run(True, None).ok) == ("PASS", True)
    assert (_run(False, None).status, _run(False, None).ok) == ("FAIL", False)


def test_an_entry_without_a_profile_is_stale():
    assert profile_gate.unknown_findings({"gone": "F9", "x": "F1"}, [profile_gate.PROFILES_DIR / "x.yaml"]) == ["gone"]


def test_the_findings_list_is_a_mapping_of_name_to_finding(tmp_path):
    p = tmp_path / "f.yaml"
    p.write_text("- qa1-x\n")
    with pytest.raises(ValueError, match="mapping of profile name -> finding"):
        profile_gate.open_findings(p)
    p.write_text("")
    assert profile_gate.open_findings(p) == {}


def test_the_gate_goes_red_when_a_listed_profile_passes(tmp_path, capsys):
    # End to end through main(): fp8 passes every check, so listing it as an
    # open finding must fail the run.
    fp8 = profile_gate.PROFILES_DIR / "fp8-ksmx-rnav30-missed.yaml"
    findings = tmp_path / "open_findings.yaml"
    findings.write_text("fp8-ksmx-rnav30-missed: F0 for the test\n")
    assert profile_gate.main([str(fp8), "--findings", str(findings)]) == 1
    assert "XPASS fp8-ksmx-rnav30-missed" in capsys.readouterr().out
    findings.write_text("")
    assert profile_gate.main([str(fp8), "--findings", str(findings)]) == 0
