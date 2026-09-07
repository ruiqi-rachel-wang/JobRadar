#!/usr/bin/env python3
"""Remove a reviewed company set from config and close its stored jobs.

Every mutation gets a compact rollback artifact. The decisions CSV is the
source of truth and must contain unique company names plus a reason.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.database import IS_POSTGRES, connect_db  # noqa: E402


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_csv(path, rows, fieldnames):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--companies", default=str(ROOT / "app" / "companies.json"))
    parser.add_argument("--review")
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--apply-config", action="store_true")
    parser.add_argument("--apply-db", action="store_true")
    args = parser.parse_args()

    decisions, decision_fields = _read_csv(args.decisions)
    if decision_fields != ["name", "reason"]:
        raise SystemExit("decisions CSV must have exactly: name,reason")
    names = [row["name"].strip() for row in decisions]
    reasons = {row["name"].strip(): row["reason"].strip() for row in decisions}
    if not names or len(names) != len(set(names)):
        raise SystemExit("decisions CSV must contain unique non-empty names")

    companies_path = Path(args.companies)
    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    configured = {company["name"] for company in companies}
    missing_config = sorted(set(names) - configured)
    if args.apply_config and missing_config:
        raise SystemExit(f"refusing config mutation; names missing: {missing_config}")

    review_rows = []
    review_fields = []
    missing_review = []
    if args.review:
        review_rows, review_fields = _read_csv(args.review)
        reviewed = {row["name"].strip() for row in review_rows}
        missing_review = sorted(set(names) - reviewed)
        if args.apply_config and missing_review:
            raise SystemExit(f"refusing review mutation; names missing: {missing_review}")

    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if args.apply_config:
        shutil.copy2(companies_path, backup_dir / f"companies-before-prune-{stamp}.json")
        _atomic_json(
            companies_path,
            [company for company in companies if company["name"] not in reasons],
        )
        if args.review:
            review_path = Path(args.review)
            shutil.copy2(
                review_path,
                backup_dir / f"import-review-before-prune-{stamp}.csv",
            )
            _atomic_csv(
                review_path,
                [row for row in review_rows if row["name"].strip() not in reasons],
                review_fields,
            )

    closed_rows = []
    if args.apply_db:
        with connect_db(rows=True) as conn:
            if IS_POSTGRES:
                cursor = conn.execute(
                    "SELECT id, company FROM jobs "
                    "WHERE is_open = TRUE AND company = ANY(%s)",
                    (names,),
                )
            else:
                marks = ",".join("?" for _ in names)
                cursor = conn.execute(
                    f"SELECT id, company FROM jobs "
                    f"WHERE is_open = 1 AND company IN ({marks})",
                    names,
                )
            closed_rows = [dict(row) for row in cursor.fetchall()]
            (backup_dir / f"open-jobs-before-prune-{stamp}.json").write_text(
                json.dumps(closed_rows, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if IS_POSTGRES:
                conn.execute(
                    "UPDATE jobs SET is_open = FALSE, updated_at = NOW() "
                    "WHERE is_open = TRUE AND company = ANY(%s)",
                    (names,),
                )
            else:
                marks = ",".join("?" for _ in names)
                conn.execute(
                    f"UPDATE jobs SET is_open = 0 "
                    f"WHERE is_open = 1 AND company IN ({marks})",
                    names,
                )

    print(json.dumps({
        "decision_companies": len(names),
        "configured_matches": len(set(names) & configured),
        "review_matches": len(names) - len(missing_review) if args.review else None,
        "remaining_config_companies": len(companies) - (
            len(set(names) & configured) if args.apply_config else 0
        ),
        "remaining_review_companies": (
            len(review_rows) - len(set(names) & {
                row["name"].strip() for row in review_rows
            })
            if args.review and args.apply_config else None
        ),
        "closed_open_jobs": len(closed_rows),
        "applied_config": args.apply_config,
        "applied_db": args.apply_db,
        "backup_dir": str(backup_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
