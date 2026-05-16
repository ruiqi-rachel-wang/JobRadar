import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from app.api.ats.detector import _parse_url


SOURCE_NAME = "Hiring Without Whiteboards"
SOURCE_URL = "https://github.com/poteto/hiring-without-whiteboards"
NO_WHITEBOARD_URL = "https://www.nowhiteboard.org/companies"


US_REMOTE_RE = re.compile(
    r"\b("
    r"Remote|USA?|United States|San Francisco|New York|Austin|Seattle|Boston|"
    r"Chicago|Denver|Atlanta|Los Angeles|Portland|Washington|DC|Texas|"
    r"California|Nashville|Raleigh|Philadelphia|Minneapolis|Madison|Boulder|"
    r"Columbus|Dallas|Houston|Pittsburgh|Salt Lake|Provo|Arlington|Oakland|"
    r"Mountain View|Palo Alto|Redwood City|San Mateo|San Jose|"
    r"CA|NY|WA|MA|IL|CO|GA|FL|VA|NC|OR|TX"
    r")\b",
    re.I,
)


def normalize_name(name):
    value = (name or "").lower()
    value = re.sub(r"&", " and ", value)
    value = re.sub(r"\b(inc|inc\.|llc|ltd|corp|corporation|company|co)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def parse_readme(path):
    rows = []
    entry_re = re.compile(r"- \[([^\]]+)\]\(([^)]+)\) \| ([^|]*) \| (.*)")
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        match = entry_re.match(raw.strip())
        if not match:
            continue
        rows.append({
            "name": match.group(1).strip(),
            "url": match.group(2).strip(),
            "locations": match.group(3).strip(),
            "process": match.group(4).strip(),
        })
    return rows


def origin_include(url):
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}"


def categories_for(row):
    categories = ["Interview Friendly"]
    if US_REMOTE_RE.search(row["locations"]):
        categories.append("US / Remote")
    return categories


def interview_payload(row):
    return {
        "no_whiteboard": True,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "secondary_source_url": NO_WHITEBOARD_URL,
        "locations": row["locations"],
        "process": row["process"],
    }


def merge_categories(existing, categories):
    merged = list(existing or [])
    for category in categories:
        if category not in merged:
            merged.append(category)
    return merged


def build_scrape(row):
    detected = _parse_url(row["url"])
    if detected:
        return detected, True
    return {
        "mode": "html",
        "list_url": row["url"],
        "include": origin_include(row["url"]),
        "reader": True,
        "stop_on_seen": True,
        "hard_max_pages": 1,
    }, False


def import_rows(companies_path, readme_path, limit=None):
    companies_path = Path(companies_path)
    companies = json.loads(companies_path.read_text(encoding="utf-8"))
    by_key = {normalize_name(company.get("name")): company for company in companies}

    rows = [
        row for row in parse_readme(readme_path)
        if US_REMOTE_RE.search(row["locations"])
    ]
    if limit:
        rows = rows[:limit]

    added = 0
    enabled = 0
    updated = 0
    skipped = 0
    for row in rows:
        key = normalize_name(row["name"])
        if not key:
            skipped += 1
            continue
        categories = categories_for(row)
        existing = by_key.get(key)
        if existing:
            existing["categories"] = merge_categories(existing.get("categories"), categories)
            existing["interview"] = interview_payload(row)
            updated += 1
            continue

        scrape, scan_enabled = build_scrape(row)
        company = {
            "name": row["name"],
            "categories": categories,
            "scrape": scrape,
            "scan_enabled": scan_enabled,
            "interview": interview_payload(row),
        }
        companies.append(company)
        by_key[key] = company
        added += 1
        if scan_enabled:
            enabled += 1

    companies.sort(key=lambda company: (company.get("name") or "").lower())
    companies_path.write_text(
        json.dumps(companies, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "source_rows": len(rows),
        "updated_existing": updated,
        "added": added,
        "added_scan_enabled": enabled,
        "added_interview_only": added - enabled,
        "skipped": skipped,
        "total_companies": len(companies),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", default="app/companies.json")
    parser.add_argument("--readme", default="/tmp/hiring_without_whiteboards_README.md")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(import_rows(args.companies, args.readme, args.limit))


if __name__ == "__main__":
    main()
