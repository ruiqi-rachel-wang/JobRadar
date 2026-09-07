#!/usr/bin/env python3
"""Rebuild canonical location fields from preserved raw source values.

Raw fields are never modified. The script is safe to run after improving the
location normalizer and supports a CSV company allow-list for bounded repairs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.database import IS_POSTGRES, connect_db  # noqa: E402
from app.api.utils import normalize_locations  # noqa: E402


def _decode_locations(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _raw_values(row):
    values = []
    for location in _decode_locations(row.get("locations")):
        if isinstance(location, dict) and location.get("raw"):
            raw = str(location["raw"]).strip()
            if raw and raw not in values:
                values.append(raw)
    raw = (row.get("location_raw") or "").strip()
    if raw and raw not in values:
        values.insert(0, raw)
    return values


def _company_names(path):
    if not path:
        return []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [row["name"].strip() for row in csv.DictReader(handle)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", help="CSV with a name column; omit for every company")
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    parser.add_argument("--backup", help="JSON backup path for changed canonical values")
    args = parser.parse_args()

    names = _company_names(args.review)
    fields = (
        "id", "company", "location", "location_raw", "locations",
        "country_iso", "remote_mode",
    )
    with connect_db(rows=True) as conn:
        if names and IS_POSTGRES:
            cursor = conn.execute(
                f"SELECT {', '.join(fields)} FROM jobs "
                "WHERE is_open = TRUE AND company = ANY(%s)",
                (names,),
            )
        elif names:
            marks = ",".join("?" for _ in names)
            cursor = conn.execute(
                f"SELECT {', '.join(fields)} FROM jobs "
                f"WHERE is_open = 1 AND company IN ({marks})",
                names,
            )
        else:
            cursor = conn.execute(
                f"SELECT {', '.join(fields)} FROM jobs "
                f"WHERE is_open = {'TRUE' if IS_POSTGRES else '1'}"
            )
        rows = [dict(row) for row in cursor.fetchall()]

        changes = []
        for row in rows:
            raw_values = _raw_values(row)
            if not raw_values:
                continue
            canonical = normalize_locations(
                raw_values,
                remote_mode=row.get("remote_mode"),
            )
            new_locations = canonical["locations"]
            old_locations = _decode_locations(row.get("locations"))
            old_mode = row.get("remote_mode")
            new_mode = canonical["remote_mode"]
            if (
                (row.get("location") or None) == canonical["location"]
                and old_locations == new_locations
                and row.get("country_iso") == canonical["country_iso"]
                and old_mode == new_mode
            ):
                continue
            changes.append({
                "id": row["id"],
                "company": row["company"],
                "raw_unchanged": raw_values,
                "old": {
                    "location": row.get("location"),
                    "locations": old_locations,
                    "country_iso": row.get("country_iso"),
                    "remote_mode": old_mode,
                },
                "new": canonical,
            })

        backup = Path(args.backup) if args.backup else Path(
            f"/tmp/canonical-location-backfill-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        if changes:
            backup.write_text(
                json.dumps(changes, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        if args.apply:
            for change in changes:
                new = change["new"]
                conn.execute(
                    "UPDATE jobs SET location=?, locations=?, country_iso=?, "
                    "remote_mode=? WHERE id=?",
                    (
                        new["location"],
                        json.dumps(new["locations"], ensure_ascii=False),
                        new["country_iso"],
                        new["remote_mode"],
                        change["id"],
                    ),
                )
            # jobs_fts is a retained legacy table and is intentionally not
            # maintained by normal upserts. Its unindexed job_id column makes
            # per-row delete/reinsert maintenance catastrophically slow, and
            # application search reads the canonical jobs table instead.

    result = {
        "scope": len(names) if names else "all",
        "rows_checked": len(rows),
        "rows_changed": len(changes),
        "applied": args.apply,
        "backup": str(backup) if changes else None,
        "raw_values_modified": 0,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
