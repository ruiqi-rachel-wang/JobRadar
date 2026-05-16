import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from app.api.apis import fetch_jobs
from app.api.ats.detector import detect_ats


def company_key(company):
    return (company.get("name") or "").strip().lower()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def quick_config(config):
    cfg = dict(config or {})
    cfg["hard_max_pages"] = min(int(cfg.get("hard_max_pages") or 1), 1)
    cfg["max_posted_details"] = min(int(cfg.get("max_posted_details") or 10), 10)
    cfg["fetch_experience_details"] = False
    cfg["fetch_posted_details"] = False
    cfg["stop_on_seen"] = False
    cfg["sort"] = False
    if cfg.get("mode") == "html":
        cfg["reader"] = False
    return cfg


def interview_source(row):
    return row.get("source") or "No Whiteboard Jobs"


def interview_source_url(row):
    return row.get("source_url") or "https://www.no-wb.org/"


def target_company(row):
    locations = row.get("locations", "")
    if isinstance(locations, list):
        locations = " / ".join(str(value) for value in locations if value)
    return {
        "name": row["name"],
        "categories": ["Interview Friendly", "US / Remote"],
        "scrape": {
            "mode": "html",
            "list_url": row["url"],
            "include": urlparse(row["url"]).netloc or row["url"],
        },
        "interview": {
            "no_whiteboard": True,
            "source": interview_source(row),
            "source_url": interview_source_url(row),
            "no_wb_id": row.get("id"),
            "locations": locations,
            "process": row.get("process", ""),
            "employment_type": row.get("employment_type", ""),
        },
    }


def candidate_urls(raw_url):
    parsed = urlparse(raw_url or "")
    if not parsed.scheme or not parsed.netloc:
        return [raw_url] if raw_url else []
    origin = f"{parsed.scheme}://{parsed.netloc}"
    paths = [
        parsed.path or "/",
        "/careers",
        "/careers/",
        "/jobs",
        "/jobs/",
        "/open-positions",
        "/openings",
        "/join-us",
        "/work-with-us",
        "/about/careers",
        "/company/careers",
    ]
    urls = []
    for path in paths:
        if not path:
            continue
        full = raw_url if path == parsed.path else origin + path
        if full not in urls:
            urls.append(full)
    return urls[:10]


def validate_company(company, timeout):
    name = company.get("name") or ""
    current = company.get("scrape") or {}
    url = (
        current.get("list_url")
        or current.get("api_url")
        or current.get("job_url_prefix")
        or (company.get("interview") or {}).get("source_url")
        or ""
    )
    started = time.monotonic()
    candidates = []
    checked_urls = []

    # Try detector against the listed URL plus common careers/jobs paths.
    for probe_url in candidate_urls(url):
        checked_urls.append(probe_url)
        try:
            detected = detect_ats(probe_url, timeout=timeout) if probe_url else []
        except Exception as exc:
            detected = [{"error": str(exc), "config": None}]
        for item in detected:
            cfg = item.get("config")
            if cfg:
                candidates.append((cfg, f"detected:{item.get('mode')}:{probe_url}"))
        if candidates:
            break

    # Also try existing non-generic configs. Generic imported HTML homepages are
    # too noisy/slow and often only prove that a homepage is reachable, not that
    # JobRadar can scan jobs from it.
    if current and current.get("mode") != "html":
        candidates.append((current, f"existing:{current.get('mode', 'unknown')}"))
    elif current and urlparse(url).path not in ("", "/"):
        candidates.append((current, "direct:html"))

    # Last chance for No-WB rows that only have a homepage or stale ATS hints:
    # try common careers/jobs paths as direct HTML pages. Keep this after ATS
    # detection so known APIs still win.
    seen_html = set()
    for probe_url in checked_urls:
        parsed = urlparse(probe_url)
        if not parsed.netloc or parsed.path in ("", "/"):
            continue
        if probe_url in seen_html:
            continue
        seen_html.add(probe_url)
        candidates.append(({
            "mode": "html",
            "list_url": probe_url,
            "include": parsed.netloc,
        }, f"direct:html:{probe_url}"))

    errors = []
    for cfg, source in candidates[:4]:
        probe_cfg = quick_config(cfg)
        try:
            jobs, err = fetch_jobs(
                {"name": name, "categories": company.get("categories") or [], "scrape": probe_cfg},
                {},
            )
            count = len(jobs or [])
            if count > 0:
                return {
                    "name": name,
                    "status": "working",
                    "count": count,
                    "config": cfg,
                    "source": source,
                    "duration_s": round(time.monotonic() - started, 2),
                }
            errors.append(f"{source}: {err or '0 jobs'}")
        except Exception as exc:
            errors.append(f"{source}: {exc}")

    return {
        "name": name,
        "status": "not_working",
        "count": 0,
        "url": url,
        "checked_urls": checked_urls,
        "errors": errors[:4],
        "duration_s": round(time.monotonic() - started, 2),
    }


