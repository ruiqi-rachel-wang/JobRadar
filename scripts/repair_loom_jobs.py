#!/usr/bin/env python3
"""Repair Loom's redirected links and close misattributed/non-job results.

Run from the repo root: python -m scripts.repair_loom_jobs [--apply]
Uses the app's configured SQLite/Postgres and account databases. Dry-run by
default; applying writes a private JSON backup before changing any rows.
"""

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def canonical_detail_url(value):
    try:
        parsed = urlsplit(value or "")
    except ValueError:
        return None
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in {
        "useloom.com", "www.useloom.com", "loom.com", "www.loom.com",
        "atlassian.com", "www.atlassian.com",
    }:
        return None
    match = re.fullmatch(r"/company/careers/details/(\d+)/?", parsed.path)
    if match:
        return f"https://www.atlassian.com/company/careers/details/{match[1]}"
    return None


def repair(conn, auth_conn, backup_dir, *, apply=False, sqlite=False):
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM jobs WHERE company=?", ("Loom",)
    ).fetchall()]
    by_id = {row["id"]: row for row in rows}
    job_updates, job_backups, close_ids = [], [], []
    for row in rows:
        url = canonical_detail_url(row["url"])
        keep = bool(url and "loom" in (row["title"] or "").lower())
        is_open = bool(row["is_open"]) and keep
        apply_url = canonical_detail_url(row.get("apply_url")) or row.get("apply_url")
        new_url = url or row["url"]
        if (new_url, apply_url, is_open) != (
            row["url"], row.get("apply_url"), bool(row["is_open"])
        ):
            job_backups.append(row)
            job_updates.append((new_url, apply_url, is_open, row["id"]))
        if row["is_open"] and not keep:
            close_ids.append((row["id"],))

    saved_updates, saved_backups = [], []
    for saved in auth_conn.execute(
        "SELECT * FROM user_apply_list WHERE job_company=?", ("Loom",)
    ).fetchall():
        saved = dict(saved)
        job = by_id.get(saved["job_id"], saved)
        url = canonical_detail_url(job.get("url") or saved["job_url"])
        if not url:
            continue
        title = job.get("title") or saved["job_title"] or ""
        company = "Loom" if "loom" in title.lower() else "Atlassian"
        if (url, company) != (saved["job_url"], saved["job_company"]):
            saved_backups.append(saved)
            saved_updates.append((url, company, saved["id"]))

    result = {
        "apply": apply,
        "job_rows_updated": len(job_updates),
        "incorrect_results_closed": len(close_ids),
        "saved_jobs_updated": len(saved_updates),
        "backup": "",
    }
    if not apply or not (job_updates or saved_updates):
        return result

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_dir / f"loom-repair-{stamp}.json"
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"jobs": job_backups, "user_apply_list": saved_backups}, stream,
                  indent=2, default=str)
    result["backup"] = str(backup)

    conn.executemany("UPDATE jobs SET url=?, apply_url=?, is_open=? WHERE id=?", job_updates)
    if sqlite and close_ids:
        has_fts = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='jobs_fts'"
        ).fetchone()
        if has_fts:
            # job_id is UNINDEXED in FTS5. Batch the IDs so cleanup scans the
            # large search table once per batch rather than once per job.
            for start in range(0, len(close_ids), 500):
                batch = [row[0] for row in close_ids[start:start + 500]]
                marks = ",".join("?" for _ in batch)
                conn.execute(f"DELETE FROM jobs_fts WHERE job_id IN ({marks})", batch)
    auth_conn.executemany(
        "UPDATE user_apply_list SET job_url=?, job_company=? WHERE id=?", saved_updates
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    from app.api.common import DATA_DIR
    from app.api.database import IS_POSTGRES, connect_auth_db, connect_db

    with connect_db(rows=True) as conn, connect_auth_db(rows=True) as auth_conn:
        result = repair(conn, auth_conn, DATA_DIR / "backups",
                        apply=args.apply, sqlite=not IS_POSTGRES)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
