#!/usr/bin/env python3
"""Repair AbbVie rows polluted by metadata from its HTML job cards."""

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app.api.utils import normalize_location


FIELD_BOUNDARY = re.compile(
    r"\s+(?:Travel|Location|Work location type|Brands?|Job Type|Function|"
    r"Experience Level|Description)\b",
    re.IGNORECASE,
)
JOB_PATH = re.compile(r"/job/[^/?#]+-jid-\d+/?$", re.IGNORECASE)


def repair(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, url, location, is_open FROM jobs WHERE company='AbbVie'"
    ).fetchall()

    location_updates = []
    close_ids = []
    changed_rows = []
    for row in rows:
        location = row["location"] or ""
        if FIELD_BOUNDARY.search(location):
            cleaned = normalize_location(FIELD_BOUNDARY.split(location, maxsplit=1)[0].strip(" ,-"))
            if cleaned and cleaned != location:
                location_updates.append((cleaned, row["id"]))
                changed_rows.append({**dict(row), "new_location": cleaned})

        if row["is_open"] and not JOB_PATH.search(row["url"] or ""):
            close_ids.append((row["id"],))
            changed_rows.append({**dict(row), "new_is_open": 0})

    backup_path = None
    if changed_rows:
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"abbvie-repair-{stamp}.json"
        backup_path.write_text(json.dumps(changed_rows, indent=2), encoding="utf-8")

    with conn:
        conn.executemany("UPDATE jobs SET location=? WHERE id=?", location_updates)
        conn.executemany("UPDATE jobs SET is_open=0 WHERE id=?", close_ids)
        for _, job_id in location_updates:
            conn.execute("DELETE FROM jobs_fts WHERE job_id=?", (job_id,))
            conn.execute(
                "INSERT INTO jobs_fts(job_id, title, company, location) "
                "SELECT id, title, company, location FROM jobs WHERE id=? AND is_open=1",
                (job_id,),
            )
        for (job_id,) in close_ids:
            conn.execute("DELETE FROM jobs_fts WHERE job_id=?", (job_id,))

    conn.close()
    return {
        "locations_repaired": len(location_updates),
        "non_job_rows_closed": len(close_ids),
        "backup": str(backup_path) if backup_path else "",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    args = parser.parse_args()
    print(json.dumps(repair(args.db), indent=2))
