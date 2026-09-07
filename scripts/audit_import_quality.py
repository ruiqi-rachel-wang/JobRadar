#!/usr/bin/env python3
"""Audit an imported company batch for duplicates and low-quality sources.

Unlike ``audit_company_sources.py``, this script evaluates the jobs already
stored by a scan. A successful HTTP response is not treated as proof that a
configuration represents the right company or a useful direct-employer board.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.database import IS_POSTGRES, connect_db  # noqa: E402


NON_JOB_TITLE_PATTERNS = (
    re.compile(r"\b(?:join|submit to)\s+(?:our\s+)?talent (?:community|network|pool)\b", re.I),
    re.compile(r"\b(?:general|spontaneous|unsolicited) application\b", re.I),
    re.compile(r"\bdon'?t see (?:a|the) (?:job|role|position)\b", re.I),
    re.compile(r"\bfuture opportunit(?:y|ies)\b", re.I),
)


def _sources(company):
    scrape = company.get("scrape") or {}
    return scrape.get("sources") or [] if scrape.get("mode") == "multi" else [scrape]


def _clean_url(value):
    return (value or "").strip().split("?", 1)[0].rstrip("/").casefold()


def _source_identity(source):
    mode = source.get("mode", "html")
    keys = {
        "ashby": "ashby_slug",
        "bamboohr": "bamboohr_slug",
        "greenhouse": "board_token",
        "jobvite": "jobvite_company",
        "lever": "lever_slug",
        "pinpoint": "pinpoint_slug",
        "rippling": "rippling_slug",
        "smartrecruiters": "smartrecruiters_company",
        "workable": "workable_slug",
    }
    key = keys.get(mode)
    if key and source.get(key):
        return f"{mode}:{str(source[key]).strip().casefold().rstrip('/')}"
    for candidate in ("list_url", "icims_slug", "url", "feed_url", "sitemap_url"):
        if source.get(candidate):
            return f"{mode}:{_clean_url(str(source[candidate]))}"
    return f"{mode}:{json.dumps(source, sort_keys=True, default=str)}"


def _source_family(source):
    """Return the ATS tenant, ignoring a Workday career-site path."""
    identity = _source_identity(source)
    for candidate in ("list_url", "icims_slug", "url", "feed_url", "sitemap_url"):
        value = source.get(candidate)
        if not value:
            continue
        parsed = urlsplit(str(value) if "://" in str(value) else f"https://{value}")
        if parsed.hostname:
            return f"{source.get('mode', 'html')}:{parsed.hostname.casefold()}"
    return identity


def _json_value(value):
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _job_countries(row):
    countries = set()
    if row.get("country_iso"):
        countries.add(str(row["country_iso"]).upper())
    locations = _json_value(row.get("locations"))
    if isinstance(locations, list):
        for location in locations:
            if isinstance(location, dict) and location.get("country_iso"):
                countries.add(str(location["country_iso"]).upper())
    return countries


def _is_non_job_title(title):
    return any(pattern.search(title or "") for pattern in NON_JOB_TITLE_PATTERNS)


def _write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", default=str(ROOT / "app" / "companies.json"))
    parser.add_argument("--review", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    companies = json.loads(Path(args.companies).read_text(encoding="utf-8"))
    with Path(args.review).open(newline="", encoding="utf-8") as handle:
        imported_names = [row["name"].strip() for row in csv.DictReader(handle)]
    imported = set(imported_names)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    exact_sources = defaultdict(set)
    source_families = defaultdict(set)
    company_sources = defaultdict(list)
    for company in companies:
        for source in _sources(company):
            exact = _source_identity(source)
            family = _source_family(source)
            exact_sources[exact].add(company["name"])
            source_families[family].add(company["name"])
            company_sources[company["name"]].append((exact, family))

    fields = (
        "company", "url", "ats_id", "requisition_id", "title", "location",
        "location_raw", "locations", "country_iso", "remote_mode", "source",
    )
    with connect_db(rows=True) as conn:
        if IS_POSTGRES:
            cursor = conn.execute(
                f"SELECT {', '.join(fields)} FROM jobs "
                "WHERE is_open = TRUE AND company = ANY(%s)",
                (imported_names,),
            )
        else:
            marks = ",".join("?" for _ in imported_names)
            cursor = conn.execute(
                f"SELECT {', '.join(fields)} FROM jobs "
                f"WHERE is_open = 1 AND company IN ({marks})",
                imported_names,
            )
        jobs = [dict(row) for row in cursor.fetchall()]

        # Exact posting URL overlap is high-confidence evidence that two labels
        # are serving the same employer board. Keep this as one server-side
        # join; repeatedly applying URL expressions over the full jobs table is
        # needlessly expensive in production.
        if IS_POSTGRES:
            cursor = conn.execute(
                "SELECT a.company AS imported_company, b.company AS other_company, "
                "COUNT(DISTINCT a.url) AS shared_open_job_urls "
                "FROM jobs a JOIN jobs b ON b.url = a.url "
                "WHERE a.is_open = TRUE AND b.is_open = TRUE "
                "AND a.url <> '' AND a.company = ANY(%s) "
                "AND a.company <> b.company "
                "GROUP BY a.company, b.company",
                (imported_names,),
            )
            raw_overlaps = [dict(row) for row in cursor.fetchall()]
        else:
            # Local databases are small enough to build the exact URL map in
            # memory, and this avoids SQLite's low bind-parameter limit.
            cursor = conn.execute(
                "SELECT company, url FROM jobs WHERE is_open = 1 AND url <> ''"
            )
            other_by_url = defaultdict(set)
            for row in cursor.fetchall():
                other_by_url[row["url"]].add(row["company"])
            counts = Counter()
            for job in jobs:
                for other in other_by_url.get(job.get("url"), ()):
                    if other != job["company"]:
                        counts[(job["company"], other)] += 1
            raw_overlaps = [
                {
                    "imported_company": company,
                    "other_company": other,
                    "shared_open_job_urls": count,
                }
                for (company, other), count in counts.items()
            ]

        intermediary_predicate = (
            "lower(COALESCE(description, '')) LIKE '%on behalf of our client%' "
            "OR lower(COALESCE(description, '')) LIKE '%our client is seeking%' "
            "OR lower(COALESCE(description, '')) LIKE '%for one of our clients%' "
            "OR lower(COALESCE(description, '')) LIKE '%client is looking for%' "
            "OR lower(COALESCE(description, '')) LIKE '%working on behalf of%'"
        )
        if IS_POSTGRES:
            cursor = conn.execute(
                "SELECT company, COUNT(*) AS described_jobs, "
                f"SUM(CASE WHEN {intermediary_predicate} THEN 1 ELSE 0 END) "
                "AS intermediary_jobs FROM jobs "
                "WHERE is_open = TRUE AND company = ANY(%s) "
                "AND COALESCE(description, '') <> '' GROUP BY company",
                (imported_names,),
            )
        else:
            marks = ",".join("?" for _ in imported_names)
            cursor = conn.execute(
                "SELECT company, COUNT(*) AS described_jobs, "
                f"SUM(CASE WHEN {intermediary_predicate} THEN 1 ELSE 0 END) "
                "AS intermediary_jobs FROM jobs "
                f"WHERE is_open = 1 AND company IN ({marks}) "
                "AND COALESCE(description, '') <> '' GROUP BY company",
                imported_names,
            )
        intermediary_metrics = {
            row["company"]: (
                int(row["described_jobs"] or 0),
                int(row["intermediary_jobs"] or 0),
            )
            for row in cursor.fetchall()
        }

    jobs_by_company = defaultdict(list)
    for job in jobs:
        jobs_by_company[job["company"]].append(job)

    overlap_rows = []
    for raw in sorted(
        raw_overlaps,
        key=lambda row: (
            -row["shared_open_job_urls"],
            row["imported_company"],
            row["other_company"],
        ),
    ):
        company = raw["imported_company"]
        other = raw["other_company"]
        count = raw["shared_open_job_urls"]
        total = len(jobs_by_company[company])
        overlap_rows.append({
            "imported_company": company,
            "other_company": other,
            "shared_open_job_urls": count,
            "imported_open_jobs": total,
            "overlap_ratio": round(count / total, 4) if total else 0,
        })

    company_rows = []
    recommendation_counts = Counter()
    for name in imported_names:
        company_jobs = jobs_by_company.get(name, [])
        countries = Counter()
        unknown_country = 0
        non_job_count = 0
        for job in company_jobs:
            job_countries = _job_countries(job)
            if job_countries:
                countries.update(job_countries)
            else:
                unknown_country += 1
            non_job_count += int(_is_non_job_title(job.get("title")))

        us_jobs = countries["US"]
        known_non_us = sum(count for country, count in countries.items() if country != "US")
        exact_collisions = set()
        family_collisions = set()
        for exact, family in company_sources.get(name, ()):
            exact_collisions.update(exact_sources[exact] - {name})
            family_collisions.update(source_families[family] - {name})
        overlaps = [
            row for row in overlap_rows
            if row["imported_company"] == name and row["overlap_ratio"] >= 0.5
        ]
        described_jobs, intermediary_jobs = intermediary_metrics.get(name, (0, 0))
        intermediary_ratio = (
            intermediary_jobs / described_jobs if described_jobs else 0
        )

        flags = []
        if not company_jobs:
            flags.append("no_open_jobs")
        if exact_collisions:
            flags.append("same_exact_source")
        if any(row["shared_open_job_urls"] >= 3 for row in overlaps):
            flags.append("high_job_overlap")
        if us_jobs == 0 and known_non_us >= 5 and unknown_country <= known_non_us:
            flags.append("non_us_only")
        if company_jobs and non_job_count == len(company_jobs):
            flags.append("non_job_board")
        if intermediary_jobs >= 3 and intermediary_ratio >= 0.3:
            flags.append("staffing_language")

        if {
            "same_exact_source", "high_job_overlap", "non_job_board",
            "staffing_language",
        } & set(flags):
            recommendation = "remove_or_merge"
        elif "non_us_only" in flags:
            recommendation = "remove_if_us_focused"
        elif not company_jobs:
            recommendation = "verify_inactive"
        elif known_non_us and us_jobs:
            recommendation = "keep_with_us_filter"
        else:
            recommendation = "keep_or_manual_review"
        recommendation_counts[recommendation] += 1

        company_rows.append({
            "name": name,
            "open_jobs": len(company_jobs),
            "us_jobs_structured": us_jobs,
            "non_us_jobs_structured": known_non_us,
            "unknown_country_jobs": unknown_country,
            "non_job_titles": non_job_count,
            "described_jobs": described_jobs,
            "intermediary_language_jobs": intermediary_jobs,
            "intermediary_language_ratio": round(intermediary_ratio, 4),
            "flags": "|".join(flags),
            "recommendation": recommendation,
            "exact_source_collisions": "|".join(sorted(exact_collisions)),
            "same_tenant_other_sites": "|".join(sorted(family_collisions)),
            "high_overlap_companies": "|".join(
                f"{row['other_company']}:{row['overlap_ratio']}"
                for row in overlaps
            ),
            "country_counts": json.dumps(dict(countries.most_common()), sort_keys=True),
        })

    _write_csv(
        output_dir / "company_quality.csv",
        company_rows,
        list(company_rows[0]) if company_rows else ["name"],
    )
    _write_csv(
        output_dir / "job_url_overlaps.csv",
        overlap_rows,
        list(overlap_rows[0]) if overlap_rows else [
            "imported_company", "other_company", "shared_open_job_urls",
            "imported_open_jobs", "overlap_ratio",
        ],
    )
    report = {
        "imported_companies": len(imported_names),
        "configured_imported_companies": sum(name in company_sources for name in imported_names),
        "open_jobs": len(jobs),
        "recommendations": dict(recommendation_counts),
        "companies_with_exact_source_collision": sum(
            bool(row["exact_source_collisions"]) for row in company_rows
        ),
        "companies_with_high_job_overlap": sum(
            "high_job_overlap" in row["flags"] for row in company_rows
        ),
        "companies_with_only_non_job_titles": sum(
            "non_job_board" in row["flags"] for row in company_rows
        ),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
