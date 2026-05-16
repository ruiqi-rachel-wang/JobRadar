"""Import legacy JobRadar JSON job data into the configured database."""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from api.job_data import init_db, upsert_jobs
from api.utils import make_id


def _job_from_mapping(item, fallback_id=""):
    if not isinstance(item, dict):
        raise ValueError(f"Unsupported job item: {item!r}")

    company = item.get("company") or item.get("company_name") or ""
    title = item.get("title") or ""
    url = item.get("url") or item.get("job_url") or ""
    if not company or not title:
        raise ValueError(f"Job missing company/title: {item!r}")

    first_seen = item.get("first_seen") or item.get("first_seen_at")
    if not first_seen:
        first_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "id": item.get("id") or fallback_id or make_id(company, url, title),
        "company": company,
        "title": title,
        "url": url,
        "location": item.get("location") or "",
        "source": item.get("source") or "",
        "categories": item.get("categories") or [],
        "first_seen": first_seen,
        "posted": item.get("posted") or item.get("posted_date") or "",
        "exp_min": item.get("exp_min"),
        "exp_max": item.get("exp_max"),
        "exp_label": item.get("exp_label") or "",
        "exp_source": item.get("exp_source") or "",
    }


def load_jobs(path):
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(path)

    with source.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return [_job_from_mapping(item) for item in data]

    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return [_job_from_mapping(item) for item in data["jobs"]]

    # Legacy seen_jobs.json format:
    # { "<stable-job-id>": { job fields... }, ... }
    if isinstance(data, dict):
        return [_job_from_mapping(item, fallback_id=job_id) for job_id, item in data.items()]

    raise ValueError("Unsupported JSON format")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/import_jobs_json.py app/data/seen_jobs.json")
        sys.exit(1)

    init_db()
    jobs = load_jobs(sys.argv[1])
    upsert_jobs(jobs)
    print("Import complete:")
    print({"total": len(jobs)})


if __name__ == "__main__":
    main()
