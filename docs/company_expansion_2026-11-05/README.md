# Verified company expansion — November 5, 2026

Reviewed 665 distinct candidate employers and approved **383 additions with 2,841 individual job listings**. The catalog grows from 2,222 to **2,605 companies**. These are newly tracked employers and discovered listings; an older opening is not represented as newly posted.

The complete [review ledger](review.csv) records every candidate, the decision, official website, identity evidence, careers source, discovery source, check time, and accepted job/link counts. Dates in the batch name use America/New_York; individual link checks were performed during the evening of November 5 (November 6 UTC).

The [individual job ledger](job-links.csv) lists all accepted titles, source URLs, advertised locations, and verification times. HR software products are classified as HR Tech; the existing exclusion of recruiting firms remains in place.

## Discovery and verification

- Used the supplied Boston LinkedIn screenshot as candidate leads, alongside publicly accessible LinkedIn hiring roundups. The exact screenshot post was not independently retrieved, and its funding figures were not treated as verified facts.
- Reviewed the official [November 2026 Hacker News hiring thread](https://news.ycombinator.com/item?id=50338728), current [funded-company hiring lists](https://theaiwins.com/funding/hiring/), Boston company directories, and companies from recent YC cohorts. This is a bounded review of public sources, not a claim to have inspected every LinkedIn post.
- The community YC directory was used only to discover candidates. Each accepted YC employer was checked against its primary YC company profile, active status, operating website, and employer-specific job data. Native YC additions required reported posting/activity within the last two months.
- Required employer identity and job-board branding to agree. Reviewed shortened names and rebrands separately, including 7AI / Seven AI, SF Tensor / San Francisco Tensor Company, and Belisar / formerly Pryzm. Existing employer names, aliases, and ATS tenants were compared before approval.
- Opened **every imported individual job URL** and verified its title or stable employer/job identifiers. A successful HTTP response or generic careers page alone did not qualify. Did not use sign-in-only feeds, paid services, private messages, or access-control workarounds.
- Removed general-interest forms and talent pools. For example, City Therapeutics' general-submissions entry is excluded, while its seven specific jobs are accepted. Flare Therapeutics and Life Biosciences had no verifiable specific openings suitable for import during this check. Standard Nuclear's individual pages could not be verified and were held back. Ambiguous directory matches such as Cowboy and Nous were held for identity review.

Examples added from the screenshot include 7AI, Air Space Intelligence, Code Metal, Cloaked, Ledgebrook, OnCorps, Wasabi Technologies, Parabilis Medicines, Anagram Therapeutics, Celea Therapeutics, Beeline Medicines, Antares Therapeutics, and City Therapeutics. Suno was already tracked. Cambridge Mobile Telematics' identity is established, but its current individual feed could not be independently verified in this run and was not added.

## Import and future scans

New source counts: 248 native Y Combinator, 109 Ashby, 18 Greenhouse, four Lever, two scoped HTML career pages, one CareerPuck, and one BambooHR.

The YC collector reads only the named employer's explicit job list, checks its stable employer ID, rejects unrelated/recommended jobs, and keeps public individual job URLs. Details must match the same job and employer. Requests share a rate and concurrency limit. Relative activity labels are retained as evidence rather than invented publication timestamps. A confirmed empty job list is distinguished from an unreadable or mismatched page before scan expiry can occur.

ASI and Anagram use explicit job-card selectors, so navigation links and neighboring locations cannot become jobs or locations. BambooHR now reads explicitly supplied `atsLocation` fields when the ordinary location fields are absent. Employer headquarters are never substituted for an unstated job location. All additions continue to use the existing structured city/state/country index and strict location search.

Only approved companies and their verified jobs are included in the deployment bundle. Existing company configurations remain unchanged. The import uses the normal job storage and geographic-index transaction, preserves provider publication dates, and sets discovery time in the server's local timezone. Account data and alert delivery are outside this import.

The browser review also exposed a search race: a slow initial search could overwrite a later company selection. Search responses now render only when they belong to the latest request, including when location resolution finishes out of order.

## Validation

- All 2,841 accepted job URLs passed individual-page checks.
- Full regression suite: 308 tests, including two checks for out-of-order searches.
- Database round trip: all accepted companies/jobs persisted; New York index memberships match the stored source geography; relative YC activity produces no fabricated posted dates; repeated writes preserve first discovery time.
- Deployment uses baseline file hashes, private rollback copies, Python compilation, service health checks, and live catalog/job-count verification. Exact results are recorded in [validation.json](validation.json).

A live posting can close after verification. Public hiring evidence establishes an identifiable operating employer and a working current source; it is not a guarantee about an employer's future hiring or conduct.