def apply_results(companies_path, original_path, results_path, not_working_path):
    companies = load_json(companies_path)
    original_names = {company_key(c) for c in load_json(original_path)}
    results = load_json(results_path)
    by_name = {company_key(r): r for r in results}

    kept = []
    removed = []
    enabled = 0
    for company in companies:
        key = company_key(company)
        if key in original_names:
            kept.append(company)
            continue
        result = by_name.get(key)
        if result and result.get("status") == "working":
            company["scrape"] = result["config"]
            company.pop("scan_enabled", None)
            kept.append(company)
            enabled += 1
        else:
            removed.append({
                "name": company.get("name"),
                "url": ((company.get("scrape") or {}).get("list_url") or ""),
                "reason": (result or {}).get("errors") or ["not validated"],
            })

    kept.sort(key=lambda company: (company.get("name") or "").lower())
    Path(companies_path).write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    Path(not_working_path).write_text(json.dumps(removed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"kept_total": len(kept), "enabled_new": enabled, "removed_new": len(removed)}


def apply_target_results(companies_path, targets_path, results_path, not_working_path):
    companies = load_json(companies_path)
    targets = load_json(targets_path)
    results = load_json(results_path)
    by_name = {company_key(r): r for r in results}
    target_by_name = {company_key(row): row for row in targets}
    company_by_name = {company_key(company): company for company in companies}

    added = 0
    updated_existing = 0
    not_working = []

    for key, row in target_by_name.items():
        result = by_name.get(key)
        if not result or result.get("status") != "working":
            not_working.append({
                "name": row.get("name"),
                "url": row.get("url"),
                "locations": row.get("locations", ""),
                "score": row.get("score"),
                "reason": (result or {}).get("errors") or ["not validated"],
            })
            continue

        company = company_by_name.get(key)
        if company is None:
            company = target_company(row)
            companies.append(company)
            company_by_name[key] = company
            added += 1
        else:
            updated_existing += 1
            company.setdefault("categories", [])
            if "Interview Friendly" not in company["categories"]:
                company["categories"].append("Interview Friendly")
            company["interview"] = target_company(row)["interview"]

        scrape = dict(result["config"])
        if scrape.get("mode") == "html":
            list_url = scrape.get("list_url") or ""
            host = urlparse(list_url).netloc
            if host and str(scrape.get("include") or "").startswith(("http://", "https://")):
                scrape["include"] = host
        company["scrape"] = scrape
        company.pop("scan_enabled", None)

    companies.sort(key=lambda company: (company.get("name") or "").lower())
    Path(companies_path).write_text(json.dumps(companies, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    Path(not_working_path).write_text(json.dumps(not_working, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "companies_total": len(companies),
        "added": added,
        "updated_existing": updated_existing,
        "not_working": len(not_working),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--companies", default="app/companies.json")
    parser.add_argument("--original", default="/tmp/original_companies.json")
    parser.add_argument("--results", default="app/data/added_company_validation.json")
    parser.add_argument("--not-working", default="app/data/added_companies_not_working.json")
    parser.add_argument("--targets")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--hard-timeout", type=int, default=35)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--one-name")
    args = parser.parse_args()

    if args.one_name:
        companies = load_json(args.companies)
        target = next((c for c in companies if c.get("name") == args.one_name), None)
        if not target and args.targets:
            target = next(c for c in load_json(args.targets) if c.get("name") == args.one_name)
            target = target_company(target)
        print(json.dumps(validate_company(target, args.timeout), ensure_ascii=False))
        return

    if args.apply:
        if args.targets:
            print(apply_target_results(args.companies, args.targets, args.results, args.not_working))
            return
        print(apply_results(args.companies, args.original, args.results, args.not_working))
        return

    companies = load_json(args.companies)
    original_names = {company_key(c) for c in load_json(args.original)}
    targets = [c for c in companies if company_key(c) not in original_names]
    if args.targets:
        wanted_rows = load_json(args.targets)
        by_name = {company_key(c): c for c in companies}
        targets = []
        for row in wanted_rows:
            company = by_name.get(company_key(row))
            if not company:
                company = target_company(row)
            targets.append(company)
    if args.limit:
        targets = targets[:args.limit]

    def run_one(company):
        cmd = [
            sys.executable,
            __file__,
            "--companies", args.companies,
            "--original", args.original,
            "--timeout", str(args.timeout),
            "--one-name", company.get("name") or "",
        ]
        if args.targets:
            cmd.extend(["--targets", args.targets])
        env = dict(os.environ)
        env["PYTHONPATH"] = "."
        try:
            proc = subprocess.run(
                cmd,
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                capture_output=True,
                timeout=args.hard_timeout,
            )
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            if proc.returncode == 0 and lines:
                return json.loads(lines[-1])
            return {
                "name": company.get("name"),
                "status": "not_working",
                "count": 0,
                "url": (company.get("scrape") or {}).get("list_url", ""),
                "errors": [proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"],
                "duration_s": args.hard_timeout,
            }
        except subprocess.TimeoutExpired:
            return {
                "name": company.get("name"),
                "status": "not_working",
                "count": 0,
                "url": (company.get("scrape") or {}).get("list_url", ""),
                "errors": [f"timed out after {args.hard_timeout}s"],
                "duration_s": args.hard_timeout,
            }

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run_one, company) for company in targets]
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(f"{index}/{len(targets)} {result['status']} {result['count']:>3} {result['name']} ({result['duration_s']}s)", flush=True)

    results.sort(key=lambda item: (item["status"] != "working", item["name"].lower()))
    Path(args.results).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    working = sum(1 for result in results if result["status"] == "working")
    print({"tested": len(results), "working": working, "not_working": len(results) - working})


if __name__ == "__main__":
    main()
