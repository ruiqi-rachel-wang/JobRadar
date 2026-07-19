#!/usr/bin/env python3
"""Synchronize stored job and saved-search filters with companies.json taxonomy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.database import connect_auth_db, connect_db, init_auth_db  # noqa: E402
from app.api.job_data import current_companies, init_db  # noqa: E402
from app.api.taxonomy import migrate_saved_filter  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit the reported updates")
    args = parser.parse_args()

    init_db()
    init_auth_db()

    categories_by_company = {
        company["name"]: json.dumps(company.get("categories") or [], ensure_ascii=False)
        for company in current_companies()
        if company.get("name")
    }
    job_rows = 0
    with connect_db(rows=True) as conn:
        for company, categories in categories_by_company.items():
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE company=? AND COALESCE(categories, '')<>?",
                (company, categories),
            ).fetchone()
            job_rows += int(row["n"] if isinstance(row, dict) else row[0])

    search_updates = []
    with connect_auth_db(rows=True) as conn:
        searches = conn.execute("SELECT id, category, tag FROM user_saved_searches").fetchall()
        for row in searches:
            category, tag = migrate_saved_filter(row["category"], row["tag"])
            if category != (row["category"] or "All") or tag != (row["tag"] or ""):
                search_updates.append((category, tag, row["id"]))

    print(f"stored job rows to update: {job_rows}")
    print(f"saved searches to update: {len(search_updates)}")
    if not args.apply:
        print("dry run only; rerun with --apply")
        return

    with connect_db() as conn:
        for company, categories in categories_by_company.items():
            conn.execute(
                "UPDATE jobs SET categories=? WHERE company=? AND COALESCE(categories, '')<>?",
                (categories, company, categories),
            )
    if search_updates:
        with connect_auth_db() as conn:
            conn.executemany(
                "UPDATE user_saved_searches SET category=?, tag=? WHERE id=?",
                search_updates,
            )
    print("taxonomy migration applied")


if __name__ == "__main__":
    main()
