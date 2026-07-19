# JobDrop

JobDrop collects public job openings directly from company career pages and
ATS job-board APIs, then makes them searchable in one place. Results prioritize
the source-posted date, fall back to when JobDrop first discovered a job when
that date is missing, and use discovery time to order jobs within the same date.

Live app: [jobdrop.site](https://jobdrop.site/)

## OpenAI Build Week 2026

JobDrop is an existing project that was meaningfully extended during the Build
Week submission period. The last pre-event baseline is commit `3c2ce52`.

### What existed before the submission period

- A Flask application and admin scanner
- Public company and job pages
- ATS-specific job collectors
- Accounts, saved searches, an application list, and optional Pro billing

### Build Week extension: Fresh Discovery & Alerts

- Broad role-family search that connects related titles, such as “software
  engineer” and “Python Application Developer”
- Search across stored job keywords as well as titles and companies
- Canonical company industries separated from descriptive company tags
- Posted-date-first ordering with added date as the missing-date fallback and
  `first_seen DESC` as the within-date tie-breaker
- Date filters and labels that match the effective posted/added-date behavior
- Saved-search email alerts with per-search delivery cursors and safe retry
- More complete pagination, descriptions, locations, public job URLs, and stable
  job identity across supported ATS sources
- Safer scan expiry: partial or failed source reads cannot incorrectly close jobs
- Persistent scheduling and queue recovery for interrupted scans
- 60 automated tests covering search, alerts, scheduling, taxonomy, security, source
  pagination, robots rules, job identity, and data repair

### How Codex and GPT-5.6 were used

Codex with GPT-5.6 Sol is the verified engineering collaborator for the final
Build Week submission-hardening and verification pass. In that thread it was
used to:

- Audit the existing repository against the pre-event Git baseline
- Trace behavior across Flask routes, SQL queries, fallbacks, templates, and tests
- Verify the posted-date-first ordering contract across SQL, fallbacks, date
  filters, interface labels, and regression tests
- Correct abbreviation matching so `SWE` is treated as a standalone term
- Remove tracked interview-process prose and update vulnerable dependencies
- Add browser security headers and cross-site write protection
- Run targeted tests before the complete suite and inspect the resulting diff
- Prepare the submission documentation and reproducible judging instructions

Earlier eligible-period changes are evidenced by the Git diff, commit dates,
and regression tests. This statement does not assign an unverified model version
to those earlier development sessions. See
[docs/build-week-evidence.md](docs/build-week-evidence.md) for the before/after
record.

The product decisions remained human-directed. In particular, JobDrop uses
an explicit date-based order rather than model relevance, uses added time when a
source has no posted date, favors broad search recall, does not auto-apply to
jobs, and preserves direct links to the original company posting.

## Product workflow

1. Select companies, industries, or company tags.
2. Search by role, location, experience, and date window.
3. Open a result to review its description, experience, skills, and source URL.
4. Save the search or add a job to the application tracker.
5. Pro users can enable a daily email after the final scheduled fresh scan.

## Local setup

Requirements:

- Python 3.10 or newer
- SQLite for the default local configuration, or PostgreSQL through
  `DATABASE_URL`
- Playwright only for company sources configured to require a browser

```bash
git clone https://github.com/ruiqi-rachel-wang/JobRadar.git
cd JobRadar
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
# Required only when testing or scanning browser-backed company sources:
python3 -m playwright install chromium
cp .env.example .env
python3 app/app.py
```

Open:

- Public app: `http://localhost:8080/`
- Signed-in app: `http://localhost:8080/app`
- Admin: `http://localhost:8080/admin`

At minimum, set unique values for `ADMIN_PASSWORD` and `SECRET_KEY` in `.env`.
The application uses a local SQLite database when `DATABASE_URL` is omitted.
A fresh local database contains no job rows until an authorized admin runs a
scan. The public live app can be used to evaluate the populated experience.

### Optional environment configuration

- `DATABASE_URL`: PostgreSQL connection URL
- `JOBRADAR_DATA_DIR`: absolute runtime-data directory
- `JOBRADAR_COMPANIES_FILE`: absolute runtime company-config path
- `JOBRADAR_ENABLE_APP_SCHEDULER`: enable the web-admin schedule
- `JOBRADAR_RESPECT_ROBOTS`: respect source robots policies
- `JOBDROP_SMTP_*`: saved-search email delivery
- `STRIPE_PRICE_ID`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`: optional Pro
  billing

See [.env.example](.env.example) for the complete list and path guidance.

## Tests

Run the entire suite:

```bash
python3 -m unittest discover -s tests -q
```

Run the primary search regression suite:

```bash
python3 -m unittest tests.test_job_search -v
```

The Build Week checkpoint passes all 60 tests from a clean Python 3.10
environment installed directly from `requirements.txt`.

## Judging and submission materials

The Devpost description, demonstration outline, and final submission checklist
are in [docs/build-week-submission.md](docs/build-week-submission.md).

## Public release and source safety

- Runtime state is ignored: `.env`, databases, seen-job state, and scan summaries
  are not committed.
- Higher-risk source configurations remain in ignored `app/private/`, and
  copied interview-process prose is excluded from the tracked company list.
- The public company list favors public ATS APIs and direct career-page feeds.
- JobDrop includes configurable robots enforcement, throttling, and backoff.
- Operators remain responsible for reviewing source terms, robots policies, data
  rights, and rate limits before enabling a collector.

This project is not legal advice.
