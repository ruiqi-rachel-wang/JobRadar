# JobDrop Build Week Before/After Evidence

This document gives judges a concise, reproducible record of the eligible
extension to the existing JobDrop project.

## Baseline and comparison

- Pre-event baseline: `3c2ce52`, dated 2026-07-12
- Comparison command: `git diff --stat 3c2ce52..<final-submission-commit>`
- Detailed comparison: `git diff 3c2ce52..<final-submission-commit>`
- Final submission commit: use `git rev-parse HEAD` after the reviewed branch is
  pushed, then paste that immutable hash into Devpost.

## Product before and after

| Area | At the baseline | Build Week extension | Reproducible evidence |
| --- | --- | --- | --- |
| Search | Literal keyword and alias matching | Broad occupational families, stored-keyword search, and standalone abbreviation boundaries | Search `software engineer`; run `tests.test_job_search` |
| Freshness | Job collection and basic date display | Posted date first, added-date fallback, and precise discovery-time tie-break | Compare mixed posted/missing-date jobs; inspect `JOB_DATE_ORDER_SQL` |
| Discovery | Company filters with mixed metadata | Canonical industries separated from descriptive company tags | Open company, industry, and tag filters; run taxonomy tests |
| Saved searches | Saved-search records existed | Daily delivery cursors, retry-safe email alerts, and user controls | Save a search; run saved-search alert tests |
| Ingestion | Multiple ATS collectors | More complete pagination, descriptions, public URLs, stable IDs, and robots handling | Run source-specific and robots regression tests |
| Scan safety | Scheduled scanning | Persistent queued scans, restart recovery, and protection from expiry after partial reads | Run scheduler, pagination, and empty-scan expiry tests |
| Submission safety | Runtime secrets ignored | Interview-process prose removed, vulnerable dependencies upgraded, and browser security hardening added | Inspect tracked data and requirements; run security tests |

## Eligible change record

- `0047cb4` preserves queued scheduled scans and avoids false company failures
  during status-write lock contention.
- `3434d64` is a large checkpoint spanning the product extension. Because it
  combines multiple workstreams, claims are supported by the file-level diff
  and regression tests rather than by commit granularity alone.
- `0948e90` and `cf4be31` record an ordering experiment and its reversal; the
  final behavior remains posted-date first with the agreed added-date fallback.
- `915c396` adds the submission documentation.
- The final hardening commit contains abbreviation-boundary tests,
  submission-risk data removal, dependency updates, security headers, and this
  evidence sheet.

## Model provenance

Codex with GPT-5.6 Sol is verified for the final submission-hardening and
verification thread. Earlier eligible-period changes remain part of the Git
comparison, but their session metadata does not establish a model version.
Submission language therefore describes the verified GPT-5.6 Sol work directly
and does not attribute every earlier change to that model.

## Judge verification

1. Check out the final submission commit and follow the README setup.
2. Run `python3 -m unittest discover -s tests -q`.
   The verified hardening checkpoint runs 60 tests in a clean Python 3.10
   environment installed from `requirements.txt`.
3. Compare the final commit with `3c2ce52` using the commands above.
4. Follow the short demonstration script in `build-week-submission.md`.
