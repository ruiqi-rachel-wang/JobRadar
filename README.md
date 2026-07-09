# JobRadar

## Public First Version

JobRadar is a Flask app for collecting and searching public job postings from
company career pages and ATS job-board APIs.

Admin actions are protected by `ADMIN_PASSWORD`; the public home page and job
search API remain readable.

### Public Release Safety

- Runtime state is intentionally ignored: `.env`, `app/data/jobs.db`,
  `app/data/seen_jobs.json`, and `app/data/scan_summary.json`.
- Higher-risk company configs are kept locally in ignored `app/private/`.
  These include browser-automation configs, proprietary/custom single-company
  fetchers, and copied interview-process prose that should be reviewed before
  public release.
- The public `app/companies.json` is the safer first-pass list. It favors
  public ATS APIs and direct career-page feeds.
- This project is not legal advice. Before deploying a public crawler, review
  each source site's terms, robots policy, and rate limits.

### Required Render Environment

Set these in Render:

- `ADMIN_PASSWORD`: password for `/admin`, scan, reset, detect, and add-company actions.
- `SECRET_KEY`: generated automatically by `render.yaml`.
- `JOBRADAR_DATA_DIR`: `/var/data`
- `JOBRADAR_COMPANIES_FILE`: `/var/data/companies.json`
- `STRIPE_PRICE_ID`: recurring Stripe price ID for JobDrop Pro.
- `STRIPE_SECRET_KEY`: Stripe secret key, stored only on the server.
- `STRIPE_WEBHOOK_SECRET`: Stripe webhook signing secret for `/stripe/webhook`.

The Render disk keeps `jobs.db`, `seen_jobs.json`, `scan_summary.json`, and
runtime company edits across deploys. On first boot, `companies.json` is copied
from the repo into `/var/data/companies.json`.

### Local Run

```bash
export ADMIN_PASSWORD=change-me
python3 job_search/app/app.py
```

Open:

- Public app: `http://localhost:8080`
- Admin: `http://localhost:8080/admin`
