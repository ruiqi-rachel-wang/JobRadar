#!/usr/bin/env python3
"""Replace stale Deel position URLs using its complete current careers feed.

Run from the repo root: python -m scripts.repair_deel_jobs [--apply]
Dry-run by default. Applying saves affected rows before changing job links or
closing listings no longer present in the source. IDs and dates are preserved.
"""

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


def posting_id(value):
    try:
        parsed = urlsplit(value or "")
        if parsed.scheme not in {"https", "http"}:
            return None
        if parsed.hostname in {"www.deel.com", "deel.com"} and parsed.path.rstrip("/") == "/careers/position":
            value = parse_qs(parsed.query).get("ashby_jid", [None])[0]
        elif parsed.hostname == "jobs.deel.com":
            match = re.fullmatch(
                r"/deel/job-details/([^/]+)(?:/(?:overview|application))?/?", parsed.path
            )
            value = match[1] if match else None
        else:
            return None
        return str(UUID(value)) if value else None
    except (ValueError, AttributeError):
        return None


def repair(conn, auth_conn, backup_dir, source_jobs, *, apply=False, sqlite=False):
    source = {}
    for job in source_jobs:
        key = posting_id(job.get("url"))
        if not key or not job["url"].startswith("https://jobs.deel.com/deel/job-details/"):
            raise ValueError("Source contains an invalid Deel job URL")
        source[key] = job
    if not source:
        raise ValueError("Refusing to repair from an empty Deel feed")

    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM jobs WHERE company=?", ("Deel",)
    ).fetchall()]
    by_id = {row["id"]: row for row in rows}
    job_updates, job_backups, close_ids = [], [], []
    for row in rows:
        job = source.get(posting_id(row["url"]))
        if job:
            url, apply_url = job["url"], job.get("apply_url") or job["url"]
        else:
            url, apply_url = row["url"], row.get("apply_url")
        is_open = bool(row["is_open"]) and bool(job)
        if (url, apply_url, is_open) != (row["url"], row.get("apply_url"), bool(row["is_open"])):
            job_backups.append(row)
            job_updates.append((url, apply_url, is_open, row["id"]))
        if row["is_open"] and not job:
            close_ids.append((row["id"],))

    saved_updates, saved_backups = [], []
    for saved in auth_conn.execute(
        "SELECT * FROM user_apply_list WHERE job_company=?", ("Deel",)
    ).fetchall():
        saved = dict(saved)
        stored = by_id.get(saved["job_id"], {})
        job = source.get(posting_id(stored.get("url") or saved["job_url"]))
        if job and job["url"] != saved["job_url"]:
            saved_backups.append(saved)
            saved_updates.append((job["url"], saved["id"]))

    result = {
        "apply": apply, "source_jobs": len(source),
        "job_rows_updated": len(job_updates),
        "expired_results_closed": len(close_ids),
        "saved_jobs_updated": len(saved_updates), "backup": "",
    }
    if not apply or not (job_updates or saved_updates):
        return result

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"deel-repair-{stamp}.json"
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"jobs": job_backups, "user_apply_list": saved_backups}, stream,
                  indent=2, default=str)
    result["backup"] = str(backup)

    conn.executemany("UPDATE jobs SET url=?, apply_url=?, is_open=? WHERE id=?", job_updates)
    if sqlite and close_ids and conn.execute(
        "SELECT name FROM sqlite_master WHERE name='jobs_fts'"
    ).fetchone():
        for start in range(0, len(close_ids), 500):
            batch = [row[0] for row in close_ids[start:start + 500]]
            marks = ",".join("?" for _ in batch)
            conn.execute(f"DELETE FROM jobs_fts WHERE job_id IN ({marks})", batch)
    auth_conn.executemany("UPDATE user_apply_list SET job_url=? WHERE id=?", saved_updates)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from app.api.ats.html import fetch_html
    from app.api.common import DATA_DIR
    from app.api.database import IS_POSTGRES, connect_auth_db, connect_db
    from app.api.job_data import current_companies

    company = next(c for c in current_companies() if c["name"] == "Deel")
    source_jobs, error = fetch_html(company["scrape"], {}, "Deel")
    if error:
        raise RuntimeError(f"Deel feed could not be read completely: {error}")
    with connect_db(rows=True) as conn, connect_auth_db(rows=True) as auth_conn:
        result = repair(conn, auth_conn, DATA_DIR / "backups", source_jobs,
                        apply=args.apply, sqlite=not IS_POSTGRES)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
