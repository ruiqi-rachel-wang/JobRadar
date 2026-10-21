# Final verification — October 21, 2026

- 188 tests passed in an isolated test database.
- Fresh full scans checked 49 companies: 48 returned jobs, Shortcut returned an expected empty-board warning, and no company failed.
- 9,978 open jobs were reconciled. All 24,084 backed-up row IDs and existing dates remained intact.
- All 138 representative posting URLs were verified: 115 by HTTP title/content and 23 after browser rendering.
- Six live browser flows passed without JavaScript errors: BOA New York search, expanded Capgemini locations, Loom direct links, Deel direct links, Luminance New York/San Francisco locations, and Quadric jobs.
- All 75 application files compared against production matched the local release source; the service is active.

The last pass found and fixed Workable duplicate-office rows overwriting each other. Luminance’s New York and San Francisco offices now remain on the same posting. Quadric’s official careers page now links to Ashby; its stale Workable source was replaced and 15 current jobs refreshed.

## Current company counts

| Company | Open jobs | Jobs with multiple locations |
| --- | ---: | ---: |
| 10x Genomics | 36 | 0 |
| AkzoNobel | 228 | 0 |
| Ally | 78 | 0 |
| Amplitude | 42 | 3 |
| AppFolio | 34 | 21 |
| Arcadia | 20 | 1 |
| Bank of America | 1741 | 698 |
| Beehiiv | 1 | 0 |
| Bentley Systems | 89 | 0 |
| Boomi | 21 | 1 |
| Capgemini | 630 | 176 |
| ClickHouse | 202 | 62 |
| CommuniCare Health Services | 2100 | 0 |
| David | 33 | 0 |
| Deel | 256 | 0 |
| DuPont | 195 | 0 |
| Elastic | 371 | 1 |
| Form3 | 6 | 1 |
| Fullbay | 13 | 0 |
| Gradient Experience | 13 | 0 |
| Hex Technologies | 44 | 24 |
| Hightouch | 87 | 11 |
| Hugging Face | 6 | 0 |
| Kellanova | 113 | 0 |
| Loom | 2 | 0 |
| Luminance | 52 | 4 |
| Mach Industries | 157 | 14 |
| Marqeta | 45 | 7 |
| Mozilla | 103 | 5 |
| Netguru | 26 | 0 |
| Outschool | 7 | 0 |
| Persado | 1 | 0 |
| Philo | 2 | 2 |
| Publix Super Markets | 395 | 0 |
| Quadric | 15 | 2 |
| Remote | 51 | 0 |
| Rokt | 11 | 0 |
| Shortcut | 0 | 0 |
| Spring Health | 87 | 10 |
| Starbucks | 2457 | 105 |
| Starcloud | 17 | 0 |
| Substack | 17 | 8 |
| Swiftly | 8 | 4 |
| Sysdig | 31 | 0 |
| Temporal | 73 | 29 |
| TrainingPeaks | 4 | 0 |
| World Labs | 11 | 0 |
| dubizzle | 41 | 1 |
| rEvolution | 6 | 0 |

## Limits and evidence

Checks cover the repaired adapters/configurations and representative links; they cannot guarantee future third-party website availability or certify every stored URL. Unresolved sources outside this repair set remain in [all_companies.csv](all_companies.csv). Shortcut is explicitly not hiring on its official careers page; its obsolete posting was closed.

[Final posting checks](final_link_checks.csv) · [Original audit](README.md)

Reproduce the test run with fresh temporary JOBRADAR_DATA_DIR and JOBDROP_AUTH_DB_FILE paths, JOBRADAR_ENABLE_APP_SCHEDULER=false, and `python3 -m unittest discover -s tests -q`. Runtime source snapshots and private row backups are excluded from Git.
