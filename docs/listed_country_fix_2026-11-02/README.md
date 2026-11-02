# Listed-country location correction — 2026-11-02

Jobs with explicitly listed countries were incorrectly marked “Country not listed” and admitted to unrelated country searches. Microsoft’s country–region–city labels were read backwards; several recognized country names lacked ISO mappings; and labels such as “Remote - Bangalore, India” lost their city and country. Workday facility addresses also dropped the separately supplied primary country.

The shared normalizer now preserves these countries, remote city/state restrictions, complete facility addresses, and secondary offices. Country aliases used by employers, including “Mainland China” and “Korea, Republic of,” are recognized. Ambiguous state codes retain their address context. Genuinely missing country/location information remains eligible for location searches.

The latest full pass inspected 627,757 open jobs. Guarded repairs corrected 14,353 distinct jobs across 399 companies; all 14,353 were then rechecked against the reviewed labels with zero differences in displayed or structured geography. Repairs checked job identity and preserved every non-location field on each write, including posting dates, first-seen dates, and IDs used by saved jobs. Private backups stay on the production server. The bulk review reparses preserved employer labels; it does not represent a fresh fetch of every employer page. Citi’s reported facility was additionally checked against its current public Workday detail response.

All 261 tests pass. Live browser checks cover Microsoft US/India filtering and job drawers, Citi’s India location, qualified remote locations, and inclusion of genuinely unknown locations. No JavaScript errors were observed. [Verification and per-company repair counts](verification.json) record the results.

`scripts/build_country_location_repairs.py --output proposals.jsonl.gz` previews repairs without writing job data. Reviewed proposals can be applied with the existing guarded `scripts/repair_job_locations.py` workflow.
