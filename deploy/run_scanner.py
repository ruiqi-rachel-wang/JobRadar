"""Thin wrapper so the scanner can be invoked from the repo root."""
import sys
import os
import argparse
import json
from pathlib import Path

# Add app to path so absolute imports work.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

# Load dotenv from repo root
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from api.common import COMPANIES_FILE
from scanner_service import run_scan


def load_companies(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("companies"), list):
        return data["companies"]
    raise ValueError("Unsupported companies.json format")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies-file", default=str(COMPANIES_FILE))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--selected", action="store_true")
    parser.add_argument("--company", action="append", default=[])
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    companies = load_companies(args.companies_file)

    if args.selected:
        companies = [c for c in companies if c.get("selected") or c.get("enabled")]
    elif args.company:
        wanted = {n.strip().lower() for n in args.company}
        companies = [c for c in companies if (c.get("name") or "").lower() in wanted]
    elif not args.all:
        parser.error("Specify --all, --selected, or --company NAME")

    scan_type = "full" if args.full else "fresh"
    result = run_scan(companies=companies, scan_type=scan_type)
    printable = {k: v for k, v in result.items() if k not in {"results", "progress"}}
    print(printable)


if __name__ == "__main__":
    main()
