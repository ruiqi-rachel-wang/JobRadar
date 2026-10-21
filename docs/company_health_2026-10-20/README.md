# Jobdrop company and job-link audit

Snapshot: 2026-10-20T04:45:26.025426+00:00. Production repairs were applied October 20–21, 2026.

Checked all 2,222 configured companies. Inspected all 599,901 stored open-job URLs structurally and HTTP-tested 12,665 samples across 7,327 company/URL families (oldest and two newest per family). This is not an HTTP test of every stored job.

The audit is complete as an inventory; unresolved sources are explicitly recorded below and in the CSV. A reachable page, an API count, or an empty board alone does not establish that all jobs are correct or that a company stopped hiring.

| Result | Companies |
| --- | ---: |
| empty source needs confirmation | 82 |
| inconclusive access or rate limit | 62 |
| inconclusive probe time limit | 13 |
| intentionally disabled | 55 |
| repaired and refreshed | 48 |
| source error needs followup | 67 |
| source lists jobs | 1317 |
| source lists jobs no recent discoveries | 461 |
| suspected navigation or category results | 116 |
| verified not hiring expired posting closed | 1 |

## Deployed repairs

| Company | Refreshed jobs |
| --- | ---: |
| Bank of America | 1741 |
| 10x Genomics | 36 |
| AkzoNobel | 227 |
| Ally | 78 |
| Amplitude | 41 |
| AppFolio | 34 |
| Arcadia | 20 |
| Beehiiv | 1 |
| Bentley Systems | 89 |
| Boomi | 21 |
| Capgemini | 629 |
| ClickHouse | 202 |
| CommuniCare Health Services | 2100 |
| DuPont | 195 |
| Elastic | 371 |
| Form3 | 6 |
| Fullbay | 13 |
| Gradient Experience | 13 |
| Hex Technologies | 44 |
| Hightouch | 87 |
| Kellanova | 113 |
| Luminance | 56 |
| Mach Industries | 157 |
| Marqeta | 45 |
| Mozilla | 103 |
| Outschool | 7 |
| Philo | 2 |
| Publix Super Markets | 395 |
| Remote | 51 |
| rEvolution | 6 |
| Spring Health | 87 |
| Starbucks | 2457 |
| Starcloud | 17 |
| Substack | 17 |
| Swiftly | 8 |
| Sysdig | 31 |
| Temporal | 73 |
| World Labs | 11 |

The initial Capgemini repair used SuccessFactors pagination and posting-level location elements to collect 629 jobs instead of 25, including 176 jobs with multiple locations. Original 25 IDs and dates were preserved. Source location values are retained; equivalent display locations may be deduplicated.

Earlier Loom and Deel repairs were separately deployed and backed up: Loom keeps brand-specific job detail links; Deel uses its current public job pages.

Shortcut’s official careers page explicitly says it is not actively hiring. Its last stored posting returned 404 and the current route redirected to an empty board; that expired row was backed up and closed. The source remains recorded for follow-up when hiring resumes.

## Open findings

Access blocks, request limits, browser-only results, and audit time limits remain inconclusive. Empty feeds are preserved for follow-up rather than treated as proof of zero jobs. Navigation/category flags require source correction; they are not counted as healthy listings. Acquired brands must not inherit every job from the parent company.

Every unresolved company, error, prior hiring activity, sample result, and refresh status is in [all_companies.csv](all_companies.csv). Historical labels missing current config are in [unconfigured_historical_companies.csv](unconfigured_historical_companies.csv); aliases were not automatically merged or deleted.

## Coverage and safeguards

- Read-only source probes covered listing pagination without sending alerts or writing jobs.
- HTTP 403/429/challenges and client-rendered shells were kept separate from verified job pages.
- A final HTTP pass sampled 114 repaired/current postings: 91 matched job titles, 22 were reachable without verifiable job content in the HTTP response, and one expired Shortcut posting was confirmed and closed. See current_repaired_link_samples.csv. These are representative checks, not certification of every posting.
- Full refreshes used the normal scanner; partial/error results did not expire unseen jobs.
- Affected database rows and code/config were backed up before deployment. Jobs were soft-closed through normal reconciliation; saved jobs were not deleted.
- Existing fresh/full scan schedules were verified and retained. Full scans already run; absence of a new job in a fresh scan can be legitimate.
- All 55 deliberately disabled entries remained disabled. Read-only rechecks covered 39 previously technical failures; 16 policy/administrative exclusions were retained. See disabled_source_rechecks.csv; a positive count can still be navigation or an unrelated employer.
- The raw runtime probes are under app/data/company_health/2026-10-20 and are ignored by Git.

Validation: all 177 tests passed, including regression checks for redirects, stale public routes, embedded apply buttons, pagination caps, and expanded Capgemini locations. Production configs were compared with the deployed changes, and 8,565 backed-up rows retained their IDs and existing dates.

[HTTP samples](sampled_job_links.csv) · [Structural flags before repairs](structural_url_flags.csv) · [Machine-readable totals](summary.json)

## Bank of America location follow-up

Refreshed 1,741 jobs and corrected 714 existing location lists, including 15 previously NC-only listings that also offer New York. All 698 multi-location jobs now retain the full location lists. The scraper reads the primary address and both additional-location fields, deduplicates offices, and retains raw source values. The live New York filter was verified with Product Manager, CashPro Security. All 7,393 historical IDs and existing dates were preserved. The follow-up passed all 185 tests.

## Final recheck and release

[Final verification](FINAL_VERIFICATION.md) records the fresh source scans, live browser checks, Workable office-merge fix, Quadric migration, 188 passing tests, and 138 verified posting links. Earlier counts above are historical; the final recheck covers 49 companies and 9,978 open jobs. Other unresolved findings in the full company inventory remain documented; the repaired sources are not a claim that all 2,222 companies are issue-free.
