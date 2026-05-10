# JobRadar

## Public First Version

The app is ready to deploy as a Render web service. Admin actions are protected
by `ADMIN_PASSWORD`; the public home page and job search API remain readable.

### Required Render Environment

Set these in Render:

- `ADMIN_PASSWORD`: password for `/admin`, scan, reset, detect, and add-company actions.
- `SECRET_KEY`: generated automatically by `render.yaml`.
- `JOBRADAR_DATA_DIR`: `/var/data`
- `JOBRADAR_COMPANIES_FILE`: `/var/data/companies.json`

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
