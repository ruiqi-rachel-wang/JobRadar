"""CLI entrypoint for scheduled or manual scanner runs."""
import argparse
import json
from pathlib import Path

from .api.common import COMPANIES_FILE
from .scanner_service import run_scan


def load_companies(path):
    source = Path(path)
    with source.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("companies"), list):
        return data["companies"]
    raise ValueError("Unsupported companies.json format")


def filter_selected(companies):
    return [
        company for company in companies
        if company.get("selected") is True or company.get("enabled") is True
    ]


def filter_named(companies, names):
    wanted = {name.strip().lower() for name in names if name.strip()}
    return [company for company in companies if (company.get("name") or "").lower() in wanted]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies-file", default=str(COMPANIES_FILE))
    parser.add_argument("--all", action="store_true", help="Scan all configured companies")
    parser.add_argument("--selected", action="store_true", help="Scan selected/enabled config entries only")
    parser.add_argument("--company", action="append", default=[], help="Scan a named company; repeat as needed")
    args = parser.parse_args()

    companies = load_companies(args.companies_file)
    scan_type = "all"
    if args.company:
        companies = filter_named(companies, args.company)
        scan_type = "named"
    elif args.selected:
        companies = filter_selected(companies)
        scan_type = "selected"
    elif not args.all:
        raise ValueError("Use --all, --selected, or --company NAME")

    result = run_scan(companies=companies, scan_type=scan_type)
    printable = {key: value for key, value in result.items() if key not in {"results", "progress"}}
    print(printable)


if __name__ == "__main__":
    main()
