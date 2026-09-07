#!/usr/bin/env python3
"""Audit configured companies through JobDrop's real ATS adapters.

This intentionally bypasses database writes and enrichment: its purpose is to
verify source reachability and listing completeness quickly.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.apis import FETCHERS, _apply_scan_defaults  # noqa: E402


def _fetch_source(company_name, source, timeout_seconds):
    scrape = _apply_scan_defaults(source, "full")
    scrape["_deadline_monotonic"] = time.monotonic() + timeout_seconds
    # Counting listings does not require hundreds of per-posting detail calls.
    scrape["fetch_posted_details"] = False
    scrape["fetch_experience_details"] = False
    scrape["max_desc_details"] = 0
    scrape["fetch_details"] = False
    mode = scrape.get("mode", "html")
    fetcher = FETCHERS.get(mode)
    if not fetcher:
        return [], f"unknown mode: {mode}"
    return fetcher(scrape, {}, company_name)


def _audit_one(company, timeout_seconds):
    started = time.monotonic()
    scrape = company.get("scrape") or {}
    sources = scrape.get("sources") if scrape.get("mode") == "multi" else [scrape]
    source_results = []
    urls = set()
    errors = []
    for source in sources or []:
        jobs, error = _fetch_source(company["name"], source or {}, timeout_seconds)
        urls.update((job.get("url") or "").strip() for job in jobs if job.get("url"))
        if error:
            errors.append(error)
        source_results.append({
            "mode": (source or {}).get("mode", "html"),
            "count": len(jobs),
            "error": error,
        })
    return {
        "name": company["name"],
        "mode": scrape.get("mode", "html"),
        "count": len(urls),
        "error": " | ".join(errors) or None,
        "sources": source_results,
        "elapsed_s": round(time.monotonic() - started, 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", default=str(ROOT / "app" / "companies.json"))
    parser.add_argument("--review", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    companies = json.loads(Path(args.companies).read_text(encoding="utf-8"))
    with Path(args.review).open(newline="", encoding="utf-8") as handle:
        wanted = {row["name"].casefold() for row in csv.DictReader(handle)}
    selected = [company for company in companies if company["name"].casefold() in wanted]
    if len(selected) != len(wanted):
        found = {company["name"].casefold() for company in selected}
        raise SystemExit(f"missing configured companies: {sorted(wanted - found)}")

    results = []
    lock = threading.Lock()
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(_audit_one, company, args.timeout_seconds): company["name"]
            for company in selected
        }
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "name": name,
                    "mode": "unknown",
                    "count": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                    "sources": [],
                    "elapsed_s": 0,
                }
            with lock:
                results.append(result)
                if len(results) % 25 == 0 or len(results) == len(selected):
                    statuses = Counter(
                        "error" if row["error"] else "jobs" if row["count"] else "zero"
                        for row in results
                    )
                    print(
                        f"{len(results)}/{len(selected)} "
                        f"jobs={statuses['jobs']} zero={statuses['zero']} "
                        f"errors={statuses['error']} elapsed={time.monotonic() - started:.1f}s",
                        flush=True,
                    )

    results.sort(key=lambda row: row["name"].casefold())
    payload = {
        "audited_at_epoch": int(time.time()),
        "elapsed_s": round(time.monotonic() - started, 2),
        "companies": len(results),
        "results": results,
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
