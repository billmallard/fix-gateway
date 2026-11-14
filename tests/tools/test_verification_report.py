#  SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for tools/verification_report.py (TV11, testing_visibility_plan.md section 7).

The generator publishes to a public page, so the hygiene and wording rules are pinned
here rather than trusted: the sanitizer, the leak guard that refuses to write a site,
the banned-words rule over the page's own wording, and the deviation-register states.
Everything runs on a small hand-built JUnit file and test tree; no Qt."""

import importlib.util
import json
import re
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("verification_report", _ROOT / "tools" / "verification_report.py")
vr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vr)

TEST_SRC = textwrap.dedent('''
    import pytest

    def test_bank():
        """XX-TC-101 | XX-BANK-001 | Bank alert past the threshold."""

    def test_untraced():
        """No requirement here."""

    @pytest.mark.xfail(strict=True, reason="XX-LIM-001 data-blocked: no AOA key at 203.0.113.5")
    def test_limit():
        """XX-LIM-001 | pitch limit marker."""

    @pytest.mark.xfail(strict=True, reason="XX-GAP-002 not built yet")
    def test_gap_closed():
        """XX-GAP-002 | lands one day."""

    class TestGroup:
        def test_method(self):
            """XX-BANK-001 | also covers bank."""
''')

SPEC_MD = textwrap.dedent('''
    | ID | Requirement |
    |---|---|
    | **XX-BANK-001** | Excessive-bank annunciation |
    | **XX-LIM-001** | Pitch-limit indication |
    | **XX-NONE-001** | Defined but nothing tests it |
    - **XX-GAP-002:** The gap feature shall exist.
''')

JUNIT = textwrap.dedent('''<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
 <testcase classname="tests.test_demo" name="test_bank" file="tests/test_demo.py" line="3" time="0.01"/>
 <testcase classname="tests.test_demo" name="test_untraced" file="tests/test_demo.py" line="6" time="0.01">
  <failure message="bad /home/runner/work/pyEfis/x.py ssh pi@192.0.2.10 C:\\pylib\\foo.py bench.local">trace</failure>
 </testcase>
 <testcase classname="tests.test_demo" name="test_limit" file="tests/test_demo.py" line="10" time="0.01">
  <skipped type="pytest.xfail" message="XX-LIM-001 data-blocked"/>
 </testcase>
 <testcase classname="tests.test_demo" name="test_gap_closed" file="tests/test_demo.py" line="14" time="0.01">
  <failure message="[XPASS(strict)] XX-GAP-002 not built yet"/>
 </testcase>
 <testcase classname="tests.test_demo.TestGroup" name="test_method" file="tests/test_demo.py" line="19" time="0.01"/>
</testsuite></testsuites>
''')


@pytest.fixture
def record(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_demo.py").write_text(TEST_SRC)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "xx_spec.md").write_text(SPEC_MD)
    (tmp_path / "junit.xml").write_text(JUNIT)
    rc = vr.main(["collect", "--junit", "junit.xml", "--tests", "tests", "--docs", "docs",
                  "--repo", "billmallard/demo", "--sha", "abc1234def", "--ref", "dev",
                  "--run-url", "https://github.com/billmallard/demo/actions/runs/9", "--out", "out/rec.json"])
    assert rc == 0
    return json.loads((tmp_path / "out" / "rec.json").read_text())


def _by_name(rec, name):
    return next(t for t in rec["tests"] if t["nodeid"].endswith(name))


# ---------------------------------------------------------------- collect

def test_requirement_ids_come_from_docstrings_and_specs(record):
    assert _by_name(record, "test_bank")["ids"] == ["XX-BANK-001", "XX-TC-101"]
    assert _by_name(record, "test_untraced")["ids"] == []
    assert set(record["requirements"]) == {"XX-BANK-001", "XX-LIM-001", "XX-NONE-001", "XX-GAP-002"}
    assert record["requirements"]["XX-GAP-002"]["title"] == "The gap feature shall exist."


def test_class_methods_map_to_their_source_line(record):
    t = _by_name(record, "TestGroup::test_method")
    assert t["ids"] == ["XX-BANK-001"]
    assert t["line"] == 19


def test_outcomes_include_strict_xfail_states(record):
    assert _by_name(record, "test_limit")["outcome"] == "xfailed"
    assert _by_name(record, "test_limit")["strict_xfail"] is True
    assert _by_name(record, "test_gap_closed")["outcome"] == "xpassed_strict"
    assert record["counts"] == {"passed": 2, "failed": 1, "xfailed": 1, "xpassed_strict": 1}


def test_run_text_is_sanitized_at_collect(record):
    msg = _by_name(record, "test_untraced")["message"]
    reason = _by_name(record, "test_limit")["xfail_reason"]
    for leaked in ("/home/runner", "192.0.2.10", "pi@", "C:\\pylib", "bench.local", "203.0.113.5"):
        assert leaked not in msg + reason
    assert "<user-at-host>" in msg and "<ip>" in reason


# ---------------------------------------------------------------- analysis

def test_requirement_rows_roll_up_results(record):
    rows = {r["id"]: r for r in vr.requirement_rows(record)}
    assert rows["XX-BANK-001"]["result"] == "pass"
    assert len(rows["XX-BANK-001"]["tests"]) == 2
    assert rows["XX-LIM-001"]["result"] == "open deviation"
    assert rows["XX-GAP-002"]["result"] == "fail"
    assert rows["XX-NONE-001"]["result"] == "no test cites it"
    assert "XX-TC-101" not in rows, "test-case IDs are not requirements"


def test_deviation_register_states(record):
    devs = {d["test"]["nodeid"].split("::")[-1]: d for d in vr.deviation_rows(record)}
    assert set(devs) == {"test_limit", "test_gap_closed"}
    assert devs["test_limit"]["state"].startswith("open")
    assert "remove the tracker" in devs["test_gap_closed"]["state"]


def test_blob_url_pins_the_commit(record):
    url = vr.blob_url(record, _by_name(record, "test_bank"))
    assert url == "https://github.com/billmallard/demo/blob/abc1234def/tests/test_demo.py#L4"


# ---------------------------------------------------------------- summary

def test_summary_lists_requirement_tests_and_trackers(record, tmp_path, capsys):
    assert vr.main(["summary", str(tmp_path / "out" / "rec.json")]) == 0
    md = capsys.readouterr().out
    assert "| `XX-BANK-001` | pass |" in md
    assert "Strict-xfail gap trackers" in md
    assert "test_limit" in md and "test_gap_closed" in md
    assert "not a warranty and not a certification" in md


def test_summary_empty_states(tmp_path, capsys):
    rec = {"name": "fix-gateway", "repo": "billmallard/fix-gateway", "ref": "dev", "sha": "0" * 40,
           "counts": {"passed": 1}, "total": 1, "requirements": {},
           "tests": [{"nodeid": "tests/t.py::test_a", "file": "tests/t.py", "line": 1, "summary": "",
                      "ids": [], "outcome": "passed", "message": "", "strict_xfail": False,
                      "xfail_reason": "", "time": 0}]}
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rec))
    vr.main(["summary", str(p)])
    md = capsys.readouterr().out
    assert "No requirement IDs are cited" in md
    assert "No strict-xfail gap trackers" in md


# ---------------------------------------------------------------- page

def _page_text(record, tmp_path, **kw):
    rec = tmp_path / "out" / "rec.json"
    assert vr.main(["page", str(rec), "--expect", "demo", "fix-gateway", "--out", str(tmp_path / "site")]) == 0
    return (tmp_path / "site" / "index.html").read_text()


def test_page_carries_standing_text_and_empty_states(record, tmp_path):
    page = _page_text(record, tmp_path)
    assert vr.STANDING_TEXT.replace("'", "&#x27;") in page
    assert "any use of it is at your own risk" in page
    assert "No hardware reports are published yet" in page
    assert "No simulator results are published yet" in page
    assert "<b>fix-gateway</b>: no published result yet" in page
    assert (tmp_path / "site" / "demo.json").is_file()


def test_notice_is_the_box_alone_with_no_reliance_clause(record, tmp_path):
    # Bill, 2026-11-01 (AER-4531): no h2 over the notice box, and no "primary
    # flight or navigation reference" clause -- own-risk wording only.
    page = _page_text(record, tmp_path)
    assert "<h2 id=notice>" not in page
    assert "What this page is and is not" not in page
    assert "primary" not in vr.STANDING_TEXT.lower()


def test_page_never_uses_banned_words_outside_the_standing_text(record, tmp_path):
    page = _page_text(record, tmp_path)
    body = re.sub(r'<div class=notice id=standing-text>.*?</div>', "", page, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", body).lower()
    for word in vr.BANNED_WORDS:
        assert not re.search(rf"\b{word}\b", text), word


def test_page_refuses_to_publish_a_leak(record, tmp_path):
    rec_path = tmp_path / "out" / "rec.json"
    rec = json.loads(rec_path.read_text())
    rec["tests"][0]["summary"] = "ran on 192.0.2.20"   # bypasses collect's sanitizer
    rec_path.write_text(json.dumps(rec))
    assert vr.main(["page", str(rec_path), "--out", str(tmp_path / "site")]) == 2
    assert not (tmp_path / "site" / "index.html").exists()


@pytest.mark.parametrize("raw, gone", [
    ("see /home/someuser/x/y.py", "/home/someuser"),
    ("D:\\Users\\someuser\\Documents\\x.py", "someuser"),
    ("ssh someuser@benchbox", "someuser@"),
    ("http://198.51.100.7:8080/bench", "198.51.100.7"),
    ("http://tracker.internal/issues/AER-1", "tracker.internal"),
], ids=["posix-path", "windows-path", "user-at-host", "ip-url", "private-host"])
def test_sanitize_strips_paths_addresses_and_hosts(raw, gone):
    out = vr.sanitize(raw)
    assert gone not in out
    assert not vr.find_leaks(out)


def test_sanitize_keeps_public_links_and_bare_ids():
    text = "see https://github.com/billmallard/pyEfis/issues/84 and AER-1149, AC 25-11B p.47"
    assert vr.sanitize(text) == text
