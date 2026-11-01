#!/usr/bin/env python3
#  SPDX-License-Identifier: GPL-2.0-or-later
"""Turn a pytest run into a test-evidence record, a CI job summary and a static page.

Three steps, one record. ``collect`` reads the JUnit XML pytest wrote plus the test
sources (and, optionally, the spec documents that define requirement IDs) and writes
``verification.json``. ``summary`` renders that JSON as Markdown for
``$GITHUB_STEP_SUMMARY``. ``page`` renders one or more of those JSON files (pyEfis and
fix-gateway) as a self-contained static HTML page for Cloudflare Pages. JSON is the
record; everything else is rendered from it.

Standard library only, so the page job does not need to install the application.

The canonical copy lives in billmallard/pyEfis ``tools/verification_report.py``;
billmallard/fix-gateway carries an identical copy for its own ``collect`` and
``summary`` steps. Change both together.

Hygiene: every string that came from a test run or a source file passes through
``sanitize`` (local paths, IP addresses, user@host and private hostnames removed),
and ``page`` refuses to write a site that still contains any of them.

Usage (CI):
    pytest --junitxml=reports/junit.xml -o junit_family=xunit1
    python tools/verification_report.py collect --junit reports/junit.xml --tests tests \
        --docs docs --repo billmallard/pyEfis --sha "$GITHUB_SHA" --ref dev \
        --run-url "$RUN_URL" --out reports/verification.json
    python tools/verification_report.py summary reports/verification.json >> "$GITHUB_STEP_SUMMARY"
    python tools/verification_report.py page reports/verification.json fgw.json --out site
"""

import argparse
import ast
import datetime
import html
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

SCHEMA = "test-evidence/1"

# A requirement or test-case ID: EFIS-TAS-001, AI-BANK-001, HSI-TC-101.
RID = re.compile(r"\b[A-Z]{2,6}(?:-[A-Z]{2,7})?-[A-Z]{2,7}-\d{3}\b")

# Standing text, testing_visibility_plan.md 4.7 (D3), adapted from a data pack to software.
STANDING_TEXT = (
    "These are the automated tests we ran on this software, what each one exercises, "
    "and its last result. It is evidence, not a warranty and not a certification. "
    "No authority has reviewed or approved this software or these tests. A passing "
    "test does not mean the software is correct, complete or fit for any purpose. "
    "This software is for Experimental / Amateur-Built use. It is provided as-is with "
    "no warranty, and any use of it is at your own risk."
)

# Words the page never uses about a result (plan 3.1, 4.7). Checked over the page's own
# wording by tests/test_verification_report.py; the standing text is exempt.
BANNED_WORDS = ("verified", "certified", "approved", "safe", "compliant", "airworthy")

ALLOWED_URL_HOSTS = ("github.com", "githubusercontent.com", "faa.gov", "pages.dev")

# --------------------------------------------------------------------------- hygiene

_URL = re.compile(r"\bhttps?://[^\s<>\"')\]]+")
_WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s:\"'<>|]+[\\/])*([^\s:\"'<>|\\/]*)")
_POSIX_PATH = re.compile(
    r"(?<![\w.])/(?:home|Users|root|tmp|var|opt|srv|mnt|media|data|paperclip|maos-work|usr|etc|run)"
    r"(?:/[^\s:\"'<>(),]+)*"
)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b")
_USER_AT_HOST = re.compile(r"\b[\w.-]+@[\w-]+(?:\.[\w-]+)*\b")
_PRIVATE_HOST = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:local|lan|internal|home|corp|localdomain)\b", re.I)

# Patterns the finished site must not contain. A match aborts ``page``.
LEAK_PATTERNS = (_IPV4, _USER_AT_HOST, _PRIVATE_HOST, _WIN_PATH,
                 re.compile(r"(?<![\w.])/(?:home|Users|root|paperclip|maos-work)/"))


def _url_ok(url):
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_URL_HOSTS)


