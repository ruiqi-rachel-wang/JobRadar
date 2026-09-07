# Disabled company source audit — 2026-08-05

The 66 disabled company configurations were rechecked against their official
careers pages, ATS boards, robots rules, public APIs, and the existing JobDrop
adapters. Ten sources were repaired and live-tested. The remaining 56 stay
disabled for the reasons below.

## Recovered and enabled (10)

| Company | Source now used | Live jobs |
| --- | --- | ---: |
| Arista Networks | SmartRecruiters API | 237 |
| Articulate | Lever API | 1 |
| Cohere | Ashby API | 139 |
| Cummins | DirectEmployers/JobSync API | 1,084 |
| DeepFin Research | Greenhouse API | 4 |
| Lindy | Teamtailor careers page | 3 |
| ManTech | Avature careers site | 749 |
| Principal Financial Group | First-party jobs API | 100 |
| Tradeweb | DirectEmployers/JobSync API | 56 |
| Yum Brands | First-party jobs API | 193 |

These are live counts from the source audit, not permanent expected counts.

## Policy, robots, or deliberate visibility restriction (13)

| Company | Why it remains disabled |
| --- | --- |
| Accredible | Official site's robots.txt disallows the careers source. |
| Amazon | Terms restrict third-party job aggregation. |
| Apple | Terms restrict automated access to job listings. |
| Archer Aviation | Careers page is allowed, but the jobs API it relies on is disallowed by robots.txt. |
| Citadel | Current terms explicitly prohibit crawling/scraping. |
| Citadel Securities | Uses the same restricted Citadel careers source and terms. |
| D. E. Shaw | Current site-use terms do not permit this third-party/commercial reuse. |
| Dartmouth College | PeopleAdmin robots.txt disallows the job search. |
| Goldman Sachs | Search results are robots-blocked and the usage policy restricts automation. |
| Google | Official sitemap works, but this company is intentionally admin-only. |
| Meta | Terms restrict automated access to job listings. |
| Microsoft | Terms restrict automated access to job listings. |
| UNC Chapel Hill | PeopleAdmin robots.txt disallows the job site. |

## Retired, acquired, empty, or no standalone authoritative source (18)

| Company | Verification result |
| --- | --- |
| Alexander Chapman | Careers page has no jobs and no authoritative feed. |
| Arcellx | Standalone source now redirects to Kite; no Arcellx feed remains. |
| Barracuda View Team | Official careers view currently exposes no positions; page-wide matches were false positives. |
| Chesapeake Technology | Official page explicitly says it is not currently hiring. |
| Coffee 'n Clothes | Former Freshteam domain is invalid and no replacement exists. |
| CoreOS | Acquired by Red Hat; roles are no longer published as CoreOS. |
| Dragonfly | Former Ashby board is gone and no employer-specific replacement was verified. |
| Exact Sciences | Standalone board no longer publishes openings after the Abbott transition. |
| Gower Street Analytics | Official site exposes no openings or jobs feed. |
| Hero Digital | Employer careers source retired after the move into Huge. |
| HudsonGray | No official job links or authoritative feed. |
| Informatica | Standalone careers source retired after the Salesforce acquisition. |
| Memora Health | Former Ashby board is empty and no replacement was verified. |
| Newfront | Standalone source retired after the WTW acquisition. |
| Nurx | Former parent-company board is gone; no standalone source remains. |
| Retool | Official rendered openings section currently returns zero roles and the former Gem feed is empty. |
| Upstatement | Official page explicitly has no open positions. |
| XR Trading | Official Paylocity board currently contains zero open roles. |

## Active employer, but no reliable supported source today (25)

| Company | Current blocker |
| --- | --- |
| Carlyle Group | Official replacement BrassRing board is unavailable to the scanner. |
| Condé Nast | Current Workday jobs endpoint returns HTTP 422/maintenance. |
| FreshDirect | Legacy iCIMS board is gone and the current site blocks collection. |
| Georgia Tech | Public staff applications move into a gated OneUSG flow. |
| Glassdoor | Legacy employer board is retired; jobs moved under Indeed without a standalone feed. |
| Harvard University | WAF plus BrassRing/PeopleSoft gateway; no stable public listing endpoint. |
| HCA Healthcare | Cloudflare challenge blocks requests and the browser collector. |
| IBM | Current proprietary careers search has no verified supported endpoint. |
| Infosys | Official BrassRing source does not return reliable job records. |
| Jefferies | Official Oracle Cloud source returns gateway errors. |
| Johns Hopkins University | Current external jobs endpoint returns HTTP 403. |
| Milk Makeup | Legacy Lever board is gone and the current site blocks non-browser access. |
| MIT | External candidate gateway does not expose reliable structured records. |
| MTA | WAF returns HTTP 403 and blocks the browser collector. |
| Northwestern University | PeopleSoft candidate gateway has no public listing feed. |
| Nubank | Current official job pages return HTTP 403; legacy Greenhouse is empty. |
| Parker Hannifin | WAF blocks plain, browser, and reader collection. |
| Precision Neuroscience | Official page shows active roles, but current application links are not exposed to the scanner. |
| Purdue University | SuccessFactors/public page yields categories, not authoritative job records. |
| Ralph Lauren | Current Avature site is active but does not yield records through the supported collector. |
| Sempra | Configured ADP source returns no authoritative records. |
| SIG | Browser collection works, but automated-use permission is not clear enough to enable safely. |
| Tesla | Official careers search blocks automated collection with HTTP 403. |
| UC Berkeley | External applications are behind a PeopleSoft candidate gateway without a public listing feed. |
| Vimeo | Current careers page exposes no current job links or authoritative feed. |

“Active but blocked” means JobDrop cannot track the company reliably and safely
with the source available on this audit date. It does not mean the company can
never be supported; those 25 are the recheck queue when a source or adapter
changes.
