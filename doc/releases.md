# Releases: flown baselines

Each `qa -> master` promotion is a flown baseline (CERT7), recorded the same way in
fix-gateway and pyEfis. The `release` job in `.github/workflows/ci.yml` gives the
master commit an annotated tag, `baseline-YYYY-MM-DD` (`-2`, `-3` for a second
promotion the same day), and a GitHub Release of the same name. The lookups are
`tools/flown_baseline.py`, kept identical in both repos and tested in
`tests/tools/test_flown_baseline.py`.

- **The tag message names the pyEfis baseline it flies with.** Both repos promoted on
  the same day share one tag name, so whichever is tagged second finds the first and
  names it as a confirmed pair. The one tagged first names the other's master commit
  as it stood then, and says what its tag will be if it is promoted later that day.
- **The release notes** carry that pairing, the open `safety:misleading` and
  `safety:loss` issues in both repos with a count of open issues not yet given a
  safety label, and the commit's test report (the same Markdown as the CI job
  summary).
- **The assets** are `verification.json` and `junit.xml` (the test records),
  `requirements-flown.lock.txt` (every package `pip install .` resolved to: Linux
  x86_64, Python 3.12) and `sbom.cdx.json` (the same environment as a CycloneDX 1.6
  SBOM). The `qt` and `sim` extras are not in the lock: the desktop client and the
  scenario tooling do not fly. The notes say how many locked versions match the
  environment the tests ran in.

A promotion whose tests failed still gets its tag and release, the release marked as a
pre-release with a line saying the run did not pass. A re-run of the job reuses the
commit's tag and does nothing once its release exists.

**Cadence and order.** Promotion is monthly (Bill, 2026-11-07), both repos on the same
day: fix-gateway first, then pyEfis once fix-gateway's release job has finished, so
pyEfis's tag names a confirmed pair. Promotion itself stays a human merge of
`dev -> qa -> master`.
