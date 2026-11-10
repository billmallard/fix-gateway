#!/usr/bin/env python3
#  SPDX-License-Identifier: GPL-2.0-or-later
"""Flown baseline record for a qa -> master promotion (CERT7).

pyEfis and fix-gateway fly as a pair, so each promotion is recorded in both repos the
same way. The release job in `.github/workflows/ci.yml` calls this to:

  name       pick the annotated tag name, `baseline-YYYY-MM-DD` (then `-2`, `-3` on a
             second promotion the same day). Promote both repos on the same day and
             both tags share one name, which is how each names the other.
  pair       resolve the other repo's flown baseline for the tag message and notes:
             its tag of the same name if it exists, else the baseline tag on its
             master, else its untagged master commit, said plainly.
  safety     list the open `safety:misleading` and `safety:loss` problem reports in
             both repos (CERT8), and count open issues that carry no safety label.
  lockcheck  compare the release's resolved lock file with the environment CI tested.

This file is kept identical in pyEfis and fix-gateway. Network reads go through
`git ls-remote` and the `gh` CLI; the logic between them is pure and tested in
`tests/tools/test_flown_baseline.py`.
"""

import argparse
import json
import re
import subprocess
import sys

TAG_PREFIX = "baseline-"
PROBLEM_LABELS = ("safety:misleading", "safety:loss")
SAFETY_LABELS = PROBLEM_LABELS + ("safety:none",)


def parse_ls_remote(text):
    """`git ls-remote --tags` output -> {tag: commit sha}. An annotated tag's peeled
    `^{}` line names the commit, and wins over the tag object's own sha."""
    tags = {}
    peeled = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/tags/"):
            continue
        sha, ref = parts
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            tags[name] = sha
    tags.update(peeled)
    return tags


def pick_tag_name(date, existing):
    """The first free name of `baseline-<date>`, `baseline-<date>-2`, ..."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    base = TAG_PREFIX + date
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def resolve_pair(name, other_tags, other_master):
    """The other repo's flown baseline, as (kind, tag or None, sha).

    kind is `same-name` (the other repo was promoted today and tagged first),
    `master-tag` (its master carries an earlier baseline tag, so it was not promoted
    this time) or `untagged` (its master has no baseline tag: promoted before CERT7,
    or its promotion today has not been tagged yet)."""
    if name in other_tags:
        return ("same-name", name, other_tags[name])
    on_master = sorted(t for t, sha in other_tags.items() if t.startswith(TAG_PREFIX) and sha == other_master)
    if on_master:
        return ("master-tag", on_master[-1], other_master)
    return ("untagged", None, other_master)


def pair_line(other_repo, name, kind, tag, sha):
    """One plain-text line naming the other repo's baseline, for the tag message.

    Only `same-name` is a confirmed pair. Otherwise the other repo may still be
    promoted later the same day, so the line says what its tag will be then."""
    if kind == "same-name":
        return f"Flown with {other_repo} {tag} ({sha[:12]}), promoted the same day."
    if kind == "master-tag":
        now = f"Flown with {other_repo} {tag} ({sha[:12]}), the baseline on its master when this tag was cut."
    else:
        now = (f"Flown with {other_repo} master at {sha[:12]}, which carried no baseline tag "
               f"when this tag was cut.")
    return (f"{now} If {other_repo} is promoted later today its tag will be {name}, "
            f"and that tag names this one.")


def safety_markdown(problems, unassessed):
    """Markdown for the release notes.

    problems: {repo: [issue dicts with number, title, url, labels]}
    unassessed: {repo: count of open issues carrying no safety:* label}"""
    out = ["### Open safety problems", ""]
    total = sum(len(v) for v in problems.values())
    if total == 0:
        out.append("No open issue in these repos is labelled `safety:misleading` or `safety:loss`.")
    else:
        out.append(f"{total} open issue(s) labelled `safety:misleading` or `safety:loss`:")
        out.append("")
        for repo, issues in problems.items():
            for issue in sorted(issues, key=lambda i: i["number"]):
                labels = sorted(lb for lb in issue["labels"] if lb in PROBLEM_LABELS)
                out.append(f"- {repo}#{issue['number']} `{', '.join(labels)}`: "
                           f"[{issue['title']}]({issue['url']})")
    out.append("")
    counts = ", ".join(f"{repo} {n}" for repo, n in unassessed.items())
    out.append(f"Open issues with no safety label yet, so not assessed: {counts}. "
               f"A problem report is only on the list above once it is labelled.")
    return "\n".join(out)


def parse_freeze(text):
    """`pip freeze` lines -> {normalized name: requirement line}."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-e "):
            continue
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            out[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = line
    return out


def lockcheck_markdown(lock_text, tested_text):
    """How the resolved lock compares with the environment the tests ran in."""
    lock = parse_freeze(lock_text)
    tested = parse_freeze(tested_text)
    # pip writes a name in its own case (PyYAML); compare the pins case-blind.
    same = [n for n in lock if n in tested and tested[n].lower() == lock[n].lower()]
    differ = [n for n in lock if n in tested and tested[n].lower() != lock[n].lower()]
    missing = [n for n in lock if n not in tested]
    out = [f"{len(same)} of {len(lock)} locked packages are the exact versions CI tested this commit with."]
    for n in sorted(differ):
        out.append(f"- differs: `{lock[n]}` locked, `{tested[n]}` tested")
    for n in sorted(missing):
        out.append(f"- not in the tested environment: `{lock[n]}`")
    return "\n".join(out)


def _run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _remote_tags(repo):
    return parse_ls_remote(_run(["git", "ls-remote", "--tags", f"https://github.com/{repo}.git"]))


def _remote_master(repo):
    line = _run(["git", "ls-remote", f"https://github.com/{repo}.git", "refs/heads/master"]).split()
    if not line:
        raise SystemExit(f"{repo} has no master branch")
    return line[0]


def _issues(repo, label=None):
    cmd = ["gh", "issue", "list", "-R", repo, "--state", "open", "--limit", "1000",
           "--json", "number,title,url,labels"]
    if label:
        cmd += ["--label", label]
    issues = json.loads(_run(cmd))
    for issue in issues:
        issue["labels"] = [lb["name"] for lb in issue["labels"]]
    return issues


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("name")
    p.add_argument("--repo", required=True)
    p.add_argument("--date", required=True)
    p = sub.add_parser("pair")
    p.add_argument("--other", required=True)
    p.add_argument("--name", required=True)
    p = sub.add_parser("safety")
    p.add_argument("--repos", nargs="+", required=True)
    p = sub.add_parser("lockcheck")
    p.add_argument("--lock", required=True)
    p.add_argument("--tested", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "name":
        print(pick_tag_name(a.date, _remote_tags(a.repo)))
    elif a.cmd == "pair":
        kind, tag, sha = resolve_pair(a.name, _remote_tags(a.other), _remote_master(a.other))
        print(pair_line(a.other, a.name, kind, tag, sha))
    elif a.cmd == "safety":
        problems = {}
        unassessed = {}
        for repo in a.repos:
            found = {}
            for label in PROBLEM_LABELS:
                for issue in _issues(repo, label):
                    found[issue["number"]] = issue
            problems[repo] = list(found.values())
            unassessed[repo] = sum(1 for i in _issues(repo) if not set(i["labels"]) & set(SAFETY_LABELS))
        print(safety_markdown(problems, unassessed))
    elif a.cmd == "lockcheck":
        with open(a.lock) as f1, open(a.tested) as f2:
            print(lockcheck_markdown(f1.read(), f2.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
