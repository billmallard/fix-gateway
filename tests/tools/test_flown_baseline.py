#  SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for tools/flown_baseline.py (CERT7): the tag name, the pairing with the other
repo's baseline, the safety-problem list and the lock check. Network reads are not
exercised; everything here runs on literal git and pip output. No Qt."""

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("flown_baseline", _ROOT / "tools" / "flown_baseline.py")
fb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fb)

A = "a" * 40
B = "b" * 40
C = "c" * 40
T = "f" * 40

LS_REMOTE = f"""\
{A}\trefs/tags/perf-fixtures-2026-09
{T}\trefs/tags/baseline-2026-11-10
{B}\trefs/tags/baseline-2026-11-10^{{}}
{C}\trefs/tags/baseline-2026-10-06
"""


def test_ls_remote_annotated_tag_resolves_to_its_commit():
    tags = fb.parse_ls_remote(LS_REMOTE)
    # The tag object's own sha (T) must not be mistaken for the commit (B).
    assert tags["baseline-2026-11-10"] == B
    assert tags["baseline-2026-10-06"] == C
    assert tags["perf-fixtures-2026-09"] == A


def test_ls_remote_ignores_branches_and_noise():
    assert fb.parse_ls_remote(f"{A}\trefs/heads/master\n\nwarning: x\n") == {}


def test_tag_name_first_and_same_day_suffixes():
    assert fb.pick_tag_name("2026-11-10", {}) == "baseline-2026-11-10"
    existing = {"baseline-2026-11-10": A, "baseline-2026-11-10-2": B}
    assert fb.pick_tag_name("2026-11-10", existing) == "baseline-2026-11-10-3"


def test_tag_name_rejects_a_bad_date():
    with pytest.raises(ValueError):
        fb.pick_tag_name("2026-11-1", {})


def test_pair_same_name_wins_even_off_master():
    # The other repo was promoted today and has since moved on: its same-day tag is
    # still the pair, not whatever its master is now.
    tags = {"baseline-2026-11-10": B}
    assert fb.resolve_pair("baseline-2026-11-10", tags, A) == ("same-name", "baseline-2026-11-10", B)


def test_pair_falls_back_to_the_baseline_on_master():
    tags = {"baseline-2026-10-06": C, "perf-fixtures-2026-09": C}
    assert fb.resolve_pair("baseline-2026-11-10", tags, C) == ("master-tag", "baseline-2026-10-06", C)


def test_pair_untagged_master_is_said_plainly():
    kind, tag, sha = fb.resolve_pair("baseline-2026-11-10", {"perf-fixtures-2026-09": A}, A)
    assert (kind, tag, sha) == ("untagged", None, A)
    line = fb.pair_line("billmallard/fix-gateway", "baseline-2026-11-10", kind, tag, sha)
    assert "no baseline tag" in line and "baseline-2026-11-10" in line and A[:12] in line


def test_pair_lines_name_the_tag_and_commit():
    line = fb.pair_line("billmallard/pyEfis", "baseline-2026-11-10", "same-name", "baseline-2026-11-10", B)
    assert line.startswith("Flown with billmallard/pyEfis baseline-2026-11-10 (" + B[:12])
    # A confirmed pair makes no promise about a later promotion.
    assert "later today" not in line
    line = fb.pair_line("billmallard/pyEfis", "baseline-2026-11-10", "master-tag", "baseline-2026-10-06", C)
    assert "baseline-2026-10-06" in line and "later today its tag will be baseline-2026-11-10" in line


def _issue(n, labels):
    return {"number": n, "title": f"issue {n}", "url": f"https://x/{n}", "labels": labels}


def test_safety_lists_problems_sorted_with_their_labels():
    md = fb.safety_markdown(
        {"r/a": [_issue(9, ["bug", "safety:loss"]), _issue(3, ["safety:misleading"])], "r/b": []},
        {"r/a": 4, "r/b": 0})
    assert "2 open issue(s)" in md
    assert md.index("r/a#3") < md.index("r/a#9")
    assert "r/a#9 `safety:loss`" in md
    assert "r/a 4, r/b 0" in md


def test_safety_none_open_says_so():
    md = fb.safety_markdown({"r/a": []}, {"r/a": 0})
    assert "No open issue" in md


def test_lockcheck_counts_matches_and_names_differences():
    lock = "PyYAML==6.0.1\nnumpy==2.1.0\npyavtools @ git+https://g/x@abc\nextra==1.0\n"
    tested = "-e git+https://g/pyefis@1#egg=pyefis\npyyaml==6.0.1\nnumpy==2.1.1\npyavtools @ git+https://g/x@abc\n"
    md = fb.lockcheck_markdown(lock, tested)
    # PyYAML vs pyyaml is the same pin; only the editable pyefis line is skipped.
    assert md.startswith("2 of 4 locked packages")
    assert "differs: `numpy==2.1.0` locked, `numpy==2.1.1` tested" in md
    assert "not in the tested environment: `extra==1.0`" in md


def test_lockcheck_all_match():
    lock = "a==1\nb==2\n"
    assert fb.lockcheck_markdown(lock, "b==2\na==1\nc==3\n").startswith("2 of 2 locked")