def sanitize(text, repo_roots=()):
    """Strip local paths, addresses and hostnames from text that came from a run."""
    if not text:
        return text
    for root in sorted((r for r in repo_roots if r), key=len, reverse=True):
        text = text.replace(root.rstrip("/") + "/", "")
    text = _URL.sub(lambda m: m.group(0) if _url_ok(m.group(0)) else "<link removed>", text)
    text = _WIN_PATH.sub(lambda m: "<path>/" + m.group(1) if m.group(1) else "<path>", text)
    text = _POSIX_PATH.sub(lambda m: "<path>/" + m.group(0).rstrip("/").rsplit("/", 1)[-1], text)
    text = _USER_AT_HOST.sub("<user-at-host>", text)
    text = _IPV4.sub("<ip>", text)
    text = _IPV6.sub("<ip>", text)
    text = _PRIVATE_HOST.sub("<host>", text)
    return text


def find_leaks(text):
    leaks = []
    for pat in LEAK_PATTERNS:
        leaks += [m.group(0) for m in pat.finditer(text)]
    return leaks


# --------------------------------------------------------------------------- collect

def parse_requirements(docs_dir):
    """Requirement IDs defined in spec documents: ``- **ID:** text`` bullets and table rows
    whose first cell is an ID. Test-case IDs (``-TC-``) are catalogue entries, not
    requirements, and are left out."""
    defs = {}
    if not docs_dir:
        return defs
    root = pathlib.Path(docs_dir)
    for path in sorted(root.glob("*.md")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            ident = title = None
            m = re.match(r"^\s*[-*]\s+\*\*([A-Z][A-Z0-9-]+-\d{3}):?\*\*:?\s*(.*)$", line)
            if m:
                ident, title = m.group(1), m.group(2)
            elif line.lstrip().startswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                first = cells[0].strip("*` ") if cells else ""
                if RID.fullmatch(first) and len(cells) > 1:
                    ident, title = first, cells[1]
            if ident and "-TC-" not in ident and ident not in defs:
                title = re.sub(r"[*`]", "", title or "").strip()
                if len(title) > 160:
                    title = title[:157].rstrip() + "..."
                defs[ident] = {"title": sanitize(title), "doc": f"{root.name}/{path.name}"}
    return defs


def _decorator_xfail(dec):
    """(is_xfail, strict, reason) for a ``@pytest.mark.xfail(...)`` decorator node."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    name = ast.unparse(target)
    if not name.endswith("mark.xfail"):
        return False, False, ""
    strict, reason = False, ""
    if isinstance(dec, ast.Call):
        for kw in dec.keywords:
            if kw.arg == "strict" and isinstance(kw.value, ast.Constant):
                strict = bool(kw.value.value)
            if kw.arg == "reason":
                try:
                    reason = ast.literal_eval(kw.value)
                except ValueError:
                    reason = ast.unparse(kw.value)
        if dec.args and not reason:
            try:
                reason = str(ast.literal_eval(dec.args[0]))
            except ValueError:
                pass
    return True, strict, reason


def index_test_sources(tests_dir):
    """Map (relative file, class or None, function) -> line, summary, cited IDs, xfail info."""
    index = {}
    base = pathlib.Path(tests_dir)
    for path in sorted(base.rglob("test_*.py")):
        rel = path.as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        def visit(body, cls):
            for node in body:
                if isinstance(node, ast.ClassDef):
                    visit(node.body, node.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    doc = ast.get_docstring(node) or ""
                    text = doc + "\n" + "\n".join(ast.unparse(d) for d in node.decorator_list)
                    xfail, strict, reason = False, False, ""
                    for dec in node.decorator_list:
                        x, s, r = _decorator_xfail(dec)
                        if x:
                            xfail, strict, reason = True, s, r
                    summary = doc.strip().splitlines()[0] if doc.strip() else ""
                    index[(rel, cls, node.name)] = {
                        "line": node.lineno,
                        "summary": summary,
                        "ids": sorted(set(RID.findall(text))),
                        "xfail": xfail,
                        "strict": strict,
                        "reason": reason,
                    }

        visit(tree.body, None)
    return index


def _outcome(case):
    for child in case:
        tag = child.tag
        msg = child.get("message") or (child.text or "")
        if tag == "failure":
            return ("xpassed_strict" if "XPASS(strict)" in msg else "failed"), msg
        if tag == "error":
            return "error", msg
        if tag == "skipped":
            return ("xfailed" if child.get("type") == "pytest.xfail" else "skipped"), msg
    return "passed", ""


def _first_line(text, limit=300):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 3] + "..."


def collect(args):
    tree = ET.parse(args.junit)
    roots = [str(pathlib.Path.cwd()), str(pathlib.Path.cwd().resolve())]
    index = index_test_sources(args.tests)
    requirements = parse_requirements(args.docs)
    tests = []
    for case in tree.iter("testcase"):
        name = case.get("name", "")
        func = name.split("[", 1)[0]
        file_attr = (case.get("file") or "").replace("\\", "/")
        classname = case.get("classname", "")
        parts = classname.split(".")
        cls = parts[-1] if parts and parts[-1][:1].isupper() else None
        if not classname and "." in name:
            # A collection error: pytest reports the module as the name.
            file_attr = file_attr or name.replace(".", "/") + ".py"
            name = func = "(collection)"
        elif not file_attr:
            mod_parts = parts[:-1] if cls else parts
            file_attr = "/".join(mod_parts) + ".py"
        src = index.get((file_attr, cls, func), {})
        outcome, message = _outcome(case)
        line = src.get("line")
        if line is None and case.get("line") is not None:
            line = int(case.get("line")) + 1
        tests.append({
            "nodeid": sanitize(f"{file_attr}::{cls + '::' if cls else ''}{name}", roots),
            "file": file_attr,
            "line": line,
            "summary": sanitize(src.get("summary", ""), roots),
            "ids": src.get("ids", []),
            "outcome": outcome,
            "message": sanitize(_first_line(message), roots) if outcome not in ("passed",) else "",
            "strict_xfail": bool(src.get("strict")),
            "xfail_reason": sanitize(src.get("reason", ""), roots),
            "time": round(float(case.get("time") or 0.0), 3),
        })
    counts = {}
    for t in tests:
        counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
    record = {
        "schema": SCHEMA,
        "repo": args.repo,
        "name": args.repo.split("/")[-1],
        "ref": args.ref,
        "sha": args.sha,
        "run_url": args.run_url if args.run_url and _url_ok(args.run_url) else "",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
        "total": len(tests),
        "requirements": requirements,
        "tests": tests,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    return 0


# --------------------------------------------------------------------------- analysis

RESULT_WORDS = {
    "passed": "pass",
    "failed": "fail",
    "error": "error",
    "skipped": "skipped",
    "xfailed": "expected fail",
    "xpassed_strict": "unexpected pass",
}


def requirement_rows(record):
    """One row per requirement ID: defined in a spec, cited by a test, or both."""
    by_id = {}
    for t in record["tests"]:
        for ident in t["ids"]:
            if "-TC-" in ident:
                continue
            by_id.setdefault(ident, []).append(t)
    defs = record.get("requirements", {})
    rows = []
    for ident in sorted(set(defs) | set(by_id)):
        tests = by_id.get(ident, [])
        outcomes = {t["outcome"] for t in tests}
        if not tests:
            result = "no test cites it"
        elif outcomes & {"failed", "error", "xpassed_strict"}:
            result = "fail"
        elif outcomes == {"xfailed"} and all(t["strict_xfail"] for t in tests):
            result = "open deviation"
        elif "passed" in outcomes and outcomes <= {"passed", "skipped", "xfailed"}:
            result = "pass" if outcomes == {"passed"} else "pass, with open deviation" \
                if "xfailed" in outcomes else "pass (some skipped)"
        else:
            result = ", ".join(sorted(RESULT_WORDS.get(o, o) for o in outcomes))
        rows.append({
            "id": ident,
            "title": defs.get(ident, {}).get("title", ""),
            "doc": defs.get(ident, {}).get("doc", ""),
            "defined": ident in defs,
            "tests": tests,
            "result": result,
        })
    return rows


def deviation_rows(record):
    """The strict-xfail gap trackers: each is a documented, self-closing deviation."""
    rows = []
    for t in record["tests"]:
        if not t["strict_xfail"]:
            continue
        if t["outcome"] == "xfailed":
            state = "open (fails as expected)"
        elif t["outcome"] == "xpassed_strict":
            state = "closing: the feature now passes; remove the tracker"
        elif t["outcome"] == "skipped":
            state = "not run in this CI (skipped)"
        else:
            state = RESULT_WORDS.get(t["outcome"], t["outcome"])
        rows.append({"test": t, "state": state,
                     "ids": [i for i in t["ids"] if "-TC-" not in i]})
    return rows


def blob_url(record, test):
    if not (record.get("repo") and record.get("sha")):
        return ""
    anchor = f"#L{test['line']}" if test.get("line") else ""
    return f"https://github.com/{record['repo']}/blob/{record['sha']}/{test['file']}{anchor}"


def _counts_line(record):
    c = record["counts"]
    order = ("passed", "failed", "error", "xfailed", "xpassed_strict", "skipped")
    parts = [f"{c[k]} {RESULT_WORDS[k]}" for k in order if c.get(k)]
    return f"{record['total']} tests: " + ", ".join(parts) if parts else "no tests"


# --------------------------------------------------------------------------- summary

def _md(text):
    return (text or "").replace("|", "\\|").replace("\n", " ")


def summary(args):
    record = json.loads(pathlib.Path(args.record).read_text(encoding="utf-8"))
    out = [f"## Test evidence: {record['name']}", "",
           f"`{record['ref']}` @ `{record['sha'][:10]}`. {_counts_line(record)}.", ""]
    rows = requirement_rows(record)
    cited = [r for r in rows if r["tests"]]
    out += ["### Requirement-ID tests", ""]
    if cited:
        out += [f"{len(cited)} requirement IDs are cited by tests "
                f"({sum(1 for r in rows if not r['tests'])} defined in a spec but cited by none).", "",
                "| Requirement | Result | Tests |", "|---|---|---|"]
        for r in cited:
            links = ", ".join(f"[`{_md(t['nodeid'].split('::')[-1])}`]({blob_url(record, t)})"
                              for t in r["tests"])
            out.append(f"| `{r['id']}` | {r['result']} | {links} |")
    else:
        out.append("No requirement IDs are cited in this suite's tests yet.")
    out += ["", "### Strict-xfail gap trackers (deviation register)", ""]
    devs = deviation_rows(record)
    if devs:
        out += ["| Test | Requirement | State | Reason |", "|---|---|---|---|"]
        for d in devs:
            t = d["test"]
            out.append(f"| [`{_md(t['nodeid'].split('::')[-1])}`]({blob_url(record, t)}) | "
                       f"{', '.join(f'`{i}`' for i in d['ids']) or '-'} | {d['state']} | "
                       f"{_md(t['xfail_reason'])} |")
    else:
        out.append("No strict-xfail gap trackers in this suite.")
    failing = [t for t in record["tests"] if t["outcome"] in ("failed", "error", "xpassed_strict")]
    if failing:
        out += ["", f"### Failing ({len(failing)})", ""]
        for t in failing[:50]:
            out.append(f"- [`{_md(t['nodeid'])}`]({blob_url(record, t)}): {_md(t['message'])}")
    out += ["", "_" + STANDING_TEXT + "_", ""]
    sys.stdout.write("\n".join(out))
    return 0


# --------------------------------------------------------------------------- page

CSS = """
body{font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;color:#1d2330;background:#f7f8fa}
main{max-width:1100px;margin:0 auto;padding:24px}
h1{font-size:26px;margin:8px 0 4px}h2{margin-top:36px;border-bottom:1px solid #d8dce3;padding-bottom:4px}
.notice{background:#fff8e1;border:1px solid #e0c46c;padding:12px 16px;border-radius:6px}
.muted{color:#5b6475}table{border-collapse:collapse;width:100%;background:#fff;font-size:14px}
th,td{border:1px solid #d8dce3;padding:5px 8px;text-align:left;vertical-align:top}
th{background:#eef1f5}code{font-size:13px}
.r-pass{color:#1d6b2f}.r-fail{color:#a4161a;font-weight:600}.r-dev{color:#8a5a00}.r-none{color:#5b6475}
.empty{background:#fff;border:1px dashed #b8bfcc;padding:14px 16px;border-radius:6px}
details{margin:6px 0}
"""


def _e(text):
    return html.escape(text or "", quote=True)


def _result_class(result):
    if result.startswith("pass"):
        return "r-dev" if "deviation" in result else "r-pass"
    if result in ("open deviation",):
        return "r-dev"
    if result in ("no test cites it", "skipped"):
        return "r-none"
    return "r-fail"


def _state_class(state):
    if state.startswith("open"):
        return "r-dev"
    return "r-none" if state.startswith("not run") else "r-fail"


def _test_link(record, test):
    label = test["nodeid"].split("::")[-1]
    url = blob_url(record, test)
    title = _e(test["nodeid"])
    return f'<a href="{_e(url)}" title="{title}"><code>{_e(label)}</code></a>' if url else f"<code>{_e(label)}</code>"


def _source_block(record):
    sha = record.get("sha", "")
    commit = (f'<a href="https://github.com/{_e(record["repo"])}/commit/{_e(sha)}"><code>{_e(sha[:10])}</code></a>'
              if sha else "unknown commit")
    run = f' &middot; <a href="{_e(record["run_url"])}">CI run</a>' if record.get("run_url") else ""
    return (f'<li><b>{_e(record["name"])}</b> (<a href="https://github.com/{_e(record["repo"])}">'
            f'{_e(record["repo"])}</a>), branch <code>{_e(record["ref"])}</code>, commit {commit}{run}, '
            f'tests run {_e(record["generated_utc"])}. {_e(_counts_line(record))}.</li>')


def render_page(records, missing=()):
    gen = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    h = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
         '<meta name=viewport content="width=device-width,initial-scale=1">',
         "<title>pyEfis and FIX-Gateway test evidence</title>",
         f"<style>{CSS}</style></head><body><main>",
         "<h1>pyEfis and FIX-Gateway: test evidence</h1>",
         f'<p class=muted>Generated {_e(gen)} from the CI test runs listed below. '
         "Every row is produced from the test suite and its JUnit results; nothing here is "
         "written by hand.</p>",
         f'<div class=notice id=standing-text><p>{_e(STANDING_TEXT)}</p></div>',
         "<h2 id=sources>Sources</h2><ul>"]
    h += [_source_block(r) for r in records]
    h += [f"<li><b>{_e(name)}</b>: no published result yet. {_e(why)}</li>" for name, why in missing]
    h.append("</ul>")

    # Requirements -> tests -> last result.
    h.append("<h2 id=requirements>Requirements, their tests, and the last result</h2>")
    h.append("<p>Each requirement ID links to the tests that cite it, at the exact commit that "
             "was run. A requirement defined in a spec that no test cites is listed too.</p>")
    for r in records:
        rows = requirement_rows(r)
        h.append(f"<h3>{_e(r['name'])}</h3>")
        if not rows:
            h.append('<p class=empty>No requirement IDs are cited in this suite\'s tests yet, '
                     "and no spec defining them was supplied.</p>")
            continue
        cited = sum(1 for x in rows if x["tests"])
        h.append(f"<p class=muted>{cited} of {len(rows)} requirement IDs are cited by at least one "
                 "test.</p>")
        h.append("<table><tr><th>Requirement</th><th>Title (from the spec)</th><th>Tests</th>"
                 "<th>Last result</th></tr>")
        for x in rows:
            tests = "<br>".join(_test_link(r, t) for t in x["tests"]) or "-"
            doc = (f' <span class=muted>({_e(x["doc"])})</span>' if x["doc"]
                   else ' <span class=muted>(no spec definition found)</span>')
            h.append(f'<tr id="{_e(r["name"])}-{_e(x["id"])}"><td><code>{_e(x["id"])}</code></td>'
                     f"<td>{_e(x['title'])}{doc}</td><td>{tests}</td>"
                     f'<td class={_result_class(x["result"])}>{_e(x["result"])}</td></tr>')
        h.append("</table>")

    # Deviation register.
    h.append("<h2 id=deviations>Deviation register</h2>")
    h.append("<p>Each entry is a known gap carried as a strict expected-failure test. It is "
             "expected to fail today; the day the feature lands the test passes, the CI run goes "
             "red, and the entry has to be closed on purpose.</p>")
    any_dev = False
    for r in records:
        devs = deviation_rows(r)
        if not devs:
            continue
        any_dev = True
        h.append(f"<h3>{_e(r['name'])}</h3><table><tr><th>Requirement</th><th>Test</th>"
                 "<th>Why it is open</th><th>State</th></tr>")
        for d in devs:
            h.append(f"<tr><td>{'<br>'.join(f'<code>{_e(i)}</code>' for i in d['ids']) or '-'}</td>"
                     f"<td>{_test_link(r, d['test'])}</td><td>{_e(d['test']['xfail_reason'])}</td>"
                     f"<td class={_state_class(d['state'])}>"
                     f"{_e(d['state'])}</td></tr>")
        h.append("</table>")
    if not any_dev:
        h.append("<p class=empty>No strict expected-failure gap trackers in the published runs.</p>")

    # Tested-where matrix (CAP-806): honest empty state until the issue form exists.
    h.append("<h2 id=tested-where>Tested where</h2>")
    h.append('<div class=empty><p><b>No hardware reports are published yet.</b></p>'
             "<p>This table is meant to list which builds have been run on which displays and "
             "computers, as reported by the people running them. It will fill from a "
             "&ldquo;Tested on my hardware&rdquo; issue form on the repositories, which does not "
             "exist yet. Until it does, this page has nothing to show here, and an empty table "
             "means exactly that: no reports, not no problems.</p></div>")

    h.append("<h2 id=sim>Simulator runs</h2>")
    h.append("<div class=empty><p><b>No simulator results are published yet.</b> Results from the "
             "scripted simulator tier will join this page once that tier runs as a CI gate.</p></div>")

    # Every test, collapsed.
    h.append("<h2 id=tests>Every test</h2>")
    for r in records:
        failing = [t for t in r["tests"] if t["outcome"] in ("failed", "error", "xpassed_strict")]
        h.append(f"<h3>{_e(r['name'])}</h3><p>{_e(_counts_line(r))}.</p>")
        if failing:
            h.append("<table><tr><th>Failing test</th><th>Message</th></tr>")
            h += [f"<tr><td>{_test_link(r, t)}</td><td>{_e(t['message'])}</td></tr>" for t in failing]
            h.append("</table>")
        by_file = {}
        for t in r["tests"]:
            by_file.setdefault(t["file"], []).append(t)
        for f in sorted(by_file):
            ts = by_file[f]
            n_pass = sum(1 for t in ts if t["outcome"] == "passed")
            h.append(f"<details><summary><code>{_e(f)}</code> &middot; {n_pass} of {len(ts)} pass"
                     "</summary><table><tr><th>Test</th><th>Result</th><th>What it checks</th></tr>")
            for t in ts:
                res = RESULT_WORDS.get(t["outcome"], t["outcome"])
                h.append(f"<tr><td>{_test_link(r, t)}</td><td class={_result_class(res)}>{_e(res)}</td>"
                         f"<td>{_e(t['summary'])}</td></tr>")
            h.append("</table></details>")

    h.append('<p class=muted style="margin-top:40px">Source records: '
             + ", ".join(f'<a href="{_e(r["name"])}.json">{_e(r["name"])}.json</a>' for r in records)
             + ". Generator: <code>tools/verification_report.py</code> in billmallard/pyEfis.</p>")
    h.append("</main></body></html>")
    return "\n".join(h)


def page(args):
    records = []
    for p in args.records:
        path = pathlib.Path(p)
        if path.is_file():
            records.append(json.loads(path.read_text(encoding="utf-8")))
    have = {r["name"] for r in records}
    missing = [(m, "Its CI has not published a record on the branch this page tracks.")
               for m in (args.expect or []) if m not in have]
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = {"index.html": render_page(records, missing)}
    for r in records:
        files[f"{r['name']}.json"] = json.dumps(r, indent=1, sort_keys=True)
    for name, text in files.items():
        leaks = find_leaks(text)
        if leaks:
            sys.stderr.write(f"refusing to publish {name}: it still contains {sorted(set(leaks))[:5]}\n")
            return 2
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="JUnit XML + test sources -> verification.json")
    c.add_argument("--junit", required=True)
    c.add_argument("--tests", required=True)
    c.add_argument("--docs", default=None, help="directory of spec .md files defining requirement IDs")
    c.add_argument("--repo", required=True)
    c.add_argument("--sha", default="")
    c.add_argument("--ref", default="")
    c.add_argument("--run-url", default="")
    c.add_argument("--out", required=True)
    s = sub.add_parser("summary", help="verification.json -> Markdown job summary")
    s.add_argument("record")
    p = sub.add_parser("page", help="verification.json files -> static site")
    p.add_argument("records", nargs="*")
    p.add_argument("--expect", nargs="*", help="record names to show as missing if absent")
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return {"collect": collect, "summary": summary, "page": page}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
